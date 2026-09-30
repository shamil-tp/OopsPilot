"""Round-robin pool of Gemini API keys with per-key cooldown and usage statistics.

Keys are identified in logs and stats only by `key_id` (e.g. `gemini-key-3`, matching the
GEMINI_API_KEY_3 variable); the secret value is held as a `SecretStr` and only revealed to the
SDK client.

The pool's state changes happen under a `threading.Lock` with no `await` inside, so concurrent
asyncio tasks (and threads) can never receive keys out of order or corrupt counters.
"""

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydantic import SecretStr

from app.core.logging import get_logger

logger = get_logger(__name__)


class KeyState(StrEnum):
    AVAILABLE = "AVAILABLE"
    COOLDOWN = "COOLDOWN"


@dataclass(frozen=True)
class PooledKey:
    key_id: str
    secret: SecretStr  # repr shows '**********'


@dataclass(frozen=True)
class KeyStats:
    """Safe-to-expose view of one key: never includes the secret."""

    key_id: str
    state: KeyState
    cooldown_remaining_seconds: float
    requests: int
    successes: int
    failures: int
    rate_limited: int


@dataclass
class _Slot:
    key: PooledKey
    cooldown_until: float = 0.0
    requests: int = 0
    successes: int = 0
    failures: int = 0
    rate_limited: int = 0


class GeminiKeyPool:
    def __init__(
        self,
        keys: Mapping[int, SecretStr],
        *,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`keys` maps the GEMINI_API_KEY_<n> number to its value (see Settings)."""
        self._slots = [
            _Slot(PooledKey(key_id=f"gemini-key-{n}", secret=secret))
            for n, secret in sorted(keys.items())
        ]
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._next = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._slots)

    def acquire(self) -> PooledKey | None:
        """Next available key in round-robin order, or None if every key is cooling down."""
        with self._lock:
            now = self._clock()
            for offset in range(len(self._slots)):
                index = (self._next + offset) % len(self._slots)
                slot = self._slots[index]
                if slot.cooldown_until <= now:
                    self._next = index + 1
                    slot.requests += 1
                    return slot.key
            return None

    def report_success(self, key: PooledKey) -> None:
        with self._lock:
            self._slot(key).successes += 1

    def report_failure(
        self, key: PooledKey, *, cooldown: bool = False, cooldown_seconds: float | None = None
    ) -> None:
        """Record a failed request. With `cooldown`, skip the key for a while (never forever)."""
        with self._lock:
            slot = self._slot(key)
            slot.failures += 1
            if not cooldown:
                return
            slot.rate_limited += 1
            seconds = cooldown_seconds if cooldown_seconds is not None else self._cooldown_seconds
            slot.cooldown_until = max(slot.cooldown_until, self._clock() + seconds)
        logger.warning(
            "ai_key_cooldown", extra={"key_id": key.key_id, "cooldown_seconds": round(seconds, 1)}
        )

    def next_available_in(self) -> float:
        """Seconds until some key is available (0 if one is available now)."""
        with self._lock:
            now = self._clock()
            return max(0.0, min((s.cooldown_until - now for s in self._slots), default=0.0))

    def stats(self) -> list[KeyStats]:
        with self._lock:
            now = self._clock()
            return [
                KeyStats(
                    key_id=s.key.key_id,
                    state=KeyState.COOLDOWN if s.cooldown_until > now else KeyState.AVAILABLE,
                    cooldown_remaining_seconds=round(max(0.0, s.cooldown_until - now), 1),
                    requests=s.requests,
                    successes=s.successes,
                    failures=s.failures,
                    rate_limited=s.rate_limited,
                )
                for s in self._slots
            ]

    def _slot(self, key: PooledKey) -> _Slot:
        return next(s for s in self._slots if s.key.key_id == key.key_id)
