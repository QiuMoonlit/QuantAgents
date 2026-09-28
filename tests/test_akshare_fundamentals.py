"""Tests for the A-share fundamentals mapping.

The hard part of Chinese fundamentals is not fetching them, it is that CAS line
items do not line up with the English names the shared interface uses. These
tests pin the mapping behaviour against vendor-shaped fixtures, including the
cases where a concept is only an approximation.
"""
from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from quantagent.dataflows.errors import NoMarketDataError
from quantagent.dataflows.vendors.akshare import fundamentals as cn_fund
from quantagent.dataflows.vendors.akshare.fundamentals import STATEMENTS


def _statement_frame(rows: dict[str, float]) -> pd.DataFrame:
    """A frame shaped like stock_financial_report_sina: period-major, one row
    per reporting period and one column per line item."""
    return pd.DataFrame({
        "报告日": ["2026-06-30", "2026-03-31", "2025-12-31"],
        **{k: [v, v * 0.9, v * 0.8] for k, v in rows.items()},
    })


def _patch(self, monkeypatch, frames: dict[str, pd.DataFrame], abstract=None):
    def report(stock, symbol):
        for key, frame in frames.items():
            if symbol == key:
                return frame
        raise AssertionError(f"unexpected statement {symbol!r}")

    fake = types.SimpleNamespace(
        stock_financial_report_sina=report,
        stock_financial_abstract=lambda symbol: abstract,
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)


def _patch_statements(monkeypatch, frames: dict[str, pd.DataFrame]) -> None:
    """Route Sina's statement endpoint at a table of canned frames."""

    def report(stock, symbol):
        if symbol in frames:
            return frames[symbol]
        raise AssertionError(f"unexpected statement {symbol!r}")

    fake = types.SimpleNamespace(
        stock_financial_report_sina=report,
        stock_financial_abstract=lambda symbol: None,
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)


def _patch_abstract(monkeypatch, frame) -> None:
    fake = types.SimpleNamespace(
        stock_financial_report_sina=lambda stock, symbol: pd.DataFrame(),
        stock_financial_abstract=lambda symbol: frame,
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)


class TestStatementMapping:
    def test_every_statement_declares_english_lines(self):
        for name in ("balance_sheet", "income_statement", "cashflow"):
            assert STATEMENTS[name], name
            for english, candidates, _note in STATEMENTS[name]:
                assert candidates, f"{name}/{english} has no Chinese candidates"

    def test_candidates_are_chinese_account_names(self):
        for lines in STATEMENTS.values():
            for _english, candidates, _note in lines:
                assert all(any(ord(c) > 127 for c in c) for c in candidates), candidates

    def test_a_mapped_line_renders_with_its_periods(self, monkeypatch):
        _patch_statements(monkeypatch, {
            "资产负债表": _statement_frame({
                "资产总计": 2.0e12, "流动资产合计": 8.0e11,
                "负债合计": 6.0e11, "所有者权益合计": 1.4e12,
            }),
        })
        out = cn_fund.get_cn_balance_sheet("600519.SS")
        assert "## Total Assets" in out
        assert "2026-06-30: 2e+12" in out or "2026-06-30: 2000000000000" in out

    def test_a_line_the_vendor_does_not_report_says_so_rather_than_guessing(self, monkeypatch):
        _patch_statements(monkeypatch, {
            "资产负债表": _statement_frame({"资产总计": 1.0e12}),
        })
        out = cn_fund.get_cn_balance_sheet("600519.SS")
        assert "## Cash and Equivalents" in out
        assert "not reported under this name" in out

    def test_the_first_matching_candidate_wins(self, monkeypatch):
        """营业收入 and 营业总收入 both exist for a bank; the narrower one is
        the right answer and the report must say which it used."""
        _patch_statements(monkeypatch, {
            "利润表": _statement_frame({
                "营业总收入": 9.9e11, "营业收入": 9.0e11, "净利润": 1.0e11,
            }),
        })
        out = cn_fund.get_cn_income_statement("600519.SS")
        assert "9e+11" in out or "900000000000" in out
        assert "9.9e+11" not in out and "990000000000" not in out
        assert "differ because banks report interest" in out

    def test_the_fallback_candidate_is_used_when_the_first_is_absent(self, monkeypatch):
        _patch_statements(monkeypatch, {
            "利润表": _statement_frame({"营业总收入": 9.9e11, "净利润": 1.0e11}),
        })
        out = cn_fund.get_cn_income_statement("600519.SS")
        assert "9.9e+11" in out or "990000000000" in out

    def test_a_direct_mapping_carries_no_caveat(self, monkeypatch):
        _patch_statements(monkeypatch, {
            "资产负债表": _statement_frame({"资产总计": 1.0e12}),
        })
        out = cn_fund.get_cn_balance_sheet("600519.SS")
        assert "differ because banks report interest" not in out

    def test_the_header_states_the_accounting_standard(self, monkeypatch):
        """A reader must not mistake CAS lines for us-gaap lines."""
        _patch_statements(monkeypatch, {"资产负债表": _statement_frame({"资产总计": 1.0e12})})
        out = cn_fund.get_cn_balance_sheet("600519.SS")
        assert "China Accounting Standards" in out
        assert "中国企业会计准则" in out


class TestStatementFailuresAreTyped:
    def test_a_us_ticker_is_a_fall_through_not_a_vendor_break(self):
        for fn in (cn_fund.get_cn_balance_sheet, cn_fund.get_cn_income_statement,
                   cn_fund.get_cn_cashflow, cn_fund.get_cn_fundamentals):
            with pytest.raises(NoMarketDataError):
                fn("AAPL")

    def test_hong_kong_is_reported_as_unavailable_not_silently_empty(self, monkeypatch):
        """Sina does not serve HK statements; saying so beats an empty report."""
        _patch_statements(monkeypatch, {"资产负债表": _statement_frame({"资产总计": 1.0})})
        with pytest.raises(NoMarketDataError) as caught:
            cn_fund.get_cn_balance_sheet("0700.HK")
        assert "Hong Kong" in str(caught.value)

    def test_a_vendor_exception_becomes_no_market_data(self, monkeypatch):
        def boom(stock, symbol):
            raise ConnectionError("no route")
        fake = types.SimpleNamespace(stock_financial_report_sina=boom)
        monkeypatch.setitem(sys.modules, "akshare", fake)
        with pytest.raises(NoMarketDataError):
            cn_fund.get_cn_balance_sheet("600519.SS")

    def test_an_empty_statement_is_an_absence(self, monkeypatch):
        fake = types.SimpleNamespace(
            stock_financial_report_sina=lambda stock, symbol: pd.DataFrame())
        monkeypatch.setitem(sys.modules, "akshare", fake)
        with pytest.raises(NoMarketDataError):
            cn_fund.get_cn_balance_sheet("600519.SS")

    def test_abstract_with_none_of_the_expected_metrics_raises(self, monkeypatch):
        """Better to fail loudly than hand the analyst a report of N/A rows."""
        junk = pd.DataFrame({"选项": ["常用指标"], "指标": ["每股收益(元)"],
                             "20260630": [1.0]})
        _patch_abstract(monkeypatch, junk)
        with pytest.raises(NoMarketDataError) as caught:
            cn_fund.get_cn_fundamentals("600519.SS")
        assert "none of the expected metrics" in str(caught.value)


class TestAbstractMetrics:
    def _abstract(self) -> pd.DataFrame:
        return pd.DataFrame({
            "选项": ["常用指标"] * 4,
            "指标": ["基本每股收益", "净资产收益率(ROE)", "销售毛利率", "净利润"],
            "20260630": [3.5, 18.2, 91.4, 3.4e11],
            "20260331": [1.8, 9.1, 90.2, 1.7e11],
        })

    def test_renders_the_english_name_and_the_chinese_source(self, monkeypatch):
        _patch_abstract(monkeypatch, self._abstract())
        out = cn_fund.get_cn_fundamentals("600519.SS")
        assert "## Basic EPS (基本每股收益)" in out
        assert "## ROE (净资产收益率(ROE))" in out
        assert "18.2" in out

    def test_unknown_metrics_are_simply_absent_not_fabricated(self, monkeypatch):
        _patch_abstract(monkeypatch, self._abstract())
        out = cn_fund.get_cn_fundamentals("600519.SS")
        assert "Diluted EPS" not in out, "the vendor did not report it"
        assert "Gross Margin" in out


class TestRouterRegistration:
    @pytest.mark.parametrize("method", [
        "get_fundamentals", "get_balance_sheet", "get_cashflow",
        "get_income_statement",
    ])
    def test_akshare_is_registered_for_the_fundamental_methods(self, method):
        from quantagent.dataflows.router import VENDOR_METHODS
        assert "akshare" in VENDOR_METHODS[method]

    def test_sec_edgar_stays_registered_for_us_tickers(self):
        """Both are registered; the chain, not the table, decides which runs."""
        from quantagent.dataflows.router import VENDOR_METHODS
        assert "sec_edgar" in VENDOR_METHODS["get_balance_sheet"]
        assert "akshare" in VENDOR_METHODS["get_balance_sheet"]
