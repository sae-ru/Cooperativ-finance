"""Commit-certificate finality and idempotent local apply against PostgreSQL."""

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.modules.federation.application.clearing_coordinator import (
    FederatedClearingCoordinator,
)
from cooperative_clearing.modules.federation.application.common import federation_actor
from cooperative_clearing.modules.federation.application.demo import DEMO_NODE_CODE
from cooperative_clearing.modules.federation.application.inter_node_clearing import (
    InterNodeClearingService,
    SignedArtifact,
    expire_stale_federated_prepares,
)
from cooperative_clearing.modules.federation.domain.federated_clearing import (
    FederatedObligationInput,
    apply_receipt_payload,
    approval_payload,
    prepare_receipt_payload,
    snapshot_payload,
)
from cooperative_clearing.modules.federation.domain.peer_protocol import PeerOperation, PeerResponse
from cooperative_clearing.modules.federation.infrastructure.clearing_models import (
    FederatedClearingCycle,
    FederatedClearingPolicyRecord,
    FederatedClearingProof,
    FederatedObligationApplication,
    InterNodeObligation,
    NodeApplyReceipt,
    NodePrepareReceipt,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    NodeExposure,
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


class SignedClearingCommitTransport:
    def __init__(self, signer: NodeSigner, receipt_payload: dict[str, object]) -> None:
        self.signer = signer
        self.receipt_payload = receipt_payload
        self.calls = 0

    async def post(self, endpoint: str, body: dict[str, object]) -> dict[str, object]:
        assert endpoint == "https://peer.demo.invalid"
        assert body["operation"] == PeerOperation.CLEARING_COMMIT.value
        self.calls += 1
        request_document = {key: value for key, value in body.items() if key != "signature_base64"}
        artifact = {
            "payload": self.receipt_payload,
            "hash": self.receipt_payload["receipt_hash"],
            "signature_base64": base64.b64encode(
                self.signer.sign(canonicalize(self.receipt_payload))
            ).decode("ascii"),
            "signer_fingerprint": self.signer.fingerprint,
        }
        now = datetime.now(UTC).replace(microsecond=0)
        response = PeerResponse(
            message_id=UUID(str(body["message_id"])),
            request_hash=payload_hash(request_document),
            source_node_code=DEMO_NODE_CODE.lower(),
            target_node_code=str(body["source_node_code"]),
            operation=PeerOperation.CLEARING_COMMIT,
            signer_fingerprint=self.signer.fingerprint,
            signed_at=now,
            expires_at=now + timedelta(seconds=30),
            payload={"apply_receipt": artifact, "status": "APPLIED"},
        )
        document = response.document()
        return {
            **document,
            "signature_base64": base64.b64encode(self.signer.sign(canonicalize(document))).decode(
                "ascii"
            ),
        }


@pytest.mark.integration
async def test_two_node_clearing_certifies_applies_and_reconciles() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"federated-clearing-{suffix}",
        blob_root=Path(f"/tmp/federated-clearing-{suffix}"),
        demo_data_enabled=True,
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    service = InterNodeClearingService(settings)
    operator = _principal(settings, "registrar", "demo-member-anna", RoleCode.CLEARING_OPERATOR)
    controller = _principal(settings, "security", "demo-member-elena", RoleCode.CLEARING_CONTROLLER)
    finalizer = _principal(settings, "auditor", "demo-member-pavel", RoleCode.CLEARING_FINALIZER)
    peer_code = DEMO_NODE_CODE.lower()
    peer_signer = NodeSigner.from_seed_hex(
        hashlib.sha256(b"demo-peer-node-signing-key").hexdigest()
    )
    cycle_id = uuid4()
    try:
        async with database.session() as session:
            operator_actor = await federation_actor(session, operator, {RoleCode.CLEARING_OPERATOR})
            controller_actor = await federation_actor(
                session, controller, {RoleCode.CLEARING_CONTROLLER}
            )
            finalizer_actor = await federation_actor(
                session, finalizer, {RoleCode.CLEARING_FINALIZER}
            )
            policy = (
                await session.execute(
                    select(FederatedClearingPolicyRecord).where(
                        FederatedClearingPolicyRecord.policy_code == "DEMO-REGIONAL-CLEARING",
                        FederatedClearingPolicyRecord.status == "ACTIVE",
                    )
                )
            ).scalar_one()
            local_obligation = await service.register_obligation(
                session,
                actor=operator_actor,
                home_node_code=settings.node_code,
                debtor_node_code=settings.node_code,
                creditor_node_code=peer_code,
                unit_code="DEMO",
                amount=Decimal("10.00"),
                source_reference=f"TEST-SUPPLY-{suffix}",
                source_event_hash=payload_hash({"test": suffix, "amount": "10.00"}),
                liquidity_class="STANDARD",
            )
            await service.register_obligation(
                session,
                actor=operator_actor,
                home_node_code=settings.node_code,
                debtor_node_code=settings.node_code,
                creditor_node_code=peer_code,
                unit_code="OTHER",
                amount=Decimal("1.00"),
                source_reference=f"TEST-EXCLUDED-{suffix}",
                source_event_hash=payload_hash({"test": suffix, "unit": "OTHER"}),
                liquidity_class="STANDARD",
            )
            now = datetime.now(UTC).replace(microsecond=0)
            cycle = await service.create_cycle(
                session,
                user_id=operator.user_id,
                actor=operator_actor,
                cycle_id=cycle_id,
                cycle_code=f"TEST-{suffix}",
                coordinator_node_code=settings.node_code,
                policy=policy,
                period_start=now - timedelta(days=30),
                period_end=now + timedelta(minutes=1),
                participant_node_codes=(settings.node_code, peer_code),
            )
            local_snapshot = await service.create_local_snapshot(session, cycle_id=cycle.id)
            assert all(
                item["unit_code"] == policy.valuation_unit
                for item in local_snapshot.snapshot_payload["obligations"]
            )
            remote_obligation = FederatedObligationInput(
                obligation_id=str(uuid4()),
                home_node_code=peer_code,
                debtor_node_code=peer_code,
                creditor_node_code=settings.node_code,
                unit_code="DEMO",
                amount=Decimal("45.00"),
                version=1,
                liquidity_class="STANDARD",
                source_event_hash=payload_hash({"remote": suffix, "amount": "45.00"}),
            )
            remote_snapshot_payload = snapshot_payload(
                cycle_id=str(cycle.id),
                node_code=peer_code,
                obligations=(remote_obligation,),
                checkpoint_hash=payload_hash({"peer_checkpoint": suffix}),
                policy_hash=policy.policy_hash,
                signed_at=now,
                expires_at=now + timedelta(minutes=30),
            )
            remote_snapshot = _signed(remote_snapshot_payload, "snapshot_hash", peer_signer)
            await service.accept_snapshot(
                session, cycle_id=cycle.id, artifact=remote_snapshot, actor=operator_actor
            )
            preview = await service.calculate_preview(session, cycle_id=cycle.id)
            await service.begin_prepare(
                session, cycle_id=cycle.id, input_hash=preview.clearing.input_hash
            )
            await service.prepare_local(
                session,
                cycle_id=cycle.id,
                input_hash=preview.clearing.input_hash,
                snapshot_hash=local_snapshot.snapshot_hash,
            )
            remote_prepare_payload = prepare_receipt_payload(
                cycle_id=str(cycle.id),
                node_code=peer_code,
                input_hash=preview.clearing.input_hash,
                snapshot_hash=str(remote_snapshot_payload["snapshot_hash"]),
                obligation_versions={remote_obligation.obligation_id: 1},
                reserved_by_unit={"DEMO": remote_obligation.amount},
                expires_at=now + timedelta(minutes=15),
            )
            await service.accept_prepare_receipt(
                session,
                cycle_id=cycle.id,
                artifact=_signed(remote_prepare_payload, "receipt_hash", peer_signer),
                actor=operator_actor,
            )
            proposal = await service.create_proposal(session, cycle_id=cycle.id)
            local_approval = await service.approve_local(
                session, cycle_id=cycle.id, actor=controller_actor
            )
            remote_approval_payload = approval_payload(
                cycle_id=str(cycle.id),
                node_code=peer_code,
                input_hash=preview.clearing.input_hash,
                result_hash=proposal.result_hash,
                prepare_receipt_hash=str(remote_prepare_payload["receipt_hash"]),
                approved_at=now + timedelta(seconds=1),
            )
            await service.accept_approval(
                session,
                cycle_id=cycle.id,
                artifact=_signed(remote_approval_payload, "approval_hash", peer_signer),
                actor=operator_actor,
            )
            certificate = await service.certify(session, cycle_id=cycle.id)
            local_apply = await service.apply_local(session, cycle_id=cycle.id)
            repeated_local_apply = await service.apply_local(session, cycle_id=cycle.id)
            assert repeated_local_apply.id == local_apply.id

            remote_entry = next(
                entry
                for entry in preview.clearing.entries
                if entry.obligation_id == remote_obligation.obligation_id
            )
            remote_apply_payload = apply_receipt_payload(
                cycle_id=str(cycle.id),
                node_code=peer_code,
                certificate_hash=certificate.certificate_hash,
                applications=(
                    {
                        "obligation_id": remote_entry.obligation_id,
                        "amount_before": str(remote_entry.amount_before),
                        "cleared_amount": str(remote_entry.cleared_amount),
                        "amount_after": str(remote_entry.amount_after),
                    },
                ),
                applied_at=now + timedelta(seconds=2),
            )
            transport = SignedClearingCommitTransport(peer_signer, remote_apply_payload)
            coordinator = FederatedClearingCoordinator(settings, transport)
            recovery = await coordinator.recover(session, cycle_id=cycle.id, actor=finalizer_actor)
            repeated_recovery = await coordinator.recover(
                session, cycle_id=cycle.id, actor=finalizer_actor
            )
            proof = (
                await session.execute(
                    select(FederatedClearingProof).where(
                        FederatedClearingProof.cycle_id == cycle.id
                    )
                )
            ).scalar_one()
            await session.commit()

            assert recovery.status == "RECONCILED"
            assert {item.node_code: item.result_code for item in recovery.nodes} == {
                settings.node_code: "ALREADY_APPLIED",
                peer_code: "OK",
            }
            assert all(item.result_code == "ALREADY_APPLIED" for item in repeated_recovery.nodes)
            assert transport.calls == 1
            assert local_approval.node_code == settings.node_code
            assert proof.proof_payload["certificate_hash"] == certificate.certificate_hash

        async with database.session() as session:
            persisted_cycle = await session.get(FederatedClearingCycle, cycle_id)
            persisted_obligation = await session.get(InterNodeObligation, local_obligation.id)
            applications = list(
                (
                    await session.execute(
                        select(FederatedObligationApplication).where(
                            FederatedObligationApplication.cycle_id == cycle_id
                        )
                    )
                ).scalars()
            )
            receipts = list(
                (
                    await session.execute(
                        select(NodeApplyReceipt).where(NodeApplyReceipt.cycle_id == cycle_id)
                    )
                ).scalars()
            )
            peer = (
                await session.execute(
                    select(ExternalNode).where(ExternalNode.node_code == peer_code)
                )
            ).scalar_one()
            exposure = (
                await session.execute(
                    select(NodeExposure).where(
                        NodeExposure.node_id == peer.id,
                        NodeExposure.capability == "CLEARING",
                        NodeExposure.unit == "DEMO",
                    )
                )
            ).scalar_one()

            assert persisted_cycle is not None and persisted_cycle.status == "RECONCILED"
            assert persisted_obligation is not None
            assert persisted_obligation.status in {"PARTIALLY_CLEARED", "CLEARED"}
            assert persisted_obligation.prepared_cycle_id is None
            assert applications
            assert len(receipts) == 2
            assert exposure.reserved_amount == Decimal(0)
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_expired_prepare_unlocks_obligations_and_bilateral_exposure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"federated-clearing-expiry-{suffix}",
        blob_root=Path(f"/tmp/federated-clearing-expiry-{suffix}"),
        demo_data_enabled=True,
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    service = InterNodeClearingService(settings)
    operator = _principal(settings, "registrar", "demo-member-anna", RoleCode.CLEARING_OPERATOR)
    peer_code = DEMO_NODE_CODE.lower()
    peer_signer = NodeSigner.from_seed_hex(
        hashlib.sha256(b"demo-peer-node-signing-key").hexdigest()
    )
    cycle_id = uuid4()
    initial_now = datetime.now(UTC).replace(microsecond=0)

    class ControlledDateTime(datetime):
        current = initial_now

        @classmethod
        def now(cls, tz: object = None) -> datetime:
            if tz is None:
                return cls.current.replace(tzinfo=None)
            return cls.current

    try:
        monkeypatch.setattr(
            "cooperative_clearing.modules.federation.application.inter_node_clearing.datetime",
            ControlledDateTime,
        )
        async with database.session() as session:
            actor = await federation_actor(session, operator, {RoleCode.CLEARING_OPERATOR})
            policy = (
                await session.execute(
                    select(FederatedClearingPolicyRecord).where(
                        FederatedClearingPolicyRecord.policy_code == "DEMO-REGIONAL-CLEARING",
                        FederatedClearingPolicyRecord.status == "ACTIVE",
                    )
                )
            ).scalar_one()
            obligation = await service.register_obligation(
                session,
                actor=actor,
                home_node_code=settings.node_code,
                debtor_node_code=settings.node_code,
                creditor_node_code=peer_code,
                unit_code="DEMO",
                amount=Decimal("8.00"),
                source_reference=f"EXPIRY-{suffix}",
                source_event_hash=payload_hash({"expiry": suffix}),
                liquidity_class="STANDARD",
            )
            cycle = await service.create_cycle(
                session,
                user_id=operator.user_id,
                actor=actor,
                cycle_id=cycle_id,
                cycle_code=f"EXPIRY-{suffix}",
                coordinator_node_code=settings.node_code,
                policy=policy,
                period_start=initial_now - timedelta(days=1),
                period_end=initial_now + timedelta(minutes=1),
                participant_node_codes=(settings.node_code, peer_code),
            )
            local_snapshot = await service.create_local_snapshot(session, cycle_id=cycle.id)
            assert all(
                item["unit_code"] == policy.valuation_unit
                for item in local_snapshot.snapshot_payload["obligations"]
            )
            remote_obligation = FederatedObligationInput(
                obligation_id=str(uuid4()),
                home_node_code=peer_code,
                debtor_node_code=peer_code,
                creditor_node_code=settings.node_code,
                unit_code="DEMO",
                amount=Decimal("9.00"),
                version=1,
                liquidity_class="STANDARD",
                source_event_hash=payload_hash({"remote_expiry": suffix}),
            )
            remote_payload = snapshot_payload(
                cycle_id=str(cycle.id),
                node_code=peer_code,
                obligations=(remote_obligation,),
                checkpoint_hash=payload_hash({"expiry_checkpoint": suffix}),
                policy_hash=policy.policy_hash,
                signed_at=initial_now,
                expires_at=initial_now + timedelta(minutes=30),
            )
            await service.accept_snapshot(
                session,
                cycle_id=cycle.id,
                artifact=_signed(remote_payload, "snapshot_hash", peer_signer),
                actor=actor,
            )
            preview = await service.calculate_preview(session, cycle_id=cycle.id)
            await service.begin_prepare(
                session, cycle_id=cycle.id, input_hash=preview.clearing.input_hash
            )
            receipt = await service.prepare_local(
                session,
                cycle_id=cycle.id,
                input_hash=preview.clearing.input_hash,
                snapshot_hash=local_snapshot.snapshot_hash,
            )
            assert receipt.expires_at > ControlledDateTime.current
            await session.commit()

        ControlledDateTime.current = initial_now + timedelta(hours=1)
        async with database.session() as session:
            expired = await expire_stale_federated_prepares(
                session, settings=settings, batch_size=10
            )
            repeated = await expire_stale_federated_prepares(
                session, settings=settings, batch_size=10
            )
            await session.commit()
            assert expired.released_cycles == 1
            assert repeated.released_cycles == 0

        async with database.session() as session:
            persisted_cycle = await session.get(FederatedClearingCycle, cycle_id)
            persisted_obligation = await session.get(InterNodeObligation, obligation.id)
            persisted_receipt = (
                await session.execute(
                    select(NodePrepareReceipt).where(NodePrepareReceipt.cycle_id == cycle_id)
                )
            ).scalar_one()
            peer = (
                await session.execute(
                    select(ExternalNode).where(ExternalNode.node_code == peer_code)
                )
            ).scalar_one()
            exposure = (
                await session.execute(
                    select(NodeExposure).where(
                        NodeExposure.node_id == peer.id,
                        NodeExposure.capability == "CLEARING",
                        NodeExposure.unit == "DEMO",
                    )
                )
            ).scalar_one()

            assert persisted_cycle is not None and persisted_cycle.status == "PREPARE_EXPIRED"
            assert persisted_cycle.certificate_hash is None
            assert persisted_obligation is not None
            assert persisted_obligation.status == "CONFIRMED"
            assert persisted_obligation.prepared_cycle_id is None
            assert persisted_receipt.expires_at < ControlledDateTime.current
            assert exposure.reserved_amount == Decimal(0)
    finally:
        await database.dispose()


def _principal(settings: Settings, login: str, member: str, role: RoleCode) -> Principal:
    return Principal(
        user_id=stable_id("bootstrap-user", login),
        session_id=uuid4(),
        login=login,
        member_id=stable_id("member", member),
        must_change_password=False,
        roles=(
            RoleGrant(
                stable_id("demo-role", f"{login}:{role.value}"),
                role,
                stable_id("cooperative", settings.node_code),
            ),
        ),
    )


def _signed(payload: dict[str, object], hash_field: str, signer: NodeSigner) -> SignedArtifact:
    return SignedArtifact(
        payload=payload,
        artifact_hash=str(payload[hash_field]),
        signature=signer.sign(canonicalize(payload)),
        signer_fingerprint=signer.fingerprint,
    )
