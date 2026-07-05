"""PostgreSQL persistence for sessions (spec §2/§9 memory-svc boundary).

The DB is the durable source of truth for session metadata + conversation
history; `SessionManager._sessions` is a cache of live (transport-attached)
sessions. Writes happen at user-message post and turn end; reads happen on
list and lazy hydration.

Uses psycopg3 synchronous API + a thread-safe ConnectionPool. The agent worker
thread persists synchronously after a turn; async endpoints wrap calls with
`asyncio.to_thread`. One pool, no async/sync split.

If DATABASE_URL is unset, the pool stays None and every function degrades to a
no-op (load → None, list → [], saves → skip) so local dev without postgres
still works in-memory.
"""
from __future__ import annotations
import json
import os
import threading
from typing import Any, Optional

from psycopg_pool import ConnectionPool
from psycopg.types.json import Jsonb

_pool: Optional[ConnectionPool] = None
_init_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  session_id    TEXT PRIMARY KEY,
  transport     TEXT NOT NULL,
  created_at    DOUBLE PRECISION NOT NULL,
  last_activity DOUBLE PRECISION NOT NULL,
  title         TEXT NOT NULL,
  chat_record   JSONB NOT NULL DEFAULT '[]',
  llm_context   JSONB NOT NULL DEFAULT '[]'
);
"""

# Migration for pre-split databases: rename history → chat_record, add llm_context.
_MIGRATION = """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'sessions' AND column_name = 'history'
  ) THEN
    ALTER TABLE sessions RENAME COLUMN history TO chat_record;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'sessions' AND column_name = 'chat_record'
  ) THEN
    ALTER TABLE sessions ADD COLUMN chat_record JSONB NOT NULL DEFAULT '[]';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'sessions' AND column_name = 'llm_context'
  ) THEN
    ALTER TABLE sessions ADD COLUMN llm_context JSONB NOT NULL DEFAULT '[]';
  END IF;
  -- Backfill: old sessions had no llm_context; seed it from the full chat_record
  -- so the first post-split turn has a working (uncompacted) context.
  UPDATE sessions SET llm_context = chat_record WHERE llm_context = '[]'::jsonb;
END $$;
"""


def _normalize(obj):
    """Recursively convert non-JSON-native objects (SimpleNamespace blocks like
    code._TextBlock/_ToolUseBlock, dataclasses, pydantic models) to plain
    dict/list/primitives so Jsonb can serialize the history."""
    from types import SimpleNamespace
    if isinstance(obj, SimpleNamespace):
        # vars() only sees instance attrs; _TextBlock/_ToolUseBlock set `type`
        # as a CLASS attr, so merge the MRO's data attrs to keep it.
        d = {}
        for cls in type(obj).__mro__:
            for k, v in getattr(cls, "__dict__", {}).items():
                if not k.startswith("_") and not callable(v):
                    d[k] = v
        d.update(vars(obj))
        return _normalize(d)
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalize(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    # dataclass-ish / pydantic-ish
    for m in ("model_dump", "to_dict"):
        if hasattr(obj, m):
            try:
                return _normalize(getattr(obj, m)())
            except Exception:
                pass
    if hasattr(obj, "__dict__"):
        return _normalize(vars(obj))
    return str(obj)


def init_pool(url: Optional[str]) -> None:
    """Open the connection pool and ensure the schema exists. No-op if url is None."""
    global _pool
    if not url:
        return
    with _init_lock:
        if _pool is not None:
            return
        _pool = ConnectionPool(url, min_size=1, max_size=8, open=True)
        with _pool.connection() as conn:
            conn.execute(SCHEMA)
            conn.execute(_MIGRATION)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def _row_to_dict(row) -> dict:
    return {
        "session_id": row[0],
        "transport": row[1],
        "created_at": row[2],
        "last_activity": row[3],
        "title": row[4],
        "chat_record": row[5],
        "llm_context": row[6],
    }


def create_session_row(sid: str, transport: str, created_at: float, title: str) -> None:
    if _pool is None:
        return
    with _pool.connection() as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, transport, created_at, last_activity, title, "
            "chat_record, llm_context) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (session_id) DO NOTHING",
            (sid, transport, created_at, created_at, title, Jsonb([]), Jsonb([])),
        )


def save_chat_record(sid: str, record: list, last_activity: float, title: str) -> None:
    """Upsert the append-only chat record (never compacted) + metadata."""
    if _pool is None:
        return
    with _pool.connection() as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, transport, created_at, last_activity, title, "
            "chat_record, llm_context) VALUES (%s, '', %s, %s, %s, %s, %s) "
            "ON CONFLICT (session_id) DO UPDATE SET last_activity = EXCLUDED.last_activity, "
            "title = EXCLUDED.title, chat_record = EXCLUDED.chat_record",
            (sid, last_activity, last_activity, title, Jsonb(_normalize(record)), Jsonb([])),
        )


def save_llm_context(sid: str, context: list, last_activity: float) -> None:
    """Upsert the compacted LLM context snapshot."""
    if _pool is None:
        return
    with _pool.connection() as conn:
        conn.execute(
            "UPDATE sessions SET llm_context = %s, last_activity = %s WHERE session_id = %s",
            (Jsonb(_normalize(context)), last_activity, sid),
        )


def load_session(sid: str) -> Optional[dict]:
    """Load one session row for hydration. Returns None if missing or DB disabled."""
    if _pool is None:
        return None
    with _pool.connection() as conn:
        cur = conn.execute(
            "SELECT session_id, transport, created_at, last_activity, title, "
            "chat_record, llm_context FROM sessions WHERE session_id = %s",
            (sid,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


def list_session_rows() -> list[dict]:
    if _pool is None:
        return []
    with _pool.connection() as conn:
        cur = conn.execute(
            "SELECT session_id, transport, created_at, last_activity, title, "
            "chat_record, llm_context FROM sessions ORDER BY last_activity DESC"
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def delete_session_row(sid: str) -> None:
    if _pool is None:
        return
    with _pool.connection() as conn:
        conn.execute("DELETE FROM sessions WHERE session_id = %s", (sid,))
