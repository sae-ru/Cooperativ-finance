"""Inbound CC-PEER-1 operations for distributed clearing participants."""

import base64
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.federation.application.inter_node_clearing import (
    InterNodeClearingService,
    SignedArtifact,
)
from cooperative_clearing.modules.federation.domain.peer_protocol import PeerOperation, PeerRequest
from cooperative_clearing.modules.federation.domain.types import federation_error
from cooperative_clearing.modules.federation.infrastructure.clearing_models import (
    FederatedClearingCycle,
    FederatedClearingPolicyRecord,
    FederatedClearingProposal,
    FederatedCommitCertificate,
    FederatedInputSnapshot,
    NodeApplyReceipt,
    NodeClearingApproval,
    NodePrepareReceipt,
)
from cooperative_clearing.modules.federation.infrastructure.models import ExternalNode
from cooperative_clearing.modules.identity.infrastructure.models import RoleAssignment
from cooperative_clearing.modules.journal.application.service import (
    ActorClaim,
    signer_from_settings,
)
from cooperative_clearing.modules.journal.domain.crypto import canonicalize, verify_signature
from cooperative_clearing.modules.journal.infrastructure.models import SignedEvent
from cooperative_clearing.modules.node.domain.node_code import NodeCode
from cooperative_clearing.shared.core.config import Settings

CLEARING_OPERATIONS = frozenset(
    {
        PeerOperation.CLEARING_SNAPSHOT,
        PeerOperation.CLEARING_PREPARE,
        PeerOperation.CLEARING_PROPOSAL,
        PeerOperation.CLEARING_STATUS,
        PeerOperation.CLEARING_COMMIT,
        PeerOperation.CLEARING_RELEASE,
    }
)


async def handle_peer_clearing(
    session: AsyncSession,
    *,
    settings: Settings,
    request: PeerRequest,
    peer: ExternalNode,
) -> dict[str, object]:
    service = InterNodeClearingService(settings)
    actor, user_id = await _peer_sponsor_actor(session, peer)
    if request.operation is PeerOperation.CLEARING_SNAPSHOT:
        cycle = await _ensure_cycle(
            session,
            service=service,
            request=request,
            actor=actor,
            user_id=user_id,
        )
        row = await service.create_local_snapshot(session, cycle_id=cycle.id)
        return {"status": cycle.status, "snapshot": _snapshot_artifact(row)}
    cycle = await _coordinator_cycle(session, request)
    if request.operation is PeerOperation.CLEARING_PREPARE:
        snapshots = _artifact_list(
            request.payload,
            "snapshots",
            hash_field="snapshot_hash",
            maximum=100,
        )
        for artifact in snapshots:
            await _verify_inner_artifact(
                session,
                settings=settings,
                artifact=artifact,
                hash_field="snapshot_hash",
            )
            if _node_code(artifact.payload) == settings.node_code:
                local = await service._required_snapshot(session, cycle.id, settings.node_code)
                if local.snapshot_hash != artifact.artifact_hash:
                    raise federation_error("FEDERATED_LOCAL_SNAPSHOT_MISMATCH", 409)
            else:
                await service.accept_snapshot(
                    session, cycle_id=cycle.id, artifact=artifact, actor=actor
                )
        input_hash = _hash(request.payload, "input_hash")
        own_snapshot_hash = _hash(request.payload, "local_snapshot_hash")
        preview = await service.calculate_preview(session, cycle_id=cycle.id)
        if preview.clearing.input_hash != input_hash:
            raise federation_error("FEDERATED_INPUT_HASH_MISMATCH", 409)
        receipt = await service.prepare_local(
            session,
            cycle_id=cycle.id,
            input_hash=input_hash,
            snapshot_hash=own_snapshot_hash,
        )
        return {"status": cycle.status, "prepare_receipt": _prepare_artifact(receipt)}
    if request.operation is PeerOperation.CLEARING_PROPOSAL:
        artifact = _artifact(request.payload, "proposal", "result_hash")
        await _verify_inner_artifact(
            session,
            settings=settings,
            artifact=artifact,
            hash_field="result_hash",
            expected_node_code=cycle.coordinator_node_code,
        )
        await service.accept_proposal(session, cycle_id=cycle.id, artifact=artifact, actor=actor)
        return await _status_payload(session, cycle, settings.node_code)
    if request.operation is PeerOperation.CLEARING_STATUS:
        return await _status_payload(session, cycle, settings.node_code)
    if request.operation is PeerOperation.CLEARING_COMMIT:
        prepare_receipts = _artifact_list(
            request.payload,
            "prepare_receipts",
            hash_field="receipt_hash",
            maximum=100,
        )
        for prepare in prepare_receipts:
            await _verify_inner_artifact(
                session,
                settings=settings,
                artifact=prepare,
                hash_field="receipt_hash",
            )
            await service.accept_prepare_receipt(
                session, cycle_id=cycle.id, artifact=prepare, actor=actor
            )
        approvals = _artifact_list(
            request.payload,
            "approvals",
            hash_field="approval_hash",
            maximum=100,
        )
        for approval in approvals:
            await _verify_inner_artifact(
                session,
                settings=settings,
                artifact=approval,
                hash_field="approval_hash",
            )
            await service.accept_approval(
                session, cycle_id=cycle.id, artifact=approval, actor=actor
            )
        artifact = _artifact(request.payload, "certificate", "certificate_hash")
        await _verify_inner_artifact(
            session,
            settings=settings,
            artifact=artifact,
            hash_field="certificate_hash",
            expected_node_code=cycle.coordinator_node_code,
        )
        await service.accept_certificate(session, cycle_id=cycle.id, artifact=artifact, actor=actor)
        apply_receipt = await service.apply_local(session, cycle_id=cycle.id)
        return {"status": cycle.status, "apply_receipt": _apply_artifact(apply_receipt)}
    if request.operation is PeerOperation.CLEARING_RELEASE:
        expired = request.payload.get("expired", False)
        if not isinstance(expired, bool):
            raise federation_error("FEDERATED_ARTIFACT_INVALID", 422)
        await service.release_local_prepare(session, cycle_id=cycle.id, expired=expired)
        return {"status": cycle.status}
    raise federation_error("PEER_OPERATION_UNSUPPORTED", 422)


async def _ensure_cycle(
    session: AsyncSession,
    *,
    service: InterNodeClearingService,
    request: PeerRequest,
    actor: ActorClaim,
    user_id: UUID,
) -> FederatedClearingCycle:
    payload = request.payload
    cycle_id = _uuid(payload, "cycle_id")
    existing = await session.get(FederatedClearingCycle, cycle_id)
    if existing is not None:
        if existing.coordinator_node_code != request.source_node_code:
            raise federation_error("FEDERATED_COORDINATOR_MISMATCH", 409)
        return existing
    coordinator = str(NodeCode(_text(payload, "coordinator_node_code", 63)))
    if coordinator != request.source_node_code:
        raise federation_error("FEDERATED_COORDINATOR_MISMATCH", 422)
    policy = (
        await session.execute(
            select(FederatedClearingPolicyRecord).where(
                FederatedClearingPolicyRecord.policy_code == _text(payload, "policy_code", 80),
                FederatedClearingPolicyRecord.policy_version
                == _positive_int(payload, "policy_version"),
                FederatedClearingPolicyRecord.policy_hash == _hash(payload, "policy_hash"),
                FederatedClearingPolicyRecord.status == "ACTIVE",
            )
        )
    ).scalar_one_or_none()
    if policy is None:
        raise federation_error("FEDERATED_POLICY_NOT_ACCEPTED", 409)
    participants = _node_list(payload, "participant_node_codes")
    if service.settings.node_code not in participants:
        raise federation_error("FEDERATED_NODE_NOT_PARTICIPANT", 409)
    return await service.create_cycle(
        session,
        user_id=user_id,
        actor=actor,
        cycle_id=cycle_id,
        cycle_code=_text(payload, "cycle_code", 80),
        coordinator_node_code=coordinator,
        policy=policy,
        period_start=_datetime(payload, "period_start"),
        period_end=_datetime(payload, "period_end"),
        participant_node_codes=participants,
    )


async def _coordinator_cycle(session: AsyncSession, request: PeerRequest) -> FederatedClearingCycle:
    cycle = await session.get(FederatedClearingCycle, _uuid(request.payload, "cycle_id"))
    if cycle is None:
        raise federation_error("FEDERATED_CYCLE_NOT_FOUND", 404)
    if cycle.coordinator_node_code != request.source_node_code:
        raise federation_error("FEDERATED_COORDINATOR_MISMATCH", 403)
    return cycle


async def _peer_sponsor_actor(session: AsyncSession, peer: ExternalNode) -> tuple[ActorClaim, UUID]:
    event = await session.get(SignedEvent, peer.created_event_id)
    if event is None:
        raise federation_error("FEDERATED_SPONSOR_EVIDENCE_MISSING", 500)
    assignment = await session.get(RoleAssignment, event.actor_role_assignment_id)
    if assignment is None:
        raise federation_error("FEDERATED_SPONSOR_EVIDENCE_MISSING", 500)
    return (
        ActorClaim(
            person_id=event.actor_person_id,
            organization_id=event.actor_organization_id,
            role_assignment_id=event.actor_role_assignment_id,
        ),
        assignment.user_id,
    )


async def _verify_inner_artifact(
    session: AsyncSession,
    *,
    settings: Settings,
    artifact: SignedArtifact,
    hash_field: str,
    expected_node_code: str | None = None,
) -> None:
    node_code = expected_node_code or _node_code(artifact.payload)
    if node_code == settings.node_code:
        signer = signer_from_settings(settings)
        public_key = signer.public_key_bytes
        expected_fingerprint = signer.fingerprint
    else:
        from cooperative_clearing.modules.federation.application.peer_protocol import (
            trusted_peer_material,
        )

        _peer, certificate = await trusted_peer_material(
            session,
            node_code=node_code,
            capability="CLEARING",
            fingerprint=artifact.signer_fingerprint,
        )
        public_key = certificate.public_key
        expected_fingerprint = certificate.fingerprint
    if (
        artifact.signer_fingerprint != expected_fingerprint
        or artifact.payload.get(hash_field) != artifact.artifact_hash
        or not verify_signature(public_key, artifact.signature, canonicalize(artifact.payload))
    ):
        raise federation_error("FEDERATED_ARTIFACT_SIGNATURE_INVALID", 401)


async def _status_payload(
    session: AsyncSession, cycle: FederatedClearingCycle, node_code: str
) -> dict[str, object]:
    snapshot = (
        await session.execute(
            select(FederatedInputSnapshot).where(
                FederatedInputSnapshot.cycle_id == cycle.id,
                FederatedInputSnapshot.node_code == node_code,
            )
        )
    ).scalar_one_or_none()
    prepare = (
        await session.execute(
            select(NodePrepareReceipt).where(
                NodePrepareReceipt.cycle_id == cycle.id,
                NodePrepareReceipt.node_code == node_code,
            )
        )
    ).scalar_one_or_none()
    approval = (
        await session.execute(
            select(NodeClearingApproval).where(
                NodeClearingApproval.cycle_id == cycle.id,
                NodeClearingApproval.node_code == node_code,
            )
        )
    ).scalar_one_or_none()
    certificate = (
        await session.execute(
            select(FederatedCommitCertificate).where(
                FederatedCommitCertificate.cycle_id == cycle.id
            )
        )
    ).scalar_one_or_none()
    apply_receipt = (
        await session.execute(
            select(NodeApplyReceipt).where(
                NodeApplyReceipt.cycle_id == cycle.id,
                NodeApplyReceipt.node_code == node_code,
            )
        )
    ).scalar_one_or_none()
    proposal = (
        await session.execute(
            select(FederatedClearingProposal).where(FederatedClearingProposal.cycle_id == cycle.id)
        )
    ).scalar_one_or_none()
    return {
        "cycle_id": str(cycle.id),
        "status": cycle.status,
        "input_hash": cycle.input_hash,
        "result_hash": cycle.result_hash,
        "certificate_hash": cycle.certificate_hash,
        "snapshot": _snapshot_artifact(snapshot) if snapshot is not None else None,
        "prepare_receipt": _prepare_artifact(prepare) if prepare is not None else None,
        "proposal_result_hash": proposal.result_hash if proposal is not None else None,
        "approval": _approval_artifact(approval) if approval is not None else None,
        "certificate": _certificate_artifact(certificate) if certificate is not None else None,
        "apply_receipt": (_apply_artifact(apply_receipt) if apply_receipt is not None else None),
    }


def _snapshot_artifact(row: FederatedInputSnapshot) -> dict[str, object]:
    return _wire_artifact(
        row.snapshot_payload, row.snapshot_hash, row.node_signature, row.signer_fingerprint
    )


def _prepare_artifact(row: NodePrepareReceipt) -> dict[str, object]:
    return _wire_artifact(
        row.receipt_payload, row.receipt_hash, row.node_signature, row.signer_fingerprint
    )


def _approval_artifact(row: NodeClearingApproval) -> dict[str, object]:
    return _wire_artifact(
        row.approval_payload, row.approval_hash, row.node_signature, row.signer_fingerprint
    )


def _certificate_artifact(row: FederatedCommitCertificate) -> dict[str, object]:
    return _wire_artifact(
        row.certificate_payload,
        row.certificate_hash,
        row.coordinator_signature,
        row.signer_fingerprint,
    )


def _apply_artifact(row: NodeApplyReceipt) -> dict[str, object]:
    return _wire_artifact(
        row.receipt_payload, row.receipt_hash, row.node_signature, row.signer_fingerprint
    )


def _wire_artifact(
    payload: dict[str, object], digest: str, signature: bytes, fingerprint: str
) -> dict[str, object]:
    return {
        "payload": payload,
        "hash": digest,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
        "signer_fingerprint": fingerprint,
    }


def _artifact_list(
    payload: dict[str, object], key: str, *, hash_field: str, maximum: int
) -> tuple[SignedArtifact, ...]:
    raw = payload.get(key)
    if not isinstance(raw, list) or not 1 <= len(raw) <= maximum:
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422)
    return tuple(_artifact_value(value, hash_field) for value in raw)


def _artifact(payload: dict[str, object], key: str, hash_field: str) -> SignedArtifact:
    return _artifact_value(payload.get(key), hash_field)


def _artifact_value(value: object, hash_field: str) -> SignedArtifact:
    if not isinstance(value, dict):
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422)
    wire = cast(dict[str, object], value)
    document = wire.get("payload")
    if not isinstance(document, dict):
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422)
    body = cast(dict[str, object], document)
    digest = _text(wire, "hash", 71)
    if body.get(hash_field) != digest:
        raise federation_error("FEDERATED_ARTIFACT_HASH_MISMATCH", 422)
    try:
        signature = base64.b64decode(_text(wire, "signature_base64", 200), validate=True)
    except ValueError as exc:
        raise federation_error("FEDERATED_ARTIFACT_SIGNATURE_INVALID", 401) from exc
    return SignedArtifact(
        payload=body,
        artifact_hash=digest,
        signature=signature,
        signer_fingerprint=_text(wire, "signer_fingerprint", 71),
    )


def _uuid(payload: dict[str, object], key: str) -> UUID:
    try:
        return UUID(_text(payload, key, 36))
    except ValueError as exc:
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422) from exc


def _text(payload: dict[str, object], key: str, maximum: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422)
    return value


def _hash(payload: dict[str, object], key: str) -> str:
    value = _text(payload, key, 71)
    if len(value) != 71 or not value.startswith("sha256:"):
        raise federation_error("FEDERATED_ARTIFACT_HASH_INVALID", 422)
    return value


def _positive_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422)
    return value


def _datetime(payload: dict[str, object], key: str) -> datetime:
    value = _text(payload, key, 40)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422) from exc
    if parsed.tzinfo is None:
        raise federation_error("FEDERATED_ARTIFACT_INVALID", 422)
    return parsed.astimezone(UTC)


def _node_list(payload: dict[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not 2 <= len(value) <= 100:
        raise federation_error("FEDERATED_PARTICIPANTS_INVALID", 422)
    try:
        nodes = tuple(sorted({str(NodeCode(str(item))) for item in value}))
    except ValueError as exc:
        raise federation_error("FEDERATED_PARTICIPANTS_INVALID", 422) from exc
    if len(nodes) != len(value):
        raise federation_error("FEDERATED_PARTICIPANTS_INVALID", 422)
    return nodes


def _node_code(payload: dict[str, object]) -> str:
    return str(NodeCode(_text(payload, "node_code", 63)))
