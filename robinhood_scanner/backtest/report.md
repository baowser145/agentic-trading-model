# Backtest Report (Milestone 0 go/no-go gate)

Slippage modeled: 5.0 bps per side. Test window: last 2 years.

## Gap Scan
**Verdict: FAIL** — avg strategy return -9.71% vs avg buy-hold 60.30%, avg per-trade expectancy -1.89%

| Ticker | Trades | Win Rate | Avg Return/Trade | Total Return | Max DD | Buy&Hold | Beats B&H |
|---|---|---|---|---|---|---|---|
| AAPL | 2 | 0.0% | -2.25% | -4.50% | -0.19% | 38.43% | No |
| NVDA | 5 | 20.0% | -3.16% | -15.21% | -18.12% | 52.79% | No |
| AMD | 11 | 45.5% | -1.05% | -11.69% | -15.70% | 208.94% | No |
| TSLA | 10 | 40.0% | -1.02% | -10.41% | -17.60% | 65.96% | No |
| MSFT | 2 | 0.0% | -2.72% | -5.38% | -4.01% | -15.75% | Yes |
| META | 10 | 40.0% | -1.54% | -14.66% | -12.09% | 14.17% | No |
| GOOGL | 4 | 50.0% | -0.62% | -2.62% | -3.57% | 95.36% | No |
| AMZN | 5 | 0.0% | -2.76% | -13.22% | -12.66% | 22.51% | No |

## Trend Join Long
**Verdict: FAIL** — avg strategy return 5.32% vs avg buy-hold 60.30%, avg per-trade expectancy 0.11%

| Ticker | Trades | Win Rate | Avg Return/Trade | Total Return | Max DD | Buy&Hold | Beats B&H |
|---|---|---|---|---|---|---|---|
| AAPL | 47 | 55.3% | 0.19% | 5.38% | -16.26% | 38.43% | No |
| NVDA | 48 | 47.9% | -1.06% | -46.98% | -44.61% | 52.79% | No |
| AMD | 36 | 47.2% | 3.29% | 155.75% | -20.55% | 208.94% | No |
| TSLA | 45 | 46.7% | 0.19% | -3.01% | -36.98% | 65.96% | No |
| MSFT | 36 | 52.8% | -0.65% | -22.63% | -26.16% | -15.75% | No |
| META | 42 | 47.6% | -1.11% | -41.13% | -50.88% | 14.17% | No |
| GOOGL | 51 | 54.9% | 0.25% | 7.75% | -16.62% | 95.36% | No |
| AMZN | 49 | 49.0% | -0.18% | -12.56% | -27.69% | 22.51% | No |

## Overall Go/No-Go
**Neither strategy beat buy-and-hold net of modeled costs. Do not build the live pipeline on these rules.** Iterate the strategy parameters (thresholds, tickers, holding period) and re-run before proceeding to Phase A.
