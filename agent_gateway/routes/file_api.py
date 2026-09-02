"""File browser REST routes for the Sessions/Agent panels.

The myagent frontend browses on-disk session artifacts via ``/file-api/*``
(list-files, file-content). myAgent keeps conversation history in postgres,
not on disk, so per-session file dirs are usually empty — but the routes must
exist and return ``{files: []}`` for missing dirs instead of 404, else the
SessionsPanel shows "Failed to load session files".
"""
from __future__ import annotations

import asyncio
import shlex
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from agent_core import REPO_ROOT
from agent_gateway.sessions import manager

router = APIRouter(prefix="/file-api", tags=["file-api"])

_FILE_API_ROOT = REPO_ROOT


def _resolve_under_root(rel: str) -> Optional[REPO_ROOT.__class__]:
    """Resolve a frontend-relative path under an allowed root, confining
    traversal. Returns None if the resolved path escapes the allowed roots.

    Security: only ``agent/workspace/...`` (the mounted workspace) and
    ``agent/sessions/...`` (session artifact files) are allowed. All other
    paths — including bare ``.agents/model.json``, ``.env``, source files —
    are rejected with 403. This prevents reading API keys and secrets."""
    import pathlib as _pathlib
    if not rel:
        # Root listing: allow workspace root only.
        ws_root = (_FILE_API_ROOT / "workspace").resolve()
        return ws_root
    _WS_PREFIX = "agent/workspace"
    _SESS_PREFIX = "agent/sessions"
    # Workspace branch: agent/workspace/...
    if rel == _WS_PREFIX or rel.startswith(_WS_PREFIX + "/"):
        sub = "" if rel == _WS_PREFIX else rel[len(_WS_PREFIX) + 1:]
        ws_root = (_FILE_API_ROOT / "workspace").resolve()
        try:
            full = (ws_root / sub).resolve() if sub else ws_root
        except (OSError, ValueError):
            return None
        try:
            full.relative_to(ws_root)
        except ValueError:
            return None
        return full
    # Session artifacts branch: agent/sessions/...
    if rel == _SESS_PREFIX or rel.startswith(_SESS_PREFIX + "/"):
        sub = "" if rel == _SESS_PREFIX else rel[len(_SESS_PREFIX) + 1:]
        sess_root = (_FILE_API_ROOT / "agent" / "sessions").resolve()
        try:
            full = (sess_root / sub).resolve() if sub else sess_root
        except (OSError, ValueError):
            return None
        try:
            full.relative_to(sess_root)
        except ValueError:
            return None
        return full
    # All other paths (including .agents/, .env, source code) are forbidden.
    return None


def _decode_auto(raw: bytes) -> str:
    """Best-effort decode for encoding='auto'. Try utf-8 strict first, then a
    list of common legacy encodings, finally utf-8 with replacement so the
    preview always renders something instead of erroring."""
    for enc in ("utf-8", "gbk", "gb2312", "big5", "shift_jis", "euc_kr", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


@router.get("/list-files")
async def file_api_list_files(dir: str = ""):
    # Browsing a session's artifact dir: hydrate that session from the DB so
    # transcript.md/history.json are refreshed from the current chat record
    # (the DB is source of truth). `dir` looks like `agent/sessions/{sid}`.
    if dir.startswith("agent/sessions/"):
        sid = dir[len("agent/sessions/"):].strip("/")
        if sid:
            loop = asyncio.get_running_loop()
            gs = await asyncio.to_thread(manager.get_or_hydrate, sid, loop)
            # get_or_hydrate returns a cached session without re-writing files,
            # so refresh them explicitly from the live record every browse.
            if gs is not None:
                from agent_gateway.sessions import _write_session_files
                await asyncio.to_thread(_write_session_files, sid, gs.agent.record)
    full = _resolve_under_root(dir)
    if full is None:
        raise HTTPException(status_code=403, detail="forbidden_dir")
    if not full.exists() or not full.is_dir():
        return {"files": []}
    files = []
    try:
        entries = sorted(full.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return {"files": []}
    for entry in entries:
        # Build the full frontend-relative path so the frontend can pass it
        # back to file-content. dir is the prefix (e.g. agent/workspace/skills).
        entry_path = f"{dir}/{entry.name}" if dir else entry.name
        files.append({
            "name": entry.name,
            "path": entry_path,
            "isMarkdown": entry.suffix.lower() == ".md" if entry.is_file() else False,
            "isDirectory": entry.is_dir(),
        })
    return {"files": files}


@router.get("/file-content")
async def file_api_file_content(path: str = "", encoding: str = "utf-8"):
    full = _resolve_under_root(path)
    if full is None:
        raise HTTPException(status_code=403, detail="forbidden_path")
    if not full.exists() or not full.is_file():
        raise HTTPException(status_code=404, detail="file_not_found")
    try:
        data = full.read_text(encoding=encoding)
    except LookupError:
        # "auto" (or any unknown codec the frontend FileViewer sends): sniff the
        # encoding from the bytes, falling back to a tolerant utf-8 decode so the
        # preview never 500s.
        try:
            raw = full.read_bytes()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        data = _decode_auto(raw)
    except (OSError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return Response(content=data.encode("utf-8"), media_type="text/plain; charset=utf-8")


@router.post("/rebuild-agent-data")
async def file_api_rebuild_agent_data():
    """Generate agent/workspace/agent-data.json for the myagent AgentPanel file
    browser by walking the REAL mounted workspace."""
    from agent_gateway.services.agent_data import rebuild_agent_data
    try:
        await asyncio.to_thread(rebuild_agent_data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True}


@router.get("/ws-debug-config")
async def file_api_ws_debug_config():
    return {"wsDisableCompress": False}


# ---------------------------------------------------------------------------
# Terminal command execution — used by the right-sidebar Terminal tab.
# Runs a single command in the workspace root with a timeout.  This mirrors
# the agent's own ``bash`` tool: same cwd, same process model.  The route is
# behind the same middleware auth as every other /file-api/* endpoint.
# ---------------------------------------------------------------------------

class ExecRequest(BaseModel):
    command: str
    cwd: str = ""  # optional sub-directory under workspace, defaults to workspace root

# Commands that must never run via this endpoint.
# Checked via both substring match (for shell metacharacters) and
# shlex parsing (for command-level deny).
_EXEC_DENY_SUBSTR = (
    ":(){", "fork bomb", "shutdown", "reboot", "halt", "init 0",
    "mkfs", "dd if=/dev/", "> /dev/sd",
)

# Denied base commands (first token after shlex split, or after &&/||/; chains).
_EXEC_DENY_CMDS = {
    "sudo", "su", "chmod", "chown", "mount", "umount",
    "shutdown", "reboot", "halt", "poweroff",
    "mkfs", "fdisk", "parted",
}

# Denied rm patterns: rm -rf /, rm -rf /*, rm -rf ~, etc.
_EXEC_DENY_RM_ROOTS = ("/", "/*", "~", "~/*", "/boot", "/etc", "/usr", "/var", "/proc", "/sys")


def _is_dangerous_command(cmd: str) -> bool:
    """Check if a command is dangerous using both substring and parsed checks."""
    # Substring checks for shell metacharacter patterns
    for deny in _EXEC_DENY_SUBSTR:
        if deny in cmd:
            return True

    # Split on shell operators to check each sub-command
    import re
    sub_cmds = re.split(r'\s*(?:&&|\|\||;|\|)\s*', cmd)
    for sub_cmd in sub_cmds:
        sub_cmd = sub_cmd.strip()
        if not sub_cmd:
            continue
        try:
            tokens = shlex.split(sub_cmd)
        except ValueError:
            # Malformed shell — be conservative and deny
            return True
        if not tokens:
            continue
        base = tokens[0]
        # Strip path prefix: /usr/bin/sudo → sudo
        base_name = base.rsplit("/", 1)[-1]

        # Check denied commands
        if base_name in _EXEC_DENY_CMDS:
            return True

        # Check rm -rf with dangerous targets
        if base_name == "rm" and "-rf" in " ".join(tokens[1:]):
            for arg in tokens[1:]:
                if arg.startswith("-"):
                    continue  # skip flags
                # Deny exact dangerous root paths
                if arg in _EXEC_DENY_RM_ROOTS:
                    return True
                # Deny absolute paths outside /tmp (conservative for web terminal)
                if arg.startswith("/") and not arg.startswith("/tmp/"):
                    return True
                # Deny home directory deletion
                if arg.startswith("~"):
                    return True

    return False


# Max output size (bytes) — truncate beyond this to keep responses manageable.
_MAX_OUTPUT = 512 * 1024


@router.post("/exec")
async def file_api_exec(req: ExecRequest):
    cmd = req.command.strip()
    if not cmd:
        raise HTTPException(status_code=400, detail="empty_command")
    if _is_dangerous_command(cmd):
        raise HTTPException(status_code=403, detail="denied_command")

    # Resolve cwd: default to the mounted workspace, optionally a sub-dir.
    # Accepts both relative paths (joined to workspace root) and absolute
    # paths (must be within the workspace root, verified by relative_to).
    import pathlib as _pathlib
    ws_root = (REPO_ROOT / "workspace").resolve()
    if req.cwd:
        cwd_path = _pathlib.Path(req.cwd)
        if cwd_path.is_absolute():
            candidate = cwd_path.resolve()
        else:
            candidate = (ws_root / req.cwd).resolve()
        try:
            candidate.relative_to(ws_root)
        except ValueError:
            raise HTTPException(status_code=403, detail="cwd_escape")
        base = candidate
    else:
        base = ws_root

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(base),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"stdout": "", "stderr": "(command timed out after 30s)", "exit_code": -1}

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        if len(stdout_text) > _MAX_OUTPUT:
            stdout_text = stdout_text[:_MAX_OUTPUT] + "\n... (truncated)"
        if len(stderr_text) > _MAX_OUTPUT:
            stderr_text = stderr_text[:_MAX_OUTPUT] + "\n... (truncated)"
        return {"stdout": stdout_text, "stderr": stderr_text, "exit_code": proc.returncode}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
