"""enable row level security

Supabase exposes the `public` schema through its auto-generated Data API (PostgREST), so any
table without RLS can be read and written by anyone holding the project's anon key. OpsPilot
never uses that API: only the FastAPI backend touches the database, connecting as the table
owner, which bypasses RLS. Enabling RLS with no policies therefore locks the Data API out
without affecting the backend. On plain PostgreSQL (CI) this is harmless.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-30 17:30:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "incidents",
    "logs",
    "deployments",
    "service_health",
    "agent_runs",
    "agent_events",
    "approvals",
    "incident_reports",
    "alembic_version",
)


def upgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
