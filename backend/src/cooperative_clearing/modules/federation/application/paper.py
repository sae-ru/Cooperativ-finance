"""Numbered paper-form intake for bounded offline federation operations."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.federation.application.common import (
    FederationCommandResult,
    audit_federation_action,
    begin_federation_command,
    complete_federation_command,
    federation_actor,
)
from cooperative_clearing.modules.federation.application.service import (
    AUDIT_ROLES,
    REGISTRAR_ROLES,
)
from cooperative_clearing.modules.federation.domain.types import (
    federation_error,
    normalize_code,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    FederationPaperForm,
    OfflineEpoch,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.journal.application.service import SignedJournalService
from cooperative_clearing.modules.journal.domain.crypto import payload_hash, utc_timestamp
from cooperative_clearing.shared.core.config import Settings

PAPER_EVENT_TYPES = {
    "federation.paper_form_issued",
    "federation.paper_operation_recorded",
    "federation.paper_form_voided",
}
PAPER_FORM_TYPES = {
    "GOODS_TRANSFER",
    "LOGISTICS_HANDOFF",
    "SERVICE_ACCEPTANCE",
    "EMERGENCY_NODE_ACTION",
    "EXCEPTION",
}
ISSUER_ROLES = REGISTRAR_ROLES | {RoleCode.NODE_BUSINESS_OPERATOR}
RECORDER_ROLES = AUDIT_ROLES | {
    RoleCode.NODE_SECURITY_ADMIN,
    RoleCode.SECURITY_ADMIN,
}


def bounded_reference(value: str, *, maximum: int = 160) -> str:
    result = value.strip()
    if (
        not result
        or len(result) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in result)
    ):
        raise federation_error("PAPER_REFERENCE_INVALID", 422)
    return result


def paper_form_checksum(
    *,
    node_id: UUID,
    epoch_id: UUID,
    serial_number: str,
    form_type: str,
    form_version: int,
    participant_refs: list[str],
    operation_constraints: dict[str, object],
    issued_at: datetime,
    expires_at: datetime,
) -> str:
    return payload_hash(
        {
            "node_id": str(node_id),
            "epoch_id": str(epoch_id),
            "serial_number": serial_number,
            "form_type": form_type,
            "form_version": form_version,
            "participant_refs": participant_refs,
            "operation_constraints": operation_constraints,
            "issued_at": utc_timestamp(issued_at),
            "expires_at": utc_timestamp(expires_at),
        }
    )


class PaperFormService:
    def __init__(self, settings: Settings) -> None:
        self.journal = SignedJournalService(settings)

    async def issue(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        epoch_id: UUID,
        serial_number: str,
        form_type: str,
        form_version: int,
        participant_refs: list[str],
        operation_constraints: dict[str, object],
        expires_at: datetime,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, ISSUER_ROLES)
        serial = normalize_code(serial_number)
        normalized_type = form_type.strip().upper()
        participants = [bounded_reference(item) for item in participant_refs]
        now = datetime.now(UTC)
        expiry = expires_at.astimezone(UTC)
        if (
            normalized_type not in PAPER_FORM_TYPES
            or not 1 <= form_version <= 100
            or not participants
            or len(set(participants)) != len(participants)
            or not operation_constraints
            or expiry <= now
        ):
            raise federation_error("PAPER_FORM_TERMS_INVALID", 422)
        command = {
            "epoch_id": str(epoch_id),
            "serial_number": serial,
            "form_type": normalized_type,
            "form_version": form_version,
            "participant_refs": participants,
            "operation_constraints": operation_constraints,
            "expires_at": utc_timestamp(expiry),
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.issue_paper_form", idempotency_key, command
        )
        if replay is not None:
            return replay
        epoch = await session.get(OfflineEpoch, epoch_id, with_for_update=True)
        if (
            epoch is None
            or epoch.external_node_id is None
            or epoch.status != "OPEN"
            or now < epoch.starts_at
            or epoch.expires_at is None
            or now >= epoch.expires_at
            or expiry > epoch.expires_at
            or not PAPER_EVENT_TYPES.issubset(set(epoch.allowed_event_types))
        ):
            raise federation_error("PAPER_FORM_EPOCH_INVALID")
        node = await session.get(ExternalNode, epoch.external_node_id)
        if node is None or node.status not in {"LIMITED", "ACTIVE"}:
            raise federation_error("PAPER_FORM_NODE_INVALID")
        checksum = paper_form_checksum(
            node_id=node.id,
            epoch_id=epoch.id,
            serial_number=serial,
            form_type=normalized_type,
            form_version=form_version,
            participant_refs=participants,
            operation_constraints=operation_constraints,
            issued_at=now,
            expires_at=expiry,
        )
        form_id = uuid4()
        qr_reference = f"CCPF:1:{node.node_code}:{serial}:{checksum[7:23]}"
        event = await self.journal.append(
            session,
            event_type="federation.paper_form_issued",
            aggregate_type="federation_paper_form",
            aggregate_id=form_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **command,
                "form_id": str(form_id),
                "node_id": str(node.id),
                "checksum": checksum,
                "qr_reference": qr_reference,
                "issued_at": utc_timestamp(now),
            },
            offline_epoch_id=epoch.id,
        )
        session.add(
            FederationPaperForm(
                id=form_id,
                external_node_id=node.id,
                epoch_id=epoch.id,
                serial_number=serial,
                qr_reference=qr_reference,
                checksum=checksum,
                form_type=normalized_type,
                form_version=form_version,
                participant_refs=participants,
                operation_constraints=operation_constraints,
                status="ISSUED",
                issued_at=now,
                expires_at=expiry,
                issued_by_user_id=principal.user_id,
                issued_by_member_id=actor.person_id,
                issued_role_assignment_id=actor.role_assignment_id,
                issued_event_id=event.event_id,
            )
        )
        await audit_federation_action(
            session,
            principal,
            "FEDERATION_PAPER_FORM_ISSUED",
            "FederationPaperForm",
            form_id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, form_id)

    async def record(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        form_id: UUID,
        expected_version: int,
        checksum: str,
        operation_payload: dict[str, object],
        signatures: list[object],
        evidence_ids: list[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, RECORDER_ROLES)
        normalized_checksum = checksum.strip().lower()
        evidence = sorted({str(item) for item in evidence_ids})
        command = {
            "form_id": str(form_id),
            "expected_version": expected_version,
            "checksum": normalized_checksum,
            "operation_payload": operation_payload,
            "signatures": signatures,
            "evidence_ids": evidence,
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.record_paper_form", idempotency_key, command
        )
        if replay is not None:
            return replay
        form = await self._locked_form(session, form_id)
        now = datetime.now(UTC)
        if form.version != expected_version:
            raise federation_error("AGGREGATE_VERSION_CONFLICT")
        if (
            form.status != "ISSUED"
            or now >= form.expires_at
            or normalized_checksum != form.checksum
            or not operation_payload
            or not signatures
            or not evidence
        ):
            raise federation_error("PAPER_FORM_RECORDING_INVALID")
        if actor.person_id == form.issued_by_member_id:
            raise federation_error("INDEPENDENT_PAPER_RECORDER_REQUIRED")
        epoch = await session.get(OfflineEpoch, form.epoch_id)
        if epoch is None or epoch.status != "OPEN":
            raise federation_error("PAPER_FORM_EPOCH_INVALID")
        recorded_payload = {
            "operation_payload": operation_payload,
            "signatures": signatures,
            "evidence_ids": evidence,
        }
        recorded_hash = payload_hash(recorded_payload)
        event = await self.journal.append(
            session,
            event_type="federation.paper_operation_recorded",
            aggregate_type="federation_paper_form",
            aggregate_id=form.id,
            aggregate_version=form.version + 1,
            actor=actor,
            payload={
                **command,
                "node_id": str(form.external_node_id),
                "epoch_id": str(form.epoch_id),
                "serial_number": form.serial_number,
                "payload_hash": recorded_hash,
                "issued_event_id": str(form.issued_event_id),
            },
            offline_epoch_id=form.epoch_id,
        )
        form.status = "RECORDED"
        form.payload = operation_payload
        form.payload_hash = recorded_hash
        form.signatures = signatures
        form.evidence_ids = evidence
        form.recorded_by_user_id = principal.user_id
        form.recorded_by_member_id = actor.person_id
        form.recorded_role_assignment_id = actor.role_assignment_id
        form.recorded_event_id = event.event_id
        form.recorded_at = now
        form.version += 1
        await audit_federation_action(
            session,
            principal,
            "FEDERATION_PAPER_OPERATION_RECORDED",
            "FederationPaperForm",
            form.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, form.id)

    async def void(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        form_id: UUID,
        expected_version: int,
        rationale: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, AUDIT_ROLES | REGISTRAR_ROLES)
        reason = bounded_reference(rationale, maximum=4000)
        command = {
            "form_id": str(form_id),
            "expected_version": expected_version,
            "rationale": reason,
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.void_paper_form", idempotency_key, command
        )
        if replay is not None:
            return replay
        form = await self._locked_form(session, form_id)
        if form.version != expected_version:
            raise federation_error("AGGREGATE_VERSION_CONFLICT")
        if form.status != "ISSUED" or actor.person_id == form.issued_by_member_id:
            raise federation_error("PAPER_FORM_VOID_INVALID")
        epoch = await session.get(OfflineEpoch, form.epoch_id)
        if epoch is None or epoch.status != "OPEN":
            raise federation_error("PAPER_FORM_EPOCH_INVALID")
        now = datetime.now(UTC)
        event = await self.journal.append(
            session,
            event_type="federation.paper_form_voided",
            aggregate_type="federation_paper_form",
            aggregate_id=form.id,
            aggregate_version=form.version + 1,
            actor=actor,
            payload={
                **command,
                "node_id": str(form.external_node_id),
                "epoch_id": str(form.epoch_id),
                "serial_number": form.serial_number,
                "issued_event_id": str(form.issued_event_id),
            },
            offline_epoch_id=form.epoch_id,
        )
        form.status = "VOID"
        form.voided_by_user_id = principal.user_id
        form.voided_by_member_id = actor.person_id
        form.voided_event_id = event.event_id
        form.voided_at = now
        form.void_reason = reason
        form.version += 1
        await audit_federation_action(
            session,
            principal,
            "FEDERATION_PAPER_FORM_VOIDED",
            "FederationPaperForm",
            form.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, form.id)

    @staticmethod
    async def _locked_form(session: AsyncSession, form_id: UUID) -> FederationPaperForm:
        form = await session.get(FederationPaperForm, form_id, with_for_update=True)
        if form is None:
            raise federation_error("PAPER_FORM_NOT_FOUND", 404)
        return form
