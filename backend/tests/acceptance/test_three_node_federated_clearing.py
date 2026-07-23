"""Three independent databases exchange signed clearing evidence over HTTP."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.clearing.domain.engine import RoundingMode
from cooperative_clearing.modules.federation.application.clearing_coordinator import (
    FederatedClearingCoordinator,
)
from cooperative_clearing.modules.federation.application.common import federation_actor
from cooperative_clearing.modules.federation.application.inter_node_clearing import (
    InterNodeClearingService,
)
from cooperative_clearing.modules.federation.application.lifecycle import NodeTrustService
from cooperative_clearing.modules.federation.application.service import (
    FederationService,
    ResponsiblePartyInput,
    challenge_message,
)
from cooperative_clearing.modules.federation.domain.federated_clearing import (
    FederatedClearingPolicy,
)
from cooperative_clearing.modules.federation.domain.types import (
    NodeCapability,
    ResponsibleRole,
    TrustLevel,
)
from cooperative_clearing.modules.federation.infrastructure.clearing_models import (
    FederatedClearingCycle,
    FederatedClearingPolicyRecord,
    InterNodeObligation,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    NodeApplication,
    NodeBilateralLimit,
    NodeExposure,
    NodeResponsibleParty,
    NodeTrustContract,
)
from cooperative_clearing.modules.federation.infrastructure.peer_models import (
    PeerProtocolExchange,
)
from cooperative_clearing.modules.identity.application.bootstrap import (
    seed_demo_identity,
    stable_id,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.journal.application.service import signer_from_settings
from cooperative_clearing.modules.journal.domain.crypto import payload_hash
from cooperative_clearing.shared.core.config import Environment, Settings
from cooperative_clearing.shared.infrastructure.database import Database

NODE_CODES = ("node-a", "node-b", "node-c")
ENDPOINTS = {code: f"http://{code}:8000" for code in NODE_CODES}


@pytest.mark.acceptance
async def test_three_nodes_recover_a_final_commit_after_peer_outage() -> None:
    settings = {code: _settings(code) for code in NODE_CODES}
    databases = {code: Database.from_settings(value) for code, value in settings.items()}
    obligation_ids: dict[str, UUID] = {}
    cycle_id: UUID | None = None
    try:
        for code in NODE_CODES:
            async with databases[code].session() as session:
                await seed_demo_identity(session, settings[code])
                await session.commit()

        for local_code in NODE_CODES:
            for remote_code in NODE_CODES:
                if remote_code == local_code:
                    continue
                async with databases[local_code].session() as session:
                    await _onboard_peer(
                        session,
                        local=settings[local_code],
                        remote=settings[remote_code],
                        endpoint=ENDPOINTS[remote_code],
                    )
                    await session.commit()

        policies: dict[str, FederatedClearingPolicyRecord] = {}
        for code in NODE_CODES:
            async with databases[code].session() as session:
                finalizer = _principal(
                    settings[code], "auditor", "demo-member-pavel", RoleCode.CLEARING_FINALIZER
                )
                actor = await federation_actor(session, finalizer, {RoleCode.CLEARING_FINALIZER})
                policies[code] = await InterNodeClearingService(settings[code]).create_policy(
                    session,
                    user_id=finalizer.user_id,
                    actor=actor,
                    policy_code="ACCEPTANCE-THREE-NODE",
                    valuation_unit="DEMO",
                    policy=FederatedClearingPolicy(
                        policy_version=1,
                        decimal_scale=2,
                        rounding_mode=RoundingMode.DOWN,
                        minimum_operation=Decimal("0.01"),
                        max_iterations=10_000,
                        max_cycle_length=8,
                        prepare_ttl_seconds=300,
                    ),
                )
                await session.commit()

        assert len({policy.policy_hash for policy in policies.values()}) == 1

        obligations = {
            "node-a": ("node-a", "node-b", Decimal("40.00")),
            "node-b": ("node-b", "node-c", Decimal("35.00")),
            "node-c": ("node-c", "node-a", Decimal("30.00")),
        }
        for code, (debtor, creditor, amount) in obligations.items():
            async with databases[code].session() as session:
                operator = _principal(
                    settings[code], "registrar", "demo-member-anna", RoleCode.CLEARING_OPERATOR
                )
                actor = await federation_actor(session, operator, {RoleCode.CLEARING_OPERATOR})
                row = await InterNodeClearingService(settings[code]).register_obligation(
                    session,
                    actor=actor,
                    home_node_code=code,
                    debtor_node_code=debtor,
                    creditor_node_code=creditor,
                    unit_code="DEMO",
                    amount=amount,
                    source_reference=f"ACCEPTANCE-{code.upper()}-SUPPLY",
                    source_event_hash=payload_hash(
                        {
                            "home_node": code,
                            "debtor": debtor,
                            "creditor": creditor,
                            "amount": str(amount),
                        }
                    ),
                    liquidity_class="STANDARD",
                )
                obligation_ids[code] = row.id
                await session.commit()

        coordinator_settings = settings["node-a"]
        coordinator_database = databases["node-a"]
        now = datetime.now(UTC).replace(microsecond=0)
        operator = _principal(
            coordinator_settings,
            "registrar",
            "demo-member-anna",
            RoleCode.CLEARING_OPERATOR,
        )
        finalizer = _principal(
            coordinator_settings,
            "auditor",
            "demo-member-pavel",
            RoleCode.CLEARING_FINALIZER,
        )
        async with coordinator_database.session() as session:
            actor = await federation_actor(session, operator, {RoleCode.CLEARING_OPERATOR})
            cycle = await InterNodeClearingService(coordinator_settings).create_cycle(
                session,
                user_id=operator.user_id,
                actor=actor,
                cycle_id=stable_id("acceptance-cycle", "three-node-01"),
                cycle_code="ACCEPTANCE-CYCLE-01",
                coordinator_node_code="node-a",
                policy=policies["node-a"],
                period_start=now - timedelta(days=1),
                period_end=now + timedelta(minutes=1),
                participant_node_codes=NODE_CODES,
            )
            cycle_id = cycle.id
            await session.commit()

        coordinator = FederatedClearingCoordinator(coordinator_settings)
        async with coordinator_database.session() as session:
            actor = await federation_actor(session, operator, {RoleCode.CLEARING_OPERATOR})
            snapshots = await coordinator.collect_snapshots(session, cycle_id=cycle_id, actor=actor)
            assert all(item.result_code == "OK" for item in snapshots.nodes), snapshots.nodes
            preview = await InterNodeClearingService(coordinator_settings).calculate_preview(
                session, cycle_id=cycle_id
            )
            expected_outstanding = {
                entry.obligation_id: entry.amount_after for entry in preview.clearing.entries
            }
            await session.commit()

        async with coordinator_database.session() as session:
            actor = await federation_actor(session, operator, {RoleCode.CLEARING_OPERATOR})
            prepared = await coordinator.prepare_nodes(session, cycle_id=cycle_id, actor=actor)
            assert prepared.status == "PREPARED"
            assert all(item.result_code == "OK" for item in prepared.nodes)
            proposed = await coordinator.publish_proposal(session, cycle_id=cycle_id)
            assert proposed.status == "PROPOSED"
            await session.commit()

        for code in NODE_CODES:
            async with databases[code].session() as session:
                controller = _principal(
                    settings[code],
                    "security",
                    "demo-member-elena",
                    RoleCode.CLEARING_CONTROLLER,
                )
                actor = await federation_actor(session, controller, {RoleCode.CLEARING_CONTROLLER})
                await InterNodeClearingService(settings[code]).approve_local(
                    session, cycle_id=cycle_id, actor=actor
                )
                await session.commit()

        async with coordinator_database.session() as session:
            actor = await federation_actor(session, operator, {RoleCode.CLEARING_OPERATOR})
            approvals = await coordinator.collect_approvals(session, cycle_id=cycle_id, actor=actor)
            assert all(item.result_code == "OK" for item in approvals.nodes)
            await session.commit()

        async with coordinator_database.session() as session:
            node_c = (
                await session.execute(
                    select(ExternalNode).where(ExternalNode.node_code == "node-c").with_for_update()
                )
            ).scalar_one()
            node_c.network_endpoints = [
                {"transport": "HTTP", "uri": "http://node-c-unavailable:8000"}
            ]
            await session.flush()
            actor = await federation_actor(session, finalizer, {RoleCode.CLEARING_FINALIZER})
            interrupted = await coordinator.certify_and_apply(
                session, cycle_id=cycle_id, actor=actor
            )
            assert interrupted.status == "COMMITTED_PENDING_APPLY"
            assert {item.node_code: item.result_code for item in interrupted.nodes} == {
                "node-a": "OK",
                "node-b": "OK",
                "node-c": "PEER_UNAVAILABLE",
            }
            await session.commit()

        async with coordinator_database.session() as session:
            node_c = (
                await session.execute(
                    select(ExternalNode).where(ExternalNode.node_code == "node-c").with_for_update()
                )
            ).scalar_one()
            node_c.network_endpoints = [{"transport": "HTTP", "uri": ENDPOINTS["node-c"]}]
            await session.commit()

        async with coordinator_database.session() as session:
            actor = await federation_actor(session, finalizer, {RoleCode.CLEARING_FINALIZER})
            recovered = await coordinator.recover(session, cycle_id=cycle_id, actor=actor)
            repeated = await coordinator.recover(session, cycle_id=cycle_id, actor=actor)
            assert recovered.status == "RECONCILED"
            assert {item.node_code: item.result_code for item in recovered.nodes} == {
                "node-a": "ALREADY_APPLIED",
                "node-b": "ALREADY_APPLIED",
                "node-c": "OK",
            }
            assert all(item.result_code == "ALREADY_APPLIED" for item in repeated.nodes)
            await session.commit()

        certificate_hashes: set[str] = set()
        for code in NODE_CODES:
            async with databases[code].session() as session:
                cycle = await session.get(FederatedClearingCycle, cycle_id)
                obligation = await session.get(InterNodeObligation, obligation_ids[code])
                exposures = list(
                    (
                        await session.execute(
                            select(NodeExposure).where(NodeExposure.capability == "CLEARING")
                        )
                    ).scalars()
                )
                assert cycle is not None and cycle.certificate_hash is not None
                certificate_hashes.add(cycle.certificate_hash)
                assert obligation is not None
                assert obligation.outstanding_amount == expected_outstanding[str(obligation.id)]
                assert obligation.prepared_cycle_id is None
                assert all(item.reserved_amount == 0 for item in exposures)

        assert len(certificate_hashes) == 1
        async with coordinator_database.session() as session:
            exchanges = list(
                (
                    await session.execute(
                        select(PeerProtocolExchange).where(
                            PeerProtocolExchange.operation.like("CLEARING_%")
                        )
                    )
                ).scalars()
            )
            assert any(
                item.operation == "CLEARING_COMMIT"
                and item.status == "FAILED"
                and item.error_code == "PEER_UNAVAILABLE"
                for item in exchanges
            )
            assert (
                sum(
                    item.operation == "CLEARING_COMMIT" and item.status == "SUCCEEDED"
                    for item in exchanges
                )
                == 2
            )
    finally:
        for database in databases.values():
            await database.dispose()


async def _onboard_peer(
    session: AsyncSession,
    *,
    local: Settings,
    remote: Settings,
    endpoint: str,
) -> None:
    existing = (
        await session.execute(
            select(ExternalNode).where(ExternalNode.node_code == remote.node_code)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return

    service = FederationService(local)
    trust = NodeTrustService(local)
    registrar = _principal(local, "registrar", "demo-member-anna", RoleCode.NODE_REGISTRAR)
    owner = _principal(local, "registrar", "demo-member-anna", RoleCode.NODE_BUSINESS_OPERATOR)
    technical = _principal(
        local, "security", "demo-member-elena", RoleCode.NODE_TECHNICAL_CUSTODIAN
    )
    security = _principal(local, "security", "demo-member-elena", RoleCode.NODE_SECURITY_ADMIN)
    auditor = _principal(local, "auditor", "demo-member-pavel", RoleCode.NODE_AUDITOR)
    signer = signer_from_settings(remote)
    now = datetime.now(UTC).replace(microsecond=0)
    prefix = f"acceptance-{local.node_code}-{remote.node_code}"
    evidence_id = stable_id("evidence", "demo-federation-node-bond")
    responsibilities = (
        _responsibility(owner, ResponsibleRole.OWNER_SIGNATORY),
        _responsibility(technical, ResponsibleRole.TECHNICAL_CUSTODIAN),
        _responsibility(security, ResponsibleRole.SECURITY_ADMINISTRATOR),
        _responsibility(owner, ResponsibleRole.BUSINESS_OPERATOR),
        _responsibility(auditor, ResponsibleRole.NODE_AUDITOR),
    )
    result = await service.create_application(
        session,
        principal=registrar,
        node_code=remote.node_code,
        display_name=remote.node_display_name,
        owner_legal_name=f"Acceptance owner {remote.node_code}",
        owner_registration_code=f"REG-{remote.node_code.upper()}",
        owner_jurisdiction=f"JUR-{remote.node_code.upper()}",
        owner_contact_payload={"channel": "acceptance-duty", "verified": True},
        territory="Acceptance network",
        purpose="Signed three-node clearing acceptance",
        network_endpoints=[{"transport": "HTTP", "uri": endpoint}],
        hardware_manifest={"platform": "linux-amd64", "disk_encryption": True},
        release_manifest={"release": remote.release, "image_verified": True},
        capabilities=(NodeCapability.CLEARING,),
        supported_protocols=["CC-PEER-1"],
        supported_policies={"federation": 1, "clearing": 1},
        data_scopes={"clearing": "contract-bound", "personal_data": False},
        requested_limits={"CLEARING": {"unit": "DEMO", "maximum": "1000"}},
        recovery_contacts=[{"role": "SECURITY_ADMINISTRATOR", "channel": "acceptance"}],
        security_questionnaire={"backup_tested": True, "dual_control": True},
        evidence_ids=[evidence_id],
        responsible_parties=responsibilities,
        public_key=signer.public_key_bytes,
        certificate_valid_from=now - timedelta(minutes=5),
        certificate_valid_until=now + timedelta(days=30),
        proposed_trust_expiry=now + timedelta(days=30),
        idempotency_key=f"{prefix}-application",
        request_id=None,
    )
    application = await session.get(NodeApplication, result.object_id)
    assert application is not None
    parties = list(
        (
            await session.execute(
                select(NodeResponsibleParty).where(
                    NodeResponsibleParty.application_id == application.id
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
            application_id=application.id,
            responsibility_id=party.id,
            idempotency_key=f"{prefix}-responsibility-{party.role_code.lower()}",
            request_id=None,
        )
    await service.submit_application(
        session,
        principal=registrar,
        application_id=application.id,
        expected_version=1,
        idempotency_key=f"{prefix}-submit",
        request_id=None,
    )
    await service.verify_identity(
        session,
        principal=security,
        application_id=application.id,
        expected_version=2,
        verification_summary="Acceptance identity and custody records verified.",
        idempotency_key=f"{prefix}-identity",
        request_id=None,
    )
    challenge = await service.issue_challenge(
        session,
        principal=security,
        application_id=application.id,
        expected_version=3,
        protocol_version="CC-PEER-1",
        idempotency_key=f"{prefix}-challenge",
        request_id=None,
    )
    response_payload: dict[str, object] = {
        "release_manifest": {"release": remote.release, "verified": True},
        "capability_statement": [NodeCapability.CLEARING.value],
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
        idempotency_key=f"{prefix}-challenge-response",
        request_id=None,
    )
    await service.decide_audit(
        session,
        principal=auditor,
        application_id=application.id,
        expected_version=5,
        approve=True,
        rationale="Independent acceptance audit passed.",
        idempotency_key=f"{prefix}-audit",
        request_id=None,
    )
    contract_result = await trust.propose_trust_contract(
        session,
        principal=registrar,
        application_id=application.id,
        contract_number=f"ACC-{local.node_code.upper()}-{remote.node_code.upper()}",
        trust_level=TrustLevel.STANDARD,
        capabilities=(NodeCapability.CLEARING,),
        event_types=["federation.clearing_snapshot_signed"],
        inbound_scope={"mode": "signed-clearing-only"},
        outbound_scope={"mode": "signed-clearing-only"},
        federation_limits={"maximum_value": "1000"},
        allowed_counterparties=[local.node_code],
        max_offline_hours=1,
        required_protocols=["CC-PEER-1"],
        required_policies={"federation": 1, "clearing": 1},
        service_levels={"incident_notice_minutes": 5},
        liability_terms={
            "node_bond_required": True,
            "ordinary_member_shares_excluded": True,
            "maximum_loss": "1000",
        },
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(days=30),
        idempotency_key=f"{prefix}-contract",
        request_id=None,
    )
    contract = await session.get(NodeTrustContract, contract_result.object_id)
    assert contract is not None
    await trust.approve_trust_contract(
        session,
        principal=auditor,
        contract_id=contract.id,
        expected_version=1,
        terms_hash=contract.terms_hash,
        idempotency_key=f"{prefix}-contract-approval",
        request_id=None,
    )
    limit_result = await trust.propose_bilateral_limit(
        session,
        principal=registrar,
        node_id=application.node_id,
        capability=NodeCapability.CLEARING,
        unit="DEMO",
        max_package_value=Decimal("0"),
        max_unsettled_obligations=Decimal("1000"),
        max_external_rights=Decimal("0"),
        max_clearing_position=Decimal("1000"),
        max_offline_hours=1,
        allowed_critical_resources=[],
        required_confirmations=2,
        idempotency_key=f"{prefix}-clearing-limit",
        request_id=None,
    )
    limit = await session.get(NodeBilateralLimit, limit_result.object_id)
    assert limit is not None
    await trust.approve_bilateral_limit(
        session,
        principal=security,
        limit_id=limit.id,
        expected_version=1,
        terms_hash=limit.terms_hash,
        idempotency_key=f"{prefix}-clearing-limit-approval",
        request_id=None,
    )
    session.add(
        NodeExposure(
            id=stable_id("acceptance-node-exposure", f"{local.node_code}:{remote.node_code}"),
            node_id=application.node_id,
            capability=NodeCapability.CLEARING.value,
            unit="DEMO",
            current_amount=Decimal("0"),
            reserved_amount=Decimal("0"),
            updated_event_id=limit.approved_event_id,
        )
    )
    await trust.register_bond(
        session,
        principal=security,
        node_id=application.node_id,
        reference=f"ACC-BOND-{local.node_code.upper()}-{remote.node_code.upper()}",
        amount=Decimal("1200"),
        protected_amount=Decimal("200"),
        maximum_loss=Decimal("1000"),
        unit="DEMO",
        capability_scope=(NodeCapability.CLEARING,),
        evidence_ids=[evidence_id],
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(days=30),
        idempotency_key=f"{prefix}-bond",
        request_id=None,
    )
    node = await session.get(ExternalNode, application.node_id)
    assert node is not None
    await trust.activate_node(
        session,
        principal=registrar,
        node_id=node.id,
        expected_version=node.version,
        idempotency_key=f"{prefix}-activate",
        request_id=None,
    )


def _responsibility(principal: Principal, role: ResponsibleRole) -> ResponsiblePartyInput:
    assert principal.member_id is not None
    return ResponsiblePartyInput(
        member_id=principal.member_id,
        role_assignment_id=principal.roles[0].assignment_id,
        role_code=role,
        capability_scope=(NodeCapability.CLEARING,),
        responsibility_scope=f"Personal accountability for {role.value} on signed clearing.",
        max_exposure=Decimal("1000"),
        exposure_unit="DEMO",
        valid_until=None,
    )


def _principal(settings: Settings, login: str, member: str, role: RoleCode) -> Principal:
    bootstrap_roles = {
        RoleCode.NODE_REGISTRAR,
        RoleCode.NODE_BUSINESS_OPERATOR,
        RoleCode.NODE_TECHNICAL_CUSTODIAN,
        RoleCode.NODE_SECURITY_ADMIN,
        RoleCode.NODE_AUDITOR,
    }
    assignment_kind = "bootstrap-role" if role in bootstrap_roles else "demo-role"
    cooperative_id = (
        None if role in bootstrap_roles else stable_id("cooperative", settings.node_code)
    )
    return Principal(
        user_id=stable_id("bootstrap-user", login),
        session_id=stable_id("acceptance-session", f"{settings.node_code}:{login}:{role.value}"),
        login=login,
        member_id=stable_id("member", member),
        must_change_password=False,
        roles=(
            RoleGrant(
                stable_id(assignment_kind, f"{login}:{role.value}"),
                role,
                cooperative_id,
            ),
        ),
    )


def _settings(node_code: str) -> Settings:
    suffix = node_code[-1].upper()
    seed_path = Path(os.environ[f"ACCEPTANCE_NODE_{suffix}_SEED_FILE"])
    return Settings(
        environment=Environment.TEST,
        release="federation-acceptance",
        service_name=f"acceptance-{node_code}",
        node_code=node_code,
        node_display_name=f"Acceptance {node_code}",
        demo_data_enabled=True,
        database_host=f"db-{node_code[-1]}",
        database_name="cooperative_clearing",
        database_user="coop_app",
        database_password_file=Path("/run/secrets/postgres_app_password"),
        blob_root=Path(f"/tmp/acceptance-{node_code}"),
        blob_encryption_key_file=Path("/run/secrets/blob_encryption_key"),
        node_signing_seed_file=seed_path,
        bootstrap_registrar_password_file=Path("/run/secrets/bootstrap_registrar_password"),
        bootstrap_security_password_file=Path("/run/secrets/bootstrap_security_password"),
        bootstrap_auditor_password_file=Path("/run/secrets/bootstrap_auditor_password"),
        allowed_hosts=[*NODE_CODES, "localhost", "127.0.0.1"],
        peer_connect_timeout_seconds=2,
    )
