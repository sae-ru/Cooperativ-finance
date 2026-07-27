from uuid import uuid4

import pytest

from cooperative_clearing.modules.identity.domain.types import (
    PRIVILEGED_ROLES,
    CooperativeStatus,
    MembershipStatus,
    MemberStatus,
    Principal,
    RoleCode,
    RoleGrant,
    UserStatus,
    ensure_cooperative_transition,
    ensure_member_transition,
    ensure_membership_transition,
    ensure_user_transition,
    normalize_login,
    require_role,
)
from cooperative_clearing.shared.domain.errors import DomainError


def principal(*roles: tuple[RoleCode, object | None], password_change: bool = False) -> Principal:
    return Principal(
        user_id=uuid4(),
        session_id=uuid4(),
        login="operator",
        member_id=None,
        must_change_password=password_change,
        roles=tuple(
            RoleGrant(
                assignment_id=uuid4(),
                role=role,
                cooperative_id=scope,  # type: ignore[arg-type]
            )
            for role, scope in roles
        ),
    )


def test_login_normalization_and_validation() -> None:
    assert normalize_login("  Registrar ") == "registrar"
    with pytest.raises(DomainError) as failure:
        normalize_login(" ")
    assert failure.value.code == "LOGIN_INVALID"


def test_exchange_participant_is_a_scoped_non_privileged_role() -> None:
    assert RoleCode.EXCHANGE_PARTICIPANT not in PRIVILEGED_ROLES


def test_role_scope_and_password_change_gate() -> None:
    cooperative_id = uuid4()
    actor = principal((RoleCode.COOPERATIVE_ADMIN, cooperative_id))
    require_role(actor, {RoleCode.COOPERATIVE_ADMIN}, cooperative_id)
    assert actor.has_role({RoleCode.COOPERATIVE_ADMIN}, cooperative_id)
    assert not actor.has_role({RoleCode.COOPERATIVE_ADMIN}, uuid4())

    with pytest.raises(DomainError) as denied:
        require_role(actor, {RoleCode.SECURITY_ADMIN})
    assert denied.value.code == "AUTHORIZATION_DENIED"

    with pytest.raises(DomainError) as password_change:
        require_role(
            principal((RoleCode.SECURITY_ADMIN, None), password_change=True),
            {RoleCode.SECURITY_ADMIN},
        )
    assert password_change.value.code == "PASSWORD_CHANGE_REQUIRED"


def test_member_transition_policy() -> None:
    ensure_member_transition(MemberStatus.APPLICANT, MemberStatus.PENDING_VERIFICATION)
    ensure_member_transition(MemberStatus.SUSPENDED, MemberStatus.ACTIVE)
    with pytest.raises(DomainError) as failure:
        ensure_member_transition(MemberStatus.APPLICANT, MemberStatus.ACTIVE)
    assert failure.value.code == "MEMBER_STATUS_TRANSITION_INVALID"


def test_administrative_registry_transition_policies() -> None:
    ensure_cooperative_transition(CooperativeStatus.ACTIVE, CooperativeStatus.SUSPENDED)
    ensure_membership_transition(MembershipStatus.PENDING, MembershipStatus.ACTIVE)
    ensure_membership_transition(MembershipStatus.ACTIVE, MembershipStatus.ENDED)
    ensure_user_transition(UserStatus.ACTIVE, UserStatus.DISABLED)

    with pytest.raises(DomainError) as cooperative_failure:
        ensure_cooperative_transition(CooperativeStatus.ACTIVE, CooperativeStatus.ACTIVE)
    assert cooperative_failure.value.code == "COOPERATIVE_STATUS_TRANSITION_INVALID"

    with pytest.raises(DomainError) as membership_failure:
        ensure_membership_transition(MembershipStatus.ENDED, MembershipStatus.ACTIVE)
    assert membership_failure.value.code == "MEMBERSHIP_STATUS_TRANSITION_INVALID"

    with pytest.raises(DomainError) as user_failure:
        ensure_user_transition(UserStatus.DISABLED, UserStatus.DISABLED)
    assert user_failure.value.code == "USER_STATUS_TRANSITION_INVALID"