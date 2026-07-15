from agentic_trading.broker.paper import PaperBroker
from agentic_trading.models import OrderIntent, Side


def test_buy_and_sell_roundtrip():
    b = PaperBroker(1000.0)
    fill = b.execute(
        OrderIntent(symbol="SPY", side=Side.BUY, notional=100.0),
        ref_price=50.0,
    )
    assert fill is not None
    assert fill.quantity == 2.0
    snap = b.snapshot()
    assert snap.cash == 900.0
    assert snap.positions["SPY"].quantity == 2.0
    assert snap.orders_today == 1

    fill2 = b.execute(
        OrderIntent(symbol="SPY", side=Side.SELL, quantity=2.0),
        ref_price=55.0,
    )
    assert fill2 is not None
    snap2 = b.snapshot()
    assert "SPY" not in snap2.positions
    assert snap2.cash == 900.0 + 110.0
    assert snap2.realized_pnl_today == 10.0


def test_insufficient_cash_returns_none():
    b = PaperBroker(50.0)
    fill = b.execute(
        OrderIntent(symbol="SPY", side=Side.BUY, notional=100.0),
        ref_price=50.0,
    )
    assert fill is None


def test_halted_broker_refuses_fills():
    b = PaperBroker(1000.0)
    b.set_halt("test halt")
    fill = b.execute(
        OrderIntent(symbol="SPY", side=Side.BUY, notional=100.0),
        ref_price=50.0,
    )
    assert fill is None
