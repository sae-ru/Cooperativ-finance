import pytest

from cooperative_clearing.shared.core.security import (
    PasswordService,
    new_token,
    private_value_hash,
    token_hash,
    tokens_equal,
)
from cooperative_clearing.shared.domain.errors import DomainError


def test_argon2id_password_lifecycle() -> None:
    service = PasswordService()
    encoded = service.hash("a-long-production-password")

    assert encoded.startswith("$argon2id$")
    assert service.verify(encoded, "a-long-production-password")
    assert not service.verify(encoded, "wrong-password")
    assert not service.verify("not-an-argon-hash", "wrong-password")
    service.consume_dummy_verification("unknown-user-password")


def test_password_policy_rejects_short_values() -> None:
    with pytest.raises(DomainError) as failure:
        PasswordService().hash("too-short")
    assert failure.value.code == "PASSWORD_POLICY_VIOLATION"


def test_opaque_token_helpers() -> None:
    first = new_token()
    second = new_token()
    assert first != second
    assert len(token_hash(first)) == 64
    assert private_value_hash("  Example@Email.test ") == private_value_hash("example@email.test")
    assert tokens_equal(first, first)
    assert not tokens_equal(first, second)
