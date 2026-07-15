# Roast Verdict — Agentic Trading Model

**Timestamp:** 2026-07-14T22:45:00Z

**Idea under test:** Personal Python trading model that full-auto live-trades stocks (unattended) via Robinhood Agentic. Weekend hack. Internal only. ~$1k Agentic account.

## Council summaries (internal)

### Contrarian
- Unit economics dead at ~$1k; weekend labor dominates any plausible edge
- Full auto unattended is the hardest MVP; reliability/risk is the product, not the signal
- Weekend cannot produce walk-forward edge; parallel stubs, not a real system
- PDT / microstructure / small book make equities auto painful
- Agentic MCP is not a pro execution stack; total-loss risk labeled by broker
- Unattended ops (auth, retries, hung orders) dominate cost
- Smart people fail by confusing coding skill with market edge
- Walk away if no costed edge, no hard circuit breakers, success = "bot traded this weekend"

### Buyer (founder capital at risk)
- **Would not buy** full auto unattended live this weekend
- Max unattended capital: **$0**; supervised pilot at most ~$50–$200 after paper
- Alternatives: manual, indexes, signal-only scripts, paper brokers
- Deal-breakers: live before paper, no kill switch / loss caps / logging
- Problem is curiosity/nice-to-have, not hair-on-fire

### Deep Researcher
- Crowded commodity: Alpaca, QuantConnect, IB API, TradingView bridges, DIY bots
- RH official API is crypto-focused for programmatic trading; equities automation via unofficial paths has enforcement risk; Agentic is MCP/agent-mediated
- Failure rates for retail auto trading are high (majority fail in months); overfitting dominates
- Infrastructure is free/$0–$100/mo elsewhere; scarce asset is edge, not glue code
- Agentic helps "agent can place trades" but hurts "set and forget production algo"

---

## Judge verdict (structured)

**Verdict:** Reshape

**Strongest Reason This Could Fail:**
Shipping unattended full-auto live trades as a weekend MVP before paper proof, hard risk limits, and order reconciliation. At ~$1k, one runaway loop or bad model flip erases the book; the buyer (you) correctly prices true unattended trust at $0.

**Strongest Upside / Opportunity:**
You already have a dedicated Robinhood Agentic account and agent tooling. A **personal execution + risk + simple rules engine** that starts paper/supervised and only later goes tiny-live is a legitimate learning system and a sane way to use Agentic without pretending a weekend bot has market edge.

**Riskiest Assumption:**
That a weekend-built model has positive expectancy after costs and can safely hold unattended authority over real orders. Almost every retail auto system fails on overfitting, ops, and risk—not on "can I place an order."

**Cheapest 48-Hour Validation Test:**
1. Define one explicit rule set (e.g. simple momentum or mean-reversion on a tiny universe) with hard caps: max position size, max daily loss, max orders/day, flatten-and-halt.
2. Run the **exact same code path** in paper/sim for the weekend: log every signal and would-be order; zero live orders.
3. Success = system runs without logic bugs, risk blocks fire correctly, and you still want live after reviewing the log. Fail = bugs, thrashing signals, or "I wouldn't trust this overnight."

**What to Build Only If the Test Passes:**
Python MVP with three modules: (1) strategy/signals, (2) Robinhood Agentic broker adapter (quotes, positions, orders), (3) risk gate that must approve every order. Default mode = **paper or supervised**. Live unattended only behind explicit config flag + caps, never as the default weekend ship target. Do not build "full auto live" as the first milestone.

---

## Reshaped MVP (recommended for pipeline)

| Was | Becomes |
|-----|---------|
| Full auto live unattended | Paper-first loop + hard risk rails; optional **supervised** live on Agentic only after paper path works |
| Success = bot trades live this weekend | Success = deterministic strategy → risk gate → simulated or approved order; kill switch + logging |
| Edge assumed | One boring, explicit rule set; no claim of alpha until paper evidence |

** monorepo workstreams still valid:** model + broker + risk (parallel)
