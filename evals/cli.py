"""evals.cli — command-line interface for the evaluation framework.

Usage:
    python -m evals.cli run --dataset tool_success --model glm-5 --repeat 5
    python -m evals.cli run --dataset tool_selection --mode mock --parallel --max-workers 8
    python -m evals.cli run --dataset tool_success --only-failed <run_id>
    python -m evals.cli run --dataset tool_success --regression-baseline <run_id>
    python -m evals.cli replay --session <sid> --dataset tool_selection
    python -m evals.cli compare --run <run_id_a> --against <run_id_b> --html
    python -m evals.cli trend --dataset tool_selection --last 20
    python -m evals.cli dataset validate --dataset tool_success
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# E-L1: anchor dataset/results paths at REPO_ROOT so the CLI works regardless
# of the process working directory.
try:
    from agent_core.paths import REPO_ROOT
except Exception:  # pragma: no cover
    REPO_ROOT = Path.cwd()

_DATASETS_ROOT = REPO_ROOT / "evals" / "datasets"


def _load_dataset(name: str) -> dict:
    """Load a dataset by name from evals/datasets/ or a direct file path."""
    # E-L1: try the REPO_ROOT-anchored datasets dir first.
    path = _DATASETS_ROOT / f"{name}.json"
    if not path.exists():
        # Try as a direct file path (absolute or relative to CWD).
        path = Path(name)
        if not path.exists():
            raise FileNotFoundError(f"dataset not found: {name}")
    return json.loads(path.read_text())


def cmd_run(args):
    """Run an evaluation dataset."""
    from evals.storage.results import ResultStore
    from evals.report.render import render_markdown, render_summary, render_html

    dataset = _load_dataset(args.dataset)

    # E-Q12: inject a dataset-wide judge model into each task so LLMJudge can
    # pick it up without per-task repetition.
    if args.judge_model:
        for task in dataset.get("tasks", []):
            task.setdefault("judge_model", args.judge_model)

    opts: dict = {
        "model": args.model,
        "mode": args.mode,
        "run_id": args.run_id,
    }
    # E-M2: pass repeat/limit through opts instead of slicing the dataset
    # in-process, so the runner (serial or parallel) owns the slicing logic.
    if args.repeat and args.repeat > 1:
        opts["repeat"] = args.repeat
    if args.limit:
        opts["limit"] = args.limit
    # E-F10: incremental + regression options.
    if args.only_failed:
        opts["only_failed_from"] = args.only_failed
    if args.regression_baseline:
        opts["regression_baseline"] = args.regression_baseline
    # E-A4: parallel concurrency.
    if args.parallel:
        opts["max_workers"] = args.max_workers

    if args.parallel:
        from evals.engine.parallel import ParallelRunner
        runner = ParallelRunner()
    else:
        from evals.engine.runner import EvalRunner
        runner = EvalRunner()

    report = runner.run_dataset(dataset, opts)

    # Save
    store = ResultStore()
    out_dir = store.save(report)
    render_markdown(report, out_dir)
    render_html(report, out_dir)  # E-F7: HTML scorecard

    print(render_summary(report))
    print(f"Report saved to: {out_dir}")
    if report.get("regression"):
        reg = report["regression"]
        print(f"Regression vs {reg.get('baseline_run_id', '?')}: "
              f"{len(reg.get('regressed', []))} regressed, "
              f"{len(reg.get('fixed', []))} fixed")
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
    """Compare two runs (text + optional HTML report)."""
    from evals.storage.results import ResultStore
    from evals.report.render import render_compare_html
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

    # E-F7: optional HTML comparison report.
    if args.html:
        out_dir = store.base_dir / "comparisons" / f"{args.run_a}__vs__{args.run_b}"
        html_path = render_compare_html(a, b, out_dir,
                                        label_a=args.run_a, label_b=args.run_b)
        print(f"\nHTML comparison report: {html_path}")


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


def cmd_dataset(args):
    """Dataset management subcommands (E-F8: schema validation + tooling)."""
    from evals.datasets.schema import validate_dataset, format_errors
    if args.dataset_cmd == "validate":
        dataset = _load_dataset(args.dataset)
        errors = validate_dataset(dataset)
        if not errors:
            n = len(dataset.get("tasks", []))
            print(f"✓ dataset '{dataset.get('name', args.dataset)}' is valid ({n} tasks)")
            return 0
        print(f"✗ dataset '{args.dataset}' has {len(errors)} error(s):")
        print(format_errors(errors))
        return 1
    elif args.dataset_cmd == "list":
        if not _DATASETS_ROOT.exists():
            print("No datasets directory found.")
            return 0
        for p in sorted(_DATASETS_ROOT.glob("*.json")):
            try:
                d = json.loads(p.read_text())
                n = len(d.get("tasks", []))
                print(f"  {p.stem}: {d.get('name', '?')} ({n} tasks)")
            except Exception as e:
                print(f"  {p.stem}: <invalid: {e}>")
        return 0
    print(f"unknown dataset command: {args.dataset_cmd}")
    return 2


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
    # E-A4: parallel execution.
    p_run.add_argument("--parallel", action="store_true", help="Run tasks in parallel")
    p_run.add_argument("--max-workers", type=int, default=4,
                       help="Thread pool size (parallel mode)")
    # E-F10: incremental + regression.
    p_run.add_argument("--only-failed", default=None, metavar="RUN_ID",
                       help="Only re-run tasks that failed in this prior run")
    p_run.add_argument("--regression-baseline", default=None, metavar="RUN_ID",
                       help="Detect regressions vs this baseline run")
    # E-Q12: judge model override.
    p_run.add_argument("--judge-model", default=None,
                       help="Override LLM judge model for llm_judge tasks")
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
    p_cmp.add_argument("--html", action="store_true",
                       help="Also write an HTML comparison report")
    p_cmp.set_defaults(func=cmd_compare)

    # trend
    p_trend = sub.add_parser("trend", help="Show trend across runs")
    p_trend.add_argument("--dataset", default=None, help="Filter by dataset")
    p_trend.add_argument("--last", type=int, default=20, help="Number of runs")
    p_trend.set_defaults(func=cmd_trend)

    # dataset (E-F8)
    p_ds = sub.add_parser("dataset", help="Dataset management")
    ds_sub = p_ds.add_subparsers(dest="dataset_cmd", required=True)
    p_ds_val = ds_sub.add_parser("validate", help="Validate a dataset schema")
    p_ds_val.add_argument("--dataset", required=True, help="Dataset name or path")
    ds_sub.add_parser("list", help="List available datasets")
    p_ds.set_defaults(func=cmd_dataset)

    args = parser.parse_args()
    result = args.func(args)
    if isinstance(result, int):
        sys.exit(result)


if __name__ == "__main__":
    main()
