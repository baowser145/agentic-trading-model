# Workflows

This repo hosts three semi-independent pipelines. Each doc covers objective, required inputs, exact CLI/module per stage, outputs, and known edge cases.

- [robinhood-scanner-daily-scans.md](robinhood-scanner-daily-scans.md) — `robinhood_scanner/`: pre-earnings options screen + PEAD gap strategy, GitHub-Actions-scheduled Discord alerts. Backtest-gated — only PEAD has cleared validation.
- [stock-screener-30d.md](stock-screener-30d.md) — `stock_screener_30d/`: pullback screener with a 30-day paper-trade tracker and local dashboard, Docker-deployed to EC2.
- [core-trading-loop.md](core-trading-loop.md) — `src/agentic_trading/`: research → select → risk-gate → propose/execute loop, paper by default, live mode requires a human-reviewed intent.

## Notes
- These three are independently deployable (separate Dockerfiles/entrypoints) even though they live in one repo — don't assume a change in one affects the others.
- Every live-facing pipeline here defaults to the safe mode (paper trading, or a backtest gate that must pass before a strategy is wired into `live_scan/`). Preserve that default when adding new strategies.
