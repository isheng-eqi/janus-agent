"""
Fuzz tests for Janus — random/malformed inputs to every parsing/execution boundary.

Goal: verify that no malformed input causes an unexpected crash (AttributeError,
TypeError, etc.).  Every public entry point must degrade gracefully.
"""

import json
import random
import string
import sys
import os

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
from unittest.mock import MagicMock


# ─── Helpers ─────────────────────────────────────────────────────────────────

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


def random_string(min_len=0, max_len=200, include_binary=False):
    """Generate a random string."""
    length = random.randint(min_len, max_len)
    if include_binary and random.random() < 0.3:
        # Include some binary bytes
        chars = string.printable + ''.join(chr(i) for i in range(256) if chr(i) not in string.printable)
        return ''.join(random.choice(chars) for _ in range(length))
    return ''.join(random.choice(string.printable) for _ in range(length))


def random_dict(depth=0, max_depth=3):
    """Generate a random dict."""
    if depth >= max_depth:
        return random.choice([random_string(), random.randint(0, 100), random.random(), True, False, None, [], {}])
    
    d = {}
    num_keys = random.randint(0, 8)
    for _ in range(num_keys):
        key = random_string(1, 20)
        if random.random() < 0.3:
            d[key] = random_dict(depth + 1, max_depth) if random.random() < 0.5 else random_string(0, 500)
        elif random.random() < 0.3:
            d[key] = random.randint(-1000, 1000)
        elif random.random() < 0.2:
            d[key] = None
        elif random.random() < 0.1:
            d[key] = []
        else:
            d[key] = random.random()
    return d


# ─── Fuzz Test Suite ─────────────────────────────────────────────────────────


# =============================================================================
# FUZZ 1: Random JSON to TaskResult.from_dict()
# =============================================================================
print("\n### FUZZ 1: TaskResult.from_dict() with random dicts")

error_types_allowed = {KeyError, TypeError, ValueError, AttributeError}
unexpected_errors = []

for i in range(500):
    d = random_dict()
    try:
        result = TaskResult.from_dict(d)
        # If it succeeds, verify it's a valid TaskResult
        check(f"from_dict #{i} returns TaskResult", isinstance(result, TaskResult),
              f"got {type(result).__name__}")
        # Verify it has expected attributes
        check(f"from_dict #{i} has status", hasattr(result, 'status'))
        check(f"from_dict #{i} has summary", hasattr(result, 'summary'))
    except Exception as e:
        if type(e) in error_types_allowed:
            pass  # Expected
        else:
            unexpected_errors.append((i, type(e).__name__, str(e), d))

if unexpected_errors:
    print(f"\n  UNEXPECTED ERRORS ({len(unexpected_errors)}):")
    for idx, etype, emsg, inp in unexpected_errors[:10]:
        print(f"    #{idx}: {etype}: {emsg[:100]}")
    check("No unexpected error types from from_dict()", False,
          f"{len(unexpected_errors)} unexpected errors")
else:
    check("No unexpected error types from from_dict() (500 trials)", True)


# =============================================================================
# FUZZ 2: Malformed LLM output to Worker._parse_result
# =============================================================================
print("\n### FUZZ 2: Worker._parse_result with malformed strings")

malformed_inputs = [
    # Plain strings
    "",
    "not json",
    "just some text",
    # Partial JSON
    "{",
    "}",
    '{"status": "success"',
    '{"status":',
    # Malformed JSON
    '{"status": success}',  # missing quotes
    '{"status": "success",}',  # trailing comma
    "{'status': 'success'}",  # single quotes
    '{"status": "success", "summary": }',  # missing value
    # Deeply nested JSON
    '{"a":' + '{"b":' * 50 + '"x"' + '}' * 50,
    # Empty-like
    "null",
    "undefined",
    "NaN",
    # Binary garbage
    "\x00\x01\x02\x03",
    # Very long non-JSON
    "x" * 10000,
    # JSON inside fences with content around
    'Some text ```json {"status": "success", "summary": "ok", "result": "done"}``` more text',
    # Multiple JSON objects
    '{"a":1} {"b":2}',
    # Valid TaskResult JSON
    '{"status": "success", "summary": "ok", "result": "done"}',
    # Valid but missing fields
    '{"status": "success"}',
    # NEEDS_DECOMPOSITION missing sub_tasks
    '{"status": "needs_decomposition", "summary": "need", "result": "more"}',
    # NEEDS_DECOMPOSITION with empty sub_tasks
    '{"status": "needs_decomposition", "summary": "need", "result": "more", "decomposition_request": {"reason": "why", "sub_tasks": []}}',
]

for idx, inp in enumerate(malformed_inputs):
    try:
        result = Worker._parse_result(inp)
        check(f"_parse_result #{idx} returns TaskResult: {inp[:50]!r}",
              isinstance(result, TaskResult),
              f"got {type(result).__name__}")
        check(f"_parse_result #{idx} has valid status",
              isinstance(result.status, TaskStatus))
    except Exception as e:
        check(f"_parse_result #{idx} no crash: {inp[:50]!r}",
              False,
              f"{type(e).__name__}: {e}")


# Additional random fuzz for _parse_result
print("  (random fuzz for _parse_result...)")
for i in range(200):
    inp = random_string(0, 1000, include_binary=True)
    try:
        result = Worker._parse_result(inp)
        check(f"_parse_result random #{i} returns TaskResult",
              isinstance(result, TaskResult),
              f"got {type(result).__name__}")
    except Exception as e:
        check(f"_parse_result random #{i} no crash: {inp[:50]!r}",
              False,
              f"{type(e).__name__}: {e}")


# =============================================================================
# FUZZ 3: Random tool arguments to ToolRegistry.execute()
# =============================================================================
print("\n### FUZZ 3: ToolRegistry.execute() with random arguments")

registry = create_default_registry()

# Fuzz known tools with random args
known_tools = ["read_file", "write_file", "terminal", "web_search"]
random_arg_names = [
    "path", "content", "command", "query",  # real params
    "foo", "bar", "baz", "xyz", "",  # unknown params
    "url", "file", "q", "search",  # aliases
    None,  # weird
]

for i in range(300):
    tool_name = random.choice(known_tools + ["nonexistent_tool", "", "read_file"])
    args = {}
    num_args = random.randint(0, 5)
    for _ in range(num_args):
        key = random.choice(random_arg_names)
        if key is None:
            continue
        if random.random() < 0.3:
            args[key] = random.randint(0, 10000)
        elif random.random() < 0.3:
            args[key] = random_string(0, 500, include_binary=True)
        elif random.random() < 0.2:
            args[key] = None
        else:
            args[key] = True

    try:
        result = registry.execute(tool_name, args)
        check(f"ToolRegistry.execute random #{i} returns string",
              isinstance(result, str),
              f"got {type(result).__name__}")
    except Exception as e:
        check(f"ToolRegistry.execute random #{i} no crash: {tool_name}({args})",
              False,
              f"{type(e).__name__}: {e}")


# Edge cases for tool execution
edge_cases = [
    # (name, args, description)
    ("read_file", {}, "no args"),
    ("read_file", {"path": None}, "None path"),
    ("write_file", {"path": "test", "content": None}, "None content"),
    ("terminal", {"command": "x" * 10000}, "very long command"),
    ("terminal", {"command": "\x00\x01"}, "binary command"),
    ("", {}, "empty tool name"),
]

for name, args, desc in edge_cases:
    try:
        result = registry.execute(name, args)
        check(f"ToolRegistry.execute edge '{desc}' returns string",
              isinstance(result, str),
              f"got {type(result).__name__}")
    except Exception as e:
        check(f"ToolRegistry.execute edge '{desc}' no crash",
              False,
              f"{type(e).__name__}: {e}")


# =============================================================================
# FUZZ 4: HermesClient._parse_output fuzz
# =============================================================================
print("\n### FUZZ 4: HermesClient._parse_output fuzz")

from core.hermes_client import HermesClient

stdout_fuzz_inputs = [
    # Empty/none-like
    "",
    "   ",
    # Valid JSON
    '{"status": "success", "summary": "ok", "result": "works"}',
    '{"status": "failure", "summary": "bad", "result": "error happened"}',
    # Partial JSON
    '{"status": "success"',
    # JSON in fences
    'some preamble text\n```json\n{"status": "success", "summary": "ok", "result": "yay"}\n```\npost text',
    # Multiple JSON objects — should find last one with status
    '{"a":1}\n{"status": "success", "summary": "later", "result": "this is the one"}',
    # Deeply nested
    '{"status": "success", "summary": "deep", "result": "ok", "nested": {"a": {"b": {"c": [1,2,3]}}}}',
    # Binary garbage with valid JSON at end
    '\x00\x01{"status": "success", "summary": "survived", "result": "ok"}',
    # Very long string with JSON at end
    "x" * 5000 + '{"status": "success", "summary": "long", "result": "found it"}',
    # Total garbage
    "not json at all just random text",
    # JSON without status
    '{"foo": "bar", "baz": 123}',
    # status but wrong type
    '{"status": 123, "summary": "wrong", "result": "bad"}',
    # Valid JSON but confidence as number
    '{"status": "success", "summary": "ok", "result": "done", "confidence": 5}',
]

for idx, inp in enumerate(stdout_fuzz_inputs):
    try:
        result = HermesClient._parse_output(inp, worker_id=f"fuzz-{idx}")
        check(f"_parse_output #{idx} returns TaskResult: {inp[:50]!r}",
              isinstance(result, TaskResult),
              f"got {type(result).__name__}")
    except Exception as e:
        check(f"_parse_output #{idx} no crash: {inp[:50]!r}",
              False,
              f"{type(e).__name__}: {e}")

# Random fuzz
print("  (random fuzz for _parse_output...)")
for i in range(200):
    inp = random_string(0, 2000, include_binary=True)
    try:
        result = HermesClient._parse_output(inp, worker_id=f"rand-{i}")
        check(f"_parse_output random #{i} returns TaskResult",
              isinstance(result, TaskResult),
              f"got {type(result).__name__}")
    except Exception as e:
        check(f"_parse_output random #{i} no crash",
              False,
              f"{type(e).__name__}: {e}")


# =============================================================================
# FUZZ 5: Session._classify fuzz
# =============================================================================
print("\n### FUZZ 5: Session._classify fuzz")

# Create a session with some history to test all branches
gk_dummy = MagicMock()
session_no_history = Session(gk_dummy)

session_with_history = Session(gk_dummy)
session_with_history._history = [
    {"role": "user", "content": "previous task"},
    {"role": "assistant", "content": "previous result"},
]

classify_inputs = [
    # No session / empty history
    ("hello", session_no_history, "new"),
    ("", session_no_history, "new"),
    # Explicit new markers
    ("新任务", session_with_history, "new"),
    ("reset everything", session_with_history, "new"),
    ("start over please", session_with_history, "new"),
    ("重来", session_with_history, "new"),
    # Question markers
    ("怎么做到的？", session_with_history, "question"),
    ("why did that happen", session_with_history, "question"),
    ("what is the result", session_with_history, "question"),
    ("这是为什么？", session_with_history, "question"),
    # Modify markers
    ("改一下颜色", session_with_history, "modify"),
    ("修改这里", session_with_history, "modify"),
    ("换成红色", session_with_history, "modify"),
    ("不要这样", session_with_history, "modify"),
    ("change the color", session_with_history, "modify"),
    ("instead use blue", session_with_history, "modify"),
    # Continue markers
    ("继续", session_with_history, "continue"),
    ("然后呢", session_with_history, "continue"),
    ("接着做", session_with_history, "continue"),
    ("下一步", session_with_history, "continue"),
    ("go on", session_with_history, "continue"),
    ("next step", session_with_history, "continue"),
    # Fallback / edge cases
    ("random text with no markers", session_with_history, "new"),
    ("你好世界", session_with_history, "new"),
    ("", session_with_history, "new"),
    # Unicode
    ("🦀 怎么办", session_with_history, "question"),
    ("日本語でお願いします", session_with_history, "new"),
    # Very long strings
    ("a" * 10000, session_with_history, "new"),
    # Mixed markers — question wins?  Actually, order matters: question is checked before modify
    ("怎么改", session_with_history, "question"),  # "怎么" (question) checked first
    ("改成什么", session_with_history, "modify"),  # "改成" (modify) → but "什么" is question? No, "什么" is not in question_markers. "改成" contains "改" so modify wins
]

for idx, (inp, session, expected_intent) in enumerate(classify_inputs):
    try:
        result = session._classify(inp)
        check(f"_classify #{idx}: {inp[:40]!r} → '{result}' (expected '{expected_intent}')",
              result == expected_intent,
              f"got '{result}'")
        # Also check it's a valid intent string
        check(f"_classify #{idx} returns valid intent string",
              result in ("new", "modify", "question", "continue"))
    except Exception as e:
        check(f"_classify #{idx} no crash: {inp[:40]!r}",
              False,
              f"{type(e).__name__}: {e}")

# Random fuzz
print("  (random fuzz for _classify...)")
for i in range(200):
    inp = random_string(0, 500, include_binary=True)
    sess = random.choice([session_no_history, session_with_history])
    try:
        result = sess._classify(inp)
        check(f"_classify random #{i} returns valid intent",
              result in ("new", "modify", "question", "continue"),
              f"got '{result}' from {inp[:50]!r}")
    except Exception as e:
        check(f"_classify random #{i} no crash",
              False,
              f"{type(e).__name__}: {e}")


# =============================================================================
# FUZZ 6: TaskManager state machine fuzz
# =============================================================================
print("\n### FUZZ 6: TaskManager state machine fuzz")

# Pre-create some tasks that exist
pre_existing_ids = ["pre-0", "pre-1", "pre-2", "pre-3", "pre-4"]
# Random IDs that may or may not exist
all_ids = pre_existing_ids + [f"rand-{i}" for i in range(20)]

# Define valid sequences and random operations
operations = ["add", "mark_running", "mark_completed", "mark_failed", "get_task", "get_result", "get_summary", "all_completed", "reset"]

for trial in range(100):
    tm = TaskManager()
    
    # Pre-populate
    for pid in pre_existing_ids:
        spec = TaskSpec(
            task_id=pid,
            description=f"Pre-existing task {pid}",
            acceptance_criteria="Works",
            context="",
            depth=1,
        )
        tm.add_task(spec)
    
    # Run a random sequence of operations
    for step in range(random.randint(5, 30)):
        op = random.choice(operations)
        
        try:
            if op == "add":
                tid = f"fuzz-add-{random.randint(0, 100)}"
                spec = TaskSpec(
                    task_id=tid,
                    description=random_string(1, 100),
                    acceptance_criteria=random_string(0, 50),
                    context=random_string(0, 200),
                    depth=random.randint(1, 5),
                )
                tm.add_task(spec)
            
            elif op == "mark_running":
                tid = random.choice(all_ids)
                try:
                    tm.mark_running(tid, worker_id=f"w-{random.randint(0, 10)}")
                except ValueError:
                    pass  # Expected for non-existent or wrong state
            
            elif op == "mark_completed":
                tid = random.choice(all_ids)
                tr = TaskResult(
                    status=TaskStatus.SUCCESS,
                    summary=random_string(1, 50),
                    result=random_string(0, 200),
                    confidence=random.choice(list(Confidence)),
                )
                try:
                    tm.mark_completed(tid, tr)
                except ValueError:
                    pass  # Expected for wrong state
            
            elif op == "mark_failed":
                tid = random.choice(all_ids)
                try:
                    tm.mark_failed(tid, random_string(0, 100))
                except ValueError:
                    pass
            
            elif op == "get_task":
                tid = random.choice(all_ids)
                record = tm.get_task(tid)
                # Can be None
            
            elif op == "get_result":
                tid = random.choice(all_ids)
                result = tm.get_result(tid)
                # Can be None
            
            elif op == "get_summary":
                summary = tm.get_summary()
                check(f"get_summary trial {trial} step {step} returns dict",
                      isinstance(summary, dict))
                # Check all expected keys present
                for key in ("total", "pending", "running", "completed", "failed"):
                    check(f"get_summary trial {trial} has key '{key}'",
                          key in summary)
                # Verify counts add up
                total_calc = (
                    summary["pending"] + summary["running"] +
                    summary["completed"] + summary["failed"]
                )
                check(f"get_summary trial {trial} counts consistent",
                      summary["total"] == total_calc,
                      f"total={summary['total']} vs calculated={total_calc}")
            
            elif op == "all_completed":
                result = tm.all_completed()
                check(f"all_completed trial {trial} step {step} returns bool",
                      isinstance(result, bool))
            
            elif op == "reset":
                tm.reset()
                check(f"reset trial {trial} step {step} clears tasks",
                      len(tm._tasks) == 0)
        
        except Exception as e:
            check(f"State machine trial {trial} step {step} {op}: no crash",
                  False,
                  f"{type(e).__name__}: {e}")

    # Final state consistency check
    try:
        summary = tm.get_summary()
        total = summary["total"]
        actual = len(tm._tasks)
        check(f"Final consistency trial {trial}: summary total == actual tasks",
              total == actual,
              f"summary says {total}, actual {actual}")
        
        # Verify state of each task matches its record
        for tid, record in tm._tasks.items():
            check(f"Trial {trial} task {tid} state matches summary",
                  record.state.value in ("pending", "running", "completed", "failed"))
    except Exception as e:
        check(f"Final consistency trial {trial}: no crash",
              False,
              f"{type(e).__name__}: {e}")


# ─── Additional edge case: mark_failed on non-existent task ─────────────────
print("\n  Edge cases: invalid transitions on non-existent tasks")
tm_edge = TaskManager()
try:
    tm_edge.mark_failed("no-such-task", "error")
    check("mark_failed on non-existent task raises ValueError", False)
except ValueError as e:
    check("mark_failed on non-existent task raises ValueError", "Unknown task_id" in str(e))

try:
    tm_edge.mark_completed("no-such-task", TaskResult(
        status=TaskStatus.SUCCESS, summary="x", result="x"
    ))
    check("mark_completed on non-existent task raises ValueError", False)
except ValueError as e:
    check("mark_completed on non-existent task raises ValueError", "Unknown task_id" in str(e))

# Test mark_failed on PENDING (not allowed — must be RUNNING first)
spec = TaskSpec(task_id="test-pending", description="d", acceptance_criteria="a", context="c", depth=1)
tm_edge.add_task(spec)
try:
    tm_edge.mark_failed("test-pending", "should not work")
    check("mark_failed on PENDING task raises ValueError", False)
except ValueError as e:
    check("mark_failed on PENDING task raises ValueError", "Invalid transition" in str(e))


# ─── Summary ─────────────────────────────────────────────────────────────────

print(f"\n{'='*60}")
print(f"RESULTS: {passed} passed, {failed} failed out of {passed + failed} checks")
if failures:
    print(f"\nFAILURES ({len(failures)}):")
    for f in failures[:30]:  # Show first 30
        print(f"  - {f}")
    if len(failures) > 30:
        print(f"  ... and {len(failures) - 30} more")
else:
    print("All fuzz tests passed!")
print(f"{'='*60}")
