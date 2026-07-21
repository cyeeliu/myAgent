"""Unified message model + RPC method enum (mirrors jiuwenswarm common/schema/message).

`ReqMethod` enumerates every method the WebChannel exposes over the method-routed
WS (`{type:'req', id, method, params}`). The frontend webClient dispatches by
these names; the gateway app_web_handlers registers one handler per method.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional


class Mode(str, Enum):
    """Agent execution mode (mirrors jiuwenswarm AgentMode, mapped to agent_core)."""
    PLAN = "agent.plan"        # default — plan then act
    FAST = "agent.fast"        # skip plan, act directly
    TEAM = "team"              # multi-teammate swarm
    AUTO_HARNESS = "auto_harness"  # extension/harness orchestration


class ReqMethod(str, Enum):
    # ── chat ──
    CHAT_SEND = "chat.send"
    CHAT_INTERRUPT = "chat.interrupt"     # pause/cancel/supplement/resume via intent
    CHAT_USER_ANSWER = "chat.user_answer"  # resolve a permission/ask_user_question

    # ── session ──
    SESSION_LIST = "session.list"
    SESSION_CREATE = "session.create"
    SESSION_SWITCH = "session.switch"
    SESSION_DELETE = "session.delete"
    SESSION_RENAME = "session.rename"
    SESSION_STATUS = "session.status"

    # ── history ──
    HISTORY_GET = "history.get"           # paginated turn replay

    # ── config / models ──
    CONFIG_GET = "config.get"
    CONFIG_SET = "config.set"
    CONFIG_SAVE_ALL = "config.save_all"  # unified save: {config?, models?, agents?, team?}
    CONFIG_VALIDATE_MODEL = "config.validate_model"  # probe api_base/api_key/model with a minimal request
    MODELS_LIST = "models.list"
    MODELS_REPLACE_ALL = "models.replace_all"

    # ── permissions (security panel: per-tool allow/ask/deny) ──
    PERMISSIONS_TOOLS_GET = "permissions.tools.get"
    PERMISSIONS_TOOLS_UPDATE = "permissions.tools.update"
    PERMISSIONS_TOOLS_DELETE = "permissions.tools.delete"

    # ── skills ──
    SKILLS_LIST = "skills.list"
    SKILLS_INSTALLED = "skills.installed"
    SKILLS_GET = "skills.get"
    SKILLS_TOGGLE = "skills.toggle"
    SKILLS_UNINSTALL = "skills.uninstall"
    SKILLS_INSTALL = "skills.install"
    SKILLS_IMPORT_LOCAL = "skills.import_local"
    SKILLS_MARKETPLACE_LIST = "skills.marketplace.list"
    # Online marketplaces (backed by agent_gateway/skill_marketplaces.py).
    SKILLS_SKILLNET_SEARCH = "skills.skillnet.search"
    SKILLS_SKILLNET_INSTALL = "skills.skillnet.install"
    SKILLS_SKILLNET_INSTALL_STATUS = "skills.skillnet.install_status"
    SKILLS_SKILLNET_EVALUATE = "skills.skillnet.evaluate"
    SKILLS_CLAWHUB_SEARCH = "skills.clawhub.search"
    SKILLS_CLAWHUB_GET_TOKEN = "skills.clawhub.get_token"
    SKILLS_CLAWHUB_SET_TOKEN = "skills.clawhub.set_token"
    SKILLS_CLAWHUB_DOWNLOAD = "skills.clawhub.download"
    SKILLS_TEAMSKILLS_SEARCH = "skills.teamskillshub.search"
    SKILLS_TEAMSKILLS_INSTALL = "skills.teamskillshub.install"
    SKILLS_TEAMSKILLS_INFO = "skills.teamskillshub.info"
    SKILLS_SKILLHUB_SEARCH = "skills.skillhub.search"
    SKILLS_SKILLHUB_INSTALL = "skills.skillhub.install"
    SKILLS_SKILLHUB_INFO = "skills.skillhub.info"

    # ── agents ──
    AGENTS_LIST = "agents.list"
    AGENTS_GET = "agents.get"
    AGENTS_CREATE = "agents.create"
    AGENTS_UPDATE = "agents.update"
    AGENTS_DELETE = "agents.delete"

    # ── path / files ──
    PATH_GET = "path.get"
    FILES_LIST = "files.list"

    # ── tts ──
    TTS_SYNTHESIZE = "tts.synthesize"

    # ── commands (slash) ──
    COMMAND_COMPACT = "command.compact"
    COMMAND_CONTEXT = "command.context"
    COMMAND_MODEL = "command.model"

    # ── runtime status ──
    MEMORY_COMPUTE = "memory.compute"  # process RSS + used % for the ToolPanel status card

    # ── channel / heartbeat ──
    CHANNEL_GET = "channel.get"
    HEARTBEAT_PING = "heartbeat.ping"

    @classmethod
    def from_str(cls, name: str) -> Optional["ReqMethod"]:
        try:
            return cls(name)
        except ValueError:
            return None


@dataclass
class Message:
    """A normalized chat message (role + content blocks), channel-agnostic."""
    role: Literal["user", "assistant", "system", "tool"]
    content: Any
    session_id: Optional[str] = None
    timestamp: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }
