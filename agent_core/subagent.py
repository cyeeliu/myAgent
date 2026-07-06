"""agent_core.subagent — extracted from code.py (s20 comprehensive agent)."""
from agent_core import adapter
from agent_core.blocks import extract_text, has_tool_use
from agent_core.env import MODEL, workdir
from agent_core.hooks import trigger_hooks
# call_tool_handler + run_* imported lazily inside spawn_subagent to avoid a
# tools<->subagent circular import (tools top-imports subagent for BUILTIN_HANDLERS).


SUB_SYSTEM = (
    f"You are a coding subagent at {workdir()}. "
    "Complete the task, then return a concise final summary. "
    "Do not spawn more agents."
)

SUB_TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "limit": {"type": "integer"},
                                     "offset": {"type": "integer"}},
                      "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "content": {"type": "string"}},
                      "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in a file once.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "old_text": {"type": "string"},
                                     "new_text": {"type": "string"}},
                      "required": ["path", "old_text", "new_text"]}},
    {"name": "glob", "description": "Find files matching a glob pattern.",
     "input_schema": {"type": "object",
                      "properties": {"pattern": {"type": "string"}},
                      "required": ["pattern"]}},
]

SUB_HANDLERS = None  # built lazily inside spawn_subagent (run_* live in tools)

def spawn_subagent(description: str) -> str:
    from agent_core.tools import call_tool_handler, run_bash, run_edit, run_glob, run_read, run_write
    sub_handlers = {
        "bash": run_bash, "read_file": run_read,
        "write_file": run_write, "edit_file": run_edit,
        "glob": run_glob,
    }
    messages = [{"role": "user", "content": description}]
    for _ in range(30):
        response = adapter.chat_create(
            model=MODEL, system=SUB_SYSTEM, messages=messages,
            tools=SUB_TOOLS, max_tokens=8000)
        messages.append({"role": "assistant", "content": response.content})
        if not has_tool_use(response.content):
            break
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            blocked = trigger_hooks("PreToolUse", block)
            if blocked:
                output = str(blocked)
            else:
                handler = sub_handlers.get(block.name)
                output = call_tool_handler(handler, block.input, block.name)
                trigger_hooks("PostToolUse", block, output)
            results.append({"type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(output)})
        messages.append({"role": "user", "content": results})
    for msg in reversed(messages):
        if msg["role"] == "assistant":
            text = extract_text(msg["content"])
            if text:
                return text
    return "Subagent finished without a text summary."
