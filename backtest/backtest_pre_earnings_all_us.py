#!/usr/bin/env python3
"""Test whether the pre-earnings timing edge (18-30 days before report, exit before the report)
holds up across the full NYSE/NASDAQ/AMEX universe, with a liquidity floor (price > $3, volume >
500k at entry, same convention as the PEAD gap_scan filter), split S&P 500 vs. non-S&P-500 -- same
methodology as the earlier all-US PEAD test, applied to this different strategy.

Reuses cached data from that earlier run: all_us_tickers.txt, all_us_earnings_2y_final.json, and the
per-ticker 3y price CSVs already downloaded for that test. No new network calls.
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
PRICE_MIN = 3.0
VOLUME_MIN = 500_000
TEST_START = pd.Timestamp("2024-07-06")
TEST_END = pd.Timestamp("2026-07-06")
LEAD_DAYS_VARIANTS = [18, 21, 25, 30]

with open(CACHE_DIR / "all_us_earnings_2y_final.json") as f:
    earnings = json.load(f)
with open(CACHE_DIR / "sp500_tickers.txt") as f:
    sp500 = set(line.strip() for line in f if line.strip())
with open(CACHE_DIR / "all_us_tickers.txt") as f:
    all_tickers = [line.strip() for line in f if line.strip()]

earnings_by_ticker = defaultdict(list)
for e in earnings:
    try:
        earnings_by_ticker[e["ticker"]].append(pd.Timestamp(e["date"]).normalize())
    except (ValueError, TypeError):
        continue

print(f"Universe: {len(all_tickers)} tickers, {len(sp500)} in S&P 500")
print(f"Earnings events: {len(earnings)} raw, {len(earnings_by_ticker)} unique tickers with dates")

price_data = {}
skipped_no_cache = 0
for ticker in all_tickers:
    if ticker not in earnings_by_ticker:
        continue
    f = CACHE_DIR / f"{ticker}_3y.csv"
    if not f.exists():
        skipped_no_cache += 1
        continue
    try:
        df = pd.read_csv(f, index_col=0, parse_dates=True).sort_index()
    except Exception:
        skipped_no_cache += 1
        continue
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    if len(df) < 10:
        continue
    price_data[ticker] = df

print(f"Price data loaded for {len(price_data)} tickers (skipped {skipped_no_cache} with no/bad cache)\n")

results = {}
for lead_days in LEAD_DAYS_VARIANTS:
    buckets = {"sp500": defaultdict(list), "non_sp500": defaultdict(list)}

    for ticker, edates in earnings_by_ticker.items():
        if ticker not in price_data:
            continue
        df = price_data[ticker]
        idx = df.index
        bucket = "sp500" if ticker in sp500 else "non_sp500"

        for ed in edates:
            if ed < TEST_START or ed > TEST_END:
                continue
            target_entry = ed - pd.Timedelta(days=lead_days)
            entry_candidates = idx[idx >= target_entry]
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

            entry_row = df.loc[entry_date]
            entry_price = entry_row["Open"]
            entry_volume = entry_row["Volume"]
            if entry_price < PRICE_MIN or entry_volume < VOLUME_MIN:
                continue

            exit_price = df.loc[exit_date, "Close"]
            if entry_price <= 0:
                continue
            net_return = (exit_price - entry_price) / entry_price - 2 * (SLIPPAGE_BPS / 10_000)
            buckets[bucket][ticker].append(net_return)

    def summarize(bucket_results):
        all_returns = []
        per_ticker_total = []
        for ticker, rets in bucket_results.items():
            all_returns.extend(rets)
            per_ticker_total.append(np.prod([1 + r for r in rets]) - 1)
        if not all_returns:
            return None
        arr = np.array(all_returns)
        t_stat, p_val = stats.ttest_1samp(arr, 0)
        return {
            "n_tickers": len(bucket_results),
            "n_trades": len(arr),
            "mean_return": float(arr.mean()),
            "std_return": float(arr.std(ddof=1)),
            "win_rate": float((arr > 0).mean()),
            "worst": float(arr.min()),
            "best": float(arr.max()),
            "p_value": float(p_val),
            "per_ticker_avg_total": float(np.mean(per_ticker_total)),
        }

    sp500_summary = summarize(buckets["sp500"])
    non_sp500_summary = summarize(buckets["non_sp500"])
    combined = defaultdict(list)
    for b in buckets.values():
        for t, r in b.items():
            combined[t].extend(r)
    combined_summary = summarize(combined)

    results[lead_days] = {
        "sp500": sp500_summary,
        "non_sp500": non_sp500_summary,
        "combined": combined_summary,
    }

    print(f"=== Lead days: {lead_days} ===")
    for label, s in [("S&P 500", sp500_summary), ("Non-S&P-500", non_sp500_summary), ("Combined", combined_summary)]:
        if s is None:
            print(f"  {label}: no trades")
            continue
        print(f"  {label:<12} trades={s['n_trades']:>5} tickers={s['n_tickers']:>4} mean={s['mean_return']:>7.3%} "
              f"std={s['std_return']:>6.2%} win={s['win_rate']:>5.1%} worst={s['worst']:>7.2%} best={s['best']:>7.2%} p={s['p_value']:.2e}")
    print()

with open(RESULTS_DIR / "pre_earnings_all_us_results.json", "w") as f:
    json.dump(results, f, indent=2)
print("Saved to results/pre_earnings_all_us_results.json")
