"""agent_core.memory — extracted from code.py (s20 comprehensive agent).

Storage architecture:
  - **Permanent storage**: `~/.myAgent/workspace/.memory/*.md` files (YAML
    frontmatter + markdown body) + `MEMORY.md` index. The durable source of
    truth; survives restarts, editable by hand, seeded by the gateway.
  - **Real-time cache**: a module-level in-memory cache (`_MEM_CACHE`) of the
    parsed files + index, invalidated by mtime. Every turn, `load_memories`
    reads from the cache (O(1) after first load) instead of re-reading and
    re-parsing every md file from disk. Writes (`write_memory_file`,
    `consolidate_memories`) invalidate the cache; the next read reloads from
    disk. The cache is per-process — lost on restart, rebuilt from the md
    files on first access (which is the whole point: md is permanent, the
    cache is the hot layer).
"""
import json
import os
import re
import time
from agent_core import adapter
from agent_core.blocks import extract_text
from agent_core.env import FALLBACK_MODEL, MODEL, workspace_dir


_MEM_DBG = os.environ.get("AGENT_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")

MEMORY_TYPES = ["user", "feedback", "project", "reference"]

CONSOLIDATE_THRESHOLD = 10

def _memory_dir():
    return workspace_dir() / ".memory"

def _memory_index():
    return _memory_dir() / "MEMORY.md"

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, parts[2].strip()

# ── Real-time cache layer ──────────────────────────────────────────────
# _MEM_CACHE holds the parsed memory files + index text + a snapshot of file
# mtimes. It is refreshed from disk only when an mtime changes (or on first
# access). This makes load_memories O(1) per turn regardless of file count.
_MEM_CACHE: dict = {
    "files": [],        # list[dict] — parsed {filename, name, description, type, body, raw}
    "by_name": {},      # dict[filename -> raw text] — for read_memory_file
    "index": "",        # str — MEMORY.md content
    "mtimes": {},       # dict[filename -> mtime_ns] — invalidation key
    "dir_mtime": None,  # entry-count check via glob is cheap; this is a fallback
}


def _invalidate_cache():
    """Drop the cache so the next read reloads from disk."""
    _MEM_CACHE["mtimes"] = {}
    _MEM_CACHE["files"] = []
    _MEM_CACHE["by_name"] = {}
    _MEM_CACHE["index"] = ""


def _refresh_cache_if_stale() -> None:
    """Reload from disk if any md file's mtime changed or the cache is empty.
    Cheap when fresh: one glob + stat per file, no file reads."""
    mdir = _memory_dir()
    if not mdir.exists():
        _invalidate_cache()
        return
    # Snapshot current mtimes (MEMORY.md + every *.md except MEMORY.md).
    files = sorted(mdir.glob("*.md"))
    cur_mtimes = {}
    for f in files:
        try:
            cur_mtimes[f.name] = f.stat().st_mtime_ns
        except OSError:
            continue
    if cur_mtimes == _MEM_CACHE["mtimes"]:
        if _MEM_DBG:
            print(f"[MEMCACHE] hit files={len(_MEM_CACHE['files'])}", flush=True)
        return  # cache is fresh — no disk reads
    if _MEM_DBG:
        print(f"[MEMCACHE] miss/reload files={len(files)} (was {len(_MEM_CACHE['files'])})",
              flush=True)
    # Stale or empty: reload everything.
    parsed = []
    by_name = {}
    index_lines = []
    for f in files:
        if f.name == "MEMORY.md":
            try:
                _MEM_CACHE["index"] = f.read_text().strip()
            except OSError:
                _MEM_CACHE["index"] = ""
            continue
        try:
            raw = f.read_text()
        except OSError:
            continue
        meta, body = _parse_frontmatter(raw)
        name = meta.get("name", f.stem)
        desc = meta.get("description", body.split("\n")[0][:80])
        parsed.append({
            "filename": f.name,
            "name": name,
            "description": desc,
            "type": meta.get("type", "user"),
            "body": body,
        })
        by_name[f.name] = raw
        index_lines.append(f"- [{name}]({f.name}) — {desc}")
    _MEM_CACHE["files"] = parsed
    _MEM_CACHE["by_name"] = by_name
    _MEM_CACHE["index"] = "\n".join(index_lines)
    _MEM_CACHE["mtimes"] = cur_mtimes


def write_memory_file(name: str, mem_type: str, description: str, body: str):
    """Write one memory file with YAML frontmatter, then rebuild the index."""
    _memory_dir().mkdir(parents=True, exist_ok=True)
    slug = name.lower().replace(" ", "-").replace("/", "-")
    filepath = _memory_dir() / f"{slug}.md"
    filepath.write_text(
        f"---\nname: {name}\ndescription: {description}\ntype: {mem_type}\n---\n\n{body}\n"
    )
    _rebuild_index()
    _invalidate_cache()
    return filepath

def _rebuild_index():
    """Rebuild MEMORY.md (one line per memory) from all memory files."""
    lines = []
    if not _memory_dir().exists():
        _memory_index().write_text("") if _memory_index().exists() else None
        return
    for f in sorted(_memory_dir().glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        raw = f.read_text()
        meta, body = _parse_frontmatter(raw)
        name = meta.get("name", f.stem)
        desc = meta.get("description", body.split("\n")[0][:80])
        lines.append(f"- [{name}]({f.name}) — {desc}")
    _memory_index().write_text("\n".join(lines) + "\n" if lines else "")

def read_memory_index() -> str:
    _refresh_cache_if_stale()
    return _MEM_CACHE["index"]

def read_memory_file(filename: str) -> str | None:
    _refresh_cache_if_stale()
    return _MEM_CACHE["by_name"].get(filename)

def list_memory_files() -> list[dict]:
    """All memory files with parsed frontmatter + body (from cache)."""
    _refresh_cache_if_stale()
    return _MEM_CACHE["files"]

def _block_text(block) -> str:
    """Extract text from a content block that may be a dict, SimpleNamespace,
    or a plain string (agent messages are heterogeneous across the pipeline)."""
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        return block.get("text", "") if block.get("type") == "text" else ""
    return getattr(block, "text", "") if getattr(block, "type", None) == "text" else ""

def _msg_text(msg) -> str:
    c = msg.get("content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(_block_text(b) for b in c)
    return str(c)

def _memory_llm(prompt: str, max_tokens: int = 800, timeout: int = 8) -> str:
    """One-shot non-streaming text call for memory selection/extraction/consolidation.
    Uses the primary model, falling back to FALLBACK_MODEL on error. Never raises.
    Hard `timeout` (default 8s) so a slow reasoning model can't stall the turn
    — memory is best-effort, never worth a 20s block."""
    for model in (MODEL, FALLBACK_MODEL):
        if not model:
            continue
        try:
            resp = adapter.chat_create(model=model,
                               messages=[{"role": "user", "content": prompt}],
                               max_tokens=max_tokens, timeout=timeout)
            return extract_text(resp.content)
        except Exception:
            continue
    return ""

# Below this catalog size, skip the LLM selection round-trip and inject every
# memory file. The selection call is only worthwhile once the catalog is large
# enough that injecting everything would bloat the system prompt. The previous
# threshold (5) made a 7-file catalog pay a ~20s glm-5 call every turn — the
# slow-reply bottleneck. 12 keeps small workspaces instant with negligible
# prompt bloat (memories are short .md files).
_MEMORY_LLM_SELECTION_THRESHOLD = 12

def select_relevant_memories(messages: list, max_items: int = 5) -> list[str]:
    """Pick memory filenames relevant to the recent dialogue (LLM, with a
    keyword fallback). Returns filenames to inject."""
    files = list_memory_files()
    if not files:
        return []

    # Drop memories matching the forbidden regex (security panel config) so
    # they never enter the catalog / injection. Best-effort, never raises.
    try:
        from agent_core import permissions
        if files:
            files = [f for f in files
                     if not permissions.matches_forbidden(
                         f["name"] + " " + f["description"] + " "
                         + f.get("body", ""))]
    except Exception:  # noqa: BLE001
        pass
    if not files:
        return []

    recent_texts = []
    for msg in reversed(messages):
        if msg.get("role") == "user":
            t = _msg_text(msg)
            if t.strip():
                recent_texts.append(t)
            if len(recent_texts) >= 3:
                break
    recent = " ".join(reversed(recent_texts))[:2000]
    if not recent.strip():
        return []

    catalog = "\n".join(f"{i}: {f['name']} — {f['description']}"
                        for i, f in enumerate(files))
    # With few memories, skip the LLM selection round-trip and just inject all.
    # The selection call is only worthwhile once the catalog is large enough
    # that injecting everything would bloat the system prompt.
    if len(files) <= _MEMORY_LLM_SELECTION_THRESHOLD:
        return [f["filename"] for f in files]
    out = _memory_llm(
        "Given the recent conversation and the memory catalog below, "
        "select the indices of memories that are clearly relevant. "
        "Return ONLY a JSON array of integers, e.g. [0, 3]. "
        "If none are relevant, return [].\n\n"
        f"Recent conversation:\n{recent}\n\nMemory catalog:\n{catalog}",
        max_tokens=200)
    match = re.search(r'\[.*?\]', out, re.DOTALL)
    if match:
        try:
            indices = json.loads(match.group())
            selected = []
            for idx in indices:
                if isinstance(idx, int) and 0 <= idx < len(files):
                    selected.append(files[idx]["filename"])
                    if len(selected) >= max_items:
                        break
            return selected
        except Exception:
            pass

    # Keyword fallback on name + description.
    keywords = [w.lower() for w in recent.split() if len(w) > 3]
    selected = []
    for f in files:
        text = (f["name"] + " " + f["description"]).lower()
        if any(kw in text for kw in keywords):
            selected.append(f["filename"])
            if len(selected) >= max_items:
                break
    return selected

def load_memories(messages: list) -> str:
    """Content of relevant memories, wrapped for injection into the turn."""
    selected = select_relevant_memories(messages)
    if not selected:
        return ""
    parts = ["<relevant_memories>"]
    for filename in selected:
        content = read_memory_file(filename)
        if content:
            parts.append(content)
    parts.append("</relevant_memories>")
    return "\n\n".join(parts)

def extract_memories(messages: list) -> int:
    """After a turn, pull new memories out of the dialogue and write them.
    Returns the number written. Never raises."""
    dialogue_parts = []
    for msg in messages[-10:]:
        role = msg.get("role", "?")
        t = _msg_text(msg)
        if t.strip():
            dialogue_parts.append(f"{role}: {t}")
    dialogue = "\n".join(dialogue_parts)
    if not dialogue.strip():
        return 0

    existing = list_memory_files()
    existing_desc = "\n".join(f"- {m['name']}: {m['description']}" for m in existing) \
        if existing else "(none)"

    out = _memory_llm(
        "Extract user preferences, constraints, or project facts from this dialogue.\n"
        "Return a JSON array. Each item: {name, type, description, body}.\n"
        "- name: short kebab-case identifier (e.g. 'user-preference-tabs')\n"
        "- type: one of 'user' (user preference), 'feedback' (guidance), "
        "'project' (project fact), 'reference' (external pointer)\n"
        "- description: one-line summary for index lookup\n"
        "- body: full detail in markdown\n"
        "If nothing new or already covered by existing memories, return [].\n\n"
        f"Existing memories:\n{existing_desc}\n\nDialogue:\n{dialogue[:4000]}",
        max_tokens=800)
    match = re.search(r'\[.*\]', out, re.DOTALL)
    if not match:
        return 0
    try:
        items = json.loads(match.group())
    except Exception:
        return 0
    if not items:
        return 0

    count = 0
    for mem in items:
        name = mem.get("name", f"memory_{int(time.time())}")
        mem_type = mem.get("type", "user")
        if mem_type not in MEMORY_TYPES:
            mem_type = "user"
        desc = mem.get("description", "")
        body = mem.get("body", "")
        if not (desc and body):
            continue
        # Skip memories matching the forbidden regex (security panel config)
        # so sensitive content is never persisted. Best-effort, never raises.
        try:
            from agent_core import permissions
            if permissions.matches_forbidden(name + " " + desc + " " + body):
                continue
        except Exception:  # noqa: BLE001
            pass
        try:
            write_memory_file(name, mem_type, desc, body)
            count += 1
        except Exception:
            pass
    return count

def consolidate_memories() -> None:
    """When the file count grows past the threshold, merge/dedupe via LLM."""
    files = list_memory_files()
    if len(files) < CONSOLIDATE_THRESHOLD:
        return

    catalog = "\n\n".join(
        f"## {f['filename']}\nname: {f['name']}\ndescription: {f['description']}\n{f['body']}"
        for f in files)
    out = _memory_llm(
        "Consolidate the following memory files. Rules:\n"
        "1. Merge duplicates into one\n"
        "2. Remove outdated/contradicted memories\n"
        "3. Keep the total under 30 memories\n"
        "4. Preserve important user preferences above all\n"
        "Return a JSON array. Each item: {name, type, description, body}.\n\n"
        f"{catalog[:16000]}",
        max_tokens=3000)
    match = re.search(r'\[.*\]', out, re.DOTALL)
    if not match:
        return
    try:
        items = json.loads(match.group())
    except Exception:
        return

    for f in _memory_dir().glob("*.md"):
        if f.name != "MEMORY.md":
            try:
                f.unlink()
            except Exception:
                pass
    _invalidate_cache()
    for mem in items:
        name = mem.get("name", f"memory_{int(time.time())}")
        mem_type = mem.get("type", "user")
        if mem_type not in MEMORY_TYPES:
            mem_type = "user"
        desc = mem.get("description", "")
        body = mem.get("body", "")
        if desc and body:
            try:
                write_memory_file(name, mem_type, desc, body)
            except Exception:
                pass
