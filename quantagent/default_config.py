import os

_QUANTAGENT_HOME = os.path.join(os.path.expanduser("~"), ".quantagent")

# Single source of truth for env-var → config-key overrides. To expose
# a new config key for environment-based override, add a row here — no
# entry-point script changes required. Coercion is driven by the type
# of the existing default, so users can keep writing plain strings in
# their .env file.
#
# These are the canonical names. The pre-rebrand TRADINGAGENTS_* spellings are
# still honoured — see _ENV_LEGACY_PREFIX below.
_ENV_OVERRIDES = {
    "QUANTAGENT_LLM_PROVIDER":         "llm_provider",
    "QUANTAGENT_DEEP_THINK_LLM":       "deep_think_llm",
    "QUANTAGENT_QUICK_THINK_LLM":      "quick_think_llm",
    "QUANTAGENT_LLM_BACKEND_URL":      "backend_url",
    "QUANTAGENT_OUTPUT_LANGUAGE":      "output_language",
    "QUANTAGENT_MAX_DEBATE_ROUNDS":    "max_debate_rounds",
    "QUANTAGENT_MAX_RISK_ROUNDS":      "max_risk_discuss_rounds",
    "QUANTAGENT_CHECKPOINT_ENABLED":   "checkpoint_enabled",
    "QUANTAGENT_BENCHMARK_TICKER":     "benchmark_ticker",
    "QUANTAGENT_TEMPERATURE":          "temperature",
    "QUANTAGENT_LLM_MAX_RETRIES":      "llm_max_retries",
    "QUANTAGENT_MAX_TOKENS":           "max_tokens",
    # Provider-specific reasoning/thinking knobs (None = each provider's own
    # default). Settable here for non-interactive runs; the CLI also offers an
    # interactive choice, which is skipped when the matching var is set.
    "QUANTAGENT_GOOGLE_THINKING_LEVEL":   "google_thinking_level",
    "QUANTAGENT_OPENAI_REASONING_EFFORT": "openai_reasoning_effort",
    "QUANTAGENT_ANTHROPIC_EFFORT":        "anthropic_effort",
}

# QuantAgent was upstream's TradingAgents. A .env written against the old
# prefix keeps working: the legacy name is read only when the canonical one
# is unset, so the new spelling always wins if both are present.
_ENV_LEGACY_PREFIX = "TRADINGAGENTS_"
_ENV_PREFIX = "QUANTAGENT_"


def _canonical_env_name(env_var: str) -> str:
    """Map a legacy TRADINGAGENTS_* name onto its QUANTAGENT_* equivalent."""
    if env_var.startswith(_ENV_LEGACY_PREFIX):
        return _ENV_PREFIX + env_var[len(_ENV_LEGACY_PREFIX):]
    return env_var


def _env_lookup(*env_vars):
    """Resolve an overlay var under either prefix.

    Returns ``(name_actually_set, value)``, or ``(None, None)`` when neither
    spelling holds a non-empty value. Callers report ``name`` in errors so a
    user who wrote the legacy spelling sees the name they actually wrote.
    """
    for name in env_vars:
        value = os.environ.get(name)
        if value:
            return name, value
    return None, None


def _env_any(*env_vars: str):
    """First non-empty value among ``env_vars``, canonical names first."""
    return _env_lookup(*env_vars)[1]



_BOOL_TRUE = ("true", "1", "yes", "on")
_BOOL_FALSE = ("false", "0", "no", "off")


def _coerce(value: str, reference):
    """Coerce env-var string to the type of the existing default value.

    Invalid values raise ``ValueError`` rather than silently falling back to a
    default — a misspelled boolean (e.g. ``treu``) or non-numeric int should fail
    loudly at startup, not quietly misconfigure an unattended run.
    """
    if isinstance(reference, bool):
        normalized = value.strip().lower()
        if normalized in _BOOL_TRUE:
            return True
        if normalized in _BOOL_FALSE:
            return False
        raise ValueError(
            f"expected a boolean ({'/'.join(_BOOL_TRUE + _BOOL_FALSE)}), got {value!r}"
        )
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(config: dict) -> dict:
    """Apply QUANTAGENT_* env vars to the config dict in-place.

    Also accepts the legacy TRADINGAGENTS_* spelling of each name, so a .env
    carried over from upstream TradingAgents keeps working. The canonical name
    wins when both are set.
    """
    for env_var, key in _ENV_OVERRIDES.items():
        legacy = env_var.replace(_ENV_PREFIX, _ENV_LEGACY_PREFIX, 1)
        # _env_lookup skips empty strings, so a blank var falls through to the
        # legacy name rather than blanking the default. `set_name` is the
        # spelling that actually carried a value, so the error names it.
        set_name, raw = _env_lookup(env_var, legacy)
        if raw is None or raw == "":
            continue
        try:
            config[key] = _coerce(raw, config.get(key))
        except ValueError as exc:
            raise ValueError(f"Invalid value for {set_name}: {exc}") from exc
    return config


DEFAULT_CONFIG = _apply_env_overrides({
    "results_dir": _env_any("QUANTAGENT_RESULTS_DIR", "TRADINGAGENTS_RESULTS_DIR")
    or os.path.join(_QUANTAGENT_HOME, "logs"),
    "data_cache_dir": _env_any("QUANTAGENT_CACHE_DIR", "TRADINGAGENTS_CACHE_DIR")
    or os.path.join(_QUANTAGENT_HOME, "cache"),
    "memory_log_path": _env_any("QUANTAGENT_MEMORY_LOG_PATH", "TRADINGAGENTS_MEMORY_LOG_PATH")
    or os.path.join(_QUANTAGENT_HOME, "memory", "trading_memory.md"),
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings
    "llm_provider": "openai",
    "deep_think_llm": "gpt-6-sol",
    "quick_think_llm": "gpt-6-luna",
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # The CLI overrides this per provider when the user picks one. Keeping a
    # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
    # being forwarded to Gemini, producing malformed request URLs).
    "backend_url": None,
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Sampling temperature, forwarded to every provider when set. None leaves
    # each provider at its own default. Lower values reduce run-to-run
    # variation on models that honor it; reasoning models largely ignore it
    # and no setting makes LLM output bit-identical across runs (see README).
    "temperature": None,
    # SDK retry budget forwarded to every provider chat client. None leaves each
    # provider/SDK at its own default (usually 2). Raise it to ride out bursty
    # 429 throttling on rate-limited deployments instead of aborting a run (#1091).
    "llm_max_retries": None,
    # Cap on output tokens forwarded to every provider chat client. None leaves
    # each provider at its own default. Set it to bound a model that emits
    # unbounded reasoning/output and hangs or trips a gateway idle timeout
    # (e.g. some deepseek-v4-flash deployments, #1204).
    "max_tokens": None,
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "English",
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    # News / data fetching parameters
    # Increase for longer lookback strategies or to broaden macro coverage;
    # decrease to reduce token usage in agent prompts.
    "news_article_limit": 20,             # max articles per ticker (ticker-news)
    "global_news_article_limit": 10,      # max articles for global/macro news
    "global_news_lookback_days": 7,       # macro news lookback window
    # Search queries used by get_global_news for macro headlines. Extend or
    # replace to broaden geographic / sector coverage.
    "global_news_queries": [
        "Federal Reserve interest rates inflation",
        "S&P 500 earnings GDP economic outlook",
        "geopolitical risk trade war sanctions",
        "ECB Bank of England BOJ central bank policy",
        "oil commodities supply chain energy",
    ],
    # Data vendor configuration
    # Category-level configuration (default for all tools in category).
    # The configured value is the exact vendor chain — requests are NOT silently
    # routed to vendors you didn't choose. For ordered fallback, list several,
    # e.g. "yfinance,alpha_vantage". "default" uses all available vendors.
    "data_vendors": {
        "core_stock_apis": "yfinance",       # Options: alpha_vantage, yfinance
        "technical_indicators": "yfinance",  # Options: alpha_vantage, yfinance
        "fundamental_data": "yfinance",      # Options: alpha_vantage, yfinance
        "news_data": "yfinance",             # Options: alpha_vantage, yfinance
        "macro_data": "fred",                # Options: fred (needs FRED_API_KEY)
        "prediction_markets": "polymarket",  # Options: polymarket (keyless)
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
    # Benchmark for alpha calculation in the reflection layer.
    # ``benchmark_ticker`` (when set) overrides the suffix map for all
    # tickers; leave it None to use ``benchmark_map`` for auto-detection
    # based on the ticker's exchange suffix. SPY remains the US default
    # so the reflection label keeps reading "Alpha vs SPY" for US tickers
    # while non-US tickers get their regional index automatically.
    # Trading days after the analysis date over which a decision's outcome is
    # measured, for reflection and for the backtest figures.
    "holding_period_days": 5,
    "benchmark_ticker": None,
    "benchmark_map": {
        ".NS":  "^NSEI",       # NSE India (Nifty 50)
        ".BO":  "^BSESN",      # BSE India (Sensex)
        ".T":   "^N225",       # Tokyo (Nikkei 225)
        ".HK":  "^HSI",        # Hong Kong (Hang Seng)
        ".L":   "^FTSE",       # London (FTSE 100)
        ".TO":  "^GSPTSE",     # Toronto (TSX Composite)
        ".AX":  "^AXJO",       # Australia (ASX 200)
        ".SS":  "000001.SS",   # Shanghai (SSE Composite)
        ".SZ":  "399001.SZ",   # Shenzhen (SZSE Component)
        ".SA":  "^BVSP",       # B3 Brazil (Ibovespa)
        "":     "SPY",         # default for US-listed tickers (no suffix)
    },
})
