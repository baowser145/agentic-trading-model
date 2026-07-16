# Paper bot watch (GitHub Pages)

Static dashboard for the agentic paper bot. Hosts on **GitHub Pages** as a demo UI.

## Enable GitHub Pages

1. Push this repo to GitHub (if it isn’t already).
2. Repo → **Settings** → **Pages**.
3. **Build and deployment** → Source: **Deploy from a branch**.
4. Branch: `master` (or `main`) → folder: **`/docs`** → **Save**.
5. After a minute, open:

   `https://<your-user>.github.io/<repo-name>/`

   Example: `https://vubl.github.io/agentic-trading-model/`

## Modes

| Mode | What you see |
|------|----------------|
| **Demo (default)** | Bundled `sample_snapshot.json` — fake/static paper state |
| **Live URL** | Polls any `…/api/status` JSON (same shape as `watch_snapshot.json`) |

### Live from your laptop (recommended)

GitHub Pages does **not** run the bot. Easiest live view is local:

```bash
# terminal A
python -m agentic_trading run-loop --quotes live --session-dir logs/paper_live --interval 60

# terminal B
python -m agentic_trading watch --session-dir logs/paper_live
# → http://127.0.0.1:8787/
```

### Live into the GitHub Pages page

Pages is **HTTPS**. Browsers block `http://127.0.0.1` from an HTTPS page (mixed content).

1. Run bot + watch as above.
2. Tunnel with HTTPS, e.g.:

   ```bash
   # install ngrok, then:
   ngrok http 8787
   ```

3. Copy the `https://….ngrok-free.app` (or `.ngrok-free.dev`) URL and open the Pages site.
4. Paste `https://….ngrok-free.app/api/status` into **Data source** → **Save & use**.

The Pages UI sends `ngrok-skip-browser-warning` so free-tier ngrok does not block the request.
If you still see **Failed to fetch**, confirm watch is on the same port ngrok forwards to
(e.g. `watch --port 8788` + `ngrok http 8788` — port **8787** may be taken by other apps).

Or use a query param:

`https://you.github.io/repo/?status=https://xxxx.ngrok-free.app/api/status`

## Security

- This UI is **read-only** (no order placement).
- Do **not** put secrets, account numbers, or API keys in `sample_snapshot.json` or public status JSON.
- Prefer local watch or a private tunnel; public status URLs expose paper portfolio state to anyone with the link.

## Preview locally (without GitHub)

```bash
cd docs
python3 -m http.server 8080
# open http://127.0.0.1:8080/
```
