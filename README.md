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

## Layout

- `backtest/strategies.py` — canonical strategy rules; ALL gap/window math lives here. The live
  CLI (`live_scan/evaluate.py`) imports these functions, never re-derives them.
- `backtest/cache/` — untracked input data (price CSVs, earnings-date JSONs from Robinhood MCP).
  `rank_pre_earnings_candidates.py` warns loudly when these go stale.
- `backtest/results/` — tracked analysis outputs (the JSONs the reports cite).
- `alerts/send_discord.py` — webhook sender; splits >2000-char messages on card boundaries and
  honors Discord 429 rate limits. Needs `DISCORD_WEBHOOK_URL` (see `config/.env.example`).
