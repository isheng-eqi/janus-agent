# L3 Anti-Deception Architectural Feasibility Analysis

## 代码库架构速览

在分析之前，先确认当前代码结构的关键事实：

| 组件 | 文件 | 关键特征 |
|------|------|----------|
| TaskSpec | `core/protocol.py:151-190` | **无budget字段，无difficulty字段，无critical标记** |
| TaskResult | `core/protocol.py:58-80` | status/summary/result/artifacts/confidence/worker_id |
| ExecutionReport | `core/protocol.py:227-263` | 只有pass/fail计数，无per-worker统计 |
| Directive | `core/protocol.py:198-223` | goal/intent/constraints/priority，**无难度评估** |
| Worker | `core/worker.py:194-430` | **工厂创建，每次dispatch新实例，无持久化ID** |
| Planner | `core/planner.py:60-1184` | **串行dispatch**，单Worker per spec（行197-233） |
| Reviewer | `core/reviewer.py:226-822` | **单一实例**无工具，0-token规则引擎`_calibrate_verdict`（行717-822） |
| TaskManager | `core/task_manager.py:60-221` | **显式单线程**（注释行6），仅track TaskRecord |
| Gatekeeper | `core/gatekeeper.py:48-1013` | 恢复循环（行344-373），交付校验（行886-954） |
| Entry | `main.py:224` | `reviewer = Reviewer(...)` 单一实例，共享给Planner+Worker |

---

## L3-1: 双Agent会签 (Dual Sign-off) — 新增Verifier角色

### 架构兼容性：⭐⭐ 严重冲突

**核心问题：Verifier必须是一个全新Agent类型，与现有Reviewer的设计原则根本冲突。**

- **Reviewer设计原则冲突**：`core/reviewer.py:6` 明确声明 "with ZERO tools"。Verifier需要执行代码/脚本来验证产出，这必须要有工具（terminal/execute_code）。Verifier不能是Reviewer的子类或衍生——它是一个全新的Agent类型，类似"有工具但只读"的Worker变体。

- **注入点问题**：当前Planner只维护单一`self._reviewer`（`core/planner.py:124`），在`_dispatch_with_review()`（行488-728）和Worker的`_review_sub_result()`（`core/worker.py:716-899`）两处调用。要注入第二个Agent，需要在Planner的构造函数（行96-136）新增`verifier`参数，在`_run_worker()`（行885-966）中创建Verifier实例，在dispatch循环中插入新的验证步骤。

- **协议层需要扩展**：`TaskResult`（`core/protocol.py:58-80`）需要新增`verification_report`字段来存储Verifier的输出。`ExecutionReport`需要在`details`中区分"Reviewer判定"和"Verifier判定"。

- **与现有`_calibrate_verdict`的关系**：`_calibrate_verdict()`（`core/reviewer.py:717-822`）是0-token确定性规则引擎，做文本关键词匹配升级severity。Verifier是实际执行代码的Agent——两者互补而非冲突。Verifier负责"执行验证"（动态），calibrate负责"规则一致性"（静态）。实施时可以让Verifier先跑，然后calibrate做后处理。

### 数据流：⭐⭐ 中等冲突

- Verifier需要访问Worker的artifacts（文件路径），但Reviewer的`_build_artifact_contents()`（`core/reviewer.py:430-505`）是静态方法——可以复用。
- 但当前artifact预加载发生在Reviewer的`review()`方法内（行606），Verifier的集成需要独立的artifact读取管线。

### 实施难点

- 需要定义一个新的`Verifier`类，约400行新代码
- 必须在`_dispatch_with_review()`的尝试循环中插入Verifier步骤，改变重试决策逻辑
- Worker的`_review_sub_result()`（`core/worker.py:716-899`）也需要支持Verifier，否则子任务不在会签保护范围内

---

## L3-2: 确定性工具调用日志 (ToolCallLog模块)

### 架构兼容性：⭐⭐⭐ 低冲突

**这是最容易实施的L3项——本质是拦截现有工具调用路径并记录。**

- **拦截点明确**：工具调用发生在`_execute_loop()`（`core/worker.py:560-620`）。具体日志写入点：
  - 行586-594：每个`tool_call`被解析为`(name, arguments)`，然后执行`self._registry.execute()`——在这两行之间插入日志记录
  - 行593-594：`result_text`是工具返回值——也需要记录

- **不可篡改性的实现**：当前代码没有append-only日志基础设施。最轻量方案是用Python `logging` 写专用日志文件 + SHA256链。如果要求真正的不可篡改，需要引入新依赖（如`sqlite3` + checksum chain），这在`core/worker.py`的依赖中不存在。

- **Reviewer交叉比对需要协议扩展**：Reviewer的`review()`方法（`core/reviewer.py:509-679`）目前只接收`spec`和`result`两个参数。要传入工具日志，需要：
  - 扩展`TaskResult`（`core/protocol.py:58-80`）新增`tool_log: list[dict]`字段
  - 或作为第三个参数传入`review()`，但会破坏现有调用签名（Planner行556、Worker行743）

### 数据流：⭐⭐ 低冲突

- 工具日志写入和Worker工具调用在同一进程——Worker的`_execute_loop()`内，无需跨进程通信
- 唯一需要解决的是：日志应该在Worker进程内写，还是通过某种IPC发送到外部日志服务。当前架构下，同进程写入是最简单的方案

### 与已有修复的重叠

- `_desk_check()`（`core/planner.py:971-1027`）已经做了一部分"声明vs实际"的交叉检查（检查空结果、仅文件路径的结果、通用摘要+artifact不一致）。L3-2的工具日志会让这些检查变得更强——例如能检测Worker声明"读了文件X"但实际工具日志显示读的是文件Y。

---

## L3-3: Worker行为基线+统计异常检测

### 架构兼容性：⭐⭐⭐⭐ 严重冲突

**这是所有L3项中与当前架构冲突最大的——因为Worker没有持久化身份。**

- **Worker身份缺失**：`core/planner.py:199` — `worker_id = f"worker-{i}"` 是每次Planner执行时按序生成的，完全不跨Session持久。L3-3需要"同一Worker的历史数据"，但当前根本没有"同一个Worker"的概念——每次`_worker_factory()`调用（`core/planner.py:893`）返回全新实例。

- **Token消耗数据未被捕获**：`_execute_loop()`中LLM API调用（`core/worker.py:505-510`）返回的`response`对象包含`usage`信息，但代码只提取了`choices`（行554 `msg = choice.message`），token用量被丢弃。

- **没有存储位置**：`TaskManager`（`core/task_manager.py:60-221`）只跟踪当前会话的TaskRecord。`Session`（`core/session.py:16-125`）只存对话历史。行为基线需要一个跨Session的持久化存储——需要新建模块。

- **统计模型需要基础设施**：2σ异常检测需要：①累计足够样本（每个Worker至少20-30次执行）②计算均值和标准差③实时判断新数据点是否偏离。这些在当前代码中完全不存在。

### 实施难点

- 需要新建`WorkerProfile`持久化存储（SQLite/JSON文件，约200行）
- 需要改造`_execute_loop()`捕获token用量（约5行改动）
- 需要改造`_dispatch_with_review()`在每次完成后Update基线（约10行改动）
- 需要新建`BaselineDetector`类（约150行）

---

## L3-4: 随机深度审查抽检 (90%快速/10%深度)

### 架构兼容性：⭐⭐ 低冲突

- **注入点在Planner**：随机逻辑应该注入在`_dispatch_with_review()`（`core/planner.py:488-728`）中，在调用`self._reviewer.review()`（行556）之前决定走哪个通道。

- **但Reviewer不支持"快速/深度"模式切换**：`review()`（`core/reviewer.py:509-679`）只有一个执行路径——总是加载artifact内容（行606 `_build_artifact_contents`）、构建完整prompt、调用LLM。要支持快速通道，需要：
  - 新增`review_fast()`方法：只读summary+issues，跳过artifact预加载，只用`_calibrate_verdict()`做规则检查（~0 token）
  - 保留现有`review()`作为深度通道：完整加载artifact + LLM审计

- **随机种子**：可以用`random.random() < 0.1`在行556之前判断，简单直接

- **与现有`_adjust_verdict_for_criteria`的关系**：`_adjust_verdict_for_criteria()`（`core/planner.py:749-805`）是对Reviewer输出的事后调整——它在Review完成之后运行（行562-564）。快速通道如果跳过LLM审计直接用`_calibrate_verdict`，也仍然可以跑这个调整。

### 实施难点

- 相对容易：~30行改动，主要是拆分Reviewer的`review()`为两个模式
- 但深度通道应包含"执行测试"——这需要Verifier（L3-1）的能力，与L3-4有耦合

---

## L3-5: 智能摘要Agent (Summarizer独立角色)

### 架构兼容性：⭐⭐⭐ 中等冲突

**Summarizer是一个新的Agent类型，但不需要工具——作为纯LLM角色，实现难度可控。**

- **需要新建Agent类型**：Summarizer是"只读+纯LLM"，类似Reviewer但没有Reviewer那样的criteria对照功能。可以复用`OpenAI`客户端模式（参考`core/reviewer.py:367-371`），但prompt完全不同——专注于结构化摘要（函数列表、复杂度、异常模式）。

- **集成点**：Summarizer应该插在Worker输出和Reviewer输入之间。当前流是：
  ```
  Worker.run() → TaskResult → Reviewer.review(spec, result)
  ```
  L3-5需要变成：
  ```
  Worker.run() → TaskResult → Summarizer.summarize(result) → enriched TaskResult → Reviewer.review(spec, enriched)
  ```

- **与Reviewer的artifact预加载重叠**：`_build_artifact_contents()`（`core/reviewer.py:430-505`）已经做了"读取artifact内容并嵌入prompt"的工作。Summarizer做的是"生成结构化摘要"——两者不冲突，但Summarizer的输出应该作为额外字段添加到Reviewer的prompt中，减少Reviewer需要阅读的原文字量。

- **Worker看不到摘要**：这是个重要约束。当前`TaskResult`从Worker返回后就进入Planner的dispatch循环——摘要必须在Planner内部生成，不能回传给Worker。这在当前架构下自然满足——Summarizer在Planner内部调用。

### 数据流

- `TaskResult`（`core/protocol.py:58-80`）需要新增`structured_summary: Optional[str]`字段
- Summarizer的输出注入Reviewer的`_REVIEW_USER_TEMPLATE`（`core/reviewer.py:312-352`）——需要在模板中新增`{structured_summary}`占位符

---

## L3-6: 子任务独立预算池

### 架构兼容性：⭐⭐⭐ 中等冲突

**需要协议层和Worker两处改动，但逻辑清晰。**

- **TaskSpec缺预算字段**：`core/protocol.py:151-190` — 当前字段：task_id, description, acceptance_criteria, context, intent, goal, user_goal, constraints, depth。需要新增`budget: int = 50`（默认值维持向后兼容）。

- **Worker的max_tool_calls是全局的**：`core/worker.py:227` — `max_tool_calls`在构造函数中设置。`_execute_loop()`的循环条件（行498 `while tool_call_count < self._max_tool_calls`）检查的是实例级上限。改为per-spec预算需要：
  - `run()`方法（行267）接收spec中的`budget`字段，传递给`_execute_loop()`
  - `_execute_loop()`的循环条件改为`while tool_call_count < budget`
  - 保留`self._max_tool_calls`作为硬上限（防止单任务预算过大）

- **TaskManager不需要大改**：`TaskManager`只跟踪状态，不管理预算。但如果需要预算消耗的记录（用于L3-3统计），可以在`TaskRecord`（`core/task_manager.py:31-48`）新增`budget_consumed`字段。

- **main.py的配置**：`max_tool_calls`从`config.yaml`读取（行179），作为默认预算值。

### 实施难点

- 改动集中在`core/protocol.py`（+1字段）和`core/worker.py`（改造循环条件），约20行改动
- 本质上就是给每个TaskSpec一个独立的计数器，而不是共享全局计数器

---

## L3-7: 关键子任务不同Worker执行

### 架构兼容性：⭐⭐⭐⭐ 严重冲突

**与当前串行dispatch模型冲突最大。需要并发改造。**

- **当前dispatch是严格的串行for循环**：`core/planner.py:197-233` — `for i, spec in enumerate(specs)`。要支持"多个Worker执行同一个关键子任务的不同副本"，需要：
  - 将串行循环改为并发调度
  - `TaskManager`（`core/task_manager.py:6`）显式标注"Thread-safe NOT required"——并发改造会破坏这个假设

- **协议层不够用**：`ExecutionReport`（`core/protocol.py:227-263`）只记录"总通过/失败"——如果同一个task_id有多个Worker执行结果，Report无法表达"3个Worker中2个通过"的语义。需要：
  - `TaskResult`新增`consensus: Optional[str]`字段
  - `ExecutionReport`新增`consensus_details: list[dict]`字段

- **Worker工厂不能区分Worker**：`main.py:227-234` — 工厂只支持`model_override`参数。L3-7需要"不同Worker"意味着不同模型或不同API key或不同的persona——当前工厂不够。需要扩展为：
  ```python
  def _make_worker(model_override=None, profile_override=None) -> Worker
  ```

- **TaskSpec需要critical标记**：当前无字段标记哪些任务"关键"——需要在`TaskSpec`新增`is_critical: bool = False`（`core/protocol.py`）

### 性能与并发

- 如果只是"同一关键任务用不同模型跑两次然后比较结果"，可以不并发，只是同一个for循环内对同一个spec跑两次`_run_worker()`——但这样执行时间翻倍
- 如果要真正并发，需要引入`asyncio`或`ThreadPoolExecutor`，改动涉及整个`execute()`方法（行140-233），约100行重构

---

## L3-8: 子任务难度独立评估+困难优先

### 架构兼容性：⭐⭐ 低冲突

**实施难度中等，冲突不大，但增加LLM调用。**

- **当前无难度概念**：`TaskSpec`（`core/protocol.py:151-190`）无difficulty字段。`Directive`（行198-223）无难度评估。需要新增。

- **评估时机**：Planner的`_plan()`（`core/planner.py:281-484`）生成TaskSpec列表后，Dispatch之前（`execute()`行178-196之间），对每个spec做难度评估。可以：
  - 方案A：增加一次LLM调用，批量评估所有spec的难度（~0.3 token as spec'd）
  - 方案B：让`_plan()`的LLM在分解时直接输出难度（改prompt）

- **排序逻辑**：在`execute()`的行198 `for i, spec in enumerate(specs)` 之前插入`specs.sort(key=lambda s: s.difficulty, reverse=True)`——约1行改动。

- **协议扩展**：`TaskSpec`新增`difficulty: str = "medium"`字段。

### 实施难点

- 主要挑战是难度评估的准确性——LLM判断"这个任务难不难"可能不可靠
- 如果评估错误导致简单任务被跳过后复杂任务先失败，可能浪费budget

---

## L3-9: Retry上限+自动升级到Gatekeeper

### 架构兼容性：⭐⭐⭐ 中等冲突——与现有恢复循环有重叠

**当前已有retry限制和Gatekeeper级恢复，L3-9需要在中间插入一个中断点。**

- **现有retry逻辑**：`_dispatch_with_review()`（`core/planner.py:488-728`）已经实现了基于verdict的分级重试：
  - MINOR_REVISIONS → 1次重试（行585-667）
  - MAJOR_REVISIONS/REJECTED → 最多2次重试（行669-693）
  - 全部失败后标记FAILED（行695-728）

- **现有Gatekeeper恢复循环**：`_execute_via_planner()`（`core/gatekeeper.py:316-419`）在Planner返回report后检查`report.failed > 0`（行348），然后进入恢复循环。但这个循环是**整个directive级别**的——所有任务跑完后才判断。

- **L3-9要求per-task升级**：当一个任务retry>2时，不应该等所有任务跑完，而应**立即**将控制权返回Gatekeeper，让Gatekeeper决定"换Worker"或"修改strategy"。这与当前架构的"先跑完所有任务再汇报"模式冲突。

- **与已有`_dispatch_with_review`的重叠**：`_dispatch_with_review()`（行514 `for attempt in range(max_retries + 1)`）已经做了retry上限——不同priority有不同max_retries（行187-194）。但它是**内部消化**，而不是**向上汇报**。L3-9要求"自动升级"意味着：内部消化失败 → 中断Planner → Gatekeeper介入。

### 实施难点

- 需要改变Planner→Gatekeeper的通信模式——当前是"跑完汇报"，L3-9需要"中途中断并升级"
- `ExecutionReport`需要新增`escalated_tasks: list[dict]`字段让Gatekeeper知道哪些任务需要特殊处理
- Gatekeeper的恢复循环需要增加"单任务重新分配"的能力，而不只是重建整个directive

---

## L3-10: Worker信用评分系统

### 架构兼容性：⭐⭐⭐⭐⭐ 几乎不可能在当前架构下实施

**这是最难实施的L3项——因为Worker完全不存在跨Session的身份。**

- **Worker完全无持久化身份**：如L3-3分析所述，`worker_id = f"worker-{i}"`（`core/planner.py:199`）每次会话从0开始计数。信用评分需要一个跨越多次会话、多次执行、不同任务类型的持久化Worker标识。

- **需要全新的基础设施**：
  - 持久化Worker身份存储（按模型/API key/profile区分）
  - 评分计算引擎（综合通过率、审查分歧率、行为基线偏差、重试频率）
  - 评分对调度的影响逻辑（低信用→降低任务优先级+提高抽检率）

- **评分影响需要反馈到L3-4**：低信用Worker提高抽检率——这建立了L3-10↔L3-4的耦合，需要两者同时存在才能生效

- **需要新建模块**：预估`core/worker_credit.py`约300行，包含`CreditStore`和`CreditScorer`两个类

### 实施难点

- 在Worker无持久身份的架构下，L3-10基本不可实施
- 必须先实施L3-3（行为基线）作为数据来源
- 必须先建立Worker身份系统——这是L3-3和L3-10的共同前提

---

## 汇总：依赖关系和实施路径

```
先决条件（必须先做）:
  ✅ 新建 WorkerIdentity 持久化系统 ← L3-3 和 L3-10 的共同前提
  ✅ TaskSpec 扩展 (budget, difficulty, is_critical) ← L3-6, L3-7, L3-8

可独立实施（低冲突）:
  ⬜ L3-2 工具调用日志 ← ~30行，拦截_execute_loop
  ⬜ L3-4 随机抽检 ← ~30行，拆分review()为快速/深度模式
  ⬜ L3-6 独立预算池 ← ~30行，TaskSpec+budget + Worker循环改造
  ⬜ L3-8 难度评估+困难优先 ← ~40行，增加LLM评估调用+排序

依赖其他L3项:
  ⬜ L3-1 双Agent会签 ← 需要新建Verifier类，可与L3-4深度通道复用
  ⬜ L3-5 智能摘要 ← 需要新建Summarizer类，可独立于其他L3
  ⬜ L3-3 行为基线 ← 依赖WorkerIdentity系统
  ⬜ L3-10 信用评分 ← 依赖L3-3（基线数据）+ WorkerIdentity
  ⬜ L3-7 多Worker并发 ← 需要协议层扩展+并发改造
  ⬜ L3-9 retry升级 ← 需要改变Planner→Gatekeeper通信模式
```

## 关键冲突总结

| L3项 | 与已有代码的致命冲突 | 文件:行号 |
|------|---------------------|-----------|
| L3-1 | Verifier需要工具，Reviewer设计为无工具 | `reviewer.py:6` "ZERO tools" |
| L3-1 | Planner只维护单一reviewer引用 | `planner.py:124` `self._reviewer` |
| L3-3 | Worker无持久身份 | `planner.py:199` `worker_id = f"worker-{i}"` |
| L3-3 | Token用量未被捕获 | `worker.py:553-554` 只取choices不取usage |
| L3-6 | TaskSpec无budget字段 | `protocol.py:151-190` |
| L3-7 | dispatch是严格串行for循环 | `planner.py:198` `for i, spec in enumerate(specs)` |
| L3-7 | TaskManager显式单线程 | `task_manager.py:6` "Thread-safe NOT required" |
| L3-8 | TaskSpec无difficulty字段 | `protocol.py:151-190` |
| L3-9 | 重试是Planner内部消化，无升级机制 | `planner.py:695-728` |
| L3-10 | 无Worker持久化身份（同L3-3） | `planner.py:199` |
| L3-10 | 无跨Session的Worker表现记录 | 新模块需求 |
| 所有 | Worker工厂不支持profile分发 | `main.py:227-234` 只支持model_override |

## 与已有防欺骗措施的重叠

当前Janus已有以下防欺骗措施（在分析L3之前就已存在），部分与L3项重叠：

| 已有措施 | 文件:行号 | 与哪个L3项重叠 | 关系 |
|----------|-----------|---------------|------|
| `_calibrate_verdict()` 规则引擎 | `reviewer.py:717-822` | L3-1 (Verifier) | 互补：calibrate做静态规则检查，Verifier做动态执行验证 |
| `_adjust_verdict_for_criteria()` 再校准 | `planner.py:749-805` | L3-4 (抽检) | 重叠：这已经是"快速通道"的一种形式——在LLM审计后做规则修正 |
| `_desk_check()` 桌面预筛查 | `planner.py:971-1027` | L3-2 (工具日志) | 互补：desk_check做快速heuristics，工具日志提供更精确的比对数据 |
| artifact真实性校验（空文件/占位符检测） | `reviewer.py:542-593` | L3-2 (工具日志) | 重叠：已验证artifact存在性和内容质量，工具日志进一步验证"声明vs实际" |
| Reviewer的`_build_artifact_contents()` | `reviewer.py:430-505` | L3-5 (Summarizer) | 重叠：Summarizer的产出可以替代/增强artifact预加载 |
| Gatekeeper恢复循环 `_execute_via_planner()` | `gatekeeper.py:344-373` | L3-9 (retry升级) | 重叠：已在directive级别做恢复，L3-9要求在task级别做 |
| 交付校验 `_validate_delivery()` | `gatekeeper.py:886-954` | L3-1 (双会签) | 互补：交付校验检查"产出是否符合用户需求"，双会签检查"产出是否真的被执行验证过" |
