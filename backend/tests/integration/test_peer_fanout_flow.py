"""Outbound peer fan-out verifies signed responses before refreshing cached offers."""

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.modules.federation.application.demo import DEMO_NODE_CODE
from cooperative_clearing.modules.federation.application.peer_protocol import (
    PeerDiscoveryClient,
)
from cooperative_clearing.modules.federation.application.peer_reservations import (
    PeerReservationClient,
)
from cooperative_clearing.modules.federation.domain.peer_protocol import (
    PeerOperation,
    PeerResponse,
)
from cooperative_clearing.modules.federation.infrastructure.discovery_models import (
    FederatedOffer,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    NodeTrustContract,
)
from cooperative_clearing.modules.federation.infrastructure.peer_models import (
    PeerProtocolExchange,
)
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.journal.domain.crypto import (
    NodeSigner,
    canonicalize,
    payload_hash,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database


class SignedCatalogTransport:
    def __init__(self, signer: NodeSigner, artifact: dict[str, object]) -> None:
        self.signer = signer
        self.artifact = artifact

    async def post(self, endpoint: str, body: dict[str, object]) -> dict[str, object]:
        assert endpoint == "https://peer.demo.invalid"
        request_document = {key: value for key, value in body.items() if key != "signature_base64"}
        now = datetime.now(UTC).replace(microsecond=0)
        response = PeerResponse(
            message_id=UUID(str(body["message_id"])),
            request_hash=payload_hash(request_document),
            source_node_code=DEMO_NODE_CODE.lower(),
            target_node_code=str(body["source_node_code"]),
            operation=PeerOperation.CATALOG_SEARCH,
            signer_fingerprint=self.signer.fingerprint,
            signed_at=now,
            expires_at=now + timedelta(seconds=30),
            payload={
                "offers": [self.artifact],
                "quotes": [],
                "quality_mapping_version": "EXACT-V1",
                "unit_mapping_version": "EXACT-V1",
                "result_count": 1,
            },
        )
        document = response.document()
        return {
            **document,
            "signature_base64": base64.b64encode(self.signer.sign(canonicalize(document))).decode(
                "ascii"
            ),
        }


class SignedReservationTransport:
    def __init__(self, signer: NodeSigner) -> None:
        self.signer = signer

    async def post(self, endpoint: str, body: dict[str, object]) -> dict[str, object]:
        assert endpoint == "https://peer.demo.invalid"
        request_document = {key: value for key, value in body.items() if key != "signature_base64"}
        request_payload = body["payload"]
        assert isinstance(request_payload, dict)
        receipt: dict[str, object] = {
            "receipt_id": request_payload["receipt_id"],
            "purchase_intent_id": request_payload["purchase_intent_id"],
            "buyer_node_code": body["source_node_code"],
            "kind": "GOODS",
            "resource_ref": (
                f"offer:{request_payload['offer_id']}:{request_payload['offer_version']}"
            ),
            "home_node_code": DEMO_NODE_CODE.lower(),
            "amount": request_payload["amount"],
            "unit_code": request_payload["unit_code"],
            "expires_at": request_payload["requested_expires_at"],
            "summary_hash": request_payload["summary_hash"],
        }
        artifact = {
            "payload": receipt,
            "payload_hash": payload_hash(receipt),
            "signature_base64": base64.b64encode(self.signer.sign(canonicalize(receipt))).decode(
                "ascii"
            ),
        }
        now = datetime.now(UTC).replace(microsecond=0)
        response = PeerResponse(
            message_id=UUID(str(body["message_id"])),
            request_hash=payload_hash(request_document),
            source_node_code=DEMO_NODE_CODE.lower(),
            target_node_code=str(body["source_node_code"]),
            operation=PeerOperation.GOODS_RESERVE,
            signer_fingerprint=self.signer.fingerprint,
            signed_at=now,
            expires_at=now + timedelta(seconds=30),
            payload={"reservation": artifact, "status": "ACTIVE"},
        )
        document = response.document()
        return {
            **document,
            "signature_base64": base64.b64encode(self.signer.sign(canonicalize(document))).decode(
                "ascii"
            ),
        }


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


@pytest.mark.integration
async def test_direct_fanout_marks_a_verified_peer_offer_live() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"peer-fanout-{suffix}",
        blob_root=Path(f"/tmp/peer-fanout-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    signer = NodeSigner.from_seed_hex(hashlib.sha256(b"demo-peer-node-signing-key").hexdigest())
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
            offer = (
                await session.execute(
                    select(FederatedOffer).where(
                        FederatedOffer.external_node_id == peer.id,
                        FederatedOffer.product_code == "CABBAGE.WHITE",
                    )
                )
            ).scalar_one()
            artifact = {
                "payload": offer.payload,
                "payload_hash": offer.payload_hash,
                "signature_base64": base64.b64encode(offer.node_signature).decode("ascii"),
            }
            await session.commit()

        async with database.session() as session:
            result = await PeerDiscoveryClient(
                settings,
                SignedCatalogTransport(signer, artifact),
            ).refresh_direct_catalog(
                session,
                principal=_operator(),
                payload={
                    "product_code": "CABBAGE.WHITE",
                    "quantity": "10.000",
                    "unit_code": "KG",
                    "valuation_unit": "COOP",
                    "destination_region": "EAST-DISTRICT",
                    "required_certificates": [],
                    "quality_grade": "A",
                    "maximum_goods_cost": None,
                    "latest_delivery": None,
                    "top_k": 20,
                },
            )
            await session.commit()
            assert result.statuses[0].status == "SUCCEEDED"
            assert result.statuses[0].imported_offers == 0

        async with database.session() as session:
            refreshed = await session.get(FederatedOffer, offer.id)
            assert refreshed is not None and refreshed.last_verified_at is not None
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_outbound_reservation_accepts_only_node_signed_bound_receipt() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"peer-reservation-client-{suffix}",
        blob_root=Path(f"/tmp/peer-reservation-client-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    signer = NodeSigner.from_seed_hex(hashlib.sha256(b"demo-peer-node-signing-key").hexdigest())
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
            offer = (
                await session.execute(
                    select(FederatedOffer).where(
                        FederatedOffer.external_node_id == peer.id,
                        FederatedOffer.product_code == "CABBAGE.WHITE",
                    )
                )
            ).scalar_one()
            await session.commit()

        now = datetime.now(UTC).replace(microsecond=0)
        payload: dict[str, object] = {
            "receipt_id": str(uuid4()),
            "purchase_intent_id": str(uuid4()),
            "kind": "GOODS",
            "offer_id": str(offer.offer_id),
            "offer_version": offer.offer_version,
            "amount": "10.000",
            "unit_code": offer.unit_code,
            "requested_expires_at": (now + timedelta(minutes=20)).isoformat(),
            "summary_hash": "sha256:" + "3" * 64,
        }
        async with database.session() as session:
            evidence = await PeerReservationClient(
                settings,
                SignedReservationTransport(signer),
            ).reserve(
                session,
                node_id=peer.id,
                kind="GOODS",
                payload=payload,
            )
            await session.commit()
            assert evidence.payload["receipt_id"] == payload["receipt_id"]
            assert evidence.payload["buyer_node_code"] == settings.node_code
            exchange = (
                (
                    await session.execute(
                        select(PeerProtocolExchange).where(
                            PeerProtocolExchange.message_id.is_not(None),
                            PeerProtocolExchange.operation == "GOODS_RESERVE",
                            PeerProtocolExchange.direction == "OUTBOUND",
                        )
                    )
                )
                .scalars()
                .first()
            )
            assert exchange is not None and exchange.status == "SUCCEEDED"
    finally:
        await database.dispose()
