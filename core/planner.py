"""
Janus Planner — tactical execution manager.

The Planner receives a Directive from the Gatekeeper, breaks it into
executable TaskSpecs, dispatches to Workers with Reviewer audit, and
returns an ExecutionReport.

Role in the Janus architecture:
  Gatekeeper → Directive → Planner → [TaskSpecs] → Workers + Reviewer → ExecutionReport

The Planner has its own LLM (typically lighter than Gatekeeper's) but
ZERO tools — it cannot read files, write files, or run commands.  It
only plans, dispatches, tracks, and summarizes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Callable, Optional, TYPE_CHECKING

from .console import _jin, _qing, _zhu
from .prompts import context_discipline_prompt, extract_json
from .protocol import (
    Confidence,
    Directive,
    ExecutionReport,
    TaskResult,
    TaskSpec,
    TaskStatus,
)
from .reviewer import ReviewResult, ReviewVerdict, Severity

if TYPE_CHECKING:
    from .console import Console
    from .reviewer import Reviewer

# ---------------------------------------------------------------------------
# OpenAI client — lazy import with a helpful error message
# ---------------------------------------------------------------------------
try:
    from openai import OpenAI  # type: ignore[import-untyped]
except ImportError as exc:
    raise ImportError(
        "The `openai` package is required by Janus Planner. "
        "Install it with: pip install openai"
    ) from exc

logger = logging.getLogger(__name__)


# L3-8: heuristic task-difficulty estimator (1-5)
def _estimate_task_difficulty(description: str) -> int:
    """Estimate a sub-task's difficulty from its description text.

    Heuristic weights:
    - File operations (create/modify/write/refactor): +2
    - Complex actions (analyze/audit/review/debug): +2
    - Moderate actions (search/find/read/check): +1
    - Minimal actions (report/summarize/list): +0

    Returns a value clamped to [1, 5].
    """
    desc_lower = description.lower()

    _hard_keywords = [
        "create", "modify", "write", "refactor", "重构", "implement",
        "build", "generate", "transform", "migrate", "deploy",
    ]
    _complex_keywords = [
        "analyze", "audit", "review", "debug", "分析", "审查", "调优",
        "optimize", "secure", "validate", "verify",
    ]
    _moderate_keywords = [
        "search", "find", "read", "check", "查找", "读取", "检查",
        "test", "compare", "extract",
    ]

    difficulty = 1  # base
    for kw in _hard_keywords:
        if kw in desc_lower:
            difficulty += 2
            break  # only count strongest match
    else:
        for kw in _complex_keywords:
            if kw in desc_lower:
                difficulty += 2
                break
        else:
            for kw in _moderate_keywords:
                if kw in desc_lower:
                    difficulty += 1
                    break

    # Extra weight for multiple files / directories
    file_count = desc_lower.count("file") + desc_lower.count("文件") + desc_lower.count("path")
    if file_count >= 3:
        difficulty += 1

    return max(1, min(5, difficulty))


# L3-8: dependency-aware difficulty sort
def _difficulty_sort_preserve_deps(specs: list[TaskSpec]) -> list[TaskSpec]:
    """Sort sub-tasks by difficulty (hardest first), but preserve dependencies.

    If a task description mentions another task's ID (e.g. ``"needs output
    from task-2"``, ``"depends on task-1"``, ``"需要 task-3 的输出"``),
    the dependent task MUST stay after its dependency — regardless of
    difficulty scores.

    Independent tasks (no detected dependencies) are sorted by estimated
    difficulty descending so the hardest tasks get the most time/attention.
    """
    import re as _re

    if len(specs) <= 1:
        return list(specs)

    # ── 1. Detect dependency patterns ───────────────────────────────────
    # Patterns that indicate "this task needs output from another task"
    _DEP_PATTERNS: list[_re.Pattern] = [
        _re.compile(r'(?:depends? on|needs? (?:output|result) from|'
                    r'after|requires? (?:output|result) from|'
                    r'需要\s*|依赖于|依赖|基于|先于)\s*'
                    r'(?:task[-_ ]?)?(\d+)',
                    _re.IGNORECASE),
        _re.compile(r'(?:task[-_ ]?)(\d+)\s*(?:先|优先|必须.*完成)'),
        _re.compile(r'(?:after|following)\s+(?:task[-_ ]?)(\d+)',
                    _re.IGNORECASE),
    ]

    # Build task_id → index mapping (match "task-N" or just "N")
    _id_to_idx: dict[str, int] = {}
    for idx, spec in enumerate(specs):
        # Try to extract numeric ID
        m = _re.search(r'(\d+)', spec.task_id)
        if m:
            _id_to_idx[m.group(1)] = idx
        _id_to_idx[spec.task_id] = idx

    # ── 2. Determine which tasks depend on which ────────────────────────
    # dep_graph[i] = {indices of tasks that task i depends on}
    dep_graph: list[set[int]] = [set() for _ in specs]

    for i, spec in enumerate(specs):
        desc = spec.description
        for pat in _DEP_PATTERNS:
            for m in pat.finditer(desc):
                dep_id = m.group(1)
                if dep_id in _id_to_idx and _id_to_idx[dep_id] != i:
                    dep_graph[i].add(_id_to_idx[dep_id])
        # Also check context for dependency hints
        ctx = getattr(spec, 'context', '') or ''
        if ctx:
            for pat in _DEP_PATTERNS:
                for m in pat.finditer(str(ctx)):
                    dep_id = m.group(1)
                    if dep_id in _id_to_idx and _id_to_idx[dep_id] != i:
                        dep_graph[i].add(_id_to_idx[dep_id])

    # ── 3. Compute difficulty scores ────────────────────────────────────
    scores = [_estimate_task_difficulty(s.description) for s in specs]

    # ── 4. Topological sort with difficulty tie-breaking ─────────────────
    # Kahn's algorithm with a max-heap on difficulty
    import heapq as _heapq

    in_degree = [len(deps) for deps in dep_graph]
    # Reverse graph: which tasks depend on task i?
    rev_graph: list[list[int]] = [[] for _ in specs]
    for i, deps in enumerate(dep_graph):
        for d in deps:
            rev_graph[d].append(i)

    # Priority queue: (-score, index) → highest score first
    # Only push independent nodes
    heap: list[tuple[float, int]] = [
        (-scores[i], i) for i in range(len(specs)) if in_degree[i] == 0
    ]
    _heapq.heapify(heap)

    result: list[TaskSpec] = []
    while heap:
        _, node = _heapq.heappop(heap)
        result.append(specs[node])
        for dependent in rev_graph[node]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                _heapq.heappush(heap, (-scores[dependent], dependent))

    # ── 5. Fallback: if topological sort failed (cycle or missed nodes),
    #        fall through to original order → difficulty sort
    if len(result) != len(specs):
        import logging as _log
        _log.getLogger(__name__).warning(
            "Dependency-aware sort produced %d results for %d specs "
            "(possible cycle). Falling back to difficulty-only sort.",
            len(result), len(specs),
        )
        result = list(specs)
        result.sort(key=lambda s: _estimate_task_difficulty(s.description),
                    reverse=True)

    return result


# ============================================================================
# Planner
# ============================================================================


# L3 shared calibration: criteria-aware verdict adjustment.
# Extracted as a module-level function so both Planner._dispatch_with_review
# and Worker._review_sub_result can apply the same HARD/SOFT criteria logic
# without cross-class imports.
def calibrate_and_adjust(
    review: Any,
    acceptance_criteria: str,
) -> tuple[Any, bool, Any]:
    """Adjust review verdict when it over-reacts to SOFT-only criteria.

    Returns (adjusted_review, was_downgraded, original_verdict).
    Mutates review.verdict in-place when a downgrade is applied.
    """
    from .reviewer import ReviewVerdict, Severity

    _HARD_RE = re.compile(r"\[HARD\]", re.IGNORECASE)
    _SOFT_RE = re.compile(r"\[SOFT\]", re.IGNORECASE)

    has_hard = bool(_HARD_RE.search(acceptance_criteria))
    has_soft = bool(_SOFT_RE.search(acceptance_criteria))

    original_verdict = review.verdict
    was_downgraded = False

    # Case 1: all explicit criteria are SOFT
    if has_soft and not has_hard:
        if review.verdict == ReviewVerdict.REJECTED:
            review.verdict = ReviewVerdict.MAJOR_REVISIONS
            was_downgraded = True
        elif review.verdict == ReviewVerdict.MAJOR_REVISIONS:
            review.verdict = ReviewVerdict.MINOR_REVISIONS
            was_downgraded = True

    # Case 2: all blocking issues are MINOR but verdict is harsh
    blocking = review.blocking_issues
    if blocking and review.verdict in (
        ReviewVerdict.MAJOR_REVISIONS,
        ReviewVerdict.REJECTED,
    ):
        all_minor = all(i.severity == Severity.MINOR for i in blocking)
        if all_minor:
            review.verdict = ReviewVerdict.MINOR_REVISIONS
            if not was_downgraded:
                was_downgraded = (review.verdict != original_verdict)

    return (review, was_downgraded, original_verdict)


class Planner:
    """战术执行层——把战略意图拆成执行计划，分派、追踪、汇总。

    Has its own LLM for tactical decomposition.  ZERO tools.
    Manages TaskManager, Worker factory, and Reviewer.

    Usage::

        planner = Planner(
            model="deepseek-chat",
            api_key=os.environ["DEEPSEEK_API_KEY"],
            task_manager=tm,
            worker_factory=_make_worker,
            reviewer=reviewer,
            console=console,
        )
        directive = Directive(goal="Write a hello-world script in Python.")
        report = planner.execute(directive)
        print(report.summary)
    """

    # -- system prompts -------------------------------------------------------

    # -- context-discipline prompt (shared with Gatekeeper via prompts.py) -------
    # Called as a function in the code below: context_discipline_prompt(...)

    _PLANNER_IDENTITY = """你是 Janus 系统的战术执行层。接收战略指令拆解为独立可执行任务，分派 Worker 执行并追踪。你没有任何工具，只做规划和协调。任务粒度适中——太大无法独立完成，太小则碎片化。"""

    _PLAN_SYSTEM_PROMPT = """\
You are a Janus Planner — a tactical planning specialist. Your job is to \
analyze strategic directives and break them down into discrete, executable \
sub-tasks. You think carefully about what needs to be done, identifying the \
right granularity so each sub-task can be executed independently by a Worker."""

    # -- constructor ----------------------------------------------------------

    def __init__(
        self,
        model: str,
        api_key: str,
        task_manager,
        worker_factory: Callable[..., Any],
        reviewer: Optional[Reviewer] = None,
        max_depth: int = 3,
        console: Optional[Console] = None,
        budget_pool: int = 200,  # L3-6: total tool-call budget for all sub-tasks
    ) -> None:
        """Create a Planner.

        Args:
            model: Model name for tactical decomposition (can be lighter
                than Gatekeeper's model).
            api_key: DeepSeek API key.
            task_manager: The ``TaskManager`` used to track task lifecycle.
            worker_factory: A callable that returns a **fresh** ``Worker``
                for each dispatched task.  May accept an optional
                ``model_override`` keyword argument.
            reviewer: Optional ``Reviewer`` for auditing Worker output.
            max_depth: Maximum recursion depth for task decomposition
                (default 3).
            console: Optional ``Console`` for CLI observability output.
        """
        self._model = model
        self._task_manager = task_manager
        self._worker_factory = worker_factory
        self._reviewer = reviewer
        self._max_depth = max_depth
        self._console = console
        self._last_error: Optional[str] = None
        self._current_priority: str = "normal"
        self._budget_pool: int = budget_pool
        self._worker_model: Optional[str] = (
            model  # Planner's model is also the default Worker model
        )

        # INSPECTOR-INDEPENDENCE: 收集被Planner降级的Reviewer原始裁决
        self._reviewer_findings: list[dict] = []

        # FEEDFORWARD-LOOP: 收集Reviewer对分解策略的反馈，改进未来的_plan()
        self._review_feedback: list[str] = []

        self._client = OpenAI(
            base_url="https://api.deepseek.com",
            api_key=api_key,
        )

    # -- public API: execute --------------------------------------------------

    def execute(self, directive: Directive) -> ExecutionReport:
        """Execute the full planning → dispatch → summarize pipeline.

        Args:
            directive: Strategic directive from Gatekeeper.

        Returns:
            An ``ExecutionReport`` summarizing the outcome.
        """
        # ── 1. Reset state ──────────────────────────────────────────────
        self._task_manager.reset()
        self._last_error = None  # clear stale error from previous run
        self._reviewer_findings.clear()  # INSPECTOR-INDEPENDENCE
        self._review_feedback.clear()    # FEEDFORWARD-LOOP: reset per execution

        # ── 2. Tactical decomposition ───────────────────────────────────
        specs = self._plan(directive)

        if not specs:
            detail = self._last_error or "Unknown reason"
            return ExecutionReport(
                status="failed",
                total_tasks=0,
                passed=0,
                failed=0,
                summary=f"Planner could not decompose the directive. Reason: {detail}",
                details=[detail],
                goal=directive.goal,
                constraints=directive.constraints,
            )

        logger.info("Planner decomposed directive into %d sub-task(s).", len(specs))

        # ── console: phase_decompose ────────────────────────────────────
        if self._console:
            tasks_lines = "\n".join(
                f"  ✓ {s.description}" for s in specs
            )
            self._console.phase_decompose(len(specs), tasks_lines)

        # ── 3. Dispatch + Review + Retry ────────────────────────────────
        # Pulse once in quiet mode so the user knows work is happening.
        if self._console:
            self._console.working_pulse()

        # Priority-driven retry budget:
        #   speed   → max_retries=0 (fail fast, no retries)
        #   quality → max_retries=3 (thorough review)
        #   balanced / normal / other → max_retries=2 (default)
        _priority_retries: dict[str, int] = {
            "speed": 0, "urgent": 0,
            "quality": 3,
            "balanced": 2, "normal": 2,
        }
        max_retries = _priority_retries.get(
            directive.priority.lower(), 2
        )
        self._current_priority = directive.priority.lower()

        results: list[TaskResult] = []
        for i, spec in enumerate(specs):
            worker_id = f"worker-{i}"
            self._task_manager.add_task(spec)
            self._task_manager.mark_running(spec.task_id, worker_id=worker_id)

            if self._console:
                self._console.task_start(spec.task_id, spec.description)

            t_start = time.perf_counter()
            try:
                result = self._dispatch_with_review(spec, max_retries=max_retries)
            except Exception as exc:
                logger.exception(
                    "Dispatch loop crashed for task %r.", spec.task_id
                )
                self._last_error = (
                    f"Dispatch crashed for task {spec.task_id}: "
                    f"{type(exc).__name__}: {exc}"
                )
                result = TaskResult(
                    status=TaskStatus.FAILURE,
                    summary=f"Dispatch crashed: {type(exc).__name__}",
                    result=str(exc),
                    confidence=Confidence.LOW,
                )
            elapsed = time.perf_counter() - t_start
            result.worker_id = worker_id
            results.append(result)

            if self._console:
                self._console.task_done(
                    spec.task_id, result.status.value, elapsed
                )

        # ── 4. Return summary ───────────────────────────────────────────
        return self._summarize(results, directive.goal, directive.constraints, specs)

    # -- internal: plan (decomposition) ---------------------------------------

    @staticmethod
    def _extract_path_from_goal(goal: str) -> Optional[str]:
        """Extract a filesystem path from the user's goal string.

        Looks for absolute paths (Unix /abs/path or Windows C:\\abs\\path),
        home-relative paths (~/...), and explicit relative paths (./...).
        Only a path that actually exists on disk is returned — avoids
        hallucinated paths from free-form text that happens to look
        path-like.

        Returns the first existing directory path found, or None.
        """
        # Patterns that capture plausible path-like strings in free text.
        # Ordered by specificity: absolute paths first, then ~, then ./
        _PATH_PATTERNS: list[re.Pattern] = [
            # Windows absolute: C:\foo\bar or D:/baz
            re.compile(r"[A-Za-z]:[/\\][^\s\"'`]+"),
            # Unix absolute: /home/user/project or /tmp/foo
            re.compile(r"/(?:home|tmp|opt|var|etc|usr|mnt|mnt|root|Users)/[^\s\"'`]+"),
            # Generic Unix absolute (less specific, tested after the common-prefix pattern)
            re.compile(r"/[^\s\"'`]{2,}"),
            # Home-relative: ~/project
            re.compile(r"~/[^\s\"'`]+"),
            # Explicit relative: ./project/src
            re.compile(r"\./[^\s\"'`]+"),
        ]

        for pat in _PATH_PATTERNS:
            for match in pat.finditer(goal):
                raw = match.group(0).rstrip(".,;:!?")
                candidate = os.path.expanduser(raw)
                # Walk upward from the candidate until we find an existing
                # directory (the user may have mentioned a file or a path
                # deep inside a project — the project root is what we want).
                check = candidate
                while check:
                    if os.path.isdir(check):
                        return os.path.abspath(check)
                    parent = os.path.dirname(check)
                    if parent == check:
                        break
                    check = parent
        return None

    # FEEDFORWARD-LOOP: 构建审查反馈注入段
    def _build_feedback_section(self) -> str:
        """Build a prompt section with historical review feedback.

        When _review_feedback has accumulated patterns from prior reviews,
        inject them as a "lessons learned" section so the Planner improves
        its decomposition strategy.  Capped at 5 recent items to avoid
        prompt bloat.
        """
        if not self._review_feedback:
            return ""
        recent = self._review_feedback[-5:]  # cap at 5 items
        lines = [
            "\n## 上次审查反馈：请避免以下问题",
            "Reviewer 在前序任务中发现了与任务分解/验收标准相关的问题。",
            "请在本次分解时特别注意避免：\n",
        ]
        for i, fb in enumerate(recent, 1):
            lines.append(f"  {i}. {fb}")
        lines.append("")
        return "\n".join(lines) + "\n"

    # Phase 2: pattern-library-driven decomposition
    # -----------------------------------------------------------

    _STOPWORDS: set[str] = {
        "的", "是", "在", "了", "和", "等", "我", "你", "他", "她", "它",
        "the", "a", "an", "is", "in", "on", "of", "to", "for", "and",
        "with", "that", "this", "from", "by", "at", "or", "as", "be",
        "也", "就", "都", "把", "被", "对", "与", "及", "所", "而",
    }

    def _load_relevant_patterns(
        self, user_goal: str, limit: int = 3
    ) -> list[dict]:
        """Load historical execution patterns relevant to *user_goal*.

        Searches ``janus/patterns/`` for JSON pattern files, scores each
        by keyword overlap with *user_goal*, and returns the top-*limit*
        matches.  Empty patterns are silently filtered.

        Args:
            user_goal: The user's goal text to match against.
            limit: Max number of patterns to return (default 3).

        Returns:
            List of pattern dicts (same shape as the JSON on disk), or
            an empty list when no patterns exist or none match.
        """
        patterns_dir = os.path.join(
            os.path.dirname(__file__), "..", "patterns"
        )
        if not os.path.isdir(patterns_dir):
            return []

        try:
            all_files = os.listdir(patterns_dir)
        except OSError:
            return []

        json_files = [f for f in all_files if f.endswith(".json")]
        if not json_files:
            return []

        # Performance guard: too many files → recent-first limit
        if len(json_files) > 50:
            json_files.sort(
                key=lambda f: os.path.getmtime(
                    os.path.join(patterns_dir, f)
                ),
                reverse=True,
            )
            json_files = json_files[:20]

        # Extract keywords from user_goal
        import re as _re

        goal_lower = user_goal.lower()
        raw_tokens: list[str] = []
        for chunk in _re.split(r'[\s,.;:!?()\[\]{}/\\]+', goal_lower):
            chunk = chunk.strip("。！？；：、，「」『』\"'`")
            if chunk:
                raw_tokens.append(chunk)

        # For CJK text, also try character bigrams as tokens
        cjk_tokens: set[str] = set()
        for token in raw_tokens:
            if any('\u4e00' <= c <= '\u9fff' for c in token):
                for i in range(len(token) - 1):
                    cjk_tokens.add(token[i:i + 2])
        raw_tokens.extend(cjk_tokens)

        goal_keywords: set[str] = {
            t for t in raw_tokens
            if t and t not in self._STOPWORDS and len(t) >= 2
        }
        if not goal_keywords:
            return []

        # Score each pattern
        scored: list[tuple[int, dict]] = []
        for fname in json_files:
            fpath = os.path.join(patterns_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                logger.debug(
                    "_load_relevant_patterns: skipping unreadable %s",
                    fname,
                )
                continue

            success = data.get("success", False)
            lessons = data.get("lessons", "")

            # Skip failed patterns with no lessons (no value)
            if not success and not lessons:
                continue

            desc = data.get("description", "")
            task_type = data.get("task_type", "")
            combined = f"{desc} {task_type}".lower()

            score = sum(1 for kw in goal_keywords if kw in combined)
            if score > 0:
                scored.append((score, data))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:limit]]

    def _build_patterns_section(self, patterns: list[dict]) -> str:
        """Format historical execution patterns into a prompt injection.

        Produces a markdown section suitable for prepending to the
        decomposition prompt in :meth:`_plan`.
        """
        if not patterns:
            return ""

        lines: list[str] = [
            "\n## 历史执行模式参考",
            "",
            "以下是与当前任务相似的历史执行经验，请参考其中的工具序列和教训：",
            "",
        ]
        for i, p in enumerate(patterns, 1):
            success_marker = "成功" if p.get("success") else "失败"
            tools = [t.get("name", "?") for t in p.get("tool_sequence", [])]
            tools_str = ", ".join(tools) if tools else "(无工具调用)"
            desc = p.get("description", "?")
            lessons = p.get("lessons", "")
            success_status = "是" if p.get("success") else "否"

            lines.append(f"模式 {i}（{success_marker}）：")
            lines.append(f"- 任务描述：{desc}")
            lines.append(f"- 使用的工具：{tools_str}")
            lines.append(f"- 经验教训：{lessons}")
            lines.append(f"- 成功：{success_status}")

            if not p.get("success") and lessons:
                lines.append("  ⚠️ 注意避免相同错误")

            lines.append("")

        return "\n".join(lines) + "\n"

    def _plan(self, directive: Directive) -> list[TaskSpec]:
        """Call the LLM to break *directive* into discrete ``TaskSpec`` items.

        Returns an empty list when the LLM returns an error, unparseable
        output, or otherwise fails.
        """
        # Build constraints section if present
        constraints_block = ""
        if directive.constraints:
            constraints_block = (
                f"\nHARD CONSTRAINTS (must be followed — the Planner MUST "
                f"respect these when creating task descriptions and "
                f"acceptance criteria):\n{directive.constraints}\n"
            )

        # Build context section if present (multi-turn conversation history)
        context_block = ""
        if directive.context:
            context_block = (
                f"\n## Recent Conversation Context\n{directive.context}\n"
                f"(Use this to understand the user's intent across "
                f"multiple turns. E.g. 'continue the previous task' means "
                f"look at what was done before.)\n"
            )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._PLANNER_IDENTITY},
            {"role": "system", "content": context_discipline_prompt(
                "the tactical planner, like a chief of staff organizing operations",
                "planning and coordination, not implementation",
            )},
            {"role": "system", "content": self._PLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    # FEEDFORWARD-LOOP: 注入上次审查反馈，改进分解策略
                    self._build_feedback_section()
                    +
                    # Phase 2: 注入历史执行模式，提供工具序列和教训参考
                    self._build_patterns_section(
                        self._load_relevant_patterns(
                            directive.user_goal or directive.goal
                        )
                    )
                    +
                    "Decompose the following goal into a JSON array of task "
                    "objects. Each task object must have:\n"
                    '- "task_id": string, unique identifier (e.g., "task-1", "task-2")\n'
                    '- "description": string, what to do — concrete and actionable\n'
                    '- "acceptance_criteria": string, how to know it\'s done right. '
                    'Prefix EACH criterion with [HARD] or [SOFT]:\n'
                    '    [HARD] = must-have, zero tolerance — failure is blocking.\n'
                    '    [SOFT] = nice-to-have, minor deviations acceptable.\n'
                    '    Unmarked criteria default to [HARD].\n'
                    '    Example: "[HARD] Output must be valid JSON. [SOFT] Variable names should be descriptive."\n'
                    '- "context": string, relevant background information\n'
                    '- "intent": string, WHY this task matters — its role in the '
                    "bigger picture. If not specified, derive from the parent goal.\n"
                    "\n"
                    "Rules:\n"
                    "- Each task must be self-contained enough for a Worker "
                    "to execute independently\n"
                    "- Tasks should be independent when possible "
                    "(no inter-task dependencies for Phase 1)\n"
                    "- If the goal is simple, a single task is acceptable\n"
                    "- If the goal is too vague to decompose, output: "
                    '{"error": "reason"}\n'
                    "- Mark at least one criterion [HARD] — every task has core requirements.\n"
                    "- All file paths in task descriptions MUST be absolute paths. "
                    "If the user specifies a project directory, include that path "
                    "in every task description.\n"
                    "- Output ONLY valid JSON, no extra text\n"
                    "\n"
                    "## Worker Tool Limitations (IMPORTANT)\n"
                    "The Worker's tools have these constraints. Design acceptance "
                    "criteria accordingly:\n"
                    "- read_file returns up to 50000 characters per call. "
                    "For larger files, the Worker can use offset/limit parameters "
                    "to read in chunks. Do NOT set acceptance criteria requiring "
                    "'complete' file return for files that may exceed this limit — "
                    "instead require 'key sections' or 'representative sample'.\n"
                    "- web_extract truncates at 3000 characters per URL.\n"
                    "- The Worker extracts its working directory from the user's "
                    "original input (user_goal). Do NOT embed CWD in task context — "
                    "the Worker will find the target path from the user's own words.\n"
                    "\n"
                    f"## Strategic Intent\n{directive.intent or 'Complete the goal as stated.'}\n"
                    f"{context_block}"
                    f"{constraints_block}"
                    f"## Goal\n{directive.goal}"
                ),
            },
        ]

        _API_MAX_RETRIES = 3
        _API_RETRY_DELAY = 2.0
        response = None
        last_api_error = None

        for api_attempt in range(_API_MAX_RETRIES + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    extra_body={"thinking": {"type": "enabled"}},
                )
                break
            except Exception as exc:
                last_api_error = exc
                if api_attempt < _API_MAX_RETRIES:
                    delay = _API_RETRY_DELAY * (2 ** api_attempt)
                    logger.warning(
                        "Planner API call failed (attempt %d/%d), "
                        "retrying in %.1fs: %s",
                        api_attempt + 1, _API_MAX_RETRIES, delay, exc,
                    )
                    import time as _time
                    _time.sleep(delay)
                else:
                    logger.exception("Plan API call failed after %d retries.", _API_MAX_RETRIES)
                    self._last_error = f"API call failed after {_API_MAX_RETRIES} retries: {type(exc).__name__}"
                    return []

        if response is None:
            self._last_error = "API call failed: all retries exhausted"
            return []

        if not response.choices:
            logger.warning("Plan API returned empty choices.")
            self._last_error = "API returned no response choices"
            return []

        choice = response.choices[0]
        content = choice.message.content or ""

        raw_data = extract_json(content)

        if raw_data is None:
            logger.warning(
                "Plan LLM response contained no parseable JSON: %r",
                content[:200],
            )
            self._last_error = (
                "LLM returned unparseable response. Check API key and balance."
            )
            return []

        # -- Error dict? ------------------------------------------------------
        if isinstance(raw_data, dict) and "error" in raw_data:
            logger.info("Plan returned error: %s", raw_data["error"])
            self._last_error = f"LLM rejected goal: {raw_data['error']}"
            return []

        # -- Array of task objects? -------------------------------------------
        if isinstance(raw_data, list):
            specs: list[TaskSpec] = []

            # L3-6: compute per-task tool-call budget from a total pool
            _valid_items = [it for it in raw_data if isinstance(it, dict)
                            and it.get("task_id") and it.get("description")]
            _total_pool = getattr(self, '_budget_pool', 200)  # L3-6: configurable
            _num_tasks = max(len(_valid_items), 1)
            _per_task_budget = max(_total_pool // _num_tasks, 10)

            for item in raw_data:
                if not isinstance(item, dict):
                    continue
                try:
                    task_id = str(item.get("task_id", "") or "")
                    if not task_id:
                        # Auto-generate task IDs when LLM returns empty
                        task_id = f"task-{len(specs) + 1}"
                    context_str = str(item.get("context", ""))
                    # Append directive-level context (multi-turn history)
                    # so Workers have full conversational context.
                    if directive.context:
                        if context_str:
                            context_str = (
                                f"{context_str}\n\n"
                                f"## Conversation Context\n{directive.context}"
                            )
                        else:
                            context_str = directive.context
                    specs.append(
                        TaskSpec(
                            task_id=task_id,
                            description=str(item.get("description", "")),
                            acceptance_criteria=str(
                                item.get("acceptance_criteria", "")
                            ),
                            context=context_str,
                            intent=str(item.get("intent", "") or directive.intent),
                            goal=directive.goal,
                            user_goal=directive.user_goal or directive.goal,
                            constraints=directive.constraints,
                            depth=1,
                            max_tool_calls=_per_task_budget,  # L3-6
                        )
                    )
                    # Validate the spec after construction
                    if not specs[-1].validate():
                        logger.warning(
                            "Skipping invalid TaskSpec — task_id=%r description=%r",
                            specs[-1].task_id,
                            specs[-1].description[:50],
                        )
                        specs.pop()
                        continue
                except (TypeError, ValueError) as exc:
                    logger.warning("Skipping malformed task item: %s", exc)
                    continue
            if not specs:
                self._last_error = (
                    "LLM returned empty task list — could not decompose "
                    "the directive into actionable tasks."
                )
            # L3-8: dependency-aware difficulty sort — hard tasks first,
            # but tasks that depend on others stay after their dependencies.
            specs = _difficulty_sort_preserve_deps(specs)
            return specs

        logger.warning(
            "Plan returned unexpected JSON shape: %s",
            type(raw_data).__name__,
        )
        return []

    # -- internal: dispatch + review + retry ----------------------------------

    def _dispatch_with_review(
        self, spec: TaskSpec, max_retries: int = 2
    ) -> TaskResult:
        """Run worker, review, retry with graded logic.

        Uses the review verdict and issue severities to decide retry strategy:

        +---------------------------+-------------------------------------------+
        | Verdict                   | Action                                    |
        +===========================+===========================================+
        | APPROVED / NOTES          | Accept immediately                        |
        | MINOR_REVISIONS           | Retry once with feedback, auto-accept     |
        | MAJOR_REVISIONS           | Retry up to 2× with full re-review        |
        | REJECTED                  | Retry up to 2×, then fail                 |
        +---------------------------+-------------------------------------------+

        Issue severities also influence per-attempt decisions:
        - CRITICAL → always triggers retry
        - MAJOR    → triggers retry
        - MINOR    → retry once, then accept with notes
        - SUGGESTION → never blocks

        Results are accepted as-is when no reviewer is configured.
        """
        # L3-4: deterministic deep-review sampling (10 %)
        # Seed from task_id hash → same task always gets the same mode on retry.
        _task_hash = hash(spec.task_id) & 0xFFFFFFFF
        _is_deep_review = (_task_hash % 100) < 10
        _artifact_limit = 8000 if _is_deep_review else 3000
        _artifact_total = 32000 if _is_deep_review else 16000

        last_review = None
        original_task_id = spec.task_id  # saved for fallback when spec reassigned
        for attempt in range(max_retries + 1):
            result = self._run_worker(spec)

            # ── Desk reject: FAILURE → skip review entirely ────────────
            # Rule-level short-circuit (no LLM call).  The Reviewer already
            # handles FAILURE internally, but skipping the call here saves
            # the overhead of instantiating the review prompt and API round-trip.
            if result.status == TaskStatus.FAILURE:
                self._task_manager.mark_failed(spec.task_id, result.result)
                return result

            # If no reviewer configured — accept result as-is
            if not self._reviewer:
                self._task_manager.mark_completed(spec.task_id, result)
                return result

            # ── Desk check: pre-screen Worker output ─────────────────────
            # Lightweight heuristics flag suspicious results so the Reviewer
            # can apply extra scrutiny.  Zero LLM cost — pure rule checks.
            review_spec = spec
            warning = self._desk_check(result)
            if warning:
                logger.info(
                    "Desk check warning for %r: %s", spec.task_id, warning
                )
                review_spec = TaskSpec(
                    task_id=spec.task_id,
                    description=spec.description,
                    acceptance_criteria=spec.acceptance_criteria,
                    context=(
                        f"{spec.context}\n\n"
                        f"[DESK CHECK WARNING — pre-review screening flagged "
                        f"potential issues] {warning}"
                    ),
                    intent=spec.intent,
                    goal=spec.goal,
                    user_goal=spec.user_goal,
                    constraints=spec.constraints,
                    depth=spec.depth,
                )

            # REVIEW step
            # L3-4: pass deep-review flag + artifact limits
            review = self._reviewer.review(
                review_spec, result,
                artifact_max_per_file=_artifact_limit,
                artifact_max_total=_artifact_total,
                deep_review=_is_deep_review,
            )
            last_review = review

            # FEEDFORWARD-LOOP: 从Reviewer问题中提取对分解策略的反馈
            if review.issues:
                self._record_decomposition_feedback(
                    review, spec.acceptance_criteria
                )

            # ── HARD/SOFT verdict adjustment ──────────────────────────
            # If the Reviewer over-reacted to SOFT-only criteria, demote the
            # verdict so the Worker isn't forced through unnecessary retries.
            # INSPECTOR-INDEPENDENCE: 保存原始verdict，检测降级
            _original_verdict = review.verdict
            review = self._adjust_verdict_for_criteria(
                review, spec.acceptance_criteria
            )
            # INSPECTOR-INDEPENDENCE: 如果裁决被降级，记录到独立监察发现
            if review.verdict != _original_verdict:
                self._reviewer_findings.append({
                    "task_id": spec.task_id,
                    "original_verdict": _original_verdict.value,
                    "downgraded_to": review.verdict.value,
                    "issues": [
                        {"severity": i.severity.value, "description": i.description}
                        for i in last_review.issues
                    ],
                    "reason": (
                        f"Planner._adjust_verdict_for_criteria downgraded "
                        f"from {_original_verdict.value} to {review.verdict.value} "
                        f"based on criteria label analysis"
                    ),
                })

            # ── APPROVED / APPROVED_WITH_NOTES → accept ──────────────
            if review.verdict in (
                ReviewVerdict.APPROVED,
                ReviewVerdict.APPROVED_WITH_NOTES,
            ):
                self._task_manager.mark_completed(spec.task_id, result)
                if self._console:
                    self._console.review_pass(spec.task_id, review.evidence)
                return result

            # ── Build feedback string from blocking issues ───────────
            feedback_parts = [review.summary]
            for issue in review.blocking_issues:
                feedback_parts.append(
                    f"[{issue.severity.value}] {issue.description}"
                )
            feedback = "\n".join(feedback_parts)

            # ── MINOR_REVISIONS → retry once, then auto-accept ───────
            if review.verdict == ReviewVerdict.MINOR_REVISIONS:
                if attempt == 0:
                    logger.info(
                        "Review: MINOR_REVISIONS for %r — retrying once then auto-accept.",
                        spec.task_id,
                    )
                    if self._console:
                        self._console.review_fail(
                            spec.task_id,
                            [i.description for i in review.issues],
                            attempt,
                        )
                    spec = self._make_retry_spec(
                        spec, feedback,
                        verdict=review.verdict.value,
                        attempt=attempt + 1,
                        previous_result=result,
                    )
                    # _make_retry_spec returns a TaskResult on validation failure
                    if isinstance(spec, TaskResult):
                        self._task_manager.mark_failed(original_task_id, spec.result)
                        return spec
                    continue
                else:
                    # attempt == 1: quick re-review to verify the fix actually worked
                    # L3-4: pass deep-review flag + artifact limits to re-review too
                    re_review = self._reviewer.review(
                        spec, result,
                        artifact_max_per_file=_artifact_limit,
                        artifact_max_total=_artifact_total,
                        deep_review=_is_deep_review,
                    )

                    # Check if new MAJOR or CRITICAL issues surfaced
                    escalated = any(
                        i.severity in (Severity.MAJOR, Severity.CRITICAL)
                        for i in re_review.issues
                    )

                    if escalated and attempt < max_retries:
                        # Retry introduced serious issues — escalate
                        logger.info(
                            "Re-review after MINOR_REVISIONS retry for %r: "
                            "escalating — new MAJOR/CRITICAL issues found.",
                            spec.task_id,
                        )
                        review = re_review
                        esc_feedback_parts = [re_review.summary]
                        for issue in re_review.blocking_issues:
                            esc_feedback_parts.append(
                                f"[{issue.severity.value}] {issue.description}"
                            )
                        spec = self._make_retry_spec(
                            spec, "\n".join(esc_feedback_parts),
                            verdict=re_review.verdict.value,
                            attempt=attempt + 1,
                            previous_result=result,
                        )
                        if isinstance(spec, TaskResult):
                            self._task_manager.mark_failed(original_task_id, spec.result)
                            return spec
                        if self._console:
                            self._console.review_fail(
                                spec.task_id,
                                [i.description for i in re_review.issues],
                                attempt,
                            )
                        continue

                    # Still minor (or no more retries) — accept
                    verdict_note = (
                        " [审查: 轻微修改后自动通过]"
                        if not escalated
                        else " [审查: 轻微修改后自动通过（重审仍存在轻微问题）]"
                    )
                    logger.info(
                        "Review: MINOR_REVISIONS for %r — re-review %s, accepting.",
                        spec.task_id,
                        "passed" if re_review.passed else "still minor",
                    )
                    self._task_manager.mark_completed(spec.task_id, result)
                    if self._console:
                        self._console.review_pass(
                            spec.task_id,
                            f"Re-reviewed after minor revisions retry. "
                            f"Verdict: {re_review.verdict.value}. {re_review.evidence}",
                        )
                    result.summary = f"{result.summary}{verdict_note}"
                    return result

            # ── MAJOR_REVISIONS / REJECTED → retry with full review ──
            if attempt < max_retries:
                logger.info(
                    "Review: %s for %r (attempt %d/%d): %s",
                    review.verdict.value,
                    spec.task_id,
                    attempt + 1,
                    max_retries + 1,
                    review.summary,
                )
                if self._console:
                    self._console.review_fail(
                        spec.task_id,
                        [i.description for i in review.issues],
                        attempt,
                    )
                spec = self._make_retry_spec(
                    spec, feedback,
                    verdict=review.verdict.value,
                    attempt=attempt + 1,
                    previous_result=result,
                )
                if isinstance(spec, TaskResult):
                    self._task_manager.mark_failed(original_task_id, spec.result)
                    return spec

        # All retries exhausted
        # L3-9: mark with [RETRY_EXHAUSTED] so Gatekeeper can escalate strategy
        self._last_error = (
            f"Task {spec.task_id} failed review after "
            f"{max_retries + 1} attempts"
        )
        # Collect review issues for user-facing details
        review_issues_text = ""
        if last_review and last_review.issues:
            review_issues_text = "; ".join(
                f"[{i.severity.value}] {i.description}"
                for i in last_review.issues
            )
        review_verdict_text = (
            f" (审查判定: {last_review.verdict.value})" if last_review else ""
        )
        failed_result = TaskResult(
            status=TaskStatus.FAILURE,
            retry_exhausted=True,  # L3-9: structured field
            summary=(
                f"Failed review after {max_retries} retries"
                f"{review_verdict_text}"
            ),
            result=(
                f"All {max_retries + 1} review attempts failed. "  # L3-9
                f"The worker could not produce output meeting the "
                f"acceptance criteria.\n"
                f"[REVIEW FAILED] Issues: {review_issues_text or '(none recorded)'}"
            ),
        )
        # Record failure in TaskManager AFTER constructing the real TaskResult
        # (so the TaskManager stores the accurate result, not a placeholder)
        self._task_manager.mark_failed(
            spec.task_id, failed_result.result
        )
        return failed_result

    # -- HARD/SOFT criteria analysis -----------------------------------------

    _HARD_RE = re.compile(r"\[HARD\]", re.IGNORECASE)
    _SOFT_RE = re.compile(r"\[SOFT\]", re.IGNORECASE)

    @staticmethod
    def _analyze_criteria_labels(acceptance_criteria: str) -> tuple[bool, bool]:
        """Check whether acceptance_criteria has explicit [HARD] or [SOFT] markers.

        Returns:
            (has_hard, has_soft) — each is True if there is at least one
            explicit marker.  Unmarked criteria are NOT counted (they default
            to HARD by convention, but are not detected by this method).
        """
        has_hard = bool(Planner._HARD_RE.search(acceptance_criteria))
        has_soft = bool(Planner._SOFT_RE.search(acceptance_criteria))
        return has_hard, has_soft

    @staticmethod
    def _adjust_verdict_for_criteria(
        review: ReviewResult,
        acceptance_criteria: str,
    ) -> ReviewResult:
        """Demote the review verdict when it over-reacts to SOFT-only criteria.

        Delegates to the shared module-level ``calibrate_and_adjust``.
        """
        calibrate_and_adjust(review, acceptance_criteria)
        return review

    # FEEDFORWARD-LOOP: 从Reviewer问题中提取分解策略反馈
    @staticmethod
    def _extract_decomposition_feedback(
        review: ReviewResult,
        acceptance_criteria: str,
    ) -> list[str]:
        """Extract decomposition-strategy feedback from Reviewer issues.

        Looks for patterns indicating that the task decomposition or
        acceptance criteria themselves are part of the problem — not just
        Worker execution quality.  These patterns are fed back to _plan()
        to improve future decomposition quality.

        Returns a list of feedback strings (may be empty).
        """
        # FEEDFORWARD-LOOP: 关键词匹配——审查问题是否指向分解/验收标准问题
        _FEEDBACK_KEYWORDS = [
            # English
            "acceptance criteria", "too vague", "too broad", "unclear",
            "ambiguous", "task decomposition", "poorly defined",
            "not specific enough", "scope too large", "criterion",
            "impossible to verify", "unmeasurable",
            # Chinese
            "验收标准", "太模糊", "太宽泛", "不明确", "不清晰",
            "任务分解", "分解粒度", "不够具体", "范围太大",
            "无法验证", "不可衡量", "定义不清",
        ]

        feedback_items: list[str] = []
        for issue in review.issues:
            desc_lower = issue.description.lower()
            for kw in _FEEDBACK_KEYWORDS:
                if kw.lower() in desc_lower:
                    feedback_items.append(
                        f"[{issue.severity.value}] {issue.description}"
                    )
                    break  # one feedback entry per issue

        return feedback_items

    # FEEDFORWARD-LOOP: 将提取的反馈存储到Planner实例
    def _record_decomposition_feedback(
        self,
        review: ReviewResult,
        acceptance_criteria: str,
    ) -> None:
        """Extract and store decomposition feedback from a review.

        Called from _dispatch_with_review after each review.
        Deduplicates against existing feedback to avoid noise.
        """
        new_feedback = self._extract_decomposition_feedback(
            review, acceptance_criteria
        )
        for fb in new_feedback:
            # Simple dedup: check if similar feedback already recorded
            if fb not in self._review_feedback:
                self._review_feedback.append(fb)

    @staticmethod
    def _make_retry_spec(
        spec: TaskSpec, feedback: str,
        verdict: str = "", attempt: int = 1,
        previous_result: Optional[TaskResult] = None,
    ) -> "TaskSpec | TaskResult":
        """Build a new TaskSpec with review feedback appended to context.

        Preserves intent and all other fields so the Worker retains the
        original task's purpose and constraints.

        The formatted feedback tells the Worker exactly which issues to
        fix and requires explicit proof for each one so the Reviewer can
        verify the changes.

        Args:
            spec: Original TaskSpec.
            feedback: Review feedback string describing issues.
            verdict: Reviewer verdict value for context.
            attempt: Retry attempt number.
            previous_result: The previous TaskResult with artifacts so the
                Worker knows what files already exist on retry.
        """
        verdict_line = f"Verdict: {verdict}\n" if verdict else ""

        # Build context: original + review feedback + prior artifacts
        context_parts = [spec.context]

        if previous_result and previous_result.artifacts:
            context_parts.append(
                "\n--- PREVIOUS ATTEMPT ARTIFACTS (files that already "
                "exist — modify/extend them, do not recreate) ---\n"
                + "\n".join(f"  • {a}" for a in previous_result.artifacts)
            )

        context_parts.append(
            f"\n--- REVIEW FEEDBACK (attempt {attempt}) ---\n"
            f"{verdict_line}"
            f"The following issues were found. You MUST fix EACH one "
            f"and include PROOF:\n\n"
            f"Issues to fix:\n"
            f"{feedback}\n\n"
            f"For each issue you fix, include in your result: "
            f"'✓ Fixed [issue]: what you changed and why it now "
            f"satisfies the requirement.'"
        )

        retry_spec = TaskSpec(
            task_id=spec.task_id,
            description=spec.description,
            acceptance_criteria=spec.acceptance_criteria,
            context="\n".join(context_parts),
            intent=spec.intent,
            goal=spec.goal,
            user_goal=spec.user_goal,
            constraints=spec.constraints,
            depth=spec.depth,
        )
        if not retry_spec.validate():
            logger.warning(
                "_make_retry_spec produced invalid TaskSpec "
                "(task_id=%r, description=%r). Returning FAILURE result, "
                "not the original spec (which would cause an infinite retry loop).",
                retry_spec.task_id,
                retry_spec.description[:50] if retry_spec.description else "",
            )
            from .protocol import Confidence
            return TaskResult(
                status=TaskStatus.FAILURE,
                summary=(
                    f"_make_retry_spec validation failed for {spec.task_id}: "
                    f"could not construct valid retry spec."
                ),
                result=f"Feedback: {feedback[:200]}",
                confidence=Confidence.LOW,
            )
        return retry_spec

    def _run_worker(self, spec: TaskSpec) -> TaskResult:
        """Run a single worker dispatch via the worker factory.

        Returns the ``TaskResult`` from the worker — caller is responsible
        for status tracking and review.
        """
        # Worker factory mode
        try:
            worker = self._worker_factory(
                model_override=self._worker_model
            )
        except TypeError:
            # Backward compat: factory doesn't accept model_override
            try:
                worker = self._worker_factory()
            except Exception as exc2:
                logger.exception(
                    "Worker factory crashed on task %r.", spec.task_id
                )
                self._last_error = (
                    f"Worker factory crashed for task {spec.task_id}: "
                    f"{type(exc2).__name__}"
                )
                return TaskResult(
                    status=TaskStatus.FAILURE,
                    summary=f"Worker factory crashed: {type(exc2).__name__}",
                    result=str(exc2),
                    confidence=Confidence.LOW,
                )
        except Exception as exc:
            logger.exception(
                "Worker factory crashed on task %r.", spec.task_id
            )
            self._last_error = (
                f"Worker factory crashed for task {spec.task_id}: "
                f"{type(exc).__name__}"
            )
            return TaskResult(
                status=TaskStatus.FAILURE,
                summary=f"Worker factory crashed: {type(exc).__name__}",
                result=str(exc),
                confidence=Confidence.LOW,
            )

        # Pass console to Worker (passive observer pattern)
        if self._console is not None:
            worker.console = self._console

        # Pass reviewer to Worker for sub-Worker audit during self-decomposition
        if self._reviewer is not None:
            worker._reviewer = self._reviewer

        # Pass priority to Worker for urgency-aware execution
        if hasattr(self, '_current_priority'):
            worker.priority = self._current_priority

        # Pass max_depth to Worker for unified depth limiting
        worker._max_depth = self._max_depth

        try:
            result = worker.run(spec)
        except Exception as exc:
            logger.exception("Worker crashed on task %r.", spec.task_id)
            self._last_error = (
                f"Worker crashed for task {spec.task_id}: "
                f"{type(exc).__name__}: {exc}"
            )
            result = TaskResult(
                status=TaskStatus.FAILURE,
                summary=f"Worker crashed: {type(exc).__name__}",
                result=str(exc),
                confidence=Confidence.LOW,
            )

        # Record _last_error for non-exception FAILUREs so Gatekeeper
        # can surface them (tool budget, depth limit, empty decomposition).
        if result.status == TaskStatus.FAILURE:
            self._last_error = (
                f"Worker {spec.task_id} returned FAILURE: {result.summary}"
            )

        return result

    # -- internal: desk reject pre-screening ----------------------------------

    @staticmethod
    def _desk_check(result: TaskResult) -> Optional[str]:
        """Pre-screen a Worker result before sending to Reviewer.

        Simple heuristics — no LLM call.  Returns ``None`` if the result
        looks reasonable, or a warning string if something is suspicious.
        The warning is injected into the review context so the Reviewer
        can apply extra scrutiny.

        Modeled on academic "desk reject": the editor checks paper quality
        BEFORE sending to reviewers — obviously flawed submissions are
        flagged immediately without wasting reviewer time.
        """
        warnings: list[str] = []

        # ── Check 1: result field is empty ────────────────────────────
        raw = result.result
        if not isinstance(raw, str):
            raw = json.dumps(raw, ensure_ascii=False) if raw is not None else ""
        result_text = raw.strip()
        if not result_text:
            warnings.append(
                "Worker result is empty — may be incomplete or failed silently."
            )

        # ── Check 2: result looks like just a file path ───────────────
        elif (
            "\n" not in result_text
            and len(result_text) < 200
            and (
                "/" in result_text
                or "\\" in result_text
                or result_text.endswith(
                    (".py", ".txt", ".md", ".json", ".yaml", ".yml")
                )
            )
        ):
            warnings.append(
                "Worker result appears to be just a file path rather than "
                "substantive output — may lack meaningful content."
            )

        # ── Check 3: artifacts present but summary is generic ─────────
        _generic_summaries = {
            "task completed", "done", "finished", "ok", "success",
            "completed successfully", "task done", "executed",
            "complete", "completed",
        }
        if result.artifacts and result.summary.strip().lower() in _generic_summaries:
            warnings.append(
                f"Worker listed {len(result.artifacts)} artifact(s) but "
                f"summary is generic ('{result.summary}') — may lack "
                f"meaningful description of what was produced."
            )

        if warnings:
            return " | ".join(warnings)
        return None

    # -- internal: summarize --------------------------------------------------

    def _summarize(
        self, results: list[TaskResult],
        goal: str = "", constraints: str = "",
        specs: list[TaskSpec] | None = None,
    ) -> ExecutionReport:
        """Aggregate task results into an ExecutionReport.

        Pure logic — no LLM call needed.

        When *specs* is provided, failure details are enriched with the
        original acceptance criteria and extracted review issues so the
        Gatekeeper's recovery loop can produce better diagnoses.
        """
        total = len(results)
        passed = sum(1 for r in results if r.status == TaskStatus.SUCCESS)
        failed = sum(1 for r in results if r.status == TaskStatus.FAILURE)

        if total == 0:
            return ExecutionReport(
                status="failed",
                total_tasks=0,
                passed=0,
                failed=0,
                summary="No tasks were executed.",
                details=["No tasks."],
                failed_tasks=[],
                goal=goal,
                constraints=constraints,
            )

        if failed == 0:
            status = "completed"
        elif passed == 0:
            status = "failed"
        else:
            status = "partial"

        summary = f"Completed: {passed}/{total} tasks."
        if failed > 0:
            summary += f", {failed} failed"

        details: list[str] = []
        # Track failure reasons for a dedicated failures section
        failure_details: list[str] = []
        # Structured failure data for Gatekeeper diagnosis
        failed_tasks: list[dict[str, str]] = []

        # Build lookup from worker_id → TaskSpec acceptance criteria.
        # Worker IDs are assigned sequentially in the dispatch loop,
        # so worker-0 corresponds to specs[0], worker-1 to specs[1], etc.
        spec_map: dict[str, str] = {}
        if specs:
            for i, s in enumerate(specs):
                spec_map[f"worker-{i}"] = s.acceptance_criteria

        for r in results:
            task_label = r.worker_id or "?"
            # Detect sub-worker review failures
            is_sub_review_failed = "[SUB-WORKER REVIEW FAILED" in r.result
            is_review_failed = "[REVIEW FAILED]" in r.result

            if r.status == TaskStatus.FAILURE:
                # Rich failure detail: include the full result text
                detail_line = (
                    f"{_zhu(task_label)}: {r.summary}"
                )
                details.append(detail_line)
                # Extract the first meaningful failure reason from result
                failure_reason = r.result.split("\n")[0] if r.result else r.summary
                # Look up acceptance criteria for richer failure diagnosis
                acceptance_criteria = spec_map.get(task_label, "")
                criteria_note = (
                    f"\n    验收标准: {acceptance_criteria}"
                    if acceptance_criteria else ""
                )
                if is_sub_review_failed:
                    failure_details.append(
                        f"{_qing(task_label)} 子任务审核未通过: {r.summary}"
                    )
                elif is_review_failed:
                    # Extract review issues
                    review_line = ""
                    for line in r.result.split("\n"):
                        if "Issues:" in line:
                            review_line = line.strip()
                            break
                    failure_details.append(
                        f"{_zhu(task_label)} 审查未通过 — {r.summary}"
                        + (f"\n    审查问题: {review_line}" if review_line else "")
                        + criteria_note
                    )
                    # Add structured failure data
                    failed_tasks.append({
                        "task_id": task_label,
                        "summary": r.summary,
                        "acceptance_criteria": acceptance_criteria,
                        "review_issues": review_line,
                    })
                else:
                    failure_details.append(
                        f"{_zhu(task_label)} — {failure_reason}"
                        + criteria_note
                    )
                    # Add structured failure data even for non-review failures
                    failed_tasks.append({
                        "task_id": task_label,
                        "summary": r.summary,
                        "acceptance_criteria": acceptance_criteria,
                        "review_issues": failure_reason,
                    })
            elif r.status == TaskStatus.SUCCESS:
                verdict_note = ""
                if is_sub_review_failed:
                    verdict_note = f" [{_qing('子任务审查失败')}]"
                elif "审查:" in r.summary:
                    verdict_note = ""
                details.append(
                    f"{_jin(task_label)}: {r.summary}{verdict_note}"
                )
            else:
                details.append(
                    f"[{r.status.value}] {task_label}: {r.summary}"
                )

        # Append failures section if any failed
        if failure_details:
            details.append("")
            details.append("── 失败详情 ──")
            details.extend(failure_details)

        logger.info(
            "Planner summary — %d total, %d succeeded, %d failed.",
            total,
            passed,
            failed,
        )

        # NOTE: console.summary() is intentionally NOT called here.
        # The Gatekeeper._report_to_user() is the single source of truth
        # for the final user-facing summary — no duplicate counting.

        return ExecutionReport(
            status=status,
            total_tasks=total,
            passed=passed,
            failed=failed,
            summary=summary,
            details=details,
            failed_details=failure_details,
            failed_tasks=failed_tasks,
            goal=goal,
            constraints=constraints,
            has_retry_exhausted=any(r.retry_exhausted for r in results),  # L3-9
            # INSPECTOR-INDEPENDENCE: 传递独立监察发现
            reviewer_findings=list(self._reviewer_findings),
        )

