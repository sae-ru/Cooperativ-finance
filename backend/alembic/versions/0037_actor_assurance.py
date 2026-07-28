"""Preserve the federated-cycle actor scope.

Revision ID: 0037_actor_assurance
Revises: 0036_fulfillment_traceability
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037_actor_assurance"
down_revision: str | None = "0036_fulfillment_traceability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "federated_clearing_cycles",
        sa.Column(
            "created_actor_organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        schema="federation",
    )
    op.create_foreign_key(
        op.f(
            "fk_federated_clearing_cycles_created_actor_organization_id_cooperatives"
        ),
        "federated_clearing_cycles",
        "cooperatives",
        ["created_actor_organization_id"],
        ["id"],
        source_schema="federation",
        referent_schema="identity",
        ondelete="RESTRICT",
    )
    op.execute(
        """
        ALTER TABLE federation.federated_clearing_cycles
          DISABLE TRIGGER trg_federated_clearing_cycles_state;

        UPDATE federation.federated_clearing_cycles AS cycle
        SET created_actor_organization_id = role.cooperative_id
        FROM identity.role_assignments AS role
        WHERE role.id = cycle.created_role_assignment_id
          AND role.cooperative_id IS NOT NULL;

        ALTER TABLE federation.federated_clearing_cycles
          ENABLE TRIGGER trg_federated_clearing_cycles_state;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM federation.federated_clearing_cycles
            WHERE created_actor_organization_id IS NOT NULL
          )
          THEN
            RAISE EXCEPTION
              'Cannot downgrade 0037_actor_assurance with scoped cycle history present';
          END IF;
        END;
        $$;
        """
    )
    op.drop_constraint(
        op.f(
            "fk_federated_clearing_cycles_created_actor_organization_id_cooperatives"
        ),
        "federated_clearing_cycles",
        schema="federation",
        type_="foreignkey",
    )
    op.drop_column(
        "federated_clearing_cycles",
        "created_actor_organization_id",
        schema="federation",
    )
