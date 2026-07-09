"""Tests for the grep tool (content search) and the /api/health endpoint.
Both are additive Claude-Code-parity features added in this pass."""
import os
os.environ.setdefault("MODEL_ID", "test-model")
os.environ.setdefault("OPENAI_API_KEY", "dummy")

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_core.tools as t
from fastapi.testclient import TestClient
from agent_gateway.main import app


def test_grep_registered_in_both_tables():
    names = [x["name"] for x in t.BUILTIN_TOOLS]
    assert "grep" in names
    assert "grep" in t.BUILTIN_HANDLERS


def test_grep_content_mode_returns_file_line_match():
    out = t.run_grep(r"def agent_loop", "agent_core/loop.py")
    assert "loop.py:" in out
    assert "def agent_loop" in out


def test_grep_files_with_matches_mode():
    out = t.run_grep(r"BUILTIN_HANDLERS", "agent_core/tools.py",
                     output_mode="files_with_matches")
    assert "tools.py" in out
    # files_with_matches should not include the match line content
    assert "BUILTIN_HANDLERS = {" not in out


def test_grep_count_mode_reports_per_file():
    out = t.run_grep(r"^def ", "agent_core/tools.py", output_mode="count")
    assert "tools.py:" in out
    # at least the run_ functions we know exist
    assert "25" in out or "26" in out or "27" in out or "28" in out or "29" in out or "30" in out


def test_grep_no_match_returns_marker():
    out = t.run_grep(r"this_string_definitely_does_not_exist_xyz", "agent_core")
    assert out == "(no matches)"


def test_grep_invalid_regex_returns_error():
    out = t.run_grep(r"(unclosed", "agent_core")
    assert out.startswith("Error: invalid regex")


def test_grep_path_escape_is_rejected():
    out = t.run_grep(r"x", "../../etc")
    assert "escapes workspace" in out


def test_health_endpoint_reports_backends():
    client = TestClient(app)
    with client:
        r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # DB/Redis are optional; with no env vars they degrade to in_memory.
    assert body["db"] in ("postgres", "in_memory")
    assert body["redis"] in ("redis", "in_memory")
    assert "model" in body and "sessions_live" in body
