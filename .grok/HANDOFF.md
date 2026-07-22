# Session Handoff — Agentic Trading Model — 2026-07-16T21:02:05Z

## One-Line Status
Live-quote paper loop + local watch + ngrok are **running**; GitHub Pages live-fetch fixed for free ngrok (`ddf0441`); multi-day paper sample still in progress (AAPL+MA open, few/no closes yet).

## Project Path
/Users/vubl/projects/agentic-trading-model

## Phase
**operate / improve** — paper watch UX + tunnel path unblocked; not unattended live thrash

## Roast Verdict
**Reshape** (user accepted) — see `.grok/roast-verdict.md`. Paper-first + risk rails; supervised Agentic live only.

## Decisions Made
- **Internal tool only**; Robinhood **Agentic only** (`agentic_allowed=true`, account `616665162`, nickname Agentic)
- Paper default: `trading_mode: paper`, `allow_live: false` (live via MCP + explicit user confirm)
- Options: long premium, **7–31 DTE**, max **1** open idea; playbook exits +50–100% / **−50%** / ≤3 DTE
- First live trade **BAC Jul 31 $62 call** closed stop @ **$0.78** → **−$11**; prefer not to use ≤10% option stops by default
- **Watch UX (this session):**
  - **Local watch is the primary path** — `http://127.0.0.1:8788/` (or whatever free port)
  - **Port 8787 is occupied** on this machine by Docker WordPress — do **not** use default 8787
  - Use **`watch --port 8788`** + **`ngrok http 8788`** (must forward to 8788, **not** 80)
  - Free ngrok interstitial blocks GitHub Pages `fetch` unless header `ngrok-skip-browser-warning: true` (fixed in Pages + CORS)
  - GitHub Pages hosts **UI only**; bot always runs on laptop
- **Live paper** isolated under `logs/paper_live` — do not mix with fixture `logs/trades.jsonl`

## What's Built
- Core package + supervised options path — **done** (prior)
- BAC postmortem + paper fixture autopsy + **R1/R2** — **done** (prior)
- Yahoo live paper quotes + session-dir — **done**
- Watch server + GitHub Pages dashboard — **done**
- **ngrok + Pages live feed fix** (`ddf0441`) — **done this session**
  - `docs/index.html`: send `ngrok-skip-browser-warning`
  - `watch_server.py`: CORS allow that header on preflight
- **logs/paper_live** live run (as of handoff):
  - equity ~**$1000.03**, cash/BP **$0** (fully invested paper)
  - open: **MA** + **AAPL** fractional lots
  - `quote_provider`: YahooLiveQuoteProvider
- Multi-day paper with closed-trade re-autopsy — **partial** (loop running; few/no closes yet)
- Options stop-sensitivity sim — **not built**
- Unattended live thrash / multi-leg — **out of scope**

## Verification Status
- Last formal `/verification` (MVP): **PASS** 2/2 — `.grok/build-log.md`
- Prior session: pytest **46 passed** after R1/R2 + live quote + watch
- This session: operational fix only (Pages/ngrok CORS); no full re-pytest required for handoff
- Git: `master` @ **`ddf0441`** = `origin/master`
  - Dirty (local only): `.grok/HANDOFF.md`, `.grok/PROJECT.md` (this handoff)
  - Untracked: `.grok/overnight-prompt.txt`
- Live RH Agentic: BAC closed earlier; re-check BP via `session-refresh` before any new option idea

## Active Goals
- none formal `/goal`
- Operational: keep multi-day live-quote paper running; later re-autopsy `logs/paper_live`

## Open Blockers
- **none critical** for paper path
- Free ngrok URL/hostname can change on restart unless reserved domain is used (`slapping-task-crawfish.ngrok-free.dev` was reserved/used)
- Anyone with the public ngrok `/api/status` URL can read paper portfolio while tunnel is up — stop tunnel when done
- Live options: **session-refresh** before proposing; BP may still be constrained by sell holds; max 1 open long-premium
- Never commit `.env` / secrets / ngrok authtoken

## Processes (user machine at handoff — may die when terminals close)
| Process | Notes |
|---------|--------|
| `run-loop --quotes live --session-dir logs/paper_live --interval 60` | PID ~20862 (s004) |
| `watch --session-dir logs/paper_live --port 8788` | PID ~21995 |
| `ngrok http --url=slapping-task-crawfish.ngrok-free.dev 8788` | PID ~21830 |
| Local UI | http://127.0.0.1:8788/ |
| Pages | https://baowser145.github.io/agentic-trading-model/ |
| Status URL for Pages | `https://slapping-task-crawfish.ngrok-free.dev/api/status` |

If processes are dead next session, restart with cheat sheet below (port **8788**, not 8787).

## Next 3 Actions (in order)
1. **Keep / restart multi-day live paper** (if processes died):
   ```bash
   cd ~/projects/agentic-trading-model && source .venv/bin/activate
   # Terminal A
   python -m agentic_trading run-loop --quotes live --session-dir logs/paper_live --interval 300 --until 2026-07-18
   # Terminal B
   python -m agentic_trading watch --session-dir logs/paper_live --port 8788
   # Optional Terminal C (only if GitHub Pages live feed wanted)
   ngrok http --url=slapping-task-crawfish.ngrok-free.dev 8788
   ```
   Prefer local **http://127.0.0.1:8788/** day-to-day; Pages needs hard-refresh after deploy.
2. **After ≥1–2 sessions with closed trades:** autopsy `logs/paper_live` and compare market-red churn vs fixture autopsy under R1/R2.
3. **Live options only if user asks + BP free:** `session-refresh` → propose only if free and no open long premium; **warn hard on ≤10% option stops**; place only on explicit confirm.

## Resume Prompt
Copy-paste this into a fresh session:

> Read `.grok/HANDOFF.md`, `.grok/PROJECT.md`, and `AGENTS.md` in `/Users/vubl/projects/agentic-trading-model`, then continue from "Next 3 Actions" item 1. Do not re-ask intake. Phase: operate / improve. **BAC call closed** −$11. Live paper: `run-loop --quotes live --session-dir logs/paper_live`. **Watch on port 8788** (8787 = Docker WordPress). Local: http://127.0.0.1:8788/. Pages: https://baowser145.github.io/agentic-trading-model/ with status `https://…ngrok…/api/status` only if ngrok → **8788** (not 80). Free ngrok needs `ngrok-skip-browser-warning` (shipped `ddf0441`). Agentic only; paper default; no auto-place options. Venv: `source .venv/bin/activate`. Git HEAD `ddf0441` on origin/master.

## Files Touched This Session
- `docs/index.html` — ngrok-skip header + HTML-vs-JSON guard
- `docs/README.md` — port/ngrok notes
- `src/agentic_trading/watch_server.py` — CORS allow `ngrok-skip-browser-warning`
- `.grok/HANDOFF.md`, `.grok/PROJECT.md` — this handoff
- Prior commits still relevant: `b36ba4a` (watch UI + R1/R2), `ddf0441` (ngrok Pages fix)

## How to run (cheat sheet)
```bash
cd ~/projects/agentic-trading-model && source .venv/bin/activate

# Live-quote paper
python -m agentic_trading run-loop --quotes live --session-dir logs/paper_live --interval 300 --until 2026-07-18
python -m agentic_trading watch --session-dir logs/paper_live --port 8788   # NOT 8787 on this Mac
# → http://127.0.0.1:8788/

# Optional public tunnel for Pages (must match watch port)
ngrok http --url=slapping-task-crawfish.ngrok-free.dev 8788
# Pages data source: https://slapping-task-crawfish.ngrok-free.dev/api/status

# Fixture paper (tests / old path)
python -m agentic_trading run-once
python -m agentic_trading trades

# Live Agentic (supervised)
python -m agentic_trading session-refresh
# propose / pick / prepare / review / place only after explicit yes
```
