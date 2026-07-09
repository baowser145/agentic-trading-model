#!/usr/bin/env python3
"""Control test for backtest_pre_earnings_timing.py: is the pre-earnings return actually special,
or just generic drift from holding an S&P 500 stock for ~N days during a strong bull market?

For each ticker and each lead_days variant, sample random (non-earnings-anchored) holding periods
of the SAME LENGTH (in trading days) as the real pre-earnings trades, same count per ticker as that
ticker had real trades, same slippage. Compare mean return to the real pre-earnings result.
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
LEAD_DAYS_VARIANTS = [3, 5, 7, 10, 14, 18, 21, 25, 30, 40, 50, 60]
N_RANDOM_SAMPLES_PER_REAL_TRADE = 20  # draw many random controls per real trade to reduce noise
RNG_SEED = 42

with open(CACHE_DIR / "sp500_earnings_dates_2y.json") as f:
    earnings = json.load(f)

earnings_by_ticker = defaultdict(list)
for e in earnings:
    earnings_by_ticker[e["ticker"]].append(pd.Timestamp(e["date"]).normalize())

ticker_dfs = {}
for ticker in earnings_by_ticker:
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

rng = np.random.default_rng(RNG_SEED)
results_by_variant = {}

for lead_days in LEAD_DAYS_VARIANTS:
    real_trading_day_lengths = []  # trading-day length of each real trade, per ticker
    per_ticker_trade_lengths = defaultdict(list)

    for ticker, edates in earnings_by_ticker.items():
        if ticker not in ticker_dfs:
            continue
        df = ticker_dfs[ticker]
        idx = df.index

        for ed in edates:
            if ed < TEST_START or ed > TEST_END:
                continue
            target_entry_date = ed - pd.Timedelta(days=lead_days)
            entry_candidates = idx[idx >= target_entry_date]
            if len(entry_candidates) == 0:
                continue
            entry_date = entry_candidates[0]
            if entry_date >= ed:
                continue
            exit_candidates = idx[idx < ed]
            if len(exit_candidates) == 0:
                continue
            exit_date = exit_candidates[-1]
            if exit_date <= entry_date:
                continue
            entry_pos = idx.get_loc(entry_date)
            exit_pos = idx.get_loc(exit_date)
            trading_day_length = exit_pos - entry_pos
            if trading_day_length <= 0:
                continue
            per_ticker_trade_lengths[ticker].append(trading_day_length)

    # Now draw random control trades: same ticker, same trade length, random start point in the
    # test window, many samples per real trade.
    control_returns = []
    for ticker, lengths in per_ticker_trade_lengths.items():
        df = ticker_dfs[ticker]
        idx = df.index
        in_window = idx[(idx >= TEST_START) & (idx <= TEST_END)]
        if len(in_window) < 5:
            continue
        for length in lengths:
            max_start_pos = len(idx) - 1 - length
            # restrict candidate starts to positions whose date falls in the test window
            window_positions = [idx.get_loc(d) for d in in_window]
            valid_starts = [p for p in window_positions if 0 <= p <= max_start_pos]
            if not valid_starts:
                continue
            sample_starts = rng.choice(valid_starts, size=min(N_RANDOM_SAMPLES_PER_REAL_TRADE, len(valid_starts)), replace=True)
            for start_pos in sample_starts:
                entry_price = df.iloc[start_pos]["Open"]
                exit_price = df.iloc[start_pos + length]["Close"]
                if entry_price <= 0:
                    continue
                raw_return = (exit_price - entry_price) / entry_price
                net_return = raw_return - 2 * (SLIPPAGE_BPS / 10_000)
                control_returns.append(net_return)

    control_returns = np.array(control_returns)
    if len(control_returns) == 0:
        continue
    results_by_variant[lead_days] = {
        "n_control_samples": len(control_returns),
        "mean_return": float(control_returns.mean()),
        "std_return": float(control_returns.std(ddof=1)),
        "win_rate": float((control_returns > 0).mean()),
    }

print(f"{'Lead days':>10} {'Samples':>9} {'Mean/period':>12} {'Std':>8} {'WinRate':>8}")
for lead_days, r in results_by_variant.items():
    print(f"{lead_days:>10} {r['n_control_samples']:>9} {r['mean_return']:>11.3%} {r['std_return']:>7.2%} {r['win_rate']:>7.1%}")

with open(RESULTS_DIR / "pre_earnings_control_results.json", "w") as f:
    json.dump(results_by_variant, f, indent=2)
print("\nSaved to results/pre_earnings_control_results.json")
