# myAgent vs Claude Code — parity gap analysis

Grounded in a read of the actual code surface (not just the `s20` docstring claims).
Audit date: 2026-07-08.

## Summary

myAgent's **backend mechanism coverage is already strong**: the `agent_core/` loop
wires together dispatch, permission, hooks, todos, subagents, skills, compaction,
memory, prompt assembly, error recovery, task graph, background tasks, cron,
teams/protocols, worktrees, and MCP. The tool set includes `bash`, `read_file`,
`write_file`, `edit_file`, `glob`, `grep` (added this pass), `todo_write`, `task`
(subagent), `load_skill`, `compact`, task-graph ops, cron ops, teammate ops,
worktree ops, and `connect_mcp`.

The real gaps vs Claude Code the product are concentrated in **UX surfaces**
(CLI slash commands, streaming tool-card rendering, diff view, slash menu) and a
few **tool/schema completions** (WebFetch, WebSearch, a richer Edit with
replace_all). This pass closed the highest-impact P0 items; P1/P2 remain.

## Parity table

| Dimension | myAgent status | Claude Code | Gap | Priority |
|---|---|---|---|---|
| Tool: bash | ✅ `bash` (fg 120s + background) | Bash | none | — |
| Tool: read | ✅ `read_file` (offset/limit) | Read | no image/pdf support | P2 |
| Tool: edit | ✅ `edit_file` (single replace) | Edit | no `replace_all`, no multi-edit | P1 |
| Tool: write | ✅ `write_file` | Write | none | — |
| Tool: glob | ✅ `glob` | Glob | no `path` scoping param | P2 |
| Tool: grep | ✅ `grep` (added this pass) | Grep | none | — |
| Tool: WebFetch | ❌ missing | WebFetch | no fetch+summarize tool | P1 |
| Tool: WebSearch | ❌ missing | WebSearch | no web search tool | P1 |
| Tool: todo | ✅ `todo_write` | TodoWrite | none | — |
| Tool: Task/subagent | ✅ `task` + `spawn_teammate` | Task | none | — |
| CLI slash commands | ✅ added this pass (`/help /clear /model /skills /agents /memory /tasks /compact /quit`) | rich `/` set | no `/init`, `/review`, `/agents` wizard | P1 |
| Plan mode | ❌ no enter/exit-plan mode | Plan mode | missing | P1 |
| Streaming token render | ⚠️ gateway streams; frontend renders blocks | token-by-token | verify frontend streams incrementally | P1 |
| Tool-call cards | ⚠️ `ToolCard.tsx` exists | expand/collapse + diff | verify diff view for edit_file | P1 |
| Slash command menu (UI) | ❌ no `/` popover | keyboard-navigable `/` menu | missing | P1 |
| Markdown + code highlight | ⚠️ `Markdown.tsx` exists | syntax highlight + copy | verify highlighter wired | P1 |
| Permission UX | ⚠️ `PermissionCard.tsx` exists | allow/allow-always/deny + shortcuts | verify shortcuts | P2 |
| Status bar | ⚠️ `StatusBar.tsx` exists | model + context + conn | verify fields | P2 |
| Skills | ✅ `skills/` + scan/load | Skills | none | — |
| Memory | ✅ `.memory/` load/extract/consolidate | memory | none | — |
| MCP | ✅ `connect_mcp` + assemble_tool_pool | MCP | none | — |
| Hooks/permission | ✅ `hooks.py` + deny list | hooks | none | — |
| Compaction | ✅ 4-stage + reactive | compaction | none | — |
| Subagents (definitions) | ✅ 5 in `.claude/agents/` | subagents | none | — |
| Gateway health | ✅ `/api/health` (added this pass) | — | none | — |
| Deployment | ✅ docker-compose + nginx | — | no healthchecks in compose | P1 |

## P0 — closed this pass

1. **`grep` tool** — `agent_core/tools.py` schema + `run_grep` handler. Content
   search with regex, three output modes, workspace-bound, skips VCS/build dirs.
   Owner: backend-engineer. ✅
2. **CLI slash commands** — `agent_core/cli.py` `handle_slash_command`. `/help`,
   `/clear`, `/model [name]`, `/skills`, `/agents`, `/memory`, `/tasks`,
   `/compact [focus]`, `/quit`. Client-side, no LLM cost. Owner: backend-engineer. ✅
3. **`/api/health` endpoint** — `agent_gateway/main.py`. Reports
   `{status, db, redis, model, sessions_live}` with graceful `in_memory` reporting
   when DATABASE_URL/REDIS_URL unset. Owner: devops-engineer. ✅
4. **Makefile** — `up down logs psql redis-cli test test-core test-frontend
   lint-frontend gateway cli health`. Owner: devops-engineer. ✅
5. **Tests for the above** — `tests/test_grep_and_health.py` (8 cases). Owner:
   test-engineer. ✅

## P1 — recommended next pass

| Task | Owner | Acceptance |
|---|---|---|
| `WebFetch` tool (fetch URL → markdown → summarize via LLM) | backend-engineer | tool registered; test with a mocked fetch |
| `WebSearch` tool | backend-engineer | tool registered; test with a mocked search |
| `edit_file` `replace_all` param | backend-engineer | schema + handler; test |
| Plan mode (enter/exit, plan-approval protocol event) | backend-engineer | `/plan` toggle; plan produced before edits |
| Frontend streaming token render | frontend-engineer | text streams token-by-token in ChatPanel |
| Frontend tool-card diff view for `edit_file`/`write_file` | frontend-engineer | before/after diff rendered |
| Frontend slash-command `/` popover | frontend-engineer | keyboard-navigable, discover from `/api/skills` |
| Frontend markdown syntax highlight + copy button | frontend-engineer | fenced code blocks highlighted |
| docker-compose healthchecks + `condition: service_healthy` | devops-engineer | `docker compose up` waits for healthy gateway/frontend |

## P2 — polish

- `read_file` image/PDF support; `glob` path scoping param; permission-card
  keyboard shortcuts; status-bar field verification; `/init`, `/review` slash
  commands; nginx gzip block for non-streamed responses.

## Cross-cutting coordination

- **Event protocol**: any new gateway event-frame shape (e.g. plan-mode approval
  events, streaming tool_use deltas) must be matched in `frontend/lib/reducer.ts`
  and its test. Backend changes must be backward-compatible (add fields, don't
  rename/remove) or coordinated with the frontend engineer.
- **Slash commands**: CLI slash commands (`agent_core/cli.py`) and any future
  frontend slash menu should share the command list. Consider a single
  `agent_core/slash.py` registry both can import.

## Execution order for P1

1. backend-engineer: WebFetch + WebSearch + edit replace_all (independent, additive).
2. backend-engineer: plan mode (touches event protocol — coordinate with frontend).
3. frontend-engineer: streaming + tool-card diff + slash menu + markdown highlight
   (can proceed in parallel with #1; depends on #2 for plan-mode UI).
4. devops-engineer: docker-compose healthchecks (independent).
5. test-engineer: contract tests for each new event frame as it lands.
