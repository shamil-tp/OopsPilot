from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel


def as_utc(value: datetime) -> datetime:
    # Timestamps are stored in UTC; SQLite (tests) returns them without tzinfo.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# Always serialized with an explicit UTC offset, so clients never misread it as local time.
UtcDatetime = Annotated[datetime, AfterValidator(as_utc)]


class ErrorResponse(BaseModel):
    """Body of every error response (FastAPI's `{"detail": ...}` shape)."""

    detail: str
