# Robinhood AI Scanner + Discord Alerts

Personal project. See `.claude/PROJECT.md` for scope, `.claude/roast-verdict.md` for the viability
review, and `.claude/plan.md` for the full architecture.

## Status

Milestone 0/1/2: backtest the strategy rules before any live pipeline exists. Phase A (live scan +
Discord alert, manual approval only) only gets built if the backtest beats buy-and-hold net of
costs. Autonomous order execution is explicitly out of scope for this project.

## Setup

```
pip install -r requirements.txt
python3 -m pytest tests/        # strategy math + Discord chunking tests
```

## Backtest

```
cd backtest
python3 run_backtest.py
```

Reads tickers from `tickers.txt`, pulls 2+ years of daily data via yfinance, runs both strategies,
and writes `report.md` with a go/no-go verdict.

## Scheduled scans (GitHub Actions)

Both daily alert jobs run as plain Python on GitHub Actions — no Claude session, no Robinhood
access (yfinance + Alpha Vantage market data only, informational Discord alerts only):

- `live_scan/daily_pre_earnings_screen.py` — weekdays 12:15 UTC (premarket), spec in
  `docs/pre-earnings-screen-prompt.md`
- `live_scan/daily_pead_check.py` — weekdays 21:15 UTC (after close), spec in
  `docs/phase-a-pead-prompt.md`

Schedule + manual trigger (with dry-run) live in `.github/workflows/daily-scans.yml`. Requires two
repo Actions secrets: `DISCORD_WEBHOOK_URL` and `ALPHAVANTAGE_API_KEY` (free key). Local test:

```
python3 live_scan/daily_pead_check.py --dry-run
python3 live_scan/daily_pre_earnings_screen.py --dry-run   # needs ALPHAVANTAGE_API_KEY in config/.env
```

## Layout

- `backtest/strategies.py` — canonical strategy rules; ALL gap/window math lives here. The live
  scan scripts (`live_scan/daily_pead_check.py`, `live_scan/evaluate.py`) import these functions,
  never re-derive them.
- `live_scan/market_data.py` — free-data layer for the scheduled scans (yfinance bars/chains/
  earnings dates, Alpha Vantage earnings calendar, Black-Scholes delta).
- `backtest/cache/` — untracked input data (price CSVs, earnings-date JSONs from Robinhood MCP).
  `rank_pre_earnings_candidates.py` warns loudly when these go stale.
- `backtest/results/` — tracked analysis outputs (the JSONs the reports cite).
- `alerts/send_discord.py` — webhook sender; splits >2000-char messages on card boundaries and
  honors Discord 429 rate limits. Needs `DISCORD_WEBHOOK_URL` (see `config/.env.example`).
