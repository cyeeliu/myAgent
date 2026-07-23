"""command_parser — slash-command parsing (mirrors myagent message_handler/command_parser).

agent_core already understands inline slash commands as user text; this parser
is for the gateway to short-circuit control commands (compact/context/model)
without round-tripping through the LLM. For now it just classifies; execution
still goes through agent_compat (which posts the slash text as a user message).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedCommand:
    raw: str
    name: str = ""          # "compact" | "context" | "model" | …
    args: str = ""
    is_control: bool = False


def parse_slash_command(text: str) -> ParsedCommand:
    t = (text or "").strip()
    if not t.startswith("/"):
        return ParsedCommand(raw=text)
    body = t[1:]
    name, _, args = body.partition(" ")
    control = name in {"compact", "context", "model", "recap", "diff", "simplify",
                       "mcp", "session", "status", "workflows"}
    return ParsedCommand(raw=text, name=name, args=args.strip(), is_control=control)
