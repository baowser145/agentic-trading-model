from __future__ import annotations

from agentic_trading.config import RiskConfig, StrategyConfig
from agentic_trading.models import (
    Bar,
    PortfolioSnapshot,
    Quote,
    Signal,
    SignalAction,
)
from agentic_trading.strategy.base import Strategy


def _sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    window = closes[-period:]
    return sum(window) / len(window)


def _prior_range(closes: list[float], lookback: int) -> tuple[float, float] | None:
    """High/low of bars *before* the current bar."""
    if len(closes) < lookback + 1:
        return None
    prior = closes[-(lookback + 1) : -1]
    return max(prior), min(prior)


class DayTradePlaybookStrategy(Strategy):
    """
    Riskier day-trade style playbook (paper-friendly approximations):

    Market filter:
      - SPY (or market_symbol) green: last close > SMA *or* last > prior-range high
        (proxy for above a key level / prior-day high when we only have closes)

    Entries (only if market green):
      - Breakout: close breaks above prior range high
      - Pullback: uptrend (close > SMA), dipped toward SMA, bouncing

    Exit management (stops/targets) is enforced by the engine via OpenTradePlan;
    strategy also emits EXIT if market filter fails while long.
    """

    def __init__(
        self,
        config: StrategyConfig,
        symbols: list[str],
        risk: RiskConfig,
    ) -> None:
        self.config = config
        self.symbols = symbols
        self.risk = risk
        self.market_symbol = (config.market_symbol or "SPY").upper()

    def _market_green(self, history: dict[str, list[Bar]], quotes: dict[str, Quote]) -> tuple[bool, str]:
        m = self.market_symbol
        q = quotes.get(m)
        bars = history.get(m) or []
        closes = [b.close for b in bars if b.close > 0]
        if q and q.price > 0:
            # Ensure last point reflects live quote
            if not closes or abs(closes[-1] - q.price) > 1e-9:
                closes = closes + [q.price]
        if len(closes) < max(3, self.config.sma_period):
            return False, f"market {m}: insufficient history"
        sma = _sma(closes, self.config.sma_period)
        last = closes[-1]
        rng = _prior_range(closes, min(self.config.range_lookback, len(closes) - 1))
        above_sma = sma is not None and last > sma
        above_key = rng is not None and last >= rng[0]
        if above_sma or above_key:
            why = []
            if above_sma:
                why.append(f"{m}>SMA{self.config.sma_period}")
            if above_key and rng:
                why.append(f"{m}>=range_high {rng[0]:.2f}")
            return True, "market green: " + ", ".join(why)
        sma_s = f"{sma:.2f}" if sma is not None else "n/a"
        return False, f"market red: {m} last={last:.2f} sma={sma_s}"

    def _size(
        self,
        entry: float,
        stop: float,
        portfolio: PortfolioSnapshot,
    ) -> tuple[float, float] | None:
        """Return (qty, notional) from risk_per_trade_pct / stop distance, capped."""
        if entry <= 0 or stop <= 0 or stop >= entry:
            return None
        risk_dollars = portfolio.equity * self.risk.risk_per_trade_pct
        stop_dist = entry - stop
        if stop_dist <= 0:
            return None
        qty = risk_dollars / stop_dist
        notional = qty * entry
        # Caps
        max_by_pos = portfolio.equity * self.risk.max_position_pct
        max_by_order = self.risk.max_order_notional
        max_by_cash = portfolio.buying_power
        cap = min(max_by_pos, max_by_order, max_by_cash)
        if cap < 1.0:
            return None
        if notional > cap:
            qty = cap / entry
            notional = cap
        # Re-check: if capped, risk is lower than full 5% — still OK
        if notional < 1.0 or qty <= 0:
            return None
        return qty, notional

    def generate(
        self,
        quotes: dict[str, Quote],
        history: dict[str, list[Bar]],
        portfolio: PortfolioSnapshot,
    ) -> list[Signal]:
        signals: list[Signal] = []
        market_ok, market_reason = self._market_green(history, quotes)
        rr = max(0.5, self.risk.reward_risk_ratio)
        period = max(2, self.config.sma_period)
        look = max(3, self.config.range_lookback)

        for symbol in self.symbols:
            q = quotes.get(symbol)
            if q is None or q.price <= 0:
                continue
            bars = history.get(symbol) or []
            closes = [b.close for b in bars if b.close > 0]
            if not closes or abs(closes[-1] - q.price) > 1e-9:
                closes = closes + [q.price]

            qty_held = portfolio.position_qty(symbol)

            if len(closes) < max(period, look + 2):
                signals.append(
                    Signal(
                        symbol=symbol,
                        action=SignalAction.HOLD if qty_held > 0 else SignalAction.FLAT,
                        strength=0.0,
                        reason=f"insufficient history ({len(closes)})",
                        ref_price=q.price,
                    )
                )
                continue

            sma = _sma(closes, period)
            last = closes[-1]
            prev = closes[-2]
            rng = _prior_range(closes, look)
            if sma is None or rng is None:
                signals.append(
                    Signal(
                        symbol=symbol,
                        action=SignalAction.HOLD if qty_held > 0 else SignalAction.FLAT,
                        strength=0.0,
                        reason="missing sma/range",
                        ref_price=q.price,
                    )
                )
                continue
            range_high, range_low = rng

            # --- already long: strategy only soft-exits if market turns red ---
            if qty_held > 0:
                if not market_ok:
                    signals.append(
                        Signal(
                            symbol=symbol,
                            action=SignalAction.EXIT_LONG,
                            strength=0.8,
                            reason=f"exit: {market_reason}",
                            ref_price=q.price,
                        )
                    )
                else:
                    signals.append(
                        Signal(
                            symbol=symbol,
                            action=SignalAction.HOLD,
                            strength=0.5,
                            reason=f"hold long; engine manages stop/target; {market_reason}",
                            ref_price=q.price,
                        )
                    )
                continue

            # --- flat: need market green ---
            if not market_ok:
                signals.append(
                    Signal(
                        symbol=symbol,
                        action=SignalAction.FLAT,
                        strength=0.0,
                        reason=market_reason,
                        ref_price=q.price,
                    )
                )
                continue

            # Breakout: close clears prior range high
            breakout = last > range_high and prev <= range_high * 1.001
            # Pullback in uptrend: above SMA, dipped near SMA, now bouncing
            near_sma = abs(prev - sma) / sma <= self.config.pullback_tol_pct or prev < sma * (
                1 + self.config.pullback_tol_pct
            )
            pullback = (
                last > sma
                and prev <= last
                and near_sma
                and last > prev
                and range_low < sma
            )

            if not breakout and not pullback:
                signals.append(
                    Signal(
                        symbol=symbol,
                        action=SignalAction.FLAT,
                        strength=0.0,
                        reason=(
                            f"no setup; last={last:.2f} RH={range_high:.2f} "
                            f"SMA={sma:.2f} ({market_reason})"
                        ),
                        ref_price=q.price,
                    )
                )
                continue

            setup = "breakout" if breakout else "pullback"
            entry = last
            # Stop under range low (buffer 0.15%)
            stop = min(range_low, sma) * 0.9985
            if stop >= entry:
                stop = entry * 0.99  # fallback 1% stop
            risk_per_share = entry - stop
            target = entry + rr * risk_per_share
            sized = self._size(entry, stop, portfolio)
            if sized is None:
                signals.append(
                    Signal(
                        symbol=symbol,
                        action=SignalAction.FLAT,
                        strength=0.0,
                        reason=f"{setup} but cannot size (cash/stop)",
                        ref_price=q.price,
                        stop_price=stop,
                        target_price=target,
                    )
                )
                continue
            qty_s, notional_s = sized
            strength = min(1.0, risk_per_share / entry * 20)
            signals.append(
                Signal(
                    symbol=symbol,
                    action=SignalAction.ENTER_LONG,
                    strength=float(strength),
                    reason=(
                        f"{setup}: entry={entry:.2f} stop={stop:.2f} "
                        f"target={target:.2f} ({rr:.1f}R); {market_reason}"
                    ),
                    ref_price=q.price,
                    stop_price=stop,
                    target_price=target,
                    suggested_quantity=qty_s,
                    suggested_notional=notional_s,
                )
            )
        return signals
