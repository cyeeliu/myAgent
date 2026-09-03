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


# ── E-F7: HTML report + A/B comparison visualization ──

_HTML_HEAD = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body{{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
       margin:0;padding:24px;color:#1f2937;background:#f9fafb}}
  h1{{margin-top:0}} h2{{border-bottom:2px solid #e5e7eb;padding-bottom:6px}}
  table{{border-collapse:collapse;width:100%;background:#fff;
        box-shadow:0 1px 2px rgba(0,0,0,.05);margin:12px 0}}
  th,td{{border:1px solid #e5e7eb;padding:8px 10px;text-align:left}}
  th{{background:#f3f4f6;font-weight:600}}
  tr:hover td{{background:#f9fafb}}
  .kpi{{display:flex;gap:16px;flex-wrap:wrap;margin:16px 0}}
  .kpi .card{{background:#fff;border:1px solid #e5e7eb;border-radius:8px;
             padding:12px 16px;min-width:140px;box-shadow:0 1px 2px rgba(0,0,0,.05)}}
  .kpi .label{{color:#6b7280;font-size:12px;text-transform:uppercase;letter-spacing:.05em}}
  .kpi .value{{font-size:22px;font-weight:700;margin-top:4px}}
  .bar{{height:10px;border-radius:5px;background:#e5e7eb;position:relative;min-width:80px}}
  .bar > span{{display:block;height:10px;border-radius:5px;background:#3b82f6}}
  .up{{color:#16a34a}} .down{{color:#dc2626}} .flat{{color:#6b7280}}
  .badge{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}}
  .ok{{background:#dcfce7;color:#166534}} .err{{background:#fee2e2;color:#991b1b}}
  .regressed{{background:#fee2e2;color:#991b1b}} .fixed{{background:#dcfce7;color:#166534}}
</style></head><body>
"""

_HTML_FOOT = "\n</body></html>\n"


def _kpi_card(label: str, value: str) -> str:
    return (f'<div class="card"><div class="label">{label}</div>'
            f'<div class="value">{value}</div></div>')


def _bar(value: float, vmax: float = 1.0) -> str:
    pct = max(0.0, min(1.0, value / vmax)) * 100 if vmax else 0.0
    return f'<div class="bar"><span style="width:{pct:.1f}%"></span></div>'


def _status_badge(status: str) -> str:
    cls = "ok" if status == "ok" else "err"
    return f'<span class="badge {cls}">{status}</span>'


def render_html(report: dict, output_dir: str | Path) -> Path:
    """Write a self-contained HTML scorecard report (E-F7)."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "report.html"

    run_id = report.get("run_id", "?")
    parts = [_HTML_HEAD.format(title=f"Evaluation Report — {run_id}")]
    parts.append(f"<h1>Evaluation Report — {run_id}</h1>")

    # KPI cards
    parts.append('<div class="kpi">')
    parts.append(_kpi_card("Dataset", str(report.get("dataset", "?"))))
    parts.append(_kpi_card("Model", str(report.get("model", "?"))))
    parts.append(_kpi_card("Mode", str(report.get("mode", "?"))))
    parts.append(_kpi_card("Tasks", str(report.get("total_tasks", 0))))
    parts.append(_kpi_card("Runs", str(report.get("total_runs", 0))))
    parts.append(_kpi_card("Pass Rate", f"{report.get('judge_pass_rate', 0):.1%}"))
    parts.append(_kpi_card("Mean Score", f"{report.get('judge_mean_score', 0):.2f}"))
    parts.append("</div>")

    # Scorecard table
    parts.append("<h2>Scorecard</h2>")
    parts.append("<table><tr><th>Metric</th><th>Mean</th><th>StdDev</th>"
                 "<th>Min</th><th>Max</th><th>Distribution</th></tr>")
    for name, stats in sorted(report.get("scorecard", {}).items()):
        mean = stats.get("mean", 0.0)
        mx = stats.get("max", 0.0) or 1.0
        parts.append(
            f"<tr><td>{name}</td><td>{mean:.4f}</td>"
            f"<td>{stats.get('stddev', 0):.4f}</td>"
            f"<td>{stats.get('min', 0):.4f}</td>"
            f"<td>{stats.get('max', 0):.4f}</td><td>{_bar(mean, mx)}</td></tr>"
        )
    parts.append("</table>")

    # Per-task table
    parts.append("<h2>Per-Task Summary</h2>")
    parts.append("<table><tr><th>Task</th><th>Reps</th><th>Mean Score</th>"
                 "<th>Pass Count</th><th>Status</th></tr>")
    for t in report.get("per_task", []):
        parts.append(
            f"<tr><td>{t.get('task_id')}</td><td>{t.get('reps')}</td>"
            f"<td>{t.get('mean_score', 0):.2f}</td>"
            f"<td>{t.get('pass_count')}</td>"
            f"<td>{_status_badge(t.get('status', 'ok'))}</td></tr>"
        )
    parts.append("</table>")

    # Failed tasks
    failed = [r for r in report.get("results", []) if r.get("status") != "ok"]
    if failed:
        parts.append("<h2>Failed Tasks</h2><ul>")
        for r in failed:
            parts.append(f"<li><strong>{r.get('task_id')}</strong> (rep {r.get('rep')}): "
                         f"{r.get('error', 'unknown')}</li>")
        parts.append("</ul>")

    # Regression section (if present)
    reg = report.get("regression")
    if reg:
        parts.append("<h2>Regression vs Baseline</h2>")
        parts.append(f"<p>Baseline run: <code>{reg.get('baseline_run_id', '?')}</code></p>")
        parts.append("<table><tr><th>Regressed (was pass → now fail)</th>"
                     "<th>Fixed (was fail → now pass)</th></tr><tr><td>")
        parts.append("<br>".join(reg.get("regressed", [])) or "—")
        parts.append("</td><td>")
        parts.append("<br>".join(reg.get("fixed", [])) or "—")
        parts.append("</td></tr></table>")

    parts.append(_HTML_FOOT)
    path.write_text("\n".join(parts))
    return path


def _delta_marker(delta: float, threshold: float = 0.01) -> str:
    if delta > threshold:
        return f'<span class="up">↑ +{delta:.4f}</span>'
    if delta < -threshold:
        return f'<span class="down">↓ {delta:.4f}</span>'
    return '<span class="flat">=</span>'


def render_compare_html(report_a: dict, report_b: dict,
                        output_dir: str | Path,
                        label_a: str = "A", label_b: str = "B") -> Path:
    """Write a self-contained A/B comparison HTML report (E-F7).

    Shows pass-rate / mean-score deltas, per-metric scorecard changes with
    direction markers, and a per-task status diff (newly-passing / newly-failing
    / unchanged).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "compare.html"

    ra, rb = report_a.get("run_id", "?"), report_b.get("run_id", "?")
    parts = [_HTML_HEAD.format(title=f"A/B Comparison — {label_a} vs {label_b}")]
    parts.append(f"<h1>A/B Comparison</h1>")
    parts.append(f"<p><strong>{label_a}</strong>: <code>{ra}</code> &nbsp; vs &nbsp; "
                 f"<strong>{label_b}</strong>: <code>{rb}</code></p>")

    # Headline deltas
    pr_a, pr_b = report_a.get("judge_pass_rate", 0), report_b.get("judge_pass_rate", 0)
    ms_a, ms_b = report_a.get("judge_mean_score", 0), report_b.get("judge_mean_score", 0)
    parts.append('<div class="kpi">')
    parts.append(_kpi_card(f"{label_a} pass rate", f"{pr_a:.1%}"))
    parts.append(_kpi_card(f"{label_b} pass rate", f"{pr_b:.1%}"))
    parts.append(_kpi_card("Δ pass rate", f"{(pr_b - pr_a):+.1%}"))
    parts.append(_kpi_card(f"{label_a} mean score", f"{ms_a:.2f}"))
    parts.append(_kpi_card(f"{label_b} mean score", f"{ms_b:.2f}"))
    parts.append(_kpi_card("Δ mean score", f"{(ms_b - ms_a):+.2f}"))
    parts.append("</div>")

    # Scorecard metric comparison
    sc_a, sc_b = report_a.get("scorecard", {}), report_b.get("scorecard", {})
    all_keys = sorted(set(sc_a) | set(sc_b))
    parts.append("<h2>Scorecard Metric Changes</h2>")
    parts.append(f"<table><tr><th>Metric</th><th>{label_a} mean</th>"
                 f"<th>{label_b} mean</th><th>Δ</th></tr>")
    for key in all_keys:
        va = sc_a.get(key, {}).get("mean", 0.0)
        vb = sc_b.get(key, {}).get("mean", 0.0)
        parts.append(f"<tr><td>{key}</td><td>{va:.4f}</td><td>{vb:.4f}</td>"
                     f"<td>{_delta_marker(vb - va)}</td></tr>")
    parts.append("</table>")

    # Per-task status diff
    def _pass_map(rep):
        return {t.get("task_id"): (t.get("pass_count", 0) > 0)
                for t in rep.get("per_task", [])}
    pa, pb = _pass_map(report_a), _pass_map(report_b)
    all_tasks = sorted(set(pa) | set(pb))
    newly_pass, newly_fail, unchanged = [], [], []
    for tid in all_tasks:
        a_pass, b_pass = pa.get(tid, False), pb.get(tid, False)
        if b_pass and not a_pass:
            newly_pass.append(tid)
        elif a_pass and not b_pass:
            newly_fail.append(tid)
        else:
            unchanged.append(tid)
    parts.append("<h2>Per-Task Status Diff</h2>")
    parts.append("<table><tr><th>Newly passing (fixed)</th>"
                 "<th>Newly failing (regressed)</th><th>Unchanged</th></tr><tr>")
    parts.append(f'<td>{("<br>".join(f"<span class=fixed>{t}</span>" for t in newly_pass)) or "—"}</td>')
    parts.append(f'<td>{("<br>".join(f"<span class=regressed>{t}</span>" for t in newly_fail)) or "—"}</td>')
    parts.append(f'<td>{len(unchanged)} tasks</td>')
    parts.append("</tr></table>")

    parts.append(_HTML_FOOT)
    path.write_text("\n".join(parts))
    return path
