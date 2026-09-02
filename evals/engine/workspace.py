"""evals.engine.workspace — task isolation via worktree or copytree."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any


class WorkspaceIsolator:
    """Isolate each task in its own workspace (worktree or temp copy)."""

    def __init__(self, base_dir: str = "evals/runs"):
        self.base_dir = Path(base_dir)

    def isolate(self, task: dict, run_id: str) -> Path:
        """Create an isolated workspace for the task. Returns the workspace path."""
        fixture = task.get("workspace", "")
        task_id = task.get("id", "unknown")
        ws_dir = self.base_dir / run_id / task_id / "ws"
        ws_dir.mkdir(parents=True, exist_ok=True)

        if fixture:
            fixture_path = Path("evals") / fixture
            if fixture_path.exists():
                shutil.copytree(fixture_path, ws_dir, dirs_exist_ok=True)

        return ws_dir

    def cleanup(self, ws: Path) -> None:
        """Remove the isolated workspace."""
        try:
            shutil.rmtree(ws, ignore_errors=True)
        except Exception:
            pass
