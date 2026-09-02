# Design: 三栏导航前端 + 智能体/模型配置（细化版）

Date: 2026-07-08
Status: elaborated spec — grounded in codebase recon (env/adapter/loop/recovery/subagent/skills/prompt/tools + gateway/main/schemas + frontend page/Sidebar/sessions/useSessionManager + tests/.gitignore/.env.example)

> 本文档在原设计基础上补全：API 契约示例、组件 props、状态机、边界用例、测试清单、现状基线签名。仍停留在设计层面，不含产品代码改动。实施时再按 P1→P2→P3 落地。

## Goal

把前端左侧改造成三个导航栏——**会话 / 智能体 / 模型**——点击不同栏右侧显示对应内容。其中「智能体」是 Claude Code 式的子 agent 定义（name + description + prompt + 可选 model + tools），由主 agent 通过 `task` 工具按名调度；「模型」是可在线编辑并持久化的全局模型配置。会话栏保持现有聊天行为。

## Non-goals

- 不做多副本同步：`.agents/` 和模型配置走磁盘文件，与现有 `skills/` 同策略，多 replica 间不自动同步（超出范围）。
- 不做每会话独立模型配置（全局单例）。
- 不在前端暴露完整 API key（掩码显示）。
- 不把 agent 定义存入 Postgres（磁盘-only，镜像 `skills/`）。
- 不复用 `.claude/agents/*.md`（那是 Claude Code harness 自身的 agent 定义格式，markdown+frontmatter）；本设计的 `.agents/<name>.json` 是 **agent 运行时按名调度的子 agent 定义**，JSON 结构体（含 tools 数组），两者独立。

## Layout

### view 状态机

`page.tsx` 顶层新增单一状态 `view`，三值切换：

```
view ∈ { "sessions", "agents", "model" }
初始: "sessions"
切换: 点击 Sidebar 导航项 → setView(item)
右侧渲染: switch(view) → ChatPanel | AgentEditor | ModelConfigPanel
```

切换是纯 UI 行为，不卸载正在跑的会话 transport（ChatPanel 仅在 view==="sessions" 时挂载；切走再切回会重连 transport，已有 `last_seq` resume 兜底，不回归）。

> 注：切走会话时 ChatPanel 卸载会断开 WS/SSE。可接受（重连靠 `last_seq` + 三级 replay）。若想保持会话后台不断流，P1 可用 `hidden` 类隐藏而非卸载——**决定：P1 用条件渲染（卸载），简单优先；若用户反馈断流体验差，P2 再改 hidden。** 记为风险 R1。

### page.tsx 改动

现状（`frontend/app/page.tsx`）：
```tsx
export default function Page() {
  const sm = useSessionManager();
  return (
    <div className="flex h-screen bg-paper-100 text-paper-900">
      <Sidebar sm={sm} />
      <main className="flex-1"><ChatPanel sessionId={sm.currentId} /></main>
    </div>
  );
}
```

改为：
```tsx
export default function Page() {
  const sm = useSessionManager();
  const [view, setView] = useState<"sessions" | "agents" | "model">("sessions");
  return (
    <div className="flex h-screen bg-paper-100 text-paper-900">
      <Sidebar sm={sm} view={view} setView={setView} />
      <main className="flex-1">
        {view === "sessions" && <ChatPanel sessionId={sm.currentId} />}
        {view === "agents"   && <AgentEditor />}
        {view === "model"    && <ModelConfigPanel />}
      </main>
    </div>
  );
}
```

### Sidebar 重构

现状（`frontend/components/Sidebar.tsx`）：`Sidebar({sm})` 内含 header + `SessionList` + skills/mcp tabs + skills/mcp 列表。`SessionList` 是同文件子组件，用 `sm.{sessions,currentId,switchTo,newSession,removeSession}`。

新结构：
```
Sidebar({ sm, view, setView })
├─ header（✨ myAgent，不变）
├─ 导航项列表（3 项：会话/智能体/模型）
│   每项: button, active = (view===item), active 用 bg-clay-100 + text-clay-600（对齐 SessionList active 样式）
│   点击 → setView(item)
├─ active 视图的上下文列表：
│   ├─ sessions → <SessionList sm={sm}/>（迁入，不变）
│   ├─ agents   → <AgentList/>（新，fetch /api/agents，+ 新建按钮）
│   └─ model    → (无列表，空)
├─ 可折叠区（底部）：skills/mcp tabs + 列表（现有逻辑整体下移，默认折叠，点击展开）
└─ (折叠区用 <details> 或 useState 折叠态)
```

- 现有 skills/mcp 的 `useEffect` fetch + 4s 轮询保留，仅位置下移到折叠区。
- `AgentList` 复用 `SessionList` 的视觉模板（border-b、+ 新建按钮、max-h-60 overflow-y-auto、active 高亮）。
- 颜色 token 不变：`paper-*` 中性 + `clay-500`/`clay-600` 强调（`tailwind.config.ts` 已定义）。

### Sidebar props 契约

```ts
type View = "sessions" | "agents" | "model";
Sidebar({ sm: SessionManager; view: View; setView: (v: View) => void }): JSX.Element
AgentList(): JSX.Element                  // 内部 fetch /api/agents，自管选中态（选中 → AgentEditor 显示该项）
AgentEditor(): JSX.Element                // 内部持有 selected agent name（由 AgentList 选中或 URL/state 传入）
ModelConfigPanel(): JSX.Element
```

> AgentList 与 AgentEditor 的选中态传递：P1 简单做法是 AgentEditor 自管 `selected` state，AgentList 选中时通过模块级回调或提升 state。**决定：把 `selectedAgent: string | null` 提升到 page.tsx**（与 `view` 同级），`<AgentList selected={selectedAgent} onSelect={setSelectedAgent}/>` + `<AgentEditor name={selectedAgent}/>`。避免两个兄弟组件间暗通。

修正后 page.tsx：
```tsx
const [view, setView] = useState<View>("sessions");
const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
// ...
{view === "agents" && <AgentEditor name={selectedAgent} onDeleted={() => setSelectedAgent(null)} />}
```
Sidebar 内 AgentList 通过 props 接收 selected/onSelect。

## 智能体（agents）

### 持久化

磁盘文件 `REPO_ROOT/.agents/<name>.json`（`REPO_ROOT = Path.cwd()`，见 `env.py:15`；与 `skills/` 同级策略，agent_core 直接读，不经 DB）。Shape：
```json
{
  "name": "researcher",
  "description": "代码库探索子 agent",
  "prompt": "You are a codebase researcher. ... Return only the conclusion.",
  "model": null,
  "tools": ["bash", "read_file", "write_file", "edit_file", "glob"]
}
```
- `model: null` → 继承全局模型配置（`model_config.model()`）。
- `model: "glm-4"` → 该子 agent 固定用此模型。
- `tools` 缺省（或省略字段）→ 子 agent 默认工具集 `SUB_TOOLS` 的 name 列表 `["bash","read_file","write_file","edit_file","glob"]`。
- `tools` 中的名字必须存在于 `BUILTIN_HANDLERS` ∪ MCP 工具前缀；未知名字在调度时报错（不落盘校验，调度时校验，因为 MCP 工具集是动态的）。
- name 限 `^[A-Za-z0-9_-]+$`，防路径穿越与奇异文件名。

### agent_core/agents.py（新模块，镜像 skills.py）

现状 `skills.py` 结构：`SKILLS_DIR = REPO_ROOT / "skills"`、`SKILL_REGISTRY`、`scan_skills()`、`list_skills()`、`load_skill(name)`、`_parse_frontmatter()`。

新模块签名：
```python
# agent_core/agents.py
import json, re
from pathlib import Path
from agent_core.env import REPO_ROOT

AGENTS_DIR = REPO_ROOT / ".agents"
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_DEFAULT_TOOLS = ["bash", "read_file", "write_file", "edit_file", "glob"]

def _validate_name(name: str) -> str:
    """Raise ValueError on invalid name (path traversal / empty / odd chars)."""
    if not name or not _NAME_RE.match(name):
        raise ValueError(f"invalid agent name: {name!r}")
    return name

def list_agents() -> list[dict]:
    """Return [{name, description, model, tools}] for all .agents/*.json.
    Missing dir → []. Corrupt JSON → skip (never raise)."""

def get_agent(name: str) -> dict | None:
    """Return full def {name, description, prompt, model, tools} or None."""

def save_agent(name: str, description: str, prompt: str,
               model: str | None, tools: list[str]) -> dict:
    """Validate name, mkdir .agents, write <name>.json atomically, return def."""

def delete_agent(name: str) -> bool:
    """Validate name, unlink if exists, return whether it existed."""

def scan_agents() -> str:
    """Catalog for system prompt injection, mirrors list_skills() format:
      - researcher: 代码库探索子 agent
      - writer: ...
    Empty → '(no agents defined)'."""
```

与 skills 的差异：skills 用 markdown+frontmatter，agents 用 JSON（因 `tools` 是结构化数组，JSON 更自然；`prompt` 是多行字符串，JSON 转义可接受）。`AGENTS_DIR` 不在 `env.set_workdir()` 的 mkdir 列表里——`save_agent` 自己 `AGENTS_DIR.mkdir(parents=True, exist_ok=True)`。

### task 工具 schema diff

现状（`agent_core/tools.py:424-428`）：
```python
{"name": "task",
 "description": "Launch a focused subagent. Returns only its final summary.",
 "input_schema": {"type": "object",
                  "properties": {"description": {"type": "string"}},
                  "required": ["description"]}},
```
改为：
```python
{"name": "task",
 "description": "Launch a focused subagent. Returns only its final summary. "
                "Pass agent=<name> to use a defined subagent's prompt/tools/model.",
 "input_schema": {"type": "object",
                  "properties": {"description": {"type": "string"},
                                 "agent": {"type": "string"}},
                  "required": ["description"]}},
```
`BUILTIN_HANDLERS["task"]` 仍指向 `spawn_subagent`（`tools.py:546`），签名扩展见下。

### spawn_subagent 签名 diff + 调度逻辑

现状（`agent_core/subagent.py:46`）：`def spawn_subagent(description: str) -> str`，用模块常量 `SUB_SYSTEM`、`SUB_TOOLS`，模型用 `from agent_core.env import MODEL`。

改为：
```python
def spawn_subagent(description: str, agent: str | None = None) -> str:
    from agent_core.tools import call_tool_handler, run_bash, run_edit, run_glob, run_read, run_write
    # ... 其他 run_* 按需 import

    if agent is not None:
        from agent_core.agents import get_agent
        from agent_core import model_config
        defn = get_agent(agent)
        if defn is None:
            return f"Agent not found: {agent}. Available: {scan_agents()}"
        system = defn["prompt"]
        tool_names = defn.get("tools") or _DEFAULT_TOOLS
        model = defn.get("model") or model_config.model()
    else:
        system = SUB_SYSTEM
        tool_names = [t["name"] for t in SUB_TOOLS]
        model = model_config.model()   # 见下：统一走 model_config

    tools, handlers = _resolve_toolset(tool_names)   # 从 BUILTIN_TOOLS/HANDLERS + MCP 池里按名挑
    messages = [{"role": "user", "content": description}]
    for _ in range(30):
        response = adapter.chat_create(model=model, system=system, messages=messages,
                                       tools=tools, max_tokens=8000)
        # ... 同现状的 tool_use 执行循环，handlers 用上面解析的
```

- `_resolve_toolset(names)`：从 `agent_core.tools.BUILTIN_TOOLS`/`BUILTIN_HANDLERS` 按 name 挑；MCP 工具（`mcp__*`）按需加入（P2 可先只支持 builtin，MCP 留 P3）。未知 name → 报错列表。
- **关键**：即使 `agent=None`，模型也从 `MODEL`（env 常量）改为 `model_config.model()`，这样在线切模型对子 agent 也生效。`from agent_core.env import MODEL` 的 import 可移除。
- `SUB_SYSTEM`/`SUB_TOOLS` 保留作为 ad-hoc 默认。

### prompt.py 注入 diff

现状（`agent_core/prompt.py:29-30`）：
```python
sections.append("Skills catalog:\n" + list_skills() +
                "\nUse load_skill(name) when a skill is relevant.")
```
在其后追加：
```python
from agent_core.agents import scan_agents
sections.append("Agents catalog:\n" + scan_agents() +
                "\nUse task(description=..., agent=<name>) to dispatch a defined agent.")
```
（import 放文件顶，避免每轮重 import。）

### Gateway（4 端点）

路由模式对齐 `/api/skills`（`main.py:197-199`，`@app.get` 直接 return）。

```python
# agent_gateway/main.py 追加
from agent_core import agents as agents_mod
from agent_core import model_config

@app.get("/api/agents")
async def list_agents():
    return agents_mod.list_agents()

@app.post("/api/agents")
async def create_agent(body: AgentCreate):
    try:
        return agents_mod.save_agent(body.name, body.description, body.prompt,
                                     body.model, body.tools)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/api/agents/{name}")
async def update_agent(name: str, body: AgentUpdate):
    try:
        return agents_mod.save_agent(name, body.description, body.prompt,
                                     body.model, body.tools)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/agents/{name}")
async def delete_agent(name: str):
    try:
        ok = agents_mod.delete_agent(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="agent not found")
    return {"ok": True}
```

Pydantic schemas（`agent_gateway/schemas.py` 追加）：
```python
class AgentCreate(BaseModel):
    name: str
    description: str = ""
    prompt: str = ""
    model: Optional[str] = None
    tools: list[str] = []

class AgentUpdate(BaseModel):   # 不含 name（name 在 path）
    description: str = ""
    prompt: str = ""
    model: Optional[str] = None
    tools: list[str] = []

class ModelConfig(BaseModel):
    model_id: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None   # 仅落盘，前端永不见全量
    fallback_model: Optional[str] = None
```

### Frontend（AgentEditor + lib/agents.ts）

`frontend/lib/agents.ts`（镜像 `sessions.ts` 的 REST plumbing）：
```ts
export type AgentDef = {
  name: string; description: string; prompt: string;
  model: string | null; tools: string[];
};
export async function listAgents(): Promise<AgentDef[]> { /* GET /api/agents */ }
export async function saveAgent(a: AgentDef): Promise<AgentDef> { /* POST 或 PUT */ }
export async function deleteAgent(name: string): Promise<void> { /* DELETE */ }
```
（`GATEWAY` 常量从 `sessions.ts` 复用或提取到 `lib/gateway.ts`。）

`AgentEditor({ name, onDeleted })`：
- `name === null` → 显示「+ 新建 agent」表单（空字段）。
- `name !== null` → `useEffect` fetch `/api/agents` 找到该项填充表单。
- 字段：name（新建时可编辑，编辑时只读）、description（input）、prompt（textarea，monospace）、model（input，placeholder「继承全局」）、tools（复选框，候选 = 已知工具名列表）。
- Save 按钮 → `saveAgent` → 刷新 AgentList。
- Delete 按钮 → `deleteAgent` → `onDeleted()`。
- 状态机见下。

### AgentEditor 状态机

```
states: idle | dirty | saving | error
transitions:
  edit field       → dirty
  click Save       → saving → (ok) idle + refresh list
                         → (fail) error(msg) + keep dirty
  click Delete     → confirm → saving → (ok) onDeleted()
                                → (fail) error(msg)
  switch selected  → idle (load new)
```
dirty 离开提示：切走/选别的 agent 时若 dirty，弹「丢弃改动？」（P2 可加；P1 先不拦，刷新即丢，记 R2）。

### 边界用例（agents）

| 场景 | 行为 |
|------|------|
| `.agents/` 不存在 | `list_agents()` → `[]`；`save_agent` 先 mkdir |
| `<name>.json` 损坏 | `list_agents` 跳过该项；`get_agent` → None |
| name 含 `../` 或 `/` | `save_agent`/`delete_agent` → ValueError → 400 |
| name 含中文/空格 | 同上，regex 拒绝 |
| `task(agent="unknown")` | 返回 `Agent not found: unknown. Available: ...` |
| `task(agent="x")` 且 x.tools 含未知工具名 | `_resolve_toolset` 报错，子 agent 不启动，返回错误列表 |
| `model: null` | 继承 `model_config.model()` |
| 并发写同一 name | 后写覆盖（无文件锁；镜像 skills 策略，可接受） |
| name 大小写 | `Researcher` 与 `researcher` 是两个文件（大小写敏感文件系统）；目录按 sorted 列出 |

## 模型配置（model）

### 持久化

单例配置 `REPO_ROOT/.agents/model.json`：
```json
{ "model_id": "glm-5", "base_url": "https://api.z.ai/api/openai",
  "api_key": "sk-...", "fallback_model": "glm-4" }
```
文件缺失或字段为空时回退到 env。回退矩阵：

| 字段 | 文件有值 | 文件缺/空 |
|------|---------|-----------|
| model_id | file.model_id | `env.MODEL`（`os.environ["MODEL_ID"]`，必填） |
| base_url | file.base_url | `os.getenv("OPENAI_BASE_URL")` |
| api_key | file.api_key | `os.getenv("OPENAI_API_KEY", "dummy")` |
| fallback_model | file.fallback_model | `os.getenv("FALLBACK_MODEL_ID")`（可能为 None） |

> 现状缺口：`FALLBACK_MODEL_ID` 未在 `.env.example` 文档化（pre-existing gap）。本设计把 fallback_model 提到 UI 可编辑，sidesteps 该 env var；但 env 回退路径仍读它——**附带修复：在 `.env.example` 补一行 `# FALLBACK_MODEL_ID=glm-4` 注释示例。**

### agent_core/model_config.py（新模块）

```python
# agent_core/model_config.py
import json, os, threading
from pathlib import Path
from openai import OpenAI
from agent_core.env import REPO_ROOT

_CONFIG_PATH = REPO_ROOT / ".agents" / "model.json"
_lock = threading.Lock()
_cache = {"mtime": None, "config": None}
_client_state = {"version": 0, "client": None}   # version bump = rebuild client

def _read_file() -> dict:
    """Return raw file dict or {} if missing/corrupt."""
    if not _CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

def get_config() -> dict:
    """Return effective config with env fallback. mtime-cached."""
    with _lock:
        try:
            m = _CONFIG_PATH.stat().st_mtime if _CONFIG_PATH.exists() else None
        except OSError:
            m = None
        if _cache["mtime"] == m and _cache["config"] is not None:
            return _cache["config"]
        f = _read_file()
        cfg = {
            "model_id": f.get("model_id") or os.environ["MODEL_ID"],
            "base_url": f.get("base_url") or os.getenv("OPENAI_BASE_URL"),
            "api_key":  f.get("api_key")  or os.getenv("OPENAI_API_KEY", "dummy"),
            "fallback_model": f.get("fallback_model") or os.getenv("FALLBACK_MODEL_ID"),
        }
        _cache["mtime"] = m
        _cache["config"] = cfg
        return cfg

def model() -> str:
    return get_config()["model_id"]

def fallback() -> str | None:
    return get_config()["fallback_model"]

def client() -> OpenAI:
    """Return cached OpenAI client; rebuild only when base_url/api_key change."""
    cfg = get_config()
    sig = (cfg["base_url"], cfg["api_key"])
    with _lock:
        if _client_state["client"] is not None and _client_state.get("sig") == sig:
            return _client_state["client"]
        c = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
        _client_state["client"] = c
        _client_state["sig"] = sig
        _client_state["version"] += 1
        return c

def refresh() -> None:
    """Invalidate cache (call after gateway writes model.json)."""
    with _lock:
        _cache["mtime"] = None
        _cache["config"] = None
        # 不清 _client_state：client() 会因 sig 变化重建；sig 不变则复用
```

- `model()` 每轮读 mtime 缓存 → 文件变了下一轮自动生效（即使不调 refresh）。
- `client()` 仅在 base_url/api_key 变化时重建 OpenAI 实例（避免每轮新建连接池）。
- `refresh()` 供 gateway `PUT /api/models` 后强制失效（mtime 跨秒精度兜底）。

### adapter.py diff

现状（`adapter.py:5`）：`from agent_core.env import client`；`chat_create` 内 `client.chat.completions.create(...)`。

改为：
```python
# adapter.py 顶
from agent_core import model_config
# 删除: from agent_core.env import client

# chat_create 内，把 client.chat.completions.create(...) 两处（非流 + 流）改为：
resp = model_config.client().chat.completions.create(**kwargs)
```
- **关键不破坏**：测试 monkeypatch 的是 `agent_core.adapter.chat_create` 整个函数，不是 `client`。`chat_create` 仍是同一函数对象，patch 仍生效。`model_config` 只提供 client/model，不替换 chat_create。✅
- 测试用 `MODEL_ID=test-model OPENAI_API_KEY=dummy` 走 env 回退路径（`model_config.get_config()` 读不到 `.agents/model.json` → 全部回退 env），与现状等价。

### loop.py / recovery.py diff

现状：`recovery.py:16` `self.current_model = PRIMARY_MODEL`；`loop.py:28` `model=state.current_model`；`recovery.py:58` 529 fallback `state.current_model = FALLBACK_MODEL`。

改动：
1. `recovery.py`：`from agent_core import model_config`；`RecoveryState.__init__` 改 `self.current_model = model_config.model()`。
2. `loop.py` `agent_loop` while 循环顶部（每轮）追加：`state.current_model = model_config.model()`——**在线切模型下一轮生效**，避免会话锁死旧模型。
3. `recovery.py:58` 529 fallback 改 `state.current_model = model_config.fallback()`（若 fallback 为 None 则不切，维持现状逻辑）。
4. `recovery.py` 顶 import 把 `FALLBACK_MODEL, PRIMARY_MODEL` 去掉（改用 model_config）；保留其它 env 常量。

> 在跑会话的当前轮已发出的请求不受影响（下一轮才读 model_config.model()）。RecoveryState 每轮重读，不锁死。✅

### Gateway（2 端点）

```python
@app.get("/api/models")
async def get_models():
    cfg = model_config.get_config()
    key = cfg["api_key"]
    masked = (f"sk-***{key[-4:]}" if key and key.startswith("sk-") and len(key) >= 4
              else ("***" if key else None))
    return {
        "model_id": cfg["model_id"],
        "base_url": cfg["base_url"],
        "api_key_masked": masked,
        "fallback_model": cfg["fallback_model"],
    }

@app.put("/api/models")
async def update_models(body: ModelConfig):
    # body.api_key 为空字符串/null → 保留磁盘现有 key（不擦）
    existing = model_config._read_file()
    new = {
        "model_id": body.model_id,
        "base_url": body.base_url,
        "api_key": body.api_key if body.api_key else existing.get("api_key"),
        "fallback_model": body.fallback_model,
    }
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(new, indent=2))
    model_config.refresh()
    return {"ok": True}
```

- 掩码：`sk-***<后4位>`；无 `sk-` 前缀的 key → `***`；无 key → `null`。前端永不见全量。
- `PUT` 时 `api_key` 空 → 保留磁盘旧 key（前端编辑其它字段时不误擦 key）。
- 写后 `model_config.refresh()` 使下一轮生效。

### Frontend（ModelConfigPanel + lib/models.ts）

`frontend/lib/models.ts`：
```ts
export type ModelConfigView = {
  model_id: string; base_url: string | null;
  api_key_masked: string | null; fallback_model: string | null;
};
export async function getModelConfig(): Promise<ModelConfigView> { /* GET */ }
export async function saveModelConfig(body: {
  model_id: string; base_url: string | null;
  api_key: string | null;        // 空字符串=不改，非空=新 key
  fallback_model: string | null;
}): Promise<void> { /* PUT */ }
```

`ModelConfigPanel()`：
- `useEffect` fetch `/api/models` 填充。
- 字段：model_id（input）、base_url（input）、api_key（显示掩码 + 旁边「输入新 key 以更改」空输入框）、fallback_model（input）。
- Save → `saveModelConfig`（api_key 输入框为空则传空字符串=保留）。
- 保存成功提示「下一轮生效」。
- 状态机同 AgentEditor（idle/dirty/saving/error）。

### 边界用例（model）

| 场景 | 行为 |
|------|------|
| `.agents/model.json` 不存在 | 全部回退 env |
| model.json 字段空字符串 | 视为缺省，回退 env |
| model.json 损坏 | `_read_file` → {}，回退 env（不 raise） |
| `api_key` 为 null/空 | `get_config` 回退 `OPENAI_API_KEY` env |
| 在线改 model_id | 下一轮 `agent_loop` 读 `model_config.model()` 生效 |
| 在线改 base_url | `client()` 检测 sig 变化 → 重建 OpenAI 实例 |
| 改 api_key | 同上，重建实例 |
| 529 fallback 且 fallback_model 为 null | 不切模型，维持现状重试逻辑 |
| 并发：gateway 写 + loop 读 | mtime 缓存 + lock；最坏多读一次旧值，无损坏 |
| `PUT /api/models` api_key 空 | 保留磁盘旧 key |
| 前端刷新后看 api_key | 永远是掩码，不见全量 |

## 会话栏

行为不变。`SessionList` 留在侧边栏「会话」导航项下，右侧为 `ChatPanel`。无后端改动。仅 `SessionList` 从「常驻 Sidebar」改为「view==="sessions" 时显示」。

## 实施分期

### P1 — 前端外壳
**文件：**
- Modify: `frontend/app/page.tsx`（加 `view`/`selectedAgent` state + 右侧 switch）
- Modify: `frontend/components/Sidebar.tsx`（加导航项 + 条件列表 + skills/mcp 折叠下移）
- Create: `frontend/components/AgentEditor.tsx`（占位：「智能体配置（P2）」）
- Create: `frontend/components/ModelConfigPanel.tsx`（占位：「模型配置（P3）」）
- Create: `frontend/components/AgentList.tsx`（占位）

**验证：**
- `npx vitest run` 全绿（现有 reducer/ChatPanel 测试不回归；Sidebar 若有快照需更新）。
- `npm run build` 通过。
- 手动冒烟：三栏切换正常，会话流程不回归，skills/mcp 折叠展开正常。

### P2 — 智能体
**文件：**
- Create: `agent_core/agents.py`
- Modify: `agent_core/tools.py`（task schema 加 `agent`）
- Modify: `agent_core/subagent.py`（`spawn_subagent(description, agent=None)` + `_resolve_toolset`）
- Modify: `agent_core/prompt.py`（注入 agents catalog）
- Modify: `agent_gateway/main.py`（4 端点）
- Modify: `agent_gateway/schemas.py`（AgentCreate/AgentUpdate）
- Modify: `frontend/components/AgentEditor.tsx`（实装表单）
- Modify: `frontend/components/AgentList.tsx`（实装列表）
- Create: `frontend/lib/agents.ts`
- Modify: `frontend/components/Sidebar.tsx`（AgentList 接入 selected/onSelect）
- Modify: `.gitignore`（加 `.agents/`）
- Create: `tests/test_agents.py`、`tests/test_agent_dispatch.py`、`tests/test_gateway_agents.py`

**验证：**
- `MODEL_ID=test-model OPENAI_API_KEY=dummy python -m pytest tests/ -q` 全绿。
- `npx vitest run` 全绿（加 AgentEditor.test.tsx）。
- 冒烟：前端新建 agent → 保存 → 主会话里 `task(agent=...)` 调用成功，子 agent 用 def.prompt/tools/model。

### P3 — 模型
**文件：**
- Create: `agent_core/model_config.py`
- Modify: `agent_core/adapter.py`（`client` → `model_config.client()`）
- Modify: `agent_core/loop.py`（每轮 `state.current_model = model_config.model()`）
- Modify: `agent_core/recovery.py`（init + 529 fallback 走 model_config）
- Modify: `agent_core/subagent.py`（ad-hoc 也走 `model_config.model()`）
- Modify: `agent_gateway/main.py`（2 端点）
- Modify: `agent_gateway/schemas.py`（ModelConfig）
- Modify: `frontend/components/ModelConfigPanel.tsx`（实装）
- Create: `frontend/lib/models.ts`
- Modify: `.env.example`（补 `FALLBACK_MODEL_ID` 注释）
- Create: `tests/test_model_config.py`、`tests/test_gateway_models.py`

**验证：**
- pytest 全绿（含 model_config env 回退 + 掩码测试）。
- vitest 全绿（加 ModelConfigPanel.test.tsx）。
- 冒烟：前端改 model_id → 下一轮用新模型（看 gateway 日志）；改 api_key → 掩码显示，下一轮生效；adapter monkeypatch 测试仍绿。

每期结束：`make test`（= pytest + vitest）全绿 + gateway 重建起服冒烟。

## 测试清单

### Python（pytest，recipe: `MODEL_ID=test-model OPENAI_API_KEY=dummy`，monkeypatch `agent_core.adapter.chat_create`）

**`tests/test_agents.py`**（新）
- `test_list_agents_empty`（无 `.agents/` → `[]`）
- `test_save_and_get_agent`（round-trip）
- `test_save_agent_invalid_name`（`../x`、`a/b`、`中文`、空 → ValueError）
- `test_delete_agent_missing`（→ False）
- `test_list_agents_skips_corrupt_json`（写一损坏文件 → 跳过，不 raise）
- `test_scan_agents_format`（输出含 `- name: desc` 行；空 → `(no agents defined)`）

**`tests/test_agent_dispatch.py`**（新）
- `test_task_with_agent_uses_def_prompt`（scripted chat_create 捕获 `system=` 参数 = def.prompt）
- `test_task_with_agent_uses_def_tools`（捕获 `tools=` = def.tools 对应 schema）
- `test_task_with_agent_model_override`（def.model="x" → chat_create model="x"）
- `test_task_with_agent_model_null_inherits_global`（def.model=None → chat_create model=model_config.model()）
- `test_task_agent_not_found`（→ 返回 "Agent not found: ... Available: ..."）
- `test_task_no_agent_unchanged`（agent=None → 用 SUB_SYSTEM/SUB_TOOLS，行为同现状）

**`tests/test_model_config.py`**（新）
- `test_get_config_env_fallback`（无文件 → 等于 env 值）
- `test_get_config_file_override`（写文件 → 用文件值）
- `test_get_config_partial_file`（文件只含 model_id → 其余回退 env）
- `test_client_rebuild_on_base_url_change`（改 base_url → 新 OpenAI 实例）
- `test_client_reuse_on_model_only_change`（只改 model_id → 同一 client 实例）
- `test_refresh_invalidates`（refresh 后下次 get_config 重读文件）
- `test_corrupt_file_falls_back`（损坏 JSON → 回退 env，不 raise）

**`tests/test_gateway_agents.py`**（新，TestClient）
- `test_list_agents_route`（GET → 200 + 列表）
- `test_create_agent_route`（POST → 200；非法 name → 400）
- `test_update_agent_route`（PUT → 200）
- `test_delete_agent_route`（DELETE → 200；不存在 → 404）

**`tests/test_gateway_models.py`**（新，TestClient）
- `test_get_models_masks_api_key`（GET → api_key_masked = `sk-***<后4>`，不见全量）
- `test_get_models_no_key`（无 key → api_key_masked = null）
- `test_update_models_writes_file`（PUT → 文件落盘）
- `test_update_models_empty_api_key_keeps_existing`（PUT api_key="" → 磁盘旧 key 保留）
- `test_update_models_refreshes`（PUT 后 model_config 缓存失效）

### Frontend（vitest，jsdom，mock fetch）

**`frontend/components/AgentEditor.test.tsx`**（新）
- 渲染空表单（name=null → 新建）
- 渲染已存在 agent（fetch mock → 字段填充）
- Save → POST 调用 + 列表刷新回调
- Delete → confirm → DELETE 调用
- 非法 name → 错误提示

**`frontend/components/ModelConfigPanel.test.tsx`**（新）
- 渲染掩码 api_key
- Save → PUT 调用，api_key 空时不传
- 成功提示「下一轮生效」

**`frontend/components/Sidebar.test.tsx`**（新或扩展）
- 三栏导航点击 → setView 调用
- view="sessions" → SessionList 可见
- view="agents" → AgentList 可见
- skills/mcp 折叠区展开/收起

## 风险

- **R1（P1 会话断流）**：切走会话卸载 ChatPanel 断 WS/SSE。`last_seq` resume + 三级 replay 兜底，重连不丢消息。若体验差，改 `hidden` 隐藏不卸载。
- **R2（P1 AgentEditor dirty 丢失）**：切走时未保存改动直接丢。P1 可接受；P2 加丢弃确认。
- **adapter 热路径改动**：`chat_create` 改用 `model_config.client()` 须保证测试 monkeypatch `agent_core.adapter.chat_create` 仍生效——model_config 只提供 client/model，不替换 chat_create 函数对象，patch 仍生效。测试走 env 回退路径，不依赖磁盘文件。✅
- **在线切模型对在跑会话**：下一轮生效；当前轮已发出的请求不受影响。`RecoveryState.current_model` 每轮从 `model_config.model()` 重读，避免会话锁死旧模型。
- **`.agents/` 路径穿越**：name 严格校验 `^[A-Za-z0-9_-]+$`，拒绝 `../`、`/`、中文、空格。
- **API key 落盘**：`.agents/` 加入 `.gitignore`（P2 一并加），避免 `model.json` 泄露 key。前端永不见掩码。
- **`.agents/model.json` 与 `.agents/<name>.json` 同目录**：`list_agents` 只读 `*.json` 但会误读 `model.json`。**决定：`list_agents` 显式排除 `model.json`**（或把模型配置放 `.agents/model.json` 而 agent 定义放 `.agents/defs/<name>.json`）。选前者更简：`list_agents` 跳过名为 `model` 的文件。
- **`FALLBACK_MODEL_ID` 未文档化**：pre-existing gap；P3 在 `.env.example` 补注释，并在 UI 暴露 fallback_model。
- **client 全局单例 vs 多 workdir**：`model_config.client()` 是进程级单例，不随 session workdir 变。模型配置是全局单例（Non-goals 明确），故正确。env.py 现状 `client` 也是进程级，行为一致。
- **子 agent tools 含 MCP 工具**：P2 `_resolve_toolset` 先只支持 builtin；MCP 工具按名调度留 P3（因 MCP 池是 per-session 动态的，子 agent 无 session 上下文）。

## 附录：现状基线签名（recon 捕获，供实施对照）

```
agent_core/env.py:15     REPO_ROOT = Path.cwd()
agent_core/env.py:37-40  client = OpenAI(base_url=os.getenv("OPENAI_BASE_URL"), api_key=os.getenv("OPENAI_API_KEY","dummy"))
agent_core/env.py:42     MODEL = os.environ["MODEL_ID"]
agent_core/env.py:44     PRIMARY_MODEL = MODEL
agent_core/env.py:46     FALLBACK_MODEL = os.getenv("FALLBACK_MODEL_ID")
agent_core/adapter.py:5  from agent_core.env import client
agent_core/adapter.py:118 chat_create(model, system=None, messages=None, tools=None, max_tokens=8000, stream=False, events=None)
agent_core/recovery.py:16 self.current_model = PRIMARY_MODEL
agent_core/recovery.py:58 if state.consecutive_529 >= MAX_CONSECUTIVE_529 and FALLBACK_MODEL: state.current_model = FALLBACK_MODEL
agent_core/loop.py:27-28  adapter.chat_create(model=state.current_model, ...)
agent_core/subagent.py:10 SUB_SYSTEM = f"You are a coding subagent at {workdir()}. ..."
agent_core/subagent.py:16 SUB_TOOLS = [bash, read_file, write_file, edit_file, glob]
agent_core/subagent.py:46 def spawn_subagent(description: str) -> str
agent_core/subagent.py:55   adapter.chat_create(model=MODEL, system=SUB_SYSTEM, messages=messages, tools=SUB_TOOLS, max_tokens=8000)
agent_core/tools.py:424-428 task schema: {description: string} required:[description]
agent_core/tools.py:546  BUILTIN_HANDLERS["task"] = spawn_subagent
agent_core/skills.py:6   SKILLS_DIR = REPO_ROOT / "skills"
agent_core/skills.py:22  scan_skills() -> list (clears registry, scans *.dir/SKILL.md)
agent_core/skills.py:43  list_skills() -> str ("- name: desc" lines)
agent_core/prompt.py:29  sections.append("Skills catalog:\n" + list_skills() + ...)
agent_gateway/main.py:197 @app.get("/api/skills") → return code.scan_skills()
agent_gateway/schemas.py  CreateSession/UserMessage/PermissionResponse/EventFrame (pydantic BaseModel)
frontend/app/page.tsx:11 Page() → <Sidebar sm={sm}/> + <main><ChatPanel sessionId={sm.currentId}/></main>
frontend/components/Sidebar.tsx:21 Sidebar({sm}) — owns SessionList + skills/mcp tabs
frontend/components/Sidebar.tsx:95 SessionList({sm}) — uses sm.{sessions,currentId,switchTo,newSession,removeSession}
frontend/lib/sessions.ts:9 GATEWAY = NEXT_PUBLIC_GATEWAY_URL || window.location.origin || localhost:8000
frontend/lib/useSessionManager.ts:25 SessionManager interface {sessions, currentId, switchTo, newSession, removeSession}
tailwind.config.ts       paper-* neutrals + clay-* accent (clay-500 primary, clay-600 replying)
.gitignore               NO .agents/ / .memory/ / .claude/ entry
.env.example             NO FALLBACK_MODEL_ID documented
Makefile                 make test / test-core / test-frontend (MODEL_ID=test-model OPENAI_API_KEY=dummy)
```
