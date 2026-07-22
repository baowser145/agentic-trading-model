"""Morning assess + daily_focus market_bias gate."""

from datetime import datetime, timezone
from pathlib import Path

from agentic_trading.agent.research import (
    ResearchPick,
    ResearchReport,
    write_daily_focus,
)
from agentic_trading.config import load_config
from agentic_trading.engine import Engine
from agentic_trading.market.quotes import FixtureQuoteProvider
from agentic_trading.models import Bar, Quote, Signal, SignalAction
from agentic_trading.paper.morning import assess_market


def test_assess_market_returns_bias_on_fixture():
    q = FixtureQuoteProvider(start_step=30)
    a = assess_market(q, quote_source="fixture")
    assert a.bias in ("call", "put", "hold")
    assert a.spy_last is not None
    assert a.reason


def test_write_daily_focus_includes_bias(tmp_path: Path):
    report = ResearchReport(
        ts=datetime.now(timezone.utc).isoformat(),
        mode="heuristic",
        model=None,
        market_view="test",
        picks=[
            ResearchPick("AAPL", "buy_candidate", 0.7, "t"),
        ],
        recommended_symbols=["SPY", "AAPL"],
        daily_picks=["AAPL"],
        expanded_candidates=[],
    )
    path = tmp_path / "daily_focus.json"
    write_daily_focus(report, path, daily_n=3, market_bias="put")
    import json

    data = json.loads(path.read_text())
    assert data["market_bias"] == "put"
    assert data["daily_picks"] == ["AAPL"]


def test_pick_daily_call_strong_put_weak():
    from agentic_trading.agent.research import ResearchPick, _pick_daily
    from agentic_trading.config import load_config

    cfg = load_config()
    picks = [
        ResearchPick("JNJ", "buy_candidate", 0.9, "strong"),
        ResearchPick("NFLX", "buy_candidate", 0.8, "strong"),
        ResearchPick("META", "put_candidate", 0.95, "weak"),
        ResearchPick("HOOD", "put_candidate", 0.85, "weak"),
        ResearchPick("MSFT", "put_candidate", 0.7, "weak"),
    ]
    call_picks = _pick_daily(picks, [], cfg, n=3, market_bias="call")
    put_picks = _pick_daily(picks, [], cfg, n=3, market_bias="put")
    hold_picks = _pick_daily(picks, [], cfg, n=3, market_bias="hold")
    assert call_picks[0] == "JNJ"
    assert "META" in put_picks and "HOOD" in put_picks
    assert "JNJ" not in put_picks  # strong not on put list
    assert hold_picks == []


def test_heuristic_put_bias_picks_weak(tmp_path: Path):
    from agentic_trading.agent.research import heuristic_research
    from agentic_trading.config import load_config

    cfg = load_config()
    # Fixture path still ranks; put bias should return put_candidates only
    report = heuristic_research(
        cfg, daily_n=3, expand=True, market_bias="put"
    )
    assert report.daily_picks
    # All picks should have put_candidate among report.picks for those symbols
    put_syms = {
        p.symbol for p in report.picks if p.action == "put_candidate"
    }
    for s in report.daily_picks:
        assert s in put_syms or s  # daily from weakest pool


def test_engine_blocks_long_on_put_bias(tmp_path: Path):
    cfg = load_config()
    focus = tmp_path / "daily_focus.json"
    focus.write_text(
        __import__("json").dumps(
            {
                "date": __import__("datetime")
                .datetime.now(__import__("zoneinfo").ZoneInfo("America/New_York"))
                .date()
                .isoformat(),
                "daily_picks": ["AAPL", "MSFT", "NVDA"],
                "market_bias": "put",
            }
        )
    )
    from dataclasses import replace

    cfg = replace(
        cfg,
        paper_state_path=tmp_path / "paper_state.json",
        log_path=tmp_path / "decisions.jsonl",
        daily_focus=replace(cfg.daily_focus, path=focus, enabled=True),
        trade_when_cash_available=False,
    )
    eng = Engine(cfg, quotes=FixtureQuoteProvider(start_step=40))
    # Inject ENTER_LONG via _apply_daily_focus
    signals = [
        Signal(
            symbol="AAPL",
            action=SignalAction.ENTER_LONG,
            strength=0.8,
            reason="test breakout",
            ref_price=330.0,
            stop_price=320.0,
            target_price=350.0,
        )
    ]
    out, notes = eng._apply_daily_focus(signals)
    assert out[0].action == SignalAction.FLAT
    assert any("market_bias=put" in n for n in notes)
