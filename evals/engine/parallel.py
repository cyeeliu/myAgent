"""evals.engine.parallel — concurrent task execution.

ParallelRunner extends EvalRunner with a thread-pool executor. It reuses the
parent's per-task logic (isolation, mock-LLM serialization via ``_mock_lock``,
cancel watching, scoring, regression detection) and only swaps the task loop
for a ``ThreadPoolExecutor``.

E-A4: ``max_workers`` is read from ``opts["max_workers"]`` (default 4) instead
of being a hard-coded parameter, so callers (CLI/API) can tune concurrency.
E-A3: mock tasks are still serialized by the inherited ``_mock_lock`` — they
won't run concurrently with each other, but online/offline tasks parallelize
freely. This is a safe trade-off (mocks are fast/deterministic).
E-F5: ``opts["cancel_event"]`` is honored: pending futures are cancelled and
the pool drains promptly when the event is set.
"""
from __future__ import annotations

import concurrent.futures
import threading
from typing import Any

from evals.engine.runner import EvalRunner, TaskResult


class ParallelRunner(EvalRunner):
    """Run tasks concurrently with a thread pool.

    Shares the parent ``EvalRunner`` instance state (workspace isolator,
    ``_mock_lock``). Because mock-LLM monkeypatching mutates the shared
    ``adapter.chat_create`` slot, concurrent mock tasks are serialized by
    ``_mock_lock`` inherited from ``EvalRunner``; online/offline tasks run in
    parallel up to ``max_workers``.
    """

    def run_dataset(self, dataset: dict, opts: dict | None = None,
                    on_progress: Any = None) -> dict:
        """Run all tasks in parallel.

        Recognized ``opts`` keys (in addition to those honored by the serial
        ``EvalRunner.run_dataset``):
            max_workers      — thread-pool size (default 4; E-A4).
        All other opts (run_id, model, mode, repeat, limit, cancel_event,
        only_failed_from, regression_baseline) are honored exactly as in the
        serial runner.
        """
        import os
        opts = opts or {}
        run_id = opts.get("run_id", self._make_run_id(dataset))
        model = opts.get("model", os.environ.get("MODEL_ID", "unknown"))
        mode = opts.get("mode", "online")
        # E-A4: read concurrency from opts instead of a hard-coded param.
        max_workers = max(1, int(opts.get("max_workers", 4)))
        global_repeat = max(1, int(opts.get("repeat", 1)))
        limit = int(opts.get("limit", 0))
        cancel_event: threading.Event | None = opts.get("cancel_event")

        tasks = dataset.get("tasks", [])
        if limit > 0:
            tasks = tasks[:limit]

        # E-F10: incremental mode — only re-run failed/errored tasks.
        only_failed_from = opts.get("only_failed_from")
        if only_failed_from:
            tasks = self._filter_to_failed(tasks, only_failed_from)

        # Build work items: (task, rep)
        work_items: list[tuple[dict, int]] = []
        for task in tasks:
            repeat = task.get("repeat", 1) if mode != "mock" else 1
            repeat = max(1, repeat * global_repeat) if mode != "mock" else 1
            for rep in range(repeat):
                work_items.append((task, rep))

        results: list[TaskResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures: dict = {}
            for task, rep in work_items:
                # E-F5: stop submitting once cancelled.
                if cancel_event is not None and cancel_event.is_set():
                    break
                fut = pool.submit(
                    self._run_one, task, rep, run_id, model, mode,
                    cancel_event=cancel_event,
                )
                futures[fut] = (task, rep)

            for future in concurrent.futures.as_completed(futures):
                # E-F5: short-circuit draining when cancelled.
                if cancel_event is not None and cancel_event.is_set():
                    # Cancel not-yet-started futures; collect started ones.
                    for f in futures:
                        if not f.done():
                            f.cancel()
                    break
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    task, rep = futures[future]
                    results.append(TaskResult(
                        task_id=task.get("id", "?"), rep=rep,
                        status="error", error=str(e),
                    ))
                # E-H2: progress callback after each completion.
                if on_progress is not None and results:
                    try:
                        r = results[-1]
                        on_progress({
                            "task_id": r.task_id, "rep": r.rep,
                            "status": r.status, "error": r.error,
                        })
                    except Exception:
                        pass

        # Aggregate (same path as the serial runner).
        from evals.report.aggregate import aggregate_results
        report = aggregate_results(results, dataset, run_id, model, mode, opts)

        # E-F10: regression detection against a baseline run.
        baseline_run_id = opts.get("regression_baseline")
        if baseline_run_id:
            report["regression"] = self._detect_regression(report, baseline_run_id)

        return report
