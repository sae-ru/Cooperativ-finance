"""Node persistence without hidden commits."""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.node.infrastructure.models import (
    NodeProfile,
    SystemNotice,
    WorkerHeartbeat,
)
from cooperative_clearing.shared.core.config import Settings


class NodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def initialize_profile(self, settings: Settings) -> UUID:
        profile_id = uuid5(NAMESPACE_URL, f"cooperative-clearing:node:{settings.node_code}")
        statement = insert(NodeProfile).values(
            id=profile_id,
            node_code=settings.node_code,
            display_name=settings.node_display_name,
            environment=settings.environment.value,
            demo_data_loaded=False,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[NodeProfile.node_code],
            set_={
                "display_name": statement.excluded.display_name,
                "environment": statement.excluded.environment,
                "updated_at": datetime.now(UTC),
                "version": NodeProfile.version + 1,
            },
        )
        await self.session.execute(statement)
        return profile_id

    async def get_profile(self, node_code: str) -> NodeProfile | None:
        result = await self.session.execute(
            select(NodeProfile).where(NodeProfile.node_code == node_code)
        )
        return result.scalar_one_or_none()

    async def active_notices(self) -> list[SystemNotice]:
        result = await self.session.execute(
            select(SystemNotice)
            .where(SystemNotice.status == "ACTIVE")
            .order_by(SystemNotice.created_at.desc(), SystemNotice.id)
        )
        return list(result.scalars())

    async def seed_demo(self, settings: Settings) -> None:
        notices = (
            (
                "DEMO_BACKUP_DRILL_PENDING",
                "WARNING",
                "notices.demo.backup_drill_pending",
            ),
            (
                "DEMO_POLICY_REVIEW_SCHEDULED",
                "INFO",
                "notices.demo.policy_review_scheduled",
            ),
        )
        for code, severity, message_key in notices:
            notice_id = uuid5(NAMESPACE_URL, f"cooperative-clearing:demo-notice:{code}")
            statement = insert(SystemNotice).values(
                id=notice_id,
                code=code,
                severity=severity,
                status="ACTIVE",
                message_key=message_key,
                parameters={},
            )
            statement = statement.on_conflict_do_update(
                index_elements=[SystemNotice.code],
                set_={
                    "severity": statement.excluded.severity,
                    "status": statement.excluded.status,
                    "message_key": statement.excluded.message_key,
                    "parameters": statement.excluded.parameters,
                    "resolved_at": None,
                },
            )
            await self.session.execute(statement)
        await self.session.execute(
            update(NodeProfile)
            .where(NodeProfile.node_code == settings.node_code)
            .values(
                demo_data_loaded=True,
                updated_at=datetime.now(UTC),
                version=NodeProfile.version + 1,
            )
        )

    async def record_worker_heartbeat(
        self,
        *,
        worker_name: str,
        instance_id: UUID,
        release: str,
    ) -> None:
        statement = insert(WorkerHeartbeat).values(
            worker_name=worker_name,
            instance_id=instance_id,
            release=release,
            last_seen_at=datetime.now(UTC),
        )
        statement = statement.on_conflict_do_update(
            index_elements=[WorkerHeartbeat.worker_name],
            set_={
                "instance_id": statement.excluded.instance_id,
                "release": statement.excluded.release,
                "last_seen_at": statement.excluded.last_seen_at,
                "details": None,
            },
        )
        await self.session.execute(statement)

    async def get_worker_heartbeat(self, worker_name: str) -> WorkerHeartbeat | None:
        return await self.session.get(WorkerHeartbeat, worker_name)
