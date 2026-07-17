"""
Unit tests for janus.core.task_manager — TaskManager lifecycle, transitions, queries.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from core.task_manager import TaskManager, TaskState, TaskRecord
from core.protocol import TaskSpec, TaskResult, TaskStatus


def _make_spec(task_id="t1"):
    return TaskSpec(
        task_id=task_id,
        description="Test task",
        acceptance_criteria="Must pass",
        context="Unit test",
    )


def _make_result(status=TaskStatus.SUCCESS, summary="OK", result="Done"):
    return TaskResult(status=status, summary=summary, result=result)


class TestTaskLifecycle(unittest.TestCase):
    """Happy-path lifecycle: PENDING → RUNNING → COMPLETED and PENDING → RUNNING → FAILED."""

    def test_full_lifecycle_to_completed(self):
        tm = TaskManager()
        tid = tm.add_task(_make_spec("t1"))
        self.assertEqual(tm.get_summary()["pending"], 1)

        tm.mark_running(tid, worker_id="w1")
        self.assertEqual(tm.get_summary()["running"], 1)

        res = _make_result()
        tm.mark_completed(tid, res)
        self.assertEqual(tm.get_summary()["completed"], 1)

    def test_full_lifecycle_to_failed(self):
        tm = TaskManager()
        tid = tm.add_task(_make_spec("t1"))

        tm.mark_running(tid, worker_id="w1")
        tm.mark_failed(tid, error="Connection timeout")

        self.assertEqual(tm.get_summary()["failed"], 1)
        record = tm.get_task(tid)
        self.assertIsNotNone(record)
        self.assertEqual(record.state, TaskState.FAILED)
        self.assertIsNotNone(record.result)
        self.assertEqual(record.result.status, TaskStatus.FAILURE)

    def test_add_task_returns_task_id(self):
        tm = TaskManager()
        spec = _make_spec("my-id")
        tid = tm.add_task(spec)
        self.assertEqual(tid, "my-id")


class TestStateTransitions(unittest.TestCase):
    """Valid and invalid state transitions."""

    def test_pending_to_running_valid(self):
        tm = TaskManager()
        tid = tm.add_task(_make_spec())
        tm.mark_running(tid, worker_id="w1")
        self.assertEqual(tm.get_task(tid).state, TaskState.RUNNING)

    def test_running_to_completed_valid(self):
        tm = TaskManager()
        tid = tm.add_task(_make_spec())
        tm.mark_running(tid, worker_id="w1")
        tm.mark_completed(tid, _make_result())
        self.assertEqual(tm.get_task(tid).state, TaskState.COMPLETED)

    def test_running_to_failed_valid(self):
        tm = TaskManager()
        tid = tm.add_task(_make_spec())
        tm.mark_running(tid, worker_id="w1")
        tm.mark_failed(tid, "error")
        self.assertEqual(tm.get_task(tid).state, TaskState.FAILED)

    def test_pending_to_completed_invalid(self):
        tm = TaskManager()
        tid = tm.add_task(_make_spec())
        with self.assertRaises(ValueError) as ctx:
            tm.mark_completed(tid, _make_result())
        self.assertIn("Invalid transition", str(ctx.exception))

    def test_completed_to_running_invalid(self):
        tm = TaskManager()
        tid = tm.add_task(_make_spec())
        tm.mark_running(tid, worker_id="w1")
        tm.mark_completed(tid, _make_result())
        with self.assertRaises(ValueError):
            tm.mark_running(tid, worker_id="w2")

    def test_failed_to_running_invalid(self):
        tm = TaskManager()
        tid = tm.add_task(_make_spec())
        tm.mark_running(tid, worker_id="w1")
        tm.mark_failed(tid, "error")
        with self.assertRaises(ValueError):
            tm.mark_running(tid, worker_id="w2")

    def test_unknown_task_id_raises(self):
        tm = TaskManager()
        with self.assertRaises(ValueError) as ctx:
            tm.mark_running("nonexistent", worker_id="w1")
        self.assertIn("Unknown task_id", str(ctx.exception))


class TestQueries(unittest.TestCase):
    """get_summary, all_completed, get_result, get_task."""

    def test_get_summary_counts_at_each_stage(self):
        tm = TaskManager()
        tm.add_task(_make_spec("t1"))
        tm.add_task(_make_spec("t2"))
        tm.add_task(_make_spec("t3"))

        s = tm.get_summary()
        self.assertEqual(s, {"total": 3, "pending": 3, "running": 0, "completed": 0, "failed": 0})

        tm.mark_running("t1", "w1")
        s = tm.get_summary()
        self.assertEqual(s["running"], 1)
        self.assertEqual(s["pending"], 2)

        tm.mark_completed("t1", _make_result())
        s = tm.get_summary()
        self.assertEqual(s["completed"], 1)

    def test_all_completed_false_while_tasks_running(self):
        tm = TaskManager()
        tm.add_task(_make_spec("t1"))
        self.assertFalse(tm.all_completed())

        tm.mark_running("t1", "w1")
        self.assertFalse(tm.all_completed())

    def test_all_completed_true_when_all_terminal(self):
        tm = TaskManager()
        tm.add_task(_make_spec("t1"))
        tm.mark_running("t1", "w1")
        tm.mark_completed("t1", _make_result())
        self.assertTrue(tm.all_completed())

    def test_get_result_returns_none_for_pending(self):
        tm = TaskManager()
        tid = tm.add_task(_make_spec())
        self.assertIsNone(tm.get_result(tid))

    def test_get_result_returns_taskresult_for_completed(self):
        tm = TaskManager()
        tid = tm.add_task(_make_spec())
        tm.mark_running(tid, "w1")
        res = _make_result()
        tm.mark_completed(tid, res)
        self.assertEqual(tm.get_result(tid), res)

    def test_get_task_returns_none_for_unknown(self):
        tm = TaskManager()
        self.assertIsNone(tm.get_task("nope"))

    def test_get_task_returns_record_for_known(self):
        tm = TaskManager()
        tid = tm.add_task(_make_spec())
        record = tm.get_task(tid)
        self.assertIsInstance(record, TaskRecord)
        self.assertEqual(record.task_id, tid)


class TestMultipleTasks(unittest.TestCase):
    """Multiple concurrent tasks lifecycle."""

    def test_three_tasks_mixed_outcomes(self):
        tm = TaskManager()
        tm.add_task(_make_spec("t1"))
        tm.add_task(_make_spec("t2"))
        tm.add_task(_make_spec("t3"))

        # Start all three
        tm.mark_running("t1", "w1")
        tm.mark_running("t2", "w2")
        tm.mark_running("t3", "w3")

        # Complete t1 and t2, fail t3
        tm.mark_completed("t1", _make_result())
        tm.mark_completed("t2", _make_result())
        tm.mark_failed("t3", "Out of memory")

        s = tm.get_summary()
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["completed"], 2)
        self.assertEqual(s["failed"], 1)
        self.assertEqual(s["pending"], 0)
        self.assertEqual(s["running"], 0)

    def test_all_completed_false_until_all_terminal(self):
        tm = TaskManager()
        tm.add_task(_make_spec("t1"))
        tm.add_task(_make_spec("t2"))

        tm.mark_running("t1", "w1")
        tm.mark_running("t2", "w2")
        self.assertFalse(tm.all_completed())

        tm.mark_completed("t1", _make_result())
        self.assertFalse(tm.all_completed())  # t2 still running

        tm.mark_failed("t2", "error")
        self.assertTrue(tm.all_completed())  # both terminal now


class TestReset(unittest.TestCase):
    """TaskManager.reset() clears everything."""

    def test_after_reset_summary_all_zeros(self):
        tm = TaskManager()
        tm.add_task(_make_spec("t1"))
        tm.mark_running("t1", "w1")
        tm.mark_completed("t1", _make_result())

        tm.reset()
        s = tm.get_summary()
        for v in s.values():
            self.assertEqual(v, 0)

    def test_after_reset_all_completed_returns_true(self):
        tm = TaskManager()
        tm.add_task(_make_spec("t1"))
        tm.mark_running("t1", "w1")
        tm.mark_completed("t1", _make_result())

        tm.reset()
        # Empty iterable → all() returns True
        self.assertTrue(tm.all_completed())

    def test_can_add_tasks_after_reset(self):
        tm = TaskManager()
        tm.add_task(_make_spec("t1"))
        tm.mark_running("t1", "w1")
        tm.mark_completed("t1", _make_result())
        tm.reset()

        tid = tm.add_task(_make_spec("t2"))
        self.assertEqual(tm.get_summary()["total"], 1)
        self.assertEqual(tm.get_summary()["pending"], 1)
        tm.mark_running(tid, "w2")
        tm.mark_completed(tid, _make_result())
        self.assertEqual(tm.get_summary()["completed"], 1)


class TestEdgeCases(unittest.TestCase):
    """Edge cases for task_manager.py."""

    def test_mark_failed_empty_error(self):
        tm = TaskManager()
        tid = tm.add_task(_make_spec())
        tm.mark_running(tid, "w1")
        tm.mark_failed(tid, error="")
        record = tm.get_task(tid)
        self.assertEqual(record.state, TaskState.FAILED)
        self.assertEqual(record.result.status, TaskStatus.FAILURE)
        self.assertEqual(record.result.summary, "Task failed: ")
        self.assertEqual(record.result.result, "")

    def test_mark_failed_very_long_error(self):
        tm = TaskManager()
        tid = tm.add_task(_make_spec())
        tm.mark_running(tid, "w1")
        long_error = "X" * 2000
        tm.mark_failed(tid, error=long_error)
        record = tm.get_task(tid)

        # Summary: "Task failed: " (13 chars) + error[:80]
        self.assertEqual(len(record.result.summary), 13 + 80)
        self.assertTrue(record.result.summary.startswith("Task failed: "))
        # Full error is preserved in result
        self.assertEqual(record.result.result, long_error)

    def test_rapid_state_transitions_no_crash(self):
        """Rapid PENDING→RUNNING→COMPLETED transitions should work without error."""
        tm = TaskManager()
        tids = [tm.add_task(_make_spec(f"t{i}")) for i in range(50)]
        for tid in tids:
            tm.mark_running(tid, f"w-{tid}")
            tm.mark_completed(tid, _make_result())
        self.assertTrue(tm.all_completed())
        self.assertEqual(tm.get_summary()["completed"], 50)

    def test_get_result_returns_none_for_unknown_task(self):
        tm = TaskManager()
        self.assertIsNone(tm.get_result("no-such-task"))

    def test_completed_task_has_spec_preserved(self):
        tm = TaskManager()
        spec = _make_spec("t1")
        tm.add_task(spec)
        tm.mark_running("t1", "w1")
        tm.mark_completed("t1", _make_result())

        record = tm.get_task("t1")
        self.assertEqual(record.spec, spec)
        self.assertEqual(record.spec.description, "Test task")


if __name__ == "__main__":
    unittest.main()
