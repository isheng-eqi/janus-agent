"""Minimal reproduction of the reviewer test issue."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest.mock import MagicMock
from core.reviewer import Reviewer, ReviewVerdict
from core.protocol import TaskSpec, TaskResult, TaskStatus, Confidence

# Setup
reviewer = Reviewer(model="deepseek-chat", api_key="fake-key")
reviewer._client = MagicMock()

msg = MagicMock()
msg.content = '{"verdict": "approved", "summary": "All criteria met.", "issues": [], "evidence": "OK"}'
choice = MagicMock()
choice.message = msg
resp = MagicMock()
resp.choices = [choice]
reviewer._client.chat.completions.create.return_value = resp

spec = TaskSpec(
    task_id="t1",
    description="Write a function",
    acceptance_criteria="[HARD] Must return int.",
    context="test",
)
result = TaskResult(
    status=TaskStatus.SUCCESS,
    summary="Done",
    result="Created function",
    artifacts=[],
    confidence=Confidence.HIGH,
)

review = reviewer.review(spec, result)
print(f"verdict: {review.verdict}")
print(f"summary: {review.summary}")
print(f"evidence: {review.evidence[:200]}")
