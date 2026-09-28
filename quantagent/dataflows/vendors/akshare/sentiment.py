"""Chinese retail sentiment via Eastmoney (东方财富).

This is the Chinese-market counterpart to the StockTwits and Reddit feeds the
Sentiment Analyst reads for US tickers. Those are chat platforms where people
post a message and, on StockTwits, self-label it Bullish or Bearish. Eastmoney's
股吧 is the same institution in China — a per-stock forum with retail posts,
and a popularity ranking that functions as the platform's engagement signal.

Two things replace the Western pair:

* 用户关注指数 (the per-stock attention index, 30 trading days) is the direct
  analogue of a cashtag stream's volume. A rising index with a flat price is
  attention without buying; the US feeds have no such separation.
* 新晋粉丝 / 铁杆粉丝 (new fans vs loyal fans in the popularity ranking) is the
  analogue of a stocktwits tag ratio, and the two move differently: loyal fans
  skew to holding, new fans to chasing. A spike in 新晋粉丝 with no price
  response is a retail-flow signal, not a conviction signal.

Deliberately *not* used: 雪球 (Xueqiu). AkShare's Xueqiu endpoints are hot-topic
and holdings screens, not a per-stock message stream —
``stock_hot_tweet_xq(symbol="SH600519")`` raises KeyError because it only
serves a global trending list. Claiming a Xueqiu feed here would be fiction.

``stock_comment_em`` returns the whole market (about 5,200 rows) and is cached
per process, because a run scores one stock at a time and re-downloading the
table per analyst would be absurd.
"""

from __future__ import annotations

import logging
import time

import pandas as pd

from quantagent.dataflows.errors import NoMarketDataError
from quantagent.dataflows.vendors.akshare.ohlcv import resolve_cn_symbol_or_skip

logger = logging.getLogger(__name__)

# The market-wide score table is large and stable within a session.
_COMMENT_TABLE_TTL_SECONDS = 15 * 60
_comment_table: tuple[float, pd.DataFrame] | None = None

# Attention index and popularity ranking both come in longer than the analysis
# window and are trimmed here.
_DEFAULT_LOOKBACK_DAYS = 30


def _market_comment_table() -> pd.DataFrame:
    """The per-stock sentiment score table, cached for the process's lifetime."""
    global _comment_table
    now = time.monotonic()
    if _comment_table is not None and now - _comment_table[0] < _COMMENT_TABLE_TTL_SECONDS:
        return _comment_table[1]

    import akshare as ak

    try:
        frame = ak.stock_comment_em()
    except Exception as exc:
        # Wrapped so the composite block can degrade one source instead of
        # letting a transport error escape as a different exception type.
        raise NoMarketDataError(
            "", "", f"Eastmoney comment table failed: {type(exc).__name__}: {exc}"
        ) from exc
    if frame is None or frame.empty:
        raise NoMarketDataError("", "", "Eastmoney comment table was empty")
    _comment_table = (now, frame)
    return frame


def _score_row(symbol: str):
    # Resolve before fetching: the table is the whole market (about 5,200 rows)
    # and downloading it to discover the symbol is not a Chinese one would be
    # a pointless multi-megabyte round trip on every US run.
    cn = resolve_cn_symbol_or_skip(symbol)
    table = _market_comment_table()
    code = cn.code
    # 代码 is read as a string on some frames and an int on others; comparing as
    # text is the only form that survives both.
    match = table[table["代码"].astype(str).str.zfill(6) == code]
    if match.empty:
        raise NoMarketDataError(symbol, code, "not present in the sentiment score table")
    return match.iloc[0]


def get_cn_retail_attention(symbol: str, look_back_days: int = _DEFAULT_LOOKBACK_DAYS) -> str:
    """The 股吧 attention index over the recent window, as a small table.

    This is the sentiment analogue of message volume: it says how much the retail
    forum is talking, not whether they are positive.
    """
    import akshare as ak

    cn = resolve_cn_symbol_or_skip(symbol)
    try:
        frame = ak.stock_comment_detail_scrd_focus_em(symbol=cn.code)
    except Exception as exc:
        raise NoMarketDataError(
            symbol, cn.canonical,
            f"Eastmoney attention request failed: {type(exc).__name__}: {exc}",
        ) from exc

    if frame is None or frame.empty:
        raise NoMarketDataError(symbol, cn.canonical, "no attention index returned")
    if "用户关注指数" not in frame.columns:
        raise NoMarketDataError(
            symbol, cn.canonical,
            f"attention response has no 用户关注指数 column "
            f"(got {list(frame.columns)[:6]})",
        )

    frame = frame.copy()
    frame["交易日"] = pd.to_datetime(frame["交易日"], errors="coerce")
    frame = frame.dropna(subset=["交易日"]).sort_values("交易日")
    window = frame.tail(max(1, look_back_days))

    lines = [
        f"# 散户关注度 (retail attention index) — {cn.canonical}",
        f"Source: 东方财富股吧 (Eastmoney), last {len(window)} trading days",
        "",
        "The index measures how much the stock's retail forum is being followed. "
        "It is a measure of attention, not of direction: a rising index with a "
        "flat price is people watching rather than buying.",
        "",
        "| 交易日 | 用户关注指数 |",
        "|---|---:|",
    ]
    for _, row in window.iterrows():
        lines.append(f"| {row['交易日'].strftime('%Y-%m-%d')} | {row['用户关注指数']:.1f} |")

    values = window["用户关注指数"]
    if len(values) >= 2:
        change = float(values.iloc[-1] - values.iloc[0])
        mean = float(values.mean())
        lines += [
            "",
            f"- Latest: {values.iloc[-1]:.1f}",
            f"- Change over window: {change:+.1f}",
            f"- Window mean: {mean:.1f}",
        ]
    return "\n".join(lines)


def get_cn_retail_sentiment(symbol: str) -> str:
    """The retail scorecard: composite score, retail cost basis, attention index.

    Combined with :func:`get_cn_retail_attention` this stands in for the
    StockTwits Bullish/Bearish ratio — it says where the crowd is and how
    committed, without depending on self-applied labels that do not exist here.
    """
    row = _score_row(symbol)
    cn = resolve_cn_symbol_or_skip(symbol)

    def num(column: str) -> float | None:
        if column not in row.index:
            return None
        value = row[column]
        return None if pd.isna(value) else float(value)

    score = num("综合得分")
    participation = num("机构参与度")
    cost = num("主力成本")
    attention = num("关注指数")
    turnover = num("换手率")
    change = num("涨跌幅")
    price = num("最新价")

    if score is None:
        raise NoMarketDataError(symbol, cn.canonical, "sentiment score table has no score")

    lines = [
        f"# 散户情绪评分 (retail sentiment scorecard) — {cn.canonical}",
        "Source: 东方财富股吧 (Eastmoney)",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 综合得分 (composite) | {score:.1f} |",
        f"| 关注指数 (attention) | {attention:.1f} |" if attention is not None else "",
        f"| 机构参与度 (institutional participation) | {participation:.2f} |"
        if participation is not None else "",
        f"| 主力成本 (retail cost basis, CNY) | {cost:.2f} |" if cost is not None else "",
        f"| 最新价 (CNY) | {price:.2f} |" if price is not None else "",
        f"| 换手率 % (turnover) | {turnover:.2f} |" if turnover is not None else "",
        f"| 涨跌幅 % (day change) | {change:.2f} |" if change is not None else "",
    ]
    lines = [line for line in lines if line != ""]

    if cost is not None and price is not None and cost > 0:
        gap = (price - cost) / cost * 100
        lines += [
            "",
            f"- Price is {gap:+.1f}% against the retail cost basis, so the average "
            f"holder is {'in profit' if gap >= 0 else 'under water'}. This is a "
            f"cost basis, not a price target: do not quote it as one.",
        ]
    return "\n".join(lines)


def get_cn_popularity_trend(symbol: str, look_back_days: int = 90) -> str:
    """Popularity ranking over time, split into new fans and loyal fans.

    The rank itself is the closest thing China has to a StockTwits message count:
    a stock sliding in the popularity table has lost the retail conversation.
    """
    import akshare as ak

    cn = resolve_cn_symbol_or_skip(symbol)
    try:
        frame = ak.stock_hot_rank_detail_em(symbol=cn.prefixed.upper())
    except Exception as exc:
        raise NoMarketDataError(
            symbol, cn.canonical,
            f"Eastmoney popularity request failed: {type(exc).__name__}: {exc}",
        ) from exc

    if frame is None or frame.empty:
        raise NoMarketDataError(symbol, cn.canonical, "no popularity history returned")

    frame = frame.copy()
    frame["时间"] = pd.to_datetime(frame["时间"], errors="coerce")
    frame = frame.dropna(subset=["时间"]).sort_values("时间")
    window = frame.tail(max(1, look_back_days))

    lines = [
        f"# 人气排名趋势 (popularity ranking) — {cn.canonical}",
        f"Source: 东方财富 (Eastmoney), last {len(window)} days",
        "",
        "Rank 1 is the most-followed stock in the market, so a falling number is "
        "losing the retail conversation. 新晋粉丝 (new fans) chase; 铁杆粉丝 "
        "(loyal fans) hold — a rise in new fans without a price response is retail "
        "flow, not conviction.",
        "",
        "| 日期 | 排名 | 新晋粉丝 | 铁杆粉丝 |",
        "|---|---:|---:|---:|",
    ]
    for _, row in window.tail(20).iterrows():
        new = row.get("新晋粉丝")
        loyal = row.get("铁杆粉丝")
        lines.append(
            f"| {row['时间'].strftime('%Y-%m-%d')} | {int(row['排名'])} | "
            f"{'' if pd.isna(new) else f'{new:.2%}'} | "
            f"{'' if pd.isna(loyal) else f'{loyal:.2%}'} |"
        )

    ranks = window["排名"]
    if len(ranks) >= 2:
        first, last = int(ranks.iloc[0]), int(ranks.iloc[-1])
        direction = "improved" if last < first else "worsened"
        lines += [
            "",
            f"- Rank went {first} -> {last} over the window ({direction}).",
        ]
    return "\n".join(lines)


def get_cn_sentiment(
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    screen=None,
) -> str:
    """The full Chinese retail sentiment block for the Sentiment Analyst.

    ``screen`` and the date window are accepted for signature parity with
    :func:`fetch_stocktwits_messages` so the analyst can pick a source the same
    way for every market. Neither applies here: the attention index is already
    a per-stock series, and the vendor serves no archive, so a historical run
    gets the current window — the same limitation the US feeds have, stated
    rather than hidden.
    """
    del screen, start_date, end_date
    parts: list[str] = []
    failures: list[str] = []
    for label, fetch in (
        ("散户情绪评分", get_cn_retail_sentiment),
        ("散户关注度", get_cn_retail_attention),
        ("人气排名趋势", get_cn_popularity_trend),
    ):
        try:
            parts.append(fetch(symbol))
        except NoMarketDataError as exc:
            logger.warning("Chinese sentiment source %s unavailable: %s", label, exc)
            failures.append(f"{label}: {exc.detail}")

    if not parts:
        raise NoMarketDataError(
            symbol, symbol,
            f"every Chinese sentiment source was unavailable: {'; '.join(failures)}",
        )
    block = "\n\n".join(parts)
    if failures:
        block += (
            "\n\n## 不可用的数据源 (unavailable sources)\n"
            + "\n".join(f"- {f}" for f in failures)
        )
    return block
