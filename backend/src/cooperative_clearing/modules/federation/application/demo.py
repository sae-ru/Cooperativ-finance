"""Deterministic two-node onboarding, offline exchange, and conflict demo."""

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.federation.application.lifecycle import NodeTrustService
from cooperative_clearing.modules.federation.application.paper import PaperFormService
from cooperative_clearing.modules.federation.application.service import (
    FederationService,
    ResponsiblePartyInput,
    challenge_message,
)
from cooperative_clearing.modules.federation.application.sync import SyncService
from cooperative_clearing.modules.federation.domain.package import build_package_archive
from cooperative_clearing.modules.federation.domain.types import (
    ConflictDecision,
    NodeCapability,
    ResponsibleRole,
    TrustLevel,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    FederationPaperForm,
    NodeApplication,
    NodeBilateralLimit,
    NodeExposure,
    NodeResponsibleParty,
    NodeTrustContract,
    OfflineEpoch,
    SyncConflict,
    SyncPackage,
)
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.journal.application.service import ActorClaim, build_envelope
from cooperative_clearing.modules.journal.domain.crypto import (
    NodeSigner,
    canonicalize,
    payload_hash,
    sha256_ref,
    utc_timestamp,
)
from cooperative_clearing.modules.node.infrastructure.models import NodeProfile
from cooperative_clearing.shared.core.config import Settings

DEMO_NODE_CODE = "DEMO-PEER-01"


async def seed_demo_federation(session: AsyncSession, settings: Settings) -> None:
    node_id = stable_id("node", DEMO_NODE_CODE.lower())
    if await session.get(ExternalNode, node_id) is not None:
        return

    now = datetime.now(UTC)
    anna_id = stable_id("member", "demo-member-anna")
    elena_id = stable_id("member", "demo-member-elena")
    pavel_id = stable_id("member", "demo-member-pavel")
    registrar = _principal("registrar", anna_id, RoleCode.NODE_REGISTRAR)
    owner = _principal("registrar", anna_id, RoleCode.NODE_BUSINESS_OPERATOR)
    technical = _principal("security", elena_id, RoleCode.NODE_TECHNICAL_CUSTODIAN)
    security = _principal("security", elena_id, RoleCode.NODE_SECURITY_ADMIN)
    auditor = _principal("auditor", pavel_id, RoleCode.NODE_AUDITOR)
    signer = NodeSigner.from_seed_hex(hashlib.sha256(b"demo-peer-node-signing-key").hexdigest())
    evidence_id = stable_id("evidence", "demo-federation-node-bond")
    service = FederationService(settings)
    trust = NodeTrustService(settings)

    responsibility_inputs = (
        _responsibility(
            anna_id,
            "registrar",
            RoleCode.NODE_BUSINESS_OPERATOR,
            ResponsibleRole.OWNER_SIGNATORY,
        ),
        _responsibility(
            elena_id,
            "security",
            RoleCode.NODE_TECHNICAL_CUSTODIAN,
            ResponsibleRole.TECHNICAL_CUSTODIAN,
        ),
        _responsibility(
            elena_id,
            "security",
            RoleCode.NODE_SECURITY_ADMIN,
            ResponsibleRole.SECURITY_ADMINISTRATOR,
        ),
        _responsibility(
            anna_id,
            "registrar",
            RoleCode.NODE_BUSINESS_OPERATOR,
            ResponsibleRole.BUSINESS_OPERATOR,
        ),
        _responsibility(
            pavel_id,
            "auditor",
            RoleCode.NODE_AUDITOR,
            ResponsibleRole.NODE_AUDITOR,
        ),
    )
    application_result = await service.create_application(
        session,
        principal=registrar,
        node_code=DEMO_NODE_CODE,
        display_name="Demo Regional Cooperative Node",
        owner_legal_name="Demo Regional Cooperative",
        owner_registration_code="DEMO-REG-001",
        owner_jurisdiction="DEMO-JURISDICTION",
        owner_contact_payload={"channel": "offline-duty-desk", "verified": True},
        territory="Demo western district",
        purpose="Controlled offline exchange and recovery drill.",
        network_endpoints=[{"transport": "HTTPS", "uri": "https://peer.demo.invalid"}],
        hardware_manifest={"platform": "linux-amd64", "tpm": True, "disk_encryption": True},
        release_manifest={"release": settings.release, "image_verified": True},
        capabilities=(NodeCapability.TEST_EXCHANGE, NodeCapability.CLEARING),
        supported_protocols=["1.0", "CC-PEER-1"],
        supported_policies={"federation": 1, "identity": 1},
        data_scopes={"catalog": "demo-only", "personal_data": False},
        requested_limits={
            "TEST_EXCHANGE": {"unit": "DEMO", "maximum": "100"},
            "CLEARING": {"unit": "DEMO", "maximum": "100"},
        },
        recovery_contacts=[{"role": "SECURITY_ADMINISTRATOR", "channel": "paper-roster"}],
        security_questionnaire={"backup_tested": True, "dual_control": True},
        evidence_ids=[evidence_id],
        responsible_parties=responsibility_inputs,
        public_key=signer.public_key_bytes,
        certificate_valid_from=now - timedelta(minutes=5),
        certificate_valid_until=now + timedelta(days=365),
        proposed_trust_expiry=now + timedelta(days=365),
        idempotency_key="demo-federation-application-v1",
        request_id=None,
    )
    application_id = application_result.object_id
    application = await session.get(NodeApplication, application_id)
    if application is None:
        raise RuntimeError("demo federation application was not persisted")
    node_id = application.node_id

    parties = list(
        (
            await session.execute(
                select(NodeResponsibleParty).where(
                    NodeResponsibleParty.application_id == application_id
                )
            )
        ).scalars()
    )
    principals = {
        ResponsibleRole.OWNER_SIGNATORY.value: owner,
        ResponsibleRole.TECHNICAL_CUSTODIAN.value: technical,
        ResponsibleRole.SECURITY_ADMINISTRATOR.value: security,
        ResponsibleRole.BUSINESS_OPERATOR.value: owner,
        ResponsibleRole.NODE_AUDITOR.value: auditor,
    }
    for party in parties:
        await service.accept_responsibility(
            session,
            principal=principals[party.role_code],
            application_id=application_id,
            responsibility_id=party.id,
            idempotency_key=f"demo-federation-responsibility-{party.role_code.lower()}-v1",
            request_id=None,
        )
    await service.submit_application(
        session,
        principal=registrar,
        application_id=application_id,
        expected_version=1,
        idempotency_key="demo-federation-submit-v1",
        request_id=None,
    )
    await service.verify_identity(
        session,
        principal=security,
        application_id=application_id,
        expected_version=2,
        verification_summary=(
            "Owner registry, signatories, hardware custody, and recovery roster verified."
        ),
        idempotency_key="demo-federation-identity-v1",
        request_id=None,
    )
    challenge = await service.issue_challenge(
        session,
        principal=security,
        application_id=application_id,
        expected_version=3,
        protocol_version="1.0",
        idempotency_key="demo-federation-challenge-v1",
        request_id=None,
    )
    response_payload: dict[str, object] = {
        "release_manifest": {"release": settings.release, "verified": True},
        "capability_statement": [
            NodeCapability.TEST_EXCHANGE.value,
            NodeCapability.CLEARING.value,
        ],
        "integrity_report": {"journal": "PASS", "storage": "PASS"},
        "test_package_receipt": {"status": "PASS", "events": 1},
    }
    await service.record_challenge_response(
        session,
        principal=registrar,
        challenge_id=challenge.result.object_id,
        nonce=challenge.nonce,
        response_payload=response_payload,
        signature=signer.sign(
            challenge_message(challenge.result.object_id, challenge.nonce, response_payload)
        ),
        idempotency_key="demo-federation-challenge-response-v1",
        request_id=None,
    )
    await service.decide_audit(
        session,
        principal=auditor,
        application_id=application_id,
        expected_version=5,
        approve=True,
        rationale="Independent review confirms the bounded demo trust scope.",
        idempotency_key="demo-federation-audit-v1",
        request_id=None,
    )

    contract_result = await trust.propose_trust_contract(
        session,
        principal=registrar,
        application_id=application_id,
        contract_number="DEMO-TRUST-001",
        trust_level=TrustLevel.STANDARD,
        capabilities=(NodeCapability.TEST_EXCHANGE, NodeCapability.CLEARING),
        event_types=[
            "federation.test_event",
            "federation.paper_form_issued",
            "federation.paper_operation_recorded",
            "federation.paper_form_voided",
        ],
        inbound_scope={"mode": "quarantine-then-simulate"},
        outbound_scope={"mode": "explicit-export"},
        federation_limits={"package_events": 100, "maximum_value": "100"},
        allowed_counterparties=[settings.node_code],
        max_offline_hours=24,
        required_protocols=["1.0"],
        required_policies={"federation": 1, "identity": 1},
        service_levels={"incident_notice_minutes": 30},
        liability_terms={
            "node_bond_required": True,
            "ordinary_member_shares_excluded": True,
            "maximum_loss": "100",
        },
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(days=180),
        idempotency_key="demo-federation-contract-v1",
        request_id=None,
    )
    contract = await session.get(NodeTrustContract, contract_result.object_id)
    if contract is None:
        raise RuntimeError("demo federation contract was not persisted")
    await trust.approve_trust_contract(
        session,
        principal=auditor,
        contract_id=contract.id,
        expected_version=1,
        terms_hash=contract.terms_hash,
        idempotency_key="demo-federation-contract-approval-v1",
        request_id=None,
    )
    limit_result = await trust.propose_bilateral_limit(
        session,
        principal=registrar,
        node_id=node_id,
        capability=NodeCapability.TEST_EXCHANGE,
        unit="DEMO",
        max_package_value=Decimal("100"),
        max_unsettled_obligations=Decimal("100"),
        max_external_rights=Decimal("0"),
        max_clearing_position=Decimal("0"),
        max_offline_hours=24,
        allowed_critical_resources=[],
        required_confirmations=2,
        idempotency_key="demo-federation-limit-v1",
        request_id=None,
    )
    limit = await session.get(NodeBilateralLimit, limit_result.object_id)
    if limit is None:
        raise RuntimeError("demo federation limit was not persisted")
    await trust.approve_bilateral_limit(
        session,
        principal=security,
        limit_id=limit.id,
        expected_version=1,
        terms_hash=limit.terms_hash,
        idempotency_key="demo-federation-limit-approval-v1",
        request_id=None,
    )
    clearing_limit_result = await trust.propose_bilateral_limit(
        session,
        principal=registrar,
        node_id=node_id,
        capability=NodeCapability.CLEARING,
        unit="DEMO",
        max_package_value=Decimal("0"),
        max_unsettled_obligations=Decimal("100"),
        max_external_rights=Decimal("0"),
        max_clearing_position=Decimal("100"),
        max_offline_hours=1,
        allowed_critical_resources=[],
        required_confirmations=2,
        idempotency_key="demo-federation-clearing-limit-v1",
        request_id=None,
    )
    clearing_limit = await session.get(NodeBilateralLimit, clearing_limit_result.object_id)
    if clearing_limit is None:
        raise RuntimeError("demo federation clearing limit was not persisted")
    await trust.approve_bilateral_limit(
        session,
        principal=security,
        limit_id=clearing_limit.id,
        expected_version=1,
        terms_hash=clearing_limit.terms_hash,
        idempotency_key="demo-federation-clearing-limit-approval-v1",
        request_id=None,
    )
    session.add(
        NodeExposure(
            id=stable_id("demo-node-exposure", "clearing"),
            node_id=node_id,
            capability=NodeCapability.CLEARING.value,
            unit="DEMO",
            current_amount=Decimal("0"),
            reserved_amount=Decimal("0"),
            updated_event_id=clearing_limit.approved_event_id,
        )
    )
    await trust.register_bond(
        session,
        principal=security,
        node_id=node_id,
        reference="DEMO-NODE-BOND-001",
        amount=Decimal("120"),
        protected_amount=Decimal("20"),
        maximum_loss=Decimal("100"),
        unit="DEMO",
        capability_scope=(NodeCapability.TEST_EXCHANGE, NodeCapability.CLEARING),
        evidence_ids=[evidence_id],
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(days=180),
        idempotency_key="demo-federation-bond-v1",
        request_id=None,
    )
    node = await session.get(ExternalNode, node_id)
    if node is None:
        raise RuntimeError("demo federation node was not persisted")
    await trust.activate_node(
        session,
        principal=registrar,
        node_id=node.id,
        expected_version=node.version,
        idempotency_key="demo-federation-activate-v1",
        request_id=None,
    )
    await trust.reserve_exposure(
        session,
        principal=owner,
        node_id=node.id,
        capability=NodeCapability.TEST_EXCHANGE,
        unit="DEMO",
        delta=Decimal("25"),
        reference="DEMO-OFFLINE-WINDOW-001",
        idempotency_key="demo-federation-exposure-v1",
        request_id=None,
    )
    epoch_result = await trust.open_offline_epoch(
        session,
        principal=security,
        node_id=node.id,
        base_checkpoint_hash=None,
        allowed_event_types=[
            "federation.test_event",
            "federation.paper_form_issued",
            "federation.paper_operation_recorded",
            "federation.paper_form_voided",
        ],
        limits={"maximum_events": 10, "maximum_value": "25", "unit": "DEMO"},
        protocol_version="1.0",
        policy_versions={"federation": 1, "identity": 1},
        emergency_contacts=[{"role": "SECURITY_ADMINISTRATOR", "channel": "paper-roster"}],
        closure_rules={"dual_review": True, "physical_reconciliation": True},
        starts_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=12),
        idempotency_key="demo-federation-epoch-v1",
        request_id=None,
    )
    epoch = await session.get(OfflineEpoch, epoch_result.object_id)
    local_node = await session.scalar(
        select(NodeProfile).where(NodeProfile.node_code == settings.node_code)
    )
    if epoch is None or local_node is None:
        raise RuntimeError("demo offline epoch was not persisted")

    paper = PaperFormService(settings)
    form_result = await paper.issue(
        session,
        principal=owner,
        epoch_id=epoch.id,
        serial_number="DEMO-FED-PAPER-001",
        form_type="GOODS_TRANSFER",
        form_version=1,
        participant_refs=["DEMO-MEMBER-ANNA", "DEMO-PEER-COUNTERPARTY"],
        operation_constraints={
            "maximum_value": "25",
            "unit": "DEMO",
            "requires_physical_reconciliation": True,
        },
        expires_at=now + timedelta(hours=2),
        idempotency_key="demo-federation-paper-issue-v1",
        request_id=None,
    )
    paper_form = await session.get(FederationPaperForm, form_result.object_id)
    if paper_form is None:
        raise RuntimeError("demo federation paper form was not persisted")
    await paper.record(
        session,
        principal=auditor,
        form_id=paper_form.id,
        expected_version=paper_form.version,
        checksum=paper_form.checksum,
        operation_payload={
            "resource": "DEMO-CABBAGE",
            "quantity": "5",
            "unit": "DEMO",
            "handoff_at": utc_timestamp(now),
        },
        signatures=[
            {"party_ref": "DEMO-MEMBER-ANNA", "kind": "WET_INK"},
            {"party_ref": "DEMO-PEER-COUNTERPARTY", "kind": "WET_INK"},
        ],
        evidence_ids=[evidence_id],
        idempotency_key="demo-federation-paper-record-v1",
        request_id=None,
    )

    sync = SyncService(settings)
    aggregate_id = stable_id("demo-federation-aggregate", "controlled-conflict")
    first_archive, first_hash = _remote_archive(
        signer=signer,
        node_id=node.id,
        node_code=node.node_code,
        local_node=local_node,
        contract=contract,
        epoch=epoch,
        sequence=1,
        previous_hash=None,
        aggregate_id=aggregate_id,
        value="accepted-branch",
        now=now,
    )
    first_result = await sync.import_package(
        session,
        principal=registrar,
        archive=first_archive,
        idempotency_key="demo-federation-import-safe-v1",
        request_id=None,
    )
    first_package = await session.get(SyncPackage, first_result.object_id)
    if first_package is None:
        raise RuntimeError("demo safe package was not persisted")
    await sync.apply_package(
        session,
        principal=auditor,
        package_id=first_package.id,
        expected_version=1,
        manifest_hash=first_package.manifest_hash,
        idempotency_key="demo-federation-apply-safe-v1",
        request_id=None,
    )

    second_archive, _ = _remote_archive(
        signer=signer,
        node_id=node.id,
        node_code=node.node_code,
        local_node=local_node,
        contract=contract,
        epoch=epoch,
        sequence=2,
        previous_hash=first_hash,
        aggregate_id=aggregate_id,
        value="competing-branch",
        now=now + timedelta(seconds=1),
    )
    second_result = await sync.import_package(
        session,
        principal=registrar,
        archive=second_archive,
        idempotency_key="demo-federation-import-conflict-v1",
        request_id=None,
    )
    conflict = await session.scalar(
        select(SyncConflict).where(SyncConflict.package_id == second_result.object_id)
    )
    if conflict is None:
        raise RuntimeError("demo controlled conflict was not detected")
    await sync.resolve_conflict(
        session,
        principal=auditor,
        conflict_id=conflict.id,
        expected_version=1,
        decision=ConflictDecision.KEEP_LOCAL,
        rationale="Keep the already applied branch; preserve the competing signed history.",
        evidence_ids=[],
        idempotency_key="demo-federation-conflict-resolution-v1",
        request_id=None,
    )
    second_package = await session.get(SyncPackage, second_result.object_id)
    if second_package is None:
        raise RuntimeError("demo conflict package was not persisted")
    await sync.apply_package(
        session,
        principal=auditor,
        package_id=second_package.id,
        expected_version=second_package.version,
        manifest_hash=second_package.manifest_hash,
        idempotency_key="demo-federation-apply-conflict-v1",
        request_id=None,
    )


def _responsibility(
    member_id: UUID,
    login: str,
    assignment_role: RoleCode,
    responsibility_role: ResponsibleRole,
) -> ResponsiblePartyInput:
    return ResponsiblePartyInput(
        member_id=member_id,
        role_assignment_id=stable_id("bootstrap-role", f"{login}:{assignment_role.value}"),
        role_code=responsibility_role,
        capability_scope=(NodeCapability.TEST_EXCHANGE, NodeCapability.CLEARING),
        responsibility_scope=f"Named personal responsibility for {responsibility_role.value}.",
        max_exposure=Decimal("100"),
        exposure_unit="DEMO",
        valid_until=None,
    )


def _principal(login: str, member_id: UUID, role: RoleCode) -> Principal:
    assignment_id = stable_id("bootstrap-role", f"{login}:{role.value}")
    return Principal(
        user_id=stable_id("bootstrap-user", login),
        session_id=stable_id("demo-session", f"{login}:{role.value}:federation"),
        login=login,
        member_id=member_id,
        must_change_password=False,
        roles=(RoleGrant(assignment_id, role, None),),
    )


def _remote_archive(
    *,
    signer: NodeSigner,
    node_id: UUID,
    node_code: str,
    local_node: NodeProfile,
    contract: NodeTrustContract,
    epoch: OfflineEpoch,
    sequence: int,
    previous_hash: str | None,
    aggregate_id: UUID,
    value: str,
    now: datetime,
) -> tuple[bytes, str]:
    event_id = uuid4()
    payload: dict[str, object] = {"value": value, "financial_effect": False}
    envelope = build_envelope(
        event_id=event_id,
        event_type="federation.test_event",
        schema_version=1,
        node_id=node_id,
        local_sequence=sequence,
        aggregate_type="federation_test_record",
        aggregate_id=aggregate_id,
        aggregate_version=1,
        actor=ActorClaim(
            person_id=stable_id("demo-remote-member", "operator"),
            organization_id=None,
            role_assignment_id=stable_id("demo-remote-role", "operator"),
        ),
        occurred_at=now,
        payload=payload,
        evidence=[],
        previous_event_hash=previous_hash,
        payload_digest=payload_hash(payload),
        offline_epoch_id=epoch.id,
    )
    canonical = canonicalize(envelope)
    event_hash = sha256_ref(canonical)
    wrapper: dict[str, object] = {
        "envelope": envelope,
        "event_hash": event_hash,
        "key_fingerprint": signer.fingerprint,
        "signature": base64.b64encode(signer.sign(canonical)).decode(),
    }
    if epoch.expires_at is None:
        raise RuntimeError("demo offline epoch must have an expiry")
    manifest = {
        "package_id": str(uuid4()),
        "source_node_code": node_code,
        "source_node_id": str(node_id),
        "target_node_code": local_node.node_code,
        "target_node_id": str(local_node.id),
        "created_at": utc_timestamp(now),
        "expires_at": utc_timestamp(min(now + timedelta(hours=6), epoch.expires_at)),
        "protocol_version": "1.0",
        "sequence_first": sequence,
        "sequence_last": sequence,
        "base_checkpoint_hash": previous_hash,
        "event_count": 1,
        "blob_count": 0,
        "required_capabilities": [NodeCapability.TEST_EXCHANGE.value],
        "contract_id": str(contract.id),
        "epoch_id": str(epoch.id),
        "epoch_policy_hash": epoch.policy_hash,
    }
    archive, _ = build_package_archive(
        manifest_base=manifest,
        events=[wrapper],
        certificate={
            "node_id": str(node_id),
            "fingerprint": signer.fingerprint,
            "algorithm": "Ed25519",
            "public_key": base64.b64encode(signer.public_key_bytes).decode(),
            "valid_from": utc_timestamp(contract.valid_from),
            "valid_until": utc_timestamp(contract.valid_until),
            "status": "ACTIVE",
        },
        revocations={"node_id": str(node_id), "revocations": []},
        signer=signer,
    )
    return archive, event_hash
