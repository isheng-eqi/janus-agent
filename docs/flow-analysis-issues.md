# Janus 数据流分析 — 问题清单

> 综合自顶向下（Top-Down）和自底向上（Bottom-Up）分析，覆盖从用户输入到最终响应的完整信息流。

---

## 一、方法说明

**自顶向下分析**：从 `main.py` → `Session` → `Gatekeeper` → `Planner` → `Worker`，追踪每条信息在下行链路中的命运——哪些字段在哪个交接点被创建，哪些在传播中丢失。

**自底向上分析**：从 `Worker._execute_loop()` 向上回溯——Worker 产出的 `TaskResult` 经过 Review → Planner 汇总 → Gatekeeper 汇报 → 用户看到的最终输出。追踪上行链路中每层丢弃了什么。

---

## 二、信息流架构概览

```
用户原始输入 (str)
    │
    ▼
  Gatekeeper._formulate_directive()  →  Directive {goal, intent, constraints, priority}
    │
    ▼
  Planner._plan()  →  [TaskSpec {task_id, description, acceptance_criteria, context, intent, depth}]
    │
    ▼
  Worker._execute_loop()  →  TaskResult {status, summary, result, artifacts, confidence}
    │
    ▼
  Reviewer.review()  →  ReviewResult {verdict, summary, issues, evidence}
    │
    ▼
  Planner._summarize()  →  ExecutionReport {status, total_tasks, passed, failed, summary, details}
    │
    ▼
  Gatekeeper._report_to_user()  →  str (用户看到的最终输出)
```

---

## 三、问题清单

---

### 🔴 Critical — 信息永久丢失、用户被欺骗

#### C1. `Directive.goal` 丢失：Worker 看不到用户原始目标

- **位置**：`protocol.py:141-162`（TaskSpec 无 goal 字段）→ `planner.py:201-316`（`_plan` 不传播 goal）
- **症状**：用户说「写一个 Web 应用」，`Directive.goal` 保留原文。但 `TaskSpec` 只有 `description`（如「创建 Flask 入口文件」），Worker 无法感知原始目标的全貌。
- **后果**：Worker 在自分解或处理反常情况时失去上下文锚点——不知道自己做这件事是为了什么更大的目标。
- **证据**：`TaskSpec` 定义（protocol.py:141-162）共 6 个字段，无 `goal`。`Directive` 定义（protocol.py:170-188）有 `goal`，但在 `_plan()`（planner.py:295-306）构造 `TaskSpec` 时从未传入。
- **修复建议**：在 `TaskSpec` 中增加 `goal: str = ""` 字段。在 `planner.py:295-306` 构造时将 `directive.goal` 传入。

---

#### C2. `Directive.intent` → `TaskSpec.intent`：无代码级兜底，可为空

- **位置**：`planner.py:303`
- **症状**：
  ```python
  intent=str(item.get("intent", ""))   # 如果 Planner LLM 不输出 intent → 空字符串
  ```
  无代码级 fallback 到 `directive.intent`。
- **后果**：`Worker._build_system_prompt()`（worker.py:516-517）仅在 `spec.intent` 非空时追加 "This task is important because: …"。如果 intent 为空，Worker 在没有战略方向的情况下执行。更糟的是，子分解（worker.py:300）显式传递 `intent=spec.intent`——如果父级为空，所有子级也为空。
- **修复建议**：
  ```python
  intent=str(item.get("intent", "") or directive.intent or directive.goal)
  ```

---

#### C3. `Directive.constraints` 无程序化传播，Worker 看不到约束

- **位置**：`planner.py:208-212`（仅嵌入 Planner 的分解提示词）→ `TaskSpec` 无约束字段
- **症状**：约束（如「不能修改已有文件」「使用 Python 3.10+」）只出现在 Planner 的 LLM 提示词中，不进入 `TaskSpec`。依赖 Planner LLM 在 `description`/`acceptance_criteria` 中「烘焙」约束——不可靠。
- **后果**：Worker 执行时完全不知道硬约束，可能违反。子分解（worker.py:291-302）也无法传递约束。
- **修复建议**：
  1. 方案A（轻量）：在 Planner 构造 TaskSpec 时追加到 `context`：
     ```python
     context=f"[Constraints] {directive.constraints}\n\n{item.get('context','')}"
     ```
  2. 方案B（完整）：在 `TaskSpec` 中增加 `constraints: str = ""` 字段。

---

#### C4. 子 Worker 审查失败静默丢失：Gatekeeper 永远不知道子任务审查失败

- **位置**：`worker.py:306-309`（`_review_sub_result` 结果无信号）→ `worker.py:544-621`（审查重试用尽后静默返回失败结果）
- **症状**：
  1. Worker 自分解时调 `_review_sub_result()` 审查每个子任务。
  2. 如果审查重试用尽（`worker.py:620-621`），返回最后的失败 `TaskResult`。
  3. 调用方（`worker.py:306-309`）直接将结果 append 到 `sub_results`，不区分「审查失败」vs「执行失败」。
  4. 格式化后（`_format_sub_results`）被喂入 resume context，残留在上下文但不被标记为审查失败。
  5. Gatekeeper 只看到最终 `ExecutionReport`，完全不知道子任务经历了审查重试。
- **后果**：用户/Gatekeeper 以为任务正常完成，实际上子任务审查多次失败后静默接受了不合格结果。
- **修复建议**：`_review_sub_result` 返回时增加审查状态标记（如 `review_attempts_used` 或在 TaskResult 中携带审查元数据）；或至少日志记录审查重试次数。

---

#### C5. 用户看不到失败细节：`_report_to_user` 只显示计数

- **位置**：`gatekeeper.py:421-453`
- **症状**：`ExecutionReport` 包含 `details: list[str]`，`_report_to_user()` 确实输出 details（line 449-451），但格式只有 `[status] summary`。用户看不到：
  - 哪个任务失败了、为什么失败
  - 审查结果（Reviewer 的 evidence/issues）
  - 是否经过了重试
- **后果**：用户看到 `⚠️ 军师汇报：Completed: 2/3 tasks.` 但不知道第 3 个任务为什么失败，也无法做出决策（重试？改需求？）。
- **修复建议**：在 `_report_to_user` 中区分 failed tasks 并给出具体失败原因。当前的 `details` 虽然被输出，但格式信息密度低——建议失败任务的 details 包含失败原因摘要。

---

### 🟡 Major — 重要但不阻塞

#### M1. `Directive.priority` 对执行零影响

- **位置**：`gatekeeper.py:326-332`（Console 显示）→ 无下游消费
- **症状**：`priority` 可选值 `"speed" | "quality" | "balanced" | "normal"`，但仅用于 `console.think_block()` 显示。没有任何代码根据 priority 调整：
  - 审查重试次数（`max_retries=2` 硬编码在 `planner.py:186`）
  - Worker 的 `max_tool_calls`（始终 50）
  - 审查严格程度
- **修复建议**：
  - `"speed"` → `max_retries=0`，更快通过
  - `"quality"` → `max_retries=3`，更严格审查
  - 将 `priority` 传入 `Planner.execute()` 并路由到 `_dispatch_with_review`

---

#### M2. Session 不向 Gatekeeper 传递对话历史

- **位置**：`session.py:51`（`self._gk.handle(user_input)` 只传当前消息）、`session.py:54-55`（事后记录）
- **症状**：对话历史 `self._history` 被记录但从未被消费。Gatekeeper 的 `_decide()`、`_respond()`、`_formulate_directive()` 每次调用都是无状态的单条消息。
- **后果**：
  - 多轮对话中 Gatekeeper 无法感知上下文——「继续上一次的任务」这样的指令无法被解析。
  - Chat 模式下的 `_respond()` 也无法延续对话。
- **修复建议**：将 `self._history` 传递给 `Gatekeeper.handle()`，Gatekeeper 在 LLM 调用时将历史消息注入 messages 列表。

---

#### M3. `Planner.max_depth` 与 `Worker.MAX_WORKER_DEPTH` 不一致且无关联

- **位置**：`main.py:151`（`max_depth=3`）→ `planner.py:126`（`self._max_depth`）vs `worker.py:36`（`MAX_WORKER_DEPTH=3`）
- **症状**：
  - `Planner._max_depth` 可配置但代码中 `TaskSpec` 始终 `depth=1`（planner.py:304），从未递增——Planner 只用它做顶层规划，不做深度管理。
  - `Worker.MAX_WORKER_DEPTH` 是硬编码常量，控制自分解深度（worker.py:267）。
  - 两者数值巧合都是 3，但无任何代码关联。
- **后果**：用户配置了更大的 `max_depth` 期望更深分解，但 Worker 硬编码限制不会改变。
- **修复建议**：将 `max_depth` 传入 Worker 构造函数，替换硬编码常量。

---

#### M4. `MINOR_REVISIONS` 第二次尝试不做审查直接接受

- **位置**：`planner.py:374-400`
- **症状**：当审查结果为 `MINOR_REVISIONS`：
  1. `attempt==0`：重试（Worker 带上反馈重新执行）
  2. `attempt==1`：**不调 Reviewer**，直接 `mark_completed` 并返回结果
- **后果**：第二次尝试的输出完全没有被验证——Worker 可能修改引入了新 bug，也可能根本没修好原问题。
- **修复建议**：`attempt==1` 时至少做一次轻量审查（如只检查 issue severity 是否为 MINOR），或至少将 auto-accept 改为 `APPROVED_WITH_NOTES` 而不是静默接受。

---

#### M5. `_review_sub_result` 重试用尽后静默返回，无升级信号

- **位置**：`worker.py:620-621`
- **症状**：当子 Worker 的审查重试用尽（最多 3 次尝试全部失败），方法直接返回最后一次的 `result`。调用方无法区分「审查通过的结果」和「审查失败被强制接受的结果」。
- **对比**：`planner._dispatch_with_review` 在同等情况下返回显式的 `TaskResult(status=FAILURE, summary="Failed review after N retries")`（planner.py:424-431）——有明确的失败信号。
- **修复建议**：`_review_sub_result` 应返回包含审查元数据的结果，或在重试用尽时返回 FAILURE（与 Planner 一致）。

---

#### M6. Gatekeeper 有身份提示词（`_GATEKEEPER_IDENTITY`），Planner 没有

- **位置**：`gatekeeper.py:90-107` vs `planner.py:80-93`（`_CONTEXT_DISCIPLINE_PROMPT` + `_PLAN_SYSTEM_PROMPT`）
- **症状**：
  - Gatekeeper 的 `_GATEKEEPER_IDENTITY` 明确告知 LLM 它的角色（军师）、约束（零工具、只看战略级信息）、与用户和 Planner 的关系。
  - Planner 只有 `_CONTEXT_DISCIPLINE_PROMPT`（上下文纪律）+ `_PLAN_SYSTEM_PROMPT`（战术分解）。Planner LLM 不知道自己是「参谋」、不知道和 Gatekeeper/Worker 的关系，不知道自己的输出会被如何消费。
- **后果**：Planner LLM 可能在分解时过度细化（不知道 Worker 会自分解），或输出不必要的信息（不知道 Gatekeeper 只看汇总）。
- **修复建议**：为 Planner 增加 `_PLANNER_IDENTITY` 提示词，定义其角色（战术参谋）、约束、与上下游的关系。

---

#### M7. Reviewer 将 Worker FAILURE 标记为 APPROVED，语义误导

- **位置**：`reviewer.py:339-354`
- **症状**：当 Worker 返回 `FAILURE` 时，Reviewer 跳过审查并返回 `ReviewResult(verdict=APPROVED, summary="Worker reported failure — no audit needed.")`。
- **问题**：`APPROVED` 语义上意味着「产出物符合验收标准」，但这里实际是「不需要审查，让 Gatekeeper 处理」。调用方（`planner.py:356-359`）检查 `review.verdict in (APPROVED, APPROVED_WITH_NOTES)` 来判断 → Worker 返回 FAILURE 会被当作「审查通过」处理。
- **后果**：`planner._dispatch_with_review`（line 356-359）会把 FAILURE 结果当作审查通过直接 `mark_completed` 并返回。但实际上这个结果应该触发重试或失败处理——不过 Planner 的调用者检查的是 `result.status == FAILURE`（在 `_summarize` 中计数），所以业务逻辑上没问题，但语义不清晰。
- **修复建议**：
  1. 增加 `ReviewVerdict.SKIPPED` 枚举值
  2. 或在 Planner 层面先检查 `result.status` 再决定是否审查（而不是依赖 Reviewer 的 auto-pass）

---

#### M8. `_CONTEXT_DISCIPLINE_PROMPT` 在 Gatekeeper 和 Planner 中重复定义

- **位置**：`gatekeeper.py:73-80` 和 `planner.py:80-87`
- **症状**：相同的 7 行提示词在两个文件中逐字重复，只有角色称谓不同（"executive" vs "chief of staff"）。
- **后果**：修改需要两边同步，容易出现不一致。
- **修复建议**：提取到 `protocol.py` 或新建 `core/prompts.py`，作为共享常量。

---

### 🟢 Minor — 值得修复，不紧急

#### m1. 子任务 artifacts 在 resume context 中丢失

- **位置**：`worker.py:520-542`（`_format_sub_results`）
- **症状**：子任务结果格式化时包含 `status`、`summary`、`result`、`confidence`，但 `artifacts` 仅在有值时附加到最后一个 part 的末尾。且 resume context 不包含 artifacts 的结构化信息。
- **后果**：恢复执行的 Worker LLM 需要从文本描述中推断子任务产出了什么文件，可能遗漏或重复创建。
- **修复建议**：在 resume context 中显式列出所有 sub-task 的 artifacts。

---

#### m2. `Planner._last_error` 不覆盖调度/审查失败

- **位置**：`planner.py:128`（定义）→ 仅 `_plan()` 中设置（line 254, 277, 285）
- **症状**：`_dispatch_with_review` 中的失败（审查重试用尽、Worker 崩溃）不更新 `_last_error`。
- **后果**：外部无法查询「最后一次执行出了什么问题」。
- **修复建议**：在 `_dispatch_with_review` 失败路径和 `_run_worker` 异常路径中更新 `_last_error`。

---

#### m3. Console `think_block` 只显示 Gatekeeper，缺少 Planner 推理

- **位置**：`gatekeeper.py:294-300`（Gatekeeper think_block）vs `planner.py:261-268`（Planner 有 think_block 但代码已存在）
- **事实核查**：Planner 的 `_plan()` 在 `planner.py:261-268` 已经有 `think_block` 调用，标签为 `"Planner"`。此问题实际上**已修复**或**从未存在**——Planner 的推理会以 `💭 [Planner]` 显示。
- **状态**：非问题。关闭。

---

#### m4. `TaskSpec` 缺少一致性校验

- **位置**：`protocol.py:141-162`
- **症状**：`TaskResult.validate()` 只验证 `NEEDS_DECOMPOSITION` 状态。`TaskSpec` 没有任何 `validate()` 方法——`task_id` 可以为空字符串（planner.py:297 的 `str(item.get("task_id", ""))` 可能产生 `""` 的 task_id）。
- **后果**：空的 `task_id` 可能导致 TaskManager 状态混乱、日志无法追踪。
- **修复建议**：为 `TaskSpec` 增加 `validate()` 方法，至少检查 `task_id` 和 `description` 非空。

---

## 四、汇总优先级矩阵

| ID | 问题 | 影响范围 | 修复难度 | 优先级 |
|----|------|----------|----------|--------|
| C1 | goal 丢失 | 所有 Task 路径 | 低（加字段） | 🔴 立即 |
| C2 | intent 空值无兜底 | 所有 Task 路径 | 低（一行代码） | 🔴 立即 |
| C3 | constraints 不传播 | 有约束的 Task | 低（加字段或拼 context） | 🔴 立即 |
| C4 | 子审查失败静默 | 自分解路径 | 中（需改协议） | 🔴 尽快 |
| C5 | 用户看不到失败细节 | 用户体验 | 低（格式化改进） | 🔴 尽快 |
| M1 | priority 无效果 | 所有 Task 路径 | 中（调度逻辑改动） | 🟡 计划 |
| M2 | Session 无历史 | 多轮对话 | 中（Gatekeeper 接口改动） | 🟡 计划 |
| M3 | max_depth 不一致 | 深度分解 | 低（传参） | 🟡 计划 |
| M4 | MINOR_REVISIONS 无复审 | 审查路径 | 低（加审查调用） | 🟡 计划 |
| M5 | 子审查无失败信号 | 自分解路径 | 低（改返回值） | 🟡 计划 |
| M6 | Planner 缺身份提示词 | Planner LLM 质量 | 低（加 prompt） | 🟡 计划 |
| M7 | Reviewer FAILURE 语义误导 | 代码可读性 | 中（加枚举值） | 🟡 计划 |
| M8 | 提示词重复 | 维护性 | 低（提取共享） | 🟡 计划 |
| m1 | 子任务 artifacts 丢失 | 自分解 resume | 低（格式化改进） | 🟢 后续 |
| m2 | _last_error 不完整 | 调试体验 | 低（加赋值） | 🟢 后续 |
| m3 | Planner think_block | 非问题 | — | ✅ 关闭 |
| m4 | TaskSpec 缺少 validate | 数据完整性 | 低（加方法） | 🟢 后续 |

---

## 五、建议修复顺序

### 第一轮（P0 — 数据完整性，1-2小时）
1. **C2**：`planner.py:303` 增加 intent fallback
2. **C1**：`TaskSpec` 增加 `goal` 字段 + `planner.py` 传播
3. **C3**：`TaskSpec` 增加 `constraints` 字段 或追加到 `context`

### 第二轮（P1 — 审查与反馈闭环，2-4小时）
4. **C4**：`_review_sub_result` 增加审查状态信号
5. **M4**：`MINOR_REVISIONS` 第二次也做审查
6. **M5**：统一 `_review_sub_result` 和 `_dispatch_with_review` 的失败行为
7. **C5**：改进 `_report_to_user` 的失败信息展示

### 第三轮（P2 — 架构改进，4-8小时）
8. **M1**：让 `priority` 影响执行策略
9. **M2**：Session 向 Gatekeeper 传递历史
10. **M3**：统一 max_depth 管理
11. **M6**：Planner 身份提示词
12. **M7**：Reviewer verdict 语义修正
13. **M8**：提取共享提示词

### 第四轮（P3 — 收尾）
14. **m1**、**m2**、**m4**：小修小补

---

> **分析日期**：2026-07-16
> **分析范围**：`core/gatekeeper.py`、`core/planner.py`、`core/worker.py`、`core/reviewer.py`、`core/protocol.py`、`core/session.py`、`main.py`
> **方法**：Top-Down（从用户输入到 Worker 执行）+ Bottom-Up（从 Worker 输出到用户看到的结果）
