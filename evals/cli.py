"""evals.cli — command-line interface for the evaluation framework.

Usage:
    python -m evals.cli run --dataset tool_success --model glm-5 --repeat 5
    python -m evals.cli run --dataset tool_selection --mode mock
    python -m evals.cli replay --session <sid> --dataset tool_selection
    python -m evals.cli compare --run <run_id_a> --run <run_id_b>
    python -m evals.cli trend --dataset tool_selection --last 20
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_dataset(name: str) -> dict:
    """Load a dataset by name from evals/datasets/."""
    path = Path("evals/datasets") / f"{name}.json"
    if not path.exists():
        # Try as a direct file path
        path = Path(name)
        if not path.exists():
            raise FileNotFoundError(f"dataset not found: {name}")
    return json.loads(path.read_text())


def cmd_run(args):
    """Run an evaluation dataset."""
    from evals.engine.runner import EvalRunner
    from evals.report.render import render_json, render_markdown, render_summary
    from evals.storage.results import ResultStore

    dataset = _load_dataset(args.dataset)
    runner = EvalRunner()
    opts = {
        "model": args.model,
        "mode": args.mode,
        "run_id": args.run_id,
    }
    if args.limit:
        dataset["tasks"] = dataset.get("tasks", [])[:args.limit]

    report = runner.run_dataset(dataset, opts)

    # Save
    store = ResultStore()
    out_dir = store.save(report)
    render_markdown(report, out_dir)

    print(render_summary(report))
    print(f"Report saved to: {out_dir}")
    return report


def cmd_replay(args):
    """Replay from a persisted session record."""
    from evals.collectors.record_replayer import RecordReplayer
    from evals.metrics.base import compute_all_metrics
    import evals.metrics.tool_metrics
    import evals.metrics.perf_metrics
    import evals.metrics.quality_metrics

    replayer = RecordReplayer()
    trace = replayer.from_postgres(args.session)
    metrics = compute_all_metrics(trace)

    print(f"Session: {args.session}")
    print(f"Mode: {trace.mode}")
    print(f"Tool calls: {len(trace.tool_calls)}")
    print(f"Turns: {len(trace.turns)}")
    print("\nMetrics:")
    for name, val in metrics.items():
        if name.startswith("_"):
            continue
        print(f"  {name}: {val}")
    return metrics


def cmd_compare(args):
    """Compare two runs."""
    from evals.storage.results import ResultStore
    store = ResultStore()
    a = store.load(args.run_a)
    b = store.load(args.run_b)
    if not a or not b:
        print("Error: one or both runs not found")
        return

    print(f"Comparison: {args.run_a} vs {args.run_b}")
    print(f"  Pass rate: {a.get('judge_pass_rate', 0):.1%} → {b.get('judge_pass_rate', 0):.1%}")
    print(f"  Mean score: {a.get('judge_mean_score', 0):.2f} → {b.get('judge_mean_score', 0):.2f}")

    # Compare scorecard
    sc_a = a.get("scorecard", {})
    sc_b = b.get("scorecard", {})
    all_keys = sorted(set(sc_a) | set(sc_b))
    print("\n  Metric changes:")
    for key in all_keys:
        va = sc_a.get(key, {}).get("mean", 0)
        vb = sc_b.get(key, {}).get("mean", 0)
        delta = vb - va
        marker = "↑" if delta > 0.01 else "↓" if delta < -0.01 else "="
        print(f"    {key}: {va:.4f} → {vb:.4f} ({marker})")


def cmd_trend(args):
    """Show trend across recent runs."""
    from evals.storage.results import ResultStore
    store = ResultStore()
    runs = store.list_runs()[-args.last:]

    print(f"Trend (last {len(runs)} runs):")
    for run_id in runs:
        report = store.load(run_id)
        if report:
            print(f"  {run_id}: pass={report.get('judge_pass_rate', 0):.1%}, "
                  f"score={report.get('judge_mean_score', 0):.2f}")


def main():
    parser = argparse.ArgumentParser(description="Agent evaluation framework")
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Run an evaluation dataset")
    p_run.add_argument("--dataset", required=True, help="Dataset name or path")
    p_run.add_argument("--model", default=None, help="Model ID")
    p_run.add_argument("--mode", default="online", choices=["online", "offline", "mock"])
    p_run.add_argument("--repeat", type=int, default=1, help="pass@k repeats")
    p_run.add_argument("--limit", type=int, default=None, help="Limit tasks")
    p_run.add_argument("--run-id", default=None, help="Custom run ID")
    p_run.set_defaults(func=cmd_run)

    # replay
    p_replay = sub.add_parser("replay", help="Replay from a session record")
    p_replay.add_argument("--session", required=True, help="Session ID")
    p_replay.add_argument("--dataset", default=None, help="Dataset for judge specs")
    p_replay.set_defaults(func=cmd_replay)

    # compare
    p_cmp = sub.add_parser("compare", help="Compare two runs")
    p_cmp.add_argument("--run", dest="run_a", required=True, help="First run ID")
    p_cmp.add_argument("--against", dest="run_b", required=True, help="Second run ID")
    p_cmp.set_defaults(func=cmd_compare)

    # trend
    p_trend = sub.add_parser("trend", help="Show trend across runs")
    p_trend.add_argument("--dataset", default=None, help="Filter by dataset")
    p_trend.add_argument("--last", type=int, default=20, help="Number of runs")
    p_trend.set_defaults(func=cmd_trend)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
