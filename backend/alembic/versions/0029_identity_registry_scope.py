"""Scope identity registrations and administration by cooperative.

Revision ID: 0029_identity_registry_scope
Revises: 0028_antifraud_rule_manifest
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_identity_registry_scope"
down_revision: str | None = "0028_antifraud_rule_manifest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "members",
        sa.Column("registered_by_cooperative_id", sa.UUID(), nullable=True),
        schema="identity",
    )
    op.create_foreign_key(
        "fk_members_registered_by_cooperative_id_cooperatives",
        "members",
        "cooperatives",
        ["registered_by_cooperative_id"],
        ["id"],
        source_schema="identity",
        referent_schema="identity",
        ondelete="RESTRICT",
    )
    op.execute(
        """
        UPDATE identity.members AS member
        SET registered_by_cooperative_id = first_membership.cooperative_id
        FROM (
            SELECT DISTINCT ON (member_id) member_id, cooperative_id
            FROM identity.memberships
            ORDER BY member_id, created_at, id
        ) AS first_membership
        WHERE member.id = first_membership.member_id
          AND member.registered_by_cooperative_id IS NULL
        """
    )
    op.create_index(
        "ix_members_registered_by_cooperative",
        "members",
        ["registered_by_cooperative_id"],
        schema="identity",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_members_registered_by_cooperative",
        table_name="members",
        schema="identity",
    )
    op.drop_constraint(
        "fk_members_registered_by_cooperative_id_cooperatives",
        "members",
        schema="identity",
        type_="foreignkey",
    )
    op.drop_column("members", "registered_by_cooperative_id", schema="identity")