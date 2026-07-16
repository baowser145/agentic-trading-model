"""
Minimal local watch UI for paper trading.

Serves logs/watch_snapshot.json (written each engine tick) on localhost.
No auth — bind to 127.0.0.1 only.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Paper bot watch</title>
  <style>
    :root {
      --bg: #0b1020;
      --card: #141b2d;
      --text: #e8eefc;
      --muted: #8b9bb8;
      --green: #3dd68c;
      --red: #ff6b6b;
      --accent: #6ea8fe;
      --border: #243049;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
      background: var(--bg); color: var(--text); line-height: 1.45;
    }
    header {
      padding: 1rem 1.25rem; border-bottom: 1px solid var(--border);
      display: flex; flex-wrap: wrap; gap: .75rem 1.5rem; align-items: baseline;
    }
    h1 { font-size: 1.15rem; margin: 0; font-weight: 650; }
    .muted { color: var(--muted); font-size: .9rem; }
    main { padding: 1rem 1.25rem; display: grid; gap: 1rem; }
    .grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
    .card {
      background: var(--card); border: 1px solid var(--border); border-radius: 12px;
      padding: 1rem;
    }
    .card h2 { margin: 0 0 .6rem; font-size: .85rem; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
    .big { font-size: 1.6rem; font-weight: 700; }
    .pos, .sig, .fill, .trade {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: .82rem; padding: .35rem 0; border-bottom: 1px solid var(--border);
    }
    .pos:last-child, .sig:last-child, .fill:last-child, .trade:last-child { border-bottom: 0; }
    .tag { display: inline-block; padding: .1rem .4rem; border-radius: 6px; font-size: .75rem; }
    .tag.enter { background: #1b3d2f; color: var(--green); }
    .tag.exit { background: #3d1b1b; color: var(--red); }
    .tag.hold { background: #243049; color: var(--muted); }
    .tag.flat { background: #1a2236; color: var(--muted); }
    .ok { color: var(--green); }
    .bad { color: var(--red); }
    .err { color: var(--red); padding: 1rem; }
    a { color: var(--accent); }
  </style>
</head>
<body>
  <header>
    <h1>Paper bot watch</h1>
    <span class="muted" id="meta">loading…</span>
    <span class="muted">poll <span id="poll">2s</span> · local only</span>
  </header>
  <main>
    <div id="error" class="err" hidden></div>
    <div class="grid">
      <div class="card"><h2>Equity</h2><div class="big" id="equity">—</div></div>
      <div class="card"><h2>Cash / BP</h2><div class="big" id="cash">—</div></div>
      <div class="card"><h2>Journal PnL</h2><div class="big" id="pnl">—</div></div>
      <div class="card"><h2>Win rate</h2><div class="big" id="wr">—</div></div>
    </div>
    <div class="card"><h2>Positions</h2><div id="positions" class="muted">none</div></div>
    <div class="card"><h2>Signals (last tick)</h2><div id="signals" class="muted">—</div></div>
    <div class="card"><h2>Fills (last tick)</h2><div id="fills" class="muted">—</div></div>
    <div class="card"><h2>Filter state (R1/R2)</h2><pre id="filter" class="muted" style="margin:0;white-space:pre-wrap;font-size:.8rem"></pre></div>
    <div class="card"><h2>Recent closed trades</h2><div id="recent" class="muted">—</div></div>
    <div class="card"><h2>Notes</h2><div id="notes" class="muted">—</div></div>
  </main>
  <script>
    const money = (n) => (n == null || Number.isNaN(n)) ? '—' :
      (n < 0 ? '-$' : '$') + Math.abs(n).toFixed(2);
    const pct = (n) => (n == null) ? '—' : (n * 100).toFixed(1) + '%';
    const tag = (a) => {
      const c = (a || '').includes('enter') ? 'enter'
        : (a || '').includes('exit') ? 'exit'
        : (a || '') === 'hold' ? 'hold' : 'flat';
      return `<span class="tag ${c}">${a || '?'}</span>`;
    };
    async function refresh() {
      try {
        const r = await fetch('/api/status?_=' + Date.now());
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const d = await r.json();
        document.getElementById('error').hidden = true;
        document.getElementById('meta').textContent =
          `${d.mode || '?'} · ${d.quote_provider || '?'} · ${d.ts || 'no snapshot yet'}`;
        document.getElementById('equity').textContent = money(d.equity);
        document.getElementById('cash').textContent =
          money(d.cash) + ' / ' + money(d.buying_power);
        const j = d.journal || {};
        const tp = j.total_pnl;
        const el = document.getElementById('pnl');
        el.textContent = money(tp);
        el.className = 'big ' + (tp > 0 ? 'ok' : tp < 0 ? 'bad' : '');
        document.getElementById('wr').textContent =
          pct(j.win_rate) + ` (${j.closed_trades ?? 0} closed)`;
        const pos = d.positions || {};
        const pkeys = Object.keys(pos);
        document.getElementById('positions').innerHTML = pkeys.length
          ? pkeys.map(s => {
              const p = pos[s];
              return `<div class="pos"><b>${s}</b> qty=${p.qty} avg=${p.avg_cost} mark=${p.mark ?? '—'}</div>`;
            }).join('')
          : '<span class="muted">flat</span>';
        const sigs = d.signals || [];
        document.getElementById('signals').innerHTML = sigs.length
          ? sigs.map(s => `<div class="sig">${tag(s.action)} <b>${s.symbol}</b> @ ${s.ref_price} — ${s.reason || ''}</div>`).join('')
          : '—';
        const fills = d.fills || [];
        document.getElementById('fills').innerHTML = fills.length
          ? fills.map(f => `<div class="fill">${f.side} <b>${f.symbol}</b> ${f.qty} @ ${f.price}</div>`).join('')
          : 'none this tick';
        document.getElementById('filter').textContent =
          JSON.stringify(d.filter_state || {}, null, 2);
        const recent = (j.recent || []).slice().reverse();
        document.getElementById('recent').innerHTML = recent.length
          ? recent.map(t => {
              const cls = t.pnl >= 0 ? 'ok' : 'bad';
              return `<div class="trade"><b>${t.symbol}</b> <span class="${cls}">${money(t.pnl)}</span> ${t.exit_reason || ''}</div>`;
            }).join('')
          : 'no closed trades yet';
        const notes = d.notes || [];
        document.getElementById('notes').innerHTML = notes.length
          ? notes.map(n => `<div class="sig">${n}</div>`).join('')
          : '—';
      } catch (e) {
        document.getElementById('error').hidden = false;
        document.getElementById('error').textContent =
          'Waiting for snapshot: run `python -m agentic_trading run-loop --quotes live` in another terminal. ' + e;
      }
    }
    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""


def make_handler(snapshot_path: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            # quieter: only errors
            if args and str(args[0]).startswith(("4", "5")):
                super().log_message(fmt, *args)

        def _cors(self) -> None:
            # Allow GitHub Pages (or any origin) to poll /api/status via HTTPS tunnel.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, DASHBOARD_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/api/status":
                if snapshot_path.is_file():
                    raw = snapshot_path.read_bytes()
                    self._send(200, raw, "application/json")
                else:
                    body = json.dumps(
                        {
                            "error": "no_snapshot",
                            "path": str(snapshot_path),
                            "hint": "Start paper loop: python -m agentic_trading run-loop --quotes live",
                        }
                    ).encode()
                    self._send(200, body, "application/json")
                return
            self._send(404, b'{"error":"not_found"}', "application/json")

    return Handler


def serve(snapshot_path: Path, host: str = "127.0.0.1", port: int = 8787) -> None:
    snapshot_path = Path(snapshot_path)
    httpd = ThreadingHTTPServer((host, port), make_handler(snapshot_path))
    print(
        json.dumps(
            {
                "event": "watch_server",
                "url": f"http://{host}:{port}/",
                "snapshot": str(snapshot_path),
                "bind": host,
                "note": "Local only. Open the URL while run-loop writes the snapshot.",
            },
            indent=2,
        ),
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nWatch server stopped.", flush=True)
    finally:
        httpd.server_close()
