import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.modules.crisis.application.service import CrisisService
from cooperative_clearing.modules.crisis.domain.types import (
    CrisisCapability,
    CrisisType,
    RationFormula,
)
from cooperative_clearing.modules.crisis.infrastructure.models import (
    CrisisMandate,
    RationingPlan,
    RationingRule,
    ReserveTarget,
)
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.inventory.infrastructure.models import EvidenceBlob
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database


def principal(
    login: str,
    member_key: str,
    cooperative_id: UUID,
    role: RoleCode,
) -> Principal:
    assignment_kind = "bootstrap-role" if role is RoleCode.COOPERATIVE_ADMIN else "demo-role"
    return Principal(
        user_id=stable_id("bootstrap-user", login),
        session_id=uuid4(),
        login=login,
        member_id=stable_id("member", member_key),
        must_change_password=False,
        roles=(
            RoleGrant(
                stable_id(assignment_kind, f"{login}:{role.value}"),
                role,
                cooperative_id,
            ),
        ),
    )


@pytest.mark.integration
async def test_reserve_target_approval_atomically_retires_previous_policy() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"reserve-rotation-{suffix}",
        blob_root=Path(f"/tmp/reserve-rotation-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    cooperative_id = stable_id("cooperative", settings.node_code)
    operator = principal("security", "demo-member-elena", cooperative_id, RoleCode.CRISIS_OPERATOR)
    controller = principal(
        "auditor", "demo-member-pavel", cooperative_id, RoleCode.CRISIS_CONTROLLER
    )
    service = CrisisService(settings)
    resource_code = f"ROTATION_{suffix.upper()}"
    try:
        async with database.session() as session:
            policies: list[ReserveTarget] = []
            for version, target_quantity in enumerate((Decimal("10"), Decimal("15")), start=1):
                result = await service.propose_reserve_target(
                    session,
                    principal=operator,
                    cooperative_id=cooperative_id,
                    resource_code=resource_code,
                    resource_name="Rotation invariant reserve",
                    unit_code="KG",
                    target_quantity=target_quantity,
                    critical_minimum=Decimal("2"),
                    warning_coverage_days=Decimal("5"),
                    critical_coverage_days=Decimal("2"),
                    max_snapshot_age_hours=24,
                    terms={"rotation_test": True, "version": version},
                    idempotency_key=f"{suffix}-target-propose-{version}",
                    request_id=None,
                )
                policy = await session.get(ReserveTarget, result.object_id)
                assert policy is not None
                await service.approve_reserve_target(
                    session,
                    principal=controller,
                    target_id=policy.id,
                    expected_version=1,
                    idempotency_key=f"{suffix}-target-approve-{version}",
                    request_id=None,
                )
                policies.append(policy)
            await session.commit()
            assert policies[0].status == "RETIRED" and policies[0].version == 3
            assert policies[1].status == "ACTIVE" and policies[1].version == 2
            assert [item.policy_version for item in policies] == [1, 2]
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_parallel_confirmation_cannot_reserve_the_same_stock_twice() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"crisis-concurrency-{suffix}",
        blob_root=Path(f"/tmp/crisis-concurrency-{suffix}"),
    )
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    cooperative_id = stable_id("cooperative", settings.node_code)
    operator = principal("security", "demo-member-elena", cooperative_id, RoleCode.CRISIS_OPERATOR)
    controller = principal(
        "auditor", "demo-member-pavel", cooperative_id, RoleCode.CRISIS_CONTROLLER
    )
    reviewer = principal(
        "registrar", "demo-member-anna", cooperative_id, RoleCode.CRISIS_CONTROLLER
    )
    service = CrisisService(settings)

    try:
        async with database.session() as session:
            target = (
                await session.execute(
                    select(ReserveTarget).where(
                        ReserveTarget.cooperative_id == cooperative_id,
                        ReserveTarget.resource_code == "CABBAGE",
                        ReserveTarget.status == "ACTIVE",
                    )
                )
            ).scalar_one()
            evidence_id = (
                await session.execute(
                    select(EvidenceBlob.id)
                    .where(
                        EvidenceBlob.cooperative_id == cooperative_id,
                        EvidenceBlob.status == "READY",
                    )
                    .order_by(EvidenceBlob.created_at, EvidenceBlob.id)
                    .limit(1)
                )
            ).scalar_one()
            now = datetime.now(UTC)
            mandate_result = await service.propose_mandate(
                session,
                principal=operator,
                cooperative_id=cooperative_id,
                mandate_code=f"CONCURRENT-{suffix}",
                crisis_type=CrisisType.CRITICAL_SHORTAGE,
                scope_payload={"resource": "CABBAGE", "test": "concurrency"},
                capabilities=(CrisisCapability.ENABLE_RATIONING,),
                evidence_ids=(evidence_id,),
                rationale="Bounded concurrent reservation invariant test.",
                exit_criteria="Competing plans are reconciled.",
                safe_state="All unissued reservations are cancelled.",
                starts_at=now - timedelta(minutes=1),
                review_at=now + timedelta(hours=1),
                expires_at=now + timedelta(hours=2),
                maximum_end_at=now + timedelta(hours=3),
                idempotency_key=f"{suffix}-mandate-propose",
                request_id=None,
            )
            mandate = await session.get(CrisisMandate, mandate_result.object_id)
            assert mandate is not None
            await service.activate_mandate(
                session,
                principal=controller,
                mandate_id=mandate.id,
                expected_version=1,
                terms_hash=mandate.terms_hash,
                idempotency_key=f"{suffix}-mandate-activate",
                request_id=None,
            )
            rule_result = await service.propose_rationing_rule(
                session,
                principal=operator,
                mandate_id=mandate.id,
                target_id=target.id,
                formula=RationFormula.EQUAL_PER_MEMBER,
                eligibility_policy={"active_membership": True},
                protected_minimum=Decimal("0"),
                maximum_per_member=Decimal("30"),
                period_hours=1,
                idempotency_key=f"{suffix}-rule-propose",
                request_id=None,
            )
            rule = await session.get(RationingRule, rule_result.object_id)
            assert rule is not None
            await service.approve_rationing_rule(
                session,
                principal=controller,
                rule_id=rule.id,
                expected_version=1,
                terms_hash=rule.terms_hash,
                idempotency_key=f"{suffix}-rule-approve",
                request_id=None,
            )
            plan_inputs: list[tuple[UUID, str]] = []
            for index, member_key in enumerate(("demo-member-anna", "demo-member-nina"), start=1):
                result = await service.preview_rationing_plan(
                    session,
                    principal=operator,
                    rule_id=rule.id,
                    eligible_members=((stable_id("member", member_key), 1),),
                    idempotency_key=f"{suffix}-preview-{index}",
                    request_id=None,
                )
                plan = await session.get(RationingPlan, result.object_id)
                assert plan is not None and plan.total_allocated == Decimal("30")
                plan_inputs.append((plan.id, plan.allocations_hash))
            await session.commit()

        async def confirm(index: int, plan_id: UUID, allocations_hash: str) -> str:
            async with database.session() as session:
                try:
                    await CrisisService(settings).confirm_rationing_plan(
                        session,
                        principal=controller,
                        plan_id=plan_id,
                        expected_version=1,
                        allocations_hash=allocations_hash,
                        idempotency_key=f"{suffix}-confirm-{index}",
                        request_id=None,
                    )
                    await session.commit()
                    return "CONFIRMED"
                except DomainError as exc:
                    await session.rollback()
                    return exc.code

        outcomes = await asyncio.gather(
            *(
                confirm(index, plan_id, digest)
                for index, (plan_id, digest) in enumerate(plan_inputs)
            )
        )
        assert sorted(outcomes) == ["CONFIRMED", "RATIONING_INPUT_STALE"]

        async with database.session() as session:
            plans = list(
                (
                    await session.execute(
                        select(RationingPlan).where(
                            RationingPlan.id.in_([item[0] for item in plan_inputs])
                        )
                    )
                ).scalars()
            )
            assert sorted(item.status for item in plans) == ["CONFIRMED", "PREVIEWED"]
            for index, plan in enumerate(plans):
                await service.cancel_rationing_plan(
                    session,
                    principal=controller,
                    plan_id=plan.id,
                    expected_version=plan.version,
                    rationale="Concurrent test reservation reconciliation.",
                    idempotency_key=f"{suffix}-cancel-{index}",
                    request_id=None,
                )
            replacement_result = await service.propose_rationing_rule(
                session,
                principal=operator,
                mandate_id=mandate.id,
                target_id=target.id,
                formula=RationFormula.WEIGHTED_PRIORITY,
                eligibility_policy={"active_membership": True, "replacement": True},
                protected_minimum=Decimal("1"),
                maximum_per_member=Decimal("20"),
                period_hours=1,
                idempotency_key=f"{suffix}-rule-replacement-propose",
                request_id=None,
            )
            replacement = await session.get(RationingRule, replacement_result.object_id)
            assert replacement is not None
            await service.approve_rationing_rule(
                session,
                principal=controller,
                rule_id=replacement.id,
                expected_version=1,
                terms_hash=replacement.terms_hash,
                idempotency_key=f"{suffix}-rule-replacement-approve",
                request_id=None,
            )
            await service.close_mandate(
                session,
                principal=reviewer,
                mandate_id=mandate.id,
                expected_version=2,
                reconciliation_note="Both competing plans were reconciled without issuance.",
                corrective_actions=("Retain cooperative-scoped serialization.",),
                idempotency_key=f"{suffix}-mandate-close",
                request_id=None,
            )
            await session.commit()
            closed_mandate = await session.get(CrisisMandate, mandate.id)
            assert closed_mandate is not None and closed_mandate.status == "CLOSED"
            assert all(item.status == "CANCELLED" for item in plans)
            old_rule = await session.get(RationingRule, rule.id)
            assert old_rule is not None and old_rule.status == "RETIRED"
            assert replacement.status == "ACTIVE"
    finally:
        await database.dispose()
