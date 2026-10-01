"""health optional resources

A real application monitored through HTTP health checks has no CPU/memory figures, and OpsPilot
never invents metrics, so `service_health.cpu_usage` and `memory_usage` become nullable. Additive
and non-destructive: existing rows are unchanged. Downgrading fills NULLs with 0 first (the old
schema cannot represent "not measured").

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("service_health", schema=None) as batch_op:
        batch_op.alter_column("cpu_usage", existing_type=sa.Float(), nullable=True)
        batch_op.alter_column("memory_usage", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE service_health SET cpu_usage = 0 WHERE cpu_usage IS NULL")
    op.execute("UPDATE service_health SET memory_usage = 0 WHERE memory_usage IS NULL")
    with op.batch_alter_table("service_health", schema=None) as batch_op:
        batch_op.alter_column("cpu_usage", existing_type=sa.Float(), nullable=False)
        batch_op.alter_column("memory_usage", existing_type=sa.Float(), nullable=False)
