import asyncio
from concurrent.futures import ThreadPoolExecutor

from app.ai.key_pool import GeminiKeyPool, KeyState
from tests.ai_fakes import FAKE_KEYS, key_slots


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _ids(pool: GeminiKeyPool, count: int) -> list[str | None]:
    return [key.key_id if (key := pool.acquire()) else None for _ in range(count)]


def test_round_robin_over_four_keys() -> None:
    pool = GeminiKeyPool(key_slots(4))

    assert _ids(pool, 5) == [
        "gemini-key-1",
        "gemini-key-2",
        "gemini-key-3",
        "gemini-key-4",
        "gemini-key-1",
    ]


def test_round_robin_over_two_keys() -> None:
    pool = GeminiKeyPool(key_slots(2))

    assert _ids(pool, 4) == ["gemini-key-1", "gemini-key-2", "gemini-key-1", "gemini-key-2"]


def test_single_key_is_reused() -> None:
    pool = GeminiKeyPool(key_slots(1))

    assert _ids(pool, 3) == ["gemini-key-1"] * 3


def test_key_ids_follow_the_env_variable_numbers() -> None:
    slots = key_slots(4)
    pool = GeminiKeyPool({1: slots[1], 3: slots[3]})  # GEMINI_API_KEY_2 and _4 unset

    assert _ids(pool, 3) == ["gemini-key-1", "gemini-key-3", "gemini-key-1"]


def test_cooldown_key_is_skipped_and_recovers() -> None:
    clock = FakeClock()
    pool = GeminiKeyPool(key_slots(3), cooldown_seconds=60, clock=clock)
    key1 = pool.acquire()
    assert key1 is not None

    pool.report_failure(key1, cooldown=True)

    assert _ids(pool, 4) == ["gemini-key-2", "gemini-key-3", "gemini-key-2", "gemini-key-3"]
    assert pool.stats()[0].state is KeyState.COOLDOWN
    assert pool.stats()[0].cooldown_remaining_seconds == 60

    clock.now += 60
    assert pool.stats()[0].state is KeyState.AVAILABLE
    assert "gemini-key-1" in _ids(pool, 3)


def test_explicit_cooldown_duration_is_used() -> None:
    clock = FakeClock()
    pool = GeminiKeyPool(key_slots(1), cooldown_seconds=60, clock=clock)
    key = pool.acquire()
    assert key is not None

    pool.report_failure(key, cooldown=True, cooldown_seconds=5)

    assert pool.acquire() is None
    assert pool.next_available_in() == 5
    clock.now += 5
    assert pool.acquire() == key


def test_failure_without_cooldown_keeps_key_available() -> None:
    pool = GeminiKeyPool(key_slots(1))
    key = pool.acquire()
    assert key is not None

    pool.report_failure(key)

    assert pool.acquire() == key
    assert pool.stats()[0].failures == 1


def test_all_keys_cooling_down_returns_none() -> None:
    pool = GeminiKeyPool(key_slots(2), clock=FakeClock())
    for _ in range(2):
        key = pool.acquire()
        assert key is not None
        pool.report_failure(key, cooldown=True)

    assert pool.acquire() is None


def test_stats_count_usage_and_never_expose_secrets() -> None:
    pool = GeminiKeyPool(key_slots(2))
    first = pool.acquire()
    second = pool.acquire()
    assert first is not None and second is not None
    pool.report_success(first)
    pool.report_failure(second, cooldown=True)

    stats = pool.stats()

    assert [(s.key_id, s.requests, s.successes, s.failures, s.rate_limited) for s in stats] == [
        ("gemini-key-1", 1, 1, 0, 0),
        ("gemini-key-2", 1, 0, 1, 1),
    ]
    dumped = repr(stats) + repr(first) + str(first)
    assert not any(secret in dumped for secret in FAKE_KEYS)


async def test_concurrent_async_acquisition_is_balanced() -> None:
    pool = GeminiKeyPool(key_slots(4))

    async def worker() -> list[str]:
        acquired = []
        for _ in range(25):
            key = pool.acquire()
            assert key is not None
            acquired.append(key.key_id)
            await asyncio.sleep(0)  # interleave with the other tasks
        return acquired

    results = await asyncio.gather(*(worker() for _ in range(16)))

    assert sum(len(r) for r in results) == 400
    assert [s.requests for s in pool.stats()] == [100, 100, 100, 100]


def test_concurrent_thread_acquisition_is_balanced() -> None:
    pool = GeminiKeyPool(key_slots(4))

    def worker(_: int) -> None:
        for _ in range(2000):
            assert pool.acquire() is not None

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(worker, range(8)))

    assert [s.requests for s in pool.stats()] == [4000, 4000, 4000, 4000]
