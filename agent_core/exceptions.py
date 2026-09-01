"""agent_core.exceptions — exception hierarchy for the agent core.

All agent_core exceptions derive from ``AgentCoreError`` so callers can catch
the entire family with one ``except AgentCoreError``. Sub-categories let
callers handle specific failure modes (retryable transport errors vs.
permanent config errors vs. permission denials).

Usage::

    from agent_core.exceptions import AgentCoreError, ToolError

    try:
        ...
    except ToolError as exc:
        # tool handler failed — surface to the model as a tool_result error
        ...
    except AgentCoreError as exc:
        # catch-all for the agent_core family
        ...
"""
from __future__ import annotations


class AgentCoreError(Exception):
    """Base for all agent_core exceptions."""


# ── Config / environment ──

class ConfigError(AgentCoreError):
    """Configuration is missing or invalid (e.g. MODEL_ID not set)."""


# ── Model / transport ──

class ModelError(AgentCoreError):
    """The LLM returned an unrecoverable error (bad model, auth, …)."""


class RateLimitError(ModelError):
    """A 429 rate-limit that exhausted the retry budget."""


class OverloadedError(ModelError):
    """A 529 overloaded that exhausted the retry budget."""


class ContextLengthError(ModelError):
    """The prompt exceeds the model's context window (reactive_compact failed)."""


# ── Tools ──

class ToolError(AgentCoreError):
    """A built-in tool handler raised an error."""


class ToolNotFoundError(ToolError):
    """The requested tool name is not in BUILTIN_TOOLS or MCP tools."""


class ToolTimeoutError(ToolError):
    """A tool execution exceeded its timeout."""


# ── Permissions ──

class PermissionDeniedError(AgentCoreError):
    """A tool_use was denied by the permission policy or safety backstop."""


# ── Session / state ──

class SessionError(AgentCoreError):
    """Session state is invalid or corrupted."""


class CompactionError(AgentCoreError):
    """Context compaction failed to reduce the context below the limit."""


# ── MCP ──

class MCPError(AgentCoreError):
    """An MCP server operation failed (connect, call, deploy)."""


class MCPConnectionError(MCPError):
    """Failed to connect to an MCP server."""


# ── Skills ──

class SkillError(AgentCoreError):
    """A skill operation failed (load, install, uninstall)."""
