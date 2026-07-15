from datetime import datetime, timezone
from pathlib import Path

from agentic_trading.journal import TradeJournal
from agentic_trading.models import Fill, Side, TradingMode


def test_journal_roundtrip(tmp_path: Path):
    j = TradeJournal(tmp_path)
    buy = Fill(
        symbol="AAPL",
        side=Side.BUY,
        quantity=10,
        price=100.0,
        notional=1000.0,
        ts=datetime(2026, 7, 14, 14, 0, tzinfo=timezone.utc),
        mode=TradingMode.PAPER,
        order_id="b1",
    )
    j.record_fill(buy, reason="breakout", stop=98.0, target=104.0)
    assert "AAPL" in j.summary()["open_symbols"]

    sell = Fill(
        symbol="AAPL",
        side=Side.SELL,
        quantity=10,
        price=104.0,
        notional=1040.0,
        ts=datetime(2026, 7, 14, 15, 0, tzinfo=timezone.utc),
        mode=TradingMode.PAPER,
        order_id="s1",
    )
    closed = j.record_fill(sell, exit_reason="TARGET hit")
    assert closed is not None
    assert abs(closed.pnl - 40.0) < 1e-9
    assert closed.r_multiple is not None
    assert abs(closed.r_multiple - 2.0) < 1e-6
    s = j.summary()
    assert s["closed_trades"] == 1
    assert s["wins"] == 1
    assert (tmp_path / "trades.csv").is_file()
    assert (tmp_path / "fills.jsonl").is_file()
