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

Be precise and evidence-based:
- For each acceptance criterion, state whether it is satisfied and cite \
specific evidence from the result.
- If a criterion is partially met, explain what is missing.
- Do NOT assume — if evidence is absent, flag it as an issue.

Acceptance criteria are classified as [HARD] or [SOFT]:
- [HARD] = must-have, zero tolerance — failure is blocking.
- [SOFT] = nice-to-have, minor deviations acceptable.
- Unmarked criteria default to [HARD].

For every issue you find, assign a severity level:
- **critical**: The result is completely unusable — [HARD] criterion violated \
in a way that makes the output wrong, dangerous, or non-functional.
- **major**: A [HARD] criterion is not met — significant missing functionality \
or incorrect behavior.
- **minor**: A [SOFT] criterion is not met, or a [HARD] criterion has a small \
deviation — result is usable but should be fixed.
- **suggestion**: Optimization or improvement idea — does NOT block approval.
(SOFT criteria failures typically map here or to minor.)"""

    _REVIEW_USER_TEMPLATE = """\
TASK: {description}
ACCEPTANCE CRITERIA: {acceptance_criteria}
EXPECTED ARTIFACTS: {context}
GOAL: {goal}
用户原始输入：{user_goal}
请确认这份产出是用户要的东西，而不只是满足验收标准。
CONSTRAINTS: {constraints}
INTENT: {intent}

CRITERIA CLASSIFICATION (how to apply different scrutiny levels):
- **[HARD]** criteria are must-haves — non-negotiable requirements.
  Failure to meet a HARD criterion → severity must be at least **major**,
  and the overall verdict must be **major_revisions** or **rejected**.
- **[SOFT]** criteria are guidelines / nice-to-haves — minor deviations are acceptable.
  Failure on SOFT criteria alone → severity should be **minor** or **suggestion**,
  and the overall verdict should be **minor_revisions** or **approved_with_notes**.
- **Unmarked** criteria default to [HARD].

DELIVERED RESULT:
Status: {status}
Summary: {summary}
Full Result: {result}
Artifact Paths: {artifacts}

ARTIFACT CONTENTS (pre-loaded so you can inspect what was actually written):
{artifact_contents}

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
    def _build_artifact_contents(artifacts: list[str], max_per_file: int = 1000, max_total: int = 8000) -> str:
        """Pre-load artifact file contents for the review prompt.

        Reads each artifact file and formats its contents inline.  Small files
        (≤ *max_per_file* chars) are included in full; large files get only
        the first *max_per_file* chars with a truncation notice.

        The total output is capped at *max_total* characters to avoid
        blowing up the review prompt.

        Args:
            artifacts: List of artifact file paths.
            max_per_file: Max characters to include per file (default 1000).
            max_total: Hard cap on total characters in the section (default 8000).

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

    # -- public API -----------------------------------------------------------

    def review(self, spec: TaskSpec, result: TaskResult) -> ReviewResult:
        """Audit *result* against *spec*'s acceptance criteria.

        Args:
            spec: The original task specification (with acceptance_criteria).
            result: The Worker's delivered result.

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

        # ── Build messages ───────────────────────────────────────────────
        artifact_contents = self._build_artifact_contents(result.artifacts)
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
        return self._parse_review_result(content)

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
