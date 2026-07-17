"""
Janus Task Manager — task lifecycle tracking for the Gatekeeper Tree architecture.

Phase 1: single-level delegation only. No recursion, no dependency tracking yet.
Thread-safe NOT required.

Tracks every task from PENDING → RUNNING → COMPLETED/FAILED.
The Gatekeeper uses this as its "memory" of what's happening.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .protocol import TaskResult, TaskSpec, TaskStatus


class TaskState(Enum):
    """Lifecycle state of a task tracked by the TaskManager."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskRecord:
    """A single task's full tracking record.

    Attributes:
        task_id: Unique identifier (same as TaskSpec.task_id).
        spec: The original TaskSpec sent to the worker.
        state: Current lifecycle state.
        result: TaskResult populated on COMPLETED/FAILED.
        worker_id: Which worker is handling this task (set on RUNNING).
        created_at: When this record was created.
    """

    task_id: str
    spec: TaskSpec
    state: TaskState = TaskState.PENDING
    result: Optional[TaskResult] = None
    worker_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── valid state transitions ────────────────────────────────────────────────
_VALID_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.PENDING: {TaskState.RUNNING},
    TaskState.RUNNING: {TaskState.COMPLETED, TaskState.FAILED},
    TaskState.COMPLETED: set(),   # terminal
    TaskState.FAILED: set(),      # terminal
}


class TaskManager:
    """Tracks task lifecycle for the Gatekeeper.

    Single-threaded by design for Phase 1.  No locks, no concurrency
    guarantees — keep access serial within the Gatekeeper loop.

    Usage::

        tm = TaskManager()
        tid = tm.add_task(spec)
        tm.mark_running(tid, worker_id="worker-1")
        tm.mark_completed(tid, result)
        print(tm.get_summary())
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}

    # ── registration ────────────────────────────────────────────────────

    def add_task(self, spec: TaskSpec) -> str:
        """Register a new task and return its task_id.

        Args:
            spec: The TaskSpec to track.

        Returns:
            The task_id (same as spec.task_id).

        Warns:
            If a task with the same task_id already exists (silent overwrite
            can cause state tracking issues — this warns operators).
        """
        task_id = spec.task_id
        if task_id in self._tasks:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.warning(
                "TaskManager.add_task: overwriting existing task %r — "
                "this may cause state tracking inconsistencies.",
                task_id,
            )
        record = TaskRecord(task_id=task_id, spec=spec)
        self._tasks[task_id] = record
        return task_id

    # ── state transitions ───────────────────────────────────────────────

    def _transition(self, task_id: str, target_state: TaskState) -> TaskRecord:
        """Apply a state transition, raising ValueError if invalid.

        Args:
            task_id: The task to transition.
            target_state: Desired new state.

        Returns:
            The updated TaskRecord.

        Raises:
            ValueError: If the task doesn't exist or the transition is illegal.
        """
        record = self._tasks.get(task_id)
        if record is None:
            raise ValueError(f"Unknown task_id: {task_id!r}")

        allowed = _VALID_TRANSITIONS.get(record.state, set())
        if target_state not in allowed:
            raise ValueError(
                f"Invalid transition: {record.state.value} → {target_state.value} "
                f"for task {task_id!r}"
            )

        record.state = target_state
        return record

    def mark_running(self, task_id: str, worker_id: str) -> None:
        """Transition PENDING → RUNNING and assign a worker.

        Args:
            task_id: The task to mark.
            worker_id: Identifier of the worker handling this task.

        Raises:
            ValueError: If the task doesn't exist or is not in PENDING state.
        """
        record = self._transition(task_id, TaskState.RUNNING)
        record.worker_id = worker_id

    def mark_completed(self, task_id: str, result: TaskResult) -> None:
        """Transition RUNNING → COMPLETED and store the result.

        Args:
            task_id: The task to mark.
            result: The TaskResult from the worker.

        Raises:
            ValueError: If the task doesn't exist or is not in RUNNING state.
        """
        record = self._transition(task_id, TaskState.COMPLETED)
        record.result = result

    def mark_failed(self, task_id: str, error: str) -> None:
        """Transition RUNNING → FAILED and store the error as a TaskResult.

        Args:
            task_id: The task to mark.
            error: Error description.

        Raises:
            ValueError: If the task doesn't exist or is not in RUNNING state.
        """
        record = self._transition(task_id, TaskState.FAILED)
        record.result = TaskResult(
            status=TaskStatus.FAILURE,
            summary=f"Task failed: {error[:80]}",
            result=error,
        )

    # ── queries ─────────────────────────────────────────────────────────

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        """Return the full TaskRecord for *task_id*, or None."""
        return self._tasks.get(task_id)

    def get_result(self, task_id: str) -> Optional[TaskResult]:
        """Return the completed/failed TaskResult, or None if not yet terminal."""
        record = self._tasks.get(task_id)
        if record is None:
            return None
        return record.result

    def all_completed(self) -> bool:
        """True when no PENDING or RUNNING tasks remain."""
        return all(
            r.state in (TaskState.COMPLETED, TaskState.FAILED)
            for r in self._tasks.values()
        )

    def get_summary(self) -> dict[str, int]:
        """Return a count of tasks by state.

        Returns:
            dict with keys: total, pending, running, completed, failed.
        """
        counts: dict[str, int] = {
            "total": 0,
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
        }
        for r in self._tasks.values():
            counts["total"] += 1
            counts[r.state.value] += 1
        return counts

    # ── lifecycle ───────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear all tracked tasks (useful for testing)."""
        self._tasks.clear()
