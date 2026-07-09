"""agent_core.sandbox — bash filesystem isolation via bubblewrap (bwrap).

File tools (read_file/write_file/edit_file/glob) are already jailed to the
session workdir by tools.safe_path (it resolves and rejects paths that escape
the workdir, following symlinks). bash is the remaining leak vector: it runs
with cwd=workdir but can read absolute paths outside it — the agent's own
source tree, .env, .git, anything the process can see. During a chat the agent
can `cat /app/agent_core/tools.py` or `cat /app/.env` and dump source/secrets
into the tool result.

When bwrap is available and the session is in a per-session workdir (not the
repo-root dev mode), we wrap each bash command in bwrap so only the workdir
(rw) + system dirs (ro) are visible; everything else is ENOENT. The agent's
own source (/app/agent_core, /app/.env, …) is not mounted → invisible.

SANDBOX env var:
  "1" → force on (requires bwrap; if missing, stays off)
  "0" → force off
  unset → auto: on when bwrap present AND workdir != REPO_ROOT (per-session)

Fail-closed: if sandboxing is enabled but bwrap fails to set up its namespace
(missing caps / seccomp blocks it), bwrap exits non-zero with a stderr message
and the command does NOT run — the error surfaces to the agent. We never
silently fall back to unsandboxed execution when the operator asked for a
sandbox.
"""
import os
import shutil
from pathlib import Path

from agent_core.env import REPO_ROOT

# Read-only system dirs needed for /bin/sh + common commands + python + node.
# python:3.12-slim puts the interpreter under /usr/local; Dockerfile.gateway
# copies node under /usr/local too, so /usr covers both. /etc for passwd/hosts.
_RO_BINDS = ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc")


def bwrap_available() -> bool:
    return shutil.which("bwrap") is not None


def enabled(workdir: Path) -> bool:
    """True if bash should be sandboxed for this workdir."""
    flag = os.getenv("SANDBOX", "").strip().lower()
    if flag == "1":
        return bwrap_available()
    if flag == "0":
        return False
    # auto: sandbox per-session workdirs, not the repo-root dev mode where the
    # operator deliberately wants the agent to touch its own source.
    return bwrap_available() and Path(workdir).resolve() != REPO_ROOT.resolve()


def build_argv(workdir: Path, command: str) -> list[str]:
    """Construct the bwrap argv that runs `command` via /bin/sh -c with only
    `workdir` (rw) + system dirs (ro) visible. Network stays shared (curl/pip
    still work). Caller runs this with subprocess.run/Popen (no shell=True)."""
    wd = str(Path(workdir).resolve())
    argv = ["bwrap", "--die-with-parent"]
    mounted = set()
    for d in _RO_BINDS:
        if Path(d).exists():
            argv += ["--ro-bind", d, d]
            mounted.add(d)
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    mounted |= {"/proc", "/dev", "/tmp"}
    # Create the workdir's ancestor dirs in the sandbox as empty dirs so the
    # bind target path exists, then bind the real workdir read-write on top.
    # Siblings of these ancestors (e.g. /app/agent_core next to /app/workspace)
    # are NOT mounted → invisible to the sandbox. Skip ancestors that are already
    # provided by a mount above (e.g. /tmp tmpfs) to avoid --dir on a mount point.
    parts = Path(wd).parts
    for i in range(1, len(parts)):
        ancestor = str(Path(*parts[:i]))
        if ancestor == "/" or ancestor in mounted:
            continue
        argv += ["--dir", ancestor]
    argv += ["--bind", wd, wd, "--chdir", wd, "--", "/bin/sh", "-c", command]
    return argv
