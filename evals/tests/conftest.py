"""Shared pytest config for evals-specific unit tests.

Mirrors the top-level tests/conftest.py setup so evals tests can import
agent_core and evals without extra configuration. Third-party packages that
agent_core imports (openai, yaml, dotenv, …) are stubbed when absent so the
suite runs in a minimal environment without network-installed deps.
"""
import os
os.environ.setdefault("MODEL_ID", "test-model")
os.environ.setdefault("OPENAI_API_KEY", "dummy")

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ── Stub missing third-party deps so agent_core can be imported ──
class _Stub:
    """Permissive stub: any attr/call returns another stub. Lets agent_core's
    import-time side effects (OpenAI(), load_dotenv(), …) succeed without the
    real packages installed."""
    def __init__(self, *a, **k):
        pass
    def __call__(self, *a, **k):
        return _Stub()
    def __getattr__(self, name):
        return _Stub()
    def __iter__(self):
        return iter(())
    def __bool__(self):
        return False
    def __contains__(self, item):
        return False


def _stub_module(name: str) -> None:
    if name in sys.modules:
        return
    m = type(sys)(name)
    def __getattr__(n):  # PEP 562
        return _Stub()
    m.__getattr__ = __getattr__
    sys.modules[name] = m


for _mod in ("openai", "yaml", "dotenv", "psycopg", "psycopg2", "redis",
             "websockets", "mcp", "mcp.types", "fastapi", "pydantic",
             "uvicorn", "requests", "httpx"):
    _stub_module(_mod)
