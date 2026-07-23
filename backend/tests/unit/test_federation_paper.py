from datetime import UTC, datetime, timedelta
from typing import TypedDict
from uuid import UUID

import pytest

from cooperative_clearing.modules.federation.application.paper import (
    bounded_reference,
    paper_form_checksum,
)
from cooperative_clearing.shared.domain.errors import DomainError


class PaperChecksumArguments(TypedDict):
    node_id: UUID
    epoch_id: UUID
    serial_number: str
    form_type: str
    form_version: int
    participant_refs: list[str]
    operation_constraints: dict[str, object]
    issued_at: datetime
    expires_at: datetime


def test_paper_checksum_is_deterministic_and_binds_terms() -> None:
    issued = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    arguments: PaperChecksumArguments = {
        "node_id": UUID("00000000-0000-0000-0000-000000000001"),
        "epoch_id": UUID("00000000-0000-0000-0000-000000000002"),
        "serial_number": "PAPER-001",
        "form_type": "GOODS_TRANSFER",
        "form_version": 1,
        "participant_refs": ["MEMBER-1", "MEMBER-2"],
        "operation_constraints": {"maximum_value": "25", "unit": "UNIT"},
        "issued_at": issued,
        "expires_at": issued + timedelta(hours=2),
    }
    checksum = paper_form_checksum(**arguments)

    assert checksum == paper_form_checksum(**arguments)
    assert checksum.startswith("sha256:")
    changed_arguments = arguments.copy()
    changed_arguments["form_version"] = 2
    assert checksum != paper_form_checksum(**changed_arguments)


@pytest.mark.parametrize("value", ["", " ", "x" * 161, "member\nadmin"])
def test_paper_references_reject_empty_oversized_and_control_text(value: str) -> None:
    with pytest.raises(DomainError) as error:
        bounded_reference(value)
    assert error.value.code == "PAPER_REFERENCE_INVALID"
