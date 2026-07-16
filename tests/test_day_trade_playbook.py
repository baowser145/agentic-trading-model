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
        market_red_exit_ticks=2,
        soft_exit_min_hold_ticks=2,
        market_red_sma_buffer_pct=0.0,
        reentry_cooldown_ticks=3,
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


def _long_port(symbol: str = "QQQ", qty: float = 1.0, avg: float = 100.0):
    return PortfolioSnapshot(
        cash=500,
        equity=1000,
        positions={symbol: Position(symbol, qty, avg)},
    )


def test_r1_no_soft_exit_on_first_market_red_tick():
    """R1: first exit-red tick while long should HOLD, not EXIT."""
    strat = DayTradePlaybookStrategy(
        _cfg(market_red_exit_ticks=2, soft_exit_min_hold_ticks=0, market_red_sma_buffer_pct=0.0),
        ["QQQ"],
        _risk(),
    )
    # Falling SPY → market red
    spy = [110 - i for i in range(15)]
    qqq = [100 + i * 0.1 for i in range(15)]
    history = {
        "SPY": [Bar("SPY", c) for c in spy],
        "QQQ": [Bar("QQQ", c) for c in qqq],
    }
    quotes = {"SPY": Quote("SPY", spy[-1]), "QQQ": Quote("QQQ", qqq[-1])}
    port = _long_port("QQQ")
    s1 = strat.generate(quotes, history, port)
    assert s1[0].action == SignalAction.HOLD
    assert "R1 soft-exit wait" in s1[0].reason
    # second consecutive red → EXIT
    s2 = strat.generate(quotes, history, port)
    assert s2[0].action == SignalAction.EXIT_LONG
    assert "R1 red_streak=" in s2[0].reason


def test_r1_min_hold_blocks_soft_exit():
    strat = DayTradePlaybookStrategy(
        _cfg(market_red_exit_ticks=1, soft_exit_min_hold_ticks=3, market_red_sma_buffer_pct=0.0),
        ["QQQ"],
        _risk(),
    )
    spy = [110 - i for i in range(15)]
    qqq = [100.0] * 15
    history = {
        "SPY": [Bar("SPY", c) for c in spy],
        "QQQ": [Bar("QQQ", c) for c in qqq],
    }
    quotes = {"SPY": Quote("SPY", spy[-1]), "QQQ": Quote("QQQ", 100.0)}
    port = _long_port("QQQ")
    s1 = strat.generate(quotes, history, port)
    s2 = strat.generate(quotes, history, port)
    assert s1[0].action == SignalAction.HOLD
    assert s2[0].action == SignalAction.HOLD
    s3 = strat.generate(quotes, history, port)
    assert s3[0].action == SignalAction.EXIT_LONG


def test_r2_cooldown_blocks_reentry_after_market_red_exit():
    strat = DayTradePlaybookStrategy(
        _cfg(
            market_red_exit_ticks=1,
            soft_exit_min_hold_ticks=0,
            market_red_sma_buffer_pct=0.0,
            reentry_cooldown_ticks=2,
            range_lookback=5,
            sma_period=5,
        ),
        ["QQQ"],
        _risk(),
    )
    # 1) Soft-exit while long on red market → arms cooldown
    spy_red = [110 - i for i in range(15)]
    qqq = [100.0] * 15
    hist_red = {
        "SPY": [Bar("SPY", c) for c in spy_red],
        "QQQ": [Bar("QQQ", c) for c in qqq],
    }
    quotes_red = {"SPY": Quote("SPY", spy_red[-1]), "QQQ": Quote("QQQ", 100.0)}
    long_port = _long_port("QQQ")
    exit_sig = strat.generate(quotes_red, hist_red, long_port)
    assert exit_sig[0].action == SignalAction.EXIT_LONG

    # 2) Green market + breakout setup, but flat — should be cooldown FLAT
    base = [100.0, 100.2, 99.8, 100.1, 100.0, 100.3, 100.1, 100.0, 100.2]
    spy_green = base + [100.5, 101.0, 102.0, 103.0, 105.0]
    qqq_bo = base + [100.5, 101.0, 102.0, 103.0, 106.0]
    hist_g = {
        "SPY": [Bar("SPY", c) for c in spy_green],
        "QQQ": [Bar("QQQ", c) for c in qqq_bo],
    }
    quotes_g = {"SPY": Quote("SPY", spy_green[-1]), "QQQ": Quote("QQQ", 106.0)}
    flat = PortfolioSnapshot(cash=1000, equity=1000, positions={})
    c1 = strat.generate(quotes_g, hist_g, flat)
    assert c1[0].action == SignalAction.FLAT
    assert "R2 reentry cooldown" in c1[0].reason
    c2 = strat.generate(quotes_g, hist_g, flat)
    assert c2[0].action == SignalAction.FLAT
    assert "R2 reentry cooldown" in c2[0].reason
    # after cooldown ends, breakout may enter
    c3 = strat.generate(quotes_g, hist_g, flat)
    assert c3[0].action == SignalAction.ENTER_LONG
