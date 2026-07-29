"""Read-only proof that restored data and installed key material agree."""

import hmac
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pyotp
from sqlalchemy import select

from cooperative_clearing.modules.identity.application.security import MfaSecretCipher
from cooperative_clearing.modules.identity.infrastructure.models import AuthenticationFactor
from cooperative_clearing.modules.inventory.infrastructure.blob_store import EncryptedBlobStore
from cooperative_clearing.modules.inventory.infrastructure.models import EvidenceBlob
from cooperative_clearing.modules.journal.application.service import (
    signer_from_settings,
    verify_journal,
)
from cooperative_clearing.modules.node.infrastructure.models import NodeKeyRecord
from cooperative_clearing.modules.node.infrastructure.repository import NodeRepository
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.core.secrets import SecretFileError
from cooperative_clearing.shared.domain.errors import DomainError
from cooperative_clearing.shared.infrastructure.database import Database

MAX_REPORTED_FAILURES = 100


@dataclass(frozen=True, slots=True)
class RestoreConsistencyFailure:
    component: str
    code: str
    object_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RestoreConsistencyReport:
    ok: bool
    node_id: UUID | None
    journal_events: int
    active_node_signing_keys: int
    ready_evidence_records: int
    expected_blob_files: int
    verified_evidence_records: int
    mfa_factors: int
    verified_mfa_factors: int
    orphan_blob_files: int
    failure_count: int
    failures: tuple[RestoreConsistencyFailure, ...]


class _FailureCollector:
    def __init__(self) -> None:
        self.count = 0
        self.items: list[RestoreConsistencyFailure] = []

    def add(self, component: str, code: str, object_id: UUID | None = None) -> None:
        self.count += 1
        if len(self.items) < MAX_REPORTED_FAILURES:
            self.items.append(RestoreConsistencyFailure(component, code, object_id))


async def verify_restore_consistency(settings: Settings) -> RestoreConsistencyReport:
    """Verify a restored node without changing its database or blob volume."""

    database = Database.from_settings(settings)
    failures = _FailureCollector()
    node_id: UUID | None = None
    journal_events = 0
    active_keys = 0
    verified_evidence = 0
    verified_mfa = 0
    orphan_blobs = 0
    evidence: list[EvidenceBlob] = []
    factors: list[AuthenticationFactor] = []
    try:
        async with database.session() as session:
            profile = await NodeRepository(session).get_profile(settings.node_code)
            evidence = list(
                (
                    await session.execute(
                        select(EvidenceBlob)
                        .where(EvidenceBlob.status == "READY")
                        .order_by(EvidenceBlob.id)
                    )
                ).scalars()
            )
            factors = list(
                (
                    await session.execute(
                        select(AuthenticationFactor).order_by(AuthenticationFactor.id)
                    )
                ).scalars()
            )
            keys: list[NodeKeyRecord] = []
            if profile is None:
                failures.add("node_signing", "NODE_PROFILE_MISSING")
            else:
                node_id = profile.id
                keys = list(
                    (
                        await session.execute(
                            select(NodeKeyRecord).where(
                                NodeKeyRecord.node_id == profile.id,
                                NodeKeyRecord.purpose == "NODE_SIGNING",
                                NodeKeyRecord.status == "ACTIVE",
                            )
                        )
                    ).scalars()
                )
                journal = await verify_journal(session, profile.id)
                journal_events = journal.checked_events
                for failure in journal.failures:
                    failures.add("journal", failure.code, failure.event_id)

            active_keys = len(keys)
            _verify_node_signing_key(settings, keys, failures)
            verified_evidence, orphan_blobs = _verify_evidence(
                settings, evidence, failures
            )
            verified_mfa = _verify_mfa(settings, factors, failures)
    finally:
        await database.dispose()

    return RestoreConsistencyReport(
        ok=failures.count == 0,
        node_id=node_id,
        journal_events=journal_events,
        active_node_signing_keys=active_keys,
        ready_evidence_records=len(evidence),
        expected_blob_files=len({_canonical_storage_key(item) for item in evidence}),
        verified_evidence_records=verified_evidence,
        mfa_factors=len(factors),
        verified_mfa_factors=verified_mfa,
        orphan_blob_files=orphan_blobs,
        failure_count=failures.count,
        failures=tuple(failures.items),
    )


def restore_consistency_payload(report: RestoreConsistencyReport) -> dict[str, object]:
    return {
        "ok": report.ok,
        "node_id": str(report.node_id) if report.node_id is not None else None,
        "journal_events": report.journal_events,
        "active_node_signing_keys": report.active_node_signing_keys,
        "ready_evidence_records": report.ready_evidence_records,
        "expected_blob_files": report.expected_blob_files,
        "verified_evidence_records": report.verified_evidence_records,
        "mfa_factors": report.mfa_factors,
        "verified_mfa_factors": report.verified_mfa_factors,
        "orphan_blob_files": report.orphan_blob_files,
        "failure_count": report.failure_count,
        "failures": [
            {
                "component": failure.component,
                "code": failure.code,
                "object_id": str(failure.object_id) if failure.object_id is not None else None,
            }
            for failure in report.failures
        ],
    }


def _verify_node_signing_key(
    settings: Settings,
    keys: list[NodeKeyRecord],
    failures: _FailureCollector,
) -> None:
    if len(keys) != 1:
        failures.add("node_signing", "ACTIVE_NODE_SIGNING_KEY_COUNT_INVALID")
        return
    try:
        signer = signer_from_settings(settings)
    except (DomainError, SecretFileError, OSError):
        failures.add("node_signing", "NODE_SIGNING_SECRET_UNAVAILABLE")
        return
    key = keys[0]
    if key.algorithm != "Ed25519":
        failures.add("node_signing", "NODE_SIGNING_ALGORITHM_INVALID", key.id)
    if not hmac.compare_digest(key.fingerprint, signer.fingerprint):
        failures.add("node_signing", "NODE_SIGNING_FINGERPRINT_MISMATCH", key.id)
    if not hmac.compare_digest(key.public_key, signer.public_key_bytes):
        failures.add("node_signing", "NODE_SIGNING_PUBLIC_KEY_MISMATCH", key.id)


def _verify_evidence(
    settings: Settings,
    evidence: list[EvidenceBlob],
    failures: _FailureCollector,
) -> tuple[int, int]:
    expected_keys = {_canonical_storage_key(item) for item in evidence}
    actual_keys = _blob_files(settings.blob_root, failures)
    orphans = actual_keys - expected_keys
    for _ in orphans:
        failures.add("blob_store", "ORPHAN_EVIDENCE_BLOB")
    try:
        store = EncryptedBlobStore(settings.blob_root, settings.blob_encryption_key_file)
    except (DomainError, SecretFileError, OSError):
        failures.add("blob_store", "BLOB_ENCRYPTION_KEY_UNAVAILABLE")
        return 0, len(orphans)

    verified = 0
    for item in evidence:
        canonical = _canonical_storage_key(item)
        if item.storage_key != canonical:
            failures.add("blob_store", "EVIDENCE_STORAGE_KEY_MISMATCH", item.id)
            continue
        if item.encryption_algorithm != "AES-256-GCM-v1":
            failures.add("blob_store", "EVIDENCE_ENCRYPTION_ALGORITHM_INVALID", item.id)
            continue
        path = settings.blob_root / canonical
        try:
            unsafe_type = path.is_symlink()
        except OSError:
            failures.add("blob_store", "EVIDENCE_CONTENT_UNAVAILABLE", item.id)
            continue
        if unsafe_type:
            failures.add("blob_store", "EVIDENCE_BLOB_UNSAFE_TYPE", item.id)
            continue
        try:
            store.read_verified(
                cooperative_id=item.cooperative_id,
                storage_key=canonical,
                expected_sha256=item.expected_sha256,
                expected_size=item.expected_size,
            )
        except DomainError as exc:
            failures.add("blob_store", exc.code, item.id)
            continue
        except OSError:
            failures.add("blob_store", "EVIDENCE_CONTENT_UNAVAILABLE", item.id)
            continue
        verified += 1
    return verified, len(orphans)


def _verify_mfa(
    settings: Settings,
    factors: list[AuthenticationFactor],
    failures: _FailureCollector,
) -> int:
    try:
        cipher = MfaSecretCipher(settings.mfa_encryption_key_file)
    except (DomainError, SecretFileError, OSError):
        failures.add("mfa", "MFA_ENCRYPTION_KEY_UNAVAILABLE")
        return 0
    verified = 0
    for factor in factors:
        if factor.factor_type != "TOTP" or factor.encryption_key_version != "v1":
            failures.add("mfa", "MFA_FACTOR_FORMAT_UNSUPPORTED", factor.id)
            continue
        try:
            secret = cipher.decrypt(factor)
            pyotp.TOTP(secret).at(0)
        except (DomainError, ValueError):
            failures.add("mfa", "MFA_SECRET_UNAVAILABLE", factor.id)
            continue
        verified += 1
    return verified


def _blob_files(root: Path, failures: _FailureCollector) -> set[str]:
    if root.is_symlink() or not root.is_dir():
        failures.add("blob_store", "BLOB_ROOT_UNAVAILABLE")
        return set()
    try:
        return {
            path.relative_to(root).as_posix()
            for path in root.rglob("*.ccb")
            if path.is_file() or path.is_symlink()
        }
    except OSError:
        failures.add("blob_store", "BLOB_ROOT_UNAVAILABLE")
        return set()


def _canonical_storage_key(item: EvidenceBlob) -> str:
    digest = item.expected_sha256.casefold()
    return f"{item.cooperative_id}/{digest[:2]}/{digest}.ccb"
