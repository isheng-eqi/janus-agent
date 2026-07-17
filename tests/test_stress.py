"""
Stress tests for Janus — robustness under abnormal conditions.

NOT unit tests — try to break things with extreme inputs, rapid sequences,
deep nesting, large data, unicode, and concurrent-like access.
"""

import json
import sys
import os
from unittest.mock import MagicMock, patch

# Ensure the janus core is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.protocol import (
    TaskResult,
    TaskSpec,
    TaskStatus,
    Confidence,
    DecompositionRequest,
    SubTask,
)
from core.task_manager import TaskManager, TaskState
from core.worker import Worker, ToolRegistry, ToolDef, create_default_registry
from core.session import Session
from core.gatekeeper import Gatekeeper


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_dummy_task_result(success=True, task_id="task-1"):
    return TaskResult(
        status=TaskStatus.SUCCESS if success else TaskStatus.FAILURE,
        summary="Done" if success else "Failed",
        result="All good" if success else "Something went wrong",
        confidence=Confidence.HIGH,
        worker_id=f"worker-{task_id}",
    )


def _fake_llm_response(content_text):
    """Create a fake OpenAI response with given text content."""
    msg = MagicMock()
    msg.content = content_text
    msg.tool_calls = None
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _fake_llm_tool_call(tool_name, arguments_dict):
    """Create a fake OpenAI response with a tool call."""
    tc = MagicMock()
    tc.id = "call_1"
    tc.type = "function"
    tc.function.name = tool_name
    tc.function.arguments = json.dumps(arguments_dict)

    msg = MagicMock()
    msg.content = None
    msg.tool_calls = [tc]
    # DeepSeek thinking mode: may or may not have reasoning_content
    msg.reasoning_content = None

    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _fake_decompose_response(tasks_list):
    """Create a fake LLM response for the decompose call."""
    return _fake_llm_response(json.dumps(tasks_list))


# ─── Test Suite ──────────────────────────────────────────────────────────────

passed = 0
failed = 0
failures: list[str] = []


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        msg = f"  FAIL: {name}" + (f" — {detail}" if detail else "")
        failures.append(msg)
        print(msg)


# =============================================================================
# TEST 1: Rapid sequential execute() — no state leaks
# =============================================================================
print("\n### TEST 1: Rapid sequential execute()")

try:
    tm = TaskManager()

    # Create a Gatekeeper with a mock worker factory
    def mock_worker_factory(model_override=None):
        w = MagicMock()
        w.run.return_value = _make_dummy_task_result(success=True, task_id="dynamic")
        return w

    gk = Gatekeeper(
        model="deepseek-chat",
        api_key="test-key",
        task_manager=tm,
        worker_factory=mock_worker_factory,
        max_depth=3,
    )

    # Mock the LLM client on the Gatekeeper
    gk._client = MagicMock()

    # Decompose returns 1 task each time
    def make_decompose(i):
        # Return different task_ids per call to avoid dedup issues
        decompose_resp = _fake_decompose_response(
            [
                {
                    "task_id": f"task-{i}",
                    "description": f"Run task {i}",
                    "acceptance_criteria": "Works",
                    "context": "",
                }
            ]
        )
        # Synthesis returns something simple
        synth_resp = _fake_llm_response(f"Synthesis {i}")
        return decompose_resp, synth_resp

    for i in range(10):
        decompose_resp, synth_resp = make_decompose(i)
        gk._client.chat.completions.create.side_effect = [
            decompose_resp,  # decompose
            synth_resp,      # synthesize
        ]
        result = gk.execute(f"Goal {i}")
        check(f"execute #{i+1} returns string", isinstance(result, str))
        check(f"execute #{i+1} non-empty", len(result) > 0)

    # After all 10 executions, verify TaskManager is clean (only last run's tasks)
    # Actually, each execute() resets, so after all 10, TM reflects only run #10
    summary = tm.get_summary()
    # After last execute with a SUCCESS, there should be 1 task completed
    check("TaskManager has 1 completed task after final execute", summary["completed"] == 1)
    check("TaskManager has 0 pending tasks", summary["pending"] == 0)
    check("TaskManager has 0 running tasks", summary["running"] == 0)

    # Verify no state leaks: rejection_counts and decomp_counts are empty
    check("No rejection counts leaked", len(gk._rejection_counts) == 0)
    check("No decomp counts leaked", len(gk._decomp_counts) == 0)

except Exception as e:
    check(f"TEST 1 — no crash: {e}", False, str(e))


# =============================================================================
# TEST 2: Deeply nested decomposition — max_depth=3
# =============================================================================
print("\n### TEST 2: Deeply nested decomposition")

try:
    tm = TaskManager()

    def deep_nest_worker_factory(model_override=None):
        w = MagicMock()
        # At depths 1 and 2, request decomposition.  At depth 3, succeed.
        # We use side_effect with context to track depth... but simpler: always
        # return NEEDS_DECOMPOSITION with well-formed request
        decomp_req = DecompositionRequest(
            reason="Task too complex for single pass",
            sub_tasks=[
                SubTask(
                    id="sub-a",
                    description="Sub-task A — a meaningful description here",
                    rationale="Needed for component A",
                ),
                SubTask(
                    id="sub-b",
                    description="Sub-task B — another meaningful description",
                    rationale="Needed for component B",
                ),
            ],
        )
        w.run.return_value = TaskResult(
            status=TaskStatus.NEEDS_DECOMPOSITION,
            summary="Needs decomposition",
            result="Too complex",
            decomposition_request=decomp_req,
            confidence=Confidence.MEDIUM,
        )
        return w

    gk = Gatekeeper(
        model="deepseek-chat",
        api_key="test-key",
        task_manager=tm,
        worker_factory=deep_nest_worker_factory,
        max_depth=3,
    )
    gk._client = MagicMock()

    # The pattern: decompose → 1 task.  Worker at depth 1 → NEEDS_DECOMPOSITION
    # → sub-tasks at depth 2 → Worker at depth 2 → NEEDS_DECOMPOSITION
    # → sub-tasks at depth 3 → Worker at depth 3 → NEEDS_DECOMPOSITION → REJECTED
    decompose_resp = _fake_decompose_response(
        [
            {
                "task_id": "root-task",
                "description": "Root task for deep nesting test",
                "acceptance_criteria": "Works",
                "context": "",
            }
        ]
    )
    synth_resp = _fake_llm_response("Deep nest synthesis done")
    gk._client.chat.completions.create.side_effect = [decompose_resp, synth_resp]

    result = gk.execute("Deep nesting stress test")
    check("Deep nesting doesn't crash", isinstance(result, str))
    check("Deep nesting returns content", len(result) > 0)

    summary = tm.get_summary()
    check("TaskManager has tasks tracked", summary["total"] > 0)

except Exception as e:
    import traceback
    check(f"TEST 2 — no crash: {e}", False, traceback.format_exc())


# =============================================================================
# TEST 3: Large number of tasks (20+)
# =============================================================================
print("\n### TEST 3: Large number of tasks (20+)")

try:
    tm = TaskManager()

    def multi_worker_factory(model_override=None):
        w = MagicMock()
        w.run.return_value = _make_dummy_task_result(success=True, task_id="multi")
        return w

    gk = Gatekeeper(
        model="deepseek-chat",
        api_key="test-key",
        task_manager=tm,
        worker_factory=multi_worker_factory,
        max_depth=3,
    )
    gk._client = MagicMock()

    NUM_TASKS = 25
    tasks = []
    for i in range(NUM_TASKS):
        tasks.append({
            "task_id": f"bulk-{i}",
            "description": f"Bulk task {i} — a meaningful description here",
            "acceptance_criteria": f"Task {i} completes successfully",
            "context": f"Part of large batch, index {i}",
        })

    decompose_resp = _fake_decompose_response(tasks)
    synth_resp = _fake_llm_response("All 25 tasks done")
    gk._client.chat.completions.create.side_effect = [decompose_resp, synth_resp]

    result = gk.execute("Run 25 tasks")
    check("25 tasks don't crash", isinstance(result, str))

    summary = tm.get_summary()
    check(f"All {NUM_TASKS} tasks dispatched", summary["total"] == NUM_TASKS)
    check("All tasks completed", summary["completed"] == NUM_TASKS)
    check("No pending tasks", summary["pending"] == 0)

except Exception as e:
    import traceback
    check(f"TEST 3 — no crash: {e}", False, traceback.format_exc())


# =============================================================================
# TEST 4: Long strings (10,000+ characters)
# =============================================================================
print("\n### TEST 4: Long strings (10,000+ characters)")

try:
    LONG_STR = "A" * 15000
    ts = TaskSpec(
        task_id="long-test",
        description=LONG_STR[:100] + "...(truncated for readability)",
        acceptance_criteria=LONG_STR[:100] + "...",
        context=LONG_STR,
        depth=1,
    )

    # Verify TaskSpec doesn't crash or truncate
    check("TaskSpec with long context survives creation", ts.context == LONG_STR)
    check("TaskSpec context length preserved", len(ts.context) == 15000)

    # Test TaskManager with long content
    tm2 = TaskManager()
    tid = tm2.add_task(ts)
    check("TaskManager.add_task with long spec", tid == "long-test")

    record = tm2.get_task("long-test")
    check("TaskManager.get_task with long spec", record is not None)
    if record:
        check("Record spec context preserved", len(record.spec.context) == 15000)

    # Test TaskResult with long strings
    tr = TaskResult(
        status=TaskStatus.SUCCESS,
        summary=LONG_STR[:100] + "...",
        result=LONG_STR,
        confidence=Confidence.HIGH,
    )
    d = tr.to_dict()
    check("TaskResult.to_dict with long result", len(d["result"]) == 15000)
    tr2 = TaskResult.from_dict(d)
    check("TaskResult.from_dict roundtrip with long result", len(tr2.result) == 15000)

    # Test HermesClient._parse_output with long output
    from core.hermes_client import HermesClient

    # Create a minimal config for parsing test
    from core.hermes_client import HermesWorkerConfig
    cfg = HermesWorkerConfig(
        id="test-long",
        role="Tester",
        profile="test",
        model="deepseek-chat",
        system_prompt="test",
    )
    # Use static method directly
    result = HermesClient._parse_output(LONG_STR + '{"status": "success", "summary": "ok", "result": "done"}', "w1")
    check("HermesClient._parse_output with long output", result.status == TaskStatus.SUCCESS)
    check("Long output parse result correct", result.summary == "ok")

except Exception as e:
    import traceback
    check(f"TEST 4 — no crash: {e}", False, traceback.format_exc())


# =============================================================================
# TEST 5: Unicode stress (Chinese, emoji, special chars)
# =============================================================================
print("\n### TEST 5: Unicode stress")

try:
    UNICODE_DESC = "任务描述 🦀 日本語テスト \x00\x01\x02 bytes"
    UNICODE_CTX = "\U0001F600 \U0001F4A9 \u00ff \u4e2d\u6587"

    ts = TaskSpec(
        task_id="unicode-test",
        description=UNICODE_DESC,
        acceptance_criteria="验收标准 🎯",
        context=UNICODE_CTX,
        depth=1,
    )
    check("TaskSpec with unicode survives creation", True)

    tm3 = TaskManager()
    tm3.add_task(ts)
    record = tm3.get_task("unicode-test")
    check("TaskManager handles unicode spec", record is not None)

    # Test from_dict with unicode
    tr = TaskResult(
        status=TaskStatus.SUCCESS,
        summary="成功 🦀",
        result="結果 日本語 \u00ff",
        confidence=Confidence.HIGH,
    )
    d = tr.to_dict()
    tr2 = TaskResult.from_dict(d)
    check("TaskResult unicode roundtrip survives", tr2.summary == "成功 🦀")
    check("TaskResult unicode result roundtrip", tr2.result == "結果 日本語 \u00ff")

    # Test _classify with unicode
    gk_dummy = MagicMock()
    session = Session(gk_dummy)
    # Empty history → first input is "new"
    intent = session._classify("你好 🦀")
    check("_classify with unicode returns valid intent", intent == "new")

except Exception as e:
    import traceback
    check(f"TEST 5 — no crash: {e}", False, traceback.format_exc())


# =============================================================================
# TEST 6: Concurrent-like access to TaskManager
# =============================================================================
print("\n### TEST 6: Concurrent-like access (rapid add/query)")

try:
    tm4 = TaskManager()

    # Rapid add and query in tight sequence
    for i in range(100):
        spec = TaskSpec(
            task_id=f"conc-{i}",
            description=f"Concurrent task {i}",
            acceptance_criteria="Fast",
            context="",
            depth=1,
        )
        tid = tm4.add_task(spec)
        # Immediately query
        record = tm4.get_task(tid)
        check(f"Rapid add+get task {i}", record is not None and record.task_id == tid)

    check("All 100 tasks registered in TM", len(tm4._tasks) == 100)

    # Now rapidly mark running + completed interleaved
    for i in range(100):
        tid = f"conc-{i}"
        tm4.mark_running(tid, worker_id=f"w-{i}")

    for i in range(100):
        tid = f"conc-{i}"
        tr = _make_dummy_task_result(success=True, task_id=tid)
        tm4.mark_completed(tid, tr)

    summary = tm4.get_summary()
    check("All 100 tasks completed after rapid transitions", summary["completed"] == 100)
    check("No pending/running after all transitions", summary["pending"] == 0 and summary["running"] == 0)

    # Test invalid transitions don't crash
    try:
        tm4.mark_running("non-existent", worker_id="w-x")
        check("mark_running on non-existent task raises ValueError", False)
    except ValueError as e:
        check("mark_running on non-existent task raises ValueError", "Unknown task_id" in str(e))

    # Try double-completing
    try:
        tm4.mark_completed("conc-0", _make_dummy_task_result())
        check("Double-mark_completed raises ValueError", False)
    except ValueError as e:
        check("Double-mark_completed raises ValueError", "Invalid transition" in str(e))

    # Reset and verify
    tm4.reset()
    check("reset clears all tasks", len(tm4._tasks) == 0)
    summary = tm4.get_summary()
    check("reset yields zero summary", summary["total"] == 0)

except Exception as e:
    import traceback
    check(f"TEST 6 — no crash: {e}", False, traceback.format_exc())


# ─── Summary ─────────────────────────────────────────────────────────────────

print(f"\n{'='*60}")
print(f"RESULTS: {passed} passed, {failed} failed out of {passed + failed} checks")
if failures:
    print(f"\nFAILURES:")
    for f in failures:
        print(f"  - {f}")
else:
    print("All stress tests passed!")
print(f"{'='*60}")
