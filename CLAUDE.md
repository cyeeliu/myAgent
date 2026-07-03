# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Python teaching agent (`code.py`, ~2250 lines) that re-implements Claude-Code-style machinery against an **OpenAI-compatible Chat Completions API**. It is "s20: Comprehensive Agent" — one loop wiring together dispatch, permission, hooks, todos, subagents, skills, compaction, memory, prompt assembly, error recovery, task graph, background tasks, cron, teams/protocols, worktrees, and MCP. Each subsystem is a labeled `# ── Section ──` block; the top docstring lists every mechanism.

The wire format is OpenAI, but the rest of the agent speaks **Anthropic-style content blocks** (`text` / `tool_use` / `tool_result`). The adapter at the top of `code.py` (`_to_openai_messages`, `_to_openai_tools`, `chat_create`) is the only place that knows the OpenAI format — swapping providers touches only that section.

## Commands

```bash
cp .env.example .env          # then fill in API key + MODEL_ID
source .venv/bin/activate     # venv exists; deps: anthropic, python-dotenv, pyyaml
python code.py                # interactive REPL; type a question, Enter to send, q to quit
```

`requirements.txt` pins `anthropic>=0.25.0`, `python-dotenv>=1.0.0`, `pyyaml>=6.0`. Despite the `anthropic` pin, `code.py` imports the `openai` SDK as a generic OpenAI-compatible client.

**Env var mismatch to be aware of:** `code.py` reads `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `MODEL_ID` (required), `FALLBACK_MODEL_ID`. But `.env.example` documents `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` instead. The example and the code disagree — when editing one, reconcile the other. `MODEL_ID` is the one var both agree on and is required (`os.environ["MODEL_ID"]`).

There are no tests, lint, or build steps.

## Architecture (read multiple files to understand)

`code.py` is one file; understanding it means understanding how the loop composes the subsystems. Key flow:

- **`agent_loop` (line ~2078)** — the heart. Each iteration: inject due cron jobs + background-task notifications, nudge todos every 3 rounds, run `prepare_context` (context budgeting), rebuild the tool pool, call the LLM via `call_llm`, then execute each `tool_use` block: `compact` short-circuits, `PreToolUse` hooks can block, slow ops fork to background, otherwise the handler runs and `PostToolUse` fires. Stops when the response has no tool_use.
- **`prepare_context` / context budgeting (line ~2039)** — applied every turn in order: `tool_result_budget` (cap total tool-result bytes, persist oversized outputs to `.task_outputs/`), `snip_compact` (drop middle messages past a count), `micro_compact` (drop trivial results), then `compact_history` if still over `CONTEXT_LIMIT` (50k). `reactive_compact` is a last-resort on prompt-too-long errors.
- **Tools** — `BUILTIN_TOOLS` (schemas, line ~1850) and `BUILTIN_HANDLERS` (Python fns, line ~1994) are kept as parallel explicit tables; adding a capability means editing both. `assemble_tool_pool` (line ~1754) merges builtins with connected MCP tools (prefixed `mcp__{server}__{tool__}`).
- **Skills** — `skills/<name>/SKILL.md` with YAML frontmatter (`name`, `description`) + markdown body. `scan_skills` builds the catalog injected into the system prompt each turn; `load_skill` returns full body on demand. Skills are prompts, not code.
- **Prompt assembly (`assemble_system_prompt`, line ~485)** — rebuilt every turn from live context: identity, tool list, working dir, current time, skill catalog, relevant memories (from `.memory/MEMORY.md`), connected MCP servers.
- **Subsystems writing to dot-dirs in `WORKDIR` (= cwd at launch):** `.tasks/` (task graph), `.worktrees/` (git worktrees), `.mailboxes/` (teammate messaging), `.scheduled_tasks.json` (durable cron), `.transcripts/`, `.task_outputs/`, `.memory/`. These are runtime state, not source.
- **Teammates** — `spawn_teammate_thread` runs a background thread with its own message history sharing the lead's tools; communication via `MessageBus` + `ProtocolState` (plan approval / shutdown handshake). `cron_autorun_loop` (line ~2193) is a daemon thread that runs `agent_loop` on scheduled prompts when the REPL is idle.
- **Hooks/permission** — `register_hook` / `trigger_hooks` for `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`. `permission_hook` enforces a deny list (`rm -rf /`, `sudo`, …) and prompts for destructive ops.

## Working in this codebase

- The agent runs with `WORKDIR = Path.cwd()`, so launch it from the directory you want it to operate in (typically `/root/myAgent`); all state dot-dirs are created there.
- When adding a tool, update both `BUILTIN_TOOLS` (schema) and `BUILTIN_HANDLERS` (handler) — they are intentionally not auto-derived.
- Provider swaps belong only in the OpenAI adapter at the top; everything downstream consumes Anthropic-style blocks.
- `.env` contains live secrets and is gitignored; `.env.example` is the template to track.
