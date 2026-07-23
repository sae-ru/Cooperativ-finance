"""Signed package export, quarantine verification, simulation, conflict, and apply."""

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.federation.application.common import (
    FederationCommandResult,
    audit_federation_action,
    begin_federation_command,
    complete_federation_command,
    federation_actor,
)
from cooperative_clearing.modules.federation.application.lifecycle import NodeTrustService
from cooperative_clearing.modules.federation.application.service import (
    AUDIT_ROLES,
    REGISTRAR_ROLES,
    SECURITY_ROLES,
)
from cooperative_clearing.modules.federation.domain.package import (
    build_package_archive,
    decode_package_archive,
    parse_event_lines,
)
from cooperative_clearing.modules.federation.domain.types import (
    ConflictClass,
    ConflictDecision,
    federation_error,
)
from cooperative_clearing.modules.federation.infrastructure.models import (
    ExternalNode,
    FederationCheckpoint,
    InboxEvent,
    NodeCertificate,
    NodeTrustContract,
    OfflineEpoch,
    SyncConflict,
    SyncPackage,
    SyncReceipt,
)
from cooperative_clearing.modules.identity.domain.types import Principal
from cooperative_clearing.modules.journal.domain.crypto import (
    canonicalize,
    payload_hash,
    sha256_ref,
    utc_timestamp,
    verify_signature,
)
from cooperative_clearing.modules.journal.infrastructure.models import (
    EventSignature,
    SignedEvent,
)
from cooperative_clearing.modules.node.infrastructure.models import NodeKeyRecord

SYNC_PROTOCOL_VERSION = "1.0"
AUTO_APPLY_EVENT_TYPES = frozenset({"federation.test_event"})


@dataclass(frozen=True, slots=True)
class PackageArchiveResult:
    result: FederationCommandResult
    archive_path: Path
    archive_hash: str


@dataclass(frozen=True, slots=True)
class ParsedInboundEvent:
    event_id: UUID
    event_type: str
    local_sequence: int
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    previous_event_hash: str | None
    event_hash: str
    envelope: dict[str, object]
    signature: bytes


@dataclass(frozen=True, slots=True)
class ConflictInput:
    conflict_class: ConflictClass
    inbox_event_id: UUID | None
    affected_object_type: str
    affected_object_id: UUID | None
    local_event_id: UUID | None
    remote_event_id: UUID | None
    local_event_hash: str | None
    remote_event_hash: str | None


class SyncService(NodeTrustService):
    async def export_package(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        peer_node_id: UUID,
        sequence_after: int,
        maximum_events: int,
        expiry_hours: int,
        epoch_id: UUID | None,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> PackageArchiveResult:
        actor = await federation_actor(session, principal, REGISTRAR_ROLES | SECURITY_ROLES)
        if sequence_after < 0 or not 1 <= maximum_events <= self.settings.sync_package_max_events:
            raise federation_error("SYNC_EXPORT_RANGE_INVALID", 422)
        if not 1 <= expiry_hours <= 168:
            raise federation_error("SYNC_PACKAGE_EXPIRY_INVALID", 422)
        payload = {
            "peer_node_id": str(peer_node_id),
            "sequence_after": sequence_after,
            "maximum_events": maximum_events,
            "expiry_hours": expiry_hours,
            "epoch_id": str(epoch_id) if epoch_id is not None else None,
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.export_sync_package", idempotency_key, payload
        )
        if replay is not None:
            package = await session.get(SyncPackage, replay.object_id)
            if package is None:
                raise federation_error("SYNC_PACKAGE_NOT_FOUND", 404)
            return PackageArchiveResult(
                replay, self._absolute_package_path(package.archive_path), package.archive_hash
            )
        peer = await self._locked(session, ExternalNode, peer_node_id)
        contract = await self._active_contract(session, peer.id)
        if peer.status not in {"LIMITED", "ACTIVE"}:
            raise federation_error("NODE_NOT_ACCEPTING_PACKAGES")
        local_node = await self._local_node(session)
        now = datetime.now(UTC)
        epoch: OfflineEpoch | None = None
        allowed_event_types = set(contract.event_types)
        if epoch_id is not None:
            epoch = await self._locked(session, OfflineEpoch, epoch_id)
            if (
                epoch.external_node_id != peer.id
                or epoch.status != "OPEN"
                or epoch.starts_at > now
                or (epoch.expires_at is not None and epoch.expires_at <= now)
                or epoch.protocol_version != SYNC_PROTOCOL_VERSION
            ):
                raise federation_error("OFFLINE_EPOCH_NOT_EXPORTABLE")
            allowed_event_types &= set(epoch.allowed_event_types)
        statement = (
            select(SignedEvent, EventSignature, NodeKeyRecord)
            .join(EventSignature, EventSignature.event_id == SignedEvent.event_id)
            .join(NodeKeyRecord, NodeKeyRecord.id == EventSignature.key_id)
            .where(
                SignedEvent.node_id == local_node.id,
                SignedEvent.local_sequence > sequence_after,
                SignedEvent.event_type.in_(allowed_event_types),
                EventSignature.signature_scope == "NODE",
            )
            .order_by(SignedEvent.local_sequence)
            .limit(maximum_events)
        )
        if epoch is None:
            statement = statement.where(SignedEvent.offline_epoch_id.is_(None))
        else:
            statement = statement.where(SignedEvent.offline_epoch_id == epoch.id)
        rows = list((await session.execute(statement)).all())
        if not rows:
            raise federation_error("SYNC_EXPORT_EMPTY", 422)
        keys = {key.fingerprint: key for _, _, key in rows}
        if len(keys) != 1:
            raise federation_error("SYNC_EXPORT_KEY_RANGE_UNSUPPORTED")
        key = next(iter(keys.values()))
        events = [
            {
                "envelope": json.loads(event.canonical_envelope),
                "event_hash": event.event_hash,
                "key_fingerprint": key_record.fingerprint,
                "signature": base64.b64encode(signature.signature).decode(),
            }
            for event, signature, key_record in rows
        ]
        package_id = uuid4()
        package_expires_at = now + timedelta(hours=expiry_hours)
        if (
            epoch is not None
            and epoch.expires_at is not None
            and package_expires_at > epoch.expires_at
        ):
            raise federation_error("SYNC_PACKAGE_EXCEEDS_EPOCH")
        required_capabilities = sorted(set(contract.capabilities))
        manifest_base = {
            "package_id": str(package_id),
            "source_node_code": local_node.node_code,
            "source_node_id": str(local_node.id),
            "target_node_code": peer.node_code,
            "target_node_id": str(peer.id),
            "created_at": utc_timestamp(now),
            "expires_at": utc_timestamp(package_expires_at),
            "protocol_version": SYNC_PROTOCOL_VERSION,
            "sequence_first": rows[0][0].local_sequence,
            "sequence_last": rows[-1][0].local_sequence,
            "base_checkpoint_hash": rows[0][0].previous_event_hash,
            "event_count": len(rows),
            "blob_count": 0,
            "required_capabilities": required_capabilities,
            "contract_id": str(contract.id),
        }
        if epoch is not None:
            manifest_base["epoch_id"] = str(epoch.id)
            manifest_base["epoch_policy_hash"] = epoch.policy_hash
        certificate = {
            "node_id": str(local_node.id),
            "fingerprint": key.fingerprint,
            "algorithm": key.algorithm,
            "public_key": base64.b64encode(key.public_key).decode(),
            "valid_from": utc_timestamp(key.valid_from),
            "valid_until": utc_timestamp(key.valid_until) if key.valid_until else None,
            "status": key.status,
        }
        archive, manifest = build_package_archive(
            manifest_base=manifest_base,
            events=events,
            certificate=certificate,
            revocations={"node_id": str(local_node.id), "revocations": []},
            signer=self.journal.signer,
        )
        if len(archive) > self.settings.sync_package_max_bytes:
            raise federation_error("SYNC_ARCHIVE_SIZE_INVALID", 422)
        relative_path = self._package_relative_path("outbound", package_id)
        absolute_path = self._absolute_package_path(relative_path)
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_bytes(archive)
        archive_hash = sha256_ref(archive)
        manifest_hash = sha256_ref(canonicalize(manifest))
        event = await self.journal.append(
            session,
            event_type="federation.sync_package_exported",
            aggregate_type="sync_package",
            aggregate_id=package_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **payload,
                "package_id": str(package_id),
                "archive_hash": archive_hash,
                "manifest_hash": manifest_hash,
                "sequence_first": rows[0][0].local_sequence,
                "sequence_last": rows[-1][0].local_sequence,
                "event_count": len(rows),
            },
            offline_epoch_id=epoch.id if epoch is not None else None,
        )
        session.add(
            SyncPackage(
                id=package_id,
                peer_node_id=peer.id,
                epoch_id=epoch.id if epoch is not None else None,
                direction="OUTBOUND",
                status="EXPORTED",
                source_node_code=local_node.node_code,
                target_node_code=peer.node_code,
                protocol_version=SYNC_PROTOCOL_VERSION,
                required_capabilities=required_capabilities,
                sequence_first=rows[0][0].local_sequence,
                sequence_last=rows[-1][0].local_sequence,
                base_checkpoint_hash=rows[0][0].previous_event_hash,
                event_count=len(rows),
                blob_count=0,
                archive_size=len(archive),
                archive_hash=archive_hash,
                manifest_payload=manifest,
                manifest_hash=manifest_hash,
                archive_path=relative_path,
                created_by_user_id=principal.user_id,
                created_event_id=event.event_id,
                created_at=now,
                expires_at=package_expires_at,
            )
        )
        await audit_federation_action(
            session,
            principal,
            "SYNC_PACKAGE_EXPORTED",
            "SyncPackage",
            package_id,
            event.event_id,
            request_id,
        )
        return PackageArchiveResult(
            complete_federation_command(record, event.event_id, package_id),
            absolute_path,
            archive_hash,
        )

    async def import_package(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        archive: bytes,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, REGISTRAR_ROLES | SECURITY_ROLES)
        decoded = decode_package_archive(
            archive,
            maximum_bytes=self.settings.sync_package_max_bytes,
            maximum_files=self.settings.sync_package_max_files,
            maximum_ratio=self.settings.sync_package_max_compression_ratio,
        )
        manifest = decoded.manifest
        package_id = self._manifest_uuid(manifest, "package_id")
        archive_hash = sha256_ref(archive)
        command_payload = {
            "package_id": str(package_id),
            "archive_hash": archive_hash,
            "manifest_hash": sha256_ref(canonicalize(manifest)),
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.import_sync_package", idempotency_key, command_payload
        )
        if replay is not None:
            return replay
        existing_package = await session.get(SyncPackage, package_id)
        if existing_package is not None:
            if existing_package.archive_hash == archive_hash:
                return complete_federation_command(
                    record, existing_package.created_event_id, existing_package.id
                )
            raise federation_error("SYNC_PACKAGE_ID_COLLISION")
        peer = await session.scalar(
            select(ExternalNode).where(
                ExternalNode.node_code == self._manifest_text(manifest, "source_node_code")
            )
        )
        if peer is None or peer.status not in {"LIMITED", "ACTIVE"}:
            raise federation_error("SYNC_SOURCE_NODE_UNTRUSTED", 403)
        contract = await self._active_contract(session, peer.id)
        local_node = await self._local_node(session)
        self._verify_manifest(manifest, peer, local_node.id, local_node.node_code, contract)
        epoch_id = self._manifest_optional_uuid(manifest, "epoch_id")
        epoch: OfflineEpoch | None = None
        if epoch_id is not None:
            epoch = await session.get(OfflineEpoch, epoch_id)
            created_at = self._manifest_datetime(manifest, "created_at")
            if (
                epoch is None
                or epoch.external_node_id != peer.id
                or epoch.status not in {"OPEN", "CLOSED"}
                or epoch.policy_hash != self._manifest_text(manifest, "epoch_policy_hash")
                or epoch.protocol_version != SYNC_PROTOCOL_VERSION
                or created_at < epoch.starts_at
                or (epoch.expires_at is not None and created_at > epoch.expires_at)
                or (epoch.closed_at is not None and created_at > epoch.closed_at)
            ):
                raise federation_error("SYNC_OFFLINE_EPOCH_INVALID", 422)
        elif "epoch_policy_hash" in manifest:
            raise federation_error("SYNC_OFFLINE_EPOCH_INVALID", 422)
        certificate = await session.scalar(
            select(NodeCertificate).where(
                NodeCertificate.node_id == peer.id,
                NodeCertificate.status == "ACTIVE",
                NodeCertificate.valid_from <= datetime.now(UTC),
                NodeCertificate.valid_until > datetime.now(UTC),
            )
        )
        if certificate is None:
            raise federation_error("ACTIVE_NODE_CERTIFICATE_REQUIRED")
        self._verify_archive_content(decoded.files, manifest)
        self._verify_package_certificate(decoded.certificate, peer, certificate)
        if not verify_signature(certificate.public_key, decoded.signature, canonicalize(manifest)):
            raise federation_error("SYNC_PACKAGE_SIGNATURE_INVALID", 422)
        raw_events = parse_event_lines(decoded.events_bytes, self.settings.sync_package_max_events)
        allowed_event_types = set(contract.event_types)
        if epoch is not None:
            allowed_event_types &= set(epoch.allowed_event_types)
        parsed_events = self._parse_and_verify_events(
            raw_events,
            manifest,
            peer,
            certificate,
            allowed_event_types,
            epoch.id if epoch is not None else None,
        )
        conflicts: list[ConflictInput] = []
        inbox_rows: list[InboxEvent] = []
        seen_aggregate_versions: dict[
            tuple[str, UUID, int], tuple[ParsedInboundEvent, InboxEvent]
        ] = {}
        duplicates = 0
        expected_previous = cast(str | None, manifest.get("base_checkpoint_hash"))
        for parsed in parsed_events:
            existing_event = await session.scalar(
                select(InboxEvent).where(
                    InboxEvent.source_node_id == peer.id,
                    InboxEvent.event_id == parsed.event_id,
                )
            )
            if existing_event is not None:
                if existing_event.event_hash == parsed.event_hash:
                    duplicates += 1
                    expected_previous = parsed.event_hash
                    continue
                conflicts.append(
                    ConflictInput(
                        ConflictClass.TAMPERED_DUPLICATE,
                        None,
                        parsed.aggregate_type,
                        parsed.aggregate_id,
                        existing_event.event_id,
                        parsed.event_id,
                        existing_event.event_hash,
                        parsed.event_hash,
                    )
                )
                expected_previous = parsed.event_hash
                continue
            inbox_id = uuid4()
            aggregate_key = (
                parsed.aggregate_type,
                parsed.aggregate_id,
                parsed.aggregate_version,
            )
            prior = seen_aggregate_versions.get(aggregate_key)
            event_conflict: ConflictInput | None
            if prior is not None and prior[0].event_hash != parsed.event_hash:
                prior[1].status = "CONFLICT"
                event_conflict = ConflictInput(
                    ConflictClass.CONCURRENT_METADATA,
                    inbox_id,
                    parsed.aggregate_type,
                    parsed.aggregate_id,
                    prior[0].event_id,
                    parsed.event_id,
                    prior[0].event_hash,
                    parsed.event_hash,
                )
            else:
                event_conflict = await self._detect_event_conflict(
                    session, peer.id, parsed, inbox_id, expected_previous
                )
            if event_conflict is not None:
                conflicts.append(event_conflict)
                status = "CONFLICT"
            else:
                status = "READY" if parsed.event_type in AUTO_APPLY_EVENT_TYPES else "HELD"
            inbox_rows.append(
                InboxEvent(
                    id=inbox_id,
                    package_id=package_id,
                    source_node_id=peer.id,
                    event_id=parsed.event_id,
                    event_type=parsed.event_type,
                    local_sequence=parsed.local_sequence,
                    aggregate_type=parsed.aggregate_type,
                    aggregate_id=parsed.aggregate_id,
                    aggregate_version=parsed.aggregate_version,
                    previous_event_hash=parsed.previous_event_hash,
                    event_hash=parsed.event_hash,
                    envelope_payload=parsed.envelope,
                    signature=parsed.signature,
                    status=status,
                    effect_summary={
                        "handler": (
                            "SAFE_TEST_NO_EFFECT"
                            if parsed.event_type in AUTO_APPLY_EVENT_TYPES
                            else "HELD_FOR_DOMAIN_HANDLER"
                        ),
                        "creates_financial_effect": False,
                    },
                )
            )
            seen_aggregate_versions[aggregate_key] = (parsed, inbox_rows[-1])
            expected_previous = parsed.event_hash
        if len(parsed_events) != self._manifest_int(manifest, "event_count"):
            raise federation_error("SYNC_EVENT_COUNT_MISMATCH", 422)
        now = datetime.now(UTC)
        relative_path = self._package_relative_path("inbound", package_id)
        absolute_path = self._absolute_package_path(relative_path)
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_bytes(archive)
        summary = {
            "verified_events": len(parsed_events),
            "new_events": len(inbox_rows),
            "ready_events": sum(item.status == "READY" for item in inbox_rows),
            "held_events": sum(item.status == "HELD" for item in inbox_rows),
            "conflicts": len(conflicts),
            "duplicates": duplicates,
            "business_tables_changed": False,
        }
        package_event = await self.journal.append(
            session,
            event_type="federation.sync_package_simulated",
            aggregate_type="sync_package",
            aggregate_id=package_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **command_payload,
                "peer_node_id": str(peer.id),
                "simulation_summary": summary,
            },
            offline_epoch_id=epoch.id if epoch is not None else None,
        )
        package = SyncPackage(
            id=package_id,
            peer_node_id=peer.id,
            epoch_id=epoch.id if epoch is not None else None,
            direction="INBOUND",
            status="CONFLICT" if conflicts else "SIMULATED",
            source_node_code=peer.node_code,
            target_node_code=local_node.node_code,
            protocol_version=SYNC_PROTOCOL_VERSION,
            required_capabilities=list(
                cast(list[object], manifest.get("required_capabilities", []))
            ),
            sequence_first=self._manifest_int(manifest, "sequence_first"),
            sequence_last=self._manifest_int(manifest, "sequence_last"),
            base_checkpoint_hash=cast(str | None, manifest.get("base_checkpoint_hash")),
            event_count=len(parsed_events),
            blob_count=self._manifest_int(manifest, "blob_count"),
            archive_size=len(archive),
            archive_hash=archive_hash,
            manifest_payload=manifest,
            manifest_hash=command_payload["manifest_hash"],
            archive_path=relative_path,
            simulation_summary=summary,
            created_by_user_id=principal.user_id,
            created_event_id=package_event.event_id,
            created_at=self._manifest_datetime(manifest, "created_at"),
            expires_at=self._manifest_datetime(manifest, "expires_at"),
            verified_at=now,
            simulated_at=now,
        )
        session.add(package)
        await session.flush()
        session.add_all(inbox_rows)
        await session.flush()
        for item in conflicts:
            conflict_id = uuid4()
            conflict_event = await self.journal.append(
                session,
                event_type="federation.sync_conflict_opened",
                aggregate_type="sync_conflict",
                aggregate_id=conflict_id,
                aggregate_version=1,
                actor=actor,
                payload={
                    "package_id": str(package_id),
                    "conflict_class": item.conflict_class.value,
                    "affected_object_type": item.affected_object_type,
                    "affected_object_id": (
                        str(item.affected_object_id) if item.affected_object_id else None
                    ),
                    "local_event_id": str(item.local_event_id) if item.local_event_id else None,
                    "remote_event_id": str(item.remote_event_id) if item.remote_event_id else None,
                    "history_preserved": True,
                },
                offline_epoch_id=epoch.id if epoch is not None else None,
            )
            session.add(
                SyncConflict(
                    id=conflict_id,
                    package_id=package_id,
                    inbox_event_id=item.inbox_event_id,
                    conflict_class=item.conflict_class.value,
                    affected_object_type=item.affected_object_type,
                    affected_object_id=item.affected_object_id,
                    local_event_id=item.local_event_id,
                    remote_event_id=item.remote_event_id,
                    local_event_hash=item.local_event_hash,
                    remote_event_hash=item.remote_event_hash,
                    maximum_exposure=Decimal(0),
                    exposure_unit="UNSPECIFIED",
                    freeze_payload={
                        "new_effects_blocked": True,
                        "physical_audit_required": item.conflict_class
                        in {
                            ConflictClass.CUSTODY_CONFLICT,
                            ConflictClass.COMPETING_RESERVATION,
                            ConflictClass.DOUBLE_REDEMPTION,
                        },
                    },
                    evidence_ids=[],
                    status="OPEN",
                    opened_event_id=conflict_event.event_id,
                )
            )
        await audit_federation_action(
            session,
            principal,
            "SYNC_PACKAGE_SIMULATED",
            "SyncPackage",
            package_id,
            package_event.event_id,
            request_id,
        )
        return complete_federation_command(record, package_event.event_id, package_id)

    async def resolve_conflict(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        conflict_id: UUID,
        expected_version: int,
        decision: ConflictDecision,
        rationale: str,
        evidence_ids: list[UUID],
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, AUDIT_ROLES)
        evidence_values = sorted(str(item) for item in evidence_ids)
        payload = {
            "conflict_id": str(conflict_id),
            "expected_version": expected_version,
            "decision": decision.value,
            "rationale": self._text(rationale, 4000),
            "evidence_ids": sorted(str(item) for item in evidence_ids),
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.resolve_sync_conflict", idempotency_key, payload
        )
        if replay is not None:
            return replay
        conflict = await self._locked(session, SyncConflict, conflict_id)
        package = await self._locked(session, SyncPackage, conflict.package_id)
        self._version(conflict.version, expected_version)
        if conflict.status not in {"OPEN", "UNDER_REVIEW"}:
            raise federation_error("SYNC_CONFLICT_STATE_INVALID")
        if package.created_by_user_id == principal.user_id:
            raise federation_error("INDEPENDENT_REVIEW_REQUIRED", 403)
        if decision is ConflictDecision.COMPENSATE and not evidence_ids:
            raise federation_error("COMPENSATION_EVIDENCE_REQUIRED", 422)
        event = await self.journal.append(
            session,
            event_type="federation.sync_conflict_resolved",
            aggregate_type="sync_conflict",
            aggregate_id=conflict.id,
            aggregate_version=conflict.version + 1,
            actor=actor,
            payload={
                **payload,
                "package_id": str(package.id),
                "losing_history_deleted": False,
                "compensating_event_required": decision is ConflictDecision.COMPENSATE,
            },
            evidence=[{"evidence_id": str(item)} for item in evidence_ids],
            offline_epoch_id=package.epoch_id,
        )
        now = datetime.now(UTC)
        conflict.status = "RESOLVED"
        conflict.decision = decision.value
        conflict.rationale = str(payload["rationale"])
        conflict.evidence_ids = evidence_values
        conflict.decided_by_user_id = principal.user_id
        conflict.decided_event_id = event.event_id
        conflict.decided_at = now
        conflict.version += 1
        if conflict.inbox_event_id is not None:
            inbox = await self._locked(session, InboxEvent, conflict.inbox_event_id)
            if decision is ConflictDecision.ACCEPT_REMOTE:
                inbox.status = "READY" if inbox.event_type in AUTO_APPLY_EVENT_TYPES else "HELD"
            else:
                inbox.status = "REJECTED"
            competing_inbox = None
            if conflict.local_event_id is not None:
                competing_inbox = await session.scalar(
                    select(InboxEvent).where(
                        InboxEvent.package_id == package.id,
                        InboxEvent.event_id == conflict.local_event_id,
                    )
                )
            if competing_inbox is not None:
                if decision is ConflictDecision.ACCEPT_REMOTE:
                    competing_inbox.status = "IGNORED"
                else:
                    competing_inbox.status = (
                        "READY" if competing_inbox.event_type in AUTO_APPLY_EVENT_TYPES else "HELD"
                    )
        if decision is ConflictDecision.REJECT_PACKAGE:
            package.status = "REJECTED"
            package.rejection_code = "CONFLICT_PANEL_REJECTED"
            package.version += 1
        else:
            remaining = int(
                await session.scalar(
                    select(func.count())
                    .select_from(SyncConflict)
                    .where(
                        SyncConflict.package_id == package.id,
                        SyncConflict.id != conflict.id,
                        SyncConflict.status != "RESOLVED",
                    )
                )
                or 0
            )
            if remaining == 0:
                package.status = "SIMULATED"
                package.version += 1
        await audit_federation_action(
            session,
            principal,
            "SYNC_CONFLICT_RESOLVED",
            "SyncConflict",
            conflict.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, conflict.id)

    async def apply_package(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        package_id: UUID,
        expected_version: int,
        manifest_hash: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> FederationCommandResult:
        actor = await federation_actor(session, principal, REGISTRAR_ROLES | AUDIT_ROLES)
        payload = {
            "package_id": str(package_id),
            "expected_version": expected_version,
            "manifest_hash": manifest_hash,
        }
        record, replay = await begin_federation_command(
            session, principal, "federation.apply_sync_package", idempotency_key, payload
        )
        if replay is not None:
            return replay
        package = await self._locked(session, SyncPackage, package_id)
        peer = await self._locked(session, ExternalNode, package.peer_node_id)
        self._version(package.version, expected_version)
        if (
            package.direction != "INBOUND"
            or package.status != "SIMULATED"
            or package.manifest_hash != manifest_hash
            or package.expires_at <= datetime.now(UTC)
            or package.created_by_user_id == principal.user_id
            or peer.status not in {"LIMITED", "ACTIVE"}
        ):
            raise federation_error("SYNC_PACKAGE_APPLY_INVALID")
        if await session.scalar(
            select(SyncConflict.id).where(
                SyncConflict.package_id == package.id,
                SyncConflict.status != "RESOLVED",
            )
        ):
            raise federation_error("SYNC_CONFLICT_OPEN")
        inbox = list(
            (
                await session.execute(
                    select(InboxEvent).where(InboxEvent.package_id == package.id).with_for_update()
                )
            ).scalars()
        )
        held = [item for item in inbox if item.status in {"HELD", "CONFLICT"}]
        if held:
            raise federation_error("SYNC_DOMAIN_HANDLER_REQUIRED")
        ready = [item for item in inbox if item.status == "READY"]
        event = await self.journal.append(
            session,
            event_type="federation.sync_package_applied",
            aggregate_type="sync_package",
            aggregate_id=package.id,
            aggregate_version=package.version + 1,
            actor=actor,
            payload={
                **payload,
                "applied_event_ids": [str(item.event_id) for item in ready],
                "ignored_event_ids": [
                    str(item.event_id) for item in inbox if item.status in {"IGNORED", "REJECTED"}
                ],
                "domain_effect": "SAFE_TEST_ONLY",
            },
            offline_epoch_id=package.epoch_id,
        )
        now = datetime.now(UTC)
        for item in ready:
            item.status = "APPLIED"
            item.applied_at = now
        package.status = "APPLIED"
        package.applied_by_user_id = principal.user_id
        package.applied_event_id = event.event_id
        package.applied_at = now
        package.version += 1
        local_node = await self._local_node(session)
        receipt_payload = {
            "receipt_id": str(uuid4()),
            "package_id": str(package.id),
            "source_node_code": local_node.node_code,
            "target_node_code": peer.node_code,
            "manifest_hash": package.manifest_hash,
            "archive_hash": package.archive_hash,
            "applied_count": len(ready),
            "ignored_count": len(inbox) - len(ready),
            "remote_sequence": package.sequence_last,
            "created_at": utc_timestamp(now),
        }
        receipt_hash = payload_hash(receipt_payload)
        receipt_signature = self.journal.signer.sign(canonicalize(receipt_payload))
        session.add(
            SyncReceipt(
                id=UUID(str(receipt_payload["receipt_id"])),
                package_id=package.id,
                receipt_payload=receipt_payload,
                receipt_hash=receipt_hash,
                signature=receipt_signature,
                event_id=event.event_id,
            )
        )
        checkpoint_payload = {
            "peer_node_id": str(peer.id),
            "package_id": str(package.id),
            "local_sequence": event.local_sequence,
            "remote_sequence": package.sequence_last,
            "local_event_hash": event.event_hash,
            "remote_event_hash": (
                max(inbox, key=lambda item: item.local_sequence).event_hash if inbox else None
            ),
            "receipt_hash": receipt_hash,
        }
        checkpoint_hash = payload_hash(checkpoint_payload)
        session.add(
            FederationCheckpoint(
                id=uuid4(),
                peer_node_id=peer.id,
                package_id=package.id,
                local_sequence=event.local_sequence,
                remote_sequence=package.sequence_last,
                local_event_hash=event.event_hash,
                remote_event_hash=cast(str | None, checkpoint_payload["remote_event_hash"]),
                checkpoint_hash=checkpoint_hash,
                event_id=event.event_id,
            )
        )
        peer.last_sync_at = now
        peer.last_checkpoint_hash = checkpoint_hash
        peer.updated_at = now
        peer.version += 1
        await audit_federation_action(
            session,
            principal,
            "SYNC_PACKAGE_APPLIED",
            "SyncPackage",
            package.id,
            event.event_id,
            request_id,
        )
        return complete_federation_command(record, event.event_id, package.id)

    async def _detect_event_conflict(
        self,
        session: AsyncSession,
        source_node_id: UUID,
        parsed: ParsedInboundEvent,
        inbox_event_id: UUID,
        expected_previous: str | None,
    ) -> ConflictInput | None:
        if parsed.previous_event_hash != expected_previous:
            return ConflictInput(
                ConflictClass.REFERENTIAL_GAP,
                inbox_event_id,
                parsed.aggregate_type,
                parsed.aggregate_id,
                None,
                parsed.event_id,
                expected_previous,
                parsed.previous_event_hash,
            )
        same_sequence = await session.scalar(
            select(InboxEvent).where(
                InboxEvent.source_node_id == source_node_id,
                InboxEvent.local_sequence == parsed.local_sequence,
            )
        )
        if same_sequence is not None:
            return ConflictInput(
                ConflictClass.TAMPERED_DUPLICATE,
                inbox_event_id,
                parsed.aggregate_type,
                parsed.aggregate_id,
                same_sequence.event_id,
                parsed.event_id,
                same_sequence.event_hash,
                parsed.event_hash,
            )
        remote_branch = await session.scalar(
            select(InboxEvent).where(
                InboxEvent.aggregate_type == parsed.aggregate_type,
                InboxEvent.aggregate_id == parsed.aggregate_id,
                InboxEvent.aggregate_version == parsed.aggregate_version,
                InboxEvent.event_hash != parsed.event_hash,
            )
        )
        local_branch = await session.scalar(
            select(SignedEvent).where(
                SignedEvent.aggregate_type == parsed.aggregate_type,
                SignedEvent.aggregate_id == parsed.aggregate_id,
                SignedEvent.aggregate_version == parsed.aggregate_version,
                SignedEvent.event_hash != parsed.event_hash,
            )
        )
        conflicting = remote_branch or local_branch
        if conflicting is None:
            return None
        event_type = parsed.event_type.lower()
        conflict_class = (
            ConflictClass.DOUBLE_REDEMPTION
            if "redeem" in event_type
            else ConflictClass.CUSTODY_CONFLICT
            if "custody" in event_type
            else ConflictClass.COMPETING_RESERVATION
            if "reservation" in event_type or "right" in event_type
            else ConflictClass.CONCURRENT_METADATA
        )
        return ConflictInput(
            conflict_class,
            inbox_event_id,
            parsed.aggregate_type,
            parsed.aggregate_id,
            conflicting.event_id,
            parsed.event_id,
            conflicting.event_hash,
            parsed.event_hash,
        )

    def _parse_and_verify_events(
        self,
        raw_events: list[dict[str, object]],
        manifest: dict[str, object],
        peer: ExternalNode,
        certificate: NodeCertificate,
        allowed_event_types: set[str],
        expected_epoch_id: UUID | None,
    ) -> list[ParsedInboundEvent]:
        result: list[ParsedInboundEvent] = []
        sequence_first = self._manifest_int(manifest, "sequence_first")
        sequence_last = self._manifest_int(manifest, "sequence_last")
        expected_sequence = sequence_first
        expected_previous = cast(str | None, manifest.get("base_checkpoint_hash"))
        for wrapper in raw_events:
            envelope = wrapper.get("envelope")
            if not isinstance(envelope, dict):
                raise federation_error("SYNC_EVENT_ENVELOPE_INVALID", 422)
            event_hash = wrapper.get("event_hash")
            fingerprint = wrapper.get("key_fingerprint")
            signature_text = wrapper.get("signature")
            if (
                not isinstance(event_hash, str)
                or not isinstance(fingerprint, str)
                or not isinstance(signature_text, str)
                or fingerprint != certificate.fingerprint
            ):
                raise federation_error("SYNC_EVENT_SIGNATURE_METADATA_INVALID", 422)
            try:
                signature = base64.b64decode(signature_text, validate=True)
                event_id = UUID(str(envelope["event_id"]))
                node_id = UUID(str(envelope["node_id"]))
                local_sequence = int(str(envelope["local_sequence"]))
                aggregate = cast(dict[str, object], envelope["aggregate"])
                aggregate_id = UUID(str(aggregate["id"]))
                aggregate_version = int(str(aggregate["version"]))
                aggregate_type = str(aggregate["type"])
                event_type = str(envelope["event_type"])
                previous_event_hash = cast(str | None, envelope.get("previous_event_hash"))
                raw_epoch_id = envelope.get("offline_epoch_id")
                event_epoch_id = UUID(str(raw_epoch_id)) if raw_epoch_id is not None else None
            except (ValueError, KeyError, TypeError) as exc:
                raise federation_error("SYNC_EVENT_ENVELOPE_INVALID", 422) from exc
            canonical = canonicalize(envelope)
            if (
                node_id != peer.id
                or local_sequence != expected_sequence
                or local_sequence > sequence_last
                or previous_event_hash != expected_previous
                or event_epoch_id != expected_epoch_id
                or event_type not in allowed_event_types
                or envelope.get("protocol_version") != SYNC_PROTOCOL_VERSION
                or event_hash != sha256_ref(canonical)
                or envelope.get("payload_hash") != payload_hash(envelope.get("payload"))
                or not verify_signature(certificate.public_key, signature, canonical)
            ):
                raise federation_error("SYNC_EVENT_VERIFICATION_FAILED", 422)
            result.append(
                ParsedInboundEvent(
                    event_id,
                    event_type,
                    local_sequence,
                    aggregate_type,
                    aggregate_id,
                    aggregate_version,
                    previous_event_hash,
                    event_hash,
                    envelope,
                    signature,
                )
            )
            expected_previous = event_hash
            expected_sequence += 1
        if (
            not result
            or result[0].local_sequence != sequence_first
            or result[-1].local_sequence != sequence_last
        ):
            raise federation_error("SYNC_EVENT_SEQUENCE_RANGE_INVALID", 422)
        return result

    def _verify_manifest(
        self,
        manifest: dict[str, object],
        peer: ExternalNode,
        local_node_id: UUID,
        local_node_code: str,
        contract: NodeTrustContract,
    ) -> None:
        created_at = self._manifest_datetime(manifest, "created_at")
        expires_at = self._manifest_datetime(manifest, "expires_at")
        sequence_first = self._manifest_int(manifest, "sequence_first")
        sequence_last = self._manifest_int(manifest, "sequence_last")
        event_count = self._manifest_int(manifest, "event_count")
        required_capabilities = manifest.get("required_capabilities")
        capabilities_valid = (
            isinstance(required_capabilities, list)
            and all(isinstance(item, str) and item for item in required_capabilities)
            and set(required_capabilities).issubset(contract.capabilities)
            and set(required_capabilities).issubset(peer.capabilities)
        )
        if (
            self._manifest_uuid(manifest, "source_node_id") != peer.id
            or self._manifest_text(manifest, "source_node_code") != peer.node_code
            or self._manifest_uuid(manifest, "target_node_id") != local_node_id
            or self._manifest_text(manifest, "target_node_code") != local_node_code
            or self._manifest_text(manifest, "protocol_version") != SYNC_PROTOCOL_VERSION
            or self._manifest_uuid(manifest, "contract_id") != contract.id
            or not capabilities_valid
            or expires_at <= datetime.now(UTC)
            or created_at > datetime.now(UTC) + timedelta(minutes=10)
            or created_at < contract.valid_from
            or expires_at > contract.valid_until
            or created_at >= expires_at
            or sequence_first < 1
            or sequence_last < sequence_first
            or event_count < 1
            or event_count > self.settings.sync_package_max_events
            or sequence_last - sequence_first + 1 != event_count
            or self._manifest_int(manifest, "blob_count") < 0
        ):
            raise federation_error("SYNC_MANIFEST_INVALID", 422)

    @staticmethod
    def _verify_archive_content(files: dict[str, bytes], manifest: dict[str, object]) -> None:
        hashes = manifest.get("file_hashes")
        if not isinstance(hashes, dict):
            raise federation_error("SYNC_FILE_HASHES_INVALID", 422)
        for name, expected in hashes.items():
            if not isinstance(name, str) or not isinstance(expected, str) or name not in files:
                raise federation_error("SYNC_FILE_HASHES_INVALID", 422)
            if sha256_ref(files[name]) != expected:
                raise federation_error("SYNC_FILE_HASH_MISMATCH", 422)

    @staticmethod
    def _verify_package_certificate(
        payload: dict[str, object],
        peer: ExternalNode,
        certificate: NodeCertificate,
    ) -> None:
        try:
            public_key = base64.b64decode(str(payload["public_key"]), validate=True)
        except (KeyError, ValueError) as exc:
            raise federation_error("SYNC_CERTIFICATE_INVALID", 422) from exc
        if (
            str(payload.get("node_id")) != str(peer.id)
            or str(payload.get("fingerprint")) != certificate.fingerprint
            or str(payload.get("algorithm")) != "Ed25519"
            or public_key != certificate.public_key
        ):
            raise federation_error("SYNC_CERTIFICATE_MISMATCH", 422)

    def _package_relative_path(self, direction: str, package_id: UUID) -> str:
        return f"sync-packages/{direction}/{package_id}.zip"

    def _absolute_package_path(self, relative: str) -> Path:
        root = (self.settings.blob_root / "sync-packages").resolve()
        candidate = (self.settings.blob_root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            raise federation_error("SYNC_ARCHIVE_PATH_INVALID", 500)
        return candidate

    @staticmethod
    def _manifest_text(manifest: dict[str, object], key: str) -> str:
        value = manifest.get(key)
        if not isinstance(value, str) or not value:
            raise federation_error("SYNC_MANIFEST_INVALID", 422)
        return value

    @classmethod
    def _manifest_uuid(cls, manifest: dict[str, object], key: str) -> UUID:
        try:
            return UUID(cls._manifest_text(manifest, key))
        except ValueError as exc:
            raise federation_error("SYNC_MANIFEST_INVALID", 422) from exc

    @classmethod
    def _manifest_optional_uuid(cls, manifest: dict[str, object], key: str) -> UUID | None:
        if manifest.get(key) is None:
            return None
        return cls._manifest_uuid(manifest, key)

    @staticmethod
    def _manifest_int(manifest: dict[str, object], key: str) -> int:
        value = manifest.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise federation_error("SYNC_MANIFEST_INVALID", 422)
        return value

    @classmethod
    def _manifest_datetime(cls, manifest: dict[str, object], key: str) -> datetime:
        try:
            return datetime.fromisoformat(
                cls._manifest_text(manifest, key).replace("Z", "+00:00")
            ).astimezone(UTC)
        except ValueError as exc:
            raise federation_error("SYNC_MANIFEST_INVALID", 422) from exc
