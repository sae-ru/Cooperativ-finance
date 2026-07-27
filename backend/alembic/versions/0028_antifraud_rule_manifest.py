"""Bind anti-fraud scans to an exact rule and calibration manifest.

Revision ID: 0028_antifraud_rule_manifest
Revises: 0027_identity_index_alignment
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_antifraud_rule_manifest"
down_revision: str | None = "0027_identity_index_alignment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_MANIFEST_HASH = f"sha256:{'0' * 64}"


def upgrade() -> None:
    op.add_column(
        "antifraud_scans",
        sa.Column(
            "rule_manifest_hash",
            sa.String(length=71),
            nullable=False,
            server_default=LEGACY_MANIFEST_HASH,
        ),
        schema="risk",
    )
    op.add_column(
        "antifraud_scans",
        sa.Column(
            "calibration_dataset_version",
            sa.String(length=40),
            nullable=False,
            server_default="legacy-none",
        ),
        schema="risk",
    )
    op.create_check_constraint(
        "rule_manifest_hash_sha256",
        "antifraud_scans",
        "rule_manifest_hash ~ '^sha256:[0-9a-f]{64}$'",
        schema="risk",
    )
    op.alter_column(
        "antifraud_scans",
        "rule_manifest_hash",
        server_default=None,
        schema="risk",
    )
    op.alter_column(
        "antifraud_scans",
        "calibration_dataset_version",
        server_default=None,
        schema="risk",
    )


def downgrade() -> None:
    op.drop_constraint(
        "rule_manifest_hash_sha256",
        "antifraud_scans",
        schema="risk",
        type_="check",
    )
    op.drop_column(
        "antifraud_scans",
        "calibration_dataset_version",
        schema="risk",
    )
    op.drop_column("antifraud_scans", "rule_manifest_hash", schema="risk")
