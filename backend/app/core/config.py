"""Application settings loaded from environment variables (and an optional .env file)."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
# Later files win: the repo-root .env (shared with Docker Compose) overrides backend/.env.
ENV_FILES = (BACKEND_DIR / ".env", REPO_ROOT / ".env")

_SUPABASE_HOST_SUFFIXES = (".supabase.co", ".supabase.com")


def normalize_database_url(value: str) -> str:
    """Adapt a PostgreSQL URL (e.g. copied from the Supabase dashboard) for SQLAlchemy + asyncpg.

    - `postgres://` / `postgresql://` -> `postgresql+asyncpg://`
    - libpq's `sslmode=` -> asyncpg's `ssl=` (asyncpg rejects `sslmode`)
    - Supabase hosts get `ssl=require` unless SSL is configured explicitly
    """
    for prefix in ("postgres://", "postgresql://"):
        if value.startswith(prefix):
            value = "postgresql+asyncpg://" + value[len(prefix) :]
            break
    if not value.startswith("postgresql+asyncpg://"):
        return value

    url = make_url(value)
    query = dict(url.query)
    if "sslmode" in query:
        query.setdefault("ssl", query.pop("sslmode"))
    if "ssl" not in query and (url.host or "").endswith(_SUPABASE_HOST_SUFFIXES):
        query["ssl"] = "require"
    return url.set(query=query).render_as_string(hide_password=False)


def redact_database_url(value: str) -> str:
    """URL safe to log or print: the password is masked."""
    return make_url(value).render_as_string(hide_password=True)


_SERVICE_ID = r"^[a-z0-9][a-z0-9-]{1,62}$"
_URL = r"^https?://[^\s]+$"
_REPOSITORY = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"


class MonitoredProject(BaseModel):
    """One real application OpsPilot watches (an entry of MONITORED_PROJECTS)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    service: str = Field(pattern=_SERVICE_ID, description="Service id inside OpsPilot")
    url: str | None = Field(default=None, pattern=_URL, description="Health-checked URL")
    repository: str | None = Field(default=None, pattern=_REPOSITORY, description="owner/repo")
    environment: str = Field(default="production", max_length=32)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "OpsPilot"
    app_version: str = "0.1.0"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # Supabase PostgreSQL (session pooler or direct connection). Required: there is deliberately
    # no default, so a missing value fails at startup instead of silently using another database.
    database_url: str
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    # Gemini is the only provider for now. All agents share these keys through one key pool.
    # Validated lazily by the AI provider factory (app.ai.factory), not at startup, so the
    # non-AI APIs keep working when AI is misconfigured.
    ai_provider: str = "gemini"
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_api_key_1: SecretStr | None = None
    gemini_api_key_2: SecretStr | None = None
    gemini_api_key_3: SecretStr | None = None
    gemini_api_key_4: SecretStr | None = None
    # How long a rate-limited key is skipped (unless Gemini says how long to wait).
    gemini_key_cooldown_seconds: float = Field(default=60, gt=0)

    # Defaults for every LLM request; agents can override them per call.
    llm_timeout_seconds: float = Field(default=30, gt=0)
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_max_output_tokens: int = Field(default=2048, ge=1)

    # GitHub webhook (CI/CD telemetry). Without a secret the endpoint rejects every delivery.
    github_webhook_secret: SecretStr | None = None
    # Optional "owner/repo": when set, deliveries from other repositories are ignored.
    github_repository: str | None = None
    # The service GITHUB_REPOSITORY builds and deploys (default: MONITORED_SERVICE).
    github_service: str | None = None

    # The simulated payment-api scenario (Simulate incident / Reset demo). Turn it off where
    # OpsPilot watches a real application; the simulation itself stays available for tests.
    demo_mode: bool = True

    # The real application OpsPilot monitors (optional). Its health is measured by requesting
    # MONITORED_SERVICE_URL every MONITORED_HEALTH_INTERVAL_SECONDS from the backend.
    monitored_project_name: str | None = None
    monitored_environment: str = "production"
    monitored_service: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    monitored_service_url: str | None = Field(default=None, pattern=r"^https?://[^\s]+$")
    # Several real applications, as JSON, e.g.
    # [{"name":"Mallu Typing","service":"mallutyping-web","url":"https://...","repository":"o/r"}]
    # The single-project MONITORED_* settings above still work and are added to this list.
    monitored_projects: Annotated[list[MonitoredProject], NoDecode] = []
    monitored_health_interval_seconds: float = Field(default=60, ge=15)
    # A check slower than this counts as degraded (cold starts on serverless hosts are slow).
    monitored_latency_slo_ms: float = Field(default=3000, gt=0)
    # Incident Manager (real projects only): deterministic detection rules on health checks.
    # An incident opens after this many consecutive DOWN checks, or DEGRADED/DOWN checks.
    incident_down_checks: int = Field(default=2, ge=1, le=20)
    incident_degraded_checks: int = Field(default=3, ge=1, le=20)
    # Run investigation -> root cause -> remediation proposal automatically for a detected
    # incident (stops at human approval), and verification once enough checks follow the action.
    auto_respond: bool = True
    # Health checks required after a remediation before recovery can be verified.
    verify_min_checks: int = Field(default=2, ge=1, le=20)

    # AI code review of pushes to a monitored project's default branch (1 AI call per push).
    # The diff is fetched from api.github.com; GITHUB_TOKEN (read-only) is only needed for
    # private repositories or a higher rate limit. Secrets are redacted before review.
    code_review_enabled: bool = True
    github_token: SecretStr | None = None
    code_review_max_diff_chars: int = Field(default=60_000, ge=2_000, le=400_000)

    # Agent safety limits.
    max_agent_steps: int = Field(default=8, ge=1)
    llm_max_retries: int = Field(default=2, ge=0)
    tool_max_retries: int = Field(default=2, ge=0)

    @field_validator(
        "github_repository",
        "github_service",
        "monitored_project_name",
        "monitored_service",
        "monitored_service_url",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # `MONITORED_SERVICE=` in .env means "not configured", not an invalid empty name.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @field_validator("monitored_projects", mode="before")
    @classmethod
    def _parse_projects(cls, value: object) -> object:
        if isinstance(value, str):
            return json.loads(value) if value.strip() else []
        return value

    @model_validator(mode="after")
    def _unique_services(self) -> "Settings":
        names = [p.service for p in self.projects]
        if len(names) != len(set(names)):
            raise ValueError("each monitored project needs its own service id")
        return self

    @property
    def projects(self) -> list[MonitoredProject]:
        """MONITORED_PROJECTS plus the single-project MONITORED_* settings, if set."""
        projects = list(self.monitored_projects)
        if self.monitored_service and all(p.service != self.monitored_service for p in projects):
            projects.insert(
                0,
                MonitoredProject(
                    name=self.monitored_project_name or self.monitored_service,
                    service=self.monitored_service,
                    url=self.monitored_service_url,
                    repository=self.github_repository,
                    environment=self.monitored_environment,
                ),
            )
        return projects

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("ai_provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        return value.strip().lower()

    @property
    def gemini_api_key_slots(self) -> dict[int, SecretStr]:
        """Configured keys by their GEMINI_API_KEY_<n> number; blank or unset slots are skipped."""
        slots = {
            1: self.gemini_api_key_1,
            2: self.gemini_api_key_2,
            3: self.gemini_api_key_3,
            4: self.gemini_api_key_4,
        }
        return {n: key for n, key in slots.items() if key and key.get_secret_value().strip()}

    @property
    def gemini_api_keys(self) -> list[SecretStr]:
        return list(self.gemini_api_key_slots.values())


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # database_url comes from the environment
