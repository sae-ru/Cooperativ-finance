"""Add the basic exchange participant role.

Revision ID: 0019_exchange_participant
Revises: 0018_inter_node_clearing
Create Date: 2026-07-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_exchange_participant"
down_revision: str | None = "0018_inter_node_clearing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_CONSTRAINT = """
role_code IN (
  'EXCHANGE_PARTICIPANT','MEMBER_REGISTRAR','COOPERATIVE_ADMIN','DATA_STEWARD',
  'WAREHOUSE_CUSTODIAN','INVENTORY_CONTROLLER','LOGISTICS_OPERATOR','RIGHTS_OPERATOR',
  'RISK_ADMIN','CLEARING_OPERATOR','CLEARING_CONTROLLER','CLEARING_FINALIZER',
  'SOLIDARITY_OPERATOR','SOLIDARITY_CONTROLLER','CRISIS_OPERATOR','CRISIS_CONTROLLER',
  'SECURITY_ADMIN','NODE_REGISTRAR','NODE_TECHNICAL_CUSTODIAN','NODE_SECURITY_ADMIN',
  'NODE_BUSINESS_OPERATOR','NODE_AUDITOR','AUDITOR','ARBITRATOR'
)
"""

PREVIOUS_ROLE_CONSTRAINT = ROLE_CONSTRAINT.replace("'EXCHANGE_PARTICIPANT',", "")


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_role_assignments_role_allowed"),
        "role_assignments",
        schema="identity",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_role_assignments_role_allowed"),
        "role_assignments",
        ROLE_CONSTRAINT,
        schema="identity",
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM identity.role_assignments WHERE role_code = 'EXCHANGE_PARTICIPANT'"
    )
    op.drop_constraint(
        op.f("ck_role_assignments_role_allowed"),
        "role_assignments",
        schema="identity",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_role_assignments_role_allowed"),
        "role_assignments",
        PREVIOUS_ROLE_CONSTRAINT,
        schema="identity",
    )