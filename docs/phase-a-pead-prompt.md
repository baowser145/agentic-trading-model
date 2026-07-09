# Phase A — Earnings-Gap PEAD Daily Check

**Strategy being alerted on**: the earnings-gap + 20-trading-day-hold strategy validated in
`backtest/report_pead_earnings_gap.md` (in-sample 2024-2026: +1.70%/trade net of slippage, 615
trades, p=0.00019, independently re-verified from scratch) and OUT-OF-SAMPLE in
`backtest/report_pead_out_of_sample_2022_2024.md` (2022-2024: +3.57%/trade, 500 trades, p<0.00001;
weakest sub-period, the 2022-23 bear/recovery, still +1.69%/trade, p=0.025). This is the only
strategy in this project with a statistically significant edge.

**Mandatory caveat — every single alert must include this, verbatim, no exceptions:**
> ⚠️ Backtested edge validated in-sample (2024-2026: +1.70%/trade, p=0.00019) and out-of-sample
> (2022-2024: +3.57%/trade, p<0.00001). Remaining caveats: survivorship bias (today's S&P 500 list
> applied to historical periods) and large single-trade tail risk (worst historical trade -38%; no
> stop-loss — stops were backtested and made results worse). This is informational only; manual
> approval required for any trade.

**This pipeline never places or reviews orders.** Do not call `place_equity_order`,
`review_equity_order`, `place_option_order`, `review_option_order`, or any similar tool, under any
circumstance.

## Why this is a daily check, not a premarket/intraday scanner

Unlike the original Gap Scan / Trend Join Long (day-trading concept), this strategy enters the day
after an earnings-gap and holds for 20 trading days (~1 month). It needs exactly one check per
trading day, run after market close — no premarket data, no intraday timing, no continuous
monitoring.

## Statelessness

This routine runs in an isolated cloud sandbox with no shared filesystem between runs — it cannot
remember "positions" from a previous day's run. Both ENTRY and EXIT signals must be re-derived from
scratch each run using only earnings-calendar + price data:
- ENTRY = a qualifying gap today (or very recently) with an earnings report nearby.
- EXIT = a qualifying entry from exactly 20 trading days ago (found by looking back, not by reading
  state saved earlier).

## Step 1 — Universe: only tickers with a recent earnings report (not all 500)

Call the Robinhood MCP tool `get_earnings_calendar` for a window covering:
- Today and the last 3 calendar days (for ENTRY candidates).
- The calendar range corresponding to ~20 trading days ago, plus/minus 3 days (for EXIT candidates
  — roughly 26-32 calendar days ago, accounting for weekends; if unsure, widen the window slightly
  rather than risk missing a day).

This naturally limits the ticker list to the handful of companies that reported earnings in each
window — do not scan all ~500 S&P 500 tickers daily; only check tickers that appear in these
earnings-calendar results.

## Step 2 — ENTRY check

For each ticker with an earnings report today or in the last 1-3 calendar days:
1. Get today's quote via `get_equity_quotes` (current price / today's open, previous close) and
   today's volume.
2. Compute `days_since_earnings` = (today's date − that ticker's earnings report date).days.
3. Run via Bash:
   ```
   python3 live_scan/evaluate.py pead-entry --ticker <T> --open <today_open> --prev-close <prev_close> --price <current_price> --volume <current_volume> --days-since-earnings <N>
   ```
4. If `hit=True`: this is an ENTRY signal. Record the ticker, gap %, and today's date (so a
   downstream run 20 trading days from now can find it via Step 3 below — since there's no
   persisted state, "finding it" means re-deriving from the earnings calendar + price history each
   time, not reading a saved file).

## Step 3 — EXIT check

For each ticker with an earnings report ~20 trading days ago (from the widened window in Step 1):
1. Get that historical day's data via `get_equity_historicals` (bounds=regular, interval=day) —
   that day's open/prev-close/volume, to re-check whether it actually qualified as a PEAD entry
   (don't assume — recompute).
2. Run via Bash:
   ```
   python3 live_scan/evaluate.py pead-entry --ticker <T> --open <that_day_open> --prev-close <that_day_prev_close> --price <that_day_close> --volume <that_day_volume> --days-since-earnings <N>
   ```
   to confirm it was a qualifying entry.
3. If it was, count the exact number of trading days between that day and today (using daily bars,
   not calendar days), and run:
   ```
   python3 live_scan/evaluate.py pead-exit --ticker <T> --trading-days-elapsed <count>
   ```
4. If `pead_exit_due=True`: this is an EXIT signal for today.

## Step 4 — Compose and send ONE Discord message

Use `alerts/send_discord.py`. When running locally (the current setup — a local cron job in a
Claude Code session at the repo root), load the webhook first:
`export $(grep -v '^#' config/.env | xargs)` or pass it inline:
`DISCORD_WEBHOOK_URL=$(grep DISCORD_WEBHOOK_URL config/.env | cut -d= -f2-) python3 alerts/send_discord.py ...`

No stop-loss and no projected-exit price (backtested in `backtest_pead_stoploss.py`: every stop
level tested REDUCED expectancy vs. no stop -- e.g. a -5% stop nearly halved the mean return and
dropped win rate below 50%; see `.claude/PROJECT.md` for the full comparison table). Decision: no
stop-loss, no projected exit price -- entries just show a suggested buy price. The exit side is
still handled entirely by the separate EXIT SIGNALS check below (time-based, 20 trading days), which
does report a real current price at exit time (not a projection).

Format:

```
[Robinhood Scanner — Earnings-Gap PEAD Daily Check — <date>]

<mandatory caveat block, verbatim, from the top of this doc>

ENTRY SIGNALS (consider buying at next open, plan to hold ~20 trading days, exit signal comes later via the EXIT check below):
🟢 <TICKER> — gapped <X>% (reported <earnings date>, <AM/PM>)
💰 Current Price: $<price>
🛒 Suggested Buy: ~$<price> (next open)
📊 Backtested: 55.8% win rate in-sample / 60.8% out-of-sample (p=0.00019 / p<0.00001)

(or "No entry signals today.")

EXIT SIGNALS (20-trading-day hold period reached — consider closing):
🔴 <TICKER> — entered ~<date>, 20 trading days elapsed. Consider exiting at today's close (~$<current_price>).
(or "No exit signals today.")

This is informational only. You approve and place any trade yourself in the Robinhood app.
```

Send this via:
```
python3 alerts/send_discord.py "$(cat report.txt)"
```
(write the composed message to a temp file first if it's easier to build multi-line content, then
pass it to the script — do not skip sending just because there were zero signals; a "no signals
today" message confirms the pipeline is alive).

## Hard constraints (repeated for emphasis)

- No order-placement or order-review tool calls, ever.
- No autonomous position sizing/execution — this pipeline only informs, never acts.
- Always include the mandatory caveat block verbatim.
- If data for a ticker is missing/ambiguous, skip it and note the skip in the report rather than
  guessing.
