"""PII-free operational snapshot assembled from local authoritative state."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text

from cooperative_clearing.shared.infrastructure.database import Database


@dataclass(frozen=True, slots=True)
class OperationalSnapshot:
    generated_at: datetime
    schema_revision: str
    signed_events: int
    outbox_pending: int
    outbox_quarantined: int
    active_sessions: int
    open_trust_cases: int
    submitted_appeals: int
    open_sync_conflicts: int
    open_node_incidents: int
    pending_key_rotations: int
    open_offline_epochs: int
    issued_federation_forms: int
    active_federated_prepares: int
    pending_federated_applies: int
    expired_federated_prepares: int
    active_crisis_mandates: int
    issued_crisis_forms: int


SNAPSHOT_QUERY = text(
    """
    SELECT
      (SELECT version_num FROM alembic_version LIMIT 1) AS schema_revision,
      (SELECT count(*) FROM journal.signed_events) AS signed_events,
      (SELECT count(*) FROM journal.outbox_messages WHERE status = 'PENDING') AS outbox_pending,
      (SELECT count(*) FROM journal.outbox_messages WHERE status = 'QUARANTINED')
        AS outbox_quarantined,
      (SELECT count(*) FROM identity.auth_sessions
        WHERE status = 'ACTIVE' AND refresh_expires_at > now()) AS active_sessions,
      (SELECT count(*) FROM trust.cases
        WHERE status NOT IN ('DECIDED','CLOSED')) AS open_trust_cases,
      (SELECT count(*) FROM trust.appeals WHERE status = 'SUBMITTED') AS submitted_appeals,
      (SELECT count(*) FROM federation.sync_conflicts
        WHERE status IN ('OPEN','UNDER_REVIEW','APPEALED')) AS open_sync_conflicts,
      (SELECT count(*) FROM federation.node_security_incidents
        WHERE status IN ('OPEN','CONTAINED','APPEALED')) AS open_node_incidents,
      (SELECT count(*) FROM federation.node_key_rotation_requests
        WHERE status = 'PENDING') AS pending_key_rotations,
      (SELECT count(*) FROM federation.offline_epochs WHERE status = 'OPEN') AS open_offline_epochs,
      (SELECT count(*) FROM federation.paper_forms WHERE status = 'ISSUED')
        AS issued_federation_forms,
      (SELECT count(*) FROM federation.federated_clearing_cycles
        WHERE status IN ('PREPARING_NODES','PREPARED','PROPOSED','VERIFYING'))
        AS active_federated_prepares,
      (SELECT count(*) FROM federation.federated_clearing_cycles
        WHERE status = 'COMMITTED_PENDING_APPLY') AS pending_federated_applies,
      (SELECT count(*) FROM federation.federated_clearing_cycles
        WHERE status = 'PREPARE_EXPIRED') AS expired_federated_prepares,
      (SELECT count(*) FROM solidarity.crisis_mandates WHERE status = 'ACTIVE')
        AS active_crisis_mandates,
      (SELECT count(*) FROM solidarity.crisis_paper_forms WHERE status = 'ISSUED')
        AS issued_crisis_forms
    """
)


class GetOperationalSnapshot:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def execute(self) -> OperationalSnapshot:
        async with self.database.session() as session:
            row = (await session.execute(SNAPSHOT_QUERY)).mappings().one()
        return OperationalSnapshot(
            generated_at=datetime.now(UTC),
            schema_revision=str(row["schema_revision"]),
            signed_events=int(row["signed_events"]),
            outbox_pending=int(row["outbox_pending"]),
            outbox_quarantined=int(row["outbox_quarantined"]),
            active_sessions=int(row["active_sessions"]),
            open_trust_cases=int(row["open_trust_cases"]),
            submitted_appeals=int(row["submitted_appeals"]),
            open_sync_conflicts=int(row["open_sync_conflicts"]),
            open_node_incidents=int(row["open_node_incidents"]),
            pending_key_rotations=int(row["pending_key_rotations"]),
            open_offline_epochs=int(row["open_offline_epochs"]),
            issued_federation_forms=int(row["issued_federation_forms"]),
            active_federated_prepares=int(row["active_federated_prepares"]),
            pending_federated_applies=int(row["pending_federated_applies"]),
            expired_federated_prepares=int(row["expired_federated_prepares"]),
            active_crisis_mandates=int(row["active_crisis_mandates"]),
            issued_crisis_forms=int(row["issued_crisis_forms"]),
        )


def snapshot_payload(snapshot: OperationalSnapshot) -> dict[str, object]:
    return {
        "generated_at": snapshot.generated_at.isoformat(),
        "schema_revision": snapshot.schema_revision,
        "signed_events": snapshot.signed_events,
        "outbox_pending": snapshot.outbox_pending,
        "outbox_quarantined": snapshot.outbox_quarantined,
        "active_sessions": snapshot.active_sessions,
        "open_trust_cases": snapshot.open_trust_cases,
        "submitted_appeals": snapshot.submitted_appeals,
        "open_sync_conflicts": snapshot.open_sync_conflicts,
        "open_node_incidents": snapshot.open_node_incidents,
        "pending_key_rotations": snapshot.pending_key_rotations,
        "open_offline_epochs": snapshot.open_offline_epochs,
        "issued_federation_forms": snapshot.issued_federation_forms,
        "active_federated_prepares": snapshot.active_federated_prepares,
        "pending_federated_applies": snapshot.pending_federated_applies,
        "expired_federated_prepares": snapshot.expired_federated_prepares,
        "active_crisis_mandates": snapshot.active_crisis_mandates,
        "issued_crisis_forms": snapshot.issued_crisis_forms,
    }


def snapshot_metrics(snapshot: OperationalSnapshot) -> str:
    gauges = {
        "signed_events": snapshot.signed_events,
        "outbox_pending": snapshot.outbox_pending,
        "outbox_quarantined": snapshot.outbox_quarantined,
        "active_sessions": snapshot.active_sessions,
        "open_trust_cases": snapshot.open_trust_cases,
        "submitted_appeals": snapshot.submitted_appeals,
        "open_sync_conflicts": snapshot.open_sync_conflicts,
        "open_node_incidents": snapshot.open_node_incidents,
        "pending_key_rotations": snapshot.pending_key_rotations,
        "open_offline_epochs": snapshot.open_offline_epochs,
        "issued_federation_forms": snapshot.issued_federation_forms,
        "active_federated_prepares": snapshot.active_federated_prepares,
        "pending_federated_applies": snapshot.pending_federated_applies,
        "expired_federated_prepares": snapshot.expired_federated_prepares,
        "active_crisis_mandates": snapshot.active_crisis_mandates,
        "issued_crisis_forms": snapshot.issued_crisis_forms,
    }
    lines = [
        "# HELP coop_operational_records Current PII-free operational record counts.",
        "# TYPE coop_operational_records gauge",
    ]
    lines.extend(
        f'coop_operational_records{{kind="{name}"}} {value}'
        for name, value in sorted(gauges.items())
    )
    return "\n".join(lines) + "\n"
