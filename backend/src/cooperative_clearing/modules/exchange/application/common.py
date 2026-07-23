"""Idempotency and actor helpers for exchange commands."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.models import IdempotencyRecord
from cooperative_clearing.modules.audit.infrastructure.repository import (
    IdempotencyRepository,
    request_payload_hash,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.journal.application.service import ActorClaim
from cooperative_clearing.shared.domain.errors import DomainError


@dataclass(frozen=True, slots=True)
class ExchangeCommandResult:
    event_id: UUID
    object_id: UUID
    replayed: bool


async def begin_exchange_command(
    session: AsyncSession,
    principal: Principal,
    operation: str,
    idempotency_key: str,
    payload: object,
) -> tuple[IdempotencyRecord, ExchangeCommandResult | None]:
    record = await IdempotencyRepository(session).begin(
        actor_user_id=principal.user_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_hash=request_payload_hash(payload),
    )
    if record.status == "COMPLETED":
        stored = record.response_payload or {}
        return record, ExchangeCommandResult(
            event_id=UUID(str(stored["event_id"])),
            object_id=UUID(str(stored["object_id"])),
            replayed=True,
        )
    return record, None


def complete_exchange_command(
    record: IdempotencyRecord, event_id: UUID, object_id: UUID
) -> ExchangeCommandResult:
    IdempotencyRepository.complete(
        record,
        response_status=201,
        response_payload={"event_id": str(event_id), "object_id": str(object_id)},
    )
    return ExchangeCommandResult(event_id, object_id, False)


def role_actor(principal: Principal, cooperative_id: UUID, roles: set[RoleCode]) -> ActorClaim:
    if principal.member_id is None:
        raise exchange_auth_error("PERSONAL_ACTOR_REQUIRED")
    for grant in principal.roles:
        if grant.role in roles and grant.cooperative_id in {None, cooperative_id}:
            return ActorClaim(
                person_id=principal.member_id,
                organization_id=cooperative_id,
                role_assignment_id=grant.assignment_id,
            )
    raise exchange_auth_error("AUTHORIZATION_DENIED")


def party_actor(principal: Principal, cooperative_id: UUID, member_id: UUID) -> ActorClaim:
    if principal.member_id != member_id:
        raise exchange_auth_error("PARTY_ACTOR_MISMATCH")
    for grant in principal.roles:
        if grant.cooperative_id in {None, cooperative_id}:
            return ActorClaim(
                person_id=member_id,
                organization_id=cooperative_id,
                role_assignment_id=grant.assignment_id,
            )
    raise exchange_auth_error("ACTIVE_ROLE_REQUIRED")


def exchange_auth_error(code: str) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.exchange.{code.lower()}",
        status_code=403,
    )
