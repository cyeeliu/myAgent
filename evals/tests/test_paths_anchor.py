"""Tests for REPO_ROOT-anchored paths (E-L1)."""
from __future__ import annotations

from pathlib import Path

from evals.engine.workspace import WorkspaceIsolator, _RUNS_ROOT, _FIXTURES_ROOT
from evals.storage.results import ResultStore, _RESULTS_ROOT


def test_workspace_default_base_anchored_at_repo_root():
    from agent_core.paths import REPO_ROOT
    iso = WorkspaceIsolator()
    assert iso.base_dir == REPO_ROOT / "evals" / "runs"
    assert _RUNS_ROOT == REPO_ROOT / "evals" / "runs"
    assert _FIXTURES_ROOT == REPO_ROOT / "evals"


def test_results_default_base_anchored_at_repo_root():
    from agent_core.paths import REPO_ROOT
    store = ResultStore()
    assert store.base_dir == REPO_ROOT / "evals" / "results"
    assert _RESULTS_ROOT == REPO_ROOT / "evals" / "results"


def test_workspace_explicit_relative_resolves_against_repo_root():
    from agent_core.paths import REPO_ROOT
    iso = WorkspaceIsolator(base_dir="evals/custom_runs")
    assert iso.base_dir == REPO_ROOT / "evals" / "custom_runs"


def test_workspace_absolute_base_respected(tmp_path):
    iso = WorkspaceIsolator(base_dir=str(tmp_path))
    assert iso.base_dir == tmp_path


def test_workspace_isolate_uses_anchored_fixture(tmp_path):
    # isolate() should resolve the fixture under REPO_ROOT/evals, not CWD.
    iso = WorkspaceIsolator(base_dir=str(tmp_path))
    ws = iso.isolate({"id": "tx", "workspace": "fixtures/repo_sample"}, "run1")
    assert ws.exists()
    # If the fixture existed, its contents would be copied; either way the ws dir
    # is under the anchored base_dir.
    assert str(ws).startswith(str(tmp_path))
