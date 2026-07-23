from pathlib import Path

import pytest
from pydantic import ValidationError

from cooperative_clearing.shared.core.config import Environment, Settings


def test_demo_data_is_rejected_in_production(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="demo data is forbidden"):
        Settings(
            environment=Environment.PRODUCTION,
            demo_data_enabled=True,
            database_password_file=tmp_path / "password",
            node_signing_seed_file=tmp_path / "seed",
            blob_root=tmp_path / "blobs",
        )


def test_hardened_environment_requires_absolute_paths() -> None:
    with pytest.raises(ValidationError, match="runtime paths must be absolute"):
        Settings(
            environment=Environment.PILOT,
            demo_data_enabled=False,
            database_password_file=Path("secrets/password"),
        )


def test_settings_normalize_node_code_and_log_level() -> None:
    settings = Settings(node_code=" NODE-TEST-01 ", log_level="warning")

    assert settings.node_code == "node-test-01"
    assert settings.log_level == "WARNING"
