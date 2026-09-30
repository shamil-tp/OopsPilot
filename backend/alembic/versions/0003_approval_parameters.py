"""approval parameters

Stores the backend-validated action parameters (e.g. rollback from/to version) on the approval
record itself, so what a human approves is exactly what will later be executed.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-30 18:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "parameters",
                sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                server_default=sa.text("'{}'"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_column("parameters")
