# Small-Sample Sanity Check — FULL 4-Condition Trend Join Long

**Warning: This is a SMALL-SAMPLE SANITY CHECK, not a replacement for the primary 2-year/500-ticker gate
in report_sp500.md.** It exists to test the actual full strategy definition -- including
premarket_high and hour_et, which the primary gate could not test because free 2-year daily-bar
data has no premarket depth. Sample size here is small (20 tickers, 7 trading days, 33 trades) --
treat results as indicative only, not conclusive.

## Why the sample is this small

Robinhood's get_equity_historicals MCP tool returns real intraday extended-hours bars (tagged
"session": "pre" during premarket), which is exactly the data needed. But pulling this at full
S&P-500/2-year scale is not practical through this interface: a single request for 10 symbols over
even 7 trading days at hourly granularity already returned ~200,000 characters and had to be
redirected to disk instead of returned inline. Scaling that to 500 tickers over a meaningful window
would be several million characters of raw bar data -- not tractable to process through tool-call
responses in one session. So this check trades sample size for feasibility:

- Tickers: 20, sampled (first 20 alphabetically) from the 297 S&P 500 tickers that had at least
  one 2-condition hit (close > prev_day_high and close > sma200) in the test window.
- Window: last 7 trading days with a 5-trading-day exit buffer (2026-06-17 to 2026-06-26 for
  signals; exits use daily closes through 2026-07-06, already cached from the primary backtest).
- Premarket data: real Robinhood extended-hours historicals, interval=hour, bounds=extended,
  filtered to session=="pre" bars, max high_price per ticker per day. Hourly (not finer)
  granularity was used to keep bar volume manageable -- this is a coarser proxy for premarket high
  than 5-minute bars would give, but doesn't change which side of the threshold most days fall on.
- hour_et condition: trivially satisfied (this backtest evaluates at end-of-day daily close,
  which is always after 10am ET by construction -- the hour filter only has real meaning for intraday
  entry timing, not for a daily-close-based check).

## Method

1. Computed the cheap 2-condition hits from already-cached daily bars (yfinance, no new calls).
2. For the 20 sampled tickers, pulled real premarket highs via 2 Robinhood get_equity_historicals
   calls (10 symbols each, hourly, extended bounds) -- both were too large to return inline and were
   auto-saved to disk, then parsed with Python to extract just the premarket highs (2,240 raw
   bars processed, only the extracted per-ticker/per-date highs kept).
3. Applied the full 4-condition filter: close > prev_day_high AND close > sma200 AND close >
   premarket_high (hour_et treated as always-true, per above).
4. Simulated trades identically to the primary backtest: enter next day's open, exit at close 5
   trading days later, net of 5bps/side slippage, compounded per ticker then averaged across
   tickers (not pooled across tickers into one curve -- trades on different tickers are concurrent
   bets, not sequential ones, so per-ticker compounding is the methodologically correct aggregation,
   matching run_backtest.py's aggregate_verdict).

## Results

| Metric | Value |
|---|---|
| 2-condition hit-days in sample | 48 |
| Hit-days with premarket data available | 48 (0 skipped) |
| Full 4-condition trades (after premarket-high filter) | 33, across 18 of 20 tickers |
| Avg strategy total return (per-ticker compounded, averaged across the 18 traded tickers) | +2.01% |
| Avg per-trade expectancy | -0.01% (essentially zero) |
| Avg buy-hold, same 18 traded tickers, same window | +3.02% |
| Avg buy-hold, all 20 sampled tickers, same window | +3.43% |
| Beats buy-and-hold? | No |

### Per-ticker detail

| Ticker | Trades | Win Rate | Avg Return/Trade | Total Return | Buy&Hold |
|---|---|---|---|---|---|
| ABBV | 4 | 100% | 5.59% | 24.08% | 14.52% |
| ACGL | 3 | 100% | 3.95% | 12.31% | 5.60% |
| ALGN | 2 | 100% | 5.95% | 12.24% | 2.82% |
| AMAT | 2 | 50% | 6.49% | 11.78% | 5.72% |
| ALL | 3 | 100% | 3.35% | 10.36% | 8.10% |
| AEP | 4 | 50% | 1.04% | 4.10% | 8.12% |
| AMCR | 1 | 100% | 2.82% | 2.82% | 5.54% |
| ABNB | 1 | 100% | 0.93% | 0.93% | 3.57% |
| ADM | 1 | 100% | 0.36% | 0.36% | 0.38% |
| AME | 1 | 0% | -0.06% | -0.06% | 2.70% |
| AES | 1 | 0% | -0.17% | -0.17% | 0.41% |
| ANET | 1 | 0% | -0.90% | -0.90% | -4.44% |
| AFL | 1 | 0% | -1.65% | -1.65% | 3.36% |
| APA | 1 | 0% | -2.59% | -2.59% | -2.71% |
| AMD | 1 | 0% | -4.48% | -4.48% | 1.78% |
| AEE | 3 | 0% | -1.61% | -4.76% | 8.63% |
| AMT | 1 | 0% | -9.18% | -9.18% | -3.04% |
| ADI | 2 | 0% | -10.02% | -19.06% | -6.64% |

## Verdict: FAIL (consistent with the primary gate)

Adding the two "not optional" conditions (premarket-high, hour-of-day) does not rescue the strategy.
Per-trade expectancy is essentially zero (-0.01%), and the strategy underperforms simply holding the
same stocks over the same window (+2.01% vs +3.02-3.43%). This matches the primary 2-year/500-ticker
finding for the weaker 2-condition version (+0.14%/trade, ~breakeven, underperforms buy-hold).

Given both the large-scale 2-condition test and this real-data full-condition small sample point the
same direction, there is no indication that adding premarket-high/time-of-day filtering turns Trend
Join Long into a strategy with positive, real edge. The live pipeline (Phase A) remains not built.
