# Janus 信息流全景

> 从用户输入到最终响应 — 每一层如何流转、每个 LLM 看到什么、Console 输出什么。

---

## 目录

- [组件一览](#组件一览)
- [启动流程](#启动流程)
- [第一章：Chat 路径（"你好"）](#第一章chat-路径你好)
- [第二章：Task 路径（"写一个阶乘函数"）](#第二章task-路径写一个阶乘函数)
  - [阶段 1：路由决策](#阶段-1路由决策)
  - [阶段 2：目标分解](#阶段-2目标分解)
  - [阶段 3：Worker 执行](#阶段-3worker-执行)
  - [阶段 4：Reviewer 审计](#阶段-4reviewer-审计)
  - [阶段 5：Worker 自分解（递归路径）](#阶段-5worker-自分解递归路径)
  - [阶段 6：汇总返图](#阶段-6汇总返图)
- [附录 A：系统提示词汇总](#附录-a系统提示词汇总)
- [附录 B：错误降级策略](#附录-b错误降级策略)

---

## 组件一览

| 文件 | 角色 | 有 LLM 调用？ | 有工具？ | 说明 |
|---|---|---|---|---|---|
| `main.py` (266 行) | 启动 + REPL 循环 | ❌ | ❌ | 加载配置 → 连线组件 → 死循环读输入 |
| `core/session.py` (130 行) | 历史记录器 | ❌ | ❌ | 纯透传，记录对话并注入历史上下文 |
| `core/gatekeeper.py` (889 行) | 战略决策层 | ✅ 3~5 次 | ❌ 零工具 | 判断 chat/task → 定战略方向 → 委托 Planner → 向用户汇报 |
| `core/planner.py` (1125 行) | 战术执行层 | ✅ 1 次 | ❌ 零工具 | 拆任务 → 派 Worker → Reviewer 审计 → 汇总报告 |
| `core/worker.py` (1320+ 行) | 工具调用循环 + 自分解 | ✅ 循环 | ✅ 9 个 | 拿 TaskSpec，调工具干活，返回 TaskResult |
| `core/reviewer.py` (~600 行) | 独立审计 Agent | ✅ 1 次 | ❌ 零工具 | 按验收标准核查 Worker 产出 |
| `core/task_manager.py` (220 行) | 任务状态机 | ❌ | ❌ | PENDING → RUNNING → COMPLETED/FAILED |
| `core/console.py` (311 行) | 被动输出 | ❌ | ❌ | 中文 + emoji 格式化，三种模式 |
| `core/protocol.py` (247 行) | 数据类型 | ❌ | ❌ | `TaskSpec` / `TaskResult` / `Directive` / `ExecutionReport` 等 |
| `core/prompts.py` (77 行) | 共享提示词 | ❌ | ❌ | `context_discipline_prompt()` / `extract_json()` |

### 调用关系

```
main.py (REPL 循环)
  └─ Session.handle(user_input)
       └─ Gatekeeper.handle(message, history_context)
            ├─ _decide(message) ── LLM① → {"action": "chat"|"task"}
            │
            ├─ [chat] _respond(message) ── LLM② → 文本
            │
            └─ [task] _execute_via_planner(goal)
                 ├─ _formulate_directive(goal) ── LLM② → Directive
                 │
                 └─ Planner.execute(directive)
                      ├─ _plan(directive) ── LLM③ → [TaskSpec, …]
                      │
                      └─ 对每个 TaskSpec:
                           _dispatch_with_review(spec)
                            ├─ _run_worker(spec)
                            │    └─ worker.run(spec)
                            │         └─ _execute_loop(spec) ── LLM④ 循环 → TaskResult
                            │              └─ [可选] 自分解递归 → 再调 run()
                            │
                            └─ reviewer.review(spec, result) ── LLM⑤ → ReviewResult
```

---

## 启动流程

> **源码**：`main.py:117-204`

1. **加载 .env**（`main.py:125`）— 读取项目根目录 `.env`，注入 `os.environ`。
2. **解析 config.yaml**（`main.py:128-139`）— YAML → dict → 解析 `${VAR}` 环境变量占位符。
3. **提取配置项**（`main.py:142-148`）— gatekeeper 模型、worker 模型、API key、最大工具调用次数 (默认 50)、最大递归深度 (默认 3)。
4. **解析 CLI 参数**（`main.py:151-157`）— `--verbose` / `-v`、`--quiet` / `-q` 决定 Console 模式。
5. **连线组件**（`main.py:160-183`）：
   - 创建 `ToolRegistry` 并注册 7 个真实工具。
   - 创建 `TaskManager`。
   - 创建 `Reviewer`（共享实例，供 Planner 和 Worker 子审查使用）。
   - 创建 Worker 工厂函数（支持可选 model_override）。
   - 创建 `Planner`（注入 TaskManager、Worker 工厂、Reviewer、Console）。
   - 创建 `Gatekeeper`（注入 Planner、Console）。
   - 创建 `Session`（包装 Gatekeeper，`max_history=100`）。
6. **启动 REPL**（`main.py:186-204`）— 死循环 `input("> ")`，非空输入交给 `session.handle()`，打印返回值。

---

## 第一章：Chat 路径（"你好"）

> **源码**：`main.py:202` → `session.py:51` → `gatekeeper.py:167-292`

### 1.1 调用链总览

```
用户输入 "你好"
  └─ main.py:202    session.handle("你好")
       └─ session.py:51   gk.handle("你好")
            ├─ gatekeeper.py:181  _decide("你好")
            │    └─ LLM API → {"action": "chat", "reason": "简单问候"}
            │
            └─ gatekeeper.py:185  _respond("你好")
                 └─ LLM API → "你好！有什么可以帮助你的？"
       └─ session.py:54-57  记录到 _history，修剪到 200 条
       └─ session.py:60     return "你好！有什么可以帮助你的？"
  └─ main.py:203    print(answer)
```

### 1.2 第一步：决策（`_decide`）

**代码位置**：`gatekeeper.py:222-252`

Gatekeeper 把用户的输入发给 LLM，让它判断是"聊天"还是"任务"。

**LLM 看到的消息**：

| 序号 | 角色 | 内容 | 来源 |
|---|---|---|---|
| 1 | system | `CRITICAL: Your context window is precious…`（上下文纪律提示） | `gatekeeper.py:79-86` |
| 2 | system | `You are Janus Gatekeeper… Output ONLY valid JSON: {"action":"chat"\|"task"…}` | `gatekeeper.py:88-91` |
| 3 | user | `你好` | 用户输入 |

**LLM 返回**：
```json
{"action": "chat", "reason": "简单问候"}
```

API 调用启用 `thinking` 模式（`gatekeeper.py:236-238`），LLM 的推理过程（`reasoning_content`）通过 Console 以 `💭 [Gatekeeper]` 显示。JSON 解析失败时默认降级为 `task`（`gatekeeper.py:252`）。

### 1.3 第二步：回复（`_respond`）

**代码位置**：`gatekeeper.py:256-292`

因为 `action == "chat"`，走聊天路径——不发 Worker、不用工具，直接用另一个 system prompt 让 LLM 回复。

**LLM 看到的消息**：

| 序号 | 角色 | 内容 | 来源 |
|---|---|---|---|
| 1 | system | `You are a helpful AI assistant. Respond naturally to the user in Chinese…` | `gatekeeper.py:113-116` |
| 2 | user | `你好` | 用户输入 |

**LLM 返回**：`"你好！有什么可以帮助你的吗？"`

**⚠️ 注意**：Chat 模式下**不使用** `_CONTEXT_DISCIPLINE_PROMPT`——只用轻量的 `_CHAT_SYSTEM_PROMPT`。API 异常时返回 `"Chat error: …"` 字符串（`gatekeeper.py:281-282`）。

### 1.4 Session 记录

**代码位置**：`session.py:54-57`

```python
_history.append({"role": "user", "content": "你好"})
_history.append({"role": "assistant", "content": "你好！…"})
# 超过 max_history*2=200 条时保留最后 200 条
```

**关键**：`_history` 目前只是存储——Gatekeeper 的每次 LLM 调用都是无状态的，不传入历史上下文。

### 1.5 Chat 路径 ASCII 流程图

```
  👤 用户输入 "你好"
          │
          ▼
  ┌──────────────────────────────────────────┐
  │         Session.handle("你好")            │
  │  纯透传 + 记录历史                         │
  └──────────────────┬───────────────────────┘
                     │
                     ▼
  ┌──────────────────────────────────────────┐
  │         Gatekeeper.handle("你好")          │
  │                                            │
  │  ┌─ _decide("你好") ──────────────────┐  │
  │  │  System: CONTEXT_DISCIPLINE         │  │
  │  │          DECIDE_SYSTEM_PROMPT       │  │
  │  │  User:   "你好"                     │  │
  │  │  → LLM API (thinking=enabled)      │  │
  │  │  → {"action": "chat"}             │  │
  │  │  → 💭 [Gatekeeper] reasoning       │  │
  │  └────────────────────────────────────┘  │
  │                                            │
  │  ┌─ _respond("你好") ─────────────────┐  │
  │  │  System: CHAT_SYSTEM_PROMPT         │  │
  │  │  User:   "你好"                     │  │
  │  │  → LLM API (thinking=enabled)      │  │
  │  │  → "你好！有什么可以帮助你的？"    │  │
  │  │  → 💭 [Gatekeeper] reasoning       │  │
  │  └────────────────────────────────────┘  │
  │  return "你好！有什么可以帮助你的？"     │
  └──────────────────────────────────────────┘
                     │
                     ▼
  ┌──────────────────────────────────────────┐
  │         main.py: print(answer)            │
  └──────────────────────────────────────────┘
```

### 1.6 Console 输出顺序

```
> 你好
                                   ← 空行 (main.py:201)
  💭 [Gatekeeper] <decide 推理过程>  ← console.think_block
  💭 [Gatekeeper] <respond 推理过程>  ← console.think_block
你好！有什么可以帮助你的吗？         ← main.py:203
                                   ← 空行 (main.py:204)
>
```

---

## 第二章：Task 路径（"写一个阶乘函数"）

Task 路径复杂得多：Gatekeeper 先决策→分解目标→派 Worker→Reviewer 审计→汇总返图。总共涉及 4 个 LLM 调用点。

### 阶段 1：路由决策

> 和 Chat 路径的第一步完全相同，只是 LLM 返回 `action: "task"`。

**代码位置**：`gatekeeper.py:222-252`

LLM 看到与 Chat 路径相同的前两条 system prompt 加上用户输入 `"写一个阶乘函数"`，返回：

```json
{"action": "task", "reason": "需要代码生成和实现"}
```

因为 `action == "task"`，进入 `_execute_via_planner(goal)`（`gatekeeper.py:159`）。

### 阶段 2：目标分解

> **源码**：`planner.py:247-432`（Planner._plan）

#### 2.1 清空任务记录

```python
self._task_manager.reset()    # gatekeeper.py:314
```

#### 2.2 LLM 分解

**代码位置**：`gatekeeper.py:487-562`

**LLM 看到的消息**：

| 序号 | 角色 | 内容 | 来源 |
|---|---|---|---|
| 1 | system | `CRITICAL: Your context window is precious…` | `gatekeeper.py:79-86` |
| 2 | system | `You are a Janus Gatekeeper. Your sole job is to decompose a user's goal…` + `Goal: 写一个阶乘函数` | `gatekeeper.py:93-111` |
| 3 | user | `写一个阶乘函数` | 用户原始输入 |

**LLM 返回示例**：
```json
[
  {
    "task_id": "task-1",
    "description": "实现一个计算阶乘的Python函数",
    "acceptance_criteria": "函数正确计算 0!=1, 1!=1, 5!=120；包含输入验证和类型提示；有测试",
    "context": "用Python编写，包含文档字符串"
  }
]
```

#### 2.3 JSON 解析 → `TaskSpec` 列表

**代码位置**：`gatekeeper.py:519-562`

1. 捕获 `reasoning_content` → Console `💭 [Gatekeeper]`（截断 500 字符）。
2. `_extract_json()` 提取 JSON——优先 ```json 围栏，回退到括号计数。
3. 三种返回形式：
   - **`[...]` 数组**：逐个解析为 `TaskSpec(depth=1)`。
   - **`{"error": "…"}` 字典**：记录 `_last_error`，返回空列表。
   - **无法解析**：记录 `_last_error`，返回空列表。

如果返回空列表，`Planner.execute` 会通过 `_last_error` 将错误信息传递给 Gatekeeper（`planner.py:170-182`）。

#### 2.4 Console 输出分解结果

**代码位置**：`gatekeeper.py:330-335`

```
🔍 Gatekeeper 分析完成，拆分为 1 个子任务：
  ✓ task-1 · 实现一个计算阶乘的Python函数
```

### 阶段 3：Worker 执行

> **源码**：`planner.py:830-911`（`_run_worker`） → `worker.py:260-421`（核心循环）

#### 3.1 Planner 创建 Worker 并派发

**代码位置**：`planner.py:830-911`

```python
# planner.py:838
worker = self._worker_factory(model_override=self._worker_model)

# planner.py:875-876 - 注入 Console（被动观察者模式）
if self._console is not None:
    worker.console = self._console

# planner.py:890
result = worker.run(spec)
```

Worker 工厂支持 `model_override`——可以让 Worker 使用与 Gatekeeper/Planner 不同的模型。如果工厂调用失败，返回 FAILURE 的 TaskResult。

#### 3.2 Worker 构建系统提示词

**代码位置**：`worker.py:376-384` → `worker.py:503-509`

Worker 把 `TaskSpec` 的三个字段渲染进模板：

```python
# worker.py:503-509
system_prompt = self._build_system_prompt(spec)
# = _SYSTEM_PROMPT_TEMPLATE.format(
#     description=spec.description,        # "实现一个计算阶乘的Python函数"
#     acceptance_criteria=spec.acceptance_criteria,  # "函数正确计算…"
#     context=spec.context,                # "用Python编写，包含文档字符串"
# )
```

**LLM (Worker) 看到的 System Prompt**（`worker.py:197-234`）：

```
You are a Janus Worker — an autonomous AI agent that executes tasks using available tools.

## Your Task
实现一个计算阶乘的Python函数

## Acceptance Criteria
函数正确计算 0!=1, 1!=1, 5!=120；包含输入验证和类型提示；有测试

## Context
用Python编写，包含文档字符串

## Instructions
1. Use the available tools — do not simulate, actually call them.
2. Be thorough. Verify your work.
3. When done, output a JSON object following the TaskResult schema.
4. If the task is too complex, return status="needs_decomposition" with a decomposition_request.
5. Output ONLY the JSON object — no extra commentary.
```

加上一条 user message（`worker.py:379-384`）：

> `"Begin working on the task. Use tools as needed. Output your final result as a TaskResult JSON when done."`

#### 3.3 工具调用循环

**代码位置**：`worker.py:389-499`

```
messages = [system_prompt, "Begin working…"]
tool_call_count = 0

while tool_call_count < 50:
    response = LLM API (thinking=enabled, tools=schemas)
    msg = choice.message

    ┌─ LLM 返回 tool_calls？ ─────────────────────────────────┐
    │ 1. 构建 assistant 消息（包含 reasoning_content）        │
    │ 2. 💭 [Worker] console.think_block (截断 500)           │
    │ 3. 执行每个工具：                                       │
    │    → registry.execute(name, args)                      │
    │    → 参数别名映射（如 "command"→"cmd"）                  │
    │    → inspect.signature 过滤有效参数                     │
    │    → 调用真实函数，捕获异常                              │
    │    → ⚡ Console 显示工具调用                           │
    │ 4. 工具结果作为 tool message 加入 messages              │
    │ 5. continue ← 回到循环，LLM 看到工具结果                │
    └────────────────────────────────────────────────────────┘

    ┌─ LLM 返回文本内容？ ────────────────────────────────────┐
    │ 💭 [Worker] console.think_block (if reasoning_content) │
    │ return _parse_result(content)  → TaskResult            │
    └────────────────────────────────────────────────────────┘

    ┌─ 两者都无 → break ─────────────────────────────────────┐
    │ return FAILURE "tool-call budget exhausted"            │
    └────────────────────────────────────────────────────────┘
```

**9 个可用工具**（`worker.py:1232-1320`）：

| 工具 | 函数 | 行为 | 截断/限制 |
|---|---|---|---|
| `read_file` | `_real_read_file` | 读文件 | 50,000 字符/次 |
| `write_file` | `_real_write_file` | 写文件（自动创建目录） | 无 |
| `terminal` | `_real_terminal` | subprocess 执行 shell | 60 秒超时 |
| `web_search` | `_real_web_search` | ✅ DuckDuckGo（ddgs） | 10 结果/次 |
| `web_extract` | `_real_web_extract` | HTTP GET + 去 HTML | 3,000 字符/URL |
| `search_files` | `_real_search_files` | glob + 内容搜索 | 最多 50 结果 |
| `patch` | `_real_patch` | 查找替换（首次出现） | 无 |
| `execute_code` | `_real_execute_code` | exec() 执行 Python | 受限内置命名空间 |
| `browser_navigate` | `_real_browser_navigate` | ✅ Playwright headless Chromium | 30s 超时 |

**参数别名映射**（`worker.py:78-91`）：LLM 可能用不同的参数名，注册表自动映射到规范名称。例如 `"filename"` → `"path"`、`"q"` → `"query"`。注意：别名必须映射到函数签名的规范参数名，否则被 `inspect.signature` 过滤后会传入空参数导致崩溃——`"command" → "cmd"` 曾在 2026-07-19 引发 `terminal` 工具 100% 崩溃。

#### 3.4 解析 Worker 输出

**代码位置**：`worker.py:536-578`

Worker 循环在 LLM 返回文本内容时退出，文本按以下顺序解析为 `TaskResult`：

1. 尝试提取 ````json … ```` 围栏内的 JSON（`worker.py:547`）。
2. 回退到正则 `{…}` 匹配（`worker.py:553`）。
3. `json.loads()` → `TaskResult.from_dict()`（`worker.py:559-560`）。
4. `validate()` 检查：NEEDS_DECOMPOSITION 必须带 `decomposition_request`（`worker.py:561`）。
5. 全部失败 → FAILURE TaskResult。

**Worker 成功返回示例**：
```json
{
  "status": "success",
  "summary": "成功实现阶乘函数，包含输入验证、类型提示和测试",
  "result": "创建了 factorial.py 和 test_factorial.py，测试全部通过",
  "artifacts": ["factorial.py", "test_factorial.py"],
  "confidence": "high"
}
```

### 阶段 4：Reviewer 审计

> **源码**：`planner.py:436-674`（调度） → `reviewer.py:485-576`（审计）

#### 4.1 调度逻辑

**代码位置**：`gatekeeper.py:377-434`

```python
for attempt in range(max_retries + 1):  # max_retries=2，最多 3 次尝试
    result = self._run_worker(spec)      # Worker 执行

    if not self._reviewer:               # 无 Reviewer → 直接接受
        tm.mark_completed(spec.task_id, result)
        return result

    review = self._reviewer.review(spec, result)  # LLM 审计
    if review.passed:                    # 通过 → 完成
        tm.mark_completed(spec.task_id, result)
        console.review_pass(...)
        return result

    # 不通过 → 把 Reviewer 反馈注入 context，重试
    spec = TaskSpec(
        ...,
        context=f"{spec.context}\n\nPREVIOUS ATTEMPT FAILED REVIEW:\n{review.summary}\nIssues: {', '.join(review.issues)}"
    )

# 重试用尽 → mark_failed
```

#### 4.2 Reviewer 的 LLM 调用

**代码位置**：`reviewer.py:180-252`

**LLM (Reviewer) 看到的 System Prompt**（`reviewer.py:123-134`）：

```
You are a Janus Reviewer. Your sole job is to audit deliverables against requirements.

Given a task specification with acceptance criteria and a Worker's delivered
result, evaluate whether the result actually meets every criterion.

Be precise and evidence-based:
- For each acceptance criterion, state whether it is satisfied and cite specific evidence
- If a criterion is partially met, explain what is missing
- Do NOT assume — if evidence is absent, flag it as an issue
```

**LLM (Reviewer) 看到的 User Prompt**（`reviewer.py:136-157`）：

```
TASK: 实现一个计算阶乘的Python函数
ACCEPTANCE CRITERIA: 函数正确计算 0!=1, 1!=1, 5!=120；包含输入验证和类型提示；有测试
EXPECTED ARTIFACTS: 用Python编写，包含文档字符串

DELIVERED RESULT:
Status: success
Summary: 成功实现阶乘函数，包含输入验证、类型提示和测试
Full Result: 创建了 factorial.py 和 test_factorial.py，测试全部通过
Artifacts: factorial.py, test_factorial.py

For each acceptance criterion:
1. Does the result satisfy it?
2. What evidence proves it?

Output ONLY a JSON object with this schema:
{"status": "pass"|"fail", "summary": "…", "issues": ["…"], "evidence": "…"}
```

**Reviewer 返回示例**：
```json
{
  "status": "pass",
  "summary": "所有验收标准已满足",
  "issues": [],
  "evidence": "函数正确计算 0!=1, 1!=1, 5!=120\n包含输入验证和类型提示\n测试全部通过"
}
```

**⚠️ 特殊行为**：
- Worker 返回 FAILURE → Reviewer 自动通过（不再审计失败）（`reviewer.py:191-206`）。
- Reviewer API 调用**不启用 thinking mode**（`reviewer.py:229-232`）——没有 `extra_body`。
- 超时 120 秒（`reviewer.py:161, 175`）。

#### 4.4 重试时 context 累积

第 N 次重试时，Worker 的 context 会累积之前所有失败反馈：

```
原始 context:
  "用Python编写，包含文档字符串和类型提示"

第 1 次重试 context:
  "用Python编写，包含文档字符串和类型提示\n\n
   PREVIOUS ATTEMPT FAILED REVIEW:
   缺少输入验证...
   Issues: 未处理负数输入, 缺少类型提示"

第 2 次重试 context:
  （前面所有内容）
   + 第 1 次重试的 Reviewer 反馈
```

### 阶段 5：Worker 自分解（递归路径）

> **源码**：`worker.py:267-361`

这是进阶路径——仅当 Worker 返回 `status: "needs_decomposition"` 时触发。

#### 5.1 触发条件

Worker 的 `_execute_loop` 返回 `TaskResult(status=NEEDS_DECOMPOSITION)` → `run()` 进入自分解路径（`worker.py:288`）。

#### 5.2 安全检查

| 检查 | 代码位置 | 失败处理 |
|---|---|---|
| 深度 ≥ `MAX_WORKER_DEPTH` (3) | `worker.py:292` | FAILURE "Depth limit reached" |
| 缺少 `decomposition_request` | `worker.py:307` | FAILURE "missing decomposition_request" |

#### 5.3 递归执行子任务

```python
# worker.py:316-328
for sub in result.decomposition_request.sub_tasks:
    sub_spec = TaskSpec(
        task_id=f"{spec.task_id}.{sub.id}",      # 如 "task-1.sub-1"
        description=sub.description,
        acceptance_criteria=spec.acceptance_criteria,  # 继承
        context=f"{spec.context}\nParent task: {sub.rationale}",
        depth=spec.depth + 1,                    # 深度 +1
    )
    sub_result = self.run(sub_spec)  # ← 递归！
```

#### 5.4 恢复执行

```python
# worker.py:331-343
resume_context = self._format_sub_results(sub_results)
resume_spec = TaskSpec(
    task_id=spec.task_id,
    description=f"resume: {spec.description}",
    acceptance_criteria=spec.acceptance_criteria,
    context=f"{spec.context}\n\n--- SUB-TASK RESULTS ---\n{resume_context}",
    depth=spec.depth,
)
final_result = self._execute_loop(resume_spec)  # 再跑一次 LLM 循环
```

#### 5.5 防止二次自分解

```python
# worker.py:346-359
if final_result.status == TaskStatus.NEEDS_DECOMPOSITION:
    return FAILURE "second self-decomposition"
```

只允许一次自分解——恢复执行后如果还要求分解，直接返回失败。

### 阶段 6：汇总返图

**代码位置**：`planner.py:973-1124`（`_summarize`） + `gatekeeper.py:816-888`（`_report_to_user`）

所有任务执行完毕后：

```python
summary = self._task_manager.get_summary()
# → {"total": 1, "pending": 0, "running": 0, "completed": 1, "failed": 0}

passed = summary["completed"]
return f"Completed: {passed}/{summary['total']} tasks.\n  [{r.status.value}] {r.summary}"
```

Console 输出（quiet 模式不同）：

```
━━━━━━━━━━━━━━━━━ 汇总 ━━━━━━━━━━━━━━━━━━
  ✅ 全部通过: 1/1
```

---

## Task 路径完整 Console 输出

```
> 写一个阶乘函数
                                        ← main.py:201 空行

  💭 [Gatekeeper] <decide 推理过程>      ← _decide

  💭 [Gatekeeper] <formulate 推理过程>   ← _formulate_directive

🔍 Gatekeeper 分析完成，拆分为 1 个子任务：
  ✓ task-1 · 实现一个计算阶乘的Python函数

┌─ task-1 · 实现一个计算阶乘的Python函数 ────────────┐
│  ⚡ 写入文件: factorial.py
│  ⚡ 执行命令: python factorial.py
│  ⚡ 读取文件: factorial.py
│  ⚡ 执行命令: pytest test_factorial.py
│
│  🔍 Reviewer 审核中...
│  ✅ 通过
│     ✓ 函数正确计算 0!=1, 1!=1, 5!=120
│     ✓ 包含输入验证和类型提示
│     ✓ 测试全部通过
│  ⏱ 耗时 12.3s
└──────────────────────────────────────────────────┘
  ✅ task-1 · 通过

━━━━━━━━━━━━━━━━━ 汇总 ━━━━━━━━━━━━━━━━━━
  ✅ 全部通过: 1/1

Completed: 1/1 tasks.
  [success] 成功实现阶乘函数，包含输入验证、类型提示和测试
                                        ← main.py:204 空行
>
```

---

## Task 路径完整 ASCII 流程图

```
  👤 用户输入 "写一个阶乘函数"
          │
          ▼
  ┌─── Session.handle ──→ Gatekeeper.handle ────────────────────────────┐
  │                                                                      │
  │  ┌──────────────────────────────────────────────────────────────┐   │
  │  │  _decide("写一个阶乘函数")                                     │   │
  │  │    System: CONTEXT_DISCIPLINE + DECIDE_SYSTEM_PROMPT          │   │
  │  │    User:   "写一个阶乘函数"                                    │   │
  │  │    → LLM API (thinking=enabled)                               │   │
  │  │    → {"action": "task"}                                       │   │
  │  │    → 💭 [Gatekeeper] reasoning                                │   │
  │  └──────────────────────────────────────────────────────────────┘   │
  │                                                                      │
  │  action == "task" → _execute_via_planner                              │
  │  ┌──────────────────────────────────────────────────────────────┐   │
  │  │  TM.reset()   清空任务记录                                     │   │
  │  │                                                                  │   │
  │  │  _formulate_directive("写一个阶乘函数")                            │   │
  │  │    → LLM API (thinking=enabled)                               │   │
  │  │    → Directive(goal=…, intent=…, constraints=…)               │   │
  │  │    → 💭 [Gatekeeper] reasoning                                │   │
  │  │                                                                  │   │
  │  │  Planner.execute(directive)                                      │   │
  │  │    ├─ _plan(directive) ── LLM③                                 │   │
  │  │    │   → [{task_id: "task-1", description: "实现阶乘…", …}]      │   │
  │  │                                                                  │   │
  │  │  🔍 Console: "分析完成，拆分为 1 个子任务"                       │   │
  │  │                                                                  │   │
  │  │  for each TaskSpec:                                             │   │
  │  │    ┌────────────────────────────────────────────────────────┐  │   │
  │  │    │  TM: add_task → PENDING                                 │  │   │
  │  │    │  TM: mark_running → RUNNING                             │  │   │
  │  │    │  Console: task_start                                    │  │   │
  │  │    │                                                         │  │   │
  │  │    │  _dispatch_with_review(spec, max_retries=2)             │  │   │
  │  │    │    ┌── _run_worker(spec) ─────────────────────────┐    │  │   │
  │  │    │    │  worker = factory()                           │    │  │   │
  │  │    │    │  worker.console = console                     │    │  │   │
  │  │    │    │  result = worker.run(spec)                    │    │  │   │
  │  │    │    │    └─ _execute_loop(spec)                     │    │  │   │
  │  │    │    │         messages = [Worker SYSTEM_PROMPT,     │    │  │   │
  │  │    │    │                     "Begin working…"]          │    │  │   │
  │  │    │    │         while < 50 tool_calls:                │    │  │   │
  │  │    │    │           LLM API (thinking, tools=schemas)   │    │  │   │
  │  │    │    │           ├─ tool_calls → 执行工具 → 继续     │    │  │   │
  │  │    │    │           └─ content   → _parse_result        │    │  │   │
  │  │    │    │         return TaskResult                     │    │  │   │
  │  │    │    │                                               │    │  │   │
  │  │    │    │    [可选] NEEDS_DECOMPOSITION:                │    │  │   │
  │  │    │    │      → 递归 self.run(sub_spec) 对每个子任务    │    │  │   │
  │  │    │    │      → resume _execute_loop(resume_spec)      │    │  │   │
  │  │    │    │      → 禁止二次自分解                          │    │  │   │
  │  │    │    └──────────────────────────────────────────────┘    │  │   │
  │  │    │                                                         │  │   │
  │  │    │    ┌── reviewer.review(spec, result) ─────────────┐    │  │   │
  │  │    │    │  FAILURE → auto-pass                          │    │  │   │
  │  │    │    │  System: REVIEW_SYSTEM_PROMPT                  │    │  │   │
  │  │    │    │  User:   REVIEW_USER_TEMPLATE (spec+result)   │    │  │   │
  │  │    │    │  → LLM API (普通，无 thinking)                 │    │  │   │
  │  │    │    │  → ReviewResult(status="pass"|"fail")         │    │  │   │
  │  │    │    └──────────────────────────────────────────────┘    │  │   │
  │  │    │                                                         │  │   │
  │  │    │    passed? → mark_completed, Console.review_pass       │  │   │
  │  │    │    未通过? → 注入反馈到 context, retry                  │  │   │
  │  │    └────────────────────────────────────────────────────────┘  │   │
  │  │                                                                  │   │
  │  │    Console: task_done(耗时)                                      │   │
  │  │                                                                  │   │
  │  │  TM.get_summary() → {total, completed, failed}                  │   │
  │  │  Console.summary                                                │   │
  │  │                                                                  │   │
  │  │  return "Completed: 1/1 tasks.\n  [success] …"                 │   │
  │  └──────────────────────────────────────────────────────────────┘   │
  └──────────────────────────────────────────────────────────────────────┘
          │
          ▼
  ┌─── main.py: print(answer) ───┐
  │ "Completed: 1/1 tasks.       │
  │   [success] 成功实现阶乘…"   │
  └───────────────────────────────┘
```

---

## TaskManager 状态转换追踪

> **源码**：`task_manager.py:52-57`、`planner.py:208-243`

```
时间点    Planner 操作                          TM 状态
──────────────────────────────────────────────────────────────
T0         Planner.execute() 开始               (reset → 空表)
T1 L345    tm.add_task(spec)                    PENDING
T2 L346    tm.mark_running(task_id, worker_id)  PENDING → RUNNING
T3 L348    _dispatch_with_review(spec)
              ├─ _run_worker(spec)               RUNNING + Worker 在执行
              │    ├─ LLM 调用                   RUNNING
              │    ├─ write_file("factorial.py")  RUNNING
              │    ├─ terminal("python …")        RUNNING
              │    └─ 返回 TaskResult            RUNNING
              └─ reviewer.review(spec, result)   RUNNING
T4 L398    tm.mark_completed(task_id, result)    RUNNING → COMPLETED
   或
T4' L427  tm.mark_failed(task_id, error)        RUNNING → FAILED
```

状态转换规则：`PENDING → RUNNING → COMPLETED/FAILED`，非法转换会抛 `ValueError`。

---

## 附录 A：系统提示词汇总

### A.1 CONTEXT_DISCIPLINE_PROMPT（上下文纪律）

> **位置**：`core/prompts.py:14-31`
> **使用**：Gatekeeper 和 Planner 的 LLM 调用的 system message

强调角色是"高管/参谋"——只看架构层面信息，不让 Worker 的实现细节污染上下文。由 `context_discipline_prompt()` 函数按角色动态生成。

### A.2 DECIDE_SYSTEM_PROMPT（决策分类）

> **位置**：`gatekeeper.py:75-77`

让 LLM 判断一条消息是 chat 还是 task，输出 JSON `{"action": "chat"|"task"}`。

### A.3 FORMULATE_SYSTEM_PROMPT（战略方向制定）

> **位置**：`gatekeeper.py:101-105`

让 Gatekeeper 把用户目标翻译为战略指令（Directive），提取意图、约束和优先级。不分解为 TaskSpec。

### A.4 CHAT_SYSTEM_PROMPT（聊天回复）

> **位置**：`gatekeeper.py:79-81`

极简 prompt——告诉 LLM 它是助手，自然回复，默认用中文，除非用户要求才输出 JSON/代码块。

### A.5 GATEKEEPER_IDENTITY（Gatekeeper 身份）

> **位置**：`gatekeeper.py:83-99`

Janus 最高决策层的自我认知——只负责战略方向，零工具，不亲自执行。

### A.6 PLANNER_IDENTITY（Planner 身份）

> **位置**：`planner.py:84-100`

战术执行层的自我认知——接收 Directive，拆解为 TaskSpec，分派 Worker，追踪进度，汇总结果。零工具。

### A.7 PLAN_SYSTEM_PROMPT（战术分解）

> **位置**：`planner.py:102-106`

让 Planner 把战略指令拆成独立的子任务。每个子任务包含 `task_id`、`description`、`acceptance_criteria`、`context`。

### A.8 Worker SYSTEM_PROMPT_TEMPLATE（任务执行）

> **位置**：`worker.py:197-213`

最长的一个 prompt。描述 Worker 身份、填充 TaskSpec 的字段（description/acceptance_criteria/context），给出 TaskResult 的 JSON schema，说明工具使用规则，以及可选的自分解路径。强调"不要模拟——真的调工具"。

### A.9 REVIEW_SYSTEM_PROMPT（审计）

> **位置**：`reviewer.py:253-279`

让 Reviewer 按验收标准逐条核查，用 [HARD]/[SOFT] 分类、Severity 四级，输出 ReviewVerdict 五级判定。

### A.10 REVIEW_USER_TEMPLATE（审计素材）

> **位置**：`reviewer.py:281-328`

把 TaskSpec 和 Worker 的 TaskResult 填入模板，要求 LLM 输出包含 `verdict`、`issues`、`evidence` 的 JSON。

---

## 附录 B：错误降级策略

| 场景 | 位置 | 处理 |
|---|---|---|---|
| `_decide` API 异常 | `gatekeeper.py:233-235` | 默认 `{"action": "task"}` |
| `_decide` JSON 解析失败 | `gatekeeper.py:251-255` | 默认 `{"action": "task"}` |
| `_respond` API 异常 | `gatekeeper.py:311-313` | `"Chat error: <ExceptionType>: <msg>"` |
| `_formulate_directive` API 异常 | `gatekeeper.py:445-457` | 模板 Directive（intent=""） |
| `_plan` API 异常 | `planner.py:334-337` | 空列表，设置 `_last_error` |
| `_plan` 非 JSON | `planner.py:349-357` | 空列表，设置 `_last_error` |
| `_plan` 返回 error dict | `planner.py:360-363` | 空列表，设置 `_last_error` |
| `_plan` 返回空 specs | `planner.py:421-426` | 设置 `_last_error` |
| Worker factory 崩溃 | `planner.py:837-872` | FAILURE TaskResult |
| `worker.run()` 异常 | `planner.py:889-911` | FAILURE TaskResult |
| Worker API 异常（循环内） | `worker.py:493-499` | FAILURE TaskResult |
| Worker 工具调用预算耗尽 | `worker.py:582-591` | FAILURE TaskResult |
| Worker 无法解析 TaskResult | `worker.py:888-894` | FAILURE TaskResult |
| Worker 自分解第二次 | `worker.py:405-420` | FAILURE TaskResult |
| Worker 深度超 3 | `worker.py:285-297` | FAILURE TaskResult |
| Worker 缺少分解请求 | `worker.py:300-306` | FAILURE TaskResult |
| Reviewer API 异常 | `reviewer.py:548-555` | `ReviewResult(verdict=REJECTED)` |
| Reviewer 空响应 | `reviewer.py:557-572` | `ReviewResult(verdict=REJECTED)` |
| Reviewer JSON 解析失败 | `reviewer.py:613-618` | `ReviewResult(verdict=REJECTED)` |
| Review 不通过 + 重试用尽 | `planner.py:641-674` | FAILURE TaskResult |
| 工具执行异常 | `worker.py:178-181` | 错误字符串（不打死 Worker 循环） |

---

> *文档基于 Janus Phase 4 源码（`main.py` 266 行，`core/` 共 ~4770 行）。*
