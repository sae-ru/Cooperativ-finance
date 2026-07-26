from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from cooperative_clearing.cli import initialize_node
from cooperative_clearing.modules.federation.application.discovery import DiscoveryService
from cooperative_clearing.modules.federation.domain.discovery import CostStatus, SearchMode
from cooperative_clearing.modules.federation.infrastructure.discovery_models import (
    FederatedOffer,
    LogisticsQuote,
    PurchaseIntent,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    Membership,
)
from cooperative_clearing.modules.risk.application.antifraud import AntifraudService
from cooperative_clearing.modules.risk.domain.types import AntifraudSignalStatus
from cooperative_clearing.modules.risk.infrastructure.models import (
    AntifraudScan,
    AntifraudSignal,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database
from tests.integration.test_inventory_flow import create_actors, evidence
from tests.integration.test_risk_flow import grant_role


async def publish_offer(
    database: Database,
    settings: Settings,
    *,
    principal_name: str,
    people: dict[str, Principal],
    offer_id: UUID,
    product_code: str,
    price: str,
    version: int = 1,
) -> UUID:
    principal = people[principal_name]
    now = datetime.now(UTC).replace(microsecond=0)
    async with database.session() as session:
        result = await DiscoveryService(settings).publish_offer(
            session,
            principal=principal,
            offer_id=offer_id,
            offer_version=version,
            external_node_id=None,
            seller_ref=f"ANTIFRAUD-{principal_name.upper()}",
            product_code=product_code,
            description="Comparable grade farm product",
            quality_grade="A",
            certificate_refs=[],
            quantity_available=Decimal("100"),
            quantity_is_band=False,
            unit_code="KG",
            unit_scale=3,
            minimum_batch=Decimal("1"),
            divisible=True,
            origin_region="NORTH-DISTRICT",
            origin_precision="REGION",
            pickup_address_text="10 Farm Road",
            pickup_contact_name="Farm contact",
            pickup_contact_phone="+1 555 010 1000",
            pickup_instructions="Use the north gate",
            availability_from=now,
            availability_until=now + timedelta(days=7),
            fulfillment_deadline=now + timedelta(days=5),
            unit_price=Decimal(price),
            mandatory_fee_per_unit=Decimal("0"),
            valuation_unit="COOP",
            price_policy_version="ANTIFRAUD-TEST-1",
            handling_requirements={},
            counterparty_policy={},
            geography_policy={},
            guarantee_terms={},
            source_mode=SearchMode.DIRECT,
            node_sequence=version,
            signed_at=now,
            valid_until=now + timedelta(days=7),
            external_signature=None,
            idempotency_key=str(uuid4()),
            request_id=uuid4(),
        )
        await session.commit()
        return result.object_id


async def issue_quote(
    database: Database,
    settings: Settings,
    *,
    principal: Principal,
    offer_record_id: UUID,
    quote_id: UUID | None = None,
) -> UUID:
    now = datetime.now(UTC).replace(microsecond=0)
    async with database.session() as session:
        result = await DiscoveryService(settings).issue_logistics_quote(
            session,
            principal=principal,
            quote_id=quote_id or uuid4(),
            quote_version=1,
            offer_record_id=offer_record_id,
            external_node_id=None,
            carrier_ref="ANTIFRAUD-CROSS-COOP-CARRIER",
            destination_region="SOUTH-DISTRICT",
            route_legs=[{"mode": "ROAD", "distance_km": "12"}],
            custody_transfers=0,
            capacity=Decimal("50"),
            cost_components={"transport": Decimal("1.25")},
            cost_status=CostStatus.CONFIRMED,
            delivery_from=now + timedelta(days=1),
            delivery_until=now + timedelta(days=2),
            liability_limit=Decimal("100"),
            bond_ref=None,
            assumptions=[],
            signed_at=now,
            valid_until=now + timedelta(days=6),
            external_signature=None,
            idempotency_key=str(uuid4()),
            request_id=uuid4(),
        )
        await session.commit()
        return result.object_id


@pytest.mark.integration
async def test_signal_holds_automation_until_independent_evidenced_review() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"antifraud-integration-{suffix}",
        blob_root=Path(f"/tmp/antifraud-{suffix}"),
    )
    await initialize_node(settings)
    database = Database.from_settings(settings)
    cooperative_id, people, members = await create_actors(database)
    service = AntifraudService(settings)
    try:
        await grant_role(
            database,
            people,
            "owner",
            RoleCode.NODE_BUSINESS_OPERATOR,
            None,
        )
        product_code = f"ANTIFRAUD.MILK.{suffix.upper()}"
        offer_ids = [uuid4(), uuid4(), uuid4()]
        offer_record_ids: list[UUID] = []
        for offer_id, price in zip(offer_ids, ("10", "11", "100"), strict=True):
            offer_record_ids.append(
                await publish_offer(
                    database,
                    settings,
                    principal_name="owner",
                    people=people,
                    offer_id=offer_id,
                    product_code=product_code,
                    price=price,
                )
            )

        carrier_cooperative_id = uuid4()
        async with database.session() as session:
            session.add(
                Cooperative(
                    id=carrier_cooperative_id,
                    code=f"carrier-{suffix}",
                    name="Cross-cooperative carrier",
                    status="ACTIVE",
                )
            )
            session.add(
                Membership(
                    id=uuid4(),
                    cooperative_id=carrier_cooperative_id,
                    member_id=members["admin"],
                    member_number=f"C-{suffix}",
                    status="ACTIVE",
                    joined_at=datetime.now(UTC),
                )
            )
            await session.commit()
        await grant_role(
            database,
            people,
            "admin",
            RoleCode.LOGISTICS_OPERATOR,
            carrier_cooperative_id,
        )
        quote_record_id = await issue_quote(
            database,
            settings,
            principal=people["admin"],
            offer_record_id=offer_record_ids[0],
        )
        async with database.session() as session:
            intent = await DiscoveryService(settings).create_purchase_intent(
                session,
                principal=people["owner"],
                offer_record_id=offer_record_ids[0],
                quote_record_id=quote_record_id,
                quantity=Decimal("1"),
                destination_region="SOUTH-DISTRICT",
                max_landed_cost=Decimal("20"),
                expires_at=datetime.now(UTC) + timedelta(days=1),
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            offer_row = await session.get(FederatedOffer, offer_record_ids[0])
            quote_row = await session.get(LogisticsQuote, quote_record_id)
            intent_row = await session.get(PurchaseIntent, intent.object_id)
            assert offer_row is not None and offer_row.cooperative_id == cooperative_id
            assert (
                quote_row is not None
                and quote_row.cooperative_id == carrier_cooperative_id
            )
            assert intent_row is not None and intent_row.cooperative_id == cooperative_id

        scan_key = str(uuid4())
        async with database.session() as session:
            scan = await service.scan(
                session,
                principal=people["risk"],
                cooperative_id=cooperative_id,
                lookback_hours=168,
                idempotency_key=scan_key,
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            replay = await service.scan(
                session,
                principal=people["risk"],
                cooperative_id=cooperative_id,
                lookback_hours=168,
                idempotency_key=scan_key,
                request_id=uuid4(),
            )
            assert replay.replayed and replay.object_id == scan.object_id
            await session.rollback()

        async with database.session() as session:
            scan_row = await session.get(AntifraudScan, scan.object_id)
            assert scan_row is not None
            assert scan_row.finding_count == 1
            assert scan_row.result_summary["automatic_decisions"] == 0
            signal = (
                await session.execute(
                    select(AntifraudSignal).where(
                        AntifraudSignal.scan_id == scan.object_id,
                        AntifraudSignal.rule_code == "OFFER_PRICE_OUTLIER",
                    )
                )
            ).scalar_one()
            signal_id = signal.id
            assert signal.subject_id == offer_ids[2]
            assert signal.status == "OPEN"
            assert signal.automation_action == "HOLD"

        async with database.session() as session:
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(AntifraudSignal)
                    .where(AntifraudSignal.id == signal_id)
                    .values(reason_key="risk.antifraud.tampered")
                )
                await session.commit()
            await session.rollback()

        with pytest.raises(
            DomainError,
            match="RISK_ANTIFRAUD_MANUAL_REVIEW_REQUIRED",
        ):
            await publish_offer(
                database,
                settings,
                principal_name="owner",
                people=people,
                offer_id=offer_ids[2],
                product_code=product_code,
                price="12",
                version=2,
            )

        with pytest.raises(
            DomainError,
            match="RISK_ANTIFRAUD_MANUAL_REVIEW_REQUIRED",
        ):
            await issue_quote(
                database,
                settings,
                principal=people["admin"],
                offer_record_id=offer_record_ids[2],
            )

        await grant_role(
            database,
            people,
            "risk",
            RoleCode.AUDITOR,
            cooperative_id,
        )
        async with database.session() as session:
            with pytest.raises(
                DomainError,
                match="RISK_ANTIFRAUD_INDEPENDENT_REVIEW_REQUIRED",
            ):
                await service.begin_review(
                    session,
                    principal=people["risk"],
                    signal_id=signal_id,
                    expected_version=1,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            await session.rollback()

        review_evidence = await evidence(
            database,
            settings,
            people["auditor"],
            cooperative_id,
            b"Independent market-price evidence",
            "antifraud-review.txt",
        )
        async with database.session() as session:
            await service.begin_review(
                session,
                principal=people["auditor"],
                signal_id=signal_id,
                expected_version=1,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            await service.decide(
                session,
                principal=people["auditor"],
                signal_id=signal_id,
                decision=AntifraudSignalStatus.CLEARED,
                rationale="The seller supplied a valid correction and current market evidence.",
                evidence_ids=[review_evidence],
                expected_version=2,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            signal = await session.get(AntifraudSignal, signal_id)
            assert signal is not None
            assert signal.status == "CLEARED"
            assert signal.decision_event_id is not None
            assert signal.reviewed_at is not None

        await publish_offer(
            database,
            settings,
            principal_name="owner",
            people=people,
            offer_id=offer_ids[2],
            product_code=product_code,
            price="12",
            version=2,
        )
    finally:
        await database.dispose()
