# Project: Agentic Trading Model

## Idea
A personal trading model that buys, sells, and trades via Robinhood Agentic — paper-first risk rails, evolving to **supervised options-first** live on Agentic only, with **AWS alert-only** stop monitoring.

## Target Customer
Internal tool (personal use only)

## Time Budget
Weekend hack → multi-day paper through Friday 2026-07-17 → live options (in progress)

## Monetization
Internal only (personal P&L, not a product)

## Tech Stack
Python 3.11+ · venv at `.venv` · optional `openai` for Grok research (`XAI_API_KEY` in `.env`) · Robinhood MCP for Agentic live (supervised) · AWS EC2 poller (yfinance + Discord webhook, alert-only)

## MVP Scope (the ONE thing)
**RESHAPED (user approved):** Paper-first trading loop with hard risk rails on Robinhood Agentic only.
- Explicit rule-based strategy (signals)
- Broker adapter (quotes, positions, paper/sim + optional supervised live behind flag)
- Risk gate must approve every order
- Full decision/order logging
- Default mode: paper/sim. Live only via explicit config + caps

### Post-MVP
- Day-trade playbook (SPY filter, breakout/pullback, stop + 2R)
- Aggressive risk: 5% per trade / 5% daily halt
- Selector agent (max 2 new entries/tick)
- Grok research + expandable universe + **daily top-3 focus**
- Trade journal (`logs/trades.jsonl`, `fills.jsonl`, `trades.csv`)
- **Live:** `status --live`, session-refresh, propose/pick/prepare option review (no auto-place)
- **Options playbook:** long premium, **7–31 DTE**, max 1 open, daily one idea **or** hold to exits
- **AWS:** option stop poller → Discord (optional Twilio); never places from cloud

## Build Mode
cohesive package (strategy + risk + broker + engine + agents + live + deploy/aws-option-poller)

## Phase
operate / live — first Agentic long call open; cloud alerts online

## Current Status
Handoff **2026-07-16T16:41Z**. **Open:** BAC $62 call exp 2026-07-31 @ **$0.89** fill; RH GTC stop-market @ **$0.80**; AWS `agentic-option-stop.timer` active (Discord webhook, alert-only). Paper journal 137 closed (~−$12 fixture); paper loop stopped. Next: manage BAC to exits; optional Twilio/commit deploy; no second option until closed. See `.grok/HANDOFF.md`.

## Success Criteria
- [x] Roast verdict: Reshape accepted by user
- [x] MVP scope implemented and working
- [x] Verification passed (2/2 loops) — initial MVP
- [x] Handoff doc current
- [x] Agentic BP free enough for first supervised long-premium trade (entered BAC call)
- [x] Cloud alert poller on AWS (Discord webhook)
- [ ] Paper session complete through Friday (loop stopped early; journal exists for review)
- [ ] Trade journal used for post-session review / backtest
- [ ] Live only if explicitly enabled after paper review (supervised options path in use; still no unattended thrash)

## Accounts / Constraints
- Broker: Robinhood Agentic only (`agentic_allowed=true`, `616665162`) for any live path
- Do not trade other Robinhood accounts via this agent
- Paper uses synthetic fixture quotes until a real quote feed is wired
- Options: single-leg L2 only via MCP; place only after review + explicit user confirm (except pre-authorized broker stops)
- AWS poller: **alert only**; secrets only in server `.env`, never git
