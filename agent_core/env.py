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

_wd_local = threading.local()

def workdir():
    """Per-thread working directory. Defaults to REPO_ROOT; a session's worker
    thread overrides via set_workdir() so each session's .tasks/.memory/
    .transcripts/… and file-tool ops live under workspace/<sid>/."""
    return getattr(_wd_local, "workdir", REPO_ROOT)

def set_workdir(p):
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    for _sub in (".tasks", ".transcripts", ".task_outputs/tool-results",
                 ".worktrees", ".mailboxes", ".memory"):
        (p / _sub).mkdir(parents=True, exist_ok=True)
    _wd_local.workdir = p

for _sub in (".tasks", ".transcripts", ".task_outputs/tool-results",
             ".worktrees", ".mailboxes", ".memory"):
    (REPO_ROOT / _sub).mkdir(parents=True, exist_ok=True)

client = OpenAI(
    base_url=os.getenv("OPENAI_BASE_URL"),
    api_key=os.getenv("OPENAI_API_KEY", "dummy"),
)

MODEL = os.environ["MODEL_ID"]

PRIMARY_MODEL = MODEL

FALLBACK_MODEL = os.getenv("FALLBACK_MODEL_ID")

def _transcript_dir():
    return workdir() / ".transcripts"

def _tool_results_dir():
    return workdir() / ".task_outputs" / "tool-results"

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
