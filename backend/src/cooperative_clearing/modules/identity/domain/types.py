"""Stable identity lifecycle and authorization types."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from cooperative_clearing.shared.domain.errors import DomainError


class CooperativeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class MemberStatus(StrEnum):
    APPLICANT = "APPLICANT"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    LIMITED = "LIMITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REJECTED = "REJECTED"
    EXITED = "EXITED"


class MembershipStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ENDED = "ENDED"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class AssignmentStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    REJECTED = "REJECTED"


class RoleGrantSource(StrEnum):
    ASSIGNMENT = "ASSIGNMENT"
    BREAK_GLASS = "BREAK_GLASS"


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class RoleCode(StrEnum):
    EXCHANGE_PARTICIPANT = "EXCHANGE_PARTICIPANT"
    MEMBER_REGISTRAR = "MEMBER_REGISTRAR"
    COOPERATIVE_ADMIN = "COOPERATIVE_ADMIN"
    DATA_STEWARD = "DATA_STEWARD"
    WAREHOUSE_CUSTODIAN = "WAREHOUSE_CUSTODIAN"
    INVENTORY_CONTROLLER = "INVENTORY_CONTROLLER"
    LOGISTICS_OPERATOR = "LOGISTICS_OPERATOR"
    RIGHTS_OPERATOR = "RIGHTS_OPERATOR"
    RISK_ADMIN = "RISK_ADMIN"
    CLEARING_OPERATOR = "CLEARING_OPERATOR"
    CLEARING_CONTROLLER = "CLEARING_CONTROLLER"
    CLEARING_FINALIZER = "CLEARING_FINALIZER"
    SOLIDARITY_OPERATOR = "SOLIDARITY_OPERATOR"
    SOLIDARITY_CONTROLLER = "SOLIDARITY_CONTROLLER"
    CRISIS_OPERATOR = "CRISIS_OPERATOR"
    CRISIS_CONTROLLER = "CRISIS_CONTROLLER"
    SECURITY_ADMIN = "SECURITY_ADMIN"
    NODE_REGISTRAR = "NODE_REGISTRAR"
    NODE_TECHNICAL_CUSTODIAN = "NODE_TECHNICAL_CUSTODIAN"
    NODE_SECURITY_ADMIN = "NODE_SECURITY_ADMIN"
    NODE_BUSINESS_OPERATOR = "NODE_BUSINESS_OPERATOR"
    NODE_AUDITOR = "NODE_AUDITOR"
    AUDITOR = "AUDITOR"
    ARBITRATOR = "ARBITRATOR"


PRIVILEGED_ROLES = frozenset(
    {
        RoleCode.SECURITY_ADMIN,
        RoleCode.NODE_REGISTRAR,
        RoleCode.NODE_TECHNICAL_CUSTODIAN,
        RoleCode.NODE_SECURITY_ADMIN,
        RoleCode.NODE_BUSINESS_OPERATOR,
        RoleCode.NODE_AUDITOR,
        RoleCode.AUDITOR,
        RoleCode.ARBITRATOR,
    }
)

MEMBER_TRANSITIONS: dict[MemberStatus, frozenset[MemberStatus]] = {
    MemberStatus.APPLICANT: frozenset({MemberStatus.PENDING_VERIFICATION, MemberStatus.REJECTED}),
    MemberStatus.PENDING_VERIFICATION: frozenset(
        {MemberStatus.LIMITED, MemberStatus.ACTIVE, MemberStatus.REJECTED}
    ),
    MemberStatus.LIMITED: frozenset(
        {MemberStatus.ACTIVE, MemberStatus.SUSPENDED, MemberStatus.EXITED}
    ),
    MemberStatus.ACTIVE: frozenset({MemberStatus.SUSPENDED, MemberStatus.EXITED}),
    MemberStatus.SUSPENDED: frozenset({MemberStatus.ACTIVE, MemberStatus.EXITED}),
    MemberStatus.REJECTED: frozenset(),
    MemberStatus.EXITED: frozenset(),
}

COOPERATIVE_TRANSITIONS: dict[CooperativeStatus, frozenset[CooperativeStatus]] = {
    CooperativeStatus.ACTIVE: frozenset({CooperativeStatus.SUSPENDED}),
    CooperativeStatus.SUSPENDED: frozenset({CooperativeStatus.ACTIVE}),
}

MEMBERSHIP_TRANSITIONS: dict[MembershipStatus, frozenset[MembershipStatus]] = {
    MembershipStatus.PENDING: frozenset(
        {MembershipStatus.ACTIVE, MembershipStatus.SUSPENDED, MembershipStatus.ENDED}
    ),
    MembershipStatus.ACTIVE: frozenset(
        {MembershipStatus.SUSPENDED, MembershipStatus.ENDED}
    ),
    MembershipStatus.SUSPENDED: frozenset(
        {MembershipStatus.ACTIVE, MembershipStatus.ENDED}
    ),
    MembershipStatus.ENDED: frozenset(),
}

USER_TRANSITIONS: dict[UserStatus, frozenset[UserStatus]] = {
    UserStatus.ACTIVE: frozenset({UserStatus.DISABLED}),
    UserStatus.DISABLED: frozenset({UserStatus.ACTIVE}),
}


@dataclass(frozen=True, slots=True)
class RoleGrant:
    assignment_id: UUID
    role: RoleCode
    cooperative_id: UUID | None
    source: RoleGrantSource = RoleGrantSource.ASSIGNMENT
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    session_id: UUID
    login: str
    member_id: UUID | None
    must_change_password: bool
    roles: tuple[RoleGrant, ...]

    @property
    def break_glass_grants(self) -> tuple[RoleGrant, ...]:
        return tuple(grant for grant in self.roles if grant.source is RoleGrantSource.BREAK_GLASS)

    def has_role(self, roles: set[RoleCode], cooperative_id: UUID | None = None) -> bool:
        return any(
            grant.role in roles
            and (
                cooperative_id is None
                or grant.cooperative_id is None
                or grant.cooperative_id == cooperative_id
            )
            for grant in self.roles
        )

    def has_permanent_role(self, roles: set[RoleCode], cooperative_id: UUID | None = None) -> bool:
        return any(
            grant.source is RoleGrantSource.ASSIGNMENT
            and grant.role in roles
            and (
                cooperative_id is None
                or grant.cooperative_id is None
                or grant.cooperative_id == cooperative_id
            )
            for grant in self.roles
        )


def normalize_login(value: str) -> str:
    normalized = value.strip().casefold()
    if not normalized or len(normalized) > 120:
        raise DomainError(
            code="LOGIN_INVALID",
            message_key="errors.identity.login_invalid",
            status_code=422,
        )
    return normalized


def require_role(
    principal: Principal,
    allowed: set[RoleCode],
    cooperative_id: UUID | None = None,
) -> None:
    if principal.must_change_password:
        raise DomainError(
            code="PASSWORD_CHANGE_REQUIRED",
            message_key="errors.auth.password_change_required",
            status_code=403,
        )
    if not principal.has_role(allowed, cooperative_id):
        raise DomainError(
            code="AUTHORIZATION_DENIED",
            message_key="errors.auth.authorization_denied",
            status_code=403,
        )


def require_permanent_role(
    principal: Principal,
    allowed: set[RoleCode],
    cooperative_id: UUID | None = None,
) -> None:
    if principal.must_change_password:
        raise DomainError(
            code="PASSWORD_CHANGE_REQUIRED",
            message_key="errors.auth.password_change_required",
            status_code=403,
        )
    if not principal.has_permanent_role(allowed, cooperative_id):
        raise DomainError(
            code="PERMANENT_ROLE_REQUIRED",
            message_key="errors.auth.permanent_role_required",
            status_code=403,
        )


def ensure_member_transition(current: MemberStatus, target: MemberStatus) -> None:
    if target not in MEMBER_TRANSITIONS[current]:
        raise DomainError(
            code="MEMBER_STATUS_TRANSITION_INVALID",
            message_key="errors.identity.member_status_transition_invalid",
            parameters={"current": current.value, "target": target.value},
            status_code=409,
        )


def ensure_cooperative_transition(
    current: CooperativeStatus, target: CooperativeStatus
) -> None:
    if target not in COOPERATIVE_TRANSITIONS[current]:
        raise DomainError(
            code="COOPERATIVE_STATUS_TRANSITION_INVALID",
            message_key="errors.identity.cooperative_status_transition_invalid",
            parameters={"current": current.value, "target": target.value},
            status_code=409,
        )


def ensure_membership_transition(
    current: MembershipStatus, target: MembershipStatus
) -> None:
    if target not in MEMBERSHIP_TRANSITIONS[current]:
        raise DomainError(
            code="MEMBERSHIP_STATUS_TRANSITION_INVALID",
            message_key="errors.identity.membership_status_transition_invalid",
            parameters={"current": current.value, "target": target.value},
            status_code=409,
        )


def ensure_user_transition(current: UserStatus, target: UserStatus) -> None:
    if target not in USER_TRANSITIONS[current]:
        raise DomainError(
            code="USER_STATUS_TRANSITION_INVALID",
            message_key="errors.identity.user_status_transition_invalid",
            parameters={"current": current.value, "target": target.value},
            status_code=409,
        )
