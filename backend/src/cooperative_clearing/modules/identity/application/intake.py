"""Duplicate-aware member intake and independently approved staging imports."""

import csv
import io
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from cooperative_clearing.modules.audit.infrastructure.models import IdempotencyRecord
from cooperative_clearing.modules.audit.infrastructure.repository import (
    AuditRepository,
    IdempotencyRepository,
    request_payload_hash,
)
from cooperative_clearing.modules.identity.domain.types import (
    MemberImportRowStatus,
    MemberImportStatus,
    Principal,
)
from cooperative_clearing.modules.identity.infrastructure.models import (
    Cooperative,
    Member,
    MemberIdentifier,
    MemberImportBatch,
    MemberImportRow,
    Membership,
)
from cooperative_clearing.shared.core.security import private_value_hash
from cooperative_clearing.shared.domain.errors import DomainError

MAX_IMPORT_BYTES = 1_000_000
MAX_IMPORT_ROWS = 500
IMPORT_CHUNK_SIZE = 100
_IDENTIFIER_TYPE = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")
_STRUCTURAL_ERRORS = frozenset(
    {
        "IMPORT_ROW_NAME_INVALID",
        "IMPORT_ROW_NAME_TOO_LONG",
        "IMPORT_ROW_IDENTIFIER_INCOMPLETE",
        "IMPORT_ROW_IDENTIFIER_TYPE_INVALID",
        "IMPORT_ROW_IDENTIFIER_VALUE_INVALID",
    }
)
_HEADER_ALIASES = {
    "display_name": "display_name",
    "name": "display_name",
    "имя": "display_name",
    "имя_участника": "display_name",
    "identifier_type": "identifier_type",
    "тип_идентификатора": "identifier_type",
    "identifier_value": "identifier_value",
    "identifier": "identifier_value",
    "идентификатор": "identifier_value",
}


@dataclass(frozen=True, slots=True)
class IntakeCommandResult:
    event_id: UUID
    object_id: UUID
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class MemberImportInput:
    row_number: int
    display_name: str
    identifier_type: str | None
    identifier_value: str | None


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    member_id: UUID
    display_name: str
    registered_by_cooperative_id: UUID | None
    status: str
    match_basis: str


def normalize_member_name(value: str) -> str:
    return " ".join(value.strip().lower().split())


def parse_member_import_csv(csv_text: str) -> list[MemberImportInput]:
    if len(csv_text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise DomainError(
            code="MEMBER_IMPORT_FILE_TOO_LARGE",
            message_key="errors.identity.member_import_file_too_large",
            parameters={"maximum_bytes": MAX_IMPORT_BYTES},
            status_code=413,
        )
    source = csv_text.lstrip("\ufeff")
    if not source.strip():
        raise _import_error("MEMBER_IMPORT_FILE_EMPTY")
    try:
        dialect = csv.Sniffer().sniff(source[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    try:
        reader = csv.DictReader(io.StringIO(source, newline=""), dialect=dialect)
        if not reader.fieldnames:
            raise _import_error("MEMBER_IMPORT_HEADER_INVALID")
        mapped_headers: dict[str, str] = {}
        unknown_headers: list[str] = []
        for original in reader.fieldnames:
            normalized = original.strip().lower().replace(" ", "_")
            canonical = _HEADER_ALIASES.get(normalized)
            if canonical is None:
                unknown_headers.append(original)
            elif canonical in mapped_headers.values():
                raise _import_error("MEMBER_IMPORT_HEADER_INVALID")
            else:
                mapped_headers[original] = canonical
        if unknown_headers or "display_name" not in mapped_headers.values():
            raise _import_error("MEMBER_IMPORT_HEADER_INVALID")

        parsed: list[MemberImportInput] = []
        for source_row in reader:
            if None in source_row:
                raise _import_error("MEMBER_IMPORT_ROW_SHAPE_INVALID")
            canonical_row = {
                mapped_headers[key]: (value or "").strip()
                for key, value in source_row.items()
                if key in mapped_headers
            }
            if not any(canonical_row.values()):
                continue
            parsed.append(
                MemberImportInput(
                    row_number=max(reader.line_num, 2),
                    display_name=canonical_row.get("display_name", ""),
                    identifier_type=canonical_row.get("identifier_type") or None,
                    identifier_value=canonical_row.get("identifier_value") or None,
                )
            )
            if len(parsed) > MAX_IMPORT_ROWS:
                raise DomainError(
                    code="MEMBER_IMPORT_ROW_LIMIT_EXCEEDED",
                    message_key="errors.identity.member_import_row_limit_exceeded",
                    parameters={"maximum_rows": MAX_IMPORT_ROWS},
                    status_code=422,
                )
    except DomainError:
        raise
    except (csv.Error, UnicodeError) as exc:
        raise _import_error("MEMBER_IMPORT_CSV_INVALID") from exc
    if not parsed:
        raise _import_error("MEMBER_IMPORT_FILE_EMPTY")
    return parsed


async def acquire_member_intake_lock(session: AsyncSession, cooperative_id: UUID) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"cooperative-clearing:member-intake:{cooperative_id}"},
    )


async def find_member_duplicate_candidates(
    session: AsyncSession,
    *,
    cooperative_id: UUID,
    display_name: str,
    identifier_type: str | None,
    identifier_value: str | None,
) -> list[DuplicateCandidate]:
    identifier_hash, structural_error = _validated_identifier(
        display_name, identifier_type, identifier_value
    )
    if structural_error:
        raise _import_error(structural_error)
    identifier_key = (
        (identifier_type.strip().upper(), identifier_hash)
        if identifier_type and identifier_hash
        else None
    )
    identifiers, names = await _existing_matches(
        session,
        cooperative_id=cooperative_id,
        identifier_keys={identifier_key} if identifier_key else set(),
        normalized_names={normalize_member_name(display_name)},
    )
    candidates: dict[UUID, DuplicateCandidate] = {}
    if identifier_key and identifier_key in identifiers:
        member = identifiers[identifier_key]
        candidates[member.id] = _candidate(member, "EXACT_IDENTIFIER")
    for member in names.get(normalize_member_name(display_name), []):
        candidates.setdefault(member.id, _candidate(member, "NORMALIZED_NAME"))
    return sorted(
        candidates.values(),
        key=lambda item: (
            item.match_basis != "EXACT_IDENTIFIER",
            item.display_name,
            str(item.member_id),
        ),
    )


class MemberIntakeService:
    async def stage_import(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        source_name: str,
        csv_text: str,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> IntakeCommandResult:
        inputs = parse_member_import_csv(csv_text)
        safe_name = source_name.strip().replace("\\", "/").split("/")[-1]
        if not safe_name or len(safe_name) > 200:
            raise _import_error("MEMBER_IMPORT_SOURCE_NAME_INVALID")

        prepared: list[tuple[MemberImportInput, str | None, str | None, str | None]] = []
        safe_rows: list[dict[str, object]] = []
        for item in inputs:
            identifier_hash, error_code = _validated_identifier(
                item.display_name, item.identifier_type, item.identifier_value
            )
            normalized_type = item.identifier_type.strip().upper() if item.identifier_type else None
            row_hash = request_payload_hash(
                {
                    "row_number": item.row_number,
                    "display_name": item.display_name,
                    "identifier_type": normalized_type,
                    "identifier_hash": identifier_hash,
                }
            )
            prepared.append((item, normalized_type, identifier_hash, error_code))
            safe_rows.append({"row_hash": row_hash, "error_code": error_code})
        source_sha256 = request_payload_hash(safe_rows)
        payload = {
            "cooperative_id": cooperative_id,
            "source_name": safe_name,
            "source_sha256": source_sha256,
            "row_count": len(prepared),
        }
        record, replay = await self._begin(
            session, principal, "MEMBER_IMPORT_STAGE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        cooperative = await session.get(Cooperative, cooperative_id)
        if cooperative is None:
            raise _not_found("COOPERATIVE_NOT_FOUND")
        if cooperative.status != "ACTIVE":
            raise DomainError(
                code="COOPERATIVE_NOT_ACTIVE",
                message_key="errors.identity.cooperative_not_active",
                status_code=409,
            )

        batch = MemberImportBatch(
            id=uuid4(),
            cooperative_id=cooperative_id,
            source_name=safe_name,
            source_sha256=source_sha256,
            status=MemberImportStatus.STAGED.value,
            row_count=len(prepared),
            invalid_count=sum(1 for _item, _type, _hash, error in prepared if error),
            created_by_user_id=principal.user_id,
        )
        session.add(batch)
        for item, identifier_type, identifier_hash, error_code in prepared:
            source_row_hash = request_payload_hash(
                {
                    "row_number": item.row_number,
                    "display_name": item.display_name,
                    "identifier_type": identifier_type,
                    "identifier_hash": identifier_hash,
                }
            )
            session.add(
                MemberImportRow(
                    id=uuid4(),
                    batch_id=batch.id,
                    row_number=item.row_number,
                    display_name=item.display_name,
                    identifier_type=identifier_type,
                    identifier_hash=identifier_hash,
                    source_row_hash=source_row_hash,
                    status=(
                        MemberImportRowStatus.INVALID.value
                        if error_code
                        else MemberImportRowStatus.STAGED.value
                    ),
                    error_code=error_code,
                )
            )
        event_id = await AuditRepository(session).record(
            action="MEMBER_IMPORT_STAGED",
            object_type="MemberImportBatch",
            object_id=batch.id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={
                "source_sha256": source_sha256,
                "row_count": batch.row_count,
                "invalid_count": batch.invalid_count,
            },
        )
        return self._complete(record, event_id, batch.id)

    async def preview_import(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        batch_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> IntakeCommandResult:
        payload = {"batch_id": batch_id, "expected_version": expected_version}
        record, replay = await self._begin(
            session, principal, "MEMBER_IMPORT_PREVIEW", idempotency_key, payload
        )
        if replay is not None:
            return replay
        batch = await session.get(MemberImportBatch, batch_id, with_for_update=True)
        if batch is None:
            raise _not_found("MEMBER_IMPORT_NOT_FOUND")
        _require_version(batch.version, expected_version)
        if batch.status not in {
            MemberImportStatus.STAGED.value,
            MemberImportStatus.PREVIEWED.value,
            MemberImportStatus.APPROVED.value,
        }:
            raise _state_error(batch.status, "PREVIEW")
        rows = await _batch_rows(session, batch.id)
        ready, invalid, duplicates = await _evaluate_rows(
            session, cooperative_id=batch.cooperative_id, rows=rows
        )
        now = datetime.now(UTC)
        batch.status = MemberImportStatus.PREVIEWED.value
        batch.ready_count = ready
        batch.invalid_count = invalid
        batch.duplicate_count = duplicates
        batch.reviewed_by_user_id = None
        batch.decision_reason_code = None
        batch.reviewed_at = None
        batch.previewed_at = now
        batch.updated_at = now
        batch.version += 1
        event_id = await AuditRepository(session).record(
            action="MEMBER_IMPORT_PREVIEWED",
            object_type="MemberImportBatch",
            object_id=batch.id,
            cooperative_id=batch.cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={
                "source_sha256": batch.source_sha256,
                "ready_count": ready,
                "invalid_count": invalid,
                "duplicate_count": duplicates,
            },
        )
        return self._complete(record, event_id, batch.id)

    async def decide_import(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        batch_id: UUID,
        approve: bool,
        reason_code: str,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> IntakeCommandResult:
        payload = {
            "batch_id": batch_id,
            "approve": approve,
            "reason_code": reason_code,
            "expected_version": expected_version,
        }
        record, replay = await self._begin(
            session, principal, "MEMBER_IMPORT_DECIDE", idempotency_key, payload
        )
        if replay is not None:
            return replay
        batch = await session.get(MemberImportBatch, batch_id, with_for_update=True)
        if batch is None:
            raise _not_found("MEMBER_IMPORT_NOT_FOUND")
        _require_version(batch.version, expected_version)
        if batch.status != MemberImportStatus.PREVIEWED.value:
            raise _state_error(batch.status, "DECIDE")
        if batch.created_by_user_id == principal.user_id:
            raise DomainError(
                code="MEMBER_IMPORT_INDEPENDENT_REVIEW_REQUIRED",
                message_key="errors.identity.member_import_independent_review_required",
                status_code=409,
            )
        if approve and batch.ready_count < 1:
            raise DomainError(
                code="MEMBER_IMPORT_NO_READY_ROWS",
                message_key="errors.identity.member_import_no_ready_rows",
                status_code=409,
            )
        now = datetime.now(UTC)
        batch.status = (
            MemberImportStatus.APPROVED.value
            if approve
            else MemberImportStatus.REJECTED.value
        )
        batch.reviewed_by_user_id = principal.user_id
        batch.decision_reason_code = reason_code
        batch.reviewed_at = now
        batch.updated_at = now
        batch.version += 1
        event_id = await AuditRepository(session).record(
            action="MEMBER_IMPORT_APPROVED" if approve else "MEMBER_IMPORT_REJECTED",
            object_type="MemberImportBatch",
            object_id=batch.id,
            cooperative_id=batch.cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            reason_code=reason_code,
            request_id=request_id,
            payload={
                "created_by_user_id": str(batch.created_by_user_id),
                "source_sha256": batch.source_sha256,
                "ready_count": batch.ready_count,
                "invalid_count": batch.invalid_count,
                "duplicate_count": batch.duplicate_count,
            },
        )
        return self._complete(record, event_id, batch.id)

    async def apply_import(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        batch_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
    ) -> IntakeCommandResult:
        payload = {"batch_id": batch_id, "expected_version": expected_version}
        record, replay = await self._begin(
            session, principal, "MEMBER_IMPORT_APPLY", idempotency_key, payload
        )
        if replay is not None:
            return replay
        batch = await session.get(MemberImportBatch, batch_id, with_for_update=True)
        if batch is None:
            raise _not_found("MEMBER_IMPORT_NOT_FOUND")
        _require_version(batch.version, expected_version)
        if batch.status != MemberImportStatus.APPROVED.value:
            raise _state_error(batch.status, "APPLY")
        if (
            batch.reviewed_by_user_id is None
            or batch.reviewed_by_user_id == batch.created_by_user_id
        ):
            raise DomainError(
                code="MEMBER_IMPORT_INDEPENDENT_REVIEW_REQUIRED",
                message_key="errors.identity.member_import_independent_review_required",
                status_code=409,
            )

        await acquire_member_intake_lock(session, batch.cooperative_id)
        rows = await _batch_rows(session, batch.id)
        approved_ready_ids = {
            row.id for row in rows if row.status == MemberImportRowStatus.READY.value
        }
        await _evaluate_rows(session, cooperative_id=batch.cooperative_id, rows=rows)
        current_ready_ids = {
            row.id for row in rows if row.status == MemberImportRowStatus.READY.value
        }
        if approved_ready_ids != current_ready_ids:
            raise DomainError(
                code="MEMBER_IMPORT_PREVIEW_STALE",
                message_key="errors.identity.member_import_preview_stale",
                status_code=409,
            )

        ready_rows = [row for row in rows if row.id in approved_ready_ids]
        now = datetime.now(UTC)
        for offset in range(0, len(ready_rows), IMPORT_CHUNK_SIZE):
            for row in ready_rows[offset : offset + IMPORT_CHUNK_SIZE]:
                member = Member(
                    id=uuid4(),
                    display_name=row.display_name.strip(),
                    registered_by_cooperative_id=batch.cooperative_id,
                    status="APPLICANT",
                )
                session.add(member)
                if row.identifier_type and row.identifier_hash:
                    session.add(
                        MemberIdentifier(
                            id=uuid4(),
                            member_id=member.id,
                            identifier_type=row.identifier_type,
                            value_hash=row.identifier_hash,
                        )
                    )
                row.status = MemberImportRowStatus.APPLIED.value
                row.created_member_id = member.id
                row.applied_at = now
            await session.flush()

        batch.status = MemberImportStatus.APPLIED.value
        batch.applied_count = len(ready_rows)
        batch.applied_at = now
        batch.updated_at = now
        batch.version += 1
        event_id = await AuditRepository(session).record(
            action="MEMBER_IMPORT_APPLIED",
            object_type="MemberImportBatch",
            object_id=batch.id,
            cooperative_id=batch.cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            reason_code=batch.decision_reason_code,
            request_id=request_id,
            payload={
                "source_sha256": batch.source_sha256,
                "reviewed_by_user_id": str(batch.reviewed_by_user_id),
                "applied_count": batch.applied_count,
                "skipped_count": batch.invalid_count + batch.duplicate_count,
                "chunk_size": IMPORT_CHUNK_SIZE,
            },
        )
        return self._complete(record, event_id, batch.id)

    @staticmethod
    async def _begin(
        session: AsyncSession,
        principal: Principal,
        operation: str,
        idempotency_key: str,
        payload: object,
    ) -> tuple[IdempotencyRecord, IntakeCommandResult | None]:
        record = await IdempotencyRepository(session).begin(
            actor_user_id=principal.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_payload_hash(payload),
        )
        if record.status == "COMPLETED":
            stored = record.response_payload or {}
            return record, IntakeCommandResult(
                event_id=UUID(str(stored["event_id"])),
                object_id=UUID(str(stored["object_id"])),
                replayed=True,
            )
        return record, None

    @staticmethod
    def _complete(
        record: IdempotencyRecord, event_id: UUID, object_id: UUID
    ) -> IntakeCommandResult:
        IdempotencyRepository.complete(
            record,
            response_status=201,
            response_payload={"event_id": str(event_id), "object_id": str(object_id)},
        )
        return IntakeCommandResult(event_id=event_id, object_id=object_id)


async def _batch_rows(session: AsyncSession, batch_id: UUID) -> list[MemberImportRow]:
    result = await session.execute(
        select(MemberImportRow)
        .where(MemberImportRow.batch_id == batch_id)
        .order_by(MemberImportRow.row_number, MemberImportRow.id)
        .with_for_update()
    )
    return list(result.scalars())


async def _evaluate_rows(
    session: AsyncSession,
    *,
    cooperative_id: UUID,
    rows: Sequence[MemberImportRow],
) -> tuple[int, int, int]:
    valid_rows = [row for row in rows if _structural_error(row) is None]
    identifier_counts = Counter(
        (row.identifier_type, row.identifier_hash)
        for row in valid_rows
        if row.identifier_type and row.identifier_hash
    )
    name_counts = Counter(normalize_member_name(row.display_name) for row in valid_rows)
    identifier_keys = set(identifier_counts)
    normalized_names = set(name_counts)
    identifiers, names = await _existing_matches(
        session,
        cooperative_id=cooperative_id,
        identifier_keys=identifier_keys,
        normalized_names=normalized_names,
    )

    for row in rows:
        row.match_basis = None
        row.candidate_member_id = None
        error = _structural_error(row)
        if error:
            row.status = MemberImportRowStatus.INVALID.value
            row.error_code = error
            continue
        identifier_key = (
            (row.identifier_type, row.identifier_hash)
            if row.identifier_type and row.identifier_hash
            else None
        )
        normalized_name = normalize_member_name(row.display_name)
        if identifier_key and identifier_key in identifiers:
            row.status = MemberImportRowStatus.DUPLICATE.value
            row.error_code = "DUPLICATE_EXISTING_IDENTIFIER"
            row.match_basis = "EXACT_IDENTIFIER"
            row.candidate_member_id = identifiers[identifier_key].id
        elif names.get(normalized_name):
            row.status = MemberImportRowStatus.DUPLICATE.value
            row.error_code = "DUPLICATE_EXISTING_NAME"
            row.match_basis = "NORMALIZED_NAME"
            row.candidate_member_id = names[normalized_name][0].id
        elif identifier_key and identifier_counts[identifier_key] > 1:
            row.status = MemberImportRowStatus.DUPLICATE.value
            row.error_code = "DUPLICATE_IN_BATCH_IDENTIFIER"
            row.match_basis = "BATCH_IDENTIFIER"
        elif name_counts[normalized_name] > 1:
            row.status = MemberImportRowStatus.DUPLICATE.value
            row.error_code = "DUPLICATE_IN_BATCH_NAME"
            row.match_basis = "BATCH_NAME"
        else:
            row.status = MemberImportRowStatus.READY.value
            row.error_code = None

    ready = sum(row.status == MemberImportRowStatus.READY.value for row in rows)
    invalid = sum(row.status == MemberImportRowStatus.INVALID.value for row in rows)
    duplicates = sum(row.status == MemberImportRowStatus.DUPLICATE.value for row in rows)
    return ready, invalid, duplicates


async def _existing_matches(
    session: AsyncSession,
    *,
    cooperative_id: UUID,
    identifier_keys: set[tuple[str, str]],
    normalized_names: set[str],
) -> tuple[dict[tuple[str, str], Member], dict[str, list[Member]]]:
    scope = _member_scope_condition(cooperative_id)
    identifiers: dict[tuple[str, str], Member] = {}
    if identifier_keys:
        hashes = {value_hash for _identifier_type, value_hash in identifier_keys}
        result = await session.execute(
            select(MemberIdentifier.identifier_type, MemberIdentifier.value_hash, Member)
            .join(Member, Member.id == MemberIdentifier.member_id)
            .where(MemberIdentifier.value_hash.in_(hashes), scope)
            .order_by(Member.created_at, Member.id)
        )
        for identifier_type, value_hash, member in result.all():
            key = (str(identifier_type), str(value_hash))
            if key in identifier_keys:
                identifiers.setdefault(key, member)

    names: dict[str, list[Member]] = {}
    if normalized_names:
        normalized_expression = func.lower(
            func.regexp_replace(func.btrim(Member.display_name), r"\s+", " ", "g")
        )
        result = await session.execute(
            select(Member)
            .where(scope, normalized_expression.in_(normalized_names))
            .order_by(Member.created_at, Member.id)
        )
        for member in result.scalars():
            names.setdefault(normalize_member_name(member.display_name), []).append(member)
    return identifiers, names


def _member_scope_condition(cooperative_id: UUID) -> ColumnElement[bool]:
    membership_members = select(Membership.member_id).where(
        Membership.cooperative_id == cooperative_id
    )
    return or_(
        Member.registered_by_cooperative_id == cooperative_id,
        Member.id.in_(membership_members),
    )


def _validated_identifier(
    display_name: str,
    identifier_type: str | None,
    identifier_value: str | None,
) -> tuple[str | None, str | None]:
    clean_name = display_name.strip()
    if len(clean_name) < 2:
        return None, "IMPORT_ROW_NAME_INVALID"
    if len(clean_name) > 200:
        return None, "IMPORT_ROW_NAME_TOO_LONG"
    if (identifier_type is None) != (identifier_value is None):
        return None, "IMPORT_ROW_IDENTIFIER_INCOMPLETE"
    if identifier_type is None:
        return None, None
    normalized_type = identifier_type.strip().upper()
    if not _IDENTIFIER_TYPE.fullmatch(normalized_type):
        return None, "IMPORT_ROW_IDENTIFIER_TYPE_INVALID"
    normalized_value = (identifier_value or "").strip()
    if not normalized_value or len(normalized_value) > 300:
        return None, "IMPORT_ROW_IDENTIFIER_VALUE_INVALID"
    return private_value_hash(normalized_value), None


def _structural_error(row: MemberImportRow) -> str | None:
    if row.error_code in _STRUCTURAL_ERRORS:
        return row.error_code
    clean_name = row.display_name.strip()
    if len(clean_name) < 2:
        return "IMPORT_ROW_NAME_INVALID"
    if len(clean_name) > 200:
        return "IMPORT_ROW_NAME_TOO_LONG"
    if (row.identifier_type is None) != (row.identifier_hash is None):
        return "IMPORT_ROW_IDENTIFIER_INCOMPLETE"
    if row.identifier_type and not _IDENTIFIER_TYPE.fullmatch(row.identifier_type):
        return "IMPORT_ROW_IDENTIFIER_TYPE_INVALID"
    return None


def _candidate(member: Member, basis: str) -> DuplicateCandidate:
    return DuplicateCandidate(
        member_id=member.id,
        display_name=member.display_name,
        registered_by_cooperative_id=member.registered_by_cooperative_id,
        status=member.status,
        match_basis=basis,
    )


def _require_version(current: int, expected: int) -> None:
    if current != expected:
        raise DomainError(
            code="VERSION_CONFLICT",
            message_key="errors.request.version_conflict",
            parameters={"current_version": current},
            status_code=409,
        )


def _state_error(current: str, operation: str) -> DomainError:
    return DomainError(
        code="MEMBER_IMPORT_STATE_INVALID",
        message_key="errors.identity.member_import_state_invalid",
        parameters={"current": current, "operation": operation},
        status_code=409,
    )


def _not_found(code: str) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.identity.{code.lower()}",
        status_code=404,
    )


def _import_error(code: str) -> DomainError:
    return DomainError(
        code=code,
        message_key=f"errors.identity.{code.lower()}",
        status_code=422,
    )