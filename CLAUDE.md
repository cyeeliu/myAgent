# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A teaching agent project with three components orchestrated by docker-compose:

1. **`agent_core/` (package) + `code.py` (facade)** — the agent core. A Python re-implementation of Claude-Code-style machinery against an **OpenAI-compatible Chat Completions API**. It is "s20: Comprehensive Agent" — one loop wiring together dispatch, permission, hooks, todos, subagents, skills, compaction, memory, prompt assembly, error recovery, task graph, background tasks, cron, teams/protocols, worktrees, and MCP. The core is split into ~22 modules under `agent_core/` (one per subsystem); `code.py` is now a thin backward-compat facade that re-exports the public API so `import code` (gateway, tests) and `python code.py` (CLI) keep working. The split was produced verbatim by `_split.py` (AST carve + auto cross-module import resolution).
2. **`agent_gateway/`** — FastAPI gateway that wraps `code.py` for the web. WS/SSE event streams, REST control endpoints, Postgres durability, Redis hot event pipe. Multi-replica-ready.
3. **`frontend/`** — Next.js chat UI (app router, Tailwind, WS/SSE transport abstraction in `lib/transports/`).

The wire format is OpenAI, but the rest of the agent speaks **Anthropic-style content blocks** (`text` / `tool_use` / `tool_result`). `agent_core/adapter.py` (`_to_openai_messages`, `_to_openai_tools`, `chat_create`) is the only place that knows the OpenAI format — swapping providers touches only that module.

## Commands

```bash
# Full stack (nginx + gateway + frontend + postgres + redis):
MODEL_ID=glm-5 OPENAI_API_KEY=sk-... docker compose up --build
# nginx on :80 is the only public entry: proxies /api/* → gateway:8000
# (with WebSocket upgrade) and everything else → frontend:3000.
# gateway and frontend bind 127.0.0.1 on the host (local debug only, not public).

# Core only (local dev, no DB/Redis — degrades to in-memory):
cp .env.example .env          # then fill in API key + MODEL_ID
source .venv/bin/activate     # venv exists; see requirements.txt
python code.py                # interactive REPL; type a question, Enter to send, q to quit

# Gateway only (local, no docker):
uvicorn agent_gateway.main:app --host 0.0.0.0 --port 8000
```

`requirements.txt` pins `openai>=1.0.0`, `python-dotenv`, `pyyaml`, `fastapi`, `uvicorn`, `websockets`, `psycopg[binary,pool]>=3.1`, `redis>=5.0`. `code.py` imports the `openai` SDK as a generic OpenAI-compatible client.

**Env vars:** `code.py` and `.env.example` agree on `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `MODEL_ID` (required), `FALLBACK_MODEL_ID`. The gateway additionally reads `DATABASE_URL` (Postgres) and `REDIS_URL` (hot event pipe); both optional — unset degrades to in-memory with no crash. The frontend build arg `NEXT_PUBLIC_GATEWAY_URL` is **optional** — unset, it falls back to `window.location.origin` (same-origin, correct behind the nginx proxy); set it only if the frontend is served on a different origin than the API.

There are no lint or build steps for the agent core. `tests/` has pytest integration tests (core event flow + gateway); run with `MODEL_ID=test-model OPENAI_API_KEY=dummy python -m pytest tests/ -q`. The frontend has vitest + playwright (`frontend/`).

## Architecture (read multiple files to understand)

### `agent_core/` — the agent core (modularized from the former single-file `code.py`)

`code.py` is now a re-export facade; the logic lives in `agent_core/` with one module per subsystem. `__init__.py` imports them in dependency order and exposes `__all__`. Two circular edges (`tools↔teammates`, `tools↔subagent`, `tools↔mcp`, `hooks↔tools`) are broken by **deferred in-function imports**. `chat_create` is reached via `from agent_core import adapter; adapter.chat_create(...)` in every call site (loop, subagent, teammates, compaction, memory) so test monkeypatch of `agent_core.adapter.chat_create` propagates everywhere (matches the old single-global semantics). Module layout (`__init__.py` import order):

```
env → blocks → adapter → session → skills → tasks → worktrees → bus → hooks →
recovery → compaction → background → subagent → teammates → mcp → memory →
prompt → cron → tools → context → loop → cli
```

Key flow (module: symbol):

- **`loop.py: agent_loop`** — the heart. Each iteration: inject due cron jobs + background-task notifications, nudge todos every 3 rounds, run `prepare_context` (context budgeting), rebuild the tool pool, call the LLM via `call_llm`, then execute each `tool_use` block: `compact` short-circuits, `PreToolUse` hooks can block, slow ops fork to background, otherwise the handler runs and `PostToolUse` fires. Stops when the response has no tool_use.
- **`session.py: Session`** dataclass — carries two parallel message lists (see *Chat record vs LLM context* below): `record` (append-only) and `context_messages` (compactable). Plus `context: dict` (side-state), `_seq`, `sinks`, `record_sinks`, `lock`, `workdir`, `mcp_clients`. `append_both(msg)` appends to both lists and fans out to `record_sinks`.
- **`context.py: prepare_context`** — applied every turn in order: `tool_result_budget` (cap total tool-result bytes, persist oversized outputs to `.task_outputs/`), `snip_compact` (drop middle messages past a count), `micro_compact` (drop trivial results), then `compact_history` if still over `CONTEXT_LIMIT` (50k). `reactive_compact` is a last-resort on prompt-too-long errors. **All compaction mutates `session.context_messages` only — never `session.record`.**
- **`tools.py`** — `BUILTIN_TOOLS` (schemas) and `BUILTIN_HANDLERS` (Python fns) are kept as parallel explicit tables; adding a capability means editing both. `mcp.py: assemble_tool_pool` merges builtins with connected MCP tools (prefixed `mcp__{server}__{tool__}`).
- **`skills.py`** — `skills/<name>/SKILL.md` with YAML frontmatter (`name`, `description`) + markdown body. `scan_skills` builds the catalog injected into the system prompt each turn; `load_skill` returns full body on demand. Skills are prompts, not code.
- **`prompt.py: assemble_system_prompt`** — rebuilt every turn from live context: identity, tool list, working dir, current time, skill catalog, relevant memories (from `.memory/MEMORY.md`), connected MCP servers.
- **`memory.py`** — persistent cross-session knowledge, mirroring `s09_memory`. Each memory is one Markdown file under `.memory/` with YAML frontmatter (`name`/`description`/`type`); `MEMORY.md` is a one-line-per-memory index rebuilt from those files. Three operations compose per user turn: `load_memories` (LLM-selects relevant files, content injected via `context["memories"]` once at `agent_loop` start), `extract_memories` (after the turn ends, pulls user/feedback/project/reference facts from `session.record` and writes files), `consolidate_memories` (merges/dedupes when file count ≥ 10). All LLM calls go through `_memory_llm` (primary → `FALLBACK_MODEL`, never raises); memory failures never break the loop. Per-session under `workdir()/.memory/`.
- **Subsystems writing to dot-dirs in `WORKDIR` (= cwd at launch):** `.tasks/` (task graph), `.worktrees/` (git worktrees), `.mailboxes/` (teammate messaging), `.scheduled_tasks.json` (durable cron), `.transcripts/`, `.task_outputs/`, `.memory/`. These are runtime state, not source.
- **`teammates.py`** — `spawn_teammate_thread` runs a background thread with its own message history sharing the lead's tools; communication via `MessageBus` + `ProtocolState` (plan approval / shutdown handshake). `loop.py: cron_autorun_loop` is a daemon thread that runs `agent_loop` on scheduled prompts when the REPL is idle.
- **`hooks.py`** — `register_hook` / `trigger_hooks` for `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`. `permission_hook` enforces a deny list (`rm -rf /`, `sudo`, …) and prompts for destructive ops.
- **`adapter.py: _to_openai_messages`** — adapter ends with a two-pass tool/tool_call consistency repair: drop orphan `tool` messages whose `tool_call_id` isn't declared by any assistant, then drop assistant `tool_calls` that have no answering `tool` message. Backstop for `snip_compact` orphaning a `tool_result` inside the compacted context (root cause of recurring ModelArts.81001 400s).

### Chat record vs LLM context (the split)

`Session` keeps **two** message lists because one list serving both roles corrupts the durable record when compaction mutates it in place:

- **`record`** — append-only, **never compacted**. The durable conversation. Source of truth for replay, title, history_len, Postgres `chat_record`.
- **`context_messages`** — what the LLM actually sees. Compaction (`snip_compact` / `micro_compact` / `compact_history` / `reactive_compact`) rebinds this list (`messages[:] = ...`); `record` is untouched.
- **`append_both(msg)`** — every `messages.append` site in `agent_loop` (user msg, cron inject, todo reminder, error, max_tokens continuation, assistant turn, tool result, explicit compact marker) goes through this so both lists stay in sync at append time. Compaction then diverges them on purpose.

### `agent_gateway/` — FastAPI gateway

- **`main.py`** — FastAPI app + lifespan. Routes: `POST /api/sessions`, `WS /api/sessions/{id}` (with `?last_seq=N` resume), `GET /api/sessions/{id}/status`, `POST /api/sessions/{id}/messages` (REST/SSE input), `POST .../permissions/{rid}/respond`, `POST .../interrupt`, `GET /api/sessions` (sidebar list), `DELETE /api/sessions/{id}`, plus read-only `/api/skills` `/api/mcp`. SSE routes registered by `sse.py`. 30-min idle RAM eviction (DB row kept).
- **`sessions.py`** — `SessionManager` + `GatewaySession`. One `code.Session` per chat session, owned by a worker thread that runs `agent_loop` per posted message. `post_message` → `append_both(user_msg)` + persist chat record → spawn worker. `_run_turn` finally persists chat record + llm context + ctx snapshot + clears `_worker`. `synthesize_frames(record)` rebuilds token-level replay frames from the append-only record (so reconnect replay shows the FULL conversation, not compacted residue). `_build` hydrates from Postgres on demand.
- **`pipe.py`** — three pipe interfaces, each with Redis + in-memory impls:
  - `EventPipe` (`live:{sid}` Stream, key `stream:{sid}`) — token-level events for WS/SSE live push + replay. 24h TTL.
  - `ChatStreamPipe` (`chat:{sid}` Stream) — append-only message-level chat record. `ChatRecordSink` fans every `append_both` here.
  - `ContextStore` (`ctx:{sid}` Hash) — compacted LLM context snapshot, overwritten each turn.
- **`db.py`** — psycopg3 + `ConnectionPool`. `sessions` table has `chat_record JSONB` + `llm_context JSONB` (split columns, with a `DO $$` migration that renames old `history`→`chat_record` and backfills `llm_context = chat_record`). `_normalize()` converts `SimpleNamespace` content blocks to plain dicts for Jsonb. Degrades to no-op when `DATABASE_URL` unset.
- **`ws.py` / `sse.py`** — transport pumps. Drain `EventPipe` (replay since `last_seq`, then live) and push frames to the client.
- **`schemas.py`** — pydantic request models.

**Three-tier replay:** `live:{sid}` (token-level, 24h) → expired → synthesize from `chat:{sid}` (message-level record) → expired → re-seed from Postgres `chat_record`. The LLM never reads Redis; it reads `session.context_messages`. Redis is purely for replay/hot-restore; Postgres is the durable source of truth.

### `frontend/`

Next.js app router. `components/ChatPanel.tsx` renders the event stream; `lib/transports/` abstracts WS vs SSE; `lib/reducer.ts` is the event→UI state reducer (unit-tested in `reducer.test.ts`). `last_seq` resume semantics match the gateway. The gateway URL (`lib/sessions.ts`, `lib/useAgentTransport.ts`, `components/Sidebar.tsx`) defaults to `window.location.origin` so the same build works behind any nginx host without re-baking an IP/domain.

### `nginx.conf` — reverse proxy

Single public entry on `:80`. `location /api/` → `gateway:8000` with `Upgrade`/`Connection` headers, `proxy_buffering off`, 1h read/send timeouts for long-lived WS/SSE streams; `location /` → `frontend:3000`. The `map $http_upgrade $connection_upgrade` block is required so non-WS requests keep normal keepalive. Add a `listen 443` server block + `ssl_certificate` here when promoting to HTTPS.

## Working in this codebase

- The agent runs with `WORKDIR = Path.cwd()`, so launch it from the directory you want it to operate in (typically the repo root); all state dot-dirs are created there. In docker, the gateway container's WORKDIR is `/app`; per-session workdirs are `workspace/<sid>/`.
- When adding a tool, update both `BUILTIN_TOOLS` (schema) and `BUILTIN_HANDLERS` (handler) in `agent_core/tools.py` — they are intentionally not auto-derived.
- Provider swaps belong only in `agent_core/adapter.py`; everything downstream consumes Anthropic-style blocks.
- The `agent_core/` split was generated by `_split.py` (AST carve + auto cross-module import resolution). To re-split after large edits to the facade, restore `code.py` from git first — `_split.py` reads `code.py` and would overwrite hand-edited modules. Circular edges are broken by deferred in-function imports; keep them deferred. `chat_create` must stay reached via `adapter.chat_create(...)` (not a direct `from agent_core.adapter import chat_create`) so test monkeypatch propagates.
- When appending a new message kind to `agent_loop`, use `session.append_both(msg)` so the chat record stays complete. Compaction sites (`messages[:] = ...`) must mutate `context_messages` only — never `record`.
- `.env` contains live secrets and is gitignored; `.env.example` is the template to track. Reconcile the two when editing env vars.
- The chat-record/llm-context split is load-bearing: do not collapse `record` and `context_messages` back into one list. Compacting the shared list was the root cause of recurring 400s and lost replay.
