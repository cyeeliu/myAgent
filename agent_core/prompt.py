"""agent_core.prompt — extracted from code.py (s20 comprehensive agent)."""
import platform
import subprocess
from datetime import datetime
from agent_core import model_config
from agent_core.env import AUTO_COMPACT_WINDOW, REPO_ROOT, workdir, workspace_dir
from agent_core.mcp import _mcp_clients
from agent_core.skills import list_skills, _skills_dir
from agent_core.agents import scan_agents, AGENTS_DIR


# Sections that never change across turns. In Claude Code these collapse into
# one global cache block; the OpenAI adapter sends a single system string so we
# can't exploit cache scopes, but keeping them separate documents intent.
PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "workspace": f"Working directory: {workdir()}",
    "memory": "Relevant memories are injected below when available.",
}

# Marker between the static prefix and the per-turn dynamic suffix. Claude Code
# uses this to split cache scopes (global cache on the static prefix, no cache
# on the dynamic suffix). The OpenAI adapter has no cache_control, so here it's
# a structural divider + documented cache intent, not a real cache breakpoint.
SYSTEM_PROMPT_DYNAMIC_BOUNDARY = "── dynamic context ──"


# ── Section cache ──────────────────────────────────────────────────────
# Per-section result cache, mirroring Claude Code's STATE.systemPromptSectionCache.
# Each section's built string is cached under an invalidation key capturing its
# inputs; on a hit the build fn is skipped so scan_skills()/list_agents() dir
# scans don't run every turn. Sections whose inputs change every turn (time,
# token_budget, memories, team/plan mode) use key=None and are never cached.
# Cleared on /compact (invalidate_section_cache) — belt-and-suspenders; the
# mtime/signature keys already auto-invalidate, so this is parity with Claude
# Code, not load-bearing. Module-level: gateway sessions share it. skills/agents
# are workspace-shared so cross-session hits are correct; tools/mcp are
# session-specific so a different session's key misses and rebuilds (thrashing
# but correct).
_SECTION_CACHE: dict[str, dict] = {}   # name -> {"key": <hashable>, "value": str}


def invalidate_section_cache(*names: str) -> None:
    """Drop cached prompt sections. No args → clear all (call on /compact)."""
    if not names:
        _SECTION_CACHE.clear()
    else:
        for n in names:
            _SECTION_CACHE.pop(n, None)


def _cached(name: str, key, build) -> str:
    """Return cached section `name` if key matches; else build() and cache.
    key=None → never cache (build fresh)."""
    if key is None:
        return build()
    entry = _SECTION_CACHE.get(name)
    if entry is not None and entry["key"] == key:
        return entry["value"]
    value = build()
    _SECTION_CACHE[name] = {"key": key, "value": value}
    return value


def _glob_mtime_signature(d, pattern: str):
    """Snapshot (relpath, mtime_ns) for d.glob(pattern) — invalidation key for
    skills/agents catalog sections. '__missing__'/'__empty__' for absent/empty
    dirs (constant → empty result still cached); None on error (don't cache)."""
    try:
        if not d.exists():
            return "__missing__"
        sig = tuple(sorted((str(p.relative_to(d)), p.stat().st_mtime_ns)
                           for p in d.glob(pattern)))
        return sig or "__empty__"
    except OSError:
        return None


def _file_mtime_sig(paths) -> tuple:
    """Snapshot (path, mtime_ns) for the subset of `paths` that exist —
    invalidation key for file-backed sections (project guidance, .git/HEAD).
    '__none__' when no file is present (constant → empty result still cached);
    None on error (don't cache)."""
    try:
        sig = tuple(sorted((str(p), p.stat().st_mtime_ns)
                           for p in paths if p.exists()))
    except OSError:
        return None
    return sig or "__none__"


def _load_project_guidance() -> str:
    """Read CLAUDE.md + AGENTS.md from the workspace and the repo root (deduped
    by resolved path) and concatenate as project guidance. Returns '' when none
    are present. session_guidance section — cf. Claude Code loading CLAUDE.md/
    AGENTS.md from the project tree."""
    seen: set = set()
    parts = []
    for base in (workspace_dir(), REPO_ROOT):
        for name in ("CLAUDE.md", "AGENTS.md"):
            p = base / name
            try:
                key = str(p.resolve())
            except OSError:
                continue
            if key in seen or not p.exists():
                continue
            try:
                text = p.read_text(errors="replace").strip()
            except OSError:
                continue
            if text:
                seen.add(key)
                parts.append(f"# {name} (from {base})\n{text}")
    return ("Project guidance:\n\n" + "\n\n".join(parts)) if parts else ""


def _git_branch() -> str:
    """Current git branch of the workspace, best-effort ('unknown' on failure)."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(workspace_dir()), capture_output=True, text=True, timeout=2)
        if r.returncode == 0:
            return r.stdout.strip() or "unknown"
    except Exception:
        pass
    return "unknown"


_STYLE_DIRECTIVES = {
    "concise": "Be concise: prefer short answers, omit filler.",
    "verbose": "Be thorough and detailed in explanations.",
    "markdown": "Format output in markdown with headers and code blocks.",
}


def _language_style_section(lang, style) -> str:
    """Output language + style directive. Empty when neither is set."""
    parts = []
    if lang:
        parts.append(f"Respond in {lang}.")
    if style:
        parts.append(_STYLE_DIRECTIVES.get(style) or f"Output style: {style}.")
    return "\n".join(parts)


# Constant directives (summarize_tool_results + scratchpad) — stable across
# turns, inlined into the static prefix.
_DIRECTIVES = (
    "When a tool result is very large, summarize the key points in your reply "
    "rather than reproducing it verbatim.\n"
    "You may reason in a <scratchpad>...</scratchpad> block before acting; it is "
    "internal scratch space, not shown to the user."
)


def assemble_system_prompt(context: dict, tools: list | None = None,
                            messages: list | None = None) -> str:
    # The system prompt is rebuilt each turn. Static sections (identity, workspace,
    # project guidance, language/style, env info, directives) are stable; dynamic
    # sections track live state (tool pool, time, token budget, skills, agents,
    # memories, MCP, team/plan mode). MCP is volatile — servers can connect/
    # disconnect between turns (cf. Claude Code's DANGEROUS_uncachedSystemPromptSection).
    # Cacheable sections go through _cached with an invalidation key; per-turn
    # sections (time, token_budget, memories, team/plan mode) are built fresh.
    static_sections = [
        PROMPT_SECTIONS["identity"],
        PROMPT_SECTIONS["workspace"],
    ]

    # session_guidance — CLAUDE.md + AGENTS.md from the workspace and repo root.
    # mtime-cached so an edit takes effect next turn without re-reading every turn.
    guidance = _cached("guidance",
                       _file_mtime_sig([workspace_dir() / "CLAUDE.md",
                                        workspace_dir() / "AGENTS.md",
                                        REPO_ROOT / "CLAUDE.md",
                                        REPO_ROOT / "AGENTS.md"]),
                       _load_project_guidance)
    if guidance:
        static_sections.append(guidance)

    # language + output_style — from model_config (model.json) or env
    # (OUTPUT_LANGUAGE / OUTPUT_STYLE). Cached on the (language, style) tuple.
    try:
        cfg = model_config.get_config()
        lang, style = cfg.get("language"), cfg.get("output_style")
    except Exception:
        lang, style = None, None
    lang_style = _cached("language_style", (lang, style),
                         lambda: _language_style_section(lang, style))
    if lang_style:
        static_sections.append(lang_style)

    # env_info_simple — platform + git branch. Cached on .git/HEAD mtime so a
    # branch switch invalidates and the git subprocess is skipped on hit.
    env_info = _cached("env_info",
                       _file_mtime_sig([workspace_dir() / ".git" / "HEAD"]),
                       lambda: f"Environment: {platform.platform()}; "
                               f"git branch: {_git_branch()}")
    static_sections.append(env_info)

    # summarize_tool_results + scratchpad directives (constant).
    static_sections.append(_DIRECTIVES)

    tool_names = [t["name"] for t in (tools or [])]
    mcp_names = list(_mcp_clients().keys())
    skills_key = _glob_mtime_signature(_skills_dir(), "**/SKILL.md")
    agents_key = _glob_mtime_signature(AGENTS_DIR, "*.json")

    dynamic_sections = [
        _cached("tools", tuple(tool_names),
                lambda: "Available tools: " + (", ".join(tool_names) + "." if tool_names else "(none)")),
        f"Current time: {datetime.now().isoformat(timespec='seconds')}",
    ]

    # token_budget — remaining context window so the agent self-paces compaction.
    # Per-turn (changes as messages grow) → not cached. system excluded from the
    # estimate to avoid a self-referential cycle (this line is part of system).
    if messages is not None:
        try:
            from agent_core.compaction import estimate_tokens
            used = estimate_tokens(messages, "", tools)
            pct = (used / AUTO_COMPACT_WINDOW * 100) if AUTO_COMPACT_WINDOW else 0
            dynamic_sections.append(
                f"Context budget: ~{used} of {AUTO_COMPACT_WINDOW} tokens "
                f"({pct:.0f}%) used; compact when approaching the limit.")
        except Exception:
            pass

    dynamic_sections.append(
        _cached("skills", skills_key,
                lambda: "Skills catalog:\n" + list_skills(enabled_only=True) +
                        "\nUse load_skill(name) when a skill is relevant."))
    dynamic_sections.append(
        _cached("agents", agents_key,
                lambda: "Agents catalog:\n" + scan_agents() +
                        "\nUse task(description=..., agent=<name>) to dispatch a defined agent."))

    if context.get("memories"):
        dynamic_sections.append(f"Relevant memories:\n{context['memories']}")

    # MCP instructions — volatile: may change mid-session as servers connect/
    # disconnect. Cached by the sorted server-name tuple so connect/disconnect
    # invalidates; kept last in the dynamic suffix to isolate churn.
    if mcp_names:
        dynamic_sections.append(_cached("mcp", tuple(sorted(mcp_names)),
                lambda: f"Connected MCP servers: {', '.join(mcp_names)}. "
                        f"Use mcp__{{server}}__{{tool}} prefixed names to call them."))
    # ── 集群模式 (Cluster Mode) directive ──
    # Set by the gateway (agent_compat.py CHAT_SEND) when the user picks
    # mode='team'. Steers the agent to start_team + orchestrate instead of
    # answering directly. str = single configured team; dict{"teams": [...]} =
    # multiple, let the agent pick the best fit for the task.
    team_mode = context.get("team_mode")
    if team_mode:
        if isinstance(team_mode, dict) and team_mode.get("teams"):
            avail = ", ".join(str(t) for t in team_mode["teams"])
            dynamic_sections.append(
                "## 集群模式 (Cluster Mode)\n"
                "用户选择了集群模式。可用团队：" + avail + "。\n"
                "请根据用户任务挑选最合适的一个团队，然后立即调用\n"
                "  start_team(team_name=<你选的团队>, task=<用户的请求>)\n"
                "启动团队。\n"
                "A2A 事件驱动协调（不要使用 wait 工具等待团队）：\n"
                "- 调用 start_team 后立即结束当前轮次（END YOUR TURN）。团队在后台线程异步工作。\n"
                "- 当队友发送结果时，系统会自动用新轮次重新唤起你，结果出现在 <team_messages> 中。\n"
                "- 每次被唤起后：处理消息，然后要么给 leader 发新指令并结束轮次，要么汇总最终结果给用户。\n"
                "- 绝对不要调用 wait(sources=[\"team\",...]) — 它会阻塞轮次并冻结会话。\n"
                "- 如需审阅成员计划，用 review_plan(request_id, approve, feedback)。\n"
                "- 收到 [TEAM COMPLETE] 或 [TEAM TIMEOUT] 时，汇总已有结果给用户。"
            )
        else:
            name = str(team_mode)
            dynamic_sections.append(
                "## 集群模式 (Cluster Mode)\n"
                "用户选择了集群模式。请立即调用\n"
                "  start_team(team_name=\"" + name + "\", task=<用户的请求>)\n"
                "启动团队。\n"
                "A2A 事件驱动协调（不要使用 wait 工具等待团队）：\n"
                "- 调用 start_team 后立即结束当前轮次（END YOUR TURN）。团队在后台线程异步工作。\n"
                "- 当队友发送结果时，系统会自动用新轮次重新唤起你，结果出现在 <team_messages> 中。\n"
                "- 每次被唤起后：处理消息，然后要么给 leader 发新指令并结束轮次，要么汇总最终结果给用户。\n"
                "- 绝对不要调用 wait(sources=[\"team\",...]) — 它会阻塞轮次并冻结会话。\n"
                "- 如需审阅成员计划，用 review_plan(request_id, approve, feedback)。\n"
                "- 收到 [TEAM COMPLETE] 或 [TEAM TIMEOUT] 时，汇总已有结果给用户。"
            )
    # ── 规划模式 (Plan Mode) directive ──
    # Set by the gateway (agent_compat.py CHAT_SEND) when the user picks
    # mode='agent.plan'. The tool pool is already restricted to read-only +
    # exit_plan_mode (mcp.assemble_tool_pool); this directive tells the agent
    # what to do: explore read-only, then submit the plan via exit_plan_mode
    # for user approval. Approval pops plan_mode → full tools next turn.
    if context.get("plan_mode"):
        dynamic_sections.append(
            "## 规划模式 (Plan Mode)\n"
            "你处于规划模式。只能用只读工具探索代码库，**禁止修改任何文件或状态**。\n"
            "探索完成后，产出一个详细实施方案（改哪些文件、怎么改、为什么），然后调用\n"
            "  exit_plan_mode(plan=<你的完整方案>)\n"
            "把方案提交给用户审批。\n"
            "- 用户选「批准并执行」→ 规划模式自动退出，你将获得全部工具，按方案执行。\n"
            "- 用户选「拒绝」→ 继续留在规划模式，根据反馈修改方案后重新调用 exit_plan_mode 提交。\n"
            "在得到批准前不要尝试执行任何修改。不要用 ask_user 提交方案，必须用 exit_plan_mode。"
        )
    return "\n\n".join(static_sections
                       + [SYSTEM_PROMPT_DYNAMIC_BOUNDARY]
                       + dynamic_sections)
