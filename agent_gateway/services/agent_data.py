"""Agent data builder for the AgentPanel file browser.

Generates ``agent/workspace/agent-data.json`` (``Record<folder_key, FileInfo[]>``)
by walking the real mounted workspace. Also seeds per-workspace skills
(copy-if-missing from ``/app/skills/*``) and ``.memory/``.
"""
from __future__ import annotations

import json
import shutil

from agent_core import REPO_ROOT


def rebuild_agent_data() -> None:
    """Walk the workspace and write ``agent-data.json`` for the AgentPanel.

    Seeds per-workspace skills (copy-if-missing from presets) and ``.memory/``
    along the way. Best-effort — failures are caught by the caller.
    """
    ws_root = (REPO_ROOT / "workspace").resolve()
    ws_root.mkdir(parents=True, exist_ok=True)

    # ── per-workspace skills: seed from presets (/app/skills/*) if missing ──
    skills_dst = ws_root / "skills"
    skills_dst.mkdir(parents=True, exist_ok=True)
    skills_src = REPO_ROOT / "skills"
    if skills_src.is_dir():
        for d in sorted(skills_src.iterdir()):
            if not d.is_dir():
                continue
            target = skills_dst / d.name
            if not target.exists():
                shutil.copytree(d, target)

    # ── memory: .memory/ holds config json ──
    mem_dir = ws_root / ".memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    mem_cfg = mem_dir / "config.json"
    if not mem_cfg.exists():
        mem_cfg.write_text(json.dumps({
            "enabled": True,
            "types": ["user", "feedback", "project", "reference"],
            "consolidate_threshold": 10,
            "index_file": "MEMORY.md",
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── walk workspace → Record<folder_key, FileInfo[]> ──
    # Skip hidden dirs/files except .memory. Other dot-dirs stay hidden.
    folder_data: dict[str, list[dict]] = {}
    for entry in sorted(ws_root.rglob("*")):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        parts = entry.relative_to(ws_root).parts
        if any(p.startswith(".") and p != ".memory" for p in parts):
            continue
        if entry.name == "agent-data.json":
            continue  # avoid self-reference
        rel = entry.relative_to(ws_root).as_posix()
        rel_parent = entry.parent.relative_to(ws_root).as_posix()
        folder_key = "workspace" if rel_parent == "." else f"workspace/{rel_parent}"
        display_path = f"agent/workspace/{rel}"
        folder_data.setdefault(folder_key, []).append({
            "name": entry.name,
            "path": display_path,
            "isMarkdown": entry.suffix.lower() in {".md", ".mdx"},
        })
    sorted_folder_data = {
        k: sorted(v, key=lambda item: item["path"])
        for k, v in sorted(folder_data.items(), key=lambda item: item[0])
    }
    (ws_root / "agent-data.json").write_text(
        json.dumps(sorted_folder_data, ensure_ascii=False, indent=2),
        encoding="utf-8")
