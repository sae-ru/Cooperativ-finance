"""Administration and runtime endpoints for external software integrations."""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from cooperative_clearing.api.auth import _request_uuid
from cooperative_clearing.api.clearing import AccountingExportResponse, ObjectEnvelope
from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    ServicePrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.api.discovery import SearchRequest, SearchResponse, _candidate_view
from cooperative_clearing.api.service_client_schemas import (
    ServiceClientChangeRequest,
    ServiceClientCollection,
    ServiceClientCommandEnvelope,
    ServiceClientCommandResponse,
    ServiceClientDecisionEnvelope,
    ServiceClientDecisionRequest,
    ServiceClientDecisionResponse,
    ServiceClientProtectiveRequest,
    ServiceClientRequestCollection,
    ServiceClientRequestResponse,
    ServiceClientResponse,
    ServiceContextEnvelope,
    ServiceContextResponse,
    ServiceTokenEnvelope,
    ServiceTokenRequest,
    ServiceTokenResponse,
)
from cooperative_clearing.modules.clearing.infrastructure.models import (
    ClearingAccountingExport,
    ClearingCycle,
)
from cooperative_clearing.modules.federation.application.discovery import DiscoveryService
from cooperative_clearing.modules.federation.domain.discovery import SearchMode
from cooperative_clearing.modules.identity.application.security import require_step_up
from cooperative_clearing.modules.identity.application.service_clients import (
    ServiceClientService,
    normalize_service_client_config,
    service_client_effective_status,
)
from cooperative_clearing.modules.identity.domain.types import RoleCode, ServiceScope
from cooperative_clearing.modules.identity.infrastructure.models import (
    ServiceClient,
    ServiceClientRequest,
)
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

admin_router = APIRouter(prefix="/api/v1/admin", tags=["service-clients"])
auth_router = APIRouter(prefix="/api/v1/service-auth", tags=["service-auth"])
service_router = APIRouter(prefix="/api/v1/service", tags=["service-integration"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]
READ_ROLES = {RoleCode.COOPERATIVE_ADMIN, RoleCode.SECURITY_ADMIN, RoleCode.AUDITOR}


def _scope(principal: object) -> set[UUID] | None:
    from cooperative_clearing.modules.identity.domain.types import Principal

    if not isinstance(principal, Principal) or principal.must_change_password:
        raise DomainError(
            code="AUTHORIZATION_DENIED",
            message_key="errors.auth.authorization_denied",
            status_code=403,
        )
    if any(
        grant.role in {RoleCode.SECURITY_ADMIN, RoleCode.AUDITOR} and grant.cooperative_id is None
        for grant in principal.roles
    ):
        return None
    scopes = {
        grant.cooperative_id
        for grant in principal.roles
        if grant.role in READ_ROLES and grant.cooperative_id is not None
    }
    if not scopes:
        raise DomainError(
            code="AUTHORIZATION_DENIED",
            message_key="errors.auth.authorization_denied",
            status_code=403,
        )
    return scopes


def _client_view(client: ServiceClient) -> ServiceClientResponse:
    return ServiceClientResponse(
        id=client.id,
        client_code=client.client_code,
        owner_cooperative_id=client.owner_cooperative_id,
        display_name=client.display_name,
        technical_contact_name=client.technical_contact_name,
        technical_contact_email=client.technical_contact_email,
        scopes=client.scopes,
        network_allowlist=client.network_allowlist,
        rate_limit_per_minute=client.rate_limit_per_minute,
        status=client.status,
        effective_status=service_client_effective_status(client),
        expires_at=client.expires_at,
        registered_by_user_id=client.registered_by_user_id,
        approved_by_user_id=client.approved_by_user_id,
        created_at=client.created_at,
        updated_at=client.updated_at,
        suspended_at=client.suspended_at,
        revoked_at=client.revoked_at,
        version=client.version,
    )


@admin_router.get("/service-clients", response_model=ServiceClientCollection)
async def list_service_clients(
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> ServiceClientCollection:
    scopes = _scope(principal)
    statement = select(ServiceClient).order_by(ServiceClient.created_at.desc(), ServiceClient.id)
    if scopes is not None:
        statement = statement.where(ServiceClient.owner_cooperative_id.in_(scopes))
    async with database.session() as session:
        rows = list((await session.execute(statement)).scalars())
    return ServiceClientCollection(
        data=[_client_view(item) for item in rows], request_id=get_request_id()
    )


@admin_router.get("/service-client-requests", response_model=ServiceClientRequestCollection)
async def list_service_client_requests(
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> ServiceClientRequestCollection:
    scopes = _scope(principal)
    statement = select(ServiceClientRequest).order_by(
        ServiceClientRequest.created_at.desc(), ServiceClientRequest.id
    )
    if scopes is not None:
        statement = statement.where(ServiceClientRequest.owner_cooperative_id.in_(scopes))
    async with database.session() as session:
        rows = list((await session.execute(statement)).scalars())
    return ServiceClientRequestCollection(
        data=[ServiceClientRequestResponse.model_validate(item) for item in rows],
        request_id=get_request_id(),
    )


@admin_router.post(
    "/service-client-requests",
    response_model=ServiceClientCommandEnvelope,
    status_code=201,
)
async def request_service_client_change(
    payload: ServiceClientChangeRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> ServiceClientCommandEnvelope:
    config = None
    if payload.config is not None:
        config = normalize_service_client_config(
            display_name=payload.config.display_name,
            technical_contact_name=payload.config.technical_contact_name,
            technical_contact_email=payload.config.technical_contact_email,
            scopes=[item.value for item in payload.config.scopes],
            network_allowlist=payload.config.network_allowlist,
            rate_limit_per_minute=payload.config.rate_limit_per_minute,
            expires_at=payload.config.expires_at,
        )
    async with database.session() as session:
        try:
            result = await ServiceClientService(settings).request_change(
                session,
                principal=principal,
                owner_cooperative_id=payload.owner_cooperative_id,
                operation=payload.operation,
                service_client_id=payload.service_client_id,
                config=config,
                expected_client_version=payload.expected_client_version,
                reason_code=payload.reason_code,
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise DomainError(
                code="SERVICE_CLIENT_CONFLICT",
                message_key="errors.identity.service_client_conflict",
                status_code=409,
            ) from exc
    return ServiceClientCommandEnvelope(
        data=ServiceClientCommandResponse(**asdict(result)), request_id=get_request_id()
    )


@admin_router.post(
    "/service-client-requests/{change_request_id}/decision",
    response_model=ServiceClientDecisionEnvelope,
    status_code=201,
)
async def decide_service_client_change(
    change_request_id: UUID,
    payload: ServiceClientDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> ServiceClientDecisionEnvelope:
    async with database.session() as session:
        try:
            await require_step_up(
                session,
                principal,
                operation="SERVICE_CLIENT_CHANGE_DECISION",
                request_id=_request_uuid(),
            )
            result = await ServiceClientService(settings).decide_request(
                session,
                principal=principal,
                change_request_id=change_request_id,
                approve=payload.approve,
                reason_code=payload.reason_code,
                expected_version=payload.expected_version,
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise DomainError(
                code="SERVICE_CLIENT_CONFLICT",
                message_key="errors.identity.service_client_conflict",
                status_code=409,
            ) from exc
    return ServiceClientDecisionEnvelope(
        data=ServiceClientDecisionResponse(**asdict(result)), request_id=get_request_id()
    )


async def _protect(
    *,
    service_client_id: UUID,
    payload: ServiceClientProtectiveRequest,
    idempotency_key: str,
    principal: object,
    database: DatabaseDependency,
    settings: SettingsDependency,
    revoke: bool,
) -> ServiceClientCommandEnvelope:
    from cooperative_clearing.modules.identity.domain.types import Principal

    if not isinstance(principal, Principal):
        raise DomainError(
            code="AUTHORIZATION_DENIED",
            message_key="errors.auth.authorization_denied",
            status_code=403,
        )
    async with database.session() as session:
        await require_step_up(
            session,
            principal,
            operation="SERVICE_CLIENT_REVOKE" if revoke else "SERVICE_CLIENT_SUSPEND",
            request_id=_request_uuid(),
        )
        service = ServiceClientService(settings)
        command = service.revoke if revoke else service.suspend
        result = await command(
            session,
            principal=principal,
            service_client_id=service_client_id,
            expected_version=payload.expected_version,
            reason_code=payload.reason_code,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )
        await session.commit()
    return ServiceClientCommandEnvelope(
        data=ServiceClientCommandResponse(**asdict(result)), request_id=get_request_id()
    )


@admin_router.post(
    "/service-clients/{service_client_id}/suspend",
    response_model=ServiceClientCommandEnvelope,
    status_code=201,
)
async def suspend_service_client(
    service_client_id: UUID,
    payload: ServiceClientProtectiveRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> ServiceClientCommandEnvelope:
    return await _protect(
        service_client_id=service_client_id,
        payload=payload,
        idempotency_key=idempotency_key,
        principal=principal,
        database=database,
        settings=settings,
        revoke=False,
    )


@admin_router.post(
    "/service-clients/{service_client_id}/revoke",
    response_model=ServiceClientCommandEnvelope,
    status_code=201,
)
async def revoke_service_client(
    service_client_id: UUID,
    payload: ServiceClientProtectiveRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> ServiceClientCommandEnvelope:
    return await _protect(
        service_client_id=service_client_id,
        payload=payload,
        idempotency_key=idempotency_key,
        principal=principal,
        database=database,
        settings=settings,
        revoke=True,
    )


@auth_router.post("/token", response_model=ServiceTokenEnvelope)
async def issue_service_token(
    payload: ServiceTokenRequest,
    request: Request,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> ServiceTokenEnvelope:
    if request.client is None:
        raise DomainError(
            code="SERVICE_NETWORK_DENIED",
            message_key="errors.identity.service_network_denied",
            status_code=403,
        )
    async with database.session() as session:
        try:
            result = await ServiceClientService(settings).issue_access_token(
                session,
                client_code=payload.client_id,
                client_secret=payload.client_secret.get_secret_value(),
                source_ip=request.client.host,
                request_id=_request_uuid(),
            )
            await session.commit()
        except DomainError:
            await session.commit()
            raise
    return ServiceTokenEnvelope(
        data=ServiceTokenResponse(
            access_token=result.access_token,
            access_expires_at=result.access_expires_at,
            service_client_id=result.service_client_id,
            client_id=result.client_code,
            owner_cooperative_id=result.owner_cooperative_id,
            scopes=result.scopes,
        ),
        request_id=get_request_id(),
    )


@service_router.get("/context", response_model=ServiceContextEnvelope)
async def service_context(
    principal: ServicePrincipalDependency,
) -> ServiceContextEnvelope:
    return ServiceContextEnvelope(
        data=ServiceContextResponse(
            service_client_id=principal.service_client_id,
            client_id=principal.client_code,
            owner_cooperative_id=principal.owner_cooperative_id,
            scopes=principal.scopes,
            source_ip=principal.source_ip,
        ),
        request_id=get_request_id(),
    )


@service_router.post("/catalog/search", response_model=SearchResponse)
async def service_catalog_search(
    payload: SearchRequest,
    principal: ServicePrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> SearchResponse:
    principal.require_scope(ServiceScope.CATALOG_READ)
    if payload.mode is SearchMode.DIRECT:
        raise DomainError(
            code="SERVICE_DIRECT_FANOUT_DENIED",
            message_key="errors.identity.service_direct_fanout_denied",
            status_code=403,
        )
    async with database.session() as session:
        results = await DiscoveryService(settings).search(session, **payload.model_dump())
        await session.commit()
    return SearchResponse(
        data=[_candidate_view(candidate) for candidate in results],
        mode=payload.mode,
        peer_statuses=[],
        request_id=get_request_id(),
    )


@service_router.get(
    "/clearing/cycles/{cycle_id}/accounting-export",
    response_model=ObjectEnvelope[AccountingExportResponse],
)
async def service_accounting_export(
    cycle_id: UUID,
    principal: ServicePrincipalDependency,
    database: DatabaseDependency,
) -> ObjectEnvelope[AccountingExportResponse]:
    principal.require_scope(ServiceScope.CLEARING_ACCOUNTING_READ)
    async with database.session() as session:
        cycle = await session.get(ClearingCycle, cycle_id)
        if cycle is None or cycle.cooperative_id != principal.owner_cooperative_id:
            raise DomainError(
                code="ACCOUNTING_EXPORT_NOT_FOUND",
                message_key="errors.clearing.accounting_export_not_found",
                status_code=404,
            )
        item = (
            await session.execute(
                select(ClearingAccountingExport).where(
                    ClearingAccountingExport.cycle_id == cycle_id
                )
            )
        ).scalar_one_or_none()
    if item is None:
        raise DomainError(
            code="ACCOUNTING_EXPORT_NOT_FOUND",
            message_key="errors.clearing.accounting_export_not_found",
            status_code=404,
        )
    return ObjectEnvelope(data=item, request_id=get_request_id())
