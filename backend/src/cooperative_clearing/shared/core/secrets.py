"""Minimal secret-file readers that never expose values in errors or logs."""

import re
from pathlib import Path

_HEX_32_BYTES = re.compile(r"^[0-9a-fA-F]{64}$")


class SecretFileError(RuntimeError):
    pass


def read_text_secret(path: Path, *, minimum_length: int = 16) -> str:
    if path.is_symlink() or not path.is_file():
        raise SecretFileError("secret file is missing or has an unsafe type")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SecretFileError("secret file cannot be read") from exc
    if len(value) < minimum_length:
        raise SecretFileError("secret file content is invalid")
    return value


def validate_node_signing_seed(path: Path) -> None:
    value = read_text_secret(path, minimum_length=64)
    if _HEX_32_BYTES.fullmatch(value) is None:
        raise SecretFileError("node signing seed has an invalid encoding")
