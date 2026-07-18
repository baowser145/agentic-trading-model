# Session Handoff — Robinhood AI Scanner + Alert System — 2026-07-09T00:00:00Z

## One-Line Status
PEAD strategy is validated (in-sample AND now out-of-sample) and running live via two daily local cron jobs (PEAD post-earnings + pre-earnings timing screen); today's out-of-sample backtest confirmed the edge holds up on 2022-2024 data, a period not used in the original validation.

## Project Path
/Users/vubl/projects/robinhood-scanner

## Phase
build (Phase A) → ongoing daily operation. Not a git repo (no version control in use for this project).

## Roast Verdict
**Reshape** (2026-07-06, `.claude/roast-verdict.md`) — original gap-scanner/trend-join-long concept had no validated edge; reshaped scope required a backtest gate before any live pipeline, explicitly deferred autonomous execution (Phase B) out of scope.

## Decisions Made
- Original Gap Scan / Trend Join Long strategies: backtested against full S&P 500, both FAILED the go/no-go gate (see `backtest/report_sp500.md`). Live pipeline was NOT built on these.
- PEAD (earnings-gap + 20-trading-day hold) strategy: discovered via a PEAD-filter test, validated at full 2-year/S&P-500 scale (+1.47-1.70%/trade, p<0.002, independently re-verified with different code). Decided to build Phase A around this strategy instead of the original video-inspired ones.
- Pre-earnings timing strategy (buy 18-30 days before earnings, sell day before report): weaker/secondary signal, real but smaller effect size than PEAD, validated with a random-entry control to rule out generic bull-market drift. Restricted to S&P 500 only (all-US test showed 2-5x worse risk-adjusted quality outside S&P 500 for both strategies).
- No stop-loss on PEAD: backtested explicitly (`backtest_pead_stoploss.py`) — every stop level tested reduced expectancy vs. no stop. Decision: no stop-loss, no fabricated price target; entries show only a suggested buy price, exits are purely time-based (20 trading days).
- Cloud routines (RemoteTrigger) abandoned for Discord delivery — the CCR sandbox blocks all outbound Bash network calls except the attached MCP connector, so scheduled Discord posts silently failed. Pivoted to local `CronCreate` jobs instead (session-only, expire after 7 days, only run while this Claude Code session stays open).
- Both bid AND ask now shown explicitly on every option quote (after a user-reported "bid vs ask" confusion against Robinhood's own app UI).
- Win rate filter for pre-earnings screen raised to >=75% (from >=60%) per user request; option picks require ALL THREE of budget (<$300), real liquidity (bid>0), and delta>=0.15, else reported as "stock-only" with a stated reason.
- **2026-07-09: out-of-sample validation completed** (was an open item since 2026-07-06) — PEAD strategy backtested fresh on 2022-07-06 to 2024-07-06 (a period NOT used in the original validation), using freshly pulled earnings-calendar data and new price data (distinct cache files, did not touch the live pipeline's existing caches). Result: edge replicated and came in *stronger* than in-sample (500 trades, 60.8% win rate, +3.57%/trade, p<0.00001 combined; even the weaker bear-market-tail sub-period stayed positive and significant at p=0.025). See `backtest/report_pead_out_of_sample_2022_2024.md`.

## What's Built
- `backtest/strategies.py` — shared strategy rule definitions (gap_scan, trend_join_long, EarningsGapPeadParams, is_earnings_gap, earnings_gap_pead_entry, earnings_gap_pead_exit_due) — done.
- `backtest/data.py` — yfinance fetch + disk cache helpers, S&P 500 ticker list via Wikipedia — done.
- `backtest/run_backtest.py`, `run_pead_all_us.py`, `verify_pead.py`, `backtest_pead_stoploss.py`, `backtest_pre_earnings_timing.py`, `backtest_pre_earnings_control.py`, `backtest_pre_earnings_all_us.py`, `verify_pre_earnings_timing.py` — all backtest/validation scripts — done, all findings written to `backtest/report_*.md`.
- `backtest/fetch_2022_2024_prices.py` + `backtest/verify_pead_oos_2022_2024.py` — out-of-sample validation, done today (2026-07-09).
- `backtest/rank_pre_earnings_candidates.py` — live daily ranking script for the pre-earnings screen — done, actively used by the morning cron job.
- `live_scan/evaluate.py` — CLI helper applying strategy math (gap, trend, pead-entry, pead-exit) via Bash so the LLM never free-hands arithmetic — done.
- `alerts/send_discord.py` — Discord webhook delivery with proper message-splitting (fixed a truncation bug that cut a card off mid-line) — done.
- `config/.env` — real Discord webhook URL (gitignored) — done.
- **Two active local cron jobs** (session-only, expire after 7 days, require this Claude Code session/terminal to stay open):
  - `eb540aa4` — weekdays 3:15pm CDT — daily PEAD post-earnings check.
  - `bea429c1` — weekdays 7:33am CDT — daily pre-earnings timing screen.
- Both jobs have fired automatically at least once successfully and delivered to Discord.

## Verification Status
- No formal `.claude/build-log.md` exists for this project — verification has been done ad hoc via: independent from-scratch re-implementations of each backtest (different code, same/similar data), random-entry controls, full-S&P-500-scale testing (not small samples), and self-caught/corrected errors (e.g. the KKR filtering mistake on 2026-07-07, corrected via a follow-up Discord message same day).
- Out-of-sample validation (2022-2024) — PASS (2026-07-09), see Decisions Made above.
- Nothing currently failing or blocked.

## Active Goals
- Keep both daily cron jobs running (they auto-expire after 7 days — will need recreating around 2026-07-14/15 if the user wants continuity).
- No new backtest or build work explicitly requested beyond what's done; project is in steady daily-operation mode.

## Open Blockers
- Local cron jobs only run while this specific Claude Code session/terminal stays open — closing it kills both jobs. No durable "laptop can be off" automation exists yet (would require a Discord MCP connector for the cloud RemoteTrigger path, which was not explored/confirmed to exist — see PROJECT.md 2026-07-07 update).
- Local cron jobs auto-expire after 7 days regardless of session state and need manual recreation.
- Project has no git repository — no version history, no ability to diff/rollback via git.

## Next 3 Actions (in order)
1. If continuing daily operation: just keep monitoring Discord for the two daily alerts; no code changes needed.
2. Before ~2026-07-14/15: recreate both cron jobs (`eb540aa4` PEAD, `bea429c1` pre-earnings) since they auto-expire after 7 days.
3. If durable (session-independent) automation becomes a priority: investigate whether a Discord MCP connector exists (claude.ai/customize/connectors) that could be attached to a cloud RemoteTrigger routine, avoiding the local-cron session dependency.

## Resume Prompt
Copy-paste this into a fresh session:

> Read `.claude/HANDOFF.md` and `.claude/PROJECT.md` in /Users/vubl/projects/robinhood-scanner, then continue from "Next 3 Actions" item 1. Do not re-ask intake questions. Current phase: build (Phase A), ongoing daily operation — PEAD strategy validated in-sample and out-of-sample, two local cron jobs running daily checks.

## Files Touched This Session
- `backtest/cache/sp500_earnings_dates_2022_2024.json` (new)
- `backtest/fetch_2022_2024_prices.py` (new)
- `backtest/cache/{ticker}_2022_2024.csv` for 499 tickers (new)
- `backtest/verify_pead_oos_2022_2024.py` (new)
- `backtest/cache/pead_oos_2022_2024_results.json` (new)
- `backtest/report_pead_out_of_sample_2022_2024.md` (new)
- `.claude/PROJECT.md` (appended: 2026-07-08 PEAD daily check note, to be appended again below with today's out-of-sample update)
- `.claude/HANDOFF.md` (this file, new)
- No changes to any live-pipeline file (`live_scan/evaluate.py`, `alerts/send_discord.py`, `rank_pre_earnings_candidates.py`, `strategies.py`) or existing cache the live cron jobs depend on.
