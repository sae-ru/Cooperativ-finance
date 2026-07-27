"""Seed one explainable anti-fraud signal through the production scan command."""

from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.risk.application.antifraud import AntifraudService
from cooperative_clearing.modules.risk.domain.types import AntifraudRuleCode
from cooperative_clearing.shared.core.config import Settings


async def seed_demo_antifraud(session: AsyncSession, settings: Settings) -> None:
    cooperative_id = stable_id("cooperative", settings.node_code)
    principal = Principal(
        user_id=stable_id("bootstrap-user", "security"),
        session_id=stable_id("demo-session", "security-antifraud"),
        login="security",
        member_id=stable_id("member", "demo-member-elena"),
        must_change_password=False,
        roles=(
            RoleGrant(
                stable_id("demo-role", "security:RISK_ADMIN"),
                RoleCode.RISK_ADMIN,
                cooperative_id,
            ),
        ),
    )
    await AntifraudService(settings).scan(
        session,
        principal=principal,
        cooperative_id=cooperative_id,
        lookback_hours=168,
        idempotency_key="demo-antifraud-scan-v4",
        request_id=None,
        rule_codes=frozenset({AntifraudRuleCode.OFFER_PRICE_OUTLIER}),
        subject_ids=frozenset({stable_id("federated-offer", "demo-milk-price-review")}),
    )
