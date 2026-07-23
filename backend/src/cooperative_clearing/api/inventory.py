"""Role-scoped API for catalog, evidence, inventory, and custody."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.api.identity_schemas import CommandEnvelope
from cooperative_clearing.api.identity_schemas import CommandResult as ApiCommandResult
from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, require_role
from cooperative_clearing.modules.identity.infrastructure.models import (
    Member,
    Membership,
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    RoleAssignment as IdentityRoleAssignment,
)
from cooperative_clearing.modules.inventory.application.catalog import (
    CATALOG_WRITE_ROLES,
    CatalogService,
)
from cooperative_clearing.modules.inventory.application.common import InventoryCommandResult
from cooperative_clearing.modules.inventory.application.evidence import (
    EVIDENCE_ROLES,
    EvidenceService,
    evidence_roles,
)
from cooperative_clearing.modules.inventory.application.service import (
    ATTESTATION_ROLES,
    CUSTODY_ROLES,
    DISCREPANCY_ROLES,
    RECEIPT_ROLES,
    InventoryService,
)
from cooperative_clearing.modules.inventory.domain.types import QualityDecision
from cooperative_clearing.modules.inventory.infrastructure.models import (
    CustodyTransfer,
    EvidenceBlob,
    EvidenceLink,
    InventoryDiscrepancy,
    InventoryLot,
    Product,
    StockAttestation,
    UnitOfMeasure,
    Warehouse,
)
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.modules.responsibility.infrastructure.models import (
    ResponsibilityAssignment,
)
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1", tags=["inventory"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]

INVENTORY_READ_ROLES = (
    CATALOG_WRITE_ROLES
    | EVIDENCE_ROLES
    | {
        RoleCode.RISK_ADMIN,
        RoleCode.SECURITY_ADMIN,
        RoleCode.RIGHTS_OPERATOR,
    }
)
GLOBAL_INVENTORY_READ_ROLES = {RoleCode.AUDITOR, RoleCode.SECURITY_ADMIN}


class UnitCreateRequest(BaseModel):
    cooperative_id: UUID
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=2, max_length=120)
    symbol: str = Field(min_length=1, max_length=24)
    dimension: str = Field(min_length=1, max_length=40)
    decimal_scale: int = Field(ge=0, le=12)


class ProductCreateRequest(BaseModel):
    cooperative_id: UUID
    sku: str = Field(min_length=1, max_length=63, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=2000)
    default_unit_id: UUID
    quantity_tolerance: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    requires_evidence: bool = True
    shelf_life_required: bool = False


class WarehouseCreateRequest(BaseModel):
    cooperative_id: UUID
    code: str = Field(min_length=1, max_length=63, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=2, max_length=200)
    address_text: str = Field(min_length=2, max_length=500)
    storage_conditions: str = Field(min_length=2, max_length=500)


class EvidenceIntentRequest(BaseModel):
    cooperative_id: UUID
    expected_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    expected_size: int = Field(ge=1, le=26_214_400)
    mime_type: str = Field(min_length=3, max_length=100)
    kind: str = Field(min_length=2, max_length=40)
    original_name: str = Field(min_length=1, max_length=255)
    access_scope: str = Field(default="COOPERATIVE", min_length=2, max_length=40)
    retention_until: datetime | None = None


class LotRegisterRequest(BaseModel):
    cooperative_id: UUID
    lot_number: str = Field(min_length=1, max_length=100)
    product_id: UUID
    warehouse_id: UUID
    owner_member_id: UUID
    declared_quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    unit_id: UUID
    declared_quality: str = Field(min_length=1, max_length=200)
    expires_at: datetime | None = None
    storage_conditions: str = Field(min_length=2, max_length=500)
    custodian_assignment_id: UUID
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=20)


class LotAttestRequest(BaseModel):
    measured_quantity: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    quality_decision: QualityDecision
    verified_quality: str = Field(min_length=1, max_length=200)
    measurements: dict[str, str] = Field(min_length=1, max_length=30)
    notes: str = Field(min_length=2, max_length=1000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=20)
    expected_version: int = Field(ge=1)


class DiscrepancyRequest(BaseModel):
    actual_quantity: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    reason_code: str = Field(min_length=2, max_length=100)
    notes: str = Field(min_length=2, max_length=1000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_version: int = Field(ge=1)


class CustodyOfferRequest(BaseModel):
    to_warehouse_id: UUID
    to_assignment_id: UUID
    place: str = Field(min_length=2, max_length=500)
    notes: str = Field(min_length=2, max_length=1000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=20)
    expected_version: int = Field(ge=1)


class CustodyAcceptRequest(BaseModel):
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected_lot_version: int = Field(ge=1)


class UnitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    cooperative_id: UUID
    code: str
    name: str
    symbol: str
    dimension: str
    decimal_scale: int
    status: str
    created_event_id: UUID


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    cooperative_id: UUID
    sku: str
    name: str
    description: str
    default_unit_id: UUID
    quantity_tolerance: Decimal
    requires_evidence: bool
    shelf_life_required: bool
    status: str
    created_event_id: UUID


class WarehouseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    cooperative_id: UUID
    code: str
    name: str
    address_text: str
    storage_conditions: str
    status: str
    created_event_id: UUID


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    cooperative_id: UUID
    expected_sha256: str
    expected_size: int
    mime_type: str
    kind: str
    original_name: str
    access_scope: str
    retention_until: datetime | None
    status: str
    encryption_algorithm: str | None
    created_by_user_id: UUID
    created_event_id: UUID
    completed_event_id: UUID | None
    created_at: datetime
    ready_at: datetime | None


class LotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    cooperative_id: UUID
    lot_number: str
    product_id: UUID
    warehouse_id: UUID
    owner_member_id: UUID
    unit_id: UUID
    declared_quantity: Decimal
    current_quantity: Decimal | None
    declared_quality: str
    verified_quality: str | None
    expires_at: datetime | None
    storage_conditions: str
    status: str
    received_by_member_id: UUID
    custodian_assignment_id: UUID
    registered_event_id: UUID
    verified_event_id: UUID | None
    created_at: datetime
    updated_at: datetime
    version: int


class DiscrepancyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    lot_id: UUID
    expected_quantity: Decimal
    actual_quantity: Decimal
    variance: Decimal
    reason_code: str
    notes: str
    status: str
    recorded_by_user_id: UUID
    event_id: UUID
    created_at: datetime


class CustodyTransferResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    lot_id: UUID
    from_warehouse_id: UUID
    to_warehouse_id: UUID
    from_assignment_id: UUID
    to_assignment_id: UUID
    place: str
    notes: str
    status: str
    offered_by_user_id: UUID
    accepted_by_user_id: UUID | None
    offered_event_id: UUID
    accepted_event_id: UUID | None
    offered_at: datetime
    accepted_at: datetime | None


class InventoryMemberResponse(BaseModel):
    member_id: UUID
    cooperative_id: UUID
    display_name: str
    member_number: str


class InventoryCustodianResponse(BaseModel):
    assignment_id: UUID
    cooperative_id: UUID
    warehouse_id: UUID
    member_id: UUID
    user_id: UUID
    display_name: str
    role_code: str


class Collection[T](BaseModel):
    data: list[T]
    request_id: str


class HistoryEvent(BaseModel):
    event_id: UUID
    event_type: str
    aggregate_version: int
    occurred_at: datetime
    event_hash: str
    payload: dict[str, object]


class ReceiptActResponse(BaseModel):
    lot: LotResponse
    product: ProductResponse
    unit: UnitResponse
    warehouse: WarehouseResponse
    owner_name: str
    receiver_name: str
    attester_name: str | None
    custodian_name: str
    attestation: dict[str, object] | None
    evidence: list[EvidenceResponse]
    signed_events: list[HistoryEvent]
    generated_at: datetime


def _command(result: InventoryCommandResult) -> CommandEnvelope:
    return CommandEnvelope(
        data=ApiCommandResult(
            event_id=result.event_id, object_id=result.object_id, replayed=result.replayed
        ),
        request_id=get_request_id(),
    )


def _request_uuid() -> UUID | None:
    try:
        return UUID(get_request_id())
    except ValueError:
        return None


def _scoped_cooperatives(principal: Principal) -> set[UUID] | None:
    require_role(principal, INVENTORY_READ_ROLES)
    if any(
        grant.role in GLOBAL_INVENTORY_READ_ROLES and grant.cooperative_id is None
        for grant in principal.roles
    ):
        return None
    result = {
        grant.cooperative_id
        for grant in principal.roles
        if grant.role in INVENTORY_READ_ROLES and grant.cooperative_id is not None
    }
    if not result:
        raise DomainError(
            code="AUTHORIZATION_DENIED",
            message_key="errors.auth.authorization_denied",
            status_code=403,
        )
    return result


def _ensure_scope(principal: Principal, cooperative_id: UUID) -> None:
    scoped = _scoped_cooperatives(principal)
    if scoped is not None and cooperative_id not in scoped:
        raise DomainError(
            code="AUTHORIZATION_DENIED",
            message_key="errors.auth.authorization_denied",
            status_code=403,
        )


async def _commit_command(database: DatabaseDependency, action: object) -> CommandEnvelope:
    async with database.session() as session:
        try:
            result = await action(session)  # type: ignore[operator]
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise DomainError(
                code="INVENTORY_CONFLICT",
                message_key="errors.inventory.conflict",
                status_code=409,
            ) from exc
    return _command(result)


@router.get("/units", response_model=Collection[UnitResponse])
async def list_units(
    principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[UnitResponse]:
    scoped = _scoped_cooperatives(principal)
    statement = select(UnitOfMeasure).order_by(UnitOfMeasure.code, UnitOfMeasure.id)
    if scoped is not None:
        statement = statement.where(UnitOfMeasure.cooperative_id.in_(scoped))
    async with database.session() as session:
        items = list((await session.execute(statement)).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.post("/units", response_model=CommandEnvelope, status_code=201)
async def create_unit(
    payload: UnitCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, CATALOG_WRITE_ROLES, payload.cooperative_id)

    async def action(session: object) -> InventoryCommandResult:
        return await CatalogService(settings).create_unit(
            session,  # type: ignore[arg-type]
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.get("/products", response_model=Collection[ProductResponse])
async def list_products(
    principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[ProductResponse]:
    scoped = _scoped_cooperatives(principal)
    statement = select(Product).order_by(Product.name, Product.id)
    if scoped is not None:
        statement = statement.where(Product.cooperative_id.in_(scoped))
    async with database.session() as session:
        items = list((await session.execute(statement)).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.post("/products", response_model=CommandEnvelope, status_code=201)
async def create_product(
    payload: ProductCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, CATALOG_WRITE_ROLES, payload.cooperative_id)

    async def action(session: object) -> InventoryCommandResult:
        return await CatalogService(settings).create_product(
            session,  # type: ignore[arg-type]
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.get("/warehouses", response_model=Collection[WarehouseResponse])
async def list_warehouses(
    principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[WarehouseResponse]:
    scoped = _scoped_cooperatives(principal)
    statement = select(Warehouse).order_by(Warehouse.name, Warehouse.id)
    if scoped is not None:
        statement = statement.where(Warehouse.cooperative_id.in_(scoped))
    async with database.session() as session:
        items = list((await session.execute(statement)).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.post("/warehouses", response_model=CommandEnvelope, status_code=201)
async def create_warehouse(
    payload: WarehouseCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, CATALOG_WRITE_ROLES, payload.cooperative_id)

    async def action(session: object) -> InventoryCommandResult:
        return await CatalogService(settings).create_warehouse(
            session,  # type: ignore[arg-type]
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/evidence/upload-intents", response_model=CommandEnvelope, status_code=201)
async def create_evidence_intent(
    payload: EvidenceIntentRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, evidence_roles(payload.kind), payload.cooperative_id)

    async def action(session: object) -> InventoryCommandResult:
        return await EvidenceService(settings).create_intent(
            session,  # type: ignore[arg-type]
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.put("/evidence/upload-intents/{evidence_id}/content", response_model=CommandEnvelope)
async def upload_evidence_content(
    evidence_id: UUID,
    request: Request,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async with database.session() as session:
        result = await EvidenceService(settings).store_content(
            session,
            principal=principal,
            evidence_id=evidence_id,
            chunks=request.stream(),
            request_id=_request_uuid(),
        )
        await session.commit()
    return _command(result)


@router.get("/evidence/{evidence_id}/content")
async def download_evidence_content(
    evidence_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> Response:
    async with database.session() as session:
        evidence = await session.get(EvidenceBlob, evidence_id)
        if evidence is None:
            raise DomainError(
                code="EVIDENCE_NOT_FOUND",
                message_key="errors.inventory.evidence_not_found",
                status_code=404,
            )
        _ensure_scope(principal, evidence.cooperative_id)
        content = EvidenceService(settings).read_content(evidence)
        await AuditRepository(session).record(
            action="EVIDENCE_BLOB_READ",
            object_type="EvidenceBlob",
            object_id=evidence.id,
            cooperative_id=evidence.cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=_request_uuid(),
            payload={"sha256": evidence.expected_sha256},
        )
        await session.commit()
    download_name = quote(evidence.original_name, safe="")
    return Response(
        content=content,
        media_type=evidence.mime_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{download_name}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/inventory/lots", response_model=Collection[LotResponse])
async def list_lots(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    status: str | None = Query(default=None, max_length=32),
    warehouse_id: UUID | None = None,
) -> Collection[LotResponse]:
    scoped = _scoped_cooperatives(principal)
    statement = select(InventoryLot).order_by(InventoryLot.created_at.desc(), InventoryLot.id)
    if scoped is not None:
        statement = statement.where(InventoryLot.cooperative_id.in_(scoped))
    if status is not None:
        statement = statement.where(InventoryLot.status == status.upper())
    if warehouse_id is not None:
        statement = statement.where(InventoryLot.warehouse_id == warehouse_id)
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/inventory/members", response_model=Collection[InventoryMemberResponse])
async def list_inventory_members(
    principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[InventoryMemberResponse]:
    scoped = _scoped_cooperatives(principal)
    statement = (
        select(Member.id, Membership.cooperative_id, Member.display_name, Membership.member_number)
        .join(Membership, Membership.member_id == Member.id)
        .where(Member.status == "ACTIVE", Membership.status == "ACTIVE")
        .order_by(Member.display_name, Member.id)
    )
    if scoped is not None:
        statement = statement.where(Membership.cooperative_id.in_(scoped))
    async with database.session() as session:
        rows = (await session.execute(statement)).all()
    return Collection(
        data=[
            InventoryMemberResponse(
                member_id=row.id,
                cooperative_id=row.cooperative_id,
                display_name=row.display_name,
                member_number=row.member_number,
            )
            for row in rows
        ],
        request_id=get_request_id(),
    )


@router.get("/inventory/custodians", response_model=Collection[InventoryCustodianResponse])
async def list_inventory_custodians(
    principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[InventoryCustodianResponse]:
    scoped = _scoped_cooperatives(principal)
    statement = (
        select(
            ResponsibilityAssignment.id.label("assignment_id"),
            ResponsibilityAssignment.cooperative_id,
            ResponsibilityAssignment.subject_id.label("warehouse_id"),
            ResponsibilityAssignment.member_id,
            IdentityRoleAssignment.user_id,
            Member.display_name,
            IdentityRoleAssignment.role_code,
        )
        .join(Member, Member.id == ResponsibilityAssignment.member_id)
        .join(
            IdentityRoleAssignment,
            IdentityRoleAssignment.id == ResponsibilityAssignment.role_assignment_id,
        )
        .where(
            ResponsibilityAssignment.subject_type.in_(("warehouse", "warehouse_zone")),
            ResponsibilityAssignment.status == "ACTIVE",
            IdentityRoleAssignment.status == "ACTIVE",
            IdentityRoleAssignment.role_code == RoleCode.WAREHOUSE_CUSTODIAN.value,
        )
        .order_by(Member.display_name, ResponsibilityAssignment.id)
    )
    if scoped is not None:
        statement = statement.where(ResponsibilityAssignment.cooperative_id.in_(scoped))
    async with database.session() as session:
        rows = (await session.execute(statement)).all()
    return Collection(
        data=[InventoryCustodianResponse(**row._mapping) for row in rows],
        request_id=get_request_id(),
    )


@router.post("/inventory/lots", response_model=CommandEnvelope, status_code=201)
async def register_lot(
    payload: LotRegisterRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, RECEIPT_ROLES, payload.cooperative_id)

    async def action(session: object) -> InventoryCommandResult:
        return await InventoryService(settings).register_lot(
            session,  # type: ignore[arg-type]
            principal=principal,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/inventory/lots/{lot_id}/attest", response_model=CommandEnvelope)
async def attest_lot(
    lot_id: UUID,
    payload: LotAttestRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, ATTESTATION_ROLES)

    async def action(session: object) -> InventoryCommandResult:
        return await InventoryService(settings).attest_lot(
            session,  # type: ignore[arg-type]
            principal=principal,
            lot_id=lot_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/inventory/lots/{lot_id}/discrepancies", response_model=CommandEnvelope)
async def record_discrepancy(
    lot_id: UUID,
    payload: DiscrepancyRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, DISCREPANCY_ROLES)

    async def action(session: object) -> InventoryCommandResult:
        return await InventoryService(settings).record_discrepancy(
            session,  # type: ignore[arg-type]
            principal=principal,
            lot_id=lot_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.get("/inventory/discrepancies", response_model=Collection[DiscrepancyResponse])
async def list_discrepancies(
    principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[DiscrepancyResponse]:
    scoped = _scoped_cooperatives(principal)
    statement = (
        select(InventoryDiscrepancy)
        .join(InventoryLot, InventoryLot.id == InventoryDiscrepancy.lot_id)
        .order_by(InventoryDiscrepancy.created_at.desc())
    )
    if scoped is not None:
        statement = statement.where(InventoryLot.cooperative_id.in_(scoped))
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.post("/inventory/lots/{lot_id}/custody-transfers", response_model=CommandEnvelope)
async def offer_custody(
    lot_id: UUID,
    payload: CustodyOfferRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, CUSTODY_ROLES)

    async def action(session: object) -> InventoryCommandResult:
        return await InventoryService(settings).offer_custody(
            session,  # type: ignore[arg-type]
            principal=principal,
            lot_id=lot_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.post("/inventory/custody-transfers/{transfer_id}/accept", response_model=CommandEnvelope)
async def accept_custody(
    transfer_id: UUID,
    payload: CustodyAcceptRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, CUSTODY_ROLES)

    async def action(session: object) -> InventoryCommandResult:
        return await InventoryService(settings).accept_custody(
            session,  # type: ignore[arg-type]
            principal=principal,
            transfer_id=transfer_id,
            **payload.model_dump(),
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )

    return await _commit_command(database, action)


@router.get("/inventory/custody-transfers", response_model=Collection[CustodyTransferResponse])
async def list_custody_transfers(
    principal: PrincipalDependency, database: DatabaseDependency
) -> Collection[CustodyTransferResponse]:
    scoped = _scoped_cooperatives(principal)
    statement = (
        select(CustodyTransfer)
        .join(InventoryLot, InventoryLot.id == CustodyTransfer.lot_id)
        .order_by(CustodyTransfer.offered_at.desc())
    )
    if scoped is not None:
        statement = statement.where(InventoryLot.cooperative_id.in_(scoped))
    async with database.session() as session:
        items = list((await session.execute(statement.limit(500))).scalars())
    return Collection(data=items, request_id=get_request_id())


@router.get("/inventory/lots/{lot_id}/history", response_model=Collection[HistoryEvent])
async def lot_history(
    lot_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> Collection[HistoryEvent]:
    async with database.session() as session:
        lot = await session.get(InventoryLot, lot_id)
        if lot is None:
            raise DomainError(
                code="LOT_NOT_FOUND", message_key="errors.inventory.lot_not_found", status_code=404
            )
        _ensure_scope(principal, lot.cooperative_id)
        events = list(
            (
                await session.execute(
                    select(SignedEvent)
                    .where(
                        SignedEvent.aggregate_type == "inventory_lot",
                        SignedEvent.aggregate_id == lot.id,
                    )
                    .order_by(SignedEvent.aggregate_version)
                )
            ).scalars()
        )
    return Collection(
        data=[
            HistoryEvent(
                event_id=item.event_id,
                event_type=item.event_type,
                aggregate_version=item.aggregate_version,
                occurred_at=item.occurred_at,
                event_hash=item.event_hash,
                payload=item.payload,
            )
            for item in events
        ],
        request_id=get_request_id(),
    )


@router.get("/inventory/lots/{lot_id}/receipt-act", response_model=ReceiptActResponse)
async def receipt_act(
    lot_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> ReceiptActResponse:
    async with database.session() as session:
        lot = await session.get(InventoryLot, lot_id)
        if lot is None:
            raise DomainError(
                code="LOT_NOT_FOUND", message_key="errors.inventory.lot_not_found", status_code=404
            )
        _ensure_scope(principal, lot.cooperative_id)
        product = await session.get(Product, lot.product_id)
        unit = await session.get(UnitOfMeasure, lot.unit_id)
        warehouse = await session.get(Warehouse, lot.warehouse_id)
        owner = await session.get(Member, lot.owner_member_id)
        receiver = await session.get(Member, lot.received_by_member_id)
        custody = await session.get(ResponsibilityAssignment, lot.custodian_assignment_id)
        custodian = await session.get(Member, custody.member_id) if custody else None
        attestation = (
            await session.execute(select(StockAttestation).where(StockAttestation.lot_id == lot.id))
        ).scalar_one_or_none()
        attester = (
            await session.get(Member, attestation.attested_by_member_id) if attestation else None
        )
        events = list(
            (
                await session.execute(
                    select(SignedEvent)
                    .where(
                        SignedEvent.aggregate_type == "inventory_lot",
                        SignedEvent.aggregate_id == lot.id,
                    )
                    .order_by(SignedEvent.aggregate_version)
                )
            ).scalars()
        )
        event_ids = [item.event_id for item in events]
        evidence = list(
            (
                await session.execute(
                    select(EvidenceBlob)
                    .join(EvidenceLink, EvidenceLink.evidence_id == EvidenceBlob.id)
                    .where(EvidenceLink.event_id.in_(event_ids))
                    .distinct()
                    .order_by(EvidenceBlob.created_at)
                )
            ).scalars()
        )
    if not all((product, unit, warehouse, owner, receiver, custodian)):
        raise DomainError(
            code="RECEIPT_ACT_DATA_INCOMPLETE",
            message_key="errors.inventory.receipt_act_data_incomplete",
            status_code=500,
        )
    assert product is not None
    assert unit is not None
    assert warehouse is not None
    assert owner is not None
    assert receiver is not None
    assert custodian is not None
    return ReceiptActResponse(
        lot=LotResponse.model_validate(lot),
        product=ProductResponse.model_validate(product),
        unit=UnitResponse.model_validate(unit),
        warehouse=WarehouseResponse.model_validate(warehouse),
        owner_name=owner.display_name,
        receiver_name=receiver.display_name,
        attester_name=attester.display_name if attester else None,
        custodian_name=custodian.display_name,
        attestation=(
            {
                "id": str(attestation.id),
                "measured_quantity": str(attestation.measured_quantity),
                "variance": str(attestation.variance),
                "quantity_decision": attestation.quantity_decision,
                "quality_decision": attestation.quality_decision,
                "verified_quality": attestation.verified_quality,
                "measurements": attestation.measurements,
                "notes": attestation.notes,
                "event_id": str(attestation.event_id),
                "attested_at": attestation.attested_at.isoformat(),
            }
            if attestation
            else None
        ),
        evidence=[EvidenceResponse.model_validate(item) for item in evidence],
        signed_events=[
            HistoryEvent(
                event_id=item.event_id,
                event_type=item.event_type,
                aggregate_version=item.aggregate_version,
                occurred_at=item.occurred_at,
                event_hash=item.event_hash,
                payload=item.payload,
            )
            for item in events
        ],
        generated_at=datetime.now(UTC),
    )
