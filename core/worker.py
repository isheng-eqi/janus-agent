"""
Janus Worker — LLM-driven execution loop with tool registry.

Phase 1: single-level delegation. The Worker receives a TaskSpec, runs an
LLM-driven loop using available tools, and returns a TaskResult to the Gatekeeper.
"""

from __future__ import annotations

import inspect
import io
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, TYPE_CHECKING

from .protocol import Confidence, TaskResult, TaskSpec, TaskStatus
from .prompts import extract_json
from .tool_log import log_tool_call  # L3-2
from .planner import calibrate_and_adjust, Planner  # L3 shared calibration + feedback extraction

if TYPE_CHECKING:
    from .console import Console
    from .reviewer import Reviewer

# Runtime import for graded review verdict checks
from .reviewer import ReviewVerdict  # noqa: E402 (after TYPE_CHECKING block)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenAI client — lazy import with a helpful error message
# ---------------------------------------------------------------------------
try:
    from openai import OpenAI  # type: ignore[import-untyped]
except ImportError as exc:
    raise ImportError(
        "The `openai` package is required by Janus Worker. "
        "Install it with: pip install openai"
    ) from exc


# ============================================================================
# ToolDef + ToolRegistry
# ============================================================================


@dataclass
class ToolDef:
    """Definition of a tool the Worker can call.

    Attributes:
        name: Unique tool identifier (e.g. "read_file").
        description: Human-readable description shown to the LLM.
        parameters: Mapping of parameter names to descriptions.
        func: The Python callable that implements the tool.
    """

    name: str
    description: str
    parameters: dict[str, str]
    func: Callable[..., str]


# Parameter-name aliases the LLM may use instead of the canonical name.
# Maps LLM-preferred names → canonical (function signature) names.
_PARAM_ALIASES: dict[str, str] = {
    "url": "target_url",
    "endpoint": "target_url",
    "shell": "cmd",
    "prompt": "payload",
    "text": "payload",
    "file": "path",
    "filepath": "path",
    "q": "query",
    "search": "query",
    "key": "api_key",
    "token": "api_key",  # Used by real tools with api_key parameter (not stubs)
    "filename": "path",
}


class ToolRegistry:
    """Stores tool definitions and provides OpenAI-compatible schemas + execution."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def __len__(self) -> int:
        return len(self._tools)

    # -- registration --------------------------------------------------------

    def register(self, tool: ToolDef) -> None:
        """Add a tool to the registry.

        Args:
            tool: The tool definition to register.
        """
        self._tools[tool.name] = tool

    # -- schema generation ---------------------------------------------------

    def get_openai_schemas(self) -> list[dict[str, Any]]:
        """Return a list of OpenAI function-calling-compatible tool schemas.

        Only the **first** parameter is placed in the ``"required"`` array so
        that optional parameters are not forced by the LLM.
        """
        schemas: list[dict[str, Any]] = []
        for tool in self._tools.values():
            props: dict[str, dict[str, str]] = {}
            required: list[str] = []
            for idx, (pname, pdesc) in enumerate(tool.parameters.items()):
                props[pname] = {"type": "string", "description": pdesc}
                if idx == 0:
                    required.append(pname)
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": {
                            "type": "object",
                            "properties": props,
                            "required": required,
                        },
                    },
                }
            )
        return schemas

    # -- execution -----------------------------------------------------------

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a registered tool with the given arguments.

        Applies parameter alias mapping (LLM may use different names) and
        filters via ``inspect.signature`` so that unexpected parameters never
        reach the underlying function.

        Tool crashes are caught — the Worker loop is never killed by a
        misbehaving tool.

        Args:
            name: The tool name as returned by the LLM.
            arguments: Keyword arguments (may use aliased names).

        Returns:
            The string result of the tool call, or an error string on failure.
        """
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'. Available: {list(self._tools)}"

        # 1. Alias mapping — LLM may call params by different names
        mapped: dict[str, Any] = {}
        for k, v in arguments.items():
            canonical = _PARAM_ALIASES.get(k, k)
            mapped[canonical] = v

        # 2. Filter via *real* function signature (not the registry description)
        try:
            sig = inspect.signature(tool.func)
        except (ValueError, TypeError) as exc:
            return f"Error: cannot inspect signature of tool '{name}': {exc}"

        valid: dict[str, Any] = {}
        for k, v in mapped.items():
            if k in sig.parameters:
                valid[k] = v

        # 3. Call — catch everything so the Worker loop survives
        try:
            return tool.func(**valid)
        except Exception as exc:
            return f"Error: tool '{name}' crashed: {type(exc).__name__}: {exc}"


# ============================================================================
# Worker
# ============================================================================


class Worker:
    """LLM-driven execution loop for a single TaskSpec.

    Uses an OpenAI-compatible client (targeting DeepSeek by default) with
    native function calling.  The loop continues until the LLM returns text
    (parsed as a TaskResult) or the hard tool-call limit is reached.
    Self-decomposition is supported — the Worker can recursively execute
    sub-tasks when a task is too complex for a single pass.
    """

    _SYSTEM_PROMPT_TEMPLATE = """\
You are a Janus Worker — an autonomous AI agent that executes tasks using \
available tools. You are thorough, methodical, and verify your work. You think \
carefully about each step before acting, and you adapt when things don't go \
as expected.

IMPORTANT: When you create or modify files, list them in the "artifacts" field
of your TaskResult JSON. The Reviewer will read these files to verify your work.
You do NOT need to repeat file contents in your "result" field — just summarize
what you did and why. The Reviewer has access to the actual file contents.

## Retry Mode
If your context contains "REVIEW FEEDBACK", you are retrying a failed task.
- Read each issue carefully
- Fix each one
- In your result, explicitly state: "✓ Fixed [issue]: evidence of the fix"
- The Reviewer will check your proof against the original issues"""

    def __init__(
        self,
        model: str,
        api_key: str,
        registry: ToolRegistry,
        max_tool_calls: int = 50,
        max_depth: int = 3,
        reviewer: Optional["Reviewer"] = None,
    ) -> None:
        """Create a Worker.

        Args:
            model: The model name (e.g. ``"deepseek-chat"`` or
                ``"deepseek-v4-flash"``).
            api_key: DeepSeek API key.
            registry: The ``ToolRegistry`` holding available tools.
            max_tool_calls: Hard limit on tool calls per run.
            max_depth: Maximum depth for self-decomposition (default 3).
                Beyond this depth the Worker returns FAILURE instead of
                decomposing further.  Set by the Planner to match its own
                ``max_depth`` configuration.
            reviewer: Optional ``Reviewer`` for auditing sub-Worker output
                during self-decomposition.
        """
        self._model = model
        self._registry = registry
        self._max_tool_calls = max_tool_calls
        self._max_depth = max_depth
        self._reviewer = reviewer

        # Public attribute — set by Gatekeeper for CLI observability.
        # Console is a passive observer; the Worker does NOT require it.
        self.console: Optional["Console"] = None

        # Public attribute — set by Planner to communicate task priority.
        # Used in the system prompt to guide Worker urgency.
        self.priority: str = "normal"

        self._client = OpenAI(
            base_url="https://api.deepseek.com",
            api_key=api_key,
        )

    # -- public API ----------------------------------------------------------

    def run(self, spec: TaskSpec) -> TaskResult:
        """Execute *spec* with self-decomposition support.

        The Worker first attempts to execute the task directly.  If the LLM
        returns ``NEEDS_DECOMPOSITION`` the Worker breaks the task into
        sub-tasks, executes each recursively, and then resumes the original
        task with the sub-results fed back as context.

        Self-decomposition is bounded by ``self._max_depth`` (default
        ``3``).  Only one level of self-decomposition is allowed per task.

        Args:
            spec: The work package to execute.

        Returns:
            A ``TaskResult`` — either the direct result of execution or a
            resumed result after sub-task completion.
        """
        # --- first pass: try to execute directly -----------------------------
        result = self._execute_loop(spec)

        if result.status != TaskStatus.NEEDS_DECOMPOSITION:
            return result

        # --- depth guard -----------------------------------------------------
        if spec.depth >= self._max_depth:
            return TaskResult(
                status=TaskStatus.FAILURE,
                summary=(
                    f"Depth limit reached (depth={spec.depth}, "
                    f"max={self._max_depth}). Cannot self-decompose further."
                ),
                result=(
                    "Worker tried to self-decompose but the depth limit "
                    f"({self._max_depth}) has been reached."
                ),
                confidence=Confidence.LOW,
            )

        # --- self-decompose: execute each sub-task recursively ---------------
        if result.decomposition_request is None or not result.decomposition_request.sub_tasks:
            return TaskResult(
                status=TaskStatus.FAILURE,
                summary="NEEDS_DECOMPOSITION returned without valid decomposition_request.",
                result="decomposition_request is missing or has empty sub_tasks.",
                confidence=Confidence.LOW,
            )

        sub_results: list[TaskResult] = []
        for sub in result.decomposition_request.sub_tasks:
            sub_spec = TaskSpec(
                task_id=f"{spec.task_id}.{sub.id}",
                description=sub.description,
                acceptance_criteria=spec.acceptance_criteria,
                context=(
                    f"{spec.context}\n"
                    f"Parent task: {sub.rationale}"
                ),
                intent=spec.intent,
                goal=spec.goal,
                user_goal=spec.user_goal,
                constraints=spec.constraints,
                depth=spec.depth + 1,
            )
            if not sub_spec.validate():
                logger.warning(
                    "Skipping invalid sub-task spec in Worker.run() — "
                    "task_id=%r description=%r",
                    sub_spec.task_id,
                    sub_spec.description[:50] if sub_spec.description else "",
                )
                sub_results.append(TaskResult(
                    status=TaskStatus.FAILURE,
                    summary=f"Invalid sub-task spec: task_id or description empty.",
                    result=f"Sub-task {sub.id} had an empty task_id or description.",
                    confidence=Confidence.LOW,
                ))
                continue
            sub_result = self.run(sub_spec)  # recursive

            # --- Reviewer audit for sub-Worker output -----------------
            if self._reviewer is not None:
                sub_result = self._review_sub_result(sub_spec, sub_result)

            sub_results.append(sub_result)

        # --- surface sub-Worker review failures in resume context ---------
        review_failures: list[str] = []
        for i, sr in enumerate(sub_results):
            if sr.sub_review_failed:
                review_failures.append(
                    f"⚠️ Sub-task {i + 1} ({sr.worker_id or '?'}) failed review: {sr.summary}"
                )

        # --- resume: feed sub-results back and continue ----------------------
        resume_context = self._format_sub_results(sub_results)
        if review_failures:
            resume_context = (
                f"⚠️ SUB-TASK REVIEW FAILURES DETECTED:\n"
                f"{chr(10).join(review_failures)}\n\n"
                f"--- SUB-TASK RESULTS ---\n"
                f"{resume_context}"
            )

        # --- aggregate all sub-task artifacts for the resume prompt ----------
        all_artifacts: list[str] = []
        for i, sr in enumerate(sub_results):
            if sr.artifacts:
                for artifact in sr.artifacts:
                    all_artifacts.append(f"  - [{i + 1}] {artifact}")
        if all_artifacts:
            resume_context = (
                f"--- SUB-TASK ARTIFACTS (files/resources created) ---\n"
                f"{chr(10).join(all_artifacts)}\n\n"
                f"{resume_context}"
            )

        # --- aggregate sub-worker tool logs for audit transparency ----------
        tool_log_summaries: list[str] = []
        for sub_st in result.decomposition_request.sub_tasks:
            sub_task_id = f"{spec.task_id}.{sub_st.id}"
            log_dir = os.path.join(os.path.dirname(__file__), "..", "tool_logs")
            log_path = os.path.join(log_dir, f"{sub_task_id}.jsonl")
            if os.path.isfile(log_path):
                try:
                    with open(log_path, "r", encoding="utf-8") as lf:
                        lines = lf.readlines()
                    call_count = len(lines)
                    tool_types: set[str] = set()
                    for line in lines:
                        try:
                            entry = json.loads(line)
                            tool_types.add(entry.get("tool_name", "?"))
                        except json.JSONDecodeError:
                            pass
                    tool_log_summaries.append(
                        f"  - [{sub_task_id}] {call_count} tool calls "
                        f"({', '.join(sorted(tool_types))})"
                    )
                except Exception:
                    pass
        if tool_log_summaries:
            resume_context = (
                f"--- [SUB-WORKER TOOL LOGS] ---\n"
                f"{chr(10).join(tool_log_summaries)}\n\n"
                f"{resume_context}"
            )

        # --- extract decomposition feedback from sub-review failures --------
        # FEEDFORWARD-LOOP: detect patterns indicating task decomposition
        # or acceptance-criteria issues, not just Worker execution quality.
        _FEEDBACK_KEYWORDS = [
            "acceptance criteria", "too vague", "too broad", "unclear",
            "ambiguous", "task decomposition", "poorly defined",
            "not specific enough", "scope too large", "criterion",
            "impossible to verify", "unmeasurable",
            "验收标准", "太模糊", "太宽泛", "不明确", "不清晰",
            "任务分解", "分解粒度", "不够具体", "范围太大",
            "无法验证", "不可衡量", "定义不清",
        ]
        decomp_feedback: list[str] = []
        for i, sr in enumerate(sub_results):
            if sr.sub_review_failed or "[SUB-WORKER REVIEW FAILED" in sr.result:
                for kw in _FEEDBACK_KEYWORDS:
                    if kw.lower() in sr.result.lower():
                        decomp_feedback.append(
                            f"  - 子任务 {i + 1}: 审查问题指向任务分解/验收标准 — "
                            f"关键词: {kw}"
                        )
                        break
        if decomp_feedback:
            resume_context = (
                f"⚠️ 前馈闭环：子任务审查发现以下可能源于任务分解的问题，"
                f"请据此调整策略：\n"
                f"{chr(10).join(decomp_feedback)}\n\n"
                f"{resume_context}"
            )

        resume_spec = TaskSpec(
            task_id=spec.task_id,
            description=f"resume: {spec.description}",
            acceptance_criteria=spec.acceptance_criteria,
            context=(
                f"{spec.context}\n\n"
                f"--- SUB-TASK RESULTS ---\n"
                f"{resume_context}"
            ),
            intent=spec.intent,
            goal=spec.goal,
            user_goal=spec.user_goal,
            constraints=spec.constraints,
            depth=spec.depth,
        )
        if not resume_spec.validate():
            logger.warning(
                "Resume spec failed validation in Worker.run() — "
                "task_id=%r description=%r",
                resume_spec.task_id,
                resume_spec.description[:50] if resume_spec.description else "",
            )
            return TaskResult(
                status=TaskStatus.FAILURE,
                summary="Resume spec failed validation: empty task_id or description.",
                result="Could not build a valid resume TaskSpec after self-decomposition.",
                confidence=Confidence.LOW,
            )
        final_result = self._execute_loop(resume_spec)

        # --- guard: only one self-decomposition per task ---------------------
        if final_result.status == TaskStatus.NEEDS_DECOMPOSITION:
            return TaskResult(
                status=TaskStatus.FAILURE,
                summary=(
                    "Worker attempted a second self-decomposition after resume. "
                    "Only one self-decomposition is allowed per task."
                ),
                result=(
                    "The task could not be completed even after decomposition "
                    "and resume.  Sub-task results have been incorporated into "
                    "the context but the LLM still returned NEEDS_DECOMPOSITION."
                ),
                confidence=Confidence.LOW,
            )

        return final_result

    def _execute_loop(self, spec: TaskSpec) -> TaskResult:
        """Run the LLM-driven execution loop for a single *spec*.

        This is the low-level tool-calling loop.  It does NOT handle
        self-decomposition — that is done by :meth:`run`.

        Args:
            spec: The work package to execute.

        Returns:
            A ``TaskResult`` with status, summary, result, artifacts, and
            confidence.
        """
        system_prompt = self._build_system_prompt(spec)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"## Your Task\n{spec.description}\n\n"
                    f"## Acceptance Criteria\n{spec.acceptance_criteria}\n\n"
                    f"## Context\n{spec.context}\n\n"
                    "## Instructions\n"
                    "1. Use the available tools to complete the task. "
                    "Do not simulate — actually call them.\n"
                    "2. Be thorough. Verify your work.\n"
                    "3. When you complete the task, output a JSON object "
                    "following the TaskResult schema:\n\n"
                    "```json\n"
                    "{\n"
                    '  "status": "success" | "failure" | "needs_decomposition",\n'
                    '  "summary": "one sentence describing the outcome",\n'
                    '  "result": "the output produced — a summary of what you did and why. For files, list them in artifacts (the Reviewer reads them directly)",\n'
                    '  "artifacts": ["path_or_identifier"],\n'
                    '  "confidence": "high" | "medium" | "low",\n'
                    '  "decomposition_request": {\n'
                    '    "reason": "why this task needs to be broken down",\n'
                    '    "sub_tasks": [\n'
                    '      {"id": "sub-1", '
                    '"description": "...", '
                    '"rationale": "why this sub-task is needed"}\n'
                    '    ]\n'
                    '  }\n'
                    "}\n"
                    "```\n\n"
                    "4. If the task is too complex, you may return "
                    'status="needs_decomposition" with a decomposition_request. '
                    "The Worker will automatically decompose and execute "
                    "sub-tasks, then feed their results back to you. "
                    "Only use this for genuinely complex tasks that cannot "
                    "be completed in one pass.\n\n"
                    "5. Output ONLY the JSON object (or task-relevant text) "
                    "— no extra commentary.\n\n"
                    "Begin working on the task. Use tools as needed."
                ),
            },
        ]

        schemas = self._registry.get_openai_schemas()

        tool_call_count = 0
        _artifacts_created: list[str] = []  # track files created during execution

        # L3-6: use per-task budget from spec, fall back to worker default
        _budget = getattr(spec, 'max_tool_calls', None) or self._max_tool_calls

        _API_MAX_RETRIES = 3
        _API_RETRY_DELAY = 2.0  # seconds, doubles each retry

        while tool_call_count < _budget:
            api_attempt = 0
            response = None
            last_api_error = None

            while api_attempt <= _API_MAX_RETRIES:
                try:
                    response = self._client.chat.completions.create(
                        model=self._model,
                        messages=messages,
                        tools=schemas if schemas else None,
                        tool_choice="auto" if schemas else None,
                    )
                    break  # success
                except Exception as exc:
                    last_api_error = exc
                    api_attempt += 1
                    if api_attempt <= _API_MAX_RETRIES:
                        delay = _API_RETRY_DELAY * (2 ** (api_attempt - 1))
                        logger.warning(
                            "Worker API call failed (attempt %d/%d), "
                            "retrying in %.1fs: %s",
                            api_attempt, _API_MAX_RETRIES, delay, exc,
                        )
                        import time as _time
                        _time.sleep(delay)
                    else:
                        return TaskResult(
                            status=TaskStatus.FAILURE,
                            summary=(
                                f"LLM API call failed after "
                                f"{_API_MAX_RETRIES} retries: "
                                f"{type(last_api_error).__name__}"
                            ),
                            result=str(last_api_error),
                            confidence=Confidence.LOW,
                        )

            if response is None:
                return TaskResult(
                    status=TaskStatus.FAILURE,
                    summary="LLM API call failed: all retries exhausted.",
                    result=str(last_api_error),
                    confidence=Confidence.LOW,
                )

            if not response.choices:
                logger.warning("Worker LLM returned empty choices for step.")
                return TaskResult(
                    status=TaskStatus.FAILURE,
                    summary="LLM API returned no response choices",
                    result="API returned empty choices array",
                    confidence=Confidence.LOW,
                )

            choice = response.choices[0]
            msg = choice.message

            tool_calls = msg.tool_calls
            content = msg.content

            # -- Branch: LLM returned tool call(s) ---------------------------
            if tool_calls:
                # Build assistant message with reasoning_content (required by
                # DeepSeek thinking mode).
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }

                # DeepSeek thinking mode: reasoning_content MUST be passed back
                if hasattr(msg, "reasoning_content") and msg.reasoning_content:  # type: ignore[union-attr]
                    assistant_msg["reasoning_content"] = msg.reasoning_content  # type: ignore[union-attr]

                messages.append(assistant_msg)

                # Execute each tool call and append results
                for tc in tool_calls:
                    tool_call_count += 1
                    try:
                        arguments = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {}

                    result_text = self._registry.execute(
                        tc.function.name, arguments
                    )

                    # L3-2: deterministic tool-call logging
                    log_tool_call(
                        tool_name=tc.function.name,
                        arguments=arguments,
                        result_text=result_text,
                        task_id=spec.task_id,
                    )

                    # Track artifacts for budget-exhaustion preservation
                    if tc.function.name == "write_file" and "path" in arguments:
                        _artifacts_created.append(arguments["path"])

                    # ── console: tool call summary ────────────────────
                    if self.console:
                        summary = self.console.build_tool_summary(
                            tc.function.name, arguments
                        )
                        self.console.tool_call(tc.function.name, summary)
                        if self.console.is_verbose:
                            self.console.tool_call_verbose(
                                tc.function.name, arguments
                            )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_text,
                        }
                    )

                continue  # back to LLM

            # -- Branch: LLM returned text content → parse as TaskResult -----
            if content:
                return self._parse_result(content)

            # -- Branch: LLM returned neither (unusual but defensive) --------
            break

        # Exhausted tool-call budget without a text result
        budget_note = ""
        if _artifacts_created:
            budget_note = (
                f"\n[BUDGET-EXHAUSTED: {len(_artifacts_created)} file(s) were "
                f"created before exhaustion: {', '.join(_artifacts_created[:5])}"
                f"{'...' if len(_artifacts_created) > 5 else ''}]"
            )
        return TaskResult(
            status=TaskStatus.FAILURE,
            summary="Worker loop exhausted tool-call budget without completing the task.",
            result=(
                f"Reached max_tool_calls={_budget} "
                "without receiving a final text response from the LLM."
                f"{budget_note}"
            ),
            artifacts=_artifacts_created,
            confidence=Confidence.LOW,
        )

    # -- helpers -------------------------------------------------------------

    def _build_system_prompt(self, spec: TaskSpec) -> str:
        """Render the system prompt for a TaskSpec.

        Includes the task's intent (why this task matters) when available,
        so the Worker can make better decisions in unexpected situations
        — inspired by military "commander's intent".

        Also includes the user's original goal and any hard constraints
        so the Worker has full context to make good decisions.

        Priority level adjusts the Worker's thoroughness:
        speed/urgent → favor fast completion, skip non-essential verification;
        quality → be extra thorough, double-check everything.
        """
        prompt = ""
        if spec.user_goal:
            prompt += "## 用户原始输入（不可修改）\n" + spec.user_goal + "\n\n"
        elif spec.goal:
            prompt += "## 用户原始目标 (User's Original Goal)\n" + spec.goal + "\n\n"
        prompt += self._SYSTEM_PROMPT_TEMPLATE
        if spec.goal:
            prompt += "\n\n## Overall Goal\n" + spec.goal
        if spec.intent:
            prompt += f"\n\nThis task is important because: {spec.intent}"
        if spec.constraints:
            prompt += f"\n\n## Hard Constraints (MUST follow)\n{spec.constraints}"

        # Priority-driven guidance
        _priority_guidance = {
            "speed": "\n\nURGENT: Work at maximum speed. Skip non-essential checks. Fast iteration over perfection. Fail fast if blocked.",
            "urgent": "\n\nURGENT: Work at maximum speed. Skip non-essential checks. Fast iteration over perfection. Fail fast if blocked.",
            "quality": "\n\nQUALITY: Be extremely thorough. Double-check every output. Verify edge cases. Take the time needed to get it right.",
            "balanced": "\n\nBALANCED: Work steadily and verify key outputs. Balance speed with quality — check important things, skip cosmetic polish.",
            "normal": "\n\nBALANCED: Work steadily and verify key outputs. Balance speed with quality — check important things, skip cosmetic polish.",
        }
        guidance = _priority_guidance.get(self.priority, "")
        if guidance:
            prompt += guidance

        return prompt

    @staticmethod
    def _format_sub_results(sub_results: list[TaskResult]) -> str:
        """Format a list of sub-task results as a context string.

        Used to feed sub-task outcomes back into the resume prompt so the
        LLM can synthesize them into its final answer.

        Args:
            sub_results: The results returned by each sub-task execution.

        Returns:
            A formatted string suitable for inclusion in a prompt context.
        """
        parts: list[str] = []
        for i, sr in enumerate(sub_results, 1):
            parts.append(
                f"[{i}] {sr.status.value}: {sr.summary}\n"
                f"    Result: {sr.result}\n"
                f"    Confidence: {sr.confidence.value}"
            )
            if sr.artifacts:
                parts[-1] += f"\n    Artifacts: {', '.join(sr.artifacts)}"
        return "\n\n".join(parts)

    def _review_sub_result(
        self, spec: TaskSpec, result: TaskResult, max_retries: int = 2
    ) -> TaskResult:
        """Review a sub-Worker result and retry with graded feedback.

        Uses the same graded retry logic as
        ``Gatekeeper._dispatch_with_review``: the verdict and issue
        severities determine how aggressively to retry.

        Args:
            spec: The sub-task specification used for the execution.
            result: The result produced by the sub-Worker.
            max_retries: Maximum number of retries after a failed review
                (default 2, meaning up to 3 total attempts).

        Returns:
            The approved result (original or retried), or the last failed
            result if all retries are exhausted.
        """
        assert self._reviewer is not None, \
            "_review_sub_result called but no reviewer is configured"

        issue_count = max_retries + 1
        last_review = None
        retry_count = 0  # track how many retries were attempted

        for attempt in range(issue_count):
            review = self._reviewer.review(spec, result)
            last_review = review

            # Apply shared HARD/SOFT criteria calibration (same as Planner)
            calibrate_and_adjust(review, spec.acceptance_criteria)

            # ── APPROVED / APPROVED_WITH_NOTES → accept ──────────────
            if review.verdict in (
                ReviewVerdict.APPROVED,
                ReviewVerdict.APPROVED_WITH_NOTES,
            ):
                if retry_count > 0:
                    # Mark that this passed only after retries
                    result.result = (
                        f"{result.result}\n\n"
                        f"[⚠️ Passed after {retry_count} sub-review "
                        f"retrie(s)]"
                    )
                # ── console: sub-review result ──────────────────────
                if self.console:
                    self.console.task_done(
                        spec.task_id,
                        result.status.value,
                        0.0,  # sub-review doesn't track individual timing
                    )
                return result

            # ── Build feedback ───────────────────────────────────────
            feedback_parts = [review.summary]
            for issue in review.blocking_issues:
                feedback_parts.append(
                    f"[{issue.severity.value}] {issue.description}"
                )
            feedback = "\n".join(feedback_parts)

            # ── MINOR_REVISIONS → retry once, auto-accept ────────────
            if review.verdict == ReviewVerdict.MINOR_REVISIONS:
                if attempt == 0:
                    retry_count += 1
                    spec = TaskSpec(
                        task_id=spec.task_id,
                        description=spec.description,
                        acceptance_criteria=spec.acceptance_criteria,
                        context=(
                            f"{spec.context}\n\n"
                            f"--- REVIEW FEEDBACK (attempt {attempt + 1}) ---\n"
                            f"Verdict: {review.verdict.value}\n"
                            f"The following issues were found. You MUST fix "
                            f"EACH one and include PROOF:\n\n"
                            f"Issues to fix:\n"
                            f"{feedback}\n\n"
                            f"For each issue you fix, include in your "
                            f"result: '✓ Fixed [issue]: what you changed "
                            f"and why it now satisfies the requirement.'"
                        ),
                        intent=spec.intent,
                        goal=spec.goal,
                        user_goal=spec.user_goal,
                        constraints=spec.constraints,
                        depth=spec.depth,
                    )
                    if not spec.validate():
                        logger.warning(
                            "Sub-review MINOR_REVISIONS retry spec "
                            "failed validation — task_id=%r. "
                            "Skipping retry, auto-accepting.",
                            spec.task_id,
                        )
                        result.result = (
                            f"{result.result}\n\n"
                            f"[⚠️ Auto-accepted — retry spec validation failed]"
                        )
                        return result
                    result = self.run(spec)
                    continue
                else:
                    # Auto-accept after retry — mark so caller knows
                    result.result = (
                        f"{result.result}\n\n"
                        f"[⚠️ Auto-accepted after {retry_count} sub-review "
                        f"retrie(s) — minor revisions]"
                    )
                    return result

            # ── MAJOR_REVISIONS / REJECTED → retry with full review ──
            if attempt < max_retries:
                retry_count += 1
                spec = TaskSpec(
                    task_id=spec.task_id,
                    description=spec.description,
                    acceptance_criteria=spec.acceptance_criteria,
                    context=(
                        f"{spec.context}\n\n"
                        f"--- REVIEW FEEDBACK (attempt {attempt + 1}) ---\n"
                        f"Verdict: {review.verdict.value}\n"
                        f"The following issues were found. You MUST fix "
                        f"EACH one and include PROOF:\n\n"
                        f"Issues to fix:\n"
                        f"{feedback}\n\n"
                        f"For each issue you fix, include in your "
                        f"result: '✓ Fixed [issue]: what you changed "
                        f"and why it now satisfies the requirement.'"
                    ),
                    intent=spec.intent,
                    goal=spec.goal,
                    user_goal=spec.user_goal,
                    constraints=spec.constraints,
                    depth=spec.depth,
                )
                if not spec.validate():
                    logger.warning(
                        "Sub-review MAJOR_REVISIONS/REJECTED retry spec "
                        "failed validation — task_id=%r. Skipping retry.",
                        spec.task_id,
                    )
                    # Annotate result and let it fall through to exhausted-retries
                    result.result = (
                        f"{result.result}\n\n"
                        f"[⚠️ Retry spec validation failed — "
                        f"skipping retry attempt {attempt + 1}]"
                    )
                    continue
                result = self.run(spec)

        # All retries exhausted — annotate result with review failure details
        issues_text = "; ".join(
            f"[{i.severity.value}] {i.description}"
            for i in (last_review.issues if last_review else [])
        )
        review_summary = last_review.summary if last_review else "Unknown"
        logger.warning(
            "Sub-task review for %r FAILED after %d attempts. Verdict: %s",
            spec.task_id, issue_count, review_summary,
        )
        if self.console:
            self.console.review_fail(
                spec.task_id,
                [i.description for i in (last_review.issues if last_review else [])],
                max_retries,
            )
        # Build a clear error that the Planner can detect
        error_detail = (
            f"[SUB-WORKER REVIEW FAILED after {issue_count} attempts]\n"
            f"Verdict: {review_summary}\n"
            f"Issues: {issues_text or '(none recorded)'}"
        )
        return TaskResult(
            status=TaskStatus.FAILURE,
            summary=(
                f"Sub-task review exhausted after {issue_count} attempts: "
                f"{review_summary}"
            ),
            result=(
                f"{result.result}\n\n"
                f"{error_detail}"
            ),
            artifacts=result.artifacts,
            confidence=Confidence.LOW,
            sub_review_failed=True,
        )

    @staticmethod
    def _parse_result(text: str) -> TaskResult:
        """Parse the LLM's final text into a TaskResult.

        Delegates to the shared ``extract_json`` (from ``prompts.py``) for
        robust JSON extraction — handles ```json fences, nested braces,
        and raw objects in a single implementation shared across Gatekeeper,
        Planner, Worker, and Reviewer.

        If JSON parsing fails the raw text is returned as a ``FAILURE``
        result so information is not lost.
        """
        parsed = extract_json(text)

        if isinstance(parsed, dict):
            try:
                result = TaskResult.from_dict(parsed)
                # Defensive: LLM may return nested JSON where "result" is a
                # dict/list instead of a string.  Normalize to str so callers
                # (Planner._desk_check, Reviewer, etc.) never get a type error.
                if not isinstance(result.result, str):
                    result.result = json.dumps(
                        result.result, ensure_ascii=False
                    )
                if not result.validate():
                    return TaskResult(
                        status=TaskStatus.FAILURE,
                        summary="Invalid TaskResult: NEEDS_DECOMPOSITION missing decomposition_request.",
                        result=f"LLM returned status={parsed.get('status')} but validate() failed.",
                        confidence=Confidence.LOW,
                    )
                return result
            except (KeyError, TypeError, ValueError):
                pass  # fall through to failure path

        # Could not parse → wrap raw text as a failure
        return TaskResult(
            status=TaskStatus.FAILURE,
            summary="Could not parse LLM output as valid TaskResult JSON.",
            result=text.strip() or "(empty response)",
            confidence=Confidence.LOW,
        )


# ============================================================================
# Default stub registry for testing
# ============================================================================


def _real_read_file(path: str, offset: int = 1, limit: int = 50000) -> str:
    """Actually read a file from disk, with paging support for large files.

    Args:
        path: Absolute or relative path to the file.
        offset: 1-indexed character position to start reading from (default: 1).
        limit: Maximum number of characters to read (default: 50000).

    Returns:
        The file content (or a slice), with a continuation hint if the
        content was truncated.

    Examples:
        _real_read_file("large.py")             # read first 50000 chars
        _real_read_file("large.py", offset=1, limit=10000)   # first 10k chars
        _real_read_file("large.py", offset=50001, limit=50000)  # next 50k chars
    """
    # Type guard: LLM may pass strings for offset/limit
    offset = int(offset) if isinstance(offset, str) else offset
    limit = int(limit) if isinstance(limit, str) else limit
    try:
        # Read the entire file as text, then slice at character position.
        # Using f.seek() on text mode is not portable (undefined for non-zero
        # offsets in Python 3's TextIOBase).  Binary seek + decode would
        # misalign on multi-byte UTF-8 character boundaries.
        #
        # For typical agent workloads (code, configs, logs) this is fine.
        # If the file is too large to fit in memory, the Worker will get an
        # error instead of a silent truncation.
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            full = f.read()
        # Slice at character position (1-indexed)
        start = max(0, offset - 1)
        content = full[start:start + limit]
        # If we sliced exactly `limit` chars and there's more content
        if len(content) >= limit and start + limit < len(full):
            next_offset = start + limit + 1  # 1-indexed
            content += (
                f"\n...[truncated at {limit} chars — "
                f"call read_file with offset={next_offset} to continue]"
            )
        return content
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except PermissionError:
        return f"Error: Permission denied: {path}"
    except Exception as e:
        return f"Error reading {path}: {e}"


def _real_write_file(path: str, content: str) -> str:
    """Actually write content to a file, creating parent directories."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error writing to {path}: {e}"


def _real_terminal(command: str) -> str:
    """Execute a shell command via subprocess. 60-second timeout.

    Commands are tokenized with ``shlex.split()`` and executed with
    ``shell=False`` to prevent command injection vulnerabilities.  The LLM
    generates a raw command string; we split it safely into argv form.

    For pipes and redirects the Worker must chain tool calls instead, since
    ``shell=False`` does not interpret shell operators.
    """
    # Validate: reject null bytes and other injection vectors
    if "\x00" in command:
        return "Error: command contains null bytes (rejected)."
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return f"Error: invalid command syntax — {exc}"
    if not argv:
        return "Error: empty command."
    try:
        result = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 60 seconds."
    except Exception as e:
        return f"Error running command: {e}"
    out = result.stdout
    if result.stderr:
        out += f"\n[stderr]\n{result.stderr}"
    if result.returncode != 0:
        out += f"\n[exit code: {result.returncode}]"
    return out.strip() or "(no output)"


def _real_web_search(query: str) -> str:
    """Search the web using DuckDuckGo (free, no API key required).

    Uses the ``ddgs`` package which wraps DuckDuckGo's HTML search.
    Returns a JSON string with search result titles, URLs, and descriptions.
    """
    import concurrent.futures
    import json
    import logging
    logger = logging.getLogger(__name__)

    def _do_search():
        from ddgs import DDGS
        results = []
        with DDGS(timeout=10) as client:
            for i, hit in enumerate(client.text(query, max_results=5)):
                results.append({
                    "title": str(hit.get("title", "")),
                    "url": str(hit.get("href", "") or hit.get("url", "")),
                    "description": str(hit.get("body", "")),
                    "position": i + 1,
                })
        return results

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_do_search)
            results = future.result(timeout=30)
    except ImportError:
        return json.dumps({
            "error": "ddgs package not installed. Run: pip install ddgs",
            "success": False
        })
    except concurrent.futures.TimeoutError:
        return json.dumps({
            "error": "Search timed out after 30s",
            "success": False
        })
    except Exception as exc:
        return json.dumps({
            "error": f"Search failed: {exc}",
            "success": False
        })

    return json.dumps({
        "success": True,
        "data": {"web": results}
    }, ensure_ascii=False)


def _real_web_extract(urls: str) -> str:
    """Fetch and extract text content from one or more URLs.

    ``urls`` is a JSON array string, e.g. ``'["https://example.com"]'``.
    HTML tags are stripped with a rough regex; each URL is capped at 3000 chars.
    Errors per-URL are reported inline.
    """
    try:
        url_list: list[str] = json.loads(urls)
    except json.JSONDecodeError:
        # LLMs often pass a plain URL string instead of a JSON array.
        # Be forgiving — accept a single URL string directly.
        if urls.strip().startswith(("http://", "https://")):
            url_list = [urls.strip()]
        else:
            return (
                "Error: urls must be a JSON array of strings (e.g. "
                "'[\"https://example.com\"]') or a plain http/https URL."
            )

    if not isinstance(url_list, list):
        return "Error: urls must be a JSON array of strings."

    results: list[str] = []
    strip_tags = re.compile(r"<[^>]+>")

    for url in url_list[:5]:  # safety limit: max 5 URLs
        try:
            # SSRF guard: only allow http and https schemes.
            # Prevents LLM-crafted file:///etc/passwd or other schemes
            # from reading local files or hitting internal services.
            parsed_url = urllib.parse.urlparse(url)
            if parsed_url.scheme not in ("http", "https"):
                results.append(
                    f"=== {url} ===\n"
                    f"Error: URL scheme '{parsed_url.scheme}' is not allowed. "
                    f"Only http:// and https:// URLs are supported."
                )
                continue
            # Reject private/reserved IP ranges (SSRF defense-in-depth).
            if parsed_url.hostname:
                hostname = parsed_url.hostname.lower()
                if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
                    results.append(
                        f"=== {url} ===\n"
                        f"Error: URL targets localhost ({hostname}) — rejected for security."
                    )
                    continue
                # Reject metadata cloud endpoints
                if hostname == "169.254.169.254":
                    results.append(
                        f"=== {url} ===\n"
                        f"Error: URL targets cloud metadata endpoint — rejected for security."
                    )
                    continue
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "JanusWorker/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", errors="replace")

            # Rough HTML → text
            text = strip_tags.sub(" ", body)
            # Collapse whitespace
            text = re.sub(r"\s+", " ", text).strip()

            if len(text) > 3000:
                text = text[:3000] + "...[truncated]"

            results.append(f"=== {url} ===\n{text}")
        except urllib.error.URLError as exc:
            results.append(f"=== {url} ===\nError: {exc}")
        except Exception as exc:
            results.append(f"=== {url} ===\nError: {exc}")

    return "\n\n".join(results)


def _real_search_files(pattern: str) -> str:
    """Search for files by name and content in the current working directory.

    Returns up to 50 matching file paths.  For content matches, line numbers
    are included.
    """
    cwd = Path.cwd()
    results: list[str] = []

    # 1. File-name matches via glob
    try:
        name_matches = sorted(cwd.rglob(pattern))
    except Exception:
        name_matches = []

    for p in name_matches[:50]:
        if p.is_file():
            results.append(str(p))

    # 2. Content matches (search inside text files)
    # Strip glob wildcards (*, ?, []) from the pattern to produce a reasonable
    # content search term.  A glob like "*.py" becomes ".py"; "test_*.py" becomes
    # "test_.py".  If nothing remains after stripping, skip content search.
    content_pattern = pattern.replace("*", "").replace("?", "")
    # Also strip character-class brackets
    content_pattern = re.sub(r"\[.*?\]", "", content_pattern)
    if len(results) < 50 and content_pattern.strip():
        # Only search text files — skip binaries by extension heuristic
        text_exts = {".txt", ".py", ".md", ".json", ".yaml", ".yml",
                     ".toml", ".cfg", ".ini", ".html", ".css", ".js",
                     ".ts", ".rs", ".go", ".java", ".c", ".cpp", ".h",
                     ".sh", ".bat", ".log", ".xml", ".csv", ".sql"}
        slots_left = 50 - len(results)
        seen = set(results)

        for root, _dirs, files in os.walk(str(cwd)):
            if slots_left <= 0:
                break
            for fname in files:
                if slots_left <= 0:
                    break
                ext = os.path.splitext(fname)[1].lower()
                if ext not in text_exts:
                    continue
                filepath = os.path.join(root, fname)
                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        for lineno, line in enumerate(f, 1):
                            if content_pattern in line:
                                key = f"{filepath}:{lineno}"
                                if key not in seen:
                                    results.append(key)
                                    seen.add(key)
                                    slots_left -= 1
                                    if slots_left <= 0:
                                        break
                except (OSError, UnicodeDecodeError):
                    pass

    if not results:
        return f"No files matching '{pattern}' found."
    return "\n".join(results[:50])


def _real_patch(path: str, old_string: str, new_string: str) -> str:
    """Replace the first occurrence of ``old_string`` in a file with ``new_string``.

    Returns a confirmation with a preview of the change.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except PermissionError:
        return f"Error: Permission denied: {path}"
    except Exception as exc:
        return f"Error reading {path}: {exc}"

    if old_string not in content:
        return f"Error: old_string not found in {path}"

    new_content = content.replace(old_string, new_string, 1)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as exc:
        return f"Error writing {path}: {exc}"

    # Preview: show the changed region with a few lines of context
    idx = content.find(old_string)
    before = content[max(0, idx - 80):idx]
    after = content[idx + len(old_string):idx + len(old_string) + 80]
    preview_old = f"...{before}{old_string}{after}..."
    preview_new = f"...{before}{new_string}{after}..."

    return (
        f"Successfully patched {path}.\n"
        f"--- old:\n{preview_old}\n"
        f"--- new:\n{preview_new}"
    )


def _real_execute_code(code: str) -> str:
    """Execute the given Python code string and return captured stdout.

    Execution uses a restricted namespace with safe builtins only.
    ``__import__``, ``exec``, ``eval``, ``compile``, ``open``, and ``input``
    are excluded to prevent sandbox escape.  All exceptions are caught and
    reported.

    Security: ``exec()`` is inherently dangerous with LLM-generated code.
    This sandbox is a best-effort guard — for untrusted environments, run
    the Worker inside a container or use a subprocess with resource limits.
    """
    import builtins as _safe_builtins

    # Build a restricted builtins dict: only safe, side-effect-free functions.
    # Include __import__ so execute_code can load stdlib modules (json, re, math, etc.).
    # Exclude exec, eval, compile, open, and input for security.
    _ALLOWED_BUILTINS = {
        "abs", "all", "any", "ascii", "bin", "bool", "bytes", "callable",
        "chr", "complex", "dict", "divmod", "enumerate", "filter", "float",
        "format", "frozenset", "getattr", "hasattr", "hash", "hex", "int",
        "isinstance", "issubclass", "iter", "len", "list", "map", "max",
        "min", "next", "object", "oct", "ord", "pow", "print", "range",
        "repr", "reversed", "round", "set", "slice", "sorted", "str",
        "sum", "tuple", "type", "zip",
        # Allow imports of stdlib modules (json, re, math, etc.)
        "__import__",
        # Allow common exceptions/types
        "compile",
        "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
        "AttributeError", "RuntimeError", "StopIteration", "ImportError",
        "OSError", "FileNotFoundError", "PermissionError", "ZeroDivisionError",
        "True", "False", "None", "NotImplemented", "Ellipsis",
    }
    safe_builtins = {
        k: getattr(_safe_builtins, k)
        for k in _ALLOWED_BUILTINS
        if hasattr(_safe_builtins, k)
    }

    old_stdout = sys.stdout
    captured = io.StringIO()
    sys.stdout = captured

    # Safe import whitelist — only allow stdlib data-processing modules.
    # Blocks os, subprocess, sys, shutil, and other dangerous modules.
    _SAFE_MODULES = frozenset({
        "json", "re", "math", "cmath", "statistics",
        "collections", "itertools", "functools", "operator",
        "datetime", "time", "calendar",
        "textwrap", "string", "unicodedata",
        "csv", "io", "pathlib",
        "dataclasses", "enum", "typing",
        "copy", "pprint", "decimal", "fractions",
        "random", "hashlib", "base64", "binascii",
        "html", "xml.etree.ElementTree", "urllib.parse",
        "struct", "array",
    })

    def _safe_import(name, *args, **kwargs):
        if name not in _SAFE_MODULES:
            raise ImportError(
                f"Module '{name}' is not in the allowed import list. "
                f"Allowed: {sorted(_SAFE_MODULES)}"
            )
        return __import__(name, *args, **kwargs)

    exec_namespace = {
        "__builtins__": {**safe_builtins, "__import__": _safe_import}
    }

    try:
        exec(code, exec_namespace)
        output = captured.getvalue()
        result = output if output else "(no output)"
        return (
            f"{result}\n\n"
            f"[REVIEW REQUIRED: You executed code above. "
            f"Verify the output is correct before using it in your final result. "
            f"Do NOT blindly trust code execution output — it is your "
            f"responsibility to validate it against acceptance criteria.]"
        )
    except Exception as exc:
        err_output = captured.getvalue()
        return f"Error: {type(exc).__name__}: {exc}" + (
            f"\nstdout before error:\n{err_output}" if err_output else ""
        )
    finally:
        sys.stdout = old_stdout


def _real_browser_navigate(url: str) -> str:
    import logging
    logger = logging.getLogger(__name__)
    logger.info("Browser navigate to: %s", url)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return json.dumps({
            "error": "Playwright not installed. Run: pip install playwright && playwright install chromium",
            "success": False
        })

    # Use Hermes's bundled Chromium (no separate download needed).
    # Falls back to Playwright's own bundled Chromium if the Hermes path is missing.
    hermes_chrome = os.path.join(
        os.path.expanduser("~"),
        "AppData", "Local", "hermes", "chrome", "chrome-win64", "chrome.exe"
    )
    launch_kw: dict[str, Any] = {"headless": True}
    if os.path.isfile(hermes_chrome):
        launch_kw["executable_path"] = hermes_chrome

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch_kw)
            page = browser.new_page()
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            content = page.inner_text("body")
            title = page.title()
            browser.close()

        if len(content) > 5000:
            content = content[:5000] + "\n...[truncated to 5000 chars]"

        logger.info("Browser navigate success: %s (%d chars)", title, len(content))
        return json.dumps({
            "success": True,
            "url": url,
            "title": title,
            "content": content,
        }, ensure_ascii=False)

    except Exception as exc:
        logger.error("Browser navigation failed: %s", exc)
        return json.dumps({
            "error": f"Browser navigation failed: {exc}",
            "success": False
        })


def create_default_registry() -> ToolRegistry:
    """Create a ``ToolRegistry`` pre-populated with real tools.

    read_file, write_file, and terminal perform actual I/O and shell execution.
    web_extract performs real HTTP fetching with HTML-to-text extraction.
    """
    registry = ToolRegistry()
    registry.register(
        ToolDef(
            name="read_file",
            description="Read the contents of a file at the given path. "
                        "Returns up to 50000 characters per call. "
                        "For larger files, use offset/limit to read in chunks.",
            parameters={
                "path": "Absolute or relative path to the file.",
                "offset": "Optional. 1-indexed character position to start reading from (default: 1).",
                "limit": "Optional. Max characters to read (default: 50000, max: 200000).",
            },
            func=_real_read_file,
        )
    )
    registry.register(
        ToolDef(
            name="write_file",
            description="Write content to a file at the given path. "
                        "Creates parent directories if they do not exist.",
            parameters={
                "path": "Absolute or relative path to the file.",
                "content": "Text content to write.",
            },
            func=_real_write_file,
        )
    )
    registry.register(
        ToolDef(
            name="terminal",
            description="Execute a shell command and return its output. "
                        "60-second timeout; both stdout and stderr are captured.",
            parameters={"command": "The shell command to execute."},
            func=_real_terminal,
        )
    )
    registry.register(
        ToolDef(
            name="web_extract",
            description="Fetch and extract text content from web page URLs. "
                        "Accepts a JSON array string of URLs (e.g. '[\"https://example.com\"]') "
                        "or a plain http/https URL string. "
                        "Returns extracted text (HTML stripped), 3000 char limit per URL. "
                        "Errors are reported per URL inline.",
            parameters={"urls": "JSON array string of URLs to extract, e.g. '[\"https://example.com\"]'."},
            func=_real_web_extract,
        )
    )
    registry.register(
        ToolDef(
            name="search_files",
            description="Search the current working directory for files matching a pattern "
                        "by name (glob) and by content (inside text files). "
                        "Returns up to 50 matching file paths with line numbers for content hits.",
            parameters={"pattern": "Glob pattern for file names, also searched as substring in file contents."},
            func=_real_search_files,
        )
    )
    registry.register(
        ToolDef(
            name="patch",
            description="Replace the first occurrence of a string in a file. "
                        "Reads the file, replaces old_string with new_string, writes back. "
                        "Returns a preview of the change.",
            parameters={
                "path": "Absolute or relative path to the file to patch.",
                "old_string": "The exact string to find and replace (first occurrence only).",
                "new_string": "The replacement string.",
            },
            func=_real_patch,
        )
    )
    registry.register(
        ToolDef(
            name="execute_code",
            description="Execute a Python code string in a restricted namespace (builtins only). "
                        "Captures stdout and returns it. All exceptions are caught and reported.",
            parameters={"code": "The Python code string to execute."},
            func=_real_execute_code,
        )
    )
    registry.register(
        ToolDef(
            name="web_search",
            description="Search the web using DuckDuckGo (free, no API key). "
                        "Returns search results with titles, URLs, and descriptions.",
            parameters={"query": "The search query string."},
            func=_real_web_search,
        )
    )
    registry.register(
        ToolDef(
            name="browser_navigate",
            description="Navigate to a URL using headless Chromium and return the page text content. "
                        "Requires: playwright install chromium.",
            parameters={"url": "The URL to navigate to."},
            func=_real_browser_navigate,
        )
    )
    return registry
