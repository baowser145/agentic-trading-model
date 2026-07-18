#!/usr/bin/env python3
"""Earnings-Gap PEAD daily check — standalone script version of docs/phase-a-pead-prompt.md.

Runs after US market close (GitHub Actions cron; see .github/workflows/daily-scans.yml). Stateless
by design: ENTRY and EXIT are both re-derived from price + earnings data every run, exactly as the
prompt doc specifies — no positions file.

Funnel (inverted from the doc's calendar-first order, same result): scan all S&P 500 daily bars
for qualifying gaps first (one batched download), then confirm the earnings window only for the
handful of gappers — this avoids needing a historical earnings-calendar API entirely.

All strategy math comes from backtest/strategies.py. This script has no brokerage access and
composes an informational Discord alert only.
"""

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backtest"))

from strategies import (  # noqa: E402
    EarningsGapPeadParams,
    earnings_gap_pead_exit_due,
    is_earnings_gap,
    qualifying_gap,
)

import market_data as md  # noqa: E402  (live_scan/market_data.py — same dir)
from alerts.send_discord import send_message  # noqa: E402

PARAMS = EarningsGapPeadParams()
CALENDAR_TICKER = "SPY"  # defines "did the market trade today"

# Mandatory caveat — every alert includes this verbatim (docs/phase-a-pead-prompt.md).
CAVEAT = (
    "⚠️ Backtested edge validated in-sample (2024-2026: +1.70%/trade, p=0.00019) and out-of-sample "
    "(2022-2024: +3.57%/trade, p<0.00001). Remaining caveats: survivorship bias (today's S&P 500 "
    "list applied to historical periods) and large single-trade tail risk (worst historical trade "
    "-38%; no stop-loss — stops were backtested and made results worse). This is informational "
    "only; manual approval required for any trade."
)

BACKTEST_LINE = "📊 Backtested: 55.8% win rate in-sample / 60.8% out-of-sample (p=0.00019 / p<0.00001)"


def find_matched_earnings(gap_day, earnings_timestamps):
    """The most recent report timestamp whose window covers gap_day, or None."""
    best = None
    for ts in earnings_timestamps:
        ed = md.to_naive_date(ts).date()
        if 0 <= (gap_day - ed).days <= 3 and (best is None or ed > md.to_naive_date(best).date()):
            best = ts
    return best


def scan(bars: dict[str, pd.DataFrame], today: pd.Timestamp):
    """Returns (entries, exits, notes). Entries/exits are dicts ready for compose_message()."""
    entries, exits, notes = [], [], []
    earnings_cache: dict[str, list] = {}

    def earnings_for(ticker):
        if ticker not in earnings_cache:
            earnings_cache[ticker] = md.get_earnings_dates(ticker)
        return earnings_cache[ticker]

    for ticker, df in sorted(bars.items()):
        if ticker == CALENDAR_TICKER or len(df) < 2 or df.index[-1] != today:
            continue

        # ENTRY: qualifying gap today that lands 0-3 calendar days after an earnings report.
        last, prev = df.iloc[-1], df.iloc[-2]
        volume = 0 if pd.isna(last["Volume"]) else float(last["Volume"])
        qualifies, gap_pct = qualifying_gap(float(last["Open"]), float(prev["Close"]), float(last["Close"]), volume, PARAMS)
        if qualifies:
            timestamps = earnings_for(ticker)
            if not timestamps:
                notes.append(f"{ticker}: gapped +{gap_pct:.1f}% today but earnings dates unavailable — skipped (not confirmable as an earnings gap).")
            elif is_earnings_gap(today.date(), md.earnings_date_set(timestamps)):
                matched = find_matched_earnings(today.date(), timestamps)
                entries.append({
                    "ticker": ticker,
                    "gap_pct": gap_pct,
                    "price": float(last["Close"]),
                    "report_date": md.to_naive_date(matched).date(),
                    "timing": md.earnings_timing(matched).upper(),
                })

        # EXIT: a qualifying earnings gap exactly hold_trading_days trading days ago (recomputed
        # from bars, never read from saved state).
        n = PARAMS.hold_trading_days
        if len(df) >= n + 2:
            entry_row, before = df.iloc[-(n + 1)], df.iloc[-(n + 2)]
            entry_day = df.index[-(n + 1)]
            e_volume = 0 if pd.isna(entry_row["Volume"]) else float(entry_row["Volume"])
            e_qualifies, _ = qualifying_gap(float(entry_row["Open"]), float(before["Close"]), float(entry_row["Close"]), e_volume, PARAMS)
            if e_qualifies:
                timestamps = earnings_for(ticker)
                if not timestamps:
                    notes.append(f"{ticker}: qualifying gap on {entry_day.date()} (~{n} trading days ago) but earnings dates unavailable — exit check skipped.")
                elif is_earnings_gap(entry_day.date(), md.earnings_date_set(timestamps)) and earnings_gap_pead_exit_due(n, PARAMS):
                    exits.append({
                        "ticker": ticker,
                        "entry_day": entry_day.date(),
                        "price": float(last["Close"]) if df.index[-1] == today else None,
                    })

    return entries, exits, notes


def compose_message(entries, exits, today, notes=(), market_open=True):
    lines = [f"[Robinhood Scanner — Earnings-Gap PEAD Daily Check — {today.date()}]", "", CAVEAT, ""]

    if not market_open:
        lines += [f"Market appears closed today ({today.date()} has no trading bar) — no signals to evaluate.", ""]

    lines.append("ENTRY SIGNALS (consider buying at next open, plan to hold ~20 trading days, exit signal comes later via the EXIT check below):")
    if entries and market_open:
        for e in entries:
            timing = f", {e['timing']}" if e["timing"] != "UNKNOWN" else ""
            lines += [
                f"🟢 {e['ticker']} — gapped {e['gap_pct']:+.1f}% (reported {e['report_date']}{timing})",
                f"💰 Current Price: ${e['price']:,.2f}",
                f"🛒 Suggested Buy: ~${e['price']:,.2f} (next open)",
                BACKTEST_LINE,
                "",
            ]
    else:
        lines += ["No entry signals today.", ""]

    lines.append("EXIT SIGNALS (20-trading-day hold period reached — consider closing):")
    if exits and market_open:
        for x in exits:
            price = f" (~${x['price']:,.2f})" if x["price"] else ""
            lines.append(f"🔴 {x['ticker']} — entered ~{x['entry_day']}, 20 trading days elapsed. Consider exiting at today's close{price}.")
        lines.append("")
    else:
        lines += ["No exit signals today.", ""]

    if notes:
        lines.append("Data notes:")
        lines += [f"- {n}" for n in notes]
        lines.append("")

    lines.append("This is informational only. You approve and place any trade yourself in the Robinhood app.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the alert instead of sending to Discord")
    parser.add_argument("--tickers", help="comma-separated ticker override (testing)")
    args = parser.parse_args()

    md.load_env_file(ROOT / "config" / ".env")
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook and not args.dry_run:
        print("DISCORD_WEBHOOK_URL is not set (use --dry-run to test without it)", file=sys.stderr)
        sys.exit(1)

    today = md.today_market_date()
    tickers = args.tickers.split(",") if args.tickers else md.get_sp500_tickers()
    if CALENDAR_TICKER not in tickers:
        tickers = [*tickers, CALENDAR_TICKER]

    print(f"Downloading daily bars for {len(tickers)} tickers...")
    bars = md.download_recent_bars(tickers)
    print(f"Got bars for {len(bars)} tickers.")

    spy = bars.get(CALENDAR_TICKER)
    market_open = spy is not None and len(spy) > 0 and spy.index[-1] == today
    if not market_open:
        print(f"No {CALENDAR_TICKER} bar for {today.date()} — treating market as closed today.")

    entries, exits, notes = scan(bars, today) if market_open else ([], [], [])
    message = compose_message(entries, exits, today, notes, market_open)

    print(f"\n{len(entries)} entry signal(s), {len(exits)} exit signal(s), {len(notes)} note(s).")
    if args.dry_run:
        print("\n--- DRY RUN — message below was NOT sent ---\n")
        print(message)
    else:
        n = send_message(webhook, message)
        print(f"Sent to Discord ({n} message{'s' if n != 1 else ''}).")


if __name__ == "__main__":
    main()
