"""Web UI for QuantAgent.

A small FastAPI app that runs the multi-agent graph behind an SSE stream so
the browser can watch each analyst and debater light up as it finishes, the
same way the terminal TUI does.

The graph already runs with ``stream_mode="values"`` (see
``graph/propagation.py``), so every node completion yields the whole state.
Agent status is derived by diffing that state — no changes to the graph, the
agents or the vendor layer were needed to make the pipeline web-visible.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
import traceback
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

STATIC_DIR = Path(__file__).parent / "static"

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
            {"key": "sentiment", "label": "Sentiment Analyst", "label_zh": "情绪分析师",
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


def _agent_status(agent: dict, state: dict) -> str:
    """pending | running | done, derived purely from graph state."""
    slot = agent.get("slot")
    if slot:
        debate = state.get(agent["debate"]) or {}
        value = debate.get(slot) if isinstance(debate, dict) else None
    else:
        value = state.get(agent["field"])
    return "done" if value else "pending"


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
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(8))
    thread: threading.Thread | None = None
    done: bool = False
    error: str | None = None


RUNS: dict[str, Run] = {}
_RUNS_LOCK = threading.Lock()
_GRAPH_LOCK = threading.Lock()


class AnalyzeRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    trade_date: str
    analysts: list[str] = Field(
        default_factory=lambda: ["market", "news", "fundamentals", "sentiment"]
    )
    deep_think_llm: str | None = None
    quick_think_llm: str | None = None
    max_debate_rounds: int | None = Field(default=None, ge=0, le=10)
    max_risk_rounds: int | None = Field(default=None, ge=0, le=10)


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
    with contextlib.suppress(RuntimeError):
        loop.call_soon_threadsafe(run.queue.put_nowait, event)


def _run_analysis(run: Run, req: AnalyzeRequest) -> None:
    """Worker thread: drive the graph and stream state deltas as SSE events."""
    import quantagent  # noqa: F401  (loads .env)
    from quantagent.agents.rating import extract_rating, parse_rating
    from quantagent.graph.trading_graph import QuantAgentGraph

    loop = run.loop
    try:
        # One graph at a time: the vendor router and the decision log hold
        # process-level state, and two concurrent SSE clients would interleave.
        with _GRAPH_LOCK:
            graph = QuantAgentGraph(
                selected_analysts=req.analysts, config=run.config, debug=False
            )
            init_state = graph.create_run_state(run.ticker, run.trade_date)
            args = graph.propagator.get_graph_args()

            _emit(loop, run, {"type": "started", "ticker": run.ticker,
                              "trade_date": run.trade_date, "teams": TEAMS})

            previous: dict = {}
            final: dict = {}
            for chunk in graph.graph.stream(init_state, **args):
                final = chunk
                status = {
                    agent["key"]: _agent_status(agent, chunk)
                    for team in TEAMS for agent in team["agents"]
                }
                if status != previous:
                    previous = status
                    _emit(loop, run, {"type": "status", "status": status})
                _emit(loop, run, {"type": "snapshot", "state": _slim_state(chunk)})

            decision = final.get("final_trade_decision", "")
            _emit(loop, run, {
                "type": "done",
                "decision": decision,
                "rating": parse_rating(decision) if decision else None,
                "extracted": extract_rating(decision) if decision else None,
            })
    except Exception as exc:  # surfaced to the browser rather than swallowed
        _emit(loop, run, {"type": "error", "error": f"{type(exc).__name__}: {exc}",
                          "traceback": traceback.format_exc()})
    finally:
        _emit(loop, run, {"type": "closed"})
        run.done = True
        loop.call_soon_threadsafe(run.queue.put_nowait, None)


_REPORT_FIELDS = (    "market_report", "sentiment_report", "news_report", "fundamentals_report",
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
def teams() -> list[dict]:
    return TEAMS


@app.get("/api/config")
def config() -> dict:
    import quantagent  # noqa: F401
    from quantagent.default_config import DEFAULT_CONFIG

    return {
        "llm_provider": DEFAULT_CONFIG["llm_provider"],
        "deep_think_llm": DEFAULT_CONFIG["deep_think_llm"],
        "quick_think_llm": DEFAULT_CONFIG["quick_think_llm"],
        "output_language": DEFAULT_CONFIG["output_language"],
        "max_debate_rounds": DEFAULT_CONFIG["max_debate_rounds"],
        "max_risk_discuss_rounds": DEFAULT_CONFIG["max_risk_discuss_rounds"],
        "results_dir": DEFAULT_CONFIG["results_dir"],
        "api_key_configured": bool(os.environ.get("DEEPSEEK_API_KEY")
                                    or os.environ.get("OPENAI_API_KEY")),
    }


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest) -> dict:
    run = Run(
        id=uuid.uuid4().hex[:12],
        ticker=req.ticker.strip().upper(),
        trade_date=req.trade_date,
        analysts=req.analysts,
        config=_build_config(req),
    )
    with _RUNS_LOCK:
        RUNS[run.id] = run

    loop = asyncio.get_running_loop()
    run.loop = loop
    run.thread = threading.Thread(
        target=_run_analysis, args=(run, req), daemon=True
    )
    run.thread.start()
    return {"run_id": run.id, "ticker": run.ticker, "trade_date": run.trade_date}


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
        )

    async def gen() -> Iterator[bytes]:
        while True:
            try:
                event = await asyncio.wait_for(run.queue.get(), timeout=20.0)
            except asyncio.TimeoutError:
                yield _sse({"type": "heartbeat"})
                continue
            if event is None:
                break
            yield _sse(event)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
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
