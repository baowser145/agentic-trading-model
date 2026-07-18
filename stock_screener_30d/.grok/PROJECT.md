# Project: Stock Screener 30d

## Idea
A Python application that screens for potential stocks to hold for only 30 days.

## Target Customer
Solo developer / personal use (just me)

## Time Budget
No hard deadline

## Monetization
Not monetized yet — personal tool first

## Tech Stack
Python + simple web UI (FastAPI + basic frontend)

## MVP Scope (the ONE thing)
CLI `scan`: daily top N stocks matching explicit 30-day hold criteria (YAML config). CLI `backtest`: validate 30-day forward returns vs SPY after costs.

## Build Mode
single-stream — CLI-first (web UI deferred until strategy proves out)

## Phase
roast → complete

## Current Status
MVP shipped. CLI scan + backtest working. 8/8 tests pass.

## Reshaped MVP Scope (approved)
CLI-first: `scan` + `backtest` commands. Web UI deferred until strategy proves out.

## Success Criteria
- [x] Roast verdict: Green Light or Reshape (with reshaped scope)
- [x] MVP scope implemented and working
- [x] Verification passed (2/2 loops, critical fixes applied)
- [x] Handoff doc current