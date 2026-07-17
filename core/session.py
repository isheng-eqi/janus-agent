"""
Janus Session — multi-turn conversation manager.

Session is a pure history recorder + pass-through to the Gatekeeper.
The Gatekeeper makes all decisions — no pre-processing, keyword classification,
or intent routing happens here.
"""

from __future__ import annotations

from typing import Optional

from .gatekeeper import Gatekeeper


class Session:
    """Multi-turn conversation manager for Janus.

    Wraps a Gatekeeper instance and maintains a conversation history so
    that the user can have a back-and-forth dialogue.  All decision-making
    (chat vs task, decomposition, dispatch) is handled by the Gatekeeper —
    Session is a thin pass-through + history recorder.

    Usage::

        gk = Gatekeeper(...)
        session = Session(gk)
        answer = session.handle("帮我写个爬虫")
    """

    def __init__(self, gatekeeper: Gatekeeper, max_history: int = 100) -> None:
        self._gk = gatekeeper
        self._history: list[dict[str, str]] = []
        self._last_result: Optional[str] = None
        self._max_history = max_history

    # -- public API -----------------------------------------------------------

    def handle(self, user_input: str) -> str:
        """Pass *user_input* to the Gatekeeper and record the exchange.

        Gatekeeper decides everything — no pre-processing, classification,
        or intent routing at the Session level.

        Formats recent conversation history (last 5 exchanges) and passes it
        to Gatekeeper for context-aware decisions.

        Args:
            user_input: The raw user input string.

        Returns:
            The Gatekeeper's response string.
        """
        history_context = self._format_history_context()

        result = self._gk.handle(user_input, history_context=history_context)

        # Record in history (trim to last N turn-pairs)
        self._history.append({"role": "user", "content": user_input})
        self._history.append({"role": "assistant", "content": result})
        if len(self._history) > self._max_history * 2:
            self._history = self._history[-(self._max_history * 2):]

        self._last_result = result
        return result

    # -- internal -------------------------------------------------------------

    def _format_history_context(self, last_n: int = 5) -> str:
        """Format the last N conversation exchanges as a context string.

        Returns a compact summary of recent turns so the Gatekeeper can
        make context-aware decisions.  Returns empty string when there's
        no history yet.

        Args:
            last_n: Number of recent exchanges to include (default 5).

        Returns:
            A formatted string like:

                --- Recent Conversation ---
                User: 帮我写个爬虫
                Assistant: 好的，我来帮你分析...
                --- (2 turns ago) ---
                User: 继续上一次的任务
                --- End Recent History ---

            Or an empty string when there's no history.
        """
        if not self._history:
            return ""

        # Take last N turn-pairs (each exchange = user + assistant)
        history_slice = self._history[-(last_n * 2):]
        if not history_slice:
            return ""

        lines = ["--- Recent Conversation ---"]
        total_turns = len(self._history) // 2
        start_turn = max(1, total_turns - last_n + 1)
        for i in range(0, len(history_slice), 2):
            turn_num = start_turn + (i // 2)
            user_msg = history_slice[i]
            truncated = user_msg["content"] if len(user_msg["content"]) <= 200 else user_msg["content"][:200] + "..."
            lines.append(f"Turn {turn_num} — User: {truncated}")
            if i + 1 < len(history_slice):
                asst_msg = history_slice[i + 1]
                truncated = asst_msg["content"] if len(asst_msg["content"]) <= 200 else asst_msg["content"][:200] + "..."
                lines.append(f"         Assistant: {truncated}")

        lines.append("--- End Recent History ---")
        return "\n".join(lines)

    # -- properties -----------------------------------------------------------

    @property
    def history(self) -> list[dict[str, str]]:
        """The conversation history as a list of role/content dicts."""
        return list(self._history)

    @property
    def last_result(self) -> Optional[str]:
        """The most recent result from the Gatekeeper."""
        return self._last_result
