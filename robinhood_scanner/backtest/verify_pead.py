#!/usr/bin/env python3
"""Independent verification of the PEAD 20-day-hold claim, built from scratch (not reusing the
fork's aggregation code) against the same cached earnings-dates and price data.
"""
import json
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy import stats

from data import fetch_daily

CACHE_DIR = Path(__file__).parent / "cache"
SLIPPAGE_BPS = 5.0
GAP_PCT_MIN = 5.0
PRICE_MIN = 3.0
VOLUME_MIN = 500_000
HOLD_DAYS = 20

with open(CACHE_DIR / "sp500_earnings_dates_2y.json") as f:
    earnings = json.load(f)

# ticker -> set of earnings dates (as pd.Timestamp, tz-naive date)
earnings_by_ticker = defaultdict(set)
for e in earnings:
    earnings_by_ticker[e["ticker"]].add(pd.Timestamp(e["date"]).normalize())

tickers = sorted(earnings_by_ticker.keys())
print(f"Testing {len(tickers)} tickers with earnings data")

trade_returns = []
per_ticker_returns = defaultdict(list)
skipped_no_cache = 0

for ticker in tickers:
    cache_file = CACHE_DIR / f"{ticker}_3y.csv"
    if not cache_file.exists():
        skipped_no_cache += 1
        continue
    df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
    if len(df) < 10:
        continue
    df = df.sort_index()
    df.index = df.index.tz_localize(None) if df.index.tz is not None else df.index
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
        # earnings-gap: reported same day (am) or in the prior 1-3 calendar days (pm -> next-day gap)
        is_earnings_gap = any((day - ed).days in (0, 1, 2, 3) for ed in edates)
        if not is_earnings_gap:
            continue

        entry = df.iloc[i + 1]["Open"]
        exit_row = df.iloc[i + 1 + HOLD_DAYS]
        raw_return = (exit_row["Close"] - entry) / entry
        net_return = raw_return - 2 * (SLIPPAGE_BPS / 10_000)
        trade_returns.append(net_return)
        per_ticker_returns[ticker].append(net_return)

trade_returns = np.array(trade_returns)
n = len(trade_returns)
print(f"\nSkipped (no cached price data): {skipped_no_cache}")
print(f"Total PEAD (earnings-gap, 20-day hold) trades found: {n}")
print(f"Tickers with at least one trade: {len(per_ticker_returns)}")
print(f"Pooled mean trade return: {trade_returns.mean():.4%}")
print(f"Pooled std dev: {trade_returns.std(ddof=1):.4%}")
print(f"Win rate: {(trade_returns > 0).mean():.1%}")

t_stat, p_value = stats.ttest_1samp(trade_returns, 0)
print(f"\nOne-sample t-test vs zero: t={t_stat:.3f}, p={p_value:.5f}")

# per-ticker compounded average (matches report's methodology)
per_ticker_total = []
for tkr, rets in per_ticker_returns.items():
    compounded = np.prod([1 + r for r in rets]) - 1
    per_ticker_total.append(compounded)
print(f"\nPer-ticker compounded avg total return (across {len(per_ticker_total)} tickers): {np.mean(per_ticker_total):.4%}")
