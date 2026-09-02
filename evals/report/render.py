"""evals.report.render — JSON and Markdown report rendering."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def render_json(report: dict, output_dir: str | Path) -> Path:
    """Write report as JSON."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "report.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    return path


def render_markdown(report: dict, output_dir: str | Path) -> Path:
    """Write report as Markdown scorecard."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "report.md"

    lines = [
        f"# Evaluation Report — {report.get('run_id', '?')}",
        "",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| Dataset | {report.get('dataset', '?')} |",
        f"| Model | {report.get('model', '?')} |",
        f"| Mode | {report.get('mode', '?')} |",
        f"| Tasks | {report.get('total_tasks', 0)} |",
        f"| Runs | {report.get('total_runs', 0)} |",
        f"| Pass Rate | {report.get('judge_pass_rate', 0):.1%} |",
        f"| Mean Score | {report.get('judge_mean_score', 0):.2f} |",
        "",
        "## Scorecard",
        "",
        "| Metric | Mean | StdDev | Min | Max |",
        "|--------|------|--------|-----|-----|",
    ]

    for name, stats in sorted(report.get("scorecard", {}).items()):
        lines.append(
            f"| {name} | {stats['mean']:.4f} | {stats['stddev']:.4f} | "
            f"{stats['min']:.4f} | {stats['max']:.4f} |"
        )

    lines.extend(["", "## Per-Task Summary", "",
                   "| Task | Reps | Mean Score | Pass Count | Status |",
                   "|------|------|------------|------------|--------|"])

    for t in report.get("per_task", []):
        lines.append(
            f"| {t['task_id']} | {t['reps']} | {t['mean_score']:.2f} | "
            f"{t['pass_count']} | {t['status']} |"
        )

    # Failed tasks detail
    failed = [r for r in report.get("results", []) if r.get("status") != "ok"]
    if failed:
        lines.extend(["", "## Failed Tasks", ""])
        for r in failed:
            lines.append(f"- **{r['task_id']}** (rep {r['rep']}): {r.get('error', 'unknown')}")

    path.write_text("\n".join(lines))
    return path


def render_summary(report: dict) -> str:
    """One-line summary for CLI output."""
    return (
        f"Run {report.get('run_id', '?')}: "
        f"{report.get('total_tasks', 0)} tasks, "
        f"pass rate {report.get('judge_pass_rate', 0):.1%}, "
        f"mean score {report.get('judge_mean_score', 0):.2f}"
    )
