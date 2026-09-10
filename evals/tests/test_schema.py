"""Tests for evals.datasets.schema (E-F8)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.datasets.schema import validate_dataset, format_errors


def _valid_dataset() -> dict:
    return {
        "name": "test-ds",
        "tasks": [
            {
                "id": "t1", "prompt": "do something", "mode": "online",
                "max_seconds": 30, "repeat": 1, "permission": "allow_all",
                "judge": {"type": "rule", "rules": [{"kind": "no_error"}]},
                "tags": ["a"],
            },
            {
                "id": "t2", "prompt": "mock task", "mode": "mock",
                "script": [{"content": [{"type": "text", "text": "ok"}]}],
                "judge": {"type": "llm_judge", "rubric": "is correct",
                          "consistency_runs": 3},
            },
        ],
    }


def test_valid_dataset_passes():
    errors = validate_dataset(_valid_dataset())
    assert errors == [], format_errors(errors)


def test_real_datasets_are_valid():
    # The shipped datasets must validate.
    root = Path(__file__).resolve().parents[2] / "datasets"
    for p in root.glob("*.json"):
        ds = json.loads(p.read_text())
        errors = validate_dataset(ds)
        assert errors == [], f"{p.name}: {format_errors(errors)}"


def test_missing_id_and_prompt():
    ds = {"name": "x", "tasks": [{"mode": "online"}]}
    errors = validate_dataset(ds)
    paths = {e[0] for e in errors}
    assert any("id" in p for p in paths)
    assert any("prompt" in p for p in paths)


def test_duplicate_task_ids():
    ds = {"name": "x", "tasks": [
        {"id": "dup", "prompt": "a"}, {"id": "dup", "prompt": "b"},
    ]}
    errors = validate_dataset(ds)
    assert any("duplicate" in e[1] for e in errors)


def test_invalid_mode_and_permission():
    ds = {"name": "x", "tasks": [
        {"id": "t", "prompt": "p", "mode": "bogus", "permission": "bogus"},
    ]}
    errors = validate_dataset(ds)
    msgs = " ".join(e[1] for e in errors)
    assert "mode" in msgs
    assert "permission" in msgs


def test_mock_without_script_warns():
    ds = {"name": "x", "tasks": [{"id": "t", "prompt": "p", "mode": "mock"}]}
    errors = validate_dataset(ds)
    assert any("script" in e[0] for e in errors)


def test_offline_without_record_path_warns():
    ds = {"name": "x", "tasks": [{"id": "t", "prompt": "p", "mode": "offline"}]}
    errors = validate_dataset(ds)
    assert any("record_path" in e[0] for e in errors)


def test_composite_judge_validation():
    ds = {"name": "x", "tasks": [{"id": "t", "prompt": "p",
        "judge": {"type": "composite", "components": []}}]}
    errors = validate_dataset(ds)
    assert any("components" in e[1] for e in errors)


def test_non_object_dataset():
    errors = validate_dataset(["not", "an", "object"])
    assert errors and "object" in errors[0][1]


def test_format_errors_renders():
    out = format_errors([("tasks[0].id", "required")])
    assert "tasks[0].id" in out and "required" in out
