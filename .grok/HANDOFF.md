# Session Handoff — Agentic Trading Model — 2026-07-16T16:41:32Z

## One-Line Status
**Live long call open** (BAC $62C 7/31, filled $0.89); RH **GTC stop-market @ $0.80** armed; **AWS poller** alerts Discord webhook on −10% (no auto-trade); paper loop still stopped; BP was free enough to enter.

## Project Path
/Users/vubl/projects/agentic-trading-model

## Phase
**operate / live** — first supervised Agentic long-premium trade is live; cloud alert infra deployed

## Roast Verdict
**Reshape** (user accepted) — see `.grok/roast-verdict.md`. Paper-first + risk rails; supervised live, not unattended thrash.

## Decisions Made
- **Internal tool only**; Robinhood **Agentic only** (`agentic_allowed=true`, account `616665162`, nickname Agentic)
- Paper default: `trading_mode: paper`, `allow_live: false` (live actions via MCP + explicit user confirm)
- Options-first: long premium only, **7–31 DTE**, max **1** open; exits +50–100% / −50% / ≤3 DTE
- User authorized first buy (~$90 BP): agent selected **BAC Jul 31 $62 call** (best CoP under budget; AAPL/MSFT ATM unaffordable)
- **−10% stop**: primary = RH **stop-market GTC @ $0.80** sell-to-close; Grok 2m LLM watch **cancelled** (token cost + laptop-off useless)
- Cloud: **AWS poller alert-only** (yfinance quotes, never places); Discord via **dedicated webhook** (not Sharp bot)
- SMS (Twilio) optional / not configured yet
- Angle brackets in CLI docs (`<payload.json>`) are placeholders — never type literally (zsh redirect)

## What's Built
- Full package `src/agentic_trading/` — paper engine, risk, journal, live CLI — **done**
- Live: session-refresh, write-live-snapshot, propose/pick/prepare/record option review — **done**
- **Open live position:** BAC $62 call exp 2026-07-31, qty 1, option_id `784784b8-5a03-4eee-8e27-bdb6e6a8fe19`
  - Buy order filled: `6a59066e-b69d-4236-86a7-92ca9ef5b9b6` @ **$0.89** ($89 debit)
  - Broker stop: `6a5906d1-19fe-4787-bcb7-e0b8db5a28ec` stop_market GTC @ **$0.80** (state was **confirmed**)
- **AWS poller** (`deploy/aws-option-poller/` → `/home/ubuntu/agentic-option-poller` via `ssh aws`)
  - systemd timer `agentic-option-stop.timer` every **2 min**, **active**
  - Alert-only; Discord webhook in server `.env` (chmod 600; **do not commit secrets**)
  - Latest poll (~16:41Z): mark **$0.89**, pct **0%**, not triggered
- Paper journal: 137 closed (~−$12 fixture) — loop **not** running
- Backtester from journal — **not built**
- Real paper quote feed — **not built**

## Verification Status
- Last formal `/verification`: **PASS** (2/2) initial MVP — `.grok/build-log.md`
- Live CLI tests previously green; this session: live RH place + AWS timer verified by hand (Discord webhook 204)
- Git HEAD: `d4f9660` — uncommitted: `.grok/*`, `config.yaml`, untracked `deploy/`, `.grok/overnight-prompt.txt`

## Active Goals
- none formal `/goal`
- Operational: **manage open BAC call** to exits; AWS Discord alert on −10%; optional Twilio SMS later

## Open Blockers
- **none critical for open trade** — position + broker stop + cloud alert online
- AWS quotes = **yfinance** (can lag RH); primary exit is still RH stop
- Twilio SMS **not** configured (Discord webhook only)
- Webhook URL appeared in chat history — consider rotating in Discord if concerned
- Equities AA/VTRS/PEGA/WBD may still have shares held for sells (check on refresh)
- Paper through Friday incomplete; no journal backtest yet
- Do not commit `.env` / webhook secrets

## Next 3 Actions (in order)
1. **Manage open BAC $62C:** if user asks status → MCP quotes/positions + stop order state; take-profit guidance +50–100% ($1.34–$1.78) / broker stop −10% / ≤3 DTE. On Discord −10% alert or user “sell BAC stop” → review + place sell-to-close only with clear confirm (or if they already pre-authorized a specific path, follow that).
2. **Optional:** add Twilio to AWS `.env` for SMS; or commit `deploy/aws-option-poller/` (without secrets) to git.
3. **Later:** journal backtest from `logs/trades.jsonl`; keep live supervised; max 1 option — no new long until BAC closed.

## Resume Prompt
Copy-paste this into a fresh session:

> Read `.grok/HANDOFF.md`, `.grok/PROJECT.md`, and `AGENTS.md` in /Users/vubl/projects/agentic-trading-model. Continue from "Next 3 Actions" item 1. Do not re-ask intake. Phase: operate / live. **Open position:** BAC $62 call exp 2026-07-31, 1x, entry $0.89, option_id `784784b8-5a03-4eee-8e27-bdb6e6a8fe19`. RH GTC stop-market @ $0.80 order `6a5906d1-19fe-4787-bcb7-e0b8db5a28ec`. AWS poller on `ssh aws` at `/home/ubuntu/agentic-option-poller` (timer active; Discord webhook alert-only; no auto-place). Agentic account `616665162` only. Venv: `source .venv/bin/activate`. Refresh live snapshot via MCP when working money. Never type literal `<payload.json>` — use real paths.

## Files Touched This Session (high level)
- Live trading via MCP (orders placed on RH, not all in git)
- `logs/option_order_placed.json`, `logs/option_stop_watch.json`, `logs/mcp_payload.json`, `logs/live_portfolio.json`
- `deploy/aws-option-poller/*` — poller, systemd unit/timer, README (new, untracked)
- Server: `/home/ubuntu/agentic-option-poller/` (code, watch.json, .env with webhook, timer enabled)
- `.grok/HANDOFF.md`, `.grok/PROJECT.md` (this handoff)

## How to run (cheat sheet)
```bash
# Local
cd ~/projects/agentic-trading-model && source .venv/bin/activate
python -m agentic_trading session-refresh
# Grok MCP → write-live-snapshot with real path e.g. logs/mcp_payload.json
python -m agentic_trading status --live-only

# AWS poller
ssh aws
systemctl list-timers | grep agentic
journalctl -u agentic-option-stop.service -n 30
cat /home/ubuntu/agentic-option-poller/state/poller_state.json
```

## Current operational snapshot
- **UTC handoff:** 2026-07-16T16:41:32Z
- **Open option:** BAC 62C 2026-07-31 · entry $0.89 · AWS last mark ~$0.89
- **Broker stop:** GTC stop_market @ $0.80 sell-to-close
- **AWS timer:** active · Discord webhook · no Twilio
- **Paper loop:** not running
- **Grok 2m stop scheduler:** cancelled
- **Git:** master `d4f9660`; dirty handoff docs + config.yaml; untracked `deploy/`
