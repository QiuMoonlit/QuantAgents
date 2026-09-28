"""Run observability: token, call and cost accounting.

Lives in ``quantagent`` rather than ``cli`` so the web UI and any future API
can share one implementation without depending on the terminal package.
``cli.stats_handler`` re-exports :class:`StatsCallbackHandler` for the TUI.
"""

from __future__ import annotations

import threading
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

# USD per 1M tokens, (input, output). Used only to put a number next to a run;
# every provider prices differently and these are deliberately rounded.
# DeepSeek's own page is the authority if a number here looks wrong.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "gpt-6-sol": (5.00, 15.00),
    "gpt-6-luna": (1.00, 4.00),
    "gpt-4.1": (2.00, 8.00),
}
_FALLBACK_PRICING = (1.00, 4.00)


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """USD estimate for a run, or None when the model is not priced here."""
    rate_in, rate_out = MODEL_PRICING.get(model, _FALLBACK_PRICING)
    if model not in MODEL_PRICING:
        return None
    return (tokens_in * rate_in + tokens_out * rate_out) / 1_000_000


class StatsCallbackHandler(BaseCallbackHandler):
    """Callback handler that tracks LLM calls, tool calls, and token usage."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.llm_calls = 0
        self.tool_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        """Increment LLM call counter when an LLM starts."""
        with self._lock:
            self.llm_calls += 1

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        """Increment LLM call counter when a chat model starts."""
        with self._lock:
            self.llm_calls += 1

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Extract token usage from LLM response."""
        try:
            generation = response.generations[0][0]
        except (IndexError, TypeError):
            return

        usage_metadata = None
        if hasattr(generation, "message"):
            message = generation.message
            if isinstance(message, AIMessage) and hasattr(message, "usage_metadata"):
                usage_metadata = message.usage_metadata

        if usage_metadata:
            with self._lock:
                self.tokens_in += usage_metadata.get("input_tokens", 0)
                self.tokens_out += usage_metadata.get("output_tokens", 0)

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """Increment tool call counter when a tool starts."""
        with self._lock:
            self.tool_calls += 1

    def get_stats(self) -> dict[str, Any]:
        """Return current statistics."""
        with self._lock:
            return {
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
            }
