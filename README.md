<div align="center">
  <h1>QuantAgent</h1>
  <p><b>A multi-agent LLM trading research framework, with A-share and Hong Kong market data.</b></p>
</div>

---

# QuantAgent

Four analyst agents gather market, news, fundamental and retail-sentiment
evidence in parallel. A bull and a bear debate it. A research manager rules on
the debate, a trader proposes an action, three risk agents stress-test it, and
a portfolio manager issues the final rating. Every agent's reasoning is written
to a decision log, and past decisions are settled against realised prices so the
framework can learn from its own track record.

It runs against US markets and against A-shares and Hong Kong. Chinese tickers
get their own price history, financial statements under 中国企业会计准则,
per-stock news, and retail sentiment from 东方财富股吧 — not two English-language
platforms that carry nothing about a 600519.

Reports are written in Chinese while the machine-read rating vocabulary stays
English, so the signal parser, the decision log and the backtest scorer all
agree on one spelling.

> **QuantAgent is a research tool, not financial advice.** Nothing it outputs is
> a recommendation to buy or sell anything. Trading performance depends on the
> backbone models, temperature, period, data quality, and other
> non-deterministic factors.

## Quick start

```bash
pip install -e ".[web,cn]"

export DEEPSEEK_API_KEY=sk-...
export QUANTAGENT_LLM_PROVIDER=deepseek
export QUANTAGENT_OUTPUT_LANGUAGE="Simplified Chinese"

quantagent            # terminal
quantagent-web        # http://127.0.0.1:8420
```

Then analyse `600519.SS`, `0700.HK` or `NVDA`. Full setup, including the
Windows CA-bundle workaround and the A-share coverage table, is below.

<div align="center">

🚀 [Framework](#quantagent-framework) | ⚡ [Installation & CLI](#installation-and-cli) | 🌐 [Web UI](#web-ui) | 📦 [Package Usage](#quantagent-package) | 🔀 [What this project adds](#what-this-project-adds) | 🤝 [Contributing](#contributing) | 📄 [Credits](#credits)

</div>
## QuantAgent Framework

QuantAgent is a multi-agent trading framework that mirrors the dynamics of real-world trading firms. By deploying specialized LLM-powered agents: from fundamental analysts, sentiment experts, and technical analysts, to trader, risk management team, the platform collaboratively evaluates market conditions and informs trading decisions. Moreover, these agents engage in dynamic discussions to pinpoint the optimal strategy.

<p align="center">
  <img src="assets/schema.png" style="width: 100%; height: auto;">
</p>

> QuantAgent is designed for research purposes. Trading performance may vary based on many factors, including the chosen backbone language models, model temperature, trading periods, the quality of data, and other non-deterministic factors. It is not intended as financial, investment, or trading advice.

Our framework decomposes complex trading tasks into specialized roles.

### Analyst Team
- Fundamentals Analyst: Evaluates company financials and performance metrics, identifying intrinsic values and potential red flags.
- Sentiment Analyst: Aggregates news and retail chatter into a single sentiment read to gauge short-term market mood. The platforms are the ones the market actually uses: StockTwits and Reddit for US tickers, 东方财富股吧 for Chinese ones.
- News Analyst: Monitors global news and macroeconomic indicators, interpreting the impact of events on market conditions.
- Technical Analyst: Utilizes technical indicators (like MACD and RSI) to detect trading patterns and forecast price movements.

<p align="center">
  <img src="assets/analyst.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

### Researcher Team
- Comprises both bullish and bearish researchers who critically assess the insights provided by the Analyst Team. Through structured debates, they balance potential gains against inherent risks.

<p align="center">
  <img src="assets/researcher.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

### Trader Agent
- Composes reports from the analysts and researchers to make informed trading decisions, determining the timing and magnitude of trades.

<p align="center">
  <img src="assets/trader.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

### Risk Management and Portfolio Manager
- Continuously evaluates portfolio risk by assessing market volatility, liquidity, and other risk factors. The risk management team evaluates and adjusts trading strategies, providing assessment reports to the Portfolio Manager for final decision.
- The Portfolio Manager approves/rejects the transaction proposal. If approved, the order will be sent to the simulated exchange and executed.

<p align="center">
  <img src="assets/risk.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

## Installation and CLI

### Installation

```bash
git clone https://github.com/QiuMoonlit/moon.git
cd QuantAgent
```

Create a virtual environment in any of your favorite environment managers:
```bash
conda create -n quantagent python=3.12
conda activate quantagent
```

Or with [uv](https://docs.astral.sh/uv/):
```bash
uv venv --python 3.12
source .venv/bin/activate
```

Or with plain `venv`:
```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
source .venv/bin/activate     # macOS / Linux
```

Install the package and its dependencies (`uv pip install .` with uv):
```bash
pip install .
```

For development, install editable with the test and lint extras:
```bash
pip install -e ".[dev]"
pytest        # 1188 tests
ruff check .
```

### Windows: the project folder must not contain non-ASCII characters

yfinance reaches Yahoo through curl_cffi, whose native layer decodes file paths
using the process ANSI code page. When the checkout lives in a folder whose name
has non-ASCII characters (a Chinese folder name, for instance), `certifi.where()`
comes back mangled and every market-data call fails before reaching the network:

```
curl_cffi.requests.exceptions.SSLError: curl: (77) error adding trust anchors
```

Nothing else is wrong — the bundle is present and valid, curl just cannot read
the path. Copy the bundle to an ASCII-only location and point curl at it:

```bash
python scripts\fix_ca_bundle.py
```

Then set the path it prints in your `.env`:

```
CURL_CA_BUNDLE=C:\Users\<you>\quantagent-cacert.pem
```

Re-run the script after recreating the virtualenv. Moving the checkout to an
ASCII path (e.g. `C:\src\QuantAgent`) avoids the problem entirely.

### Web UI

A dark terminal-style web interface ships with the project. It runs the same
multi-agent graph and streams progress over SSE, so you can watch each analyst
and debater light up as it finishes instead of sitting at a terminal.

```bash
pip install -e ".[web]"      # adds FastAPI + uvicorn
quantagent-web               # http://127.0.0.1:8420
```

Or drive uvicorn directly:

```bash
uvicorn quantagent.web.server:app --port 8420
```

**What it does**

- Left rail: ticker, analysis date, which analysts to run, debate round counts.
  Everything pre-filled from `.env` / `DEFAULT_CONFIG`.
- Centre: the five agent teams, each agent going `pending → running → done` as
  the graph advances, with the live-updating report panel below.
- Final decision card with the parsed rating, colour-coded
  (Buy/Overweight green, Hold amber, Underweight/Sell red).
- A second tab reads the decision log written to
  `~/.quantagent/memory/trading_memory.md`.

**How it works.** `quantagent/graph/propagation.py` already runs the graph with
`stream_mode="values"`, so every node completion yields the full state.
`quantagent/web/server.py` diffs that state to decide which agent just
finished, then pushes an SSE frame. No changes to the graph, the agents, or the
vendor layer were needed — the terminal TUI consumes the same stream.

Runs are serialised behind a single lock: the vendor router and the decision log
hold process-level state, so two concurrent runs would interleave. One analysis
at a time, which is also how the LLM cost works out.

**Not included:** no auth, no multi-user isolation, and it binds to loopback.
Put it behind a reverse proxy with auth before binding it to anything but
`127.0.0.1`.

### Docker

Alternatively, run with Docker:
```bash
cp .env.example .env  # add your API keys
docker compose run --rm quantagent
```

After updating the repository, rebuild the image with `docker compose build`.

For local models with Ollama:
```bash
docker compose --profile ollama run --rm quantagent-ollama
```

### Required APIs

QuantAgent supports multiple LLM providers. Set the API key for your chosen provider:

```bash
export OPENAI_API_KEY=...          # OpenAI (GPT)
export GOOGLE_API_KEY=...          # Google (Gemini)
export ANTHROPIC_API_KEY=...       # Anthropic (Claude)
export XAI_API_KEY=...             # xAI (Grok)
export DEEPSEEK_API_KEY=...        # DeepSeek
export DASHSCOPE_API_KEY=...       # Qwen — International (dashscope-intl.aliyuncs.com)
export DASHSCOPE_CN_API_KEY=...    # Qwen — China (dashscope.aliyuncs.com)
export ZHIPU_API_KEY=...           # GLM via Z.AI (international)
export ZHIPU_CN_API_KEY=...        # GLM via BigModel (China, open.bigmodel.cn)
export MINIMAX_API_KEY=...         # MiniMax — Global (api.minimax.io)
export MINIMAX_CN_API_KEY=...      # MiniMax — China (api.minimaxi.com)
export OPENROUTER_API_KEY=...      # OpenRouter
export MISTRAL_API_KEY=...         # Mistral
export MOONSHOT_API_KEY=...        # Kimi (Moonshot)
export GROQ_API_KEY=...            # Groq
export NVIDIA_API_KEY=...          # NVIDIA NIM
export FRED_API_KEY=...            # FRED macro data (free, optional)
export ALPHA_VANTAGE_API_KEY=...   # Alpha Vantage
export TYPESAFE_API_KEY=...        # Jev social-post screening (optional)
```

For Azure OpenAI, copy `.env.enterprise.example` to `.env.enterprise` and fill in your credentials.

For AWS Bedrock, install the extra with `pip install ".[bedrock]"`, set `llm_provider: "bedrock"`, configure AWS credentials (environment variables, `~/.aws/credentials`, or an IAM role) and `AWS_DEFAULT_REGION`, and use a Bedrock model ID, e.g. `us.anthropic.claude-opus-4-8-v1:0`.

For local models, configure Ollama with `llm_provider: "ollama"`. The default endpoint is `http://localhost:11434/v1`; set `OLLAMA_BASE_URL` to point at a remote `ollama-serve`. Pull models with `ollama pull <name>`, and pick "Custom model ID" in the CLI for any model not listed by default.

For any other OpenAI-compatible server (vLLM, LM Studio, llama.cpp, or a custom relay), use `llm_provider: "openai_compatible"` and set the endpoint via `backend_url` (or `QUANTAGENT_LLM_BACKEND_URL`), e.g. `http://localhost:8000/v1` for vLLM or `http://localhost:1234/v1` for LM Studio. The model is whatever your server serves. No key is needed for local servers; set `OPENAI_COMPATIBLE_API_KEY` when the endpoint requires one.

With `TYPESAFE_API_KEY` set, the Sentiment Analyst screens StockTwits and Reddit posts with TypeSafe's Jev before reading them. Posts that are not about the company are dropped, and each source opens with a count of the remaining posts by stance: bullish, bearish, neutral, or unclear. Without the key, posts pass through unscreened. `jev-latest` moves with new releases; set `TYPESAFE_DEFAULT_MODEL` to a versioned ID such as `jev-1.13.0` to hold it fixed across runs.

Alternatively, copy `.env.example` to `.env` and fill in your keys:
```bash
cp .env.example .env
```

### CLI Usage

Launch the interactive CLI:
```bash
quantagent          # installed command
python -m cli.main     # alternative: run directly from source
```
You will see a screen where you can select your desired tickers, analysis date, LLM provider, research depth, and more. Your previous run's answers come back as the defaults, so pressing Enter accepts them. The `QUANTAGENT_*` variables in `.env` still skip their step entirely — the legacy `TRADINGAGENTS_*` names work too.

### Markets and tickers

QuantAgent works with any market Yahoo Finance covers, using the exchange-suffixed ticker. Company identity and the alpha benchmark resolve automatically per market.

- US: `AAPL`, `SPY`
- Hong Kong: `0700.HK` · Tokyo: `7203.T` · London: `AZN.L`
- India: `RELIANCE.NS`, `.BO` · Canada: `.TO` · Australia: `.AX`
- China A-shares: Shanghai `.SS`, Shenzhen `.SZ` (e.g. `600519.SS` for Kweichow Moutai)
- Crypto: `BTC-USD`, `ETH-USD`

<p align="center">
  <img src="assets/cli/cli_init.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

An interface will appear showing results as they load, letting you track the agent's progress as it runs.

<p align="center">
  <img src="assets/cli/cli_news.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

<p align="center">
  <img src="assets/cli/cli_transaction.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

## What this project adds

QuantAgent is a derived work — see [NOTICE](NOTICE) for the Apache-2.0
derivation record and attribution. The multi-agent graph, the
analyst/researcher/risk debate, the vendor router, the decision log and the
backtest harness are the inherited framework. Everything below is what this
project adds on top.

**Naming and packaging**
- Distribution, package and CLI command: `quantagent` / `quantagent`.
- Public graph class: `QuantAgentGraph`.
- Settings prefix: `QUANTAGENT_*`. Every legacy `TRADINGAGENTS_*` name is still
  read, and the CLI's "skip this prompt" checks accept either, so an older
  `.env` keeps working unchanged. The new spelling wins when both are set.
- State directory: `~/.quantagent`.

**Planned**
- Chinese-language analyst reports and CLI output beyond what shipped in
  v0.6.0 — the narrative is already localized, the CLI chrome is not.

**A-share / Hong Kong market data (partial)**

`pip install -e ".[cn]"` and point the relevant categories at `akshare`:

```powershell
config["data_vendors"]["core_stock_apis"] = "akshare,yfinance"
config["data_vendors"]["technical_indicators"] = "akshare,yfinance"
config["data_vendors"]["fundamental_data"] = "akshare,yfinance"
config["data_vendors"]["news_data"] = "akshare,yfinance"
config["data_vendors"]["sentiment_data"] = "akshare,yfinance"
```

Wired: daily OHLCV, stockstats indicators, balance sheet / income statement /
cash flow under 中国企业会计准则, headline metrics, the settlement price
series, per-stock news, retail sentiment from 东方财富股吧, and the market
analyst's verified price snapshot. Tickers use the normal spellings —
`600519.SS`, `000001.SZ`, `0700.HK`. A US ticker falls out of the AkShare
vendor with `NoMarketDataError` and continues down the chain, so the two can be
listed in one config, which is the default.

**Not wired yet** — these still resolve to the US vendors, and an A-share run
will degrade rather than fail:

| Gap | Effect on a Chinese ticker |
|---|---|
| Insider transactions | Form 4 has no A-share equivalent; 董监高持股变动 is a different disclosure with a different cadence. Not implemented. |
| Macro indicators | The News Analyst's macro tool is FRED, which is US-only. Chinese macro (PMI, 社融, LPR) is not wired. Global *news* now falls back to a Baidu economic digest. |
| Hong Kong statements | Price, indicators, news and sentiment work for `.HK`; fundamentals are not served by the underlying vendor and say so. |
| Trading calendar | `MAX_OHLCV_STALE_DAYS` was raised to 20 so Chinese holidays do not read as stale, but there is still no real exchange calendar — `date_window.py` is plain calendar arithmetic and `settlement.py`'s holding-window estimate is tuned for Western holidays. |

### Sentiment, for what it is

The Chinese source is 东方财富股吧 (Eastmoney's per-stock forum), which is the
institutional equivalent of the StockTwits/Reddit pair. It is **not** a message
stream — there are no per-stock posts to read — so what it offers is a set of
indices:

| Signal | What it means |
|---|---|
| 用户关注指数 (attention index) | How much the retail forum is following, 30 trading days. Attention, not direction. |
| 综合得分 / 机构参与度 / 主力成本 | A per-stock scorecard. 主力成本 is the crowd's cost basis, which is **not** a price target — the output says so, because it reads like one. |
| 人气排名 + 新晋粉丝/铁杆粉丝 | The popularity ranking over time, split into fans who chase and fans who hold. A rise in 新晋粉丝 with no price response is retail flow, not conviction. |

雪球 (Xueqiu) is deliberately not used: AkShare's Xueqiu endpoints are
hot-topic and holdings screens, not a per-stock message stream —
`stock_hot_tweet_xq(symbol="SH600519")` raises `KeyError` because it only
serves a global trending list. Claiming a Xueqiu feed would be fiction.

A partial outage keeps what worked and lists what did not; a total one raises
rather than handing the analyst an empty block.

### Two things that look like gaps and are not

*Company profile.* `agents/context.py` still reads the instrument identity
(name, sector, industry, exchange) from the Yahoo vendor, and it works for
Chinese tickers — `600519.SS` resolves to Kweichow Moutai / Consumer Defensive,
`0700.HK` to Tencent Holdings. Verified, not assumed.

*Ticker spelling.* Pass the exchange suffix: `600519.SS`, `000001.SZ`,
`0700.HK`. A bare `600519` reaches the AkShare vendor correctly, but the
identity lookup asks Yahoo, which needs the suffix and returns nothing without
it.

**Added in v0.6.0**
- A web UI (`quantagent.web`) with SSE progress streaming — see
  [Web UI](#web-ui).
- `CURL_CA_BUNDLE` fix for checkouts under a non-ASCII path — see
  [Windows](#windows-the-project-folder-must-not-contain-non-ascii-characters).

## QuantAgent Package

### Implementation Details

We built QuantAgent with LangGraph to ensure flexibility and modularity. The framework supports multiple LLM providers: OpenAI, Google, Anthropic, xAI, DeepSeek, Qwen (Alibaba DashScope, international and China endpoints), GLM (Zhipu), MiniMax (global + China), OpenRouter, Ollama for local models, and Azure OpenAI for enterprise.

### Python Usage

To use QuantAgent inside your code, you can import the `quantagent` module and initialize a `QuantAgentGraph()` object. The `.propagate()` function will return a decision. You can run `main.py`, here's also a quick example:

```python
from quantagent.graph.trading_graph import QuantAgentGraph
from quantagent.default_config import DEFAULT_CONFIG

ta = QuantAgentGraph(debug=True, config=DEFAULT_CONFIG.copy())

# forward propagate
_, decision = ta.propagate("NVDA", "2026-09-01")
print(decision)
```

You can also adjust the default configuration to set your own choice of LLMs, debate rounds, etc.

```python
from quantagent.graph.trading_graph import QuantAgentGraph
from quantagent.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"        # e.g. openai, google, anthropic, deepseek, groq, ollama; openai_compatible covers any OpenAI-compatible endpoint (vLLM, LM Studio, llama.cpp, ...)
config["deep_think_llm"] = "gpt-6-sol"    # Model for complex reasoning
config["quick_think_llm"] = "gpt-6-luna"   # Model for quick tasks
config["max_debate_rounds"] = 2

ta = QuantAgentGraph(debug=True, config=config)
_, decision = ta.propagate("NVDA", "2026-09-01")
print(decision)
```

See `quantagent/default_config.py` for all configuration options.

### Fundamentals as filed

US company statements can come from SEC EDGAR, which records the date every figure was filed. A run dated in the past then reads the statements exactly as they stood that day: a fiscal year that has ended but has not been filed yet is not served, and a figure restated later still reads as first reported. Apple's 2008 total assets were filed as $39.6B and restated to $36.2B in 2010, so a run dated in between reads $39.6B.

EDGAR needs no account or API key. Add the vendor to the chain:

```python
config["data_vendors"]["fundamental_data"] = "sec_edgar,yfinance"
```

SEC asks callers to identify themselves and refuses requests that carry no contact address, so a default one is sent. Set your own so SEC can reach you rather than the project:

```bash
SEC_EDGAR_USER_AGENT="Your Name your@email.com"
```

It covers companies that file with the SEC, including foreign companies listed in the US. Anything else, such as Hong Kong or A-share listings, falls through to the next vendor in the chain. EDGAR's machine-readable filings begin in 2009, and a fourth quarter is reported as unavailable rather than derived, because filers publish it only inside the annual figure.

### Current holdings

By default the agents do not know what you hold, so their guidance is written for a reader who applies it to their own position. Pass a portfolio to have the trader, the risk analysts and the portfolio manager work against your actual book.

```python
from quantagent.portfolio import PortfolioContext

portfolio = PortfolioContext.model_validate({
    "cash": 25000.0,
    "currency": "USD",
    "positions": [{"ticker": "NVDA", "quantity": 120, "average_price": 150.0}],
})
_, decision = ta.propagate("NVDA", "2026-09-01", portfolio=portfolio)
```

The CLI takes the same content as a JSON file: `quantagent --portfolio my_book.json`.

An empty `positions` list means a flat book, which is different from passing nothing. A run without a portfolio is never treated as flat.

## Persistence and Recovery

QuantAgent persists two kinds of state across runs.

### Decision log

The decision log is always on. Each completed run appends its decision to `~/.quantagent/memory/trading_memory.md`. On the next run for the same ticker, QuantAgent fetches the realised return (raw, and alpha against the instrument's regional benchmark), generates a one-paragraph reflection, and injects the most recent same-ticker decisions plus recent cross-ticker lessons into the Portfolio Manager prompt, so each analysis carries forward what worked and what didn't.

Override the path with `QUANTAGENT_MEMORY_LOG_PATH` (or the legacy `TRADINGAGENTS_MEMORY_LOG_PATH`).

### Checkpoint resume

Checkpoint resume is opt-in via `--checkpoint`. When enabled, LangGraph saves state after each node so a crashed or interrupted run resumes from the last successful step instead of starting over. The run view says whether it resumed a saved run or started fresh. Checkpoints are cleared automatically on successful completion.

Per-ticker SQLite databases live at `~/.quantagent/cache/checkpoints/<TICKER>.db` (override the base with `QUANTAGENT_CACHE_DIR`). Use `--clear-checkpoints` to reset all of them before a run.

```bash
quantagent --checkpoint           # enable for this run
quantagent --clear-checkpoints    # reset before running
```

```python
config = DEFAULT_CONFIG.copy()
config["checkpoint_enabled"] = True
ta = QuantAgentGraph(config=config)
_, decision = ta.propagate("NVDA", "2026-09-01")
```

## Evaluating decisions over time

One run gives one decision, which cannot tell you whether the system decides well. `run_backtest` runs the same pipeline over a grid of tickers and dates, writes to a decision log of its own, and scores the decisions whose holding window has since traded.

```python
from quantagent.backtest import iter_grid, run_backtest, summarize

dates = iter_grid("2026-06-01", "2026-08-01", every_n_days=7)
result = run_backtest(["NVDA", "AAPL"], dates, config, selected_analysts=["market", "news"])
print(summarize(result).render())
```

From the CLI:

```bash
quantagent backtest NVDA,AAPL --start 2026-06-01 --end 2026-08-01 --every 7
```

Each cell is scored on realized alpha against the instrument's regional benchmark, grouped by rating. Your own decision log is never written to, and re-running the same grid with `run_id=result.run_id` skips the cells that already ran, so an interrupted sweep continues where it stopped.

## Reproducibility

QuantAgent is LLM-driven, so two runs of the same ticker and date can differ. This is expected for a research tool built on language models, not a defect. The variation comes from a few distinct sources, and it helps to separate them.

Language model sampling is non-deterministic. Even at a fixed temperature, providers do not guarantee byte-identical output across calls, and reasoning models (the default GPT-6 family, and any thinking-mode model) vary the most because their internal reasoning is itself sampled.

Live data moves. News, StockTwits, and Reddit return different content as time passes, so a run today sees different inputs than a run last week even for the same historical trade date. Pin the analysis date to hold the price and indicator window fixed, but the social and news sources still reflect "now".

To reduce variation you can lower the sampling temperature. Set `temperature` in your config (or `QUANTAGENT_TEMPERATURE` in `.env`); lower values make models that honor it more repeatable. The current curated models are reasoning-first and largely ignore temperature, so for tighter reproducibility name a non-reasoning model in your config, or in `QUANTAGENT_DEEP_THINK_LLM` and `QUANTAGENT_QUICK_THINK_LLM`. Any model ID your provider serves is accepted, whether or not the picker lists it.

```python
config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"
config["temperature"] = 0.0
# Reasoning models ignore temperature. For tighter reproducibility, name a
# non-reasoning model in deep_think_llm / quick_think_llm.
```

What does not vary anymore: the analyzed company identity is resolved deterministically from the ticker before any agent runs, and the market analyst grounds exact price and indicator claims in a verified data snapshot. Earlier reports of "different companies" or fabricated price levels across runs are addressed by these two mechanisms.

Backtest results are not guaranteed to match any published figure. Returns depend on the model, the temperature, the date range, data quality, and the sampling above. Treat the framework as a research scaffold for studying multi-agent analysis, not as a strategy with a fixed, replicable return.

## Contributing

Bug fixes, documentation and feature ideas are welcome. Contributions are
credited per release in [`CHANGELOG.md`](CHANGELOG.md).

## Credits

QuantAgent is built on an existing open-source framework, and the paper that
introduced the multi-agent trading design is the reference for the architecture
here. If your work uses it, please cite the original:

```
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework},
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138},
}
```

The A-share and Hong Kong market data layer, the web UI, the cancellation and
observability work, and the Chinese-language output are this project's own
contributions to that derived work. See [NOTICE](NOTICE) and
[LICENSE](LICENSE) for the full derivation record.
