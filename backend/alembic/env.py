"""Alembic environment using the same secret-file settings as the runtime."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from cooperative_clearing.modules.audit.infrastructure import models as audit_models  # noqa: F401
from cooperative_clearing.modules.clearing.infrastructure import (
    models as clearing_models,  # noqa: F401
)
from cooperative_clearing.modules.crisis.infrastructure import models as crisis_models  # noqa: F401
from cooperative_clearing.modules.exchange.infrastructure import (
    models as exchange_models,  # noqa: F401
)
from cooperative_clearing.modules.federation.infrastructure import (
    clearing_models as federation_clearing_models,  # noqa: F401
)
from cooperative_clearing.modules.federation.infrastructure import (
    discovery_models as federation_discovery_models,  # noqa: F401
)
from cooperative_clearing.modules.federation.infrastructure import (
    models as federation_models,  # noqa: F401
)
from cooperative_clearing.modules.federation.infrastructure import (
    peer_models as federation_peer_models,  # noqa: F401
)
from cooperative_clearing.modules.federation.infrastructure import (
    reservation_models as federation_reservation_models,  # noqa: F401
)
from cooperative_clearing.modules.identity.infrastructure import (
    models as identity_models,  # noqa: F401
)
from cooperative_clearing.modules.inventory.infrastructure import (
    models as inventory_models,  # noqa: F401
)
from cooperative_clearing.modules.journal.infrastructure import (
    models as journal_models,  # noqa: F401
)
from cooperative_clearing.modules.node.infrastructure import models as node_models  # noqa: F401
from cooperative_clearing.modules.responsibility.infrastructure import (
    models as responsibility_models,  # noqa: F401
)
from cooperative_clearing.modules.rights.infrastructure import models as rights_models  # noqa: F401
from cooperative_clearing.modules.risk.infrastructure import models as risk_models  # noqa: F401
from cooperative_clearing.modules.solidarity.infrastructure import (
    models as solidarity_models,  # noqa: F401
)
from cooperative_clearing.modules.trust.infrastructure import (
    models as trust_models,  # noqa: F401
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database
from cooperative_clearing.shared.infrastructure.orm import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    database = Database.from_settings(Settings(service_name="migration"))
    try:
        async with database.engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await database.dispose()


def run_migrations_offline() -> None:
    raise RuntimeError("offline SQL generation is not enabled for secret-file configuration")


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
