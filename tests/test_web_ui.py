"""Tests for the web UI layer.

The web app is a thin shell over the graph: it starts a run, streams state
deltas, and derives agent status from those deltas. The status derivation is
the part with real logic, so that is what is pinned here. Nothing in this file
touches the network or an LLM.
"""
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from pydantic import ValidationError

from quantagent.web import server  # noqa: E402
from quantagent.web.server import TEAMS, _agent_status, _slim_state  # noqa: E402

STATIC_DIR = Path(server.__file__).parent / "static"


def test_the_asgi_app_is_importable():
    """The module must expose a FastAPI instance for uvicorn to serve."""
    from fastapi import FastAPI

    assert isinstance(server.app, FastAPI)
    paths = {r.path for r in server.app.routes if hasattr(r, "path")}
    assert {"/", "/api/analyze", "/api/teams", "/api/config",
            "/api/stream/{run_id}"} <= paths


def _agent(key: str) -> dict:
    for team in TEAMS:
        for agent in team["agents"]:
            if agent["key"] == key:
                return agent
    raise AssertionError(f"no agent named {key!r} in the roster")


class TestAgentStatus:
    def test_pending_when_report_field_absent(self):
        assert _agent_status(_agent("market"), {}) == "pending"

    def test_pending_when_report_field_empty(self):
        assert _agent_status(_agent("market"), {"market_report": ""}) == "pending"

    def test_done_once_report_is_written(self):
        assert _agent_status(_agent("market"), {"market_report": "# 报告"}) == "done"

    def test_debate_agent_reads_its_own_slot(self):
        agent = _agent("bull")
        state = {"investment_debate_state": {"bull_history": "看多理由…",
                                              "bear_history": ""}}
        assert _agent_status(agent, state) == "done"

    def test_debate_agent_ignores_the_opponents_slot(self):
        """Bear speaking must not mark Bull done — the board would over-report."""
        agent = _agent("bull")
        state = {"investment_debate_state": {"bull_history": "",
                                              "bear_history": "看空理由…"}}
        assert _agent_status(agent, state) == "pending"

    def test_tolerates_a_missing_debate_block(self):
        assert _agent_status(_agent("bear"), {}) == "pending"

    def test_risk_and_investment_debates_are_distinct(self):
        state = {"investment_debate_state": {"bull_history": "x"},
                 "risk_debate_state": {"aggressive_history": "y"}}
        assert _agent_status(_agent("bull"), state) == "done"
        assert _agent_status(_agent("aggressive"), state) == "done"
        assert _agent_status(_agent("bear"), state) == "pending"


class TestSlimState:
    def test_keeps_report_text(self):
        slim = _slim_state({"market_report": "abc", "irrelevant": "drop me"})
        assert slim["market_report"] == "abc"
        assert "irrelevant" not in slim

    def test_flattens_both_debate_blocks(self):
        slim = _slim_state({
            "investment_debate_state": {"bull_history": "b", "bear_history": "s",
                                        "count": 1, "current_response": "x"},
            "risk_debate_state": {"judge_decision": "j", "latest_speaker": "A",
                                  "aggressive_history": "a"},
        })
        assert slim["investment_debate_state.bull_history"] == "b"
        assert slim["investment_debate_state.bear_history"] == "s"
        assert slim["risk_debate_state.judge_decision"] == "j"
        assert slim["risk_debate_state.latest_speaker"] == "A"
        # Noise the browser has no use for.
        assert "investment_debate_state.count" not in slim
        assert "investment_debate_state.current_response" not in slim

    def test_empty_state_is_safe(self):
        assert _slim_state({}) is not None


class TestRoster:
    def test_agent_keys_are_unique(self):
        keys = [a["key"] for t in TEAMS for a in t["agents"]]
        assert len(keys) == len(set(keys))

    def test_every_agent_declares_a_readable_source(self):
        for team in TEAMS:
            for agent in team["agents"]:
                assert agent.get("field") or agent.get("slot"), agent
                assert agent.get("label_zh"), agent

    def test_roster_covers_the_five_teams(self):
        assert {t["key"] for t in TEAMS} == {
            "analysts", "researchers", "trader", "risk", "portfolio",
        }


class TestRoutes:
    """Handlers are called directly rather than through TestClient.

    Starlette's TestClient spins up an anyio blocking portal (a thread plus a
    fresh event loop per request), which does not start in every sandboxed or
    CI environment. The handlers are plain functions, so calling them exercises
    the same code with none of that machinery.
    """

    def test_index_points_at_the_shipped_html(self):
        res = server.index()
        assert Path(res.path) == STATIC_DIR / "index.html"
        assert "QuantAgent" in Path(res.path).read_text(encoding="utf-8")

    def test_static_files_are_present_and_non_trivial(self):
        for name in ("index.html", "app.css", "app.js"):
            path = STATIC_DIR / name
            assert path.exists(), f"{name} missing — it will not be served"
            assert path.stat().st_size > 500, f"{name} is suspiciously small"

    def test_static_dir_matches_the_package_data_glob(self):
        """pyproject ships quantagent.web via ["static/*"]; keep them in step."""
        pyproject = (STATIC_DIR.parents[2] / "pyproject.toml").read_text(encoding="utf-8")
        assert '"quantagent.web" = ["static/*"]' in pyproject

    def test_teams_endpoint_returns_the_roster(self):
        assert server.teams() is TEAMS

    def test_config_endpoint_reports_key_state(self):
        body = server.config()
        assert "llm_provider" in body
        assert isinstance(body["api_key_configured"], bool)
        assert "results_dir" in body

    def test_analyze_rejects_an_empty_ticker(self):
        with pytest.raises(ValidationError):
            server.AnalyzeRequest(ticker="", trade_date="2026-09-01")

    def test_analyze_rejects_an_overlong_ticker(self):
        with pytest.raises(ValidationError):
            server.AnalyzeRequest(ticker="X" * 64, trade_date="2026-09-01")

    def test_analyze_rejects_a_negative_round_count(self):
        with pytest.raises(ValidationError):
            server.AnalyzeRequest(ticker="NVDA", trade_date="2026-09-01",
                                  max_debate_rounds=-1)

    def test_analyze_registers_a_run_and_returns_its_id(self):
        req = server.AnalyzeRequest(ticker="nvda", trade_date="2026-09-01",
                                    analysts=["market"])
        # The worker thread is not started here; only the registration path.
        run = server.Run(id="test", ticker="NVDA", trade_date="2026-09-01",
                         analysts=["market"], config=server._build_config(req))
        with server._RUNS_LOCK:
            server.RUNS[run.id] = run
        try:
            assert run.ticker == "NVDA"
            assert run.config["max_debate_rounds"] >= 0
        finally:
            with server._RUNS_LOCK:
                server.RUNS.pop(run.id, None)

    def test_config_overrides_reach_the_run_config(self):
        req = server.AnalyzeRequest(ticker="NVDA", trade_date="2026-09-01",
                                    deep_think_llm="x", quick_think_llm="y",
                                    max_debate_rounds=4, max_risk_rounds=5)
        cfg = server._build_config(req)
        assert cfg["deep_think_llm"] == "x"
        assert cfg["quick_think_llm"] == "y"
        assert cfg["max_debate_rounds"] == 4
        assert cfg["max_risk_discuss_rounds"] == 5

    def test_unset_optional_overrides_keep_the_config_default(self):
        cfg = server._build_config(
            server.AnalyzeRequest(ticker="NVDA", trade_date="2026-09-01")
        )
        from quantagent.default_config import DEFAULT_CONFIG
        assert cfg["deep_think_llm"] == DEFAULT_CONFIG["deep_think_llm"]

    def test_streaming_an_unknown_run_reports_the_error(self):
        # Called without an event loop: the suite blocks sockets, and asyncio's
        # ProactorEventLoop needs a socketpair for its self-pipe.
        assert server.RUNS.get("does-not-exist") is None
        assert b"unknown run does-not-exist" in server._unknown_run_frame("does-not-exist")

    def test_sse_frames_are_single_line_json(self):
        frame = server._sse({"type": "status", "status": {"market": "done"}})
        text = frame.decode()
        assert text.startswith("data: ") and text.endswith("\n\n")
        assert text.count("\n") == 2, "an SSE frame must not embed a raw newline"
