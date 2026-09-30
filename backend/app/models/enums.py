"""Domain enums shared by models, schemas, and (mirrored in) the frontend types."""

from enum import StrEnum

from sqlalchemy import Enum


class IncidentStatus(StrEnum):
    DETECTED = "DETECTED"
    INVESTIGATING = "INVESTIGATING"
    ANALYZING = "ANALYZING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REMEDIATING = "REMEDIATING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class ServiceStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class DeploymentStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class AgentName(StrEnum):
    ORCHESTRATOR = "orchestrator"
    INVESTIGATION = "investigation"
    ROOT_CAUSE = "root_cause"
    REMEDIATION = "remediation"
    VERIFICATION = "verification"


class AgentRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ActionType(StrEnum):
    ROLLBACK_DEPLOYMENT = "ROLLBACK_DEPLOYMENT"
    RESTART_SERVICE = "RESTART_SERVICE"
    NO_ACTION = "NO_ACTION"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class CicdCategory(StrEnum):
    """What a CI/CD event represents. Only DEPLOYMENT events can become deployment telemetry."""

    COMMIT = "COMMIT"
    BUILD = "BUILD"
    TEST = "TEST"
    DEPLOYMENT = "DEPLOYMENT"


class CicdStatus(StrEnum):
    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class CicdConclusion(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    NEUTRAL = "NEUTRAL"
    SKIPPED = "SKIPPED"
    OTHER = "OTHER"


class RecoveryStatus(StrEnum):
    RECOVERED = "RECOVERED"
    NOT_RECOVERED = "NOT_RECOVERED"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


def enum_column(enum_cls: type[StrEnum]) -> Enum:
    """Store enums as VARCHAR (not native PG enums) so adding values needs no type migration."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        values_callable=lambda members: [m.value for m in members],
    )
