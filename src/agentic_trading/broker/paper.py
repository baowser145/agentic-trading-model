from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from agentic_trading.broker.base import Broker
from agentic_trading.models import (
    Fill,
    OrderIntent,
    PortfolioSnapshot,
    Position,
    Side,
    TradingMode,
)


def next_business_day(d: date, days: int = 1) -> date:
    """Advance `days` business days (skips Sat/Sun)."""
    out = d
    remaining = max(1, days)
    while remaining > 0:
        out += timedelta(days=1)
        if out.weekday() < 5:  # Mon-Fri
            remaining -= 1
    return out


@dataclass
class PendingSettlement:
    amount: float
    settle_on: date  # cash becomes settled on this calendar date


class PaperBroker(Broker):
    """
    Simulated fills at ref_price with T+N cash settlement.

    - Buys debit settled cash only (buying power).
    - Sells credit unsettled cash; funds settle after `settlement_days`
      business day(s) (default 1 — proceeds not reusable same day).
    """

    def __init__(
        self,
        starting_equity: float,
        settlement_days: int = 1,
        state_path: Path | None = None,
        now: datetime | None = None,
    ) -> None:
        self.settlement_days = max(0, int(settlement_days))
        self.state_path = Path(state_path) if state_path else None
        self.settled_cash = float(starting_equity)
        self.pending: list[PendingSettlement] = []
        self.positions: dict[str, Position] = {}
        self.realized_pnl_today = 0.0
        self.orders_today = 0
        self.starting_equity_today = float(starting_equity)
        self.halted = False
        self.halt_reason: str | None = None
        self._last_prices: dict[str, float] = {}
        self._current_day: date | None = None
        self._now_override: datetime | None = now
        if self.state_path and self.state_path.is_file():
            self._load_state()
        # Apply settlement/day roll for "now"
        self._sync_clock(self._now())

    @property
    def unsettled_cash(self) -> float:
        return sum(p.amount for p in self.pending)

    @property
    def cash(self) -> float:
        return self.settled_cash + self.unsettled_cash

    def _now(self) -> datetime:
        if self._now_override is not None:
            return self._now_override
        return datetime.now(timezone.utc)

    def set_now(self, now: datetime) -> None:
        """Advance simulated clock (tests / calendar control)."""
        self._now_override = now
        self._sync_clock(now)

    def _sync_clock(self, now: datetime) -> None:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        today = now.date()
        if self._current_day is None:
            self._current_day = today
        elif today > self._current_day:
            # New trading day: reset daily counters
            self.orders_today = 0
            self.realized_pnl_today = 0.0
            self.starting_equity_today = self._equity()
            # Clear soft halt overnight so a new day can trade (daily loss is re-evaluated)
            if self.halted and self.halt_reason and "max_daily_loss" in self.halt_reason:
                self.halted = False
                self.halt_reason = None
            self._current_day = today
        self._settle_due(today)
        self._save_state()

    def _settle_due(self, today: date) -> None:
        still: list[PendingSettlement] = []
        settled_amt = 0.0
        for p in self.pending:
            if p.settle_on <= today:
                settled_amt += p.amount
            else:
                still.append(p)
        if settled_amt:
            self.settled_cash += settled_amt
        self.pending = still

    def snapshot(self) -> PortfolioSnapshot:
        equity = self._equity()
        return PortfolioSnapshot(
            cash=self.cash,
            equity=equity,
            positions=dict(self.positions),
            realized_pnl_today=self.realized_pnl_today,
            orders_today=self.orders_today,
            starting_equity_today=self.starting_equity_today,
            halted=self.halted,
            halt_reason=self.halt_reason,
            settled_cash=self.settled_cash,
            unsettled_cash=self.unsettled_cash,
        )

    def _equity(self) -> float:
        mv = 0.0
        for sym, pos in self.positions.items():
            px = self._last_prices.get(sym, pos.avg_cost)
            mv += pos.quantity * px
        return self.cash + mv

    def mark_to_market(self, prices: dict[str, float]) -> PortfolioSnapshot:
        self._last_prices.update(prices)
        self._sync_clock(self._now())
        return self.snapshot()

    def set_halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason
        self._save_state()

    def execute(self, intent: OrderIntent, ref_price: float) -> Fill | None:
        self._sync_clock(self._now())
        if ref_price <= 0:
            return None
        # Halt freezes new buys only; sells still allowed
        if self.halted and intent.side == Side.BUY:
            return None

        if intent.side == Side.BUY:
            return self._buy(intent, ref_price)
        return self._sell(intent, ref_price)

    def _buy(self, intent: OrderIntent, ref_price: float) -> Fill | None:
        notional = intent.notional
        if notional is None and intent.quantity is not None:
            notional = intent.quantity * ref_price
        if notional is None or notional <= 0:
            return None
        # Only settled cash can fund buys (T+1)
        if notional > self.settled_cash + 1e-9:
            return None
        qty = notional / ref_price
        self.settled_cash -= notional
        existing = self.positions.get(intent.symbol)
        if existing:
            new_qty = existing.quantity + qty
            new_cost = (
                (existing.avg_cost * existing.quantity) + notional
            ) / new_qty
            self.positions[intent.symbol] = Position(
                intent.symbol, new_qty, new_cost
            )
        else:
            self.positions[intent.symbol] = Position(intent.symbol, qty, ref_price)
        self.orders_today += 1
        self._last_prices[intent.symbol] = ref_price
        self._save_state()
        return Fill(
            symbol=intent.symbol,
            side=Side.BUY,
            quantity=qty,
            price=ref_price,
            notional=notional,
            ts=self._now(),
            mode=TradingMode.PAPER,
            order_id=f"paper-{uuid.uuid4().hex[:10]}",
        )

    def _sell(self, intent: OrderIntent, ref_price: float) -> Fill | None:
        qty = intent.quantity
        if qty is None and intent.notional is not None:
            qty = intent.notional / ref_price
        if qty is None or qty <= 0:
            return None
        existing = self.positions.get(intent.symbol)
        if not existing or existing.quantity + 1e-12 < qty:
            return None
        proceeds = qty * ref_price
        cost_basis = qty * existing.avg_cost
        self.realized_pnl_today += proceeds - cost_basis
        remaining = existing.quantity - qty
        if remaining <= 1e-12:
            del self.positions[intent.symbol]
        else:
            self.positions[intent.symbol] = Position(
                intent.symbol, remaining, existing.avg_cost
            )
        # Proceeds unsettled for settlement_days business days
        today = self._current_day or datetime.now(timezone.utc).date()
        if self.settlement_days <= 0:
            self.settled_cash += proceeds
        else:
            settle_on = next_business_day(today, self.settlement_days)
            self.pending.append(PendingSettlement(proceeds, settle_on))
        self.orders_today += 1
        self._last_prices[intent.symbol] = ref_price
        self._save_state()
        return Fill(
            symbol=intent.symbol,
            side=Side.SELL,
            quantity=qty,
            price=ref_price,
            notional=proceeds,
            ts=self._now(),
            mode=TradingMode.PAPER,
            order_id=f"paper-{uuid.uuid4().hex[:10]}",
        )

    def _save_state(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "settled_cash": self.settled_cash,
            "pending": [
                {"amount": p.amount, "settle_on": p.settle_on.isoformat()}
                for p in self.pending
            ],
            "positions": {
                sym: {"quantity": pos.quantity, "avg_cost": pos.avg_cost}
                for sym, pos in self.positions.items()
            },
            "realized_pnl_today": self.realized_pnl_today,
            "orders_today": self.orders_today,
            "starting_equity_today": self.starting_equity_today,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "last_prices": self._last_prices,
            "current_day": self._current_day.isoformat() if self._current_day else None,
            "settlement_days": self.settlement_days,
        }
        self.state_path.write_text(json.dumps(payload, indent=2))

    def _load_state(self) -> None:
        if not self.state_path or not self.state_path.is_file():
            return
        data = json.loads(self.state_path.read_text())
        self.settled_cash = float(data.get("settled_cash", self.settled_cash))
        self.pending = [
            PendingSettlement(
                amount=float(p["amount"]),
                settle_on=date.fromisoformat(p["settle_on"]),
            )
            for p in data.get("pending") or []
        ]
        self.positions = {
            sym: Position(sym, float(v["quantity"]), float(v["avg_cost"]))
            for sym, v in (data.get("positions") or {}).items()
        }
        self.realized_pnl_today = float(data.get("realized_pnl_today", 0.0))
        self.orders_today = int(data.get("orders_today", 0))
        self.starting_equity_today = float(
            data.get("starting_equity_today", self.starting_equity_today)
        )
        self.halted = bool(data.get("halted", False))
        self.halt_reason = data.get("halt_reason")
        self._last_prices = {
            k: float(v) for k, v in (data.get("last_prices") or {}).items()
        }
        cd = data.get("current_day")
        self._current_day = date.fromisoformat(cd) if cd else None
        if "settlement_days" in data:
            self.settlement_days = int(data["settlement_days"])
