"""Unicode capability detection and fallback mapping for Janus Console."""

import os
import sys

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
    # MSYS2 / Git Bash: claims TERM=xterm-256color but cannot reliably
    # render box-drawing characters (─│┌└) and decorative Unicode symbols.
    # Reference: Hermes Agent PR #24309 (display_compat.py)
    if env.get('MSYSTEM'):
        return False
    ostype = (env.get('OSTYPE') or '').lower()
    term = (env.get('TERM') or '').lower()
    if any(t in ostype or t in term for t in ('msys', 'mingw')):
        return False
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


# ── Global state ─────────────────────────────────────────────────────────────

_UNICODE_OK: bool = _supports_unicode()
_FORCE_ASCII: bool = False


def set_force_ascii(value: bool = True) -> None:
    """Force ASCII-only output (for --ascii-only CLI flag)."""
    global _FORCE_ASCII
    _FORCE_ASCII = value


def _use_unicode() -> bool:
    return _UNICODE_OK and not _FORCE_ASCII


# ── Symbol constants (lazy, computed once) ───────────────────────────────────

class Symbols:
    """Unicode or ASCII symbols depending on terminal capability."""

    # Box-drawing
    HLINE: str = '─'  if _use_unicode() else '-'
    VLINE: str = '│'  if _use_unicode() else '|'
    TOP_L: str = '┌'  if _use_unicode() else '+'
    TOP_R: str = '┐'  if _use_unicode() else '+'
    BOT_L: str = '└'  if _use_unicode() else '+'
    BOT_R: str = '┘'  if _use_unicode() else '+'

    # Status
    CHECK: str = '✓'  if _use_unicode() else '[OK]'
    CROSS: str = '✗'  if _use_unicode() else '[FAIL]'
    ARROW: str = '→'  if _use_unicode() else '->'
    CLOCK: str = '⏱'  if _use_unicode() else ''

    # Punctuation
    ELLIP:  str = '…'  if _use_unicode() else '...'
    EM_DASH: str = '—' if _use_unicode() else '--'

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
        if _use_unicode():
            return labels_zh.get(key, key)
        return labels_en.get(key, key)

    # Phase / status labels
    PHASE_DECOMPOSE: str = '分析完成' if _use_unicode() else 'DECOMPOSED'
    STATUS_DONE:      str = '完成'     if _use_unicode() else 'DONE'
    STATUS_FAIL:      str = '未完成'   if _use_unicode() else 'FAIL'
    STATUS_DECOMP:    str = '需分解'    if _use_unicode() else 'DECOMPOSE'
    REVIEW_PASS:      str = '通过'     if _use_unicode() else 'PASS'
    REVIEW_FAIL:      str = '未通过'    if _use_unicode() else 'FAIL'
    WORKING:          str = '思考中...' if _use_unicode() else 'Thinking...'
    SUMMARY:          str = '汇总'     if _use_unicode() else 'SUMMARY'


def supports_unicode() -> bool:
    """Public API: does the current terminal support Unicode?"""
    return _use_unicode()
