# Janus Planner（参谋）角色设计

> **状态：** ✅ 已实现（实际字段名与提案略有差异，见下方标注）
> **日期：** 2026-07-16
> **前置阅读：** `docs/social-structure-insights.md`（人类社会结构启示）、`docs/information-flow.md`（信息流全景）、`docs/user-gatekeeper-protocol.md`（Gatekeeper↔User 协议）
>
> **⚠️ 代码实现差异：** 以下设计方案的核心架构已落地，但实际 `protocol.py` 中的字段名有所不同：
> - 提案 `Directive.direction` → 代码 `Directive.intent`
> - 提案 `Directive.constraints: list[str]` → 代码 `Directive.constraints: str`
> - 提案 `Directive.directive_id` → 未实现（不需要跨次追踪）
> - 提案 `ExecutionReport.completed_tasks / failed_tasks` → 代码 `ExecutionReport.passed / failed`
> - 提案 `ExecutionReport.directive_id / escalations / suggestions` → 未实现（Phase 4 简化）

---

## 目录

- [1. 问题：Gatekeeper 太重](#1-问题gatekeeper-太重)
- [2. 目标架构](#2-目标架构)
- [3. Planner 角色定义](#3-planner-角色定义)
- [4. 接口契约：Gatekeeper ↔ Planner](#4-接口契约gatekeeper--planner)
- [5. Planner 内部结构](#5-planner-内部结构)
- [6. 方法迁移清单：从 Gatekeeper 到 Planner](#6-方法迁移清单从-gatekeeper-到-planner)
- [7. Gatekeeper 瘦身后剩什么](#7-gatekeeper-瘦身后剩什么)
- [8. 借鉴的组织模式](#8-借鉴的组织模式)
- [9. 关键设计决策](#9-关键设计决策)
- [10. 推荐实施顺序](#10-推荐实施顺序)
- [11. 边界情况与风险](#11-边界情况与风险)
- [附录 A：瘦身前后的代码量对比估算](#附录-a瘦身前后的代码量对比估算)

---

## 1. 问题：Gatekeeper 太重

### 1.1 现状（已解决）

> ⚠️ 此问题已在 Phase 4 中通过引入 Planner 解决。当前架构：Gatekeeper（889 行）负责战略决策，Planner（1125 行）负责战术执行。以下分析保留作为设计决策的记录。

原 `core/gatekeeper.py` 曾承担从战略决策到战术执行的**全部职责**：

```
Gatekeeper.handle(message)
  ├── _decide()          ← 战略：判断 chat vs task（LLM 调用）
  ├── _respond()         ← 战略：纯对话回复（LLM 调用）
  └── _execute_task()
       ├── _decompose()            ← 战术：目标分解（LLM 调用）
       ├── _dispatch_with_review() ← 战术：分派 Worker + Reviewer 审计 + 重试
       │    ├── _run_worker()      ← 战术：创建 Worker 实例并运行
       │    └── _make_retry_spec() ← 战术：构建重试 TaskSpec
       └── 汇总结果                ← 战术：统计通过/失败
```

Gatekeeper 的系统提示 `_GATEKEEPER_IDENTITY` 自称：

> "你是军师，用户（主公）向你下达目标，你负责思考和决策。"

但现实中它也在做**参谋部的工作**——分解、分派、追踪、汇总。军师和参谋混在一个类里，导致：

| 问题 | 具体表现 |
|------|---------|
| **上下文压力大** | `_execute_task()` 中 Gatekeeper 直接遍历 specs、管理 TaskManager、处理重试——所有这些都发生在 Gatekeeper 的上下文中 |
| **职责模糊** | 同一个 LLM 实例既做战略决策（"这个目标应该拆成哪几个方向"）又做战术执行（"这个 Worker 失败了，重试还是放弃"） |
| **扩展困难** | 想加并行执行、动态重规划、子任务间依赖——都要改 Gatekeeper，而它已经 742 行 |
| **测试困难** | 想单独测试分解逻辑、分派逻辑、重试逻辑——它们耦合在 Gatekeeper 的一个方法里 |

### 1.2 核心矛盾

**战略思维和战术执行是两种完全不同的认知模式。**

- 战略（Gatekeeper 该干的）：理解意图、定方向、做取舍、向用户汇报。类比军事中的**指挥官**。
- 战术（Planner 该干的）：拆任务、分派人、追踪进度、处理异常、汇总结果。类比军事中的**参谋部**。

让同一个 LLM 实例同时做这两件事，就像让将军既指挥战役又亲自填作战表格。

---

## 2. 目标架构

```
                         ┌──────────────────────┐
                         │      用  户（主公）    │
                         └──────────┬───────────┘
                                    │ 自然语言
                                    ▼
                         ┌──────────────────────┐
                         │    Gatekeeper（军师）  │
                         │                      │
                         │  职责：               │
                         │  - 理解用户意图       │
                         │  - 判断 chat vs task  │
                         │  - 定战略方向         │
                         │  - 向用户汇报         │
                         │                      │
                         │  有 LLM：✅           │
                         │  有工具：❌（不变）    │
                         └──────────┬───────────┘
                                    │ Directive（作战指令）
                                    ▼
                         ┌──────────────────────┐
                         │   Planner（参谋）      │
                         │                      │
                         │  职责：               │
                         │  - 把意图拆成战术计划  │
                         │  - 分派 Worker        │
                         │  - 追踪进度           │
                         │  - 审核→重试循环      │
                         │  - 汇总结果           │
                         │                      │
                         │  有 LLM：✅           │
                         │  有工具：❌（零工具）   │
                         └──────┬───────┬───────┘
                                │       │
                    ┌───────────┘       └───────────┐
                    ▼                               ▼
          ┌──────────────┐                ┌──────────────┐
          │ Worker（执行） │                │Reviewer（验收）│
          │              │                │              │
          │ 有 LLM：✅   │                │ 有 LLM：✅   │
          │ 有工具：✅   │                │ 有工具：❌   │
          └──────────────┘                └──────────────┘
```

### 2.1 三层对比

| 层级 | 角色 | 类比 | 有 LLM？ | 有工具？ | 对外接口 |
|------|------|------|:------:|:------:|---------|
| **Gatekeeper** | 军师/指挥官 | 军事：将军；企业：CEO | ✅ | ❌ | 只对用户 |
| **Planner** | 参谋/参谋长 | 军事：G3 作战参谋；企业：PMO | ✅ | ❌ | 对 Gatekeeper + Worker + Reviewer |
| **Worker** | 执行者 | 士兵/工程师 | ✅ | ✅ | 对 Planner |
| **Reviewer** | 审计 | 质检/同行评审 | ✅ | ❌ | 对 Planner |

**关键约束：Planner 和 Gatekeeper 一样——零工具。它不能读文件、写文件、跑命令。它只做计划、分派、追踪、汇总。**

---

## 3. Planner 角色定义

### 3.1 一句话定义

> **Planner 是战术执行层的管理者。它接收 Gatekeeper 的作战指令（Directive），将其分解为可执行的任务计划，分派给 Worker 执行，追踪进度，协调 Reviewer 审计，并将结果汇总为执行报告返回给 Gatekeeper。**

### 3.2 核心职责

| 职责 | 说明 | 当前在 Gatekeeper 的方法 |
|------|------|------------------------|
| **理解作战指令** | 解析 Gatekeeper 下发的 Directive，理解意图、方向、约束 | 无需 LLM——纯数据结构解析 |
| **战术分解** | 把 Directive 拆成 TaskSpec 列表 | `_decompose()` |
| **任务分派** | 创建 Worker，分派 TaskSpec，管理生命周期 | `_run_worker()` |
| **审核协调** | 调用 Reviewer 审计 Worker 产出，决策重试/通过/放弃 | `_dispatch_with_review()` |
| **进度追踪** | 管理 TaskManager，维护任务状态机 | `_execute_task()` 中的循环 |
| **异常处理** | Worker 崩溃、超时、重试用尽时的降级策略 | `_dispatch_with_review()` 中的重试逻辑 |
| **结果汇总** | 将所有 Worker 结果汇总为 ExecutionReport | `_execute_task()` 的汇总部分 |

### 3.3 不该做的事

| 不该做 | 原因 |
|--------|------|
| 直接和用户对话 | 用户只和 Gatekeeper 对话。Planner 对用户完全不可见 |
| 做战略决策（"这个方向对不对"） | 方向由 Gatekeeper 定。Planner 只负责"在给定方向下怎么执行" |
| 读文件/写文件/跑命令 | Planner 和 Gatekeeper 一样零工具——它通过 Worker 间接操作 |
| 修改 Directive | Planner 发现 Directive 有问题时上报 Gatekeeper（异常升级），不自作主张 |
| 自己执行任务 | 永远不写代码、不调 API。只做计划和管理 |

### 3.4 Planner 有自己的 LLM

**是的。** Planner 需要 LLM 来做战术分解（`_decompose` → `_plan`）。理由：

1. **战术分解是 LLM 强项**——给定方向和约束，LLM 可以把模糊意图拆成具体可执行的 TaskSpec
2. **可以用更轻的模型**——战术分解比战略决策"浅"，用 `deepseek-chat` 而非 `deepseek-v4-pro`
3. **和 Gatekeeper 隔离**——Planner 的思考不污染 Gatekeeper 的战略上下文

但 Planner 的 LLM 调用比 Gatekeeper 更受控——只用于分解和汇总，不用于自由对话。

---

## 4. 接口契约：Gatekeeper ↔ Planner

### 4.1 Directive（作战指令）：Gatekeeper → Planner

```python
@dataclass
class Directive:
    """Gatekeeper 下发给 Planner 的作战指令。

    对应军事中的"指挥官意图"——只定方向和约束，不定执行细节。
    """

    # ── 核心 ──
    goal: str
    """用户原始目标，一字不改。Planner 需要原文来理解语境。"""

    direction: str
    """Gatekeeper 的战略方向判断。
    不是"怎么做"，而是"往哪个方向做"。
    示例："这是一个数据分析任务，核心是数据清洗和可视化，先保证正确性再考虑美化。"
    """

    # ── 约束 ──
    constraints: list[str] = field(default_factory=list)
    """硬约束——Planner 分解时必须遵守。
    示例：["最大递归深度 3", "不修改项目根目录外的文件", "所有输出用中文"]
    """

    priority: str = "balanced"
    """优先级：speed | quality | balanced
    影响 Planner 的分派策略（并行 vs 串行、审核严格度）。
    """

    # ── 元信息 ──
    directive_id: str = ""
    """唯一标识，用于追踪和关联。"""
```

### 4.2 ExecutionReport（执行报告）：Planner → Gatekeeper

```python
@dataclass
class ExecutionReport:
    """Planner 完成任务后返回给 Gatekeeper 的执行报告。

    只包含战略级信息——Gatekeeper 不需要知道哪个 Worker 做了什么，
    只需要知道"任务完成了多少、有什么问题、关键发现是什么"。
    """

    # ── 状态 ──
    directive_id: str
    """对应的作战指令 ID。"""

    status: str  # "completed" | "partial" | "failed"
    """整体状态。"""

    # ── 统计 ──
    total_tasks: int
    completed_tasks: int
    failed_tasks: int

    # ── 摘要 ──
    summary: str
    """三五句话的执行摘要，给 Gatekeeper 向上汇报用。
    只含架构级信息：做了什么、产出什么、关键决策、遗留问题。
    """

    # ── 详情（可选——Gatekeeper 在需要时深入查看） ──
    task_results: list[dict] = field(default_factory=list)
    """每个任务的简化结果：{task_id, status, summary}。
    不包含完整 TaskResult——那是 Planner 内部的事。
    """

    # ── 升级事项 ──
    escalations: list[str] = field(default_factory=list)
    """需要 Gatekeeper 决策的事项。
    示例：["两个子任务产出矛盾，无法自动裁决", "检测到安全风险，需确认是否继续"]
    """

    # ── 建议 ──
    suggestions: list[str] = field(default_factory=list)
    """Planner 从执行中洞察到的改进建议。
    示例：["建议将数据清洗和可视化拆为两个独立指令以提高并行度"]
    """
```

### 4.3 交互流程

```
Gatekeeper                          Planner
    │                                  │
    │  Directive(goal, direction,      │
    │           constraints, priority)  │
    │ ─────────────────────────────────>│
    │                                  │
    │                                  ├─ _plan(directive) → [TaskSpec, …]
    │                                  ├─ 对每个 TaskSpec:
    │                                  │   ├─ _dispatch(spec)
    │                                  │   │   ├─ Worker.run(spec)
    │                                  │   │   ├─ Reviewer.review(spec, result)
    │                                  │   │   └─ 重试/通过/放弃
    │                                  │   └─ TaskManager 状态更新
    │                                  ├─ _summarize() → ExecutionReport
    │                                  │
    │  ExecutionReport                 │
    │ <─────────────────────────────────│
    │                                  │
    ├─ 判断：report 是否有 escalations？
    │   ├─ 有 → 向用户报告 + 请求决策
    │   └─ 无 → 用自己的语言向用户汇报
    │
    └─ 用户看到 Gatekeeper 的汇报（不感知 Planner 存在）
```

---

## 5. Planner 内部结构

### 5.1 类结构

```python
class Planner:
    """战术执行层管理者——接收 Directive，产出 ExecutionReport。

    零工具。有 LLM（用于分解和汇总）。
    管理 TaskManager、Worker 工厂、Reviewer。
    """

    def __init__(
        self,
        model: str,                  # 战术分解用 LLM（通常比 Gatekeeper 轻）
        api_key: str,
        task_manager: TaskManager,
        worker_factory: Callable[..., Worker],
        reviewer: Reviewer | None = None,
        max_depth: int = 3,
        max_retries: int = 2,
        console: Console | None = None,
    ): ...

    # ── 核心入口 ──
    def execute(self, directive: Directive) -> ExecutionReport:
        """接收作战指令，执行完整管线，返回执行报告。"""

    # ── 内部方法 ──
    def _plan(self, directive: Directive) -> list[TaskSpec]:
        """战术分解：Directive → [TaskSpec, ...]（LLM 调用）"""

    def _dispatch_with_review(
        self, spec: TaskSpec, max_retries: int = 2
    ) -> TaskResult:
        """分派 Worker → Reviewer 审计 → 重试循环"""

    def _run_worker(self, spec: TaskSpec) -> TaskResult:
        """创建 Worker 实例并运行"""

    def _make_retry_spec(self, spec: TaskSpec, feedback: str) -> TaskSpec:
        """构建带反馈的重试 TaskSpec"""

    def _summarize(self, results: list[TaskResult], directive: Directive) -> ExecutionReport:
        """汇总结果 → ExecutionReport（轻量 LLM 调用，仅在有 escalations 时）"""

    def _check_escalations(self, results: list[TaskResult]) -> list[str]:
        """检测需要升级给 Gatekeeper 的事项"""
```

### 5.2 内部管线

```
Planner.execute(directive)
  │
  ├─ 1. 重置状态
  │     task_manager.reset()
  │
  ├─ 2. 战术分解
  │     specs = self._plan(directive)  ← LLM 调用
  │     └─ 将 directive.direction 和 constraints 注入 LLM prompt
  │     └─ 产出 TaskSpec 列表（复用现有的 TaskSpec 结构，零改动）
  │
  ├─ 3. 串行分派（Phase 1 保持串行，未来可并行）
  │     for spec in specs:
  │         task_manager.add_task(spec)
  │         task_manager.mark_running(spec.task_id)
  │         result = self._dispatch_with_review(spec)
  │         task_manager.mark_completed(spec.task_id, result)
  │
  ├─ 4. 检测升级事项
  │     escalations = self._check_escalations(results)
  │     └─ 矛盾检测？无法自动裁决？→ 列入 escalations
  │
  └─ 5. 汇总 → ExecutionReport
        report = self._summarize(results, directive)  ← 轻量 LLM 调用（仅必要时）
```

### 5.3 Planner 的 LLM 调用点

| 方法 | LLM 调用？ | 原因 |
|------|:--------:|------|
| `_plan()` | ✅ | 战术分解需要 LLM 理解方向和约束 |
| `_dispatch_with_review()` | ❌ | 纯逻辑——分派、调 Reviewer、重试判断 |
| `_run_worker()` | ❌ | 创建 Worker → `worker.run()`（Worker 自己有 LLM） |
| `_summarize()` | ⚡ 按需 | 多数情况纯逻辑聚合；有 escalations/矛盾时才调 LLM 做语义汇总 |
| `_check_escalations()` | ❌ | 规则匹配：矛盾检测、安全风险、重试用尽 |

**Planner 的 LLM 使用量远低于当前 Gatekeeper。** 当前 Gatekeeper 对每个任务做 _decompose → 每个任务做 _dispatch_with_review。Planner 只在分解时调用一次 LLM。

### 5.4 递归 Planner？

**不需要。** Planner 下发的 Worker 如果需要自分解，走现有机制（Worker 返回 NEEDS_DECOMPOSITION → Planner 审批 → 子 Worker）。不需要再嵌套一个 Planner。

理由：
- Planner 的粒度 = 一次 Directive 的全部执行
- 如果子任务复杂到需要另一个 Planner，说明 Directive 本身拆得不够细——这是 Gatekeeper 的责任（把大目标拆成多个 Directive，每个给一个 Planner）
- 递归 Planner = 层级爆炸 → 上下文膨胀。保持两层（Gatekeeper → Planner）是甜点。

**但**：如果 Gatekeeper 把一个超大目标拆成多个 Directive（如"重构整个系统"→ Directive-A: 数据库、Directive-B: API、Directive-C: 前端），每个 Directive 都可以有自己的 Planner 实例。这是**并行 Planner**而非**递归 Planner**。

---

## 6. 方法迁移清单：从 Gatekeeper 到 Planner

### 6.1 迁移对照表

| Gatekeeper 当前方法 | 行数 | 迁移到 Planner？ | 新名称 | 变化 |
|---------------------|:--:|:--------------:|--------|------|
| `_decide()` | ~30 | ❌ 留 Gatekeeper | 不变 | — |
| `_respond()` | ~30 | ❌ 留 Gatekeeper | 不变 | — |
| `_decompose()` | ~110 | ✅ 移到 Planner | `_plan()` | 输入从 `goal: str` 改为 `directive: Directive` |
| `_execute_task()` | ~60 | ✅ 移到 Planner | `execute()` | 输入从 `goal: str` 改为 `directive: Directive`；返回 `ExecutionReport` 而非 `str` |
| `_dispatch_with_review()` | ~140 | ✅ 移到 Planner | 不变 | 逻辑完全不变——只是 `self._reviewer` 等现在来自 Planner |
| `_run_worker()` | ~50 | ✅ 移到 Planner | 不变 | 逻辑完全不变 |
| `_make_retry_spec()` | ~15 | ✅ 移到 Planner | 不变 | 纯静态方法，不变 |
| `_extract_json()` | ~40 | ⚡ 复制到 Planner | 不变 | 工具方法，两个类都需要。或者提取到 `core/json_utils.py` |
| `_CONTEXT_DISCIPLINE_PROMPT` | ~7 | ⚡ 拆分 | — | 保留在 Gatekeeper 中；Planner 需要自己的"战术纪律"版 |
| `_GATEKEEPER_IDENTITY` | ~18 | ❌ 留 Gatekeeper | 不变 | — |
| `_DECIDE_SYSTEM_PROMPT` | ~3 | ❌ 留 Gatekeeper | 不变 | — |
| `_DECOMPOSE_SYSTEM_PROMPT` | ~4 | ✅ 移到 Planner | `_PLAN_SYSTEM_PROMPT` | 改为"参谋"角色定义 |
| `_CHAT_SYSTEM_PROMPT` | ~3 | ❌ 留 Gatekeeper | 不变 | — |

### 6.2 移到 Planner 的实例变量

| Gatekeeper 当前变量 | 迁移？ | 说明 |
|---------------------|:----:|------|
| `self._task_manager` | ✅ | Planner 管理 TaskManager |
| `self._worker_factory` | ✅ | Planner 创建 Worker |
| `self._reviewer` | ✅ | Planner 协调 Reviewer |
| `self._max_depth` | ✅ | Planner 控制深度 |
| `self._worker_model` | ✅ | Planner 选择 Worker 模型 |
| `self._console` | ⚡ 两个都要 | Gatekeeper 用它输出战略思考；Planner 用它输出战术进度 |
| `self._client` | ⚡ 两个都要 | 各自有自己的 LLM 客户端 |
| `self._last_error` | ✅ | Planner 的错误追踪 |

### 6.3 不动的东西

| 模块 | 变化 | 原因 |
|------|:--:|------|
| `core/protocol.py` | ❌ 零变化 | TaskSpec、TaskResult、TaskStatus 完全不变 |
| `core/worker.py` | ❌ 零变化 | Worker 不感知调用它的是 Gatekeeper 还是 Planner |
| `core/reviewer.py` | ❌ 零变化 | Reviewer 不感知调用上下文 |
| `core/task_manager.py` | ❌ 零变化 | 状态机逻辑完全不变 |
| `core/console.py` | ⚡ 微调 | 新增 Planner 相关的输出标签（如 `[参谋]`） |
| `core/session.py` | ⚡ 微调 | 从调 `Gatekeeper.handle()` 变为调 Gatekeeper → Planner 管线 |
| `main.py` | ⚡ 微调 | 连线：创建 Planner 实例，注入 Gatekeeper |

---

## 7. Gatekeeper 瘦身后剩什么

### 7.1 瘦身后的 Gatekeeper

```python
class Gatekeeper:
    """战略决策层——只对用户。

    零工具。只做三件事：
    1. 理解用户意图（_decide）
    2. 对话回复（_respond）
    3. 下发作战指令给 Planner + 接收执行报告 + 向用户汇报
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        planner: Planner,           # ← 新增：注入 Planner
        console: Console | None = None,
    ): ...

    # ── 统一入口（不变） ──
    def handle(self, message: str) -> str:
        """Gatekeeper 决策：chat 还是 task？"""
        decision = self._decide(message)
        if decision.get("action") == "chat":
            return self._respond(message)
        else:
            return self._execute_via_planner(message)

    # ── 战略决策（保留，不变） ──
    def _decide(self, message: str) -> dict[str, str]: ...
    def _respond(self, message: str) -> str: ...

    # ── 新增：通过 Planner 执行 ──
    def _execute_via_planner(self, goal: str) -> str:
        """新的任务执行路径：Gatekeeper 不亲自分解/分派。

        1. 生成 Directive（轻量 LLM 调用——只定方向）
        2. 下发给 Planner
        3. 接收 ExecutionReport
        4. 用自己的语言向用户汇报
        """
        directive = self._formulate_directive(goal)     # ← 新增
        report = self._planner.execute(directive)        # ← 委托
        return self._report_to_user(report)              # ← 新增

    def _formulate_directive(self, goal: str) -> Directive:
        """轻量 LLM 调用：把用户目标翻译为作战指令。

        不分解为 TaskSpec——只定方向和约束。
        输出 ~200 tokens，而非原来的 ~1000+ tokens 分解 JSON。
        """

    def _report_to_user(self, report: ExecutionReport) -> str:
        """把执行报告翻译为用户友好的自然语言。

        遵循 user-gatekeeper-protocol.md：
        - 只说架构和结果，不说 Worker 细节
        - 有升级事项时请求用户决策
        - 用中文，军师的口吻
        """
```

### 7.2 瘦身幅度

| 指标 | 当前 Gatekeeper | 瘦身后 Gatekeeper | 减少 |
|------|:------------:|:----------------:|:---:|
| 总行数 | ~742 | ~350 | **53%** |
| 方法数 | 9 | 6 | 33% |
| LLM 调用点 | 3~4 | 2~3 | 25% |
| 管理的实例 | TaskManager + WorkerFactory + Reviewer | 仅 Planner | — |
| 上下文涉及 | 分解结果 + Worker 结果 + 重试状态 | 仅 Directive + ExecutionReport | **大幅减少** |

### 7.3 瘦身后的好处

1. **Gatekeeper 真正做到了"零执行细节"**——它看不到 TaskSpec、TaskManager、Worker、重试循环。它只看到 Directive（输入）和 ExecutionReport（输出）。
2. **Gatekeeper 的上下文不再膨胀**——不再有 `for spec in specs` 循环、`_dispatch_with_review` 的重试状态。
3. **Gatekeeper 的 system prompt 更纯粹**——"军师"身份不再掺杂"参谋"操作。
4. **测试独立**——可以 mock Planner 来单独测试 Gatekeeper 的战略决策逻辑。

---

## 8. 借鉴的组织模式

### 8.1 军事：参谋部（General Staff）

军事参谋体系是 Planner 设计最直接的灵感来源。普鲁士/德国总参谋部（Großer Generalstab）定义了参谋的四个核心职能：

| 参谋部门 | 职能 | Janus Planner 对应 |
|----------|------|-------------------|
| **G1 人事** | 人员分配、兵力统计 | Worker 选择、Worker 模型分配 |
| **G2 情报** | 敌情分析、态势评估 | 上下文注入、依赖分析 |
| **G3 作战** | 制定作战计划、下达命令 | **战术分解（_plan）** ← 核心 |
| **G4 后勤** | 补给、装备、运输 | 工具注册、资源管理 |

**Janus Planner 本质上是 G3（作战参谋）+ 轻量 G1/G2/G4。**

关键原则：
- **参谋不指挥战斗**——Planner 不亲自执行（零工具），只做计划和管理
- **参谋向指挥官汇报**——Planner 只对 Gatekeeper 汇报，不对用户
- **指挥官定方向，参谋定方案**——Directive 是方向，TaskSpec 列表是方案

### 8.2 企业：PMO / 项目经理

| PMO 职能 | Janus Planner 对应 |
|----------|-------------------|
| 项目计划 | `_plan()` — 把战略目标拆成任务 |
| 资源分配 | `_run_worker()` — 选择合适的 Worker |
| 进度追踪 | `TaskManager` — 状态机管理 |
| 质量保证 | `_dispatch_with_review()` — 协调 Reviewer |
| 风险升级 | `_check_escalations()` — 向 Gatekeeper 报告异常 |
| 项目收尾 | `_summarize()` — 汇总 ExecutionReport |

### 8.3 为什么不用更复杂的分工？

有人可能问：为什么不把 Planner 再拆成 G1/G2/G3/G4 四个子模块？

**答：过度设计。** Janus 当前的规模不需要四个独立参谋。理由：

1. **Worker 选择**（G1）目前是规则匹配 + 工厂模式，不需要独立 LLM
2. **情报/上下文**（G2）由 TaskSpec.context 和 Directive.direction 覆盖，不需要独立情报模块
3. **后勤/工具**（G4）由 Worker 的 ToolRegistry 管理，Planner 不碰工具

**未来扩展**：如果 Janus 发展到管理数十个异质 Worker、需要动态资源调度和跨任务依赖分析时，可以将 Planner 内部模块化。但目前一个统一的 Planner 已足够。

---

## 9. 关键设计决策

### 9.1 Planner 有自己的 LLM 吗？

**有。** 用于战术分解（`_plan`）和按需汇总（`_summarize`）。

但用**更轻的模型**——`deepseek-chat` 而非 Gatekeeper 的 `deepseek-v4-pro`。理由：
- 分解是结构化输出（TaskSpec JSON），不需要深度推理
- 轻模型更快、更便宜
- 和 Gatekeeper 的战略推理分离，避免上下文污染

### 9.2 Planner 有工具吗？

**没有。** 和 Gatekeeper 一样零工具。

Planner 的"手"是 Worker。如果 Planner 自己能读文件，就会产生"我亲自看看"的诱惑——破坏分层。

### 9.3 Planner 可以递归吗？

**Phase 1 不可以。** 如果 Planner 发现自己拆出的子任务还需要再拆：
- Worker 返回 NEEDS_DECOMPOSITION → Planner 审批 → 创建子 Worker（现有机制）
- 不需要另一个 Planner

**但**：如果一个 Directive 本身太大（如"重构整个项目"），Gatekeeper 应该在战略层把它拆成多个 Directive（Directive-A: 数据库层、Directive-B: API 层），每个 Directive 独立分发给一个 Planner。这意味着：

```
Gatekeeper
  ├── Planner-1 (Directive-A: 数据库重构)
  │     ├── Worker-A1, Worker-A2
  │     └── Reviewer
  └── Planner-2 (Directive-B: API 重构)
        ├── Worker-B1, Worker-B2
        └── Reviewer
```

这是**并行 Planner**，不是递归 Planner。

### 9.4 Planner 对用户可见吗？

**不可见。** 用户只和 Gatekeeper 对话。Planner 是内部实现细节。

但 Console 输出中可以体现 Planner 的存在：

```
💭 [军师] 分析用户意图...                  ← Gatekeeper 的战略思考
📋 [参谋] 制定作战计划，拆分为 3 个任务...   ← Planner 的战术行动
⚡ Worker-0 执行中...
```

用户看到"[参谋]"标签知道有个东西在规划，但不需要理解 Planner 是什么。

### 9.5 Directive 由谁生成？

**Gatekeeper。** 具体是 `_formulate_directive()`。

这是一个**轻量 LLM 调用**——输入用户目标，输出结构化 Directive（~200 tokens 输出）。比当前 `_decompose()` 的分解（~1000+ tokens JSON 数组）轻得多。

**替代方案**：不用 LLM，纯模板填充（`Directive(goal=message, direction="", constraints=[])`）→ 可行但丢失了"战略判断"的价值。推荐先用轻量 LLM，后续可优化。

### 9.6 后向兼容

- `Gatekeeper.execute(goal)` 保留为便捷方法——内部创建默认 Directive → 调 Planner
- `Gatekeeper.chat(message)` 保留不变
- `Gatekeeper.handle(message)` 保留为统一入口
- Worker、Reviewer、TaskManager、Protocol **全部零变化**

---

## 10. 推荐实施顺序

### Phase 1：提取 Planner 骨架（1-2 天）

**目标**：创建 `core/planner.py`，把现有方法从 Gatekeeper 搬过来，Gatekeeper 通过 Planner 调用。

```
步骤 1：创建 core/planner.py
  - class Planner
  - __init__(model, api_key, task_manager, worker_factory, reviewer, console, ...)
  - 搬入 _decompose → 改名为 _plan
  - 搬入 _dispatch_with_review（一字不改）
  - 搬入 _run_worker（一字不改）
  - 搬入 _make_retry_spec（一字不改）
  - 新增 execute(directive) → ExecutionReport 入口

步骤 2：创建 Directive / ExecutionReport dataclass
  - 可以放在 core/protocol.py 或新建 core/directive.py

步骤 3：Gatekeeper 新增 _execute_via_planner()
  - 创建 Directive → 调 planner.execute() → 接收 ExecutionReport → 返回 str
  - 旧的 _execute_task() 保留但标记 deprecated

步骤 4：main.py 连线
  - 创建 Planner 实例
  - 传入 Gatekeeper(planner=planner)
  - TaskManager、WorkerFactory、Reviewer 从 Gatekeeper 移到 Planner

步骤 5：验证
  - 现有测试全部通过
  - 手动测试："写一个阶乘函数" → 行为和之前一样
```

### Phase 2：Gatekeeper 瘦身（1 天）

```
步骤 1：删除 Gatekeeper 中已迁移的方法
  - _decompose, _dispatch_with_review, _run_worker, _make_retry_spec

步骤 2：删除 Gatekeeper 中已迁移的实例变量
  - _task_manager, _worker_factory, _reviewer, _max_depth, _worker_model

步骤 3：实现 _formulate_directive()
  - 轻量 LLM 调用：用户目标 → Directive（方向 + 约束）
  - 或先做纯模板模式（零 LLM），后续迭代加 LLM

步骤 4：实现 _report_to_user()
  - ExecutionReport → 用户友好的中文汇报（遵循 user-gatekeeper-protocol.md）
```

### Phase 3：Planner 增强（可选，后续）

```
- Planner 支持多个 Directive 队列（并行 Planner）
- Planner._plan() 用 LLM 根据 direction 和 constraints 做更智能的分解
- Planner._summarize() 用 LLM 做语义汇总（目前可纯逻辑聚合）
- Console 增加 [参谋] 标签
```

---

## 11. 边界情况与风险

### 11.1 Planner 的 LLM 调用失败

和当前 Gatekeeper 一样：降级策略。

- `_plan()` 失败 → 尝试用模板分解（基于 directive.direction 的关键词拆任务），全失败则 ExecutionReport(status="failed")
- `_summarize()` 失败 → 纯逻辑聚合（不调 LLM）

### 11.2 Directive 不清晰

如果 Planner 发现 Directive 信息不足（direction 为空、constraints 矛盾）：
- 不猜测——通过 ExecutionReport.escalations 上报："作战指令不清晰，需要明确 X"
- Gatekeeper 向用户请求澄清

### 11.3 矛盾检测

当前 Gatekeeper 有矛盾检测（`_detect_contradictions`）。迁移到 Planner 的 `_check_escalations()`：
- 两个 Worker 产出矛盾 → 列入 escalations → Gatekeeper 决定：手动裁决 or 分派第三个 Worker 裁决
- 不自动裁决——那是 Gatekeeper 的战略判断

### 11.4 性能

- Planner 引入不会增加 LLM 调用次数——原来 Gatekeeper 的 `_decompose` 和 `_dispatch_with_review` 中的 LLM 调用一个不少
- 多了一次 Planner.execute() 的方法调用开销——可忽略
- 如果 Gatekeeper._formulate_directive() 用轻量 LLM，总 token 消耗**减少**（原来 _decompose 输出 ~1000 tokens JSON，现在 Directive ~200 tokens）

### 11.5 测试影响

- `tests/test_integration.py` 等集成测试可能需要微调（Gatekeeper 的接口变了）
- 可以新增 `tests/test_planner.py` 单独测试 Planner
- Worker、Reviewer、TaskManager 的单元测试完全不受影响

---

## 附录 A：瘦身前后的代码量对比估算

| 文件 | 当前行数 | 瘦身后 | 新增 |
|------|:------:|:-----:|------|
| `core/gatekeeper.py` | 742 | ~350 | — |
| `core/planner.py` | — | — | ~400（新文件） |
| `core/protocol.py` | 162 | ~200 | + Directive, ExecutionReport |
| `core/session.py` | 72 | ~80 | 微调 |
| `main.py` | 212 | ~240 | 连线 Planner |
| **总计** | ~1,188 | ~1,270 | +82 行（但职责清晰得多） |

代码量略增，但：
- 每个类的职责减半
- 测试可以独立（Planner 可 mock）
- Gatekeeper 的上下文压力大幅降低
- 扩展性显著提升

---

*本文档由 Planner Role Design Task 生成，基于 Janus Phase 1-5 完整代码分析和六种人类社会组织的分层模式研究。*
