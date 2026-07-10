"""agent_core.env — extracted from code.py (s20 comprehensive agent)."""
from openai import OpenAI
from pathlib import Path
import os
import threading


try:
    import readline
    readline.parse_and_bind('set bind-tty-special-chars off')
    READLINE_AVAILABLE = True
except ImportError:
    READLINE_AVAILABLE = False

REPO_ROOT = Path.cwd()

# ── Workspace vs session dirs ──
# workspace_dir(): shared root, CWD for file ops/bash/MCP/subagents. Holds the
#   shared, cross-session state — .memory/ and skills/. In gateway mode this is
#   the mounted ~/.myAgent/workspace; in CLI mode it's REPO_ROOT (cwd at launch).
# session_dir(): per-session (threading.local) root for session-bound state —
#   .tasks/.transcripts/.task_outputs/.worktrees/.mailboxes/.scheduled_tasks.json.
#   Defaults to workspace_dir() when no session is bound (CLI).
# workdir(): alias for workspace_dir() — the CWD. Kept so the many call sites
#   that use workdir() as "where bash/file-ops run" keep working unchanged.
_WORKSPACE_ROOT = REPO_ROOT
_sess_local = threading.local()

def workspace_dir():
    """Shared workspace root. CWD for file ops/bash/MCP/subagents. Holds
    .memory/ and skills/ (shared across all sessions)."""
    return _WORKSPACE_ROOT

def set_workspace_dir(p):
    global _WORKSPACE_ROOT
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    for _sub in (".memory", "skills"):
        (p / _sub).mkdir(parents=True, exist_ok=True)
    _WORKSPACE_ROOT = p

def session_dir():
    """Per-session dir for session-bound state
    (.tasks/.transcripts/.task_outputs/.worktrees/.mailboxes/.scheduled_tasks.json).
    Defaults to workspace_dir() when no session is bound (CLI)."""
    return getattr(_sess_local, "session", None) or _WORKSPACE_ROOT

def set_session_dir(p):
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    for _sub in (".tasks", ".transcripts", ".task_outputs/tool-results",
                 ".worktrees", ".mailboxes"):
        (p / _sub).mkdir(parents=True, exist_ok=True)
    _sess_local.session = p

def workdir():
    """CWD for file ops / bash / MCP / subagents. Alias for workspace_dir()."""
    return workspace_dir()

def set_workdir(p):
    """Backward-compat entry point: bind the per-session dir to `p`. The shared
    workspace is set separately via set_workspace_dir() (gateway does this once
    at startup; CLI leaves it at the REPO_ROOT default)."""
    set_session_dir(p)

# CLI defaults: dot-dirs live under REPO_ROOT (= cwd at launch) until a session
# binds a separate workspace/session pair via set_workdir().
for _sub in (".tasks", ".transcripts", ".task_outputs/tool-results",
             ".worktrees", ".mailboxes", ".memory", "skills"):
    (REPO_ROOT / _sub).mkdir(parents=True, exist_ok=True)

client = OpenAI(
    base_url=os.getenv("OPENAI_BASE_URL"),
    api_key=os.getenv("OPENAI_API_KEY", "dummy"),
)

MODEL = os.environ["MODEL_ID"]

PRIMARY_MODEL = MODEL

FALLBACK_MODEL = os.getenv("FALLBACK_MODEL_ID")

def _transcript_dir():
    return session_dir() / ".transcripts"

def _tool_results_dir():
    return session_dir() / ".task_outputs" / "tool-results"

DEFAULT_MAX_TOKENS = 8000

ESCALATED_MAX_TOKENS = 16000

MAX_RETRIES = 3

# Rate-limit (429) retries: provider rate limits need longer backoff than a
# transient 529, so give 429 its own (generous) budget. 6 attempts with a 2s
# base and exponential backoff capped at 60s ≈ up to ~2 min of waiting before
# giving up, which absorbs normal rate-limit windows.
MAX_RETRIES_429 = 6
BASE_DELAY_429_MS = 2000
MAX_DELAY_429_MS = 60000

MAX_CONSECUTIVE_529 = 2

MAX_RECOVERY_RETRIES = 2

BASE_DELAY_MS = 500

CONTEXT_LIMIT = 50000

KEEP_RECENT_TOOL_RESULTS = 3

PERSIST_THRESHOLD = 30000

CONTINUATION_PROMPT = "Continue from the previous response. Do not repeat completed work."

PROMPT = "\033[36ms20 >> \033[0m"

CLI_ACTIVE = False

def terminal_print(text: str):
    if threading.current_thread() is threading.main_thread() or not CLI_ACTIVE:
        print(text)
        return
    line = ""
    if READLINE_AVAILABLE:
        try:
            line = readline.get_line_buffer()
        except Exception:
            line = ""
    print(f"\r\033[K{text}")
    print(PROMPT + line, end="", flush=True)
