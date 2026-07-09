#!/usr/bin/env python3
"""Test whether adding a stop-loss to the validated PEAD (earnings-gap + 20-trading-day-hold)
strategy improves or hurts results, vs. the no-stop baseline already validated in
report_pead_earnings_gap.md.

Uses only already-cached data (earnings dates + 3y daily bars) -- no new Robinhood MCP calls.

For each stop level tested: if any day's Low during the hold breaches entry_price*(1-stop_pct)
before day 20, exit that day at the stop price (conservative fill assumption); otherwise exit at
day 20's Close, exactly as the validated no-stop strategy does. Same 5bps/side slippage throughout.
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
GAP_PCT_MIN = 5.0
PRICE_MIN = 3.0
VOLUME_MIN = 500_000
HOLD_DAYS = 20
STOP_LEVELS = [None, 0.05, 0.08, 0.10, 0.15]  # None = no stop (baseline)

with open(CACHE_DIR / "sp500_earnings_dates_2y.json") as f:
    earnings = json.load(f)

earnings_by_ticker = defaultdict(set)
for e in earnings:
    earnings_by_ticker[e["ticker"]].add(pd.Timestamp(e["date"]).normalize())

tickers = sorted(earnings_by_ticker.keys())

# Find all qualifying entries once (shared across stop levels)
entries = []  # (ticker, entry_idx_in_df, entry_price)
ticker_dfs = {}

for ticker in tickers:
    cache_file = CACHE_DIR / f"{ticker}_3y.csv"
    if not cache_file.exists():
        continue
    df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
    if len(df) < 10:
        continue
    df = df.sort_index()
    df.index = df.index.tz_localize(None) if df.index.tz is not None else df.index
    df["prev_close"] = df["Close"].shift(1)
    ticker_dfs[ticker] = df

    edates = earnings_by_ticker[ticker]
    for i in range(1, len(df) - HOLD_DAYS - 1):
        row = df.iloc[i]
        if pd.isna(row["prev_close"]) or row["prev_close"] <= 0:
            continue
        gap_pct = (row["Open"] - row["prev_close"]) / row["prev_close"] * 100
        if not (gap_pct >= GAP_PCT_MIN and row["Close"] >= PRICE_MIN and row["Volume"] >= VOLUME_MIN):
            continue
        day = df.index[i].normalize()
        if not any((day - ed).days in (0, 1, 2, 3) for ed in edates):
            continue
        entries.append((ticker, i))

print(f"Found {len(entries)} qualifying entries across {len(set(t for t, _ in entries))} tickers\n")

results = {}
for stop_pct in STOP_LEVELS:
    trade_returns = []
    per_ticker_returns = defaultdict(list)
    stopped_out_count = 0

    for ticker, i in entries:
        df = ticker_dfs[ticker]
        entry_price = df.iloc[i + 1]["Open"]
        stop_price = entry_price * (1 - stop_pct) if stop_pct is not None else None

        exit_price = None
        if stop_pct is not None:
            for k in range(1, HOLD_DAYS + 1):
                day_row = df.iloc[i + 1 + k] if (i + 1 + k) < len(df) else None
                if day_row is None:
                    break
                if day_row["Low"] <= stop_price:
                    exit_price = stop_price
                    stopped_out_count += 1
                    break
        if exit_price is None:
            exit_idx = i + 1 + HOLD_DAYS
            if exit_idx >= len(df):
                continue
            exit_price = df.iloc[exit_idx]["Close"]

        raw_return = (exit_price - entry_price) / entry_price
        net_return = raw_return - 2 * (SLIPPAGE_BPS / 10_000)
        trade_returns.append(net_return)
        per_ticker_returns[ticker].append(net_return)

    trade_returns = np.array(trade_returns)
    t_stat, p_value = stats.ttest_1samp(trade_returns, 0)
    per_ticker_total = [np.prod([1 + r for r in rets]) - 1 for rets in per_ticker_returns.values()]

    label = "No stop (baseline)" if stop_pct is None else f"-{stop_pct:.0%} stop"
    results[label] = {
        "n_trades": len(trade_returns),
        "stopped_out": stopped_out_count,
        "mean_return": trade_returns.mean(),
        "std_return": trade_returns.std(ddof=1),
        "win_rate": (trade_returns > 0).mean(),
        "worst_trade": trade_returns.min(),
        "p_value": p_value,
        "per_ticker_avg_total": np.mean(per_ticker_total),
    }

print(f"{'Variant':<20} {'Trades':>7} {'StoppedOut':>10} {'Mean/trade':>11} {'Std':>8} {'WinRate':>8} {'Worst':>8} {'p-value':>9}")
for label, r in results.items():
    print(f"{label:<20} {r['n_trades']:>7} {r['stopped_out']:>10} {r['mean_return']:>10.3%} {r['std_return']:>7.2%} {r['win_rate']:>7.1%} {r['worst_trade']:>7.2%} {r['p_value']:>9.5f}")

with open(RESULTS_DIR / "pead_stoploss_results.json", "w") as f:
    json.dump({k: {kk: (vv if not isinstance(vv, (np.floating, np.integer)) else float(vv)) for kk, vv in v.items()} for k, v in results.items()}, f, indent=2)
