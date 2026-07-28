from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from cooperative_clearing.api.dependencies import get_principal
from cooperative_clearing.cli import initialize_node
from cooperative_clearing.main import create_app
from cooperative_clearing.modules.exchange.application.service import (
    ExchangeService,
    ObligationDraft,
)
from cooperative_clearing.modules.exchange.infrastructure.models import (
    AcceptanceRecord,
    Deal,
    DealConfirmation,
    DealParty,
    Fulfillment,
    LogisticsOrder,
    Obligation,
    ObligationDispute,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import RoleAssignment
from cooperative_clearing.modules.inventory.application.catalog import CatalogService
from cooperative_clearing.modules.journal.application.service import verify_journal
from cooperative_clearing.modules.node.infrastructure.models import NodeProfile
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database
from tests.integration.test_inventory_flow import actor, create_actors, evidence


def add_grant(
    principal: Principal,
    assignment_id: UUID,
    role: RoleCode,
    cooperative_id: UUID,
) -> Principal:
    assert principal.member_id is not None
    return actor(
        principal.user_id,
        principal.member_id,
        [
            *[(grant.assignment_id, grant.role, grant.cooperative_id) for grant in principal.roles],
            (assignment_id, role, cooperative_id),
        ],
    )


@pytest.mark.integration
async def test_exchange_flow_is_versioned_personal_and_two_phase() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"exchange-integration-{suffix}",
        blob_root=Path(f"/tmp/exchange-{suffix}"),
    )
    await initialize_node(settings)
    database = Database.from_settings(settings)
    cooperative_id, people, members = await create_actors(database)
    role_specs = {
        "admin": RoleCode.COOPERATIVE_ADMIN,
        "owner": RoleCode.DATA_STEWARD,
        "custodian_b": RoleCode.LOGISTICS_OPERATOR,
    }
    try:
        async with database.session() as session:
            for name, role in role_specs.items():
                assignment_id = uuid4()
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
                people[name] = add_grant(people[name], assignment_id, role, cooperative_id)
            owner_risk_assignment = uuid4()
            session.add(
                RoleAssignment(
                    id=owner_risk_assignment,
                    user_id=people["owner"].user_id,
                    role_code=RoleCode.RISK_ADMIN.value,
                    cooperative_id=cooperative_id,
                    status="ACTIVE",
                    granted_by_user_id=None,
                    approved_by_user_id=None,
                )
            )
            people["owner"] = add_grant(
                people["owner"],
                owner_risk_assignment,
                RoleCode.RISK_ADMIN,
                cooperative_id,
            )
            await session.commit()

        catalog = CatalogService(settings)
        async with database.session() as session:
            unit = await catalog.create_unit(
                session,
                principal=people["admin"],
                cooperative_id=cooperative_id,
                code=f"BOX-{suffix}",
                name="Standard box",
                symbol="box",
                dimension="COUNT",
                decimal_scale=0,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        now = datetime.now(UTC)
        draft = ObligationDraft(
            debtor_member_id=members["owner"],
            creditor_member_id=members["custodian_a"],
            subject_type="OTHER",
            subject_id=None,
            description="Deliver ten sealed boxes",
            quality_criteria="Dry, sealed and undamaged",
            fulfillment_place="Integration warehouse",
            due_at=now + timedelta(days=1),
            unit_id=unit.object_id,
            quantity=Decimal("10"),
            partial_allowed=True,
            evidence_required=True,
            confirmation_method="Independent acceptance act",
            substitute_policy="Only equivalent sealed boxes by written agreement",
            valuation_source="No monetary valuation in local exchange slice",
        )
        exchange = ExchangeService(settings)
        async with database.session() as session:
            proposed = await exchange.propose_deal(
                session,
                principal=people["admin"],
                cooperative_id=cooperative_id,
                title="Integration delivery",
                obligations=[draft],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            deal = await session.get(Deal, proposed.object_id)
            assert deal is not None
            terms_version = deal.terms_version
            terms_hash = deal.terms_hash
            parties = set(
                (
                    await session.execute(
                        select(DealParty.member_id).where(
                            DealParty.deal_id == deal.id,
                            DealParty.terms_version == deal.terms_version,
                        )
                    )
                ).scalars()
            )
            assert parties == {members["owner"], members["custodian_a"]}

        async with database.session() as session:
            with pytest.raises(DomainError, match="DEAL_CONFIRMATION_NOT_A_PARTY"):
                await exchange.confirm_deal(
                    session,
                    principal=people["controller"],
                    deal_id=proposed.object_id,
                    terms_version=terms_version,
                    terms_hash=terms_hash,
                    expected_version=1,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            await session.rollback()

        first_key = str(uuid4())
        async with database.session() as session:
            first = await exchange.confirm_deal(
                session,
                principal=people["owner"],
                deal_id=proposed.object_id,
                terms_version=terms_version,
                terms_hash=terms_hash,
                expected_version=1,
                idempotency_key=first_key,
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            replay = await exchange.confirm_deal(
                session,
                principal=people["owner"],
                deal_id=proposed.object_id,
                terms_version=terms_version,
                terms_hash=terms_hash,
                expected_version=1,
                idempotency_key=first_key,
                request_id=uuid4(),
            )
            assert replay.object_id == first.object_id
            assert replay.replayed
            await session.rollback()

        async with database.session() as session:
            await exchange.confirm_deal(
                session,
                principal=people["custodian_a"],
                deal_id=proposed.object_id,
                terms_version=terms_version,
                terms_hash=terms_hash,
                expected_version=2,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            deal = await session.get(Deal, proposed.object_id)
            obligation = (
                await session.execute(
                    select(Obligation).where(Obligation.deal_id == proposed.object_id)
                )
            ).scalar_one()
            assert deal is not None and deal.status == "ACTIVE"
            assert obligation.status == "ACTIVE"
            assert obligation.quantity_submitted == 0
            assert obligation.quantity_fulfilled == 0
            obligation_id = obligation.id
            obligation_version = obligation.version
            confirmations = list(
                (
                    await session.execute(
                        select(DealConfirmation).where(
                            DealConfirmation.deal_id == proposed.object_id
                        )
                    )
                ).scalars()
            )
            assert len(confirmations) == 2

        async with database.session() as session:
            order = await exchange.create_logistics_order(
                session,
                principal=people["admin"],
                obligation_id=obligation_id,
                carrier_member_id=members["custodian_b"],
                quantity=Decimal("6"),
                origin_text="Origin warehouse",
                destination_text="Integration warehouse",
                pickup_due_at=now + timedelta(hours=1),
                delivery_due_at=now + timedelta(hours=2),
                expected_obligation_version=obligation_version,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        pickup_evidence = await evidence(
            database,
            settings,
            people["custodian_b"],
            cooperative_id,
            b"pickup act",
            "pickup.txt",
        )
        delivery_evidence = await evidence(
            database,
            settings,
            people["custodian_b"],
            cooperative_id,
            b"delivery act",
            "delivery.txt",
        )
        for action, evidence_ids, expected_version in [
            ("accept", [], 1),
            ("pickup", [pickup_evidence], 2),
            ("deliver", [delivery_evidence], 3),
        ]:
            async with database.session() as session:
                await exchange.transition_logistics_order(
                    session,
                    principal=people["custodian_b"],
                    order_id=order.object_id,
                    action=action,
                    evidence_ids=evidence_ids,
                    expected_version=expected_version,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
                await session.commit()

        fulfillment_evidence = await evidence(
            database,
            settings,
            people["owner"],
            cooperative_id,
            b"debtor fulfillment act",
            "fulfillment.txt",
        )
        async with database.session() as session:
            submitted = await exchange.submit_fulfillment(
                session,
                principal=people["owner"],
                obligation_id=obligation_id,
                quantity=Decimal("6"),
                quality_claim="Six sealed boxes delivered",
                location_text="Integration warehouse",
                performed_at=now + timedelta(hours=2),
                logistics_order_id=order.object_id,
                source_redemption_id=None,
                evidence_ids=[fulfillment_evidence],
                expected_version=obligation_version,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        acceptance_evidence = await evidence(
            database,
            settings,
            people["custodian_a"],
            cooperative_id,
            b"creditor acceptance act",
            "acceptance.txt",
        )
        async with database.session() as session:
            await exchange.accept_fulfillment(
                session,
                principal=people["custodian_a"],
                fulfillment_id=submitted.object_id,
                accepted_quantity=Decimal("4"),
                quality_status="Four accepted, two damaged",
                notes="Rejected remainder released for replacement",
                evidence_ids=[acceptance_evidence],
                expected_fulfillment_version=1,
                expected_obligation_version=obligation_version + 1,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        dispute_evidence = await evidence(
            database,
            settings,
            people["owner"],
            cooperative_id,
            b"debtor dispute statement",
            "dispute.txt",
        )
        async with database.session() as session:
            disputed = await exchange.open_dispute(
                session,
                principal=people["owner"],
                obligation_id=obligation_id,
                fulfillment_id=submitted.object_id,
                reason_code="QUALITY_ASSESSMENT",
                statement="The rejected quantity requires joint inspection",
                evidence_ids=[dispute_evidence],
                expected_version=obligation_version + 2,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            deal = await session.get(Deal, proposed.object_id)
            obligation = await session.get(Obligation, obligation_id)
            fulfillment = await session.get(Fulfillment, submitted.object_id)
            logistics = await session.get(LogisticsOrder, order.object_id)
            acceptance = (
                await session.execute(
                    select(AcceptanceRecord).where(
                        AcceptanceRecord.fulfillment_id == submitted.object_id
                    )
                )
            ).scalar_one()
            dispute = await session.get(ObligationDispute, disputed.object_id)
            assert deal is not None and deal.status == "DISPUTED"
            assert obligation is not None and obligation.status == "DISPUTED"
            assert obligation.quantity_submitted == 0
            assert obligation.quantity_fulfilled == 4
            assert fulfillment is not None and fulfillment.status == "DISPUTED"
            assert fulfillment.accepted_quantity == 4
            assert acceptance.decision == "PARTIALLY_ACCEPTED"
            assert logistics is not None and logistics.status == "DELIVERED"
            assert dispute is not None and dispute.status == "OPEN"
            node = (
                await session.execute(
                    select(NodeProfile).where(NodeProfile.node_code == settings.node_code)
                )
            ).scalar_one()
            assert (await verify_journal(session, node.id)).ok

        resolution_evidence = await evidence(
            database,
            settings,
            people["admin"],
            cooperative_id,
            b"independent dispute decision",
            "resolution.txt",
        )
        async with database.session() as session:
            with pytest.raises(DomainError, match="DISPUTE_RESOLVER_CONFLICT"):
                await exchange.resolve_dispute(
                    session,
                    principal=people["owner"],
                    dispute_id=disputed.object_id,
                    resolution_action="CONTINUE_PERFORMANCE",
                    resolution_notes="A party cannot resolve its own dispute",
                    evidence_ids=[resolution_evidence],
                    expected_version=1,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            await session.rollback()

        resolution_key = str(uuid4())
        async with database.session() as session:
            resolved = await exchange.resolve_dispute(
                session,
                principal=people["admin"],
                dispute_id=disputed.object_id,
                resolution_action="CONTINUE_PERFORMANCE",
                resolution_notes="Joint inspection completed; continue the remaining delivery",
                evidence_ids=[resolution_evidence],
                expected_version=1,
                idempotency_key=resolution_key,
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            replayed_resolution = await exchange.resolve_dispute(
                session,
                principal=people["admin"],
                dispute_id=disputed.object_id,
                resolution_action="CONTINUE_PERFORMANCE",
                resolution_notes="Joint inspection completed; continue the remaining delivery",
                evidence_ids=[resolution_evidence],
                expected_version=1,
                idempotency_key=resolution_key,
                request_id=uuid4(),
            )
            assert replayed_resolution.object_id == resolved.object_id
            assert replayed_resolution.replayed
            await session.rollback()

        async with database.session() as session:
            deal = await session.get(Deal, proposed.object_id)
            obligation = await session.get(Obligation, obligation_id)
            fulfillment = await session.get(Fulfillment, submitted.object_id)
            dispute = await session.get(ObligationDispute, disputed.object_id)
            assert deal is not None and deal.status == "PARTIALLY_FULFILLED"
            assert obligation is not None and obligation.status == "PARTIALLY_FULFILLED"
            assert fulfillment is not None and fulfillment.status == "PARTIALLY_ACCEPTED"
            assert dispute is not None and dispute.status == "RESOLVED"
            assert dispute.previous_obligation_status == "PARTIALLY_FULFILLED"
            assert dispute.previous_fulfillment_status == "PARTIALLY_ACCEPTED"
            assert dispute.resolution_action == "CONTINUE_PERFORMANCE"
            assert dispute.resolution_event_id == resolved.event_id
            assert dispute.resolved_by_member_id == people["admin"].member_id
            assert dispute.resolved_at is not None
            assert dispute.version == 2
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
            deals = client.get("/api/v1/exchange/deals")
            assert deals.status_code == 200
            assert str(proposed.object_id) in {item["id"] for item in deals.json()["data"]}
            detail = client.get(f"/api/v1/exchange/deals/{proposed.object_id}")
            assert detail.status_code == 200
            assert len(detail.json()["confirmations"]) == 2
            disputes_response = client.get("/api/v1/exchange/disputes")
            assert disputes_response.status_code == 200
            dispute_payload = next(
                item
                for item in disputes_response.json()["data"]
                if item["id"] == str(disputed.object_id)
            )
            assert dispute_payload["status"] == "RESOLVED"
            assert dispute_payload["resolution_action"] == "CONTINUE_PERFORMANCE"
            assert dispute_payload["version"] == 2

        async def as_outsider() -> Principal:
            return people["controller"]

        app.dependency_overrides[get_principal] = as_outsider
        with TestClient(app) as client:
            deals = client.get("/api/v1/exchange/deals")
            assert deals.status_code == 200
            assert str(proposed.object_id) not in {item["id"] for item in deals.json()["data"]}
            detail = client.get(f"/api/v1/exchange/deals/{proposed.object_id}")
            assert detail.status_code == 404
    finally:
        await database.dispose()
