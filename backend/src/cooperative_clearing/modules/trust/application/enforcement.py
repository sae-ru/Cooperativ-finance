"""Cross-module enforcement for active protective measures and sanctions."""

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.trust.domain.types import trust_error
from cooperative_clearing.modules.trust.infrastructure.models import ProtectiveMeasure, Sanction

GUARANTEE_CREATE = "GUARANTEE_CREATE"
RISK_COMMITMENT_CREATE = "RISK_COMMITMENT_CREATE"
ROLE_ASSIGNMENT_CREATE = "ROLE_ASSIGNMENT_CREATE"


def restriction_applies(
    *,
    restriction_type: str,
    scope: dict[str, object],
    action: str,
    target_role: str | None = None,
) -> bool:
    blocked = scope.get("blocked_actions", [])
    if isinstance(blocked, list) and any(
        isinstance(value, str) and value in {"*", action} for value in blocked
    ):
        return True
    if restriction_type == "BLOCK_NEW_GUARANTEES" and action == GUARANTEE_CREATE:
        return True
    if restriction_type not in {"SUSPEND_ROLE", "TERMINATE_ROLE"}:
        return False
    if action != ROLE_ASSIGNMENT_CREATE:
        return False
    roles = scope.get("role_codes", [])
    return target_role is None or not isinstance(roles, list) or not roles or target_role in roles


async def require_member_action_allowed(
    session: AsyncSession,
    *,
    cooperative_id: UUID | None,
    member_ids: Iterable[UUID],
    action: str,
    target_role: str | None = None,
) -> None:
    subjects = set(member_ids)
    if not subjects:
        return
    now = datetime.now(UTC)
    measures_statement = select(ProtectiveMeasure).where(
        ProtectiveMeasure.subject_member_id.in_(subjects),
        ProtectiveMeasure.status == "ACTIVE",
        ProtectiveMeasure.expires_at > now,
    )
    sanctions_statement = select(Sanction).where(
        Sanction.subject_member_id.in_(subjects),
        Sanction.status == "ACTIVE",
        or_(Sanction.expires_at.is_(None), Sanction.expires_at > now),
    )
    if cooperative_id is not None:
        from cooperative_clearing.modules.trust.infrastructure.models import TrustCase

        measures_statement = measures_statement.join(
            TrustCase, TrustCase.id == ProtectiveMeasure.case_id
        ).where(TrustCase.cooperative_id == cooperative_id)
        sanctions_statement = sanctions_statement.join(
            TrustCase, TrustCase.id == Sanction.case_id
        ).where(TrustCase.cooperative_id == cooperative_id)
    measures = list((await session.execute(measures_statement)).scalars())
    sanctions = list((await session.execute(sanctions_statement)).scalars())
    measure_restricted = any(
        restriction_applies(
            restriction_type=item.measure_type,
            scope=item.scope,
            action=action,
            target_role=target_role,
        )
        for item in measures
    )
    sanction_restricted = any(
        restriction_applies(
            restriction_type=item.measure_type,
            scope=item.scope,
            action=action,
            target_role=target_role,
        )
        for item in sanctions
    )
    if measure_restricted or sanction_restricted:
        raise trust_error("ACTION_RESTRICTED", 403)
