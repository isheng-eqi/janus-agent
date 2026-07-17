"""
Janus Gatekeeper — strategic decision layer.  Delegates execution to Planner.

The Gatekeeper has ZERO tools.  It cannot read files, write files, run
commands, or search the web.  It ONLY calls the LLM to think — for
decision-making, directive formulation, and user-facing responses.

Role in the Janus architecture:
  Gatekeeper → decide (chat vs task) → formulate Directive → Planner → ExecutionReport → respond to user

  Gatekeeper is the "strategist" — it understands user intent, sets direction,
  and reports results.  The Planner handles all tactical execution.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, TYPE_CHECKING

from .console import _qing, _zhu, _jin, _nongmo, _danmo
from .prompts import context_discipline_prompt, extract_json
from .protocol import Directive, ExecutionReport

if TYPE_CHECKING:
    from .console import Console
    from .planner import Planner

# ---------------------------------------------------------------------------
# OpenAI client — lazy import with a helpful error message
# ---------------------------------------------------------------------------
try:
    from openai import OpenAI  # type: ignore[import-untyped]
except ImportError as exc:
    raise ImportError(
        "The `openai` package is required by Janus Gatekeeper. "
        "Install it with: pip install openai"
    ) from exc

logger = logging.getLogger(__name__)


# ============================================================================
# Gatekeeper
# ============================================================================


class Gatekeeper:
    """LLM-driven strategic decision layer.  Delegates execution to Planner.

    The Gatekeeper receives a user message, decides whether it's a chat or
    task, then either responds conversationally or formulates a Directive
    for the Planner and reports the ExecutionReport back to the user.

    All tactical execution — decomposition, Worker dispatch, review loops,
    retry management — is handled by the Planner.  The Gatekeeper only
    sees Directive (input) and ExecutionReport (output).

    Usage::

        planner = Planner(...)
        gk = Gatekeeper(
            model="deepseek-v4-pro",
            api_key=os.environ["DEEPSEEK_API_KEY"],
            planner=planner,
            console=console,
        )
        answer = gk.handle("Write a hello-world script in Python.")
        print(answer)
    """

    # -- system prompts -------------------------------------------------------

    # -- context-discipline prompt (shared with Planner via prompts.py) ---------
    # Called as a function in the code below: context_discipline_prompt(...)

    _DECIDE_SYSTEM_PROMPT = """\
You are Janus Gatekeeper. Your role is to analyze user messages and decide the best action path.
You think strategically about what the user truly needs — a simple conversation or a complex task requiring decomposition."""

    _CHAT_SYSTEM_PROMPT = """\
You are a helpful, friendly AI assistant. You converse naturally with users \
in Chinese, engaging in genuine conversation and providing thoughtful responses."""

    _GATEKEEPER_IDENTITY = """你是 Janus 系统的战略决策层。判断用户意图（闲聊或任务），制定方向交给 Planner 执行。你没有任何工具，只看摘要不读执行细节。用中文自然对话，不要角色扮演。"""

    _FORMULATE_SYSTEM_PROMPT = """\
You are a Janus Gatekeeper — a strategic decision-maker. Your job is to \
translate a user's goal into a concise strategic directive for your planning \
team. Extract the core intent, identify constraints, and set priority. \
Output ONLY valid JSON."""

    # -- constructor ----------------------------------------------------------

    def __init__(
        self,
        model: str,
        api_key: str,
        planner: Planner,
        console: Optional[Console] = None,
    ) -> None:
        """Create a Gatekeeper.

        Args:
            model: DeepSeek model name (e.g. ``"deepseek-v4-pro"``) for
                gatekeeper decision-making and directive formulation.
            api_key: DeepSeek API key.
            planner: The ``Planner`` instance that handles tactical execution
                (decomposition, Worker dispatch, review, retry).
            console: Optional ``Console`` for CLI observability output.
        """
        self._model = model
        self._planner = planner
        self._console = console

        self._client = OpenAI(
            base_url="https://api.deepseek.com",
            api_key=api_key,
        )

    # -- public API: unified handle() -----------------------------------------

    def handle(self, message: str, history_context: str = "") -> str:
        """Gatekeeper decides: is this a task or conversation?

        Single entry point for all user input.  The Gatekeeper calls the LLM
        to decide whether *message* is a TASK (needs Planner dispatch) or
        CHAT (simple conversation), then routes accordingly.

        Args:
            message: The user's raw input string.
            history_context: Optional formatted recent conversation history
                from Session, for context-aware decisions.

        Returns:
            The Gatekeeper's response — either a conversational reply or
            a task execution summary.
        """
        decision = self._decide(message, history_context)
        action = decision.get("action", "task")

        if action == "chat":
            return self._respond(message, history_context)
        else:
            return self._execute_via_planner(message, history_context)

    # -- backward-compat public API -------------------------------------------

    def execute(self, goal: str) -> str:
        """Run the full Gatekeeper → Planner pipeline on *goal* (backward compat).

        Delegates to ``_execute_via_planner``.
        Prefer ``handle()`` for new code.

        Args:
            goal: The user's free-text goal.

        Returns:
            A summary string from the Planner's ExecutionReport.
        """
        return self._execute_via_planner(goal)

    def chat(self, message: str) -> str:
        """Simple conversation — no Planner dispatch (backward compat).

        Delegates to ``_respond``.
        Prefer ``handle()`` for new code.

        Args:
            message: The user's chat message.

        Returns:
            The LLM's text response, or an error string on API failure.
        """
        return self._respond(message)

    # -- internal: decision ---------------------------------------------------

    def _decide(self, message: str, history_context: str = "") -> dict[str, str]:
        """Call the LLM to decide whether *message* is a task or chat.

        Returns a dict with keys ``action`` (``"chat"`` or ``"task"``) and
        ``reason``.  Falls back to ``{"action": "chat"}`` on any error —
        a failed decision API call means we should give the user a simple
        error message, not cascade into more API calls through the task path.

        When *history_context* is provided, it is included in the prompt
        so the decision is informed by recent conversation.
        """
        history_block = ""
        if history_context:
            history_block = f"{history_context}\n\n"

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._GATEKEEPER_IDENTITY},
            {"role": "system", "content": context_discipline_prompt(
                "the top-level decision maker, like an executive talking to their assistant",
                "direction and decisions, not implementation",
            )},
            {"role": "system", "content": self._DECIDE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"{history_block}"
                    'Decide whether the following user message is a TASK '
                    '(needs Planner execution) or CHAT '
                    '(simple conversation).\n\n'
                    'Output ONLY valid JSON with this exact format:\n'
                    '{"action": "chat"|"task", "reason": "brief explanation"}\n\n'
                    f'User message: {message}'
                ),
            },
        ]

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        except Exception as exc:
            logger.exception("Decision API call failed, defaulting to task.")
            return {"action": "task", "reason": f"API error: {type(exc).__name__}"}

        if not response.choices:
            logger.warning("Decision API returned empty choices, defaulting to task.")
            return {"action": "task", "reason": "API returned no choices"}

        choice = response.choices[0]
        content = choice.message.content or ""

        parsed = extract_json(content)
        if isinstance(parsed, dict) and "action" in parsed:
            return {
                "action": str(parsed.get("action", "task")),
                "reason": str(parsed.get("reason", "")),
            }

        logger.warning(
            "Decision returned unexpected shape, defaulting to task: %r",
            content[:200],
        )
        return {"action": "task", "reason": "unparseable decision"}

    # -- internal: respond (chat) ---------------------------------------------

    def _respond(self, message: str, history_context: str = "") -> str:
        """Simple LLM reply — no Planner, no tools.

        Uses a lightweight system prompt and returns the LLM's text response
        directly.  Intended for greetings, small talk, and conversational
        questions that should not go through the full task pipeline.

        When *history_context* is provided, it is prepended to the user
        message so the chat response can reference recent conversation.

        Args:
            message: The user's chat message.
            history_context: Optional formatted recent conversation history.

        Returns:
            The LLM's text response, or an error string on API failure.
        """
        history_block = ""
        if history_context:
            history_block = f"{history_context}\n\n"

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self._GATEKEEPER_IDENTITY + "\n\n" + self._CHAT_SYSTEM_PROMPT,
                # NOTE: context_discipline_prompt is intentionally OMITTED from
                # _respond().  This method is for simple chat (greetings, small
                # talk), not task execution.  The discipline prompt tells the LLM
                # to aggressively summarise and discard context — exactly the
                # opposite of what a natural conversation needs.  Chat should feel
                # human and reference earlier context freely; task execution (in
                # _decide / _execute_via_planner / _plan) gets the discipline
                # prompt because those paths must conserve context-window budget.
            },
            {
                "role": "user",
                "content": (
                    f"{history_block}"
                    f"{message}\n\n"
                    "(Respond naturally in Chinese. Do NOT output JSON, code "
                    "blocks, or structured data unless the user specifically "
                    "asks for it. Just have a normal, friendly conversation.)"
                ),
            },
        ]

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        except Exception as exc:
            logger.exception("Chat API call failed.")
            return (
                "抱歉，我现在无法回复。API 调用出现问题，请稍后再试。\n"
                "(如果持续出现此问题，请检查 API 密钥和网络连接。)"
            )

        if not response.choices:
            return (
                "抱歉，API 返回了空响应。请稍后再试。\n"
                "(如果持续出现此问题，请检查 API 密钥和网络连接。)"
            )
        choice = response.choices[0]
        content = choice.message.content or "(no response)"

        return content

    # -- internal: execute via Planner ----------------------------------------

    def _execute_via_planner(self, goal: str, history_context: str = "") -> str:
        """New task execution path: Gatekeeper delegates to Planner.

        1. Formulate a Directive from the user's goal.
        2. Pass to Planner for tactical execution.
        3. If failures remain, enter recovery loop (up to 2 attempts):
           - Diagnose: ask LLM why tasks failed.
           - Reformulate: create a new Directive targeting failed aspects.
           - Re-execute: call Planner again.
           - Merge: combine successful results from all attempts.
        4. Format the ExecutionReport for the user.

        The Gatekeeper never sees TaskSpecs, Workers, or retry loops —
        only the strategic Directive and ExecutionReport.

        Args:
            goal: The user's free-text goal.
            history_context: Optional formatted recent conversation history.

        Returns:
            A user-facing summary string.
        """
        # ── 1. Formulate strategic directive ────────────────────────────
        directive = self._formulate_directive(goal, history_context, user_goal=goal)

        # ── 2. Delegate to Planner (first attempt) ──────────────────────
        report = self._planner.execute(directive)

        # ── 3. Recovery loop: diagnose → reformulate → retry ────────────
        recovery_attempts = 0
        max_recovery = 2  # Max 2 recovery attempts at Gatekeeper level

        while recovery_attempts < max_recovery and report.failed > 0:
            # L3-9: if tasks are retry-exhausted, don't waste recovery cycles
            _retry_exhausted = any(
                "[RETRY_EXHAUSTED]" in fd
                for fd in (report.failed_details or [])
            )
            if _retry_exhausted:
                logger.info(
                    "Gatekeeper: [RETRY_EXHAUSTED] detected — "
                    "skipping further recovery, delivering with caveat."
                )
                break

            logger.info(
                "Gatekeeper recovery attempt %d/%d — %d task(s) failed.",
                recovery_attempts + 1, max_recovery, report.failed,
            )

            # Diagnose: why did these tasks fail?
            diagnosis = self._diagnose_failures(report, goal)

            # Reformulate: create a new directive with a different strategy
            new_directive = self._reformulate_for_recovery(
                goal, report, diagnosis, recovery_attempts, user_goal=goal,
            )

            # Execute again
            new_report = self._planner.execute(new_directive)

            # Merge: keep successes from previous runs, add new results
            report = self._merge_reports(report, new_report, recovery_attempts=recovery_attempts + 1)
            recovery_attempts += 1

            if report.failed == 0:
                logger.info(
                    "Recovery succeeded — all tasks passed after %d attempt(s).",
                    recovery_attempts,
                )
                break

        # ── 3.5 交付前校验：产出是否符合用户原始需求 ────────────────
        # Bounded loop: if validation keeps failing, don't loop forever.
        # Max 3 delivery-validation recovery attempts, then give up and
        # deliver whatever we have with a caveat.
        _MAX_VALIDATION_RETRIES = 3
        for _val_attempt in range(_MAX_VALIDATION_RETRIES):
            validation = self._validate_delivery(goal, report)
            if validation["valid"]:
                break
            logger.info(
                "Gatekeeper delivery validation failed (attempt %d/%d): %s",
                _val_attempt + 1, _MAX_VALIDATION_RETRIES, validation["reason"],
            )
            # 注入偏差原因，触发一次额外恢复循环
            recovery_goal = f"{goal}\n\n[上次偏差：{validation['reason']}]"
            recovery_directive = self._formulate_directive(recovery_goal, history_context, user_goal=goal)
            recovery_report = self._planner.execute(recovery_directive)
            report = self._merge_reports(
                report, recovery_report, recovery_attempts=recovery_attempts + 1,
            )
        else:
            # All validation retries exhausted — deliver with caveat
            logger.warning(
                "Gatekeeper delivery validation failed after %d attempts — "
                "delivering partial result with caveat.",
                _MAX_VALIDATION_RETRIES,
            )
            report.failed_details.append(
                f"[交付校验未通过（{_MAX_VALIDATION_RETRIES}次尝试）] "
                f"产出可能不完全符合用户原始需求，请人工确认。"
            )

        # ── 4. Format for user ──────────────────────────────────────────
        # If report is empty/failed, check planner's _last_error for
        # dispatch-level failures the report might not surface.
        if report.total_tasks == 0 or report.status == "failed":
            planner_error = getattr(self._planner, '_last_error', None)
            if planner_error:
                return (
                    f"{_zhu('Janus 汇报：任务执行未完成。')}\n"
                    f"原因：{planner_error}"
                )

        return self._report_to_user(report)

    def _formulate_directive(self, goal: str, history_context: str = "", user_goal: str = "") -> Directive:
        """Translate a user goal into a strategic Directive.

        Uses a lightweight LLM call to extract strategic intent, identify
        constraints, and set priority.  Falls back to a template-based
        Directive on API failure.

        When *history_context* is provided, it is prepended so the directive
        is informed by recent conversation (e.g. "continue the previous task").

        Args:
            goal: The user's raw goal string (may be modified for recovery).
            history_context: Optional formatted recent conversation history.
            user_goal: The pristine, never-modified original user input.
                Defaults to *goal* when empty (normal path, where goal IS pristine).

        Returns:
            A ``Directive`` ready for Planner consumption.
        """
        # Default user_goal to goal when not explicitly provided (normal path)
        pristine = user_goal or goal
        history_block = ""
        if history_context:
            history_block = f"{history_context}\n\n"

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._GATEKEEPER_IDENTITY},
            {"role": "system", "content": self._FORMULATE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"{history_block}"
                    "Analyze the following user goal and produce a strategic "
                    "directive for the planning team. Consider the recent "
                    "conversation history when interpreting the goal.\n\n"
                    "IMPORTANT: If the user's goal mentions a file path, "
                    "directory, or project location, you MUST preserve that "
                    "exact path in the 'intent' field so the Planner and "
                    "Workers operate on the correct directory.\n\n"
                    "Output ONLY valid JSON with this exact format:\n"
                    "{\n"
                    '  "intent": "strategic direction — what is the user really trying to achieve? Include the exact target path if one was specified.",\n'
                    '  "constraints": "hard constraints — what must NOT be violated?",\n'
                    '  "priority": "speed" | "quality" | "balanced"\n'
                    "}\n\n"
                    f"User goal: {goal}"
                ),
            },
        ]

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                extra_body={"thinking": {"type": "enabled"}},
            )
        except Exception as exc:
            logger.exception("Directive formulation API call failed, using template.")
            logger.warning(
                "Using template-based Directive (intent='') — API call failed: %s",
                type(exc).__name__,
            )
            return Directive(
                goal=goal,
                user_goal=pristine,
                intent="",
                constraints="",
                context=history_context,
                priority="normal",
            )

        if not response.choices:
            logger.warning("Directive formulation API returned empty choices, using template.")
            logger.warning(
                "Intent is empty — Worker will execute without strategic direction. "
                "This may produce suboptimal results."
            )
            return Directive(
                goal=goal,
                user_goal=pristine,
                intent="",
                constraints="",
                context=history_context,
                priority="normal",
            )

        choice = response.choices[0]
        content = choice.message.content or ""

        parsed = extract_json(content)

        if isinstance(parsed, dict):
            return Directive(
                goal=goal,
                user_goal=pristine,
                intent=str(parsed.get("intent", "")),
                constraints=str(parsed.get("constraints", "")),
                context=history_context,
                priority=str(parsed.get("priority", "normal")),
            )

        logger.warning(
            "Directive formulation returned unexpected shape, using template: %r",
            content[:200],
        )
        logger.warning(
            "Falling back to template-based Directive (intent='') — "
            "LLM returned unparseable JSON. Worker will execute without "
            "strategic direction — results may be suboptimal."
        )
        return Directive(
            goal=goal,
            user_goal=pristine,
            intent="",
            constraints="",
            context=history_context,
            priority="normal",
        )

    # -- internal: recovery methods -------------------------------------------

    def _diagnose_failures(self, report: ExecutionReport, goal: str) -> str:
        """Ask the LLM to diagnose WHY tasks failed and suggest different approaches.

        Uses the report's ``failed_details`` for structured failure information.
        Returns diagnostic text that the reformulation step can use.

        Args:
            report: The ExecutionReport containing failures.
            goal: The original user goal.

        Returns:
            Diagnostic analysis string, or a fallback on API error.
        """
        # Build failure context from failed_details
        if report.failed_tasks:
            # Structured failure data — rich context for better diagnosis
            failure_lines: list[str] = []
            for ft in report.failed_tasks:
                parts = [
                    f"  - {ft.get('task_id', '?')}: {ft.get('summary', '')}",
                ]
                if ft.get("acceptance_criteria"):
                    parts.append(f"    验收标准: {ft['acceptance_criteria']}")
                if ft.get("review_issues"):
                    parts.append(f"    审查问题: {ft['review_issues']}")
                failure_lines.append("\n".join(parts))
            failure_text = "\n\n".join(failure_lines)
        elif report.failed_details:
            failure_text = "\n".join(
                f"  - {d}" for d in report.failed_details
            )
        else:
            failure_text = "\n".join(
                f"  - {d}" for d in report.details if "失败" in d or "未通过" in d or "未完成" in d
            )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._GATEKEEPER_IDENTITY},
            {
                "role": "user",
                "content": (
                    "You are diagnosing task failures to plan a recovery strategy.\n\n"
                    "## Known Worker Tool Limitations\n"
                    "Consider these when diagnosing failures — they may explain "
                    "why certain acceptance criteria are impossible to satisfy:\n"
                    "- read_file: returns up to 50,000 characters per call. "
                    "The Worker CAN page through larger files using offset/limit "
                    "parameters. But if acceptance criteria require the Worker's "
                    "output to contain 'complete' file content for very large files, "
                    "this may cause repeated failures.\n"
                    "- web_extract: truncates at 3,000 characters per URL.\n"
                    "If a failure pattern matches one of these limitations, the fix is "
                    "to change the acceptance criteria or task description, NOT to retry "
                    "the same task.\n\n"
                    f"## Original Goal\n{goal}\n\n"
                    f"## Failed Tasks ({report.failed} out of {report.total_tasks})\n"
                    f"{failure_text}\n\n"
                    "## Your Task\n"
                    "1. Diagnose WHY each task likely failed — was it a decomposition "
                    "problem (task too large/vague), a worker capability gap, "
                    "a tool limitation, or something else?\n"
                    "2. Suggest a DIFFERENT strategy for the failed aspects. "
                    "Think about: finer-grained tasks, different approach angle, "
                    "explicit preconditions, or splitting one hard task into "
                    "easier sub-tasks.\n"
                    "3. Output a concise diagnostic analysis in plain text. "
                    "Focus on ACTIONABLE insights — what should be tried "
                    "differently in the next attempt?\n\n"
                    "Do NOT output JSON. Output plain text analysis."
                ),
            },
        ]

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        except Exception as exc:
            logger.exception("Diagnosis API call failed.")
            return (
                f"API error during diagnosis: {type(exc).__name__}. "
                f"Fallback: tasks likely failed due to execution errors — "
                f"try smaller, more explicit task descriptions."
            )

        if not response.choices:
            logger.warning("Diagnosis API returned empty choices.")
            return "Unable to diagnose — API returned no response choices."

        choice = response.choices[0]
        return choice.message.content or "Unable to diagnose — no LLM response."

    def _reformulate_for_recovery(
        self,
        goal: str,
        report: ExecutionReport,
        diagnosis: str,
        attempt: int,
        user_goal: str = "",
    ) -> Directive:
        """Create a new Directive targeting only the failed aspects.

        Uses the diagnosis to craft a different strategic approach.
        The new directive focuses on what wasn't achieved yet.

        Args:
            goal: The original user goal.
            report: The ExecutionReport with failures.
            diagnosis: The diagnostic text from _diagnose_failures.
            attempt: Current recovery attempt number (0-indexed).
            user_goal: The pristine, never-modified original user input.

        Returns:
            A new Directive with recovery-focused strategy.
        """
        pristine = user_goal or goal
        # Build failure context
        if report.failed_details:
            failure_text = "\n".join(
                f"  - {d}" for d in report.failed_details
            )
        else:
            failure_text = "\n".join(
                f"  - {d}" for d in report.details if "失败" in d or "未通过" in d or "未完成" in d
            )

        # Build success context — what already worked, so don't redo it
        success_details = [d for d in report.details if "✓" in d or "完成" in d or "通过" in d]
        success_text = (
            "\n".join(f"  - {d}" for d in success_details)
            if success_details
            else "(none — all tasks failed)"
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._FORMULATE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"## Original Goal\n{goal}\n\n"
                    f"## Already Completed (DO NOT redo)\n{success_text}\n\n"
                    f"## Failed Tasks\n{failure_text}\n\n"
                    f"## Failure Diagnosis\n{diagnosis}\n\n"
                    f"## Recovery Attempt\n"
                    f"This is recovery attempt {attempt + 1} of 2. "
                    f"The previous attempt failed on some tasks.\n\n"
                    "## Your Task\n"
                    "Create a NEW strategic directive that:\n"
                    "1. Targets ONLY the failed aspects — DO NOT redo what already succeeded.\n"
                    "2. Uses a DIFFERENT strategy based on the diagnosis above.\n"
                    "3. Makes failed tasks more concrete, smaller, or approached from a different angle.\n\n"
                    "Output ONLY valid JSON with this exact format:\n"
                    "{\n"
                    '  "intent": "recovery strategic direction — what new approach to take",\n'
                    '  "constraints": "hard constraints — same as original plus any new ones",\n'
                    '  "priority": "speed" | "quality" | "balanced"\n'
                    "}\n\n"
                    f"Original constraints: {report.constraints or '(none)'}"
                ),
            },
        ]

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                extra_body={"thinking": {"type": "enabled"}},
            )
        except Exception as exc:
            logger.exception("Recovery reformulation API call failed.")
            # Fallback: create a bare-bones directive from the goal + diagnosis
            return Directive(
                goal=f"{goal}\n\n[RECOVERY — previous attempt had failures. "
                     f"Diagnosis: {diagnosis[:200]}]",
                user_goal=pristine,
                intent=f"Recover from failed attempt {attempt + 1} — "
                       f"try a different approach.",
                constraints=report.constraints,
                priority="quality",
            )

        if not response.choices:
            logger.warning("Recovery reformulation API returned empty choices.")
            return Directive(
                goal=f"{goal}\n\n[RECOVERY — previous attempt had failures. "
                     f"Diagnosis: {diagnosis[:200]}]",
                user_goal=pristine,
                intent=f"Recover from failed attempt {attempt + 1} — "
                       f"try a different approach.",
                constraints=report.constraints,
                priority="quality",
            )

        choice = response.choices[0]
        content = choice.message.content or ""

        parsed = extract_json(content)

        if isinstance(parsed, dict):
            recovery_goal = (
                f"{goal}\n\n[RECOVERY ATTEMPT {attempt + 1}/2 — "
                f"only address the failed aspects. "
                f"Already completed tasks should NOT be redone. "
                f"Diagnosis: {diagnosis[:300]}]"
            )
            return Directive(
                goal=recovery_goal,
                user_goal=pristine,
                intent=str(parsed.get("intent", "")),
                constraints=str(
                    parsed.get("constraints", report.constraints)
                ),
                priority=str(parsed.get("priority", "quality")),
            )

        logger.warning(
            "Recovery reformulation returned unexpected shape: %r",
            content[:200],
        )
        return Directive(
            goal=(
                f"{goal}\n\n[RECOVERY ATTEMPT {attempt + 1}/2 — "
                f"try a different strategy. "
                f"Diagnosis: {diagnosis[:300]}]"
            ),
            user_goal=pristine,
            intent=f"Recover from failed attempt {attempt + 1}.",
            constraints=report.constraints,
            priority="quality",
        )

    @staticmethod
    def _merge_reports(old: ExecutionReport, new: ExecutionReport, recovery_attempts: int = 0) -> ExecutionReport:
        """Merge two ExecutionReports — keep successes from old, add new results.

        Strategically combines results so the user sees cumulative progress
        rather than just the last attempt's results.

        Args:
            old: The previous ExecutionReport (may have both successes and failures).
            new: The new ExecutionReport from the recovery attempt.

        Returns:
            A merged ExecutionReport with combined results.
        """
        # Use ExecutionReport's structured fields instead of text parsing.
        # (Text matching broke after Taiji aesthetic refactor — ANSI color
        # codes wrap the Chinese labels, making "✓"/"完成" matching useless.)
        #
        # old.details contains all task summaries (mix of pass + fail).
        # old.failed_details contains only failures — use it to identify
        # which detail lines are failures when old has both pass and fail.
        old_failure_set: set[str] = set(old.failed_details) if old.failed_details else set()

        old_success_details: list[str] = []
        old_fail_details: list[str] = []
        if old_failure_set:
            for d in old.details:
                if d in old_failure_set:
                    old_fail_details.append(d)
                else:
                    old_success_details.append(d)
        else:
            # No failures at all — everything is success
            old_success_details = list(old.details)

        # Build merged details: old successes first, then new results
        merged_details: list[str] = []

        # Add old successes (they still count)
        if old_success_details:
            merged_details.append("── 首次尝试成功 ──")
            merged_details.extend(old_success_details)

        # Add new results
        if new.details:
            if merged_details:
                merged_details.append("")
            merged_details.append("── 恢复尝试结果 ──")
            merged_details.extend(new.details)

        # Merge failed_details: keep old failures (marked as prior attempt)
        # plus new failures, so recovery-attempt context is never silently lost.
        merged_failed_details: list[str] = []
        if old.failed_details:
            merged_failed_details.append(
                "── 首次尝试失败（已在恢复尝试中重试）──"
            )
            merged_failed_details.extend(old.failed_details)
        if new.failed_details:
            if merged_failed_details:
                merged_failed_details.append("")
            merged_failed_details.append("── 恢复尝试失败 ──")
            merged_failed_details.extend(new.failed_details)

        # Recalculate totals
        # Guard: if the recovery attempt produced zero tasks (Planner
        # decomposition failed), preserve the old report's failure state
        # rather than making it look like everything passed.
        if new.total_tasks == 0:
            merged_details.append(
                "── 恢复尝试 ──\n"
                "恢复尝试未能生成任何任务（Planner 分解失败）。"
            )
            total_passed = old.passed
            total_failed = old.failed  # Preserve original failures
            total_tasks = total_passed + total_failed
        else:
            total_passed = old.passed + new.passed
            total_failed = new.failed  # Only new failures matter; old failures were retried
            total_tasks = total_passed + total_failed

        if total_failed == 0:
            status = "completed"
        elif total_passed == 0:
            status = "failed"
        else:
            status = "partial"

        if recovery_attempts > 0:
            merged_summary = (
                f"Completed: {total_passed}/{total_tasks} tasks"
                f" (after {recovery_attempts} recovery attempt(s))."
            )
        else:
            merged_summary = f"Completed: {total_passed}/{total_tasks} tasks."
        if total_failed > 0:
            merged_summary += f", {total_failed} failed"

        return ExecutionReport(
            status=status,
            total_tasks=total_tasks,
            passed=total_passed,
            failed=total_failed,
            summary=merged_summary,
            details=merged_details,
            failed_details=merged_failed_details,
            goal=old.goal or new.goal,
            constraints=old.constraints or new.constraints,
        )

    # -- internal: delivery validation ----------------------------------------

    def _validate_delivery(self, goal: str, report: ExecutionReport) -> dict:
        """一次 LLM 调用判断产出是否回答原始需求。

        在 Gatekeeper 向用户汇报之前运行，确保 Planner 的产出真正回答了
        用户的问题，而不是跑偏到了完全不同的方向。

        Args:
            goal: 用户原始需求。
            report: Planner 的执行报告。

        Returns:
            ``{"valid": True|False, "reason": "一句话说明"}``。
            API 异常时返回 ``{"valid": True, "reason": ""}``，不阻塞交付。
        """
        # 构建精简摘要——不传完整报告以节省 token
        report_summary = report.summary
        if report.details:
            detail_preview = "\n".join(report.details[:3])
            if len(report.details) > 3:
                detail_preview += f"\n... (共 {len(report.details)} 项)"
            report_summary = f"{report.summary}\n{detail_preview}"

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._GATEKEEPER_IDENTITY},
            {
                "role": "user",
                "content": (
                    f"用户原始需求：{goal}\n\n"
                    f"我们产出了以下结果：{report_summary}\n\n"
                    "判断：这份产出是否真正回答了用户的需求？\n"
                    "如果分析的对象完全错误（比如用户要求分析项目A，产出分析了项目B），判定为 invalid。\n"
                    "如果内容大体正确但有遗漏，判定为 valid_with_gaps。\n"
                    "如果完全正确，判定为 valid。\n\n"
                    "输出 JSON：{\"valid\": true|false, \"reason\": \"一句话说明\"}"
                ),
            },
        ]

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        except Exception as exc:
            logger.exception("Delivery validation API call failed, skipping.")
            return {"valid": True, "reason": ""}

        if not response.choices:
            logger.warning("Delivery validation returned empty choices, skipping.")
            return {"valid": True, "reason": ""}

        choice = response.choices[0]
        content = choice.message.content or ""

        parsed = extract_json(content)
        if isinstance(parsed, dict):
            valid = parsed.get("valid", True)
            if isinstance(valid, str):
                valid = valid.lower() != "false"
            return {
                "valid": bool(valid),
                "reason": str(parsed.get("reason", "")),
            }

        logger.warning(
            "Delivery validation returned unexpected shape, skipping: %r",
            content[:200],
        )
        return {"valid": True, "reason": ""}

    def _report_to_user(self, report: ExecutionReport) -> str:
        """Format an ExecutionReport into a user-facing string.

        Taiji aesthetics:
        - 全部完成 → 金「完成」
        - 全部未完成 → 朱「未完成」
        - 部分完成 → 青「部分完成」

        Each semantic block separated by blank lines (阴阳呼吸).

        Args:
            report: The Planner's execution report.

        Returns:
            A user-friendly summary string.
        """
        if report.total_tasks == 0:
            return (
                f"任务未能执行。\n"
                f"原因：{report.summary}\n"
                f"\n"
                f"{_jin('→ 尝试把目标拆分成更小的步骤重新描述，或输入 help 查看使用示例。')}"
            )

        if report.failed == 0:
            # 全部完成——金色落定
            summary_line = _jin(f"完成 · {report.passed}/{report.total_tasks} 个任务")
        elif report.passed == 0:
            # 全部未完成——朱砂印记
            summary_line = _zhu(f"未完成 · 0/{report.total_tasks} 个任务")
        else:
            # 部分完成——青花过渡
            summary_line = _qing(f"部分完成 · {report.passed}/{report.total_tasks} 个任务")

        lines = [summary_line]

        if report.details:
            lines.append("")
            for d in report.details:
                # Clean up detail lines: remove worker-N prefixes, keep clean format
                cleaned = d
                # Remove "worker-N: " prefix
                cleaned = re.sub(r'^\s*(worker-\d+|\[[^\]]+\])\s*[:\s]*', '', cleaned)
                # Keep structural separator lines
                if cleaned.startswith("── ") and cleaned.endswith(" ──"):
                    lines.append(f"  {cleaned}")
                elif cleaned.strip():
                    lines.append(f"  {cleaned.strip()}")

        # -- Actionable suggestions when tasks failed ------------------------
        if report.failed > 0:
            lines.append("")
            lines.append(
                f"{_jin(f'→ {report.failed} 个任务未完成，输入 help 查看排错建议。')}"
            )

        return "\n".join(lines)

