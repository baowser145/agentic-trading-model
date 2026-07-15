# Agentic Trading Model

Personal **paper-first** stock trading loop with **hard risk rails**. Optional live path is supervised and **Robinhood Agentic account only** — never the default.

## What it does

1. **Strategy (day_trade_playbook)** — SPY market filter + breakout / pullback entries  
2. **Selector agent** — ranks multi-name setups (relative strength, R, liquidity); picks best N  
3. **Risk** — size so stop ≈ **5% equity** risk; **2R** take-profit; **5% daily** kill switch  
4. **Broker** — paper fills by default; live writes intents for agent/MCP execution  
5. **Logging** — JSONL of signals, selector picks, fills, stop/target plans  

### Universe + agent

- **ETFs:** SPY (filter + trade), QQQ, IWM  
- **Mega-caps:** AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA  
- **Selector (in the loop):** ranks setups each tick; `max_new_entries_per_tick: 2`  
- **Research (before live):** optional Grok LLM pass — does **not** place orders  

### More aggressive selector

In `config.yaml`:

```yaml
selector:
  max_new_entries_per_tick: 2   # was 1
```

Restart the paper loop after changing config.

### LLM research pass (SpaceXAI / Grok) before live

```bash
cd ~/projects/agentic-trading-model
source .venv/bin/activate
pip install -e ".[llm]"          # once — installs openai client
export XAI_API_KEY=xai-...       # from https://console.x.ai

# Advisory only — writes logs/research_latest.md + .json
python -m agentic_trading research --llm

# Optional: rewrite config symbols from recommendations (review MD first!)
python -m agentic_trading research --llm --apply
```

Without an API key, `research` still runs a **heuristic** RS ranking (no LLM).

**Live path (supervised):**  
1. Paper until Friday  
2. `research --llm` → read `logs/research_latest.md`  
3. Only then consider `trading_mode: live` + `allow_live: true` + Agentic MCP with caps  
4. Risk rails (5% stop risk, 5% daily halt, 2R) always stay in code — LLM never bypasses them

### Risk profile (config.yaml)

| Rule | Value |
|------|--------|
| Risk per trade (if stop hits) | **5%** of equity |
| Daily loss halt | **5%** |
| Take profit | **2R** (2× stop distance) |
| Market filter | SPY green (above SMA or range high) |

On a $1,000 book: ~**$50** risk per trade if stop hits; ~**$50** max daily drawdown before halt.  
This is **much riskier** than the classic 1% rule — intentional per your choice.

## Quick start

**Use the project venv** (not system/Anaconda `python`).

```bash
cd ~/projects/agentic-trading-model
python3 -m venv .venv          # once
source .venv/bin/activate      # every new shell — should show (.venv)
pip install -e ".[dev]"        # once (or after pull)
pytest
python -m agentic_trading status
python -m agentic_trading run-once
python -m agentic_trading run-loop --interval 5 --max-ticks 3
```

Without activating the venv:

```bash
.venv/bin/python -m agentic_trading status
```

`No module named agentic_trading` means another Python is on your PATH (e.g. `/opt/anaconda3/bin/python`). Activate `.venv` or call `.venv/bin/python` explicitly.

## Config

See `config.yaml`:

| Key | Default | Notes |
|-----|---------|--------|
| `trading_mode` | `paper` | `live` only if `broker.allow_live: true` |
| `risk.max_order_notional` | `100` | Hard floor max 500 in code |
| `risk.max_daily_loss_pct` | `0.02` | Halts new orders when breached |
| `broker.allow_live` | `false` | Must be true for live path |

## Settlement vs trading

- **Settlement** still takes ~1 business day (`broker.settlement_days`) — tracked as settled vs unsettled for awareness.
- **Trading:** by default (`trade_when_cash_available: true`) you **trade immediately** when cash is in the account after a sale. No forced 1-day wait to redeploy available funds.
- Strict mode: set `trade_when_cash_available: false` to require settled cash only for buys.

Paper state persists in `logs/paper_state.json` so multi-day sessions keep positions and pending settlements.

## Multi-day paper session

```bash
# Run until Friday 2026-07-17 end of day (America/New_York)
python -m agentic_trading run-loop --until 2026-07-17 --interval 300
```

## Kill switch

1. Set `trading_mode: paper` and `broker.allow_live: false`  
2. Stop the process (`Ctrl-C`)  
3. On daily-loss halt, **buys** freeze; **sells** still allowed  

## Live / Robinhood Agentic

This package does **not** scrape unofficial Robinhood APIs. Live mode:

1. Sets mode + `allow_live`  
2. Writes approved intents to `logs/live_intents.jsonl`  
3. Shadows fills in paper ledger for local bookkeeping (intent + shadow)  
4. A human/agent session must execute real orders via **Robinhood MCP on the Agentic account only**

Do not enable unattended full-auto until paper logs look sane for days, not minutes.

## Layout

```
src/agentic_trading/
  strategy/     # signals
  risk/         # hard gates
  broker/       # paper + agentic stub
  market/       # quote providers (fixture for paper)
  engine.py     # one tick
  __main__.py   # CLI
```

## Disclaimer

Not financial advice. You can lose money. Weekend bots do not have edge by default. Paper first.
