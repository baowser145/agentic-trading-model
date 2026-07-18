# Backtest Report (Milestone 0 go/no-go gate)

Universe: 500 tickers. Slippage modeled: 5.0 bps per side. Test window: last 2 years.

## Gap Scan
**Verdict: FAIL** — avg strategy return -1.63% vs avg buy-hold 60.66%, avg per-trade expectancy -0.40%

375 of 500 tickers triggered at least one trade (125 had none).

### Top 10 by total return (Gap Scan)
| Ticker | Trades | Win Rate | Avg Return/Trade | Total Return | Max DD | Buy&Hold | Beats B&H |
|---|---|---|---|---|---|---|---|
| AXON | 8 | 75.0% | 4.17% | 37.74% | -2.45% | 105.75% | No |
| COIN | 15 | 53.3% | 2.16% | 32.37% | -18.95% | -23.44% | Yes |
| TER | 11 | 72.7% | 1.95% | 23.15% | -0.93% | 147.95% | No |
| DVA | 2 | 100.0% | 9.79% | 20.33% | 0.00% | 71.58% | No |
| FSLR | 7 | 71.4% | 2.39% | 16.91% | -7.59% | 2.62% | Yes |
| ON | 7 | 85.7% | 2.10% | 15.60% | -0.80% | 26.24% | No |
| MMM | 2 | 100.0% | 7.46% | 15.20% | 0.00% | 63.68% | No |
| AES | 4 | 100.0% | 3.43% | 14.41% | 0.00% | -9.43% | Yes |
| DG | 4 | 75.0% | 3.45% | 14.26% | -1.09% | -6.45% | Yes |
| GNRC | 6 | 50.0% | 2.36% | 13.47% | -1.97% | 81.31% | No |

### Bottom 10 by total return (Gap Scan)
| Ticker | Trades | Win Rate | Avg Return/Trade | Total Return | Max DD | Buy&Hold | Beats B&H |
|---|---|---|---|---|---|---|---|
| ZBRA | 8 | 12.5% | -2.30% | -17.21% | -14.34% | -14.05% | No |
| ALGN | 6 | 0.0% | -3.11% | -17.42% | -13.90% | -24.32% | Yes |
| CNC | 5 | 20.0% | -4.08% | -19.05% | -11.67% | -0.21% | No |
| HOOD | 18 | 33.3% | -1.07% | -20.04% | -29.01% | 432.14% | No |
| UAL | 11 | 27.3% | -2.03% | -20.81% | -23.29% | 183.00% | No |
| APH | 6 | 0.0% | -4.08% | -22.22% | -20.14% | 149.29% | No |
| HPE | 8 | 25.0% | -3.18% | -23.26% | -20.87% | 117.00% | No |
| WDC | 20 | 40.0% | -1.29% | -24.45% | -26.50% | 877.68% | No |
| APP | 16 | 18.8% | -2.76% | -37.06% | -38.83% | 538.40% | No |
| VRT | 15 | 26.7% | -3.01% | -37.82% | -37.79% | 245.77% | No |

## Trend Join Long
**Verdict: FAIL** — avg strategy return 13.09% vs avg buy-hold 50.88%, avg per-trade expectancy 0.14%

500 of 500 tickers triggered at least one trade (0 had none).

### Top 10 by total return (Trend Join Long)
| Ticker | Trades | Win Rate | Avg Return/Trade | Total Return | Max DD | Buy&Hold | Beats B&H |
|---|---|---|---|---|---|---|---|
| STX | 50 | 70.0% | 4.03% | 515.21% | -20.15% | 770.05% | No |
| MU | 43 | 60.5% | 4.55% | 424.99% | -35.79% | 658.12% | No |
| LITE | 57 | 57.9% | 3.22% | 350.91% | -30.94% | 1201.39% | No |
| WDC | 50 | 70.0% | 3.39% | 348.05% | -26.06% | 877.68% | No |
| SNDK | 18 | 66.7% | 9.39% | 333.50% | -12.83% | 730.01% | No |
| PLTR | 46 | 67.4% | 3.17% | 244.28% | -21.63% | 378.48% | No |
| TER | 43 | 60.5% | 3.22% | 237.27% | -18.05% | 147.95% | Yes |
| CIEN | 59 | 72.9% | 2.38% | 230.86% | -35.72% | 801.93% | No |
| FLEX | 56 | 60.7% | 2.35% | 204.67% | -18.46% | 367.78% | No |
| FIX | 58 | 65.5% | 2.17% | 200.77% | -17.42% | 497.17% | No |

### Bottom 10 by total return (Trend Join Long)
| Ticker | Trades | Win Rate | Avg Return/Trade | Total Return | Max DD | Buy&Hold | Beats B&H |
|---|---|---|---|---|---|---|---|
| META | 42 | 47.6% | -1.11% | -41.13% | -50.88% | 14.17% | No |
| QCOM | 35 | 31.4% | -1.27% | -41.54% | -47.72% | -6.40% | No |
| COIN | 29 | 44.8% | -1.08% | -41.55% | -56.32% | -23.44% | No |
| TTD | 18 | 27.8% | -2.48% | -41.69% | -47.07% | -80.51% | Yes |
| CHTR | 28 | 42.9% | -1.66% | -42.04% | -47.04% | -53.39% | Yes |
| DECK | 27 | 37.0% | -1.89% | -42.77% | -40.40% | -33.45% | No |
| NVDA | 48 | 47.9% | -1.06% | -46.98% | -44.61% | 52.79% | No |
| FSLR | 30 | 40.0% | -1.85% | -48.86% | -44.27% | 2.62% | No |
| NXPI | 32 | 34.4% | -2.14% | -52.97% | -55.12% | 4.91% | No |
| SNPS | 27 | 29.6% | -3.97% | -69.48% | -66.20% | -28.14% | No |

## Overall Go/No-Go
**Neither strategy beat buy-and-hold net of modeled costs. Do not build the live pipeline on these rules.** Iterate the strategy parameters (thresholds, tickers, holding period) and re-run before proceeding to Phase A.
