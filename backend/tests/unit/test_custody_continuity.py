from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest

from cooperative_clearing.modules.identity.domain.types import (
    Principal,
    RoleCode,
    RoleGrant,
    RoleGrantSource,
)
from cooperative_clearing.modules.inventory.application.custody_continuity import (
    REQUEST_ROLES,
    CustodyContinuityService,
    _evidence_refs,
    _lot_quantity,
    _reason,
    _temporary_expiry,
)
from cooperative_clearing.modules.inventory.infrastructure.models import (
    CustodyContinuityCase,
    CustodyContinuityItem,
    InventoryLot,
)
from cooperative_clearing.shared.domain.errors import DomainError


def principal(
    *,
    cooperative_id: UUID | None = None,
    role: RoleCode = RoleCode.COOPERATIVE_ADMIN,
    source: RoleGrantSource = RoleGrantSource.ASSIGNMENT,
    member: bool = True,
) -> Principal:
    return Principal(
        user_id=uuid4(),
        session_id=uuid4(),
        login="custody-test",
        member_id=uuid4() if member else None,
        must_change_password=False,
        roles=(
            RoleGrant(
                assignment_id=uuid4(),
                role=role,
                cooperative_id=cooperative_id,
                source=source,
            ),
        ),
    )


def test_normalizes_references_and_reason_codes() -> None:
    assert _evidence_refs(
        ["registry:notice-42", " registry:notice-42 ", "sha256:" + "a" * 64]
    ) == ("registry:notice-42", "sha256:" + "a" * 64)
    assert _reason(" independent_review ") == "INDEPENDENT_REVIEW"

    with pytest.raises(DomainError, match="CUSTODY_CONTINUITY_EVIDENCE_INVALID"):
        _evidence_refs([])
    with pytest.raises(DomainError, match="CUSTODY_CONTINUITY_REASON_INVALID"):
        _reason("not a reason")


def test_temporary_assignment_requires_bounded_utc_expiry() -> None:
    valid = datetime.now(UTC) + timedelta(days=2)
    assert _temporary_expiry(valid).tzinfo is UTC

    with pytest.raises(DomainError, match="CUSTODY_CONTINUITY_EXPIRY_INVALID"):
        _temporary_expiry(datetime.now())
    with pytest.raises(DomainError, match="CUSTODY_CONTINUITY_EXPIRY_INVALID"):
        _temporary_expiry(datetime.now(UTC) + timedelta(days=31))


def test_request_requires_personal_permanent_role() -> None:
    cooperative_id = uuid4()
    CustodyContinuityService._require_role(
        principal(cooperative_id=cooperative_id),
        REQUEST_ROLES,
        cooperative_id,
    )

    with pytest.raises(DomainError, match="PERMANENT_CUSTODY_CONTINUITY_ROLE_REQUIRED"):
        CustodyContinuityService._require_role(
            principal(
                cooperative_id=cooperative_id,
                source=RoleGrantSource.BREAK_GLASS,
            ),
            REQUEST_ROLES,
            cooperative_id,
        )
    with pytest.raises(DomainError, match="PERSONAL_ACTOR_REQUIRED"):
        CustodyContinuityService._require_role(
            principal(cooperative_id=cooperative_id, member=False),
            REQUEST_ROLES,
            cooperative_id,
        )


def test_lot_blockers_fail_closed_on_changed_custody_or_quantity() -> None:
    case_id = uuid4()
    source_assignment_id = uuid4()
    warehouse_id = uuid4()
    lot_id = uuid4()
    continuity_case = cast(
        CustodyContinuityCase,
        SimpleNamespace(
            id=case_id,
            source_assignment_id=source_assignment_id,
            warehouse_id=warehouse_id,
        ),
    )
    item = cast(
        CustodyContinuityItem,
        SimpleNamespace(
            lot_id=lot_id,
            lot_version=3,
            expected_quantity=Decimal("10.000000000000"),
        ),
    )
    lot = cast(
        InventoryLot,
        SimpleNamespace(
            id=lot_id,
            continuity_hold_case_id=case_id,
            custodian_assignment_id=source_assignment_id,
            warehouse_id=warehouse_id,
            current_quantity=Decimal("10"),
            declared_quantity=Decimal("10"),
            version=3,
        ),
    )
    assert CustodyContinuityService._lot_blockers(continuity_case, item, lot) == set()

    lot.custodian_assignment_id = uuid4()
    lot.current_quantity = Decimal("9")
    lot.continuity_hold_case_id = None
    assert CustodyContinuityService._lot_blockers(continuity_case, item, lot) == {
        "LOT_CUSTODIAN_CHANGED",
        "LOT_HOLD_CHANGED",
        "LOT_QUANTITY_CHANGED",
    }


def test_effective_lot_quantity_prefers_verified_balance() -> None:
    lot = cast(
        InventoryLot,
        SimpleNamespace(
            current_quantity=Decimal("4.2500"),
            declared_quantity=Decimal("5"),
        ),
    )
    assert _lot_quantity(lot) == Decimal("4.250000000000")
    lot.current_quantity = None
    assert _lot_quantity(lot) == Decimal("5.000000000000")
