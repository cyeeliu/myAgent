"""agent_core.paths — workspace and session directory management.

Two distinct roots split by lifecycle:
  - ``workspace_dir()`` — shared workspace root, CWD for file ops / bash / MCP /
    subagents / git. Holds cross-session state: ``.memory/`` and ``skills/``.
    Global (module-level ``_WORKSPACE_ROOT``), set once via ``set_workspace_dir()``;
    defaults to ``REPO_ROOT`` in CLI, ``REPO_ROOT/workspace`` in the gateway.
  - ``session_dir()`` — per-session (threading.local) root for session-bound
    state: ``.tasks/``, ``.transcripts/``, ``.task_outputs/``, ``.worktrees/``,
    ``.mailboxes/``, ``.scheduled_tasks.json``. Defaults to ``workspace_dir()``
    when no session is bound (CLI).
  - ``workdir()`` is an alias for ``workspace_dir()`` (CWD). ``set_workdir(p)``
    binds the session dir (backward-compat).
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

REPO_ROOT = Path.cwd()

# ── Workspace vs session dirs ──
_WORKSPACE_ROOT = REPO_ROOT
_sess_local = threading.local()


def workspace_dir() -> Path:
    """Shared workspace root. CWD for file ops/bash/MCP/subagents. Holds
    .memory/ and skills/ (shared across all sessions)."""
    return _WORKSPACE_ROOT


def set_workspace_dir(p) -> None:
    """Set the shared workspace root and ensure .memory/ + skills/ exist."""
    global _WORKSPACE_ROOT
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    for _sub in (".memory", "skills"):
        (p / _sub).mkdir(parents=True, exist_ok=True)
    _WORKSPACE_ROOT = p


def session_dir() -> Path:
    """Per-session dir for session-bound state
    (.tasks/.transcripts/.task_outputs/.worktrees/.mailboxes/.scheduled_tasks.json).
    Defaults to workspace_dir() when no session is bound (CLI)."""
    return getattr(_sess_local, "session", None) or _WORKSPACE_ROOT


def set_session_dir(p) -> None:
    """Bind the per-session dir and ensure session sub-dirs exist."""
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    for _sub in (".tasks", ".transcripts", ".task_outputs/tool-results",
                 ".worktrees", ".mailboxes"):
        (p / _sub).mkdir(parents=True, exist_ok=True)
    _sess_local.session = p


def clear_session_dir() -> None:
    """Unbind the per-session dir (restore to workspace_dir() default).

    Used by the eval harness and other callers that temporarily bind a
    session dir on a shared thread and must restore the default afterward
    so subsequent runs on the same (pool-reused) thread don't inherit a
    stale session root.
    """
    if hasattr(_sess_local, "session"):
        try:
            del _sess_local.session
        except AttributeError:
            pass


def workdir() -> Path:
    """CWD for file ops / bash / MCP / subagents. Per-session: returns
    session_dir() (workspace/.sessions/<sid>/ in the gateway, workspace_dir()
    in CLI when no session is bound) so each session gets an isolated work
    directory. Shared state (.memory/, skills/, .permissions/) stays under
    workspace_dir() via the dedicated workspace_dir() accessor."""
    return session_dir()


def set_workdir(p) -> None:
    """Backward-compat entry point: bind the per-session dir to ``p``. The shared
    workspace is set separately via set_workspace_dir() (gateway does this once
    at startup; CLI leaves it at the REPO_ROOT default)."""
    set_session_dir(p)


def _transcript_dir() -> Path:
    return session_dir() / ".transcripts"


def _tool_results_dir() -> Path:
    return session_dir() / ".task_outputs" / "tool-results"


# CLI defaults: dot-dirs live under REPO_ROOT (= cwd at launch) until a session
# binds a separate workspace/session pair via set_workdir().
for _sub in (".tasks", ".transcripts", ".task_outputs/tool-results",
             ".worktrees", ".mailboxes", ".memory", "skills"):
    (REPO_ROOT / _sub).mkdir(parents=True, exist_ok=True)
