"""routing — session binding + in-process agent client (mirrors myagent gateway/routing)."""
from .session_map import SessionMap
from .agent_client import AgentServerClient
from .interaction_context import InteractionContext

__all__ = ["SessionMap", "AgentServerClient", "InteractionContext"]
