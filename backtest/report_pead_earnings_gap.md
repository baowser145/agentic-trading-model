# PEAD Earnings-Gap Filter Test — FULL 2-YEAR RESULTS (supersedes the earlier 7-month pass)

## Summary of what changed

An earlier pass on a 7-month sample (2025-11-01 to 2026-06-04) suggested earnings-gap trades had
positive same-day expectancy (+0.38%/trade). The user asked for the full 2-year version to confirm.
**The full 2-year test does NOT confirm that same-day result** -- it was noise. But it surfaces a
DIFFERENT, statistically significant finding: **the classic PEAD pattern (20-trading-day hold after
an earnings gap) shows a real, positive, statistically significant edge.** This is the first result
in this entire project with p < 0.01.

## Data

- Earnings dates: 4,030 verified S&P 500 earnings events, 501 tickers, full window 2024-05-23 to
  2026-07-01 (25 overlapping ~31-day windows pulled via Robinhood's get_earnings_calendar,
  deduplicated). 17 of 25 windows were too large to return inline and were redirected to disk by the
  harness; those were parsed with Python without loading raw bars into context. The other 8 came
  back inline and were manually transcribed to compact {symbol, date, verified} form.
- Price/gap data: same cached 3-year daily bars (yfinance) used throughout this project.
- Test window: 2024-07-06 to 2026-07-06 -- identical to the primary Gap Scan / Trend Join Long
  2-year backtest window (report_sp500.md), for direct comparability.
- Same trade construction as before: Gap Scan hit (gap >= 5%, price >= $3, volume >= 500k) tagged
  earnings-day (report am same day, or pm within 1-3 days), 5bps/side slippage.

## Results (full 2-year, full S&P 500)

| Bucket | Tickers | Trades | Avg per-trade expectancy | Win rate | Significance |
|---|---|---|---|---|---|
| Earnings-gap, same-day exit | 291 | 559 | -0.09% | 48.5% | p=0.454 (NOT significant -- the earlier +0.38% was noise) |
| Non-earnings-gap, same-day exit | 260 | 902 | -0.70% | 42.0% | pooled mean -0.56% |
| **Earnings-gap, PEAD 20-trading-day hold** | 289 | 555 | **+1.47%** | **55.3%** | **p=0.0013** |

Earnings-gap vs non-earnings-gap (same-day) difference: p=0.062 (borderline, not quite significant
at full scale, versus p=0.019 in the smaller sample -- weakens with more data, another sign the
same-day effect isn't robust).

Avg buy-and-hold, full 2-year window, 503 tickers: +59.52% (matches report_sp500.md's +60.66%
closely, cross-validating the two runs used consistent data).

## Interpretation

- **Same-day earnings-gap "pop" is not real** -- this is a good example of exactly the discipline
  this project has tried to apply throughout: a promising small-sample result (p was never even
  checked as very strong, and turned into noise at scale). Do not build anything on the same-day
  version.
- **The PEAD 20-day hold is real and meaningfully different from everything else tested in this
  project**: 555 trades, +1.47%/trade net of slippage, p=0.0013 (roughly a 1-in-800 chance this is
  noise), win rate 55.3%. This is economically consistent with the academic PEAD literature (drift
  continues over weeks, not just the announcement day) and is the first strategy variant in this
  project's entire testing process that clears a reasonable significance bar.
- It still does not beat the raw +59.52% buy-and-hold figure in total portfolio return -- but that
  comparison undersells an intermittent-signal strategy (555 trades total across 289 tickers over 2
  years, each holding for only ~1 month, versus being fully invested continuously). The per-trade
  expectancy and its significance are the right way to judge whether this rule has real edge, and by
  that measure, this is the strongest result so far.

## Practical implication: this is a DIFFERENT strategy than originally scoped

This is not a day-trading / premarket-scanner strategy like the original Gap Scan or Trend Join
Long. It is an event-driven swing strategy:
1. Detect a gap >=5% (price >= $3, volume >= 500k) on a day the company reports earnings.
2. Enter next day's open.
3. Hold for 20 trading days (~1 calendar month).
4. Exit at close.

This needs an earnings calendar feed (Robinhood's get_earnings_calendar, confirmed working) plus
the existing gap-detection logic -- but does NOT need premarket-high or intraday-timing data (the
conditions that turned out not to matter here), and does NOT need constant intraday monitoring --
one check per day is enough, since positions are held for a month at a time.

## Verdict: PASS (with caveats) -- this is the first strategy variant in the project with a
statistically real edge

Recommend treating this, not the original Gap Scan/Trend Join Long, as the candidate for the live
scanner if the user wants to proceed to Phase A. Caveats to carry forward:
- 2 years / 555 trades is a real sample but still a single historical period (2024-2026); no
  out-of-sample/forward validation has been done yet.
- Win rate 55.3% is decent but not overwhelming -- risk management (position sizing, max concurrent
  positions) matters a lot given std dev of returns is large (10.68%) relative to the mean (1.47%).
- This strategy's 20-day hold conflicts with the project's original "day trading alert" framing --
  if the user wants a daily-scan-and-forget system, this doesn't fit that shape as cleanly (it wants
  ongoing position tracking over a month, not same-day resolution).
