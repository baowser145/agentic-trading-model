# Build Log

## Verification — 2026-07-02 — CLI MVP (scan + backtest)

- Loop A: PASS (install, 5 tests, scan, backtest, CLI help)
- Loop B: FAIL (critical bugs found)
- Fixes applied: empty ticker list, RSI NA guard, SMA config, 52w rolling window, backtest top-N parity, cash months, SQ removed, config validation
- Re-verify: 8/8 tests PASS, scan smoke PASS

## Verification — 2026-07-02 — Post-fix

- Loop A: PASS (8 tests, scan works)
- Loop B: Critical issues addressed; remaining survivorship bias documented as MVP limitation