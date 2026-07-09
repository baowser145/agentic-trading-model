#!/usr/bin/env python3
"""Out-of-sample PEAD backtest, 2022-07-06 to 2024-07-06 -- a period NOT used in the original
validation (2024-07-06 to 2026-07-06). Same methodology as verify_pead.py, applied to the earlier
window using cache/sp500_earnings_dates_2022_2024.json and cache/{ticker}_2022_2024.csv.
"""
import json
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy import stats

CACHE_DIR = Path(__file__).parent / "cache"
SLIPPAGE_BPS = 5.0
GAP_PCT_MIN = 5.0
PRICE_MIN = 3.0
VOLUME_MIN = 500_000
HOLD_DAYS = 20
TEST_START = pd.Timestamp("2022-07-06")
TEST_END = pd.Timestamp("2024-07-06")

with open(CACHE_DIR / "sp500_earnings_dates_2022_2024.json") as f:
    earnings = json.load(f)

earnings_by_ticker = defaultdict(set)
for e in earnings:
    earnings_by_ticker[e["ticker"]].add(pd.Timestamp(e["date"]).normalize())

tickers = sorted(earnings_by_ticker.keys())
print(f"Testing {len(tickers)} tickers with earnings data")

trade_returns = []
trade_dates = []
per_ticker_returns = defaultdict(list)
skipped_no_cache = 0

for ticker in tickers:
    cache_file = CACHE_DIR / f"{ticker}_2022_2024.csv"
    if not cache_file.exists():
        skipped_no_cache += 1
        continue
    df = pd.read_csv(cache_file, index_col=0)
    if len(df) < 10:
        continue
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
    df = df.sort_index()
    df["prev_close"] = df["Close"].shift(1)

    edates = earnings_by_ticker[ticker]

    for i in range(1, len(df) - HOLD_DAYS - 1):
        row = df.iloc[i]
        if pd.isna(row["prev_close"]) or row["prev_close"] <= 0:
            continue
        gap_pct = (row["Open"] - row["prev_close"]) / row["prev_close"] * 100
        if not (gap_pct >= GAP_PCT_MIN and row["Close"] >= PRICE_MIN and row["Volume"] >= VOLUME_MIN):
            continue

        day = df.index[i].normalize()
        if not (TEST_START <= day <= TEST_END):
            continue
        is_earnings_gap = any((day - ed).days in (0, 1, 2, 3) for ed in edates)
        if not is_earnings_gap:
            continue

        entry = df.iloc[i + 1]["Open"]
        exit_row = df.iloc[i + 1 + HOLD_DAYS]
        raw_return = (exit_row["Close"] - entry) / entry
        net_return = raw_return - 2 * (SLIPPAGE_BPS / 10_000)
        trade_returns.append(net_return)
        trade_dates.append(day)
        per_ticker_returns[ticker].append(net_return)

trade_returns = np.array(trade_returns)
trade_dates = pd.Series(trade_dates)
n = len(trade_returns)
print(f"\nSkipped (no cached price data): {skipped_no_cache}")
print(f"Total PEAD (earnings-gap, 20-day hold) trades found: {n}")
print(f"Tickers with at least one trade: {len(per_ticker_returns)}")

if n > 0:
    print(f"Pooled mean trade return: {trade_returns.mean():.4%}")
    print(f"Pooled std dev: {trade_returns.std(ddof=1):.4%}")
    print(f"Win rate: {(trade_returns > 0).mean():.1%}")
    print(f"Worst: {trade_returns.min():.4%}  Best: {trade_returns.max():.4%}")

    t_stat, p_value = stats.ttest_1samp(trade_returns, 0)
    print(f"\nOne-sample t-test vs zero: t={t_stat:.3f}, p={p_value:.5f}")

    per_ticker_total = []
    for tkr, rets in per_ticker_returns.items():
        compounded = np.prod([1 + r for r in rets]) - 1
        per_ticker_total.append(compounded)
    print(f"Per-ticker compounded avg total return (across {len(per_ticker_total)} tickers): {np.mean(per_ticker_total):.4%}")

    # Sub-period split
    mid = pd.Timestamp("2023-07-06")
    mask1 = trade_dates < mid
    mask2 = trade_dates >= mid
    for label, mask in [("2022-07 to 2023-07 (bear/recovery start)", mask1), ("2023-07 to 2024-07 (recovery)", mask2)]:
        sub = trade_returns[mask.values]
        if len(sub) == 0:
            print(f"\n{label}: no trades")
            continue
        t_stat_sub, p_sub = stats.ttest_1samp(sub, 0)
        print(f"\n{label}: n={len(sub)} mean={sub.mean():.4%} win_rate={(sub > 0).mean():.1%} "
              f"worst={sub.min():.4%} best={sub.max():.4%} p={p_sub:.5f}")

with open(CACHE_DIR / "pead_oos_2022_2024_results.json", "w") as f:
    json.dump({
        "n_trades": n,
        "n_tickers": len(per_ticker_returns),
        "skipped_no_cache": skipped_no_cache,
        "mean_return": float(trade_returns.mean()) if n else None,
        "std_return": float(trade_returns.std(ddof=1)) if n else None,
        "win_rate": float((trade_returns > 0).mean()) if n else None,
        "worst": float(trade_returns.min()) if n else None,
        "best": float(trade_returns.max()) if n else None,
    }, f, indent=2)
print("\nSaved summary to cache/pead_oos_2022_2024_results.json")
