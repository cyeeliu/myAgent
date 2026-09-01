"""Team definition types and config parsing.

Extracts structured types from the raw dict shape persisted in
``agents_config.json`` so ``start_team`` and ``run_team_info`` can
operate on validated dataclasses instead of ad-hoc ``dict.get()`` chains.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TeamRole(Enum):
    """Role of a teammate within a 3-tier team hierarchy."""

    MEMBER = "member"
    LEADER = "leader"


@dataclass(frozen=True)
class MemberDef:
    """One predefined team member entry from ``agents_config.json``."""

    member_name: str
    display_name: str = ""
    persona: str = ""
    prompt_hint: str = ""
    agent_key: str = ""

    @property
    def effective_display_name(self) -> str:
        """Display name falling back to member name."""
        return self.display_name or self.member_name


@dataclass(frozen=True)
class LeaderDef:
    """Team leader configuration from ``agents_config.json``."""

    member_name: str = ""
    display_name: str = ""
    agent_key: str = ""
    persona: str = ""

    def effective_name(self, team_name: str) -> str:
        """Leader member name, defaulting to ``{team_name}-leader``."""
        return self.member_name or f"{team_name}-leader"

    def effective_display_name(self, team_name: str) -> str:
        """Display name falling back to the effective member name."""
        return self.display_name or self.effective_name(team_name)


@dataclass(frozen=True)
class TeammateTemplate:
    """Fallback template for spawning a dynamic worker when no
    ``predefined_members`` are configured."""

    agent_key: str = ""


@dataclass(frozen=True)
class TeamConfig:
    """A fully parsed team entry from ``agents_config.json``.

    All fields have safe defaults so a partially-saved team config
    does not crash on load.
    """

    team_name: str
    leader: LeaderDef = field(default_factory=LeaderDef)
    predefined_members: list[MemberDef] = field(default_factory=list)
    teammate: TeammateTemplate = field(default_factory=TeammateTemplate)
    lifecycle: str = ""
    teammate_mode: str = ""
    spawn_mode: str = ""
    enable_permissions: str = ""


def _parse_member(raw: dict) -> MemberDef | None:
    """Parse one member dict, returning ``None`` if it has no member_name."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("member_name", "")
    if not name:
        return None
    return MemberDef(
        member_name=name,
        display_name=raw.get("display_name", "") or "",
        persona=raw.get("persona", "") or "",
        prompt_hint=raw.get("prompt_hint", "") or "",
        agent_key=raw.get("agent_key", "") or "",
    )


def _parse_leader(raw: dict | None) -> LeaderDef:
    """Parse the leader dict (may be ``None`` or empty)."""
    if not raw or not isinstance(raw, dict):
        return LeaderDef()
    return LeaderDef(
        member_name=raw.get("member_name", "") or "",
        display_name=raw.get("display_name", "") or "",
        agent_key=raw.get("agent_key", "") or "",
        persona=raw.get("persona", "") or "",
    )


def _parse_teammate_template(raw: dict | None) -> TeammateTemplate:
    """Parse the teammate template dict (may be ``None``)."""
    if not raw or not isinstance(raw, dict):
        return TeammateTemplate()
    return TeammateTemplate(agent_key=raw.get("agent_key", "") or "")


def parse_team_config(team_name: str, raw: dict) -> TeamConfig:
    """Parse a raw team dict from ``agents_config.json`` into a
    validated :class:`TeamConfig`.

    ``raw`` is the entry returned by ``agents.get_team(team_name)``.
    Missing keys default safely; invalid member entries are skipped.
    """
    members = []
    for m in raw.get("predefined_members") or []:
        parsed = _parse_member(m)
        if parsed is not None:
            members.append(parsed)

    return TeamConfig(
        team_name=team_name,
        leader=_parse_leader(raw.get("leader")),
        predefined_members=members,
        teammate=_parse_teammate_template(raw.get("teammate")),
        lifecycle=raw.get("lifecycle", "") or "",
        teammate_mode=raw.get("teammate_mode", "") or "",
        spawn_mode=raw.get("spawn_mode", "") or "",
        enable_permissions=raw.get("enable_permissions", "") or "",
    )


def resolve_members(config: TeamConfig) -> list[MemberDef]:
    """Return the effective member roster for a team.

    If ``predefined_members`` is non-empty, return it as-is.
    Otherwise, if a teammate template ``agent_key`` is set, synthesize
    one dynamic worker from it. Returns ``[]`` if neither is configured.
    """
    if config.predefined_members:
        return list(config.predefined_members)
    if config.teammate.agent_key:
        return [MemberDef(
            member_name=f"{config.team_name}-worker",
        )]
    return []
