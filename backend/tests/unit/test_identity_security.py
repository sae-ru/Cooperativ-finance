from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pyotp
import pytest

from cooperative_clearing.modules.identity.application.security import (
    IdentitySecurityService,
    MfaSecretCipher,
)
from cooperative_clearing.modules.identity.infrastructure.models import AuthenticationFactor
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.domain.errors import DomainError


def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "mfa.key"
    path.write_text("42" * 32, encoding="ascii")
    return path


def factor_with_secret(path: Path, secret: str) -> tuple[MfaSecretCipher, AuthenticationFactor]:
    cipher = MfaSecretCipher(path)
    factor_id = uuid4()
    user_id = uuid4()
    nonce, ciphertext = cipher.encrypt(factor_id=factor_id, user_id=user_id, secret=secret)
    factor = AuthenticationFactor(
        id=factor_id,
        user_id=user_id,
        factor_type="TOTP",
        status="ACTIVE",
        secret_nonce=nonce,
        secret_ciphertext=ciphertext,
        encryption_key_version="v1",
    )
    return cipher, factor


def test_mfa_secret_is_authenticated_and_bound_to_factor(tmp_path: Path) -> None:
    secret = pyotp.random_base32()
    cipher, factor = factor_with_secret(key_file(tmp_path), secret)

    assert cipher.decrypt(factor) == secret

    factor.secret_ciphertext = factor.secret_ciphertext[:-1] + bytes(
        [factor.secret_ciphertext[-1] ^ 1]
    )
    with pytest.raises(DomainError) as error:
        cipher.decrypt(factor)
    assert error.value.code == "MFA_SECRET_UNAVAILABLE"


def test_totp_matching_uses_rfc_counter_window(tmp_path: Path) -> None:
    secret = "JBSWY3DPEHPK3PXP"
    path = key_file(tmp_path)
    cipher, factor = factor_with_secret(path, secret)
    service = IdentitySecurityService(
        Settings(mfa_encryption_key_file=path),
        cipher=cipher,
    )
    moment = datetime(2026, 7, 27, 12, 0, 0, tzinfo=UTC)
    totp = pyotp.TOTP(secret)
    counter = totp.timecode(moment)

    assert service._matching_counter(factor, totp.generate_otp(counter), moment) == counter
    assert service._matching_counter(factor, "12345", moment) is None
    assert service._matching_counter(factor, "abcdef", moment) is None
