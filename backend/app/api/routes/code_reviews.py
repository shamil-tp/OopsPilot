"""AI code reviews of pushes to monitored projects (read-only, plus retry of a failed review)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import code_review
from app.db.session import get_db
from app.models import CodeReview
from app.models.enums import CodeReviewStatus
from app.schemas.code_review import CodeReviewRead
from app.schemas.common import ErrorResponse

router = APIRouter(prefix="/code-reviews", tags=["code reviews"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=list[CodeReviewRead], summary="Code reviews, newest first")
async def list_code_reviews(
    db: DbSession,
    service: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[CodeReviewRead]:
    stmt = select(CodeReview)
    if service:
        stmt = stmt.where(CodeReview.service_name == service)
    rows = await db.scalars(
        stmt.order_by(CodeReview.created_at.desc(), CodeReview.id.desc()).limit(limit)
    )
    return [CodeReviewRead.model_validate(row) for row in rows]


@router.get(
    "/{review_id}",
    response_model=CodeReviewRead,
    summary="One code review",
    responses={404: {"model": ErrorResponse, "description": "Code review not found"}},
)
async def get_code_review(review_id: int, db: DbSession) -> CodeReviewRead:
    row = await db.get(CodeReview, review_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Code review {review_id} not found")
    return CodeReviewRead.model_validate(row)


@router.post(
    "/{review_id}/retry",
    response_model=CodeReviewRead,
    summary="Retry a failed code review (e.g. after a GitHub rate limit)",
    responses={
        404: {"model": ErrorResponse, "description": "Code review not found"},
        409: {"model": ErrorResponse, "description": "Only FAILED reviews can be retried"},
    },
)
async def retry_code_review(review_id: int, db: DbSession) -> CodeReviewRead:
    row = await db.get(CodeReview, review_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Code review {review_id} not found")
    if row.status is not CodeReviewStatus.FAILED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Code review {review_id} is {row.status}")
    review = await code_review.retry(db, review_id)
    assert review is not None
    await db.refresh(review)
    return CodeReviewRead.model_validate(review)
