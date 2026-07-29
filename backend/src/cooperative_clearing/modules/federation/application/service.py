"""Atomic onboarding, trust, responsibility, limits, keys, and incident workflows."""

import base64
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, TypeVar, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.federation.application.common import (
    FederationCommandResult,
    audit_federation_action,
    begin_federation_command,
    complete_federation_command,
    federation_actor,
    federation_command_assurance,
)
from cooperative_clearing.modules.federation.domain.types import (
    NodeCapability,
    ResponsibleRole,
    bounded_amount,
    federation_error,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    NodeApplication,
    NodeCertificate,
    NodeChallenge,
    NodeOwnerOrganization,
    NodeResponsibleParty,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import Member, RoleAssignment
from cooperative_clearing.modules.journal.application.service import SignedJournalService
from cooperative_clearing.modules.journal.domain.crypto import (
    canonicalize,
    payload_hash,
    sha256_ref,
    utc_timestamp,
    verify_signature,
)
from cooperative_clearing.modules.node.domain.node_code import NodeCode
from cooperative_clearing.modules.node.infrastructure.models import NodeProfile
from cooperative_clearing.shared.core.config import Settings

REGISTRAR_ROLES = {RoleCode.NODE_REGISTRAR}
SECURITY_ROLES = {RoleCode.SECURITY_ADMIN, RoleCode.NODE_SECURITY_ADMIN}
AUDIT_ROLES = {RoleCode.AUDITOR, RoleCode.NODE_AUDITOR}
NODE_PERSON_ROLES = {
    RoleCode.NODE_REGISTRAR,
    RoleCode.NODE_TECHNICAL_CUSTODIAN,
    RoleCode.NODE_SECURITY_ADMIN,
    RoleCode.NODE_BUSINESS_OPERATOR,
    RoleCode.NODE_AUDITOR,
    RoleCode.AUDITOR,
}
REQUIRED_RESPONSIBILITIES = {
    ResponsibleRole.OWNER_SIGNATORY,
    ResponsibleRole.TECHNICAL_CUSTODIAN,
    ResponsibleRole.SECURITY_ADMINISTRATOR,
    ResponsibleRole.BUSINESS_OPERATOR,
    ResponsibleRole.NODE_AUDITOR,
}
ModelT = TypeVar("ModelT")

RESPONSIBILITY_ASSIGNMENT_ROLES = {
    ResponsibleRole.OWNER_SIGNATORY: {
        RoleCode.NODE_BUSINESS_OPERATOR,
        RoleCode.NODE_REGISTRAR,
    },
    ResponsibleRole.TECHNICAL_CUSTODIAN: {RoleCode.NODE_TECHNICAL_CUSTODIAN},
    ResponsibleRole.SECURITY_ADMINISTRATOR: {
        RoleCode.NODE_SECURITY_ADMIN,
        RoleCode.SECURITY_ADMIN,
    },
    ResponsibleRole.BUSINESS_OPERATOR: {RoleCode.NODE_BUSINESS_OPERATOR},
    ResponsibleRole.NODE_AUDITOR: {RoleCode.NODE_AUDITOR, RoleCode.AUDITOR},
    ResponsibleRole.SPONSOR_APPROVER: {RoleCode.NODE_REGISTRAR},
}


@dataclass(frozen=True, slots=True)
class ResponsiblePartyInput:
    member_id: UUID
    role_assignment_id: UUID
    role_code: ResponsibleRole
    capability_scope: tuple[NodeCapability, ...]
    responsibility_scope: str
    max_exposure: Decimal
    exposure_unit: str
    valid_until: datetime | None


@dataclass(frozen=True, slots=True)
class ChallengeMaterial:
    result: FederationCommandResult
    nonce: str


def challenge_message(challenge_id: UUID, nonce: str, response_payload: dict[str, object]) -> bytes:
    return canonicalize(
        {
            "challenge_id": str(challenge_id),
            "nonce": nonce,
            "response_payload": response_payload,
        }
    )


def rotation_message(
    *,
    node_id: UUID,
    old_fingerprint: str,
    new_fingerprint: str,
    reason: str,
    valid_from: datetime,
    valid_until: datetime,
) -> bytes:
    return canonicalize(
        {
            "node_id": str(node_id),
            "old_fingerprint": old_fingerprint,
            "new_fingerprint": new_fingerprint,
            "reason": reason,
            "valid_from": utc_timestamp(valid_from),
            "valid_until": utc_timestamp(valid_until),
        }
    )


class FederationService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.journal = SignedJournalService(settings)

    async def create_application(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        node_code: str,
        display_name: str,
        owner_legal_name: str,
        owner_registration_code: str,
        owner_jurisdiction: str,
        owner_contact_payload: dict[str, object],
        territory: str,
        purpose: str,
        network_endpoints: list[object],
        hardware_manifest: dict[str, object],
        release_manifest: dict[str, object],
        capabilities: tuple[NodeCapability, ...],
        supported_protocols: list[str],
        supported_policies: dict[str, int],
        data_scopes: dict[str, object],
        requested_limits: dict[str, object],
        recovery_contacts: list[object],
        security_questionnaire: dict[str, object],
        evidence_ids: list[UUID],
        responsible_parties: tuple[ResponsiblePartyInput, ...],
        public_key: bytes,
        certificate_valid_from: datetime,
        certificate_valid_until: datetime,
        proposed_trust_expiry: datetime,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, REGISTRAR_ROLES)
        try:
            normalized_node_code = str(NodeCode(node_code))
        except ValueError as exc:
            raise federation_error("NODE_CODE_INVALID", 422) from exc
        now = datetime.now(UTC)
        valid_from = certificate_valid_from.astimezone(UTC)
        valid_until = certificate_valid_until.astimezone(UTC)
        trust_expiry = proposed_trust_expiry.astimezone(UTC)
        if len(public_key) != 32 or not (valid_from <= now < valid_until <= trust_expiry):
            raise federation_error("NODE_CERTIFICATE_PERIOD_INVALID", 422)
        if trust_expiry > now + timedelta(days=730):
            raise federation_error("TRUST_PERIOD_TOO_LONG", 422)
        capability_values = sorted({item.value for item in capabilities})
        if (
            not capability_values
            or not supported_protocols
            or any(value < 1 for value in supported_policies.values())
        ):
            raise federation_error("NODE_CAPABILITIES_INVALID", 422)
        role_values = {item.role_code for item in responsible_parties}
        if not REQUIRED_RESPONSIBILITIES.issubset(role_values):
            raise federation_error("NODE_RESPONSIBLE_ROLES_INCOMPLETE", 422)
        if len(responsible_parties) != len(
            {(item.member_id, item.role_code) for item in responsible_parties}
        ):
            raise federation_error("NODE_RESPONSIBLE_ROLE_DUPLICATE", 422)
        payload: dict[str, object] = {
            "node_code": normalized_node_code,
            "display_name": self._text(display_name, 200),
            "owner": {
                "legal_name": self._text(owner_legal_name, 240),
                "registration_code": self._text(owner_registration_code, 120),
                "jurisdiction": self._text(owner_jurisdiction, 120),
            },
            "territory": self._text(territory, 160),
            "purpose": self._text(purpose, 4000),
            "network_endpoints": network_endpoints,
            "hardware_manifest": hardware_manifest,
            "release_manifest": release_manifest,
            "capabilities": capability_values,
            "supported_protocols": sorted(set(supported_protocols)),
            "supported_policies": supported_policies,
            "data_scopes": data_scopes,
            "requested_limits": requested_limits,
            "recovery_contacts": recovery_contacts,
            "security_questionnaire_hash": payload_hash(security_questionnaire),
            "evidence_ids": sorted(str(item) for item in evidence_ids),
            "responsible_parties": [
                {
                    "member_id": str(item.member_id),
                    "role_assignment_id": str(item.role_assignment_id),
                    "role_code": item.role_code.value,
                    "capability_scope": sorted(scope.value for scope in item.capability_scope),
                    "responsibility_scope": self._text(item.responsibility_scope, 2000),
                    "max_exposure": str(bounded_amount(item.max_exposure)),
                    "exposure_unit": self._code(item.exposure_unit, 32),
                    "valid_until": (
                        utc_timestamp(item.valid_until) if item.valid_until is not None else None
                    ),
                }
                for item in responsible_parties
            ],
            "certificate_fingerprint": sha256_ref(public_key),
            "certificate_valid_from": utc_timestamp(valid_from),
            "certificate_valid_until": utc_timestamp(valid_until),
            "proposed_trust_expiry": utc_timestamp(trust_expiry),
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.create_application", idempotency_key, payload
        )
        if replay is not None:
            return replay
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"federation-node:{normalized_node_code}"},
        )
        if await session.scalar(
            select(ExternalNode.id).where(ExternalNode.node_code == normalized_node_code)
        ):
            raise federation_error("NODE_CODE_EXISTS")
        owner = await session.scalar(
            select(NodeOwnerOrganization).where(
                NodeOwnerOrganization.jurisdiction == payload["owner"]["jurisdiction"],  # type: ignore[index]
                NodeOwnerOrganization.registration_code == payload["owner"]["registration_code"],  # type: ignore[index]
            )
        )
        owner_id = owner.id if owner is not None else uuid4()
        if owner is not None and owner.legal_name != payload["owner"]["legal_name"]:  # type: ignore[index]
            raise federation_error("OWNER_IDENTITY_CONFLICT")
        local_node = await self._local_node(session)
        node_id = uuid5(NAMESPACE_URL, f"cooperative-clearing:node:{normalized_node_code}")
        application_id, certificate_id = uuid4(), uuid4()
        event = await self.journal.append(
            session,
            event_type="federation.node_application_created",
            aggregate_type="external_node",
            aggregate_id=node_id,
            aggregate_version=1,
            actor=actor,
            payload=payload,
            assurance=await federation_command_assurance(
                session,
                principal=principal,
                actor=actor,
                local_node_reference=self.settings.node_code,
                event_type="federation.node_application_created",
                subject_type="external_node",
                subject_id=node_id,
                target_node_id=node_id,
                command_record=record,
                evidence_refs=tuple({"evidence_id": str(item)} for item in evidence_ids),
                next_member_ids=tuple(item.member_id for item in responsible_parties),
            ),
        )
        if owner is None:
            session.add(
                NodeOwnerOrganization(
                    id=owner_id,
                    legal_name=str(payload["owner"]["legal_name"]),  # type: ignore[index]
                    registration_code=str(payload["owner"]["registration_code"]),  # type: ignore[index]
                    jurisdiction=str(payload["owner"]["jurisdiction"]),  # type: ignore[index]
                    contact_payload=owner_contact_payload,
                    status="ACTIVE",
                    created_event_id=event.event_id,
                )
            )
            await session.flush()
        session.add(
            ExternalNode(
                id=node_id,
                node_code=normalized_node_code,
                display_name=str(payload["display_name"]),
                owner_organization_id=owner_id,
                sponsor_local_node_id=local_node.id,
                territory=str(payload["territory"]),
                purpose=str(payload["purpose"]),
                status="DRAFT",
                trust_level="UNTRUSTED",
                network_endpoints=network_endpoints,
                hardware_manifest=hardware_manifest,
                release_manifest=release_manifest,
                capabilities=capability_values,
                supported_protocols=sorted(set(supported_protocols)),
                supported_policies=supported_policies,
                data_scopes=data_scopes,
                created_event_id=event.event_id,
            )
        )
        session.add(
            NodeApplication(
                id=application_id,
                node_id=node_id,
                status="DRAFT",
                requested_capabilities=capability_values,
                requested_limits=requested_limits,
                requested_data_scopes=data_scopes,
                recovery_contacts=recovery_contacts,
                security_questionnaire=security_questionnaire,
                evidence_ids=sorted(str(item) for item in evidence_ids),
                proposed_trust_expiry=trust_expiry,
                created_by_user_id=principal.user_id,
                created_by_member_id=actor.person_id,
                created_role_assignment_id=actor.role_assignment_id,
                created_event_id=event.event_id,
            )
        )
        session.add(
            NodeCertificate(
                id=certificate_id,
                node_id=node_id,
                algorithm="Ed25519",
                public_key=public_key,
                fingerprint=sha256_ref(public_key),
                status="PENDING",
                valid_from=valid_from,
                valid_until=valid_until,
            )
        )
        for item in responsible_parties:
            await self._validate_responsible_party(session, item, trust_expiry)
            session.add(
                NodeResponsibleParty(
                    id=uuid4(),
                    node_id=node_id,
                    application_id=application_id,
                    member_id=item.member_id,
                    role_assignment_id=item.role_assignment_id,
                    role_code=item.role_code.value,
                    capability_scope=sorted(scope.value for scope in item.capability_scope),
                    responsibility_scope=self._text(item.responsibility_scope, 2000),
                    max_exposure=bounded_amount(item.max_exposure),
                    exposure_unit=self._code(item.exposure_unit, 32),
                    valid_from=now,
                    valid_until=(
                        item.valid_until.astimezone(UTC) if item.valid_until is not None else None
                    ),
                    status="PROPOSED",
                )
            )
        await audit_federation_action(
            session,
            principal,
            "NODE_APPLICATION_CREATED",
            "ExternalNode",
            node_id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, application_id)

    async def accept_responsibility(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        application_id: UUID,
        responsibility_id: UUID,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, NODE_PERSON_ROLES)
        payload = {
            "application_id": str(application_id),
            "responsibility_id": str(responsibility_id),
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.accept_responsibility", idempotency_key, payload
        )
        if replay is not None:
            return replay
        item = await self._locked(session, NodeResponsibleParty, responsibility_id)
        if item.application_id != application_id:
            raise federation_error("RESPONSIBILITY_APPLICATION_MISMATCH", 404)
        if item.status != "PROPOSED" or item.member_id != principal.member_id:
            raise federation_error("RESPONSIBILITY_ACCEPTANCE_INVALID")
        if actor.role_assignment_id != item.role_assignment_id:
            raise federation_error("RESPONSIBILITY_ROLE_MISMATCH", 403)
        if item.valid_until is not None and item.valid_until <= datetime.now(UTC):
            raise federation_error("RESPONSIBILITY_EXPIRED")
        event = await self.journal.append(
            session,
            event_type="federation.node_responsibility_accepted",
            aggregate_type="node_responsibility",
            aggregate_id=item.id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "node_id": str(item.node_id),
                "role_code": item.role_code,
                "capability_scope": item.capability_scope,
                "maximum_exposure": str(item.max_exposure),
                "exposure_unit": item.exposure_unit,
            },
            assurance=await federation_command_assurance(
                session,
                principal=principal,
                actor=actor,
                local_node_reference=self.settings.node_code,
                event_type="federation.node_responsibility_accepted",
                subject_type="node_responsibility",
                subject_id=item.id,
                target_node_id=item.node_id,
                command_record=record,
                next_member_ids=(item.member_id,),
                maximum_loss=item.max_exposure,
                unit=item.exposure_unit,
            ),
        )
        item.status = "ACTIVE"
        item.accepted_by_user_id = principal.user_id
        item.accepted_event_id = event.event_id
        item.accepted_at = datetime.now(UTC)
        await audit_federation_action(
            session,
            principal,
            "NODE_RESPONSIBILITY_ACCEPTED",
            "NodeResponsibleParty",
            item.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, item.id)

    async def submit_application(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        application_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, REGISTRAR_ROLES)
        payload = {"application_id": str(application_id), "expected_version": expected_version}
        record, replay = await begin_federation_command(
            session, principal, "federation.submit_application", idempotency_key, payload
        )
        if replay is not None:
            return replay
        application = await self._locked(session, NodeApplication, application_id)
        node = await self._locked(session, ExternalNode, application.node_id)
        self._version(application.version, expected_version)
        if application.status != "DRAFT" or node.status != "DRAFT":
            raise federation_error("APPLICATION_STATE_INVALID")
        parties = list(
            (
                await session.execute(
                    select(NodeResponsibleParty).where(
                        NodeResponsibleParty.application_id == application.id,
                        NodeResponsibleParty.status == "ACTIVE",
                    )
                )
            ).scalars()
        )
        if not REQUIRED_RESPONSIBILITIES.issubset(
            {ResponsibleRole(item.role_code) for item in parties}
        ):
            raise federation_error("NODE_RESPONSIBLE_ROLES_NOT_ACCEPTED")
        certificate = await session.scalar(
            select(NodeCertificate).where(
                NodeCertificate.node_id == node.id, NodeCertificate.status == "PENDING"
            )
        )
        if certificate is None:
            raise federation_error("NODE_CERTIFICATE_MISSING")
        event = await self.journal.append(
            session,
            event_type="federation.node_application_submitted",
            aggregate_type="node_application",
            aggregate_id=application.id,
            aggregate_version=application.version + 1,
            actor=actor,
            payload={
                **payload,
                "node_id": str(node.id),
                "responsibility_ids": sorted(str(item.id) for item in parties),
                "certificate_fingerprint": certificate.fingerprint,
            },
            assurance=await federation_command_assurance(
                session,
                principal=principal,
                actor=actor,
                local_node_reference=self.settings.node_code,
                event_type="federation.node_application_submitted",
                subject_type="node_application",
                subject_id=application.id,
                target_node_id=node.id,
                command_record=record,
            ),
        )
        now = datetime.now(UTC)
        application.status = node.status = "APPLICATION_SUBMITTED"
        application.submitted_at = now
        application.version += 1
        node.version += 1
        node.updated_at = now
        await audit_federation_action(
            session,
            principal,
            "NODE_APPLICATION_SUBMITTED",
            "NodeApplication",
            application.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, application.id)

    async def verify_identity(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        application_id: UUID,
        expected_version: int,
        verification_summary: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(
            session, principal, REGISTRAR_ROLES | SECURITY_ROLES | AUDIT_ROLES
        )
        payload = {
            "application_id": str(application_id),
            "expected_version": expected_version,
            "verification_summary": self._text(verification_summary, 4000),
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.verify_identity", idempotency_key, payload
        )
        if replay is not None:
            return replay
        application = await self._locked(session, NodeApplication, application_id)
        node = await self._locked(session, ExternalNode, application.node_id)
        self._version(application.version, expected_version)
        if application.status != "APPLICATION_SUBMITTED":
            raise federation_error("APPLICATION_STATE_INVALID")
        if application.created_by_user_id == principal.user_id:
            raise federation_error("INDEPENDENT_REVIEW_REQUIRED", 403)
        event = await self.journal.append(
            session,
            event_type="federation.node_identity_verified",
            aggregate_type="node_application",
            aggregate_id=application.id,
            aggregate_version=application.version + 1,
            actor=actor,
            payload={**payload, "node_id": str(node.id)},
            assurance=await federation_command_assurance(
                session,
                principal=principal,
                actor=actor,
                local_node_reference=self.settings.node_code,
                event_type="federation.node_identity_verified",
                subject_type="node_application",
                subject_id=application.id,
                target_node_id=node.id,
                command_record=record,
                evidence_refs=({"verification_summary": payload["verification_summary"]},),
                attester_user_ids=(application.created_by_user_id,),
            ),
        )
        now = datetime.now(UTC)
        application.status = node.status = "IDENTITY_VERIFIED"
        application.identity_verified_by_user_id = principal.user_id
        application.identity_verified_event_id = event.event_id
        application.identity_verified_at = now
        application.version += 1
        node.version += 1
        node.updated_at = now
        await audit_federation_action(
            session,
            principal,
            "NODE_IDENTITY_VERIFIED",
            "NodeApplication",
            application.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, application.id)

    async def issue_challenge(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        application_id: UUID,
        expected_version: int,
        protocol_version: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> ChallengeMaterial:
        actor = await federation_actor(session, principal, SECURITY_ROLES)
        payload = {
            "application_id": str(application_id),
            "expected_version": expected_version,
            "protocol_version": self._code(protocol_version, 32),
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.issue_challenge", idempotency_key, payload
        )
        if replay is not None:
            challenge = await session.get(NodeChallenge, replay.object_id)
            if challenge is None or not isinstance(challenge.challenge_payload.get("nonce"), str):
                raise federation_error("CHALLENGE_MATERIAL_UNAVAILABLE")
            return ChallengeMaterial(replay, str(challenge.challenge_payload["nonce"]))
        application = await self._locked(session, NodeApplication, application_id)
        node = await self._locked(session, ExternalNode, application.node_id)
        self._version(application.version, expected_version)
        if application.status != "IDENTITY_VERIFIED":
            raise federation_error("APPLICATION_STATE_INVALID")
        if protocol_version not in node.supported_protocols:
            raise federation_error("PROTOCOL_VERSION_UNSUPPORTED", 422)
        if await session.scalar(
            select(NodeChallenge.id).where(
                NodeChallenge.application_id == application.id,
                NodeChallenge.status == "ISSUED",
            )
        ):
            raise federation_error("CHALLENGE_ALREADY_ISSUED")
        certificate = await session.scalar(
            select(NodeCertificate).where(
                NodeCertificate.node_id == node.id, NodeCertificate.status == "PENDING"
            )
        )
        if certificate is None:
            raise federation_error("NODE_CERTIFICATE_MISSING")
        now, challenge_id, nonce = datetime.now(UTC), uuid4(), secrets.token_urlsafe(32)
        expires_at = now + timedelta(minutes=30)
        challenge_payload = {
            "challenge_id": str(challenge_id),
            "nonce": nonce,
            "source_node": self.settings.node_code,
            "target_node": node.node_code,
            "protocol_version": protocol_version,
            "issued_at": utc_timestamp(now),
            "expires_at": utc_timestamp(expires_at),
            "required_response": [
                "release_manifest",
                "capability_statement",
                "integrity_report",
                "test_package_receipt",
            ],
        }
        event = await self.journal.append(
            session,
            event_type="federation.node_challenge_issued",
            aggregate_type="node_challenge",
            aggregate_id=challenge_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "node_id": str(node.id),
                "nonce_hash": sha256_ref(nonce.encode()),
                "expires_at": utc_timestamp(expires_at),
            },
            assurance=await federation_command_assurance(
                session,
                principal=principal,
                actor=actor,
                local_node_reference=self.settings.node_code,
                event_type="federation.node_challenge_issued",
                subject_type="node_challenge",
                subject_id=challenge_id,
                target_node_id=node.id,
                command_record=record,
                evidence_refs=(
                    {
                        "certificate_fingerprint": certificate.fingerprint,
                        "nonce_hash": sha256_ref(nonce.encode()),
                    },
                ),
            ),
        )
        session.add(
            NodeChallenge(
                id=challenge_id,
                application_id=application.id,
                certificate_id=certificate.id,
                nonce_hash=sha256_ref(nonce.encode()),
                challenge_payload=challenge_payload,
                protocol_version=protocol_version,
                status="ISSUED",
                issued_by_user_id=principal.user_id,
                issued_event_id=event.event_id,
                issued_at=now,
                expires_at=expires_at,
            )
        )
        application.status = node.status = "TECHNICAL_CHALLENGE"
        application.version += 1
        node.version += 1
        node.updated_at = now
        await audit_federation_action(
            session,
            principal,
            "NODE_CHALLENGE_ISSUED",
            "NodeChallenge",
            challenge_id,
            event.event_id,
            request_id,
        )
        return ChallengeMaterial(
            complete_federation_command(record, event.event_id, challenge_id), nonce
        )

    async def record_challenge_response(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        challenge_id: UUID,
        nonce: str,
        response_payload: dict[str, object],
        signature: bytes,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, REGISTRAR_ROLES | SECURITY_ROLES)
        payload = {
            "challenge_id": str(challenge_id),
            "nonce_hash": sha256_ref(nonce.encode()),
            "response_hash": payload_hash(response_payload),
            "signature": base64.b64encode(signature).decode(),
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.record_challenge_response", idempotency_key, payload
        )
        if replay is not None:
            return replay
        challenge = await self._locked(session, NodeChallenge, challenge_id)
        application = await self._locked(session, NodeApplication, challenge.application_id)
        node = await self._locked(session, ExternalNode, application.node_id)
        certificate = await session.get(NodeCertificate, challenge.certificate_id)
        now = datetime.now(UTC)
        if (
            challenge.status != "ISSUED"
            or application.status != "TECHNICAL_CHALLENGE"
            or challenge.expires_at <= now
            or certificate is None
            or certificate.status != "PENDING"
        ):
            raise federation_error("CHALLENGE_STATE_INVALID")
        if challenge.nonce_hash != sha256_ref(nonce.encode()):
            raise federation_error("CHALLENGE_NONCE_INVALID", 422)
        required = {
            "release_manifest",
            "capability_statement",
            "integrity_report",
            "test_package_receipt",
        }
        if not required.issubset(response_payload):
            raise federation_error("CHALLENGE_RESPONSE_INCOMPLETE", 422)
        if not verify_signature(
            certificate.public_key,
            signature,
            challenge_message(challenge.id, nonce, response_payload),
        ):
            raise federation_error("CHALLENGE_SIGNATURE_INVALID", 422)
        event = await self.journal.append(
            session,
            event_type="federation.node_challenge_passed",
            aggregate_type="node_challenge",
            aggregate_id=challenge.id,
            aggregate_version=2,
            actor=actor,
            payload={
                "challenge_id": str(challenge.id),
                "node_id": str(node.id),
                "certificate_fingerprint": certificate.fingerprint,
                "response_hash": payload["response_hash"],
                "signature_verified": True,
            },
            assurance=await federation_command_assurance(
                session,
                principal=principal,
                actor=actor,
                local_node_reference=self.settings.node_code,
                event_type="federation.node_challenge_passed",
                subject_type="node_challenge",
                subject_id=challenge.id,
                target_node_id=node.id,
                command_record=record,
                evidence_refs=(
                    {
                        "certificate_fingerprint": certificate.fingerprint,
                        "response_hash": payload["response_hash"],
                        "signature_verified": True,
                    },
                ),
                attester_user_ids=(challenge.issued_by_user_id,),
            ),
        )
        challenge.status = "PASSED"
        challenge.response_signature = signature
        challenge.response_payload = response_payload
        challenge.response_event_id = event.event_id
        challenge.responded_at = now
        certificate.status = "ACTIVE"
        certificate.activated_event_id = event.event_id
        application.status = node.status = "AUDIT_PENDING"
        application.version += 1
        node.version += 1
        node.updated_at = now
        await audit_federation_action(
            session,
            principal,
            "NODE_CHALLENGE_PASSED",
            "NodeChallenge",
            challenge.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, challenge.id)

    async def decide_audit(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        application_id: UUID,
        expected_version: int,
        approve: bool,
        rationale: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, AUDIT_ROLES)
        payload = {
            "application_id": str(application_id),
            "expected_version": expected_version,
            "decision": "APPROVE" if approve else "REJECT",
            "rationale": self._text(rationale, 4000),
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.decide_node_audit", idempotency_key, payload
        )
        if replay is not None:
            return replay
        application = await self._locked(session, NodeApplication, application_id)
        node = await self._locked(session, ExternalNode, application.node_id)
        self._version(application.version, expected_version)
        if application.status != "AUDIT_PENDING" or application.audit_event_id is not None:
            raise federation_error("APPLICATION_STATE_INVALID")
        if principal.user_id in {
            application.created_by_user_id,
            application.identity_verified_by_user_id,
        }:
            raise federation_error("INDEPENDENT_REVIEW_REQUIRED", 403)
        challenge = await session.scalar(
            select(NodeChallenge).where(
                NodeChallenge.application_id == application.id,
                NodeChallenge.status == "PASSED",
            )
        )
        if challenge is None or challenge.issued_by_user_id == principal.user_id:
            raise federation_error("INDEPENDENT_REVIEW_REQUIRED", 403)
        event = await self.journal.append(
            session,
            event_type=(
                "federation.node_audit_approved"
                if approve
                else "federation.node_application_rejected"
            ),
            aggregate_type="node_application",
            aggregate_id=application.id,
            aggregate_version=application.version + 1,
            actor=actor,
            payload={**payload, "node_id": str(node.id)},
            assurance=await federation_command_assurance(
                session,
                principal=principal,
                actor=actor,
                local_node_reference=self.settings.node_code,
                event_type="federation.node_audit_approved"
                if approve
                else "federation.node_application_rejected",
                subject_type="node_application",
                subject_id=application.id,
                target_node_id=node.id,
                command_record=record,
                evidence_refs=(
                    {"challenge_id": str(challenge.id), "rationale": payload["rationale"]},
                ),
                attester_user_ids=(
                    application.created_by_user_id,
                    application.identity_verified_by_user_id,
                    challenge.issued_by_user_id,
                ),
            ),
        )
        now = datetime.now(UTC)
        application.audit_decided_by_user_id = principal.user_id
        application.audit_event_id = event.event_id
        application.audit_decided_at = now
        application.version += 1
        if not approve:
            application.status = node.status = "REJECTED"
            certificate = await session.scalar(
                select(NodeCertificate).where(
                    NodeCertificate.node_id == node.id, NodeCertificate.status == "ACTIVE"
                )
            )
            if certificate is not None:
                certificate.status = "REVOKED"
                certificate.revoked_event_id = event.event_id
                certificate.revoked_at = now
        node.version += 1
        node.updated_at = now
        await audit_federation_action(
            session,
            principal,
            "NODE_AUDIT_DECIDED",
            "NodeApplication",
            application.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, application.id)

    async def _validate_responsible_party(
        self,
        session: AsyncSession,
        item: ResponsiblePartyInput,
        trust_expiry: datetime,
    ) -> None:
        member = await session.get(Member, item.member_id)
        assignment = await session.get(RoleAssignment, item.role_assignment_id)
        allowed = RESPONSIBILITY_ASSIGNMENT_ROLES[item.role_code]
        if (
            member is None
            or member.status != "ACTIVE"
            or assignment is None
            or assignment.status != "ACTIVE"
            or assignment.role_code not in {role.value for role in allowed}
        ):
            raise federation_error("RESPONSIBLE_PARTY_INVALID", 422)
        if item.valid_until is not None:
            valid_until = item.valid_until.astimezone(UTC)
            if valid_until <= datetime.now(UTC) or valid_until < trust_expiry:
                raise federation_error("RESPONSIBILITY_PERIOD_INVALID", 422)
        requested = {scope.value for scope in item.capability_scope}
        if not requested:
            raise federation_error("RESPONSIBILITY_CAPABILITY_REQUIRED", 422)

    async def _local_node(self, session: AsyncSession) -> NodeProfile:
        profile = await session.scalar(
            select(NodeProfile).where(NodeProfile.node_code == self.settings.node_code)
        )
        if profile is None:
            raise federation_error("LOCAL_NODE_NOT_INITIALIZED", 503)
        return profile

    @staticmethod
    async def _locked(session: AsyncSession, model: type[ModelT], object_id: UUID) -> ModelT:
        item = await session.scalar(
            select(model).where(cast(Any, model).id == object_id).with_for_update()
        )
        if item is None:
            raise federation_error("OBJECT_NOT_FOUND", 404)
        return item

    @staticmethod
    def _version(actual: int, expected: int) -> None:
        if actual != expected:
            raise federation_error("VERSION_CONFLICT")

    @staticmethod
    def _text(value: str, maximum: int) -> str:
        result = value.strip()
        if not result or len(result) > maximum:
            raise federation_error("TEXT_INVALID", 422)
        return result

    @staticmethod
    def _code(value: str, maximum: int) -> str:
        result = value.strip().upper()
        if (
            not result
            or len(result) > maximum
            or not result.isascii()
            or not all(character.isalnum() or character in {"_", "-", "."} for character in result)
        ):
            raise federation_error("CODE_INVALID", 422)
        return result
