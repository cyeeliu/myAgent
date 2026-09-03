"""EvalRunManager — manage evaluation run lifecycle in the gateway process.

Mirrors SessionManager: one background thread per run, results persisted to
evals/results/ via ResultStore. Progress events published to a callback that
the caller wires into the WS event pipe.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

# E-L1: Use REPO_ROOT for dataset/results paths instead of relative CWD.
try:
    from agent_core.env import REPO_ROOT
except Exception:
    REPO_ROOT = Path.cwd()

_DATASETS_DIR = REPO_ROOT / "evals" / "datasets"
_RESULTS_DIR = REPO_ROOT / "evals" / "results"

# E-H1: Validate run_id to prevent path traversal.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")

# Module-level singleton (mirrors `manager` in sessions)
eval_runs: "EvalRunManager | None" = None


def get_eval_runs() -> "EvalRunManager":
    global eval_runs
    if eval_runs is None:
        eval_runs = EvalRunManager()
    return eval_runs


class EvalRunHandle:
    """Handle for a single eval run."""

    def __init__(self, run_id: str, dataset: str, model: str):
        self.run_id = run_id
        self.dataset = dataset
        self.model = model
        self.thread: Optional[threading.Thread] = None
        self.cancelled = threading.Event()
        self.status = "running"  # running / complete / error / cancelled
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.error: Optional[str] = None
        self.scorecard: Optional[dict] = None
        self.progress: list[dict] = []  # accumulated progress events
        self.per_task_status: dict[str, dict] = {}
        self.current_task_id: str = ""
        self.total_tasks: int = 0


class EvalRunManager:
    """Manage concurrent eval runs."""

    MAX_CONCURRENT = 2

    def __init__(self):
        self._runs: dict[str, EvalRunHandle] = {}
        self._lock = threading.Lock()
        # E-C1: Callback set by the gateway to push events to WS.
        # Signature: (session_id, kind, payload) → None
        self._event_callback: Optional[Callable[[str, str, dict], None]] = None
        # Map run_id → session_id (which session started the run)
        self._run_sessions: dict[str, str] = {}

    def set_event_callback(self, cb: Callable[[str, str, dict], None]):
        """Set the callback for pushing events to the WS pipe.

        cb(session_id, kind, payload) → None
        """
        self._event_callback = cb

    def _emit(self, run_id: str, kind: str, payload: dict):
        """E-C1: Emit an event to the WS pipe via the callback."""
        if self._event_callback:
            session_id = self._run_sessions.get(run_id, "")
            try:
                self._event_callback(session_id, kind, payload)
            except Exception:
                pass

    def start(
        self,
        dataset: str,
        model: str,
        repeat: int = 1,
        mode: str = "online",
        limit: int = 0,
        session_id: str = "",
    ) -> str:
        """Start a new eval run. Returns run_id.

        E-C1: session_id is stored so events can be routed back to the
        originating WS connection's EventPipe.
        """
        # E-M1: check + insert in a single lock block to prevent TOCTOU race.
        with self._lock:
            active = sum(1 for h in self._runs.values() if h.status == "running")
            if active >= self.MAX_CONCURRENT:
                raise RuntimeError(f"max_concurrent_runs ({self.MAX_CONCURRENT}) reached")

            run_id = f"{dataset}_{model}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            handle = EvalRunHandle(run_id, dataset, model)
            self._runs[run_id] = handle
            self._run_sessions[run_id] = session_id

        t = threading.Thread(
            target=self._execute,
            args=(handle, dataset, model, repeat, mode, limit),
            daemon=True,
        )
        handle.thread = t
        t.start()
        return run_id

    def _execute(
        self,
        handle: EvalRunHandle,
        dataset: str,
        model: str,
        repeat: int,
        mode: str,
        limit: int,
    ):
        """Background thread: run EvalRunner, emit progress events."""
        try:
            from evals.engine.runner import EvalRunner
            from evals.storage.results import ResultStore

            # E-L1: Use REPO_ROOT-anchored path instead of relative CWD.
            dataset_path = _DATASETS_DIR / f"{dataset}.json"
            if not dataset_path.exists():
                raise FileNotFoundError(f"dataset not found: {dataset}")
            dataset_data = json.loads(dataset_path.read_text())

            # E-M2: Apply limit by slicing tasks list. The runner also honors
            # opts["limit"], but we slice here too so the dataset is correct
            # if the runner's limit handling changes.
            if limit > 0:
                dataset_data["tasks"] = dataset_data.get("tasks", [])[:limit]

            handle.total_tasks = len(dataset_data.get("tasks", []))

            # E-H2: Progress callback — actually pass it to run_dataset.
            def on_progress(event: dict):
                if handle.cancelled.is_set():
                    return
                handle.progress.append(event)
                task_id = event.get("task_id", "")
                status = event.get("status", "")
                if task_id:
                    handle.current_task_id = task_id
                    handle.per_task_status[task_id] = {
                        "status": status,
                        "started_at": time.time(),
                        "duration_ms": event.get("duration_ms", 0),
                        "rep": event.get("rep", 0),
                    }
                self._emit(handle.run_id, "eval_progress", {"run_id": handle.run_id, **event})

            # Run
            runner = EvalRunner()
            opts = {
                "run_id": handle.run_id,
                "model": model,
                "repeat": repeat,
                "mode": mode,
            }
            if limit > 0:
                opts["limit"] = limit

            if mode == "mock":
                opts["llm"] = "scripted"

            # E-H2: Pass on_progress so per-task progress events fire.
            # E-M2: Pass repeat and limit through opts so run_dataset can
            # honor them (the runner reads opts["repeat"] and opts["limit"]).
            report = runner.run_dataset(dataset_data, opts, on_progress=on_progress)

            if handle.cancelled.is_set():
                handle.status = "cancelled"
                self._emit(handle.run_id, "eval_run_error", {"run_id": handle.run_id, "error": "cancelled"})
                return

            # Persist
            store = ResultStore()
            store.save(report)

            handle.scorecard = report.get("scorecard", {})
            handle.status = "complete"
            handle.finished_at = time.time()
            self._emit(handle.run_id, "eval_run_complete", {
                "run_id": handle.run_id,
                "scorecard": handle.scorecard,
                "duration_s": handle.finished_at - handle.started_at,
                "total_tasks": report.get("total_tasks", 0),
            })

        except Exception as e:
            handle.status = "error"
            handle.error = str(e)
            handle.finished_at = time.time()
            self._emit(handle.run_id, "eval_run_error", {"run_id": handle.run_id, "error": str(e)})

    def list_runs(self, offset: int = 0, limit: int = 20) -> list[dict]:
        """List run summaries (from storage + active)."""
        from evals.storage.results import ResultStore
        store = ResultStore()
        stored_runs = store.list_runs()

        summaries = []
        # Active runs first
        with self._lock:
            for h in sorted(
                self._runs.values(),
                key=lambda x: x.started_at,
                reverse=True,
            ):
                summaries.append({
                    "run_id": h.run_id,
                    "dataset": h.dataset,
                    "model": h.model,
                    "status": h.status,
                    "started_at": h.started_at,
                    "finished_at": h.finished_at,
                })

        # Stored runs
        for run_id in stored_runs:
            if any(s["run_id"] == run_id for s in summaries):
                continue
            # E-H1: Validate run_id before loading.
            if not _RUN_ID_RE.match(run_id) or ".." in run_id:
                continue
            report = store.load(run_id)
            if report:
                summaries.append({
                    "run_id": run_id,
                    "dataset": report.get("dataset", ""),
                    "model": report.get("model", ""),
                    "status": "complete",
                    "started_at": report.get("started_at", 0),
                    "finished_at": report.get("finished_at", 0),
                })

        return summaries[offset : offset + limit]

    def get_run(self, run_id: str) -> Optional[dict]:
        """Get a run's full report."""
        # E-H1: Validate run_id to prevent path traversal.
        if not _RUN_ID_RE.match(run_id) or ".." in run_id:
            return None
        from evals.storage.results import ResultStore
        store = ResultStore()
        return store.load(run_id)

    def cancel(self, run_id: str) -> bool:
        # E-H1: Validate run_id.
        if not _RUN_ID_RE.match(run_id) or ".." in run_id:
            return False
        with self._lock:
            h = self._runs.get(run_id)
        if h and h.status == "running":
            h.cancelled.set()
            return True
        return False

    def delete(self, run_id: str) -> bool:
        # E-H1: Validate run_id to prevent path traversal (../).
        if not _RUN_ID_RE.match(run_id) or ".." in run_id:
            return False
        import shutil
        from evals.storage.results import ResultStore
        store = ResultStore()
        run_dir = store.base_dir / run_id
        # E-H1: Resolve and verify the path stays under base_dir.
        try:
            run_dir.resolve().relative_to(store.base_dir.resolve())
        except (ValueError, OSError):
            return False
        if run_dir.exists():
            shutil.rmtree(run_dir, ignore_errors=True)
            return True
        return False

    def load_trace(self, run_id: str, task_id: str) -> Optional[dict]:
        """Load a single task's full trace from the trace JSON file."""
        # E-H1: Validate run_id and task_id to prevent path traversal.
        if not _RUN_ID_RE.match(run_id) or ".." in run_id:
            return None
        if not _RUN_ID_RE.match(task_id) or ".." in task_id:
            return None
        from evals.storage.results import ResultStore
        store = ResultStore()
        # E-H3: Read the actual trace file, not the per_task summary.
        trace_dir = store.base_dir / run_id / "traces"
        # Look for {task_id}__{rep}.json for any rep.
        for trace_file in sorted(trace_dir.glob(f"{task_id}__*.json")):
            try:
                return json.loads(trace_file.read_text())
            except Exception:
                pass
        # Fallback: if no trace file exists, return the per_task summary.
        report = store.load(run_id)
        if not report:
            return None
        for task in report.get("per_task", []):
            if task.get("task_id") == task_id:
                return task
        return None

    def list_datasets(self) -> list[dict]:
        """List available datasets."""
        # E-L1: Use REPO_ROOT-anchored path.
        if not _DATASETS_DIR.exists():
            return []
        result = []
        for f in sorted(_DATASETS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                tasks = data.get("tasks", [])
                # E-M4: Fall back to dataset "name" field if no "description".
                desc = data.get("description", "") or data.get("name", "")
                result.append({
                    "name": f.stem,
                    "description": desc,
                    "task_count": len(tasks),
                })
            except Exception:
                pass
        return result

    def compare(self, run_ids: list[str]) -> dict:
        """Compare multiple runs."""
        from evals.storage.results import ResultStore
        store = ResultStore()
        matrix = {}
        for run_id in run_ids:
            # E-H1: Validate run_id.
            if not _RUN_ID_RE.match(run_id) or ".." in run_id:
                continue
            report = store.load(run_id)
            if report:
                matrix[run_id] = {
                    "scorecard": report.get("scorecard", {}),
                    "per_task": [
                        {"task_id": t.get("task_id"), "scores": t.get("judge", {})}
                        for t in report.get("per_task", [])
                    ],
                }
        return matrix

    def trend(self, dataset: str, metric: str,
              metrics: list[str] | None = None,
              since: float | None = None, until: float | None = None) -> list[dict]:
        """Get metric trend across runs for a dataset.

        Extended: supports multi-metric via ``metrics`` list and time-range
        filtering via ``since``/``until``.
        """
        from evals.storage.results import ResultStore
        store = ResultStore()
        all_metrics = list(metrics) if metrics else [metric]
        points = []
        for run_id in store.list_runs():
            # E-H1: Validate run_id.
            if not _RUN_ID_RE.match(run_id) or ".." in run_id:
                continue
            report = store.load(run_id)
            if not report or report.get("dataset") != dataset:
                continue
            ts = report.get("started_at", 0)
            if since and ts < since:
                continue
            if until and ts > until:
                continue
            scorecard = report.get("scorecard", {})
            entry: dict = {"run_id": run_id, "ts": ts}
            for m in all_metrics:
                value = scorecard.get(m)
                if value is not None:
                    if isinstance(value, dict):
                        value = value.get("mean", 0.0)
                    entry[m] = value
            if len(all_metrics) == 1:
                entry["value"] = entry.get(all_metrics[0])
            points.append(entry)
        return sorted(points, key=lambda p: p["ts"])
