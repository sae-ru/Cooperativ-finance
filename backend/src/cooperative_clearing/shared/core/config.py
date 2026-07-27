"""Strict runtime settings sourced from non-secret environment values."""

from enum import StrEnum
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEV = "dev"
    TEST = "test"
    STAGING = "staging-node"
    PILOT = "pilot"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COOP_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Environment = Environment.DEV
    release: str = "0.1.0-dev"
    service_name: str = "api"
    log_level: str = "INFO"
    node_code: str = "node-local-01"
    node_display_name: str = "Local cooperative node"
    demo_data_enabled: bool = False

    bootstrap_registrar_password_file: Path = Path("/run/secrets/bootstrap_registrar_password")
    bootstrap_security_password_file: Path = Path("/run/secrets/bootstrap_security_password")
    bootstrap_auditor_password_file: Path = Path("/run/secrets/bootstrap_auditor_password")
    access_token_minutes: int = Field(default=15, ge=5, le=60)
    refresh_session_hours: int = Field(default=12, ge=1, le=168)
    auth_max_failed_attempts: int = Field(default=5, ge=3, le=20)
    auth_lock_seconds: int = Field(default=900, ge=60, le=86400)
    auth_cookie_secure: bool | None = None
    step_up_ttl_minutes: int = Field(default=10, ge=2, le=30)
    totp_enrollment_minutes: int = Field(default=15, ge=5, le=60)
    account_recovery_minutes: int = Field(default=30, ge=10, le=120)
    break_glass_max_minutes: int = Field(default=60, ge=15, le=240)
    mfa_encryption_key_file: Path = Path("/run/secrets/mfa_encryption_key")

    database_host: str = "postgres"
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = "cooperative_clearing"
    database_user: str = "coop_app"
    database_password_file: Path = Path("/run/secrets/postgres_app_password")
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=30)

    blob_root: Path = Path("/var/lib/cooperative-clearing/blobs")
    operations_state_root: Path = Path("/var/lib/cooperative-clearing/operations")
    blob_encryption_key_file: Path = Path("/run/secrets/blob_encryption_key")
    node_signing_seed_file: Path = Path("/run/secrets/node_signing_seed")
    readiness_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    worker_heartbeat_seconds: float = Field(default=5.0, ge=1, le=60)
    worker_stale_after_seconds: float = Field(default=20.0, ge=5, le=600)
    host_probe_stale_seconds: int = Field(default=180, ge=30, le=3600)
    disk_warning_percent: int = Field(default=15, ge=2, le=80)
    disk_critical_percent: int = Field(default=5, ge=1, le=50)
    clock_drift_warning_seconds: int = Field(default=5, ge=1, le=300)
    clock_drift_critical_seconds: int = Field(default=60, ge=2, le=3600)
    backup_warning_hours: int = Field(default=36, ge=1, le=720)
    backup_critical_hours: int = Field(default=72, ge=2, le=2160)
    certificate_warning_days: int = Field(default=30, ge=2, le=365)
    certificate_critical_days: int = Field(default=7, ge=1, le=90)
    outbox_poll_seconds: float = Field(default=1.0, ge=0.2, le=60)
    outbox_batch_size: int = Field(default=50, ge=1, le=500)
    outbox_lease_seconds: int = Field(default=30, ge=5, le=600)
    outbox_max_attempts: int = Field(default=5, ge=1, le=20)
    sync_package_max_bytes: int = Field(default=52_428_800, ge=1_048_576, le=1_073_741_824)
    sync_package_max_files: int = Field(default=2048, ge=5, le=100_000)
    sync_package_max_events: int = Field(default=10_000, ge=1, le=1_000_000)
    sync_package_max_compression_ratio: int = Field(default=100, ge=2, le=1000)
    peer_connect_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30)
    peer_response_max_bytes: int = Field(default=2_097_152, ge=65_536, le=52_428_800)
    peer_max_fanout: int = Field(default=30, ge=1, le=100)
    federated_recovery_retry_seconds: int = Field(default=60, ge=10, le=3600)
    federated_recovery_batch_size: int = Field(default=1, ge=1, le=10)
    peer_tls_ca_file: Path | None = None
    peer_tls_client_cert_file: Path | None = None
    peer_tls_client_key_file: Path | None = None
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver", "nginx"]

    @field_validator("release", "service_name", "node_display_name")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("node_code")
    @classmethod
    def valid_node_code(cls, value: str) -> str:
        from cooperative_clearing.modules.node.domain.node_code import NodeCode

        return str(NodeCode(value))

    @field_validator("log_level")
    @classmethod
    def valid_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unsupported log level")
        return value

    @model_validator(mode="after")
    def validate_environment_policy(self) -> "Settings":
        hardened = {Environment.STAGING, Environment.PILOT, Environment.PRODUCTION}
        if (self.peer_tls_client_cert_file is None) != (self.peer_tls_client_key_file is None):
            raise ValueError("peer TLS client certificate and key must be configured together")
        if self.environment in hardened:
            if self.demo_data_enabled:
                raise ValueError("demo data is forbidden in hardened environments")
            for path in (
                self.database_password_file,
                self.blob_root,
                self.operations_state_root,
                self.blob_encryption_key_file,
                self.mfa_encryption_key_file,
                self.node_signing_seed_file,
                self.bootstrap_registrar_password_file,
                self.bootstrap_security_password_file,
                self.bootstrap_auditor_password_file,
                *(
                    path
                    for path in (
                        self.peer_tls_ca_file,
                        self.peer_tls_client_cert_file,
                        self.peer_tls_client_key_file,
                    )
                    if path is not None
                ),
            ):
                if not path.is_absolute():
                    raise ValueError("runtime paths must be absolute")
        if self.disk_critical_percent >= self.disk_warning_percent:
            raise ValueError("critical disk threshold must be below warning threshold")
        if self.clock_drift_warning_seconds >= self.clock_drift_critical_seconds:
            raise ValueError("clock warning threshold must be below critical threshold")
        if self.backup_warning_hours >= self.backup_critical_hours:
            raise ValueError("backup warning threshold must be below critical threshold")
        if self.certificate_critical_days >= self.certificate_warning_days:
            raise ValueError("critical certificate threshold must be below warning threshold")
        return self

    @property
    def secure_auth_cookies(self) -> bool:
        if self.auth_cookie_secure is not None:
            return self.auth_cookie_secure
        return self.environment in {
            Environment.STAGING,
            Environment.PILOT,
            Environment.PRODUCTION,
        }
