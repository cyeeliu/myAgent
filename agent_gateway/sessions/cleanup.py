"""Session artifact cleanup — removes on-disk + Redis state for a session."""
from __future__ import annotations

from ._constants import SESSION_FILES_ROOT, SESSION_STATE_ROOT


def cleanup_session_artifacts(sid: str) -> None:
    """Remove all on-disk + Redis artifacts for a session. Called on delete so
    the SessionsPanel file browser and the workspace .sessions/ tree don't
    accumulate stale dirs, and a reused session_id doesn't hydrate from a
    previous run's leftover state. Best-effort: a cleanup failure must not
    block the delete (the Postgres row is already gone by the time this runs
    in the delete path). Idempotent — missing dirs/keys are fine."""
    if not sid or not isinstance(sid, str) or "/" in sid or "\\" in sid or sid in (".", ".."):
        return  # guard against path traversal / bogus ids
    import shutil
    # 1. On-disk session files (transcript.md, history.json) under /app/agent/sessions/<sid>/
    try:
        shutil.rmtree(SESSION_FILES_ROOT / sid, ignore_errors=True)
    except Exception:
        pass
    # 2. Session-bound state (.tasks/.transcripts/.task_outputs/.worktrees/
    #    .mailboxes/.scheduled_tasks.json) under workspace/.sessions/<sid>/
    try:
        shutil.rmtree(SESSION_STATE_ROOT / sid, ignore_errors=True)
    except Exception:
        pass
    # 3. Redis hot pipes (live stream / chat record stream / ctx hash). These
    #    have a 24h TTL so they'd expire anyway, but deleting now frees memory
    #    immediately and prevents a same-id resurrect from seeing stale frames.
    try:
        from agent_gateway import pipe as pipe_mod
        if pipe_mod.redis_enabled():
            r = pipe_mod._sync_r
            r.delete(f"stream:{sid}", f"chat:{sid}", f"ctx:{sid}")
    except Exception:
        pass
