# Janus CLI 启动信息设计

> 设计目标：追求极简。每多一行信息都必须有充分的用户价值。
> 基于对 `main.py`、`config.yaml`、`core/` 全部模块、`docs/` 全部文档的完整审读。

---

## 一、当前启动信息（main.py:250-253）

```text
Janus · AI Agent 框架
输入「help」了解用法，「quit」退出。
```

**评价：已做到接近极简，2 行。** 但有一个信息缺口——用户看不到配置状态。

---

## 二、逐条信息价值评估

以「启动时用户最需要知道什么」为核心判断标准，按优先级排序。

### P0 — 必须展示（缺了用户会困惑）

| 信息 | 当前状态 | 价值 | 行数 |
|------|----------|------|:----:|
| **身份标识**（这是什么） | ✅ 已有：「Janus · AI Agent 框架」 | 新用户需要知道这是什么工具 | 1 |
| **入口指引**（下一步做什么） | ✅ 已有：「输入 help/quit」 | 防止用户面对空白提示符不知所措 | 1 |
| **配置加载确认**（config 是否正确） | ❌ 缺失 | 用户改了 config 后无法确认是否生效；模型选错会导致行为差异 | 0→1 |

**P0 缺口的严重性：** config.yaml 加载失败时 Janus 会 `sys.exit()` 并给出明确错误——这是好的。但加载成功时用户得不到任何确认。用户改了 `config.yaml` 中的模型，重新启动，看不到任何变化提示——他们只能通过实际执行一个任务来"猜测"配置是否生效。这不是极简，这是信息不足。

### P1 — 建议展示（有明显用户价值）

| 信息 | 价值 | 代价 |
|------|------|------|
| **当前使用的模型** | 模型影响成本、能力、行为。Janus 支持异构模型（Gatekeeper 用 `deepseek-v4-pro`，Worker 用 `deepseek-v4-flash`），用户有权知道。 | 1 行或与配置行合并 |
| **可用工具数量** | 工具数 = Worker 能力边界。9 个工具意味着能读写文件、执行命令、搜索网页。这是能力摘要。 | 可合并到同一行 |

### P2 — 不必展示（help 或 verbose 里更合适）

| 信息 | 理由 |
|------|------|
| **工具详细列表** | 太长（9 个工具名），启动屏不应做目录。放入 `help` |
| **Gatekeeper / Worker 模型分别列出** | 内部架构细节，用户不需要在启动时区分"军师模型"和"执行模型" |
| **版本号** | 排查问题时有用，启动时是噪音。放入 `help` 或 `--version` |
| **项目路径 / config 路径** | 内部实现细节 |

### P3 — 明确不展示（有负面影响）

| 信息 | 理由 |
|------|------|
| **API 连通性检测** | 增加 500ms-2s 启动延迟；瞬断会导致启动失败；首次任务调用自然会暴露问题；错误消息已足够清晰（main.py:322-323） |
| **ASCII 艺术框 / 装饰线** | 纯噪音，违反极简原则。当前代码已移除（之前是 17 行 banner） |
| **示例 / 命令列表** | 这是 `help` 的职责，不应在每次启动时重复 |
| **"加载中…" spinner** | 启动应该瞬间完成；如果需要加载时间说明架构有问题 |
| **Gatekeeper/Planner/Worker 角色说明** | 用户不需要知道内部架构来使用 Janus |

---

## 三、推荐方案

### 方案 A：合并到配置行（推荐）

```text
Janus · deepseek-v4-pro · 9 工具
输入 help 了解用法，quit 退出。
```

**2 行，信息密度极大化。** 第一行同时完成三个任务：身份标识 + 模型确认 + 能力摘要。

**理由：**
- 「AI Agent 框架」对老用户是噪音，对首次用户有价值。用模型名替代——首次用户可以用 `help` 了解 Janus 是什么，老用户直接看到有用的配置信息。
- `deepseek-v4-pro` 确认当前使用的模型。
- `9 工具` 暗示能力边界——用户知道 Worker 能做文件读写、命令执行、网页搜索等。

### 方案 B：保留「AI Agent 框架」，追加配置行

```text
Janus · AI Agent 框架
deepseek-v4-pro · 9 工具
输入 help 了解用法，quit 退出。
```

**3 行。** 身份标识 + 配置确认各行其职，不混在一起。

**理由：** 「AI Agent 框架」对首次用户有不可替代的认知锚定作用。删掉它，新用户面对 `Janus · deepseek-v4-pro · 9 工具` 不知道这是什么。老用户看第二行即可，视线自然跳过第一行。

### 方案对比

| 维度 | 方案 A（2 行） | 方案 B（3 行） |
|------|:---:|:---:|
| 行数 | 2 | 3 |
| 首次用户体验 | 需通过 `help` 了解 Janus 是什么 | 一目了然 |
| 老用户体验 | 一行获得全部配置信息 | 第一行是噪音，但可视线跳过 |
| 信息完整性 | 缺失「AI Agent 框架」定位 | 完整 |
| 可 grep 性 | 模型名和工具数在同一行 | 各有独立行 |

**推荐：方案 B。** 多一行换来的「首次用户认知锚定」价值远大于一行终端空间。老用户对第一行的「AI Agent 框架」会形成视觉惯性，自然聚焦第二行。

---

## 四、配置行的显示逻辑

### 4.1 模型显示

```python
def _format_model_line(cfg: dict) -> str:
    """从 config 提取模型信息，一行展示。"""
    model_cfg = cfg.get("model", {})
    provider = model_cfg.get("provider", "?")
    model = model_cfg.get("model", "?")

    gatekeeper_model = cfg.get("gatekeeper", {}).get("model", model)
    worker_model = cfg.get("worker", {}).get("model")

    if worker_model and worker_model != gatekeeper_model:
        # 异构模型：显示主模型即可，Worker 模型是内部细节
        pass

    return f"{provider}/{gatekeeper_model}"
```

**显示规则：**
- 始终显示 Gatekeeper 实际使用的模型（从 `config.yaml` 的 `gatekeeper.model` 或 fallback 到 `model.model`）
- 不区分显示 Gatekeeper vs Worker 模型——用户在启动时不需要关心内部角色分工
- Provider 前缀提供可辨识性（`deepseek/deepseek-v4-pro` vs 裸 `deepseek-v4-pro`）

### 4.2 工具计数

```python
from core.worker import create_default_registry

def _count_tools() -> int:
    registry = create_default_registry()
    return len(registry.get_openai_schemas())
```

**显示规则：**
- 启动时加载 `ToolRegistry` 并计数
- 只显示数量，不列名称
- 格式：「9 工具」或「9 个工具」

### 4.3 颜色和样式

遵循 `janus-cli-aesthetic-design.md` 五色调色板：

```
Janus · AI Agent 框架          ← 白色/默认（正文）
deepseek-v4-pro · 9 工具        ← 暗化/灰色（次要信息，dim）
输入 help 了解用法，quit 退出。   ← 白色/默认
```

- 第一行加粗（`\033[1m`）：身份标识需要突出
- 第二行暗化（`\033[2m`）：配置信息是确认性信息，不应抢夺视线
- 第三行保持默认

**效果预览（带颜色）：**
```
**Janus · AI Agent 框架**          ← 加粗
deepseek-v4-pro · 9 工具           ← 暗化灰色
输入 help 了解用法，quit 退出。
```

### 4.4 异常处理

| 场景 | 显示 | 行为 |
|------|------|------|
| config.yaml 缺失 | `sys.exit("Config file not found: ...")` | 退出，不进入 REPL |
| model 段缺失 | `sys.exit("config.yaml is missing...")` | 退出，不进入 REPL |
| api_key 缺失 | `sys.exit("config.yaml is missing...")` | 退出，不进入 REPL |
| 模型名为空字符串 | 显示 `未知模型` | 允许进入 REPL（让首次任务调用报错） |
| ToolRegistry 加载失败 | 显示 `? 工具` | 允许进入 REPL（Worker 无工具可用） |

**原则：** 致命配置错误已在 `main.py:140-170` 覆盖，以 `sys.exit()` 终止。显示层的容错只处理非致命退化——即使模型名解析失败，REPL 也应该可用。

---

## 五、对比：其他知名 CLI 工具启动行为

| 工具 | 启动输出 | 行数 | 特点 |
|------|----------|:---:|------|
| `python` | `Python 3.13.0 ...` + `>>> ` | 2 | 版本+提示符，无多余信息 |
| `node` | 无输出，直接 `> ` | 0 | 绝对静默，REPL 自己探索 |
| `psql` | `psql (17.0) ... Type "help" for help.` | 1 | 身份+版本+入口 |
| `ghci` | `GHCi, version 9.4.8: https://...  :? for help` | 2 | 身份+版本+入口 |
| **Janus 方案 B** | 身份 + 配置 + 入口 | 3 | 身份+配置确认+入口 |

Janus 比这些工具多一行配置确认，因为：
1. Janus 的行为高度依赖模型选择（不同模型能力差异大）
2. 配置在外部 YAML 文件中，不是命令行参数——用户无法从启动命令看到
3. 其他工具的行为由版本号决定（版本号固定），Janus 的行为由 config 决定（config 可变）

---

## 六、实现要点

### 6.1 代码位置

`main.py` 的 `main()` 函数中，`_WELCOME` 常量（约第 250 行）替换为动态生成。

### 6.2 伪代码

```python
def _build_welcome(cfg: dict, tool_count: int) -> str:
    model_name = _resolve_model_display(cfg)  # e.g. "deepseek-v4-pro"
    return (
        f"\033[1mJanus · AI Agent 框架\033[0m\n"
        f"\033[2m{model_name} · {tool_count} 工具\033[0m\n"
        f"输入 help 了解用法，quit 退出。\n"
    )
```

### 6.3 注意事项

- 颜色代码在非 TTY 环境自动去除（`console.py` 已有 `_supports_color()` 机制）
- `--no-color` 标志应禁用配置行的暗化效果
- `--quiet` 模式下是否显示启动信息？当前设计 `--quiet` 跳过 welcome。建议保持此行为——quiet 模式意味着「给我最少输出」

---

## 七、设计决策总结

| 决策 | 结论 | 理由 |
|------|------|------|
| 启动信息行数 | 3 行（方案 B） | 2 行太紧（丢失身份标识），4 行太松 |
| 是否显示模型 | 是 | 配置可变，用户需要确认 |
| 是否显示工具数 | 是 | 一行内零成本添加的能力摘要 |
| 是否检测 API 连通性 | 否 | 增加延迟，首次任务调用自然暴露 |
| 是否显示异构模型 | 否 | 内部架构细节，非用户关心 |
| 是否显示版本号 | 否 | 放入 `help` 或 `--version` |
| 是否显示 ASCII 艺术 | 否 | 纯噪音，已移除 |
| quiet 模式是否显示 | 否（保持现状） | quiet 的语义是「最少输出」 |

---

*本文档基于 Janus main.py (Phase 4)、config.yaml、core/ 全部模块、docs/ 全部设计文档综合分析。结合 `janus-cli-aesthetic-design.md` 的极简原则，在 2 行现状基础上增加 1 行配置确认，不做更多扩展。*
