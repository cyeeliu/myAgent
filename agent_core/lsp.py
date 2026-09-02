"""agent_core.lsp — LSP code intelligence client.

A lightweight JSON-RPC 2.0 client that talks to an LSP server (pyright,
typescript-language-server, etc.) over stdio.  Provides diagnostics,
go-to-definition, find-references, and hover for the agent's tools.

The client is lazy — it only spawns the server when first queried and
caches the process handle.  If no server binary is found, all queries
return a helpful "LSP server not available" message instead of crashing.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from agent_core.env import workspace_dir

# ── Language server config ──
_SERVER_CONFIG = {
    ".py": {
        "cmd": ["pyright-langserver", "--stdio"],
        "init_options": {},
    },
    ".ts": {
        "cmd": ["typescript-language-server", "--stdio"],
        "init_options": {},
    },
    ".tsx": {
        "cmd": ["typescript-language-server", "--stdio"],
        "init_options": {},
    },
    ".js": {
        "cmd": ["typescript-language-server", "--stdio"],
        "init_options": {},
    },
    ".jsx": {
        "cmd": ["typescript-language-server", "--stdio"],
        "init_options": {},
    },
}


class LSPClient:
    """Manages a single LSP server process per language."""

    def __init__(self):
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()
        self._next_id = 1
        self._initialized: set[str] = set()
        self._diagnostics: dict[str, list[dict]] = {}  # uri → diagnostics

    def _get_server_key(self, file_path: Path) -> str | None:
        ext = file_path.suffix
        return ext if ext in _SERVER_CONFIG else None

    def _ensure_server(self, file_path: Path) -> subprocess.Popen | None:
        key = self._get_server_key(file_path)
        if key is None:
            return None

        with self._lock:
            if key in self._processes:
                proc = self._processes[key]
                if proc.poll() is not None:
                    del self._processes[key]
                    key_init = key
                    if key_init in self._initialized:
                        self._initialized.discard(key_init)
                else:
                    return proc

            config = _SERVER_CONFIG.get(key)
            if config is None:
                return None

            # Check if the binary exists
            binary = config["cmd"][0]
            try:
                subprocess.run(
                    ["which", binary], capture_output=True, check=True, timeout=2
                )
            except Exception:
                return None

            try:
                proc = subprocess.Popen(
                    config["cmd"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(workspace_dir()),
                )
                self._processes[key] = proc
                self._initialize(key, proc, file_path)
                return proc
            except Exception:
                return None

    def _send(self, proc: subprocess.Popen, method: str, params: Any = None) -> dict | None:
        msg = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params or {},
        }
        self._next_id += 1
        content = json.dumps(msg)
        header = f"Content-Length: {len(content)}\r\n\r\n"
        try:
            proc.stdin.write(header.encode() + content.encode())
            proc.stdin.flush()
            return self._read_response(proc)
        except Exception:
            return None

    def _send_notification(self, proc: subprocess.Popen, method: str, params: Any = None):
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        content = json.dumps(msg)
        header = f"Content-Length: {len(content)}\r\n\r\n"
        try:
            proc.stdin.write(header.encode() + content.encode())
            proc.stdin.flush()
        except Exception:
            pass

    def _read_response(self, proc: subprocess.Popen, timeout: float = 5.0) -> dict | None:
        """Read one JSON-RPC message (header + body). Returns None on timeout/error."""
        import select
        try:
            # Read headers
            headers = {}
            while True:
                line = proc.stdout.readline()
                if not line:
                    return None
                line = line.strip()
                if line == b"":
                    break
                if b":" in line:
                    k, v = line.split(b":", 1)
                    headers[k.strip().lower()] = v.strip()

            length = int(headers.get(b"content-length", b"0"))
            if length == 0:
                return None

            body = proc.stdout.read(length)
            msg = json.loads(body)

            # If it's a notification (no id), skip and read next
            if "id" not in msg:
                # Could be a diagnostic notification
                if msg.get("method") == "textDocument/publishDiagnostics":
                    params = msg.get("params", {})
                    uri = params.get("uri", "")
                    self._diagnostics[uri] = params.get("diagnostics", [])
                return self._read_response(proc, timeout)

            return msg
        except Exception:
            return None

    def _initialize(self, key: str, proc: subprocess.Popen, file_path: Path):
        if key in self._initialized:
            return
        root = str(workspace_dir())
        resp = self._send(proc, "initialize", {
            "processId": os.getpid(),
            "rootUri": Path(root).as_uri(),
            "capabilities": {
                "textDocument": {
                    "diagnostics": {"dynamicRegistration": True},
                    "definition": {},
                    "references": {},
                    "hover": {},
                    "synchronization": {
                        "didOpen": True,
                        "didChange": True,
                        "didClose": True,
                    },
                },
            },
            "initializationOptions": _SERVER_CONFIG[key].get("init_options", {}),
        })
        if resp is not None:
            self._send_notification(proc, "initialized", {})
            self._initialized.add(key)

    def _uri(self, path: Path) -> str:
        return path.resolve().as_uri()

    def _open_doc(self, proc: subprocess.Popen, path: Path):
        try:
            text = path.read_text()
        except Exception:
            text = ""
        self._send_notification(proc, "textDocument/didOpen", {
            "textDocument": {
                "uri": self._uri(path),
                "languageId": path.suffix.lstrip("."),
                "version": 1,
                "text": text,
            }
        })

    def diagnostics(self, file_path: str) -> str:
        """Get diagnostics (errors/warnings) for a file."""
        fp = Path(file_path)
        if not fp.exists():
            return f"Error: file not found: {file_path}"
        proc = self._ensure_server(fp)
        if proc is None:
            return f"LSP server not available for {fp.suffix} files. Install pyright-langserver (Python) or typescript-language-server (JS/TS)."
        self._open_doc(proc, fp)
        # Give the server a moment to publish diagnostics
        time.sleep(0.3)
        # Try to read any pending notifications
        try:
            import select
            while select.select([proc.stdout], [], [], 0.1)[0]:
                self._read_response(proc, timeout=0.5)
        except Exception:
            pass
        diags = self._diagnostics.get(self._uri(fp), [])
        if not diags:
            return f"No diagnostics for {file_path}"
        lines = []
        for d in diags:
            sev = {1: "ERROR", 2: "WARN", 3: "INFO", 4: "HINT"}.get(d.get("severity", 1), "?")
            rng = d.get("range", {})
            start = rng.get("start", {})
            line = start.get("line", 0) + 1
            col = start.get("character", 0) + 1
            msg = d.get("message", "")
            lines.append(f"  {sev} L{line}:{col} {msg}")
        return "\n".join(lines)

    def goto_definition(self, file_path: str, line: int, character: int) -> str:
        """Go to definition at position (1-indexed line, 1-indexed character)."""
        fp = Path(file_path)
        if not fp.exists():
            return f"Error: file not found: {file_path}"
        proc = self._ensure_server(fp)
        if proc is None:
            return f"LSP server not available for {fp.suffix} files."
        self._open_doc(proc, fp)
        resp = self._send(proc, "textDocument/definition", {
            "textDocument": {"uri": self._uri(fp)},
            "position": {"line": line - 1, "character": character - 1},
        })
        if resp is None:
            return "No definition found (timeout or error)"
        result = resp.get("result")
        if not result:
            return "No definition found"
        if isinstance(result, list):
            locs = result
        else:
            locs = [result]
        lines = []
        for loc in locs:
            uri = loc.get("uri", "")
            rng = loc.get("range", {}).get("start", {})
            l = rng.get("line", 0) + 1
            c = rng.get("character", 0) + 1
            path_str = uri.replace("file://", "")
            lines.append(f"  {path_str}:{l}:{c}")
        return "\n".join(lines) if lines else "No definition found"

    def find_references(self, file_path: str, line: int, character: int) -> str:
        """Find all references to the symbol at position."""
        fp = Path(file_path)
        if not fp.exists():
            return f"Error: file not found: {file_path}"
        proc = self._ensure_server(fp)
        if proc is None:
            return f"LSP server not available for {fp.suffix} files."
        self._open_doc(proc, fp)
        resp = self._send(proc, "textDocument/references", {
            "textDocument": {"uri": self._uri(fp)},
            "position": {"line": line - 1, "character": character - 1},
            "context": {"includeDeclaration": True},
        })
        if resp is None:
            return "No references found (timeout or error)"
        result = resp.get("result")
        if not result:
            return "No references found"
        lines = []
        for loc in result:
            uri = loc.get("uri", "")
            rng = loc.get("range", {}).get("start", {})
            l = rng.get("line", 0) + 1
            c = rng.get("character", 0) + 1
            path_str = uri.replace("file://", "")
            lines.append(f"  {path_str}:{l}:{c}")
        return f"Found {len(lines)} reference(s):\n" + "\n".join(lines)

    def hover(self, file_path: str, line: int, character: int) -> str:
        """Get hover info (type/docstring) at position."""
        fp = Path(file_path)
        if not fp.exists():
            return f"Error: file not found: {file_path}"
        proc = self._ensure_server(fp)
        if proc is None:
            return f"LSP server not available for {fp.suffix} files."
        self._open_doc(proc, fp)
        resp = self._send(proc, "textDocument/hover", {
            "textDocument": {"uri": self._uri(fp)},
            "position": {"line": line - 1, "character": character - 1},
        })
        if resp is None:
            return "No hover info (timeout or error)"
        result = resp.get("result")
        if not result:
            return "No hover info at this position"
        contents = result.get("contents", "")
        if isinstance(contents, dict):
            # MarkupContent
            return contents.get("value", "")
        if isinstance(contents, list):
            return "\n".join(
                c.get("value", str(c)) if isinstance(c, dict) else str(c)
                for c in contents
            )
        return str(contents)

    def shutdown(self):
        for key, proc in self._processes.items():
            try:
                self._send(proc, "shutdown", {})
                self._send_notification(proc, "exit")
                proc.terminate()
            except Exception:
                pass
        self._processes.clear()
        self._initialized.clear()


# Singleton
client = LSPClient()
