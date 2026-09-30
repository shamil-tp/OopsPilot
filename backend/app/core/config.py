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

    # Gemini is the only provider for now. All agents will share these keys through a key pool.
    ai_provider: Literal["gemini"] = "gemini"
    gemini_model: str = "gemini-2.5-flash"
    gemini_api_key_1: SecretStr | None = None
    gemini_api_key_2: SecretStr | None = None
    gemini_api_key_3: SecretStr | None = None
    gemini_api_key_4: SecretStr | None = None

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

    @property
    def gemini_api_keys(self) -> list[SecretStr]:
        keys = (
            self.gemini_api_key_1,
            self.gemini_api_key_2,
            self.gemini_api_key_3,
            self.gemini_api_key_4,
        )
        return [key for key in keys if key and key.get_secret_value().strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # database_url comes from the environment
