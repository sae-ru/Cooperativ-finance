"""Operational commands used by Compose jobs and local runbooks."""

import argparse
import asyncio
import json
import logging
import signal
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from cooperative_clearing.main import create_app
from cooperative_clearing.modules.clearing.application.demo import seed_demo_clearing
from cooperative_clearing.modules.crisis.application.demo import seed_demo_crisis
from cooperative_clearing.modules.exchange.application.demo import seed_demo_exchange
from cooperative_clearing.modules.federation.application.clearing_coordinator import (
    recover_pending_federated_cycles,
)
from cooperative_clearing.modules.federation.application.clearing_demo import (
    seed_demo_inter_node_clearing,
)
from cooperative_clearing.modules.federation.application.demo import seed_demo_federation
from cooperative_clearing.modules.federation.application.discovery_demo import (
    seed_demo_discovery,
)
from cooperative_clearing.modules.federation.application.inter_node_clearing import (
    expire_stale_federated_prepares,
)
from cooperative_clearing.modules.federation.application.peer_reservations import (
    expire_stale_reservations,
)
from cooperative_clearing.modules.identity.application.bootstrap import (
    bootstrap_identity as bootstrap_identity_store,
)
from cooperative_clearing.modules.identity.application.bootstrap import (
    seed_demo_identity,
)
from cooperative_clearing.modules.inventory.application.demo import (
    seed_demo_catalog,
    seed_demo_inventory,
)
from cooperative_clearing.modules.journal.application.outbox import dispatch_outbox_batch
from cooperative_clearing.modules.journal.application.service import (
    IntegrityReport,
    initialize_node_key,
    verify_journal,
)
from cooperative_clearing.modules.node.infrastructure.repository import NodeRepository
from cooperative_clearing.modules.operations.application.status import (
    GetOperationalSnapshot,
    snapshot_payload,
)
from cooperative_clearing.modules.responsibility.application.demo import (
    seed_demo_responsibility,
)
from cooperative_clearing.modules.rights.application.demo import seed_demo_rights
from cooperative_clearing.modules.risk.application.antifraud_demo import seed_demo_antifraud
from cooperative_clearing.modules.risk.application.demo import seed_demo_risk
from cooperative_clearing.modules.solidarity.application.demo import seed_demo_solidarity
from cooperative_clearing.modules.trust.application.demo import seed_demo_trust
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.logging import configure_logging, event_fields
from cooperative_clearing.shared.infrastructure.database import Database

logger = logging.getLogger(__name__)


async def initialize_node(settings: Settings) -> None:
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            await NodeRepository(session).initialize_profile(settings)
            await initialize_node_key(session, settings)
            await session.commit()
    finally:
        await database.dispose()


async def initialize_identity(settings: Settings) -> None:
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            await bootstrap_identity_store(session, settings)
            await session.commit()
    finally:
        await database.dispose()


async def seed_demo(settings: Settings) -> None:
    if not settings.demo_data_enabled:
        raise RuntimeError("demo data is not enabled for this environment")
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            repository = NodeRepository(session)
            await repository.initialize_profile(settings)
            await initialize_node_key(session, settings)
            await repository.seed_demo(settings)
            await bootstrap_identity_store(session, settings)
            await seed_demo_identity(session, settings)
            catalog = await seed_demo_catalog(session, settings)
            custody_a_id, custody_b_id = await seed_demo_responsibility(
                session,
                settings,
                warehouse_a_id=catalog.warehouse_a_id,
                warehouse_b_id=catalog.warehouse_b_id,
            )
            await seed_demo_inventory(
                session,
                settings,
                catalog=catalog,
                custody_a_id=custody_a_id,
                custody_b_id=custody_b_id,
            )
            await seed_demo_rights(
                session,
                settings,
                catalog=catalog,
            )
            await seed_demo_exchange(
                session,
                settings,
                catalog=catalog,
            )
            await seed_demo_risk(session, settings)
            await seed_demo_clearing(session, settings)
            await seed_demo_trust(session, settings)
            await seed_demo_solidarity(session, settings)
            await seed_demo_crisis(session, settings)
            await seed_demo_federation(session, settings)
            await seed_demo_inter_node_clearing(session, settings)
            await seed_demo_discovery(session, settings)
            await seed_demo_antifraud(session, settings)
            await session.commit()
    finally:
        await database.dispose()


async def run_worker(settings: Settings) -> None:
    database = Database.from_settings(settings)
    instance_id = uuid4()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signal_name, stop.set)

    logger.info(
        "worker_started",
        extra=event_fields(worker="outbox-worker", instance_id=str(instance_id)),
    )
    try:
        while not stop.is_set():
            try:
                async with database.session() as session:
                    expired = await expire_stale_reservations(
                        session,
                        settings=settings,
                        batch_size=settings.outbox_batch_size,
                    )
                    expired_clearing = await expire_stale_federated_prepares(
                        session,
                        settings=settings,
                        batch_size=settings.outbox_batch_size,
                    )
                    recovered_clearing = await recover_pending_federated_cycles(
                        session,
                        settings=settings,
                        batch_size=settings.federated_recovery_batch_size,
                    )
                    dispatch = await dispatch_outbox_batch(
                        session,
                        instance_id=instance_id,
                        batch_size=settings.outbox_batch_size,
                        lease_seconds=settings.outbox_lease_seconds,
                        max_attempts=settings.outbox_max_attempts,
                    )
                    await NodeRepository(session).record_worker_heartbeat(
                        worker_name="outbox-worker",
                        instance_id=instance_id,
                        release=settings.release,
                    )
                    await session.commit()
                    if recovered_clearing.attempted_cycles:
                        logger.info(
                            "federated_clearing_recovery_sweep",
                            extra=event_fields(
                                worker="outbox-worker",
                                attempted_cycles=recovered_clearing.attempted_cycles,
                                reconciled_cycles=recovered_clearing.reconciled_cycles,
                                pending_cycles=recovered_clearing.pending_cycles,
                                result_code=(
                                    "RECOVERED"
                                    if recovered_clearing.pending_cycles == 0
                                    else "PENDING_APPLY"
                                ),
                            ),
                        )
                    if expired_clearing.released_cycles:
                        logger.warning(
                            "federated_clearing_prepares_expired",
                            extra=event_fields(
                                worker="outbox-worker",
                                released_cycles=expired_clearing.released_cycles,
                                result_code="PREPARE_EXPIRED",
                            ),
                        )
                    if expired.peer_reservations or expired.purchase_intents:
                        logger.info(
                            "federation_reservations_expired",
                            extra=event_fields(
                                worker="outbox-worker",
                                peer_reservations=expired.peer_reservations,
                                purchase_intents=expired.purchase_intents,
                            ),
                        )
                    if dispatch.quarantined:
                        logger.error(
                            "outbox_messages_quarantined",
                            extra=event_fields(
                                worker="outbox-worker",
                                quarantined=dispatch.quarantined,
                                result_code="OUTBOX_QUARANTINED",
                            ),
                        )
            except Exception:
                logger.exception(
                    "worker_heartbeat_failed",
                    extra=event_fields(worker="outbox-worker", result_code="DATABASE_ERROR"),
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.outbox_poll_seconds)
            except TimeoutError:
                continue
    finally:
        await database.dispose()
        logger.info("worker_stopped", extra=event_fields(worker="outbox-worker"))


async def worker_is_healthy(settings: Settings) -> bool:
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            heartbeat = await NodeRepository(session).get_worker_heartbeat("outbox-worker")
        if heartbeat is None:
            return False
        age = (datetime.now(UTC) - heartbeat.last_seen_at).total_seconds()
        return age <= settings.worker_stale_after_seconds
    finally:
        await database.dispose()


async def verify_local_journal(settings: Settings) -> IntegrityReport:
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            profile = await NodeRepository(session).get_profile(settings.node_code)
            if profile is None:
                raise RuntimeError(f"node profile {settings.node_code!r} is not initialized")
            return await verify_journal(session, profile.id)
    finally:
        await database.dispose()


async def get_operational_diagnostics(settings: Settings) -> dict[str, object]:
    database = Database.from_settings(settings)
    try:
        snapshot = await GetOperationalSnapshot(database).execute()
        return snapshot_payload(snapshot)
    finally:
        await database.dispose()


def _print_journal_report(report: IntegrityReport) -> None:
    print(
        json.dumps(
            {
                "ok": report.ok,
                "node_id": str(report.node_id),
                "checked_events": report.checked_events,
                "last_sequence": report.last_sequence,
                "last_event_hash": report.last_event_hash,
                "failures": [
                    {
                        "sequence": failure.sequence,
                        "event_id": str(failure.event_id),
                        "code": failure.code,
                    }
                    for failure in report.failures
                ],
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
    )


def export_openapi(output: Path, settings: Settings) -> None:
    app = create_app(settings, manage_runtime=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coopctl")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-node")
    commands.add_parser("bootstrap-identity")
    commands.add_parser("seed-demo")
    commands.add_parser("worker")
    commands.add_parser("worker-health")
    commands.add_parser("verify-journal")
    commands.add_parser("diagnostics")
    export = commands.add_parser("export-openapi")
    export.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    settings = Settings(service_name="worker" if args.command.startswith("worker") else "api")
    configure_logging(
        level=settings.log_level,
        service=settings.service_name,
        release=settings.release,
        node_id=settings.node_code,
    )
    if args.command == "init-node":
        asyncio.run(initialize_node(settings))
    elif args.command == "bootstrap-identity":
        asyncio.run(initialize_identity(settings))
    elif args.command == "seed-demo":
        asyncio.run(seed_demo(settings))
    elif args.command == "worker":
        asyncio.run(run_worker(settings))
    elif args.command == "worker-health":
        raise SystemExit(0 if asyncio.run(worker_is_healthy(settings)) else 1)
    elif args.command == "verify-journal":
        report = asyncio.run(verify_local_journal(settings))
        _print_journal_report(report)
        raise SystemExit(0 if report.ok else 1)
    elif args.command == "diagnostics":
        print(
            json.dumps(
                asyncio.run(get_operational_diagnostics(settings)),
                ensure_ascii=True,
                separators=(",", ":"),
            )
        )
    elif args.command == "export-openapi":
        export_openapi(args.output, settings)
