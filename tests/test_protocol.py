"""
Unit tests for janus.core.protocol — TaskResult, DecompositionRequest, TaskSpec.
"""
import sys
import os

# Add project root to path so we can import core.protocol
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from core.protocol import (
    TaskStatus,
    Confidence,
    SubTask,
    DecompositionRequest,
    TaskResult,
    TaskSpec,
)


class TestTaskResultValidate(unittest.TestCase):
    """TaskResult.validate() — ensures NEEDS_DECOMPOSITION invariants."""

    def test_success_without_decomposition_validates_true(self):
        r = TaskResult(status=TaskStatus.SUCCESS, summary="Done", result="All good")
        self.assertTrue(r.validate())

    def test_failure_without_decomposition_validates_true(self):
        r = TaskResult(status=TaskStatus.FAILURE, summary="Fail", result="Boom")
        self.assertTrue(r.validate())

    def test_needs_decomposition_with_valid_request_validates_true(self):
        dr = DecompositionRequest(
            reason="too complex",
            sub_tasks=[SubTask(id="s1", description="do X", rationale="split for focus")]
        )
        r = TaskResult(
            status=TaskStatus.NEEDS_DECOMPOSITION,
            summary="Needs split",
            result="...",
            decomposition_request=dr,
        )
        self.assertTrue(r.validate())

    def test_needs_decomposition_with_none_request_validates_false(self):
        r = TaskResult(
            status=TaskStatus.NEEDS_DECOMPOSITION,
            summary="Needs split",
            result="...",
            decomposition_request=None,
        )
        self.assertFalse(r.validate())

    def test_needs_decomposition_with_empty_sub_tasks_validates_false(self):
        dr = DecompositionRequest(reason="too big", sub_tasks=[])
        r = TaskResult(
            status=TaskStatus.NEEDS_DECOMPOSITION,
            summary="Needs split",
            result="...",
            decomposition_request=dr,
        )
        self.assertFalse(r.validate())

    def test_needs_decomposition_without_argument_defaults_none(self):
        # decomposition_request defaults to None
        r = TaskResult(
            status=TaskStatus.NEEDS_DECOMPOSITION,
            summary="Needs split",
            result="...",
        )
        self.assertFalse(r.validate())


class TestTaskResultRoundTrip(unittest.TestCase):
    """TaskResult.to_dict() → from_dict() round-trips."""

    def test_success_round_trip_all_fields(self):
        r = TaskResult(
            status=TaskStatus.SUCCESS,
            summary="Task completed successfully",
            result="Full output details here.",
            artifacts=["/tmp/out.txt"],
            confidence=Confidence.HIGH,
            worker_id="worker-001",
        )
        d = r.to_dict()
        rebuilt = TaskResult.from_dict(d)
        self.assertEqual(rebuilt.status, r.status)
        self.assertEqual(rebuilt.summary, r.summary)
        self.assertEqual(rebuilt.result, r.result)
        self.assertEqual(rebuilt.artifacts, r.artifacts)
        self.assertEqual(rebuilt.confidence, r.confidence)
        self.assertEqual(rebuilt.worker_id, r.worker_id)
        self.assertIsNone(rebuilt.decomposition_request)

    def test_failure_round_trip_with_artifacts(self):
        r = TaskResult(
            status=TaskStatus.FAILURE,
            summary="Build failed",
            result="Compilation error at line 42",
            artifacts=["/tmp/build.log", "/tmp/errors.txt"],
            confidence=Confidence.HIGH,
            worker_id="worker-002",
        )
        d = r.to_dict()
        rebuilt = TaskResult.from_dict(d)
        self.assertEqual(rebuilt.status, TaskStatus.FAILURE)
        self.assertEqual(rebuilt.artifacts, ["/tmp/build.log", "/tmp/errors.txt"])

    def test_needs_decomposition_round_trip(self):
        dr = DecompositionRequest(
            reason="Task too large for single worker",
            sub_tasks=[
                SubTask(id="sub-1", description="Parse input", rationale="Separate parsing"),
                SubTask(id="sub-2", description="Validate schema", rationale="Separate validation"),
                SubTask(id="sub-3", description="Execute", rationale="Core logic only"),
            ],
        )
        r = TaskResult(
            status=TaskStatus.NEEDS_DECOMPOSITION,
            summary="Needs 3 sub-tasks",
            result="Identified 3 separable concerns",
            decomposition_request=dr,
            confidence=Confidence.MEDIUM,
            worker_id="worker-003",
        )
        d = r.to_dict()
        rebuilt = TaskResult.from_dict(d)
        self.assertEqual(rebuilt.status, TaskStatus.NEEDS_DECOMPOSITION)
        self.assertIsNotNone(rebuilt.decomposition_request)
        self.assertEqual(rebuilt.decomposition_request.reason, dr.reason)
        self.assertEqual(len(rebuilt.decomposition_request.sub_tasks), 3)
        self.assertEqual(rebuilt.decomposition_request.sub_tasks[0].id, "sub-1")
        self.assertEqual(rebuilt.decomposition_request.sub_tasks[2].description, "Execute")

    def test_minimal_result_round_trip(self):
        r = TaskResult(
            status=TaskStatus.SUCCESS,
            summary="Minimal",
            result="Done",
        )
        d = r.to_dict()
        rebuilt = TaskResult.from_dict(d)
        self.assertEqual(rebuilt.status, TaskStatus.SUCCESS)
        self.assertEqual(rebuilt.artifacts, [])
        self.assertEqual(rebuilt.confidence, Confidence.MEDIUM)
        self.assertIsNone(rebuilt.worker_id)
        self.assertIsNone(rebuilt.decomposition_request)

    def test_worker_id_none_serialized_without_key(self):
        r = TaskResult(
            status=TaskStatus.SUCCESS,
            summary="Test",
            result="OK",
            worker_id=None,
        )
        d = r.to_dict()
        self.assertNotIn("worker_id", d)
        rebuilt = TaskResult.from_dict(d)
        self.assertIsNone(rebuilt.worker_id)

    def test_decomposition_request_none_serialized_without_key(self):
        r = TaskResult(
            status=TaskStatus.SUCCESS,
            summary="Test",
            result="OK",
            decomposition_request=None,
        )
        d = r.to_dict()
        self.assertNotIn("decomposition_request", d)

    def test_from_dict_with_null_decomposition_request(self):
        """Edge case: JSON null for decomposition_request key."""
        d = {
            "status": "success",
            "summary": "OK",
            "result": "All done",
            "artifacts": [],
            "confidence": "high",
            "decomposition_request": None,
        }
        r = TaskResult.from_dict(d)
        self.assertEqual(r.status, TaskStatus.SUCCESS)
        self.assertIsNone(r.decomposition_request)


class TestDecompositionRequest(unittest.TestCase):
    """DecompositionRequest dataclass."""

    def test_empty_sub_tasks_default(self):
        dr = DecompositionRequest(reason="simple")
        self.assertEqual(dr.sub_tasks, [])

    def test_multiple_sub_tasks(self):
        sts = [
            SubTask(id="a", description="Step A", rationale="First"),
            SubTask(id="b", description="Step B", rationale="Second"),
        ]
        dr = DecompositionRequest(reason="multi-step", sub_tasks=sts)
        self.assertEqual(len(dr.sub_tasks), 2)
        self.assertEqual(dr.sub_tasks[0].id, "a")
        self.assertEqual(dr.sub_tasks[1].rationale, "Second")


class TestTaskSpec(unittest.TestCase):
    """TaskSpec dataclass."""

    def test_default_depth_is_one(self):
        spec = TaskSpec(
            task_id="t1",
            description="Test",
            acceptance_criteria="Passes",
            context="None",
        )
        self.assertEqual(spec.depth, 1)

    def test_all_fields_populated(self):
        spec = TaskSpec(
            task_id="task-42",
            description="Analyze logs for anomalies",
            acceptance_criteria="Must find at least 3 patterns",
            context="Production logs from 2025-Jul",
            depth=3,
        )
        self.assertEqual(spec.task_id, "task-42")
        self.assertEqual(spec.description, "Analyze logs for anomalies")
        self.assertEqual(spec.acceptance_criteria, "Must find at least 3 patterns")
        self.assertEqual(spec.context, "Production logs from 2025-Jul")
        self.assertEqual(spec.depth, 3)


class TestEdgeCases(unittest.TestCase):
    """Edge cases for protocol.py."""

    def test_from_dict_missing_optional_fields(self):
        d = {
            "status": "success",
            "summary": "OK",
            "result": "Done",
        }
        r = TaskResult.from_dict(d)
        self.assertEqual(r.artifacts, [])
        self.assertEqual(r.confidence, Confidence.MEDIUM)
        self.assertIsNone(r.worker_id)
        self.assertIsNone(r.decomposition_request)

    def test_from_dict_unknown_status_string(self):
        d = {
            "status": "bogus_status",
            "summary": "x",
            "result": "y",
        }
        with self.assertRaises(ValueError):
            TaskResult.from_dict(d)

    def test_very_long_summary_and_result(self):
        long_text = "A" * 10000
        r = TaskResult(
            status=TaskStatus.SUCCESS,
            summary=long_text,
            result=long_text,
        )
        d = r.to_dict()
        rebuilt = TaskResult.from_dict(d)
        self.assertEqual(rebuilt.summary, long_text)
        self.assertEqual(rebuilt.result, long_text)

    def test_unicode_in_fields(self):
        r = TaskResult(
            status=TaskStatus.SUCCESS,
            summary="任务完成 \U0001F680",
            result="中文测试 — 通过 ✅",
            artifacts=["/tmp/文件.txt"],
        )
        d = r.to_dict()
        rebuilt = TaskResult.from_dict(d)
        self.assertEqual(rebuilt.summary, "任务完成 \U0001F680")
        self.assertEqual(rebuilt.result, "中文测试 — 通过 ✅")
        self.assertEqual(rebuilt.artifacts, ["/tmp/文件.txt"])

    def test_confidence_unknown_value_defaults_medium(self):
        d = {
            "status": "success",
            "summary": "x",
            "result": "y",
            "confidence": "extreme",
        }
        with self.assertRaises(ValueError):
            TaskResult.from_dict(d)


if __name__ == "__main__":
    unittest.main()
