# Janus CLI 可观测性与 UX 设计方案

> 版本：v1.0  
> 日期：2026-07-16  
> 状态：设计提案（待评审）

---

## 1. 现状分析

### 1.1 当前输出（用户可见）

```
> 写一个Python函数

Completed: 1/1 tasks.
  [success] Created a Python module factorial.py...
```

### 1.2 当前不可见但关键的信息

| 阶段 | 发生什么 | 用户是否看到 |
|------|---------|:----------:|
| Gatekeeper 分解目标 | LLM 分析目标 → 产出 N 个 TaskSpec | :x: 完全不可见 |
| Worker 开始执行 | 创建 Worker 实例 → 收到 TaskSpec | :x: |
| Worker 调用工具 | `write_file` / `read_file` / `run_command` 等 | :x: |
| Reviewer 审核 | 对照验收标准逐条检查 → pass/fail | :x: |
| 审核失败 → 重试 | 注入反馈 → Worker 重试（最多 3 次） | :x: |
| 各阶段耗时 | 分解花了多久？Worker 执行花了多久？ | :x: |
| 错误原因 | 为什么失败？网络？API 余额？工具崩溃？ | :x: |

### 1.3 当前日志系统

所有 `logger.info/warning/exception` 调用默认不输出到终端。用户要看到这些信息必须手动配置 Python logging，体验约等于没有可观测性。

---

## 2. 设计目标

1. **默认模式足够用**：用户无需 `--verbose` 就能看懂 Janus 在做什么、做到哪了、有没有问题
2. **不淹没信息**：不是把所有 logger 消息 dump 到终端，而是精心设计**信息层级**
3. **中文优先**：所有终端输出、阶段标签使用中文
4. **可读性**：用缩进、符号、颜色（如果终端支持）区分层级，而非文字墙
5. **可扩展**：未来支持 `--verbose` / `--quiet` / `--json` 模式

---

## 3. 信息分级：什么该显示，什么不该

### 3.1 核心原则

> **默认模式下，用户应该看到一个"时间线"——Janus 的每一步决策和结果，而非实现细节的日志转储。**

### 3.2 四级信息分类

| 级别 | 内容 | 默认显示 | `--verbose` | 设计理由 |
|:----:|------|:--------:|:----------:|----------|
| **L0 用户可见** | 最终答案、合成结果 | :white_check_mark: | :white_check_mark: | 这是用户要的东西 |
| **L1 阶段节点** | 分解、分发、审核、重试、完成 | :white_check_mark: | :white_check_mark: | 用户需要知道"进度"和"决策" |
| **L2 任务详情** | Worker 做了什么、工具调用摘要、review 结果 | :white_check_mark: | :white_check_mark: | 让用户验证过程合理性 |
| **L3 调试细节** | 完整 tool 输入输出、LLM raw response、堆栈 | :x: | :white_check_mark: | 仅开发者调试需要 |

### 3.3 各阶段信息价值判断

| 信息 | 价值 | 结论 |
|------|------|------|
| 分解出的子任务清单 | 很高 — 用户想确认 Janus 是否理解对了 | **默认显示** |
| Worker 正在执行哪个任务 | 高 — 进度感 | **默认显示** |
| Worker 调用了哪些工具（摘要） | 中高 — 让用户知道"它在写文件/跑命令" | **默认显示** |
| Reviewer 审核结果 (pass/fail) | 高 — 质量保证的关键环节，用户有权知道 | **默认显示** |
| 审核失败的具体原因 | 高 — 用户可能想干预 | **默认显示** |
| 重试信息 | 中 — 让用户知道不是卡住了 | **默认显示** |
| 各阶段耗时 | 中 — 帮助用户判断是否正常 | **默认显示** |
| 完整 tool 入参/出参 | 低 — 大部分用户不需要看 API 返回的 2000 行 JSON | **仅 --verbose** |
| 完整 LLM response | 极低 — 调试用 | **仅 --verbose** |
| Python traceback | 极低 — 仅开发者 | **仅 --verbose** |

---

## 4. 默认输出设计（典型执行示例）

以下是一次典型执行的终端输出示例。目标：「写一个 Python 阶乘函数，带测试」。

```
> 写一个 Python 阶乘函数，带测试

━━━ Gatekeeper 分析中... ━━━  ⏱ 2.3s
  拆分为 2 个子任务：
  ✓ task-1 · 实现阶乘函数 (factorial.py)
  ✓ task-2 · 编写单元测试

┌─ task-1 · 实现阶乘函数 ─────────────────────────────┐
│  worker-0 开始执行...                                │
│  ⚡ 写入文件: factorial.py                            │
│  ⚡ 读取文件: factorial.py (验证)                     │
│  ⏱ 执行耗时 3.1s                                    │
│                                                       │
│  🔍 Reviewer 审核中...                               │
│  ✅ 通过 (置信度: HIGH)                               │
│     ✓ 函数名 factorial，支持正整数输入                │
│     ✓ 正确处理 n=0 边界情况                          │
│     ✓ 对负数输入抛出 ValueError                     │
└───────────────────────────────────────────────────────┘

┌─ task-2 · 编写单元测试 ─────────────────────────────┐
│  worker-1 开始执行...                                │
│  ⚡ 写入文件: test_factorial.py                       │
│  🔄 首次审核未通过，第 1 次重试...                   │
│     ⚠ 问题: 缺少边界测试 (n=0, n=1)，未测试异常输入  │
│  ⚡ 修改文件: test_factorial.py                       │
│  ⏱ 执行耗时 6.8s (含 1 次重试)                      │
│                                                       │
│  🔍 Reviewer 审核中...                               │
│  ✅ 通过 (置信度: HIGH)                               │
│     ✓ 包含 n=0, n=1, n=5 的测试用例                  │
│     ✓ 测试了负数抛异常                               │
└───────────────────────────────────────────────────────┘

━━━ 汇总 ━━━  ⏱ 总耗时 12.4s
  全部通过: 2/2
  ✓ 实现阶乘函数 (factorial.py)
  ✓ 编写单元测试 (test_factorial.py)
```

### 4.1 默认输出的设计要点

- **进度可见**：用户一眼看到 2 个子任务、谁在执行、做了什么、结果如何
- **层级清晰**：缩进 + 边框框出每个任务，视觉上任务边界明确
- **时间透明**：每个阶段标注耗时，用户不会觉得"卡住了"
- **审核可见**：Reviewer 的判决和具体检查项都显示，赋予用户对质量的信心
- **重试可见**：审核失败 → 重试这个循环是 Janus 的核心价值，必须显示
- **简洁**：不显示 Worker 的完整 LLM 对话、不显示工具调用的完整 JSON 参数

---

## 5. 工具级别输出：「该不该显示工具调用？」

### 5.1 推荐：显示工具调用**摘要**，不显示完整输入输出

| 工具 | 默认显示 | --verbose |
|------|---------|-----------|
| `write_file(path, content)` | `⚡ 写入文件: factorial.py` | 同时显示 content 预览（前 120 字符） |
| `read_file(path)` | `⚡ 读取文件: data.json` | 显示读取内容摘要 |
| `run_command(cmd)` | `⚡ 执行: pytest test_factorial.py` | 显示 stdout/stderr 摘要 |
| `web_search(query)` | `⚡ 搜索: "Python factorial best practice"` | 显示返回结果摘要 |
| 其他自定义工具 | `⚡ 调用工具: <name>` | 显示完整参数 |

### 5.2 理由

- Worker 的 LLM 对话可能包含多次 tool 调用 → 全部显示会刷屏
- 用户关心的是**工具做了什么**（写了哪个文件、跑了什么命令），而非完整 JSON payload
- `--verbose` 保留完整信息，满足调试场景

---

## 6. 审核链展示：「Gatekeeper → Worker → Reviewer」

### 6.1 展示策略

审核是 Janus 最有价值的差异化功能。应该**高亮展示**，而非隐藏在日志中。

```
🔍 Reviewer 审核中...
✅ 通过 (置信度: HIGH)
   ✓ 函数正确实现了阶乘逻辑
   ✓ 正确处理 n=0 边界情况
   ✓ 代码有类型注解和文档字符串

🔍 Reviewer 审核中...
❌ 未通过
   ✗ 缺少对负数输入的异常处理
   ✗ 测试用例只覆盖了 n=5，缺少边界测试
🔄 第 1 次重试...
```

### 6.2 视觉设计

| 状态 | 图标 | 颜色（若终端支持） | 含义 |
|------|------|:---:|------|
| 审核通过 | `✅ 通过` | 绿色 | pass |
| 审核未通过 | `❌ 未通过` | 红色 | fail（触发重试） |
| 审核进行中 | `🔍 审核中...` | 黄色/灰色 | 进行中 |
| 重试 | `🔄 第 N 次重试...` | 黄色 | 重试循环 |
| 最终失败 | `⛔ 放弃` | 红色 | 重试耗尽 |

---

## 7. 错误与重试展示

### 7.1 设计原则

> **错误必须显示，且说明原因——但不能用 Python traceback 吓到用户。**

### 7.2 错误分类展示

```
# API 错误
⚠ task-1 执行失败: API 调用超时 (网络不可达)
  → 自动重试中... (1/3)

# 工具错误
⚠ task-2 执行失败: 无法写入文件 factorial.py (权限不足)
  → Worker 尝试替代方案...

# 审核失败
❌ Reviewer 未通过: 3 个问题
   ✗ 函数签名不符合验收标准
   ✗ 缺少类型注解
   ✗ 文档字符串缺失
  → 第 1 次重试 (将发现问题注入上下文)...

# 放弃
⛔ task-2 重试 3 次后仍未通过审核，放弃。
   最后问题: 函数未处理浮点数输入
```

### 7.3 重试计数显示

```
┌─ task-2 · 编写单元测试 ──────── ⏱ 15.8s ─────────────┐
│  ⚡ 写入文件: test.py                                  │
│  ❌ Reviewer 未通过 → 🔄 重试 1/3                      │
│  ⚡ 修改文件: test.py                                  │
│  ❌ Reviewer 未通过 → 🔄 重试 2/3                      │
│  ⚡ 修改文件: test.py                                  │
│  ✅ Reviewer 通过                                      │
└─────────────────────────────────────────────────────────┘
```

---

## 8. 并行执行展示

### 8.1 当前架构

当前 Gatekeeper 是**串行**分发——`for i, spec in enumerate(specs)`。但设计应考虑未来并行执行。

### 8.2 并行时的显示设计

```
> 同时构建前端 React 组件和后端 API

━━━ Gatekeeper 分析中... ⏱ 1.8s ━━━
  拆分为 4 个子任务：
  ✓ task-1 · 创建 React 组件
  ✓ task-2 · 编写 API 路由
  ✓ task-3 · 配置数据库模型
  ✓ task-4 · 编写集成测试

⚡ 并行执行 4 个任务...

┌─ task-1  React 组件 · worker-0 ═══ ⏱ 8.2s · ✅ ────┐
│  ⚡ 写入: components/UserCard.tsx                      │
│  ✅ Reviewer 通过                                     │
└───────────────────────────────────────────────────────┘

┌─ task-2  API 路由 · worker-1 ═══ ⏱ 12.1s · ✅ ────┐
│  ⚡ 写入: routes/users.py                             │
│  ⚡ 执行: pytest test_users_api.py                    │
│  ❌ Reviewer 未通过 → 🔄 重试 1/3                      │
│  ⚡ 修改: routes/users.py                             │
│  ✅ Reviewer 通过                                     │
└───────────────────────────────────────────────────────┘

┌─ task-3  数据库模型 · worker-2 ═══ ⏱ 5.4s · ✅ ──┐
│  ⚡ 写入: models.py                                    │
│  ✅ Reviewer 通过                                     │
└───────────────────────────────────────────────────────┘

┌─ task-4  集成测试 · worker-3 ═══ ⏱ 4.7s · ❌ ────┐
│  ⚡ 写入: test_integration.py                         │
│  ⛔ Worker 崩溃 (API 限流)                             │
└───────────────────────────────────────────────────────┘

━━━ 汇总 ⏱ 总耗时 15.3s ━━━
  通过: 3/4  —  失败: 1/4
  ✅ task-1 · 创建 React 组件
  ✅ task-2 · 编写 API 路由
  ✅ task-3 · 配置数据库模型
  ❌ task-4 · 编写集成测试 — Worker 崩溃 (API 限流)
```

### 8.3 并行显示要点

- 每个任务独立成一个框，完成后立即显示（不等其他任务）
- 边框标题显示「任务名 · worker-id · 最终状态」，一目了然
- 总耗时取最长子任务的完成时间
- 按完成时间顺序输出（自然流）

---

## 9. 模式切换

### 9.1 三个模式

| 模式 | CLI 标志 | 行为 |
|------|---------|------|
| **默认** | （无） | 显示 L0+L1+L2 信息，ASCII 艺术边框，彩色图标 |
| **详细** | `--verbose` / `-v` | 额外显示 L3：完整 tool 入参出参、LLM raw response、Python traceback |
| **静默** | `--quiet` / `-q` | 仅显示 L0：最终答案，无边框、无图标、无耗时 |
| **JSON** | `--json` | 所有输出为 JSON Lines，供管道/脚本消费 |

### 9.2 各模式输出对比

**默认**：
```
━━━ Gatekeeper 分析中... ⏱ 2.3s ━━━
  拆分为 2 个子任务：
  ✓ task-1 · ...
```

**`--verbose`**：
```
━━━ Gatekeeper 分析中... ⏱ 2.3s ━━━
  [DEBUG] LLM request: model=deepseek-v4-pro, tokens=342
  [DEBUG] LLM response: 2 task objects, 412 tokens
  [TRACE] Raw JSON: [{"task_id": "task-1", ...
  拆分为 2 个子任务：
  ✓ task-1 · ...
```

**`--quiet`**：
```
# 无任何中间输出，直接显示最终结果：
完成。创建了 factorial.py 和 test_factorial.py。
```

**`--json`**：
```json
{"type": "phase", "phase": "decompose", "elapsed_ms": 2300, "task_count": 2}
{"type": "task_start", "task_id": "task-1", "description": "实现阶乘函数", "worker_id": "worker-0"}
{"type": "tool_call", "task_id": "task-1", "tool": "write_file", "args": {"path": "factorial.py"}}
{"type": "review", "task_id": "task-1", "status": "pass", "confidence": "HIGH", "issues": []}
{"type": "task_end", "task_id": "task-1", "status": "success", "elapsed_ms": 3100}
{"type": "summary", "total": 2, "completed": 2, "failed": 0, "elapsed_ms": 12400}
```

---

## 10. 实现建议

### 10.1 架构：不污染 Gatekeeper/Worker 代码

不应该在 Gatekeeper 和 Worker 的核心逻辑中加 `print()`。应该引入一个 `Console` 类或回调机制：

```python
# core/console.py (新增)

class Console:
    """统一输出管理——所有终端显示通过此类发出。"""
    
    def __init__(self, mode: str = "default"):
        self._mode = mode  # default | verbose | quiet | json

    def phase(self, icon: str, message: str):
        """输出 L1 阶段节点"""
        ...

    def task_start(self, spec: TaskSpec, worker_id: str):
        """输出任务开始框"""
        ...

    def task_end(self, result: TaskResult, elapsed_ms: int):
        """输出任务结束框"""
        ...

    def tool_call(self, tool_name: str, args_preview: str):
        """输出 L2 工具调用摘要"""
        ...

    def review(self, result: ReviewResult):
        """输出审核结果"""
        ...

    def error(self, message: str):
        """输出错误"""
        ...
```

### 10.2 集成方式

```python
# main.py

@app.callback()
def main(verbose: bool = False, quiet: bool = False, json: bool = False):
    mode = "json" if json else "quiet" if quiet else "verbose" if verbose else "default"
    console = Console(mode=mode)
    gk = Gatekeeper(..., console=console)  # 或 observer 回调
```

### 10.3 优先实现顺序

1. **默认模式**（L0+L1+L2）—— 立即实现，收益最大
2. **`--verbose`** —— 第二优先，调试必需
3. **`--quiet`** —— 简单，加个 if 即可
4. **`--json`** —— 管道场景，可以和 verbose 后面一起做
5. **颜色支持** —— 检测终端 `isatty()` + ANSI 转义序列，渐进增强

---

## 11. 关键设计决策总结

| 决策 | 结论 |
|------|------|
| 工具调用是否默认显示？ | :white_check_mark: 显示**摘要**（文件名/命令），不显示完整 JSON |
| 审核详情是否默认显示？ | :white_check_mark: 显示通过/不通过 + 具体检查项，这是 Janus 核心价值 |
| 错误信息展示方式？ | 内联显示（发生即显示），不在末尾汇总 |
| 重试信息展示？ | 显示每次重试的原因和结果，给用户透明度 |
| 默认详细程度？ | 显示阶段节点 + 任务细节 + 审核结果（即 L0+L1+L2），不用 `--verbose` |
| 并行任务显示？ | 每个任务独立成框，完成后立即输出，按完成序排列 |
| 语言？ | 中文，图标辅助 |
| 实现方式？ | 新增 `Console` 类，不污染核心逻辑 |

---

## 12. 待讨论的开放问题

1. **滚动输出 vs 固定位置刷新**：在终端做类似 `tqdm` 的进度条+实时刷新，还是简单的滚动输出？滚动输出更简单、更适合容器/CI 环境；固定刷新更酷但兼容性差。建议先做滚动，后期可选。
2. **Worker 工具调用的数量阈值**：如果 Worker 调用了 30 次工具，全部显示会刷屏。建议默认只显示前 5 次，超出的折叠显示 `⚡ ... 还有 25 次工具调用`，`--verbose` 显示全部。
3. **审核检查项的上限**：Reviewer 可能返回 20 条检查项，全显示太长。默认显示前 5 条问题，超出折叠为 `... 还有 15 条（使用 --verbose 查看完整列表）`。

---

*本文档由 Janus CLI Output Design Task 生成，待评审后进入实现阶段。*
