from pathlib import Path

import pytest

from cooperative_clearing.shared.core.secrets import (
    SecretFileError,
    read_text_secret,
    validate_node_signing_seed,
)


def test_text_secret_is_trimmed_without_being_logged(tmp_path: Path) -> None:
    secret = tmp_path / "password"
    secret.write_text("a-secure-local-password\n", encoding="utf-8")

    assert read_text_secret(secret) == "a-secure-local-password"


def test_short_secret_is_rejected(tmp_path: Path) -> None:
    secret = tmp_path / "password"
    secret.write_text("short", encoding="utf-8")

    with pytest.raises(SecretFileError, match="content is invalid"):
        read_text_secret(secret)


def test_node_seed_requires_exact_hex_encoding(tmp_path: Path) -> None:
    seed = tmp_path / "node_seed"
    seed.write_text("ab" * 32, encoding="utf-8")
    validate_node_signing_seed(seed)

    seed.write_text("z" * 64, encoding="utf-8")
    with pytest.raises(SecretFileError, match="invalid encoding"):
        validate_node_signing_seed(seed)
