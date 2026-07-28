from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from cooperative_clearing.api.dependencies import get_principal
from cooperative_clearing.cli import seed_demo
from cooperative_clearing.main import create_app
from cooperative_clearing.modules.exchange.application.service import ExchangeService
from cooperative_clearing.modules.exchange.infrastructure.models import (
    AcceptanceRecord,
    Deal,
    Fulfillment,
    FulfillmentProvenance,
    Obligation,
)
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.inventory.infrastructure.models import InventoryLot, Product
from cooperative_clearing.modules.journal.application.service import verify_journal
from cooperative_clearing.modules.node.infrastructure.models import NodeProfile
from cooperative_clearing.modules.rights.infrastructure.models import (
    CommodityRight,
    RightRedemption,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database


def demo_principal(
    settings: Settings,
    *,
    login: str,
    member_key: str,
    role_key: str,
    role: RoleCode,
    bootstrap_user: bool,
) -> Principal:
    cooperative_id = stable_id("cooperative", settings.node_code)
    return Principal(
        user_id=stable_id("bootstrap-user" if bootstrap_user else "demo-user", login),
        session_id=uuid4(),
        login=login,
        member_id=stable_id("member", member_key),
        must_change_password=False,
        roles=(
            RoleGrant(
                stable_id("demo-role", role_key),
                role,
                cooperative_id,
            ),
        ),
    )


@pytest.mark.integration
async def test_product_fulfillment_is_traceable_and_source_cannot_be_reused() -> None:
    settings = Settings(
        service_name="fulfillment-traceability-integration",
        demo_data_enabled=True,
    )
    await seed_demo(settings)
    database = Database.from_settings(settings)
    recipient = demo_principal(
        settings,
        login="security",
        member_key="demo-member-elena",
        role_key="security:LOGISTICS_OPERATOR",
        role=RoleCode.LOGISTICS_OPERATOR,
        bootstrap_user=True,
    )
    outsider = demo_principal(
        settings,
        login="farmer",
        member_key="demo-member-ivan",
        role_key="farmer:EXCHANGE_PARTICIPANT",
        role=RoleCode.EXCHANGE_PARTICIPANT,
        bootstrap_user=False,
    )
    debtor = demo_principal(
        settings,
        login="registrar",
        member_key="demo-member-anna",
        role_key="registrar:RIGHTS_OPERATOR",
        role=RoleCode.RIGHTS_OPERATOR,
        bootstrap_user=True,
    )
    try:
        async with database.session() as session:
            deal = (
                await session.execute(
                    select(Deal).where(Deal.title == "Demo traced cabbage delivery")
                )
            ).scalar_one()
            obligation = (
                await session.execute(
                    select(Obligation).where(Obligation.deal_id == deal.id)
                )
            ).scalar_one()
            fulfillment = (
                await session.execute(
                    select(Fulfillment).where(Fulfillment.obligation_id == obligation.id)
                )
            ).scalar_one()
            provenance = await session.get(FulfillmentProvenance, fulfillment.id)
            assert provenance is not None
            redemption = await session.get(RightRedemption, provenance.redemption_id)
            right = await session.get(CommodityRight, provenance.right_id)
            lot = await session.get(InventoryLot, provenance.lot_id)
            product = await session.get(Product, provenance.product_id)
            acceptance = (
                await session.execute(
                    select(AcceptanceRecord).where(
                        AcceptanceRecord.fulfillment_id == fulfillment.id
                    )
                )
            ).scalar_one()
            assert redemption is not None
            assert right is not None
            assert lot is not None
            assert product is not None

            assert obligation.subject_type == "PRODUCT"
            assert obligation.subject_id == product.id == lot.product_id
            assert obligation.unit_id == right.unit_id == lot.unit_id
            assert provenance.fulfillment_id == fulfillment.id
            assert provenance.right_id == redemption.right_id == right.id
            assert provenance.lot_id == redemption.lot_id == right.lot_id == lot.id
            assert provenance.source_owner_member_id == obligation.debtor_member_id
            assert provenance.intended_recipient_member_id == obligation.creditor_member_id
            assert provenance.quantity == redemption.quantity == Decimal("8.00")
            assert redemption.status == "COMPLETED"
            assert redemption.completed_event_id == right.redeemed_event_id
            assert right.status == "REDEEMED"
            assert fulfillment.status == "PARTIALLY_ACCEPTED"
            assert fulfillment.quantity == Decimal("8.00")
            assert acceptance.accepted_by_member_id == obligation.creditor_member_id
            assert acceptance.accepted_quantity == Decimal("6.00")
            provenance_count = await session.scalar(
                select(func.count())
                .select_from(FulfillmentProvenance)
                .where(FulfillmentProvenance.redemption_id == redemption.id)
            )
            assert provenance_count == 1

            fulfillment_id = fulfillment.id
            redemption_id = redemption.id
            obligation_id = obligation.id
            with pytest.raises(DomainError) as caught:
                await ExchangeService(settings).submit_fulfillment(
                    session,
                    principal=debtor,
                    obligation_id=obligation.id,
                    quantity=Decimal("8.00"),
                    quality_claim="Attempt to reuse an already linked source",
                    location_text=obligation.fulfillment_place,
                    performed_at=datetime.now(UTC),
                    logistics_order_id=None,
                    source_redemption_id=redemption.id,
                    evidence_ids=[],
                    expected_version=obligation.version,
                    idempotency_key=f"traceability-reuse-{uuid4()}",
                    request_id=uuid4(),
                )
            assert caught.value.code == "FULFILLMENT_SOURCE_ALREADY_USED"
            await session.rollback()

        app = create_app(settings, manage_runtime=False)
        app.state.database = database
        app.dependency_overrides[get_principal] = lambda: recipient
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/exchange/traceability",
                params={"fulfillment_id": str(fulfillment_id)},
            )
        assert response.status_code == 200
        trace = response.json()["data"]
        assert len(trace) == 1
        assert trace[0]["fulfillment_id"] == str(fulfillment_id)
        assert trace[0]["obligation_id"] == str(obligation_id)
        assert trace[0]["redemption_id"] == str(redemption_id)
        assert trace[0]["fulfillment_status"] == "PARTIALLY_ACCEPTED"
        assert Decimal(trace[0]["accepted_quantity"]) == Decimal("6.00")
        assert trace[0]["proof_hash"].startswith("sha256:")
        assert len(trace[0]["proof_hash"]) == 71

        app.dependency_overrides[get_principal] = lambda: outsider
        with TestClient(app) as client:
            private_response = client.get(
                "/api/v1/exchange/traceability",
                params={"fulfillment_id": str(fulfillment_id)},
            )
        assert private_response.status_code == 200
        assert private_response.json()["data"] == []

        async with database.session() as session:
            node = (
                await session.execute(
                    select(NodeProfile).where(NodeProfile.node_code == settings.node_code)
                )
            ).scalar_one()
            assert (await verify_journal(session, node.id)).ok
    finally:
        await database.dispose()
