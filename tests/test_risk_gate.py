from agentic_trading.config import RiskConfig
from agentic_trading.models import (
    OrderIntent,
    PortfolioSnapshot,
    Position,
    Side,
    Signal,
    SignalAction,
)
from agentic_trading.risk.gate import RiskGate


def _risk(**kwargs) -> RiskGate:
    defaults = dict(
        max_position_pct=0.20,
        max_open_positions=3,
        max_orders_per_day=10,
        max_daily_loss_pct=0.02,
        max_order_notional=100.0,
    )
    defaults.update(kwargs)
    return RiskGate(RiskConfig(**defaults))


def _portfolio(**kwargs) -> PortfolioSnapshot:
    base = dict(
        cash=1000.0,
        equity=1000.0,
        positions={},
        realized_pnl_today=0.0,
        orders_today=0,
        starting_equity_today=1000.0,
        halted=False,
        halt_reason=None,
    )
    base.update(kwargs)
    return PortfolioSnapshot(**base)


def test_approve_buy_within_limits():
    gate = _risk()
    intent = OrderIntent(symbol="SPY", side=Side.BUY, notional=100.0)
    d = gate.approve(intent, _portfolio(), ref_price=500.0)
    assert d.approved is True


def test_reject_over_max_order_notional():
    gate = _risk(max_order_notional=50.0)
    intent = OrderIntent(symbol="SPY", side=Side.BUY, notional=100.0)
    d = gate.approve(intent, _portfolio(), ref_price=500.0)
    assert d.approved is False
    assert "max_order_notional" in d.reason


def test_reject_max_orders_per_day():
    gate = _risk(max_orders_per_day=2)
    intent = OrderIntent(symbol="SPY", side=Side.BUY, notional=50.0)
    d = gate.approve(intent, _portfolio(orders_today=2), ref_price=500.0)
    assert d.approved is False
    assert "max_orders_per_day" in d.reason


def test_max_orders_still_allows_sell():
    gate = _risk(max_orders_per_day=2)
    port = _portfolio(
        orders_today=2,
        positions={"SPY": Position("SPY", 1.0, 100.0)},
    )
    intent = OrderIntent(symbol="SPY", side=Side.SELL, quantity=1.0)
    d = gate.approve(intent, port, ref_price=90.0)
    assert d.approved is True


def test_halt_on_daily_loss():
    gate = _risk(max_daily_loss_pct=0.02)
    port = _portfolio(equity=970.0, starting_equity_today=1000.0)
    port = gate.evaluate_portfolio_halt(port)
    assert port.halted is True
    intent = OrderIntent(symbol="SPY", side=Side.BUY, notional=50.0)
    d = gate.approve(intent, port, ref_price=500.0)
    assert d.approved is False
    assert d.halted is True


def test_halt_still_allows_sell():
    gate = _risk(max_daily_loss_pct=0.02)
    port = _portfolio(
        equity=970.0,
        starting_equity_today=1000.0,
        positions={"SPY": Position("SPY", 1.0, 100.0)},
        halted=True,
        halt_reason="max_daily_loss",
    )
    intent = OrderIntent(symbol="SPY", side=Side.SELL, quantity=1.0)
    d = gate.approve(intent, port, ref_price=90.0)
    assert d.approved is True


def test_reject_max_open_positions():
    gate = _risk(max_open_positions=1)
    port = _portfolio(
        positions={"QQQ": Position("QQQ", 1.0, 400.0)},
        cash=500.0,
        equity=900.0,
        starting_equity_today=900.0,  # avoid daily-loss halt masking this check
    )
    intent = OrderIntent(symbol="SPY", side=Side.BUY, notional=50.0)
    d = gate.approve(intent, port, ref_price=500.0)
    assert d.approved is False
    assert "max_open_positions" in d.reason


def test_sell_reduces_risk_when_held():
    gate = _risk()
    port = _portfolio(
        positions={"SPY": Position("SPY", 2.0, 100.0)},
        cash=800.0,
        equity=1000.0,
    )
    intent = OrderIntent(symbol="SPY", side=Side.SELL, quantity=2.0)
    d = gate.approve(intent, port, ref_price=110.0)
    assert d.approved is True


def test_same_tick_max_open_positions():
    """Multiple ENTER signals in one batch must not exceed max_open_positions."""
    gate = _risk(max_open_positions=1, max_order_notional=100.0)
    port = _portfolio(cash=1000.0, equity=1000.0)
    signals = [
        Signal("SPY", SignalAction.ENTER_LONG, 0.5, "a", 100.0),
        Signal("QQQ", SignalAction.ENTER_LONG, 0.5, "b", 100.0),
        Signal("IWM", SignalAction.ENTER_LONG, 0.5, "c", 100.0),
    ]
    decisions = gate.process_signals(signals, port)
    approved = [d for d in decisions if d.approved]
    assert len(approved) == 1
    assert approved[0].intent is not None
    assert approved[0].intent.symbol == "SPY"
    rejected = [d for d in decisions if not d.approved and d.intent is not None]
    assert any("max_open_positions" in d.reason for d in rejected)


def test_same_tick_max_orders_per_day():
    gate = _risk(max_orders_per_day=1, max_open_positions=5)
    port = _portfolio()
    signals = [
        Signal("SPY", SignalAction.ENTER_LONG, 0.5, "a", 100.0),
        Signal("QQQ", SignalAction.ENTER_LONG, 0.5, "b", 100.0),
    ]
    decisions = gate.process_signals(signals, port)
    assert sum(1 for d in decisions if d.approved) == 1
    assert any("max_orders_per_day" in d.reason for d in decisions if not d.approved)


def test_same_tick_cash_reservation():
    gate = _risk(max_order_notional=100.0, max_open_positions=5, max_position_pct=0.5)
    # Only enough settled cash for one full $100 order
    port = _portfolio(cash=100.0, equity=1000.0, settled_cash=100.0)
    signals = [
        Signal("SPY", SignalAction.ENTER_LONG, 0.5, "a", 100.0),
        Signal("QQQ", SignalAction.ENTER_LONG, 0.5, "b", 100.0),
    ]
    decisions = gate.process_signals(signals, port)
    approved = [d for d in decisions if d.approved]
    assert len(approved) == 1
    # Second signal either no intent (buying_power < $1) or settled-cash reject
    assert any(not d.approved for d in decisions)


def test_available_cash_funds_buy_immediately():
    """Default: unsettled proceeds still count as available to trade."""
    gate = _risk(max_order_notional=200.0)
    port = _portfolio(
        cash=1000.0,
        equity=1000.0,
        settled_cash=50.0,
        unsettled_cash=950.0,
        trade_when_cash_available=True,
    )
    intent = OrderIntent(symbol="SPY", side=Side.BUY, notional=100.0)
    d = gate.approve(intent, port, ref_price=100.0)
    assert d.approved is True


def test_strict_settled_only_blocks_unsettled():
    gate = _risk(max_order_notional=200.0)
    port = _portfolio(
        cash=1000.0,
        equity=1000.0,
        settled_cash=50.0,
        unsettled_cash=950.0,
        trade_when_cash_available=False,
    )
    intent = OrderIntent(symbol="SPY", side=Side.BUY, notional=100.0)
    d = gate.approve(intent, port, ref_price=100.0)
    assert d.approved is False
    assert "settled cash" in d.reason


def test_signal_to_intent_enter_and_exit():
    gate = _risk()
    port = _portfolio()
    enter = Signal(
        symbol="SPY",
        action=SignalAction.ENTER_LONG,
        strength=0.5,
        reason="up",
        ref_price=500.0,
    )
    intent = gate.signal_to_intent(enter, port)
    assert intent is not None
    assert intent.side == Side.BUY

    port2 = _portfolio(positions={"SPY": Position("SPY", 1.0, 500.0)})
    exit_sig = Signal(
        symbol="SPY",
        action=SignalAction.EXIT_LONG,
        strength=0.5,
        reason="down",
        ref_price=490.0,
    )
    intent2 = gate.signal_to_intent(exit_sig, port2)
    assert intent2 is not None
    assert intent2.side == Side.SELL
