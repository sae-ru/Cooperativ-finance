"""Align the authentication-factor index name with SQLAlchemy metadata.

Revision ID: 0027_identity_index_alignment
Revises: 0026_break_glass_authority
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_identity_index_alignment"
down_revision: str | None = "0026_break_glass_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER INDEX identity.ix_identity_authentication_factors_user_id "
            "RENAME TO ix_authentication_factors_user_id"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "ALTER INDEX identity.ix_authentication_factors_user_id "
            "RENAME TO ix_identity_authentication_factors_user_id"
        )
    )