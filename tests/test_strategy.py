from agentic_trading.config import StrategyConfig
from agentic_trading.models import Bar, PortfolioSnapshot, Position, Quote
from agentic_trading.strategy.simple_momentum import SimpleMomentumStrategy
from agentic_trading.models import SignalAction


def test_enter_when_close_above_sma():
    cfg = StrategyConfig(name="simple_momentum", sma_period=3, lookback_bars=5)
    strat = SimpleMomentumStrategy(cfg, ["SPY"])
    # Rising series → last > SMA
    history = {
        "SPY": [
            Bar("SPY", 100.0),
            Bar("SPY", 101.0),
            Bar("SPY", 102.0),
            Bar("SPY", 103.0),
            Bar("SPY", 110.0),
        ]
    }
    quotes = {"SPY": Quote("SPY", 110.0)}
    port = PortfolioSnapshot(cash=1000, equity=1000, positions={})
    signals = strat.generate(quotes, history, port)
    assert len(signals) == 1
    assert signals[0].action == SignalAction.ENTER_LONG


def test_exit_when_close_below_sma_and_long():
    cfg = StrategyConfig(name="simple_momentum", sma_period=3, lookback_bars=5)
    strat = SimpleMomentumStrategy(cfg, ["SPY"])
    history = {
        "SPY": [
            Bar("SPY", 110.0),
            Bar("SPY", 109.0),
            Bar("SPY", 108.0),
            Bar("SPY", 107.0),
            Bar("SPY", 90.0),
        ]
    }
    quotes = {"SPY": Quote("SPY", 90.0)}
    port = PortfolioSnapshot(
        cash=500,
        equity=1000,
        positions={"SPY": Position("SPY", 1.0, 100.0)},
    )
    signals = strat.generate(quotes, history, port)
    assert signals[0].action == SignalAction.EXIT_LONG


def test_hold_when_insufficient_history():
    cfg = StrategyConfig(name="simple_momentum", sma_period=10, lookback_bars=3)
    strat = SimpleMomentumStrategy(cfg, ["SPY"])
    history = {"SPY": [Bar("SPY", 100.0), Bar("SPY", 101.0)]}
    quotes = {"SPY": Quote("SPY", 101.0)}
    port = PortfolioSnapshot(cash=1000, equity=1000, positions={})
    signals = strat.generate(quotes, history, port)
    assert signals[0].action == SignalAction.HOLD
