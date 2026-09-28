"""The rating reaches the signal layer as a value, not as parsed prose.

The Portfolio Manager already asks the provider for a structured
``PortfolioDecision``; this pins that the typed rating is what ``_run_graph``
feeds ``process_signal``, so a decision rendered in any language still scores.
"""
from __future__ import annotations

import pytest

from quantagent.agents.managers.portfolio_manager import _rating_of
from quantagent.agents.rating import RATING_REVIEW
from quantagent.agents.schemas import PortfolioDecision, PortfolioRating
from quantagent.agents.structured import invoke_structured_or_freetext_with_model
from quantagent.graph.trading_graph import QuantAgentGraph


class _FakeStructured:
    """Stands in for llm.with_structured_output(Schema)."""

    def __init__(self, result):
        self._result = result
        self.seen = None

    def invoke(self, prompt):
        self.seen = prompt
        return self._result


class _FakePlain:
    def __init__(self, content="free text"):
        self.content = content
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        return self


def _decision(rating: PortfolioRating) -> PortfolioDecision:
    return PortfolioDecision(
        rating=rating,
        executive_summary="结构面偏多。",
        investment_thesis="均线多头排列。",
    )


class TestRatingExtraction:
    def test_reads_the_enum_value_not_its_repr(self):
        assert _rating_of(_decision(PortfolioRating.BUY)) == "Buy"

    @pytest.mark.parametrize("rating", list(PortfolioRating))
    def test_covers_every_tier(self, rating):
        assert _rating_of(_decision(rating)) == rating.value

    def test_is_none_when_there_is_no_model(self):
        assert _rating_of(None) is None

    def test_is_none_when_the_object_has_no_rating(self):
        class Bare:
            pass
        assert _rating_of(Bare()) is None


class TestStructuredCallReturnsTheModel:
    def test_returns_rendered_markdown_and_the_model(self):
        structured = _FakeStructured(_decision(PortfolioRating.OVERWEIGHT))
        rendered, model = invoke_structured_or_freetext_with_model(
            structured, _FakePlain(), "prompt", lambda d: f"**Rating**: {d.rating.value}",
            "PM",
        )
        assert rendered == "**Rating**: Overweight"
        assert model is not None
        assert _rating_of(model) == "Overweight"

    def test_returns_none_model_on_the_free_text_fallback(self):
        class Boom:
            def invoke(self, prompt):
                raise RuntimeError("no structured output here")

        plain = _FakePlain("**Rating**: Hold")
        rendered, model = invoke_structured_or_freetext_with_model(
            Boom(), plain, "prompt", lambda d: "unused", "PM",
        )
        assert rendered == "**Rating**: Hold"
        assert model is None, "the fallback has no model, and must not pretend to"
        assert plain.calls == 1

    def test_a_none_result_is_treated_as_a_structured_miss(self):
        rendered, model = invoke_structured_or_freetext_with_model(
            _FakeStructured(None), _FakePlain("text"), "p", lambda d: "unused", "PM",
        )
        assert model is None
        assert rendered == "text"


class TestSignalUsesTheStructuredRating:
    """Mirrors the precedence _run_graph applies:

        signal_source = state.get("final_rating") or state["final_trade_decision"]
        process_signal(signal_source)
    """

    def _graph(self) -> QuantAgentGraph:
        return object.__new__(QuantAgentGraph)

    def test_translated_prose_alone_cannot_be_read(self):
        """Why the structured path exists: prose parsing is English-only."""
        graph = self._graph()
        chinese_prose = "## 最终决策\n\n**评级**：买入\n\n结构面偏多，建议增持。"
        assert graph.process_signal(chinese_prose) == RATING_REVIEW

    def test_the_structured_rating_carries_the_signal(self):
        graph = self._graph()
        state = {"final_trade_decision": "**评级**：买入", "final_rating": "Buy"}
        assert graph.process_signal(state["final_rating"] or state["final_trade_decision"]) == "Buy"

    def test_falls_back_to_prose_when_no_structured_rating(self):
        graph = self._graph()
        english_prose = "**Rating**: Hold\n\n**Executive Summary**: balanced."
        assert graph.process_signal(english_prose) == "Hold"

    def test_unparseable_prose_still_yields_review(self):
        assert self._graph().process_signal("no rating in here at all") == RATING_REVIEW

    def test_every_tier_survives_the_structured_path(self):
        graph = self._graph()
        for rating in PortfolioRating:
            assert graph.process_signal(_rating_of(_decision(rating))) == rating.value


class TestGraphStateCarriesTheRating:
    def test_initial_state_declares_the_key(self):
        """A missing key would make _run_graph's .get() silently always None."""
        from quantagent.graph.propagation import Propagator

        state = Propagator().create_initial_state("NVDA", "2026-09-01")
        assert "final_rating" in state
        assert state["final_rating"] == ""

    def test_agent_state_declares_the_field(self):
        from quantagent.agents.state import AgentState

        assert "final_rating" in AgentState.__annotations__
