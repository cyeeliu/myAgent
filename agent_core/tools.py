"""agent_core.tools — extracted from code.py (s20 comprehensive agent)."""
from pathlib import Path
import ast
import json
import subprocess
import urllib.request
import urllib.error
import urllib.parse
import re
import socket
from agent_core.bus import BUS, consume_lead_inbox
from agent_core.cron import run_cancel_cron, run_list_crons, run_schedule_cron
from agent_core.env import workdir
# connect_mcp imported lazily inside run_connect_mcp to avoid a tools<->mcp
# circular import (mcp.assemble_tool_pool imports BUILTIN_* from tools).
from agent_core.skills import load_skill
from agent_core.subagent import spawn_subagent
from agent_core.tasks import claim_task, complete_task, create_task, get_task_json, list_tasks, set_todos
from agent_core.teammates import run_request_plan, run_request_shutdown, run_review_plan, spawn_teammate_thread
from agent_core.worktrees import create_worktree, keep_worktree, remove_worktree


def safe_path(p: str, cwd: Path = None) -> Path:
    # File tools stay inside the workspace or teammate worktree. Bash remains
    # powerful on purpose and is controlled by the permission hook instead.
    base = cwd or workdir()
    path = (base / p).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"Path escapes workspace: {p}")
    return path

def run_bash(command: str, cwd: Path = None,
             run_in_background: bool = False) -> str:
    # run_in_background is consumed by the dispatcher (background.py starts the
    # command detached via Popen with no timeout); this path only runs foreground
    # commands, which get a 120s cap. Long-running work must use run_in_background.
    from agent_core import sandbox
    base = Path(cwd) if cwd else workdir()
    try:
        if sandbox.enabled(base):
            # bwrap fails closed: a namespace/cap error makes bwrap exit
            # non-zero with a stderr message rather than running unsandboxed.
            r = subprocess.run(sandbox.build_argv(base, command),
                               capture_output=True, text=True, timeout=120)
        else:
            r = subprocess.run(command, shell=True, cwd=base,
                               capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return ("Error: Timeout (120s). The command ran longer than 120s. "
                "Re-run with run_in_background=true to let it continue detached, "
                "then read its output with the task_output tool.")
    except FileNotFoundError as e:
        # bwrap on PATH at enabled()-check time but gone at run time.
        return f"Error: sandbox binary not found: {e}"


def run_task_output(task_id: str, timeout: int = 0) -> str:
    """Read output from a background task. If timeout > 0, block up to that many
    seconds for the task to finish (or more output to accumulate)."""
    from agent_core.background import read_task_output
    return read_task_output(task_id, float(timeout or 0))


def run_task_stop(task_id: str) -> str:
    """Kill a running background task (SIGTERM then SIGKILL on its process group)."""
    from agent_core.background import stop_task
    return stop_task(task_id)


def run_task_list() -> str:
    """List background tasks and their status."""
    from agent_core.background import list_tasks as _list_tasks
    return _list_tasks()

def run_read(path: str, limit: int | None = None,
             offset: int = 0, cwd: Path = None) -> str:
    try:
        lines = safe_path(path, cwd).read_text().splitlines()
        offset = max(int(offset or 0), 0)
        limit = int(limit) if limit is not None else None
        lines = lines[offset:]
        if limit is not None and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str, cwd: Path = None) -> str:
    try:
        fp = safe_path(path, cwd)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str,
             cwd: Path = None, replace_all: bool = False) -> str:
    try:
        fp = safe_path(path, cwd)
        text = fp.read_text()
        if old_text not in text:
            return f"Error: text not found in {path}"
        count = text.count(old_text)
        if replace_all:
            fp.write_text(text.replace(old_text, new_text))
            return f"Edited {path} (replaced {count} occurrences)"
        else:
            fp.write_text(text.replace(old_text, new_text, 1))
            return f"Edited {path} (replaced first of {count} occurrences)"
    except Exception as e:
        return f"Error: {e}"


def run_web_fetch(url: str, prompt: str) -> str:
    """Fetch a URL (15s timeout, 2MB cap, follow redirects, upgrade http→https),
    strip HTML tags, return first ~8000 chars + a note that the prompt should be
    applied by the caller."""
    try:
        # Upgrade http to https
        if url.startswith("http://"):
            url = url.replace("http://", "https://", 1)

        # Set up request with timeout
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Claude-Code-Agent/1.0)"
            }
        )

        # Fetch with timeout
        with urllib.request.urlopen(req, timeout=15) as response:
            # Check content length
            content_length = response.getheader("Content-Length")
            if content_length and int(content_length) > 2 * 1024 * 1024:  # 2MB
                return f"Error: Content too large ({content_length} bytes > 2MB limit)"

            # Read content with size limit
            content = response.read(2 * 1024 * 1024 + 1)  # Read up to 2MB + 1 byte
            if len(content) > 2 * 1024 * 1024:
                return "Error: Content exceeds 2MB limit"

            # Decode
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                html = content.decode(charset, errors="replace")
            except UnicodeDecodeError:
                # Fallback to utf-8 with replacement
                html = content.decode("utf-8", errors="replace")

            # Basic HTML to text conversion
            # Remove script and style tags and their content
            html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
            # Remove HTML tags
            text = re.sub(r'<[^>]+>', ' ', html)
            # Normalize whitespace
            text = re.sub(r'\s+', ' ', text).strip()

            # Truncate to ~8000 chars
            if len(text) > 8000:
                text = text[:8000] + "... (truncated)"

            return f"Fetched {len(text)} chars from {url}. Prompt to apply: \"{prompt[:100]}{'...' if len(prompt) > 100 else ''}\". Content:\n\n{text}"

    except urllib.error.HTTPError as e:
        return f"Error: HTTP {e.code} {e.reason}"
    except urllib.error.URLError as e:
        return f"Error: URL error: {e.reason}"
    except socket.timeout:
        return "Error: Timeout (15s)"
    except Exception as e:
        return f"Error: {e}"


def run_web_search(query: str, max_results: int = 10) -> str:
    """WebSearch not configured: set SEARCH_API_KEY or wire a search backend."""
    return f"WebSearch not configured: set SEARCH_API_KEY or wire a search backend. Query was: \"{query}\" (max_results={max_results})"

def run_glob(pattern: str, cwd: Path = None) -> str:
    import glob as g
    try:
        base = cwd or workdir()
        results = []
        for match in g.glob(pattern, root_dir=base):
            if (base / match).resolve().is_relative_to(base):
                results.append(match)
        return "\n".join(results) if results else "(no matches)"
    except Exception as e:
        return f"Error: {e}"


def run_grep(pattern: str, path: str = ".", output_mode: str = "content",
             max_results: int = 200, cwd: Path = None) -> str:
    """Content search with a regex, Claude-Code-Grep-style.

    pattern: Python regex. path: file or dir to search (relative to workspace).
    output_mode: "content" (default) prints file:line: match; "files_with_matches"
    prints just filenames; "count" prints file: count.
    Stays inside the workspace; skips .git, node_modules, .venv, __pycache__, .next.
    """
    import re
    try:
        base = cwd or workdir()
        target = (base / path).resolve()
        if not target.is_relative_to(base):
            return f"Error: path escapes workspace: {path}"
        regex = re.compile(pattern)
        skip = {".git", "node_modules", ".venv", "__pycache__", ".next",
                ".pytest_cache", ".task_outputs", ".transcripts"}
        files = []
        if target.is_file():
            files = [target]
        else:
            for p in target.rglob("*"):
                if not p.is_file():
                    continue
                if any(part in skip for part in p.parts):
                    continue
                files.append(p)
        out = []
        matches = 0
        for fp in files:
            try:
                text = fp.read_text(errors="replace")
            except Exception:
                continue
            local = str(fp.relative_to(base))
            file_hits = 0
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    file_hits += 1
                    matches += 1
                    if output_mode == "content" and matches <= max_results:
                        out.append(f"{local}:{i}: {line[:300]}")
            if file_hits and output_mode == "files_with_matches":
                out.append(local)
            if file_hits and output_mode == "count":
                out.append(f"{local}: {file_hits}")
        if not out:
            return "(no matches)"
        if matches > max_results and output_mode == "content":
            out.append(f"... ({matches - max_results} more matches, truncated at {max_results})")
        return "\n".join(out)
    except re.error as e:
        return f"Error: invalid regex: {e}"
    except Exception as e:
        return f"Error: {e}"

def call_tool_handler(handler, args: dict, name: str) -> str:
    if not handler:
        return f"Unknown: {name}"
    try:
        return handler(**(args or {}))
    except TypeError as e:
        return f"Error: {e}"

def _normalize_todos(todos):
    if isinstance(todos, str):
        try:
            todos = json.loads(todos)
        except json.JSONDecodeError:
            try:
                todos = ast.literal_eval(todos)
            except (SyntaxError, ValueError):
                return None, "Error: todos must be a list or JSON array string"
    if not isinstance(todos, list):
        return None, "Error: todos must be a list"
    for i, todo in enumerate(todos):
        if not isinstance(todo, dict):
            return None, f"Error: todos[{i}] must be an object"
        if "content" not in todo or "status" not in todo:
            return None, f"Error: todos[{i}] missing 'content' or 'status'"
        if todo["status"] not in ("pending", "in_progress", "completed"):
            return None, f"Error: todos[{i}] has invalid status '{todo['status']}'"
    return todos, None

def run_todo_write(todos: list) -> str:
    todos, error = _normalize_todos(todos)
    if error:
        return error
    set_todos(todos)
    print(f"  \033[33m[todo] updated {len(todos)} item(s)\033[0m")
    return f"Updated {len(todos)} todos"

def run_create_worktree(name: str, task_id: str = "") -> str:
    return create_worktree(name, task_id)

def run_remove_worktree(name: str, discard_changes: bool = False) -> str:
    return remove_worktree(name, discard_changes)

def run_keep_worktree(name: str) -> str:
    return keep_worktree(name)

def run_create_task(subject: str, description: str = "",
                    blockedBy: list[str] | None = None) -> str:
    task = create_task(subject, description, blockedBy)
    deps = f" (blockedBy: {', '.join(blockedBy)})" if blockedBy else ""
    print(f"  \033[34m[create] {task.subject}{deps}\033[0m")
    return f"Created {task.id}: {task.subject}{deps}"

def run_list_tasks() -> str:
    tasks = list_tasks()
    if not tasks:
        return "No tasks."
    return "\n".join(
        f"  {t.id}: {t.subject} [{t.status}]"
        + (f" (wt:{t.worktree})" if t.worktree else "")
        for t in tasks)

def run_get_task(task_id: str) -> str:
    try:
        return get_task_json(task_id)
    except FileNotFoundError:
        return f"Error: task {task_id} not found"

def run_claim_task(task_id: str) -> str:
    try:
        return claim_task(task_id, owner="agent")
    except FileNotFoundError:
        return f"Error: task {task_id} not found"

def run_complete_task(task_id: str) -> str:
    try:
        return complete_task(task_id)
    except FileNotFoundError:
        return f"Error: task {task_id} not found"

def run_spawn_teammate(name: str, role: str, prompt: str) -> str:
    return spawn_teammate_thread(name, role, prompt)

def run_send_message(to: str, content: str) -> str:
    BUS.send("lead", to, content)
    return f"Sent to {to}"

def run_check_inbox() -> str:
    msgs = consume_lead_inbox(route_protocol=True)
    if not msgs:
        return "(inbox empty)"
    lines = []
    for m in msgs:
        meta = m.get("metadata", {})
        req_id = meta.get("request_id", "")
        tag = f" [{m['type']} req:{req_id}]" if req_id else f" [{m['type']}]"
        lines.append(f"  [{m['from']}]{tag} {m['content'][:200]}")
    return "\n".join(lines)

def run_connect_mcp(name: str, command: str = None,
                     args: list = None, env: dict = None) -> str:
    from agent_core.mcp import connect_mcp
    return connect_mcp(name, command=command, args=args, env=env)

BUILTIN_TOOLS = [
    {"name": "bash", "description":
     "Run a shell command. Foreground commands are capped at 120s. "
     "Set run_in_background=true to run detached with NO timeout: the command "
     "keeps running across turns and the agent is re-invoked when it exits. "
     "Use task_output to read a background task's output, task_stop to kill it.",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"},
                                     "run_in_background": {"type": "boolean"}},
                      "required": ["command"]}},
    {"name": "task_output", "description":
     "Read output from a background task started with bash(run_in_background=true). "
     "If timeout > 0, block up to that many seconds for the task to finish or more "
     "output to accumulate. Returns a status header plus the (tail-capped) output.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"},
                                     "timeout": {"type": "integer"}},
                      "required": ["task_id"]}},
    {"name": "task_stop", "description":
     "Kill a running background task (SIGTERM then SIGKILL on its process group).",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}},
                      "required": ["task_id"]}},
    {"name": "task_list", "description":
     "List background tasks with their status (running/completed/killed), pid, "
     "exit code, and command.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
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
    {"name": "edit_file", "description": "Replace exact text in a file.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "old_text": {"type": "string"},
                                     "new_text": {"type": "string"},
                                     "replace_all": {"type": "boolean"}},
                      "required": ["path", "old_text", "new_text"]}},
    {"name": "web_fetch", "description": "Fetch a URL (15s timeout, 2MB cap, follow redirects, upgrade http→https), strip HTML tags, return first ~8000 chars + a note that the prompt should be applied by the caller.",
     "input_schema": {"type": "object",
                      "properties": {"url": {"type": "string"},
                                     "prompt": {"type": "string"}},
                      "required": ["url", "prompt"]}},
    {"name": "web_search", "description": "Search the web. Returns result blocks with titles and URLs. US-only.",
     "input_schema": {"type": "object",
                      "properties": {"query": {"type": "string"},
                                     "max_results": {"type": "integer"}},
                      "required": ["query"]}},
    {"name": "glob", "description": "Find files matching a glob pattern.",
     "input_schema": {"type": "object",
                      "properties": {"pattern": {"type": "string"}},
                      "required": ["pattern"]}},
    {"name": "grep", "description":
     "Search file contents with a regex (Python re syntax). Returns file:line: match "
     "lines by default. output_mode: 'content' | 'files_with_matches' | 'count'. "
     "Stays inside the workspace; skips .git/node_modules/.venv/__pycache__/.next.",
     "input_schema": {"type": "object",
                      "properties": {"pattern": {"type": "string"},
                                     "path": {"type": "string"},
                                     "output_mode": {"type": "string"},
                                     "max_results": {"type": "integer"}},
                      "required": ["pattern"]}},
    {"name": "todo_write",
     "description": "Create and manage a task list for the current session.",
     "input_schema": {"type": "object",
                      "properties": {"todos": {"type": "array",
                            "items": {"type": "object",
                                    "properties": {"content": {"type": "string"},
                                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}},
                                    "required": ["content", "status"]}}},
                      "required": ["todos"]}},
    {"name": "task",
     "description": "Launch a focused subagent. Returns only its final summary. "
                    "Pass agent=<name> to use a defined subagent's prompt/tools/model.",
     "input_schema": {"type": "object",
                      "properties": {"description": {"type": "string"},
                                     "agent": {"type": "string"}},
                      "required": ["description"]}},
    {"name": "load_skill",
     "description": "Load the full content of a skill by name.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"}},
                      "required": ["name"]}},
    {"name": "compact",
     "description": "Summarize earlier conversation and continue with compacted context.",
     "input_schema": {"type": "object",
                      "properties": {"focus": {"type": "string"}},
                      "required": []}},
    {"name": "create_task", "description": "Create a task.",
     "input_schema": {"type": "object",
                      "properties": {"subject": {"type": "string"},
                                     "description": {"type": "string"},
                                     "blockedBy": {"type": "array",
                                                   "items": {"type": "string"}}},
                      "required": ["subject"]}},
    {"name": "list_tasks", "description": "List all tasks.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_task", "description": "Get full task details.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}},
                      "required": ["task_id"]}},
    {"name": "claim_task", "description": "Claim a pending task.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}},
                      "required": ["task_id"]}},
    {"name": "complete_task", "description": "Complete an in-progress task.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}},
                      "required": ["task_id"]}},
    {"name": "schedule_cron",
     "description": ("Schedule a cron job. cron is 5-field: min hour dom "
                     "month dow. For one-shot reminders, compute the target "
                     "minute and set recurring=false."),
     "input_schema": {"type": "object",
                      "properties": {"cron": {"type": "string"},
                                     "prompt": {"type": "string"},
                                     "recurring": {"type": "boolean"},
                                     "durable": {"type": "boolean"}},
                      "required": ["cron", "prompt"]}},
    {"name": "list_crons", "description": "List registered cron jobs.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "cancel_cron", "description": "Cancel a cron job by ID.",
     "input_schema": {"type": "object",
                      "properties": {"job_id": {"type": "string"}},
                      "required": ["job_id"]}},
    {"name": "spawn_teammate", "description": "Spawn an autonomous teammate.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "role": {"type": "string"},
                                     "prompt": {"type": "string"}},
                      "required": ["name", "role", "prompt"]}},
    {"name": "send_message", "description": "Send message to a teammate.",
     "input_schema": {"type": "object",
                      "properties": {"to": {"type": "string"},
                                     "content": {"type": "string"}},
                      "required": ["to", "content"]}},
    {"name": "check_inbox",
     "description": "Check inbox for messages and protocol responses.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "request_shutdown",
     "description": "Request a teammate to shut down.",
     "input_schema": {"type": "object",
                      "properties": {"teammate": {"type": "string"}},
                      "required": ["teammate"]}},
    {"name": "request_plan",
     "description": "Ask a teammate to submit a plan.",
     "input_schema": {"type": "object",
                      "properties": {"teammate": {"type": "string"},
                                     "task": {"type": "string"}},
                      "required": ["teammate", "task"]}},
    {"name": "review_plan",
     "description": "Approve or reject a submitted plan.",
     "input_schema": {"type": "object",
                      "properties": {"request_id": {"type": "string"},
                                     "approve": {"type": "boolean"},
                                     "feedback": {"type": "string"}},
                      "required": ["request_id", "approve"]}},
    {"name": "create_worktree",
     "description": "Create an isolated git worktree.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "task_id": {"type": "string"}},
                      "required": ["name"]}},
    {"name": "remove_worktree",
     "description": "Remove a worktree. Refuses if changes exist.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "discard_changes": {"type": "boolean"}},
                      "required": ["name"]}},
    {"name": "keep_worktree",
     "description": "Keep a worktree for manual review.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"}},
                      "required": ["name"]}},
    {"name": "connect_mcp",
     "description": "Connect to an MCP server and discover its tools. Give a "
                    "command+args for a stdio server, or a name defined in "
                    "mcp.json, or a built-in mock name (docs, deploy). "
                    "Discovered tools become callable as mcp__<server>__<tool>.",
     "input_schema": {"type": "object",
                      "properties": {
                          "name": {"type": "string", "description": "server name to register it under"},
                          "command": {"type": "string", "description": "executable to run (stdio transport). Omit to use mcp.json or a mock name."},
                          "args": {"type": "array", "items": {"type": "string"}},
                          "env": {"type": "object", "additionalProperties": {"type": "string"}}
                      },
                      "required": ["name"]}},
]

BUILTIN_HANDLERS = {
    "bash": run_bash, "read_file": run_read, "write_file": run_write,
    "edit_file": run_edit, "web_fetch": run_web_fetch, "web_search": run_web_search,
    "glob": run_glob, "grep": run_grep,
    "task_output": run_task_output, "task_stop": run_task_stop,
    "task_list": run_task_list,
    "todo_write": run_todo_write, "task": spawn_subagent,
    "load_skill": load_skill,
    "create_task": run_create_task, "list_tasks": run_list_tasks,
    "get_task": run_get_task,
    "claim_task": run_claim_task, "complete_task": run_complete_task,
    "schedule_cron": run_schedule_cron,
    "list_crons": run_list_crons,
    "cancel_cron": run_cancel_cron,
    "spawn_teammate": run_spawn_teammate,
    "send_message": run_send_message, "check_inbox": run_check_inbox,
    "request_shutdown": run_request_shutdown,
    "request_plan": run_request_plan, "review_plan": run_review_plan,
    "create_worktree": run_create_worktree,
    "remove_worktree": run_remove_worktree,
    "keep_worktree": run_keep_worktree,
    "connect_mcp": run_connect_mcp,
}
