"""evals.adapters.swe_bench_adapter — adapt existing runner.py to the new framework.

Maps SWE-bench EvalTask → framework Task with RuleJudge(tests_pass).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def adapt_task(eval_task: dict) -> dict:
    """Convert a SWE-bench EvalTask to the framework's Task schema."""
    return {
        "id": eval_task.get("id", eval_task.get("instance_id", "")),
        "category": "swe_bench",
        "difficulty": "hard",
        "prompt": eval_task.get("problem_statement", ""),
        "workspace": "",
        "mode": "online",
        "max_turns": 30,
        "max_seconds": 600,
        "repeat": 1,
        "permission": "allow_all",
        "judge": {
            "type": "rule",
            "rules": [
                {"kind": "tests_pass",
                 "command": eval_task.get("test_cmd", "pytest -x")},
            ],
        },
        "tags": ["swe_bench"],
        # Preserve original fields for backward compat
        "_original": eval_task,
    }


def adapt_dataset(tasks: list[dict], name: str = "swe-bench-adapted") -> dict:
    """Convert a list of SWE-bench tasks to a framework dataset."""
    return {
        "name": name,
        "tasks": [adapt_task(t) for t in tasks],
    }


def load_swe_bench_tasks(path: str) -> dict:
    """Load existing evals/tasks/*.json and adapt."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return adapt_dataset(data)
    elif isinstance(data, dict) and "tasks" in data:
        return adapt_dataset(data["tasks"], data.get("name", "swe-bench"))
    else:
        return adapt_dataset([data])
