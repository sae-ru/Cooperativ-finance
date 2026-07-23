from cooperative_clearing.modules.identity.domain.types import RoleCode
from cooperative_clearing.modules.inventory.application.evidence import (
    EVIDENCE_ROLES,
    evidence_roles,
)


def test_solidarity_evidence_accepts_any_participant_assignment() -> None:
    assert evidence_roles("solidarity_aid") == set(RoleCode)


def test_other_evidence_keeps_restricted_roles() -> None:
    assert evidence_roles("RECEIPT") is EVIDENCE_ROLES
    assert RoleCode.CLEARING_OPERATOR not in evidence_roles("RECEIPT")
