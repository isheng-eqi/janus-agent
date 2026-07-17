# Janus CLI Windows Unicode 字符异常 — 根因诊断与修复方案

> 状态: 诊断完成，待实施
> 日期: 2026-07-17

---

## 一、问题复现

运行 `python main.py` 后，Windows 终端输出中出现 `*` 等乱码字符。源代码中的 Unicode 符号（✓ ✗ ❯ ⏱ │ └ ┌ ─ · … → — 等）被错误渲染。

已尝试的修复无效：
- ✅ `colorama.just_fix_windows_console()` — 只修 ANSI 转义码，不修 Unicode 渲染
- ❌ 把 `·` 替换为 `|` — 治标不治本

---

## 二、console.py 中使用的所有非 ASCII 字符（按类别）

### 2.1 结构性/装饰字符（运行时输出 — 最可能出问题）

| 字符 | Unicode | 名称 | 使用位置 | 数量 |
|------|---------|------|----------|------|
| `─` | U+2500 | BOX DRAWINGS LIGHT HORIZONTAL | 分隔线、box边框、summary | 118 |
| `│` | U+2502 | BOX DRAWINGS LIGHT VERTICAL | 任务框左侧竖线 | 10 |
| `┌` | U+250C | BOX DRAWINGS LIGHT DOWN AND RIGHT | 任务框左上角 (task_start) | 1 |
| `┐` | U+2510 | BOX DRAWINGS LIGHT DOWN AND LEFT | 任务框右上角 (task_start) | 1 |
| `└` | U+2514 | BOX DRAWINGS LIGHT UP AND RIGHT | 任务框左下角、嵌套参数 | 2 |
| `┘` | U+2518 | BOX DRAWINGS LIGHT UP AND LEFT | 任务框右下角 (task_done) | 1 |
| `✓` | U+2713 | CHECK MARK | review_pass 通过标记 | 4 |
| `✗` | U+2717 | BALLOT X | review_fail 失败标记 | 1 |
| `→` | U+2192 | RIGHTWARDS ARROW | 错误块操作指引、docstring | 11 |
| `⏱` | U+23F1 | STOPWATCH | task_done 耗时显示 | 1 |
| `…` | U+2026 | HORIZONTAL ELLIPSIS | 省略号 | 1 |
| `—` | U+2014 | EM DASH | docstring 中广泛使用 | 52 |
| `·` | U+00B7 | MIDDLE DOT | docstring 分隔符（青·朱·金） | 3 |

### 2.2 中文内容（运行时输出 + docstring）

| 类别 | 示例 | 说明 |
|------|------|------|
| 工具标签 | `写入`, `读取`, `执行`, `搜索`, `修改` 等 | _TOOL_LABELS dict，运行时输出 |
| 状态文案 | `完成`, `未完成`, `通过`, `未通过`, `需分解` 等 | task_done, review_pass/fail 输出 |
| 阶段标题 | `分析完成 | N 个子任务` | phase_decompose 输出 |
| 错误文案 | `分解 出错`, `错误: CODE — TITLE` 等 | error, error_block 输出 |
| docstring | 太极美学、设计原则等 | 仅用于文档，不输出到终端 |

### 2.3 全角标点（运行时 + docstring）

`（ ） ： ， 。 「 」` — 这些是 EAST ASIAN 全角字符。在 docstring 中不会出问题，但在错误消息中可能出现。

---

## 三、根因诊断

### 3.1 三层问题模型

Windows 终端中 Unicode 显示异常，是 **三层问题叠加** 的结果：

```
┌─────────────────────────────────────────────┐
│  Layer 3: 字体 (Font)                        │
│  终端使用的字体是否包含该字形的 glyph？        │
│  例：Consolas 不含 ✓ ✗ 等，但含 ─ │ ┌ └ 等   │
├─────────────────────────────────────────────┤
│  Layer 2: 编码 (Encoding)                    │
│  Python stdout 编码 vs 终端期望的编码是否匹配  │
│  例：Python 输出 UTF-8，终端解码为 CP437       │
├─────────────────────────────────────────────┤
│  Layer 1: 终端渲染引擎 (Renderer)             │
│  终端是否支持 Unicode 渲染（ConHost v2 vs v1）│
│  例：Windows 10 1809 前的 ConHost 不支持 UTF-8│
└─────────────────────────────────────────────┘
```

### 3.2 具体根因分析

**核心问题**：Python 3.x 在 Windows 上，当 `sys.stdout.encoding` 设置为 `utf-8` 时，`print()` 会输出 UTF-8 字节序列。但如果终端运行在 **legacy code page 模式**（如 CP437=IBM437），终端会将 UTF-8 多字节序列误读为多个单字节 CP437 字符，产生 `*`、`â`、`├` 等乱码。

**你看到的 `*` 符号**：当 Python 输出 UTF-8 编码的字符（如 `✓` = `\xe2\x9c\x93`），被 CP437 解码时：
- `\xe2` → `Γ` (Greek capital gamma)
- `\x9c` → `£` (pound sign)
- `\x93` → `ô` (o with circumflex)

或在某些终端字体缺失时直接显示为 `*`（占位符）。

**重要区别**：
- `colorama.init()` / `just_fix_windows_console()` 只修复 **ANSI 转义码**（颜色控制），调用 `SetConsoleMode(ENABLE_VIRTUAL_TERMINAL_PROCESSING)` 
- 它 **不改变** 终端编码或字符集

### 3.3 Windows 终端 Unicode 支持矩阵

| 终端 | 默认编码 | 盒状字符 | 符号 | CJK | ANSI色 |
|------|----------|:--------:|:----:|:---:|:------:|
| **Windows Terminal** (≥1.0) | UTF-8 | ✅ | ✅ | ✅ | ✅ |
| **Git Bash (Mintty)** | UTF-8 | ✅ | ✅ | ✅ | ✅ |
| **VSCode 内置终端** | UTF-8 | ✅ | ✅ | ✅ | ✅ |
| **ConEmu / Cmder** | UTF-8 | ✅ | ✅ | ✅ | ✅ |
| **PowerShell 7+** (独立) | UTF-8 | ✅ | ✅ | ✅ | ✅ |
| **JetBrains 终端** | UTF-8 | ✅ | ✅ | ✅ | ✅ |
| **WezTerm / Alacritty** | UTF-8 | ✅ | ✅ | ✅ | ✅ |
| **cmd.exe** (Win10 <1809) | CP437/CP850 | ❌ | ❌ | ❌ | ❌* |
| **cmd.exe** (Win10 ≥1809, UTF-8 Beta) | UTF-8 | ✅ | ⚠️ | ✅ | ✅ |
| **PowerShell 5.1** (Windows PS) | CP850 | ❌ | ❌ | ❌ | ✅* |
| **Linux 控制台** (tty) | ASCII | ❌ | ❌ | ❌ | ✅** |

> \* 启用 `ENABLE_VIRTUAL_TERMINAL_PROCESSING`（colorama 默认做）后支持  
> \*\* 内核控制台有限支持  
> ⚠️ 取决于字体；默认 Consolas 不含 ✓ ✗ ❯ ⏱

### 3.4 关键环境变量

修复或诊断时可检查以下环境变量：

```bash
# 理想设置（Windows Terminal, Git Bash, VSCode）
PYTHONIOENCODING=utf-8    # Python 3.7+
PYTHONUTF8=1              # Python 3.7+, 等效于 -X utf8
WT_SESSION=<guid>         # 仅 Windows Terminal 设置
TERM=xterm-256color        # Git Bash / Mintty 设置

# 问题环境
# cmd.exe: 没有 WT_SESSION, TERM 通常未设置或为 ''
# chcp 输出: Active code page: 437 或 850
```

---

## 四、业界最佳实践

### 4.1 npm `figures` 包（Sindre Sorhus，630+ stars）

**核心理念**：维护两套符号表 — `mainSymbols` (Unicode) 和 `fallbackSymbols` (ASCII安全)

```javascript
// 检测逻辑
import isUnicodeSupported from 'is-unicode-supported';
const shouldUseMain = isUnicodeSupported();
const figures = shouldUseMain ? mainSymbols : fallbackSymbols;

// 映射示例
tick:    '✔'  →  '√'
cross:   '✘'  →  '×'
pointer: '❯'  →  '>'
info:    'ℹ'  →  'i'
```

**检测逻辑** (`is-unicode-supported`)：
```javascript
if (platform !== 'win32') return TERM !== 'linux'; // 非Linux内核控制台
// Windows 下硬编码白名单
return Boolean(env.WT_SESSION)       // Windows Terminal
    || env.ConEmuTask === '{cmd::Cmder}'  // ConEmu/Cmder
    || TERM_PROGRAM === 'vscode'
    || TERM === 'xterm-256color'     // Git Bash (Mintty)
    || TERM === 'alacritty'
    || env.TERMINAL_EMULATOR === 'JetBrains-JediTerm';
```

**注意**：box-drawing 字符（─│┌└┐┘）在 `figures` 中属于 `common` 集合，即 **主和备选都相同**——因为它们假设 box-drawing 字符在现代终端中普遍可用（Windows Terminal, Mintty, VSCode 均支持）。这是对 Janus 的关键启发。

### 4.2 Python `rich` 库（Textualize，50k+ stars）

**核心理念**：通过 `legacy_windows` 检测 + `Box` 抽象分层

```python
# Rich 的检测逻辑
# - 检测是否是 Windows legacy console (非 WT, 非 Mintty)
# - 如果是 legacy，设置 legacy_windows=True
# - legacy_windows=True 时：
#   - box drawing 字符自动降级为 ASCII
#   - 字体敏感的符号也降级

# Rich Box 样式抽象
box.ASCII    # 纯 ASCII: +-|
box.SQUARE   # Unicode 盒状: ┌─┐│└┘
box.MINIMAL  # 最简线条: ─│ (无角)
```

**Rich 的 legacy_windows 检测核心逻辑**（简化）：
```python
def detect_legacy_windows():
    if sys.platform != 'win32':
        return False
    # 如果是 Windows Terminal，非 legacy
    if 'WT_SESSION' in os.environ:
        return False
    # Cygwin/MSYS2/Mintty 设置 TERM
    if os.environ.get('TERM', '') != '':
        return False
    # ANSICON, ConEmu 也设置各种标志
    if 'ANSICON' in os.environ or 'ConEmuANSI' in os.environ:
        return False
    return True  # 剩下的就是 legacy (cmd.exe, PowerShell 5)
```

### 4.3 pip 的做法

pip 历史上使用简单的 ASCII spinner：`-\|/`，后来切换到 `rich` 库内部渲染。pip 自己的代码从不直接输出 Unicode 符号。

### 4.4 npm / Cargo / Go 的通用做法

- **npm**: 使用 `npmlog` + `gauge`，装饰性输出用 ASCII
- **Cargo**: 使用 `indicatif` 库（Rust），自动检测终端能力，fallback 到简单 ASCII
- **Go**: `github.com/fatih/color` 只管颜色；社区用 `mattn/go-runewidth` 处理宽度

### 4.5 总结：业界共识

1. **检测终端能力**（不是猜测）
2. **维护 Unicode→ASCII fallback 映射表**
3. **Box-drawing 字符是最高优先级**（最常用，但也最容易在 legacy 终端坏掉）
4. **中文/日文/韩文（CJK）需要宽字符处理**，不应 fallback 到 ASCII（除非是极端 legacy 环境）
5. **让用户在 Unicode 模式和 ASCII 模式间切换**（环境变量或 CLI flag）

---

## 五、修复方案对比

### 方案 A：检测终端能力，自动降级（推荐 ⭐）

**思路**：在 `Console.__init__()` 时检测当前终端是否支持 Unicode，维护两套符号，自动切换。

**优点**：
- ✅ 零用户配置，智能适应
- ✅ Windows Terminal / VSCode / Git Bash 用户享受完整体验
- ✅ 旧 cmd.exe 用户自动获得可读输出
- ✅ 不引入第三方依赖
- ✅ 符合业界最佳实践

**缺点**：
- ❌ 检测逻辑需要持续维护（新终端出现时）
- ❌ 增加 ~200 行代码

**实现要点**：
```python
class UnicodeSupport:
    """Detect terminal Unicode capability."""
    
    @staticmethod
    def detect() -> bool:
        if sys.platform != 'win32':
            return os.environ.get('TERM', '') != 'linux'  # Linux kernel console
        
        # Windows: whitelist known Unicode-capable terminals
        env = os.environ
        if 'WT_SESSION' in env: return True  # Windows Terminal
        if 'TERMINUS_SUBLIME' in env: return True
        if env.get('ConEmuTask', '') == '{cmd::Cmder}': return True
        if env.get('TERM_PROGRAM', '') in ('vscode', 'Terminus-Sublime'): return True
        if env.get('TERM', '') in ('xterm-256color', 'alacritty', 'rxvt-unicode'): return True
        if env.get('TERMINAL_EMULATOR', '') == 'JetBrains-JediTerm': return True
        
        # Additional checks
        if 'PYTHONUTF8' in env: return True
        if env.get('PYTHONIOENCODING', '') == 'utf-8': return True
        
        # Check stdout encoding
        try:
            if sys.stdout.encoding and sys.stdout.encoding.lower() in ('utf-8', 'utf8'):
                return True
        except Exception:
            pass
        
        return False  # Legacy console
```

### 方案 B：统一替换为 ASCII（不推荐）

**思路**：将所有 Unicode 符号替换为纯 ASCII。

**优点**：
- ✅ 100% 兼容，永不失败
- ✅ 代码简单

**缺点**：
- ❌ 失去所有视觉美感（太极美学全毁）
- ❌ Windows Terminal 等现代终端用户被降级体验
- ❌ 中文工具标签（写入/读取/执行）无法替换为 ASCII（丢失含义）

### 方案 C：使用 rich 库托管终端渲染

**思路**：用 `rich.Console` 替换自建 `Console`，让 rich 处理所有终端兼容性。

**优点**：
- ✅ 专业的终端渲染，维护由社区负责
- ✅ 自动处理编码、颜色、宽度
- ✅ 支持 Table、Progress Bar、Markdown 等

**缺点**：
- ❌ 引入重量级依赖（rich + 依赖 ≈ 数 MB）
- ❌ Janus 的太极美学设计无法直接映射（需要自定义 Theme）
- ❌ 重构工作量大（整个 Console API 要重写）
- ❌ 丧失"零依赖"的设计哲学

### 推荐方案

**⇒ 方案 A：终端能力检测 + 自动降级**

理由：
1. Janus 目前是零依赖（仅 colorama），方案 A 不引入新依赖
2. 检测逻辑业界验证充分（npm `is-unicode-supported` 使用数年，没问题）
3. 中文文案（`完成`、`未完成`等）在 Unicode 终端正常显示，在 legacy 终端降级到中英混合（如 `完成 -> [OK]`）也比纯乱码好
4. 可用 `--ascii-only` CLI flag 支持用户手动切换

---

## 六、完整字符映射表

### 6.1 Box-drawing 字符（最优先）

| Unicode | ASCII Fallback | 说明 |
|---------|---------------|------|
| `─` (U+2500) | `-` | 水平线 |
| `│` (U+2502) | `\|` | 竖线 |
| `┌` (U+250C) | `,` | 左上角 |
| `┐` (U+2510) | `.` | 右上角 |
| `└` (U+2514) | `` ` `` | 左下角 |
| `┘` (U+2518) | `'` | 右下角 |

**备选**：box-drawing 可统一使用更传统的 ASCII 绘制风格：

| 元素 | Unicode | ASCII 备选 |
|------|---------|-----------|
| 水平线 | `─` | `-` |
| 竖线 | `│` | `\|` |
| 左上角 | `┌` | `+` |
| 右上角 | `┐` | `+` |
| 左下角 | `└` | `+` |
| 右下角 | `┘` | `+` |

> 推荐使用加号 (`+`) 风格，因为这是 ASCII table 绘制的标准做法（见 `rich.box.ASCII`）

### 6.2 状态符号

| Unicode | ASCII Fallback | 用途 |
|---------|---------------|------|
| `✓` (U+2713) | `[OK]` 或 `v` | review 通过 |
| `✗` (U+2717) | `[FAIL]` 或 `x` | review 失败 |
| `→` (U+2192) | `->` | 指引方向 |
| `❯` (U+276F) | `>` | 指针（未在运行时使用） |
| `⏱` (U+23F1) | `[` 或直接省略字符保留 `{elapsed}s` | 耗时标记 |

### 6.3 标点

| Unicode | ASCII Fallback | 用途 |
|---------|---------------|------|
| `…` (U+2026) | `...` | 省略号 |
| `—` (U+2014) | `--` | em dash（仅 docstring） |
| `·` (U+00B7) | `.` | middle dot（仅 docstring） |

### 6.4 CJK 中文文案（特殊处理）

中文文案（工具标签、状态文案）在方案 A 下不需要 fallback——因为 `_supports_unicode()` 返回 True 时正常使用，返回 False 时给出英文替代。

| 原中文 | 英文 Fallback | 位置 |
|--------|--------------|------|
| `写入` | `write` | _TOOL_LABELS |
| `读取` | `read` | _TOOL_LABELS |
| `执行` | `exec` | _TOOL_LABELS |
| `搜索` | `search` | _TOOL_LABELS |
| `提取网页` | `extract` | _TOOL_LABELS |
| `修改` | `patch` | _TOOL_LABELS |
| `搜索文件` | `find` | _TOOL_LABELS |
| `完成` | `DONE` | task_done (success) |
| `未完成` | `FAIL` | task_done (failure) |
| `需分解` | `DECOMPOSE` | task_done (needs_decomposition) |
| `通过` | `PASS` | review_pass |
| `未通过` | `FAIL` | review_fail |
| `分析完成 \| N 个子任务` | `DECOMPOSED: N subtasks` | phase_decompose |
| `思考中...` | `Thinking...` | working_pulse |
| `→ 第 N 次重试` | `-> 第 N 次重试` (仅替换箭头) | review_fail |
| `错误: CODE — TITLE` | `错误: CODE -- TITLE` | error_block |

### 6.5 不需要 fallback 的字符

以下字符在现代终端中普遍可用，且在 scheme A 的检测白名单下不会出问题：

- 基本 box-drawing 字符（U+2500─U+257F）：所有白名单终端均支持
- 中文汉字：所有白名单终端均支持（Windows Terminal, Mintty, VSCode 等均使用 Unicode 字体）

---

## 七、实施方案（推荐）

### 7.1 新增模块：`core/_unicode.py`

```python
"""Unicode capability detection and fallback mapping for Janus Console."""

import os
import sys
from typing import Dict

# ── Detection ────────────────────────────────────────────────────────────────

def _supports_unicode() -> bool:
    """Return True if the terminal likely supports Unicode symbols.

    Non-Windows: assume True (except Linux kernel console).
    Windows: whitelist known Unicode-capable terminals, fall back to False
    for legacy cmd.exe / PowerShell 5.1 (ConHost v1).
    """
    if sys.platform != 'win32':
        return os.environ.get('TERM', '') != 'linux'

    env = os.environ
    # Windows Terminal
    if 'WT_SESSION' in env:
        return True
    # ConEmu / Cmder
    if env.get('ConEmuTask', '') == '{cmd::Cmder}':
        return True
    # VSCode integrated terminal
    if env.get('TERM_PROGRAM', '') in ('vscode', 'Terminus-Sublime'):
        return True
    # Git Bash (Mintty), Cygwin, MSYS2 — set TERM
    if env.get('TERM', '') in ('xterm-256color', 'xterm', 'alacritty',
                                'rxvt-unicode', 'rxvt-unicode-256color'):
        return True
    # JetBrains IDE terminal
    if env.get('TERMINAL_EMULATOR', '') == 'JetBrains-JediTerm':
        return True
    # Terminus (older versions)
    if 'TERMINUS_SUBLIME' in env:
        return True
    # User explicitly requested UTF-8
    if 'PYTHONUTF8' in env:
        return True
    # stdout encoding is UTF-8
    try:
        enc = sys.stdout.encoding or ''
        if enc.lower() in ('utf-8', 'utf8'):
            return True
    except Exception:
        pass

    return False


# ── Symbol constants (lazy, computed once) ───────────────────────────────────

_UNICODE_OK: bool = _supports_unicode()


class Symbols:
    """Unicode or ASCII symbols depending on terminal capability."""

    # Box-drawing
    HLINE:  str = '─'  if _UNICODE_OK else '-'
    VLINE:  str = '│'  if _UNICODE_OK else '|'
    TOP_L:  str = '┌'  if _UNICODE_OK else ','
    TOP_R:  str = '┐'  if _UNICODE_OK else '.'
    BOT_L:  str = '└'  if _UNICODE_OK else "'"
    BOT_R:  str = '┘'  if _UNICODE_OK else "'"

    # Status
    CHECK:  str = '✓'  if _UNICODE_OK else '[OK]'
    CROSS:  str = '✗'  if _UNICODE_OK else '[FAIL]'
    ARROW:  str = '→'  if _UNICODE_OK else '->'
    CLOCK:  str = '⏱'  if _UNICODE_OK else ''

    # Punctuation
    ELLIP:  str = '…'  if _UNICODE_OK else '...'
    EM_DASH: str = '—' if _UNICODE_OK else '--'

    # Labels (when Unicode is not available, use English)
    @staticmethod
    def label(key: str) -> str:
        """Return the display label for a tool, respecting terminal capability."""
        labels_zh = {
            'write_file': '写入',
            'read_file': '读取',
            'terminal': '执行',
            'web_search': '搜索',
            'web_extract': '提取网页',
            'execute_code': '执行代码',
            'patch': '修改',
            'search_files': '搜索文件',
            'browser_navigate': '浏览器导航',
        }
        labels_en = {
            'write_file': 'write',
            'read_file': 'read',
            'terminal': 'exec',
            'web_search': 'search',
            'web_extract': 'extract',
            'execute_code': 'execute',
            'patch': 'patch',
            'search_files': 'find',
            'browser_navigate': 'navigate',
        }
        if _UNICODE_OK:
            return labels_zh.get(key, key)
        return labels_en.get(key, key)

    # Phase / status labels
    PHASE_DECOMPOSE: str = '分析完成' if _UNICODE_OK else 'DECOMPOSED'
    STATUS_DONE:      str = '完成'     if _UNICODE_OK else 'DONE'
    STATUS_FAIL:      str = '未完成'   if _UNICODE_OK else 'FAIL'
    STATUS_DECOMPOSE: str = '需分解'    if _UNICODE_OK else 'DECOMPOSE'
    REVIEW_PASS:      str = '通过'     if _UNICODE_OK else 'PASS'
    REVIEW_FAIL:      str = '未通过'    if _UNICODE_OK else 'FAIL'
    WORKING:          str = '思考中...' if _UNICODE_OK else 'Thinking...'
    SUMMARY:          str = '汇总'     if _UNICODE_OK else 'SUMMARY'


def supports_unicode() -> bool:
    """Public API: does the current terminal support Unicode?"""
    return _UNICODE_OK
```

### 7.2 修改 `core/console.py`

改动点：

1. **导入 Symbols**：`from core._unicode import Symbols, supports_unicode`
2. **替换硬编码 Unicode 字符**：将所有 `'─'` → `Symbols.HLINE`，`'│'` → `Symbols.VLINE`，等等
3. **工具标签**：`_TOOL_LABELS.get(...)` → `Symbols.label(tool_name)`
4. **状态文案**：`'完成'` → `Symbols.STATUS_DONE`，等等
5. **task_start 的 box header**：
   ```python
   # 前:
   header = f"\u250c\u2500 {description} "
   # 后:
   header = f"{Symbols.TOP_L}{Symbols.HLINE} {description} "
   ```
6. **task_done 的 box footer**：
   ```python
   # 前:
   print(f"└{'─' * self._box_width}┘")
   # 后:
   print(f"{Symbols.BOT_L}{Symbols.HLINE * self._box_width}{Symbols.BOT_R}")
   ```
7. **summary 分隔线**：
   ```python
   # 前:
   print(f"{'─' * 20} 汇总 {'─' * 20}")
   # 后:
   print(f"{Symbols.HLINE * 20} {Symbols.SUMMARY} {Symbols.HLINE * 20}")
   ```

### 7.3 可选：CLI flag

在 `main.py` 中增加 `--ascii-only` flag：

```python
parser.add_argument('--ascii-only', action='store_true',
                    help='Force ASCII-only output (no Unicode symbols)')
```

实现：检测到 `--ascii-only` 时，在 `_unicode.py` 中设置全局变量 `_FORCE_ASCII = True`。

---

## 八、测试策略

### 8.1 单元测试

```python
def test_symbols_unicode_mode(self):
    """在 Unicode 环境中（WT/Git Bash），Symbols 返回 Unicode 字符。"""
    with patch('core._unicode._UNICODE_OK', True):
        assert Symbols.VLINE == '│'
        assert Symbols.STATUS_DONE == '完成'

def test_symbols_ascii_mode(self):
    """在 legacy 环境中，Symbols 返回 ASCII fallback。"""
    with patch('core._unicode._UNICODE_OK', False):
        assert Symbols.VLINE == '|'
        assert Symbols.STATUS_DONE == 'DONE'

def test_supports_unicode_on_windows_terminal(self):
    """Windows Terminal 环境检测为 True。"""
    with patch.dict(os.environ, {'WT_SESSION': 'abc-123'}):
        with patch('sys.platform', 'win32'):
            assert supports_unicode() is True
```

### 8.2 真实环境测试

1. **Windows Terminal** (Powershell 7 / cmd)：全部 Unicode 正常
2. **Git Bash (Mintty)**：全部 Unicode 正常
3. **VSCode 终端**：全部 Unicode 正常
4. **cmd.exe (CP437)**：全部降级为 ASCII，可读
5. **cmd.exe (chcp 65001)**：全部 Unicode 正常（需字体支持部分符号）

---

## 九、结论

**根因**：Janus Console 直接使用 Unicode 硬编码字符，在 legacy Windows 终端（CP437/CP850 编码 + 旧 ConHost）中无法正确渲染。`colorama` 只解决 ANSI 颜色问题，不解决 Unicode 渲染。

**推荐方案**：方案 A — 终端能力检测 + 自动降级。在 `Console.__init__()` 时检测终端 Unicode 能力，维护一套 `Symbols` 静态类，根据检测结果自动切换 Unicode 和 ASCII 字符。

**实施路径**：
1. 新增 `core/_unicode.py`（检测逻辑 + 字符映射）
2. 修改 `core/console.py`（替换所有硬编码 Unicode 为 `Symbols.*`）
3. 在 `main.py` 增加 `--ascii-only` CLI flag
4. 更新 `tests/test_console.py` 添加 Unicode/ASCII 双模式测试
5. 在三个真实终端环境中验证：Windows Terminal、Git Bash、cmd.exe

**工作量估算**：2-4 小时。
