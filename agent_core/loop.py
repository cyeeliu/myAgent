"""agent_core.loop — extracted from code.py (s20 comprehensive agent)."""
import sys
import threading
import time
from agent_core import adapter
from agent_core import model_config
from agent_core.background import (
    should_run_background, start_background_task, collect_background_results,
)
from agent_core.blocks import has_tool_use
from agent_core.compaction import compact_history, reactive_compact
from agent_core.context import build_user_content, inject_background_notifications, prepare_context, update_context
from agent_core.cron import consume_cron_queue
from agent_core.env import CONTINUATION_PROMPT, DEFAULT_MAX_TOKENS, ESCALATED_MAX_TOKENS, MAX_RECOVERY_RETRIES, set_workdir, terminal_print, workdir
from agent_core.hooks import check_permission, trigger_hooks
from agent_core.mcp import assemble_tool_pool, set_current_session
from agent_core.memory import consolidate_memories, extract_memories, load_memories, read_memory_index
from agent_core.prompt import assemble_system_prompt
from agent_core.recovery import RecoveryState, is_prompt_too_long_error, with_retry
from agent_core.session import Session
from agent_core.tools import call_tool_handler


def call_llm(messages: list, context: dict, tools: list,
             state: RecoveryState, max_tokens: int, events=None, stream: bool = False):
    system = assemble_system_prompt(context)
    return with_retry(
        lambda: adapter.chat_create(
            model=state.current_model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            stream=stream,
            events=events),
        state)

def agent_loop(session: Session):
    set_current_session(session)
    messages = session.context_messages   # compactable LLM context (compaction mutates this only)
    context = session.context
    tools, handlers = assemble_tool_pool()
    state = RecoveryState()
    max_tokens = DEFAULT_MAX_TOKENS

    # Memory: once per user turn, select relevant memories and stage them in
    # context["memories"] (index + relevant content) so assemble_system_prompt
    # surfaces them. Selection uses the full record (pre-compression) for fidelity.
    try:
        idx = read_memory_index()
        relevant = load_memories(session.record)
        session.context["memories"] = (
            (idx[:2000] + ("\n\n" + relevant if relevant else ""))[:4000] if (idx or relevant) else ""
        )
    except Exception:
        pass

    while True:
        # One cycle: inject scheduled/background work, prepare context, call
        # the model, execute tool_use blocks, append tool_results, repeat.
        # Re-read the model each turn so an online config change takes effect
        # next turn (model_config.model() is mtime-cached).
        state.current_model = model_config.model()
        if session.interrupted:
            session.emit("done", {"reason": "interrupted"})
            return
        fired = consume_cron_queue()
        for job in fired:
            session.append_both({"role": "user",
                                 "content": f"[Scheduled] {job.prompt}"})
            session.emit("text", {"text": f"  \033[35m[cron inject] {job.prompt[:60]}\033[0m"})

        inject_background_notifications(session)

        if session.rounds_since_todo >= 3:
            session.append_both({"role": "user",
                                 "content": "<reminder>Update your todos.</reminder>"})
            session.rounds_since_todo = 0

        prepare_context(messages)
        context = update_context(context, messages)
        session.context = context
        tools, handlers = assemble_tool_pool()

        try:
            response = call_llm(messages, context, tools, state, max_tokens,
                                events=session, stream=session.streaming)
        except Exception as e:
            if is_prompt_too_long_error(e) and not state.has_attempted_reactive_compact:
                messages[:] = reactive_compact(messages)
                state.has_attempted_reactive_compact = True
                session.emit("compacted", {"reason": "reactive_compact"})
                continue
            session.append_both({"role": "assistant", "content": [
                {"type": "text", "text": f"[Error] {type(e).__name__}: {e}"}]})
            # Surface LLM/API failures in the gateway stdout (otherwise they only
            # travel as an error event on the WS and are invisible in docker logs).
            try:
                import traceback as _tb
                print(f"[agent_loop] LLM call failed: {type(e).__name__}: {e}",
                      file=sys.stderr)
                _tb.print_exc(file=sys.stderr)
            except Exception:
                pass
            session.emit("error", {"error": f"{type(e).__name__}: {e}"})
            session.emit("done", {})
            return

        # Mid-stream interrupt: the client hit Interrupt during token streaming.
        # chat_create already dropped the partial content; don't append anything
        # to history and don't execute half-formed tool_use blocks — just end.
        if getattr(response, "interrupted", False):
            session.emit("done", {"reason": "interrupted"})
            return

        if response.stop_reason == "max_tokens":
            if not state.has_escalated:
                max_tokens = ESCALATED_MAX_TOKENS
                state.has_escalated = True
                session.emit("text",
                             {"text": f"  \033[33m[max_tokens] retry with {max_tokens}\033[0m"})
                continue
            session.append_both({"role": "assistant", "content": response.content})
            if state.recovery_count < MAX_RECOVERY_RETRIES:
                session.append_both({"role": "user", "content": CONTINUATION_PROMPT})
                state.recovery_count += 1
                continue
            session.emit("done", {})
            return

        max_tokens = DEFAULT_MAX_TOKENS
        state.has_escalated = False
        session.append_both({"role": "assistant", "content": response.content})
        if not has_tool_use(response.content):
            trigger_hooks("Stop", messages)
            # Background tasks are NOT waited on here: a long-running command
            # (dev server, watcher) would hang the turn. Instead the task runs
            # detached; when it exits, on_background_complete re-triggers a fresh
            # turn with its output (mirrors Claude Code's "re-invoke on exit").
            # The agent can also poll/kill via the task_output / task_stop tools.
            # Memory extraction/consolidation runs in a fire-and-forget daemon
            # thread so the turn's `done` fires immediately and the client can
            # start the next turn without waiting on the extra LLM round-trip.
            # Snapshot the record (append-only) so a subsequent turn appending
            # to it can't race the extraction.
            record_snapshot = list(session.record)
            # workdir() is threading.local — capture it here and restore inside
            # the background thread so memory writes land in the session's
            # workspace, not the container cwd.
            wd = workdir()

            def _memory_background():
                set_workdir(wd)
                try:
                    written = extract_memories(record_snapshot)
                    if written:
                        session.emit("memory", {"extracted": written})
                    consolidate_memories()
                except Exception:
                    pass

            threading.Thread(target=_memory_background, daemon=True).start()
            session.emit("done", {})
            return

        results = []
        compacted_now = False
        for block in response.content:
            if block.type != "tool_use":
                continue
            session.emit("tool_start", {"id": block.id, "name": block.name,
                                        "input": block.input})

            if block.name == "compact":
                messages[:] = compact_history(messages)
                session.append_both({"role": "user",
                                     "content": "[Compacted. Continue with summarized context.]"})
                session.emit("compacted", {"reason": "explicit"})
                compacted_now = True
                break

            blocked = check_permission(block, session.permission, events=session)
            if blocked:
                results.append({"type": "tool_result",
                                "tool_use_id": block.id,
                                "content": str(blocked)})
                session.emit("tool_result", {"id": block.id, "content": str(blocked),
                                             "blocked": True})
                continue

            blocked = trigger_hooks("PreToolUse", block)
            if blocked:
                results.append({"type": "tool_result",
                                "tool_use_id": block.id,
                                "content": str(blocked)})
                session.emit("tool_result", {"id": block.id, "content": str(blocked),
                                             "blocked": True})
                continue

            if should_run_background(block.name, block.input):
                bg_id = start_background_task(block, handlers, session)
                output = (f"[Background task {bg_id} started] "
                          "Result will arrive as a task_notification.")
                results.append({"type": "tool_result",
                                "tool_use_id": block.id,
                                "content": output})
                session.emit("tool_result", {"id": block.id, "content": output})
                continue

            handler = handlers.get(block.name)
            output = call_tool_handler(handler, block.input, block.name)
            trigger_hooks("PostToolUse", block, output)
            session.emit("tool_result", {"id": block.id, "content": output})

            if block.name == "todo_write":
                session.rounds_since_todo = 0
            else:
                session.rounds_since_todo += 1

            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})

        if compacted_now:
            continue

        session.append_both({"role": "user", "content": build_user_content(results)})

def print_turn_assistants(messages: list, turn_start: int):
    for msg in messages[turn_start:]:
        if msg.get("role") != "assistant":
            continue
        for block in msg.get("content", []):
            if getattr(block, "type", None) == "text":
                terminal_print(block.text)

def cron_autorun_loop(session: Session):
    while True:
        time.sleep(1)
        fired = consume_cron_queue()
        if not fired:
            continue
        with session.lock:
            turn_start = len(session.record)
            for job in fired:
                session.append_both({"role": "user",
                                     "content": f"[Scheduled] {job.prompt}"})
                if session.transport == "cli":
                    terminal_print(
                        f"  \033[35m[cron auto] {job.prompt[:60]}\033[0m")
            agent_loop(session)
            session.context.update(update_context(session.context, session.record))
            if session.transport == "cli":
                print_turn_assistants(session.record, turn_start)
