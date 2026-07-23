import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from cooperative_clearing.api.dependencies import get_principal
from cooperative_clearing.cli import initialize_node
from cooperative_clearing.main import create_app
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import RoleAssignment
from cooperative_clearing.modules.journal.application.service import verify_journal
from cooperative_clearing.modules.node.infrastructure.models import NodeProfile
from cooperative_clearing.modules.risk.application.service import RiskService
from cooperative_clearing.modules.risk.domain.types import (
    CommitmentType,
    FaultClass,
    ShareContour,
)
from cooperative_clearing.modules.risk.infrastructure.models import (
    ExposureCommitment,
    LiabilityCase,
    RelatedPartyLink,
    RiskPolicy,
    ShareAccount,
    ShareContribution,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database
from tests.integration.test_exchange_flow import add_grant
from tests.integration.test_inventory_flow import create_actors, evidence


async def grant_role(
    database: Database,
    people: dict[str, Principal],
    name: str,
    role: RoleCode,
    cooperative_id: UUID,
) -> None:
    assignment_id = uuid4()
    async with database.session() as session:
        session.add(
            RoleAssignment(
                id=assignment_id,
                user_id=people[name].user_id,
                role_code=role.value,
                cooperative_id=cooperative_id,
                status="ACTIVE",
                granted_by_user_id=None,
                approved_by_user_id=None,
            )
        )
        await session.commit()
    people[name] = add_grant(people[name], assignment_id, role, cooperative_id)


@pytest.mark.integration
async def test_bounded_risk_flow_is_personal_serialized_and_auditable() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"risk-integration-{suffix}",
        blob_root=Path(f"/tmp/risk-{suffix}"),
    )
    await initialize_node(settings)
    database = Database.from_settings(settings)
    cooperative_id, people, members = await create_actors(database)
    service = RiskService(settings)
    try:
        await grant_role(database, people, "admin", RoleCode.COOPERATIVE_ADMIN, cooperative_id)
        await grant_role(database, people, "admin", RoleCode.RISK_ADMIN, cooperative_id)
        for name in ("owner", "custodian_a", "custodian_b"):
            await grant_role(database, people, name, RoleCode.DATA_STEWARD, cooperative_id)

        policy_proposal_evidence = await evidence(
            database,
            settings,
            people["admin"],
            cooperative_id,
            b"risk policy proposal",
            "risk-policy-proposal.txt",
        )
        policy_approval_evidence = await evidence(
            database,
            settings,
            people["risk"],
            cooperative_id,
            b"independent risk policy approval",
            "risk-policy-approval.txt",
        )
        account_evidence = await evidence(
            database,
            settings,
            people["admin"],
            cooperative_id,
            b"share contribution register",
            "share-register.txt",
        )

        async with database.session() as session:
            proposed_policy = await service.propose_policy(
                session,
                principal=people["admin"],
                cooperative_id=cooperative_id,
                denomination=f"RISK-{suffix}",
                max_member_exposure=Decimal("50"),
                max_related_exposure=Decimal("70"),
                max_guarantee_chain_depth=3,
                protected_amount_rule="The protected amount is never automatically reserved.",
                related_party_rule="Active related parties share one exposure ceiling.",
                approval_reference=f"BOARD-{suffix}",
                evidence_ids=[policy_proposal_evidence],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            policy = await session.get(RiskPolicy, proposed_policy.object_id)
            assert policy is not None and policy.status == "PROPOSED"
            policy_hash = policy.terms_hash

        async with database.session() as session:
            with pytest.raises(DomainError, match="RISK_POLICY_APPROVER_NOT_INDEPENDENT"):
                await service.approve_policy(
                    session,
                    principal=people["admin"],
                    policy_id=proposed_policy.object_id,
                    terms_hash=policy_hash,
                    expected_version=1,
                    evidence_ids=[policy_approval_evidence],
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            await session.rollback()

        approval_key = str(uuid4())
        async with database.session() as session:
            approved_policy = await service.approve_policy(
                session,
                principal=people["risk"],
                policy_id=proposed_policy.object_id,
                terms_hash=policy_hash,
                expected_version=1,
                evidence_ids=[policy_approval_evidence],
                idempotency_key=approval_key,
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            replayed = await service.approve_policy(
                session,
                principal=people["risk"],
                policy_id=proposed_policy.object_id,
                terms_hash=policy_hash,
                expected_version=1,
                evidence_ids=[policy_approval_evidence],
                idempotency_key=approval_key,
                request_id=uuid4(),
            )
            assert replayed.replayed and replayed.event_id == approved_policy.event_id
            await session.rollback()

        account_ids: dict[str, UUID] = {}
        for name, balance, protected in (
            ("owner", "100", "20"),
            ("custodian_a", "100", "20"),
            ("custodian_b", "50", "0"),
        ):
            async with database.session() as session:
                opened = await service.open_account(
                    session,
                    principal=people["admin"],
                    policy_id=proposed_policy.object_id,
                    member_id=members[name],
                    contour=ShareContour.GUARANTEE,
                    opening_balance=Decimal(balance),
                    protected_amount=Decimal(protected),
                    source_reference=f"REGISTER-{name.upper()}-{suffix}",
                    evidence_ids=[account_evidence],
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
                await session.commit()
                account_ids[name] = opened.object_id

        now = datetime.now(UTC)
        async with database.session() as session:
            proposed_commitment = await service.propose_commitment(
                session,
                principal=people["risk"],
                account_id=account_ids["owner"],
                policy_id=proposed_policy.object_id,
                commitment_type=CommitmentType.DIRECT_OBLIGATION,
                risk_type="DELIVERY_OBLIGATION",
                risk_id=uuid4(),
                debtor_member_id=members["owner"],
                beneficiary_member_id=None,
                role_assignment_id=None,
                amount_reserved=Decimal("40"),
                max_loss=Decimal("40"),
                coverage_ratio=Decimal("1"),
                starts_at=now,
                expires_at=now + timedelta(days=30),
                release_condition="Verified completion or independent resolution.",
                trigger_conditions="Documented non-performance after the due date.",
                exclusions="Force majeure and excluded protected shares.",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            commitment = await session.get(ExposureCommitment, proposed_commitment.object_id)
            assert commitment is not None
            terms_hash = commitment.terms_hash
        async with database.session() as session:
            accepted_commitment = await service.accept_commitment(
                session,
                principal=people["owner"],
                commitment_id=proposed_commitment.object_id,
                terms_hash=terms_hash,
                expected_version=1,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            with pytest.raises(DomainError, match="RISK_MEMBER_EXPOSURE_LIMIT_EXCEEDED"):
                await service.propose_commitment(
                    session,
                    principal=people["risk"],
                    account_id=account_ids["owner"],
                    policy_id=proposed_policy.object_id,
                    commitment_type=CommitmentType.DIRECT_OBLIGATION,
                    risk_type="SECOND_DIRECT_RISK",
                    risk_id=uuid4(),
                    debtor_member_id=members["owner"],
                    beneficiary_member_id=None,
                    role_assignment_id=None,
                    amount_reserved=Decimal("15"),
                    max_loss=Decimal("15"),
                    coverage_ratio=Decimal("1"),
                    starts_at=now,
                    expires_at=now + timedelta(days=30),
                    release_condition="Independent release.",
                    trigger_conditions="Documented default.",
                    exclusions="Protected shares.",
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            await session.rollback()

        relation_evidence = await evidence(
            database,
            settings,
            people["risk"],
            cooperative_id,
            b"related party declaration",
            "related-declaration.txt",
        )
        relation_decision_evidence = await evidence(
            database,
            settings,
            people["auditor"],
            cooperative_id,
            b"related party independent review",
            "related-review.txt",
        )
        async with database.session() as session:
            proposed_link = await service.propose_related_link(
                session,
                principal=people["risk"],
                cooperative_id=cooperative_id,
                member_a_id=members["owner"],
                member_b_id=members["custodian_a"],
                relation_type="RELATED",
                source_statement="The members share control over the same delivery resource.",
                evidence_ids=[relation_evidence],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            await service.decide_related_link(
                session,
                principal=people["auditor"],
                link_id=proposed_link.object_id,
                approve=True,
                decision_notes="Independent evidence confirms common control.",
                evidence_ids=[relation_decision_evidence],
                expected_version=1,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            with pytest.raises(DomainError, match="RISK_RELATED_EXPOSURE_LIMIT_EXCEEDED"):
                await service.propose_commitment(
                    session,
                    principal=people["risk"],
                    account_id=account_ids["custodian_a"],
                    policy_id=proposed_policy.object_id,
                    commitment_type=CommitmentType.DIRECT_OBLIGATION,
                    risk_type="RELATED_GROUP_RISK",
                    risk_id=uuid4(),
                    debtor_member_id=members["custodian_a"],
                    beneficiary_member_id=None,
                    role_assignment_id=None,
                    amount_reserved=Decimal("35"),
                    max_loss=Decimal("35"),
                    coverage_ratio=Decimal("1"),
                    starts_at=now,
                    expires_at=now + timedelta(days=30),
                    release_condition="Independent release.",
                    trigger_conditions="Documented default.",
                    exclusions="Protected shares.",
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            await session.rollback()

        async with database.session() as session:
            proposed_guarantee = await service.propose_commitment(
                session,
                principal=people["risk"],
                account_id=account_ids["custodian_a"],
                policy_id=proposed_policy.object_id,
                commitment_type=CommitmentType.GUARANTEE,
                risk_type="GUARANTEE_EDGE_A",
                risk_id=uuid4(),
                debtor_member_id=members["owner"],
                beneficiary_member_id=members["controller"],
                role_assignment_id=None,
                amount_reserved=Decimal("10"),
                max_loss=Decimal("10"),
                coverage_ratio=Decimal("1"),
                starts_at=now,
                expires_at=now + timedelta(days=30),
                release_condition="Independent release.",
                trigger_conditions="Documented debtor default.",
                exclusions="Protected shares.",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            guarantee = await session.get(ExposureCommitment, proposed_guarantee.object_id)
            assert guarantee is not None
            guarantee_hash = guarantee.terms_hash
        async with database.session() as session:
            await service.accept_commitment(
                session,
                principal=people["custodian_a"],
                commitment_id=proposed_guarantee.object_id,
                terms_hash=guarantee_hash,
                expected_version=1,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            with pytest.raises(DomainError, match="RISK_GUARANTEE_CYCLE_DETECTED"):
                await service.propose_commitment(
                    session,
                    principal=people["risk"],
                    account_id=account_ids["owner"],
                    policy_id=proposed_policy.object_id,
                    commitment_type=CommitmentType.GUARANTEE,
                    risk_type="GUARANTEE_EDGE_B",
                    risk_id=uuid4(),
                    debtor_member_id=members["custodian_a"],
                    beneficiary_member_id=members["controller"],
                    role_assignment_id=None,
                    amount_reserved=Decimal("10"),
                    max_loss=Decimal("10"),
                    coverage_ratio=Decimal("1"),
                    starts_at=now,
                    expires_at=now + timedelta(days=30),
                    release_condition="Independent release.",
                    trigger_conditions="Documented debtor default.",
                    exclusions="Protected shares.",
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            await session.rollback()

        concurrent_ids: list[UUID] = []
        for risk_type in ("CONCURRENT_A", "CONCURRENT_B"):
            async with database.session() as session:
                proposed = await service.propose_commitment(
                    session,
                    principal=people["risk"],
                    account_id=account_ids["custodian_b"],
                    policy_id=proposed_policy.object_id,
                    commitment_type=CommitmentType.DIRECT_OBLIGATION,
                    risk_type=risk_type,
                    risk_id=uuid4(),
                    debtor_member_id=members["custodian_b"],
                    beneficiary_member_id=None,
                    role_assignment_id=None,
                    amount_reserved=Decimal("30"),
                    max_loss=Decimal("30"),
                    coverage_ratio=Decimal("1"),
                    starts_at=now,
                    expires_at=now + timedelta(days=30),
                    release_condition="Independent release.",
                    trigger_conditions="Documented default.",
                    exclusions="Protected shares.",
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
                await session.commit()
                concurrent_ids.append(proposed.object_id)

        async with database.session() as session:
            hashes = {
                item.id: item.terms_hash
                for item in (
                    await session.execute(
                        select(ExposureCommitment).where(ExposureCommitment.id.in_(concurrent_ids))
                    )
                ).scalars()
            }

        async def accept_concurrently(commitment_id: UUID) -> tuple[UUID, str]:
            async with database.session() as session:
                try:
                    await service.accept_commitment(
                        session,
                        principal=people["custodian_b"],
                        commitment_id=commitment_id,
                        terms_hash=hashes[commitment_id],
                        expected_version=1,
                        idempotency_key=str(uuid4()),
                        request_id=uuid4(),
                    )
                    await session.commit()
                    return commitment_id, "ACTIVE"
                except DomainError as exc:
                    await session.rollback()
                    return commitment_id, exc.code

        outcomes = await asyncio.gather(
            *(accept_concurrently(commitment_id) for commitment_id in concurrent_ids)
        )
        assert sorted(status for _, status in outcomes) == [
            "ACTIVE",
            "RISK_ACCOUNT_AVAILABLE_EXCEEDED",
        ]
        concurrent_active_id = next(item_id for item_id, status in outcomes if status == "ACTIVE")

        liability_open_evidence = await evidence(
            database,
            settings,
            people["risk"],
            cooperative_id,
            b"liability incident evidence",
            "liability-incident.txt",
        )
        liability_assessment_evidence = await evidence(
            database,
            settings,
            people["auditor"],
            cooperative_id,
            b"independent liability assessment",
            "liability-assessment.txt",
        )
        async with database.session() as session:
            first_case = await service.open_liability_case(
                session,
                principal=people["risk"],
                commitment_id=accepted_commitment.object_id,
                incident_reference=f"INCIDENT-A-{suffix}",
                affected_amount=Decimal("30"),
                facts="The documented obligation was not fully performed on time.",
                causal_graph={"cause": "non_performance", "effect": "documented_loss"},
                evidence_ids=[liability_open_evidence],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            await service.assess_liability_case(
                session,
                principal=people["auditor"],
                case_id=first_case.object_id,
                fault_class=FaultClass.NEGLIGENCE,
                assessed_loss=Decimal("25"),
                rationale="Evidence supports a bounded loss below the reserved maximum.",
                appeal_until=now + timedelta(days=14),
                evidence_ids=[liability_assessment_evidence],
                expected_version=1,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            second_case = await service.open_liability_case(
                session,
                principal=people["risk"],
                commitment_id=accepted_commitment.object_id,
                incident_reference=f"INCIDENT-B-{suffix}",
                affected_amount=Decimal("20"),
                facts="A separate documented incident is claimed under the same commitment.",
                causal_graph={"cause": "second_incident", "effect": "additional_loss"},
                evidence_ids=[liability_open_evidence],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            with pytest.raises(DomainError, match="RISK_LIABILITY_AGGREGATE_LOSS_EXCEEDS_BOUND"):
                await service.assess_liability_case(
                    session,
                    principal=people["auditor"],
                    case_id=second_case.object_id,
                    fault_class=FaultClass.NEGLIGENCE,
                    assessed_loss=Decimal("20"),
                    rationale="This would exceed the commitment aggregate loss ceiling.",
                    appeal_until=now + timedelta(days=14),
                    evidence_ids=[liability_assessment_evidence],
                    expected_version=1,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            await session.rollback()

        async with database.session() as session:
            contribution = (
                await session.execute(
                    select(ShareContribution).where(
                        ShareContribution.account_id == account_ids["owner"]
                    )
                )
            ).scalar_one()
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(ShareContribution)
                    .where(ShareContribution.id == contribution.id)
                    .values(source_reference="MUTATED")
                )
            await session.rollback()

        async with database.session() as session:
            owner_account = await session.get(ShareAccount, account_ids["owner"])
            assessed_case = await session.get(LiabilityCase, first_case.object_id)
            open_case = await session.get(LiabilityCase, second_case.object_id)
            link = await session.get(RelatedPartyLink, proposed_link.object_id)
            assert owner_account is not None
            assert owner_account.balance == Decimal("100")
            assert owner_account.protected_amount == Decimal("20")
            assert owner_account.executed_not_settled == 0
            assert assessed_case is not None and assessed_case.status == "ASSESSED"
            assert assessed_case.coverage_summary is not None
            assert assessed_case.coverage_summary["execution_status"] == "NOT_EXECUTED"
            assert open_case is not None and open_case.status == "OPEN"
            assert link is not None and link.status == "ACTIVE"
            node = (
                await session.execute(
                    select(NodeProfile).where(NodeProfile.node_code == settings.node_code)
                )
            ).scalar_one()
            assert (await verify_journal(session, node.id)).ok

        app = create_app(settings, manage_runtime=False)
        app.state.database = database

        async def as_owner() -> Principal:
            return people["owner"]

        app.dependency_overrides[get_principal] = as_owner
        with TestClient(app) as client:
            policies = client.get("/api/v1/risk/policies")
            assert policies.status_code == 200
            assert str(proposed_policy.object_id) in {
                item["id"] for item in policies.json()["data"]
            }
            accounts = client.get("/api/v1/risk/accounts")
            assert accounts.status_code == 200
            assert {item["id"] for item in accounts.json()["data"]} == {str(account_ids["owner"])}
            cases = client.get("/api/v1/risk/liability-cases")
            assert cases.status_code == 200 and len(cases.json()["data"]) == 2
            preview = client.post(
                "/api/v1/risk/exposure-previews",
                json={
                    "account_id": str(account_ids["owner"]),
                    "policy_id": str(proposed_policy.object_id),
                    "commitment_type": "DIRECT_OBLIGATION",
                    "amount_reserved": "1",
                    "max_loss": "1",
                },
            )
            assert preview.status_code == 200
            assert preview.json()["data"]["allowed"] is True

        async def as_outsider() -> Principal:
            return people["controller"]

        app.dependency_overrides[get_principal] = as_outsider
        with TestClient(app) as client:
            accounts = client.get("/api/v1/risk/accounts")
            assert accounts.status_code == 200 and accounts.json()["data"] == []
            hidden_preview = client.post(
                "/api/v1/risk/exposure-previews",
                json={
                    "account_id": str(account_ids["owner"]),
                    "policy_id": str(proposed_policy.object_id),
                    "commitment_type": "DIRECT_OBLIGATION",
                    "amount_reserved": "1",
                    "max_loss": "1",
                },
            )
            assert hidden_preview.status_code == 404

        release_evidence = await evidence(
            database,
            settings,
            people["risk"],
            cooperative_id,
            b"risk release decision",
            "risk-release.txt",
        )

        async def as_risk() -> Principal:
            return people["risk"]

        app.dependency_overrides[get_principal] = as_risk
        release_key = str(uuid4())
        with TestClient(app) as client:
            all_accounts = client.get("/api/v1/risk/accounts")
            assert all_accounts.status_code == 200
            assert len(all_accounts.json()["data"]) == 3
            release_payload = {
                "reason": "The tested exposure is no longer required.",
                "evidence_ids": [str(release_evidence)],
                "expected_version": 2,
            }
            released = client.post(
                f"/api/v1/risk/commitments/{concurrent_active_id}/release",
                headers={"Idempotency-Key": release_key},
                json=release_payload,
            )
            assert released.status_code == 201
            replay = client.post(
                f"/api/v1/risk/commitments/{concurrent_active_id}/release",
                headers={"Idempotency-Key": release_key},
                json=release_payload,
            )
            assert replay.status_code == 201 and replay.json()["data"]["replayed"] is True
    finally:
        await database.dispose()
