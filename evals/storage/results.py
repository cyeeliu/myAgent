"""evals.storage.results — persist evaluation results (JSON files + optional Postgres)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# E-L1: Anchor results root at REPO_ROOT instead of relative CWD so the store
# reads/writes the same location regardless of the process working directory.
try:
    from agent_core.paths import REPO_ROOT
except Exception:  # pragma: no cover — agent_core always present in practice
    REPO_ROOT = Path.cwd()

_RESULTS_ROOT = REPO_ROOT / "evals" / "results"


class ResultStore:
    """Persist evaluation reports to JSON files, optionally to Postgres."""

    def __init__(self, base_dir: str | Path | None = None):
        # E-L1: default to the REPO_ROOT-anchored results root; an explicit
        # absolute ``base_dir`` overrides it (relative paths resolve against
        # REPO_ROOT so callers stay CWD-independent).
        if base_dir is None:
            self.base_dir = _RESULTS_ROOT
        else:
            p = Path(base_dir)
            self.base_dir = p if p.is_absolute() else REPO_ROOT / p

    def save(self, report: dict) -> Path:
        """Save report to JSON file. Returns the directory path."""
        run_id = report.get("run_id", "unknown")
        out_dir = self.base_dir / run_id
        out_dir.mkdir(parents=True, exist_ok=True)

        # Save full report
        (out_dir / "report.json").write_text(
            json.dumps(report, indent=2, default=str)
        )

        # Save summary
        from evals.report.render import render_summary
        (out_dir / "summary.txt").write_text(render_summary(report))

        # Optional Postgres
        self._try_save_postgres(report)

        return out_dir

    def load(self, run_id: str) -> dict | None:
        """Load a report from JSON file."""
        path = self.base_dir / run_id / "report.json"
        if path.exists():
            return json.loads(path.read_text())
        return self._try_load_postgres(run_id)

    def list_runs(self) -> list[str]:
        """List all run IDs."""
        if not self.base_dir.exists():
            return []
        return sorted(
            d.name for d in self.base_dir.iterdir()
            if d.is_dir() and (d / "report.json").exists()
        )

    def _try_save_postgres(self, report: dict) -> None:
        """Save to Postgres if DATABASE_URL is set."""
        if not os.environ.get("DATABASE_URL"):
            return
        try:
            from agent_gateway import db
            # Tables would be created here if they don't exist
            # This is optional and best-effort
            db.execute("""
                INSERT INTO eval_runs (run_id, dataset, model, scorecard)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET scorecard = EXCLUDED.scorecard
            """, (report.get("run_id"), report.get("dataset"),
                  report.get("model"), json.dumps(report.get("scorecard", {}))))
        except Exception:
            pass  # Postgres optional

    def _try_load_postgres(self, run_id: str) -> dict | None:
        """Load from Postgres if available."""
        if not os.environ.get("DATABASE_URL"):
            return None
        try:
            from agent_gateway import db
            row = db.query_one("SELECT * FROM eval_runs WHERE run_id = %s", (run_id,))
            return row
        except Exception:
            return None
