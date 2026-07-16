"""agent_core.background — Claude-Code-style background tasks.

A background bash command is started with subprocess.Popen (NO timeout), its
stdout+stderr streamed to a log file under .task_outputs/. A per-task monitor
thread waits for exit, records the result, and notifies the session so the
agent is re-invoked when the task finishes. The agent can read partial output
with the `task_output` tool and kill a task with `task_stop`. This mirrors
Claude Code's Bash(run_in_background=true) + TaskOutput + TaskStop mechanism:
the command "keeps running across turns and re-invokes you when it exits"."""
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from agent_core.env import session_dir, workdir
from agent_core.hooks import trigger_hooks


_bg_counter = 0

background_tasks: dict[str, dict] = {}

background_results: dict[str, str] = {}

background_lock = threading.Lock()


def is_slow_operation(tool_name: str, tool_input: dict) -> bool:
    if tool_name != "bash":
        return False
    command = tool_input.get("command", "").lower()
    slow_keywords = ["install", "build", "test", "deploy", "compile",
                     "docker build", "pip install", "npm install",
                     "cargo build", "pytest", "make"]
    return any(keyword in command for keyword in slow_keywords)


def should_run_background(tool_name: str, tool_input: dict) -> bool:
    if tool_name != "bash":
        return False
    return bool(tool_input.get("run_in_background")) or is_slow_operation(tool_name, tool_input)


def _output_dir() -> Path:
    d = session_dir() / ".task_outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def start_background_task(block, handlers: dict, session=None) -> str:
    """Start a background bash command detached (no timeout). Returns the bg_id
    immediately; a monitor thread waits for exit and notifies the session.

    Non-bash background tools (none currently) fall back to the old in-thread
    handler approach so the dispatcher stays general."""
    global _bg_counter
    _bg_counter += 1
    bg_id = f"bg_{_bg_counter:04d}"
    command = block.input.get("command", block.name)
    cwd = str(workdir())

    if block.name != "bash":
        return _start_background_handler(block, handlers, session, bg_id, command)

    # Stream stdout+stderr to a line-buffered file so the agent can read partial
    # output via task_output and nothing is lost if the process is killed.
    out_path = _output_dir() / f"{bg_id}.log"
    out_file = open(out_path, "w", buffering=1)

    try:
        from agent_core import sandbox
        if sandbox.enabled(Path(cwd)):
            proc = subprocess.Popen(
                sandbox.build_argv(Path(cwd), command),
                stdout=out_file, stderr=subprocess.STDOUT,
                start_new_session=True,  # own process group → killpg kills the tree
                text=True,
            )
        else:
            proc = subprocess.Popen(
                command, shell=True, cwd=cwd,
                stdout=out_file, stderr=subprocess.STDOUT,
                start_new_session=True,  # own process group → killpg kills the tree
                text=True,
            )
    except Exception as e:
        out_file.close()
        with background_lock:
            background_tasks[bg_id] = {
                "tool_use_id": block.id, "command": command,
                "status": "completed", "pid": None,
                "log": str(out_path), "code": -1, "started_at": time.time(),
            }
            background_results[bg_id] = f"Error starting command: {e}"
        return bg_id

    with background_lock:
        background_tasks[bg_id] = {
            "tool_use_id": block.id, "command": command,
            "status": "running", "pid": proc.pid,
            "log": str(out_path), "code": None, "started_at": time.time(),
        }

    def monitor():
        try:
            code = proc.wait()
        except Exception:
            code = -1
        try:
            out_file.close()
        except Exception:
            pass
        output = out_path.read_text(errors="replace") if out_path.exists() else ""
        output = output[:200000]  # cap in-memory copy; full log stays on disk
        with background_lock:
            t = background_tasks.get(bg_id)
            # natural exit only if the task was still running (not killed by
            # stop_task). A killed task must NOT re-trigger the loop — the
            # agent already has the task_stop result.
            natural_exit = bool(t) and t.get("status") == "running"
            if natural_exit:
                t["status"] = "completed"
                t["code"] = code
            background_results[bg_id] = output
        trigger_hooks("PostToolUse", block, output)
        if natural_exit:
            _notify(session, bg_id, command, code, output)

    threading.Thread(target=monitor, daemon=True).start()
    print(f"  \033[33m[background] {bg_id}: {str(command)[:60]} (pid {proc.pid})\033[0m")
    return bg_id


def _start_background_handler(block, handlers, session, bg_id, command):
    """Fallback for non-bash background tools: run the handler in a thread."""
    from agent_core.tools import call_tool_handler
    with background_lock:
        background_tasks[bg_id] = {
            "tool_use_id": block.id, "command": command,
            "status": "running", "pid": None,
            "log": None, "code": None, "started_at": time.time(),
        }

    def worker():
        handler = handlers.get(block.name)
        result = call_tool_handler(handler, block.input, block.name)
        trigger_hooks("PostToolUse", block, result)
        with background_lock:
            t = background_tasks.get(bg_id)
            if t and t.get("status") == "running":
                t["status"] = "completed"
                t["code"] = 0
            background_results[bg_id] = str(result)
        _notify(session, bg_id, command, 0, str(result))

    threading.Thread(target=worker, daemon=True).start()
    print(f"  \033[33m[background] {bg_id}: {str(command)[:60]}\033[0m")
    return bg_id


def _notify(session, bg_id, command, code, output):
    """Emit a live task_notification event and re-trigger the loop if the turn
    has already ended, so the agent reacts to the result in a fresh turn."""
    if session is None:
        return
    # Wake a waiting agent FIRST, before any session.lock-acquiring call below.
    # The agent holds session.lock for the whole turn (gateway _run_turn wraps
    # agent_loop in `with session.lock`); session.emit() takes that lock briefly.
    # If the agent is blocked in the `wait` tool, calling wake first ensures the
    # monitor thread unblocks it before emit() would block on the held lock —
    # otherwise emit() blocks, wake never runs, and the wait deadlocks. The
    # background_results entry is already set before _notify is called, so the
    # loop's inject_background_notifications will drain it once the wait returns.
    wl = getattr(session, "wait_lock", None)
    if wl is not None:
        try:
            wl.wake("background", bg_id)
        except Exception:
            pass
    try:
        first_line = ""
        for line in output.splitlines():
            if line.strip():
                first_line = line.strip()[:200]
                break
        summary = first_line or f"(exit {code}, no output)"
        session.emit("task_notification",
                     {"task_id": bg_id, "command": str(command),
                      "status": "completed", "exit_code": code,
                      "summary": summary})
        cb = getattr(session, "on_background_complete", None)
        if cb is not None:
            cb()
    except Exception:
        pass


def collect_background_results() -> list[str]:
    """Pop completed tasks and return notification strings for the model. Full
    output (capped) is included so the agent can act on it without a separate
    task_output call."""
    with background_lock:
        ready = [bg_id for bg_id, task in background_tasks.items()
                 if task["status"] in ("completed", "killed")]
    notifications = []
    for bg_id in ready:
        with background_lock:
            task = background_tasks.pop(bg_id)
            output = background_results.pop(bg_id, "")
        code = task.get("code")
        status = task.get("status", "completed")
        notifications.append(
            f"<task_notification>\n"
            f"  <task_id>{bg_id}</task_id>\n"
            f"  <status>{status}</status>\n"
            f"  <exit_code>{code}</exit_code>\n"
            f"  <command>{task['command']}</command>\n"
            f"  <output>{output[:8000]}</output>\n"
            f"</task_notification>")
    return notifications


def pending_background_count() -> int:
    """Number of background tasks still running."""
    with background_lock:
        return sum(1 for t in background_tasks.values() if t["status"] == "running")


def read_task_output(bg_id: str, timeout: float = 0.0) -> str:
    """Read current output for a background task. If timeout > 0 and the task
    is still running, block up to `timeout` seconds for it to complete (or more
    output to accumulate). Returns a status header + the (tail-capped) output."""
    with background_lock:
        task = background_tasks.get(bg_id)
    if task is None:
        # Already completed and drained by collect_background_results.
        return f"[task {bg_id} not found — already completed and consumed]"
    log = task.get("log")
    if timeout and timeout > 0:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with background_lock:
                t = background_tasks.get(bg_id)
                if not t or t.get("status") != "running":
                    break
            time.sleep(0.2)
    out = ""
    if log:
        p = Path(log)
        if p.exists():
            out = p.read_text(errors="replace")
    with background_lock:
        t = background_tasks.get(bg_id, {})
        status = t.get("status", "completed")
        code = t.get("code")
    tail = out[-50000:] if len(out) > 50000 else out
    return f"[task {bg_id} status={status} exit={code}]\n{tail}"


def stop_task(bg_id: str) -> str:
    """Kill a running background task's process group (SIGTERM, then SIGKILL).
    Marks the task killed BEFORE signalling so the monitor thread does not
    treat the exit as natural and re-trigger the loop."""
    with background_lock:
        task = background_tasks.get(bg_id)
        if task is None:
            return f"Task {bg_id} not found."
        if task.get("status") != "running":
            return f"Task {bg_id} is not running (status={task.get('status')})."
        # Reserve the kill transition under the lock so the monitor can't mark
        # it completed first and notify.
        task["status"] = "killed"
        task["code"] = -9
        pid = task.get("pid")
    if not pid:
        return f"Task {bg_id} has no pid (non-bash task); cannot kill."
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return f"Task {bg_id} process already exited."
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        except Exception as e:
            return f"Error stopping {bg_id}: {e}"
        if sig == signal.SIGTERM:
            time.sleep(0.5)
            try:
                os.killpg(pgid, 0)
            except (ProcessLookupError, OSError):
                break
    return f"Task {bg_id} stopped."


def list_tasks() -> str:
    """One-line-per-task status summary for the agent."""
    with background_lock:
        items = list(background_tasks.items())
    if not items:
        return "(no background tasks)"
    lines = []
    for bg_id, t in items:
        lines.append(
            f"  {bg_id}  {str(t.get('status')):10s}  "
            f"pid={t.get('pid')}  exit={t.get('code')}  "
            f"{str(t.get('command', ''))[:60]}")
    return "\n".join(lines)
