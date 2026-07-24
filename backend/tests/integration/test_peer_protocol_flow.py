"""Authenticated peer search, replay, and tamper handling against PostgreSQL."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.modules.federation.application.demo import DEMO_NODE_CODE
from cooperative_clearing.modules.federation.application.peer_protocol import (
    PeerProtocolService,
)
from cooperative_clearing.modules.federation.application.peer_reservations import (
    expire_stale_reservations,
)
from cooperative_clearing.modules.federation.domain.peer_protocol import (
    PeerOperation,
    PeerRequest,
)
from cooperative_clearing.modules.federation.infrastructure.discovery_models import (
    FederatedOffer,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    NodeBilateralLimit,
    NodeExposure,
    NodeTrustContract,
)
from cooperative_clearing.modules.federation.infrastructure.peer_models import (
    PeerProtocolExchange,
)
from cooperative_clearing.modules.federation.infrastructure.reservation_models import (
    PeerResourceReservation,
)
from cooperative_clearing.modules.journal.application.service import signer_from_settings
from cooperative_clearing.modules.journal.domain.crypto import (
    NodeSigner,
    canonicalize,
    verify_signature,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database


@pytest.mark.integration
async def test_signed_peer_search_is_replay_safe_and_target_bound() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"peer-protocol-{suffix}",
        blob_root=Path(f"/tmp/peer-protocol-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
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

        now = datetime.now(UTC).replace(microsecond=0)
        request = PeerRequest(
            message_id=uuid4(),
            source_node_code=peer.node_code,
            target_node_code=settings.node_code,
            operation=PeerOperation.CATALOG_SEARCH,
            signer_fingerprint=peer_signer.fingerprint,
            issued_at=now,
            expires_at=now + timedelta(seconds=60),
            payload={
                "product_code": "CABBAGE.WHITE",
                "quantity": "10.000",
                "unit_code": "KG",
                "valuation_unit": "COOP",
                "destination_region": "EAST-DISTRICT",
                "required_certificates": ["quality:demo-v1"],
                "quality_grade": "A",
                "maximum_goods_cost": "100.00",
                "latest_delivery": None,
                "top_k": 10,
            },
        )
        signature = peer_signer.sign(canonicalize(request.document()))
        service = PeerProtocolService(settings)
        async with database.session() as session:
            first = await service.handle(session, request=request, signature=signature)
            await session.commit()
        assert first.document["source_node_code"] == settings.node_code
        response_payload = first.document["payload"]
        assert isinstance(response_payload, dict)
        offers = response_payload["offers"]
        assert isinstance(offers, list) and len(offers) == 1
        local_signer = signer_from_settings(settings)
        assert verify_signature(
            local_signer.public_key_bytes,
            first.signature,
            canonicalize(first.document),
        )

        async with database.session() as session:
            replay = await service.handle(session, request=request, signature=signature)
            assert replay == first
            count = await session.scalar(
                select(func.count(PeerProtocolExchange.id)).where(
                    PeerProtocolExchange.direction == "INBOUND",
                    PeerProtocolExchange.message_id == request.message_id,
                )
            )
            assert count == 1

        tampered = PeerRequest(
            message_id=request.message_id,
            source_node_code=request.source_node_code,
            target_node_code=request.target_node_code,
            operation=request.operation,
            signer_fingerprint=request.signer_fingerprint,
            issued_at=request.issued_at,
            expires_at=request.expires_at,
            payload={**request.payload, "quantity": "11.000"},
        )
        async with database.session() as session:
            with pytest.raises(DomainError, match="PEER_REQUEST_TAMPERED_REPLAY"):
                await service.handle(
                    session,
                    request=tampered,
                    signature=peer_signer.sign(canonicalize(tampered.document())),
                )
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_peer_goods_reservation_is_capacity_safe_and_irreversible_after_commit() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"peer-reservation-{suffix}",
        blob_root=Path(f"/tmp/peer-reservation-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    peer_signer = NodeSigner.from_seed_hex(
        hashlib.sha256(b"demo-peer-node-signing-key").hexdigest()
    )
    service = PeerProtocolService(settings)
    limit_id = None
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
            peer.capabilities = sorted({*peer.capabilities, "CATALOG", "LOGISTICS"})
            peer.supported_protocols = sorted({*peer.supported_protocols, "CC-PEER-1"})
            contract.capabilities = sorted({*contract.capabilities, "CATALOG", "LOGISTICS"})
            offer = (
                await session.execute(
                    select(FederatedOffer).where(
                        FederatedOffer.external_node_id.is_(None),
                        FederatedOffer.product_code == "CABBAGE.WHITE",
                        FederatedOffer.status == "ACTIVE",
                    )
                )
            ).scalar_one()
            limit = (
                await session.execute(
                    select(NodeBilateralLimit).where(
                        NodeBilateralLimit.node_id == peer.id,
                        NodeBilateralLimit.status == "ACTIVE",
                        NodeBilateralLimit.capability != "CLEARING",
                    )
                )
            ).scalar_one()
            limit_id = limit.id
            limit.capability = "CATALOG"
            limit.unit = offer.valuation_unit
            limit.max_package_value = Decimal("100000")
            limit.max_unsettled_obligations = Decimal("100000")
            await session.commit()

        now = datetime.now(UTC).replace(microsecond=0)
        intent_id = uuid4()
        receipt_id = uuid4()
        amount = offer.minimum_batch
        reserve_payload: dict[str, object] = {
            "receipt_id": str(receipt_id),
            "purchase_intent_id": str(intent_id),
            "kind": "GOODS",
            "offer_id": str(offer.offer_id),
            "offer_version": offer.offer_version,
            "amount": format(amount, "f"),
            "unit_code": offer.unit_code,
            "requested_expires_at": (now + timedelta(minutes=20)).isoformat(),
            "summary_hash": "sha256:" + "1" * 64,
        }
        reserve_request = PeerRequest(
            message_id=uuid4(),
            source_node_code=peer.node_code,
            target_node_code=settings.node_code,
            operation=PeerOperation.GOODS_RESERVE,
            signer_fingerprint=peer_signer.fingerprint,
            issued_at=now,
            expires_at=now + timedelta(seconds=60),
            payload=reserve_payload,
        )
        reserve_signature = peer_signer.sign(canonicalize(reserve_request.document()))
        async with database.session() as session:
            reserved = await service.handle(
                session,
                request=reserve_request,
                signature=reserve_signature,
            )
            await session.commit()
        response_payload = reserved.document["payload"]
        assert isinstance(response_payload, dict)
        artifact = response_payload["reservation"]
        assert isinstance(artifact, dict)
        receipt_payload = artifact["payload"]
        assert isinstance(receipt_payload, dict)
        assert receipt_payload["buyer_node_code"] == peer.node_code
        assert receipt_payload["resource_ref"] == f"offer:{offer.offer_id}:{offer.offer_version}"

        async with database.session() as session:
            replay = await service.handle(
                session,
                request=reserve_request,
                signature=reserve_signature,
            )
            assert replay == reserved
            row = await session.get(PeerResourceReservation, receipt_id)
            assert row is not None and row.status == "ACTIVE"

        oversell_now = datetime.now(UTC).replace(microsecond=0)
        oversell_payload = {
            **reserve_payload,
            "receipt_id": str(uuid4()),
            "purchase_intent_id": str(uuid4()),
            "amount": format(offer.quantity_available, "f"),
        }
        oversell_request = PeerRequest(
            message_id=uuid4(),
            source_node_code=peer.node_code,
            target_node_code=settings.node_code,
            operation=PeerOperation.GOODS_RESERVE,
            signer_fingerprint=peer_signer.fingerprint,
            issued_at=oversell_now,
            expires_at=oversell_now + timedelta(seconds=60),
            payload=oversell_payload,
        )
        async with database.session() as session:
            with pytest.raises(DomainError, match="PEER_RESOURCE_CAPACITY_INSUFFICIENT"):
                await service.handle(
                    session,
                    request=oversell_request,
                    signature=peer_signer.sign(canonicalize(oversell_request.document())),
                )
            await session.rollback()

        commit_now = datetime.now(UTC).replace(microsecond=0)
        commit_request = PeerRequest(
            message_id=uuid4(),
            source_node_code=peer.node_code,
            target_node_code=settings.node_code,
            operation=PeerOperation.GOODS_COMMIT,
            signer_fingerprint=peer_signer.fingerprint,
            issued_at=commit_now,
            expires_at=commit_now + timedelta(seconds=60),
            payload={
                "receipt_id": str(receipt_id),
                "purchase_intent_id": str(intent_id),
                "kind": "GOODS",
                "receipt_hash": str(artifact["payload_hash"]),
                "summary_hash": reserve_payload["summary_hash"],
                "commit_request_hash": "sha256:" + "2" * 64,
            },
        )
        async with database.session() as session:
            committed = await service.handle(
                session,
                request=commit_request,
                signature=peer_signer.sign(canonicalize(commit_request.document())),
            )
            await session.commit()
        committed_payload = committed.document["payload"]
        assert isinstance(committed_payload, dict)
        assert committed_payload["status"] == "COMMITTED"

        release_now = datetime.now(UTC).replace(microsecond=0)
        release_request = PeerRequest(
            message_id=uuid4(),
            source_node_code=peer.node_code,
            target_node_code=settings.node_code,
            operation=PeerOperation.GOODS_RELEASE,
            signer_fingerprint=peer_signer.fingerprint,
            issued_at=release_now,
            expires_at=release_now + timedelta(seconds=60),
            payload={
                "receipt_id": str(receipt_id),
                "purchase_intent_id": str(intent_id),
                "kind": "GOODS",
                "receipt_hash": str(artifact["payload_hash"]),
                "summary_hash": reserve_payload["summary_hash"],
                "reason": "buyer cancelled",
            },
        )
        async with database.session() as session:
            with pytest.raises(
                DomainError,
                match="PEER_COMMITTED_RESERVATION_CANNOT_RELEASE",
            ):
                await service.handle(
                    session,
                    request=release_request,
                    signature=peer_signer.sign(canonicalize(release_request.document())),
                )
            await session.rollback()
            row = await session.get(PeerResourceReservation, receipt_id)
            assert row is not None and row.status == "COMMITTED"

        expiry_now = datetime.now(UTC).replace(microsecond=0)
        expiring_receipt_id = uuid4()
        expiring_payload = {
            **reserve_payload,
            "receipt_id": str(expiring_receipt_id),
            "purchase_intent_id": str(uuid4()),
            "requested_expires_at": (expiry_now + timedelta(seconds=1)).isoformat(),
        }
        expiring_request = PeerRequest(
            message_id=uuid4(),
            source_node_code=peer.node_code,
            target_node_code=settings.node_code,
            operation=PeerOperation.GOODS_RESERVE,
            signer_fingerprint=peer_signer.fingerprint,
            issued_at=expiry_now,
            expires_at=expiry_now + timedelta(seconds=60),
            payload=expiring_payload,
        )
        async with database.session() as session:
            await service.handle(
                session,
                request=expiring_request,
                signature=peer_signer.sign(canonicalize(expiring_request.document())),
            )
            await session.commit()
        await asyncio.sleep(1.2)
        async with database.session() as session:
            expired = await expire_stale_reservations(session, settings=settings)
            await session.commit()
            assert expired.peer_reservations >= 1
        async with database.session() as session:
            row = await session.get(PeerResourceReservation, expiring_receipt_id)
            exposure = (
                await session.execute(
                    select(NodeExposure).where(
                        NodeExposure.node_id == peer.id,
                        NodeExposure.capability == "CATALOG",
                        NodeExposure.unit == offer.valuation_unit,
                    )
                )
            ).scalar_one()
            assert row is not None and row.status == "EXPIRED"
            assert row.expiry_event_id is not None
            assert exposure.reserved_amount == 0
    finally:
        if limit_id is not None:
            async with database.session() as session:
                limit = await session.get(NodeBilateralLimit, limit_id)
                if limit is not None:
                    limit.capability = "TEST_EXCHANGE"
                    limit.unit = "DEMO"
                    limit.max_package_value = Decimal("100")
                    limit.max_unsettled_obligations = Decimal("100")
                    await session.commit()
        await database.dispose()
