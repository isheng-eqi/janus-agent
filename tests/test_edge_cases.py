"""
Edge case tests for Janus Phase 2 architecture.

Tests boundary conditions, error handling, and internal logic for:
- prompts.extract_json (formerly Gatekeeper._extract_json)
- Session (new Phase 2 thin pass-through API)
- Worker._parse_result
- Worker self-decomposition boundary conditions
- Protocol validation
- TaskManager edge cases
- ToolRegistry edge cases
- HermesClient prompt building (backward compat)
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from core.gatekeeper import Gatekeeper
from core.session import Session
from core.worker import Worker, create_default_registry, ToolRegistry, ToolDef
from core.task_manager import TaskManager, TaskState
from core.protocol import (
    TaskSpec, TaskResult, TaskStatus, Confidence,
    DecompositionRequest, SubTask, Directive, ExecutionReport,
)
from core.prompts import extract_json, context_discipline_prompt
from core.hermes_client import HermesWorkerConfig, HermesClient


# ============================================================================
# Helpers
# ============================================================================

def _make_stub_worker(status, summary, result_text, confidence=Confidence.MEDIUM):
    worker = MagicMock(spec=Worker)
    worker.run.return_value = TaskResult(
        status=status,
        summary=summary,
        result=result_text,
        confidence=confidence,
    )
    return worker


def _make_spec(task_id="task-1", description="Do something", depth=1):
    return TaskSpec(
        task_id=task_id,
        description=description,
        acceptance_criteria="Should work",
        context="No context",
        depth=depth,
    )


# ============================================================================
# 1. extract_json Tests (was Gatekeeper._extract_json)
# ============================================================================

class TestExtractJson(unittest.TestCase):
    """Tests for prompts.extract_json — the shared JSON extraction function."""

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

    def test_extract_with_leading_text(self):
        text = 'Some preamble\n{"key": "value"}'
        result = extract_json(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["key"], "value")

    def test_extract_empty_string(self):
        self.assertIsNone(extract_json(""))

    def test_extract_unbalanced_braces(self):
        self.assertIsNone(extract_json('{"a": "b"'))

    def test_extract_with_escaped_quotes(self):
        text = '{"msg": "hello \\"world\\""}'
        result = extract_json(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["msg"], 'hello "world"')


# ============================================================================
# 2. context_discipline_prompt Tests
# ============================================================================

class TestContextDisciplinePrompt(unittest.TestCase):
    """Tests for prompts.context_discipline_prompt."""

    def test_includes_role_and_job_descriptions(self):
        prompt = context_discipline_prompt("the strategist", "direction setting")
        self.assertIn("the strategist", prompt)
        self.assertIn("direction setting", prompt)

    def test_includes_key_phrases(self):
        prompt = context_discipline_prompt("X", "Y")
        self.assertIn("Context discipline", prompt)
        self.assertIn("architecture-level", prompt)


# ============================================================================
# 3. Session Tests (Phase 2 — thin pass-through)
# ============================================================================

class TestSession(unittest.TestCase):
    """Tests for Session — Phase 2 thin pass-through API."""

    def _make_session(self, max_history=100):
        gk = MagicMock(spec=Gatekeeper)
        gk.handle.return_value = "Gatekeeper response."
        return Session(gk, max_history=max_history), gk

    def test_handle_passes_to_gatekeeper_with_history_context(self):
        session, gk = self._make_session()
        result = session.handle("Hello")
        self.assertEqual(result, "Gatekeeper response.")
        gk.handle.assert_called_once()
        # history_context is a keyword arg, should be empty for first call
        self.assertEqual(gk.handle.call_args.kwargs.get("history_context"), "")

    def test_history_grows_with_turns(self):
        session, gk = self._make_session()
        session.handle("Turn 1")
        session.handle("Turn 2")
        session.handle("Turn 3")
        # Each turn = 2 entries (user + assistant)
        self.assertEqual(len(session.history), 6)

    def test_history_property_returns_copy(self):
        session, gk = self._make_session()
        session.handle("Hello")
        hist = session.history
        hist.append({"role": "user", "content": "extra"})
        # Internal history should be unchanged
        self.assertEqual(len(session.history), 2)

    def test_last_result_property(self):
        session, gk = self._make_session()
        session.handle("Hello")
        self.assertEqual(session.last_result, "Gatekeeper response.")

    def test_last_result_none_for_new_session(self):
        session, gk = self._make_session()
        self.assertIsNone(session.last_result)

    def test_max_history_truncation(self):
        session, gk = self._make_session(max_history=3)
        for i in range(5):
            session.handle(f"Turn {i}")
        self.assertLessEqual(len(session.history), 6)  # 3 turns * 2

    def test_history_context_includes_recent_turns(self):
        session, gk = self._make_session()
        session.handle("First task")
        session.handle("Second task")

        # Check that history_context is passed to second and subsequent calls
        gk.handle.assert_called()
        # The second call should have a non-empty history_context
        last_call_context = gk.handle.call_args_list[-1].kwargs.get("history_context", "")
        self.assertIn("Recent Conversation", last_call_context)
        self.assertIn("First task", last_call_context)

    def test_empty_input_does_not_crash(self):
        session, gk = self._make_session()
        result = session.handle("")
        self.assertIsInstance(result, str)


# ============================================================================
# 4. Worker._parse_result Tests
# ============================================================================

class TestWorkerParseResult(unittest.TestCase):
    """Tests for Worker._parse_result static method."""

    def test_parse_valid_success_json(self):
        json_text = '{"status": "success", "summary": "Done", "result": "All good", "confidence": "high"}'
        result = Worker._parse_result(json_text)
        self.assertEqual(result.status, TaskStatus.SUCCESS)
        self.assertEqual(result.summary, "Done")
        self.assertEqual(result.confidence, Confidence.HIGH)

    def test_parse_valid_failure_json(self):
        json_text = '{"status": "failure", "summary": "Broke", "result": "Error 500", "confidence": "low"}'
        result = Worker._parse_result(json_text)
        self.assertEqual(result.status, TaskStatus.FAILURE)

    def test_parse_garbage_text(self):
        result = Worker._parse_result("Not JSON at all!")
        self.assertEqual(result.status, TaskStatus.FAILURE)
        self.assertIn("Could not parse", result.summary)

    def test_parse_empty_string(self):
        result = Worker._parse_result("")
        self.assertEqual(result.status, TaskStatus.FAILURE)
        self.assertEqual(result.result, "(empty response)")

    def test_parse_json_in_fences(self):
        json_text = '```json\n{"status": "success", "summary": "OK", "result": "Done", "confidence": "high"}\n```'
        result = Worker._parse_result(json_text)
        self.assertEqual(result.status, TaskStatus.SUCCESS)

    def test_parse_needs_decomposition(self):
        # Use code fence to force top-level object extraction
        json_text = '```json\n{"status": "needs_decomposition", "summary": "Too complex", "result": "Needs breakdown", "confidence": "medium", "decomposition_request": {"reason": "Multiple concerns", "sub_tasks": [{"id": "s1", "description": "Part A", "rationale": "Separate"}]}}\n```'
        result = Worker._parse_result(json_text)
        self.assertEqual(result.status, TaskStatus.NEEDS_DECOMPOSITION)
        self.assertIsNotNone(result.decomposition_request)
        self.assertEqual(len(result.decomposition_request.sub_tasks), 1)

    def test_parse_needs_decomposition_missing_request(self):
        json_text = '{"status": "needs_decomposition", "summary": "X", "result": "Y", "confidence": "medium"}'
        result = Worker._parse_result(json_text)
        self.assertEqual(result.status, TaskStatus.FAILURE)
        self.assertIn("Invalid TaskResult", result.summary)

    def test_parse_default_confidence(self):
        json_text = '{"status": "success", "summary": "OK", "result": "Done"}'
        result = Worker._parse_result(json_text)
        self.assertEqual(result.confidence, Confidence.MEDIUM)

    def test_parse_with_leading_text(self):
        json_text = 'Some preamble\n{"status": "success", "summary": "OK", "result": "Done", "confidence": "high"}\nPost text'
        result = Worker._parse_result(json_text)
        self.assertEqual(result.status, TaskStatus.SUCCESS)

    def test_parse_partial_json(self):
        result = Worker._parse_result('{"status": "success"')
        self.assertEqual(result.status, TaskStatus.FAILURE)


# ============================================================================
# 5. Worker Self-Decomposition Boundary Tests
# ============================================================================

class TestWorkerSelfDecomposition(unittest.TestCase):
    """Tests for Worker.run() self-decomposition boundaries."""

    def _make_worker(self, max_depth=3):
        registry = create_default_registry()
        return Worker(
            model="deepseek-chat",
            api_key="fake-key",
            registry=registry,
            max_depth=max_depth,
        )

    def test_depth_limit_blocks_decomposition(self):
        """When depth >= max_depth, NEEDS_DECOMPOSITION is rejected."""
        worker = self._make_worker(max_depth=1)
        # Mock _execute_loop to return NEEDS_DECOMPOSITION
        worker._execute_loop = MagicMock(return_value=TaskResult(
            status=TaskStatus.NEEDS_DECOMPOSITION,
            summary="Too complex",
            result="...",
            decomposition_request=DecompositionRequest(
                reason="Need breakdown",
                sub_tasks=[SubTask(id="s1", description="Part 1", rationale="Separate")],
            ),
            confidence=Confidence.MEDIUM,
        ))

        spec = _make_spec(task_id="t1", depth=1)
        result = worker.run(spec)
        self.assertEqual(result.status, TaskStatus.FAILURE)
        self.assertIn("Depth limit", result.summary)

    def test_empty_decomposition_request_rejected(self):
        """NEEDS_DECOMPOSITION without decomposition_request fails."""
        worker = self._make_worker(max_depth=3)
        worker._execute_loop = MagicMock(return_value=TaskResult(
            status=TaskStatus.NEEDS_DECOMPOSITION,
            summary="Need breakdown",
            result="...",
            decomposition_request=None,
            confidence=Confidence.MEDIUM,
        ))

        spec = _make_spec(task_id="t1", depth=1)
        result = worker.run(spec)
        self.assertEqual(result.status, TaskStatus.FAILURE)
        self.assertIn("without valid decomposition_request", result.summary)

    def test_empty_sub_tasks_rejected(self):
        """NEEDS_DECOMPOSITION with empty sub_tasks fails."""
        worker = self._make_worker(max_depth=3)
        worker._execute_loop = MagicMock(return_value=TaskResult(
            status=TaskStatus.NEEDS_DECOMPOSITION,
            summary="Need breakdown",
            result="...",
            decomposition_request=DecompositionRequest(reason="big", sub_tasks=[]),
            confidence=Confidence.MEDIUM,
        ))

        spec = _make_spec(task_id="t1", depth=1)
        result = worker.run(spec)
        self.assertEqual(result.status, TaskStatus.FAILURE)
        self.assertIn("without valid decomposition_request", result.summary)


# ============================================================================
# 6. Protocol / TaskResult Validation Tests
# ============================================================================

class TestProtocolValidation(unittest.TestCase):
    """Tests for TaskResult validation and serialization."""

    def test_validate_needs_decomp_with_request_passes(self):
        tr = TaskResult(
            status=TaskStatus.NEEDS_DECOMPOSITION,
            summary="Needs breakdown",
            result="...",
            decomposition_request=DecompositionRequest(
                reason="Too complex",
                sub_tasks=[SubTask(id="s1", description="Part 1", rationale="Independent")],
            ),
        )
        self.assertTrue(tr.validate())

    def test_validate_needs_decomp_without_request_fails(self):
        tr = TaskResult(
            status=TaskStatus.NEEDS_DECOMPOSITION,
            summary="Needs breakdown",
            result="...",
        )
        self.assertFalse(tr.validate())

    def test_validate_needs_decomp_with_empty_subtasks_fails(self):
        tr = TaskResult(
            status=TaskStatus.NEEDS_DECOMPOSITION,
            summary="Needs breakdown",
            result="...",
            decomposition_request=DecompositionRequest(reason="Too complex", sub_tasks=[]),
        )
        self.assertFalse(tr.validate())

    def test_validate_success_always_passes(self):
        tr = TaskResult(status=TaskStatus.SUCCESS, summary="Done", result="OK")
        self.assertTrue(tr.validate())

    def test_validate_failure_always_passes(self):
        tr = TaskResult(status=TaskStatus.FAILURE, summary="Crashed", result="Error")
        self.assertTrue(tr.validate())

    def test_to_dict_and_from_dict_roundtrip(self):
        original = TaskResult(
            status=TaskStatus.SUCCESS,
            summary="All good",
            result="Output data",
            artifacts=["/tmp/output.txt"],
            confidence=Confidence.HIGH,
            worker_id="worker-1",
            decomposition_request=DecompositionRequest(
                reason="Was complex",
                sub_tasks=[SubTask(id="s1", description="Part A", rationale="Separate")],
            ),
        )
        d = original.to_dict()
        restored = TaskResult.from_dict(d)
        self.assertEqual(restored.status, original.status)
        self.assertEqual(restored.summary, original.summary)
        self.assertEqual(restored.result, original.result)
        self.assertEqual(restored.artifacts, original.artifacts)
        self.assertEqual(restored.confidence, original.confidence)
        self.assertEqual(restored.worker_id, original.worker_id)
        self.assertIsNotNone(restored.decomposition_request)
        self.assertEqual(
            restored.decomposition_request.reason,
            original.decomposition_request.reason,
        )

    def test_from_dict_without_decomposition_request(self):
        d = {"status": "success", "summary": "Done", "result": "OK", "artifacts": [], "confidence": "high"}
        tr = TaskResult.from_dict(d)
        self.assertEqual(tr.status, TaskStatus.SUCCESS)
        self.assertIsNone(tr.decomposition_request)

    def test_from_dict_without_optional_fields(self):
        d = {"status": "failure", "summary": "Bad", "result": "Error"}
        tr = TaskResult.from_dict(d)
        self.assertEqual(tr.status, TaskStatus.FAILURE)
        self.assertEqual(tr.artifacts, [])
        self.assertEqual(tr.confidence, Confidence.MEDIUM)
        self.assertIsNone(tr.worker_id)

    def test_from_dict_unknown_status(self):
        with self.assertRaises(ValueError):
            TaskResult.from_dict({"status": "bogus", "summary": "x", "result": "y"})

    def test_sub_review_failed_field(self):
        tr = TaskResult(status=TaskStatus.FAILURE, summary="Bad", result="X", sub_review_failed=True)
        self.assertTrue(tr.sub_review_failed)

    def test_task_spec_validate_empty_id(self):
        spec = TaskSpec(task_id="", description="X", acceptance_criteria="Y", context="Z")
        self.assertFalse(spec.validate())

    def test_task_spec_validate_empty_description(self):
        spec = TaskSpec(task_id="t1", description="", acceptance_criteria="Y", context="Z")
        self.assertFalse(spec.validate())

    def test_task_spec_validate_empty_both(self):
        spec = TaskSpec(task_id="", description="", acceptance_criteria="Y", context="Z")
        self.assertFalse(spec.validate())


# ============================================================================
# 7. TaskManager Edge Cases
# ============================================================================

class TestTaskManagerEdgeCases(unittest.TestCase):
    """Edge case tests for TaskManager."""

    def test_add_task_returns_task_id(self):
        tm = TaskManager()
        spec = TaskSpec(task_id="my-task", description="X", acceptance_criteria="Y", context="Z")
        tid = tm.add_task(spec)
        self.assertEqual(tid, "my-task")

    def test_mark_running_unknown_task_raises(self):
        tm = TaskManager()
        with self.assertRaises(ValueError):
            tm.mark_running("nonexistent", worker_id="w1")

    def test_mark_completed_unknown_task_raises(self):
        tm = TaskManager()
        tr = TaskResult(status=TaskStatus.SUCCESS, summary="OK", result="OK")
        with self.assertRaises(ValueError):
            tm.mark_completed("nonexistent", tr)

    def test_invalid_transition_raises(self):
        tm = TaskManager()
        spec = TaskSpec(task_id="t1", description="X", acceptance_criteria="Y", context="Z")
        tm.add_task(spec)
        tr = TaskResult(status=TaskStatus.SUCCESS, summary="OK", result="OK")
        with self.assertRaises(ValueError):
            tm.mark_completed("t1", tr)

    def test_double_completion_raises(self):
        tm = TaskManager()
        spec = TaskSpec(task_id="t1", description="X", acceptance_criteria="Y", context="Z")
        tm.add_task(spec)
        tm.mark_running("t1", worker_id="w1")
        tr = TaskResult(status=TaskStatus.SUCCESS, summary="OK", result="OK")
        tm.mark_completed("t1", tr)
        with self.assertRaises(ValueError):
            tm.mark_completed("t1", tr)

    def test_mark_failed_stores_error_as_taskresult(self):
        tm = TaskManager()
        spec = TaskSpec(task_id="t1", description="X", acceptance_criteria="Y", context="Z")
        tm.add_task(spec)
        tm.mark_running("t1", worker_id="w1")
        tm.mark_failed("t1", "Connection refused")
        record = tm.get_task("t1")
        self.assertEqual(record.state, TaskState.FAILED)
        self.assertIsNotNone(record.result)
        self.assertEqual(record.result.status, TaskStatus.FAILURE)
        self.assertIn("Connection refused", record.result.summary)

    def test_get_summary_counts(self):
        tm = TaskManager()
        for i in range(5):
            spec = TaskSpec(task_id=f"t{i}", description="X", acceptance_criteria="Y", context="Z")
            tm.add_task(spec)
            tm.mark_running(f"t{i}", worker_id="w")
        tr_ok = TaskResult(status=TaskStatus.SUCCESS, summary="OK", result="OK")
        for i in range(3):
            tm.mark_completed(f"t{i}", tr_ok)
        for i in range(3, 5):
            tm.mark_failed(f"t{i}", "Error")
        summary = tm.get_summary()
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["completed"], 3)
        self.assertEqual(summary["failed"], 2)
        self.assertEqual(summary["pending"], 0)
        self.assertEqual(summary["running"], 0)

    def test_all_completed(self):
        tm = TaskManager()
        spec = TaskSpec(task_id="t1", description="X", acceptance_criteria="Y", context="Z")
        tm.add_task(spec)
        self.assertFalse(tm.all_completed())
        tm.mark_running("t1", worker_id="w")
        self.assertFalse(tm.all_completed())
        tr = TaskResult(status=TaskStatus.SUCCESS, summary="OK", result="OK")
        tm.mark_completed("t1", tr)
        self.assertTrue(tm.all_completed())

    def test_reset_clears_all(self):
        tm = TaskManager()
        spec = TaskSpec(task_id="t1", description="X", acceptance_criteria="Y", context="Z")
        tm.add_task(spec)
        tm.mark_running("t1", worker_id="w")
        tr = TaskResult(status=TaskStatus.SUCCESS, summary="OK", result="OK")
        tm.mark_completed("t1", tr)
        self.assertEqual(tm.get_summary()["total"], 1)
        tm.reset()
        self.assertEqual(tm.get_summary()["total"], 0)

    def test_get_task_nonexistent(self):
        tm = TaskManager()
        self.assertIsNone(tm.get_task("nonexistent"))

    def test_get_result_nonexistent(self):
        tm = TaskManager()
        self.assertIsNone(tm.get_result("nonexistent"))


# ============================================================================
# 8. ToolRegistry Edge Cases
# ============================================================================

class TestToolRegistryEdgeCases(unittest.TestCase):
    """Edge case tests for ToolRegistry."""

    def test_execute_unknown_tool(self):
        registry = ToolRegistry()
        result = registry.execute("nonexistent", {})
        self.assertIn("unknown tool", result.lower())

    def test_execute_with_alias(self):
        def my_tool(target_url: str) -> str:
            return f"Fetched {target_url}"

        registry = ToolRegistry()
        registry.register(ToolDef(
            name="fetch",
            description="Fetch a URL",
            parameters={"target_url": "The URL to fetch"},
            func=my_tool,
        ))
        result = registry.execute("fetch", {"url": "http://example.com"})
        self.assertIn("Fetched http://example.com", result)

    def test_execute_filters_unknown_params(self):
        def my_tool(path: str) -> str:
            return f"Path: {path}"

        registry = ToolRegistry()
        registry.register(ToolDef(
            name="read",
            description="Read a file",
            parameters={"path": "File path"},
            func=my_tool,
        ))
        result = registry.execute("read", {"path": "/tmp/x", "extra": "ignored"})
        self.assertEqual(result, "Path: /tmp/x")

    def test_execute_tool_crash_caught(self):
        def crash_tool() -> str:
            raise RuntimeError("Tool exploded!")

        registry = ToolRegistry()
        registry.register(ToolDef(
            name="crash",
            description="This tool crashes",
            parameters={},
            func=crash_tool,
        ))
        result = registry.execute("crash", {})
        self.assertIn("crashed", result.lower())
        self.assertIn("RuntimeError", result)

    def test_execute_inspects_signature(self):
        class NoSigCallable:
            pass

        registry = ToolRegistry()
        registry.register(ToolDef(
            name="bad",
            description="Bad",
            parameters={},
            func=NoSigCallable(),
        ))
        result = registry.execute("bad", {})
        self.assertIsInstance(result, str)

    def test_create_default_registry_has_tools(self):
        """create_default_registry returns a non-empty registry."""
        registry = create_default_registry()
        schemas = registry.get_openai_schemas()
        self.assertGreater(len(schemas), 0)
        tool_names = {s["function"]["name"] for s in schemas}
        # Core tools should be present
        core_tools = {"read_file", "write_file", "terminal", "web_search"}
        self.assertTrue(core_tools.issubset(tool_names))


# ============================================================================
# 9. HermesClient prompt building tests
# ============================================================================

class TestHermesClientPromptBuilding(unittest.TestCase):
    """Tests for HermesClient._build_prompt."""

    def test_build_prompt_includes_all_fields(self):
        config = HermesWorkerConfig(
            id="test-worker",
            role="Test Worker",
            profile="test-profile",
            model="deepseek-chat",
            system_prompt="You are a test worker.",
            capabilities=["test"],
        )
        client = HermesClient(config)
        spec = TaskSpec(
            task_id="task-1",
            description="Do the thing",
            acceptance_criteria="It must work perfectly.",
            context="This is the context.",
        )
        prompt = client._build_prompt(spec)
        self.assertIn("You are a test worker.", prompt)
        self.assertIn("Do the thing", prompt)
        self.assertIn("It must work perfectly.", prompt)
        self.assertIn("This is the context.", prompt)
        self.assertIn("status", prompt)
        self.assertIn('"success"', prompt)

    def test_hermes_worker_config_defaults(self):
        config = HermesWorkerConfig.from_dict({"id": "minimal"})
        self.assertEqual(config.id, "minimal")
        self.assertEqual(config.role, "minimal")
        self.assertEqual(config.profile, "janus-worker-minimal")
        self.assertEqual(config.model, "deepseek-chat")
        self.assertEqual(config.timeout_seconds, 300)
        self.assertEqual(config.capabilities, [])
        self.assertEqual(config.system_prompt, "")

    def test_hermes_worker_config_with_defaults_dict(self):
        config = HermesWorkerConfig.from_dict(
            {"id": "worker-1"},
            defaults={"timeout_seconds": 600, "model": "gpt-4o"},
        )
        self.assertEqual(config.timeout_seconds, 600)
        self.assertEqual(config.model, "gpt-4o")

    def test_hermes_worker_config_overrides_defaults(self):
        config = HermesWorkerConfig.from_dict(
            {"id": "worker-1", "timeout_seconds": 120},
            defaults={"timeout_seconds": 600},
        )
        self.assertEqual(config.timeout_seconds, 120)


# ============================================================================
# 10. HermesClient._parse_output tests
# ============================================================================

class TestHermesClientParseOutput(unittest.TestCase):
    """Tests for HermesClient._parse_output."""

    def test_parse_valid_output(self):
        stdout = 'Some preamble text\n{"status": "success", "summary": "OK", "result": "All done", "confidence": "high"}'
        result = HermesClient._parse_output(stdout, "test-worker")
        self.assertEqual(result.status, TaskStatus.SUCCESS)
        self.assertEqual(result.worker_id, "test-worker")
        self.assertEqual(result.summary, "OK")

    def test_parse_nested_json(self):
        stdout = '{"status": "success", "summary": "OK", "result": "{\\"nested\\": \\"value\\"}", "confidence": "high"}'
        result = HermesClient._parse_output(stdout, "test-worker")
        self.assertEqual(result.status, TaskStatus.SUCCESS)

    def test_parse_failure_output(self):
        stdout = '{"status": "failure", "summary": "Bad", "result": "Error 500", "confidence": "low"}'
        result = HermesClient._parse_output(stdout, "test-worker")
        self.assertEqual(result.status, TaskStatus.FAILURE)

    def test_parse_empty_output(self):
        result = HermesClient._parse_output("", "test-worker")
        self.assertEqual(result.status, TaskStatus.FAILURE)
        self.assertIn("no output", result.summary.lower())

    def test_parse_garbage_output(self):
        stdout = "This is not JSON at all."
        result = HermesClient._parse_output(stdout, "test-worker")
        self.assertEqual(result.status, TaskStatus.FAILURE)
        self.assertIn("Could not parse", result.summary)
        self.assertIn("This is not JSON at all.", result.result)

    def test_parse_json_with_leading_trailing_text(self):
        stdout = (
            "I'll now complete this task.\n"
            'Let me write the result:\n'
            '{"status": "success", "summary": "OK", "result": "Done", "confidence": "high"}\n'
            "Task complete!"
        )
        result = HermesClient._parse_output(stdout, "test-worker")
        self.assertEqual(result.status, TaskStatus.SUCCESS)

    def test_parse_multiple_json_objects_last_wins(self):
        stdout = (
            '{"config": "some value"}\n'
            '{"status": "failure", "summary": "First attempt", "result": "X", "confidence": "low"}\n'
            '{"status": "success", "summary": "Final", "result": "Y", "confidence": "high"}'
        )
        result = HermesClient._parse_output(stdout, "test-worker")
        self.assertEqual(result.status, TaskStatus.SUCCESS)
        self.assertEqual(result.summary, "Final")


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    unittest.main()
