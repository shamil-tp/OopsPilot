"""monitored projects

Dynamically registered real applications (one row per project). Row level security
is enabled like every other table (see 0002).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-01 08:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'monitored_projects',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('service', sa.String(length=64), nullable=False),
        sa.Column('url', sa.String(length=300), nullable=True),
        sa.Column('repository', sa.String(length=140), nullable=False),
        sa.Column('environment', sa.String(length=32), nullable=False, server_default='production'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_monitored_projects')),
        sa.UniqueConstraint('service', name=op.f('uq_monitored_projects_service')),
    )
    with op.batch_alter_table('monitored_projects', schema=None) as batch_op:
        batch_op.create_index('ix_monitored_projects_service', ['service'], unique=True)
        batch_op.create_index('ix_monitored_projects_repository', ['repository'], unique=False)

    if op.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE monitored_projects ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    with op.batch_alter_table('monitored_projects', schema=None) as batch_op:
        batch_op.drop_index('ix_monitored_projects_repository')
        batch_op.drop_index('ix_monitored_projects_service')

    op.drop_table('monitored_projects')
