"""
Janus shared prompts — reusable LLM prompt fragments for Gatekeeper and Planner.

These prompts are extracted to a single shared location so that a change
made here automatically benefits all consumers, eliminating the maintenance
burden of keeping duplicate copies in sync.
"""

import json
import re
from typing import Optional


def context_discipline_prompt(role_desc: str, job_desc: str) -> str:
    """Return the context-window discipline prompt tailored to *role_desc*.

    Args:
        role_desc: The actor's role description, e.g.
            ``"the top-level decision maker, like an executive talking to
            their assistant"``.
        job_desc: What this actor's job is fundamentally about, e.g.
            ``"direction and decisions, not implementation"``.
    """
    return f"""\
Context discipline: You are {role_desc}. Keep only architecture-level info \
(task status, key decisions). Summarize worker results to one line. Never \
load implementation details into context. Your job is {job_desc}."""


def extract_json(text: str) -> Optional[dict | list]:
    """Extract the outermost JSON object or array from *text*.

    Handles ```json fences and raw JSON.  Uses brace/bracket counting
    to handle nested JSON objects correctly.  Returns the candidate
    that spans the most text — so when a JSON object contains an array
    literal (e.g. ``{"issues": []}``), the outer ``{…}`` wins over the
    inner ``[]``.  Returns ``None`` when no valid JSON is found.

    Shared between Gatekeeper and Planner to avoid duplicate
    implementations of the same JSON extraction logic.
    """
    # 1. Try ```json ... ``` fences
    fence_match = re.search(
        r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```",
        text,
        re.DOTALL,
    )
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # 2. Try raw array/object with bracket counting (handles nesting).
    #    Find ALL top-level JSON candidates and return the one that spans
    #    the most text — this is the outermost structure.  When a JSON
    #    object contains an array literal (e.g. {"issues": []}), the
    #    inner [ ] will NOT win.
    candidates: list[tuple[str, int, int]] = []  # (json_str, start, end)
    for open_char, close_char in (("[", "]"), ("{", "}")):
        search_start = 0
        while True:
            first_open = text.find(open_char, search_start)
            if first_open == -1:
                break
            depth = 0
            end = first_open
            for i in range(first_open, len(text)):
                if text[i] == open_char:
                    depth += 1
                elif text[i] == close_char:
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            candidate = text[first_open:end]
            try:
                json.loads(candidate)
                candidates.append((candidate, first_open, end))
            except json.JSONDecodeError:
                pass
            search_start = end  # continue after this candidate

    if candidates:
        # Return the candidate that spans the most text (outermost structure)
        candidates.sort(key=lambda x: x[2] - x[1], reverse=True)
        return json.loads(candidates[0][0])

    return None
