"""Option strategy scenario definition for backtests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class OptionScenario:
    """
    Long-premium single-leg playbook parameters.

    Compatible with live constraints: 7–31 DTE, no 0DTE, max 1 open, single leg.
    """

    name: str = "baseline"
    # Direction: call | put | auto (auto = call if market green else put)
    option_type: str = "call"
    # Contract selection
    target_dte: int = 21
    min_dte: int = 14
    max_dte: int = 31
    target_delta: float = 0.40  # |delta| target ~0.30–0.50 liquid long premium
    # Entry filters
    require_market_green: bool = True  # SPY > SMA
    market_sma_period: int = 20
    min_momentum_pct: float = 0.02  # underlying N-day return
    momentum_lookback: int = 5
    require_above_sma: bool = True
    underlying_sma_period: int = 10
    # Premium management (BAC lesson: avoid tiny stops like 10%)
    take_profit_pct: float = 0.80  # +80% on premium
    stop_loss_pct: float = 0.50  # -50% on premium
    exit_dte: int = 3  # force close when DTE <= this
    max_hold_days: int = 20
    # Risk / book
    premium_budget_usd: float = 100.0
    contracts: int = 1
    max_open: int = 1
    starting_equity: float = 1000.0
    # IV model
    iv_window: int = 20
    iv_multiplier: float = 1.10  # slight IV premium vs realized
    risk_free_rate: float = 0.04
    # Scan cadence
    entry_cooldown_days: int = 1  # min days between new entries
    # Universe handled externally; scenario may bias names
    symbols: list[str] = field(default_factory=lambda: ["AAPL", "MSFT", "NVDA", "META", "AMZN"])
    market_symbol: str = "SPY"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OptionScenario":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        clean = {k: v for k, v in d.items() if k in known}
        return cls(**clean)

    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha1(payload.encode()).hexdigest()[:12]

    def clamp_live_rails(self) -> "OptionScenario":
        """Enforce AGENTS.md options rails."""
        self.min_dte = max(7, int(self.min_dte))
        self.max_dte = min(31, max(self.min_dte, int(self.max_dte)))
        self.target_dte = min(max(self.target_dte, self.min_dte), self.max_dte)
        self.exit_dte = max(1, min(int(self.exit_dte), self.min_dte - 1 if self.min_dte > 1 else 1))
        self.contracts = max(1, min(int(self.contracts), 1))  # Level-2 playbook: 1
        self.max_open = 1
        # Don't allow death-by-noise stops from BAC autopsy
        self.stop_loss_pct = max(0.30, min(0.90, float(self.stop_loss_pct)))
        self.take_profit_pct = max(0.25, min(3.0, float(self.take_profit_pct)))
        self.target_delta = max(0.25, min(0.55, float(self.target_delta)))
        return self


# Seed library for the search agent — spans risk / win-rate tradeoffs.
SEED_SCENARIOS: list[OptionScenario] = [
    OptionScenario(
        name="balanced_40d_tp80_sl50",
        target_delta=0.40,
        target_dte=21,
        take_profit_pct=0.80,
        stop_loss_pct=0.50,
        min_momentum_pct=0.02,
        notes="Baseline long call: ~40d, +80/-50, mild momentum",
    ),
    OptionScenario(
        name="high_wr_tp40_sl50",
        target_delta=0.45,
        target_dte=28,
        take_profit_pct=0.40,
        stop_loss_pct=0.50,
        min_momentum_pct=0.015,
        notes="Faster profit take for higher hit rate",
    ),
    OptionScenario(
        name="moonshot_tp150_sl50",
        target_delta=0.30,
        target_dte=21,
        take_profit_pct=1.50,
        stop_loss_pct=0.50,
        min_momentum_pct=0.03,
        notes="Riskier OTM, bigger target — lower WR expected",
    ),
    OptionScenario(
        name="atm_swing_tp60_sl45",
        target_delta=0.50,
        target_dte=14,
        min_dte=10,
        max_dte=21,
        take_profit_pct=0.60,
        stop_loss_pct=0.45,
        min_momentum_pct=0.01,
        notes="Near ATM, shorter DTE, balanced exits",
    ),
    OptionScenario(
        name="strict_filter_tp100",
        target_delta=0.35,
        target_dte=25,
        take_profit_pct=1.00,
        stop_loss_pct=0.50,
        min_momentum_pct=0.04,
        momentum_lookback=10,
        require_above_sma=True,
        underlying_sma_period=20,
        notes="Strict momentum filter; fewer higher-conviction trades",
    ),
    OptionScenario(
        name="auto_direction_tp70",
        option_type="auto",
        target_delta=0.40,
        target_dte=21,
        take_profit_pct=0.70,
        stop_loss_pct=0.50,
        min_momentum_pct=0.02,
        notes="Call if SPY green else put; same premium management",
    ),
    OptionScenario(
        name="wide_stop_tp50",
        target_delta=0.40,
        target_dte=28,
        take_profit_pct=0.50,
        stop_loss_pct=0.65,
        min_momentum_pct=0.02,
        notes="Wider stop, smaller target — WR-first",
    ),
    OptionScenario(
        name="bac_lesson_no_tight_stop",
        target_delta=0.40,
        target_dte=21,
        take_profit_pct=0.80,
        stop_loss_pct=0.50,  # not 0.10
        min_momentum_pct=0.025,
        exit_dte=3,
        notes="Explicit BAC postmortem: no 10% option stops",
    ),
]
