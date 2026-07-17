"""
Janus Console — unified CLI output with Taiji aesthetics (太极美学).

The Console is a PASSIVE observer.  It receives events from Gatekeeper and
Worker, formats them, and prints to stdout.  It does NOT control flow.

Modes:
    - "default": L0+L1+L2 info (phase nodes, task details, review results)
    - "verbose": additionally show full tool parameters (L3)
    - "quiet": only final summary (L0)

Taiji three-colour palette (青·朱·金) + two ink weights (浓墨·淡墨):

    青 (qing, 36)  = info / progress / partial — 青花瓷的沉静
    朱 (zhu, 31)   = error / failure — 朱砂印章的严肃
    金 (jin, 33)   = success / completion — 泥金笺的暖意

    浓墨 (nongmo, 1) = emphasis on key data
    淡墨 (danmo, 2)  = secondary info (timestamps, metadata)

Design principles:
    - 阴阳呼吸：every semantic block separated by a blank line
    - 白底黑字：terminal defaults are the canvas; colour is accent only
    - 刚柔并济：errors are firm (朱, bold), success is soft (金, plain)

Colour is automatically suppressed when:
    1. stdout is not a TTY (piped to file/another program)
    2. NO_COLOR environment variable is set
    3. TERM=dumb
    4. ``--no-color`` CLI flag is passed (set via ``Console.set_no_color()``)
"""

from __future__ import annotations

import os
import sys
import unicodedata
from typing import Optional

from core._unicode import Symbols

# Fix ANSI escape code rendering on Windows (cmd, PowerShell, etc.)
# Git Bash usually handles ANSI natively, but this ensures it works everywhere.
import colorama
colorama.just_fix_windows_console()

# ---------------------------------------------------------------------------
# Colour support detection
# ---------------------------------------------------------------------------

_no_color_forced: bool = False


def set_no_color() -> None:
    """Force-disable ANSI colours (for ``--no-color`` CLI flag support)."""
    global _no_color_forced
    _no_color_forced = True


def _supports_color() -> bool:
    """Return False when colour output should be suppressed."""
    if _no_color_forced:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if not sys.stdout.isatty():
        return False
    return True


def _color(code: int, text: str) -> str:
    """Wrap *text* in an ANSI SGR code. Returns plain *text* when colour is suppressed."""
    if not _supports_color():
        return text
    return f"\033[{code}m{text}\033[0m"


# ── Taiji three-colour palette ───────────────────────────────────────────


def _qing(text: str) -> str:
    """青 — info / progress / partial. 青花瓷的沉静."""
    return _color(36, text)


def _zhu(text: str) -> str:
    """朱 — error / failure. 朱砂印章的严肃."""
    return _color(31, text)


def _jin(text: str) -> str:
    """金 — success / completion. 泥金笺的暖意."""
    return _color(33, text)


# ── Ink weights (墨分浓淡) ────────────────────────────────────────────────


def _nongmo(text: str) -> str:
    """浓墨 — bold emphasis on key data."""
    return _color(1, text)


def _danmo(text: str) -> str:
    """淡墨 — dimmed secondary info (timestamps, metadata)."""
    return _color(2, text)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _display_width(text: str) -> int:
    """Return the display width of *text* in a monospace terminal.

    CJK characters (East Asian Wide/Fullwidth) count as 2 columns;
    ASCII and most other characters count as 1.
    """
    width = 0
    for ch in text:
        ea = unicodedata.east_asian_width(ch)
        width += 2 if ea in ("W", "F") else 1
    return width


# ---------------------------------------------------------------------------
# Tool → Chinese summary
# ---------------------------------------------------------------------------

_TOOL_LABELS: dict[str, str] = {
    "write_file": "写入",
    "read_file": "读取",
    "terminal": "执行",
    "web_search": "搜索",
    "web_extract": "提取网页",
    "execute_code": "执行代码",
    "patch": "修改",
    "search_files": "搜索文件",
    "browser_navigate": "浏览器导航",
}


def _tool_args_preview(tool_name: str, arguments: dict) -> str:
    """Build a one-line human-readable preview of tool arguments.

    Returns a string like ``"factorial.py"`` or ``"pytest test.py"``,
    suitable for inlining after the tool label.
    """
    if tool_name in ("read_file", "write_file", "patch"):
        return arguments.get("path", "?")
    if tool_name == "terminal":
        return arguments.get("command", arguments.get("cmd", "?"))
    if tool_name in ("web_search", "search_files"):
        return arguments.get("query", arguments.get("pattern", "?"))
    if tool_name == "web_extract":
        urls = arguments.get("urls", "?")
        if urls is None:
            urls = "?"
        if isinstance(urls, list):
            urls = ", ".join(urls)
        if not isinstance(urls, str):
            urls = str(urls)
        if len(urls) > 60:
            urls = urls[:57] + "..."
        return urls
    if tool_name == "execute_code":
        code = arguments.get("code", "")
        if len(code) > 40:
            code = code[:37] + "..."
        return code
    if tool_name == "browser_navigate":
        return arguments.get("url", "?")
    return ""


# ============================================================================
# Console
# ============================================================================


class Console:
    """统一输出管理——太极美学 CLI 输出。

    Usage::

        c = Console(mode="default")
        c.phase_decompose(3, "  ✓ 实现阶乘函数\\n  ✓ 编写测试\\n  ✓ 验证通过")
        c.task_start("task-1", "实现阶乘函数")
        c.tool_call("write_file", "factorial.py")
        c.review_pass("task-1", "函数正确实现")
        c.task_done("task-1", "success", 3.1)
        c.summary(3, 2, 1)
    """

    def __init__(self, mode: str = "default") -> None:
        """Create a Console.

        Args:
            mode: One of ``"default"``, ``"verbose"``, ``"quiet"``.
        """
        if mode not in ("default", "verbose", "quiet"):
            raise ValueError(
                f"Console mode must be 'default', 'verbose', or 'quiet', "
                f"got {mode!r}"
            )
        self._mode = mode
        self._box_width = 56  # default box width, updated by task_start

    # -- properties -----------------------------------------------------------

    @property
    def mode(self) -> str:
        """The current output mode."""
        return self._mode

    @property
    def is_verbose(self) -> bool:
        """True when full tool parameters are displayed."""
        return self._mode == "verbose"

    @property
    def is_quiet(self) -> bool:
        """True when only the final summary is displayed."""
        return self._mode == "quiet"

    # -- L1: Phase nodes ------------------------------------------------------

    def phase_decompose(self, n_tasks: int, tasks_summary: str) -> None:
        """显示 Gatekeeper 分解结果——太极节奏：空行（阴）→ 标题（阳）→ 空行（阴）→ 列表。

        Args:
            n_tasks: Number of sub-tasks produced.
            tasks_summary: Pre-formatted multi-line string listing each task.
        """
        if self.is_quiet:
            return
        # 阴——与前文呼吸隔离
        print()
        # 阳——青花标题
        print(_qing(f"{Symbols.PHASE_DECOMPOSE} | {n_tasks} 个子任务"))
        # 阴——标题与列表之间留白
        print()
        print(tasks_summary)

    # -- L2: Task lifecycle ---------------------------------------------------

    def task_start(self, task_id: str, description: str) -> None:
        """显示任务开始——打开一个任务框。

        上方空行（阴）让每个任务框成为独立的呼吸单元。

        Args:
            task_id: The task identifier (e.g. ``"task-1"``) — kept for
                     internal reference, NOT displayed.
            description: Human-readable description of the task.
        """
        if self.is_quiet:
            return
        # 阴——与前文呼吸隔离
        print()
        # Build header: e.g. "+- Implement factorial -----------+"
        header = f"{Symbols.TOP_L}{Symbols.HLINE} {description} "
        header_width = _display_width(header)
        target = max(40, min(90, header_width + 10))
        padding = max(0, target - header_width - 1)
        header_line = f"{_qing(header)}{_qing(Symbols.HLINE * padding)}{Symbols.TOP_R}"
        self._box_width = target - 2
        print(header_line)

    def task_done(self, task_id: str, status: str, elapsed: float) -> None:
        """显示任务完成并关闭任务框。

        太极措辞：「通过」→「完成」，「失败」→「未完成」。金色落定，朱砂标记。

        Args:
            task_id: The task identifier (NOT displayed).
            status: ``"success"``, ``"failure"``, or ``"needs_decomposition"``.
            elapsed: Wall-clock seconds the task took.
        """
        if self.is_quiet:
            return
        if status == "success":
            status_cn = Symbols.STATUS_DONE
            status_color = _jin
        elif status == "needs_decomposition":
            status_cn = Symbols.STATUS_DECOMP
            status_color = _qing
        else:
            status_cn = Symbols.STATUS_FAIL
            status_color = _zhu
        # 淡墨——耗时
        print(f"{Symbols.VLINE}  {_danmo(f'{Symbols.CLOCK} {elapsed:.1f}s')}")
        print(f"{Symbols.BOT_L}{Symbols.HLINE * self._box_width}{Symbols.BOT_R}")
        # 空行（阴）——框与状态之间留呼吸
        print()
        print(f"  {status_color(status_cn)}")

    # -- L2: Tool calls -------------------------------------------------------

    def tool_call(self, tool_name: str, summary: str) -> None:
        """显示工具调用摘要——动作动词 + 淡墨路径，形成浓淡对比。

        Args:
            tool_name: The tool identifier (e.g. ``"write_file"``).
            summary: Human-readable summary (e.g. file path, command).
        """
        if self.is_quiet:
            return
        label = Symbols.label(tool_name)
        # 动作动词（默认色）+ 淡墨路径 —— 浓淡对比
        print(f"{Symbols.VLINE}  {label}: {_danmo(summary)}")

    def tool_call_verbose(
        self, tool_name: str, arguments: dict
    ) -> None:
        """Show a tool call with full arguments (verbose mode only).

        In non-verbose mode this is a no-op — the summary was already
        printed by :meth:`tool_call`.

        Args:
            tool_name: The tool identifier.
            arguments: The full arguments dict passed to the tool.
        """
        if not self.is_verbose:
            return
        label = Symbols.label(tool_name)
        print(f"{Symbols.VLINE}     {Symbols.BOT_L}{Symbols.HLINE} 完整参数: {arguments}")

    # -- L2: Review -----------------------------------------------------------

    def review_pass(self, task_id: str, evidence: str) -> None:
        """显示审核通过——金色「通过」，去 emoji，安静落定。

        Args:
            task_id: The task identifier.
            evidence: What the Reviewer cited as proof of success.
                Each line of *evidence* is printed as a sub-item.
        """
        if self.is_quiet:
            return
        print(f"{Symbols.VLINE}  {_jin(Symbols.REVIEW_PASS)}")
        if evidence:
            for line in evidence.strip().split("\n")[:5]:
                stripped = line.strip()
                if stripped:
                    print(f"{Symbols.VLINE}     {Symbols.CHECK} {stripped}")
            remaining = len(evidence.strip().split("\n")) - 5
            if remaining > 0:
                print(f"{Symbols.VLINE}     ... 还有 {remaining} 项")

    def review_fail(
        self, task_id: str, issues: list[str], attempt: int
    ) -> None:
        """显示审核失败——朱砂「未通过」，重试信息独立成行（金色）。

        Args:
            task_id: The task identifier.
            issues: The specific problems found by the Reviewer.
            attempt: Current retry index (0 = first retry attempt, 1 = second, …).
                The displayed retry number is attempt + 1.
        """
        if self.is_quiet:
            return
        retry_n = attempt + 1
        # 朱砂——未通过（刚而不厉）
        print(f"{Symbols.VLINE}  {_zhu(Symbols.REVIEW_FAIL)}")
        for issue in issues[:5]:
            print(f"{Symbols.VLINE}     {Symbols.CROSS} {issue}")
        if len(issues) > 5:
            print(f"{Symbols.VLINE}     {Symbols.ELLIP} 还有 {len(issues) - 5} 个问题")
        # 金——行动建议独立成行
        print(f"{Symbols.VLINE}  {_jin(f'{Symbols.ARROW} 第 {retry_n} 次重试')}")

    # -- L2: Errors -----------------------------------------------------------

    def error(self, phase: str, message: str) -> None:
        """显示内联错误（无 traceback）。

        上方空行（阴）与前面的输出呼吸隔离。错误信息用朱砂。

        Args:
            phase: Which phase failed (e.g. ``"分解"``, ``"执行"``).
            message: Human-readable error message.
        """
        if self.is_quiet:
            return
        # 阴——与前面的输出呼吸隔离
        print()
        print(f"{_zhu(f'{phase} 出错')}: {message}")

    def error_block(self, code: str, title: str, reason: str,
                    actions: list[str]) -> None:
        """显示标准错误块——朱砂标记问题，金指引出路。

        Args:
            code: Error code (e.g. ``"CONFIG_NOT_FOUND"``).
            title: One-line description.
            reason: Why this happened (1-2 sentences).
            actions: List of ``"→ 修复步骤"`` strings.
        """
        lines = [f"{_zhu(f'错误: {code} — {title}')}", ""]
        lines.append(f"  {reason}")
        if actions:
            lines.append("")
            for action in actions:
                lines.append(f"  {_jin(action)}")
        print("\n".join(lines), file=sys.stderr)

    # -- L1.5: Think block (strategist thinking) ------------------------------

    def think_block(self, text: str, source: str = "Gatekeeper") -> None:
        """显示 LLM 的战略思考过程（仅 verbose 模式）。

        Gated behind verbose mode to avoid leaking operational reasoning
        prompts into the user-facing output.  Only shown when the user
        explicitly requests ``--verbose``.

        Args:
            text: The reasoning content from the LLM.
            source: ``"Gatekeeper"`` or other source label.
        """
        if not self.is_verbose:
            return
        prefix = "思考" if source == "Gatekeeper" else "  思考"
        truncated = text[:500]
        if len(text) > 500:
            truncated += "...[省略]"
        print(f"{_danmo(prefix)}: {truncated}")

    # -- Pulse (quiet-mode progress indicator) --------------------------------

    def working_pulse(self, message: str = "思考中...") -> None:
        """Brief indicator that always prints — even in quiet mode.

        The quiet-mode contract is "only the final summary," but absolute
        silence during multi-second LLM calls makes the user think the
        program has frozen.  This one-line pulse fixes that without
        breaking the contract.

        In default/verbose modes this is a no-op — the richer output
        (phase_decompose, task_start, tool_call) already signals progress.
        """
        if self.is_quiet:
            print(f"{_danmo(message)}")

    # -- L0/L1: Final summary -------------------------------------------------

    def summary(self, total: int, passed: int, failed: int) -> None:
        """最终汇总。

        保留此方法向后兼容——新代码中 Gatekeeper 直接格式化汇报，
        不再调用此方法。Planner 也不再调用。

        太极设计：
        - 全部成功 → 金「完成」
        - 全部失败 → 朱「未完成」
        - 部分成功 → 青「部分完成」

        Args:
            total: Total number of tasks.
            passed: Number that passed.
            failed: Number that failed.
        """
        if self.is_quiet:
            if failed == 0:
                print(f"完成。{passed}/{total} 个任务完成。")
            else:
                print(f"完成。{passed}/{total} 完成，{failed} 未完成。")
            return

        if failed == 0:
            line = f"  {_jin(f'{passed}/{total}')} {_nongmo(Symbols.STATUS_DONE)}"
        elif passed == 0:
            line = f"  {_zhu(f'{failed}/{total}')} {_nongmo(_zhu(Symbols.STATUS_FAIL))}"
        else:
            line = (f"  {_jin(str(passed))} {_nongmo(Symbols.STATUS_DONE)}  "
                    f"{_zhu(str(failed))} {_nongmo(_zhu(Symbols.STATUS_FAIL))}")

        # 阴——与上面的输出呼吸隔离
        print()
        print(f"{Symbols.HLINE * 20} {Symbols.SUMMARY} {Symbols.HLINE * 20}")
        print(line)

    # -- Convenience ----------------------------------------------------------

    def build_tool_summary(
        self, tool_name: str, arguments: dict
    ) -> str:
        """Build a human-readable tool call summary string.

        Used by Worker to format the summary before calling
        :meth:`tool_call`.

        Args:
            tool_name: The tool identifier.
            arguments: The arguments passed to the tool.

        Returns:
            A short summary string like ``"factorial.py"``.
        """
        return _tool_args_preview(tool_name, arguments)
