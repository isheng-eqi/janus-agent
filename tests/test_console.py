"""
Unit tests for janus.core.console — Console class modes and output formatting.
"""

import sys
import os
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import patch

from core.console import Console, _tool_args_preview


# ============================================================================
# Console Mode Tests
# ============================================================================

class TestConsoleModes(unittest.TestCase):
    """Tests for Console mode configuration."""

    def test_default_mode(self):
        c = Console()
        self.assertEqual(c.mode, "default")
        self.assertFalse(c.is_verbose)
        self.assertFalse(c.is_quiet)

    def test_verbose_mode(self):
        c = Console(mode="verbose")
        self.assertEqual(c.mode, "verbose")
        self.assertTrue(c.is_verbose)
        self.assertFalse(c.is_quiet)

    def test_quiet_mode(self):
        c = Console(mode="quiet")
        self.assertEqual(c.mode, "quiet")
        self.assertFalse(c.is_verbose)
        self.assertTrue(c.is_quiet)

    def test_invalid_mode_raises(self):
        with self.assertRaises(ValueError):
            Console(mode="invalid")


# ============================================================================
# Console Output Tests (quiet mode suppresses everything)
# ============================================================================

class TestConsoleQuietMode(unittest.TestCase):
    """Tests that quiet mode suppresses all output."""

    def setUp(self):
        self.c = Console(mode="quiet")

    def test_phase_decompose_suppressed(self):
        with patch("builtins.print") as mock_print:
            self.c.phase_decompose(3, "tasks")
            mock_print.assert_not_called()

    def test_task_start_suppressed(self):
        with patch("builtins.print") as mock_print:
            self.c.task_start("t1", "Do X")
            mock_print.assert_not_called()

    def test_task_done_suppressed(self):
        with patch("builtins.print") as mock_print:
            self.c.task_done("t1", "success", 1.0)
            mock_print.assert_not_called()

    def test_tool_call_suppressed(self):
        with patch("builtins.print") as mock_print:
            self.c.tool_call("write_file", "test.py")
            mock_print.assert_not_called()

    def test_tool_call_verbose_suppressed(self):
        with patch("builtins.print") as mock_print:
            self.c.tool_call_verbose("write_file", {"path": "x"})
            mock_print.assert_not_called()

    def test_review_pass_suppressed(self):
        with patch("builtins.print") as mock_print:
            self.c.review_pass("t1", "evidence")
            mock_print.assert_not_called()

    def test_review_fail_suppressed(self):
        with patch("builtins.print") as mock_print:
            self.c.review_fail("t1", ["issue1"], 0)
            mock_print.assert_not_called()

    def test_error_suppressed(self):
        with patch("builtins.print") as mock_print:
            self.c.error("phase", "message")
            mock_print.assert_not_called()

    def test_think_block_suppressed(self):
        with patch("builtins.print") as mock_print:
            self.c.think_block("thought", "Gatekeeper")
            mock_print.assert_not_called()


# ============================================================================
# Console Output Tests (default mode)
# ============================================================================

class TestConsoleDefaultMode(unittest.TestCase):
    """Tests that default mode produces output."""

    def setUp(self):
        self.c = Console(mode="default")

    def test_phase_decompose_outputs(self):
        with patch("builtins.print") as mock_print:
            self.c.phase_decompose(2, "  ✓ Task 1\n  ✓ Task 2")
            mock_print.assert_called()
            # phase_decompose now prints: blank line → title → blank line → tasks
            # First non-empty call should be the title
            all_calls = [c[0] for c in mock_print.call_args_list if c[0]]
            title_call = all_calls[0][0]
            self.assertIn("分析完成", title_call)
            self.assertIn("2", title_call)

    def test_task_start_outputs(self):
        with patch("builtins.print") as mock_print:
            self.c.task_start("task-1", "Implement factorial")
            mock_print.assert_called()

    def test_task_done_success(self):
        with patch("builtins.print") as mock_print:
            self.c.task_done("task-1", "success", 3.14)
            mock_print.assert_called()
            # Check for "完成" (Taiji: 通过 → 完成)
            all_output = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
            self.assertTrue(any("完成" in o for o in all_output))

    def test_task_done_failure(self):
        with patch("builtins.print") as mock_print:
            self.c.task_done("task-1", "failure", 1.5)
            mock_print.assert_called()
            all_output = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
            self.assertTrue(any("未完成" in o for o in all_output))

    def test_task_done_needs_decomposition(self):
        with patch("builtins.print") as mock_print:
            self.c.task_done("task-1", "needs_decomposition", 2.0)
            all_output = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
            self.assertTrue(any("需分解" in o for o in all_output))

    def test_tool_call_outputs(self):
        with patch("builtins.print") as mock_print:
            self.c.tool_call("write_file", "test.py")
            mock_print.assert_called()

    def test_error_outputs(self):
        with patch("builtins.print") as mock_print:
            self.c.error("分解", "API调用失败")
            mock_print.assert_called()

    def test_summary_all_pass(self):
        with patch("builtins.print") as mock_print:
            self.c.summary(3, 3, 0)
            all_output = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
            self.assertTrue(any("3/3" in o for o in all_output))

    def test_summary_mixed(self):
        with patch("builtins.print") as mock_print:
            self.c.summary(3, 2, 1)
            all_output = [str(c[0][0]) for c in mock_print.call_args_list if c[0]]
            self.assertTrue(any("完成" in o for o in all_output))
            self.assertTrue(any("未完成" in o for o in all_output))


# ============================================================================
# Console Summary Tests (quiet mode)
# ============================================================================

class TestConsoleSummaryQuiet(unittest.TestCase):
    """Tests that quiet mode summary is minimal."""

    def test_quiet_summary_success(self):
        c = Console(mode="quiet")
        with patch("builtins.print") as mock_print:
            c.summary(3, 3, 0)
            mock_print.assert_called_once()
            output = mock_print.call_args[0][0]
            self.assertIn("完成", output)
            self.assertNotIn("━", output)

    def test_quiet_summary_failure(self):
        c = Console(mode="quiet")
        with patch("builtins.print") as mock_print:
            c.summary(3, 1, 2)
            mock_print.assert_called_once()
            output = mock_print.call_args[0][0]
            self.assertIn("未完成", output)


# ============================================================================
# Think Block Tests (verbose only)
# ============================================================================

class TestThinkBlock(unittest.TestCase):
    """Tests for think_block in verbose mode."""

    def test_verbose_shows_think_block(self):
        c = Console(mode="verbose")
        with patch("builtins.print") as mock_print:
            c.think_block("Strategic reasoning here...")
            mock_print.assert_called()

    def test_default_hides_think_block(self):
        c = Console(mode="default")
        with patch("builtins.print") as mock_print:
            c.think_block("Strategic reasoning here...")
            mock_print.assert_not_called()

    def test_long_think_block_truncated(self):
        c = Console(mode="verbose")
        long_text = "X" * 600
        with patch("builtins.print") as mock_print:
            c.think_block(long_text)
            called_text = mock_print.call_args[0][0]
            self.assertIn("省略", called_text)


# ============================================================================
# _tool_args_preview Tests
# ============================================================================

class TestToolArgsPreview(unittest.TestCase):
    """Tests for _tool_args_preview helper."""

    def test_read_file(self):
        result = _tool_args_preview("read_file", {"path": "/tmp/test.py"})
        self.assertEqual(result, "/tmp/test.py")

    def test_write_file(self):
        result = _tool_args_preview("write_file", {"path": "output.txt"})
        self.assertEqual(result, "output.txt")

    def test_terminal(self):
        result = _tool_args_preview("terminal", {"command": "pytest -v"})
        self.assertEqual(result, "pytest -v")

    def test_web_search(self):
        result = _tool_args_preview("web_search", {"query": "Python testing"})
        self.assertEqual(result, "Python testing")

    def test_unknown_tool(self):
        result = _tool_args_preview("unknown", {"foo": "bar"})
        self.assertEqual(result, "")

    def test_terminal_with_cmd_alias(self):
        """Uses 'command' first, falls back to 'cmd'."""
        result = _tool_args_preview("terminal", {"cmd": "ls"})
        self.assertEqual(result, "ls")

    def test_search_files_with_pattern(self):
        result = _tool_args_preview("search_files", {"pattern": "*.py"})
        self.assertEqual(result, "*.py")


# ============================================================================
# build_tool_summary Tests
# ============================================================================

class TestBuildToolSummary(unittest.TestCase):
    """Tests for Console.build_tool_summary."""

    def test_build_tool_summary_delegates(self):
        c = Console()
        result = c.build_tool_summary("write_file", {"path": "hello.py"})
        self.assertEqual(result, "hello.py")


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    unittest.main()
