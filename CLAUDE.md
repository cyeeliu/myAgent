# CLAUDE.md

This file provides guidance when working with code in this repository.

## What this is

An agent project with three components orchestrated by docker-compose:

1. **`agent_core/` (package) + `code.py` (facade)** — the agent core. A Python re-implementation of a modern coding-agent loop against an **OpenAI-compatible Chat Completions API**. It is "s20: Comprehensive Agent" — one loop wiring together dispatch, permission, hooks, todos, subagents, skills, compaction, memory, prompt assembly, error recovery, task graph, background tasks, cron, teams/protocols, worktrees, and MCP. The core is split into ~22 modules under `agent_core/` (one per subsystem); `code.py` is now a thin backward-compat facade that re-exports the public API so `import code` (gateway, tests) and `python code.py` (CLI) keep working. The split was produced verbatim by `_split.py` (AST carve + auto cross-module import resolution).
2. **`agent_gateway/`** — FastAPI gateway that wraps `code.py` for the web. Method-routed WebSocket at `/ws` (jiuwenswarm-style `req`/`res`/`event` envelopes), SSE event streams, REST control endpoints, Postgres durability, Redis hot event pipe. Multi-replica-ready.
3. **`frontend_vite/`** — vendored jiuwenswarm Vite chat UI (React + Tailwind, served as static dist by nginx). Feature-flagged to the panels myAgent has a backend for (chat/sessions/skills/agents/config/tools); teams/cron/channels/etc. are hidden. The legacy Next.js `frontend/` is kept as a fallback but not deployed.

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
env → blocks → adapter → session → skills → tasks → worktrees → bus → permissions → hooks →
recovery → compaction → background → subagent → teammates → mcp → memory →
prompt → cron → tools → context → loop → cli
```

Key flow (module: symbol):

- **`loop.py: agent_loop`** — the heart. Each iteration: inject due cron jobs + background-task notifications, nudge todos every 3 rounds, run `prepare_context` (context budgeting), rebuild the tool pool, call the LLM via `call_llm`, then execute each `tool_use` block: `compact` short-circuits, `PreToolUse` hooks can block, slow ops fork to background, otherwise the handler runs and `PostToolUse` fires. Stops when the response has no tool_use.
- **`session.py: Session`** dataclass — carries two parallel message lists (see *Chat record vs LLM context* below): `record` (append-only) and `context_messages` (compactable). Plus `context: dict` (side-state), `_seq`, `sinks`, `record_sinks`, `lock`, `workdir`, `mcp_clients`. `append_both(msg)` appends to both lists and fans out to `record_sinks`.
- **`context.py: prepare_context`** — applied every turn in order: `tool_result_budget` (cap total tool-result bytes, persist oversized outputs to `.task_outputs/`), `snip_compact` (drop middle messages past a count), `micro_compact` (drop trivial results), then `compact_history` if still over `CONTEXT_LIMIT` (50k). `reactive_compact` is a last-resort on prompt-too-long errors. **All compaction mutates `session.context_messages` only — never `session.record`.**
- **`tools.py`** — `BUILTIN_TOOLS` (schemas) and `BUILTIN_HANDLERS` (Python fns) are kept as parallel explicit tables; adding a capability means editing both. `mcp.py: assemble_tool_pool` merges builtins with connected MCP tools (prefixed `mcp__{server}__{tool__}`).
- **`skills.py`** — `skills/<name>/SKILL.md` with YAML frontmatter (`name`, `description`) + markdown body. `scan_skills` builds the catalog injected into the system prompt each turn; `load_skill` returns full body on demand. Skills are prompts, not code. **Per-workspace:** `_skills_dir() = workspace_dir() / "skills"` (shared across sessions, not per-session). The gateway seeds presets from `/app/skills/*` into the workspace on `rebuild-agent-data` (copy-if-missing, preserves user edits).
- **`prompt.py: assemble_system_prompt`** — rebuilt every turn from live context: identity, tool list, working dir, current time, skill catalog, relevant memories (from `.memory/MEMORY.md`), connected MCP servers.
- **`memory.py`** — persistent cross-session knowledge, mirroring `s09_memory`. Each memory is one Markdown file under `.memory/` with YAML frontmatter (`name`/`description`/`type`); `MEMORY.md` is a one-line-per-memory index rebuilt from those files. Three operations compose per user turn: `load_memories` (LLM-selects relevant files, content injected via `context["memories"]` once at `agent_loop` start), `extract_memories` (after the turn ends, pulls user/feedback/project/reference facts from `session.record` and writes files), `consolidate_memories` (merges/dedupes when file count ≥ 10). All LLM calls go through `_memory_llm` (primary → `FALLBACK_MODEL`, never raises); memory failures never break the loop. **Shared across sessions** under `workspace_dir()/.memory/` (NOT per-session). agent_core reads/writes `.md` files + `MEMORY.md` only — there is no `config.json`; do not seed one.
- **Workspace vs session dirs (`env.py`)** — two distinct roots split by lifecycle:
  - **`workspace_dir()`** — shared workspace root, CWD for file ops / bash / MCP / subagents / git. Holds cross-session state: `.memory/` and `skills/`. Global (module-level `_WORKSPACE_ROOT`), set once via `set_workspace_dir()`; defaults to `REPO_ROOT` in CLI, `REPO_ROOT/workspace` in the gateway (= the mounted `~/.myAgent/workspace`).
  - **`session_dir()`** — per-session (threading.local) root for session-bound state: `.tasks/`, `.transcripts/`, `.task_outputs/`, `.worktrees/`, `.mailboxes/`, `.scheduled_tasks.json`. Defaults to `workspace_dir()` when no session is bound (CLI). In the gateway: `workspace/.sessions/<sid>/` — a hidden dot-dir inside the mount so it persists across container restarts (durable cron, worktrees) yet stays out of the AgentPanel file browser (which skips dot-dirs other than `.memory`).
  - **`workdir()`** is an alias for `workspace_dir()` (CWD). `set_workdir(p)` binds the session dir (backward-compat). The memory background thread in `loop.py` captures/restores `session_dir()` (threading.local doesn't inherit to child threads); memory itself writes to the global `workspace_dir()`, which needs no capture.
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
- **`sessions.py`** — `SessionManager` + `GatewaySession`. One `code.Session` per chat session, owned by a worker thread that runs `agent_loop` per posted message. `post_message` → `append_both(user_msg)` + persist chat record → spawn worker. `_run_turn` finally persists chat record + llm context + ctx snapshot + clears `_worker`. `synthesize_frames(record)` rebuilds token-level replay frames from the append-only record (so reconnect replay shows the FULL conversation, not compacted residue). `_build` hydrates from Postgres on demand. At import: `code.set_workspace_dir(REPO_ROOT/workspace)`; per-session `agent.workdir = SESSION_STATE_ROOT/<sid>` (= `workspace/.sessions/<sid>`), bound via `set_workdir` in `_run_turn`. `_write_session_files(sid, record)` writes `transcript.md` + `history.json` to `SESSION_FILES_ROOT/<sid>` (= `/app/agent/sessions/<sid>`, a named volume) in the jiuwenswarm preview-parser shape (`{role, content, timestamp}` for user, `{role:"assistant", event_type:"chat.final", content, timestamp}` for assistant) so the SessionsPanel preview renders both sides.
- **`pipe.py`** — three pipe interfaces, each with Redis + in-memory impls:
  - `EventPipe` (`live:{sid}` Stream, key `stream:{sid}`) — token-level events for WS/SSE live push + replay. 24h TTL.
  - `ChatStreamPipe` (`chat:{sid}` Stream) — append-only message-level chat record. `ChatRecordSink` fans every `append_both` here.
  - `ContextStore` (`ctx:{sid}` Hash) — compacted LLM context snapshot, overwritten each turn.
- **`db.py`** — psycopg3 + `ConnectionPool`. `sessions` table has `chat_record JSONB` + `llm_context JSONB` (split columns, with a `DO $$` migration that renames old `history`→`chat_record` and backfills `llm_context = chat_record`). `_normalize()` converts `SimpleNamespace` content blocks to plain dicts for Jsonb. Degrades to no-op when `DATABASE_URL` unset.
- **`ws.py` / `sse.py`** — transport pumps. Drain `EventPipe` (replay since `last_seq`, then live) and push frames to the client.
- **`channel_manager/web/` + `common/e2a/`** — the jiuwenswarm-style method-routed WS at `/ws`. Wire shapes: `req {type:"req", id, method, params}` / `res {type:"res", id, ok, payload, error?}` / `event {type:"event", event, payload, seq?}`. `web_connect.py` sends `connection.ack` on accept (the frontend won't fire `config.get`/`models.list`/`session.list` until it sees it), binds the event drain on the first `chat.send`/`session.create` carrying a `session_id`. `agent_compat.py` dispatches ~30 `ReqMethod`s in-process; unsupported methods the frontend calls (`config.save_all`, `media.persist`, `updater.check`, …) return `{ok:true, payload:{}}` stubs so kept panels don't throw.
- **`gateway_push/wire.py`** — maps agent_core event kinds → jiuwenswarm dotted events: `token→chat.delta{content}`, `tool_start→chat.tool_call{id,name,arguments}`, `tool_result→chat.tool_result{tool_call_id,tool_name,result,success}`, `done→chat.final{content}`, `user→chat.user`, `error→chat.error`. **`done` is emitted as `{}` (empty payload), so `chat.final.content` is `""` — the streamed text lives in the `chat.delta` frames; the frontend accumulates deltas and treats `chat.final` as the end-of-turn marker (it sets `isProcessing=false` regardless of content).
- **`file-api/*` (in `main.py`)** — REST file browser the jiuwenswarm Sessions/Agent panels use. `GET /file-api/list-files?dir=` and `GET /file-api/file-content?path=&encoding=` (encoding=`auto` sniffed via `_decode_auto`: utf-8→gbk→gb2312→big5→shift_jis→euc_kr→latin-1→utf-8 replace; never 500s). `POST /file-api/rebuild-agent-data` walks the real mounted workspace (`/app/workspace`) and writes `agent-data.json` (`Record<folder_key, FileInfo[]>`) for the AgentPanel, seeding per-workspace `skills/` (copy-if-missing from `/app/skills/*`) and `.memory/` along the way. `_resolve_under_root` rewrites the `agent/workspace/...` frontend prefix to the real workspace and confines path traversal; `agent/sessions/<sid>/` resolves to `SESSION_FILES_ROOT`.
- **`schemas.py`** — pydantic request models.

**Three-tier replay:** `live:{sid}` (token-level, 24h) → expired → synthesize from `chat:{sid}` (message-level record) → expired → re-seed from Postgres `chat_record`. The LLM never reads Redis; it reads `session.context_messages`. Redis is purely for replay/hot-restore; Postgres is the durable source of truth.

### `frontend_vite/` — vendored jiuwenswarm UI

React + Vite + Tailwind, built to static dist and served by nginx. `src/hooks/useWebSocket.ts` is the event→store reducer (handles `connection.ack`, `chat.delta`/`chat.tool_call`/`chat.tool_result`/`chat.final`, …); `src/stores/` hold session/chat state. `src/components/AgentPanel/` browses workspace files via `/file-api/rebuild-agent-data` + `agent-data.json`; `src/features/historyRestore.ts` parses `history.json` for the SessionsPanel preview (`parseHistoryTimelineEntry` requires assistant records to carry `event_type` in `ALLOWED_ASSISTANT_EVENT_TYPES` and string `content`, else they're dropped — that's why `_write_session_files` normalizes the shape). `src/featureFlags.ts` hides panels with no backend. The legacy Next.js `frontend/` is kept but not deployed.

### `nginx.conf` — reverse proxy

Single public entry on `:80`. `location /api/` → `gateway:8000` with `Upgrade`/`Connection` headers, `proxy_buffering off`, 1h read/send timeouts for long-lived WS/SSE streams; `location /` → `frontend:3000`. The `map $http_upgrade $connection_upgrade` block is required so non-WS requests keep normal keepalive. Add a `listen 443` server block + `ssl_certificate` here when promoting to HTTPS.

## Permission system

Per-tool authorization, a memory-content filter, and the security-config panel all hang off one policy store and one gate.

- **Policy store** — `agent_core/permissions.py`, persisted at `workspace_dir()/.permissions/policy.json` (shared across sessions, like `.memory/` and `skills/`). Shape: `{default, ask_on_overwrite, enabled, memory_forbidden:{enabled, pattern}, tools:{name: "allow"|"ask"|"deny"}}`. Seed: `bash`/`write_file`/`edit_file` = `"ask"`, `default` = `"allow"`, `enabled` = `True`. API: `get_policy`, `decide`, `set_tool_level`, `delete_tool`, `ask_on_overwrite`, `is_enabled`/`set_enabled`, `get_memory_forbidden`/`set_memory_forbidden`, `matches_forbidden`.
- **The gate** — `check_permission` (`agent_core/hooks.py`) is the **only** permission check, called once per tool_use in `agent_loop` (`loop.py`). Order: policy gate (`is_enabled()` → `decide(name)` → `"deny"` hard-deny / `"ask"` surface a `permission_request`) **then** the hardcoded safety backstop (bash `DENY_LIST`/`DESTRUCTIVE`, `safe_path` escape, `ask_on_overwrite` overwrite prompt, mcp `deploy` ask). The backstop always runs — an `"allow"` policy never bypasses deny-list bash or path escape.
- **`_ask` request_id round-trip (load-bearing)** — `hooks._ask` generates `request_id = uuid().hex[:12]`, registers the answer future **first** via `permission.resolver(block, request_id)`, emits `permission_request` **with** `request_id`, then blocks. The `request_id` must round-trip so `gs.grant(rid)` finds the pending future. Do not use `permission.request(block)` here — `FuturePermission`'s internally-generated id is never sent to the client, so `gs.grant` finds nothing and the future never resolves (the "Allow click does nothing" bug).
- **Memory filter** — `memory.select_relevant_memories` and `extract_memories` skip any memory whose `name + description + body` matches `permissions.matches_forbidden(...)`. `matches_forbidden` compiles the regex into a module-level cache keyed by pattern string; an invalid regex is cached as `None` and returns `False` — it never raises (memory filtering must not break the loop).
- **WS endpoints + panel** — `permissions.tools.get/update/delete` (schema in `agent_gateway/common/schema/message.py`, dispatched in `agent_compat.py`) drive the frontend `ConfigPanel/PermissionsToolsEditor`. The security-config tab mounts via the `"permissions"` group's `afterTable`, which requires `_config_get` to return `permissions_enabled` / `memory_forbidden_enabled` / `memory_forbidden_description` — if those keys are missing the tab renders empty. `_config_set` persists them back through `permissions.set_enabled` / `set_memory_forbidden`. The `permission_request` event is mapped in `gateway_push/wire.py` to `chat.ask_user_question` with explicit `options: [Allow, Deny]` (the `UserQuestionModal` renders `question.options.map`, so omitting `options` crashes the frontend). Answers return via `chat.send{source:"permission_interrupt", request_id, answers}` → `gs.grant`.

## Plan mode (`agent.plan`)

Plan mode: read-only exploration → submit plan → user approves → exit plan mode → execute. The default mode in the frontend selector.

- **Flag** — `agent_compat.py` `chat.send` sets `gs.agent.context["plan_mode"] = True` when `req.mode == Mode.PLAN`, else pops it. The flag must survive across turns; see `update_context` merge below.
- **Tool restriction** — `mcp.assemble_tool_pool(context)` filters to `_PLAN_MODE_ALLOWED` (read-only tools + `exit_plan_mode`) when `context.get("plan_mode")`. `ask_user` and every mutating/orchestration tool (`write_file`/`edit_file`/worktree mutate/cron create/teammates/task graph writes/`task`/`connect_mcp`/team protocols) are hidden, and **all MCP tools are hidden** (can't tell which are read-only). `bash` is kept for exploration (`ls`/`git status`/`cat`) — the bash read-only gate below enforces it. `loop.py` passes `context` to `assemble_tool_pool` at both call sites.
- **Approval gate** — `exit_plan_mode` builtin (`tools.py`: schema in `BUILTIN_TOOLS`, `run_exit_plan_mode` in `BUILTIN_HANDLERS`) reuses the `ask_user` event pipe (`_ask_user_via_gateway` shared helper) to pop a UserQuestionModal with options `批准并执行` / `拒绝`. **No wire.py or frontend changes** — same event kind, same `ask_resolver`, same `chat.send{source:ask_user_interrupt}` answer path. On approval it pops `plan_mode` from context; the same turn's next loop iteration calls `assemble_tool_pool(context)` with no `plan_mode` → full tools restored, so the agent executes immediately without waiting for another user message. On rejection it stays in plan mode.
- **bash read-only gate** — `check_permission` denies mutating bash commands in plan mode via `_PLAN_MODE_BASH_DENY` (`rm`/`mv`/`cp`/`mkdir`/`chmod`/redirects/`git add|commit|reset|checkout|…`/`npm install`/`pip install`/`docker`/`kill`/`sed -i`/…). Substring match, best-effort, denies (doesn't ask) — matches plan-mode read-only semantics.
- **Persistence** — `_build` (`sessions.py`) takes a `mode` param and sets `agent.context["plan_mode"]` when `mode == "agent.plan"`, so a session rehydrated after idle eviction / replica crash keeps its read-only restriction. `get_or_hydrate` and `create` pass `row.get("mode")`. `_run_turn` captures `plan_at_start` at turn begin and, in `finally`, if `plan_mode` transitioned True→False mid-turn (approval) calls `db.save_session_mode(sid, "agent.fast")` — so reconnect after approval resumes in executing state, not plan mode. `run_exit_plan_mode` writes the plan to `session_dir()/"plan.md"` for audit / reconnect-mid-approval recovery.
- **`team_mode`** uses the same context-flag pattern (`chat.send` sets `gs.agent.context["team_mode"]`); the `update_context` merge keeps both alive across turns.

## Working in this codebase

- **`update_context` must merge, not rebuild (load-bearing):** `context.update_context` does `out = dict(context); out[...] = ...` — it preserves caller-set flags (`plan_mode`, `team_mode`, …) across turns. Do not change it to return a fresh dict. The previous fresh-3-key-dict form dropped `plan_mode`/`team_mode` every turn; `team_mode` survived only because it acts on turn 1, but `plan_mode` (explore → approve → exit spans multiple turns) was broken. Writers are only the gateway (`team_mode`/`plan_mode`) and the loop (`memories`), so merging doesn't accumulate junk.
- **Workspace vs session dirs (load-bearing):** `workspace_dir()` is the shared CWD + holds `.memory/` and `skills/`; `session_dir()` holds the session-bound dot-dirs (`.tasks/.transcripts/.task_outputs/.worktrees/.mailboxes/.scheduled_tasks.json`). In the gateway, workspace = `/app/workspace` (the mount `~/.myAgent/workspace`), session = `workspace/.sessions/<sid>/`. When adding a subsystem that writes state, decide which root it belongs to and use `workspace_dir()` or `session_dir()` explicitly — not `workdir()` (which is just the CWD alias). CLI mode (no `set_workdir` call) keeps both at `REPO_ROOT`.
- The agent runs with `REPO_ROOT = Path.cwd()`; launch it from the directory you want it to operate in (typically the repo root). In docker, the gateway container's WORKDIR is `/app`; the mounted workspace is `/app/workspace` (← `~/.myAgent/workspace`), NOT `/app/workspace` the repo dir. `agent_gateway/`, `agent_core/`, and `code.py` are bind-mounted `:ro` so edits apply on container restart without a rebuild.
- When adding a tool, update both `BUILTIN_TOOLS` (schema) and `BUILTIN_HANDLERS` (handler) in `agent_core/tools.py` — they are intentionally not auto-derived.
- Provider swaps belong only in `agent_core/adapter.py`; everything downstream consumes Anthropic-style blocks.
- The `agent_core/` split was generated by `_split.py` (AST carve + auto cross-module import resolution). To re-split after large edits to the facade, restore `code.py` from git first — `_split.py` reads `code.py` and would overwrite hand-edited modules. Circular edges are broken by deferred in-function imports; keep them deferred. `chat_create` must stay reached via `adapter.chat_create(...)` (not a direct `from agent_core.adapter import chat_create`) so test monkeypatch propagates.
- When appending a new message kind to `agent_loop`, use `session.append_both(msg)` so the chat record stays complete. Compaction sites (`messages[:] = ...`) must mutate `context_messages` only — never `record`.
- `.env` contains live secrets and is gitignored; `.env.example` is the template to track. Reconcile the two when editing env vars.
- The chat-record/llm-context split is load-bearing: do not collapse `record` and `context_messages` back into one list. Compacting the shared list was the root cause of recurring 400s and lost replay.
- **jiuwenswarm frontend contract pitfalls:** (1) `chat.final.content` is intentionally `""` — text arrives via `chat.delta`; don't "fix" the wire to put full text in `done` or the frontend double-renders. (2) `history.json` assistant records need `event_type` (e.g. `chat.final`) + string `content` or `parseHistoryTimelineEntry` drops them (preview shows only user messages). (3) `file-content` with `encoding=auto` must not 500 — sniff via `_decode_auto`. (4) The AgentPanel walks the real workspace and skips dot-dirs except `.memory`; session state must live under a dot-dir (`.sessions/`) or it pollutes the file tree. (5) `agent_core` memory uses `.md` files + `MEMORY.md` — there is no `config.json`; don't seed one.
