import os
from collections.abc import AsyncIterator, Iterator

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from app.core.config import BACKEND_DIR, ENV_FILES, normalize_database_url

# The test suite migrates down to an empty schema, so it must never run against the shared
# Supabase development database. It only ever uses TEST_DATABASE_URL (SQLite by default).
_TEST_DATABASE_URL = normalize_database_url(
    os.environ.get(
        "TEST_DATABASE_URL", f"sqlite+aiosqlite:///{(BACKEND_DIR / 'test.db').as_posix()}"
    )
)


def _development_database_url() -> str | None:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    values: dict[str, str | None] = {}
    for env_file in ENV_FILES:
        if env_file.exists():
            values.update(dotenv_values(env_file))
    return values.get("DATABASE_URL")


def _same_database(a: str, b: str) -> bool:
    url_a, url_b = make_url(normalize_database_url(a)), make_url(normalize_database_url(b))
    fields = ("host", "port", "database", "username")
    return all(getattr(url_a, f) == getattr(url_b, f) for f in fields)


_dev_url = _development_database_url()
if _dev_url and _same_database(_TEST_DATABASE_URL, _dev_url):
    raise RuntimeError(
        "TEST_DATABASE_URL points at the development database (DATABASE_URL). "
        "Refusing to run: the test suite drops every table. Use a separate test database."
    )

# Must run before `app.main` / `app.db` are imported: the engine binds at import time.
os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
os.environ["APP_ENV"] = "test"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    """Build the schema through the real Alembic migrations, so they are tested too."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
    command.downgrade(config, "base")


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def clean_demo(client: AsyncClient) -> None:
    """Start from the healthy demo baseline (no incidents) on the test database."""
    response = await client.post("/api/demo/reset")
    assert response.status_code == 200


@pytest.fixture
async def isolated_real_projects() -> AsyncIterator[None]:
    """Real-project data is never removed by a demo reset (by design), so tests that create it
    remove it themselves, before and after: incidents, telemetry and code reviews of non-demo
    services, and CI/CD events of repositories other than the demo's."""
    from sqlalchemy import delete

    from app.github.demo import DEMO_REPOSITORY
    from app.models import CicdEvent, CodeReview, Deployment, Incident, LogEntry, ServiceHealth
    from app.services.service_catalog import DEMO_SERVICE_NAMES

    async def purge() -> None:
        async with SessionLocal() as session:
            await session.execute(delete(CodeReview))
            await session.execute(delete(CicdEvent).where(CicdEvent.repository != DEMO_REPOSITORY))
            for model in (Incident, ServiceHealth, Deployment, LogEntry):
                await session.execute(
                    delete(model).where(model.service_name.not_in(DEMO_SERVICE_NAMES))
                )
            await session.commit()

    await purge()
    yield
    await purge()
