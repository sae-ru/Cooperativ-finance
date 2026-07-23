from cooperative_clearing.modules.trust.application.enforcement import (
    GUARANTEE_CREATE,
    ROLE_ASSIGNMENT_CREATE,
    restriction_applies,
)


def test_restriction_scope_blocks_only_declared_actions_and_roles() -> None:
    assert restriction_applies(
        restriction_type="BLOCK_NEW_GUARANTEES",
        scope={},
        action=GUARANTEE_CREATE,
    )
    assert restriction_applies(
        restriction_type="LIMIT_SCOPE",
        scope={"blocked_actions": ["RISK_COMMITMENT_CREATE"]},
        action="RISK_COMMITMENT_CREATE",
    )
    assert restriction_applies(
        restriction_type="SUSPEND_ROLE",
        scope={"role_codes": ["RISK_ADMIN"]},
        action=ROLE_ASSIGNMENT_CREATE,
        target_role="RISK_ADMIN",
    )
    assert not restriction_applies(
        restriction_type="SUSPEND_ROLE",
        scope={"role_codes": ["RISK_ADMIN"]},
        action=ROLE_ASSIGNMENT_CREATE,
        target_role="DATA_STEWARD",
    )
    assert not restriction_applies(
        restriction_type="ADDITIONAL_REVIEW",
        scope={"blocked_actions": []},
        action=GUARANTEE_CREATE,
    )
