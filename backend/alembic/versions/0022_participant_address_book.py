"""Add the private participant address book.

Revision ID: 0022_participant_addresses
Revises: 0021_logistics_contacts
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_participant_addresses"
down_revision: str | None = "0021_logistics_contacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "participant_addresses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cooperative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("region_code", sa.String(length=63), nullable=False),
        sa.Column("address_text", sa.String(length=500), nullable=False),
        sa.Column("contact_name", sa.String(length=200), nullable=False),
        sa.Column("contact_phone", sa.String(length=80), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("is_default_pickup", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_default_delivery", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("purpose IN ('PICKUP','DELIVERY','BOTH')", name="purpose_allowed"),
        sa.CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="status_allowed"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.ForeignKeyConstraint(["cooperative_id"], ["identity.cooperatives.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["member_id"], ["identity.members.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        schema="identity",
    )
    op.create_index(
        "ix_participant_addresses_member_status",
        "participant_addresses",
        ["member_id", "status"],
        schema="identity",
    )
    op.create_index(
        "uq_participant_addresses_active_label",
        "participant_addresses",
        ["member_id", "cooperative_id", sa.text("lower(label)")],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
        schema="identity",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_participant_addresses_active_label",
        table_name="participant_addresses",
        schema="identity",
    )
    op.drop_index(
        "ix_participant_addresses_member_status",
        table_name="participant_addresses",
        schema="identity",
    )
    op.drop_table("participant_addresses", schema="identity")
