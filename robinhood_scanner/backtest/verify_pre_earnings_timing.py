#!/usr/bin/env python3
"""Independent re-implementation of backtest_pre_earnings_timing.py + backtest_pre_earnings_control.py,
written fresh (vectorized pandas approach instead of the original's manual index-scanning loop) to
catch any implementation-specific bugs, same way verify_pead.py cross-checked the PEAD result.

Tests whether buying N days before an S&P 500 stock's earnings report and selling the day before the
report has an edge beyond generic drift, focusing on the 18-30 day window flagged as the peak in the
first pass, with flanking points for context.
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
LEAD_DAYS_VARIANTS = [14, 18, 21, 25, 30, 40]
N_CONTROL_PER_TRADE = 15
RNG_SEED = 7  # different seed from the original control script, deliberately

with open(CACHE_DIR / "sp500_earnings_dates_2y.json") as f:
    earnings = json.load(f)

earnings_by_ticker = defaultdict(list)
for e in earnings:
    earnings_by_ticker[e["ticker"]].append(pd.Timestamp(e["date"]).normalize())

# Load all price data once, indexed for fast position lookups
price_data = {}
for ticker in earnings_by_ticker:
    f = CACHE_DIR / f"{ticker}_3y.csv"
    if not f.exists():
        continue
    df = pd.read_csv(f, index_col=0, parse_dates=True).sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    if len(df) < 10:
        continue
    price_data[ticker] = df

print(f"Loaded price data for {len(price_data)} tickers")

rng = np.random.default_rng(RNG_SEED)


def find_trade(df, idx_array, earnings_day, lead_days):
    """Vectorized-ish: use searchsorted on the date index instead of boolean masking."""
    target = earnings_day - pd.Timedelta(days=lead_days)
    entry_pos = idx_array.searchsorted(target, side="left")
    if entry_pos >= len(idx_array):
        return None
    entry_date = idx_array[entry_pos]
    if entry_date >= earnings_day:
        return None
    exit_pos = idx_array.searchsorted(earnings_day, side="left") - 1
    if exit_pos < 0 or exit_pos <= entry_pos:
        return None
    return entry_pos, exit_pos


results = {}
for lead_days in LEAD_DAYS_VARIANTS:
    real_returns = []
    control_returns = []
    real_per_ticker = defaultdict(list)

    for ticker, edates in earnings_by_ticker.items():
        if ticker not in price_data:
            continue
        df = price_data[ticker]
        idx_array = df.index.values.astype("datetime64[ns]")
        idx_array = pd.DatetimeIndex(idx_array)
        n = len(df)

        for ed in edates:
            if ed < TEST_START or ed > TEST_END:
                continue
            trade = find_trade(df, idx_array, ed, lead_days)
            if trade is None:
                continue
            entry_pos, exit_pos = trade
            entry_price = df.iloc[entry_pos]["Open"]
            exit_price = df.iloc[exit_pos]["Close"]
            if entry_price <= 0:
                continue
            ret = (exit_price - entry_price) / entry_price - 2 * (SLIPPAGE_BPS / 10_000)
            real_returns.append(ret)
            real_per_ticker[ticker].append(ret)

            trade_len = exit_pos - entry_pos
            # random control: same ticker, same trade length, random start within test window
            window_mask = (df.index >= TEST_START) & (df.index <= TEST_END)
            window_positions = np.where(window_mask)[0]
            max_start = n - 1 - trade_len
            valid = window_positions[window_positions <= max_start]
            if len(valid) == 0:
                continue
            starts = rng.choice(valid, size=min(N_CONTROL_PER_TRADE, len(valid)), replace=True)
            for s in starts:
                cp_entry = df.iloc[s]["Open"]
                cp_exit = df.iloc[s + trade_len]["Close"]
                if cp_entry <= 0:
                    continue
                control_returns.append((cp_exit - cp_entry) / cp_entry - 2 * (SLIPPAGE_BPS / 10_000))

    real_returns = np.array(real_returns)
    control_returns = np.array(control_returns)
    if len(real_returns) == 0:
        continue
    t_stat, p_val = stats.ttest_1samp(real_returns, 0)
    per_ticker_total = [np.prod([1 + r for r in rets]) - 1 for rets in real_per_ticker.values()]

    results[lead_days] = {
        "n_trades": len(real_returns),
        "n_tickers": len(real_per_ticker),
        "real_mean": float(real_returns.mean()),
        "real_win_rate": float((real_returns > 0).mean()),
        "real_p_value": float(p_val),
        "control_mean": float(control_returns.mean()) if len(control_returns) else None,
        "control_win_rate": float((control_returns > 0).mean()) if len(control_returns) else None,
        "incremental_edge": float(real_returns.mean() - control_returns.mean()) if len(control_returns) else None,
        "per_ticker_avg_total": float(np.mean(per_ticker_total)),
    }

print(f"\n{'Lead':>5} {'Trades':>7} {'RealMean':>9} {'CtrlMean':>9} {'Incr.Edge':>10} {'RealWin':>8} {'CtrlWin':>8} {'p-value':>10}")
for lead_days, r in results.items():
    print(f"{lead_days:>5} {r['n_trades']:>7} {r['real_mean']:>8.3%} {r['control_mean']:>8.3%} {r['incremental_edge']:>9.3%} {r['real_win_rate']:>7.1%} {r['control_win_rate']:>7.1%} {r['real_p_value']:>10.6f}")

with open(RESULTS_DIR / "verify_pre_earnings_timing_results.json", "w") as f:
    json.dump(results, f, indent=2)
print("\nSaved to results/verify_pre_earnings_timing_results.json")
