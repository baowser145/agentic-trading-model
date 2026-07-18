#!/usr/bin/env python3
"""Backtest ONLY the testable half of the 'Value + Catalyst' scanner: does buying a stock some
days before its earnings report, and exiting before the report (sidestepping earnings-day risk),
have an edge? This does NOT test the analyst-sentiment/price-target filters -- there is no
historical data source for those (no tool gives point-in-time-past analyst targets/Buy%, and the
scanner itself gets that via live web search, which only reflects the present).

Reuses the same cached data as the validated PEAD backtest: S&P 500 3y daily bars
(backtest/cache/{ticker}_3y.csv) and the confirmed 2-year earnings-date cache
(backtest/cache/sp500_earnings_dates_2y.json). No new network calls.
"""
import json
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy import stats

CACHE_DIR = Path(__file__).parent / "cache"
RESULTS_DIR = Path(__file__).parent / "results"  # analysis outputs, tracked in git (cache/ is not)
RESULTS_DIR.mkdir(exist_ok=True)
SLIPPAGE_BPS = 5.0
TEST_START = pd.Timestamp("2024-07-06")
TEST_END = pd.Timestamp("2026-07-06")
LEAD_DAYS_VARIANTS = [3, 5, 7, 10, 14, 18, 21, 25, 30, 40, 50, 60]  # calendar days before earnings to enter

with open(CACHE_DIR / "sp500_earnings_dates_2y.json") as f:
    earnings = json.load(f)

earnings_by_ticker = defaultdict(list)
for e in earnings:
    earnings_by_ticker[e["ticker"]].append(pd.Timestamp(e["date"]).normalize())

tickers = sorted(earnings_by_ticker.keys())
print(f"Testing {len(tickers)} tickers with earnings data\n")

ticker_dfs = {}
for ticker in tickers:
    cache_file = CACHE_DIR / f"{ticker}_3y.csv"
    if not cache_file.exists():
        continue
    df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
    if len(df) < 10:
        continue
    df = df.sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    ticker_dfs[ticker] = df

results_by_variant = {}

for lead_days in LEAD_DAYS_VARIANTS:
    trade_returns = []
    per_ticker_returns = defaultdict(list)

    for ticker, edates in earnings_by_ticker.items():
        if ticker not in ticker_dfs:
            continue
        df = ticker_dfs[ticker]
        idx = df.index

        for ed in edates:
            if ed < TEST_START or ed > TEST_END:
                continue

            target_entry_date = ed - pd.Timedelta(days=lead_days)
            # entry = first trading day on/after target_entry_date
            entry_candidates = idx[idx >= target_entry_date]
            if len(entry_candidates) == 0:
                continue
            entry_date = entry_candidates[0]
            if entry_date >= ed:
                continue  # lead window collapsed past the report itself

            # exit = last trading day strictly before the earnings report date
            exit_candidates = idx[idx < ed]
            if len(exit_candidates) == 0:
                continue
            exit_date = exit_candidates[-1]
            if exit_date <= entry_date:
                continue  # no room between entry and report

            entry_price = df.loc[entry_date, "Open"]
            exit_price = df.loc[exit_date, "Close"]
            if entry_price <= 0:
                continue

            raw_return = (exit_price - entry_price) / entry_price
            net_return = raw_return - 2 * (SLIPPAGE_BPS / 10_000)
            trade_returns.append(net_return)
            per_ticker_returns[ticker].append(net_return)

    trade_returns = np.array(trade_returns)
    if len(trade_returns) == 0:
        continue
    t_stat, p_value = stats.ttest_1samp(trade_returns, 0)
    per_ticker_total = [np.prod([1 + r for r in rets]) - 1 for rets in per_ticker_returns.values()]

    results_by_variant[lead_days] = {
        "n_trades": len(trade_returns),
        "n_tickers": len(per_ticker_returns),
        "mean_return": float(trade_returns.mean()),
        "std_return": float(trade_returns.std(ddof=1)),
        "win_rate": float((trade_returns > 0).mean()),
        "worst": float(trade_returns.min()),
        "best": float(trade_returns.max()),
        "p_value": float(p_value),
        "per_ticker_avg_total": float(np.mean(per_ticker_total)),
    }

print(f"{'Lead days':>10} {'Trades':>7} {'Tickers':>8} {'Mean/trade':>11} {'Std':>8} {'WinRate':>8} {'Worst':>8} {'Best':>8} {'p-value':>10}")
for lead_days, r in results_by_variant.items():
    print(f"{lead_days:>10} {r['n_trades']:>7} {r['n_tickers']:>8} {r['mean_return']:>10.3%} {r['std_return']:>7.2%} {r['win_rate']:>7.1%} {r['worst']:>7.2%} {r['best']:>7.2%} {r['p_value']:>10.6f}")

with open(RESULTS_DIR / "pre_earnings_timing_results.json", "w") as f:
    json.dump(results_by_variant, f, indent=2)
print(f"\nSaved to results/pre_earnings_timing_results.json")
