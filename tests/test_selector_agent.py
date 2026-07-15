from agentic_trading.agent.selector import SelectorConfig, SetupSelectorAgent
from agentic_trading.models import Bar, Quote, Signal, SignalAction


def test_selector_keeps_top_entry_only():
    agent = SetupSelectorAgent(
        SelectorConfig(enabled=True, max_new_entries_per_tick=1, rs_lookback=5)
    )
    # NVDA strong RS, AAPL weaker
    history = {
        "SPY": [Bar("SPY", 100 + i * 0.1) for i in range(12)],
        "NVDA": [Bar("NVDA", 100 + i * 1.0) for i in range(12)],
        "AAPL": [Bar("AAPL", 100 + i * 0.05) for i in range(12)],
    }
    quotes = {
        "SPY": Quote("SPY", history["SPY"][-1].close),
        "NVDA": Quote("NVDA", history["NVDA"][-1].close),
        "AAPL": Quote("AAPL", history["AAPL"][-1].close),
    }
    signals = [
        Signal(
            "AAPL",
            SignalAction.ENTER_LONG,
            0.4,
            "pullback: weak",
            quotes["AAPL"].price,
            stop_price=quotes["AAPL"].price * 0.99,
            target_price=quotes["AAPL"].price * 1.02,
        ),
        Signal(
            "NVDA",
            SignalAction.ENTER_LONG,
            0.8,
            "breakout: strong",
            quotes["NVDA"].price,
            stop_price=quotes["NVDA"].price * 0.99,
            target_price=quotes["NVDA"].price * 1.02,
        ),
        Signal("SPY", SignalAction.HOLD, 0.5, "hold", quotes["SPY"].price),
    ]
    out, notes = agent.select(signals, quotes, history)
    enters = [s for s in out if s.action == SignalAction.ENTER_LONG]
    assert len(enters) == 1
    assert enters[0].symbol == "NVDA"
    assert any("PICK NVDA" in n for n in notes)
    assert any(s.symbol == "AAPL" and s.action == SignalAction.FLAT for s in out)


def test_selector_disabled_passes_all():
    agent = SetupSelectorAgent(SelectorConfig(enabled=False))
    signals = [
        Signal("AAPL", SignalAction.ENTER_LONG, 0.5, "x", 100.0),
        Signal("MSFT", SignalAction.ENTER_LONG, 0.5, "y", 200.0),
    ]
    out, _ = agent.select(signals, {}, {})
    assert sum(1 for s in out if s.action == SignalAction.ENTER_LONG) == 2
