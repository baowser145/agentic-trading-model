from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class SignalAction(str, Enum):
    ENTER_LONG = "enter_long"
    EXIT_LONG = "exit_long"
    HOLD = "hold"
    FLAT = "flat"


class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class Bar:
    symbol: str
    close: float
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class Signal:
    symbol: str
    action: SignalAction
    strength: float  # 0..1
    reason: str
    ref_price: float


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: float
    avg_cost: float

    @property
    def notional_at(self) -> float:
        return abs(self.quantity) * self.avg_cost

    def market_value(self, price: float) -> float:
        return self.quantity * price


@dataclass
class PortfolioSnapshot:
    cash: float
    equity: float
    positions: dict[str, Position]
    realized_pnl_today: float = 0.0
    orders_today: int = 0
    starting_equity_today: float = 0.0
    halted: bool = False
    halt_reason: str | None = None

    def position_qty(self, symbol: str) -> float:
        p = self.positions.get(symbol)
        return p.quantity if p else 0.0


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: Side
    notional: float | None = None
    quantity: float | None = None
    reason: str = ""
    limit_price: float | None = None  # None => market-like at ref

    def __post_init__(self) -> None:
        if self.notional is None and self.quantity is None:
            raise ValueError("OrderIntent requires notional or quantity")
        if self.notional is not None and self.notional < 0:
            raise ValueError("notional must be >= 0")
        if self.quantity is not None and self.quantity < 0:
            raise ValueError("quantity must be >= 0")


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    intent: OrderIntent | None
    reason: str
    halted: bool = False


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: Side
    quantity: float
    price: float
    notional: float
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    mode: TradingMode = TradingMode.PAPER
    order_id: str = ""


@dataclass
class TickResult:
    ts: datetime
    mode: TradingMode
    signals: list[Signal]
    decisions: list[RiskDecision]
    fills: list[Fill]
    portfolio: PortfolioSnapshot
    notes: list[str] = field(default_factory=list)

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "mode": self.mode.value,
            "signals": [
                {
                    "symbol": s.symbol,
                    "action": s.action.value,
                    "strength": s.strength,
                    "reason": s.reason,
                    "ref_price": s.ref_price,
                }
                for s in self.signals
            ],
            "decisions": [
                {
                    "approved": d.approved,
                    "reason": d.reason,
                    "halted": d.halted,
                    "intent": (
                        None
                        if d.intent is None
                        else {
                            "symbol": d.intent.symbol,
                            "side": d.intent.side.value,
                            "notional": d.intent.notional,
                            "quantity": d.intent.quantity,
                            "reason": d.intent.reason,
                        }
                    ),
                }
                for d in self.decisions
            ],
            "fills": [
                {
                    "symbol": f.symbol,
                    "side": f.side.value,
                    "quantity": f.quantity,
                    "price": f.price,
                    "notional": f.notional,
                    "mode": f.mode.value,
                    "order_id": f.order_id,
                    "ts": f.ts.isoformat(),
                }
                for f in self.fills
            ],
            "portfolio": {
                "cash": self.portfolio.cash,
                "equity": self.portfolio.equity,
                "orders_today": self.portfolio.orders_today,
                "realized_pnl_today": self.portfolio.realized_pnl_today,
                "halted": self.portfolio.halted,
                "halt_reason": self.portfolio.halt_reason,
                "positions": {
                    sym: {
                        "quantity": p.quantity,
                        "avg_cost": p.avg_cost,
                    }
                    for sym, p in self.portfolio.positions.items()
                },
            },
            "notes": self.notes,
        }
