"""Debug script for reviewer test failures."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.reviewer import Reviewer, ReviewVerdict, ReviewResult
from core.protocol import TaskSpec, TaskResult, TaskStatus, Confidence
from unittest.mock import MagicMock

json_text = '{"verdict": "approved", "summary": "All criteria met.", "issues": [], "evidence": "Function returns int correctly."}'

# Test _parse_review_result directly
result = Reviewer._parse_review_result(json_text)
print(f'Direct parse: verdict={result.verdict}, passed={result.passed}')

# Test _fake_llm_client
def _fake_llm_client(json_content):
    mock = MagicMock()
    msg = MagicMock()
    msg.content = json_content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    mock.chat.completions.create.return_value = resp
    return mock

client = _fake_llm_client(json_text)
resp = client.chat.completions.create(model='foo', messages=[])
print(f'Mock response choices: {len(resp.choices)}')
print(f'Message content: {resp.choices[0].message.content[:80] if resp.choices[0].message.content else "(None)"}')

# Now test full review but monkey-patch _build_artifact_contents to be safe
reviewer = Reviewer(model='deepseek-chat', api_key='fake-key')
reviewer._client = client

spec = TaskSpec(
    task_id='t1', description='Write a function',
    acceptance_criteria='[HARD] Must return int. [SOFT] Should be fast.',
    context='Python project', goal='Build library',
    constraints='Use Python 3.10+', intent='Core utility',
)
result = TaskResult(
    status=TaskStatus.SUCCESS, summary='Done',
    result='Created factorial function', artifacts=[],
    confidence=Confidence.HIGH, worker_id='worker-1',
)

# Diagnose step by step
artifact_contents = reviewer._build_artifact_contents([])
print(f'Artifact contents: [{artifact_contents[:100]}]')

# Check user_content manually
uc = reviewer._REVIEW_USER_TEMPLATE
uc = uc.replace("{description}", spec.description)
uc = uc.replace("{acceptance_criteria}", spec.acceptance_criteria)
uc = uc.replace("{context}", spec.context)
uc = uc.replace("{goal}", spec.goal)
uc = uc.replace("{constraints}", spec.constraints)
uc = uc.replace("{intent}", spec.intent)
uc = uc.replace("{status}", result.status.value)
uc = uc.replace("{summary}", result.summary)
uc = uc.replace("{result}", result.result)
uc = uc.replace("{artifacts}", "(none)")
uc = uc.replace("{artifact_contents}", artifact_contents)
uc = uc.replace("{{", "{").replace("}}", "}")
print(f'User content length: {len(uc)}')
print(f'User content last 200 chars: [...{uc[-200:]}]')

# Call LLM
response = reviewer._client.chat.completions.create(model=reviewer._model, messages=[{'role': 'system', 'content': 'x'}, {'role': 'user', 'content': uc}])
content = response.choices[0].message.content
print(f'LLM content type: {type(content).__name__}')
print(f'LLM content value: {str(content)[:200] if content else "(None/Empty)"}')

# Parse
parsed = Reviewer._parse_review_result(content if content else "")
print(f'Final verdict: {parsed.verdict}')
print(f'Final passed: {parsed.passed}')
