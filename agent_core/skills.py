"""agent_core.skills — extracted from code.py (s20 comprehensive agent)."""
import json
import shutil
from pathlib import Path

import yaml
from agent_core.env import workspace_dir


def _skills_dir():
    """Per-workspace skills dir (shared across sessions). Lives under the
    workspace root, not REPO_ROOT, so each mounted workspace owns its skills."""
    return workspace_dir() / "skills"

# Backward-compat alias (module-level, points at the default workspace's skills
# dir at import time). Prefer _skills_dir() for runtime resolution.
SKILLS_DIR = _skills_dir()

SKILL_REGISTRY: dict[str, dict] = {}


def _disabled_state_path() -> Path:
    """JSON file recording which skills the user has disabled. Lives alongside
    the skills so it persists across sessions and container restarts."""
    return _skills_dir() / ".disabled.json"


def _read_disabled() -> set[str]:
    try:
        data = json.loads(_disabled_state_path().read_text("utf-8"))
        return {str(x) for x in (data.get("disabled") or [])}
    except Exception:
        return set()


def _write_disabled(names: set[str]) -> None:
    p = _disabled_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"disabled": sorted(names)}), "utf-8")


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


def _read_marketplace_manifest(skill_dir: Path) -> dict:
    """Read the optional `.marketplace.json` a marketplace install writes
    alongside SKILL.md. Carries source/version/author/summary/url so installed
    skills show provenance in the UI instead of appearing as anonymous locals."""
    try:
        return json.loads((skill_dir / ".marketplace.json").read_text("utf-8"))
    except Exception:
        return {}


# Dirs that never contain skills but frequently hold example/docs SKILL.md
# copies in multi-skill repos. Skipped when recursing so those aren't mistaken
# for real skills.
_SKILL_NOISE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "docs", "doc", "tests", "test", "examples", "example",
    "assets", "hooks", "scripts", "references", ".idea", ".vscode",
}


def _find_skill_files(top: Path) -> list[Path]:
    """Return every SKILL.md that defines a skill under `top`.

    A single-skill install (clawhub/skillhub/skillnet single, or a manual
    `import_local_skill`) has SKILL.md at the repo root — use only that so
    example SKILL.md files nested in docs/tests aren't double-counted.

    A multi-skill repo (e.g. skillnet's `superpowers`, which has
    `skills/<name>/SKILL.md` and NO root SKILL.md) is recursed: every SKILL.md
    under `top` is a skill, skipping noise dirs and dot-dirs. This is what makes
    a freshly installed skillnet bundle show up in the my-skills tab at all."""
    root_md = top / "SKILL.md"
    if root_md.exists():
        return [root_md]
    found = []
    for path in sorted(top.rglob("SKILL.md")):
        rel = path.relative_to(top)
        # skip if any parent part is noise or a dot-dir
        if any(part in _SKILL_NOISE_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        found.append(path)
    return found


def _read_marketplace_manifest_ancestor(skill_dir: Path, top: Path) -> dict:
    """Walk from `skill_dir` up to `top` looking for `.marketplace.json`.

    A nested skill (e.g. `superpowers/skills/brainstorming/`) doesn't have its
    own manifest — the install wrote one at the repo root (`superpowers/`).
    Inherit it so the nested skill still shows its marketplace source/version."""
    d = skill_dir
    while True:
        mp = _read_marketplace_manifest(d)
        if mp:
            return mp
        if d == top or d.parent == d:
            break
        d = d.parent
    return {}


def resolve_install_dst(slug: str, source: str) -> tuple[Path, bool]:
    """Pick a unique install dir under workspace/skills/ for a marketplace skill.

    Returns ``(dst, same_source_exists)``:
    - ``<slug>`` free → ``(<slug>, False)``.
    - ``<slug>`` exists with the SAME ``source`` (re-install) → ``(<slug>, True)``
      so the caller can be idempotent or overwrite with ``force``.
    - ``<slug>`` exists with a DIFFERENT source (cross-source name collision,
      e.g. a `legal` from clawhub already there when skillhub's `legal` is
      installed) → ``(<slug>@<source>, False)`` so both coexist instead of the
      second install failing with "already installed".
    """
    skills_dir = _skills_dir()
    base = skills_dir / slug
    if not base.exists():
        return base, False
    existing_mp = _read_marketplace_manifest(base)
    if (existing_mp.get("source") or "") == (source or ""):
        return base, True
    candidate = skills_dir / f"{slug}@{source}"
    i = 2
    while candidate.exists():
        candidate = skills_dir / f"{slug}@{source}-{i}"
        i += 1
    return candidate, False


def scan_skills():
    SKILL_REGISTRY.clear()
    skills_dir = _skills_dir()
    if not skills_dir.exists():
        return []
    disabled = _read_disabled()
    # Pass 1: collect every SKILL.md under skills/ (recurses multi-skill repos).
    entries = []
    for top in sorted(skills_dir.iterdir()):
        if not top.is_dir() or top.name.startswith("."):
            continue
        for manifest in _find_skill_files(top):
            try:
                raw = manifest.read_text("utf-8", "replace")
            except Exception:
                continue
            meta, _ = _parse_frontmatter(raw)
            skill_dir = manifest.parent
            entries.append({
                "manifest": manifest,
                "skill_dir": skill_dir,
                "raw": raw,
                "fm_name": meta.get("name", skill_dir.name),
                "desc": meta.get("description", raw.split("\n")[0].lstrip("#").strip()),
                "mp": _read_marketplace_manifest_ancestor(skill_dir, top),
            })
    # Pass 2: count frontmatter names so collisions are detected up front. This
    # makes disambiguation deterministic (a skill always gets the same registry
    # name regardless of scan order) so the enabled/disabled flag stays stable.
    name_counts: dict[str, int] = {}
    for e in entries:
        name_counts[e["fm_name"]] = name_counts.get(e["fm_name"], 0) + 1
    # Pass 3: assign unique registry names. A skill whose frontmatter name is
    # unique keeps it. When two skills share a frontmatter name (e.g. "Legal"
    # from clawhub and "Legal" from skillhub), each is suffixed with its
    # marketplace source so both coexist in the catalog instead of the second
    # silently overwriting the first.
    used: set[str] = set()
    for e in entries:
        fm = e["fm_name"]
        if name_counts[fm] == 1:
            name = fm
        else:
            src = (e["mp"].get("source") or "").strip()
            suffix = f" ({src})" if src else f" ({e['skill_dir'].name})"
            name = f"{fm}{suffix}"
        if name in used:
            k = 2
            cand = f"{name} {k}"
            while cand in used:
                k += 1
                cand = f"{name} {k}"
            name = cand
        used.add(name)
        mp = e["mp"]
        SKILL_REGISTRY[name] = {
            "name": name,
            "description": e["desc"],
            "content": e["raw"],
            # These live in the workspace skills/ dir — they're installed.
            # source="local" is the "installed workspace skill" marker the
            # frontend my-skills tab keys on; marketplace_source carries the
            # origin (clawhub/skillhub/skillnet/...) for display only. A new
            # marketplace or a manual import (no manifest) still gets
            # source="local" and shows up — no source allowlist anywhere.
            "source": "local",
            "is_builtin": True,
            "is_builtin_source": True,
            "marketplace_source": mp.get("source") or "",
            "version": mp.get("version") or "",
            "author": mp.get("author") or "",
            "marketplace_url": mp.get("url") or "",
            "file_path": str(e["manifest"]),
            "enabled": name not in disabled,
        }
    return list(SKILL_REGISTRY.values())

def get_skill(name: str) -> dict | None:
    """Return the full skill record (name/description/content/file_path/...) or
    None if not found. Refreshes the registry first so a freshly added skill is
    visible without a separate scan_skills() call."""
    scan_skills()
    return SKILL_REGISTRY.get(name)

def list_skills(enabled_only: bool = False) -> str:
    """Format the skill catalog for system-prompt injection. Refreshes the
    registry first so newly added/removed/disabled skills are reflected. When
    enabled_only is True, disabled skills are omitted entirely so the agent
    never sees them in the catalog."""
    scan_skills()
    skills = list(SKILL_REGISTRY.values())
    if enabled_only:
        skills = [s for s in skills if s.get("enabled", True)]
    if not skills:
        return "(no skills found)"
    return "\n".join(
        f"- {skill['name']}: {skill['description']}"
        for skill in skills)

def load_skill(name: str) -> str:
    """Return a skill's SKILL.md content for the agent to follow. Refreshes the
    registry first, and refuses to load a disabled skill — disabling is a
    hard off switch, not just a UI marker."""
    scan_skills()
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        available = ", ".join(SKILL_REGISTRY.keys()) or "(none)"
        return f"Skill not found: {name}. Available: {available}"
    if not skill.get("enabled", True):
        return (f"Skill '{name}' is disabled. Enable it in the skills panel "
                "to make it available.")
    return skill["content"]


# ── skill lifecycle: enable/disable, uninstall, install, import ──────────

def set_skill_enabled(name: str, enabled: bool) -> dict:
    """Persist the enabled/disabled state of a skill in
    workspace/skills/.disabled.json. Returns the new state so the caller can
    update its UI without a re-scan."""
    if not name:
        return {"success": False, "detail": "missing name"}
    disabled = _read_disabled()
    if enabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    _write_disabled(disabled)
    return {"success": True, "name": name, "enabled": enabled}


def uninstall_skill(name: str) -> dict:
    """Remove a skill directory from workspace/skills/. Also clears any disabled
    marker for it. Refuses to delete paths outside the skills dir (path
    traversal guard).

    The skill's directory isn't always named `name` — a marketplace install names
    the dir after the slug (e.g. `legal`) while the SKILL.md frontmatter `name`
    may differ (e.g. `Legal`). So locate the dir by matching the frontmatter name
    across the skills dir, falling back to `skills_dir/name`."""
    if not name:
        return {"success": False, "detail": "missing name"}
    skills_dir = _skills_dir()
    resolved_root = skills_dir.resolve()
    target = None
    target_top = None
    # 1. Registry hit — handles disambiguated names like "Legal (skillhub)"
    # produced by scan_skills when two skills share a frontmatter name. The
    # registry carries the exact file_path, so this is unambiguous.
    scan_skills()
    reg = SKILL_REGISTRY.get(name)
    if reg and reg.get("file_path"):
        p = Path(reg["file_path"])
        if p.exists():
            target = p.parent.resolve()
            try:
                rel = p.parent.relative_to(resolved_root)
                target_top = (resolved_root / rel.parts[0]).resolve()
            except Exception:
                target_top = target
    # 2. Fallback: walk by frontmatter name (backward compat for callers using
    # the raw frontmatter name or a dir name).
    if target is None and skills_dir.exists():
        for top in skills_dir.iterdir():
            if not top.is_dir() or top.name.startswith("."):
                continue
            for mf in _find_skill_files(top):
                try:
                    meta, _ = _parse_frontmatter(mf.read_text("utf-8", "replace"))
                except Exception:
                    meta = {}
                if meta.get("name", mf.parent.name) == name:
                    target = mf.parent.resolve()
                    target_top = top.resolve()
                    break
            if target is not None:
                break
    if target is None:
        candidate = (skills_dir / name).resolve()
        if candidate.exists():
            target = candidate
            target_top = candidate
    if target is None:
        # Already gone — clear any stale disabled marker and report success.
        disabled = _read_disabled()
        if name in disabled:
            disabled.discard(name)
            _write_disabled(disabled)
        return {"success": True, "name": name, "detail": "not present"}
    try:
        target.relative_to(resolved_root)
    except ValueError:
        return {"success": False, "detail": "invalid skill name"}
    if not target.is_dir():
        return {"success": False, "detail": "skill path is not a directory"}
    shutil.rmtree(target)
    # If this was a nested skill in a multi-skill repo and no SKILL.md remains
    # in the repo, remove the now-empty repo dir too so the workspace doesn't
    # accumulate orphaned README/assets shells.
    if target_top is not None and target_top != target:
        try:
            if not _find_skill_files(target_top):
                shutil.rmtree(target_top, ignore_errors=True)
        except Exception:
            pass
    disabled = _read_disabled()
    if name in disabled:
        disabled.discard(name)
        _write_disabled(disabled)
    SKILL_REGISTRY.pop(name, None)
    return {"success": True, "name": name}


def _builtin_source_dir(name: str) -> Path | None:
    """Locate a preset skill source dir. In the gateway, presets live under
    /app/skills/<name> (mounted read-only from the repo's skills/). In CLI mode,
    fall back to REPO_ROOT/skills/<name>."""
    for cand in (Path("/app/skills") / name,
                 workspace_dir() / "skills" / name):
        if cand.is_dir() and (cand / "SKILL.md").exists():
            return cand
    return None


def install_skill(spec: str, force: bool = False) -> dict:
    """Install a skill by spec. Currently supports `name@builtin` (and a bare
    name): copies the preset from /app/skills/<name> into workspace/skills/<name>
    (overwriting if force). If the skill is already present and not force,
    reports success (idempotent)."""
    if not spec:
        return {"success": False, "detail": "missing spec"}
    name = spec.split("@", 1)[0].strip()
    if not name:
        return {"success": False, "detail": "missing name"}
    dst, same = resolve_install_dst(name, "builtin")
    if same and not force:
        return {"success": True, "name": name, "detail": "already installed"}
    src = _builtin_source_dir(name)
    if src is None:
        return {"success": False, "detail": f"builtin source for {name} not found"}
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return {"success": True, "name": name}


def import_local_skill(path: str, force: bool = False) -> dict:
    """Import a skill from a local filesystem path (a dir containing SKILL.md,
    or a SKILL.md file). Copies it into workspace/skills/<name>/."""
    if not path:
        return {"success": False, "detail": "missing path"}
    src = Path(path).expanduser()
    if not src.exists():
        return {"success": False, "detail": f"path not found: {path}"}
    if src.is_file():
        # A standalone SKILL.md — derive name from frontmatter or filename.
        raw = src.read_text("utf-8", "replace")
        meta, _ = _parse_frontmatter(raw)
        name = meta.get("name") or src.stem
        dst, same = resolve_install_dst(name, "local")
        if same and not force:
            return {"success": False, "detail": "already installed"}
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "SKILL.md").write_text(raw, "utf-8")
        return {"success": True, "skill": {"name": name}}
    if not (src / "SKILL.md").exists():
        return {"success": False, "detail": "dir has no SKILL.md"}
    raw = (src / "SKILL.md").read_text("utf-8", "replace")
    meta, _ = _parse_frontmatter(raw)
    name = meta.get("name") or src.name
    dst, same = resolve_install_dst(name, "local")
    if same and not force:
        return {"success": False, "detail": "already installed"}
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return {"success": True, "skill": {"name": name}}


def import_upload_skill(filename: str, data: bytes, force: bool = False) -> dict:
    """Import a skill from an uploaded file (a SKILL.md or a .zip of a skill dir).

    Writes the upload to a temp path (extracting zips first), then reuses
    ``import_local_skill`` for name derivation (frontmatter → stem/fallback),
    ``resolve_install_dst`` collision handling, and the final copy into
    workspace/skills/<name>/. Keeps a single source of truth for install logic.
    """
    import io, tempfile, zipfile
    if not data:
        return {"success": False, "detail": "empty upload"}
    if len(data) > 20 * 1024 * 1024:
        return {"success": False, "detail": "file too large (>20MB)"}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        fn = (filename or "SKILL.md").lower()
        if fn.endswith(".zip"):
            try:
                zf = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                return {"success": False, "detail": "bad zip file"}
            # zip-slip guard: reject absolute / parent-traversal entries
            for member in zf.namelist():
                if member.startswith(("/", "\\")) or ".." in Path(member).parts:
                    return {"success": False, "detail": f"unsafe zip entry: {member}"}
            zf.extractall(tmp)
            zf.close()
            # locate SKILL.md: at root, or exactly one child dir has it
            if (tmp / "SKILL.md").exists():
                target = tmp
            else:
                kids = [p for p in tmp.iterdir() if p.is_dir() and (p / "SKILL.md").exists()]
                if len(kids) != 1:
                    return {"success": False, "detail": "zip has no single skill dir with SKILL.md"}
                target = kids[0]
        else:
            # single .md — keep original filename so the stem fallback is meaningful
            target = tmp / (filename or "SKILL.md")
            target.write_bytes(data)
        return import_local_skill(str(target), force=force)


_MARKETPLACE_INSTALLERS: dict = {}


def register_marketplace_installer(source: str, fn) -> None:
    """Register a marketplace install function so the agent's download_skill
    tool can pull from that source by id. Called by the gateway at startup
    (agent_core must not import agent_gateway, so installers are injected).
    fn signature: fn(id: str, force: bool = False) -> dict."""
    _MARKETPLACE_INSTALLERS[source] = fn


def download_skill(source: str, name: str, force: bool = False) -> dict:
    """Download and install a skill from an online marketplace by id/slug.
    Dispatches to the installer registered for `source`:
      clawhub   -> clawhub_download(slug)
      skillhub  -> skillhub_install(slug)
      skillnet  -> skillnet_install(url)   # name is a GitHub repo URL
      teamskills-> teamskills_install(asset_id)
    Installers are registered by the gateway at startup; in CLI mode without
    the gateway, returns 'unsupported source'."""
    fn = _MARKETPLACE_INSTALLERS.get(source)
    if not fn:
        known = ", ".join(sorted(_MARKETPLACE_INSTALLERS)) or "(none)"
        return {"success": False,
                "detail": f"unsupported source: {source} (known: {known})"}
    try:
        return fn(name, force=force)
    except Exception as e:
        return {"success": False, "detail": f"install failed: {e}"}


_MARKETPLACE_SEARCH = None


def register_marketplace_search(fn) -> None:
    """Register the unified marketplace search function (called by the gateway
    at startup). fn(query, source, limit) -> dict."""
    global _MARKETPLACE_SEARCH
    _MARKETPLACE_SEARCH = fn


def search_skill(query: str, source: str | None = None, limit: int = 20) -> dict:
    """Search online skill marketplaces for skills matching a task/query.
    Returns {success, results:[{source, id, name, summary, stars, downloads}]}.
    Pass source + id to download_skill to install. source: optional, one of
    clawhub/skillhub/skillnet/teamskills; omit to search all. Requires the
    gateway (search backends registered at gateway startup)."""
    if not _MARKETPLACE_SEARCH:
        return {"success": False,
                "detail": "marketplace search not available (requires gateway)"}
    try:
        return _MARKETPLACE_SEARCH(query, source, limit)
    except Exception as e:
        return {"success": False, "detail": f"search failed: {e}"}


def list_marketplaces() -> dict:
    """Static catalog of the online marketplaces the gateway backs. The frontend
    SkillsPanel uses this for the marketplace list / install-source picker."""
    return {"marketplaces": [
        {"name": "builtin", "url": "", "install_location": "local",
         "last_updated": None},
        {"name": "skillhub", "url": "https://www.skillhub.cn",
         "install_location": "local", "last_updated": None},
        {"name": "skillnet", "url": "https://github.com",
         "install_location": "local", "last_updated": None},
        {"name": "clawhub", "url": "https://clawhub.ai",
         "install_location": "local", "last_updated": None},
    ]}
