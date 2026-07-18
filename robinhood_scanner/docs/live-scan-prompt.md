# Live Dry-Run Scan — Instructions for the Scheduled Session

**Status: INFORMATIONAL DRY-RUN ONLY.** The backtest (see `backtest/report_sp500.md`) showed Gap
Scan has negative expectancy and Trend Join Long is statistically flat (~breakeven) across the full
S&P 500. Neither strategy has a demonstrated edge. This run exists to test the live-data pipeline
and show what the scanner would flag — **not** to produce trades to act on.

## What to do, in order

1. **Watchlist**: AAPL, NVDA, AMD, TSLA, MSFT, META, GOOGL, AMZN (from `backtest/tickers.txt`).

2. **For each ticker**, use the connected Robinhood MCP tools to get:
   - Current quote: `get_equity_quotes` — current price, previous close.
   - Historicals: `get_equity_historicals` (daily, ~250+ bars, `bounds=regular`) — to determine
     prior day's high and compute the 200-day SMA of close prices.
   - Premarket high (**mandatory, not optional**): `get_equity_historicals` with `bounds=extended`
     and an intraday `interval` (e.g. `5minute` or `30minute`), `start_time` = today 4:00am ET,
     `end_time` = now. Filter returned bars to `session == "pre"` and take `max(high_price)`.
   - Current hour ET (**mandatory, not optional**): read the system clock converted to
     `America/New_York` (e.g. `TZ=America/New_York date +%H.%M`) — no data-access needed, this is
     just the current time.

3. **Evaluate each ticker** by calling the Python helper via Bash (do not hand-compute the
   arithmetic yourself — this keeps the exact numbers consistent with what was backtested).
   Trend Join Long requires all four values every time — do not omit `--premarket-high` or
   `--hour-et`; the script will error if they're missing, by design:
   ```
   python3 live_scan/evaluate.py gap --ticker <T> --open <today_open> --prev-close <prev_close> --price <current_price> --volume <current_volume>
   python3 live_scan/evaluate.py trend --ticker <T> --close <current_price> --prev-day-high <prior_day_high> --sma200 <sma200> --premarket-high <premarket_high> --hour-et <current_hour_et>
   ```

4. **Report the results** as a plain summary to the user (console output / chat — no Discord, no
   external delivery in this dry run). For every ticker, whether it hit or not. At the top of the
   report, always include this exact line:

   > ⚠️ Informational only. Backtest shows Gap Scan has negative expectancy and Trend Join Long is
   > ~breakeven across the full S&P 500 (see backtest/report_sp500.md). These are not validated
   > trade signals.

5. **Do not**:
   - Call any Robinhood order-placement or order-review tool (`place_equity_order`,
     `review_equity_order`, `place_option_order`, `review_option_order`, etc.) — this pipeline never
     places trades.
   - Suggest a specific entry/stop/size as if it were a recommendation to act on.
   - Post to Discord or any external channel — this run is informational-console-output only.

## If the user later wants to re-enable Phase A (live alerts to act on)

Do not do this automatically. Phase A (Discord alerts with a suggested trade, manual approval) was
paused because the backtest gate failed. Re-enabling it requires the user to explicitly say so,
ideally after the strategy has been reworked (e.g. trailing-stop exits) and re-passed the backtest
gate — see `.claude/plan.md` and `.claude/PROJECT.md` for the gate rule.
