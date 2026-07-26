"""Hold automatic actions while an explainable anti-fraud signal is active."""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.risk.domain.types import AntifraudSubjectType
from cooperative_clearing.modules.risk.infrastructure.models import AntifraudSignal
from cooperative_clearing.shared.domain.errors import DomainError

ACTIVE_HOLD_STATUSES = {"OPEN", "IN_REVIEW", "CONFIRMED"}


async def require_antifraud_action_allowed(
    session: AsyncSession,
    *,
    cooperative_id: UUID,
    subjects: Iterable[tuple[AntifraudSubjectType, UUID]],
) -> None:
    unique_subjects = tuple(dict.fromkeys(subjects))
    if not unique_subjects:
        return
    conditions = [
        and_(
            AntifraudSignal.subject_type == subject_type.value,
            AntifraudSignal.subject_id == subject_id,
        )
        for subject_type, subject_id in unique_subjects
    ]
    signal = (
        await session.execute(
            select(AntifraudSignal)
            .where(
                AntifraudSignal.cooperative_id == cooperative_id,
                AntifraudSignal.automation_action == "HOLD",
                AntifraudSignal.status.in_(ACTIVE_HOLD_STATUSES),
                or_(*conditions),
            )
            .order_by(
                AntifraudSignal.last_seen_at.desc(),
                AntifraudSignal.id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if signal is None:
        return
    raise DomainError(
        code="RISK_ANTIFRAUD_MANUAL_REVIEW_REQUIRED",
        message_key="errors.risk.antifraud_manual_review_required",
        parameters={
            "signal_id": str(signal.id),
            "rule_code": signal.rule_code,
            "subject_type": signal.subject_type,
        },
        status_code=409,
    )
