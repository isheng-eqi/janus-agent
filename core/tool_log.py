"""
Janus L3-2: Deterministic tool-call logging.

Every tool invocation inside the Worker's execution loop is logged to a
JSONL file so tool-call patterns can be audited post-hoc — making
deception (e.g. claiming to have read a file without actually doing so)
detectable via log/replay comparison.

Logs are written to ``tool_logs/{task_id}.jsonl`` in append mode so each
task gets its own audit trail.
"""

from __future__ import annotations

import json
import os
import datetime
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class ToolCallLog:
    """A single tool-invocation record for the audit log.

    Attributes:
        timestamp: ISO-8601 timestamp of the invocation.
        tool_name: Name of the tool that was called (e.g. ``"read_file"``).
        arguments: The (possibly aliased) arguments passed to the tool.
        result_summary: First 200 characters of the tool's return value.
        task_id: The task that triggered this tool call.
    """

    timestamp: str
    tool_name: str
    arguments: dict[str, Any]
    result_summary: str
    task_id: str


def log_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    result_text: str,
    task_id: str,
    log_dir: str = "",
) -> None:
    """Append a ``ToolCallLog`` entry to the task's JSONL audit file.

    Args:
        tool_name: The tool that was invoked.
        arguments: The arguments the tool was called with.
        result_text: The raw tool output string.
        task_id: Identifier of the owning task.
        log_dir: Optional override for the log directory.  Defaults to
            ``tool_logs/`` relative to the Janus project root.
    """
    # L3-2: deterministic tool-call logging
    if not log_dir:
        log_dir = os.path.join(os.path.dirname(__file__), "..", "tool_logs")

    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{task_id}.jsonl")

    record = ToolCallLog(
        timestamp=datetime.datetime.now().isoformat(),
        tool_name=tool_name,
        arguments=arguments,
        result_summary=result_text[:200],
        task_id=task_id,
    )

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
