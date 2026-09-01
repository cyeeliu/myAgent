"""Shared constants for the sessions package.

This module has no imports from sibling submodules, so it can be imported by
any of them without creating a circular dependency. The workspace setup side
effect (``code.set_workspace_dir``) runs exactly once at import time, matching
the original ``sessions.py`` module-level behavior.
"""
from __future__ import annotations

from agent_core import set_workspace_dir, REPO_ROOT, workspace_dir

# ── Workspace setup (side effect — must run once at import) ──
set_workspace_dir(REPO_ROOT / "workspace")

# ── Path constants ──
SESSION_FILES_ROOT = REPO_ROOT / "agent" / "sessions"
SESSION_STATE_ROOT = workspace_dir() / ".sessions"

# ── Timeouts ──
PERMISSION_TIMEOUT = 120.0

# ── Internal prompt markers (agent-internal nudges, not real user turns) ──
CONTINUATION_PROMPT = "Continue from the previous response. Do not repeat completed work."
TODO_REMINDER_PREFIX = "<reminder>"
TASK_NOTIFICATION_PREFIX = "<task_notification>"
