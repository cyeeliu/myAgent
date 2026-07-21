"""agent_core.waitlock — signal-driven wait lock for the main loop.

Replaces `bash sleep` polling when the agent has nothing active to do but is
waiting on async events. The agent calls the `wait` tool, which blocks on a
threading.Condition with NO LLM calls during the wait, and resumes the instant a
categorized wake signal arrives:

  - "user"       — a new user message posted while the agent was waiting
                   (agent_gateway.sessions.post_message → wake).
  - "team"       — a teammate/leader wrote to the boss mailbox
                   (agent_core.bus.MessageBus.send → boss listener → wake).
  - "background" — a background task finished (agent_core.background._notify → wake).

The wake is just a *poke*: the actual data for each source stays queued in its
own store (boss mailbox / background_results / pending user message), so a lost
poke can't lose data — the next check_inbox / inject_background_notifications /
fresh turn picks it up. Wakes for sources the waiter didn't ask for are dropped
(the poke is cleared) and the wait continues; the underlying data is still
queued, so a later check_inbox / inject will find it.
"""
import threading
import time


class WaitLock:
    """Condition-backed wait with categorized wake sources.

    One waiter is expected (the main loop, which holds session.lock for the whole
    turn). `wake` may be called from any thread (background monitor, teammate,
    gateway post_message); it just stamps the latest reason and notifies.

    `wait(sources, timeout)` blocks until a wake whose source is in `sources`
    arrives or the timeout elapses, then returns {"source", "detail"} (source
    "timeout" on timeout). Wakes for sources outside the interested set are
    cleared and the wait continues — the data they signal is still queued
    elsewhere, so dropping the poke is safe.
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._reason: dict | None = None
        self._waiting: bool = False

    def is_waiting(self) -> bool:
        with self._cond:
            return self._waiting

    def wake(self, source: str, detail: str = "") -> None:
        """Poke the waiter. Non-blocking; safe from any thread."""
        with self._cond:
            self._reason = {"source": source, "detail": detail or ""}
            self._cond.notify_all()

    def wait(self, sources: list[str], timeout: float) -> dict:
        """Block until a wake with a source in `sources` (or timeout).

        Returns {"source", "detail"}. source == "timeout" on timeout. A wake
        whose source is NOT in `sources` is cleared and the wait continues."""
        wanted = set(sources or [])
        deadline = (time.monotonic() + timeout) if (timeout and timeout > 0) else None
        with self._cond:
            self._waiting = True
            try:
                while True:
                    # Consume any pending wake.
                    if self._reason is not None:
                        r = self._reason
                        self._reason = None
                        if r["source"] in wanted:
                            return r
                        # Non-matching source: drop the poke, keep waiting. The
                        # data it signals is still queued in its own store.
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            return {"source": "timeout", "detail": ""}
                        self._cond.wait(timeout=remaining)
                    else:
                        self._cond.wait()
            finally:
                self._waiting = False
