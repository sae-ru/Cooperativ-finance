"""Role-scoped API for emergency physical custody continuity."""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError

from cooperative_clearing.api.auth import _request_uuid
from cooperative_clearing.api.custody_continuity_schemas import (
    CustodyContinuityAttestRequest,
    CustodyContinuityCandidateCollection,
    CustodyContinuityCandidateDecisionRequest,
    CustodyContinuityCandidateResponse,
    CustodyContinuityCaseCollection,
    CustodyContinuityCaseResponse,
    CustodyContinuityCommandEnvelope,
    CustodyContinuityCommandResponse,
    CustodyContinuityCreateRequest,
    CustodyContinuityDecisionRequest,
    CustodyContinuityItemResponse,
    CustodyContinuitySourceCollection,
    CustodyContinuitySourceResponse,
)
from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.modules.identity.application.security import require_step_up
from cooperative_clearing.modules.identity.domain.types import (
    MemberContinuityCaseStatus,
    MemberContinuityCaseType,
    MemberStatus,
    Principal,
    RoleCode,
    RoleGrantSource,
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    Member,
    MemberContinuityCase,
    Membership,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.inventory.application.custody_continuity import (
    READ_ROLES,
    REQUEST_ROLES,
    CustodyContinuityCommandResult,
    CustodyContinuityService,
)
from cooperative_clearing.modules.inventory.infrastructure.models import (
    CustodyContinuityCase,
    CustodyContinuityItem,
    InventoryLot,
    Product,
    UnitOfMeasure,
    Warehouse,
)
from cooperative_clearing.modules.responsibility.infrastructure.models import (
    ResponsibilityAssignment,
)
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/inventory", tags=["custody-continuity"])
IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=8, max_length=100)
]


def _scope(principal: Principal) -> set[UUID] | None:
    if principal.must_change_password:
        raise _denied()
    if any(
        grant.role in {RoleCode.SECURITY_ADMIN, RoleCode.AUDITOR}
        and grant.cooperative_id is None
        for grant in principal.roles
    ):
        return None
    scopes = {
        grant.cooperative_id
        for grant in principal.roles
        if grant.role in READ_ROLES and grant.cooperative_id is not None
    }
    if not scopes:
        raise _denied()
    return scopes


def _require_permanent_request_role(
    principal: Principal, cooperative_id: UUID
) -> None:
    if (
        principal.member_id is None
        or not principal.has_permanent_role(set(REQUEST_ROLES), cooperative_id)
    ):
        raise _denied()


@router.get(
    "/custody-continuity-cases",
    response_model=CustodyContinuityCaseCollection,
)
async def list_custody_continuity_cases(
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CustodyContinuityCaseCollection:
    scopes = _scope(principal)
    statement = select(CustodyContinuityCase).order_by(
        CustodyContinuityCase.created_at.desc(), CustodyContinuityCase.id
    )
    if scopes is not None:
        statement = statement.where(CustodyContinuityCase.cooperative_id.in_(scopes))
    async with database.session() as session:
        cases = list((await session.execute(statement)).scalars())
        if not cases:
            return CustodyContinuityCaseCollection(
                data=[], request_id=get_request_id()
            )
        case_ids = [item.id for item in cases]
        item_rows = list(
            (
                await session.execute(
                    select(
                        CustodyContinuityItem,
                        InventoryLot.lot_number,
                        Product.name,
                        UnitOfMeasure.symbol,
                    )
                    .join(InventoryLot, InventoryLot.id == CustodyContinuityItem.lot_id)
                    .join(Product, Product.id == InventoryLot.product_id)
                    .join(UnitOfMeasure, UnitOfMeasure.id == InventoryLot.unit_id)
                    .where(CustodyContinuityItem.case_id.in_(case_ids))
                    .order_by(
                        CustodyContinuityItem.case_id,
                        InventoryLot.lot_number,
                        CustodyContinuityItem.id,
                    )
                )
            ).all()
        )
        member_ids = {
            value
            for item in cases
            for value in (item.source_member_id, item.target_member_id)
        }
        members = {
            item.id: item.display_name
            for item in (
                await session.execute(select(Member).where(Member.id.in_(member_ids)))
            ).scalars()
        }
        warehouse_ids = {item.warehouse_id for item in cases}
        warehouses = {
            item.id: item.name
            for item in (
                await session.execute(
                    select(Warehouse).where(Warehouse.id.in_(warehouse_ids))
                )
            ).scalars()
        }
    items_by_case: dict[UUID, list[CustodyContinuityItemResponse]] = {
        item.id: [] for item in cases
    }
    for item, lot_number, product_name, unit_symbol in item_rows:
        items_by_case[item.case_id].append(
            CustodyContinuityItemResponse(
                id=item.id,
                lot_id=item.lot_id,
                lot_number=lot_number,
                product_name=product_name,
                unit_symbol=unit_symbol,
                lot_version=item.lot_version,
                expected_quantity=item.expected_quantity,
                actual_quantity=item.actual_quantity,
                status=item.status,
                condition_notes=item.condition_notes,
                evidence_ids=item.evidence_ids,
                attested_by_user_id=item.attested_by_user_id,
                attested_at=item.attested_at,
                version=item.version,
            )
        )
    return CustodyContinuityCaseCollection(
        data=[
            _case_response(
                item,
                source_member_name=members.get(item.source_member_id, str(item.source_member_id)),
                target_member_name=members.get(item.target_member_id, str(item.target_member_id)),
                warehouse_name=warehouses.get(item.warehouse_id, str(item.warehouse_id)),
                items=items_by_case[item.id],
            )
            for item in cases
        ],
        request_id=get_request_id(),
    )


@router.get(
    "/custody-continuity-sources",
    response_model=CustodyContinuitySourceCollection,
)
async def list_custody_continuity_sources(
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CustodyContinuitySourceCollection:
    scopes = _scope(principal)
    statement = (
        select(
            ResponsibilityAssignment,
            MemberContinuityCase,
            Member,
            Warehouse,
        )
        .join(
            MemberContinuityCase,
            MemberContinuityCase.member_id == ResponsibilityAssignment.member_id,
        )
        .join(Member, Member.id == ResponsibilityAssignment.member_id)
        .join(Warehouse, Warehouse.id == ResponsibilityAssignment.subject_id)
        .join(RoleAssignment, RoleAssignment.id == ResponsibilityAssignment.role_assignment_id)
        .where(
            ResponsibilityAssignment.status == "ACTIVE",
            ResponsibilityAssignment.subject_type == "warehouse",
            RoleAssignment.role_code == RoleCode.WAREHOUSE_CUSTODIAN.value,
            RoleAssignment.status == "ACTIVE",
            RoleAssignment.source == RoleGrantSource.ASSIGNMENT.value,
            MemberContinuityCase.case_type
            == MemberContinuityCaseType.DEATH_OR_INCAPACITY.value,
            MemberContinuityCase.status
            == MemberContinuityCaseStatus.CONFIRMED.value,
            Member.status == MemberStatus.SUCCESSION_REVIEW.value,
            ~exists(
                select(CustodyContinuityCase.id).where(
                    CustodyContinuityCase.source_assignment_id
                    == ResponsibilityAssignment.id,
                    CustodyContinuityCase.status.in_(
                        [
                            "INVENTORY_PENDING",
                            "PENDING_APPROVAL",
                            "PENDING_ACCEPTANCE",
                            "BLOCKED",
                        ]
                    ),
                )
            ),
        )
        .order_by(Warehouse.name, Member.display_name)
    )
    if scopes is not None:
        statement = statement.where(
            ResponsibilityAssignment.cooperative_id.in_(scopes)
        )
    async with database.session() as session:
        rows = list((await session.execute(statement)).all())
        assignment_ids = [assignment.id for assignment, _, _, _ in rows]
        lot_rows = (
            list(
                (
                    await session.execute(
                        select(
                            InventoryLot.custodian_assignment_id,
                            InventoryLot.id,
                        ).where(
                            InventoryLot.custodian_assignment_id.in_(assignment_ids)
                        )
                    )
                ).all()
            )
            if assignment_ids
            else []
        )
    counts: dict[UUID, int] = {}
    for assignment_id, _lot_id in lot_rows:
        counts[assignment_id] = counts.get(assignment_id, 0) + 1
    return CustodyContinuitySourceCollection(
        data=[
            CustodyContinuitySourceResponse(
                member_continuity_case_id=member_case.id,
                cooperative_id=assignment.cooperative_id,
                source_assignment_id=assignment.id,
                source_assignment_version=assignment.version,
                source_member_id=member.id,
                source_member_name=member.display_name,
                warehouse_id=warehouse.id,
                warehouse_name=warehouse.name,
                lot_count=counts.get(assignment.id, 0),
            )
            for assignment, member_case, member, warehouse in rows
            if counts.get(assignment.id, 0) > 0
        ],
        request_id=get_request_id(),
    )


@router.get(
    "/custody-continuity-candidates",
    response_model=CustodyContinuityCandidateCollection,
)
async def list_custody_continuity_candidates(
    cooperative_id: UUID,
    warehouse_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> CustodyContinuityCandidateCollection:
    _require_permanent_request_role(principal, cooperative_id)
    async with database.session() as session:
        rows = list(
            (
                await session.execute(
                    select(RoleAssignment, UserAccount, Member)
                    .join(UserAccount, UserAccount.id == RoleAssignment.user_id)
                    .join(Member, Member.id == UserAccount.member_id)
                    .join(
                        Membership,
                        (Membership.member_id == Member.id)
                        & (Membership.cooperative_id == cooperative_id),
                    )
                    .where(
                        RoleAssignment.role_code
                        == RoleCode.WAREHOUSE_CUSTODIAN.value,
                        RoleAssignment.cooperative_id == cooperative_id,
                        RoleAssignment.status == "ACTIVE",
                        RoleAssignment.source == RoleGrantSource.ASSIGNMENT.value,
                        UserAccount.status == "ACTIVE",
                        UserAccount.id != principal.user_id,
                        Member.status.in_(
                            [MemberStatus.ACTIVE.value, MemberStatus.LIMITED.value]
                        ),
                        Membership.status == "ACTIVE",
                        ~exists(
                            select(ResponsibilityAssignment.id).where(
                                ResponsibilityAssignment.cooperative_id
                                == cooperative_id,
                                ResponsibilityAssignment.member_id == Member.id,
                                ResponsibilityAssignment.subject_type == "warehouse",
                                ResponsibilityAssignment.subject_id == warehouse_id,
                                ResponsibilityAssignment.status.in_(
                                    [
                                        "PENDING_APPROVAL",
                                        "PENDING_ACCEPTANCE",
                                        "ACTIVE",
                                    ]
                                ),
                            )
                        ),
                    )
                    .order_by(Member.display_name, RoleAssignment.id)
                )
            ).all()
        )
    return CustodyContinuityCandidateCollection(
        data=[
            CustodyContinuityCandidateResponse(
                role_assignment_id=role.id,
                user_id=user.id,
                member_id=member.id,
                display_name=member.display_name,
            )
            for role, user, member in rows
        ],
        request_id=get_request_id(),
    )


@router.post(
    "/custody-continuity-cases",
    response_model=CustodyContinuityCommandEnvelope,
    status_code=201,
)
async def request_custody_continuity(
    payload: CustodyContinuityCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CustodyContinuityCommandEnvelope:
    async with database.session() as session:
        try:
            result = await CustodyContinuityService(settings).request_case(
                session,
                principal=principal,
                **payload.model_dump(),
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise _conflict() from exc
    return _command(result)


@router.post(
    "/custody-continuity-cases/{continuity_case_id}/items/{item_id}/attest",
    response_model=CustodyContinuityCommandEnvelope,
    status_code=201,
)
async def attest_custody_continuity_item(
    continuity_case_id: UUID,
    item_id: UUID,
    payload: CustodyContinuityAttestRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CustodyContinuityCommandEnvelope:
    async with database.session() as session:
        try:
            result = await CustodyContinuityService(settings).attest_item(
                session,
                principal=principal,
                continuity_case_id=continuity_case_id,
                item_id=item_id,
                **payload.model_dump(),
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise _conflict() from exc
    return _command(result)


@router.post(
    "/custody-continuity-cases/{continuity_case_id}/decision",
    response_model=CustodyContinuityCommandEnvelope,
    status_code=201,
)
async def decide_custody_continuity(
    continuity_case_id: UUID,
    payload: CustodyContinuityDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CustodyContinuityCommandEnvelope:
    async with database.session() as session:
        try:
            await require_step_up(
                session,
                principal,
                operation="CUSTODY_CONTINUITY_DECISION",
                request_id=_request_uuid(),
            )
            result = await CustodyContinuityService(settings).decide_case(
                session,
                principal=principal,
                continuity_case_id=continuity_case_id,
                **payload.model_dump(),
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise _conflict() from exc
    return _command(result)


@router.post(
    "/custody-continuity-cases/{continuity_case_id}/candidate-decision",
    response_model=CustodyContinuityCommandEnvelope,
    status_code=201,
)
async def decide_custody_continuity_candidate(
    continuity_case_id: UUID,
    payload: CustodyContinuityCandidateDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CustodyContinuityCommandEnvelope:
    async with database.session() as session:
        try:
            result = await CustodyContinuityService(settings).candidate_decision(
                session,
                principal=principal,
                continuity_case_id=continuity_case_id,
                **payload.model_dump(),
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise _conflict() from exc
    return _command(result)


def _case_response(
    item: CustodyContinuityCase,
    *,
    source_member_name: str,
    target_member_name: str,
    warehouse_name: str,
    items: list[CustodyContinuityItemResponse],
) -> CustodyContinuityCaseResponse:
    return CustodyContinuityCaseResponse(
        id=item.id,
        cooperative_id=item.cooperative_id,
        member_continuity_case_id=item.member_continuity_case_id,
        source_member_id=item.source_member_id,
        source_member_name=source_member_name,
        warehouse_id=item.warehouse_id,
        warehouse_name=warehouse_name,
        source_assignment_id=item.source_assignment_id,
        source_assignment_version=item.source_assignment_version,
        target_member_id=item.target_member_id,
        target_member_name=target_member_name,
        target_role_assignment_id=item.target_role_assignment_id,
        target_assignment_id=item.target_assignment_id,
        handover_place=item.handover_place,
        temporary_valid_until=item.temporary_valid_until,
        evidence_refs=item.evidence_refs,
        blocked_reasons=item.blocked_reasons,
        status=item.status,
        requested_by_user_id=item.requested_by_user_id,
        decided_by_user_id=item.decided_by_user_id,
        accepted_by_user_id=item.accepted_by_user_id,
        decision_reason_code=item.decision_reason_code,
        created_at=item.created_at,
        inventory_completed_at=item.inventory_completed_at,
        decided_at=item.decided_at,
        accepted_at=item.accepted_at,
        updated_at=item.updated_at,
        version=item.version,
        items=items,
    )


def _command(
    result: CustodyContinuityCommandResult,
) -> CustodyContinuityCommandEnvelope:
    data = CustodyContinuityCommandResponse(**asdict(result))
    return CustodyContinuityCommandEnvelope(data=data, request_id=get_request_id())


def _denied() -> DomainError:
    return DomainError(
        code="AUTHORIZATION_DENIED",
        message_key="errors.auth.authorization_denied",
        status_code=403,
    )


def _conflict() -> DomainError:
    return DomainError(
        code="CUSTODY_CONTINUITY_CONFLICT",
        message_key="errors.inventory.custody_continuity_conflict",
        status_code=409,
    )
