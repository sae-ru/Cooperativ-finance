"""Deterministic final-compensation demo built through production commands."""

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.identity.infrastructure.models import (
    Member,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.risk.application.compensation import CompensationService
from cooperative_clearing.modules.risk.application.service import RiskService
from cooperative_clearing.modules.risk.domain.types import FaultClass as RiskFaultClass
from cooperative_clearing.modules.risk.domain.types import ShareContour
from cooperative_clearing.modules.risk.infrastructure.models import (
    CompensationTransfer,
    ExposureCommitment,
    LiabilityCase,
    RiskPolicy,
    ShareAccount,
)
from cooperative_clearing.modules.trust.application.service import TrustService
from cooperative_clearing.modules.trust.domain.types import (
    AppealOutcome,
    ConflictAssessment,
    DecisionOutcome,
    DecisionStage,
    FaultClass,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.security import PasswordService

DEMO_CASE_REFERENCE = "DEMO-COMPENSATION-APPEAL-001"
DEMO_INCIDENT_REFERENCE = "DEMO-COMPENSATION-INCIDENT-001"
DEMO_AMOUNT = Decimal("15")


async def seed_demo_compensation(session: AsyncSession, settings: Settings) -> None:
    cooperative_id = stable_id("cooperative", settings.node_code)
    principals = await _ensure_principals(session, cooperative_id)
    registrar = principals["registrar"]
    security = principals["security"]
    auditor = principals["auditor"]
    original_arbitrator = principals["original_arbitrator"]
    appeal_arbitrator = principals["appeal_arbitrator"]
    claimant = principals["claimant"]
    controller = principals["controller"]

    policy = await session.scalar(
        select(RiskPolicy).where(
            RiskPolicy.cooperative_id == cooperative_id,
            RiskPolicy.denomination == "DEMO_SHARE",
            RiskPolicy.status == "ACTIVE",
        )
    )
    source = await session.scalar(
        select(ShareAccount).where(
            ShareAccount.cooperative_id == cooperative_id,
            ShareAccount.member_id == stable_id("member", "demo-member-anna"),
            ShareAccount.contour == ShareContour.GUARANTEE.value,
        )
    )
    if policy is None or source is None:
        raise RuntimeError("bounded-risk demo must be seeded before compensation")
    commitment = await session.scalar(
        select(ExposureCommitment).where(
            ExposureCommitment.cooperative_id == cooperative_id,
            ExposureCommitment.account_id == source.id,
            ExposureCommitment.risk_type == "DEMO_DELIVERY",
        )
    )
    if commitment is None:
        raise RuntimeError("bounded-risk demo must be seeded before compensation")

    destination_evidence = await _evidence(
        session,
        settings,
        registrar,
        cooperative_id,
        "demo-compensation-destination-account-v1",
        "Opening entry for the claimant primary share account.",
    )
    incident_evidence = await _evidence(
        session,
        settings,
        auditor,
        cooperative_id,
        "demo-compensation-incident-v1",
        "Documented non-performance and the claimant's verified delivery loss.",
    )
    assessment_evidence = await _evidence(
        session,
        settings,
        security,
        cooperative_id,
        "demo-compensation-assessment-v1",
        "Independent causal assessment of the bounded delivery loss.",
    )
    case_evidence = await _evidence(
        session,
        settings,
        claimant,
        cooperative_id,
        "demo-compensation-case-v1",
        "Claimant request for a final human decision before any share transfer.",
    )
    original_evidence = await _evidence(
        session,
        settings,
        original_arbitrator,
        cooperative_id,
        "demo-compensation-original-decision-v1",
        "Original arbitration review of the liability assessment.",
    )
    appeal_evidence = await _evidence(
        session,
        settings,
        registrar,
        cooperative_id,
        "demo-compensation-appeal-v1",
        "Responsible participant request for an independent second review.",
    )
    appeal_decision_evidence = await _evidence(
        session,
        settings,
        appeal_arbitrator,
        cooperative_id,
        "demo-compensation-appeal-decision-v1",
        "Independent appeal review affirming the bounded established loss.",
    )
    authorization_evidence = await _evidence(
        session,
        settings,
        controller,
        cooperative_id,
        "demo-compensation-authorization-v1",
        "Independent authorization limited by the final decision and accepted reserve.",
    )

    risk = RiskService(settings)
    trust = TrustService(settings)
    compensation = CompensationService(settings)
    destination_result = await risk.open_account(
        session,
        principal=registrar,
        policy_id=policy.id,
        member_id=stable_id("member", "demo-member-ivan"),
        contour=ShareContour.PRIMARY,
        opening_balance=Decimal("5"),
        protected_amount=Decimal("0"),
        source_reference="DEMO-COMPENSATION-CLAIMANT-PRIMARY-V1",
        evidence_ids=[destination_evidence],
        idempotency_key="demo-compensation-destination-open-v1",
        request_id=None,
    )
    liability_result = await risk.open_liability_case(
        session,
        principal=auditor,
        commitment_id=commitment.id,
        incident_reference=DEMO_INCIDENT_REFERENCE,
        affected_amount=DEMO_AMOUNT,
        facts="The promised cabbage delivery was not completed.",
        causal_graph={
            "cause": "documented_non_performance",
            "effect": "verified_claimant_loss",
        },
        evidence_ids=[incident_evidence],
        idempotency_key="demo-compensation-liability-open-v1",
        request_id=None,
    )
    assessment_result = await risk.assess_liability_case(
        session,
        principal=security,
        case_id=liability_result.object_id,
        fault_class=RiskFaultClass.NEGLIGENCE,
        assessed_loss=DEMO_AMOUNT,
        rationale="The evidence confirms non-performance, causation, and the bounded loss.",
        appeal_until=datetime(2035, 2, 15, tzinfo=UTC),
        evidence_ids=[assessment_evidence],
        expected_version=1,
        idempotency_key="demo-compensation-liability-assess-v1",
        request_id=None,
    )
    trust_case_result = await trust.open_case(
        session,
        principal=claimant,
        cooperative_id=cooperative_id,
        case_reference=DEMO_CASE_REFERENCE,
        subject_member_id=stable_id("member", "demo-member-anna"),
        claimant_member_id=stable_id("member", "demo-member-ivan"),
        source_type="LIABILITY",
        source_reference=str(liability_result.object_id),
        source_event_ids=[assessment_result.event_id],
        evidence_ids=[case_evidence],
        summary="Compensation for a verified delivery loss",
        facts="The linked liability case contains an independent bounded assessment.",
        requested_outcome="Transfer fifteen demo shares only after final appeal review.",
        confidentiality="NORMAL",
        idempotency_key="demo-compensation-trust-case-open-v1",
        request_id=None,
    )
    await trust.record_response(
        session,
        principal=registrar,
        case_id=trust_case_result.object_id,
        expected_version=1,
        response_text="I request an independent appeal before any transfer is accepted.",
        evidence_ids=[],
        idempotency_key="demo-compensation-response-v1",
        request_id=None,
    )
    await trust.mark_case_ready(
        session,
        principal=auditor,
        case_id=trust_case_result.object_id,
        expected_version=2,
        review_note="The exact liability source, parties, and evidence are complete.",
        idempotency_key="demo-compensation-ready-v1",
        request_id=None,
    )
    await trust.declare_conflict(
        session,
        principal=original_arbitrator,
        case_id=trust_case_result.object_id,
        stage=DecisionStage.ORIGINAL,
        assessment=ConflictAssessment.CLEAR,
        relationship="No personal, economic, or operational relationship with either party.",
        rationale="Identity, role, and case participation were checked before review.",
        idempotency_key="demo-compensation-original-conflict-v1",
        request_id=None,
    )
    original_decision = await trust.issue_original_decision(
        session,
        principal=original_arbitrator,
        case_id=trust_case_result.object_id,
        expected_case_version=3,
        outcome=DecisionOutcome.SUBSTANTIATED,
        standard_of_proof="Balance of verified operational evidence",
        fault_class=FaultClass.NEGLIGENCE,
        causal_findings={"liability_case_id": str(liability_result.object_id)},
        established_loss=DEMO_AMOUNT,
        reasoning="The exact linked evidence supports the bounded established loss.",
        consequence_spec={"compensation_review": True, "automatic_execution": False},
        evidence_ids=[original_evidence],
        idempotency_key="demo-compensation-original-decision-v1",
        request_id=None,
    )
    appeal_result = await trust.submit_appeal(
        session,
        principal=registrar,
        case_id=trust_case_result.object_id,
        original_decision_id=original_decision.object_id,
        sanction_id=None,
        expected_case_version=4,
        grounds="Require a second independent review before the recipient can accept shares.",
        evidence_ids=[appeal_evidence],
        idempotency_key="demo-compensation-appeal-submit-v1",
        request_id=None,
    )
    await trust.declare_conflict(
        session,
        principal=appeal_arbitrator,
        case_id=trust_case_result.object_id,
        stage=DecisionStage.APPEAL,
        assessment=ConflictAssessment.CLEAR,
        relationship="No participation in the original arbitration or either party's activity.",
        rationale="The appeal reviewer is separate from the original decision maker.",
        idempotency_key="demo-compensation-appeal-conflict-v1",
        request_id=None,
    )
    appeal_decision = await trust.decide_appeal(
        session,
        principal=appeal_arbitrator,
        appeal_id=appeal_result.object_id,
        expected_case_version=5,
        outcome=AppealOutcome.AFFIRMED,
        standard_of_proof="Independent review of the complete signed record",
        causal_findings={"affirmed_liability_case_id": str(liability_result.object_id)},
        reasoning="The appeal confirms the causal link and bounded established loss.",
        consequence_spec={"compensation_final": True},
        evidence_ids=[appeal_decision_evidence],
        idempotency_key="demo-compensation-appeal-decision-v1",
        request_id=None,
    )
    transfer_result = await compensation.authorize(
        session,
        principal=controller,
        liability_case_id=liability_result.object_id,
        trust_case_id=trust_case_result.object_id,
        trust_decision_id=appeal_decision.object_id,
        destination_account_id=destination_result.object_id,
        amount=DEMO_AMOUNT,
        rationale="The affirmed decision permits only the verified bounded amount.",
        evidence_ids=[authorization_evidence],
        expected_liability_version=2,
        expected_source_account_version=1,
        expected_destination_account_version=1,
        expected_commitment_version=2,
        idempotency_key="demo-compensation-authorize-v1",
        request_id=None,
    )
    await session.flush()

    transfer = await session.get(CompensationTransfer, transfer_result.object_id)
    liability = await session.get(LiabilityCase, liability_result.object_id)
    source = await session.get(ShareAccount, commitment.account_id)
    destination = await session.get(ShareAccount, destination_result.object_id)
    if transfer is None or liability is None or source is None or destination is None:
        raise RuntimeError("demo compensation records were not created")
    pending_ok = (
        transfer.status == "PENDING_ACCEPTANCE"
        and liability.status == "ASSESSED"
        and source.balance == Decimal("100")
        and source.executed_not_settled == DEMO_AMOUNT
        and destination.balance == Decimal("5")
    )
    settled_ok = (
        transfer.status == "SETTLED"
        and liability.status == "CLOSED"
        and source.balance == Decimal("85")
        and source.executed_not_settled == Decimal("0")
        and destination.balance == Decimal("20")
    )
    if (
        source.protected_amount != Decimal("40")
        or transfer.amount != DEMO_AMOUNT
        or not (pending_ok or settled_ok)
    ):
        raise RuntimeError(
            "demo compensation flow is inconsistent: "
            f"status={transfer.status}, liability={liability.status}, "
            f"source={source.balance}, held={source.executed_not_settled}, "
            f"protected={source.protected_amount}, destination={destination.balance}"
        )


async def _ensure_principals(
    session: AsyncSession, cooperative_id: UUID
) -> dict[str, Principal]:
    controller_member_id = stable_id("member", "demo-member-compensation-controller")
    controller_user_id = stable_id("demo-user", "compensation-controller")
    member_statement = insert(Member).values(
        id=controller_member_id,
        display_name="Olga Compensation Controller",
        registered_by_cooperative_id=cooperative_id,
        status="ACTIVE",
    )
    await session.execute(
        member_statement.on_conflict_do_update(
            index_elements=[Member.id],
            set_={"status": "ACTIVE", "registered_by_cooperative_id": cooperative_id},
        )
    )
    membership_statement = insert(Membership).values(
        id=stable_id("membership", "demo-member-compensation-controller"),
        cooperative_id=cooperative_id,
        member_id=controller_member_id,
        member_number="D-0008",
        status="ACTIVE",
        joined_at=datetime.now(UTC),
    )
    await session.execute(
        membership_statement.on_conflict_do_update(
            index_elements=[Membership.id],
            set_={"status": "ACTIVE"},
        )
    )
    user_statement = insert(UserAccount).values(
        id=controller_user_id,
        login="demo-compensation-controller",
        password_hash=PasswordService().hash(str(uuid4())),
        member_id=controller_member_id,
        status="ACTIVE",
        must_change_password=True,
    )
    await session.execute(user_statement.on_conflict_do_nothing(index_elements=[UserAccount.id]))

    roles = (
        (
            "compensation-controller",
            controller_user_id,
            RoleCode.RISK_ADMIN,
            cooperative_id,
        ),
        (
            "auditor",
            stable_id("bootstrap-user", "auditor"),
            RoleCode.RISK_ADMIN,
            cooperative_id,
        ),
        (
            "auditor",
            stable_id("bootstrap-user", "auditor"),
            RoleCode.ARBITRATOR,
            None,
        ),
    )
    for login, user_id, role, scope in roles:
        role_statement = insert(RoleAssignment).values(
            id=stable_id("demo-role", f"{login}:{role.value}"),
            user_id=user_id,
            role_code=role.value,
            cooperative_id=scope,
            status="ACTIVE",
            granted_by_user_id=stable_id("bootstrap-user", "registrar"),
            approved_by_user_id=stable_id("bootstrap-user", "auditor"),
            approved_at=datetime.now(UTC),
        )
        await session.execute(
            role_statement.on_conflict_do_update(
                index_elements=[RoleAssignment.id],
                set_={"status": "ACTIVE", "cooperative_id": scope},
            )
        )
    await session.flush()

    return {
        "registrar": _principal(
            "registrar",
            stable_id("bootstrap-user", "registrar"),
            stable_id("member", "demo-member-anna"),
            (
                _grant(
                    "bootstrap-role",
                    "registrar:COOPERATIVE_ADMIN",
                    RoleCode.COOPERATIVE_ADMIN,
                    cooperative_id,
                ),
            ),
        ),
        "security": _principal(
            "security",
            stable_id("bootstrap-user", "security"),
            stable_id("member", "demo-member-elena"),
            (
                _grant(
                    "demo-role",
                    "security:RISK_ADMIN",
                    RoleCode.RISK_ADMIN,
                    cooperative_id,
                ),
                _grant("demo-role", "security:ARBITRATOR", RoleCode.ARBITRATOR, None),
            ),
        ),
        "auditor": _principal(
            "auditor",
            stable_id("bootstrap-user", "auditor"),
            stable_id("member", "demo-member-pavel"),
            (
                _grant("bootstrap-role", "auditor:AUDITOR", RoleCode.AUDITOR, None),
                _grant(
                    "demo-role",
                    "auditor:RISK_ADMIN",
                    RoleCode.RISK_ADMIN,
                    cooperative_id,
                ),
                _grant("demo-role", "auditor:ARBITRATOR", RoleCode.ARBITRATOR, None),
            ),
        ),
        "original_arbitrator": _principal(
            "demo-arbitrator",
            stable_id("demo-user", "nina-arbitrator"),
            stable_id("member", "demo-member-nina"),
            (
                _grant(
                    "demo-role",
                    "demo-arbitrator:ARBITRATOR",
                    RoleCode.ARBITRATOR,
                    None,
                ),
            ),
        ),
        "appeal_arbitrator": _principal(
            "auditor",
            stable_id("bootstrap-user", "auditor"),
            stable_id("member", "demo-member-pavel"),
            (
                _grant("demo-role", "auditor:ARBITRATOR", RoleCode.ARBITRATOR, None),
            ),
        ),
        "claimant": _principal(
            "farmer",
            stable_id("demo-user", "farmer"),
            stable_id("member", "demo-member-ivan"),
            (
                _grant(
                    "demo-role",
                    "farmer:EXCHANGE_PARTICIPANT",
                    RoleCode.EXCHANGE_PARTICIPANT,
                    cooperative_id,
                ),
            ),
        ),
        "controller": _principal(
            "demo-compensation-controller",
            controller_user_id,
            controller_member_id,
            (
                _grant(
                    "demo-role",
                    "compensation-controller:RISK_ADMIN",
                    RoleCode.RISK_ADMIN,
                    cooperative_id,
                ),
            ),
        ),
    }


def _grant(
    kind: str,
    value: str,
    role: RoleCode,
    cooperative_id: UUID | None,
) -> RoleGrant:
    return RoleGrant(stable_id(kind, value), role, cooperative_id)


def _principal(
    login: str,
    user_id: UUID,
    member_id: UUID,
    roles: tuple[RoleGrant, ...],
) -> Principal:
    return Principal(
        user_id=user_id,
        session_id=stable_id("demo-session", login),
        login=login,
        member_id=member_id,
        must_change_password=False,
        roles=roles,
    )


async def _evidence(
    session: AsyncSession,
    settings: Settings,
    principal: Principal,
    cooperative_id: UUID,
    key: str,
    text: str,
) -> UUID:
    content = text.encode("utf-8")
    service = EvidenceService(settings)
    intent = await service.create_intent(
        session,
        principal=principal,
        cooperative_id=cooperative_id,
        expected_sha256=hashlib.sha256(content).hexdigest(),
        expected_size=len(content),
        mime_type="text/plain",
        kind="TRUST_CASE",
        original_name=f"{key}.txt",
        access_scope="COOPERATIVE",
        retention_until=None,
        idempotency_key=f"{key}-intent",
        request_id=None,
    )

    async def chunks() -> AsyncIterator[bytes]:
        yield content

    await service.store_content(
        session,
        principal=principal,
        evidence_id=intent.object_id,
        chunks=chunks(),
        request_id=None,
    )
    return intent.object_id
