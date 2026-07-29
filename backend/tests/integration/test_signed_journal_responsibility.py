import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.cli import initialize_node
from cooperative_clearing.modules.audit.infrastructure.models import AuditEntry
from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    Member,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.journal.application.outbox import (
    DispatchResult,
    dispatch_outbox_batch,
)
from cooperative_clearing.modules.journal.application.service import (
    OUTBOX_TOPIC,
    ActorClaim,
    SignedJournalService,
    verify_journal,
)
from cooperative_clearing.modules.journal.domain.assurance import (
    AccountabilityParty,
    AccountabilityPartyKind,
    CommandAssurance,
    ExposureCategory,
    ExposureClaim,
    ExposureEffect,
    actor_party,
    member_party,
)
from cooperative_clearing.modules.journal.infrastructure.models import (
    ConsumerReceipt,
    EventSignature,
    OutboxMessage,
    SignedEvent,
)
from cooperative_clearing.modules.node.infrastructure.models import NodeProfile
from cooperative_clearing.modules.responsibility.application.service import (
    ResponsibilityService,
    assignment_summary,
    canonical_preview,
)
from cooperative_clearing.modules.responsibility.domain.types import ApprovalDecision
from cooperative_clearing.modules.responsibility.infrastructure.models import (
    ResponsibilityAssignment,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.security import PasswordService
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database


def principal(
    user_id: UUID,
    member_id: UUID,
    assignment_id: UUID,
    role: RoleCode,
    cooperative_id: UUID | None,
) -> Principal:
    return Principal(
        user_id=user_id,
        session_id=uuid4(),
        login=f"journal-{user_id}",
        member_id=member_id,
        must_change_password=False,
        roles=(RoleGrant(assignment_id, role, cooperative_id),),
    )


def proposal_hash(
    *,
    cooperative_id: UUID,
    member_id: UUID,
    role_assignment_id: UUID,
    subject_type: str,
    subject_id: UUID,
    scope: str,
    max_exposure: Decimal,
    exposure_unit: str,
) -> str:
    return canonical_preview(
        assignment_summary(
            cooperative_id=cooperative_id,
            member_id=member_id,
            role_assignment_id=role_assignment_id,
            subject_type=subject_type,
            subject_id=subject_id,
            scope=scope,
            max_exposure=max_exposure,
            exposure_unit=exposure_unit,
            valid_until=None,
        )
    ).summary_hash


async def create_people_and_roles(
    database: Database,
) -> tuple[UUID, Principal, Principal, Principal]:
    cooperative_id = uuid4()
    creator_user_id, approver_user_id, target_user_id = uuid4(), uuid4(), uuid4()
    creator_member_id, approver_member_id, target_member_id = uuid4(), uuid4(), uuid4()
    creator_role_id, approver_role_id, target_role_id = uuid4(), uuid4(), uuid4()
    password_hash = PasswordService().hash("integration-password-value")
    async with database.session() as session:
        session.add(
            Cooperative(
                id=cooperative_id,
                code=f"journal-{cooperative_id.hex[:12]}",
                name="Journal integration cooperative",
                status="ACTIVE",
            )
        )
        for member_id, name in (
            (creator_member_id, "Creator"),
            (approver_member_id, "Approver"),
            (target_member_id, "Target"),
        ):
            session.add(Member(id=member_id, display_name=name, status="ACTIVE"))
        await session.flush()
        for user_id, member_id in (
            (creator_user_id, creator_member_id),
            (approver_user_id, approver_member_id),
            (target_user_id, target_member_id),
        ):
            session.add(
                UserAccount(
                    id=user_id,
                    login=f"journal-{user_id}",
                    password_hash=password_hash,
                    member_id=member_id,
                    status="ACTIVE",
                    must_change_password=False,
                )
            )
        await session.flush()
        session.add_all(
            [
                RoleAssignment(
                    id=creator_role_id,
                    user_id=creator_user_id,
                    role_code="RISK_ADMIN",
                    cooperative_id=cooperative_id,
                    status="ACTIVE",
                    granted_by_user_id=None,
                    approved_by_user_id=None,
                ),
                RoleAssignment(
                    id=approver_role_id,
                    user_id=approver_user_id,
                    role_code="AUDITOR",
                    cooperative_id=None,
                    status="ACTIVE",
                    granted_by_user_id=None,
                    approved_by_user_id=None,
                ),
                RoleAssignment(
                    id=target_role_id,
                    user_id=target_user_id,
                    role_code="DATA_STEWARD",
                    cooperative_id=cooperative_id,
                    status="ACTIVE",
                    granted_by_user_id=None,
                    approved_by_user_id=None,
                ),
            ]
        )
        await session.commit()
    return (
        target_member_id,
        principal(
            creator_user_id,
            creator_member_id,
            creator_role_id,
            RoleCode.RISK_ADMIN,
            cooperative_id,
        ),
        principal(
            approver_user_id,
            approver_member_id,
            approver_role_id,
            RoleCode.AUDITOR,
            None,
        ),
        principal(
            target_user_id,
            target_member_id,
            target_role_id,
            RoleCode.DATA_STEWARD,
            cooperative_id,
        ),
    )


async def drain_pending_outbox(database: Database) -> None:
    while True:
        async with database.session() as session:
            result = await dispatch_outbox_batch(
                session,
                instance_id=uuid4(),
                batch_size=500,
                lease_seconds=30,
                max_attempts=1,
            )
            await session.commit()
        if result.claimed == 0:
            return


async def append_probe_event(
    session: AsyncSession,
    *,
    settings: Settings,
    actor: Principal,
    event_type: str,
) -> UUID:
    assert actor.member_id is not None
    role = actor.roles[0]
    appended = await SignedJournalService(settings).append(
        session,
        event_type=event_type,
        aggregate_type="atomicity_probe",
        aggregate_id=uuid4(),
        aggregate_version=1,
        actor=ActorClaim(
            person_id=actor.member_id,
            organization_id=role.cooperative_id,
            role_assignment_id=role.assignment_id,
        ),
        payload={"probe": event_type},
    )
    return appended.event_id


@pytest.mark.integration
async def test_responsibility_command_is_signed_chained_and_dispatched() -> None:
    settings = Settings(service_name="responsibility-integration")
    await initialize_node(settings)
    database = Database.from_settings(settings)
    try:
        target_member_id, creator, approver, target = await create_people_and_roles(database)
        cooperative_id = creator.roles[0].cooperative_id
        assert cooperative_id is not None
        subject_id = uuid4()
        service = ResponsibilityService(settings)
        expected_summary_hash = proposal_hash(
            cooperative_id=cooperative_id,
            member_id=target_member_id,
            role_assignment_id=target.roles[0].assignment_id,
            subject_type="demo_asset",
            subject_id=subject_id,
            scope="Custody and condition reporting",
            max_exposure=Decimal("1250.5000"),
            exposure_unit="UNIT",
        )
        async with database.session() as session:
            proposed = await service.propose(
                session,
                principal=creator,
                cooperative_id=cooperative_id,
                member_id=target_member_id,
                role_assignment_id=target.roles[0].assignment_id,
                subject_type="demo_asset",
                subject_id=subject_id,
                scope="Custody and condition reporting",
                max_exposure=Decimal("1250.5000"),
                exposure_unit="UNIT",
                valid_until=None,
                expected_summary_hash=expected_summary_hash,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            assignment = await session.get(ResponsibilityAssignment, proposed.object_id)
            assert assignment is not None
            assert assignment.status == "PENDING_APPROVAL"
            assert assignment.created_event_id == proposed.event_id
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SignedEvent)
                    .where(SignedEvent.aggregate_id == assignment.id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AuditEntry)
                    .where(AuditEntry.object_id == assignment.id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(OutboxMessage)
                    .where(OutboxMessage.event_id == proposed.event_id)
                )
                == 1
            )

        async with database.session() as session:
            approved = await service.decide(
                session,
                principal=approver,
                assignment_id=proposed.object_id,
                decision=ApprovalDecision.APPROVE,
                reason_code="INDEPENDENT_REVIEW",
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        async with database.session() as session:
            assignment = await session.get(ResponsibilityAssignment, proposed.object_id)
            assert assignment is not None and assignment.status == "PENDING_ACCEPTANCE"
            accepted = await service.accept(
                session,
                principal=target,
                assignment_id=assignment.id,
                expected_version=assignment.version,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()
        assert approved.event_id != accepted.event_id

        async with database.session() as session:
            assignment = await session.get(ResponsibilityAssignment, proposed.object_id)
            assert assignment is not None and assignment.status == "ACTIVE"
            node = (
                await session.execute(
                    select(NodeProfile).where(NodeProfile.node_code == settings.node_code)
                )
            ).scalar_one()
            report = await verify_journal(session, node.id)
            assert report.ok is True
            first_dispatch = await dispatch_outbox_batch(
                session,
                instance_id=uuid4(),
                batch_size=500,
                lease_seconds=30,
                max_attempts=5,
            )
            await session.commit()
            assert first_dispatch.published >= 3
        async with database.session() as session:
            await dispatch_outbox_batch(
                session,
                instance_id=uuid4(),
                batch_size=500,
                lease_seconds=30,
                max_attempts=5,
            )
            receipts = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(ConsumerReceipt)
                        .where(
                            ConsumerReceipt.event_id.in_(
                                [proposed.event_id, approved.event_id, accepted.event_id]
                            )
                        )
                    )
                ).scalar_one()
            )
            target_statuses = list(
                (
                    await session.execute(
                        select(OutboxMessage.status).where(
                            OutboxMessage.event_id.in_(
                                [proposed.event_id, approved.event_id, accepted.event_id]
                            )
                        )
                    )
                ).scalars()
            )
            assert len(target_statuses) == 3
            assert set(target_statuses) == {"PUBLISHED"}
            assert receipts == 3

        async with database.session() as session:
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(SignedEvent)
                    .where(SignedEvent.event_id == proposed.event_id)
                    .values(event_hash="sha256:" + "0" * 64)
                )
            await session.rollback()
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_responsibility_rolls_back_state_event_audit_and_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(service_name="responsibility-rollback-integration")
    await initialize_node(settings)
    database = Database.from_settings(settings)
    try:
        target_member_id, creator, _approver, target = await create_people_and_roles(database)
        cooperative_id = creator.roles[0].cooperative_id
        assert cooperative_id is not None
        rollback_subject_id = uuid4()
        expected_summary_hash = proposal_hash(
            cooperative_id=cooperative_id,
            member_id=target_member_id,
            role_assignment_id=target.roles[0].assignment_id,
            subject_type="rollback_asset",
            subject_id=rollback_subject_id,
            scope="Rollback proof",
            max_exposure=Decimal("1.0000"),
            exposure_unit="UNIT",
        )
        async with database.session() as session:
            before = (
                int(
                    (
                        await session.execute(
                            select(func.count()).select_from(ResponsibilityAssignment)
                        )
                    ).scalar_one()
                ),
                int(
                    (
                        await session.execute(select(func.count()).select_from(SignedEvent))
                    ).scalar_one()
                ),
                int(
                    (
                        await session.execute(select(func.count()).select_from(AuditEntry))
                    ).scalar_one()
                ),
                int(
                    (
                        await session.execute(select(func.count()).select_from(OutboxMessage))
                    ).scalar_one()
                ),
            )

        async def fail_audit(*_args: object, **_kwargs: object) -> UUID:
            raise RuntimeError("simulated audit failure")

        monkeypatch.setattr(AuditRepository, "record", fail_audit)
        async with database.session() as session:
            with pytest.raises(RuntimeError, match="simulated audit failure"):
                await ResponsibilityService(settings).propose(
                    session,
                    principal=creator,
                    cooperative_id=cooperative_id,
                    member_id=target_member_id,
                    role_assignment_id=target.roles[0].assignment_id,
                    subject_type="rollback_asset",
                    subject_id=rollback_subject_id,
                    scope="Rollback proof",
                    max_exposure=Decimal("1.0000"),
                    exposure_unit="UNIT",
                    valid_until=None,
                    expected_summary_hash=expected_summary_hash,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
            await session.rollback()

        async with database.session() as session:
            after = (
                int(
                    (
                        await session.execute(
                            select(func.count()).select_from(ResponsibilityAssignment)
                        )
                    ).scalar_one()
                ),
                int(
                    (
                        await session.execute(select(func.count()).select_from(SignedEvent))
                    ).scalar_one()
                ),
                int(
                    (
                        await session.execute(select(func.count()).select_from(AuditEntry))
                    ).scalar_one()
                ),
                int(
                    (
                        await session.execute(select(func.count()).select_from(OutboxMessage))
                    ).scalar_one()
                ),
            )
        assert after == before
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_concurrent_commands_preserve_node_sequence() -> None:
    settings = Settings(service_name="journal-concurrency-integration")
    await initialize_node(settings)
    database = Database.from_settings(settings)
    try:
        target_member_id, creator, _approver, target = await create_people_and_roles(database)
        cooperative_id = creator.roles[0].cooperative_id
        assert cooperative_id is not None

        async def propose_one(subject_id: UUID) -> UUID:
            expected_summary_hash = proposal_hash(
                cooperative_id=cooperative_id,
                member_id=target_member_id,
                role_assignment_id=target.roles[0].assignment_id,
                subject_type="concurrent_asset",
                subject_id=subject_id,
                scope="Concurrent chain proof",
                max_exposure=Decimal("2.0000"),
                exposure_unit="UNIT",
            )
            async with database.session() as session:
                result = await ResponsibilityService(settings).propose(
                    session,
                    principal=creator,
                    cooperative_id=cooperative_id,
                    member_id=target_member_id,
                    role_assignment_id=target.roles[0].assignment_id,
                    subject_type="concurrent_asset",
                    subject_id=subject_id,
                    scope="Concurrent chain proof",
                    max_exposure=Decimal("2.0000"),
                    exposure_unit="UNIT",
                    valid_until=None,
                    expected_summary_hash=expected_summary_hash,
                    idempotency_key=str(uuid4()),
                    request_id=uuid4(),
                )
                await session.commit()
                return result.event_id

        event_ids = await asyncio.gather(propose_one(uuid4()), propose_one(uuid4()))
        assert event_ids[0] != event_ids[1]
        async with database.session() as session:
            sequences = list(
                (
                    await session.execute(
                        select(SignedEvent.local_sequence)
                        .where(SignedEvent.event_id.in_(event_ids))
                        .order_by(SignedEvent.local_sequence)
                    )
                ).scalars()
            )
            assert sequences[1] - sequences[0] == 1
            node = (
                await session.execute(
                    select(NodeProfile).where(NodeProfile.node_code == settings.node_code)
                )
            ).scalar_one()
            assert (await verify_journal(session, node.id)).ok is True
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_database_rejects_event_without_signature_and_outbox() -> None:
    settings = Settings(service_name="journal-atomicity-guard-integration")
    await initialize_node(settings)
    database = Database.from_settings(settings)
    try:
        _target_member_id, creator, _approver, _target = await create_people_and_roles(database)
        async with database.session() as session:
            before = (
                int(await session.scalar(select(func.count()).select_from(SignedEvent)) or 0),
                int(await session.scalar(select(func.count()).select_from(EventSignature)) or 0),
                int(await session.scalar(select(func.count()).select_from(OutboxMessage)) or 0),
            )
            event_id = await append_probe_event(
                session,
                settings=settings,
                actor=creator,
                event_type="journal.atomicity_guard_probe",
            )
            delivery_rows = [
                row for row in list(session.new) if isinstance(row, (EventSignature, OutboxMessage))
            ]
            assert {type(row) for row in delivery_rows} == {EventSignature, OutboxMessage}
            for row in delivery_rows:
                session.expunge(row)

            with pytest.raises(DBAPIError) as failed_commit:
                await session.commit()
            error = str(failed_commit.value)
            assert "EVENT_DELIVERY_ATOMICITY_VIOLATION" in error
            assert str(event_id) not in error
            await session.rollback()

        async with database.session() as session:
            after = (
                int(await session.scalar(select(func.count()).select_from(SignedEvent)) or 0),
                int(await session.scalar(select(func.count()).select_from(EventSignature)) or 0),
                int(await session.scalar(select(func.count()).select_from(OutboxMessage)) or 0),
            )
        assert after == before
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_worker_crash_and_concurrent_restart_deliver_once() -> None:
    settings = Settings(service_name="outbox-recovery-integration")
    await initialize_node(settings)
    database = Database.from_settings(settings)
    try:
        await drain_pending_outbox(database)
        target_member_id, creator, _approver, target = await create_people_and_roles(database)
        cooperative_id = creator.roles[0].cooperative_id
        assert cooperative_id is not None
        subject_id = uuid4()
        expected_summary_hash = proposal_hash(
            cooperative_id=cooperative_id,
            member_id=target_member_id,
            role_assignment_id=target.roles[0].assignment_id,
            subject_type="worker_outage_asset",
            subject_id=subject_id,
            scope="Worker outage proof",
            max_exposure=Decimal("4.0000"),
            exposure_unit="UNIT",
        )
        async with database.session() as session:
            proposed = await ResponsibilityService(settings).propose(
                session,
                principal=creator,
                cooperative_id=cooperative_id,
                member_id=target_member_id,
                role_assignment_id=target.roles[0].assignment_id,
                subject_type="worker_outage_asset",
                subject_id=subject_id,
                scope="Worker outage proof",
                max_exposure=Decimal("4.0000"),
                exposure_unit="UNIT",
                valid_until=None,
                expected_summary_hash=expected_summary_hash,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            assignment = await session.get(ResponsibilityAssignment, proposed.object_id)
            message = (
                await session.execute(
                    select(OutboxMessage).where(OutboxMessage.event_id == proposed.event_id)
                )
            ).scalar_one()
            assert assignment is not None and assignment.status == "PENDING_APPROVAL"
            assert message.status == "PENDING"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ConsumerReceipt)
                    .where(ConsumerReceipt.event_id == proposed.event_id)
                )
                == 0
            )

        async with database.session() as session:
            interrupted = await dispatch_outbox_batch(
                session,
                instance_id=uuid4(),
                batch_size=1,
                lease_seconds=30,
                max_attempts=5,
            )
            assert interrupted.claimed == 1 and interrupted.published == 1
            await session.rollback()

        async with database.session() as session:
            message = (
                await session.execute(
                    select(OutboxMessage).where(OutboxMessage.event_id == proposed.event_id)
                )
            ).scalar_one()
            assert message.status == "PENDING"
            assert message.attempt_count == 0
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ConsumerReceipt)
                    .where(ConsumerReceipt.event_id == proposed.event_id)
                )
                == 0
            )

        async def restart_worker() -> DispatchResult:
            async with database.session() as session:
                result = await dispatch_outbox_batch(
                    session,
                    instance_id=uuid4(),
                    batch_size=1,
                    lease_seconds=30,
                    max_attempts=5,
                )
                await session.commit()
                return result

        results = await asyncio.gather(restart_worker(), restart_worker())
        assert sum(result.claimed for result in results) == 1
        assert sum(result.published for result in results) == 1

        async with database.session() as session:
            message = (
                await session.execute(
                    select(OutboxMessage).where(OutboxMessage.event_id == proposed.event_id)
                )
            ).scalar_one()
            assert message.status == "PUBLISHED"
            assert message.attempt_count == 1
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ConsumerReceipt)
                    .where(ConsumerReceipt.event_id == proposed.event_id)
                )
                == 1
            )
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_outbox_tamper_is_detected_quarantined_and_recoverable() -> None:
    settings = Settings(service_name="outbox-tamper-integration")
    await initialize_node(settings)
    database = Database.from_settings(settings)
    try:
        await drain_pending_outbox(database)
        _target_member_id, creator, _approver, _target = await create_people_and_roles(database)
        async with database.session() as session:
            event_id = await append_probe_event(
                session,
                settings=settings,
                actor=creator,
                event_type="journal.outbox_tamper_probe",
            )
            await session.commit()

        async with database.session() as session:
            message = (
                await session.execute(
                    select(OutboxMessage).where(OutboxMessage.event_id == event_id)
                )
            ).scalar_one()
            original_payload = dict(message.payload)
            message.payload = {**original_payload, "event_hash": "sha256:" + "0" * 64}
            await session.commit()

        async with database.session() as session:
            node = (
                await session.execute(
                    select(NodeProfile).where(NodeProfile.node_code == settings.node_code)
                )
            ).scalar_one()
            report = await verify_journal(session, node.id)
            failure = next(item for item in report.failures if item.event_id == event_id)
            assert failure.code == "OUTBOX_PAYLOAD_INVALID"
            dispatched = await dispatch_outbox_batch(
                session,
                instance_id=uuid4(),
                batch_size=1,
                lease_seconds=30,
                max_attempts=1,
            )
            await session.commit()
            assert dispatched.quarantined == 1

        async with database.session() as session:
            message = (
                await session.execute(
                    select(OutboxMessage).where(OutboxMessage.event_id == event_id)
                )
            ).scalar_one()
            assert message.status == "QUARANTINED"
            assert message.last_error_code == "OUTBOX_EVENT_ENVELOPE_MISMATCH"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ConsumerReceipt)
                    .where(ConsumerReceipt.event_id == event_id)
                )
                == 0
            )
            message.payload = original_payload
            message.status = "PENDING"
            message.attempt_count = 0
            message.available_at = datetime.now(UTC)
            message.last_error_code = None
            await session.commit()

        async with database.session() as session:
            recovered = await dispatch_outbox_batch(
                session,
                instance_id=uuid4(),
                batch_size=1,
                lease_seconds=30,
                max_attempts=5,
            )
            await session.commit()
            assert recovered.published == 1
        async with database.session() as session:
            node = (
                await session.execute(
                    select(NodeProfile).where(NodeProfile.node_code == settings.node_code)
                )
            ).scalar_one()
            assert (await verify_journal(session, node.id)).ok is True
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_outbox_retries_then_quarantines_invalid_message() -> None:
    settings = Settings(service_name="outbox-quarantine-integration")
    await initialize_node(settings)
    database = Database.from_settings(settings)
    try:
        target_member_id, creator, _approver, target = await create_people_and_roles(database)
        cooperative_id = creator.roles[0].cooperative_id
        assert cooperative_id is not None
        subject_id = uuid4()
        expected_summary_hash = proposal_hash(
            cooperative_id=cooperative_id,
            member_id=target_member_id,
            role_assignment_id=target.roles[0].assignment_id,
            subject_type="invalid_outbox_asset",
            subject_id=subject_id,
            scope="Outbox quarantine proof",
            max_exposure=Decimal("3.0000"),
            exposure_unit="UNIT",
        )
        async with database.session() as session:
            proposed = await ResponsibilityService(settings).propose(
                session,
                principal=creator,
                cooperative_id=cooperative_id,
                member_id=target_member_id,
                role_assignment_id=target.roles[0].assignment_id,
                subject_type="invalid_outbox_asset",
                subject_id=subject_id,
                scope="Outbox quarantine proof",
                max_exposure=Decimal("3.0000"),
                exposure_unit="UNIT",
                valid_until=None,
                expected_summary_hash=expected_summary_hash,
                idempotency_key=str(uuid4()),
                request_id=uuid4(),
            )
            await session.commit()

        async with database.session() as session:
            message = (
                await session.execute(
                    select(OutboxMessage).where(OutboxMessage.event_id == proposed.event_id)
                )
            ).scalar_one()
            message_id = message.id
            message.topic = "unsupported.topic"
            await session.commit()

        async with database.session() as session:
            first = await dispatch_outbox_batch(
                session,
                instance_id=uuid4(),
                batch_size=500,
                lease_seconds=30,
                max_attempts=2,
            )
            await session.commit()
            assert first.retried >= 1

        async with database.session() as session:
            pending_message = await session.get(OutboxMessage, message_id, with_for_update=True)
            assert pending_message is not None and pending_message.status == "PENDING"
            pending_message.available_at = datetime.now(UTC)
            await session.commit()
        async with database.session() as session:
            second = await dispatch_outbox_batch(
                session,
                instance_id=uuid4(),
                batch_size=500,
                lease_seconds=30,
                max_attempts=2,
            )
            await session.commit()
            assert second.quarantined >= 1
        async with database.session() as session:
            quarantined_message = await session.get(OutboxMessage, message_id)
            assert quarantined_message is not None
            assert quarantined_message.status == "QUARANTINED"
            assert quarantined_message.last_error_code == "OUTBOX_TOPIC_UNSUPPORTED"
            receipt_count = await session.scalar(
                select(func.count())
                .select_from(ConsumerReceipt)
                .where(ConsumerReceipt.event_id == proposed.event_id)
            )
            assert receipt_count == 0
            quarantined_message.topic = OUTBOX_TOPIC
            quarantined_message.status = "PENDING"
            quarantined_message.attempt_count = 0
            quarantined_message.available_at = datetime.now(UTC)
            quarantined_message.last_error_code = None
            await session.commit()
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_signed_event_rejects_forged_actor_role_and_scope() -> None:
    settings = Settings(service_name="journal-actor-assurance-integration")
    await initialize_node(settings)
    database = Database.from_settings(settings)
    try:
        target_member_id, creator, _approver, target = await create_people_and_roles(database)
        cooperative_id = creator.roles[0].cooperative_id
        assert cooperative_id is not None
        assert creator.member_id is not None
        journal = SignedJournalService(settings)

        async with database.session() as session:
            with pytest.raises(DomainError) as forged_person:
                await journal.append(
                    session,
                    event_type="assurance.forged_person_rejected",
                    aggregate_type="assurance_probe",
                    aggregate_id=uuid4(),
                    aggregate_version=1,
                    actor=ActorClaim(
                        person_id=target_member_id,
                        organization_id=cooperative_id,
                        role_assignment_id=creator.roles[0].assignment_id,
                    ),
                    payload={"expected": "rejected"},
                )
            assert forged_person.value.code == "ACTOR_PERSON_MISMATCH"
            await session.rollback()

        async with database.session() as session:
            with pytest.raises(DomainError) as forged_scope:
                await journal.append(
                    session,
                    event_type="assurance.forged_scope_rejected",
                    aggregate_type="assurance_probe",
                    aggregate_id=uuid4(),
                    aggregate_version=1,
                    actor=ActorClaim(
                        person_id=creator.member_id,
                        organization_id=uuid4(),
                        role_assignment_id=creator.roles[0].assignment_id,
                    ),
                    payload={"expected": "rejected"},
                )
            assert forged_scope.value.code == "ACTOR_SCOPE_MISMATCH"
            await session.rollback()

        async with database.session() as session:
            assignment = await session.get(
                RoleAssignment, target.roles[0].assignment_id, with_for_update=True
            )
            assert assignment is not None
            assignment.status = "REVOKED"
            await session.commit()

        async with database.session() as session:
            with pytest.raises(DomainError) as inactive_role:
                await journal.append(
                    session,
                    event_type="assurance.inactive_role_rejected",
                    aggregate_type="assurance_probe",
                    aggregate_id=uuid4(),
                    aggregate_version=1,
                    actor=ActorClaim(
                        person_id=target_member_id,
                        organization_id=cooperative_id,
                        role_assignment_id=target.roles[0].assignment_id,
                    ),
                    payload={"expected": "rejected"},
                )
            assert inactive_role.value.code == "ACTOR_ROLE_INACTIVE"
            await session.rollback()
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_critical_event_requires_signed_evidence_and_exposure_snapshot() -> None:
    settings = Settings(service_name="journal-command-assurance-integration")
    await initialize_node(settings)
    database = Database.from_settings(settings)
    try:
        target_member_id, creator, approver, _target = await create_people_and_roles(database)
        cooperative_id = creator.roles[0].cooperative_id
        assert cooperative_id is not None
        assert creator.member_id is not None
        assert approver.member_id is not None
        actor = ActorClaim(
            person_id=creator.member_id,
            organization_id=cooperative_id,
            role_assignment_id=creator.roles[0].assignment_id,
        )
        journal = SignedJournalService(settings)
        subject_id = uuid4()
        exposure = ExposureClaim(
            category=ExposureCategory.SHARE,
            effect=ExposureEffect.CREATE,
            subject_type="share_account",
            subject_id=subject_id,
            amount=Decimal("5.00"),
            unit="SHARE",
            maximum_loss=Decimal("5.00"),
        )

        async with database.session() as session:
            with pytest.raises(DomainError) as missing:
                await journal.append(
                    session,
                    event_type="shares.contribution_recorded",
                    aggregate_type="share_account",
                    aggregate_id=subject_id,
                    aggregate_version=1,
                    actor=actor,
                    payload={"amount": "5.00"},
                )
            assert missing.value.code == "CRITICAL_COMMAND_ASSURANCE_REQUIRED"
            await session.rollback()

        async with database.session() as session:
            with pytest.raises(DomainError) as no_evidence:
                await journal.append(
                    session,
                    event_type="shares.contribution_recorded",
                    aggregate_type="share_account",
                    aggregate_id=subject_id,
                    aggregate_version=1,
                    actor=actor,
                    payload={"amount": "5.00"},
                    assurance=CommandAssurance(
                        on_behalf_of=actor_party(actor),
                        exposure=exposure,
                        evidence_refs=(),
                        next_responsible=(member_party(target_member_id),),
                    ),
                )
            assert no_evidence.value.code == "CRITICAL_COMMAND_EVIDENCE_REQUIRED"
            await session.rollback()

        evidence = ({"event_id": str(uuid4()), "kind": "SHARE_CONTRIBUTION_SOURCE"},)
        async with database.session() as session:
            with pytest.raises(DomainError) as wrong_scope:
                await journal.append(
                    session,
                    event_type="shares.contribution_recorded",
                    aggregate_type="share_account",
                    aggregate_id=subject_id,
                    aggregate_version=1,
                    actor=actor,
                    payload={"amount": "5.00"},
                    assurance=CommandAssurance(
                        on_behalf_of=AccountabilityParty(
                            kind=AccountabilityPartyKind.COOPERATIVE,
                            reference=str(uuid4()),
                            role_assignment_id=creator.roles[0].assignment_id,
                        ),
                        exposure=exposure,
                        evidence_refs=evidence,
                        next_responsible=(member_party(target_member_id),),
                    ),
                )
            assert wrong_scope.value.code == "COMMAND_ASSURANCE_SCOPE_MISMATCH"
            await session.rollback()

        async with database.session() as session:
            with pytest.raises(DomainError) as evidence_conflict:
                await journal.append(
                    session,
                    event_type="shares.contribution_recorded",
                    aggregate_type="share_account",
                    aggregate_id=subject_id,
                    aggregate_version=1,
                    actor=actor,
                    payload={"amount": "5.00"},
                    evidence=list(evidence),
                    assurance=CommandAssurance(
                        on_behalf_of=actor_party(actor),
                        exposure=exposure,
                        evidence_refs=evidence,
                        next_responsible=(member_party(target_member_id),),
                    ),
                )
            assert evidence_conflict.value.code == "COMMAND_ASSURANCE_EVIDENCE_CONFLICT"
            await session.rollback()

        async with database.session() as session:
            appended = await journal.append(
                session,
                event_type="shares.contribution_recorded",
                aggregate_type="share_account",
                aggregate_id=subject_id,
                aggregate_version=1,
                actor=actor,
                payload={"amount": "5.00"},
                assurance=CommandAssurance(
                    on_behalf_of=actor_party(actor),
                    exposure=exposure,
                    evidence_refs=evidence,
                    next_responsible=(member_party(target_member_id),),
                    attesters=(
                        member_party(
                            approver.member_id,
                            approver.roles[0].assignment_id,
                        ),
                    ),
                    approvers=(
                        member_party(
                            creator.member_id,
                            creator.roles[0].assignment_id,
                        ),
                    ),
                ),
            )
            await session.commit()

        async with database.session() as session:
            event = await session.get(SignedEvent, appended.event_id)
            assert event is not None
            assurance = event.payload["_command_assurance"]
            assert isinstance(assurance, dict)
            assert assurance["format"] == "critical-command-assurance-v2"
            assert assurance["performed_by"] == {
                "person_id": str(creator.member_id),
                "user_id": str(creator.user_id),
            }
            assert assurance["on_behalf_of"] == {
                "kind": "COOPERATIVE",
                "reference": str(cooperative_id),
                "role_assignment_id": str(creator.roles[0].assignment_id),
            }
            assert assurance["role"] == {
                "assignment_id": str(creator.roles[0].assignment_id),
                "code": creator.roles[0].role.value,
                "source": "ASSIGNMENT",
            }
            assert assurance["scope"] == {
                "actor_organization_id": str(cooperative_id),
                "role_cooperative_id": str(cooperative_id),
            }
            assert assurance["attesters"] == [
                {
                    "kind": "MEMBER",
                    "reference": str(approver.member_id),
                    "role_assignment_id": str(approver.roles[0].assignment_id),
                }
            ]
            assert assurance["approvers"] == [
                {
                    "kind": "MEMBER",
                    "reference": str(creator.member_id),
                    "role_assignment_id": str(creator.roles[0].assignment_id),
                }
            ]
            assert assurance["next_responsible"] == [
                {
                    "kind": "MEMBER",
                    "reference": str(target_member_id),
                    "role_assignment_id": None,
                }
            ]
            assert assurance["exposure"] == {
                "category": "SHARE",
                "effect": "CREATE",
                "subject_type": "share_account",
                "subject_id": str(subject_id),
                "amount": "5.00",
                "unit": "SHARE",
                "maximum_loss": "5.00",
                "basis_refs": [],
            }
            assert event.evidence == list(evidence)
            node = (
                await session.execute(
                    select(NodeProfile).where(NodeProfile.node_code == settings.node_code)
                )
            ).scalar_one()
            report = await verify_journal(session, node.id)
            assert report.ok
    finally:
        await database.dispose()
