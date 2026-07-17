"""
Unit tests for janus.core.planner — Planner class.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import MagicMock, patch

from core.planner import Planner
from core.protocol import (
    Directive, ExecutionReport, TaskSpec, TaskResult,
    TaskStatus, Confidence, DecompositionRequest, SubTask,
)
from core.task_manager import TaskManager
from core.worker import Worker, create_default_registry
from core.reviewer import ReviewVerdict, Severity, ReviewIssue, ReviewResult


# ============================================================================
# Helpers
# ============================================================================

def _make_directive(goal="Test goal"):
    return Directive(
        goal=goal,
        intent="Execute the test",
        constraints="Use Python",
        priority="normal",
    )


def _make_worker_factory(status=TaskStatus.SUCCESS):
    def factory(model_override=None):
        worker = MagicMock(spec=Worker)
        worker.run.return_value = TaskResult(
            status=status,
            summary="Done",
            result="Output",
            artifacts=[],
            confidence=Confidence.HIGH,
        )
        worker.console = None
        worker.priority = "normal"
        return worker
    return factory


def _make_stub_planner(worker_factory=None, reviewer=None, max_depth=3):
    if worker_factory is None:
        worker_factory = _make_worker_factory()
    tm = TaskManager()
    return Planner(
        model="deepseek-chat",
        api_key="fake-key",
        task_manager=tm,
        worker_factory=worker_factory,
        reviewer=reviewer,
        max_depth=max_depth,
    )


# ============================================================================
# Planner._plan Tests
# ============================================================================

class TestPlannerPlan(unittest.TestCase):
    """Tests for Planner._plan (decomposition into TaskSpecs)."""

    def test_plan_api_error_returns_empty(self):
        planner = _make_stub_planner()
        planner._client = MagicMock()
        planner._client.chat.completions.create.side_effect = RuntimeError("API down")

        result = planner._plan(_make_directive())
        self.assertEqual(result, [])
        self.assertIn("API call failed", planner._last_error)

    def test_plan_empty_choices_returns_empty(self):
        planner = _make_stub_planner()
        mock_resp = MagicMock()
        mock_resp.choices = []
        planner._client = MagicMock()
        planner._client.chat.completions.create.return_value = mock_resp

        result = planner._plan(_make_directive())
        self.assertEqual(result, [])

    def test_plan_error_dict(self):
        """LLM returns an error dict → should return empty list."""
        planner = _make_stub_planner()
        mock_msg = MagicMock()
        mock_msg.content = '{"error": "Goal too vague"}'
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        planner._client = MagicMock()
        planner._client.chat.completions.create.return_value = mock_resp

        result = planner._plan(_make_directive())
        self.assertEqual(result, [])

    def test_plan_unparseable_returns_empty(self):
        planner = _make_stub_planner()
        mock_msg = MagicMock()
        mock_msg.content = "Not JSON at all"
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        planner._client = MagicMock()
        planner._client.chat.completions.create.return_value = mock_resp

        result = planner._plan(_make_directive())
        self.assertEqual(result, [])

    def test_plan_valid_tasks(self):
        planner = _make_stub_planner()
        mock_msg = MagicMock()
        mock_msg.content = (
            '[{"task_id": "task-1", "description": "Write code", '
            '"acceptance_criteria": "[HARD] Must compile", '
            '"context": "Python project", "intent": "Core logic"}]'
        )
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        planner._client = MagicMock()
        planner._client.chat.completions.create.return_value = mock_resp

        result = planner._plan(_make_directive())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].task_id, "task-1")
        self.assertEqual(result[0].depth, 1)

    def test_plan_multiple_tasks(self):
        planner = _make_stub_planner()
        mock_msg = MagicMock()
        mock_msg.content = (
            '[{"task_id": "task-1", "description": "Part A", '
            '"acceptance_criteria": "[HARD] Works", "context": ""},'
            '{"task_id": "task-2", "description": "Part B", '
            '"acceptance_criteria": "[HARD] Works", "context": ""}]'
        )
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        planner._client = MagicMock()
        planner._client.chat.completions.create.return_value = mock_resp

        result = planner._plan(_make_directive())
        self.assertEqual(len(result), 2)

    def test_plan_skips_malformed_items(self):
        planner = _make_stub_planner()
        mock_msg = MagicMock()
        mock_msg.content = (
            '[{"not_a_task": true}, '
            '{"task_id": "ok", "description": "Valid", '
            '"acceptance_criteria": "Works", "context": ""}]'
        )
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        planner._client = MagicMock()
        planner._client.chat.completions.create.return_value = mock_resp

        result = planner._plan(_make_directive())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].task_id, "ok")

    def test_plan_invalid_spec_skipped(self):
        """TaskSpec with empty task_id or description is skipped."""
        planner = _make_stub_planner()
        mock_msg = MagicMock()
        mock_msg.content = (
            '[{"task_id": "", "description": "", '
            '"acceptance_criteria": "X", "context": ""}]'
        )
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        planner._client = MagicMock()
        planner._client.chat.completions.create.return_value = mock_resp

        result = planner._plan(_make_directive())
        self.assertEqual(result, [])

    def test_plan_with_constraints(self):
        planner = _make_stub_planner()
        mock_msg = MagicMock()
        mock_msg.content = (
            '[{"task_id": "task-1", "description": "Write code", '
            '"acceptance_criteria": "[HARD] Must compile", "context": ""}]'
        )
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        planner._client = MagicMock()
        planner._client.chat.completions.create.return_value = mock_resp

        directive = _make_directive()
        directive.constraints = "Use Python 3.10+, no external deps"
        result = planner._plan(directive)
        self.assertEqual(len(result), 1)

    def test_plan_with_context(self):
        planner = _make_stub_planner()
        mock_msg = MagicMock()
        mock_msg.content = (
            '[{"task_id": "task-1", "description": "Continue work", '
            '"acceptance_criteria": "[HARD] Works", "context": ""}]'
        )
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        planner._client = MagicMock()
        planner._client.chat.completions.create.return_value = mock_resp

        directive = _make_directive()
        directive.context = "--- history ---"
        result = planner._plan(directive)
        self.assertEqual(len(result), 1)


# ============================================================================
# Planner.execute Tests
# ============================================================================

class TestPlannerExecute(unittest.TestCase):
    """Tests for Planner.execute (full pipeline)."""

    def test_execute_with_no_tasks(self):
        planner = _make_stub_planner()
        planner._plan = MagicMock(return_value=[])

        report = planner.execute(_make_directive())
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.total_tasks, 0)

    def test_execute_with_one_task_success(self):
        planner = _make_stub_planner(worker_factory=_make_worker_factory(TaskStatus.SUCCESS))
        planner._plan = MagicMock(return_value=[
            TaskSpec(
                task_id="task-1", description="Do X",
                acceptance_criteria="Works", context="",
            ),
        ])
        # Disable LLM-based _summarize by mocking
        planner._summarize = MagicMock(return_value=ExecutionReport(
            status="completed", total_tasks=1, passed=1, failed=0,
            summary="All done.", details=["✅ task-1 · OK"], goal="Test",
        ))

        report = planner.execute(_make_directive())
        self.assertEqual(report.status, "completed")
        self.assertEqual(report.total_tasks, 1)
        self.assertEqual(report.passed, 1)

    def test_execute_with_task_failure(self):
        planner = _make_stub_planner(worker_factory=_make_worker_factory(TaskStatus.FAILURE))
        planner._plan = MagicMock(return_value=[
            TaskSpec(
                task_id="task-1", description="Do X",
                acceptance_criteria="Works", context="",
            ),
        ])
        planner._summarize = MagicMock(return_value=ExecutionReport(
            status="failed", total_tasks=1, passed=0, failed=1,
            summary="Failed.", details=["❌ task-1 · Error"],
            failed_details=["task-1: error"], goal="Test",
        ))

        report = planner.execute(_make_directive())
        self.assertEqual(report.failed, 1)

    def test_execute_worker_crash_handled(self):
        """Worker factory that crashes should not crash the Planner."""
        def crash_factory(model_override=None):
            raise RuntimeError("Worker factory exploded!")
        planner = _make_stub_planner(worker_factory=crash_factory)
        planner._plan = MagicMock(return_value=[
            TaskSpec(
                task_id="task-1", description="Do X",
                acceptance_criteria="Works", context="",
            ),
        ])
        planner._summarize = MagicMock(return_value=ExecutionReport(
            status="failed", total_tasks=1, passed=0, failed=1,
            summary="Crashed.", details=["❌ task-1 · Factory crashed"],
            goal="Test",
        ))

        # Should not raise
        report = planner.execute(_make_directive())
        self.assertIsInstance(report, ExecutionReport)


# ============================================================================
# Planner._dispatch_with_review Tests
# ============================================================================

class TestPlannerDispatchWithReview(unittest.TestCase):
    """Tests for Planner._dispatch_with_review."""

    def _make_dispatch_planner(self, worker_factory=None, reviewer=None):
        if worker_factory is None:
            worker_factory = _make_worker_factory(TaskStatus.SUCCESS)
        tm = TaskManager()
        return Planner(
            model="deepseek-chat",
            api_key="fake-key",
            task_manager=tm,
            worker_factory=worker_factory,
            reviewer=reviewer,
            max_depth=3,
        )

    def test_dispatch_no_reviewer_accepts_result(self):
        planner = self._make_dispatch_planner(reviewer=None)
        spec = TaskSpec(
            task_id="t1", description="X",
            acceptance_criteria="Y", context="",
        )
        # Register task before dispatch (normally done by execute())
        planner._task_manager.add_task(spec)
        planner._task_manager.mark_running("t1", worker_id="w0")

        result = planner._dispatch_with_review(spec, max_retries=0)
        self.assertEqual(result.status, TaskStatus.SUCCESS)
        self.assertEqual(planner._task_manager.get_summary()["completed"], 1)

    def test_dispatch_failure_skips_review(self):
        """FAILURE result should skip review and mark as failed."""
        planner = self._make_dispatch_planner(
            worker_factory=_make_worker_factory(TaskStatus.FAILURE),
            reviewer=MagicMock(),
        )
        spec = TaskSpec(
            task_id="t1", description="X",
            acceptance_criteria="Y", context="",
        )
        planner._task_manager.add_task(spec)
        planner._task_manager.mark_running("t1", worker_id="w0")

        result = planner._dispatch_with_review(spec, max_retries=0)
        self.assertEqual(result.status, TaskStatus.FAILURE)
        # Reviewer should NOT have been called
        planner._reviewer.review.assert_not_called()
        self.assertEqual(planner._task_manager.get_summary()["failed"], 1)

    def test_dispatch_with_approved_review(self):
        rv = MagicMock()
        rv.review.return_value = ReviewResult(
            verdict=ReviewVerdict.APPROVED,
            summary="All good",
            evidence="Works correctly",
        )
        planner = self._make_dispatch_planner(reviewer=rv)
        spec = TaskSpec(
            task_id="t1", description="X",
            acceptance_criteria="Y", context="",
        )
        planner._task_manager.add_task(spec)
        planner._task_manager.mark_running("t1", worker_id="w0")

        result = planner._dispatch_with_review(spec, max_retries=1)
        self.assertEqual(result.status, TaskStatus.SUCCESS)
        rv.review.assert_called_once()
        self.assertEqual(planner._task_manager.get_summary()["completed"], 1)

    def test_dispatch_with_minor_revisions_retries(self):
        rv = MagicMock()
        # First review: minor revisions, second review should auto-accept
        rv.review.side_effect = [
            ReviewResult(
                verdict=ReviewVerdict.MINOR_REVISIONS,
                summary="Small fixes",
                issues=[ReviewIssue(Severity.MINOR, "Style issue")],
            ),
            ReviewResult(
                verdict=ReviewVerdict.APPROVED,
                summary="Fixed",
                evidence="Looks good now",
            ),
        ]
        planner = self._make_dispatch_planner(reviewer=rv)
        spec = TaskSpec(
            task_id="t1", description="X",
            acceptance_criteria="Y", context="",
        )
        planner._task_manager.add_task(spec)
        planner._task_manager.mark_running("t1", worker_id="w0")

        result = planner._dispatch_with_review(spec, max_retries=2)
        self.assertEqual(result.status, TaskStatus.SUCCESS)
        # Should have been called twice (initial + retry)
        self.assertEqual(rv.review.call_count, 2)
        self.assertEqual(planner._task_manager.get_summary()["completed"], 1)


# ============================================================================
# Planner._summarize Tests
# ============================================================================

class TestPlannerSummarize(unittest.TestCase):
    """Tests for Planner._summarize."""

    def _make_planner(self):
        tm = TaskManager()
        return Planner(
            model="deepseek-chat",
            api_key="fake-key",
            task_manager=tm,
            worker_factory=_make_worker_factory(),
        )

    def test_summarize_all_success(self):
        planner = self._make_planner()
        planner._client = MagicMock()
        mock_msg = MagicMock()
        mock_msg.content = '{"assessment": "All tasks passed successfully.", "issues": []}'
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        planner._client.chat.completions.create.return_value = mock_resp

        results = [
            TaskResult(status=TaskStatus.SUCCESS, summary="Done 1", result="Output 1",
                       worker_id="w1", confidence=Confidence.HIGH),
            TaskResult(status=TaskStatus.SUCCESS, summary="Done 2", result="Output 2",
                       worker_id="w2", confidence=Confidence.HIGH),
        ]
        specs = [
            TaskSpec(task_id="t1", description="X", acceptance_criteria="Y", context=""),
            TaskSpec(task_id="t2", description="Z", acceptance_criteria="W", context=""),
        ]
        report = planner._summarize(results, "Test goal", "", specs)
        self.assertIsInstance(report, ExecutionReport)
        self.assertEqual(report.status, "completed")
        self.assertEqual(report.passed, 2)
        self.assertEqual(report.total_tasks, 2)

    def test_summarize_mixed_results(self):
        planner = self._make_planner()
        planner._client = MagicMock()
        mock_msg = MagicMock()
        mock_msg.content = '{"assessment": "One passed, one failed.", "issues": ["t2: error"]}'
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        planner._client.chat.completions.create.return_value = mock_resp

        results = [
            TaskResult(status=TaskStatus.SUCCESS, summary="Done", result="OK",
                       worker_id="w1", confidence=Confidence.HIGH),
            TaskResult(status=TaskStatus.FAILURE, summary="Failed", result="Error",
                       worker_id="w2", confidence=Confidence.LOW),
        ]
        specs = [
            TaskSpec(task_id="t1", description="X", acceptance_criteria="Y", context=""),
            TaskSpec(task_id="t2", description="Z", acceptance_criteria="W", context=""),
        ]
        report = planner._summarize(results, "Test", "", specs)
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.passed, 1)
        self.assertEqual(report.failed, 1)

    def test_summarize_all_failed(self):
        planner = self._make_planner()
        planner._client = MagicMock()
        mock_msg = MagicMock()
        mock_msg.content = '{"assessment": "All failed.", "issues": ["t1: crash", "t2: crash"]}'
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        planner._client.chat.completions.create.return_value = mock_resp

        results = [
            TaskResult(status=TaskStatus.FAILURE, summary="Crashed", result="Error",
                       worker_id="w1", confidence=Confidence.LOW),
            TaskResult(status=TaskStatus.FAILURE, summary="Crashed", result="Error",
                       worker_id="w2", confidence=Confidence.LOW),
        ]
        specs = [
            TaskSpec(task_id="t1", description="X", acceptance_criteria="Y", context=""),
            TaskSpec(task_id="t2", description="Z", acceptance_criteria="W", context=""),
        ]
        report = planner._summarize(results, "Test", "", specs)
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.passed, 0)
        self.assertEqual(report.failed, 2)

    def test_summarize_api_error_fallback(self):
        planner = self._make_planner()
        planner._client = MagicMock()
        planner._client.chat.completions.create.side_effect = RuntimeError("API down")

        results = [
            TaskResult(status=TaskStatus.SUCCESS, summary="Done", result="OK",
                       worker_id="w1", confidence=Confidence.HIGH),
        ]
        specs = [
            TaskSpec(task_id="t1", description="X", acceptance_criteria="Y", context=""),
        ]
        report = planner._summarize(results, "Test", "", specs)
        self.assertIsInstance(report, ExecutionReport)
        # Should still produce a valid report even on API error
        self.assertEqual(report.total_tasks, 1)
        self.assertEqual(report.passed, 1)


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    unittest.main()
