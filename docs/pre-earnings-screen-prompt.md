# Pre-Earnings Timing Screen — Daily Morning Check (weekdays before market open)

Reconstructed 2026-07-09 from `.claude/PROJECT.md` change log (2026-07-07/08 updates) after the
original instructions — which lived only inside a session-local cron job — were lost when that
session closed. This doc is now the canonical copy; any cron job should point here.

**Strategy**: buy S&P 500 names ~18-30 days before their earnings report, sell the day before the
report. Validated as an incremental edge over a random-entry control (~+0.7-1.1pp, peak at 18-30
day lead; see `backtest/backtest_pre_earnings_timing.py` and
`backtest/results/pre_earnings_timing_results.json`). Weaker evidence than the PEAD strategy —
smaller effect, no out-of-sample period test. S&P 500 ONLY (all-US test showed 4-5x worse
risk-adjusted quality outside it).

**This pipeline never places or reviews orders.** Do not call `place_equity_order`,
`review_equity_order`, `place_option_order`, `review_option_order`, or any similar tool, under any
circumstance. Informational alerts only; the user approves and places trades manually.

## Procedure

Run from the repo root (`/Users/vubl/projects/robinhood-scanner`).

1. **Candidate pull**: `get_earnings_calendar` for the window 18-30 calendar days from today.
   Filter to S&P 500 (cross-reference `backtest/cache/sp500_tickers.txt`). Write the result to
   `backtest/cache/upcoming_sp500_earnings.json` as a list of
   `{"ticker", "date", "timing" (am/pm), "verified" (bool)}`.
2. **Rank**: `python3 backtest/rank_pre_earnings_candidates.py`. Obey its output:
   - If it prints staleness WARNINGs, refresh the named cache (earnings dates via
     `get_earnings_calendar`, prices via `backtest/data.py` fetch helpers) before trusting ranks.
   - The quality filter (win_rate >= 75% AND worst >= -20%, enforced by the script — never
     re-derive it ad hoc; a hand-filtered list once wrongly included KKR) yields the top 10.
   - Include the gist of its printed STATISTICAL CAVEAT in the alert (per-ticker win rates are
     n~8 small samples; the pooled edge is the validated result).
3. **Live data per top-10 ticker**: current price via `get_equity_quotes`, company name via
   `get_equity_fundamentals`.
4. **Option pick per ticker** (real quotes only, never fabricated):
   - Chain via `get_option_chains`; expiration = nearest listed date ON/AFTER the report date.
   - Start at the strike nearest `current_price * 1.075` (~5-10% OTM call).
   - Requirements, ALL THREE: cost = ask * 100 < $300; real liquidity (bid > 0); delta >= 0.15.
   - If the starting strike fails, search progressively deeper OTM. If nothing qualifies, report
     **stock-only** with the stated reason (e.g. "too expensive even at deepest strike" or "only
     illiquid/near-zero-delta strikes fit the budget") — never present a technically-in-budget but
     low-quality strike as a real trade idea.
5. **Compose cards** (one per ticker, blank line between cards — `send_discord.py` splits long
   messages on blank lines):

   ```
   🟢 TICKER — Company Name (reports YYYY-MM-DD am/pm)
   💰 Current Price: $X.XX
   🛒 Buy Under: $X.XX
   📈 $STRIKEc exp MM/DD — Bid $X.XX / Ask $Y.YY (buy = ask, $Z/contract, delta 0.NN)
   ```

   - Emoji: 🟢 if the ticker's own backtested win rate >= 75%, 🟡 if 60-74% (based on its OWN
     history from the ranking script, not analyst data).
   - Buy Under = current price × 0.99 — an execution-buffer convention only, NOT a backtested
     level. No Sell Target, Stop Loss, or G/L Ratio fields, ever (no validated basis; the exit is
     a DATE: the day before the report).
   - Option line only when step 4 found a qualifying strike; otherwise
     `📈 Stock-only (<reason>)`. Note thin liquidity explicitly when bid size/price is marginal.
6. **Caveats in the message** (short — one line each):
   - The stock backtest does not validate the options (leverage/theta/IV never tested); quotes are
     real, the strategy on options is not proven.
   - The statistical caveat gist from step 2.
   - Robinhood's app shows the Bid by default on its Sell tab — our "buy = ask" number will look
     higher than the app's headline price.
7. **Send**: `alerts/send_discord.py` with `DISCORD_WEBHOOK_URL` loaded from `config/.env`.
   ALWAYS send, even if zero candidates pass ("no qualifying candidates today") — a daily message
   confirms the pipeline is alive. Write the message to a temp file and pipe it in rather than
   fighting shell quoting.

## Hard constraints (repeated)

- No order-placement or order-review tool calls, ever.
- Real quotes only — if a data point is missing/ambiguous, say so or skip the ticker; never guess.
- S&P 500 only.
- Filters come from the script's output, not ad-hoc re-derivation.
