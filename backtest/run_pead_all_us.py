#!/usr/bin/env python3
"""PEAD (earnings-gap + 20-trading-day-hold) backtest across all US common stocks, with an
S&P-500-vs-rest breakdown, to test whether the validated S&P 500 edge generalizes or is
concentrated in large/liquid names.
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
TEST_START = pd.Timestamp("2024-07-06")
TEST_END = pd.Timestamp("2026-07-06")

with open(CACHE_DIR / "all_us_earnings_2y_final.json") as f:
    earnings = json.load(f)

with open(CACHE_DIR / "sp500_tickers.txt") as f:
    sp500 = set(line.strip() for line in f if line.strip())

with open(CACHE_DIR / "all_us_tickers.txt") as f:
    all_tickers = [line.strip() for line in f if line.strip()]

earnings_by_ticker = defaultdict(set)
for e in earnings:
    try:
        earnings_by_ticker[e["ticker"]].add(pd.Timestamp(e["date"]).normalize())
    except (ValueError, TypeError):
        continue

print(f"Universe: {len(all_tickers)} tickers, {len(sp500)} in S&P 500")
print(f"Earnings events: {len(earnings)} raw, {len(earnings_by_ticker)} unique tickers with dates")

results = {"sp500": defaultdict(list), "non_sp500": defaultdict(list)}
skipped_no_cache = 0
skipped_too_short = 0
tickers_with_trades = {"sp500": set(), "non_sp500": set()}

for ticker in all_tickers:
    if ticker not in earnings_by_ticker:
        continue
    cache_file = CACHE_DIR / f"{ticker}_3y.csv"
    if not cache_file.exists():
        skipped_no_cache += 1
        continue
    try:
        df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
    except Exception:
        skipped_no_cache += 1
        continue
    if len(df) < 10:
        skipped_too_short += 1
        continue
    df = df.sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df["prev_close"] = df["Close"].shift(1)

    edates = earnings_by_ticker[ticker]
    bucket = "sp500" if ticker in sp500 else "non_sp500"

    for i in range(1, len(df) - HOLD_DAYS - 1):
        row = df.iloc[i]
        day = df.index[i]
        if day < TEST_START or day > TEST_END:
            continue
        if pd.isna(row["prev_close"]) or row["prev_close"] <= 0:
            continue
        gap_pct = (row["Open"] - row["prev_close"]) / row["prev_close"] * 100
        if not (gap_pct >= GAP_PCT_MIN and row["Close"] >= PRICE_MIN and row["Volume"] >= VOLUME_MIN):
            continue
        day_norm = day.normalize()
        if not any(0 <= (day_norm - ed).days <= 3 for ed in edates):
            continue

        entry_idx = i + 1
        exit_idx = i + 1 + HOLD_DAYS
        if exit_idx >= len(df):
            continue
        entry_price = df.iloc[entry_idx]["Open"]
        exit_price = df.iloc[exit_idx]["Close"]
        if entry_price <= 0:
            continue
        raw_return = (exit_price - entry_price) / entry_price
        net_return = raw_return - 2 * (SLIPPAGE_BPS / 10_000)

        results[bucket][ticker].append(net_return)
        tickers_with_trades[bucket].add(ticker)

print(f"Skipped (no cache): {skipped_no_cache}, skipped (too short): {skipped_too_short}")


def summarize(bucket_results):
    all_returns = []
    per_ticker_total = []
    for ticker, rets in bucket_results.items():
        all_returns.extend(rets)
        compounded = np.prod([1 + r for r in rets]) - 1
        per_ticker_total.append(compounded)
    if not all_returns:
        return None
    all_returns = np.array(all_returns)
    t_stat, p_value = stats.ttest_1samp(all_returns, 0)
    return {
        "n_tickers": len(bucket_results),
        "n_trades": len(all_returns),
        "mean_return": all_returns.mean(),
        "std_return": all_returns.std(ddof=1),
        "win_rate": (all_returns > 0).mean(),
        "worst": all_returns.min(),
        "best": all_returns.max(),
        "p_value": p_value,
        "per_ticker_avg_total": np.mean(per_ticker_total),
    }


sp500_summary = summarize(results["sp500"])
non_sp500_summary = summarize(results["non_sp500"])
combined = defaultdict(list)
for b in results.values():
    for t, r in b.items():
        combined[t].extend(r)
combined_summary = summarize(combined)

print("\n=== S&P 500 subset ===")
print(sp500_summary)
print("\n=== Non-S&P-500 subset ===")
print(non_sp500_summary)
print("\n=== Combined (full universe) ===")
print(combined_summary)

with open(RESULTS_DIR / "pead_all_us_results.json", "w") as f:
    def clean(d):
        if d is None:
            return None
        return {k: (float(v) if isinstance(v, (np.floating, np.integer)) else v) for k, v in d.items()}
    json.dump({
        "sp500": clean(sp500_summary),
        "non_sp500": clean(non_sp500_summary),
        "combined": clean(combined_summary),
    }, f, indent=2)

print("\nSaved to results/pead_all_us_results.json")
