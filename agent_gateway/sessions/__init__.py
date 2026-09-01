"""Session management package — one agent core Session per chat session.

Public API (re-exported for backward compatibility with the former
``agent_gateway.sessions`` module):

  - ``manager``              — the SessionManager singleton
  - ``SessionManager``       — session registry + DB hydration
  - ``GatewaySession``       — live session wrapper (worker thread, pipes, perms)
  - ``PipeSink`` / ``ChatRecordSink`` — event sinks
  - ``synthesize_frames``    — replay frame synthesis from chat record
  - ``cleanup_session_artifacts`` — on-disk + Redis cleanup
  - ``_write_session_files`` — transcript.md + history.json writer

Internal modules:
  - ``_constants``    — shared constants + workspace setup side effect
  - ``files``         — session file writing + stringification
  - ``replay``        — frame synthesis + todo reconstruction
  - ``cleanup``       — artifact cleanup
  - ``gateway_session`` — GatewaySession dataclass + sinks
  - ``manager``       — SessionManager + singleton
"""
from __future__ import annotations

# Constants (also triggers workspace setup side effect)
from ._constants import (
    SESSION_FILES_ROOT,
    SESSION_STATE_ROOT,
    PERMISSION_TIMEOUT,
    CONTINUATION_PROMPT,
    TODO_REMINDER_PREFIX,
    TASK_NOTIFICATION_PREFIX,
)

# File writing
from .files import (
    _write_session_files,
    _stringify,
    _is_internal_user_prompt,
)

# Replay
from .replay import (
    synthesize_frames,
    _last_todos_from_record,
)

# Cleanup
from .cleanup import cleanup_session_artifacts

# Gateway session + sinks
from .gateway_session import (
    GatewaySession,
    PipeSink,
    ChatRecordSink,
)

# Manager + singleton
from .manager import (
    SessionManager,
    manager,
)

__all__ = [
    "manager",
    "SessionManager",
    "GatewaySession",
    "PipeSink",
    "ChatRecordSink",
    "synthesize_frames",
    "cleanup_session_artifacts",
    "_write_session_files",
    "SESSION_FILES_ROOT",
    "SESSION_STATE_ROOT",
    "PERMISSION_TIMEOUT",
]
