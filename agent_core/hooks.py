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

# Plan-mode bash gate: bash is allowed for exploration (ls/cat/git status) but
# must not mutate. Cf. Claude Code plan mode read-only enforcement. Substring
# match on the command; conservative — a false-positive on a read-only command
# is annoying but safe, a false-negative on a write command defeats plan mode.
_PLAN_MODE_BASH_DENY = [
    "rm ", "rmdir", "mv ", "cp ", "mkdir ", "touch ", "chmod ", "chown ",
    " > ", " >> ", ">", ">>", "tee ",
    "git add", "git commit", "git push", "git pull", "git reset", "git checkout",
    "git rebase", "git merge", "git stash", "git rm", "git mv", "git apply",
    "git clean", "git restore",
    "npm install", "npm uninstall", "npm i ", "yarn add", "yarn remove", "pnpm add",
    "pip install", "pip uninstall", "pip3 install", "python -m pip", "uv add", "uv pip",
    "docker ", "docker-compose", "kill ", "pkill", "curl -X", "wget ", "scp ", "rsync ",
    "sed -i", "awk -i", "truncate", "ln -s", "tar ",
    # Interpreter calls that can write files — bypasses the file-tool gate.
    "python -c", "python3 -c", "python -C", "python3 -C",
    "node -e", "node --eval", "node -p", "node --print",
    "perl -e", "perl -E", "ruby -e", "ruby -E",
    "bash -c", "sh -c", "zsh -c", "eval ",
]

def _ask(permission, events, reason, detail, block):
    """Emit a permission_request and resolve via the Permission object.

    Gateway path mirrors run_ask_user: generate a request_id, register the
    future FIRST (so a fast client answer can't race the registration), emit
    the event WITH the request_id, then block on the future. The request_id
    must round-trip so gs.grant(rid) finds the pending future — the
    FuturePermission's internally-generated id is never sent to the client,
    so we drive the resolver directly here.

    CLI path (no resolver on the permission object) → CliPermission input prompt."""
    import uuid
    request_id = uuid.uuid4().hex[:12]
    payload = {"request_id": request_id, "reason": reason, "detail": detail,
               "tool": block.name, "input": block.input}
    resolver = getattr(permission, "resolver", None)
    if resolver is not None:
        try:
            fut = resolver(block, request_id)
        except Exception:
            fut = None
        if events is not None:
            events.emit("permission_request", payload)
        if fut is not None:
            try:
                decision = fut.result(timeout=getattr(permission, "timeout", 120.0))
            except Exception:
                decision = {"allow": False, "modify": None}
            return bool(decision.get("allow"))
        return False
    # CLI path: emit (for any event sink) then prompt via permission.request.
    if events is not None:
        events.emit("permission_request", payload)
    decision = permission.request(block)
    return bool(decision.get("allow"))


def check_permission(block, permission: Permission, events=None):
    # Policy-driven gate first, then hardcoded safety backstop (which can never
    # be bypassed by an "allow" policy — deny-list bash, path escape, and
    # destructive commands are always enforced).
    from agent_core import permissions

    if permissions.is_enabled():
        level = permissions.decide(block.name)
        if level == "deny":
            return f"Permission denied by policy: {block.name}"
        if level == "ask":
            # Policy asks for this tool — surface a permission_request for every
            # call. Hardcoded safety (below) still runs as a backstop, but the
            # ask-level prompt is the primary gate.
            if not _ask(permission, events, f"policy: ask for {block.name}",
                        "", block):
                return "Permission denied by user"
    # When the master toggle (permissions_enabled) is off, skip the per-tool
    # ask/deny entirely — only the hardcoded safety backstop below runs.

    # ── Hardcoded safety backstop (runs regardless of policy level) ──
    # Plan-mode bash read-only gate: bash is in the plan-mode tool allowlist
    # for exploration, but must not mutate. Deny mutating commands outright
    # (cf. Claude Code plan mode). Best-effort — never breaks the loop.
    if block.name == "bash":
        try:
            from agent_core.mcp import get_current_session
            sess = get_current_session()
            if sess is not None and sess.context.get("plan_mode"):
                command = block.input.get("command", "")
                for pat in _PLAN_MODE_BASH_DENY:
                    if pat in command:
                        return (f"Permission denied in plan mode: '{pat.strip()}' "
                                f"looks mutating. 完成探索后用 exit_plan_mode 提交方案，"
                                f"批准即可执行写操作。")
        except Exception:
            pass
    if block.name == "bash":
        command = block.input.get("command", "")
        for pattern in DENY_LIST:
            if pattern in command:
                return f"Permission denied: '{pattern}' is on the deny list"
        if any(token in command for token in DESTRUCTIVE):
            # Destructive commands always ask, even when policy=allow.
            if not _ask(permission, events, "destructive command",
                        command, block):
                return "Permission denied by user"
    if block.name in ("write_file", "edit_file"):
        path = block.input.get("path", "")
        try:
            from agent_core.tools import safe_path
            resolved = safe_path(path)
        except Exception:
            return f"Permission denied: path escapes workspace: {path}"
        # Overwrite prompt: when the target exists and ask_on_overwrite is on,
        # confirm before clobbering — regardless of policy level (the ask-level
        # prompt above already covered policy=ask; this catches policy=allow
        # overwrites too). New-file writes are left through.
        if permissions.ask_on_overwrite():
            try:
                from pathlib import Path
                if Path(resolved).exists():
                    if not _ask(permission, events,
                                "overwrite existing file", str(resolved), block):
                        return "Permission denied by user"
            except Exception:
                pass
    if block.name.startswith("mcp__") and "deploy" in block.name:
        if not _ask(permission, events,
                    f"MCP destructive-looking tool: {block.name}",
                    "", block):
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
