"""Safe system status read model for the operator workspace."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cooperative_clearing.modules.node.application.readiness import (
    EXPECTED_SCHEMA_REVISION,
    ComponentCheck,
    ReadinessProbe,
)
from cooperative_clearing.modules.node.infrastructure.repository import NodeRepository
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import ServiceNotReadyError
from cooperative_clearing.shared.infrastructure.database import Database


@dataclass(frozen=True, slots=True)
class NoticeView:
    code: str
    severity: str
    message_key: str
    parameters: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SystemStatusView:
    status: str
    node_id: str
    node_code: str
    display_name: str
    environment: str
    demo_data_loaded: bool
    release: str
    schema_revision: str
    checks: tuple[ComponentCheck, ...]
    worker_status: str
    worker_last_seen_at: datetime | None
    notices: tuple[NoticeView, ...]


class GetSystemStatus:
    def __init__(self, *, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.readiness = ReadinessProbe(database=database, settings=settings)

    async def execute(self) -> SystemStatusView:
        checks = await self.readiness.run()
        if any(check.status != "UP" for check in checks):
            raise ServiceNotReadyError()

        async with self.database.session() as session:
            repository = NodeRepository(session)
            profile = await repository.get_profile(self.settings.node_code)
            if profile is None:
                raise ServiceNotReadyError("NODE_NOT_INITIALIZED")
            notices = await repository.active_notices()
            heartbeat = await repository.get_worker_heartbeat("outbox-worker")

        worker_status = "STARTING"
        last_seen_at = None
        if heartbeat is not None:
            last_seen_at = heartbeat.last_seen_at
            age = (datetime.now(UTC) - heartbeat.last_seen_at).total_seconds()
            worker_status = (
                "RUNNING" if age <= self.settings.worker_stale_after_seconds else "STALE"
            )

        overall = "OPERATIONAL" if worker_status == "RUNNING" else "DEGRADED"
        return SystemStatusView(
            status=overall,
            node_id=str(profile.id),
            node_code=profile.node_code,
            display_name=profile.display_name,
            environment=profile.environment,
            demo_data_loaded=profile.demo_data_loaded,
            release=self.settings.release,
            schema_revision=EXPECTED_SCHEMA_REVISION,
            checks=checks,
            worker_status=worker_status,
            worker_last_seen_at=last_seen_at,
            notices=tuple(
                NoticeView(
                    code=notice.code,
                    severity=notice.severity,
                    message_key=notice.message_key,
                    parameters=notice.parameters,
                    created_at=notice.created_at,
                )
                for notice in notices
            ),
        )
