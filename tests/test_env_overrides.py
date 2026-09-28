"""Tests for the QUANTAGENT_* env-var overlay onto DEFAULT_CONFIG.

The legacy TRADINGAGENTS_* spellings stay supported, so most cases here
exercise the back-compat path; the canonical-prefix cases are pinned below.
"""

from __future__ import annotations

import importlib
import os

import pytest

import quantagent.default_config as default_config_module


def _clear_all_prefixes(monkeypatch):
    """Drop every overlay var under both prefixes.

    ``_ENV_OVERRIDES`` only holds the canonical names now, so clearing just its
    keys would leave a TRADINGAGENTS_* var set by an earlier test in place —
    and the overlay would still honour it.
    """
    for key in list(default_config_module._ENV_OVERRIDES):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(
            key.replace(
                default_config_module._ENV_PREFIX,
                default_config_module._ENV_LEGACY_PREFIX,
            ),
            raising=False,
        )


def _reload_with_env(monkeypatch, **overrides):
    """Set/clear env vars then reload default_config to re-evaluate DEFAULT_CONFIG."""
    _clear_all_prefixes(monkeypatch)
    for key, val in overrides.items():
        monkeypatch.setenv(key, val)
    return importlib.reload(default_config_module)


def test_no_env_uses_built_in_defaults(monkeypatch):
    dc = _reload_with_env(monkeypatch)
    assert dc.DEFAULT_CONFIG["llm_provider"] == "openai"
    assert dc.DEFAULT_CONFIG["deep_think_llm"] == "gpt-6-sol"
    assert dc.DEFAULT_CONFIG["quick_think_llm"] == "gpt-6-luna"
    assert dc.DEFAULT_CONFIG["backend_url"] is None
    assert dc.DEFAULT_CONFIG["max_debate_rounds"] == 1
    assert dc.DEFAULT_CONFIG["checkpoint_enabled"] is False


def test_string_overrides(monkeypatch):
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_LLM_PROVIDER="google",
        TRADINGAGENTS_DEEP_THINK_LLM="gemini-3-pro-preview",
        TRADINGAGENTS_QUICK_THINK_LLM="gemini-3-flash-preview",
        TRADINGAGENTS_LLM_BACKEND_URL="https://example.invalid/v1",
        TRADINGAGENTS_OUTPUT_LANGUAGE="Chinese",
    )
    assert dc.DEFAULT_CONFIG["llm_provider"] == "google"
    assert dc.DEFAULT_CONFIG["deep_think_llm"] == "gemini-3-pro-preview"
    assert dc.DEFAULT_CONFIG["quick_think_llm"] == "gemini-3-flash-preview"
    assert dc.DEFAULT_CONFIG["backend_url"] == "https://example.invalid/v1"
    assert dc.DEFAULT_CONFIG["output_language"] == "Chinese"


def test_int_coercion(monkeypatch):
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_MAX_DEBATE_ROUNDS="3",
        TRADINGAGENTS_MAX_RISK_ROUNDS="2",
    )
    assert dc.DEFAULT_CONFIG["max_debate_rounds"] == 3
    assert isinstance(dc.DEFAULT_CONFIG["max_debate_rounds"], int)
    assert dc.DEFAULT_CONFIG["max_risk_discuss_rounds"] == 2
    assert isinstance(dc.DEFAULT_CONFIG["max_risk_discuss_rounds"], int)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
        ("false", False), ("False", False), ("0", False), ("no", False), ("off", False),
    ],
)
def test_bool_coercion(monkeypatch, raw, expected):
    dc = _reload_with_env(monkeypatch, TRADINGAGENTS_CHECKPOINT_ENABLED=raw)
    assert dc.DEFAULT_CONFIG["checkpoint_enabled"] is expected


def test_reasoning_thinking_overrides(monkeypatch):
    """The provider reasoning/thinking knobs are env-configurable (non-interactive runs)."""
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_OPENAI_REASONING_EFFORT="high",
        TRADINGAGENTS_GOOGLE_THINKING_LEVEL="minimal",
        TRADINGAGENTS_ANTHROPIC_EFFORT="low",
    )
    assert dc.DEFAULT_CONFIG["openai_reasoning_effort"] == "high"
    assert dc.DEFAULT_CONFIG["google_thinking_level"] == "minimal"
    assert dc.DEFAULT_CONFIG["anthropic_effort"] == "low"


def test_reasoning_effort_defaults_to_none(monkeypatch):
    """Unset reasoning/thinking knobs stay None so each provider uses its own default."""
    dc = _reload_with_env(monkeypatch)
    assert dc.DEFAULT_CONFIG["openai_reasoning_effort"] is None
    assert dc.DEFAULT_CONFIG["google_thinking_level"] is None
    assert dc.DEFAULT_CONFIG["anthropic_effort"] is None


def test_empty_env_value_is_passthrough(monkeypatch):
    """Empty TRADINGAGENTS_* values must not clobber the built-in default."""
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_LLM_PROVIDER="",
        TRADINGAGENTS_MAX_DEBATE_ROUNDS="",
    )
    assert dc.DEFAULT_CONFIG["llm_provider"] == "openai"
    assert dc.DEFAULT_CONFIG["max_debate_rounds"] == 1


def test_empty_path_value_keeps_the_default_path(monkeypatch):
    """.env.example lists the path variables blank; uncommenting one made the
    path empty, and the graph failed creating its directories."""
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_RESULTS_DIR="",
        TRADINGAGENTS_CACHE_DIR="",
        TRADINGAGENTS_MEMORY_LOG_PATH="",
    )
    home = dc._QUANTAGENT_HOME
    assert dc.DEFAULT_CONFIG["results_dir"] == os.path.join(home, "logs")
    assert dc.DEFAULT_CONFIG["data_cache_dir"] == os.path.join(home, "cache")
    assert dc.DEFAULT_CONFIG["memory_log_path"] == os.path.join(home, "memory", "trading_memory.md")


def test_invalid_int_raises(monkeypatch):
    """Garbage int values should surface a ValueError at import, not silently misconfigure."""
    monkeypatch.setenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "not-a-number")
    with pytest.raises(ValueError, match="TRADINGAGENTS_MAX_DEBATE_ROUNDS"):
        importlib.reload(default_config_module)
    # Restore module state for subsequent tests in this process
    monkeypatch.delenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", raising=False)
    importlib.reload(default_config_module)


@pytest.mark.parametrize("bad", ["treu", "flase", "maybe", "2", "enabled"])
def test_invalid_bool_raises(monkeypatch, bad):
    """A misspelled boolean must fail loudly (like ints) instead of silently False."""
    monkeypatch.setenv("TRADINGAGENTS_CHECKPOINT_ENABLED", bad)
    with pytest.raises(ValueError, match="TRADINGAGENTS_CHECKPOINT_ENABLED"):
        importlib.reload(default_config_module)
    monkeypatch.delenv("TRADINGAGENTS_CHECKPOINT_ENABLED", raising=False)
    importlib.reload(default_config_module)


def test_unknown_env_var_is_ignored(monkeypatch):
    """Env vars outside _ENV_OVERRIDES must not bleed into DEFAULT_CONFIG."""
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_NONEXISTENT_KEY="oops",
    )
    assert "nonexistent_key" not in dc.DEFAULT_CONFIG


# --- prefix rename: QUANTAGENT_ is canonical, TRADINGAGENTS_ still honoured ---


def test_canonical_prefix_is_applied(monkeypatch):
    dc = _reload_with_env(
        monkeypatch,
        QUANTAGENT_LLM_PROVIDER="deepseek",
        QUANTAGENT_DEEP_THINK_LLM="deepseek-reasoner",
        QUANTAGENT_MAX_DEBATE_ROUNDS="3",
        QUANTAGENT_MAX_RISK_ROUNDS="2",
        QUANTAGENT_OUTPUT_LANGUAGE="Simplified Chinese",
        QUANTAGENT_TEMPERATURE="0.2",
    )
    assert dc.DEFAULT_CONFIG["llm_provider"] == "deepseek"
    assert dc.DEFAULT_CONFIG["deep_think_llm"] == "deepseek-reasoner"
    assert dc.DEFAULT_CONFIG["max_debate_rounds"] == 3
    assert dc.DEFAULT_CONFIG["max_risk_discuss_rounds"] == 2
    assert dc.DEFAULT_CONFIG["output_language"] == "Simplified Chinese"
    # temperature defaults to None, so _coerce has no float reference to cast
    # against and the raw string survives; the LLM factory does the float().
    assert dc.DEFAULT_CONFIG["temperature"] == "0.2"


def test_legacy_prefix_still_applies(monkeypatch):
    """A .env carried over from before the rebrand must keep working."""
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_LLM_PROVIDER="google",
        TRADINGAGENTS_MAX_DEBATE_ROUNDS="4",
    )
    assert dc.DEFAULT_CONFIG["llm_provider"] == "google"
    assert dc.DEFAULT_CONFIG["max_debate_rounds"] == 4


def test_canonical_prefix_wins_over_legacy(monkeypatch):
    """Both spellings set: the current name must take precedence."""
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_LLM_PROVIDER="google",
        QUANTAGENT_LLM_PROVIDER="deepseek",
        TRADINGAGENTS_MAX_DEBATE_ROUNDS="4",
        QUANTAGENT_MAX_DEBATE_ROUNDS="1",
    )
    assert dc.DEFAULT_CONFIG["llm_provider"] == "deepseek"
    assert dc.DEFAULT_CONFIG["max_debate_rounds"] == 1


def test_blank_canonical_falls_through_to_legacy(monkeypatch):
    """A blanked-out new name (as shipped in .env.example) still reads the old one."""
    dc = _reload_with_env(
        monkeypatch,
        QUANTAGENT_LLM_PROVIDER="",
        TRADINGAGENTS_LLM_PROVIDER="anthropic",
    )
    assert dc.DEFAULT_CONFIG["llm_provider"] == "anthropic"


def test_every_override_has_a_distinct_legacy_alias(monkeypatch):
    """No canonical name may collide with a legacy one after the prefix swap."""
    dc = _reload_with_env(monkeypatch)
    for name in dc._ENV_OVERRIDES:
        assert name.startswith(dc._ENV_PREFIX), name
        legacy = dc._canonical_env_name(name.replace(dc._ENV_PREFIX, dc._ENV_LEGACY_PREFIX))
        assert legacy == name
        assert legacy not in dc._ENV_OVERRIDES or legacy == name


def test_legacy_path_vars_still_resolve(monkeypatch):
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_RESULTS_DIR="/tmp/legacy-logs",
    )
    assert dc.DEFAULT_CONFIG["results_dir"] == "/tmp/legacy-logs"
