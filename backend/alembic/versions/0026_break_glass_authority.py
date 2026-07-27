"""Preserve break-glass authority provenance for signed events.

Revision ID: 0026_break_glass_authority
Revises: 0025_identity_step_up
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_break_glass_authority"
down_revision: str | None = "0025_identity_step_up"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "role_assignments",
        sa.Column(
            "source",
            sa.String(length=16),
            server_default=sa.text("'ASSIGNMENT'"),
            nullable=False,
        ),
        schema="identity",
    )
    op.add_column(
        "role_assignments",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="identity",
    )
    op.create_check_constraint(
        "source_allowed",
        "role_assignments",
        "source IN ('ASSIGNMENT','BREAK_GLASS')",
        schema="identity",
    )
    op.execute(
        sa.text(
            "UPDATE identity.role_assignments AS authority "
            "SET source = 'BREAK_GLASS', expires_at = bg.expires_at "
            "FROM identity.break_glass_grants AS bg "
            "WHERE authority.id = bg.id"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE identity.role_assignments "
            "SET status = 'REVOKED', revoked_at = COALESCE(revoked_at, now()) "
            "WHERE source = 'BREAK_GLASS'"
        )
    )
    op.drop_constraint("source_allowed", "role_assignments", schema="identity", type_="check")
    op.drop_column("role_assignments", "expires_at", schema="identity")
    op.drop_column("role_assignments", "source", schema="identity")
