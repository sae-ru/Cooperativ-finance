"""External node onboarding, liability, offline epochs, and signed package API."""

import base64
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.api.identity_schemas import CommandEnvelope
from cooperative_clearing.api.identity_schemas import CommandResult as ApiCommandResult
from cooperative_clearing.modules.federation.application.common import FederationCommandResult
from cooperative_clearing.modules.federation.application.lifecycle import NodeTrustService
from cooperative_clearing.modules.federation.application.paper import PaperFormService
from cooperative_clearing.modules.federation.application.service import (
    ChallengeMaterial,
    FederationService,
    ResponsiblePartyInput,
)
from cooperative_clearing.modules.federation.application.sync import (
    PackageArchiveResult,
    SyncService,
)
from cooperative_clearing.modules.federation.domain.types import (
    ConflictDecision,
    NodeCapability,
    ResponsibleRole,
    TrustLevel,
    federation_error,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    FederationCheckpoint,
    FederationPaperForm,
    InboxEvent,
    NodeApplication,
    NodeBilateralLimit,
    NodeBond,
    NodeCertificate,
    NodeChallenge,
    NodeExposure,
    NodeKeyRotationRequest,
    NodeOwnerOrganization,
    NodeResponsibleParty,
    NodeSecurityIncident,
    NodeTrustContract,
    OfflineEpoch,
    SyncConflict,
    SyncPackage,
    SyncReceipt,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, require_role
from cooperative_clearing.shared.core.request_context import get_request_id

router = APIRouter(prefix="/api/v1/federation", tags=["external-nodes-and-offline-sync"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]
READ_ROLES = {
    RoleCode.NODE_REGISTRAR,
    RoleCode.NODE_TECHNICAL_CUSTODIAN,
    RoleCode.NODE_SECURITY_ADMIN,
    RoleCode.NODE_BUSINESS_OPERATOR,
    RoleCode.NODE_AUDITOR,
    RoleCode.SECURITY_ADMIN,
    RoleCode.AUDITOR,
}


class ObjectCollection(BaseModel):
    data: list[dict[str, Any]]
    request_id: str


class ObjectEnvelope(BaseModel):
    data: dict[str, Any]
    request_id: str


class VersionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class ResponsiblePartyRequest(BaseModel):
    member_id: UUID
    role_assignment_id: UUID
    role_code: ResponsibleRole
    capability_scope: tuple[NodeCapability, ...] = Field(min_length=1)
    responsibility_scope: str = Field(min_length=2, max_length=2000)
    max_exposure: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    exposure_unit: str = Field(min_length=1, max_length=32)
    valid_until: datetime | None = None


class NodeApplicationCreateRequest(BaseModel):
    node_code: str = Field(min_length=3, max_length=63)
    display_name: str = Field(min_length=2, max_length=200)
    owner_legal_name: str = Field(min_length=2, max_length=240)
    owner_registration_code: str = Field(min_length=1, max_length=120)
    owner_jurisdiction: str = Field(min_length=2, max_length=120)
    owner_contact_payload: dict[str, object]
    territory: str = Field(min_length=2, max_length=160)
    purpose: str = Field(min_length=2, max_length=4000)
    network_endpoints: list[object] = Field(default_factory=list, max_length=20)
    hardware_manifest: dict[str, object]
    release_manifest: dict[str, object]
    capabilities: tuple[NodeCapability, ...] = Field(min_length=1)
    supported_protocols: list[str] = Field(min_length=1, max_length=20)
    supported_policies: dict[str, int]
    data_scopes: dict[str, object]
    requested_limits: dict[str, object]
    recovery_contacts: list[object] = Field(min_length=1, max_length=20)
    security_questionnaire: dict[str, object]
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)
    responsible_parties: tuple[ResponsiblePartyRequest, ...] = Field(min_length=5, max_length=30)
    public_key_base64: str = Field(min_length=40, max_length=100)
    certificate_valid_from: datetime
    certificate_valid_until: datetime
    proposed_trust_expiry: datetime


class IdentityVerificationRequest(VersionRequest):
    verification_summary: str = Field(min_length=2, max_length=4000)


class ChallengeIssueRequest(VersionRequest):
    protocol_version: str = Field(min_length=1, max_length=32)


class ChallengeResponseRequest(BaseModel):
    nonce: str = Field(min_length=20, max_length=200)
    response_payload: dict[str, object]
    signature_base64: str = Field(min_length=80, max_length=200)


class AuditDecisionRequest(VersionRequest):
    approve: bool
    rationale: str = Field(min_length=2, max_length=4000)


class TrustContractRequest(BaseModel):
    application_id: UUID
    contract_number: str = Field(min_length=2, max_length=80)
    trust_level: TrustLevel
    capabilities: tuple[NodeCapability, ...] = Field(min_length=1)
    event_types: list[str] = Field(min_length=1, max_length=200)
    inbound_scope: dict[str, object]
    outbound_scope: dict[str, object]
    federation_limits: dict[str, object]
    allowed_counterparties: list[str] = Field(default_factory=list, max_length=200)
    max_offline_hours: int = Field(ge=1, le=720)
    required_protocols: list[str] = Field(min_length=1, max_length=20)
    required_policies: dict[str, int]
    service_levels: dict[str, object]
    liability_terms: dict[str, object]
    valid_from: datetime
    valid_until: datetime


class HashApprovalRequest(VersionRequest):
    terms_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class BilateralLimitRequest(BaseModel):
    capability: NodeCapability
    unit: str = Field(min_length=1, max_length=32)
    max_package_value: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    max_unsettled_obligations: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    max_external_rights: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    max_clearing_position: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    max_offline_hours: int = Field(ge=1, le=720)
    allowed_critical_resources: list[str] = Field(default_factory=list, max_length=200)
    required_confirmations: int = Field(ge=1, le=10)


class NodeBondRequest(BaseModel):
    reference: str = Field(min_length=2, max_length=160)
    amount: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    protected_amount: Decimal = Field(ge=0, max_digits=38, decimal_places=12)
    maximum_loss: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    unit: str = Field(min_length=1, max_length=32)
    capability_scope: tuple[NodeCapability, ...] = Field(min_length=1)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)
    valid_from: datetime
    valid_until: datetime


class NodeStatusRequest(VersionRequest):
    rationale: str = Field(min_length=2, max_length=4000)


class IncidentRequest(BaseModel):
    incident_type: str = Field(min_length=2, max_length=80)
    severity: str = Field(pattern=r"^(LOW|MEDIUM|HIGH|CRITICAL)$")
    earliest_compromise_at: datetime | None = None
    description: str = Field(min_length=2, max_length=8000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)


class IncidentResolutionRequest(VersionRequest):
    corrective_actions: list[object] = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=2, max_length=4000)


class KeyRotationRequest(BaseModel):
    new_public_key_base64: str = Field(min_length=40, max_length=100)
    valid_from: datetime
    valid_until: datetime
    reason: str = Field(pattern=r"^(SCHEDULED|COMPROMISE|CUSTODY_CHANGE|RECOVERY)$")
    old_signature_base64: str | None = Field(default=None, min_length=80, max_length=200)
    new_signature_base64: str = Field(min_length=80, max_length=200)


class KeyRotationDecisionRequest(VersionRequest):
    approve: bool


class RehabilitationRequest(VersionRequest):
    integrity_summary: dict[str, object]


class OfflineEpochRequest(BaseModel):
    base_checkpoint_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    allowed_event_types: list[str] = Field(min_length=1, max_length=500)
    limits: dict[str, object]
    protocol_version: str = Field(min_length=1, max_length=32)
    policy_versions: dict[str, int]
    emergency_contacts: list[object] = Field(min_length=1, max_length=20)
    closure_rules: dict[str, object]
    starts_at: datetime
    expires_at: datetime


class EpochCloseRequest(VersionRequest):
    reconciliation: dict[str, object]


class PaperFormIssueRequest(BaseModel):
    serial_number: str = Field(min_length=1, max_length=64)
    form_type: str = Field(
        pattern=r"^(GOODS_TRANSFER|LOGISTICS_HANDOFF|SERVICE_ACCEPTANCE|EMERGENCY_NODE_ACTION|EXCEPTION)$"
    )
    form_version: int = Field(default=1, ge=1, le=100)
    participant_refs: list[str] = Field(min_length=1, max_length=50)
    operation_constraints: dict[str, object]
    expires_at: datetime


class PaperFormRecordRequest(VersionRequest):
    checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    operation_payload: dict[str, object]
    signatures: list[object] = Field(min_length=1, max_length=20)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)


class PaperFormVoidRequest(VersionRequest):
    rationale: str = Field(min_length=2, max_length=4000)


class ExposureReserveRequest(BaseModel):
    capability: NodeCapability
    unit: str = Field(min_length=1, max_length=32)
    delta: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    reference: str = Field(min_length=2, max_length=160)


class ExportPackageRequest(BaseModel):
    peer_node_id: UUID
    sequence_after: int = Field(ge=0)
    maximum_events: int = Field(default=1000, ge=1, le=10_000)
    expiry_hours: int = Field(default=24, ge=1, le=168)
    epoch_id: UUID | None = None


class PackageApplyRequest(VersionRequest):
    manifest_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ConflictDecisionRequest(VersionRequest):
    decision: ConflictDecision
    rationale: str = Field(min_length=2, max_length=4000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)


CommandAction = Callable[[AsyncSession], Awaitable[FederationCommandResult]]


def _request_uuid() -> UUID | None:
    try:
        return UUID(get_request_id())
    except ValueError:
        return None


def _command(result: FederationCommandResult) -> CommandEnvelope:
    return CommandEnvelope(
        data=ApiCommandResult(
            event_id=result.event_id,
            object_id=result.object_id,
            replayed=result.replayed,
        ),
        request_id=get_request_id(),
    )


async def _commit(database: DatabaseDependency, action: CommandAction) -> CommandEnvelope:
    async with database.session() as session:
        try:
            result = await action(session)
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise federation_error("CONFLICT") from exc
    return _command(result)


def _read(principal: Principal) -> None:
    require_role(principal, READ_ROLES)


def _collection(items: list[dict[str, Any]]) -> ObjectCollection:
    return ObjectCollection(data=items, request_id=get_request_id())


def _base(item: Any, *names: str) -> dict[str, Any]:
    return {name: getattr(item, name) for name in names}


def _node(item: ExternalNode) -> dict[str, Any]:
    return _base(
        item,
        "id",
        "node_code",
        "display_name",
        "owner_organization_id",
        "sponsor_local_node_id",
        "territory",
        "purpose",
        "status",
        "trust_level",
        "network_endpoints",
        "hardware_manifest",
        "release_manifest",
        "capabilities",
        "supported_protocols",
        "supported_policies",
        "data_scopes",
        "last_sync_at",
        "last_checkpoint_hash",
        "created_at",
        "updated_at",
        "version",
    )


def _application(item: NodeApplication) -> dict[str, Any]:
    return _base(
        item,
        "id",
        "node_id",
        "status",
        "requested_capabilities",
        "requested_limits",
        "requested_data_scopes",
        "recovery_contacts",
        "evidence_ids",
        "proposed_trust_expiry",
        "created_by_user_id",
        "identity_verified_by_user_id",
        "audit_decided_by_user_id",
        "created_at",
        "submitted_at",
        "identity_verified_at",
        "audit_decided_at",
        "version",
    )


def _responsibility(item: NodeResponsibleParty) -> dict[str, Any]:
    return _base(
        item,
        "id",
        "node_id",
        "application_id",
        "member_id",
        "role_assignment_id",
        "role_code",
        "capability_scope",
        "responsibility_scope",
        "max_exposure",
        "exposure_unit",
        "valid_from",
        "valid_until",
        "status",
        "accepted_by_user_id",
        "accepted_at",
        "created_at",
    )


def _certificate(item: NodeCertificate) -> dict[str, Any]:
    result = _base(
        item,
        "id",
        "node_id",
        "algorithm",
        "fingerprint",
        "status",
        "valid_from",
        "valid_until",
        "created_at",
        "revoked_at",
    )
    result["public_key_base64"] = base64.b64encode(item.public_key).decode()
    return result


def _challenge(item: NodeChallenge) -> dict[str, Any]:
    return _base(
        item,
        "id",
        "application_id",
        "certificate_id",
        "nonce_hash",
        "challenge_payload",
        "protocol_version",
        "status",
        "issued_by_user_id",
        "issued_at",
        "expires_at",
        "responded_at",
        "response_payload",
    )


def _contract(item: NodeTrustContract) -> dict[str, Any]:
    return _base(
        item,
        "id",
        "node_id",
        "application_id",
        "contract_number",
        "trust_level",
        "capabilities",
        "event_types",
        "inbound_scope",
        "outbound_scope",
        "federation_limits",
        "allowed_counterparties",
        "max_offline_hours",
        "required_protocols",
        "required_policies",
        "service_levels",
        "liability_terms",
        "terms_hash",
        "status",
        "valid_from",
        "valid_until",
        "proposed_by_user_id",
        "approved_by_user_id",
        "created_at",
        "approved_at",
        "revoked_at",
        "version",
    )


def _limit(item: NodeBilateralLimit) -> dict[str, Any]:
    return _base(
        item,
        "id",
        "node_id",
        "capability",
        "unit",
        "max_package_value",
        "max_unsettled_obligations",
        "max_external_rights",
        "max_clearing_position",
        "max_offline_hours",
        "allowed_critical_resources",
        "required_confirmations",
        "terms_hash",
        "status",
        "proposed_by_user_id",
        "approved_by_user_id",
        "created_at",
        "approved_at",
        "version",
    )


def _bond(item: NodeBond) -> dict[str, Any]:
    return _base(
        item,
        "id",
        "node_id",
        "provider_organization_id",
        "reference",
        "amount",
        "protected_amount",
        "maximum_loss",
        "unit",
        "capability_scope",
        "evidence_ids",
        "status",
        "valid_from",
        "valid_until",
        "created_at",
    )


def _epoch(item: OfflineEpoch) -> dict[str, Any]:
    return _base(
        item,
        "id",
        "local_node_id",
        "external_node_id",
        "base_checkpoint_hash",
        "allowed_event_types",
        "limits",
        "protocol_version",
        "policy_versions",
        "emergency_contacts",
        "closure_rules",
        "policy_hash",
        "status",
        "starts_at",
        "expires_at",
        "opened_by_user_id",
        "created_at",
        "closed_at",
        "version",
    )


def _paper_form(item: FederationPaperForm) -> dict[str, Any]:
    return _base(
        item,
        "id",
        "external_node_id",
        "epoch_id",
        "serial_number",
        "qr_reference",
        "checksum",
        "form_type",
        "form_version",
        "participant_refs",
        "operation_constraints",
        "status",
        "issued_at",
        "expires_at",
        "payload",
        "payload_hash",
        "signatures",
        "evidence_ids",
        "issued_by_user_id",
        "issued_by_member_id",
        "recorded_by_user_id",
        "recorded_by_member_id",
        "recorded_at",
        "voided_by_user_id",
        "voided_by_member_id",
        "voided_at",
        "void_reason",
        "version",
    )


def _package(item: SyncPackage) -> dict[str, Any]:
    return _base(
        item,
        "id",
        "peer_node_id",
        "epoch_id",
        "direction",
        "status",
        "source_node_code",
        "target_node_code",
        "protocol_version",
        "required_capabilities",
        "sequence_first",
        "sequence_last",
        "base_checkpoint_hash",
        "event_count",
        "blob_count",
        "archive_size",
        "archive_hash",
        "manifest_hash",
        "simulation_summary",
        "rejection_code",
        "created_by_user_id",
        "applied_by_user_id",
        "created_at",
        "expires_at",
        "verified_at",
        "simulated_at",
        "applied_at",
        "version",
    )


def _conflict(item: SyncConflict) -> dict[str, Any]:
    return _base(
        item,
        "id",
        "package_id",
        "inbox_event_id",
        "conflict_class",
        "affected_object_type",
        "affected_object_id",
        "local_event_id",
        "remote_event_id",
        "local_event_hash",
        "remote_event_hash",
        "maximum_exposure",
        "exposure_unit",
        "freeze_payload",
        "evidence_ids",
        "status",
        "decision",
        "rationale",
        "decided_by_user_id",
        "created_at",
        "decided_at",
        "version",
    )


async def _list(
    principal: Principal,
    database: DatabaseDependency,
    model: Any,
    mapper: Callable[[Any], dict[str, Any]],
    *,
    limit: int,
) -> ObjectCollection:
    _read(principal)
    async with database.session() as session:
        rows = list((await session.execute(select(model).limit(limit))).scalars())
    return _collection([mapper(item) for item in rows])


@router.get("/organizations", response_model=ObjectCollection)
async def list_organizations(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=200, ge=1, le=500),
) -> ObjectCollection:
    return await _list(
        principal,
        database,
        NodeOwnerOrganization,
        lambda item: _base(
            item,
            "id",
            "legal_name",
            "registration_code",
            "jurisdiction",
            "contact_payload",
            "status",
            "created_at",
            "updated_at",
            "version",
        ),
        limit=limit,
    )


@router.get("/nodes", response_model=ObjectCollection)
async def list_nodes(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=200, ge=1, le=500),
) -> ObjectCollection:
    return await _list(principal, database, ExternalNode, _node, limit=limit)


@router.get("/nodes/applications", response_model=ObjectCollection)
async def list_applications(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=200, ge=1, le=500),
) -> ObjectCollection:
    return await _list(principal, database, NodeApplication, _application, limit=limit)


@router.get("/responsibilities", response_model=ObjectCollection)
async def list_responsibilities(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=500, ge=1, le=1000),
) -> ObjectCollection:
    return await _list(principal, database, NodeResponsibleParty, _responsibility, limit=limit)


@router.get("/certificates", response_model=ObjectCollection)
async def list_certificates(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=500, ge=1, le=1000),
) -> ObjectCollection:
    return await _list(principal, database, NodeCertificate, _certificate, limit=limit)


@router.get("/challenges", response_model=ObjectCollection)
async def list_challenges(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=200, ge=1, le=500),
) -> ObjectCollection:
    require_role(
        principal,
        {
            RoleCode.NODE_REGISTRAR,
            RoleCode.NODE_SECURITY_ADMIN,
            RoleCode.SECURITY_ADMIN,
            RoleCode.NODE_AUDITOR,
            RoleCode.AUDITOR,
        },
    )
    return await _list(principal, database, NodeChallenge, _challenge, limit=limit)


@router.get("/trust-contracts", response_model=ObjectCollection)
async def list_contracts(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=200, ge=1, le=500),
) -> ObjectCollection:
    return await _list(principal, database, NodeTrustContract, _contract, limit=limit)


@router.get("/bilateral-limits", response_model=ObjectCollection)
async def list_limits(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=500, ge=1, le=1000),
) -> ObjectCollection:
    return await _list(principal, database, NodeBilateralLimit, _limit, limit=limit)


@router.get("/bonds", response_model=ObjectCollection)
async def list_bonds(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=500, ge=1, le=1000),
) -> ObjectCollection:
    return await _list(principal, database, NodeBond, _bond, limit=limit)


@router.get("/exposures", response_model=ObjectCollection)
async def list_exposures(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=500, ge=1, le=1000),
) -> ObjectCollection:
    return await _list(
        principal,
        database,
        NodeExposure,
        lambda item: _base(
            item,
            "id",
            "node_id",
            "capability",
            "unit",
            "current_amount",
            "reserved_amount",
            "updated_at",
            "version",
        ),
        limit=limit,
    )


@router.get("/offline-epochs", response_model=ObjectCollection)
async def list_epochs(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=500, ge=1, le=1000),
) -> ObjectCollection:
    return await _list(principal, database, OfflineEpoch, _epoch, limit=limit)


@router.get("/paper-forms", response_model=ObjectCollection)
async def list_paper_forms(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    epoch_id: UUID | None = None,
    limit: int = Query(default=500, ge=1, le=1000),
) -> ObjectCollection:
    _read(principal)
    statement = select(FederationPaperForm).order_by(FederationPaperForm.issued_at.desc())
    if epoch_id is not None:
        statement = statement.where(FederationPaperForm.epoch_id == epoch_id)
    async with database.session() as session:
        rows = list((await session.execute(statement.limit(limit))).scalars())
    return _collection([_paper_form(item) for item in rows])


@router.get("/sync/packages", response_model=ObjectCollection)
async def list_packages(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=500, ge=1, le=1000),
) -> ObjectCollection:
    return await _list(principal, database, SyncPackage, _package, limit=limit)


@router.get("/sync/inbox-events", response_model=ObjectCollection)
async def list_inbox_events(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    package_id: UUID | None = None,
    limit: int = Query(default=500, ge=1, le=1000),
) -> ObjectCollection:
    _read(principal)
    statement = select(InboxEvent).order_by(InboxEvent.received_at.desc())
    if package_id is not None:
        statement = statement.where(InboxEvent.package_id == package_id)
    async with database.session() as session:
        rows = list((await session.execute(statement.limit(limit))).scalars())
    return _collection(
        [
            _base(
                item,
                "id",
                "package_id",
                "source_node_id",
                "event_id",
                "event_type",
                "local_sequence",
                "aggregate_type",
                "aggregate_id",
                "aggregate_version",
                "previous_event_hash",
                "event_hash",
                "status",
                "effect_summary",
                "received_at",
                "applied_at",
            )
            for item in rows
        ]
    )


@router.get("/sync/conflicts", response_model=ObjectCollection)
async def list_conflicts(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=500, ge=1, le=1000),
) -> ObjectCollection:
    return await _list(principal, database, SyncConflict, _conflict, limit=limit)


@router.get("/sync/receipts", response_model=ObjectCollection)
async def list_receipts(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=500, ge=1, le=1000),
) -> ObjectCollection:
    return await _list(
        principal,
        database,
        SyncReceipt,
        lambda item: {
            **_base(
                item,
                "id",
                "package_id",
                "receipt_payload",
                "receipt_hash",
                "event_id",
                "created_at",
            ),
            "signature_base64": base64.b64encode(item.signature).decode(),
        },
        limit=limit,
    )


@router.get("/sync/checkpoints", response_model=ObjectCollection)
async def list_checkpoints(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=500, ge=1, le=1000),
) -> ObjectCollection:
    return await _list(
        principal,
        database,
        FederationCheckpoint,
        lambda item: _base(
            item,
            "id",
            "peer_node_id",
            "package_id",
            "local_sequence",
            "remote_sequence",
            "local_event_hash",
            "remote_event_hash",
            "checkpoint_hash",
            "event_id",
            "created_at",
        ),
        limit=limit,
    )


@router.get("/incidents", response_model=ObjectCollection)
async def list_incidents(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=500, ge=1, le=1000),
) -> ObjectCollection:
    return await _list(
        principal,
        database,
        NodeSecurityIncident,
        lambda item: _base(
            item,
            "id",
            "node_id",
            "incident_type",
            "severity",
            "status",
            "earliest_compromise_at",
            "description",
            "evidence_ids",
            "containment_payload",
            "corrective_actions",
            "opened_by_user_id",
            "resolved_by_user_id",
            "created_at",
            "resolved_at",
            "version",
        ),
        limit=limit,
    )


@router.get("/key-rotations", response_model=ObjectCollection)
async def list_key_rotations(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    limit: int = Query(default=500, ge=1, le=1000),
) -> ObjectCollection:
    return await _list(
        principal,
        database,
        NodeKeyRotationRequest,
        lambda item: _base(
            item,
            "id",
            "node_id",
            "old_certificate_id",
            "new_certificate_id",
            "reason",
            "status",
            "requested_by_user_id",
            "decided_by_user_id",
            "continuity_verified",
            "created_at",
            "decided_at",
            "version",
        ),
        limit=limit,
    )


@router.get("/workspaces/{workspace}", response_model=ObjectEnvelope)
async def workspace(
    workspace: str,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> ObjectEnvelope:
    role_by_workspace = {
        "registrar": {RoleCode.NODE_REGISTRAR},
        "security": {RoleCode.NODE_SECURITY_ADMIN, RoleCode.SECURITY_ADMIN},
        "auditor": {RoleCode.NODE_AUDITOR, RoleCode.AUDITOR},
    }
    if workspace not in role_by_workspace:
        raise federation_error("WORKSPACE_NOT_FOUND", 404)
    require_role(principal, role_by_workspace[workspace])
    async with database.session() as session:
        nodes = list((await session.execute(select(ExternalNode))).scalars())
        applications = list((await session.execute(select(NodeApplication))).scalars())
        responsibilities = list((await session.execute(select(NodeResponsibleParty))).scalars())
        contracts = list((await session.execute(select(NodeTrustContract))).scalars())
        limits = list((await session.execute(select(NodeBilateralLimit))).scalars())
        packages = list((await session.execute(select(SyncPackage))).scalars())
        conflicts = list((await session.execute(select(SyncConflict))).scalars())
        incidents = list((await session.execute(select(NodeSecurityIncident))).scalars())
    data = {
        "nodes": [_node(item) for item in nodes],
        "applications": [_application(item) for item in applications],
        "responsibilities": [_responsibility(item) for item in responsibilities],
        "contracts": [_contract(item) for item in contracts],
        "limits": [_limit(item) for item in limits],
        "packages": [_package(item) for item in packages],
        "conflicts": [_conflict(item) for item in conflicts],
        "incidents": [
            _base(
                item,
                "id",
                "node_id",
                "incident_type",
                "severity",
                "status",
                "created_at",
                "resolved_at",
                "version",
            )
            for item in incidents
        ],
    }
    return ObjectEnvelope(data=data, request_id=get_request_id())


@router.post("/nodes/applications", response_model=CommandEnvelope, status_code=201)
async def create_application(
    payload: NodeApplicationCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    try:
        public_key = base64.b64decode(payload.public_key_base64, validate=True)
    except ValueError as exc:
        raise federation_error("NODE_PUBLIC_KEY_INVALID", 422) from exc
    values = payload.model_dump(exclude={"public_key_base64", "responsible_parties"})
    parties = tuple(
        ResponsiblePartyInput(**item.model_dump()) for item in payload.responsible_parties
    )
    return await _commit(
        database,
        lambda session: FederationService(settings).create_application(
            session,
            principal=principal,
            public_key=public_key,
            responsible_parties=parties,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **values,
        ),
    )


@router.post(
    "/nodes/applications/{application_id}/responsibilities/{responsibility_id}/accept",
    response_model=CommandEnvelope,
    status_code=201,
)
async def accept_responsibility(
    application_id: UUID,
    responsibility_id: UUID,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: FederationService(settings).accept_responsibility(
            session,
            principal=principal,
            application_id=application_id,
            responsibility_id=responsibility_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/nodes/applications/{application_id}/submit",
    response_model=CommandEnvelope,
    status_code=201,
)
async def submit_application(
    application_id: UUID,
    payload: VersionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: FederationService(settings).submit_application(
            session,
            principal=principal,
            application_id=application_id,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/nodes/applications/{application_id}/identity-verification",
    response_model=CommandEnvelope,
    status_code=201,
)
async def verify_identity(
    application_id: UUID,
    payload: IdentityVerificationRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: FederationService(settings).verify_identity(
            session,
            principal=principal,
            application_id=application_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post(
    "/nodes/applications/{application_id}/challenge",
    response_model=ObjectEnvelope,
    status_code=201,
)
async def issue_challenge(
    application_id: UUID,
    payload: ChallengeIssueRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> ObjectEnvelope:
    async with database.session() as session:
        material: ChallengeMaterial = await FederationService(settings).issue_challenge(
            session,
            principal=principal,
            application_id=application_id,
            expected_version=payload.expected_version,
            protocol_version=payload.protocol_version,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )
        await session.commit()
    return ObjectEnvelope(
        data={
            "event_id": material.result.event_id,
            "object_id": material.result.object_id,
            "replayed": material.result.replayed,
            "nonce": material.nonce,
        },
        request_id=get_request_id(),
    )


@router.post(
    "/challenges/{challenge_id}/response",
    response_model=CommandEnvelope,
    status_code=201,
)
async def record_challenge_response(
    challenge_id: UUID,
    payload: ChallengeResponseRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    try:
        signature = base64.b64decode(payload.signature_base64, validate=True)
    except ValueError as exc:
        raise federation_error("CHALLENGE_SIGNATURE_INVALID", 422) from exc
    return await _commit(
        database,
        lambda session: FederationService(settings).record_challenge_response(
            session,
            principal=principal,
            challenge_id=challenge_id,
            nonce=payload.nonce,
            response_payload=payload.response_payload,
            signature=signature,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/nodes/applications/{application_id}/audit-decision",
    response_model=CommandEnvelope,
    status_code=201,
)
async def decide_audit(
    application_id: UUID,
    payload: AuditDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: FederationService(settings).decide_audit(
            session,
            principal=principal,
            application_id=application_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post("/trust-contracts", response_model=CommandEnvelope, status_code=201)
async def propose_contract(
    payload: TrustContractRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: NodeTrustService(settings).propose_trust_contract(
            session,
            principal=principal,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post(
    "/trust-contracts/{contract_id}/approval",
    response_model=CommandEnvelope,
    status_code=201,
)
async def approve_contract(
    contract_id: UUID,
    payload: HashApprovalRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: NodeTrustService(settings).approve_trust_contract(
            session,
            principal=principal,
            contract_id=contract_id,
            expected_version=payload.expected_version,
            terms_hash=payload.terms_hash,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/nodes/{node_id}/bilateral-limits",
    response_model=CommandEnvelope,
    status_code=201,
)
async def propose_limit(
    node_id: UUID,
    payload: BilateralLimitRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: NodeTrustService(settings).propose_bilateral_limit(
            session,
            principal=principal,
            node_id=node_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post(
    "/bilateral-limits/{limit_id}/approval",
    response_model=CommandEnvelope,
    status_code=201,
)
async def approve_limit(
    limit_id: UUID,
    payload: HashApprovalRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: NodeTrustService(settings).approve_bilateral_limit(
            session,
            principal=principal,
            limit_id=limit_id,
            expected_version=payload.expected_version,
            terms_hash=payload.terms_hash,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post("/nodes/{node_id}/bonds", response_model=CommandEnvelope, status_code=201)
async def register_bond(
    node_id: UUID,
    payload: NodeBondRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: NodeTrustService(settings).register_bond(
            session,
            principal=principal,
            node_id=node_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post("/nodes/{node_id}/activate", response_model=CommandEnvelope, status_code=201)
async def activate_node(
    node_id: UUID,
    payload: VersionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: NodeTrustService(settings).activate_node(
            session,
            principal=principal,
            node_id=node_id,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


def _status_endpoint(action: str) -> Callable[..., Awaitable[CommandEnvelope]]:
    async def endpoint(
        node_id: UUID,
        payload: NodeStatusRequest,
        idempotency_key: IdempotencyKey,
        principal: PrincipalDependency,
        database: DatabaseDependency,
        settings: SettingsDependency,
    ) -> CommandEnvelope:
        return await _commit(
            database,
            lambda session: NodeTrustService(settings).change_node_status(
                session,
                principal=principal,
                node_id=node_id,
                expected_version=payload.expected_version,
                action=action,
                rationale=payload.rationale,
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            ),
        )

    return endpoint


router.post("/nodes/{node_id}/suspend", response_model=CommandEnvelope, status_code=201)(
    _status_endpoint("suspend")
)
router.post("/nodes/{node_id}/quarantine", response_model=CommandEnvelope, status_code=201)(
    _status_endpoint("quarantine")
)
router.post("/nodes/{node_id}/revoke", response_model=CommandEnvelope, status_code=201)(
    _status_endpoint("revoke")
)


@router.post("/nodes/{node_id}/incidents", response_model=CommandEnvelope, status_code=201)
async def open_incident(
    node_id: UUID,
    payload: IncidentRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: NodeTrustService(settings).open_incident(
            session,
            principal=principal,
            node_id=node_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post(
    "/incidents/{incident_id}/resolution",
    response_model=CommandEnvelope,
    status_code=201,
)
async def resolve_incident(
    incident_id: UUID,
    payload: IncidentResolutionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: NodeTrustService(settings).resolve_incident(
            session,
            principal=principal,
            incident_id=incident_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post(
    "/nodes/{node_id}/key-rotations",
    response_model=CommandEnvelope,
    status_code=201,
)
async def request_rotation(
    node_id: UUID,
    payload: KeyRotationRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    try:
        new_key = base64.b64decode(payload.new_public_key_base64, validate=True)
        old_signature = (
            base64.b64decode(payload.old_signature_base64, validate=True)
            if payload.old_signature_base64
            else None
        )
        new_signature = base64.b64decode(payload.new_signature_base64, validate=True)
    except ValueError as exc:
        raise federation_error("KEY_ROTATION_ENCODING_INVALID", 422) from exc
    return await _commit(
        database,
        lambda session: NodeTrustService(settings).request_key_rotation(
            session,
            principal=principal,
            node_id=node_id,
            new_public_key=new_key,
            old_signature=old_signature,
            new_signature=new_signature,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            reason=payload.reason,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/key-rotations/{rotation_id}/decision",
    response_model=CommandEnvelope,
    status_code=201,
)
async def decide_rotation(
    rotation_id: UUID,
    payload: KeyRotationDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: NodeTrustService(settings).approve_key_rotation(
            session,
            principal=principal,
            rotation_id=rotation_id,
            expected_version=payload.expected_version,
            approve=payload.approve,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/nodes/{node_id}/rehabilitate",
    response_model=CommandEnvelope,
    status_code=201,
)
async def rehabilitate_node(
    node_id: UUID,
    payload: RehabilitationRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: NodeTrustService(settings).rehabilitate_node(
            session,
            principal=principal,
            node_id=node_id,
            expected_version=payload.expected_version,
            integrity_summary=payload.integrity_summary,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/nodes/{node_id}/offline-epochs",
    response_model=CommandEnvelope,
    status_code=201,
)
async def open_epoch(
    node_id: UUID,
    payload: OfflineEpochRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: NodeTrustService(settings).open_offline_epoch(
            session,
            principal=principal,
            node_id=node_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post(
    "/offline-epochs/{epoch_id}/close",
    response_model=CommandEnvelope,
    status_code=201,
)
async def close_epoch(
    epoch_id: UUID,
    payload: EpochCloseRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: NodeTrustService(settings).close_offline_epoch(
            session,
            principal=principal,
            epoch_id=epoch_id,
            expected_version=payload.expected_version,
            reconciliation=payload.reconciliation,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/offline-epochs/{epoch_id}/paper-forms",
    response_model=CommandEnvelope,
    status_code=201,
)
async def issue_paper_form(
    epoch_id: UUID,
    payload: PaperFormIssueRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: PaperFormService(settings).issue(
            session,
            principal=principal,
            epoch_id=epoch_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post(
    "/paper-forms/{form_id}/record",
    response_model=CommandEnvelope,
    status_code=201,
)
async def record_paper_form(
    form_id: UUID,
    payload: PaperFormRecordRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: PaperFormService(settings).record(
            session,
            principal=principal,
            form_id=form_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post(
    "/paper-forms/{form_id}/void",
    response_model=CommandEnvelope,
    status_code=201,
)
async def void_paper_form(
    form_id: UUID,
    payload: PaperFormVoidRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: PaperFormService(settings).void(
            session,
            principal=principal,
            form_id=form_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post(
    "/nodes/{node_id}/exposure-reservations",
    response_model=CommandEnvelope,
    status_code=201,
)
async def reserve_exposure(
    node_id: UUID,
    payload: ExposureReserveRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: NodeTrustService(settings).reserve_exposure(
            session,
            principal=principal,
            node_id=node_id,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        ),
    )


@router.post("/sync/packages/export", response_model=ObjectEnvelope, status_code=201)
async def export_package(
    payload: ExportPackageRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> ObjectEnvelope:
    async with database.session() as session:
        result: PackageArchiveResult = await SyncService(settings).export_package(
            session,
            principal=principal,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
            **payload.model_dump(),
        )
        await session.commit()
    return ObjectEnvelope(
        data={
            "event_id": result.result.event_id,
            "object_id": result.result.object_id,
            "replayed": result.result.replayed,
            "archive_hash": result.archive_hash,
            "download_path": f"/api/v1/federation/sync/packages/{result.result.object_id}/archive",
        },
        request_id=get_request_id(),
    )


@router.post("/sync/packages/import", response_model=CommandEnvelope, status_code=201)
async def import_package(
    request: Request,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"application/zip", "application/octet-stream"}:
        raise federation_error("SYNC_ARCHIVE_MEDIA_TYPE_INVALID", 415)
    archive = await request.body()
    if len(archive) > settings.sync_package_max_bytes:
        raise federation_error("SYNC_ARCHIVE_SIZE_INVALID", 413)
    return await _commit(
        database,
        lambda session: SyncService(settings).import_package(
            session,
            principal=principal,
            archive=archive,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/sync/conflicts/{conflict_id}/resolution",
    response_model=CommandEnvelope,
    status_code=201,
)
async def resolve_conflict(
    conflict_id: UUID,
    payload: ConflictDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: SyncService(settings).resolve_conflict(
            session,
            principal=principal,
            conflict_id=conflict_id,
            expected_version=payload.expected_version,
            decision=payload.decision,
            rationale=payload.rationale,
            evidence_ids=payload.evidence_ids,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.post(
    "/sync/packages/{package_id}/apply",
    response_model=CommandEnvelope,
    status_code=201,
)
async def apply_package(
    package_id: UUID,
    payload: PackageApplyRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    return await _commit(
        database,
        lambda session: SyncService(settings).apply_package(
            session,
            principal=principal,
            package_id=package_id,
            expected_version=payload.expected_version,
            manifest_hash=payload.manifest_hash,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        ),
    )


@router.get("/sync/packages/{package_id}/archive", response_class=FileResponse)
async def download_archive(
    package_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> FileResponse:
    _read(principal)
    async with database.session() as session:
        package = await session.get(SyncPackage, package_id)
    if package is None or package.direction != "OUTBOUND":
        raise federation_error("SYNC_PACKAGE_NOT_FOUND", 404)
    path = SyncService(settings)._absolute_package_path(package.archive_path)
    if not path.is_file():
        raise federation_error("SYNC_ARCHIVE_MISSING", 404)
    return FileResponse(
        path=Path(path),
        media_type="application/zip",
        filename=f"sync-package-{package.id}.zip",
    )
