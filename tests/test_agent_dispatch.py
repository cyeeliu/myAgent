"""Tests for task-tool agent dispatch: spawn_subagent(description, agent=<name>).

Scripts adapter.chat_create to capture the system/tools/model the subagent runs
with, and asserts a defined agent's prompt/tools/model are applied."""
import agent_core.adapter
import agent_core.agents as ag
from agent_core.subagent import spawn_subagent, SUB_SYSTEM, SUB_TOOLS
from code import _TextBlock, SimpleNamespace


def _resp_text(text="done"):
    return SimpleNamespace(content=[_TextBlock(text)], stop_reason="end_turn")


def _capture_chat():
    """Install a chat_create that records kwargs and returns a plain text turn."""
    calls = []

    def fake(**kw):
        calls.append(kw)
        return _resp_text()

    agent_core.adapter.chat_create = fake
    return calls


def test_task_no_agent_unchanged(agents_dir):
    """agent=None → ad-hoc: SUB_SYSTEM + SUB_TOOLS + global MODEL."""
    calls = _capture_chat()
    out = spawn_subagent(description="do something")
    assert out == "done"
    kw = calls[0]
    assert kw["system"] == SUB_SYSTEM
    assert [t["name"] for t in kw["tools"]] == [t["name"] for t in SUB_TOOLS]
    assert kw["model"]  # global MODEL (test-model)


def test_task_with_agent_uses_def_prompt(agents_dir):
    ag.save_agent("researcher", "d", "You are a researcher. Be thorough.", None, [])
    calls = _capture_chat()
    spawn_subagent(description="explore", agent="researcher")
    assert calls[0]["system"] == "You are a researcher. Be thorough."


def test_task_with_agent_uses_def_tools(agents_dir):
    ag.save_agent("researcher", "d", "p", None,
                  ["bash", "read_file", "glob"])
    calls = _capture_chat()
    spawn_subagent(description="explore", agent="researcher")
    assert [t["name"] for t in calls[0]["tools"]] == ["bash", "read_file", "glob"]


def test_task_with_agent_model_override(agents_dir):
    ag.save_agent("researcher", "d", "p", "glm-4", [])
    calls = _capture_chat()
    spawn_subagent(description="explore", agent="researcher")
    assert calls[0]["model"] == "glm-4"


def test_task_with_agent_model_null_inherits_global(agents_dir):
    ag.save_agent("researcher", "d", "p", None, [])
    calls = _capture_chat()
    spawn_subagent(description="explore", agent="researcher")
    # global MODEL from env = test-model
    assert calls[0]["model"] == "test-model"


def test_task_agent_not_found(agents_dir):
    _capture_chat()
    out = spawn_subagent(description="x", agent="ghost")
    assert out.startswith("Agent not found: ghost")
    assert "Available" in out


def test_task_agent_unknown_tool(agents_dir):
    """A def listing an unknown tool name → friendly error, no chat call."""
    ag.save_agent("researcher", "d", "p", None, ["bash", "nonexistent_tool"])
    calls = _capture_chat()
    out = spawn_subagent(description="x", agent="researcher")
    assert "tool resolution failed" in out
    assert calls == []  # never reached the LLM
