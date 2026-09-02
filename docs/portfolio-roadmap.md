# Portfolio Roadmap

> 按「面试命中率 ÷ 实现成本」排序的实现清单。
> 边做边勾 `[x]`，每项附验收标准和插入位置。

---

## P0 — 必做（最高频考点 × 项目最大空白）

### [ ] 1. RAG 子系统（向量检索 + 知识库）

**为什么**：深圳 Agent 应用岗 80%+ JD 写「RAG / 向量检索 / 知识库」，这是项目最大的单点空白。做完它，作品从「只会写代码的 agent」变成「能做企业知识问答的 agent」，两个方向都覆盖。

**做什么**：
- embedding 调用（走 OpenAI 兼容 embeddings API，复用 `adapter.py` 的 client）
- 向量存储：**首选 pgvector**——已有 Postgres，零新基础设施；或 chroma/qdrant 二选一
- ingest 管线：文档 → 切 chunk → embed → 入库（chunk size/overlap 可配）
- 新工具 `retrieve` / `search_knowledge`（加进 `tools.py` 的 `BUILTIN_TOOLS` + `BUILTIN_HANDLERS` 两张表）
- 检索结果注入 system prompt（在 `prompt.py:assemble_system_prompt` 里拼一段）

**验收**：放一份 PDF/markdown 知识库，问「XX 文档里说了什么」，agent 能检索后回答；附一个 ingest 脚本和一篇 demo 知识库。

**插哪**：新模块 `agent_core/rag.py` + `tools.py` 注册 + `prompt.py` 注入。

---

### [ ] 2. 结构化输出 / JSON Schema 强约束

**为什么**：应用岗要可靠抽取实体 / 分类 / 生成配置，infra 岗要懂可靠性。`adapter.py` 现在没有任何 `response_format` / `tool_choice`。

**做什么**：
- `adapter.chat_create` 支持 `response_format={"type":"json_schema",...}` 和 `tool_choice`（强制 / 自动选工具）
- 一个 `structured_output` 辅助：给定 pydantic schema → 强约束 LLM 返回 → 校验
- 一个 demo skill：从自然语言抽结构化字段

**验收**：给 schema 调一次，返回 100% 合规 JSON；`tool_choice="required"` 能强制走工具。

**插哪**：只动 `adapter.py`（CLAUDE.md 明确「换 provider 只动这一个文件」）+ 新 `agent_core/structured.py`。

---

### [ ] 3. 可观测性 + 评测（Tracing / Metrics / Eval harness）

**为什么**：大厂和创业公司都越来越看重「agent 上线后怎么调、怎么评」。只有 `context_usage`，没有 trace / eval。这是「玩具」和「生产级」的分水岭，面试讲故事的关键。

**做什么**：
- **Tracing**：每轮 / 每个 tool_use 生成 `trace_id` + `span_id`，记到 `.transcripts/` 或 Postgres 一张 `spans` 表（开始 / 结束 / 输入 / 输出 / 耗时 / token）
- **Metrics**：延迟 p50/p95、token 成本、工具成功率、按 session 聚合；一个 `/api/metrics` 端点
- **Eval harness**：`tests/evals/` 放 golden Q&A 对 + 评分（exact / LLM-as-judge），`make eval` 跑出报告

**验收**：跑一轮对话后能看到完整 span 树 + 指标；`make eval` 输出通过率 / 平均分。

**插哪**：新 `agent_core/tracing.py` + 扩 `loop.py` 的 tool 执行段埋点 + `tests/evals/`。

---

## P1 — 强差异化（补全 infra + 应用两翼）

### [ ] 4. 工作流 / DAG 编排（LangGraph 风格状态机）

**为什么**：应用岗要「审批流 / 多步业务流程」，只有对话式单 loop + teams，没有显式图编排。做完能讲「我既写了 reactive agent loop，又写了 graph workflow 引擎」——很强的叙事。

**做什么**：新 `agent_core/workflow.py`：节点（agent 步骤或工具）、边（含条件路由）、共享 state 对象、run 引擎；一个 `run_workflow` 工具；一个 demo：三节点报销审批流。

**验收**：定义一个条件分支 workflow，跑通；和单 loop 的区别在 README 讲清楚。

**插哪**：新 `workflow.py` + `tools.py` 注册。

---

### [ ] 5. 多模态（图片 / PDF 输入）

**为什么**：2025 热门技能，JD 常写「多模态 / 视觉理解」。现在图片块直接丢弃（`[image omitted]`）。

**做什么**：`read_file` 支持图片 → 转 base64 image content block；`adapter._to_openai_messages` 把 image block 喂给视觉模型（GLM-4V / GPT-4o）；PDF → 按页抽图或文本。

**验收**：丢一张截图问「这图里有什么」，视觉模型能答。

**插哪**：`tools.py` 的 `run_read_file` + `adapter.py` + `blocks.py`。

---

### [ ] 6. k8s 部署 + Helm Chart + Healthcheck

**为什么**：大厂岗要生产部署成熟度。有 docker-compose，补 k8s 是自然延伸。

**做什么**：`deploy/k8s/` 一套 manifests + Helm chart（gateway / frontend / postgres / redis），compose 加 healthcheck + `condition: service_healthy`。

**验收**：`helm install` 起得来，`kubectl get pods` 全 Ready。

**插哪**：新 `deploy/` 目录 + 改 `docker-compose.yml`。

---

### [ ] 7. 前端三件套（流式 token 渲染 + tool-card diff + slash 菜单）

**为什么**：全能作品的 demo UI 在面试现场很加分。

**做什么**：ChatPanel token-by-token 流式；`edit_file` / `write_file` 的 before/after diff 视图；`/` 键弹出可搜索命令菜单。

**验收**：打字时逐字出现；编辑工具卡片能看红绿 diff；`/` 弹菜单可键盘选。

**插哪**：`frontend_vite/src/components/` + `features/`。

---

## P2 — 打磨（时间够再做）

| # | 状态 | 功能 | 价值 | 工作量 |
|---|------|------|------|--------|
| 8 | [ ] | `edit_file` `replace_all` + multi-edit | gap 文档 P1，小补丁 | 半天 |
| 9 | [ ] | A2A 多 agent 互操作协议 | 2025 新兴（Google A2A），infra 岗差异化 | 2-3 天 |
| 10 | [ ] | 成本 / 预算控制（per-session token 上限 + 成本面板） | 配合可观测性，大厂爱问「怎么控成本」 | 1-2 天 |
| 11 | [ ] | `read_file` PDF 支持 | gap 文档 P2 | 半天 |
| 12 | [ ] | nginx gzip + HTTPS 443 块 | 部署打磨 | 半天 |

---

## 实现顺序（按依赖 + 见效）

```
第1周: P0-1 RAG (pgvector)              ← 改变项目性质，最高优先
第2周: P0-2 结构化输出 + P0-3 可观测性/eval  ← 可并行，都只动 adapter/loop 边缘
第3周: P1-4 工作流DAG + P1-5 多模态        ← 可并行
第4周: P1-6 k8s + P1-7 前端三件套          ← 收尾，demo 用
```

## 面试叙事

把 README 头部从「自研 coding agent」改成：

> 自研 Agent 平台：同时支持 reactive loop（对话式）和 graph workflow（流程式），内置 RAG / 结构化输出 / 可观测性，OpenAI 兼容多 provider。

这套话术同时打动应用岗和 infra 岗的面试官。

---

## 已完成的基础（不在本清单内）

以下能力已实现，无需重做：

- ✅ `web_fetch` / `web_search` 工具（`tools.py` schema + handler）
- ✅ Plan mode（`agent.plan` — read-only → submit → approve → execute）
- ✅ `grep` 工具（ripgrep wrapper）
- ✅ `/api/health` + `/api/version` 端点
- ✅ API 版本化（`/api/v1/*` 透明路径重写）
- ✅ X-Request-ID 中间件
- ✅ 企业级后端结构（app 工厂 / routes / services / middleware / 异常层级）
- ✅ 团队协作模块企业级拆分（team_types / team_state / team_events / team_protocol / team_prompts / teammate_loop）
