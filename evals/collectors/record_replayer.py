"""evals.collectors.record_replayer — offline trace reconstruction from records.

Rebuilds an EvalTrace from a persisted session record (history.json,
Postgres chat_record, or Redis chat stream) without re-running the agent.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from evals.collectors.trace_model import (
    EvalTrace, ToolCallRecord, TurnRecord, is_error_output, classify_error,
)


class RecordReplayer:
    """Reconstruct EvalTrace from persisted records."""

    def from_record(self, record: list[dict], task_id: str = "") -> EvalTrace:
        """Build trace directly from a session.record list.

        Record entries are Anthropic-style content blocks:
        - {role: "user", content: [{type: "tool_result", tool_use_id, content}]}
        - {role: "assistant", content: [{type: "tool_use", id, name, input}, {type: "text", text}]}
        """
        events: list[dict] = []
        tool_calls: list[ToolCallRecord] = []
        turns: list[TurnRecord] = []
        # Map tool_use_id → ToolCallRecord (partially filled)
        pending: dict[str, ToolCallRecord] = {}
        turn_idx = 0
        ts = time.time()

        for i, msg in enumerate(record):
            role = msg.get("role", "")
            content = msg.get("content", [])
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]

            if role == "assistant":
                turn_tool_ids = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tid = block.get("id", "")
                        name = block.get("name", "")
                        inp = block.get("input", {})
                        tc = ToolCallRecord(id=tid, name=name, input=inp, start_ts=ts + i)
                        pending[tid] = tc
                        tool_calls.append(tc)
                        turn_tool_ids.append(tid)
                        events.append({"kind": "tool_start", "payload": {
                            "id": tid, "name": name, "arguments": inp,
                        }, "seq": len(events) + 1, "ts": ts + i})
                    elif isinstance(block, dict) and block.get("type") == "text":
                        events.append({"kind": "token", "payload": {
                            "content": block.get("text", ""),
                        }, "seq": len(events) + 1, "ts": ts + i})
                turns.append(TurnRecord(
                    index=turn_idx, start_ts=ts + i, end_ts=ts + i + 1,
                    duration_ms=1000.0, stop_reason="tool_use" if turn_tool_ids else "end_turn",
                    tool_call_ids=turn_tool_ids,
                ))
                turn_idx += 1

            elif role == "user":
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tid = block.get("tool_use_id", "")
                        output = block.get("content", "")
                        if isinstance(output, list):
                            output = " ".join(
                                b.get("text", "") if isinstance(b, dict) else str(b)
                                for b in output
                            )
                        output = str(output)
                        tc = pending.pop(tid, None)
                        if tc is not None:
                            tc.output = output
                            tc.blocked = "blocked" in str(block).lower()
                            tc.success = not tc.blocked and not is_error_output(output)
                            tc.error_kind = classify_error(output) if not tc.success else None
                            tc.end_ts = ts + i
                            tc.duration_ms = (tc.end_ts - tc.start_ts) * 1000
                        events.append({"kind": "tool_result", "payload": {
                            "id": tid, "result": output[:500],
                        }, "seq": len(events) + 1, "ts": ts + i})

        return EvalTrace(
            task_id=task_id, mode="offline",
            events=events, record=record, spans=[],
            meta={"source": "record", "record_len": len(record)},
            tool_calls=tool_calls, turns=turns,
        )

    def from_history_json(self, path: str | Path, task_id: str = "") -> EvalTrace:
        """Read history.json written by the gateway."""
        path = Path(path)
        data = json.loads(path.read_text())
        # history.json format: [{role, content, timestamp} | {role:"assistant", event_type, content, timestamp}]
        record = []
        for entry in data:
            role = entry.get("role", "")
            content = entry.get("content", "")
            if role == "assistant":
                record.append({"role": "assistant", "content": [
                    {"type": "text", "text": content}
                ]})
            elif role == "user":
                record.append({"role": "user", "content": [
                    {"type": "text", "text": content}
                ]})
        return self.from_record(record, task_id=task_id or path.stem)

    def from_postgres(self, sid: str, task_id: str = "") -> EvalTrace:
        """Read chat_record from Postgres (requires DATABASE_URL)."""
        try:
            from agent_gateway import db
            row = db.load_session(sid)
            if row is None:
                raise ValueError(f"session {sid} not found")
            record = row.get("chat_record") or []
            return self.from_record(record, task_id=task_id or sid)
        except ImportError:
            raise RuntimeError("Postgres not available (agent_gateway.db not importable)")

    def from_redis_chat(self, sid: str, task_id: str = "") -> EvalTrace:
        """Read chat:{sid} stream from Redis."""
        try:
            from agent_gateway.sessions import pipe_mod
            chat_pipe = pipe_mod.make_chat_pipe(sid)
            record = chat_pipe.all()
            return self.from_record(record, task_id=task_id or sid)
        except (ImportError, Exception):
            raise RuntimeError("Redis chat stream not available")
