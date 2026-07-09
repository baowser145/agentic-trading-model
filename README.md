# Robinhood AI Scanner + Discord Alerts

Personal project. See `.claude/PROJECT.md` for scope, `.claude/roast-verdict.md` for the viability
review, and `.claude/plan.md` for the full architecture.

## Status

Milestone 0/1/2: backtest the strategy rules before any live pipeline exists. Phase A (live scan +
Discord alert, manual approval only) only gets built if the backtest beats buy-and-hold net of
costs. Autonomous order execution is explicitly out of scope for this project.

## Backtest

```
cd backtest
python3 run_backtest.py
```

Reads tickers from `tickers.txt`, pulls 2+ years of daily data via yfinance, runs both strategies,
and writes `report.md` with a go/no-go verdict.
