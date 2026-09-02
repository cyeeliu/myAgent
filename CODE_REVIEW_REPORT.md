# 代码审查与优化报告

**审查范围：** `agent_core/`（~41 模块）、`agent_gateway/`（FastAPI 网关）、`frontend_vite/`、`docker-compose.yml`、`nginx.conf`、`_split.py`
**审查方式：** 全栈逐文件精读 + 6 个并行子代理分域深审 + 人工交叉验证关键发现
**审查结论：** 仅输出报告，未修改任何代码

---

## 概述

本次审查共发现 **约 120 项问题**，其中 **7 项严重（Critical）**、**16 项高危（High）**、**约 47 项中等（Medium）**、**约 50 项低危（Low）**。最核心的问题集中在四个方面：

1. **远程安全**：网关无任何认证，`/file-api` 路径穿越可读取原始 API Key；`session_id` 路径穿越可逃逸沙箱；模型配置可被劫持重定向 LLM 流量。**这些问题当前即可被远程利用**（nginx 在 :80 暴露 `/file-api/` 和 `/ws`，无认证）。
2. **数据完整性**：压缩（compaction）就地变异共享 block 字典，直接腐蚀只读的持久化 `record`；记忆合并（consolidate）在 LLM 返回空数组时删除全部记忆。
3. **功能失效**：cron 调度器从未启动，整个定时任务机制是死代码；checkpoint 单例跨会话污染撤销历史。
4. **安全边界**：`web_fetch` 无 SSRF 防护可泄露云凭证；subagent 绕过权限检查；plan-mode bash 门可被解释器绕过。

---

## 🔴 严重（Critical）

### C1 — 压缩就地变异共享 block，腐蚀持久化 record
**文件：** `agent_core/compaction.py:87-88`（`tool_result_budget`）、`compaction.py:114-116`（`micro_compact`）；根因 `agent_core/session.py:129-130`（`append_both`）
**类别：** 数据腐蚀 / 不变式破坏

`Session.append_both` 将**同一个** `msg` 字典对象追加到 `record` 和 `context_messages` 两个列表，因此二者的嵌套 block 字典是**同一对象**。而两个压缩函数对 `context_messages` 中的 block **就地变异**：

```python
# compaction.py:87-88
block["content"] = persist_large_output(...)   # tool_result_budget
# compaction.py:116
block["content"] = "[Earlier tool result compacted. Re-run if needed.]"  # micro_compact
```

由于 block 共享引用，变异同时作用于 `record`。`micro_compact` 尤其 destructive——直接用占位字符串替换原始内容且**不做任何持久化**，原始工具输出永久丢失。

**直接违反 CLAUDE.md 核心不变式**：*"All compaction mutates `session.context_messages` only — never `session.record`"*。record/context 分离正是为防止此类腐蚀而引入，但这两个函数通过共享可变对象击穿了它。

**影响：**
- 持久化 chat record（`history.json`、Postgres `chat_record`、replay）包含压缩占位符而非原始输出
- `synthesize_frames(record)`（replay.py）向重连客户端回放被腐蚀的占位符
- `extract_memories` 从 `record` 读取，看到占位符而非真实工具输出，丢失记忆信息
- `snip_compact`（line 106）虽创建新列表但保留对幸存 block 的引用，后续 `micro_compact` 仍腐蚀 record 中的幸存 block

**建议修复：** `tool_result_budget` 和 `micro_compact` 必须构建**新的**消息字典 + 新 content 列表 + 新 block 字典，绝不就地变异。或更简单但更耗内存：`append_both` 对 `context_messages` 追加 `copy.deepcopy(msg)`，使两列表永不共享可变对象。

---

### C2 — cron 调度器从未启动，整个定时任务机制是死代码
**文件：** `agent_core/cron.py:147`（`cron_scheduler_loop` 定义但从未启动）、`cron.py:112`（`load_durable_jobs` 定义但从未调用）
**类别：** 功能失效

`cron_scheduler_loop`（评估任务到点并推入 `cron_queue`）和 `load_durable_jobs`（启动时从 `.scheduled_tasks.json` 恢复持久化任务）均已定义并导出，但**全代码库无任何调用点**。实际启动的是 `cron_autorun_loop`（loop.py:374，cli.py:129），它仅消费 `cron_queue`——而该队列永远为空，因为无人向其中推入到点任务。

**影响：** 用户通过 `schedule_job` 接受的 cron 任务永不触发；重启后持久化任务静默丢失。整个 cron 子系统是不工作的。

**建议修复：** 在启动 `cron_autorun_loop` 的同一位置启动 `cron_scheduler_loop` 为守护线程，并在启动前调用 `load_durable_jobs()` 恢复持久化任务。

---

### C3 — checkpoint 管理器单例，跨会话撤销历史污染
**文件：** `agent_core/checkpoint.py:206`（`manager = CheckpointManager()` 模块级单例）
**类别：** 跨会话状态污染

`CheckpointManager` 的 `_stack`/`_current`/`_named`/`_loaded` 是实例属性，但实例本身是模块级单例，所有会话共享同一内存状态。注释（line 202-205）声称"通过 `session_dir()` 实现会话隔离"——但 `_loaded` 在首次加载后全局置 True，第二个会话复用第一个会话的内存栈，`undo()` 在会话 B 中恢复会话 A 的文件。

**影响：** 多会话网关中，一个会话的撤销操作腐蚀另一个会话的文件状态。

**建议修复：** 改为 per-session 实例（按 `session_id` 索引的字典），或在 `_ensure_loaded` 中按 `session_dir()` 的 persist path 校验当前加载的会话是否匹配，不匹配则重新加载。

---

### C4 — `_split.py` 若运行将摧毁整个包
**文件：** `_split.py`
**类别：** 破坏性脚本

`_split.py` 读取 `code.py` 做 AST 拆分。但 `code.py` 现已是 re-export 门面（facade），实际逻辑在 41 个手编模块中。若运行 `_split.py`，它会从门面重新生成并**覆写全部 41 个手编模块**，丢失所有手工修改。CLAUDE.md 警告"先从 git 恢复 code.py"，但脚本本身无任何防护。

**建议修复：** 在 `_split.py` 顶部加入防护：检测 `code.py` 是否为门面（如检查文件大小 < N 行或缺少关键函数体），若是则拒绝运行并提示。或将其移至 `scripts/` 并加 `--force` 确认参数。

---

### C5 — `web_fetch` 无 SSRF 防护，可泄露云凭证
**文件：** `agent_core/tools.py:432-472`
**类别：** 服务端请求伪造（SSRF）

`run_web_fetch` 对 URL 无任何主机校验。LLM 选择 URL（可被 prompt injection 误导），`urllib.request.urlopen` 跟随重定向且无 allowlist。可请求 `http://169.254.169.254/latest/meta-data/iam/...`（云 IAM 临时凭证）、`http://localhost:8000/admin`（内部服务）、RFC1918 私网地址，响应体返回给模型。

**影响：** 云元数据端点 → 临时凭证泄露 → 账户接管。此工具不经 `sandbox.py`，沙箱不提供任何防护。

**建议修复：** 解析主机名并拒绝 loopback（127/8, ::1）、link-local（169.254/16, fe80::/10）、private（10/8, 172.16/12, 192.168/16, fc00::/7）、multicast/broadcast、`0.0.0.0`、`169.254.169.254`；重定向后再次校验（或禁用重定向逐跳检查）。

---

### C6 — `consolidate_memories` 在 LLM 返回 `[]` 时删除全部记忆
**文件：** `agent_core/memory.py:383-401`；加剧于 `agent_core/loop.py:201-204`
**类别：** 数据丢失

删除循环（line 383-388）在写入循环（390-401）**之前**执行，且仅以 `json.loads` 成功为条件——不以 `items` 非空为条件。若 LLM 返回合法空数组 `[]`（合理场景：prompt 说"移除过时记忆"，模型可能判定全部过时或以 `[]` 为安全默认值），所有 `*.md` 文件被 unlink，写入循环迭代空列表——全部记忆永久删除，无备份、无确认。

更严重：`loop.py:201-204` 中 `extract_memories` 先写入新文件，同一后台运行中 `consolidate_memories` 随即将其全部删除。

**建议修复：** 在 unlink 前加 `if not items: return`。更好：先写新文件到临时名，再删除不在新集合中的旧文件，最后 rename——原子替换而非 delete-all-then-rewrite。

---

### C7 — `/file-api` 路径穿越读取任意文件（含 API Key），无认证远程可利用
**文件：** `agent_gateway/routes/file_api.py:24-55`（`_resolve_under_root`）、`file_api.py:109-129`（`file_api_file_content`）
**类别：** 路径穿越 / 敏感文件泄露

`_resolve_under_root` 将 `agent/workspace/...` 前缀约束到工作区根，但**所有其他路径**落入通用分支，仅约束到 `_FILE_API_ROOT = REPO_ROOT`（整个仓库/应用目录）。请求 `GET /file-api/file-content?path=.agents/model.json` 解析到 `REPO_ROOT/.agents/model.json` 并返回内容——该文件存储**原始 OpenAI API Key**（`model_config.py:22`: `_CONFIG_PATH = REPO_ROOT / ".agents" / "model.json"`）。同理 `path=.env`（DB_URL/REDIS_URL/密钥）、`path=agent_gateway/config.py`、`path=code.py` 均返回源码/密钥。`nginx.conf:61` 将 `/file-api/` 代理到网关公共端口 80，且**无任何认证**（见 H13）。

**影响：** 任意未认证调用者可获取全部密钥（API Key、DB URL、Redis URL）和源码。**当前即可远程利用。**

**建议修复：** 白名单允许的根目录——将 `file-content`/`list-files` 仅约束到 `SESSION_FILES_ROOT` 和 `workspace_root`，其余一律 403。不以 `REPO_ROOT` 为约束边界。加认证。

---

## 🟠 高危（High）

### H1 — subagent 绕过权限检查
**文件：** `agent_core/subagent.py:80-86`
**类别：** 安全绕过

`spawn_subagent`（line 80）仅调用 `trigger_hooks("PreToolUse", block)`，**从不调用 `check_permission()`**。主循环（loop.py）对每个 tool_use 先过 `check_permission`。subagent 因此绕过 deny-list、路径逃逸检查、破坏性命令门、policy 门。若 subagent 工具集包含 `bash`，prompt-injected lead 可生成 subagent 执行 `rm -rf` 等命令。

**建议修复：** 在 `spawn_subagent` 的 tool 执行前调用 `check_permission`（与 loop.py 一致），被拒绝时将拒绝原因作为 tool_result 返回。

### H2 — tool 结果在 skip/exec 交错时乱序发送给 LLM
**文件：** `agent_core/loop.py:273-281`（Phase 2 skip）vs `loop.py:353-359`（Phase 3 exec 组装）
**类别：** 逻辑错误

skip 动作结果在 Phase 2 遇到时直接 append 到 `results`；exec 动作结果在 Phase 3 按 action 顺序 append。当单次响应中 skip 与 exec 交错（如一个工具被权限拒绝、另一个只读工具执行），最终顺序为 **[全部 skip] + [全部 exec]** 而非原始 action 顺序。

示例 `actions = [exec_0, skip_1, exec_2]` → `results = [skip_1, exec_0, exec_2]`，正确应为 `[exec_0, skip_1, exec_2]`。

**影响：** LLM 收到的 tool 结果顺序与调用顺序不一致。OpenAI API 按 `tool_call_id` 匹配故不 400，但模型看到语义错误的排序，可能混淆推理。

**建议修复：** Phase 2 不直接 append skip 结果到 `results`，改存入 `_exec_outputs[ai]`（或并行 `_skip_outputs`），Phase 3 统一按 action 顺序组装全部结果。

### H3 — 跨会话 background 任务结果泄露
**文件：** `agent_core/background.py:23-25`（`background_tasks`/`background_results` 模块级全局字典）
**类别：** 跨会话数据泄露

`background_tasks` 和 `background_results` 是模块级字典，无会话作用域。多会话网关中，一个会话的后台任务结果泄露到另一个会话的 LLM 上下文（`collect_background_results` 和 `_on_background_complete` 无差别地 pop 全局字典）。

**建议修复：** 改为 per-session 字典（按 `session_id` 索引），或在 `start_background_task`/`collect_background_results` 中传入并校验 `session_id`。

### H4 — `bg_id` 计数器竞态
**文件：** `agent_core/background.py:58-60`
**类别：** 竞态条件

`_bg_counter += 1` 在 `background_lock` 之外执行。并发 `start_background_task` 调用可产生重复 `bg_id`。

**建议修复：** 将计数器自增移入 `with background_lock` 块内。

### H5 — MessageBus `read_inbox` 读→unlink 竞态丢消息
**文件：** `agent_core/bus.py:50-89`
**类别：** 消息丢失 / 死锁

`read_inbox` 执行 read→unlink 无锁。若 `send` 在 read 和 unlink 之间追加消息，该消息被 unlink 永久丢失——可死锁团队协议握手。

**建议修复：** 使用文件锁或原子 rename（read 后 rename 文件再解析，而非 unlink）。

### H6 — MCP 子进程资源泄漏 / 无超时挂起
**文件：** `agent_core/mcp.py:63`（`_request` 的 `while True: readline()` 无超时）、`mcp.py`（`MCPClient.close()` 从不调用）、`mcp.py`（`normalize_mcp_name` 冲突静默覆写）
**类别：** 资源泄漏 / 挂起

- `_request` 的 `readline()` 无超时：MCP server 挂起时整个会话永久阻塞
- `MCPClient.close()` 从不调用：子进程在会话结束后泄漏
- 握手失败时子进程不被清理

**建议修复：** 为 `readline()` 加超时；在会话结束/`Session` 析构时调用 `close()`；握手失败时 `proc.kill()` + `proc.wait()`。

### H7 — 后台子进程退出后孤立
**文件：** `agent_core/background.py`（`start_new_session=True`）；`agent_core/tools.py:85-89`（bash 超时 SIGKILL 仅杀直接子进程）
**类别：** 资源泄漏

后台子进程以 `start_new_session=True` 启动，进程退出时无 reaping 机制，孤立为 init 子进程。bash 超时时 `subprocess.run` 仅 SIGKILL 直接子进程（`/bin/sh` 或 `bwrap`），孙进程（管道阶段、后台作业）reparent 到 init 继续运行。

**建议修复：** 用 `start_new_session=True` + 超时时 `os.killpg(os.getpgid(proc.pid), SIGKILL)` 杀整个进程组；后台任务注册 atexit/信号清理。

### H8 — 记忆目录并发无锁竞态
**文件：** `agent_core/memory.py:126-136, 357-401` + `agent_core/loop.py:196-208`
**类别：** 竞态 / 数据丢失

`.memory/` 跨会话共享（per CLAUDE.md）。`extract_memories` 和 `consolidate_memories` 在 fire-and-forget 守护线程中运行，无锁。两个网关会话的后台线程对同一目录竞态：consolidate 的 delete-all 可删除另一会话 extract 刚写入的记忆；并发 `load_memories` 可观察到删除中途的空目录。

**建议修复：** 用 `threading.Lock`（或跨进程文件锁）序列化所有记忆变异；consolidate 改为原子 write-new-then-delete-old。

### H9 — consolidate 无迟滞，≥10 记忆后每轮触发
**文件：** `agent_core/memory.py:357-374`
**类别：** 性能 / 重复破坏性操作

`CONSOLIDATE_THRESHOLD = 10`，门控为 `if len(files) < 10: return`。consolidate prompt 告诉 LLM "keep under 30"。若 consolidate 产生 10-29 条记忆，下一轮 `len(files) >= 10` 仍为真，consolidate 再次运行——每轮一次 LLM 往返 + delete-all + rewrite-all，永久循环。结合 C6/H8，10+ 记忆工作区每轮对共享目录执行破坏性 delete-all。

**建议修复：** 迟滞：`>= HIGH_WATER`（如 20）时 consolidate，降至 `LOW_WATER` 以下前不再触发；或仅在计数较上次 consolidate 增长时触发。

### H10 — skills 安装/导入路径穿越
**文件：** `agent_core/skills.py:116-140, 367-385, 388-417`
**类别：** 路径穿越

`resolve_install_dst` 直接用 slug 作路径组件（`base = skills_dir / slug`，line 129），无穿越检查。`install_skill` 的 `name = spec.split("@")[0].strip()`（line 374）仅 `.strip()` 无消毒。三者均由网关 WS handler 直接调用（`agent_gateway/common/e2a/handlers/skills.py:48,54,66`），参数用户可控。`install_skill("../../etc/evil@builtin")` 或上传 `name: ../../etc/evil` 的 SKILL.md → `dst = skills_dir/../../etc/evil`，`mkdir(parents=True)` + `copytree` 写入 skills 目录外。`uninstall_skill` **有** `relative_to` 防护（332-335），证明作者知晓风险——但 install/import 没有。

**建议修复：** 在 `resolve_install_dst` 中拒绝含 `..`/`/`/`\` 的 slug，或校验 `resolve()` 后仍在 `skills_dir` 内（镜像 `uninstall_skill` 的 `relative_to` 防护）。

### H11 — plan-mode bash 门可被解释器绕过
**文件：** `agent_core/hooks.py:29-39, 102-114`
**类别：** 安全绕过 / plan-mode 完整性

plan-mode bash 门是纯子串匹配 `_PLAN_MODE_BASH_DENY`。遗漏不匹配任何子串的解释器变异：`python -c "open('x','w').write('y')"` 和 `node -e "require('fs').writeFileSync('x','y')"` 不含任何 deny 模式但写文件，静默击穿 plan-mode 只读保证。plan-mode 工具池隐藏 `write_file`/`edit_file` 但保留 `bash`，此门是唯一执行只读 bash 的机制。

**建议修复：** 子串匹配无法根本修复。选项：(a) plan-mode 中完全禁用 `bash`；(b) 解析命令并拒绝任何解释器调用（`python`/`node`/`perl`/`ruby`/`bash -c`/`sh -c`/`eval`）；(c) plan-mode 下只读 bind mount。至少将 `python -c`/`python3 -c`/`node -e`/`perl -e`/`ruby -e`/`bash -c`/`sh -c`/`eval`/`npm i`（裸形式）加入 deny 列表。

### H12 — `MODEL_ID` 缺失时硬 KeyError
**文件：** `agent_core/env.py:70`；`agent_core/model_config.py:48`
**类别：** 启动失败

`MODEL = os.environ["MODEL_ID"]`（env.py:70）和 `f.get("model_id") or os.environ["MODEL_ID"]`（model_config.py:48）在变量缺失时抛 `KeyError`，而非有意义的错误消息。`MODEL_ID` 虽标注必需，但失败是 per-turn `model()` 调用内的晦涩 traceback。

**建议修复：** `mid = os.getenv("MODEL_ID"); if not mid: raise RuntimeError("MODEL_ID not set — required env var")`。

### H13 — 网关无任何认证
**文件：** `agent_gateway/middleware.py:29-59`（仅 CORS + request-ID）、所有 `routes/*.py`、`channel_manager/web/web_connect.py:78-89`
**类别：** 安全 / 缺失认证

无任何 auth 中间件、依赖或 token 检查。每个端点——创建/删除会话、读写文件、修改模型配置（含 API Key）、验证模型、安装技能、`/file-api/*`——全部开放。CORS 为 `allow_origins=["*"]`（middleware.py:33）。

**影响：** 任何可达客户端可泄露密钥（C7）、重定向 LLM 流量（H16）、删除全部会话、执行 SSRF（H14）。

**建议修复：** 添加 auth 依赖（API Key header / session token）应用于所有 router + WS 端点。CORS 收紧至已知来源。

### H14 — `config.validate_model` SSRF
**文件：** `agent_gateway/common/e2a/handlers/config.py:81-87`
**类别：** SSRF

`api_base` 直接取自请求参数用作 OpenAI client base_url：`OpenAI(base_url=api_base, ...).models.list()`。攻击者可设 `api_base=http://169.254.169.254/`（云元数据）或任意内部服务，网关发出出站 HTTP 请求。结合无认证（H13），为未认证 SSRF。

**建议修复：** 将 `api_base` 校验至已批准 provider URL 白名单；拒绝私网/loopback/link-local IP。要求认证。

### H15 — `session_id` 路径穿越，任意文件写入 + CWD 逃逸
**文件：** `agent_gateway/sessions/manager.py:110-133`（`create` 无 sid 校验）、`manager.py:48`（`agent.workdir = SESSION_STATE_ROOT / sid`）、`sessions/files.py:62`（`out = SESSION_FILES_ROOT / sid`）、`common/e2a/handlers/chat.py:19-21`
**类别：** 路径穿越 / 沙箱逃逸

`session_id` 从不校验路径安全字符。客户端发送 `session_id = "../../../../etc/cron.d"` → `agent.workdir = SESSION_STATE_ROOT / "../../../../etc/cron.d"` → 解析为 `/etc/cron.d`。agent 的 `bash`/`read_file`/`write_file` 以该 CWD 运行，`_write_session_files` 写 `transcript.md`/`history.json` 至该处。`cleanup_session_artifacts`（cleanup.py:14）有防护，但创建/build 路径**没有**。

**影响：** 沙箱逃逸——agent 在工作区外操作；容器内任意文件写入。

**建议修复：** 在入口点校验 `sid`（含 `/`/`\`/`..` 时拒绝，或要求匹配 `[A-Za-z0-9_-]+`）。应用 `cleanup_session_artifacts` 已有的防护。

### H16 — 未认证模型配置写入 → LLM 流量重定向 + API Key 泄露
**文件：** `agent_gateway/common/e2a/handlers/config.py:39-63`（`config_save_all` → `model_config.write_models`）、`routes/models.py:18-24`（`update_models`）
**类别：** 安全 / 提权

无认证（H13）下，攻击者可 `PUT /api/models` 或 `config.save_all` 设恶意 `base_url` 指向其服务器。agent 随后将所有 LLM 请求（含 `Authorization` header 中的 API Key）发至攻击者端点。

**影响：** API Key 泄露 + 流量拦截。

**建议修复：** 要求认证（H13）；将 `base_url` 校验至白名单。

---

## 🟡 中等（Medium）

> 以下按主题分组，附文件:行号。

### 并发与竞态
| # | 文件:行 | 描述 |
|---|---------|------|
| M1 | `agent_gateway/sessions/manager.py:142-155` | `get_or_hydrate` check-then-act 竞态：锁在 line 145 释放后做 DB load + build（无锁），line 153 重新加锁存储。两个并发请求同 sid 均缓存未命中→均 build→后者覆写前者，先建会话及其 worker 线程被孤立 |
| M2 | `agent_gateway/routes/sessions.py:32-35` | 空闲驱逐竞态：`_maybe_cleanup` 读 `gs._worker` 无 `_worker_lock`，check 与 drop 之间 `post_message` 可启动 worker，孤立在途 turn |
| M3 | `agent_core/session.py:123-135` | `append_both` 不持 `session.lock`；并发 append 可使 record 与 context_messages 顺序不一致 |
| M4 | `agent_core/loop.py:190` | 记忆后台线程的 `record_snapshot = list(session.record)` 是浅拷贝，共享 block 字典，compaction 可并发变异（C1 的衍生） |
| M5 | `agent_core/permissions.py:38, 94-125` | policy.json 无跨进程锁；多副本 read-modify-write 竞态（last-write-wins）；`_write` 用 `write_text` 非原子 rename |
| M6 | `agent_core/bus.py` | JSONL append 交错（多线程 send 无文件锁） |

### 错误处理
| # | 文件:行 | 描述 |
|---|---------|------|
| M7 | `agent_core/loop.py:265` | 显式 `compact` 工具调用无 try/except（不像 `reactive_compact` 有防护）；API 失败抛未处理异常崩溃整个 turn |
| M8 | `agent_core/adapter.py:214-275` | 流式循环无 try/except；中途 API 错误原始抛出，已发 token 事件孤立，部分 `text_parts`/`tool_calls` 丢弃无部分结果返回 |
| M9 | `agent_core/memory.py:184-199, 311-328, 366-381` | `_memory_llm` 失败静默吞掉（返回 `""`），无任何日志，记忆静默停止提取/合并时无诊断信号 |
| M10 | `agent_core/hooks.py:57-70` | `_ask` 在 resolver 抛异常时仍无条件 emit `permission_request`，产生孤立权限弹窗（request_id 未注册，用户点击 Allow 无响应） |

### 资源 / DoS
| # | 文件:行 | 描述 |
|---|---------|------|
| M11 | `agent_core/tools.py:147` | `read_file` 先 `read_bytes()` 全文加载再应用 offset/limit；多 GB 文件 OOM |
| M12 | `agent_core/tools.py:799-826` | `grep` 逐文件全文 `read_text()` 无大小上限 + LLM  supplied regex 无超时（ReDoS 灾难性回溯可挂起 turn） |
| M13 | `agent_core/tools.py:85-89` | bash 超时 SIGKILL 仅直接子进程，孙进程孤立（见 H7） |
| M14 | `agent_core/adapter.py:188-275` | 流式路径忽略 `timeout`，依赖 SDK 默认 ~600s 无 per-call 上限 |
| M15 | `agent_core/sandbox.py:55-79` | 无 `--unshare-pid`：沙箱 bash 共享主机 PID ns，可 `kill`/枚举主机进程 |
| M16 | `agent_core/sandbox.py:55-79` | 无 `--unshare-net`（文档承认）：沙箱 bash 仍可 SSRF/exfiltrate（`curl 169.254.169.254`），沙箱仅提供文件隔离 |

### 正确性
| # | 文件:行 | 描述 |
|---|---------|------|
| M17 | `agent_core/tools.py:436-437` | `web_fetch` 无条件 http→https 升级，破坏本地 HTTP 端点（`http://localhost:8080` → TLS 握手失败） |
| M18 | `agent_core/adapter.py:158-161` | `response_format`/`tool_choice` 无条件传递，docstring 称"仅模型支持时"——doc/code 不一致，不支持的模型返回 400 |
| M19 | `agent_core/memory.py:131-133` | `write_memory_file` 将 LLM 值直接插入 YAML frontmatter 无转义；含换行/`---`/引号的值腐蚀 frontmatter，reload 时字段静默截断 |
| M20 | `agent_core/memory.py:129` | slug 碰撞静默覆写（`name.lower().replace(" ","-")` 将不同名折叠为同文件名） |
| M21 | `agent_core/hooks.py:29-39` | plan-mode bash 门假阳性拒绝合法只读命令（`docker ps`、`tar -tf`、`curl -X GET`、含字面 `>` 的命令、`echo "warm"` 匹配 `"rm "`） |
| M22 | `agent_core/hooks.py:21-23` | DENY_LIST 子串匹配可被双空格/Tab 绕过（`rm -rf  /`）；`"sudo"` 匹配 `"pseudo"` |
| M23 | `agent_core/prompt.py:18` | `PROMPT_SECTIONS["workspace"]` 在 import 时求值，`set_workspace_dir()` 后变陈旧 |
| M24 | `agent_core/prompt.py:40, 212, 247` | `_SECTION_CACHE` 模块级单条目，不同 MCP 工具池的会话互相驱逐→每轮缓存未命中 |
| M25 | `agent_core/tasks.py:137-139` | `list_tasks` 无错误处理，单个损坏 task 文件使整个列表不可读 |
| M26 | `agent_core/tasks.py:93` | `task_id` 路径穿越（`_tasks_dir() / f"{task_id}.json"` 无消毒，`../evil` 逃逸 `.tasks`） |
| M27 | `agent_core/permissions.py:94-105` | `get_policy()` 每次 tool 调用都读+解析 `policy.json`，无 mtime 缓存；5 个工具的 turn 做 10-15 次策略读 |
| M28 | `agent_core/skills.py:143-218` | `scan_skills` 每次调用全量重建 `SKILL_REGISTRY`；`load_skill` 每次技能调用全量遍历+读取所有 SKILL.md，无缓存 |
| M29 | `agent_core/compaction.py:92-108` | `snip_compact` 产出 `max_messages+1` 条目（head 3 + marker 1 + tail (max-3)），边界 case 无缩减 |
| M30 | `agent_core/tools.py:338-348` | `apply_diff` 计算 `context_before/removed/added/expected` 后未使用（死代码）；宽泛回退搜索取首个匹配区，重复上下文可补错位置 |
| M31 | `agent_core/tools.py:841-847` | `call_tool_handler` 仅 catch `TypeError`；其他 handler 异常传播崩溃 turn |
| M32 | `agent_core/tools.py:1193 vs 1393` | `compact` 有 schema 无 handler（intentional，loop.py:230 短路），但破坏"并行显式表"不变式 |
| M33 | `agent_core/adapter.py:40` | `tool_result` content `str()` 强制转换；list/None content 产生 Python repr / `"None"` |
| M34 | `agent_core/adapter.py:178, 301` | 未知 `finish_reason`（`content_filter`/`None`）原样透传，可能致空 turn 自旋 |
| M35 | `agent_core/adapter.py:215` | `interrupted` 仅在 chunk 间检查；长 TTFT 在首个 token 前阻塞中断 |
| M36 | `agent_core/adapter.py:245` | `context_usage` emit 每次 `"".join(text_parts)` 重拼全部累积 token（O(n²)，受节流限制故不致命） |
| M37 | `agent_core/model_config.py:49` | `base_url` 静默 `None` → `OpenAI()` 默认指向公共 api.openai.com，错误配置静默打错端点 |
| M38 | `agent_core/hooks.py:88-94, 136-142` | policy=ask 且目标文件存在时双重权限弹窗（policy 门 + overwrite 回退门） |
| M39 | `agent_core/hooks.py:136-144` | overwrite 检查异常时 `except: pass` 跳过确认，默认"允许" |
| M40 | `agent_core/tools.py:157-163` | 负 `limit` 在 `read_file` 中误切片（`lines[:limit]` 丢尾部）且打印错误的"more lines"计数 |

### 网关并发与逻辑
| # | 文件:行 | 描述 |
|---|---------|------|
| M41 | `agent_gateway/sessions/gateway_session.py:185-225` | delete-while-turn-in-flight：`delete_session` 调 `interrupt()` + `db.delete_session_row()`，但 worker 异步退出时 `_run_turn` finally 的 `save_chat_record` upsert **重建**刚删的 DB 行 + 磁盘文件——已删会话静默重现 |
| M42 | `agent_gateway/sessions/gateway_session.py:214-225` | pending-message 丢失竞态：finally 释放 `_worker_lock` 后调 `post_message(pending)` 重新加锁，窗口期内并发 `post_message` 可抢占→pending 被 `return False` 静默丢弃 |
| M43 | `agent_gateway/common/e2a/handlers/chat.py:62-68` | `chat.send` 未带 `mode` 时静默退出 plan mode（`req.mode is None` → pop `plan_mode` + 覆写 DB mode 为 `agent.fast`）；REST `POST /messages` 不碰 mode→两输入路径行为分歧 |
| M44 | `agent_gateway/services/agent_data.py:51` | `rebuild_agent_data` 的 `ws_root.rglob("*")` 无深度/数量上限；`node_modules` 或 symlink 循环致无限递归/OOM；启动时运行 |
| M45 | `agent_gateway/services/agent_data.py:36-46` | 种子 `.memory/config.json`——违反 CLAUDE.md 明确不变式*"there is no config.json; do not seed one"* |
| M46 | `agent_gateway/channel_manager/web/web_connect.py:311` + `ws.py:75` + `sse.py:34` | `pipe.replay_since` 同步 Redis XRANGE 在 async generator 中直接调用，无 `asyncio.to_thread`→大量缓冲帧时阻塞事件循环；`config.validate_model` 同步 OpenAI HTTP（30s）在 async handler 中阻塞所有请求 |
| M47 | `agent_gateway/common/e2a/handlers/files.py:18-24` | `files.list` 的 `os.listdir(path)` 中 `path` 直接取自请求参数，无约束→可列举 `/etc`/`/`/`/proc`（文件系统枚举/信息泄露） |

---

## 🟢 低危（Low）

> 简表，按文件分组。

**`agent_core/loop.py`：** 广义 `except Exception: pass` 吞掉编程错误（line 81-82, 42-43, 205-206, 343-344）——应缩窄至预期失败类型或至少 log。

**`agent_core/session.py`：** `record_sinks`/`sinks` fan-out 的 `except Exception: pass`（134-135, 157-158）——Redis sink 故障静默丢事件无日志。

**`agent_core/context.py:11-28`：** `update_context` 的 `messages` 参数未使用（死参数）。

**`agent_core/blocks.py:36-48`：** `extract_text`/`has_tool_use` 用 `getattr` 不处理 dict block（latent——当前调用点均传 SimpleNamespace，但 hydrate 后的 dict 会静默返回 `""`/`False`）。

**`agent_core/tools.py`：**
- `safe_path`（23-30）resolve `path` 不 resolve `base`→workdir 含 symlink 分量时误拒合法文件
- `run_bash` `cwd`（line 80）不经 `safe_path`（当前 schema 不暴露 cwd，非 LLM 可达）
- `list_dir`/`glob`（562/534）无条目/遍历上限
- `show_widget`（771-777）`NamedTemporaryFile(delete=False)` 临时文件永不清理

**`agent_core/model_config.py`：**
- `write_config`/`write_models`（126, 219）非原子写（无 temp+rename）
- `get_config_masked`（92-107）省略 `language`/`output_style`，UI 无法读写

**`agent_core/permissions.py:42, 184-193`：** `_regex_cache` 无界增长（模式变更时旧编译累积）；多线程无锁访问（GIL 下实际安全）。

**`agent_core/prompt.py:269-273`：** `team_mode` 名称插入 system prompt 无转义（prompt injection 向量，来源可信故风险低）。

**`agent_core/tasks.py`：**
- `_shape_todos`（62）index-based id 在重排时不稳定→React 重 mount 闪烁
- `create_task`（122）同秒+同 4 位随机 id 碰撞（1/10000）→ `save_task` 静默覆写

**`agent_core/memory.py:37-48`：** frontmatter 解析手写且与 `skills.py` 不一致（memory 手写 k:v，skills 用 `yaml.safe_load`）。

**`agent_core/mcp.py`：** `normalize_mcp_name` 冲突静默覆写（两个 server 同名 tool 后者覆盖前者无警告）。

**`agent_core/sandbox.py:36`：** `/etc` ro-bind 暴露主机 passwd/hosts（标准，容器内影响极小）。

**`agent_gateway/gateway_push/wire.py`：** `_TOOL_NAME_CACHE` 模块级字典无锁（多线程访问，GIL 下实际安全）。

**`agent_gateway/sse.py:65`：** `int(last_seq_hdr)` 对畸形 Last-Event-ID header 抛 ValueError→500（应 try/except 归零）。

**`agent_gateway/sessions/gateway_session.py`：** `_on_background_complete`（260-280）与 `_run_turn` finally 的 worker 启动逻辑虽加锁序列化，但 `collect_background_results` 持 `_worker_lock` 时调用（持两锁，无死锁但增加锁竞争）。

**网关补充低危：**
- `middleware.py:33`：CORS `allow_origins=["*"]` 硬编码，忽略 `GatewayConfig.cors_origins`（config.py:68 字段为死代码）
- `channel_manager/web/web_connect.py:232-235`：未知 WS method 返回 `{ok:true}` 静默成功（掩盖 typo/真正不支持的方法）
- `pipe.py:290`：`RedisContextStore.snapshot` 用 `default=str` 序列化 SimpleNamespace→字符串化而非归一化为 dict（当前 write-only 故 latent，读回则上下文腐蚀）
- `sessions/gateway_session.py:245-247`：`interrupt()` 读 `self._worker` 无 `_worker_lock`（仅 debug log，GIL 下原子，可能记陈旧值）
- `routes/sessions.py:24-35`：空闲驱逐仅在 `POST /api/sessions`/`GET /api/sessions` 时触发，无定期后台任务→无调用时 idle 会话无限累积
- `ws.py:75-83`：legacy `handle_ws` 不跳过 replay `token` 帧（新 `/ws` drain 跳过），legacy 客户端可能双重渲染
- `db.py:72`：`DO $$` 迁移 backfill `UPDATE ... WHERE llm_context = '[]'` 每次启动全表扫描（幂等但大表启动慢）
- `pipe.py`：`RedisStreamPipe` TTL 每 64 次 publish 才刷新；<64 事件后 idle → 流在 seed 后 24h 过期而非最后事件后 24h
- `channel_manager/web/web_connect.py:211, 286-302`：`_handle_legacy` 用 `_receiver` 启动时捕获的陈旧 `bound_sid`，session switch 后 legacy 帧仍路由旧会话
- `config.py:100`：`settings = GatewayConfig()` import 时读 env，后续设 env 无效（测试须 monkeypatch）

---

## 🏗 基础设施 / 部署安全

### 网关认证（最高优先）
| 严重度 | 问题 |
|--------|------|
| **Critical** | **网关无任何认证**（`middleware.py:29-59` 仅 CORS + request-ID）。nginx 在公共 :80 暴露 `/api/*`、`/ws`、`/file-api/*`，全部端点开放——这是 C7/H14/H15/H16 远程可利用的根因。**部署前必须加认证。** |

### `docker-compose.yml`
| 严重度 | 行 | 问题 |
|--------|-----|------|
| High | 81-85 | `seccomp=unconfined` / `apparmor=unconfined` / `cap_add: SYS_ADMIN`——容器几乎无隔离，SYS_ADMIN 可挂载主机文件系统 |
| High | — | 硬编码 `postgres/postgres` 凭据（环境变量未参数化） |
| Medium | — | 无内存/CPU 限制（`mem_limit`/`cpus` 缺失），单容器 OOM 可拖垮主机 |
| Medium | — | gateway/frontend 无 healthcheck，docker 无法感知崩溃重启 |

### `nginx.conf`
| 严重度 | 问题 |
|--------|------|
| Medium | 无 `server_tokens off`——暴露 nginx 版本号 |
| Medium | 无安全响应头（`X-Frame-Options`/`X-Content-Type-Options`/`Strict-Transport-Security`/`Content-Security-Policy`） |
| Medium | 无 `rate_limit`——API 无限流，可被滥用/DoS |
| Medium | 无 `client_max_body_size`——无请求体上限，可上传超大文件耗尽内存 |
| Low | 仅 HTTP :80，无 TLS（CLAUDE.md 注明待加 443） |

---

## 🧪 测试覆盖缺口

以下子系统**零测试覆盖**（`tests/` 仅覆盖核心事件流 + 网关基础）：
- `agent_core/lsp.py` — LSP 集成
- `agent_core/structured.py` — 结构化输出
- `agent_core/tracing.py` — 追踪
- `agent_core/checkpoint.py` — 撤销/恢复
- plan-mode 全流程（explore→approve→exit→execute）
- cron 调度（因功能本身失效，无法测试）
- 记忆 consolidate 的空数组/竞态边界
- 多会话并发（background 泄露、checkpoint 污染、idle 驱逐竞态）
- 网关安全（认证、路径穿越、session_id 校验、SSRF、配置劫持）
- 网关并发（get_or_hydrate 竞态、delete-during-turn、pending-message 丢失）

---

## 📋 优先修复建议

### P0 — 立即修复（远程可利用 / 数据丢失 / 安全）
1. **H13** 网关加认证（所有 router + WS 端点）——这是 C7/H14/H15/H16 的前置防线
2. **C7** `/file-api` 路径穿越：白名单约束到 `SESSION_FILES_ROOT` + `workspace_root`，禁止 `REPO_ROOT` 回退分支
3. **H15** `session_id` 入口校验（拒绝 `/`/`\`/`..`，要求 `[A-Za-z0-9_-]+`）
4. **H16** 模型配置写入加认证 + `base_url` 白名单
5. **H14** `config.validate_model` 的 `api_base` 加白名单/私网过滤
6. **C1** 压缩不再就地变异共享 block（构建新对象或 deepcopy）——同时解决 M4
7. **C5** `web_fetch` 加 SSRF 防护（私网/元数据 IP 过滤）
8. **C6** `consolidate_memories` 加 `if not items: return` 防空数组删除全部
9. **H1** subagent 执行前调用 `check_permission`
10. **H10** skills install/import 加路径穿越防护

### P1 — 尽快修复（功能失效 / 跨会话污染 / 沙箱）
11. **C2** 启动 `cron_scheduler_loop` + 调用 `load_durable_jobs`
12. **C3** checkpoint 改 per-session 实例
13. **C4** `_split.py` 加门面检测防护
14. **H3** background 任务改 per-session 作用域
15. **H8** 记忆目录加锁 + 原子 consolidate
16. **H11** plan-mode bash 门加解释器 deny 模式
17. **M15/M16** 沙箱加 `--unshare-pid`/`--unshare-net`
18. **M44** `rebuild_agent_data` 加深度/数量上限 + `followlinks=False`
19. **M47** `files.list` 路径约束到工作区

### P2 — 计划修复（竞态 / 资源 / 健壮性）
20. **H2** tool 结果按 action 顺序组装
21. **H5** MessageBus 原子 read
22. **H6** MCP 超时 + close + 握手清理
23. **H7** 进程组感知的 bash/后台超时清理
24. **M1/M2** 网关 get_or_hydrate + idle 驱逐加锁
25. **M41** delete-while-turn 标记 "deleting" 防 DB 行重现
26. **M42** pending-message 在锁内原子 drain-and-start
27. **M43** `chat.send` 未带 mode 时保留当前 plan mode
28. **M46** `replay_since`/`validate_model` 用 `asyncio.to_thread` 包裹
29. **M7/M8** 显式 compact + 流式循环加错误处理
30. **M11/M12** read_file/grep 加大小上限 + regex 超时
31. Docker/nginx 安全加固（去 SYS_ADMIN、加 healthcheck/限流/安全头/server_tokens off）

### P3 — 改进项（性能 / UX / 代码质量 / 不变式）
32. **M45** 移除 `.memory/config.json` 种子（违反 CLAUDE.md 不变式）
33. 策略/技能缓存（M27/M28）、prompt 缓存去抖（M24）、`_SECTION_CACHE` 按 session 索引
34. frontmatter 改 `yaml.safe_dump`/`yaml.safe_load`（M19/L1）
35. 广义 except 缩窄或加日志
36. 测试覆盖补齐（见下节）

---

*本报告由全栈代码审查生成，涵盖 ~60 个源文件，未修改任何代码。所有发现均附文件:行号以便定位。*
