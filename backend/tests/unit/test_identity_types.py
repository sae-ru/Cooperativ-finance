from uuid import uuid4

import pytest

from cooperative_clearing.modules.identity.domain.types import (
    MemberStatus,
    Principal,
    RoleCode,
    RoleGrant,
    ensure_member_transition,
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
