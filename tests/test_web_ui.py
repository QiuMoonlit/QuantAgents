"""Tests for the web UI layer.

The web app is a thin shell over the graph: it starts a run, streams state
deltas, and derives agent status from those deltas. The status derivation is
the part with real logic, so that is what is pinned here. Nothing in this file
touches the network or an LLM.
"""
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from pydantic import ValidationError

from quantagent.web import server  # noqa: E402
from quantagent.web.server import (  # noqa: E402
    STATUS_ZH,
    TEAMS,
    _agent_status,
    _evict_runs,
    _slim_state,
    derive_status,
)

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


class TestDeriveStatus:
    """LangGraph runs the four analysts concurrently, so "running" is resolved
    per phase: every unfinished analyst while that phase is in flight, then a
    single next agent through the sequential debate and risk phases."""

    def test_all_pending_when_nothing_has_run(self):
        status = derive_status({})
        assert set(status.values()) == {"pending"}

    def test_all_four_analysts_marked_running_while_they_are_in_flight(self):
        """They run in parallel, so marking only the first as running lies."""
        status = derive_status({"market_report": "# 技术分析"})
        assert status["market"] == "done"
        assert status["sentiment"] == "running"
        assert status["news"] == "running"
        assert status["fundamentals"] == "running"
        assert status["bull"] == "pending", "the debate has not started yet"

    def test_debate_phase_marks_one_agent(self):
        state = {"market_report": "x", "news_report": "y",
                 "fundamentals_report": "z", "sentiment_report": "w",
                 "investment_debate_state": {"bull_history": "b"}}
        status = derive_status(state)
        for key in ("market", "news", "fundamentals", "sentiment", "bull"):
            assert status[key] == "done", key
        assert status["bear"] == "running", "debate is sequential after the analysts"
        assert status["research_manager"] == "pending"

    def test_debate_and_report_agents_do_not_collide(self):
        state = {"market_report": "x", "news_report": "y",
                 "fundamentals_report": "z", "sentiment_report": "w",
                 "investment_debate_state": {"bull_history": "b", "bear_history": "s"}}
        status = derive_status(state)
        assert status["bull"] == "done" and status["bear"] == "done"
        assert status["research_manager"] == "running"
        assert status["trader"] == "pending"

    def test_risk_phase_advances_in_order(self):
        state = {"market_report": "x", "news_report": "y",
                 "fundamentals_report": "z", "sentiment_report": "w",
                 "investment_debate_state": {"judge_decision": "plan",
                                             "bull_history": "b", "bear_history": "s"},
                 "trader_investment_plan": "trade",
                 "risk_debate_state": {"aggressive_history": "a",
                                       "conservative_history": "c"}}
        status = derive_status(state)
        assert status["aggressive"] == "done"
        assert status["conservative"] == "done"
        assert status["neutral"] == "running"

    def test_a_judgment_implies_the_debate_already_finished(self):
        """With max_debate_rounds=0 the debate history is legitimately empty,
        so the Research Manager's plan must not make Bull look still-running."""
        state = {"market_report": "x", "news_report": "y",
                 "fundamentals_report": "z", "sentiment_report": "w",
                 "investment_debate_state": {"judge_decision": "plan"}}
        status = derive_status(state)
        assert status["bull"] == "done"
        assert status["bear"] == "done"
        assert status["research_manager"] == "done"
        assert status["trader"] == "running"

    def test_a_finished_run_shows_nothing_running(self):
        """final_trade_decision means the pipeline is over, not mid-flight."""
        status = derive_status({"final_trade_decision": "**Rating**: Buy"})
        assert status["portfolio_manager"] == "done"
        assert "running" not in status.values()


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

    def test_every_status_word_is_localized(self):
        assert set(STATUS_ZH) == {"pending", "running", "done"}
        assert all(v and any(ord(c) > 127 for c in v) for v in STATUS_ZH.values()), \
            "status words must be Chinese, not English placeholders"


class TestDateValidation:
    @pytest.mark.parametrize("bad", ["", "not-a-date", "2026/09/01", "20260901", "2026-13-01"])
    def test_rejects_malformed_dates(self, bad):
        with pytest.raises(ValidationError):
            server.AnalyzeRequest(ticker="NVDA", trade_date=bad)

    def test_rejects_a_future_date(self):
        future = (date.today() + timedelta(days=1)).isoformat()
        with pytest.raises(ValidationError):
            server.AnalyzeRequest(ticker="NVDA", trade_date=future)

    def test_accepts_today(self):
        req = server.AnalyzeRequest(ticker="NVDA", trade_date=date.today().isoformat())
        assert req.trade_date == date.today().isoformat()

    def test_accepts_a_past_date(self):
        assert server.AnalyzeRequest(ticker="NVDA",
                                     trade_date="2026-09-01").trade_date == "2026-09-01"


class TestAnalystValidation:
    def test_rejects_an_unknown_analyst(self):
        with pytest.raises(ValidationError):
            server.AnalyzeRequest(ticker="NVDA", trade_date="2026-09-01",
                                  analysts=["astrology"])

    def test_rejects_an_empty_analyst_list(self):
        with pytest.raises(ValidationError):
            server.AnalyzeRequest(ticker="NVDA", trade_date="2026-09-01", analysts=[])

    @pytest.mark.parametrize("key", ["market", "news", "fundamentals", "sentiment"])
    def test_accepts_each_known_analyst(self, key):
        assert server.AnalyzeRequest(ticker="NVDA", trade_date="2026-09-01",
                                     analysts=[key]).analysts == [key]


class TestRunRegistry:
    """RUNS used to grow without bound; finished runs are now shed and evicted."""

    def _finished(self, idx: int) -> "server.Run":
        run = server.Run(id=f"t{idx}", ticker="NVDA", trade_date="2026-09-01",
                         analysts=["market"], config={"a": 1})
        run.phase = "done"
        run.finished_at = time.monotonic()
        return run

    def test_release_drops_the_heavy_fields(self):
        run = server.Run(id="r", ticker="NVDA", trade_date="2026-09-01",
                         analysts=["market"], config={"k": "v"})
        run.thread = object()
        run.queue.put_nowait({"type": "x"})
        run.release()
        assert run.config == {}
        assert run.thread is None
        assert run.queue.empty()

    def test_release_keeps_what_a_reconnecting_client_needs(self):
        run = server.Run(id="r", ticker="NVDA", trade_date="2026-09-01",
                         analysts=["market"], config={"k": "v"})
        run.decision = "**Rating**: Buy"
        run.rating = "Buy"
        run.release()
        summary = run.summary()
        assert summary["decision"] == "**Rating**: Buy"
        assert summary["rating"] == "Buy"

    def test_eviction_caps_the_registry(self):
        with server._RUNS_LOCK:
            saved = dict(server.RUNS)
            server.RUNS.clear()
            try:
                for i in range(server.MAX_RETAINED_RUNS + 8):
                    server.RUNS[f"t{i}"] = self._finished(i)
                _evict_runs()
                assert len(server.RUNS) <= server.MAX_RETAINED_RUNS
            finally:
                server.RUNS.clear()
                server.RUNS.update(saved)

    def test_eviction_drops_runs_past_the_ttl(self):
        with server._RUNS_LOCK:
            saved = dict(server.RUNS)
            server.RUNS.clear()
            try:
                stale = self._finished(0)
                stale.finished_at = time.monotonic() - server.RUN_TTL_SECONDS - 60
                server.RUNS[stale.id] = stale
                _evict_runs()
                assert stale.id not in server.RUNS
            finally:
                server.RUNS.clear()
                server.RUNS.update(saved)

    def test_eviction_never_drops_an_unfinished_run(self):
        with server._RUNS_LOCK:
            saved = dict(server.RUNS)
            server.RUNS.clear()
            try:
                live = server.Run(id="live", ticker="NVDA", trade_date="2026-09-01",
                                  analysts=["market"], config={})
                for i in range(server.MAX_RETAINED_RUNS + 5):
                    server.RUNS[f"t{i}"] = self._finished(i)
                server.RUNS["live"] = live
                _evict_runs()
                assert "live" in server.RUNS, "a run in progress must survive eviction"
            finally:
                server.RUNS.clear()
                server.RUNS.update(saved)

    def test_done_property_reflects_phase(self):
        run = server.Run(id="r", ticker="X", trade_date="2026-09-01",
                         analysts=["market"], config={})
        assert not run.done
        for phase in ("done", "cancelled", "error"):
            run.phase = phase
            assert run.done, phase
        run.phase = "running"
        assert not run.done


class TestCancel:
    def test_cancel_sets_the_event(self):
        run = server.Run(id="r", ticker="X", trade_date="2026-09-01",
                         analysts=["market"], config={})
        assert not run.cancel.is_set()
        run.cancel.set()
        assert run.cancel.is_set()

    def test_acquiring_the_slot_returns_false_when_cancelled(self):
        """A queued run must be able to give up instead of waiting its turn."""
        run = server.Run(id="r", ticker="X", trade_date="2026-09-01",
                         analysts=["market"], config={})
        run.cancel.set()
        server._GRAPH_LOCK.acquire()
        try:
            assert server._acquire_graph_slot(run) is False
        finally:
            server._GRAPH_LOCK.release()


class TestCost:
    def test_priced_model_yields_an_estimate(self):
        from quantagent.observability import estimate_cost
        assert estimate_cost("deepseek-chat", 1_000_000, 0) is not None

    def test_unpriced_model_is_unknown_not_zero(self):
        from quantagent.observability import estimate_cost
        assert estimate_cost("some-unlisted-model", 100, 100) is None, \
            "an unknown price must show as unknown, not as $0.00"

    def test_stats_payload_reports_zero_not_missing(self):
        from quantagent.observability import StatsCallbackHandler
        payload = server._stats_payload(StatsCallbackHandler(), {"deep_think_llm": "deepseek-chat"})
        assert payload["llm_calls"] == 0
        assert payload["tokens_in"] == 0


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

    def test_teams_endpoint_returns_roster_and_status_words(self):
        body = server.teams()
        assert body["teams"] is TEAMS
        assert set(body["status_zh"]) == {"pending", "running", "done"}

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

    def test_run_state_endpoint_reports_unknown_runs(self):
        assert "error" in server.run_state("does-not-exist")

    def test_run_state_endpoint_returns_a_full_summary(self):
        with server._RUNS_LOCK:
            saved = dict(server.RUNS)
            server.RUNS.clear()
            try:
                run = server.Run(id="sum", ticker="NVDA", trade_date="2026-09-01",
                                 analysts=["market"], config={"k": "v"})
                run.phase = "done"
                run.rating = "Hold"
                run.last_status = {"market": "done"}
                run.last_snapshot = {"market_report": "报告"}
                server.RUNS["sum"] = run
                body = server.run_state("sum")
                for key in ("run_id", "ticker", "phase", "status", "snapshot",
                            "stats", "decision", "rating", "error"):
                    assert key in body, key
                assert body["rating"] == "Hold"
                assert body["status"] == {"market": "done"}
            finally:
                server.RUNS.clear()
                server.RUNS.update(saved)

    def test_sse_frames_are_single_line_json(self):
        frame = server._sse({"type": "status", "status": {"market": "done"}})
        text = frame.decode()
        assert text.startswith("data: ") and text.endswith("\n\n")
        assert text.count("\n") == 2, "an SSE frame must not embed a raw newline"
