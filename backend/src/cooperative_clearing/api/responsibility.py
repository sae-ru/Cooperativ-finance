"""Personal responsibility commands and signed-journal evidence API."""

import base64
from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError

from cooperative_clearing.api.auth import _request_uuid
from cooperative_clearing.api.dependencies import (
    DatabaseDependency,
    PrincipalDependency,
    SettingsDependency,
)
from cooperative_clearing.api.identity_schemas import CommandEnvelope
from cooperative_clearing.api.identity_schemas import CommandResult as ApiCommandResult
from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, require_role
from cooperative_clearing.modules.journal.application.service import (
    envelope_from_event,
    verify_journal,
)
from cooperative_clearing.modules.journal.infrastructure.models import (
    EventSignature,
    OutboxMessage,
    SignedEvent,
)
from cooperative_clearing.modules.node.infrastructure.models import NodeKeyRecord, NodeProfile
from cooperative_clearing.modules.responsibility.application.service import (
    APPROVER_ROLES,
    PROPOSER_ROLES,
    ResponsibilityCommandResult,
    ResponsibilityService,
    assignment_summary,
    canonical_preview,
)
from cooperative_clearing.modules.responsibility.domain.types import ApprovalDecision
from cooperative_clearing.modules.responsibility.infrastructure.models import (
    ResponsibilityApproval,
    ResponsibilityAssignment,
)
from cooperative_clearing.shared.core.request_context import get_request_id
from cooperative_clearing.shared.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/responsibility", tags=["responsibility"])
journal_router = APIRouter(prefix="/api/v1/journal", tags=["signed-journal"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]

GLOBAL_RESPONSIBILITY_READ = {RoleCode.SECURITY_ADMIN, RoleCode.AUDITOR}
SCOPED_RESPONSIBILITY_READ = {RoleCode.COOPERATIVE_ADMIN, RoleCode.RISK_ADMIN}
RESPONSIBILITY_READ_ALL = GLOBAL_RESPONSIBILITY_READ | SCOPED_RESPONSIBILITY_READ
JOURNAL_READ_ROLES = {
    RoleCode.RISK_ADMIN,
    RoleCode.SECURITY_ADMIN,
    RoleCode.AUDITOR,
    RoleCode.NODE_REGISTRAR,
}


class ResponsibilityProposalRequest(BaseModel):
    cooperative_id: UUID
    member_id: UUID
    role_assignment_id: UUID
    subject_type: str = Field(min_length=2, max_length=80)
    subject_id: UUID
    scope: str = Field(min_length=2, max_length=200)
    max_exposure: Decimal = Field(ge=0, max_digits=20, decimal_places=4)
    exposure_unit: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    valid_until: datetime | None = None
    expected_summary_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")


class ResponsibilityDecisionRequest(BaseModel):
    decision: ApprovalDecision
    reason_code: str = Field(min_length=2, max_length=100)


class ResponsibilityAcceptRequest(BaseModel):
    expected_version: int = Field(ge=1)


class ResponsibilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooperative_id: UUID
    member_id: UUID
    role_assignment_id: UUID
    subject_type: str
    subject_id: UUID
    scope: str
    max_exposure: Decimal
    exposure_unit: str
    valid_from: datetime
    valid_until: datetime | None
    status: str
    created_by_user_id: UUID
    approved_by_user_id: UUID | None
    accepted_by_user_id: UUID | None
    created_event_id: UUID
    approved_event_id: UUID | None
    accepted_event_id: UUID | None
    created_at: datetime
    approved_at: datetime | None
    accepted_at: datetime | None
    version: int


class ResponsibilityApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    assignment_id: UUID
    decision: str
    reason_code: str
    decided_by_user_id: UUID
    event_id: UUID
    decided_at: datetime


class ResponsibilityCollection(BaseModel):
    data: list[ResponsibilityResponse]
    request_id: str


class ApprovalCollection(BaseModel):
    data: list[ResponsibilityApprovalResponse]
    request_id: str


class CanonicalPreviewResponse(BaseModel):
    canonicalization_profile: str
    canonical_json: str
    summary_hash: str


class CanonicalPreviewEnvelope(BaseModel):
    data: CanonicalPreviewResponse
    request_id: str


class EventSignatureResponse(BaseModel):
    key_id: UUID
    key_fingerprint: str
    algorithm: str
    scope: str
    signature_base64: str
    signed_at: datetime


class SignedEventResponse(BaseModel):
    event_id: UUID
    event_type: str
    node_id: UUID
    local_sequence: int
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    occurred_at: datetime
    recorded_at: datetime
    previous_event_hash: str | None
    payload_hash: str
    event_hash: str
    canonicalization_profile: str
    canonical_json: str
    envelope: dict[str, object]
    signatures: list[EventSignatureResponse]


class SignedEventCollection(BaseModel):
    data: list[SignedEventResponse]
    request_id: str


class IntegrityFailureResponse(BaseModel):
    sequence: int
    event_id: UUID
    code: str


class IntegrityResponse(BaseModel):
    ok: bool
    node_id: UUID
    checked_events: int
    last_sequence: int
    last_event_hash: str | None
    failures: list[IntegrityFailureResponse]


class IntegrityEnvelope(BaseModel):
    data: IntegrityResponse
    request_id: str


class OutboxStatusResponse(BaseModel):
    pending: int
    processing: int
    published: int
    quarantined: int
    oldest_pending_at: datetime | None


class OutboxStatusEnvelope(BaseModel):
    data: OutboxStatusResponse
    request_id: str


def _has_global_role(principal: Principal, roles: set[RoleCode]) -> bool:
    return any(grant.role in roles and grant.cooperative_id is None for grant in principal.roles)


def _require_global_role(principal: Principal, roles: set[RoleCode]) -> None:
    require_role(principal, roles)
    if not _has_global_role(principal, roles):
        raise DomainError(
            code="AUTHORIZATION_DENIED",
            message_key="errors.auth.authorization_denied",
            status_code=403,
        )


def _command(result: ResponsibilityCommandResult) -> CommandEnvelope:
    return CommandEnvelope(
        data=ApiCommandResult(
            event_id=result.event_id,
            object_id=result.object_id,
            replayed=result.replayed,
        ),
        request_id=get_request_id(),
    )


@router.get("/assignments", response_model=ResponsibilityCollection)
async def list_assignments(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    subject_type: str | None = Query(default=None, min_length=2, max_length=80),
    subject_id: UUID | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> ResponsibilityCollection:
    statement = select(ResponsibilityAssignment).order_by(
        ResponsibilityAssignment.created_at.desc(), ResponsibilityAssignment.id
    )
    scoped_cooperatives = {
        grant.cooperative_id
        for grant in principal.roles
        if grant.role in SCOPED_RESPONSIBILITY_READ and grant.cooperative_id is not None
    }
    if _has_global_role(principal, GLOBAL_RESPONSIBILITY_READ | SCOPED_RESPONSIBILITY_READ):
        pass
    elif scoped_cooperatives:
        statement = statement.where(
            ResponsibilityAssignment.cooperative_id.in_(scoped_cooperatives)
        )
    elif principal.member_id is not None:
        statement = statement.where(ResponsibilityAssignment.member_id == principal.member_id)
    else:
        raise DomainError(
            code="AUTHORIZATION_DENIED",
            message_key="errors.auth.authorization_denied",
            status_code=403,
        )
    if subject_type is not None:
        statement = statement.where(ResponsibilityAssignment.subject_type == subject_type)
    if subject_id is not None:
        statement = statement.where(ResponsibilityAssignment.subject_id == subject_id)
    async with database.session() as session:
        items = list((await session.execute(statement.limit(limit))).scalars())
    return ResponsibilityCollection(data=items, request_id=get_request_id())


@router.get("/approvals", response_model=ApprovalCollection)
async def list_approvals(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    assignment_id: UUID | None = None,
) -> ApprovalCollection:
    require_role(principal, RESPONSIBILITY_READ_ALL)
    statement = (
        select(ResponsibilityApproval)
        .join(
            ResponsibilityAssignment,
            ResponsibilityAssignment.id == ResponsibilityApproval.assignment_id,
        )
        .order_by(ResponsibilityApproval.decided_at.desc())
    )
    scoped_cooperatives = {
        grant.cooperative_id
        for grant in principal.roles
        if grant.role in SCOPED_RESPONSIBILITY_READ and grant.cooperative_id is not None
    }
    if _has_global_role(principal, GLOBAL_RESPONSIBILITY_READ | SCOPED_RESPONSIBILITY_READ):
        pass
    elif scoped_cooperatives:
        statement = statement.where(
            ResponsibilityAssignment.cooperative_id.in_(scoped_cooperatives)
        )
    else:
        raise DomainError(
            code="AUTHORIZATION_DENIED",
            message_key="errors.auth.authorization_denied",
            status_code=403,
        )
    if assignment_id is not None:
        statement = statement.where(ResponsibilityApproval.assignment_id == assignment_id)
    async with database.session() as session:
        items = list((await session.execute(statement)).scalars())
    return ApprovalCollection(data=items, request_id=get_request_id())


@router.post("/preview", response_model=CanonicalPreviewEnvelope)
async def preview_assignment(
    payload: ResponsibilityProposalRequest,
    principal: PrincipalDependency,
) -> CanonicalPreviewEnvelope:
    require_role(principal, PROPOSER_ROLES, payload.cooperative_id)
    if principal.member_id is None:
        raise DomainError(
            code="PHYSICAL_ACTOR_REQUIRED",
            message_key="errors.responsibility.physical_actor_required",
            status_code=403,
        )
    preview = canonical_preview(
        assignment_summary(
            cooperative_id=payload.cooperative_id,
            member_id=payload.member_id,
            role_assignment_id=payload.role_assignment_id,
            subject_type=payload.subject_type.strip(),
            subject_id=payload.subject_id,
            scope=payload.scope.strip(),
            max_exposure=payload.max_exposure,
            exposure_unit=payload.exposure_unit.upper(),
            valid_until=payload.valid_until,
        )
    )
    return CanonicalPreviewEnvelope(
        data=CanonicalPreviewResponse(
            canonicalization_profile=preview.profile,
            canonical_json=preview.canonical_json,
            summary_hash=preview.summary_hash,
        ),
        request_id=get_request_id(),
    )


@router.post("/assignments", response_model=CommandEnvelope, status_code=201)
async def propose_assignment(
    payload: ResponsibilityProposalRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, PROPOSER_ROLES, payload.cooperative_id)
    if payload.expected_summary_hash is None:
        raise DomainError(
            code="CANONICAL_PREVIEW_REQUIRED",
            message_key="errors.responsibility.canonical_preview_required",
            status_code=422,
        )
    async with database.session() as session:
        try:
            result = await ResponsibilityService(settings).propose(
                session,
                principal=principal,
                cooperative_id=payload.cooperative_id,
                member_id=payload.member_id,
                role_assignment_id=payload.role_assignment_id,
                subject_type=payload.subject_type,
                subject_id=payload.subject_id,
                scope=payload.scope,
                max_exposure=payload.max_exposure,
                exposure_unit=payload.exposure_unit,
                valid_until=payload.valid_until,
                expected_summary_hash=payload.expected_summary_hash,
                idempotency_key=idempotency_key,
                request_id=_request_uuid(),
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise DomainError(
                code="RESPONSIBILITY_CONFLICT",
                message_key="errors.responsibility.conflict",
                status_code=409,
            ) from exc
    return _command(result)


@router.post("/assignments/{assignment_id}/decision", response_model=CommandEnvelope)
async def decide_assignment(
    assignment_id: UUID,
    payload: ResponsibilityDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    require_role(principal, APPROVER_ROLES)
    async with database.session() as session:
        result = await ResponsibilityService(settings).decide(
            session,
            principal=principal,
            assignment_id=assignment_id,
            decision=payload.decision,
            reason_code=payload.reason_code,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )
        await session.commit()
    return _command(result)


@router.post("/assignments/{assignment_id}/accept", response_model=CommandEnvelope)
async def accept_assignment(
    assignment_id: UUID,
    payload: ResponsibilityAcceptRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CommandEnvelope:
    async with database.session() as session:
        result = await ResponsibilityService(settings).accept(
            session,
            principal=principal,
            assignment_id=assignment_id,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
            request_id=_request_uuid(),
        )
        await session.commit()
    return _command(result)


@journal_router.get("/events", response_model=SignedEventCollection)
async def list_signed_events(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    aggregate_type: str | None = Query(default=None, min_length=2, max_length=80),
    aggregate_id: UUID | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> SignedEventCollection:
    _require_global_role(principal, JOURNAL_READ_ROLES)
    statement = (
        select(SignedEvent, EventSignature, NodeKeyRecord)
        .join(EventSignature, EventSignature.event_id == SignedEvent.event_id)
        .join(NodeKeyRecord, NodeKeyRecord.id == EventSignature.key_id)
        .where(EventSignature.signature_scope == "NODE")
        .order_by(SignedEvent.local_sequence.desc())
        .limit(limit)
    )
    if aggregate_type is not None:
        statement = statement.where(SignedEvent.aggregate_type == aggregate_type)
    if aggregate_id is not None:
        statement = statement.where(SignedEvent.aggregate_id == aggregate_id)
    async with database.session() as session:
        rows = list((await session.execute(statement)).all())
    return SignedEventCollection(
        data=[_event_response(*row) for row in rows], request_id=get_request_id()
    )


@journal_router.get("/integrity", response_model=IntegrityEnvelope)
async def journal_integrity(
    principal: PrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> IntegrityEnvelope:
    _require_global_role(principal, JOURNAL_READ_ROLES)
    async with database.session() as session:
        node = (
            await session.execute(
                select(NodeProfile).where(NodeProfile.node_code == settings.node_code)
            )
        ).scalar_one()
        report = await verify_journal(session, node.id)
        if not report.ok:
            await AuditRepository(session).record(
                action="JOURNAL_INTEGRITY_FAILED",
                object_type="NodeProfile",
                object_id=node.id,
                actor_user_id=principal.user_id,
                outcome="FAILURE",
                payload={"failure_codes": [item.code for item in report.failures]},
            )
            await session.commit()
    return IntegrityEnvelope(
        data=IntegrityResponse(
            ok=report.ok,
            node_id=report.node_id,
            checked_events=report.checked_events,
            last_sequence=report.last_sequence,
            last_event_hash=report.last_event_hash,
            failures=[
                IntegrityFailureResponse(
                    sequence=item.sequence, event_id=item.event_id, code=item.code
                )
                for item in report.failures
            ],
        ),
        request_id=get_request_id(),
    )


@journal_router.get("/outbox", response_model=OutboxStatusEnvelope)
async def outbox_status(
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> OutboxStatusEnvelope:
    _require_global_role(principal, JOURNAL_READ_ROLES)
    async with database.session() as session:
        row = (
            await session.execute(
                select(
                    func.count().filter(OutboxMessage.status == "PENDING"),
                    func.count().filter(OutboxMessage.status == "PROCESSING"),
                    func.count().filter(OutboxMessage.status == "PUBLISHED"),
                    func.count().filter(OutboxMessage.status == "QUARANTINED"),
                    func.min(
                        case(
                            (OutboxMessage.status == "PENDING", OutboxMessage.created_at),
                            else_=None,
                        )
                    ),
                )
            )
        ).one()
    return OutboxStatusEnvelope(
        data=OutboxStatusResponse(
            pending=int(row[0]),
            processing=int(row[1]),
            published=int(row[2]),
            quarantined=int(row[3]),
            oldest_pending_at=row[4],
        ),
        request_id=get_request_id(),
    )


def _event_response(
    event: SignedEvent, signature: EventSignature, key: NodeKeyRecord
) -> SignedEventResponse:
    envelope = envelope_from_event(event)
    return SignedEventResponse(
        event_id=event.event_id,
        event_type=event.event_type,
        node_id=event.node_id,
        local_sequence=event.local_sequence,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        aggregate_version=event.aggregate_version,
        occurred_at=event.occurred_at,
        recorded_at=event.recorded_at,
        previous_event_hash=event.previous_event_hash,
        payload_hash=event.payload_hash,
        event_hash=event.event_hash,
        canonicalization_profile=event.canonicalization_profile,
        canonical_json=event.canonical_envelope.decode("utf-8"),
        envelope=envelope,
        signatures=[
            EventSignatureResponse(
                key_id=key.id,
                key_fingerprint=key.fingerprint,
                algorithm=signature.algorithm,
                scope=signature.signature_scope,
                signature_base64=base64.b64encode(signature.signature).decode("ascii"),
                signed_at=signature.signed_at,
            )
        ],
    )
