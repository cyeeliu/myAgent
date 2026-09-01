"""agent_core.env — environment, config, and runtime globals.

**Composition module** — re-exports from focused sub-modules so the many
``from agent_core.env import …`` call sites across agent_core keep working
after the split:

  - ``paths``       — REPO_ROOT, workspace_dir, session_dir, workdir, …
  - ``constants``   — DEFAULT_MAX_TOKENS, MAX_RETRIES, AUTO_COMPACT_WINDOW, …
  - ``terminal``    — READLINE_AVAILABLE, PROMPT, CLI_ACTIVE, terminal_print

The OpenAI client + model-id globals (``client``, ``MODEL``, ``PRIMARY_MODEL``,
``FALLBACK_MODEL``) are defined here because they are import-time env-var reads
that serve as the fallback source for ``model_config.py`` (which hot-swaps them
at runtime from ``.agents/model.json``).
"""
from __future__ import annotations

import os

from openai import OpenAI

# ── Paths (workspace / session dir management) ──
from .paths import (
    REPO_ROOT,
    _WORKSPACE_ROOT,
    _sess_local,
    workspace_dir,
    set_workspace_dir,
    session_dir,
    set_session_dir,
    workdir,
    set_workdir,
    _transcript_dir,
    _tool_results_dir,
)

# ── Constants (tuning knobs) ──
from .constants import (
    DEFAULT_MAX_TOKENS,
    ESCALATED_MAX_TOKENS,
    MAX_RETRIES,
    MAX_RETRIES_429,
    BASE_DELAY_429_MS,
    MAX_DELAY_429_MS,
    MAX_CONSECUTIVE_529,
    MAX_RECOVERY_RETRIES,
    BASE_DELAY_MS,
    AUTO_COMPACT_WINDOW,
    KEEP_RECENT_TOOL_RESULTS,
    PERSIST_THRESHOLD,
    CONTINUATION_PROMPT,
)

# ── Terminal (CLI output + readline) ──
from .terminal import (
    READLINE_AVAILABLE,
    PROMPT,
    CLI_ACTIVE,
    terminal_print,
)

# ── Config: OpenAI client + model-id globals ──
# These are import-time env-var reads. model_config.py hot-swaps them at runtime
# from .agents/model.json; env remains the fallback source.
client = OpenAI(
    base_url=os.getenv("OPENAI_BASE_URL"),
    api_key=os.getenv("OPENAI_API_KEY", "dummy"),
)

MODEL = os.environ["MODEL_ID"]
PRIMARY_MODEL = MODEL
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL_ID")
