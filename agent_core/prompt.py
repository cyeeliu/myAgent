"""agent_core.prompt — extracted from code.py (s20 comprehensive agent)."""
from datetime import datetime
from agent_core.env import workdir
from agent_core.mcp import _mcp_clients
from agent_core.skills import list_skills
from agent_core.agents import scan_agents


PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "tools": "Available tools: bash, read_file, write_file, edit_file, glob, "
             "todo_write, task, load_skill, compact, "
             "create_task, list_tasks, get_task, claim_task, complete_task, "
             "schedule_cron, list_crons, cancel_cron, "
             "spawn_teammate, send_message, check_inbox, "
             "request_shutdown, request_plan, review_plan, "
             "create_worktree, remove_worktree, keep_worktree, "
             "connect_mcp. MCP tools are prefixed mcp__{server}__{tool}.",
    "workspace": f"Working directory: {workdir()}",
    "memory": "Relevant memories are injected below when available.",
}

def assemble_system_prompt(context: dict) -> str:
    # The system prompt is rebuilt each turn from live context. This is where
    # memory, skill catalog, MCP state, and active teammates become visible.
    sections = [PROMPT_SECTIONS["identity"],
                PROMPT_SECTIONS["tools"],
                PROMPT_SECTIONS["workspace"]]
    sections.append(f"Current time: {datetime.now().isoformat(timespec='seconds')}")
    sections.append("Skills catalog:\n" + list_skills(enabled_only=True) +
                    "\nUse load_skill(name) when a skill is relevant.")
    sections.append("Agents catalog:\n" + scan_agents() +
                    "\nUse task(description=..., agent=<name>) to dispatch a defined agent.")
    if context.get("memories"):
        sections.append(f"Relevant memories:\n{context['memories']}")
    mcp_names = list(_mcp_clients().keys())
    if mcp_names:
        sections.append(f"Connected MCP servers: {', '.join(mcp_names)}")
    # ── 集群模式 (Cluster Mode) directive ──
    # Set by the gateway (agent_compat.py CHAT_SEND) when the user picks
    # mode='team'. Steers the agent to start_team + orchestrate instead of
    # answering directly. str = single configured team; dict{"teams": [...]} =
    # multiple, let the agent pick the best fit for the task.
    team_mode = context.get("team_mode")
    if team_mode:
        if isinstance(team_mode, dict) and team_mode.get("teams"):
            avail = ", ".join(str(t) for t in team_mode["teams"])
            sections.append(
                "## 集群模式 (Cluster Mode)\n"
                "用户选择了集群模式。可用团队：" + avail + "。\n"
                "请根据用户任务挑选最合适的一个团队，然后立即调用\n"
                "  start_team(team_name=<你选的团队>, task=<用户的请求>)\n"
                "启动团队。随后用 wait(sources=[\"team\",\"background\"], timeout=600)\n"
                "配合 check_inbox 轮询 leader 的结果，必要时用 review_plan 审阅/批准\n"
                "成员计划，最后把团队产出综合成最终答复。不要自己直接回答用户的问题。"
            )
        else:
            name = str(team_mode)
            sections.append(
                "## 集群模式 (Cluster Mode)\n"
                "用户选择了集群模式。请立即调用\n"
                "  start_team(team_name=\"" + name + "\", task=<用户的请求>)\n"
                "启动团队。随后用 wait(sources=[\"team\",\"background\"], timeout=600)\n"
                "配合 check_inbox 轮询 leader 的结果，必要时用 review_plan 审阅/批准\n"
                "成员计划，最后把团队产出综合成最终答复。不要自己直接回答用户的问题。"
            )
    # ── 规划模式 (Plan Mode) directive ──
    # Set by the gateway (agent_compat.py CHAT_SEND) when the user picks
    # mode='agent.plan'. The tool pool is already restricted to read-only +
    # exit_plan_mode (mcp.assemble_tool_pool); this directive tells the agent
    # what to do: explore read-only, then submit the plan via exit_plan_mode
    # for user approval. Approval pops plan_mode → full tools next turn.
    if context.get("plan_mode"):
        sections.append(
            "## 规划模式 (Plan Mode)\n"
            "你处于规划模式。只能用只读工具探索代码库，**禁止修改任何文件或状态**。\n"
            "探索完成后，产出一个详细实施方案（改哪些文件、怎么改、为什么），然后调用\n"
            "  exit_plan_mode(plan=<你的完整方案>)\n"
            "把方案提交给用户审批。\n"
            "- 用户选「批准并执行」→ 规划模式自动退出，你将获得全部工具，按方案执行。\n"
            "- 用户选「拒绝」→ 继续留在规划模式，根据反馈修改方案后重新调用 exit_plan_mode 提交。\n"
            "在得到批准前不要尝试执行任何修改。不要用 ask_user 提交方案，必须用 exit_plan_mode。"
        )
    return "\n\n".join(sections)
