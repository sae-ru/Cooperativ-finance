"""Two-node federation lifecycle, preserved conflicts, and database evidence guards."""

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError

from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.modules.federation.application.demo import DEMO_NODE_CODE
from cooperative_clearing.modules.federation.application.lifecycle import NodeTrustService
from cooperative_clearing.modules.federation.application.service import rotation_message
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    FederationPaperForm,
    InboxEvent,
    NodeBond,
    NodeCertificate,
    NodeExposure,
    NodeKeyRotationRequest,
    NodeResponsibleParty,
    NodeTrustContract,
    OfflineEpoch,
    SyncConflict,
    SyncPackage,
    SyncReceipt,
)
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.journal.application.service import verify_journal
from cooperative_clearing.modules.journal.domain.crypto import NodeSigner
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.modules.node.infrastructure.models import NodeProfile
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database


@pytest.mark.integration
async def test_demo_federation_is_idempotent_bounded_and_preserves_both_histories() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"federation-flow-{suffix}",
        blob_root=Path(f"/tmp/federation-flow-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            event_count = int(
                await session.scalar(select(func.count()).select_from(SignedEvent)) or 0
            )
        await seed_demo(settings)
        async with database.session() as session:
            assert (
                int(await session.scalar(select(func.count()).select_from(SignedEvent)) or 0)
                == event_count
            )
            node = (
                await session.execute(
                    select(ExternalNode).where(ExternalNode.node_code == DEMO_NODE_CODE.lower())
                )
            ).scalar_one()
            contract = (
                await session.execute(
                    select(NodeTrustContract).where(NodeTrustContract.node_id == node.id)
                )
            ).scalar_one()
            responsibilities = list(
                (
                    await session.execute(
                        select(NodeResponsibleParty).where(NodeResponsibleParty.node_id == node.id)
                    )
                ).scalars()
            )
            bond = (
                await session.execute(select(NodeBond).where(NodeBond.node_id == node.id))
            ).scalar_one()
            exposure = (
                await session.execute(
                    select(NodeExposure).where(
                        NodeExposure.node_id == node.id,
                        NodeExposure.capability == "TEST_EXCHANGE",
                        NodeExposure.unit == "DEMO",
                    )
                )
            ).scalar_one()
            epoch = (
                await session.execute(
                    select(OfflineEpoch).where(OfflineEpoch.external_node_id == node.id)
                )
            ).scalar_one()
            paper_form = (
                await session.execute(
                    select(FederationPaperForm).where(
                        FederationPaperForm.external_node_id == node.id
                    )
                )
            ).scalar_one()
            packages = list(
                (
                    await session.execute(
                        select(SyncPackage)
                        .where(SyncPackage.peer_node_id == node.id)
                        .order_by(SyncPackage.sequence_first)
                    )
                ).scalars()
            )
            conflict = (
                await session.execute(
                    select(SyncConflict).where(SyncConflict.package_id == packages[1].id)
                )
            ).scalar_one()
            histories = list(
                (
                    await session.execute(
                        select(InboxEvent)
                        .where(InboxEvent.source_node_id == node.id)
                        .order_by(InboxEvent.local_sequence)
                    )
                ).scalars()
            )
            receipts = list(
                (
                    await session.execute(
                        select(SyncReceipt).where(
                            SyncReceipt.package_id.in_([item.id for item in packages])
                        )
                    )
                ).scalars()
            )
            profile = (
                await session.execute(
                    select(NodeProfile).where(NodeProfile.node_code == settings.node_code)
                )
            ).scalar_one()
            integrity = await verify_journal(session, profile.id)
            authority_events = list(
                (
                    await session.execute(
                        select(SignedEvent).where(
                            SignedEvent.event_type.in_(
                                {
                                    "federation.node_application_created",
                                    "federation.node_responsibility_accepted",
                                    "federation.node_application_submitted",
                                    "federation.node_identity_verified",
                                    "federation.node_challenge_issued",
                                    "federation.node_challenge_passed",
                                    "federation.node_audit_approved",
                                    "federation.trust_contract_proposed",
                                    "federation.trust_contract_activated",
                                    "federation.bilateral_limit_proposed",
                                    "federation.bilateral_limit_activated",
                                    "federation.node_bond_activated",
                                    "federation.node_activated",
                                    "federation.offline_epoch_opened",
                                    "federation.node_exposure_reserved",
                                }
                            )
                        )
                    )
                ).scalars()
            )

            assert node.status == "ACTIVE"
            assert node.trust_level == "STANDARD"
            assert contract.status == "ACTIVE"
            assert contract.liability_terms["ordinary_member_shares_excluded"] is True
            assert {item.role_code for item in responsibilities} == {
                "OWNER_SIGNATORY",
                "TECHNICAL_CUSTODIAN",
                "SECURITY_ADMINISTRATOR",
                "BUSINESS_OPERATOR",
                "NODE_AUDITOR",
            }
            assert all(item.status == "ACTIVE" for item in responsibilities)
            assert bond.maximum_loss == bond.amount - bond.protected_amount
            assert exposure.current_amount + exposure.reserved_amount <= 100
            assert epoch.status == "OPEN"
            assert paper_form.status == "RECORDED"
            assert paper_form.serial_number == "DEMO-FED-PAPER-001"
            assert paper_form.recorded_by_member_id != paper_form.issued_by_member_id
            assert paper_form.payload_hash is not None
            assert paper_form.evidence_ids
            assert [item.status for item in packages] == ["APPLIED", "APPLIED"]
            assert conflict.status == "RESOLVED"
            assert conflict.decision == "KEEP_LOCAL"
            assert len(histories) == 2
            assert histories[0].aggregate_id == histories[1].aggregate_id
            assert histories[0].aggregate_version == histories[1].aggregate_version == 1
            assert histories[0].event_hash != histories[1].event_hash
            assert [item.status for item in histories] == ["APPLIED", "REJECTED"]
            assert len(receipts) == 2
            assert integrity.ok is True
            assured_events = [
                item for item in authority_events if "_command_assurance" in item.payload
            ]
            if any(
                item.event_type == "federation.node_application_created"
                for item in assured_events
            ):
                expected_types = {
                    "federation.node_application_created",
                    "federation.node_responsibility_accepted",
                    "federation.node_application_submitted",
                    "federation.node_identity_verified",
                    "federation.node_challenge_issued",
                    "federation.node_challenge_passed",
                    "federation.node_audit_approved",
                    "federation.trust_contract_proposed",
                    "federation.trust_contract_activated",
                    "federation.bilateral_limit_proposed",
                    "federation.bilateral_limit_activated",
                    "federation.node_bond_activated",
                    "federation.node_activated",
                    "federation.offline_epoch_opened",
                    "federation.node_exposure_reserved",
                }
                assert expected_types <= {item.event_type for item in assured_events}
                for item in assured_events:
                    assurance = item.payload["_command_assurance"]
                    assert assurance["format"] == "critical-command-assurance-v2"
                    assert assurance["on_behalf_of"] == {
                        "kind": "NODE",
                        "reference": settings.node_code,
                        "role_assignment_id": None,
                    }
                    assert str(node.id) in {
                        party["reference"] for party in assurance["next_responsible"]
                    }
                responsibility_assurances = [
                    item.payload["_command_assurance"]
                    for item in assured_events
                    if item.event_type == "federation.node_responsibility_accepted"
                ]
                assert len(responsibility_assurances) == 5
                assert {str(item.member_id) for item in responsibilities} <= {
                    party["reference"]
                    for assurance in responsibility_assurances
                    for party in assurance["next_responsible"]
                }
                by_type = {
                    item.event_type: item.payload["_command_assurance"] for item in assured_events
                }
                bond_exposure = by_type["federation.node_bond_activated"]["exposure"]
                assert Decimal(bond_exposure["amount"]) == Decimal("120")
                assert Decimal(bond_exposure["maximum_loss"]) == Decimal("100")
                assert bond_exposure["unit"] == "DEMO"
                reserve_exposure = by_type["federation.node_exposure_reserved"]["exposure"]
                assert Decimal(reserve_exposure["amount"]) == Decimal("25")
                assert reserve_exposure["unit"] == "DEMO"

        async with database.session() as session:
            receipt_id = await session.scalar(select(SyncReceipt.id).limit(1))
            assert receipt_id is not None
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(SyncReceipt)
                    .where(SyncReceipt.id == receipt_id)
                    .values(receipt_hash="sha256:" + "0" * 64)
                )
            await session.rollback()

        async with database.session() as session:
            paper_id = await session.scalar(select(FederationPaperForm.id).limit(1))
            assert paper_id is not None
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(FederationPaperForm)
                    .where(FederationPaperForm.id == paper_id)
                    .values(payload_hash="sha256:" + "0" * 64)
                )
            await session.rollback()

        async with database.session() as session:
            inbox_id = await session.scalar(select(InboxEvent.id).limit(1))
            assert inbox_id is not None
            with pytest.raises(DBAPIError):
                await session.execute(delete(InboxEvent).where(InboxEvent.id == inbox_id))
            await session.rollback()
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_node_incident_key_rotation_status_and_offline_authority_are_assured() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"node-authority-{suffix}",
        blob_root=Path(f"/tmp/node-authority-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    security = _node_principal("security", "demo-member-elena", RoleCode.NODE_SECURITY_ADMIN)
    auditor = _node_principal("auditor", "demo-member-pavel", RoleCode.NODE_AUDITOR)
    old_signer = NodeSigner.from_seed_hex(hashlib.sha256(b"demo-peer-node-signing-key").hexdigest())
    service = NodeTrustService(settings)
    try:
        async with database.session() as session:
            node = (
                await session.execute(
                    select(ExternalNode).where(ExternalNode.node_code == DEMO_NODE_CODE.lower())
                )
            ).scalar_one()
            epoch = (
                await session.execute(
                    select(OfflineEpoch).where(
                        OfflineEpoch.external_node_id == node.id,
                        OfflineEpoch.status == "OPEN",
                    )
                )
            ).scalar_one()
            bond = (
                await session.execute(select(NodeBond).where(NodeBond.node_id == node.id))
            ).scalar_one()
            evidence_id = UUID(bond.evidence_ids[0])

            results = []
            results.append(
                await service.close_offline_epoch(
                    session,
                    principal=auditor,
                    epoch_id=epoch.id,
                    expected_version=epoch.version,
                    reconciliation={"paper_forms": "RECONCILED", "packages": "RECONCILED"},
                    idempotency_key=f"{suffix}-offline-close",
                    request_id=None,
                )
            )

            old = (
                await session.execute(
                    select(NodeCertificate).where(
                        NodeCertificate.node_id == node.id,
                        NodeCertificate.status == "ACTIVE",
                    )
                )
            ).scalar_one()
            now = datetime.now(UTC)
            scheduled_signer = NodeSigner.from_seed_hex(
                hashlib.sha256(f"{suffix}-scheduled".encode()).hexdigest()
            )
            scheduled_start = now - timedelta(minutes=1)
            scheduled_end = now + timedelta(days=120)
            scheduled_message = rotation_message(
                node_id=node.id,
                old_fingerprint=old.fingerprint,
                new_fingerprint=scheduled_signer.fingerprint,
                reason="SCHEDULED",
                valid_from=scheduled_start,
                valid_until=scheduled_end,
            )
            scheduled_request = await service.request_key_rotation(
                session,
                principal=security,
                node_id=node.id,
                new_public_key=scheduled_signer.public_key_bytes,
                valid_from=scheduled_start,
                valid_until=scheduled_end,
                reason="SCHEDULED",
                old_signature=old_signer.sign(scheduled_message),
                new_signature=scheduled_signer.sign(scheduled_message),
                idempotency_key=f"{suffix}-scheduled-request",
                request_id=None,
            )
            results.append(scheduled_request)
            scheduled_rotation = await session.get(
                NodeKeyRotationRequest, scheduled_request.object_id
            )
            assert scheduled_rotation is not None
            results.append(
                await service.approve_key_rotation(
                    session,
                    principal=auditor,
                    rotation_id=scheduled_rotation.id,
                    expected_version=scheduled_rotation.version,
                    approve=False,
                    idempotency_key=f"{suffix}-scheduled-reject",
                    request_id=None,
                )
            )

            incident = await service.open_incident(
                session,
                principal=security,
                node_id=node.id,
                incident_type="KEY_COMPROMISE",
                severity="CRITICAL",
                earliest_compromise_at=now - timedelta(minutes=5),
                description="Controlled key-compromise continuity drill.",
                evidence_ids=[evidence_id],
                idempotency_key=f"{suffix}-incident-open",
                request_id=None,
            )
            results.append(incident)
            compromise_signer = NodeSigner.from_seed_hex(
                hashlib.sha256(f"{suffix}-compromise".encode()).hexdigest()
            )
            compromise_start = now - timedelta(minutes=1)
            compromise_end = now + timedelta(days=180)
            compromise_message = rotation_message(
                node_id=node.id,
                old_fingerprint=old.fingerprint,
                new_fingerprint=compromise_signer.fingerprint,
                reason="COMPROMISE",
                valid_from=compromise_start,
                valid_until=compromise_end,
            )
            compromise_request = await service.request_key_rotation(
                session,
                principal=security,
                node_id=node.id,
                new_public_key=compromise_signer.public_key_bytes,
                valid_from=compromise_start,
                valid_until=compromise_end,
                reason="COMPROMISE",
                old_signature=None,
                new_signature=compromise_signer.sign(compromise_message),
                idempotency_key=f"{suffix}-compromise-request",
                request_id=None,
            )
            results.append(compromise_request)
            compromise_rotation = await session.get(
                NodeKeyRotationRequest, compromise_request.object_id
            )
            assert compromise_rotation is not None
            results.append(
                await service.approve_key_rotation(
                    session,
                    principal=auditor,
                    rotation_id=compromise_rotation.id,
                    expected_version=compromise_rotation.version,
                    approve=True,
                    idempotency_key=f"{suffix}-compromise-approve",
                    request_id=None,
                )
            )
            results.append(
                await service.resolve_incident(
                    session,
                    principal=auditor,
                    incident_id=incident.object_id,
                    expected_version=1,
                    corrective_actions=[{"action": "ROTATE_KEY", "status": "DONE"}],
                    rationale="New key verified and old key contained.",
                    idempotency_key=f"{suffix}-incident-resolve",
                    request_id=None,
                )
            )
            results.append(
                await service.rehabilitate_node(
                    session,
                    principal=auditor,
                    node_id=node.id,
                    expected_version=node.version,
                    integrity_summary={"key_rotation": "PASS", "journal": "PASS"},
                    idempotency_key=f"{suffix}-rehabilitate-after-incident",
                    request_id=None,
                )
            )
            results.append(
                await service.change_node_status(
                    session,
                    principal=security,
                    node_id=node.id,
                    expected_version=node.version,
                    action="quarantine",
                    rationale="Controlled quarantine transition test.",
                    idempotency_key=f"{suffix}-quarantine",
                    request_id=None,
                )
            )
            results.append(
                await service.rehabilitate_node(
                    session,
                    principal=auditor,
                    node_id=node.id,
                    expected_version=node.version,
                    integrity_summary={"quarantine_review": "PASS"},
                    idempotency_key=f"{suffix}-rehabilitate-after-quarantine",
                    request_id=None,
                )
            )
            for action in ("suspend", "revoke"):
                results.append(
                    await service.change_node_status(
                        session,
                        principal=security,
                        node_id=node.id,
                        expected_version=node.version,
                        action=action,
                        rationale=f"Controlled {action} transition test.",
                        idempotency_key=f"{suffix}-{action}",
                        request_id=None,
                    )
                )

            event_ids = [item.event_id for item in results]
            events = list(
                (
                    await session.execute(
                        select(SignedEvent).where(SignedEvent.event_id.in_(event_ids))
                    )
                ).scalars()
            )
            expected_types = {
                "federation.offline_epoch_closed",
                "federation.node_key_rotation_requested",
                "federation.node_key_rotation_rejected",
                "federation.node_incident_opened",
                "federation.node_key_rotated",
                "federation.node_incident_resolved",
                "federation.node_rehabilitated_limited",
                "federation.node_quarantined",
                "federation.node_suspended",
                "federation.node_revoked",
            }
            assert expected_types == {item.event_type for item in events}
            by_type = {item.event_type: item.payload["_command_assurance"] for item in events}
            responsible_refs = {
                str(item.member_id)
                for item in (
                    await session.execute(
                        select(NodeResponsibleParty).where(
                            NodeResponsibleParty.node_id == node.id,
                            NodeResponsibleParty.status == "ACTIVE",
                        )
                    )
                ).scalars()
            }
            for assurance in by_type.values():
                assert assurance["format"] == "critical-command-assurance-v2"
                assert assurance["on_behalf_of"]["kind"] == "NODE"
                assert assurance["on_behalf_of"]["reference"] == settings.node_code
                next_refs = {party["reference"] for party in assurance["next_responsible"]}
                assert str(node.id) in next_refs
                assert responsible_refs <= next_refs
            rotated = by_type["federation.node_key_rotated"]
            assert str(security.member_id) in {party["reference"] for party in rotated["attesters"]}
            assert str(auditor.member_id) in {party["reference"] for party in rotated["approvers"]}
            assert by_type["federation.node_revoked"]["exposure"]["effect"] == "REVOKE"
            await session.rollback()
    finally:
        await database.dispose()


def _node_principal(login: str, member_key: str, role: RoleCode) -> Principal:
    return Principal(
        user_id=stable_id("bootstrap-user", login),
        session_id=uuid4(),
        login=login,
        member_id=stable_id("member", member_key),
        must_change_password=False,
        roles=(
            RoleGrant(
                stable_id("bootstrap-role", f"{login}:{role.value}"),
                role,
                None,
            ),
        ),
    )
