#!/usr/bin/env python3
"""For each upcoming S&P 500 earnings candidate (18-30 days out), backtest that SPECIFIC company's
own history in the validated pre-earnings window (25-day lead, exit day before report) using the
cached 2-year data. Rank candidates by their own individual track record rather than just handing
over the raw earnings-calendar list.
"""
import json
import time
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

CACHE_DIR = Path(__file__).parent / "cache"
SLIPPAGE_BPS = 5.0
LEAD_DAYS = 25  # the strongest single point found in the sweep
# Rolling 2-year evaluation window ending today. This script runs daily via cron — a hardcoded
# window would silently ossify the "own history" stats quoted in the alert as time moves on.
TEST_END = pd.Timestamp.now().normalize()
TEST_START = TEST_END - pd.DateOffset(years=2)
MIN_HISTORICAL_TRADES = 6  # require most of the ~8-quarter history to be present
MIN_WIN_RATE = 0.75  # user-requested 2026-07-07: only show the strongest individual track records
MAX_ACCEPTABLE_WORST = -0.20  # exclude tickers whose worst historical instance lost more than this

EARNINGS_CACHE_FILE = CACHE_DIR / "sp500_earnings_dates_2y.json"
EARNINGS_CACHE_MAX_AGE_DAYS = 14
PRICE_CACHE_MAX_AGE_DAYS = 7


def cache_age_days(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 86400


with open(CACHE_DIR / "upcoming_sp500_earnings.json") as f:
    candidates = json.load(f)

with open(EARNINGS_CACHE_FILE) as f:
    earnings = json.load(f)

warnings = []
earnings_age = cache_age_days(EARNINGS_CACHE_FILE)
if earnings_age > EARNINGS_CACHE_MAX_AGE_DAYS:
    warnings.append(
        f"WARNING: {EARNINGS_CACHE_FILE.name} is {earnings_age:.0f} days old (max {EARNINGS_CACHE_MAX_AGE_DAYS}). "
        f"Recent quarters are missing from every ticker's history — refresh it from Robinhood "
        f"get_earnings_calendar (same format: list of {{ticker, date}}) before trusting these rankings."
    )

earnings_by_ticker = defaultdict(list)
for e in earnings:
    earnings_by_ticker[e["ticker"]].append(pd.Timestamp(e["date"]).normalize())

rows = []
oldest_price_cache_days = 0.0
for c in candidates:
    ticker = c["ticker"]
    cache_file = CACHE_DIR / f"{ticker}_3y.csv"
    if not cache_file.exists():
        continue
    oldest_price_cache_days = max(oldest_price_cache_days, cache_age_days(cache_file))
    df = pd.read_csv(cache_file, index_col=0, parse_dates=True).sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    if len(df) < 10:
        continue
    idx = df.index

    edates = earnings_by_ticker.get(ticker, [])
    trade_returns = []
    for ed in edates:
        if ed < TEST_START or ed > TEST_END:
            continue
        target_entry = ed - pd.Timedelta(days=LEAD_DAYS)
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
        entry_price = df.loc[entry_date, "Open"]
        exit_price = df.loc[exit_date, "Close"]
        if entry_price <= 0:
            continue
        net_return = (exit_price - entry_price) / entry_price - 2 * (SLIPPAGE_BPS / 10_000)
        trade_returns.append(net_return)

    if len(trade_returns) < MIN_HISTORICAL_TRADES:
        continue

    arr = np.array(trade_returns)
    rows.append({
        "ticker": ticker,
        "report_date": c["date"],
        "timing": c["timing"],
        "verified": c["verified"],
        "n_historical_trades": len(arr),
        "mean_return": arr.mean(),
        "win_rate": (arr > 0).mean(),
        "worst": arr.min(),
        "best": arr.max(),
    })

result_df = pd.DataFrame(rows)
result_df = result_df.sort_values(["mean_return", "win_rate"], ascending=False)

quality_mask = (result_df["win_rate"] >= MIN_WIN_RATE) & (result_df["worst"] >= MAX_ACCEPTABLE_WORST)
passed = result_df[quality_mask]
excluded_high_mean = result_df[~quality_mask].sort_values("mean_return", ascending=False).head(5)

if oldest_price_cache_days > PRICE_CACHE_MAX_AGE_DAYS:
    warnings.append(
        f"WARNING: oldest price cache used is {oldest_price_cache_days:.0f} days old "
        f"(max {PRICE_CACHE_MAX_AGE_DAYS}). Re-warm with backtest/data.py fetch_daily_bulk "
        f"(or fetch_daily per ticker) so recent quarters are included."
    )

for w in warnings:
    print(w)
if warnings:
    print()

print(f"Evaluation window: {TEST_START.date()} to {TEST_END.date()} (rolling 2y ending today)")
print(f"Candidates with usable price data: {len(candidates)}")
print(f"Candidates with >= {MIN_HISTORICAL_TRADES} historical pre-earnings trades: {len(result_df)}")
print(f"Passing quality filter (win_rate >= {MIN_WIN_RATE:.0%}, worst >= {MAX_ACCEPTABLE_WORST:.0%}): {len(passed)}\n")
print("=== PASSED (top 10) ===")
print(passed.head(10).to_string(index=False))
print("\n=== EXCLUDED but notable (high mean, failed quality filter -- risky, not recommended) ===")
print(excluded_high_mean.to_string(index=False))
print(
    "\nSTATISTICAL CAVEAT (include the gist of this in any alert): each ticker's win rate is based "
    f"on only ~8 historical trades (min {MIN_HISTORICAL_TRADES}). A ticker with NO real edge passes a "
    f"{MIN_WIN_RATE:.0%} bar (6-of-8) by luck ~14% of the time, so screening ~300 candidates is "
    "expected to surface dozens of lucky false positives. Individual track records are weak evidence; "
    "the validated result is the POOLED 18-30-day pre-earnings edge (~+0.7-1.1pp over random-entry "
    "control), not any single ticker's history."
)

result_df.to_csv(CACHE_DIR / "ranked_pre_earnings_candidates.csv", index=False)
passed.head(10).to_csv(CACHE_DIR / "ranked_pre_earnings_candidates_top10.csv", index=False)
print(f"\nFull ranked list saved to cache/ranked_pre_earnings_candidates.csv")
print(f"Top 10 passing quality filter saved to cache/ranked_pre_earnings_candidates_top10.csv")
