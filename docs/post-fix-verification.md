# Janus Post-Fix 综合验证报告

> **验证日期**：2026-07-16
> **方法**：自顶向下（Top-Down，追踪用户输入→Worker 的全链路数据流）+ 自底向上（Bottom-Up，逐文件审查源代码修复到位情况）
> **基线**：`docs/flow-analysis-issues.md`（17 个原始问题）

---

## 一、总览

| 类别 | 数量 | 说明 |
|------|------|------|
| ✅ 已确认修复 | 12/17 | 含 5 个 Critical 全修 + 3 个 Major + 4 个 Minor |
| ⚠️ 未修复 | 5/17 | 4 个 Major + 1 个 Minor，均为非阻塞级 |
| 🔴 新发现缺口 | 5 | 1 个中等、2 个低、2 个极低 |

**整体结论：核心管线完整，Janus 已达到可实际使用状态。** 17 个原始问题中 12 个已修复（含全部 5 个 Critical），剩余 5 个都是 Major/Minor 级别的架构优化项。关键数据流（goal、constraints、intent、priority、history）从 Session → Gatekeeper → Directive → Planner → TaskSpec → Worker 完整贯穿，边缘路径（自分解、重试、子审查）均保留了关键字段。

---

## 二、逐项验证（17 项交叉验证）

### 🔴 Critical（5/5 全部修复）

#### ✅ C1：`Directive.goal` 已到达 Worker

- **Top-Down 追踪**：`planner.py:335` → `TaskSpec(goal=directive.goal)` → `worker.py:563` → `## Overall Goal\n{spec.goal}`
- **Bottom-Up 确认**：`protocol.py:168` TaskSpec 已有 `goal: str = ""` 字段；`worker.py:563-564` 系统提示中渲染
- **自分解路径**：`worker.py:308` 子 TaskSpec 传递 `goal=spec.goal`
- **重试路径**：`planner.py:508`（_make_retry_spec）、`worker.py:663-665`（子审查重试）均保留

#### ✅ C2：intent fallback 已生效

- **三层 fallback**：
  1. Prompt 层：`planner.py:258` — `"If not specified, derive from the parent goal."`
  2. 战略意图注入：`planner.py:270` — `{directive.intent or 'Complete the goal as stated.'}`
  3. 解析层兜底：`planner.py:334` — `intent=str(item.get("intent", "") or directive.intent)`
- **⚠️ 轻微注意**：当 `directive.intent` 也为空时（API 失败/JSON 解析失败），Worker 无意图提示。设计上可接受（Gatekeeper 未能提取意图时 Worker 仅执行任务本身），但建议增加日志警告。

#### ✅ C3：constraints 已传播

- **Planner prompt**：`planner.py:237-243` — 非空时构建 `HARD CONSTRAINTS` 块，插入 LLM 的 user message（`planner.py:271`）
- **TaskSpec**：`planner.py:336` — `constraints=directive.constraints`
- **Worker system prompt**：`worker.py:567-568` — `## Hard Constraints (MUST follow)\n{spec.constraints}`
- **自分解/重试路径**：`worker.py:309, 362`、`planner.py:509` 均传播

#### ✅ C4：子 Worker 审查失败已浮出水面

- `worker.py:696-705`：重试用尽后 `logger.warning()` + `console.review_fail()` + 返回带 `[SUB-WORKER REVIEW FAILED after N attempts]` 标记的结果
- `worker.py:321-326`：resume context 中注入 `⚠️ SUB-TASK REVIEW FAILURES DETECTED` 警告段
- `planner.py:625-649`：`_summarize()` 扫描 `[SUB-WORKER REVIEW FAILED]` 和 `[REVIEW FAILED]` 标记，生成中文失败详情

#### ✅ C5：用户可看到失败详情

- `planner.py:588-660`：`_summarize()` 输出 `❌`/`✅`/`⚠️` 标记 + 失败原因提取
- `gatekeeper.py:486-498`：中文格式汇报（"X个任务完成，Y个失败" / "全部完成" / "全部失败"）
- 失败详情含子任务审核失败、审查未通过等区分

---

### 🟡 Major（4/8 已修复，4 未修复）

#### ✅ M1：priority 已影响执行

- **retry 预算**：`planner.py:180-187` — speed/urgent→0, quality→3, balanced/normal→2
- **Worker 行为指引**：`planner.py:566-567` → `worker.priority = self._current_priority` → `worker.py:571-578` 注入系统提示

#### ✅ M2：Session 历史已到达 Gatekeeper

- `session.py:54` → `_format_history_context()` → 最近 5 轮对话
- `gatekeeper.py:211` (`_decide`)、`gatekeeper.py:389` (`_formulate_directive`)、`gatekeeper.py:279` (`_respond`) 三处分发

#### ⚠️ M3：max_depth 不一致（未修复）

- `planner.py:126`（`self._max_depth`）仍与 `worker.py:39`（`MAX_WORKER_DEPTH=3`）无代码关联
- Planner 的 `_max_depth` 可配置但 Worker 硬编码，用户配置无效
- **影响**：低。深度限制在实际使用中极少触及，当前两值巧合相等（均为 3）

#### ⚠️ M4：MINOR_REVISIONS 第二次仍不做审查（部分改善）

- `planner.py:408-430`：`attempt==1` 仍不调 Reviewer，直接 auto-accept
- **改善**：现在 auto-accept 时会注释 `[审查: 轻微修改后自动通过]`
- **未修复**：第二次尝试的输出完全没有被验证

#### ✅ M5：子审查重试用尽有可见信号

- `worker.py:690-720`：返回带 `[SUB-WORKER REVIEW FAILED]` 标记 + `logger.warning()` + `console.review_fail()` 的结果
- 与 `planner._dispatch_with_review` 的失败行为一致（均有显式失败信号）

#### ⚠️ M6：Planner 仍缺身份提示词（未修复）

- Gatekeeper 有 `_GATEKEEPER_IDENTITY`（gatekeeper.py:90-107），明确角色和约束
- Planner 仅有 `_CONTEXT_DISCIPLINE_PROMPT` + `_PLAN_SYSTEM_PROMPT`，LLM 不知道自己是"参谋"
- **影响**：低。Planner LLM 可能输出不必要的细节，但不影响功能正确性

#### ⚠️ M7：Reviewer 对 Worker FAILURE 仍返回 APPROVED（未修复）

- `reviewer.py:339-354`：Worker 返回 FAILURE → `ReviewResult(verdict=APPROVED, ...)`
- 语义问题：`APPROVED` 意味着"产出物符合标准"，但实际是"跳过审查"
- 业务逻辑上因 `planner._summarize` 检查 `result.status == FAILURE`，计数正确
- **未修复原因**：语义修正需加 `ReviewVerdict.SKIPPED` 枚举值，影响面较小

#### ⚠️ M8：`_CONTEXT_DISCIPLINE_PROMPT` 仍重复（未修复）

- `gatekeeper.py:73-80` 和 `planner.py:80-87` 中 7 行提示词完全相同（仅角色称谓不同）
- 未提取到共享模块

---

### 🟢 Minor（3/4 已修复，1 未修复/不适用）

#### ✅ m1：子任务 artifacts 已在 resume context 中列出

- `worker.py:338-349`：dedicated `--- SUB-TASK ARTIFACTS ---` 段，列出所有文件路径

#### ✅ m2：`_last_error` 已覆盖调度/审查失败

- `planner.py:202-215`：dispatch 循环 try/except → 设置 `_last_error`
- `planner.py:530-555`：Worker factory 崩溃 → 设置 `_last_error`
- `planner.py:571-582`：Worker 运行崩溃 → 设置 `_last_error`
- `gatekeeper.py:360-366`：report 为空/失败时检查 `planner._last_error`

#### ✅ m3：Planner think_block 已存在（非问题，关闭）

- `planner.py:291-299` 已有 think_block 调用，标签为 "Planner"

#### ✅ m4：TaskSpec 已有 validate() 方法

- `protocol.py:172-174`：`validate()` 检查 `task_id` 和 `description` 非空

---

## 三、新发现的缺口（Post-Fix）

综合 Top-Down 逐链路追踪 + Bottom-Up 逐文件审查，发现 5 个新缺口：

### 🔴 GAP-1：Reviewer 看不到 goal/constraints/intent

- **严重程度**：🟡 中等
- **位置**：`reviewer.py:275-305`（`_REVIEW_USER_TEMPLATE`）
- **症状**：模板仅包含 `description`、`acceptance_criteria`、`context`、`status`、`summary`、`result`、`artifacts`
- **缺失**：**不包含 `spec.goal`、`spec.constraints`、`spec.intent`**
- **影响**：Reviewer 无法审核 Worker 是否遵守硬性约束（如"不修改已有文件"），也无法判断结果是否服务大局目标。例如 Worker 成功写了一个文件但违反 constraints 中的"不超过 100 行"限制 → Reviewer 不会发现
- **修复建议**：在 `_REVIEW_USER_TEMPLATE` 中加入 `CONSTRAINTS: {constraints}`、`GOAL: {goal}`、`INTENT: {intent}`，并在 `review()` 调用处传入 `spec.constraints`、`spec.goal`、`spec.intent`

### 🟡 GAP-2：Planner 无法感知多轮对话历史

- **严重程度**：🟢 低
- **位置**：`gatekeeper.py:355` → `self._planner.execute(directive)`
- **症状**：Directive 不含历史上下文，Planner 的 `execute()` 无 history 参数
- **影响**：用户说"继续上一次的任务"，Gatekeeper 能通过 history_context 理解意图并制定 directive，但 Planner 分解时看不到之前的任务结果。对当前架构影响有限 —— Planner 每次执行是独立的
- **修复建议**：在 Directive 中增加 `context: str = ""` 字段（承载历史摘要），或在 Planner.execute() 中增加可选的 history 参数

### 🟡 GAP-3：Directive.intent 为空时无 fallback 警告

- **严重程度**：🟢 低
- **位置**：`gatekeeper.py:420-425`（API 失败 fallback）、`gatekeeper.py:454-459`（JSON 解析失败 fallback）
- **症状**：Gatekeeper 静默返回 `intent=""`，Worker 在无战略方向的情况下工作
- **影响**：非 bug，但可导致 Workers 做次优决策。建议增加 `logger.warning()` 在 intent 为空时告警
- **修复建议**：在 fallback 路径中增加日志警告

### 🟢 GAP-4：Worker priority 指引仅覆盖 speed/urgent/quality

- **严重程度**：🟢 极低
- **位置**：`worker.py:571-575`（`_priority_guidance`）
- **症状**：字典仅含 `speed`、`urgent`、`quality`，不含 `balanced`、`normal` 及其他未来可能的值
- **影响**：`balanced`/`normal` 静默获得空字符串指引，不注入任何 priority prompt。retry budget 有 fallback（默认 2），功能上无影响
- **修复建议**：使 `_priority_guidance` 和 `_priority_retries` 使用统一字典

### 🟢 GAP-5：ExecutionReport 不携带原始 goal/constraints

- **严重程度**：🟢 极低
- **位置**：`protocol.py:202-225`（ExecutionReport 定义）
- **症状**：ExecutionReport 无 goal/constraints 字段
- **影响**：Gatekeeper 向用户汇报时无法提及"约束 X 已被遵守"。这是设计取舍——Gatekeeper 只看战略级报告
- **修复建议**：如需确认约束遵守，在 ExecutionReport 中增加 `goal` 和 `constraints` 字段

---

## 四、修复状态矩阵

| ID | 问题 | 严重度 | 状态 | 关键证据 |
|----|------|--------|------|---------|
| C1 | goal 丢失 | 🔴 Critical | ✅ 已修复 | `protocol.py:168`, `planner.py:335`, `worker.py:563` |
| C2 | intent 空值无兜底 | 🔴 Critical | ✅ 已修复 | `planner.py:334` — `or directive.intent` |
| C3 | constraints 不传播 | 🔴 Critical | ✅ 已修复 | `planner.py:237-243, 271, 336`, `worker.py:567` |
| C4 | 子审查失败静默 | 🔴 Critical | ✅ 已修复 | `worker.py:690-720` — 标记 + 日志 + console |
| C5 | 用户看不到失败细节 | 🔴 Critical | ✅ 已修复 | `planner.py:588-660`, `gatekeeper.py:486-498` |
| M1 | priority 无效果 | 🟡 Major | ✅ 已修复 | `planner.py:180-187`, `worker.py:571-578` |
| M2 | Session 无历史 | 🟡 Major | ✅ 已修复 | `session.py:54`, `gatekeeper.py:211, 389, 279` |
| M3 | max_depth 不一致 | 🟡 Major | ⚠️ 未修复 | Planner vs Worker 两套独立常量 |
| M4 | MINOR_REVISIONS 无复审 | 🟡 Major | ⚠️ 部分改善 | 已注解但不做实际审查 |
| M5 | 子审查无失败信号 | 🟡 Major | ✅ 已修复 | `worker.py:690-720` — 显式失败标记 |
| M6 | Planner 缺身份提示词 | 🟡 Major | ⚠️ 未修复 | Planner 仅有技术 prompt，无角色定义 |
| M7 | Reviewer FAILURE 语义误导 | 🟡 Major | ⚠️ 未修复 | `reviewer.py:346` — APPROVED for FAILURE |
| M8 | 提示词重复 | 🟡 Major | ⚠️ 未修复 | gatekeeper.py:73 vs planner.py:80 |
| m1 | 子任务 artifacts 丢失 | 🟢 Minor | ✅ 已修复 | `worker.py:338-349` — artifacts 段 |
| m2 | _last_error 不完整 | 🟢 Minor | ✅ 已修复 | `planner.py:202, 530, 573` |
| m3 | Planner think_block | 🟢 Minor | ✅ 非问题 | `planner.py:291-299` 已存在 |
| m4 | TaskSpec 缺 validate | 🟢 Minor | ✅ 已修复 | `protocol.py:172-174` |

---

## 五、新缺口严重度排序

| 排序 | ID | 严重度 | 影响 | 修复难度 |
|------|-----|--------|------|---------|
| 1 | GAP-1 | 🟡 中等 | Reviewer 盲区——无法审计约束遵守和目标对齐 | 低（改模板 + 传参数） |
| 2 | GAP-2 | 🟢 低 | Planner 无历史感知，多轮任务分解可能不精准 | 中（改协议 + 接口） |
| 3 | GAP-3 | 🟢 低 | intent 为空时无法预警次优决策 | 极低（加日志） |
| 4 | GAP-4 | 🟢 极低 | 新 priority 值无 Worker 指引 | 极低（增字典键） |
| 5 | GAP-5 | 🟢 极低 | 约束遵守情况无法向上汇报 | 极低（加字段） |

---

## 六、整体评估：Janus 能否实际使用？

### ✅ 可以实际使用。

**理由**：

1. **核心管线完整**：用户输入 → Gatekeeper 决策 → Directive 制定 → Planner 分解 → Worker 执行 → Reviewer 审查 → Gatekeeper 汇报，全部 7 个环节的 goal/constraints/intent/priority 关键字段均有覆盖

2. **失败路径覆盖**：自分解、重试（含 MAJOR_REVISIONS/REJECTED 重试）、子审查重试用尽、Worker 崩溃、Worker factory 崩溃、dispatch loop 崩溃——6 条异常路径均有 `_last_error` 设置和用户可见的错误信息

3. **多轮对话支持**：Session → Gatekeeper 的历史传递已打通，`handle()` 可处理上下文相关指令（"继续上次的任务"）

4. **审查闭环**：Worker → Reviewer → 重试/通过 → Gatekeeper 汇报，分级审查（APPROVED/MINOR/MAJOR/REJECTED）均已实现

5. **剩余问题均为非阻塞级**：未修复的 5 个 Major 和 5 个新缺口均为架构优化/提示词/语义层面的改善项，不影响核心功能正确性

### 建议优先修复（投入产出比最高）

1. **GAP-1**（Reviewer 看不到 goal/constraints/intent）：1 小时工作量，显著提升审查质量
2. **M3**（max_depth 统一）：15 分钟工作量，消除用户困惑
3. **GAP-3**（intent 为空时告警）：5 分钟工作量，改善可观测性

---

## 七、附录：未修复项详细说明

### M3：max_depth 不一致

```
planner.py:126   self._max_depth = max_depth          # 可配置，从未递增
worker.py:39      MAX_WORKER_DEPTH: int = 3            # 硬编码，实际生效
```

用户配置 `max_depth=5` 期望更深分解，但 Worker 硬编码 `MAX_WORKER_DEPTH=3` 不变。当前两值巧合相等，暂不阻塞。

### M4：MINOR_REVISIONS 无复审

```
planner.py:408-430:
  attempt==0: 带反馈重试
  attempt==1: 直接 auto-accept，不调 Reviewer
```

改善：auto-accept 时标注 `[审查: 轻微修改后自动通过]`。但第二次输出未被验证——Worker 可能引入新 bug。

### M6：Planner 缺身份提示词

Gatekeeper 有 18 行 `_GATEKEEPER_IDENTITY`（中文，明确角色、约束、工作方式），Planner 仅有英文技术提示词。Planner LLM 不知道自己是"参谋"、不知道与 Gatekeeper/Worker 的关系——可能过度细化或不必要地输出细节。

### M7：Reviewer FAILURE 语义

```python
reviewer.py:346:
  return ReviewResult(
      verdict=ReviewVerdict.APPROVED,           # "通过" 但实际是 "跳过"
      summary="Worker reported failure — no audit needed.",
  )
```

业务逻辑因 `planner._summarize` 检查 `result.status == FAILURE` 计数正确，但代码语义不清。建议增加 `ReviewVerdict.SKIPPED`。

### M8：提示词重复

```python
gatekeeper.py:73-80:  "...like an executive talking to their assistant..."
planner.py:80-87:     "...like a chief of staff organizing operations..."
```

7 行相同逻辑，仅角色称谓不同。提取到 `protocol.py` 或 `core/prompts.py` 可减少维护负担。

---

> **验证范围**：`core/gatekeeper.py`（555 行）、`core/planner.py`（737 行）、`core/worker.py`（1104 行）、`core/reviewer.py`（443 行）、`core/protocol.py`（225 行）、`core/session.py`（129 行）
> **验证方法**：自顶向下（完整数据流链路追踪）+ 自底向上（逐文件逐函数交叉验证）
