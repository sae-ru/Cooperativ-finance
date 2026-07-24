"""Participant marketplace purchase materializes into visible exchange obligations."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from cooperative_clearing.api.dependencies import get_principal
from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.main import create_app
from cooperative_clearing.modules.exchange.infrastructure.models import (
    Deal,
    LogisticsOrder,
    Obligation,
)
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database


def _upload_evidence(
    client: TestClient,
    cooperative_id: UUID,
    kind: str,
    content: bytes,
) -> str:
    intent = client.post(
        "/api/v1/evidence/upload-intents",
        headers={"Idempotency-Key": f"participant-evidence-{uuid4()}"},
        json={
            "cooperative_id": str(cooperative_id),
            "expected_sha256": sha256(content).hexdigest(),
            "expected_size": len(content),
            "mime_type": "text/plain",
            "kind": kind,
            "original_name": f"{kind.lower()}.txt",
            "access_scope": "COOPERATIVE",
        },
    )
    assert intent.status_code == 201, intent.text
    evidence_id = intent.json()["data"]["object_id"]
    stored = client.put(
        f"/api/v1/evidence/upload-intents/{evidence_id}/content",
        content=content,
        headers={"Content-Type": "text/plain"},
    )
    assert stored.status_code == 200, stored.text
    return evidence_id


@pytest.mark.integration
async def test_local_purchase_becomes_deal_and_is_visible_to_both_participants() -> None:
    settings = Settings(service_name="participant-marketplace-integration")
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    cooperative_id = stable_id("cooperative", settings.node_code)
    try:
        buyer = Principal(
            user_id=stable_id("demo-user", "farmer"),
            session_id=uuid4(),
            login="farmer",
            member_id=stable_id("member", "demo-member-ivan"),
            must_change_password=False,
            roles=(
                RoleGrant(
                    stable_id("demo-role", "farmer:EXCHANGE_PARTICIPANT"),
                    RoleCode.EXCHANGE_PARTICIPANT,
                    cooperative_id,
                ),
            ),
        )
        seller = Principal(
            user_id=stable_id("bootstrap-user", "registrar"),
            session_id=uuid4(),
            login="registrar",
            member_id=stable_id("member", "demo-member-anna"),
            must_change_password=False,
            roles=(
                RoleGrant(
                    stable_id("bootstrap-role", "registrar:NODE_BUSINESS_OPERATOR"),
                    RoleCode.NODE_BUSINESS_OPERATOR,
                    None,
                ),
            ),
        )

        async def as_buyer() -> Principal:
            return buyer

        async def as_seller() -> Principal:
            return seller

        farmer = Principal(
            user_id=stable_id("demo-user", "farmer"),
            session_id=uuid4(),
            login="farmer",
            member_id=stable_id("member", "demo-member-ivan"),
            must_change_password=False,
            roles=(
                RoleGrant(
                    stable_id("demo-role", "farmer:EXCHANGE_PARTICIPANT"),
                    RoleCode.EXCHANGE_PARTICIPANT,
                    cooperative_id,
                ),
            ),
        )

        async def as_farmer() -> Principal:
            return farmer

        carrier = Principal(
            user_id=stable_id("bootstrap-user", "security"),
            session_id=uuid4(),
            login="security",
            member_id=stable_id("member", "demo-member-elena"),
            must_change_password=False,
            roles=(
                RoleGrant(
                    stable_id("demo-role", "security:LOGISTICS_OPERATOR"),
                    RoleCode.LOGISTICS_OPERATOR,
                    cooperative_id,
                ),
            ),
        )

        async def as_carrier() -> Principal:
            return carrier

        other_carrier = Principal(
            user_id=uuid4(),
            session_id=uuid4(),
            login="other-carrier",
            member_id=uuid4(),
            must_change_password=False,
            roles=(
                RoleGrant(uuid4(), RoleCode.LOGISTICS_OPERATOR, cooperative_id),
            ),
        )

        async def as_other_carrier() -> Principal:
            return other_carrier

        app = create_app(settings)
        app.dependency_overrides[get_principal] = as_buyer
        with TestClient(app) as client:
            service_now = datetime.now(UTC).replace(microsecond=0)
            app.dependency_overrides[get_principal] = as_seller
            service_product_code = f"SERVICE.COMPUTER.REPAIR.TEST.{uuid4()}"
            service_offer = client.post(
                "/api/v1/federation/offers/publish",
                headers={"Idempotency-Key": f"participant-service-{uuid4()}"},
                json={
                    "seller_ref": str(seller.member_id),
                    "product_code": service_product_code,
                    "description": "Computer repair",
                    "quality_grade": "A",
                    "certificate_refs": [],
                    "quantity_available": "4.000",
                    "quantity_is_band": False,
                    "unit_code": "HOUR",
                    "unit_scale": 3,
                    "minimum_batch": "1.000",
                    "divisible": True,
                    "origin_region": "EAST-DISTRICT",
                    "origin_precision": "DISTRICT",
                    "availability_from": (service_now - timedelta(minutes=1)).isoformat(),
                    "availability_until": (service_now + timedelta(days=7)).isoformat(),
                    "fulfillment_deadline": (service_now + timedelta(days=6)).isoformat(),
                    "unit_price": "10.00",
                    "mandatory_fee_per_unit": "0",
                    "valuation_unit": "COOP",
                    "price_policy_version": "MARKET-UI-V1",
                    "handling_requirements": {"offer_kind": "SERVICE"},
                    "counterparty_policy": {},
                    "geography_policy": {},
                    "guarantee_terms": {},
                    "source_mode": "DIRECT",
                    "node_sequence": 1,
                    "signed_at": service_now.isoformat(),
                    "valid_until": (service_now + timedelta(days=7)).isoformat(),
                },
            )
            assert service_offer.status_code == 201, service_offer.text

            app.dependency_overrides[get_principal] = as_buyer
            service_search = client.post(
                "/api/v1/federation/catalog/search",
                json={
                    "mode": "DIRECT",
                    "product_code": service_product_code,
                    "quantity": "1",
                    "unit_code": "HOUR",
                    "valuation_unit": "COOP",
                    "destination_region": "EAST-DISTRICT",
                    "maximum_age_seconds": 604800,
                    "quality_minimum": "A",
                    "top_k": 20,
                },
            )
            assert service_search.status_code == 200, service_search.text
            service_candidate = service_search.json()["data"][0]
            assert service_candidate["quote"] is not None
            assert Decimal(service_candidate["goods_cost"]) == Decimal("10")
            assert Decimal(service_candidate["logistics_cost"]) == Decimal("0")
            assert Decimal(service_candidate["landed_cost"]) == Decimal("10")
            assert service_candidate["cost_status"] == "CONFIRMED"
            service_intent = client.post(
                "/api/v1/federation/purchase-intents",
                headers={"Idempotency-Key": f"participant-service-intent-{uuid4()}"},
                json={
                    "offer_record_id": service_candidate["offer"]["record_id"],
                    "quote_record_id": service_candidate["quote"]["record_id"],
                    "quantity": "1",
                    "destination_region": "EAST-DISTRICT",
                    "delivery_address_text": "12 Farm Road, Barn 2",
                    "delivery_contact_name": "John Buyer",
                    "delivery_contact_phone": "+1 555 010 2000",
                    "delivery_instructions": "Call at the gate",
                    "max_landed_cost": service_candidate["landed_cost"],
                    "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
                },
            )
            assert service_intent.status_code == 201, service_intent.text
            search = client.post(
                "/api/v1/federation/catalog/search",
                json={
                    "mode": "DIRECT",
                    "product_code": "NAIL.STEEL.100MM",
                    "quantity": "100",
                    "unit_code": "PCS",
                    "valuation_unit": "COOP",
                    "destination_region": "EAST-DISTRICT",
                    "maximum_age_seconds": 604800,
                    "quality_minimum": "A",
                    "top_k": 20,
                },
            )
            assert search.status_code == 200, search.text
            candidate = next(
                item
                for item in search.json()["data"]
                if item["offer"]["home_node_code"] == settings.node_code
            )
            assert "pickup_address_text" not in candidate["offer"]
            assert "pickup_contact_phone" not in candidate["offer"]
            now = datetime.now(UTC)
            logistics_destination = f"LOGISTICS-TEST-{uuid4()}"
            app.dependency_overrides[get_principal] = as_carrier
            quote = client.post(
                "/api/v1/federation/logistics/quotes",
                headers={"Idempotency-Key": f"participant-logistics-quote-{uuid4()}"},
                json={
                    "offer_record_id": candidate["offer"]["record_id"],
                    "carrier_ref": "TEST-CARRIER",
                    "destination_region": logistics_destination,
                    "route_legs": [
                        {
                            "from": candidate["offer"]["origin_region"],
                            "to": logistics_destination,
                            "mode": "TRUCK",
                        }
                    ],
                    "custody_transfers": 1,
                    "capacity": "100",
                    "cost_components": {"transport": "7", "handling": "1"},
                    "cost_status": "CONFIRMED",
                    "delivery_from": (now + timedelta(hours=2)).isoformat(),
                    "delivery_until": (now + timedelta(days=2)).isoformat(),
                    "liability_limit": "100",
                    "assumptions": [],
                    "signed_at": now.isoformat(),
                    "valid_until": (now + timedelta(days=7)).isoformat(),
                },
            )
            assert quote.status_code == 201, quote.text
            my_quotes = client.get("/api/v1/federation/logistics/quotes/mine")
            assert my_quotes.status_code == 200, my_quotes.text
            assert any(
                item["destination_region"] == logistics_destination
                for item in my_quotes.json()["data"]
            )

            app.dependency_overrides[get_principal] = as_buyer
            logistics_search = client.post(
                "/api/v1/federation/catalog/search",
                json={
                    "mode": "DIRECT",
                    "product_code": "NAIL.STEEL.100MM",
                    "quantity": "100",
                    "unit_code": "PCS",
                    "valuation_unit": "COOP",
                    "destination_region": logistics_destination,
                    "maximum_age_seconds": 604800,
                    "quality_minimum": "A",
                    "top_k": 20,
                },
            )
            assert logistics_search.status_code == 200, logistics_search.text
            candidate = next(
                item
                for item in logistics_search.json()["data"]
                if item["offer"]["record_id"] == candidate["offer"]["record_id"]
            )
            intent = client.post(
                "/api/v1/federation/purchase-intents",
                headers={"Idempotency-Key": f"participant-intent-{uuid4()}"},
                json={
                    "offer_record_id": candidate["offer"]["record_id"],
                    "quote_record_id": candidate["quote"]["record_id"],
                    "quantity": "100",
                    "destination_region": logistics_destination,
                    "delivery_address_text": "12 Farm Road, Barn 2",
                    "delivery_contact_name": "John Buyer",
                    "delivery_contact_phone": "+1 555 010 2000",
                    "delivery_instructions": "Call at the gate",
                    "max_landed_cost": "100",
                    "expires_at": (now + timedelta(minutes=30)).isoformat(),
                },
            )
            assert intent.status_code == 201, intent.text
            intent_id = intent.json()["data"]["object_id"]
            reserve_until = (now + timedelta(minutes=20)).isoformat()
            for kind in ("goods", "logistics"):
                reserved = client.post(
                    f"/api/v1/federation/purchase-intents/{intent_id}/reserve-{kind}",
                    headers={"Idempotency-Key": f"participant-{kind}-{uuid4()}"},
                    json={"expires_at": reserve_until},
                )
                assert reserved.status_code == 201, reserved.text

            listed = client.get("/api/v1/federation/purchase-intents")
            prepared = next(item for item in listed.json()["data"] if item["id"] == intent_id)
            committed = client.post(
                f"/api/v1/federation/purchase-intents/{intent_id}/commit",
                headers={"Idempotency-Key": f"participant-commit-{uuid4()}"},
                json={
                    "summary_hash": prepared["summary_hash"],
                    "expected_version": prepared["version"],
                },
            )
            assert committed.status_code == 201, committed.text

            replay = client.post(
                f"/api/v1/federation/purchase-intents/{intent_id}/materialize-deal"
            )
            assert replay.status_code == 201, replay.text
            assert replay.json()["data"]["replayed"] is True
            deal_id = replay.json()["data"]["object_id"]

            app.dependency_overrides[get_principal] = as_carrier
            carrier_orders = client.get("/api/v1/exchange/logistics-orders")
            assert carrier_orders.status_code == 200, carrier_orders.text
            assigned_order = max(
                (
                    item
                    for item in carrier_orders.json()["data"]
                    if item["destination_text"] == "12 Farm Road, Barn 2"
                    and Decimal(item["quantity"]) == Decimal("100")
                ),
                key=lambda item: item["created_at"],
            )
            assert assigned_order["origin_text"] == "Demo Farm, 12 Field Road, loading gate"
            assert assigned_order["origin_contact_name"] == "Demo Seller"
            assert assigned_order["origin_contact_phone"] == "+1 555 010 1000"
            assert assigned_order["destination_text"] == "12 Farm Road, Barn 2"
            assert assigned_order["destination_contact_name"] == "John Buyer"
            assert assigned_order["destination_contact_phone"] == "+1 555 010 2000"
            assert assigned_order["destination_instructions"] == "Call at the gate"

            app.dependency_overrides[get_principal] = as_other_carrier
            unrelated_orders = client.get("/api/v1/exchange/logistics-orders")
            assert unrelated_orders.status_code == 200, unrelated_orders.text
            assert assigned_order["id"] not in {
                item["id"] for item in unrelated_orders.json()["data"]
            }

            app.dependency_overrides[get_principal] = as_carrier
            accepted_order = client.post(
                f"/api/v1/exchange/logistics-orders/{assigned_order['id']}/accept",
                headers={"Idempotency-Key": f"participant-logistics-accept-{uuid4()}"},
                json={"evidence_ids": [], "expected_version": 1},
            )
            assert accepted_order.status_code == 200, accepted_order.text

            pickup_evidence_id = _upload_evidence(
                client,
                cooperative_id,
                "LOGISTICS_PICKUP_ACT",
                b"carrier pickup record",
            )
            picked_up = client.post(
                f"/api/v1/exchange/logistics-orders/{assigned_order['id']}/pickup",
                headers={"Idempotency-Key": f"participant-logistics-pickup-{uuid4()}"},
                json={"evidence_ids": [pickup_evidence_id], "expected_version": 2},
            )
            assert picked_up.status_code == 200, picked_up.text
            delivery_evidence_id = _upload_evidence(
                client,
                cooperative_id,
                "LOGISTICS_DELIVER_ACT",
                b"carrier delivery record",
            )
            delivered = client.post(
                f"/api/v1/exchange/logistics-orders/{assigned_order['id']}/deliver",
                headers={"Idempotency-Key": f"participant-logistics-deliver-{uuid4()}"},
                json={"evidence_ids": [delivery_evidence_id], "expected_version": 3},
            )
            assert delivered.status_code == 200, delivered.text

            app.dependency_overrides[get_principal] = as_buyer
            buyer_dashboard = client.get("/api/v1/participant/dashboard")
            assert buyer_dashboard.status_code == 200, buyer_dashboard.text
            buyer_data = buyer_dashboard.json()["data"]
            assert buyer_data["profile"]["display_name"] == "Ivan Milkman"
            assert buyer_data["memberships"][0]["cooperative_name"]
            assert buyer_data["purchases"][0]["id"] == intent_id
            assert Decimal(buyer_data["exchange_position"]["expected_outgoing"]) > 0
            assert {item["direction"] for item in buyer_data["obligations"]} == {
                "OWE",
                "RECEIVE",
            }

            app.dependency_overrides[get_principal] = as_seller
            seller_dashboard = client.get("/api/v1/participant/dashboard")
            assert seller_dashboard.status_code == 200, seller_dashboard.text
            seller_data = seller_dashboard.json()["data"]
            assert seller_data["shares"]["account_missing"] is False
            assert seller_data["offers"]
            assert seller_data["sales"][0]["id"] == intent_id
            assert Decimal(seller_data["exchange_position"]["expected_incoming"]) > 0
            product_obligation = next(
                item
                for item in seller_data["obligations"]
                if item["deal_id"] == deal_id and item["subject_type"] == "PRODUCT"
            )
            assert product_obligation["direction"] == "OWE"
            assert product_obligation["fulfillment_place"] == "12 Farm Road, Barn 2"

            handover_evidence_id = _upload_evidence(
                client,
                cooperative_id,
                "FULFILLMENT_ACT",
                b"seller handover record",
            )
            submitted = client.post(
                f"/api/v1/exchange/obligations/{product_obligation['id']}/fulfillments",
                headers={"Idempotency-Key": f"participant-handover-{uuid4()}"},
                json={
                    "quantity": "100",
                    "quality_claim": "Handed over in the agreed quantity and condition",
                    "location_text": product_obligation["fulfillment_place"],
                    "performed_at": datetime.now(UTC).isoformat(),
                    "logistics_order_id": assigned_order["id"],
                    "evidence_ids": [handover_evidence_id],
                    "expected_version": product_obligation["version"],
                },
            )
            assert submitted.status_code == 201, submitted.text
            fulfillment_id = submitted.json()["data"]["object_id"]

            app.dependency_overrides[get_principal] = as_buyer
            visible_fulfillments = client.get("/api/v1/exchange/fulfillments")
            assert visible_fulfillments.status_code == 200, visible_fulfillments.text
            pending_fulfillment = next(
                item
                for item in visible_fulfillments.json()["data"]
                if item["id"] == fulfillment_id
            )
            assert pending_fulfillment["status"] == "SUBMITTED"
            receipt_evidence_id = _upload_evidence(
                client,
                cooperative_id,
                "ACCEPTANCE_ACT",
                b"buyer receipt record",
            )
            accepted_fulfillment = client.post(
                f"/api/v1/exchange/fulfillments/{fulfillment_id}/acceptance",
                headers={"Idempotency-Key": f"participant-receipt-{uuid4()}"},
                json={
                    "accepted_quantity": "100",
                    "quality_status": "ACCEPTED_AS_AGREED",
                    "notes": "Quantity and condition checked at delivery",
                    "evidence_ids": [receipt_evidence_id],
                    "expected_fulfillment_version": pending_fulfillment["version"],
                    "expected_obligation_version": product_obligation["version"] + 1,
                },
            )
            assert accepted_fulfillment.status_code == 201, accepted_fulfillment.text
            completed_dashboard = client.get("/api/v1/participant/dashboard")
            assert completed_dashboard.status_code == 200, completed_dashboard.text
            completed_product = next(
                item
                for item in completed_dashboard.json()["data"]["obligations"]
                if item["id"] == product_obligation["id"]
            )
            assert completed_product["status"] == "FULFILLED"
            assert Decimal(completed_product["quantity_fulfilled"]) == Decimal("100")

            app.dependency_overrides[get_principal] = as_other_carrier
            unrelated_fulfillments = client.get("/api/v1/exchange/fulfillments")
            assert unrelated_fulfillments.status_code == 200, unrelated_fulfillments.text
            assert fulfillment_id not in {
                item["id"] for item in unrelated_fulfillments.json()["data"]
            }

            app.dependency_overrides[get_principal] = as_farmer
            farmer_dashboard = client.get("/api/v1/participant/dashboard")
            assert farmer_dashboard.status_code == 200, farmer_dashboard.text
            farmer_data = farmer_dashboard.json()["data"]
            assert farmer_data["profile"]["login"] == "farmer"
            assert farmer_data["memberships"][0]["member_number"] == "D-0007"
            assert Decimal(farmer_data["shares"]["total_balance"]) == Decimal("50")
            assert Decimal(farmer_data["shares"]["available"]) == Decimal("40")
            assert Decimal(farmer_data["shares"]["protected"]) == Decimal("10")
            assert (
                farmer_data["shares"]["accounts"][0]["sources"][0]["source_reference"]
                == "DEMO-SHARE-REGISTER-FARMER-V1"
            )
            assert any(
                offer["description"] == "Farm milk offered by the ordinary demo member"
                for offer in farmer_data["offers"]
            )

        async with database.session() as session:
            deal = await session.get(Deal, deal_id)
            assert deal is not None
            assert str(deal.source_purchase_intent_id) == intent_id
            obligation_count = await session.scalar(
                select(func.count()).select_from(Obligation).where(Obligation.deal_id == deal.id)
            )
            assert obligation_count == 3
            logistics_order = (
                await session.execute(
                    select(LogisticsOrder).where(
                        LogisticsOrder.obligation_id.in_(
                            select(Obligation.id).where(Obligation.deal_id == deal.id)
                        )
                    )
                )
            ).scalar_one()
            assert logistics_order.status == "DELIVERED"
            assert logistics_order.quantity == Decimal("100")
            assert logistics_order.origin_text == "Demo Farm, 12 Field Road, loading gate"
            assert logistics_order.destination_text == "12 Farm Road, Barn 2"
            product_obligation_row = await session.scalar(
                select(Obligation).where(
                    Obligation.deal_id == deal.id,
                    Obligation.subject_type == "PRODUCT",
                )
            )
            assert product_obligation_row is not None
            assert product_obligation_row.status == "FULFILLED"
            assert product_obligation_row.quantity_fulfilled == Decimal("100")
    finally:
        await database.dispose()
