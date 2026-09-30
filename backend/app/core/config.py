"""Application settings loaded from environment variables (and an optional .env file)."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
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

    github_webhook_secret: SecretStr | None = None

    # Agent safety limits.
    max_agent_steps: int = Field(default=8, ge=1)
    llm_max_retries: int = Field(default=2, ge=0)
    tool_max_retries: int = Field(default=2, ge=0)

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)

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
