"""Readiness probes limited to local mandatory dependencies."""

import asyncio
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.secrets import validate_node_signing_seed
from cooperative_clearing.shared.infrastructure.database import Database

EXPECTED_SCHEMA_REVISION = "0018_inter_node_clearing"


@dataclass(frozen=True, slots=True)
class ComponentCheck:
    name: str
    status: str
    code: str


class ReadinessProbe:
    def __init__(self, *, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    async def run(self) -> tuple[ComponentCheck, ...]:
        checks = await asyncio.gather(
            self._database_check(),
            self._blob_check(),
            self._key_check(),
        )
        return tuple(checks)

    async def _database_check(self) -> ComponentCheck:
        try:
            async with asyncio.timeout(self.settings.readiness_timeout_seconds):
                await self.database.ping()
                revision = await self.database.schema_revision()
        except Exception:
            return ComponentCheck("database", "DOWN", "DATABASE_UNAVAILABLE")
        if revision != EXPECTED_SCHEMA_REVISION:
            return ComponentCheck("database", "DOWN", "SCHEMA_REVISION_MISMATCH")
        return ComponentCheck("database", "UP", "OK")

    async def _blob_check(self) -> ComponentCheck:
        try:
            async with asyncio.timeout(self.settings.readiness_timeout_seconds):
                await asyncio.to_thread(_write_probe, self.settings.blob_root)
        except Exception:
            return ComponentCheck("blob_store", "DOWN", "BLOB_STORE_UNAVAILABLE")
        return ComponentCheck("blob_store", "UP", "OK")

    async def _key_check(self) -> ComponentCheck:
        try:
            async with asyncio.timeout(self.settings.readiness_timeout_seconds):
                await asyncio.to_thread(
                    validate_node_signing_seed,
                    self.settings.node_signing_seed_file,
                )
        except Exception:
            return ComponentCheck("node_key", "DOWN", "NODE_KEY_UNAVAILABLE")
        return ComponentCheck("node_key", "UP", "OK")


def _write_probe(root: Path) -> None:
    if not root.is_dir():
        raise RuntimeError("blob root is unavailable")
    descriptor, path = tempfile.mkstemp(prefix=".health-", dir=root)
    try:
        os.write(descriptor, b"ok")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
        Path(path).unlink(missing_ok=True)
