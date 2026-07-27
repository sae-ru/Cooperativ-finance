"""The shipped demo exposes one bounded compensation awaiting its recipient."""

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from cooperative_clearing.cli import seed_demo
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.risk.application.compensation_demo import (
    DEMO_INCIDENT_REFERENCE,
)
from cooperative_clearing.modules.risk.infrastructure.models import (
    CompensationTransfer,
    LiabilityCase,
    ShareAccount,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database


async def _snapshot(database: Database) -> tuple[object, ...]:
    async with database.session() as session:
        transfer = (
            await session.execute(
                select(CompensationTransfer)
                .join(
                    LiabilityCase,
                    LiabilityCase.id == CompensationTransfer.liability_case_id,
                )
                .where(LiabilityCase.incident_reference == DEMO_INCIDENT_REFERENCE)
            )
        ).scalar_one()
        source = await session.get(ShareAccount, transfer.source_account_id)
        destination = await session.get(ShareAccount, transfer.destination_account_id)
        count = await session.scalar(
            select(func.count())
            .select_from(CompensationTransfer)
            .where(CompensationTransfer.liability_case_id == transfer.liability_case_id)
        )
        assert source and destination
        return (
            transfer.id,
            transfer.authorized_event_id,
            transfer.status,
            transfer.recipient_member_id,
            source.balance,
            source.protected_amount,
            source.executed_not_settled,
            destination.balance,
            count,
        )


@pytest.mark.integration
async def test_demo_compensation_is_pending_bounded_and_idempotent() -> None:
    suffix = uuid4().hex[:12]
    settings = Settings(
        service_name=f"compensation-demo-{suffix}",
        blob_root=Path(f"/tmp/compensation-demo-{suffix}"),
    )
    await seed_demo(settings)
    database = Database.from_settings(settings)
    try:
        before = await _snapshot(database)
        await seed_demo(settings)
        after = await _snapshot(database)
    finally:
        await database.dispose()

    assert before == after
    assert before[2:] == (
        "PENDING_ACCEPTANCE",
        stable_id("member", "demo-member-ivan"),
        Decimal("100"),
        Decimal("40"),
        Decimal("15"),
        Decimal("5"),
        1,
    )
