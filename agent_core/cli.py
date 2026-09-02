"""agent_core.cli — interactive REPL. Equivalent to code.py's __main__ block."""
import os
import threading

from agent_core.env import CLI_ACTIVE, PROMPT
from agent_core.session import Session, TerminalSink, CliPermission
from agent_core.context import update_context
from agent_core.loop import agent_loop, cron_autorun_loop, print_turn_assistants
from agent_core.hooks import trigger_hooks
from agent_core.bus import consume_boss_inbox


SLASH_HELP = """\
Slash commands:
  /help           show this list
  /clear          clear conversation history (keeps session)
  /model [name]   show or set MODEL_ID for the next turn
  /skills         list available skills
  /agents         list subagent definitions in .claude/agents/
  /memory         list memory files in .memory/
  /tasks          list the task graph
  /compact [focus]  compact conversation now (optional focus topic)
  /quit           exit"""


def _list_dir_files(label, dirpath):
    from agent_core.env import workdir
    d = workdir() / dirpath
    if not d.is_dir():
        return f"(no {dirpath}/ directory)"
    entries = []
    for p in sorted(d.iterdir()):
        if p.is_file():
            entries.append(f"  {p.name}")
        elif p.is_dir():
            sub = [c.name for c in sorted(p.iterdir()) if c.is_file()]
            if sub:
                entries.append(f"  {p.name}/ ({', '.join(sub[:3])}…)")
            else:
                entries.append(f"  {p.name}/")
    return f"{label}:\n" + "\n".join(entries) if entries else f"{label}: (empty)"


def handle_slash_command(query: str, session: Session) -> bool:
    """Handle a /-prefixed command client-side. Returns True if handled
    (the REPL should skip the normal agent_loop turn), False to fall through."""
    parts = query.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/help":
        print(SLASH_HELP)
        return True
    if cmd == "/quit":
        raise KeyboardInterrupt  # signal exit to the REPL loop
    if cmd == "/clear":
        with session.lock:
            session.record.clear()
            session.context_messages.clear()
        print("(history cleared)")
        return True
    if cmd == "/model":
        if arg:
            os.environ["MODEL_ID"] = arg
            print(f"(MODEL_ID set to {arg} for subsequent turns)")
        else:
            print(f"MODEL_ID={os.environ.get('MODEL_ID', '?')}  "
                  f"FALLBACK={os.environ.get('FALLBACK_MODEL_ID', '?')}")
        return True
    if cmd == "/skills":
        try:
            import code
            catalog = code.scan_skills()
            if isinstance(catalog, list):
                print("Skills:")
                for s in catalog:
                    print(f"  {s.get('name', '?')}: {s.get('description', '')[:70]}")
            else:
                print(catalog)
        except Exception as e:
            print(f"(skills error: {e})")
        return True
    if cmd == "/agents":
        print(_list_dir_files("Agents", ".claude/agents"))
        return True
    if cmd == "/memory":
        print(_list_dir_files("Memory", ".memory"))
        return True
    if cmd == "/tasks":
        try:
            from agent_core.tasks import list_tasks
            ts = list_tasks()
            if not ts:
                print("(no tasks)")
            else:
                for t in ts:
                    print(f"  {t.id}: {t.subject} [{t.status}]")
        except Exception as e:
            print(f"(tasks error: {e})")
        return True
    if cmd == "/compact":
        # Request compaction as a normal turn; the agent has the `compact` tool
        # and will use it. focus topic optionally narrows the summary.
        msg = f"Please compact the conversation now. Focus: {arg}" if arg \
            else "Please compact the conversation now."
        trigger_hooks("UserPromptSubmit", msg)
        session.append_both({"role": "user", "content": msg})
        with session.lock:
            agent_loop(session)
            session.context = update_context(session.context, session.record)
        print("(compact turn done)")
        return True

    print(f"Unknown command: {cmd}. Type /help for the list.")
    return True


def main():
    CLI_ACTIVE = True  # noqa: F841 (read by terminal_print via env module global)
    # terminal_print checks env.CLI_ACTIVE at call time; set it on the env module.
    import agent_core.env as _env
    _env.CLI_ACTIVE = True
    print("s20: comprehensive agent")
    print("Enter a question, press Enter to send. Type q or /quit to exit.\n")
    session = Session(transport="cli",
                      sinks=[TerminalSink()],
                      permission=CliPermission(),
                      context=update_context({}, []))
    threading.Thread(target=cron_autorun_loop,
                     args=(session,), daemon=True).start()
    while True:
        try:
            query = input(PROMPT)
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        if query.strip().startswith("/"):
            try:
                if handle_slash_command(query, session):
                    continue
            except KeyboardInterrupt:
                break
            continue
        trigger_hooks("UserPromptSubmit", query)
        turn_start = len(session.record)
        session.append_both({"role": "user", "content": query})
        with session.lock:
            agent_loop(session)
            session.context = update_context(session.context, session.record)
            print_turn_assistants(session.record, turn_start)

        inbox = consume_boss_inbox(route_protocol=True)
        if inbox:
            def inbox_label(msg):
                req_id = msg.get("metadata", {}).get("request_id", "")
                suffix = f" req:{req_id}" if req_id else ""
                return f"{msg.get('type', 'message')}{suffix}"

            inbox_text = "\n".join(
                f"From {m['from']} [{inbox_label(m)}]: "
                f"{m['content'][:200]}" for m in inbox)
            session.append_both({"role": "user",
                                 "content": f"[Inbox]\n{inbox_text}"})
        print()


if __name__ == "__main__":
    main()
