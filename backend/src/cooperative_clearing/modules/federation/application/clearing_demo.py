"""Deterministic operator-ready inter-node clearing demo records."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.clearing.domain.engine import RoundingMode
from cooperative_clearing.modules.federation.application.common import federation_actor
from cooperative_clearing.modules.federation.application.demo import DEMO_NODE_CODE
from cooperative_clearing.modules.federation.application.inter_node_clearing import (
    InterNodeClearingService,
)
from cooperative_clearing.modules.federation.domain.federated_clearing import (
    FederatedClearingPolicy,
)
from cooperative_clearing.modules.federation.infrastructure.clearing_models import (
    FederatedClearingCycle,
    FederatedClearingPolicyRecord,
    InterNodeObligation,
)
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.journal.domain.crypto import payload_hash
from cooperative_clearing.shared.core.config import Settings


async def seed_demo_inter_node_clearing(session: AsyncSession, settings: Settings) -> None:
    cycle_id = stable_id("demo-federated-clearing-cycle", "regional-supplies-01")
    if await session.get(FederatedClearingCycle, cycle_id) is not None:
        return
    service = InterNodeClearingService(settings)
    finalizer = _principal(
        settings,
        login="auditor",
        member="demo-member-pavel",
        role=RoleCode.CLEARING_FINALIZER,
    )
    operator = _principal(
        settings,
        login="registrar",
        member="demo-member-anna",
        role=RoleCode.CLEARING_OPERATOR,
    )
    finalizer_actor = await federation_actor(session, finalizer, {RoleCode.CLEARING_FINALIZER})
    operator_actor = await federation_actor(session, operator, {RoleCode.CLEARING_OPERATOR})
    policy = (
        await session.execute(
            select(FederatedClearingPolicyRecord).where(
                FederatedClearingPolicyRecord.policy_code == "DEMO-REGIONAL-CLEARING",
                FederatedClearingPolicyRecord.policy_version == 1,
            )
        )
    ).scalar_one_or_none()
    if policy is None:
        policy = await service.create_policy(
            session,
            user_id=finalizer.user_id,
            actor=finalizer_actor,
            policy_code="DEMO-REGIONAL-CLEARING",
            valuation_unit="DEMO",
            policy=FederatedClearingPolicy(
                policy_version=1,
                decimal_scale=2,
                rounding_mode=RoundingMode.DOWN,
                minimum_operation=Decimal("0.01"),
                max_iterations=10_000,
                max_cycle_length=8,
                prepare_ttl_seconds=900,
            ),
        )
        await session.flush()
    obligation_id = stable_id("demo-inter-node-obligation", "local-to-demo-peer")
    if await session.get(InterNodeObligation, obligation_id) is None:
        await service.register_obligation(
            session,
            actor=operator_actor,
            home_node_code=settings.node_code,
            debtor_node_code=settings.node_code,
            creditor_node_code=DEMO_NODE_CODE.lower(),
            unit_code="DEMO",
            amount=Decimal("40.00"),
            source_reference="DEMO-SUPPLY-DELIVERY-001",
            source_event_hash=payload_hash(
                {
                    "source": "DEMO-SUPPLY-DELIVERY-001",
                    "debtor_node_code": settings.node_code,
                    "creditor_node_code": DEMO_NODE_CODE.lower(),
                    "amount": "40.00",
                    "unit_code": "DEMO",
                }
            ),
            liquidity_class="STANDARD",
            obligation_id=obligation_id,
        )
        await session.flush()
    now = datetime.now(UTC).replace(microsecond=0)
    await service.create_cycle(
        session,
        user_id=operator.user_id,
        actor=operator_actor,
        cycle_id=cycle_id,
        cycle_code="DEMO-REGIONAL-SUPPLIES-01",
        coordinator_node_code=settings.node_code,
        policy=policy,
        period_start=now - timedelta(days=7),
        period_end=now + timedelta(minutes=1),
        participant_node_codes=(settings.node_code, DEMO_NODE_CODE.lower()),
    )


def _principal(
    settings: Settings,
    *,
    login: str,
    member: str,
    role: RoleCode,
) -> Principal:
    return Principal(
        user_id=stable_id("bootstrap-user", login),
        session_id=stable_id("demo-session", f"{login}:{role.value}:federated-clearing"),
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
