# Janus 完整信息流追踪

> ⚠️ **历史文档** — 此文件是 Phase 2 时期的详细执行追踪，架构已演进（引入 Planner）。最新信息流请参考 `docs/information-flow.md`。

---

## 目录
1. [组件总览](#1-组件总览)
2. [全局入口：main.py](#2-全局入口mainpy)
3. [路径A：Chat 消息（"你好"）](#3-路径achat-消息你好)
4. [路径B：Task 消息（"写一个阶乘函数"）](#4-路径btask-消息写一个阶乘函数)
5. [关键附录：系统提示词全量](#5-关键附录系统提示词全量)

---

## 1. 组件总览

| 文件 | 行数 | 角色 | 是否有 LLM 调用 | 是否有工具 |
|------|------|------|:---:|:---:|
| `main.py` | 208 | 入口、REPL 循环 | ❌ | ❌ |
| `core/session.py` | 72 | 对话历史记录器 (纯透传) | ❌ | ❌ |
| `core/gatekeeper.py` | 610 | 决策者 + 任务分解 + 调度 | ✅ 3次 | ❌ (零工具) |
| `core/worker.py` | 928 | 任务执行循环 + 工具调用 | ✅ 循环调用 | ✅ 9个工具 |
| `core/reviewer.py` | 295 | 独立审计 Agent | ✅ 1次 | ❌ (零工具) |
| `core/task_manager.py` | 207 | 任务生命周期状态机 | ❌ | ❌ |
| `core/protocol.py` | 158 | 数据类型定义 | ❌ | ❌ |
| `core/console.py` | 307 | 被动输出格式化 (中文+emoji) | ❌ | ❌ |

### 调用关系图

```
main.py (REPL)
  └─ Session.handle(user_input)
       └─ Gatekeeper.handle(message)
            ├─ _decide(message) ─── LLM调用① ──→ {"action": "chat"|"task"}
            │
            ├─ [chat] _respond(message) ─── LLM调用② ──→ 文本响应
            │
            └─ [task] _execute_task(goal)
                 ├─ _decompose(goal) ─── LLM调用② ──→ [TaskSpec, ...]
                 │
                 └─ 对每个 TaskSpec:
                      _run_worker(spec)
                      │   └─ worker.run(spec)
                      │        └─ _execute_loop(spec) ─── LLM循环③ ──→ TaskResult
                      │             └─ 可选自分解 → 递归 run()
                      │
                      └─ _dispatch_with_review(spec)
                           └─ reviewer.review(spec, result) ─── LLM调用④ ──→ ReviewResult
```

---

## 2. 全局入口：main.py

### 2.1 启动流程 (main.py:117-208)

```
main.py:125   _load_dotenv()                       — 加载 .env 文件到环境变量
main.py:128   config_path = Path(...) / "config.yaml"
main.py:130   raw = load_config(config_path)       — YAML 解析 (line 35-54)
main.py:137   cfg = resolve_config(raw)            — 解析 ${VAR} 占位符 (line 57-87)
main.py:142   gatekeeper_model = cfg["gatekeeper"]["model"] 或 cfg["model"]["model"]
main.py:145   worker_model = cfg["worker"]["model"] 或 None
main.py:146   api_key = cfg["model"]["api_key"]
main.py:147   max_tool_calls = cfg["worker"].get("max_tool_calls", 50)
main.py:148   max_depth = cfg["janus"].get("max_depth", 3)
main.py:151   mode = "default"/"verbose"/"quiet"    — 解析 CLI 参数 --verbose/-v/--quiet/-q
main.py:157   console = Console(mode=mode)
main.py:160   registry = create_default_registry()  — 注册9个真实工具 (worker.py:828-928)
main.py:161   tm = TaskManager()
main.py:164   _make_worker = lambda (worker工厂)
main.py:172   gk = Gatekeeper(...)                   — 注入 reviewer=Reviewer(...), console
main.py:183   session = Session(gk)                  — max_history=100 (默认)
```

### 2.2 REPL 循环 (main.py:186-204)

```
main.py:186   print("Janus — multi-turn agent framework.")
main.py:189   while True:
main.py:191       user_input = input("> ")          — 获取用户输入
main.py:196       if user_input == "quit": break
main.py:198       if blank: continue
main.py:201       print()                            — 空行
main.py:202       answer = session.handle(user_input) — 【核心入口】
main.py:203       print(answer)                      — 显示结果
main.py:204       print()                            — 空行
```

**用户看到的内容**：
- 启动时：`"Janus — multi-turn agent framework.\nType your goal or 'quit' to exit.\n"`
- 输入后：空行 + Gatekeeper 返回的字符串 + 空行

---

## 3. 路径A：Chat 消息（"你好"）

### 完整调用链 (每行标注)

```
Step 1: main.py:202  answer = session.handle(user_input)
         ↓
Step 2: session.py:51  result = self._gk.handle(user_input)
         ↓
Step 3: gatekeeper.py:179  decision = self._decide(message)
         ↓
Step 4: gatekeeper.py:182  action == "chat" → return self._respond(message)
         ↓
Step 5: session.py:54-57  记录到 _history，修剪到 max_history*2 条
Step 6: session.py:60     return result
         ↓
Step 7: main.py:203       print(answer)
```

### Step 3 详解：Gatekeeper._decide("你好")

**代码位置**：`gatekeeper.py:220-255`

#### 3.1 构建 LLM 消息 (line 226-230)

```
messages = [
    {"role": "system", "content": _CONTEXT_DISCIPLINE_PROMPT},     // line 79-86
    {"role": "system", "content": _DECIDE_SYSTEM_PROMPT},          // line 88-91
    {"role": "user", "content": "你好"},
]
```

**LLM 看到的完整 System Prompt #1（上下文纪律）**：
```
CRITICAL: Your context window is precious and limited.
- You are the top-level decision maker, like an executive talking to their assistant
- Only keep architecture-level information: what tasks exist, their status, key decisions
- NEVER load Worker implementation details, tool outputs, or verbose results into your context
- Worker results should be summarized to one line: "[task-id]: PASS/FAIL — one sentence"
- When tasks return large outputs, summarize them immediately before they enter your context
- Your job is direction and decisions, not implementation
```

**LLM 看到的完整 System Prompt #2（决策）**：
```
You are Janus Gatekeeper. Your context is precious — only keep high-level decisions.
Given a user message, decide: is this a TASK (needs decomposition and worker dispatch) or CHAT (simple conversation)?
Output ONLY valid JSON: {"action": "chat"|"task", "reason": "why"}
```

#### 3.2 API 调用 (line 233-237)

```python
response = self._client.chat.completions.create(
    model=self._model,               # 如 "deepseek-v4-pro"
    messages=messages,
    extra_body={"thinking": {"type": "enabled"}},
)
```

**关键**：thinking 模式开启，LLM 返回 `reasoning_content` 属性。

#### 3.3 解析响应 (line 242-255)

```python
choice = response.choices[0]                          # line 242
content = choice.message.content or ""                # line 243
reasoning = getattr(choice.message, "reasoning_content", None)  # line 246
```

**reasoning_content 去向** (line 247-248)：
```python
if reasoning and self._console:
    self._console.think_block(reasoning[:500], "Gatekeeper")
```
→ Console 以 `💭 [Gatekeeper]` 前缀打印（截断 500 字符）

**JSON 解析** (line 250-252)：
```python
parsed = self._extract_json(content)
# 返回 {"action": "chat", "reason": "简单问候"}
return {"action": str(parsed.get("action", "task")), "reason": str(parsed.get("reason", ""))}
```

**降级策略** (line 239-240, 254-255)：
- API 异常 → `{"action": "task", "reason": "API error: ..."}`
- JSON 解析失败 → `{"action": "task", "reason": "unparseable decision"}`

#### 3.4 用户看到的内容

如果 Console mode = "default"/"verbose"：
```
  💭 [Gatekeeper] <reasoning_content 前500字符>
```

### Step 4 详解：Gatekeeper._respond("你好")

**代码位置**：`gatekeeper.py:259-296`

#### 4.1 构建 LLM 消息 (line 272-276)

```
messages = [
    {"role": "system", "content": _CONTEXT_DISCIPLINE_PROMPT},     // line 79-86 (同上)
    {"role": "system", "content": _CHAT_SYSTEM_PROMPT},            // line 113-114
    {"role": "user", "content": "你好"},
]
```

**LLM 看到的 Chat System Prompt** (line 113-114)：
```
You are Janus, a helpful AI assistant.  Respond naturally to the user.
```

#### 4.2 API 调用 (line 279-283)
```python
response = self._client.chat.completions.create(
    model=self._model,
    messages=messages,
    extra_body={"thinking": {"type": "enabled"}},
)
```

#### 4.3 解析响应 (line 288-296)
```python
choice = response.choices[0]                          # line 288
content = choice.message.content or "(no response)"   # line 289
reasoning = getattr(choice.message, "reasoning_content", None)  # line 292
if reasoning and self._console:                       # line 293
    self._console.think_block(reasoning[:500], "Gatekeeper")  # line 294
return content                                         # line 296
```

**reasoning_content 去向**：再次通过 Console 以 `💭 [Gatekeeper]` 显示。

**降级策略** (line 284-286)：
- API 异常 → `"Chat error: <ExceptionType>: <message>"`

#### 4.4 返回到 Session (session.py:51-60)

```python
result = self._gk.handle(user_input)    # line 51, 拿到 chat 文本响应
self._history.append({"role": "user", "content": user_input})      # line 54
self._history.append({"role": "assistant", "content": result})     # line 55
if len(self._history) > self._max_history * 2:                     # line 56
    self._history = self._history[-(self._max_history * 2):]       # line 57
self._last_result = result                                          # line 59
return result                                                        # line 60
```

**关键**：Session 的 `_history` 存储的是 `max_history=100` 个 turn-pairs（200条消息），超出时保留最后200条。**但这个 history 目前只存储，不使用**——Gatekeeper 的每次 LLM 调用都是无状态的，不传入历史。

#### 4.5 最终显示 (main.py:203)

```python
print(answer)   # 如 "你好！有什么我可以帮助你的吗？"
```

### 路径A 完整上下文图

```
                          ┌─────────────────────────────────────────────┐
                          │            main.py REPL 循环                 │
                          │  user_input = input("> ")   # "你好"        │
                          │  answer = session.handle("你好")            │
                          │  print(answer)                              │
                          └──────────────────┬──────────────────────────┘
                                             │
                          ┌──────────────────▼──────────────────────────┐
                          │            Session.handle()                  │
                          │  result = self._gk.handle("你好")           │
                          │  _history += [user_msg, assistant_msg]      │
                          │  return result                               │
                          └──────────────────┬──────────────────────────┘
                                             │
                          ┌──────────────────▼──────────────────────────┐
                          │          Gatekeeper.handle()                  │
                          │                                              │
                          │  ┌─────────────────────────────────────┐    │
                          │  │ _decide("你好")                       │    │
                          │  │   System: CONTEXT_DISCIPLINE +       │    │
                          │  │            DECIDE_SYSTEM_PROMPT      │    │
                          │  │   User: "你好"                       │    │
                          │  │   → LLM API (thinking=enabled)      │    │
                          │  │   → {"action": "chat"}              │    │
                          │  │   → console.think_block()            │    │
                          │  └─────────────────────────────────────┘    │
                          │                                              │
                          │  action=="chat":                             │
                          │  ┌─────────────────────────────────────┐    │
                          │  │ _respond("你好")                      │    │
                          │  │   System: CONTEXT_DISCIPLINE +       │    │
                          │  │            CHAT_SYSTEM_PROMPT        │    │
                          │  │   User: "你好"                       │    │
                          │  │   → LLM API (thinking=enabled)      │    │
                          │  │   → "你好！有什么可以帮助你的？"     │    │
                          │  │   → console.think_block()            │    │
                          │  └─────────────────────────────────────┘    │
                          │  return "你好！有什么可以帮助你的？"        │
                          └─────────────────────────────────────────────┘
```

### 路径A 中 Console 输出的完整顺序

```
[启动] Janus — multi-turn agent framework.
       Type your goal or 'quit' to exit.

> 你好
                                  ← main.py:201 print()
  💭 [Gatekeeper] <decide reasoning>  ← console.think_block in _decide
  💭 [Gatekeeper] <respond reasoning> ← console.think_block in _respond
你好！有什么可以帮助你的吗？       ← main.py:203 print(answer)
                                  ← main.py:204 print()
>
```

---

## 4. 路径B：Task 消息（"写一个阶乘函数"）

### 完整调用链总览

```
main.py:202    answer = session.handle("写一个阶乘函数")
  └─ session.py:51    result = self._gk.handle(user_input)
       └─ gatekeeper.py:179    decision = self._decide(message)  → {"action": "task"}
            └─ gatekeeper.py:185    return self._execute_task(message)
                 │
                 ├─ gatekeeper.py:318    self._task_manager.reset()
                 ├─ gatekeeper.py:321    specs = self._decompose(goal)
                 │
                 └─ gatekeeper.py:343-360    for each spec:
                      │
                      ├─ TM: add_task(spec)           [PENDING]
                      ├─ TM: mark_running(id, ...)    [PENDING→RUNNING]
                      ├─ Console: task_start(id, desc)
                      │
                      ├─ gatekeeper.py:352    result = self._dispatch_with_review(spec)
                      │    │
                      │    └─ gatekeeper.py:392    result = self._run_worker(spec)
                      │         └─ worker.py:286    result = worker.run(spec) → TaskResult
                      │              │
                      │              └─ worker.py:286    result = self._execute_loop(spec)
                      │                   │
                      │                   └─ [LLM 工具调用循环] → TaskResult
                      │                        │
                      │                        └─ [可选] worker.py:288-361 自分解递归
                      │
                      ├─ gatekeeper.py:400    review = reviewer.review(spec, result)
                      │    └─ reviewer.py:180-252    LLM 审计 → ReviewResult
                      │
                      ├─ TM: mark_completed(id, result) / mark_failed(id, error)
                      └─ Console: task_done(id, status, elapsed)
                 │
                 └─ gatekeeper.py:363-379    summary = TM.get_summary()
                      Console: summary(total, passed, failed)
                      return "Completed: N/M tasks.\n  [...]"
```

### 阶段1：Gatekeeper._execute_task 入口 (gatekeeper.py:300-379)

```python
def _execute_task(self, goal: str) -> str:          # line 300
    self._task_manager.reset()                       # line 318  ← 清空全部任务记录
    specs = self._decompose(goal)                    # line 321  ← LLM调用: 分解目标
    if not specs:                                    # line 323
        return error_message                         # line 325-329 ← 包含 self._last_error
    # Console: phase_decompose                       # line 334-339
    results: list[TaskResult] = []                   # line 342
    for i, spec in enumerate(specs):                 # line 343
        worker_id = f"worker-{i}"                    # line 344
        self._task_manager.add_task(spec)            # line 345  ← TM: PENDING
        self._task_manager.mark_running(spec.task_id, worker_id=worker_id)  # line 346 ← PENDING→RUNNING
        console.task_start(spec.task_id, spec.description)  # line 348-349
        t_start = time.perf_counter()                # line 351
        result = self._dispatch_with_review(spec, max_retries=2)  # line 352 ← 核心调度
        elapsed = time.perf_counter() - t_start      # line 353
        result.worker_id = worker_id                 # line 354
        results.append(result)                       # line 355
        console.task_done(spec.task_id, result.status.value, elapsed)  # line 357-360
    # Summary
    summary = self._task_manager.get_summary()       # line 363
    console.summary(summary["total"], passed, summary["failed"])  # line 372-373
    return f"Completed: {passed}/{total} tasks.\n  [...]"  # line 374-379
```

### 阶段2：Gatekeeper._decompose 目标分解 (gatekeeper.py:490-565)

#### 2.1 构建 LLM 消息 (line 496-501)

```python
system_prompt = self._DECOMPOSE_SYSTEM_PROMPT.format(goal=goal)  # line 496
messages = [
    {"role": "system", "content": _CONTEXT_DISCIPLINE_PROMPT},    # line 498
    {"role": "system", "content": system_prompt},                 # line 499
    {"role": "user", "content": goal},                            # line 500
]
```

**LLM 看到的完整 Decompose System Prompt** (line 93-111)：
```
You are a Janus Gatekeeper. Your sole job is to decompose a user's goal into \
discrete, executable sub-tasks.

Given a goal, output a JSON array of task objects. Each task object must have:
- "task_id": string, unique identifier (e.g., "task-1", "task-2")
- "description": string, what to do — concrete and actionable
- "acceptance_criteria": string, how to know it's done right
- "context": string, relevant background information for this specific task

Rules:
- Each task must be self-contained enough for a Worker to execute independently
- Tasks should be independent when possible (no inter-task dependencies for Phase 1)
- If the goal is simple, a single task is acceptable
- If the goal is too vague to decompose, output: {"error": "reason"}
- Output ONLY valid JSON, no extra text

Goal: 写一个阶乘函数
```

#### 2.2 API 调用 (line 503-508)
```python
response = self._client.chat.completions.create(
    model=self._model,
    messages=messages,
    extra_body={"thinking": {"type": "enabled"}},
)
```

#### 2.3 reasoning_content 捕获 (line 518-520)
```python
reasoning = getattr(choice.message, "reasoning_content", None)
if reasoning and self._console:
    self._console.think_block(reasoning[:500], "Gatekeeper")  # 截断500字符
```

#### 2.4 JSON 解析 → TaskSpec 列表 (line 522-559)

```python
raw_data = self._extract_json(content)               # line 522
# 可能返回:
#   None → 错误 (line 524-530)
#   {"error": "..."} → 设置 _last_error (line 533-536)
#   [...] → 构造 TaskSpec 列表 (line 539-559)

for item in raw_data:
    specs.append(TaskSpec(
        task_id=str(item.get("task_id", "")),
        description=str(item.get("description", "")),
        acceptance_criteria=str(item.get("acceptance_criteria", "")),
        context=str(item.get("context", "")),
        depth=1,
    ))
```

**示例**：对于"写一个阶乘函数"，LLM 可能返回：
```json
[
  {
    "task_id": "task-1",
    "description": "实现一个计算阶乘的Python函数",
    "acceptance_criteria": "函数能正确计算0的阶乘为1、1的阶乘为1、5的阶乘为120；包含输入验证；有相应测试",
    "context": "用Python编写，包含文档字符串和类型提示"
  }
]
```

#### 2.5 Console 输出 (line 334-339)

```python
# 非 quiet 模式下：
tasks_lines = "\n".join(f"  ✓ {s.task_id} · {s.description}" for s in specs)
self._console.phase_decompose(len(specs), tasks_lines)
```

**输出示例**：
```
🔍 Gatekeeper 分析完成，拆分为 1 个子任务：
  ✓ task-1 · 实现一个计算阶乘的Python函数
```

### 阶段3：Worker 执行 (_run_worker → worker.run)

#### 3.1 Gatekeeper._run_worker (gatekeeper.py:439-486)

```python
worker = self._worker_factory(model_override=self._worker_model)  # line 447-449
if self._console is not None:                                     # line 472
    worker.console = self._console                                # line 473  ← 注入 Console
result = worker.run(spec)                                          # line 476  ← 核心调用
```

**降级策略**：
- factory 不支持 `model_override` → 尝试无参调用 (line 450-453)
- factory 全部失败 → FAILURE TaskResult (line 454-469)
- worker.run() 异常 → FAILURE TaskResult (line 478-484)

#### 3.2 Worker.run 入口 (worker.py:267-361)

```python
def run(self, spec: TaskSpec) -> TaskResult:
    result = self._execute_loop(spec)                    # line 286 ← 第一轮执行
    if result.status != TaskStatus.NEEDS_DECOMPOSITION:  # line 288
        return result                                     # line 289 ← 直接返回
    
    # ═══ 自分解路径 ═══
    if spec.depth >= MAX_WORKER_DEPTH:                   # line 292 (MAX_WORKER_DEPTH=3)
        return FAILURE "Depth limit reached"              # line 293-304
    
    if not result.decomposition_request or not sub_tasks: # line 307
        return FAILURE "missing decomposition_request"    # line 308-313
    
    # 递归执行每个子任务
    sub_results: list[TaskResult] = []                    # line 315
    for sub in result.decomposition_request.sub_tasks:    # line 316
        sub_spec = TaskSpec(                               # line 317-326
            task_id=f"{spec.task_id}.{sub.id}",            # 如 "task-1.sub-1"
            description=sub.description,
            acceptance_criteria=spec.acceptance_criteria,  # 继承父任务标准
            context=f"{spec.context}\nParent task: {sub.rationale}",
            depth=spec.depth + 1,                          # 深度+1
        )
        sub_result = self.run(sub_spec)                   # line 327 ← 递归
        sub_results.append(sub_result)                     # line 328
    
    # 用子结果恢复执行
    resume_context = self._format_sub_results(sub_results) # line 331
    resume_spec = TaskSpec(                                 # line 332-342
        task_id=spec.task_id,
        description=f"resume: {spec.description}",
        acceptance_criteria=spec.acceptance_criteria,
        context=f"{spec.context}\n\n--- SUB-TASK RESULTS ---\n{resume_context}",
        depth=spec.depth,
    )
    final_result = self._execute_loop(resume_spec)         # line 343 ← 第二轮执行
    
    # 防止二次自分解
    if final_result.status == TaskStatus.NEEDS_DECOMPOSITION:  # line 346
        return FAILURE "second self-decomposition"              # line 348-359
    
    return final_result                                        # line 361
```

#### 3.3 Worker._execute_loop 核心循环 (worker.py:363-499)

##### 3.3.1 构建系统提示词 (line 376-384)

```python
system_prompt = self._build_system_prompt(spec)          # line 376 → line 503-509
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": "Begin working on the task.  Use tools as needed.  "
                                "Output your final result as a TaskResult JSON when done."},
]
```

**_build_system_prompt 渲染 (line 503-509)**：
```python
def _build_system_prompt(self, spec: TaskSpec) -> str:
    return self._SYSTEM_PROMPT_TEMPLATE.format(
        description=spec.description,
        acceptance_criteria=spec.acceptance_criteria,
        context=spec.context,
    )
```

**LLM (Worker) 看到的完整 System Prompt** (line 197-234)：
```
You are a Janus Worker — an autonomous AI agent that executes tasks using available tools.

## Your Task
{description}              ← 如 "实现一个计算阶乘的Python函数"

## Acceptance Criteria
{acceptance_criteria}      ← 如 "函数能正确计算0的阶乘为1..."

## Context
{context}                  ← 如 "用Python编写，包含文档字符串和类型提示"

## Instructions
1. Use the available tools to complete the task.  Do not simulate — actually call them.
2. Be thorough.  Verify your work.
3. When you complete the task, output a JSON object following the TaskResult schema:

```json
{
  "status": "success" | "failure" | "needs_decomposition",
  "summary": "one sentence describing the outcome",
  "result": "full detail of what happened",
  "artifacts": ["path_or_identifier"],
  "confidence": "high" | "medium" | "low",
  "decomposition_request": {
    "reason": "why this task needs to be broken down",
    "sub_tasks": [
      {"id": "sub-1", "description": "...", "rationale": "why this sub-task is needed"}
    ]
  }
}
```

4. If the task is too complex, you may return status="needs_decomposition" with a
   decomposition_request.  The Worker will automatically decompose and execute
   sub-tasks, then feed their results back to you.  Only use this for genuinely
   complex tasks that cannot be completed in one pass.

5. Output ONLY the JSON object (or task-relevant text) — no extra commentary.
```

##### 3.3.2 工具调用循环 (line 386-489)

```python
schemas = self._registry.get_openai_schemas()             # line 386 — 获取9个工具的 OpenAI schema
tool_call_count = 0                                        # line 388

while tool_call_count < self._max_tool_calls:              # line 389 (默认50)
    # ── LLM API 调用 ──
    response = self._client.chat.completions.create(       # line 391-397
        model=self._model,
        messages=messages,                                  # 累积所有历史
        tools=schemas if schemas else None,
        tool_choice="auto" if schemas else None,
        extra_body={"thinking": {"type": "enabled"}},
    )
    
    choice = response.choices[0]                            # line 406
    msg = choice.message                                    # line 407
    tool_calls = msg.tool_calls                             # line 409
    content = msg.content                                   # line 410
    
    # ── 分支A: LLM 返回工具调用 ──
    if tool_calls:                                          # line 413
        # 构建 assistant 消息 (保留 reasoning_content)
        assistant_msg = {                                    # line 416-430
            "role": "assistant",
            "content": None,
            "tool_calls": [...]
        }
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            if self.console:
                self.console.think_block(msg.reasoning_content[:500], "Worker")  # line 435-438
            assistant_msg["reasoning_content"] = msg.reasoning_content           # line 439
        
        messages.append(assistant_msg)                       # line 441
        
        # 执行每个工具调用
        for tc in tool_calls:                                # line 444
            tool_call_count += 1                             # line 445
            arguments = json.loads(tc.function.arguments)    # line 447
            result_text = self._registry.execute(            # line 451-453
                tc.function.name, arguments
            )
            # Console 显示工具调用
            if self.console:                                 # line 456
                summary = self.console.build_tool_summary(tc.function.name, arguments)  # line 457-459
                self.console.tool_call(tc.function.name, summary)                      # line 460
                if self.console.is_verbose:                                              # line 461
                    self.console.tool_call_verbose(tc.function.name, arguments)          # line 462-464
            
            messages.append({                                 # line 466-472
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_text,
            })
        continue  # ← 回到 LLM，携带工具结果
    
    # ── 分支B: LLM 返回文本内容 → 解析为 TaskResult ──
    if content:                                              # line 477
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            if self.console:
                self.console.think_block(msg.reasoning_content[:500], "Worker")  # line 481-484
        return self._parse_result(content)                   # line 485
    
    # ── 分支C: 两者都无 → 退出循环 ──
    break                                                    # line 488

# 耗尽工具调用预算
return TaskResult(                                           # line 491-499
    status=TaskStatus.FAILURE,
    summary="Worker loop exhausted tool-call budget...",
    ...
)
```

##### 3.3.3 _parse_result 解析 (worker.py:536-578)

```python
# 1. 尝试从 ```json 围栏提取 (line 547)
fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
# 2. 回退到原始 { ... } (line 553)
raw_match = re.search(r"\{.*\}", text, re.DOTALL)

if json_str:
    data = json.loads(json_str)                    # line 559
    result = TaskResult.from_dict(data)             # line 560
    if not result.validate():                       # line 561 ← 检查 NEEDS_DECOMPOSITION 必须带 decomposition_request
        return FAILURE                              # line 562-567
    return result                                   # line 568

# 解析失败 → FAILURE (line 573-578)
return TaskResult(
    status=TaskStatus.FAILURE,
    summary="Could not parse LLM output as valid TaskResult JSON.",
    result=text.strip() or "(empty response)",
    confidence=Confidence.LOW,
)
```

#### 3.4 ToolRegistry.execute 工具执行 (worker.py:139-181)

```python
def execute(self, name: str, arguments: dict[str, Any]) -> str:
    tool = self._tools.get(name)                       # line 156
    if tool is None:                                   # line 157
        return f"Error: unknown tool '{name}'..."      # line 158
    
    # 1. 参数别名映射 (line 161-164)
    mapped = {}
    for k, v in arguments.items():
        canonical = _PARAM_ALIASES.get(k, k)           # 如 "url"→"target_url", "command"→"cmd"
        mapped[canonical] = v
    
    # 2. 用 inspect.signature 过滤有效参数 (line 168-175)
    sig = inspect.signature(tool.func)
    valid = {k: v for k, v in mapped.items() if k in sig.parameters}
    
    # 3. 调用实际函数，捕获所有异常 (line 178-181)
    try:
        return tool.func(**valid)
    except Exception as exc:
        return f"Error: tool '{name}' crashed: {type(exc).__name__}: {exc}"
```

**9个注册工具** (worker.py:828-928)：

| 工具名 | 函数 | 实际行为 |
|--------|------|---------|
| `read_file` | `_real_read_file` | 读文件，截断5000字符 |
| `write_file` | `_real_write_file` | 写文件，自动创建目录 |
| `terminal` | `_real_terminal` | subprocess 执行 shell 命令，60秒超时 |
| `web_search` | `_real_web_search` | ⚠ 占位符，未实现 |
| `web_extract` | `_real_web_extract` | HTTP GET 抓取网页，去HTML标签，截断3000字符/URL |
| `search_files` | `_real_search_files` | glob 文件名 + 内容搜索，最多50结果 |
| `patch` | `_real_patch` | 文件内查找替换（首次出现） |
| `execute_code` | `_real_execute_code` | exec() 执行 Python 代码，捕获 stdout |
| `browser_navigate` | `_real_browser_navigate` | ⚠ 占位符，需 Playwright |

#### 3.5 参数别名映射表 (worker.py:72-86)

```
_PARAM_ALIASES = {
    "url": "target_url",       "endpoint": "target_url",
    "command": "cmd",          "shell": "cmd",
    "prompt": "payload",       "text": "payload",
    "file": "path",            "filepath": "path",
    "q": "query",              "search": "query",
    "key": "api_key",          "token": "api_key",
    "filename": "path",
}
```

### 阶段4：Reviewer 审核 (gatekeeper.py:381-437 → reviewer.py:180-295)

#### 4.1 Gatekeeper._dispatch_with_review (gatekeeper.py:381-437)

```python
def _dispatch_with_review(self, spec, max_retries=2):
    for attempt in range(max_retries + 1):              # line 391
        result = self._run_worker(spec)                  # line 392 ← Worker 执行
        
        if not self._reviewer:                           # line 395
            self._task_manager.mark_completed(spec.task_id, result)  # line 396
            return result                                # line 397
        
        review = self._reviewer.review(spec, result)     # line 400 ← LLM 审计
        if review.passed:                                # line 401
            self._task_manager.mark_completed(spec.task_id, result)  # line 402
            console.review_pass(spec.task_id, review.evidence)        # line 403-404
            return result                                # line 405
        
        # 不通过 — 重试
        if attempt < max_retries:                        # line 408
            console.review_fail(spec.task_id, review.issues, attempt) # line 413-416
            spec = TaskSpec(                              # line 417-428 ← 重建 TaskSpec
                task_id=spec.task_id,
                description=spec.description,
                acceptance_criteria=spec.acceptance_criteria,
                context=f"{spec.context}\n\n"
                        f"PREVIOUS ATTEMPT FAILED REVIEW:\n"
                        f"{review.summary}\n"
                        f"Issues: {', '.join(review.issues)}",
                depth=spec.depth,
            )
    
    # 重试用尽
    self._task_manager.mark_failed(spec.task_id, "Review failed after max retries")  # line 431-433
    return TaskResult(status=TaskStatus.FAILURE, ...)                                   # line 434-437
```

#### 4.2 Reviewer.review (reviewer.py:180-252)

##### 4.2.1 FAILURE 自动跳过 (line 191-206)
```python
if result.status == TaskStatus.FAILURE:
    return ReviewResult(
        status="pass",
        summary="Worker reported failure — no audit needed.",
        ...
    )
```

##### 4.2.2 构建消息 (line 209-225)

**LLM (Reviewer) 看到的 System Prompt** (line 123-134)：
```
You are a Janus Reviewer. Your sole job is to audit deliverables against \
requirements.

Given a task specification with acceptance criteria and a Worker's delivered \
result, evaluate whether the result actually meets every criterion.

Be precise and evidence-based:
- For each acceptance criterion, state whether it is satisfied and cite \
specific evidence from the result.
- If a criterion is partially met, explain what is missing.
- Do NOT assume — if evidence is absent, flag it as an issue.
```

**LLM (Reviewer) 看到的 User Prompt** (line 136-157)：
```
TASK: {description}
ACCEPTANCE CRITERIA: {acceptance_criteria}
EXPECTED ARTIFACTS: {context}

DELIVERED RESULT:
Status: {status}
Summary: {summary}
Full Result: {result}
Artifacts: {artifacts}

For each acceptance criterion:
1. Does the result satisfy it?
2. What evidence proves it?

Output ONLY a JSON object with this schema:
{
  "status": "pass" | "fail",
  "summary": "one-line verdict",
  "issues": ["issue 1", "issue 2"],
  "evidence": "what proved success or what was missing"
}
```

##### 4.2.3 API 调用 (line 228-232)

```python
response = self._client.chat.completions.create(
    model=self._model,
    messages=messages,
)
# ⚠ 注意：Reviewer 的 API 调用没有启用 thinking mode
```

**超时设置**：120秒 (构造函数 line 161, API client line 175)

##### 4.2.4 解析 ReviewResult (line 252 → 256-295)

解析逻辑 (line 256-295)：尝试 ```json 围栏 → 原始 { } → JSON解析 → ReviewResult.from_dict()。

#### 4.3 重试时的 context 累积

第N次重试时，Worker 看到的 context 会包含之前所有失败的内容：

```
原始 context:
  "用Python编写，包含文档字符串和类型提示"

第1次重试 context:
  "用Python编写，包含文档字符串和类型提示\n\n
   PREVIOUS ATTEMPT FAILED REVIEW:
   缺少输入验证...
   Issues: 未处理负数输入, 缺少类型提示"

第2次重试 context:
  "用Python编写，包含文档字符串和类型提示\n\n
   PREVIOUS ATTEMPT FAILED REVIEW:
   缺少输入验证...
   Issues: 未处理负数输入, 缺少类型提示\n\n
   PREVIOUS ATTEMPT FAILED REVIEW:
   类型提示不完整...
   Issues: 返回类型缺失"
```

### 阶段5：TaskManager 状态追踪

**状态转换图** (task_manager.py:52-57)：

```
PENDING  ──→  RUNNING  ──→  COMPLETED (terminal)
                        └─→  FAILED    (terminal)
```

**完整状态流水线** (以 task-1 为例)：

```
时间点              Gatekeeper 操作                     TM 状态
───────────────────────────────────────────────────────────────────
T0                  _execute_task 开始                   (reset → 空)
T1   line 345       tm.add_task(spec)                   PENDING
T2   line 346       tm.mark_running("task-1", "worker-0")  PENDING → RUNNING
T3   line 352       result = _dispatch_with_review(spec)    RUNNING
                     ├─ _run_worker → Worker 开始执行        RUNNING
                     │   └─ worker.run(spec)                 RUNNING
                     │       └─ _execute_loop(spec)          RUNNING
                     │           ├─ LLM 调用                  RUNNING
                     │           ├─ write_file(...)           RUNNING
                     │           ├─ terminal("python ...")    RUNNING
                     │           ├─ read_file(..)             RUNNING
                     │           └─ 返回 TaskResult           RUNNING
                     └─ reviewer.review(spec, result)         RUNNING
T4   line 396/402   tm.mark_completed("task-1", result)    RUNNING → COMPLETED
                    或
T4'  line 431       tm.mark_failed("task-1", error)        RUNNING → FAILED
```

**TaskRecord 数据结构** (task_manager.py:30-48)：
```python
@dataclass
class TaskRecord:
    task_id: str                     # 如 "task-1"
    spec: TaskSpec                   # 原始任务规格
    state: TaskState                 # PENDING/RUNNING/COMPLETED/FAILED
    result: Optional[TaskResult]     # COMPLETED/FAILED 时有值
    worker_id: Optional[str]         # 如 "worker-0"
    created_at: datetime             # UTC 时间戳
```

**get_summary 返回值** (line 185-201)：
```python
{"total": 1, "pending": 0, "running": 0, "completed": 1, "failed": 0}
```

### 路径B 中 Console 输出的完整顺序

```
> 写一个阶乘函数
                                       ← main.py:201 (空行)

  💭 [Gatekeeper] <decide reasoning>   ← gatekeeper._decide L248

🔍 Gatekeeper 分析完成，拆分为 1 个子任务：     ← console.phase_decompose L122
  ✓ task-1 · 实现一个计算阶乘的Python函数

  💭 [Gatekeeper] <decompose reasoning> ← gatekeeper._decompose L520

┌─ task-1 · 实现一个计算阶乘的Python函数 ──...┐  ← console.task_start L160
│  ⚡ 写入文件: factorial.py                ← console.tool_call (write_file)
│  ⚡ 执行命令: python factorial.py         ← console.tool_call (terminal)
│  ⚡ 读取文件: factorial.py                ← console.tool_call (read_file)
│  ⚡ 写入文件: test_factorial.py           ← console.tool_call (write_file)
│  ⚡ 执行命令: pytest test_factorial.py    ← console.tool_call (terminal)
│
│  🔍 Reviewer 审核中...                     ← console.review_pass L223-224
│  ✅ 通过                                  ← console.review_pass L224
│     ✓ 函数正确计算0!=1, 1!=1, 5!=120     ← console.review_pass L229
│     ✓ 包含输入验证和类型提示              ← console.review_pass L229
│     ✓ 测试全部通过                       ← console.review_pass L229
│  ⏱ 耗时 12.3s                             ← console.task_done L174
└──────────────────────────────────────────┘  ← console.task_done L175
  ✅ task-1 · 通过                           ← console.task_done L176

━━━━━━━━━━━━━━━━━ 汇总 ━━━━━━━━━━━━━━━━━━   ← console.summary L284
  ✅ 全部通过: 1/1                           ← console.summary L286

Completed: 1/1 tasks.                        ← gatekeeper L375 (print)
  [success] 成功实现阶乘函数，包含输入验证、类型提示和测试    ← gatekeeper L377

                                       ← main.py:204 (空行)
>
```

### 路径B：reasoning_content 捕获点总结

| 阶段 | 代码位置 | LLM 角色 | 操作 | 截断长度 |
|------|---------|---------|------|---------|
| 决策 | gatekeeper.py:246-248 | Gatekeeper | console.think_block | 500 |
| 分解 | gatekeeper.py:518-520 | Gatekeeper | console.think_block | 500 |
| Worker工具调用中 | worker.py:433-438 | Worker | console.think_block | 500 |
| Worker返回文本 | worker.py:479-484 | Worker | console.think_block | 500 |
| Chat 响应 | gatekeeper.py:292-294 | Gatekeeper | console.think_block | 500 |

**⚠️ Reviewer 没有 reasoning_content 捕获**——它使用普通 API 调用（未启用 thinking）。

### 路径B：数据流汇总

```
User Input: "写一个阶乘函数"
    │
    ├─ Gatekeeper._decide ("写一个阶乘函数")
    │   ├─ LLM Input:  [CONTEXT_DISCIPLINE, DECIDE_PROMPT, "写一个阶乘函数"]
    │   ├─ LLM Output: {"action": "task", "reason": "需要代码生成"}
    │   └─ reasoning:  <displayed via console.think_block>
    │
    ├─ Gatekeeper._decompose ("写一个阶乘函数")
    │   ├─ LLM Input:  [CONTEXT_DISCIPLINE, DECOMPOSE_PROMPT(goal="写一个阶乘函数"), "写一个阶乘函数"]
    │   ├─ LLM Output: [{"task_id": "task-1", "description": "...", ...}]
    │   ├─ reasoning:  <displayed via console.think_block>
    │   └─ Result:     [TaskSpec(task_id="task-1", ...)]
    │
    ├─ Worker._execute_loop (spec = "实现阶乘函数")
    │   ├─ LLM Input:  [Worker SYSTEM_PROMPT(task=spec), "Begin working..."]
    │   ├─ 循环1:
    │   │   ├─ LLM Output: tool_call(write_file, path="factorial.py", content="...")
    │   │   ├─ reasoning:  <displayed via console.think_block>
    │   │   └─ Tool Result: "Successfully wrote N bytes to factorial.py"
    │   ├─ 循环2:
    │   │   ├─ LLM Output: tool_call(terminal, command="python factorial.py")
    │   │   ├─ reasoning:  <displayed via console.think_block>
    │   │   └─ Tool Result: "(no output)"
    │   ├─ ... (更多工具调用)
    │   └─ 最终:
    │       ├─ LLM Output: {"status": "success", "summary": "...", "result": "...", ...}
    │       ├─ reasoning:  <displayed via console.think_block>
    │       └─ Result:     TaskResult(status=SUCCESS, ...)
    │
    ├─ Reviewer.review (spec, result)
    │   ├─ LLM Input:  [REVIEWER_SYSTEM, REVIEWER_USER(task=spec, result=result)]
    │   ├─ LLM Output: {"status": "pass", "summary": "...", "evidence": "...", "issues": []}
    │   └─ Result:     ReviewResult(status="pass", ...)
    │
    └─ Gatekeeper Summary
        ├─ TM.get_summary() → {"total": 1, "completed": 1, "failed": 0}
        └─ Output: "Completed: 1/1 tasks.\n  [success] 成功实现阶乘函数..."
```

---

## 5. 关键附录：系统提示词全量

### 5.1 CONTEXT_DISCIPLINE_PROMPT (gatekeeper.py:79-86)
在所有 Gatekeeper LLM 调用的首个 system message 中使用：
```
CRITICAL: Your context window is precious and limited.
- You are the top-level decision maker, like an executive talking to their assistant
- Only keep architecture-level information: what tasks exist, their status, key decisions
- NEVER load Worker implementation details, tool outputs, or verbose results into your context
- Worker results should be summarized to one line: "[task-id]: PASS/FAIL — one sentence"
- When tasks return large outputs, summarize them immediately before they enter your context
- Your job is direction and decisions, not implementation
```

### 5.2 DECIDE_SYSTEM_PROMPT (gatekeeper.py:88-91)
在 `_decide` 中使用：
```
You are Janus Gatekeeper. Your context is precious — only keep high-level decisions.
Given a user message, decide: is this a TASK (needs decomposition and worker dispatch) or CHAT (simple conversation)?
Output ONLY valid JSON: {"action": "chat"|"task", "reason": "why"}
```

### 5.3 DECOMPOSE_SYSTEM_PROMPT (gatekeeper.py:93-111)
在 `_decompose` 中使用，`{goal}` 被替换为用户输入：
```
You are a Janus Gatekeeper. Your sole job is to decompose a user's goal into discrete, executable sub-tasks.

Given a goal, output a JSON array of task objects. Each task object must have:
- "task_id": string, unique identifier (e.g., "task-1", "task-2")
- "description": string, what to do — concrete and actionable
- "acceptance_criteria": string, how to know it's done right
- "context": string, relevant background information for this specific task

Rules:
- Each task must be self-contained enough for a Worker to execute independently
- Tasks should be independent when possible (no inter-task dependencies for Phase 1)
- If the goal is simple, a single task is acceptable
- If the goal is too vague to decompose, output: {"error": "reason"}
- Output ONLY valid JSON, no extra text

Goal: {goal}
```

### 5.4 CHAT_SYSTEM_PROMPT (gatekeeper.py:113-114)
在 `_respond` 中使用：
```
You are Janus, a helpful AI assistant.  Respond naturally to the user.
```

### 5.5 Worker SYSTEM_PROMPT_TEMPLATE (worker.py:197-234)
在 `_build_system_prompt` 中渲染，`{description}`, `{acceptance_criteria}`, `{context}` 来自 TaskSpec：
```
You are a Janus Worker — an autonomous AI agent that executes tasks using available tools.

## Your Task
{description}

## Acceptance Criteria
{acceptance_criteria}

## Context
{context}

## Instructions
1. Use the available tools to complete the task.  Do not simulate — actually call them.
2. Be thorough.  Verify your work.
3. When you complete the task, output a JSON object following the TaskResult schema:

```json
{
  "status": "success" | "failure" | "needs_decomposition",
  "summary": "one sentence describing the outcome",
  "result": "full detail of what happened",
  "artifacts": ["path_or_identifier"],
  "confidence": "high" | "medium" | "low",
  "decomposition_request": {
    "reason": "why this task needs to be broken down",
    "sub_tasks": [
      {"id": "sub-1", "description": "...", "rationale": "why this sub-task is needed"}
    ]
  }
}
```

4. If the task is too complex, you may return status="needs_decomposition" with a
   decomposition_request.  The Worker will automatically decompose and execute
   sub-tasks, then feed their results back to you.  Only use this for genuinely
   complex tasks that cannot be completed in one pass.

5. Output ONLY the JSON object (or task-relevant text) — no extra commentary.
```

### 5.6 REVIEW_SYSTEM_PROMPT (reviewer.py:123-134)
```
You are a Janus Reviewer. Your sole job is to audit deliverables against requirements.

Given a task specification with acceptance criteria and a Worker's delivered result, evaluate whether the result actually meets every criterion.

Be precise and evidence-based:
- For each acceptance criterion, state whether it is satisfied and cite specific evidence from the result.
- If a criterion is partially met, explain what is missing.
- Do NOT assume — if evidence is absent, flag it as an issue.
```

### 5.7 REVIEW_USER_TEMPLATE (reviewer.py:136-157)
```
TASK: {description}
ACCEPTANCE CRITERIA: {acceptance_criteria}
EXPECTED ARTIFACTS: {context}

DELIVERED RESULT:
Status: {status}
Summary: {summary}
Full Result: {result}
Artifacts: {artifacts}

For each acceptance criterion:
1. Does the result satisfy it?
2. What evidence proves it?

Output ONLY a JSON object with this schema:
{
  "status": "pass" | "fail",
  "summary": "one-line verdict",
  "issues": ["issue 1", "issue 2"],
  "evidence": "what proved success or what was missing"
}
```

---

## 附录：错误处理与降级策略汇总

| 场景 | 代码位置 | 处理方式 |
|------|---------|---------|
| _decide API 异常 | gatekeeper.py:238-240 | 默认 → `{"action": "task"}` |
| _decide 解析失败 | gatekeeper.py:254-255 | 默认 → `{"action": "task"}` |
| _respond API 异常 | gatekeeper.py:284-286 | 返回错误字符串 `"Chat error: ..."` |
| _decompose API 异常 | gatekeeper.py:509-512 | 返回空列表 `[]`，设置 `_last_error` |
| _decompose 非 JSON | gatekeeper.py:525-530 | 返回空列表，设置 `_last_error` |
| _decompose 返回 error dict | gatekeeper.py:533-536 | 返回空列表，设置 `_last_error` |
| _decompose 空 specs | gatekeeper.py:323-329 | 返回详细错误信息 |
| Worker factory 崩溃 | gatekeeper.py:454-469 | FAILURE TaskResult |
| Worker.run 异常 | gatekeeper.py:477-484 | FAILURE TaskResult |
| Worker API 异常 | worker.py:398-404 | FAILURE TaskResult |
| Worker 耗尽工具预算 | worker.py:491-499 | FAILURE TaskResult |
| Worker 解析 TaskResult 失败 | worker.py:573-578 | FAILURE TaskResult |
| Worker 自分解第二次 | worker.py:346-359 | FAILURE TaskResult |
| Worker 深度超过3 | worker.py:292-304 | FAILURE TaskResult |
| Worker 缺少分解请求 | worker.py:307-313 | FAILURE TaskResult |
| Reviewer API 异常 | reviewer.py:233-240 | ReviewResult(status="fail") |
| Reviewer 空响应 | reviewer.py:243-249 | ReviewResult(status="fail") |
| Reviewer 解析失败 | reviewer.py:290-295 | ReviewResult(status="fail") |
| Review 不通过+重试用尽 | gatekeeper.py:431-437 | FAILURE TaskResult |
| 工具执行异常 | worker.py:180-181 | 错误字符串，Worker 循环不死 |

---

## 附录：Session 历史的角色

**当前实现**：Session 存储历史但不使用。

```
session.py:31  __init__: _history = []           — 空列表
session.py:54  _history.append(user_msg)         — 记录用户消息
session.py:55  _history.append(assistant_msg)    — 记录助手消息
session.py:56  if len > max_history*2: trim      — 修剪到200条
```

**关键发现**：Gatekeeper 的所有 LLM 调用（_decide, _respond, _decompose）都是**无状态的**——每次只传入当前 message/problem 作为 user content，不附加历史。这对多轮对话是限制（Session 记了历史但没用），设计意图可能是在未来版本中注入。

Worker 的 context 也不包含对话历史——它只从 TaskSpec.context 中获取知识。

---

*追踪完成。所有行号对应 Janus Phase 4 源码 (main.py:208行, core/: 2570行 总计)。*
