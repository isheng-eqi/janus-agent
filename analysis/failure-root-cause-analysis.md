# Janus AgentCanary 测试运行失败 — 根因分析

## 概述

经过三个轮次的任务执行，发现两条系统性失败路径：
1. **截断冲突**：Worker 的 `read_file` 工具硬截断 5000 字符，但 Reviewer 的验收标准要求"完整返回内容"→ 永远无法通过审查
2. **Worker 目标混淆**：Worker 被分配到分析 agentcanary 项目却被引导到 Janus 源码目录

---

## 问题一：截断冲突 — 系统性死锁

### 证据链

```
Worker.read_file(path) 
  → _real_read_file()          # worker.py:901-908
      → 读取文件全文
      → if len(content) > 5000: 
          content = content[:5000] + "...[truncated]"
      → 返回截断内容给 LLM

Reviewer.review(spec, result)
  → _build_artifact_contents() # reviewer.py:406-481
      → 直接读磁盘（绕开 Worker 工具），每文件 1000 字符
  → 审查 prompt 包含 ARTIFACT CONTENTS
  → 但审查对象是 Worker 的 result TEXT（通过 Worker 的 read_file 获取的截断版）

验收标准（Planner LLM 生成）:
  "[HARD] 文件内容必须完整返回 —— 不能有截断、省略或占位符"

Reviewer 看到：
  Worker result: "...[truncated]"  
  → [CRITICAL] 内容不完整，违反 [HARD] 标准
  → Verdict: REJECTED 或 MAJOR_REVISIONS
```

### 根因

有三层冲突：

| 层级 | 约束 | 数值 |
|------|------|------|
| `_real_read_file()` | Worker 读文件截断上限 | **5,000 字符** |
| `_build_artifact_contents()` | Reviewer 直读产物上限 | **1,000 字符/文件，8,000 总** |
| Planner 的验收标准生成 | LLM 不知道这些限制 | 常生成"完整返回"类 [HARD] 标准 |

**关键矛盾**：
- Worker 的工具链永远无法"完整返回"超过 5000 字符的文件内容
- 但 Planner（高层的 LLM）不知道工具限制，容易生成"文件内容必须完整返回"这样的标准
- Reviewer 虽然**自己能直接读到完整文件**（`_build_artifact_contents` 绕开了 Worker 工具），但它审查的是 Worker 的 **result 文本**，其中包含截断标记 `...[truncated]`
- Worker 的 system prompt 说："**Reviewer 可以直接读取产物文件**"——但 Reviewer 仍然会审查 Worker 输出的文本中是否满足"完整返回"这个要求

### 这是一个"不可能完成的任务"

Worker 无论如何重试，`read_file` 永远会在 5000 字符处截断 → 永远带着 `...[truncated]` → Reviewer 永远判定不满足 → 重试死循环 → 最终耗尽 retry budget 标记 FAIL

### 修复方案

#### 方案 A：提升 read_file 截断上限（快速修复，治标）

```python
# worker.py _real_read_file
MAX_READ_CHARS = 50000  # 从 5000 提升到 50000
```

优点：立即缓解大部分案例
缺点：仍有上限，超大文件仍然会截断；LLM 上下文成本增加

#### 方案 B：read_file 支持分页读取（推荐）

```python
def _real_read_file(path: str, offset: int = 0, limit: int = 5000) -> str:
    """分页读取，Worker 可以多次调用来读取完整文件"""
    with open(path, "r") as f:
        f.seek(offset)
        content = f.read(limit)
    if len(content) == limit:
        content += f"\n...[可用 offset={offset+limit} 继续读取]"
    return content
```

同时在 tool description 里说明分页机制，让 LLM 知道可以循环调用。

#### 方案 C：修改 Planner 分解 prompt，禁止"完整返回"类标准（治本）

在 `planner.py:_plan()` 的 prompt 中增加工具限制说明：

```python
# 在 _plan() 的 user prompt 中增加：
"IMPORTANT TOOL LIMITATIONS — the Worker's tools have these constraints:\n"
"- read_file truncates at 5000 characters — do NOT require 'complete' file "
"  return in acceptance criteria. Instead use: '[HARD] 文件的前 5000 字符必须正确返回' "
"  or '[HARD] 文件的关键部分（imports, 核心函数签名, 主要逻辑）必须返回'\n"
"- web_extract truncates at 3000 chars per URL\n"
```

#### 方案 D：Reviewer 审查焦点从"Worker 输出"转向"产物文件"（结构修正）

当前：Reviewer 既审查 Worker 的 result 文本，也读产物文件
问题：acceptance_criteria 中的"完整返回"是针对 Worker 输出，不是针对产物

修正方向：
- Worker 的 system prompt 已经说"Reviewer 可以直接读产物"——但这只对"文件创建/修改"类任务有效
- 对"代码分析"类任务，Worker 的产物是 analysis result（文本），Reviewer 读不到"完整文件内容"
- 应该让 Planner 区分两类任务：
  - **创作类**（写代码/文件）：产物是文件，Reviewer 读文件验证 → 不需要"完整返回"
  - **分析类**（读代码/报告）：产物是文本，但不需要"完整返回原文件"——分析摘要即可

#### 推荐组合方案

1. **短期**：提升 `_real_read_file` 上限至 50000 字符 + 在 Planner prompt 中插入工具限制说明
2. **中期**：给 `read_file` 加分页参数（offset/limit）使 Worker 可以完整读取任意大小文件
3. **长期**：区分"创作"和"分析"任务类型，Reviewer 对不同类型用不同审查策略

---

## 问题二：Worker 目标混淆——读了错误项目的文件

### 现象

Task-4：Worker 被要求"分析 agentcanary 的 Python 文件"，但实际读取了 Janus 核心源码 (`C:\Users\HI\Desktop\janus\core\*.py`)

### 根因

跟踪 TaskSpec 的构造链：

```
Gatekeeper._formulate_directive(goal)
  → directive.goal = "分析 agentcanary 项目"
  → directive.context = ""  (空！)
  → Planner._plan(directive)
      → LLM 分解出 task-4: description="分析 Python 文件", context=""
      → context_str = "" (LLM 没填) + "" (directive.context 也是空)
      → TaskSpec.context = ""  ← 没有任何项目路径信息！
```

Worker 收到的 prompt：
```
## Your Task
分析项目中的 Python 文件的结构...
## Context
(空)
## Acceptance Criteria
...
```

Worker 的 LLM 不知道"哪个项目"，默认去读当前工作目录下的文件——恰好运行目录是 Janus 自己的目录。

### 更深层的结构问题

1. **`directive.context` 只承载对话历史**（`history_context`），不是项目路径
2. **TaskSpec 没有 `workspace` / `project_root` 字段**
3. **Planner LLM 不知道 CWD**——没把工作目录注入 decomposition prompt
4. **Worker 的 CWD 是启动 Janus 的目录**——恰好是 janus 项目目录

### 修复方案

#### 方案 A：在 TaskSpec.context 中显式注入工作路径

```python
# 在 Gatekeeper._execute_via_planner 或 Planner._plan 中：
import os
workspace_note = f"WORKSPACE: {os.getcwd()}\n"
directive.context = workspace_note + (directive.context or "")
```

或者更精确地在每个 TaskSpec 构造时注入：

```python
# planner.py:_plan() item iteration
context_str = str(item.get("context", ""))
# 自动注入工作目录
workspace_hint = f"当前工作目录: {os.getcwd()}"
if workspace_hint not in context_str and workspace_hint not in directive.context:
    context_str = f"{workspace_hint}\n{context_str}"
```

#### 方案 B：给 TaskSpec 增加 `workspace` 字段（推荐）

```python
@dataclass
class TaskSpec:
    task_id: str
    description: str
    workspace: str = ""  # 新增：工作目录/项目根路径
    ...
```

Worker 在运行前 `os.chdir(spec.workspace)`，确保所有文件操作都在正确项目下。

#### 方案 C：Planner 分解 prompt 里明确要求 LLM 包含项目路径

```python
# planner.py:_plan() user prompt 中增加：
"- If the goal references a specific project or directory, include the full "
"  path in the 'context' field of each task, e.g. 'Project root: /path/to/project'\n"
"- Tasks like 'analyze files' MUST specify which directory to operate in\n"
```

#### 推荐：组合 A + C

既在 Planner prompt 中要求 LLM 输出路径，又在代码层自动兜底（注入当前 CWD），双重保险。

---

## 问题三：恢复循环无效——重复同样的系统性错误

### 现象

Recovery loop 运行了 2 次，每次都重新分解任务 → 重新分派 Worker → 同样的截断冲突导致同样的审查失败。

### 根因

恢复管道（`gatekeeper.py:_execute_via_planner` 352-382 行）：

```
while recovery_attempts < max_recovery and report.failed > 0:
    diagnosis = self._diagnose_failures(report, goal)    # LLM 诊断
    new_directive = self._reformulate_for_recovery(...)    # 制定新策略
    new_report = self._planner.execute(new_directive)      # 重新执行
    report = self._merge_reports(report, new_report)
```

**`_diagnose_failures` 的盲区**（501-568 行）：

LLM 收到的失败信息是：
```
Failed Tasks (2 out of 4):
  - ❌ worker-1: Failed review after 2 retries (审查判定: rejected)
  - ❌ worker-3: Failed review after 2 retries (审查判定: rejected)
```

从这些摘要中，LLM **完全看不到**：
- 具体是哪个 acceptance_criterion 没通过
- Reviewer 指出的具体 issue 是什么
- 工具限制（read_file 截断 5000 字符）
- 原任务的 acceptance_criteria 原文

所以 LLM 的诊断只能是泛泛的：
> "Tasks likely failed due to unclear acceptance criteria or worker capability gaps. Try smaller, more explicit tasks."

然后 `_reformulate_for_recovery` 基于这种模糊诊断制定"新策略"——结果只是把同样的问题用不同的话重新描述一遍，Planner 重新分解出几乎一样（或类似）的 Task，Worker 再次遇到同样的截断冲突。

### 修复方案

#### 方案 A：将 Reviewer 的具体 issue 注入失败摘要（必做）

```python
# planner.py:_summarize() 中，失败任务应该携带 acceptance_criteria 和 review issues
if r.status == TaskStatus.FAILURE:
    detail_line = (
        f"❌ {task_label}: {r.summary}\n"
        f"   验收标准: {acceptance_criteria_for_task}\n"  # 新增
        f"   审查问题: {review_issues}\n"                  # 新增
    )
```

这样 Gatekeeper 的 `_diagnose_failures` 能看到具体问题：
```
Failed Tasks:
  - ❌ worker-1: Failed review (rejected)
    验收标准: [HARD] 文件内容必须完整返回
    审查问题: [critical] Worker 返回的内容包含截断标记 ...[truncated]
```

LLM 诊断就会变成：
> "Task-1 failed because the acceptance criterion requires complete file content, but the Worker's read_file tool truncates at 5000 chars. This is a tool limitation, not a decomposition problem. Fix: change acceptance criteria or raise tool limit."

#### 方案 B：在诊断 prompt 中加入已知工具限制

```python
# gatekeeper.py:_diagnose_failures 的 prompt 中：
"Known tool limitations of the Worker:\n"
"- read_file: truncates at 5,000 characters\n"
"- web_extract: truncates at 3,000 characters per URL\n"
"- web_search: NOT IMPLEMENTED (returns placeholder)\n"
"- browser_navigate: NOT IMPLEMENTED (returns placeholder)\n"
"Consider these when diagnosing failures.\n"
```

#### 方案 C：恢复循环中检测"重复失败签名"（结构性修复）

如果两个连续轮次的失败原因相同（相同的 review issue 签名），跳过重试，直接报告"工具限制导致不可完成任务"：

```python
# 在 Gatekeeper._execute_via_planner 中：
if self._is_same_failure_signature(previous_report, new_report):
    logger.warning("Same failure signature detected — tool limitation, aborting recovery.")
    break
```

#### 推荐：A + B 组合

A（注入详细失败信息）是最关键的——没有具体信息，任何诊断都是盲猜。B（告知工具限制）让 LLM 能做更有针对性的分析。

---

## 总结：优先级排序

| 优先级 | 修复项 | 影响范围 | 复杂度 |
|--------|--------|----------|--------|
| 🔴 P0 | **Planner prompt 注入工具限制说明** | 防止生成不可完成的验收标准 | 低（改 prompt 字符串） |
| 🔴 P0 | **将 review issues 注入失败详情** | 使恢复循环能看到具体失败原因 | 低（修改 `_summarize()`） |
| 🟡 P1 | **提升 read_file 上限至 50000** | 缓解大部分截断场景 | 极低（改一个常量） |
| 🟡 P1 | **在 Gatekeeper 诊断 prompt 中告知工具限制** | 让诊断 LLM 知道约束 | 低 |
| 🟡 P1 | **TaskSpec.context 注入工作目录** | 防止 Worker 目标混淆 | 低 |
| 🟢 P2 | **read_file 分页支持 (offset/limit)** | 彻底消除截断问题 | 中 |
| 🟢 P2 | **区分创作/分析任务类型** | 让 Reviewer 用正确策略审查 | 中 |
| 🟢 P2 | **重复失败签名检测** | 避免无意义的重试循环 | 中 |
