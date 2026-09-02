"""Method dispatch registry for agent_compat.

Replaces the 861-line if/elif chain in ``agent_compat.py`` with a declarative
handler registry. Each handler is an async function registered via the
``@handler(ReqMethod.X)`` decorator; ``dispatch()`` looks up and calls it.

Usage in a handler module::

    from .dispatcher import handler, HandlerContext
    from ..schema.message import ReqMethod

    @handler(ReqMethod.SESSION_LIST)
    async def list_sessions(req, ctx: HandlerContext):
        ...
        return AgentResponse(req.request_id, payload={...})
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

from ..schema.agent import AgentResponse
from ..schema.message import ReqMethod


# A handler is an async function (req, ctx) → AgentResponse.
HandlerFn = Callable[[Any, "HandlerContext"], Awaitable[AgentResponse]]


@dataclass
class HandlerContext:
    """Passed to every handler — carries the session manager + event loop."""
    sessions: Any          # SessionManager
    loop: asyncio.AbstractEventLoop


class MethodRegistry:
    """Maps ``ReqMethod`` → handler function. Handlers register at import time
    via the module-level ``@handler`` decorator."""

    def __init__(self):
        self._handlers: dict[ReqMethod, HandlerFn] = {}

    def register(self, method: ReqMethod, fn: HandlerFn) -> HandlerFn:
        """Register a handler for a method. Returns ``fn`` (for use as a
        decorator). Overwriting an existing handler is allowed (last wins)."""
        self._handlers[method] = fn
        return fn

    def dispatch(self, req, ctx: HandlerContext) -> Awaitable[AgentResponse]:
        """Look up and call the handler for ``req.method``.

        Returns an ``AgentResponse`` with an error if no handler is registered
        for the method (unhandled method)."""
        fn = self._handlers.get(req.method)
        if fn is None:
            return AgentResponse(req.request_id, ok=False,
                                 error=f"unhandled method {req.method.value}")
        return fn(req, ctx)

    def methods(self) -> list[str]:
        """Return sorted list of registered method names (for debugging)."""
        return sorted(m.value for m in self._handlers)


# Module-level singleton — handlers register on it at import time.
registry = MethodRegistry()


def handler(method: ReqMethod) -> Callable[[HandlerFn], HandlerFn]:
    """Decorator: register a handler function for the given ReqMethod.

    Usage::

        @handler(ReqMethod.SESSION_LIST)
        async def list_sessions(req, ctx):
            ...
    """
    def decorator(fn: HandlerFn) -> HandlerFn:
        registry.register(method, fn)
        return fn
    return decorator
