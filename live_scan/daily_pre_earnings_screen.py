#!/usr/bin/env python3
"""Pre-earnings timing screen — standalone script version of docs/pre-earnings-screen-prompt.md.

Runs premarket on weekdays (GitHub Actions cron; see .github/workflows/daily-scans.yml).

Pipeline, mirroring the doc:
1. Upcoming earnings 18-30 days out via Alpha Vantage EARNINGS_CALENDAR (replaces Robinhood
   get_earnings_calendar), filtered to S&P 500, written to backtest/cache/ in the exact format
   backtest/rank_pre_earnings_candidates.py expects.
2. Warm the price + historical-earnings caches for just the candidate tickers (yfinance).
3. Run the EXISTING ranker script unmodified — the quality filter stays in one place.
4. For the top 10: live-ish price, company name, and an option pick from the real yfinance chain
   (delta computed via Black-Scholes from chain IV; see live_scan/market_data.py).
5. Compose one card per ticker and send to Discord. ALWAYS sends, even with zero candidates.

No brokerage access; informational alert only.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import market_data as md  # noqa: E402  (live_scan/market_data.py — same dir)
from alerts.send_discord import send_message  # noqa: E402

CACHE_DIR = ROOT / "backtest" / "cache"
WINDOW_MIN_DAYS = 18
WINDOW_MAX_DAYS = 30
EARNINGS_FETCH_SLEEP_SECONDS = 0.25
THIN_BID_DOLLARS = 0.10  # a sub-$0.10 bid is a token quote, not real liquidity — flag it

CAVEAT_LINES = [
    "⚠️ The stock backtest does not validate the options themselves (leverage/theta/IV never tested) — option quotes are real, but the strategy on options is unproven.",
    "⚠️ Per-ticker win rates are tiny samples (~8 trades); expect lucky false positives — the validated result is the pooled 18-30-day pre-earnings edge (~+0.7-1.1pp over random entry), not any single ticker's record.",
    "ℹ️ Robinhood's app shows the Bid by default on its Sell tab — our \"buy = ask\" number will look higher than the app's headline price.",
]


def build_candidates(calendar: pd.DataFrame, sp500: list[str], today: pd.Timestamp) -> list[dict]:
    """Filter the AV calendar to S&P 500 names reporting 18-30 calendar days out, in the JSON
    shape the ranker expects."""
    sp500_set = set(sp500)
    days_out = (calendar["reportDate"] - today).dt.days
    window = calendar[(days_out >= WINDOW_MIN_DAYS) & (days_out <= WINDOW_MAX_DAYS) & calendar["symbol"].isin(sp500_set)]
    window = window.drop_duplicates(subset="symbol", keep="first")
    return [
        {"ticker": row["symbol"], "date": row["reportDate"].strftime("%Y-%m-%d"), "timing": "unknown", "verified": True}
        for _, row in window.iterrows()
    ]


def warm_caches(candidates: list[dict]) -> tuple[dict[str, list], list[str]]:
    """Write the two cache files the ranker reads (prices + historical earnings dates for the
    candidate tickers only). Returns ({ticker: raw earnings timestamps}, failed_tickers)."""
    tickers = [c["ticker"] for c in candidates]

    print(f"Warming price cache for {len(tickers)} candidates...")
    price_failed = md.fetch_daily_bulk(tickers, period="3y")

    print(f"Fetching historical earnings dates for {len(tickers)} candidates via yfinance...")
    timestamps_by_ticker: dict[str, list] = {}
    earnings_failed = []
    earnings_records = []
    for ticker in tickers:
        ts_list = md.get_earnings_dates(ticker)
        time.sleep(EARNINGS_FETCH_SLEEP_SECONDS)
        if not ts_list:
            earnings_failed.append(ticker)
            continue
        timestamps_by_ticker[ticker] = ts_list
        for d in sorted(md.earnings_date_set(ts_list)):
            earnings_records.append({"ticker": ticker, "date": str(d)})

    CACHE_DIR.mkdir(exist_ok=True)
    with open(CACHE_DIR / "sp500_earnings_dates_2y.json", "w") as f:
        json.dump(earnings_records, f, indent=1)

    failed = sorted(set(price_failed) | set(earnings_failed))
    if failed:
        print(f"  no usable data for {len(failed)} candidates (skipped by ranker): {', '.join(failed[:15])}{'...' if len(failed) > 15 else ''}")
    return timestamps_by_ticker, failed


def run_ranker() -> pd.DataFrame:
    """Run backtest/rank_pre_earnings_candidates.py unmodified and return its top-10 output."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "backtest" / "rank_pre_earnings_candidates.py")],
        cwd=ROOT, capture_output=True, text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"Ranker failed with exit code {result.returncode}")
    top10_file = CACHE_DIR / "ranked_pre_earnings_candidates_top10.csv"
    if not top10_file.exists():
        raise RuntimeError("Ranker did not produce ranked_pre_earnings_candidates_top10.csv")
    return pd.read_csv(top10_file)


def timing_for(ticker: str, report_date: pd.Timestamp, timestamps_by_ticker: dict) -> str:
    """am/pm from the yfinance report timestamp matching the AV report date (±1 day)."""
    for ts in timestamps_by_ticker.get(ticker, []):
        if abs((md.to_naive_date(ts) - report_date).days) <= 1:
            return md.earnings_timing(ts)
    return "unknown"


def cached_close(ticker: str) -> float | None:
    cache_file = CACHE_DIR / f"{ticker}_3y.csv"
    if not cache_file.exists():
        return None
    df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
    return float(df["Close"].iloc[-1]) if len(df) else None


def compose_card(row: pd.Series, price: float, name: str, timing: str, pick: dict | None, reason: str) -> str:
    emoji = "🟢" if row["win_rate"] >= 0.75 else "🟡"
    timing_str = f" {timing}" if timing != "unknown" else ""
    lines = [
        f"{emoji} {row['ticker']} — {name} (reports {row['report_date']}{timing_str})",
        f"💰 Current Price: ${price:,.2f}",
        f"🛒 Buy Under: ${price * 0.99:,.2f}",
    ]
    if pick:
        thin = " — thin bid, quote may be stale" if pick["bid"] < THIN_BID_DOLLARS else ""
        lines.append(
            f"📈 ${pick['strike']:g}c exp {pick['expiration']:%m/%d} — Bid ${pick['bid']:.2f} / Ask ${pick['ask']:.2f} "
            f"(buy = ask, ${pick['cost']:.0f}/contract, delta {pick['delta']:.2f}){thin}"
        )
    else:
        lines.append(f"📈 Stock-only ({reason})")
    return "\n".join(lines)


def compose_message(cards: list[str], today: pd.Timestamp, n_candidates: int, failed: list[str]) -> str:
    parts = [f"[Robinhood Scanner — Pre-Earnings Timing Screen — {today.date()}]"]
    if cards:
        parts.extend(cards)
    else:
        parts.append(
            f"No qualifying candidates today ({n_candidates} S&P 500 names report in the 18-30 day "
            "window; none passed the quality filter: win_rate >= 75% and worst >= -20% over their "
            "own 2-year pre-earnings history)."
        )
    if failed:
        parts.append(f"Data note: {len(failed)} candidate(s) skipped for missing price/earnings history: {', '.join(failed[:10])}{'...' if len(failed) > 10 else ''}")
    parts.extend(CAVEAT_LINES)
    return "\n\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the alert instead of sending to Discord")
    parser.add_argument("--calendar-csv", help="local CSV to use instead of calling Alpha Vantage (testing); AV column format")
    args = parser.parse_args()

    md.load_env_file(ROOT / "config" / ".env")
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook and not args.dry_run:
        print("DISCORD_WEBHOOK_URL is not set (use --dry-run to test without it)", file=sys.stderr)
        sys.exit(1)

    today = md.today_market_date()

    if args.calendar_csv:
        calendar = pd.read_csv(args.calendar_csv)
        calendar["symbol"] = calendar["symbol"].astype(str).str.replace(".", "-", regex=False)
        calendar["reportDate"] = pd.to_datetime(calendar["reportDate"])
    else:
        api_key = os.environ.get("ALPHAVANTAGE_API_KEY")
        if not api_key:
            print("ALPHAVANTAGE_API_KEY is not set (get a free key at alphavantage.co)", file=sys.stderr)
            sys.exit(1)
        calendar = md.fetch_alpha_vantage_earnings_calendar(api_key)

    sp500 = md.get_sp500_tickers()
    candidates = build_candidates(calendar, sp500, today)
    print(f"{len(candidates)} S&P 500 candidates report in {WINDOW_MIN_DAYS}-{WINDOW_MAX_DAYS} days.")

    CACHE_DIR.mkdir(exist_ok=True)
    with open(CACHE_DIR / "upcoming_sp500_earnings.json", "w") as f:
        json.dump(candidates, f, indent=1)

    cards: list[str] = []
    failed: list[str] = []
    if candidates:
        timestamps_by_ticker, failed = warm_caches(candidates)
        top10 = run_ranker()

        for _, row in top10.iterrows():
            ticker = row["ticker"]
            report_date = pd.Timestamp(row["report_date"])
            price = md.last_price(ticker) or cached_close(ticker)
            if not price:
                print(f"  {ticker}: no price available — skipping card", file=sys.stderr)
                continue
            name = md.company_name(ticker)
            timing = timing_for(ticker, report_date, timestamps_by_ticker)
            pick, reason = md.pick_option_contract(ticker, price, report_date, today)
            cards.append(compose_card(row, price, name, timing, pick, reason))

    message = compose_message(cards, today, len(candidates), failed)
    print(f"\n{len(cards)} card(s) composed from {len(candidates)} candidates.")
    if args.dry_run:
        print("\n--- DRY RUN — message below was NOT sent ---\n")
        print(message)
    else:
        n = send_message(webhook, message)
        print(f"Sent to Discord ({n} message{'s' if n != 1 else ''}).")


if __name__ == "__main__":
    main()
