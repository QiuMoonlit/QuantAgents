"""Web UI for QuantAgent.

A small FastAPI app that runs the multi-agent graph behind an SSE stream so
the browser can watch each analyst and debater light up as it finishes, the
same way the terminal TUI does.

The graph already runs with ``stream_mode="values"`` (see
``graph/propagation.py``), so every node completion yields the whole state.
Agent status is derived by diffing that state — no changes to the graph, the
agents or the vendor layer were needed to make the pipeline web-visible.

Operational notes
-----------------
* **One run at a time.** ``_GRAPH_LOCK`` serialises analysis because the vendor
  router and the decision log hold process-level state. Queued runs emit a
  ``queued`` event so the browser can say so instead of appearing to hang.
* **Cancellation is cooperative.** ``Run.cancel`` is checked between graph
  nodes, so a cancelled run stops before its next LLM call. The node already in
  flight when the user hits stop still runs to completion — an in-flight HTTP
  request to a provider cannot be interrupted from here.
* **Runs are bounded.** Finished runs shed their config, thread and queue, and
  the registry evicts past ``MAX_RETAINED_RUNS`` / ``RUN_TTL_SECONDS`` so a
  long-lived server does not accumulate state.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import logging
import os
import threading
import time
import traceback
import uuid
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from quantagent.observability import (
    MODEL_PRICING,
    StatsCallbackHandler,
    estimate_cost,
)

STATIC_DIR = Path(__file__).parent / "static"

# Finished runs are kept this long so a reconnecting browser can still collect
# its result, and at most this many at once.
RUN_TTL_SECONDS = 30 * 60
MAX_RETAINED_RUNS = 20
REPLAY_BUFFER = 60

app = FastAPI(title="QuantAgent", docs_url="/api/docs", redoc_url=None)


# --------------------------------------------------------------------------
# Agent roster: how to tell, from the graph state alone, that a node finished.
# --------------------------------------------------------------------------

TEAMS: list[dict[str, Any]] = [
    {
        "key": "analysts",
        "label": "Analyst Team",
        "label_zh": "分析团队",
        "agents": [
            {"key": "market", "label": "Market Analyst", "label_zh": "技术分析师",
             "field": "market_report"},
            {"key": "social", "label": "Sentiment Analyst", "label_zh": "情绪分析师",
             "field": "sentiment_report"},
            {"key": "news", "label": "News Analyst", "label_zh": "新闻分析师",
             "field": "news_report"},
            {"key": "fundamentals", "label": "Fundamentals Analyst", "label_zh": "基本面分析师",
             "field": "fundamentals_report"},
        ],
    },
    {
        "key": "researchers",
        "label": "Research Team",
        "label_zh": "研究团队",
        "agents": [
            {"key": "bull", "label": "Bull Researcher", "label_zh": "多头研究员",
             "debate": "investment_debate_state", "slot": "bull_history"},
            {"key": "bear", "label": "Bear Researcher", "label_zh": "空头研究员",
             "debate": "investment_debate_state", "slot": "bear_history"},
            {"key": "research_manager", "label": "Research Manager", "label_zh": "研究主管",
             "debate": "investment_debate_state", "slot": "judge_decision"},
        ],
    },
    {
        "key": "trader",
        "label": "Trading Team",
        "label_zh": "交易团队",
        "agents": [
            {"key": "trader", "label": "Trader", "label_zh": "交易员",
             "field": "trader_investment_plan"},
        ],
    },
    {
        "key": "risk",
        "label": "Risk Team",
        "label_zh": "风控团队",
        "agents": [
            {"key": "aggressive", "label": "Aggressive Analyst", "label_zh": "激进派",
             "debate": "risk_debate_state", "slot": "aggressive_history"},
            {"key": "conservative", "label": "Conservative Analyst", "label_zh": "保守派",
             "debate": "risk_debate_state", "slot": "conservative_history"},
            {"key": "neutral", "label": "Neutral Analyst", "label_zh": "中立派",
             "debate": "risk_debate_state", "slot": "neutral_history"},
        ],
    },
    {
        "key": "portfolio",
        "label": "Portfolio Team",
        "label_zh": "组合团队",
        "agents": [
            {"key": "portfolio_manager", "label": "Portfolio Manager", "label_zh": "组合经理",
             "field": "final_trade_decision"},
        ],
    },
]

# Localized status words, so the UI does not ship English strings for a
# Chinese-language run.
STATUS_ZH = {"pending": "等待中", "running": "分析中", "done": "已完成"}


def _agent_status(agent: dict, state: dict) -> str:
    """pending | done, derived purely from graph state.

    A node's completion is observed at the *next* state snapshot: LangGraph's
    ``stream_mode="values"`` emits the state after a node returns, so an agent
    whose output field is still empty is the one currently running.
    """
    slot = agent.get("slot")
    if slot:
        debate = state.get(agent["debate"]) or {}
        value = debate.get(slot) if isinstance(debate, dict) else None
    else:
        value = state.get(agent["field"])
    return "done" if value else "pending"


# The graph runs the four analysts concurrently and everything after them in
# sequence, so "which agent is running" differs by phase. Order here is the
# execution order used to pick the next agent to highlight.
SEQUENTIAL_ORDER = [
    "bull", "bear", "research_manager", "trader",
    "aggressive", "conservative", "neutral", "portfolio_manager",
]
ANALYST_KEYS = ("market", "social", "news", "fundamentals")


def derive_status(state: dict, previous: dict | None = None) -> dict[str, str]:
    """Per-agent status from a graph state snapshot.

    ``stream_mode="values"`` emits state *after* a node returns, so an agent
    whose output is still empty is one that has not finished. Emptiness alone
    leaves the board entirely grey, so the phase is resolved here:

    * nothing done yet -> everything pending (the first node is mid-flight)
    * the analyst phase -> every unfinished analyst is running, because
      LangGraph runs those four concurrently
    * later phases -> the single next agent in execution order is running
    * ``final_trade_decision`` present -> the run is over, nothing running
    """
    derived = {
        agent["key"]: _agent_status(agent, state)
        for team in TEAMS for agent in team["agents"]
    }

    if state.get("final_trade_decision"):
        return derived

    analyst_done = [k for k in ANALYST_KEYS if derived[k] == "done"]
    if not analyst_done:
        return derived

    if len(analyst_done) < len(ANALYST_KEYS):
        # Still inside the parallel analyst phase.
        for key in ANALYST_KEYS:
            if derived[key] == "pending":
                derived[key] = "running"
        return derived

    # A later agent finishing implies the earlier ones in the sequence are
    # over: the Research Manager only runs after the debate, and with
    # max_debate_rounds=0 the debate history is legitimately empty. Taking
    # the furthest completed position avoids marking a finished debater as the
    # one that is still running.
    reached = [i for i, key in enumerate(SEQUENTIAL_ORDER) if derived[key] == "done"]
    if not reached:
        return derived
    furthest = max(reached)
    for key in SEQUENTIAL_ORDER[:furthest]:
        derived[key] = "done"
    nxt = furthest + 1
    if nxt < len(SEQUENTIAL_ORDER):
        derived[SEQUENTIAL_ORDER[nxt]] = "running"
    return derived

# --------------------------------------------------------------------------
# Run bookkeeping
# --------------------------------------------------------------------------


@dataclass
class Run:
    id: str
    ticker: str
    trade_date: str
    analysts: list[str]
    config: dict
    loop: Any = None
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(16))
    thread: threading.Thread | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    created_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    # Replayed to a browser that reconnects after a dropped SSE connection.
    replay: deque = field(default_factory=lambda: deque(maxlen=REPLAY_BUFFER))
    last_status: dict = field(default_factory=dict)
    last_snapshot: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    decision: str | None = None
    rating: str | None = None
    error: str | None = None
    phase: str = "queued"      # queued | running | done | cancelled | error

    @property
    def done(self) -> bool:
        return self.phase in ("done", "cancelled", "error")

    def summary(self) -> dict:
        """Reconnect-safe view: enough to redraw the whole run."""
        return {
            "run_id": self.id,
            "ticker": self.ticker,
            "trade_date": self.trade_date,
            "analysts": self.analysts,
            "phase": self.phase,
            "status": self.last_status,
            "snapshot": self.last_snapshot,
            "stats": self.stats,
            "decision": self.decision,
            "rating": self.rating,
            "error": self.error,
        }

    def release(self) -> None:
        """Drop everything heavy once the run can no longer change."""
        self.config = {}
        self.thread = None
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break


RUNS: dict[str, Run] = {}
_RUNS_LOCK = threading.Lock()
_GRAPH_LOCK = threading.Lock()


def _evict_runs() -> None:
    """Bound the registry. Caller holds ``_RUNS_LOCK``."""
    now = time.monotonic()
    for run_id in [r.id for r in RUNS.values() if r.finished_at is not None]:
        if now - RUNS[run_id].finished_at > RUN_TTL_SECONDS:
            del RUNS[run_id]
    if len(RUNS) <= MAX_RETAINED_RUNS:
        return
    finished = sorted(
        (r for r in RUNS.values() if r.done), key=lambda r: r.finished_at or 0.0
    )
    for run in finished[: len(RUNS) - MAX_RETAINED_RUNS]:
        del RUNS[run.id]


def _known_analyst_keys() -> set[str]:
    """The analyst keys the graph accepts, read from its own registry.

    Duplicating this list is how the web UI came to offer "sentiment" while
    the graph wanted "social": the validator and the graph then agreed on
    nothing and every sentiment run died in ``setup_graph``. There is one
    source of truth and this reads it.
    """
    from quantagent.graph.analyst_execution import ANALYST_NODE_SPECS

    return set(ANALYST_NODE_SPECS)


class AnalyzeRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    trade_date: str
    analysts: list[str] = Field(
        default_factory=lambda: ["market", "news", "fundamentals", "social"]
    )
    deep_think_llm: str | None = None
    quick_think_llm: str | None = None
    max_debate_rounds: int | None = Field(default=None, ge=0, le=10)
    max_risk_rounds: int | None = Field(default=None, ge=0, le=10)

    @field_validator("trade_date")
    @classmethod
    def _valid_date(cls, value: str) -> str:
        """Same rule the CLI applies: YYYY-MM-DD, never in the future.

        ``strptime`` rather than ``date.fromisoformat`` on purpose — the latter
        also accepts the compact ``20260901`` form on 3.11+, which the CLI's
        ``get_analysis_date`` rejects, and the two entry points should agree.

        The graph treats trade_date as a point-in-time anchor, so a future or
        malformed value otherwise fails deep inside a vendor with an opaque
        message. Rejecting it here turns that into a 422.
        """
        try:
            parsed = datetime.datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("date must be YYYY-MM-DD") from exc
        if parsed.date() > datetime.date.today():
            raise ValueError("date cannot be in the future")
        return value

    @field_validator("analysts")
    @classmethod
    def _known_analysts(cls, value: list[str]) -> list[str]:
        known = _known_analyst_keys()
        unknown = [v for v in value if v not in known]
        if unknown:
            raise ValueError(
                f"unknown analyst(s): {', '.join(unknown)}; "
                f"the graph accepts {', '.join(sorted(known))}"
            )
        if not value:
            raise ValueError("select at least one analyst")
        return value


def _build_config(req: AnalyzeRequest) -> dict:
    from quantagent.default_config import DEFAULT_CONFIG

    cfg = DEFAULT_CONFIG.copy()
    if req.deep_think_llm:
        cfg["deep_think_llm"] = req.deep_think_llm
    if req.quick_think_llm:
        cfg["quick_think_llm"] = req.quick_think_llm
    if req.max_debate_rounds is not None:
        cfg["max_debate_rounds"] = req.max_debate_rounds
    if req.max_risk_rounds is not None:
        cfg["max_risk_discuss_rounds"] = req.max_risk_rounds
    return cfg


def _emit(loop: asyncio.AbstractEventLoop, run: Run, event: dict) -> None:
    """Push an event onto the run's queue from a worker thread.

    The loop may already be closed if the browser disconnected mid-run; that
    is not an error worth reporting, the run is simply unobserved.
    """
    run.replay.append(event)
    with contextlib.suppress(RuntimeError):
        loop.call_soon_threadsafe(run.queue.put_nowait, event)


def _acquire_graph_slot(run: Run) -> bool:
    """Wait for the global analysis slot, staying cancellable while queued.

    Emits one ``queued`` frame on the first wait so the browser can say the
    run is waiting rather than appearing to hang on a silent lock.
    """
    announced = False
    while not _GRAPH_LOCK.acquire(timeout=0.25):
        if run.cancel.is_set():
            return False
        if not announced:
            announced = True
            _emit(run.loop, run, {"type": "queued", "position": 1})
    return True


def _run_analysis(run: Run, req: AnalyzeRequest) -> None:
    """Worker thread: drive the graph and stream state deltas as SSE events."""
    import quantagent  # noqa: F401  (loads .env)
    from quantagent.agents.rating import extract_rating, parse_rating
    from quantagent.graph.trading_graph import QuantAgentGraph

    loop = run.loop
    stats_handler = StatsCallbackHandler()

    try:
        if not _acquire_graph_slot(run):
            run.phase = "cancelled"
            _emit(loop, run, {"type": "cancelled", "reason": "cancelled while queued"})
            return
        try:
            if run.cancel.is_set():
                run.phase = "cancelled"
                _emit(loop, run, {"type": "cancelled", "reason": "cancelled before start"})
                return

            run.phase = "running"
            graph = QuantAgentGraph(
                selected_analysts=req.analysts, config=run.config, debug=False
            )
            init_state = graph.create_run_state(run.ticker, run.trade_date)
            args = graph.propagator.get_graph_args(callbacks=[stats_handler])

            _emit(loop, run, {
                "type": "started",
                "ticker": run.ticker,
                "trade_date": run.trade_date,
                "teams": TEAMS,
                "status_zh": STATUS_ZH,
            })

            previous: dict = {}
            final: dict = {}
            for chunk in graph.graph.stream(init_state, **args):
                final = chunk
                run.last_snapshot = _slim_state(chunk)
                status = derive_status(chunk)
                if status != previous:
                    previous = status
                    run.last_status = status
                    _emit(loop, run, {"type": "status", "status": status})

                run.stats = _stats_payload(stats_handler, run.config)
                _emit(loop, run, {"type": "snapshot",
                                  "state": run.last_snapshot, "stats": run.stats})

                # Cooperative stop: checked between nodes, so no further LLM
                # call is issued. The node in flight is already paid for.
                if run.cancel.is_set():
                    run.phase = "cancelled"
                    _emit(loop, run, {"type": "cancelled",
                                      "reason": "stopped by user",
                                      "stats": run.stats})
                    return

            decision = final.get("final_trade_decision", "")
            run.decision = decision
            run.rating = parse_rating(decision) if decision else None
            run.phase = "done"
            _emit(loop, run, {
                "type": "done",
                "decision": decision,
                "rating": run.rating,
                "extracted": extract_rating(decision) if decision else None,
                "stats": run.stats,
            })
        finally:
            _GRAPH_LOCK.release()
    except Exception as exc:  # surfaced to the browser rather than swallowed
        run.phase = "error"
        run.error = f"{type(exc).__name__}: {exc}"
        _emit(loop, run, {"type": "error", "error": run.error,
                          "traceback": traceback.format_exc()})
    finally:
        if not run.done:
            run.phase = run.phase if run.phase != "running" else "error"
        run.finished_at = time.monotonic()
        run.release()
        _emit(loop, run, {"type": "closed"})
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(run.queue.put_nowait, None)


def _stats_payload(handler: StatsCallbackHandler, config: dict) -> dict:
    stats = handler.get_stats()
    stats["cost_usd"] = estimate_cost(
        config.get("deep_think_llm", ""), stats["tokens_in"], stats["tokens_out"]
    )
    return stats


_REPORT_FIELDS = (
    "market_report", "sentiment_report", "news_report", "fundamentals_report",
    "trader_investment_plan", "final_trade_decision",
)


def _slim_state(state: dict) -> dict:
    """Only the report text — the full state is far too big to ship per node."""
    slim: dict[str, Any] = {f: state.get(f, "") for f in _REPORT_FIELDS}
    for debate in ("investment_debate_state", "risk_debate_state"):
        block = state.get(debate) or {}
        if isinstance(block, dict):
            for k, v in block.items():
                if k.endswith("_history") or k in ("judge_decision", "latest_speaker"):
                    slim[f"{debate}.{k}"] = v
    return slim


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/teams")
def teams() -> dict:
    return {"teams": TEAMS, "status_zh": STATUS_ZH}


@app.get("/api/config")
def config() -> dict:
    import quantagent  # noqa: F401
    from quantagent.default_config import DEFAULT_CONFIG
    from quantagent.llm_clients.api_key_env import get_api_key_env

    # Ask the provider registry which variable holds this provider's key rather
    # than naming a couple of them here: a hardcoded pair reports "no key" for
    # the other seventeen providers even when they are configured correctly.
    provider = DEFAULT_CONFIG["llm_provider"]
    key_env = get_api_key_env(provider)
    # None means the provider authenticates some other way (ollama is keyless,
    # bedrock uses the AWS credential chain), so there is nothing to be missing.
    api_key_configured = bool(os.environ.get(key_env)) if key_env else True

    return {
        "llm_provider": DEFAULT_CONFIG["llm_provider"],
        "deep_think_llm": DEFAULT_CONFIG["deep_think_llm"],
        "quick_think_llm": DEFAULT_CONFIG["quick_think_llm"],
        "output_language": DEFAULT_CONFIG["output_language"],
        "max_debate_rounds": DEFAULT_CONFIG["max_debate_rounds"],
        "max_risk_discuss_rounds": DEFAULT_CONFIG["max_risk_discuss_rounds"],
        "results_dir": DEFAULT_CONFIG["results_dir"],
        "state_dirs_writable": state_dir_problem() is None,
        "state_dir_error": state_dir_problem(),
        "api_key_configured": api_key_configured,
        "pricing": sorted(MODEL_PRICING),
    }


logger = logging.getLogger(__name__)


def state_dir_problem() -> str | None:
    """Why the run's state directories are unusable, or None if they are fine.

    QuantAgent writes its cache, results and memory log under
    ``~/.quantagent`` by default. That is the right default in a normal
    terminal and the wrong one in a sandboxed or containerised context, where the
    home directory is outside the writable tree — and the failure surfaces as a
    bare ``PermissionError: [WinError 5]`` from deep inside graph
    construction, several frames past anything the user can act on.

    Checked once at startup and reported on /api/config, so the page can say
    what to do instead of showing a stack trace after a minute of waiting.
    """
    from quantagent.default_config import DEFAULT_CONFIG

    for key in ("results_dir", "data_cache_dir"):
        directory = Path(DEFAULT_CONFIG[key]).expanduser()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".quantagent-write-probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return (
                f"Cannot use {key} at {directory}: {exc.strerror or exc}. "
                f"Point it somewhere writable by setting QUANTAGENT_RESULTS_DIR "
                f"and QUANTAGENT_CACHE_DIR before starting the server."
            )
    return None


@app.on_event("startup")
async def _check_state_dirs() -> None:
    problem = state_dir_problem()
    if problem:
        logger.error("State directories are unusable: %s", problem)


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest) -> dict:
    # Refuse before starting a run rather than letting graph construction raise
    # a PermissionError thirty seconds into a run the user already paid for.
    problem = state_dir_problem()
    if problem:
        raise HTTPException(status_code=503, detail=problem)

    run = Run(
        id=uuid.uuid4().hex[:12],
        ticker=req.ticker.strip().upper(),
        trade_date=req.trade_date,
        analysts=req.analysts,
        config=_build_config(req),
    )
    with _RUNS_LOCK:
        _evict_runs()
        RUNS[run.id] = run

    run.loop = asyncio.get_running_loop()
    run.thread = threading.Thread(
        target=_run_analysis, args=(run, req), daemon=True
    )
    run.thread.start()
    return {"run_id": run.id, "ticker": run.ticker, "trade_date": run.trade_date,
            "phase": run.phase}


@app.post("/api/cancel/{run_id}")
async def cancel(run_id: str) -> dict:
    """Ask a run to stop. Returns immediately; the worker checks between nodes."""
    run = RUNS.get(run_id)
    if run is None:
        return {"ok": False, "error": f"unknown run {run_id}"}
    run.cancel.set()
    return {"ok": True, "phase": run.phase}


@app.get("/api/runs/{run_id}")
def run_state(run_id: str) -> dict:
    """Full run state, so a browser that lost its SSE connection can redraw."""
    run = RUNS.get(run_id)
    if run is None:
        return {"error": f"unknown run {run_id}"}
    return run.summary()


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    # Without this a reverse proxy buffers frames and the browser sees nothing
    # until the run ends, which defeats the whole point of the stream.
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _unknown_run_frame(run_id: str) -> bytes:
    """SSE frame for a stream request naming a run this server never issued."""
    return _sse({"type": "error", "error": f"unknown run {run_id}"})


@app.get("/api/stream/{run_id}")
async def stream(run_id: str) -> StreamingResponse:
    run = RUNS.get(run_id)
    if run is None:
        return StreamingResponse(
            iter([_unknown_run_frame(run_id)]),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    async def gen() -> Iterator[bytes]:
        # Replay first so a reconnecting client lands in the same place.
        for event in list(run.replay):
            yield _sse(event)
        while True:
            try:
                event = await asyncio.wait_for(run.queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield _sse({"type": "heartbeat"})
                continue
            if event is None:
                break
            yield _sse(event)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@app.get("/api/runs")
def runs() -> list[dict]:
    """Recent entries from the decision log, newest first."""
    import quantagent  # noqa: F401
    from quantagent.default_config import DEFAULT_CONFIG

    path = Path(DEFAULT_CONFIG["memory_log_path"])
    if not path.exists():
        return []
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("## "):
            continue
        body = line[3:].strip()
        parts = [p.strip() for p in body.split("|")]
        entries.append({
            "raw": body,
            "ticker": parts[0] if parts else "",
            "trade_date": parts[1] if len(parts) > 1 else "",
            "decision": parts[2] if len(parts) > 2 else "",
        })
    entries.reverse()
    return entries[:100]


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def serve(host: str = "127.0.0.1", port: int = 8420, reload: bool = False) -> None:
    import uvicorn

    print(f"QuantAgent UI  ->  http://{host}:{port}")
    uvicorn.run("quantagent.web.server:app" if reload else app,
                host=host, port=port, reload=reload)
