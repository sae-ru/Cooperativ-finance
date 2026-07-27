"""Scoped machine identities with dual-control lifecycle and revocable access."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address, ip_network
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.models import IdempotencyRecord
from cooperative_clearing.modules.audit.infrastructure.repository import (
    AuditRepository,
    IdempotencyRepository,
    request_payload_hash,
)
from cooperative_clearing.modules.identity.domain.types import (
    Principal,
    RoleCode,
    RoleGrantSource,
    ServiceClientRequestOperation,
    ServiceClientRequestStatus,
    ServiceClientStatus,
    ServiceCredentialStatus,
    ServiceScope,
    ServiceTokenStatus,
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    ServiceClient,
    ServiceClientAccessToken,
    ServiceClientCredential,
    ServiceClientRateBucket,
    ServiceClientRequest,
)
from cooperative_clearing.modules.journal.application.service import (
    ActorClaim,
    SignedJournalService,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.security import new_token, token_hash, tokens_equal
from cooperative_clearing.shared.domain.errors import DomainError

MANAGER_ROLES = frozenset({RoleCode.COOPERATIVE_ADMIN, RoleCode.SECURITY_ADMIN})
SECURITY_ROLES = frozenset({RoleCode.SECURITY_ADMIN})
REQUEST_TTL = timedelta(hours=24)
CLIENT_MIN_TTL = timedelta(hours=1)
CLIENT_MAX_TTL = timedelta(days=365)
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SECRET_PATTERN = re.compile(r"^ccs_([0-9a-f]{32})_([A-Za-z0-9_-]{40,64})$")


@dataclass(frozen=True, slots=True)
class ServiceClientConfig:
    display_name: str
    technical_contact_name: str
    technical_contact_email: str
    scopes: tuple[str, ...]
    network_allowlist: tuple[str, ...]
    rate_limit_per_minute: int
    expires_at: datetime

    def as_json(self) -> dict[str, object]:
        return {
            "display_name": self.display_name,
            "technical_contact_name": self.technical_contact_name,
            "technical_contact_email": self.technical_contact_email,
            "scopes": list(self.scopes),
            "network_allowlist": list(self.network_allowlist),
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "expires_at": self.expires_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ServiceCommandResult:
    event_id: UUID
    object_id: UUID
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ServiceDecisionResult:
    event_id: UUID
    object_id: UUID
    service_client_id: UUID | None
    client_code: str | None
    credential_secret: str | None
    credential_expires_at: datetime | None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ServiceTokenResult:
    access_token: str
    access_expires_at: datetime
    service_client_id: UUID
    client_code: str
    owner_cooperative_id: UUID
    scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ServicePrincipal:
    service_client_id: UUID
    token_id: UUID
    client_code: str
    owner_cooperative_id: UUID
    scopes: tuple[str, ...]
    source_ip: str

    def require_scope(self, scope: ServiceScope) -> None:
        if scope.value not in self.scopes:
            raise _error("SERVICE_SCOPE_DENIED", 403)


def normalize_service_client_config(
    *,
    display_name: str,
    technical_contact_name: str,
    technical_contact_email: str,
    scopes: list[str] | tuple[str, ...],
    network_allowlist: list[str] | tuple[str, ...],
    rate_limit_per_minute: int,
    expires_at: datetime,
    now: datetime | None = None,
) -> ServiceClientConfig:
    current = now or datetime.now(UTC)
    normalized_name = " ".join(display_name.strip().split())
    contact_name = " ".join(technical_contact_name.strip().split())
    contact_email = technical_contact_email.strip().casefold()
    if not 2 <= len(normalized_name) <= 200:
        raise _error("SERVICE_CLIENT_NAME_INVALID", 422)
    if not 2 <= len(contact_name) <= 200:
        raise _error("SERVICE_CONTACT_NAME_INVALID", 422)
    if len(contact_email) > 254 or EMAIL_PATTERN.fullmatch(contact_email) is None:
        raise _error("SERVICE_CONTACT_EMAIL_INVALID", 422)
    allowed_scopes = {item.value for item in ServiceScope}
    normalized_scopes = tuple(sorted({item.strip() for item in scopes if item.strip()}))
    if not normalized_scopes or not set(normalized_scopes).issubset(allowed_scopes):
        raise _error("SERVICE_SCOPES_INVALID", 422)
    if len(network_allowlist) > 32:
        raise _error("SERVICE_NETWORK_ALLOWLIST_INVALID", 422)
    normalized_networks: list[str] = []
    try:
        for item in network_allowlist:
            network = ip_network(item.strip(), strict=False)
            if network.prefixlen == 0:
                raise ValueError("unbounded network")
            normalized_networks.append(network.with_prefixlen)
    except ValueError as exc:
        raise _error("SERVICE_NETWORK_ALLOWLIST_INVALID", 422) from exc
    normalized_allowlist = tuple(sorted(set(normalized_networks)))
    if not normalized_allowlist:
        raise _error("SERVICE_NETWORK_ALLOWLIST_INVALID", 422)
    if not 1 <= rate_limit_per_minute <= 6000:
        raise _error("SERVICE_RATE_LIMIT_INVALID", 422)
    normalized_expiry = expires_at.astimezone(UTC)
    remaining = normalized_expiry - current
    if remaining < CLIENT_MIN_TTL or remaining > CLIENT_MAX_TTL:
        raise _error("SERVICE_EXPIRY_INVALID", 422)
    return ServiceClientConfig(
        display_name=normalized_name,
        technical_contact_name=contact_name,
        technical_contact_email=contact_email,
        scopes=normalized_scopes,
        network_allowlist=normalized_allowlist,
        rate_limit_per_minute=rate_limit_per_minute,
        expires_at=normalized_expiry,
    )


def service_client_effective_status(client: ServiceClient, now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    if client.status == ServiceClientStatus.ACTIVE.value and client.expires_at <= current:
        return "EXPIRED"
    return client.status


class ServiceClientService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.journal = SignedJournalService(settings)

    async def request_change(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        owner_cooperative_id: UUID,
        operation: ServiceClientRequestOperation,
        service_client_id: UUID | None,
        config: ServiceClientConfig | None,
        expected_client_version: int | None,
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ServiceCommandResult:
        self._require_manager(principal, owner_cooperative_id)
        reason = _normalize_reason(reason_code)
        safe_payload = {
            "owner_cooperative_id": owner_cooperative_id,
            "operation": operation.value,
            "service_client_id": service_client_id,
            "config": config.as_json() if config else None,
            "expected_client_version": expected_client_version,
            "reason_code": reason,
        }
        record, replay = await self._begin(
            session, principal, "SERVICE_CLIENT_CHANGE_REQUEST", idempotency_key, safe_payload
        )
        if replay is not None:
            return replay
        cooperative = await session.get(Cooperative, owner_cooperative_id)
        if cooperative is None:
            raise _error("COOPERATIVE_NOT_FOUND", 404)
        if cooperative.status != "ACTIVE":
            raise _error("SERVICE_OWNER_COOPERATIVE_INACTIVE", 409)
        if operation is ServiceClientRequestOperation.CREATE:
            if (
                service_client_id is not None
                or config is None
                or expected_client_version is not None
            ):
                raise _error("SERVICE_REQUEST_INVALID", 422)
        else:
            if service_client_id is None or expected_client_version is None:
                raise _error("SERVICE_REQUEST_INVALID", 422)
            client = await session.get(ServiceClient, service_client_id, with_for_update=True)
            if client is None:
                raise _error("SERVICE_CLIENT_NOT_FOUND", 404)
            if client.owner_cooperative_id != owner_cooperative_id:
                raise _error("AUTHORIZATION_DENIED", 403, "errors.auth.authorization_denied")
            if client.version != expected_client_version:
                raise _version_conflict(client.version)
            if operation is ServiceClientRequestOperation.UPDATE and config is None:
                raise _error("SERVICE_REQUEST_INVALID", 422)
            if (
                operation
                in {ServiceClientRequestOperation.ROTATE, ServiceClientRequestOperation.REACTIVATE}
                and config is not None
            ):
                raise _error("SERVICE_REQUEST_INVALID", 422)
            if (
                operation
                in {ServiceClientRequestOperation.UPDATE, ServiceClientRequestOperation.ROTATE}
                and service_client_effective_status(client) != ServiceClientStatus.ACTIVE.value
            ):
                raise _error("SERVICE_CLIENT_NOT_ACTIVE", 409)
            if (
                operation is ServiceClientRequestOperation.REACTIVATE
                and client.status != ServiceClientStatus.SUSPENDED.value
            ):
                raise _error("SERVICE_CLIENT_NOT_SUSPENDED", 409)
        now = datetime.now(UTC)
        change_request = ServiceClientRequest(
            id=uuid4(),
            service_client_id=service_client_id,
            owner_cooperative_id=owner_cooperative_id,
            operation=operation.value,
            proposed_config=config.as_json() if config else None,
            expected_client_version=expected_client_version,
            reason_code=reason,
            status=ServiceClientRequestStatus.PENDING.value,
            requested_by_user_id=principal.user_id,
            expires_at=now + REQUEST_TTL,
        )
        session.add(change_request)
        event = await self.journal.append(
            session,
            event_type="identity.service_client_change_requested",
            aggregate_type="service_client_request",
            aggregate_id=change_request.id,
            aggregate_version=1,
            actor=self._actor(principal, owner_cooperative_id, MANAGER_ROLES),
            payload={
                "operation": operation.value,
                "owner_cooperative_id": str(owner_cooperative_id),
                "service_client_id": str(service_client_id) if service_client_id else None,
                "scopes": list(config.scopes) if config else None,
                "network_allowlist": list(config.network_allowlist) if config else None,
                "rate_limit_per_minute": config.rate_limit_per_minute if config else None,
                "client_expires_at": config.expires_at.isoformat() if config else None,
                "request_expires_at": change_request.expires_at.isoformat(),
            },
        )
        await AuditRepository(session).record(
            action="SERVICE_CLIENT_CHANGE_REQUESTED",
            object_type="ServiceClientRequest",
            object_id=change_request.id,
            cooperative_id=owner_cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            reason_code=reason,
            request_id=request_id,
            payload={
                "operation": operation.value,
                "service_client_id": str(service_client_id) if service_client_id else None,
                "signed_event_id": str(event.event_id),
            },
        )
        return self._complete(record, event.event_id, change_request.id)

    async def decide_request(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        change_request_id: UUID,
        approve: bool,
        reason_code: str,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ServiceDecisionResult:
        reason = _normalize_reason(reason_code)
        record = await IdempotencyRepository(session).begin(
            actor_user_id=principal.user_id,
            operation="SERVICE_CLIENT_CHANGE_DECISION",
            idempotency_key=idempotency_key,
            request_hash=request_payload_hash(
                {
                    "change_request_id": change_request_id,
                    "approve": approve,
                    "reason_code": reason,
                    "expected_version": expected_version,
                }
            ),
        )
        if record.status == "COMPLETED":
            stored = record.response_payload or {}
            return ServiceDecisionResult(
                event_id=UUID(str(stored["event_id"])),
                object_id=UUID(str(stored["object_id"])),
                service_client_id=UUID(str(stored["service_client_id"]))
                if stored.get("service_client_id")
                else None,
                client_code=str(stored["client_code"]) if stored.get("client_code") else None,
                credential_secret=None,
                credential_expires_at=datetime.fromisoformat(str(stored["credential_expires_at"]))
                if stored.get("credential_expires_at")
                else None,
                replayed=True,
            )
        change_request = await session.get(
            ServiceClientRequest, change_request_id, with_for_update=True
        )
        if change_request is None:
            raise _error("SERVICE_REQUEST_NOT_FOUND", 404)
        self._require_security(principal, change_request.owner_cooperative_id)
        if change_request.requested_by_user_id == principal.user_id:
            raise _error("SERVICE_INDEPENDENT_REVIEW_REQUIRED", 409)
        if change_request.version != expected_version:
            raise _version_conflict(change_request.version)
        if change_request.status != ServiceClientRequestStatus.PENDING.value:
            raise _error("SERVICE_REQUEST_NOT_PENDING", 409)
        now = datetime.now(UTC)
        if change_request.expires_at <= now:
            raise _error("SERVICE_REQUEST_EXPIRED", 409)
        client: ServiceClient | None = None
        credential_secret: str | None = None
        credential: ServiceClientCredential | None = None
        event_type = "identity.service_client_change_rejected"
        object_id = change_request.id
        if approve:
            client, credential_secret, credential, event_type = await self._apply_approved_request(
                session, change_request, principal, now
            )
            object_id = client.id
            change_request.status = ServiceClientRequestStatus.APPROVED.value
            change_request.service_client_id = client.id
            change_request.issued_credential_id = credential.id if credential else None
        else:
            change_request.status = ServiceClientRequestStatus.REJECTED.value
        change_request.decided_by_user_id = principal.user_id
        change_request.decision_reason_code = reason
        change_request.decided_at = now
        change_request.version += 1
        event = await self.journal.append(
            session,
            event_type=event_type,
            aggregate_type="service_client" if client else "service_client_request",
            aggregate_id=object_id,
            aggregate_version=client.version if client else change_request.version,
            actor=self._actor(principal, change_request.owner_cooperative_id, SECURITY_ROLES),
            payload={
                "request_id": str(change_request.id),
                "operation": change_request.operation,
                "approved": approve,
                "owner_cooperative_id": str(change_request.owner_cooperative_id),
                "service_client_id": str(client.id) if client else None,
                "client_code": client.client_code if client else None,
                "scopes": list(client.scopes) if client else None,
                "expires_at": client.expires_at.isoformat() if client else None,
                "credential_id": str(credential.id) if credential else None,
            },
        )
        await AuditRepository(session).record(
            action="SERVICE_CLIENT_CHANGE_APPROVED"
            if approve
            else "SERVICE_CLIENT_CHANGE_REJECTED",
            object_type="ServiceClient" if client else "ServiceClientRequest",
            object_id=object_id,
            cooperative_id=change_request.owner_cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            reason_code=reason,
            request_id=request_id,
            payload={
                "change_request_id": str(change_request.id),
                "operation": change_request.operation,
                "credential_issued": credential is not None,
                "signed_event_id": str(event.event_id),
            },
        )
        response_payload: dict[str, object] = {
            "event_id": str(event.event_id),
            "object_id": str(object_id),
            "service_client_id": str(client.id) if client else None,
            "client_code": client.client_code if client else None,
            "credential_expires_at": credential.expires_at.isoformat() if credential else None,
        }
        IdempotencyRepository.complete(
            record, response_status=201, response_payload=response_payload
        )
        return ServiceDecisionResult(
            event_id=event.event_id,
            object_id=object_id,
            service_client_id=client.id if client else None,
            client_code=client.client_code if client else None,
            credential_secret=credential_secret,
            credential_expires_at=credential.expires_at if credential else None,
        )

    async def suspend(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        service_client_id: UUID,
        expected_version: int,
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ServiceCommandResult:
        return await self._protective_transition(
            session,
            principal=principal,
            service_client_id=service_client_id,
            expected_version=expected_version,
            reason_code=reason_code,
            idempotency_key=idempotency_key,
            request_id=request_id,
            revoke=False,
        )

    async def revoke(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        service_client_id: UUID,
        expected_version: int,
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ServiceCommandResult:
        return await self._protective_transition(
            session,
            principal=principal,
            service_client_id=service_client_id,
            expected_version=expected_version,
            reason_code=reason_code,
            idempotency_key=idempotency_key,
            request_id=request_id,
            revoke=True,
        )

    async def issue_access_token(
        self,
        session: AsyncSession,
        *,
        client_code: str,
        client_secret: str,
        source_ip: str,
        request_id: UUID | None,
    ) -> ServiceTokenResult:
        now = datetime.now(UTC)
        normalized_code = client_code.strip().lower()
        client = (
            await session.execute(
                select(ServiceClient)
                .where(ServiceClient.client_code == normalized_code)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if client is None:
            raise _error("SERVICE_AUTHENTICATION_FAILED", 401)
        await self._consume_rate(session, client, now)
        await self._require_runtime_access(session, client, source_ip, now)
        match = SECRET_PATTERN.fullmatch(client_secret)
        credential: ServiceClientCredential | None = None
        if match is not None:
            credential = await session.get(ServiceClientCredential, UUID(hex=match.group(1)))
        if (
            credential is None
            or credential.service_client_id != client.id
            or credential.status != ServiceCredentialStatus.ACTIVE.value
            or credential.expires_at <= now
            or not tokens_equal(credential.secret_hash, token_hash(client_secret))
        ):
            await AuditRepository(session).record(
                action="SERVICE_CLIENT_AUTHENTICATION",
                object_type="ServiceClient",
                object_id=client.id,
                cooperative_id=client.owner_cooperative_id,
                outcome="FAILURE",
                request_id=request_id,
                payload={"source_ip": source_ip, "reason": "CREDENTIAL_REJECTED"},
            )
            raise _error("SERVICE_AUTHENTICATION_FAILED", 401)
        raw_token = new_token()
        expires_at = min(
            now + timedelta(minutes=self.settings.access_token_minutes), client.expires_at
        )
        access = ServiceClientAccessToken(
            id=uuid4(),
            service_client_id=client.id,
            credential_id=credential.id,
            access_token_hash=token_hash(raw_token),
            source_ip=str(ip_address(source_ip)),
            status=ServiceTokenStatus.ACTIVE.value,
            issued_at=now,
            expires_at=expires_at,
            last_seen_at=now,
        )
        session.add(access)
        await AuditRepository(session).record(
            action="SERVICE_CLIENT_TOKEN_ISSUED",
            object_type="ServiceClientAccessToken",
            object_id=access.id,
            cooperative_id=client.owner_cooperative_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={
                "service_client_id": str(client.id),
                "credential_id": str(credential.id),
                "source_ip": access.source_ip,
                "expires_at": expires_at.isoformat(),
            },
        )
        return ServiceTokenResult(
            access_token=raw_token,
            access_expires_at=expires_at,
            service_client_id=client.id,
            client_code=client.client_code,
            owner_cooperative_id=client.owner_cooperative_id,
            scopes=tuple(str(item) for item in client.scopes),
        )

    async def principal_for_access(
        self,
        session: AsyncSession,
        *,
        access_token: str,
        source_ip: str,
    ) -> ServicePrincipal:
        now = datetime.now(UTC)
        access = (
            await session.execute(
                select(ServiceClientAccessToken)
                .where(ServiceClientAccessToken.access_token_hash == token_hash(access_token))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if access is None or access.status != ServiceTokenStatus.ACTIVE.value:
            raise _error("SERVICE_AUTHENTICATION_FAILED", 401)
        if access.expires_at <= now:
            access.status = ServiceTokenStatus.EXPIRED.value
            raise _error("SERVICE_TOKEN_EXPIRED", 401)
        normalized_ip = str(ip_address(source_ip))
        if access.source_ip != normalized_ip:
            raise _error("SERVICE_NETWORK_DENIED", 403)
        client = await session.get(ServiceClient, access.service_client_id, with_for_update=True)
        if client is None:
            raise _error("SERVICE_AUTHENTICATION_FAILED", 401)
        await self._require_runtime_access(session, client, normalized_ip, now)
        credential = await session.get(ServiceClientCredential, access.credential_id)
        if credential is None or credential.status != ServiceCredentialStatus.ACTIVE.value:
            access.status = ServiceTokenStatus.REVOKED.value
            access.revoked_at = now
            raise _error("SERVICE_TOKEN_REVOKED", 401)
        await self._consume_rate(session, client, now)
        access.last_seen_at = now
        return ServicePrincipal(
            service_client_id=client.id,
            token_id=access.id,
            client_code=client.client_code,
            owner_cooperative_id=client.owner_cooperative_id,
            scopes=tuple(str(item) for item in client.scopes),
            source_ip=normalized_ip,
        )

    async def _apply_approved_request(
        self,
        session: AsyncSession,
        change_request: ServiceClientRequest,
        principal: Principal,
        now: datetime,
    ) -> tuple[ServiceClient, str | None, ServiceClientCredential | None, str]:
        operation = ServiceClientRequestOperation(change_request.operation)
        if operation is ServiceClientRequestOperation.CREATE:
            config = self._config_from_request(change_request, now)
            created_client = ServiceClient(
                id=uuid4(),
                client_code=f"svc_{uuid4().hex[:20]}",
                owner_cooperative_id=change_request.owner_cooperative_id,
                display_name=config.display_name,
                technical_contact_name=config.technical_contact_name,
                technical_contact_email=config.technical_contact_email,
                scopes=list(config.scopes),
                network_allowlist=list(config.network_allowlist),
                rate_limit_per_minute=config.rate_limit_per_minute,
                status=ServiceClientStatus.ACTIVE.value,
                expires_at=config.expires_at,
                registered_by_user_id=change_request.requested_by_user_id,
                approved_by_user_id=principal.user_id,
            )
            session.add(created_client)
            await session.flush()
            secret, credential = self._issue_credential(created_client, principal.user_id, now)
            session.add(credential)
            return created_client, secret, credential, "identity.service_client_registered"
        if change_request.service_client_id is None:
            raise _error("SERVICE_REQUEST_INVALID", 409)
        client = await session.get(
            ServiceClient, change_request.service_client_id, with_for_update=True
        )
        if client is None:
            raise _error("SERVICE_CLIENT_NOT_FOUND", 404)
        if client.version != change_request.expected_client_version:
            raise _version_conflict(client.version)
        if operation is ServiceClientRequestOperation.UPDATE:
            if service_client_effective_status(client, now) != ServiceClientStatus.ACTIVE.value:
                raise _error("SERVICE_CLIENT_NOT_ACTIVE", 409)
            config = self._config_from_request(change_request, now)
            client.display_name = config.display_name
            client.technical_contact_name = config.technical_contact_name
            client.technical_contact_email = config.technical_contact_email
            client.scopes = list(config.scopes)
            client.network_allowlist = list(config.network_allowlist)
            client.rate_limit_per_minute = config.rate_limit_per_minute
            client.expires_at = config.expires_at
            client.updated_at = now
            client.version += 1
            await self._revoke_tokens(session, client.id, now)
            return client, None, None, "identity.service_client_policy_updated"
        if operation is ServiceClientRequestOperation.ROTATE:
            if service_client_effective_status(client, now) != ServiceClientStatus.ACTIVE.value:
                raise _error("SERVICE_CLIENT_NOT_ACTIVE", 409)
            await session.execute(
                update(ServiceClientCredential)
                .where(
                    ServiceClientCredential.service_client_id == client.id,
                    ServiceClientCredential.status == ServiceCredentialStatus.ACTIVE.value,
                )
                .values(status=ServiceCredentialStatus.RETIRED.value, retired_at=now)
            )
            await self._revoke_tokens(session, client.id, now)
            secret, credential = self._issue_credential(client, principal.user_id, now)
            session.add(credential)
            client.updated_at = now
            client.version += 1
            return client, secret, credential, "identity.service_client_credential_rotated"
        if operation is ServiceClientRequestOperation.REACTIVATE:
            if client.status != ServiceClientStatus.SUSPENDED.value:
                raise _error("SERVICE_CLIENT_NOT_SUSPENDED", 409)
            if client.expires_at <= now:
                raise _error("SERVICE_CLIENT_EXPIRED", 409)
            client.status = ServiceClientStatus.ACTIVE.value
            client.suspended_at = None
            client.updated_at = now
            client.version += 1
            return client, None, None, "identity.service_client_reactivated"
        raise _error("SERVICE_REQUEST_INVALID", 409)

    async def _protective_transition(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        service_client_id: UUID,
        expected_version: int,
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
        revoke: bool,
    ) -> ServiceCommandResult:
        reason = _normalize_reason(reason_code)
        operation = "SERVICE_CLIENT_REVOKE" if revoke else "SERVICE_CLIENT_SUSPEND"
        record, replay = await self._begin(
            session,
            principal,
            operation,
            idempotency_key,
            {
                "service_client_id": service_client_id,
                "expected_version": expected_version,
                "reason_code": reason,
            },
        )
        if replay is not None:
            return replay
        client = await session.get(ServiceClient, service_client_id, with_for_update=True)
        if client is None:
            raise _error("SERVICE_CLIENT_NOT_FOUND", 404)
        self._require_security(principal, client.owner_cooperative_id)
        if client.version != expected_version:
            raise _version_conflict(client.version)
        if client.status == ServiceClientStatus.REVOKED.value:
            raise _error("SERVICE_CLIENT_REVOKED", 409)
        now = datetime.now(UTC)
        if revoke:
            client.status = ServiceClientStatus.REVOKED.value
            client.revoked_at = now
            await session.execute(
                update(ServiceClientCredential)
                .where(
                    ServiceClientCredential.service_client_id == client.id,
                    ServiceClientCredential.status.in_(
                        [
                            ServiceCredentialStatus.ACTIVE.value,
                            ServiceCredentialStatus.RETIRED.value,
                        ]
                    ),
                )
                .values(status=ServiceCredentialStatus.REVOKED.value, revoked_at=now)
            )
            event_type = "identity.service_client_revoked"
            action = "SERVICE_CLIENT_REVOKED"
        else:
            if client.status != ServiceClientStatus.ACTIVE.value:
                raise _error("SERVICE_CLIENT_NOT_ACTIVE", 409)
            client.status = ServiceClientStatus.SUSPENDED.value
            client.suspended_at = now
            event_type = "identity.service_client_suspended"
            action = "SERVICE_CLIENT_SUSPENDED"
        await self._revoke_tokens(session, client.id, now)
        client.updated_at = now
        client.version += 1
        event = await self.journal.append(
            session,
            event_type=event_type,
            aggregate_type="service_client",
            aggregate_id=client.id,
            aggregate_version=client.version,
            actor=self._actor(principal, client.owner_cooperative_id, SECURITY_ROLES),
            payload={
                "service_client_id": str(client.id),
                "client_code": client.client_code,
                "owner_cooperative_id": str(client.owner_cooperative_id),
                "reason_code": reason,
            },
        )
        await AuditRepository(session).record(
            action=action,
            object_type="ServiceClient",
            object_id=client.id,
            cooperative_id=client.owner_cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            reason_code=reason,
            request_id=request_id,
            payload={"signed_event_id": str(event.event_id)},
        )
        return self._complete(record, event.event_id, client.id)

    async def _require_runtime_access(
        self, session: AsyncSession, client: ServiceClient, source_ip: str, now: datetime
    ) -> None:
        if client.status != ServiceClientStatus.ACTIVE.value:
            raise _error("SERVICE_CLIENT_INACTIVE", 403)
        if client.expires_at <= now:
            raise _error("SERVICE_CLIENT_EXPIRED", 403)
        cooperative = await session.get(Cooperative, client.owner_cooperative_id)
        if cooperative is None or cooperative.status != "ACTIVE":
            raise _error("SERVICE_OWNER_COOPERATIVE_INACTIVE", 403)
        try:
            address = ip_address(source_ip)
            allowed = any(
                address in ip_network(item, strict=False) for item in client.network_allowlist
            )
        except ValueError as exc:
            raise _error("SERVICE_NETWORK_DENIED", 403) from exc
        if not allowed:
            raise _error("SERVICE_NETWORK_DENIED", 403)

    @staticmethod
    async def _consume_rate(session: AsyncSession, client: ServiceClient, now: datetime) -> None:
        window = now.replace(second=0, microsecond=0)
        statement = (
            pg_insert(ServiceClientRateBucket)
            .values(service_client_id=client.id, window_started_at=window, request_count=1)
            .on_conflict_do_update(
                index_elements=[
                    ServiceClientRateBucket.service_client_id,
                    ServiceClientRateBucket.window_started_at,
                ],
                set_={"request_count": ServiceClientRateBucket.request_count + 1},
                where=ServiceClientRateBucket.request_count < client.rate_limit_per_minute,
            )
            .returning(ServiceClientRateBucket.request_count)
        )
        if (await session.execute(statement)).scalar_one_or_none() is None:
            raise _error("SERVICE_RATE_LIMIT_EXCEEDED", 429)

    @staticmethod
    async def _revoke_tokens(session: AsyncSession, service_client_id: UUID, now: datetime) -> None:
        await session.execute(
            update(ServiceClientAccessToken)
            .where(
                ServiceClientAccessToken.service_client_id == service_client_id,
                ServiceClientAccessToken.status == ServiceTokenStatus.ACTIVE.value,
            )
            .values(status=ServiceTokenStatus.REVOKED.value, revoked_at=now)
        )

    @staticmethod
    def _issue_credential(
        client: ServiceClient, issuer_user_id: UUID, now: datetime
    ) -> tuple[str, ServiceClientCredential]:
        credential_id = uuid4()
        secret = f"ccs_{credential_id.hex}_{new_token()}"
        credential = ServiceClientCredential(
            id=credential_id,
            service_client_id=client.id,
            secret_hash=token_hash(secret),
            secret_prefix=secret[:24],
            status=ServiceCredentialStatus.ACTIVE.value,
            issued_by_user_id=issuer_user_id,
            created_at=now,
            expires_at=client.expires_at,
        )
        return secret, credential

    @staticmethod
    def _config_from_request(
        change_request: ServiceClientRequest, now: datetime
    ) -> ServiceClientConfig:
        raw = change_request.proposed_config
        if raw is None:
            raise _error("SERVICE_REQUEST_INVALID", 409)
        try:
            raw_scopes = raw["scopes"]
            raw_allowlist = raw["network_allowlist"]
            if not isinstance(raw_scopes, list) or not isinstance(raw_allowlist, list):
                raise TypeError("list expected")
            return normalize_service_client_config(
                display_name=str(raw["display_name"]),
                technical_contact_name=str(raw["technical_contact_name"]),
                technical_contact_email=str(raw["technical_contact_email"]),
                scopes=[str(item) for item in raw_scopes],
                network_allowlist=[str(item) for item in raw_allowlist],
                rate_limit_per_minute=int(str(raw["rate_limit_per_minute"])),
                expires_at=datetime.fromisoformat(str(raw["expires_at"])),
                now=now,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise _error("SERVICE_REQUEST_INVALID", 409) from exc

    @staticmethod
    async def _begin(
        session: AsyncSession,
        principal: Principal,
        operation: str,
        idempotency_key: str,
        payload: object,
    ) -> tuple[IdempotencyRecord, ServiceCommandResult | None]:
        record = await IdempotencyRepository(session).begin(
            actor_user_id=principal.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_payload_hash(payload),
        )
        if record.status == "COMPLETED":
            stored = record.response_payload or {}
            return record, ServiceCommandResult(
                event_id=UUID(str(stored["event_id"])),
                object_id=UUID(str(stored["object_id"])),
                replayed=True,
            )
        return record, None

    @staticmethod
    def _complete(
        record: IdempotencyRecord, event_id: UUID, object_id: UUID
    ) -> ServiceCommandResult:
        IdempotencyRepository.complete(
            record,
            response_status=201,
            response_payload={"event_id": str(event_id), "object_id": str(object_id)},
        )
        return ServiceCommandResult(event_id=event_id, object_id=object_id)

    @staticmethod
    def _require_manager(principal: Principal, cooperative_id: UUID) -> None:
        _require_permanent_role(principal, MANAGER_ROLES, cooperative_id)

    @staticmethod
    def _require_security(principal: Principal, cooperative_id: UUID) -> None:
        _require_permanent_role(principal, SECURITY_ROLES, cooperative_id)

    @staticmethod
    def _actor(
        principal: Principal,
        cooperative_id: UUID,
        roles: frozenset[RoleCode],
    ) -> ActorClaim:
        if principal.member_id is None:
            raise _error("PERSONAL_ACTOR_REQUIRED", 403)
        for grant in principal.roles:
            if (
                grant.source is RoleGrantSource.ASSIGNMENT
                and grant.role in roles
                and grant.cooperative_id in {None, cooperative_id}
            ):
                return ActorClaim(
                    person_id=principal.member_id,
                    organization_id=cooperative_id,
                    role_assignment_id=grant.assignment_id,
                )
        raise _error("PERMANENT_SERVICE_CLIENT_ROLE_REQUIRED", 403)


def _require_permanent_role(
    principal: Principal, roles: frozenset[RoleCode], cooperative_id: UUID
) -> None:
    if principal.must_change_password:
        raise _error("PASSWORD_CHANGE_REQUIRED", 403, "errors.auth.password_change_required")
    if not principal.has_permanent_role(set(roles), cooperative_id):
        raise _error("PERMANENT_SERVICE_CLIENT_ROLE_REQUIRED", 403)


def _normalize_reason(value: str) -> str:
    reason = value.strip().upper()
    if not 2 <= len(reason) <= 100 or re.fullmatch(r"[A-Z0-9_.-]+", reason) is None:
        raise _error("SERVICE_REASON_INVALID", 422)
    return reason


def _version_conflict(current_version: int) -> DomainError:
    return DomainError(
        code="VERSION_CONFLICT",
        message_key="errors.request.version_conflict",
        parameters={"current_version": current_version},
        status_code=409,
    )


def _error(code: str, status_code: int, message_key: str | None = None) -> DomainError:
    return DomainError(
        code=code,
        message_key=message_key or f"errors.identity.{code.lower()}",
        status_code=status_code,
    )
