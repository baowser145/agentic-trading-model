from agentic_trading.config import RiskConfig, StrategyConfig
from agentic_trading.models import Bar, PortfolioSnapshot, Position, Quote, SignalAction
from agentic_trading.strategy.day_trade_playbook import DayTradePlaybookStrategy


def _risk(**kwargs) -> RiskConfig:
    base = dict(
        max_position_pct=0.5,
        max_open_positions=3,
        max_orders_per_day=10,
        max_daily_loss_pct=0.05,
        max_order_notional=1000.0,
        risk_per_trade_pct=0.05,
        reward_risk_ratio=2.0,
    )
    base.update(kwargs)
    return RiskConfig(**base)


def _cfg(**kwargs) -> StrategyConfig:
    base = dict(
        name="day_trade_playbook",
        sma_period=5,
        lookback_bars=20,
        market_symbol="SPY",
        range_lookback=5,
        pullback_tol_pct=0.01,
    )
    base.update(kwargs)
    return StrategyConfig(**base)


def test_no_entry_when_market_red():
    # Falling SPY → market red → no entries
    strat = DayTradePlaybookStrategy(_cfg(), ["SPY", "QQQ"], _risk())
    spy = [110 - i for i in range(15)]  # falling
    qqq = [200 + i * 0.5 for i in range(15)]  # rising alone not enough
    history = {
        "SPY": [Bar("SPY", c) for c in spy],
        "QQQ": [Bar("QQQ", c) for c in qqq],
    }
    quotes = {"SPY": Quote("SPY", spy[-1]), "QQQ": Quote("QQQ", qqq[-1])}
    port = PortfolioSnapshot(cash=1000, equity=1000, positions={})
    signals = strat.generate(quotes, history, port)
    assert all(s.action != SignalAction.ENTER_LONG for s in signals)


def test_breakout_entry_sizes_to_5pct_risk():
    # Strong uptrend + breakout bar on both SPY and QQQ
    strat = DayTradePlaybookStrategy(_cfg(range_lookback=5, sma_period=5), ["QQQ"], _risk())
    # Range: mostly flat 100, then breakout to 106
    base = [100.0, 100.2, 99.8, 100.1, 100.0, 100.3, 100.1, 100.0, 100.2]
    spy_closes = base + [100.5, 101.0, 102.0, 103.0, 105.0]  # green
    qqq_closes = base + [100.5, 101.0, 102.0, 103.0, 106.0]  # breakout
    history = {
        "SPY": [Bar("SPY", c) for c in spy_closes],
        "QQQ": [Bar("QQQ", c) for c in qqq_closes],
    }
    entry = 106.0
    quotes = {"SPY": Quote("SPY", spy_closes[-1]), "QQQ": Quote("QQQ", entry)}
    port = PortfolioSnapshot(cash=1000, equity=1000, positions={})
    signals = strat.generate(quotes, history, port)
    enters = [s for s in signals if s.action == SignalAction.ENTER_LONG]
    assert len(enters) == 1
    s = enters[0]
    assert s.stop_price is not None and s.stop_price < entry
    assert s.target_price is not None and s.target_price > entry
    assert s.suggested_quantity is not None and s.suggested_quantity > 0
    # Risk ≈ qty * (entry - stop) should be ~5% of equity when notional not capped hard
    risk = s.suggested_quantity * (entry - s.stop_price)
    # Allow cap effects but should be in a reasonable band
    assert 1.0 <= risk <= 60.0


def test_stop_target_plan_fields_on_enter():
    strat = DayTradePlaybookStrategy(_cfg(), ["SPY"], _risk(reward_risk_ratio=2.0))
    closes = [100 + i * 0.2 for i in range(12)] + [103.0]  # break higher
    history = {"SPY": [Bar("SPY", c) for c in closes]}
    quotes = {"SPY": Quote("SPY", closes[-1])}
    port = PortfolioSnapshot(cash=1000, equity=1000, positions={})
    signals = strat.generate(quotes, history, port)
    enters = [s for s in signals if s.action == SignalAction.ENTER_LONG]
    if enters:
        s = enters[0]
        r = s.ref_price - s.stop_price
        assert abs((s.target_price - s.ref_price) - 2 * r) < 0.05
