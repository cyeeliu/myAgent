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
    if isinstance(existing.get("models"), list):
        # preserve the multi-model list across a flat-config write
        new["models"] = existing["models"]
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(new, indent=2, ensure_ascii=False))
    refresh()
    return new


def _mask_key(key) -> str | None:
    if key and str(key).startswith("sk-") and len(str(key)) >= 4:
        return f"sk-***{str(key)[-4:]}"
    if key:
        return "***"
    return None


def get_models() -> list[dict]:
    """Persisted model list for the UI (api_key masked). Synthesizes a single
    entry from the flat config when no `models` key is present (backward compat
    with single-model configs)."""
    f = _read_file()
    raw_list = f.get("models") if isinstance(f.get("models"), list) else None
    if raw_list:
        out = []
        for e in raw_list:
            if not isinstance(e, dict):
                continue
            entry = dict(e)
            entry["model_name"] = (e.get("model_name") or e.get("model_id") or "")
            entry["api_base"] = e.get("api_base") or ""
            entry["api_key"] = _mask_key(e.get("api_key")) or ""
            entry["model_provider"] = e.get("model_provider") or "openai-compatible"
            entry["alias"] = e.get("alias") if e.get("alias") is not None else None
            out.append(entry)
        if out:
            return out
    cfg = get_config_masked()
    return [{
        "model_name": cfg["model_id"],
        "api_base": cfg.get("base_url") or "",
        "api_key": cfg.get("api_key_masked") or "",
        "model_provider": "openai-compatible",
        "is_default": True,
        "alias": None,
    }]


def write_models(models: list[dict]) -> dict:
    """Persist the full model list under .agents/model.json["models"]. The
    primary entry (first with is_default, else the first) also drives the
    top-level model_id/base_url/api_key so the agent loop uses it as the active
    model. Masked/empty api_keys are resolved against the existing list so
    unchanged keys aren't overwritten with the mask. Refreshes the cache.
    Returns the new on-disk dict."""
    existing = _read_file()
    existing_list = [e for e in (existing.get("models") or []) if isinstance(e, dict)]

    def resolve_key(entry: dict):
        raw = entry.get("api_key") or ""
        if raw and "***" not in raw:
            return raw  # the user typed a real key
        # masked or empty: reuse the existing real key for this model_name if any
        name = entry.get("model_name") or entry.get("model_id") or ""
        for e in existing_list:
            ek = e.get("api_key")
            if (e.get("model_name") or e.get("model_id") or "") == name and ek and "***" not in str(ek):
                return ek
        # fall back to the top-level file key, then env (what the agent uses)
        tk = existing.get("api_key")
        if tk and "***" not in str(tk):
            return tk
        return os.getenv("OPENAI_API_KEY")

    resolved = []
    for entry in (models or []):
        if not isinstance(entry, dict):
            continue
        e = dict(entry)
        e["model_name"] = entry.get("model_name") or entry.get("model_id") or ""
        e["api_key"] = resolve_key(entry)
        e.setdefault("model_provider", "openai-compatible")
        resolved.append(e)

    primary = next((e for e in resolved if e.get("is_default")), None)
    if primary is None and resolved:
        primary = resolved[0]
    if primary is not None:
        primary["is_default"] = True

    new = existing.copy()
    new["models"] = resolved
    if primary is not None:
        new["model_id"] = primary["model_name"]
        new["base_url"] = primary.get("api_base")
        new["api_key"] = primary.get("api_key")
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(new, indent=2, ensure_ascii=False))
    refresh()
    return new
