import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from cooperative_clearing.cli import initialize_node
from cooperative_clearing.modules.identity.application.member_continuity import (
    MemberContinuityService,
)
from cooperative_clearing.modules.identity.domain.types import (
    MemberContinuityCaseType,
    Principal,
    RoleCode,
    RoleGrant,
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    Member,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.inventory.application.catalog import CatalogService
from cooperative_clearing.modules.inventory.application.custody_continuity import (
    CustodyContinuityService,
)
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.inventory.application.service import InventoryService
from cooperative_clearing.modules.inventory.domain.types import QualityDecision
from cooperative_clearing.modules.inventory.infrastructure.models import (
    CustodyContinuityCase,
    CustodyContinuityItem,
    CustodyTransfer,
    InventoryLot,
)
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
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
    login: str,
    grants: list[tuple[UUID, RoleCode, UUID | None]],
) -> Principal:
    return Principal(
        user_id=user_id,
        session_id=uuid4(),
        login=login,
        member_id=member_id,
        must_change_password=False,
        roles=tuple(RoleGrant(*grant) for grant in grants),
    )


async def create_people(
    database: Database,
) -> tuple[UUID, dict[str, Principal], dict[str, UUID]]:
    cooperative_id = uuid4()
    specs = {
        "requester": [
            RoleCode.COOPERATIVE_ADMIN,
            RoleCode.MEMBER_REGISTRAR,
            RoleCode.DATA_STEWARD,
        ],
        "security": [RoleCode.SECURITY_ADMIN],
        "controller": [RoleCode.AUDITOR, RoleCode.INVENTORY_CONTROLLER],
        "source": [RoleCode.WAREHOUSE_CUSTODIAN],
        "candidate": [RoleCode.WAREHOUSE_CUSTODIAN],
    }
    principals: dict[str, Principal] = {}
    members: dict[str, UUID] = {}
    password_hash = PasswordService().hash("custody-continuity-test-2026")
    async with database.session() as session:
        session.add(
            Cooperative(
                id=cooperative_id,
                code=f"custody-{uuid4().hex[:12]}",
                name="Custody continuity integration cooperative",
                status="ACTIVE",
            )
        )
        for index, (name, roles) in enumerate(specs.items(), start=1):
            member_id, user_id = uuid4(), uuid4()
            members[name] = member_id
            session.add(
                Member(
                    id=member_id,
                    display_name=name,
                    registered_by_cooperative_id=cooperative_id,
                    status="ACTIVE",
                )
            )
            session.add(
                Membership(
                    id=uuid4(),
                    cooperative_id=cooperative_id,
                    member_id=member_id,
                    member_number=f"CC-{index:03d}",
                    status="ACTIVE",
                    joined_at=datetime.now(UTC),
                )
            )
            session.add(
                UserAccount(
                    id=user_id,
                    login=f"custody-{name}-{uuid4().hex[:8]}",
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
                role_scope = (
                    None
                    if role in {RoleCode.AUDITOR, RoleCode.SECURITY_ADMIN}
                    else cooperative_id
                )
                session.add(
                    RoleAssignment(
                        id=role_id,
                        user_id=user_id,
                        role_code=role.value,
                        cooperative_id=role_scope,
                        status="ACTIVE",
                        source="ASSIGNMENT",
                        granted_by_user_id=None,
                        approved_by_user_id=None,
                    )
                )
                grants.append((role_id, role, role_scope))
            principals[name] = actor(
                user_id,
                member_id,
                f"custody-{name}",
                grants,
            )
        await session.commit()
    return cooperative_id, principals, members


async def evidence(
    database: Database,
    settings: Settings,
    principal: Principal,
    cooperative_id: UUID,
    content: bytes,
) -> UUID:
    service = EvidenceService(settings)
    async with database.session() as session:
        intent = await service.create_intent(
            session,
            principal=principal,
            cooperative_id=cooperative_id,
            expected_sha256=hashlib.sha256(content).hexdigest(),
            expected_size=len(content),
            mime_type="text/plain",
            kind="ACT",
            original_name="custody-continuity.txt",
            access_scope="COOPERATIVE",
            retention_until=None,
            idempotency_key=str(uuid4()),
            request_id=uuid4(),
        )
        await session.commit()

    async def chunks() -> AsyncIterator[bytes]:
        yield content

    async with database.session() as session:
        await service.store_content(
            session,
            principal=principal,
            evidence_id=intent.object_id,
            chunks=chunks(),
            request_id=uuid4(),
        )
        await session.commit()
    return intent.object_id


@pytest.mark.integration
async def test_emergency_custody_keeps_old_custodian_until_personal_acceptance() -> None:
    settings = Settings(
        service_name=f"custody-continuity-{uuid4().hex[:12]}",
        blob_root=Path(f"/tmp/custody-continuity-{uuid4()}"),
    )
    await initialize_node(settings)
    database = Database.from_settings(settings)
    try:
        cooperative_id, people, members = await create_people(database)
        catalog = CatalogService(settings)
        async with database.session() as session:
            unit = await catalog.create_unit(
                session,
                principal=people["requester"],
                cooperative_id=cooperative_id,
                code="KG",
                name="Kilogram",
                symbol="kg",
                dimension="MASS",
                decimal_scale=2,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            product = await catalog.create_product(
                session,
                principal=people["requester"],
                cooperative_id=cooperative_id,
                sku="EMERGENCY-CABBAGE",
                name="Emergency cabbage",
                description="Inventory under named physical custody",
                default_unit_id=unit.object_id,
                quantity_tolerance=Decimal("0.10"),
                requires_evidence=True,
                shelf_life_required=False,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            warehouse = await catalog.create_warehouse(
                session,
                principal=people["requester"],
                cooperative_id=cooperative_id,
                code="WH-EMERGENCY",
                name="Emergency warehouse",
                address_text="Site C",
                storage_conditions="Dry room",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        source_role = next(
            grant
            for grant in people["source"].roles
            if grant.role is RoleCode.WAREHOUSE_CUSTODIAN
        )
        responsibility = ResponsibilityService(settings)
        summary = assignment_summary(
            cooperative_id=cooperative_id,
            member_id=members["source"],
            role_assignment_id=source_role.assignment_id,
            subject_type="warehouse",
            subject_id=warehouse.object_id,
            scope="Physical custody and condition reporting",
            max_exposure=Decimal("500"),
            exposure_unit="SHARE",
            valid_until=None,
        )
        async with database.session() as session:
            proposed = await responsibility.propose(
                session,
                principal=people["requester"],
                cooperative_id=cooperative_id,
                member_id=members["source"],
                role_assignment_id=source_role.assignment_id,
                subject_type="warehouse",
                subject_id=warehouse.object_id,
                scope="Physical custody and condition reporting",
                max_exposure=Decimal("500"),
                exposure_unit="SHARE",
                valid_until=None,
                expected_summary_hash=canonical_preview(summary).summary_hash,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            await responsibility.decide(
                session,
                principal=people["controller"],
                assignment_id=proposed.object_id,
                decision=ApprovalDecision.APPROVE,
                reason_code="INDEPENDENT_REVIEW",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            source_assignment = await session.get(
                ResponsibilityAssignment, proposed.object_id
            )
            assert source_assignment is not None
            await responsibility.accept(
                session,
                principal=people["source"],
                assignment_id=source_assignment.id,
                expected_version=source_assignment.version,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        receipt = await evidence(
            database,
            settings,
            people["source"],
            cooperative_id,
            b"source receipt",
        )
        inventory = InventoryService(settings)
        async with database.session() as session:
            lot_result = await inventory.register_lot(
                session,
                principal=people["source"],
                cooperative_id=cooperative_id,
                lot_number="CC-LOT-001",
                product_id=product.object_id,
                warehouse_id=warehouse.object_id,
                owner_member_id=members["source"],
                declared_quantity=Decimal("25"),
                unit_id=unit.object_id,
                declared_quality="Grade 1",
                expires_at=None,
                storage_conditions="Dry room",
                custodian_assignment_id=proposed.object_id,
                evidence_ids=[receipt],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        member_continuity = MemberContinuityService(settings)
        async with database.session() as session:
            contained = await member_continuity.request_case(
                session,
                principal=people["requester"],
                cooperative_id=cooperative_id,
                member_id=members["source"],
                case_type=MemberContinuityCaseType.DEATH_OR_INCAPACITY,
                expected_member_version=1,
                evidence_refs=["registry:test-incapacity"],
                reason_code="OFFICIAL_NOTICE_RECEIVED",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            await member_continuity.decide_case(
                session,
                principal=people["security"],
                continuity_case_id=contained.object_id,
                approve=True,
                expected_version=1,
                reason_code="INDEPENDENT_CONFIRMATION",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        candidate_role = next(
            grant
            for grant in people["candidate"].roles
            if grant.role is RoleCode.WAREHOUSE_CUSTODIAN
        )
        custody = CustodyContinuityService(settings)
        async with database.session() as session:
            source_assignment = await session.get(
                ResponsibilityAssignment, proposed.object_id
            )
            assert source_assignment is not None
            started = await custody.request_case(
                session,
                principal=people["requester"],
                member_continuity_case_id=contained.object_id,
                source_assignment_id=source_assignment.id,
                expected_source_assignment_version=source_assignment.version,
                target_role_assignment_id=candidate_role.assignment_id,
                handover_place="Emergency warehouse desk",
                temporary_valid_until=datetime.now(UTC) + timedelta(days=2),
                evidence_refs=["case:test-emergency-custody"],
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        count_evidence = await evidence(
            database,
            settings,
            people["controller"],
            cooperative_id,
            b"independent count 25 kg",
        )
        async with database.session() as session:
            lot = await session.get(InventoryLot, lot_result.object_id)
            case = await session.get(CustodyContinuityCase, started.object_id)
            assert lot is not None and case is not None
            assert lot.custodian_assignment_id == proposed.object_id
            assert lot.continuity_hold_case_id == case.id
            with pytest.raises(DomainError, match="LOT_CUSTODY_CONTINUITY_HELD"):
                await inventory.attest_lot(
                    session,
                    principal=people["controller"],
                    lot_id=lot.id,
                    measured_quantity=Decimal("25"),
                    quality_decision=QualityDecision.ACCEPTED,
                    verified_quality="Grade 1",
                    measurements={"weight": "25 kg"},
                    notes="Ordinary workflow must remain blocked",
                    evidence_ids=[count_evidence],
                    expected_version=lot.version,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            await session.rollback()

        async with database.session() as session:
            case = await session.get(CustodyContinuityCase, started.object_id)
            assert case is not None
            case_items = list(
                (
                    await session.execute(
                        select(CustodyContinuityItem).where(
                            CustodyContinuityItem.case_id == case.id
                        )
                    )
                ).scalars()
            )
            assert len(case_items) == 1
            item = case_items[0]
            counted = await custody.attest_item(
                session,
                principal=people["controller"],
                continuity_case_id=case.id,
                item_id=item.id,
                actual_quantity=Decimal("25"),
                condition_notes="Count and packaging match",
                evidence_ids=[count_evidence],
                expected_case_version=case.version,
                expected_item_version=item.version,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        assert counted.status == "PENDING_APPROVAL"

        async with database.session() as session:
            case = await session.get(CustodyContinuityCase, started.object_id)
            assert case is not None
            approved = await custody.decide_case(
                session,
                principal=people["security"],
                continuity_case_id=case.id,
                approve=True,
                expected_version=case.version,
                reason_code="INDEPENDENT_INVENTORY_REVIEW",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        assert approved.status == "PENDING_ACCEPTANCE"

        async with database.session() as session:
            lot = await session.get(InventoryLot, lot_result.object_id)
            assert lot is not None
            assert lot.custodian_assignment_id == proposed.object_id

        acceptance_evidence = await evidence(
            database,
            settings,
            people["candidate"],
            cooperative_id,
            b"candidate signed acceptance",
        )
        async with database.session() as session:
            case = await session.get(CustodyContinuityCase, started.object_id)
            assert case is not None
            accepted = await custody.candidate_decision(
                session,
                principal=people["candidate"],
                continuity_case_id=case.id,
                accept=True,
                expected_version=case.version,
                evidence_ids=[acceptance_evidence],
                reason_code="PERSONAL_ACCEPTANCE",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        assert accepted.status == "ACCEPTED"

        async with database.session() as session:
            case = await session.get(CustodyContinuityCase, started.object_id)
            lot = await session.get(InventoryLot, lot_result.object_id)
            source_assignment = await session.get(
                ResponsibilityAssignment, proposed.object_id
            )
            assert case is not None and lot is not None and source_assignment is not None
            target_assignment = await session.get(
                ResponsibilityAssignment, case.target_assignment_id
            )
            assert target_assignment is not None
            assert source_assignment.status == "RELEASED"
            assert target_assignment.status == "ACTIVE"
            assert lot.custodian_assignment_id == target_assignment.id
            assert lot.continuity_hold_case_id is None
            transfer = await session.scalar(
                select(CustodyTransfer).where(
                    CustodyTransfer.lot_id == lot.id,
                    CustodyTransfer.status == "ACCEPTED",
                )
            )
            assert transfer is not None
            event_types = set(
                (
                    await session.execute(
                        select(SignedEvent.event_type).where(
                            SignedEvent.aggregate_id.in_([case.id, lot.id])
                        )
                    )
                ).scalars()
            )
            assert {
                "responsibility.custody_continuity_started",
                "responsibility.custody_hold_applied",
                "inventory.emergency_count_attested",
                "responsibility.temporary_custodian_approved",
                "responsibility.emergency_custody_accepted",
                "responsibility.emergency_custody_transferred",
            }.issubset(event_types)
            responsibility_events = list(
                (
                    await session.execute(
                        select(SignedEvent).where(
                            SignedEvent.aggregate_id.in_([case.id, lot.id]),
                            SignedEvent.event_type.like("responsibility.%"),
                        )
                    )
                ).scalars()
            )
            assurances = {
                item.event_type: item.payload["_command_assurance"]
                for item in responsibility_events
            }
            assert all(
                item["format"] == "critical-command-assurance-v2"
                for item in assurances.values()
            )
            started_exposure = assurances[
                "responsibility.custody_continuity_started"
            ]["exposure"]
            assert started_exposure["category"] == "CUSTODY"
            assert started_exposure["maximum_loss"] == "500.0000"
            assert assurances["responsibility.custody_hold_applied"]["exposure"][
                "effect"
            ] == "HOLD"
            for event_type in (
                "responsibility.temporary_custodian_approved",
                "responsibility.emergency_custody_accepted",
                "responsibility.emergency_custody_transferred",
            ):
                next_party = assurances[event_type]["next_responsible"][0]
                assert next_party["reference"] == str(members["candidate"])
                assert next_party["role_assignment_id"] == str(
                    candidate_role.assignment_id
                )
    finally:
        await database.dispose()
