-- =============================================================================
-- OpsPilot — complete database schema for Supabase PostgreSQL
-- =============================================================================
--
-- Equivalent to `alembic upgrade head` (migrations 0001 + 0002 + 0003). Generated from
-- `alembic upgrade head --sql` and annotated. The Alembic migrations in
-- backend/alembic/versions/ remain the source of truth; regenerate this file
-- whenever a migration is added:
--
--     cd backend
--     DATABASE_URL=postgresql+asyncpg://x:x@offline.invalid/postgres \
--         alembic upgrade head --sql 2>/dev/null
--
-- Use EITHER this script OR `alembic upgrade head` on a database, not both.
-- The script records revision 0003 in `alembic_version`, so Alembic treats the
-- database as fully migrated and future migrations apply normally.
--
-- Safety:
--   * Runs in one transaction. If any object already exists (for example the
--     schema was already created by Alembic), the script stops with an error
--     and rolls back completely: nothing is changed, nothing is dropped.
--   * No DROP / TRUNCATE / DELETE statements. No seed or demo data.
--
-- Design notes (matching backend/app/models):
--   * Enum-like columns (status, severity, level, ...) are VARCHAR(32), not
--     native PostgreSQL ENUM types, so adding a value never needs a type
--     migration. Allowed values are enforced by the backend (SQLAlchemy/Pydantic)
--     and listed in the comments below.
--   * Incidents use an integer id; the "INC-001" label is derived in the backend
--     (`Incident.reference`), not stored.
--   * logs, deployments and service_health describe the simulated environment
--     per service and have no incident_id; agents query them by service_name and
--     time window. agent_runs, agent_events, approvals and incident_reports belong
--     to one incident and are deleted with it (ON DELETE CASCADE).
--   * Row Level Security is enabled with NO policies: Supabase's public Data API
--     (anon/authenticated roles) gets no access, while the FastAPI backend, which
--     connects as the table owner (postgres), bypasses RLS.
-- =============================================================================

BEGIN;

-- Alembic's revision bookkeeping table.
CREATE TABLE public.alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- -----------------------------------------------------------------------------
-- Simulated environment telemetry (per service, not per incident)
-- -----------------------------------------------------------------------------

-- Deployment history, e.g. payment-api v1.8.0, v1.8.1, v1.8.2.
-- status: IN_PROGRESS | SUCCEEDED | FAILED | ROLLED_BACK
CREATE TABLE public.deployments (
    id SERIAL NOT NULL,
    service_name VARCHAR(64) NOT NULL,
    version VARCHAR(32) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(32) NOT NULL,
    commit_sha VARCHAR(64),
    CONSTRAINT pk_deployments PRIMARY KEY (id)
);

CREATE INDEX ix_deployments_service_name_timestamp ON public.deployments (service_name, timestamp);

-- Simulated application logs.
-- level: DEBUG | INFO | WARN | ERROR
CREATE TABLE public.logs (
    id SERIAL NOT NULL,
    service_name VARCHAR(64) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    level VARCHAR(32) NOT NULL,
    message TEXT NOT NULL,
    metadata JSONB NOT NULL,
    CONSTRAINT pk_logs PRIMARY KEY (id)
);

CREATE INDEX ix_logs_service_name_timestamp ON public.logs (service_name, timestamp);

-- Health snapshots (error_rate in %, latency in ms, cpu/memory in %).
-- status: HEALTHY | DEGRADED | DOWN
CREATE TABLE public.service_health (
    id SERIAL NOT NULL,
    service_name VARCHAR(64) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(32) NOT NULL,
    error_rate FLOAT NOT NULL,
    latency_ms FLOAT NOT NULL,
    cpu_usage FLOAT NOT NULL,
    memory_usage FLOAT NOT NULL,
    CONSTRAINT pk_service_health PRIMARY KEY (id)
);

CREATE INDEX ix_service_health_service_name_timestamp ON public.service_health (service_name, timestamp);

-- -----------------------------------------------------------------------------
-- Incidents and incident-scoped records
-- -----------------------------------------------------------------------------

-- severity: LOW | MEDIUM | HIGH | CRITICAL
-- status:   DETECTED | INVESTIGATING | ANALYZING | AWAITING_APPROVAL | REMEDIATING
--           | VERIFYING | RESOLVED | FAILED | ESCALATED   (backend default: DETECTED)
-- updated_at is refreshed by the backend (SQLAlchemy onupdate), not a trigger.
CREATE TABLE public.incidents (
    id SERIAL NOT NULL,
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    severity VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    service_name VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_incidents PRIMARY KEY (id)
);

CREATE INDEX ix_incidents_service_name ON public.incidents (service_name);

CREATE INDEX ix_incidents_status ON public.incidents (status);

-- Live timeline events (incident_created, agent_started, tool_completed, ...).
-- agent_name: orchestrator | investigation | root_cause | remediation | verification
--             (NULL for system events such as incident_created)
CREATE TABLE public.agent_events (
    id SERIAL NOT NULL,
    incident_id INTEGER NOT NULL,
    agent_name VARCHAR(32),
    event_type VARCHAR(64) NOT NULL,
    message TEXT NOT NULL,
    metadata JSONB NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_agent_events PRIMARY KEY (id),
    CONSTRAINT fk_agent_events_incident_id_incidents FOREIGN KEY (incident_id)
        REFERENCES public.incidents (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_events_incident_id_timestamp ON public.agent_events (incident_id, timestamp);

-- One execution of a logical agent. `summary` holds a concise, user-safe
-- reasoning summary (never chain-of-thought); `output` the validated JSON result.
-- agent_name: orchestrator | investigation | root_cause | remediation | verification
-- status:     RUNNING | COMPLETED | FAILED   (backend default: RUNNING)
CREATE TABLE public.agent_runs (
    id SERIAL NOT NULL,
    incident_id INTEGER NOT NULL,
    agent_name VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    summary TEXT,
    output JSONB,
    CONSTRAINT pk_agent_runs PRIMARY KEY (id),
    CONSTRAINT fk_agent_runs_incident_id_incidents FOREIGN KEY (incident_id)
        REFERENCES public.incidents (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_runs_incident_id ON public.agent_runs (incident_id);

-- Human approval gate for risky actions. decided_at is set for approvals AND rejections.
-- action_type: ROLLBACK_DEPLOYMENT | RESTART_SERVICE | NO_ACTION | ESCALATE_TO_HUMAN
-- risk:        LOW | MEDIUM | HIGH
-- status:      PENDING | APPROVED | REJECTED | EXPIRED   (backend default: PENDING)
CREATE TABLE public.approvals (
    id SERIAL NOT NULL,
    incident_id INTEGER NOT NULL,
    action_type VARCHAR(32) NOT NULL,
    target VARCHAR(64) NOT NULL,
    risk VARCHAR(32) NOT NULL,
    reason TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    requested_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    decided_at TIMESTAMP WITH TIME ZONE,
    -- Migration 0003: backend-validated action parameters (e.g. rollback from/to version).
    parameters JSONB DEFAULT '{}' NOT NULL,
    CONSTRAINT pk_approvals PRIMARY KEY (id),
    CONSTRAINT fk_approvals_incident_id_incidents FOREIGN KEY (incident_id)
        REFERENCES public.incidents (id) ON DELETE CASCADE
);

CREATE INDEX ix_approvals_incident_id ON public.approvals (incident_id);

-- Final report, at most one per incident (the UNIQUE constraint also indexes incident_id).
-- recovery_status: RECOVERED | NOT_RECOVERED | NOT_ATTEMPTED   (backend default: NOT_ATTEMPTED)
CREATE TABLE public.incident_reports (
    id SERIAL NOT NULL,
    incident_id INTEGER NOT NULL,
    root_cause TEXT,
    confidence FLOAT,
    evidence JSONB NOT NULL,
    action_taken TEXT,
    recovery_status VARCHAR(32) NOT NULL,
    report JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT pk_incident_reports PRIMARY KEY (id),
    CONSTRAINT fk_incident_reports_incident_id_incidents FOREIGN KEY (incident_id)
        REFERENCES public.incidents (id) ON DELETE CASCADE,
    CONSTRAINT uq_incident_reports_incident_id UNIQUE (incident_id)
);

-- -----------------------------------------------------------------------------
-- Migration 0002: lock out Supabase's Data API (RLS on, no policies)
-- -----------------------------------------------------------------------------

ALTER TABLE public.incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.deployments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.service_health ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.incident_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.alembic_version ENABLE ROW LEVEL SECURITY;

-- Tell Alembic the schema is at the latest migration.
INSERT INTO public.alembic_version (version_num) VALUES ('0003');

COMMIT;

-- -----------------------------------------------------------------------------
-- Verification (run separately after the script succeeds)
-- -----------------------------------------------------------------------------
-- SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
--   -> 9 rows (8 OpsPilot tables + alembic_version), rowsecurity = true for all
-- SELECT version_num FROM public.alembic_version;
--   -> 0003
