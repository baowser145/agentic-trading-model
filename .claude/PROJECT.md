# Project: Robinhood AI Scanner + Alert System

## Idea
An AI-assisted trading workflow that replicates the "Claude + TradingView" premarket-gap and
trend-join-long scanner pipeline from the Humbled Trader video, but built on Robinhood instead of
TradingView, using Claude's existing Agentic Robinhood MCP connection for scanning/market data and
a Discord webhook for alert delivery. Runs on a schedule (scheduled Claude sessions via cron),
starting in a manual-approval mode and later graduating to full autonomous trade execution once
proven out.

## Target Customer
Internal / personal use — solo trader (the user), not a product for others.

## Time Budget
1 month.

## Monetization
Not monetized — internal/personal tool.

## Tech Stack
- Claude scheduled cron sessions (via /schedule) as the "runtime" — no separate always-on server.
- Robinhood Agentic MCP tools (already connected to this Claude account) for quotes, historicals,
  scans, watchlists, and (later phase only) order placement.
- Discord webhook for alert delivery (no bot hosting required — a channel webhook URL is enough).

## MVP Scope (the ONE thing, reshaped per roast verdict)
A backtest-gated premarket gap scanner + trend-join-long strategy scanner that runs on a schedule,
using Robinhood MCP tools for live data, and sends a Discord alert with a suggested trade (ticker,
entry, stop, size) requiring the user's manual approval before any order is placed. The live pipeline
is only built if the strategy rules first pass a historical backtest (2+ years, slippage/fees
modeled) beating a buy-and-hold baseline. No autonomous order placement in this project.

## Phased Rollout (explicit, user-approved, reshaped per roast)
- **Milestone 0 (gate):** Backtest both strategies (yfinance, 2+ years, realistic slippage/fees) vs.
  buy-and-hold baseline. This MUST pass before Phase A is built. If it fails, iterate the strategy
  rules or stop — do not build the live pipeline on an unvalidated strategy.
- **Phase A (this build, only after Milestone 0 passes):** Live scan + Discord alert with suggested
  trade. Manual approval only — the system never calls a Robinhood order-placement tool.
- **Phase B (explicitly OUT OF SCOPE for this project):** Full autonomous execution — Claude placing
  orders via Robinhood MCP with no per-trade human approval. Per roast verdict, this requires a
  code-enforced (non-LLM) daily loss circuit breaker, a statistically validated track record from
  Phase A (4+ weeks minimum), and its own separate roast before it is ever built. Do not wire any
  order-placement MCP tool into this pipeline.

## Build Mode
Single-stream initially (scanner logic + Discord delivery + scheduling), may split into workstreams
after design if warranted.

## Phase
build (Milestone 0/1/2) → complete. Gate result: FAIL (confirmed at both 8-ticker and full S&P 500 scale).

## Current Status
Backtest extended to support full S&P 500 universe (backtest/data.py: get_sp500_tickers() via
Wikipedia + fetch_daily_bulk() via chunked yf.download; run_backtest.py --universe sp500). Ran
against 500 S&P 500 tickers, 3y history, 2y test window, 5bps/side slippage. Results in
backtest/report_sp500.md:
- Gap Scan: -1.63% avg strategy return vs +60.66% avg buy-hold, avg expectancy -0.40%/trade (375/500
  tickers triggered trades). FAIL.
- Trend Join Long: +13.09% avg strategy return vs +50.88% avg buy-hold, avg expectancy +0.14%/trade
  (barely positive, essentially noise) (500/500 tickers triggered trades). FAIL.
Note: the 2y test window was a strong bull market (avg buy-hold ~50-60% across the S&P 500), which
is an unusually high bar — a minority of individual tickers did have a strategy beat buy-hold (e.g.
COIN, FSLR, AES, DG for gap scan; TER, TTD, CHTR for trend join), but in aggregate neither strategy
clears the bar. Original 8-ticker result (backtest/report.md) is consistent with this at smaller
scale. Per plan.md gate rule, live pipeline (Phase A) is NOT being built on these rules.

User asked to run the scan live "for tomorrow" despite the failed gate. Clarified intent via
AskUserQuestion — user chose "informational dry-run only" (not real trade suggestions). Built:
`live_scan/evaluate.py` (same strategy math as backtest/strategies.py, invoked via Bash so the LLM
never free-hands the arithmetic) and `docs/live-scan-prompt.md` (instructions requiring the report
to lead with the negative-expectancy caveat, forbidding any order-placement/review tool call and any
Discord/external posting). Scheduled a ONE-TIME cloud routine via RemoteTrigger (not a local cron —
runs in Anthropic's cloud with the Robinhood MCP connector attached) named
"robinhood-scanner-dry-run-2026-07-07", trigger_id trig_01Tgqc2mcX865u1hnuzac9N5, firing
2026-07-07T13:40:00Z (8:40am CDT / 9:40am ET, ~10 min after market open) for the 8-ticker watchlist.
Prompt is self-contained (no repo dependency) since the local project isn't in git/pushed anywhere.
Does not repeat after firing. Live pipeline (repeatable Phase A) still NOT built — this is a single
informational run only.

## Success Criteria
- [x] Roast verdict: Reshape, reshaped scope accepted by user
- [ ] Milestone 0: backtest passes go/no-go gate (beats buy-and-hold net of costs)
- [ ] MVP scope implemented and working (scan → Discord alert, manual approval, no auto-orders)
- [ ] Verification passed (2/2 loops)
- [ ] Handoff doc current

## Update 2026-07-06 (small-sample full-condition sanity check)
Ran a small-sample sanity check of the FULL 4-condition Trend Join Long (adding real premarket-high
and hour-ET, which the primary 2-year/500-ticker gate could not test due to free-data limits). Used
real Robinhood get_equity_historicals extended-hours data (not synthetic). Scope had to be reduced
from the full universe to 20 tickers / 7 trading days / 33 trades, because pulling intraday extended
bars at full S&P-500 scale exceeded practical per-call data volume (a single 10-symbol/7-day/hourly
request already returned ~200K characters and had to be redirected to disk). See
backtest/report_small_sample_full_conditions.md for full detail.

Result: FAIL, consistent with the primary gate. Avg strategy total return +2.01% vs avg buy-hold
+3.02-3.43% over the same window; avg per-trade expectancy essentially zero (-0.01%). Adding the
premarket-high and time-of-day conditions did not rescue the strategy. Live pipeline (Phase A)
remains not built.

## Update 2026-07-06 (PEAD earnings-gap filter test)
Tested whether filtering Gap Scan to earnings-day gaps only (Post-Earnings Announcement Drift
hypothesis) rescues it, using real Robinhood earnings-calendar data cross-referenced with cached
S&P 500 daily bars over a ~7-month window (2025-11-01 to 2026-06-04; 1,164 verified earnings events,
501 tickers). See backtest/report_pead_earnings_gap.md for full detail.

Result: a real, theory-consistent, but not-yet-conclusive signal. Earnings-gap trades: +0.38%/trade
expectancy, 56.4% win rate, 172 trades (one-sample t-test vs zero: p=0.071, suggestive not
significant). Non-earnings-gap trades: -0.62%/trade, 42.6% win rate, 265 trades. The difference
between the two buckets IS statistically significant (two-sample t-test p=0.019) and matches the
PEAD hypothesis directionally. A secondary PEAD-style 20-trading-day hold on earnings gaps performed
worse (+0.07%/trade) than the same-day exit, which cuts against the classic multi-week PEAD pattern.
Neither beats the +17.01% buy-and-hold benchmark over the same window in raw total return, though
that comparison undersells intermittent-signal strategies vs continuous exposure.

This is the first result in the project that isn't flat/negative noise -- but p=0.071 alone isn't
below the standard significance bar, and the sample is 7 months vs the 2-year gate used elsewhere.
Verdict: promising, not yet proven. Recommended next step (not yet done): re-run over a full 2-year
earnings-date window before treating this as validated. Live pipeline still not built.

## Update 2026-07-06 (PEAD full 2-year re-test, per user request)
Re-ran the PEAD earnings-gap test over the FULL 2-year window (2024-07-06 to 2026-07-06, matching
the primary Gap Scan/Trend Join Long backtest window exactly), using 4,030 verified S&P 500 earnings
events across 501 tickers (25 overlapping ~31-day get_earnings_calendar windows, deduplicated). See
backtest/report_pead_earnings_gap.md for full detail.

RESULT CHANGED FROM THE 7-MONTH PASS:
- Same-day earnings-gap "pop" (+0.38%/trade in the 7-month sample) did NOT hold up at full scale:
  -0.09%/trade, p=0.454 (not significant). It was noise -- good example of why the full-universe/
  full-window re-test mattered.
- BUT: the classic PEAD 20-trading-day hold on earnings-gap days IS real: +1.47%/trade net of
  slippage, 555 trades across 289 tickers, win rate 55.3%, **p=0.0013**. This is the first result in
  the entire project with a statistically significant positive edge.
- Non-earnings gaps remain negative (-0.70%/trade), consistent with all prior findings.
- Still does not beat raw buy-and-hold total return (+59.52% over the window) since it's an
  intermittent-signal strategy, not continuously invested -- expected and not disqualifying given
  the per-trade expectancy is what actually matters here.

IMPORTANT SCOPE NOTE: this passing strategy is NOT the original Gap Scan / Trend Join Long
day-trading concept. It's a different, event-driven swing strategy: detect a >=5% gap on an earnings
day, enter next day's open, hold ~1 month (20 trading days), exit at close. Needs an earnings
calendar feed (Robinhood's get_earnings_calendar, confirmed working) but does NOT need premarket
data, intraday timing, or constant scanning -- one check per day suffices since positions hold for a
month. If Phase A gets built, THIS is the candidate strategy, not the original video-inspired ones.

Caveats: single historical period only (no forward/out-of-sample validation yet), return std dev
(10.68%) is large relative to mean (1.47%) so position sizing/risk management matters a lot.

## Update 2026-07-06 (independent verification + decision to build Phase A)
Independently re-verified the PEAD 20-day finding from scratch (backtest/verify_pead.py, different
code, same underlying cached earnings-dates + price data, not just re-reading the fork's numbers):
615 trades, win rate 55.8%, mean +1.70%/trade, p=0.00019. Numbers don't match the fork's exactly
(555 trades/p=0.0013) -- likely differences in overlap-handling of consecutive earnings-gap days --
but the qualitative finding replicates: real, positive, statistically significant effect in this
historical period, not a fabrication.

Flagged important caveat to user before proceeding: ~6-7 different strategy variants were tested
this session (Gap Scan, Trend Join Long weak/full, PEAD same-day x2 samples, PEAD 20-day x2 samples).
Finding one that clears a strong significance bar after that many cuts carries real multiple-
comparisons risk even without dishonest tuning. Recommended an out-of-sample test on a different
market period (e.g. 2022-2024) before treating this as validated -- NOT yet done.

User decided to proceed to Phase A build anyway (skipping the out-of-sample test for now). Per user
instruction, building Phase A around the earnings-gap + 20-day-hold strategy specifically (not the
original Gap Scan/Trend Join Long). The out-of-sample-caveat is being baked directly into the Discord
alert copy itself so it is never presented as a proven edge. Architecture: stateless daily check
(cloud routines don't share a filesystem between runs) -- each day's run re-derives both entry
signals (earnings-gap today) and exit signals (an earnings-gap entry exactly 20 trading days ago) from
the earnings calendar + price data, with no persisted position state needed.

Note: a background fork attempting to start this build hit an API session limit mid-response and
produced no actual files -- proceeding with a fresh direct build instead of trusting that stub.

## Update 2026-07-06 (Phase A built: earnings-gap PEAD daily Discord alert)

Built directly (not via fork, to avoid the session-limit issue):
- `backtest/strategies.py`: added `EarningsGapPeadParams`, `is_earnings_gap`, `earnings_gap_pead_entry`,
  `earnings_gap_pead_exit_due` -- the validated strategy's rules, in the same module as everything
  else, math verified via smoke tests.
- `live_scan/evaluate.py`: added `pead-entry` and `pead-exit` CLI subcommands (smoke-tested).
- `alerts/send_discord.py`: posts to the project Discord webhook; smoke-tested with a real message
  (delivery confirmed via `Sent.` response from Discord's API).
- `config/.env`: real Discord webhook URL saved locally (gitignored). NOTE: the cloud routine below
  cannot read this file (isolated sandbox, no shared filesystem) -- the webhook URL is embedded
  directly in the routine's prompt instead.
- `docs/phase-a-pead-prompt.md`: full instructions for the daily check (local reference copy).
- **Recurring cloud routine** created via RemoteTrigger: name `robinhood-scanner-pead-daily`,
  trigger_id `trig_01DngYnHFJRouJwy3vy9an2Q`, cron `15 20 * * 1-5` (weekdays 20:15 UTC = 4:15pm ET /
  3:15pm CDT during current DST -- note this is a static cron and will drift by an hour across
  DST transitions, not auto-corrected). Robinhood MCP connector attached. Self-contained prompt
  (writes its own `pead_lib.py` each run, since stateless) implementing: earnings-calendar-filtered
  candidate search (not a full 500-ticker daily scan -- keeps the job small), entry check, exit check
  via trading-day counting, mandatory out-of-sample-caveat text in every message, hard prohibition on
  any order-placement/review tool call, and delivery via curl directly to the Discord webhook.
  Triggered a manual test run immediately after creation (`action: run`) -- **user must check Discord
  to confirm the test message actually arrived**, since there is no log-viewing tool available here to
  verify cloud routine output directly.

Still true: no autonomous execution anywhere in this pipeline. Phase B remains explicitly out of
scope. Out-of-sample validation (2022-2024) of the PEAD strategy itself is still not done -- the
caveat is surfaced in every alert instead of blocking the build, per user's explicit decision.

## Update 2026-07-06 (alert format: buy/exit prices, no fabricated stop/target)

User asked for a Buy Under / Sell Target / Stop Loss / G-L Ratio visual format seen elsewhere.
Flagged that this implies a pre-earnings, defined-risk strategy different in kind from our validated
post-earnings-gap + time-based-exit strategy (no stop-loss or price target was ever backtested).
User clarified the specific numbers were illustrative -- what they actually want is just a suggested
buy price and exit price.

Resolved honestly: since the exit is time-based (20 trading days), there's no real target/stop to
report. Instead, added a "Projected Exit" price = current_price * 1.0147 (the backtested average
per-trade return), explicitly labeled every time as a statistical expectation from the backtest
average, NOT a price target, stop-loss, or guarantee. Updated both `docs/phase-a-pead-prompt.md` and
the live cloud routine (trig_01DngYnHFJRouJwy3vy9an2Q) with this format. Schedule unchanged (weekdays
4:15pm ET / 20:15 UTC); next automatic run is tomorrow, no manual action needed.

## Update 2026-07-06 (stop-loss backtested and rejected; projected exit removed)

Before adding a real stop-loss, backtested it first (`backtest/backtest_pead_stoploss.py`, reuses
cached earnings-dates + 3y daily bars, no new Robinhood calls): tested -5%/-8%/-10%/-15% stops
against the no-stop baseline (615 entries, 303 tickers). RESULT: every stop level REDUCED
expectancy vs. no stop -- e.g. -5% stop: mean return dropped from +1.70% to +0.84% (nearly halved),
win rate dropped from 55.8% to 40.0% (below a coin flip). Even the loosest tested stop (-15%) still
underperformed no-stop (+1.52% vs +1.70%). Classic drift-strategy pattern: tight stops lock in
temporary dips that would have recovered by day 20. Full comparison table in conversation history;
raw results also in `backtest/cache/pead_stoploss_results.json`.

Decision (user-confirmed): no stop-loss, and also no projected-exit price at all -- entries in the
Discord alert now show only ticker/gap/earnings info, current price, and suggested buy price (next
open). The real worst-case risk (-37.95% on the worst historical no-stop trade) is not currently
stated in the alert copy -- worth adding if the user wants that visibility later. Updated both
`docs/phase-a-pead-prompt.md` and the live cloud routine (trig_01DngYnHFJRouJwy3vy9an2Q) accordingly.
EXIT signals (20-trading-day hold reached) are unaffected -- they already report a real current
price, not a projection.

## Update 2026-07-06 (cloud routine delivery bug found and fixed)

User asked to run the scan for tomorrow; triggered the routine manually but NOTHING arrived in
Discord (not even the "no signals today" message the prompt mandates). Diagnosed by running the
exact same check directly in this session (same Robinhood MCP tools): today (2026-07-06) had no
S&P 500 earnings reports in the entry window, and the three tickers approaching their 20-day exit
(SJM ~16-17 trading days, COO ~18-19, CASY ~15-16, cross-checked against backtest/cache/sp500_tickers.txt)
weren't due yet (COO closest, likely due 2026-07-07). So "no signals" was the CORRECT answer, but the
routine should have sent that message regardless and didn't -- a delivery bug, not a data problem.
Manually sent the correct result to Discord in the meantime.

Root cause suspected: Step 6 originally used a fragile one-line `curl` wrapped around a nested
`python3 -c "...json.dumps(...)"` inside bash command substitution inside double quotes -- multiple
layers of shell quoting an LLM agent can easily mis-type or bash can mis-parse. Fixed by replacing it
with a plain Python script (`send.py`, stdlib `urllib.request` only, no `requests` dependency risk)
written via the Write tool and run directly -- no shell quoting involved. Also added an explicit
instruction that Step 6 must always execute regardless of signal count (repeating the point from
the original prompt, since it was apparently not followed). Triggered a fresh test run after the fix
-- user needs to confirm in Discord whether this resolved it.

## Update 2026-07-07 (real root cause found: cloud sandbox blocks network egress; pivoted to local cron)

The "fixed" test run from 2026-07-06 22:38 UTC still produced nothing 21+ hours later -- the quoting
fix wasn't the real problem. Ran a minimal diagnostic (temporarily replaced the routine's prompt):
just try `urllib.request.urlopen('https://discord.com')` and a real webhook POST, report raw
output/errors, do nothing else. Result: **nothing arrived in Discord at all**, not even the trivial
diagnostic message. Conclusion: the cloud CCR sandbox (trig_01DngYnHFJRouJwy3vy9an2Q's environment)
blocks general outbound network calls from Bash entirely (curl, urllib, presumably `requests` too).
Only the explicitly-attached Robinhood MCP connector has network access, because MCP tool calls go
through a different, sanctioned channel than raw Bash sockets. This is a platform sandboxing
constraint, not a bug in any script -- no amount of rewriting the send step would have fixed it.

Presented the user two real options: (A) local cron via the `CronCreate` tool, running in this
interactive Claude Code session where Bash has already proven it can reach Discord (used successfully
twice earlier), or (B) check claude.ai/customize/connectors for a Discord MCP connector to attach to
the cloud routine instead (untested, unknown if it exists). User chose (A) for now.

**Current state:**
- Cloud routine `trig_01DngYnHFJRouJwy3vy9an2Q` (`robinhood-scanner-pead-daily`) is now **disabled**
  (`enabled: false`) -- it can still pull real Robinhood data fine, but can't deliver to Discord, so
  there's no point letting it keep firing.
- Local recurring cron job (via `CronCreate`, job id `eb540aa4`) created instead: weekdays 3:15pm CDT
  (4:15pm ET), same schedule as before. Runs the same daily check logic using LOCAL files
  (`live_scan/evaluate.py`, `alerts/send_discord.py`, `config/.env`) plus live Robinhood MCP tool
  calls in this session.
- **Important limitations of the local cron approach** (told to user): only fires while this Claude
  Code session/terminal stays open on their machine (closing it kills the job); CronCreate jobs
  auto-expire after 7 days regardless and need to be recreated; nothing is persisted to disk for
  this job, it lives only in-session.
- If the user wants durable "always on, laptop can be off" automation later, revisit option (B)
  above (a Discord MCP connector for the cloud routine), which was not explored/confirmed to exist.

## Update 2026-07-07 (local cron job's first automatic firing -- confirmed working)

Local cron job `eb540aa4` fired automatically for the first time at its scheduled 3:15pm CDT time.
Result: no entry signals, no exit signals (SJM ~18 trading days elapsed, CASY ~17, both still short
of the 20-day threshold; matches the manual check run ~50 minutes earlier the same day, as expected
since market was already closed and nothing new could appear in that window). Message delivered to
Discord successfully, confirming the local-cron delivery path works end-to-end without manual
intervention.

## Update 2026-07-07 (all-US-stocks backtest: expansion NOT justified, S&P 500 restriction stays)

Backtested the validated PEAD strategy (earnings-gap + 20-trading-day-hold) against the full
NYSE/NASDAQ/AMEX universe (5,992 tickers from NASDAQ Trader symbol directories, 503 of which are
S&P 500) to check whether expanding the live scanner beyond S&P 500 is justified, per user request.
Same 2yr window/rules as the validated backtest. See `backtest/run_pead_all_us.py` and
`backtest/cache/pead_all_us_results.json` for full detail (raw report file could not be written by
the executing subagent per harness policy; results captured here instead).

| Bucket | Tickers | Trades | Mean/trade | Win rate | Std dev | Worst | Best | p-value |
|---|---|---|---|---|---|---|---|---|
| S&P 500 | 277 | 489 | +1.84% | 56.0% | 11.5% | -37.9% | +74.5% | 0.00047 |
| Non-S&P-500 | 1,725 | 3,401 | +1.47% | 50.5% | 22.0% | -92.6% | +230.7% | 0.00010 |
| Combined | 2,002 | 3,890 | +1.51% | 51.2% | 21.0% | -92.6% | +230.7% | 0.0000071 |

**Verdict: do NOT expand beyond S&P 500.** Mean returns look superficially similar, but outside the
S&P 500: win rate collapses to barely-above-coin-flip (50.5%), volatility nearly doubles (22.0% vs
11.5% std), and worst-case trade risk goes from -38% to a near-total-loss -92.6%. Risk-adjusted
quality (mean/std) is roughly 2.5x worse outside the S&P 500. The non-S&P-500 p-value looks
"significant" only because of large trade count (3,401), not because it's a well-behaved edge --
classic case of statistical significance without practical reliability. Live scanner stays
restricted to S&P 500; this was a real, data-driven no, not a default/conservative choice.

## Update 2026-07-07/08 (new strategy: pre-earnings timing screen, second cron job)

User separately runs a "Daily Value + Catalyst" scanner (Robinhood scan_id
77411ffb-3ce4-4042-8e43-3bb27efa6aae, managed in a different session -- not visible/editable via
this project's RemoteTrigger) and asked why a WFC call option trade from it made money. Investigated
via real trade history (get_pnl_trade_history/get_option_orders): WFC $95c exp 2026-07-17, bought
2026-07-01 @ $0.13 (10 contracts), sold 2026-07-07 @ $0.40 via a progressively-raised trailing stop,
+$270 realized. Mechanism: WFC rallied from a 6/30 local bottom into Q2 earnings (confirmed
2026-07-14 AM, one week after the trade closed) -- a plausible pre-earnings-anticipation move,
amplified by far-OTM option leverage, closed out before the actual earnings-day volatility.

That scanner's "Value" half (analyst Buy%/price-target/EPS-growth estimates) is NOT backtestable --
no tool or data source gives historical point-in-time analyst data; the scanner gets it via live web
search, which only reflects the present. Only the "Catalyst" half (timing relative to the report) is
testable with data we have.

**New backtest: pre-earnings timing** (`backtest_pre_earnings_timing.py` /
`backtest_pre_earnings_control.py` / `verify_pre_earnings_timing.py`, S&P 500, buy N days before
earnings, sell the day before the report): swept lead times 3-60 days with a random-entry control
(same ticker, same holding length, random start) to separate real signal from generic bull-market
drift. Result: incremental edge (real minus random-control) PEAKS around 18-30 days
(~+0.7 to +1.1pp over control), collapses toward zero by 50 days, and goes NEGATIVE at 60 days (a
60-day lead lands near the tail of the PRIOR quarter's post-earnings settling period, not real
anticipation). Independently re-verified with a fresh vectorized implementation -- real-trade numbers
matched exactly, control pattern held under a different RNG seed. This is weaker evidence than PEAD
(smaller effect size, not yet as rigorously stress-tested) but real, not an illusion.

**Broader-universe check** (`backtest_pre_earnings_all_us.py`, reusing the all-US-stock cache from
the PEAD universe test): same pattern as PEAD -- risk-adjusted quality (mean/std) is 4-5x worse
outside S&P 500 at 18-21 days, ~2x worse even at the best 25-30 day window, and worst-case single
trade outside S&P 500 is consistently a near-total loss (-96%) at every lead time, even with a
price>$3/volume>500k liquidity floor. Confirmed: do not expand this strategy beyond S&P 500 either.

**Live screen delivered** (2026-07-07): 307 S&P 500 tickers had earnings in the live 18-30 day
window (peak earnings season) -- too many to use raw. Built `rank_pre_earnings_candidates.py`:
backtests each candidate's OWN individual history (~8 quarters, 25-day lead) using cached data,
auto-filters to win_rate >= 60% AND worst >= -20% (excludes coin-flip/high-tail-risk names like
SMCI and HOOD despite good headline means), outputs top 10. Delivered manually to user: MPWR, GLW,
VRT, STX as top picks (GLW notably 8-for-8 historically). Gave suggested buy price (current price)
and a suggested sell DATE (day before report) -- deliberately NOT a sell/target price, consistent
with the no-fabricated-numbers rule applied throughout this project. Flagged that all 10 candidates
were down 2.4-7.8% the day of the screen, a likely broad sector/market move worth knowing before
entry, separate from the backtest signal.

**Second recurring local cron job created**: weekdays 7:33am CDT (before market open), running this
pre-earnings screen end-to-end (live earnings-calendar pull -> S&P-500 filter -> per-ticker backtest
via rank_pre_earnings_candidates.py -> live quotes + company names -> Discord). Same local-cron
limitations as the existing PEAD job (`eb540aa4`, weekdays 3:15pm CDT): both require this Claude Code
session to stay open on the user's machine, and both auto-expire after 7 days and need recreating.
Two independent jobs now running, covering pre-earnings (morning) and post-earnings PEAD (afternoon)
-- different strategies, different validation strength, not merged into one filter.

## Update 2026-07-07 (pre-earnings alert reformatted to card style, header trimmed)

User asked for the Value+Catalyst scanner's card visual style (ticker/company name, emoji, report
date, current price) applied to this alert, with the long caveat paragraph trimmed to just one line.
Kept the style but NOT the Buy Under/Sell Target/Stop Loss/G-L Ratio fields as-is -- Sell
Target/Stop Loss/G-L Ratio still have no validated basis for this strategy (time-based exit only).
Final format: emoji (🟢 win rate >=75%, 🟡 60-74%, based on the ticker's OWN backtested history, not
analyst data) + company name + report date, Current Price, Buy Under (current price x 0.99, an
execution-buffer convention only, not a backtested level), Sell = day before report (date, not
price), and the ticker's own historical mean return + win rate (n=8 quarters). No Sell Target, Stop
Loss, or G/L Ratio fields -- explicitly omitted per the no-fabricated-numbers rule.

Sent manually to Discord in this format (MPWR, WDC, AMD, VRT, STX, EME, GLW, KLAC, VTRS, WBD, with
real company names via get_equity_fundamentals) and cron job recreated (old id 7e63e692 deleted,
new id `f94bef35`, same schedule: weekdays 7:33am CDT) with updated instructions to produce this
exact trimmed format going forward.

## Update 2026-07-07 (Discord truncation bug fixed; card format simplified to 4 lines)

User flagged the WBD card got cut off mid-line ("💰 Curre..."). Root cause:
`alerts/send_discord.py` silently truncated any message over Discord's 2000-character hard cap
(`message[:2000]`) instead of handling overflow -- with 10 full cards it exceeded the limit and cut
off mid-card. **Fixed**: `send_discord.py` now splits overlong messages on blank-line boundaries and
sends multiple Discord messages instead of truncating (unit-tested: a 4288-char message split
cleanly into 3 messages, all under 2000 chars, no mid-line cuts). This bug could have silently
affected the PEAD alert too on a high-signal day -- same fix applies there since both use this
shared script.

User also asked for an even simpler card (4 lines only: ticker+company, report date, current price,
buy under -- dropped the "Sell: day before X" line and the "Own history" backtest-stats line
entirely). Implemented exactly as requested. Cron job recreated again (old id `f94bef35` deleted,
new id `67aca2fd`, same schedule) with updated instructions for the 4-line format and a note that
the send script no longer needs manual length management.

## Update 2026-07-07 (real option prices added to pre-earnings cards)

User asked to add a suggested option buy price. Convention chosen (user-confirmed): a slightly OTM
call, expiration = nearest listed date on/after the ticker's earnings report, strike = nearest listed
strike to current_price * 1.075 (~5-10% OTM, matching the user's own real WFC trade pattern). Pulled
REAL live quotes via get_option_chains/get_option_instruments/get_option_quotes for all 10 tickers
(MPWR, WDC, AMD, VRT, STX, EME, GLW, KLAC, VTRS, WBD) -- not fabricated numbers. WBD's $28c (exp
Aug 7) showed bid_price=0/bid_size=0 despite a real ask -- flagged as illiquid in the card rather than
silently presented as a normal quote.

Important distinction stated in the alert: the backtest only validated the STOCK's return -- options
add leverage, theta decay, and IV risk that have never been tested for this strategy. This is
explicitly NOT a claim that the option pricing itself is validated, only that the quotes are real.

Cron job recreated again (old id `67aca2fd` deleted, new id `cc701e26`, same schedule: weekdays
7:33am CDT) with updated instructions covering the full option-selection procedure (chain lookup,
nearest-expiration-after-report selection, closest-to-1.075x-strike selection, illiquidity check) and
the exact 5-line card format (ticker+company, report date, current price, buy under, option line).

## Update 2026-07-07 (win rate >=75% filter + budget-aware option search; caught own filtering error)

User asked to raise the win-rate bar to >=75% (from >=60%) and only show options under $300/contract.
Updated `rank_pre_earnings_candidates.py`'s MIN_WIN_RATE to 0.75 (verified: 93 of 305 candidates now
pass, vs 174 at the old 60% bar).

For the $300 option budget: discovered most of the top-ranked (by win rate) candidates are
expensive/high-IV stocks (MPWR, STX, PWR, GLW, KLAC, NRG) where NO listed strike at the
nearest-expiration-after-report gets under $300 with real liquidity (bid>0) and a meaningful delta
(>=0.15) -- the only strikes that hit the price budget are either far enough OTM to have zero bid
(illiquid) or near-zero delta (effectively a lottery ticket, not a real expression of the thesis).
Rather than force a bad pick, these are now reported as "stock-only" with the reason (too expensive
even at the deepest strike, vs. only a low-quality strike meets budget).

Of the 10 win-rate>=75%/worst>=-20% candidates (MPWR, VRT, STX, GLW, KLAC, VTRS, WBD, PWR, APO, NRG),
only 2 have a real, liquid, meaningful-delta option under $300: VTRS ($18c exp Aug 21, ask $0.55,
delta 0.36) and APO ($140c exp Aug 7, ask $2.70, delta 0.16, thin liquidity).

**Self-caught error**: an earlier message in this session included KKR as a "good option pick" --
but KKR only passes the win-rate>=75% filter, NOT the worst>=-20% filter (a manual ad-hoc re-query
checked win rate only and skipped the second condition the actual script enforces). Caught this by
re-running the real script and diffing its output against what was sent. Corrected via a follow-up
Discord message removing KKR and substituting NRG (the ticker that actually replaces it in the
properly double-filtered top 10) -- NRG turned out to have no qualifying option either (same
illiquid/low-delta pattern as the other expensive names).

Cron job recreated again (old id `cc701e26` deleted, new id `bc2c803e`, same schedule) with updated
instructions: enforce win_rate>=75% via the script (not re-derived ad hoc), option search requires
ALL THREE of budget (<$300), real liquidity (bid>0), and delta>=0.15, searching progressively deeper
OTM strikes and explicitly falling back to "stock-only" with a stated reason when no strike
qualifies, rather than presenting a technically-in-budget but low-quality pick as if it were a real
trade idea.

## Update 2026-07-07 (bid/ask confusion resolved; both now shown explicitly)

User compared the VTRS $18c alert (ask $0.55) against Robinhood's own app and saw $0.40, suspecting
bad data. Verified no actual error: $0.40 was the app's "Sell" tab price (bid), which matched our own
bid_price ($0.40) exactly; the ask we quoted ($0.55) is on the app's "Buy" tab. Also, Robinhood's
"chance of profit" (75.97%) is a seller's-profit-probability metric, not the same thing as delta
(0.36) -- the user was comparing two different metrics. Separately, the stock price had genuinely
moved between when the quote was pulled and when the user checked ($16.97 -> $17.21), which is
normal live market movement, not an error.

User asked to show both bid and ask going forward to prevent this confusion. Cron job recreated
again (old id `bc2c803e` deleted, new id `bea429c1`, same schedule) with updated option-line format:
"Bid $X / Ask $Y (buy = ask, $Z/contract, delta D)" plus an explanation line in the caveat noting
Robinhood's app shows Bid by default on its Sell tab.

## Update 2026-07-08 (first automatic-format daily run)

Cron job `bea429c1` fired for 2026-07-08. Same top-10 as 2026-07-07 (earnings window barely shifted
one day): MPWR, VRT, STX, GLW, KLAC, VTRS, WBD, PWR, APO, NRG. Re-verified VTRS ($18c exp Aug 21) and
APO ($140c exp Aug 7) option quotes fresh -- both still pass budget/liquidity/delta bar (VTRS bid
$0.40/ask $0.55/delta 0.36; APO bid $0.05/ask $2.70/delta 0.16, thin). Remaining 7 stay stock-only.
Sent to Discord in the bid/ask-explicit format. No incidents.

## Update 2026-07-08 (PEAD daily check)

Ran the PEAD daily check for 2026-07-08. Entry window (last 4 calendar days): 16 candidates, none in
S&P 500 -- no entry signals. Exit window (~26-32 calendar days ago): 90+ tickers, 5 in S&P 500 (CASY,
SJM, ORCL, LEN, ADBE). Re-verified each historical gap day via evaluate.py pead-entry (not assumed):
only CASY (entered 2026-06-10, gap +5.19%) and SJM (entered 2026-06-09, gap +5.10%) actually
qualified as PEAD entries; ORCL (-10.73%), LEN (-1.85%), and ADBE (-7.50%) all gapped down and were
never real positions. Counted trading days elapsed via daily bars: CASY at 18/20, SJM at 19/20 --
neither due for exit yet (CASY ~2026-07-10, SJM ~2026-07-09). Sent to Discord with a "tracking 2 open
positions, not yet due" note so the pipeline's state stays visible even with no signal today. No
incidents.

## Update 2026-07-08 (out-of-sample backtest, 2022-2024: edge replicated)

Ran the out-of-sample test flagged as an open item since 2026-07-06: same PEAD strategy (>=5% earnings
gap, S&P 500, hold 20 trading days, 5bps/side slippage), tested on 2022-07-06 to 2024-07-06 -- a
period NOT used in the original validation (2024-2026, one continuous bull market). Robinhood's
`get_earnings_calendar` turned out to have data back to at least Jan 2022, making this possible. See
`backtest/report_pead_out_of_sample_2022_2024.md` for full detail; new cache files only
(`sp500_earnings_dates_2022_2024.json`, `{ticker}_2022_2024.csv`), nothing existing touched.

**Result: the edge replicated, and came in stronger than in-sample.** 500 trades, 260 tickers, win
rate 60.8%, mean +3.57%/trade, p<0.00001 (vs. in-sample: 615 trades, 55.8% win rate, +1.70%/trade,
p=0.00019). Split into sub-periods: even the weakest half (2022-07 to 2023-07, bear-market tail into
early recovery) stayed positive and significant (+1.69%/trade, 53.4% win rate, p=0.025) -- not just
non-negative, which is the pattern expected from a real regime-independent effect rather than a
bull-market artifact. Re-ran the script directly in this session (not just trusting the executing
subagent's summary) -- numbers matched exactly.

Caveats NOT resolved by this test: (1) survivorship bias -- used today's S&P 500 list against a
2022-2024 historical period; (2) this is now 2 of ~6-7 total strategy variants screened -- multiple-
comparisons risk reduced, not eliminated; (3) worst-case single-trade risk stayed large (-31% to
-38%), consistent with the earlier stop-loss finding -- position sizing still matters a lot.

## Update 2026-07-09 (code review fixes: git, tests, staleness, dedup, rate limits)

Full-project code review applied, all items user-approved ("knock out all"):
1. **git repo initialized** (biggest gap: no version control at all). Baseline commit = pre-review
   state; fixes committed on top as a reviewable diff. `.env` and `backtest/cache/` excluded via
   the existing .gitignore. NOT pushed to any remote yet — local only.
2. **strategies.py refactor**: new `qualifying_gap()` is the single source of gap math (gap_scan
   and earnings_gap_pead_entry both delegate); new `earnings_window_ok(days_since_earnings)`;
   `is_earnings_gap` param renamed `max_days_before` -> `max_days_after` (the old name said the
   opposite of what it measured). PEAD docstring updated to reflect the 2022-2024 out-of-sample
   validation (it still claimed OOS hadn't been done).
3. **live_scan/evaluate.py**: pead-entry no longer re-implements the gap math inline — calls
   qualifying_gap/earnings_window_ok. CLI args and output format byte-identical (verified), so the
   cron job prompts need no changes.
4. **rank_pre_earnings_candidates.py**: TEST_START/TEST_END no longer frozen at 2024-07-06/
   2026-07-06 — now a rolling 2y window ending today (was silently ossifying the daily alert's
   "own history" stats). Prints loud WARNINGs when sp500_earnings_dates_2y.json (>14d) or the price
   CSVs (>7d) go stale, with refresh instructions. Output now also prints a STATISTICAL CAVEAT
   (75% win-rate bar on n~8 = ~14% false-positive rate per no-edge ticker; pooled edge is the
   validated result, not per-ticker records) that the cron LLM is told to reflect in alerts.
   Verified: same top-10 as 2026-07-07/08 runs, so no behavior change today.
5. **alerts/send_discord.py**: handles Discord 429 rate limits (Retry-After honored, capped 30s,
   max 5 attempts/chunk, 0.5s between chunks). Previously a 429 mid-multi-chunk-send would crash
   and silently drop the rest of the alert.
6. **Tests + requirements.txt added**: tests/ (33 tests, all passing) covering gap math boundaries,
   earnings-window edges (day 0/3/4/negative), PEAD entry/exit, trend conditions, message
   chunking, and 429 retry logic (mocked, no network). requirements.txt pins pandas/numpy/scipy/
   yfinance/requests/pytest.
7. **cache/ vs results/ split**: one-off analysis outputs (pead_*_results.json,
   pre_earnings_*_results.json, full_condition_*, sample_hits, two_condition_hits, etc.) moved to
   backtest/results/ (git-tracked; they're the findings the reports cite). backtest/cache/ now
   holds only untracked input data. All 7 writer scripts updated; verify_pead_oos_2022_2024.py
   re-run end-to-end to confirm (numbers matched the documented result exactly). NOTE: older
   report/PROJECT.md entries still say "cache/<x>_results.json" — those are historical records,
   left as written; the files now live in results/.
8. **run_backtest.py**: comment added documenting the gap_scan simulation's known look-ahead
   (filters on full-day close/volume not knowable at the open) — moot while the strategy is dead,
   must be fixed before ever reviving it.

Not done (flagged, needs separate investigation): durable scheduling. Both daily jobs still live
in a local Claude Code session (die on close, 7-day expiry). Option B from 2026-07-07 (Discord MCP
connector attached to the cloud routine) checked against the MCP registry this session — see
conversation for outcome.
