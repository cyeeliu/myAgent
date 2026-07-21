"""agent_core.permissions — per-tool permission policy store.

A persisted JSON policy maps tool name → "allow" | "ask" | "deny", plus a
default level and an `ask_on_overwrite` flag. `check_permission` (hooks.py)
consults this on every tool call: "deny" hard-denies, "ask" surfaces a
permission_request to the user (CLI input / gateway UserQuestionModal),
"allow" skips the prompt but keeps the hardcoded safety backstop (deny-list
bash, path escape, destructive bash, overwrite prompt).

The policy lives at `workspace_dir() / ".permissions" / "policy.json"` —
shared across sessions (like `.memory/` and `skills/`), not per-session.

The frontend ConfigPanel/PermissionsToolsEditor already speaks the shape
`{tools: {name: level}}` via the `permissions.tools.get/update/delete` WS
methods; this module is the backend those methods call.
"""
from __future__ import annotations
import json
import re
import threading
from agent_core.env import workspace_dir

LEVELS = ("allow", "ask", "deny")

# Seed defaults: bash + file writes ask by default (the user asked for real
# gating + authorization prompts). Reads/globs/etc. fall through to
# `default=allow`. Users relax via the security panel. `enabled` is the
# master toggle for the per-tool policy; `memory_forbidden` holds the regex
# filter applied to memory recall + extraction.
_SEED = {
    "default": "allow",
    "ask_on_overwrite": True,
    "enabled": True,
    "memory_forbidden": {"enabled": False, "pattern": ""},
    "tools": {"bash": "ask", "write_file": "ask", "edit_file": "ask"},
}

_lock = threading.Lock()
# Compiled-regex cache keyed by pattern string so matches_forbidden() doesn't
# recompile on every memory recall/extraction call. None entry = invalid regex
# (treated as no-match, never raises).
_regex_cache: dict[str, object] = {}


def _policy_path():
    return workspace_dir() / ".permissions" / "policy.json"


def _read() -> dict:
    path = _policy_path()
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _normalize(data: dict) -> dict:
    default = data.get("default", "allow")
    if default not in LEVELS:
        default = "allow"
    aoo = data.get("ask_on_overwrite", True)
    if not isinstance(aoo, bool):
        aoo = True
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        enabled = True
    tools = data.get("tools") or {}
    clean = {}
    if isinstance(tools, dict):
        for k, v in tools.items():
            if isinstance(v, str) and v.lower() in LEVELS:
                clean[str(k)] = v.lower()
    mf = data.get("memory_forbidden") or {}
    if not isinstance(mf, dict):
        mf = {}
    mf_en = mf.get("enabled", False)
    if not isinstance(mf_en, bool):
        mf_en = False
    mf_pat = mf.get("pattern", "")
    if not isinstance(mf_pat, str):
        mf_pat = ""
    return {
        "default": default,
        "ask_on_overwrite": aoo,
        "enabled": enabled,
        "memory_forbidden": {"enabled": mf_en, "pattern": mf_pat},
        "tools": clean,
    }


def get_policy() -> dict:
    """Return the persisted policy, seeding defaults on first read."""
    with _lock:
        data = _read()
        if data is None:
            data = dict(_SEED)
            try:
                _policy_path().parent.mkdir(parents=True, exist_ok=True)
                _policy_path().write_text(json.dumps(data, indent=2, ensure_ascii=False))
            except OSError:
                pass
        return _normalize(data)


def _write(policy: dict) -> dict:
    norm = _normalize(policy)
    try:
        _policy_path().parent.mkdir(parents=True, exist_ok=True)
        _policy_path().write_text(json.dumps(norm, indent=2, ensure_ascii=False))
    except OSError:
        pass
    return norm


def set_tool_level(name: str, level: str) -> dict:
    """Set tools[name]=level and return the updated policy."""
    if not name or level not in LEVELS:
        return get_policy()
    with _lock:
        policy = _normalize(_read() or dict(_SEED))
        policy["tools"][str(name)] = level
        return _write(policy)


def delete_tool(name: str) -> dict:
    """Remove tools[name] and return the updated policy."""
    with _lock:
        policy = _normalize(_read() or dict(_SEED))
        policy["tools"].pop(str(name), None)
        return _write(policy)


def decide(tool_name: str) -> str:
    """Return the effective level for a tool: tools[name] or default or allow."""
    policy = get_policy()
    return policy["tools"].get(tool_name) or policy["default"]


def ask_on_overwrite() -> bool:
    return get_policy()["ask_on_overwrite"]


def is_enabled() -> bool:
    """Master toggle for the per-tool policy. When False, check_permission
    skips the per-tool ask/deny (hardcoded safety backstop still runs)."""
    return get_policy()["enabled"]


def set_enabled(enabled: bool) -> dict:
    """Persist the master toggle and return the updated policy."""
    with _lock:
        policy = _normalize(_read() or dict(_SEED))
        policy["enabled"] = bool(enabled)
        return _write(policy)


def get_memory_forbidden() -> dict:
    """Return {"enabled": bool, "pattern": str} for the memory regex filter."""
    return get_policy()["memory_forbidden"]


def set_memory_forbidden(enabled: bool, pattern: str) -> dict:
    """Persist the memory forbidden filter and return the updated policy."""
    with _lock:
        policy = _normalize(_read() or dict(_SEED))
        policy["memory_forbidden"] = {"enabled": bool(enabled),
                                      "pattern": str(pattern or "")}
        return _write(policy)


def matches_forbidden(text: str) -> bool:
    """True if `text` matches the forbidden regex. Returns False when the
    filter is disabled, the pattern is empty, or the regex is invalid —
    never raises (memory filtering must not break the agent loop)."""
    mf = get_memory_forbidden()
    if not mf.get("enabled"):
        return False
    pattern = mf.get("pattern", "")
    if not pattern:
        return False
    compiled = _regex_cache.get(pattern, "__miss__")
    if compiled == "__miss__":
        try:
            compiled = re.compile(pattern)
        except re.error:
            compiled = None
        _regex_cache[pattern] = compiled
    if compiled is None:
        return False
    return bool(compiled.search(text or ""))
