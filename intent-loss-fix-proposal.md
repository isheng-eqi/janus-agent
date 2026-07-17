# Janus 用户意图丢失问题 —— 根因分析与架构修复方案

## 问题复现

用户输入: `分析 C:\Users\HI\Desktop\agentcanary`
实际产出: Janus 分析了 **自己** (`C:\Users\HI\Desktop\janus`)，而不是 `agentcanary`。

---

## 根因分析（按严重程度排序）

### 🔴 根因 #1：Planner 无条件的 CWD 注入（最致命）

**文件**: `core/planner.py`，第 365-372 行

```python
# Prepend working directory so Workers know where to operate.
# Prevents target confusion when the Planner decomposes tasks
# without specifying a project directory.
cwd_info = f"Working directory: {os.getcwd()}"
if context_str:
    context_str = f"{cwd_info}\n{context_str}"
else:
    context_str = cwd_info
```

**问题**: Planner 在分解任务时，**无条件**地把 Janus 自己的运行目录（`C:\Users\HI\Desktop\janus`）以 "Working directory: …" 的形式注入到每个 TaskSpec 的 context 中。Worker 收到后，会把这条上下文当作「要分析的工作目录」，于是分析 Janus 自己而非 `agentcanary`。

注释说这"防止目标混淆"，实际上它**正是混淆的根源**。用户明确给出 `agentcanary` 路径时，这个 CWD 注入会与用户的路径产生冲突。

**严重性**: 🔴 CRITICAL — 这是意图丢失的直接原因。

---

### 🟡 根因 #2：Gatekeeper 的 intent 提取可能漂移

**文件**: `core/gatekeeper.py`，第 402-506 行 `_formulate_directive()`

LLM 被要求从用户目标中提取 "strategic intent"。如果 LLM 产生泛化意图（如"分析当前项目"），而原始 goal 包含具体路径（`C:\Users\HI\Desktop\agentcanary`），意图层的泛化会削弱具体指引。

**实际情况**: `goal` 字段确实原样保留，但 `intent` 字段可能泛化。Worker 的 system prompt 同时展示 goal 和 intent，intent 如果过于泛化 + CWD 注入 → Worker 最终选了错误的目标。

**严重性**: 🟡 MAJOR

---

### 🟡 根因 #3：交付验证 `_validate_delivery()` 不够精准

**文件**: `core/gatekeeper.py`，第 830-898 行

```python
f"用户原始需求：{goal}\n\n"
f"我们产出了以下结果：{report_summary}\n\n"
"判断：这份产出是否真正回答了用户的需求？\n"
"如果分析的对象完全错误（比如用户要求分析项目A，产出分析了项目B），判定为 invalid。\n"
```

**问题**: 
1. 传入的是 `report.summary`（摘要）而非完整细节，LLM 可能无法从摘要中判断对象是否正确。
2. 没有把用户请求中的**具体路径**抽取出来做精确对比。
3. API 错误时静默跳过（返回 `valid=True`），门禁失效。

**严重性**: 🟡 MAJOR — 已有防护但不够强。

---

### 🟢 根因 #4：缺少端到端的"原文锚点"

用户原始输入在经过 Gatekeeper 决策 → Directive 提取 → Planner 分解 → Worker 执行的多层 LLM 处理后，没有任何**不被任何 LLM 解释的原文锚点**一路伴随。每一层都可能引入偏差。

**严重性**: 🟢 MINOR（前三个根因修复后，这个问题自然缓解）

---

## 架构修复方案

### 修改 1：移除 Planner 的无条件 CWD 注入 ⭐ 最关键

**文件**: `core/planner.py`，`_plan()` 方法

**现状**:
```python
cwd_info = f"Working directory: {os.getcwd()}"
if context_str:
    context_str = f"{cwd_info}\n{context_str}"
else:
    context_str = cwd_info
```

**改为**: 只在用户目标中**没有**明确路径时，才添加 CWD 作为默认工作目录。检测逻辑：如果 `directive.goal` 中已包含盘符路径（如 `C:\`、`D:\`）或绝对路径（如 `/home/`），则不注入 CWD，因为用户已经指明了目标。

```python
import re

def _goal_has_explicit_path(goal: str) -> bool:
    """检测用户目标中是否已包含明确路径。"""
    # Windows: C:\... D:\...
    if re.search(r'[A-Za-z]:[\\/]', goal):
        return True
    # Unix absolute path: /home/...
    if re.search(r'(?:^|\s)/[a-zA-Z0-9_/]', goal):
        return True
    # UNC path: \\server\share
    if goal.startswith('\\\\'):
        return True
    return False

# === 在 _plan() 中替换原来 365-372 行 ===
if not _goal_has_explicit_path(directive.goal):
    cwd_info = f"Working directory: {os.getcwd()}"
    if context_str:
        context_str = f"{cwd_info}\n{context_str}"
    else:
        context_str = cwd_info
# else: goal 中有明确路径 → 不注入 CWD，避免混淆
```

---

### 修改 2：强化 `_formulate_directive()` 的 intent 提取

**文件**: `core/gatekeeper.py`，`_formulate_directive()` 方法

在提示词中明确告诉 LLM：
- intent 是**补充**，绝不覆盖 goal 中的具体信息（路径、文件名、项目名）
- 如果 goal 包含具体路径，intent 中必须**原样复述**该路径

```python
# 在 _DECIDE_SYSTEM_PROMPT 或 user message 中增加：
"CRITICAL RULE: If the user's goal contains explicit file paths, project "
"names, or directory references, the 'intent' field MUST repeat those "
"specifics verbatim. Intent should ADD strategic context, never REPLACE "
"or GENERALIZE the concrete details in the goal.\n\n"
"Example:\n"
"  Goal: '分析 C:\\Projects\\myapp 的代码质量'\n"
"  CORRECT intent: '分析 C:\\Projects\\myapp 项目的代码架构、安全问题和代码质量'\n"
"  WRONG intent: '分析当前项目的代码质量' (lost the path!)\n"
```

---

### 修改 3：在 Directive 和 TaskSpec 中增加 `user_request` 锚点字段

**文件**: `core/protocol.py`

在 `Directive` 和 `TaskSpec` 中增加一个 `user_request: str = ""` 字段，承载用户的**原始输入**，一路传递不被 LLM 修改。

```python
@dataclass
class Directive:
    goal: str
    intent: str = ""
    constraints: str = ""
    priority: str = "normal"
    context: str = ""
    user_request: str = ""  # ← 新增：用户原始输入，不做任何处理

@dataclass
class TaskSpec:
    task_id: str
    description: str
    acceptance_criteria: str
    context: str
    intent: str = ""
    goal: str = ""
    constraints: str = ""
    depth: int = 1
    user_request: str = ""  # ← 新增：用户原始输入
```

**传递链路**:
1. `Gatekeeper._execute_via_planner()`: `Directive(user_request=goal)`  — goal 就是原始输入
2. `Gatekeeper._formulate_directive()`: 把 `user_request` 原样传入
3. `Planner._plan()`: `TaskSpec(..., user_request=directive.user_request)`
4. `Worker._build_system_prompt()`: 在 system prompt 中展示 user_request 作为"用户原始请求锚点"

---

### 修改 4：强化 `_validate_delivery()` 做精确路径对比

**文件**: `core/gatekeeper.py`，`_validate_delivery()` 方法

```python
def _validate_delivery(self, goal: str, report: ExecutionReport) -> dict:
    # ... 现有代码 ...
    
    # 新增：从 goal 中提取路径，做精确对比
    paths_in_goal = self._extract_paths(goal)
    paths_in_report = self._extract_paths(report_summary)
    
    # 如果 goal 中有路径而 report 中没有 → 极可能跑偏
    if paths_in_goal and not paths_in_report:
        return {
            "valid": False,
            "reason": f"用户要求分析 {paths_in_goal}，但报告中未提及该路径 — 可能分析了错误的目标"
        }
    
    # ... 原有 LLM 验证 ...

@staticmethod
def _extract_paths(text: str) -> list[str]:
    """从文本中提取所有文件系统路径。"""
    import re
    paths = []
    # Windows 路径: C:\... D:\...
    paths.extend(re.findall(r'[A-Za-z]:[\\/][^\s,，。；;]+', text))
    # Unix 绝对路径
    paths.extend(re.findall(r'(?:^|\s)(/[a-zA-Z0-9_./-]{2,})', text))
    return paths
```

---

### 修改 5：Worker system prompt 增加"目标锚点"段

**文件**: `core/worker.py`，`_build_system_prompt()` 方法

```python
def _build_system_prompt(self, spec: TaskSpec) -> str:
    prompt = self._SYSTEM_PROMPT_TEMPLATE
    
    # ⭐ 新增：用户原始请求锚点，放在最前面
    if spec.user_request:
        prompt += (
            f"\n\n## ⚓ USER'S ORIGINAL REQUEST (this is your anchor — "
            f"never deviate from it)\n{spec.user_request}"
        )
    
    if spec.goal:
        prompt += f"\n\n## Overall Goal\n{spec.goal}"
    # ... 其余不变 ...
```

---

## 修改汇总

| 优先级 | 文件 | 改动 | 行数 | 效果 |
|--------|------|------|------|------|
| 🔴 P0 | `core/planner.py` | `_plan()`: 有明确路径时不注入 CWD | ~365-372 | **修复根因** |
| 🟡 P1 | `core/gatekeeper.py` | `_formulate_directive()`: 要求 intent 保留路径 | ~423-441 | 防漂移 |
| 🟡 P1 | `core/protocol.py` | Directive/TaskSpec 加 `user_request` 字段 | ~190, ~170 | 锚点 |
| 🟡 P1 | `core/gatekeeper.py` | `_validate_delivery()`: 路径精确对比 | ~830-898 | 门禁加固 |
| 🟢 P2 | `core/worker.py` | `_build_system_prompt()`: 展示锚点 | ~598-632 | 兜底 |

---

## 最少改动方案（如果只能改一处）

只改 **修改 1**（`planner.py` CWD 注入）就能解决大部分问题。因为：

1. 用户的 `goal` 字符串本身在整个链路中**是原样保留的**（Directive.goal → TaskSpec.goal → Worker system prompt）
2. 问题出在 Planner 注入的 CWD 信息**覆盖/混淆**了 goal 中用户指定的路径
3. 去掉 CWD 注入后，Worker 只能依赖 goal 中的路径信息，自然就会分析正确的目标

建议先实施修改 1，然后逐步加上修改 2-5 做纵深防御。
