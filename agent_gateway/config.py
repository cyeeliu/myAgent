"""Centralized gateway configuration.

All environment-variable reads for the gateway live here as typed attributes on
``GatewayConfig``, loaded once at startup from ``os.environ``. This replaces the
scattered ``os.environ.get(...)`` / ``os.environ[...]`` calls across main.py,
sessions.py, and other modules.

Usage::

    from agent_gateway.config import settings
    settings.database_url       # → str | None
    settings.redis_url          # → str | None
    settings.gateway_port       # → int

The singleton ``settings`` is created at import time. Tests may construct a
fresh ``GatewayConfig()`` with overrides and monkeypatch ``settings`` if needed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class GatewayConfig:
    """Immutable gateway settings loaded from the environment."""

    # ── Server ──
    host: str = field(default_factory=lambda: os.environ.get("GATEWAY_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("GATEWAY_PORT", 8000))
    ws_path: str = "/ws"

    # ── Persistence (optional — unset degrades to in-memory) ──
    database_url: str | None = field(default_factory=lambda: os.environ.get("DATABASE_URL"))
    redis_url: str | None = field(default_factory=lambda: os.environ.get("REDIS_URL"))

    # ── Model ──
    model_id: str = field(default_factory=lambda: os.environ.get("MODEL_ID", ""))
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", "dummy"))
    openai_base_url: str | None = field(default_factory=lambda: os.environ.get("OPENAI_BASE_URL"))
    fallback_model_id: str | None = field(default_factory=lambda: os.environ.get("FALLBACK_MODEL_ID"))

    # ── Session management ──
    idle_timeout: int = field(default_factory=lambda: _env_int("IDLE_TIMEOUT", 30 * 60))
    permission_timeout: float = field(default_factory=lambda: float(_env_int("PERMISSION_TIMEOUT", 120)))
    cleanup_interval: int = 60  # seconds between idle-eviction sweeps

    # ── Debug / observability ──
    debug: bool = field(default_factory=lambda: _env_bool("AGENT_DEBUG"))
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO").upper())

    # ── CORS ──
    cors_origins: list[str] = field(default_factory=lambda: ["*"])

    # ── Paths (resolved lazily via properties) ──
    _repo_root: Path = field(default_factory=lambda: Path.cwd())

    @property
    def repo_root(self) -> Path:
        return self._repo_root

    @property
    def workspace_root(self) -> Path:
        """Mounted workspace root (``~/.myAgent/workspace`` in docker)."""
        return self._repo_root / "workspace"

    @property
    def session_files_root(self) -> Path:
        """On-disk session artifacts root (transcript.md, history.json)."""
        return self._repo_root / "agent" / "sessions"

    @property
    def session_state_root(self) -> Path:
        """Session-bound state root (``workspace/.sessions/``)."""
        return self.workspace_root / ".sessions"

    @property
    def skills_source_root(self) -> Path:
        """Preset skills source (``/app/skills`` in docker)."""
        return self._repo_root / "skills"


# Singleton loaded at import time — the single source of truth for all gateway
# modules. Tests may replace this via monkeypatch.
settings = GatewayConfig()
