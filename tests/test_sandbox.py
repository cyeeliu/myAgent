"""Tests for agent_core.sandbox — bash filesystem isolation via bubblewrap."""
import shutil
from pathlib import Path
import pytest

import agent_core.sandbox as sb
from agent_core import tools
from agent_core.env import REPO_ROOT


def test_build_argv_structure(tmp_path):
    wd = tmp_path / "ws"
    wd.mkdir()
    argv = sb.build_argv(wd, "cat foo")
    assert argv[0] == "bwrap"
    assert "--die-with-parent" in argv
    wds = str(wd.resolve())
    # workdir bound read-write (not ro-bind), at its real path
    i = argv.index("--bind")
    assert argv[i + 1] == wds and argv[i + 2] == wds
    j = argv.index("--chdir")
    assert argv[j + 1] == wds
    # command handed to /bin/sh -c
    assert argv[-3:] == ["/bin/sh", "-c", "cat foo"]


def test_build_argv_ro_mounts_system(tmp_path):
    argv = sb.build_argv(tmp_path, "x")
    for d in ("/usr", "/etc", "/bin"):
        if Path(d).exists():
            assert d in argv  # appears as a ro-bind src/dest


def test_build_argv_skips_tmp_ancestor(tmp_path):
    """workdir under /tmp must not --dir /tmp (already a tmpfs)."""
    argv = sb.build_argv(tmp_path, "x")
    # /tmp is provided by --tmpfs; a redundant --dir /tmp would collide.
    for k in range(len(argv)):
        if argv[k] == "--dir" and argv[k + 1] == "/tmp":
            pytest.fail("--dir /tmp emitted over tmpfs")


def test_enabled_force_off(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX", "0")
    assert sb.enabled(tmp_path) is False


def test_enabled_force_on(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX", "1")
    monkeypatch.setattr(sb, "bwrap_available", lambda: True)
    assert sb.enabled(tmp_path) is True
    monkeypatch.setattr(sb, "bwrap_available", lambda: False)
    assert sb.enabled(tmp_path) is False


def test_enabled_auto(monkeypatch, tmp_path):
    monkeypatch.delenv("SANDBOX", raising=False)
    monkeypatch.setattr(sb, "bwrap_available", lambda: True)
    assert sb.enabled(tmp_path) is True  # per-session workdir
    assert sb.enabled(REPO_ROOT) is False  # repo-root dev mode → off
    monkeypatch.setattr(sb, "bwrap_available", lambda: False)
    assert sb.enabled(tmp_path) is False


class _FakeCP:
    def __init__(self, out="ok"):
        self.stdout = out
        self.stderr = ""
        self.returncode = 0


def test_run_bash_uses_bwrap_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX", "1")
    monkeypatch.setattr(sb, "bwrap_available", lambda: True)
    captured = {}

    def fake_run(args, **kw):
        captured["args"] = args
        captured["shell"] = kw.get("shell")
        return _FakeCP("ok")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    out = tools.run_bash("ls", cwd=tmp_path)
    assert captured["shell"] is None  # bwrap path: argv list, no shell=True
    assert captured["args"][0] == "bwrap"
    assert "ok" in out


def test_run_bash_plain_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX", "0")
    captured = {}

    def fake_run(args, **kw):
        captured["shell"] = kw.get("shell")
        captured["cwd"] = kw.get("cwd")
        return _FakeCP("plain")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    out = tools.run_bash("ls", cwd=tmp_path)
    assert captured["shell"] is True
    assert captured["cwd"] == tmp_path
    assert "plain" in out


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed")
def test_bwrap_real_isolation(tmp_path, monkeypatch):
    """End-to-end: a file outside the workdir is invisible to sandboxed bash."""
    monkeypatch.setenv("SANDBOX", "1")
    wd = tmp_path / "ws"
    wd.mkdir()
    (wd / "inside.txt").write_text("hello-sandbox")
    (tmp_path / "outside.txt").write_text("SECRET-TOKEN")

    # reading a file inside the workdir works
    assert "hello-sandbox" in tools.run_bash("cat inside.txt", cwd=wd)
    # reading outside via absolute path fails (not mounted → ENOENT)
    assert "SECRET-TOKEN" not in tools.run_bash(
        f"cat {tmp_path / 'outside.txt'}", cwd=wd)
    # the agent's own source tree is invisible
    assert "tools.py" not in tools.run_bash(
        f"ls {REPO_ROOT / 'agent_core'}", cwd=wd)
