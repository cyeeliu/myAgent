"""evals.runner — SWE-bench-style evaluation harness for myAgent.

Runs the agent against a set of coding tasks (SWE-bench instances or custom
test cases), measures pass@1 / pass@5, latency, token usage, and produces
a JSON report + Markdown summary.

Usage:
    python -m evals.runner --dataset swe-bench-lite --limit 10
    python -m evals.runner --custom evals/tasks/my_tasks.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# ── Data structures ──

@dataclass
class EvalTask:
    """A single evaluation task."""
    id: str
    repo: str = ""
    base_commit: str = ""
    problem_statement: str = ""
    test_patch: str = ""
    fail_to_pass: list[str] = field(default_factory=list)  # tests that should pass after fix
    pass_to_pass: list[str] = field(default_factory=list)  # tests that should still pass
    environment_setup: str = ""  # setup commands


@dataclass
class EvalResult:
    """Result of running the agent on one task."""
    task_id: str
    resolved: bool = False
    fail_to_pass_results: dict[str, bool] = field(default_factory=dict)
    pass_to_pass_results: dict[str, bool] = field(default_factory=dict)
    latency_seconds: float = 0.0
    num_turns: int = 0
    num_tool_calls: int = 0
    error: str = ""


@dataclass
class EvalReport:
    """Aggregate report across all tasks."""
    dataset: str
    total: int = 0
    resolved: int = 0
    pass_rate: float = 0.0
    avg_latency: float = 0.0
    results: list[EvalResult] = field(default_factory=list)

    def compute(self):
        self.total = len(self.results)
        self.resolved = sum(1 for r in self.results if r.resolved)
        self.pass_rate = self.resolved / self.total if self.total > 0 else 0.0
        latencies = [r.latency_seconds for r in self.results if r.latency_seconds > 0]
        self.avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

    def to_json(self) -> str:
        self.compute()
        return json.dumps({
            "dataset": self.dataset,
            "total": self.total,
            "resolved": self.resolved,
            "pass_rate": self.pass_rate,
            "avg_latency": self.avg_latency,
            "results": [asdict(r) for r in self.results],
        }, indent=2)

    def to_markdown(self) -> str:
        self.compute()
        lines = [
            f"# SWE-bench Evaluation Report: {self.dataset}",
            "",
            f"- **Total tasks**: {self.total}",
            f"- **Resolved**: {self.resolved}",
            f"- **Pass rate**: {self.pass_rate:.1%}",
            f"- **Avg latency**: {self.avg_latency:.1f}s",
            "",
            "## Per-task results",
            "",
            "| Task ID | Resolved | Latency (s) | Error |",
            "|---------|----------|-------------|-------|",
        ]
        for r in self.results:
            lines.append(f"| {r.task_id} | {'✅' if r.resolved else '❌'} | {r.latency_seconds:.1f} | {r.error[:50] if r.error else ''} |")
        return "\n".join(lines)


# ── Task loading ──

def load_swe_bench_dataset(name: str, limit: int = 0) -> list[EvalTask]:
    """Load SWE-bench instances from the HuggingFace dataset or a local cache."""
    cache_path = Path(__file__).parent / "data" / f"{name}.json"
    if cache_path.exists():
        data = json.loads(cache_path.read_text())
        tasks = []
        for item in data:
            tasks.append(EvalTask(
                id=item.get("instance_id", ""),
                repo=item.get("repo", ""),
                base_commit=item.get("base_commit", ""),
                problem_statement=item.get("problem_statement", ""),
                test_patch=item.get("test_patch", ""),
                fail_to_pass=json.loads(item.get("FAIL_TO_PASS", "[]")),
                pass_to_pass=json.loads(item.get("PASS_TO_PASS", "[]")),
            ))
        if limit > 0:
            tasks = tasks[:limit]
        return tasks

    # Try HuggingFace datasets
    try:
        from datasets import load_dataset
        ds = load_dataset(name, split="test")
        tasks = []
        for item in ds:
            tasks.append(EvalTask(
                id=item.get("instance_id", ""),
                repo=item.get("repo", ""),
                base_commit=item.get("base_commit", ""),
                problem_statement=item.get("problem_statement", ""),
                test_patch=item.get("test_patch", ""),
                fail_to_pass=json.loads(item.get("FAIL_TO_PASS", "[]")),
                pass_to_pass=json.loads(item.get("PASS_TO_PASS", "[]")),
            ))
            if limit > 0 and len(tasks) >= limit:
                break
        return tasks
    except ImportError:
        print(f"Warning: 'datasets' package not installed. Cache at {cache_path} not found.")
        print(f"Install with: pip install datasets")
        return []


def load_custom_tasks(path: str) -> list[EvalTask]:
    """Load custom tasks from a JSON file."""
    data = json.loads(Path(path).read_text())
    tasks = []
    for item in data:
        tasks.append(EvalTask(
            id=item.get("id", f"task-{len(tasks)}"),
            problem_statement=item.get("problem_statement", ""),
            repo=item.get("repo", ""),
            base_commit=item.get("base_commit", ""),
            fail_to_pass=item.get("fail_to_pass", []),
            pass_to_pass=item.get("pass_to_pass", []),
            environment_setup=item.get("environment_setup", ""),
        ))
    return tasks


# ── Test execution ──

def run_tests(repo_dir: str, test_names: list[str]) -> dict[str, bool]:
    """Run pytest for each test name and return pass/fail results."""
    results = {}
    for test_name in test_names:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", test_name, "-x", "-q", "--no-header",
                 "--tb=short"],
                cwd=repo_dir,
                capture_output=True,
                timeout=120,
                text=True,
            )
            results[test_name] = proc.returncode == 0
        except subprocess.TimeoutExpired:
            results[test_name] = False
        except Exception as e:
            results[test_name] = False
    return results


def apply_test_patch(repo_dir: str, patch: str) -> bool:
    """Apply a test patch to the repo."""
    if not patch:
        return True
    try:
        proc = subprocess.run(
            ["git", "apply", "-"],
            input=patch,
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0
    except Exception:
        return False


# ── Agent execution ──

def run_agent_on_task(task: EvalTask, workdir: str, model_id: str = "") -> tuple[str, float, int, int]:
    """Run the agent on a task and return (patch, latency, num_turns, num_tool_calls)."""
    start = time.time()

    # Build the prompt
    prompt = f"""You are working on the repository: {task.repo}
Base commit: {task.base_commit}

## Problem
{task.problem_statement}

## Instructions
1. Explore the codebase to understand the issue.
2. Make the minimal fix needed to resolve the problem.
3. Run the failing tests to verify your fix.
4. Do NOT modify test files — only fix the source code.
"""

    # Run the agent via CLI
    env = os.environ.copy()
    if model_id:
        env["MODEL_ID"] = model_id

    repo_dir = os.path.join(workdir, task.id)
    os.makedirs(repo_dir, exist_ok=True)

    try:
        proc = subprocess.run(
            [sys.executable, "-c", f"""
import sys
sys.path.insert(0, '.')
from agent_core.cli import run_once
run_once({repr(prompt)})
"""],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
        )
        output = proc.stdout
    except subprocess.TimeoutExpired:
        output = ""
    except Exception:
        output = ""

    latency = time.time() - start

    # Extract the patch
    try:
        proc = subprocess.run(
            ["git", "diff"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        patch = proc.stdout
    except Exception:
        patch = ""

    # Count turns and tool calls from output (rough estimate)
    num_turns = output.count("=== Turn") if "=== Turn" in output else 1
    num_tool_calls = output.count("tool_use") if "tool_use" in output else 0

    return patch, latency, num_turns, num_tool_calls


# ── Main evaluation loop ──

def evaluate(
    tasks: list[EvalTask],
    dataset_name: str,
    workdir: str = "/tmp/swe-bench-eval",
    model_id: str = "",
) -> EvalReport:
    """Run the agent against all tasks and produce a report."""
    report = EvalReport(dataset=dataset_name)
    workdir_path = Path(workdir)
    workdir_path.mkdir(parents=True, exist_ok=True)

    for i, task in enumerate(tasks):
        print(f"[{i+1}/{len(tasks)}] Evaluating {task.id}...", flush=True)
        result = EvalResult(task_id=task.id)

        try:
            # Run the agent
            patch, latency, turns, tool_calls = run_agent_on_task(
                task, str(workdir_path), model_id
            )
            result.latency_seconds = latency
            result.num_turns = turns
            result.num_tool_calls = tool_calls

            # Apply test patch and run tests
            repo_dir = workdir_path / task.id
            if task.test_patch:
                apply_test_patch(str(repo_dir), task.test_patch)

            if task.fail_to_pass:
                result.fail_to_pass_results = run_tests(
                    str(repo_dir), task.fail_to_pass
                )
                result.resolved = all(result.fail_to_pass_results.values())

            if task.pass_to_pass:
                result.pass_to_pass_results = run_tests(
                    str(repo_dir), task.pass_to_pass
                )
                # All pass_to_pass must still pass
                if not all(result.pass_to_pass_results.values()):
                    result.resolved = False

        except Exception as e:
            result.error = str(e)

        report.results.append(result)
        status = "✅" if result.resolved else "❌"
        print(f"  {status} resolved={result.resolved} latency={result.latency_seconds:.1f}s")

    return report


def main():
    parser = argparse.ArgumentParser(description="SWE-bench evaluation runner")
    parser.add_argument("--dataset", default="swe-bench-lite",
                        help="SWE-bench dataset name")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max tasks to evaluate (0 = all)")
    parser.add_argument("--custom", default="",
                        help="Path to custom tasks JSON file")
    parser.add_argument("--workdir", default="/tmp/swe-bench-eval",
                        help="Working directory for repos")
    parser.add_argument("--model", default="",
                        help="Model ID to use")
    parser.add_argument("--output", default="",
                        help="Output JSON report path")
    args = parser.parse_args()

    if args.custom:
        tasks = load_custom_tasks(args.custom)
        dataset_name = Path(args.custom).stem
    else:
        tasks = load_swe_bench_dataset(args.dataset, args.limit)
        dataset_name = args.dataset

    if not tasks:
        print("No tasks to evaluate.")
        sys.exit(1)

    print(f"Loaded {len(tasks)} tasks from {dataset_name}")

    report = evaluate(tasks, dataset_name, args.workdir, args.model)

    # Output
    json_str = report.to_json()
    md_str = report.to_markdown()

    output_dir = Path(__file__).parent / "results"
    output_dir.mkdir(exist_ok=True)

    json_path = Path(args.output) if args.output else output_dir / f"{dataset_name}.json"
    md_path = json_path.with_suffix(".md")

    json_path.write_text(json_str)
    md_path.write_text(md_str)

    print(f"\n{md_str}")
    print(f"\nReport saved to {json_path}")


if __name__ == "__main__":
    main()
