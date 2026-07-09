"""agent_core.model_config — global model configuration, hot-swappable.

Single config at REPO_ROOT/.agents/model.json:
    {"model_id": "glm-5", "base_url": "https://...", "api_key": "sk-...",
     "fallback_model": "glm-4"}

File missing or a field empty → fall back to env (MODEL_ID / OPENAI_BASE_URL /
OPENAI_API_KEY / FALLBACK_MODEL_ID). `model()` is re-read each turn (mtime-cached)
so an online edit takes effect next turn without a restart. `client()` rebuilds
the OpenAI instance only when base_url/api_key change. `refresh()` invalidates
the cache (called by the gateway after a PUT).

This replaces the import-time globals in agent_core.env (client/MODEL/
FALLBACK_MODEL) for the live code paths; env remains the fallback source.
"""
import json
import os
import threading
from pathlib import Path
from openai import OpenAI
from agent_core.env import REPO_ROOT

_CONFIG_PATH = REPO_ROOT / ".agents" / "model.json"
_lock = threading.Lock()
_cache: dict = {"mtime": None, "config": None}
_client_state: dict = {"sig": None, "client": None}


def _read_file() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def get_config() -> dict:
    """Effective config with env fallback. mtime-cached."""
    with _lock:
        try:
            m = _CONFIG_PATH.stat().st_mtime if _CONFIG_PATH.exists() else None
        except OSError:
            m = None
        if _cache["mtime"] == m and _cache["config"] is not None:
            return _cache["config"]
        f = _read_file()
        cfg = {
            "model_id": f.get("model_id") or os.environ["MODEL_ID"],
            "base_url": f.get("base_url") or os.getenv("OPENAI_BASE_URL"),
            "api_key": f.get("api_key") or os.getenv("OPENAI_API_KEY", "dummy"),
            "fallback_model": f.get("fallback_model") or os.getenv("FALLBACK_MODEL_ID"),
        }
        _cache["mtime"] = m
        _cache["config"] = cfg
        return cfg


def model() -> str:
    return get_config()["model_id"]


def fallback() -> str | None:
    return get_config()["fallback_model"]


def client() -> OpenAI:
    """Cached OpenAI client; rebuild only when base_url/api_key change."""
    cfg = get_config()
    sig = (cfg["base_url"], cfg["api_key"])
    with _lock:
        if _client_state["client"] is not None and _client_state["sig"] == sig:
            return _client_state["client"]
        c = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
        _client_state["client"] = c
        _client_state["sig"] = sig
        return c


def refresh() -> None:
    """Invalidate the config cache (call after the gateway writes model.json).
    client() will rebuild iff the new base_url/api_key differ; otherwise the
    existing client is reused."""
    with _lock:
        _cache["mtime"] = None
        _cache["config"] = None


def get_config_masked() -> dict:
    """Config with api_key masked for API/UI exposure. Never returns the raw key."""
    cfg = get_config()
    key = cfg["api_key"]
    if key and key.startswith("sk-") and len(key) >= 4:
        masked = f"sk-***{key[-4:]}"
    elif key:
        masked = "***"
    else:
        masked = None
    return {
        "model_id": cfg["model_id"],
        "base_url": cfg["base_url"],
        "api_key_masked": masked,
        "fallback_model": cfg["fallback_model"],
    }


def write_config(model_id: str, base_url: str | None,
                 api_key: str | None, fallback_model: str | None) -> dict:
    """Persist config to .agents/model.json. An empty/None api_key preserves
    the existing on-disk key (so editing other fields doesn't wipe it).
    Refreshes the cache. Returns the new on-disk dict."""
    existing = _read_file()
    new = {
        "model_id": model_id,
        "base_url": base_url,
        "api_key": api_key if api_key else existing.get("api_key"),
        "fallback_model": fallback_model,
    }
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(new, indent=2, ensure_ascii=False))
    refresh()
    return new
