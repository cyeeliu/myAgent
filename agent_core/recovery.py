"""agent_core.recovery — extracted from code.py (s20 comprehensive agent)."""
import random
import time
from agent_core import model_config
from agent_core.env import (
    BASE_DELAY_429_MS, BASE_DELAY_MS, MAX_CONSECUTIVE_529,
    MAX_DELAY_429_MS, MAX_RETRIES, MAX_RETRIES_429,
)


class RecoveryState:
    def __init__(self):
        self.has_escalated = False
        self.recovery_count = 0
        self.consecutive_529 = 0
        self.has_attempted_reactive_compact = False
        self.current_model = model_config.model()

def retry_delay(attempt: int) -> float:
    base = min(BASE_DELAY_MS * (2 ** attempt), 32000) / 1000
    return base + random.uniform(0, base * 0.25)

def retry_delay_429(attempt: int) -> float:
    # Longer base + higher cap than the transient-error path: rate-limit windows
    # are typically tens of seconds, not sub-second.
    base = min(BASE_DELAY_429_MS * (2 ** attempt), MAX_DELAY_429_MS) / 1000
    return base + random.uniform(0, base * 0.25)

def with_retry(fn, state: RecoveryState):
    # 429 (rate limit) gets its own generous budget; 529/overloaded and other
    # retriable errors share MAX_RETRIES. Each counter only counts its own kind,
    # so a 429 storm doesn't burn the 529 budget or vice versa.
    attempts_429 = 0
    attempts_529 = 0
    while True:
        try:
            result = fn()
            state.consecutive_529 = 0
            return result
        except Exception as e:
            name = type(e).__name__.lower()
            msg = str(e).lower()
            if "ratelimit" in name or "429" in msg:
                attempts_429 += 1
                if attempts_429 > MAX_RETRIES_429:
                    raise RuntimeError(
                        f"Max retries ({MAX_RETRIES_429}) exceeded for 429 rate limit")
                delay = retry_delay_429(attempts_429 - 1)
                print(f"  \033[33m[429] retry {attempts_429}/{MAX_RETRIES_429} "
                      f"after {delay:.1f}s\033[0m")
                time.sleep(delay)
                continue
            if "overloaded" in name or "529" in msg or "overloaded" in msg:
                attempts_529 += 1
                if attempts_529 > MAX_RETRIES:
                    raise RuntimeError(
                        f"Max retries ({MAX_RETRIES}) exceeded for 529/overloaded")
                state.consecutive_529 += 1
                fb = model_config.fallback()
                if state.consecutive_529 >= MAX_CONSECUTIVE_529 and fb:
                    state.current_model = fb
                    state.consecutive_529 = 0
                    print(f"  \033[31m[529] switching to {fb}\033[0m")
                delay = retry_delay(attempts_529 - 1)
                print(f"  \033[33m[529] retry {attempts_529}/{MAX_RETRIES} "
                      f"after {delay:.1f}s\033[0m")
                time.sleep(delay)
                continue
            raise

def is_prompt_too_long_error(e: Exception) -> bool:
    msg = str(e).lower()
    return (("prompt" in msg and "long" in msg)
            or "context_length_exceeded" in msg
            or "max_context_window" in msg)
