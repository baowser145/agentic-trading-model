from __future__ import annotations

from copy import deepcopy

from agentic_trading.config import RiskConfig
from agentic_trading.models import (
    OrderIntent,
    PortfolioSnapshot,
    Position,
    RiskDecision,
    Side,
    Signal,
    SignalAction,
)


class RiskGate:
    """Hard risk rails. Every order must pass before broker execution."""

    def __init__(self, config: RiskConfig) -> None:
        self.config = config

    def evaluate_portfolio_halt(self, portfolio: PortfolioSnapshot) -> PortfolioSnapshot:
        """Halt if daily loss exceeds cap. Mutates halted flags on a copy-like update."""
        if portfolio.halted:
            return portfolio
        start = portfolio.starting_equity_today or portfolio.equity
        if start <= 0:
            return portfolio
        # Unrealized + realized: use equity vs start
        loss_pct = (start - portfolio.equity) / start
        if loss_pct >= self.config.max_daily_loss_pct:
            portfolio.halted = True
            portfolio.halt_reason = (
                f"max_daily_loss_pct breached: {loss_pct:.4f} >= "
                f"{self.config.max_daily_loss_pct:.4f}"
            )
        return portfolio

    def signal_to_intent(
        self,
        signal: Signal,
        portfolio: PortfolioSnapshot,
    ) -> OrderIntent | None:
        """Map a signal to a proposed order intent (pre-risk size clamp)."""
        qty = portfolio.position_qty(signal.symbol)
        price = signal.ref_price
        if price <= 0:
            return None

        if signal.action == SignalAction.ENTER_LONG and qty <= 0:
            notional = min(
                self.config.max_order_notional,
                portfolio.equity * self.config.max_position_pct,
                portfolio.buying_power,  # available cash (immediate when in account)
            )
            if notional < 1.0:
                return None
            return OrderIntent(
                symbol=signal.symbol,
                side=Side.BUY,
                notional=notional,
                reason=signal.reason,
                limit_price=price,
            )

        if signal.action in (SignalAction.EXIT_LONG, SignalAction.FLAT) and qty > 0:
            return OrderIntent(
                symbol=signal.symbol,
                side=Side.SELL,
                quantity=qty,
                reason=signal.reason,
                limit_price=price,
            )

        return None

    def approve(
        self,
        intent: OrderIntent,
        portfolio: PortfolioSnapshot,
        ref_price: float,
    ) -> RiskDecision:
        portfolio = self.evaluate_portfolio_halt(portfolio)

        if portfolio.halted:
            # Halt freezes new risk (buys). Still allow sells to reduce exposure.
            if intent.side != Side.SELL:
                return RiskDecision(
                    approved=False,
                    intent=intent,
                    reason=f"halted: {portfolio.halt_reason}",
                    halted=True,
                )

        if portfolio.orders_today >= self.config.max_orders_per_day:
            return RiskDecision(
                approved=False,
                intent=intent,
                reason=(
                    f"max_orders_per_day: {portfolio.orders_today} >= "
                    f"{self.config.max_orders_per_day}"
                ),
            )

        if ref_price <= 0:
            return RiskDecision(
                approved=False,
                intent=intent,
                reason="invalid ref_price",
            )

        # Resolve quantity / notional
        if intent.side == Side.BUY:
            notional = intent.notional
            if notional is None and intent.quantity is not None:
                notional = intent.quantity * ref_price
            if notional is None or notional <= 0:
                return RiskDecision(False, intent, "buy requires positive notional/qty")

            if notional > self.config.max_order_notional + 1e-9:
                return RiskDecision(
                    False,
                    intent,
                    f"max_order_notional: {notional:.2f} > {self.config.max_order_notional:.2f}",
                )

            # Buys use available cash (default: any cash in account — no 1-day delay)
            if notional > portfolio.buying_power + 1e-9:
                label = (
                    "available cash"
                    if portfolio.trade_when_cash_available
                    else "settled cash (strict T+1)"
                )
                return RiskDecision(
                    False,
                    intent,
                    (
                        f"insufficient {label}: need {notional:.2f}, "
                        f"buying_power {portfolio.buying_power:.2f}, "
                        f"settled {portfolio.settled_cash}, "
                        f"unsettled {portfolio.unsettled_cash:.2f}"
                    ),
                )

            open_count = sum(1 for p in portfolio.positions.values() if p.quantity > 0)
            existing = portfolio.position_qty(intent.symbol)
            if existing <= 0 and open_count >= self.config.max_open_positions:
                return RiskDecision(
                    False,
                    intent,
                    f"max_open_positions: {open_count} >= {self.config.max_open_positions}",
                )

            # Position notional after buy
            cur_pos = portfolio.positions.get(intent.symbol)
            cur_mv = (cur_pos.quantity * ref_price) if cur_pos else 0.0
            new_mv = cur_mv + notional
            max_mv = portfolio.equity * self.config.max_position_pct
            if new_mv > max_mv + 1e-6:
                return RiskDecision(
                    False,
                    intent,
                    f"max_position_pct: target mv {new_mv:.2f} > {max_mv:.2f}",
                )

            return RiskDecision(True, intent, "approved")

        # SELL
        qty = intent.quantity
        if qty is None and intent.notional is not None:
            qty = intent.notional / ref_price
        if qty is None or qty <= 0:
            return RiskDecision(False, intent, "sell requires positive quantity/notional")

        held = portfolio.position_qty(intent.symbol)
        if qty > held + 1e-9:
            return RiskDecision(
                False,
                intent,
                f"sell qty {qty:.6f} > held {held:.6f}",
            )

        # Cap sell notional reporting only — sells always allowed if held (risk reduce)
        return RiskDecision(True, intent, "approved")

    def _reserve(self, working: PortfolioSnapshot, intent: OrderIntent, ref_price: float) -> None:
        """Update working portfolio so subsequent same-tick signals see capacity used."""
        working.orders_today += 1
        if intent.side == Side.BUY:
            notional = intent.notional
            if notional is None and intent.quantity is not None:
                notional = intent.quantity * ref_price
            if notional is None:
                return
            qty = notional / ref_price if ref_price > 0 else 0.0
            # Reserve available cash immediately (no wait when funds are in account)
            if working.trade_when_cash_available:
                sc = working.settled_cash if working.settled_cash is not None else 0.0
                from_settled = min(sc, notional)
                working.settled_cash = sc - from_settled
                rem = notional - from_settled
                if rem > 0:
                    working.unsettled_cash = max(0.0, working.unsettled_cash - rem)
                working.cash = (working.settled_cash or 0.0) + working.unsettled_cash
            else:
                sc = working.settled_cash if working.settled_cash is not None else working.cash
                working.settled_cash = sc - notional
                working.cash = working.buying_power + working.unsettled_cash
            existing = working.positions.get(intent.symbol)
            if existing:
                new_qty = existing.quantity + qty
                new_cost = (
                    (existing.avg_cost * existing.quantity) + notional
                ) / new_qty if new_qty else ref_price
                working.positions[intent.symbol] = Position(
                    intent.symbol, new_qty, new_cost
                )
            else:
                working.positions[intent.symbol] = Position(
                    intent.symbol, qty, ref_price
                )
        else:
            qty = intent.quantity
            if qty is None and intent.notional is not None and ref_price > 0:
                qty = intent.notional / ref_price
            if qty is None:
                return
            existing = working.positions.get(intent.symbol)
            if not existing:
                return
            remaining = existing.quantity - qty
            proceeds = qty * ref_price
            # Track as unsettled for settlement calendar; still available if flag on
            working.unsettled_cash += proceeds
            working.cash = (working.settled_cash or 0.0) + working.unsettled_cash
            if remaining <= 1e-12:
                del working.positions[intent.symbol]
            else:
                working.positions[intent.symbol] = Position(
                    intent.symbol, remaining, existing.avg_cost
                )

    def process_signals(
        self,
        signals: list[Signal],
        portfolio: PortfolioSnapshot,
    ) -> list[RiskDecision]:
        decisions: list[RiskDecision] = []
        # Working copy: reserve capacity after each approval within the same tick
        working = PortfolioSnapshot(
            cash=portfolio.cash,
            equity=portfolio.equity,
            positions=deepcopy(portfolio.positions),
            realized_pnl_today=portfolio.realized_pnl_today,
            orders_today=portfolio.orders_today,
            starting_equity_today=portfolio.starting_equity_today,
            halted=portfolio.halted,
            halt_reason=portfolio.halt_reason,
            settled_cash=(
                portfolio.settled_cash
                if portfolio.settled_cash is not None
                else max(0.0, portfolio.cash - portfolio.unsettled_cash)
            ),
            unsettled_cash=portfolio.unsettled_cash,
            trade_when_cash_available=portfolio.trade_when_cash_available,
        )
        for signal in signals:
            intent = self.signal_to_intent(signal, working)
            if intent is None:
                decisions.append(
                    RiskDecision(
                        approved=False,
                        intent=None,
                        reason=f"no intent for {signal.symbol} action={signal.action.value}",
                    )
                )
                continue
            decision = self.approve(intent, working, signal.ref_price)
            decisions.append(decision)
            if decision.approved and decision.intent is not None:
                self._reserve(working, decision.intent, signal.ref_price)
        return decisions
