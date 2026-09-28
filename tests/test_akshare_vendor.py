"""Tests for the A-share / Hong Kong vendor.

These use AkShare-shaped fixtures rather than the network. Reachability of
eastmoney/sina varies by network and by proxy configuration, and correctness of
column mapping, unit conversion and error typing must not depend on either.

Fixtures mirror what ``ak.stock_zh_a_hist`` actually returns: Chinese column
names, 成交量 in 手, and extra columns (成交额, 换手率) that no shared code
reads.
"""
from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from quantagent.dataflows.errors import NoMarketDataError
from quantagent.dataflows.vendors.akshare import ohlcv as cn_ohlcv
from quantagent.dataflows.vendors.akshare.ohlcv import (
    CN_MAX_OHLCV_STALE_DAYS,
    normalize_ohlcv,
    resolve_cn_symbol,
)

SH = resolve_cn_symbol("600519.SS")
SZ = resolve_cn_symbol("000001.SZ")
HK = resolve_cn_symbol("0700.HK")


def _a_share_frame(n: int = 5) -> pd.DataFrame:
    """A frame shaped exactly like stock_zh_a_hist's output.

    成交额 is derived from 成交量 x price so the lot-size cross-check in
    TestNormalizeOhlcv is meaningful. The 100x lot size is the real one: on
    live data, 23248 手 x 100 x 1295 CNY = 3.0098e9 against a reported turnover
    of 3.0030e9.
    """
    dates = pd.date_range("2026-08-25", periods=n, freq="D")
    lots = [23248, 32664, 20308, 18000, 21000][:n]
    high = [1305.0 + i for i in range(n)]
    low = [1286.0 + i for i in range(n)]
    return pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "股票代码": ["600519"] * n,
        "开盘": [1290.0 + i for i in range(n)],
        "收盘": [1295.0 + i for i in range(n)],
        "最高": high,
        "最低": low,
        "成交量": lots,                                        # 手
        "成交额": [lot * 100 * ((hi + lo) / 2)
                   for lot, hi, lo in zip(lots, high, low, strict=True)],
        "换手率": [0.19] * n,
    })


def _hk_frame(n: int = 5) -> pd.DataFrame:
    """stock_hk_hist uses English-ish headers but the same Chinese core."""
    dates = pd.date_range("2026-08-25", periods=n, freq="D")
    return pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "开盘": [500.0 + i for i in range(n)],
        "收盘": [505.0 + i for i in range(n)],
        "最高": [510.0 + i for i in range(n)],
        "最低": [495.0 + i for i in range(n)],
        "成交量": [1000, 2000, 1500, 1200, 1800],        # 手
    })


class TestSymbolResolution:
    @pytest.mark.parametrize("raw,expected", [
        ("600519.SS", "600519.SS"),
        ("000001.SZ", "000001.SZ"),
        ("0700.HK", "00700.HK"),
        ("700.HK", "00700.HK"),
        ("600519", "600519.SS"),
        ("000001", "000001.SZ"),
    ])
    def test_resolves_every_spelling(self, raw, expected):
        assert resolve_cn_symbol(raw).canonical == expected

    def test_shanghai_is_ss_not_sh(self):
        """The rest of QuantAgent spells Shanghai .SS; emitting .SH would make
        this vendor disagree with benchmark_map and normalize_symbol."""
        assert resolve_cn_symbol("600519.SS").canonical.endswith(".SS")
        assert resolve_cn_symbol("600519.SH").canonical == "600519.SS"

    def test_leading_digit_picks_the_board(self):
        assert resolve_cn_symbol("600000").market == "sh"
        assert resolve_cn_symbol("688981").market == "sh", "STAR market is Shanghai"
        assert resolve_cn_symbol("000001").market == "sz"
        assert resolve_cn_symbol("300750").market == "sz", "ChiNext is Shenzhen"

    def test_hk_is_zero_padded_to_five(self):
        assert resolve_cn_symbol("700.HK").code == "00700"
        assert resolve_cn_symbol("9992.HK").code == "09992"

    def test_prefixed_form_is_what_sina_wants(self):
        assert resolve_cn_symbol("600519.SS").prefixed == "sh600519"
        assert resolve_cn_symbol("000001.SZ").prefixed == "sz000001"

    def test_exchange_suffix_matches_benchmark_map_keys(self):
        from quantagent.default_config import DEFAULT_CONFIG
        mapping = DEFAULT_CONFIG["benchmark_map"]
        for cn in (SH, SZ, HK):
            assert cn.exchange_suffix in mapping, cn.canonical

    @pytest.mark.parametrize("us_or_crypto", [
        "AAPL", "BTC-USD", "0700.T", "^GSPC", "SPY", "",
    ])
    def test_rejects_non_chinese_tickers_so_the_router_falls_through(self, us_or_crypto):
        """A US symbol must raise here, not return an empty frame, so the
        router can move to the next vendor instead of reporting 'no data'."""
        with pytest.raises(ValueError):
            resolve_cn_symbol(us_or_crypto)

    def test_is_case_insensitive(self):
        assert resolve_cn_symbol("600519.ss") == resolve_cn_symbol("600519.SS")


class TestNormalizeOhlcv:
    def test_maps_chinese_columns_onto_the_shared_contract(self):
        out = normalize_ohlcv(_a_share_frame(), SH)
        assert list(out.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]

    def test_drops_columns_nothing_reads(self):
        out = normalize_ohlcv(_a_share_frame(), SH)
        for junk in ("成交额", "换手率", "股票代码"):
            assert junk not in out.columns

    def test_converts_volume_from_lots_to_shares(self):
        out = normalize_ohlcv(_a_share_frame(), SH)
        assert out["Volume"].iloc[0] == 23248 * 100

    def test_hk_volume_is_passed_through_not_guessed(self):
        """HK board lots vary per security and AkShare does not document the
        unit, so no multiplier is applied rather than a possibly-wrong one."""
        out = normalize_ohlcv(_hk_frame(), HK)
        assert out["Volume"].iloc[0] == 1000

    def test_mainland_conversion_matches_the_vendors_own_turnover(self):
        """成交额 ships alongside 成交量, so after the lot conversion the two
        must agree. A wrong multiplier shows up here as a 100x divergence."""
        raw = _a_share_frame()
        out = normalize_ohlcv(raw, SH)
        price = (raw["最高"] + raw["最低"]) / 2
        ratio = (out["Volume"] * price) / raw["成交额"]
        assert ratio.between(0.999, 1.001).all()

    def test_a_wrong_lot_size_would_fail_that_check(self):
        """Guards the guard: without the x100 the ratio is 100, not 1."""
        raw = _a_share_frame()
        unconverted = raw["成交量"]  # 手, unconverted
        price = (raw["最高"] + raw["最低"]) / 2
        ratio = (unconverted * price) / raw["成交额"]
        assert not ratio.between(0.999, 1.001).any()

    def test_prices_are_numeric(self):
        out = normalize_ohlcv(_a_share_frame(), SH)
        for col in ("Open", "High", "Low", "Close", "Volume"):
            assert pd.api.types.is_numeric_dtype(out[col]), col

    def test_dates_are_naive_midnight(self):
        out = normalize_ohlcv(_a_share_frame(), SH)
        assert out["Date"].dt.tz is None
        assert (out["Date"] == out["Date"].dt.normalize()).all()

    def test_sorted_ascending(self):
        frame = _a_share_frame().iloc[::-1]
        out = normalize_ohlcv(frame, SH)
        assert out["Date"].is_monotonic_increasing

    def test_empty_input_yields_an_empty_contract_frame(self):
        out = normalize_ohlcv(pd.DataFrame(), SH)
        assert out.empty
        assert list(out.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]

    def test_none_input_yields_an_empty_frame(self):
        assert normalize_ohlcv(None, SH).empty

    def test_a_frame_without_a_date_column_is_a_typed_failure(self):
        with pytest.raises(NoMarketDataError):
            normalize_ohlcv(pd.DataFrame({"开盘": [1.0]}), SH)

    def test_unparseable_values_become_nan_not_an_exception(self):
        frame = _a_share_frame()
        # Widen to object first: pandas 3 refuses a string into a float64 column.
        frame["收盘"] = frame["收盘"].astype(object)
        frame.loc[2, "收盘"] = "n/a"
        out = normalize_ohlcv(frame, SH)
        assert pd.isna(out["Close"].iloc[2])
        assert out["Close"].iloc[0] == 1295.0
        assert out["Close"].iloc[4] == 1299.0


class TestStaleThreshold:
    def test_cn_threshold_clears_spring_festival(self):
        """Spring Festival runs ~10 calendar days; Yahoo's 10-day threshold
        would call a correct frame stale right after it."""
        assert CN_MAX_OHLCV_STALE_DAYS > 10

    def test_cn_threshold_is_bounded(self):
        assert CN_MAX_OHLCV_STALE_DAYS <= 30, (
            "loose enough to clear a holiday, tight enough to catch a year-old frame"
        )


class TestLoadErrorsAreTyped:
    """The router distinguishes NoMarketDataError (try the next vendor) from
    a programming error, so AkShare's wide exception surface is wrapped."""

    def _patch_akshare(self, monkeypatch, exc: Exception):
        fake = types.SimpleNamespace(
            stock_zh_a_hist=lambda **kw: (_ for _ in ()).throw(exc),
            stock_hk_hist=lambda **kw: (_ for _ in ()).throw(exc),
        )
        monkeypatch.setitem(sys.modules, "akshare", fake)

    @pytest.mark.parametrize("exc", [
        RuntimeError("boom"),
        ConnectionError("no route"),
        ValueError("bad json"),
        TimeoutError("slow"),
    ])
    def test_any_vendor_failure_becomes_no_market_data(self, monkeypatch, exc):
        self._patch_akshare(monkeypatch, exc)
        with pytest.raises(NoMarketDataError) as caught:
            cn_ohlcv.load_cn_ohlcv(SH, "2026-08-20", "2026-09-02")
        assert "AkShare request failed" in str(caught.value)

    def test_an_empty_response_is_an_absence_not_a_crash(self, monkeypatch):
        fake = types.SimpleNamespace(
            stock_zh_a_hist=lambda **kw: pd.DataFrame(),
            stock_hk_hist=lambda **kw: pd.DataFrame(),
        )
        monkeypatch.setitem(sys.modules, "akshare", fake)
        with pytest.raises(NoMarketDataError) as caught:
            cn_ohlcv.load_cn_ohlcv(SH, "2026-08-20", "2026-09-02")
        assert "no rows" in str(caught.value)

    def test_a_response_that_normalizes_to_nothing_is_still_an_absence(self, monkeypatch):
        junk = pd.DataFrame({"日期": ["not-a-date"], "开盘": [1.0], "收盘": [1.0]})
        fake = types.SimpleNamespace(
            stock_zh_a_hist=lambda **kw: junk,
            stock_hk_hist=lambda **kw: junk,
        )
        monkeypatch.setitem(sys.modules, "akshare", fake)
        with pytest.raises(NoMarketDataError):
            cn_ohlcv.load_cn_ohlcv(SH, "2026-08-20", "2026-09-02")

    def test_the_correct_akshare_function_is_called_per_market(self, monkeypatch):
        calls = []

        fake = types.SimpleNamespace(
            stock_zh_a_hist=lambda **kw: (calls.append(("a", kw)), _a_share_frame())[1],
            stock_hk_hist=lambda **kw: (calls.append(("hk", kw)), _hk_frame())[1],
        )
        monkeypatch.setitem(sys.modules, "akshare", fake)

        cn_ohlcv.load_cn_ohlcv(SH, "2026-08-20", "2026-09-02")
        cn_ohlcv.load_cn_ohlcv(HK, "2026-08-20", "2026-09-02")

        assert [c[0] for c in calls] == ["a", "hk"]
        assert calls[0][1]["symbol"] == "600519"
        assert calls[1][1]["symbol"] == "00700"
        assert calls[0][1]["start_date"] == "20260820", "AkShare wants YYYYMMDD"


class TestRouterIntegration:
    """The registered functions must raise the type the router falls through on.

    A bare ValueError would be logged as "this vendor broke" and, if the rest of
    the chain also came up empty, would surface as the run's error — hiding the
    real cause behind a non-event.
    """

    @pytest.mark.parametrize("vendor_fn,args", [
        ("get_cn_stock_data", ("AAPL", "2026-08-20", "2026-09-02")),
        ("get_cn_stock_stats_window", ("AAPL", "rsi", "2026-09-01", 30)),
        ("get_cn_stock_stats_indicator", ("AAPL", "rsi", "2026-09-01")),
        ("get_cn_stock_stats_bulk", ("AAPL", "rsi", "2026-09-01")),
        ("get_cn_closes", ("AAPL", "2026-08-20", "2026-09-02")),
    ])
    def test_a_us_ticker_raises_no_market_data_not_value_error(self, vendor_fn, args):
        import pytest as _pytest

        from quantagent.dataflows.vendors.akshare import market as cn_market

        with _pytest.raises(NoMarketDataError):
            getattr(cn_market, vendor_fn)(*args)

    def test_akshare_is_registered_for_the_two_methods_it_serves(self):
        from quantagent.dataflows.router import VENDOR_LIST, VENDOR_METHODS

        assert "akshare" in VENDOR_LIST
        assert "akshare" in VENDOR_METHODS["get_stock_data"]
        assert "akshare" in VENDOR_METHODS["get_indicators"]

    def test_it_is_not_registered_for_methods_it_does_not_implement(self):
        """Registering it for, say, fundamentals would make the router call a
        name that does not exist."""
        from quantagent.dataflows import router

        for method, impls in router.VENDOR_METHODS.items():
            fn = impls.get("akshare")
            assert fn is None or callable(fn), method

    def test_a_non_chinese_symbol_reports_why_it_declined(self):
        from quantagent.dataflows.vendors.akshare.ohlcv import resolve_cn_symbol_or_skip

        with pytest.raises(NoMarketDataError) as caught:
            resolve_cn_symbol_or_skip("AAPL")
        assert "not a Chinese market" in str(caught.value)

    def test_get_closes_routes_so_chinese_decisions_can_settle(self):
        """Settlement used to import yfinance's get_closes directly, so a
        Chinese ticker's outcome never resolved — and fetch_returns swallows
        that failure, so the only symptom was a permanently pending decision
        and a warning nobody reads."""
        from quantagent.dataflows.router import VENDOR_METHODS, get_category_for_method

        assert "akshare" in VENDOR_METHODS["get_closes"]
        assert get_category_for_method("get_closes") == "core_stock_apis"

    def test_settlement_no_longer_hardcodes_a_vendor(self):
        """Regression guard: any direct vendor import there reintroduces the
        silent-pending bug."""
        import ast
        from pathlib import Path

        from quantagent.graph import settlement

        tree = ast.parse(Path(settlement.__file__).read_text(encoding="utf-8"))
        for node in tree.body:  # module scope only
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "vendors.yahoo" not in node.module, (
                    "settlement must route through the registry, not import a vendor"
                )

    def test_get_closes_is_not_offered_to_agents_as_a_tool(self):
        """It belongs in the registry but not on an analyst's tool list."""
        from quantagent.dataflows.router import TOOLS_CATEGORIES

        for info in TOOLS_CATEGORIES.values():
            assert "get_closes" not in info["tools"]


class TestLayering:
    def test_akshare_is_imported_lazily_not_at_module_scope(self):
        """Importing quantagent must not pull a heavy scraping library in.

        Only module-scope imports are checked: the fetch functions import
        akshare inside the function body, which is the point.
        """
        import ast
        from pathlib import Path

        tree = ast.parse(Path(cn_ohlcv.__file__).read_text(encoding="utf-8"))
        for node in tree.body:  # module scope only
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "akshare", "akshare must be imported lazily"
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("akshare"), "akshare must be lazy"
