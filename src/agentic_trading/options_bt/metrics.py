"""Trade and portfolio metrics for options backtests."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any  # noqa: F401 — used by score_scenario_risk duck typing


@dataclass
class OptionTrade:
    symbol: str
    option_type: str
    entry_day: str
    exit_day: str
    strike: float
    dte_entry: int
    dte_exit: int
    entry_premium: float
    exit_premium: float
    contracts: int
    premium_paid_usd: float
    premium_exit_usd: float
    pnl_usd: float
    pnl_pct: float
    hold_days: int
    exit_reason: str
    entry_spot: float
    exit_spot: float
    iv_entry: float
    delta_entry: float

    @property
    def win(self) -> bool:
        return self.pnl_usd > 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["win"] = self.win
        return d


@dataclass
class TradeMetrics:
    n_trades: int = 0
    n_wins: int = 0
    n_losses: int = 0
    win_rate: float = 0.0
    total_pnl_usd: float = 0.0
    avg_pnl_usd: float = 0.0
    avg_win_usd: float = 0.0
    avg_loss_usd: float = 0.0
    expectancy_usd: float = 0.0
    profit_factor: float = 0.0
    avg_hold_days: float = 0.0
    avg_pnl_pct: float = 0.0
    max_win_usd: float = 0.0
    max_loss_usd: float = 0.0
    max_drawdown_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    ending_equity: float = 0.0
    total_return_pct: float = 0.0
    sharpe_like: float = 0.0  # mean/std of trade pnls (not annualized)
    exit_reasons: dict[str, int] = field(default_factory=dict)
    by_symbol: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_trades(
    trades: list[OptionTrade],
    *,
    starting_equity: float = 1000.0,
) -> TradeMetrics:
    m = TradeMetrics(ending_equity=starting_equity)
    if not trades:
        return m

    pnls = [t.pnl_usd for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    m.n_trades = len(trades)
    m.n_wins = len(wins)
    m.n_losses = len(losses)
    m.win_rate = m.n_wins / m.n_trades if m.n_trades else 0.0
    m.total_pnl_usd = sum(pnls)
    m.avg_pnl_usd = m.total_pnl_usd / m.n_trades
    m.avg_win_usd = (sum(wins) / len(wins)) if wins else 0.0
    m.avg_loss_usd = (sum(losses) / len(losses)) if losses else 0.0
    m.expectancy_usd = m.avg_pnl_usd
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    m.profit_factor = (gross_win / gross_loss) if gross_loss > 1e-9 else (99.0 if gross_win > 0 else 0.0)
    m.avg_hold_days = sum(t.hold_days for t in trades) / m.n_trades
    m.avg_pnl_pct = sum(t.pnl_pct for t in trades) / m.n_trades
    m.max_win_usd = max(pnls)
    m.max_loss_usd = min(pnls)

    # Equity curve (sequential, one open max so sum is fine)
    eq = starting_equity
    peak = eq
    max_dd = 0.0
    max_dd_pct = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = peak - eq
        max_dd = max(max_dd, dd)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, dd / peak)
    m.ending_equity = eq
    m.total_return_pct = (eq / starting_equity) - 1.0 if starting_equity else 0.0
    m.max_drawdown_usd = max_dd
    m.max_drawdown_pct = max_dd_pct

    if len(pnls) >= 2:
        sd = statistics.pstdev(pnls)
        m.sharpe_like = (statistics.mean(pnls) / sd) if sd > 1e-9 else 0.0

    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    m.exit_reasons = reasons

    by_sym: dict[str, dict[str, float]] = {}
    for t in trades:
        b = by_sym.setdefault(t.symbol, {"n": 0, "pnl": 0.0, "wins": 0})
        b["n"] += 1
        b["pnl"] += t.pnl_usd
        if t.win:
            b["wins"] += 1
    for sym, b in by_sym.items():
        b["win_rate"] = b["wins"] / b["n"] if b["n"] else 0.0
    m.by_symbol = by_sym
    return m


def score_metrics(
    m: TradeMetrics,
    *,
    target_win_rate: float = 0.60,
    min_trades: int = 15,
    prefer_big_money: bool = True,
) -> float:
    """
    Single objective for the search agent.

    Balances: hit rate near target, positive expectancy, enough sample, upside.
    """
    if m.n_trades < min_trades:
        # Soft penalty for sparse strategies
        sample_pen = -5.0 * (min_trades - m.n_trades)
    else:
        sample_pen = 0.0

    # Win-rate band: prefer near target; mild bonus inside ±8pp
    wr_err = abs(m.win_rate - target_win_rate)
    if m.win_rate >= target_win_rate - 0.08:
        wr_score = 20.0 - 80.0 * wr_err
    else:
        wr_score = 10.0 - 120.0 * wr_err

    # Expectancy / PF
    exp_score = max(-30.0, min(40.0, m.expectancy_usd * 0.4))
    pf_score = max(-10.0, min(25.0, (m.profit_factor - 1.0) * 12.0))

    # Big-money tilt: total return + max win
    if prefer_big_money:
        ret_score = max(-20.0, min(40.0, m.total_return_pct * 40.0))
        moon_score = max(0.0, min(15.0, m.max_win_usd / 50.0))
    else:
        ret_score = max(-10.0, min(20.0, m.total_return_pct * 20.0))
        moon_score = 0.0

    # Drawdown penalty
    dd_pen = -40.0 * m.max_drawdown_pct

    # Negative expectancy hard drag
    if m.total_pnl_usd < 0:
        exp_score -= 15.0

    return wr_score + exp_score + pf_score + ret_score + moon_score + dd_pen + sample_pen


def score_scenario_risk(scenario: Any, base_score: float) -> float:
    """Nudge scores away from death-spiral stops and toward practical risk."""
    # Late import-style: duck-typed scenario
    stop = float(getattr(scenario, "stop_loss_pct", 0.5) or 0.5)
    tp = float(getattr(scenario, "take_profit_pct", 0.8) or 0.8)
    # Prefer stop in 40–60% band (BAC lesson + risk control)
    if stop > 0.75:
        base_score -= 12.0
    elif 0.40 <= stop <= 0.60:
        base_score += 4.0
    # Prefer RR where TP is not tiny vs stop
    if tp >= stop * 0.8:
        base_score += 3.0
    return base_score


def bootstrap_win_rate_ci(
    trades: list[OptionTrade],
    *,
    n_boot: int = 400,
    seed: int = 7,
) -> tuple[float, float, float]:
    """Return (mean, lo, hi) empirical bootstrap CI for win rate (2.5–97.5)."""
    if not trades:
        return 0.0, 0.0, 0.0
    wins = [1 if t.win else 0 for t in trades]
    n = len(wins)
    state = seed

    def rnd() -> float:
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF

    samples: list[float] = []
    for _ in range(n_boot):
        s = 0
        for _i in range(n):
            s += wins[int(rnd() * n) % n]
        samples.append(s / n)
    samples.sort()
    lo = samples[int(0.025 * n_boot)]
    hi = samples[min(n_boot - 1, int(0.975 * n_boot))]
    mean = sum(samples) / len(samples)
    return mean, lo, hi
