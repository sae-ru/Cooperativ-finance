"""Transactional lifecycle for voluntary aid without debt or reputation effects."""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import Member, Membership
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.journal.application.service import (
    ActorClaim,
    SignedJournalService,
)
from cooperative_clearing.modules.journal.domain.crypto import payload_hash
from cooperative_clearing.modules.solidarity.application.common import (
    SolidarityCommandResult,
    audit_solidarity_action,
    begin_solidarity_command,
    complete_solidarity_command,
    evidence_payload,
    link_evidence,
    solidarity_participant_actor,
    solidarity_role_actor,
)
from cooperative_clearing.modules.solidarity.domain.types import (
    AidBucket,
    BucketBalance,
    BucketEntry,
    ContributionForm,
    DeliveryAttestorKind,
    NeedCategory,
    PrivacyScope,
    ResidueRule,
    build_bucket_balances,
    exact_quantity,
    normalize_unit,
    solidarity_error,
)
from cooperative_clearing.modules.solidarity.infrastructure.models import (
    AidAllocation,
    AidApplication,
    AidCampaign,
    AidDelivery,
    AllocationApproval,
    CampaignReport,
    Contribution,
    Pledge,
    SolidarityComplaint,
    SolidarityFund,
)
from cooperative_clearing.shared.core.config import Settings

OPERATOR_ROLES = {RoleCode.SOLIDARITY_OPERATOR}
CONTROLLER_ROLES = {RoleCode.SOLIDARITY_CONTROLLER}
COMPLAINT_REVIEW_ROLES = {RoleCode.SOLIDARITY_CONTROLLER, RoleCode.AUDITOR}


class SolidarityService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.journal = SignedJournalService(settings)

    async def propose_fund(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        fund_code: str,
        name: str,
        purpose: str,
        residue_rule: ResidueRule,
        admin_expense_limit: Decimal,
        terms: dict[str, object],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SolidarityCommandResult:
        code = self._code(fund_code, "FUND_CODE_INVALID", 48)
        limit = Decimal(admin_expense_limit)
        if not limit.is_finite() or limit < 0 or limit > 1:
            raise solidarity_error("ADMIN_EXPENSE_LIMIT_INVALID", 422)
        command = {
            "cooperative_id": str(cooperative_id),
            "fund_code": code,
            "name": self._text(name, "FUND_NAME_INVALID", 200),
            "purpose": self._text(purpose, "FUND_PURPOSE_INVALID", 5_000),
            "residue_rule": residue_rule.value,
            "admin_expense_limit": str(limit),
            "terms": terms,
        }
        record, replay = await begin_solidarity_command(
            session, principal, "SOLIDARITY_PROPOSE_FUND", idempotency_key, command
        )
        if replay is not None:
            return replay
        actor = await solidarity_role_actor(session, principal, cooperative_id, OPERATOR_ROLES)
        await self._lock_cooperative(session, cooperative_id)
        if await session.scalar(
            select(SolidarityFund.id).where(
                SolidarityFund.cooperative_id == cooperative_id,
                SolidarityFund.fund_code == code,
            )
        ):
            raise solidarity_error("FUND_CODE_EXISTS")
        policy_version = (
            int(
                await session.scalar(
                    select(func.coalesce(func.max(SolidarityFund.policy_version), 0)).where(
                        SolidarityFund.cooperative_id == cooperative_id
                    )
                )
                or 0
            )
            + 1
        )
        terms_payload = {**command, "policy_version": policy_version, "no_debt": True}
        terms_hash = payload_hash(terms_payload)
        fund_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="solidarity.fund_proposed",
            aggregate_type="solidarity_fund",
            aggregate_id=fund_id,
            aggregate_version=1,
            actor=actor,
            payload={**terms_payload, "fund_id": str(fund_id), "terms_hash": terms_hash},
        )
        session.add(
            SolidarityFund(
                id=fund_id,
                cooperative_id=cooperative_id,
                fund_code=code,
                name=str(command["name"]),
                purpose=str(command["purpose"]),
                policy_version=policy_version,
                residue_rule=residue_rule.value,
                admin_expense_limit=limit,
                terms_payload=terms_payload,
                terms_hash=terms_hash,
                status="DRAFT",
                proposed_by_user_id=principal.user_id,
                proposed_by_member_id=actor.person_id,
                proposed_role_assignment_id=actor.role_assignment_id,
                proposed_event_id=event.event_id,
                version=1,
            )
        )
        await audit_solidarity_action(
            session,
            principal,
            cooperative_id,
            "SOLIDARITY_FUND_PROPOSED",
            "SolidarityFund",
            fund_id,
            event.event_id,
            request_id,
        )
        return complete_solidarity_command(record, event.event_id, fund_id)

    async def approve_fund(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        fund_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SolidarityCommandResult:
        fund = await self._fund(session, fund_id, lock=True)
        payload = {"fund_id": str(fund_id), "expected_version": expected_version}
        record, replay = await begin_solidarity_command(
            session, principal, "SOLIDARITY_APPROVE_FUND", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(fund.version, expected_version)
        if fund.status != "DRAFT":
            raise solidarity_error("FUND_NOT_DRAFT")
        actor = await solidarity_role_actor(
            session, principal, fund.cooperative_id, CONTROLLER_ROLES
        )
        if actor.person_id == fund.proposed_by_member_id:
            raise solidarity_error("INDEPENDENT_APPROVER_REQUIRED")
        event = await self.journal.append(
            session,
            event_type="solidarity.fund_approved",
            aggregate_type="solidarity_fund",
            aggregate_id=fund.id,
            aggregate_version=fund.version + 1,
            actor=actor,
            payload={**payload, "terms_hash": fund.terms_hash},
        )
        fund.status = "ACTIVE"
        fund.approved_by_user_id = principal.user_id
        fund.approved_by_member_id = actor.person_id
        fund.approved_role_assignment_id = actor.role_assignment_id
        fund.approved_event_id = event.event_id
        fund.approved_at = datetime.now(UTC)
        fund.version += 1
        await audit_solidarity_action(
            session,
            principal,
            fund.cooperative_id,
            "SOLIDARITY_FUND_APPROVED",
            "SolidarityFund",
            fund.id,
            event.event_id,
            request_id,
        )
        return complete_solidarity_command(record, event.event_id, fund.id)

    async def create_campaign(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        fund_id: UUID,
        campaign_code: str,
        title: str,
        public_purpose: str,
        eligibility_policy: dict[str, object],
        accepted_forms: Sequence[ContributionForm],
        starts_at: datetime,
        ends_at: datetime,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SolidarityCommandResult:
        fund = await self._fund(session, fund_id, lock=False)
        if fund.status != "ACTIVE":
            raise solidarity_error("ACTIVE_FUND_REQUIRED")
        code = self._code(campaign_code, "CAMPAIGN_CODE_INVALID", 64)
        start = self._utc(starts_at, "CAMPAIGN_PERIOD_INVALID")
        end = self._utc(ends_at, "CAMPAIGN_PERIOD_INVALID")
        if end <= start:
            raise solidarity_error("CAMPAIGN_PERIOD_INVALID", 422)
        forms = sorted({item.value for item in accepted_forms})
        if not forms:
            raise solidarity_error("ACCEPTED_FORM_REQUIRED", 422)
        command = {
            "fund_id": str(fund_id),
            "campaign_code": code,
            "title": self._text(title, "CAMPAIGN_TITLE_INVALID", 200),
            "public_purpose": self._text(public_purpose, "CAMPAIGN_PURPOSE_INVALID", 5_000),
            "eligibility_policy": eligibility_policy,
            "accepted_forms": forms,
            "starts_at": start.isoformat(),
            "ends_at": end.isoformat(),
            "residue_rule": fund.residue_rule,
            "fund_terms_hash": fund.terms_hash,
        }
        record, replay = await begin_solidarity_command(
            session, principal, "SOLIDARITY_CREATE_CAMPAIGN", idempotency_key, command
        )
        if replay is not None:
            return replay
        actor = await solidarity_role_actor(session, principal, fund.cooperative_id, OPERATOR_ROLES)
        await self._lock_cooperative(session, fund.cooperative_id)
        if await session.scalar(
            select(AidCampaign.id).where(
                AidCampaign.cooperative_id == fund.cooperative_id,
                AidCampaign.campaign_code == code,
            )
        ):
            raise solidarity_error("CAMPAIGN_CODE_EXISTS")
        terms_hash = payload_hash(command)
        campaign_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="solidarity.campaign_created",
            aggregate_type="aid_campaign",
            aggregate_id=campaign_id,
            aggregate_version=1,
            actor=actor,
            payload={**command, "campaign_id": str(campaign_id), "terms_hash": terms_hash},
        )
        session.add(
            AidCampaign(
                id=campaign_id,
                cooperative_id=fund.cooperative_id,
                fund_id=fund.id,
                campaign_code=code,
                title=str(command["title"]),
                public_purpose=str(command["public_purpose"]),
                eligibility_policy=eligibility_policy,
                accepted_forms=forms,
                starts_at=start,
                ends_at=end,
                residue_rule=fund.residue_rule,
                terms_payload=command,
                terms_hash=terms_hash,
                status="DRAFT",
                created_by_user_id=principal.user_id,
                created_by_member_id=actor.person_id,
                created_role_assignment_id=actor.role_assignment_id,
                created_event_id=event.event_id,
                version=1,
            )
        )
        await audit_solidarity_action(
            session,
            principal,
            fund.cooperative_id,
            "SOLIDARITY_CAMPAIGN_CREATED",
            "AidCampaign",
            campaign_id,
            event.event_id,
            request_id,
        )
        return complete_solidarity_command(record, event.event_id, campaign_id)

    async def open_campaign(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        campaign_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SolidarityCommandResult:
        campaign = await self._campaign(session, campaign_id, lock=True)
        payload = {"campaign_id": str(campaign_id), "expected_version": expected_version}
        record, replay = await begin_solidarity_command(
            session, principal, "SOLIDARITY_OPEN_CAMPAIGN", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(campaign.version, expected_version)
        if campaign.status != "DRAFT":
            raise solidarity_error("CAMPAIGN_NOT_DRAFT")
        actor = await solidarity_role_actor(
            session, principal, campaign.cooperative_id, CONTROLLER_ROLES
        )
        if actor.person_id == campaign.created_by_member_id:
            raise solidarity_error("INDEPENDENT_APPROVER_REQUIRED")
        event = await self.journal.append(
            session,
            event_type="solidarity.campaign_opened",
            aggregate_type="aid_campaign",
            aggregate_id=campaign.id,
            aggregate_version=campaign.version + 1,
            actor=actor,
            payload={**payload, "terms_hash": campaign.terms_hash},
        )
        campaign.status = "OPEN"
        campaign.opened_by_user_id = principal.user_id
        campaign.opened_by_member_id = actor.person_id
        campaign.opened_role_assignment_id = actor.role_assignment_id
        campaign.opened_event_id = event.event_id
        campaign.opened_at = datetime.now(UTC)
        campaign.version += 1
        await audit_solidarity_action(
            session,
            principal,
            campaign.cooperative_id,
            "SOLIDARITY_CAMPAIGN_OPENED",
            "AidCampaign",
            campaign.id,
            event.event_id,
            request_id,
        )
        return complete_solidarity_command(record, event.event_id, campaign.id)

    async def create_pledge(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        campaign_id: UUID,
        donor_member_id: UUID,
        contribution_form: ContributionForm,
        unit_code: str,
        quantity: Decimal,
        description: str,
        expires_at: datetime,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SolidarityCommandResult:
        campaign = await self._campaign(session, campaign_id, lock=False)
        self._require_open_campaign(campaign)
        qty = exact_quantity(quantity)
        unit = normalize_unit(unit_code)
        expiry = self._utc(expires_at, "PLEDGE_EXPIRY_INVALID")
        if expiry <= datetime.now(UTC) or expiry > campaign.ends_at:
            raise solidarity_error("PLEDGE_EXPIRY_INVALID", 422)
        payload = {
            "campaign_id": str(campaign.id),
            "donor_member_id": str(donor_member_id),
            "contribution_form": contribution_form.value,
            "unit_code": unit,
            "quantity": str(qty),
            "description": self._text(description, "PLEDGE_DESCRIPTION_INVALID", 2_000),
            "expires_at": expiry.isoformat(),
            "asset_recognized": False,
        }
        record, replay = await begin_solidarity_command(
            session, principal, "SOLIDARITY_CREATE_PLEDGE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        actor = await self._participant_or_operator(
            session, principal, campaign.cooperative_id, donor_member_id
        )
        await self._eligible_member(session, campaign.cooperative_id, donor_member_id)
        self._require_form(campaign, contribution_form)
        pledge_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="solidarity.pledge_recorded",
            aggregate_type="solidarity_pledge",
            aggregate_id=pledge_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "pledge_id": str(pledge_id)},
        )
        session.add(
            Pledge(
                id=pledge_id,
                campaign_id=campaign.id,
                donor_member_id=donor_member_id,
                contribution_form=contribution_form.value,
                unit_code=unit,
                quantity=qty,
                description=str(payload["description"]),
                status="ACTIVE",
                expires_at=expiry,
                created_by_user_id=principal.user_id,
                created_by_member_id=actor.person_id,
                created_role_assignment_id=actor.role_assignment_id,
                created_event_id=event.event_id,
                version=1,
            )
        )
        await audit_solidarity_action(
            session,
            principal,
            campaign.cooperative_id,
            "SOLIDARITY_PLEDGE_RECORDED",
            "Pledge",
            pledge_id,
            event.event_id,
            request_id,
        )
        return complete_solidarity_command(record, event.event_id, pledge_id)

    async def receive_contribution(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        campaign_id: UUID,
        pledge_id: UUID | None,
        donor_member_id: UUID,
        contribution_form: ContributionForm,
        unit_code: str,
        quantity: Decimal,
        description: str,
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SolidarityCommandResult:
        campaign = await self._campaign(session, campaign_id, lock=False)
        self._require_open_campaign(campaign)
        qty = exact_quantity(quantity)
        unit = normalize_unit(unit_code)
        payload = {
            "campaign_id": str(campaign.id),
            "pledge_id": str(pledge_id) if pledge_id else None,
            "donor_member_id": str(donor_member_id),
            "contribution_form": contribution_form.value,
            "unit_code": unit,
            "quantity": str(qty),
            "description": self._text(description, "CONTRIBUTION_DESCRIPTION_INVALID", 2_000),
            "evidence_ids": [str(value) for value in evidence_ids],
            "creates_debt": False,
            "affects_reputation": False,
        }
        record, replay = await begin_solidarity_command(
            session, principal, "SOLIDARITY_RECEIVE_CONTRIBUTION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        actor = await self._participant_or_operator(
            session, principal, campaign.cooperative_id, donor_member_id
        )
        await self._eligible_member(session, campaign.cooperative_id, donor_member_id)
        self._require_form(campaign, contribution_form)
        evidence = await EvidenceService.require_ready(
            session, campaign.cooperative_id, evidence_ids, required=True
        )
        pledge: Pledge | None = None
        if pledge_id is not None:
            pledge = await session.get(Pledge, pledge_id, with_for_update=True)
            if pledge is None or pledge.campaign_id != campaign.id:
                raise solidarity_error("PLEDGE_NOT_FOUND", 404)
            if pledge.status != "ACTIVE" or pledge.expires_at <= datetime.now(UTC):
                raise solidarity_error("PLEDGE_NOT_ACTIVE")
            if (
                pledge.donor_member_id != donor_member_id
                or pledge.contribution_form != contribution_form.value
                or pledge.unit_code != unit
                or pledge.quantity != qty
            ):
                raise solidarity_error("PLEDGE_TERMS_MISMATCH")
        contribution_id = uuid4()
        refs = evidence_payload(evidence)
        event = await self.journal.append(
            session,
            event_type="solidarity.contribution_received",
            aggregate_type="solidarity_contribution",
            aggregate_id=contribution_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "contribution_id": str(contribution_id), "evidence": refs},
        )
        session.add(
            Contribution(
                id=contribution_id,
                campaign_id=campaign.id,
                pledge_id=pledge_id,
                donor_member_id=donor_member_id,
                contribution_form=contribution_form.value,
                unit_code=unit,
                quantity=qty,
                description=str(payload["description"]),
                evidence_refs=refs,
                status="RECEIVED",
                received_by_user_id=principal.user_id,
                received_by_member_id=actor.person_id,
                received_role_assignment_id=actor.role_assignment_id,
                received_event_id=event.event_id,
                version=1,
            )
        )
        if pledge is not None:
            pledge.status = "FULFILLED"
            pledge.fulfilled_contribution_id = contribution_id
            pledge.version += 1
        link_evidence(session, evidence, event.event_id, "solidarity_contribution", contribution_id)
        await audit_solidarity_action(
            session,
            principal,
            campaign.cooperative_id,
            "SOLIDARITY_CONTRIBUTION_RECEIVED",
            "Contribution",
            contribution_id,
            event.event_id,
            request_id,
        )
        return complete_solidarity_command(record, event.event_id, contribution_id)

    async def verify_contribution(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        contribution_id: UUID,
        expected_version: int,
        accepted: bool,
        verification_note: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SolidarityCommandResult:
        contribution = await session.get(Contribution, contribution_id, with_for_update=True)
        if contribution is None:
            raise solidarity_error("CONTRIBUTION_NOT_FOUND", 404)
        campaign = await self._campaign(session, contribution.campaign_id, lock=False)
        payload = {
            "contribution_id": str(contribution.id),
            "expected_version": expected_version,
            "accepted": accepted,
            "verification_note": self._text(verification_note, "VERIFICATION_NOTE_INVALID", 5_000),
        }
        record, replay = await begin_solidarity_command(
            session, principal, "SOLIDARITY_VERIFY_CONTRIBUTION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(contribution.version, expected_version)
        if contribution.status != "RECEIVED":
            raise solidarity_error("CONTRIBUTION_NOT_RECEIVED")
        actor = await solidarity_role_actor(
            session, principal, campaign.cooperative_id, CONTROLLER_ROLES
        )
        if actor.person_id in {contribution.received_by_member_id, contribution.donor_member_id}:
            raise solidarity_error("INDEPENDENT_VERIFIER_REQUIRED")
        target = "VERIFIED" if accepted else "REJECTED"
        event = await self.journal.append(
            session,
            event_type=(
                "solidarity.contribution_verified"
                if accepted
                else "solidarity.contribution_rejected"
            ),
            aggregate_type="solidarity_contribution",
            aggregate_id=contribution.id,
            aggregate_version=contribution.version + 1,
            actor=actor,
            payload={
                **payload,
                "status": target,
                "bucket": {
                    "form": contribution.contribution_form,
                    "unit_code": contribution.unit_code,
                },
                "available_quantity_delta": str(contribution.quantity if accepted else Decimal(0)),
                "reputation_delta": None,
            },
        )
        contribution.status = target
        contribution.verified_by_user_id = principal.user_id
        contribution.verified_by_member_id = actor.person_id
        contribution.verified_role_assignment_id = actor.role_assignment_id
        contribution.verified_event_id = event.event_id
        contribution.verification_note = str(payload["verification_note"])
        contribution.verified_at = datetime.now(UTC)
        contribution.version += 1
        if not accepted and contribution.pledge_id is not None:
            pledge = await session.get(Pledge, contribution.pledge_id, with_for_update=True)
            if pledge is not None and pledge.fulfilled_contribution_id == contribution.id:
                pledge.status = "CANCELLED"
                pledge.fulfilled_contribution_id = None
                pledge.version += 1
        await audit_solidarity_action(
            session,
            principal,
            campaign.cooperative_id,
            f"SOLIDARITY_CONTRIBUTION_{target}",
            "Contribution",
            contribution.id,
            event.event_id,
            request_id,
        )
        return complete_solidarity_command(record, event.event_id, contribution.id)

    async def submit_application(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        campaign_id: UUID,
        recipient_member_id: UUID,
        need_category: NeedCategory,
        requested_form: ContributionForm,
        requested_unit_code: str,
        requested_quantity: Decimal,
        privacy_scope: PrivacyScope,
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SolidarityCommandResult:
        campaign = await self._campaign(session, campaign_id, lock=False)
        self._require_open_campaign(campaign)
        unit = normalize_unit(requested_unit_code)
        quantity = exact_quantity(requested_quantity)
        payload = {
            "campaign_id": str(campaign.id),
            "recipient_member_id": str(recipient_member_id),
            "need_category": need_category.value,
            "requested_form": requested_form.value,
            "requested_unit_code": unit,
            "requested_quantity": str(quantity),
            "privacy_scope": privacy_scope.value,
            "evidence_ids": [str(value) for value in evidence_ids],
            "creates_obligation": False,
        }
        record, replay = await begin_solidarity_command(
            session, principal, "SOLIDARITY_SUBMIT_APPLICATION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        actor = await self._participant_or_operator(
            session, principal, campaign.cooperative_id, recipient_member_id
        )
        await self._eligible_member(session, campaign.cooperative_id, recipient_member_id)
        self._require_form(campaign, requested_form)
        evidence = await EvidenceService.require_ready(
            session, campaign.cooperative_id, evidence_ids, required=False
        )
        refs = evidence_payload(evidence)
        application_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="solidarity.application_submitted",
            aggregate_type="aid_application",
            aggregate_id=application_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "application_id": str(application_id),
                "private_evidence_count": len(refs),
            },
        )
        session.add(
            AidApplication(
                id=application_id,
                campaign_id=campaign.id,
                recipient_member_id=recipient_member_id,
                need_category=need_category.value,
                requested_form=requested_form.value,
                requested_unit_code=unit,
                requested_quantity=quantity,
                privacy_scope=privacy_scope.value,
                private_evidence_refs=refs,
                status="SUBMITTED",
                submitted_by_user_id=principal.user_id,
                submitted_by_member_id=actor.person_id,
                submitted_role_assignment_id=actor.role_assignment_id,
                submitted_event_id=event.event_id,
                version=1,
            )
        )
        link_evidence(session, evidence, event.event_id, "aid_application", application_id)
        await audit_solidarity_action(
            session,
            principal,
            campaign.cooperative_id,
            "SOLIDARITY_APPLICATION_SUBMITTED",
            "AidApplication",
            application_id,
            event.event_id,
            request_id,
        )
        return complete_solidarity_command(record, event.event_id, application_id)

    async def review_application(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        application_id: UUID,
        expected_version: int,
        eligible: bool,
        eligibility_note: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SolidarityCommandResult:
        application = await session.get(AidApplication, application_id, with_for_update=True)
        if application is None:
            raise solidarity_error("APPLICATION_NOT_FOUND", 404)
        campaign = await self._campaign(session, application.campaign_id, lock=False)
        payload = {
            "application_id": str(application.id),
            "expected_version": expected_version,
            "eligible": eligible,
            "eligibility_note": self._text(eligibility_note, "ELIGIBILITY_NOTE_INVALID", 5_000),
        }
        record, replay = await begin_solidarity_command(
            session, principal, "SOLIDARITY_REVIEW_APPLICATION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(application.version, expected_version)
        if application.status != "SUBMITTED":
            raise solidarity_error("APPLICATION_NOT_SUBMITTED")
        actor = await solidarity_role_actor(
            session, principal, campaign.cooperative_id, CONTROLLER_ROLES
        )
        if actor.person_id in {
            application.submitted_by_member_id,
            application.recipient_member_id,
        }:
            raise solidarity_error("INDEPENDENT_REVIEWER_REQUIRED")
        target = "ELIGIBLE" if eligible else "REJECTED"
        event = await self.journal.append(
            session,
            event_type=(
                "solidarity.application_eligible" if eligible else "solidarity.application_rejected"
            ),
            aggregate_type="aid_application",
            aggregate_id=application.id,
            aggregate_version=application.version + 1,
            actor=actor,
            payload={**payload, "status": target, "policy_terms_hash": campaign.terms_hash},
        )
        application.status = target
        application.reviewed_by_user_id = principal.user_id
        application.reviewed_by_member_id = actor.person_id
        application.reviewed_role_assignment_id = actor.role_assignment_id
        application.reviewed_event_id = event.event_id
        application.eligibility_note = str(payload["eligibility_note"])
        application.reviewed_at = datetime.now(UTC)
        application.version += 1
        await audit_solidarity_action(
            session,
            principal,
            campaign.cooperative_id,
            f"SOLIDARITY_APPLICATION_{target}",
            "AidApplication",
            application.id,
            event.event_id,
            request_id,
        )
        return complete_solidarity_command(record, event.event_id, application.id)

    async def propose_allocation(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        application_id: UUID,
        quantity: Decimal,
        public_summary: str,
        rationale: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SolidarityCommandResult:
        application = await session.get(AidApplication, application_id, with_for_update=True)
        if application is None:
            raise solidarity_error("APPLICATION_NOT_FOUND", 404)
        campaign = await self._campaign(session, application.campaign_id, lock=False)
        self._require_open_campaign(campaign)
        qty = exact_quantity(quantity)
        if qty > application.requested_quantity:
            raise solidarity_error("ALLOCATION_EXCEEDS_REQUEST")
        command = {
            "application_id": str(application.id),
            "campaign_id": str(campaign.id),
            "recipient_member_id": str(application.recipient_member_id),
            "contribution_form": application.requested_form,
            "unit_code": application.requested_unit_code,
            "quantity": str(qty),
            "public_summary": self._text(public_summary, "PUBLIC_SUMMARY_INVALID", 240),
            "rationale": self._text(rationale, "ALLOCATION_RATIONALE_INVALID", 5_000),
            "policy_terms_hash": campaign.terms_hash,
            "creates_debt": False,
            "creates_right": False,
        }
        record, replay = await begin_solidarity_command(
            session, principal, "SOLIDARITY_PROPOSE_ALLOCATION", idempotency_key, command
        )
        if replay is not None:
            return replay
        if application.status != "ELIGIBLE":
            raise solidarity_error("APPLICATION_NOT_ELIGIBLE")
        actor = await solidarity_role_actor(
            session, principal, campaign.cooperative_id, OPERATOR_ROLES
        )
        if actor.person_id == application.recipient_member_id:
            raise solidarity_error("ALLOCATION_SELF_DEALING_DENIED")
        await self._lock_cooperative(session, campaign.cooperative_id)
        balance = await self._bucket_balance(
            session,
            campaign.id,
            ContributionForm(application.requested_form),
            application.requested_unit_code,
        )
        if balance.available < qty:
            raise solidarity_error("ALLOCATION_EXCEEDS_VERIFIED_BALANCE")
        allocation_hash = payload_hash(command)
        allocation_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="solidarity.allocation_proposed",
            aggregate_type="aid_allocation",
            aggregate_id=allocation_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **command,
                "allocation_id": str(allocation_id),
                "allocation_hash": allocation_hash,
                "available_before": str(balance.available),
            },
        )
        session.add(
            AidAllocation(
                id=allocation_id,
                campaign_id=campaign.id,
                application_id=application.id,
                recipient_member_id=application.recipient_member_id,
                contribution_form=application.requested_form,
                unit_code=application.requested_unit_code,
                quantity=qty,
                public_summary=str(command["public_summary"]),
                rationale=str(command["rationale"]),
                policy_terms_hash=campaign.terms_hash,
                allocation_hash=allocation_hash,
                status="PROPOSED",
                proposed_by_user_id=principal.user_id,
                proposed_by_member_id=actor.person_id,
                proposed_role_assignment_id=actor.role_assignment_id,
                proposed_event_id=event.event_id,
                version=1,
            )
        )
        await audit_solidarity_action(
            session,
            principal,
            campaign.cooperative_id,
            "SOLIDARITY_ALLOCATION_PROPOSED",
            "AidAllocation",
            allocation_id,
            event.event_id,
            request_id,
        )
        return complete_solidarity_command(record, event.event_id, allocation_id)

    async def approve_allocation(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        allocation_id: UUID,
        expected_version: int,
        allocation_hash: str,
        approved: bool,
        conflict_statement: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SolidarityCommandResult:
        allocation = await session.get(AidAllocation, allocation_id, with_for_update=True)
        if allocation is None:
            raise solidarity_error("ALLOCATION_NOT_FOUND", 404)
        campaign = await self._campaign(session, allocation.campaign_id, lock=False)
        payload = {
            "allocation_id": str(allocation.id),
            "expected_version": expected_version,
            "allocation_hash": allocation_hash,
            "approved": approved,
            "conflict_statement": self._text(
                conflict_statement, "CONFLICT_STATEMENT_INVALID", 5_000
            ),
        }
        record, replay = await begin_solidarity_command(
            session, principal, "SOLIDARITY_APPROVE_ALLOCATION", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(allocation.version, expected_version)
        if allocation.status != "PROPOSED":
            raise solidarity_error("ALLOCATION_NOT_PROPOSED")
        if allocation_hash != allocation.allocation_hash:
            raise solidarity_error("ALLOCATION_HASH_MISMATCH")
        actor = await solidarity_role_actor(
            session, principal, campaign.cooperative_id, CONTROLLER_ROLES
        )
        if actor.person_id in {allocation.proposed_by_member_id, allocation.recipient_member_id}:
            raise solidarity_error("INDEPENDENT_APPROVER_REQUIRED")
        await self._lock_cooperative(session, campaign.cooperative_id)
        if approved:
            balance = await self._bucket_balance(
                session,
                campaign.id,
                ContributionForm(allocation.contribution_form),
                allocation.unit_code,
            )
            if balance.available < allocation.quantity:
                raise solidarity_error("ALLOCATION_EXCEEDS_VERIFIED_BALANCE")
        decision = "APPROVED" if approved else "REJECTED"
        approval_id = uuid4()
        event = await self.journal.append(
            session,
            event_type=(
                "solidarity.allocation_approved" if approved else "solidarity.allocation_rejected"
            ),
            aggregate_type="aid_allocation",
            aggregate_id=allocation.id,
            aggregate_version=allocation.version + 1,
            actor=actor,
            payload={**payload, "approval_id": str(approval_id), "decision": decision},
        )
        session.add(
            AllocationApproval(
                id=approval_id,
                allocation_id=allocation.id,
                decision=decision,
                allocation_hash=allocation.allocation_hash,
                conflict_statement=str(payload["conflict_statement"]),
                decided_by_user_id=principal.user_id,
                decided_by_member_id=actor.person_id,
                decided_role_assignment_id=actor.role_assignment_id,
                decided_event_id=event.event_id,
            )
        )
        allocation.status = "APPROVED" if approved else "CANCELLED"
        allocation.version += 1
        application = await session.get(
            AidApplication, allocation.application_id, with_for_update=True
        )
        if application is None:
            raise solidarity_error("APPLICATION_NOT_FOUND", 404)
        if approved:
            application.status = "ALLOCATED"
            application.version += 1
        await audit_solidarity_action(
            session,
            principal,
            campaign.cooperative_id,
            f"SOLIDARITY_ALLOCATION_{decision}",
            "AidAllocation",
            allocation.id,
            event.event_id,
            request_id,
        )
        return complete_solidarity_command(record, event.event_id, allocation.id)

    async def record_delivery(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        allocation_id: UUID,
        expected_version: int,
        attestor_kind: DeliveryAttestorKind,
        acknowledgement: str,
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SolidarityCommandResult:
        allocation = await session.get(AidAllocation, allocation_id, with_for_update=True)
        if allocation is None:
            raise solidarity_error("ALLOCATION_NOT_FOUND", 404)
        campaign = await self._campaign(session, allocation.campaign_id, lock=False)
        payload = {
            "allocation_id": str(allocation.id),
            "expected_version": expected_version,
            "attestor_kind": attestor_kind.value,
            "acknowledgement": self._text(
                acknowledgement, "DELIVERY_ACKNOWLEDGEMENT_INVALID", 5_000
            ),
            "evidence_ids": [str(value) for value in evidence_ids],
            "creates_recipient_debt": False,
        }
        record, replay = await begin_solidarity_command(
            session, principal, "SOLIDARITY_RECORD_DELIVERY", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(allocation.version, expected_version)
        if allocation.status != "APPROVED":
            raise solidarity_error("ALLOCATION_NOT_APPROVED")
        actor = await solidarity_participant_actor(session, principal, campaign.cooperative_id)
        approval = await session.scalar(
            select(AllocationApproval).where(
                AllocationApproval.allocation_id == allocation.id,
                AllocationApproval.decision == "APPROVED",
            )
        )
        if approval is None:
            raise solidarity_error("ALLOCATION_APPROVAL_REQUIRED")
        if attestor_kind is DeliveryAttestorKind.RECIPIENT:
            if actor.person_id != allocation.recipient_member_id:
                raise solidarity_error("RECIPIENT_ATTESTOR_REQUIRED", 403)
        elif actor.person_id in {
            allocation.recipient_member_id,
            allocation.proposed_by_member_id,
            approval.decided_by_member_id,
        }:
            raise solidarity_error("INDEPENDENT_DELIVERY_ATTESTOR_REQUIRED", 403)
        evidence = await EvidenceService.require_ready(
            session, campaign.cooperative_id, evidence_ids, required=True
        )
        refs = evidence_payload(evidence)
        delivery_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="solidarity.aid_delivered",
            aggregate_type="aid_allocation",
            aggregate_id=allocation.id,
            aggregate_version=allocation.version + 1,
            actor=actor,
            payload={
                **payload,
                "delivery_id": str(delivery_id),
                "recipient_member_id": str(allocation.recipient_member_id),
                "evidence": refs,
            },
        )
        session.add(
            AidDelivery(
                id=delivery_id,
                allocation_id=allocation.id,
                recipient_member_id=allocation.recipient_member_id,
                attestor_kind=attestor_kind.value,
                attested_by_user_id=principal.user_id,
                attested_by_member_id=actor.person_id,
                attested_role_assignment_id=actor.role_assignment_id,
                evidence_refs=refs,
                acknowledgement=str(payload["acknowledgement"]),
                delivered_event_id=event.event_id,
            )
        )
        allocation.status = "DELIVERED"
        allocation.version += 1
        application = await session.get(
            AidApplication, allocation.application_id, with_for_update=True
        )
        if application is None:
            raise solidarity_error("APPLICATION_NOT_FOUND", 404)
        application.status = "CLOSED"
        application.version += 1
        link_evidence(session, evidence, event.event_id, "aid_delivery", delivery_id)
        await audit_solidarity_action(
            session,
            principal,
            campaign.cooperative_id,
            "SOLIDARITY_AID_DELIVERED",
            "AidDelivery",
            delivery_id,
            event.event_id,
            request_id,
        )
        return complete_solidarity_command(record, event.event_id, delivery_id)

    async def open_complaint(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        campaign_id: UUID,
        allocation_id: UUID | None,
        contribution_id: UUID | None,
        category: str,
        summary: str,
        privacy_scope: PrivacyScope,
        evidence_ids: Sequence[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SolidarityCommandResult:
        campaign = await self._campaign(session, campaign_id, lock=False)
        category_code = self._choice(
            category,
            {"ELIGIBILITY", "ALLOCATION", "DELIVERY", "CONTRIBUTION", "PRIVACY", "OTHER"},
            "COMPLAINT_CATEGORY_INVALID",
        )
        payload = {
            "campaign_id": str(campaign.id),
            "allocation_id": str(allocation_id) if allocation_id else None,
            "contribution_id": str(contribution_id) if contribution_id else None,
            "category": category_code,
            "summary": self._text(summary, "COMPLAINT_SUMMARY_INVALID", 240),
            "privacy_scope": privacy_scope.value,
            "evidence_ids": [str(value) for value in evidence_ids],
        }
        record, replay = await begin_solidarity_command(
            session, principal, "SOLIDARITY_OPEN_COMPLAINT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        actor = await solidarity_participant_actor(session, principal, campaign.cooperative_id)
        allocation: AidAllocation | None = None
        if allocation_id is not None:
            allocation = await session.get(AidAllocation, allocation_id, with_for_update=True)
            if allocation is None or allocation.campaign_id != campaign.id:
                raise solidarity_error("ALLOCATION_NOT_FOUND", 404)
        if contribution_id is not None:
            contribution = await session.get(Contribution, contribution_id)
            if contribution is None or contribution.campaign_id != campaign.id:
                raise solidarity_error("CONTRIBUTION_NOT_FOUND", 404)
        evidence = await EvidenceService.require_ready(
            session, campaign.cooperative_id, evidence_ids, required=False
        )
        refs = evidence_payload(evidence)
        complaint_id = uuid4()
        suspended = allocation is not None and allocation.status == "APPROVED"
        event = await self.journal.append(
            session,
            event_type="solidarity.complaint_opened",
            aggregate_type="solidarity_complaint",
            aggregate_id=complaint_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "complaint_id": str(complaint_id),
                "complainant_member_id": str(actor.person_id),
                "allocation_suspended": suspended,
                "evidence": refs,
            },
        )
        session.add(
            SolidarityComplaint(
                id=complaint_id,
                campaign_id=campaign.id,
                allocation_id=allocation_id,
                contribution_id=contribution_id,
                complainant_member_id=actor.person_id,
                category=category_code,
                summary=str(payload["summary"]),
                privacy_scope=privacy_scope.value,
                evidence_refs=refs,
                status="OPEN",
                opened_by_user_id=principal.user_id,
                opened_role_assignment_id=actor.role_assignment_id,
                opened_event_id=event.event_id,
                version=1,
            )
        )
        if suspended and allocation is not None:
            allocation.status = "SUSPENDED"
            allocation.version += 1
        link_evidence(session, evidence, event.event_id, "solidarity_complaint", complaint_id)
        await audit_solidarity_action(
            session,
            principal,
            campaign.cooperative_id,
            "SOLIDARITY_COMPLAINT_OPENED",
            "SolidarityComplaint",
            complaint_id,
            event.event_id,
            request_id,
        )
        return complete_solidarity_command(record, event.event_id, complaint_id)

    async def resolve_complaint(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        complaint_id: UUID,
        expected_version: int,
        accepted: bool,
        resolution_action: str,
        resolution_note: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SolidarityCommandResult:
        complaint = await session.get(SolidarityComplaint, complaint_id, with_for_update=True)
        if complaint is None:
            raise solidarity_error("COMPLAINT_NOT_FOUND", 404)
        campaign = await self._campaign(session, complaint.campaign_id, lock=False)
        action = self._choice(
            resolution_action,
            {"RESTORE_ALLOCATION", "CANCEL_ALLOCATION", "NOTE_ONLY"},
            "COMPLAINT_RESOLUTION_INVALID",
        )
        payload = {
            "complaint_id": str(complaint.id),
            "expected_version": expected_version,
            "accepted": accepted,
            "resolution_action": action,
            "resolution_note": self._text(
                resolution_note, "COMPLAINT_RESOLUTION_NOTE_INVALID", 5_000
            ),
        }
        record, replay = await begin_solidarity_command(
            session, principal, "SOLIDARITY_RESOLVE_COMPLAINT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(complaint.version, expected_version)
        if complaint.status != "OPEN":
            raise solidarity_error("COMPLAINT_NOT_OPEN")
        actor = await solidarity_role_actor(
            session, principal, campaign.cooperative_id, COMPLAINT_REVIEW_ROLES
        )
        if actor.person_id == complaint.complainant_member_id:
            raise solidarity_error("INDEPENDENT_COMPLAINT_REVIEWER_REQUIRED")
        allocation: AidAllocation | None = None
        if complaint.allocation_id is not None:
            allocation = await session.get(
                AidAllocation, complaint.allocation_id, with_for_update=True
            )
            if allocation is None:
                raise solidarity_error("ALLOCATION_NOT_FOUND", 404)
            approval = await session.scalar(
                select(AllocationApproval).where(AllocationApproval.allocation_id == allocation.id)
            )
            excluded = {allocation.proposed_by_member_id, allocation.recipient_member_id}
            if approval is not None:
                excluded.add(approval.decided_by_member_id)
            if actor.person_id in excluded:
                raise solidarity_error("INDEPENDENT_COMPLAINT_REVIEWER_REQUIRED")
        if not accepted and action != "NOTE_ONLY":
            raise solidarity_error("COMPLAINT_RESOLUTION_INVALID", 422)
        if action in {"RESTORE_ALLOCATION", "CANCEL_ALLOCATION"} and allocation is None:
            raise solidarity_error("COMPLAINT_ALLOCATION_REQUIRED", 422)
        if allocation is not None and accepted:
            if action == "RESTORE_ALLOCATION":
                if allocation.status != "SUSPENDED":
                    raise solidarity_error("ALLOCATION_NOT_SUSPENDED")
                allocation.status = "APPROVED"
                allocation.version += 1
            elif action == "CANCEL_ALLOCATION":
                if allocation.status not in {"PROPOSED", "APPROVED", "SUSPENDED"}:
                    raise solidarity_error("ALLOCATION_NOT_CANCELLABLE")
                allocation.status = "CANCELLED"
                allocation.version += 1
                application = await session.get(
                    AidApplication, allocation.application_id, with_for_update=True
                )
                if application is not None and application.status == "ALLOCATED":
                    application.status = "ELIGIBLE"
                    application.version += 1
        event = await self.journal.append(
            session,
            event_type=(
                "solidarity.complaint_resolved" if accepted else "solidarity.complaint_rejected"
            ),
            aggregate_type="solidarity_complaint",
            aggregate_id=complaint.id,
            aggregate_version=complaint.version + 1,
            actor=actor,
            payload=payload,
        )
        complaint.status = "RESOLVED" if accepted else "REJECTED"
        complaint.resolved_by_user_id = principal.user_id
        complaint.resolved_by_member_id = actor.person_id
        complaint.resolved_role_assignment_id = actor.role_assignment_id
        complaint.resolved_event_id = event.event_id
        complaint.resolution_action = action
        complaint.resolution_note = str(payload["resolution_note"])
        complaint.resolved_at = datetime.now(UTC)
        complaint.version += 1
        await audit_solidarity_action(
            session,
            principal,
            campaign.cooperative_id,
            "SOLIDARITY_COMPLAINT_RESOLVED",
            "SolidarityComplaint",
            complaint.id,
            event.event_id,
            request_id,
        )
        return complete_solidarity_command(record, event.event_id, complaint.id)

    async def close_campaign(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        campaign_id: UUID,
        expected_version: int,
        reconciliation_note: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SolidarityCommandResult:
        campaign = await self._campaign(session, campaign_id, lock=True)
        payload = {
            "campaign_id": str(campaign.id),
            "expected_version": expected_version,
            "reconciliation_note": self._text(
                reconciliation_note, "RECONCILIATION_NOTE_INVALID", 5_000
            ),
        }
        record, replay = await begin_solidarity_command(
            session, principal, "SOLIDARITY_CLOSE_CAMPAIGN", idempotency_key, payload
        )
        if replay is not None:
            return replay
        self._version(campaign.version, expected_version)
        if campaign.status != "OPEN":
            raise solidarity_error("CAMPAIGN_NOT_OPEN")
        actor = await solidarity_role_actor(
            session, principal, campaign.cooperative_id, CONTROLLER_ROLES
        )
        if actor.person_id == campaign.created_by_member_id:
            raise solidarity_error("INDEPENDENT_APPROVER_REQUIRED")
        await self._lock_cooperative(session, campaign.cooperative_id)
        open_allocations = int(
            await session.scalar(
                select(func.count())
                .select_from(AidAllocation)
                .where(
                    AidAllocation.campaign_id == campaign.id,
                    AidAllocation.status.in_({"PROPOSED", "APPROVED", "SUSPENDED"}),
                )
            )
            or 0
        )
        open_complaints = int(
            await session.scalar(
                select(func.count())
                .select_from(SolidarityComplaint)
                .where(
                    SolidarityComplaint.campaign_id == campaign.id,
                    SolidarityComplaint.status == "OPEN",
                )
            )
            or 0
        )
        unverified_contributions = int(
            await session.scalar(
                select(func.count())
                .select_from(Contribution)
                .where(
                    Contribution.campaign_id == campaign.id,
                    Contribution.status == "RECEIVED",
                )
            )
            or 0
        )
        pending_applications = int(
            await session.scalar(
                select(func.count())
                .select_from(AidApplication)
                .where(
                    AidApplication.campaign_id == campaign.id,
                    AidApplication.status.in_({"SUBMITTED", "ELIGIBLE", "ALLOCATED"}),
                )
            )
            or 0
        )
        if open_allocations or open_complaints or unverified_contributions or pending_applications:
            raise solidarity_error(
                "CAMPAIGN_RECONCILIATION_INCOMPLETE",
                409,
            )
        balances = await self.campaign_balances(session, campaign.id)
        if campaign.residue_rule != ResidueRule.RETAIN_IN_FUND.value and any(
            item.available != 0 for item in balances
        ):
            raise solidarity_error("RESIDUE_RECONCILIATION_REQUIRED")
        counts = await self._report_counts(session, campaign.id)
        bucket_totals = [
            {
                "contribution_form": item.bucket.contribution_form.value,
                "unit_code": item.bucket.unit_code,
                "verified": str(item.verified),
                "delivered": str(item.reserved_or_delivered),
                "residue": str(item.available),
            }
            for item in balances
        ]
        responsibility = [
            {
                "role": "SOLIDARITY_OPERATOR",
                "member_id": str(campaign.created_by_member_id),
                "role_assignment_id": str(campaign.created_role_assignment_id),
            },
            {
                "role": "SOLIDARITY_CONTROLLER",
                "member_id": str(actor.person_id),
                "role_assignment_id": str(actor.role_assignment_id),
            },
        ]
        report_id = uuid4()
        report_payload = {
            **payload,
            "report_id": str(report_id),
            "campaign_code": campaign.campaign_code,
            "bucket_totals": bucket_totals,
            **counts,
            "residue_rule": campaign.residue_rule,
            "responsibility": responsibility,
            "contains_recipient_identity": False,
        }
        report_hash = payload_hash(report_payload)
        event = await self.journal.append(
            session,
            event_type="solidarity.campaign_closed",
            aggregate_type="aid_campaign",
            aggregate_id=campaign.id,
            aggregate_version=campaign.version + 1,
            actor=actor,
            payload={**report_payload, "report_hash": report_hash},
        )
        session.add(
            CampaignReport(
                id=report_id,
                campaign_id=campaign.id,
                cooperative_id=campaign.cooperative_id,
                bucket_totals=bucket_totals,
                contribution_count=counts["contribution_count"],
                allocation_count=counts["allocation_count"],
                delivery_count=counts["delivery_count"],
                complaint_count=counts["complaint_count"],
                residue_rule=campaign.residue_rule,
                responsibility_snapshot=responsibility,
                report_hash=report_hash,
                generated_by_user_id=principal.user_id,
                generated_by_member_id=actor.person_id,
                generated_role_assignment_id=actor.role_assignment_id,
                generated_event_id=event.event_id,
            )
        )
        campaign.status = "CLOSED"
        campaign.closed_by_user_id = principal.user_id
        campaign.closed_by_member_id = actor.person_id
        campaign.closed_role_assignment_id = actor.role_assignment_id
        campaign.closed_event_id = event.event_id
        campaign.closed_at = datetime.now(UTC)
        campaign.version += 1
        await audit_solidarity_action(
            session,
            principal,
            campaign.cooperative_id,
            "SOLIDARITY_CAMPAIGN_CLOSED",
            "AidCampaign",
            campaign.id,
            event.event_id,
            request_id,
        )
        return complete_solidarity_command(record, event.event_id, report_id)

    async def campaign_balances(
        self, session: AsyncSession, campaign_id: UUID
    ) -> tuple[BucketBalance, ...]:
        contribution_rows = (
            await session.execute(
                select(
                    Contribution.contribution_form,
                    Contribution.unit_code,
                    Contribution.quantity,
                ).where(
                    Contribution.campaign_id == campaign_id,
                    Contribution.status == "VERIFIED",
                )
            )
        ).all()
        allocation_rows = (
            await session.execute(
                select(
                    AidAllocation.contribution_form,
                    AidAllocation.unit_code,
                    AidAllocation.quantity,
                ).where(
                    AidAllocation.campaign_id == campaign_id,
                    AidAllocation.status.in_({"APPROVED", "SUSPENDED", "DELIVERED"}),
                )
            )
        ).all()
        contributions = [
            BucketEntry(AidBucket(ContributionForm(form), unit), quantity)
            for form, unit, quantity in contribution_rows
        ]
        allocations = [
            BucketEntry(AidBucket(ContributionForm(form), unit), quantity)
            for form, unit, quantity in allocation_rows
        ]
        return build_bucket_balances(contributions, allocations)

    async def _bucket_balance(
        self,
        session: AsyncSession,
        campaign_id: UUID,
        contribution_form: ContributionForm,
        unit_code: str,
    ) -> BucketBalance:
        bucket = AidBucket(contribution_form, unit_code)
        balances = await self.campaign_balances(session, campaign_id)
        return next(
            (item for item in balances if item.bucket == bucket),
            BucketBalance(bucket, Decimal(0), Decimal(0), Decimal(0)),
        )

    @staticmethod
    async def _report_counts(session: AsyncSession, campaign_id: UUID) -> dict[str, int]:
        contribution_count = await session.scalar(
            select(func.count())
            .select_from(Contribution)
            .where(Contribution.campaign_id == campaign_id)
        )
        allocation_count = await session.scalar(
            select(func.count())
            .select_from(AidAllocation)
            .where(AidAllocation.campaign_id == campaign_id)
        )
        delivery_count = await session.scalar(
            select(func.count())
            .select_from(AidDelivery)
            .join(AidAllocation, AidAllocation.id == AidDelivery.allocation_id)
            .where(AidAllocation.campaign_id == campaign_id)
        )
        complaint_count = await session.scalar(
            select(func.count())
            .select_from(SolidarityComplaint)
            .where(SolidarityComplaint.campaign_id == campaign_id)
        )
        return {
            "contribution_count": int(contribution_count or 0),
            "allocation_count": int(allocation_count or 0),
            "delivery_count": int(delivery_count or 0),
            "complaint_count": int(complaint_count or 0),
        }

    async def _participant_or_operator(
        self,
        session: AsyncSession,
        principal: Principal,
        cooperative_id: UUID,
        represented_member_id: UUID,
    ) -> ActorClaim:
        if principal.member_id == represented_member_id:
            return await solidarity_participant_actor(session, principal, cooperative_id)
        return await solidarity_role_actor(session, principal, cooperative_id, OPERATOR_ROLES)

    @staticmethod
    async def _fund(session: AsyncSession, fund_id: UUID, *, lock: bool) -> SolidarityFund:
        fund = await session.get(SolidarityFund, fund_id, with_for_update=lock)
        if fund is None:
            raise solidarity_error("FUND_NOT_FOUND", 404)
        return fund

    @staticmethod
    async def _campaign(session: AsyncSession, campaign_id: UUID, *, lock: bool) -> AidCampaign:
        campaign = await session.get(AidCampaign, campaign_id, with_for_update=lock)
        if campaign is None:
            raise solidarity_error("CAMPAIGN_NOT_FOUND", 404)
        return campaign

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
            raise solidarity_error("MEMBER_NOT_ELIGIBLE", 422)

    @staticmethod
    async def _lock_cooperative(session: AsyncSession, cooperative_id: UUID) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"cooperative-clearing:solidarity:{cooperative_id}"},
        )

    @staticmethod
    def _require_open_campaign(campaign: AidCampaign) -> None:
        now = datetime.now(UTC)
        if campaign.status != "OPEN" or now < campaign.starts_at or now > campaign.ends_at:
            raise solidarity_error("CAMPAIGN_NOT_OPEN")

    @staticmethod
    def _require_form(campaign: AidCampaign, contribution_form: ContributionForm) -> None:
        if contribution_form.value not in campaign.accepted_forms:
            raise solidarity_error("CONTRIBUTION_FORM_NOT_ACCEPTED", 422)

    @staticmethod
    def _version(current: int, expected: int) -> None:
        if current != expected:
            raise solidarity_error("VERSION_CONFLICT")

    @staticmethod
    def _text(value: str, code: str, maximum: int) -> str:
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > maximum:
            raise solidarity_error(code, 422)
        return normalized

    @classmethod
    def _code(cls, value: str, code: str, maximum: int) -> str:
        normalized = cls._text(value, code, maximum).upper()
        if not normalized.isascii() or not all(
            character.isalnum() or character in {"_", "-", "."} for character in normalized
        ):
            raise solidarity_error(code, 422)
        return normalized

    @staticmethod
    def _choice(value: str, allowed: set[str], code: str) -> str:
        normalized = value.strip().upper()
        if normalized not in allowed:
            raise solidarity_error(code, 422)
        return normalized

    @staticmethod
    def _utc(value: datetime, code: str) -> datetime:
        if value.utcoffset() is None:
            raise solidarity_error(code, 422)
        return value.astimezone(UTC)
