"""Token/call accounting for the terminal TUI.

The implementation moved to ``quantagent.observability`` so the web UI can
share it without depending on the ``cli`` package. This module stays as the
import path the TUI already uses.
"""

from quantagent.observability import (
    MODEL_PRICING,
    StatsCallbackHandler,
    estimate_cost,
)

__all__ = ["StatsCallbackHandler", "MODEL_PRICING", "estimate_cost"]
