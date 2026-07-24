"""Multi-node signed discovery and compensating purchase reservation flow."""

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import ClassVar, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from cooperative_clearing.api.dependencies import get_principal
from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.main import create_app
from cooperative_clearing.modules.federation.application.demo import DEMO_NODE_CODE
from cooperative_clearing.modules.federation.application.discovery import DiscoveryService
from cooperative_clearing.modules.federation.application.peer_reservations import RemoteEvidence
from cooperative_clearing.modules.federation.domain.discovery import SearchMode
from cooperative_clearing.modules.federation.infrastructure.discovery_models import (
    FederatedOffer,
    PurchaseIntent,
    ReservationReceipt,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    NodeTrustContract,
)
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.journal.domain.crypto import (
    NodeSigner,
    canonicalize,
    payload_hash,
    utc_timestamp,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database


def _operator() -> Principal:
    return Principal(
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


def _offer_body(
    *,
    offer_id: UUID,
    now: datetime,
    seller_ref: str,
    product_code: str,
    price: str,
    source_mode: str,
    node_sequence: int,
) -> dict[str, object]:
    return {
        "offer_id": str(offer_id),
        "offer_version": 1,
        "seller_ref": seller_ref,
        "product_code": product_code,
        "description": "Fresh white cabbage, verified demo lot",
        "quality_grade": "A",
        "certificate_refs": ["quality:demo-v1"],
        "quantity_available": "100.000",
        "quantity_is_band": False,
        "unit_code": "KG",
        "unit_scale": 3,
        "minimum_batch": "1.000",
        "divisible": True,
        "origin_region": "WEST-DISTRICT",
        "origin_precision": "REGION",
        "availability_from": now.isoformat(),
        "availability_until": (now + timedelta(hours=6)).isoformat(),
        "fulfillment_deadline": (now + timedelta(hours=8)).isoformat(),
        "unit_price": price,
        "mandatory_fee_per_unit": "0.10",
        "valuation_unit": "COOP",
        "price_policy_version": "DEMO-V1",
        "handling_requirements": {"temperature": "AMBIENT"},
        "counterparty_policy": {"active_member": True},
        "geography_policy": {"destination": "ANY-DEMO"},
        "guarantee_terms": {"required": False},
        "source_mode": source_mode,
        "node_sequence": node_sequence,
        "signed_at": now.isoformat(),
        "valid_until": (now + timedelta(hours=2)).isoformat(),
    }


def _external_offer_signature(
    *,
    body: dict[str, object],
    home_node_code: str,
    signer: NodeSigner,
) -> str:
    payload = DiscoveryService._offer_payload(
        offer_id=UUID(str(body["offer_id"])),
        offer_version=int(str(body["offer_version"])),
        home_node_code=home_node_code,
        seller_ref=str(body["seller_ref"]),
        product_code=str(body["product_code"]),
        description=str(body["description"]),
        quality_grade=str(body["quality_grade"]),
        certificate_refs=cast(list[str], body["certificate_refs"]),
        quantity_available=Decimal(str(body["quantity_available"])),
        quantity_is_band=bool(body["quantity_is_band"]),
        unit_code=str(body["unit_code"]),
        unit_scale=int(str(body["unit_scale"])),
        minimum_batch=Decimal(str(body["minimum_batch"])),
        divisible=bool(body["divisible"]),
        origin_region=str(body["origin_region"]),
        origin_precision=str(body["origin_precision"]),
        availability_from=datetime.fromisoformat(str(body["availability_from"])),
        availability_until=datetime.fromisoformat(str(body["availability_until"])),
        fulfillment_deadline=datetime.fromisoformat(str(body["fulfillment_deadline"])),
        unit_price=Decimal(str(body["unit_price"])),
        mandatory_fee_per_unit=Decimal(str(body["mandatory_fee_per_unit"])),
        valuation_unit=str(body["valuation_unit"]),
        price_policy_version=str(body["price_policy_version"]),
        handling_requirements=cast(dict[str, object], body["handling_requirements"]),
        counterparty_policy=cast(dict[str, object], body["counterparty_policy"]),
        geography_policy=cast(dict[str, object], body["geography_policy"]),
        guarantee_terms=cast(dict[str, object], body["guarantee_terms"]),
        source_mode=SearchMode(str(body["source_mode"])),
        node_sequence=int(str(body["node_sequence"])),
        signed_at=datetime.fromisoformat(str(body["signed_at"])),
        valid_until=datetime.fromisoformat(str(body["valid_until"])),
    )
    return base64.b64encode(signer.sign(canonicalize(payload))).decode()


def _external_index_signature(
    *,
    home_node_code: str,
    node_sequence: int,
    offer_hashes: list[str],
    signed_at: datetime,
    valid_until: datetime,
    signer: NodeSigner,
) -> str:
    base: dict[str, object] = {
        "home_node_code": home_node_code,
        "source_mode": "INDEXED",
        "node_sequence": node_sequence,
        "ordered_offer_hashes": offer_hashes,
        "signed_at": utc_timestamp(signed_at),
        "valid_until": utc_timestamp(valid_until),
    }
    snapshot = {**base, "checkpoint_hash": payload_hash(base)}
    return base64.b64encode(signer.sign(canonicalize(snapshot))).decode()


def _quote_body(*, offer_record_id: str, now: datetime, transport: str) -> dict[str, object]:
    return {
        "quote_id": str(uuid4()),
        "quote_version": 1,
        "offer_record_id": offer_record_id,
        "carrier_ref": "DEMO-CARRIER-01",
        "destination_region": "EAST-DISTRICT",
        "route_legs": [{"from": "WEST-DISTRICT", "to": "EAST-DISTRICT", "mode": "TRUCK"}],
        "custody_transfers": 1,
        "capacity": "100.000",
        "cost_components": {
            "transport": transport,
            "handling": "1.00",
            "insurance": "0.50",
        },
        "cost_status": "CONFIRMED",
        "delivery_from": (now + timedelta(hours=2)).isoformat(),
        "delivery_until": (now + timedelta(hours=5)).isoformat(),
        "liability_limit": "1000.00",
        "bond_ref": "DEMO-LOGISTICS-BOND",
        "assumptions": ["road open", "standard packaging"],
        "signed_at": now.isoformat(),
        "valid_until": (now + timedelta(hours=2)).isoformat(),
    }


@pytest.mark.integration
async def test_federated_search_reservation_commit_and_compensation() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"federated-discovery-{suffix}",
        blob_root=Path(f"/tmp/federated-discovery-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    principal = _operator()
    try:
        async with database.session() as session:
            peer = (
                await session.execute(
                    select(ExternalNode).where(ExternalNode.node_code == DEMO_NODE_CODE.lower())
                )
            ).scalar_one()

        app = create_app(settings, manage_runtime=False)
        app.state.database = database

        async def as_operator() -> Principal:
            return principal

        app.dependency_overrides[get_principal] = as_operator
        now = datetime.now(UTC).replace(microsecond=0)
        peer_signer = NodeSigner.from_seed_hex(
            hashlib.sha256(b"demo-peer-node-signing-key").hexdigest()
        )
        product_code = f"CABBAGE.TEST.{suffix.upper()}"
        local_body = _offer_body(
            offer_id=uuid4(),
            now=now,
            seller_ref="LOCAL-FARM-01",
            product_code=product_code,
            price="5.00",
            source_mode="DIRECT",
            node_sequence=1,
        )
        external_body = _offer_body(
            offer_id=uuid4(),
            now=now,
            seller_ref="PEER-FARM-01",
            product_code=product_code,
            price="4.50",
            source_mode="INDEXED",
            node_sequence=1,
        )
        external_body["signed_at"] = (now - timedelta(minutes=5)).isoformat()
        external_body["external_node_id"] = str(peer.id)
        external_body["external_signature_base64"] = _external_offer_signature(
            body=external_body,
            home_node_code=peer.node_code,
            signer=peer_signer,
        )

        with TestClient(app) as client:
            local_offer = client.post(
                "/api/v1/federation/offers/publish",
                headers={"Idempotency-Key": f"local-offer-{suffix}"},
                json=local_body,
            )
            external_offer = client.post(
                "/api/v1/federation/offers/publish",
                headers={"Idempotency-Key": f"peer-offer-{suffix}"},
                json=external_body,
            )
            assert local_offer.status_code == 201, local_offer.text
            assert external_offer.status_code == 201, external_offer.text
            local_record_id = local_offer.json()["data"]["object_id"]
            external_record_id = external_offer.json()["data"]["object_id"]

            local_quote = client.post(
                "/api/v1/federation/logistics/quotes",
                headers={"Idempotency-Key": f"local-quote-{suffix}"},
                json=_quote_body(offer_record_id=local_record_id, now=now, transport="3.00"),
            )
            external_quote = client.post(
                "/api/v1/federation/logistics/quotes",
                headers={"Idempotency-Key": f"peer-quote-{suffix}"},
                json=_quote_body(offer_record_id=external_record_id, now=now, transport="6.00"),
            )
            assert local_quote.status_code == 201, local_quote.text
            assert external_quote.status_code == 201, external_quote.text

            search = client.post(
                "/api/v1/federation/catalog/search",
                json={
                    "mode": "DIRECT",
                    "product_code": product_code,
                    "quantity": "10.000",
                    "unit_code": "KG",
                    "valuation_unit": "COOP",
                    "destination_region": "EAST-DISTRICT",
                    "maximum_age_seconds": 3600,
                    "quality_minimum": "A",
                    "top_k": 10,
                },
            )
            assert search.status_code == 200, search.text
            candidates = search.json()["data"]
            assert len(candidates) == 2
            assert {item["offer"]["home_node_code"] for item in candidates} == {
                settings.node_code,
                peer.node_code,
            }
            assert all(item["signature_verified"] for item in candidates)
            freshness = {item["offer"]["home_node_code"]: item["freshness"] for item in candidates}
            assert freshness[settings.node_code] == "LIVE_VERIFIED"
            assert freshness[peer.node_code] == "SIGNED_CACHED"
            assert Decimal(candidates[0]["landed_cost"]) == Decimal("53.5")
            assert candidates[0]["cost_status"] == "CONFIRMED"

            peer_candidate = next(
                item for item in candidates if item["offer"]["home_node_code"] == peer.node_code
            )
            peer_intent = client.post(
                "/api/v1/federation/purchase-intents",
                headers={"Idempotency-Key": f"peer-intent-{suffix}"},
                json={
                    "offer_record_id": peer_candidate["offer"]["record_id"],
                    "quote_record_id": peer_candidate["quote"]["record_id"],
                    "quantity": "10.000",
                    "destination_region": "EAST-DISTRICT",
                    "delivery_address_text": "12 Farm Road, Barn 2",
                    "delivery_contact_name": "John Buyer",
                    "delivery_contact_phone": "+1 555 010 2000",
                    "delivery_instructions": "Call at the gate",
                    "max_landed_cost": "60.00",
                    "expires_at": (now + timedelta(minutes=30)).isoformat(),
                },
            )
            assert peer_intent.status_code == 201, peer_intent.text
            index_rows = client.get("/api/v1/federation/catalog/indexes")
            assert index_rows.status_code == 200
            peer_sequences = [
                item["node_sequence"]
                for item in index_rows.json()["data"]
                if item["home_node_code"] == peer.node_code
            ]
            index_sequence = max(peer_sequences, default=0) + 1
            index_valid_until = now + timedelta(hours=2)
            offer_hashes = sorted([peer_candidate["offer"]["payload_hash"]])
            published_index = client.post(
                "/api/v1/federation/catalog/indexes",
                headers={"Idempotency-Key": f"peer-index-{suffix}"},
                json={
                    "external_node_id": str(peer.id),
                    "source_mode": "INDEXED",
                    "node_sequence": index_sequence,
                    "ordered_offer_hashes": offer_hashes,
                    "signed_at": now.isoformat(),
                    "valid_until": index_valid_until.isoformat(),
                    "external_signature_base64": _external_index_signature(
                        home_node_code=peer.node_code,
                        node_sequence=index_sequence,
                        offer_hashes=offer_hashes,
                        signed_at=now,
                        valid_until=index_valid_until,
                        signer=peer_signer,
                    ),
                },
            )
            assert published_index.status_code == 201, published_index.text
            indexed_search = client.post(
                "/api/v1/federation/catalog/search",
                json={
                    "mode": "INDEXED",
                    "product_code": product_code,
                    "quantity": "10.000",
                    "unit_code": "KG",
                    "valuation_unit": "COOP",
                    "destination_region": "EAST-DISTRICT",
                    "maximum_age_seconds": 3600,
                    "quality_minimum": "A",
                    "top_k": 10,
                },
            )
            assert indexed_search.status_code == 200, indexed_search.text
            assert {item["offer"]["home_node_code"] for item in indexed_search.json()["data"]} == {
                settings.node_code,
                peer.node_code,
            }
            selected = next(
                item for item in candidates if item["offer"]["home_node_code"] == settings.node_code
            )
            intent = client.post(
                "/api/v1/federation/purchase-intents",
                headers={"Idempotency-Key": f"intent-{suffix}"},
                json={
                    "offer_record_id": selected["offer"]["record_id"],
                    "quote_record_id": selected["quote"]["record_id"],
                    "quantity": "10.000",
                    "destination_region": "EAST-DISTRICT",
                    "delivery_address_text": "12 Farm Road, Barn 2",
                    "delivery_contact_name": "John Buyer",
                    "delivery_contact_phone": "+1 555 010 2000",
                    "delivery_instructions": "Call at the gate",
                    "max_landed_cost": "60.00",
                    "expires_at": (now + timedelta(minutes=30)).isoformat(),
                },
            )
            assert intent.status_code == 201, intent.text
            intent_id = intent.json()["data"]["object_id"]
            expiry = (now + timedelta(minutes=20)).isoformat()
            goods = client.post(
                f"/api/v1/federation/purchase-intents/{intent_id}/reserve-goods",
                headers={"Idempotency-Key": f"goods-{suffix}"},
                json={"expires_at": expiry},
            )
            logistics = client.post(
                f"/api/v1/federation/purchase-intents/{intent_id}/reserve-logistics",
                headers={"Idempotency-Key": f"logistics-{suffix}"},
                json={"expires_at": expiry},
            )
            assert goods.status_code == 201, goods.text
            assert logistics.status_code == 201, logistics.text

            intents = client.get("/api/v1/federation/purchase-intents")
            prepared = next(item for item in intents.json()["data"] if item["id"] == intent_id)
            assert prepared["status"] == "PREPARED"
            receipts = client.get(f"/api/v1/federation/purchase-intents/{intent_id}/receipts")
            assert {item["kind"] for item in receipts.json()["data"]} == {
                "GOODS",
                "LOGISTICS",
            }
            committed = client.post(
                f"/api/v1/federation/purchase-intents/{intent_id}/commit",
                headers={"Idempotency-Key": f"commit-{suffix}"},
                json={
                    "summary_hash": prepared["summary_hash"],
                    "expected_version": prepared["version"],
                },
            )
            assert committed.status_code == 201, committed.text

            oversized_intent = client.post(
                "/api/v1/federation/purchase-intents",
                headers={"Idempotency-Key": f"oversized-intent-{suffix}"},
                json={
                    "offer_record_id": selected["offer"]["record_id"],
                    "quote_record_id": selected["quote"]["record_id"],
                    "quantity": "95.000",
                    "destination_region": "EAST-DISTRICT",
                    "delivery_address_text": "12 Farm Road, Barn 2",
                    "delivery_contact_name": "John Buyer",
                    "delivery_contact_phone": "+1 555 010 2000",
                    "delivery_instructions": "Call at the gate",
                    "max_landed_cost": "600.00",
                    "expires_at": (now + timedelta(minutes=30)).isoformat(),
                },
            )
            assert oversized_intent.status_code == 201, oversized_intent.text
            oversized_id = oversized_intent.json()["data"]["object_id"]
            oversubscribed = client.post(
                f"/api/v1/federation/purchase-intents/{oversized_id}/reserve-goods",
                headers={"Idempotency-Key": f"oversized-goods-{suffix}"},
                json={"expires_at": expiry},
            )
            assert oversubscribed.status_code == 409
            assert oversubscribed.json()["error"]["code"] == "GOODS_QUANTITY_INSUFFICIENT"
            compensated_intent = client.post(
                "/api/v1/federation/purchase-intents",
                headers={"Idempotency-Key": f"compensated-intent-{suffix}"},
                json={
                    "offer_record_id": selected["offer"]["record_id"],
                    "quote_record_id": selected["quote"]["record_id"],
                    "quantity": "5.000",
                    "destination_region": "EAST-DISTRICT",
                    "delivery_address_text": "12 Farm Road, Barn 2",
                    "delivery_contact_name": "John Buyer",
                    "delivery_contact_phone": "+1 555 010 2000",
                    "delivery_instructions": "Call at the gate",
                    "max_landed_cost": "40.00",
                    "expires_at": (now + timedelta(minutes=30)).isoformat(),
                },
            )
            assert compensated_intent.status_code == 201, compensated_intent.text
            compensated_id = compensated_intent.json()["data"]["object_id"]
            assert (
                client.post(
                    f"/api/v1/federation/purchase-intents/{compensated_id}/reserve-goods",
                    headers={"Idempotency-Key": f"compensated-goods-{suffix}"},
                    json={"expires_at": expiry},
                ).status_code
                == 201
            )
            cancelled = client.post(
                f"/api/v1/federation/purchase-intents/{compensated_id}/cancel",
                headers={"Idempotency-Key": f"cancel-{suffix}"},
                json={"reason": "Buyer cancelled before logistics", "expected_version": 2},
            )
            assert cancelled.status_code == 201, cancelled.text

        async with database.session() as session:
            committed_intent = await session.get(PurchaseIntent, UUID(intent_id))
            compensated = await session.get(PurchaseIntent, UUID(compensated_id))
            released = list(
                (
                    await session.execute(
                        select(ReservationReceipt).where(
                            ReservationReceipt.intent_id == UUID(compensated_id)
                        )
                    )
                ).scalars()
            )
            assert committed_intent is not None and committed_intent.status == "COMMITTED"
            assert compensated is not None and compensated.status == "COMPENSATED"
            assert [receipt.status for receipt in released] == ["RELEASED"]

            peer_record = await session.get(FederatedOffer, UUID(external_record_id))
            assert peer_record is not None
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(FederatedOffer)
                    .where(FederatedOffer.id == peer_record.id)
                    .values(payload_hash="sha256:" + "0" * 64)
                )
            await session.rollback()
    finally:
        await database.dispose()


class SignedPeerReservationClient:
    signer: NodeSigner
    node_code: str
    receipts: ClassVar[dict[str, dict[str, object]]] = {}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @classmethod
    def configure(cls, signer: NodeSigner, node_code: str) -> None:
        cls.signer = signer
        cls.node_code = node_code
        cls.receipts = {}

    @classmethod
    def evidence(cls, payload: dict[str, object]) -> RemoteEvidence:
        digest = payload_hash(payload)
        return RemoteEvidence(
            payload=payload,
            evidence_hash=digest,
            signature=cls.signer.sign(canonicalize(payload)),
            signer_fingerprint=cls.signer.fingerprint,
        )

    async def reserve(
        self,
        session: object,
        *,
        node_id: UUID,
        kind: str,
        payload: dict[str, object],
    ) -> RemoteEvidence:
        del session, node_id
        assert kind == "GOODS"
        receipt = {
            "receipt_id": payload["receipt_id"],
            "purchase_intent_id": payload["purchase_intent_id"],
            "buyer_node_code": self.settings.node_code,
            "kind": kind,
            "resource_ref": f"offer:{payload['offer_id']}:{payload['offer_version']}",
            "home_node_code": self.node_code,
            "amount": payload["amount"],
            "unit_code": payload["unit_code"],
            "expires_at": payload["requested_expires_at"],
            "summary_hash": payload["summary_hash"],
        }
        self.receipts[str(payload["receipt_id"])] = receipt
        return self.evidence(receipt)

    async def commit(
        self,
        session: object,
        *,
        node_id: UUID,
        kind: str,
        payload: dict[str, object],
    ) -> RemoteEvidence:
        del session, node_id
        receipt = self.receipts[str(payload["receipt_id"])]
        acknowledgement = {
            "receipt_id": receipt["receipt_id"],
            "purchase_intent_id": receipt["purchase_intent_id"],
            "buyer_node_code": self.settings.node_code,
            "kind": kind,
            "resource_ref": receipt["resource_ref"],
            "receipt_hash": payload["receipt_hash"],
            "summary_hash": payload["summary_hash"],
            "commit_request_hash": payload["commit_request_hash"],
            "status": "COMMITTED",
            "committed_at": utc_timestamp(datetime.now(UTC)),
        }
        return self.evidence(acknowledgement)

    async def release(
        self,
        session: object,
        *,
        node_id: UUID,
        kind: str,
        payload: dict[str, object],
    ) -> RemoteEvidence:
        del session, node_id
        receipt = self.receipts[str(payload["receipt_id"])]
        acknowledgement = {
            "receipt_id": receipt["receipt_id"],
            "purchase_intent_id": receipt["purchase_intent_id"],
            "buyer_node_code": self.settings.node_code,
            "kind": kind,
            "resource_ref": receipt["resource_ref"],
            "receipt_hash": payload["receipt_hash"],
            "summary_hash": payload["summary_hash"],
            "reason": payload["reason"],
            "status": "RELEASED",
            "released_at": utc_timestamp(datetime.now(UTC)),
        }
        return self.evidence(acknowledgement)


@pytest.mark.integration
async def test_external_reservation_is_node_coordinated_without_client_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"external-reservation-{suffix}",
        blob_root=Path(f"/tmp/external-reservation-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    principal = _operator()
    peer_signer = NodeSigner.from_seed_hex(
        hashlib.sha256(b"demo-peer-node-signing-key").hexdigest()
    )
    try:
        async with database.session() as session:
            peer = (
                await session.execute(
                    select(ExternalNode).where(ExternalNode.node_code == DEMO_NODE_CODE.lower())
                )
            ).scalar_one()
            contract = (
                await session.execute(
                    select(NodeTrustContract).where(
                        NodeTrustContract.node_id == peer.id,
                        NodeTrustContract.status == "ACTIVE",
                    )
                )
            ).scalar_one()
            peer.capabilities = sorted({*peer.capabilities, "CATALOG"})
            peer.supported_protocols = sorted({*peer.supported_protocols, "CC-PEER-1"})
            contract.capabilities = sorted({*contract.capabilities, "CATALOG"})
            await session.commit()

        SignedPeerReservationClient.configure(peer_signer, peer.node_code)
        monkeypatch.setattr(
            "cooperative_clearing.api.discovery.PeerReservationClient",
            SignedPeerReservationClient,
        )
        app = create_app(settings, manage_runtime=False)
        app.state.database = database

        async def as_operator() -> Principal:
            return principal

        app.dependency_overrides[get_principal] = as_operator
        now = datetime.now(UTC).replace(microsecond=0)
        external_body = _offer_body(
            offer_id=uuid4(),
            now=now,
            seller_ref="REMOTE-FARM",
            product_code=f"REMOTE.CABBAGE.{suffix.upper()}",
            price="4.00",
            source_mode="DIRECT",
            node_sequence=1,
        )
        external_body["signed_at"] = (now - timedelta(minutes=5)).isoformat()
        external_body["external_node_id"] = str(peer.id)
        external_body["external_signature_base64"] = _external_offer_signature(
            body=external_body,
            home_node_code=peer.node_code,
            signer=peer_signer,
        )

        with TestClient(app) as client:
            offer_response = client.post(
                "/api/v1/federation/offers/publish",
                headers={"Idempotency-Key": f"remote-offer-{suffix}"},
                json=external_body,
            )
            assert offer_response.status_code == 201, offer_response.text
            quote_response = client.post(
                "/api/v1/federation/logistics/quotes",
                headers={"Idempotency-Key": f"local-quote-{suffix}"},
                json=_quote_body(
                    offer_record_id=offer_response.json()["data"]["object_id"],
                    now=now,
                    transport="2.00",
                ),
            )
            assert quote_response.status_code == 201, quote_response.text
            async with database.session() as session:
                external_offer = await session.get(
                    FederatedOffer,
                    UUID(offer_response.json()["data"]["object_id"]),
                )
                assert external_offer is not None
                external_offer.last_verified_at = datetime.now(UTC)
                await session.commit()
            intent_response = client.post(
                "/api/v1/federation/purchase-intents",
                headers={"Idempotency-Key": f"remote-intent-{suffix}"},
                json={
                    "offer_record_id": offer_response.json()["data"]["object_id"],
                    "quote_record_id": quote_response.json()["data"]["object_id"],
                    "quantity": "10.000",
                    "destination_region": "EAST-DISTRICT",
                    "delivery_address_text": "12 Farm Road, Barn 2",
                    "delivery_contact_name": "John Buyer",
                    "delivery_contact_phone": "+1 555 010 2000",
                    "delivery_instructions": "Call at the gate",
                    "max_landed_cost": "100.00",
                    "expires_at": (now + timedelta(minutes=30)).isoformat(),
                },
            )
            assert intent_response.status_code == 201, intent_response.text
            intent_id = intent_response.json()["data"]["object_id"]
            rejected_signature = client.post(
                f"/api/v1/federation/purchase-intents/{intent_id}/reserve-goods",
                headers={"Idempotency-Key": f"bad-reserve-{suffix}"},
                json={
                    "expires_at": (now + timedelta(minutes=20)).isoformat(),
                    "external_signature_base64": base64.b64encode(b"x" * 64).decode(),
                },
            )
            assert rejected_signature.status_code == 422

            goods_response = client.post(
                f"/api/v1/federation/purchase-intents/{intent_id}/reserve-goods",
                headers={"Idempotency-Key": f"remote-goods-{suffix}"},
                json={"expires_at": (now + timedelta(minutes=20)).isoformat()},
            )
            assert goods_response.status_code == 201, goods_response.text
            logistics_response = client.post(
                f"/api/v1/federation/purchase-intents/{intent_id}/reserve-logistics",
                headers={"Idempotency-Key": f"local-logistics-{suffix}"},
                json={"expires_at": (now + timedelta(minutes=20)).isoformat()},
            )
            assert logistics_response.status_code == 201, logistics_response.text
            intent_rows = client.get("/api/v1/federation/purchase-intents").json()["data"]
            prepared = next(row for row in intent_rows if row["id"] == intent_id)
            assert prepared["product_code"] == external_body["product_code"]
            assert prepared["seller_ref"] == "REMOTE-FARM"
            assert prepared["seller_node_code"] == peer.node_code
            commit_response = client.post(
                f"/api/v1/federation/purchase-intents/{intent_id}/commit",
                headers={"Idempotency-Key": f"remote-commit-{suffix}"},
                json={
                    "summary_hash": prepared["summary_hash"],
                    "expected_version": prepared["version"],
                },
            )
            assert commit_response.status_code == 201, commit_response.text

        async with database.session() as session:
            intent = await session.get(PurchaseIntent, UUID(intent_id))
            receipts = list(
                (
                    await session.execute(
                        select(ReservationReceipt).where(
                            ReservationReceipt.intent_id == UUID(intent_id)
                        )
                    )
                ).scalars()
            )
            assert intent is not None and intent.status == "COMMITTED"
            remote = next(receipt for receipt in receipts if receipt.kind == "GOODS")
            assert remote.remote_commit_hash is not None
            assert remote.signer_fingerprint == peer_signer.fingerprint
    finally:
        await database.dispose()
