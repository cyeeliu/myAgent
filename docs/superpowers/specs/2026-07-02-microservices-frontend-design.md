# myAgent 微服务 + 前端 设计

> 日期: 2026-07-02
> 状态: 已定稿，待实现
> 范围: 在现有 `code.py` agent core 之上加 FastAPI 网关 + Next.js 前端，事件驱动解耦 I/O，为后续真微服务化预留边界。

## 1. 目标与约束

- **目标**: 把现有单文件 CLI agent 变成可前端对话交互的"生成级 agent"，微服务架构边界，高可扩展性。
- **已实现**: `code.py`（~2250 行，agent core，CLI REPL）。
- **本次不做**: 真微服务拆分（agent/tool/memory/skill/cron 各自独立部署）、多用户鉴权。但设计要为这些留好边界。
- **约束**: `code.py` 的"一个 loop"教学价值要保留；CLI 入口行为不变。

## 2. 服务拓扑

```
┌──────────┐   WS    ┌──────────────┐  in-proc  ┌──────────────────┐
│ Next.js  │◄──────► │  gateway     │◄────────► │  agent core      │
│ frontend │         │  (FastAPI)   │           │  (code.py +      │
│ (chat UI)│         │  session mgr │           │   EventSink)     │
└──────────┘         └──────────────┘           └──────────────────┘
                           │  pub (later)              │
                           └──────────► event bus ◄────┘   (in-proc queue now;
                                                          NATS/Redis later)
```

- 今天 3 个服务，gateway + core 同进程（边界是微服务，部署不是）。
- 事件总线现在是进程内队列；以后换 NATS/Redis 是一处接口改动，也是 memory-svc / cron-svc / tool-svc 真正拆出去的接缝。

## 3. 核心改动：解耦 `code.py` 的 I/O（方案 A：注入事件 sink）

`agent_loop` 现在直接调 `print()` / `input()`，读全局 `rounds_since_todo` / `agent_lock` / `history` / `context`。改为：

- **`EventSink` 协议** — `emit(kind, payload)`，`kind ∈ {token, text, tool_start, tool_result, error, permission_request, compacted, done}`。
  - `TerminalSink`（CLI）→ 打印，行为同今。
  - `ChannelSink`（API）→ 推到该 session 的 `asyncio.Queue`。
- **`agent_loop(messages, context, events, permission)`** — ~15 处 `print`/`terminal_print` 换成 `events.emit(...)`；CLI 行为不变。
- **`permission_hook`** — `input("Allow?")` → `permission.request(block)` 返回 `{allow, modify}`。CLI 走 `input`；API 走 future，由前端 WS 消息 resolve。
- **`chat_create` 加流式** — `stream=True` 路径，token delta 经 `events.emit("token", ...)`，最后拼回 content block。非流式保留作 fallback。
- **Session 状态** — `history` / `context` / `rounds_since_todo` 提进 `Session` 对象；`agent_lock` 改为 per-session。热路径去全局（`__main__` CLI 保留）。
- **`__main__` 不变** — 仍是教学 CLI。

被否决的方案：
- B（重定向 stdout 解析 ANSI）：脆弱、无 token 流、权限回不来。
- C（fork 出 `agent_server.py`）：两个 loop 会漂移，丢单文件教学价值。仅当要冻结 `code.py` 作参考时才选。

## 4. Gateway 服务（FastAPI）

- `POST /api/sessions` → 建 session，返回 `{session_id}`。
- `WS /api/sessions/{id}` → 双向：
  - server→client: `token` `text` `tool_start` `tool_result` `permission_request` `error` `compacted` `done`
  - client→server: `{type:"permission_response", request_id, allow}`、`{type:"user_message", text}`、`{type:"interrupt"}`
- `GET /api/skills` `GET /api/tasks` `GET /api/memories` → 对现有 dot-dir 的只读视图。
- `POST /api/sessions/{id}/messages` → 非流式 REST fallback。
- 每 session 一个 asyncio task 跑 `agent_loop`（配 `ChannelSink`）；WS pump 把队列排到客户端。

## 5. 前端（Next.js + TypeScript + Tailwind）

- App Router。
- **`ChatPanel`** — 消费事件流：token delta 追加到当前 assistant 气泡；`tool_start`/`tool_result` 渲染为可折叠工具卡片；`permission_request` 渲染内联允许/拒绝卡片，结果回传 WS。
- **`useAgentSocket`** hook — 管 WS 连接、事件分发、重连、interrupt。
- **Sidebar** — sessions / skills / tasks / memories（REST 拉取），每面板一组件，新功能加法式扩展。
- Markdown + 代码块（react-markdown + shiki），工具输出剥 ANSI。

## 6. 一轮数据流

```
用户输入 → WS user_message
  → gateway 追加 session.history，触发 agent_loop 一轮
  → prepare_context → call_llm(stream)
      ↳ token 事件 → WS → 前端追加气泡
  → tool_use(Read) → tool_start → WS → 卡片出现
  → handler → tool_result → WS → 卡片填充
  → tool_use(Bash, "rm ...") → permission.request
      → permission_request → WS → 前端显示允许/拒绝
      → 用户点 Allow → WS permission_response
      → future resolve → handler → tool_result
  → 无 tool_use → done → WS → 气泡定稿
```

## 7. 错误处理

- LLM 错误 → `events.error`，session 存活（保留现有 `RecoveryState`）。
- WS 中途断开 → session worker 跑到完成；事件缓冲在队列；重连后回放缓冲再续实时。
- 权限超时（默认 120s）→ 视为拒绝并记录。
- prompt-too-long → `reactive_compact` → 发 `compacted` 事件，前端提示。

## 8. 测试

- Core: `agent_loop` + `RecordingSink`（捕获事件）→ 对脚本化一轮断言事件序列，无网络。这是之前没有的可测接缝。
- Gateway: FastAPI `TestClient` + 假 session → 断言 WS 事件帧。
- Frontend: Vitest 测 `useAgentSocket` reducer（纯事件→state）；Playwright e2e 冒烟（输入 → 见 token 流 → 见工具卡）对 docker-compose。
- `code.py` 此前无测试；本次新增的都打在新接缝上。

## 9. 可扩展性接缝

- **新工具** → 现有 `BUILTIN_TOOLS`/`BUILTIN_HANDLERS` 表（机制不变），前端工具卡自动出现。
- **新事件类型** → 加到版本化事件 enum + 前端 renderer；gateway 透传未知事件。
- **新面板/功能** → Next.js 路由 + gateway REST 端点读对应 dot-dir。
- **拆出真微服务** → 把该子系统挪到事件总线（NATS/Redis）后；core 和 gateway 不变，因为它们说的是事件不是直接调用。
- **多用户** → 加 auth 中间件 + session 上 `user_id`；`Session` 已 per-session，core 不动。

## 10. 仓库布局

```
myAgent/
  code.py                      # core（+ EventSink/Session；CLI 不变）
  agent_gateway/               # FastAPI 服务
    main.py  sessions.py  ws.py  schemas.py
  frontend/                    # Next.js
    app/  components/  lib/useAgentSocket.ts
  docker-compose.yml           # frontend + gateway(+core)
  Dockerfile.gateway  Dockerfile.frontend
  docs/superpowers/specs/      # 本文档所在
```

## 11. 实现顺序（给 writing-plans 的输入）

1. core: `EventSink`/`Session`/`permission` 抽象 + `agent_loop` 改造 + `chat_create` 流式（CLI 回归通过）。
2. gateway: FastAPI + session mgr + WS pump + REST 视图端点。
3. frontend: 脚手架 + `useAgentSocket` + `ChatPanel` + 工具卡 + 权限卡 + sidebar。
4. docker-compose + 两个 Dockerfile。
5. 测试: RecordingSink 单测 + gateway TestClient + 前端 Vitest + e2e 冒烟。
