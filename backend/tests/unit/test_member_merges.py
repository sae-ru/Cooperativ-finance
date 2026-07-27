from uuid import uuid4

import pytest

from cooperative_clearing.modules.identity.application.member_merges import (
    has_member_merge_blockers,
    normalize_evidence_refs,
)
from cooperative_clearing.shared.domain.errors import DomainError


def test_evidence_references_are_trimmed_and_deduplicated() -> None:
    assert normalize_evidence_refs(
        [" case:duplicate-101 ", "case:duplicate-101", "sha256:abcdef123"]
    ) == ("case:duplicate-101", "sha256:abcdef123")


@pytest.mark.parametrize(
    "values",
    [
        [],
        ["raw person name"],
        [f"ref:{index}" for index in range(11)],
        [str(uuid4()), "contains@email"],
    ],
)
def test_evidence_references_reject_unsafe_values(values: list[str]) -> None:
    with pytest.raises(DomainError) as error:
        normalize_evidence_refs(values)
    assert error.value.code == "MEMBER_MERGE_EVIDENCE_INVALID"


def test_blocker_summary_is_explicit() -> None:
    assert not has_member_merge_blockers({"codes": [], "references": {}})
    assert has_member_merge_blockers({"codes": ["IDENTITY_ACCOUNT_CONFLICT"], "references": {}})
    assert has_member_merge_blockers(
        {"codes": [], "references": {"risk.share_accounts.member_id": 1}}
    )
