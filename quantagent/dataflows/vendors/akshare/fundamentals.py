"""A-share and Hong Kong fundamentals via AkShare.

Chinese statements follow 中国企业会计准则 (CAS), not us-gaap, so the line items
have no one-to-one equivalent. :data:`STATEMENTS` below maps each English line
the shared interface expects onto the Chinese account names that actually
carry it, taking the first match present in the vendor's columns.

A mapping table is the honest way to express this. Presenting CAS line items
under English names as though they were the same concept would be worse: 资产
and Assets are close, but 营业收入 is not Revenue (it is total operating
revenue, before the deductions that make Revenue meaningful), and 归母净利润
is net profit attributable to the parent, a narrower figure than Net Income.
Each mapping below notes where the concepts differ.

AkShare's ``stock_financial_report_sina`` returns one row per line item and one
column per reporting period, newest first, which is the same shape the SEC
EDGAR vendor parses — see ``vendors/sec_edgar.py`` for the reference
implementation of the date filtering.
"""

from __future__ import annotations

import logging

import pandas as pd

from quantagent.dataflows.errors import NoMarketDataError
from quantagent.dataflows.vendors.akshare.ohlcv import resolve_cn_symbol_or_skip

logger = logging.getLogger(__name__)

# Sina's endpoint names the three statements; the HK exchange publishes under
# a different set of names, handled separately below.
_SINA_STATEMENTS = {
    "balance_sheet": "资产负债表",
    "income_statement": "利润表",
    "cashflow": "现金流量表",
}

# (english line, [chinese account names in preference order], note)
STATEMENTS: dict[str, list[tuple[str, list[str], str]]] = {
    "balance_sheet": [
        ("Total Assets", ["资产总计", "资产合计"], "direct"),
        ("Current Assets", ["流动资产合计", "流动资产"], "direct"),
        ("Cash and Equivalents", ["货币资金"], "direct"),
        ("Total Liabilities", ["负债合计", "负债总计"], "direct"),
        ("Current Liabilities", ["流动负债合计", "流动负债"], "direct"),
        ("Total Equity", ["所有者权益合计", "股东权益合计"], "direct"),
        ("Retained Earnings", ["未分配利润"], "direct"),
    ],
    "income_statement": [
        # 营业总收入 is total operating revenue; 营业收入 excludes 利息收入 and
        # 手续费及佣金收入, which banks report separately. Prefer the narrower
        # one when present, and say so in the header.
        ("Revenue", ["营业收入", "营业总收入"], "narrow"),
        ("Operating Income", ["营业利润"], "direct"),
        ("Net Income", ["净利润"], "direct"),
        ("Net Income Attributable to Parent",
         ["归属于母公司所有者的净利润", "归母净利润"], "direct"),
        ("Total Profit", ["利润总额"], "direct"),
    ],
    "cashflow": [
        ("Operating Cash Flow", ["经营活动产生的现金流量净额", "经营活动现金流量净额"], "direct"),
        ("Investing Cash Flow", ["投资活动产生的现金流量净额", "投资活动现金流量净额"], "direct"),
        ("Financing Cash Flow", ["筹资活动产生的现金流量净额", "筹资活动现金流量净额"], "direct"),
        ("Cash and Equivalents at End of Period",
         ["期末现金及现金等价物余额"], "direct"),
    ],
}


def _statement(statement: str, symbol: str) -> pd.DataFrame:
    """Fetch one statement for a Chinese ticker, or raise a typed absence."""
    import akshare as ak

    cn = resolve_cn_symbol_or_skip(symbol)
    name = _SINA_STATEMENTS.get(statement)
    if name is None:
        raise ValueError(f"unknown statement {statement!r}")

    # Sina wants a prefixed symbol (sh600519 / sz000001); HK is not served here.
    if not cn.is_mainland:
        raise NoMarketDataError(
            symbol, cn.canonical,
            "Hong Kong statements are not served by this vendor yet",
        )

    try:
        frame = ak.stock_financial_report_sina(stock=cn.prefixed, symbol=name)
    except Exception as exc:
        raise NoMarketDataError(
            symbol, cn.canonical,
            f"AkShare statement request failed: {type(exc).__name__}: {exc}",
        ) from exc

    if frame is None or frame.empty:
        raise NoMarketDataError(
            symbol, cn.canonical, f"no {statement} rows returned",
        )
    return frame


def _render(frame: pd.DataFrame, lines: list[tuple[str, list[str], str]],
            symbol: str, title: str) -> str:
    """Format mapped statement lines for the Fundamentals Analyst.

    ``stock_financial_report_sina`` is period-major: one row per reporting
    period, one column per line item, with 报告日 naming the period. So the
    Chinese account names in the mapping are looked up among the *columns* and
    the report is written line-item by line-item, which is how the analyst (and
    the SEC EDGAR vendor's output) expects to read it.
    """
    if "报告日" not in frame.columns or frame.empty:
        return ""

    out = [f"# {title} for {symbol} (中国企业会计准则 / China Accounting Standards)", ""]
    notes: list[str] = []
    for english, candidates, note in lines:
        matched = next((c for c in candidates if c in frame.columns), None)
        out.append(f"## {english}")
        if matched is None:
            out.append("  N/A: not reported under this name by the vendor")
            out.append("")
            continue
        for _, row in frame.iterrows():
            value = row[matched]
            out.append(f"  {row['报告日']}: {'N/A' if pd.isna(value) else value}")
        if note == "narrow" and matched == "营业收入":
            notes.append(
                "  (Revenue here is 营业收入. Where a financial institution "
                "reports only 营业总收入, that broader figure is used instead; "
                "the two differ because banks report interest and fee income "
                "separately.)"
            )
        out.append("")

    if notes:
        out.append("# Notes")
        out.extend(notes)
    return "\n".join(out)


def get_cn_balance_sheet(symbol: str) -> str:
    cn = resolve_cn_symbol_or_skip(symbol)
    return _render(_statement("balance_sheet", symbol),
                   STATEMENTS["balance_sheet"], cn.canonical, "Balance Sheet")


def get_cn_income_statement(symbol: str) -> str:
    cn = resolve_cn_symbol_or_skip(symbol)
    return _render(_statement("income_statement", symbol),
                   STATEMENTS["income_statement"], cn.canonical, "Income Statement")


def get_cn_cashflow(symbol: str) -> str:
    cn = resolve_cn_symbol_or_skip(symbol)
    return _render(_statement("cashflow", symbol),
                   STATEMENTS["cashflow"], cn.canonical, "Cash Flow Statement")


def get_cn_fundamentals(symbol: str) -> str:
    """Headline metrics: EPS, ROE and margins, as the analyst expects them."""
    import akshare as ak

    cn = resolve_cn_symbol_or_skip(symbol)
    try:
        frame = ak.stock_financial_abstract(symbol=cn.code)
    except Exception as exc:
        raise NoMarketDataError(
            symbol, cn.canonical,
            f"AkShare abstract request failed: {type(exc).__name__}: {exc}",
        ) from exc

    if frame is None or frame.empty:
        raise NoMarketDataError(symbol, cn.canonical, "no abstract metrics returned")

    # stock_financial_abstract is indicator-major: 指标 names the metric and the
    # remaining columns are periods.
    label_col = "指标" if "指标" in frame.columns else frame.columns[1]
    periods = [c for c in frame.columns if c not in ("选项", label_col)]
    wanted = {
        "基本每股收益": "Basic EPS",
        "稀释每股收益": "Diluted EPS",
        "净资产收益率(ROE)": "ROE",
        "净资产收益率": "ROE",
        "销售毛利率": "Gross Margin",
        "销售净利率": "Net Margin",
    }
    by_label = {str(v): v for v in frame[label_col].tolist()}

    out = [f"# Key metrics for {cn.canonical} (中国企业会计准则)", ""]
    matched = 0
    for chinese, english in wanted.items():
        if chinese not in by_label:
            continue
        row = frame[frame[label_col] == by_label[chinese]]
        if row.empty:
            continue
        matched += 1
        out.append(f"## {english} ({chinese})")
        for period in periods:
            value = row.iloc[0][period]
            out.append(f"  {period}: {'N/A' if pd.isna(value) else value}")
        out.append("")

    if not matched:
        raise NoMarketDataError(
            symbol, cn.canonical,
            "abstract returned none of the expected metrics "
            f"(looked for {', '.join(sorted(set(wanted)))})",
        )
    return "\n".join(out)
