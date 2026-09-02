# Harness Portfolio Roadmap

> 按「harness 岗面试命中率 ÷ 实现成本」排序。
> 边做边勾 `[x]`，每项附验收标准和插入位置。
> 上一份 `docs/portfolio-roadmap.md` 按应用岗排序；本份按 **harness 开发岗**重新定位。

---

## 已覆盖的 harness 骨架（直接可讲，不在本清单内）

- ✅ 单 loop 编排（`loop.py: agent_loop` — dispatch / permission / hooks / todos / compaction / recovery）
- ✅ 工具调度（`tools.py` 双表 + `mcp.py` 合并池）
- ✅ 权限/沙箱（`permissions.py` policy store + `hooks.py` gate + bash deny-list + path escape）
- ✅ 上下文压缩（`context.py` — tool_result_budget / snip / micro / compact / reactive）
- ✅ 子代理 / 团队（`subagent.py` + `teammates.py` 7 模块拆分 + `bus.py` 协议）
- ✅ MCP 外部工具接入（`mcp.py` — connect / assemble_tool_pool）
- ✅ 三层 replay 网关（live Redis → chat record → Postgres chat_record）
- ✅ 持久化（Postgres sessions 表 + Redis event pipe + 文件 transcript）
- ✅ Plan mode（read-only → submit → approve → execute）
- ✅ 企业级后端结构（app 工厂 / routes / services / middleware / 异常层级）

---

## P0 — harness 核心能力，面试必问

### [x] 1. 并行工具执行

**现状**：`loop.py:204` 的 `for block in response.content:` 串行执行每个 tool_use。

**为什么**：Claude Code / Cursor 对独立只读工具（多个 read_file / grep / glob）并发执行，这是 harness 性能和设计成熟度的标志，面试必问「你怎么处理 parallel tool calls」。

**做什么**：
- 把 tool_use 分两类——只读无副作用工具（read / grep / glob / web_fetch / web_search）用 `concurrent.futures` 或 `asyncio` 并发；写工具仍串行（保顺序语义）
- 结果按原 block 顺序收集回填
- `tools.py` 标注每个工具 `readonly: bool`

**验收**：一次返回 3 个 read_file，总耗时 ≈ max(单次) 而非 3×；写工具顺序不变。

**插哪**：`loop.py` 的执行段 + `tools.py` 标注。

---

### [x] 2. 流式 tool_use 增量协议

**现状**：`loop.py:207` 等 LLM 响应完整返回后才发 `tool_start{input}`，前端看不到工具参数逐字生成。

**为什么**：流式是 harness 的核心传输话题，「tool call 参数边生成边渲染」是 Cursor / Claude Code 的体验分水岭，面试常问 streaming 设计。

**做什么**：
- `adapter.chat_create` 流式分支里把 OpenAI 的 `tool_calls` delta（`index` / `function.arguments` 增量片段）聚合成 partial tool_use，逐片段 emit `tool_start_delta`
- loop 收完再执行
- 前端 ToolCard 边收边渲染参数

**验收**：长参数工具（如 write_file 大内容）参数区逐字滚动出现，而非最后一次性弹出。

**插哪**：`adapter.py` 流式解析 + `loop.py` / `wire.py` 新事件 + 前端 reducer。

---

### [x] 3. 健壮编辑原语（fuzzy match + replace_all + multi-edit + diff apply）

**现状**：`edit_file` 只做单次精确字符串替换，匹配失败即报错。

**为什么**：编辑是 coding harness 的心脏。Aider 的 search/replace block、Claude Code 的 normalized 匹配都是面试考点；「LLM 给的 old_string 有空白 / 缩进偏差时怎么办」是经典追问。

**做什么**：
- `replace_all` + multi-edit（一次多个替换）
- **fuzzy / anchored 匹配**：精确失败时做空白归一化匹配，仍失败则返回带行号的上下文让 LLM 自纠（而非直接报错）
- **unified diff apply**：新增 `apply_diff` 工具，接受 `--- / +++` 格式 patch

**验收**：old_string 缩进差 2 空格仍能命中；`replace_all` 替换全部；diff patch 能 apply。

**插哪**：`tools.py` 的 `run_edit_file` + 新 `run_apply_diff` + `BUILTIN_TOOLS` 两张表。

---

### [x] 4. Checkpoint / Undo / 文件状态回滚

**现状**：无任何文件快照 / 回滚机制。

**为什么**：harness 安全性的核心——「agent 改坏了能撤」。面试问「你的 agent 把代码改崩了怎么办」时，没有 undo 是硬伤。

**做什么**：
- 每轮写工具执行前快照受影响文件（或用 git stash / commit 到隔离 ref）
- 新增 `undo` 工具回滚最近 N 步写操作
- `checkpoint` / `restore` 显式工具
- 出错自动回滚到上一 checkpoint

**验收**：agent 连改 3 个文件后 `undo` 2 次，文件回到第 1 次改后状态；改崩后自动 restore。

**插哪**：新 `agent_core/checkpoint.py` + `loop.py` 写工具前后埋点 + `tools.py` 注册 undo / restore。

---

## P1 — 强差异化（harness 岗加分项）

### [x] 5. LSP / 代码智能集成

**现状**：完全无语言服务器，agent 靠 grep 猜代码结构。

**为什么**：Cursor / Continue 的护城河就是 LSP。harness 岗问「你的 agent 怎么理解代码」时，「接了 LSP 拿 diagnostics / go-to-def / references」是顶级回答。

**做什么**：
- 起一个 language server（pyright / pylsp 起步，Python 自举）
- 通过 JSON-RPC 暴露成工具：`diagnostics`（当前文件错误）、`goto_definition`、`find_references`、`hover`
- agent 改完文件先看 diagnostics 自纠

**验收**：写个有语法错的文件，agent 调 `diagnostics` 拿到错误行号并修复。

**插哪**：新 `agent_core/lsp.py`（JSON-RPC client）+ `tools.py` 注册 4 个工具。

---

### [x] 6. Agent 自评测（SWE-bench 风格）

**现状**：只有 `tests/` 单元测试，无「agent 能不能解决真实 issue」的评测。

**为什么**：对 harness 开发岗，「我的 harness 在 SWE-bench-lite 过 N%」是杀手级简历行，比任何功能描述都有说服力。

**做什么**：
- `evals/` 目录，每个 case = 一个 mini repo + issue 描述 + 隐藏测试
- 跑 harness 让它修，跑测试判通过
- `make eval` 出报告（通过率 / 平均步数 / 平均 token）

**验收**：放 5-10 个手写小 case，`make eval` 输出 `3/8 passed, avg 12 steps`。

**插哪**：新 `evals/` + `evals/runner.py` + Makefile target。

---

### [x] 7. 可观测性 / Tracing

**现状**：只有 `context_usage` token 统计，无 span 树 / 延迟 / 成本 / 工具成功率。

**为什么**：harness 上线后调试靠 trace，面试问「怎么排查 agent 行为异常」时这是标准答案。

**做什么**：
- 每轮 + 每个 tool_use 生成 `trace_id` / `span_id`，记耗时 / token / 输入输出到 `.transcripts/` 或 Postgres `spans` 表
- `/api/metrics` 出 p50 / p95 延迟 + 成本 + 工具成功率
- 前端一个 trace 视图

**验收**：跑一轮后能看到 span 树（turn → tool_call → tool_result，带耗时）。

**插哪**：新 `agent_core/tracing.py` + `loop.py` 埋点 + gateway 端点。

---

### [x] 8. 结构化输出 / tool_choice 强约束

**现状**：`adapter.py` 无 `response_format` / `tool_choice`。

**为什么**：harness 要可靠控制 LLM 行为（强制走工具、强制 JSON），是可靠性设计考点，且实现成本低。

**做什么**：
- `adapter.chat_create` 支持 `response_format=json_schema` 和 `tool_choice`（`required` / 指定工具）
- 一个 `structured_output` 辅助给 schema 强约束返回

**验收**：`tool_choice="required"` 强制走工具；给 schema 返回 100% 合规 JSON。

**插哪**：只动 `adapter.py` + 新 `agent_core/structured.py`。

---

## P2 — 打磨 / 新兴

| # | 状态 | 功能 | harness 价值 | 工作量 |
|---|------|------|------|--------|
| 9 | [ ] | 多模态截图输入（read_image → image block → 视觉模型） | Devin / Claude Code 读截图做 UI 任务 | 1-2 天 |
| 10 | [ ] | A2A 多 agent 互操作协议 | 2025 新兴（Google A2A），harness 互操作差异化 | 2-3 天 |
| 11 | [ ] | 成本 / 预算控制（per-session token 上限 + 面板） | harness 上线必问「怎么控成本」 | 1-2 天 |
| 12 | [ ] | k8s + Helm + compose healthcheck | 生产部署成熟度 | 1-2 天 |

> 上一份清单里的 RAG / 工作流 DAG 对 harness 岗是应用层，优先级降到 P2 以下。但留一个轻量 RAG 工具（retrieve）当「harness 也能做知识问答」的 demo 不亏，时间富余再加。

---

## 实现顺序（按依赖 + 见效）

```
第1周: P0-1 并行工具 + P0-3 编辑原语        ← 都改 loop/tools，harness 内核
第2周: P0-2 流式 tool_use + P0-4 checkpoint  ← 一个改传输一个改安全
第3周: P1-5 LSP + P1-6 SWE-bench 评测        ← 一个加智能一个加证明，可并行
第4周: P1-7 tracing + P1-8 结构化输出        ← 收尾
```

## 面试叙事

把 README 头部改成：

> 自研 coding agent harness：单 loop 编排 + 并行工具调度 + 流式 tool_use 协议 + 健壮编辑原语 + checkpoint / undo + LSP 代码智能，SWE-bench-lite 过 N%。

这套话术直接对标 Claude Code / Cursor / Trae 的 harness 团队。
