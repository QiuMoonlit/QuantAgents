"""Tests for the Chinese news and sentiment vendors.

Neither source is a per-stock message stream. News is a vendor-side article
list with no date argument, so the window can only be filtered. Sentiment is a
set of per-stock indices (attention, scorecard, popularity) rather than posts,
which changes what the analyst can legitimately claim. These tests pin that
distinction, because rendering an index as though it were a post count would be
the easy mistake.
"""
from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from quantagent.dataflows.errors import NoMarketDataError
from quantagent.dataflows.vendors.akshare import news as cn_news, sentiment as cn_sent
from quantagent.dataflows.vendors.akshare.ohlcv import is_chinese_symbol


def _news_frame(rows: list[tuple[str, str]] | None = None) -> pd.DataFrame:
    """stock_news_em shape: rows are articles, columns are Chinese fields."""
    rows = rows or [
        ("2026-09-01 09:00:00", "茅台批价企稳"),
        ("2026-08-30 15:00:00", "公司公告：回购进展"),
        ("2026-08-20 10:00:00", "半年报点评"),
    ]
    return pd.DataFrame({
        "关键词": ["600519"] * len(rows),
        "新闻标题": [t for _, t in rows],
        "新闻内容": [f"正文：{t}。" for _, t in rows],
        "发布时间": [d for d, _ in rows],
        "文章来源": ["证券时报"] * len(rows),
        "新闻链接": ["http://example.com/1"] * len(rows),
    })


def _attention_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "交易日": pd.bdate_range(end="2026-09-01", periods=5).strftime("%Y-%m-%d"),
        "用户关注指数": [92.0, 93.5, 94.0, 94.0, 94.8],
    })


def _comment_table() -> pd.DataFrame:
    return pd.DataFrame({
        "代码": ["600519", "000001"],
        "名称": ["贵州茅台", "平安银行"],
        "最新价": [1243.88, 11.3],
        "涨跌幅": [0.56, 0.0],
        "换手率": [0.23, 0.37],
        "主力成本": [1236.33, 11.1],
        "机构参与度": [0.503, 0.4],
        "综合得分": [75.9, 60.0],
        "关注指数": [94.8, 70.0],
    })


def _hot_rank_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "时间": pd.bdate_range(end="2026-09-01", periods=4).strftime("%Y-%m-%d"),
        "排名": [40, 30, 20, 17],
        "证券代码": ["SH600519"] * 4,
        "新晋粉丝": [0.44, 0.40, 0.38, None],
        "铁杆粉丝": [0.56, 0.60, 0.62, None],
    })


def _patch(monkeypatch, **overrides):
    """Install a fake akshare module; each source returns its fixture."""
    base = {
        "stock_news_em": lambda symbol: _news_frame(),
        "stock_comment_detail_scrd_focus_em": lambda symbol: _attention_frame(),
        "stock_comment_em": lambda: _comment_table(),
        "stock_hot_rank_detail_em": lambda symbol: _hot_rank_frame(),
        "news_economic_baidu": lambda date: pd.DataFrame({"标题": ["央行降准"]}),
    }
    base.update(overrides)
    monkeypatch.setitem(sys.modules, "akshare", types.SimpleNamespace(**base))
    # The market table is cached for the process's lifetime, so a test that
    # inherits a previous one's fixture would silently not call the stub.
    monkeypatch.setattr(cn_sent, "_comment_table", None)
    # Replace the market-table fetch with the fixture, but only when the caller
    # did not supply its own stock_comment_em. Otherwise the real
    # _market_comment_table runs and wraps whatever the stub raises, which is
    # the behaviour a total-failure test needs to exercise.
    if "stock_comment_em" not in overrides:
        monkeypatch.setattr(cn_sent, "_market_comment_table", base["stock_comment_em"])


class TestSymbolDiscrimination:
    """The source is picked before any data is fetched."""

    @pytest.mark.parametrize("sym,expected", [
        ("600519.SS", True), ("000001.SZ", True), ("0700.HK", True), ("600519", True),
        ("NVDA", False), ("BTC-USD", False), ("^GSPC", False), ("SPY", False), ("", False),
    ])
    def test_is_chinese_symbol(self, sym, expected):
        assert is_chinese_symbol(sym) is expected

    def test_a_us_ticker_declines_both_new_sources(self):
        for fn, args in ((cn_news.get_cn_news, ("NVDA", "2026-08-25", "2026-09-01")),
                         (cn_sent.get_cn_sentiment, ("NVDA",))):
            with pytest.raises(NoMarketDataError) as caught:
                fn(*args)
            assert "not a Chinese market" in str(caught.value), fn.__name__

    def test_hong_kong_news_is_reported_unavailable_not_empty(self, monkeypatch):
        _patch(monkeypatch)
        with pytest.raises(NoMarketDataError) as caught:
            cn_news.get_cn_news("0700.HK", "2026-08-25", "2026-09-01")
        assert "Hong Kong" in str(caught.value)


class TestNewsWindowing:
    def test_only_articles_inside_the_window_are_rendered(self, monkeypatch):
        _patch(monkeypatch)
        out = cn_news.get_cn_news("600519.SS", "2026-08-29", "2026-09-02")
        assert "茅台批价企稳" in out
        assert "半年报点评" not in out, "2026-08-20 is outside the window"

    def test_an_article_after_the_window_is_excluded(self, monkeypatch):
        """Look-ahead: a historical run must not see a later headline. The
        comparison is on calendar days, so an article published at 09:00 on the
        end date counts as inside and the next day does not."""
        _patch(monkeypatch)
        out = cn_news.get_cn_news("600519.SS", "2026-08-15", "2026-08-25")
        assert "半年报点评" in out, "2026-08-20 is inside"
        assert "茅台批价企稳" not in out, "2026-09-01 is not"
        assert "回购进展" not in out, "2026-08-30 is not"

    def test_the_end_date_itself_is_included_despite_its_publish_time(self, monkeypatch):
        _patch(monkeypatch)
        out = cn_news.get_cn_news("600519.SS", "2026-08-25", "2026-09-01")
        assert "茅台批价企稳" in out, "published 09:00 on the end date"

    def test_newest_first(self, monkeypatch):
        _patch(monkeypatch)
        out = cn_news.get_cn_news("600519.SS", "2026-08-01", "2026-09-02")
        assert out.index("茅台批价企稳") < out.index("半年报点评")

    def test_an_empty_window_says_why_rather_than_reporting_no_news(self, monkeypatch):
        _patch(monkeypatch)
        with pytest.raises(NoMarketDataError) as caught:
            cn_news.get_cn_news("600519.SS", "2020-01-01", "2020-01-31")
        detail = str(caught.value)
        assert "no articles between" in detail
        assert "2026-09-01" in detail, "it should say how new the vendor's data is"

    def test_the_response_shape_is_checked(self, monkeypatch):
        _patch(monkeypatch, stock_news_em=lambda symbol: pd.DataFrame({"foo": [1]}))
        with pytest.raises(NoMarketDataError) as caught:
            cn_news.get_cn_news("600519.SS", "2026-08-25", "2026-09-02")
        assert "新闻标题" in str(caught.value)

    def test_a_vendor_failure_is_typed(self, monkeypatch):
        def boom(symbol):
            raise ConnectionError("no route")
        _patch(monkeypatch, stock_news_em=boom)
        with pytest.raises(NoMarketDataError):
            cn_news.get_cn_news("600519.SS", "2026-08-25", "2026-09-02")

    def test_the_source_is_named_so_the_analyst_knows_the_provenance(self, monkeypatch):
        _patch(monkeypatch)
        out = cn_news.get_cn_news("600519.SS", "2026-08-25", "2026-09-02")
        assert "东方财富" in out
        assert "600519.SS" in out


class TestMacroNews:
    def test_it_falls_back_to_earlier_days(self, monkeypatch):
        """A date the vendor has not published yet is normal, not a failure."""
        _patch(monkeypatch, news_economic_baidu=lambda date: (
            pd.DataFrame({"标题": ["央行降准"]}) if date == "20260830" else pd.DataFrame()))
        out = cn_news.get_cn_global_news("2026-09-01", look_back_days=7)
        assert "央行降准" in out
        assert "20260830" in out

    def test_every_day_failing_is_reported_with_the_reasons(self, monkeypatch):
        def boom(date):
            raise TimeoutError("slow")
        _patch(monkeypatch, news_economic_baidu=boom)
        with pytest.raises(NoMarketDataError) as caught:
            cn_news.get_cn_global_news("2026-09-01", look_back_days=3)
        assert "no macro news available" in str(caught.value)
        assert "TimeoutError" in str(caught.value), "the reason should be visible"


class TestAttentionIndex:
    def test_it_renders_the_series_and_the_change(self, monkeypatch):
        _patch(monkeypatch)
        out = cn_sent.get_cn_retail_attention("600519.SS")
        assert "用户关注指数" in out
        assert "94.8" in out
        assert "+2.8" in out, "92.0 -> 94.8 over the window"

    def test_it_is_labelled_as_attention_not_direction(self, monkeypatch):
        """An index says how much people are watching, not whether they are
        positive. The analyst must not be able to read it as a score."""
        _patch(monkeypatch)
        out = cn_sent.get_cn_retail_attention("600519.SS")
        assert "not of direction" in out
        assert "not of direction" in out or "not a measure of direction" in out

    def test_a_single_row_still_renders(self, monkeypatch):
        _patch(monkeypatch, stock_comment_detail_scrd_focus_em=lambda symbol: pd.DataFrame(
            {"交易日": ["2026-09-01"], "用户关注指数": [94.8]}))
        out = cn_sent.get_cn_retail_attention("600519.SS")
        assert "94.8" in out

    def test_an_empty_response_is_typed(self, monkeypatch):
        _patch(monkeypatch, stock_comment_detail_scrd_focus_em=lambda symbol: pd.DataFrame())
        with pytest.raises(NoMarketDataError):
            cn_sent.get_cn_retail_attention("600519.SS")

    def test_a_missing_column_is_caught_rather_than_rendered_blank(self, monkeypatch):
        _patch(monkeypatch, stock_comment_detail_scrd_focus_em=lambda symbol: pd.DataFrame(
            {"交易日": ["2026-09-01"], "别的": [1.0]}))
        with pytest.raises(NoMarketDataError) as caught:
            cn_sent.get_cn_retail_attention("600519.SS")
        assert "用户关注指数" in str(caught.value)


class TestScorecard:
    def test_it_renders_the_real_columns(self, monkeypatch):
        _patch(monkeypatch)
        out = cn_sent.get_cn_retail_sentiment("600519.SS")
        assert "综合得分" in out and "75.9" in out
        assert "机构参与度" in out
        assert "主力成本" in out

    def test_price_versus_cost_basis_is_explained(self, monkeypatch):
        _patch(monkeypatch)
        out = cn_sent.get_cn_retail_sentiment("600519.SS")
        assert "in profit" in out, "1243.88 is above the 1236.33 cost basis"
        assert "not a target" in out or "not a price target" in out, \
            "a cost basis is not a price target"

    def test_an_int_code_column_still_matches(self, monkeypatch):
        """Some frames read 代码 as int64; string comparison must still work."""
        table = _comment_table()
        table["代码"] = [600519, 1]
        _patch(monkeypatch, stock_comment_em=lambda: table)
        assert "75.9" in cn_sent.get_cn_retail_sentiment("600519.SS")

    def test_a_stock_absent_from_the_table_is_typed(self, monkeypatch):
        _patch(monkeypatch)
        with pytest.raises(NoMarketDataError) as caught:
            cn_sent.get_cn_retail_sentiment("300750.SZ")
        assert "not present" in str(caught.value)

    def test_a_row_with_no_score_is_refused(self, monkeypatch):
        table = _comment_table()
        table["综合得分"] = [float("nan"), 60.0]
        _patch(monkeypatch, stock_comment_em=lambda: table)
        with pytest.raises(NoMarketDataError):
            cn_sent.get_cn_retail_sentiment("600519.SS")


class TestPopularityTrend:
    def test_it_renders_the_rank_series(self, monkeypatch):
        _patch(monkeypatch)
        out = cn_sent.get_cn_popularity_trend("600519.SS")
        assert "人气排名" in out
        assert "40 -> 17" in out, "improving, so the text should say so"

    def test_the_fan_split_is_explained(self, monkeypatch):
        _patch(monkeypatch)
        out = cn_sent.get_cn_popularity_trend("600519.SS")
        assert "新晋粉丝" in out and "铁杆粉丝" in out
        assert "44.00%" in out, "0.44 should render as a percentage"

    def test_a_missing_fan_split_renders_blank_not_nan(self, monkeypatch):
        """The live endpoint returns NaN for the most recent row."""
        _patch(monkeypatch)
        out = cn_sent.get_cn_popularity_trend("600519.SS")
        assert "nan" not in out.lower()

    def test_a_worsening_rank_says_so(self, monkeypatch):
        frame = _hot_rank_frame()
        frame["排名"] = [10, 20, 30, 40]
        _patch(monkeypatch, stock_hot_rank_detail_em=lambda symbol: frame)
        assert "worsened" in cn_sent.get_cn_popularity_trend("600519.SS")


class TestCompositeBlock:
    def test_it_joins_all_three(self, monkeypatch):
        _patch(monkeypatch)
        out = cn_sent.get_cn_sentiment("600519.SS")
        assert "综合得分" in out and "用户关注指数" in out and "人气排名" in out

    def test_a_partial_failure_keeps_what_worked(self, monkeypatch):
        """One dead source must not cost the analyst the other two."""
        def boom(symbol):
            raise ConnectionError("down")
        _patch(monkeypatch, stock_comment_detail_scrd_focus_em=boom)
        out = cn_sent.get_cn_sentiment("600519.SS")
        assert "综合得分" in out
        assert "unavailable sources" in out
        assert "ConnectionError" in out

    def test_total_failure_raises_rather_than_returning_an_empty_block(self, monkeypatch):
        def boom(*a, **k):
            raise ConnectionError("down")
        _patch(monkeypatch,
               stock_comment_detail_scrd_focus_em=boom,
               stock_comment_em=boom,
               stock_hot_rank_detail_em=boom)
        with pytest.raises(NoMarketDataError) as caught:
            cn_sent.get_cn_sentiment("600519.SS")
        assert "every Chinese sentiment source" in str(caught.value)

    def test_the_jev_screen_argument_is_accepted_for_signature_parity(self, monkeypatch):
        """The analyst calls one shape for every market; the Chinese sources
        have nothing to screen because they are already per-stock indices."""
        _patch(monkeypatch)
        assert "综合得分" in cn_sent.get_cn_sentiment("600519.SS", screen=object())


class TestRouterRegistration:
    @pytest.mark.parametrize("method", ["get_news", "get_global_news", "get_sentiment"])
    def test_akshare_is_registered(self, method):
        from quantagent.dataflows.router import VENDOR_METHODS
        assert "akshare" in VENDOR_METHODS[method]

    def test_the_us_vendors_stay_registered(self):
        from quantagent.dataflows.router import VENDOR_METHODS
        assert "yfinance" in VENDOR_METHODS["get_sentiment"]
        assert "yfinance" in VENDOR_METHODS["get_news"]

    def test_sentiment_has_its_own_category(self):
        from quantagent.dataflows.router import get_category_for_method
        assert get_category_for_method("get_sentiment") == "sentiment_data"

    def test_the_default_chain_leads_with_akshare_for_sentiment_and_news(self):
        from quantagent.default_config import DEFAULT_CONFIG
        for category in ("news_data", "sentiment_data"):
            assert DEFAULT_CONFIG["data_vendors"][category].startswith("akshare"), category

    def test_the_analyst_no_longer_imports_the_western_platforms(self):
        """The whole point: a 600519 must not be sent to StockTwits."""
        import ast
        from pathlib import Path

        from quantagent.agents.analysts import sentiment_analyst

        tree = ast.parse(Path(sentiment_analyst.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "stocktwits" not in node.module
                assert "reddit" not in node.module

    def test_the_us_pair_is_still_reachable_through_the_router(self, monkeypatch):
        """Moving the composition into a vendor must not lose the sources."""
        from quantagent.dataflows.vendors import us_sentiment

        calls = []
        monkeypatch.setattr(us_sentiment, "fetch_stocktwits_messages",
                            lambda *a, **k: calls.append("st") or "st")
        monkeypatch.setattr(us_sentiment, "fetch_reddit_posts",
                            lambda *a, **k: calls.append("rd") or "rd")
        out = us_sentiment.get_us_sentiment("NVDA", screen=lambda posts: posts)
        assert calls == ["st", "rd"]
        assert "st" in out and "rd" in out
