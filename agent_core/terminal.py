"""agent_core.terminal — CLI terminal output + readline integration.

``terminal_print`` routes output to the terminal without clobbering the
in-progress readline input line when the agent thread prints from a background
thread. In gateway mode (CLI_ACTIVE=False) it's a plain print.
"""
from __future__ import annotations

import threading

try:
    import readline
    readline.parse_and_bind('set bind-tty-special-chars off')
    READLINE_AVAILABLE = True
except ImportError:
    READLINE_AVAILABLE = False

PROMPT = "\033[36ms20 >> \033[0m"

CLI_ACTIVE = False


def terminal_print(text: str) -> None:
    """Print to terminal without clobbering the readline input line.

    In gateway mode (CLI_ACTIVE=False) or when called from the main thread,
    this is a plain ``print``. When called from a background thread with the
    CLI active, it clears the current line, prints, then redraws the prompt
    + the user's in-progress input.
    """
    if threading.current_thread() is threading.main_thread() or not CLI_ACTIVE:
        print(text)
        return
    line = ""
    if READLINE_AVAILABLE:
        try:
            line = readline.get_line_buffer()
        except Exception:
            line = ""
    print(f"\r\033[K{text}")
    print(PROMPT + line, end="", flush=True)
