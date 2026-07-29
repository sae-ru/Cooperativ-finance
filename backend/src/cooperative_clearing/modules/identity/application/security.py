"""Local MFA, session step-up, recovery, and scoped break-glass workflows."""

import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pyotp
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, update
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
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    AccountRecoveryRequest,
    AuthenticationFactor,
    AuthSession,
    BreakGlassGrant,
    Cooperative,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.journal.application.service import (
    ActorClaim,
    SignedJournalService,
)
from cooperative_clearing.modules.journal.domain.assurance import (
    AccountabilityParty,
    AccountabilityPartyKind,
    CommandAssurance,
    ExposureCategory,
    ExposureClaim,
    ExposureEffect,
    actor_party,
    member_party,
    node_party,
)
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.secrets import read_text_secret
from cooperative_clearing.shared.core.security import PasswordService
from cooperative_clearing.shared.domain.errors import DomainError

TOTP_CODE = re.compile(r"^[0-9]{6}$")
TOTP_KEY_VERSION = "v1"
TOTP_ISSUER = "Cooperative Clearing"
BREAK_GLASS_ROLES = frozenset(
    {
        RoleCode.SECURITY_ADMIN,
        RoleCode.NODE_SECURITY_ADMIN,
        RoleCode.NODE_TECHNICAL_CUSTODIAN,
        RoleCode.CRISIS_OPERATOR,
        RoleCode.CRISIS_CONTROLLER,
    }
)
NODE_BREAK_GLASS_ROLES = frozenset(
    {RoleCode.NODE_SECURITY_ADMIN, RoleCode.NODE_TECHNICAL_CUSTODIAN}
)
RECOVERY_CONTROL_ROLES = frozenset(
    {
        RoleCode.SECURITY_ADMIN,
        RoleCode.NODE_SECURITY_ADMIN,
        RoleCode.NODE_REGISTRAR,
        RoleCode.AUDITOR,
        RoleCode.NODE_AUDITOR,
    }
)


@dataclass(frozen=True, slots=True)
class TotpEnrollment:
    factor_id: UUID
    secret: str
    provisioning_uri: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StepUpGrant:
    method: str
    verified_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SecurityCommandResult:
    event_id: UUID
    object_id: UUID
    replayed: bool = False


class MfaSecretCipher:
    """Encrypt TOTP seed material with a purpose-specific node key."""

    def __init__(self, key_file: Path) -> None:
        encoded = read_text_secret(key_file, minimum_length=64)
        try:
            key = bytes.fromhex(encoded)
        except ValueError as exc:
            raise _error("MFA_ENCRYPTION_KEY_INVALID", 503) from exc
        if len(key) != 32:
            raise _error("MFA_ENCRYPTION_KEY_INVALID", 503)
        self._key = key
        self._cipher = AESGCM(key)

    def encrypt(self, *, factor_id: UUID, user_id: UUID, secret: str) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(
            nonce, secret.encode("ascii"), self._aad(factor_id, user_id)
        )
        return nonce, ciphertext

    def decrypt(self, factor: AuthenticationFactor) -> str:
        try:
            cleartext = self._cipher.decrypt(
                factor.secret_nonce,
                factor.secret_ciphertext,
                self._aad(factor.id, factor.user_id),
            )
            return cleartext.decode("ascii")
        except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
            raise _error("MFA_SECRET_UNAVAILABLE", 503) from exc

    def private_fingerprint(self, value: str) -> str:
        return hmac.new(
            self._key,
            f"cooperative-clearing:idempotency:v1:{value}".encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _aad(factor_id: UUID, user_id: UUID) -> bytes:
        return f"cooperative-clearing:mfa:v1:{user_id}:{factor_id}".encode("ascii")


class IdentitySecurityService:
    def __init__(
        self,
        settings: Settings,
        *,
        passwords: PasswordService | None = None,
        cipher: MfaSecretCipher | None = None,
    ) -> None:
        self.settings = settings
        self.passwords = passwords or PasswordService()
        self.cipher = cipher or MfaSecretCipher(settings.mfa_encryption_key_file)
        self.journal = SignedJournalService(settings)

    async def security_state(
        self, session: AsyncSession, principal: Principal
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        factors = list(
            (
                await session.execute(
                    select(AuthenticationFactor)
                    .where(AuthenticationFactor.user_id == principal.user_id)
                    .order_by(AuthenticationFactor.created_at.desc())
                )
            ).scalars()
        )
        active = next((item for item in factors if item.status == "ACTIVE"), None)
        pending = next(
            (
                item
                for item in factors
                if item.status == "PENDING"
                and item.enrollment_expires_at is not None
                and item.enrollment_expires_at > now
            ),
            None,
        )
        auth_session = await session.get(AuthSession, principal.session_id)
        step_up_active = bool(
            auth_session is not None
            and auth_session.status == "ACTIVE"
            and auth_session.step_up_expires_at is not None
            and auth_session.step_up_expires_at > now
        )
        return {
            "totp_enabled": active is not None,
            "totp_confirmed_at": active.confirmed_at if active else None,
            "enrollment_pending": pending is not None,
            "enrollment_expires_at": pending.enrollment_expires_at if pending else None,
            "step_up_active": step_up_active,
            "step_up_method": auth_session.step_up_method
            if step_up_active and auth_session
            else None,
            "step_up_expires_at": (
                auth_session.step_up_expires_at if step_up_active and auth_session else None
            ),
            "break_glass_grants": len(principal.break_glass_grants),
        }

    async def begin_totp_enrollment(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        current_password: str,
        current_totp_code: str | None,
        request_id: UUID | None,
    ) -> TotpEnrollment:
        user = await session.get(UserAccount, principal.user_id, with_for_update=True)
        if user is None or not self.passwords.verify(user.password_hash, current_password):
            raise _error("AUTHENTICATION_FAILED", 401, "errors.auth.authentication_failed")
        now = datetime.now(UTC)
        factors = list(
            (
                await session.execute(
                    select(AuthenticationFactor)
                    .where(
                        AuthenticationFactor.user_id == principal.user_id,
                        AuthenticationFactor.status.in_(("ACTIVE", "PENDING")),
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        active = next((item for item in factors if item.status == "ACTIVE"), None)
        if active is not None:
            if current_totp_code is None:
                raise _error("CURRENT_TOTP_REQUIRED", 403)
            await self._accept_totp(
                session,
                active,
                current_totp_code,
                now=now,
                action="MFA_TOTP_ROTATION_AUTH",
                request_id=request_id,
            )
        for factor in factors:
            if factor.status == "PENDING":
                factor.status = "DISABLED"
                factor.disabled_at = now
                factor.version += 1
        factor_id = uuid4()
        secret = pyotp.random_base32(length=32)
        nonce, ciphertext = self.cipher.encrypt(
            factor_id=factor_id, user_id=principal.user_id, secret=secret
        )
        expires_at = now + timedelta(minutes=self.settings.totp_enrollment_minutes)
        factor = AuthenticationFactor(
            id=factor_id,
            user_id=principal.user_id,
            factor_type="TOTP",
            status="PENDING",
            secret_nonce=nonce,
            secret_ciphertext=ciphertext,
            encryption_key_version=TOTP_KEY_VERSION,
            enrollment_expires_at=expires_at,
        )
        session.add(factor)
        await AuditRepository(session).record(
            action="MFA_TOTP_ENROLLMENT_STARTED",
            object_type="AuthenticationFactor",
            object_id=factor.id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={"rotation": active is not None, "expires_at": expires_at.isoformat()},
        )
        uri = pyotp.TOTP(secret).provisioning_uri(
            name=principal.login,
            issuer_name=TOTP_ISSUER,
        )
        return TotpEnrollment(
            factor_id=factor.id,
            secret=secret,
            provisioning_uri=uri,
            expires_at=expires_at,
        )

    async def confirm_totp_enrollment(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        code: str,
        request_id: UUID | None,
    ) -> StepUpGrant:
        now = datetime.now(UTC)
        pending = await session.scalar(
            select(AuthenticationFactor)
            .where(
                AuthenticationFactor.user_id == principal.user_id,
                AuthenticationFactor.factor_type == "TOTP",
                AuthenticationFactor.status == "PENDING",
            )
            .with_for_update()
        )
        if pending is None:
            raise _error("TOTP_ENROLLMENT_NOT_FOUND", 404)
        if pending.enrollment_expires_at is None or pending.enrollment_expires_at <= now:
            pending.status = "DISABLED"
            pending.disabled_at = now
            pending.version += 1
            raise _error("TOTP_ENROLLMENT_EXPIRED", 409)
        counter = await self._accept_totp(
            session,
            pending,
            code,
            now=now,
            action="MFA_TOTP_ENROLLMENT_CONFIRM",
            request_id=request_id,
        )
        active = await session.scalar(
            select(AuthenticationFactor)
            .where(
                AuthenticationFactor.user_id == principal.user_id,
                AuthenticationFactor.factor_type == "TOTP",
                AuthenticationFactor.status == "ACTIVE",
            )
            .with_for_update()
        )
        if active is not None:
            active.status = "DISABLED"
            active.disabled_at = now
            active.version += 1
        pending.status = "ACTIVE"
        pending.confirmed_at = now
        pending.enrollment_expires_at = None
        pending.last_accepted_counter = counter
        pending.version += 1
        grant = await self._grant_step_up(session, principal, now=now)
        await AuditRepository(session).record(
            action="MFA_TOTP_ENROLLMENT_CONFIRMED",
            object_type="AuthenticationFactor",
            object_id=pending.id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
        )
        return grant

    async def verify_step_up(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        code: str,
        request_id: UUID | None,
    ) -> StepUpGrant:
        factor = await session.scalar(
            select(AuthenticationFactor)
            .where(
                AuthenticationFactor.user_id == principal.user_id,
                AuthenticationFactor.factor_type == "TOTP",
                AuthenticationFactor.status == "ACTIVE",
            )
            .with_for_update()
        )
        if factor is None:
            raise _error("TOTP_NOT_ENROLLED", 409)
        now = datetime.now(UTC)
        await self._accept_totp(
            session,
            factor,
            code,
            now=now,
            action="AUTH_STEP_UP",
            request_id=request_id,
        )
        grant = await self._grant_step_up(session, principal, now=now)
        await AuditRepository(session).record(
            action="AUTH_STEP_UP",
            object_type="AuthSession",
            object_id=principal.session_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={"method": "TOTP", "expires_at": grant.expires_at.isoformat()},
        )
        return grant

    async def disable_totp(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        current_password: str,
        code: str,
        reason_code: str,
        request_id: UUID | None,
    ) -> UUID:
        user = await session.get(UserAccount, principal.user_id, with_for_update=True)
        if user is None or not self.passwords.verify(user.password_hash, current_password):
            raise _error("AUTHENTICATION_FAILED", 401, "errors.auth.authentication_failed")
        factor = await session.scalar(
            select(AuthenticationFactor)
            .where(
                AuthenticationFactor.user_id == principal.user_id,
                AuthenticationFactor.factor_type == "TOTP",
                AuthenticationFactor.status == "ACTIVE",
            )
            .with_for_update()
        )
        if factor is None:
            raise _error("TOTP_NOT_ENROLLED", 409)
        now = datetime.now(UTC)
        await self._accept_totp(
            session,
            factor,
            code,
            now=now,
            action="MFA_TOTP_DISABLE_AUTH",
            request_id=request_id,
        )
        factor.status = "DISABLED"
        factor.disabled_at = now
        factor.version += 1
        await session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == principal.user_id, AuthSession.status == "ACTIVE")
            .values(
                step_up_method=None,
                step_up_verified_at=None,
                step_up_expires_at=None,
            )
        )
        return await AuditRepository(session).record(
            action="MFA_TOTP_DISABLED",
            object_type="AuthenticationFactor",
            object_id=factor.id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            reason_code=reason_code,
            request_id=request_id,
        )

    async def request_account_recovery(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        target_user_id: UUID,
        temporary_password: str,
        reason_code: str,
        evidence_id: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SecurityCommandResult:
        payload = {
            "target_user_id": target_user_id,
            "reason_code": reason_code,
            "evidence_id": evidence_id,
            "temporary_password_fingerprint": self.cipher.private_fingerprint(temporary_password),
        }
        record, replay = await self._begin(
            session, principal, "ACCOUNT_RECOVERY_REQUEST", idempotency_key, payload
        )
        if replay is not None:
            return replay
        if target_user_id == principal.user_id:
            raise _error("RECOVERY_SELF_REQUEST_FORBIDDEN", 409)
        target = await session.get(UserAccount, target_user_id)
        if target is None:
            raise _error("USER_NOT_FOUND", 404)
        now = datetime.now(UTC)
        recovery = AccountRecoveryRequest(
            id=uuid4(),
            target_user_id=target_user_id,
            requested_by_user_id=principal.user_id,
            temporary_password_hash=self.passwords.hash(temporary_password),
            reason_code=reason_code,
            evidence_id=evidence_id,
            status="PENDING_APPROVAL",
            expires_at=now + timedelta(minutes=self.settings.account_recovery_minutes),
        )
        session.add(recovery)
        actor = self._security_actor(principal)
        scope_party = self._security_scope_party(actor)
        event = await self.journal.append(
            session,
            event_type="identity.account_recovery_requested",
            aggregate_type="account_recovery_request",
            aggregate_id=recovery.id,
            aggregate_version=1,
            actor=actor,
            payload={
                "target_user_id": str(target_user_id),
                "requester_user_id": str(principal.user_id),
                "reason_code": reason_code,
                "expires_at": recovery.expires_at.isoformat(),
            },
            assurance=CommandAssurance(
                on_behalf_of=scope_party,
                exposure=ExposureClaim(
                    category=ExposureCategory.IDENTITY,
                    effect=ExposureEffect.REQUEST,
                    subject_type="user_account",
                    subject_id=target.id,
                    basis_refs=(f"recovery:{recovery.id}", evidence_id),
                ),
                evidence_refs=({"evidence_id": evidence_id},),
                next_responsible=(scope_party,),
                attesters=(actor_party(actor),),
            ),
        )
        await AuditRepository(session).record(
            action="ACCOUNT_RECOVERY_REQUESTED",
            object_type="AccountRecoveryRequest",
            object_id=recovery.id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            reason_code=reason_code,
            request_id=request_id,
            payload={
                "target_user_id": str(target_user_id),
                "evidence_id": evidence_id,
                "signed_event_id": str(event.event_id),
            },
        )
        return self._complete(record, event.event_id, recovery.id)

    async def decide_account_recovery(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        recovery_id: UUID,
        approve: bool,
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SecurityCommandResult:
        payload = {"recovery_id": recovery_id, "approve": approve, "reason_code": reason_code}
        record, replay = await self._begin(
            session, principal, "ACCOUNT_RECOVERY_DECIDE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        recovery = await session.get(AccountRecoveryRequest, recovery_id, with_for_update=True)
        if recovery is None:
            raise _error("ACCOUNT_RECOVERY_NOT_FOUND", 404)
        if recovery.status != "PENDING_APPROVAL":
            raise _error("ACCOUNT_RECOVERY_NOT_PENDING", 409)
        if principal.user_id in {recovery.requested_by_user_id, recovery.target_user_id}:
            raise _error("INDEPENDENT_APPROVAL_REQUIRED", 409)
        now = datetime.now(UTC)
        if recovery.expires_at <= now:
            recovery.status = "EXPIRED"
            recovery.version += 1
            raise _error("ACCOUNT_RECOVERY_EXPIRED", 409)
        target = await session.get(UserAccount, recovery.target_user_id, with_for_update=True)
        requester = await session.get(UserAccount, recovery.requested_by_user_id)
        if target is None or requester is None:
            raise _error("USER_NOT_FOUND", 404)
        target_party = self._user_party(target)
        requester_party = self._user_party(requester)
        recovery.decided_by_user_id = principal.user_id
        recovery.decided_at = now
        recovery.version += 1
        if approve:
            target.password_hash = recovery.temporary_password_hash
            target.status = "ACTIVE"
            target.must_change_password = True
            target.failed_login_attempts = 0
            target.locked_until = None
            target.password_changed_at = now
            target.updated_at = now
            target.version += 1
            await session.execute(
                update(AuthSession)
                .where(AuthSession.user_id == target.id, AuthSession.status == "ACTIVE")
                .values(status="REVOKED", revoked_at=now)
            )
            await session.execute(
                update(AuthenticationFactor)
                .where(
                    AuthenticationFactor.user_id == target.id,
                    AuthenticationFactor.status.in_(("ACTIVE", "PENDING")),
                )
                .values(status="DISABLED", disabled_at=now)
            )
            recovery.status = "EXECUTED"
            action = "ACCOUNT_RECOVERY_EXECUTED"
            outcome = "SUCCESS"
        else:
            recovery.status = "REJECTED"
            action = "ACCOUNT_RECOVERY_REJECTED"
            outcome = "DENIED"
        actor = self._security_actor(principal)
        scope_party = self._security_scope_party(actor)
        event = await self.journal.append(
            session,
            event_type=(
                "identity.account_recovery_executed"
                if approve
                else "identity.account_recovery_rejected"
            ),
            aggregate_type="account_recovery_request",
            aggregate_id=recovery.id,
            aggregate_version=recovery.version,
            actor=actor,
            payload={
                "target_user_id": str(recovery.target_user_id),
                "requester_user_id": str(recovery.requested_by_user_id),
                "decider_user_id": str(principal.user_id),
                "reason_code": reason_code,
                "status": recovery.status,
            },
            assurance=CommandAssurance(
                on_behalf_of=scope_party,
                exposure=ExposureClaim(
                    category=ExposureCategory.IDENTITY,
                    effect=(
                        ExposureEffect.EXECUTE if approve else ExposureEffect.REJECT
                    ),
                    subject_type="user_account",
                    subject_id=target.id,
                    basis_refs=(
                        f"recovery:{recovery.id}",
                        recovery.evidence_id,
                    ),
                ),
                evidence_refs=(
                    {"evidence_id": recovery.evidence_id},
                    {"recovery_id": str(recovery.id)},
                ),
                next_responsible=(target_party,) if approve else (),
                attesters=(requester_party,),
                approvers=(actor_party(actor),),
            ),
        )
        await AuditRepository(session).record(
            action=action,
            object_type="AccountRecoveryRequest",
            object_id=recovery.id,
            actor_user_id=principal.user_id,
            outcome=outcome,
            reason_code=reason_code,
            request_id=request_id,
            payload={
                "target_user_id": str(recovery.target_user_id),
                "requester_user_id": str(recovery.requested_by_user_id),
                "evidence_id": recovery.evidence_id,
                "signed_event_id": str(event.event_id),
            },
        )
        return self._complete(record, event.event_id, recovery.id)

    async def request_break_glass(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        target_user_id: UUID,
        role: RoleCode,
        cooperative_id: UUID | None,
        duration_minutes: int,
        reason_code: str,
        evidence_id: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SecurityCommandResult:
        payload = {
            "target_user_id": target_user_id,
            "role": role,
            "cooperative_id": cooperative_id,
            "duration_minutes": duration_minutes,
            "reason_code": reason_code,
            "evidence_id": evidence_id,
        }
        record, replay = await self._begin(
            session, principal, "BREAK_GLASS_REQUEST", idempotency_key, payload
        )
        if replay is not None:
            return replay
        if role not in BREAK_GLASS_ROLES:
            raise _error("BREAK_GLASS_ROLE_FORBIDDEN", 422)
        if duration_minutes > self.settings.break_glass_max_minutes:
            raise _error("BREAK_GLASS_DURATION_INVALID", 422)
        if (role in NODE_BREAK_GLASS_ROLES) != (cooperative_id is None):
            raise _error("BREAK_GLASS_SCOPE_INVALID", 422)
        if cooperative_id is not None and await session.get(Cooperative, cooperative_id) is None:
            raise _error("COOPERATIVE_NOT_FOUND", 404)
        target = await session.get(UserAccount, target_user_id)
        if target is None or target.status != "ACTIVE":
            raise _error("USER_NOT_FOUND", 404)
        now = datetime.now(UTC)
        grant_id = uuid4()
        approval_expires_at = now + timedelta(minutes=self.settings.account_recovery_minutes)
        grant = BreakGlassGrant(
            id=grant_id,
            target_user_id=target_user_id,
            role_code=role.value,
            cooperative_id=cooperative_id,
            requested_by_user_id=principal.user_id,
            reason_code=reason_code,
            evidence_id=evidence_id,
            requested_duration_minutes=duration_minutes,
            status="PENDING_APPROVAL",
            expires_at=approval_expires_at,
        )
        authority = RoleAssignment(
            id=grant_id,
            user_id=target_user_id,
            role_code=role.value,
            cooperative_id=cooperative_id,
            status="PENDING_APPROVAL",
            source=RoleGrantSource.BREAK_GLASS.value,
            expires_at=approval_expires_at,
            granted_by_user_id=principal.user_id,
        )
        session.add_all((grant, authority))
        actor = self._security_actor(principal, cooperative_id)
        scope_party = self._security_scope_party(actor)
        event = await self.journal.append(
            session,
            event_type="identity.break_glass_requested",
            aggregate_type="break_glass_grant",
            aggregate_id=grant.id,
            aggregate_version=1,
            actor=actor,
            payload={
                "target_user_id": str(target_user_id),
                "role": role.value,
                "duration_minutes": duration_minutes,
                "reason_code": reason_code,
                "approval_expires_at": approval_expires_at.isoformat(),
            },
            assurance=CommandAssurance(
                on_behalf_of=scope_party,
                exposure=ExposureClaim(
                    category=ExposureCategory.AUTHORITY,
                    effect=ExposureEffect.REQUEST,
                    subject_type="break_glass_grant",
                    subject_id=grant.id,
                    basis_refs=(
                        role.value,
                        str(target.id),
                        str(duration_minutes),
                        evidence_id,
                    ),
                ),
                evidence_refs=({"evidence_id": evidence_id},),
                next_responsible=(scope_party,),
                attesters=(actor_party(actor),),
            ),
        )
        await AuditRepository(session).record(
            action="BREAK_GLASS_REQUESTED",
            object_type="BreakGlassGrant",
            object_id=grant.id,
            actor_user_id=principal.user_id,
            cooperative_id=cooperative_id,
            outcome="SUCCESS",
            reason_code=reason_code,
            request_id=request_id,
            payload={
                "target_user_id": str(target_user_id),
                "role": role.value,
                "duration_minutes": duration_minutes,
                "evidence_id": evidence_id,
                "signed_event_id": str(event.event_id),
            },
        )
        return self._complete(record, event.event_id, grant.id)

    async def decide_break_glass(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        grant_id: UUID,
        approve: bool,
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SecurityCommandResult:
        payload = {"grant_id": grant_id, "approve": approve, "reason_code": reason_code}
        record, replay = await self._begin(
            session, principal, "BREAK_GLASS_DECIDE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        grant = await session.get(BreakGlassGrant, grant_id, with_for_update=True)
        if grant is None:
            raise _error("BREAK_GLASS_NOT_FOUND", 404)
        authority = await session.get(RoleAssignment, grant_id, with_for_update=True)
        if authority is None or authority.source != RoleGrantSource.BREAK_GLASS.value:
            raise _error("BREAK_GLASS_AUTHORITY_MISSING", 503)
        if grant.status != "PENDING_APPROVAL":
            raise _error("BREAK_GLASS_NOT_PENDING", 409)
        if principal.user_id in {grant.requested_by_user_id, grant.target_user_id}:
            raise _error("INDEPENDENT_APPROVAL_REQUIRED", 409)
        now = datetime.now(UTC)
        if grant.expires_at is None or grant.expires_at <= now:
            grant.status = "EXPIRED"
            grant.version += 1
            raise _error("BREAK_GLASS_REQUEST_EXPIRED", 409)
        target = await session.get(UserAccount, grant.target_user_id)
        requester = await session.get(UserAccount, grant.requested_by_user_id)
        if target is None or requester is None:
            raise _error("USER_NOT_FOUND", 404)
        target_party = self._user_party(target)
        requester_party = self._user_party(requester)
        grant.approved_by_user_id = principal.user_id
        grant.approved_at = now
        grant.version += 1
        if approve:
            grant.status = "ACTIVE"
            grant.expires_at = now + timedelta(minutes=grant.requested_duration_minutes)
            authority.status = "ACTIVE"
            authority.expires_at = grant.expires_at
            action = "BREAK_GLASS_ACTIVATED"
            outcome = "SUCCESS"
        else:
            grant.status = "REJECTED"
            grant.expires_at = None
            authority.status = "REJECTED"
            authority.expires_at = None
            action = "BREAK_GLASS_REJECTED"
            outcome = "DENIED"
        authority.approved_by_user_id = principal.user_id
        authority.approved_at = now
        authority.version += 1
        actor = self._security_actor(principal, grant.cooperative_id)
        scope_party = self._security_scope_party(actor)
        event = await self.journal.append(
            session,
            event_type=(
                "identity.break_glass_activated"
                if approve
                else "identity.break_glass_rejected"
            ),
            aggregate_type="break_glass_grant",
            aggregate_id=grant.id,
            aggregate_version=grant.version,
            actor=actor,
            payload={
                "target_user_id": str(grant.target_user_id),
                "role": grant.role_code,
                "requester_user_id": str(grant.requested_by_user_id),
                "approver_user_id": str(principal.user_id),
                "reason_code": reason_code,
                "status": grant.status,
                "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
            },
            assurance=CommandAssurance(
                on_behalf_of=scope_party,
                exposure=ExposureClaim(
                    category=ExposureCategory.AUTHORITY,
                    effect=ExposureEffect.CREATE if approve else ExposureEffect.REJECT,
                    subject_type="break_glass_grant",
                    subject_id=grant.id,
                    basis_refs=(
                        grant.role_code,
                        str(grant.requested_duration_minutes),
                        grant.evidence_id,
                    ),
                ),
                evidence_refs=(
                    {"evidence_id": grant.evidence_id},
                    {"grant_id": str(grant.id)},
                ),
                next_responsible=(target_party,) if approve else (),
                attesters=(requester_party,),
                approvers=(actor_party(actor),),
            ),
        )
        await AuditRepository(session).record(
            action=action,
            object_type="BreakGlassGrant",
            object_id=grant.id,
            actor_user_id=principal.user_id,
            cooperative_id=grant.cooperative_id,
            outcome=outcome,
            reason_code=reason_code,
            request_id=request_id,
            payload={
                "target_user_id": str(grant.target_user_id),
                "role": grant.role_code,
                "requester_user_id": str(grant.requested_by_user_id),
                "evidence_id": grant.evidence_id,
                "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
                "signed_event_id": str(event.event_id),
            },
        )
        return self._complete(record, event.event_id, grant.id)

    async def revoke_break_glass(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        grant_id: UUID,
        reason_code: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> SecurityCommandResult:
        payload = {"grant_id": grant_id, "reason_code": reason_code}
        record, replay = await self._begin(
            session, principal, "BREAK_GLASS_REVOKE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        grant = await session.get(BreakGlassGrant, grant_id, with_for_update=True)
        if grant is None:
            raise _error("BREAK_GLASS_NOT_FOUND", 404)
        authority = await session.get(RoleAssignment, grant_id, with_for_update=True)
        if authority is None or authority.source != RoleGrantSource.BREAK_GLASS.value:
            raise _error("BREAK_GLASS_AUTHORITY_MISSING", 503)
        if grant.status not in {"PENDING_APPROVAL", "ACTIVE"}:
            raise _error("BREAK_GLASS_NOT_REVOCABLE", 409)
        now = datetime.now(UTC)
        grant.status = "REVOKED"
        grant.revoked_by_user_id = principal.user_id
        grant.revoked_at = now
        grant.expires_at = now
        grant.version += 1
        authority.status = "REVOKED"
        authority.revoked_at = now
        authority.expires_at = now
        authority.version += 1
        actor = self._security_actor(principal, grant.cooperative_id)
        scope_party = self._security_scope_party(actor)
        event = await self.journal.append(
            session,
            event_type="identity.break_glass_revoked",
            aggregate_type="break_glass_grant",
            aggregate_id=grant.id,
            aggregate_version=grant.version,
            actor=actor,
            payload={
                "target_user_id": str(grant.target_user_id),
                "role": grant.role_code,
                "revoker_user_id": str(principal.user_id),
                "reason_code": reason_code,
                "status": grant.status,
            },
            assurance=CommandAssurance(
                on_behalf_of=scope_party,
                exposure=ExposureClaim(
                    category=ExposureCategory.AUTHORITY,
                    effect=ExposureEffect.REVOKE,
                    subject_type="break_glass_grant",
                    subject_id=grant.id,
                    basis_refs=(grant.role_code, grant.evidence_id),
                ),
                evidence_refs=(
                    {"evidence_id": grant.evidence_id},
                    {"grant_id": str(grant.id)},
                ),
                next_responsible=(scope_party,),
                approvers=(actor_party(actor),),
            ),
        )
        await AuditRepository(session).record(
            action="BREAK_GLASS_REVOKED",
            object_type="BreakGlassGrant",
            object_id=grant.id,
            actor_user_id=principal.user_id,
            cooperative_id=grant.cooperative_id,
            outcome="SUCCESS",
            reason_code=reason_code,
            request_id=request_id,
            payload={
                "target_user_id": str(grant.target_user_id),
                "role": grant.role_code,
                "signed_event_id": str(event.event_id),
            },
        )
        return self._complete(record, event.event_id, grant.id)

    def _security_scope_party(self, actor: ActorClaim) -> AccountabilityParty:
        if actor.organization_id is not None:
            return AccountabilityParty(
                kind=AccountabilityPartyKind.COOPERATIVE,
                reference=str(actor.organization_id),
            )
        return node_party(self.settings.node_code)

    @staticmethod
    def _user_party(user: UserAccount) -> AccountabilityParty:
        if user.member_id is None:
            raise _error("PERSONAL_ACTOR_REQUIRED", 403)
        return member_party(user.member_id)

    @staticmethod
    def _security_actor(principal: Principal, cooperative_id: UUID | None = None) -> ActorClaim:
        if principal.member_id is None:
            raise _error("PERSONAL_ACTOR_REQUIRED", 403)
        for grant in principal.roles:
            if (
                grant.source is RoleGrantSource.ASSIGNMENT
                and grant.role in RECOVERY_CONTROL_ROLES
                and (cooperative_id is None or grant.cooperative_id in {None, cooperative_id})
            ):
                return ActorClaim(
                    person_id=principal.member_id,
                    organization_id=cooperative_id,
                    role_assignment_id=grant.assignment_id,
                )
        raise _error("PERMANENT_SECURITY_ROLE_REQUIRED", 403)

    async def _grant_step_up(
        self, session: AsyncSession, principal: Principal, *, now: datetime
    ) -> StepUpGrant:
        auth_session = await session.get(AuthSession, principal.session_id, with_for_update=True)
        if auth_session is None or auth_session.status != "ACTIVE":
            raise _error("AUTHENTICATION_FAILED", 401, "errors.auth.authentication_failed")
        expires_at = now + timedelta(minutes=self.settings.step_up_ttl_minutes)
        auth_session.step_up_method = "TOTP"
        auth_session.step_up_verified_at = now
        auth_session.step_up_expires_at = expires_at
        return StepUpGrant(method="TOTP", verified_at=now, expires_at=expires_at)

    async def _accept_totp(
        self,
        session: AsyncSession,
        factor: AuthenticationFactor,
        code: str,
        *,
        now: datetime,
        action: str,
        request_id: UUID | None,
    ) -> int:
        if factor.locked_until is not None and factor.locked_until > now:
            raise _error("TOTP_TEMPORARILY_LOCKED", 429)
        counter = self._matching_counter(factor, code, now)
        if counter is None or (
            factor.last_accepted_counter is not None and counter <= factor.last_accepted_counter
        ):
            factor.failed_attempts += 1
            if factor.failed_attempts >= self.settings.auth_max_failed_attempts:
                factor.failed_attempts = 0
                factor.locked_until = now + timedelta(seconds=self.settings.auth_lock_seconds)
            factor.version += 1
            await AuditRepository(session).record(
                action=action,
                object_type="AuthenticationFactor",
                object_id=factor.id,
                actor_user_id=factor.user_id,
                outcome="DENIED",
                reason_code="TOTP_INVALID_OR_REPLAYED",
                request_id=request_id,
            )
            raise _error("TOTP_INVALID_OR_REPLAYED", 401)
        factor.last_accepted_counter = counter
        factor.failed_attempts = 0
        factor.locked_until = None
        factor.version += 1
        return counter

    def _matching_counter(
        self, factor: AuthenticationFactor, code: str, now: datetime
    ) -> int | None:
        if TOTP_CODE.fullmatch(code) is None:
            return None
        secret = self.cipher.decrypt(factor)
        totp = pyotp.TOTP(secret)
        current = totp.timecode(now)
        for counter in range(max(0, current - 1), current + 2):
            if pyotp.utils.strings_equal(totp.generate_otp(counter), code):
                return counter
        return None

    @staticmethod
    async def _begin(
        session: AsyncSession,
        principal: Principal,
        operation: str,
        idempotency_key: str,
        payload: object,
    ) -> tuple[IdempotencyRecord, SecurityCommandResult | None]:
        record = await IdempotencyRepository(session).begin(
            actor_user_id=principal.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_payload_hash(payload),
        )
        if record.status == "COMPLETED":
            stored = record.response_payload or {}
            return record, SecurityCommandResult(
                event_id=UUID(str(stored["event_id"])),
                object_id=UUID(str(stored["object_id"])),
                replayed=True,
            )
        return record, None

    @staticmethod
    def _complete(
        record: IdempotencyRecord, event_id: UUID, object_id: UUID
    ) -> SecurityCommandResult:
        IdempotencyRepository.complete(
            record,
            response_status=201,
            response_payload={"event_id": str(event_id), "object_id": str(object_id)},
        )
        return SecurityCommandResult(event_id=event_id, object_id=object_id)


async def require_step_up(
    session: AsyncSession,
    principal: Principal,
    *,
    operation: str,
    emergency_roles: frozenset[RoleCode] = frozenset(),
    request_id: UUID | None = None,
) -> None:
    now = datetime.now(UTC)
    auth_session = await session.get(AuthSession, principal.session_id)
    if (
        auth_session is not None
        and auth_session.status == "ACTIVE"
        and auth_session.step_up_expires_at is not None
        and auth_session.step_up_expires_at > now
    ):
        return
    emergency = next(
        (
            grant
            for grant in principal.roles
            if grant.source is RoleGrantSource.BREAK_GLASS
            and grant.role in emergency_roles
            and grant.expires_at is not None
            and grant.expires_at > now
        ),
        None,
    )
    if emergency is not None:
        await AuditRepository(session).record(
            action="BREAK_GLASS_STEP_UP_BYPASS",
            object_type="BreakGlassGrant",
            object_id=emergency.assignment_id,
            actor_user_id=principal.user_id,
            cooperative_id=emergency.cooperative_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={"operation": operation, "role": emergency.role.value},
        )
        return
    raise _error("STEP_UP_REQUIRED", 403)


async def expire_security_workflows(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    await session.execute(
        update(BreakGlassGrant)
        .where(
            BreakGlassGrant.status.in_(("PENDING_APPROVAL", "ACTIVE")),
            BreakGlassGrant.expires_at <= now,
        )
        .values(status="EXPIRED", version=BreakGlassGrant.version + 1)
    )
    await session.execute(
        update(RoleAssignment)
        .where(
            RoleAssignment.source == RoleGrantSource.BREAK_GLASS.value,
            RoleAssignment.status.in_(("PENDING_APPROVAL", "ACTIVE")),
            RoleAssignment.id.in_(
                select(BreakGlassGrant.id).where(
                    BreakGlassGrant.status == "EXPIRED",
                    BreakGlassGrant.expires_at <= now,
                )
            ),
        )
        .values(
            status="REVOKED",
            revoked_at=now,
            expires_at=now,
            version=RoleAssignment.version + 1,
        )
    )
    await session.execute(
        update(AccountRecoveryRequest)
        .where(
            AccountRecoveryRequest.status == "PENDING_APPROVAL",
            AccountRecoveryRequest.expires_at <= now,
        )
        .values(status="EXPIRED", version=AccountRecoveryRequest.version + 1)
    )


def _error(code: str, status_code: int, message_key: str | None = None) -> DomainError:
    return DomainError(
        code=code,
        message_key=message_key or f"errors.identity.{code.lower()}",
        status_code=status_code,
    )
