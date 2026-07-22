# Project: Agentic Trading Model

## Idea
A personal trading model that buys, sells, and trades via Robinhood Agentic — paper-first risk rails, evolving to **supervised options-first** live on Agentic only, with **AWS alert-only** stop monitoring and a **paper watch UI** (local + GitHub Pages).

## Target Customer
Internal tool (personal use only)

## Time Budget
Weekend hack → multi-day paper → first live option (closed) → live-quote paper sample + process fixes (in progress)

## Monetization
Internal only (personal P&L, not a product)

## Tech Stack
Python 3.11+ · venv at `.venv` · optional `openai` for Grok research (`XAI_API_KEY` in `.env`) · optional `yfinance` for paper live quotes · Robinhood MCP for Agentic live (supervised) · AWS EC2 poller (yfinance + Discord webhook, alert-only) · GitHub Pages static watch UI (`docs/`)

## MVP Scope (the ONE thing)
**RESHAPED (user approved):** Paper-first trading loop with hard risk rails on Robinhood Agentic only.
- Explicit rule-based strategy (signals)
- Broker adapter (quotes, positions, paper/sim + optional supervised live behind flag)
- Risk gate must approve every order
- Full decision/order logging
- Default mode: paper/sim. Live only via explicit config + caps

### Post-MVP
- Day-trade playbook (SPY filter, breakout/pullback, stop + 2R)
- **R1/R2 market-red hysteresis + re-entry cooldown** (post-autopsy)
- Aggressive risk: 5% per trade / 5% daily halt
- Selector agent (max 2 new entries/tick)
- Grok research + expandable universe + **daily top-3 focus**
- Trade journal + paper autopsy tooling
- **Live:** session-refresh, propose/pick/prepare option review (no auto-place)
- **Options playbook:** long premium, **7–31 DTE**, max 1 open
- **Paper live quotes:** `--quotes live` (Yahoo) + `--session-dir logs/paper_live`
- **Watch:** local server + GitHub Pages demo
- **AWS:** option stop poller → Discord (optional Twilio); never places from cloud

## Build Mode
cohesive package (strategy + risk + broker + engine + agents + live + deploy/aws-option-poller + docs watch)

## Phase
operate / improve — first live option closed; paper + watch + free-ngrok Pages path working

## Current Status
Handoff **2026-07-16T21:02Z**. **BAC $62 call closed** (**−$11**). R1/R2 + live Yahoo paper + watch UI shipped. **This session:** user ran paper loop; **port 8787 taken by Docker WordPress** → use **8788**; ngrok must target **8788 not 80**; free ngrok interstitial broke Pages until **`ddf0441`** (`ngrok-skip-browser-warning` + CORS). Local watch primary: http://127.0.0.1:8788/. Pages: https://baowser145.github.io/agentic-trading-model/. Live paper open AAPL+MA ~$1000 equity in `logs/paper_live`. Repo @ **`ddf0441`**. Next: continue multi-day paper → re-autopsy closes; options only if user asks + BP free. See `.grok/HANDOFF.md`.

## Success Criteria
- [x] Roast verdict: Reshape accepted by user
- [x] MVP scope implemented and working
- [x] Verification passed (2/2 loops) — initial MVP
- [x] Handoff doc current
- [x] First supervised long-premium trade (BAC) entered + managed (closed −$11; postmortem written)
- [x] Cloud alert poller on AWS (Discord webhook) — prior session
- [x] Paper autopsy + R1/R2 filter guards + tests
- [x] Live-quote paper path + GitHub Pages watch UI
- [x] Free ngrok → Pages live status works (skip-browser-warning + port 8788)
- [ ] Multi-day live-quote paper sample with closed-trade autopsy
- [ ] Trade journal used for ongoing post-session review
- [ ] Live options only supervised after process lessons (no tight 10% premium stops by default)

## Accounts / Constraints
- Broker: Robinhood Agentic only (`agentic_allowed=true`, `616665162`) for any live path
- Do not trade other Robinhood accounts via this agent
- Paper: fixture for tests; **live Yahoo** via `--quotes live` for real multi-day sample
- Options: single-leg L2 only via MCP; place only after review + explicit user confirm
- AWS poller: **alert only**; secrets only in server `.env`, never git
- GitHub Pages: static UI only; never hosts the bot or secrets
- This Mac: default watch port **8787 may be WordPress (Docker)** — prefer **8788**; ngrok port must match watch
