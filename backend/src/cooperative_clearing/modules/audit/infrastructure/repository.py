"""Append-only audit writes and idempotent command bookkeeping."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.models import (
    AuditEntry,
    IdempotencyRecord,
)
from cooperative_clearing.shared.domain.errors import DomainError


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        action: str,
        object_type: str,
        outcome: str,
        actor_user_id: UUID | None = None,
        object_id: UUID | None = None,
        cooperative_id: UUID | None = None,
        request_id: UUID | None = None,
        reason_code: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> UUID:
        entry_id = uuid4()
        self.session.add(
            AuditEntry(
                id=entry_id,
                actor_user_id=actor_user_id,
                action=action,
                object_type=object_type,
                object_id=object_id,
                cooperative_id=cooperative_id,
                request_id=request_id,
                outcome=outcome,
                reason_code=reason_code,
                payload=payload or {},
            )
        )
        return entry_id

    async def list_recent(self, *, limit: int = 100) -> list[AuditEntry]:
        result = await self.session.execute(
            select(AuditEntry)
            .order_by(AuditEntry.occurred_at.desc(), AuditEntry.id.desc())
            .limit(limit)
        )
        return list(result.scalars())


def request_payload_hash(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class IdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def begin(
        self,
        *,
        actor_user_id: UUID,
        operation: str,
        idempotency_key: str,
        request_hash: str,
    ) -> IdempotencyRecord:
        if not idempotency_key.strip() or len(idempotency_key) > 100:
            raise DomainError(
                code="IDEMPOTENCY_KEY_INVALID",
                message_key="errors.request.idempotency_key_invalid",
                status_code=422,
            )
        record_id = uuid4()
        statement = (
            insert(IdempotencyRecord)
            .values(
                id=record_id,
                actor_user_id=actor_user_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status="PROCESSING",
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            .on_conflict_do_nothing(
                index_elements=["actor_user_id", "operation", "idempotency_key"]
            )
            .returning(IdempotencyRecord.id)
        )
        inserted = (await self.session.execute(statement)).scalar_one_or_none()
        if inserted is not None:
            result = await self.session.execute(
                select(IdempotencyRecord).where(IdempotencyRecord.id == inserted)
            )
            return result.scalar_one()

        result = await self.session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_user_id == actor_user_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )
        existing = result.scalar_one()
        if existing.request_hash != request_hash:
            raise DomainError(
                code="IDEMPOTENCY_KEY_REUSED",
                message_key="errors.request.idempotency_key_reused",
                status_code=409,
            )
        if existing.status != "COMPLETED":
            raise DomainError(
                code="COMMAND_ALREADY_PROCESSING",
                message_key="errors.request.command_already_processing",
                retryable=True,
                status_code=409,
            )
        return existing

    @staticmethod
    def complete(
        record: IdempotencyRecord,
        *,
        response_status: int,
        response_payload: dict[str, object],
    ) -> None:
        record.status = "COMPLETED"
        record.response_status = response_status
        record.response_payload = response_payload
