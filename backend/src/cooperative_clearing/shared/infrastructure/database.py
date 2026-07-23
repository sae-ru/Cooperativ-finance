"""SQLAlchemy engine and explicit transaction/session boundaries."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.secrets import read_text_secret


class Database:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database":
        password = read_text_secret(settings.database_password_file)
        url = URL.create(
            drivername="postgresql+psycopg",
            username=settings.database_user,
            password=password,
            host=settings.database_host,
            port=settings.database_port,
            database=settings.database_name,
        )
        engine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_recycle=1800,
            connect_args={
                "connect_timeout": settings.database_connect_timeout_seconds,
                "application_name": f"cooperative-clearing-{settings.service_name}",
            },
        )
        return cls(engine)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def schema_revision(self) -> str | None:
        async with self.engine.connect() as connection:
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
            value = result.scalar_one_or_none()
            return str(value) if value is not None else None

    async def dispose(self) -> None:
        await self.engine.dispose()
