from typing import Literal

from pydantic import BaseModel


class DatabaseHealth(BaseModel):
    status: Literal["ok", "unavailable"]
    latency_ms: float | None = None
    error: str | None = None


class AIProviderHealth(BaseModel):
    provider: str
    model: str
    configured_keys: int


class SystemHealth(BaseModel):
    status: Literal["ok", "degraded"]
    app: str
    version: str
    environment: str
    database: DatabaseHealth
    ai: AIProviderHealth
