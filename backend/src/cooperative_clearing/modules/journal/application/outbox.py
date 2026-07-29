"""Lease-aware, idempotent local outbox dispatch."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.journal.application.service import OUTBOX_TOPIC
from cooperative_clearing.modules.journal.infrastructure.models import (
    ConsumerReceipt,
    OutboxMessage,
    SignedEvent,
)

LOCAL_CONSUMER = "local-journal-projector-v1"


@dataclass(frozen=True, slots=True)
class DispatchResult:
    claimed: int
    published: int
    retried: int
    quarantined: int


async def dispatch_outbox_batch(
    session: AsyncSession,
    *,
    instance_id: UUID,
    batch_size: int,
    lease_seconds: int,
    max_attempts: int,
) -> DispatchResult:
    now = datetime.now(UTC)
    ready = or_(
        and_(OutboxMessage.status == "PENDING", OutboxMessage.available_at <= now),
        and_(
            OutboxMessage.status == "PROCESSING",
            OutboxMessage.lease_expires_at.is_not(None),
            OutboxMessage.lease_expires_at <= now,
        ),
    )
    rows = list(
        (
            await session.execute(
                select(OutboxMessage, SignedEvent)
                .join(SignedEvent, SignedEvent.event_id == OutboxMessage.event_id)
                .where(ready)
                .order_by(OutboxMessage.available_at, OutboxMessage.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True, of=OutboxMessage)
            )
        ).all()
    )
    published = retried = quarantined = 0
    for message, event in rows:
        message.status = "PROCESSING"
        message.lease_owner = instance_id
        message.lease_expires_at = now + timedelta(seconds=lease_seconds)
        message.attempt_count += 1
        error_code = _validate_message(message, event)
        if error_code is None:
            receipt = (
                insert(ConsumerReceipt)
                .values(
                    id=uuid4(),
                    event_id=message.event_id,
                    consumer_name=LOCAL_CONSUMER,
                )
                .on_conflict_do_nothing(index_elements=["event_id", "consumer_name"])
            )
            await session.execute(receipt)
            message.status = "PUBLISHED"
            message.published_at = now
            message.lease_owner = None
            message.lease_expires_at = None
            message.last_error_code = None
            published += 1
            continue

        message.last_error_code = error_code
        message.lease_owner = None
        message.lease_expires_at = None
        if message.attempt_count >= max_attempts:
            message.status = "QUARANTINED"
            quarantined += 1
        else:
            message.status = "PENDING"
            delay_seconds = min(300, 2 ** min(message.attempt_count, 8))
            message.available_at = now + timedelta(seconds=delay_seconds)
            retried += 1
    return DispatchResult(len(rows), published, retried, quarantined)


def _validate_message(message: OutboxMessage, event: SignedEvent) -> str | None:
    if message.topic != OUTBOX_TOPIC:
        return "OUTBOX_TOPIC_UNSUPPORTED"
    if str(message.payload.get("event_id")) != str(message.event_id):
        return "OUTBOX_EVENT_ID_MISMATCH"
    expected_payload = {
        "event_type": event.event_type,
        "event_hash": event.event_hash,
        "node_id": str(event.node_id),
        "local_sequence": event.local_sequence,
    }
    if any(message.payload.get(key) != value for key, value in expected_payload.items()):
        return "OUTBOX_EVENT_ENVELOPE_MISMATCH"
    return None
