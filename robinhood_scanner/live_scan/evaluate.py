#!/usr/bin/env python3
"""CLI helper for the live dry-run scan.

The scheduled Claude session pulls live quotes/historicals via the connected Robinhood MCP tools,
then calls this script (via Bash) to apply the exact same strategy rule functions used in the
backtest (backtest/strategies.py) — math stays in Python, not free-form LLM arithmetic.

Usage:
  python3 evaluate.py gap --ticker AAPL --open 190.5 --prev-close 182.1 --price 191.0 --volume 900000
  python3 evaluate.py trend --ticker AAPL --close 191.0 --prev-day-high 189.0 --sma200 175.3 \
      --premarket-high 190.2 --hour-et 10.5

Trend Join Long requires all four conditions live (premarket-high and hour-et are NOT optional here
-- both are obtainable from Robinhood MCP tools, so there is no reason to skip them for the live
scan). They remain optional parameters in backtest/strategies.py itself only because 2-year daily-bar
backtesting has no free premarket history at that depth (see backtest/report_sp500.md caveat).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backtest"))

from strategies import (  # noqa: E402
    EarningsGapPeadParams,
    earnings_gap_pead_exit_due,
    earnings_window_ok,
    gap_scan,
    qualifying_gap,
    trend_join_long,
)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="strategy", required=True)

    gap = sub.add_parser("gap")
    gap.add_argument("--ticker", required=True)
    gap.add_argument("--open", type=float, required=True)
    gap.add_argument("--prev-close", type=float, required=True)
    gap.add_argument("--price", type=float, required=True)
    gap.add_argument("--volume", type=float, required=True)

    trend = sub.add_parser("trend")
    trend.add_argument("--ticker", required=True)
    trend.add_argument("--close", type=float, required=True)
    trend.add_argument("--prev-day-high", type=float, required=True)
    trend.add_argument("--sma200", type=float, required=True)
    trend.add_argument("--premarket-high", type=float, required=True)
    trend.add_argument("--hour-et", type=float, required=True)

    pead_entry = sub.add_parser("pead-entry")
    pead_entry.add_argument("--ticker", required=True)
    pead_entry.add_argument("--open", type=float, required=True)
    pead_entry.add_argument("--prev-close", type=float, required=True)
    pead_entry.add_argument("--price", type=float, required=True)
    pead_entry.add_argument("--volume", type=float, required=True)
    pead_entry.add_argument("--days-since-earnings", type=int, required=True,
                             help="Calendar days between the earnings report and this gap day (0=same-day am report, 1-3=pm report causing a next-day gap). Values outside 0-3 do not qualify.")

    pead_exit = sub.add_parser("pead-exit")
    pead_exit.add_argument("--ticker", required=True)
    pead_exit.add_argument("--trading-days-elapsed", type=int, required=True,
                            help="Trading days (not calendar days) since the entry day, counted by the caller.")

    args = parser.parse_args()

    if args.strategy == "gap":
        hit, gap_pct = gap_scan(args.open, args.prev_close, args.price, args.volume)
        print(f"{args.ticker}: gap_scan hit={hit} gap_pct={gap_pct:.2f}%")
    elif args.strategy == "trend":
        hit = trend_join_long(
            args.close,
            args.prev_day_high,
            args.sma200,
            premarket_high=args.premarket_high,
            hour_et=args.hour_et,
        )
        print(f"{args.ticker}: trend_join_long hit={hit}")
    elif args.strategy == "pead-entry":
        # Same code path as the backtest (strategies.qualifying_gap) — no math re-derived here.
        qualifies, gap_pct = qualifying_gap(
            args.open, args.prev_close, args.price, args.volume, EarningsGapPeadParams()
        )
        is_earnings_window = earnings_window_ok(args.days_since_earnings)
        hit = qualifies and is_earnings_window
        print(f"{args.ticker}: pead_entry hit={hit} gap_pct={gap_pct:.2f}% qualifies_gap={qualifies} is_earnings_window={is_earnings_window}")
    elif args.strategy == "pead-exit":
        hit = earnings_gap_pead_exit_due(args.trading_days_elapsed)
        print(f"{args.ticker}: pead_exit_due={hit} trading_days_elapsed={args.trading_days_elapsed}")


if __name__ == "__main__":
    main()
