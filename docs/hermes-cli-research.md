# Hermes Agent CLI 美学设计研究报告

> 为 Janus 项目做的竞品参考调研  
> 调研时间：2026-07-17  
> 数据来源：Hermes Agent 官方文档 (v0.15+)、GitHub 仓库、社区讨论

---

## 一、概述

Hermes Agent 的 CLI 不是简单的命令行工具，而是 **一个完整的终端用户界面（TUI）**，面向"活在终端里的人"设计。它有两条平行的交互面：

| 模式 | 启动方式 | 特点 |
|------|----------|------|
| **Classic CLI** | `hermes` / `hermes chat` | prompt_toolkit 驱动的 REPL，流式工具输出，多功能逐行展示 |
| **Modern TUI** | `hermes --tui` | Node.js 全屏 TUI，模态浮层、鼠标支持、LaTeX 渲染 |

两者共享同一套 Python 运行时、session 数据库、skills 和配置，可以随时切换。

核心理念：**一句话就能开始** — `hermes setup --portal && hermes chat`。

---

## 二、终端输出风格

### 2.1 色彩体系（Skin 系统）

Hermes 没有写死颜色，而是通过 **YAML 皮肤文件** 实现完全可配置的视觉呈现。系统由 `hermes_cli/skin_engine.py` 驱动，`~/.hermes/skins/` 下放自定义皮肤，内置 9 套预设。

**默认皮肤 `default` — 经典 Hermes 金 + kawaii 风格：**

| 颜色键 | 默认值 | 用途 |
|--------|--------|------|
| `banner_border` | `#CD7F32`（青铜） | Banner 面板边框 |
| `banner_title` | `#FFD700`（金） | Banner 标题文字 |
| `banner_accent` | `#FFBF00`（琥珀） | Banner 分区标题 |
| `banner_dim` | `#B8860B`（暗金） | Banner 辅助文字 |
| `banner_text` | `#FFF8DC`（玉米丝） | Banner 正文 |
| `ui_accent` | `#FFBF00` | 通用高亮色 |
| `ui_label` | `#4dd0e1`（青） | 标签 |
| `ui_ok` | `#4caf50`（绿） | 成功状态 |
| `ui_error` | `#ef5350`（红） | 错误状态 |
| `ui_warn` | `#ffa726`（橙） | 警告状态 |
| `prompt` | `#FFF8DC` | 输入提示符 |
| `input_rule` | `#CD7F32` | 输入区上方分割线 |
| `response_border` | `#FFD700` | 回复框边框 |
| `session_label` | `#DAA520` | Session 标签 |
| `session_border` | `#8B8682` | Session 边框 |
| `status_bar_bg` | `#1a1a2e` | 状态栏背景 |
| `completion_menu_bg` | `#1a1a2e` | 补全菜单背景 |
| `completion_menu_current_bg` | `#333355` | 补全菜单选中行 |

**设计亮点：** 
- 语义化颜色命名（`ui_ok`/`ui_error`/`ui_warn`），不是"红/绿/黄"
- 默认深色主题，白色/奶油色终端完全不可读（已有用户报 bug，后续新增 `daylight` 和 `warm-lightmode` 两套浅色皮肤）
- TUI 支持自动检测终端背景色并切换浅色主题

### 2.2 内置皮肤一览

| 皮肤 | 风格 | 品牌名 | 视觉特征 |
|------|------|--------|----------|
| `default` | 经典金 + kawaii | Hermes Agent | 金色边框、玉米丝文本、kawaii 颜文字 |
| `ares` | 战神红铜 | Ares Agent | 深红边框 + 青铜强调色、剑盾 ASCII 艺术 |
| `mono` | 极简灰度 | Hermes Agent | 全灰、`#555555` 边框、适合录屏 |
| `slate` | 冷静蓝 | Hermes Agent | 皇家蓝 `#4169e1`、专业感 |
| `daylight` | 浅色终端适配 | Hermes Agent | 深石板文本、蓝色边框、浅色补全菜单 |
| `warm-lightmode` | 暖棕羊皮纸 | Hermes Agent | 深棕文本、奶油色表面 |
| `poseidon` | 海神蓝绿 | Poseidon Agent | 深蓝 → 海沫绿渐变、三叉戟 ASCII |
| `sisyphus` | 西西弗斯灰 | Sisyphus Agent | 浅灰高对比、巨石主题 spinner |
| `charizard` | 火山橙 | Charizard Agent | 焦橙 → 余烬渐变、龙形 ASCII |

**关键设计决策：**
- 每个皮肤有独立的 **品牌名**（`agent_name`），影响 banner 标题和状态显示
- `tool_prefix` 默认 `┊`，皮肤可覆盖为 `▏` 等
- 自定义皮肤只需覆盖要改的键，其余自动继承 `default`

### 2.3 颜色语义化的状态体系

**上下文用量颜色编码：**

| 颜色 | 阈值 | 语义 |
|------|------|------|
| 绿色 | < 50% | 空间充裕 |
| 黄色 | 50–80% | 逐渐填满 |
| 橙色 | 80–95% | 接近上限 |
| 红色 | ≥ 95% | 即将溢出 — 建议 `/compress` |

这种渐进色编码是优秀的 **信息设计**：不需要精确数字，颜色本身就是行动信号。

---

## 三、版面布局

### 3.1 经典 CLI 布局

```
┌──────────────────────────────────────┐
│            BANNER（启动横幅）          │
│  - ASCII 艺术 logo                   │
│  - 模型 / 终端后端 / 工作目录          │
│  - 可用工具 / 已装技能                 │
├──────────────────────────────────────┤
│     （恢复 session 时）                │
│  "Previous Conversation" 面板         │
│  显示历史对话摘要                      │
├──────────────────────────────────────┤
│         对话流（主内容区）              │
│  - Agent 回复（带边框标签）             │
│  - 工具输出（┊ 前缀 + 图标 + 耗时）     │
│  - Thinking 动画行                     │
│  - 背景任务结果面板                    │
├──────────────────────────────────────┤
│         状态栏（持久化）                │
│  ⚕ model │ tokens │ [████░░] % │ $cost │ 时间 │ ⚠ │
├──────────────────────────────────────┤
│  ❯ 输入区                             │
│  支持多行、Tab 补全、ghost text        │
└──────────────────────────────────────┘
```

### 3.2 TUI 布局

TUI 新增：
- **可折叠 Banner 分区**：工具（默认展开）、Skills / System Prompt / MCP Servers（默认折叠），用 `▸`/`▾` chevron 控制
- `/help`、`/model`、`/sessions`、`/agents` 等以 **模态浮层** 呈现而非行内面板
- 鼠标支持：滚动、点击、选中
- LaTeX 数学公式 Unicode 渲染（`$E=mc^2$` → 排版后显示）
- `/mouse` 命令运行时切换鼠标追踪级别（wheel/buttons/all）

### 3.3 状态栏设计

持久状态栏是 Hermes CLI 最突出的 UX 设计之一：

```
⚕ claude-sonnet-4-20250514 │ 12.4K/200K │ [██████░░░░] 6% │ $0.06 │ 🗜️ 2 │ ▶ 1 │ 15m │ ⚠ YOLO
```

| 元素 | 说明 |
|------|------|
| 模型名 | 当前模型，>26 字符截断 |
| Token 数 | 已用 / 最大上下文窗口 |
| 上下文条 | 可视化进度条 + 颜色编码阈值 |
| 费用 | 估算 session 费用（未知模型显示 `n/a`） |
| 🗜️ N | 上下文自动压缩次数 |
| ▶ N | 活跃后台任务数 |
| 时长 | Session 运行时间 |
| ⚠ YOLO | YOLO 模式警告（自动批准危险操作时） |

响应式设计：≥76 列显示完整布局，52–75 列紧凑模式，<52 列最小模式（仅模型 + 时长 + YOLO 标识）。

TUI 额外增加：当前 git 分支、当前/总时间（`⏲ 32s / 3m45s`）、`⏱ 12s/3m45s`。

---

## 四、动画与动态反馈

### 4.1 Thinking 动画（Spinner 系统）

**等待 API 响应时的动画：**

```
◜ (｡•́︿•̀｡) pondering... (1.2s)
◠ (⊙_⊙) contemplating... (2.4s)
✧٩(ˊᗜˋ*)و✧ got it! (3.1s)
```

**设计要素（所有可皮肤化）：**

| 组件 | 说明 | 例子 |
|------|------|------|
| `waiting_faces` | 等待时循环的表情 | `["(⚔)", "(⛨)", "(▲)"]` |
| `thinking_faces` | 推理时循环的表情 | `["(⚔)", "(⌁)", "(<>))"]` |
| `thinking_verbs` | spinner 中的动词 | `["forging", "marching", "tempering steel"]` |
| `wings` | 装饰性括号 | `[["⟪⚔", "⚔⟫"], ["⟪▲", "▲⟫"]]` |

TUI 中有 **busy indicator 风格**（kaomoji/emoji/unicode/ascii），匹配宽度防止状态栏抖动。

### 4.2 工具执行提要

```
┊ 💻 terminal `ls -la` (0.3s)
┊ 🔍 web_search (1.2s)
┊ 📄 web_extract (2.1s)
```

- 默认前缀 `┊`，每皮肤可定制
- 每工具有专属 emoji（`tool_emojis` 字典可覆盖）
- 显示工具名、参数预览（可截断）、耗时

### 4.3 多行粘贴预览

粘贴多行文本时，不直接倒进 scrollback，而是显示精简预览：

```
[pasted: 47 lines, 1,842 chars — press Enter to send]
```

防止大段粘贴污染视觉空间。

### 4.4 Markdown 剥离

Agent 最终回复会剥离 `**bold**` / `*italic*` 标记，让终端渲染为纯文本而非 raw markdown。代码块和列表保留。不影响 gateway 平台和工具结果。

---

## 五、信息层级设计

### 5.1 Banner 启动信息

启动 Banner 一目了然展示：

```
模型、终端后端、工作目录
可用工具列表（图标 + 名称）
已安装 Skills 列表
```

**TUI 的折叠设计尤为出色：** 最常查看的"工具"默认展开，Skills/System Prompt/MCP Servers 默认折叠，保持 banner 紧凑。点击 section header 或 chevron 切换。

### 5.2 工具输出可见性控制

通过 `/verbose` 切换工具输出显示级别：

```
off → new → all → verbose
```

| 级别 | 含义 |
|------|------|
| off | 不显示工具输出 |
| new | 只显示本轮新执行的工具 |
| all | 显示所有工具调用 |
| verbose | 完整调试输出 |

TUI 有更细粒度的 `/details` 控制：`thinking`/`tools`/`subagents`/`activity` 每个 section 独立设置 `hidden`/`collapsed`/`expanded`。

### 5.3 Session 恢复信息

退出 CLI 时打印：

```
Resume this session with:
  hermes --resume 20260225_143052_a1b2c3

Session: 20260225_143052_a1b2c3
Duration: 12m 34s
Messages: 28 (5 user, 18 tool calls)
```

恢复时显示 "Previous Conversation" 面板，展示历史对话摘要。

### 5.4 背景任务结果面板

```
╭─ ⚕ Hermes (background #1) ──────────────────────────────────╮
│ Found 3 errors in syslog from today:                        │
│ 1. OOM killer invoked at 03:22 — killed process nginx       │
│ 2. Disk I/O error on /dev/sda1 at 07:15                     │
│ 3. Failed SSH login attempts from 192.168.1.50 at 14:30     │
╰──────────────────────────────────────────────────────────────╯
```

带品牌标识的边框面板，结构化展示结果。

---

## 六、交互设计

### 6.1 快捷键体系

| 快捷键 | 行为 |
|--------|------|
| `Enter` | 发送消息 |
| `Alt+Enter` / `Ctrl+J` | 换行（多行输入） |
| `Ctrl+C` | 中断 Agent（2 秒内双击强制退出） |
| `Ctrl+D` | 退出 |
| `Ctrl+G` / `Ctrl+X Ctrl+E` | 在 `$EDITOR` 中打开输入缓冲区 |
| `Tab` | 接受 ghost text 建议 / 补全 slash 命令 |
| `Ctrl+V` | 粘贴文本并尝试附加剪贴板图片 |
| `Alt+V` | 粘贴剪贴板图片 |
| `Ctrl+B` | 开始/停止语音录制 |
| `Ctrl+Z` | 挂起到后台（Unix） |

### 6.2 Busy Input 模式

在 Agent 工作时按 Enter 有三种行为：

| 模式 | 行为 |
|------|------|
| `interrupt`（默认） | 中断当前操作，立即处理新消息 |
| `queue` | 静默排队，Agent 完成后再发送 |
| `steer` | 注入到当前运行（`/steer`），不中断不新 turn |

### 6.3 斜杠命令

输入 `/` 触发自动补全下拉。支持：
- 119 个内置命令
- 每个已安装 skill 自动注册为命令
- 用户自定义 `quick_commands`（shell 执行或别名）
- 大小写不敏感

### 6.4 一键启动

```bash
hermes setup --portal  # OAuth 登录 + 配置
hermes chat            # 开始聊天
```

---

## 七、技术架构要点

### 7.1 皮肤引擎

`hermes_cli/skin_engine.py` — 纯数据驱动：
- YAML 定义，`~/.hermes/skins/` 存放
- 缺失键继承 `default` 皮肤
- 支持 Rich markup 的自定义 ASCII banner（`banner_logo`、`banner_hero`）
- 运行时热切换：`/skin ares`

### 7.2 三种界面共享核心

```
          ┌─────────────┐
          │  Python 核心  │
          │ (agent loop) │
          └──────┬──────┘
     ┌───────────┼───────────┐
     ▼           ▼           ▼
 Classic CLI   TUI      Desktop App
(prompt_tk)  (Node.js)  (Electron)
```

同一 `~/.hermes/state.db`，同一 config，任何界面启动的 session 都可以在其他界面恢复。

### 7.3 程序化模式

`hermes -z <prompt>` — 纯文本一投一收，无 banner/spinner/工具预览/Session 行，适合脚本和 CI。

---

## 八、值得 Janus 借鉴的设计点

### 8.1 必须借鉴

| 设计点 | 理由 |
|--------|------|
| **语义化色彩系统** | `ui_ok`/`ui_error`/`ui_warn` 而非 `red`/`green`/`yellow`，代码可读性高，换肤容易 |
| **持久状态栏** | 模型、token、费用、时间、后台任务数、YOLO 警告一站式可见。是"信任"的基础——用户随时知道 Agent 在消耗什么 |
| **渐进式上下文用量指示** | 绿/黄/橙/红 四色编码 + 进度条，颜色本身就是行动信号 |
| **皮肤继承机制** | 自定义皮肤只需覆盖差异键，其余自动继承。降低主题创作门槛 |
| **三种界面一致性** | Classic CLI / TUI / Desktop 共享核心、session、config。用户在终端开始的对话可以在桌面继续 |
| **`-z` 程序化模式** | 纯文本输入输出，零干扰。是 Agent 可编程性的基础 |

### 8.2 建议借鉴

| 设计点 | 理由 |
|--------|------|
| **工具执行提要行** | `┊ 💻 terminal ls -la (0.3s)` — 一眼看清工具调用和耗时，不淹没在输出中 |
| **多行粘贴预览** | 粘贴大段内容时不污染终端，显示 `[pasted: N lines, N chars]` 预览 |
| **Busy Input 三模式** | interrupt / queue / steer 让用户在 Agent 工作时可以选择不同交互方式 |
| **启动 Banner 折叠** | 工具默认展开，其余默认折叠。信息密度控制得很好 |
| **快捷键 `$EDITOR` 编辑** | `Ctrl+G` 在用户编辑器打开当前输入，处理长 prompt 的优雅方案 |

### 8.3 可选借鉴

| 设计点 | 理由 |
|--------|------|
| **kawaii 颜文字 Spinner** | 个性化强，但不适合所有产品。Anthropic 的"thinking..."更克制 |
| **多皮肤（战神/海神/龙王）** | 同上。但语义化色彩系统本身值得保留 |
| **模态浮层（TUI）** | `/help`、`/model` 等以覆盖层出现。对全屏 TUI 是好设计，对 REPL 不必要 |
| **TUI LaTeX 渲染** | 特色功能，非通用需求 |

### 8.4 不应借鉴

- Hermes 写死了"深色终端优先"——浅色终端默认完全不可读。Janus 应该第一天就支持浅色/深色。
- 皮肤文件中的 ASCII 艺术 banner（`banner_logo`）维护成本高、移植性差。
- `⚕` caduceus 符号作为品牌标识——文化含义有争议（与医疗符号混淆）。

---

## 九、关键数据

- 175,000+ GitHub stars（2026年4月前，发布不到4个月）
- OpenRouter 上使用量最大的 agent
- 支持 40+ provider
- 119 个 slash 命令（v0.17.0）
- 60+ 内置工具
- 9 套内置皮肤 + 社区自制皮肤包
- 3 种界面（Classic CLI / TUI / Desktop）+ Web Dashboard
- 20+ 消息平台支持（Telegram/Discord/Slack/WhatsApp/Signal/飞书/微信/QQ 等）

---

## 参考资料

- [CLI Interface 官方文档](https://hermes-agent.nousresearch.com/docs/user-guide/cli)
- [Skins & Themes 官方文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/skins)
- [TUI 官方文档](https://hermes-agent.nousresearch.com/docs/user-guide/tui)
- [CLI Commands Reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands)
- [GitHub: skin_engine.py](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/skin_engine.py)
- [GitHub: AGENTS.md](https://github.com/NousResearch/hermes-agent/blob/main/AGENTS.md)
- [Hermes Custom CLI Themes (社区)](https://github.com/Sahil-SS9/hermes-Custom-CLI-Themes)
- [Hermes Mod — 可视化皮肤编辑器](https://github.com/cocktailpeanut/hermes-mod)
- [GitHub Issue #4807: 浅色终端不可读](https://github.com/NousResearch/hermes-agent/issues/4807)
- [GitHub Issue #7307: 扩展皮肤系统覆盖](https://github.com/NousResearch/hermes-agent/issues/7307)
