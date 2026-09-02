# myAgent 微服务 + 前端 设计

> 日期: 2026-07-02
> 状态: 已定稿，待实现
> 范围: 在现有 `code.py` agent core 之上加 FastAPI 网关 + Next.js 前端，事件驱动解耦 I/O，为后续真微服务化预留边界。
> 修订: 2026-07-03 — 传输层加 SSE（WebSocket 为主、SSE 降级），见 §4.1。

## 1. 目标与约束

- **目标**: 把现有单文件 CLI agent 变成可前端对话交互的"生成级 agent"，微服务架构边界，高可扩展性。
- **已实现**: `code.py`（~2250 行，agent core，CLI REPL）。
- **本次不做**: 真微服务拆分（agent/tool/memory/skill/cron 各自独立部署）、多用户鉴权。但设计要为这些留好边界。
- **约束**: `code.py` 的"一个 loop"教学价值要保留；CLI 入口行为不变。

## 2. 服务拓扑

```
┌──────────┐ WS+SSE  ┌──────────────┐  in-proc  ┌──────────────────┐
│ Next.js  │◄──────► │  gateway     │◄────────► │  agent core      │
│ frontend │  (见4.1) │  (FastAPI)   │           │  (code.py +      │
│ (chat UI)│         │  session mgr │           │   EventSink)     │
└──────────┘         └──────────────┘           └──────────────────┘
                           │  pub (later)              │
                           └──────────► event bus ◄────┘   (in-proc queue now;
                                                          NATS/Redis later)
```

- 今天 3 个服务，gateway + core 同进程（边界是微服务，部署不是）。
- 事件总线现在是进程内队列；以后换 NATS/Redis 是一处接口改动，也是 memory-svc / cron-svc / tool-svc 真正拆出去的接缝。
- 前端↔gateway 传输：WebSocket（默认双向）+ SSE（单向降级）。两者复用同一事件枚举，见 §4.1。

## 3. 核心改动：解耦 `code.py` 的 I/O（方案 A：注入事件 sink）

`agent_loop` 现在直接调 `print()` / `input()`，读全局 `rounds_since_todo` / `agent_lock` / `history` / `context`。改为：

- **`EventSink` 协议** — `emit(kind, payload)`，`kind ∈ {token, text, tool_start, tool_result, error, permission_request, compacted, done}`。
  - `TerminalSink`（CLI）→ 打印，行为同今。
  - `ChannelSink`（API）→ 推到该 session 的 `asyncio.Queue`；session 可挂多个 sink（WS + SSE 各一），`emit` 扇出给所有活跃 sink。事件带单调递增 `seq`，供重连回放（见 §4.1/§7）。
- **`agent_loop(messages, context, events, permission)`** — ~15 处 `print`/`terminal_print` 换成 `events.emit(...)`；CLI 行为不变。
- **`permission_hook`** — `input("Allow?")` → `permission.request(block)` 返回 `{allow, modify}`。CLI 走 `input`；API 走 future，由前端消息 resolve（WS `permission_response` 或 SSE 模式下 `POST .../permissions/{request_id}/respond`，见 §4.1）。future 带 120s 超时，超时视为拒绝。
- **`chat_create` 加流式** — `stream=True` 路径，token delta 经 `events.emit("token", ...)`，最后拼回 content block。非流式保留作 fallback。
- **Session 状态** — `history` / `context` / `rounds_since_todo` / `transport`（`ws|sse`，创建时定、不可变）/ `active_sinks` 提进 `Session` 对象；`agent_lock` 改为 per-session。热路径去全局（`__main__` CLI 保留）。
- **`__main__` 不变** — 仍是教学 CLI。

被否决的方案：
- B（重定向 stdout 解析 ANSI）：脆弱、无 token 流、权限回不来。
- C（fork 出 `agent_server.py`）：两个 loop 会漂移，丢单文件教学价值。仅当要冻结 `code.py` 作参考时才选。

## 4. Gateway 服务（FastAPI）

- `POST /api/sessions` → 建 session，可选 `transport=ws|sse|auto`（默认 `auto`：先试 WS 握手，失败降级 SSE；一旦确定即不可变，避免运行中切换的状态搬运）。
- `WS /api/sessions/{id}` → 双向（默认，功能完整）：
  - server→client: `token` `text` `tool_start` `tool_result` `permission_request` `error` `compacted` `done`（每帧带 `seq`）
  - client→server: `{type:"permission_response", request_id, allow}`、`{type:"user_message", text}`、`{type:"interrupt"}`、`{type:"resume", last_seq}`（重连回放）
- `GET /api/sessions/{id}/status` → 会话状态：支持的传输、当前 `transport`、活跃 sink 列表、最新 `seq`。（独立路径，避免与 `WS /api/sessions/{id}` 冲突。）
- `GET /api/skills` `GET /api/tasks` `GET /api/memories` → 对现有 dot-dir 的只读视图。
- `POST /api/sessions/{id}/messages` → 发用户消息。WS 模式下作非流式 REST fallback；SSE 模式下是客户端→服务器主通道。
- 每 session 一个 asyncio task 跑 `agent_loop`（配 `ChannelSink`）；WS/SSE pump 把队列排到客户端。
- 横切：CORS 允许前端域；SSE/WS 每 15s 心跳；session 空闲 30min 清理；所有端点用同一 session 鉴权 cookie/token 关联 SSE 流与 REST POST，防止跨连接串台。

### 4.1 传输层：WebSocket + SSE

**角色分工**：WebSocket 是默认双向通道，覆盖所有交互（用户消息、权限响应、中断、重连）。SSE 是单向 server→client 降级通道，用于 WS 被屏蔽的环境（反向代理、企业防火墙、HTTP/1.1 only）和只读观察者。SSE 复用 WS 的 `server→client` 事件枚举，不另起一套 taxonomy。

**SSE 端点**：`GET /api/sessions/{id}/events` → `Content-Type: text/event-stream`。帧格式：
```
id: <seq>
event: <kind>          # token | text | tool_start | tool_result | permission_request | error | compacted | done
data: <json_payload>   # 与 WS 同 kind 的 payload 完全一致

```
断线重连：客户端带 `Last-Event-ID: <seq>` 重连，服务器从该 seq 之后回放缓冲（保留最近 N 事件或 M 字节，见 §7），再续实时。连接错误时发 `retry: 3000` 建议重试间隔。

**SSE 模式下客户端→服务器**（SSE 单向，故走 REST POST，与 SSE 流同 session 鉴权）：
- 用户消息：`POST /api/sessions/{id}/messages` `{text}`
- 权限响应：`POST /api/sessions/{id}/permissions/{request_id}/respond` `{allow, modify?}` → 唤醒对应 pending future → `agent_loop` 继续
- 中断：`POST /api/sessions/{id}/interrupt`

**传输选择**：`transport=auto` 时前端先试 WS 握手，失败降级 SSE；`ws`/`sse` 强制单传输。`auto` 在首次成功后锁定为该 session 的 `transport`（不可变），杜绝运行中切换。多订阅：一个 session 可同时挂 SSE（只读观众）+ WS（驱动者），事件扇出给所有 sink；权限响应只接受驱动者那条通道，避免冲突。

## 5. 前端（Next.js + TypeScript + Tailwind）

- App Router。
- **`ChatPanel`** — 消费事件流：token delta 追加到当前 assistant 气泡；`tool_start`/`tool_result` 渲染为可折叠工具卡片；`permission_request` 渲染内联允许/拒绝卡片，结果按当前传输回传（WS `permission_response` 或 SSE `POST .../respond`）。UI 不感知底层传输。
- **`useAgentTransport`** hook（由 `useAgentSocket` 演化而来）— Transport 抽象：
  ```ts
  interface Transport {
    connect(): Promise<void>;
    disconnect(): void;
    send(data: AgentClientMsg): void;   // user_message / permission_response / interrupt
    onEvent(cb: (e: AgentEvent) => void): void;
  }
  ```
  实现 `WebSocketTransport`（双向，`send` 走 WS）与 `SSETransport`（`EventSource` 接事件，`send` 走 REST POST）。hook 管连接、事件分发、重连、interrupt；按 session `transport` 选实现。
- Sidebar — sessions / skills / tasks / memories（REST 拉取），每面板一组件，新功能加法式扩展。
- Markdown + 代码块（react-markdown + shiki），工具输出剥 ANSI。

## 6. 一轮数据流

```
用户输入 → (WS user_message | POST /messages)
  → gateway 追加 session.history，触发 agent_loop 一轮
  → prepare_context → call_llm(stream)
      ↳ token 事件 → (WS | SSE) → 前端追加气泡
  → tool_use(Read) → tool_start → (WS | SSE) → 卡片出现
  → handler → tool_result → (WS | SSE) → 卡片填充
  → tool_use(Bash, "rm ...") → permission.request
      → permission_request → (WS | SSE) → 前端显示允许/拒绝
      → 用户点 Allow → (WS permission_response | POST .../respond)
      → future resolve → handler → tool_result
  → 无 tool_use → done → (WS | SSE) → 气泡定稿
```

传输选择在 session 建立时定（`auto` 先 WS 后 SSE，锁定后不变）；同一 session 内所有事件走同一传输（多订阅时驱动者走 WS、观察者走 SSE）。

## 7. 错误处理

- LLM 错误 → `events.error`，session 存活（保留现有 `RecoveryState`）。
- WS 中途断开 → session worker 跑到完成；事件缓冲在队列；重连时客户端发 `{type:"resume", last_seq}`，服务器从 `last_seq` 之后回放再续实时。
- SSE 中途断开 → 同上，但用浏览器原生 `Last-Event-ID` 头，无需自定义协议。
- 缓冲策略：每 session 保留最近事件，上限 `MAX_BUFFER_BYTES`（默认 4MB）或 1000 条，取先到者；满时丢最旧并发一条 `error` 事件通知前端（不静默丢）。
- 权限超时（默认 120s）→ 视为拒绝并记录。
- prompt-too-long → `reactive_compact` → 发 `compacted` 事件，前端提示。
- 心跳：WS/SSE 每 15s 一帧；超 45s 无响应判定连接死，触发重连。

## 8. 测试

- Core: `agent_loop` + `RecordingSink`（捕获事件）→ 对脚本化一轮断言事件序列，无网络。这是之前没有的可测接缝。
- Gateway: FastAPI `TestClient` + 假 session → 断言 WS 事件帧；新增 SSE 专项：`/events` 帧格式（`id`/`event`/`data`）、`Last-Event-ID` 回放、`POST .../permissions/{rid}/respond` 唤醒 future、`POST .../interrupt`、WS+SSE 并存扇出、缓冲满发 `error`。
- Frontend: Vitest 测 `useAgentTransport` reducer（纯事件→state），`WebSocketTransport` 与 `SSETransport` 各一套；Playwright e2e 冒烟（输入 → 见 token 流 → 见工具卡；WS 被屏蔽时自动降级 SSE）对 docker-compose。
- `code.py` 此前无测试；本次新增的都打在新接缝上。

## 9. 可扩展性接缝

- **新工具** → 现有 `BUILTIN_TOOLS`/`BUILTIN_HANDLERS` 表（机制不变），前端工具卡自动出现。
- **新事件类型** → 加到版本化事件 enum + 前端 renderer；gateway 透传未知事件（WS 与 SSE 同路径，自动同时支持）。
- **新传输** → 实现 `EventSink` + gateway 路由 + 前端 `Transport` 实现；core 不动（说的是事件，不是传输）。预留 WebTransport。
- **新面板/功能** → Next.js 路由 + gateway REST 端点读对应 dot-dir。
- **拆出真微服务** → 把该子系统挪到事件总线（NATS/Redis）后；core 和 gateway 不变，因为它们说的是事件不是直接调用。SSE/WS 服务亦可独立部署，只订阅事件总线。
- **多用户** → 加 auth 中间件 + session 上 `user_id`；`Session` 已 per-session，core 不动。

## 10. 仓库布局

```
myAgent/
  code.py                      # core（+ EventSink/Session；CLI 不变）
  agent_gateway/               # FastAPI 服务
    main.py  sessions.py  ws.py  sse.py  transports.py  schemas.py
  frontend/                    # Next.js
    app/  components/  lib/useAgentTransport.ts  lib/transports/{ws,sse}.ts
  docker-compose.yml           # frontend + gateway(+core)
  Dockerfile.gateway  Dockerfile.frontend
  docs/superpowers/specs/      # 本文档所在
```

## 11. 实现顺序（给 writing-plans 的输入）

1. core: `EventSink`/`Session`/`permission` 抽象 + `agent_loop` 改造 + `chat_create` 流式（CLI 回归通过）。
2. gateway: FastAPI + session mgr + WS pump + REST 视图端点。
3. gateway SSE: `/events` 端点 + `Last-Event-ID` 回放 + SSE 模式的 REST POST（messages/permissions/interrupt）+ 缓冲 + 心跳 + CORS。
4. frontend: 脚手架 + `useAgentTransport` + `WebSocketTransport`/`SSETransport` + `ChatPanel` + 工具卡 + 权限卡 + sidebar。
5. docker-compose + 两个 Dockerfile。
6. 测试: RecordingSink 单测 + gateway TestClient（WS+SSE）+ 前端 Vitest + e2e 冒烟（含 WS 降级 SSE）。
