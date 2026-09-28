"""Chinese market news via AkShare.

``stock_news_em`` returns the same six fields a US news vendor returns, under
Chinese column names, so the mapping is mechanical. The one thing worth getting
right is the window: the vendor returns its own most recent N articles and has no
date argument, so the analysis window is applied here by filtering. A historical
run must not see today's headlines.

The vendor also reports 时/换手率 alongside each article, which is the reason
this file is not a pure column rename — a headline published while the stock was
limit-down carries different information than one published on an up day, and
the Fundamentals/Market analysts should not have to guess.
"""

from __future__ import annotations

import logging

import pandas as pd

from quantagent.dataflows.errors import NoMarketDataError
from quantagent.dataflows.vendors.akshare.ohlcv import resolve_cn_symbol_or_skip

logger = logging.getLogger(__name__)

# Eastmoney's per-stock news endpoint returns roughly this many rows and has no
# pagination argument, so the window can only be filtered, not widened.
_MAX_ARTICLES = 30


def get_cn_news(symbol: str, start_date: str, end_date: str) -> str:
    """Chinese news for a Mainland ticker, filtered to the analysis window.

    Hong Kong is not served by this endpoint and says so rather than returning
    an empty section the analyst would read as "nothing happened".
    """
    import akshare as ak

    cn = resolve_cn_symbol_or_skip(symbol)
    if not cn.is_mainland:
        raise NoMarketDataError(
            symbol, cn.canonical,
            "Hong Kong news is not served by the AkShare vendor",
        )

    try:
        frame = ak.stock_news_em(symbol=cn.code)
    except Exception as exc:
        raise NoMarketDataError(
            symbol, cn.canonical,
            f"AkShare news request failed: {type(exc).__name__}: {exc}",
        ) from exc

    if frame is None or frame.empty:
        raise NoMarketDataError(symbol, cn.canonical, "no news rows returned")

    columns = {c: c for c in frame.columns}
    for needed in ("新闻标题", "发布时间"):
        if needed not in columns:
            raise NoMarketDataError(
                symbol, cn.canonical,
                f"news response is missing {needed!r} "
                f"(got {list(frame.columns)[:8]})",
            )

    start = pd.to_datetime(start_date, errors="coerce")
    end = pd.to_datetime(end_date, errors="coerce")
    if pd.isna(start) or pd.isna(end):
        raise NoMarketDataError(
            symbol, cn.canonical, f"unparseable window {start_date}..{end_date}",
        )

    # Compare calendar days, not timestamps. The vendor stamps a publication
    # time, so an article published at 09:00 on the end date is inside a window
    # that ends at 00:00 — and a timestamp comparison plus a one-day grace
    # period would leak tomorrow's headline into a historical run, which is the
    # look-ahead the whole trade_date anchor exists to prevent.
    published = pd.to_datetime(frame["发布时间"], errors="coerce").dt.normalize()
    day0, day1 = start.normalize(), end.normalize()

    windowed = frame[(published >= day0) & (published <= day1)]
    windowed = windowed.assign(_ts=published[windowed.index]).sort_values(
        "_ts", ascending=False
    )
    windowed = windowed.head(_MAX_ARTICLES)

    if windowed.empty:
        newest = published.max()
        raise NoMarketDataError(
            symbol, cn.canonical,
            f"no articles between {start_date} and {end_date}; "
            f"the vendor's newest is {newest.date() if pd.notna(newest) else 'unknown'}",
        )

    lines = [
        f"# Chinese market news for {cn.canonical} "
        f"({start_date} to {end_date}, 东方财富 / Eastmoney)",
        "",
        "## Articles",
    ]
    for _, row in windowed.iterrows():
        when = pd.to_datetime(row["发布时间"], errors="coerce")
        lines.append(f"### {row['新闻标题']}")
        lines.append(
            f"- Published: {when.strftime('%Y-%m-%d %H:%M') if pd.notna(when) else 'unknown'}"
        )
        source = row.get("文章来源")
        if pd.notna(source):
            lines.append(f"- Source: {source}")
        link = row.get("新闻链接")
        if pd.notna(link):
            lines.append(f"- Link: {link}")
        body = row.get("新闻内容")
        if pd.notna(body):
            # Long bodies blow the prompt budget; the headline and the first
            # paragraph carry the event, the rest is usually detail.
            text = " ".join(str(body).split())
            lines.append(f"- Summary: {text[:400]}")
        lines.append("")

    return "\n".join(lines)


def get_cn_global_news(curr_date: str, look_back_days: int = 7,
                       limit: int = 10) -> str:
    """Domestic macro headlines for the News Analyst's global block.

    Baidu publishes a dated economic-news digest, which is the closest
    equivalent to the US "Fed / inflation / GDP" headline feed this method
    normally serves. A date the vendor has not published yet yields nothing;
    that is reported as unavailable rather than as a quiet day.
    """
    import akshare as ak

    end = pd.to_datetime(curr_date, errors="coerce")
    if pd.isna(end):
        raise NoMarketDataError("", curr_date, f"unparseable date {curr_date!r}")

    errors: list[str] = []
    for offset in range(0, max(1, look_back_days)):
        day = (end - pd.Timedelta(days=offset)).strftime("%Y%m%d")
        try:
            frame = ak.news_economic_baidu(date=day)
        except Exception as exc:  # noqa: BLE001 — try the next day back
            errors.append(f"{day}: {type(exc).__name__}")
            continue
        if frame is None or frame.empty:
            continue

        title_col = next((c for c in ("标题", "新闻标题", "title") if c in frame.columns), None)
        if title_col is None:
            errors.append(f"{day}: no title column ({list(frame.columns)[:6]})")
            continue

        lines = [f"# Chinese macro news for {curr_date} (past {look_back_days} days, 百度财经)",
                 "", f"## Headlines on {day}"]
        for _, row in frame.head(limit).iterrows():
            lines.append(f"- {row[title_col]}")
        if len(lines) > 2:
            return "\n".join(lines)
        errors.append(f"{day}: no rows")

    raise NoMarketDataError(
        "", curr_date,
        f"no macro news available: {'; '.join(errors[:3]) or 'no days tried'}",
    )
