"""Atomic policy, share exposure, guarantee, and liability workflows."""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import (
    Member,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.inventory.domain.types import decimal_text
from cooperative_clearing.modules.inventory.infrastructure.models import EvidenceBlob, EvidenceLink
from cooperative_clearing.modules.journal.application.service import SignedJournalService
from cooperative_clearing.modules.journal.domain.crypto import payload_hash
from cooperative_clearing.modules.risk.application.antifraud_enforcement import (
    require_antifraud_action_allowed,
)
from cooperative_clearing.modules.risk.application.common import (
    RiskCommandResult,
    begin_risk_command,
    complete_risk_command,
    risk_owner_actor,
    risk_role_actor,
)
from cooperative_clearing.modules.risk.domain.types import (
    AccountAmounts,
    AccountStatus,
    AntifraudSubjectType,
    CommitmentStatus,
    CommitmentType,
    ExposurePreview,
    FaultClass,
    LiabilityStatus,
    PolicyStatus,
    RelatedLinkStatus,
    ShareContour,
    ensure_contour_supports,
    exact_amount,
    exact_ratio,
    preview_exposure,
    risk_error,
)
from cooperative_clearing.modules.risk.infrastructure.models import (
    ExposureCommitment,
    LiabilityCase,
    RelatedPartyLink,
    RiskPolicy,
    ShareAccount,
    ShareContribution,
)
from cooperative_clearing.modules.trust.application.enforcement import (
    GUARANTEE_CREATE,
    RISK_COMMITMENT_CREATE,
    require_member_action_allowed,
)
from cooperative_clearing.shared.core.config import Settings

POLICY_PROPOSER_ROLES = {RoleCode.COOPERATIVE_ADMIN}
POLICY_APPROVER_ROLES = {RoleCode.RISK_ADMIN, RoleCode.AUDITOR}
ACCOUNT_OPERATOR_ROLES = {RoleCode.COOPERATIVE_ADMIN, RoleCode.RISK_ADMIN}
RISK_OPERATOR_ROLES = {RoleCode.RISK_ADMIN, RoleCode.AUDITOR}


class RiskService:
    def __init__(self, settings: Settings) -> None:
        self.journal = SignedJournalService(settings)

    async def propose_policy(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        denomination: str,
        max_member_exposure: Decimal,
        max_related_exposure: Decimal,
        max_guarantee_chain_depth: int,
        protected_amount_rule: str,
        related_party_rule: str,
        approval_reference: str,
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> RiskCommandResult:
        actor = risk_role_actor(principal, cooperative_id, POLICY_PROPOSER_ROLES)
        await self._eligible_member(session, cooperative_id, actor.person_id)
        denomination_value = self._code(denomination, "DENOMINATION_INVALID", 32)
        member_limit = exact_amount(max_member_exposure)
        related_limit = exact_amount(max_related_exposure)
        if related_limit < member_limit:
            raise risk_error("RELATED_LIMIT_BELOW_MEMBER_LIMIT")
        if not 1 <= max_guarantee_chain_depth <= 20:
            raise risk_error("GUARANTEE_CHAIN_DEPTH_INVALID")
        protected_rule = self._text(protected_amount_rule, "PROTECTED_AMOUNT_RULE_INVALID", 2000)
        related_rule = self._text(related_party_rule, "RELATED_PARTY_RULE_INVALID", 2000)
        approval_ref = self._text(approval_reference, "APPROVAL_REFERENCE_INVALID", 500)
        request_payload = {
            "cooperative_id": str(cooperative_id),
            "denomination": denomination_value,
            "max_member_exposure": decimal_text(member_limit),
            "max_related_exposure": decimal_text(related_limit),
            "max_guarantee_chain_depth": max_guarantee_chain_depth,
            "protected_amount_rule": protected_rule,
            "related_party_rule": related_rule,
            "approval_reference": approval_ref,
            "evidence_ids": [str(value) for value in evidence_ids],
        }
        record, replay = await begin_risk_command(
            session, principal, "RISK_POLICY_PROPOSE", idempotency_key, request_payload
        )
        if replay is not None:
            return replay
        evidence = await EvidenceService.require_ready(
            session, cooperative_id, evidence_ids, required=True
        )
        await self._lock_cooperative(session, cooperative_id)
        policy_version = (
            int(
                (
                    await session.execute(
                        select(func.coalesce(func.max(RiskPolicy.policy_version), 0)).where(
                            RiskPolicy.cooperative_id == cooperative_id
                        )
                    )
                ).scalar_one()
            )
            + 1
        )
        terms = {
            "cooperative_id": str(cooperative_id),
            "policy_version": policy_version,
            "denomination": denomination_value,
            "max_member_exposure": decimal_text(member_limit),
            "max_related_exposure": decimal_text(related_limit),
            "max_guarantee_chain_depth": max_guarantee_chain_depth,
            "protected_amount_rule": protected_rule,
            "related_party_rule": related_rule,
            "approval_reference": approval_ref,
        }
        terms_hash = payload_hash(terms)
        payload = {
            **terms,
            "terms_hash": terms_hash,
            "evidence": self._evidence_payload(evidence),
        }
        policy_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="risk.policy_proposed",
            aggregate_type="risk_policy",
            aggregate_id=policy_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "policy_id": str(policy_id)},
        )
        session.add(
            RiskPolicy(
                id=policy_id,
                cooperative_id=cooperative_id,
                policy_version=policy_version,
                denomination=denomination_value,
                max_member_exposure=member_limit,
                max_related_exposure=related_limit,
                max_guarantee_chain_depth=max_guarantee_chain_depth,
                terms_hash=terms_hash,
                terms_payload=terms,
                status=PolicyStatus.PROPOSED.value,
                proposed_by_user_id=principal.user_id,
                proposed_by_member_id=actor.person_id,
                proposed_role_assignment_id=actor.role_assignment_id,
                proposed_event_id=event.event_id,
                version=1,
            )
        )
        self._link_evidence(session, evidence, event.event_id, "risk_policy", policy_id)
        await self._audit(
            session,
            principal,
            cooperative_id,
            "RISK_POLICY_PROPOSED",
            "RiskPolicy",
            policy_id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, policy_id)

    async def approve_policy(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        policy_id: UUID,
        terms_hash: str,
        expected_version: int,
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> RiskCommandResult:
        policy = await session.get(RiskPolicy, policy_id, with_for_update=True)
        if policy is None:
            raise risk_error("POLICY_NOT_FOUND", 404)
        actor = risk_role_actor(principal, policy.cooperative_id, POLICY_APPROVER_ROLES)
        await self._eligible_member(session, policy.cooperative_id, actor.person_id)
        payload = {
            "policy_id": str(policy_id),
            "terms_hash": terms_hash,
            "expected_version": expected_version,
            "evidence_ids": [str(value) for value in evidence_ids],
        }
        record, replay = await begin_risk_command(
            session, principal, "RISK_POLICY_APPROVE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(policy.version, expected_version)
        if policy.status != PolicyStatus.PROPOSED.value:
            raise risk_error("POLICY_NOT_PROPOSED", 409)
        if policy.terms_hash != terms_hash:
            raise risk_error("POLICY_TERMS_HASH_MISMATCH", 409)
        if (
            policy.proposed_by_user_id == principal.user_id
            or policy.proposed_by_member_id == actor.person_id
        ):
            raise risk_error("POLICY_APPROVER_NOT_INDEPENDENT", 403)
        evidence = await EvidenceService.require_ready(
            session, policy.cooperative_id, evidence_ids, required=True
        )
        await self._lock_cooperative(session, policy.cooperative_id)
        active = list(
            (
                await session.execute(
                    select(RiskPolicy)
                    .where(
                        RiskPolicy.cooperative_id == policy.cooperative_id,
                        RiskPolicy.denomination == policy.denomination,
                        RiskPolicy.status == PolicyStatus.ACTIVE.value,
                        RiskPolicy.id != policy.id,
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        for previous in active:
            superseded_event = await self.journal.append(
                session,
                event_type="risk.policy_superseded",
                aggregate_type="risk_policy",
                aggregate_id=previous.id,
                aggregate_version=previous.version + 1,
                actor=actor,
                payload={
                    "superseded_policy_id": str(previous.id),
                    "superseded_policy_version": previous.policy_version,
                    "superseded_policy_hash": previous.terms_hash,
                    "replacement_policy_id": str(policy.id),
                    "replacement_policy_version": policy.policy_version,
                    "replacement_policy_hash": policy.terms_hash,
                },
            )
            previous.status = PolicyStatus.SUPERSEDED.value
            previous.version += 1
            await self._audit(
                session,
                principal,
                previous.cooperative_id,
                "RISK_POLICY_SUPERSEDED",
                "RiskPolicy",
                previous.id,
                superseded_event.event_id,
                request_id,
            )
        event = await self.journal.append(
            session,
            event_type="risk.policy_approved",
            aggregate_type="risk_policy",
            aggregate_id=policy.id,
            aggregate_version=policy.version + 1,
            actor=actor,
            payload={
                **payload,
                "policy_version": policy.policy_version,
                "superseded_policy_ids": [str(item.id) for item in active],
                "evidence": self._evidence_payload(evidence),
            },
        )
        now = datetime.now(UTC)
        policy.status = PolicyStatus.ACTIVE.value
        policy.approved_by_user_id = principal.user_id
        policy.approved_by_member_id = actor.person_id
        policy.approved_role_assignment_id = actor.role_assignment_id
        policy.approved_event_id = event.event_id
        policy.approved_at = now
        policy.version += 1
        self._link_evidence(session, evidence, event.event_id, "risk_policy", policy.id)
        await self._audit(
            session,
            principal,
            policy.cooperative_id,
            "RISK_POLICY_APPROVED",
            "RiskPolicy",
            policy.id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, policy.id)

    async def open_account(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        policy_id: UUID,
        member_id: UUID,
        contour: ShareContour,
        opening_balance: Decimal,
        protected_amount: Decimal,
        source_reference: str,
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> RiskCommandResult:
        policy = await self._active_policy(session, policy_id)
        actor = risk_role_actor(principal, policy.cooperative_id, ACCOUNT_OPERATOR_ROLES)
        await self._eligible_member(session, policy.cooperative_id, actor.person_id)
        await self._eligible_member(session, policy.cooperative_id, member_id)
        balance = exact_amount(opening_balance)
        protected = exact_amount(protected_amount, allow_zero=True)
        AccountAmounts(balance, protected, Decimal(0), Decimal(0)).validate()
        evidence = await EvidenceService.require_ready(
            session, policy.cooperative_id, evidence_ids, required=True
        )
        payload = {
            "policy_id": str(policy.id),
            "policy_version": policy.policy_version,
            "policy_hash": policy.terms_hash,
            "member_id": str(member_id),
            "contour": contour.value,
            "denomination": policy.denomination,
            "opening_balance": decimal_text(balance),
            "protected_amount": decimal_text(protected),
            "source_reference": self._text(source_reference, "SOURCE_REFERENCE_INVALID", 300),
            "evidence": self._evidence_payload(evidence),
        }
        record, replay = await begin_risk_command(
            session, principal, "RISK_SHARE_ACCOUNT_OPEN", idempotency_key, payload
        )
        if replay is not None:
            return replay
        account_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="shares.account_opened",
            aggregate_type="share_account",
            aggregate_id=account_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "account_id": str(account_id)},
        )
        session.add(
            ShareAccount(
                id=account_id,
                cooperative_id=policy.cooperative_id,
                member_id=member_id,
                opening_policy_id=policy.id,
                contour=contour.value,
                denomination=policy.denomination,
                balance=balance,
                protected_amount=protected,
                executed_not_settled=Decimal(0),
                status=AccountStatus.ACTIVE.value,
                created_by_user_id=principal.user_id,
                created_event_id=event.event_id,
                last_event_id=event.event_id,
                version=1,
            )
        )
        session.add(
            ShareContribution(
                id=uuid4(),
                account_id=account_id,
                amount=balance,
                entry_type="CONTRIBUTION",
                source_reference=str(payload["source_reference"]),
                recorded_by_user_id=principal.user_id,
                event_id=event.event_id,
            )
        )
        self._link_evidence(session, evidence, event.event_id, "share_account", account_id)
        await self._audit(
            session,
            principal,
            policy.cooperative_id,
            "SHARE_ACCOUNT_OPENED",
            "ShareAccount",
            account_id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, account_id)

    async def add_contribution(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        account_id: UUID,
        amount: Decimal,
        source_reference: str,
        evidence_ids: Sequence[UUID],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> RiskCommandResult:
        account = await session.get(ShareAccount, account_id, with_for_update=True)
        if account is None:
            raise risk_error("ACCOUNT_NOT_FOUND", 404)
        actor = risk_role_actor(principal, account.cooperative_id, ACCOUNT_OPERATOR_ROLES)
        await self._eligible_member(session, account.cooperative_id, actor.person_id)
        contribution = exact_amount(amount)
        source = self._text(source_reference, "SOURCE_REFERENCE_INVALID", 300)
        payload = {
            "account_id": str(account.id),
            "amount": decimal_text(contribution),
            "source_reference": source,
            "expected_version": expected_version,
            "evidence_ids": [str(value) for value in evidence_ids],
        }
        record, replay = await begin_risk_command(
            session, principal, "RISK_SHARE_CONTRIBUTE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(account.version, expected_version)
        if account.status != AccountStatus.ACTIVE.value:
            raise risk_error("ACCOUNT_NOT_ACTIVE", 409)
        new_balance = exact_amount(account.balance + contribution)
        evidence = await EvidenceService.require_ready(
            session, account.cooperative_id, evidence_ids, required=True
        )
        entry_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="shares.contribution_recorded",
            aggregate_type="share_account",
            aggregate_id=account.id,
            aggregate_version=account.version + 1,
            actor=actor,
            payload={
                **payload,
                "contribution_id": str(entry_id),
                "balance_before": decimal_text(account.balance),
                "balance_after": decimal_text(new_balance),
                "evidence": self._evidence_payload(evidence),
            },
        )
        session.add(
            ShareContribution(
                id=entry_id,
                account_id=account.id,
                amount=contribution,
                entry_type="CONTRIBUTION",
                source_reference=source,
                recorded_by_user_id=principal.user_id,
                event_id=event.event_id,
            )
        )
        account.balance = new_balance
        account.last_event_id = event.event_id
        account.updated_at = datetime.now(UTC)
        account.version += 1
        self._link_evidence(session, evidence, event.event_id, "share_account", account.id)
        await self._audit(
            session,
            principal,
            account.cooperative_id,
            "SHARE_CONTRIBUTION_RECORDED",
            "ShareAccount",
            account.id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, account.id)

    async def propose_related_link(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        member_a_id: UUID,
        member_b_id: UUID,
        relation_type: str,
        source_statement: str,
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> RiskCommandResult:
        actor = risk_role_actor(principal, cooperative_id, {RoleCode.RISK_ADMIN})
        await self._eligible_member(session, cooperative_id, actor.person_id)
        if member_a_id == member_b_id:
            raise risk_error("RELATED_PARTIES_IDENTICAL")
        member_a, member_b = sorted((member_a_id, member_b_id), key=lambda value: value.int)
        await self._eligible_member(session, cooperative_id, member_a)
        await self._eligible_member(session, cooperative_id, member_b)
        relation = self._code(relation_type, "RELATION_TYPE_INVALID", 24)
        if relation not in {"HOUSEHOLD", "CONTROL", "RELATED"}:
            raise risk_error("RELATION_TYPE_INVALID")
        statement = self._text(source_statement, "RELATED_SOURCE_INVALID", 4000)
        evidence = await EvidenceService.require_ready(
            session, cooperative_id, evidence_ids, required=True
        )
        payload = {
            "cooperative_id": str(cooperative_id),
            "member_a_id": str(member_a),
            "member_b_id": str(member_b),
            "relation_type": relation,
            "source_statement": statement,
            "evidence": self._evidence_payload(evidence),
        }
        record, replay = await begin_risk_command(
            session, principal, "RISK_RELATED_LINK_PROPOSE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        link_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="risk.related_party_link_proposed",
            aggregate_type="related_party_link",
            aggregate_id=link_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "link_id": str(link_id)},
        )
        session.add(
            RelatedPartyLink(
                id=link_id,
                cooperative_id=cooperative_id,
                member_a_id=member_a,
                member_b_id=member_b,
                relation_type=relation,
                source_statement=statement,
                status=RelatedLinkStatus.PENDING_APPROVAL.value,
                proposed_by_user_id=principal.user_id,
                proposed_by_member_id=actor.person_id,
                proposed_event_id=event.event_id,
                version=1,
            )
        )
        self._link_evidence(session, evidence, event.event_id, "related_party_link", link_id)
        await self._audit(
            session,
            principal,
            cooperative_id,
            "RELATED_PARTY_LINK_PROPOSED",
            "RelatedPartyLink",
            link_id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, link_id)

    async def decide_related_link(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        link_id: UUID,
        approve: bool,
        decision_notes: str,
        evidence_ids: Sequence[UUID],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> RiskCommandResult:
        link = await session.get(RelatedPartyLink, link_id, with_for_update=True)
        if link is None:
            raise risk_error("RELATED_LINK_NOT_FOUND", 404)
        actor = risk_role_actor(principal, link.cooperative_id, POLICY_APPROVER_ROLES)
        await self._eligible_member(session, link.cooperative_id, actor.person_id)
        payload = {
            "link_id": str(link.id),
            "approve": approve,
            "decision_notes": self._text(decision_notes, "DECISION_NOTES_INVALID", 2000),
            "expected_version": expected_version,
            "evidence_ids": [str(value) for value in evidence_ids],
        }
        record, replay = await begin_risk_command(
            session, principal, "RISK_RELATED_LINK_DECIDE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(link.version, expected_version)
        if link.status != RelatedLinkStatus.PENDING_APPROVAL.value:
            raise risk_error("RELATED_LINK_NOT_PENDING", 409)
        if link.proposed_by_user_id == principal.user_id or actor.person_id in {
            link.proposed_by_member_id,
            link.member_a_id,
            link.member_b_id,
        }:
            raise risk_error("RELATED_LINK_DECIDER_NOT_INDEPENDENT", 403)
        evidence = await EvidenceService.require_ready(
            session, link.cooperative_id, evidence_ids, required=True
        )
        await self._lock_cooperative(session, link.cooperative_id)
        if approve:
            await self._ensure_merged_group_limits(
                session, link.cooperative_id, link.member_a_id, link.member_b_id
            )
        target = RelatedLinkStatus.ACTIVE if approve else RelatedLinkStatus.REJECTED
        event = await self.journal.append(
            session,
            event_type=(
                "risk.related_party_link_approved"
                if approve
                else "risk.related_party_link_rejected"
            ),
            aggregate_type="related_party_link",
            aggregate_id=link.id,
            aggregate_version=link.version + 1,
            actor=actor,
            payload={
                **payload,
                "member_a_id": str(link.member_a_id),
                "member_b_id": str(link.member_b_id),
                "resulting_status": target.value,
                "evidence": self._evidence_payload(evidence),
            },
        )
        link.status = target.value
        link.decided_by_user_id = principal.user_id
        link.decided_by_member_id = actor.person_id
        link.decision_event_id = event.event_id
        link.decided_at = datetime.now(UTC)
        link.version += 1
        self._link_evidence(session, evidence, event.event_id, "related_party_link", link.id)
        await self._audit(
            session,
            principal,
            link.cooperative_id,
            "RELATED_PARTY_LINK_DECIDED",
            "RelatedPartyLink",
            link.id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, link.id)

    async def preview_commitment(
        self,
        session: AsyncSession,
        *,
        account_id: UUID,
        policy_id: UUID,
        commitment_type: CommitmentType,
        amount_reserved: Decimal,
        max_loss: Decimal,
    ) -> ExposurePreview:
        account = await session.get(ShareAccount, account_id)
        if account is None:
            raise risk_error("ACCOUNT_NOT_FOUND", 404)
        policy = await self._active_policy(session, policy_id)
        if (
            account.cooperative_id != policy.cooperative_id
            or account.denomination != policy.denomination
        ):
            raise risk_error("POLICY_ACCOUNT_MISMATCH", 409)
        if account.status != AccountStatus.ACTIVE.value:
            raise risk_error("ACCOUNT_NOT_ACTIVE", 409)
        ensure_contour_supports(ShareContour(account.contour), commitment_type)
        return await self._exposure_preview(
            session,
            account=account,
            policy=policy,
            amount_reserved=amount_reserved,
            max_loss=max_loss,
        )

    async def propose_commitment(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        account_id: UUID,
        policy_id: UUID,
        commitment_type: CommitmentType,
        risk_type: str,
        risk_id: UUID,
        debtor_member_id: UUID | None,
        beneficiary_member_id: UUID | None,
        role_assignment_id: UUID | None,
        amount_reserved: Decimal,
        max_loss: Decimal,
        coverage_ratio: Decimal,
        starts_at: datetime,
        expires_at: datetime,
        release_condition: str,
        trigger_conditions: str,
        exclusions: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> RiskCommandResult:
        account = await session.get(ShareAccount, account_id)
        if account is None:
            raise risk_error("ACCOUNT_NOT_FOUND", 404)
        actor = risk_role_actor(principal, account.cooperative_id, RISK_OPERATOR_ROLES)
        await self._eligible_member(session, account.cooperative_id, actor.person_id)
        policy = await self._active_policy(session, policy_id)
        if (
            account.cooperative_id != policy.cooperative_id
            or account.denomination != policy.denomination
        ):
            raise risk_error("POLICY_ACCOUNT_MISMATCH", 409)
        if account.status != AccountStatus.ACTIVE.value:
            raise risk_error("ACCOUNT_NOT_ACTIVE", 409)
        ensure_contour_supports(ShareContour(account.contour), commitment_type)
        amount = exact_amount(amount_reserved)
        loss = exact_amount(max_loss)
        ratio = exact_ratio(coverage_ratio)
        if loss > amount:
            raise risk_error("COMMITMENT_AMOUNTS_INVALID")
        start = self._utc(starts_at, "COMMITMENT_PERIOD_INVALID")
        expiry = self._utc(expires_at, "COMMITMENT_PERIOD_INVALID")
        if start >= expiry or expiry <= datetime.now(UTC):
            raise risk_error("COMMITMENT_PERIOD_INVALID")
        normalized_risk_type = self._code(risk_type, "RISK_TYPE_INVALID", 64)
        terms = {
            "account_id": str(account.id),
            "owner_member_id": str(account.member_id),
            "policy_id": str(policy.id),
            "policy_version": policy.policy_version,
            "policy_hash": policy.terms_hash,
            "commitment_type": commitment_type.value,
            "risk_type": normalized_risk_type,
            "risk_id": str(risk_id),
            "debtor_member_id": str(debtor_member_id) if debtor_member_id else None,
            "beneficiary_member_id": (
                str(beneficiary_member_id) if beneficiary_member_id else None
            ),
            "role_assignment_id": str(role_assignment_id) if role_assignment_id else None,
            "amount_reserved": decimal_text(amount),
            "max_loss": decimal_text(loss),
            "coverage_ratio": decimal_text(ratio),
            "starts_at": start.isoformat(),
            "expires_at": expiry.isoformat(),
            "release_condition": self._text(release_condition, "RELEASE_CONDITION_INVALID", 4000),
            "trigger_conditions": self._text(
                trigger_conditions, "TRIGGER_CONDITIONS_INVALID", 4000
            ),
            "exclusions": self._text(exclusions, "EXCLUSIONS_INVALID", 4000),
        }
        terms_hash = payload_hash(terms)
        request_payload = {**terms, "terms_hash": terms_hash}
        record, replay = await begin_risk_command(
            session, principal, "RISK_COMMITMENT_PROPOSE", idempotency_key, request_payload
        )
        if replay is not None:
            return replay
        antifraud_subjects = [
            (AntifraudSubjectType.MEMBER, account.member_id),
            (AntifraudSubjectType.SHARE_ACCOUNT, account.id),
        ]
        if debtor_member_id is not None:
            antifraud_subjects.append((AntifraudSubjectType.MEMBER, debtor_member_id))
        await require_antifraud_action_allowed(
            session,
            cooperative_id=account.cooperative_id,
            subjects=antifraud_subjects,
        )
        await self._validate_commitment_parties(
            session,
            account,
            commitment_type,
            debtor_member_id,
            beneficiary_member_id,
            role_assignment_id,
        )
        affected_members = {account.member_id}
        if debtor_member_id is not None:
            affected_members.add(debtor_member_id)
        await require_member_action_allowed(
            session,
            cooperative_id=account.cooperative_id,
            member_ids=affected_members,
            action=RISK_COMMITMENT_CREATE,
        )
        if commitment_type is CommitmentType.GUARANTEE:
            await require_member_action_allowed(
                session,
                cooperative_id=account.cooperative_id,
                member_ids=affected_members,
                action=GUARANTEE_CREATE,
            )
        preview = await self._exposure_preview(
            session,
            account=account,
            policy=policy,
            amount_reserved=amount,
            max_loss=loss,
        )
        if not preview.allowed:
            raise risk_error(preview.reason_code or "EXPOSURE_REJECTED", 409)
        if commitment_type is CommitmentType.GUARANTEE:
            assert debtor_member_id is not None
            await self._validate_guarantee_graph(
                session,
                account.cooperative_id,
                account.member_id,
                debtor_member_id,
                policy.max_guarantee_chain_depth,
            )
        payload = {
            **request_payload,
            "exposure_preview": self._preview_payload(preview),
        }
        commitment_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="shares.exposure_proposed",
            aggregate_type="exposure_commitment",
            aggregate_id=commitment_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "commitment_id": str(commitment_id)},
        )
        session.add(
            ExposureCommitment(
                id=commitment_id,
                cooperative_id=account.cooperative_id,
                policy_id=policy.id,
                account_id=account.id,
                owner_member_id=account.member_id,
                commitment_type=commitment_type.value,
                risk_type=normalized_risk_type,
                risk_id=risk_id,
                debtor_member_id=debtor_member_id,
                beneficiary_member_id=beneficiary_member_id,
                role_assignment_id=role_assignment_id,
                amount_reserved=amount,
                max_loss=loss,
                coverage_ratio=ratio,
                starts_at=start,
                expires_at=expiry,
                release_condition=str(terms["release_condition"]),
                trigger_conditions=str(terms["trigger_conditions"]),
                exclusions=str(terms["exclusions"]),
                terms_hash=terms_hash,
                terms_payload=terms,
                status=CommitmentStatus.PROPOSED.value,
                proposed_by_user_id=principal.user_id,
                proposed_by_member_id=actor.person_id,
                proposed_role_assignment_id=actor.role_assignment_id,
                proposed_event_id=event.event_id,
                version=1,
            )
        )
        await self._audit(
            session,
            principal,
            account.cooperative_id,
            "EXPOSURE_COMMITMENT_PROPOSED",
            "ExposureCommitment",
            commitment_id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, commitment_id)

    async def accept_commitment(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        commitment_id: UUID,
        terms_hash: str,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> RiskCommandResult:
        commitment = await session.get(ExposureCommitment, commitment_id, with_for_update=True)
        if commitment is None:
            raise risk_error("COMMITMENT_NOT_FOUND", 404)
        actor = risk_owner_actor(principal, commitment.cooperative_id, commitment.owner_member_id)
        await self._eligible_member(session, commitment.cooperative_id, actor.person_id)
        payload = {
            "commitment_id": str(commitment.id),
            "terms_hash": terms_hash,
            "expected_version": expected_version,
        }
        record, replay = await begin_risk_command(
            session, principal, "RISK_COMMITMENT_ACCEPT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        antifraud_subjects = [
            (AntifraudSubjectType.MEMBER, commitment.owner_member_id),
            (AntifraudSubjectType.SHARE_ACCOUNT, commitment.account_id),
            (AntifraudSubjectType.EXPOSURE_COMMITMENT, commitment.id),
        ]
        if commitment.debtor_member_id is not None:
            antifraud_subjects.append(
                (AntifraudSubjectType.MEMBER, commitment.debtor_member_id)
            )
        await require_antifraud_action_allowed(
            session,
            cooperative_id=commitment.cooperative_id,
            subjects=antifraud_subjects,
        )
        self._version(commitment.version, expected_version)
        if commitment.status != CommitmentStatus.PROPOSED.value:
            raise risk_error("COMMITMENT_NOT_PROPOSED", 409)
        if commitment.terms_hash != terms_hash:
            raise risk_error("COMMITMENT_TERMS_HASH_MISMATCH", 409)
        await self._lock_cooperative(session, commitment.cooperative_id)
        policy = await self._active_policy(session, commitment.policy_id)
        account = await session.get(ShareAccount, commitment.account_id, with_for_update=True)
        if account is None or account.status != AccountStatus.ACTIVE.value:
            raise risk_error("ACCOUNT_NOT_ACTIVE", 409)
        affected_members = {account.member_id}
        if commitment.debtor_member_id is not None:
            affected_members.add(commitment.debtor_member_id)
        await require_member_action_allowed(
            session,
            cooperative_id=account.cooperative_id,
            member_ids=affected_members,
            action=RISK_COMMITMENT_CREATE,
        )
        if commitment.commitment_type == CommitmentType.GUARANTEE.value:
            await require_member_action_allowed(
                session,
                cooperative_id=account.cooperative_id,
                member_ids=affected_members,
                action=GUARANTEE_CREATE,
            )
        preview = await self._exposure_preview(
            session,
            account=account,
            policy=policy,
            amount_reserved=commitment.amount_reserved,
            max_loss=commitment.max_loss,
        )
        if not preview.allowed:
            raise risk_error(preview.reason_code or "EXPOSURE_REJECTED", 409)
        if commitment.commitment_type == CommitmentType.GUARANTEE.value:
            if commitment.debtor_member_id is None:
                raise risk_error("GUARANTEE_PARTIES_INVALID", 500)
            await self._validate_guarantee_graph(
                session,
                commitment.cooperative_id,
                commitment.owner_member_id,
                commitment.debtor_member_id,
                policy.max_guarantee_chain_depth,
            )
        event = await self.journal.append(
            session,
            event_type="shares.exposure_reserved",
            aggregate_type="exposure_commitment",
            aggregate_id=commitment.id,
            aggregate_version=commitment.version + 1,
            actor=actor,
            payload={
                **payload,
                "owner_member_id": str(commitment.owner_member_id),
                "commitment_type": commitment.commitment_type,
                "risk_type": commitment.risk_type,
                "risk_id": str(commitment.risk_id),
                "amount_reserved": decimal_text(commitment.amount_reserved),
                "max_loss": decimal_text(commitment.max_loss),
                "exposure_preview": self._preview_payload(preview),
            },
        )
        commitment.status = CommitmentStatus.ACTIVE.value
        commitment.accepted_by_user_id = principal.user_id
        commitment.accepted_role_assignment_id = actor.role_assignment_id
        commitment.accepted_event_id = event.event_id
        commitment.accepted_at = datetime.now(UTC)
        commitment.version += 1
        await self._audit(
            session,
            principal,
            commitment.cooperative_id,
            "EXPOSURE_COMMITMENT_ACCEPTED",
            "ExposureCommitment",
            commitment.id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, commitment.id)

    async def release_commitment(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        commitment_id: UUID,
        reason: str,
        evidence_ids: Sequence[UUID],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> RiskCommandResult:
        commitment = await session.get(ExposureCommitment, commitment_id, with_for_update=True)
        if commitment is None:
            raise risk_error("COMMITMENT_NOT_FOUND", 404)
        actor = risk_role_actor(principal, commitment.cooperative_id, RISK_OPERATOR_ROLES)
        await self._eligible_member(session, commitment.cooperative_id, actor.person_id)
        normalized_reason = self._text(reason, "RELEASE_REASON_INVALID", 2000)
        payload = {
            "commitment_id": str(commitment.id),
            "reason": normalized_reason,
            "expected_version": expected_version,
            "evidence_ids": [str(value) for value in evidence_ids],
        }
        record, replay = await begin_risk_command(
            session, principal, "RISK_COMMITMENT_RELEASE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(commitment.version, expected_version)
        if commitment.status not in {
            CommitmentStatus.PROPOSED.value,
            CommitmentStatus.ACTIVE.value,
        }:
            raise risk_error("COMMITMENT_NOT_RELEASABLE", 409)
        evidence = await EvidenceService.require_ready(
            session, commitment.cooperative_id, evidence_ids, required=True
        )
        target = (
            CommitmentStatus.CANCELLED
            if commitment.status == CommitmentStatus.PROPOSED.value
            else CommitmentStatus.RELEASED
        )
        event = await self.journal.append(
            session,
            event_type=(
                "shares.exposure_cancelled"
                if target is CommitmentStatus.CANCELLED
                else "shares.exposure_released"
            ),
            aggregate_type="exposure_commitment",
            aggregate_id=commitment.id,
            aggregate_version=commitment.version + 1,
            actor=actor,
            payload={
                **payload,
                "from_status": commitment.status,
                "to_status": target.value,
                "evidence": self._evidence_payload(evidence),
            },
        )
        commitment.status = target.value
        commitment.released_by_user_id = principal.user_id
        commitment.released_event_id = event.event_id
        commitment.release_reason = normalized_reason
        commitment.released_at = datetime.now(UTC)
        commitment.version += 1
        self._link_evidence(session, evidence, event.event_id, "exposure_commitment", commitment.id)
        await self._audit(
            session,
            principal,
            commitment.cooperative_id,
            "EXPOSURE_COMMITMENT_RELEASED",
            "ExposureCommitment",
            commitment.id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, commitment.id)

    async def open_liability_case(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        commitment_id: UUID,
        incident_reference: str,
        affected_amount: Decimal,
        facts: str,
        causal_graph: dict[str, object],
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> RiskCommandResult:
        commitment = await session.get(ExposureCommitment, commitment_id)
        if commitment is None:
            raise risk_error("COMMITMENT_NOT_FOUND", 404)
        actor = risk_role_actor(principal, commitment.cooperative_id, RISK_OPERATOR_ROLES)
        await self._eligible_member(session, commitment.cooperative_id, actor.person_id)
        if actor.person_id == commitment.owner_member_id:
            raise risk_error("LIABILITY_OPENER_NOT_INDEPENDENT", 403)
        if commitment.status not in {
            CommitmentStatus.ACTIVE.value,
            CommitmentStatus.RELEASED.value,
        }:
            raise risk_error("COMMITMENT_NOT_CASE_ELIGIBLE", 409)
        amount = exact_amount(affected_amount)
        if amount > commitment.max_loss:
            raise risk_error("LIABILITY_AFFECTED_EXCEEDS_MAX_LOSS", 409)
        if not causal_graph:
            raise risk_error("LIABILITY_CAUSAL_GRAPH_REQUIRED")
        normalized_facts = self._text(facts, "LIABILITY_FACTS_INVALID", 8000)
        incident = self._code(incident_reference, "INCIDENT_REFERENCE_INVALID", 80)
        evidence = await EvidenceService.require_ready(
            session, commitment.cooperative_id, evidence_ids, required=True
        )
        payload = {
            "commitment_id": str(commitment.id),
            "incident_reference": incident,
            "responsible_member_id": str(commitment.owner_member_id),
            "affected_amount": decimal_text(amount),
            "facts": normalized_facts,
            "causal_graph": causal_graph,
            "max_loss": decimal_text(commitment.max_loss),
            "evidence": self._evidence_payload(evidence),
        }
        record, replay = await begin_risk_command(
            session, principal, "RISK_LIABILITY_CASE_OPEN", idempotency_key, payload
        )
        if replay is not None:
            return replay
        case_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="liability.case_opened",
            aggregate_type="liability_case",
            aggregate_id=case_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "case_id": str(case_id)},
        )
        session.add(
            LiabilityCase(
                id=case_id,
                cooperative_id=commitment.cooperative_id,
                commitment_id=commitment.id,
                incident_reference=incident,
                responsible_member_id=commitment.owner_member_id,
                affected_amount=amount,
                facts=normalized_facts,
                causal_graph=causal_graph,
                status=LiabilityStatus.OPEN.value,
                opened_by_user_id=principal.user_id,
                opened_by_member_id=actor.person_id,
                opened_event_id=event.event_id,
                version=1,
            )
        )
        self._link_evidence(session, evidence, event.event_id, "liability_case", case_id)
        await self._audit(
            session,
            principal,
            commitment.cooperative_id,
            "LIABILITY_CASE_OPENED",
            "LiabilityCase",
            case_id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, case_id)

    async def assess_liability_case(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        case_id: UUID,
        fault_class: FaultClass,
        assessed_loss: Decimal,
        rationale: str,
        appeal_until: datetime,
        evidence_ids: Sequence[UUID],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> RiskCommandResult:
        case = await session.get(LiabilityCase, case_id, with_for_update=True)
        if case is None:
            raise risk_error("LIABILITY_CASE_NOT_FOUND", 404)
        actor = risk_role_actor(principal, case.cooperative_id, RISK_OPERATOR_ROLES)
        await self._eligible_member(session, case.cooperative_id, actor.person_id)
        payload = {
            "case_id": str(case.id),
            "fault_class": fault_class.value,
            "assessed_loss": decimal_text(exact_amount(assessed_loss, allow_zero=True)),
            "rationale": self._text(rationale, "ASSESSMENT_RATIONALE_INVALID", 8000),
            "appeal_until": self._utc(appeal_until, "APPEAL_PERIOD_INVALID").isoformat(),
            "expected_version": expected_version,
            "evidence_ids": [str(value) for value in evidence_ids],
        }
        record, replay = await begin_risk_command(
            session, principal, "RISK_LIABILITY_CASE_ASSESS", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(case.version, expected_version)
        if case.status != LiabilityStatus.OPEN.value:
            raise risk_error("LIABILITY_CASE_NOT_OPEN", 409)
        if actor.person_id in {case.opened_by_member_id, case.responsible_member_id}:
            raise risk_error("LIABILITY_ASSESSOR_NOT_INDEPENDENT", 403)
        loss = exact_amount(assessed_loss, allow_zero=True)
        commitment = await session.get(ExposureCommitment, case.commitment_id)
        if commitment is None:
            raise risk_error("COMMITMENT_NOT_FOUND", 500)
        if loss > case.affected_amount or loss > commitment.max_loss:
            raise risk_error("LIABILITY_LOSS_EXCEEDS_BOUND", 409)
        await self._lock_cooperative(session, case.cooperative_id)
        assessed_other = (
            await session.execute(
                select(func.coalesce(func.sum(LiabilityCase.assessed_loss), 0)).where(
                    LiabilityCase.commitment_id == commitment.id,
                    LiabilityCase.id != case.id,
                    LiabilityCase.status == LiabilityStatus.ASSESSED.value,
                )
            )
        ).scalar_one()
        if Decimal(assessed_other or 0) + loss > commitment.max_loss:
            raise risk_error("LIABILITY_AGGREGATE_LOSS_EXCEEDS_BOUND", 409)
        appeal_deadline = self._utc(appeal_until, "APPEAL_PERIOD_INVALID")
        if appeal_deadline <= datetime.now(UTC):
            raise risk_error("APPEAL_PERIOD_INVALID")
        evidence = await EvidenceService.require_ready(
            session, case.cooperative_id, evidence_ids, required=True
        )
        coverage = {
            "commitment_id": str(commitment.id),
            "account_id": str(commitment.account_id),
            "reservation_amount": decimal_text(commitment.amount_reserved),
            "max_loss": decimal_text(commitment.max_loss),
            "assessed_loss": decimal_text(loss),
            "protected_amount_excluded": True,
            "execution_status": "NOT_EXECUTED",
        }
        event = await self.journal.append(
            session,
            event_type="liability.assessment_recorded",
            aggregate_type="liability_case",
            aggregate_id=case.id,
            aggregate_version=case.version + 1,
            actor=actor,
            payload={
                **payload,
                "responsible_member_id": str(case.responsible_member_id),
                "coverage": coverage,
                "evidence": self._evidence_payload(evidence),
            },
        )
        case.status = LiabilityStatus.ASSESSED.value
        case.fault_class = fault_class.value
        case.assessed_loss = loss
        case.coverage_summary = coverage
        case.assessment_rationale = str(payload["rationale"])
        case.assessed_by_user_id = principal.user_id
        case.assessed_by_member_id = actor.person_id
        case.assessed_event_id = event.event_id
        case.appeal_until = appeal_deadline
        case.assessed_at = datetime.now(UTC)
        case.version += 1
        self._link_evidence(session, evidence, event.event_id, "liability_case", case.id)
        await self._audit(
            session,
            principal,
            case.cooperative_id,
            "LIABILITY_ASSESSMENT_RECORDED",
            "LiabilityCase",
            case.id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, case.id)

    async def _exposure_preview(
        self,
        session: AsyncSession,
        *,
        account: ShareAccount,
        policy: RiskPolicy,
        amount_reserved: Decimal,
        max_loss: Decimal,
    ) -> ExposurePreview:
        reserved = (
            await session.execute(
                select(
                    func.coalesce(
                        func.sum(
                            ExposureCommitment.amount_reserved
                            - ExposureCommitment.executed_amount
                        ),
                        0,
                    )
                ).where(
                    ExposureCommitment.account_id == account.id,
                    ExposureCommitment.status == CommitmentStatus.ACTIVE.value,
                )
            )
        ).scalar_one()
        direct = (
            await session.execute(
                select(
                    func.coalesce(
                        func.sum(
                            ExposureCommitment.max_loss
                            - ExposureCommitment.executed_amount
                        ),
                        0,
                    )
                ).where(
                    ExposureCommitment.cooperative_id == account.cooperative_id,
                    ExposureCommitment.owner_member_id == account.member_id,
                    ExposureCommitment.status == CommitmentStatus.ACTIVE.value,
                )
            )
        ).scalar_one()
        group = await self._related_group(session, account.cooperative_id, account.member_id)
        related = (
            await session.execute(
                select(
                    func.coalesce(
                        func.sum(
                            ExposureCommitment.max_loss
                            - ExposureCommitment.executed_amount
                        ),
                        0,
                    )
                ).where(
                    ExposureCommitment.cooperative_id == account.cooperative_id,
                    ExposureCommitment.owner_member_id.in_(group),
                    ExposureCommitment.status == CommitmentStatus.ACTIVE.value,
                )
            )
        ).scalar_one()
        return preview_exposure(
            account=AccountAmounts(
                balance=account.balance,
                protected=account.protected_amount,
                reserved=Decimal(reserved),
                executed_not_settled=account.executed_not_settled,
            ),
            proposed_reservation=amount_reserved,
            proposed_max_loss=max_loss,
            member_exposure=Decimal(direct),
            related_exposure=Decimal(related),
            max_member_exposure=policy.max_member_exposure,
            max_related_exposure=policy.max_related_exposure,
        )

    async def _validate_commitment_parties(
        self,
        session: AsyncSession,
        account: ShareAccount,
        commitment_type: CommitmentType,
        debtor_member_id: UUID | None,
        beneficiary_member_id: UUID | None,
        role_assignment_id: UUID | None,
    ) -> None:
        if commitment_type is CommitmentType.GUARANTEE:
            if debtor_member_id is None or beneficiary_member_id is None:
                raise risk_error("GUARANTEE_PARTIES_REQUIRED")
            if len({account.member_id, debtor_member_id, beneficiary_member_id}) != 3:
                raise risk_error("GUARANTEE_SELF_OR_DUPLICATE_PARTY")
            await self._eligible_member(session, account.cooperative_id, debtor_member_id)
            await self._eligible_member(session, account.cooperative_id, beneficiary_member_id)
        elif debtor_member_id not in {None, account.member_id}:
            raise risk_error("COMMITMENT_DEBTOR_MISMATCH")
        if commitment_type is CommitmentType.ROLE_BOND:
            if role_assignment_id is None:
                raise risk_error("ROLE_BOND_ASSIGNMENT_REQUIRED")
            assignment = await session.get(RoleAssignment, role_assignment_id)
            if assignment is None or assignment.status != "ACTIVE":
                raise risk_error("ROLE_BOND_ASSIGNMENT_NOT_ACTIVE", 409)
            user = await session.get(UserAccount, assignment.user_id)
            if (
                user is None
                or user.member_id != account.member_id
                or assignment.cooperative_id not in {None, account.cooperative_id}
            ):
                raise risk_error("ROLE_BOND_ASSIGNMENT_OWNER_MISMATCH", 409)
        elif role_assignment_id is not None:
            raise risk_error("ROLE_ASSIGNMENT_NOT_ALLOWED")

    async def _validate_guarantee_graph(
        self,
        session: AsyncSession,
        cooperative_id: UUID,
        guarantor_id: UUID,
        debtor_id: UUID,
        max_depth: int,
    ) -> None:
        rows = list(
            (
                await session.execute(
                    select(
                        ExposureCommitment.owner_member_id,
                        ExposureCommitment.debtor_member_id,
                    ).where(
                        ExposureCommitment.cooperative_id == cooperative_id,
                        ExposureCommitment.commitment_type == CommitmentType.GUARANTEE.value,
                        ExposureCommitment.status == CommitmentStatus.ACTIVE.value,
                    )
                )
            ).all()
        )
        edges: dict[UUID, set[UUID]] = {}
        for source, target in rows:
            if target is not None:
                edges.setdefault(source, set()).add(target)
        edges.setdefault(guarantor_id, set()).add(debtor_id)
        visiting: set[UUID] = set()
        complete: dict[UUID, int] = {}

        def depth(node: UUID) -> int:
            if node in visiting:
                raise risk_error("GUARANTEE_CYCLE_DETECTED", 409)
            if node in complete:
                return complete[node]
            visiting.add(node)
            value = max((1 + depth(child) for child in edges.get(node, set())), default=0)
            visiting.remove(node)
            complete[node] = value
            return value

        graph_depth = max((depth(node) for node in edges), default=0)
        if graph_depth > max_depth:
            raise risk_error("GUARANTEE_CHAIN_DEPTH_EXCEEDED", 409)

    async def _ensure_merged_group_limits(
        self,
        session: AsyncSession,
        cooperative_id: UUID,
        member_a_id: UUID,
        member_b_id: UUID,
    ) -> None:
        group_a = await self._related_group(session, cooperative_id, member_a_id)
        group_b = await self._related_group(session, cooperative_id, member_b_id)
        members = group_a | group_b
        exposures = list(
            (
                await session.execute(
                    select(
                        ShareAccount.denomination,
                        func.sum(
                            ExposureCommitment.max_loss
                            - ExposureCommitment.executed_amount
                        ),
                    )
                    .join(ShareAccount, ShareAccount.id == ExposureCommitment.account_id)
                    .where(
                        ExposureCommitment.cooperative_id == cooperative_id,
                        ExposureCommitment.owner_member_id.in_(members),
                        ExposureCommitment.status == CommitmentStatus.ACTIVE.value,
                    )
                    .group_by(ShareAccount.denomination)
                )
            ).all()
        )
        for denomination, total in exposures:
            policy = (
                await session.execute(
                    select(RiskPolicy).where(
                        RiskPolicy.cooperative_id == cooperative_id,
                        RiskPolicy.denomination == denomination,
                        RiskPolicy.status == PolicyStatus.ACTIVE.value,
                    )
                )
            ).scalar_one_or_none()
            if policy is None or Decimal(total) > policy.max_related_exposure:
                raise risk_error("RELATED_GROUP_EXPOSURE_LIMIT_EXCEEDED", 409)

    @staticmethod
    async def _related_group(
        session: AsyncSession, cooperative_id: UUID, member_id: UUID
    ) -> set[UUID]:
        rows = list(
            (
                await session.execute(
                    select(RelatedPartyLink.member_a_id, RelatedPartyLink.member_b_id).where(
                        RelatedPartyLink.cooperative_id == cooperative_id,
                        RelatedPartyLink.status == RelatedLinkStatus.ACTIVE.value,
                    )
                )
            ).all()
        )
        edges: dict[UUID, set[UUID]] = {}
        for left, right in rows:
            edges.setdefault(left, set()).add(right)
            edges.setdefault(right, set()).add(left)
        seen = {member_id}
        pending = [member_id]
        while pending:
            current = pending.pop()
            for neighbor in edges.get(current, set()) - seen:
                seen.add(neighbor)
                pending.append(neighbor)
        return seen

    @staticmethod
    async def _active_policy(session: AsyncSession, policy_id: UUID) -> RiskPolicy:
        policy = await session.get(RiskPolicy, policy_id)
        if policy is None:
            raise risk_error("POLICY_NOT_FOUND", 404)
        if policy.status != PolicyStatus.ACTIVE.value:
            raise risk_error("POLICY_NOT_ACTIVE", 409)
        return policy

    @staticmethod
    async def _eligible_member(
        session: AsyncSession, cooperative_id: UUID, member_id: UUID
    ) -> None:
        member = await session.get(Member, member_id)
        membership = (
            await session.execute(
                select(Membership.id).where(
                    Membership.cooperative_id == cooperative_id,
                    Membership.member_id == member_id,
                    Membership.status == "ACTIVE",
                )
            )
        ).scalar_one_or_none()
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

    @classmethod
    def _code(cls, value: str, code: str, maximum: int) -> str:
        normalized = cls._text(value, code, maximum).upper()
        if not normalized.isascii() or not all(
            character.isalnum() or character in {"_", "-", "."} for character in normalized
        ):
            raise risk_error(code)
        return normalized

    @staticmethod
    def _utc(value: datetime, code: str) -> datetime:
        if value.utcoffset() is None:
            raise risk_error(code)
        return value.astimezone(UTC)

    @staticmethod
    def _preview_payload(preview: ExposurePreview) -> dict[str, object]:
        return {
            "account_available_before": decimal_text(preview.account_available_before),
            "account_available_after": decimal_text(preview.account_available_after),
            "member_exposure_before": decimal_text(preview.member_exposure_before),
            "member_exposure_after": decimal_text(preview.member_exposure_after),
            "related_exposure_before": decimal_text(preview.related_exposure_before),
            "related_exposure_after": decimal_text(preview.related_exposure_after),
            "max_member_exposure": decimal_text(preview.max_member_exposure),
            "max_related_exposure": decimal_text(preview.max_related_exposure),
            "allowed": preview.allowed,
            "reason_code": preview.reason_code,
        }

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
        object_type: str,
        object_id: UUID,
        event_id: UUID,
        request_id: UUID | None,
    ) -> None:
        await AuditRepository(session).record(
            action=action,
            object_type=object_type,
            object_id=object_id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={"signed_event_id": str(event_id)},
        )
