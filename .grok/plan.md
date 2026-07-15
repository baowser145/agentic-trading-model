# Plan — Agentic Trading Model (Weekend MVP)

## Goal
Run a **paper-first** stock trading loop against **Robinhood Agentic only**: strategy → risk gate → broker adapter → log. Live orders only behind an explicit config flag (supervised / tiny caps). Default is never unattended full-auto.

## Architecture

```
┌─────────────┐    signals     ┌────────────┐   OrderIntent   ┌─────────────────┐
│  Strategy   │ ─────────────► │ Risk Gate  │ ──────────────► │ Broker Adapter  │
│  (rules)    │                │ (hard caps)│                 │ paper | live*   │
└─────────────┘                └────────────┘                 └────────┬────────┘
       ▲                              │                                │
       │ market data                  │ reject / halt                  │ fills / positions
       └──────────────────────────────┴────────────────────────────────┘
                                      │
                                      ▼
                               ┌────────────┐
                               │  JSON log  │
                               │  decisions │
                               └────────────┘
* live requires TRADING_MODE=live + risk pass + Agentic account only
```

## Stack
- Python 3.11+
- CLI entrypoint (`python -m agentic_trading`)
- Config: `config.yaml` + env overrides
- Tests: pytest
- Robinhood: MCP tools used **by the human/agent session** for live; adapter exposes a clean interface so paper mode needs no live credentials

### Live execution note
This repo does not embed unofficial Robinhood scraping. **Paper mode** is fully local. **Live mode** places orders only through the approved Agentic path (orchestrator/agent calling Robinhood MCP with risk-approved intents). The adapter records intents and, in live, expects an execution backend injectable for tests.

## File structure

```
agentic-trading-model/
  .grok/                 # pipeline state
  config.yaml            # mode, symbols, risk caps, strategy params
  pyproject.toml
  README.md
  src/agentic_trading/
    __init__.py
    __main__.py          # CLI: run-once | run-loop | status | flatten
    config.py
    models.py            # Signal, OrderIntent, Position, RiskDecision
    strategy/
      base.py
      simple_momentum.py # boring MVP rule set
    risk/
      gate.py            # max position, daily loss, max orders, halt
    broker/
      base.py
      paper.py           # sim fills from last quote
      agentic.py         # live intent + position snapshot interface
    market/
      quotes.py          # quote provider interface (paper: cached/fixture; live: RH quotes)
    engine.py            # one tick: data → strategy → risk → broker → log
    log.py               # append-only JSONL decision log
  tests/
    test_risk_gate.py
    test_strategy.py
    test_paper_broker.py
    test_engine_paper.py
```

## Risk defaults (hard-coded floors; config can only tighten)

| Cap | Default |
|-----|---------|
| `trading_mode` | `paper` |
| Max position notional / symbol | 20% of equity |
| Max open positions | 3 |
| Max orders / day | 10 |
| Max daily loss | 2% of starting equity |
| Max order notional | $100 (tiny for ~$1k book) |
| Halt on breach | flatten paper book + refuse new orders |

## Strategy (MVP — boring on purpose)
**Simple momentum (daily):**
- Universe: small list from config (e.g. `SPY`, `QQQ`, `IWM` — liquid ETFs)
- Signal: if last close > SMA(N) → want long; else → flat
- Position size: risk gate decides size from remaining capacity
- No options, no leverage, no day-trade churn loop

## Workstreams (parallel)

### A — Risk + models + config
- `models.py`, `config.py`, `risk/gate.py`, tests for every reject path

### B — Strategy + paper market/broker
- `simple_momentum.py`, `broker/paper.py`, `market/quotes.py` (fixture + file replay)
- Paper fills at last mid/last trade; track cash/positions

### C — Engine + CLI + logging + agentic stub
- `engine.py`, `__main__.py`, `log.py`, `broker/agentic.py` (interface + dry-run live intents)
- README: how to run paper loop; how live would be wired via MCP with caps

### Integration
- Wire config → engine → paper end-to-end
- `pytest` green
- Smoke: `python -m agentic_trading run-once` writes a decision log

## Implementation order (if sequential)
1. Scaffold package + config + models  
2. Risk gate + tests  
3. Paper broker + strategy + tests  
4. Engine + CLI + logging  
5. Agentic adapter stub + docs  
6. Integration smoke + verification  

## Out of scope (weekend)
- Unattended full-auto live as default  
- ML / LLM signal generation as primary edge  
- Options, crypto, multi-account  
- Web dashboard  
- Backtest UI (optional later: replay JSON bars)

## Success criteria for “MVP done”
1. `pytest` passes  
2. Paper `run-once` and short `run-loop` produce JSONL with signals, risk decisions, fills  
3. Risk gate blocks oversized / over-limit / halted orders (proven by tests)  
4. Live path cannot run unless `trading_mode: live` and is documented as supervised / agent-mediated  
5. README explains kill switch: set mode paper / stop process / risk halt file  

## How to run (target)
```bash
cd ~/projects/agentic-trading-model
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m agentic_trading run-once
python -m agentic_trading run-loop --interval 60
```
