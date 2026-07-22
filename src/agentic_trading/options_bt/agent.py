"""Scenario-generation agent: mutates playbooks and keeps winners."""

from __future__ import annotations

import json
import random
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_trading.options_bt.backtest import run_backtest
from agentic_trading.options_bt.data import DailyBar
from agentic_trading.options_bt.metrics import score_metrics, score_scenario_risk
from agentic_trading.options_bt.scenario import SEED_SCENARIOS, OptionScenario
from agentic_trading.options_bt.validate import ValidationReport, validate_scenario


@dataclass
class SearchConfig:
    iterations: int = 40
    target_win_rate: float = 0.60
    min_trades: int = 15
    seed: int = 42
    top_k: int = 8
    mutate_from_top: int = 4
    prefer_big_money: bool = True
    require_test_positive: bool = False
    out_dir: Path | None = None


@dataclass
class CandidateResult:
    scenario: OptionScenario
    score: float
    full_metrics: dict[str, Any]
    validation: ValidationReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.to_dict(),
            "score": self.score,
            "full_metrics": self.full_metrics,
            "validation": self.validation.to_dict(),
        }


@dataclass
class SearchResult:
    best: CandidateResult | None
    leaderboard: list[CandidateResult]
    history: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    target_win_rate: float = 0.60
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "iterations": self.iterations,
            "target_win_rate": self.target_win_rate,
            "best": self.best.to_dict() if self.best else None,
            "leaderboard": [c.to_dict() for c in self.leaderboard[:15]],
            "history": self.history,
            "notes": self.notes,
            "recommended_plan": recommend_plan(self),
        }


def _mutate(rng: random.Random, base: OptionScenario, gen: int) -> OptionScenario:
    s = OptionScenario.from_dict(base.to_dict())
    s.name = f"gen{gen}_{base.fingerprint()[:6]}_{rng.randint(100,999)}"

    # Parameter neighborhoods
    if rng.random() < 0.55:
        s.target_delta = round(
            min(0.55, max(0.25, s.target_delta + rng.choice([-0.1, -0.05, 0.0, 0.05, 0.1]))),
            2,
        )
    if rng.random() < 0.55:
        s.take_profit_pct = round(
            min(3.0, max(0.25, s.take_profit_pct + rng.choice([-0.3, -0.2, -0.1, 0.1, 0.2, 0.4]))),
            2,
        )
    if rng.random() < 0.5:
        s.stop_loss_pct = round(
            min(0.70, max(0.30, s.stop_loss_pct + rng.choice([-0.1, -0.05, 0.0, 0.05, 0.1]))),
            2,
        )
    if rng.random() < 0.45:
        s.target_dte = int(
            min(31, max(10, s.target_dte + rng.choice([-7, -3, 0, 3, 7])))
        )
        s.min_dte = max(7, s.target_dte - 7)
        s.max_dte = min(31, s.target_dte + 7)
    if rng.random() < 0.45:
        s.min_momentum_pct = round(
            max(0.0, min(0.08, s.min_momentum_pct + rng.choice([-0.015, -0.01, 0.0, 0.01, 0.015, 0.02]))),
            3,
        )
    if rng.random() < 0.35:
        s.momentum_lookback = int(rng.choice([3, 5, 8, 10, 15]))
    if rng.random() < 0.3:
        s.underlying_sma_period = int(rng.choice([5, 10, 15, 20]))
    if rng.random() < 0.25:
        s.market_sma_period = int(rng.choice([10, 20, 50]))
    if rng.random() < 0.2:
        s.require_above_sma = bool(rng.choice([True, True, False]))
    if rng.random() < 0.2:
        s.require_market_green = bool(rng.choice([True, True, False]))
    if rng.random() < 0.25:
        s.option_type = rng.choice(["call", "call", "call", "auto", "put"])
    if rng.random() < 0.3:
        s.max_hold_days = int(rng.choice([8, 12, 15, 20, 25]))
    if rng.random() < 0.25:
        s.exit_dte = int(rng.choice([2, 3, 5, 7]))
    if rng.random() < 0.2:
        s.iv_multiplier = round(rng.choice([0.95, 1.0, 1.1, 1.2, 1.3]), 2)

    # WR-seeking nudge: if base name suggests moonshot, sometimes pull TP down
    if rng.random() < 0.15:
        s.take_profit_pct = round(rng.uniform(0.35, 0.7), 2)
        s.stop_loss_pct = round(rng.uniform(0.45, 0.65), 2)
        s.target_delta = round(rng.uniform(0.40, 0.52), 2)

    # Big-money nudge: occasionally push OTM + large TP
    if rng.random() < 0.12:
        s.target_delta = round(rng.uniform(0.28, 0.38), 2)
        s.take_profit_pct = round(rng.uniform(1.0, 2.0), 2)
        s.min_momentum_pct = round(rng.uniform(0.02, 0.05), 3)

    s.notes = f"mutated from {base.name}"
    return s.clamp_live_rails()


def _crossover(rng: random.Random, a: OptionScenario, b: OptionScenario, gen: int) -> OptionScenario:
    s = OptionScenario.from_dict(a.to_dict())
    s.name = f"xover{gen}_{rng.randint(100,999)}"
    for field_name in (
        "target_delta",
        "take_profit_pct",
        "stop_loss_pct",
        "target_dte",
        "min_momentum_pct",
        "momentum_lookback",
        "option_type",
        "require_market_green",
        "max_hold_days",
    ):
        if rng.random() < 0.5:
            setattr(s, field_name, getattr(b, field_name))
    s.min_dte = max(7, s.target_dte - 7)
    s.max_dte = min(31, s.target_dte + 7)
    s.notes = f"crossover {a.name} x {b.name}"
    return s.clamp_live_rails()


def evaluate_candidate(
    scenario: OptionScenario,
    series: dict[str, list[DailyBar]],
    cfg: SearchConfig,
) -> CandidateResult:
    sc = scenario.clamp_live_rails()
    # Keep universe from search config via scenario.symbols
    bt = run_backtest(sc, series)
    val = validate_scenario(
        sc,
        series,
        target_win_rate=cfg.target_win_rate,
        require_test_positive=cfg.require_test_positive,
    )
    score = val.score
    # Extra boost if full win rate near target AND positive expectancy
    if (
        bt.metrics.win_rate >= cfg.target_win_rate - 0.05
        and bt.metrics.expectancy_usd > 0
        and bt.metrics.n_trades >= cfg.min_trades
    ):
        score += 8.0
    score = score_scenario_risk(sc, score)
    return CandidateResult(
        scenario=sc,
        score=score,
        full_metrics=bt.metrics.to_dict(),
        validation=val,
    )


def run_scenario_search(
    series: dict[str, list[DailyBar]],
    cfg: SearchConfig | None = None,
    *,
    symbols: list[str] | None = None,
    seeds: list[OptionScenario] | None = None,
) -> SearchResult:
    """
    Iterate: seed scenarios → evaluate → mutate top → re-evaluate.

    Stops after cfg.iterations evaluations (not generations).
    """
    cfg = cfg or SearchConfig()
    rng = random.Random(cfg.seed)
    notes = [
        "Agent mutates DTE/delta/TP/SL/filters under live rails (7–31 DTE, 1 contract, no 10% stops).",
        f"Objective: ~{cfg.target_win_rate:.0%} win rate + positive expectancy + big-money tilt.",
        "BS/realized-vol model — validate with paper before live.",
    ]

    pool: list[OptionScenario] = []
    base_seeds = seeds or SEED_SCENARIOS
    for s in base_seeds:
        sc = OptionScenario.from_dict(s.to_dict())
        if symbols:
            sc.symbols = list(symbols)
        pool.append(sc.clamp_live_rails())

    seen: set[str] = set()
    leaderboard: list[CandidateResult] = []
    history: list[dict[str, Any]] = []

    def consider(sc: OptionScenario) -> None:
        fp = sc.fingerprint()
        if fp in seen:
            return
        seen.add(fp)
        cand = evaluate_candidate(sc, series, cfg)
        leaderboard.append(cand)
        leaderboard.sort(key=lambda c: c.score, reverse=True)
        del leaderboard[cfg.top_k * 3 :]  # keep extra for diversity
        history.append(
            {
                "name": sc.name,
                "fingerprint": fp,
                "score": round(cand.score, 3),
                "n_trades": cand.full_metrics.get("n_trades"),
                "win_rate": cand.full_metrics.get("win_rate"),
                "total_pnl_usd": cand.full_metrics.get("total_pnl_usd"),
                "expectancy_usd": cand.full_metrics.get("expectancy_usd"),
                "test_wr": cand.validation.test.win_rate,
                "test_pnl": cand.validation.test.total_pnl_usd,
                "passes": cand.validation.passes,
            }
        )

    # Evaluate seeds first
    for sc in pool:
        consider(sc)
        if len(history) >= cfg.iterations:
            break

    gen = 1
    while len(history) < cfg.iterations:
        tops = sorted(leaderboard, key=lambda c: c.score, reverse=True)[: cfg.mutate_from_top]
        if not tops:
            break
        # Mutate
        parent = rng.choice(tops).scenario
        child = _mutate(rng, parent, gen)
        if symbols:
            child.symbols = list(symbols)
        consider(child)
        if len(history) >= cfg.iterations:
            break
        # Occasional crossover
        if len(tops) >= 2 and rng.random() < 0.35:
            a, b = rng.sample([t.scenario for t in tops], 2)
            x = _crossover(rng, a, b, gen)
            if symbols:
                x.symbols = list(symbols)
            consider(x)
        gen += 1

    leaderboard.sort(key=lambda c: c.score, reverse=True)
    # Prefer passing candidates with sane drawdown + near-target WR
    def _pick_key(c: CandidateResult) -> tuple:
        m = c.full_metrics
        wr = float(m.get("win_rate") or 0)
        dd = float(m.get("max_drawdown_pct") or 1)
        exp = float(m.get("expectancy_usd") or 0)
        n = int(m.get("n_trades") or 0)
        near_wr = 1 if wr >= cfg.target_win_rate - 0.08 else 0
        sane_dd = 1 if dd <= 0.45 else 0
        return (
            1 if c.validation.passes else 0,
            near_wr,
            sane_dd,
            c.score - 40.0 * max(0.0, dd - 0.40),  # soft DD penalty for ranking
            exp,
            n,
        )

    ranked = sorted(leaderboard, key=_pick_key, reverse=True)
    best = ranked[0] if ranked else None

    result = SearchResult(
        best=best,
        leaderboard=leaderboard[: cfg.top_k],
        history=history,
        iterations=len(history),
        target_win_rate=cfg.target_win_rate,
        notes=notes,
    )

    if cfg.out_dir:
        out = Path(cfg.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "options_search_result.json").write_text(
            json.dumps(result.to_dict(), indent=2) + "\n"
        )
        if best:
            (out / "best_scenario.json").write_text(
                json.dumps(best.scenario.to_dict(), indent=2) + "\n"
            )
            (out / "best_validation.json").write_text(
                json.dumps(best.validation.to_dict(), indent=2) + "\n"
            )
        # Markdown summary
        (out / "OPTIONS_PLAN.md").write_text(format_plan_markdown(result))

    return result


def recommend_plan(result: SearchResult) -> dict[str, Any]:
    if not result.best:
        return {
            "action": "hold_no_options",
            "reason": "No viable scenario found — do not buy options until paper edge appears.",
        }
    b = result.best
    m = b.full_metrics
    t = b.validation.test
    plan = {
        "action": "paper_then_supervised_live" if b.validation.passes else "paper_only_research",
        "scenario_name": b.scenario.name,
        "fingerprint": b.scenario.fingerprint(),
        "playbook": {
            "option_type": b.scenario.option_type,
            "target_delta": b.scenario.target_delta,
            "target_dte": b.scenario.target_dte,
            "min_dte": b.scenario.min_dte,
            "max_dte": b.scenario.max_dte,
            "take_profit_pct": b.scenario.take_profit_pct,
            "stop_loss_pct": b.scenario.stop_loss_pct,
            "exit_dte": b.scenario.exit_dte,
            "max_hold_days": b.scenario.max_hold_days,
            "min_momentum_pct": b.scenario.min_momentum_pct,
            "momentum_lookback": b.scenario.momentum_lookback,
            "require_market_green": b.scenario.require_market_green,
            "require_above_sma": b.scenario.require_above_sma,
            "premium_budget_usd": b.scenario.premium_budget_usd,
            "max_open": 1,
            "contracts": 1,
        },
        "full_sample": {
            "n_trades": m.get("n_trades"),
            "win_rate": m.get("win_rate"),
            "expectancy_usd": m.get("expectancy_usd"),
            "total_pnl_usd": m.get("total_pnl_usd"),
            "profit_factor": m.get("profit_factor"),
            "max_drawdown_pct": m.get("max_drawdown_pct"),
        },
        "out_of_sample": {
            "n_trades": t.n_trades,
            "win_rate": t.win_rate,
            "expectancy_usd": t.expectancy_usd,
            "total_pnl_usd": t.total_pnl_usd,
            "profit_factor": t.profit_factor,
        },
        "validation_passes": b.validation.passes,
        "validation_reasons": b.validation.reasons,
        "live_rules": [
            "Paper first with this scenario for ≥2 weeks of decisions.",
            "Live only on Agentic account, supervised MCP path.",
            "Never 0DTE; stay 7–31 DTE.",
            "Never use ≤10% option stops (BAC postmortem).",
            "Max 1 open long-premium idea.",
            "place_option_order only after explicit user confirm.",
        ],
        "risk_disclaimer": (
            "Simulated BS premiums ≠ real fills. Long options can go to zero. "
            "Past/sim results do not guarantee future PnL. Not financial advice."
        ),
    }
    return plan


def format_plan_markdown(result: SearchResult) -> str:
    plan = recommend_plan(result)
    lines = [
        "# Options Plan (backtest search)",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Iterations: {result.iterations}",
        f"Target win rate: {result.target_win_rate:.0%}",
        "",
        "## Recommendation",
        "",
        f"- **Action:** `{plan.get('action')}`",
        f"- **Scenario:** `{plan.get('scenario_name')}`",
        f"- **Validation passes:** {plan.get('validation_passes')}",
        "",
    ]
    if result.best:
        pb = plan.get("playbook") or {}
        lines += [
            "### Playbook parameters",
            "",
            "```yaml",
            f"option_type: {pb.get('option_type')}",
            f"target_delta: {pb.get('target_delta')}",
            f"target_dte: {pb.get('target_dte')}",
            f"dte_window: [{pb.get('min_dte')}, {pb.get('max_dte')}]",
            f"take_profit_pct: {pb.get('take_profit_pct')}  # on premium",
            f"stop_loss_pct: {pb.get('stop_loss_pct')}  # on premium",
            f"exit_dte: {pb.get('exit_dte')}",
            f"max_hold_days: {pb.get('max_hold_days')}",
            f"min_momentum_pct: {pb.get('min_momentum_pct')}",
            f"momentum_lookback: {pb.get('momentum_lookback')}",
            f"require_market_green: {pb.get('require_market_green')}",
            f"require_above_sma: {pb.get('require_above_sma')}",
            f"premium_budget_usd: {pb.get('premium_budget_usd')}",
            "max_open: 1",
            "```",
            "",
            "### Full sample",
            "",
        ]
        fs = plan.get("full_sample") or {}
        lines.append(
            f"- Trades: **{fs.get('n_trades')}** | Win rate: **{(fs.get('win_rate') or 0):.1%}** | "
            f"Expectancy: **${fs.get('expectancy_usd')}** | Total PnL: **${fs.get('total_pnl_usd')}** | "
            f"PF: **{fs.get('profit_factor')}** | Max DD: **{(fs.get('max_drawdown_pct') or 0):.1%}**"
        )
        oos = plan.get("out_of_sample") or {}
        lines += [
            "",
            "### Out-of-sample (test)",
            "",
            f"- Trades: **{oos.get('n_trades')}** | Win rate: **{(oos.get('win_rate') or 0):.1%}** | "
            f"Expectancy: **${oos.get('expectancy_usd')}** | Total PnL: **${oos.get('total_pnl_usd')}**",
            "",
            "### Validation notes",
            "",
        ]
        for r in plan.get("validation_reasons") or []:
            lines.append(f"- {r}")
        lines += ["", "### Live rails", ""]
        for r in plan.get("live_rules") or []:
            lines.append(f"- {r}")
        lines += ["", f"_{plan.get('risk_disclaimer')}_", ""]

    lines += ["## Leaderboard (top)", ""]
    for i, c in enumerate(result.leaderboard[:8], 1):
        m = c.full_metrics
        lines.append(
            f"{i}. `{c.scenario.name}` score={c.score:.1f} "
            f"WR={m.get('win_rate', 0):.1%} n={m.get('n_trades')} "
            f"PnL=${m.get('total_pnl_usd')} "
            f"test_WR={c.validation.test.win_rate:.1%} "
            f"pass={c.validation.passes}"
        )
    lines.append("")
    return "\n".join(lines)
