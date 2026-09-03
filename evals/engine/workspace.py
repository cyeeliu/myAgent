"""evals.engine.workspace — task isolation via worktree or copytree."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

# E-L1: Anchor workspace/runs paths at REPO_ROOT instead of relative CWD so the
# isolator works regardless of the process working directory.
try:
    from agent_core.paths import REPO_ROOT
except Exception:  # pragma: no cover — agent_core always present in practice
    REPO_ROOT = Path.cwd()

_RUNS_ROOT = REPO_ROOT / "evals" / "runs"
_FIXTURES_ROOT = REPO_ROOT / "evals"


class WorkspaceIsolator:
    """Isolate each task in its own workspace (worktree or temp copy)."""

    def __init__(self, base_dir: str | Path | None = None):
        # E-L1: default to the REPO_ROOT-anchored runs root; an explicit
        # absolute ``base_dir`` overrides it (relative paths are resolved
        # against REPO_ROOT so callers stay CWD-independent).
        if base_dir is None:
            self.base_dir = _RUNS_ROOT
        else:
            p = Path(base_dir)
            self.base_dir = p if p.is_absolute() else REPO_ROOT / p

    def isolate(self, task: dict, run_id: str) -> Path:
        """Create an isolated workspace for the task. Returns the workspace path."""
        fixture = task.get("workspace", "")
        task_id = task.get("id", "unknown")
        ws_dir = self.base_dir / run_id / task_id / "ws"
        ws_dir.mkdir(parents=True, exist_ok=True)

        if fixture:
            # E-L1: resolve fixture relative to REPO_ROOT/evals, not CWD.
            fixture_path = _FIXTURES_ROOT / fixture
            if fixture_path.exists():
                shutil.copytree(fixture_path, ws_dir, dirs_exist_ok=True)

        return ws_dir

    def cleanup(self, ws: Path) -> None:
        """Remove the isolated workspace."""
        try:
            shutil.rmtree(ws, ignore_errors=True)
        except Exception:
            pass
