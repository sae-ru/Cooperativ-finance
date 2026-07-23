"""Evidence upload lifecycle and immutable link validation."""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import PurePath
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.inventory.application.common import (
    InventoryCommandResult,
    actor_claim,
    begin_command,
    bounded_text,
    complete_command,
    inventory_error,
)
from cooperative_clearing.modules.inventory.infrastructure.blob_store import EncryptedBlobStore
from cooperative_clearing.modules.inventory.infrastructure.models import EvidenceBlob
from cooperative_clearing.modules.journal.application.service import SignedJournalService
from cooperative_clearing.shared.core.config import Settings

EVIDENCE_ROLES = {
    RoleCode.COOPERATIVE_ADMIN,
    RoleCode.DATA_STEWARD,
    RoleCode.WAREHOUSE_CUSTODIAN,
    RoleCode.INVENTORY_CONTROLLER,
    RoleCode.LOGISTICS_OPERATOR,
    RoleCode.RIGHTS_OPERATOR,
    RoleCode.RISK_ADMIN,
    RoleCode.AUDITOR,
}
SOLIDARITY_EVIDENCE_KIND = "SOLIDARITY_AID"
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "text/plain",
}


def evidence_roles(kind: str) -> set[RoleCode]:
    """Allow a cooperative participant to attest their own solidarity action."""
    if kind.strip().upper() == SOLIDARITY_EVIDENCE_KIND:
        return set(RoleCode)
    return EVIDENCE_ROLES


class EvidenceService:
    def __init__(self, settings: Settings) -> None:
        self.journal = SignedJournalService(settings)
        self.store = EncryptedBlobStore(settings.blob_root, settings.blob_encryption_key_file)

    async def create_intent(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        expected_sha256: str,
        expected_size: int,
        mime_type: str,
        kind: str,
        original_name: str,
        access_scope: str,
        retention_until: datetime | None,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        digest = expected_sha256.casefold()
        normalized_mime = mime_type.strip().casefold()
        normalized_kind = bounded_text(kind, "EVIDENCE_KIND_INVALID", 40).upper()
        normalized_name = bounded_text(original_name, "EVIDENCE_NAME_INVALID", 255)
        normalized_scope = bounded_text(access_scope, "EVIDENCE_SCOPE_INVALID", 40).upper()
        if (
            PurePath(normalized_name).name != normalized_name
            or normalized_mime not in ALLOWED_MIME_TYPES
        ):
            raise inventory_error("EVIDENCE_TYPE_INVALID")
        if expected_size < 1 or expected_size > 26_214_400:
            raise inventory_error("EVIDENCE_SIZE_INVALID")
        if retention_until is not None and retention_until.astimezone(UTC) <= datetime.now(UTC):
            raise inventory_error("EVIDENCE_RETENTION_INVALID")
        payload = {
            "cooperative_id": str(cooperative_id),
            "expected_sha256": digest,
            "expected_size": expected_size,
            "mime_type": normalized_mime,
            "kind": normalized_kind,
            "original_name": normalized_name,
            "access_scope": normalized_scope,
            "retention_until": retention_until.isoformat() if retention_until else None,
        }
        record, replay = await begin_command(
            session, principal, "EVIDENCE_CREATE_UPLOAD_INTENT", idempotency_key, payload
        )
        if replay is not None:
            return replay
        actor = actor_claim(principal, cooperative_id, evidence_roles(normalized_kind))
        evidence_id = uuid4()
        event = await self.journal.append(
            session,
            event_type="evidence.upload_intent_created",
            aggregate_type="evidence_blob",
            aggregate_id=evidence_id,
            aggregate_version=1,
            actor=actor,
            payload=payload,
        )
        session.add(
            EvidenceBlob(
                id=evidence_id,
                cooperative_id=cooperative_id,
                expected_sha256=digest,
                expected_size=expected_size,
                mime_type=normalized_mime,
                kind=normalized_kind,
                original_name=normalized_name,
                access_scope=normalized_scope,
                retention_until=retention_until,
                status="PENDING",
                storage_key=None,
                encryption_algorithm=None,
                created_by_user_id=principal.user_id,
                created_event_id=event.event_id,
                completed_event_id=None,
            )
        )
        await self._audit(
            session,
            principal,
            cooperative_id,
            "EVIDENCE_UPLOAD_INTENT_CREATED",
            evidence_id,
            event.event_id,
            request_id,
        )
        return complete_command(record, event.event_id, evidence_id)

    async def store_content(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        evidence_id: UUID,
        chunks: AsyncIterator[bytes],
        request_id: UUID | None,
    ) -> InventoryCommandResult:
        evidence = await session.get(EvidenceBlob, evidence_id, with_for_update=True)
        if evidence is None:
            raise inventory_error("EVIDENCE_NOT_FOUND", 404)
        actor = actor_claim(
            principal,
            evidence.cooperative_id,
            evidence_roles(evidence.kind),
        )
        if evidence.created_by_user_id != principal.user_id:
            raise inventory_error("EVIDENCE_UPLOADER_REQUIRED", 403)
        if evidence.status == "READY":
            if evidence.completed_event_id is None:
                raise inventory_error("EVIDENCE_STATE_INVALID", 500)
            return InventoryCommandResult(evidence.completed_event_id, evidence.id, True)
        if evidence.status != "PENDING":
            raise inventory_error("EVIDENCE_NOT_PENDING", 409)
        stored = await self.store.put(
            cooperative_id=evidence.cooperative_id,
            expected_sha256=evidence.expected_sha256,
            expected_size=evidence.expected_size,
            chunks=chunks,
        )
        event = await self.journal.append(
            session,
            event_type="evidence.blob_stored",
            aggregate_type="evidence_blob",
            aggregate_id=evidence.id,
            aggregate_version=2,
            actor=actor,
            payload={
                "evidence_id": str(evidence.id),
                "sha256": stored.sha256,
                "size": stored.size,
                "mime_type": evidence.mime_type,
                "kind": evidence.kind,
                "encryption_algorithm": stored.encryption_algorithm,
            },
        )
        evidence.status = "READY"
        evidence.storage_key = stored.storage_key
        evidence.encryption_algorithm = stored.encryption_algorithm
        evidence.ready_at = datetime.now(UTC)
        evidence.completed_event_id = event.event_id
        await self._audit(
            session,
            principal,
            evidence.cooperative_id,
            "EVIDENCE_BLOB_STORED",
            evidence.id,
            event.event_id,
            request_id,
        )
        return InventoryCommandResult(event.event_id, evidence.id, False)

    def read_content(self, evidence: EvidenceBlob) -> bytes:
        if evidence.status != "READY" or evidence.storage_key is None:
            raise inventory_error("EVIDENCE_NOT_READY", 409)
        return self.store.read_verified(
            cooperative_id=evidence.cooperative_id,
            storage_key=evidence.storage_key,
            expected_sha256=evidence.expected_sha256,
            expected_size=evidence.expected_size,
        )

    @staticmethod
    async def require_ready(
        session: AsyncSession,
        cooperative_id: UUID,
        evidence_ids: Sequence[UUID],
        *,
        required: bool,
    ) -> list[EvidenceBlob]:
        unique_ids = tuple(dict.fromkeys(evidence_ids))
        if required and not unique_ids:
            raise inventory_error("EVIDENCE_REQUIRED", 422)
        if not unique_ids:
            return []
        items = list(
            (
                await session.execute(
                    select(EvidenceBlob).where(
                        EvidenceBlob.id.in_(unique_ids),
                        EvidenceBlob.cooperative_id == cooperative_id,
                        EvidenceBlob.status == "READY",
                    )
                )
            ).scalars()
        )
        if len(items) != len(unique_ids):
            raise inventory_error("EVIDENCE_NOT_READY", 409)
        return items

    @staticmethod
    async def _audit(
        session: AsyncSession,
        principal: Principal,
        cooperative_id: UUID,
        action: str,
        evidence_id: UUID,
        event_id: UUID,
        request_id: UUID | None,
    ) -> None:
        await AuditRepository(session).record(
            action=action,
            object_type="EvidenceBlob",
            object_id=evidence_id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={"signed_event_id": str(event_id)},
        )
