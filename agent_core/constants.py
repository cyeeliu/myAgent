"""agent_core.constants — tuning knobs and magic numbers.

Single source of truth for retry budgets, context limits, compaction thresholds,
and token limits. All values are read from env vars with sensible defaults so
operators can tune without code changes.
"""
from __future__ import annotations

import os

# ── Token limits ──
DEFAULT_MAX_TOKENS = 8000
ESCALATED_MAX_TOKENS = 16000

# ── Retry budgets ──
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

# ── Context budget ──
# The agent's context budget in TOKENS — the single source of truth for BOTH
# the ToolPanel context-usage stat denominator AND the auto-compact trigger
# (prepare_context compacts when the message context reaches this many tokens).
# One knob so the stat's "100%" coincides with when compaction fires. Default
# 128000 (GLM-5 / GPT-4-class); set via the AUTO_COMPACT_WINDOW env var.
AUTO_COMPACT_WINDOW = int(os.environ.get("AUTO_COMPACT_WINDOW", "128000"))

# ── Compaction thresholds ──
KEEP_RECENT_TOOL_RESULTS = 3
PERSIST_THRESHOLD = 30000

# ── Prompts ──
CONTINUATION_PROMPT = "Continue from the previous response. Do not repeat completed work."
