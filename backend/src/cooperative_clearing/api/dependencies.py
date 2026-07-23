"""Explicit access to runtime adapters stored on the application."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from cooperative_clearing.modules.identity.application.authentication import AuthenticationService
from cooperative_clearing.modules.identity.domain.types import Principal
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import ServiceNotReadyError
from cooperative_clearing.shared.infrastructure.database import Database


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_database(request: Request) -> Database:
    database = getattr(request.app.state, "database", None)
    if not isinstance(database, Database):
        raise ServiceNotReadyError()
    return database


SettingsDependency = Annotated[Settings, Depends(get_settings)]
DatabaseDependency = Annotated[Database, Depends(get_database)]

bearer = HTTPBearer(auto_error=False)


async def get_principal(
    request: Request,
    settings: SettingsDependency,
    database: DatabaseDependency,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        from cooperative_clearing.shared.domain.errors import DomainError

        raise DomainError(
            code="AUTHENTICATION_REQUIRED",
            message_key="errors.auth.authentication_required",
            status_code=401,
        )
    async with database.session() as session:
        principal = await AuthenticationService(settings).principal_for_access(
            session, credentials.credentials
        )
    request.state.principal = principal
    return principal


PrincipalDependency = Annotated[Principal, Depends(get_principal)]
