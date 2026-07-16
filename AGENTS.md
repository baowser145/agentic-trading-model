# Agentic Trading Model — Agent Rules

Personal paper-first trading system with optional **Robinhood Agentic** live paths.
Default is always **paper**. Live = Agentic account only, supervised.

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

**If BP is free** and user wants an options idea:

1. `python -m agentic_trading propose-option --type call` (or put / `--symbol`).
2. If proposal `blocked`, stop and explain.
3. MCP: `get_option_chains` → `get_option_instruments` (active, tradable, expiries in proposal window).
4. `python -m agentic_trading pick-option-contract --file <instruments.json> --type call --strike-hint <n>`
5. MCP: `get_option_quotes` for picked `option_id` → choose limit near ask (debit).
6. `python -m agentic_trading prepare-option-review --option-id <id> --price <limit> --symbol <SYM>`
7. If prepare is **not** blocked: MCP `robinhood__review_option_order` with `mcp_review_args`.
8. `python -m agentic_trading record-option-review --file <review.json> --request-file logs/option_review_request.json`
9. Present review (cost, alerts) to the user.
10. **`place_option_order` only after explicit user yes** (e.g. “place it”, “confirmed”).  
    Generic “looks good” is not enough if ambiguous — ask once.

Never place on non-agentic accounts. Never enable `allow_live` / thrash stock day-trades on live without explicit user request.

## Hard rules

- Paper is default (`trading_mode: paper`, `allow_live: false`).
- Agentic account only for live MCP trades.
- Options automation is **long premium / single-leg Level 2** first; no multi-leg via MCP.
- No 0DTE. Default long-premium window: **7–31 DTE** only (quick turn).
- Daily options rule: **at most one** open long-premium idea. If one is open, manage it (do not add) until exit rules hit.
- Daily scan universe: liquid focus names (research top-3) + any single-name holdings on Agentic; pick **one** best debit call/put or **hold**.
- Never commit `.env` or secrets.
- Prefer small fixes; don’t expand scope without user ask.

## Useful CLI

```bash
source .venv/bin/activate
python -m agentic_trading session-refresh
python -m agentic_trading status --live
python -m agentic_trading propose-option --type call
python -m agentic_trading pick-option-contract --file instruments.json --type call
python -m agentic_trading prepare-option-review --option-id UUID --price 0.85 --symbol AAPL
python -m agentic_trading record-option-review --file review.json
```
