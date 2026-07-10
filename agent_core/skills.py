"""agent_core.skills — extracted from code.py (s20 comprehensive agent)."""
import yaml
from agent_core.env import REPO_ROOT, workspace_dir


def _skills_dir():
    """Per-workspace skills dir (shared across sessions). Lives under the
    workspace root, not REPO_ROOT, so each mounted workspace owns its skills."""
    return workspace_dir() / "skills"

# Backward-compat alias (module-level, points at the default workspace's skills
# dir at import time). Prefer _skills_dir() for runtime resolution.
SKILLS_DIR = _skills_dir()

SKILL_REGISTRY: dict[str, dict] = {}

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, parts[2].strip()

def scan_skills():
    SKILL_REGISTRY.clear()
    skills_dir = _skills_dir()
    if not skills_dir.exists():
        return []
    for directory in sorted(skills_dir.iterdir()):
        if not directory.is_dir():
            continue
        manifest = directory / "SKILL.md"
        if not manifest.exists():
            continue
        raw = manifest.read_text()
        meta, _ = _parse_frontmatter(raw)
        name = meta.get("name", directory.name)
        desc = meta.get("description", raw.split("\n")[0].lstrip("#").strip())
        SKILL_REGISTRY[name] = {
            "name": name,
            "description": desc,
            "content": raw,
        }
    return list(SKILL_REGISTRY.values())

def list_skills() -> str:
    if not SKILL_REGISTRY:
        return "(no skills found)"
    return "\n".join(
        f"- {skill['name']}: {skill['description']}"
        for skill in SKILL_REGISTRY.values())

def load_skill(name: str) -> str:
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        available = ", ".join(SKILL_REGISTRY.keys()) or "(none)"
        return f"Skill not found: {name}. Available: {available}"
    return skill["content"]
