"""Unit tests for agent_core.agents (runtime subagent definitions)."""
import json

import agent_core.agents as ag


def test_list_agents_empty(agents_dir):
    assert ag.list_agents() == []


def test_save_and_get_agent(agents_dir):
    d = ag.save_agent("researcher", "explore code", "You are a researcher.",
                      None, ["bash", "read_file", "glob"])
    assert d["name"] == "researcher"
    got = ag.get_agent("researcher")
    assert got is not None
    assert got["description"] == "explore code"
    assert got["prompt"] == "You are a researcher."
    assert got["model"] is None
    assert got["tools"] == ["bash", "read_file", "glob"]


def test_save_agent_default_tools(agents_dir):
    ag.save_agent("x", "d", "p", None, [])
    assert ag.get_agent("x")["tools"] == ["bash", "read_file", "write_file",
                                          "edit_file", "glob"]


def test_save_agent_invalid_name(agents_dir):
    import pytest
    for bad in ["../x", "a/b", "中文", "", "a b", "a.b"]:
        with pytest.raises(ValueError):
            ag.save_agent(bad, "d", "p", None, [])


def test_delete_agent_missing(agents_dir):
    assert ag.delete_agent("nope") is False


def test_delete_agent_existing(agents_dir):
    ag.save_agent("x", "d", "p", None, [])
    assert ag.delete_agent("x") is True
    assert ag.get_agent("x") is None


def test_list_agents_skips_corrupt_json(agents_dir):
    ag.save_agent("good", "d", "p", None, [])
    (agents_dir / "bad.json").write_text("{not valid json")
    names = [a["name"] for a in ag.list_agents()]
    assert names == ["good"]


def test_list_agents_excludes_model_json(agents_dir):
    """model.json (global model config) must not appear as an agent."""
    ag.save_agent("researcher", "d", "p", None, [])
    (agents_dir / "model.json").write_text(
        json.dumps({"model_id": "glm-5", "api_key": "sk-x"}))
    names = [a["name"] for a in ag.list_agents()]
    assert "model" not in names
    assert "researcher" in names


def test_scan_agents_format(agents_dir):
    assert ag.scan_agents() == "(no agents defined)"
    ag.save_agent("researcher", "explore code", "p", None, [])
    ag.save_agent("writer", "write code", "p", "glm-4", [])
    catalog = ag.scan_agents()
    assert "- researcher: explore code" in catalog
    assert "- writer: write code" in catalog


def test_get_agent_invalid_name(agents_dir):
    assert ag.get_agent("../x") is None
