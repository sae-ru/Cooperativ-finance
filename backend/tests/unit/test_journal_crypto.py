from datetime import UTC, datetime

import pytest

from cooperative_clearing.modules.journal.domain.crypto import (
    NodeSigner,
    canonicalize,
    payload_hash,
    utc_timestamp,
    verify_signature,
)
from cooperative_clearing.shared.domain.errors import DomainError


def test_rfc8032_ed25519_vector_one() -> None:
    signer = NodeSigner.from_seed_hex(
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
    )
    assert signer.public_key_bytes.hex() == (
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    )
    signature = signer.sign(b"")
    assert signature.hex() == (
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert signer.verify(signature, b"") is True
    assert verify_signature(signer.public_key_bytes, signature, b"changed") is False


def test_rfc8785_canonicalization_is_stable() -> None:
    left = {"z": [3, 2, 1], "a": {"value": "Привет", "active": True}}
    right = {"a": {"active": True, "value": "Привет"}, "z": [3, 2, 1]}
    expected = (
        b'{"a":{"active":true,"value":"\xd0\x9f\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82"},'
        b'"z":[3,2,1]}'
    )
    assert canonicalize(left) == expected
    assert canonicalize(right) == expected
    assert payload_hash(left) == payload_hash(right)


def test_canonicalization_rejects_non_json_numbers() -> None:
    with pytest.raises(DomainError) as error:
        canonicalize({"not_json": float("nan")})
    assert error.value.code == "CANONICALIZATION_FAILED"


def test_utc_timestamp_is_fixed_precision() -> None:
    assert utc_timestamp(datetime(2026, 7, 20, 8, 9, 10, 123, tzinfo=UTC)) == (
        "2026-07-20T08:09:10.000123Z"
    )
