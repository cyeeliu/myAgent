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
# model.json lives in the same dir (global model config); never treat it as an agent.
_RESERVED = {"model"}


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
