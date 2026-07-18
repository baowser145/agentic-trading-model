# Design Plan — Robinhood AI Scanner + Discord Alert System

## Architecture

Two separate runtimes, deliberately decoupled:

1. **Backtest (Milestone 0, gate)** — a local Python/pandas job using free historical data
   (yfinance). No Robinhood MCP dependency, no live account involved. Its only job is to answer:
   "does this strategy beat buy-and-hold net of costs?" before anything live gets built.
2. **Live scan + alert (Phase A, only if M0 passes)** — a scheduled Claude Code session (via
   `/schedule`), triggered by cron, that uses the already-connected Robinhood MCP tools for live
   quotes/historicals, applies the same strategy rules, and posts a Discord alert. It never calls
   an order-placement tool.

These are separate because the backtest needs 2+ years of history (cheap via yfinance, not
practical to pull that volume through live MCP calls), while the live scan needs real-time-ish
data (which is what the Robinhood MCP tools are for).

## Known data limitation (flagged now, not discovered later)

Free intraday premarket data at 2-year depth doesn't exist (yfinance only gives ~30-60 days of
1-minute/prepost data). So:

- **Gap scanner** backtests cleanly on 2+ years of daily bars (gap = today's open vs. prior close).
- **Trend-join-long** backtests its daily-bar conditions (close > prior day high, close > SMA200)
  over the full 2+ year window, but the premarket-high and "after 10am ET / new intraday high"
  conditions can only be backtested on the ~30-60 days of free intraday data yfinance provides.
  That second check is reported separately, labeled as a small-sample sanity check, not treated as
  the primary gate.

## Strategy rules (shared, parameterized — one source of truth for backtest and live)

`strategies.py`:
- `gap_scan(row, prev_close, gap_pct_min=5.0, price_min=3.0, volume_min=...)`
- `trend_join_long(row, prev_day_high, sma200, premarket_high=None, after_hour_et=10)` —
  `premarket_high` optional so the same function runs in both the daily-bar backtest (None) and the
  full live version (populated from Robinhood MCP intraday data).

## Go/no-go gate (Milestone 0)

For each strategy, over a fixed ticker universe (start with the 8 liquid large-caps already used
as examples: AAPL, NVDA, AMD, TSLA, MSFT, META, GOOGL, AMZN) and 2+ years of daily data:

- Win rate, average return per trade, total strategy return, max drawdown.
- Costs modeled: 0.05% slippage per side (Robinhood is commission-free, but premarket/gap fills
  are not free of spread) — no pretending costs are zero.
- **Pass condition:** total return net of modeled costs beats buy-and-hold on the same tickers over
  the same window, AND expectancy per trade is positive.
- **If it fails:** stop and iterate the rules (tighter filters, different tickers, different
  thresholds) before building anything live. Do not build Phase A on a failing backtest.

## File structure

```
~/projects/robinhood-scanner/
  .claude/
    PROJECT.md
    roast-verdict.md
    plan.md
    HANDOFF.md            (written at handoff time)
  backtest/
    strategies.py          # shared rule definitions
    data.py                 # yfinance fetch + caching
    run_backtest.py         # CLI: computes metrics, prints/saves go/no-go report
    tickers.txt              # starting universe
    report.md                # written after each backtest run
  alerts/
    send_discord.py          # tiny script: POST a formatted message to DISCORD_WEBHOOK_URL
  docs/
    live-scan-prompt.md       # the exact instructions the scheduled Claude session follows
  config/
    .env.example              # DISCORD_WEBHOOK_URL=
  .gitignore                  # excludes .env
  README.md
```

## Live scan design (Phase A, post-gate)

`docs/live-scan-prompt.md` is what the `/schedule`d Claude session actually runs each trigger. It
instructs the agent to:
1. Pull quotes/historicals for the watchlist via the connected Robinhood MCP tools
   (`get_equity_quotes`, `get_equity_historicals`).
2. Apply the same `gap_scan` / `trend_join_long` rules from `strategies.py` (values only — the
   scheduled session runs the Python rule functions via Bash, it does not re-derive the math
   itself, to avoid LLM arithmetic errors on entry/stop/size).
3. For any hit, compute a suggested entry/stop/size using a fixed risk model (e.g. 1% account risk
   per idea, stop at recent swing low) — again via a Python helper, not free-form LLM math.
4. Format and POST to Discord via `alerts/send_discord.py`.
5. **Never call a Robinhood order-placement tool.** This is enforced by the prompt AND by simply
   not granting/using that tool in this pipeline.

Two schedules via `/schedule`:
- Premarket gap scan: ~8:30am ET on trading days.
- Trend-join-long scan: every 30 min, 10am-3:30pm ET.

## Workstream breakdown (single-stream, sequential milestones)

| # | Milestone | Gate before continuing |
|---|-----------|------------------------|
| M0 | Repo scaffold, .gitignore, README, config | — |
| M1 | `strategies.py` shared rule definitions | code review only |
| M2 | Backtest engine + report | **go/no-go: must beat buy-and-hold net of costs** |
| M3 | `alerts/send_discord.py` + manual test send | Discord message actually arrives |
| M4 | `docs/live-scan-prompt.md` + risk-sizing helper | user reviews wording, confirms no order-tool use |
| M5 | Wire up `/schedule` cron jobs | one live dry-run scan produces a sane (or correctly empty) alert |
| M6 | Verification (2 loops) | per `/verification` skill |
| M7 | Handoff | doc written |

If M2 fails the gate, we stop and iterate on strategy rules before M3+ — we do not build the live
alert pipeline on an unvalidated strategy.

## What you'll need to do (can't be automated)

- Create a Discord webhook (Server Settings → Integrations → Webhooks → New Webhook → copy URL)
  and put it in `config/.env`.
- Confirm the starting ticker watchlist (defaulting to the 8 tickers above unless you want others).
