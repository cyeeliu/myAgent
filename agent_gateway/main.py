"""FastAPI gateway entry point.

The application factory and all wiring live in ``agent_gateway.app``. This
module is kept as a thin shim so that ``uvicorn agent_gateway.main:app`` (used
in Dockerfile.gateway and docker-compose) continues to work unchanged.

Run locally::

    uvicorn agent_gateway.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from agent_gateway.app import create_app

app = create_app()
