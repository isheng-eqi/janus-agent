"""
Hermes Client — minimal stub for backward compatibility with tests.

In Phase 1, Workers were Hermes subprocesses. In Phase 2, Workers are
LLM-driven with native function calling.  This module keeps the old
config + parsing classes that tests still reference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from .protocol import Confidence, TaskResult, TaskSpec, TaskStatus


# ============================================================================
# HermesWorkerConfig
# ============================================================================


@dataclass
class HermesWorkerConfig:
    """Configuration for a Hermes Worker (Phase 1 compat)."""

    id: str
    role: str = ""
    profile: str = ""
    model: str = "deepseek-chat"
    system_prompt: str = ""
    capabilities: list[str] = field(default_factory=list)
    timeout_seconds: int = 300

    @classmethod
    def from_dict(
        cls,
        data: dict,
        defaults: Optional[dict] = None,
    ) -> "HermesWorkerConfig":
        """Create config from a dict with sensible defaults.

        Args:
            data: The configuration dictionary.
            defaults: Optional override defaults for missing fields.
        """
        merged: dict = {}
        # Apply class-level defaults
        merged.setdefault("role", data.get("id", "minimal"))
        merged.setdefault("profile", f"janus-worker-{data.get('id', 'minimal')}")
        merged.setdefault("model", "deepseek-chat")
        merged.setdefault("system_prompt", "")
        merged.setdefault("capabilities", [])
        merged.setdefault("timeout_seconds", 300)

        # Apply external defaults on top
        if defaults:
            merged.update(defaults)

        # Apply explicit data on top of everything
        merged.update(data)

        return cls(
            id=merged["id"],
            role=merged["role"],
            profile=merged["profile"],
            model=merged["model"],
            system_prompt=merged["system_prompt"],
            capabilities=merged["capabilities"],
            timeout_seconds=merged["timeout_seconds"],
        )


# ============================================================================
# HermesClient
# ============================================================================


class HermesClient:
    """Stub Hermes client (Phase 1 compat).

    In Phase 2 this functionality is handled by Worker._parse_result.
    Kept for backward-compatible test support.
    """

    def __init__(self, config: HermesWorkerConfig) -> None:
        self._config = config

    def _build_prompt(self, spec: TaskSpec) -> str:
        """Build a prompt string from config and task spec."""
        parts: list[str] = []
        if self._config.system_prompt:
            parts.append(self._config.system_prompt)
        parts.append("")
        parts.append(f"## Your Task\n{spec.description}")
        parts.append("")
        parts.append(f"## Acceptance Criteria\n{spec.acceptance_criteria}")
        parts.append("")
        if spec.context:
            parts.append(f"## Context\n{spec.context}")
            parts.append("")
        parts.append("## Output Format")
        parts.append(
            'Output a JSON object with "status" ("success", "failure", '
            'or "needs_decomposition"), "summary", "result", '
            '"artifacts", "confidence", and optionally '
            '"decomposition_request".'
        )
        return "\n".join(parts)

    @staticmethod
    def _parse_output(stdout: str, worker_id: str) -> TaskResult:
        """Parse Hermes agent stdout into a TaskResult.

        This mirrors Worker._parse_result but keeps the static signature
        that old tests expect.

        Args:
            stdout: The raw text output from Hermes.
            worker_id: Identifier assigned to this worker.

        Returns:
            A TaskResult, always — malformed output returns FAILURE.
        """
        if not stdout or not stdout.strip():
            return TaskResult(
                status=TaskStatus.FAILURE,
                summary="Hermes returned no output.",
                result="(empty response)",
                confidence=Confidence.LOW,
                worker_id=worker_id,
            )

        # Try to extract JSON from the output using the shared extract_json
        # from prompts.py (avoids duplicate JSON extraction — BP19).
        from .prompts import extract_json

        # 1. Try _extract_hermes_json first — it has the correct
        #    last-wins semantics for Hermes output (the last JSON object
        #    with a 'status' key wins).
        parsed = _extract_hermes_json(stdout)

        # 2. Fallback to the shared extract_json (handles ```json fences,
        #    bracket counting) when no status-bearing object is found.
        if parsed is None:
            parsed = extract_json(stdout)

        if parsed is None:
            return TaskResult(
                status=TaskStatus.FAILURE,
                summary="Could not parse Hermes output as JSON.",
                result=stdout,
                confidence=Confidence.LOW,
                worker_id=worker_id,
            )

        try:
            status_str = parsed.get("status", "failure")
            status = TaskStatus(status_str)
            summary = parsed.get("summary", "")
            result_text = parsed.get("result", "")

            tr = TaskResult(
                status=status,
                summary=summary,
                result=result_text,
                artifacts=parsed.get("artifacts", []),
                confidence=Confidence(
                    parsed.get("confidence", "medium")
                ),
                worker_id=worker_id,
            )

            # Handle decomposition request if present
            dr_data = parsed.get("decomposition_request")
            if dr_data:
                from .protocol import DecompositionRequest, SubTask

                tr.decomposition_request = DecompositionRequest(
                    reason=dr_data.get("reason", ""),
                    sub_tasks=[
                        SubTask(
                            id=st.get("id", ""),
                            description=st.get("description", ""),
                            rationale=st.get("rationale", ""),
                        )
                        for st in dr_data.get("sub_tasks", [])
                    ],
                )

            # Validate
            if status == TaskStatus.NEEDS_DECOMPOSITION and not tr.validate():
                return TaskResult(
                    status=TaskStatus.FAILURE,
                    summary=(
                        "Invalid TaskResult: NEEDS_DECOMPOSITION requires "
                        "valid decomposition_request with sub_tasks."
                    ),
                    result=stdout,
                    confidence=Confidence.LOW,
                    worker_id=worker_id,
                )

            return tr

        except (ValueError, KeyError, TypeError):
            return TaskResult(
                status=TaskStatus.FAILURE,
                summary="Could not parse Hermes output as TaskResult.",
                result=stdout,
                confidence=Confidence.LOW,
                worker_id=worker_id,
            )


# ============================================================================
# Internal: JSON extraction
# ============================================================================


def _extract_hermes_json(text: str) -> Optional[dict]:
    """[DEPRECATED] Use ``extract_json`` from ``.prompts`` instead.

    Kept for backward compatibility with external test code.
    Internally, ``_parse_output`` now delegates to the shared
    ``extract_json`` from ``prompts.py`` (BP19 deduplication).
    """
    # Try code fences
    import re

    fence_match = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        text,
        re.DOTALL,
    )
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try brace-counted JSON objects — find all, return last with 'status'
    candidates: list[dict] = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start : i + 1]
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict):
                        candidates.append(obj)
                except json.JSONDecodeError:
                    pass
                start = -1

    # Return the last candidate that has a 'status' key
    for obj in reversed(candidates):
        if "status" in obj:
            return obj

    # Fallback: return last candidate
    if candidates:
        return candidates[-1]

    # Final fallback: try the whole text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
