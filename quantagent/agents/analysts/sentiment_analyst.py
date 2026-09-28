"""Sentiment analyst: one sentiment report from news plus retail chatter.

The node fetches its sources before calling the model and puts them in the
prompt, so the model reports on data it was given rather than inventing posts:

  1. News headlines, market-specific
  2. Retail chatter, market-specific:
     - US: StockTwits (self-labelled Bullish/Bearish) and Reddit
       (r/wallstreetbets, r/stocks, r/investing)
     - China: 东方财富股吧 — the attention index, the retail scorecard and the
       popularity ranking. There is no per-stock Xueqiu message stream, so this
       is the honest Chinese equivalent of the two Western feeds.

The source pair is chosen by the configured ``sentiment_data`` chain rather
than by a hardcoded import, because a 600519 asked about r/wallstreetbets gets
nothing. Each source is trimmed to the analysis window where the vendor
archives it. These feeds serve recent items and are not archived, so a
historical run's sentiment inputs are not point-in-time.

The report is a SentimentReport through structured output where the provider
supports it and free text otherwise, so the band, score and confidence header
reads the same across providers.
"""

from datetime import datetime, timedelta

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from quantagent.agents.context import get_instrument_context_from_state, get_language_instruction
from quantagent.agents.schemas import SentimentReport, render_sentiment_report
from quantagent.agents.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_structured_or_freetext,
)
from quantagent.agents.tools import get_news
from quantagent.dataflows.router import route_to_vendor
from quantagent.dataflows.vendors.akshare.ohlcv import is_chinese_symbol


def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def create_sentiment_analyst(llm):
    """Create a sentiment analyst node for the trading graph.

    Pre-fetches news + StockTwits + Reddit data, injects them into the
    prompt as structured blocks, and produces a deterministic sentiment
    report via structured output (with a free-text fallback for providers
    that do not support it).
    """
    structured_llm = bind_structured(llm, SentimentReport, "Sentiment Analyst")

    def sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        end_date = state["trade_date"]
        start_date = _seven_days_back(end_date)
        instrument_context = get_instrument_context_from_state(state)

        # Pre-fetch both source groups. Each degrades to a string (no exception
        # surfaces), so the LLM always sees something — real data or a clear
        # placeholder.
        news_block = get_news.func(ticker, start_date, end_date)
        # Routed, so a Chinese ticker reads the 东方财富股吧 feeds instead
        # of two English-language platforms that carry nothing about it. The
        # analysis window is passed so a historical run trims to it where the
        # vendor archives (#1220).
        social_block = route_to_vendor("get_sentiment", ticker, start_date, end_date)

        system_message = _build_system_message(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            chinese=is_chinese_symbol(ticker),
            news_block=news_block,
            social_block=social_block,
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Report what your tools support; another agent decides the trade."
                    # No tool-calling here: the data is pre-fetched into the
                    # prompt, so tool-range wording would only invite a
                    # hallucinated tool call (#1130).
                    " Today's date is {current_date}; treat it as 'now' for all analysis. {instrument_context}"
                    " " + NO_EXTERNAL_TOOLS +
                    "\n{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=end_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        # Format the template into a concrete message list so the structured
        # and free-text paths receive the same input. No bind_tools — the
        # data is already in the prompt.
        formatted_messages = prompt.format_messages(messages=state["messages"])

        report_text = invoke_structured_or_freetext(
            structured_llm,
            llm,
            formatted_messages,
            render_sentiment_report,
            "Sentiment Analyst",
        )

        return {
            "messages": [AIMessage(content=report_text)],
            "sentiment_report": report_text,
        }

    return sentiment_analyst_node


def _build_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    chinese: bool,
    news_block: str,
    social_block: str,
) -> str:
    """Assemble the sentiment-analyst system message with structured data blocks.

    The source block differs by market because the platforms differ: there is no
    per-stock Xueqiu message stream, and a 600519 has nothing on StockTwits. The
    *method* is the same either way - read what the source can and cannot tell
    you - so the analysis guidance is shared and only the source description and
    the source-specific reading notes change.
    """
    if chinese:
        source_section = f"""### 东方财富股吧 — 中国零售情绪
三个指标，三者各测一件事：**关注度**（谈多少）、**情绪评分**（怎么看）、**人气排名**（在群里多稍）。

<start_of_social>
{social_block}
<end_of_social>

这些是**指标**而不是帖子本身。它们反映零售群体的行为，但不包含每个人自选的 Bullish/Bearish 标签——因此读时不要当作为立场方向，而要当作**热度和持仓意志**。"""
        news_source = "东方财富（Eastmoney）个股新闻，过去 7 天"
        reading_notes = """1. **先分清“关注”和“买入”。** 关注指数上升而价格持平，说明是在看不是在买。换手还需要股票换手率、主力成本与现价的偏离同时看。

2. **读人气排名的方向，不是绝对值。** 排名从 5 升到 20 是零售在回流，从 1 并到 5 是其他标的更强话题挤占了空间。绝对位置的变化比位置更有意义。

3. **区分新丰和铁杆。** 新丰追、铁杆持。**新丰占比突然上升而价格无反应，是零售流入信号，不是持仓信心**。卖方供应出现而价格不动，通常意味着接盘仓库在出。

4. **主力成本是成本的不安，不是目标价。** 现价高于主力成本，平均持仓者盈利；低于则平均持仓者消费，下行中结构性风险变大。**它不是目标价，不要当作价格目标写进报告。**

5. **机构参与度是结构性信息。** 零售情绪极热但机构参与度下行，说明流动性资金主导；情绪冷漂但机构参与度上升，可能是公募资金吸引。"""
    else:
        source_section = f"""### StockTwits and Reddit — US retail-chatter platforms
StockTwits messages carry a user-labeled sentiment tag (Bullish / Bearish / no-label) plus the message body. Reddit posts come from r/wallstreetbets, r/stocks and r/investing, without vote or comment counts.

<start_of_social>
{social_block}
<end_of_social>"""
        news_source = "Yahoo Finance, past 7 days"
        reading_notes = """1. **读 StockTwits 的 Bullish/Bearish 比作为零售预期信号。** 70/30 中性看多；≥90/10 可能是过热并有反向风险；50/50 是不确定。样本量很重要，以实际条数为准。标有“Screened by Jev”的区块已去掉偏题帖子；其立场计数是分类器对所有在题帖的读取，列出的仅是样本。

2. **不要从 Reddit 推断互动量。** 该流没有投票数和评论数，只能从正文摘要判断质量。

3. **区分偶见与事件。** 新闻标题是事件，一条 StockTwits 帖子是意见。两者都是输入，但权重不应相同。"""

    return f"""You are a financial market sentiment analyst. Your task is to produce a comprehensive sentiment report for {ticker} covering the period from {start_date} to {end_date}, drawing on complementary data sources that have already been collected for you.

## Data sources (pre-fetched, in this prompt)

### News headlines — {news_source}
Institutional framing. Fact-driven, slower-moving signal.

<start_of_news>
{news_block}
<end_of_news>

{source_section}

## How to analyze this data (best practices)

{reading_notes}

6. **寻找跨源分歧。** 如果新闻口径偏稀中而零售情绪极热（或反之），这个不一致本身就是信号——它可能表明零售在追一个机构尚未体现的主张，也可能表明机构比零售更慎慎。

7. **找出反复出现的叙事主线。** 哪个话题跨来源反复出现？那就是驱动当前情绪的主导叙事。

8. **对数据限制诚实。** 如果某个源只返回了少量内容，或出现了不可用占位符，情绪判断就不多孔富——请在 `confidence` 字段和正文里明确标出。

9. **识别交叉的催化剂与风险。** 优势、产品发布、竞争压力、宏观头条等。

10. **过去的情绪不具预测性。** 把结论表述为交给交易者与基本面、技术面共同权衡的信号，而不是价格预测。

## Output fields

Fill the following fields:

- **overall_band**: Exactly one of Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. Use Mixed when sources point in clearly different directions; Neutral only when all sources are genuinely silent.
- **overall_score**: A number from 0 (maximally bearish) to 10 (maximally bullish); 5 is neutral. Keep it consistent with overall_band.
- **confidence**: low / medium / high, based on data quality and sample size.
- **narrative**: Full source-by-source breakdown, divergences, dominant narrative themes, catalysts and risks, and a markdown summary table of key sentiment signals (direction, source, supporting evidence).

{get_language_instruction()}"""

