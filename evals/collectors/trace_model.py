"""evals.collectors.trace_model — core data structures for evaluation traces.

EvalTrace is the unified output of all three collection modes (online/offline/mock).
ToolCallRecord and TurnRecord are derived at collection time so the metrics layer
never re-derives success/failure — guaranteeing consistency across modes.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any


# ── Error detection (shared by all collectors) ──

_ERROR_PATTERNS = [
    r"^Error:",
    r"^ERROR:",
    r"Traceback \(most recent",
    r"Timeout",
    r"timed out",
    r"ValueError:",
    r"TypeError:",
    r"KeyError:",
    r"FileNotFoundError:",
    r"PermissionError:",
    r"path escapes",
    r"SSRF blocked",
    r"Command not found",
    r"No such file",
    r"Is a directory",
    r"JSONDecodeError",
    r"ConnectionError",
    r"HTTPError",
    r"RateLimitError",
    r"BadRequestError",
]
_ERROR_RE = re.compile("|".join(_ERROR_PATTERNS), re.IGNORECASE)


def is_error_output(output: str) -> bool:
    """Check if a tool result string indicates an error.

    Uses the same prefix/pattern conventions as agent_core/tools.py handlers
    which return 'Error: ...' strings on failure.
    """
    if not output:
        return False
    # Fast path: most errors start with "Error:"
    if output.startswith("Error:") or output.startswith("ERROR:"):
        return True
    return bool(_ERROR_RE.search(output[:500]))


def classify_error(output: str) -> str | None:
    """Classify an error output into a kind category.

    Returns None if the output is not an error.
    """
    if not output:
        return None
    low = output.lower()
    if "timeout" in low or "timed out" in low:
        return "timeout"
    if "path escape" in low:
        return "path_escape"
    if "ssrf" in low:
        return "ssrf"
    if "connection" in low or "http" in low:
        return "http"
    if "json" in low and "decode" in low:
        return "schema"
    if "valueerror" in low or "typeerror" in low or "keyerror" in low:
        return "schema"
    if "filenotfound" in low or "no such file" in low:
        return "path_escape"
    if "permission" in low:
        return "permission"
    if is_error_output(output):
        return "other"
    return None


# ── Core data structures ──

@dataclass
class ToolCallRecord:
    """A single tool invocation with derived success/failure."""
    id: str                         # tool_use_id, links tool_start↔tool_result
    name: str                       # bash / read_file / grep / ...
    input: dict = field(default_factory=dict)
    output: str = ""                # tool_result.content
    blocked: bool = False           # permission/hooks denied
    success: bool = True            # derived: not blocked and not is_error(output)
    error_kind: str | None = None   # timeout / path_escape / http / ssrf / schema / ...
    start_ts: float = 0.0
    end_ts: float = 0.0
    duration_ms: float = 0.0
    readonly: bool = False

    @classmethod
    def from_event_pair(cls, start_payload: dict, result_payload: dict | None,
                        start_ts: float = 0.0, result_ts: float = 0.0) -> "ToolCallRecord":
        """Build a ToolCallRecord from a tool_start event and its matching tool_result."""
        tid = start_payload.get("id", "")
        name = start_payload.get("name", "")
        inp = start_payload.get("arguments", start_payload.get("input", {}))
        output = ""
        blocked = False
        if result_payload is not None:
            output = str(result_payload.get("result", result_payload.get("content", "")))
            blocked = result_payload.get("blocked", False) or result_payload.get("success") is False
        success = not blocked and not is_error_output(output)
        error_kind = classify_error(output) if not success else None
        dur = (result_ts - start_ts) * 1000 if result_ts and start_ts else 0.0
        return cls(
            id=tid, name=name, input=inp if isinstance(inp, dict) else {},
            output=output, blocked=blocked, success=success,
            error_kind=error_kind, start_ts=start_ts, end_ts=result_ts,
            duration_ms=dur,
        )


@dataclass
class TurnRecord:
    """A single agent turn (one LLM call + its tool executions)."""
    index: int
    start_ts: float = 0.0
    end_ts: float = 0.0
    duration_ms: float = 0.0
    stop_reason: str = ""           # end_turn / tool_use / max_tokens
    tool_call_ids: list[str] = field(default_factory=list)
    had_error: bool = False
    had_compaction: bool = False
    had_permission_request: bool = False


@dataclass
class EvalTrace:
    """Unified trace output from all collection modes.

    The metrics layer consumes this structure exclusively.
    """
    task_id: str = ""
    mode: str = ""                  # "online" | "offline" | "mock"
    events: list[dict] = field(default_factory=list)     # [{kind, payload, seq, ts}]
    record: list[dict] = field(default_factory=list)     # session.record snapshot
    spans: list[dict] = field(default_factory=list)      # tracer spans (optional)
    meta: dict = field(default_factory=dict)             # model, workspace, duration, ...
    # Derived (computed at collection time)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    turns: list[TurnRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict."""
        return {
            "task_id": self.task_id,
            "mode": self.mode,
            "events": self.events,
            "record": self.record,
            "spans": self.spans,
            "meta": self.meta,
            "tool_calls": [
                {
                    "id": tc.id, "name": tc.name, "input": tc.input,
                    "output": tc.output[:2000],  # truncate for storage
                    "blocked": tc.blocked, "success": tc.success,
                    "error_kind": tc.error_kind,
                    "start_ts": tc.start_ts, "end_ts": tc.end_ts,
                    "duration_ms": tc.duration_ms, "readonly": tc.readonly,
                }
                for tc in self.tool_calls
            ],
            "turns": [
                {
                    "index": t.index, "start_ts": t.start_ts, "end_ts": t.end_ts,
                    "duration_ms": t.duration_ms, "stop_reason": t.stop_reason,
                    "tool_call_ids": t.tool_call_ids,
                    "had_error": t.had_error, "had_compaction": t.had_compaction,
                    "had_permission_request": t.had_permission_request,
                }
                for t in self.turns
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EvalTrace":
        """Deserialize from dict."""
        tool_calls = [
            ToolCallRecord(
                id=tc["id"], name=tc["name"], input=tc.get("input", {}),
                output=tc.get("output", ""), blocked=tc.get("blocked", False),
                success=tc.get("success", True), error_kind=tc.get("error_kind"),
                start_ts=tc.get("start_ts", 0), end_ts=tc.get("end_ts", 0),
                duration_ms=tc.get("duration_ms", 0), readonly=tc.get("readonly", False),
            )
            for tc in d.get("tool_calls", [])
        ]
        turns = [
            TurnRecord(
                index=t["index"], start_ts=t.get("start_ts", 0),
                end_ts=t.get("end_ts", 0), duration_ms=t.get("duration_ms", 0),
                stop_reason=t.get("stop_reason", ""),
                tool_call_ids=t.get("tool_call_ids", []),
                had_error=t.get("had_error", False),
                had_compaction=t.get("had_compaction", False),
                had_permission_request=t.get("had_permission_request", False),
            )
            for t in d.get("turns", [])
        ]
        return cls(
            task_id=d.get("task_id", ""), mode=d.get("mode", ""),
            events=d.get("events", []), record=d.get("record", []),
            spans=d.get("spans", []), meta=d.get("meta", {}),
            tool_calls=tool_calls, turns=turns,
        )
