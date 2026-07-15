from __future__ import annotations

import uuid
from datetime import datetime, timezone

from agentic_trading.broker.base import Broker
from agentic_trading.models import (
    Fill,
    OrderIntent,
    PortfolioSnapshot,
    Position,
    Side,
    TradingMode,
)


class PaperBroker(Broker):
    """Simulated fills at ref_price. Tracks cash, positions, daily counters."""

    def __init__(self, starting_equity: float) -> None:
        self.cash = float(starting_equity)
        self.positions: dict[str, Position] = {}
        self.realized_pnl_today = 0.0
        self.orders_today = 0
        self.starting_equity_today = float(starting_equity)
        self.halted = False
        self.halt_reason: str | None = None
        self._last_prices: dict[str, float] = {}

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
        )

    def _equity(self) -> float:
        mv = 0.0
        for sym, pos in self.positions.items():
            px = self._last_prices.get(sym, pos.avg_cost)
            mv += pos.quantity * px
        return self.cash + mv

    def mark_to_market(self, prices: dict[str, float]) -> PortfolioSnapshot:
        self._last_prices.update(prices)
        return self.snapshot()

    def set_halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason

    def execute(self, intent: OrderIntent, ref_price: float) -> Fill | None:
        if ref_price <= 0:
            return None
        if self.halted:
            return None

        if intent.side == Side.BUY:
            notional = intent.notional
            if notional is None and intent.quantity is not None:
                notional = intent.quantity * ref_price
            if notional is None or notional <= 0:
                return None
            if notional > self.cash + 1e-9:
                return None
            qty = notional / ref_price
            self.cash -= notional
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
            return Fill(
                symbol=intent.symbol,
                side=Side.BUY,
                quantity=qty,
                price=ref_price,
                notional=notional,
                ts=datetime.now(timezone.utc),
                mode=TradingMode.PAPER,
                order_id=f"paper-{uuid.uuid4().hex[:10]}",
            )

        # SELL
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
        self.cash += proceeds
        remaining = existing.quantity - qty
        if remaining <= 1e-12:
            del self.positions[intent.symbol]
        else:
            self.positions[intent.symbol] = Position(
                intent.symbol, remaining, existing.avg_cost
            )
        self.orders_today += 1
        self._last_prices[intent.symbol] = ref_price
        return Fill(
            symbol=intent.symbol,
            side=Side.SELL,
            quantity=qty,
            price=ref_price,
            notional=proceeds,
            ts=datetime.now(timezone.utc),
            mode=TradingMode.PAPER,
            order_id=f"paper-{uuid.uuid4().hex[:10]}",
        )
