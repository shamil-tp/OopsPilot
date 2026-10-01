"""Read-only CI/CD telemetry tool for the Investigation Agent.

It reads the backend's normalized `cicd_events` only: the agent never sees raw GitHub payloads
and never talks to GitHub. Arguments are strict (known service, bounded window and count).
"""

from datetime import timedelta

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CodeReview
from app.models.enums import CodeReviewStatus
from app.schemas.cicd import CicdEventRead
from app.schemas.code_review import CodeReviewRead
from app.schemas.common import UtcDatetime
from app.services import cicd
from app.tools.registry import ToolPermission, ToolSpec, registry
from app.tools.telemetry_tools import _ServiceArgs

MAX_CICD_WINDOW = timedelta(hours=24)


class CicdEventsArgs(_ServiceArgs):
    since: UtcDatetime
    until: UtcDatetime
    limit: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def _bounded_window(self) -> "CicdEventsArgs":
        if not timedelta(0) <= self.until - self.since <= MAX_CICD_WINDOW:
            raise ValueError("window must be between 0 and 24 hours")
        return self


async def get_recent_cicd_events(db: AsyncSession, args: CicdEventsArgs) -> list[CicdEventRead]:
    """The newest `limit` CI/CD events of the service in the window, oldest first."""
    rows = await cicd.list_events(
        db,
        cicd.CicdFilter(service=args.service, since=args.since, until=args.until),
        limit=args.limit,
    )
    return [CicdEventRead.model_validate(row) for row in reversed(rows)]


registry.register(
    ToolSpec(
        "get_recent_cicd_events",
        ToolPermission.READ_ONLY,
        "Normalized GitHub CI/CD events (pushes, workflow runs, deployments) of one service in a "
        "bounded window (max 24 h, max 10 events).",
        CicdEventsArgs,
        get_recent_cicd_events,
    )
)


class CodeReviewsArgs(_ServiceArgs):
    since: UtcDatetime
    until: UtcDatetime
    limit: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def _bounded_window(self) -> "CodeReviewsArgs":
        if not timedelta(0) <= self.until - self.since <= MAX_CICD_WINDOW:
            raise ValueError("window must be between 0 and 24 hours")
        return self


async def get_recent_code_reviews(db: AsyncSession, args: CodeReviewsArgs) -> list[CodeReviewRead]:
    """Completed AI code reviews of the service's pushes in the window, oldest first."""
    rows = await db.scalars(
        select(CodeReview)
        .where(
            CodeReview.service_name == args.service,
            CodeReview.status == CodeReviewStatus.COMPLETED,
            CodeReview.created_at >= args.since,
            CodeReview.created_at <= args.until,
        )
        .order_by(CodeReview.created_at.desc(), CodeReview.id.desc())
        .limit(args.limit)
    )
    return [CodeReviewRead.model_validate(row) for row in reversed(rows.all())]


registry.register(
    ToolSpec(
        "get_recent_code_reviews",
        ToolPermission.READ_ONLY,
        "Stored AI code reviews of one service's recent pushes (max 24 h, max 5).",
        CodeReviewsArgs,
        get_recent_code_reviews,
    )
)
