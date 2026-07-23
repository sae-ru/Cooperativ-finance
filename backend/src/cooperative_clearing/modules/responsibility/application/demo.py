"""Deterministic demo responsibility chains created through production commands."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
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


async def seed_demo_responsibility(
    session: AsyncSession,
    settings: Settings,
    *,
    warehouse_a_id: UUID,
    warehouse_b_id: UUID,
) -> tuple[UUID, UUID]:
    cooperative_id = stable_id("cooperative", settings.node_code)
    registrar = _principal(
        "registrar",
        "demo-member-anna",
        cooperative_id,
        (
            ("bootstrap-role", "registrar:COOPERATIVE_ADMIN", RoleCode.COOPERATIVE_ADMIN),
            ("demo-role", "registrar:WAREHOUSE_CUSTODIAN", RoleCode.WAREHOUSE_CUSTODIAN),
        ),
    )
    auditor = _principal(
        "auditor",
        "demo-member-boris",
        cooperative_id,
        (("bootstrap-role", "auditor:AUDITOR", RoleCode.AUDITOR),),
        global_scope=True,
    )
    security = _principal(
        "security",
        "demo-member-elena",
        cooperative_id,
        (
            ("demo-role", "security:DATA_STEWARD", RoleCode.DATA_STEWARD),
            ("demo-role", "security:WAREHOUSE_CUSTODIAN", RoleCode.WAREHOUSE_CUSTODIAN),
        ),
    )
    assignment_a = await _assignment(
        session,
        settings,
        proposer=registrar,
        approver=auditor,
        target=security,
        target_role_id=stable_id("demo-role", "security:WAREHOUSE_CUSTODIAN"),
        warehouse_id=warehouse_a_id,
        key="warehouse-a",
    )
    assignment_b = await _assignment(
        session,
        settings,
        proposer=registrar,
        approver=auditor,
        target=registrar,
        target_role_id=stable_id("demo-role", "registrar:WAREHOUSE_CUSTODIAN"),
        warehouse_id=warehouse_b_id,
        key="warehouse-b",
    )
    return assignment_a, assignment_b


async def _assignment(
    session: AsyncSession,
    settings: Settings,
    *,
    proposer: Principal,
    approver: Principal,
    target: Principal,
    target_role_id: UUID,
    warehouse_id: UUID,
    key: str,
) -> UUID:
    if target.member_id is None:
        raise RuntimeError("demo custodian must be linked to a member")
    cooperative_id = stable_id("cooperative", settings.node_code)
    scope = "Приёмка, физическая сохранность и фиксация расхождений"
    exposure = Decimal("500.0000")
    expected_summary_hash = canonical_preview(
        assignment_summary(
            cooperative_id=cooperative_id,
            member_id=target.member_id,
            role_assignment_id=target_role_id,
            subject_type="warehouse",
            subject_id=warehouse_id,
            scope=scope,
            max_exposure=exposure,
            exposure_unit="DEMO_UNIT",
            valid_until=None,
        )
    ).summary_hash
    service = ResponsibilityService(settings)
    proposed = await service.propose(
        session,
        principal=proposer,
        cooperative_id=cooperative_id,
        member_id=target.member_id,
        role_assignment_id=target_role_id,
        subject_type="warehouse",
        subject_id=warehouse_id,
        scope=scope,
        max_exposure=exposure,
        exposure_unit="DEMO_UNIT",
        valid_until=None,
        expected_summary_hash=expected_summary_hash,
        idempotency_key=f"demo-responsibility-proposal-{key}-v1",
        request_id=None,
    )
    assignment = await session.get(
        ResponsibilityAssignment, proposed.object_id, with_for_update=True
    )
    if assignment is None:
        raise RuntimeError("demo responsibility assignment was not created")
    if assignment.status == "PENDING_APPROVAL":
        await service.decide(
            session,
            principal=approver,
            assignment_id=assignment.id,
            decision=ApprovalDecision.APPROVE,
            reason_code="DEMO_INDEPENDENT_REVIEW",
            idempotency_key=f"demo-responsibility-decision-{key}-v1",
            request_id=None,
        )
    if assignment.status == "PENDING_ACCEPTANCE":
        await service.accept(
            session,
            principal=target,
            assignment_id=assignment.id,
            expected_version=assignment.version,
            idempotency_key=f"demo-responsibility-acceptance-{key}-v1",
            request_id=None,
        )
    return assignment.id


def _principal(
    login: str,
    member_key: str,
    cooperative_id: UUID,
    roles: tuple[tuple[str, str, RoleCode], ...],
    *,
    global_scope: bool = False,
) -> Principal:
    return Principal(
        user_id=stable_id("bootstrap-user", login),
        session_id=stable_id("demo-session", login),
        login=login,
        member_id=stable_id("member", member_key),
        must_change_password=False,
        roles=tuple(
            RoleGrant(
                stable_id(id_kind, id_value),
                role,
                None if global_scope else cooperative_id,
            )
            for id_kind, id_value, role in roles
        ),
    )
