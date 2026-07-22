"""Long-premium options backtest engine (BS premium path on daily bars)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from agentic_trading.options_bt.data import DailyBar, bar_on, closes_up_to
from agentic_trading.options_bt.metrics import OptionTrade, TradeMetrics, summarize_trades
from agentic_trading.options_bt.pricing import (
    black_scholes,
    momentum_pct,
    realized_vol,
    sma,
    strike_for_target_delta,
)
from agentic_trading.options_bt.scenario import OptionScenario


@dataclass
class OpenOption:
    symbol: str
    option_type: str
    strike: float
    entry_day: date
    expiry: date
    dte_entry: int
    entry_premium: float
    contracts: int
    entry_spot: float
    iv_entry: float
    delta_entry: float


@dataclass
class BacktestResult:
    scenario: OptionScenario
    trades: list[OptionTrade]
    metrics: TradeMetrics
    notes: list[str] = field(default_factory=list)
    train_metrics: TradeMetrics | None = None
    test_metrics: TradeMetrics | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.to_dict(),
            "metrics": self.metrics.to_dict(),
            "n_trades": len(self.trades),
            "trades": [t.to_dict() for t in self.trades],
            "notes": self.notes,
            "train_metrics": self.train_metrics.to_dict() if self.train_metrics else None,
            "test_metrics": self.test_metrics.to_dict() if self.test_metrics else None,
        }


def _next_friday_on_or_after(d: date) -> date:
    # Friday = 4
    add = (4 - d.weekday()) % 7
    return d + timedelta(days=add)


def _expiry_for_dte(entry: date, target_dte: int) -> date:
    """Pick Friday expiry nearest target DTE (not before min typical)."""
    target = entry + timedelta(days=target_dte)
    # walk to Friday on or after target-3
    exp = _next_friday_on_or_after(target - timedelta(days=3))
    if exp <= entry:
        exp = _next_friday_on_or_after(entry + timedelta(days=7))
    return exp


def _market_green(spy_closes: list[float], period: int) -> bool:
    s = sma(spy_closes, period)
    if s is None or not spy_closes:
        return False
    return spy_closes[-1] > s


def _entry_ok(
    scenario: OptionScenario,
    *,
    und_closes: list[float],
    spy_closes: list[float],
    option_type: str,
) -> tuple[bool, str]:
    if scenario.require_market_green:
        green = _market_green(spy_closes, scenario.market_sma_period)
        if option_type == "call" and not green:
            return False, "market_red_for_call"
        if option_type == "put" and green:
            return False, "market_green_for_put"
    mom = momentum_pct(und_closes, scenario.momentum_lookback)
    if mom is None:
        return False, "insufficient_history"
    if option_type == "call" and mom < scenario.min_momentum_pct:
        return False, "momentum_too_weak"
    if option_type == "put" and mom > -scenario.min_momentum_pct:
        return False, "momentum_not_bearish"
    if scenario.require_above_sma:
        s = sma(und_closes, scenario.underlying_sma_period)
        if s is None:
            return False, "no_sma"
        if option_type == "call" and und_closes[-1] < s:
            return False, "below_sma"
        if option_type == "put" and und_closes[-1] > s:
            return False, "above_sma"
    return True, "ok"


def _mark_premium(
    spot: float,
    strike: float,
    day: date,
    expiry: date,
    iv: float,
    option_type: str,
    rate: float,
) -> float:
    dte = (expiry - day).days
    t = max(dte, 0) / 365.0
    if dte <= 0:
        if option_type == "call":
            return max(0.0, spot - strike)
        return max(0.0, strike - spot)
    return black_scholes(spot, strike, t, iv, rate=rate, option_type=option_type).premium


def run_backtest(
    scenario: OptionScenario,
    series: dict[str, list[DailyBar]],
    *,
    start: date | None = None,
    end: date | None = None,
) -> BacktestResult:
    """
    Simulate long premium trades day-by-day.

    Assumptions (research model — not broker truth):
    - Premium = Black-Scholes with IV = realized_vol * iv_multiplier
    - Enter/exit at same-day close mark (no bid/ask slip modeled)
    - Max one open position (playbook)
    - One new entry attempt per day across universe (picks strongest momentum)
    """
    sc = OptionScenario.from_dict(scenario.to_dict()).clamp_live_rails()
    notes: list[str] = [
        "BS premium path with realized-vol IV proxy; not historical option quotes.",
        "No bid/ask spread or assignment modeling.",
        f"Rails: DTE {sc.min_dte}-{sc.max_dte}, max_open={sc.max_open}, contracts={sc.contracts}.",
    ]

    mkt = sc.market_symbol.upper()
    if mkt not in series:
        return BacktestResult(sc, [], summarize_trades([], starting_equity=sc.starting_equity), notes + [f"missing market {mkt}"])

    symbols = [s.upper() for s in sc.symbols if s.upper() in series and s.upper() != mkt]
    if not symbols:
        return BacktestResult(sc, [], summarize_trades([], starting_equity=sc.starting_equity), notes + ["no tradeable symbols"])

    # Build day calendar from market
    days = [b.day for b in series[mkt]]
    if start:
        days = [d for d in days if d >= start]
    if end:
        days = [d for d in days if d <= end]
    if len(days) < 40:
        return BacktestResult(sc, [], summarize_trades([], starting_equity=sc.starting_equity), notes + ["too few days"])

    open_pos: OpenOption | None = None
    trades: list[OptionTrade] = []
    last_entry_day: date | None = None
    # Pre-index bars
    by_sym = {sym: series[sym] for sym in symbols + [mkt]}

    warmup = max(sc.market_sma_period, sc.underlying_sma_period, sc.momentum_lookback, sc.iv_window) + 5

    for i, day in enumerate(days):
        if i < warmup:
            continue

        spy_closes = closes_up_to(by_sym[mkt], day)
        if len(spy_closes) < warmup:
            continue

        # Mark / exit open
        if open_pos is not None:
            und_bar = bar_on(by_sym[open_pos.symbol], day)
            if und_bar is None:
                continue
            und_closes = closes_up_to(by_sym[open_pos.symbol], day)
            iv = realized_vol(und_closes, sc.iv_window) * sc.iv_multiplier
            mark = _mark_premium(
                und_bar.close,
                open_pos.strike,
                day,
                open_pos.expiry,
                iv,
                open_pos.option_type,
                sc.risk_free_rate,
            )
            dte = (open_pos.expiry - day).days
            hold = (day - open_pos.entry_day).days
            pnl_pct = (mark / open_pos.entry_premium - 1.0) if open_pos.entry_premium > 1e-9 else -1.0

            reason = None
            if pnl_pct >= sc.take_profit_pct:
                reason = "take_profit"
            elif pnl_pct <= -sc.stop_loss_pct:
                reason = "stop_loss"
            elif dte <= sc.exit_dte:
                reason = "exit_dte"
            elif hold >= sc.max_hold_days:
                reason = "max_hold"
            elif dte <= 0:
                reason = "expiry"

            if reason:
                paid = open_pos.entry_premium * 100 * open_pos.contracts
                got = mark * 100 * open_pos.contracts
                pnl = got - paid
                trades.append(
                    OptionTrade(
                        symbol=open_pos.symbol,
                        option_type=open_pos.option_type,
                        entry_day=open_pos.entry_day.isoformat(),
                        exit_day=day.isoformat(),
                        strike=open_pos.strike,
                        dte_entry=open_pos.dte_entry,
                        dte_exit=max(dte, 0),
                        entry_premium=round(open_pos.entry_premium, 4),
                        exit_premium=round(mark, 4),
                        contracts=open_pos.contracts,
                        premium_paid_usd=round(paid, 2),
                        premium_exit_usd=round(got, 2),
                        pnl_usd=round(pnl, 2),
                        pnl_pct=round(pnl_pct, 4),
                        hold_days=hold,
                        exit_reason=reason,
                        entry_spot=open_pos.entry_spot,
                        exit_spot=und_bar.close,
                        iv_entry=open_pos.iv_entry,
                        delta_entry=open_pos.delta_entry,
                    )
                )
                open_pos = None

        # Entry
        if open_pos is not None:
            continue
        if last_entry_day is not None and (day - last_entry_day).days < sc.entry_cooldown_days:
            continue

        # Resolve option type for auto
        green = _market_green(spy_closes, sc.market_sma_period)
        if sc.option_type == "auto":
            ot = "call" if green else "put"
        else:
            ot = sc.option_type.lower()

        # Rank candidates by |momentum|
        candidates: list[tuple[float, str, list[float], DailyBar]] = []
        for sym in symbols:
            bar = bar_on(by_sym[sym], day)
            if bar is None:
                continue
            closes = closes_up_to(by_sym[sym], day)
            ok, _why = _entry_ok(sc, und_closes=closes, spy_closes=spy_closes, option_type=ot)
            if not ok:
                continue
            mom = momentum_pct(closes, sc.momentum_lookback) or 0.0
            score = abs(mom)
            candidates.append((score, sym, closes, bar))

        if not candidates:
            continue
        candidates.sort(key=lambda x: x[0], reverse=True)
        _score, sym, closes, bar = candidates[0]

        expiry = _expiry_for_dte(day, sc.target_dte)
        dte = (expiry - day).days
        if dte < sc.min_dte or dte > sc.max_dte:
            # adjust to mid window Friday
            mid = (sc.min_dte + sc.max_dte) // 2
            expiry = _expiry_for_dte(day, mid)
            dte = (expiry - day).days
            if dte < sc.min_dte or dte > sc.max_dte:
                continue

        iv = realized_vol(closes, sc.iv_window) * sc.iv_multiplier
        t_years = dte / 365.0
        strike = strike_for_target_delta(
            bar.close, t_years, iv, sc.target_delta, option_type=ot, rate=sc.risk_free_rate
        )
        bs = black_scholes(bar.close, strike, t_years, iv, rate=sc.risk_free_rate, option_type=ot)
        prem = bs.premium
        if prem < 0.05:
            continue
        # Budget: contracts already 1; skip if premium * 100 > budget * 1.5
        cost = prem * 100 * sc.contracts
        if cost > sc.premium_budget_usd * 1.5:
            # try slightly farther OTM (lower delta)
            strike2 = strike_for_target_delta(
                bar.close,
                t_years,
                iv,
                max(0.25, sc.target_delta - 0.1),
                option_type=ot,
                rate=sc.risk_free_rate,
            )
            bs = black_scholes(bar.close, strike2, t_years, iv, rate=sc.risk_free_rate, option_type=ot)
            prem = bs.premium
            strike = strike2
            cost = prem * 100 * sc.contracts
            if cost > sc.premium_budget_usd * 1.5 or prem < 0.05:
                continue

        open_pos = OpenOption(
            symbol=sym,
            option_type=ot,
            strike=strike,
            entry_day=day,
            expiry=expiry,
            dte_entry=dte,
            entry_premium=prem,
            contracts=sc.contracts,
            entry_spot=bar.close,
            iv_entry=iv,
            delta_entry=bs.delta,
        )
        last_entry_day = day

    # Force close any open at last day
    if open_pos is not None:
        day = days[-1]
        und_bar = bar_on(by_sym[open_pos.symbol], day)
        if und_bar:
            und_closes = closes_up_to(by_sym[open_pos.symbol], day)
            iv = realized_vol(und_closes, sc.iv_window) * sc.iv_multiplier
            mark = _mark_premium(
                und_bar.close,
                open_pos.strike,
                day,
                open_pos.expiry,
                iv,
                open_pos.option_type,
                sc.risk_free_rate,
            )
            dte = (open_pos.expiry - day).days
            hold = (day - open_pos.entry_day).days
            pnl_pct = (mark / open_pos.entry_premium - 1.0) if open_pos.entry_premium > 1e-9 else -1.0
            paid = open_pos.entry_premium * 100 * open_pos.contracts
            got = mark * 100 * open_pos.contracts
            trades.append(
                OptionTrade(
                    symbol=open_pos.symbol,
                    option_type=open_pos.option_type,
                    entry_day=open_pos.entry_day.isoformat(),
                    exit_day=day.isoformat(),
                    strike=open_pos.strike,
                    dte_entry=open_pos.dte_entry,
                    dte_exit=max(dte, 0),
                    entry_premium=round(open_pos.entry_premium, 4),
                    exit_premium=round(mark, 4),
                    contracts=open_pos.contracts,
                    premium_paid_usd=round(paid, 2),
                    premium_exit_usd=round(got, 2),
                    pnl_usd=round(got - paid, 2),
                    pnl_pct=round(pnl_pct, 4),
                    hold_days=hold,
                    exit_reason="end_of_data",
                    entry_spot=open_pos.entry_spot,
                    exit_spot=und_bar.close,
                    iv_entry=open_pos.iv_entry,
                    delta_entry=open_pos.delta_entry,
                )
            )

    metrics = summarize_trades(trades, starting_equity=sc.starting_equity)
    return BacktestResult(scenario=sc, trades=trades, metrics=metrics, notes=notes)
