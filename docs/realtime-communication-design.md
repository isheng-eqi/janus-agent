# Janus 实时用户↔Gatekeeper 通信架构设计

> **状态：** 设计提案（待评审）
> **日期：** 2026-07-16
> **范围：** Worker 后台执行期间，用户如何与 Gatekeeper 保持实时对话

---

## 1. 问题定义

### 1.1 当前阻塞模式

```
User: input("> ")                     # 用户输入目标
  → Session.handle(input)             # 意图分类
    → Gatekeeper.execute(goal)         # ⚠ 阻塞调用
      → _decompose(goal)              # LLM 分解 (~2-5s)
      → for spec in specs:            # ⚠ 串行阻塞循环
          → Worker-0.run(spec)        # LLM + 工具调用 (~10-300s)
          → Reviewer.review()         # LLM 审核 (~3-10s)
          → 可能重试 (×3)
      → return summary                # 返回结果字符串
    → print(result)                   # 打印到终端
User: input("> ")                     # ⚠ 用户一直被冻结，直到所有 Worker 完成
```

### 1.2 核心矛盾

| 维度 | 当前实现 | 用户期望 |
|------|---------|---------|
| 交互模式 | 一问一答，全程阻塞 | 对话与执行可并行 |
| 执行可见性 | 仅最终汇总 | 实时进度 + 中间状态可查询 |
| 干预能力 | 无——只能等全部完成 | 可中断、修改、重定向运行中任务 |
| 任务粒度 | 一个 `execute()` 是一个原子操作 | 多个独立 Worker 应独立可操作 |

### 1.3 目标体验

```
User: "写一个完整的Web应用"
Gatekeeper: "分解为 5 个任务，开始执行..."
  ├─ Worker-0: 项目结构搭建         ⚡ 运行中...
  ├─ Worker-1: 数据库 schema 设计   ⚡ 运行中...
  └─ Worker-2: API 路由实现         ⏳ 排队中...

User: "第三个任务具体要做什么？"           ← 可以在 Worker 运行时对话
Gatekeeper: "任务 3 是设计数据库 schema，包括 User、Post、Comment 三张表..."

User: "数据库用 PostgreSQL，加一个 Tag 表"
Gatekeeper: "已更新任务 3 的要求，正在重新分派..."  ← 实时干预
  ├─ Worker-1 收到更新后的 TaskSpec，继续执行

... (5 分钟后)
Gatekeeper: "全部 5 个任务完成，汇总如下..."
```

---

## 2. 设计约束

### 2.1 不可妥协的原则

这些是 Janus 架构中的硬约束，任何方案不得违反：

| 约束 | 来源 | 含义 |
|------|------|------|
| **Gatekeeper 是唯一用户界面** | `user-gatekeeper-protocol.md` | 用户永远不直接看到 Worker、Reviewer、工具输出 |
| **Gatekeeper 零工具** | `gatekeeper.py` 设计 | Gatekeeper 不能读文件、不能执行命令、不能搜索 |
| **Worker 不直接与用户通信** | 协议第 4 节 | Worker 的输出经过 Gatekeeper 过滤后才到达用户 |
| **用户看到的是架构层信息** | 协议第 3 节 | 永远不泄露代码行号、工具级结果、原始错误 |

### 2.2 可放宽的约束

| 当前约束 | 来源 | 是否可放宽 |
|---------|------|:--------:|
| TaskManager 单线程 | `task_manager.py` 注释 | ✅ 可改为线程安全 |
| Gatekeeper.execute() 同步 | 当前实现 | ✅ 可改为 async |
| Session.handle() 同步 | 当前实现 | ✅ 可改为 async |
| Worker 在主线程运行 | 当前实现 | ✅ 可移到后台线程/协程 |
| 任务串行执行 (`for spec in specs`) | `gatekeeper.py:190` | ✅ 可并行化 |

---

## 3. 四种候选架构

### 3.1 方案 A：后台线程 + 轮询

```
Gatekeeper.dispatch(spec) → 创建 Thread(target=worker.run, args=(spec,))
Gatekeeper 返回控制权给 Session
Session 轮询: while not all_done: check queue; 处理用户输入
```

| 优点 | 缺点 |
|------|------|
| 实现简单，`threading.Thread` 零依赖 | 轮询低效（要么高频 CPU 空转，要么延迟感知结果） |
| Gatekeeper/Worker 核心逻辑改动小 | GIL 限制真正并行，但 LLM API 调用是 IO 密集，影响不大 |
| Session 可以是同步的（用 `input` + 超时） | TaskManager 需要加锁；状态一致性难保证 |
| | 线程的取消/中断不优雅（没有原生取消机制） |

**Janus 适配度：** ⭐⭐ （能工作，但不优雅）

### 3.2 方案 B：事件驱动

```
EventBus: 全局事件总线
  Worker 完成 → emit("task.completed", result)
  Worker 工具调用 → emit("worker.tool_call", name, args)
  Reviewer 审核 → emit("review.done", verdict)

Gatekeeper: 订阅事件
  on("task.completed"): 收集结果，判断是否全部完成
  on("user.message"): 处理用户输入，可能触发 cancel/modify 事件

Session: 同时监听 stdin 和 EventBus
```

| 优点 | 缺点 |
|------|------|
| 解耦彻底——Worker 不知道 Gatekeeper 存在 | 复杂度高——引入事件总线基础设施 |
| 扩展性好——未来可加监控、日志、告警 | 事件顺序难以保证（任务 2 完成可能在任务 1 之前） |
| 天然支持多消费者 | 调试困难——事件流不可见 |

**Janus 适配度：** ⭐⭐⭐ （架构优美，但对 Phase 2 过度设计）

### 3.3 方案 C：双通道

```
通道 A（任务执行）: Gatekeeper → Worker 分发 → Worker 结果 → Gatekeeper 收集
通道 B（用户对话）: User → Gatekeeper 消息 → Gatekeeper 回复 → User

Gatekeeper 在两个通道间复用：
  - 通道 A 有新结果 → 更新进度，可能发送状态更新给用户
  - 通道 B 有新消息 → 判断是查询/修改/取消，执行对应操作

Session 持有两个通道的引用，用 select/epoll 模式等待任一通道就绪
```

| 优点 | 缺点 |
|------|------|
| 概念上与 Janus 协议完美契合——Gatekeeper 是两个世界的桥梁 | 需要 Gatekeeper 具备"上下文切换"能力 |
| 通道 A 可完全异步，不阻塞通道 B | 两通道间的协调逻辑（如：用户改了任务 3 但 Worker-3 刚完成，用哪个结果？） |
| 自然映射到现有架构 | 内存管理：通道 A 的结果要保留多久、何时清理 |

**Janus 适配度：** ⭐⭐⭐⭐ （概念最优，实现需要仔细的状态机设计）

### 3.4 方案 D：Async REPL

```
Session: async event loop
  ├─ asyncio.Task: user_input_listener()  → Queue[UserMessage]
  ├─ asyncio.Task: worker_supervisor()    → Queue[WorkerEvent]
  └─ Gatekeeper.run_async():
        while not done:
            done, pending = await asyncio.wait(
                [user_queue.get(), worker_queue.get()],
                return_when=FIRST_COMPLETED
            )
            if user_message:
                handle_interrupt(message)
            if worker_event:
                process_result(event)
```

| 优点 | 缺点 |
|------|------|
| Python 原生并发，无需外部依赖 | 需要将核心链路改为 async（Gatekeeper、Worker、Session） |
| `asyncio.Task.cancel()` 原生支持优雅取消 | DeepSeek SDK 的 `openai` 库需确认 async 支持（有 `AsyncOpenAI`） |
| 单线程事件循环 → 天然避免锁竞争 | 现有代码量中等，改写成本可观 |
| `asyncio.wait` 的 FIRST_COMPLETED 模式天然适合"有活就干" | 用户输入 `input()` 是同步的，需要 `asyncio.to_thread` 或 `aioconsole` |

**Janus 适配度：** ⭐⭐⭐⭐⭐ （Python 生态最佳实践，与 Gatekeeper Tree 模式天然契合）

---

## 4. 推荐方案：Async 双通道（方案 C + D 融合）

### 4.1 为什么是这个组合

方案 C（双通道）提供了正确的**概念模型**——Gatekeeper 作为用户和 Worker 之间的唯一中介。方案 D（Async REPL）提供了正确的**实现路径**——Python 的 asyncio 是处理"同时等待多件事"的最自然方式。

两者融合：**用 asyncio 实现双通道架构。**

### 4.2 核心架构图

```
┌──────────────────────────────────────────────────────────────────┐
│                        AsyncSession                               │
│                                                                   │
│  通道 A: 任务执行                          通道 B: 用户对话       │
│  ┌─────────────────────┐                ┌──────────────────┐    │
│  │ worker_queue        │                │ user_queue       │    │
│  │ (asyncio.Queue)     │                │ (asyncio.Queue)  │    │
│  │                     │                │                  │    │
│  │ WorkerEvent:        │                │ UserMessage:     │    │
│  │  - task_started     │                │  - text          │    │
│  │  - tool_called      │                │  - intent        │    │
│  │  - review_done      │                │  - timestamp     │    │
│  │  - task_completed   │                │                  │    │
│  │  - task_failed      │                │                  │    │
│  └─────────┬───────────┘                └────────┬─────────┘    │
│            │                                      │               │
│            │         ┌──────────────┐            │               │
│            └────────→│  Gatekeeper  │←───────────┘               │
│                      │  (async)     │                            │
│                      │              │                            │
│                      │ await any:   │                            │
│                      │  worker_queue│                            │
│                      │  user_queue  │                            │
│                      │  timeout     │                            │
│                      └──────┬───────┘                            │
│                             │                                     │
│                             ▼                                     │
│                      ┌──────────────┐                            │
│                      │  Console     │                            │
│                      │  (streaming) │                            │
│                      └──────────────┘                            │
└──────────────────────────────────────────────────────────────────┘

后台协程池:
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Worker-0 │  │ Worker-1 │  │ Worker-2 │   ← asyncio.Task
│ (协程)    │  │ (协程)    │  │ (协程)    │      可独立取消
└──────────┘  └──────────┘  └──────────┘
```

### 4.3 关键组件设计

#### 4.3.1 WorkerEvent（通道 A 的消息类型）

```python
@dataclass
class WorkerEvent:
    """Worker 发给 Gatekeeper 的事件。"""
    type: Literal[
        "task_started",      # Worker 开始执行
        "tool_called",       # Worker 调用了工具（摘要，非原始输出）
        "review_requested",  # 提交审核
        "review_done",       # 审核完成
        "task_completed",    # 任务完成
        "task_failed",       # 任务失败
        "progress",          # 通用进度更新
    ]
    task_id: str
    worker_id: str
    payload: dict            # 事件相关的数据
    timestamp: float
```

**关键设计决策：** Worker 不直接写 Console，不直接发消息给用户。Worker 只往 `worker_queue` 推事件。Gatekeeper 消费事件后决定（a）是否告诉用户（b）以什么粒度告诉用户。

#### 4.3.2 UserMessage（通道 B 的消息类型）

```python
@dataclass
class UserMessage:
    """用户发给 Gatekeeper 的消息。"""
    text: str
    intent: Literal[
        "new_task",          # 新目标
        "query_status",      # 查询进度："任务3在做什么？"
        "modify_task",       # 修改运行中任务："数据库用 PostgreSQL"
        "cancel_task",       # 取消任务："停下任务2"
        "pause_all",         # 暂停全部
        "resume_all",        # 恢复全部
        "add_task",          # 追加新任务
        "chat",              # 普通对话（不涉及任务执行）
    ]
    target_task_id: Optional[str]  # 针对哪个任务（可选）
    timestamp: float
```

**关键设计决策：** Session 不再做简单的关键词意图分类。对于运行中 Worker 时的用户消息，需要更丰富的意图分类——"数据库用 PostgreSQL"可能分类为 `modify_task`，也可能需要 LLM 辅助分类。

#### 4.3.3 AsyncGatekeeper

```python
class AsyncGatekeeper:
    """Gatekeeper 的异步版本。

    核心变化：
    1. execute() → async generator，yield 状态更新
    2. 分发 Worker 用 asyncio.create_task()，不阻塞
    3. 同时等待用户消息和 Worker 结果
    """

    async def execute(
        self,
        goal: str,
        user_queue: asyncio.Queue,    # 通道 B
        worker_queue: asyncio.Queue,  # 通道 A
    ) -> AsyncGenerator[GatekeeperMessage, None]:
        """异步执行目标，yield 给用户的消息流。"""
        # 1. 分解
        specs = await self._decompose_async(goal)
        yield GatekeeperMessage(type="decomposed", tasks=specs)

        # 2. 并行分发（Phase 2 可改为并发）
        worker_tasks: dict[str, asyncio.Task] = {}
        for spec in specs:
            task = asyncio.create_task(
                self._run_worker_async(spec, worker_queue)
            )
            worker_tasks[spec.task_id] = task

        # 3. 事件循环：同时等待用户输入和 Worker 完成
        pending = set(worker_tasks.values())
        results: dict[str, TaskResult] = {}

        while pending:
            # 构建等待集合：Worker 完成 + 用户消息
            waitables = list(pending) + [user_queue.get()]

            done, pending_wait = await asyncio.wait(
                waitables,
                return_when=asyncio.FIRST_COMPLETED,
            )

            for item in done:
                if isinstance(item, UserMessage):
                    # 通道 B：用户消息
                    response = await self._handle_user_interrupt(
                        item, worker_tasks, results
                    )
                    yield response
                elif isinstance(item, asyncio.Task):
                    # 通道 A：Worker 完成
                    result = item.result()
                    results[result.task_id] = result
                    pending.discard(item)
                    yield GatekeeperMessage(
                        type="task_done",
                        task_id=result.task_id,
                        result=result,
                    )

        # 4. 汇总
        yield GatekeeperMessage(type="all_done", results=results)
```

#### 4.3.4 中断处理逻辑

```python
async def _handle_user_interrupt(
    self,
    msg: UserMessage,
    worker_tasks: dict[str, asyncio.Task],
    results: dict[str, TaskResult],
) -> GatekeeperMessage:
    """处理运行中的用户中断。"""

    if msg.intent == "query_status":
        # 查询：不改变任何状态，只返回当前进度
        return self._build_status_message(worker_tasks, results)

    elif msg.intent == "modify_task":
        # 修改：取消当前 Worker，用新 TaskSpec 重新分派
        task_id = msg.target_task_id
        if task_id and task_id in worker_tasks:
            old_task = worker_tasks[task_id]
            old_task.cancel()  # asyncio 原生取消
            # 构建更新后的 TaskSpec
            new_spec = await self._build_modified_spec(task_id, msg.text)
            new_task = asyncio.create_task(
                self._run_worker_async(new_spec, worker_queue)
            )
            worker_tasks[task_id] = new_task
            return GatekeeperMessage(
                type="task_modified",
                task_id=task_id,
                detail=f"已更新任务 {task_id} 并重新分派"
            )

    elif msg.intent == "cancel_task":
        task_id = msg.target_task_id
        if task_id and task_id in worker_tasks:
            worker_tasks[task_id].cancel()
            del worker_tasks[task_id]
            results[task_id] = TaskResult(
                status=TaskStatus.FAILURE,
                summary="用户取消",
            )
            return GatekeeperMessage(
                type="task_cancelled",
                task_id=task_id,
            )

    elif msg.intent == "add_task":
        # 追加新任务到运行中的批次
        new_spec = await self._decompose_single(msg.text)
        new_task = asyncio.create_task(
            self._run_worker_async(new_spec, worker_queue)
        )
        worker_tasks[new_spec.task_id] = new_task
        return GatekeeperMessage(
            type="task_added",
            task_id=new_spec.task_id,
        )

    elif msg.intent == "chat":
        # 纯对话——Gatekeeper 用 LLM 回复，不改变执行状态
        return await self._chat_response(msg.text)

    # ... 更多意图
```

### 4.4 关键问题的答案

#### Q1: Gatekeeper.execute() 应该是异步的吗？

**是的，必须。** 这是实现非阻塞行为的必要条件。同步的 `execute()` 本质上无法释放控制权。

变化：`def execute(self, goal: str) -> str` 变为 `async def execute(self, goal: str, ...) -> AsyncGenerator[GatekeeperMessage, None]`。

注意：改为 async generator 而非返回单个字符串。这意味着调用方（Session/CLI）需要 `async for msg in gk.execute(...)` 来逐步消费状态更新。

#### Q2: 用户如何中断或重定向运行中的任务？

通过 **asyncio.Task.cancel()** 机制。每个 Worker 被包装为一个 `asyncio.Task`，Gatekeeper 持有引用。用户发出 `modify_task` 或 `cancel_task` 意图时：

1. Gatekeeper 调用 `worker_task.cancel()`
2. Worker 的下一个 `await` 点（通常是 LLM API 调用）会抛出 `asyncio.CancelledError`
3. Worker 捕获 `CancelledError`，做清理（删除临时文件等），推送 `task_cancelled` 事件
4. 如果是 `modify`：Gatekeeper 构造新 TaskSpec，创建新 Worker Task
5. 如果是 `cancel`：Gatekeeper 从等待集合中移除该 task

**关键设计点：** Worker 的 `_execute_loop` 需要在每个 `await` 点后检查取消状态。好在这在 asyncio 中是自动的——任何 `await` 都可能触发 `CancelledError`。

#### Q3: Worker 结果如何在对话持续进行时返回？

Worker 完成后，其结果进入 `worker_queue`（或直接作为 `asyncio.Task` 的返回值被 `asyncio.wait` 捕获）。Gatekeeper 在处理下一个用户消息**之前**检查是否有 Worker 结果就绪。

具体机制：

```python
# in Gatekeeper's main loop
done, pending = await asyncio.wait(
    [*worker_tasks.values(), user_queue.get()],
    return_when=asyncio.FIRST_COMPLETED,
)
```

`return_when=FIRST_COMPLETED` 确保无论哪边先有数据，Gatekeeper 立即响应。Worker 完成和用户输入是平等的——谁先到就先处理谁。

如果 Gatekeeper 正在处理一个用户消息时 Worker 完成，Worker 的结果在下一个 `await` 点被捕获，不会丢失。

#### Q4: 需要一个任务队列 + 通知系统吗？

**是的，但不需要复杂的消息队列中间件。** Python 的 `asyncio.Queue` 就是天然的轻量级消息通道。

需要的队列：

| 队列 | 方向 | 内容 |
|------|------|------|
| `user_queue: asyncio.Queue[UserMessage]` | 用户 → Gatekeeper | 用户输入消息 |
| `worker_queue: asyncio.Queue[WorkerEvent]` | Worker → Gatekeeper | Worker 状态事件（用于 Console 显示） |
| （内部）`asyncio.Task` 返回值 | Worker → Gatekeeper | Worker 最终结果 |

`worker_queue` 主要用于**实时 Console 输出**——让用户看到 "Worker-0: 正在写入文件..." 这种中间状态。最终结果的收集通过 `asyncio.Task` 的完成通知实现，不需要经过队列。

#### Q5: Session 模型如何演进？

从同步 REPL 包装器演进为 **Async Orchestrator**（异步编排器）：

```
当前 Session               →          未来 AsyncSession
─────────────────────────────────────────────────────────
同步 handle(input)→str             异步 consume()→AsyncGenerator
关键词意图分类                      LLM 辅助意图分类 + 上下文感知
无状态（仅历史记录）                有状态（运行中任务、待处理结果）
execute() 期间不可交互              execute() 期间可交互
单一职责：路由                      多重职责：路由 + 编排 + 中断管理
```

**AsyncSession 的职责：**

1. **生命周期管理：** 启动/停止 AsyncGatekeeper 的执行循环
2. **输入采集：** 将同步 `input()` 包装为 async（`asyncio.to_thread(input, "> ")`）
3. **输出流式传输：** 消费 Gatekeeper 的 `AsyncGenerator`，逐步输出到终端
4. **意图预分类：** 快速判断用户输入是否需要传进执行循环（"进度如何？"→是 / "今天天气？"→否，普通对话）
5. **上下文保持：** 记录当前运行中的任务、已完成的任务、用户最近的修改意图

---

## 5. 实现路线图

### Phase 2A：最小可行异步化（1-2 周）

**目标：** 用户可以在 Worker 运行时查询状态，但不能修改。

```
改动范围：
  ✅ Gatekeeper.execute() 改为 async generator
  ✅ Worker.run() 改为 async
  ✅ 所有 LLM 调用改用 AsyncOpenAI
  ✅ 分发改为 asyncio.create_task（仍串行启动，不并行）
  ✅ Session 改为 async，消费 generator
  ✅ main.py REPL 改为 async
  ✅ Console 改为流式输出（已有基础）

暂不做：
  ❌ 用户中断/修改（Phase 2B）
  ❌ 并行 Worker 执行（Phase 3）
  ❌ LLM 辅助意图分类（仍用关键词）
```

### Phase 2B：交互式中断（1 周）

```
  ✅ 实现 UserMessage + 意图分类（query_status, chat）
  ✅ Gatekeeper 的 asyncio.wait() 双通道等待
  ✅ 状态查询：用户可问"进度？""任务3是什么？"
  ✅ 纯对话：用户可闲聊，不影响执行
```

### Phase 2C：修改与取消（1-2 周）

```
  ✅ modify_task：取消 Worker → 更新 TaskSpec → 重新分派
  ✅ cancel_task：取消 Worker，标记为"用户取消"
  ✅ add_task：运行中追加新任务
  ✅ pause_all / resume_all（可选）
```

### Phase 3：并行执行（未来）

```
  ✅ 真正并行分发：asyncio.gather() 或信号量控制并发数
  ✅ TaskManager 线程安全化
  ✅ 并行 Console 显示（每个 Worker 独立输出框，参考 cli-output-design.md 第 8 节）
```

---

## 6. 风险与缓解

### 6.1 API 兼容性风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| DeepSeek `openai` SDK 的 `AsyncOpenAI` 是否稳定？ | Worker LLM 调用全部阻塞 | 已验证：`openai` 库自 v1.0 起提供 `AsyncOpenAI`，API 与同步版本一致 |
| 部分工具（web_search, terminal）是同步的 | 阻塞事件循环 | 用 `asyncio.to_thread()` 包装同步工具调用 |

### 6.2 并发一致性风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| 多个 Worker 同时写 TaskManager | 状态不一致 | asyncio 单线程事件循环天然避免竞态；若引入线程则加 `asyncio.Lock` |
| 用户修改任务时 Worker 恰好完成 | 用旧结果还是新结果？ | "最后写入者胜出"——Worker 完成时先检查 `task_version`，若版本不匹配则丢弃 |
| 内存泄漏（事件队列无限增长） | Worker 事件堆积 | 设置 `worker_queue` 的 `maxsize`；定期清理已完成任务的引用 |

### 6.3 用户体验风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| 输出混乱（Worker 事件和用户消息交错打印） | 信息可读性差 | Console 维护一个输出缓冲区，按消息类型排序输出 |
| 用户困惑"现在到底在干什么" | 信任下降 | Gatekeeper 定期（每 30s 或每次工具调用后）推送状态摘要 |
| 中断延迟（用户说"停下"但 Worker 还在跑） | 用户挫败 | LLM API 调用通常 1-5s 返回——取消延迟最多一个 API 往返 |

---

## 7. 设计权衡总结

| 决策 | 选择 | 替代方案 | 理由 |
|------|------|---------|------|
| 并发模型 | asyncio | threading / multiprocessing | LLM API 调用是 IO 密集，asyncio 最自然；单线程避免锁 |
| 双通道 vs 单通道 | 双通道 | 事件总线 / 单一消息队列 | 概念上契合 Gatekeeper Tree 的"唯一中介"定位 |
| Worker 返回方式 | asyncio.Task 完成 | 结果队列 | Task 完成是 Python 原生机制，无需额外队列 |
| Console 事件 | worker_queue (独立) | 与 Task 完成合并 | 实时显示需要中间事件（工具调用、审核进度），不只是最终结果 |
| 用户输入 | asyncio.to_thread(input) | aioconsole / prompt_toolkit | 保持零外部依赖；Phase 2 够用 |
| 意图分类 | 关键词（渐进到 LLM） | 纯 LLM 分类 | Phase 2 关键词够用；LLM 分类增加延迟且消耗 token |
| 旧 API 兼容 | 保留同步 Gatekeeper.execute() | 全部改为 async | 同步方法内部调用 `asyncio.run()` 作为降级路径，向后兼容 |

---

## 8. 与现有 Janus 协议的契合

### 8.1 协议不变

`user-gatekeeper-protocol.md` 中定义的所有原则在新架构下**完整保留**：

- ✅ Gatekeeper 仍然是唯一的用户界面
- ✅ 用户仍然看不到 Worker 细节、工具输出、代码行号
- ✅ Gatekeeper 仍然零工具
- ✅ 架构层信息通过 GatekeeperMessage 传递
- ✅ "一个一个来"策略仍然有效（AsyncGatekeeper 默认仍串行分发，除非显式并行）

### 8.2 协议增强

新架构**增强**了协议的一个维度：**实时性**。

原来协议假设的是"用户发指令 → 等结果 → 看结果 → 发下一条"，新架构支持"用户发指令 → 边执行边对话 → 持续调整 → 收结果"。

这个增强不影响协议的核心原则，只是在时间维度上展开了原本压缩在一起的事件。

---

## 9. 未决问题

以下是需要在评审中讨论的开放问题：

1. **Worker 的"版本"机制：** 用户修改任务时需要知道 Worker 当前执行到哪一步了。是否需要 Worker 在执行过程中定期"checkpoint"以便恢复？还是简单粗暴地取消重来？

2. **Gatekeeper 的"关注力"：** 当 Worker 正在执行而用户说话时，Gatekeeper 应该立即回复用户，还是"等一下，让我先处理完这个 Worker 的结果"？建议：用户消息永远优先——Worker 结果排队等待。

3. **并行度控制：** 如果用户说"写一个完整的 Web 应用"，Gatekeeper 分解出 12 个任务，应该并行启动多少个？建议：可配置的并发上限（`max_concurrent_workers`，config.yaml 中已有注释占位），默认 3。

4. **Worker 事件粒度：** Worker 应该每调一个工具就发事件，还是每 N 个工具调用发一次，还是只在开始/结束时发？建议：每个工具调用发一个 `tool_called` 事件，但 Console 做去重/合并（同类工具连续调用时折叠显示）。

5. **历史 Worker 结果的生命周期：** 用户说"之前任务 2 的结果是什么？"——Gatekeeper 需要记住已完成 Worker 的结果。当前由 TaskManager 持有，但 TaskManager 在执行间会 `reset()`。Session 需要额外的"长期结果缓存"。

---

## 10. 参考资料

- `core/gatekeeper.py` — 当前同步 Gatekeeper 实现
- `core/worker.py` — 当前同步 Worker 实现
- `core/session.py` — 当前同步 Session 实现
- `core/task_manager.py` — 当前单线程 TaskManager
- `core/protocol.py` — TaskSpec, TaskResult, TaskStatus 等数据结构
- `core/console.py` — 当前 Console 观察者实现
- `core/reviewer.py` — Reviewer 审核代理
- `docs/user-gatekeeper-protocol.md` — Gatekeeper↔User 交互协议
- `docs/cli-output-design.md` — CLI 可观测性设计（含并行执行显示方案）
- `config.yaml` — 配置（含注释中的 `max_concurrent_workers` 占位）

---

*本文档由 Janus Architecture Design Task 生成，作为实时通信架构的设计提案。建议先评审 Phase 2A 方案，确认技术方向后再细化后续 Phase。*
