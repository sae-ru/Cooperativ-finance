import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

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
from cooperative_clearing.modules.journal.application.outbox import dispatch_outbox_batch
from cooperative_clearing.modules.journal.application.service import verify_journal
from cooperative_clearing.modules.journal.infrastructure.models import (
    ConsumerReceipt,
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
    finally:
        await database.dispose()
