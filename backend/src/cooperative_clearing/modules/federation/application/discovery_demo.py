"""Deterministic signed catalog and logistics data for the offline demo node."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.federation.application.demo import DEMO_NODE_CODE
from cooperative_clearing.modules.federation.application.discovery import DiscoveryService
from cooperative_clearing.modules.federation.domain.discovery import CostStatus, SearchMode
from cooperative_clearing.modules.federation.infrastructure.discovery_models import (
    FederatedOffer,
    OfferIndexSnapshot,
)
from cooperative_clearing.modules.federation.infrastructure.models import ExternalNode
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.journal.domain.crypto import (
    NodeSigner,
    canonicalize,
    payload_hash,
    utc_timestamp,
)
from cooperative_clearing.shared.core.config import Settings


@dataclass(frozen=True, slots=True)
class DemoOffer:
    key: str
    product_code: str
    description: str
    seller_ref: str
    quantity: Decimal
    minimum_batch: Decimal
    unit_code: str
    unit_scale: int
    unit_price: Decimal
    external: bool
    node_sequence: int
    transport_cost: Decimal


DEMO_OFFERS = (
    DemoOffer(
        key="cabbage-local",
        product_code="CABBAGE.WHITE",
        description="Fresh white cabbage from the local cooperative warehouse",
        seller_ref="LOCAL-FARM-01",
        quantity=Decimal("800.000"),
        minimum_batch=Decimal("5.000"),
        unit_code="KG",
        unit_scale=3,
        unit_price=Decimal("3.20"),
        external=False,
        node_sequence=1,
        transport_cost=Decimal("18.00"),
    ),
    DemoOffer(
        key="cabbage-peer",
        product_code="CABBAGE.WHITE",
        description="Fresh white cabbage from the western district cooperative",
        seller_ref="PEER-FARM-17",
        quantity=Decimal("1200.000"),
        minimum_batch=Decimal("10.000"),
        unit_code="KG",
        unit_scale=3,
        unit_price=Decimal("2.85"),
        external=True,
        node_sequence=1,
        transport_cost=Decimal("32.00"),
    ),
    DemoOffer(
        key="nails-peer",
        product_code="NAIL.STEEL.100MM",
        description="Galvanized steel nails, 100 millimetres",
        seller_ref="PEER-HARDWARE-04",
        quantity=Decimal("20000"),
        minimum_batch=Decimal("100"),
        unit_code="PCS",
        unit_scale=0,
        unit_price=Decimal("0.08"),
        external=True,
        node_sequence=2,
        transport_cost=Decimal("24.00"),
    ),
    DemoOffer(
        key="nails-local",
        product_code="NAIL.STEEL.100MM",
        description="Galvanized steel nails, 100 millimetres",
        seller_ref="LOCAL-HARDWARE-01",
        quantity=Decimal("5000"),
        minimum_batch=Decimal("100"),
        unit_code="PCS",
        unit_scale=0,
        unit_price=Decimal("0.10"),
        external=False,
        node_sequence=3,
        transport_cost=Decimal("12.00"),
    ),
    DemoOffer(
        key="milk-local",
        product_code="MILK.UHT.3_2",
        description="Long-life milk, 3.2 percent fat",
        seller_ref="LOCAL-DAIRY-02",
        quantity=Decimal("600.000"),
        minimum_batch=Decimal("12.000"),
        unit_code="L",
        unit_scale=3,
        unit_price=Decimal("1.70"),
        external=False,
        node_sequence=2,
        transport_cost=Decimal("14.00"),
    ),
    DemoOffer(
        key="farmer-milk-local",
        product_code="MILK.UHT.3_2",
        description="Farm milk offered by the ordinary demo member",
        seller_ref="D-0007",
        quantity=Decimal("100.000"),
        minimum_batch=Decimal("1.000"),
        unit_code="L",
        unit_scale=3,
        unit_price=Decimal("1.90"),
        external=False,
        node_sequence=4,
        transport_cost=Decimal("10.00"),
    ),
)


async def seed_demo_discovery(session: AsyncSession, settings: Settings) -> None:

    peer = (
        await session.execute(
            select(ExternalNode).where(ExternalNode.node_code == DEMO_NODE_CODE.lower())
        )
    ).scalar_one()
    principal = _operator_principal()
    logistics_principal = _logistics_principal(settings)
    peer_signer = NodeSigner.from_seed_hex(
        hashlib.sha256(b"demo-peer-node-signing-key").hexdigest()
    )
    service = DiscoveryService(settings)
    now = datetime.now(UTC).replace(microsecond=0)
    valid_until = now + timedelta(days=7)
    published: list[tuple[DemoOffer, FederatedOffer]] = []
    external_changed = False

    for spec in DEMO_OFFERS:
        offer_id = stable_id("federated-offer", f"demo-{spec.key}")
        existing = await session.scalar(
            select(FederatedOffer)
            .where(FederatedOffer.offer_id == offer_id)
            .order_by(FederatedOffer.offer_version.desc())
            .limit(1)
        )
        if existing is not None:
            if not spec.external and existing.pickup_address_text is None:
                existing.pickup_address_text = "Demo Farm, 12 Field Road, loading gate"
                existing.pickup_contact_name = "Demo Seller"
                existing.pickup_contact_phone = "+1 555 010 1000"
                existing.pickup_instructions = "Call 30 minutes before pickup"
            published.append((spec, existing))
            continue
        external_node_id = peer.id if spec.external else None
        signature = (
            peer_signer.sign(
                canonicalize(
                    _offer_payload(
                        service,
                        spec,
                        offer_id=offer_id,
                        home_node_code=peer.node_code,
                        now=now,
                        valid_until=valid_until,
                    )
                )
            )
            if spec.external
            else None
        )
        publisher = _farmer_principal(settings) if spec.key == "farmer-milk-local" else principal
        result = await service.publish_offer(
            session,
            principal=publisher,
            offer_id=offer_id,
            offer_version=1,
            external_node_id=external_node_id,
            seller_ref=spec.seller_ref,
            product_code=spec.product_code,
            description=spec.description,
            quality_grade="A",
            certificate_refs=["quality:demo-v1"],
            quantity_available=spec.quantity,
            quantity_is_band=False,
            unit_code=spec.unit_code,
            unit_scale=spec.unit_scale,
            minimum_batch=spec.minimum_batch,
            divisible=True,
            origin_region="WEST-DISTRICT" if spec.external else "LOCAL-DISTRICT",
            origin_precision="REGION",
            pickup_address_text=(
                None if spec.external else "Demo Farm, 12 Field Road, loading gate"
            ),
            pickup_contact_name=None if spec.external else "Demo Seller",
            pickup_contact_phone=None if spec.external else "+1 555 010 1000",
            pickup_instructions=None if spec.external else "Call 30 minutes before pickup",
            availability_from=now,
            availability_until=now + timedelta(days=5),
            fulfillment_deadline=now + timedelta(days=6),
            unit_price=spec.unit_price,
            mandatory_fee_per_unit=Decimal("0.05"),
            valuation_unit="COOP",
            price_policy_version="DEMO-V1",
            handling_requirements={"packaging": "STANDARD"},
            counterparty_policy={"active_member": True},
            geography_policy={"destination": "DEMO-REGIONS"},
            guarantee_terms={"required": False},
            source_mode=SearchMode.INDEXED if spec.external else SearchMode.DIRECT,
            node_sequence=spec.node_sequence,
            signed_at=now,
            valid_until=valid_until,
            external_signature=signature,
            idempotency_key=f"demo-discovery-offer-{spec.key}-v1",
            request_id=None,
        )
        await session.flush()
        row = await session.get(FederatedOffer, result.object_id)
        if row is None:
            raise RuntimeError("demo federated offer was not persisted")
        published.append((spec, row))
        external_changed = external_changed or spec.external

        await service.issue_logistics_quote(
            session,
            principal=logistics_principal,
            quote_id=stable_id("logistics-quote", f"demo-{spec.key}"),
            quote_version=1,
            offer_record_id=row.id,
            external_node_id=None,
            carrier_ref="LOCAL-CARRIER-01",
            destination_region="EAST-DISTRICT",
            route_legs=[
                {
                    "from": row.origin_region,
                    "to": "EAST-DISTRICT",
                    "mode": "TRUCK",
                }
            ],
            custody_transfers=1,
            capacity=spec.quantity,
            cost_components={
                "transport": spec.transport_cost,
                "handling": Decimal("3.00"),
                "insurance": Decimal("1.00"),
            },
            cost_status=CostStatus.CONFIRMED,
            delivery_from=now + timedelta(hours=4),
            delivery_until=now + timedelta(days=2),
            liability_limit=Decimal("5000.00"),
            bond_ref="DEMO-LOGISTICS-BOND",
            assumptions=["standard packaging", "road route available"],
            signed_at=now,
            valid_until=valid_until,
            external_signature=None,
            idempotency_key=f"demo-discovery-quote-{spec.key}-v1",
            request_id=None,
        )

    if not external_changed:
        return

    external_hashes = sorted(row.payload_hash for spec, row in published if spec.external)
    latest_index_sequence = await session.scalar(
        select(OfferIndexSnapshot.node_sequence)
        .where(OfferIndexSnapshot.home_node_code == peer.node_code)
        .order_by(OfferIndexSnapshot.node_sequence.desc())
        .limit(1)
    )
    index_sequence = (latest_index_sequence or 0) + 1
    index_base: dict[str, object] = {
        "home_node_code": peer.node_code,
        "source_mode": SearchMode.INDEXED.value,
        "node_sequence": index_sequence,
        "ordered_offer_hashes": external_hashes,
        "signed_at": utc_timestamp(now),
        "valid_until": utc_timestamp(valid_until),
    }
    index_payload = {**index_base, "checkpoint_hash": payload_hash(index_base)}
    await service.publish_offer_index(
        session,
        principal=principal,
        external_node_id=peer.id,
        source_mode=SearchMode.INDEXED,
        node_sequence=index_sequence,
        ordered_offer_hashes=external_hashes,
        signed_at=now,
        valid_until=valid_until,
        external_signature=peer_signer.sign(canonicalize(index_payload)),
        idempotency_key=f"demo-discovery-peer-index-{index_sequence}-v1",
        request_id=None,
    )


def _offer_payload(
    service: DiscoveryService,
    spec: DemoOffer,
    *,
    offer_id: UUID,
    home_node_code: str,
    now: datetime,
    valid_until: datetime,
) -> dict[str, object]:
    return service._offer_payload(
        offer_id=offer_id,
        offer_version=1,
        home_node_code=home_node_code,
        seller_ref=spec.seller_ref,
        product_code=spec.product_code,
        description=spec.description,
        quality_grade="A",
        certificate_refs=["quality:demo-v1"],
        quantity_available=spec.quantity,
        quantity_is_band=False,
        unit_code=spec.unit_code,
        unit_scale=spec.unit_scale,
        minimum_batch=spec.minimum_batch,
        divisible=True,
        origin_region="WEST-DISTRICT",
        origin_precision="REGION",
        availability_from=now,
        availability_until=now + timedelta(days=5),
        fulfillment_deadline=now + timedelta(days=6),
        unit_price=spec.unit_price,
        mandatory_fee_per_unit=Decimal("0.05"),
        valuation_unit="COOP",
        price_policy_version="DEMO-V1",
        handling_requirements={"packaging": "STANDARD"},
        counterparty_policy={"active_member": True},
        geography_policy={"destination": "DEMO-REGIONS"},
        guarantee_terms={"required": False},
        source_mode=SearchMode.INDEXED,
        node_sequence=spec.node_sequence,
        signed_at=now,
        valid_until=valid_until,
    )


def _farmer_principal(settings: Settings) -> Principal:
    cooperative_id = stable_id("cooperative", settings.node_code)
    return Principal(
        user_id=stable_id("demo-user", "farmer"),
        session_id=stable_id("demo-session", "farmer:federated-discovery"),
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


def _operator_principal() -> Principal:
    return Principal(
        user_id=stable_id("bootstrap-user", "registrar"),
        session_id=stable_id("demo-session", "registrar:federated-discovery"),
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


def _logistics_principal(settings: Settings) -> Principal:
    cooperative_id = stable_id("cooperative", settings.node_code)
    return Principal(
        user_id=stable_id("bootstrap-user", "security"),
        session_id=stable_id("demo-session", "security:federated-logistics"),
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
