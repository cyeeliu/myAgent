"""agent_core.cli — interactive REPL. Equivalent to code.py's __main__ block."""
import threading

from agent_core.env import CLI_ACTIVE, PROMPT
from agent_core.session import Session, TerminalSink, CliPermission
from agent_core.context import update_context
from agent_core.loop import agent_loop, cron_autorun_loop, print_turn_assistants
from agent_core.hooks import trigger_hooks
from agent_core.bus import consume_lead_inbox


def main():
    CLI_ACTIVE = True  # noqa: F841 (read by terminal_print via env module global)
    # terminal_print checks env.CLI_ACTIVE at call time; set it on the env module.
    import agent_core.env as _env
    _env.CLI_ACTIVE = True
    print("s20: comprehensive agent")
    print("Enter a question, press Enter to send. Type q to quit.\n")
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
        trigger_hooks("UserPromptSubmit", query)
        turn_start = len(session.record)
        session.append_both({"role": "user", "content": query})
        with session.lock:
            agent_loop(session)
            session.context = update_context(session.context, session.record)
            print_turn_assistants(session.record, turn_start)

        inbox = consume_lead_inbox(route_protocol=True)
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
