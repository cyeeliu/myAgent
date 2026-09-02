"""agent_core.checkpoint — file-state snapshot / undo / restore.

Provides per-session undo history for write operations.  Before each write
tool modifies a file, ``before_write(path)`` snapshots the current content.
``undo(n)`` rolls back the last *n* write steps by restoring file contents.
``checkpoint(label)`` creates an explicit named snapshot; ``restore(id)``
jumps back to it.

Persistence: JSON file at ``session_dir()/.checkpoints/history.json`` so
undo survives across turns within a session.  The stack is bounded (max 50
entries) to avoid unbounded growth.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from agent_core.env import session_dir

_MAX_STACK = 50


class FileCheckpoint:
    """A snapshot of file states at a point in time."""

    __slots__ = ("id", "timestamp", "label", "files")

    def __init__(self, label: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.timestamp = time.time()
        self.label = label
        self.files: dict[str, str] = {}  # path → content_before

    def to_dict(self) -> dict:
        return {"id": self.id, "timestamp": self.timestamp,
                "label": self.label, "files": self.files}

    @classmethod
    def from_dict(cls, d: dict) -> "FileCheckpoint":
        cp = cls(d.get("label", ""))
        cp.id = d.get("id", cp.id)
        cp.timestamp = d.get("timestamp", cp.timestamp)
        cp.files = d.get("files", {})
        return cp


class CheckpointManager:
    """Manages undo history for a session."""

    def __init__(self):
        self._stack: list[FileCheckpoint] = []
        self._current: FileCheckpoint | None = None
        self._named: dict[str, FileCheckpoint] = {}
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            p = self._persist_path()
            if p.exists():
                data = json.loads(p.read_text())
                self._stack = [FileCheckpoint.from_dict(d) for d in data.get("stack", [])]
                self._named = {k: FileCheckpoint.from_dict(v) for k, v in data.get("named", {}).items()}
        except Exception:
            pass

    def _persist_path(self) -> Path:
        d = session_dir()
        cp_dir = Path(d) / ".checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        return cp_dir / "history.json"

    def _persist(self):
        try:
            data = {
                "stack": [cp.to_dict() for cp in self._stack],
                "named": {k: v.to_dict() for k, v in self._named.items()},
            }
            self._persist_path().write_text(json.dumps(data))
        except Exception:
            pass

    def before_write(self, path: str) -> None:
        """Called before a write tool modifies a file.  Snapshots current
        content so the operation can be undone."""
        self._ensure_loaded()
        if self._current is None:
            self._current = FileCheckpoint()
        if path not in self._current.files:
            try:
                p = Path(path)
                if p.exists():
                    self._current.files[path] = p.read_text()
                else:
                    self._current.files[path] = None  # file didn't exist
            except Exception:
                pass

    def after_write(self) -> None:
        """Called after a write tool completes.  Pushes the current
        checkpoint onto the stack."""
        self._ensure_loaded()
        if self._current is not None and self._current.files:
            self._stack.append(self._current)
            if len(self._stack) > _MAX_STACK:
                self._stack = self._stack[-_MAX_STACK:]
            self._persist()
        self._current = None

    def undo(self, n: int = 1) -> str:
        """Roll back the last *n* write operations."""
        self._ensure_loaded()
        if not self._stack:
            return "Nothing to undo (checkpoint stack is empty)"
        n = min(n, len(self._stack))
        restored: list[str] = []
        for _ in range(n):
            cp = self._stack.pop()
            for path, content in cp.files.items():
                try:
                    p = Path(path)
                    if content is None:
                        if p.exists():
                            p.unlink()
                    else:
                        p.write_text(content)
                    if path not in restored:
                        restored.append(path)
                except Exception:
                    pass
        self._persist()
        return f"Undid {n} step(s). Restored {len(restored)} file(s): {', '.join(restored)}"

    def checkpoint(self, label: str = "") -> str:
        """Create a named checkpoint marker at the current stack position.
        Use restore(id) to roll back to this point (undoes all writes since)."""
        self._ensure_loaded()
        cp = FileCheckpoint(label)
        cp.files = {}  # marker — no file snapshots, just a stack position
        self._named[cp.id] = cp
        # Push a marker onto the stack so restore can find its position
        self._stack.append(cp)
        if len(self._stack) > _MAX_STACK:
            self._stack = self._stack[-_MAX_STACK:]
        self._persist()
        return f"Checkpoint '{label or cp.id}' created (id={cp.id})"

    def restore(self, checkpoint_id: str = "") -> str:
        """Restore to a named checkpoint by undoing all writes since it.
        With no ID, lists available checkpoints."""
        self._ensure_loaded()
        if not checkpoint_id:
            return self.list_checkpoints()
        cp = self._named.get(checkpoint_id)
        if cp is None:
            return f"Error: no checkpoint with id={checkpoint_id}"
        # Find the marker in the stack and undo everything above it
        marker_idx = None
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i].id == cp.id:
                marker_idx = i
                break
        if marker_idx is None:
            return f"Error: checkpoint '{cp.label or cp.id}' has been undone already"
        # Count entries to undo (everything above the marker, excluding marker itself)
        n_to_undo = len(self._stack) - 1 - marker_idx
        if n_to_undo == 0:
            return f"Already at checkpoint '{cp.label or cp.id}' — nothing to restore"
        return self.undo(n_to_undo)

    def list_checkpoints(self) -> str:
        """List all named checkpoints."""
        self._ensure_loaded()
        if not self._named:
            return "No named checkpoints. Use the checkpoint tool to create one."
        lines = []
        for cp_id, cp in self._named.items():
            label = cp.label or "(unnamed)"
            n_files = len(cp.files)
            lines.append(f"  {cp_id}  {label}  ({n_files} files)")
        return "\n".join(lines)

    def clear(self) -> None:
        """Clear all checkpoint history (used on session reset)."""
        self._stack.clear()
        self._named.clear()
        self._current = None
        try:
            p = self._persist_path()
            if p.exists():
                p.unlink()
        except Exception:
            pass


# Singleton — one checkpoint manager per process.  Session isolation is via
# session_dir() in the persist path.  In the gateway each session runs in
# its own worker thread with session_dir() bound, so the singleton is
# effectively per-session.
manager = CheckpointManager()
