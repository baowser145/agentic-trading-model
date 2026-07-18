from __future__ import annotations

from typing import Any

import pandas as pd

from stock_screener_30d.config import get_universe
from stock_screener_30d.data import _safe_float, fetch_history, rsi
from stock_screener_30d.screener import composite_score, passes_criteria

try:
    from stock_screener_30d.activity_log import log as act_log
except ImportError:
    def act_log(msg, level="info", source="backtest"):  # noqa: ARG001
        pass


def _metrics_at_date(
    close: pd.Series, volume: pd.Series, idx: int, screening: dict
) -> dict | None:
    if idx < 60:
        return None
    window_close = close.iloc[: idx + 1]
    window_vol = volume.iloc[: idx + 1]
    latest = _safe_float(window_close.iloc[-1])
    window_52w = window_close.tail(min(252, len(window_close)))
    high_52w = _safe_float(window_52w.max())
    sma_days = screening.get("above_sma_days", 50)
    sma = _safe_float(window_close.rolling(sma_days).mean().iloc[-1])
    rsi_val = _safe_float(rsi(window_close).iloc[-1])
    avg_vol = _safe_float(window_vol.tail(20).mean())
    if None in (latest, high_52w, sma, rsi_val, avg_vol) or high_52w <= 0:
        return None
    pullback = ((high_52w - latest) / high_52w) * 100
    return {
        "price": latest,
        "rsi": rsi_val,
        "above_sma": latest > sma,
        "pullback_pct": pullback,
        "avg_volume": avg_vol,
    }


def run_backtest(config: dict[str, Any], tickers: list[str] | None = None) -> dict[str, Any]:
    bt = config["backtest"]
    screening = config["screening"]
    top_n = max(1, config["output"]["top_n"])
    hold_days = bt["hold_days"]
    cost_pct = bt["round_trip_cost_pct"]
    start = f"{bt['start_year']}-01-01"
    end = f"{bt['end_year']}-12-31"
    universe = tickers if tickers is not None else get_universe(config)

    act_log(f"Fetching benchmark {bt['benchmark']} ({start} → {end})", source="backtest")
    spy = fetch_history(bt["benchmark"], start, end)
    if spy is None or len(spy) < hold_days + 60:
        act_log(f"Failed to load benchmark {bt['benchmark']}", level="error", source="backtest")
        return {"error": f"Could not fetch benchmark {bt['benchmark']}"}

    trade_returns: list[float] = []
    spy_monthly = spy.resample("ME").last().dropna()
    rebalance_dates = list(spy_monthly.index[:-1])
    act_log(f"Simulating {len(rebalance_dates)} monthly rebalances across {len(universe)} tickers", source="backtest")

    for i, rebalance_date in enumerate(rebalance_dates, 1):
        if i == 1 or i % 12 == 0 or i == len(rebalance_dates):
            act_log(f"Rebalance {i}/{len(rebalance_dates)} — {rebalance_date.strftime('%Y-%m')}", source="backtest")
        month_picks: list[float] = []

        for ticker in universe:
            try:
                import yfinance as yf
                hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
            except Exception:
                continue
            if hist is None or hist.empty:
                continue

            close = hist["Close"]
            volume = hist["Volume"]
            mask = close.index <= rebalance_date
            if mask.sum() < 60:
                continue
            idx = int(mask.sum()) - 1
            metrics = _metrics_at_date(close, volume, idx, screening)
            if metrics is None or not passes_criteria(metrics, screening):
                continue

            future = close.iloc[idx:]
            if len(future) <= hold_days:
                continue
            entry = float(future.iloc[0])
            exit_price = float(future.iloc[hold_days])
            gross_ret = (exit_price - entry) / entry * 100
            net_ret = gross_ret - cost_pct
            metrics["score"] = composite_score(metrics, screening)
            month_picks.append((net_ret, metrics["score"]))

        if month_picks:
            # Match scan: top N by composite score, equal-weight
            month_picks.sort(key=lambda x: x[1], reverse=True)
            top = month_picks[:top_n]
            avg_ret = sum(r for r, _ in top) / len(top)
            trade_returns.append(avg_ret)
        else:
            # Cash month — record 0% return
            trade_returns.append(0.0)

    bench_returns: list[float] = []
    for rebalance_date in rebalance_dates:
        mask = spy.index <= rebalance_date
        if mask.sum() < 1:
            continue
        idx = int(mask.sum()) - 1
        future = spy.iloc[idx:]
        if len(future) <= hold_days:
            continue
        entry = float(future.iloc[0])
        exit_price = float(future.iloc[hold_days])
        gross = (exit_price - entry) / entry * 100
        bench_returns.append(gross - cost_pct)

    if not trade_returns or not bench_returns:
        return {"error": "Insufficient trades generated for backtest"}

    strategy_avg = sum(trade_returns) / len(trade_returns)
    bench_avg = sum(bench_returns) / len(bench_returns)
    strategy_annual = strategy_avg * 12
    bench_annual = bench_avg * 12
    beats = strategy_annual > bench_annual
    periods_won = sum(1 for r in trade_returns if r > 0)
    win_rate = periods_won / len(trade_returns) * 100 if trade_returns else 0
    act_log(
        f"Done — strategy {strategy_annual:+.2f}% ann. vs SPY {bench_annual:+.2f}% "
        f"({'BEATS' if beats else 'LOSES'})",
        level="success" if beats else "warn",
        source="backtest",
    )

    return {
        "period": f"{bt['start_year']}-{bt['end_year']}",
        "hold_days": hold_days,
        "round_trip_cost_pct": cost_pct,
        "num_rebalances": len(trade_returns),
        "strategy_avg_per_period_pct": round(strategy_avg, 2),
        "benchmark_avg_per_period_pct": round(bench_avg, 2),
        "strategy_annualized_pct": round(strategy_annual, 2),
        "benchmark_annualized_pct": round(bench_annual, 2),
        "excess_annualized_pct": round(strategy_annual - bench_annual, 2),
        "beats_benchmark": beats,
        "win_rate_pct": round(win_rate, 1),
        "periods_won": periods_won,
        "periods_total": len(trade_returns),
    }