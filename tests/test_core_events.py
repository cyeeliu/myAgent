"""Core event-flow test: drive agent_loop with a RecordingSink and a scripted
LLM, assert the event sequence. No network, no CLI. Exercises the EventSink /
Session / permission refactor (spec §3, §8)."""
import os
os.environ.setdefault("MODEL_ID", "test-model")
os.environ.setdefault("OPENAI_API_KEY", "dummy")

import sys, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import code
import agent_core.adapter
from code import (Session, RecordingSink, Permission, _TextBlock, _ToolUseBlock,
                  SimpleNamespace)


class AllowAllPermission(Permission):
    def request(self, block):
        return {"allow": True, "modify": None}


def _resp(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


def test_text_then_done():
    """One assistant text turn → text is appended; done emitted; no tools."""
    calls = []
    def fake_chat_create(**kw):
        calls.append(kw)
        return _resp(_TextBlock("hello world"))
    agent_core.adapter.chat_create = fake_chat_create

    sink = RecordingSink()
    sess = Session(sinks=[sink], permission=AllowAllPermission(),
                   context=code.update_context({}, []))
    sess.append_both({"role": "user", "content": "hi"})
    code.agent_loop(sess)

    kinds = [e["kind"] for e in sink.events]
    assert "done" in kinds and kinds[-1] == "done", kinds
    assert any(e["kind"] == "token" for e in sink.events) is False  # RecordingSink not streaming
    assert sess.record[-1]["role"] == "assistant"
    print("test_text_then_done: OK", kinds)


def test_tool_start_result_done():
    """A tool_use turn → tool_start + tool_result + done, in order."""
    # First call: model emits a read_file tool_use; second call: plain text done.
    script = [
        _resp(_ToolUseBlock(id="t1", name="read_file",
                            input={"path": "README.md"}), stop="tool_use"),
        _resp(_TextBlock("done reading"), stop="end_turn"),
    ]
    def fake_chat_create(**kw):
        return script.pop(0)
    agent_core.adapter.chat_create = fake_chat_create

    sink = RecordingSink()
    sess = Session(sinks=[sink], permission=AllowAllPermission(),
                   context=code.update_context({}, []))
    sess.append_both({"role": "user", "content": "read the readme"})
    code.agent_loop(sess)

    kinds = [e["kind"] for e in sink.events]
    assert "tool_start" in kinds, kinds
    assert "tool_result" in kinds, kinds
    assert kinds.index("tool_start") < kinds.index("tool_result"), kinds
    assert kinds[-1] == "done", kinds
    # tool_start carries name + input
    ts = next(e for e in sink.events if e["kind"] == "tool_start")
    assert ts["payload"]["name"] == "read_file" and "seq" in ts["payload"], ts
    print("test_tool_start_result_done: OK", kinds)


def test_permission_denied():
    """A denied destructive bash → tool_result with blocked=True, no execution."""
    # Use a destructive token that is NOT on the hard deny list (so it reaches
    # the interactive permission path), then a benign text turn to terminate.
    script = [
        _resp(_ToolUseBlock(id="t1", name="bash",
                            input={"command": "rm /tmp/agent_smoke_x"}), stop="tool_use"),
        _resp(_TextBlock("okay"), stop="end_turn"),
    ]
    def fake_chat_create(**kw):
        return script.pop(0)
    agent_core.adapter.chat_create = fake_chat_create

    class DenyAll(Permission):
        def request(self, block):
            return {"allow": False, "modify": None}

    sink = RecordingSink()
    sess = Session(sinks=[sink], permission=DenyAll(),
                   context=code.update_context({}, []))
    sess.append_both({"role": "user", "content": "rm something"})
    code.agent_loop(sess)

    kinds = [e["kind"] for e in sink.events]
    assert "permission_request" in kinds, kinds
    tr = next(e for e in sink.events if e["kind"] == "tool_result")
    assert tr["payload"].get("blocked") is True, tr
    print("test_permission_denied: OK", kinds)


def test_seq_monotonic():
    """Every emitted event carries a strictly increasing seq."""
    def fake_chat_create(**kw):
        return _resp(_TextBlock("x"))
    agent_core.adapter.chat_create = fake_chat_create

    sink = RecordingSink()
    sess = Session(sinks=[sink], permission=AllowAllPermission(),
                   context=code.update_context({}, []))
    sess.append_both({"role": "user", "content": "hi"})
    code.agent_loop(sess)

    seqs = [e["payload"]["seq"] for e in sink.events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), seqs
    print("test_seq_monotonic: OK", seqs)


if __name__ == "__main__":
    test_text_then_done()
    test_tool_start_result_done()
    test_permission_denied()
    test_seq_monotonic()
    print("\nAll core event tests passed.")
