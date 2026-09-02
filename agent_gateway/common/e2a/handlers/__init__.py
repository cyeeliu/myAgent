"""Handler modules for agent_compat.

Importing this package triggers registration of all handlers on the
``dispatcher.registry`` singleton. ``agent_compat.py`` imports this package
to ensure all handlers are registered before any request is dispatched.
"""
from . import (  # noqa: F401 — imports for side effect (handler registration)
    chat,
    session,
    history,
    config,
    models,
    permissions,
    skills,
    agents,
    files,
    misc,
    eval,
)
