"""Middleware and global exception handler registration.

Called once from ``create_app()`` to attach:
  - CORS middleware
  - API versioning middleware (``/api/v1/*`` → ``/api/*`` path rewrite)
  - Request-ID injection middleware
  - Gateway auth middleware (API-key header when GATEWAY_API_KEY is set)
  - Global exception handlers for the ``GatewayError`` hierarchy
"""
from __future__ import annotations

import logging
import os
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agent_gateway.exceptions import GatewayError

_log = logging.getLogger(__name__)

# Current API version. Clients may use either /api/* (unversioned, backward
# compat) or /api/v1/* (versioned). The version middleware rewrites the latter
# to the former so all routes are defined once.
API_VERSION = "v1"
_VERSION_PREFIX = f"/api/{API_VERSION}/"

# Gateway API key for auth. When set, all /api/* and /file-api/* requests must
# include it as ``Authorization: Bearer <key>`` or ``X-Gateway-Key: <key>``.
# When unset (local dev), auth is disabled.
_GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "")


def _check_auth(request: Request) -> bool:
    """Return True if the request is authorized. When GATEWAY_API_KEY is unset,
    auth is disabled (backward compat for local dev)."""
    if not _GATEWAY_API_KEY:
        return True
    # Accept either Authorization: Bearer <key> or X-Gateway-Key: <key>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer ") and auth_header[7:] == _GATEWAY_API_KEY:
        return True
    x_key = request.headers.get("X-Gateway-Key", "")
    if x_key == _GATEWAY_API_KEY:
        return True
    return False


def setup_middleware(app: FastAPI) -> None:
    """Attach CORS + API-version + request-ID + auth middleware to the app."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],          # tighten per-deploy
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        """Reject unauthenticated requests to /api/* and /file-api/* when
        GATEWAY_API_KEY is configured."""
        path = request.url.path
        if (path.startswith("/api/") or path.startswith("/file-api/")) and not _check_auth(request):
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "detail": "Missing or invalid API key"},
            )
        return await call_next(request)

    @app.middleware("http")
    async def api_version_middleware(request: Request, call_next):
        """Rewrite ``/api/v1/*`` → ``/api/*`` so versioned clients reach the
        same handlers without duplicating route definitions. The original
        versioned path is preserved in ``request.state.api_version``."""
        path = request.url.path
        if path.startswith(_VERSION_PREFIX):
            request.scope["path"] = "/api/" + path[len(_VERSION_PREFIX):]
            request.state.api_version = API_VERSION
        else:
            request.state.api_version = None
        return await call_next(request)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        """Inject a per-request UUID into request.state and the X-Request-ID
        response header for log correlation."""
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


def setup_exception_handlers(app: FastAPI) -> None:
    """Register global handlers for the GatewayError hierarchy."""

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError):
        rid = getattr(request.state, "request_id", None)
        if rid:
            _log.warning("gateway error %s: %s (request_id=%s)",
                         type(exc).__name__, exc.detail, rid)
        else:
            _log.warning("gateway error %s: %s", type(exc).__name__, exc.detail)
        body: dict = {"error": exc.detail, "type": type(exc).__name__}
        if exc.extra:
            body.update(exc.extra)
        return JSONResponse(status_code=exc.status_code, content=body)
