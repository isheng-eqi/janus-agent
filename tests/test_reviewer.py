"""
Unit tests for janus.core.reviewer — ReviewVerdict, Severity, ReviewIssue,
ReviewResult, and Reviewer.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import MagicMock, patch

from core.reviewer import (
    ReviewVerdict,
    Severity,
    ReviewIssue,
    ReviewResult,
    Reviewer,
)
from core.protocol import TaskSpec, TaskResult, TaskStatus, Confidence


def _fake_llm_client(json_content: str):
    """Create a fake OpenAI client that returns the given JSON content."""
    mock = MagicMock()
    msg = MagicMock()
    msg.content = json_content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    mock.chat.completions.create.return_value = resp
    return mock


# ============================================================================
# ReviewVerdict Tests
# ============================================================================

class TestReviewVerdict(unittest.TestCase):
    """Tests for ReviewVerdict enum."""

    def test_all_values_exist(self):
        self.assertEqual(ReviewVerdict.APPROVED.value, "approved")
        self.assertEqual(ReviewVerdict.APPROVED_WITH_NOTES.value, "approved_with_notes")
        self.assertEqual(ReviewVerdict.MINOR_REVISIONS.value, "minor_revisions")
        self.assertEqual(ReviewVerdict.MAJOR_REVISIONS.value, "major_revisions")
        self.assertEqual(ReviewVerdict.REJECTED.value, "rejected")

    def test_from_string(self):
        self.assertEqual(ReviewVerdict("approved"), ReviewVerdict.APPROVED)
        self.assertEqual(ReviewVerdict("rejected"), ReviewVerdict.REJECTED)


# ============================================================================
# Severity Tests
# ============================================================================

class TestSeverity(unittest.TestCase):
    """Tests for Severity enum."""

    def test_all_values_exist(self):
        self.assertEqual(Severity.CRITICAL.value, "critical")
        self.assertEqual(Severity.MAJOR.value, "major")
        self.assertEqual(Severity.MINOR.value, "minor")
        self.assertEqual(Severity.SUGGESTION.value, "suggestion")

    def test_from_string(self):
        self.assertEqual(Severity("critical"), Severity.CRITICAL)
        self.assertEqual(Severity("suggestion"), Severity.SUGGESTION)


# ============================================================================
# ReviewIssue Tests
# ============================================================================

class TestReviewIssue(unittest.TestCase):
    """Tests for ReviewIssue dataclass."""

    def test_create_and_serialize(self):
        issue = ReviewIssue(severity=Severity.MAJOR, description="Missing test coverage")
        self.assertEqual(issue.severity, Severity.MAJOR)
        self.assertEqual(issue.description, "Missing test coverage")

        d = issue.to_dict()
        self.assertEqual(d["severity"], "major")
        self.assertEqual(d["description"], "Missing test coverage")

    def test_from_dict(self):
        d = {"severity": "critical", "description": "Security vulnerability"}
        issue = ReviewIssue.from_dict(d)
        self.assertEqual(issue.severity, Severity.CRITICAL)
        self.assertEqual(issue.description, "Security vulnerability")

    def test_all_severities_serialize(self):
        for sev in Severity:
            issue = ReviewIssue(severity=sev, description=f"Issue at {sev.value}")
            d = issue.to_dict()
            restored = ReviewIssue.from_dict(d)
            self.assertEqual(restored.severity, sev)
            self.assertEqual(restored.description, f"Issue at {sev.value}")


# ============================================================================
# ReviewResult Tests
# ============================================================================

class TestReviewResult(unittest.TestCase):
    """Tests for ReviewResult dataclass."""

    def test_passed_for_approved(self):
        result = ReviewResult(verdict=ReviewVerdict.APPROVED, summary="Perfect")
        self.assertTrue(result.passed)
        self.assertFalse(result.is_blocking)

    def test_passed_for_approved_with_notes(self):
        result = ReviewResult(verdict=ReviewVerdict.APPROVED_WITH_NOTES, summary="Good with notes")
        self.assertTrue(result.passed)
        self.assertFalse(result.is_blocking)

    def test_not_passed_for_rejected(self):
        result = ReviewResult(verdict=ReviewVerdict.REJECTED, summary="Bad")
        self.assertFalse(result.passed)
        self.assertTrue(result.is_blocking)

    def test_not_passed_for_major_revisions(self):
        result = ReviewResult(verdict=ReviewVerdict.MAJOR_REVISIONS, summary="Needs work")
        self.assertFalse(result.passed)
        self.assertTrue(result.is_blocking)

    def test_not_passed_for_minor_revisions(self):
        result = ReviewResult(verdict=ReviewVerdict.MINOR_REVISIONS, summary="Small fixes")
        self.assertFalse(result.passed)

    def test_blocking_issues_filter(self):
        result = ReviewResult(
            verdict=ReviewVerdict.MAJOR_REVISIONS,
            summary="Issues found",
            issues=[
                ReviewIssue(Severity.CRITICAL, "Security hole"),
                ReviewIssue(Severity.MAJOR, "Missing feature"),
                ReviewIssue(Severity.MINOR, "Style issue"),
                ReviewIssue(Severity.SUGGESTION, "Could be faster"),
            ],
        )
        blocking = result.blocking_issues
        self.assertEqual(len(blocking), 3)  # CRITICAL, MAJOR, MINOR (not SUGGESTION)

    def test_has_critical(self):
        result = ReviewResult(
            verdict=ReviewVerdict.REJECTED,
            summary="Critical",
            issues=[ReviewIssue(Severity.CRITICAL, "Fatal")],
        )
        self.assertTrue(result.has_critical)

        result2 = ReviewResult(
            verdict=ReviewVerdict.MINOR_REVISIONS,
            summary="Minor only",
            issues=[ReviewIssue(Severity.MINOR, "Small")],
        )
        self.assertFalse(result2.has_critical)

    def test_to_dict_and_from_dict(self):
        original = ReviewResult(
            verdict=ReviewVerdict.MINOR_REVISIONS,
            summary="Needs small fixes",
            issues=[
                ReviewIssue(Severity.MAJOR, "Broken test"),
                ReviewIssue(Severity.MINOR, "Formatting"),
            ],
            evidence="Tests: 5/6 pass. Formatting inconsistent.",
        )
        d = original.to_dict()
        restored = ReviewResult.from_dict(d)
        self.assertEqual(restored.verdict, original.verdict)
        self.assertEqual(restored.summary, original.summary)
        self.assertEqual(len(restored.issues), 2)
        self.assertEqual(restored.issues[0].severity, Severity.MAJOR)
        self.assertEqual(restored.evidence, original.evidence)

    def test_from_dict_legacy_status(self):
        """Backward-compat: old 'status': 'pass' / 'fail' maps to verdict."""
        d = {"status": "pass", "summary": "OK", "issues": [], "evidence": ""}
        result = ReviewResult.from_dict(d)
        self.assertEqual(result.verdict, ReviewVerdict.APPROVED)

        d2 = {"status": "fail", "summary": "Bad", "issues": [], "evidence": ""}
        result2 = ReviewResult.from_dict(d2)
        self.assertEqual(result2.verdict, ReviewVerdict.REJECTED)

    def test_from_dict_legacy_string_issues(self):
        """Legacy string-only issues default to MAJOR severity."""
        d = {
            "verdict": "major_revisions",
            "summary": "Needs work",
            "issues": ["Missing file", "Incorrect output"],
            "evidence": "Files not found.",
        }
        result = ReviewResult.from_dict(d)
        self.assertEqual(len(result.issues), 2)
        self.assertEqual(result.issues[0].severity, Severity.MAJOR)

    def test_from_dict_malformed_issue_fallback(self):
        d = {
            "verdict": "rejected",
            "summary": "Bad",
            "issues": [{"bad_key": "no severity"}],
            "evidence": "",
        }
        result = ReviewResult.from_dict(d)
        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].severity, Severity.MAJOR)  # Fallback


# ============================================================================
# Reviewer Tests
# ============================================================================

class TestReviewer(unittest.TestCase):
    """Tests for Reviewer class."""

    def _make_reviewer(self):
        return Reviewer(model="deepseek-chat", api_key="fake-key")

    def _make_spec(self):
        return TaskSpec(
            task_id="t1",
            description="Write a function",
            acceptance_criteria="[HARD] Must return int. [SOFT] Should be fast.",
            context="Python project",
            goal="Build library",
            constraints="Use Python 3.10+",
            intent="Core utility",
        )

    def _make_success_result(self):
        return TaskResult(
            status=TaskStatus.SUCCESS,
            summary="Done",
            result="Created factorial function",
            artifacts=["/tmp/factorial.py"],
            confidence=Confidence.HIGH,
            worker_id="worker-1",
        )

    def _make_failure_result(self):
        return TaskResult(
            status=TaskStatus.FAILURE,
            summary="Crashed",
            result="RuntimeError: division by zero",
            confidence=Confidence.LOW,
            worker_id="worker-1",
        )

    def test_review_failure_auto_rejects(self):
        """Worker FAILURE result should auto-reject without LLM call."""
        reviewer = self._make_reviewer()
        spec = self._make_spec()
        result = self._make_failure_result()

        review = reviewer.review(spec, result)
        self.assertEqual(review.verdict, ReviewVerdict.REJECTED)
        self.assertIn("self-reported FAILURE", review.summary)
        self.assertEqual(len(review.issues), 1)
        self.assertEqual(review.issues[0].severity, Severity.CRITICAL)

    def test_review_api_error_returns_rejected(self):
        """API failure returns REJECTED with error info."""
        reviewer = self._make_reviewer()
        reviewer._client = MagicMock()
        reviewer._client.chat.completions.create.side_effect = RuntimeError("API down")

        spec = self._make_spec()
        result = self._make_success_result()

        review = reviewer.review(spec, result)
        self.assertEqual(review.verdict, ReviewVerdict.REJECTED)
        self.assertIn("RuntimeError", review.summary)

    def test_review_success_approved(self):
        """Happy path: LLM returns APPROVED verdict."""
        reviewer = self._make_reviewer()
        reviewer._client = _fake_llm_client(
            '{"verdict": "approved", "summary": "All criteria met.", '
            '"issues": [], "evidence": "Function returns int correctly."}'
        )

        spec = self._make_spec()
        result = TaskResult(
            status=TaskStatus.SUCCESS,
            summary="Done",
            result="Created factorial function",
            artifacts=[],
            confidence=Confidence.HIGH,
            worker_id="worker-1",
        )
        review = reviewer.review(spec, result)

        self.assertEqual(review.verdict, ReviewVerdict.APPROVED)
        self.assertTrue(review.passed)

    def test_review_with_issues(self):
        """LLM returns issues with various severities."""
        reviewer = self._make_reviewer()
        reviewer._client = _fake_llm_client(
            '{"verdict": "major_revisions", "summary": "Needs fixes", '
            '"issues": ['
            '{"severity": "critical", "description": "Returns wrong type"}, '
            '{"severity": "major", "description": "Missing edge case"}, '
            '{"severity": "minor", "description": "No docstring"}'
            '], "evidence": "Type is str, not int."}'
        )

        spec = self._make_spec()
        result = TaskResult(
            status=TaskStatus.SUCCESS,
            summary="Done",
            result="Created factorial function",
            artifacts=[],
            confidence=Confidence.HIGH,
            worker_id="worker-1",
        )
        review = reviewer.review(spec, result)

        self.assertEqual(review.verdict, ReviewVerdict.MAJOR_REVISIONS)
        self.assertEqual(len(review.issues), 3)
        self.assertTrue(review.has_critical)
        self.assertEqual(len(review.blocking_issues), 3)


# ============================================================================
# Reviewer._read_artifact Tests
# ============================================================================

class TestReviewerReadArtifact(unittest.TestCase):
    """Tests for Reviewer._read_artifact."""

    def test_access_denied_for_unapproved_path(self):
        result = Reviewer._read_artifact("/tmp/secret.txt", allowed_artifacts=[])
        self.assertIn("Access denied", result)

    def test_file_not_found(self):
        result = Reviewer._read_artifact("/nonexistent/path.txt", allowed_artifacts=["/nonexistent/path.txt"])
        self.assertIn("not found", result)


# ============================================================================
# Reviewer._build_artifact_contents Tests
# ============================================================================

class TestReviewerBuildArtifactContents(unittest.TestCase):
    """Tests for Reviewer._build_artifact_contents."""

    def test_empty_artifacts(self):
        result = Reviewer._build_artifact_contents([])
        self.assertIn("no artifact files", result)

    def test_non_existent_artifacts(self):
        result = Reviewer._build_artifact_contents(["/nonexistent/file.txt"])
        self.assertIn("not found on disk", result)


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    unittest.main()
