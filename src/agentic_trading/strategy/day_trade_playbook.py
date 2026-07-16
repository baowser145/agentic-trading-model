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
    strategy also emits EXIT if market filter fails while long — with R1 hysteresis
    (consecutive red ticks + min hold + optional SMA buffer) and R2 re-entry cooldown.
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
        # Stateful filters (persist for process lifetime of the engine)
        self._tick: int = 0
        self._market_red_streak: int = 0
        self._hold_ticks: dict[str, int] = {}
        self._cooldown_until: dict[str, int] = {}

    def _market_green(
        self, history: dict[str, list[Bar]], quotes: dict[str, Quote]
    ) -> tuple[bool, str, float | None, float | None]:
        """
        Returns (green, reason, last, sma).
        Green when last > SMA or last >= prior range high.
        """
        m = self.market_symbol
        q = quotes.get(m)
        bars = history.get(m) or []
        closes = [b.close for b in bars if b.close > 0]
        if q and q.price > 0:
            if not closes or abs(closes[-1] - q.price) > 1e-9:
                closes = closes + [q.price]
        if len(closes) < max(3, self.config.sma_period):
            return False, f"market {m}: insufficient history", None, None
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
            return True, "market green: " + ", ".join(why), last, sma
        sma_s = f"{sma:.2f}" if sma is not None else "n/a"
        return False, f"market red: {m} last={last:.2f} sma={sma_s}", last, sma

    def _exit_red(
        self,
        market_ok: bool,
        last: float | None,
        sma: float | None,
    ) -> tuple[bool, str]:
        """
        R1: stricter red for *soft exits* than for blocking entries.
        Requires not green, plus optional SMA buffer (last meaningfully below SMA).
        """
        if market_ok:
            return False, "market green"
        buf = max(0.0, float(self.config.market_red_sma_buffer_pct))
        if buf > 0 and last is not None and sma is not None and sma > 0:
            floor = sma * (1.0 - buf)
            if last >= floor:
                return (
                    False,
                    f"market soft-zone: last={last:.2f} >= SMA*(1-{buf:.4f})={floor:.2f}",
                )
        return True, "market exit-red"

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
        max_by_pos = portfolio.equity * self.risk.max_position_pct
        max_by_order = self.risk.max_order_notional
        max_by_cash = portfolio.buying_power
        cap = min(max_by_pos, max_by_order, max_by_cash)
        if cap < 1.0:
            return None
        if notional > cap:
            qty = cap / entry
            notional = cap
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
        self._tick += 1
        market_ok, market_reason, mkt_last, mkt_sma = self._market_green(
            history, quotes
        )
        exit_red, exit_red_detail = self._exit_red(market_ok, mkt_last, mkt_sma)

        if exit_red:
            self._market_red_streak += 1
        else:
            self._market_red_streak = 0

        need_red = max(1, int(self.config.market_red_exit_ticks))
        min_hold = max(0, int(self.config.soft_exit_min_hold_ticks))
        cooldown_len = max(0, int(self.config.reentry_cooldown_ticks))
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

            # --- already long: hard stop/target are engine; soft-exit is R1 ---
            if qty_held > 0:
                held = self._hold_ticks.get(symbol, 0) + 1
                self._hold_ticks[symbol] = held

                if exit_red:
                    streak_ok = self._market_red_streak >= need_red
                    hold_ok = held >= min_hold
                    if streak_ok and hold_ok:
                        # R2: arm cooldown when we soft-exit on market red
                        if cooldown_len > 0:
                            self._cooldown_until[symbol] = self._tick + cooldown_len
                        signals.append(
                            Signal(
                                symbol=symbol,
                                action=SignalAction.EXIT_LONG,
                                strength=0.8,
                                reason=(
                                    f"exit: {market_reason} "
                                    f"(R1 red_streak={self._market_red_streak}/{need_red}, "
                                    f"held={held}>={min_hold}; {exit_red_detail})"
                                ),
                                ref_price=q.price,
                            )
                        )
                    else:
                        wait_bits = []
                        if not streak_ok:
                            wait_bits.append(
                                f"red_streak={self._market_red_streak}/{need_red}"
                            )
                        if not hold_ok:
                            wait_bits.append(f"held={held}/{min_hold}")
                        signals.append(
                            Signal(
                                symbol=symbol,
                                action=SignalAction.HOLD,
                                strength=0.5,
                                reason=(
                                    f"hold long; R1 soft-exit wait "
                                    f"({', '.join(wait_bits)}); "
                                    f"engine manages stop/target; {market_reason}"
                                ),
                                ref_price=q.price,
                            )
                        )
                else:
                    signals.append(
                        Signal(
                            symbol=symbol,
                            action=SignalAction.HOLD,
                            strength=0.5,
                            reason=(
                                f"hold long; engine manages stop/target; "
                                f"{market_reason}"
                                + (
                                    f"; {exit_red_detail}"
                                    if not market_ok
                                    else ""
                                )
                            ),
                            ref_price=q.price,
                        )
                    )
                continue

            # Flat: clear hold counter
            self._hold_ticks.pop(symbol, None)

            # R2: cooldown after market-red soft-exit
            until = self._cooldown_until.get(symbol)
            if until is not None and self._tick <= until:
                remaining = until - self._tick + 1
                signals.append(
                    Signal(
                        symbol=symbol,
                        action=SignalAction.FLAT,
                        strength=0.0,
                        reason=(
                            f"R2 reentry cooldown: {remaining} tick(s) left "
                            f"(until tick {until}); was market-red exit"
                        ),
                        ref_price=q.price,
                    )
                )
                continue
            if until is not None and self._tick > until:
                self._cooldown_until.pop(symbol, None)

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
            stop = min(range_low, sma) * 0.9985
            if stop >= entry:
                stop = entry * 0.99
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
