from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_trading.models import Fill, Side, TradingMode


@dataclass
class OpenLot:
    symbol: str
    quantity: float
    entry_price: float
    entry_ts: str
    entry_order_id: str
    entry_reason: str = ""
    stop: float | None = None
    target: float | None = None
    mode: str = TradingMode.PAPER.value


@dataclass
class ClosedTrade:
    trade_id: str
    symbol: str
    quantity: float
    entry_price: float
    exit_price: float
    entry_ts: str
    exit_ts: str
    entry_order_id: str
    exit_order_id: str
    pnl: float
    pnl_pct: float
    hold_seconds: float
    entry_reason: str = ""
    exit_reason: str = ""
    stop: float | None = None
    target: float | None = None
    mode: str = TradingMode.PAPER.value
    r_multiple: float | None = None  # pnl / risk if stop known

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TradeJournal:
    """
    Track fills → open lots → closed trades for later backtest analysis.

    Files (under logs/ by default):
      - fills.jsonl      every fill
      - trades.jsonl     closed round-trips
      - trades.csv       same closed trades for spreadsheets
      - open_lots.json   current open lots
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.fills_path = self.directory / "fills.jsonl"
        self.trades_jsonl = self.directory / "trades.jsonl"
        self.trades_csv = self.directory / "trades.csv"
        self.open_path = self.directory / "open_lots.json"
        self._open: dict[str, OpenLot] = {}
        self._trade_seq = 0
        self._load_open()
        self._trade_seq = self._count_closed()

    def _count_closed(self) -> int:
        if not self.trades_jsonl.is_file():
            return 0
        return sum(1 for _ in self.trades_jsonl.open() if _.strip())

    def _load_open(self) -> None:
        if not self.open_path.is_file():
            return
        try:
            raw = json.loads(self.open_path.read_text())
            for sym, d in (raw or {}).items():
                self._open[sym] = OpenLot(
                    symbol=sym,
                    quantity=float(d["quantity"]),
                    entry_price=float(d["entry_price"]),
                    entry_ts=str(d["entry_ts"]),
                    entry_order_id=str(d.get("entry_order_id", "")),
                    entry_reason=str(d.get("entry_reason", "")),
                    stop=d.get("stop"),
                    target=d.get("target"),
                    mode=str(d.get("mode", TradingMode.PAPER.value)),
                )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            self._open = {}

    def _save_open(self) -> None:
        payload = {
            sym: {
                "quantity": lot.quantity,
                "entry_price": lot.entry_price,
                "entry_ts": lot.entry_ts,
                "entry_order_id": lot.entry_order_id,
                "entry_reason": lot.entry_reason,
                "stop": lot.stop,
                "target": lot.target,
                "mode": lot.mode,
            }
            for sym, lot in self._open.items()
        }
        self.open_path.write_text(json.dumps(payload, indent=2))

    def record_fill(
        self,
        fill: Fill,
        *,
        reason: str = "",
        stop: float | None = None,
        target: float | None = None,
        exit_reason: str = "",
    ) -> ClosedTrade | None:
        """Append fill; on SELL against an open lot, close a trade and return it."""
        fill_rec = {
            "ts": fill.ts.isoformat(),
            "symbol": fill.symbol,
            "side": fill.side.value,
            "quantity": fill.quantity,
            "price": fill.price,
            "notional": fill.notional,
            "order_id": fill.order_id,
            "mode": fill.mode.value,
            "reason": reason or exit_reason,
            "stop": stop,
            "target": target,
        }
        with self.fills_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fill_rec) + "\n")

        closed: ClosedTrade | None = None
        if fill.side == Side.BUY:
            existing = self._open.get(fill.symbol)
            if existing:
                # Average up
                tot_q = existing.quantity + fill.quantity
                avg = (
                    existing.entry_price * existing.quantity
                    + fill.price * fill.quantity
                ) / tot_q
                existing.quantity = tot_q
                existing.entry_price = avg
                if stop is not None:
                    existing.stop = stop
                if target is not None:
                    existing.target = target
                if reason:
                    existing.entry_reason = reason
            else:
                self._open[fill.symbol] = OpenLot(
                    symbol=fill.symbol,
                    quantity=fill.quantity,
                    entry_price=fill.price,
                    entry_ts=fill.ts.isoformat(),
                    entry_order_id=fill.order_id,
                    entry_reason=reason,
                    stop=stop,
                    target=target,
                    mode=fill.mode.value,
                )
        else:
            lot = self._open.get(fill.symbol)
            if lot and lot.quantity > 0:
                qty = min(lot.quantity, fill.quantity)
                pnl = (fill.price - lot.entry_price) * qty
                pnl_pct = (
                    (fill.price / lot.entry_price - 1.0) * 100.0
                    if lot.entry_price
                    else 0.0
                )
                try:
                    t0 = datetime.fromisoformat(lot.entry_ts)
                    if t0.tzinfo is None:
                        t0 = t0.replace(tzinfo=timezone.utc)
                    hold = (fill.ts - t0).total_seconds()
                except ValueError:
                    hold = 0.0
                r_mult = None
                if lot.stop is not None and lot.entry_price > lot.stop:
                    risk = lot.entry_price - lot.stop
                    if risk > 0:
                        r_mult = (fill.price - lot.entry_price) / risk

                self._trade_seq += 1
                closed = ClosedTrade(
                    trade_id=f"T{self._trade_seq:05d}",
                    symbol=fill.symbol,
                    quantity=qty,
                    entry_price=lot.entry_price,
                    exit_price=fill.price,
                    entry_ts=lot.entry_ts,
                    exit_ts=fill.ts.isoformat(),
                    entry_order_id=lot.entry_order_id,
                    exit_order_id=fill.order_id,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    hold_seconds=hold,
                    entry_reason=lot.entry_reason,
                    exit_reason=exit_reason or reason,
                    stop=lot.stop,
                    target=lot.target,
                    mode=fill.mode.value,
                    r_multiple=r_mult,
                )
                with self.trades_jsonl.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(closed.to_dict()) + "\n")
                self._append_csv(closed)

                remaining = lot.quantity - qty
                if remaining <= 1e-12:
                    del self._open[fill.symbol]
                else:
                    lot.quantity = remaining

        self._save_open()
        return closed

    def _append_csv(self, trade: ClosedTrade) -> None:
        new_file = not self.trades_csv.is_file()
        fieldnames = list(trade.to_dict().keys())
        with self.trades_csv.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if new_file:
                w.writeheader()
            w.writerow(trade.to_dict())

    def summary(self) -> dict[str, Any]:
        trades: list[dict] = []
        if self.trades_jsonl.is_file():
            for line in self.trades_jsonl.read_text().splitlines():
                if line.strip():
                    trades.append(json.loads(line))
        wins = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) <= 0]
        total_pnl = sum(t.get("pnl", 0) for t in trades)
        return {
            "closed_trades": len(trades),
            "open_lots": len(self._open),
            "open_symbols": list(self._open.keys()),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(trades)) if trades else None,
            "total_pnl": total_pnl,
            "avg_pnl": (total_pnl / len(trades)) if trades else None,
            "fills_path": str(self.fills_path),
            "trades_jsonl": str(self.trades_jsonl),
            "trades_csv": str(self.trades_csv),
            "recent": trades[-10:],
        }
