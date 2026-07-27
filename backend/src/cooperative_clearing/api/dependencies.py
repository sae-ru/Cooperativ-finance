"""Explicit access to runtime adapters stored on the application."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.identity.application.authentication import AuthenticationService
from cooperative_clearing.modules.identity.application.service_clients import (
    ServiceClientService,
    ServicePrincipal,
)
from cooperative_clearing.modules.identity.domain.types import Principal
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError, ServiceNotReadyError
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
        if principal.break_glass_grants:
            await AuditRepository(session).record(
                action="BREAK_GLASS_ACCESS_USED",
                object_type="AuthSession",
                object_id=principal.session_id,
                actor_user_id=principal.user_id,
                outcome="SUCCESS",
                payload={
                    "method": request.method,
                    "path": request.url.path,
                    "grant_ids": [
                        str(grant.assignment_id) for grant in principal.break_glass_grants
                    ],
                },
            )
            await session.commit()
    request.state.principal = principal
    return principal


PrincipalDependency = Annotated[Principal, Depends(get_principal)]


async def get_service_principal(
    request: Request,
    settings: SettingsDependency,
    database: DatabaseDependency,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> ServicePrincipal:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise DomainError(
            code="SERVICE_AUTHENTICATION_REQUIRED",
            message_key="errors.identity.service_authentication_required",
            status_code=401,
        )
    if request.client is None:
        raise DomainError(
            code="SERVICE_NETWORK_DENIED",
            message_key="errors.identity.service_network_denied",
            status_code=403,
        )
    async with database.session() as session:
        try:
            principal = await ServiceClientService(settings).principal_for_access(
                session,
                access_token=credentials.credentials,
                source_ip=request.client.host,
            )
            await session.commit()
        except DomainError:
            await session.commit()
            raise
    request.state.service_principal = principal
    return principal


ServicePrincipalDependency = Annotated[ServicePrincipal, Depends(get_service_principal)]
