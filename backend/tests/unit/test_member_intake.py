import pytest

from cooperative_clearing.modules.identity.application.intake import (
    normalize_member_name,
    parse_member_import_csv,
)
from cooperative_clearing.shared.domain.errors import DomainError


def test_member_import_csv_accepts_localized_headers_and_semicolon_delimiter() -> None:
    rows = parse_member_import_csv(
        "имя_участника;тип_идентификатора;идентификатор\n"
        "Мария Федорова;EXTERNAL_REFERENCE;farm-42\n"
    )

    assert len(rows) == 1
    assert rows[0].display_name == "Мария Федорова"
    assert rows[0].identifier_type == "EXTERNAL_REFERENCE"
    assert rows[0].identifier_value == "farm-42"

def test_member_import_csv_rejects_unknown_columns_and_row_overflow() -> None:
    with pytest.raises(DomainError) as invalid_header:
        parse_member_import_csv("display_name,private_note\nPerson,secret\n")
    assert invalid_header.value.code == "MEMBER_IMPORT_HEADER_INVALID"

    rows = ["display_name", *(f"Member {index}" for index in range(501))]
    with pytest.raises(DomainError) as overflow:
        parse_member_import_csv("\n".join(rows))
    assert overflow.value.code == "MEMBER_IMPORT_ROW_LIMIT_EXCEEDED"


def test_member_name_normalization_is_case_and_whitespace_stable() -> None:
    assert normalize_member_name("  Anna   PETROVA ") == "anna petrova"