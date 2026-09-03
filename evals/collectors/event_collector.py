"""evals.collectors.event_collector — online event collection via EventSink.

EvalCollectorSink is appended to Session.sinks (a plain list) to capture
the full event sequence and derive ToolCallRecord/TurnRecord in real time.
Zero intrusion: only uses the existing EventSink protocol.
"""
from __future__ import annotations

import time
from typing import Any

from agent_core.session import EventSink
from evals.collectors.trace_model import (
    EvalTrace, ToolCallRecord, TurnRecord, is_error_output, classify_error,
)


class EvalCollectorSink(EventSink):
    """Observer that captures events and derives structured trace data.

    Usage (direct mode):
        collector = EvalCollectorSink()
        sess = Session(sinks=[collector, ...])
        agent_loop(sess)
        trace = collector.finalize(task_id="...", mode="online")

    Usage (gateway mode):
        gs = SessionManager.create(...)
        gs.agent.sinks.append(EvalCollectorSink())
        gs.post_message(prompt)
        # after turn ends:
        trace = collector.finalize(...)
    """
    streaming = True  # accept token events (we buffer but don't act on them)

    def __init__(self):
        self._events: list[dict] = []
        self._tool_starts: dict[str, dict] = {}   # id → {payload, ts}
        self._tool_calls: list[ToolCallRecord] = []
        self._turn_index = 0
        self._turn_start_ts = 0.0
        self._turn_tool_ids: list[str] = []
        self._turn_had_error = False
        self._turn_had_compaction = False
        self._turn_had_perm = False
        self._turns: list[TurnRecord] = []
        self._start_ts = time.time()
        self._seq = 0

    def emit(self, kind: str, payload: dict):
        """Record event and derive structured data. Never calls back into session."""
        ts = time.time()
        self._seq += 1
        self._events.append({"kind": kind, "payload": payload, "seq": self._seq, "ts": ts})

        if kind == "tool_start":
            tid = payload.get("id", "")
            self._tool_starts[tid] = {"payload": payload, "ts": ts}
            self._turn_tool_ids.append(tid)

        elif kind == "tool_result":
            tid = payload.get("id", payload.get("tool_call_id", ""))
            start_info = self._tool_starts.pop(tid, None)
            if start_info is not None:
                tc = ToolCallRecord.from_event_pair(
                    start_info["payload"], payload,
                    start_ts=start_info["ts"], result_ts=ts,
                )
                self._tool_calls.append(tc)
            else:
                # Orphan result (no matching start) — still record
                output = str(payload.get("result", payload.get("content", "")))
                blocked = payload.get("blocked", False)
                self._tool_calls.append(ToolCallRecord(
                    id=tid, name=payload.get("tool_name", ""),
                    output=output, blocked=blocked,
                    success=not blocked and not is_error_output(output),
                    error_kind=classify_error(output) if blocked or is_error_output(output) else None,
                    end_ts=ts,
                ))

        elif kind == "error":
            self._turn_had_error = True

        elif kind == "compacted":
            self._turn_had_compaction = True

        elif kind == "permission_request":
            self._turn_had_perm = True

        elif kind == "done":
            self._end_turn("end_turn", ts)

        elif kind == "token" and not self._turn_start_ts:
            self._turn_start_ts = ts

    def _end_turn(self, stop_reason: str, ts: float):
        """Finalize the current turn record."""
        if self._turn_start_ts or self._turn_tool_ids or self._turn_had_error:
            dur = (ts - self._turn_start_ts) * 1000 if self._turn_start_ts else 0.0
            self._turns.append(TurnRecord(
                index=self._turn_index,
                start_ts=self._turn_start_ts or ts,
                end_ts=ts,
                duration_ms=dur,
                stop_reason=stop_reason,
                tool_call_ids=list(self._turn_tool_ids),
                had_error=self._turn_had_error,
                had_compaction=self._turn_had_compaction,
                had_permission_request=self._turn_had_perm,
            ))
            self._turn_index += 1
        # Reset for next turn
        self._turn_start_ts = 0.0
        self._turn_tool_ids = []
        self._turn_had_error = False
        self._turn_had_compaction = False
        self._turn_had_perm = False

    def finalize(self, task_id: str = "", mode: str = "online",
                 record: list[dict] | None = None,
                 meta: dict | None = None) -> EvalTrace:
        """Produce the final EvalTrace. Call after agent_loop returns."""
        # Close any dangling turn
        if self._turn_tool_ids or self._turn_had_error:
            self._end_turn("interrupted", time.time())

        return EvalTrace(
            task_id=task_id,
            mode=mode,
            events=list(self._events),
            record=record or [],
            spans=[],  # filled by TraceCollector if used
            meta={
                "duration": time.time() - self._start_ts,
                "event_count": len(self._events),
                **(meta or {}),
            },
            tool_calls=list(self._tool_calls),
            turns=list(self._turns),
        )
