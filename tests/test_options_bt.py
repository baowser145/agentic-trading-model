"""Tests for options backtest + scenario search agent."""

from __future__ import annotations

from agentic_trading.options_bt.agent import SearchConfig, run_scenario_search
from agentic_trading.options_bt.backtest import run_backtest
from agentic_trading.options_bt.data import synthetic_bars
from agentic_trading.options_bt.metrics import score_metrics, summarize_trades
from agentic_trading.options_bt.pricing import black_scholes, realized_vol, strike_for_target_delta
from agentic_trading.options_bt.scenario import OptionScenario, SEED_SCENARIOS
from agentic_trading.options_bt.validate import validate_scenario


def _series(n: int = 400) -> dict:
    return {
        "SPY": synthetic_bars("SPY", n=n, start_price=400, drift=0.0003, vol=0.01, seed=1),
        "AAPL": synthetic_bars("AAPL", n=n, start_price=180, drift=0.0004, vol=0.015, seed=2),
        "MSFT": synthetic_bars("MSFT", n=n, start_price=350, drift=0.00035, vol=0.012, seed=3),
        "NVDA": synthetic_bars("NVDA", n=n, start_price=100, drift=0.0006, vol=0.025, seed=4),
    }


def test_black_scholes_call_positive():
    px = black_scholes(100, 100, 30 / 365, 0.25, option_type="call")
    assert px.premium > 0
    assert 0.4 < px.delta < 0.7


def test_black_scholes_put_positive():
    px = black_scholes(100, 100, 30 / 365, 0.25, option_type="put")
    assert px.premium > 0
    assert -0.7 < px.delta < -0.3


def test_strike_for_delta():
    k = strike_for_target_delta(100, 21 / 365, 0.3, 0.40, option_type="call")
    assert 90 < k < 110
    bs = black_scholes(100, k, 21 / 365, 0.3, option_type="call")
    assert abs(abs(bs.delta) - 0.40) < 0.15


def test_realized_vol_floor():
    closes = [100.0 + i * 0.1 for i in range(30)]
    v = realized_vol(closes, 20)
    assert 0.10 <= v <= 1.50


def test_backtest_runs_and_trades():
    sc = OptionScenario(
        name="test",
        symbols=["AAPL", "MSFT", "NVDA"],
        min_momentum_pct=0.0,
        require_market_green=False,
        require_above_sma=False,
        take_profit_pct=0.5,
        stop_loss_pct=0.5,
    )
    bt = run_backtest(sc, _series())
    assert bt.metrics.n_trades >= 1
    assert bt.metrics.ending_equity != 0
    m = summarize_trades(bt.trades, starting_equity=1000)
    assert m.n_trades == len(bt.trades)


def test_scenario_clamp_rails():
    sc = OptionScenario(min_dte=1, max_dte=60, stop_loss_pct=0.1, contracts=5)
    sc.clamp_live_rails()
    assert sc.min_dte >= 7
    assert sc.max_dte <= 31
    assert sc.stop_loss_pct >= 0.30
    assert sc.contracts == 1
    assert sc.max_open == 1


def test_seeds_have_unique_names():
    names = [s.name for s in SEED_SCENARIOS]
    assert len(names) == len(set(names))


def test_validate_and_score():
    sc = OptionScenario(
        name="val",
        symbols=["AAPL", "MSFT", "NVDA"],
        min_momentum_pct=0.0,
        require_market_green=False,
        require_above_sma=False,
    )
    series = _series(500)
    rep = validate_scenario(sc, series, min_test_trades=1)
    assert rep.full.n_trades >= 0
    assert isinstance(rep.score, float)
    sc2 = score_metrics(rep.full, min_trades=5)
    assert isinstance(sc2, float)


def test_scenario_search_iterates():
    series = _series(450)
    cfg = SearchConfig(iterations=12, seed=7, top_k=5, mutate_from_top=3, min_trades=5)
    result = run_scenario_search(
        series,
        cfg,
        symbols=["AAPL", "MSFT", "NVDA"],
    )
    assert result.iterations >= 8
    assert result.best is not None
    assert len(result.leaderboard) >= 1
    plan = result.to_dict()["recommended_plan"]
    assert "action" in plan
    assert "playbook" in plan or plan["action"] == "hold_no_options"
