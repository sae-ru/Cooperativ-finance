"""Recoverable coordinator workflow for signed inter-node clearing."""

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.federation.application.inter_node_clearing import (
    InterNodeClearingService,
    SignedArtifact,
)
from cooperative_clearing.modules.federation.application.peer_reservations import (
    PeerReservationClient,
)
from cooperative_clearing.modules.federation.domain.peer_protocol import PeerOperation
from cooperative_clearing.modules.federation.domain.types import federation_error
from cooperative_clearing.modules.federation.infrastructure.clearing_models import (
    FederatedClearingCycle,
    FederatedClearingPolicyRecord,
    FederatedClearingProposal,
    FederatedCommitCertificate,
    FederatedInputSnapshot,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    NodeCertificate,
)
from cooperative_clearing.modules.federation.infrastructure.peer_transport import PeerTransport
from cooperative_clearing.modules.journal.application.service import ActorClaim
from cooperative_clearing.modules.journal.domain.crypto import canonicalize, verify_signature
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError


@dataclass(frozen=True, slots=True)
class CoordinatorNodeStatus:
    node_code: str
    phase: str
    result_code: str


@dataclass(frozen=True, slots=True)
class CoordinatorResult:
    cycle_id: UUID
    status: str
    nodes: tuple[CoordinatorNodeStatus, ...]


class FederatedClearingCoordinator:
    def __init__(self, settings: Settings, transport: PeerTransport | None = None) -> None:
        self.settings = settings
        self.service = InterNodeClearingService(settings)
        self.peer = PeerReservationClient(settings, transport)

    async def collect_snapshots(
        self,
        session: AsyncSession,
        *,
        cycle_id: UUID,
        actor: ActorClaim,
    ) -> CoordinatorResult:
        cycle = await self.service._cycle(session, cycle_id)
        self._require_coordinator(cycle)
        policy = await self.service._policy(session, cycle.policy_id)
        statuses: list[CoordinatorNodeStatus] = []
        for node_code in cycle.participant_node_codes:
            if node_code == self.settings.node_code:
                await self.service.create_local_snapshot(session, cycle_id=cycle.id)
                statuses.append(CoordinatorNodeStatus(node_code, "SNAPSHOT", "OK"))
                continue
            try:
                node = await self._external_node(session, node_code)
                response, certificate = await self.peer.exchange(
                    session,
                    node_id=node.id,
                    operation=PeerOperation.CLEARING_SNAPSHOT,
                    payload=_cycle_wire(cycle, policy),
                )
                artifact = _response_artifact(response, "snapshot", "snapshot_hash", certificate)
                if artifact.payload.get("node_code") != node_code:
                    raise federation_error("FEDERATED_SNAPSHOT_NODE_MISMATCH", 502)
                await self.service.accept_snapshot(
                    session, cycle_id=cycle.id, artifact=artifact, actor=actor
                )
                statuses.append(CoordinatorNodeStatus(node_code, "SNAPSHOT", "OK"))
            except DomainError as exc:
                statuses.append(CoordinatorNodeStatus(node_code, "SNAPSHOT", exc.code))
        return CoordinatorResult(cycle.id, cycle.status, tuple(statuses))

    async def prepare_nodes(
        self,
        session: AsyncSession,
        *,
        cycle_id: UUID,
        actor: ActorClaim,
    ) -> CoordinatorResult:
        cycle = await self.service._cycle(session, cycle_id)
        self._require_coordinator(cycle)
        preview = await self.service.calculate_preview(session, cycle_id=cycle.id)
        await self.service.begin_prepare(
            session, cycle_id=cycle.id, input_hash=preview.clearing.input_hash
        )
        snapshots = await self.service._all_snapshots(session, cycle)
        snapshot_wire = [_snapshot_wire(row) for row in snapshots]
        snapshot_by_node = {row.node_code: row for row in snapshots}
        statuses: list[CoordinatorNodeStatus] = []
        for node_code in cycle.participant_node_codes:
            try:
                if node_code == self.settings.node_code:
                    await self.service.prepare_local(
                        session,
                        cycle_id=cycle.id,
                        input_hash=preview.clearing.input_hash,
                        snapshot_hash=snapshot_by_node[node_code].snapshot_hash,
                    )
                else:
                    node = await self._external_node(session, node_code)
                    response, certificate = await self.peer.exchange(
                        session,
                        node_id=node.id,
                        operation=PeerOperation.CLEARING_PREPARE,
                        payload={
                            "cycle_id": str(cycle.id),
                            "input_hash": preview.clearing.input_hash,
                            "local_snapshot_hash": snapshot_by_node[node_code].snapshot_hash,
                            "snapshots": snapshot_wire,
                        },
                    )
                    artifact = _response_artifact(
                        response, "prepare_receipt", "receipt_hash", certificate
                    )
                    await self.service.accept_prepare_receipt(
                        session, cycle_id=cycle.id, artifact=artifact, actor=actor
                    )
                statuses.append(CoordinatorNodeStatus(node_code, "PREPARE", "OK"))
            except DomainError as exc:
                statuses.append(CoordinatorNodeStatus(node_code, "PREPARE", exc.code))
        await self.service._mark_prepared_if_complete(session, cycle)
        return CoordinatorResult(cycle.id, cycle.status, tuple(statuses))

    async def publish_proposal(
        self,
        session: AsyncSession,
        *,
        cycle_id: UUID,
    ) -> CoordinatorResult:
        cycle = await self.service._cycle(session, cycle_id)
        self._require_coordinator(cycle)
        proposal = await self.service.create_proposal(session, cycle_id=cycle.id)
        wire = _proposal_wire(proposal)
        statuses: list[CoordinatorNodeStatus] = []
        for node_code in cycle.affected_node_codes:
            if node_code == self.settings.node_code:
                statuses.append(
                    CoordinatorNodeStatus(node_code, "PROPOSAL", "AWAITING_LOCAL_APPROVAL")
                )
                continue
            try:
                node = await self._external_node(session, node_code)
                await self.peer.exchange(
                    session,
                    node_id=node.id,
                    operation=PeerOperation.CLEARING_PROPOSAL,
                    payload={"cycle_id": str(cycle.id), "proposal": wire},
                )
                statuses.append(
                    CoordinatorNodeStatus(node_code, "PROPOSAL", "AWAITING_LOCAL_APPROVAL")
                )
            except DomainError as exc:
                statuses.append(CoordinatorNodeStatus(node_code, "PROPOSAL", exc.code))
        return CoordinatorResult(cycle.id, cycle.status, tuple(statuses))

    async def collect_approvals(
        self,
        session: AsyncSession,
        *,
        cycle_id: UUID,
        actor: ActorClaim,
    ) -> CoordinatorResult:
        cycle = await self.service._cycle(session, cycle_id)
        self._require_coordinator(cycle)
        statuses: list[CoordinatorNodeStatus] = []
        for node_code in cycle.affected_node_codes:
            current = await self.service._approval(session, cycle.id, node_code)
            if current is not None:
                statuses.append(CoordinatorNodeStatus(node_code, "APPROVAL", "OK"))
                continue
            if node_code == self.settings.node_code:
                statuses.append(
                    CoordinatorNodeStatus(node_code, "APPROVAL", "AWAITING_LOCAL_APPROVAL")
                )
                continue
            try:
                node = await self._external_node(session, node_code)
                response, certificate = await self.peer.exchange(
                    session,
                    node_id=node.id,
                    operation=PeerOperation.CLEARING_STATUS,
                    payload={"cycle_id": str(cycle.id)},
                )
                if response.get("approval") is None:
                    statuses.append(
                        CoordinatorNodeStatus(node_code, "APPROVAL", "AWAITING_LOCAL_APPROVAL")
                    )
                    continue
                artifact = _response_artifact(response, "approval", "approval_hash", certificate)
                await self.service.accept_approval(
                    session, cycle_id=cycle.id, artifact=artifact, actor=actor
                )
                statuses.append(CoordinatorNodeStatus(node_code, "APPROVAL", "OK"))
            except DomainError as exc:
                statuses.append(CoordinatorNodeStatus(node_code, "APPROVAL", exc.code))
        return CoordinatorResult(cycle.id, cycle.status, tuple(statuses))

    async def certify_and_apply(
        self,
        session: AsyncSession,
        *,
        cycle_id: UUID,
        actor: ActorClaim,
    ) -> CoordinatorResult:
        cycle = await self.service._cycle(session, cycle_id)
        self._require_coordinator(cycle)
        certificate = await self.service.certify(session, cycle_id=cycle.id)
        commit_payload = await self._commit_payload(session, cycle, certificate)
        statuses: list[CoordinatorNodeStatus] = []
        for node_code in cycle.affected_node_codes:
            try:
                if node_code == self.settings.node_code:
                    await self.service.apply_local(session, cycle_id=cycle.id)
                else:
                    node = await self._external_node(session, node_code)
                    response, remote_certificate = await self.peer.exchange(
                        session,
                        node_id=node.id,
                        operation=PeerOperation.CLEARING_COMMIT,
                        payload=commit_payload,
                    )
                    artifact = _response_artifact(
                        response, "apply_receipt", "receipt_hash", remote_certificate
                    )
                    await self.service.accept_apply_receipt(
                        session, cycle_id=cycle.id, artifact=artifact, actor=actor
                    )
                statuses.append(CoordinatorNodeStatus(node_code, "APPLY", "OK"))
            except DomainError as exc:
                statuses.append(CoordinatorNodeStatus(node_code, "APPLY", exc.code))
        if any(item.result_code != "OK" for item in statuses):
            if cycle.status == "COMMIT_CERTIFIED":
                await _set_pending_apply(session, cycle)
        else:
            await self.service.reconcile(session, cycle_id=cycle.id)
        return CoordinatorResult(cycle.id, cycle.status, tuple(statuses))

    async def recover(
        self,
        session: AsyncSession,
        *,
        cycle_id: UUID,
        actor: ActorClaim,
    ) -> CoordinatorResult:
        cycle = await self.service._cycle(session, cycle_id)
        self._require_coordinator(cycle)
        if cycle.certificate_hash is None:
            return await self.collect_approvals(session, cycle_id=cycle.id, actor=actor)
        certificate = await self.service._certificate(session, cycle.id)
        if cycle.status == "COMMITTED_PENDING_APPLY":
            cycle.status = "APPLYING"
            cycle.updated_at = datetime.now(UTC)
            cycle.version += 1
            await session.flush()
        commit_payload = await self._commit_payload(session, cycle, certificate)
        statuses: list[CoordinatorNodeStatus] = []
        for node_code in cycle.affected_node_codes:
            if await self.service._apply_receipt(session, cycle.id, node_code) is not None:
                statuses.append(CoordinatorNodeStatus(node_code, "RECOVERY", "ALREADY_APPLIED"))
                continue
            try:
                if node_code == self.settings.node_code:
                    await self.service.apply_local(session, cycle_id=cycle.id)
                else:
                    node = await self._external_node(session, node_code)
                    response, remote_certificate = await self.peer.exchange(
                        session,
                        node_id=node.id,
                        operation=PeerOperation.CLEARING_COMMIT,
                        payload=commit_payload,
                    )
                    artifact = _response_artifact(
                        response, "apply_receipt", "receipt_hash", remote_certificate
                    )
                    await self.service.accept_apply_receipt(
                        session, cycle_id=cycle.id, artifact=artifact, actor=actor
                    )
                statuses.append(CoordinatorNodeStatus(node_code, "RECOVERY", "OK"))
            except DomainError as exc:
                statuses.append(CoordinatorNodeStatus(node_code, "RECOVERY", exc.code))
        if all(item.result_code in {"OK", "ALREADY_APPLIED"} for item in statuses):
            await self.service.reconcile(session, cycle_id=cycle.id)
        elif cycle.status == "APPLYING":
            await _set_pending_apply(session, cycle)
        return CoordinatorResult(cycle.id, cycle.status, tuple(statuses))

    async def release(
        self, session: AsyncSession, *, cycle_id: UUID, expired: bool
    ) -> CoordinatorResult:
        cycle = await self.service._cycle(session, cycle_id)
        self._require_coordinator(cycle)
        statuses: list[CoordinatorNodeStatus] = []
        for node_code in cycle.participant_node_codes:
            try:
                if node_code == self.settings.node_code:
                    await self.service.release_local_prepare(
                        session, cycle_id=cycle.id, expired=expired
                    )
                else:
                    node = await self._external_node(session, node_code)
                    await self.peer.exchange(
                        session,
                        node_id=node.id,
                        operation=PeerOperation.CLEARING_RELEASE,
                        payload={"cycle_id": str(cycle.id), "expired": expired},
                    )
                statuses.append(CoordinatorNodeStatus(node_code, "RELEASE", "OK"))
            except DomainError as exc:
                statuses.append(CoordinatorNodeStatus(node_code, "RELEASE", exc.code))
        return CoordinatorResult(cycle.id, cycle.status, tuple(statuses))

    async def _commit_payload(
        self,
        session: AsyncSession,
        cycle: FederatedClearingCycle,
        certificate: FederatedCommitCertificate,
    ) -> dict[str, object]:
        prepares = await self.service._prepare_map(session, cycle.id)
        approvals = await self.service._approval_map(session, cycle.id)
        required = tuple(sorted(cycle.affected_node_codes))
        if not set(required).issubset(prepares) or not set(required).issubset(approvals):
            raise federation_error("FEDERATED_CERTIFICATE_APPROVALS_INCOMPLETE", 409)
        return {
            "cycle_id": str(cycle.id),
            "certificate": _certificate_wire(certificate),
            "prepare_receipts": [
                _wire(
                    prepares[code].receipt_payload,
                    prepares[code].receipt_hash,
                    prepares[code].node_signature,
                    prepares[code].signer_fingerprint,
                )
                for code in required
            ],
            "approvals": [
                _wire(
                    approvals[code].approval_payload,
                    approvals[code].approval_hash,
                    approvals[code].node_signature,
                    approvals[code].signer_fingerprint,
                )
                for code in required
            ],
        }

    def _require_coordinator(self, cycle: FederatedClearingCycle) -> None:
        if cycle.coordinator_node_code != self.settings.node_code:
            raise federation_error("FEDERATED_LOCAL_NODE_NOT_COORDINATOR", 403)

    async def _external_node(self, session: AsyncSession, node_code: str) -> ExternalNode:
        row = (
            await session.execute(
                select(ExternalNode).where(
                    ExternalNode.node_code == node_code, ExternalNode.status == "ACTIVE"
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise federation_error("FEDERATED_COUNTERPARTY_NOT_ACTIVE", 409)
        return row


@dataclass(frozen=True, slots=True)
class FederatedRecoverySweepResult:
    attempted_cycles: int
    reconciled_cycles: int
    pending_cycles: int


async def recover_pending_federated_cycles(
    session: AsyncSession,
    *,
    settings: Settings,
    batch_size: int,
) -> FederatedRecoverySweepResult:
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.federated_recovery_retry_seconds)
    cycles = list(
        (
            await session.execute(
                select(FederatedClearingCycle)
                .where(
                    FederatedClearingCycle.coordinator_node_code == settings.node_code,
                    FederatedClearingCycle.status == "COMMITTED_PENDING_APPLY",
                    FederatedClearingCycle.updated_at <= cutoff,
                )
                .order_by(FederatedClearingCycle.updated_at, FederatedClearingCycle.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    coordinator = FederatedClearingCoordinator(settings)
    reconciled = 0
    for cycle in cycles:
        actor = ActorClaim(
            person_id=cycle.created_by_member_id,
            organization_id=None,
            role_assignment_id=cycle.created_role_assignment_id,
        )
        result = await coordinator.recover(session, cycle_id=cycle.id, actor=actor)
        if result.status == "RECONCILED":
            reconciled += 1
    return FederatedRecoverySweepResult(
        attempted_cycles=len(cycles),
        reconciled_cycles=reconciled,
        pending_cycles=len(cycles) - reconciled,
    )


async def _set_pending_apply(session: AsyncSession, cycle: FederatedClearingCycle) -> None:
    cycle.status = "COMMITTED_PENDING_APPLY"
    cycle.updated_at = datetime.now(UTC)
    cycle.version += 1
    await session.flush()


def _cycle_wire(
    cycle: FederatedClearingCycle, policy: FederatedClearingPolicyRecord
) -> dict[str, object]:
    return {
        "cycle_id": str(cycle.id),
        "cycle_code": cycle.cycle_code,
        "coordinator_node_code": cycle.coordinator_node_code,
        "policy_code": policy.policy_code,
        "policy_version": policy.policy_version,
        "policy_hash": policy.policy_hash,
        "period_start": cycle.period_start.astimezone(UTC).isoformat(),
        "period_end": cycle.period_end.astimezone(UTC).isoformat(),
        "participant_node_codes": cycle.participant_node_codes,
    }


def _snapshot_wire(row: FederatedInputSnapshot) -> dict[str, object]:
    return _wire(
        row.snapshot_payload, row.snapshot_hash, row.node_signature, row.signer_fingerprint
    )


def _proposal_wire(row: FederatedClearingProposal) -> dict[str, object]:
    return _wire(
        row.proposal_payload,
        row.result_hash,
        row.coordinator_signature,
        row.signer_fingerprint,
    )


def _certificate_wire(row: FederatedCommitCertificate) -> dict[str, object]:
    return _wire(
        row.certificate_payload,
        row.certificate_hash,
        row.coordinator_signature,
        row.signer_fingerprint,
    )


def _wire(
    payload: dict[str, object], digest: str, signature: bytes, fingerprint: str
) -> dict[str, object]:
    return {
        "payload": payload,
        "hash": digest,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
        "signer_fingerprint": fingerprint,
    }


def _response_artifact(
    response: dict[str, object],
    key: str,
    hash_field: str,
    certificate: NodeCertificate,
) -> SignedArtifact:
    raw = response.get(key)
    if not isinstance(raw, dict):
        raise federation_error("FEDERATED_PEER_RESPONSE_INVALID", 502)
    wire = cast(dict[str, object], raw)
    payload = wire.get("payload")
    digest = wire.get("hash")
    encoded = wire.get("signature_base64")
    fingerprint = wire.get("signer_fingerprint")
    if (
        not isinstance(payload, dict)
        or not isinstance(digest, str)
        or not isinstance(encoded, str)
        or fingerprint != certificate.fingerprint
    ):
        raise federation_error("FEDERATED_PEER_RESPONSE_INVALID", 502)
    document = cast(dict[str, object], payload)
    if document.get(hash_field) != digest:
        raise federation_error("FEDERATED_PEER_RESPONSE_INVALID", 502)
    try:
        signature = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise federation_error("FEDERATED_PEER_RESPONSE_INVALID", 502) from exc
    if not verify_signature(certificate.public_key, signature, canonicalize(document)):
        raise federation_error("FEDERATED_ARTIFACT_SIGNATURE_INVALID", 502)
    return SignedArtifact(document, digest, signature, certificate.fingerprint)
