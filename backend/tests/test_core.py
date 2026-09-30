import json
import logging

import pytest
from pydantic import ValidationError

from app.core.config import Settings, normalize_database_url, redact_database_url
from app.core.logging import JsonFormatter

POOLER = "aws-0-eu-central-1.pooler.supabase.com"


def make_settings(**values: object) -> Settings:
    """Settings isolated from the developer's real .env file."""
    values.setdefault("database_url", "postgresql+asyncpg://u:p@db.example:5432/opspilot")
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_database_url_is_normalized_to_async_driver() -> None:
    settings = make_settings(database_url="postgresql://u:p@db.example:5432/opspilot")
    assert settings.database_url == "postgresql+asyncpg://u:p@db.example:5432/opspilot"


def test_supabase_url_gets_required_ssl() -> None:
    url = normalize_database_url(f"postgresql://postgres.abc:pw@{POOLER}:5432/postgres")
    assert url == f"postgresql+asyncpg://postgres.abc:pw@{POOLER}:5432/postgres?ssl=require"


def test_libpq_sslmode_is_translated_for_asyncpg() -> None:
    url = normalize_database_url(
        f"postgresql://postgres.abc:pw@{POOLER}:5432/postgres?sslmode=verify-full"
    )
    assert url.endswith("/postgres?ssl=verify-full")


def test_special_characters_in_password_survive_normalization() -> None:
    url = normalize_database_url("postgresql://u:p%40ss%2Fw0rd@db.example:5432/opspilot")
    assert url == "postgresql+asyncpg://u:p%40ss%2Fw0rd@db.example:5432/opspilot"


def test_redacted_url_hides_password() -> None:
    redacted = redact_database_url(
        f"postgresql+asyncpg://postgres.abc:s3cret@{POOLER}:5432/postgres"
    )
    assert "s3cret" not in redacted
    assert POOLER in redacted


def test_cors_origins_accept_comma_separated_string() -> None:
    settings = make_settings(cors_origins="http://a.test, http://b.test")
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_only_non_empty_gemini_keys_are_used() -> None:
    settings = make_settings(
        gemini_api_key_1="k1", gemini_api_key_2="", gemini_api_key_3="k3", gemini_api_key_4=None
    )
    assert [k.get_secret_value() for k in settings.gemini_api_keys] == ["k1", "k3"]
    assert "k1" not in repr(settings)


def test_json_formatter_includes_extra_fields() -> None:
    record = logging.LogRecord("opspilot", logging.INFO, __file__, 1, "tool_call", None, None)
    record.agent = "investigation"
    record.tool = "get_application_logs"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "tool_call"
    assert payload["level"] == "INFO"
    assert payload["agent"] == "investigation"
    assert payload["tool"] == "get_application_logs"
