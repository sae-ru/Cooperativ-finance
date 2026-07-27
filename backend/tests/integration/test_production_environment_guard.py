from uuid import uuid4

import pytest
from sqlalchemy import update

from cooperative_clearing.modules.node.infrastructure.models import NodeProfile
from cooperative_clearing.modules.node.infrastructure.repository import NodeRepository
from cooperative_clearing.shared.core.config import Environment, Settings
from cooperative_clearing.shared.infrastructure.database import Database


def unique_node_code(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


@pytest.mark.integration
async def test_node_profile_rejects_in_place_hardened_transition() -> None:
    settings = Settings(
        service_name="environment-guard-test",
        node_code=unique_node_code("guard"),
        demo_data_enabled=False,
    )
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            await NodeRepository(session).initialize_profile(settings)
            await session.commit()

        production = settings.model_copy(
            update={
                "environment": Environment.PRODUCTION,
                "demo_data_enabled": False,
            }
        )
        async with database.session() as session:
            with pytest.raises(
                RuntimeError,
                match="in-place transition to or from a hardened environment",
            ):
                await NodeRepository(session).initialize_profile(production)
            await session.rollback()
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_demo_marker_blocks_hardened_startup() -> None:
    settings = Settings(
        service_name="demo-guard-test",
        node_code=unique_node_code("demo-guard"),
        demo_data_enabled=False,
    )
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            profile_id = await NodeRepository(session).initialize_profile(settings)
            await session.execute(
                update(NodeProfile)
                .where(NodeProfile.id == profile_id)
                .values(demo_data_loaded=True)
            )
            await session.commit()

        pilot = settings.model_copy(
            update={
                "environment": Environment.PILOT,
                "demo_data_enabled": False,
            }
        )
        async with database.session() as session:
            with pytest.raises(RuntimeError, match="demo data is present"):
                await NodeRepository(session).initialize_profile(pilot)
            await session.rollback()
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_fresh_production_profile_can_restart_in_same_environment() -> None:
    base = Settings(
        service_name="production-restart-test",
        node_code=unique_node_code("production"),
        demo_data_enabled=False,
    )
    production = base.model_copy(
        update={
            "environment": Environment.PRODUCTION,
            "demo_data_enabled": False,
        }
    )
    database = Database.from_settings(production)
    try:
        async with database.session() as session:
            first_id = await NodeRepository(session).initialize_profile(production)
            await session.commit()
        async with database.session() as session:
            second_id = await NodeRepository(session).initialize_profile(production)
            await session.commit()
    finally:
        await database.dispose()

    assert first_id == second_id
