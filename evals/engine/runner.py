"""evals.engine.runner — evaluation execution engine.

EvalRunner loads a dataset, isolates each task, drives the agent,
collects traces, computes metrics, runs judges, and aggregates results.
"""
from __future__ import annotations

import json
import os
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.collectors.trace_model import EvalTrace
from evals.collectors.event_collector import EvalCollectorSink
from evals.collectors.trace_collector import TraceCollector
from evals.engine.workspace import WorkspaceIsolator
from evals.engine.scripted_llm import ScriptedLLM
from evals.judges.base import Judge, build_judge
from evals.metrics.base import compute_all_metrics

# Import all metric modules to trigger registration
import evals.metrics.tool_metrics
import evals.metrics.perf_metrics
import evals.metrics.safety_metrics
import evals.metrics.robustness_metrics
import evals.metrics.context_metrics
import evals.metrics.quality_metrics
import evals.metrics.cost_metrics
import evals.metrics.repeatability_metrics
import evals.metrics.memory_metrics
import evals.metrics.team_metrics

# E-L1: Anchor trace/results paths at REPO_ROOT instead of relative CWD so the
# runner works regardless of the process working directory.
try:
    from agent_core.paths import REPO_ROOT
except Exception:  # pragma: no cover — agent_core always present in practice
    REPO_ROOT = Path.cwd()

_RESULTS_ROOT = REPO_ROOT / "evals" / "results"

# Module-level AllowAllPermission (E-Q1: previously redefined inside _drive_agent
# on every call, creating a fresh class object per task).
try:
    from agent_core.session import Permission

    class AllowAllPermission(Permission):
        """Permit every tool_use without prompting. Used by the eval harness
        when a task opts into ``permission: allow_all``."""

        def request(self, block) -> dict:
            return {"allow": True, "modify": None}
except Exception:  # pragma: no cover — agent_core absent in some unit contexts
    class AllowAllPermission:  # type: ignore[no-redef]
        def request(self, block) -> dict:
            return {"allow": True, "modify": None}


@dataclass
class TaskResult:
    """Result of evaluating one task (one repeat)."""
    task_id: str
    rep: int = 0
    metrics: dict = field(default_factory=dict)
    judge: dict = field(default_factory=dict)
    trace_ref: str = ""
    status: str = "ok"       # ok / error / timeout / skipped
    error: str = ""


class EvalRunner:
    """Run evaluation datasets with isolation, metrics, and judging."""

    def __init__(self, workspace: WorkspaceIsolator | None = None):
        self.workspace = workspace or WorkspaceIsolator()
        self._orig_chat_create = None
        # E-A3: Lock that serializes mock-LLM installation/restore. Mock tasks
        # monkeypatch the shared ``adapter.chat_create``; running two mock tasks
        # concurrently on a shared EvalRunner instance would let one thread
        # overwrite the other's script. Online/offline tasks are unaffected.
        self._mock_lock = threading.Lock()

    def run_dataset(self, dataset: dict, opts: dict | None = None,
                    on_progress: Any = None) -> dict:
        """Run all tasks in a dataset. Returns the full report dict.

        Args:
            on_progress: optional callback ``fn(event_dict)`` called after
                each task completes with ``{task_id, rep, status, ...}``.

        Recognized ``opts`` keys:
            run_id, model, mode, repeat, limit,
            cancel_event   — threading.Event; checked between tasks and watched
                             during each task to interrupt the agent loop ASAP,
            only_failed_from — run_id of a previous run; only tasks that failed
                             or errored in that run are re-evaluated,
            regression_baseline — run_id of a baseline run; after completion the
                             report gains a ``regression`` section listing
                             regressed and fixed task ids.
        """
        opts = opts or {}
        run_id = opts.get("run_id", self._make_run_id(dataset))
        model = opts.get("model", os.environ.get("MODEL_ID", "unknown"))
        mode = opts.get("mode", "online")
        # E-M2: Honor repeat and limit from opts.
        global_repeat = max(1, int(opts.get("repeat", 1)))
        limit = int(opts.get("limit", 0))
        cancel_event: threading.Event | None = opts.get("cancel_event")
        all_results: list[TaskResult] = []

        tasks = dataset.get("tasks", [])
        # E-M2: Apply limit slicing if specified.
        if limit > 0:
            tasks = tasks[:limit]

        # E-F10: Incremental mode — only re-run tasks that failed/errored in a
        # previous run. Enables fast feedback loops without re-running greens.
        only_failed_from = opts.get("only_failed_from")
        if only_failed_from:
            tasks = self._filter_to_failed(tasks, only_failed_from)

        for task in tasks:
            # E-F5: Cooperative cancel between tasks.
            if cancel_event is not None and cancel_event.is_set():
                break
            repeat = task.get("repeat", 1) if mode != "mock" else 1
            # E-M2: Multiply per-task repeat by global repeat factor.
            repeat = max(1, repeat * global_repeat) if mode != "mock" else 1
            for rep in range(repeat):
                if cancel_event is not None and cancel_event.is_set():
                    break
                result = self._run_one(task, rep, run_id, model, mode,
                                       cancel_event=cancel_event)
                all_results.append(result)
                # E-H2: Fire progress callback after each task.
                if on_progress is not None:
                    try:
                        on_progress({
                            "task_id": result.task_id,
                            "rep": result.rep,
                            "status": result.status,
                            "error": result.error,
                        })
                    except Exception:
                        pass

        # Aggregate
        from evals.report.aggregate import aggregate_results
        report = aggregate_results(all_results, dataset, run_id, model, mode, opts)

        # E-F10: Regression detection against a baseline run.
        baseline_run_id = opts.get("regression_baseline")
        if baseline_run_id:
            report["regression"] = self._detect_regression(report, baseline_run_id)

        return report

    def _filter_to_failed(self, tasks: list[dict], run_id: str) -> list[dict]:
        """Keep only tasks that failed or errored in the given prior run."""
        from evals.storage.results import ResultStore
        baseline = ResultStore().load(run_id)
        if not baseline:
            return tasks  # baseline missing → run everything (safe default)
        failed_ids: set[str] = set()
        for t in baseline.get("per_task", []):
            if t.get("status") != "ok" or t.get("pass_count", 0) == 0:
                failed_ids.add(t.get("task_id", ""))
        for r in baseline.get("results", []):
            if r.get("status") != "ok":
                failed_ids.add(r.get("task_id", ""))
        return [t for t in tasks if t.get("id") in failed_ids]

    def _detect_regression(self, current: dict, baseline_run_id: str) -> dict:
        """Compare current report to a baseline; list regressed + fixed tasks."""
        from evals.storage.results import ResultStore
        baseline = ResultStore().load(baseline_run_id)
        if not baseline:
            return {"regressed": [], "fixed": [], "baseline_run_id": baseline_run_id,
                    "note": "baseline run not found"}
        cur_pass = {t.get("task_id"): (t.get("pass_count", 0) > 0)
                    for t in current.get("per_task", [])}
        base_pass = {t.get("task_id"): (t.get("pass_count", 0) > 0)
                     for t in baseline.get("per_task", [])}
        regressed = [tid for tid in cur_pass
                     if base_pass.get(tid, False) and not cur_pass[tid]]
        fixed = [tid for tid in cur_pass
                 if not base_pass.get(tid, True) and cur_pass[tid]]
        return {"regressed": regressed, "fixed": fixed,
                "baseline_run_id": baseline.get("run_id", baseline_run_id)}

    def _run_one(self, task: dict, rep: int, run_id: str,
                 model: str, mode: str,
                 cancel_event: threading.Event | None = None) -> TaskResult:
        """Run a single task instance."""
        task_id = task.get("id", "unknown")
        max_seconds = task.get("max_seconds", 120)

        # Offline mode: replay from record
        if mode == "offline" or task.get("mode") == "offline":
            return self._run_offline(task, rep, run_id, model)

        # Online or mock mode
        try:
            ws = self.workspace.isolate(task, run_id)
            trace = self._drive_agent(task, ws, model, mode, max_seconds,
                                      cancel_event=cancel_event)
            return self._score(trace, task, rep, run_id)
        except Exception as e:
            return TaskResult(task_id=task_id, rep=rep, status="error", error=str(e))

    def _drive_agent(self, task: dict, ws: Path, model: str,
                     mode: str, max_seconds: int,
                     cancel_event: threading.Event | None = None) -> EvalTrace:
        """Drive the agent loop and collect trace.

        E-A2: Only binds the per-session dir (``set_session_dir``); the shared
        ``workspace_dir()`` is left untouched so other chat sessions' .memory/,
        skills/, .permissions/ keep resolving to the real workspace. The session
        binding is cleared in ``finally`` so pool-reused threads don't inherit a
        stale root.

        E-A3: Mock-LLM monkeypatch is serialized by ``self._mock_lock`` to avoid
        two concurrent mock tasks racing on ``adapter.chat_create``.

        E-F5: When ``cancel_event`` is provided a lightweight watcher flips
        ``sess.interrupted`` so ``agent_loop`` stops at the next turn boundary.
        """
        from agent_core.env import set_session_dir, clear_session_dir
        from agent_core.session import Session, DenyAllPermission
        from agent_core.loop import agent_loop
        from agent_core.context import update_context

        is_mock = mode == "mock" or task.get("mode") == "mock"

        # E-A2: bind only the session dir, not the shared workspace.
        set_session_dir(ws)

        watcher_thread: threading.Thread | None = None
        done_event = threading.Event()
        try:
            # Mock LLM — serialized so concurrent mock tasks don't race on the
            # shared adapter.chat_create slot (E-A3).
            if is_mock:
                with self._mock_lock:
                    return self._drive_with_mock(
                        task, ws, model, mode, max_seconds, cancel_event,
                        done_event, set_session_dir, clear_session_dir,
                        Session, DenyAllPermission, agent_loop, update_context,
                    )
            return self._drive_inner(
                task, ws, model, mode, max_seconds, cancel_event, done_event,
                Session, DenyAllPermission, agent_loop, update_context,
            )
        finally:
            # Wake the cancel watcher so it can exit promptly.
            done_event.set()
            # E-A2: restore the thread's session binding to the default.
            clear_session_dir()

    def _drive_inner(self, task, ws, model, mode, max_seconds, cancel_event,
                     done_event, Session, DenyAllPermission, agent_loop,
                     update_context) -> EvalTrace:
        """Run agent_loop without a mock LLM."""
        return self._drive_common(
            task, ws, model, mode, max_seconds, cancel_event, done_event,
            Session, DenyAllPermission, agent_loop, update_context, mock=None,
        )

    def _drive_with_mock(self, task, ws, model, mode, max_seconds, cancel_event,
                         done_event, set_session_dir, clear_session_dir,
                         Session, DenyAllPermission, agent_loop,
                         update_context) -> EvalTrace:
        """Run agent_loop with a scripted mock LLM. Caller holds _mock_lock."""
        import agent_core.adapter as adapter
        script = task.get("script", [])
        responses = []
        if script:
            from evals.engine.scripted_llm import ScriptedResponse
            responses = [ScriptedResponse(content=s.get("content", [])) for s in script]
        mock = ScriptedLLM(responses)
        self._orig_chat_create = adapter.chat_create
        adapter.chat_create = mock
        try:
            return self._drive_common(
                task, ws, model, mode, max_seconds, cancel_event, done_event,
                Session, DenyAllPermission, agent_loop, update_context, mock=mock,
            )
        finally:
            adapter.chat_create = self._orig_chat_create
            self._orig_chat_create = None

    def _drive_common(self, task, ws, model, mode, max_seconds, cancel_event,
                      done_event, Session, DenyAllPermission, agent_loop,
                      update_context, mock) -> EvalTrace:
        """Shared agent-driving logic (session, permission, timeout, trace)."""
        from evals.collectors.event_collector import EvalCollectorSink

        # Permission setup
        perm_config = task.get("permission", "allow_all")
        if perm_config == "deny_all":
            permission = DenyAllPermission()
        else:
            permission = AllowAllPermission()

        # Create session with collector
        collector = EvalCollectorSink()
        sess = Session(
            sinks=[collector],
            permission=permission,
            context=update_context({}, []),
        )
        sess.workdir = ws

        # Add user prompt
        prompt = task.get("prompt", "")
        sess.append_both({"role": "user", "content": [{"type": "text", "text": prompt}]})

        # E-F5: cancel watcher — flips sess.interrupted when cancel_event fires.
        if cancel_event is not None:
            def _watch():
                while not done_event.is_set():
                    if cancel_event.wait(timeout=0.5):
                        sess.interrupted = True
                        return
            watcher = threading.Thread(target=_watch, daemon=True)
            watcher.start()

        # Run with timeout
        result_holder = {"done": False, "error": None}
        def _run():
            try:
                agent_loop(sess)
            except Exception as e:
                result_holder["error"] = e
            finally:
                result_holder["done"] = True

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=max_seconds)

        if t.is_alive():
            # Timeout — interrupt
            sess.interrupted = True
            t.join(timeout=5)

        # Finalize trace
        trace = collector.finalize(
            task_id=task.get("id", ""),
            mode=mode,
            record=sess.record,
            meta={"model": model, "workspace": str(ws)},
        )

        # Merge tracer spans
        try:
            TraceCollector().merge_into(trace)
        except Exception:
            pass

        return trace

    def _run_offline(self, task: dict, rep: int, run_id: str, model: str) -> TaskResult:
        """Replay from a persisted record."""
        from evals.collectors.record_replayer import RecordReplayer
        replayer = RecordReplayer()
        record_path = task.get("record_path", "")
        if record_path:
            trace = replayer.from_history_json(record_path, task_id=task.get("id", ""))
        else:
            return TaskResult(task_id=task.get("id", ""), rep=rep,
                             status="skipped", error="no record_path for offline mode")
        return self._score(trace, task, rep, run_id)

    def _score(self, trace: EvalTrace, task: dict, rep: int, run_id: str) -> TaskResult:
        """Compute metrics and run judge."""
        task_id = task.get("id", "")

        # Compute all metrics
        metrics = compute_all_metrics(trace, task)

        # Run judge
        judge_spec = task.get("judge", {"type": "rule", "rules": []})
        try:
            judge = build_judge(judge_spec)
            judge_result = judge.run(trace, metrics, task)
            judge_dict = {
                "score": judge_result.score,
                "passed": judge_result.passed,
                "reasoning": judge_result.reasoning,
                "details": judge_result.details,
            }
        except Exception as e:
            judge_dict = {"score": 0.0, "passed": False, "error": str(e)}

        # Save trace (E-L1: REPO_ROOT-anchored path).
        trace_ref = ""
        try:
            trace_dir = _RESULTS_ROOT / run_id / "traces"
            trace_dir.mkdir(parents=True, exist_ok=True)
            trace_file = trace_dir / f"{task_id}__{rep}.json"
            trace_file.write_text(json.dumps(trace.to_dict(), indent=2, default=str))
            trace_ref = str(trace_file)
        except Exception:
            pass

        return TaskResult(
            task_id=task_id, rep=rep,
            metrics=metrics, judge=judge_dict,
            trace_ref=trace_ref, status="ok",
        )

    def _make_run_id(self, dataset: dict) -> str:
        import subprocess
        try:
            sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                          stderr=subprocess.DEVNULL).decode().strip()
        except Exception:
            sha = "nosha"
        name = dataset.get("name", "dataset")
        model = os.environ.get("MODEL_ID", "model")
        ts = time.strftime("%Y%m%d_%H%M%S")
        return f"{sha}_{name}_{model}_{ts}"
