"""Deterministic signed sync archives with bounded, path-safe decoding."""

import base64
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from cooperative_clearing.modules.federation.domain.types import federation_error
from cooperative_clearing.modules.journal.domain.crypto import (
    NodeSigner,
    canonicalize,
    sha256_ref,
)

REQUIRED_PACKAGE_FILES = frozenset(
    {
        "manifest.json",
        "events.ndjson",
        "certificates/node-key.json",
        "revocations/revocations.json",
        "package.sig",
    }
)


@dataclass(frozen=True, slots=True)
class DecodedPackage:
    manifest: dict[str, object]
    events_bytes: bytes
    certificate: dict[str, object]
    revocations: dict[str, object]
    signature: bytes
    files: dict[str, bytes]


def build_package_archive(
    *,
    manifest_base: dict[str, object],
    events: list[dict[str, object]],
    certificate: dict[str, object],
    revocations: dict[str, object],
    signer: NodeSigner,
) -> tuple[bytes, dict[str, object]]:
    event_lines = [canonicalize(item) for item in events]
    events_bytes = b"\n".join(event_lines) + b"\n"
    certificate_bytes = canonicalize(certificate)
    revocation_bytes = canonicalize(revocations)
    file_hashes = {
        "events.ndjson": sha256_ref(events_bytes),
        "certificates/node-key.json": sha256_ref(certificate_bytes),
        "revocations/revocations.json": sha256_ref(revocation_bytes),
    }
    manifest = {
        **manifest_base,
        "compression": "ZIP-DEFLATE",
        "file_hashes": file_hashes,
    }
    manifest_bytes = canonicalize(manifest)
    signature = signer.sign(manifest_bytes)
    entries = {
        "manifest.json": manifest_bytes,
        "events.ndjson": events_bytes,
        "certificates/node-key.json": certificate_bytes,
        "revocations/revocations.json": revocation_bytes,
        "package.sig": base64.b64encode(signature),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100600 << 16
            archive.writestr(info, entries[name])
    return buffer.getvalue(), manifest


def decode_package_archive(
    payload: bytes,
    *,
    maximum_bytes: int,
    maximum_files: int,
    maximum_ratio: int,
) -> DecodedPackage:
    if not payload or len(payload) > maximum_bytes:
        raise federation_error("SYNC_ARCHIVE_SIZE_INVALID", 422)
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload), mode="r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise federation_error("SYNC_ARCHIVE_INVALID", 422) from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > maximum_files:
            raise federation_error("SYNC_ARCHIVE_FILE_LIMIT", 422)
        names: set[str] = set()
        total_uncompressed = 0
        for info in infos:
            path = PurePosixPath(info.filename)
            if (
                info.is_dir()
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in info.filename
                or info.filename in names
                or info.file_size < 0
                or info.compress_size < 0
            ):
                raise federation_error("SYNC_ARCHIVE_PATH_INVALID", 422)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise federation_error("SYNC_ARCHIVE_LINK_FORBIDDEN", 422)
            if info.compress_size == 0 and info.file_size > 0:
                raise federation_error("SYNC_ARCHIVE_RATIO_INVALID", 422)
            if info.compress_size > 0 and info.file_size > info.compress_size * maximum_ratio:
                raise federation_error("SYNC_ARCHIVE_RATIO_INVALID", 422)
            total_uncompressed += info.file_size
            if total_uncompressed > maximum_bytes:
                raise federation_error("SYNC_ARCHIVE_EXPANDED_SIZE_INVALID", 422)
            names.add(info.filename)
        if not REQUIRED_PACKAGE_FILES.issubset(names):
            raise federation_error("SYNC_ARCHIVE_FILES_MISSING", 422)
        forbidden = {
            name
            for name in names
            if name not in REQUIRED_PACKAGE_FILES
            and not name.startswith("blobs/")
            and not name.startswith("proofs/")
        }
        if forbidden:
            raise federation_error("SYNC_ARCHIVE_FILE_FORBIDDEN", 422)
        try:
            files = {name: archive.read(name) for name in names}
        except (zipfile.BadZipFile, KeyError, RuntimeError) as exc:
            raise federation_error("SYNC_ARCHIVE_INVALID", 422) from exc
    try:
        manifest = json.loads(files["manifest.json"])
        certificate = json.loads(files["certificates/node-key.json"])
        revocations = json.loads(files["revocations/revocations.json"])
        signature = base64.b64decode(files["package.sig"], validate=True)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise federation_error("SYNC_ARCHIVE_CONTENT_INVALID", 422) from exc
    if (
        not isinstance(manifest, dict)
        or not isinstance(certificate, dict)
        or not isinstance(revocations, dict)
    ):
        raise federation_error("SYNC_ARCHIVE_CONTENT_INVALID", 422)
    if canonicalize(manifest) != files["manifest.json"]:
        raise federation_error("SYNC_MANIFEST_NOT_CANONICAL", 422)
    return DecodedPackage(
        manifest=manifest,
        events_bytes=files["events.ndjson"],
        certificate=certificate,
        revocations=revocations,
        signature=signature,
        files=files,
    )


def parse_event_lines(events_bytes: bytes, maximum_events: int) -> list[dict[str, object]]:
    lines = events_bytes.splitlines()
    if not lines or len(lines) > maximum_events:
        raise federation_error("SYNC_EVENT_COUNT_INVALID", 422)
    result: list[dict[str, object]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise federation_error("SYNC_EVENT_LINE_INVALID", 422) from exc
        if not isinstance(item, dict) or canonicalize(item) != line:
            raise federation_error("SYNC_EVENT_NOT_CANONICAL", 422)
        result.append(item)
    return result
