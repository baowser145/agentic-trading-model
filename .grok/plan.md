# Plan — Stock Screener 30d (CLI MVP)

## Architecture

```
stock-screener-30d/
├── config/criteria.yaml      # Screening rules (RSI, SMA, pullback)
├── src/stock_screener_30d/
│   ├── cli.py                # Click/Typer CLI: scan, backtest
│   ├── data.py               # yfinance fetch + indicator calc
│   ├── screener.py           # Apply criteria, rank top N
│   └── backtest.py           # 30-day forward returns vs SPY
├── tests/
├── pyproject.toml
└── README.md
```

## Screening Criteria (30-day swing hold)

| Rule | Default |
|------|---------|
| RSI(14) | 40–60 |
| Price vs 50-day SMA | Above SMA |
| Pullback from 52-week high | 5–15% |
| Min avg daily volume | 500k shares |

## Implementation Steps

1. Scaffold Python package + dependencies (yfinance, pandas, typer)
2. `data.py` — fetch tickers, compute indicators
3. `screener.py` — filter + rank by composite score
4. `cli.py scan` — output top N to terminal + optional CSV
5. `cli.py backtest` — walk-forward 30-day holds, compare vs SPY after 0.1% round-trip cost
6. Tests + README

## Deferred

- FastAPI web UI (after 3 months paper-trading beats SPY)
- Real-time data feeds
- ML ranking