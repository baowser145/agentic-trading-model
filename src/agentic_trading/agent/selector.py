from __future__ import annotations

from dataclasses import dataclass

from agentic_trading.models import Bar, Quote, Signal, SignalAction


@dataclass(frozen=True)
class SelectorConfig:
    enabled: bool = True
    max_new_entries_per_tick: int = 1
    market_symbol: str = "SPY"
    # Prefer names outperforming SPY over the lookback window
    prefer_relative_strength: bool = True
    rs_lookback: int = 10


@dataclass
class RankedCandidate:
    signal: Signal
    score: float
    reasons: list[str]


class SetupSelectorAgent:
    """
    Lightweight "what to buy" agent.

    Strategy may flag many ENTER_LONG setups. This agent ranks them and keeps
    only the best N new entries per tick (exits/holds always pass through).

    Not an LLM: deterministic scoring so paper sessions are reproducible.
    A future LLM agent can wrap the same interface for live research.
    """

    def __init__(self, config: SelectorConfig) -> None:
        self.config = config

    def select(
        self,
        signals: list[Signal],
        quotes: dict[str, Quote],
        history: dict[str, list[Bar]],
    ) -> tuple[list[Signal], list[str]]:
        """
        Returns (filtered_signals, notes).
        Non-enter signals pass unchanged. ENTER_LONGs are ranked; only top N kept.
        """
        if not self.config.enabled:
            return signals, ["selector: disabled"]

        enters = [s for s in signals if s.action == SignalAction.ENTER_LONG]
        others = [s for s in signals if s.action != SignalAction.ENTER_LONG]
        if not enters:
            return signals, ["selector: no entry candidates"]

        ranked = [self._rank(s, quotes, history) for s in enters]
        ranked.sort(key=lambda c: c.score, reverse=True)

        keep_n = max(1, self.config.max_new_entries_per_tick)
        kept = ranked[:keep_n]
        dropped = ranked[keep_n:]

        notes = [
            "selector agent ranked entries: "
            + ", ".join(f"{c.signal.symbol}={c.score:.2f}" for c in ranked)
        ]
        for c in kept:
            notes.append(
                f"selector PICK {c.signal.symbol} score={c.score:.2f} "
                f"({'; '.join(c.reasons)})"
            )
        for c in dropped:
            notes.append(
                f"selector SKIP {c.signal.symbol} score={c.score:.2f} "
                f"({'; '.join(c.reasons)})"
            )

        kept_sigs = [c.signal for c in kept]
        # Convert skipped enters into FLAT with selector reason (audit trail)
        skipped_sigs = [
            Signal(
                symbol=c.signal.symbol,
                action=SignalAction.FLAT,
                strength=0.0,
                reason=(
                    f"selector skipped (score={c.score:.2f}; "
                    f"kept top {keep_n}): {c.signal.reason}"
                ),
                ref_price=c.signal.ref_price,
                stop_price=c.signal.stop_price,
                target_price=c.signal.target_price,
            )
            for c in dropped
        ]
        return others + kept_sigs + skipped_sigs, notes

    def _rank(
        self,
        signal: Signal,
        quotes: dict[str, Quote],
        history: dict[str, list[Bar]],
    ) -> RankedCandidate:
        reasons: list[str] = []
        score = 0.0

        # Setup strength from strategy (0..1)
        score += 40.0 * max(0.0, min(1.0, signal.strength))
        reasons.append(f"setup={signal.strength:.2f}")

        # Reward clean R: tighter stop relative to price is OK if 2R is clean
        if signal.stop_price and signal.target_price and signal.ref_price:
            risk = signal.ref_price - signal.stop_price
            reward = signal.target_price - signal.ref_price
            if risk > 0:
                rr = reward / risk
                score += min(25.0, rr * 10.0)
                reasons.append(f"R={rr:.1f}")
                stop_pct = risk / signal.ref_price
                # Prefer stops not absurdly tight/wide (0.3%–3%)
                if 0.003 <= stop_pct <= 0.03:
                    score += 10.0
                    reasons.append("stop_band_ok")
                elif stop_pct > 0.05:
                    score -= 10.0
                    reasons.append("stop_wide")

        # Relative strength vs market
        if self.config.prefer_relative_strength:
            rs = self._relative_strength(signal.symbol, history)
            if rs is not None:
                # rs in fraction, e.g. +0.02 = +2% vs SPY
                score += max(-15.0, min(25.0, rs * 500.0))
                reasons.append(f"RS={rs:+.3%}")

        # Prefer non-index single names slightly for diversification once market green
        if signal.symbol not in ("SPY", "QQQ", "IWM"):
            score += 5.0
            reasons.append("single_name")
        elif signal.symbol == "SPY":
            score -= 2.0
            reasons.append("index")

        # Liquidity proxy: prefer known mega liquid (all our universe is liquid)
        if signal.symbol in {
            "AAPL",
            "MSFT",
            "NVDA",
            "AMZN",
            "META",
            "GOOGL",
            "TSLA",
            "QQQ",
            "SPY",
        }:
            score += 5.0
            reasons.append("liquid")

        # Breakout vs pullback hint in reason string
        rlow = signal.reason.lower()
        if "breakout" in rlow:
            score += 8.0
            reasons.append("breakout")
        if "pullback" in rlow:
            score += 6.0
            reasons.append("pullback")

        return RankedCandidate(signal=signal, score=score, reasons=reasons)

    def _relative_strength(
        self, symbol: str, history: dict[str, list[Bar]]
    ) -> float | None:
        mkt = self.config.market_symbol
        n = max(3, self.config.rs_lookback)
        sc = [b.close for b in (history.get(symbol) or []) if b.close > 0]
        mc = [b.close for b in (history.get(mkt) or []) if b.close > 0]
        if len(sc) < n or len(mc) < n:
            return None
        s_ret = (sc[-1] / sc[-n]) - 1.0
        m_ret = (mc[-1] / mc[-n]) - 1.0
        return s_ret - m_ret
