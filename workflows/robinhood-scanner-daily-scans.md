# Workflow: Robinhood Scanner — Daily Earnings Scans

## Objective
Alert on two validated earnings-driven setups via Discord: a pre-earnings options screen (weaker evidence, options overlay unvalidated) and a post-earnings-drift (PEAD) gap strategy (validated in-sample and out-of-sample). Each strategy must clear its backtest gate before going live, and stays stateless in production — every run re-derives signals from price + earnings data rather than trusting persisted state.

## Required Inputs
- Alpha Vantage API key (`EARNINGS_CALENDAR` endpoint).
- yfinance access (quotes, option chains, daily bars — no key required).
- `DISCORD_WEBHOOK_URL` / Discord bot token for `alerts/send_discord.py`.
- `backtest/cache/` price data (warm cache speeds up re-verification).

## Tools to Use
Location: `robinhood_scanner/`.

1. **Backtest gate (manual, run before trusting any live signal from a strategy)**
   - `backtest/run_backtest.py` — original strategy sweep (gap-scan/trend-join long strategies — currently **failed** the Milestone 0 gate, do not alert on these).
   - `backtest/run_pead_all_us.py` + `backtest/verify_pead.py` — PEAD (earnings-gap entry, 20-day hold) — **passed**: +1.70%/trade in-sample (p=0.00019, n=615), +3.57%/trade out-of-sample on 2022-2024 (p<0.00001, n=500). This is the only strategy currently cleared for the evening scan below.
   - Outputs: `backtest/report_*.md`, `backtest/results/*_results.json`.
   - Known limitation: 6-7 strategy variants were tested in one research session — treat PEAD's edge as first-among-many until it's re-validated on a fresh out-of-sample window.

2. **Pre-earnings screen** — cron `45 9 * * 1-5` (GitHub Actions `.github/workflows/daily-scans.yml`), runs `live_scan/daily_pre_earnings_screen.py`:
   - Pulls Alpha Vantage earnings calendar (18-30 day window), yfinance quotes/chains (S&P 500 only).
   - Ranks candidates via `backtest/rank_pre_earnings_candidates.py` (2yr backtested win rate ≥75%, worst-case ≥-20%), prices options via Black-Scholes (budget <$300, bid>0, delta≥0.15).
   - Posts a Discord card per ticker via `alerts/send_discord.py`.
   - **Always posts**, even with zero candidates — an empty run confirms the job is alive, don't treat it as a failure signal.
   - Caveat: weaker evidence than PEAD — no out-of-sample validation, options overlay unvalidated, per-ticker win rates are small-n (~8). The *pooled* edge is the validated result, not any single ticker's rate.

3. **PEAD check** — cron `15 21 * * 1-5` (same workflow file), runs `live_scan/daily_pead_check.py`:
   - Scans S&P 500 daily bars for ≥5% gaps, filters to earnings-window (today ±3 days for entry, ~26-32 days ago for exit).
   - Re-verifies entry/exit conditions via `live_scan/evaluate.py` (`backtest/strategies.py::qualifying_gap`, `earnings_gap_pead_entry`, `earnings_gap_pead_exit_due`).
   - Posts entry signals (ticker, gap%, price) and exit signals (ticker, days held, exit price) via Discord.
   - **Stateless by design** — every run re-derives entries/exits from price + earnings data; there is no persisted position list. If you add position tracking, keep this re-derivation as a sanity check, don't replace it outright.
   - **No stop-loss** — backtested and found to reduce returns; worst-case tail risk is -38%. Don't add one without re-running the backtest gate.

## Expected Outputs
- Discord alerts (pre-earnings screen ~9:45am UTC, PEAD check ~9:15pm UTC on weekdays).
- `backtest/report_*.md`, `backtest/results/*.json` (only touched when the gate is re-run manually).

## Edge Cases & Error Handling
- **Zero candidates/signals** — expected and still posts, to confirm liveness. Don't add alerting-on-silence without also alerting on the job itself failing to run.
- **A strategy that hasn't passed the backtest gate** (e.g. gap-scan/trend-join) must not be wired into a live scan job — check `backtest/report_*.md` before adding a new strategy to `live_scan/`.
- **Multiple-comparisons risk** — before trusting a newly "passing" strategy, check how many variants were tried in the same research session (see `.claude/PROJECT.md`/`HANDOFF.md`).

## Success Criteria
- Both scheduled GitHub Actions runs complete and post (or explicitly post "no signals") every weekday.
- No strategy alerts live without a corresponding entry in `backtest/report_*.md` showing it cleared the gate.
