# Janus 当前所有面向用户输出点位调查

> 调查日期：2026-07-17
> 调查范围：`main.py`、`core/console.py`、`core/gatekeeper.py`、`core/planner.py`、`core/worker.py`
> 产出按用户看到的时间顺序组织。

---

## 一、启动阶段

### 1.1 启动欢迎 Banner

| 项目 | 内容 |
|------|------|
| **文件:行号** | `main.py:245-261` |
| **控制方** | `main()` → `print(_WELCOME)` |
| **触发条件** | 非 quiet 模式（`console.is_quiet == False`） |
| **简洁/冗余** | **冗余**——ASCII 艺术框 + 4 个示例 + 命令说明，占 17 行 |

```text
╔══════════════════════════════════════════════════════════════╗
║           Janus — 多轮对话 Agent 框架                        ║
╠══════════════════════════════════════════════════════════════╣
║  只需用自然语言描述你的需求，Janus 会自动拆解并执行。         ║
║                                                            ║
║  示例:                                                      ║
║    > 帮我写一个排序 CSV 文件的 Python 脚本                    ║
║    > 在 ./my-app 下创建 README.md                            ║
║    > 找出代码中所有的 TODO 注释并列出                         ║
║    > 嗨，你能帮我做什么？                                      ║
║                                                            ║
║  命令:                                                      ║
║    help / h / ?  — 显示此帮助信息                            ║
║    quit / exit / q  — 退出 Janus                             ║
╚══════════════════════════════════════════════════════════════╝
```

### 1.2 配置文件错误退出

| 项目 | 内容 |
|------|------|
| **文件:行号** | `main.py:143-145`（文件未找到）`main.py:145`（YAML 解析失败）`main.py:150`（环境变量未设置）`main.py:157-160`（缺少 model 段）`main.py:167-170`（缺少 api_key） |
| **控制方** | `main()` → `sys.exit()` |
| **触发条件** | 配置文件缺失、格式错误、必填字段缺失 |
| **简洁/冗余** | **简洁**——一行错误信息直接退出 |

```text
Config file not found: C:\...\config.yaml
Expected config.yaml in the same directory as main.py.
```
```text
Failed to parse config.yaml: <YAML error>
```
```text
Environment variable 'DEEPSEEK_API_KEY' is not set. Required by config value: '${DEEPSEEK_API_KEY}'
```
```text
config.yaml is missing the required 'model' section.
Expected keys: model.model, model.api_key
```
```text
config.yaml is missing 'model.api_key'.
Set it to your DeepSeek API key (or use ${DEEPSEEK_API_KEY}).
```

### 1.3 CLI 冲突提示

| 项目 | 内容 |
|------|------|
| **文件:行号** | `main.py:198` |
| **控制方** | `main()` → `print()` |
| **触发条件** | `--verbose` 和 `--quiet` 同时指定 |
| **简洁/冗余** | **简洁**——一行警告 |

```text
⚠️  同时指定了 --verbose 和 --quiet，使用 --verbose（详细模式）。
```

---

## 二、帮助信息

### 2.1 CLI `--help` 输出

| 项目 | 内容 |
|------|------|
| **文件:行号** | `main.py:182-195` |
| **控制方** | `main()` → `print()` → `sys.exit(0)` |
| **触发条件** | `--help` 或 `-h` 命令行参数 |
| **简洁/冗余** | **简洁**——用法 + 选项 + 示例，约 12 行 |

```text
用法: python main.py [选项]

选项:
  --verbose, -v    显示详细执行日志（含模型思考过程）
  --quiet, -q      仅显示最终结果，隐藏中间步骤
  --help, -h       显示此帮助信息

示例:
  python main.py                    默认模式
  python main.py --verbose           详细模式
  python main.py --quiet             静默模式
```

### 2.2 REPL 内 `help` 命令

| 项目 | 内容 |
|------|------|
| **文件:行号** | `main.py:262-282`（`_HELP_TEXT`），触发在 `main.py:304-306` |
| **控制方** | `main()` → `print(_HELP_TEXT)` |
| **触发条件** | 用户输入 `help` / `h` / `?` |
| **简洁/冗余** | **适中**——分隔线 + 功能说明 + 快速上手 + 技巧，约 18 行 |

```text
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Janus 帮助
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Janus 是一个多轮对话 AI Agent 框架。它会将你的目标
  拆解为可执行的任务并逐一完成。

  快速上手：
    • 用自然语言描述任何目标 — Janus 会处理剩下的事。
    • 想闲聊？直接说「你好」或问个问题即可。
    • 输入 'quit' 或按 Ctrl+C 退出。

  技巧：
    • 描述越具体，执行越精准 —「写个脚本」可以，
      「在 ./scripts/ 下写一个读取 data.csv 并输出统计
      结果的 Python 脚本」效果更好。
    • Janus 会读写真实文件 — 执行完后检查你的项目目录。
    • 使用 --verbose 查看详细执行日志。
```

---

## 三、REPL 交互循环

### 3.1 提示符

| 项目 | 内容 |
|------|------|
| **文件:行号** | `main.py:291` |
| **控制方** | `main()` → `input("\n> ")` |
| **触发条件** | 每次等待用户输入 |
| **简洁/冗余** | **极简洁**——仅 `>` 提示符 |

```text
> 
```

### 3.2 空输入提示

| 项目 | 内容 |
|------|------|
| **文件:行号** | `main.py:302` |
| **控制方** | `main()` → `print()` |
| **触发条件** | 用户输入空行 |
| **简洁/冗余** | **简洁**——一行提示，防止用户困惑 |

```text
  (输入 'help' 查看帮助，'quit' 退出，或按 Ctrl+C 强制中断)
```

### 3.3 正常退出

| 项目 | 内容 |
|------|------|
| **文件:行号** | `main.py:293`（Ctrl+C/EOF），`main.py:297`（quit/exit/q） |
| **控制方** | `main()` → `print()` |
| **触发条件** | 用户输入 `quit` / `exit` / `q`，或按 Ctrl+C / EOF |
| **简洁/冗余** | **极简洁**——仅一行 |

```text
👋 再见！
```

### 3.4 任务中 Ctrl+C 中断

| 项目 | 内容 |
|------|------|
| **文件:行号** | `main.py:313-316` |
| **控制方** | `main()` → `print()` |
| **触发条件** | 任务执行过程中按 Ctrl+C（由 `session.handle()` 抛出） |
| **简洁/冗余** | **简洁**——两行 |

```text
⚠ 任务被中断（按了 Ctrl+C）。
  输入新指令继续，或 'quit' 退出。
```

### 3.5 运行时异常

| 项目 | 内容 |
|------|------|
| **文件:行号** | `main.py:322-323` |
| **控制方** | `main()` → `print()` |
| **触发条件** | `session.handle()` 抛出非 KeyboardInterrupt 的异常 |
| **简洁/冗余** | **简洁**——两行 |

```text
❌ 出错了: <exception message>
  请检查网络连接和 API 密钥，然后重试。
```

### 3.6 非任务回复（Chat 模式）输出

| 项目 | 内容 |
|------|------|
| **文件:行号** | `main.py:310-311` |
| **控制方** | `main()` → `print(answer)` |
| **触发条件** | Gatekeeper 判定为 chat 时，直接输出 LLM 回复 |
| **简洁/冗余** | **不可控**——直接透传 LLM 原始输出，长短由 LLM 决定 |

```text
<LLM 的聊天回复文本>
```

---

## 四、任务执行过程（默认/详细模式可见）

> 以下输出由 `Console` 类（`core/console.py`）统一管理，受 `--verbose` / `--default` / `--quiet` 控制。

### 4.1 分解阶段：Gatekeeper 分析结果

| 项目 | 内容 |
|------|------|
| **文件:行号** | `console.py:148-149`（`phase_decompose()`），调用在 `planner.py:174` |
| **控制方** | `Planner._plan()` → `console.phase_decompose()` |
| **触发条件** | Planner 成功分解出 ≥ 1 个子任务；quiet 模式下不显示 |
| **简洁/冗余** | **简洁**——一行标题 + 任务列表 |

```text
🔍 Gatekeeper 分析完成，拆分为 3 个子任务：
  ✓ task-1 · 实现阶乘函数
  ✓ task-2 · 编写单元测试
  ✓ task-3 · 验证测试通过
```

### 4.2 任务开始：任务框打开

| 项目 | 内容 |
|------|------|
| **文件:行号** | `console.py:153-172`（`task_start()`），调用在 `planner.py:202` |
| **控制方** | `Planner` dispatch 循环 → `console.task_start()` |
| **触发条件** | 每个子任务开始执行时；quiet 模式下不显示 |
| **简洁/冗余** | **简洁**——一行带框的任务标题，宽度自适应（40-90 列） |

```text
┌─ task-1 · 实现阶乘函数 ───────────────────────────────────────┐
```

### 4.3 工具调用摘要

| 项目 | 内容 |
|------|------|
| **文件:行号** | `console.py:196-207`（`tool_call()`），调用在 `worker.py:561-564` |
| **控制方** | `Worker._execute_loop()` → `console.tool_call()` |
| **触发条件** | Worker 每次调用工具时；quiet 模式下不显示 |
| **简洁/冗余** | **简洁**——一行，中文标签 + 参数摘要 |

```text
│  ⚡ 写入文件: C:\project\factorial.py
│  ⚡ 执行命令: python factorial.py
│  ⚡ 读取文件: C:\project\test_factorial.py
│  ⚡ 搜索: factorial python
│  ⚡ 提取网页: https://docs.python.org/3/library/math.html
│  ⚡ 修改文件: C:\project\factorial.py
│  ⚡ 搜索文件: *.py
│  ⚡ 浏览器导航: https://example.com
│  ⚡ 调用工具: unknown_tool: arg_summary
```

> 工具名称映射（`console.py:38-48` `_TOOL_LABELS`）：
> - `write_file` → `写入文件`
> - `read_file` → `读取文件`
> - `terminal` → `执行命令`
> - `web_search` → `搜索`
> - `web_extract` → `提取网页`
> - `execute_code` → `执行代码`
> - `patch` → `修改文件`
> - `search_files` → `搜索文件`
> - `browser_navigate` → `浏览器导航`
> - 其他工具 → `调用工具: <原始名>`

### 4.4 工具调用完整参数（仅 verbose 模式）

| 项目 | 内容 |
|------|------|
| **文件:行号** | `console.py:209-224`（`tool_call_verbose()`），调用在 `worker.py:565-568` |
| **控制方** | `Worker._execute_loop()` → `console.tool_call_verbose()` |
| **触发条件** | verbose 模式下每次工具调用 |
| **简洁/冗余** | **冗余**（但 deliberate）——显示完整 arguments dict，调试用 |

```text
│     └─ 完整参数: {'path': 'factorial.py', 'content': 'def factorial(n): ...'}
```

### 4.5 Reviewer 审核通过

| 项目 | 内容 |
|------|------|
| **文件:行号** | `console.py:228-248`（`review_pass()`），调用在 `planner.py:548` / `planner.py:636-640` |
| **控制方** | `Planner._dispatch_with_review()` → `console.review_pass()` |
| **触发条件** | Reviewer 判定 APPROVED / APPROVED_WITH_NOTES，或 MINOR_REVISIONS 重试后；quiet 不显示 |
| **简洁/冗余** | **适中**——审核中+通过+证据项（最多 5 条） |

```text
│
│  🔍 Reviewer 审核中...
│  ✅ 通过
│     ✓ 函数正确地计算了 5! = 120
│     ✓ 代码包含类型提示和文档字符串
│     ✓ 边界条件 n=0 返回 1
```

> 证据超过 5 条时追加：
> ```text
> │     ... 还有 3 项
> ```

### 4.6 Reviewer 审核失败 + 触发重试

| 项目 | 内容 |
|------|------|
| **文件:行号** | `console.py:250-270`（`review_fail()`），调用在 `planner.py:567/617-620/655-659`，`worker.py:822-826` |
| **控制方** | `Planner._dispatch_with_review()` 或 `Worker._review_sub_result()` → `console.review_fail()` |
| **触发条件** | Reviewer 判定 MINOR_REVISIONS / MAJOR_REVISIONS / REJECTED；quiet 不显示 |
| **简洁/冗余** | **适中**——审核中+未通过+问题列表（最多 5 条）+重试提示 |

```text
│
│  🔍 Reviewer 审核中...
│  ❌ 未通过
│     ✗ 函数未处理负数输入
│     ✗ 缺少文档字符串
│  🔄 第 1 次重试...
```

> 问题超过 5 条时追加：
> ```text
> │     ... 还有 3 个问题
> ```

### 4.7 任务完成

| 项目 | 内容 |
|------|------|
| **文件:行号** | `console.py:174-192`（`task_done()`），调用在 `planner.py:226-228` / `worker.py:709-713` |
| **控制方** | `Planner` dispatch 循环 → `console.task_done()` / Worker sub-review |
| **触发条件** | 每个子任务执行完毕时；quiet 模式下不显示 |
| **简洁/冗余** | **简洁**——耗时 + 任务框关闭 + 状态行 |

**成功：**
```text
│  ⏱ 耗时 3.1s
└──────────────────────────────────────────────────────┘
  ✅ task-1 · 通过
```

**失败：**
```text
│  ⏱ 耗时 2.5s
└──────────────────────────────────────────────────────┘
  ❌ task-1 · 失败
```

**需分解：**
```text
│  ⏱ 耗时 1.8s
└──────────────────────────────────────────────────────┘
  🔄 task-1 · 需分解
```

### 4.8 内部错误

| 项目 | 内容 |
|------|------|
| **文件:行号** | `console.py:274-283`（`error()`） |
| **控制方** | 目前代码中无调用点（已定义但未被使用） |
| **触发条件** | 无 |
| **简洁/冗余** | **简洁**——一行 emoji + 阶段名 + 错误信息 |

```text
⚠️ 分解 出错: LLM returned unparseable response
```

### 4.9 思考块（仅 verbose 模式）

| 项目 | 内容 |
|------|------|
| **文件:行号** | `console.py:287-304`（`think_block()`） |
| **控制方** | 目前代码中无调用点（已定义但未被使用） |
| **触发条件** | verbose 模式 + Gatekeeper/Planner 显式传递 reasoning |
| **简洁/冗余** | **冗余**（但 deliberate）——截断到 500 字符 |

```text
💭 <LLM reasoning content，最多 500 字符>
```

### 4.10 静默模式 Pulse（仅 quiet 模式）

| 项目 | 内容 |
|------|------|
| **文件:行号** | `console.py:308-320`（`working_pulse()`），调用在 `planner.py:179` |
| **控制方** | `Planner.execute()` → `console.working_pulse()` |
| **触发条件** | quiet 模式下 Planner 开始 dispatch 循环时 |
| **简洁/冗余** | **极简洁**——一行，防止用户以为程序卡死 |

```text
⏳ 思考中...
```

---

## 五、最终结果汇报

### 5.1 Console 摘要（默认模式）

| 项目 | 内容 |
|------|------|
| **文件:行号** | `console.py:324-342`（`summary()`），调用在 `planner.py:1139-1140` |
| **控制方** | `Planner._summarize()` → `console.summary()` |
| **触发条件** | 每次 Planner 完成所有任务 dispatch 后 |
| **简洁/冗余** | **简洁**——分隔线 + 一行汇总 |

```text
━━━━━━━━━━━━━━━━━━━━ 汇总 ━━━━━━━━━━━━━━━━━━━━
  ✅ 全部通过: 3/3
```

```text
━━━━━━━━━━━━━━━━━━━━ 汇总 ━━━━━━━━━━━━━━━━━━━━
  ✅ 通过: 2/3  |  ❌ 失败: 1/3
```

### 5.2 Console 摘要（quiet 模式）

| 项目 | 内容 |
|------|------|
| **文件:行号** | `console.py:333-337` |
| **控制方** | `console.summary()` |
| **触发条件** | quiet 模式下的最终汇总 |
| **简洁/冗余** | **极简洁**——一行 |

```text
完成。3/3 个任务通过。
```

```text
完成。2/3 通过，1 失败。
```

### 5.3 Gatekeeper 最终汇报：全部成功

| 项目 | 内容 |
|------|------|
| **文件:行号** | `gatekeeper.py:931-968`（`_report_to_user()`），调用在 `gatekeeper.py:400` |
| **控制方** | `Gatekeeper._execute_via_planner()` → `_report_to_user()` → `main.py:311` `print()` |
| **触发条件** | 所有任务通过 |
| **简洁/冗余** | **适中**——emoji + 汇总 + 细节列表 |

```text
✅ Janus 汇报：3个任务全部完成
  Completed: 3/3 tasks.
  ✅ worker-0: 函数正确实现了阶乘计算
  ✅ worker-1: 测试已编写并通过
  ✅ worker-2: 项目目录结构完整
```

### 5.4 Gatekeeper 最终汇报：全部失败

| 项目 | 内容 |
|------|------|
| **文件:行号** | `gatekeeper.py:954-989` |
| **控制方** | `Gatekeeper._report_to_user()` → `print()` |
| **触发条件** | 所有任务失败 |
| **简洁/冗余** | **适中偏冗余**——emoji + 汇总 + 细节 + 4 条建议 |

```text
❌ Janus 汇报：3个任务全部失败
  Completed: 0/3 tasks, 3 failed
  ── 失败详情 ──
  ❌ worker-0 — Worker crashed: ConnectionError

💡 你可以尝试：
  • 输入更具体的需求（包含文件路径、期望的输出格式）
  • 将复杂任务拆分成多个小步骤逐步执行
  • 检查任务是否依赖 web 搜索或浏览器（当前未启用）
  • 使用 'help' 命令查看使用示例和技巧
```

### 5.5 Gatekeeper 最终汇报：部分成功

| 项目 | 内容 |
|------|------|
| **文件:行号** | `gatekeeper.py:958-989` |
| **触发条件** | 部分任务失败 |
| **简洁/冗余** | **适中**——emoji + 汇总 + 细节 + 3 条建议 |

```text
⚠️ Janus 汇报：2个任务完成，1个失败
  Completed: 2/3 tasks., 1 failed
  ✅ worker-0: 函数正确实现
  ❌ worker-1: Worker crashed: ConnectionError
  ── 失败详情 ──
  ❌ worker-1 — Worker crashed: ConnectionError

💡 你可以尝试：
  • 输入更具体的需求（包含文件路径、期望的输出格式）
  • 将复杂任务拆分成多个小步骤逐步执行
  • 检查任务是否依赖 web 搜索或浏览器（当前未启用）
```

### 5.6 Gatekeeper 最终汇报：零任务（无法分解）

| 项目 | 内容 |
|------|------|
| **文件:行号** | `gatekeeper.py:932-941` |
| **触发条件** | Planner 无法分解出任何子任务 |
| **简洁/冗余** | **适中**——无 emoji + 原因 + 4 条建议 |

```text
Janus 汇报：任务未能执行。
原因：LLM returned unparseable response. Check API key and balance.

💡 建议：
  • 尝试把目标拆分成更小的步骤重新描述
  • 检查是否包含无法执行的操作（如需要网络但无网络连接）
  • 尝试用更具体的语言重新描述你的需求
  • 输入 'help' 查看使用示例
```

### 5.7 Gatekeeper 最终汇报：dispatch 失败

| 项目 | 内容 |
|------|------|
| **文件:行号** | `gatekeeper.py:392-398` |
| **触发条件** | report 为空且 Planner 有 `_last_error` |
| **简洁/冗余** | **简洁**——emoji + 一行原因 |

```text
❌ Janus 汇报：任务执行失败。
原因：Worker factory crashed for task task-1: ConnectionError
```

---

## 六、汇总统计

### 6.1 输出模式总览

| 模式 | `--quiet` | `--default` | `--verbose` |
|------|-----------|-------------|-------------|
| 启动 Banner | ❌ | ✅ | ✅ |
| 分解阶段 | ❌ | ✅ | ✅ |
| 任务框 | ❌ | ✅ | ✅ |
| 工具调用 | ❌ | ✅ | ✅ |
| 工具调用完整参数 | ❌ | ❌ | ✅ |
| Reviewer 审核 | ❌ | ✅ | ✅ |
| 思考块 | ❌ | ❌ | ✅ |
| 静默 pulse | ✅ | ❌ | ❌ |
| 最终汇总 | 一行 | 带框三行 | 带框三行 |
| 最终汇报 | ✅ | ✅ | ✅ |

### 6.2 输出源头分布

| 源文件 | 输出点数 | 说明 |
|--------|----------|------|
| `main.py` | 10 | REPL 入口、退出、异常、帮助、Chat 回复 |
| `core/console.py` | 9 | 所有任务框架输出（phase、task、tool、review、summary） |
| `core/gatekeeper.py` | 5 | 最终汇报格式（5 种场景） |
| `core/planner.py` | 1 | Console 控制调用（非输出文本本身） |
| `core/worker.py` | 1 | Console 控制调用（非输出文本本身） |

### 6.3 简洁性评估

| 评估 | 点数 | 代表输出 |
|------|------|----------|
| **极简洁**（≤ 1 行） | 8 | 提示符 `>`、退出 `👋 再见！`、quiet summary、pulse |
| **简洁**（2-5 行） | 6 | 配置错误、任务框、工具调用、任务完成、异常 |
| **适中**（5-15 行） | 5 | help 命令、审核通过/失败、最终汇报各类场景 |
| **冗余**（15+ 行） | 2 | 启动 Banner（17 行）、verbose 完整参数 |

### 6.4 未使用的 Console 方法

`console.py` 中有 2 个方法已定义但当前无调用点：

| 方法 | 行号 | 设计用途 |
|------|------|----------|
| `error()` | `console.py:274-283` | 显示内联错误信息 |
| `think_block()` | `console.py:287-304` | 显示 LLM 推理过程（verbose 模式） |

---

## 七、改进建议

1. **启动 Banner**：最显眼的冗余点。17 行的 ASCII 框可精简为 3-4 行，或提供 `--no-banner` 选项。
2. **恢复循环输出**：Gatekeeper 的 recovery 循环（diagnose → reformulate → retry）对用户完全不可见，用户只知道"等了很久再出结果"。可考虑添加 `⏳ 正在恢复...` 类型的 pulse。
3. **`error()` 方法**：已定义但未使用，可在 Planner 分解失败、Worker 崩溃时统一调用。
4. **`think_block()` 方法**：已定义但未使用，Gatekeeper 使用了 DeepSeek 的 `extra_body={"thinking": {"type": "enabled"}}` 但未将 reasoning 内容传递给 Console。
5. **最终汇报的双重显示**：`console.summary()` 和 `gatekeeper._report_to_user()` 信息有重叠（都显示通过/失败数量），可考虑合并或去重。
