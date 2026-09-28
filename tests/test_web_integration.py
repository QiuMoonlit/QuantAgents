"""End-to-end tests over the real ASGI stack.

The unit tests in ``test_web_ui`` call handler functions directly, which skips
routing, validation serialization and the SSE transport. These run an actual
uvicorn process and speak HTTP to it.

Starlette's ``TestClient`` is not used: it needs an anyio blocking portal (a
thread plus a fresh event loop per request) and the suite's network guard
blocks the socketpair asyncio needs on Windows. Marking these ``integration``
exempts them from that guard, so a real server on a real port is both possible
and the more honest test.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[1]

_API_KEY_VARS = {
    "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY",
    "XAI_API_KEY", "DASHSCOPE_API_KEY", "DASHSCOPE_CN_API_KEY",
    "ZHIPU_API_KEY", "ZHIPU_CN_API_KEY", "MINIMAX_API_KEY", "MINIMAX_CN_API_KEY",
    "OPENROUTER_API_KEY", "AZURE_OPENAI_API_KEY", "ALPHA_VANTAGE_API_KEY",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(base: str, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base + "/api/config", timeout=2).read()
            return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("server did not come up")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """A real uvicorn process serving the app on a free port.

    The environment is rebuilt rather than inherited. conftest blanks every
    QUANTAGENT_/TRADINGAGENTS_ var in the pytest process, and the API-key
    fixture leaves placeholder values behind; both would be inherited by the
    child, and load_dotenv does not override an already-set variable — so the
    server would silently fall back to the built-in openai default.
    """
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("QUANTAGENT_", "TRADINGAGENTS_"))
           and k not in _API_KEY_VARS}
    env.update({
        # Keep the child's state inside tmp so a test run never touches the
        # developer's real ~/.quantagent.
        "QUANTAGENT_RESULTS_DIR": str(tmp_path_factory.mktemp("logs")),
        "QUANTAGENT_CACHE_DIR": str(tmp_path_factory.mktemp("cache")),
        "QUANTAGENT_MEMORY_LOG_PATH": str(tmp_path_factory.mktemp("mem") / "m.md"),
        # A placeholder key: the transport tests never reach a provider, but
        # graph construction checks that one is configured.
        "DEEPSEEK_API_KEY": "placeholder",
        "QUANTAGENT_LLM_PROVIDER": "deepseek",
        "QUANTAGENT_DEEP_THINK_LLM": "deepseek-chat",
        "QUANTAGENT_QUICK_THINK_LLM": "deepseek-chat",
        "PYTHONPATH": str(REPO_ROOT),
        "PYTHONIOENCODING": "utf-8",
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "quantagent.web.server:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        _wait_for(base)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _get(base: str, path: str, timeout: float = 20.0):
    return json.loads(urllib.request.urlopen(base + path, timeout=timeout).read())


def _post(base: str, path: str, payload=None, timeout: float = 20.0):
    data = json.dumps(payload).encode() if payload is not None else b""
    req = urllib.request.Request(
        base + path, data=data or None,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _post_expect_error(base: str, path: str, payload) -> int:
    try:
        _post(base, path, payload)
    except urllib.error.HTTPError as exc:
        return exc.code
    raise AssertionError("expected an HTTP error")


class TestServerIsUp:
    def test_index_renders(self, server):
        html = urllib.request.urlopen(server + "/", timeout=20).read().decode()
        assert "QuantAgent" in html
        assert "/static/app.css" in html

    def test_static_css_is_served_with_real_bytes(self, server):
        css = urllib.request.urlopen(server + "/static/app.css", timeout=20).read()
        assert css.startswith(b"/*") or b"--bg" in css

    def test_static_js_is_served_with_real_bytes(self, server):
        js = urllib.request.urlopen(server + "/static/app.js", timeout=20).read()
        assert b"EventSource" in js

    def test_config_reports_the_environment(self, server):
        body = _get(server, "/api/config")
        assert body["llm_provider"]
        assert isinstance(body["api_key_configured"], bool)

    def test_teams_returns_roster_and_status_words(self, server):
        body = _get(server, "/api/teams")
        assert len(body["teams"]) == 5
        assert body["status_zh"]["done"] == "已完成"


class TestValidationOverHttp:
    def test_future_date_is_rejected(self, server):
        import datetime
        future = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
        code = _post_expect_error(server, "/api/analyze",
                                  {"ticker": "NVDA", "trade_date": future,
                                   "analysts": ["market"]})
        assert code == 422

    def test_malformed_date_is_rejected(self, server):
        assert _post_expect_error(server, "/api/analyze",
                                  {"ticker": "NVDA", "trade_date": "01-09-2026",
                                   "analysts": ["market"]}) == 422

    def test_compact_iso_date_is_rejected_like_the_cli(self, server):
        """date.fromisoformat would accept 20260901; the CLI's strptime does not."""
        assert _post_expect_error(server, "/api/analyze",
                                  {"ticker": "NVDA", "trade_date": "20260901",
                                   "analysts": ["market"]}) == 422

    def test_empty_ticker_is_rejected(self, server):
        assert _post_expect_error(server, "/api/analyze",
                                  {"ticker": "", "trade_date": "2026-09-01"}) == 422

    def test_empty_analyst_list_is_rejected(self, server):
        assert _post_expect_error(server, "/api/analyze",
                                  {"ticker": "NVDA", "trade_date": "2026-09-01",
                                   "analysts": []}) == 422

    def test_unknown_analyst_is_rejected(self, server):
        assert _post_expect_error(server, "/api/analyze",
                                  {"ticker": "NVDA", "trade_date": "2026-09-01",
                                   "analysts": ["tarot"]}) == 422


class TestRunLifecycleOverHttp:
    """A run is registered, is queryable, and can be cancelled — without ever
    reaching an LLM, since cancellation lands before the first node finishes."""

    def test_run_is_registered_and_queryable(self, server):
        started = _post(server, "/api/analyze",
                        {"ticker": "NVDA", "trade_date": "2026-09-01",
                         "analysts": ["market"],
                         "max_debate_rounds": 0, "max_risk_rounds": 0})
        assert started["run_id"]
        assert started["ticker"] == "NVDA"

        state = _get(server, f"/api/runs/{started['run_id']}")
        assert state["ticker"] == "NVDA"
        assert state["phase"] in ("queued", "running", "done", "cancelled", "error")

        _post(server, f"/api/cancel/{started['run_id']}")

    def test_cancel_endpoint_accepts_a_known_run(self, server):
        started = _post(server, "/api/analyze",
                        {"ticker": "AAPL", "trade_date": "2026-09-01",
                         "analysts": ["market"]})
        body = _post(server, f"/api/cancel/{started['run_id']}")
        assert body["ok"] is True

    def test_cancel_endpoint_reports_an_unknown_run_without_500(self, server):
        body = _post(server, "/api/cancel/nope")
        assert body["ok"] is False
        assert "unknown run" in body["error"]

    def test_streaming_an_unknown_run_yields_an_error_frame(self, server):
        with urllib.request.urlopen(server + "/api/stream/nope", timeout=20) as r:
            body = r.read().decode()
        assert body.startswith("data: ")
        assert "unknown run" in body

    def test_run_state_endpoint_reports_unknown_runs(self, server):
        assert "error" in _get(server, "/api/runs/nope")


class TestSseTransport:
    def test_stream_yields_well_formed_frames(self, server):
        """Transport check only: every frame is `data: <json>` with a type, and
        the server keeps the connection open with heartbeats. Does not wait for
        the run to finish, so it costs seconds rather than minutes."""
        started = _post(server, "/api/analyze",
                        {"ticker": "MSFT", "trade_date": "2026-09-01",
                         "analysts": ["market"],
                         "max_debate_rounds": 0, "max_risk_rounds": 0})
        run_id = started["run_id"]

        frames = []
        deadline = time.time() + 12
        try:
            with urllib.request.urlopen(f"{server}/api/stream/{run_id}",
                                        timeout=30) as r:
                for raw in r:
                    line = raw.decode("utf-8").strip()
                    if line:
                        assert line.startswith("data: "), line
                        frames.append(json.loads(line[6:]))
                    if time.time() > deadline:
                        break
        finally:
            _post(server, f"/api/cancel/{run_id}")

        assert frames, "stream produced no frames"
        assert all(isinstance(f, dict) and "type" in f for f in frames)
        seen_types = [f["type"] for f in frames]
        errors = [f for f in frames if f["type"] == "error"]
        assert not errors, (
            f"the run errored before the transport could be observed: "
            f"{[e.get('error') for e in errors]}"
        )
        assert seen_types[0] in {"started", "queued", "status"}, (
            f"unexpected first frame {seen_types[0]!r}; all: {seen_types}"
        )
        started_frame = next((f for f in frames if f["type"] == "started"), None)
        if started_frame:
            assert len(started_frame["teams"]) == 5
            assert started_frame["status_zh"]["done"] == "已完成"

    @pytest.mark.skipif(
        os.environ.get("QUANTAGENT_E2E_LIVE") != "1",
        reason="waits for a real LLM run to finish; set QUANTAGENT_E2E_LIVE=1 to spend tokens",
    )
    def test_cancelled_run_reports_a_terminal_frame(self, server):
        started = _post(server, "/api/analyze",
                        {"ticker": "AAPL", "trade_date": "2026-09-01",
                         "analysts": ["market"],
                         "max_debate_rounds": 0, "max_risk_rounds": 0})
        run_id = started["run_id"]
        _post(server, f"/api/cancel/{run_id}")

        types = []
        with urllib.request.urlopen(f"{server}/api/stream/{run_id}",
                                    timeout=300) as r:
            for raw in r:
                line = raw.decode("utf-8").strip()
                if line.startswith("data: "):
                    types.append(json.loads(line[6:])["type"])
                if types and types[-1] == "closed":
                    break

        assert "cancelled" in types, f"no cancelled frame: {types}"
        assert types[-1] == "closed", f"stream did not close: {types[-5:]}"
