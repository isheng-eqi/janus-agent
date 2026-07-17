# Janus 异步通信实现计划

> **状态：** 实现方案（基于 realtime-communication-design.md + Hermes delegate_task 模式）
> **日期：** 2026-07-16
> **前置阅读：** `docs/realtime-communication-design.md`（设计提案）

---

## 0. 两个系统的关键模式对照

在深入 Janus 实现之前，先理解 Hermes 的模式如何映射到 Janus：

| 概念 | Hermes | Janus 映射 |
|------|--------|-----------|
| 后台任务启动 | `delegate_task` 生成 daemon thread，立刻返回 `delegation_id` | `asyncio.create_task(worker_coro)`，不阻塞 |
| 中间状态查询 | `process(action='poll')` 拉取结果 | 用户在 REPL 输入 "进度？" → Gatekeeper 返回状态摘要 |
| 结果到达方式 | `completion_queue` 事件注入对话流 | `worker_queue`（asyncio.Queue）→ Gatekeeper 消费 → 流式输出到终端 |
| 对话不中断 | 顶层 agent 继续处理用户消息 | `asyncio.wait(FIRST_COMPLETED)` 同时等待用户输入和 Worker 完成 |
| 取消机制 | 无原生取消（thread 需协作） | `asyncio.Task.cancel()` 原生支持，下一次 `await` 即触发 |

**核心差异：** Hermes 用线程 + 轮询，Janus 用 asyncio + 事件驱动。但两者的**用户体感**一致："发指令 → 后台执行 → 边执行边对话 → 结果回来时自然插入对话流"。

---

## 1. 最小可行变更（Phase 2A）

**目标：** 用户可以在 Worker 运行时查询状态（"进度？""任务3在做什么？"），但不能修改或取消。

**不引入并行执行——Worker 仍然串行启动，只是不阻塞 REPL。**

### 1.1 变更清单

#### 必须改的文件（4 个）

| 文件 | 当前 | 改为 | 变更量 |
|------|------|------|--------|
| `core/worker.py` | `def run(self, spec) -> TaskResult`（同步） | 新增 `async def run_async(self, spec, event_queue) -> TaskResult` | ~20 行新增 |
| `core/gatekeeper.py` | `def execute(self, goal) -> str`（同步，阻塞） | 新增 `async def execute_async(self, goal) -> AsyncGenerator[GatekeeperMessage]` | ~80 行新增 |
| `core/session.py` | `def handle(self, input) -> str`（同步） | 新增 `async def consume(self, goal) -> AsyncGenerator[str]` | ~60 行新增 |
| `main.py` | `while True: input() → session.handle()`（同步） | `async def repl()` 事件循环 | ~30 行重写 |

#### 必须新增的文件（1 个）

| 文件 | 内容 | 行数 |
|------|------|------|
| `core/events.py` | `WorkerEvent`、`UserMessage`、`GatekeeperMessage` 三个 dataclass | ~60 行 |

#### 不改的文件

| 文件 | 原因 |
|------|------|
| `core/protocol.py` | `TaskSpec`、`TaskResult`、`TaskStatus` 等数据结构完全不变 |
| `core/task_manager.py` | 状态机逻辑不变；Phase 2A 单线程，无需加锁 |
| `core/console.py` | 纯输出格式化，不变；新增的方法加到 Console 类上 |
| `core/reviewer.py` | `review()` 是纯 LLM 调用，改为 async 即可，逻辑不变 |

### 1.2 为什么这个是最小集

不引入以下内容（留给 Phase 2B/2C）：
- ❌ 用户中断/修改/取消 Worker
- ❌ 并行 Worker 执行（仍然 `for spec in specs` 串行启动）
- ❌ LLM 辅助意图分类（仍用关键词）
- ❌ `asyncio.wait()` 双通道（Phase 2A 用户只能在 Worker 间**间隙**说话，不是真正的并发对话）
- ❌ TaskManager 线程安全化

Phase 2A 的本质：**把 `execute()` 从"一堵墙"变成"一扇窗"——用户可以 peek 进去看进度，但不能伸手干预。**

---

## 2. 新模块设计

### 2.1 `core/events.py` — 事件数据结构

这是唯一需要新建的文件。三个 dataclass：

```
WorkerEvent     — Worker → worker_queue → Gatekeeper
  类型: task_started | tool_called | review_requested | review_done |
        task_completed | task_failed | progress
  字段: type, task_id, worker_id, payload(dict), timestamp

UserMessage     — REPL → user_queue → Gatekeeper
  类型: new_task | query_status | modify_task | cancel_task |
        pause_all | resume_all | add_task | chat
  字段: text, intent, target_task_id(Optional), timestamp

GatekeeperMessage — Gatekeeper → AsyncGenerator → Session → 终端
  类型: decomposed | task_started | task_progress | task_done |
        task_failed | all_done | chat_reply
  字段: type, content(str), task_id(Optional), detail(dict, Optional)
```

**设计决策：**

- `WorkerEvent` 和 `UserMessage` 分开——前者是 Worker→Gatekeeper 的内部事件，后者是 User→Gatekeeper 的外部输入。不混在一个队列里。
- `GatekeeperMessage` 是 Gatekeeper 对用户的**架构层**输出，遵循 user-gatekeeper-protocol.md——不泄露工具输出、代码行号、原始错误。
- `WorkerEvent` 中的 `tool_called` 携带的是工具名和参数摘要（如 `{"tool":"write_file", "file":"app.py"}`），不是原始工具输出。

### 2.2 为什么不需要更多新文件

| 不需要的文件 | 理由 |
|-------------|------|
| `core/async_gatekeeper.py` | `execute_async()` 是 `Gatekeeper` 的一个新方法，不是新类。Gatekeeper Tree 架构说 Gatekeeper 是唯一用户界面——拆成两个类反而违背这个原则 |
| `core/event_bus.py` | `asyncio.Queue` 足够。事件总线对这个规模是过度设计 |
| `core/orchestrator.py` | Phase 2A 的编排逻辑仍在 Session 中。Phase 3（并行执行）才需要独立的编排器 |

---

## 3. REPL 循环的变更

### 3.1 当前（同步）

```python
# main.py — 当前
while True:
    user_input = input("> ")       # 阻塞，用户等待
    answer = session.handle(user_input)  # 阻塞，可能 10-300s
    print(answer)
```

用户输入 → 冻结 → 结果 → 用户输入。没有中间状态。

### 3.2 Phase 2A（异步，但仍是半双工）

```python
# main.py — Phase 2A
async def repl():
    while True:
        user_input = await asyncio.to_thread(input, "> ")
        if user_input in ("quit", "exit", "q"):
            break

        # 启动异步执行（不阻塞）
        async for msg in session.consume(user_input):
            # msg 可能是：
            #   GatekeeperMessage(type="decomposed", ...)    → 打印任务列表
            #   GatekeeperMessage(type="task_started", ...)  → 打印 "Worker-0 开始..."
            #   GatekeeperMessage(type="task_done", ...)     → 打印 "Worker-0 完成"
            #   GatekeeperMessage(type="all_done", ...)      → 打印最终汇总
            print_message(msg)

        # ⚠ Phase 2A 限制：在 async for 循环内，用户不能输入。
        # 用户只能在 execute() 全部完成后才能发下一条消息。
        # 但至少看到了中间状态（task_started/task_done）。
```

**Phase 2A 的关键限制：** `async for` 循环期间用户不能输入。这是"半双工"——Gatekeeper 说话时用户不能打断。但好处是：
- 用户**看到**了中间状态（每个 Worker 开始/完成时实时打印）
- 不再是"黑洞 5 分钟后吐结果"
- 实现极其简单——`async for` 是 Python 原生语法

### 3.3 Phase 2B（真正的全双工）

```python
# main.py — Phase 2B
async def repl():
    session = AsyncSession(gk)
    asyncio.create_task(session.run_execution_loop())  # 后台执行循环

    while True:
        user_input = await asyncio.to_thread(input, "> ")
        if user_input in ("quit", "exit", "q"):
            break
        await session.send_message(user_input)  # 注入到执行循环
```

在 Phase 2B，`session.run_execution_loop()` 是一个后台协程，内部用 `asyncio.wait(FIRST_COMPLETED)` 同时等待 Worker 完成和用户消息。用户消息通过 `user_queue` 注入。

**这才是 Hermes 的 `delegate_task` + `completion_queue` 等价物：** 任务在后台跑，结果自动注入对话流，用户随时可以说话。

### 3.4 分阶段理由

Phase 2A（半双工）是必须的过渡步骤，因为：
1. **验证 asyncio 管道正确。** 在引入并发对话之前，先确保单个 async generator 的消费是正常的。
2. **Console 输出格式化。** 确保 Worker 事件→GatekeeperMessage→终端打印的链条每一环都正确。
3. **降低风险。** 全双工涉及 `asyncio.wait()` 的复杂协调逻辑。先做简单版本，确认 API 兼容性。

---

## 4. Worker 结果如何在中途呈现

### 4.1 当前：返回值模式

```
Gatekeeper.execute()
  → for spec in specs:
      result = worker.run(spec)      # 阻塞，返回值
      results.append(result)
  → return summary_string           # 所有结果一次性返回
```

用户看到的是：等 5 分钟 → 一个字符串。

### 4.2 Phase 2A：Async Generator 模式

```
Gatekeeper.execute_async()
  → yield GatekeeperMessage(type="decomposed", tasks=[...])  ← 用户立刻看到
  → for spec in specs:
      task = asyncio.create_task(worker.run_async(spec, event_queue))
      yield GatekeeperMessage(type="task_started", task_id="task-1")  ← 用户立刻看到
      result = await task
      yield GatekeeperMessage(type="task_done", task_id="task-1")    ← Worker 完成后立刻看到
  → yield GatekeeperMessage(type="all_done", ...)                   ← 最终汇总
```

用户看到的是流式输出：

```
> 写一个完整的 Web 应用

🔍 Gatekeeper 分析完成，拆分为 3 个子任务：
  ✓ task-1 · 项目结构搭建
  ✓ task-2 · 数据库 schema 设计
  ✓ task-3 · API 路由实现

┌─ task-1 · 项目结构搭建 ────────────────────────┐
│  ⚡ 写入文件: app/__init__.py
│  ⚡ 写入文件: app/main.py
│  ⚡ 执行命令: pip install fastapi
│  ⏱ 耗时 8.3s
└──────────────────────────────────────────────────┘
  ✅ task-1 · 通过

┌─ task-2 · 数据库 schema 设计 ───────────────────┐
│  ⚡ 写入文件: app/models.py
...
```

**关键：** `yield` 发生在每个 Worker 完成时（Phase 2A 串行）或每个事件到达时（Phase 2B 用 `asyncio.wait`）。用户不需要等所有 Worker 完成。

### 4.3 Phase 2B：真正的"中途呈现"

在 Phase 2B，用户可以在 Worker 执行过程中输入：

```
> 写一个完整的 Web 应用
🔍 Gatekeeper 分析完成，拆分为 3 个子任务...
┌─ task-1 · 项目结构搭建 ────────────────────────┐
│  ⚡ 写入文件: app/__init__.py

> 进度？                                  ← 用户在 Worker 执行中输入
  当前进度: task-1 执行中（已调用 3 个工具），
           task-2 排队中，task-3 排队中

│  ⚡ 执行命令: pip install fastapi       ← Worker 事件继续打印
│  ⏱ 耗时 8.3s
└──────────────────────────────────────────────────┘
  ✅ task-1 · 通过
```

**实现机制：**

1. `Session` 维护一个 `asyncio.Queue`（`user_queue`）
2. `Session.run_execution_loop()` 中 `asyncio.wait([worker_tasks, user_queue.get()], return_when=FIRST_COMPLETED)`
3. 用户消息到达 → Gatekeeper 处理 → 返回回复 → 打印 → 继续等待
4. Worker 完成 → Gatekeeper 收集结果 → 打印 → 继续等待

**与 Hermes 的精确对应：**

| Hermes | Janus Phase 2B |
|--------|---------------|
| `delegate_task` → daemon thread → `delegation_id` | `asyncio.create_task(worker_coro)` → `task_id` |
| 用户在顶层继续聊天 | `user_queue` 注入到 `asyncio.wait()` |
| `completion_queue` 事件注入对话 | `worker_queue` 事件 → Gatekeeper → 终端输出 |
| `process(action='poll')` | 用户输入 "进度？" → `query_status` → Gatekeeper 返回摘要 |

---

## 5. 什么不变

### 5.1 核心数据协议（完全不变）

`core/protocol.py` 中的所有类型**零变更**：

- `TaskSpec` — Worker 接收的输入，不变
- `TaskResult` — Worker 返回的输出，不变
- `TaskStatus` (SUCCESS/FAILURE/NEEDS_DECOMPOSITION) — 不变
- `Confidence` (HIGH/MEDIUM/LOW) — 不变
- `SubTask`、`DecompositionRequest` — 不变

### 5.2 TaskManager 生命周期（完全不变）

`core/task_manager.py` 的状态机不变：

- PENDING → RUNNING → COMPLETED/FAILED 转换逻辑不变
- `add_task()`、`mark_running()`、`mark_completed()`、`mark_failed()` API 不变
- `get_summary()` 查询不变
- Phase 2A 仍在单线程事件循环中运行，无需加锁

唯一的潜在变更（Phase 3）：当引入真正并行时，需要在 `_transition()` 加 `asyncio.Lock`。但 Phase 2A 和 2B 不需要。

### 5.3 Worker 核心执行循环（逻辑不变，只加 async）

`core/worker.py` 的 `_execute_loop()` 逻辑完全不变：

- 构建 system prompt → 不变
- 调用 LLM → 改用 `await client.chat.completions.create()`
- 解析 tool_calls → 不变
- 执行工具 → 工具本身可能是同步的，用 `asyncio.to_thread()` 包装
- 解析 TaskResult → 不变
- 自分解逻辑 (`run()` 中的 NEEDS_DECOMPOSITION 处理) → 不变

**变化仅是：** `def _execute_loop` → `async def _execute_loop_async`，`def run` → `async def run_async`。内部加 `await` 和 `async for`。保留原同步方法作为向后兼容。

### 5.4 Console 输出格式化（完全不变）

`core/console.py` 的所有格式化逻辑不变：
- `phase_decompose()`、`task_start()`、`task_done()`、`tool_call()` 等方法不变
- 新增一个 `status_update()` 方法用于显示 "进度？" 查询的回复，但格式逻辑复用现有风格

### 5.5 Gatekeeper Tree 架构原则（完全不变）

- ✅ Gatekeeper 仍然是唯一用户界面
- ✅ Gatekeeper 仍然零工具
- ✅ Worker 不直接与用户通信
- ✅ 用户看到的是架构层信息，不泄露工具级细节
- ✅ "一个一个来"策略仍然有效（Phase 2A/2B 默认串行分发）

### 5.6 同步 `execute()` 保留

`Gatekeeper.execute(goal) -> str` **保留不变**。新增的 `execute_async()` 是独立方法。这确保了：
- 现有调用方（测试、脚本）不受影响
- 可以作为降级路径：如果 async 管道出问题，fallback 到同步版本

---

## 6. 实现顺序

### Phase 2A：最小可行异步化（目标：worker 不阻塞 REPL，可看进度）

**优先级：🔴 最高。这是所有后续 Phase 的基础。**

#### 步骤 1：新增 `core/events.py`（0.5 天）
- 定义 `WorkerEvent`、`UserMessage`、`GatekeeperMessage` 三个 dataclass
- 无外部依赖，纯数据结构

#### 步骤 2：Worker 异步化 —— `core/worker.py`（1 天）
- 新增 `async def run_async(self, spec, event_queue=None) -> TaskResult`
- 内部新增 `async def _execute_loop_async(self, spec, event_queue) -> TaskResult`
- LLM 调用改用 `AsyncOpenAI`（与同步 `OpenAI` 并存，同文件，同 api_key）
- 每个工具调用后 `await event_queue.put(WorkerEvent(type="tool_called", ...))`
- 同步工具（`terminal`、`web_search` 等）用 `asyncio.to_thread()` 包装
- **保留** `def run()` 和 `def _execute_loop()` 不变
- 验证：`await worker.run_async(spec)` 返回与 `worker.run(spec)` 相同的 `TaskResult`

#### 步骤 3：Gatekeeper 异步化 —— `core/gatekeeper.py`（1 天）
- 新增 `async def execute_async(self, goal) -> AsyncGenerator[GatekeeperMessage, None]`
- 内部调用 `await self._planner.execute_async(directive)`（Planner 的 async 版本）
- Worker 分发改为 `asyncio.create_task(self._run_worker_async(spec, event_queue))`
- 串行 `await task` 每个 Worker（Phase 2A 不并行）
- `yield GatekeeperMessage(...)` 在每个 Worker 开始/完成时
- **保留** `def execute(goal) -> str` 不变
- 验证：`async for msg in gk.execute_async(goal): print(msg)` 输出与 `gk.execute(goal)` 内容等价

#### 步骤 4：Session 异步化 —— `core/session.py`（1 天）
- 新增 `async def consume(self, goal) -> AsyncGenerator[str, None]`
- 内部调用 `gk.execute_async(goal)`，将 `GatekeeperMessage` 格式化为终端输出字符串
- 意图分类复用现有 `_classify()`（关键词，不变）
- **保留** `def handle(input) -> str` 不变
- 验证：`async for line in session.consume("写一个阶乘函数"): print(line)`

#### 步骤 5：main.py REPL 异步化（0.5 天）
- 新增 `async def repl()` 函数
- 用户输入用 `asyncio.to_thread(input, "> ")`
- 消费 Session 的 async generator
- `if __name__ == "__main__": asyncio.run(repl())`
- 验证：端到端运行，确认中间状态实时打印

#### 步骤 6：Console 微调（0.5 天）
- 新增 `Console.status_update(summary: str)` 方法（为 Phase 2B 准备）
- Phase 2A 中暂不使用，但提前定义接口

**Phase 2A 总计：~4 天**

#### Phase 2A 的交付物是什么

- 用户输入目标 → 立刻看到分解结果 → 每个 Worker 开始/完成时实时打印 → 最终汇总
- 用户**不能**在 Worker 执行期间输入新消息
- 同步 API 完全保留，向后兼容
- 所有现有测试通过

---

### Phase 2B：交互式中断（目标：Worker 执行期间用户可以对话）

**优先级：🟡 高。这是实时通信的核心价值。**

#### 步骤 1：全双工 Session（1 天）
- `Session` 新增 `run_execution_loop()` 后台协程
- 内部维护 `user_queue: asyncio.Queue` 和 `worker_queue: asyncio.Queue`
- 使用 `asyncio.wait([worker_tasks, user_queue.get()], return_when=FIRST_COMPLETED)`
- Gatekeeper 的 `execute_async()` 改为接受 `user_queue` 参数，在循环中同时等待

#### 步骤 2：意图分类增强（0.5 天）
- 扩展 `_classify()` 增加 `query_status`、`chat` 意图
- 关键词匹配：`"进度"、"状态"、"在做什么"、"还要多久"` → `query_status`
- 默认 → `chat`（纯对话，不影响执行）

#### 步骤 3：状态查询实现（0.5 天）
- Gatekeeper 新增 `_build_status_message()` 方法
- 返回：当前运行中的任务、已完成任务数、总任务数、每个任务的状态
- 格式化为架构层语言（"任务 2/5：Worker-1 正在写入文件，已调用 4 个工具"）

#### 步骤 4：终端输出协调（0.5 天）
- Console 管理输出缓冲区
- Worker 事件和用户消息交错打印时，确保不破坏任务框的视觉结构
- 用户消息始终以 `> [用户]` 前缀打印，与 Worker 事件区分

**Phase 2B 总计：~2.5 天**

#### Phase 2B 的交付物

- 用户在 Worker 执行期间可以输入消息
- 支持 `query_status`（查询进度）和 `chat`（闲聊）两种意图
- Worker 结果和用户消息在终端交错但可读
- **不支持**修改/取消/追加任务

---

### Phase 2C：修改与取消（目标：执行期间可干预）

**优先级：🟢 中。需要 Phase 2B 的全双工管道。**

#### 步骤 1：modify_task 实现（1 天）
- Gatekeeper 新增 `_handle_modify(task_id, new_instruction)`
- 调用 `worker_task.cancel()` → Worker 在下一个 `await` 点抛 `CancelledError`
- Worker 捕获后清理临时文件，推送 `task_cancelled` 事件
- Gatekeeper 用 LLM 重新分解修改指令 → 更新 TaskSpec → 创建新 Worker
- 版本机制：TaskSpec 增加 `version: int = 1`，Worker 完成时检查版本是否匹配

#### 步骤 2：cancel_task 实现（0.5 天）
- 调用 `worker_task.cancel()`，标记为 "用户取消"
- 从等待集合中移除

#### 步骤 3：add_task 实现（0.5 天）
- 用户追加新任务 → 分解 → 创建新 Worker → 加入等待集合
- 与现有 Worker 并行（Phase 2C 允许追加的任务与已有任务并发）

#### 步骤 4：边界情况处理（0.5 天）
- 用户修改任务时 Worker 恰好完成 → 用版本号判断，丢弃过期结果
- 用户取消所有任务 → Gatekeeper 返回 "所有任务已取消"
- 用户追加任务后立刻查询状态 → 新任务应出现在状态摘要中

**Phase 2C 总计：~2.5 天**

---

### Phase 3：并行执行（未来）

**优先级：🔵 低。Phase 2A-2C 已经是完整的实时对话系统。**

- `asyncio.gather()` 或信号量控制并发 Worker 数量
- TaskManager 加 `asyncio.Lock`（仅 `_transition` 方法）
- Console 支持多 Worker 并行输出（每个 Worker 独立输出区域）
- 可配置并发上限（`config.yaml` 中 `max_concurrent_workers`）

---

## 7. 关键架构决策汇总

| 决策 | 选择 | 理由 |
|------|------|------|
| 并发模型 | asyncio（不是 threading） | Python 原生 async/await，LLM API 调用是 IO 密集，单线程避免锁竞争 |
| Gatekeeper 返回类型 | `AsyncGenerator[GatekeeperMessage]` | 流式输出——用户不必等所有 Worker 完成才看到第一个结果 |
| Worker 结果传递 | `asyncio.Task` 完成（不是额外队列） | Python 原生机制，无需额外基础设施 |
| Worker 中间事件 | 独立的 `worker_queue`（asyncio.Queue） | 实时 Console 显示需要中间事件（工具调用、审核进度），不只是最终结果 |
| 用户输入 | `asyncio.to_thread(input)` | 零外部依赖；Phase 2 够用 |
| 新增文件 | 仅 `core/events.py` | 最小化变更面。所有其他逻辑在现有文件中以新方法形式添加 |
| 同步 API 保留 | `execute()`、`run()`、`handle()` 全部保留 | 向后兼容；测试和脚本不受影响；async 作为增强而非替换 |
| 意图分类 | 关键词（渐进到 LLM） | Phase 2 关键词够用；LLM 分类增加延迟和 token 消耗 |
| Worker 取消 | `asyncio.Task.cancel()` | 原生支持，下一次 `await` 自动触发 `CancelledError` |
| TaskManager 线程安全 | Phase 2 不加锁 | asyncio 单线程事件循环天然避免竞态；Phase 3 再加 `asyncio.Lock` |

---

## 8. 风险与注意事项

### 8.1 DeepSeek AsyncOpenAI 兼容性

- 已验证：`openai` 库 v1.0+ 提供 `AsyncOpenAI`，API 与同步 `OpenAI` 一致
- 相同 base_url (`https://api.deepseek.com`)，相同 api_key
- 相同 `extra_body={"thinking": {"type": "enabled"}}`
- 同步和异步 client 可以在同一个 Gatekeeper/Worker 中共存

### 8.2 Console 输出混乱

- Phase 2B 引入全双工时，Worker 事件和用户消息可能交错打印
- 缓解：Console 维护输出缓冲区，用户消息始终以 `> [用户]` 前缀打印
- Worker 事件在任务框内打印（`│  ⚡ ...`），用户消息在框外打印
- 如果用户在任务框输出期间输入，用户消息插入后任务框继续

### 8.3 内存泄漏

- `worker_queue` 只存中间事件，Worker 完成后清理
- 设置 `worker_queue` 的 `maxsize`（如 100），防止事件堆积
- 历史 Worker 结果由 `TaskManager` 持有，`reset()` 时清理

### 8.4 向后兼容

- 同步 `execute()` 内部可以调用 `asyncio.run(self.execute_async().__anext__())` 作为降级路径
- 或者同步 `execute()` 完全保持不变，async 和 sync 是两个独立代码路径
- **推荐后者**——两条路径独立，互不干扰

---

## 9. 与现有 Janus 协议的关系

### 不冲突

`user-gatekeeper-protocol.md` 所有原则完整保留：

- ✅ Gatekeeper 是唯一用户界面（异步 Gatekeeper 依然是唯一界面）
- ✅ 用户看不到 Worker 细节（`GatekeeperMessage` 过滤了工具级信息）
- ✅ Gatekeeper 零工具（异步版本也不增加工具）
- ✅ 架构层信息传递（`GatekeeperMessage` 的 `content` 字段是架构层语言）
- ✅ "一个一个来"策略（Phase 2A/2B 默认串行）

### 增强

新架构在**时间维度**增强了协议：原本压缩在一起的"分解→执行→汇总"事件在时间轴上展开，用户可以**看到**和**参与**这个过程。

原协议假设：用户发指令 → 等结果 → 看结果 → 发下一条
新协议支持：用户发指令 → 边执行边对话 → 持续调整 → 收结果

---

## 10. 总结

| Phase | 用户能做什么 | 不能做什么 | 变更量 | 时间 |
|-------|------------|-----------|--------|------|
| **2A** | 看实时进度 | 在 Worker 运行时输入 | 4 文件改 + 1 文件新 | ~4 天 |
| **2B** | 查询状态、闲聊 | 修改/取消任务 | 增量 3 文件改 | ~2.5 天 |
| **2C** | 修改、取消、追加任务 | — | 增量 2 文件改 | ~2.5 天 |
| **3** | 并行执行 | — | 增量 3 文件改 | 未来 |

**核心思想：渐进增强，不重写。** 从 Phase 2A 的半双工 async generator 开始，逐步升级到 Phase 2B 的全双工 `asyncio.wait`，再到 Phase 2C 的修改/取消能力。每一步都建立在前面已经验证的基础上，每一步都不破坏现有的同步 API 和 Gatekeeper Tree 架构原则。

Hermes 的 `delegate_task` + `completion_queue` 模式给了我们清晰的目标体验；asyncio 的 `Task` + `Queue` + `wait(FIRST_COMPLETED)` 给了我们最自然的 Python 实现路径。
