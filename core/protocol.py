"""
Janus Protocol — dataclasses and enums for the Gatekeeper Tree architecture.

Gatekeeper: decomposes tasks, dispatches to Workers, reviews results. ZERO execution.
Worker: receives a TaskSpec, executes with tools, returns a TaskResult or NEEDS_DECOMPOSITION.
Leaf: same as Worker but decomposition is disallowed at depth limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TaskStatus(Enum):
    """Outcome of a worker's task execution."""

    SUCCESS = "success"
    FAILURE = "failure"
    NEEDS_DECOMPOSITION = "needs_decomposition"


class Confidence(Enum):
    """Worker's confidence in their result."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class SubTask:
    """A single proposed sub-task within a decomposition.

    Attributes:
        id: Unique identifier within the decomposition (e.g. "sub-1", "parse-config").
        description: What the sub-task should accomplish.
        rationale: Why this is split out — justification for Gatekeeper review.
    """

    id: str
    description: str
    rationale: str


@dataclass
class DecompositionRequest:
    """Proposal from a Worker asking the Gatekeeper to break a task down further.

    Only respected when the Gatekeeper approves. The Worker does NOT self-decompose.
    """

    reason: str
    sub_tasks: list[SubTask] = field(default_factory=list)


@dataclass
class ExecutionPattern:
    """Worker 执行模式记录 — 为 Planner 任务分解提供参考。

    每次 Worker 完成一个任务后，将其执行的工具序列、成功/失败、
    经验教训记录下来。Pattern 库供 Planner 在分解后续任务时参考，
    以提升分解精准度和工具有效性。

    Attributes:
        task_type: 任务类型关键词，如 ``"csv_sort"``、``"file_table"``。
        description: 任务描述原文。
        tool_sequence: 使用的工具序列，每项包含 name / args_summary / success。
        success: 执行是否成功。
        lessons: 经验教训（成功则记录有效模式，失败则记录根因）。
        timestamp: ISO 8601 时间戳。
        task_id: 关联的任务 ID。
    """

    task_type: str
    description: str
    tool_sequence: list = field(default_factory=list)
    success: bool = False
    lessons: str = ""
    timestamp: str = ""
    task_id: str = ""


@dataclass
class TaskResult:
    """The result of a Worker's attempt at a task.

    Attributes:
        status: Outcome — SUCCESS, FAILURE, or NEEDS_DECOMPOSITION.
        summary: One-sentence result. Gatekeeper scans this first when reviewing many results.
        result: Full detail. On success: the actual output. On failure: what went wrong.
        decomposition_request: Set only when status == NEEDS_DECOMPOSITION.
        artifacts: Paths to files created or side effects produced.
        confidence: Worker's self-assessed confidence level.
        worker_id: Which worker produced this result (set by Gatekeeper).
    """

    status: TaskStatus
    summary: str
    result: str
    decomposition_request: Optional[DecompositionRequest] = None
    artifacts: list[str] = field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM
    worker_id: Optional[str] = None
    sub_review_failed: bool = False  # Set when sub-worker review is exhausted
    retry_exhausted: bool = False    # L3-9: all review retries consumed
    retry_history: list[dict] = field(default_factory=list)
    # Structured retry log: {attempt, verdict, issue_count, highest_severity}

    def validate(self) -> bool:
        """Check that NEEDS_DECOMPOSITION has a valid decomposition_request.

        Returns:
            False if status is NEEDS_DECOMPOSITION but decomposition_request is
            None or has an empty sub_tasks list. True otherwise.
        """
        if self.status == TaskStatus.NEEDS_DECOMPOSITION:
            if self.decomposition_request is None:
                return False
            if not self.decomposition_request.sub_tasks:
                return False
        return True

    def to_dict(self) -> dict:
        """Serialize to a plain dict for transport/storage."""
        data: dict = {
            "status": self.status.value,
            "summary": self.summary,
            "result": self.result,
            "artifacts": self.artifacts,
            "confidence": self.confidence.value,
        }
        if self.decomposition_request is not None:
            data["decomposition_request"] = {
                "reason": self.decomposition_request.reason,
                "sub_tasks": [
                    {"id": st.id, "description": st.description, "rationale": st.rationale}
                    for st in self.decomposition_request.sub_tasks
                ],
            }
        if self.worker_id is not None:
            data["worker_id"] = self.worker_id
        if self.sub_review_failed:
            data["sub_review_failed"] = self.sub_review_failed
        if self.retry_exhausted:
            data["retry_exhausted"] = self.retry_exhausted
        if self.retry_history:
            data["retry_history"] = self.retry_history
        return data

    @classmethod
    def from_dict(cls, data: dict) -> TaskResult:
        """Deserialize from a plain dict."""
        import json as _json

        decomp = None
        if "decomposition_request" in data and data["decomposition_request"] is not None:
            dr = data["decomposition_request"]
            decomp = DecompositionRequest(
                reason=dr["reason"],
                sub_tasks=[
                    SubTask(id=st["id"], description=st["description"], rationale=st["rationale"])
                    for st in dr["sub_tasks"]
                ],
            )

        result = data["result"]
        # Normalize: LLMs sometimes nest a dict/list inside the "result" field.
        if not isinstance(result, str):
            result = _json.dumps(result, ensure_ascii=False)

        return cls(
            status=TaskStatus(data["status"]),
            summary=data["summary"],
            result=result,
            decomposition_request=decomp,
            artifacts=data.get("artifacts", []),
            confidence=Confidence(data.get("confidence", "medium")),
            worker_id=data.get("worker_id"),
            sub_review_failed=data.get("sub_review_failed", False),
            retry_exhausted=data.get("retry_exhausted", False),
            retry_history=data.get("retry_history", []),
        )


@dataclass
class TaskSpec:
    """The work package the Gatekeeper sends to a Worker.

    Contains only what that Worker needs — not the full conversation history.

    Attributes:
        task_id: Unique identifier for this task.
        description: What to do.
        acceptance_criteria: How to know it's done right.  Each criterion
            SHOULD be prefixed with [HARD] (must-have, zero tolerance) or
            [SOFT] (nice-to-have, minor deviations acceptable).  Unmarked
            criteria default to [HARD] for backward compatibility.
        context: Relevant background — scoped, not full history.
        intent: Why this task matters in the bigger picture.  Informs the Worker
            of the task's role so it can make better decisions in unexpected
            situations (inspired by military "commander's intent").
        goal: The user's original goal (from Directive.goal).  Provides the
            Worker with the big-picture context so it understands WHY it's
            doing this task, not just WHAT to do.
        constraints: Hard constraints the Worker must follow (from
            Directive.constraints).  E.g. "don't modify existing files",
            "use Python 3.10+".
        depth: Current depth in the decomposition tree (1 = root task).
    """

    task_id: str
    description: str
    acceptance_criteria: str
    context: str
    intent: str = ""
    goal: str = ""
    user_goal: str = ""
    """用户原始输入，一字不改，全链路贯穿。Worker 和 Reviewer 用这个对照原文。"""
    constraints: str = ""
    depth: int = 1
    max_tool_calls: int = 50  # L3-6: per-task tool-call budget

    def validate(self) -> bool:
        """Check required fields. Returns False if task_id or description empty."""
        return bool(self.task_id and self.description)


# ============================================================================
# Planner ↔ Gatekeeper protocol
# ============================================================================


@dataclass
class Directive:
    """Gatekeeper 下发给 Planner 的战略指令。

    Gatekeeper 定方向和约束，Planner 负责战术执行。
    """

    goal: str
    """用户原始目标，一字不改。Planner 需要原文来理解语境。"""

    user_goal: str = ""
    """用户原始输入，一字不改——全链路贯穿的不可变锚点。
    goal 可能在恢复循环中被追加诊断信息，user_goal 永远不变。"""

    intent: str = ""
    """战略意图（为什么）——Gatekeeper 对目标的理解和方向判断。"""

    constraints: str = ""
    """硬性约束——Planner 分解时必须遵守。"""

    priority: str = "normal"
    """优先级：speed | quality | balanced | normal。"""

    context: str = ""
    """多轮对话历史上下文。由 Gatekeeper 从 Session 传入，供 Planner 在分解任务时
    参考，以理解用户的连续意图（例如 '继续上一个任务'）。"""



@dataclass
class ExecutionReport:
    """Planner 完成任务后返回给 Gatekeeper 的执行报告。

    只包含战略级信息——Gatekeeper 不需要知道每个 Worker 的细节。
    """

    status: str  # "completed" | "partial" | "failed"
    """整体执行状态。"""

    total_tasks: int
    """总任务数。"""

    passed: int
    """通过的任务数。"""

    failed: int
    """失败的任务数。"""

    summary: str
    """一句话总结，给 Gatekeeper 向上汇报用。"""

    details: list[str] = field(default_factory=list)
    """每个任务的结果摘要：\"[status] summary\"。"""

    failed_details: list[str] = field(default_factory=list)
    """失败任务的详细诊断信息，供 Gatekeeper 恢复循环使用。
    每个元素对应一个失败任务，包含 worker_id、summary、和根因信息。"""

    failed_tasks: list[dict[str, str]] = field(default_factory=list)
    """失败任务的结构化数据，供 Gatekeeper 做精确诊断。
    每个 dict 包含 task_id、acceptance_criteria、review_issues 等字段。"""

    goal: str = ""
    """原始用户目标，用于 Gatekeeper 向用户汇报时引用。"""

    constraints: str = ""
    """硬性约束，用于 Gatekeeper 确认约束遵守情况。"""

    has_retry_exhausted: bool = False
    """L3-9: 是否有任何子任务的重试次数已耗尽。"""

    # INSPECTOR-INDEPENDENCE: Reviewer的独立发现——Planner可能未采纳的问题。
    # Reviewer作为独立监察，其发现不应完全由Planner过滤。当Planner通过
    # _adjust_verdict_for_criteria降级了Reviewer的裁决时，原始裁决和问题
    # 记录在此，供Gatekeeper做独立的"监察发现"汇报。
    # 每个dict包含: task_id, original_verdict, downgraded_to, issues, reason
    reviewer_findings: list[dict] = field(default_factory=list)
