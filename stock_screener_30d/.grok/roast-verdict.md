# ROAST Verdict — Stock Screener 30d
**Timestamp:** 2026-07-02

**Verdict:** Reshape

**Strongest Reason This Could Fail:**
You're building a FastAPI + web frontend around an unproven 30-day signal in a market where Finviz (20M users), TradingView (100M+), and free OpenBB already deliver the same daily scan. The screening logic is the easy 20% — validation after transaction costs is the hard 80%, and your MVP plan skips it entirely. A parallel backend/frontend build before a walk-forward backtest that beats SPY after fees is months of infrastructure for a hypothesis that will likely fail.

**Strongest Upside / Opportunity:**
As a personal tool, a custom screener with *your own* explicit criteria (momentum + pullback + earnings drift, etc.) that no Finviz preset matches could save 15 minutes/day and enforce discipline on entries/exits. The FastAPI stack is well-trodden (published tutorials exist), so a reshaped CLI-first MVP is buildable in a weekend. If you prove edge on held-out data, the web UI becomes a genuine workflow upgrade, not a dashboard you'll stop opening after the first losing month.

**Riskiest Assumption:**
That "30-day hold" is a meaningful, testable edge — not an arbitrary calendar length — and that daily top-N picks from free/delayed data (yfinance) are stable enough to trade profitably after spreads, slippage, and commissions. If rankings swing wildly day-to-day, you're overfitting noise.

**Cheapest 48-Hour Validation Test:**
1. Write a 50-line Python script (no web UI) that screens S&P 500 on 3 explicit rules (e.g., RSI 40–60, above 50-day SMA, 5–15% off 52-week high).
2. Backtest 30-day forward returns for picks over 2019–2024 with realistic round-trip costs (0.1% per trade).
3. Compare vs buy-and-hold SPY on the same periods. If your picks don't beat SPY by ≥2% annualized after costs, kill the strategy — don't build the app.

**What to Build Only If the Test Passes:**
Reshape to **CLI-first MVP**: single Python package with `scan` command (daily top N to terminal/CSV), configurable criteria in YAML, and a `backtest` command. Skip the web UI until 3 months of paper-trading your scans beats SPY. Then add FastAPI + minimal frontend as a watchlist viewer, not a screener replacement.