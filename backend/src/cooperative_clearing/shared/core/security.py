"""Password and opaque-token primitives with no secret persistence."""

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type

from cooperative_clearing.shared.domain.errors import DomainError

PASSWORD_MIN_LENGTH = 16


class PasswordService:
    def __init__(self) -> None:
        self._hasher = PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=2,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        self._dummy_hash = self._hasher.hash("not-a-real-password-value")

    def validate(self, password: str) -> None:
        if len(password) < PASSWORD_MIN_LENGTH:
            raise DomainError(
                code="PASSWORD_POLICY_VIOLATION",
                message_key="errors.auth.password_policy_violation",
                parameters={"min_length": PASSWORD_MIN_LENGTH},
                status_code=422,
            )

    def hash(self, password: str) -> str:
        self.validate(password)
        return self._hasher.hash(password)

    def verify(self, encoded: str, password: str) -> bool:
        try:
            return self._hasher.verify(encoded, password)
        except (VerifyMismatchError, InvalidHashError):
            return False

    def consume_dummy_verification(self, password: str) -> None:
        self.verify(self._dummy_hash, password)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def private_value_hash(value: str) -> str:
    return token_hash(value.strip().casefold())


def tokens_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
