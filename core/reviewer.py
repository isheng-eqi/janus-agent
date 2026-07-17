"""
Janus Reviewer — independent audit agent that checks Worker output against
acceptance criteria.

The Reviewer is NOT part of the Gatekeeper.  It is a standalone LLM-driven
agent with ZERO tools — it reasons purely by inspecting the TaskSpec
(including acceptance_criteria) and the Worker's TaskResult.

Integration point:
    After each Worker finishes (SUCCESS or NEEDS_DECOMPOSITION), the Gatekeeper
    may call ``reviewer.review(spec, result)`` to get an independent audit.
    FAILURE results are auto-passed through (no re-audit of failures).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum

from .prompts import extract_json
from .protocol import TaskResult, TaskSpec, TaskStatus

# ---------------------------------------------------------------------------
# OpenAI client — lazy import with a helpful error message
# ---------------------------------------------------------------------------
try:
    from openai import OpenAI  # type: ignore[import-untyped]
except ImportError as exc:
    raise ImportError(
        "The `openai` package is required by Janus Reviewer. "
        "Install it with: pip install openai"
    ) from exc

logger = logging.getLogger(__name__)


# ============================================================================
# Enums
# ============================================================================


class ReviewVerdict(Enum):
    """Graded review outcome — replaces the old binary pass/fail.

    Inspired by academic peer review (accept / minor revisions / major
    revisions / reject) and manufacturing quality control levels.
    """

    APPROVED = "approved"
    """Perfect — meets all criteria with no issues."""

    APPROVED_WITH_NOTES = "approved_with_notes"
    """Conditionally approved — has minor suggestions but no blocking issues."""

    MINOR_REVISIONS = "minor_revisions"
    """Needs small fixes — retry once with feedback, auto-accept after."""

    MAJOR_REVISIONS = "major_revisions"
    """Needs significant rework — retry up to 2 times with full re-review."""

    REJECTED = "rejected"
    """Does not meet core requirements — retry up to 2 times, then fail."""


class Severity(Enum):
    """Defect severity level for individual review issues.

    Inspired by manufacturing quality control's four-tier defect system
    (致命 / 严重 / 轻微 / 可接受偏差).
    """

    CRITICAL = "critical"
    """🔴 Fatal — result is completely unusable. Always triggers retry."""

    MAJOR = "major"
    """🟡 Serious — core requirement not met. Triggers retry."""

    MINOR = "minor"
    """🟢 Moderate — partial deviation but result is usable.
    Retry once, then accept with notes."""

    SUGGESTION = "suggestion"
    """💡 Optimization suggestion — does not block approval."""


# ============================================================================
# ReviewIssue
# ============================================================================


@dataclass
class ReviewIssue:
    """A single issue found during review, carrying a severity level.

    Attributes:
        severity: How serious this issue is (see :class:`Severity`).
        description: Human-readable description of the problem.
    """

    severity: Severity
    description: str

    def to_dict(self) -> dict:
        return {
            "severity": self.severity.value,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ReviewIssue:
        return cls(
            severity=Severity(data["severity"]),
            description=data["description"],
        )


# ============================================================================
# ReviewResult
# ============================================================================


@dataclass
class ReviewResult:
    """The output of a single review.

    Attributes:
        verdict: Graded review outcome (see :class:`ReviewVerdict`).
        summary: One-line verdict describing the review outcome.
        issues: Specific problems found, each tagged with a severity level.
        evidence: What convinced the reviewer — concrete observations from the
            result that prove success or document what was missing.
    """

    verdict: ReviewVerdict
    summary: str
    issues: list[ReviewIssue] = field(default_factory=list)
    evidence: str = ""

    @property
    def passed(self) -> bool:
        """Convenience accessor — True when the verdict is acceptable.

        APPROVED and APPROVED_WITH_NOTES are considered passing.
        MINOR_REVISIONS, MAJOR_REVISIONS, and REJECTED are not.
        """
        return self.verdict in (
            ReviewVerdict.APPROVED,
            ReviewVerdict.APPROVED_WITH_NOTES,
        )

    @property
    def is_blocking(self) -> bool:
        """True when the verdict requires a retry (not passing)."""
        return not self.passed

    @property
    def blocking_issues(self) -> list[ReviewIssue]:
        """Issues that warrant a retry — CRITICAL, MAJOR, and MINOR."""
        return [
            i for i in self.issues
            if i.severity in (Severity.CRITICAL, Severity.MAJOR, Severity.MINOR)
        ]

    @property
    def has_critical(self) -> bool:
        """True when at least one CRITICAL issue is present."""
        return any(i.severity == Severity.CRITICAL for i in self.issues)

    def to_dict(self) -> dict:
        """Serialize to a plain dict for transport/storage."""
        return {
            "verdict": self.verdict.value,
            "summary": self.summary,
            "issues": [i.to_dict() for i in self.issues],
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ReviewResult:
        """Deserialize from a plain dict.

        Supports both the new ``verdict`` key and the legacy ``status`` key
        for backward compatibility.
        """
        # Backward compat: map old "status" field to "verdict"
        verdict_raw = data.get("verdict") or data.get("status", "rejected")
        try:
            verdict = ReviewVerdict(verdict_raw)
        except ValueError:
            # Legacy "pass" / "fail" mapping
            verdict_map = {
                "pass": ReviewVerdict.APPROVED,
                "fail": ReviewVerdict.REJECTED,
            }
            verdict = verdict_map.get(verdict_raw, ReviewVerdict.REJECTED)

        issues: list[ReviewIssue] = []
        for item in data.get("issues", []):
            if isinstance(item, str):
                # Legacy string-only issues → default to MAJOR severity
                issues.append(ReviewIssue(Severity.MAJOR, item))
            elif isinstance(item, dict):
                try:
                    issues.append(ReviewIssue.from_dict(item))
                except (KeyError, ValueError):
                    # Fallback for malformed issue dicts
                    issues.append(
                        ReviewIssue(Severity.MAJOR, str(item))
                    )

        return cls(
            verdict=verdict,
            summary=data.get("summary", ""),
            issues=issues,
            evidence=data.get("evidence", ""),
        )


# ============================================================================
# Reviewer
# ============================================================================


class Reviewer:
    """LLM-driven audit agent that checks Worker output against acceptance criteria.

    The Reviewer has **no tools** — it reasons solely from the TaskSpec and
    TaskResult.  It is called by the Gatekeeper after each Worker finishes.

    Key behaviors:
        - Worker FAILURE → auto-pass (no point re-auditing a failure).
        - Worker NEEDS_DECOMPOSITION → checks that the decomposition request
          reasonably addresses the original acceptance criteria.
        - API failure → returns a ``ReviewResult(verdict=REJECTED)`` with the error.
        - Timeout: 120 seconds per review.

    Usage::

        reviewer = Reviewer(model="deepseek-chat", api_key=os.environ["DEEPSEEK_API_KEY"])
        review = reviewer.review(spec, result)
        if review.passed:
            print(f"✓ {review.summary}")
        else:
            print(f"✗ {review.summary}")
            for issue in review.issues:
                print(f"  [{issue.severity.value}] {issue.description}")
    """

    # -- review prompt template -----------------------------------------------

    _REVIEW_SYSTEM_PROMPT = """\
You are a Janus Reviewer. Your sole job is to audit deliverables against \
requirements.

Given a task specification with acceptance criteria and a Worker's delivered \
result, evaluate whether the result actually meets every criterion.

## Review Philosophy: Verify, Don't Presume

The Worker who produced this deliverable had direct access to tools, files, \
and execution context that you do not have. Your job is NOT to second-guess \
every decision — it is to verify that the Worker's claims are backed by \
evidence in the artifact contents and result summary.

- If the Worker claims something and the artifact contents support it → \
criterion is SATISFIED. Do not invent hypothetical failure modes.
- If evidence is absent or contradictory → flag as an issue with evidence.
- If the Worker says "X is done" and the artifact shows X is done → ACCEPT.
- The burden of proof is on the ABSENCE of evidence, not the presence of \
perfection. A deliverable that meets all stated criteria is APPROVED \
regardless of whether YOU would have done it differently.

## Acceptance Criteria Classification

Criteria are marked [HARD] or [SOFT]. Unmarked criteria default to [HARD].

## EXACT SEVERITY MAPPING (this is the single authoritative rule set)

For each criterion, identify its class, then apply the corresponding rule:

| Criterion Class | Failure Mode                | Severity   | Verdict Floor     |
|-----------------|-----------------------------|------------|-------------------|
| [HARD]          | completely unmet / absent   | critical   | rejected          |
| [HARD]          | significantly wrong         | major      | major_revisions   |
| [HARD]          | small / partial deviation   | major      | major_revisions   |
| [SOFT]          | completely unmet / absent   | minor      | minor_revisions   |
| [SOFT]          | partially met / suboptimal  | suggestion | approved_with_notes |
| ANY             | optimization / improvement  | suggestion | approved_with_notes |

## NON-NEGOTIABLE RULES

1. If a [HARD] criterion is NOT fully met → severity CANNOT be "minor" or \
"suggestion" for that issue. A small gap on a hard requirement is still a \
hard-requirement failure.
2. If at least one issue has severity "critical" → verdict MUST be "rejected".
3. If at least one issue has severity "major" → verdict MUST be at least \
"major_revisions".
4. SOFT-only failures (no HARD criterion violated) → verdict is at most \
"minor_revisions".
5. When in doubt between two severities, choose the HIGHER one. Understating \
severity masks problems; overstating triggers a verification check.
6. Regardless of task priority, empty or placeholder artifacts (files without \
meaningful content) ALWAYS constitute at least a MAJOR issue. Speed is no \
excuse for empty deliverables.

Be precise and evidence-based. For each criterion, cite specific evidence \
from the result and artifact contents.

## DEEP REVIEW MODE (when tool-call audit log is present)

When the TOOL-CALL AUDIT LOG section is populated (not "(no tool-call log)"),
you MUST perform a cross-comparison audit:
1. For each claim the Worker makes in its result/summary, verify that a
   corresponding tool invocation exists in the log.
2. If the Worker claims to have read a file but no read_file call appears
   in the log -> flag as CRITICAL deception.
3. If the Worker claims to have written a file but no write_file call
   appears -> flag as CRITICAL deception.
4. If file paths in the Worker result do not match actual tool-call
   arguments -> flag as MAJOR inconsistency.
5. Cite specific tool-call timestamps as evidence for each verification.
"""

    _REVIEW_USER_TEMPLATE = """\
TASK: {description}
ACCEPTANCE CRITERIA: {acceptance_criteria}
EXPECTED ARTIFACTS: {context}
GOAL: {goal}
用户原始输入：{user_goal}
请确认这份产出是用户要的东西，而不只是满足验收标准。
CONSTRAINTS: {constraints}
INTENT: {intent}

DELIVERED RESULT:
Status: {status}
Summary: {summary}
Full Result: {result}
Artifact Paths: {artifacts}

ARTIFACT CONTENTS (pre-loaded so you can inspect what was actually written):
{artifact_contents}

TOOL-CALL AUDIT LOG (Worker's actual tool invocations):
{tool_log_section}

For each acceptance criterion:
1. Identify whether it is [HARD] or [SOFT].
2. Does the result satisfy it?
3. What evidence proves it? Inspect the artifact contents above to verify.
4. Assign severity based on the criterion's classification (see CRITERIA CLASSIFICATION above).

Then decide the overall verdict:
- **approved**: All criteria perfectly met, no issues.
- **approved_with_notes**: All [HARD] criteria met, only SOFT suggestions remain.
- **minor_revisions**: Small issues found (especially SOFT-only) — fix and auto-pass.
- **major_revisions**: One or more [HARD] criteria not met — significant rework needed.
- **rejected**: Core [HARD] criteria not met — substantial rework needed.

Output ONLY a JSON object with this schema:
{{
  "verdict": "approved" | "approved_with_notes" | "minor_revisions" | "major_revisions" | "rejected",
  "summary": "one-line verdict",
  "issues": [
    {{"severity": "critical" | "major" | "minor" | "suggestion", "description": "..."}}
  ],
  "evidence": "what proved success or what was missing"
}}"""

    # -- constructor ----------------------------------------------------------

    def __init__(self, model: str, api_key: str, timeout: int = 120) -> None:
        """Create a Reviewer.

        Args:
            model: DeepSeek model name (e.g. ``"deepseek-chat"``).
            api_key: DeepSeek API key.
            timeout: Maximum seconds to wait for the LLM response (default 120).
        """
        self._model = model
        self._timeout = timeout

        self._client = OpenAI(
            base_url="https://api.deepseek.com",
            api_key=api_key,
            timeout=timeout,
        )

    # -- artifact helpers ------------------------------------------------------

    @staticmethod
    def _read_artifact(path: str, allowed_artifacts: list[str], max_chars: int = 3000) -> str:
        """Read a single artifact file with access control.

        Only files listed in *allowed_artifacts* can be read.  The resolved
        absolute path is checked against the allowed list to prevent path
        traversal attacks.

        Args:
            path: The file path to read.
            allowed_artifacts: List of approved artifact paths.
            max_chars: Maximum characters to return (default 3000).
                Content longer than this is truncated with ``"...[truncated]"``.

        Returns:
            The file contents (possibly truncated), or an error string.
        """
        import os as _os

        # Resolve to absolute so we can compare reliably.
        try:
            resolved = _os.path.abspath(_os.path.expanduser(path))
        except (ValueError, OSError):
            return f"Access denied: invalid path '{path}'"

        # Check against the allowed list (also resolve each entry).
        allowed_set: set[str] = set()
        for art in allowed_artifacts:
            try:
                allowed_set.add(_os.path.abspath(_os.path.expanduser(art)))
            except (ValueError, OSError):
                pass

        # Also allow the raw path as-is (some Workers may list relative paths).
        if resolved not in allowed_set and path not in allowed_artifacts:
            return (
                f"Access denied: file not in approved artifacts list. "
                f"Allowed: {allowed_artifacts}"
            )

        try:
            with open(resolved, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read(max_chars + 1)
        except FileNotFoundError:
            return f"File not found: {path}"
        except PermissionError:
            return f"Permission denied: {path}"
        except OSError as exc:
            return f"Error reading {path}: {exc}"

        if len(content) > max_chars:
            content = content[:max_chars] + "\n...[truncated]"
        return content

    @staticmethod
    def _build_artifact_contents(artifacts: list[str], max_per_file: int = 3000, max_total: int = 16000) -> str:
        """Pre-load artifact file contents for the review prompt.

        Reads each artifact file and formats its contents inline.  Small files
        (≤ *max_per_file* chars) are included in full; large files get only
        the first *max_per_file* chars with a truncation notice.

        The total output is capped at *max_total* characters to avoid
        blowing up the review prompt.

        Args:
            artifacts: List of artifact file paths.
            max_per_file: Max characters to include per file (default 3000).
            max_total: Hard cap on total characters in the section (default 16000).

        Returns:
            A formatted string suitable for embedding in the review prompt.
        """
        import os as _os

        if not artifacts:
            return "(no artifact files — verify the Worker's result description only)"

        sections: list[str] = []
        total_chars = 0

        for i, path in enumerate(artifacts, start=1):
            # Try to read the full file first (using _read_artifact with a
            # generous limit so we can decide truncation per our own thresholds).
            try:
                resolved = _os.path.abspath(_os.path.expanduser(path))
                exists = _os.path.isfile(resolved)
            except (ValueError, OSError):
                resolved = path
                exists = False

            if not exists:
                sections.append(f"--- Artifact {i}: {path} ---\n[File not found on disk]\n")
                total_chars += len(sections[-1])
                continue

            try:
                with open(resolved, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read(max_per_file + 1)
            except (PermissionError, OSError):
                sections.append(f"--- Artifact {i}: {path} ---\n[Cannot read file]\n")
                total_chars += len(sections[-1])
                continue

            truncated = len(content) > max_per_file
            if truncated:
                content = content[:max_per_file]

            # Build the section header + body with injection boundary markers.
            # These markers tell the Reviewer LLM that the enclosed text is
            # external artifact data, not instructions to follow.
            header = (
                f'"""BEGIN ARTIFACT {i}: {path}'
            )
            if truncated:
                header += " (truncated — first 1000 chars)"
            header += '"""\n'
            footer = f'\n"""END ARTIFACT {i}: {path}"""\n'
            section = header + content + footer
            sections.append(section)
            total_chars += len(section)

            # Hard cap on total output.
            if total_chars >= max_total:
                sections.append(
                    f"...[remaining {len(artifacts) - i} artifacts omitted — "
                    f"review prompt size limit reached]\n"
                )
                break

        return "".join(sections)

    @staticmethod
    def _build_tool_log_section(task_id: str, log_dir: str = "") -> str:
        """Read the tool-call log for *task_id* and format it for the prompt.

        Reads ``tool_logs/{task_id}.jsonl`` and returns a formatted section
        listing each tool invocation with timestamp, tool name, and arguments.
        Returns ``"(no tool-call log)"`` when no log file exists or it's empty.

        Args:
            task_id: The task whose tool log to read.
            log_dir: Optional override for the log directory.  Defaults to
                ``tool_logs/`` relative to the Janus project root.

        Returns:
            A formatted string for inclusion in the review prompt, or an
            empty-fallback message.
        """
        import os as _os

        if not task_id:
            return "(no tool-call log)"

        if not log_dir:
            log_dir = _os.path.join(
                _os.path.dirname(__file__), "..", "tool_logs"
            )

        log_path = _os.path.join(log_dir, f"{task_id}.jsonl")
        if not _os.path.isfile(log_path):
            return "(no tool-call log — file not found)"

        lines: list[str] = []
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        import json as _json
                        rec = _json.loads(line)
                        ts = rec.get("timestamp", "?")
                        tn = rec.get("tool_name", "?")
                        args = rec.get("arguments", {})
                        summary = rec.get("result_summary", "")[:100]
                        lines.append(
                            f"  [{ts}] {tn}({_json.dumps(args, ensure_ascii=False)})"
                            f"  → {summary}"
                        )
                    except (ValueError, KeyError):
                        lines.append(f"  (unparseable) {line[:120]}")
        except OSError:
            return "(no tool-call log — could not read file)"

        if not lines:
            return "(no tool-call log — empty)"

        header = (
            f"=== TOOL-CALL LOG for task {task_id} ===\n"
            f"({len(lines)} invocation(s) recorded)\n"
        )
        return header + "\n".join(lines)

    # -- public API -----------------------------------------------------------

    def review(
        self,
        spec: TaskSpec,
        result: TaskResult,
        artifact_max_per_file: int = 3000,
        artifact_max_total: int = 16000,
        deep_review: bool = False,
    ) -> ReviewResult:
        """Audit *result* against *spec*'s acceptance criteria.

        Args:
            spec: The original task specification (with acceptance_criteria).
            result: The Worker's delivered result.
            artifact_max_per_file: Max chars to read per artifact file
                (default 3000).  # L3-4: deep review raises this to 8000.
            artifact_max_total: Hard cap on total chars in the artifact
                contents section (default 16000).  # L3-4

        Returns:
            A ``ReviewResult`` with the audit verdict, issues, and evidence.
        """
        # ── Worker FAILURE → skip review, reject immediately ─────────────
        if result.status == TaskStatus.FAILURE:
            logger.info(
                "Worker %r reported FAILURE for task %r — skipping detailed review.",
                result.worker_id,
                spec.task_id,
            )
            return ReviewResult(
                verdict=ReviewVerdict.REJECTED,
                summary=f"Worker self-reported FAILURE: {result.summary}",
                issues=[
                    ReviewIssue(
                        Severity.CRITICAL,
                        f"Worker self-reported FAILURE: {result.summary}",
                    )
                ],
                evidence=(
                    f"Worker {result.worker_id or '?'} returned FAILURE for "
                    f"task {spec.task_id}.  No detailed audit performed — "
                    f"the Worker itself declared the task unsuccessful."
                ),
            )

        # ── Hard artifact content check (P0 fix) ───────────────────────
        # Verify artifact files have meaningful content — not empty shells
        # or one-line placeholders ("# ok", "# todo", etc.).
        _PLACEHOLDER_PATTERNS = [
            "# ok", "# todo", "# placeholder", "# stub",
            "# TODO", "pass", "return None", "# implement",
        ]
        _MIN_MEANINGFUL_BYTES = 80

        missing_artifacts = []
        suspicious_artifacts = []
        for path in result.artifacts:
            if not os.path.exists(path):
                missing_artifacts.append(path)
                continue
            size = os.path.getsize(path)
            if size == 0:
                suspicious_artifacts.append(f"{path} (0 bytes)")
                continue
            if size < _MIN_MEANINGFUL_BYTES:
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        head = f.read(200).strip().lower()
                    if any(p.lower() in head for p in _PLACEHOLDER_PATTERNS):
                        suspicious_artifacts.append(
                            f"{path} ({size}B, placeholder: '{head[:60]}')"
                        )
                except Exception:
                    pass  # binary file, skip pattern check

        if missing_artifacts:
            return ReviewResult(
                verdict=ReviewVerdict.REJECTED,
                issues=[ReviewIssue(
                    severity=Severity.CRITICAL,
                    description=f"声称的文件不存在: {', '.join(missing_artifacts)}"
                )],
                summary="产物真实性校验失败"
            )

        if suspicious_artifacts:
            return ReviewResult(
                verdict=ReviewVerdict.MAJOR_REVISIONS,
                issues=[ReviewIssue(
                    severity=Severity.MAJOR,
                    description=(
                        f"产物内容可疑 (空文件或占位符): "
                        f"{'; '.join(suspicious_artifacts)}"
                    )
                )],
                summary="产物内容可疑"
            )

        # ── Budget-exhaustion marker check ────────────────────────────────
        # If Worker ran out of budget, flag for deeper scrutiny.
        budget_exhausted = "[BUDGET-EXHAUSTED:" in (result.result or "")
        if budget_exhausted:
            logger.warning(
                "Task %r: Worker budget exhausted — %d artifacts preserved. "
                "Applying stricter review.",
                spec.task_id, len(result.artifacts),
            )

        # ── Build messages ───────────────────────────────────────────────
        # L3-4: pass artifact limits through for deep-review support
        artifact_contents = self._build_artifact_contents(
            result.artifacts,
            max_per_file=artifact_max_per_file,
            max_total=artifact_max_total,
        )
        # ── L3-4 deep review: read tool-call log and add cross-comparison ─
        tool_log_section = ""
        if deep_review:
            tool_log_section = self._build_tool_log_section(spec.task_id)
            if tool_log_section:
                logger.info(
                    "Deep review for %r: loaded tool-call log (%d chars).",
                    spec.task_id, len(tool_log_section),
                )

        # Use manual placeholder replacement instead of .format() to avoid
        # crashes when dynamic content (e.g. Worker results) contains
        # literal { or } characters — .format() would interpret them as
        # format specifiers and raise KeyError (BP10: format-string
        # curly-brace collision).
        user_content = (
            self._REVIEW_USER_TEMPLATE
            .replace("{description}", spec.description)
            .replace("{acceptance_criteria}", spec.acceptance_criteria)
            .replace("{context}", spec.context)
            .replace("{goal}", spec.goal)
            .replace("{user_goal}", spec.user_goal or spec.goal)
            .replace("{constraints}", spec.constraints)
            .replace("{intent}", spec.intent)
            .replace("{status}", result.status.value)
            .replace("{summary}", result.summary)
            .replace("{result}", result.result)
            .replace("{artifacts}", (
                ", ".join(result.artifacts) if result.artifacts else "(none)"
            ))
            .replace("{artifact_contents}", artifact_contents)
            .replace("{tool_log_section}", tool_log_section)
            # L3-4 deep review: append cross-comparison instructions
            # The template used {{ and }} as .format() escape sequences
            # for the JSON schema example.  Since we're now using manual
            # .replace() instead of .format(), convert escaped braces
            # back to single braces so the JSON schema renders correctly.
            .replace("{{", "{")
            .replace("}}", "}")
        )
        messages: list[dict] = [
            {"role": "system", "content": self._REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        # ── Call LLM ─────────────────────────────────────────────────────
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        except Exception as exc:
            logger.exception("Reviewer API call failed for task %r.", spec.task_id)
            return ReviewResult(
                verdict=ReviewVerdict.REJECTED,
                summary=f"Reviewer API call failed: {type(exc).__name__}",
                issues=[ReviewIssue(Severity.CRITICAL, f"Reviewer API call failed: {exc}")],
                evidence="",
            )

        if not response.choices:
            logger.warning("Reviewer API returned empty choices for task %r.", spec.task_id)
            return ReviewResult(
                verdict=ReviewVerdict.REJECTED,
                summary="Reviewer API returned no response choices.",
                issues=[ReviewIssue(Severity.CRITICAL, "API returned no choices — cannot audit.")],
                evidence="",
            )

        content = response.choices[0].message.content
        if not content:
            return ReviewResult(
                verdict=ReviewVerdict.REJECTED,
                summary="Reviewer returned empty response.",
                issues=[ReviewIssue(Severity.CRITICAL, "LLM returned no content — cannot audit.")],
                evidence="",
            )

        # ── Parse JSON ───────────────────────────────────────────────────
        parsed = self._parse_review_result(content)

        # ── Rule-engine calibration (enforce severity-verdict consistency) ──
        parsed = self._calibrate_verdict(parsed, spec.acceptance_criteria)

        return parsed

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _parse_review_result(text: str) -> ReviewResult:
        """Parse the LLM's JSON output into a ReviewResult.

        Delegates to the shared ``extract_json`` (from ``prompts.py``) for
        robust JSON extraction — handles ```json fences, nested braces,
        and raw objects in a single implementation shared across Gatekeeper,
        Planner, Worker, and Reviewer.

        Any parse failure returns a ``REJECTED`` ReviewResult with the raw
        text as evidence.
        """
        parsed = extract_json(text)

        if isinstance(parsed, dict):
            try:
                return ReviewResult.from_dict(parsed)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "Reviewer JSON parse succeeded but ReviewResult.from_dict "
                    "failed: %s. Raw text: %s",
                    exc,
                    text[:200],
                )

        # Could not parse — wrap raw text as a reject
        return ReviewResult(
            verdict=ReviewVerdict.REJECTED,
            summary="Could not parse Reviewer output as valid ReviewResult JSON.",
            issues=[ReviewIssue(Severity.CRITICAL, "Reviewer returned non-JSON or malformed JSON.")],
            evidence=text.strip() or "(empty response)",
        )

    @staticmethod
    def _calibrate_verdict(
        result: ReviewResult,
        acceptance_criteria: str,
    ) -> ReviewResult:
        """Post-hoc calibration: enforce hard rules that the LLM might violate.

        This is a zero-token, deterministic rule engine. It catches:
        - [HARD] criterion failure assigned severity=minor or suggestion
        - CRITICAL issues present but verdict not rejected
        - MAJOR issues present but verdict less than major_revisions
        """
        import re

        adjustments: list[str] = []
        adjusted_issues = list(result.issues)

        # Step 1: Extract [HARD] and [SOFT] criteria keywords
        hard_texts = re.findall(
            r'\[HARD\]\s*(.+?)(?=\[HARD\]|\[SOFT\]|$)', acceptance_criteria
        )
        if not hard_texts:
            hard_texts = [
                line for line in acceptance_criteria.split('\n')
                if line.strip() and '[SOFT]' not in line
            ]
        soft_texts = re.findall(
            r'\[SOFT\]\s*(.+?)(?=\[HARD\]|\[SOFT\]|$)', acceptance_criteria
        )

        hard_keywords: set[str] = set()
        for hc in hard_texts:
            for w in hc.strip().split()[:4]:
                if len(w) > 3:
                    hard_keywords.add(w.lower())

        soft_keywords: set[str] = set()
        for sc in soft_texts:
            for w in sc.strip().split()[:4]:
                if len(w) > 3:
                    soft_keywords.add(w.lower())

        # Step 2: Upgrade severity for issues referencing [HARD] criteria
        for i, issue in enumerate(adjusted_issues):
            desc_lower = issue.description.lower()
            hits_hard = any(kw in desc_lower for kw in hard_keywords)
            hits_soft = any(kw in desc_lower for kw in soft_keywords)

            if hits_hard and not hits_soft:
                if issue.severity in (Severity.MINOR, Severity.SUGGESTION):
                    old_sev = issue.severity.value
                    adjusted_issues[i] = ReviewIssue(
                        severity=Severity.MAJOR,
                        description=(
                            f"[CALIBRATED: was {old_sev}, upgraded to major "
                            f"(references [HARD] criterion)] "
                            f"{issue.description}"
                        ),
                    )
                    adjustments.append(
                        f"Upgraded issue #{i+1} from {old_sev} -> major"
                    )

        # Step 3: Derive verdict floor from issue severities
        has_critical = any(i.severity == Severity.CRITICAL for i in adjusted_issues)
        has_major = any(i.severity == Severity.MAJOR for i in adjusted_issues)
        has_minor = any(i.severity == Severity.MINOR for i in adjusted_issues)
        has_suggestion = any(i.severity == Severity.SUGGESTION for i in adjusted_issues)
        no_issues = len(adjusted_issues) == 0

        verdict_rank = {
            ReviewVerdict.APPROVED: 0,
            ReviewVerdict.APPROVED_WITH_NOTES: 1,
            ReviewVerdict.MINOR_REVISIONS: 2,
            ReviewVerdict.MAJOR_REVISIONS: 3,
            ReviewVerdict.REJECTED: 4,
        }

        if no_issues:
            floor_verdict = ReviewVerdict.APPROVED
        elif has_critical:
            floor_verdict = ReviewVerdict.REJECTED
        elif has_major:
            floor_verdict = ReviewVerdict.MAJOR_REVISIONS
        elif has_minor:
            floor_verdict = ReviewVerdict.MINOR_REVISIONS
        elif has_suggestion:
            floor_verdict = ReviewVerdict.APPROVED_WITH_NOTES
        else:
            floor_verdict = ReviewVerdict.APPROVED

        if verdict_rank.get(result.verdict, 0) < verdict_rank.get(floor_verdict, 0):
            old_verdict = result.verdict.value
            adjustments.append(
                f"Upgraded verdict from {old_verdict} -> {floor_verdict.value}"
            )
            result.verdict = floor_verdict

        # Step 4: Append calibration note
        if adjustments:
            result.evidence = (result.evidence or "") + (
                "\n\n[CALIBRATION: rule-engine corrections]\n"
                + "\n".join(f"  - {a}" for a in adjustments)
            )

        result.issues = adjusted_issues
        return result
