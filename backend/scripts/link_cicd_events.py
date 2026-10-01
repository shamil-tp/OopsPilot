"""Link CI/CD events that arrived before their repository was mapped to a project.

    cd backend
    python -m scripts.link_cicd_events            # dry run: shows what would change
    python -m scripts.link_cicd_events --apply    # writes

Uses the same repository -> service mapping as the webhook (MONITORED_PROJECTS / GITHUB_REPOSITORY).
Only sets `service_name` on events that have none; deletes nothing and creates no deployments.
"""

import argparse
import asyncio
from collections import Counter

from sqlalchemy import select, update

from app.core.config import get_settings, redact_database_url
from app.db.session import SessionLocal, engine
from app.models import CicdEvent
from app.services.cicd import repository_service


async def main(apply: bool) -> None:
    print(f"Database: {redact_database_url(get_settings().database_url)}")
    async with SessionLocal() as db:
        rows = await db.execute(
            select(CicdEvent.id, CicdEvent.repository).where(CicdEvent.service_name.is_(None))
        )
        plan: dict[str, list[int]] = {}
        unmapped: Counter[str] = Counter()
        for event_id, repository in rows.all():
            service = repository_service(repository)
            if service:
                plan.setdefault(service, []).append(event_id)
            else:
                unmapped[repository] += 1

        for service, ids in plan.items():
            print(f"  {len(ids)} event(s) -> {service}")
            if apply:
                await db.execute(
                    update(CicdEvent)
                    .where(CicdEvent.id.in_(ids), CicdEvent.service_name.is_(None))
                    .values(service_name=service)
                )
        for repository, count in unmapped.items():
            print(f"  {count} event(s) from {repository}: no project configured, left as is")
        if apply:
            await db.commit()
        print("Applied." if apply else "Dry run: nothing changed (use --apply).")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the changes")
    asyncio.run(main(parser.parse_args().apply))
