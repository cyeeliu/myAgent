"""Gateway exception hierarchy.

All gateway-specific errors derive from ``GatewayError``. The global exception
handlers in ``middleware.py`` map each subclass to the appropriate HTTP status
code and JSON error body, so route handlers can ``raise SessionNotFound(sid)``
instead of constructing ``HTTPException`` inline.

Hierarchy::

    GatewayError                    (500 Internal Server Error)
    ├── NotFoundError               (404)
    │   ├── SessionNotFound
    │   ├── AgentNotFound
    │   └── PermissionNotFound
    ├── ConflictError               (409)
    │   └── TurnInFlight
    ├── ValidationError             (400)
    │   └── ConfigError
    └── ForbiddenError              (403)
"""
from __future__ import annotations


class GatewayError(Exception):
    """Base for all gateway errors. Maps to HTTP 500 unless overridden."""

    status_code: int = 500
    detail: str = "internal gateway error"

    def __init__(self, detail: str | None = None, **extra):
        if detail:
            self.detail = detail
        self.extra = extra
        super().__init__(self.detail)


# ── 404 Not Found ──

class NotFoundError(GatewayError):
    status_code = 404
    detail = "not found"


class SessionNotFound(NotFoundError):
    detail = "session not found"

    def __init__(self, session_id: str | None = None):
        super().__init__(
            f"session not found: {session_id}" if session_id else "session not found",
            session_id=session_id,
        )


class AgentNotFound(NotFoundError):
    detail = "agent not found"

    def __init__(self, name: str | None = None):
        super().__init__(
            f"agent not found: {name}" if name else "agent not found",
            name=name,
        )


class PermissionNotFound(NotFoundError):
    detail = "no pending permission with that id"


# ── 409 Conflict ──

class ConflictError(GatewayError):
    status_code = 409
    detail = "conflict"


class TurnInFlight(ConflictError):
    detail = "a turn is already in flight"


# ── 400 Bad Request ──

class ValidationError(GatewayError):
    status_code = 400
    detail = "validation error"


class ConfigError(ValidationError):
    detail = "configuration error"


# ── 403 Forbidden ──

class ForbiddenError(GatewayError):
    status_code = 403
    detail = "forbidden"
