"""Local authentication with revocable server-side sessions."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.identity.domain.types import (
    Principal,
    RoleCode,
    RoleGrant,
    RoleGrantSource,
    normalize_login,
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    AuthSession,
    BreakGlassGrant,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.security import (
    PasswordService,
    new_token,
    private_value_hash,
    token_hash,
    tokens_equal,
)
from cooperative_clearing.shared.domain.errors import DomainError


@dataclass(frozen=True, slots=True)
class IssuedSession:
    principal: Principal
    access_token: str
    refresh_token: str
    csrf_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


class AuthenticationService:
    def __init__(self, settings: Settings, passwords: PasswordService | None = None) -> None:
        self.settings = settings
        self.passwords = passwords or PasswordService()

    async def login(
        self,
        session: AsyncSession,
        *,
        login: str,
        password: str,
        client_ip: str | None,
        user_agent: str | None,
        request_id: UUID | None,
    ) -> IssuedSession:
        normalized = normalize_login(login)
        result = await session.execute(
            select(UserAccount).where(UserAccount.login == normalized).with_for_update()
        )
        user = result.scalar_one_or_none()
        now = datetime.now(UTC)
        valid = False
        if user is None:
            self.passwords.consume_dummy_verification(password)
        elif user.status == "ACTIVE" and (user.locked_until is None or user.locked_until <= now):
            valid = self.passwords.verify(user.password_hash, password)

        if not valid:
            if user is not None:
                user.failed_login_attempts += 1
                if user.failed_login_attempts >= self.settings.auth_max_failed_attempts:
                    user.locked_until = now + timedelta(seconds=self.settings.auth_lock_seconds)
                    user.failed_login_attempts = 0
                user.updated_at = now
                user.version += 1
            await AuditRepository(session).record(
                action="AUTH_LOGIN",
                object_type="UserAccount",
                object_id=user.id if user is not None else None,
                outcome="DENIED",
                reason_code="AUTHENTICATION_FAILED",
                request_id=request_id,
            )
            raise self._authentication_failed()

        assert user is not None
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = now
        user.updated_at = now
        user.version += 1
        issued = await self._issue(session, user, client_ip=client_ip, user_agent=user_agent)
        await AuditRepository(session).record(
            action="AUTH_LOGIN",
            object_type="UserAccount",
            object_id=user.id,
            actor_user_id=user.id,
            outcome="SUCCESS",
            request_id=request_id,
        )
        return issued

    async def principal_for_access(self, session: AsyncSession, access_token: str) -> Principal:
        now = datetime.now(UTC)
        result = await session.execute(
            select(AuthSession, UserAccount)
            .join(UserAccount, UserAccount.id == AuthSession.user_id)
            .where(
                AuthSession.access_token_hash == token_hash(access_token),
                AuthSession.status == "ACTIVE",
                AuthSession.access_expires_at > now,
                AuthSession.refresh_expires_at > now,
                UserAccount.status == "ACTIVE",
            )
        )
        row = result.one_or_none()
        if row is None:
            raise self._authentication_failed()
        auth_session, user = row
        return await self._principal(session, user, auth_session.id)

    async def refresh(
        self,
        session: AsyncSession,
        *,
        refresh_token: str,
        csrf_cookie: str,
        csrf_header: str,
        client_ip: str | None,
        user_agent: str | None,
        request_id: UUID | None,
    ) -> IssuedSession:
        if not tokens_equal(csrf_cookie, csrf_header):
            raise DomainError(
                code="CSRF_VALIDATION_FAILED",
                message_key="errors.auth.csrf_validation_failed",
                status_code=403,
            )
        now = datetime.now(UTC)
        result = await session.execute(
            select(AuthSession, UserAccount)
            .join(UserAccount, UserAccount.id == AuthSession.user_id)
            .where(AuthSession.refresh_token_hash == token_hash(refresh_token))
            .with_for_update()
        )
        row = result.one_or_none()
        if row is None:
            raise self._authentication_failed()
        auth_session, user = row
        if (
            auth_session.status != "ACTIVE"
            or auth_session.refresh_expires_at <= now
            or user.status != "ACTIVE"
            or not tokens_equal(auth_session.csrf_token_hash, token_hash(csrf_cookie))
        ):
            auth_session.status = "EXPIRED"
            raise self._authentication_failed()

        access_token = new_token()
        refresh_token_new = new_token()
        csrf_token = new_token()
        access_expires_at = now + timedelta(minutes=self.settings.access_token_minutes)
        auth_session.access_token_hash = token_hash(access_token)
        auth_session.refresh_token_hash = token_hash(refresh_token_new)
        auth_session.csrf_token_hash = token_hash(csrf_token)
        auth_session.access_expires_at = access_expires_at
        auth_session.refresh_expires_at = now + timedelta(hours=self.settings.refresh_session_hours)
        auth_session.client_ip_hash = private_value_hash(client_ip) if client_ip else None
        auth_session.user_agent_hash = private_value_hash(user_agent) if user_agent else None
        auth_session.last_seen_at = now
        auth_session.rotated_at = now
        principal = await self._principal(session, user, auth_session.id)
        await AuditRepository(session).record(
            action="AUTH_REFRESH",
            object_type="AuthSession",
            object_id=auth_session.id,
            actor_user_id=user.id,
            outcome="SUCCESS",
            request_id=request_id,
        )
        return IssuedSession(
            principal=principal,
            access_token=access_token,
            refresh_token=refresh_token_new,
            csrf_token=csrf_token,
            access_expires_at=access_expires_at,
            refresh_expires_at=auth_session.refresh_expires_at,
        )

    async def logout(
        self, session: AsyncSession, principal: Principal, request_id: UUID | None
    ) -> None:
        now = datetime.now(UTC)
        await session.execute(
            update(AuthSession)
            .where(AuthSession.id == principal.session_id, AuthSession.status == "ACTIVE")
            .values(status="REVOKED", revoked_at=now, last_seen_at=now)
        )
        await AuditRepository(session).record(
            action="AUTH_LOGOUT",
            object_type="AuthSession",
            object_id=principal.session_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
        )

    async def change_password(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        current_password: str,
        new_password: str,
        client_ip: str | None,
        user_agent: str | None,
        request_id: UUID | None,
    ) -> IssuedSession:
        user = await session.get(UserAccount, principal.user_id, with_for_update=True)
        if user is None or not self.passwords.verify(user.password_hash, current_password):
            raise self._authentication_failed()
        if self.passwords.verify(user.password_hash, new_password):
            raise DomainError(
                code="PASSWORD_REUSE_FORBIDDEN",
                message_key="errors.auth.password_reuse_forbidden",
                status_code=422,
            )
        user.password_hash = self.passwords.hash(new_password)
        user.must_change_password = False
        user.password_changed_at = datetime.now(UTC)
        user.updated_at = datetime.now(UTC)
        user.version += 1
        await session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.status == "ACTIVE")
            .values(status="REVOKED", revoked_at=datetime.now(UTC))
        )
        issued = await self._issue(session, user, client_ip=client_ip, user_agent=user_agent)
        await AuditRepository(session).record(
            action="AUTH_PASSWORD_CHANGED",
            object_type="UserAccount",
            object_id=user.id,
            actor_user_id=user.id,
            outcome="SUCCESS",
            request_id=request_id,
        )
        return issued

    async def _issue(
        self,
        session: AsyncSession,
        user: UserAccount,
        *,
        client_ip: str | None,
        user_agent: str | None,
    ) -> IssuedSession:
        now = datetime.now(UTC)
        access_token = new_token()
        refresh_token = new_token()
        csrf_token = new_token()
        auth_session = AuthSession(
            id=uuid4(),
            user_id=user.id,
            access_token_hash=token_hash(access_token),
            refresh_token_hash=token_hash(refresh_token),
            csrf_token_hash=token_hash(csrf_token),
            status="ACTIVE",
            access_expires_at=now + timedelta(minutes=self.settings.access_token_minutes),
            refresh_expires_at=now + timedelta(hours=self.settings.refresh_session_hours),
            client_ip_hash=private_value_hash(client_ip) if client_ip else None,
            user_agent_hash=private_value_hash(user_agent) if user_agent else None,
            last_seen_at=now,
        )
        session.add(auth_session)
        principal = await self._principal(session, user, auth_session.id)
        return IssuedSession(
            principal=principal,
            access_token=access_token,
            refresh_token=refresh_token,
            csrf_token=csrf_token,
            access_expires_at=auth_session.access_expires_at,
            refresh_expires_at=auth_session.refresh_expires_at,
        )

    @staticmethod
    async def _principal(
        session: AsyncSession, user: UserAccount, auth_session_id: UUID
    ) -> Principal:
        result = await session.execute(
            select(RoleAssignment).where(
                RoleAssignment.user_id == user.id,
                RoleAssignment.status == "ACTIVE",
                RoleAssignment.source == RoleGrantSource.ASSIGNMENT.value,
            )
        )
        roles = tuple(
            RoleGrant(
                assignment_id=item.id,
                role=RoleCode(item.role_code),
                cooperative_id=item.cooperative_id,
            )
            for item in result.scalars()
        )
        now = datetime.now(UTC)
        emergency_result = await session.execute(
            select(BreakGlassGrant).where(
                BreakGlassGrant.target_user_id == user.id,
                BreakGlassGrant.status == "ACTIVE",
                BreakGlassGrant.expires_at > now,
            )
        )
        emergency_roles = tuple(
            RoleGrant(
                assignment_id=item.id,
                role=RoleCode(item.role_code),
                cooperative_id=item.cooperative_id,
                source=RoleGrantSource.BREAK_GLASS,
                expires_at=item.expires_at,
            )
            for item in emergency_result.scalars()
        )
        return Principal(
            user_id=user.id,
            session_id=auth_session_id,
            login=user.login,
            member_id=user.member_id,
            must_change_password=user.must_change_password,
            roles=roles + emergency_roles,
        )

    @staticmethod
    def _authentication_failed() -> DomainError:
        return DomainError(
            code="AUTHENTICATION_FAILED",
            message_key="errors.auth.authentication_failed",
            status_code=401,
        )
