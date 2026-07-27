"""Authentication endpoints with cookie-bound refresh rotation."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response

from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.api.identity_schemas import (
    ChangePasswordRequest,
    CommandEnvelope,
    CommandResult,
    LoginRequest,
    PrincipalEnvelope,
    PrincipalResponse,
    RoleGrantResponse,
    SecurityStateEnvelope,
    SecurityStateResponse,
    SessionEnvelope,
    SessionResponse,
    StepUpEnvelope,
    StepUpResponse,
    TotpConfirmationRequest,
    TotpDisableRequest,
    TotpEnrollmentEnvelope,
    TotpEnrollmentRequest,
    TotpEnrollmentResponse,
)
from cooperative_clearing.modules.identity.application.authentication import (
    AuthenticationService,
    IssuedSession,
)
from cooperative_clearing.modules.identity.application.security import IdentitySecurityService
from cooperative_clearing.modules.identity.domain.types import Principal
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
REFRESH_COOKIE = "coop_refresh"
CSRF_COOKIE = "coop_csrf"


def _request_uuid() -> UUID | None:
    try:
        return UUID(get_request_id())
    except ValueError:
        return None


def _principal_response(principal: Principal) -> PrincipalResponse:
    return PrincipalResponse(
        user_id=principal.user_id,
        login=principal.login,
        member_id=principal.member_id,
        must_change_password=principal.must_change_password,
        roles=[
            RoleGrantResponse(
                assignment_id=grant.assignment_id,
                role=grant.role,
                cooperative_id=grant.cooperative_id,
                source=grant.source,
                expires_at=grant.expires_at,
            )
            for grant in principal.roles
        ],
    )


def _session_response(issued: IssuedSession) -> SessionResponse:
    return SessionResponse(
        access_token=issued.access_token,
        access_expires_at=issued.access_expires_at,
        refresh_expires_at=issued.refresh_expires_at,
        principal=_principal_response(issued.principal),
    )


def _set_session_cookies(response: Response, issued: IssuedSession, *, secure: bool) -> None:
    max_age = max(0, int((issued.refresh_expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        REFRESH_COOKIE,
        issued.refresh_token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/api/v1/auth",
    )
    response.set_cookie(
        CSRF_COOKIE,
        issued.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


def _clear_session_cookies(response: Response, *, secure: bool) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path="/api/v1/auth",
        secure=secure,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(
        CSRF_COOKIE,
        path="/",
        secure=secure,
        httponly=False,
        samesite="strict",
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


@router.post("/login", response_model=SessionEnvelope)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> SessionEnvelope:
    service = AuthenticationService(settings)
    async with database.session() as session:
        try:
            issued = await service.login(
                session,
                login=payload.login,
                password=payload.password.get_secret_value(),
                client_ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                request_id=_request_uuid(),
            )
        except DomainError:
            await session.commit()
            raise
        await session.commit()
    _set_session_cookies(response, issued, secure=settings.secure_auth_cookies)
    return SessionEnvelope(data=_session_response(issued), request_id=get_request_id())


@router.post("/refresh", response_model=SessionEnvelope)
async def refresh(
    request: Request,
    response: Response,
    settings: SettingsDependency,
    database: DatabaseDependency,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
) -> SessionEnvelope:
    refresh_token = request.cookies.get(REFRESH_COOKIE, "")
    csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
    if not refresh_token or not csrf_cookie or not x_csrf_token:
        raise DomainError(
            code="AUTHENTICATION_FAILED",
            message_key="errors.auth.authentication_failed",
            status_code=401,
        )
    async with database.session() as session:
        issued = await AuthenticationService(settings).refresh(
            session,
            refresh_token=refresh_token,
            csrf_cookie=csrf_cookie,
            csrf_header=x_csrf_token,
            client_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            request_id=_request_uuid(),
        )
        await session.commit()
    _set_session_cookies(response, issued, secure=settings.secure_auth_cookies)
    return SessionEnvelope(data=_session_response(issued), request_id=get_request_id())


@router.post("/logout", status_code=204)
async def logout(
    principal: PrincipalDependency,
    response: Response,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> None:
    async with database.session() as session:
        await AuthenticationService(settings).logout(session, principal, _request_uuid())
        await session.commit()
    _clear_session_cookies(response, secure=settings.secure_auth_cookies)


@router.get("/me", response_model=PrincipalEnvelope)
async def me(principal: PrincipalDependency) -> PrincipalEnvelope:
    return PrincipalEnvelope(data=_principal_response(principal), request_id=get_request_id())


@router.post("/change-password", response_model=SessionEnvelope)
async def change_password(
    payload: ChangePasswordRequest,
    principal: PrincipalDependency,
    request: Request,
    response: Response,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> SessionEnvelope:
    async with database.session() as session:
        issued = await AuthenticationService(settings).change_password(
            session,
            principal=principal,
            current_password=payload.current_password.get_secret_value(),
            new_password=payload.new_password.get_secret_value(),
            client_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            request_id=_request_uuid(),
        )
        await session.commit()
    _set_session_cookies(response, issued, secure=settings.secure_auth_cookies)
    return SessionEnvelope(data=_session_response(issued), request_id=get_request_id())


@router.get("/security", response_model=SecurityStateEnvelope)
async def security_state(
    principal: PrincipalDependency,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> SecurityStateEnvelope:
    async with database.session() as session:
        state = await IdentitySecurityService(settings).security_state(session, principal)
    return SecurityStateEnvelope(
        data=SecurityStateResponse.model_validate(state), request_id=get_request_id()
    )


@router.post("/totp/enrollment", response_model=TotpEnrollmentEnvelope, status_code=201)
async def begin_totp_enrollment(
    payload: TotpEnrollmentRequest,
    principal: PrincipalDependency,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> TotpEnrollmentEnvelope:
    async with database.session() as session:
        try:
            enrollment = await IdentitySecurityService(settings).begin_totp_enrollment(
                session,
                principal=principal,
                current_password=payload.current_password.get_secret_value(),
                current_totp_code=payload.current_totp_code,
                request_id=_request_uuid(),
            )
        except DomainError:
            await session.commit()
            raise
        await session.commit()
    return TotpEnrollmentEnvelope(
        data=TotpEnrollmentResponse(
            factor_id=enrollment.factor_id,
            secret=enrollment.secret,
            provisioning_uri=enrollment.provisioning_uri,
            expires_at=enrollment.expires_at,
        ),
        request_id=get_request_id(),
    )


@router.post("/totp/enrollment/confirm", response_model=StepUpEnvelope)
async def confirm_totp_enrollment(
    payload: TotpConfirmationRequest,
    principal: PrincipalDependency,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> StepUpEnvelope:
    async with database.session() as session:
        try:
            grant = await IdentitySecurityService(settings).confirm_totp_enrollment(
                session,
                principal=principal,
                code=payload.code,
                request_id=_request_uuid(),
            )
        except DomainError:
            await session.commit()
            raise
        await session.commit()
    return StepUpEnvelope(
        data=StepUpResponse(verified_at=grant.verified_at, expires_at=grant.expires_at),
        request_id=get_request_id(),
    )


@router.post("/step-up/totp", response_model=StepUpEnvelope)
async def verify_totp_step_up(
    payload: TotpConfirmationRequest,
    principal: PrincipalDependency,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> StepUpEnvelope:
    async with database.session() as session:
        try:
            grant = await IdentitySecurityService(settings).verify_step_up(
                session,
                principal=principal,
                code=payload.code,
                request_id=_request_uuid(),
            )
        except DomainError:
            await session.commit()
            raise
        await session.commit()
    return StepUpEnvelope(
        data=StepUpResponse(verified_at=grant.verified_at, expires_at=grant.expires_at),
        request_id=get_request_id(),
    )


@router.delete("/totp", response_model=CommandEnvelope)
async def disable_totp(
    payload: TotpDisableRequest,
    principal: PrincipalDependency,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> CommandEnvelope:
    async with database.session() as session:
        try:
            event_id = await IdentitySecurityService(settings).disable_totp(
                session,
                principal=principal,
                current_password=payload.current_password.get_secret_value(),
                code=payload.code,
                reason_code=payload.reason_code,
                request_id=_request_uuid(),
            )
        except DomainError:
            await session.commit()
            raise
        await session.commit()
    return CommandEnvelope(
        data=CommandResult(event_id=event_id, object_id=principal.user_id),
        request_id=get_request_id(),
    )
