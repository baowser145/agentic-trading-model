# ROAST Verdict — Robinhood AI Scanner + Alert System

Timestamp: 2026-07-06

## Verdict: Reshape

**Strongest Reason This Could Fail:**
No trading edge has been validated — there is no backtest, no paper-trading track record, nothing
showing the premarket-gap and trend-join-long rules actually make money net of slippage and fees.
Layered on top of that unvalidated strategy is an LLM doing exact financial arithmetic (entry, stop,
size) inside a live-money control loop, with an explicit roadmap to remove human approval entirely.
Academic evidence (arXiv "TradeTrap," "The Losing Winner: An LLM Agent that Predicts the Market but
Loses Money") shows LLM trading agents that are directionally *right* can still lose money because
prediction quality doesn't translate into good execution/risk management. This project is racing
toward autonomous execution before establishing the one thing that determines whether any of it is
worth automating: does the strategy have an edge.

**Strongest Upside / Opportunity:**
Robinhood's Agentic Trading is real, sanctioned, and only ~6 weeks old (launched 2026-05-27) — it
runs in a dedicated, separately-funded sub-account with a user-set spending cap and previews orders
for approval by default. That's genuine, legitimate infrastructure, not a hack. The scan + Telegram
alert layer (Phase A) is a legitimate, low-risk, buildable-in-a-month project on its own, and it
doubles as a forcing function to learn how to actually backtest and validate a strategy — a durable
skill independent of whether this particular bot ever makes money.

**Riskiest Assumption:**
That the gap-scanner and trend-join-long rules produce a positive-expectancy edge net of realistic
slippage, spreads, and fees. Nothing so far establishes this, and the base-rate evidence points the
other way: ~70-90% of retail day traders lose money, only ~13% are still profitable after 6 months,
collapsing to ~1% at 5 years. No publicly documented LLM-driven autonomous trading agent has a
verified sustained-profit track record.

**Cheapest 48-Hour Validation Test:**
Before writing any scheduling, alerting, or MCP-integration code: backtest the exact gap-scanner and
trend-join-long rules against 2+ years of historical data (free via yfinance/pandas), computing win
rate, expectancy, and max drawdown net of realistic slippage and commissions. Compare against a dumb
buy-and-hold baseline on the same tickers. If it doesn't beat the baseline, the strategy logic is the
problem — no amount of pipeline/alerting engineering fixes that.

**What to Build Only If the Test Passes:**
Phase A only, and only after the backtest clears the bar above: scanner + Telegram alert with a
suggested trade, manual approval required, running against the sanctioned Robinhood Agentic
sub-account with a hard dollar cap set at the platform level (not just in code). Track every alert's
real/paper outcome for a minimum of 4 weeks before Phase B is even discussed again. Phase B
(autonomous execution) should require, at minimum: a daily-loss circuit breaker enforced outside the
LLM's own judgment (i.e., code that hard-stops trading, not the agent "deciding" to stop), a
statistically significant validated track record from the Phase A alerts, and should be treated as a
separate future decision — not something built into this 1-month MVP.

---

### Reshaped scope (if user proceeds)

- **Keep:** premarket gap scanner + trend-join-long scanner, using Robinhood MCP tools for market
  data, Telegram alerts with a suggested trade (entry/stop/size), manual approval only.
- **Add, ahead of everything else:** a Python/pandas backtest of both strategies over 2+ years of
  historical data (yfinance), with slippage/commission modeled, as the actual first build milestone
  and a go/no-go gate before building the live scanner+alert pipeline.
- **Explicitly defer, not build:** Phase B autonomous execution. Do not wire any order-placement MCP
  tool into the scheduled pipeline in this build. If the user wants to revisit Phase B later, it
  should be scoped as its own project with its own roast, after Phase A has a real track record.
