"""agent_core.tools — extracted from code.py (s20 comprehensive agent)."""
from pathlib import Path
import ast
import json
import subprocess
import urllib.request
import urllib.error
import urllib.parse
import re
import socket
from agent_core.bus import BUS, consume_lead_inbox
from agent_core.cron import run_cancel_cron, run_list_crons, run_schedule_cron
from agent_core.env import workdir
# connect_mcp imported lazily inside run_connect_mcp to avoid a tools<->mcp
# circular import (mcp.assemble_tool_pool imports BUILTIN_* from tools).
from agent_core.skills import load_skill
from agent_core.subagent import spawn_subagent
from agent_core.tasks import claim_task, complete_task, create_task, get_task_json, list_tasks, set_todos
from agent_core.teammates import run_request_plan, run_request_shutdown, run_review_plan, spawn_teammate_thread
from agent_core.worktrees import create_worktree, keep_worktree, remove_worktree


def safe_path(p: str, cwd: Path = None) -> Path:
    # File tools stay inside the workspace or teammate worktree. Bash remains
    # powerful on purpose and is controlled by the permission hook instead.
    base = cwd or workdir()
    path = (base / p).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"Path escapes workspace: {p}")
    return path

def run_bash(command: str, cwd: Path = None,
             run_in_background: bool = False) -> str:
    # run_in_background is consumed by the dispatcher (background.py starts the
    # command detached via Popen with no timeout); this path only runs foreground
    # commands, which get a 120s cap. Long-running work must use run_in_background.
    from agent_core import sandbox
    base = Path(cwd) if cwd else workdir()
    try:
        if sandbox.enabled(base):
            # bwrap fails closed: a namespace/cap error makes bwrap exit
            # non-zero with a stderr message rather than running unsandboxed.
            r = subprocess.run(sandbox.build_argv(base, command),
                               capture_output=True, text=True, timeout=120)
        else:
            r = subprocess.run(command, shell=True, cwd=base,
                               capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return ("Error: Timeout (120s). The command ran longer than 120s. "
                "Re-run with run_in_background=true to let it continue detached, "
                "then read its output with the task_output tool.")
    except FileNotFoundError as e:
        # bwrap on PATH at enabled()-check time but gone at run time.
        return f"Error: sandbox binary not found: {e}"


def run_task_output(task_id: str, timeout: int = 0) -> str:
    """Read output from a background task. If timeout > 0, block up to that many
    seconds for the task to finish (or more output to accumulate)."""
    from agent_core.background import read_task_output
    return read_task_output(task_id, float(timeout or 0))


def run_task_stop(task_id: str) -> str:
    """Kill a running background task (SIGTERM then SIGKILL on its process group)."""
    from agent_core.background import stop_task
    return stop_task(task_id)


def run_task_list() -> str:
    """List background tasks and their status."""
    from agent_core.background import list_tasks as _list_tasks
    return _list_tasks()

def _decode_bytes(content: bytes) -> str:
    """Decode bytes with a CJK-aware fallback chain (matches the gateway
    file-api _decode_auto): utf-8 → gbk → gb2312 → big5 → shift_jis → euc_kr
    → latin-1. Never raises — the final latin-1 replace is a guaranteed win."""
    for enc in ("utf-8", "gbk", "gb2312", "big5", "shift_jis", "euc_kr"):
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("latin-1", errors="replace")


def _is_binary(content: bytes, peek: int = 2048) -> bool:
    """NUL-byte / high-control ratio sniff on the first `peek` bytes."""
    sample = content[:peek]
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    text = sample.decode("latin-1", errors="replace")
    control = sum(1 for c in text if ord(c) < 32 and c not in "\n\r\t")
    return control / len(text) > 0.10


def run_read(path: str, limit: int | None = None,
             offset: int = 0, cwd: Path = None) -> str:
    try:
        fp = safe_path(path, cwd)
        raw = fp.read_bytes()
        if _is_binary(raw):
            return (f"Error: {path} appears to be a binary file "
                    f"({len(raw)} bytes). read_file is text-only.")
        text = _decode_bytes(raw)
        lines = text.splitlines()
        offset = max(int(offset or 0), 0)
        limit = int(limit) if limit is not None else None
        lines = lines[offset:]
        total = len(lines)
        if limit is not None and limit < total:
            lines = lines[:limit]
        # cat -n style line numbers (1-based, tab-aligned) — matches Claude Code
        # Read output so file:line references are clickable in the UI.
        numbered = "\n".join(f"{i + offset + 1:6}\t{ln}" for i, ln in enumerate(lines))
        if limit is not None and limit < total:
            numbered += f"\n... ({total - limit} more lines)"
        return numbered
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str, cwd: Path = None) -> str:
    try:
        fp = safe_path(path, cwd)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str,
             cwd: Path = None, replace_all: bool = False) -> str:
    try:
        fp = safe_path(path, cwd)
        text = fp.read_text()
        if old_text not in text:
            return f"Error: text not found in {path}"
        count = text.count(old_text)
        if replace_all:
            fp.write_text(text.replace(old_text, new_text))
            return f"Edited {path} (replaced {count} occurrences)"
        else:
            # Ambiguity guard: a non-unique old_text without replace_all is
            # almost always a mistake (the model picked a too-short anchor).
            # Refuse and report the count so the model can lengthen the anchor
            # or set replace_all — matches Claude Code Edit semantics.
            if count > 1:
                return (f"Error: old_text matches {count} places in {path}. "
                        "Make it unique, or set replace_all=true to replace all.")
            fp.write_text(text.replace(old_text, new_text, 1))
            return f"Edited {path} (replaced 1 occurrence)"
    except Exception as e:
        return f"Error: {e}"


def _html_to_text(html: str) -> str:
    """Crude but useful HTML→markdown-ish text: drop script/style/nav, keep
    headings/links/lists/code with light markup so the model gets structure
    instead of a tag-stripped word soup. Pure-regex (no deps) — good enough
    for fetch-and-answer; not a full markdown converter."""
    # Drop non-content blocks entirely.
    for tag in ("script", "style", "nav", "footer", "header", "svg", "noscript"):
        html = re.sub(rf'<{tag}[^>]*>.*?</{tag}>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Headings → markdown # lines.
    for n in range(6, 0, -1):
        html = re.sub(rf'<h{n}[^>]*>(.*?)</h{n}>', lambda m: '\n\n' + '#' * n + ' ' + re.sub(r'<[^>]+>', '', m.group(1)).strip() + '\n', html, flags=re.DOTALL | re.IGNORECASE)
    # li → "- " lines; p/br → newlines; a → "text (url)".
    html = re.sub(r'<li[^>]*>(.*?)</li>', lambda m: '- ' + re.sub(r'<[^>]+>', '', m.group(1)).strip() + '\n', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', lambda m: (re.sub(r'<[^>]+>', '', m.group(2)).strip() + f' ({m.group(1)})'), html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<(p|br|div|tr)[^>]*>', '\n', html, flags=re.IGNORECASE)
    # code/pre → fenced.
    html = re.sub(r'<pre[^>]*>(.*?)</pre>', lambda m: '\n```\n' + re.sub(r'<[^>]+>', '', m.group(1)) + '\n```\n', html, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining tags, decode entities, collapse whitespace per line.
    text = re.sub(r'<[^>]+>', '', html)
    text = (text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                .replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' '))
    lines = [re.sub(r'[ \t]+', ' ', ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def run_web_fetch(url: str, prompt: str, max_chars: int = 12000) -> str:
    """Fetch a URL (15s timeout, 5MB cap, follow redirects, upgrade http→https),
    convert HTML→markdown-ish text, return up to max_chars + the prompt note."""
    try:
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Claude-Code-Agent/1.0)"})
        with urllib.request.urlopen(req, timeout=15) as response:
            cap = 5 * 1024 * 1024
            content_length = response.getheader("Content-Length")
            if content_length and int(content_length) > cap:
                return f"Error: Content too large ({content_length} bytes > {cap} limit)"
            content = response.read(cap + 1)
            if len(content) > cap:
                return f"Error: Content exceeds {cap} limit"
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                html = content.decode(charset, errors="replace")
            except (UnicodeDecodeError, LookupError):
                html = _decode_bytes(content)
            ct = (response.headers.get("Content-Type") or "").lower()
            if "html" in ct or "<html" in html[:512].lower():
                text = _html_to_text(html)
            else:
                text = html  # already text (json, plain, markdown, csv, …)
            max_chars = max(1000, min(int(max_chars or 12000), 50000))
            if len(text) > max_chars:
                text = text[:max_chars] + "\n... (truncated)"
            return (f"Fetched {len(text)} chars from {url}. "
                    f"Prompt to apply: \"{prompt[:100]}{'...' if len(prompt) > 100 else ''}\". "
                    f"Content:\n\n{text}")
    except urllib.error.HTTPError as e:
        return f"Error: HTTP {e.code} {e.reason}"
    except urllib.error.URLError as e:
        return f"Error: URL error: {e.reason}"
    except socket.timeout:
        return "Error: Timeout (15s)"
    except Exception as e:
        return f"Error: {e}"


def run_web_search(query: str, max_results: int = 10) -> str:
    """DuckDuckGo Lite HTML search — no API key needed. Returns up to
    max_results results as `title\n  url\n  snippet` blocks. Falls back to a
    clear error string on network/parse failure so the agent can react."""
    try:
        max_results = max(1, min(int(max_results or 10), 25))
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({"q": query, "kl": "us-en"}).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Claude-Code-Agent/1.0)",
                     "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read(2 * 1024 * 1024).decode(
                resp.headers.get_content_charset() or "utf-8", errors="replace")
        # DDG lite lays results out in <a class="result-link" href="...">title</a>
        # followed by a <td class="result-snippet">snippet</td>. Pull anchors +
        # snippets in document order and zip them.
        links = re.findall(
            r'<a[^>]*class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html, flags=re.DOTALL)
        snippets = re.findall(
            r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
            html, flags=re.DOTALL)
        out = []
        for i, (href, title) in enumerate(links[:max_results]):
            # DDG wraps hrefs in a redirect like //duckduckgo.com/l/?uddg=<enc>.
            m = re.search(r"uddg=([^&]+)", href)
            real = urllib.parse.unquote(m.group(1)) if m else href
            title_text = re.sub(r"<[^>]+>", "", title).strip()
            snip = re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""
            if not title_text:
                continue
            out.append(f"{title_text}\n  {real}\n  {snip}")
        if not out:
            return f"(no results for: {query})"
        return "\n\n".join(out)
    except urllib.error.HTTPError as e:
        return f"Error: HTTP {e.code} {e.reason}"
    except Exception as e:
        return f"Error: {e}"


def run_glob(pattern: str, path: str = ".", cwd: Path = None) -> str:
    """Glob search, Claude-Code-Glob-style. Supports `**` recursive globs via
    pathlib; `path` scopes the search root (default workspace). Results are
    sorted (dirs-first) and capped at 200 to avoid flooding the context."""
    try:
        base = (cwd or workdir())
        scope = (base / path).resolve()
        if not scope.is_relative_to(base):
            return f"Error: path escapes workspace: {path}"
        if not scope.exists():
            return f"Error: no such directory: {path}"
        skip = {".git", "node_modules", ".venv", "__pycache__", ".next",
                ".pytest_cache", ".task_outputs", ".transcripts"}
        # Translate the glob to a recursive pathlib match. `**` works natively
        # in Path.glob; single-segment `*` stays non-recursive.
        matches = []
        for p in scope.glob(pattern):
            if any(part in skip for part in p.parts):
                continue
            if p.is_relative_to(base):
                matches.append(str(p.relative_to(base)))
        matches = sorted(set(matches))
        if len(matches) > 200:
            matches = matches[:200] + [f"... ({len(matches) - 200} more, truncated)"]
        return "\n".join(matches) if matches else "(no matches)"
    except Exception as e:
        return f"Error: {e}"


def run_list_dir(path: str = ".", cwd: Path = None) -> str:
    """Directory listing, Claude-Code-LS-style. One line per entry:
    `name/` for dirs, `name  (size)` for files, sorted dirs-first. Stays
    inside the workspace; skips nothing (the caller sees the real tree, but
    dot-dirs like .git are included so the agent can reason about state)."""
    try:
        base = (cwd or workdir())
        target = (base / path).resolve()
        if not target.is_relative_to(base):
            return f"Error: path escapes workspace: {path}"
        if not target.exists():
            return f"Error: no such directory: {path}"
        if not target.is_dir():
            return f"Error: not a directory: {path}"
        entries = []
        for p in target.iterdir():
            if p.is_dir():
                entries.append((0, p.name + "/"))
            else:
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                entries.append((1, f"{p.name}  ({size} bytes)"))
        entries.sort(key=lambda e: (e[0], e[1].lower()))
        rel = str(target.relative_to(base)) or "."
        if not entries:
            return f"{rel}/ (empty)"
        return f"{rel}/\n" + "\n".join(e[1] for e in entries)
    except Exception as e:
        return f"Error: {e}"


def run_ask_user(question: str, options: list = None, header: str = "",
                 detail: str = "", multi_select: bool = False,
                 timeout: float = 300.0) -> str:
    """Ask the user a question and block until they answer. In the gateway this
    emits an `ask_user` event (rendered as UserQuestionModal) and blocks on a
    future the client resolves via chat.send{request_id, answers}. In CLI it
    prints the question + options and reads input(). Returns the selected option
    label (comma-joined for multi_select), or the custom input if the user typed
    one. Times out after `timeout` seconds (default 300) returning a notice so
    the agent can proceed without an answer rather than hanging the turn."""
    import uuid
    options = list(options or [])
    request_id = uuid.uuid4().hex[:12]
    try:
        from agent_core.mcp import get_current_session
        s = get_current_session()
    except Exception:
        s = None
    resolver = getattr(s, "ask_resolver", None) if s is not None else None
    if resolver is not None:
        # Gateway path: register the future FIRST (so a fast client answer can't
        # land before the future exists), then emit the event, then block.
        try:
            fut = resolver(request_id)
            s.emit("ask_user", {
                "request_id": request_id,
                "questions": [{
                    "question": question,
                    "header": header or "Question",
                    "detail": detail,
                    "options": [{"label": o} for o in options],
                    "multi_select": bool(multi_select),
                }],
                "source": "ask_user_interrupt",
            })
            result = fut.result(timeout=timeout)
        except Exception as e:
            return f"(ask_user failed: {e})"
        return _format_ask_answer(result)
    # CLI path: print + input().
    print(f"\n\033[33m[ask_user] {question}\033[0m")
    if detail:
        print(f"  {detail}")
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}")
    if options:
        prompt = (f"  Choose {'numbers (comma-separated)' if multi_select else 'number'}"
                  f" [1-{len(options)}] or type a custom answer: ")
    else:
        prompt = "  Your answer: "
    raw = input(prompt).strip()
    if not raw:
        return "(no answer)"
    if options:
        parts = [p.strip() for p in raw.split(",")] if multi_select else [raw]
        picked = []
        for p in parts:
            if p.isdigit() and 1 <= int(p) <= len(options):
                picked.append(options[int(p) - 1])
            elif p in options:
                picked.append(p)
            else:
                picked.append(p)  # custom input
        return ", ".join(picked)
    return raw


def _format_ask_answer(result) -> str:
    """Normalize the gateway answer future result into a string. The client sends
    answers=[{selected_options:[label,...], custom_input?}]; respond_ask resolves
    the future with that list (or a bare string)."""
    if isinstance(result, str):
        return result
    answers = result
    if isinstance(result, dict):
        answers = result.get("answers") or result.get("selected_options") or []
    if isinstance(answers, list) and answers:
        a0 = answers[0]
        if isinstance(a0, dict):
            sel = a0.get("selected_options") or []
            custom = a0.get("custom_input")
            if sel:
                return ", ".join(str(x) for x in sel)
            if custom:
                return str(custom)
        elif isinstance(a0, str):
            return a0
    return "(no answer)"


def run_show_widget(type: str = "svg", content: str = "",
                    title: str = "", width: int = 0, height: int = 0) -> str:
    """Render an inline widget to the user — an SVG chart/diagram or an
    interactive HTML snippet. In the gateway the widget is emitted as a `widget`
    event (→ chat.widget) and the frontend renders it in a sandboxed iframe so
    embedded scripts run but can't touch the parent page. In CLI there's no
    inline surface, so the content is written to a temp .html/.svg file and the
    path is returned for the user to open. The tool always returns a short
    confirmation so the agent can continue; the visual artifact travels
    out-of-band via the event, not the tool_result."""
    wtype = (type or "svg").strip().lower()
    if wtype not in ("svg", "html"):
        return f"Error: type must be 'svg' or 'html' (got {type!r})"
    if not content:
        return "Error: content is empty"
    try:
        from agent_core.mcp import get_current_session
        s = get_current_session()
    except Exception:
        s = None
    if s is not None and getattr(s, "transport", "cli") != "cli":
        payload = {"type": wtype, "content": content, "title": title or ""}
        if width:
            payload["width"] = int(width)
        if height:
            payload["height"] = int(height)
        try:
            s.emit("widget", payload)
        except Exception:
            pass
        label = title or (f"{wtype} widget")
        return f"Rendered {label} ({len(content)} chars {wtype}) to the user."
    # CLI: spill to a temp file the user can open in a browser.
    try:
        import tempfile
        suffix = ".svg" if wtype == "svg" else ".html"
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False,
                                         encoding="utf-8") as f:
            if wtype == "svg":
                f.write(content)
            else:
                f.write(content)
            path = f.name
        print(f"  \033[36m[widget] {title or wtype} → {path}\033[0m")
        return f"Wrote {title or wtype + ' widget'} to {path} ({len(content)} chars). Open it in a browser."
    except Exception as e:
        return f"Error writing widget: {e}"


def run_grep(pattern: str, path: str = ".", output_mode: str = "content",
             max_results: int = 200, cwd: Path = None) -> str:
    """Content search with a regex, Claude-Code-Grep-style.

    pattern: Python regex. path: file or dir to search (relative to workspace).
    output_mode: "content" (default) prints file:line: match; "files_with_matches"
    prints just filenames; "count" prints file: count.
    Stays inside the workspace; skips .git, node_modules, .venv, __pycache__, .next.
    """
    import re
    try:
        base = cwd or workdir()
        target = (base / path).resolve()
        if not target.is_relative_to(base):
            return f"Error: path escapes workspace: {path}"
        regex = re.compile(pattern)
        skip = {".git", "node_modules", ".venv", "__pycache__", ".next",
                ".pytest_cache", ".task_outputs", ".transcripts"}
        files = []
        if target.is_file():
            files = [target]
        else:
            for p in target.rglob("*"):
                if not p.is_file():
                    continue
                if any(part in skip for part in p.parts):
                    continue
                files.append(p)
        out = []
        matches = 0
        for fp in files:
            try:
                text = fp.read_text(errors="replace")
            except Exception:
                continue
            local = str(fp.relative_to(base))
            file_hits = 0
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    file_hits += 1
                    matches += 1
                    if output_mode == "content" and matches <= max_results:
                        out.append(f"{local}:{i}: {line[:300]}")
            if file_hits and output_mode == "files_with_matches":
                out.append(local)
            if file_hits and output_mode == "count":
                out.append(f"{local}: {file_hits}")
        if not out:
            return "(no matches)"
        if matches > max_results and output_mode == "content":
            out.append(f"... ({matches - max_results} more matches, truncated at {max_results})")
        return "\n".join(out)
    except re.error as e:
        return f"Error: invalid regex: {e}"
    except Exception as e:
        return f"Error: {e}"

def call_tool_handler(handler, args: dict, name: str) -> str:
    if not handler:
        return f"Unknown: {name}"
    try:
        return handler(**(args or {}))
    except TypeError as e:
        return f"Error: {e}"

def _normalize_todos(todos):
    if isinstance(todos, str):
        try:
            todos = json.loads(todos)
        except json.JSONDecodeError:
            try:
                todos = ast.literal_eval(todos)
            except (SyntaxError, ValueError):
                return None, "Error: todos must be a list or JSON array string"
    if not isinstance(todos, list):
        return None, "Error: todos must be a list"
    for i, todo in enumerate(todos):
        if not isinstance(todo, dict):
            return None, f"Error: todos[{i}] must be an object"
        if "content" not in todo or "status" not in todo:
            return None, f"Error: todos[{i}] missing 'content' or 'status'"
        if todo["status"] not in ("pending", "in_progress", "completed"):
            return None, f"Error: todos[{i}] has invalid status '{todo['status']}'"
    return todos, None

def run_todo_write(todos: list) -> str:
    todos, error = _normalize_todos(todos)
    if error:
        return error
    set_todos(todos)
    print(f"  \033[33m[todo] updated {len(todos)} item(s)\033[0m")
    return f"Updated {len(todos)} todos"

def run_create_worktree(name: str, task_id: str = "") -> str:
    return create_worktree(name, task_id)

def run_remove_worktree(name: str, discard_changes: bool = False) -> str:
    return remove_worktree(name, discard_changes)

def run_keep_worktree(name: str) -> str:
    return keep_worktree(name)

def run_create_task(subject: str, description: str = "",
                    blockedBy: list[str] | None = None) -> str:
    task = create_task(subject, description, blockedBy)
    deps = f" (blockedBy: {', '.join(blockedBy)})" if blockedBy else ""
    print(f"  \033[34m[create] {task.subject}{deps}\033[0m")
    return f"Created {task.id}: {task.subject}{deps}"

def run_list_tasks() -> str:
    tasks = list_tasks()
    if not tasks:
        return "No tasks."
    return "\n".join(
        f"  {t.id}: {t.subject} [{t.status}]"
        + (f" (wt:{t.worktree})" if t.worktree else "")
        for t in tasks)

def run_get_task(task_id: str) -> str:
    try:
        return get_task_json(task_id)
    except FileNotFoundError:
        return f"Error: task {task_id} not found"

def run_claim_task(task_id: str) -> str:
    try:
        return claim_task(task_id, owner="agent")
    except FileNotFoundError:
        return f"Error: task {task_id} not found"

def run_complete_task(task_id: str) -> str:
    try:
        return complete_task(task_id)
    except FileNotFoundError:
        return f"Error: task {task_id} not found"

def run_spawn_teammate(name: str, role: str, prompt: str) -> str:
    return spawn_teammate_thread(name, role, prompt)

def run_send_message(to: str, content: str) -> str:
    BUS.send("lead", to, content)
    return f"Sent to {to}"

def run_check_inbox() -> str:
    msgs = consume_lead_inbox(route_protocol=True)
    if not msgs:
        return "(inbox empty)"
    lines = []
    for m in msgs:
        meta = m.get("metadata", {})
        req_id = meta.get("request_id", "")
        tag = f" [{m['type']} req:{req_id}]" if req_id else f" [{m['type']}]"
        lines.append(f"  [{m['from']}]{tag} {m['content'][:200]}")
    return "\n".join(lines)

def run_connect_mcp(name: str, command: str = None,
                     args: list = None, env: dict = None) -> str:
    from agent_core.mcp import connect_mcp
    return connect_mcp(name, command=command, args=args, env=env)

BUILTIN_TOOLS = [
    {"name": "bash", "description":
     "Run a shell command. Foreground commands are capped at 120s. "
     "Set run_in_background=true to run detached with NO timeout: the command "
     "keeps running across turns and the agent is re-invoked when it exits. "
     "Use task_output to read a background task's output, task_stop to kill it.",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"},
                                     "run_in_background": {"type": "boolean"}},
                      "required": ["command"]}},
    {"name": "task_output", "description":
     "Read output from a background task started with bash(run_in_background=true). "
     "If timeout > 0, block up to that many seconds for the task to finish or more "
     "output to accumulate. Returns a status header plus the (tail-capped) output.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"},
                                     "timeout": {"type": "integer"}},
                      "required": ["task_id"]}},
    {"name": "task_stop", "description":
     "Kill a running background task (SIGTERM then SIGKILL on its process group).",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}},
                      "required": ["task_id"]}},
    {"name": "task_list", "description":
     "List background tasks with their status (running/completed/killed), pid, "
     "exit code, and command.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "read_file", "description":
     "Read a text file with `cat -n`-style line numbers. Auto-detects encoding "
     "(utf-8 → gbk → gb2312 → big5 → shift_jis → euc_kr → latin-1) and refuses "
     "binary files. Use offset/limit for paging.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "limit": {"type": "integer"},
                                     "offset": {"type": "integer"}},
                      "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "content": {"type": "string"}},
                      "required": ["path", "content"]}},
    {"name": "edit_file", "description":
     "Replace exact text in a file. Refuses if old_text matches more than one "
     "place unless replace_all=true (guards against ambiguous bulk edits).",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "old_text": {"type": "string"},
                                     "new_text": {"type": "string"},
                                     "replace_all": {"type": "boolean"}},
                      "required": ["path", "old_text", "new_text"]}},
    {"name": "list_dir", "description":
     "List directory entries (dirs-first, sorted). `name/` for dirs, "
     "`name  (size bytes)` for files. Stays inside the workspace.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": []}},
    {"name": "web_fetch", "description":
     "Fetch a URL (15s timeout, 5MB cap, follow redirects, upgrade http→https), "
     "convert HTML→markdown-ish text (headings/links/lists/code preserved), "
     "return up to max_chars + a note that the prompt should be applied by the caller.",
     "input_schema": {"type": "object",
                      "properties": {"url": {"type": "string"},
                                     "prompt": {"type": "string"},
                                     "max_chars": {"type": "integer"}},
                      "required": ["url", "prompt"]}},
    {"name": "web_search", "description":
     "Search the web via DuckDuckGo Lite (no API key needed). Returns up to "
     "max_results blocks of `title / url / snippet`. US-only.",
     "input_schema": {"type": "object",
                      "properties": {"query": {"type": "string"},
                                     "max_results": {"type": "integer"}},
                      "required": ["query"]}},
    {"name": "glob", "description":
     "Find files matching a glob pattern (supports `**` recursive). `path` scopes "
     "the search root (default workspace). Sorted, capped at 200; skips "
     ".git/node_modules/.venv/__pycache__/.next.",
     "input_schema": {"type": "object",
                      "properties": {"pattern": {"type": "string"},
                                     "path": {"type": "string"}},
                      "required": ["pattern"]}},
    {"name": "ask_user", "description":
     "Ask the user a question and block until they answer. Returns the selected "
     "option label (comma-joined for multi_select) or the user's custom input. "
     "Use for decisions only the user can make; don't overuse. Times out after "
     "300s returning '(no answer)' so the turn isn't blocked forever.",
     "input_schema": {"type": "object",
                      "properties": {"question": {"type": "string"},
                                     "options": {"type": "array",
                                                 "items": {"type": "string"}},
                                     "header": {"type": "string"},
                                     "detail": {"type": "string"},
                                     "multi_select": {"type": "boolean"}},
                      "required": ["question"]}},
    {"name": "show_widget", "description":
     "Render an inline visual widget to the user — an SVG chart/flowchart or an "
     "interactive HTML snippet. Use for diagrams, plots, and small interactive "
     "controls that communicate better than text. The widget renders in a "
     "sandboxed iframe (scripts run but cannot access the parent page). "
     "type='svg': pass a complete <svg>...</svg>. type='html': pass an HTML "
     "snippet (can include <style>/<script>). Returns a short confirmation; the "
     "artifact itself is delivered out-of-band and is NOT in the tool result.",
     "input_schema": {"type": "object",
                      "properties": {"type": {"type": "string", "enum": ["svg", "html"]},
                                     "content": {"type": "string"},
                                     "title": {"type": "string"},
                                     "width": {"type": "integer"},
                                     "height": {"type": "integer"}},
                      "required": ["type", "content"]}},
    {"name": "grep", "description":
     "Search file contents with a regex (Python re syntax). Returns file:line: match "
     "lines by default. output_mode: 'content' | 'files_with_matches' | 'count'. "
     "Stays inside the workspace; skips .git/node_modules/.venv/__pycache__/.next.",
     "input_schema": {"type": "object",
                      "properties": {"pattern": {"type": "string"},
                                     "path": {"type": "string"},
                                     "output_mode": {"type": "string"},
                                     "max_results": {"type": "integer"}},
                      "required": ["pattern"]}},
    {"name": "todo_write",
     "description": "Create and manage a task list for the current session.",
     "input_schema": {"type": "object",
                      "properties": {"todos": {"type": "array",
                            "items": {"type": "object",
                                    "properties": {"content": {"type": "string"},
                                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}},
                                    "required": ["content", "status"]}}},
                      "required": ["todos"]}},
    {"name": "task",
     "description": "Launch a focused subagent. Returns only its final summary. "
                    "Pass agent=<name> to use a defined subagent's prompt/tools/model.",
     "input_schema": {"type": "object",
                      "properties": {"description": {"type": "string"},
                                     "agent": {"type": "string"}},
                      "required": ["description"]}},
    {"name": "load_skill",
     "description": "Load the full content of a skill by name.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"}},
                      "required": ["name"]}},
    {"name": "compact",
     "description": "Summarize earlier conversation and continue with compacted context.",
     "input_schema": {"type": "object",
                      "properties": {"focus": {"type": "string"}},
                      "required": []}},
    {"name": "create_task", "description": "Create a task.",
     "input_schema": {"type": "object",
                      "properties": {"subject": {"type": "string"},
                                     "description": {"type": "string"},
                                     "blockedBy": {"type": "array",
                                                   "items": {"type": "string"}}},
                      "required": ["subject"]}},
    {"name": "list_tasks", "description": "List all tasks.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_task", "description": "Get full task details.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}},
                      "required": ["task_id"]}},
    {"name": "claim_task", "description": "Claim a pending task.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}},
                      "required": ["task_id"]}},
    {"name": "complete_task", "description": "Complete an in-progress task.",
     "input_schema": {"type": "object",
                      "properties": {"task_id": {"type": "string"}},
                      "required": ["task_id"]}},
    {"name": "schedule_cron",
     "description": ("Schedule a cron job. cron is 5-field: min hour dom "
                     "month dow. For one-shot reminders, compute the target "
                     "minute and set recurring=false."),
     "input_schema": {"type": "object",
                      "properties": {"cron": {"type": "string"},
                                     "prompt": {"type": "string"},
                                     "recurring": {"type": "boolean"},
                                     "durable": {"type": "boolean"}},
                      "required": ["cron", "prompt"]}},
    {"name": "list_crons", "description": "List registered cron jobs.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "cancel_cron", "description": "Cancel a cron job by ID.",
     "input_schema": {"type": "object",
                      "properties": {"job_id": {"type": "string"}},
                      "required": ["job_id"]}},
    {"name": "spawn_teammate", "description": "Spawn an autonomous teammate.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "role": {"type": "string"},
                                     "prompt": {"type": "string"}},
                      "required": ["name", "role", "prompt"]}},
    {"name": "send_message", "description": "Send message to a teammate.",
     "input_schema": {"type": "object",
                      "properties": {"to": {"type": "string"},
                                     "content": {"type": "string"}},
                      "required": ["to", "content"]}},
    {"name": "check_inbox",
     "description": "Check inbox for messages and protocol responses.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "request_shutdown",
     "description": "Request a teammate to shut down.",
     "input_schema": {"type": "object",
                      "properties": {"teammate": {"type": "string"}},
                      "required": ["teammate"]}},
    {"name": "request_plan",
     "description": "Ask a teammate to submit a plan.",
     "input_schema": {"type": "object",
                      "properties": {"teammate": {"type": "string"},
                                     "task": {"type": "string"}},
                      "required": ["teammate", "task"]}},
    {"name": "review_plan",
     "description": "Approve or reject a submitted plan.",
     "input_schema": {"type": "object",
                      "properties": {"request_id": {"type": "string"},
                                     "approve": {"type": "boolean"},
                                     "feedback": {"type": "string"}},
                      "required": ["request_id", "approve"]}},
    {"name": "create_worktree",
     "description": "Create an isolated git worktree.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "task_id": {"type": "string"}},
                      "required": ["name"]}},
    {"name": "remove_worktree",
     "description": "Remove a worktree. Refuses if changes exist.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "discard_changes": {"type": "boolean"}},
                      "required": ["name"]}},
    {"name": "keep_worktree",
     "description": "Keep a worktree for manual review.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"}},
                      "required": ["name"]}},
    {"name": "connect_mcp",
     "description": "Connect to an MCP server and discover its tools. Give a "
                    "command+args for a stdio server, or a name defined in "
                    "mcp.json, or a built-in mock name (docs, deploy). "
                    "Discovered tools become callable as mcp__<server>__<tool>.",
     "input_schema": {"type": "object",
                      "properties": {
                          "name": {"type": "string", "description": "server name to register it under"},
                          "command": {"type": "string", "description": "executable to run (stdio transport). Omit to use mcp.json or a mock name."},
                          "args": {"type": "array", "items": {"type": "string"}},
                          "env": {"type": "object", "additionalProperties": {"type": "string"}}
                      },
                      "required": ["name"]}},
]

BUILTIN_HANDLERS = {
    "bash": run_bash, "read_file": run_read, "write_file": run_write,
    "edit_file": run_edit, "list_dir": run_list_dir,
    "web_fetch": run_web_fetch, "web_search": run_web_search,
    "glob": run_glob, "grep": run_grep, "ask_user": run_ask_user,
    "show_widget": run_show_widget,
    "task_output": run_task_output, "task_stop": run_task_stop,
    "task_list": run_task_list,
    "todo_write": run_todo_write, "task": spawn_subagent,
    "load_skill": load_skill,
    "create_task": run_create_task, "list_tasks": run_list_tasks,
    "get_task": run_get_task,
    "claim_task": run_claim_task, "complete_task": run_complete_task,
    "schedule_cron": run_schedule_cron,
    "list_crons": run_list_crons,
    "cancel_cron": run_cancel_cron,
    "spawn_teammate": run_spawn_teammate,
    "send_message": run_send_message, "check_inbox": run_check_inbox,
    "request_shutdown": run_request_shutdown,
    "request_plan": run_request_plan, "review_plan": run_review_plan,
    "create_worktree": run_create_worktree,
    "remove_worktree": run_remove_worktree,
    "keep_worktree": run_keep_worktree,
    "connect_mcp": run_connect_mcp,
}
