"""evals.datasets.schema — dataset schema validation (E-F8).

A dataset is a JSON object:
    {
      "name": str,                 # required
      "description": str,          # optional
      "judge_model": str,          # optional dataset-wide LLM judge model
      "tasks": [ Task, ... ]        # required, non-empty
    }

A Task is:
    {
      "id": str,                   # required, unique within dataset
      "prompt": str,               # required
      "category": str,             # optional
      "difficulty": str,           # optional (easy|medium|hard)
      "workspace": str,            # optional fixture path under evals/
      "mode": str,                 # optional (online|offline|mock)
      "max_turns": int,            # optional, > 0
      "max_seconds": int,          # optional, > 0
      "repeat": int,               # optional, >= 1
      "permission": str,           # optional (allow_all|deny_all)
      "expected_tools": [str],     # optional
      "record_path": str,          # optional (offline mode)
      "script": [Response, ...],   # optional (mock mode)
      "judge": JudgeSpec,          # optional
      "judge_model": str,          # optional task-level LLM judge model
      "tags": [str]                # optional
    }

Validation is structural only — it never imports agent_core or executes tasks,
so it is safe to run on untrusted datasets.
"""
from __future__ import annotations

from typing import Any

_VALID_MODES = {"online", "offline", "mock"}
_VALID_PERMISSIONS = {"allow_all", "deny_all"}
_VALID_DIFFICULTIES = {"easy", "medium", "hard"}
_VALID_JUDGE_TYPES = {"rule", "trajectory", "reference", "llm_judge", "composite"}


def _err(path: str, msg: str) -> tuple[str, str]:
    return (path, msg)


def _validate_judge(spec: Any, path: str) -> list[tuple[str, str]]:
    errors: list[tuple[str, str]] = []
    if not isinstance(spec, dict):
        errors.append(_err(path, "judge must be an object"))
        return errors
    jtype = spec.get("type", "rule")
    if jtype not in _VALID_JUDGE_TYPES:
        errors.append(_err(f"{path}.type",
                           f"invalid judge type '{jtype}'; expected one of "
                           f"{sorted(_VALID_JUDGE_TYPES)}"))
    if jtype == "rule":
        rules = spec.get("rules", [])
        if not isinstance(rules, list):
            errors.append(_err(f"{path}.rules", "rules must be a list"))
    elif jtype == "trajectory":
        if not isinstance(spec.get("expected_sequence", []), list):
            errors.append(_err(f"{path}.expected_sequence",
                               "expected_sequence must be a list"))
    elif jtype == "reference":
        if not isinstance(spec.get("reference", ""), str):
            errors.append(_err(f"{path}.reference", "reference must be a string"))
    elif jtype == "llm_judge":
        if not isinstance(spec.get("rubric", ""), str):
            errors.append(_err(f"{path}.rubric", "rubric must be a string"))
        if "consistency_runs" in spec and not isinstance(spec["consistency_runs"], int):
            errors.append(_err(f"{path}.consistency_runs",
                               "consistency_runs must be an int"))
    elif jtype == "composite":
        comps = spec.get("components", [])
        if not isinstance(comps, list) or not comps:
            errors.append(_err(f"{path}.components",
                               "composite judge needs a non-empty components list"))
        else:
            for i, c in enumerate(comps):
                errors.extend(_validate_judge(c, f"{path}.components[{i}]"))
    return errors


def _validate_task(task: Any, index: int) -> list[tuple[str, str]]:
    errors: list[tuple[str, str]] = []
    p = f"tasks[{index}]"
    if not isinstance(task, dict):
        errors.append(_err(p, "task must be an object"))
        return errors

    # Required fields
    if not task.get("id"):
        errors.append(_err(f"{p}.id", "id is required and must be a non-empty string"))
    elif not isinstance(task["id"], str):
        errors.append(_err(f"{p}.id", "id must be a string"))
    if "prompt" not in task or not isinstance(task["prompt"], str):
        errors.append(_err(f"{p}.prompt", "prompt is required and must be a string"))

    # Typed optional fields
    if "mode" in task and task["mode"] not in _VALID_MODES:
        errors.append(_err(f"{p}.mode", f"mode must be one of {sorted(_VALID_MODES)}"))
    if "difficulty" in task and task["difficulty"] not in _VALID_DIFFICULTIES:
        errors.append(_err(f"{p}.difficulty",
                           f"difficulty must be one of {sorted(_VALID_DIFFICULTIES)}"))
    if "permission" in task and task["permission"] not in _VALID_PERMISSIONS:
        errors.append(_err(f"{p}.permission",
                           f"permission must be one of {sorted(_VALID_PERMISSIONS)}"))
    if "max_turns" in task and (not isinstance(task["max_turns"], int) or task["max_turns"] <= 0):
        errors.append(_err(f"{p}.max_turns", "max_turns must be a positive int"))
    if "max_seconds" in task and (not isinstance(task["max_seconds"], int) or task["max_seconds"] <= 0):
        errors.append(_err(f"{p}.max_seconds", "max_seconds must be a positive int"))
    if "repeat" in task and (not isinstance(task["repeat"], int) or task["repeat"] < 1):
        errors.append(_err(f"{p}.repeat", "repeat must be an int >= 1"))
    for key in ("workspace", "record_path", "judge_model"):
        if key in task and not isinstance(task[key], str):
            errors.append(_err(f"{p}.{key}", f"{key} must be a string"))
    for key in ("expected_tools", "tags", "script"):
        if key in task and not isinstance(task[key], list):
            errors.append(_err(f"{p}.{key}", f"{key} must be a list"))

    # Mock mode should have a script
    mode = task.get("mode")
    if mode == "mock" and not task.get("script"):
        errors.append(_err(f"{p}.script",
                           "mock mode tasks should define a 'script' of responses"))
    # Offline mode should have a record_path
    if mode == "offline" and not task.get("record_path"):
        errors.append(_err(f"{p}.record_path",
                           "offline mode tasks should define a 'record_path'"))

    # Judge
    if "judge" in task:
        errors.extend(_validate_judge(task["judge"], f"{p}.judge"))

    return errors


def validate_dataset(dataset: Any) -> list[tuple[str, str]]:
    """Validate a dataset dict against the schema.

    Returns a list of (path, message) error tuples. An empty list means valid.
    Validation is structural only and never imports agent_core.
    """
    errors: list[tuple[str, str]] = []
    if not isinstance(dataset, dict):
        return [_err("root", "dataset must be a JSON object")]

    if not dataset.get("name") or not isinstance(dataset["name"], str):
        errors.append(_err("name", "name is required and must be a non-empty string"))

    tasks = dataset.get("tasks")
    if not isinstance(tasks, list):
        errors.append(_err("tasks", "tasks is required and must be a list"))
        return errors
    if not tasks:
        errors.append(_err("tasks", "tasks must not be empty"))

    seen_ids: set[str] = set()
    for i, task in enumerate(tasks):
        errors.extend(_validate_task(task, i))
        if isinstance(task, dict) and isinstance(task.get("id"), str):
            tid = task["id"]
            if tid in seen_ids:
                errors.append(_err(f"tasks[{i}].id", f"duplicate task id '{tid}'"))
            seen_ids.add(tid)

    return errors


def format_errors(errors: list[tuple[str, str]]) -> str:
    """Format a list of (path, message) errors for CLI output."""
    lines = []
    for path, msg in errors:
        lines.append(f"  - {path}: {msg}")
    return "\n".join(lines)
