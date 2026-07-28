"""Transactional inter-node clearing lifecycle and local finality application."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.clearing.domain.engine import RoundingMode
from cooperative_clearing.modules.federation.domain.federated_clearing import (
    FEDERATED_ALGORITHM_ID,
    FEDERATED_ALGORITHM_VERSION,
    FederatedClearingPolicy,
    FederatedClearingResult,
    FederatedObligationInput,
    apply_receipt_payload,
    approval_payload,
    calculate_federated_clearing,
    commit_certificate_payload,
    prepare_receipt_payload,
    reconciliation_proof_payload,
    snapshot_payload,
)
from cooperative_clearing.modules.federation.domain.types import federation_error
from cooperative_clearing.modules.federation.infrastructure.clearing_models import (
    FederatedClearingCycle,
    FederatedClearingPolicyRecord,
    FederatedClearingProof,
    FederatedClearingProposal,
    FederatedCommitCertificate,
    FederatedInputSnapshot,
    FederatedObligationApplication,
    InterNodeObligation,
    NodeApplyReceipt,
    NodeClearingApproval,
    NodePrepareReceipt,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    NodeBilateralLimit,
    NodeExposure,
)
from cooperative_clearing.modules.journal.application.service import (
    ActorClaim,
    SignedJournalService,
    signer_from_settings,
)
from cooperative_clearing.modules.journal.domain.assurance import (
    CommandAssurance,
    ExposureCategory,
    ExposureClaim,
    ExposureEffect,
)
from cooperative_clearing.modules.journal.domain.crypto import canonicalize, payload_hash
from cooperative_clearing.modules.journal.infrastructure.models import NodeChainState
from cooperative_clearing.modules.node.domain.node_code import NodeCode
from cooperative_clearing.modules.node.infrastructure.models import NodeProfile
from cooperative_clearing.shared.core.config import Settings


@dataclass(frozen=True, slots=True)
class SignedArtifact:
    payload: dict[str, object]
    artifact_hash: str
    signature: bytes
    signer_fingerprint: str


class InterNodeClearingService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.signer = signer_from_settings(settings)
        self.journal = SignedJournalService(settings)

    async def create_policy(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        actor: ActorClaim,
        policy_code: str,
        valuation_unit: str,
        policy: FederatedClearingPolicy,
    ) -> FederatedClearingPolicyRecord:
        code = _bounded_code(policy_code, 80)
        unit = _bounded_code(valuation_unit, 32)
        policy_payload = {"policy_code": code, "valuation_unit": unit, **policy.payload()}
        digest = payload_hash(policy_payload)
        existing = (
            await session.execute(
                select(FederatedClearingPolicyRecord).where(
                    FederatedClearingPolicyRecord.policy_code == code,
                    FederatedClearingPolicyRecord.policy_version == policy.policy_version,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.policy_hash != digest:
                raise federation_error("FEDERATED_POLICY_VERSION_CONFLICT", 409)
            return existing
        active = (
            await session.execute(
                select(FederatedClearingPolicyRecord)
                .where(
                    FederatedClearingPolicyRecord.policy_code == code,
                    FederatedClearingPolicyRecord.status == "ACTIVE",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if active is not None and active.policy_version >= policy.policy_version:
            raise federation_error("FEDERATED_POLICY_VERSION_NOT_ADVANCED", 409)
        record_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="federation.clearing_policy_activated",
            aggregate_type="federated_clearing_policy",
            aggregate_id=record_id,
            aggregate_version=1,
            actor=actor,
            payload={**policy_payload, "policy_hash": digest},
        )
        if active is not None:
            active.status = "SUPERSEDED"
            active.version += 1
            await session.flush()
        row = FederatedClearingPolicyRecord(
            id=record_id,
            policy_code=code,
            policy_version=policy.policy_version,
            valuation_unit=unit,
            algorithm_id=FEDERATED_ALGORITHM_ID,
            algorithm_version=FEDERATED_ALGORITHM_VERSION,
            decimal_scale=policy.decimal_scale,
            rounding_mode=policy.rounding_mode.value,
            minimum_operation=policy.minimum_operation,
            max_iterations=policy.max_iterations,
            max_cycle_length=policy.max_cycle_length,
            prepare_ttl_seconds=policy.prepare_ttl_seconds,
            policy_payload=policy_payload,
            policy_hash=digest,
            status="ACTIVE",
            created_by_user_id=user_id,
            created_by_member_id=actor.person_id,
            created_role_assignment_id=actor.role_assignment_id,
            created_event_id=event.event_id,
        )
        session.add(row)
        return row

    async def register_obligation(
        self,
        session: AsyncSession,
        *,
        actor: ActorClaim,
        home_node_code: str,
        debtor_node_code: str,
        creditor_node_code: str,
        unit_code: str,
        amount: Decimal,
        source_reference: str,
        source_event_hash: str,
        liquidity_class: str = "UNASSESSED",
        obligation_id: UUID | None = None,
    ) -> InterNodeObligation:
        domain = FederatedObligationInput(
            obligation_id=str(obligation_id or uuid4()),
            home_node_code=home_node_code,
            debtor_node_code=debtor_node_code,
            creditor_node_code=creditor_node_code,
            unit_code=_bounded_code(unit_code, 32),
            amount=amount,
            version=1,
            liquidity_class=_bounded_code(liquidity_class, 32),
            source_event_hash=source_event_hash,
        ).validate()
        if str(NodeCode(domain.home_node_code)) != self.settings.node_code:
            raise federation_error("FEDERATED_OBLIGATION_NOT_HOME_NODE", 422)
        row_id = UUID(domain.obligation_id)
        existing = await session.get(InterNodeObligation, row_id)
        if existing is not None:
            if (
                existing.source_event_hash != source_event_hash
                or existing.original_amount != amount
                or existing.debtor_node_code != str(NodeCode(debtor_node_code))
                or existing.creditor_node_code != str(NodeCode(creditor_node_code))
            ):
                raise federation_error("FEDERATED_OBLIGATION_ID_CONFLICT", 409)
            return existing
        reference = source_reference.strip()
        if not reference or len(reference) > 200:
            raise federation_error("FEDERATED_SOURCE_REFERENCE_INVALID", 422)
        event = await self.journal.append(
            session,
            event_type="federation.inter_node_obligation_confirmed",
            aggregate_type="inter_node_obligation",
            aggregate_id=row_id,
            aggregate_version=1,
            actor=actor,
            payload={**domain.payload(), "source_reference": reference},
        )
        row = InterNodeObligation(
            id=row_id,
            home_node_code=self.settings.node_code,
            debtor_node_code=str(NodeCode(debtor_node_code)),
            creditor_node_code=str(NodeCode(creditor_node_code)),
            unit_code=domain.unit_code,
            original_amount=amount,
            outstanding_amount=amount,
            cleared_amount=Decimal(0),
            source_reference=reference,
            source_event_hash=source_event_hash,
            liquidity_class=domain.liquidity_class,
            status="CONFIRMED",
            prepared_cycle_id=None,
            prepared_input_hash=None,
            prepared_until=None,
            created_event_id=event.event_id,
        )
        session.add(row)
        return row

    async def create_cycle(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        actor: ActorClaim,
        cycle_id: UUID,
        cycle_code: str,
        coordinator_node_code: str,
        policy: FederatedClearingPolicyRecord,
        period_start: datetime,
        period_end: datetime,
        participant_node_codes: tuple[str, ...],
    ) -> FederatedClearingCycle:
        if period_end <= period_start:
            raise federation_error("FEDERATED_CYCLE_PERIOD_INVALID", 422)
        coordinator = str(NodeCode(coordinator_node_code))
        participants = tuple(sorted({str(NodeCode(code)) for code in participant_node_codes}))
        if len(participants) < 2:
            raise federation_error("FEDERATED_PARTICIPANTS_INSUFFICIENT", 422)
        existing = await session.get(FederatedClearingCycle, cycle_id)
        if existing is not None:
            if (
                existing.cycle_code != cycle_code
                or existing.coordinator_node_code != coordinator
                or existing.policy_id != policy.id
                or tuple(existing.participant_node_codes) != participants
            ):
                raise federation_error("FEDERATED_CYCLE_ID_CONFLICT", 409)
            return existing
        code = _bounded_code(cycle_code, 80)
        event = await self.journal.append(
            session,
            event_type="federation.clearing_cycle_created",
            aggregate_type="federated_clearing_cycle",
            aggregate_id=cycle_id,
            aggregate_version=1,
            actor=actor,
            payload={
                "cycle_id": str(cycle_id),
                "cycle_code": code,
                "coordinator_node_code": coordinator,
                "policy_hash": policy.policy_hash,
                "period_start": period_start.astimezone(UTC).isoformat(),
                "period_end": period_end.astimezone(UTC).isoformat(),
                "participant_node_codes": list(participants),
            },
        )
        row = FederatedClearingCycle(
            id=cycle_id,
            cycle_code=code,
            coordinator_node_code=coordinator,
            policy_id=policy.id,
            period_start=period_start,
            period_end=period_end,
            status="DRAFT",
            participant_node_codes=list(participants),
            affected_node_codes=[],
            input_hash=None,
            result_hash=None,
            certificate_hash=None,
            created_by_user_id=user_id,
            created_by_member_id=actor.person_id,
            created_role_assignment_id=actor.role_assignment_id,
            created_actor_organization_id=actor.organization_id,
            created_event_id=event.event_id,
        )
        session.add(row)
        return row

    async def create_local_snapshot(
        self, session: AsyncSession, *, cycle_id: UUID
    ) -> FederatedInputSnapshot:
        cycle = await self._cycle(session, cycle_id, lock=True)
        existing = await self._snapshot(session, cycle.id, self.settings.node_code)
        if existing is not None:
            return existing
        if cycle.status not in {"DRAFT", "COLLECTING_SNAPSHOTS"}:
            raise federation_error("FEDERATED_CYCLE_NOT_COLLECTING", 409)
        if self.settings.node_code not in cycle.participant_node_codes:
            raise federation_error("FEDERATED_NODE_NOT_PARTICIPANT", 409)
        policy = await self._policy(session, cycle.policy_id)
        rows = list(
            (
                await session.execute(
                    select(InterNodeObligation)
                    .where(
                        InterNodeObligation.home_node_code == self.settings.node_code,
                        InterNodeObligation.unit_code == policy.valuation_unit,
                        InterNodeObligation.debtor_node_code.in_(cycle.participant_node_codes),
                        InterNodeObligation.creditor_node_code.in_(cycle.participant_node_codes),
                        InterNodeObligation.status.in_(["CONFIRMED", "PARTIALLY_CLEARED"]),
                        InterNodeObligation.created_at < cycle.period_end,
                    )
                    .order_by(InterNodeObligation.id)
                )
            ).scalars()
        )
        obligations = tuple(_domain_obligation(row) for row in rows)
        checkpoint = await self._checkpoint_hash(session)
        now = datetime.now(UTC).replace(microsecond=0)
        expiry = now + timedelta(seconds=policy.prepare_ttl_seconds * 2)
        document = snapshot_payload(
            cycle_id=str(cycle.id),
            node_code=self.settings.node_code,
            obligations=obligations,
            checkpoint_hash=checkpoint,
            policy_hash=policy.policy_hash,
            signed_at=now,
            expires_at=expiry,
        )
        signature = self.signer.sign(canonicalize(document))
        actor = _actor_from_cycle(cycle)
        event = await self.journal.append(
            session,
            event_type="federation.clearing_snapshot_signed",
            aggregate_type="federated_clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload=document,
        )
        row = FederatedInputSnapshot(
            id=uuid4(),
            cycle_id=cycle.id,
            node_code=self.settings.node_code,
            snapshot_payload=document,
            snapshot_hash=_hash_field(document, "snapshot_hash"),
            checkpoint_hash=checkpoint,
            node_signature=signature,
            signer_fingerprint=self.signer.fingerprint,
            accepted_event_id=event.event_id,
            expires_at=expiry,
        )
        session.add(row)
        if cycle.status == "DRAFT":
            await _transition(session, cycle, "COLLECTING_SNAPSHOTS")
        return row

    async def accept_snapshot(
        self,
        session: AsyncSession,
        *,
        cycle_id: UUID,
        artifact: SignedArtifact,
        actor: ActorClaim,
    ) -> FederatedInputSnapshot:
        cycle = await self._cycle(session, cycle_id, lock=True)
        node_code = _payload_node(artifact.payload)
        existing = await self._snapshot(session, cycle.id, node_code)
        if existing is not None:
            if existing.snapshot_hash != artifact.artifact_hash:
                raise federation_error("FEDERATED_SNAPSHOT_CONFLICT", 409)
            return existing
        if cycle.status not in {"DRAFT", "COLLECTING_SNAPSHOTS"}:
            raise federation_error("FEDERATED_CYCLE_NOT_COLLECTING", 409)
        if node_code not in cycle.participant_node_codes:
            raise federation_error("FEDERATED_NODE_NOT_PARTICIPANT", 409)
        policy = await self._policy(session, cycle.policy_id)
        if artifact.payload.get("cycle_id") != str(cycle.id):
            raise federation_error("FEDERATED_SNAPSHOT_CYCLE_MISMATCH", 422)
        if artifact.payload.get("snapshot_hash") != artifact.artifact_hash:
            raise federation_error("FEDERATED_SNAPSHOT_HASH_MISMATCH", 422)
        if artifact.payload.get("policy_hash") != policy.policy_hash:
            raise federation_error("FEDERATED_SNAPSHOT_POLICY_MISMATCH", 422)
        _verify_payload_hash(artifact.payload, "snapshot_hash")
        expires_at = _payload_datetime(artifact.payload, "expires_at")
        if expires_at <= datetime.now(UTC):
            raise federation_error("FEDERATED_SNAPSHOT_EXPIRED", 409)
        event = await self.journal.append(
            session,
            event_type="federation.clearing_snapshot_accepted",
            aggregate_type="federated_clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload={
                "node_code": node_code,
                "snapshot_hash": artifact.artifact_hash,
                "signer_fingerprint": artifact.signer_fingerprint,
            },
        )
        row = FederatedInputSnapshot(
            id=uuid4(),
            cycle_id=cycle.id,
            node_code=node_code,
            snapshot_payload=artifact.payload,
            snapshot_hash=artifact.artifact_hash,
            checkpoint_hash=_hash_field(artifact.payload, "checkpoint_hash"),
            node_signature=artifact.signature,
            signer_fingerprint=artifact.signer_fingerprint,
            accepted_event_id=event.event_id,
            expires_at=expires_at,
        )
        session.add(row)
        if cycle.status == "DRAFT":
            await _transition(session, cycle, "COLLECTING_SNAPSHOTS")
        return row

    async def calculate_preview(
        self, session: AsyncSession, *, cycle_id: UUID
    ) -> FederatedClearingResult:
        cycle = await self._cycle(session, cycle_id)
        policy = await self._policy(session, cycle.policy_id)
        snapshots = await self._all_snapshots(session, cycle)
        obligations = tuple(
            item
            for snapshot in snapshots
            for item in _snapshot_obligations(snapshot.snapshot_payload)
        )
        return calculate_federated_clearing(
            cycle_id=str(cycle.id), obligations=obligations, policy=_domain_policy(policy)
        )

    async def begin_prepare(
        self, session: AsyncSession, *, cycle_id: UUID, input_hash: str
    ) -> FederatedClearingCycle:
        cycle = await self._cycle(session, cycle_id, lock=True)
        if cycle.status == "PREPARING_NODES" and cycle.input_hash == input_hash:
            return cycle
        if cycle.status != "COLLECTING_SNAPSHOTS":
            raise federation_error("FEDERATED_CYCLE_NOT_READY_TO_PREPARE", 409)
        result = await self.calculate_preview(session, cycle_id=cycle.id)
        if result.clearing.input_hash != input_hash:
            raise federation_error("FEDERATED_INPUT_HASH_MISMATCH", 422)
        cycle.input_hash = input_hash
        cycle.affected_node_codes = list(result.affected_node_codes)
        await _transition(session, cycle, "PREPARING_NODES")
        return cycle

    async def prepare_local(
        self,
        session: AsyncSession,
        *,
        cycle_id: UUID,
        input_hash: str,
        snapshot_hash: str,
    ) -> NodePrepareReceipt:
        cycle = await self._cycle(session, cycle_id, lock=True)
        existing = await self._prepare_receipt(session, cycle.id, self.settings.node_code)
        if existing is not None:
            if (
                existing.receipt_payload.get("input_hash") != input_hash
                or existing.receipt_payload.get("snapshot_hash") != snapshot_hash
            ):
                raise federation_error("FEDERATED_PREPARE_CONFLICT", 409)
            return existing
        if cycle.status == "COLLECTING_SNAPSHOTS":
            preview = await self.calculate_preview(session, cycle_id=cycle.id)
            if preview.clearing.input_hash != input_hash:
                raise federation_error("FEDERATED_INPUT_HASH_MISMATCH", 409)
            cycle.input_hash = input_hash
            cycle.affected_node_codes = list(preview.affected_node_codes)
            await _transition(session, cycle, "PREPARING_NODES")
        if cycle.status != "PREPARING_NODES" or cycle.input_hash != input_hash:
            raise federation_error("FEDERATED_CYCLE_NOT_PREPARING", 409)
        snapshot = await self._snapshot(session, cycle.id, self.settings.node_code)
        if snapshot is None or snapshot.snapshot_hash != snapshot_hash:
            raise federation_error("FEDERATED_LOCAL_SNAPSHOT_MISMATCH", 409)
        policy = await self._policy(session, cycle.policy_id)
        now = datetime.now(UTC).replace(microsecond=0)
        expires_at = min(snapshot.expires_at, now + timedelta(seconds=policy.prepare_ttl_seconds))
        if expires_at <= now:
            raise federation_error("FEDERATED_PREPARE_EXPIRED", 409)
        obligations = _snapshot_obligations(snapshot.snapshot_payload)
        ids = [UUID(item.obligation_id) for item in obligations]
        rows = list(
            (
                await session.execute(
                    select(InterNodeObligation)
                    .where(InterNodeObligation.id.in_(ids))
                    .order_by(InterNodeObligation.id)
                    .with_for_update()
                )
            ).scalars()
        )
        if len(rows) != len(ids):
            raise federation_error("FEDERATED_OBLIGATION_MISSING", 409)
        by_id = {str(row.id): row for row in rows}
        versions: dict[str, int] = {}
        reserved_by_unit: dict[str, Decimal] = {}
        reserved_by_peer: dict[tuple[str, str], Decimal] = {}
        for item in obligations:
            row = by_id[item.obligation_id]
            if (
                row.version != item.version
                or row.outstanding_amount != item.amount
                or row.status not in {"CONFIRMED", "PARTIALLY_CLEARED"}
            ):
                raise federation_error("FEDERATED_OBLIGATION_VERSION_CONFLICT", 409)
            versions[str(row.id)] = row.version
            reserved_by_unit[row.unit_code] = (
                reserved_by_unit.get(row.unit_code, Decimal(0)) + row.outstanding_amount
            )
            peer_code = (
                row.creditor_node_code
                if row.debtor_node_code == self.settings.node_code
                else row.debtor_node_code
            )
            key = (peer_code, row.unit_code)
            reserved_by_peer[key] = reserved_by_peer.get(key, Decimal(0)) + row.outstanding_amount
        exposures = await self._lock_exposures(session, reserved_by_peer)
        document = prepare_receipt_payload(
            cycle_id=str(cycle.id),
            node_code=self.settings.node_code,
            input_hash=input_hash,
            snapshot_hash=snapshot_hash,
            obligation_versions=versions,
            reserved_by_unit=reserved_by_unit,
            expires_at=expires_at,
        )
        signature = self.signer.sign(canonicalize(document))
        actor = _actor_from_cycle(cycle)
        event = await self.journal.append(
            session,
            event_type="federation.clearing_node_prepared",
            aggregate_type="federated_clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload=document,
            assurance=CommandAssurance(
                exposure=ExposureClaim(
                    category=ExposureCategory.NODE,
                    effect=ExposureEffect.RESERVE,
                    subject_type="federated_clearing_cycle",
                    subject_id=cycle.id,
                    basis_refs=(
                        input_hash,
                        snapshot_hash,
                        str(document["receipt_hash"]),
                    ),
                ),
                evidence_refs=(
                    {
                        "input_hash": input_hash,
                        "snapshot_hash": snapshot_hash,
                        "receipt_hash": document["receipt_hash"],
                        "kind": "NODE_PREPARE_RECEIPT",
                    },
                ),
            ),
        )
        for row in rows:
            row.status = "PREPARED"
            row.prepared_cycle_id = cycle.id
            row.prepared_input_hash = input_hash
            row.prepared_until = expires_at
            row.updated_at = now
            row.version += 1
        for exposure, delta in exposures:
            exposure.reserved_amount += delta
            exposure.updated_event_id = event.event_id
            exposure.updated_at = now
            exposure.version += 1
        receipt = NodePrepareReceipt(
            id=uuid4(),
            cycle_id=cycle.id,
            node_code=self.settings.node_code,
            receipt_payload=document,
            receipt_hash=_hash_field(document, "receipt_hash"),
            node_signature=signature,
            signer_fingerprint=self.signer.fingerprint,
            accepted_event_id=event.event_id,
            expires_at=expires_at,
        )
        session.add(receipt)
        return receipt

    async def accept_prepare_receipt(
        self,
        session: AsyncSession,
        *,
        cycle_id: UUID,
        artifact: SignedArtifact,
        actor: ActorClaim,
    ) -> NodePrepareReceipt:
        cycle = await self._cycle(session, cycle_id, lock=True)
        node_code = _payload_node(artifact.payload)
        existing = await self._prepare_receipt(session, cycle.id, node_code)
        if existing is not None:
            if existing.receipt_hash != artifact.artifact_hash:
                raise federation_error("FEDERATED_PREPARE_CONFLICT", 409)
            return existing
        if (
            cycle.status not in {"PREPARING_NODES", "PREPARED", "PROPOSED", "VERIFYING"}
            or node_code not in cycle.participant_node_codes
        ):
            raise federation_error("FEDERATED_CYCLE_NOT_PREPARING", 409)
        if (
            artifact.payload.get("cycle_id") != str(cycle.id)
            or artifact.payload.get("input_hash") != cycle.input_hash
            or artifact.payload.get("receipt_hash") != artifact.artifact_hash
        ):
            raise federation_error("FEDERATED_PREPARE_BINDING_INVALID", 422)
        _verify_payload_hash(artifact.payload, "receipt_hash")
        expires_at = _payload_datetime(artifact.payload, "expires_at")
        if expires_at <= datetime.now(UTC):
            raise federation_error("FEDERATED_PREPARE_EXPIRED", 409)
        event = await self.journal.append(
            session,
            event_type="federation.clearing_prepare_receipt_accepted",
            aggregate_type="federated_clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload={
                "node_code": node_code,
                "receipt_hash": artifact.artifact_hash,
                "signer_fingerprint": artifact.signer_fingerprint,
            },
        )
        row = NodePrepareReceipt(
            id=uuid4(),
            cycle_id=cycle.id,
            node_code=node_code,
            receipt_payload=artifact.payload,
            receipt_hash=artifact.artifact_hash,
            node_signature=artifact.signature,
            signer_fingerprint=artifact.signer_fingerprint,
            accepted_event_id=event.event_id,
            expires_at=expires_at,
        )
        session.add(row)
        await session.flush()
        await self._mark_prepared_if_complete(session, cycle)
        return row

    async def create_proposal(
        self, session: AsyncSession, *, cycle_id: UUID
    ) -> FederatedClearingProposal:
        cycle = await self._cycle(session, cycle_id, lock=True)
        existing = (
            await session.execute(
                select(FederatedClearingProposal).where(
                    FederatedClearingProposal.cycle_id == cycle.id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        if cycle.status != "PREPARED":
            raise federation_error("FEDERATED_CYCLE_NOT_PREPARED", 409)
        result = await self.calculate_preview(session, cycle_id=cycle.id)
        if result.clearing.input_hash != cycle.input_hash:
            raise federation_error("FEDERATED_INPUT_HASH_CHANGED", 409)
        document: dict[str, object] = {
            "cycle_id": str(cycle.id),
            "coordinator_node_code": cycle.coordinator_node_code,
            "policy_hash": (await self._policy(session, cycle.policy_id)).policy_hash,
            **result.payload(),
            "result_hash": result.result_hash,
        }
        signature = self.signer.sign(canonicalize(document))
        event = await self.journal.append(
            session,
            event_type="federation.clearing_proposal_signed",
            aggregate_type="federated_clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=_actor_from_cycle(cycle),
            payload=document,
        )
        row = FederatedClearingProposal(
            id=uuid4(),
            cycle_id=cycle.id,
            proposal_payload=document,
            input_hash=result.clearing.input_hash,
            result_hash=result.result_hash,
            coordinator_signature=signature,
            signer_fingerprint=self.signer.fingerprint,
            created_event_id=event.event_id,
        )
        session.add(row)
        cycle.result_hash = result.result_hash
        cycle.affected_node_codes = list(result.affected_node_codes)
        await _transition(session, cycle, "PROPOSED")
        return row

    async def accept_proposal(
        self,
        session: AsyncSession,
        *,
        cycle_id: UUID,
        artifact: SignedArtifact,
        actor: ActorClaim,
    ) -> FederatedClearingProposal:
        cycle = await self._cycle(session, cycle_id, lock=True)
        existing = (
            await session.execute(
                select(FederatedClearingProposal).where(
                    FederatedClearingProposal.cycle_id == cycle.id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.result_hash != artifact.artifact_hash:
                raise federation_error("FEDERATED_PROPOSAL_CONFLICT", 409)
            return existing
        own_prepare = await self._prepare_receipt(session, cycle.id, self.settings.node_code)
        if own_prepare is None or own_prepare.expires_at <= datetime.now(UTC):
            raise federation_error("FEDERATED_LOCAL_PREPARE_REQUIRED", 409)
        if cycle.status == "PREPARING_NODES":
            await _transition(session, cycle, "PREPARED")
        if cycle.status != "PREPARED":
            raise federation_error("FEDERATED_CYCLE_NOT_PREPARED", 409)
        result = await self.calculate_preview(session, cycle_id=cycle.id)
        if (
            artifact.payload.get("cycle_id") != str(cycle.id)
            or artifact.payload.get("input_hash") != result.clearing.input_hash
            or artifact.payload.get("result_hash") != artifact.artifact_hash
            or artifact.artifact_hash != result.result_hash
        ):
            raise federation_error("FEDERATED_PROPOSAL_VERIFICATION_FAILED", 409)
        event = await self.journal.append(
            session,
            event_type="federation.clearing_proposal_verified",
            aggregate_type="federated_clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload={
                "result_hash": result.result_hash,
                "signer_fingerprint": artifact.signer_fingerprint,
            },
        )
        row = FederatedClearingProposal(
            id=uuid4(),
            cycle_id=cycle.id,
            proposal_payload=artifact.payload,
            input_hash=result.clearing.input_hash,
            result_hash=result.result_hash,
            coordinator_signature=artifact.signature,
            signer_fingerprint=artifact.signer_fingerprint,
            created_event_id=event.event_id,
        )
        session.add(row)
        cycle.input_hash = result.clearing.input_hash
        cycle.result_hash = result.result_hash
        cycle.affected_node_codes = list(result.affected_node_codes)
        await _transition(session, cycle, "PROPOSED")
        return row

    async def approve_local(
        self, session: AsyncSession, *, cycle_id: UUID, actor: ActorClaim
    ) -> NodeClearingApproval:
        cycle = await self._cycle(session, cycle_id, lock=True)
        existing = await self._approval(session, cycle.id, self.settings.node_code)
        if existing is not None:
            return existing
        if cycle.status not in {"PROPOSED", "VERIFYING"}:
            raise federation_error("FEDERATED_CYCLE_NOT_PROPOSED", 409)
        proposal = await self._proposal(session, cycle.id)
        prepare = await self._prepare_receipt(session, cycle.id, self.settings.node_code)
        if prepare is None or prepare.expires_at <= datetime.now(UTC):
            raise federation_error("FEDERATED_PREPARE_EXPIRED", 409)
        result = await self.calculate_preview(session, cycle_id=cycle.id)
        if proposal.result_hash != result.result_hash or cycle.result_hash != result.result_hash:
            raise federation_error("FEDERATED_PROPOSAL_VERIFICATION_FAILED", 409)
        now = datetime.now(UTC).replace(microsecond=0)
        document = approval_payload(
            cycle_id=str(cycle.id),
            node_code=self.settings.node_code,
            input_hash=result.clearing.input_hash,
            result_hash=result.result_hash,
            prepare_receipt_hash=prepare.receipt_hash,
            approved_at=now,
        )
        signature = self.signer.sign(canonicalize(document))
        event = await self.journal.append(
            session,
            event_type="federation.clearing_node_approved",
            aggregate_type="federated_clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload=document,
        )
        row = NodeClearingApproval(
            id=uuid4(),
            cycle_id=cycle.id,
            node_code=self.settings.node_code,
            approval_payload=document,
            approval_hash=_hash_field(document, "approval_hash"),
            node_signature=signature,
            signer_fingerprint=self.signer.fingerprint,
            accepted_event_id=event.event_id,
            approved_at=now,
        )
        session.add(row)
        if cycle.status == "PROPOSED":
            await _transition(session, cycle, "VERIFYING")
        return row

    async def accept_approval(
        self,
        session: AsyncSession,
        *,
        cycle_id: UUID,
        artifact: SignedArtifact,
        actor: ActorClaim,
    ) -> NodeClearingApproval:
        cycle = await self._cycle(session, cycle_id, lock=True)
        node_code = _payload_node(artifact.payload)
        existing = await self._approval(session, cycle.id, node_code)
        if existing is not None:
            if existing.approval_hash != artifact.artifact_hash:
                raise federation_error("FEDERATED_APPROVAL_CONFLICT", 409)
            return existing
        prepare = await self._prepare_receipt(session, cycle.id, node_code)
        if prepare is None or prepare.expires_at <= datetime.now(UTC):
            raise federation_error("FEDERATED_PREPARE_EXPIRED", 409)
        if (
            cycle.status not in {"PROPOSED", "VERIFYING"}
            or artifact.payload.get("cycle_id") != str(cycle.id)
            or artifact.payload.get("input_hash") != cycle.input_hash
            or artifact.payload.get("result_hash") != cycle.result_hash
            or artifact.payload.get("prepare_receipt_hash") != prepare.receipt_hash
            or artifact.payload.get("approval_hash") != artifact.artifact_hash
        ):
            raise federation_error("FEDERATED_APPROVAL_BINDING_INVALID", 422)
        _verify_payload_hash(artifact.payload, "approval_hash")
        approved_at = _payload_datetime(artifact.payload, "approved_at")
        event = await self.journal.append(
            session,
            event_type="federation.clearing_approval_accepted",
            aggregate_type="federated_clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload={
                "node_code": node_code,
                "approval_hash": artifact.artifact_hash,
                "signer_fingerprint": artifact.signer_fingerprint,
            },
        )
        row = NodeClearingApproval(
            id=uuid4(),
            cycle_id=cycle.id,
            node_code=node_code,
            approval_payload=artifact.payload,
            approval_hash=artifact.artifact_hash,
            node_signature=artifact.signature,
            signer_fingerprint=artifact.signer_fingerprint,
            accepted_event_id=event.event_id,
            approved_at=approved_at,
        )
        session.add(row)
        if cycle.status == "PROPOSED":
            await _transition(session, cycle, "VERIFYING")
        return row

    async def certify(self, session: AsyncSession, *, cycle_id: UUID) -> FederatedCommitCertificate:
        cycle = await self._cycle(session, cycle_id, lock=True)
        existing = (
            await session.execute(
                select(FederatedCommitCertificate).where(
                    FederatedCommitCertificate.cycle_id == cycle.id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        if cycle.status != "VERIFYING" or cycle.input_hash is None or cycle.result_hash is None:
            raise federation_error("FEDERATED_CYCLE_NOT_VERIFIED", 409)
        required = tuple(sorted(cycle.affected_node_codes))
        if not required:
            raise federation_error("FEDERATED_RESULT_HAS_NO_AFFECTED_NODES", 409)
        prepares = await self._prepare_map(session, cycle.id)
        approvals = await self._approval_map(session, cycle.id)
        if not set(required).issubset(prepares) or not set(required).issubset(approvals):
            raise federation_error("FEDERATED_CERTIFICATE_APPROVALS_INCOMPLETE", 409)
        if any(prepares[code].expires_at <= datetime.now(UTC) for code in required):
            raise federation_error("FEDERATED_PREPARE_EXPIRED", 409)
        now = datetime.now(UTC).replace(microsecond=0)
        policy = await self._policy(session, cycle.policy_id)
        document = commit_certificate_payload(
            cycle_id=str(cycle.id),
            coordinator_node_code=cycle.coordinator_node_code,
            input_hash=cycle.input_hash,
            result_hash=cycle.result_hash,
            required_node_codes=required,
            prepare_receipt_hashes={code: prepares[code].receipt_hash for code in required},
            approval_hashes={code: approvals[code].approval_hash for code in required},
            policy_hash=policy.policy_hash,
            certified_at=now,
        )
        signature = self.signer.sign(canonicalize(document))
        event = await self.journal.append(
            session,
            event_type="federation.clearing_commit_certified",
            aggregate_type="federated_clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=_actor_from_cycle(cycle),
            payload=document,
            assurance=CommandAssurance(
                exposure=ExposureClaim(
                    category=ExposureCategory.NODE,
                    effect=ExposureEffect.FINALIZE,
                    subject_type="federated_clearing_cycle",
                    subject_id=cycle.id,
                    basis_refs=(
                        cycle.input_hash,
                        cycle.result_hash,
                        str(document["certificate_hash"]),
                    ),
                ),
                evidence_refs=(
                    {
                        "input_hash": cycle.input_hash,
                        "result_hash": cycle.result_hash,
                        "certificate_hash": document["certificate_hash"],
                        "kind": "COMMIT_CERTIFICATE",
                    },
                ),
            ),
        )
        row = FederatedCommitCertificate(
            id=uuid4(),
            cycle_id=cycle.id,
            certificate_payload=document,
            certificate_hash=_hash_field(document, "certificate_hash"),
            coordinator_signature=signature,
            signer_fingerprint=self.signer.fingerprint,
            created_event_id=event.event_id,
            certified_at=now,
        )
        session.add(row)
        cycle.certificate_hash = row.certificate_hash
        cycle.certified_at = now
        await _transition(session, cycle, "COMMIT_CERTIFIED")
        return row

    async def accept_certificate(
        self,
        session: AsyncSession,
        *,
        cycle_id: UUID,
        artifact: SignedArtifact,
        actor: ActorClaim,
    ) -> FederatedCommitCertificate:
        cycle = await self._cycle(session, cycle_id, lock=True)
        existing = (
            await session.execute(
                select(FederatedCommitCertificate).where(
                    FederatedCommitCertificate.cycle_id == cycle.id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.certificate_hash != artifact.artifact_hash:
                raise federation_error("FEDERATED_CERTIFICATE_CONFLICT", 409)
            return existing
        policy = await self._policy(session, cycle.policy_id)
        required = tuple(sorted(cycle.affected_node_codes))
        prepares = await self._prepare_map(session, cycle.id)
        approvals = await self._approval_map(session, cycle.id)
        if not set(required).issubset(prepares) or not set(required).issubset(approvals):
            raise federation_error("FEDERATED_CERTIFICATE_APPROVALS_INCOMPLETE", 409)
        expected = commit_certificate_payload(
            cycle_id=str(cycle.id),
            coordinator_node_code=cycle.coordinator_node_code,
            input_hash=_required_hash(cycle.input_hash),
            result_hash=_required_hash(cycle.result_hash),
            required_node_codes=required,
            prepare_receipt_hashes={code: prepares[code].receipt_hash for code in required},
            approval_hashes={code: approvals[code].approval_hash for code in required},
            policy_hash=policy.policy_hash,
            certified_at=_payload_datetime(artifact.payload, "certified_at"),
        )
        if expected != artifact.payload or artifact.artifact_hash != expected["certificate_hash"]:
            raise federation_error("FEDERATED_CERTIFICATE_VERIFICATION_FAILED", 409)
        event = await self.journal.append(
            session,
            event_type="federation.clearing_certificate_accepted",
            aggregate_type="federated_clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload={
                "certificate_hash": artifact.artifact_hash,
                "signer_fingerprint": artifact.signer_fingerprint,
            },
        )
        row = FederatedCommitCertificate(
            id=uuid4(),
            cycle_id=cycle.id,
            certificate_payload=artifact.payload,
            certificate_hash=artifact.artifact_hash,
            coordinator_signature=artifact.signature,
            signer_fingerprint=artifact.signer_fingerprint,
            created_event_id=event.event_id,
            certified_at=_payload_datetime(artifact.payload, "certified_at"),
        )
        session.add(row)
        cycle.certificate_hash = artifact.artifact_hash
        cycle.certified_at = row.certified_at
        await _transition(session, cycle, "COMMIT_CERTIFIED")
        return row

    async def apply_local(self, session: AsyncSession, *, cycle_id: UUID) -> NodeApplyReceipt:
        cycle = await self._cycle(session, cycle_id, lock=True)
        existing = await self._apply_receipt(session, cycle.id, self.settings.node_code)
        if existing is not None:
            return existing
        if cycle.status not in {"COMMIT_CERTIFIED", "APPLYING", "COMMITTED_PENDING_APPLY"}:
            raise federation_error("FEDERATED_CERTIFICATE_REQUIRED", 409)
        certificate = await self._certificate(session, cycle.id)
        proposal = await self._proposal(session, cycle.id)
        if cycle.certificate_hash != certificate.certificate_hash:
            raise federation_error("FEDERATED_CERTIFICATE_BINDING_INVALID", 409)
        entries = _proposal_entries(proposal.proposal_payload)
        local_entries = {
            item.obligation_id: item
            for item in _snapshot_obligations_for_node(
                (
                    await self._required_snapshot(session, cycle.id, self.settings.node_code)
                ).snapshot_payload
            )
        }
        clearing_by_id = {str(item["obligation_id"]): item for item in entries}
        if not set(local_entries).issubset(clearing_by_id):
            raise federation_error("FEDERATED_PROPOSAL_LOCAL_INPUT_MISSING", 409)
        ids = [UUID(key) for key in local_entries]
        rows = list(
            (
                await session.execute(
                    select(InterNodeObligation)
                    .where(InterNodeObligation.id.in_(ids))
                    .order_by(InterNodeObligation.id)
                    .with_for_update()
                )
            ).scalars()
        )
        if len(rows) != len(ids):
            raise federation_error("FEDERATED_OBLIGATION_MISSING", 409)
        applications: list[dict[str, object]] = []
        now = datetime.now(UTC).replace(microsecond=0)
        if cycle.status == "COMMIT_CERTIFIED":
            await _transition(session, cycle, "APPLYING")
        event = await self.journal.append(
            session,
            event_type="federation.clearing_certificate_applied",
            aggregate_type="federated_clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=_actor_from_cycle(cycle),
            payload={
                "cycle_id": str(cycle.id),
                "certificate_hash": certificate.certificate_hash,
                "local_obligation_ids": [str(row.id) for row in rows],
            },
            assurance=CommandAssurance(
                exposure=ExposureClaim(
                    category=ExposureCategory.NODE,
                    effect=ExposureEffect.EXECUTE,
                    subject_type="federated_clearing_cycle",
                    subject_id=cycle.id,
                    basis_refs=(
                        certificate.certificate_hash,
                        str(proposal.proposal_payload["result_hash"]),
                    ),
                ),
                evidence_refs=(
                    {
                        "certificate_hash": certificate.certificate_hash,
                        "result_hash": proposal.proposal_payload["result_hash"],
                        "kind": "LOCAL_CERTIFICATE_APPLY",
                    },
                ),
            ),
        )
        for row in rows:
            item = clearing_by_id[str(row.id)]
            amount = Decimal(str(item["cleared_amount"]))
            if (
                row.status != "PREPARED"
                or row.prepared_cycle_id != cycle.id
                or row.prepared_input_hash != cycle.input_hash
                or row.prepared_until is None
                or row.prepared_until <= now
                or row.outstanding_amount < amount
            ):
                raise federation_error("FEDERATED_LOCAL_APPLY_CONFLICT", 409)
            if amount == 0:
                row.status = "PARTIALLY_CLEARED" if row.cleared_amount > 0 else "CONFIRMED"
                row.prepared_cycle_id = None
                row.prepared_input_hash = None
                row.prepared_until = None
                row.updated_at = now
                row.version += 1
                continue
            prior = (
                await session.execute(
                    select(FederatedObligationApplication).where(
                        FederatedObligationApplication.obligation_id == row.id,
                        FederatedObligationApplication.certificate_hash
                        == certificate.certificate_hash,
                    )
                )
            ).scalar_one_or_none()
            if prior is not None:
                applications.append(_application_payload(prior))
                continue
            before = row.outstanding_amount
            after = before - amount
            application = FederatedObligationApplication(
                id=uuid4(),
                cycle_id=cycle.id,
                obligation_id=row.id,
                node_code=self.settings.node_code,
                certificate_hash=certificate.certificate_hash,
                amount_before=before,
                cleared_amount=amount,
                amount_after=after,
                applied_event_id=event.event_id,
                applied_at=now,
            )
            session.add(application)
            row.outstanding_amount = after
            row.cleared_amount += amount
            row.status = "CLEARED" if after == 0 else "PARTIALLY_CLEARED"
            row.prepared_cycle_id = None
            row.prepared_input_hash = None
            row.prepared_until = None
            row.updated_at = now
            row.version += 1
            applications.append(_application_payload(application))
        await self._release_exposures(session, cycle, event.event_id)
        document = apply_receipt_payload(
            cycle_id=str(cycle.id),
            node_code=self.settings.node_code,
            certificate_hash=certificate.certificate_hash,
            applications=tuple(applications),
            applied_at=now,
        )
        signature = self.signer.sign(canonicalize(document))
        receipt = NodeApplyReceipt(
            id=uuid4(),
            cycle_id=cycle.id,
            node_code=self.settings.node_code,
            certificate_hash=certificate.certificate_hash,
            receipt_payload=document,
            receipt_hash=_hash_field(document, "receipt_hash"),
            node_signature=signature,
            signer_fingerprint=self.signer.fingerprint,
            applied_count=len(applications),
            applied_amount=sum(
                (Decimal(str(item["cleared_amount"])) for item in applications), Decimal(0)
            ),
            accepted_event_id=event.event_id,
            applied_at=now,
        )
        session.add(receipt)
        await _transition(session, cycle, "COMMITTED_PENDING_APPLY")
        return receipt

    async def release_local_prepare(
        self,
        session: AsyncSession,
        *,
        cycle_id: UUID,
        expired: bool,
    ) -> FederatedClearingCycle:
        cycle = await self._cycle(session, cycle_id, lock=True)
        if cycle.certificate_hash is not None or cycle.status in {
            "COMMIT_CERTIFIED",
            "APPLYING",
            "COMMITTED_PENDING_APPLY",
            "RECONCILED",
        }:
            raise federation_error("FEDERATED_COMMIT_IS_FINAL", 409)
        if cycle.status in {"PREPARE_EXPIRED", "REJECTED", "CANCELLED"}:
            return cycle
        if cycle.status not in {"PREPARING_NODES", "PREPARED", "PROPOSED", "VERIFYING"}:
            raise federation_error("FEDERATED_PREPARE_NOT_RELEASABLE", 409)
        rows = list(
            (
                await session.execute(
                    select(InterNodeObligation)
                    .where(
                        InterNodeObligation.prepared_cycle_id == cycle.id,
                        InterNodeObligation.home_node_code == self.settings.node_code,
                        InterNodeObligation.status == "PREPARED",
                    )
                    .order_by(InterNodeObligation.id)
                    .with_for_update()
                )
            ).scalars()
        )
        now = datetime.now(UTC).replace(microsecond=0)
        event = await self.journal.append(
            session,
            event_type=(
                "federation.clearing_prepare_expired"
                if expired
                else "federation.clearing_prepare_released"
            ),
            aggregate_type="federated_clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=_actor_from_cycle(cycle),
            payload={
                "cycle_id": str(cycle.id),
                "released_obligation_ids": [str(row.id) for row in rows],
                "expired": expired,
            },
        )
        for row in rows:
            row.status = "PARTIALLY_CLEARED" if row.cleared_amount > 0 else "CONFIRMED"
            row.prepared_cycle_id = None
            row.prepared_input_hash = None
            row.prepared_until = None
            row.updated_at = now
            row.version += 1
        await self._release_exposures(session, cycle, event.event_id)
        await _transition(session, cycle, "PREPARE_EXPIRED" if expired else "REJECTED")
        return cycle

    async def accept_apply_receipt(
        self,
        session: AsyncSession,
        *,
        cycle_id: UUID,
        artifact: SignedArtifact,
        actor: ActorClaim,
    ) -> NodeApplyReceipt:
        cycle = await self._cycle(session, cycle_id, lock=True)
        node_code = _payload_node(artifact.payload)
        existing = await self._apply_receipt(session, cycle.id, node_code)
        if existing is not None:
            if existing.receipt_hash != artifact.artifact_hash:
                raise federation_error("FEDERATED_APPLY_RECEIPT_CONFLICT", 409)
            return existing
        certificate = await self._certificate(session, cycle.id)
        if (
            artifact.payload.get("cycle_id") != str(cycle.id)
            or artifact.payload.get("certificate_hash") != certificate.certificate_hash
            or artifact.payload.get("receipt_hash") != artifact.artifact_hash
        ):
            raise federation_error("FEDERATED_APPLY_RECEIPT_BINDING_INVALID", 422)
        _verify_payload_hash(artifact.payload, "receipt_hash")
        count = _payload_int(artifact.payload, "applied_count")
        amount = _payload_decimal(artifact.payload, "applied_amount")
        event = await self.journal.append(
            session,
            event_type="federation.clearing_apply_receipt_accepted",
            aggregate_type="federated_clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=actor,
            payload={"node_code": node_code, "receipt_hash": artifact.artifact_hash},
        )
        row = NodeApplyReceipt(
            id=uuid4(),
            cycle_id=cycle.id,
            node_code=node_code,
            certificate_hash=certificate.certificate_hash,
            receipt_payload=artifact.payload,
            receipt_hash=artifact.artifact_hash,
            node_signature=artifact.signature,
            signer_fingerprint=artifact.signer_fingerprint,
            applied_count=count,
            applied_amount=amount,
            accepted_event_id=event.event_id,
            applied_at=_payload_datetime(artifact.payload, "applied_at"),
        )
        session.add(row)
        return row

    async def reconcile(self, session: AsyncSession, *, cycle_id: UUID) -> FederatedClearingProof:
        cycle = await self._cycle(session, cycle_id, lock=True)
        existing = (
            await session.execute(
                select(FederatedClearingProof).where(FederatedClearingProof.cycle_id == cycle.id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        if cycle.status not in {"COMMIT_CERTIFIED", "APPLYING", "COMMITTED_PENDING_APPLY"}:
            raise federation_error("FEDERATED_CYCLE_NOT_COMMITTED", 409)
        required = tuple(sorted(cycle.affected_node_codes))
        snapshots = {row.node_code: row for row in await self._all_snapshots(session, cycle)}
        prepares = await self._prepare_map(session, cycle.id)
        approvals = await self._approval_map(session, cycle.id)
        applies = await self._apply_map(session, cycle.id)
        required_set = set(required)
        if any(
            not required_set.issubset(mapping)
            for mapping in (snapshots, prepares, approvals, applies)
        ):
            raise federation_error("FEDERATED_RECONCILIATION_INCOMPLETE", 409)
        now = datetime.now(UTC).replace(microsecond=0)
        document = reconciliation_proof_payload(
            cycle_id=str(cycle.id),
            input_hash=_required_hash(cycle.input_hash),
            result_hash=_required_hash(cycle.result_hash),
            certificate_hash=_required_hash(cycle.certificate_hash),
            required_node_codes=required,
            snapshot_hashes={code: snapshots[code].snapshot_hash for code in required},
            prepare_receipt_hashes={code: prepares[code].receipt_hash for code in required},
            approval_hashes={code: approvals[code].approval_hash for code in required},
            apply_receipt_hashes={code: applies[code].receipt_hash for code in required},
            reconciled_at=now,
        )
        event = await self.journal.append(
            session,
            event_type="federation.clearing_reconciled",
            aggregate_type="federated_clearing_cycle",
            aggregate_id=cycle.id,
            aggregate_version=cycle.version + 1,
            actor=_actor_from_cycle(cycle),
            payload=document,
            assurance=CommandAssurance(
                exposure=ExposureClaim(
                    category=ExposureCategory.NODE,
                    effect=ExposureEffect.FINALIZE,
                    subject_type="federated_clearing_cycle",
                    subject_id=cycle.id,
                    basis_refs=(
                        str(document["certificate_hash"]),
                        str(document["proof_hash"]),
                    ),
                ),
                evidence_refs=(
                    {
                        "certificate_hash": document["certificate_hash"],
                        "proof_hash": document["proof_hash"],
                        "kind": "RECONCILIATION_PROOF",
                    },
                ),
            ),
        )
        row = FederatedClearingProof(
            id=uuid4(),
            cycle_id=cycle.id,
            proof_payload=document,
            proof_hash=_hash_field(document, "proof_hash"),
            created_event_id=event.event_id,
        )
        session.add(row)
        cycle.reconciled_at = now
        await _transition(session, cycle, "RECONCILED")
        return row

    async def _cycle(
        self, session: AsyncSession, cycle_id: UUID, *, lock: bool = False
    ) -> FederatedClearingCycle:
        query = select(FederatedClearingCycle).where(FederatedClearingCycle.id == cycle_id)
        if lock:
            query = query.with_for_update()
        row = (await session.execute(query)).scalar_one_or_none()
        if row is None:
            raise federation_error("FEDERATED_CYCLE_NOT_FOUND", 404)
        return row

    async def _policy(
        self, session: AsyncSession, policy_id: UUID
    ) -> FederatedClearingPolicyRecord:
        row = await session.get(FederatedClearingPolicyRecord, policy_id)
        if row is None:
            raise federation_error("FEDERATED_POLICY_NOT_FOUND", 404)
        return row

    async def _snapshot(
        self, session: AsyncSession, cycle_id: UUID, node_code: str
    ) -> FederatedInputSnapshot | None:
        return (
            await session.execute(
                select(FederatedInputSnapshot).where(
                    FederatedInputSnapshot.cycle_id == cycle_id,
                    FederatedInputSnapshot.node_code == node_code,
                )
            )
        ).scalar_one_or_none()

    async def _required_snapshot(
        self, session: AsyncSession, cycle_id: UUID, node_code: str
    ) -> FederatedInputSnapshot:
        row = await self._snapshot(session, cycle_id, node_code)
        if row is None:
            raise federation_error("FEDERATED_SNAPSHOT_NOT_FOUND", 404)
        return row

    async def _all_snapshots(
        self, session: AsyncSession, cycle: FederatedClearingCycle
    ) -> list[FederatedInputSnapshot]:
        rows = list(
            (
                await session.execute(
                    select(FederatedInputSnapshot)
                    .where(FederatedInputSnapshot.cycle_id == cycle.id)
                    .order_by(FederatedInputSnapshot.node_code)
                )
            ).scalars()
        )
        if {row.node_code for row in rows} != set(cycle.participant_node_codes):
            raise federation_error("FEDERATED_SNAPSHOTS_INCOMPLETE", 409)
        if any(row.expires_at <= datetime.now(UTC) for row in rows):
            raise federation_error("FEDERATED_SNAPSHOT_EXPIRED", 409)
        return rows

    async def _prepare_receipt(
        self, session: AsyncSession, cycle_id: UUID, node_code: str
    ) -> NodePrepareReceipt | None:
        return (
            await session.execute(
                select(NodePrepareReceipt).where(
                    NodePrepareReceipt.cycle_id == cycle_id,
                    NodePrepareReceipt.node_code == node_code,
                )
            )
        ).scalar_one_or_none()

    async def _proposal(self, session: AsyncSession, cycle_id: UUID) -> FederatedClearingProposal:
        row = (
            await session.execute(
                select(FederatedClearingProposal).where(
                    FederatedClearingProposal.cycle_id == cycle_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise federation_error("FEDERATED_PROPOSAL_NOT_FOUND", 404)
        return row

    async def _approval(
        self, session: AsyncSession, cycle_id: UUID, node_code: str
    ) -> NodeClearingApproval | None:
        return (
            await session.execute(
                select(NodeClearingApproval).where(
                    NodeClearingApproval.cycle_id == cycle_id,
                    NodeClearingApproval.node_code == node_code,
                )
            )
        ).scalar_one_or_none()

    async def _certificate(
        self, session: AsyncSession, cycle_id: UUID
    ) -> FederatedCommitCertificate:
        row = (
            await session.execute(
                select(FederatedCommitCertificate).where(
                    FederatedCommitCertificate.cycle_id == cycle_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise federation_error("FEDERATED_CERTIFICATE_NOT_FOUND", 404)
        return row

    async def _apply_receipt(
        self, session: AsyncSession, cycle_id: UUID, node_code: str
    ) -> NodeApplyReceipt | None:
        return (
            await session.execute(
                select(NodeApplyReceipt).where(
                    NodeApplyReceipt.cycle_id == cycle_id,
                    NodeApplyReceipt.node_code == node_code,
                )
            )
        ).scalar_one_or_none()

    async def _prepare_map(
        self, session: AsyncSession, cycle_id: UUID
    ) -> dict[str, NodePrepareReceipt]:
        rows = list(
            (
                await session.execute(
                    select(NodePrepareReceipt).where(NodePrepareReceipt.cycle_id == cycle_id)
                )
            ).scalars()
        )
        return {row.node_code: row for row in rows}

    async def _approval_map(
        self, session: AsyncSession, cycle_id: UUID
    ) -> dict[str, NodeClearingApproval]:
        rows = list(
            (
                await session.execute(
                    select(NodeClearingApproval).where(NodeClearingApproval.cycle_id == cycle_id)
                )
            ).scalars()
        )
        return {row.node_code: row for row in rows}

    async def _apply_map(
        self, session: AsyncSession, cycle_id: UUID
    ) -> dict[str, NodeApplyReceipt]:
        rows = list(
            (
                await session.execute(
                    select(NodeApplyReceipt).where(NodeApplyReceipt.cycle_id == cycle_id)
                )
            ).scalars()
        )
        return {row.node_code: row for row in rows}

    async def _mark_prepared_if_complete(
        self, session: AsyncSession, cycle: FederatedClearingCycle
    ) -> None:
        count = int(
            (
                await session.execute(
                    select(func.count(NodePrepareReceipt.id)).where(
                        NodePrepareReceipt.cycle_id == cycle.id,
                        NodePrepareReceipt.node_code.in_(cycle.participant_node_codes),
                    )
                )
            ).scalar_one()
        )
        if count == len(cycle.participant_node_codes) and cycle.status == "PREPARING_NODES":
            cycle.prepared_at = datetime.now(UTC)
            await _transition(session, cycle, "PREPARED")

    async def _checkpoint_hash(self, session: AsyncSession) -> str:
        profile = (
            await session.execute(
                select(NodeProfile).where(NodeProfile.node_code == self.settings.node_code)
            )
        ).scalar_one_or_none()
        if profile is None:
            raise federation_error("NODE_PROFILE_NOT_INITIALIZED", 503)
        chain = await session.get(NodeChainState, profile.id)
        if chain is None or chain.last_event_hash is None:
            raise federation_error("NODE_CHECKPOINT_UNAVAILABLE", 503)
        return chain.last_event_hash

    async def _lock_exposures(
        self, session: AsyncSession, reservations: dict[tuple[str, str], Decimal]
    ) -> list[tuple[NodeExposure, Decimal]]:
        result: list[tuple[NodeExposure, Decimal]] = []
        for (node_code, unit), delta in sorted(reservations.items()):
            node = (
                await session.execute(
                    select(ExternalNode).where(
                        ExternalNode.node_code == node_code, ExternalNode.status == "ACTIVE"
                    )
                )
            ).scalar_one_or_none()
            if node is None:
                raise federation_error("FEDERATED_COUNTERPARTY_NOT_ACTIVE", 409)
            limit = (
                await session.execute(
                    select(NodeBilateralLimit).where(
                        NodeBilateralLimit.node_id == node.id,
                        NodeBilateralLimit.capability == "CLEARING",
                        NodeBilateralLimit.unit == unit,
                        NodeBilateralLimit.status == "ACTIVE",
                    )
                )
            ).scalar_one_or_none()
            if limit is None:
                raise federation_error("FEDERATED_CLEARING_LIMIT_MISSING", 409)
            exposure = (
                await session.execute(
                    select(NodeExposure)
                    .where(
                        NodeExposure.node_id == node.id,
                        NodeExposure.capability == "CLEARING",
                        NodeExposure.unit == unit,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if exposure is None:
                raise federation_error("FEDERATED_CLEARING_EXPOSURE_MISSING", 409)
            if (
                exposure.current_amount + exposure.reserved_amount + delta
                > limit.max_clearing_position
            ):
                raise federation_error("FEDERATED_CLEARING_LIMIT_EXCEEDED", 409)
            result.append((exposure, delta))
        return result

    async def _release_exposures(
        self, session: AsyncSession, cycle: FederatedClearingCycle, event_id: UUID
    ) -> None:
        snapshot = await self._required_snapshot(session, cycle.id, self.settings.node_code)
        reservations: dict[tuple[str, str], Decimal] = {}
        for item in _snapshot_obligations(snapshot.snapshot_payload):
            peer = (
                item.creditor_node_code
                if item.debtor_node_code == self.settings.node_code
                else item.debtor_node_code
            )
            key = (peer, item.unit_code)
            reservations[key] = reservations.get(key, Decimal(0)) + item.amount
        for (node_code, unit), delta in sorted(reservations.items()):
            node = (
                await session.execute(
                    select(ExternalNode).where(ExternalNode.node_code == node_code)
                )
            ).scalar_one()
            exposure = (
                await session.execute(
                    select(NodeExposure)
                    .where(
                        NodeExposure.node_id == node.id,
                        NodeExposure.capability == "CLEARING",
                        NodeExposure.unit == unit,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            if exposure.reserved_amount < delta:
                raise federation_error("FEDERATED_CLEARING_EXPOSURE_INCONSISTENT", 500)
            exposure.reserved_amount -= delta
            exposure.updated_event_id = event_id
            exposure.updated_at = datetime.now(UTC)
            exposure.version += 1


@dataclass(frozen=True, slots=True)
class FederatedPrepareExpiryResult:
    released_cycles: int


async def expire_stale_federated_prepares(
    session: AsyncSession,
    *,
    settings: Settings,
    batch_size: int,
) -> FederatedPrepareExpiryResult:
    now = datetime.now(UTC)
    cycle_ids = list(
        (
            await session.execute(
                select(FederatedClearingCycle.id)
                .join(
                    NodePrepareReceipt,
                    NodePrepareReceipt.cycle_id == FederatedClearingCycle.id,
                )
                .where(
                    NodePrepareReceipt.node_code == settings.node_code,
                    NodePrepareReceipt.expires_at <= now,
                    FederatedClearingCycle.certificate_hash.is_(None),
                    FederatedClearingCycle.status.in_(
                        ["PREPARING_NODES", "PREPARED", "PROPOSED", "VERIFYING"]
                    ),
                )
                .order_by(NodePrepareReceipt.expires_at, FederatedClearingCycle.id)
                .limit(batch_size)
                .with_for_update(of=FederatedClearingCycle, skip_locked=True)
            )
        ).scalars()
    )
    service = InterNodeClearingService(settings)
    released = 0
    for cycle_id in cycle_ids:
        await service.release_local_prepare(session, cycle_id=cycle_id, expired=True)
        released += 1
    return FederatedPrepareExpiryResult(released_cycles=released)


async def _transition(session: AsyncSession, cycle: FederatedClearingCycle, status: str) -> None:
    cycle.status = status
    cycle.updated_at = datetime.now(UTC)
    cycle.version += 1
    await session.flush()


def _domain_policy(row: FederatedClearingPolicyRecord) -> FederatedClearingPolicy:
    return FederatedClearingPolicy(
        policy_version=row.policy_version,
        decimal_scale=row.decimal_scale,
        rounding_mode=RoundingMode(row.rounding_mode),
        minimum_operation=row.minimum_operation,
        max_iterations=row.max_iterations,
        max_cycle_length=row.max_cycle_length,
        prepare_ttl_seconds=row.prepare_ttl_seconds,
    )


def _domain_obligation(row: InterNodeObligation) -> FederatedObligationInput:
    return FederatedObligationInput(
        obligation_id=str(row.id),
        home_node_code=row.home_node_code,
        debtor_node_code=row.debtor_node_code,
        creditor_node_code=row.creditor_node_code,
        unit_code=row.unit_code,
        amount=row.outstanding_amount,
        version=row.version,
        liquidity_class=row.liquidity_class,
        source_event_hash=row.source_event_hash,
    )


def _snapshot_obligations(payload: dict[str, object]) -> tuple[FederatedObligationInput, ...]:
    raw = payload.get("obligations")
    if not isinstance(raw, list):
        raise federation_error("FEDERATED_SNAPSHOT_INVALID", 422)
    result: list[FederatedObligationInput] = []
    for value in raw:
        if not isinstance(value, dict):
            raise federation_error("FEDERATED_SNAPSHOT_INVALID", 422)
        item = cast(dict[str, object], value)
        result.append(
            FederatedObligationInput(
                obligation_id=_payload_text(item, "obligation_id", 64),
                home_node_code=_payload_text(item, "home_node_code", 63),
                debtor_node_code=_payload_text(item, "debtor_node_code", 63),
                creditor_node_code=_payload_text(item, "creditor_node_code", 63),
                unit_code=_payload_text(item, "unit_code", 32),
                amount=_payload_decimal(item, "amount"),
                version=_payload_int(item, "version"),
                liquidity_class=_payload_text(item, "liquidity_class", 32),
                eligible=bool(item.get("eligible", True)),
                exclusion_reason=(
                    str(item["exclusion_reason"])
                    if item.get("exclusion_reason") is not None
                    else None
                ),
                disputed=bool(item.get("disputed", False)),
                frozen=bool(item.get("frozen", False)),
                risk_limit=(
                    Decimal(str(item["risk_limit"])) if item.get("risk_limit") is not None else None
                ),
                source_event_hash=(
                    str(item["source_event_hash"])
                    if item.get("source_event_hash") is not None
                    else None
                ),
            ).validate()
        )
    return tuple(result)


def _snapshot_obligations_for_node(
    payload: dict[str, object],
) -> tuple[FederatedObligationInput, ...]:
    return _snapshot_obligations(payload)


def _proposal_entries(payload: dict[str, object]) -> tuple[dict[str, object], ...]:
    raw = payload.get("entries")
    if not isinstance(raw, list):
        raise federation_error("FEDERATED_PROPOSAL_INVALID", 422)
    entries: list[dict[str, object]] = []
    for value in raw:
        if not isinstance(value, dict):
            raise federation_error("FEDERATED_PROPOSAL_INVALID", 422)
        entries.append(cast(dict[str, object], value))
    return tuple(entries)


def _application_payload(row: FederatedObligationApplication) -> dict[str, object]:
    return {
        "obligation_id": str(row.obligation_id),
        "amount_before": str(row.amount_before),
        "cleared_amount": str(row.cleared_amount),
        "amount_after": str(row.amount_after),
    }


def _actor_from_cycle(cycle: FederatedClearingCycle) -> ActorClaim:
    return ActorClaim(
        person_id=cycle.created_by_member_id,
        organization_id=cycle.created_actor_organization_id,
        role_assignment_id=cycle.created_role_assignment_id,
    )


def _bounded_code(value: str, maximum: int) -> str:
    cleaned = value.strip().upper()
    if (
        not cleaned
        or len(cleaned) > maximum
        or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in cleaned)
    ):
        raise federation_error("FEDERATED_CODE_INVALID", 422)
    return cleaned


def _payload_node(payload: dict[str, object]) -> str:
    return str(NodeCode(_payload_text(payload, "node_code", 63)))


def _payload_text(payload: dict[str, object], key: str, maximum: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422)
    return value


def _payload_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422)
    return value


def _payload_decimal(payload: dict[str, object], key: str) -> Decimal:
    value = payload.get(key)
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422)
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422) from exc
    if parsed < 0:
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422)
    return parsed


def _payload_datetime(payload: dict[str, object], key: str) -> datetime:
    value = payload.get(key)
    if not isinstance(value, str):
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422) from exc
    if parsed.tzinfo is None:
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422)
    return parsed.astimezone(UTC)


def _hash_field(payload: dict[str, object], key: str) -> str:
    value = _payload_text(payload, key, 71)
    if len(value) != 71 or not value.startswith("sha256:"):
        raise federation_error("FEDERATED_ARTIFACT_HASH_INVALID", 422)
    return value


def _required_hash(value: str | None) -> str:
    if value is None:
        raise federation_error("FEDERATED_ARTIFACT_HASH_MISSING", 409)
    return value


def _verify_payload_hash(payload: dict[str, object], field: str) -> None:
    claimed = _hash_field(payload, field)
    body = {key: value for key, value in payload.items() if key != field}
    if payload_hash(body) != claimed:
        raise federation_error("FEDERATED_ARTIFACT_HASH_MISMATCH", 422)
