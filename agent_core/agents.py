"""agent_core.agents — runtime subagent definitions (Claude-Code-style).

Mirrors `skills.py` but stores JSON config (not markdown+frontmatter) under
`REPO_ROOT/.agents/<name>.json`. The main agent dispatches a defined agent by
name via the `task` tool (`task(description=..., agent=<name>)`); see
`subagent.spawn_subagent`.

Shape:
    {"name": "researcher", "description": "...", "prompt": "You are...",
     "model": null, "tools": ["bash","read_file","write_file","edit_file","glob"]}

`model: null` → inherit the global model config. `tools` omitted/empty → the
subagent default tool set. Names are restricted to `[A-Za-z0-9_-]+` to prevent
path traversal. `model.json` (the global model config, see model_config.py) is
excluded from the agent list.
"""
import json
import re
from pathlib import Path
from agent_core.env import REPO_ROOT

AGENTS_DIR = REPO_ROOT / ".agents"

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_DEFAULT_TOOLS = ["bash", "read_file", "write_file", "edit_file", "glob"]
# model.json (global model config) and agents_config.json (the Agent-tab config)
# live in the same dir; never treat them as agent definitions.
_RESERVED = {"model", "agents_config"}

# The Agent config tab's structured config (agents + teams), persisted here.
CONFIG_PATH = AGENTS_DIR / "agents_config.json"


def _validate_name(name: str) -> str:
    if not name or not _NAME_RE.match(name):
        raise ValueError(
            f"invalid agent name: {name!r} (must match [A-Za-z0-9_-]+)")
    return name


def _path_for(name: str) -> Path:
    return AGENTS_DIR / f"{_validate_name(name)}.json"


def _read_safe(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def list_agents() -> list[dict]:
    """Return [{name, description, model, tools}] for all .agents/*.json.
    Missing dir → []; corrupt JSON skipped (never raises). Excludes model.json."""
    if not AGENTS_DIR.exists():
        return []
    out = []
    for path in sorted(AGENTS_DIR.glob("*.json")):
        stem = path.stem
        if stem in _RESERVED:
            continue
        data = _read_safe(path)
        if not data:
            continue
        out.append({
            "name": data.get("name", stem),
            "description": data.get("description", ""),
            "model": data.get("model"),
            "tools": data.get("tools") or list(_DEFAULT_TOOLS),
        })
    return out


def get_agent(name: str) -> dict | None:
    """Return full def {name, description, prompt, model, tools} or None."""
    try:
        path = _path_for(name)
    except ValueError:
        return None
    if not path.exists():
        return None
    data = _read_safe(path)
    if not data:
        return None
    return {
        "name": data.get("name", name),
        "description": data.get("description", ""),
        "prompt": data.get("prompt", ""),
        "model": data.get("model"),
        "tools": data.get("tools") or list(_DEFAULT_TOOLS),
    }


def save_agent(name: str, description: str, prompt: str,
               model: str | None, tools: list[str]) -> dict:
    """Validate name, mkdir .agents, write <name>.json, return the def."""
    _validate_name(name)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    defn = {
        "name": name,
        "description": description,
        "prompt": prompt,
        "model": model,
        "tools": tools or list(_DEFAULT_TOOLS),
    }
    _path_for(name).write_text(json.dumps(defn, indent=2, ensure_ascii=False))
    return defn


def delete_agent(name: str) -> bool:
    """Validate name, unlink if exists, return whether it existed."""
    path = _path_for(name)
    if not path.exists():
        return False
    path.unlink()
    return True


def scan_agents() -> str:
    """Catalog for system-prompt injection, mirrors list_skills() format.
    Empty → '(no agents defined)'."""
    agents = list_agents()
    if not agents:
        return "(no agents defined)"
    return "\n".join(f"- {a['name']}: {a['description']}" for a in agents)


# ── Agent config tab (multi-agent + teams) ──
# The frontend ConfigPanel "Agent" tab edits a list of agents (each {name, model,
# skills}) and a list of teams (full team orchestration entries). It loads them
# from flat config keys (agent_name_${i}, agent_model_${i}, agent_skills_${i},
# team_${i}_name, …) on `config.get`, and saves them as a structured payload
# ({agents: Record<name,{model,skills}>, team: [...]}) on `config.save_all`. We
# persist the structured form to agents_config.json and expose flat keys for
# config.get. We also sync each agent to a <name>.json subagent definition
# (marked managed=True) so it appears in scan_agents() and is usable via the
# `task` tool; removing an agent in the UI deletes its managed subagent def.

def get_agents_config() -> dict:
    """Return {"agents": [...], "team": [...]} persisted by the Agent tab."""
    if not CONFIG_PATH.exists():
        return {"agents": [], "team": []}
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {"agents": [], "team": []}
    return {
        "agents": data.get("agents") or [],
        "team": data.get("team") or [],
    }


def write_agents_config(agents, team) -> dict:
    """Persist the Agent-tab config and sync subagent defs.

    `agents` may be the save_all shape (Record<name, {model, skills}>) or a list
    of {name, model, skills}. `team` is a list of team entries. Returns the
    normalized config that was written."""
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    agent_list = []
    if isinstance(agents, dict):
        items = list(agents.items())
    elif isinstance(agents, list):
        items = [(a.get("name", ""), a) for a in agents if isinstance(a, dict)]
    else:
        items = []
    for name, a in items:
        if not isinstance(a, dict):
            continue
        if not name or not _NAME_RE.match(str(name)):
            continue
        model = a.get("model")
        if isinstance(model, dict):
            model_name = model.get("model") or ""
        else:
            model_name = model or ""
        skills = a.get("skills") or []
        agent_list.append({
            "name": str(name),
            "model_name": str(model_name or ""),
            "skills": [str(s) for s in skills if s],
        })
    team_list = team if isinstance(team, list) else []
    cfg = {"agents": agent_list, "team": team_list}
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    _sync_subagent_defs(agent_list)
    return cfg


def _sync_subagent_defs(agent_list: list[dict]) -> None:
    """Upsert a managed subagent def for each UI agent; delete managed defs whose
    name is no longer present. Hand-authored defs (no managed flag) are left."""
    desired = {a["name"] for a in agent_list}
    for path in AGENTS_DIR.glob("*.json"):
        stem = path.stem
        if stem in _RESERVED or stem in desired:
            continue
        data = _read_safe(path)
        if data and data.get("managed"):
            try:
                path.unlink()
            except OSError:
                pass
    for a in agent_list:
        defn = {
            "name": a["name"],
            "description": a["name"],
            "prompt": "",
            "model": a.get("model_name") or None,
            "tools": list(_DEFAULT_TOOLS),
            "skills": list(a.get("skills") or []),
            "managed": True,
        }
        try:
            _path_for(a["name"]).write_text(json.dumps(defn, indent=2, ensure_ascii=False))
        except ValueError:
            pass


def get_team(team_name: str) -> dict | None:
    """Return a team entry from agents_config.json by team_name, or None."""
    if not team_name:
        return None
    for t in get_agents_config().get("team") or []:
        if isinstance(t, dict) and t.get("team_name") == team_name:
            return t
    return None


def list_team_names() -> list[str]:
    """All saved team names (for error messages / catalog)."""
    return [t.get("team_name", "")
            for t in get_agents_config().get("team") or []
            if isinstance(t, dict) and t.get("team_name")]


def agents_flat_config() -> dict:
    """Flat config keys for the Agent tab, merged into config.get() so the UI
    populates. Emits agent_name_${i}/agent_model_${i}/agent_skills_${i} and the
    team_${i}_* keys (including predefined_members as JSON), padded to 10 so
    stale entries clear on reload."""
    cfg = get_agents_config()
    flat: dict[str, str] = {}
    agents = cfg.get("agents") or []
    for i, a in enumerate(agents[:10]):
        flat[f"agent_name_{i}"] = a.get("name", "")
        flat[f"agent_model_{i}"] = a.get("model_name", "") or \
            (a.get("model", {}).get("model", "") if isinstance(a.get("model"), dict) else "")
        flat[f"agent_skills_{i}"] = ",".join(a.get("skills") or [])
    for i in range(len(agents), 10):
        flat[f"agent_name_{i}"] = ""
        flat[f"agent_model_{i}"] = ""
        flat[f"agent_skills_{i}"] = ""
    teams = cfg.get("team") or []
    for i, t in enumerate(teams[:10]):
        flat[f"team_{i}_name"] = t.get("team_name", "")
        flat[f"team_{i}_lifecycle"] = t.get("lifecycle", "")
        flat[f"team_{i}_teammate_mode"] = t.get("teammate_mode", "")
        flat[f"team_{i}_spawn_mode"] = t.get("spawn_mode", "")
        flat[f"team_{i}_enable_permissions"] = "true" if t.get("enable_permissions") else "false"
        leader = t.get("leader") or {}
        flat[f"team_{i}_leader_member_name"] = leader.get("member_name", "")
        flat[f"team_{i}_leader_display_name"] = leader.get("display_name", "")
        flat[f"team_{i}_leader_persona"] = leader.get("persona", "")
        flat[f"team_{i}_leader_agent_key"] = leader.get("agent_key", "")
        teammate = t.get("teammate") or {}
        flat[f"team_{i}_teammate_agent_key"] = teammate.get("agent_key", "")
        flat[f"team_{i}_predefined_members"] = json.dumps(
            t.get("predefined_members") or [], ensure_ascii=False)
    team_keys = ("name", "lifecycle", "teammate_mode", "spawn_mode",
                 "enable_permissions", "leader_member_name", "leader_display_name",
                 "leader_persona", "leader_agent_key", "teammate_agent_key",
                 "predefined_members")
    for i in range(len(teams), 10):
        for k in team_keys:
            flat[f"team_{i}_{k}"] = ""
    return flat


def agents_flat_to_structured(flat: dict) -> tuple[list, list]:
    """Inverse of agents_flat_config: reconstruct (agents, team) from flat config
    keys (both team_${i}_* and team_*_${i} shapes). Used by config.set."""
    agents: list[dict] = []
    for i in range(10):
        name = flat.get(f"agent_name_{i}") or flat.get(f"agent_{i}_name") or ""
        if not name:
            continue
        model_name = flat.get(f"agent_model_{i}") or flat.get(f"agent_{i}_model") or ""
        skills = [s.strip() for s in
                  (flat.get(f"agent_skills_{i}") or flat.get(f"agent_{i}_skills") or "").split(",")
                  if s.strip()]
        agents.append({
            "name": name,
            "model": {"provider": "", "api_base": "", "api_key": "", "model": model_name},
            "skills": skills,
        })
    teams: list[dict] = []
    for i in range(10):
        tname = flat.get(f"team_name_{i}") or flat.get(f"team_{i}_name") or ""
        if not tname:
            continue
        members_json = flat.get(f"team_predefined_members_{i}") or \
            flat.get(f"team_{i}_predefined_members") or ""
        try:
            members = json.loads(members_json) if members_json else []
        except (json.JSONDecodeError, TypeError):
            members = []
        ep = flat.get(f"team_enable_permissions_{i}") or \
            flat.get(f"team_{i}_enable_permissions") or "false"
        teams.append({
            "team_name": tname,
            "lifecycle": flat.get(f"team_lifecycle_{i}") or flat.get(f"team_{i}_lifecycle") or "",
            "teammate_mode": flat.get(f"team_teammate_mode_{i}") or flat.get(f"team_{i}_teammate_mode") or "",
            "spawn_mode": flat.get(f"team_spawn_mode_{i}") or flat.get(f"team_{i}_spawn_mode") or "",
            "enable_permissions": str(ep).lower() in ("true", "1", "yes"),
            "leader": {
                "member_name": flat.get(f"team_leader_member_name_{i}") or flat.get(f"team_{i}_leader_member_name") or "",
                "display_name": flat.get(f"team_leader_display_name_{i}") or flat.get(f"team_{i}_leader_display_name") or "",
                "persona": flat.get(f"team_leader_persona_{i}") or flat.get(f"team_{i}_leader_persona") or "",
                "agent_key": flat.get(f"team_leader_agent_key_{i}") or flat.get(f"team_{i}_leader_agent_key") or "",
            },
            "teammate": {
                "agent_key": flat.get(f"team_teammate_agent_key_{i}") or flat.get(f"team_{i}_teammate_agent_key") or "",
            },
            "predefined_members": members,
        })
    return agents, teams
