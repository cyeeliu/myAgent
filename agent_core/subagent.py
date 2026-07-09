"""agent_core.subagent — extracted from code.py (s20 comprehensive agent)."""
from agent_core import adapter
from agent_core import model_config
from agent_core.blocks import extract_text, has_tool_use
from agent_core.env import workdir
from agent_core.hooks import trigger_hooks
# call_tool_handler + BUILTIN_TOOLS/HANDLERS imported lazily inside
# spawn_subagent to avoid a tools<->subagent circular import
# (tools top-imports subagent for BUILTIN_HANDLERS["task"]).


SUB_SYSTEM = (
    f"You are a coding subagent at {workdir()}. "
    "Complete the task, then return a concise final summary. "
    "Do not spawn more agents."
)

SUB_TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object",
                      "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
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
    {"name": "edit_file", "description": "Replace exact text in a file once.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "old_text": {"type": "string"},
                                     "new_text": {"type": "string"}},
                      "required": ["path", "old_text", "new_text"]}},
    {"name": "glob", "description": "Find files matching a glob pattern.",
     "input_schema": {"type": "object",
                      "properties": {"pattern": {"type": "string"}},
                      "required": ["pattern"]}},
]

SUB_HANDLERS = None  # kept for backward-compat (re-exported); unused by spawn_subagent


def _resolve_toolset(tool_names: list[str]):
    """Pick (schemas, handlers) by name from the builtin tables.
    Raises KeyError listing any unknown names."""
    from agent_core.tools import BUILTIN_TOOLS, BUILTIN_HANDLERS
    schema_by_name = {t["name"]: t for t in BUILTIN_TOOLS}
    tools, handlers, missing = [], {}, []
    for n in tool_names:
        if n in schema_by_name and n in BUILTIN_HANDLERS:
            tools.append(schema_by_name[n])
            handlers[n] = BUILTIN_HANDLERS[n]
        else:
            missing.append(n)
    if missing:
        raise KeyError(f"unknown tools: {missing}")
    return tools, handlers


def spawn_subagent(description: str, agent: str | None = None) -> str:
    """Launch a focused subagent and return its final text summary.

    agent=None → ad-hoc subagent with SUB_SYSTEM/SUB_TOOLS and the global model.
    agent=<name> → load the defined agent (.agents/<name>.json) and use its
    prompt/tools/model (model=null inherits the global MODEL)."""
    from agent_core.tools import call_tool_handler

    if agent is not None:
        from agent_core.agents import get_agent, scan_agents
        defn = get_agent(agent)
        if defn is None:
            return f"Agent not found: {agent}. Available:\n{scan_agents()}"
        system = defn.get("prompt") or SUB_SYSTEM
        tool_names = defn.get("tools") or [t["name"] for t in SUB_TOOLS]
        model = defn.get("model") or model_config.model()
    else:
        system = SUB_SYSTEM
        tool_names = [t["name"] for t in SUB_TOOLS]
        model = model_config.model()

    try:
        tools, sub_handlers = _resolve_toolset(tool_names)
    except KeyError as e:
        return f"Agent {agent or '(ad-hoc)'} tool resolution failed: {e}"

    messages = [{"role": "user", "content": description}]
    for _ in range(30):
        response = adapter.chat_create(
            model=model, system=system, messages=messages,
            tools=tools, max_tokens=8000)
        messages.append({"role": "assistant", "content": response.content})
        if not has_tool_use(response.content):
            break
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            blocked = trigger_hooks("PreToolUse", block)
            if blocked:
                output = str(blocked)
            else:
                handler = sub_handlers.get(block.name)
                output = call_tool_handler(handler, block.input, block.name)
                trigger_hooks("PostToolUse", block, output)
            results.append({"type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(output)})
        messages.append({"role": "user", "content": results})
    for msg in reversed(messages):
        if msg["role"] == "assistant":
            text = extract_text(msg["content"])
            if text:
                return text
    return "Subagent finished without a text summary."
