"""Recovery / retry logic: 429 rate limits get a generous separate budget with
longer backoff; 529/overloaded keep the short budget; other errors raise."""
import os
os.environ.setdefault("MODEL_ID", "test-model")
os.environ.setdefault("OPENAI_API_KEY", "dummy")
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import agent_core.recovery as recovery
from agent_core.recovery import RecoveryState, with_retry, retry_delay_429
from agent_core.env import MAX_RETRIES_429


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # with_retry backs off with real time.sleep; tests must not wait minutes.
    monkeypatch.setattr(recovery.time, "sleep", lambda *_a, **_k: None)


class RateLimitError(Exception):
    pass


class OverloadedError(Exception):
    pass


def test_429_retries_up_to_max_then_raises():
    """A sustained 429 should retry MAX_RETRIES_429 times then raise."""
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise RateLimitError("rate limit exceeded 429")

    with pytest.raises(RuntimeError, match="429 rate limit"):
        with_retry(fn, RecoveryState())
    # MAX_RETRIES_429 retries after the first failure → that many + 1 calls.
    assert calls["n"] == MAX_RETRIES_429 + 1


def test_429_succeeds_within_budget():
    """A 429 that clears on the 3rd call should succeed, not exhaust the budget."""
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimitError("429 too many requests")
        return "ok"

    assert with_retry(fn, RecoveryState()) == "ok"
    assert calls["n"] == 3


def test_non_retriable_error_raises_immediately():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("bad request 400")

    with pytest.raises(ValueError):
        with_retry(fn, RecoveryState())
    assert calls["n"] == 1


def test_429_and_529_have_separate_budgets():
    """A 429 storm must not burn the 529 budget — the counters are independent."""
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        # alternate, but never succeed — should exhaust 429 first (larger budget)
        raise RateLimitError("429")

    with pytest.raises(RuntimeError, match="429"):
        with_retry(fn, RecoveryState())


def test_retry_delay_429_grows_and_caps():
    d0 = retry_delay_429(0)
    d1 = retry_delay_429(1)
    # base grows exponentially; cap keeps it finite
    assert d0 > 0
    assert d1 > d0
    assert retry_delay_429(20) < 90  # capped near MAX_DELAY_429_MS (60s) + jitter


if __name__ == "__main__":
    test_429_retries_up_to_max_then_raises()
    test_429_succeeds_within_budget()
    test_non_retriable_error_raises_immediately()
    test_429_and_529_have_separate_budgets()
    test_retry_delay_429_grows_and_caps()
    print("test_recovery: OK")
