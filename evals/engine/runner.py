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

    def run_dataset(self, dataset: dict, opts: dict | None = None) -> dict:
        """Run all tasks in a dataset. Returns the full report dict."""
        opts = opts or {}
        run_id = opts.get("run_id", self._make_run_id(dataset))
        model = opts.get("model", os.environ.get("MODEL_ID", "unknown"))
        mode = opts.get("mode", "online")
        all_results: list[TaskResult] = []

        tasks = dataset.get("tasks", [])
        for task in tasks:
            repeat = task.get("repeat", 1) if mode != "mock" else 1
            for rep in range(repeat):
                result = self._run_one(task, rep, run_id, model, mode)
                all_results.append(result)

        # Aggregate
        from evals.report.aggregate import aggregate_results
        report = aggregate_results(all_results, dataset, run_id, model, mode, opts)
        return report

    def _run_one(self, task: dict, rep: int, run_id: str,
                 model: str, mode: str) -> TaskResult:
        """Run a single task instance."""
        task_id = task.get("id", "unknown")
        max_seconds = task.get("max_seconds", 120)

        # Offline mode: replay from record
        if mode == "offline" or task.get("mode") == "offline":
            return self._run_offline(task, rep, run_id, model)

        # Online or mock mode
        try:
            ws = self.workspace.isolate(task, run_id)
            trace = self._drive_agent(task, ws, model, mode, max_seconds)
            return self._score(trace, task, rep, run_id)
        except Exception as e:
            return TaskResult(task_id=task_id, rep=rep, status="error", error=str(e))

    def _drive_agent(self, task: dict, ws: Path, model: str,
                     mode: str, max_seconds: int) -> EvalTrace:
        """Drive the agent loop and collect trace."""
        from agent_core.env import set_workspace_dir, set_session_dir
        from agent_core.session import Session, DenyAllPermission
        from agent_core.loop import agent_loop
        from agent_core.adapter import chat_create
        from agent_core.context import update_context

        set_workspace_dir(ws)
        set_session_dir(ws)

        # Mock LLM
        if mode == "mock" or task.get("mode") == "mock":
            script = task.get("script", [])
            if script:
                from evals.engine.scripted_llm import ScriptedLLM, ScriptedResponse
                responses = [ScriptedResponse(content=s.get("content", [])) for s in script]
                mock = ScriptedLLM(responses)
                import agent_core.adapter as adapter
                self._orig_chat_create = adapter.chat_create
                adapter.chat_create = mock

        # Permission setup
        perm_config = task.get("permission", "allow_all")
        if perm_config == "deny_all":
            permission = DenyAllPermission()
        else:
            from agent_core.session import Permission
            class AllowAllPermission(Permission):
                def request(self, block): return {"allow": True, "modify": None}
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

        # Restore chat_create if mocked
        if self._orig_chat_create is not None:
            import agent_core.adapter as adapter
            adapter.chat_create = self._orig_chat_create
            self._orig_chat_create = None

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

        # Save trace
        trace_ref = ""
        try:
            trace_dir = Path("evals/results") / run_id / "traces"
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
