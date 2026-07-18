#!/usr/bin/env python3
"""Milestone 0 go/no-go gate: backtest gap_scan and trend_join_long against buy-and-hold.

Usage: python3 run_backtest.py

Reads tickers from tickers.txt, pulls ~3 years of daily data per ticker (first year used only as
indicator burn-in, e.g. for SMA200; the trailing ~2 years is the actual test window), simulates
each strategy's trades net of modeled slippage, and writes report.md with a pass/fail verdict.

Costs modeled: SLIPPAGE_BPS per side (round-trip = 2x), since Robinhood is commission-free but
premarket/gap fills are not free of spread.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from data import fetch_daily, fetch_daily_bulk, get_sp500_tickers
from strategies import GapScanParams, TrendJoinLongParams, gap_scan, trend_join_long

TICKERS_FILE = Path(__file__).parent / "tickers.txt"
REPORT_FILE = Path(__file__).parent / "report.md"

SLIPPAGE_BPS = 5.0  # per side, i.e. 0.05%
TEST_WINDOW_YEARS = 2
HOLD_DAYS_TREND_JOIN = 5  # trend_join_long exits after N trading days


def load_tickers():
    return [line.strip() for line in TICKERS_FILE.read_text().splitlines() if line.strip()]


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["prev_close"] = df["Close"].shift(1)
    df["prev_day_high"] = df["High"].shift(1)
    df["sma200"] = df["Close"].rolling(200).mean()
    return df


def max_drawdown(equity_curve: np.ndarray) -> float:
    if len(equity_curve) == 0:
        return 0.0
    running_max = np.maximum.accumulate(equity_curve)
    drawdowns = (equity_curve - running_max) / running_max
    return drawdowns.min()


def simulate_gap_scan(df: pd.DataFrame, test_start_idx: int):
    # Known look-ahead: the price/volume filters use the day's CLOSE and full-day volume, neither
    # knowable at the open when a live gap trade would be entered. This flatters the simulated
    # results relative to reality. Left as-is because the strategy failed its gate anyway
    # (report_sp500.md) and is not live — fix before ever reviving it.
    trades = []
    params = GapScanParams()
    for i in range(test_start_idx, len(df)):
        row = df.iloc[i]
        if pd.isna(row["prev_close"]):
            continue
        hit, gap_pct = gap_scan(row["Open"], row["prev_close"], row["Close"], row["Volume"], params)
        if hit:
            raw_return = (row["Close"] - row["Open"]) / row["Open"]
            net_return = raw_return - 2 * (SLIPPAGE_BPS / 10_000)
            trades.append({"date": df.index[i], "gap_pct": gap_pct, "return": net_return})
    return trades


def simulate_trend_join_long(df: pd.DataFrame, test_start_idx: int):
    trades = []
    params = TrendJoinLongParams()
    i = test_start_idx
    while i < len(df):
        row = df.iloc[i]
        if pd.isna(row["prev_day_high"]) or pd.isna(row["sma200"]):
            i += 1
            continue
        hit = trend_join_long(row["Close"], row["prev_day_high"], row["sma200"], params=params)
        if hit and i + 1 + HOLD_DAYS_TREND_JOIN < len(df):
            entry = df.iloc[i + 1]["Open"]
            exit_row = df.iloc[i + 1 + HOLD_DAYS_TREND_JOIN]
            raw_return = (exit_row["Close"] - entry) / entry
            net_return = raw_return - 2 * (SLIPPAGE_BPS / 10_000)
            trades.append({"date": df.index[i], "return": net_return})
            i += 1 + HOLD_DAYS_TREND_JOIN  # no overlapping positions
        else:
            i += 1
    return trades


def summarize(trades, buy_hold_return):
    if not trades:
        return {
            "num_trades": 0,
            "win_rate": None,
            "avg_return": None,
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "buy_hold_return": buy_hold_return,
            "beats_buy_hold": False,
            "positive_expectancy": False,
        }
    returns = [t["return"] for t in trades]
    equity = np.cumprod([1 + r for r in returns])
    total_return = equity[-1] - 1
    win_rate = sum(1 for r in returns if r > 0) / len(returns)
    avg_return = float(np.mean(returns))
    return {
        "num_trades": len(trades),
        "win_rate": win_rate,
        "avg_return": avg_return,
        "total_return": total_return,
        "max_drawdown": max_drawdown(equity),
        "buy_hold_return": buy_hold_return,
        "beats_buy_hold": total_return > buy_hold_return,
        "positive_expectancy": avg_return > 0,
    }


def run(tickers, report_file: Path, bulk: bool = False):
    gap_results = {}
    trend_results = {}
    skipped = []

    if bulk:
        print(f"Bulk downloading {len(tickers)} tickers...")
        failed = fetch_daily_bulk(tickers, period="3y")
        if failed:
            print(f"  {len(failed)} tickers failed to download and will be skipped: {failed[:10]}{'...' if len(failed) > 10 else ''}")
            skipped.extend(failed)
            tickers = [t for t in tickers if t not in set(failed)]

    for idx, ticker in enumerate(tickers):
        if not bulk:
            print(f"Fetching {ticker}...")
        elif idx % 50 == 0:
            print(f"  Backtesting {idx + 1}/{len(tickers)}...")

        try:
            raw = fetch_daily(ticker, period="3y")
        except Exception as e:
            print(f"  Skipping {ticker}: {e}")
            skipped.append(ticker)
            continue

        df = compute_indicators(raw)
        if len(df) < 250:
            skipped.append(ticker)
            continue

        test_start_date = df.index[-1] - pd.DateOffset(years=TEST_WINDOW_YEARS)
        test_start_idx = df.index.searchsorted(test_start_date)
        # ensure SMA200 burn-in has passed
        test_start_idx = max(test_start_idx, 200)
        if test_start_idx >= len(df):
            skipped.append(ticker)
            continue

        test_df = df.iloc[test_start_idx:]
        buy_hold_return = (test_df["Close"].iloc[-1] - test_df["Close"].iloc[0]) / test_df["Close"].iloc[0]

        gap_trades = simulate_gap_scan(df, test_start_idx)
        trend_trades = simulate_trend_join_long(df, test_start_idx)

        gap_results[ticker] = summarize(gap_trades, buy_hold_return)
        trend_results[ticker] = summarize(trend_trades, buy_hold_return)

    if skipped:
        print(f"Skipped {len(skipped)} tickers total (insufficient/failed data).")

    write_report(gap_results, trend_results, report_file)


def aggregate_verdict(results: dict):
    with_trades = {t: r for t, r in results.items() if r["num_trades"] > 0}
    if not with_trades:
        return False, "No trades triggered across any ticker in the test window."
    avg_strategy_return = np.mean([r["total_return"] for r in with_trades.values()])
    avg_buy_hold_return = np.mean([r["buy_hold_return"] for r in with_trades.values()])
    avg_expectancy = np.mean([r["avg_return"] for r in with_trades.values()])
    beats = avg_strategy_return > avg_buy_hold_return
    positive = avg_expectancy > 0
    passed = beats and positive
    detail = (
        f"avg strategy return {avg_strategy_return:.2%} vs avg buy-hold {avg_buy_hold_return:.2%}, "
        f"avg per-trade expectancy {avg_expectancy:.2%}"
    )
    return passed, detail


def render_ticker_table(results: dict) -> list[str]:
    lines = ["| Ticker | Trades | Win Rate | Avg Return/Trade | Total Return | Max DD | Buy&Hold | Beats B&H |"]
    lines.append("|---|---|---|---|---|---|---|---|")
    for ticker, r in results.items():
        if r["num_trades"] == 0:
            lines.append(f"| {ticker} | 0 | - | - | - | - | {r['buy_hold_return']:.2%} | - |")
            continue
        lines.append(
            f"| {ticker} | {r['num_trades']} | {r['win_rate']:.1%} | {r['avg_return']:.2%} | "
            f"{r['total_return']:.2%} | {r['max_drawdown']:.2%} | {r['buy_hold_return']:.2%} | "
            f"{'Yes' if r['beats_buy_hold'] else 'No'} |"
        )
    return lines


def write_report(gap_results, trend_results, report_file: Path):
    gap_passed, gap_detail = aggregate_verdict(gap_results)
    trend_passed, trend_detail = aggregate_verdict(trend_results)

    lines = ["# Backtest Report (Milestone 0 go/no-go gate)", ""]
    lines.append(
        f"Universe: {len(gap_results)} tickers. Slippage modeled: {SLIPPAGE_BPS} bps per side. "
        f"Test window: last {TEST_WINDOW_YEARS} years."
    )
    lines.append("")

    large_universe = len(gap_results) > 30

    for name, results, passed, detail in [
        ("Gap Scan", gap_results, gap_passed, gap_detail),
        ("Trend Join Long", trend_results, trend_passed, trend_detail),
    ]:
        lines.append(f"## {name}")
        lines.append(f"**Verdict: {'PASS' if passed else 'FAIL'}** — {detail}")
        lines.append("")

        with_trades = {t: r for t, r in results.items() if r["num_trades"] > 0}
        no_trades_count = len(results) - len(with_trades)
        lines.append(
            f"{len(with_trades)} of {len(results)} tickers triggered at least one trade "
            f"({no_trades_count} had none)."
        )
        lines.append("")

        if not large_universe:
            lines.extend(render_ticker_table(results))
        else:
            ranked = sorted(with_trades.items(), key=lambda kv: kv[1]["total_return"], reverse=True)
            lines.append(f"### Top 10 by total return ({name})")
            lines.extend(render_ticker_table(dict(ranked[:10])))
            lines.append("")
            lines.append(f"### Bottom 10 by total return ({name})")
            lines.extend(render_ticker_table(dict(ranked[-10:])))
        lines.append("")

    lines.append("## Overall Go/No-Go")
    if gap_passed or trend_passed:
        lines.append(
            "At least one strategy passed. Per plan.md, Phase A (live scan + Discord alert) may "
            "proceed **using only the strategy/strategies that passed.**"
        )
    else:
        lines.append(
            "**Neither strategy beat buy-and-hold net of modeled costs. Do not build the live "
            "pipeline on these rules.** Iterate the strategy parameters (thresholds, tickers, "
            "holding period) and re-run before proceeding to Phase A."
        )

    report_file.write_text("\n".join(lines) + "\n")
    print(f"\nReport written to {report_file}")
    print(f"Gap Scan: {'PASS' if gap_passed else 'FAIL'} ({gap_detail})")
    print(f"Trend Join Long: {'PASS' if trend_passed else 'FAIL'} ({trend_detail})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--universe",
        choices=["default", "sp500"],
        default="default",
        help="default = tickers.txt (8 tickers); sp500 = full S&P 500 constituents (bulk download)",
    )
    args = parser.parse_args()

    if args.universe == "sp500":
        tickers = get_sp500_tickers()
        report_file = Path(__file__).parent / "report_sp500.md"
        run(tickers, report_file, bulk=True)
    else:
        tickers = load_tickers()
        run(tickers, REPORT_FILE, bulk=False)


if __name__ == "__main__":
    main()
