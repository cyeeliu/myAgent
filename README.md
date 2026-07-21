# myAgent

> 一个自研 Agent，跑在 **OpenAI 兼容的 Chat Completions API** 上。单 loop 串起 20+ 子系统，配 FastAPI 网关 + Vite 前端 + Postgres/Redis，docker-compose 一键起。

![Python](https://img.shields.io/badge/python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688)
![React](https://img.shields.io/badge/React-Vite-61DAFB)
![Docker](https://img.shields.io/badge/Docker-compose-2496ED)
![License](https://img.shields.io/badge/license-Apache--2.0-blue)

---

## 简介

myAgent 把现代 coding agent 那套机器（工具调度、权限、hooks、todo、子代理、技能、上下文压缩、记忆、规划模式、cron、团队、worktree、MCP …）重新实现了一遍，但对接的是 **OpenAI 兼容的 Chat Completions** 端点（OpenAI / MiniMax / GLM / Kimi / DeepSeek 都能跑）。线格式是 OpenAI，但 agent 内部全程说 **Anthropic 风格的 content block**（`text` / `tool_use` / `tool_result`）——只有 `agent_core/adapter.py` 知道 OpenAI 格式，换 provider 只动这一个文件。

三个组件，docker-compose 编排：

| 组件 | 作用 |
|------|------|
| `agent_core/` + `code.py` | agent 核心，24 个模块（一个子系统一个模块），`code.py` 是向后兼容的 re-export facade |
| `agent_gateway/` | FastAPI 网关，方法路由 WS `/ws` + SSE + REST，Postgres 持久化、Redis 热事件 |
| `frontend_vite/` | React + Vite + Tailwind 前端，nginx 出静态 dist |

---

## ✨ Features

- **单 loop agent** — `agent_loop` 一轮：注入 cron/后台通知 → 上下文预算 → 重建工具池 → 调 LLM → 执行 tool_use → PostToolUse → 重复
- **权限系统** — per-tool `allow`/`ask`/`deny` 策略，硬编码安全兜底，用户授权弹窗
- **规划模式** — 只读探索 + `exit_plan_mode` 审批 gate + bash 只读门 + 跨重连持久化
- **hooks** — `UserPromptSubmit` / `PreToolUse` / `PostToolUse` / `Stop`
- **todos** — 任务清单，每 3 轮提醒
- **子代理 / 团队** — `task` 派发、`start_team` 集群、MessageBus + ProtocolState（计划审批 / shutdown 握手）
- **技能** — `skills/<name>/SKILL.md`，YAML frontmatter + markdown，是 prompt 不是代码
- **上下文压缩** — `tool_result_budget` / `snip_compact` / `micro_compact` / `compact_history` / `reactive_compact`，`record` 与 `context_messages` 双列表
- **记忆** — `.memory/*.md` + `MEMORY.md` 索引，LLM 选相关记忆注入，正则过滤敏感内容
- **cron / 后台任务** — 定时 prompt、慢操作 fork 到后台
- **worktrees** — git worktree 隔离
- **MCP** — 连接 MCP server，工具前缀 `mcp__{server}__{tool}`
- **恢复** — prompt-too-long 自适应压缩、529/overload fallback model
- **网关** — 三层 replay（live token → message record → Postgres）、WS resume `?last_seq=N`、30 分钟空闲驱逐
- **沙箱** — bubblewrap 包 bash，会话 workdir 外不可见

---

## 🏛 Architecture

```
                ┌──────────────┐
   浏览器 ──WS──▶   nginx :80   │
                └──────┬───────┘
                       │ /api/* → gateway:8000 (WS upgrade)
                       │ /*     → frontend:3000
              ┌────────┴────────┐
              │  agent_gateway   │  FastAPI
              │  sessions.py     │  一会话一 worker 线程跑 agent_loop
              │  pipe.py         │  EventPipe / ChatStreamPipe / ContextStore
              │  db.py           │  Postgres (chat_record + llm_context + mode)
              └────────┬────────┘
                       │ import code (agent_core)
              ┌────────┴────────┐
              │   agent_core     │  24 模块
              │  loop.agent_loop │  ← 心脏
              │  adapter.py      │  ← 唯一懂 OpenAI 格式的地方
              └────────┬────────┘
                       │
                 ┌─────┴─────┐
                 │ Postgres  │  持久 chat_record / llm_context / mode
                 │  Redis    │  热事件流 (24h TTL)
                 └───────────┘
```

**三层 replay**：`live:{sid}`（token 级，24h）→ 过期 → 从 `chat:{sid}`（消息级 record）合成 → 过期 → 从 Postgres `chat_record` 重放。LLM 只读 `session.context_messages`，不读 Redis；Redis 纯为 replay/热恢复，Postgres 是持久真相。

**Chat record vs LLM context**：`Session` 维护两条消息列表——`record`（只追加、永不压缩、持久真相）和 `context_messages`（LLM 实际看的、可压缩）。压缩只动 `context_messages`，绝不碰 `record`。

### agent_core —— 24 模块，单 loop

```
env → blocks → adapter → session → skills → tasks → worktrees → bus → permissions → hooks →
recovery → compaction → background → subagent → teammates → mcp → memory →
prompt → cron → tools → context → loop → cli
```

- `loop.py: agent_loop` — 心脏。每轮：注入 cron/后台通知 → 每 3 轮 todo 提醒 → `prepare_context` 上下文预算 → 重建工具池 → `call_llm` → 逐个执行 `tool_use`（`compact` 短路、`PreToolUse` 可拦、慢操作 fork 后台、否则跑 handler + `PostToolUse`）→ 无 tool_use 则停。
- `session.py: Session` — 双消息列表（见上）+ `context` 侧状态 + `append_both`。
- `context.py: prepare_context` — 每轮顺序：`tool_result_budget` → `snip_compact` → `micro_compact` → `compact_history`（仍超 50k 才动）→ `reactive_compact`（prompt-too-long 兜底）。只动 `context_messages`。
- `tools.py` — `BUILTIN_TOOLS`（schema）+ `BUILTIN_HANDLERS`（handler）平行表；`mcp.py: assemble_tool_pool` 合并 MCP 工具（前缀 `mcp__{server}__{tool}`），plan 模式裁剪。
- `hooks.py: check_permission` — 唯一权限 gate；`permissions.py` — 策略存储；`memory.py` — `.memory` 持久记忆 + 正则过滤；`prompt.py` — 每轮重组系统 prompt；`adapter.py` — 唯一懂 OpenAI 格式，末尾两遍 tool/tool_call 一致性修复。
- 循环依赖（tools↔teammates/subagent/mcp、hooks↔tools）靠函数内 deferred import 打断。`chat_create` 全走 `adapter.chat_create(...)` 以便测试 monkeypatch 传播。

### agent_gateway —— FastAPI

- `sessions.py` — `SessionManager` + `GatewaySession`，一会话一 worker 线程跑 `agent_loop`；`_run_turn` finally 持久化 chat_record + llm_context + ctx 快照；`synthesize_frames(record)` 从 append-only record 重建 token 级 replay 帧；`_build` 从 Postgres 按需 hydrate，收 `mode` 参数恢复 plan_mode。
- `pipe.py` — 三管道各带 Redis + 内存实现：`EventPipe`（token 级，24h）、`ChatStreamPipe`（消息级 record）、`ContextStore`（LLM context 快照）。
- `db.py` — psycopg3 连接池，`sessions` 表 `chat_record JSONB` + `llm_context JSONB` 双列（带 `history`→`chat_record` 迁移）；`DATABASE_URL` 不设降级 no-op。
- `gateway_push/wire.py` — agent 事件 → jiuwenswarm 点点事件（`token→chat.delta`、`tool_start→chat.tool_call`、`tool_result→chat.tool_result`、`done→chat.final{}`、`user→chat.user`、`error→chat.error`）。`done` payload 故意是 `{}`，文本走 `chat.delta`。
- `common/e2a/agent_compat.py` — ~30 个 WS method 派发；`web_connect.py` accept 即发 `connection.ack`，首个 `chat.send` 绑事件 drain。
- `main.py` file-api — `/file-api/list-files`、`/file-api/file-content?encoding=auto`（utf-8→gbk→…→latin-1 嗅探，不 500）、`/file-api/rebuild-agent-data` 写 `agent-data.json` + 种 skills/.memory。

### frontend_vite / nginx

- React + Vite + Tailwind，nginx 出静态 dist。`useWebSocket.ts` 是事件→store reducer；`AgentPanel` 走 `/file-api/rebuild-agent-data`；`historyRestore.ts` 解析 `history.json`（assistant 记录需 `event_type` + 字符串 `content` 否则丢）；`featureFlags.ts` 隐藏无后端的面板。
- `nginx.conf` — `:80` 唯一入口，`/api/` → gateway:8000（WS upgrade、`proxy_buffering off`、1h 超时），`/` → frontend:3000。

---

## 🚀 Quick Start

```bash
git clone <repo> && cd myAgent
cp .env.example .env          # 填入 OPENAI_API_KEY + MODEL_ID
docker compose up --build     # nginx :80 是唯一公开入口
```

浏览器打开 `http://localhost`，新建会话开聊。

> `MODEL_ID` 必填。`docker compose` 会自动设 `DATABASE_URL` / `REDIS_URL` 指向 compose 里的 db/redis 服务。

---

## 🛠 Local Development

**只跑核心**（无 DB/Redis，降级到内存）：

```bash
cp .env.example .env          # 填 API key + MODEL_ID
source .venv/bin/activate     # venv 需自建：python -m venv .venv && pip install -r requirements.txt
python code.py                # 交互 REPL，输问题回车发送，q 退出
```

**只跑网关**（本地，无 docker）：

```bash
uvicorn agent_gateway.main:app --host 0.0.0.0 --port 8000
```

**改了 agent_core / agent_gateway 代码**：`agent_core/`、`agent_gateway/`、`code.py` 是 `:ro` bind-mount，`docker compose restart gateway` 即生效，不用 rebuild。**改了前端**要 rebuild：`docker compose up --build frontend`。

---

## ⚙️ Configuration

### 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `OPENAI_API_KEY` | ✅ | API key |
| `MODEL_ID` | ✅ | 模型 id |
| `OPENAI_BASE_URL` | | 非 OpenAI provider 的 base url |
| `FALLBACK_MODEL_ID` | | 529/overload 后切换的备用模型 |
| `DATABASE_URL` | | Postgres，不设降级到内存（重启丢历史） |
| `REDIS_URL` | | Redis 热事件流，不设用进程内队列 |
| `AUTO_COMPACT_WINDOW` | | 上下文预算（token），默认 128000 |
| `AGENT_DEBUG` | | 1 开网关调试日志 |
| `SANDBOX` | | 1/0 强制开关 bubblewrap bash 沙箱 |

### 兼容 provider

| Provider | `MODEL_ID` | `OPENAI_BASE_URL` |
|----------|-----------|-------------------|
| OpenAI | `gpt-4o` | （默认） |
| GLM (智谱) | `glm-5` | `https://api.z.ai/api/openai` |
| MiniMax | `MiniMax-M2.5` | `https://api.minimax.io/v1` |
| Kimi (月之暗面) | `kimi-k2.5` | `https://api.moonshot.ai/v1` |
| DeepSeek | `deepseek-chat` | `https://api.deepseek.com/v1` |

国内端点见 `.env.example`。

---

## 📁 Project Structure

```
myAgent/
├── agent_core/            # agent 核心，24 模块
│   ├── adapter.py         # OpenAI 兼容适配（唯一懂 OpenAI 格式）
│   ├── loop.py            # agent_loop — 心脏
│   ├── session.py         # Session（record + context_messages 双列表）
│   ├── context.py         # 上下文预算 / 压缩
│   ├── tools.py           # BUILTIN_TOOLS + BUILTIN_HANDLERS（平行表）
│   ├── hooks.py           # check_permission gate + hooks
│   ├── permissions.py     # per-tool 策略存储
│   ├── memory.py          # .memory 持久记忆 + 正则过滤
│   ├── prompt.py          # 系统 prompt 组装
│   ├── mcp.py             # MCP + assemble_tool_pool（plan 模式裁剪）
│   ├── teammates.py       # 团队 / 子代理
│   ├── compaction.py      # 压缩策略
│   └── ...
├── agent_gateway/         # FastAPI 网关
│   ├── sessions.py        # SessionManager + GatewaySession
│   ├── pipe.py            # EventPipe / ChatStreamPipe / ContextStore
│   ├── db.py              # psycopg3 + 连接池
│   ├── gateway_push/wire.py  # agent 事件 → jiuwenswarm 事件
│   └── common/e2a/agent_compat.py  # ~30 个 WS method 派发
├── frontend_vite/         # React + Vite + Tailwind
├── code.py                # re-export facade（import code / python code.py 都能用）
├── docker-compose.yml     # nginx + gateway + frontend + postgres + redis
```

---

## 🔐 Permission System

- **策略文件**：`workspace_dir()/.permissions/policy.json`，shape `{default, ask_on_overwrite, enabled, memory_forbidden:{enabled,pattern}, tools:{name:level}}`，默认 `bash`/`write_file`/`edit_file` = `ask`。
- **唯一 gate**：`check_permission`（`hooks.py`）每个工具调用一次——策略层（`deny` 拒 / `ask` 弹窗）→ 硬编码兜底（deny-list bash、路径逃逸、覆写确认）。
- **记忆过滤**：`memory_forbidden` 正则过滤记忆召回 + 抽取，敏感内容不进 catalog、不落盘。
- **前端**：配置信息 → 安全配置面板，per-tool allow/ask/deny 表 + 总开关 + 记忆过滤正则。
- **策略存储**：`agent_core/permissions.py`，落 `workspace_dir()/.permissions/policy.json`（跨会话共享）。shape `{default, ask_on_overwrite, enabled, memory_forbidden:{enabled, pattern}, tools:{name: "allow"|"ask"|"deny"}}`，seed `bash`/`write_file`/`edit_file`=`ask`、`default`=`allow`、`enabled`=`True`。API：`get_policy`/`decide`/`set_tool_level`/`delete_tool`/`ask_on_overwrite`/`is_enabled`/`set_enabled`/`get_memory_forbidden`/`set_memory_forbidden`/`matches_forbidden`。
- **gate 顺序**：`check_permission`（`hooks.py`）每个 tool_use 调一次——策略层（`is_enabled()` → `decide(name)` → `deny` 硬拒 / `ask` 弹 `permission_request`）**再**硬编码兜底（bash `DENY_LIST`/`DESTRUCTIVE`、`safe_path` 路径逃逸、`ask_on_overwrite` 覆写确认、mcp `deploy` ask）。兜底总跑——`allow` 策略也绕不过 deny-list bash / 路径逃逸。
- **`_ask` request_id 闭环（load-bearing）**：`hooks._ask` 自己生成 `request_id = uuid().hex[:12]`，**先**经 `permission.resolver(block, request_id)` 注册 future，**再** emit 带 `request_id` 的 `permission_request`，**再** block。不能用 `permission.request(block)`——`FuturePermission` 内部 uuid 没发给客户端，`gs.grant` 找不到 pending future，Allow 点击无效。
- **记忆过滤**：`memory.select_relevant_memories` / `extract_memories` 跳过 `name+description+body` 命中 `matches_forbidden` 的记忆。正则按 pattern 字符串缓存；非法正则缓存为 `None` 返回 `False`——永不抛（记忆过滤不能搞坏 loop）。
- **WS 端点 + 面板**：`permissions.tools.get/update/delete`（schema 在 `agent_gateway/common/schema/message.py`，派发在 `agent_compat.py`）驱动前端 `ConfigPanel/PermissionsToolsEditor`。安全配置 tab 挂在 `"permissions"` group 的 `afterTable`，要求 `_config_get` 返回 `permissions_enabled`/`memory_forbidden_enabled`/`memory_forbidden_description`——缺 key tab 空；`_config_set` 经 `permissions.set_enabled`/`set_memory_forbidden` 写回。`permission_request` 在 `wire.py` 映射成 `chat.ask_user_question` 并显式带 `options:[Allow,Deny]`（`UserQuestionModal` 渲染 `question.options.map`，缺 options 崩）。答案走 `chat.send{source:"permission_interrupt", request_id, answers}` → `gs.grant`。

---

## 📋 Plan Mode

对话模式选「规划模式」（`agent.plan`，默认）：

1. **只读探索** — 工具池裁成只读白名单 + `exit_plan_mode`，隐藏 `write_file`/`edit_file`/所有编排工具/MCP；bash 变更命令（`rm`/`git reset`/`>`/`npm install`…）被 deny。
2. **提交方案** — agent 调 `exit_plan_mode(plan=...)`，弹审批框（批准并执行 / 拒绝），方案写 `session_dir()/plan.md`。
3. **批准** — pop `plan_mode`，同 turn 下一次循环恢复全工具，立即执行；DB mode 落库为 `agent.fast`，重连不重入规划模式。
4. **拒绝** — 留在规划模式，改方案重提。

**机制细节**：

- **Flag**：`agent_compat.py` `chat.send` 在 `req.mode == Mode.PLAN` 时设 `gs.agent.context["plan_mode"]=True`，否则 pop。flag 须跨轮存活——靠 `update_context` merge（见下）。
- **工具裁剪**：`mcp.assemble_tool_pool(context)` 在 `plan_mode` 时裁成 `_PLAN_MODE_ALLOWED`（只读工具 + `exit_plan_mode`）。`ask_user` 和所有写/编排工具（`write_file`/`edit_file`/worktree 变更/cron 创建/teammates/任务图写/`task`/`connect_mcp`/团队协议）隐藏，**所有 MCP 工具隐藏**（没法判断只读）。`bash` 保留供探索（`ls`/`git status`/`cat`），由下面的 bash 只读门兜底。`loop.py` 两处调用点都传 `context`。
- **审批 gate**：`exit_plan_mode` builtin（`tools.py` schema + `run_exit_plan_mode` handler）复用 `ask_user` 事件管道（`_ask_user_via_gateway` 共享 helper）弹 UserQuestionModal，选项 `批准并执行`/`拒绝`。**无 wire.py / 前端改动**——同事件 kind、同 `ask_resolver`、同 `chat.send{source:ask_user_interrupt}` 答路径。批准 → pop `plan_mode`，同 turn 下一次循环 `assemble_tool_pool` 无 `plan_mode` → 全工具恢复，立即执行，不等下一条用户消息。拒绝 → 留在 plan 模式。
- **bash 只读门**：`check_permission` 在 plan 模式按 `_PLAN_MODE_BASH_DENY`（`rm`/`mv`/`cp`/`mkdir`/`chmod`/重定向/`git add|commit|reset|checkout|…`/`npm install`/`pip install`/`docker`/`kill`/`sed -i`/…）deny 变更命令。子串匹配，best-effort，直接 deny（不 ask）——符合只读语义。
- **持久化**：`_build`（`sessions.py`）收 `mode` 参数，`agent.plan` 时设 `plan_mode`——idle 驱逐 / 副本崩溃后 rehydrate 仍只读。`get_or_hydrate`/`create` 传 `row.get("mode")`。`_run_turn` 在 turn 开始记 `plan_at_start`，`finally` 里若 `plan_mode` 从 True→False（审批通过）调 `db.save_session_mode(sid, "agent.fast")`——重连后恢复成执行态，不重入规划模式。`run_exit_plan_mode` 把方案写 `session_dir()/"plan.md"` 留档 / 重连中途审批恢复。
- **`team_mode`** 同 flag 模式（`chat.send` 设 `team_mode`），靠 `update_context` merge 跨轮存活。
- **`update_context` 必须 merge**：`context.update_context` 做 `out = dict(context); out[...]=...`，保留 caller-set flag（`plan_mode`/`team_mode`）。不能改成返回全新 dict——旧实现每轮丢 flag，plan 模式（探索→审批→退出跨多轮）直接废。

---

## 🧪 Testing

```bash
MODEL_ID=test-model OPENAI_API_KEY=dummy python -m pytest tests/ -q
```

`tests/` 有 pytest 集成测试（核心事件流 + 网关）。前端有 vitest + playwright。

---

## 💣 踩过的坑（Pitfalls）

> 这一节是实打实踩出来的，每条都是"现象 → 根因 → 修复/约定"。

### 1. `update_context` 重建 dict 丢跨轮 flag

- **现象**：规划模式第 1 轮生效，第 2 轮就变回 fast；team 模式也只在第 1 轮有指令。
- **根因**：`update_context` 返回全新 3-key dict `{memories, connected_mcp, active_teammates}`，把 caller 设的 `plan_mode`/`team_mode` 丢了。team 靠"第 1 轮就 `start_team`"侥幸能跑，plan 需要跨多轮（探索→审批→退出）就废了。
- **修复**：改成 `out = dict(context); out[...] = ...` merge，保留所有 caller-set flag。

### 2. wire.py `permission_request` 映射缺 `options`，前端崩

- **现象**：弹权限框时前端报 `Cannot read properties of undefined (reading 'map')`。
- **根因**：`wire.py` 把 `permission_request` 映射成 `questions:[{question, detail}]`，没 `options`。`UserQuestionModal` 渲染 `question.options.map(...)` 直接炸。
- **修复**：映射时带上 `options: [{label:"Allow"},{label:"Deny"}]`。

### 3. `_ask` 用 `permission.request(block)`，Allow 点击无效

- **现象**：权限弹窗点 Allow 没反应，agent 一直卡着。
- **根因**：`_ask` 走 `permission.request(block)`，`FuturePermission` 内部生成 uuid 作为 `pending_permissions` 的 key，但这个 id **从没发给客户端**。`wire.py` 回退用 `seq` 当 request_id，`gs.grant(seq)` 找不到 pending future，future 永不 resolve。
- **修复**：`_ask` 自己生成 `request_id`，**先**经 `resolver(block, request_id)` 注册 future，**再** emit 带 `request_id` 的事件，**再** block。request_id 必须闭环。

### 4. `_config_get` 不返回 `permissions_enabled`，安全配置 tab 空

- **现象**：配置信息 → 安全配置页空白，没有工具权限/记忆过滤配置项。
- **根因**：`PermissionsToolsEditor` 挂在 `"permissions"` group 的 `afterTable`，该挂载点要求 `config.get` 返回 `permissions_enabled` key。`_config_get` 没返回，afterTable 不挂载。
- **修复**：`_config_get` 读 `permissions.is_enabled()` / `get_memory_forbidden()` 返回 `permissions_enabled` / `memory_forbidden_enabled` / `memory_forbidden_description`；`_config_set` 对写回。

### 5. `chat.final.content` 故意是 `""`

- **现象**：想"修"wire 把整段文本放进 `done` 事件，结果前端重复渲染。
- **根因**：流式文本走 `chat.delta`，前端累加 delta；`chat.final` 只是 turn 结束标记（`isProcessing=false`）。放全文进 `done` 会和已累加的 delta 双渲染。
- **约定**：`done` 事件 payload 保持 `{}`，别"修"。

### 6. `history.json` assistant 记录缺 `event_type`，预览只有用户消息

- **现象**：SessionsPanel 预览只显示用户消息，assistant 回复不见了。
- **根因**：`parseHistoryTimelineEntry` 要求 assistant 记录带 `event_type`（如 `chat.final`）+ 字符串 `content`，否则丢弃。
- **修复**：`_write_session_files` 把 assistant 记录归一成 `{role:"assistant", event_type:"chat.final", content, timestamp}`。

### 7. 共享消息列表被 compaction 原地改，400 + replay 丢失

- **现象**：反复出 ModelArts.81001 400；重连后历史残缺。
- **根因**：一开始只有一条消息列表既当持久 record 又当 LLM context，compaction 原地改它，把 tool_result 孤儿了（adapter 两端 tool/tool_call 对不上 → 400），还把持久 record 改坏。
- **修复**：拆成 `record`（只追加、永不压缩）+ `context_messages`（可压缩）。所有 `append` 走 `append_both`，压缩只 `messages[:] = ...` 动 `context_messages`。

### 8. 规划模式审批不落库，重连错误重入

- **现象**：规划模式审批通过后，会话被驱逐+重连，又回到规划模式（只读），没法执行。
- **根因**：`exit_plan_mode` 只 pop 了内存里的 `plan_mode`，DB mode 还是 `agent.plan`（`chat.send` 存的）。重连 `_build` 从 DB 恢复 mode 又设上 `plan_mode`。
- **修复**：`_run_turn` 检测 `plan_mode` 从 True→False 的迁移，`db.save_session_mode(sid, "agent.fast")` 落库。

### 9. bash 在 plan 模式可写，绕过只读

- **现象**：规划模式本该只读，但 `bash: "rm x"` / `"git reset --hard"` / `"echo > f"` 照样跑。
- **根因**：`bash` 在 `_PLAN_MODE_ALLOWED` 里（探索要用），但 `check_permission` 不感知 `plan_mode`，没拦变更命令。
- **修复**：`check_permission` 在 `plan_mode` 下按 `_PLAN_MODE_BASH_DENY` deny 变更命令（rm/mv/cp/redirects/git-write/npm install/…），`ls`/`git status`/`cat` 放行。

### 10. `chat.send` mode 只处理 TEAM，PLAN 被忽略

- **现象**：选「规划模式」和「agent.fast」行为完全一样，规划模式没实现。
- **根因**：`agent_compat.py` chat.send 只对 `Mode.TEAM` 翻 `team_mode` flag，`Mode.PLAN` 走 else 被忽略。
- **修复**：加 `Mode.PLAN` 分支翻 `plan_mode`，配工具裁剪 + prompt 指令 + `exit_plan_mode` 审批 gate。

---

## 🤝 Contributing

1. Fork → branch → PR。
2. **加 builtin 工具**：同时改 `agent_core/tools.py` 的 `BUILTIN_TOOLS`（schema）和 `BUILTIN_HANDLERS`（handler）——两张表故意不自动派生。
3. **换 provider**：只动 `agent_core/adapter.py`，下游全消费 Anthropic 风格 block。
4. **加消息类型**：`agent_loop` 里用 `session.append_both(msg)`，别直接 `messages.append`（record 会不同步）。
5. **压缩**：只动 `session.context_messages`，绝不碰 `session.record`。
6. `agent_core/` 的循环依赖靠函数内 deferred import，别提到模块顶层。

---

## 📄 License

Apache License 2.0（见 [`LICENSE`](./LICENSE)）。

`frontend_vite/` 基于开源项目 [jiuwenswarm](https://github.com/openJiuwen-ai/jiuwenswarm)（Apache-2.0）修改而来，相关代码保留其版权与许可声明。
