"""Atomic signed-event append and independent journal verification."""

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.identity.infrastructure.models import (
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.journal.domain.assurance import (
    CRITICAL_EVENT_TYPES,
    CommandAssurance,
    ExposureClaim,
)
from cooperative_clearing.modules.journal.domain.crypto import (
    CANONICALIZATION_PROFILE,
    SIGNATURE_ALGORITHM,
    NodeSigner,
    canonicalize,
    payload_hash,
    sha256_ref,
    utc_timestamp,
    verify_signature,
)
from cooperative_clearing.modules.journal.infrastructure.models import (
    EventSignature,
    NodeChainState,
    OutboxMessage,
    SignedEvent,
)
from cooperative_clearing.modules.node.infrastructure.models import NodeKeyRecord, NodeProfile
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.secrets import read_text_secret
from cooperative_clearing.shared.domain.errors import DomainError

PROTOCOL_VERSION = "1.0"
OUTBOX_TOPIC = "journal.event.committed"


@dataclass(frozen=True, slots=True)
class ActorClaim:
    person_id: UUID
    organization_id: UUID | None
    role_assignment_id: UUID


@dataclass(frozen=True, slots=True)
class AppendedEvent:
    event_id: UUID
    event_hash: str
    local_sequence: int


@dataclass(frozen=True, slots=True)
class IntegrityFailure:
    sequence: int
    event_id: UUID
    code: str


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    ok: bool
    node_id: UUID
    checked_events: int
    last_sequence: int
    last_event_hash: str | None
    failures: tuple[IntegrityFailure, ...]


def signer_from_settings(settings: Settings) -> NodeSigner:
    seed_hex = read_text_secret(settings.node_signing_seed_file, minimum_length=64)
    return NodeSigner.from_seed_hex(seed_hex)


async def initialize_node_key(session: AsyncSession, settings: Settings) -> UUID:
    """Register public key material while keeping the seed in the mounted secret."""

    profile = (
        await session.execute(
            select(NodeProfile).where(NodeProfile.node_code == settings.node_code)
        )
    ).scalar_one_or_none()
    if profile is None:
        raise DomainError(
            code="NODE_PROFILE_NOT_INITIALIZED",
            message_key="errors.journal.node_profile_not_initialized",
            status_code=503,
        )
    signer = signer_from_settings(settings)
    active = list(
        (
            await session.execute(
                select(NodeKeyRecord).where(
                    NodeKeyRecord.node_id == profile.id,
                    NodeKeyRecord.purpose == "NODE_SIGNING",
                    NodeKeyRecord.status == "ACTIVE",
                )
            )
        ).scalars()
    )
    for record in active:
        if hmac.compare_digest(record.fingerprint, signer.fingerprint):
            if not hmac.compare_digest(record.public_key, signer.public_key_bytes):
                raise _service_error("NODE_PUBLIC_KEY_MISMATCH")
            await _ensure_chain_state(session, profile.id)
            return record.id
    if active:
        raise _service_error("NODE_KEY_ROTATION_REQUIRED")

    key_id = uuid5(
        NAMESPACE_URL,
        f"cooperative-clearing:node-key:{profile.id}:{signer.fingerprint}",
    )
    session.add(
        NodeKeyRecord(
            id=key_id,
            node_id=profile.id,
            purpose="NODE_SIGNING",
            algorithm=SIGNATURE_ALGORITHM,
            public_key=signer.public_key_bytes,
            fingerprint=signer.fingerprint,
            status="ACTIVE",
            valid_from=datetime.now(UTC),
        )
    )
    await _ensure_chain_state(session, profile.id)
    return key_id


async def _ensure_chain_state(session: AsyncSession, node_id: UUID) -> None:
    if await session.get(NodeChainState, node_id) is None:
        session.add(NodeChainState(node_id=node_id, next_sequence=1, last_event_hash=None))


class SignedJournalService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.signer = signer_from_settings(settings)

    async def append(
        self,
        session: AsyncSession,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_version: int,
        actor: ActorClaim,
        payload: dict[str, object],
        evidence: list[object] | None = None,
        assurance: CommandAssurance | None = None,
        occurred_at: datetime | None = None,
        schema_version: int = 1,
        offline_epoch_id: UUID | None = None,
    ) -> AppendedEvent:
        if not event_type or not aggregate_type or aggregate_version < 1 or schema_version < 1:
            raise DomainError(
                code="EVENT_ENVELOPE_INVALID",
                message_key="errors.journal.event_envelope_invalid",
                status_code=422,
            )
        assignment, user = await self._validate_actor_claim(session, actor)
        evidence_items = list(assurance.evidence_refs) if assurance is not None else evidence or []
        event_payload = self._assured_payload(
            event_type=event_type,
            payload=payload,
            evidence=evidence_items,
            assurance=assurance,
            actor=actor,
            role_code=assignment.role_code,
            role_source=assignment.source,
            user_id=user.id,
            role_cooperative_id=assignment.cooperative_id,
        )
        profile = (
            await session.execute(
                select(NodeProfile).where(NodeProfile.node_code == self.settings.node_code)
            )
        ).scalar_one_or_none()
        if profile is None:
            raise _service_error("NODE_PROFILE_NOT_INITIALIZED")
        chain = (
            await session.execute(
                select(NodeChainState).where(NodeChainState.node_id == profile.id).with_for_update()
            )
        ).scalar_one_or_none()
        if chain is None:
            raise _service_error("NODE_CHAIN_NOT_INITIALIZED")
        key = (
            await session.execute(
                select(NodeKeyRecord).where(
                    NodeKeyRecord.node_id == profile.id,
                    NodeKeyRecord.purpose == "NODE_SIGNING",
                    NodeKeyRecord.status == "ACTIVE",
                )
            )
        ).scalar_one_or_none()
        if key is None:
            raise _service_error("NODE_SIGNING_KEY_UNAVAILABLE")
        if not hmac.compare_digest(key.fingerprint, self.signer.fingerprint):
            raise _service_error("NODE_SIGNING_KEY_MISMATCH")

        event_id = uuid4()
        timestamp = (occurred_at or datetime.now(UTC)).astimezone(UTC)
        payload_digest = payload_hash(event_payload)
        envelope = build_envelope(
            event_id=event_id,
            event_type=event_type,
            schema_version=schema_version,
            node_id=profile.id,
            local_sequence=chain.next_sequence,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            actor=actor,
            occurred_at=timestamp,
            payload=event_payload,
            evidence=evidence_items,
            previous_event_hash=chain.last_event_hash,
            payload_digest=payload_digest,
            offline_epoch_id=offline_epoch_id,
        )
        canonical = canonicalize(envelope)
        event_digest = sha256_ref(canonical)
        signature = self.signer.sign(canonical)

        event = SignedEvent(
            event_id=event_id,
            event_type=event_type,
            schema_version=schema_version,
            protocol_version=PROTOCOL_VERSION,
            canonicalization_profile=CANONICALIZATION_PROFILE,
            node_id=profile.id,
            offline_epoch_id=offline_epoch_id,
            local_sequence=chain.next_sequence,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            actor_person_id=actor.person_id,
            actor_organization_id=actor.organization_id,
            actor_role_assignment_id=actor.role_assignment_id,
            occurred_at=timestamp,
            payload=event_payload,
            evidence=evidence_items,
            previous_event_hash=chain.last_event_hash,
            payload_hash=payload_digest,
            event_hash=event_digest,
            canonical_envelope=canonical,
        )
        session.add(event)
        await session.flush()
        session.add(
            EventSignature(
                id=uuid4(),
                event_id=event_id,
                key_id=key.id,
                signature_scope="NODE",
                algorithm=SIGNATURE_ALGORITHM,
                signature=signature,
            )
        )
        session.add(
            OutboxMessage(
                id=uuid4(),
                event_id=event_id,
                topic=OUTBOX_TOPIC,
                payload={
                    "event_id": str(event_id),
                    "event_type": event_type,
                    "event_hash": event_digest,
                    "node_id": str(profile.id),
                    "local_sequence": chain.next_sequence,
                },
                status="PENDING",
            )
        )
        sequence = chain.next_sequence
        chain.next_sequence += 1
        chain.last_event_hash = event_digest
        chain.updated_at = datetime.now(UTC)
        return AppendedEvent(event_id, event_digest, sequence)

    @staticmethod
    async def _validate_actor_claim(
        session: AsyncSession, actor: ActorClaim
    ) -> tuple[RoleAssignment, UserAccount]:
        assignment = await session.get(RoleAssignment, actor.role_assignment_id)
        if assignment is None or assignment.status != "ACTIVE":
            raise _actor_error("ACTOR_ROLE_INACTIVE")
        user = await session.get(UserAccount, assignment.user_id)
        if user is None or user.status != "ACTIVE":
            raise _actor_error("ACTOR_USER_INACTIVE")
        if user.member_id != actor.person_id:
            raise _actor_error("ACTOR_PERSON_MISMATCH")
        if (
            assignment.cooperative_id is not None
            and assignment.cooperative_id != actor.organization_id
        ):
            raise _actor_error("ACTOR_SCOPE_MISMATCH")
        return assignment, user

    @staticmethod
    def _assured_payload(
        *,
        event_type: str,
        payload: dict[str, object],
        evidence: list[object],
        assurance: CommandAssurance | None,
        actor: ActorClaim,
        role_code: str,
        role_source: str,
        user_id: UUID,
        role_cooperative_id: UUID | None,
    ) -> dict[str, object]:
        if "_command_assurance" in payload:
            raise _actor_error("COMMAND_ASSURANCE_RESERVED")
        if event_type in CRITICAL_EVENT_TYPES and assurance is None:
            raise _actor_error("CRITICAL_COMMAND_ASSURANCE_REQUIRED")
        if assurance is None:
            return payload
        if not evidence:
            raise _actor_error("CRITICAL_COMMAND_EVIDENCE_REQUIRED")
        return {
            **payload,
            "_command_assurance": {
                "format": "critical-command-assurance-v1",
                "actor": {
                    "person_id": str(actor.person_id),
                    "user_id": str(user_id),
                },
                "role": {
                    "assignment_id": str(actor.role_assignment_id),
                    "code": role_code,
                    "source": role_source,
                },
                "scope": {
                    "actor_organization_id": (
                        str(actor.organization_id)
                        if actor.organization_id is not None
                        else None
                    ),
                    "role_cooperative_id": (
                        str(role_cooperative_id)
                        if role_cooperative_id is not None
                        else None
                    ),
                },
                "evidence": {
                    "count": len(evidence),
                    "digest": sha256_ref(canonicalize(evidence)),
                },
                "exposure": _exposure_payload(assurance.exposure),
            },
        }


def build_envelope(
    *,
    event_id: UUID,
    event_type: str,
    schema_version: int,
    node_id: UUID,
    local_sequence: int,
    aggregate_type: str,
    aggregate_id: UUID,
    aggregate_version: int,
    actor: ActorClaim,
    occurred_at: datetime,
    payload: dict[str, object],
    evidence: list[object],
    previous_event_hash: str | None,
    payload_digest: str,
    offline_epoch_id: UUID | None = None,
) -> dict[str, object]:
    envelope: dict[str, object] = {
        "event_id": str(event_id),
        "event_type": event_type,
        "schema_version": schema_version,
        "protocol_version": PROTOCOL_VERSION,
        "canonicalization_profile": CANONICALIZATION_PROFILE,
        "node_id": str(node_id),
        "local_sequence": local_sequence,
        "aggregate": {
            "type": aggregate_type,
            "id": str(aggregate_id),
            "version": aggregate_version,
        },
        "actor": {
            "person_id": str(actor.person_id),
            "organization_id": (
                str(actor.organization_id) if actor.organization_id is not None else None
            ),
            "role_assignment_id": str(actor.role_assignment_id),
        },
        "occurred_at": utc_timestamp(occurred_at),
        "payload": payload,
        "evidence": evidence,
        "previous_event_hash": previous_event_hash,
        "payload_hash": payload_digest,
    }
    if offline_epoch_id is not None:
        envelope["offline_epoch_id"] = str(offline_epoch_id)
    return envelope


def envelope_from_event(event: SignedEvent) -> dict[str, object]:
    return build_envelope(
        event_id=event.event_id,
        event_type=event.event_type,
        schema_version=event.schema_version,
        node_id=event.node_id,
        local_sequence=event.local_sequence,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        aggregate_version=event.aggregate_version,
        actor=ActorClaim(
            person_id=event.actor_person_id,
            organization_id=event.actor_organization_id,
            role_assignment_id=event.actor_role_assignment_id,
        ),
        occurred_at=event.occurred_at,
        payload=event.payload,
        evidence=event.evidence,
        previous_event_hash=event.previous_event_hash,
        payload_digest=event.payload_hash,
        offline_epoch_id=event.offline_epoch_id,
    )


async def verify_journal(session: AsyncSession, node_id: UUID) -> IntegrityReport:
    rows = list(
        (
            await session.execute(
                select(SignedEvent, EventSignature, NodeKeyRecord)
                .join(EventSignature, EventSignature.event_id == SignedEvent.event_id)
                .join(NodeKeyRecord, NodeKeyRecord.id == EventSignature.key_id)
                .where(
                    SignedEvent.node_id == node_id,
                    EventSignature.signature_scope == "NODE",
                )
                .order_by(SignedEvent.local_sequence)
            )
        ).all()
    )
    failures: list[IntegrityFailure] = []
    expected_sequence = 1
    previous_hash: str | None = None
    for event, signature, key in rows:
        code: str | None = None
        canonical = canonicalize(envelope_from_event(event))
        if event.local_sequence != expected_sequence:
            code = "SEQUENCE_GAP"
        elif event.previous_event_hash != previous_hash:
            code = "PREVIOUS_HASH_MISMATCH"
        elif event.canonicalization_profile != CANONICALIZATION_PROFILE:
            code = "CANONICAL_PROFILE_UNSUPPORTED"
        elif event.payload_hash != payload_hash(event.payload):
            code = "PAYLOAD_HASH_MISMATCH"
        elif not hmac.compare_digest(event.canonical_envelope, canonical):
            code = "CANONICAL_BYTES_MISMATCH"
        elif event.event_hash != sha256_ref(canonical):
            code = "EVENT_HASH_MISMATCH"
        elif signature.algorithm != SIGNATURE_ALGORITHM:
            code = "SIGNATURE_ALGORITHM_UNSUPPORTED"
        elif key.algorithm != SIGNATURE_ALGORITHM:
            code = "KEY_ALGORITHM_UNSUPPORTED"
        elif event.occurred_at < key.valid_from:
            code = "KEY_NOT_YET_VALID"
        elif key.valid_until is not None and event.occurred_at >= key.valid_until:
            code = "KEY_EXPIRED"
        elif key.revoked_at is not None and event.occurred_at >= key.revoked_at:
            code = "KEY_REVOKED_AT_EVENT_TIME"
        elif not verify_signature(key.public_key, signature.signature, canonical):
            code = "SIGNATURE_INVALID"
        if code is not None:
            failures.append(IntegrityFailure(event.local_sequence, event.event_id, code))
        previous_hash = event.event_hash
        expected_sequence = event.local_sequence + 1

    chain = await session.get(NodeChainState, node_id)
    if chain is None:
        failures.append(IntegrityFailure(0, UUID(int=0), "CHAIN_STATE_MISSING"))
    elif chain.next_sequence != expected_sequence or chain.last_event_hash != previous_hash:
        failures.append(
            IntegrityFailure(max(0, expected_sequence - 1), UUID(int=0), "CHAIN_HEAD_MISMATCH")
        )
    return IntegrityReport(
        ok=not failures,
        node_id=node_id,
        checked_events=len(rows),
        last_sequence=max(0, expected_sequence - 1),
        last_event_hash=previous_hash,
        failures=tuple(failures),
    )


def _service_error(code: str) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.journal.{code.lower()}",
        status_code=503,
    )


def _actor_error(code: str) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.journal.{code.lower()}",
        status_code=409,
    )


def _exposure_payload(claim: ExposureClaim) -> dict[str, object]:
    subject_type = claim.subject_type.strip()
    unit = claim.unit.strip() if claim.unit is not None else None
    basis_refs = tuple(item.strip() for item in claim.basis_refs if item.strip())
    if not subject_type:
        raise _actor_error("CRITICAL_COMMAND_EXPOSURE_INVALID")
    if claim.amount is not None and claim.amount <= 0:
        raise _actor_error("CRITICAL_COMMAND_EXPOSURE_INVALID")
    if claim.maximum_loss is not None and claim.maximum_loss < 0:
        raise _actor_error("CRITICAL_COMMAND_EXPOSURE_INVALID")
    if (claim.amount is None) != (unit is None):
        raise _actor_error("CRITICAL_COMMAND_EXPOSURE_INVALID")
    if claim.amount is None and not basis_refs:
        raise _actor_error("CRITICAL_COMMAND_EXPOSURE_INVALID")
    return {
        "category": claim.category.value,
        "effect": claim.effect.value,
        "subject_type": subject_type,
        "subject_id": str(claim.subject_id),
        "amount": format(claim.amount, "f") if claim.amount is not None else None,
        "unit": unit,
        "maximum_loss": (
            format(claim.maximum_loss, "f") if claim.maximum_loss is not None else None
        ),
        "basis_refs": list(basis_refs),
    }
