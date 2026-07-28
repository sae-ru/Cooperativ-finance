import asyncio
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from cooperative_clearing.api.dependencies import get_principal
from cooperative_clearing.cli import initialize_node
from cooperative_clearing.main import create_app
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import RoleAssignment
from cooperative_clearing.modules.inventory.application.catalog import CatalogService
from cooperative_clearing.modules.inventory.application.service import InventoryService
from cooperative_clearing.modules.inventory.domain.types import QualityDecision
from cooperative_clearing.modules.inventory.infrastructure.models import InventoryLot
from cooperative_clearing.modules.journal.application.service import verify_journal
from cooperative_clearing.modules.node.infrastructure.models import NodeProfile
from cooperative_clearing.modules.rights.application.service import CommodityRightsService
from cooperative_clearing.modules.rights.infrastructure.models import (
    CommodityRight,
    LotBalance,
    RightRedemption,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database
from tests.integration.test_inventory_flow import (
    actor,
    assign_custody,
    create_actors,
    evidence,
)


@pytest.mark.integration
async def test_commodity_rights_are_backed_signed_and_redeemed_once() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"rights-integration-{suffix}",
        blob_root=Path(f"/tmp/rights-{suffix}"),
    )
    await initialize_node(settings)
    database = Database.from_settings(settings)
    cooperative_id, people, members = await create_actors(database)
    assert people["admin"].member_id is not None
    operator_member_id = people["admin"].member_id
    rights_role_id = uuid4()
    async with database.session() as session:
        session.add(
            RoleAssignment(
                id=rights_role_id,
                user_id=people["admin"].user_id,
                role_code=RoleCode.RIGHTS_OPERATOR.value,
                cooperative_id=cooperative_id,
                status="ACTIVE",
                granted_by_user_id=None,
                approved_by_user_id=None,
            )
        )
        await session.commit()
    operator = actor(
        people["admin"].user_id,
        operator_member_id,
        [
            *[
                (grant.assignment_id, grant.role, grant.cooperative_id)
                for grant in people["admin"].roles
            ],
            (rights_role_id, RoleCode.RIGHTS_OPERATOR, cooperative_id),
        ],
    )
    try:
        catalog = CatalogService(settings)
        async with database.session() as session:
            unit = await catalog.create_unit(
                session,
                principal=operator,
                cooperative_id=cooperative_id,
                code=f"KG-{suffix}",
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
                principal=operator,
                cooperative_id=cooperative_id,
                sku=f"CABBAGE-{suffix}",
                name="Cabbage",
                description="Commodity rights backing",
                default_unit_id=unit.object_id,
                quantity_tolerance=Decimal("0"),
                requires_evidence=True,
                shelf_life_required=False,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            warehouse = await catalog.create_warehouse(
                session,
                principal=operator,
                cooperative_id=cooperative_id,
                code=f"WH-{suffix}",
                name="Rights warehouse",
                address_text="Integration site",
                storage_conditions="Dry and cool",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        custody = await assign_custody(
            database,
            settings,
            cooperative_id,
            warehouse.object_id,
            people["custodian_b"],
            people["risk"],
            people["auditor"],
        )
        receipt_evidence = await evidence(
            database,
            settings,
            people["custodian_b"],
            cooperative_id,
            b"rights receipt",
            "rights-receipt.txt",
        )
        attestation_evidence = await evidence(
            database,
            settings,
            people["controller"],
            cooperative_id,
            b"rights attestation",
            "rights-attestation.txt",
        )
        inventory = InventoryService(settings)
        async with database.session() as session:
            registered = await inventory.register_lot(
                session,
                principal=people["custodian_b"],
                cooperative_id=cooperative_id,
                lot_number=f"RIGHTS-{suffix}",
                product_id=product.object_id,
                warehouse_id=warehouse.object_id,
                owner_member_id=members["owner"],
                declared_quantity=Decimal("100"),
                unit_id=unit.object_id,
                declared_quality="Grade A",
                expires_at=None,
                storage_conditions="Dry and cool",
                custodian_assignment_id=custody,
                evidence_ids=[receipt_evidence],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            await inventory.attest_lot(
                session,
                principal=people["controller"],
                lot_id=registered.object_id,
                measured_quantity=Decimal("100"),
                quality_decision=QualityDecision.ACCEPTED,
                verified_quality="Grade A",
                measurements={"weight": "100.00 kg"},
                notes="Independent match",
                evidence_ids=[attestation_evidence],
                expected_version=1,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        rights = CommodityRightsService(settings)
        issue_outcomes: list[object] = []

        async def issue_once(attempt: str) -> None:
            try:
                async with database.session() as session:
                    result = await rights.issue(
                        session,
                        principal=operator,
                        lot_id=registered.object_id,
                        owner_member_id=members["owner"],
                        quantity=Decimal("1"),
                        redeem_warehouse_id=warehouse.object_id,
                        valid_until=None,
                        expected_balance_version=1,
                        idempotency_key=f"{suffix}-concurrent-issue-{attempt}",
                        request_id=uuid4(),
                    )
                    await session.commit()
                    issue_outcomes.append(result)
            except DomainError as exc:
                issue_outcomes.append(exc)

        await asyncio.gather(issue_once("a"), issue_once("b"))
        assert len(
            [item for item in issue_outcomes if not isinstance(item, DomainError)]
        ) == 1
        issue_conflicts = [
            item for item in issue_outcomes if isinstance(item, DomainError)
        ]
        assert len(issue_conflicts) == 1
        assert issue_conflicts[0].code == "VERSION_CONFLICT"

        async with database.session() as session:
            pending = await rights.issue(
                session,
                principal=operator,
                lot_id=registered.object_id,
                owner_member_id=members["owner"],
                quantity=Decimal("25"),
                redeem_warehouse_id=warehouse.object_id,
                valid_until=None,
                expected_balance_version=2,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            frozen = await rights.issue(
                session,
                principal=operator,
                lot_id=registered.object_id,
                owner_member_id=members["owner"],
                quantity=Decimal("10"),
                redeem_warehouse_id=warehouse.object_id,
                valid_until=None,
                expected_balance_version=3,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            issued = await rights.issue(
                session,
                principal=operator,
                lot_id=registered.object_id,
                owner_member_id=members["owner"],
                quantity=Decimal("5"),
                redeem_warehouse_id=warehouse.object_id,
                valid_until=None,
                expected_balance_version=4,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        transfer_evidence = await evidence(
            database,
            settings,
            operator,
            cooperative_id,
            b"owner transfer authorization",
            "right-transfer.txt",
        )
        async with database.session() as session:
            await rights.transfer(
                session,
                principal=operator,
                right_id=pending.object_id,
                from_member_id=members["owner"],
                to_member_id=operator_member_id,
                evidence_ids=[transfer_evidence],
                expected_version=1,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            request = await rights.request_redemption(
                session,
                principal=operator,
                right_id=pending.object_id,
                owner_member_id=operator_member_id,
                expected_version=2,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await rights.freeze(
                session,
                principal=people["auditor"],
                right_id=frozen.object_id,
                reason_code="INDEPENDENT_REVIEW",
                decision_reference="Integration freeze decision",
                expected_version=1,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        redemption_evidence = await evidence(
            database,
            settings,
            people["custodian_b"],
            cooperative_id,
            b"physical issue act",
            "right-redemption.txt",
        )
        async with database.session() as session:
            pending_right = await session.get(CommodityRight, pending.object_id)
            balance = await session.get(LotBalance, registered.object_id)
            assert pending_right is not None
            assert balance is not None
            pending_version = pending_right.version
            balance_version = balance.version
            assert balance.available_quantity == 59
            assert balance.rights_issued_quantity == 41

        outcomes: list[object] = []

        async def complete_once() -> None:
            try:
                async with database.session() as session:
                    result = await rights.complete_redemption(
                        session,
                        principal=people["custodian_b"],
                        redemption_id=request.object_id,
                        evidence_ids=[redemption_evidence],
                        expected_right_version=pending_version,
                        idempotency_key=str(uuid4()),
                        request_id=uuid4(),
                    )
                    await session.commit()
                    outcomes.append(result)
            except DomainError as exc:
                outcomes.append(exc)

        await asyncio.gather(complete_once(), complete_once())
        assert len([item for item in outcomes if not isinstance(item, DomainError)]) == 1
        conflicts = [item for item in outcomes if isinstance(item, DomainError)]
        assert len(conflicts) == 1
        assert conflicts[0].code == "REDEMPTION_NOT_PENDING"

        async with database.session() as session:
            refreshed = await session.get(RightRedemption, request.object_id)
            right = await session.get(CommodityRight, pending.object_id)
            lot = await session.get(InventoryLot, registered.object_id)
            balance = await session.get(LotBalance, registered.object_id)
            assert refreshed is not None and refreshed.status == "COMPLETED"
            assert right is not None and right.status == "REDEEMED"
            assert lot is not None
            assert balance is not None
            assert lot.current_quantity == balance.verified_quantity == 75
            assert balance.available_quantity == 59
            assert balance.rights_issued_quantity == 16
            assert balance.version == balance_version + 1
            node = (
                await session.execute(
                    select(NodeProfile).where(NodeProfile.node_code == settings.node_code)
                )
            ).scalar_one()
            assert (await verify_journal(session, node.id)).ok

        app = create_app(settings, manage_runtime=False)
        app.state.database = database

        async def as_auditor() -> Principal:
            return people["auditor"]

        app.dependency_overrides[get_principal] = as_auditor
        with TestClient(app) as client:
            registry = client.get("/api/v1/rights")
            assert registry.status_code == 200
            assert {
                str(pending.object_id),
                str(frozen.object_id),
                str(issued.object_id),
            }.issubset({item["id"] for item in registry.json()["data"]})
            proof = client.get(f"/api/v1/rights/{frozen.object_id}/proof")
            assert proof.status_code == 200
            assert len(proof.json()["proof_hash"]) == 64
            assert proof.json()["right"]["status"] == "FROZEN"

        async def as_operator() -> Principal:
            return operator

        app.dependency_overrides[get_principal] = as_operator
        with TestClient(app) as client:
            over_issue = client.post(
                "/api/v1/rights",
                headers={"Idempotency-Key": str(uuid4())},
                json={
                    "lot_id": str(registered.object_id),
                    "owner_member_id": str(operator.member_id),
                    "quantity": "999999.00",
                    "redeem_warehouse_id": str(warehouse.object_id),
                    "expected_balance_version": balance_version + 1,
                },
            )
            assert over_issue.status_code == 409
            assert over_issue.json()["error"]["code"] == "INSUFFICIENT_AVAILABLE_QUANTITY"
    finally:
        await database.dispose()
