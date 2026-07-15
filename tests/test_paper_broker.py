from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from agentic_trading.broker.paper import PaperBroker, next_business_day
from agentic_trading.models import OrderIntent, Side


def test_buy_and_sell_roundtrip_with_settlement():
    b = PaperBroker(1000.0, settlement_days=1)
    fill = b.execute(
        OrderIntent(symbol="SPY", side=Side.BUY, notional=100.0),
        ref_price=50.0,
    )
    assert fill is not None
    assert fill.quantity == 2.0
    snap = b.snapshot()
    assert snap.settled_cash == 900.0
    assert snap.unsettled_cash == 0.0
    assert snap.positions["SPY"].quantity == 2.0
    assert snap.orders_today == 1

    fill2 = b.execute(
        OrderIntent(symbol="SPY", side=Side.SELL, quantity=2.0),
        ref_price=55.0,
    )
    assert fill2 is not None
    snap2 = b.snapshot()
    assert "SPY" not in snap2.positions
    # Proceeds unsettled until next business day
    assert snap2.settled_cash == 900.0
    assert abs(snap2.unsettled_cash - 110.0) < 1e-9
    assert abs(snap2.cash - 1010.0) < 1e-9
    assert snap2.realized_pnl_today == 10.0
    # Cannot redeploy unsettled proceeds same day
    fill3 = b.execute(
        OrderIntent(symbol="QQQ", side=Side.BUY, notional=1000.0),
        ref_price=100.0,
    )
    assert fill3 is None  # only 900 settled


def test_settlement_releases_on_next_business_day(tmp_path: Path):
    state = tmp_path / "state.json"
    day0 = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)  # Tuesday
    b = PaperBroker(1000.0, settlement_days=1, state_path=state, now=day0)
    b.execute(OrderIntent("SPY", Side.BUY, notional=100.0), 50.0)
    b.execute(OrderIntent("SPY", Side.SELL, quantity=2.0), 55.0)
    assert b.settled_cash == 900.0
    assert b.unsettled_cash == 110.0
    assert b.pending[0].settle_on == date(2026, 7, 15)

    # Simulate Wednesday open
    day1 = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    b.set_now(day1)
    assert abs(b.settled_cash - 1010.0) < 1e-9
    assert b.unsettled_cash == 0.0
    assert b.orders_today == 0  # daily reset


def test_next_business_day_skips_weekend():
    friday = date(2026, 7, 17)
    assert next_business_day(friday, 1) == date(2026, 7, 20)  # Monday


def test_insufficient_settled_cash_returns_none():
    b = PaperBroker(50.0, settlement_days=1)
    fill = b.execute(
        OrderIntent(symbol="SPY", side=Side.BUY, notional=100.0),
        ref_price=50.0,
    )
    assert fill is None


def test_halted_broker_refuses_buys_allows_sells():
    b = PaperBroker(1000.0, settlement_days=1)
    b.execute(OrderIntent("SPY", Side.BUY, notional=100.0), 50.0)
    b.set_halt("test halt")
    buy = b.execute(OrderIntent("QQQ", Side.BUY, notional=50.0), 50.0)
    assert buy is None
    sell = b.execute(OrderIntent("SPY", Side.SELL, quantity=2.0), 50.0)
    assert sell is not None
