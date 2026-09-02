"""agent_core.loop — extracted from code.py (s20 comprehensive agent)."""
import concurrent.futures
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
from agent_core.context import build_user_content, inject_background_notifications, inject_team_messages, prepare_context, update_context
from agent_core.cron import consume_cron_queue
from agent_core.env import AUTO_COMPACT_WINDOW, CONTINUATION_PROMPT, DEFAULT_MAX_TOKENS, ESCALATED_MAX_TOKENS, MAX_RECOVERY_RETRIES, session_dir, set_session_dir, terminal_print
from agent_core.hooks import check_permission, trigger_hooks
from agent_core.mcp import assemble_tool_pool, set_current_session
from agent_core.memory import consolidate_memories, extract_memories, load_memories, read_memory_index
from agent_core.prompt import assemble_system_prompt, invalidate_section_cache
from agent_core.recovery import RecoveryState, is_prompt_too_long_error, with_retry
from agent_core.session import Session
from agent_core.tasks import has_active_todos
from agent_core.tools import call_tool_handler, is_readonly_tool
from agent_core.tracing import tracer as _tracer


def call_llm(messages: list, context: dict, tools: list,
             state: RecoveryState, max_tokens: int, events=None, stream: bool = False):
    system = assemble_system_prompt(context, tools, messages)
    # Report pre-call context-window usage (system + messages + tools, in tokens)
    # to the ToolPanel status card. The adapter emits further updates during
    # streaming so the stat tracks the growing response.
    if events is not None:
        try:
            from agent_core.compaction import estimate_tokens
            _used = estimate_tokens(messages, system, tools)
            _ctx_max = AUTO_COMPACT_WINDOW
            _rate = (_used / _ctx_max * 100) if _ctx_max else 0
            events.emit("context_usage",
                        {"tokens_used": _used, "context_max": _ctx_max,
                         "rate": round(_rate, 1)})
        except Exception:
            pass
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
    import os as _os, time as _time
    _DBG = _os.environ.get("AGENT_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
    _t = _time.monotonic()
    def _phase(label):
        nonlocal _t
        if _DBG:
            print(f"[ALOOP] {label} t={_time.monotonic()-_t:.3f}s", flush=True)
        _t = _time.monotonic()
    tools, handlers = assemble_tool_pool(context)
    _phase("assemble_tool_pool(init)")
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
    _phase("load_memories")

    while True:
        _turn_span = _tracer.start_span("agent_turn")
        # One cycle: inject scheduled/background work, prepare context, call
        # the model, execute tool_use blocks, append tool_results, repeat.
        # Re-read the model each turn so an online config change takes effect
        # next turn (model_config.model() is mtime-cached).
        state.current_model = model_config.model()
        if session.interrupted:
            session.emit("done", {"reason": "interrupted"})
            _tracer.end_span(_turn_span, status="cancelled")
            return
        fired = consume_cron_queue()
        for job in fired:
            session.append_both({"role": "user",
                                 "content": f"[Scheduled] {job.prompt}"})
            session.emit("text", {"text": f"  \033[35m[cron inject] {job.prompt[:60]}\033[0m"})

        inject_background_notifications(session)
        inject_team_messages(session)
        _phase("cron+bg+team inject")

        if session.rounds_since_todo >= 3 and has_active_todos():
            # Internal nudge — LLM only, never the durable record / live chat.
            # Only fires when there's at least one pending/in_progress todo;
            # with no todo list or all completed, the counter stays 0 and this
            # never triggers, so the model isn't nagged to "update your todos"
            # when there's nothing to update.
            session.append_context({"role": "user",
                                    "content": "<reminder>Update your todos.</reminder>"})
            session.rounds_since_todo = 0

        prepare_context(messages)
        _phase("prepare_context")
        context = update_context(context, messages)
        session.context = context
        tools, handlers = assemble_tool_pool(context)
        _phase("update_context+assemble_tool_pool")

        try:
            _llm_span = _tracer.start_span("llm_call", {
                "model": state.current_model,
                "stream": session.streaming,
            }, parent_id=_turn_span.id)
            response = call_llm(messages, context, tools, state, max_tokens,
                                events=session, stream=session.streaming)
            _tracer.end_span(_llm_span)
        except Exception as e:
            _tracer.end_span(_llm_span, status="error", error=str(e))
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
                # Internal continuation prompt — LLM only, not the chat record.
                session.append_context({"role": "user", "content": CONTINUATION_PROMPT})
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
            # session_dir() is threading.local and child threads don't inherit
            # it — capture it here and restore inside the background thread so
            # any session-bound state the memory path touches resolves to this
            # session. (Memory itself writes to the shared workspace_dir(), which
            # is global, so it needs no capture/restore.)
            sd = session_dir()

            def _memory_background():
                set_session_dir(sd)
                try:
                    written = extract_memories(record_snapshot)
                    if written:
                        session.emit("memory", {"extracted": written})
                    consolidate_memories()
                except Exception:
                    pass

            threading.Thread(target=_memory_background, daemon=True).start()
            session.emit("done", {})
            _tracer.end_span(_turn_span)
            return

        results = []
        compacted_now = False

        # ── Phase 1: pre-check all tool_use blocks serially ──
        # Permission, PreToolUse hooks, and background dispatch are fast and
        # may have side effects (emit events, start threads), so they run
        # serially.  We build an action list: each entry is either
        #   ("skip", block, output_str, blocked_bool)  — pre-check failed
        #   ("compact", block)                         — compact, breaks loop
        #   ("exec", block)                            — needs handler call
        actions = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            session.emit("tool_start", {"id": block.id, "name": block.name,
                                        "input": block.input})

            if block.name == "compact":
                actions.append(("compact", block))
                continue

            blocked = check_permission(block, session.permission, events=session)
            if blocked:
                actions.append(("skip", block, str(blocked), True))
                continue

            blocked = trigger_hooks("PreToolUse", block)
            if blocked:
                actions.append(("skip", block, str(blocked), True))
                continue

            if should_run_background(block.name, block.input):
                bg_id = start_background_task(block, handlers, session)
                output = (f"[Background task {bg_id} started] "
                          "Result will arrive as a task_notification.")
                actions.append(("skip", block, output, False))
                continue

            actions.append(("exec", block))

        # ── Phase 2: execute ──
        # Consecutive readonly "exec" blocks run in parallel via a thread
        # pool; write blocks run serially to preserve ordering semantics.
        # Results are collected in original action order.
        _exec_outputs: dict[int, str] = {}  # action_index → output

        ai = 0
        while ai < len(actions):
            kind = actions[ai][0]

            if kind == "compact":
                block = actions[ai][1]
                messages[:] = compact_history(messages)
                invalidate_section_cache()
                session.append_context({"role": "user",
                                        "content": "[Compacted. Continue with summarized context.]"})
                session.emit("compacted", {"reason": "explicit"})
                compacted_now = True
                break

            if kind == "skip":
                block, output, blocked = actions[ai][1], actions[ai][2], actions[ai][3]
                results.append({"type": "tool_result",
                                "tool_use_id": block.id,
                                "content": output})
                session.emit("tool_result", {"id": block.id, "content": output,
                                             "blocked": blocked})
                ai += 1
                continue

            # kind == "exec"
            block = actions[ai][1]

            if is_readonly_tool(block.name):
                # Gather maximal consecutive readonly exec batch
                batch = []  # list of (action_index, block)
                while (ai < len(actions)
                       and actions[ai][0] == "exec"
                       and is_readonly_tool(actions[ai][1].name)):
                    batch.append((ai, actions[ai][1]))
                    ai += 1

                if len(batch) == 1:
                    # Single readonly — run inline (no pool overhead)
                    idx, blk = batch[0]
                    handler = handlers.get(blk.name)
                    _tool_span = _tracer.start_span("tool_call", {
                        "tool": blk.name, "readonly": True,
                    }, parent_id=_turn_span.id)
                    output = call_tool_handler(handler, blk.input, blk.name)
                    _tracer.end_span(_tool_span)
                    trigger_hooks("PostToolUse", blk, output)
                    session.emit("tool_result", {"id": blk.id, "content": output})
                    _exec_outputs[idx] = output
                else:
                    # Parallel readonly batch
                    with concurrent.futures.ThreadPoolExecutor(
                            max_workers=min(4, len(batch))) as pool:
                        fut_map = {}
                        for idx, blk in batch:
                            handler = handlers.get(blk.name)
                            fut = pool.submit(call_tool_handler,
                                              handler, blk.input, blk.name)
                            fut_map[fut] = (idx, blk)
                        for fut in concurrent.futures.as_completed(fut_map):
                            idx, blk = fut_map[fut]
                            output = fut.result()
                            trigger_hooks("PostToolUse", blk, output)
                            session.emit("tool_result",
                                         {"id": blk.id, "content": output})
                            _exec_outputs[idx] = output
            else:
                # Write block — serial (preserves ordering)
                # Checkpoint before_write/after_write hooks are handled inside
                # run_write/run_edit/run_apply_diff themselves, so undo works
                # regardless of call path.
                _tool_span = _tracer.start_span("tool_call", {
                    "tool": block.name, "readonly": False,
                }, parent_id=_turn_span.id)
                handler = handlers.get(block.name)
                output = call_tool_handler(handler, block.input, block.name)
                _tracer.end_span(_tool_span)
                trigger_hooks("PostToolUse", block, output)
                session.emit("tool_result", {"id": block.id, "content": output})

                if block.name == "todo_write":
                    session.rounds_since_todo = 0
                    try:
                        from agent_core.tasks import todo_payload
                        session.emit("todo", {"todos": todo_payload(session.todos)})
                    except Exception:
                        pass
                elif has_active_todos():
                    session.rounds_since_todo += 1
                else:
                    session.rounds_since_todo = 0

                _exec_outputs[ai] = output
                ai += 1

        # ── Phase 3: assemble results in original order ──
        for ai2, act in enumerate(actions):
            if act[0] == "exec" and ai2 in _exec_outputs:
                blk = act[1]
                results.append({"type": "tool_result",
                                "tool_use_id": blk.id,
                                "content": _exec_outputs[ai2]})

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
        # T-M3: only hold session.lock for the quick mutations
        # (append_both + context update).  agent_loop contains LLM
        # calls and tool execution that can take tens of seconds;
        # holding the lock during it blocks post_message / interrupt
        # / any other session operation for the entire cron turn.
        with session.lock:
            turn_start = len(session.record)
            for job in fired:
                session.append_both({"role": "user",
                                     "content": f"[Scheduled] {job.prompt}"})
                if session.transport == "cli":
                    terminal_print(
                        f"  \033[35m[cron auto] {job.prompt[:60]}\033[0m")
        # agent_loop runs lock-free — it acquires session.lock
        # internally only for the brief append_both / context mutations
        # it needs, so other operations can proceed during LLM calls.
        agent_loop(session)
        with session.lock:
            session.context.update(update_context(session.context, session.record))
        if session.transport == "cli":
            print_turn_assistants(session.record, turn_start)
