"""Trust contracts, bilateral exposure, key custody, incidents, and offline epochs."""

import base64
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.federation.application.common import (
    FederationCommandResult,
    audit_federation_action,
    begin_federation_command,
    complete_federation_command,
    federation_actor,
)
from cooperative_clearing.modules.federation.application.service import (
    AUDIT_ROLES,
    REGISTRAR_ROLES,
    SECURITY_ROLES,
    FederationService,
    rotation_message,
)
from cooperative_clearing.modules.federation.domain.types import (
    NodeCapability,
    TrustLevel,
    bounded_amount,
    federation_error,
    preview_exposure,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    FederationPaperForm,
    NodeApplication,
    NodeBilateralLimit,
    NodeBond,
    NodeCertificate,
    NodeExposure,
    NodeKeyRotationRequest,
    NodeResponsibleParty,
    NodeSecurityIncident,
    NodeTrustContract,
    OfflineEpoch,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.journal.domain.assurance import (
    CommandAssurance,
    ExposureCategory,
    ExposureClaim,
    ExposureEffect,
)
from cooperative_clearing.modules.journal.domain.crypto import (
    payload_hash,
    sha256_ref,
    utc_timestamp,
    verify_signature,
)


class NodeTrustService(FederationService):
    async def propose_trust_contract(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        application_id: UUID,
        contract_number: str,
        trust_level: TrustLevel,
        capabilities: tuple[NodeCapability, ...],
        event_types: list[str],
        inbound_scope: dict[str, object],
        outbound_scope: dict[str, object],
        federation_limits: dict[str, object],
        allowed_counterparties: list[str],
        max_offline_hours: int,
        required_protocols: list[str],
        required_policies: dict[str, int],
        service_levels: dict[str, object],
        liability_terms: dict[str, object],
        valid_from: datetime,
        valid_until: datetime,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, REGISTRAR_ROLES)
        capability_values = sorted({item.value for item in capabilities})
        start, end = valid_from.astimezone(UTC), valid_until.astimezone(UTC)
        if (
            trust_level is TrustLevel.UNTRUSTED
            or not capability_values
            or not event_types
            or not required_protocols
            or not 1 <= max_offline_hours <= 720
            or end <= start
        ):
            raise federation_error("TRUST_CONTRACT_TERMS_INVALID", 422)
        terms = {
            "application_id": str(application_id),
            "contract_number": self._code(contract_number, 80),
            "trust_level": trust_level.value,
            "capabilities": capability_values,
            "event_types": sorted(set(event_types)),
            "inbound_scope": inbound_scope,
            "outbound_scope": outbound_scope,
            "federation_limits": federation_limits,
            "allowed_counterparties": sorted(set(allowed_counterparties)),
            "max_offline_hours": max_offline_hours,
            "required_protocols": sorted(set(required_protocols)),
            "required_policies": required_policies,
            "service_levels": service_levels,
            "liability_terms": liability_terms,
            "valid_from": utc_timestamp(start),
            "valid_until": utc_timestamp(end),
        }
        terms_hash = payload_hash(terms)
        record, replay = await begin_federation_command(
            session, principal, "federation.propose_trust_contract", idempotency_key, terms
        )
        if replay is not None:
            return replay
        application = await self._locked(session, NodeApplication, application_id)
        node = await self._locked(session, ExternalNode, application.node_id)
        if (
            application.status != "AUDIT_PENDING"
            or application.audit_event_id is None
            or node.status != "AUDIT_PENDING"
            or end > application.proposed_trust_expiry
            or not set(capability_values).issubset(application.requested_capabilities)
            or not set(required_protocols).issubset(node.supported_protocols)
            or any(
                node.supported_policies.get(key) != value
                for key, value in required_policies.items()
            )
        ):
            raise federation_error("TRUST_CONTRACT_NOT_ALLOWED")
        if await session.scalar(
            select(NodeTrustContract.id).where(
                NodeTrustContract.node_id == node.id,
                NodeTrustContract.status.in_(("DRAFT", "ACTIVE")),
            )
        ):
            raise federation_error("TRUST_CONTRACT_ALREADY_OPEN")
        contract_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="federation.trust_contract_proposed",
            aggregate_type="node_trust_contract",
            aggregate_id=contract_id,
            aggregate_version=1,
            actor=actor,
            payload={**terms, "node_id": str(node.id), "terms_hash": terms_hash},
        )
        session.add(
            NodeTrustContract(
                id=contract_id,
                node_id=node.id,
                application_id=application.id,
                contract_number=str(terms["contract_number"]),
                trust_level=trust_level.value,
                capabilities=capability_values,
                event_types=sorted(set(event_types)),
                inbound_scope=inbound_scope,
                outbound_scope=outbound_scope,
                federation_limits=federation_limits,
                allowed_counterparties=sorted(set(allowed_counterparties)),
                max_offline_hours=max_offline_hours,
                required_protocols=sorted(set(required_protocols)),
                required_policies=required_policies,
                service_levels=service_levels,
                liability_terms=liability_terms,
                terms_hash=terms_hash,
                status="DRAFT",
                valid_from=start,
                valid_until=end,
                proposed_by_user_id=principal.user_id,
                proposed_event_id=event.event_id,
            )
        )
        await audit_federation_action(
            session,
            principal,
            "NODE_TRUST_CONTRACT_PROPOSED",
            "NodeTrustContract",
            contract_id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, contract_id)

    async def approve_trust_contract(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        contract_id: UUID,
        expected_version: int,
        terms_hash: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, AUDIT_ROLES)
        payload = {
            "contract_id": str(contract_id),
            "expected_version": expected_version,
            "terms_hash": terms_hash,
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.approve_trust_contract", idempotency_key, payload
        )
        if replay is not None:
            return replay
        contract = await self._locked(session, NodeTrustContract, contract_id)
        node = await self._locked(session, ExternalNode, contract.node_id)
        application = await self._locked(session, NodeApplication, contract.application_id)
        self._version(contract.version, expected_version)
        if (
            contract.status != "DRAFT"
            or contract.terms_hash != terms_hash
            or contract.proposed_by_user_id == principal.user_id
            or application.status != "AUDIT_PENDING"
            or node.status != "AUDIT_PENDING"
            or not (contract.valid_from <= datetime.now(UTC) < contract.valid_until)
        ):
            raise federation_error("TRUST_CONTRACT_APPROVAL_INVALID")
        certificate = await session.scalar(
            select(NodeCertificate.id).where(
                NodeCertificate.node_id == node.id,
                NodeCertificate.status == "ACTIVE",
                NodeCertificate.valid_until > datetime.now(UTC),
            )
        )
        if certificate is None:
            raise federation_error("ACTIVE_NODE_CERTIFICATE_REQUIRED")
        event = await self.journal.append(
            session,
            event_type="federation.trust_contract_activated",
            aggregate_type="node_trust_contract",
            aggregate_id=contract.id,
            aggregate_version=contract.version + 1,
            actor=actor,
            payload={**payload, "node_id": str(node.id), "trust_level": contract.trust_level},
        )
        now = datetime.now(UTC)
        contract.status = "ACTIVE"
        contract.approved_by_user_id = principal.user_id
        contract.approved_event_id = event.event_id
        contract.approved_at = now
        contract.version += 1
        application.status = node.status = "LIMITED"
        application.version += 1
        node.trust_level = "LIMITED"
        node.version += 1
        node.updated_at = now
        await audit_federation_action(
            session,
            principal,
            "NODE_TRUST_CONTRACT_ACTIVATED",
            "NodeTrustContract",
            contract.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, contract.id)

    async def propose_bilateral_limit(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        node_id: UUID,
        capability: NodeCapability,
        unit: str,
        max_package_value: Decimal,
        max_unsettled_obligations: Decimal,
        max_external_rights: Decimal,
        max_clearing_position: Decimal,
        max_offline_hours: int,
        allowed_critical_resources: list[str],
        required_confirmations: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, REGISTRAR_ROLES)
        amounts = {
            "max_package_value": str(bounded_amount(max_package_value)),
            "max_unsettled_obligations": str(bounded_amount(max_unsettled_obligations)),
            "max_external_rights": str(bounded_amount(max_external_rights)),
            "max_clearing_position": str(bounded_amount(max_clearing_position)),
        }
        resource_codes = sorted({self._code(item, 64) for item in allowed_critical_resources})
        payload = {
            "node_id": str(node_id),
            "capability": capability.value,
            "unit": self._code(unit, 32),
            **amounts,
            "max_offline_hours": max_offline_hours,
            "allowed_critical_resources": resource_codes,
            "required_confirmations": required_confirmations,
        }
        if not 1 <= max_offline_hours <= 720 or not 1 <= required_confirmations <= 10:
            raise federation_error("BILATERAL_LIMIT_INVALID", 422)
        terms_hash = payload_hash(payload)
        record, replay = await begin_federation_command(
            session, principal, "federation.propose_bilateral_limit", idempotency_key, payload
        )
        if replay is not None:
            return replay
        node = await self._locked(session, ExternalNode, node_id)
        contract = await self._active_contract(session, node.id)
        if (
            node.status not in {"LIMITED", "ACTIVE"}
            or capability.value not in contract.capabilities
        ):
            raise federation_error("BILATERAL_LIMIT_NOT_ALLOWED")
        if await session.scalar(
            select(NodeBilateralLimit.id).where(
                NodeBilateralLimit.node_id == node.id,
                NodeBilateralLimit.capability == capability.value,
                NodeBilateralLimit.status == "DRAFT",
            )
        ):
            raise federation_error("BILATERAL_LIMIT_DRAFT_EXISTS")
        limit_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="federation.bilateral_limit_proposed",
            aggregate_type="node_bilateral_limit",
            aggregate_id=limit_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "terms_hash": terms_hash},
        )
        session.add(
            NodeBilateralLimit(
                id=limit_id,
                node_id=node.id,
                capability=capability.value,
                unit=str(payload["unit"]),
                max_package_value=Decimal(amounts["max_package_value"]),
                max_unsettled_obligations=Decimal(amounts["max_unsettled_obligations"]),
                max_external_rights=Decimal(amounts["max_external_rights"]),
                max_clearing_position=Decimal(amounts["max_clearing_position"]),
                max_offline_hours=max_offline_hours,
                allowed_critical_resources=resource_codes,
                required_confirmations=required_confirmations,
                terms_hash=terms_hash,
                status="DRAFT",
                proposed_by_user_id=principal.user_id,
                proposed_event_id=event.event_id,
            )
        )
        await audit_federation_action(
            session,
            principal,
            "NODE_BILATERAL_LIMIT_PROPOSED",
            "NodeBilateralLimit",
            limit_id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, limit_id)

    async def approve_bilateral_limit(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        limit_id: UUID,
        expected_version: int,
        terms_hash: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, AUDIT_ROLES | SECURITY_ROLES)
        payload = {
            "limit_id": str(limit_id),
            "expected_version": expected_version,
            "terms_hash": terms_hash,
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.approve_bilateral_limit", idempotency_key, payload
        )
        if replay is not None:
            return replay
        limit = await self._locked(session, NodeBilateralLimit, limit_id)
        self._version(limit.version, expected_version)
        if (
            limit.status != "DRAFT"
            or limit.terms_hash != terms_hash
            or limit.proposed_by_user_id == principal.user_id
        ):
            raise federation_error("BILATERAL_LIMIT_APPROVAL_INVALID")
        exposure = await session.scalar(
            select(NodeExposure).where(
                NodeExposure.node_id == limit.node_id,
                NodeExposure.capability == limit.capability,
                NodeExposure.unit == limit.unit,
            )
        )
        if (
            exposure is not None
            and exposure.current_amount + exposure.reserved_amount > limit.max_unsettled_obligations
        ):
            raise federation_error("BILATERAL_LIMIT_BELOW_EXISTING_EXPOSURE")
        old_limits = list(
            (
                await session.execute(
                    select(NodeBilateralLimit)
                    .where(
                        NodeBilateralLimit.node_id == limit.node_id,
                        NodeBilateralLimit.capability == limit.capability,
                        NodeBilateralLimit.status == "ACTIVE",
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        event = await self.journal.append(
            session,
            event_type="federation.bilateral_limit_activated",
            aggregate_type="node_bilateral_limit",
            aggregate_id=limit.id,
            aggregate_version=limit.version + 1,
            actor=actor,
            payload={
                **payload,
                "node_id": str(limit.node_id),
                "retired_limit_ids": [str(item.id) for item in old_limits],
            },
        )
        now = datetime.now(UTC)
        for old in old_limits:
            old.status = "RETIRED"
            old.version += 1
        limit.status = "ACTIVE"
        limit.approved_by_user_id = principal.user_id
        limit.approved_event_id = event.event_id
        limit.approved_at = now
        limit.version += 1
        await audit_federation_action(
            session,
            principal,
            "NODE_BILATERAL_LIMIT_ACTIVATED",
            "NodeBilateralLimit",
            limit.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, limit.id)

    async def register_bond(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        node_id: UUID,
        reference: str,
        amount: Decimal,
        protected_amount: Decimal,
        maximum_loss: Decimal,
        unit: str,
        capability_scope: tuple[NodeCapability, ...],
        evidence_ids: list[UUID],
        valid_from: datetime,
        valid_until: datetime,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, REGISTRAR_ROLES | SECURITY_ROLES)
        total = bounded_amount(amount, allow_zero=False)
        protected = bounded_amount(protected_amount)
        loss = bounded_amount(maximum_loss, allow_zero=False)
        start, end = valid_from.astimezone(UTC), valid_until.astimezone(UTC)
        if protected >= total or loss > total - protected or end <= start or not evidence_ids:
            raise federation_error("NODE_BOND_INVALID", 422)
        scope_values = sorted({item.value for item in capability_scope})
        evidence_values = sorted(str(item) for item in evidence_ids)
        payload = {
            "node_id": str(node_id),
            "reference": self._code(reference, 160),
            "amount": str(total),
            "protected_amount": str(protected),
            "maximum_loss": str(loss),
            "unit": self._code(unit, 32),
            "capability_scope": scope_values,
            "evidence_ids": evidence_values,
            "valid_from": utc_timestamp(start),
            "valid_until": utc_timestamp(end),
            "ordinary_member_shares_excluded": True,
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.register_node_bond", idempotency_key, payload
        )
        if replay is not None:
            return replay
        node = await self._locked(session, ExternalNode, node_id)
        if node.status not in {"LIMITED", "ACTIVE"}:
            raise federation_error("NODE_BOND_NOT_ALLOWED")
        bond_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="federation.node_bond_activated",
            aggregate_type="node_bond",
            aggregate_id=bond_id,
            aggregate_version=1,
            actor=actor,
            payload=payload,
            evidence=[{"evidence_id": str(item)} for item in evidence_ids],
        )
        session.add(
            NodeBond(
                id=bond_id,
                node_id=node.id,
                provider_organization_id=node.owner_organization_id,
                reference=str(payload["reference"]),
                amount=total,
                protected_amount=protected,
                maximum_loss=loss,
                unit=str(payload["unit"]),
                capability_scope=scope_values,
                evidence_ids=evidence_values,
                status="ACTIVE",
                valid_from=start,
                valid_until=end,
                event_id=event.event_id,
            )
        )
        await audit_federation_action(
            session,
            principal,
            "NODE_BOND_ACTIVATED",
            "NodeBond",
            bond_id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, bond_id)

    async def activate_node(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        node_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, REGISTRAR_ROLES | AUDIT_ROLES)
        payload = {"node_id": str(node_id), "expected_version": expected_version}
        record, replay = await begin_federation_command(
            session, principal, "federation.activate_node", idempotency_key, payload
        )
        if replay is not None:
            return replay
        node = await self._locked(session, ExternalNode, node_id)
        self._version(node.version, expected_version)
        contract = await self._active_contract(session, node.id)
        if (
            node.status != "LIMITED"
            or contract.valid_until <= datetime.now(UTC)
            or contract.approved_by_user_id == principal.user_id
        ):
            raise federation_error("NODE_ACTIVATION_INVALID")
        role_codes = set(
            (
                await session.execute(
                    select(NodeResponsibleParty.role_code).where(
                        NodeResponsibleParty.node_id == node.id,
                        NodeResponsibleParty.status == "ACTIVE",
                        (NodeResponsibleParty.valid_until.is_(None))
                        | (NodeResponsibleParty.valid_until > datetime.now(UTC)),
                    )
                )
            ).scalars()
        )
        if not {
            "OWNER_SIGNATORY",
            "TECHNICAL_CUSTODIAN",
            "SECURITY_ADMINISTRATOR",
            "BUSINESS_OPERATOR",
            "NODE_AUDITOR",
        }.issubset(role_codes):
            raise federation_error("NODE_RESPONSIBLE_ROLES_INCOMPLETE")
        if not await session.scalar(
            select(NodeBilateralLimit.id).where(
                NodeBilateralLimit.node_id == node.id,
                NodeBilateralLimit.status == "ACTIVE",
            )
        ):
            raise federation_error("ACTIVE_BILATERAL_LIMIT_REQUIRED")
        if not await session.scalar(
            select(NodeBond.id).where(
                NodeBond.node_id == node.id,
                NodeBond.status == "ACTIVE",
                NodeBond.valid_until > datetime.now(UTC),
            )
        ):
            raise federation_error("ACTIVE_NODE_BOND_REQUIRED")
        if await session.scalar(
            select(NodeSecurityIncident.id).where(
                NodeSecurityIncident.node_id == node.id,
                NodeSecurityIncident.status.in_(("OPEN", "CONTAINED", "APPEALED")),
            )
        ):
            raise federation_error("OPEN_NODE_INCIDENT")
        event = await self.journal.append(
            session,
            event_type="federation.node_activated",
            aggregate_type="external_node",
            aggregate_id=node.id,
            aggregate_version=node.version + 1,
            actor=actor,
            payload={
                **payload,
                "contract_id": str(contract.id),
                "capabilities": contract.capabilities,
                "responsible_roles": sorted(role_codes),
            },
        )
        application = await self._locked(session, NodeApplication, contract.application_id)
        now = datetime.now(UTC)
        node.status = application.status = "ACTIVE"
        node.trust_level = contract.trust_level
        node.version += 1
        node.updated_at = now
        application.version += 1
        await audit_federation_action(
            session,
            principal,
            "EXTERNAL_NODE_ACTIVATED",
            "ExternalNode",
            node.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, node.id)

    async def change_node_status(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        node_id: UUID,
        expected_version: int,
        action: str,
        rationale: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, SECURITY_ROLES | REGISTRAR_ROLES)
        target_by_action = {
            "suspend": "SUSPENDED",
            "quarantine": "QUARANTINED",
            "revoke": "REVOKED",
        }
        if action not in target_by_action:
            raise federation_error("NODE_STATUS_ACTION_INVALID", 422)
        payload = {
            "node_id": str(node_id),
            "expected_version": expected_version,
            "action": action,
            "rationale": self._text(rationale, 4000),
        }
        record, replay = await begin_federation_command(
            session, principal, f"federation.{action}_node", idempotency_key, payload
        )
        if replay is not None:
            return replay
        node = await self._locked(session, ExternalNode, node_id)
        self._version(node.version, expected_version)
        if node.status in {"REVOKED", "ARCHIVED", "REJECTED"}:
            raise federation_error("NODE_STATUS_TRANSITION_INVALID")
        event = await self.journal.append(
            session,
            event_type=f"federation.node_{action}d",
            aggregate_type="external_node",
            aggregate_id=node.id,
            aggregate_version=node.version + 1,
            actor=actor,
            payload={**payload, "previous_status": node.status},
        )
        now = datetime.now(UTC)
        node.status = target_by_action[action]
        node.version += 1
        node.updated_at = now
        if action in {"quarantine", "revoke"}:
            certificates = list(
                (
                    await session.execute(
                        select(NodeCertificate)
                        .where(
                            NodeCertificate.node_id == node.id,
                            NodeCertificate.status.in_(("ACTIVE", "ROTATING", "PENDING")),
                        )
                        .with_for_update()
                    )
                ).scalars()
            )
            for certificate in certificates:
                certificate.status = "REVOKED" if action == "revoke" else "SUSPENDED"
                if action == "revoke":
                    certificate.revoked_event_id = event.event_id
                    certificate.revoked_at = now
        await audit_federation_action(
            session,
            principal,
            f"EXTERNAL_NODE_{target_by_action[action]}",
            "ExternalNode",
            node.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, node.id)

    async def open_incident(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        node_id: UUID,
        incident_type: str,
        severity: str,
        earliest_compromise_at: datetime | None,
        description: str,
        evidence_ids: list[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, SECURITY_ROLES)
        severity_value = severity.strip().upper()
        if severity_value not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            raise federation_error("INCIDENT_SEVERITY_INVALID", 422)
        evidence_values = sorted(str(item) for item in evidence_ids)
        payload = {
            "node_id": str(node_id),
            "incident_type": self._code(incident_type, 80),
            "severity": severity_value,
            "earliest_compromise_at": (
                utc_timestamp(earliest_compromise_at)
                if earliest_compromise_at is not None
                else None
            ),
            "description": self._text(description, 8000),
            "evidence_ids": evidence_values,
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.open_node_incident", idempotency_key, payload
        )
        if replay is not None:
            return replay
        node = await self._locked(session, ExternalNode, node_id)
        if node.status in {"REVOKED", "ARCHIVED", "REJECTED"}:
            raise federation_error("NODE_INCIDENT_NOT_ALLOWED")
        incident_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="federation.node_incident_opened",
            aggregate_type="node_security_incident",
            aggregate_id=incident_id,
            aggregate_version=1,
            actor=actor,
            payload={**payload, "previous_node_status": node.status},
            evidence=[{"evidence_id": str(item)} for item in evidence_ids],
        )
        now = datetime.now(UTC)
        session.add(
            NodeSecurityIncident(
                id=incident_id,
                node_id=node.id,
                incident_type=str(payload["incident_type"]),
                severity=severity_value,
                status="CONTAINED",
                earliest_compromise_at=earliest_compromise_at,
                description=str(payload["description"]),
                evidence_ids=evidence_values,
                containment_payload={
                    "node_quarantined": True,
                    "new_packages_blocked": True,
                    "peer_notification_required": True,
                },
                corrective_actions=[],
                opened_by_user_id=principal.user_id,
                opened_event_id=event.event_id,
            )
        )
        node.status = "QUARANTINED"
        node.version += 1
        node.updated_at = now
        certificates = list(
            (
                await session.execute(
                    select(NodeCertificate)
                    .where(
                        NodeCertificate.node_id == node.id,
                        NodeCertificate.status == "ACTIVE",
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        for certificate in certificates:
            certificate.status = (
                "COMPROMISED" if str(payload["incident_type"]) == "KEY_COMPROMISE" else "SUSPENDED"
            )
        await audit_federation_action(
            session,
            principal,
            "NODE_SECURITY_INCIDENT_OPENED",
            "NodeSecurityIncident",
            incident_id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, incident_id)

    async def resolve_incident(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        incident_id: UUID,
        expected_version: int,
        corrective_actions: list[object],
        rationale: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, AUDIT_ROLES)
        payload = {
            "incident_id": str(incident_id),
            "expected_version": expected_version,
            "corrective_actions": corrective_actions,
            "rationale": self._text(rationale, 4000),
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.resolve_node_incident", idempotency_key, payload
        )
        if replay is not None:
            return replay
        incident = await self._locked(session, NodeSecurityIncident, incident_id)
        self._version(incident.version, expected_version)
        if (
            incident.status not in {"OPEN", "CONTAINED"}
            or incident.opened_by_user_id == principal.user_id
            or not corrective_actions
        ):
            raise federation_error("INCIDENT_RESOLUTION_INVALID")
        event = await self.journal.append(
            session,
            event_type="federation.node_incident_resolved",
            aggregate_type="node_security_incident",
            aggregate_id=incident.id,
            aggregate_version=incident.version + 1,
            actor=actor,
            payload={**payload, "node_id": str(incident.node_id)},
        )
        incident.status = "RESOLVED"
        incident.corrective_actions = corrective_actions
        incident.resolved_by_user_id = principal.user_id
        incident.resolved_event_id = event.event_id
        incident.resolved_at = datetime.now(UTC)
        incident.version += 1
        await audit_federation_action(
            session,
            principal,
            "NODE_SECURITY_INCIDENT_RESOLVED",
            "NodeSecurityIncident",
            incident.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, incident.id)

    async def request_key_rotation(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        node_id: UUID,
        new_public_key: bytes,
        valid_from: datetime,
        valid_until: datetime,
        reason: str,
        old_signature: bytes | None,
        new_signature: bytes,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, SECURITY_ROLES)
        reason_value = reason.strip().upper()
        if reason_value not in {"SCHEDULED", "COMPROMISE", "CUSTODY_CHANGE", "RECOVERY"}:
            raise federation_error("KEY_ROTATION_REASON_INVALID", 422)
        start, end = valid_from.astimezone(UTC), valid_until.astimezone(UTC)
        if len(new_public_key) != 32 or end <= start or end <= datetime.now(UTC):
            raise federation_error("NODE_CERTIFICATE_PERIOD_INVALID", 422)
        payload = {
            "node_id": str(node_id),
            "new_fingerprint": sha256_ref(new_public_key),
            "valid_from": utc_timestamp(start),
            "valid_until": utc_timestamp(end),
            "reason": reason_value,
            "old_signature": base64.b64encode(old_signature).decode() if old_signature else None,
            "new_signature": base64.b64encode(new_signature).decode(),
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.request_key_rotation", idempotency_key, payload
        )
        if replay is not None:
            return replay
        node = await self._locked(session, ExternalNode, node_id)
        old = await session.scalar(
            select(NodeCertificate)
            .where(
                NodeCertificate.node_id == node.id,
                NodeCertificate.status.in_(("ACTIVE", "SUSPENDED", "COMPROMISED")),
            )
            .order_by(NodeCertificate.created_at.desc())
            .with_for_update()
        )
        if old is None:
            raise federation_error("ACTIVE_NODE_CERTIFICATE_REQUIRED")
        message = rotation_message(
            node_id=node.id,
            old_fingerprint=old.fingerprint,
            new_fingerprint=sha256_ref(new_public_key),
            reason=reason_value,
            valid_from=start,
            valid_until=end,
        )
        if not verify_signature(new_public_key, new_signature, message):
            raise federation_error("NEW_KEY_PROOF_INVALID", 422)
        continuity_verified = old_signature is not None and verify_signature(
            old.public_key, old_signature, message
        )
        if reason_value != "COMPROMISE" and not continuity_verified:
            raise federation_error("OLD_KEY_CONTINUITY_REQUIRED", 422)
        if reason_value == "COMPROMISE" and not await session.scalar(
            select(NodeSecurityIncident.id).where(
                NodeSecurityIncident.node_id == node.id,
                NodeSecurityIncident.incident_type == "KEY_COMPROMISE",
                NodeSecurityIncident.status.in_(("OPEN", "CONTAINED")),
            )
        ):
            raise federation_error("KEY_COMPROMISE_INCIDENT_REQUIRED")
        rotation_id, certificate_id = uuid4(), uuid4()
        event = await self.journal.append(
            session,
            event_type="federation.node_key_rotation_requested",
            aggregate_type="node_key_rotation",
            aggregate_id=rotation_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "old_fingerprint": old.fingerprint,
                "continuity_verified": continuity_verified,
            },
        )
        session.add(
            NodeCertificate(
                id=certificate_id,
                node_id=node.id,
                algorithm="Ed25519",
                public_key=new_public_key,
                fingerprint=sha256_ref(new_public_key),
                status="PENDING",
                valid_from=start,
                valid_until=end,
            )
        )
        session.add(
            NodeKeyRotationRequest(
                id=rotation_id,
                node_id=node.id,
                old_certificate_id=old.id,
                new_certificate_id=certificate_id,
                reason=reason_value,
                status="PENDING",
                requested_by_user_id=principal.user_id,
                requested_event_id=event.event_id,
                continuity_verified=continuity_verified,
            )
        )
        await audit_federation_action(
            session,
            principal,
            "NODE_KEY_ROTATION_REQUESTED",
            "NodeKeyRotationRequest",
            rotation_id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, rotation_id)

    async def approve_key_rotation(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        rotation_id: UUID,
        expected_version: int,
        approve: bool,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, AUDIT_ROLES | REGISTRAR_ROLES)
        payload = {
            "rotation_id": str(rotation_id),
            "expected_version": expected_version,
            "decision": "APPROVE" if approve else "REJECT",
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.approve_key_rotation", idempotency_key, payload
        )
        if replay is not None:
            return replay
        rotation = await self._locked(session, NodeKeyRotationRequest, rotation_id)
        self._version(rotation.version, expected_version)
        if rotation.status != "PENDING" or rotation.requested_by_user_id == principal.user_id:
            raise federation_error("KEY_ROTATION_DECISION_INVALID")
        old = await self._locked(session, NodeCertificate, rotation.old_certificate_id)
        new = await self._locked(session, NodeCertificate, rotation.new_certificate_id)
        event = await self.journal.append(
            session,
            event_type=(
                "federation.node_key_rotated"
                if approve
                else "federation.node_key_rotation_rejected"
            ),
            aggregate_type="node_key_rotation",
            aggregate_id=rotation.id,
            aggregate_version=rotation.version + 1,
            actor=actor,
            payload={
                **payload,
                "node_id": str(rotation.node_id),
                "old_fingerprint": old.fingerprint,
                "new_fingerprint": new.fingerprint,
            },
        )
        now = datetime.now(UTC)
        rotation.status = "APPROVED" if approve else "REJECTED"
        rotation.decided_by_user_id = principal.user_id
        rotation.decided_event_id = event.event_id
        rotation.decided_at = now
        rotation.version += 1
        if approve:
            old.status = "COMPROMISED" if rotation.reason == "COMPROMISE" else "RETIRED"
            old.revoked_event_id = event.event_id
            old.revoked_at = now
            new.status = "ACTIVE"
            new.activated_event_id = event.event_id
        else:
            new.status = "REVOKED"
            new.revoked_event_id = event.event_id
            new.revoked_at = now
        await audit_federation_action(
            session,
            principal,
            "NODE_KEY_ROTATION_DECIDED",
            "NodeKeyRotationRequest",
            rotation.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, rotation.id)

    async def rehabilitate_node(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        node_id: UUID,
        expected_version: int,
        integrity_summary: dict[str, object],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, AUDIT_ROLES)
        payload = {
            "node_id": str(node_id),
            "expected_version": expected_version,
            "integrity_summary": integrity_summary,
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.rehabilitate_node", idempotency_key, payload
        )
        if replay is not None:
            return replay
        node = await self._locked(session, ExternalNode, node_id)
        self._version(node.version, expected_version)
        if node.status != "QUARANTINED":
            raise federation_error("NODE_REHABILITATION_INVALID")
        if await session.scalar(
            select(NodeSecurityIncident.id).where(
                NodeSecurityIncident.node_id == node.id,
                NodeSecurityIncident.status.in_(("OPEN", "CONTAINED", "APPEALED")),
            )
        ):
            raise federation_error("OPEN_NODE_INCIDENT")
        active_certificate = await session.scalar(
            select(NodeCertificate.id).where(
                NodeCertificate.node_id == node.id,
                NodeCertificate.status == "ACTIVE",
                NodeCertificate.valid_until > datetime.now(UTC),
            )
        )
        if active_certificate is None:
            suspended = await session.scalar(
                select(NodeCertificate)
                .where(
                    NodeCertificate.node_id == node.id,
                    NodeCertificate.status == "SUSPENDED",
                    NodeCertificate.valid_until > datetime.now(UTC),
                )
                .order_by(NodeCertificate.created_at.desc())
                .with_for_update()
            )
            if suspended is None:
                raise federation_error("ACTIVE_NODE_CERTIFICATE_REQUIRED")
            suspended.status = "ACTIVE"
            active_certificate = suspended.id
        event = await self.journal.append(
            session,
            event_type="federation.node_rehabilitated_limited",
            aggregate_type="external_node",
            aggregate_id=node.id,
            aggregate_version=node.version + 1,
            actor=actor,
            payload={
                **payload,
                "certificate_id": str(active_certificate),
                "target_status": "LIMITED",
            },
        )
        node.status = "LIMITED"
        node.trust_level = "LIMITED"
        node.version += 1
        node.updated_at = datetime.now(UTC)
        await audit_federation_action(
            session,
            principal,
            "EXTERNAL_NODE_REHABILITATED_LIMITED",
            "ExternalNode",
            node.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, node.id)

    async def open_offline_epoch(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        node_id: UUID,
        base_checkpoint_hash: str | None,
        allowed_event_types: list[str],
        limits: dict[str, object],
        protocol_version: str,
        policy_versions: dict[str, int],
        emergency_contacts: list[object],
        closure_rules: dict[str, object],
        starts_at: datetime,
        expires_at: datetime,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, REGISTRAR_ROLES | SECURITY_ROLES)
        start, end = starts_at.astimezone(UTC), expires_at.astimezone(UTC)
        policy = {
            "node_id": str(node_id),
            "base_checkpoint_hash": base_checkpoint_hash,
            "allowed_event_types": sorted(set(allowed_event_types)),
            "limits": limits,
            "protocol_version": protocol_version,
            "policy_versions": policy_versions,
            "emergency_contacts": emergency_contacts,
            "closure_rules": closure_rules,
            "starts_at": utc_timestamp(start),
            "expires_at": utc_timestamp(end),
        }
        policy_hash = payload_hash(policy)
        record, replay = await begin_federation_command(
            session, principal, "federation.open_offline_epoch", idempotency_key, policy
        )
        if replay is not None:
            return replay
        node = await self._locked(session, ExternalNode, node_id)
        contract = await self._active_contract(session, node.id)
        if (
            node.status not in {"LIMITED", "ACTIVE"}
            or end <= start
            or end > start + timedelta(hours=contract.max_offline_hours)
            or end > contract.valid_until
            or protocol_version not in contract.required_protocols
            or not set(allowed_event_types).issubset(contract.event_types)
            or any(
                contract.required_policies.get(key) != value
                for key, value in policy_versions.items()
            )
        ):
            raise federation_error("OFFLINE_EPOCH_POLICY_INVALID")
        if await session.scalar(
            select(OfflineEpoch.id).where(
                OfflineEpoch.external_node_id == node.id,
                OfflineEpoch.status == "OPEN",
            )
        ):
            raise federation_error("OFFLINE_EPOCH_ALREADY_OPEN")
        epoch_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="federation.offline_epoch_opened",
            aggregate_type="offline_epoch",
            aggregate_id=epoch_id,
            aggregate_version=1,
            actor=actor,
            payload={**policy, "policy_hash": policy_hash},
        )
        session.add(
            OfflineEpoch(
                id=epoch_id,
                local_node_id=None,
                external_node_id=node.id,
                base_checkpoint_hash=base_checkpoint_hash,
                allowed_event_types=sorted(set(allowed_event_types)),
                limits=limits,
                protocol_version=protocol_version,
                policy_versions=policy_versions,
                emergency_contacts=emergency_contacts,
                closure_rules=closure_rules,
                policy_hash=policy_hash,
                status="OPEN",
                starts_at=start,
                expires_at=end,
                opened_by_user_id=principal.user_id,
                opened_event_id=event.event_id,
            )
        )
        await audit_federation_action(
            session,
            principal,
            "OFFLINE_EPOCH_OPENED",
            "OfflineEpoch",
            epoch_id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, epoch_id)

    async def close_offline_epoch(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        epoch_id: UUID,
        expected_version: int,
        reconciliation: dict[str, object],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, AUDIT_ROLES | REGISTRAR_ROLES)
        payload = {
            "epoch_id": str(epoch_id),
            "expected_version": expected_version,
            "reconciliation": reconciliation,
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.close_offline_epoch", idempotency_key, payload
        )
        if replay is not None:
            return replay
        epoch = await self._locked(session, OfflineEpoch, epoch_id)
        self._version(epoch.version, expected_version)
        if epoch.status != "OPEN":
            raise federation_error("OFFLINE_EPOCH_STATE_INVALID")
        if await session.scalar(
            select(FederationPaperForm.id).where(
                FederationPaperForm.epoch_id == epoch.id,
                FederationPaperForm.status == "ISSUED",
            )
        ):
            raise federation_error("UNRECONCILED_PAPER_FORMS")
        event = await self.journal.append(
            session,
            event_type="federation.offline_epoch_closed",
            aggregate_type="offline_epoch",
            aggregate_id=epoch.id,
            aggregate_version=epoch.version + 1,
            actor=actor,
            payload={**payload, "policy_hash": epoch.policy_hash},
            offline_epoch_id=epoch.id,
        )
        epoch.status = "CLOSED"
        epoch.closed_event_id = event.event_id
        epoch.closed_at = datetime.now(UTC)
        epoch.version += 1
        await audit_federation_action(
            session,
            principal,
            "OFFLINE_EPOCH_CLOSED",
            "OfflineEpoch",
            epoch.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, epoch.id)

    async def reserve_exposure(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        node_id: UUID,
        capability: NodeCapability,
        unit: str,
        delta: Decimal,
        reference: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(
            session,
            principal,
            REGISTRAR_ROLES | SECURITY_ROLES | {RoleCode.NODE_BUSINESS_OPERATOR},
        )
        amount = bounded_amount(delta, allow_zero=False)
        unit_code = self._code(unit, 32)
        payload = {
            "node_id": str(node_id),
            "capability": capability.value,
            "unit": unit_code,
            "delta": str(amount),
            "reference": self._code(reference, 160),
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.reserve_node_exposure", idempotency_key, payload
        )
        if replay is not None:
            return replay
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"node-exposure:{node_id}:{capability.value}:{unit_code}"},
        )
        node = await self._locked(session, ExternalNode, node_id)
        if node.status not in {"LIMITED", "ACTIVE"}:
            raise federation_error("NODE_NOT_ACCEPTING_EXPOSURE")
        limit = await session.scalar(
            select(NodeBilateralLimit).where(
                NodeBilateralLimit.node_id == node.id,
                NodeBilateralLimit.capability == capability.value,
                NodeBilateralLimit.unit == unit_code,
                NodeBilateralLimit.status == "ACTIVE",
            )
        )
        if limit is None:
            raise federation_error("ACTIVE_BILATERAL_LIMIT_REQUIRED")
        exposure = await session.scalar(
            select(NodeExposure)
            .where(
                NodeExposure.node_id == node.id,
                NodeExposure.capability == capability.value,
                NodeExposure.unit == unit_code,
            )
            .with_for_update()
        )
        current = exposure.current_amount if exposure is not None else Decimal(0)
        reserved = exposure.reserved_amount if exposure is not None else Decimal(0)
        preview = preview_exposure(
            current=current,
            reserved=reserved,
            delta=amount,
            limit=limit.max_unsettled_obligations,
        )
        exposure_id = exposure.id if exposure is not None else uuid4()
        version = exposure.version + 1 if exposure is not None else 1
        limit_event_id = limit.approved_event_id or limit.proposed_event_id
        evidence_refs = (
            {
                "event_id": str(limit_event_id),
                "terms_hash": limit.terms_hash,
                "kind": "ACTIVE_BILATERAL_LIMIT",
            },
        )
        event = await self.journal.append(
            session,
            event_type="federation.node_exposure_reserved",
            aggregate_type="node_exposure",
            aggregate_id=exposure_id,
            aggregate_version=version,
            actor=actor,
            payload={
                **payload,
                "current_before": str(preview.current),
                "reserved_before": str(preview.reserved),
                "exposure_after": str(preview.after),
                "limit": str(preview.limit),
            },
            assurance=CommandAssurance(
                exposure=ExposureClaim(
                    category=ExposureCategory.NODE,
                    effect=ExposureEffect.RESERVE,
                    subject_type="node_exposure",
                    subject_id=exposure_id,
                    amount=amount,
                    unit=unit_code,
                    basis_refs=(limit.terms_hash,),
                ),
                evidence_refs=evidence_refs,
            ),
        )
        now = datetime.now(UTC)
        if exposure is None:
            session.add(
                NodeExposure(
                    id=exposure_id,
                    node_id=node.id,
                    capability=capability.value,
                    unit=unit_code,
                    current_amount=Decimal(0),
                    reserved_amount=amount,
                    updated_event_id=event.event_id,
                )
            )
        else:
            exposure.reserved_amount += amount
            exposure.updated_event_id = event.event_id
            exposure.updated_at = now
            exposure.version += 1
        await audit_federation_action(
            session,
            principal,
            "NODE_EXPOSURE_RESERVED",
            "NodeExposure",
            exposure_id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, exposure_id)

    async def _active_contract(self, session: AsyncSession, node_id: UUID) -> NodeTrustContract:
        contract = await session.scalar(
            select(NodeTrustContract).where(
                NodeTrustContract.node_id == node_id,
                NodeTrustContract.status == "ACTIVE",
                NodeTrustContract.valid_until > datetime.now(UTC),
            )
        )
        if contract is None:
            raise federation_error("ACTIVE_TRUST_CONTRACT_REQUIRED")
        return contract
