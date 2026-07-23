"""agent_core.mcp — extracted from code.py (s20 comprehensive agent)."""
import json
import os
import re
import subprocess
import threading
from agent_core.env import REPO_ROOT, client, workdir
# BUILTIN_TOOLS/BUILTIN_HANDLERS imported lazily inside assemble_tool_pool to
# avoid a tools<->mcp circular import (tools top-imports connect_mcp).


class MCPClient:
    """MCP client. Real transport = JSON-RPC 2.0 over a subprocess's stdio
    (the standard MCP stdio transport). Mock servers register() in-process
    handlers for the teaching demo. call_tool dispatches to whichever is active.
    """

    def __init__(self, name: str, command: str = None, args: list = None,
                 env: dict = None, cwd: str = None):
        self.name = name
        self.tools: list[dict] = []
        self._handlers: dict[str, callable] = {}
        self._proc = None
        self._req_id = 0
        self._err = None
        if command:
            self._spawn(command, args or [], env or {}, cwd)

    def _spawn(self, command, args, env, cwd):
        full_env = dict(os.environ)
        full_env.update({str(k): str(v) for k, v in env.items()})
        try:
            self._proc = subprocess.Popen(
                [command] + [str(a) for a in args],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=full_env,
                cwd=cwd or str(workdir()), text=True, bufsize=1)
        except FileNotFoundError as e:
            self._err = f"command not found: {command} ({e})"
            raise
        # MCP handshake: initialize → notifications/initialized → tools/list
        self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "myAgent", "version": "1.0"},
        })
        self._notify("notifications/initialized", {})
        tl = self._request("tools/list", {})
        self.tools = [
            {"name": t["name"], "description": t.get("description", ""),
             "inputSchema": t.get("inputSchema", {"type": "object"})}
            for t in (tl or {}).get("tools", [])
        ]

    def _request(self, method, params):
        if self._proc is None:
            raise RuntimeError(f"MCP server {self.name} has no process")
        self._req_id += 1
        rid = self._req_id
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError(f"MCP server {self.name} closed stdout")
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                continue  # server printed a non-JSON line; skip
            if resp.get("id") == rid:
                if "error" in resp:
                    raise RuntimeError(f"MCP error from {self.name}: {resp['error']}")
                return resp.get("result", {})
            # notification or unrelated response — ignore

    def _notify(self, method, params):
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def register(self, tool_defs: list[dict], handlers: dict[str, callable]):
        """In-process mock registration (teaching demo / fallback)."""
        self.tools = tool_defs
        self._handlers = handlers

    def call_tool(self, tool_name: str, args: dict) -> str:
        if self._proc is not None:
            try:
                res = self._request("tools/call",
                                    {"name": tool_name, "arguments": args or {}})
            except Exception as e:
                return f"MCP error calling {self.name}.{tool_name}: {e}"
            content = (res or {}).get("content", [])
            parts = []
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif b.get("type") == "image":
                    parts.append("[image omitted]")
                elif b.get("type") == "error":
                    parts.append(f"[error] {b.get('text', '')}")
            return "\n".join(parts) if parts else json.dumps(res)
        handler = self._handlers.get(tool_name)
        if not handler:
            return f"MCP error: unknown tool '{tool_name}'"
        try:
            return handler(**(args or {}))
        except Exception as e:
            return f"MCP error: {e}"

    def close(self):
        if self._proc is not None:
            try:
                self._proc.stdin.close()
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                pass
            self._proc = None

mcp_clients: dict[str, MCPClient] = {}  # CLI fallback; sessions use Session.mcp_clients

_session_local = threading.local()

def set_current_session(session) -> None:
    """Bind the running agent's session to the current thread so tool handlers
    (which only receive **args) can reach per-session state like MCP clients."""
    _session_local.current = session

def get_current_session():
    return getattr(_session_local, "current", None)

def _mcp_clients() -> dict:
    """MCP clients for the current session (per-session isolation), falling back
    to the module-level dict in CLI mode."""
    s = get_current_session()
    if s is not None and getattr(s, "mcp_clients", None) is not None:
        return s.mcp_clients
    return mcp_clients

def _load_mcp_config() -> dict:
    """Load server definitions from mcp.json (workdir first, then REPO_ROOT).
    Schema: {"servers": {"name": {"command": "...", "args": [...], "env": {...}}}}."""
    import json as _j
    for base in (workdir(), REPO_ROOT):
        f = base / "mcp.json"
        if f.exists():
            try:
                return _j.loads(f.read_text()).get("servers", {}) or {}
            except Exception:
                pass
    return {}

_DISALLOWED_CHARS = re.compile(r'[^a-zA-Z0-9_-]')

def normalize_mcp_name(name: str) -> str:
    """Replace non [a-zA-Z0-9_-] with underscore."""
    return _DISALLOWED_CHARS.sub('_', name)

def _mock_server_docs():
    client = MCPClient("docs")
    client.register(
        tool_defs=[
            {"name": "search", "description": "Search documentation. (readOnly)",
             "inputSchema": {"type": "object",
                             "properties": {"query": {"type": "string"}},
                             "required": ["query"]}},
            {"name": "get_version", "description": "Get API version. (readOnly)",
             "inputSchema": {"type": "object", "properties": {},
                             "required": []}},
        ],
        handlers={
            "search": lambda query: f"[docs] Found 3 results for '{query}'",
            "get_version": lambda: "[docs] API v2.1.0",
        })
    return client

def _mock_server_deploy():
    client = MCPClient("deploy")
    client.register(
        tool_defs=[
            {"name": "trigger",
             "description": "Trigger a deployment. (destructive — requires approval in real CC)",
             "inputSchema": {"type": "object",
                             "properties": {"service": {"type": "string"}},
                             "required": ["service"]}},
            {"name": "status", "description": "Check deployment status. (readOnly)",
             "inputSchema": {"type": "object",
                             "properties": {"service": {"type": "string"}},
                             "required": ["service"]}},
        ],
        handlers={
            "trigger": lambda service: f"[deploy] Triggered: {service}",
            "status": lambda service: f"[deploy] {service}: running (v1.4.2)",
        })
    return client

MOCK_SERVERS = {
    "docs": _mock_server_docs,
    "deploy": _mock_server_deploy,
}

def connect_mcp(name: str, command: str = None, args: list = None,
                 env: dict = None) -> str:
    """Connect an MCP server. Real transport when command/args given or when
    `name` is found in mcp.json; mock fallback for the built-in demo servers."""
    clients = _mcp_clients()
    if name in clients:
        return f"MCP server '{name}' already connected"
    cwd = str(workdir())
    cfg = _load_mcp_config().get(name) if not command else None
    try:
        if command:
            client = MCPClient(name, command=command, args=args, env=env, cwd=cwd)
        elif cfg:
            client = MCPClient(name, command=cfg.get("command"),
                               args=cfg.get("args"), env=cfg.get("env"), cwd=cwd)
        else:
            factory = MOCK_SERVERS.get(name)
            if not factory:
                avail = ", ".join(list(_load_mcp_config().keys()) + list(MOCK_SERVERS.keys()))
                return (f"Unknown server '{name}'. Provide command+args, add it to "
                        f"mcp.json, or use a mock name. Known: {avail}")
            client = factory()
    except Exception as e:
        return f"Failed to connect MCP server '{name}': {type(e).__name__}: {e}"
    clients[name] = client
    tool_names = [t["name"] for t in client.tools]
    print(f"  \033[31m[mcp] connected: {name} → {tool_names}\033[0m")
    return (f"Connected to MCP server '{name}'. "
            f"Discovered {len(client.tools)} tools: {', '.join(tool_names)}")

def assemble_tool_pool(context: dict | None = None) -> tuple[list[dict], dict]:
    """Merge builtin tools + all MCP tools into one pool.

    In plan mode (context.get("plan_mode")) the pool is restricted to a
    read-only allowlist + the `exit_plan_mode` approval gate (cf. Claude Code
    plan mode). MCP tools are hidden entirely in plan mode — we can't tell
    which are read-only."""
    from agent_core.tools import BUILTIN_HANDLERS, BUILTIN_TOOLS
    plan_mode = bool(context and context.get("plan_mode"))
    if plan_mode:
        tools = [t for t in BUILTIN_TOOLS if t["name"] in _PLAN_MODE_ALLOWED]
        handlers = {k: v for k, v in BUILTIN_HANDLERS.items()
                    if k in _PLAN_MODE_ALLOWED}
    else:
        tools = list(BUILTIN_TOOLS)
        handlers = dict(BUILTIN_HANDLERS)
        for server_name, mcp_client in _mcp_clients().items():
            safe_server = normalize_mcp_name(server_name)
            for tool_def in mcp_client.tools:
                safe_tool = normalize_mcp_name(tool_def["name"])
                prefixed = f"mcp__{safe_server}__{safe_tool}"
                tools.append({
                    "name": prefixed,
                    "description": tool_def.get("description", ""),
                    "input_schema": tool_def.get("inputSchema", {}),
                })
                handlers[prefixed] = (
                    lambda *, c=mcp_client, t=tool_def["name"], **kw: c.call_tool(t, kw))
    return tools, handlers


# Read-only tools available in plan mode + the exit_plan_mode approval gate.
# Excludes all mutating / orchestration tools (write_file, edit_file, worktree
# mutate, cron create/cancel, teammates, task graph writes, subagent dispatch,
# mcp connect, team protocols). bash is kept — exploration needs git/ls/cat —
# and the write path (write_file/edit_file) is already cut, with the plan-mode
# prompt directive reinforcing read-only intent.
_PLAN_MODE_ALLOWED = {
    "read_file", "glob", "grep", "list_dir", "bash",
    "web_fetch", "web_search", "todo_write",
    "load_skill", "search_skill", "compact", "show_widget",
    "list_tasks", "get_task", "list_crons",
    "task_list", "task_output",
    "exit_plan_mode",
}
