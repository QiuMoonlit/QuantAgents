"""A-share and Hong Kong price history via AkShare.

AkShare is the routing target for Chinese markets. It returns a different
shape from yfinance in three ways that matter downstream:

* **Column names are Chinese** — 日期/开盘/收盘/最高/最低/成交量 — so
  :func:`normalize_ohlcv` maps them onto the ``Date/Open/High/Low/Close/
  Volume`` contract that ``stockstats`` and every other vendor expect.
* **Volume is in 手 (lots of 100 shares)** for A-shares, where Yahoo reports
  shares. Volume is converted to shares so volume-based indicators and any
  comparison with a US ticker mean the same thing.
* **Symbol spelling differs** — Sina and Eastmoney want ``sh600519`` or the
  bare ``600519``, not Yahoo's ``600519.SS``.

Nothing here is market-specific beyond those translations, so the indicator
math in ``market.py`` is the same stockstats code the Yahoo vendor runs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import pandas as pd

from quantagent.dataflows.errors import NoMarketDataError

logger = logging.getLogger(__name__)

# Yahoo's MAX_OHLCV_STALE_DAYS is 10, sized for US long weekends. Chinese
# markets close for longer: Spring Festival can run ~9-10 calendar days and
# National Day ~7-8, so a correct frame can legitimately look 10+ days stale
# right after Golden Week. 20 clears every mainland and HK holiday while still
# catching a genuinely year-old response.
CN_MAX_OHLCV_STALE_DAYS = 20

# AkShare's Chinese column names -> the shared English contract.
_COLUMN_MAP = {
    "日期": "Date",
    "开盘": "Open",
    "收盘": "Close",
    "最高": "High",
    "最低": "Low",
    "成交量": "Volume",
    "time": "Date",
    "open": "Open",
    "close": "Close",
    "high": "High",
    "low": "Low",
    "volume": "Volume",
}

_SH_SUFFIX = re.compile(r"^(?P<code>\d{6})\.(?P<board>SS|SH|SZ)$", re.IGNORECASE)
_HK_SUFFIX = re.compile(r"^(?P<code>\d{1,5})\.HK$", re.IGNORECASE)

# The suffix the rest of QuantAgent uses per market. Shanghai is .SS, not
# Yahoo's .SH — see CnSymbol.
_SUFFIX_BY_MARKET = {"sh": ".SS", "sz": ".SZ", "hk": ".HK"}


@dataclass(frozen=True)
class CnSymbol:
    """A QuantAgent ticker resolved into what the Chinese vendors expect.

    ``canonical`` uses the project's own spelling: ``.SS`` for Shanghai, not
    Yahoo's ``.SH`` — ``dataflows.symbols.normalize_symbol`` rewrites ``.SH`` to
    ``.SS`` on the way in, and ``DEFAULT_CONFIG.benchmark_map`` keys Shanghai on
    ``.SS``. Emitting ``.SH`` here would make this vendor disagree with the rest
    of the system about which exchange a ticker belongs to.
    """

    canonical: str   # 600519.SS
    code: str        # 600519
    market: str      # "sh" | "sz" | "hk"
    prefixed: str    # sh600519

    @property
    def is_mainland(self) -> bool:
        return self.market in ("sh", "sz")

    @property
    def exchange_suffix(self) -> str:
        """The suffix ``benchmark_map`` uses for this market."""
        return {"sh": ".SS", "sz": ".SZ", "hk": ".HK"}[self.market]


# Board lot size per market, for converting AkShare's 成交量 into shares.
#
# Mainland A-shares trade in 手 of 100 shares. Verified against the same rows'
# 成交额: 23248 手 x 100 x 1295 CNY = 3.0098e9, against a reported turnover of
# 3.0030e9 — the two agree to the rounding AkShare applies.
#
# Hong Kong trades in board lots too, but the lot size varies per security
# ( Tencent is 100, some counters are 500 or 2000) and AkShare does not document
# which unit stock_hk_hist reports. Rather than apply a possibly-wrong
# multiplier, HK volume is passed through untouched and the header says so.
# Only volume-weighted indicators are affected, and only on HK.
_LOT_SIZE = {"sh": 100, "sz": 100, "hk": None}


def resolve_cn_symbol(symbol: str) -> CnSymbol:
    """Map ``600519.SS`` / ``000001.SZ`` / ``0700.HK`` to a vendor-ready symbol.

    Raises :class:`ValueError` for anything that is not a Mainland or Hong
    Kong ticker, so the router can fall through to a different vendor instead
    of asking AkShare for a US stock it will not have.
    """
    raw = (symbol or "").strip().upper()

    if match := _HK_SUFFIX.match(raw):
        code = match.group("code").zfill(5)
        return CnSymbol(canonical=f"{code}.HK", code=code, market="hk", prefixed=code)

    if match := _SH_SUFFIX.match(raw):
        code = match.group("code")
        board = match.group("board").upper()
        market = "sh" if board in ("SS", "SH") else "sz"
        return CnSymbol(canonical=f"{code}{_SUFFIX_BY_MARKET[market]}", code=code,
                        market=market, prefixed=f"{market}{code}")

    # Bare six-digit codes are Mainland by convention: 6/9 -> Shanghai, else
    # Shenzhen. This mirrors how the exchanges themselves read the leading digit.
    if raw.isdigit() and len(raw) == 6:
        market = "sh" if raw[0] in "69" else "sz"
        return CnSymbol(canonical=f"{raw}{_SUFFIX_BY_MARKET[market]}", code=raw,
                        market=market, prefixed=f"{market}{raw}")

    raise ValueError(
        f"{symbol!r} is not a Mainland or Hong Kong ticker; "
        f"expected 600519.SS / 000001.SZ / 0700.HK"
    )


def normalize_ohlcv(data: pd.DataFrame, symbol: CnSymbol) -> pd.DataFrame:
    """Coerce an AkShare frame onto the shared OHLCV contract.

    Returns a frame with ``Date`` (naive, midnight) plus numeric
    ``Open/High/Low/Close/Volume``, sorted ascending. Mainland volume is
    converted from 手 to shares; Hong Kong is left as the vendor reports it
    (see ``_LOT_SIZE``).
    """
    if data is None or data.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    frame = data.rename(columns=_COLUMN_MAP)
    # Keep only the contract columns, in order, dropping extras like 成交额 and
    # 换手率 that no shared code reads.
    keep = [c for c in ("Date", "Open", "High", "Low", "Close", "Volume")
            if c in frame.columns]
    if "Date" not in keep:
        raise NoMarketDataError(
            symbol.canonical, symbol.canonical,
            f"AkShare returned no date column (columns: {list(data.columns)[:8]})",
        )
    frame = frame[keep].copy()

    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce").dt.normalize()
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

    frame = frame.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)

    lot = _LOT_SIZE.get(symbol.market)
    if lot and "Volume" in frame.columns:
        # 手 -> shares. Skipping this is a silent 100x error in every
        # volume-based indicator, so it is not optional.
        frame["Volume"] = frame["Volume"] * lot

    return frame


def resolve_cn_symbol_or_skip(symbol: str) -> CnSymbol:
    """:func:`resolve_cn_symbol`, but raising the type the router falls through on.

    The router treats ``NoMarketDataError`` as "this vendor has nothing for
    this symbol, try the next one" and a bare ``Exception`` as "this vendor
    broke". A US ticker is the former, not the latter: raising ``ValueError``
    would mark akshare as a failed vendor, and if the rest of the chain also
    came up empty the router would surface that as the run's real error,
    hiding whatever actually went wrong.
    """
    try:
        return resolve_cn_symbol(symbol)
    except ValueError as exc:
        raise NoMarketDataError(symbol, symbol, f"not a Chinese market: {exc}") from exc


def load_cn_ohlcv(symbol: CnSymbol, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch and normalize daily bars for a Mainland or HK ticker.

    AkShare's end date is inclusive, so no +1 day dance is needed (unlike
    yfinance, whose ``end`` is exclusive).

    Proxy settings are left alone on purpose. These endpoints are domestic
    Chinese services, so in a typical Mainland network a direct connection is
    both faster and more reliable; but some networks (and some corporate VPNs)
    can only reach them a particular way. Standard library behaviour — honour
    ``HTTP_PROXY``/``HTTPS_PROXY`` and the Windows proxy configuration — lets
    the operator decide, and overriding it here broke a working setup.
    """
    import akshare as ak

    compact_start = start_date.replace("-", "")
    compact_end = end_date.replace("-", "")

    try:
        if symbol.market == "hk":
            raw = ak.stock_hk_hist(symbol=symbol.code, period="daily",
                                   start_date=compact_start, end_date=compact_end,
                                   adjust="qfq")
        else:
            raw = ak.stock_zh_a_hist(symbol=symbol.code, period="daily",
                                     start_date=compact_start, end_date=compact_end,
                                     adjust="qfq")
    except Exception as exc:
        # AkShare raises a wide variety of transport/parse errors; the router
        # needs the typed ones to decide whether to try the next vendor.
        raise NoMarketDataError(
            symbol.canonical, symbol.canonical,
            f"AkShare request failed: {type(exc).__name__}: {exc}",
        ) from exc

    if raw is None or raw.empty:
        raise NoMarketDataError(
            symbol.canonical, symbol.canonical,
            f"no rows between {start_date} and {end_date}",
        )

    frame = normalize_ohlcv(raw, symbol)
    if frame.empty:
        raise NoMarketDataError(
            symbol.canonical, symbol.canonical,
            f"no usable rows between {start_date} and {end_date}",
        )
    return frame
