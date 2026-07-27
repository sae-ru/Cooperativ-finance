"""Bounded compensation funded by a final share-exposure decision."""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import Member, Membership
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.inventory.domain.types import decimal_text
from cooperative_clearing.modules.inventory.infrastructure.models import EvidenceBlob, EvidenceLink
from cooperative_clearing.modules.journal.application.service import SignedJournalService
from cooperative_clearing.modules.risk.application.common import (
    RiskCommandResult,
    begin_risk_command,
    complete_risk_command,
    risk_owner_actor,
    risk_role_actor,
)
from cooperative_clearing.modules.risk.domain.types import (
    AccountStatus,
    CommitmentStatus,
    CompensationStatus,
    LiabilityStatus,
    ShareContour,
    exact_amount,
    risk_error,
)
from cooperative_clearing.modules.risk.infrastructure.models import (
    CompensationTransfer,
    ExposureCommitment,
    LiabilityCase,
    ShareAccount,
)
from cooperative_clearing.modules.trust.infrastructure.models import (
    Appeal,
    ArbitrationDecision,
    TrustCase,
)
from cooperative_clearing.shared.core.config import Settings

COMPENSATION_OPERATOR_ROLES = {RoleCode.RISK_ADMIN, RoleCode.AUDITOR}


class CompensationService:
    def __init__(self, settings: Settings) -> None:
        self.journal = SignedJournalService(settings)

    async def authorize(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        liability_case_id: UUID,
        trust_case_id: UUID,
        trust_decision_id: UUID,
        destination_account_id: UUID,
        amount: Decimal,
        rationale: str,
        evidence_ids: Sequence[UUID],
        expected_liability_version: int,
        expected_source_account_version: int,
        expected_destination_account_version: int,
        expected_commitment_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> RiskCommandResult:
        liability = await session.get(LiabilityCase, liability_case_id, with_for_update=True)
        if liability is None:
            raise risk_error("LIABILITY_CASE_NOT_FOUND", 404)
        payload = {
            "liability_case_id": str(liability_case_id),
            "trust_case_id": str(trust_case_id),
            "trust_decision_id": str(trust_decision_id),
            "destination_account_id": str(destination_account_id),
            "amount": decimal_text(exact_amount(amount)),
            "rationale": self._text(rationale, "COMPENSATION_RATIONALE_INVALID", 8000),
            "evidence_ids": [str(value) for value in evidence_ids],
            "expected_liability_version": expected_liability_version,
            "expected_source_account_version": expected_source_account_version,
            "expected_destination_account_version": expected_destination_account_version,
            "expected_commitment_version": expected_commitment_version,
        }
        record, replay = await begin_risk_command(
            session, principal, "RISK_COMPENSATION_AUTHORIZE", idempotency_key, payload
        )
        if replay is not None:
            return replay

        self._version(liability.version, expected_liability_version)
        if liability.status != LiabilityStatus.ASSESSED.value or liability.assessed_loss is None:
            raise risk_error("COMPENSATION_LIABILITY_NOT_ASSESSED", 409)
        actor = risk_role_actor(
            principal, liability.cooperative_id, COMPENSATION_OPERATOR_ROLES
        )
        await self._eligible_member(session, liability.cooperative_id, actor.person_id)

        commitment = await session.get(
            ExposureCommitment, liability.commitment_id, with_for_update=True
        )
        if commitment is None:
            raise risk_error("COMMITMENT_NOT_FOUND", 500)
        self._version(commitment.version, expected_commitment_version)
        if commitment.status not in {
            CommitmentStatus.ACTIVE.value,
            CommitmentStatus.RELEASED.value,
        }:
            raise risk_error("COMPENSATION_COMMITMENT_NOT_EXECUTABLE", 409)

        accounts = await self._locked_accounts(
            session, commitment.account_id, destination_account_id
        )
        source = accounts.get(commitment.account_id)
        destination = accounts.get(destination_account_id)
        if source is None or destination is None:
            raise risk_error("COMPENSATION_ACCOUNT_NOT_FOUND", 404)
        self._version(source.version, expected_source_account_version)
        self._version(destination.version, expected_destination_account_version)
        self._validate_accounts(liability, source, destination)

        trust_case = await session.get(TrustCase, trust_case_id)
        decision = await session.get(ArbitrationDecision, trust_decision_id)
        if trust_case is None or decision is None:
            raise risk_error("COMPENSATION_DECISION_NOT_FOUND", 404)
        final_loss = await self._final_loss(session, liability, trust_case, decision)
        recipient_member_id = trust_case.claimant_member_id
        if destination.member_id != recipient_member_id:
            raise risk_error("COMPENSATION_RECIPIENT_ACCOUNT_MISMATCH", 409)
        independent_people = {
            liability.responsible_member_id,
            recipient_member_id,
            liability.opened_by_member_id,
            liability.assessed_by_member_id,
            decision.issued_by_member_id,
        }
        if actor.person_id in independent_people:
            raise risk_error("COMPENSATION_AUTHORIZER_NOT_INDEPENDENT", 403)
        await self._eligible_member(session, liability.cooperative_id, recipient_member_id)

        compensation_amount = exact_amount(amount)
        remaining_execution = min(
            commitment.max_loss - commitment.executed_amount,
            commitment.amount_reserved - commitment.executed_amount,
        )
        if (
            compensation_amount > liability.assessed_loss
            or compensation_amount > final_loss
            or compensation_amount > remaining_execution
        ):
            raise risk_error("COMPENSATION_AMOUNT_EXCEEDS_BOUND", 409)

        await self._lock_cooperative(session, liability.cooperative_id)
        existing = await session.scalar(
            select(CompensationTransfer.id).where(
                CompensationTransfer.liability_case_id == liability.id,
                CompensationTransfer.status.in_(
                    {
                        CompensationStatus.PENDING_ACCEPTANCE.value,
                        CompensationStatus.SETTLED.value,
                    }
                ),
            )
        )
        if existing is not None:
            raise risk_error("COMPENSATION_ALREADY_ACTIVE", 409)
        await self._ensure_account_capacity(
            session, source, commitment, compensation_amount
        )
        evidence = await EvidenceService.require_ready(
            session, liability.cooperative_id, evidence_ids, required=True
        )
        refs = self._evidence_payload(evidence)
        transfer_id = uuid4()
        source_version_before = source.version
        destination_version = destination.version
        commitment_version_before = commitment.version
        event = await self.journal.append(
            session,
            event_type="liability.compensation_authorized",
            aggregate_type="compensation_transfer",
            aggregate_id=transfer_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "transfer_id": str(transfer_id),
                "commitment_id": str(commitment.id),
                "source_account_id": str(source.id),
                "responsible_member_id": str(liability.responsible_member_id),
                "recipient_member_id": str(recipient_member_id),
                "denomination": source.denomination,
                "final_decision_loss": decimal_text(final_loss),
                "assessed_loss": decimal_text(liability.assessed_loss),
                "protected_amount": decimal_text(source.protected_amount),
                "status": CompensationStatus.PENDING_ACCEPTANCE.value,
                "evidence": refs,
            },
        )
        source.executed_not_settled += compensation_amount
        source.last_event_id = event.event_id
        source.updated_at = datetime.now(UTC)
        source.version += 1
        commitment.executed_amount += compensation_amount
        commitment.version += 1
        session.add(
            CompensationTransfer(
                id=transfer_id,
                cooperative_id=liability.cooperative_id,
                liability_case_id=liability.id,
                trust_case_id=trust_case.id,
                trust_decision_id=decision.id,
                commitment_id=commitment.id,
                source_account_id=source.id,
                destination_account_id=destination.id,
                responsible_member_id=liability.responsible_member_id,
                recipient_member_id=recipient_member_id,
                amount=compensation_amount,
                denomination=source.denomination,
                rationale=str(payload["rationale"]),
                status=CompensationStatus.PENDING_ACCEPTANCE.value,
                authorization_evidence_refs=refs,
                authorized_by_user_id=principal.user_id,
                authorized_by_member_id=actor.person_id,
                authorized_role_assignment_id=actor.role_assignment_id,
                authorized_event_id=event.event_id,
                source_account_version_before=source_version_before,
                destination_account_version_at_authorization=destination_version,
                commitment_version_before=commitment_version_before,
                version=1,
            )
        )
        self._link_evidence(session, evidence, event.event_id, "compensation_transfer", transfer_id)
        await self._audit(
            session,
            principal,
            liability.cooperative_id,
            "LIABILITY_COMPENSATION_AUTHORIZED",
            transfer_id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, transfer_id)

    async def accept(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        transfer_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> RiskCommandResult:
        transfer = await session.get(CompensationTransfer, transfer_id, with_for_update=True)
        if transfer is None:
            raise risk_error("COMPENSATION_NOT_FOUND", 404)
        payload = {"transfer_id": str(transfer_id), "expected_version": expected_version}
        record, replay = await begin_risk_command(
            session, principal, "RISK_COMPENSATION_ACCEPT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(transfer.version, expected_version)
        if transfer.status != CompensationStatus.PENDING_ACCEPTANCE.value:
            raise risk_error("COMPENSATION_NOT_PENDING", 409)
        actor = risk_owner_actor(
            principal, transfer.cooperative_id, transfer.recipient_member_id
        )
        await self._eligible_member(session, transfer.cooperative_id, actor.person_id)
        accounts = await self._locked_accounts(
            session, transfer.source_account_id, transfer.destination_account_id
        )
        source = accounts.get(transfer.source_account_id)
        destination = accounts.get(transfer.destination_account_id)
        if source is None or destination is None:
            raise risk_error("COMPENSATION_ACCOUNT_NOT_FOUND", 404)
        if source.executed_not_settled < transfer.amount or source.balance < transfer.amount:
            raise risk_error("COMPENSATION_RESERVATION_BROKEN", 409)
        source_before = source.balance
        destination_before = destination.balance
        source_after = exact_amount(source_before - transfer.amount, allow_zero=True)
        destination_after = exact_amount(destination_before + transfer.amount, allow_zero=True)
        event = await self.journal.append(
            session,
            event_type="liability.compensation_settled",
            aggregate_type="compensation_transfer",
            aggregate_id=transfer.id,
            aggregate_version=transfer.version + 1,
            actor=actor,
            payload={
                **payload,
                "liability_case_id": str(transfer.liability_case_id),
                "source_account_id": str(source.id),
                "destination_account_id": str(destination.id),
                "amount": decimal_text(transfer.amount),
                "denomination": transfer.denomination,
                "source_balance_before": decimal_text(source_before),
                "source_balance_after": decimal_text(source_after),
                "destination_balance_before": decimal_text(destination_before),
                "destination_balance_after": decimal_text(destination_after),
            },
        )
        now = datetime.now(UTC)
        source.balance = source_after
        source.executed_not_settled -= transfer.amount
        source.last_event_id = event.event_id
        source.updated_at = now
        source.version += 1
        destination.balance = destination_after
        destination.last_event_id = event.event_id
        destination.updated_at = now
        destination.version += 1
        transfer.status = CompensationStatus.SETTLED.value
        transfer.accepted_by_user_id = principal.user_id
        transfer.accepted_by_member_id = actor.person_id
        transfer.accepted_role_assignment_id = actor.role_assignment_id
        transfer.accepted_event_id = event.event_id
        transfer.source_balance_before = source_before
        transfer.source_balance_after = source_after
        transfer.destination_balance_before = destination_before
        transfer.destination_balance_after = destination_after
        transfer.accepted_at = now
        transfer.updated_at = now
        transfer.version += 1
        liability = await session.get(
            LiabilityCase, transfer.liability_case_id, with_for_update=True
        )
        if liability is None:
            raise risk_error("LIABILITY_CASE_NOT_FOUND", 500)
        liability.status = LiabilityStatus.CLOSED.value
        liability.version += 1
        await self._audit(
            session,
            principal,
            transfer.cooperative_id,
            "LIABILITY_COMPENSATION_SETTLED",
            transfer.id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, transfer.id)

    async def void(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        transfer_id: UUID,
        reason: str,
        evidence_ids: Sequence[UUID],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> RiskCommandResult:
        transfer = await session.get(CompensationTransfer, transfer_id, with_for_update=True)
        if transfer is None:
            raise risk_error("COMPENSATION_NOT_FOUND", 404)
        payload = {
            "transfer_id": str(transfer_id),
            "reason": self._text(reason, "COMPENSATION_VOID_REASON_INVALID", 4000),
            "evidence_ids": [str(value) for value in evidence_ids],
            "expected_version": expected_version,
        }
        record, replay = await begin_risk_command(
            session, principal, "RISK_COMPENSATION_VOID", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(transfer.version, expected_version)
        if transfer.status != CompensationStatus.PENDING_ACCEPTANCE.value:
            raise risk_error("COMPENSATION_NOT_PENDING", 409)
        actor = risk_role_actor(
            principal, transfer.cooperative_id, COMPENSATION_OPERATOR_ROLES
        )
        if actor.person_id in {
            transfer.authorized_by_member_id,
            transfer.responsible_member_id,
            transfer.recipient_member_id,
        }:
            raise risk_error("COMPENSATION_VOIDER_NOT_INDEPENDENT", 403)
        source = await session.get(
            ShareAccount, transfer.source_account_id, with_for_update=True
        )
        commitment = await session.get(
            ExposureCommitment, transfer.commitment_id, with_for_update=True
        )
        if source is None or commitment is None:
            raise risk_error("COMPENSATION_ACCOUNT_NOT_FOUND", 404)
        if (
            source.executed_not_settled < transfer.amount
            or commitment.executed_amount < transfer.amount
        ):
            raise risk_error("COMPENSATION_RESERVATION_BROKEN", 409)
        evidence = await EvidenceService.require_ready(
            session, transfer.cooperative_id, evidence_ids, required=True
        )
        refs = self._evidence_payload(evidence)
        event = await self.journal.append(
            session,
            event_type="liability.compensation_voided",
            aggregate_type="compensation_transfer",
            aggregate_id=transfer.id,
            aggregate_version=transfer.version + 1,
            actor=actor,
            payload={
                **payload,
                "amount": decimal_text(transfer.amount),
                "source_account_id": str(source.id),
                "commitment_id": str(commitment.id),
                "evidence": refs,
            },
        )
        now = datetime.now(UTC)
        source.executed_not_settled -= transfer.amount
        source.last_event_id = event.event_id
        source.updated_at = now
        source.version += 1
        commitment.executed_amount -= transfer.amount
        commitment.version += 1
        transfer.status = CompensationStatus.VOIDED.value
        transfer.voided_by_user_id = principal.user_id
        transfer.voided_by_member_id = actor.person_id
        transfer.voided_role_assignment_id = actor.role_assignment_id
        transfer.voided_event_id = event.event_id
        transfer.void_reason = str(payload["reason"])
        transfer.void_evidence_refs = refs
        transfer.voided_at = now
        transfer.updated_at = now
        transfer.version += 1
        self._link_evidence(session, evidence, event.event_id, "compensation_transfer", transfer.id)
        await self._audit(
            session,
            principal,
            transfer.cooperative_id,
            "LIABILITY_COMPENSATION_VOIDED",
            transfer.id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, transfer.id)

    @staticmethod
    async def _final_loss(
        session: AsyncSession,
        liability: LiabilityCase,
        trust_case: TrustCase,
        decision: ArbitrationDecision,
    ) -> Decimal:
        if (
            trust_case.id != decision.case_id
            or trust_case.cooperative_id != liability.cooperative_id
            or trust_case.source_type != "LIABILITY"
            or trust_case.source_reference != str(liability.id)
            or trust_case.subject_member_id != liability.responsible_member_id
            or liability.assessed_event_id is None
            or str(liability.assessed_event_id) not in trust_case.source_event_ids
        ):
            raise risk_error("COMPENSATION_DECISION_SOURCE_MISMATCH", 409)
        now = datetime.now(UTC)
        if decision.stage == "ORIGINAL":
            appeal = await session.scalar(
                select(Appeal.id).where(Appeal.case_id == trust_case.id)
            )
            if (
                trust_case.status != "DECIDED"
                or trust_case.appeal_until is None
                or trust_case.appeal_until > now
                or liability.appeal_until is None
                or liability.appeal_until > now
                or appeal is not None
                or decision.outcome not in {"SUBSTANTIATED", "PARTLY_SUBSTANTIATED"}
            ):
                raise risk_error("COMPENSATION_DECISION_NOT_FINAL", 409)
        elif decision.stage == "APPEAL":
            appeal = await session.scalar(
                select(Appeal).where(
                    Appeal.case_id == trust_case.id,
                    Appeal.appeal_decision_id == decision.id,
                    Appeal.status == "DECIDED",
                )
            )
            if (
                trust_case.status != "CLOSED"
                or appeal is None
                or decision.outcome != "AFFIRMED"
            ):
                raise risk_error("COMPENSATION_DECISION_NOT_FINAL", 409)
        else:
            raise risk_error("COMPENSATION_DECISION_NOT_FINAL", 409)
        if decision.established_loss is None:
            raise risk_error("COMPENSATION_DECISION_LOSS_REQUIRED", 409)
        loss = exact_amount(decision.established_loss, allow_zero=True)
        if loss <= 0:
            raise risk_error("COMPENSATION_DECISION_LOSS_REQUIRED", 409)
        return loss

    @staticmethod
    def _validate_accounts(
        liability: LiabilityCase, source: ShareAccount, destination: ShareAccount
    ) -> None:
        if (
            source.cooperative_id != liability.cooperative_id
            or destination.cooperative_id != liability.cooperative_id
            or source.id == destination.id
        ):
            raise risk_error("COMPENSATION_ACCOUNT_SCOPE_MISMATCH", 409)
        if (
            source.status != AccountStatus.ACTIVE.value
            or destination.status != AccountStatus.ACTIVE.value
        ):
            raise risk_error("COMPENSATION_ACCOUNT_NOT_ACTIVE", 409)
        if destination.contour != ShareContour.PRIMARY.value:
            raise risk_error("COMPENSATION_DESTINATION_NOT_PRIMARY", 409)
        if source.denomination != destination.denomination:
            raise risk_error("COMPENSATION_DENOMINATION_MISMATCH", 409)
        if source.member_id != liability.responsible_member_id:
            raise risk_error("COMPENSATION_SOURCE_ACCOUNT_MISMATCH", 409)

    @staticmethod
    async def _ensure_account_capacity(
        session: AsyncSession,
        source: ShareAccount,
        commitment: ExposureCommitment,
        amount: Decimal,
    ) -> None:
        reserved = await session.scalar(
            select(
                func.coalesce(
                    func.sum(
                        ExposureCommitment.amount_reserved
                        - ExposureCommitment.executed_amount
                    ),
                    0,
                )
            ).where(
                ExposureCommitment.account_id == source.id,
                ExposureCommitment.status == CommitmentStatus.ACTIVE.value,
            )
        )
        reserved_after = Decimal(reserved or 0)
        if commitment.status == CommitmentStatus.ACTIVE.value:
            reserved_after -= amount
        protected_after = (
            source.protected_amount
            + source.executed_not_settled
            + amount
            + reserved_after
        )
        if protected_after > source.balance:
            raise risk_error("COMPENSATION_ACCOUNT_AVAILABLE_EXCEEDED", 409)

    @staticmethod
    async def _locked_accounts(
        session: AsyncSession, first_id: UUID, second_id: UUID
    ) -> dict[UUID, ShareAccount]:
        rows = (
            await session.execute(
                select(ShareAccount)
                .where(ShareAccount.id.in_({first_id, second_id}))
                .order_by(ShareAccount.id)
                .with_for_update()
            )
        ).scalars()
        return {item.id: item for item in rows}

    @staticmethod
    async def _eligible_member(
        session: AsyncSession, cooperative_id: UUID, member_id: UUID
    ) -> None:
        member = await session.get(Member, member_id)
        membership = await session.scalar(
            select(Membership.id).where(
                Membership.cooperative_id == cooperative_id,
                Membership.member_id == member_id,
                Membership.status == "ACTIVE",
            )
        )
        if member is None or member.status != "ACTIVE" or membership is None:
            raise risk_error("MEMBER_NOT_ELIGIBLE", 409)

    @staticmethod
    async def _lock_cooperative(session: AsyncSession, cooperative_id: UUID) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"cooperative-clearing:risk:{cooperative_id}"},
        )

    @staticmethod
    def _version(current: int, expected: int) -> None:
        if current != expected:
            raise risk_error("VERSION_CONFLICT", 409)

    @staticmethod
    def _text(value: str, code: str, maximum: int) -> str:
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > maximum:
            raise risk_error(code)
        return normalized

    @staticmethod
    def _evidence_payload(items: Sequence[EvidenceBlob]) -> list[dict[str, object]]:
        return [
            {
                "evidence_id": str(item.id),
                "sha256": item.expected_sha256,
                "size": item.expected_size,
                "kind": item.kind,
            }
            for item in items
        ]

    @staticmethod
    def _link_evidence(
        session: AsyncSession,
        evidence: Sequence[EvidenceBlob],
        event_id: UUID,
        subject_type: str,
        subject_id: UUID,
    ) -> None:
        session.add_all(
            [
                EvidenceLink(
                    id=uuid4(),
                    evidence_id=item.id,
                    event_id=event_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                )
                for item in evidence
            ]
        )

    @staticmethod
    async def _audit(
        session: AsyncSession,
        principal: Principal,
        cooperative_id: UUID,
        action: str,
        transfer_id: UUID,
        event_id: UUID,
        request_id: UUID | None,
    ) -> None:
        await AuditRepository(session).record(
            action=action,
            object_type="CompensationTransfer",
            object_id=transfer_id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={"signed_event_id": str(event_id)},
        )
