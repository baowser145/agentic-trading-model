# Improvement Roadmap

Prioritized features for continuous improvement. Revisit after each paper-trading month.

## Next (high value, low effort)

- [x] **Paper-trade log** — `stock-screener log` append daily picks to `data/paper-trades.csv`, track actual vs predicted
- [x] **Watchlist diff** — NEW / DROPPED shown on each `log` run
- [ ] **Full S&P 500 universe** — fetch ticker list from Wikipedia/API instead of hardcoded 49
- [ ] **NYSE calendar** — exact trading days for exit dates (not BDay approximation)

## Validation (prove the strategy)

- [x] **Paper vs backtest panel** — live picks compared to simulated history on dashboard
- [ ] **Walk-forward report** — monthly win rate, max drawdown, Sharpe ratio
- [ ] **Out-of-sample test** — train criteria on 2019-2022, test on 2023-2024
- [ ] **Monte Carlo** — shuffle entry dates to detect luck vs edge
- [ ] **Survivorship-free backtest** — historical S&P constituents per year

## Signal quality

- [ ] **Earnings filter** — exclude stocks with earnings in next 5 days
- [ ] **Sector cap** — max 2 picks per sector (diversification)
- [ ] **ATR-based stop** — optional stop at 2x ATR below entry instead of SMA only
- [ ] **Take-profit target** — optional exit at 52-week high or fixed R:R

## Ops & reliability

- [ ] **Cached data** — SQLite cache for yfinance pulls (faster rescans)
- [ ] **Data quality checks** — flag stale/missing tickers before scan
- [ ] **Cron-friendly** — `scan --quiet --output picks-$(date +%F).csv` for daily automation
- [ ] **Alerts** — email/Slack when new picks appear

## UI (only after paper trading beats SPY for 3 months)

- [ ] FastAPI dashboard — watchlist, charts, trade history
- [ ] Plot entry/stop/exit on price chart per ticker