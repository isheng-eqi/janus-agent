"""
Integration tests for Janus Phase 2 architecture.

Tests Gatekeeper → Planner pipeline with mocked Planner and workers.
Also tests Session → Gatekeeper integration, Worker._parse_result,
and prompts.extract_json.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from core.gatekeeper import Gatekeeper
from core.worker import Worker, create_default_registry
from core.task_manager import TaskManager
from core.protocol import (
    TaskSpec, TaskResult, TaskStatus, Confidence,
    DecompositionRequest, SubTask, Directive, ExecutionReport,
)
from core.prompts import extract_json


# ============================================================================
# Helper factories
# ============================================================================

def _make_simple_spec(task_id="task-1", description="Do something"):
    return TaskSpec(
        task_id=task_id,
        description=description,
        acceptance_criteria="It should work",
        context="No special context",
        depth=1,
    )


def _make_success_report(passed=2, total=2):
    return ExecutionReport(
        status="completed",
        total_tasks=total,
        passed=passed,
        failed=total - passed,
        summary="All done.",
        details=["✅ task-1 · Done", "✅ task-2 · Done"],
        goal="Test goal",
    )


def _make_failure_report(total=2):
    return ExecutionReport(
        status="failed",
        total_tasks=total,
        passed=0,
        failed=total,
        summary="All failed.",
        details=["❌ task-1 · Error", "❌ task-2 · Error"],
        failed_details=["task-1: error", "task-2: error"],
        goal="Test goal",
    )


def _make_partial_report():
    return ExecutionReport(
        status="partial",
        total_tasks=2,
        passed=1,
        failed=1,
        summary="One done, one failed.",
        details=["✅ task-1 · OK", "❌ task-2 · Failed"],
        failed_details=["task-2: something went wrong"],
        goal="Test goal",
    )


# ============================================================================
# Integration Tests: Gatekeeper + Planner
# ============================================================================

class TestGatekeeperPlannerIntegration(unittest.TestCase):
    """Integration tests for Gatekeeper → Planner pipeline."""

    def _make_gatekeeper(self, planner_mock=None):
        """Create a Gatekeeper with a mock Planner."""
        if planner_mock is None:
            planner_mock = MagicMock()
            planner_mock.execute.return_value = _make_success_report()

        gk = Gatekeeper(
            model="deepseek-chat",
            api_key="fake-key-for-testing",
            planner=planner_mock,
        )
        return gk, planner_mock

    def test_handle_routes_to_task_execution(self):
        """handle() with a task goal routes to _execute_via_planner."""
        gk, planner = self._make_gatekeeper()

        # Mock _decide to return task
        gk._decide = MagicMock(return_value={"action": "task", "reason": "It's a task"})
        # Mock _formulate_directive
        gk._formulate_directive = MagicMock(return_value=Directive(
            goal="Do X",
            intent="Execute",
            constraints="",
            priority="normal",
        ))

        result = gk.handle("Write a script.")

        self.assertIsInstance(result, str)
        gk._decide.assert_called_once()
        gk._formulate_directive.assert_called_once()
        planner.execute.assert_called_once()

    def test_handle_routes_to_chat(self):
        """handle() with a chat message routes to _respond."""
        gk, planner = self._make_gatekeeper()

        gk._decide = MagicMock(return_value={"action": "chat", "reason": "Greeting"})
        gk._respond = MagicMock(return_value="Hello! How can I help you?")

        result = gk.handle("Hi there!")

        self.assertEqual(result, "Hello! How can I help you?")
        planner.execute.assert_not_called()

    def test_execute_via_planner_success(self):
        """_execute_via_planner with all-success report."""
        gk, planner = self._make_gatekeeper()
        gk._formulate_directive = MagicMock(return_value=Directive(
            goal="Test goal",
            intent="Execute",
            constraints="",
            priority="normal",
        ))

        result = gk._execute_via_planner("Test goal")

        self.assertIsInstance(result, str)
        self.assertIn("✅", result)
        planner.execute.assert_called_once()

    def test_execute_via_planner_all_failed(self):
        """_execute_via_planner with all-failed report."""
        planner = MagicMock()
        planner.execute.return_value = _make_failure_report()
        planner._last_error = None  # prevent error-path bypass

        gk = Gatekeeper(model="deepseek-chat", api_key="fk", planner=planner)
        gk._formulate_directive = MagicMock(return_value=Directive(
            goal="Test", intent="X", constraints="",
        ))
        # Disable recovery loop for this test
        gk._diagnose_failures = MagicMock(return_value="diagnosis")
        gk._reformulate_for_recovery = MagicMock(return_value=Directive(
            goal="Test", intent="X", constraints="",
        ))
        gk._validate_delivery = MagicMock(return_value={"valid": True})

        result = gk._execute_via_planner("Test")

        self.assertIsInstance(result, str)
        self.assertIn("❌", result)

    def test_execute_via_planner_partial(self):
        """_execute_via_planner with partial-failure report."""
        planner = MagicMock()
        planner.execute.return_value = _make_partial_report()

        gk = Gatekeeper(model="deepseek-chat", api_key="fk", planner=planner)
        gk._formulate_directive = MagicMock(return_value=Directive(
            goal="Test", intent="X", constraints="",
        ))
        # Disable recovery loop for this test
        gk._diagnose_failures = MagicMock(return_value="diagnosis")
        gk._reformulate_for_recovery = MagicMock(return_value=Directive(
            goal="Test", intent="X", constraints="",
        ))
        gk._validate_delivery = MagicMock(return_value={"valid": True})

        result = gk._execute_via_planner("Test")

        self.assertIsInstance(result, str)
        self.assertIn("部分完成", result)

    def test_execute_zero_tasks_report(self):
        """_report_to_user with zero tasks."""
        gk = Gatekeeper(model="x", api_key="x", planner=MagicMock())

        report = ExecutionReport(
            status="failed",
            total_tasks=0,
            passed=0,
            failed=0,
            summary="Planner failed to decompose.",
            details=[],
        )
        result = gk._report_to_user(report)
        self.assertIn("未能执行", result)
        self.assertIn("→", result)

    def test_execute_with_planner_error(self):
        """When report shows zero tasks AND planner has _last_error."""
        planner = MagicMock()
        planner._last_error = "API rate limit exceeded"
        planner.execute.return_value = ExecutionReport(
            status="failed",
            total_tasks=0,
            passed=0,
            failed=0,
            summary="Failed",
            details=[],
        )

        gk = Gatekeeper(model="x", api_key="x", planner=planner)
        gk._formulate_directive = MagicMock(return_value=Directive(
            goal="Test", intent="X", constraints="",
        ))

        result = gk._execute_via_planner("Test")
        self.assertIn("API rate limit exceeded", result)


# ============================================================================
# Gatekeeper._formulate_directive Tests
# ============================================================================

class TestFormulateDirective(unittest.TestCase):
    """Tests for Gatekeeper._formulate_directive."""

    def _make_gk(self):
        gk = Gatekeeper(
            model="deepseek-chat",
            api_key="fake-key",
            planner=MagicMock(),
        )
        return gk

    def test_formulate_directive_api_error_fallback(self):
        """On API error, falls back to template-based Directive."""
        gk = self._make_gk()
        gk._client = MagicMock()
        gk._client.chat.completions.create.side_effect = RuntimeError("API down")

        directive = gk._formulate_directive("Do something")
        self.assertIsInstance(directive, Directive)
        self.assertEqual(directive.goal, "Do something")
        self.assertEqual(directive.priority, "normal")

    def test_formulate_directive_empty_choices_fallback(self):
        """On empty choices, falls back to template Directive."""
        gk = self._make_gk()
        mock_resp = MagicMock()
        mock_resp.choices = []
        gk._client = MagicMock()
        gk._client.chat.completions.create.return_value = mock_resp

        directive = gk._formulate_directive("Do something")
        self.assertIsInstance(directive, Directive)
        self.assertEqual(directive.priority, "normal")

    def test_formulate_directive_success(self):
        """Happy path: LLM returns valid JSON."""
        gk = self._make_gk()

        mock_msg = MagicMock()
        mock_msg.content = '{"intent": "Build a web app", "constraints": "Use Python", "priority": "quality"}'
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        gk._client = MagicMock()
        gk._client.chat.completions.create.return_value = mock_resp

        directive = gk._formulate_directive("Build a web app")
        self.assertIsInstance(directive, Directive)
        self.assertEqual(directive.goal, "Build a web app")
        self.assertEqual(directive.intent, "Build a web app")
        self.assertEqual(directive.constraints, "Use Python")
        self.assertEqual(directive.priority, "quality")

    def test_formulate_directive_with_history_context(self):
        """History context is included in the directive."""
        gk = self._make_gk()

        mock_msg = MagicMock()
        mock_msg.content = '{"intent": "Continue", "constraints": "", "priority": "speed"}'
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        gk._client = MagicMock()
        gk._client.chat.completions.create.return_value = mock_resp

        directive = gk._formulate_directive("Continue task", history_context="--- history ---")
        self.assertEqual(directive.context, "--- history ---")


# ============================================================================
# Gatekeeper._merge_reports Tests
# ============================================================================

class TestMergeReports(unittest.TestCase):
    """Tests for Gatekeeper._merge_reports."""

    def test_merge_two_success_reports(self):
        old = ExecutionReport(
            status="completed", total_tasks=2, passed=2, failed=0,
            summary="First done.", details=["✅ t1 · OK", "✅ t2 · OK"],
            goal="g",
        )
        new = ExecutionReport(
            status="completed", total_tasks=1, passed=1, failed=0,
            summary="Second done.", details=["✅ t3 · OK"],
            goal="g",
        )
        merged = Gatekeeper._merge_reports(old, new)
        self.assertEqual(merged.passed, 3)
        self.assertEqual(merged.failed, 0)
        self.assertEqual(merged.status, "completed")

    def test_merge_with_new_failures(self):
        old = ExecutionReport(
            status="completed", total_tasks=1, passed=1, failed=0,
            summary="OK", details=["✅ t1 · OK"], goal="g",
        )
        new = ExecutionReport(
            status="failed", total_tasks=1, passed=0, failed=1,
            summary="Fail", details=["❌ t2 · Fail"],
            failed_details=["t2: error"], goal="g",
        )
        merged = Gatekeeper._merge_reports(old, new)
        self.assertEqual(merged.passed, 1)
        self.assertEqual(merged.failed, 1)
        self.assertEqual(merged.status, "partial")

    def test_merge_new_zero_tasks_preserves_old_failures(self):
        old = ExecutionReport(
            status="failed", total_tasks=2, passed=0, failed=2,
            summary="All failed", details=["❌ t1", "❌ t2"],
            failed_details=["err1", "err2"], goal="g",
        )
        new = ExecutionReport(
            status="failed", total_tasks=0, passed=0, failed=0,
            summary="Decomp failed", details=[], goal="g",
        )
        merged = Gatekeeper._merge_reports(old, new)
        self.assertEqual(merged.failed, 2)  # Old failures preserved


# ============================================================================
# Gatekeeper._report_to_user Tests
# ============================================================================

class TestReportToUser(unittest.TestCase):
    """Tests for Gatekeeper._report_to_user."""

    def _make_gk(self):
        return Gatekeeper(model="x", api_key="x", planner=MagicMock())

    def test_completed_report(self):
        gk = self._make_gk()
        report = ExecutionReport(
            status="completed", total_tasks=3, passed=3, failed=0,
            summary="All passed", details=["✅ 1", "✅ 2", "✅ 3"],
        )
        result = gk._report_to_user(report)
        self.assertIn("✅", result)
        self.assertIn("完成", result)

    def test_failed_report(self):
        gk = self._make_gk()
        report = ExecutionReport(
            status="failed", total_tasks=2, passed=0, failed=2,
            summary="All failed", details=["❌ 1", "❌ 2"],
        )
        result = gk._report_to_user(report)
        self.assertIn("❌", result)
        self.assertIn("未完成", result)

    def test_partial_report(self):
        gk = self._make_gk()
        report = ExecutionReport(
            status="partial", total_tasks=2, passed=1, failed=1,
            summary="Mixed", details=["✅ 1", "❌ 2"],
        )
        result = gk._report_to_user(report)
        self.assertIn("部分完成", result)

    def test_failed_report_has_suggestions(self):
        gk = self._make_gk()
        report = ExecutionReport(
            status="failed", total_tasks=2, passed=0, failed=2,
            summary="All failed", details=["❌ 1", "❌ 2"],
        )
        result = gk._report_to_user(report)
        self.assertIn("→", result)
        self.assertIn("help", result)

    def test_all_failed_has_help_suggestion(self):
        gk = self._make_gk()
        report = ExecutionReport(
            status="failed", total_tasks=1, passed=0, failed=1,
            summary="Failed", details=["❌ 1"],
        )
        result = gk._report_to_user(report)
        self.assertIn("help", result)


# ============================================================================
# Worker._parse_result tests
# ============================================================================

class TestWorkerParseResult(unittest.TestCase):
    """Tests for Worker._parse_result static method."""

    def test_worker_garbage_json_handled(self):
        result = Worker._parse_result(
            "This is not JSON at all, just random text from the LLM."
        )
        self.assertEqual(result.status, TaskStatus.FAILURE)
        self.assertIn("Could not parse", result.summary)
        self.assertEqual(
            result.result,
            "This is not JSON at all, just random text from the LLM."
        )

    def test_worker_empty_text_handled(self):
        result = Worker._parse_result("")
        self.assertEqual(result.status, TaskStatus.FAILURE)
        self.assertEqual(result.result, "(empty response)")

    def test_worker_parse_valid_json(self):
        json_text = '```json\n{"status": "success", "summary": "Did the thing", "result": "All good", "confidence": "high"}\n```'
        result = Worker._parse_result(json_text)
        self.assertEqual(result.status, TaskStatus.SUCCESS)
        self.assertEqual(result.summary, "Did the thing")

    def test_worker_parse_json_no_fence(self):
        json_text = '{"status": "failure", "summary": "It broke", "result": "Error: 500", "confidence": "low"}'
        result = Worker._parse_result(json_text)
        self.assertEqual(result.status, TaskStatus.FAILURE)
        self.assertEqual(result.summary, "It broke")

    def test_worker_parse_needs_decomposition(self):
        json_text = '''```json
{
  "status": "needs_decomposition",
  "summary": "Too complex",
  "result": "This task needs breaking down",
  "confidence": "medium",
  "decomposition_request": {
    "reason": "Multiple independent concerns",
    "sub_tasks": [
      {"id": "sub-1", "description": "Handle auth", "rationale": "Separate concern"},
      {"id": "sub-2", "description": "Handle data", "rationale": "Separate concern"}
    ]
  }
}
```'''
        result = Worker._parse_result(json_text)
        self.assertEqual(result.status, TaskStatus.NEEDS_DECOMPOSITION)
        self.assertIsNotNone(result.decomposition_request)
        self.assertEqual(len(result.decomposition_request.sub_tasks), 2)

    def test_worker_parse_needs_decomposition_missing_request(self):
        json_text = '{"status": "needs_decomposition", "summary": "X", "result": "Y", "confidence": "medium"}'
        result = Worker._parse_result(json_text)
        self.assertEqual(result.status, TaskStatus.FAILURE)
        self.assertIn("Invalid TaskResult", result.summary)


# ============================================================================
# Session + Gatekeeper Integration
# ============================================================================

class TestSessionIntegration(unittest.TestCase):
    """Integration tests for Session wrapping Gatekeeper."""

    def _make_session_with_gk(self, max_history=100):
        planner = MagicMock()
        planner.execute.return_value = _make_success_report()

        gk = Gatekeeper(
            model="deepseek-chat",
            api_key="fake-key",
            planner=planner,
        )
        return gk, planner

    def test_session_handle_passes_to_gatekeeper(self):
        from core.session import Session

        gk, planner = self._make_session_with_gk()
        gk._decide = MagicMock(return_value={"action": "task", "reason": "task"})
        gk._formulate_directive = MagicMock(return_value=Directive(
            goal="Write a scraper", intent="Build", constraints="", priority="normal",
        ))

        session = Session(gk)
        result = session.handle("Write a scraper")
        self.assertIsInstance(result, str)
        self.assertEqual(len(session.history), 2)

    def test_session_multiple_turns(self):
        from core.session import Session

        gk, planner = self._make_session_with_gk()
        gk._decide = MagicMock(return_value={"action": "task", "reason": "task"})
        gk._formulate_directive = MagicMock(return_value=Directive(
            goal="T", intent="X", constraints="",
        ))

        session = Session(gk)
        session.handle("Task 1")
        session.handle("Task 2")
        session.handle("Task 3")

        self.assertEqual(len(session.history), 6)  # 3 turns * 2

    def test_session_history_truncation(self):
        from core.session import Session

        gk, planner = self._make_session_with_gk()
        gk._decide = MagicMock(return_value={"action": "task", "reason": "task"})
        gk._formulate_directive = MagicMock(return_value=Directive(
            goal="T", intent="X", constraints="",
        ))

        session = Session(gk, max_history=3)
        for i in range(7):
            session.handle(f"Task {i}")

        self.assertLessEqual(len(session.history), 6)

    def test_session_last_result_tracks_latest(self):
        from core.session import Session

        gk, planner = self._make_session_with_gk()

        session = Session(gk)
        result1 = session.handle("First task")
        self.assertEqual(session.last_result, result1)

        result2 = session.handle("Second task")
        self.assertEqual(session.last_result, result2)


# ============================================================================
# extract_json tests
# ============================================================================

class TestExtractJson(unittest.TestCase):
    """Tests for prompts.extract_json."""

    def test_extract_array(self):
        result = extract_json(
            'Here is the decomposition:\n[{"task_id": "t1", "description": "Do X"}]'
        )
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["task_id"], "t1")

    def test_extract_object(self):
        result = extract_json('{"error": "too vague"}')
        self.assertIsInstance(result, dict)
        self.assertEqual(result["error"], "too vague")

    def test_extract_fenced_json(self):
        text = '```json\n[{"task_id": "t1", "description": "Do X"}]\n```'
        result = extract_json(text)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)

    def test_extract_fenced_no_lang(self):
        text = '```\n[{"task_id": "t1", "description": "Do X"}]\n```'
        result = extract_json(text)
        self.assertIsInstance(result, list)

    def test_extract_no_json(self):
        result = extract_json("Just some random text, no braces at all.")
        self.assertIsNone(result)

    def test_extract_nested_object(self):
        text = '{"outer": {"inner": "value"}}'
        result = extract_json(text)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, dict)
        self.assertIn("outer", result)


# ============================================================================
# Gatekeeper._decide Tests
# ============================================================================

class TestGatekeeperDecide(unittest.TestCase):
    """Tests for Gatekeeper._decide."""

    def _make_gk(self):
        return Gatekeeper(
            model="deepseek-chat",
            api_key="fake-key",
            planner=MagicMock(),
        )

    def test_decide_returns_task_on_api_error(self):
        gk = self._make_gk()
        gk._client = MagicMock()
        gk._client.chat.completions.create.side_effect = RuntimeError("API down")

        result = gk._decide("Do something")
        self.assertEqual(result["action"], "task")

    def test_decide_returns_task_on_empty_choices(self):
        gk = self._make_gk()
        mock_resp = MagicMock()
        mock_resp.choices = []
        gk._client = MagicMock()
        gk._client.chat.completions.create.return_value = mock_resp

        result = gk._decide("Do something")
        self.assertEqual(result["action"], "task")

    def test_decide_returns_task_on_unparseable(self):
        gk = self._make_gk()
        mock_msg = MagicMock()
        mock_msg.content = "Not JSON at all"
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        gk._client = MagicMock()
        gk._client.chat.completions.create.return_value = mock_resp

        result = gk._decide("Do something")
        self.assertEqual(result["action"], "task")

    def test_decide_returns_chat_when_llm_says_so(self):
        gk = self._make_gk()
        mock_msg = MagicMock()
        mock_msg.content = '{"action": "chat", "reason": "Greeting"}'
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        gk._client = MagicMock()
        gk._client.chat.completions.create.return_value = mock_resp

        result = gk._decide("Hi there!")
        self.assertEqual(result["action"], "chat")
        self.assertEqual(result["reason"], "Greeting")


# ============================================================================
# Gatekeeper._diagnose_failures Tests
# ============================================================================

class TestDiagnoseFailures(unittest.TestCase):
    """Tests for Gatekeeper._diagnose_failures."""

    def _make_gk(self):
        return Gatekeeper(
            model="deepseek-chat",
            api_key="fake-key",
            planner=MagicMock(),
        )

    def test_diagnose_with_failed_tasks(self):
        gk = self._make_gk()
        report = ExecutionReport(
            status="failed",
            total_tasks=2,
            passed=0,
            failed=2,
            summary="Failed",
            failed_tasks=[
                {"task_id": "t1", "summary": "File not found", "acceptance_criteria": "AC1", "review_issues": "Missing"},
                {"task_id": "t2", "summary": "Timeout", "acceptance_criteria": "AC2", "review_issues": "Slow"},
            ],
        )

        mock_msg = MagicMock()
        mock_msg.content = "Diagnosis: files were missing, network was slow."
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        gk._client = MagicMock()
        gk._client.chat.completions.create.return_value = mock_resp

        diagnosis = gk._diagnose_failures(report, "Original goal")
        self.assertIn("Diagnosis", diagnosis)

    def test_diagnose_api_error_fallback(self):
        gk = self._make_gk()
        report = _make_failure_report()
        gk._client = MagicMock()
        gk._client.chat.completions.create.side_effect = RuntimeError("API down")

        diagnosis = gk._diagnose_failures(report, "Goal")
        self.assertIsInstance(diagnosis, str)
        self.assertIn("API error", diagnosis)

    def test_diagnose_empty_choices(self):
        gk = self._make_gk()
        report = _make_failure_report()
        mock_resp = MagicMock()
        mock_resp.choices = []
        gk._client = MagicMock()
        gk._client.chat.completions.create.return_value = mock_resp

        diagnosis = gk._diagnose_failures(report, "Goal")
        self.assertIsInstance(diagnosis, str)


# ============================================================================
# HermesClient parse output tests
# ============================================================================

class TestHermesClientParse(unittest.TestCase):
    """Tests for HermesClient._parse_output."""

    def test_parse_valid_output(self):
        from core.hermes_client import HermesClient

        stdout = 'Some preamble text\n{"status": "success", "summary": "OK", "result": "All done", "confidence": "high"}'
        result = HermesClient._parse_output(stdout, "test-worker")
        self.assertEqual(result.status, TaskStatus.SUCCESS)
        self.assertEqual(result.worker_id, "test-worker")
        self.assertEqual(result.summary, "OK")

    def test_parse_nested_json(self):
        from core.hermes_client import HermesClient

        stdout = '{"status": "success", "summary": "OK", "result": "{\\"nested\\": \\"value\\"}", "confidence": "high"}'
        result = HermesClient._parse_output(stdout, "test-worker")
        self.assertEqual(result.status, TaskStatus.SUCCESS)
        self.assertIn("nested", result.result)

    def test_parse_failure_output(self):
        from core.hermes_client import HermesClient

        stdout = '{"status": "failure", "summary": "Bad", "result": "Error 500", "confidence": "low"}'
        result = HermesClient._parse_output(stdout, "test-worker")
        self.assertEqual(result.status, TaskStatus.FAILURE)

    def test_parse_empty_output(self):
        from core.hermes_client import HermesClient

        result = HermesClient._parse_output("", "test-worker")
        self.assertEqual(result.status, TaskStatus.FAILURE)
        self.assertIn("no output", result.summary.lower())

    def test_parse_garbage_output(self):
        from core.hermes_client import HermesClient

        stdout = "This is not JSON at all."
        result = HermesClient._parse_output(stdout, "test-worker")
        self.assertEqual(result.status, TaskStatus.FAILURE)
        self.assertIn("Could not parse", result.summary)

    def test_parse_json_with_leading_trailing_text(self):
        from core.hermes_client import HermesClient

        stdout = (
            "I'll now complete this task.\n"
            'Let me write the result:\n'
            '{"status": "success", "summary": "OK", "result": "Done", "confidence": "high"}\n'
            "Task complete!"
        )
        result = HermesClient._parse_output(stdout, "test-worker")
        self.assertEqual(result.status, TaskStatus.SUCCESS)

    def test_parse_multiple_json_objects_last_wins(self):
        from core.hermes_client import HermesClient

        stdout = (
            '{"config": "some value"}\n'
            '{"status": "failure", "summary": "First attempt", "result": "X", "confidence": "low"}\n'
            '{"status": "success", "summary": "Final", "result": "Y", "confidence": "high"}'
        )
        result = HermesClient._parse_output(stdout, "test-worker")
        self.assertEqual(result.status, TaskStatus.SUCCESS)
        self.assertEqual(result.summary, "Final")


# ============================================================================
# Gatekeeper._reformulate_for_recovery Tests
# ============================================================================

class TestReformulateForRecovery(unittest.TestCase):
    """Tests for Gatekeeper._reformulate_for_recovery."""

    def _make_gk(self):
        return Gatekeeper(
            model="deepseek-chat",
            api_key="fake-key",
            planner=MagicMock(),
        )

    def test_reformulate_with_success_data(self):
        gk = self._make_gk()
        report = ExecutionReport(
            status="partial", total_tasks=3, passed=1, failed=2,
            summary="Partial",
            details=["✅ t1 · Done", "❌ t2 · Fail", "❌ t3 · Fail"],
            failed_details=["t2: error", "t3: error"],
            constraints="Use Python",
        )
        diagnosis = "Tasks failed due to network issues."

        mock_msg = MagicMock()
        mock_msg.content = '{"intent": "Retry with network checks", "constraints": "Use Python, add retries", "priority": "quality"}'
        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        gk._client = MagicMock()
        gk._client.chat.completions.create.return_value = mock_resp

        directive = gk._reformulate_for_recovery("Original goal", report, diagnosis, 0)
        self.assertIsInstance(directive, Directive)
        self.assertIn("Original goal", directive.goal)
        self.assertEqual(directive.priority, "quality")

    def test_reformulate_api_error_fallback(self):
        gk = self._make_gk()
        report = _make_failure_report()
        gk._client = MagicMock()
        gk._client.chat.completions.create.side_effect = RuntimeError("API down")

        directive = gk._reformulate_for_recovery("Goal", report, "Diagnosis", 0)
        self.assertIsInstance(directive, Directive)
        self.assertIn("RECOVERY", directive.goal)


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    unittest.main()
