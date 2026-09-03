"""evals.engine.parallel — concurrent task execution."""
from __future__ import annotations

import concurrent.futures
import threading
from typing import Any

from evals.engine.runner import EvalRunner, TaskResult


class ParallelRunner(EvalRunner):
    """Run tasks concurrently with thread or process pool."""

    def run_dataset(self, dataset: dict, opts: dict | None = None,
                    max_workers: int = 4) -> dict:
        """Run all tasks in parallel."""
        opts = opts or {}
        run_id = opts.get("run_id", self._make_run_id(dataset))
        model = opts.get("model", "unknown")
        mode = opts.get("mode", "online")

        tasks = dataset.get("tasks", [])
        # Build work items: (task, rep)
        work_items = []
        for task in tasks:
            repeat = task.get("repeat", 1) if mode != "mock" else 1
            for rep in range(repeat):
                work_items.append((task, rep))

        results: list[TaskResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._run_one, task, rep, run_id, model, mode): (task, rep)
                for task, rep in work_items
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    task, rep = futures[future]
                    results.append(TaskResult(
                        task_id=task.get("id", "?"), rep=rep,
                        status="error", error=str(e),
                    ))

        from evals.report.aggregate import aggregate_results
        return aggregate_results(results, dataset, run_id, model, mode, opts)
