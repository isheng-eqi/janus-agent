# Janus Agent Ecology — 自组织 Worker 生态架构设计

> **日期**：2026-07-16  
> **状态**：设计草案  
> **前置阅读**：`docs/janus-full-summary.md`（Janus Phase 4 现状）、`docs/design-philosophy.md`（9 条人类管理原则）  
> **核心问题**：如何让 Worker 不只是递归调用自己，而是**孵化独立的子 Worker**，形成自组织层级生态？

---

## 目录

- [0. 动机：当前 Worker 自分解的局限](#0-动机当前-worker-自分解的局限)
- [1. 设计问题总览](#1-设计问题总览)
- [2. 核心设计决策](#2-核心设计决策)
  - [2.1 Worker 孵化机制](#21-worker-孵化机制)
  - [2.2 子 Worker 身份系统](#22-子-worker-身份系统)
  - [2.3 有界自主权](#23-有界自主权)
  - [2.4 审查链](#24-审查链)
  - [2.5 深度/预算控制](#25-深度预算控制)
  - [2.6 父子通信](#26-父子通信)
  - [2.7 结果聚合](#27-结果聚合)
- [3. 新协议层](#3-新协议层)
- [4. 实现范围与路线图](#4-实现范围与路线图)
- [5. 权衡记录](#5-权衡记录)
- [6. 附录：与当前架构的 diff 对比](#6-附录与当前架构的-diff-对比)

---

## 0. 动机：当前 Worker 自分解的局限

### 当前行为（`worker.py:259-420`）

```python
def run(self, spec: TaskSpec) -> TaskResult:
    result = self._execute_loop(spec)          # 第一次尝试
    if result.status == NEEDS_DECOMPOSITION:
        for sub in result.decomposition_request.sub_tasks:
            sub_spec = TaskSpec(...)
            sub_result = self.run(sub_spec)     # ⚠️ 递归调用自己
        ...  # resume
```

### 三个结构性局限

| 局限 | 表现 | 根本原因 |
|------|------|----------|
| **身份同质** | 所有子任务由同一个 Worker 实例执行 | `self.run(sub_spec)` 是递归自调用，不是独立孵化 |
| **工具集同质** | 子 Worker 和父 Worker 共享完全相同的 ToolRegistry | 当前只注册全局唯一的 registry |
| **无层级自治** | 子 Worker 完全不知道自己在第几层、有多少兄弟姐妹 | depth 字段存在但只用于硬限制，不用于行为调整 |

### 人类管理的类比

一个团队负责人（Worker）遇到复杂任务时，不是"自己做一部分 → 自己再做一部分"，而是**招人**：找有不同技能的同事，分配子任务，授予权限，定好验收标准，让他们独立执行。

Janus Agent Ecology 要建模的正是这个"招人→授权→协调→收结果"的周期。

---

## 1. 设计问题总览

| # | 设计问题 | 当前状态 | 目标 |
|---|---------|---------|------|
| 1 | Worker 孵化 | 递归自调用 `self.run()` | 通过工厂孵化**独立实例** |
| 2 | 身份系统 | 无独立身份（仅 `worker-{i}` 标签） | 独立 role + model + toolset |
| 3 | 自主权 | 子 Worker 可自分解但无决策权 | 有界自主——可独立拆解但受层级约束 |
| 4 | 审查链 | 仅 Planner 层有统一的 Reviewer | 可选每层 Reviewer / 共享 Reviewer / 跳过 |
| 5 | 深度/预算 | `max_depth=3` 单一限制 | 多层次约束：深度 + Token 预算 + 时间 + 并发数 |
| 6 | 父子通信 | 仅 fire-and-collect（递归返回） | 可选 mid-execution 通信 + 信号机制 |
| 7 | 结果聚合 | 原始传递到 resume 上下文 | 层级压缩 + 增值解释 + 置信度加权 |

---

## 2. 核心设计决策

### 2.1 Worker 孵化机制

**决策：Factory Pattern + 策略覆写**

不走全局单例 Worker 工厂，而是每个 Worker 携带自己的 **`SubWorkerFactory`**——一个可配置的孵化器，决定子 Worker 的模型、工具集、权限边界。

```
┌─────────────────────────────────┐
│  Parent Worker                  │
│  ├─ sub_factory: SubWorkerFactory│
│  │    ├─ default_model: str     │
│  │    ├─ tool_filter: list[str] │  ← 子 Worker 可用工具白名单
│  │    ├─ max_depth: int         │  ← 传递并递减
│  │    └─ spawn(): Worker        │
│  │                              │
│  ├─ _spawn_sub_workers()        │
│  │     for sub in decomposition: │
│  │         child = factory.spawn(sub.role, sub.capabilities)│
│  │         result = child.run(sub_spec)│
│  └─ ...                         │
└─────────────────────────────────┘
```

**`SubWorkerFactory` 接口：**

```python
@dataclass
class SubWorkerConfig:
    """子 Worker 孵化配置——父 Worker 决定子的能力边界。"""
    role: str                          # 角色标签，如 "code-reviewer"、"researcher"
    model: str | None = None           # 模型覆写；None = 继承父 Worker 模型
    tool_allowlist: list[str] | None = None  # 工具白名单；None = 继承全部
    tool_denylist: list[str] | None = None   # 工具黑名单（不能和 allowlist 同时用）
    max_tool_calls: int | None = None        # None = 继承全局配置
    reviewer: Reviewer | None = None         # 子 Worker 专属 Reviewer；None = 无审查
    autonomy_level: AutonomyLevel = AutonomyLevel.MEDIUM

class SubWorkerFactory:
    """孵化独立子 Worker，根据角色配置分配不同的模型和工具集。"""

    def __init__(self, base_registry: ToolRegistry, api_key: str):
        self._base_registry = base_registry
        self._api_key = api_key

    def spawn(self, config: SubWorkerConfig) -> Worker:
        """根据配置创建一个全新的 Worker 实例。"""
        model = config.model or self._default_model
        # 构建子 Worker 的工具注册表——可能比父 Worker 更窄
        child_registry = self._build_child_registry(config)
        child = Worker(
            model=model,
            api_key=self._api_key,
            registry=child_registry,
            max_tool_calls=config.max_tool_calls or self._default_max_tool_calls,
            max_depth=config.max_depth,
            reviewer=config.reviewer,
            parent_id=self._parent_id,          # ← 新增：知道自己是谁的"孩子"
            autonomy_level=config.autonomy_level,
        )
        return child
```

**关键问题：预定义角色 vs 动态角色？**

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **预定义角色** | 预先注册 "researcher"、"coder"、"reviewer" 等角色模板 | 稳定、可测试、工具集预配置 | 灵活性低 |
| **动态角色** | LLM 在分解时动态指定子 Worker 需要的工具和模型 | 灵活、适应未知场景 | 引入可靠性风险 |
| **混合（推荐）** | 预定义角色为默认 + LLM 可覆写具体字段 | 最佳平衡 | 实现复杂度稍高 |

**推荐：混合模式。** Planner/Worker 在分解时可以为每个子任务指定 `SubWorkerConfig.role`。如果角色名命中预定义模板，使用模板的默认值；LLM 可以在 `capabilities` 字段中覆写 `model`、`tool_allowlist`、`autonomy_level`。

**权衡：**
- ✅ 独立孵化意味着子 Worker 的失败不污染父 Worker 的消息上下文——这是比递归自调用最大的改进
- ❌ 每次孵化创建一个新的 OpenAI client 实例和消息列表，增加实例化开销（可接受——相对于 LLM API 调用延迟来说微不足道）
- ❌ 父 Worker 的 conversation context 不能直接传递给子 Worker——子 Worker 只能收到 scoped context（这是特性而非 bug，符合信息筛选原则）

---

### 2.2 子 Worker 身份系统

**决策：层级 ID + 角色标签 + 可选模型异构**

每个子 Worker 获得一个**层级化身份**，体现在以下三个维度：

#### 2.2.1 层级 ID（Lineage ID）

```
root:  worker-0
  ├── worker-0.sub-1       # 父=worker-0，子任务 "research"
  │    ├── worker-0.sub-1.sub-a    # 孙 Worker
  │    └── worker-0.sub-1.sub-b
  └── worker-0.sub-2       # 父=worker-0，子任务 "implementation"
       └── worker-0.sub-2.sub-x
```

**`WorkerIdentity` 数据结构：**

```python
@dataclass
class WorkerIdentity:
    """Worker 在生态中的唯一身份标识。"""
    lineage_id: str          # 层级 ID，如 "worker-0.sub-1.sub-a"
    role: str                # 角色标签，如 "researcher", "coder"
    parent_id: str           # 直接父 Worker 的 lineage_id（root 为 None）
    depth: int               # 在树中的深度（root=0）
    generation: int          # 第几代孵化（每次 spawn() 递增）
    siblings_count: int      # 有多少兄弟姐妹（同父同层的其他子 Worker）
    sibling_index: int       # 在兄弟姐妹中的序号
```

#### 2.2.2 工具集差异化

不是所有 Worker 都应该有 `write_file` 权限。子 Worker 的工具集由父 Worker 通过 `SubWorkerConfig.tool_allowlist` 控制：

| 角色 | 典型工具集 | 说明 |
|------|-----------|------|
| `researcher` | `web_search`, `read_file` | 只读——收集信息 |
| `coder` | `read_file`, `write_file`, `execute_command` | 读写——生成代码 |
| `reviewer-sub` | `read_file` | 只读——核查产出 |
| `coordinator` | 无工具 | 类似 Planner——只协调子 Worker，不执行 |

**重要约束：子 Worker 的工具集必须是父 Worker 工具集的子集（不能越权）。**

#### 2.2.3 模型异构

子 Worker 可以使用不同于父 Worker 的模型：

```
Gatekeeper:  deepseek-v4-pro   (重型推理)
  └─ Planner: deepseek-v4-flash (战术规划)
       └─ Worker-0: deepseek-v4-flash (执行)
            ├─ Sub-Worker "research":  deepseek-v4-flash (快速搜索)
            ├─ Sub-Worker "code-gen":  deepseek-v4-pro   (重型——写复杂逻辑)
            └─ Sub-Worker "doc":       deepseek-v4-flash (写文档)
```

**权衡：**
- ✅ 模型异构 = 成本优化——简单任务用便宜模型，复杂任务用强模型
- ✅ 工具集差异化 = 安全边界——research Worker 不能意外写文件
- ❌ 增加配置复杂度——需要在 config.yaml 或角色模板中维护模型映射
- ❌ 跨模型的消息格式兼容性——DeepSeek 的 `reasoning_content` 只在 thinking 模式下有，需在 Worker 间传递时标准化

---

### 2.3 有界自主权

**决策：AutonomyLevel 枚举 + 硬边界**

子 Worker 的自主权不是二元的（有/无），而是分级的：

```python
class AutonomyLevel(Enum):
    """子 Worker 的自主权级别。

    来自军事"交战规则"（Rules of Engagement）——不同层级的部队有不同的
    决策自由度。
    """
    NONE = "none"
    """零自主——严格按照父 Worker 的指令执行，不可自分解，不可孵化子 Worker。
    对应：minion / leaf node / 纯执行者。"""

    LIMITED = "limited"
    """有限自主——可调用工具做出战术级决策（如选哪个库、用哪个 API），但不可
    自分解。遇到超出范围的问题必须上报父 Worker。
    对应：有经验的执行者。"""

    MEDIUM = "medium"
    """中等自主——可自分解（一层），但不可孵化子 Worker。分解后仍需 resume
    回自身完成任务。
    对应：当前 Janus 的 self-decomposition 行为。这是默认值。"""

    HIGH = "high"
    """高度自主——可自分解并可孵化独立子 Worker。子 Worker 可以是不同模型、
    不同工具集。但受 max_depth 和 budget 约束。
    对应：团队负责人——可招人、分工、协调。"""

    FULL = "full"
    """完全自主——等同于一个顶层的 Planner。可任意分解、孵化、协调。
    仅 Gatekeeper 直接授权的特殊 Worker 可获此级别。
    对应：独立项目负责人。"""
```

**自主权的硬边界：**

无论 `autonomy_level` 是多少，以下边界不可逾越：

1. **工具集下界**：子 Worker 的工具集必须是父 Worker 工具集的子集
2. **深度上限**：任何 Worker 的 `depth` 不能超过全局 `max_depth`
3. **Token 预算**：整个层级树共享一个 Token 预算（见 §2.5）
4. **禁止回环**：子 Worker 不能孵化其祖先（防止循环引用）
5. **最终责任**：父 Worker 对其孵化的所有子 Worker 的结果负最终责任

**父 Worker 设置子 Worker 自主权的考量因素：**

| 因素 | 低自主 (NONE/LIMITED) | 高自主 (HIGH/FULL) |
|------|----------------------|---------------------|
| 任务明确性 | 验收标准极其明确 | 验收标准模糊，需要探索 |
| 风险等级 | 涉及敏感操作、安全性 | 低风险，可逆操作 |
| 父 Worker 对子领域的知识 | 父 Worker 是该领域专家 | 父 Worker 不熟悉该子领域 |
| 层级位置 | 深层（depth ≥ max_depth - 1） | 浅层（depth ≤ 1） |

**权衡：**
- ✅ 分级自主比二元开关更精准——避免"要么完全不管，要么管太死"
- ✅ `NONE` 模式下的子 Worker 就是纯 leaf node——不能分解，只能执行——这是最安全的模式
- ❌ `FULL` 自主可能导致"递归爆炸"——需要配合 budget 控制
- ❌ 父 Worker（LLM）可能给出不合理的 autonomy_level——需要 Reviewer 或规则层做安全检查

---

### 2.4 审查链

**核心问题：Planner→Worker→Reviewer 的审查链在多层孵化下如何扩展？**

有三种可行模式，各有适用场景：

#### 模式 A：共享 Reviewer（当前模式，推荐为默认）

```
                    ┌──────────┐
                    │ Reviewer │  ← 全局共享一个 Reviewer 实例
                    └────┬─────┘
           ┌─────────────┼─────────────┐
           ▼             ▼             ▼
       Worker-0     Worker-1     Worker-2
         │
    ┌────┴────┐
    ▼         ▼
  Sub-0.1   Sub-0.2      ← 子 Worker 产出也送同一个 Reviewer
```

**适用**：同构任务（所有 Worker 做相似的事，验收标准一致）。

**优点**：简单、Token 消耗可控、审查标准一致。  
**缺点**：Reviewer 成为瓶颈（所有 Worker 串行等审查）。

#### 模式 B：每层独立 Reviewer

```
                    ┌──────────┐
                    │ Reviewer │  ← Planner 层 Reviewer（审 Worker-0..N 的整体产出）
                    └────┬─────┘
           ┌─────────────┼─────────────┐
           ▼             ▼             ▼
       Worker-0     Worker-1     Worker-2
         │ (有自己的 Reviewer_R1)
    ┌────┴────┐
    ▼         ▼
  Sub-0.1   Sub-0.2     ← 每个有独立的 Reviewer_R2
  (Reviewer_R2)  (Reviewer_R2)
```

**适用**：异构任务（子 Worker 做完全不同的事，验收标准差异大）。

**优点**：每层审查关注自己层级的正确性，不跨层混乱。  
**缺点**：Token 消耗剧增；多个 Reviewer 可能给出矛盾判断。

#### 模式 C：可选跳过

```
Worker-0 (autonomy=HIGH)
  │
  ├── Sub-0.1 (autonomy=LIMITED) → 产出 → 跳过审查（父 Worker 信任）
  └── Sub-0.2 (autonomy=MEDIUM)  → 产出 → 送 Reviewer
```

**适用**：父 Worker 对特定子 Worker 有足够信心时跳过审查。

**优点**：节省 Token、加速流程。  
**缺点**：审查缺失可能导致质量问题在后续环节爆发。

#### 推荐策略

```
默认：模式 A（共享 Reviewer）
│
├── 当 Worker.autonomy_level >= HIGH 时，该 Worker 获得一个独立的 Reviewer
│   → 过渡到模式 B 的局部变体
│
├── 当 SubWorkerConfig.reviewer 显式设为 None 时
│   → 跳过该子 Worker 的审查（模式 C），但在聚合结果时标记为"未经审查"
│
└── 门卫层的硬规则：
    → 任何 depth >= max_depth 的 Worker 产出 **必须** 经过审查（无论 autonomy_level）
    → 防止深层 Worker 的错误在无审查的情况下被逐级放大
```

**权衡：**
- ✅ 默认共享 Reviewer = 向后兼容，零破坏性变更
- ✅ 高自主 Worker 独立 Reviewer = 符合"权力越大，审查越严"的管理原则
- ❌ 多 Reviewer 模式增加 API 调用次数，需要更精细的预算控制
- ❌ "跳过审查"的风险需要父 Worker 承担——如果父 Worker 判断失误，问题会传导到更上层

---

### 2.5 深度/预算控制

**决策：四维约束矩阵**

单一 `max_depth=3` 不足以控制复杂的层级生态。引入四维约束：

#### 2.5.1 约束维度

```python
@dataclass
class EcologyBudget:
    """整个 Agent 生态的资源约束。

    这是硬约束——达到任何一个上限都会触发降级或终止。
    """
    # 结构约束
    max_depth: int = 3
    """最大孵化深度。root Worker 的 depth=0，每 spawn() 一次 depth+1。"""

    max_children_per_worker: int = 5
    """一个 Worker 最多孵化的直接子 Worker 数。超过则需上报父层协调。"""

    max_total_workers: int = 20
    """整个生态树中同时存在的 Worker 最大数量。"""

    # 资源约束
    total_token_budget: int | None = None
    """整个任务链的总 Token 预算（输入+输出+审查）。None=不限制。
    由 Planner 从 Directive 的 task_complexity 估算初始值。"""

    per_worker_token_budget: int = 50000
    """单个 Worker（包括其子 Worker 树）的 Token 预算上限。"""

    # 时间约束
    per_worker_timeout_seconds: int = 600
    """单个 Worker 的最大执行时间（秒）。超时 = 强制终止 + 上报 FAILURE。"""

    total_timeout_seconds: int | None = None
    """整个任务的全局超时。None=不限制。"""

    # 并发约束
    max_concurrent_workers: int = 3
    """同时运行的 Worker 最大数量。超过的排队等待。"""
```

#### 2.5.2 预算消耗追踪

当父 Worker 孵化子 Worker 时，需要**分配预算**：

```python
class BudgetTracker:
    """追踪整个生态树的资源消耗，在每个孵化点校验预算。"""

    def can_spawn(self, parent_id: str, requested_budget: int = 0) -> bool:
        """检查当前是否允许孵化新的子 Worker。

        校验项：
        1. 全局 total_workers 是否已达上限
        2. 父 Worker 的 children 数量是否已达 per_worker 上限
        3. 剩余 Token 预算是否足够
        4. 是否已超全局超时
        """

    def allocate(self, worker_id: str, budget_share: int) -> None:
        """为子 Worker 分配预算份额。"""

    def report_consumption(self, worker_id: str, tokens_used: int) -> None:
        """子 Worker 完成后报告实际 Token 消耗。"""

    @property
    def remaining_budget(self) -> int:
        """剩余全局 Token 预算。"""
```

#### 2.5.3 预算耗尽时的降级策略

```
预算类型 耗尽 → 降级策略
─────────────────────────────────────────────────
max_depth 耗尽 → NONE/LIMITED autonomy 强制，不可自分解
max_children 耗尽 → 合并子任务到已有 Worker 或上报父层
max_total_workers 耗尽 → 进入队列等待，FIFO 调度
Token 预算耗尽 → 所有 Worker 收到 "budget_exhausted" 信号，只做收尾不展开
Timeout 耗尽 → 强制 CACNEL 所有运行中的 Worker，聚合已完成的子结果
```

**权衡：**
- ✅ 多维度约束比单一 `max_depth` 精确得多——防止 TaskToken 黑洞的同时允许合理的深层分解
- ✅ `max_children_per_worker` 对应管理学的"管理幅度"原则（§2.9）
- ❌ `total_token_budget` 难以精确预估——LLM 的 Token 消耗波动大
- ❌ 预算追踪本身有开销——`BudgetTracker` 需要在每次工具调用后更新计数

---

### 2.6 父子通信

**决策：Fire-and-Collect 为默认 + 可选 Signal 通道**

#### 2.6.1 默认模式：Fire-and-Collect

父 Worker 孵化子 Worker → 等待子 Worker 完成 → 收集结果。这是当前递归模式的自然扩展。

```
Parent: spawn(child_config) → child.run(sub_spec) → TaskResult
                                                      ↑
                                            (阻塞等待，不通信)
```

**优点**：简单、可预测、符合当前 Janus 语义。  
**缺点**：子 Worker 遇到困难时无法求助；父 Worker 发现问题时无法中途纠正。

#### 2.6.2 可选模式：Signal 通道

为父子 Worker 之间增加一个轻量级的异步信号通道：

```python
class SignalType(Enum):
    """父→子 或 子→父 的异步信号类型。"""
    # 子 → 父
    NEED_HELP = "need_help"            # 子 Worker 遇到超出权限的问题
    PROGRESS_UPDATE = "progress"       # 定期进度报告
    BUDGET_WARNING = "budget_warning"  # 子 Worker token 预算即将耗尽
    # 父 → 子
    REPRIORITIZE = "reprioritize"      # 父 Worker 要求子 Worker 调整优先级
    CANCEL = "cancel"                  # 强制终止
    EXTEND_BUDGET = "extend_budget"    # 追加预算
    NEW_CONSTRAINT = "new_constraint"  # 父 Worker 发现新情况，追加约束

@dataclass
class WorkerSignal:
    """Worker 间异步信号。"""
    signal_type: SignalType
    sender_id: str
    target_id: str
    payload: str
    timestamp: float
```

**实现方式：**

父 Worker 在调用 `child.run()` 时可以选择性地传入一个 `SignalChannel`：

```python
# 父 Worker 侧
channel = SignalChannel(parent_id="worker-0", child_id="worker-0.sub-1")
child_result = await child.run_async(sub_spec, signal_channel=channel)

# 在等待期间，父 Worker 可以轮询信号：
while child.is_running():
    signal = channel.poll_from_child()      # 非阻塞
    if signal and signal.type == NEED_HELP:
        channel.send_to_child(EXTEND_BUDGET, "追加 20000 tokens")
```

**子 Worker 侧**，`_execute_loop` 中增加信号检查点：

```python
def _execute_loop(self, spec, signal_channel=None):
    while tool_call_count < max_tool_calls:
        # 在每次工具调用前检查是否有来自父的信号
        if signal_channel:
            signal = signal_channel.poll_from_parent()
            if signal and signal.type == CANCEL:
                return TaskResult(status=FAILURE, summary="Cancelled by parent.")
            if signal and signal.type == REPRIORITIZE:
                # 注入到系统提示或下一个 user message
                ...
        # 正常执行逻辑...
```

**权衡：**
- ✅ Signal 通道给了父子 Worker 在意外情况下的协调能力——类比人类团队的 Slack 消息
- ✅ 非阻塞轮询，不打断 LLM 推理流程
- ❌ 实现复杂度显著增加——需要 asyncio 或线程支持
- ❌ Signal 的语义需要在 Worker 的 system prompt 中定义——不是所有 LLM 都能正确响应
- **建议**：Phase 1 只实现 Fire-and-Collect。Signal 通道留给后续迭代。

---

### 2.7 结果聚合

**决策：逐级压缩 + 置信度加权 + 可追溯性**

当前 Worker 的递归自分解中，子结果通过 `_format_sub_results()` 原样拼接到 resume 上下文。这在单层分解中足够，但在多层生态中会导致两个问题：

1. **信息膨胀**：三层 10 个子 Worker 的原始结果全部塞进 resume 上下文 → 超过 Token 窗口
2. **无增值解释**：原始结果没有"这表示什么 / 可靠性如何"的元信息

#### 2.7.1 聚合管道

```
Sub-0.1 结果 ─┐
Sub-0.2 结果 ─┤
Sub-0.3 结果 ─┤ → Worker-0 聚合层 → 压缩摘要 + 置信度矩阵 → resume 上下文
Sub-0.4 结果 ─┘
```

**父 Worker 聚合时执行：**

1. **去重**：多个子 Worker 产出相似内容时合并
2. **置信度加权**：低置信度的子结果在聚合中权重降低
3. **审查结果标记**：未经审查 / 审查通过 / 审查失败的子结果明确标注
4. **生成摘要**：LLM 生成一句话总结，取代"全部原始结果拼接"

```python
@dataclass
class AggregatedSubResult:
    """父 Worker 聚合后的子结果——压缩 + 增值。"""
    summary: str                          # LLM 生成的一句话摘要
    key_findings: list[str]               # 关键发现（最多 5 条）
    confidence_distribution: dict[str, int]  # 置信度分布：{"high": 3, "medium": 1}
    review_coverage: float                # 审查覆盖率 0.0-1.0
    artifacts_by_worker: dict[str, list[str]]  # 按 Worker 分组的产出文件
    raw_results: list[TaskResult]         # 保留原始引用用于审查追溯
    warnings: list[str]                   # 聚合过程中发现的风险信号
```

#### 2.7.2 向上传递策略

```
深度 N+2 (孙 Worker) → 原始 TaskResult
    ↓ 孙→子的聚合：保留关键发现 + 置信度 + 审查状态
深度 N+1 (子 Worker) → AggregatedSubResult + 自身产出
    ↓ 子→父的聚合：合并同层结果 + 产生更高级别摘要
深度 N   (父 Worker) → "子模块总体完成度 80%，2 个风险项" → resume 上下文
```

每一层的聚合都是 **有损压缩 + 增值解释**——丢掉实现细节但增加判断。

**权衡：**
- ✅ 逐级压缩 = 防止信息膨胀，确保 resume 上下文不超 Token 窗口
- ✅ 置信度加权 = 低质量结果不会拉低聚合判断
- ❌ 有损压缩可能丢失关键细节——需要 `raw_results` 保留引用供回溯
- ❌ LLM 驱动的摘要可能产生幻觉——特别是对技术细节的总结

---

## 3. 新协议层

当前 `protocol.py` 主要建模单层 TaskSpec→TaskResult 通信。Agent Ecology 需要新增以下协议：

```python
@dataclass
class EcologyConfig:
    """全局生态配置——从 config.yaml 加载。"""
    max_depth: int = 3
    max_children_per_worker: int = 5
    max_total_workers: int = 20
    total_token_budget: int | None = None
    per_worker_token_budget: int = 50000
    per_worker_timeout_seconds: int = 600
    max_concurrent_workers: int = 3
    default_autonomy: AutonomyLevel = AutonomyLevel.MEDIUM
    role_templates: dict[str, SubWorkerConfig] = field(default_factory=dict)


@dataclass
class WorkerManifest:
    """Worker 的"身份证"——在生态树中的完整身份信息。"""
    identity: WorkerIdentity
    config: SubWorkerConfig
    spawned_at: float
    status: WorkerStatus             # IDLE | RUNNING | COMPLETED | FAILED | CANCELLED


@dataclass
class EcologyReport:
    """整个生态执行完成后，Planner 层面的聚合报告。

    这是 ExecutionReport 的扩展——在多层孵化下，除了任务级别的通过/失败，
    还需要生态级别的结构分析。
    """
    # 继承现有字段
    status: str                      # "completed" | "partial" | "failed"
    total_tasks: int
    passed: int
    failed: int
    summary: str
    details: list[str]
    goal: str

    # 新增生态字段
    tree_depth_reached: int          # 实际到达的最大深度
    total_workers_spawned: int       # 总共孵化了多少 Worker
    total_tokens_consumed: int       # 总 Token 消耗
    budget_breaches: list[str]       # 预算违规记录
    autonomy_distribution: dict[str, int]  # 各自主级别的 Worker 数量
    collapsed_paths: list[str]       # 因预算/深度限制被截断的路径
    ecology_warnings: list[str]      # 生态级别的风险提示
```

---

## 4. 实现范围与路线图

### Phase 0：基础重构（当前 → 孵化就绪）

| 变更 | 文件 | 说明 |
|------|------|------|
| 引入 `WorkerIdentity` | `core/protocol.py` | 层级 ID + 角色标签 |
| 引入 `SubWorkerConfig` | `core/protocol.py` | 孵化配置 |
| 引入 `AutonomyLevel` | `core/protocol.py` | 自主权枚举 |
| 引入 `EcologyBudget` | `core/protocol.py` | 资源约束 |
| 引入 `EcologyConfig` | `core/config.py`（新建）或扩展现有 config | 从 YAML 加载 |

### Phase 1：独立孵化（核心变更）

| 变更 | 文件 | 说明 |
|------|------|------|
| `SubWorkerFactory` | `core/worker.py` | 孵化逻辑——创建独立 Worker 实例 |
| `Worker._spawn_and_collect()` | `core/worker.py` | 替代当前 `self.run(sub_spec)` 递归调用 |
| `Worker.spawned_children` 追踪 | `core/worker.py` | 父 Worker 维护子 Worker 列表 |
| `ToolRegistry.filter()` | `core/worker.py` | 支持按 allowlist/denylist 裁剪工具集 |
| `EcologyBudget` 加载 | `main.py` | 从 config.yaml 读取生态约束 |
| `BudgetTracker` 基础版 | `core/budget.py`（新建） | 追踪深度 + Worker 数量 |

**Phase 1 不做的：**
- ❌ Signal 通道（留 Phase 2）
- ❌ 多 Reviewer 模式（留 Phase 2）
- ❌ 并发 Worker 执行（留 Phase 3）
- ❌ Token 预算精确追踪（留 Phase 2）
- ❌ `AggregatedSubResult` 逐级压缩（留 Phase 2）

### Phase 2：通信与聚合

| 变更 | 说明 |
|------|------|
| `WorkerSignal` + `SignalChannel` | 父子异步通信 |
| `AggregatedSubResult` + 聚合管道 | 逐级压缩 + 置信度加权 |
| 多 Reviewer 模式 | 高自主 Worker 独立 Reviewer |
| `BudgetTracker` 完整版 | Token 计数 + 超时控制 |

### Phase 3：并发与调度

| 变更 | 说明 |
|------|------|
| 并发 Worker 执行 | `asyncio.gather()` 并行启动同层子 Worker |
| Worker 队列调度 | 当 `max_concurrent_workers` 达到上限时排队 |
| 生态可视化 | Console 输出层级树 |

---

## 5. 权衡记录

| # | 决策 | 选择 | 替代方案 | 为什么选择这个 |
|---|------|------|---------|---------------|
| 1 | 孵化模式 | Factory Pattern | 全局单例工厂 / 原型克隆 | Factory 最灵活——每个 Worker 可定制子 Worker 配置 |
| 2 | 工具集策略 | 白名单为主 | 黑名单 / 全部继承 | 白名单 = 默认最小权限，安全原则 |
| 3 | 角色系统 | 混合（预定义 + LLM 覆写） | 纯预定义 / 纯动态 | 兼顾可靠性和灵活性 |
| 4 | 自主权 | 五级 AutonomyLevel | 二元（可/不可） / 三级 | 五级映射到实际管理场景最自然 |
| 5 | 审查链 | 默认共享 + 可选独立 | 始终独立 / 始终共享 | 简单场景不浪费 Token，复杂场景可以加 |
| 6 | 预算控制 | 四维（深度+子数+Token+时间） | 仅深度 / 仅 Token | 单一维度都有盲区，四维覆盖更全面 |
| 7 | 父子通信 | Fire-and-Collect 默认 | 始终双向 / WebSocket 实时 | 默认简单；预留 Signal 通道给未来 |
| 8 | 结果聚合 | 逐级压缩 + 置信度加权 | 原始透传 / LLM 全量总结 | 防止信息膨胀同时保留关键信号 |
| 9 | 信息传递 | scoped context（不传完整历史） | 完整历史透传 / 零上下文 | 对应军事"need-to-know"原则 |

---

## 6. 附录：与当前架构的 diff 对比

### 当前 Worker.run() 的自分解路径

```
Worker.run(spec)
  └─ _execute_loop(spec)
       └─ 如果 NEEDS_DECOMPOSITION:
            ├─ for sub in sub_tasks:
            │    sub_spec = TaskSpec(depth=spec.depth+1)
            │    self.run(sub_spec)         ← 递归自调用，同一实例 + 同一工具集
            │    self._review_sub_result()  ← 可选审查
            ├─ _format_sub_results()        ← 原始拼接
            └─ resume → _execute_loop()     ← 同一实例继续
```

### 新 Worker.run() 的孵化路径

```
Worker.run(spec)
  └─ _execute_loop(spec)
       └─ 如果 NEEDS_DECOMPOSITION:
            ├─ 检查 EcologyBudget:
            │    ├─ depth >= max_depth? → 拒绝，返回 FAILURE
            │    ├─ 子 Worker 数 >= max_children? → 合并或上报
            │    └─ 预算充足？→ 继续
            │
            ├─ for sub in sub_tasks:
            │    config = SubWorkerConfig(
            │        role=sub.role,           ← LLM 指定的角色
            │        model=sub.model,         ← 可选模型覆写
            │        tool_allowlist=[...],    ← 父 Worker 裁剪的工具集
            │        autonomy_level=...       ← 父 Worker 决定的自主权
            │    )
            │    child = factory.spawn(config) ← 新实例！独立身份+独立上下文
            │    child_result = child.run(sub_spec)
            │    child_result 可选送 Reviewer
            │
            ├─ _aggregate_child_results()    ← 压缩 + 加权
            └─ resume → _execute_loop()      ← 只看到聚合摘要
```

### 关键差异总结

| 维度 | 当前 (self-call) | 新设计 (spawn) |
|------|-----------------|---------------|
| Worker 实例 | 同一个 `self` | 全新的 `Worker` 对象 |
| 工具集 | 完全相同 | 可裁剪（白名单/黑名单） |
| 模型 | 完全相同 | 可异构（不同模型） |
| 上下文 | 子 Worker 隐式共享 LLM 消息历史 | 子 Worker 干净的消息列表，只收到 scoped context |
| 失败隔离 | 子递归失败污染父调用栈 | 独立实例——失败不影响父 Worker 状态 |
| 审查 | 共享 Reviewer | 可选每层独立 Reviewer |
| 预算控制 | 仅 `max_depth` | 四维约束矩阵 |
| 身份 | 无 | 层级 ID + 角色标签 |

---

> **设计原则回顾**（来自 `docs/design-philosophy.md` Step 4）：**先实现核心机制，看它是否真的过载，再拆分。**  
> Agent Ecology 的 Phase 1 只做孵化 + 身份 + 预算基础 + 工具裁剪——这就是最小可行生态。Phase 2/3 的通信和聚合可以等 Phase 1 用起来再根据实际瓶颈设计。
