from __future__ import annotations

from agentic_trading.config import StrategyConfig
from agentic_trading.models import (
    Bar,
    PortfolioSnapshot,
    Quote,
    Signal,
    SignalAction,
)
from agentic_trading.strategy.base import Strategy


class SimpleMomentumStrategy(Strategy):
    """Long when last close > SMA(N); flat otherwise. No shorting."""

    def __init__(self, config: StrategyConfig, symbols: list[str]) -> None:
        self.config = config
        self.symbols = symbols

    def generate(
        self,
        quotes: dict[str, Quote],
        history: dict[str, list[Bar]],
        portfolio: PortfolioSnapshot,
    ) -> list[Signal]:
        signals: list[Signal] = []
        period = max(2, self.config.sma_period)

        for symbol in self.symbols:
            q = quotes.get(symbol)
            if q is None or q.price <= 0:
                continue
            bars = history.get(symbol) or []
            closes = [b.close for b in bars if b.close > 0]
            # Prefer history; if short, use quote only as last point
            if len(closes) < period:
                signals.append(
                    Signal(
                        symbol=symbol,
                        action=SignalAction.HOLD,
                        strength=0.0,
                        reason=f"insufficient history ({len(closes)} < {period})",
                        ref_price=q.price,
                    )
                )
                continue

            window = closes[-period:]
            sma = sum(window) / len(window)
            last = window[-1]
            qty = portfolio.position_qty(symbol)

            if last > sma:
                if qty > 0:
                    action = SignalAction.HOLD
                    reason = f"already long; close {last:.4f} > SMA{period} {sma:.4f}"
                else:
                    action = SignalAction.ENTER_LONG
                    reason = f"close {last:.4f} > SMA{period} {sma:.4f}"
                strength = min(1.0, (last - sma) / sma) if sma else 0.0
            else:
                if qty > 0:
                    action = SignalAction.EXIT_LONG
                    reason = f"close {last:.4f} <= SMA{period} {sma:.4f}"
                else:
                    action = SignalAction.FLAT
                    reason = f"flat; close {last:.4f} <= SMA{period} {sma:.4f}"
                strength = min(1.0, (sma - last) / sma) if sma else 0.0

            signals.append(
                Signal(
                    symbol=symbol,
                    action=action,
                    strength=float(strength),
                    reason=reason,
                    ref_price=q.price,
                )
            )
        return signals
