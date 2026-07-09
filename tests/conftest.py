"""Shared pytest fixtures for the myAgent test suite."""
import os
os.environ.setdefault("MODEL_ID", "test-model")
os.environ.setdefault("OPENAI_API_KEY", "dummy")

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture(autouse=True)
def _isolate_model_config(tmp_path, monkeypatch):
    """Every test gets a model_config pointed at a non-existent tmp path, so no
    test ever reads the real REPO_ROOT/.agents/model.json. Tests that want to
    write a config use the `agents_dir` fixture (which re-points _CONFIG_PATH to
    a tmp .agents/model.json and is writable)."""
    import agent_core.model_config as mc
    monkeypatch.setattr(mc, "_CONFIG_PATH", tmp_path / "_isolated_model.json")
    mc.refresh()
    # Reset the process-level client cache so sig/client don't leak between tests.
    mc._client_state["client"] = None
    mc._client_state["sig"] = None


@pytest.fixture
def agents_dir(tmp_path, monkeypatch):
    """Redirect agent_core.agents.AGENTS_DIR AND agent_core.model_config._CONFIG_PATH
    to a tmp dir so tests never touch the real REPO_ROOT/.agents. Returns the
    tmp .agents Path. model_config is refreshed so no cached real config leaks in."""
    import agent_core.agents
    import agent_core.model_config as mc
    d = tmp_path / ".agents"
    d.mkdir()
    monkeypatch.setattr(agent_core.agents, "AGENTS_DIR", d)
    monkeypatch.setattr(mc, "_CONFIG_PATH", d / "model.json")
    mc.refresh()
    return d
