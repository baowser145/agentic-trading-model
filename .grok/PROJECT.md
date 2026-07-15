# Project: Agentic Trading Model

## Idea
A personal trading model that buys, sells, and trades stocks via Robinhood Agentic account access — with paper-first risk rails.

## Target Customer
Internal tool (personal use only)

## Time Budget
Weekend hack

## Monetization
Internal only (personal P&L, not a product)

## Tech Stack
Python

## MVP Scope (the ONE thing)
**RESHAPED (user approved):** Paper-first trading loop with hard risk rails on Robinhood Agentic only.
- Explicit rule-based strategy (signals)
- Broker adapter (quotes, positions, paper/sim + optional supervised live behind flag)
- Risk gate must approve every order (max position, daily loss, max orders/day, flatten-and-halt)
- Full decision/order logging
- Default mode: paper/sim. Live only via explicit config + caps — never unattended default.

## Build Mode
parallel (model + broker + risk) — implemented as cohesive package

## Phase
ship → complete

## Current Status
MVP shipped. Paper-first loop works. Verification 2/2 passed. Live = intents queue for Agentic MCP, not unattended auto.

## Success Criteria
- [x] Roast verdict: Reshape accepted by user
- [x] MVP scope implemented and working
- [x] Verification passed (2/2 loops)
- [x] Handoff doc current

## Accounts / Constraints
- Broker: Robinhood Agentic account only (`agentic_allowed=true`)
- Do not trade other Robinhood accounts via this agent
