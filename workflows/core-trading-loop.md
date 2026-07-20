# Workflow: Core Agentic Trading Loop

## Objective
Run a research → select → risk-gate → propose/execute → journal loop for options/equity positions, in either paper mode (simulated fills, safe default) or supervised live mode (writes an intent for a human to execute via Robinhood — the agent never places a live order unattended).

## Required Inputs
- `config.yaml` — `trading_mode` (`paper` default, `live`), `broker.allow_live` (must be explicitly `true` *and* mode=`live` before any live intent is written), risk limits (`risk.max_daily_loss_pct` 5%, `risk.risk_per_trade_pct` 5%), `selector.max_new_entries_per_tick` (2), `daily_focus.enabled` (gates entries to the day's locked research picks only).
- Market data: yfinance (live quotes) or fixture data (tests/dry runs) via `market/quotes.py`.
- Robinhood MCP session, only required for live mode.

## Tools to Use
Entrypoint: `python -m agentic_trading <subcommand>` (`src/agentic_trading/__main__.py`, orchestrated by `engine.py`).

1. **Research** — `research [--llm] [--apply] [--apply-daily] [--daily-n 3]`, module `agent/research.py`: expands the trading universe and, with `--apply-daily`, locks N picks for the day (`agent/deep_research.py` for per-name memos, `agent/sp500_scan.py` for RS/liquidity ranking).
2. **Trading loop** — `run-once` or `run-loop --interval SECS --max-ticks N`: each tick runs `engine.py::one_tick()` — pulls quotes (`market/quotes.py`), generates signals (`strategy/day_trade_playbook.py` primary, `strategy/simple_momentum.py` fallback), ranks/selects setups (`agent/selector.py`, capped at `max_new_entries_per_tick`), applies the hard risk gate (`risk/gate.py` — 5% per-trade cap, 5% daily-loss halt on new buys, max 10 open positions), then routes to a broker adapter:
   - **Paper** (`broker/paper.py`) — simulated fill, always available, default mode.
   - **Live/Agentic** (`broker/agentic.py`, `live/propose_option.py`, `live/pick_contract.py`, `live/supervised_review.py`) — proposes a 7-31 DTE long-premium option, writes the intent to `logs/live_intents.jsonl`; a human reviews and executes it via a Robinhood Agentic session. The code path never submits a live order by itself.
3. **Monitoring** — `status` / `trades --session-dir` for portfolio + journal (`journal.py`); `watch --session-dir` for a local web view (`live/watch_server.py`, port 8787) or the static GitHub Pages demo (`docs/sample_snapshot.json`).
4. **Options scenario testing** — `options-backtest --scenario NAME` / `options-search --iterations 60` (`options_bt/backtest.py` Black-Scholes pricer, `options_bt/runner.py` scenario sweep, `options_bt/agent.py` mutator search) — used to validate the option-selection logic against `options_bt/scenario.py` seed scenarios before trusting it in live proposals.

## Expected Outputs
- `logs/<session>/` — journal of every tick's decisions (`journal.py`), portfolio snapshots.
- `logs/live_intents.jsonl` — only in live mode; human-actionable proposed orders, never auto-executed.
- Watch UI at `localhost:8787` or the GitHub Pages static snapshot.

## Edge Cases & Error Handling
- **`risk/gate.py` is the hard stop** — it caps per-trade risk, halts new buys (not sells) after a 5% daily loss, and caps open positions at 10. Config validation enforces floors on these, but per repo notes, option-placement code has a path that can bypass the enforced floor — treat any change near `broker/agentic.py` or `live/propose_option.py` as risk-sensitive and re-check the gate is actually being called on that path before trusting it.
- **Live mode requires two separate flags to line up** (`trading_mode: live` *and* `broker.allow_live: true`) — this is intentional double-keying to prevent an accidental live intent from a stray config change.
- **`daily_focus.enabled`** restricts entries to the day's locked research picks — if a promising setup outside that list should be tradeable, it needs to go through `research --apply-daily` first, not be special-cased in the selector.

## Success Criteria
- Paper mode: `run-loop` completes its ticks without a risk-gate violation slipping through (spot-check `journal.py` output against the configured limits).
- Live mode: every entry in `live_intents.jsonl` corresponds to a human-reviewed decision (`live/supervised_review.py`) — no code path writes there without going through review.
