import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from cooperative_clearing.cli import initialize_node
from cooperative_clearing.main import create_app
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    Member,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.inventory.application.catalog import CatalogService
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.inventory.application.service import InventoryService
from cooperative_clearing.modules.inventory.domain.types import QualityDecision
from cooperative_clearing.modules.inventory.infrastructure.models import (
    InventoryDiscrepancy,
    InventoryLot,
    InventoryMovement,
    StockAttestation,
)
from cooperative_clearing.modules.journal.application.service import verify_journal
from cooperative_clearing.modules.journal.infrastructure.models import OutboxMessage, SignedEvent
from cooperative_clearing.modules.node.infrastructure.models import NodeProfile
from cooperative_clearing.modules.responsibility.application.service import (
    ResponsibilityService,
    assignment_summary,
    canonical_preview,
)
from cooperative_clearing.modules.responsibility.domain.types import ApprovalDecision
from cooperative_clearing.modules.responsibility.infrastructure.models import (
    ResponsibilityAssignment,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.security import PasswordService
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database


def actor(
    user_id: UUID,
    member_id: UUID,
    grants: list[tuple[UUID, RoleCode, UUID | None]],
) -> Principal:
    return Principal(
        user_id=user_id,
        session_id=uuid4(),
        login=f"inventory-{user_id}",
        member_id=member_id,
        must_change_password=False,
        roles=tuple(RoleGrant(*grant) for grant in grants),
    )


async def create_actors(
    database: Database,
) -> tuple[UUID, dict[str, Principal], dict[str, UUID]]:
    cooperative_id = uuid4()
    specs = {
        "admin": [RoleCode.DATA_STEWARD],
        "risk": [RoleCode.RISK_ADMIN],
        "auditor": [RoleCode.AUDITOR],
        "custodian_a": [RoleCode.WAREHOUSE_CUSTODIAN, RoleCode.INVENTORY_CONTROLLER],
        "custodian_b": [RoleCode.WAREHOUSE_CUSTODIAN],
        "controller": [RoleCode.INVENTORY_CONTROLLER],
        "owner": [],
    }
    principals: dict[str, Principal] = {}
    members: dict[str, UUID] = {}
    password_hash = PasswordService().hash("inventory-integration-password")
    async with database.session() as session:
        session.add(
            Cooperative(
                id=cooperative_id,
                code=f"inventory-{cooperative_id.hex[:12]}",
                name="Inventory integration cooperative",
                status="ACTIVE",
            )
        )
        for name, roles in specs.items():
            member_id, user_id = uuid4(), uuid4()
            members[name] = member_id
            session.add(Member(id=member_id, display_name=name, status="ACTIVE"))
            await session.flush()
            session.add(
                Membership(
                    id=uuid4(),
                    cooperative_id=cooperative_id,
                    member_id=member_id,
                    member_number=f"I-{len(members):04d}",
                    status="ACTIVE",
                    joined_at=datetime.now(UTC),
                )
            )
            session.add(
                UserAccount(
                    id=user_id,
                    login=f"inventory-{user_id}",
                    password_hash=password_hash,
                    member_id=member_id,
                    status="ACTIVE",
                    must_change_password=False,
                )
            )
            await session.flush()
            grants: list[tuple[UUID, RoleCode, UUID | None]] = []
            for role in roles:
                role_id = uuid4()
                role_cooperative = None if role is RoleCode.AUDITOR else cooperative_id
                session.add(
                    RoleAssignment(
                        id=role_id,
                        user_id=user_id,
                        role_code=role.value,
                        cooperative_id=role_cooperative,
                        status="ACTIVE",
                        granted_by_user_id=None,
                        approved_by_user_id=None,
                    )
                )
                grants.append((role_id, role, role_cooperative))
            principals[name] = actor(user_id, member_id, grants)
        await session.commit()
    return cooperative_id, principals, members


async def assign_custody(
    database: Database,
    settings: Settings,
    cooperative_id: UUID,
    warehouse_id: UUID,
    target: Principal,
    risk: Principal,
    auditor: Principal,
) -> UUID:
    target_role = next(
        grant for grant in target.roles if grant.role is RoleCode.WAREHOUSE_CUSTODIAN
    )
    assert target.member_id is not None
    summary = assignment_summary(
        cooperative_id=cooperative_id,
        member_id=target.member_id,
        role_assignment_id=target_role.assignment_id,
        subject_type="warehouse",
        subject_id=warehouse_id,
        scope="Physical custody and condition reporting",
        max_exposure=Decimal("500.0000"),
        exposure_unit="DEMO_UNIT",
        valid_until=None,
    )
    service = ResponsibilityService(settings)
    async with database.session() as session:
        proposed = await service.propose(
            session,
            principal=risk,
            cooperative_id=cooperative_id,
            member_id=target.member_id,
            role_assignment_id=target_role.assignment_id,
            subject_type="warehouse",
            subject_id=warehouse_id,
            scope="Physical custody and condition reporting",
            max_exposure=Decimal("500.0000"),
            exposure_unit="DEMO_UNIT",
            valid_until=None,
            expected_summary_hash=canonical_preview(summary).summary_hash,
            idempotency_key=str(uuid4()),
            request_id=uuid4(),
        )
        await session.commit()
    async with database.session() as session:
        await service.decide(
            session,
            principal=auditor,
            assignment_id=proposed.object_id,
            decision=ApprovalDecision.APPROVE,
            reason_code="INDEPENDENT_REVIEW",
            idempotency_key=str(uuid4()),
            request_id=uuid4(),
        )
        await session.commit()
    async with database.session() as session:
        assignment = await session.get(ResponsibilityAssignment, proposed.object_id)
        assert assignment is not None
        await service.accept(
            session,
            principal=target,
            assignment_id=assignment.id,
            expected_version=assignment.version,
            idempotency_key=str(uuid4()),
            request_id=uuid4(),
        )
        await session.commit()
    return proposed.object_id


async def evidence(
    database: Database,
    settings: Settings,
    principal: Principal,
    cooperative_id: UUID,
    content: bytes,
    name: str,
) -> UUID:
    digest = hashlib.sha256(content).hexdigest()
    service = EvidenceService(settings)
    async with database.session() as session:
        intent = await service.create_intent(
            session,
            principal=principal,
            cooperative_id=cooperative_id,
            expected_sha256=digest,
            expected_size=len(content),
            mime_type="text/plain",
            kind="ACT",
            original_name=name,
            access_scope="COOPERATIVE",
            retention_until=None,
            idempotency_key=str(uuid4()),
            request_id=uuid4(),
        )
        await session.commit()

    async def stream() -> AsyncIterator[bytes]:
        yield content

    async with database.session() as session:
        await service.store_content(
            session,
            principal=principal,
            evidence_id=intent.object_id,
            chunks=stream(),
            request_id=uuid4(),
        )
        await session.commit()
    return intent.object_id


@pytest.mark.integration
async def test_inventory_flow_is_signed_independent_and_custody_is_two_phase() -> None:
    settings = Settings(
        service_name="inventory-integration",
        blob_root=Path(f"/tmp/inventory-{uuid4()}"),
    )
    await initialize_node(settings)
    database = Database.from_settings(settings)
    try:
        cooperative_id, people, members = await create_actors(database)
        catalog = CatalogService(settings)
        async with database.session() as session:
            unit = await catalog.create_unit(
                session,
                principal=people["admin"],
                cooperative_id=cooperative_id,
                code="KG",
                name="Kilogram",
                symbol="kg",
                dimension="MASS",
                decimal_scale=2,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            product = await catalog.create_product(
                session,
                principal=people["admin"],
                cooperative_id=cooperative_id,
                sku="CABBAGE",
                name="Cabbage",
                description="Fresh cabbage",
                default_unit_id=unit.object_id,
                quantity_tolerance=Decimal("0.10"),
                requires_evidence=True,
                shelf_life_required=False,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            warehouse_a = await catalog.create_warehouse(
                session,
                principal=people["admin"],
                cooperative_id=cooperative_id,
                code="WH-A",
                name="Warehouse A",
                address_text="Site A",
                storage_conditions="Dry and cool",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            warehouse_b = await catalog.create_warehouse(
                session,
                principal=people["admin"],
                cooperative_id=cooperative_id,
                code="WH-B",
                name="Warehouse B",
                address_text="Site B",
                storage_conditions="Dry and cool",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        custody_a = await assign_custody(
            database,
            settings,
            cooperative_id,
            warehouse_a.object_id,
            people["custodian_a"],
            people["risk"],
            people["auditor"],
        )
        custody_b = await assign_custody(
            database,
            settings,
            cooperative_id,
            warehouse_b.object_id,
            people["custodian_b"],
            people["risk"],
            people["auditor"],
        )
        receipt_evidence = await evidence(
            database,
            settings,
            people["custodian_a"],
            cooperative_id,
            b"receipt act",
            "receipt.txt",
        )
        inventory = InventoryService(settings)
        async with database.session() as session:
            registered = await inventory.register_lot(
                session,
                principal=people["custodian_a"],
                cooperative_id=cooperative_id,
                lot_number="LOT-001",
                product_id=product.object_id,
                warehouse_id=warehouse_a.object_id,
                owner_member_id=members["owner"],
                declared_quantity=Decimal("10.00"),
                unit_id=unit.object_id,
                declared_quality="Grade A",
                expires_at=None,
                storage_conditions="Dry and cool",
                custodian_assignment_id=custody_a,
                evidence_ids=[receipt_evidence],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        attestation_evidence = await evidence(
            database,
            settings,
            people["controller"],
            cooperative_id,
            b"independent measurement",
            "attestation.txt",
        )
        async with database.session() as session:
            with pytest.raises(DomainError, match="INDEPENDENT_ATTESTER_REQUIRED"):
                await inventory.attest_lot(
                    session,
                    principal=people["custodian_a"],
                    lot_id=registered.object_id,
                    measured_quantity=Decimal("9.95"),
                    quality_decision=QualityDecision.ACCEPTED,
                    verified_quality="Grade A",
                    measurements={"scale": "9.95 kg"},
                    notes="Self attestation must fail",
                    evidence_ids=[attestation_evidence],
                    expected_version=1,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            await session.rollback()
        async with database.session() as session:
            attested = await inventory.attest_lot(
                session,
                principal=people["controller"],
                lot_id=registered.object_id,
                measured_quantity=Decimal("9.95"),
                quality_decision=QualityDecision.ACCEPTED,
                verified_quality="Grade A",
                measurements={"scale": "9.95 kg", "temperature": "4 C"},
                notes="Quantity is within configured tolerance",
                evidence_ids=[attestation_evidence],
                expected_version=1,
                idempotency_key="attest-replay-key",
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            replay = await inventory.attest_lot(
                session,
                principal=people["controller"],
                lot_id=registered.object_id,
                measured_quantity=Decimal("9.95"),
                quality_decision=QualityDecision.ACCEPTED,
                verified_quality="Grade A",
                measurements={"scale": "9.95 kg", "temperature": "4 C"},
                notes="Quantity is within configured tolerance",
                evidence_ids=[attestation_evidence],
                expected_version=1,
                idempotency_key="attest-replay-key",
                request_id=uuid4(),
            )
            assert replay.replayed is True and replay.event_id == attested.event_id
            await session.rollback()
        async with database.session() as session:
            lot = await session.get(InventoryLot, registered.object_id)
            assert lot is not None
            assert lot.status == "VERIFIED"
            assert lot.current_quantity == Decimal("9.950000000000")
            assert lot.custodian_assignment_id == custody_a
            assert lot.version == 3
        transfer_evidence = await evidence(
            database,
            settings,
            people["custodian_b"],
            cooperative_id,
            b"custody acceptance act",
            "custody.txt",
        )
        async with database.session() as session:
            offered = await inventory.offer_custody(
                session,
                principal=people["custodian_a"],
                lot_id=registered.object_id,
                to_warehouse_id=warehouse_b.object_id,
                to_assignment_id=custody_b,
                place="Warehouse B receiving gate",
                notes="Sealed lot handed over",
                evidence_ids=[],
                expected_version=3,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            lot = await session.get(InventoryLot, registered.object_id)
            assert lot is not None and lot.custodian_assignment_id == custody_a
            await inventory.accept_custody(
                session,
                principal=people["custodian_b"],
                transfer_id=offered.object_id,
                evidence_ids=[transfer_evidence],
                expected_lot_version=4,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        discrepancy_evidence = await evidence(
            database,
            settings,
            people["controller"],
            cooperative_id,
            b"cycle count discrepancy",
            "count.txt",
        )
        async with database.session() as session:
            discrepancy = await inventory.record_discrepancy(
                session,
                principal=people["controller"],
                lot_id=registered.object_id,
                actual_quantity=Decimal("9.00"),
                reason_code="CYCLE_COUNT_VARIANCE",
                notes="Independent cycle count found a shortage",
                evidence_ids=[discrepancy_evidence],
                expected_version=5,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            lot = await session.get(InventoryLot, registered.object_id)
            assert lot is not None
            assert lot.custodian_assignment_id == custody_b
            assert lot.warehouse_id == warehouse_b.object_id
            assert lot.status == "DISPUTED"
            assert lot.current_quantity == Decimal("9.000000000000")
            assert await session.get(InventoryDiscrepancy, discrepancy.object_id) is not None
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(StockAttestation)
                    .where(StockAttestation.lot_id == lot.id)
                )
                == 1
            )
            movement = (
                (
                    await session.execute(
                        select(InventoryMovement)
                        .where(InventoryMovement.lot_id == lot.id)
                        .order_by(InventoryMovement.created_at.desc())
                    )
                )
                .scalars()
                .first()
            )
            assert movement is not None and movement.resulting_quantity == Decimal("9.000000000000")
            node = (
                await session.execute(
                    select(NodeProfile).where(NodeProfile.node_code == settings.node_code)
                )
            ).scalar_one()
            assert (await verify_journal(session, node.id)).ok is True
            event_count = await session.scalar(
                select(func.count())
                .select_from(SignedEvent)
                .where(SignedEvent.aggregate_id == lot.id)
            )
            outbox_count = await session.scalar(
                select(func.count())
                .select_from(OutboxMessage)
                .join(SignedEvent, SignedEvent.event_id == OutboxMessage.event_id)
                .where(SignedEvent.aggregate_id == lot.id)
            )
            assert event_count == 6
            assert outbox_count == 6
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(InventoryMovement)
                    .where(InventoryMovement.id == movement.id)
                    .values(reason_code="MUTATED")
                )
            await session.rollback()
        with TestClient(create_app(settings)) as client:
            login_response = client.post(
                "/api/v1/auth/login",
                json={
                    "login": people["controller"].login,
                    "password": "inventory-integration-password",
                },
            )
            assert login_response.status_code == 200
            headers = {"Authorization": (f"Bearer {login_response.json()['data']['access_token']}")}
            members_response = client.get("/api/v1/inventory/members", headers=headers)
            assert members_response.status_code == 200
            assert members["owner"] in {
                UUID(item["member_id"]) for item in members_response.json()["data"]
            }
            custodians_response = client.get("/api/v1/inventory/custodians", headers=headers)
            assert custodians_response.status_code == 200
            assert {custody_a, custody_b}.issubset(
                {UUID(item["assignment_id"]) for item in custodians_response.json()["data"]}
            )
            lots_response = client.get("/api/v1/inventory/lots", headers=headers)
            assert lots_response.status_code == 200
            assert registered.object_id in {
                UUID(item["id"]) for item in lots_response.json()["data"]
            }
            history_response = client.get(
                f"/api/v1/inventory/lots/{registered.object_id}/history",
                headers=headers,
            )
            assert history_response.status_code == 200
            assert len(history_response.json()["data"]) == 6
            act_response = client.get(
                f"/api/v1/inventory/lots/{registered.object_id}/receipt-act",
                headers=headers,
            )
            assert act_response.status_code == 200
            assert act_response.json()["lot"]["status"] == "DISPUTED"
            assert len(act_response.json()["signed_events"]) == 6

            api_content = b"api streamed evidence"
            api_digest = hashlib.sha256(api_content).hexdigest()
            intent_response = client.post(
                "/api/v1/evidence/upload-intents",
                headers={**headers, "Idempotency-Key": str(uuid4())},
                json={
                    "cooperative_id": str(cooperative_id),
                    "expected_sha256": api_digest,
                    "expected_size": len(api_content),
                    "mime_type": "text/plain",
                    "kind": "PHOTO",
                    "original_name": "api-evidence.txt",
                    "access_scope": "COOPERATIVE",
                },
            )
            assert intent_response.status_code == 201
            api_evidence_id = intent_response.json()["data"]["object_id"]
            upload_response = client.put(
                f"/api/v1/evidence/upload-intents/{api_evidence_id}/content",
                headers={**headers, "Content-Type": "application/octet-stream"},
                content=api_content,
            )
            assert upload_response.status_code == 200
            download_response = client.get(
                f"/api/v1/evidence/{api_evidence_id}/content",
                headers=headers,
            )
            assert download_response.status_code == 200
            assert download_response.content == api_content
    finally:
        await database.dispose()
