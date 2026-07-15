# Project: Agentic Trading Model

## Idea
A personal trading model that buys, sells, and trades stocks via Robinhood Agentic account access — with paper-first risk rails.

## Target Customer
Internal tool (personal use only)

## Time Budget
Weekend hack → multi-day paper session through Friday 2026-07-17

## Monetization
Internal only (personal P&L, not a product)

## Tech Stack
Python 3.11+ · venv at `.venv` · optional `openai` for Grok research (`XAI_API_KEY` in `.env`)

## MVP Scope (the ONE thing)
**RESHAPED (user approved):** Paper-first trading loop with hard risk rails on Robinhood Agentic only.
- Explicit rule-based strategy (signals)
- Broker adapter (quotes, positions, paper/sim + optional supervised live behind flag)
- Risk gate must approve every order
- Full decision/order logging
- Default mode: paper/sim. Live only via explicit config + caps

### Post-MVP (built this session)
- Day-trade playbook (SPY filter, breakout/pullback, stop + 2R)
- Aggressive risk: 5% per trade / 5% daily halt
- Selector agent (max 2 new entries/tick)
- Grok research + expandable universe + **daily top-3 focus**
- Trade journal (`logs/trades.jsonl`, `fills.jsonl`, `trades.csv`) for early backtest

## Build Mode
cohesive package (strategy + risk + broker + engine + agents)

## Phase
operate → paper through Friday

## Current Status
Handoff 2026-07-15T03:38:43Z. MVP shipped + enhanced. Paper loop running (see `logs/session.pid`). Last LLM daily picks: **AAPL, MSFT, MA**. Next: commit dirty files, restart loop for journal, daily research until Friday, then backtest from journal. See `.grok/HANDOFF.md`.

## Success Criteria
- [x] Roast verdict: Reshape accepted by user
- [x] MVP scope implemented and working
- [x] Verification passed (2/2 loops) — initial MVP
- [x] Handoff doc current
- [ ] Paper session complete through Friday
- [ ] Trade journal used for post-session review / backtest
- [ ] Live only if explicitly enabled after paper review

## Accounts / Constraints
- Broker: Robinhood Agentic account only (`agentic_allowed=true`) for any live path
- Do not trade other Robinhood accounts via this agent
- Paper uses synthetic fixture quotes until a real quote feed is wired
