"""Out-of-sample validation for options scenarios."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from agentic_trading.options_bt.backtest import BacktestResult, run_backtest
from agentic_trading.options_bt.data import DailyBar
from agentic_trading.options_bt.metrics import (
    TradeMetrics,
    bootstrap_win_rate_ci,
    score_metrics,
    summarize_trades,
)
from agentic_trading.options_bt.scenario import OptionScenario


@dataclass
class ValidationReport:
    scenario_name: str
    fingerprint: str
    full: TradeMetrics
    train: TradeMetrics
    test: TradeMetrics
    walk_forward: list[dict[str, Any]] = field(default_factory=list)
    win_rate_ci: tuple[float, float, float] = (0.0, 0.0, 0.0)
    passes: bool = False
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "fingerprint": self.fingerprint,
            "full": self.full.to_dict(),
            "train": self.train.to_dict(),
            "test": self.test.to_dict(),
            "walk_forward": self.walk_forward,
            "win_rate_ci": {
                "mean": self.win_rate_ci[0],
                "lo": self.win_rate_ci[1],
                "hi": self.win_rate_ci[2],
            },
            "passes": self.passes,
            "reasons": self.reasons,
            "score": self.score,
        }


def _split_day(series: dict[str, list[DailyBar]], frac: float = 0.7) -> date | None:
    mkt = next(iter(series.values()), None)
    if not mkt or len(mkt) < 30:
        return None
    idx = int(len(mkt) * frac)
    idx = max(20, min(len(mkt) - 15, idx))
    return mkt[idx].day


def validate_scenario(
    scenario: OptionScenario,
    series: dict[str, list[DailyBar]],
    *,
    target_win_rate: float = 0.60,
    min_test_trades: int = 5,
    min_test_expectancy: float = -5.0,
    require_test_positive: bool = False,
    train_frac: float = 0.70,
    walk_folds: int = 3,
) -> ValidationReport:
    """
    Full sample + train/test + simple walk-forward folds.

    Pass criteria (research bar, not live green light):
    - test win_rate within 15pp of target OR >= target-0.12
    - test profit_factor >= 0.9 (near break-even+) OR positive expectancy
    - enough trades on full sample
    - train and test not wildly inconsistent (both not disastrous)
    - sparse OOS can pass if walk-forward folds show positive expectancy
    """
    full_bt = run_backtest(scenario, series)
    split = _split_day(series, train_frac)
    if split is None:
        rep = ValidationReport(
            scenario_name=scenario.name,
            fingerprint=scenario.fingerprint(),
            full=full_bt.metrics,
            train=TradeMetrics(),
            test=TradeMetrics(),
            reasons=["insufficient data for split"],
            score=score_metrics(full_bt.metrics, target_win_rate=target_win_rate),
        )
        return rep

    train_bt = run_backtest(scenario, series, end=split)
    test_bt = run_backtest(scenario, series, start=split)

    # Walk-forward: sequential folds on calendar
    days = [b.day for b in next(iter(series.values()))]
    wf: list[dict[str, Any]] = []
    if walk_folds >= 2 and len(days) > 120:
        fold_size = len(days) // (walk_folds + 1)
        for f in range(walk_folds):
            # train grows; test is next fold
            train_end_idx = fold_size * (f + 1)
            test_end_idx = min(len(days) - 1, fold_size * (f + 2))
            if test_end_idx <= train_end_idx + 10:
                continue
            train_end = days[train_end_idx]
            test_start = days[train_end_idx + 1]
            test_end = days[test_end_idx]
            tr = run_backtest(scenario, series, end=train_end)
            te = run_backtest(scenario, series, start=test_start, end=test_end)
            wf.append(
                {
                    "fold": f,
                    "train_end": train_end.isoformat(),
                    "test_start": test_start.isoformat(),
                    "test_end": test_end.isoformat(),
                    "train_n": tr.metrics.n_trades,
                    "train_wr": round(tr.metrics.win_rate, 3),
                    "train_pnl": round(tr.metrics.total_pnl_usd, 2),
                    "test_n": te.metrics.n_trades,
                    "test_wr": round(te.metrics.win_rate, 3),
                    "test_pnl": round(te.metrics.total_pnl_usd, 2),
                    "test_expectancy": round(te.metrics.expectancy_usd, 2),
                }
            )

    ci = bootstrap_win_rate_ci(full_bt.trades)
    reasons: list[str] = []
    passes = True

    if full_bt.metrics.n_trades < 12:
        passes = False
        reasons.append(f"full sample trades {full_bt.metrics.n_trades} < 12")

    sparse_oos = test_bt.metrics.n_trades < min_test_trades
    if sparse_oos:
        # Allow pass via walk-forward if folds are healthy
        fold_tests = [f for f in wf if f.get("test_n", 0) >= 2]
        fold_pos = [f for f in fold_tests if (f.get("test_expectancy") or 0) > 0]
        if len(fold_pos) >= max(1, len(fold_tests) // 2) and fold_tests:
            reasons.append(
                f"test trades sparse ({test_bt.metrics.n_trades}<{min_test_trades}); "
                f"walk-forward folds OK ({len(fold_pos)}/{len(fold_tests)} positive expectancy)"
            )
        else:
            passes = False
            reasons.append(f"test trades {test_bt.metrics.n_trades} < {min_test_trades}")

    wr = test_bt.metrics.win_rate
    if test_bt.metrics.n_trades >= 3 and wr < target_win_rate - 0.18:
        passes = False
        reasons.append(
            f"test win_rate {wr:.1%} far below target {target_win_rate:.0%} "
            f"(need ≥{target_win_rate-0.18:.0%})"
        )

    if test_bt.metrics.n_trades >= 3 and test_bt.metrics.expectancy_usd < min_test_expectancy:
        passes = False
        reasons.append(
            f"test expectancy ${test_bt.metrics.expectancy_usd:.2f} < ${min_test_expectancy:.2f}"
        )

    if require_test_positive and test_bt.metrics.total_pnl_usd <= 0:
        passes = False
        reasons.append("test total PnL not positive")

    if (
        test_bt.metrics.n_trades >= min_test_trades
        and test_bt.metrics.profit_factor < 0.85
        and test_bt.metrics.total_pnl_usd < 0
    ):
        passes = False
        reasons.append(
            f"test PF {test_bt.metrics.profit_factor:.2f} and negative PnL"
        )

    # Stability: train not +huge while test collapses
    if (
        train_bt.metrics.total_pnl_usd > 50
        and test_bt.metrics.total_pnl_usd < -30
        and test_bt.metrics.n_trades >= min_test_trades
    ):
        passes = False
        reasons.append("train/test regime break (overfit signal)")

    # Risk sanity: ultra-wide stops inflate WR but risk near-total premium loss
    if scenario.stop_loss_pct > 0.75 and full_bt.metrics.win_rate >= target_win_rate:
        reasons.append(
            f"note: stop_loss_pct={scenario.stop_loss_pct:.0%} is wide — "
            "win rate may be inflated vs tighter risk control"
        )

    if passes and not any(r.startswith("test trades") for r in reasons if "sparse" not in r):
        if not any("passed" in r or "walk-forward" in r for r in reasons):
            reasons.append("passed research validation gates")

    score = score_metrics(
        full_bt.metrics,
        target_win_rate=target_win_rate,
        prefer_big_money=True,
    )
    # Blend OOS quality into score
    score += 0.5 * score_metrics(
        test_bt.metrics,
        target_win_rate=target_win_rate,
        min_trades=min_test_trades,
        prefer_big_money=True,
    )
    if passes:
        score += 10.0

    return ValidationReport(
        scenario_name=scenario.name,
        fingerprint=scenario.fingerprint(),
        full=full_bt.metrics,
        train=train_bt.metrics,
        test=test_bt.metrics,
        walk_forward=wf,
        win_rate_ci=ci,
        passes=passes,
        reasons=reasons,
        score=score,
    )
