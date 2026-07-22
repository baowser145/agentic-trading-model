# Agentic Trading Model — Agent Rules

Personal paper-first trading system with optional **Robinhood Agentic** live paths.
Default is always **paper**. Live = Agentic account only, supervised.

This repo holds three semi-independent pipelines (core trading loop, robinhood_scanner,
stock_screener_30d) — each is documented stage-by-stage in [`workflows/`](workflows/README.md).

## Session start (auto-refresh live snapshot)

When the user works on this repo, live balance, options, or Agentic trading — **at the start of the session** (and again if snapshot is stale):

1. Run `python -m agentic_trading session-refresh` (exit 2 ⇒ needs refresh).
2. If `needs_refresh` is true (or no `logs/live_portfolio.json`):
   - `robinhood__get_accounts` → account with `agentic_allowed=true` (nickname **Agentic**). Use full `account_number` for tools; mask in user prose.
   - `robinhood__get_portfolio` on that account.
   - `robinhood__get_equity_positions` on that account.
   - `robinhood__get_option_positions` with `nonzero=true`.
   - Write a JSON payload and run:
     ```bash
     python -m agentic_trading write-live-snapshot --file <payload.json>
     ```
     Payload shape:
     ```json
     {
       "account_number": "<agentic>",
       "account_nickname": "Agentic",
       "agentic_allowed": true,
       "portfolio": { "...get_portfolio data..." },
       "equity_positions": [ "...positions array..." ],
       "option_positions": [ "...or empty..." ]
     }
     ```
   - Verify: `python -m agentic_trading status --live-only`
3. Do **not** skip refresh because paper state exists — paper ≠ broker truth.

Stale threshold: **15 minutes** (see `session-refresh --stale-seconds`).

## Supervised options path (only if BP is free)

After a fresh snapshot, check `bp_free_for_options` from `session-refresh` (default min BP **$50**).

**If BP is not free:** report cash/BP and open sell holds; do **not** call `review_option_order` / `place_option_order`. Suggest freeing BP (settle sells, cancel stuck sells, deposit).

**Learning mode (user 2026-07-20; BP override 2026-07-20 live test):** `live.learning_mode: true`, **`bp_usage_pct: 1.0`**.
**100% of broker buying_power** may fund new live debits (`usable_bp`).
Paper stocks are primary. Live options: max premium **$50**, **max_open_options: 1**.
If usable BP &lt; min ($50 default), report and do **not** propose/place.

**If BP is free** (usable BP ≥ min) and user wants an options idea:

1. `python -m agentic_trading propose-option --type call` (or put / `--symbol`).
2. If proposal `blocked`, stop and explain.
3. MCP: `get_option_chains` → `get_option_instruments` (active, tradable, expiries in proposal window).
4. `python -m agentic_trading pick-option-contract --file <instruments.json> --type call --strike-hint <n>`
5. MCP: `get_option_quotes` for picked `option_id` → choose limit near ask (debit).
6. `python -m agentic_trading prepare-option-review --option-id <id> --price <limit> --symbol <SYM>`
7. If prepare is **not** blocked: MCP `robinhood__review_option_order` with `mcp_review_args`.
8. `python -m agentic_trading record-option-review --file <review.json> --request-file logs/option_review_request.json`
9. Log review (cost, alerts).
10. **Standing place auth (user 2026-07-17):** if `live.options_place_without_confirm: true`
    and review is clean / prepare not blocked → **`place_option_order` without asking again**.
    Still refuse place when: non-Agentic, BP blocked, max open options hit, day halt, or review hard-blocked.
    Revoke anytime by setting `options_place_without_confirm: false` or user saying stop/revoke.

Never place on non-agentic accounts. Never enable stock thrash on live without explicit user request.

## Hard rules

- Paper is default (`trading_mode: paper`, `allow_live: false`).
- Agentic account only for live MCP trades.
- Options automation is **long premium / single-leg Level 2** first; no multi-leg via MCP.
- No 0DTE. Default long-premium window: **7–31 DTE** only (quick turn).
- Daily options rule: **at most `live.max_open_options` open long-premium ideas** (default was 1; user override 2026-07-17 → **2**). If at max, manage existing (do not add) until exit rules free a slot.
- Daily scan universe: liquid focus names (research top-3) + any single-name holdings on Agentic; pick **one** best debit call/put or **hold**.
- Never commit `.env` or secrets.
- Prefer small fixes; don’t expand scope without user ask.

## Daily options manage rules (user lock-in)

When user grants Agentic access for the day, follow this manage playbook (also in `config.yaml` → `live`):

| Rule | Value |
|------|--------|
| Take profit | **+10% to +20%** on option premium (mark vs entry) |
| Stop loss | **−10%** on option premium (user-chosen; tight — expect noise stops) |
| Time | Exit / force manage by **≤3 DTE** |
| Max open long premium | **2** (user override 2026-07-17; was 1) |
| Account day kill | If account day P&L ≤ **−5%** → **stop new trades** that day |

Manage order when an option is open: check stop → take-profit band → DTE → else hold.  
**Do not** open a second debit option while one is open.  
**Do not** propose new risk after day halt.  
**Place auth:** standing yes for Agentic long-premium that clear gates (see step 10 above).

## Morning paper routine (default before live)

```bash
source .venv/bin/activate
# Assess SPY/QQQ → call|put|hold, scan focus, watch, then paper trigger if call
python -m agentic_trading morning-paper --quotes yahoo --session-dir logs/paper_morning
# Settlement: config trade_when_cash_available: false (cash ≠ BP lag after sells)
```

**Bias → focus ranking (2026-07-17 research):**
| Bias | `daily_picks` | Long equity entries |
|------|----------------|---------------------|
| **call** | Strongest RS (liquid) | Allowed (playbook) |
| **put** | Weakest liquid single-names | **Blocked** (put/short watch for options) |
| **hold** | Empty | **Blocked** |

Universe = config mega + liquid second tier (HOOD, COIN, PLTR, …) — not full market.

Postmortem (live options 2026-07-17): `logs/live_options_postmortem_2026-07-17.md`

## Useful CLI

```bash
source .venv/bin/activate
python -m agentic_trading session-refresh
python -m agentic_trading status --live
python -m agentic_trading morning-paper --quotes yahoo
# Quality gate before promoting a name to daily_focus (memo under logs/deep_research/)
python -m agentic_trading deep-research --ticker PLTR --llm --quotes yahoo
# S&P 500 liquid+RS scan → optional top-N deep-research (not Russell 3000)
python -m agentic_trading sp500-scan --top 10 --deep-research --deep-n 3 --llm --quotes yahoo
python -m agentic_trading propose-option --type call
python -m agentic_trading pick-option-contract --file instruments.json --type call
python -m agentic_trading prepare-option-review --option-id UUID --price 0.85 --symbol AAPL
python -m agentic_trading record-option-review --file review.json
```
