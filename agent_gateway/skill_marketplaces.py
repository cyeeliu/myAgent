"""Online skill marketplace backends — ClawHub (clawhub.ai) and SkillNet
(github.com/zjunlp/SkillNet, searched via GitHub's repository search API).

These back the jiuwenswarm SkillPanel's `skills.clawhub.*` / `skills.skillnet.*`
WS methods so the 技能广场 online subtabs have real content. All HTTP uses stdlib
urllib (no extra deps). Search hits the upstream public APIs (no token needed);
install writes the skill into the shared workspace `skills/` dir so the agent's
own scan_skills() picks it up next turn. Failures return {success:False, ...}
so the frontend shows a localized error instead of an empty list."""
import base64
import json
import urllib.parse
import urllib.request

from agent_core.env import workspace_dir
from agent_core.skills import resolve_install_dst

_GITHUB_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "myAgent"}
_CLAWHUB_BASE = "https://clawhub.ai/api/v1"
_HTTP_TIMEOUT = 15
_INSTALL_TIMEOUT = 60

# ── search result cache ────────────────────────────────────────────────
# The frontend search modals fire a query on (debounced) every keystroke and
# re-open re-runs the same listing; clawhub's upstream is ~2-3s per call. A
# 60s TTL cache keyed by (source, query, limit) turns repeat/identical calls
# into instant returns without staleness mattering for a browse UI. Only
# successful results are cached so transient errors retry.
import threading as _threading
import time as _time
_SEARCH_CACHE: dict[str, tuple[float, dict]] = {}
_SEARCH_CACHE_TTL = 300.0


def _cached_search(key: str, producer):
    """TTL cache + stale-while-revalidate for marketplace search.

    Fresh hit → return immediately. Stale hit → return immediately AND refresh
    in a background daemon thread (so repeats are always instant, even past TTL,
    and the next caller sees fresh data). Miss → fetch synchronously, cache on
    success. Only successful results are cached so transient errors retry."""
    now = _time.monotonic()
    hit = _SEARCH_CACHE.get(key)
    if hit:
        if now - hit[0] < _SEARCH_CACHE_TTL:
            return hit[1]

        def _refresh():
            try:
                r = producer()
                if isinstance(r, dict) and r.get("success"):
                    _SEARCH_CACHE[key] = (_time.monotonic(), r)
            except Exception:
                pass
        _threading.Thread(target=_refresh, daemon=True).start()
        return hit[1]
    result = producer()
    if isinstance(result, dict) and result.get("success"):
        _SEARCH_CACHE[key] = (now, result)
    return result


def _http_get(url: str, headers: dict | None = None, timeout: int = _HTTP_TIMEOUT) -> dict:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "myAgent"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _write_manifest(skill_dir, info: dict) -> None:
    """Persist marketplace provenance (source/version/author/summary/url) next to
    SKILL.md as `.marketplace.json` so scan_skills can surface it in the UI."""
    try:
        (skill_dir / ".marketplace.json").write_text(
            json.dumps(info, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ── ClawHub (clawhub.ai) ───────────────────────────────────────────────
#
# /api/v1/search?q=<kw> is the only keyword-aware endpoint (~2-3s server-side,
# relevance-ranked). /api/v1/skills?limit= is a listing that ignores q. We use
# the search endpoint for keywords and the listing for empty query, both behind
# the shared TTL cache + stale-while-revalidate. The 2-3s first miss is
# clawhub.ai's server latency — irreducible locally; caching + SWR + frontend
# prefetch hide it for everything except the first search of a brand-new keyword.

def clawhub_search(q: str, limit: int = 50) -> dict:
    kw = q.strip()
    return _cached_search(f"clawhub:{kw}:{limit}", lambda: _clawhub_search_fetch(kw, limit))


def _clawhub_search_fetch(kw: str, limit: int) -> dict:
    try:
        if kw:
            data = _http_get(f"{_CLAWHUB_BASE}/search?q={urllib.parse.quote(kw)}&limit={limit}", timeout=8)
            skills = [{
                "slug": it.get("slug", ""),
                "display_name": it.get("displayName", ""),
                "summary": it.get("summary", ""),
                "version": it.get("version") or "",
                "updated_at": it.get("updatedAt", 0),
                "downloads": it.get("downloads", 0),
                "owner": (it.get("ownerHandle") or (it.get("owner") or {}).get("handle") or ""),
                "score": it.get("score", 0),
            } for it in data.get("results", [])]
            return {"success": True, "skills": skills}
        data = _http_get(f"{_CLAWHUB_BASE}/skills?limit={limit}", timeout=8)
        skills = []
        for it in data.get("items", []):
            lv = it.get("latestVersion") or {}
            stats = it.get("stats") or {}
            skills.append({
                "slug": it.get("slug", ""),
                "display_name": it.get("displayName", ""),
                "summary": it.get("summary", ""),
                "version": lv.get("version", ""),
                "updated_at": it.get("updatedAt", 0),
                "downloads": stats.get("downloads", 0),
                "owner": (it.get("owner") or {}).get("handle", ""),
                "score": 0,
            })
        return {"success": True, "skills": skills}
    except Exception as e:
        return {"success": False, "detail": f"search failed: {e}"}


def clawhub_download(slug: str, force: bool = False, meta: dict | None = None) -> dict:
    """Fetch the skill detail (its `description` field IS the SKILL.md content)
    and write it into workspace/skills/<slug>/SKILL.md. Persist provenance
    (version/owner/source) to `.marketplace.json` from the detail response,
    merged with any `meta` the frontend passed from the search result."""
    if not slug:
        return {"success": False, "detail": "missing slug"}
    dst, same = resolve_install_dst(slug, "clawhub")
    if same and not force:
        return {"success": False,
                "detail_key": "skills.clawhub.errors.skillAlreadyInstalled"}
    try:
        data = _http_get(f"{_CLAWHUB_BASE}/skills/{urllib.parse.quote(slug)}")
    except Exception as e:
        return {"success": False, "detail": f"fetch failed: {e}"}
    sk = data.get("skill", data)
    content = sk.get("description") or ""
    if not content:
        return {"success": False, "detail": "skill has no content"}
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "SKILL.md").write_text(content, encoding="utf-8")
    lv = data.get("latestVersion") or {}
    owner = (data.get("owner") or {}).get("handle") or ""
    _write_manifest(dst, {
        "source": "clawhub",
        "slug": slug,
        "version": lv.get("version") or (meta or {}).get("version") or "",
        "author": owner or (meta or {}).get("author") or "",
        "summary": sk.get("summary") or (meta or {}).get("summary") or "",
        "url": f"https://clawhub.ai/skills/{slug}",
    })
    return {"success": True, "skill": {"name": slug}}


def clawhub_get_token() -> dict:
    """ClawHub search is public; no token required. Report an empty token so the
    frontend's token-management UI degrades gracefully."""
    return {"success": True, "token": "", "has_token": False}


def clawhub_set_token(token: str) -> dict:
    return {"success": True, "token": token or ""}


# ── SkillNet (GitHub repo search) ──────────────────────────────────────

def skillnet_search(q: str, limit: int = 20) -> dict:
    """Search GitHub for skill repos matching the query; map to SkillNetItem
    ({skill_name,skill_description,author,stars,skill_url,category}). An empty
    query searches for top-starred 'skill' repos so the subtab shows content on
    open without forcing the user to type first."""
    kw = q.strip()
    return _cached_search(f"skillnet:{kw}:{limit}", lambda: _skillnet_search_fetch(kw, limit))


def _skillnet_search_fetch(kw: str, limit: int) -> dict:
    try:
        ghq = f"{urllib.parse.quote(kw)}+skill" if kw else "skill"
        url = (f"https://api.github.com/search/repositories?"
               f"q={ghq}&sort=stars&per_page={limit}")
        data = _http_get(url, _GITHUB_HEADERS, timeout=10)
    except Exception as e:
        return {"success": False, "detail": f"search failed: {e}"}
    skills = []
    for r in data.get("items", []):
        topics = r.get("topics") or []
        skills.append({
            "skill_name": r.get("full_name", ""),
            "skill_description": r.get("description") or "",
            "author": (r.get("owner") or {}).get("login", ""),
            "stars": r.get("stargazers_count", 0),
            "skill_url": r.get("html_url", ""),
            "category": topics[0] if topics else "skill",
        })
    return {"success": True, "skills": skills}


def skillnet_install(url: str, force: bool = False, meta: dict | None = None) -> dict:
    """Install a SkillNet skill by downloading the FULL repo tarball from GitHub
    (`/repos/{owner}/{repo}/tarball/{ref}` → codeload redirect) and extracting
    every file into workspace/skills/<repo>/, preserving subdirs (scripts/,
    references/, etc.). Uses stdlib tarfile — no git in the container. Path
    traversal is guarded (absolute paths or '..' segments are skipped)."""
    import io
    import tarfile
    if not url:
        return {"success": False, "detail": "missing url"}
    parts = url.rstrip("/").split("/")
    if len(parts) < 2:
        return {"success": False, "detail": "bad url"}
    owner, repo = parts[-2], parts[-1]
    dst, same = resolve_install_dst(repo, "skillnet")
    if same and not force:
        return {"success": False,
                "detail_key": "skills.skillNet.errors.skillAlreadyInstalled"}
    try:
        tarball_url = f"https://api.github.com/repos/{owner}/{repo}/tarball"
        req = urllib.request.Request(tarball_url, headers=_GITHUB_HEADERS)
        with urllib.request.urlopen(req, timeout=_INSTALL_TIMEOUT) as r:
            buf = io.BytesIO(r.read())
    except Exception as e:
        return {"success": False, "detail": f"fetch tarball failed: {e}"}
    dst.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = []
    try:
        tf = tarfile.open(fileobj=buf, mode="r:gz")
    except Exception as e:
        return {"success": False, "detail": f"open tarball failed: {e}"}
    with tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            name = member.name.replace("\\", "/")
            # GitHub tarballs nest under `<owner>-<repo>-<ref>/` — strip it.
            if "/" in name:
                name = name.split("/", 1)[1]
            else:
                continue
            if not name or name.startswith("/") or any(
                seg == ".." for seg in name.split("/")
            ):
                skipped.append(member.name)
                continue
            target = dst / name
            target.parent.mkdir(parents=True, exist_ok=True)
            fobj = tf.extractfile(member)
            if fobj is None:
                continue
            target.write_bytes(fobj.read())
            written += 1
    if written == 0:
        return {"success": False, "detail": "tarball had no files"}
    _write_manifest(dst, {
        "source": "skillnet",
        "slug": repo,
        "version": (meta or {}).get("version") or "",
        "author": owner or (meta or {}).get("author") or "",
        "summary": (meta or {}).get("summary") or "",
        "url": url,
    })
    return {"success": True, "skill": {"name": repo},
            "files_written": written, "skipped": skipped}


def skillnet_install_status(install_id: str) -> dict:
    """Install is synchronous (no pending state) — report completion."""
    return {"status": "completed", "success": True}


def skillnet_evaluate(url: str) -> dict:
    """The evaluate UI is disabled (SKILLNET_EVALUATE_BUTTON_ENABLED=false);
    keep a stub so the method doesn't 404 if re-enabled."""
    return {"success": True, "evaluation": {}}


# ── TeamSkillsHub / SwarmSkills (teamskills.openjiuwen.com) ────────────

_TEAMSKILLS_API = "https://teamskills.openjiuwen.com/api/v1"
_TEAMSKILLS_SITE = "https://teamskills.openjiuwen.com"


# ── SkillHub (www.skillhub.cn — 专为中国用户优化的 Skills 社区) ────────

_SKILLHUB_API = "https://api.skillhub.cn"
_SKILLHUB_SITE = "https://www.skillhub.cn"


def skillhub_search(q: str, limit: int = 50) -> dict:
    """GET /api/skills?keyword=&sortBy=score&page=1&pageSize= →
    {code:0,data:{skills:[{slug,name,description,description_zh,category,
    downloads,stars,score,ownerName,version,iconUrl,...}],total}}. Public,
    no auth. Map to the SkillHubSkillItem shape the frontend expects. Empty
    query returns the score-ranked listing so the subtab shows content on open."""
    kw = q.strip()
    return _cached_search(f"skillhub:{kw}:{limit}", lambda: _skillhub_search_fetch(kw, limit))


def _skillhub_search_fetch(kw: str, limit: int) -> dict:
    try:
        params = {"sortBy": "score", "order": "desc",
                  "page": "1", "pageSize": str(limit)}
        if kw:
            params["keyword"] = kw
        url = f"{_SKILLHUB_API}/api/skills?{urllib.parse.urlencode(params)}"
        data = _http_get(url, timeout=8)
    except Exception as e:
        return {"success": False, "detail": f"search failed: {e}"}
    rows = (data.get("data") or {}).get("skills", [])
    skills = []
    for it in rows:
        skills.append({
            "asset_id": it.get("slug", ""),
            "name": it.get("slug", ""),
            "display_name": it.get("name", ""),
            "summary": it.get("description_zh") or it.get("description") or "",
            "version": it.get("version", ""),
            "updated_at": it.get("updated_at", 0),
            "stars": it.get("stars", 0),
            "downloads": it.get("downloads", 0),
            "score": it.get("score", 0),
            "icon_url": it.get("iconUrl", ""),
            "owner": it.get("ownerName", ""),
            "source": it.get("source", ""),
            "category": it.get("category", ""),
        })
    return {"success": True, "skills": skills,
            "total": (data.get("data") or {}).get("total", 0)}


def skillhub_info() -> dict:
    """Report the hub base URL so the frontend can build external links."""
    return {"success": True, "market_base_url": _SKILLHUB_SITE}


def skillhub_install(slug: str, force: bool = False, meta: dict | None = None) -> dict:
    """Install a SkillHub skill by fetching its FULL file tree and writing every
    file into workspace/skills/<slug>/<path> (preserving subdirs like scripts/,
    references/, etc.). Uses two endpoints:
      1. GET /api/v1/skills/{slug}/files → {files:[{path,sha256,size}], version}
      2. GET /api/v1/skills/{slug}/file?path=<path> → 302 → Tencent COS
    urllib follows the redirect. Public, no auth. Path traversal is guarded
    (paths with '..' segments or absolute paths are skipped)."""
    if not slug:
        return {"success": False, "detail": "missing slug"}
    dst, same = resolve_install_dst(slug, "skillhub")
    if same and not force:
        return {"success": False,
                "detail_key": "skills.skillhub.errors.skillAlreadyInstalled"}
    base = f"{_SKILLHUB_API}/api/v1/skills/{urllib.parse.quote(slug)}"
    try:
        listing = _http_get(f"{base}/files", timeout=_HTTP_TIMEOUT)
    except Exception as e:
        return {"success": False, "detail": f"fetch file list failed: {e}"}
    files = listing.get("files") or []
    if not files:
        return {"success": False, "detail": "skill has no files"}
    dst.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = []
    for f in files:
        path = f.get("path") or ""
        if not path:
            continue
        # Path-traversal guard: reject absolute or parent-dir escapes.
        norm = path.replace("\\", "/")
        if norm.startswith("/") or any(seg == ".." for seg in norm.split("/")):
            skipped.append(path)
            continue
        try:
            url = (f"{base}/file?path={urllib.parse.quote(path)}")
            req = urllib.request.Request(url, headers={"User-Agent": "myAgent"})
            with urllib.request.urlopen(req, timeout=_INSTALL_TIMEOUT) as r:
                content = r.read()
        except Exception as e:
            skipped.append(f"{path} ({e})")
            continue
        target = dst / norm
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        written += 1
    if written == 0:
        return {"success": False, "detail": "no files could be downloaded"}
    _write_manifest(dst, {
        "source": "skillhub",
        "slug": slug,
        "version": listing.get("version") or (meta or {}).get("version") or "",
        "author": (meta or {}).get("author") or "",
        "summary": (meta or {}).get("summary") or "",
        "url": f"{_SKILLHUB_SITE}/skill/{slug}",
    })
    return {"success": True, "skill": {"name": slug},
            "files_written": written, "skipped": skipped}


def teamskills_search(q: str, limit: int = 50) -> dict:
    """GET /api/v1/skills?q=&limit= → {items:[{slug,displayName,summary,
    latestVersion:{version},updatedAt,...}]}. Map to TeamSkillsHubSkillItem
    ({asset_id,name,display_name,summary,version,updated_at}). Empty query
    returns the registry's default listing so the subtab shows content on open."""
    try:
        base = f"{_TEAMSKILLS_API}/skills?limit={limit}"
        if q.strip():
            base += f"&q={urllib.parse.quote(q)}"
        data = _http_get(base)
    except Exception as e:
        return {"success": False, "detail": f"search failed: {e}"}
    skills = []
    for it in data.get("items", []):
        lv = it.get("latestVersion") or {}
        slug = it.get("slug", "")
        skills.append({
            "asset_id": slug,
            "name": slug,
            "display_name": it.get("displayName", ""),
            "summary": it.get("summary", ""),
            "version": lv.get("version", ""),
            "updated_at": it.get("updatedAt", 0),
        })
    return {"success": True, "skills": skills}


def teamskills_info() -> dict:
    """Report the hub base URL so the frontend can build external links."""
    return {"success": True, "market_base_url": _TEAMSKILLS_SITE}


def teamskills_install(asset_id: str, force: bool = False) -> dict:
    """Install a TeamSkillsHub skill. The registry exposes a file tree
    ({path,sha256,size}) per version but no public blob-download endpoint, so
    we fetch the SKILL.md via the version endpoint's file list and write what
    we can. If the content endpoint is unavailable, return a clear error so the
    UI shows a localized message instead of silently failing."""
    if not asset_id:
        return {"success": False, "detail": "missing asset_id"}
    dst = workspace_dir() / "skills" / asset_id
    if dst.exists() and not force:
        return {"success": False,
                "detail_key": "skills.teamskillshub.errors.skillAlreadyInstalled"}
    # Resolve the latest version, then try the candidate content endpoints.
    try:
        detail = _http_get(f"{_TEAMSKILLS_API}/skills/{urllib.parse.quote(asset_id)}")
        lv = detail.get("latestVersion") or {}
        version = lv.get("version") or ""
        if not version:
            return {"success": False, "detail": "no version found"}
        ver = _http_get(f"{_TEAMSKILLS_API}/skills/{urllib.parse.quote(asset_id)}/versions/{version}")
        files = (ver.get("version") or {}).get("files") or []
        skill_md = next((f for f in files if f.get("path") == "SKILL.md"), None)
        if not skill_md:
            return {"success": False, "detail": "skill has no SKILL.md"}
        sha = skill_md.get("sha256", "")
        # Try content-addressable blob endpoints (best-effort).
        content = None
        for url in (f"{_TEAMSKILLS_API}/blobs/{sha}",
                    f"{_TEAMSKILLS_API}/skills/{asset_id}/blobs/{sha}",
                    f"{_TEAMSKILLS_API}/skills/{asset_id}/versions/{version}/blobs/{sha}"):
            try:
                blob = _http_get(url)
                content = blob.get("content") if isinstance(blob, dict) else None
                if content:
                    break
            except Exception:
                continue
        if not content:
            return {"success": False,
                    "detail": "TeamSkillsHub content download not available"}
        import base64 as _b64
        text = _b64.b64decode(content).decode("utf-8", "replace")
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "SKILL.md").write_text(text, encoding="utf-8")
        return {"success": True, "skill": {"name": asset_id}}
    except Exception as e:
        return {"success": False, "detail": f"install failed: {e}"}
