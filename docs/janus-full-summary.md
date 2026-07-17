# Janus 系统全景总结

> **日期**：2026-07-16  
> **版本**：Phase 4（多轮对话 + Planner 战术层引入）  
> **状态**：✅ 生产就绪（17/17 issue 已修复，15 个非阻塞缺口待改善）

---

## 目录

- [1. 项目概况](#1-项目概况)
- [2. 架构概览——军师/参谋/士兵/督察四层模型](#2-架构概览)
- [3. 文件清单](#3-文件清单)
- [4. 四层角色详解](#4-四层角色详解)
  - [4.1 Gatekeeper（军师/战略决策层）](#41-gatekeeper军师战略决策层)
  - [4.2 Planner（参谋/战术执行层）](#42-planner参谋战术执行层)
  - [4.3 Worker（士兵/工具执行层）](#43-worker士兵工具执行层)
  - [4.4 Reviewer（督察/独立审计层）](#44-reviewer督察独立审计层)
- [5. 数据协议（protocol.py）](#5-数据协议)
- [6. 基础设施层](#6-基础设施层)
  - [6.1 Session（会话管理）](#61-session会话管理)
  - [6.2 TaskManager（任务状态机）](#62-taskmanager任务状态机)
  - [6.3 Console（被动观察者）](#63-console被动观察者)
  - [6.4 prompts.py（共享提示词）](#64-promptspy共享提示词)
- [7. Worker 工具系统](#7-worker-工具系统)
- [8. 数据流全景](#8-数据流全景)
  - [8.1 Chat 路径（\"你好\"）](#81-chat-路径你好)
  - [8.2 Task 路径（\"写一个阶乘函数\"）](#82-task-路径写一个阶乘函数)
  - [8.3 自分解递归路径](#83-自分解递归路径)
  - [8.4 分级重试闭环](#84-分级重试闭环)
- [9. 设计原则](#9-设计原则)
- [10. 已实现功能清单](#10-已实现功能清单)
- [11. 配置与启动](#11-配置与启动)
- [12. 测试体系](#12-测试体系)
- [13. 当前状态（生产就绪验证）](#13-当前状态生产就绪验证)
- [14. 已知缺口](#14-已知缺口)

---

## 1. 项目概况

Janus 是一个**分层递归任务分解 Agent 框架**。核心理念：**Agent 管 Agent 的方式，应当镜像人类管人类的方式**——从军事、政府、企业、司法、制造业、学术六个领域提取管理智慧，映射到四层 LLM Agent 架构。

- **语言**：Python 3.13+
- **LLM 提供商**：DeepSeek（通过 OpenAI 兼容 API）
- **模型分层**：Gatekeeper 用重型推理模型（`deepseek-v4-pro`），Worker 用快速模型（`deepseek-v4-flash`）
- **交互方式**：CLI REPL（`python main.py`），三种输出模式（default / verbose / quiet）

---

## 2. 架构概览

```
                        ┌──────────────────────────────┐
                        │          用户 (User)          │
                        └──────────┬───────────────────┘
                                   │ 自然语言
                        ┌──────────▼───────────────────┐
                        │      Session (会话管理)       │
                        │  纯透传 + 历史记录（无决策）    │
                        └──────────┬───────────────────┘
                                   │
                        ┌──────────▼───────────────────┐
                        │   Gatekeeper (军师/战略层)     │
                        │  决策 chat/task → 定方向       │
                        │  零工具 — 纯 LLM 推理          │
                        │  Model: deepseek-v4-pro       │
                        └──────────┬───────────────────┘
                                   │ Directive
                        ┌──────────▼───────────────────┐
                        │    Planner (参谋/战术层)       │
                        │  拆任务 → 分派 → 追踪 → 汇总   │
                        │  零工具 — 纯规划协调           │
                        │  Model: deepseek-v4-flash      │
                        └──────────┬───────────────────┘
                                   │ TaskSpec[]
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
            ┌──────────┐   ┌──────────┐   ┌──────────┐
            │ Worker-0 │   │ Worker-1 │   │ Worker-N │
            │  有工具   │   │  有工具   │   │  有工具   │
            │ LLM 循环  │   │ LLM 循环  │   │ LLM 循环  │
            └────┬─────┘   └────┬─────┘   └────┬─────┘
                 ▼              ▼              ▼
            ┌──────────────────────────────────────┐
            │        Reviewer (督察/审计层)          │
            │  独立验证 Worker 产出 vs 验收标准      │
            │  零工具 — 纯 LLM 审计                  │
            │  五级裁决 + 四级严重度                  │
            └──────────────────────────────────────┘
```

### 四层分工

| 层 | 角色 | 人类类比 | 有工具？ | 有 LLM？ | 职责 |
|----|------|---------|---------|---------|------|
| **战略** | Gatekeeper | 军师/CEO | ❌ 零工具 | ✅ | 理解意图、判断 chat/task、定方向、向用户汇报 |
| **战术** | Planner | 参谋/VP | ❌ 零工具 | ✅ | 拆解战略指令为 TaskSpec[]、分派 Worker、追踪进度、汇总 |
| **执行** | Worker | 士兵/工程师 | ✅ 9 个工具 | ✅（循环） | 读取/写入文件、执行命令、搜索网络、自分解 |
| **审计** | Reviewer | 督察/同行评审 | ❌ 零工具 | ✅ | 按验收标准独立核查 Worker 产出 |

---

## 3. 文件清单

### 核心源码（core/）

| 文件 | 行数 | 用途 |
|------|------|------|
| `core/gatekeeper.py` | 496 | 战略决策层：chat/task 路由、Directive 制定、向用户汇报 |
| `core/planner.py` | 812 | 战术执行层：目标分解→TaskSpec[]、Worker 分派、重试管理、汇总 |
| `core/worker.py` | 1,185 | 工具执行层：LLM 工具调用循环、自分解递归、9 个真实工具实现 + ToolRegistry |
| `core/reviewer.py` | 588 | 独立审计层：五级 ReviewVerdict、四级 Severity、artifact 预加载审计 |
| `core/task_manager.py` | 220 | 任务状态机：PENDING→RUNNING→COMPLETED/FAILED |
| `core/session.py` | 130 | 多轮对话管理：历史记录 + 透传到 Gatekeeper |
| `core/console.py` | 311 | CLI 被动输出：中文 + emoji、三种模式（default/verbose/quiet） |
| `core/protocol.py` | 235 | 数据类型：TaskSpec、TaskResult、TaskStatus、Directive、ExecutionReport 等 |
| `core/prompts.py` | 77 | 共享提示词片段：context_discipline_prompt()、extract_json() |

### 入口与配置

| 文件 | 行数 | 用途 |
|------|------|------|
| `main.py` | 223 | 入口：加载 .env/config.yaml、连线组件、启动 REPL |
| `config.yaml` | 37 | 配置：模型选择、最大工具调用次数、最大递归深度 |
| `.env` | — | API key 等敏感信息（不纳入版本控制） |

### 文档（docs/）

| 文件 | 用途 |
|------|------|
| `design-philosophy.md` | 设计哲学——9 条已应用的人类管理规则 + 四步设计流程 |
| `social-structure-insights.md` | 六种人类组织结构的深度分析 + 8 条改进建议 |
| `information-flow.md` | 信息流全景——每个 LLM 看到什么、Console 输出什么 |
| `final-verdict.md` | 生产就绪验证报告——双轨审计、17 个 issue 修复、15 个缺口 |
| `user-gatekeeper-protocol.md` | 用户↔Gatekeeper 交互协议——用户应看到和不看到什么 |

### 测试（tests/）

| 文件 | 用途 |
|------|------|
| `test_integration.py` | 集成测试 |
| `test_edge_cases.py` | 边界情况测试 |
| `test_fuzz.py` | 模糊测试 |
| `test_stress.py` | 压力测试 |
| `test_task_manager.py` | TaskManager 状态转换测试 |
| `test_protocol.py` | 协议数据类序列化/反序列化测试 |

---

## 4. 四层角色详解

### 4.1 Gatekeeper（军师/战略决策层）

**文件**：`core/gatekeeper.py`（496 行）

**核心原则**：零工具——不能读文件、写文件、运行命令、搜索网络。强制架构思维。

**职责**：
1. **决策路由**（`_decide`）：LLM 判断用户输入是 chat 还是 task，输出 `{"action": "chat"|"task"}`，失败默认 task
2. **Chat 回复**（`_respond`）：轻量级 LLM 自然对话，不使用 context_discipline_prompt
3. **战略指令制定**（`_formulate_directive`）：将用户目标翻译为 `Directive(intent, constraints, priority)`，失败回退到模板
4. **委托 Planner**（`_execute_via_planner`）：Directive → Planner.execute() → ExecutionReport
5. **向用户汇报**（`_report_to_user`）：将 ExecutionReport 格式化为中文 + emoji 的架构级摘要

**系统提示词**：
- `_GATEKEEPER_IDENTITY`：角色定义 + 硬性约束
- `_DECIDE_SYSTEM_PROMPT`：分类判断
- `_CHAT_SYSTEM_PROMPT`：自然对话
- `_FORMULATE_SYSTEM_PROMPT`：战略指令制定
- `context_discipline_prompt()`：上下文纪律（共享）

**关键设计决策**：
- Chat 模式**不**使用上下文纪律提示词——chat 需要自然引用历史
- 决策失败默认路由到 task（宁可多执行，不要错过）
- API 异常时返回友好错误字符串，不崩溃

---

### 4.2 Planner（参谋/战术执行层）

**文件**：`core/planner.py`（812 行）

**核心原则**：零工具——不亲自执行，只规划和协调。

**职责**：
1. **战术分解**（`_plan`）：LLM 将 Directive 拆解为 `TaskSpec[]`，每项含 task_id、description、acceptance_criteria、context、intent
2. **Worker 分派**（`_run_worker`）：通过工厂函数创建 Worker 实例，注入 console、reviewer、priority、max_depth
3. **分级重试调度**（`_dispatch_with_review`）：执行→审查→按判决分级重试
4. **汇总报告**（`_summarize`）：纯逻辑聚合 → `ExecutionReport(status, total, passed, failed, summary, details, goal, constraints)`

**优先级驱动的重试预算**：
| priority | max_retries | 含义 |
|----------|-------------|------|
| speed / urgent | 0 | 快速失败，不重试 |
| balanced / normal | 2 | 默认，最多 3 次尝试 |
| quality | 3 | 全面审查，最多 4 次尝试 |

**系统提示词**：
- `_PLANNER_IDENTITY`：角色定义 + 硬性约束（中文）
- `_PLAN_SYSTEM_PROMPT`：分解方法
- `context_discipline_prompt()`：上下文纪律（共享）

---

### 4.3 Worker（士兵/工具执行层）

**文件**：`core/worker.py`（1,185 行）

**核心原则**：有 9 个真实工具，LLM 驱动循环，支持自分解。

**职责**：
1. **工具调用循环**（`_execute_loop`）：system prompt + user prompt → LLM → tool_call 或 text → 循环直到返回 TaskResult 或预算耗尽
2. **自分解**（`run`）：LLM 返回 NEEDS_DECOMPOSITION 时，递归执行子任务，然后 resume 原任务
3. **子审查**（`_review_sub_result`）：自分解后对子 Worker 产出进行分级审查
4. **结果解析**（`_parse_result`）：提取 JSON → TaskResult.from_dict()

**关键约束**：
- 最大工具调用次数：50（可配置）
- 最大递归深度：3（与 Planner 一致，由 Planner 注入）
- 禁止二次自分解（resume 后再 NEEDS_DECOMPOSITION → FAILURE）
- 深度超限 → FAILURE

**系统提示词**（`_SYSTEM_PROMPT_TEMPLATE`）：
- Worker 身份描述
- TaskSpec 字段填充：description、acceptance_criteria、context
- 优先级指引：speed→快速迭代、quality→极度仔细、balanced→正常
- TaskResult JSON schema
- 自分解说明
- Retry Mode：Review 反馈格式要求（"✓ Fixed [issue]: evidence"）

---

### 4.4 Reviewer（督察/独立审计层）

**文件**：`core/reviewer.py`（588 行）

**核心原则**：零工具——纯 LLM 推理，独立于执行者。

**职责**：
1. **逐条验收**：按 TaskSpec.acceptance_criteria 逐条核查 Worker 的 TaskResult
2. **制品审计**（`_build_artifact_contents`）：预加载 Worker 产出的文件内容到 prompt 中，供 LLM 直接检查
3. **五级裁决**（`ReviewVerdict`）
4. **四级严重度**（`Severity`）

#### 五级审查裁决（ReviewVerdict）

| 裁决 | 含义 | 行为 |
|------|------|------|
| `APPROVED` | 完全通过 | 直接放行 |
| `APPROVED_WITH_NOTES` | 通过但有建议 | 记录观察项，放行 |
| `MINOR_REVISIONS` | 小修 | 重试一次，自动接受 |
| `MAJOR_REVISIONS` | 大修 | 重试最多 2 次，需重新送审 |
| `REJECTED` | 不满足核心要求 | 重试最多 2 次，否则失败 |

#### 四级缺陷严重度（Severity）

| 等级 | 含义 | 行为 |
|------|------|------|
| `CRITICAL` | 🔴 致命——产出完全不可用 | 总是触发重试 |
| `MAJOR` | 🟡 严重——核心要求未满足 | 触发重试 |
| `MINOR` | 🟢 轻微——部分偏离但可用 | 重试一次，然后接受 |
| `SUGGESTION` | 💡 建议——优化想法 | 不阻塞 |

#### 特殊行为
- Worker 返回 FAILURE → Reviewer 直接返回 REJECTED（不浪费 token 审计失败）
- 制品访问控制：`_read_artifact` 只允许读取 Worker 在 artifacts 列表中声明的文件（防路径遍历）
- 超时：120 秒

---

## 5. 数据协议（protocol.py）

**文件**：`core/protocol.py`（235 行）

### 核心枚举

| 类型 | 值 | 用途 |
|------|-----|------|
| `TaskStatus` | SUCCESS / FAILURE / NEEDS_DECOMPOSITION | Worker 执行结果状态 |
| `Confidence` | HIGH / MEDIUM / LOW | Worker 自信心级别 |

### 核心数据类

**TaskSpec** — 工作包（Gatekeeper/Planner → Worker）：
| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | str | 唯一标识 |
| `description` | str | 做什么 |
| `acceptance_criteria` | str | 怎么算完成 |
| `context` | str | 背景信息 |
| `intent` | str | 为什么（指挥官意图） |
| `goal` | str | 用户原始目标 |
| `constraints` | str | 硬性约束 |
| `depth` | int | 分解深度（1=根） |

**TaskResult** — Worker 执行结果：
| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | TaskStatus | SUCCESS/FAILURE/NEEDS_DECOMPOSITION |
| `summary` | str | 一句话摘要 |
| `result` | str | 完整输出 |
| `decomposition_request` | DecompositionRequest? | 仅在 NEEDS_DECOMPOSITION 时 |
| `artifacts` | list[str] | 产出文件路径 |
| `confidence` | Confidence | 自信心 |
| `worker_id` | str? | 执行该任务的 Worker |

**Directive** — 战略指令（Gatekeeper → Planner）：
| 字段 | 说明 |
|------|------|
| `goal` | 用户原始目标 |
| `intent` | 战略意图 |
| `constraints` | 硬性约束 |
| `priority` | speed / quality / balanced / normal |
| `context` | 多轮对话历史 |

**ExecutionReport** — 执行报告（Planner → Gatekeeper）：
| 字段 | 说明 |
|------|------|
| `status` | completed / partial / failed |
| `total_tasks` | 总任务数 |
| `passed` | 通过数 |
| `failed` | 失败数 |
| `summary` | 一句话总结 |
| `details` | 逐任务详情 |
| `goal` / `constraints` | 回传原始意图 |

### 全链路关键字段贯穿

以下 5 个字段在 **Session → Gatekeeper → Directive → Planner → TaskSpec → Worker → Reviewer → ExecutionReport → 用户输出** 全链路贯穿，无一丢失：

- `goal` — 用户原始目标
- `intent` — 战略意图（指挥官意图）
- `constraints` — 硬性约束
- `priority` — 优先级（影响重试预算和 Worker 行为）
- `depth` — 递归深度（守卫无限分解）

---

## 6. 基础设施层

### 6.1 Session（会话管理）

**文件**：`core/session.py`（130 行）

- 包装 Gatekeeper，维护 `_history: list[dict]`（最多 100 轮 = 200 条消息）
- `_format_history_context(last_n=5)`：将最近 5 轮对话格式化为上下文字符串
- 纯透传——不做任何分类、意图识别或预处理
- 历史上下文注入到 Gatekeeper 的 `handle()` → `_decide()` / `_respond()` / `_formulate_directive()`

### 6.2 TaskManager（任务状态机）

**文件**：`core/task_manager.py`（220 行）

- 状态转换：`PENDING → RUNNING → COMPLETED/FAILED`
- 有状态守卫（`_VALID_TRANSITIONS`），非法转换抛 ValueError
- `mark_failed()` 自动构造 FAILURE TaskResult
- `reset()` 清空所有任务（每次 `Planner.execute()` 开头调用）
- `get_summary()` 返回各状态计数

### 6.3 Console（被动观察者）

**文件**：`core/console.py`（311 行）

- 三种模式：`default`（L0+L1+L2）/ `verbose`（+L3 完整参数）/ `quiet`（仅 L0 摘要）
- 中文 + emoji 输出：🔍 分析、✅ 通过、❌ 失败、⚡ 工具调用、💭 思考过程
- 分层输出：L1（阶段节点）→ L2（任务生命周期、工具调用、审查）→ L0（最终汇总）
- 被动设计——所有组件调用 console 但不依赖其返回值

### 6.4 prompts.py（共享提示词）

**文件**：`core/prompts.py`（77 行）

- `context_discipline_prompt(role_desc, job_desc)`：上下文窗口纪律提示词，可适配不同角色
- `extract_json(text)`：JSON 提取器（```json 围栏 → 括号计数回退），Gatekeeper、Planner、Worker 共享

---

## 7. Worker 工具系统

### ToolRegistry + ToolDef

**文件**：`core/worker.py`（第 50-180 行）

- `ToolDef(name, description, parameters, func)`：工具定义
- `ToolRegistry`：注册、schema 生成（OpenAI function-calling 兼容）、执行
- 参数别名映射（`_PARAM_ALIASES`）：LLM 可能用不同名称，自动映射到规范名（如 `"command"`→`"cmd"`、`"filename"`→`"path"`）
- `inspect.signature` 过滤：意外参数不会到达底层函数
- 工具崩溃不影响 Worker 循环（`try/except` 包裹）

### 9 个 Worker 工具

| # | 工具名 | 函数 | 行为 | 限制 |
|---|--------|------|------|------|
| 1 | `read_file` | `_real_read_file` | 读取文件内容 | 5,000 字符截断 |
| 2 | `write_file` | `_real_write_file` | 写入文件（自动创建父目录） | 无 |
| 3 | `terminal` | `_real_terminal` | `subprocess` 执行 shell 命令 | 60 秒超时 |
| 4 | `web_search` | `_real_web_search` | ⚠️ 占位符 — 需 DuckDuckGo/SerpAPI | — |
| 5 | `web_extract` | `_real_web_extract` | HTTP GET + 去 HTML 标签 | 3,000 字符/URL，最多 5 URL |
| 6 | `search_files` | `_real_search_files` | glob + 内容搜索 | 最多 50 结果 |
| 7 | `patch` | `_real_patch` | 首次匹配替换 | 无 |
| 8 | `execute_code` | `_real_execute_code` | `exec()` 执行 Python | 受限 builtins 命名空间 |
| 9 | `browser_navigate` | `_real_browser_navigate` | ⚠️ 占位符 — 需 Playwright | — |

---

## 8. 数据流全景

### 8.1 Chat 路径（"你好"）

```
用户输入 → Session.handle() → Gatekeeper.handle()
  → _decide(message) — LLM① → {"action": "chat"}
  → _respond(message) — LLM② → 自然语言回复
  → 返回给用户
```

- Chat 模式不使用 `context_discipline_prompt`
- 失败回退：API 异常返回 `"Chat error: ..."`

### 8.2 Task 路径（"写一个阶乘函数"）

```
用户输入 → Session.handle() → Gatekeeper.handle()
  → _decide(message) — LLM① → {"action": "task"}
  → _execute_via_planner(goal)
       → _formulate_directive(goal) — LLM② → Directive
       → Planner.execute(directive)
            → _plan(directive) — LLM③ → [TaskSpec, ...]
            → 对每个 TaskSpec:
                 → _dispatch_with_review(spec)
                      → _run_worker(spec)
                           → worker.run(spec) — LLM④ 循环 → TaskResult
                                └─ [可选] 自分解递归
                      → reviewer.review(spec, result) — LLM⑤ → ReviewResult
                           └─ [不通过] 注入反馈 → 重试
            → _summarize(results) → ExecutionReport
       → _report_to_user(report) → 中文 + emoji 摘要
  → 返回给用户
```

### 8.3 自分解递归路径

```
Worker.run(spec)
  → _execute_loop(spec) → NEEDS_DECOMPOSITION
  → 深度检查（spec.depth >= max_depth? → FAILURE）
  → 对每个 sub_task:
       TaskSpec(depth+1, 字段全传播)
       → self.run(sub_spec)  ← 递归
            └─ _review_sub_result(sub_spec, sub_result)  ← 子审查
  → _format_sub_results(sub_results)
  → TaskSpec(resume, 注入子结果)
  → _execute_loop(resume_spec) → 最终 TaskResult
  → [二次 NEEDS_DECOMPOSITION? → FAILURE]
```

### 8.4 分级重试闭环

```
Planner._dispatch_with_review(spec, max_retries)
  → for attempt in range(max_retries + 1):
       result = _run_worker(spec)
       review = reviewer.review(spec, result)
       
       ┌─ APPROVED / APPROVED_WITH_NOTES → mark_completed, return
       ├─ MINOR_REVISIONS:
       │    attempt=0 → retry with feedback
       │    attempt≥1 → re-review, auto-accept (除非发现新 MAJOR/CRITICAL)
       ├─ MAJOR_REVISIONS: retry up to max_retries, full re-review
       └─ REJECTED: retry up to max_retries, then fail
  
  → 耗尽 → mark_failed, return FAILURE TaskResult
```

---

## 9. 设计原则

Janus 的第一设计原则：**Agent 管 Agent 的方式，应当镜像人类管人类的方式**。

### 9 条已应用的人类管理规则

| # | 规则 | 来源 | Janus 落地 |
|---|------|------|-----------|
| 1 | **指挥官意图** | 军事（任务式指挥） | `TaskSpec.intent` — Worker 知道"为什么" |
| 2 | **参谋/一线分工** | 军事（Staff/Line） | Gatekeeper→Planner→Worker 三层分离 |
| 3 | **监察长独立审计** | 政府（Inspector General） | Reviewer 独立于 Worker，零工具 |
| 4 | **质量检查点** | 制造业（三道防线） | 每层都有 Review——Planner 级 + Worker 子审查 |
| 5 | **绩效改进计划** | 企业 HR（PIP） | `_make_retry_spec()` 注入具体反馈（不是"重做"） |
| 6 | **司法审查标准** | 司法（三层 standard of review） | `ReviewVerdict` 五级裁决替代二元 pass/fail |
| 7 | **缺陷严重度分级** | 制造业（四级缺陷矩阵） | `Severity` 四级：CRITICAL/MAJOR/MINOR/SUGGESTION |
| 8 | **指挥链** | 军事（逐级关注不同粒度） | 每层看不同信息——Gatekeeper 只看架构级 |
| 9 | **管理幅度** | 管理学（Span of Control） | `context_discipline_prompt` — "summarize to one line" |

### 设计新功能的标准流程

1. **找到人类等价物**：这个问题在人类组织里叫什么？
2. **提取核心机制**：剥离制度外壳，保留可迁移的核心理念
3. **映射到 Janus 角色和协议**：人类概念 → Janus 数据类
4. **最小化实现**：只实现核心机制，不过度工程化

### 反模式警示
- ❌ 不要因为人类有就加角色（如 ResourceManager）
- ❌ 不要加没有明确价值提升的流程（如"周报"）
- ❌ 当人类实践与 Agent 物理现实冲突时——尊重 Agent 现实
- ✅ 提取机制而非模仿组织

---

## 10. 已实现功能清单

### 核心能力

| 功能 | 说明 |
|------|------|
| ✅ chat/task 自动路由 | Gatekeeper LLM 判断聊天还是任务 |
| ✅ 战略意图提取 | `_formulate_directive` 从用户目标中提取 intent/constraints/priority |
| ✅ LLM 驱动任务分解 | Planner `_plan` 将 Directive 拆为 TaskSpec[] |
| ✅ 9 工具 Worker 执行 | LLM 循环使用 read_file/write_file/terminal/web_extract/search_files/patch/execute_code 等 |
| ✅ 五级审查裁决 | APPROVED / APPROVED_WITH_NOTES / MINOR_REVISIONS / MAJOR_REVISIONS / REJECTED |
| ✅ 四级缺陷严重度 | CRITICAL / MAJOR / MINOR / SUGGESTION |
| ✅ 分级重试 | MINOR→重试一次自动接受、MAJOR/REJECTED→最多 2 次、speed→不重试 |
| ✅ 绩效改进式重试 | 重试时注入具体问题 + 严重度标签（不是"重做"） |
| ✅ 制品审计 | Reviewer 预加载 Worker 产出文件内容到 prompt |
| ✅ Worker 自分解 | Worker 可返回 NEEDS_DECOMPOSITION → 递归执行子任务 → resume |
| ✅ 自分解子审查 | Worker 自分解的子任务产出经过 Reviewer 分级审查 |
| ✅ 深度守卫 | max_depth=3，超限 → FAILURE；禁止二次自分解 |
| ✅ 优先级驱动 | speed=0 retry、quality=3 retry，影响 Worker 行为提示词 |
| ✅ 多轮对话 | Session 记录历史 → Gatekeeper 感知上下文 |
| ✅ 全链路字段贯穿 | goal/intent/constraints/priority/depth 在 7 个交接点无一丢失 |
| ✅ 被动 Console | 三种输出模式 + 中文 emoji + 分层显示 |
| ✅ 状态机守卫 | TaskManager 非法状态转换抛 ValueError |
| ✅ 错误降级 | 每个 LLM 调用点都有 fallback，不崩溃 |
| ✅ 参数别名映射 | LLM 可用不同参数名，自动映射到规范名 |

### 辅助能力

| 功能 | 说明 |
|------|------|
| ✅ CLI 三种模式 | `--verbose` / `--quiet` / default |
| ✅ .env 加载 | 启动时读取，不覆盖已有环境变量 |
| ✅ config.yaml ${VAR} 解析 | 递归解析环境变量占位符 |
| ✅ 异构模型支持 | Gatekeeper 用推理模型，Worker 用快速模型 |
| ✅ Worker 工厂模式 | 每次分派创建新 Worker 实例 |
| ✅ 制品访问控制 | Reviewer 只读 Worker 声明过的文件 |

---

## 11. 配置与启动

### config.yaml 结构

```yaml
model:
  provider: "deepseek"
  model: "deepseek-v4-pro"
  api_key: "${DEEPSEEK_API_KEY}"

gatekeeper:
  model: "deepseek-v4-pro"        # 重型推理模型

worker:
  model: "deepseek-v4-flash"      # 快速执行模型
  max_tool_calls: 50              # 工具调用硬上限

janus:
  max_depth: 3                    # 递归深度硬上限
```

### 启动命令

```bash
# 默认模式
python main.py

# 详细模式（显示完整工具参数 + LLM 思考过程）
python main.py --verbose

# 安静模式（仅最终汇总）
python main.py --quiet
```

### 启动流程（main.py）

1. `_load_dotenv()` — 加载 `.env`
2. `load_config(config_path)` — 解析 `config.yaml`
3. `resolve_config(raw)` — 递归解析 `${VAR}` 占位符
4. 提取配置项：模型名、API key、max_tool_calls、max_depth
5. 解析 CLI 参数：`--verbose/-v`、`--quiet/-q`
6. 连线组件：ToolRegistry → TaskManager → Reviewer → Worker 工厂 → Planner → Gatekeeper → Session
7. REPL 循环：`input("> ")` → `session.handle()` → `print(answer)`

---

## 12. 测试体系

| 文件 | 测试内容 |
|------|---------|
| `test_integration.py` | 端到端集成测试 |
| `test_edge_cases.py` | 边界情况：极端输入、null/空值 |
| `test_fuzz.py` | 模糊测试 |
| `test_stress.py` | 压力/负载测试 |
| `test_task_manager.py` | 状态转换合法性、非法转换抛异常 |
| `test_protocol.py` | 序列化/反序列化、validate() |

---

## 13. 当前状态（生产就绪验证）

**裁决：✅ Janus 已生产就绪**（来自 `final-verdict.md` 双轨审计）

### 已验证正确项（20/20 交接点）

所有数据交接点类型匹配、错误处理到位：
- Session → Gatekeeper
- Gatekeeper._decide → 路由（含 fallback）
- Gatekeeper → Directive（含 goal/intent/constraints/priority）
- Gatekeeper → Planner
- Planner._plan → TaskSpec[]（含字段注入）
- Planner → Worker（含 console/reviewer/priority/max_depth 注入）
- Worker._execute_loop → TaskResult
- Worker.run → 自分解（字段全传播）
- Worker → Reviewer（子审查）
- Planner._dispatch_with_review → Reviewer（主审查）
- Planner._summarize → ExecutionReport
- Gatekeeper._report_to_user → 用户
- TaskManager 状态转换（有守卫）
- Worker 工具注册表（9 个工具）
- Console 被动观察
- Worker depth guard
- Worker 二次自分解 guard
- Reviewer FAILURE 快速通道
- 优先级驱动重试预算
- `_make_retry_spec` 字段保留

### 17/17 原始 issue 全部修复

包含 C1-C5（严重）、M1-M8（中等）、m1-m4（低）、GAP-1/5 等全部修复。

---

## 14. 已知缺口（15 个，非阻塞）

### 🔴 中等（3 个）

| # | 问题 | 影响 |
|---|------|------|
| GAP-2 | Planner 无法感知多轮对话历史（Directive 不携带 history_context） | 多轮复杂任务分解精度下降 |
| NEW-1 | task_id 可为空字符串，TaskManager 覆盖 | 状态跟踪可能错乱 |
| Gap 1(TD) | `Planner.execute()` 开头未重置 `_last_error` | 前次调用的陈旧错误污染本次报告 |

### 🟡 低优先级（7 个）

- `_formulate_directive` fallback 路径缺少 logger.warning()
- `Worker._parse_result()` 使用贪婪正则 vs 括号计数不一致
- `_format_history_context()` 标签 "X turns ago" 实际是绝对位置
- `TaskSpec.validate()` 定义但未被调用
- `Console.task_done()` 未处理 `needs_decomposition` 状态
- `TaskManager.add_task()` 静默覆盖重复 task_id
- `mark_failed` 记录的 TaskResult 与 `_summarize` 使用不一致

### 🟢 极低/设计选择（5 个）

- `_extract_json()` 在 Gatekeeper 和 Planner 中重复（现已提取到 prompts.py）
- `balanced`/`normal` priority 无 Worker 行为指引条目
- 子审查重试无逐次 Console 输出
- `web_search` 和 `browser_navigate` 为占位符
- `Gatekeeper._respond()` 不含 `context_discipline_prompt`（设计选择，非 bug）

---

> *本文档基于 Janus Phase 4 完整源码（`core/` 10 个源文件 + `main.py`，总计约 4,275 行）及全部设计文档。*
