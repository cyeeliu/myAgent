"""agent_core.hooks — extracted from code.py (s20 comprehensive agent)."""
import shlex
import logging
from agent_core.env import workdir
from agent_core.session import CliPermission, Permission
# safe_path imported lazily inside check_permission to avoid a hooks->tools->subagent->hooks
# circular import at module load time.

logger = logging.getLogger(__name__)

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

DENY_LIST = ["rm -rf /", "sudo", "su ", "shutdown", "reboot", "mkfs", "dd if="]

DESTRUCTIVE = ["rm ", "> /etc/", "chmod 777"]

# 扩展的 deny 命令名集合（结构化检查用）
_DENY_COMMANDS = {"sudo", "su", "shutdown", "reboot", "mkfs", "dd"}

# rm 递归删除的危险路径
_RM_DANGEROUS_PATHS = {"/", "/*", "$HOME/..", "~", "$HOME", "${HOME}"}


def _normalize_command(command: str) -> str:
    """规范化命令字符串：合并多余空格，使子串匹配不被空格变化绕过。"""
    return ' '.join(command.split())


def _check_bash_safety(command: str) -> tuple[bool, str | None]:
    """结构化检查 bash 命令安全性。

    返回 (is_destructive, deny_reason)：
    - deny_reason 非 None → 硬拒绝，返回拒绝原因
    - is_destructive=True 且 deny_reason=None → 需要用户确认（_ask）
    - 两者都为 False/None → 安全，放行
    """
    normalized = _normalize_command(command)

    # 1. 规范化后的子串匹配（防止双空格、tab 等绕过）
    for pattern in DENY_LIST:
        if pattern in normalized:
            return False, f"Permission denied: '{pattern}' is on the deny list"

    # 2. 结构化检查：用 shlex 解析命令名和参数
    try:
        tokens = shlex.split(command)
    except ValueError:
        # shlex 解析失败（语法错误/不完整引号），用规范化子串匹配兜底
        for token in DESTRUCTIVE:
            if token in normalized:
                return True, None
        return False, None

    if not tokens:
        return False, None

    cmd_name = tokens[0]

    # 3. 检查命令名是否在 deny 集合中
    if cmd_name in _DENY_COMMANDS:
        return False, f"Permission denied: '{cmd_name}' is on the deny list"

    # 4. 检查 rm 递归删除危险路径
    if cmd_name == "rm":
        flags = ''.join(t for t in tokens[1:] if t.startswith('-'))
        is_recursive = 'r' in flags or '--recursive' in tokens
        is_force = 'f' in flags or '--force' in tokens
        if is_recursive:
            for arg in tokens[1:]:
                if arg in _RM_DANGEROUS_PATHS or arg.startswith('/') and arg.count('/') <= 1:
                    return False, (f"Permission denied: recursive delete of "
                                   f"root-like path '{arg}'")

    # 5. 规范化后的 DESTRUCTIVE 子串匹配
    for token in DESTRUCTIVE:
        if token in normalized:
            return True, None

    return False, None

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
        except Exception as e:
            logger.warning("permission resolver failed: %s", e, exc_info=True)
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
                normalized_cmd = _normalize_command(command)
                for pat in _PLAN_MODE_BASH_DENY:
                    if pat in normalized_cmd:
                        return (f"Permission denied in plan mode: '{pat.strip()}' "
                                f"looks mutating. 完成探索后用 exit_plan_mode 提交方案，"
                                f"批准即可执行写操作。")
        except Exception as e:
            logger.warning("plan_mode bash check failed: %s", e, exc_info=True)
    if block.name == "bash":
        command = block.input.get("command", "")
        is_destructive, deny_reason = _check_bash_safety(command)
        if deny_reason:
            return deny_reason
        if is_destructive:
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
            except Exception as e:
                logger.warning("overwrite check failed: %s", e, exc_info=True)
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
