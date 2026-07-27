"""Idempotent emergency custody continuity demo through production services."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.identity.application.bootstrap import stable_id
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
    Member,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.inventory.application.catalog import CatalogService
from cooperative_clearing.modules.inventory.application.custody_continuity import (
    CustodyContinuityService,
)
from cooperative_clearing.modules.inventory.application.demo import (
    DemoCatalog,
    _evidence,
    _principal,
)
from cooperative_clearing.modules.inventory.application.service import InventoryService
from cooperative_clearing.modules.inventory.infrastructure.models import (
    CustodyContinuityCase,
)
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


async def seed_demo_custody_continuity(
    session: AsyncSession,
    settings: Settings,
    *,
    catalog: DemoCatalog,
) -> None:
    cooperative_id = stable_id("cooperative", settings.node_code)
    source_member_id = stable_id("member", "demo-emergency-custodian")
    source_user_id = stable_id("demo-user", "demo-emergency-custodian")
    source_role_id = stable_id(
        "demo-role", "demo-emergency-custodian:WAREHOUSE_CUSTODIAN"
    )
    await session.execute(
        insert(Member)
        .values(
            id=source_member_id,
            display_name="Alexey Sokolov",
            registered_by_cooperative_id=cooperative_id,
            status="ACTIVE",
        )
        .on_conflict_do_nothing(index_elements=[Member.id])
    )
    await session.execute(
        insert(Membership)
        .values(
            id=stable_id("membership", "demo-emergency-custodian"),
            cooperative_id=cooperative_id,
            member_id=source_member_id,
            member_number="D-EMERGENCY-01",
            status="ACTIVE",
            joined_at=datetime.now(UTC) - timedelta(days=500),
        )
        .on_conflict_do_nothing(index_elements=[Membership.id])
    )
    await session.execute(
        insert(UserAccount)
        .values(
            id=source_user_id,
            login="demo-emergency-custodian",
            password_hash=PasswordService().hash("Disabled-Demo-Account-2026!"),
            member_id=source_member_id,
            status="ACTIVE",
            must_change_password=False,
        )
        .on_conflict_do_nothing(index_elements=[UserAccount.id])
    )
    await session.execute(
        insert(RoleAssignment)
        .values(
            id=source_role_id,
            user_id=source_user_id,
            role_code=RoleCode.WAREHOUSE_CUSTODIAN.value,
            cooperative_id=cooperative_id,
            status="ACTIVE",
            source="ASSIGNMENT",
            granted_by_user_id=stable_id("bootstrap-user", "registrar"),
            approved_by_user_id=stable_id("bootstrap-user", "auditor"),
            approved_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=[RoleAssignment.id])
    )
    await session.flush()

    registrar = _principal(
        "registrar",
        "demo-member-anna",
        cooperative_id,
        (
            (
                "bootstrap-role",
                "registrar:MEMBER_REGISTRAR",
                RoleCode.MEMBER_REGISTRAR,
            ),
            (
                "bootstrap-role",
                "registrar:COOPERATIVE_ADMIN",
                RoleCode.COOPERATIVE_ADMIN,
            ),
        ),
    )
    security = _principal(
        "security",
        "demo-member-elena",
        cooperative_id,
        (
            (
                "bootstrap-role",
                "security:SECURITY_ADMIN",
                RoleCode.SECURITY_ADMIN,
            ),
            (
                "demo-role",
                "security:WAREHOUSE_CUSTODIAN",
                RoleCode.WAREHOUSE_CUSTODIAN,
            ),
        ),
    )
    auditor = _principal(
        "auditor",
        "demo-member-pavel",
        cooperative_id,
        (
            ("bootstrap-role", "auditor:AUDITOR", RoleCode.AUDITOR),
            (
                "demo-role",
                "auditor:INVENTORY_CONTROLLER",
                RoleCode.INVENTORY_CONTROLLER,
            ),
        ),
        global_scope=True,
    )
    source = Principal(
        user_id=source_user_id,
        session_id=stable_id("demo-session", "demo-emergency-custodian"),
        login="demo-emergency-custodian",
        member_id=source_member_id,
        must_change_password=False,
        roles=(
            RoleGrant(
                source_role_id,
                RoleCode.WAREHOUSE_CUSTODIAN,
                cooperative_id,
            ),
        ),
    )

    warehouse = await CatalogService(settings).create_warehouse(
        session,
        principal=registrar,
        cooperative_id=cooperative_id,
        code="DEMO-WH-EMERGENCY",
        name="Emergency continuity warehouse",
        address_text="Demo site, building C",
        storage_conditions="Dry room, access by signed custody record",
        idempotency_key="demo-emergency-warehouse-v1",
        request_id=None,
    )
    responsibility = ResponsibilityService(settings)
    summary = assignment_summary(
        cooperative_id=cooperative_id,
        member_id=source_member_id,
        role_assignment_id=source_role_id,
        subject_type="warehouse",
        subject_id=warehouse.object_id,
        scope="Physical custody and condition reporting",
        max_exposure=Decimal("500.00"),
        exposure_unit="SHARE",
        valid_until=None,
    )
    proposed = await responsibility.propose(
        session,
        principal=registrar,
        cooperative_id=cooperative_id,
        member_id=source_member_id,
        role_assignment_id=source_role_id,
        subject_type="warehouse",
        subject_id=warehouse.object_id,
        scope="Physical custody and condition reporting",
        max_exposure=Decimal("500.00"),
        exposure_unit="SHARE",
        valid_until=None,
        expected_summary_hash=canonical_preview(summary).summary_hash,
        idempotency_key="demo-emergency-responsibility-propose-v1",
        request_id=None,
    )
    await responsibility.decide(
        session,
        principal=auditor,
        assignment_id=proposed.object_id,
        decision=ApprovalDecision.APPROVE,
        reason_code="INDEPENDENT_DEMO_REVIEW",
        idempotency_key="demo-emergency-responsibility-approve-v1",
        request_id=None,
    )
    assignment = await session.get(ResponsibilityAssignment, proposed.object_id)
    if assignment is not None and assignment.status == "PENDING_ACCEPTANCE":
        await responsibility.accept(
            session,
            principal=source,
            assignment_id=assignment.id,
            expected_version=assignment.version,
            idempotency_key="demo-emergency-responsibility-accept-v1",
            request_id=None,
        )

    receipt_evidence = await _evidence(
        session,
        settings,
        source,
        cooperative_id,
        "demo-emergency-receipt-v1",
        "Receipt record: 25 kg of cabbage accepted by the named custodian.",
    )
    await InventoryService(settings).register_lot(
        session,
        principal=source,
        cooperative_id=cooperative_id,
        lot_number="DEMO-EMERGENCY-001",
        product_id=catalog.product_id,
        warehouse_id=warehouse.object_id,
        owner_member_id=source_member_id,
        declared_quantity=Decimal("25.00"),
        unit_id=catalog.unit_id,
        declared_quality="Grade 1",
        expires_at=None,
        storage_conditions="Dry room",
        custodian_assignment_id=proposed.object_id,
        evidence_ids=[receipt_evidence],
        idempotency_key="demo-emergency-lot-register-v1",
        request_id=None,
    )

    member_continuity = MemberContinuityService(settings)
    contained = await member_continuity.request_case(
        session,
        principal=registrar,
        cooperative_id=cooperative_id,
        member_id=source_member_id,
        case_type=MemberContinuityCaseType.DEATH_OR_INCAPACITY,
        expected_member_version=1,
        evidence_refs=["registry:demo-incapacity-notice"],
        reason_code="OFFICIAL_NOTICE_RECEIVED",
        idempotency_key="demo-emergency-member-continuity-request-v1",
        request_id=None,
    )
    await member_continuity.decide_case(
        session,
        principal=security,
        continuity_case_id=contained.object_id,
        approve=True,
        expected_version=1,
        reason_code="INDEPENDENT_CONFIRMATION",
        idempotency_key="demo-emergency-member-continuity-approve-v1",
        request_id=None,
    )
    existing = await session.scalar(
        select(CustodyContinuityCase.id).where(
            CustodyContinuityCase.source_assignment_id == proposed.object_id,
            CustodyContinuityCase.status.in_(
                [
                    "INVENTORY_PENDING",
                    "PENDING_APPROVAL",
                    "PENDING_ACCEPTANCE",
                    "BLOCKED",
                ]
            ),
        )
    )
    if existing is not None:
        return
    assignment = await session.get(ResponsibilityAssignment, proposed.object_id)
    assert assignment is not None
    await CustodyContinuityService(settings).request_case(
        session,
        principal=registrar,
        member_continuity_case_id=contained.object_id,
        source_assignment_id=assignment.id,
        expected_source_assignment_version=assignment.version,
        target_role_assignment_id=stable_id(
            "demo-role", "security:WAREHOUSE_CUSTODIAN"
        ),
        handover_place="Emergency continuity warehouse, receiving desk",
        temporary_valid_until=datetime.now(UTC) + timedelta(days=7),
        evidence_refs=["case:demo-emergency-custody"],
        idempotency_key="demo-emergency-custody-request-v1",
        request_id=None,
    )
