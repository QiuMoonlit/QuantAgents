"""A-share and Hong Kong market data and technical indicators.

The indicator math is stockstats, which is market-agnostic: it only needs a
frame with ``Date/Open/High/Low/Close/Volume``. That is the same code the
Yahoo vendor runs, so the two produce comparable numbers for a comparable
ticker and any divergence is a data problem, not a methodology one.

The one market-specific concern is the staleness guard. Chinese exchanges
close for longer stretches than US ones (Spring Festival runs about ten
calendar days, National Day about eight), so a correct frame can sit well
past Yahoo's ten-day threshold; :data:`CN_MAX_OHLCV_STALE_DAYS` allows for
that while still rejecting a genuinely year-old response.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

import pandas as pd

from quantagent.dataflows.errors import NoMarketDataError
from quantagent.dataflows.vendors.akshare.ohlcv import (
    CN_MAX_OHLCV_STALE_DAYS,
    load_cn_ohlcv,
    resolve_cn_symbol_or_skip,
)
from quantagent.dataflows.vendors.yahoo.market import best_ind_params
from quantagent.dataflows.vendors.yahoo.ohlcv import _assert_ohlcv_not_stale

logger = logging.getLogger(__name__)

# Long enough for the 200-day SMA plus a holiday buffer, matching what the
# Yahoo vendor loads.
LOOKBACK_CALENDAR_DAYS = 500


def _history(symbol: str, curr_date: str, *, days: int = LOOKBACK_CALENDAR_DAYS) -> pd.DataFrame:
    """Daily bars ending at ``curr_date``, stale-checked."""
    cn = resolve_cn_symbol_or_skip(symbol)
    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - pd.Timedelta(days=days)
    frame = load_cn_ohlcv(cn, start_dt.strftime("%Y-%m-%d"), curr_date)
    _assert_ohlcv_not_stale(
        frame, curr_date, cn.canonical, cn.canonical,
        max_stale_days=CN_MAX_OHLCV_STALE_DAYS,
    )
    return frame


def get_cn_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Daily OHLCV as the CSV report the Market Analyst reads."""
    cn = resolve_cn_symbol_or_skip(symbol)
    frame = load_cn_ohlcv(cn, start_date, end_date)
    _assert_ohlcv_not_stale(
        frame, end_date, cn.canonical, cn.canonical,
        max_stale_days=CN_MAX_OHLCV_STALE_DAYS,
    )

    csv_string = frame.to_csv(index=False)
    label = cn.canonical if cn.canonical == symbol.upper() else f"{cn.canonical} (from {symbol})"
    header = f"# Stock data for {label} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(frame)}\n"
    header += ("# Volume in shares (converted from 手)\n" if cn.is_mainland
               else "# Volume as reported by the vendor (Hong Kong board lots "
                    "vary per security and are not converted)\n")
    return header + csv_string


def get_cn_stock_stats(
    symbol: Annotated[str, "ticker symbol for the company"],
    indicator: Annotated[str, "quantitative indicator to calculate"],
    curr_date: Annotated[str, "curr date for retrieving stock price data, YYYY-mm-dd"],
):
    """One indicator value on ``curr_date``, via stockstats."""
    from stockstats import wrap

    frame = _history(symbol, curr_date)
    df = wrap(frame)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

    df[indicator]  # trigger stockstats to calculate the indicator
    matching = df[df["Date"].str.startswith(curr_date)]

    if not matching.empty:
        return matching[indicator].values[0]
    return "N/A: Not a trading day (weekend or holiday)"


def get_cn_stock_stats_window(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    """An indicator series over the trailing window, with its description."""
    from stockstats import wrap

    frame = _history(symbol, curr_date)
    df = wrap(frame)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    df[indicator]

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    window_start = (end_dt - pd.Timedelta(days=look_back_days)).strftime("%Y-%m-%d")
    subset = df[df["Date"] >= window_start]

    rows = []
    for _, row in subset.iterrows():
        value = row[indicator]
        rows.append(f"{row['Date']}: {'N/A' if pd.isna(value) else round(float(value), 2)}")

    result_str = f"## {indicator} for {resolve_cn_symbol_or_skip(symbol).canonical} from "
    result_str += f"{window_start} to {curr_date}\n\n"
    result_str += "\n".join(rows) if rows else "N/A: no trading days in this window"
    result_str += "\n\n"
    result_str += best_ind_params.get(indicator, "No description available.")
    return result_str


def get_cn_stock_stats_bulk(symbol: str, indicator: str, curr_date: str) -> dict:
    """Every date's value for one indicator, in one pass."""
    from stockstats import wrap

    frame = _history(symbol, curr_date)
    df = wrap(frame)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    df[indicator]

    result: dict[str, str] = {}
    for _, row in df.iterrows():
        value = row[indicator]
        result[row["Date"]] = "N/A" if pd.isna(value) else str(value)
    return result


def get_cn_stock_stats_indicator(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
) -> str:
    """Single indicator, with a typed failure so the router can fall through."""
    try:
        return str(get_cn_stock_stats(symbol, indicator, curr_date))
    except NoMarketDataError:
        raise
    except Exception as exc:
        raise NoMarketDataError(
            symbol, symbol,
            f"{indicator} could not be read for {curr_date}: {exc}",
        ) from exc


def get_cn_verified_market_snapshot(
    symbol: str,
    curr_date: str,
    look_back_days: int = 30,
    indicators=None,
) -> str:
    """Ground-truth snapshot for a Chinese ticker.

    The rendering is the shared one every vendor uses, so the guard rails
    against a confabulated price or indicator are identical across markets; only
    the frame behind it is fetched from AkShare.
    """
    from quantagent.dataflows.vendors.yahoo.snapshot import render_verified_snapshot

    return render_verified_snapshot(
        _history(symbol, curr_date), symbol, curr_date, look_back_days, indicators,
    )


def get_cn_closes(symbol: str, start_date: str, end_date: str) -> pd.Series:
    """Daily closes over a range, for the settlement and alpha layers."""
    cn = resolve_cn_symbol_or_skip(symbol)
    try:
        frame = load_cn_ohlcv(cn, start_date, end_date)
    except NoMarketDataError:
        raise
    except Exception as exc:
        raise NoMarketDataError(
            symbol, cn.canonical, f"prices unavailable: {exc}"
        ) from exc
    if "Close" not in frame:
        return pd.Series(dtype=float)
    return frame.set_index("Date")["Close"]
