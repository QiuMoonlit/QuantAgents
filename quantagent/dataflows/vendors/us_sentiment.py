"""US retail sentiment: StockTwits plus Reddit.

Extracted from the Sentiment Analyst node, which used to import both fetchers
directly and inline the composition. Routing needs one callable per vendor, and
keeping the US pair here means the analyst has a single source-selection point
instead of a branch on the ticker.

Behaviour is unchanged: same two fetches, same JEV screening, same window.
"""

from __future__ import annotations

import logging

from quantagent.dataflows.vendors.reddit import fetch_reddit_posts
from quantagent.dataflows.vendors.stocktwits import fetch_stocktwits_messages

logger = logging.getLogger(__name__)


def get_us_sentiment(
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    screen=None,
) -> str:
    """StockTwits and Reddit blocks for a US ticker.

    Both fetchers degrade on their own — they return a placeholder string rather
    than raising — so a partial result still reaches the model. ``screen`` is
    applied when not supplied, preserving the #1220 behaviour of trimming social
    posts to the analysis window.

    ``jev_screen`` is imported here, not at module scope: the router imports
    this module, and ``quantagent.agents.post_screen`` pulls in the agent
    package, which imports the router back.
    """
    if screen is None:
        from quantagent.agents.post_screen import jev_screen

        screen = jev_screen(symbol)

    parts: list[str] = []
    for label, fetch in (
        ("StockTwits", lambda: fetch_stocktwits_messages(
            symbol, limit=30, start_date=start_date, end_date=end_date, screen=screen)),
        ("Reddit", lambda: fetch_reddit_posts(
            symbol, start_date=start_date, end_date=end_date, screen=screen)),
    ):
        try:
            parts.append(fetch())
        except Exception as exc:  # noqa: BLE001 — one dead feed must not sink sentiment
            logger.warning("US sentiment source %s unavailable: %s", label, exc)
            parts.append(f"<{label} unavailable: {type(exc).__name__}>")

    return "\n\n".join(parts)
