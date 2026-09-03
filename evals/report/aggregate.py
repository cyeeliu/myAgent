"""evals.report.aggregate — scorecard aggregation across task results."""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any


def aggregate_results(results: list, dataset: dict, run_id: str,
                       model: str, mode: str, opts: dict) -> dict:
    """Aggregate task results into a full report."""
    # Collect per-task results
    per_task: dict[str, list] = defaultdict(list)
    for r in results:
        per_task[r.task_id].append(r)

    # Scorecard: average key metrics across all tasks
    scorecard: dict[str, Any] = {}
    metric_values: dict[str, list] = defaultdict(list)

    for r in results:
        if r.status != "ok":
            continue
        # Flatten metrics
        for name, val in r.metrics.items():
            if name.startswith("_"):
                continue
            if isinstance(val, dict):
                # Extract rate/score/freq if present
                for key in ("rate", "score", "freq", "peak"):
                    if key in val and isinstance(val[key], (int, float)):
                        metric_values[f"{name}.{key}"].append(val[key])
            elif isinstance(val, (int, float)):
                metric_values[name].append(val)

    for name, vals in metric_values.items():
        if vals:
            scorecard[name] = {
                "mean": statistics.mean(vals),
                "stddev": statistics.stdev(vals) if len(vals) > 1 else 0.0,
                "min": min(vals),
                "max": max(vals),
                "count": len(vals),
            }

    # Judge scores
    judge_scores = [r.judge.get("score", 0.0) for r in results if r.status == "ok"]
    judge_passed = sum(1 for r in results if r.judge.get("passed", False))
    judge_total = sum(1 for r in results if r.status == "ok")

    # Per-task summary
    task_summary = []
    # E-Q14: aggregate-level repeatability. The per-trace repeatability_metrics
    # functions return "n/a" for a single trace because cross-repeat scores are
    # only known after aggregation. We compute determinism / pass@k here (over
    # each task's repeats) and surface them on per_task + the scorecard, so the
    # repeatability metrics are populated rather than dead code.
    rep_det_values: list[float] = []
    rep_passk_values: list[float] = []
    for task_id, reps in per_task.items():
        scores = [r.judge.get("score", 0.0) for r in reps if r.status == "ok"]
        passed_flags = [r.judge.get("passed", False) for r in reps if r.status == "ok"]
        entry = {
            "task_id": task_id,
            "reps": len(reps),
            "mean_score": statistics.mean(scores) if scores else 0.0,
            "pass_count": sum(1 for p in passed_flags if p),
            "status": "ok" if all(r.status == "ok" for r in reps) else "has_errors",
        }
        if len(scores) >= 2:
            std = statistics.stdev(scores)
            det = max(0.0, 1.0 - std)
            entry["repeatability"] = {
                "determinism": det,
                "stddev": std,
                "n": len(scores),
                "pass_at_k": (sum(1 for p in passed_flags if p) / len(scores)),
            }
            rep_det_values.append(det)
            rep_passk_values.append(entry["repeatability"]["pass_at_k"])
        elif scores:
            entry["repeatability"] = {"determinism": 1.0, "n": len(scores),
                                      "note": "need >=2 runs for stddev"}
        task_summary.append(entry)

    # Surface aggregate repeatability in the scorecard too.
    if rep_det_values:
        scorecard["repeatability.determinism"] = {
            "mean": statistics.mean(rep_det_values),
            "stddev": statistics.stdev(rep_det_values) if len(rep_det_values) > 1 else 0.0,
            "min": min(rep_det_values), "max": max(rep_det_values),
            "count": len(rep_det_values),
        }
    if rep_passk_values:
        scorecard["repeatability.pass_at_k"] = {
            "mean": statistics.mean(rep_passk_values),
            "stddev": statistics.stdev(rep_passk_values) if len(rep_passk_values) > 1 else 0.0,
            "min": min(rep_passk_values), "max": max(rep_passk_values),
            "count": len(rep_passk_values),
        }

    return {
        "run_id": run_id,
        "dataset": dataset.get("name", "unknown"),
        "model": model,
        "mode": mode,
        "total_tasks": len(per_task),
        "total_runs": len(results),
        "judge_pass_rate": judge_passed / judge_total if judge_total else 0.0,
        "judge_mean_score": statistics.mean(judge_scores) if judge_scores else 0.0,
        "scorecard": scorecard,
        "per_task": task_summary,
        "results": [
            {
                "task_id": r.task_id, "rep": r.rep, "status": r.status,
                "judge": r.judge, "error": r.error,
            }
            for r in results
        ],
        "opts": opts,
    }
