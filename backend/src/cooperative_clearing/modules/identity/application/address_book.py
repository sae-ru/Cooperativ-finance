"""Private participant address-book commands."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.models import IdempotencyRecord
from cooperative_clearing.modules.audit.infrastructure.repository import (
    AuditRepository,
    IdempotencyRepository,
    request_payload_hash,
)
from cooperative_clearing.modules.identity.domain.types import (
    Principal,
    RoleGrantSource,
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    Member,
    Membership,
    ParticipantAddress,
)
from cooperative_clearing.modules.journal.application.service import (
    ActorClaim,
    AppendedEvent,
    SignedJournalService,
)
from cooperative_clearing.modules.journal.domain.assurance import (
    CommandAssurance,
    ExposureCategory,
    ExposureClaim,
    ExposureEffect,
    member_party,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError


@dataclass(frozen=True)
class AddressCommandResult:
    event_id: UUID
    object_id: UUID
    replayed: bool = False


@dataclass(frozen=True)
class AddressValues:
    cooperative_id: UUID
    label: str
    purpose: str
    region_code: str
    address_text: str
    contact_name: str
    contact_phone: str
    instructions: str | None
    is_default_pickup: bool
    is_default_delivery: bool

    def normalized(self) -> "AddressValues":
        return AddressValues(
            cooperative_id=self.cooperative_id,
            label=self.label.strip(),
            purpose=self.purpose.upper(),
            region_code=self.region_code.strip().upper(),
            address_text=self.address_text.strip(),
            contact_name=self.contact_name.strip(),
            contact_phone=self.contact_phone.strip(),
            instructions=(
                self.instructions.strip()
                if self.instructions and self.instructions.strip()
                else None
            ),
            is_default_pickup=self.is_default_pickup,
            is_default_delivery=self.is_default_delivery,
        )


class ParticipantAddressBookService:
    def __init__(self, settings: Settings) -> None:
        self.journal = SignedJournalService(settings)

    async def create(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        member_id: UUID,
        values: AddressValues,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> AddressCommandResult:
        values = values.normalized()
        self._validate_defaults(values)
        payload = self._command_payload(member_id, values)
        record, replay = await self._begin(
            session, principal, "PARTICIPANT_ADDRESS_CREATE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        membership = await self._require_active_membership(
            session, member_id=member_id, cooperative_id=values.cooperative_id
        )
        actor = self._actor(principal, values.cooperative_id)
        address = ParticipantAddress(
            id=uuid4(),
            member_id=member_id,
            cooperative_id=values.cooperative_id,
            label=values.label,
            purpose=values.purpose,
            region_code=values.region_code,
            address_text=values.address_text,
            contact_name=values.contact_name,
            contact_phone=values.contact_phone,
            instructions=values.instructions,
            is_default_pickup=values.is_default_pickup,
            is_default_delivery=values.is_default_delivery,
            status="ACTIVE",
            version=1,
            event_tracking_required=True,
        )
        affected = await self._clear_other_defaults(
            session, member_id=member_id, values=values
        )
        event_id = uuid4()
        address.last_event_id = event_id
        for item in affected:
            item.last_event_id = event_id
            item.event_tracking_required = True
        event = await self._append_participant_address_event(
            session,
            event_type="identity.participant_address_created",
            effect=ExposureEffect.CREATE,
            principal=principal,
            actor=actor,
            record=record,
            membership=membership,
            address=address,
            affected=affected,
            event_id=event_id,
        )
        session.add(address)
        await self._audit(
            session,
            action="PARTICIPANT_ADDRESS_CREATED",
            principal=principal,
            address=address,
            event_id=event.event_id,
            request_id=request_id,
        )
        return self._complete(record, event.event_id, address.id)

    async def update(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        member_id: UUID,
        address_id: UUID,
        expected_version: int,
        values: AddressValues,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> AddressCommandResult:
        values = values.normalized()
        self._validate_defaults(values)
        payload = {
            **self._command_payload(member_id, values),
            "address_id": str(address_id),
            "expected_version": expected_version,
        }
        record, replay = await self._begin(
            session, principal, "PARTICIPANT_ADDRESS_UPDATE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        address = await self._owned_active_address(
            session, member_id=member_id, address_id=address_id
        )
        if address.version != expected_version:
            raise self._conflict()
        membership = await self._require_active_membership(
            session, member_id=member_id, cooperative_id=values.cooperative_id
        )
        actor = self._actor(principal, values.cooperative_id)
        affected = await self._clear_other_defaults(
            session, member_id=member_id, values=values, exclude_id=address.id
        )
        address.cooperative_id = values.cooperative_id
        address.label = values.label
        address.purpose = values.purpose
        address.region_code = values.region_code
        address.address_text = values.address_text
        address.contact_name = values.contact_name
        address.contact_phone = values.contact_phone
        address.instructions = values.instructions
        address.is_default_pickup = values.is_default_pickup
        address.is_default_delivery = values.is_default_delivery
        address.updated_at = datetime.now(UTC)
        address.version += 1
        event_id = uuid4()
        address.last_event_id = event_id
        address.event_tracking_required = True
        for item in affected:
            item.last_event_id = event_id
            item.event_tracking_required = True
        event = await self._append_participant_address_event(
            session,
            event_type="identity.participant_address_updated",
            effect=ExposureEffect.CORRECT,
            principal=principal,
            actor=actor,
            record=record,
            membership=membership,
            address=address,
            affected=affected,
            event_id=event_id,
        )
        await self._audit(
            session,
            action="PARTICIPANT_ADDRESS_UPDATED",
            principal=principal,
            address=address,
            event_id=event.event_id,
            request_id=request_id,
        )
        return self._complete(record, event.event_id, address.id)

    async def archive(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        member_id: UUID,
        address_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> AddressCommandResult:
        payload = {
            "member_id": str(member_id),
            "address_id": str(address_id),
            "expected_version": expected_version,
        }
        record, replay = await self._begin(
            session, principal, "PARTICIPANT_ADDRESS_ARCHIVE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        address = await self._owned_active_address(
            session, member_id=member_id, address_id=address_id
        )
        if address.version != expected_version:
            raise self._conflict()
        membership = await self._require_active_membership(
            session,
            member_id=member_id,
            cooperative_id=address.cooperative_id,
        )
        actor = self._actor(principal, address.cooperative_id)
        address.status = "ARCHIVED"
        address.is_default_pickup = False
        address.is_default_delivery = False
        address.updated_at = datetime.now(UTC)
        address.version += 1
        event_id = uuid4()
        address.last_event_id = event_id
        address.event_tracking_required = True
        event = await self._append_participant_address_event(
            session,
            event_type="identity.participant_address_archived",
            effect=ExposureEffect.CLOSE,
            principal=principal,
            actor=actor,
            record=record,
            membership=membership,
            address=address,
            affected=[],
            event_id=event_id,
        )
        await self._audit(
            session,
            action="PARTICIPANT_ADDRESS_ARCHIVED",
            principal=principal,
            address=address,
            event_id=event.event_id,
            request_id=request_id,
        )
        return self._complete(record, event.event_id, address.id)

    @staticmethod
    async def _require_active_membership(
        session: AsyncSession, *, member_id: UUID, cooperative_id: UUID
    ) -> Membership:
        member = await session.get(Member, member_id)
        membership = await session.scalar(
            select(Membership).where(
                Membership.member_id == member_id,
                Membership.cooperative_id == cooperative_id,
                Membership.status == "ACTIVE",
            )
        )
        if member is None or member.status != "ACTIVE" or membership is None:
            raise DomainError(
                code="ACTIVE_MEMBERSHIP_REQUIRED",
                message_key="errors.identity.active_membership_required",
                status_code=403,
            )
        return membership

    @staticmethod
    async def _owned_active_address(
        session: AsyncSession, *, member_id: UUID, address_id: UUID
    ) -> ParticipantAddress:
        address = await session.scalar(
            select(ParticipantAddress)
            .where(
                ParticipantAddress.id == address_id,
                ParticipantAddress.member_id == member_id,
                ParticipantAddress.status == "ACTIVE",
            )
            .with_for_update()
        )
        if address is None:
            raise DomainError(
                code="PARTICIPANT_ADDRESS_NOT_FOUND",
                message_key="errors.identity.participant_address_not_found",
                status_code=404,
            )
        return address

    @staticmethod
    async def _clear_other_defaults(
        session: AsyncSession,
        *,
        member_id: UUID,
        values: AddressValues,
        exclude_id: UUID | None = None,
    ) -> list[ParticipantAddress]:
        if not values.is_default_pickup and not values.is_default_delivery:
            return []
        statement = select(ParticipantAddress).where(
            ParticipantAddress.member_id == member_id,
            ParticipantAddress.status == "ACTIVE",
        )
        if exclude_id is not None:
            statement = statement.where(ParticipantAddress.id != exclude_id)
        if values.is_default_pickup and not values.is_default_delivery:
            statement = statement.where(ParticipantAddress.is_default_pickup.is_(True))
        elif values.is_default_delivery and not values.is_default_pickup:
            statement = statement.where(ParticipantAddress.is_default_delivery.is_(True))
        else:
            statement = statement.where(
                ParticipantAddress.is_default_pickup.is_(True)
                | ParticipantAddress.is_default_delivery.is_(True)
            )
        affected = list((await session.execute(statement.with_for_update())).scalars())
        now = datetime.now(UTC)
        for address in affected:
            if values.is_default_pickup:
                address.is_default_pickup = False
            if values.is_default_delivery:
                address.is_default_delivery = False
            address.updated_at = now
            address.version += 1
        return affected

    @staticmethod
    def _actor(principal: Principal, cooperative_id: UUID) -> ActorClaim:
        if principal.member_id is None:
            raise DomainError(
                code="PERSONAL_ACTOR_REQUIRED",
                message_key="errors.identity.personal_actor_required",
                status_code=403,
            )
        for grant in principal.roles:
            if (
                grant.source is RoleGrantSource.ASSIGNMENT
                and grant.cooperative_id in {None, cooperative_id}
            ):
                return ActorClaim(
                    person_id=principal.member_id,
                    organization_id=cooperative_id,
                    role_assignment_id=grant.assignment_id,
                )
        raise DomainError(
            code="PERMANENT_ROLE_REQUIRED",
            message_key="errors.identity.permanent_role_required",
            status_code=403,
        )

    async def _append_participant_address_event(
        self,
        session: AsyncSession,
        *,
        event_type: str,
        effect: ExposureEffect,
        principal: Principal,
        actor: ActorClaim,
        record: IdempotencyRecord,
        membership: Membership,
        address: ParticipantAddress,
        affected: list[ParticipantAddress],
        event_id: UUID,
    ) -> AppendedEvent:
        evidence_refs: tuple[object, ...] = (
            {"idempotency_record_id": str(record.id)},
            {"authenticated_session_id": str(principal.session_id)},
            {"active_membership_id": str(membership.id)},
        )
        with session.no_autoflush:
            return await self.journal.append(
                session,
                event_type=event_type,
                aggregate_type="participant_address",
                aggregate_id=address.id,
                aggregate_version=address.version,
                actor=actor,
                payload={
                    "member_id": str(address.member_id),
                    "cooperative_id": str(address.cooperative_id),
                    "purpose": address.purpose,
                    "region_code": address.region_code,
                    "status": address.status,
                    "version": address.version,
                    "is_default_pickup": address.is_default_pickup,
                    "is_default_delivery": address.is_default_delivery,
                    "superseded_default_addresses": [
                        {"address_id": str(item.id), "version": item.version}
                        for item in affected
                    ],
                },
                assurance=CommandAssurance(
                    on_behalf_of=member_party(address.member_id),
                    exposure=ExposureClaim(
                        category=ExposureCategory.CUSTODY,
                        effect=effect,
                        subject_type="participant_address",
                        subject_id=address.id,
                        basis_refs=(record.request_hash, str(membership.id)),
                    ),
                    evidence_refs=evidence_refs,
                    next_responsible=(member_party(address.member_id),),
                    attesters=(
                        member_party(address.member_id, actor.role_assignment_id),
                    ),
                ),
                event_id=event_id,
            )
    @staticmethod
    def _validate_defaults(values: AddressValues) -> None:
        if values.is_default_pickup and values.purpose not in {"PICKUP", "BOTH"}:
            raise DomainError(
                code="ADDRESS_PURPOSE_INVALID",
                message_key="errors.identity.address_purpose_invalid",
                status_code=422,
            )
        if values.is_default_delivery and values.purpose not in {"DELIVERY", "BOTH"}:
            raise DomainError(
                code="ADDRESS_PURPOSE_INVALID",
                message_key="errors.identity.address_purpose_invalid",
                status_code=422,
            )

    @staticmethod
    def _command_payload(member_id: UUID, values: AddressValues) -> dict[str, object]:
        return {
            "member_id": str(member_id),
            "cooperative_id": str(values.cooperative_id),
            "label": values.label,
            "purpose": values.purpose,
            "region_code": values.region_code,
            "address_text": values.address_text,
            "contact_name": values.contact_name,
            "contact_phone": values.contact_phone,
            "instructions": values.instructions,
            "is_default_pickup": values.is_default_pickup,
            "is_default_delivery": values.is_default_delivery,
        }

    @staticmethod
    async def _audit(
        session: AsyncSession,
        *,
        action: str,
        principal: Principal,
        address: ParticipantAddress,
        event_id: UUID,
        request_id: UUID | None,
    ) -> UUID:
        return await AuditRepository(session).record(
            action=action,
            object_type="ParticipantAddress",
            object_id=address.id,
            cooperative_id=address.cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={
                "member_id": str(address.member_id),
                "purpose": address.purpose,
                "region_code": address.region_code,
                "version": address.version,
                "signed_event_id": str(event_id),
            },
        )

    @staticmethod
    async def _begin(
        session: AsyncSession,
        principal: Principal,
        operation: str,
        idempotency_key: str,
        payload: object,
    ) -> tuple[IdempotencyRecord, AddressCommandResult | None]:
        record = await IdempotencyRepository(session).begin(
            actor_user_id=principal.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_payload_hash(payload),
        )
        if record.status == "COMPLETED":
            stored = record.response_payload or {}
            return record, AddressCommandResult(
                event_id=UUID(str(stored["event_id"])),
                object_id=UUID(str(stored["object_id"])),
                replayed=True,
            )
        return record, None

    @staticmethod
    def _complete(
        record: IdempotencyRecord, event_id: UUID, object_id: UUID
    ) -> AddressCommandResult:
        IdempotencyRepository.complete(
            record,
            response_status=201,
            response_payload={"event_id": str(event_id), "object_id": str(object_id)},
        )
        return AddressCommandResult(event_id=event_id, object_id=object_id)

    @staticmethod
    def _conflict() -> DomainError:
        return DomainError(
            code="PARTICIPANT_ADDRESS_VERSION_CONFLICT",
            message_key="errors.identity.participant_address_version_conflict",
            status_code=409,
        )
