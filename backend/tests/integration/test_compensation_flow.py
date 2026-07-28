"""Final arbitration can settle bounded compensation without touching protected shares."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from cooperative_clearing.api.dependencies import get_principal
from cooperative_clearing.cli import initialize_node
from cooperative_clearing.main import create_app
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.risk.application.compensation import CompensationService
from cooperative_clearing.modules.risk.application.service import RiskService
from cooperative_clearing.modules.risk.domain.types import (
    CommitmentType,
    ShareContour,
)
from cooperative_clearing.modules.risk.domain.types import (
    FaultClass as RiskFaultClass,
)
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
from cooperative_clearing.modules.trust.infrastructure.models import TrustPolicy
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database
from tests.integration.test_inventory_flow import create_actors, evidence
from tests.integration.test_risk_flow import grant_role


async def _grant_compensation_roles(
    database: Database,
    people: dict[str, Principal],
    cooperative_id: UUID,
) -> None:
    grants = (
        ("admin", RoleCode.COOPERATIVE_ADMIN),
        ("admin", RoleCode.RISK_ADMIN),
        ("owner", RoleCode.DATA_STEWARD),
        ("controller", RoleCode.DATA_STEWARD),
        ("custodian_a", RoleCode.ARBITRATOR),
        ("custodian_a", RoleCode.RISK_ADMIN),
        ("custodian_b", RoleCode.ARBITRATOR),
    )
    for name, role in grants:
        await grant_role(database, people, name, role, cooperative_id)


@pytest.mark.integration
async def test_affirmed_appeal_settles_bounded_share_compensation() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"compensation-integration-{suffix}",
        blob_root=Path(f"/tmp/compensation-{suffix}"),
    )
    await initialize_node(settings)
    database = Database.from_settings(settings)
    cooperative_id, people, members = await create_actors(database)
    risk_service = RiskService(settings)
    trust_service = TrustService(settings)
    compensation_service = CompensationService(settings)
    try:
        await _grant_compensation_roles(database, people, cooperative_id)
        risk_proposal_evidence = await evidence(
            database,
            settings,
            people["admin"],
            cooperative_id,
            b"bounded compensation policy proposal",
            "compensation-risk-policy.txt",
        )
        risk_approval_evidence = await evidence(
            database,
            settings,
            people["risk"],
            cooperative_id,
            b"independent bounded compensation policy approval",
            "compensation-risk-approval.txt",
        )
        account_evidence = await evidence(
            database,
            settings,
            people["admin"],
            cooperative_id,
            b"verified share register",
            "compensation-share-register.txt",
        )
        incident_evidence = await evidence(
            database,
            settings,
            people["risk"],
            cooperative_id,
            b"documented delivery loss",
            "compensation-incident.txt",
        )
        assessment_evidence = await evidence(
            database,
            settings,
            people["auditor"],
            cooperative_id,
            b"independent loss assessment",
            "compensation-assessment.txt",
        )
        arbitration_evidence = await evidence(
            database,
            settings,
            people["controller"],
            cooperative_id,
            b"arbitration evidence pack",
            "compensation-arbitration.txt",
        )
        authorization_evidence = await evidence(
            database,
            settings,
            people["admin"],
            cooperative_id,
            b"final decision and bounded payout authorization",
            "compensation-authorization.txt",
        )

        async with database.session() as session:
            risk_policy_result = await risk_service.propose_policy(
                session,
                principal=people["admin"],
                cooperative_id=cooperative_id,
                denomination=f"COMP-{suffix}",
                max_member_exposure=Decimal("100"),
                max_related_exposure=Decimal("150"),
                max_guarantee_chain_depth=3,
                protected_amount_rule="Protected shares cannot fund compensation.",
                related_party_rule="Related exposure is aggregated.",
                approval_reference=f"COMP-BOARD-{suffix}",
                evidence_ids=[risk_proposal_evidence],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            risk_policy = await session.get(RiskPolicy, risk_policy_result.object_id)
            assert risk_policy is not None
            await risk_service.approve_policy(
                session,
                principal=people["risk"],
                policy_id=risk_policy.id,
                terms_hash=risk_policy.terms_hash,
                expected_version=risk_policy.version,
                evidence_ids=[risk_approval_evidence],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            source_result = await risk_service.open_account(
                session,
                principal=people["admin"],
                policy_id=risk_policy_result.object_id,
                member_id=members["owner"],
                contour=ShareContour.GUARANTEE,
                opening_balance=Decimal("100"),
                protected_amount=Decimal("40"),
                source_reference=f"COMP-SOURCE-{suffix}",
                evidence_ids=[account_evidence],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            destination_result = await risk_service.open_account(
                session,
                principal=people["admin"],
                policy_id=risk_policy_result.object_id,
                member_id=members["controller"],
                contour=ShareContour.PRIMARY,
                opening_balance=Decimal("5"),
                protected_amount=Decimal("0"),
                source_reference=f"COMP-DESTINATION-{suffix}",
                evidence_ids=[account_evidence],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        now = datetime.now(UTC)
        async with database.session() as session:
            commitment_result = await risk_service.propose_commitment(
                session,
                principal=people["risk"],
                account_id=source_result.object_id,
                policy_id=risk_policy_result.object_id,
                commitment_type=CommitmentType.DIRECT_OBLIGATION,
                risk_type="DELIVERY_COMPENSATION",
                risk_id=uuid4(),
                debtor_member_id=members["owner"],
                beneficiary_member_id=members["controller"],
                role_assignment_id=None,
                amount_reserved=Decimal("30"),
                max_loss=Decimal("30"),
                coverage_ratio=Decimal("1"),
                starts_at=now,
                expires_at=now + timedelta(days=30),
                release_condition="Final arbitration or verified completion.",
                trigger_conditions="Documented non-performance.",
                exclusions="Protected amount and force majeure.",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            commitment = await session.get(ExposureCommitment, commitment_result.object_id)
            assert commitment is not None
            await risk_service.accept_commitment(
                session,
                principal=people["owner"],
                commitment_id=commitment.id,
                terms_hash=commitment.terms_hash,
                expected_version=commitment.version,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            liability_result = await risk_service.open_liability_case(
                session,
                principal=people["risk"],
                commitment_id=commitment_result.object_id,
                incident_reference=f"COMP-INCIDENT-{suffix}",
                affected_amount=Decimal("15"),
                facts="The promised delivery was not completed.",
                causal_graph={"cause": "non-performance", "effect": "verified loss"},
                evidence_ids=[incident_evidence],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            liability = await session.get(LiabilityCase, liability_result.object_id)
            assert liability is not None
            assessment_result = await risk_service.assess_liability_case(
                session,
                principal=people["auditor"],
                case_id=liability.id,
                fault_class=RiskFaultClass.NEGLIGENCE,
                assessed_loss=Decimal("15"),
                rationale="Evidence confirms the loss and causal connection.",
                appeal_until=now + timedelta(days=2),
                evidence_ids=[assessment_evidence],
                expected_version=liability.version,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            trust_policy_result = await trust_service.propose_policy(
                session,
                principal=people["admin"],
                cooperative_id=cooperative_id,
                semantic_version="COMP-1.0.0",
                appeal_window_seconds=86_400,
                max_protective_seconds=604_800,
                panel_quorum=1,
                terms={"compensation": "Only final human decisions can move bounded shares."},
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            trust_policy = await session.get(TrustPolicy, trust_policy_result.object_id)
            assert trust_policy is not None
            await trust_service.approve_policy(
                session,
                principal=people["auditor"],
                policy_id=trust_policy.id,
                expected_version=trust_policy.version,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            trust_case_result = await trust_service.open_case(
                session,
                principal=people["controller"],
                cooperative_id=cooperative_id,
                case_reference=f"COMP-TRUST-{suffix}",
                subject_member_id=members["owner"],
                claimant_member_id=members["controller"],
                source_type="LIABILITY",
                source_reference=str(liability_result.object_id),
                source_event_ids=[assessment_result.event_id],
                evidence_ids=[arbitration_evidence],
                summary="Compensation for verified delivery loss",
                facts="The liability case contains a bounded independent assessment.",
                requested_outcome="Compensate fifteen units from the accepted guarantee contour.",
                confidentiality="NORMAL",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            await trust_service.record_response(
                session,
                principal=people["owner"],
                case_id=trust_case_result.object_id,
                expected_version=1,
                response_text="I request independent review of the assessment.",
                evidence_ids=[],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            await trust_service.mark_case_ready(
                session,
                principal=people["auditor"],
                case_id=trust_case_result.object_id,
                expected_version=2,
                review_note="The parties and evidence are complete.",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            await trust_service.declare_conflict(
                session,
                principal=people["custodian_a"],
                case_id=trust_case_result.object_id,
                stage=DecisionStage.ORIGINAL,
                assessment=ConflictAssessment.CLEAR,
                relationship="No relationship",
                rationale="No personal or economic conflict was identified.",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            original_decision = await trust_service.issue_original_decision(
                session,
                principal=people["custodian_a"],
                case_id=trust_case_result.object_id,
                expected_case_version=3,
                outcome=DecisionOutcome.SUBSTANTIATED,
                standard_of_proof="Balance of documented evidence",
                fault_class=FaultClass.NEGLIGENCE,
                causal_findings={"liability_case_id": str(liability_result.object_id)},
                established_loss=Decimal("15"),
                reasoning="The bounded assessment is supported by the evidence.",
                consequence_spec={"compensation_review": True},
                evidence_ids=[arbitration_evidence],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            source = await session.get(ShareAccount, source_result.object_id)
            destination = await session.get(ShareAccount, destination_result.object_id)
            commitment = await session.get(ExposureCommitment, commitment_result.object_id)
            liability = await session.get(LiabilityCase, liability_result.object_id)
            assert source and destination and commitment and liability
            with pytest.raises(DomainError, match="RISK_COMPENSATION_DECISION_NOT_FINAL"):
                await compensation_service.authorize(
                    session,
                    principal=people["admin"],
                    liability_case_id=liability.id,
                    trust_case_id=trust_case_result.object_id,
                    trust_decision_id=original_decision.object_id,
                    destination_account_id=destination.id,
                    amount=Decimal("15"),
                    rationale="Authorize the final bounded compensation.",
                    evidence_ids=[authorization_evidence],
                    expected_liability_version=liability.version,
                    expected_source_account_version=source.version,
                    expected_destination_account_version=destination.version,
                    expected_commitment_version=commitment.version,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            await session.rollback()

        async with database.session() as session:
            appeal_result = await trust_service.submit_appeal(
                session,
                principal=people["owner"],
                case_id=trust_case_result.object_id,
                original_decision_id=original_decision.object_id,
                sanction_id=None,
                expected_case_version=4,
                grounds="Request an independent second review before payment.",
                evidence_ids=[arbitration_evidence],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            await trust_service.declare_conflict(
                session,
                principal=people["custodian_b"],
                case_id=trust_case_result.object_id,
                stage=DecisionStage.APPEAL,
                assessment=ConflictAssessment.CLEAR,
                relationship="No relationship",
                rationale="The appeal panel is independent from the original decision.",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            appeal_decision = await trust_service.decide_appeal(
                session,
                principal=people["custodian_b"],
                appeal_id=appeal_result.object_id,
                expected_case_version=5,
                outcome=AppealOutcome.AFFIRMED,
                standard_of_proof="Independent review of the complete record",
                causal_findings={"affirmed_liability_case_id": str(liability_result.object_id)},
                reasoning="The appeal confirms the original bounded loss.",
                consequence_spec={"compensation_final": True},
                evidence_ids=[arbitration_evidence],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            source = await session.get(ShareAccount, source_result.object_id)
            destination = await session.get(ShareAccount, destination_result.object_id)
            commitment = await session.get(ExposureCommitment, commitment_result.object_id)
            liability = await session.get(LiabilityCase, liability_result.object_id)
            assert source and destination and commitment and liability
            with pytest.raises(
                DomainError, match="RISK_COMPENSATION_AUTHORIZER_NOT_INDEPENDENT"
            ):
                await compensation_service.authorize(
                    session,
                    principal=people["custodian_a"],
                    liability_case_id=liability.id,
                    trust_case_id=trust_case_result.object_id,
                    trust_decision_id=appeal_decision.object_id,
                    destination_account_id=destination.id,
                    amount=Decimal("15"),
                    rationale="The original arbitrator cannot authorize the transfer.",
                    evidence_ids=[authorization_evidence],
                    expected_liability_version=liability.version,
                    expected_source_account_version=source.version,
                    expected_destination_account_version=destination.version,
                    expected_commitment_version=commitment.version,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            await session.rollback()

        async with database.session() as session:
            source = await session.get(ShareAccount, source_result.object_id)
            destination = await session.get(ShareAccount, destination_result.object_id)
            commitment = await session.get(ExposureCommitment, commitment_result.object_id)
            liability = await session.get(LiabilityCase, liability_result.object_id)
            assert source and destination and commitment and liability
            transfer_result = await compensation_service.authorize(
                session,
                principal=people["admin"],
                liability_case_id=liability.id,
                trust_case_id=trust_case_result.object_id,
                trust_decision_id=appeal_decision.object_id,
                destination_account_id=destination.id,
                amount=Decimal("15"),
                rationale="The affirmed decision authorizes only the bounded loss.",
                evidence_ids=[authorization_evidence],
                expected_liability_version=liability.version,
                expected_source_account_version=source.version,
                expected_destination_account_version=destination.version,
                expected_commitment_version=commitment.version,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            transfer = await session.get(CompensationTransfer, transfer_result.object_id)
            source = await session.get(ShareAccount, source_result.object_id)
            commitment = await session.get(ExposureCommitment, commitment_result.object_id)
            assert transfer and source and commitment
            assert transfer.status == "PENDING_ACCEPTANCE"
            assert source.balance == Decimal("100")
            assert source.protected_amount == Decimal("40")
            assert source.executed_not_settled == Decimal("15")
            assert commitment.executed_amount == Decimal("15")
            transfer_version = transfer.version

        async def accept_once(attempt: str) -> str:
            async with database.session() as session:
                try:
                    await compensation_service.accept(
                        session,
                        principal=people["controller"],
                        transfer_id=transfer_result.object_id,
                        expected_version=transfer_version,
                        idempotency_key=f"{suffix}-concurrent-accept-{attempt}",
                        request_id=uuid4(),
                    )
                    await session.commit()
                    return "SETTLED"
                except DomainError as exc:
                    await session.rollback()
                    return exc.code

        acceptance_outcomes = await asyncio.gather(accept_once("a"), accept_once("b"))
        assert acceptance_outcomes.count("SETTLED") == 1
        assert acceptance_outcomes.count("RISK_VERSION_CONFLICT") == 1

        app = create_app(settings, manage_runtime=False)
        app.state.database = database

        async def as_recipient() -> Principal:
            return people["controller"]

        app.dependency_overrides[get_principal] = as_recipient
        with TestClient(app) as client:
            visible = client.get("/api/v1/risk/compensations")
            assert visible.status_code == 200
            assert [item["id"] for item in visible.json()["data"]] == [
                str(transfer_result.object_id)
            ]
            stale = client.post(
                f"/api/v1/risk/compensations/{transfer_result.object_id}/acceptance",
                headers={"Idempotency-Key": str(uuid4())},
                json={"expected_version": transfer_version},
            )
            assert stale.status_code == 409
            assert stale.json()["error"]["code"] == "RISK_VERSION_CONFLICT"

        async with database.session() as session:
            transfer = await session.get(CompensationTransfer, transfer_result.object_id)
            source = await session.get(ShareAccount, source_result.object_id)
            destination = await session.get(ShareAccount, destination_result.object_id)
            liability = await session.get(LiabilityCase, liability_result.object_id)
            assert transfer and source and destination and liability
            assert transfer.status == "SETTLED"
            assert transfer.source_balance_before == Decimal("100")
            assert transfer.source_balance_after == Decimal("85")
            assert transfer.destination_balance_before == Decimal("5")
            assert transfer.destination_balance_after == Decimal("20")
            assert source.balance == Decimal("85")
            assert source.protected_amount == Decimal("40")
            assert source.executed_not_settled == Decimal("0")
            assert destination.balance == Decimal("20")
            assert liability.status == "CLOSED"
    finally:
        await database.dispose()
