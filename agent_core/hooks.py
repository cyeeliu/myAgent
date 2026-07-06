"""agent_core.hooks — extracted from code.py (s20 comprehensive agent)."""
from agent_core.env import workdir
from agent_core.session import CliPermission, Permission
# safe_path imported lazily inside check_permission to avoid a hooks->tools->subagent->hooks
# circular import at module load time.


HOOKS = {"UserPromptSubmit": [], "PreToolUse": [],
         "PostToolUse": [], "Stop": []}

def register_hook(event: str, callback):
    HOOKS[event].append(callback)

def trigger_hooks(event: str, *args):
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:
            return result
    return None

DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if="]

DESTRUCTIVE = ["rm ", "> /etc/", "chmod 777"]

def check_permission(block, permission: Permission, events=None):
    # Non-interactive checks (deny list, path escape) return a deny string
    # directly. Interactive checks (destructive bash, mcp deploy) emit a
    # permission_request event and ask the Permission object — CLI prompts
    # via input, API resolves a future from a WS/REST frame.
    if block.name == "bash":
        command = block.input.get("command", "")
        for pattern in DENY_LIST:
            if pattern in command:
                return f"Permission denied: '{pattern}' is on the deny list"
        if any(token in command for token in DESTRUCTIVE):
            if events is not None:
                events.emit("permission_request",
                            {"reason": "destructive command",
                             "detail": command, "tool": block.name,
                             "input": block.input})
            decision = permission.request(block)
            if not decision.get("allow"):
                return "Permission denied by user"
    if block.name in ("write_file", "edit_file"):
        path = block.input.get("path", "")
        try:
            from agent_core.tools import safe_path
            safe_path(path)
        except Exception:
            return f"Permission denied: path escapes workspace: {path}"
    if block.name.startswith("mcp__") and "deploy" in block.name:
        if events is not None:
            events.emit("permission_request",
                        {"reason": f"MCP destructive-looking tool: {block.name}",
                         "detail": "", "tool": block.name, "input": block.input})
        decision = permission.request(block)
        if not decision.get("allow"):
            return "Permission denied by user"
    return None

def permission_hook(block):
    return check_permission(block, CliPermission())

def log_hook(block):
    print(f"\033[90m[HOOK] {block.name}\033[0m")
    return None

def large_output_hook(block, output):
    if len(str(output)) > 100000:
        print(f"\033[33m[HOOK] large output from {block.name}: "
              f"{len(str(output))} chars\033[0m")
    return None

def user_prompt_hook(query: str):
    print(f"\033[90m[HOOK] UserPromptSubmit: {workdir()}\033[0m")
    return None

def stop_hook(messages: list):
    tool_count = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            tool_count += sum(1 for item in content
                              if isinstance(item, dict)
                              and item.get("type") == "tool_result")
    print(f"\033[90m[HOOK] Stop: {tool_count} tool result(s)\033[0m")
    return None
