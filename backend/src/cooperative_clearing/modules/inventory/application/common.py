"""Shared command mechanics for the inventory vertical slice."""

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
class InventoryCommandResult:
    event_id: UUID
    object_id: UUID
    replayed: bool


async def begin_command(
    session: AsyncSession,
    principal: Principal,
    operation: str,
    idempotency_key: str,
    payload: object,
) -> tuple[IdempotencyRecord, InventoryCommandResult | None]:
    record = await IdempotencyRepository(session).begin(
        actor_user_id=principal.user_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_hash=request_payload_hash(payload),
    )
    if record.status == "COMPLETED":
        stored = record.response_payload or {}
        return record, InventoryCommandResult(
            event_id=UUID(str(stored["event_id"])),
            object_id=UUID(str(stored["object_id"])),
            replayed=True,
        )
    return record, None


def complete_command(
    record: IdempotencyRecord, event_id: UUID, object_id: UUID
) -> InventoryCommandResult:
    IdempotencyRepository.complete(
        record,
        response_status=201,
        response_payload={"event_id": str(event_id), "object_id": str(object_id)},
    )
    return InventoryCommandResult(event_id, object_id, False)


def actor_claim(
    principal: Principal,
    cooperative_id: UUID,
    roles: set[RoleCode],
    *,
    exact_assignment_id: UUID | None = None,
) -> ActorClaim:
    if principal.member_id is None:
        raise inventory_error("PHYSICAL_ACTOR_REQUIRED", 403)
    for grant in principal.roles:
        if (
            grant.role in roles
            and grant.cooperative_id in {None, cooperative_id}
            and (exact_assignment_id is None or grant.assignment_id == exact_assignment_id)
        ):
            return ActorClaim(
                person_id=principal.member_id,
                organization_id=cooperative_id,
                role_assignment_id=grant.assignment_id,
            )
    raise inventory_error("AUTHORIZATION_DENIED", 403)


def bounded_text(value: str, code: str, maximum: int) -> str:
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise inventory_error(code, 422)
    return normalized


def inventory_error(code: str, status_code: int = 422) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.inventory.{code.lower()}",
        status_code=status_code,
    )
