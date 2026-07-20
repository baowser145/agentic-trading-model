# Workflow: Stock Screener 30D

## Objective
Screen for stocks in a pullback-within-uptrend setup (RSI 40-60, above 50-day SMA, 5-15% pullback, min 500k volume), track picks as paper trades over a fixed 30-trading-day hold, and report win rate / return versus a validated historical backtest — all exposed through one `stock-screener` CLI plus a local read-only dashboard.

## Required Inputs
- yfinance access (quotes, historical bars).
- `config/criteria.yaml` (screen thresholds, scoring weights).
- Persisted state: `data/paper-trades.csv`, `data/backtest-cache.json`.

## Tools to Use
Location: `stock_screener_30d/`. All stages share one CLI: `stock-screener <subcommand>`.

1. **Scan** — `stock-screener scan [--top N] [--with-targets] [-o out.csv]`, module `src/stock_screener_30d/screener.py::run_scan()`/`passes_criteria()`/`composite_score()`. Filters against `config/criteria.yaml`, scores by RSI proximity (40%) + pullback quality (40%) + volume (20%). Scheduled weekdays 18:30 ET via Docker cron (or run manually / via GitHub Actions if configured later).
2. **Backtest** — `stock-screener backtest`, modules `backtest.py` + `backtest_cache.py`. Replays the same criteria over 2019-2024 daily bars with a fixed 30-day hold and 0.1% round-trip cost, reports win rate/avg return/total return vs SPY. This is the benchmark stage 3's live paper results get compared against — re-run it whenever `criteria.yaml` changes.
3. **Paper log** — `stock-screener log [--update] [--status] [--report]`, module `paper_log.py`. `log` appends today's scan picks; `log --update` closes any trade that's hit its 30-day hold first; `--status` shows open P&L; `--report` gives win rate/avg return/best-worst. Scheduled weekdays 18:30 ET (`--update` variant, so matured trades close before new ones are logged).
4. **Web dashboard** — `stock-screener` web server / `uvicorn stock_screener_30d.web:app` (FastAPI, `web.py`), compares paper vs backtest via `comparison.py`. Local-only, port 8000, **no auth** — restrict via security group/VPN, don't expose publicly as-is.
5. **Deploy** — `docker-compose up` on the target host (currently AWS EC2), entrypoint `scripts/docker-entrypoint.sh`. Optional `BACKTEST_ON_START=1` warms the backtest cache on container start. In-container crontab runs `stock-screener log --update` weekdays 18:30 ET.

## Expected Outputs
- `data/paper-trades.csv` (entry/exit date, price, return% — source of truth for live performance).
- `data/backtest-cache.json` (speeds up repeated backtest runs).
- Console reports (`--report`, `--status`).
- Dashboard at `localhost:8000` (or the deployed host, VPN/security-group restricted).

## Edge Cases & Error Handling
- **Cron drift (~15min) inside the container** — known and tolerated; don't chase exact-minute timing.
- **GitHub disables inactive repos after ~60 days** — any commit resets the clock; relevant if GitHub Actions is later wired up for scanning instead of the in-container cron.
- **No dashboard auth** — this is a deliberate simplification, not an oversight; keep it off the public internet.
- **Criteria change** — always re-run the backtest (stage 2) after editing `config/criteria.yaml`, since the paper-vs-backtest comparison in the dashboard is only meaningful if both used the same criteria.

## Success Criteria
- `data/paper-trades.csv` has no position open past 30 trading days without being closed by the next `log --update` run.
- Backtest cache and paper trade log agree on the criteria version in use (re-run backtest after any `criteria.yaml` change, before trusting the dashboard comparison).
