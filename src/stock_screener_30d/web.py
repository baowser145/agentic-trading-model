from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from stock_screener_30d.activity_log import get_logs, log as act_log
from stock_screener_30d.backtest_cache import load_cached_backtest, run_and_cache_backtest
from stock_screener_30d.config import get_universe, load_config
from stock_screener_30d.paper_log import (
    append_scan,
    load_log,
    open_positions_status,
    performance_report,
    update_closed_trades,
)
from stock_screener_30d.comparison import paper_vs_backtest
from stock_screener_30d.scan_cache import get_scan, set_scan
from stock_screener_30d.screener import run_scan
from stock_screener_30d.targets import enrich_with_targets

app = FastAPI(title="Stock Screener 30d", version="0.1.0")

DISCLAIMER = (
    "Not financial advice. For educational and paper-trading purposes only. "
    "Past backtest performance does not guarantee future results."
)


def _cfg():
    return load_config()


def _run_scan_pipeline(top_n: int | None = None) -> dict:
    cfg = _cfg()
    if top_n:
        cfg["output"]["top_n"] = top_n
    universe = get_universe(cfg)
    act_log(f"Scan started — {len(universe)} tickers in universe", source="scan")
    df = run_scan(cfg)
    if df.empty:
        act_log("Scan complete — no picks matched criteria", level="warn", source="scan")
        set_scan([], 0)
        return {"picks": [], "count": 0, "disclaimer": DISCLAIMER}
    hold = cfg.get("backtest", {}).get("hold_days", 30)
    df = enrich_with_targets(df, hold_days=hold)
    picks = df.to_dict(orient="records")
    tickers = ", ".join(p["ticker"] for p in picks)
    act_log(f"Scan complete — {len(picks)} picks: {tickers}", level="success", source="scan")
    set_scan(picks, len(picks))
    return {"picks": picks, "count": len(picks), "disclaimer": DISCLAIMER}


@app.get("/api/health")
def health():
    return {"status": "ok", "disclaimer": DISCLAIMER}


@app.get("/api/logs")
def api_logs(since: int = 0):
    return {"logs": get_logs(since)}


@app.get("/api/scan")
def api_scan_run(top_n: int | None = None):
    return _run_scan_pipeline(top_n)


@app.get("/api/scan/latest")
def api_scan_latest():
    cached = get_scan()
    if cached is None:
        return {"picks": [], "count": 0, "cached": False, "disclaimer": DISCLAIMER}
    return {**cached, "cached": True, "disclaimer": DISCLAIMER}


@app.get("/api/backtest")
def api_backtest_cached():
    cached = load_cached_backtest()
    if cached is None:
        return {"cached": False, "message": "No backtest cached yet. Click Run Backtest.", "disclaimer": DISCLAIMER}
    return {"cached": True, **cached, "disclaimer": DISCLAIMER}


@app.post("/api/backtest")
def api_backtest_run():
    result = run_and_cache_backtest()
    if "error" in result:
        return {"cached": False, **result, "disclaimer": DISCLAIMER}
    return {"cached": True, **result, "disclaimer": DISCLAIMER}


@app.post("/api/log")
def api_log(update: bool = False):
    cfg = _cfg()
    if update:
        act_log("Closing matured paper trades…", source="paper")
        closed = update_closed_trades(cfg)
        if closed:
            act_log(f"Closed {closed} trade(s)", level="success", source="paper")
    else:
        closed = 0
    act_log("Logging today's scan picks…", source="paper")
    result = append_scan(cfg)
    act_log(
        f"Logged {result['appended']} new trade(s), skipped {result['skipped']} duplicate(s)",
        level="success",
        source="paper",
    )
    if result.get("new_tickers"):
        act_log(f"NEW: {', '.join(result['new_tickers'])}", source="paper")
    if result.get("dropped_tickers"):
        act_log(f"DROPPED: {', '.join(result['dropped_tickers'])}", source="paper")
    return {"closed": closed, **result}


@app.get("/api/positions")
def api_positions():
    df = open_positions_status()
    if df.empty:
        return {"positions": []}
    return {"positions": df.to_dict(orient="records")}


@app.get("/api/report")
def api_report():
    return performance_report()


@app.get("/api/comparison")
def api_comparison():
    return paper_vs_backtest()


@app.get("/api/trades")
def api_trades():
    df = load_log()
    if df.empty:
        return {"trades": []}
    return {"trades": df.to_dict(orient="records")}


@app.on_event("startup")
def startup():
    act_log("Dashboard started — ready", source="system")
    cached = load_cached_backtest()
    if cached and "error" not in cached:
        act_log(
            f"Loaded cached backtest: {cached.get('strategy_annualized_pct', '?')}% vs SPY "
            f"{cached.get('benchmark_annualized_pct', '?')}%",
            source="system",
        )


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stock Screener 30d</title>
  <style>
    :root { --bg:#0b0f14; --card:#151d2b; --text:#e8edf4; --muted:#8b9cb3; --accent:#3b82f6; --green:#22c55e; --red:#ef4444; --warn:#f59e0b; --border:#243044; --logbg:#0d1117; }
    * { box-sizing:border-box; }
    html, body { height:100%; margin:0; }
    body { font-family: system-ui, sans-serif; background:var(--bg); color:var(--text); }
    .page { display:grid; grid-template-columns:minmax(0,1fr) 360px; min-height:100vh; }
    .main { padding:1.25rem; overflow-x:auto; }
    .sidebar { background:var(--logbg); border-left:1px solid var(--border); display:flex; flex-direction:column; height:100vh; position:sticky; top:0; }
    h1 { font-size:1.5rem; margin:0 0 .2rem; }
    .sub { color:var(--muted); margin-bottom:1rem; font-size:.88rem; }
    .disclaimer { background:#2a1f0a; border:1px solid #854d0e; color:#fcd34d; padding:.65rem .85rem; border-radius:8px; font-size:.78rem; margin-bottom:1rem; line-height:1.4; }
    .backtest-banner { background:linear-gradient(135deg,#1e3a5f,#151d2b); border:1px solid var(--border); border-radius:10px; padding:1rem; margin-bottom:1rem; display:flex; flex-wrap:wrap; gap:1.2rem; align-items:center; }
    .backtest-banner .label { font-size:.68rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
    .backtest-banner .val { font-size:1.25rem; font-weight:700; }
    .backtest-banner .meta { font-size:.72rem; color:var(--muted); }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:.65rem; margin-bottom:1rem; }
    .card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:.85rem; }
    .card h2 { font-size:.7rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:0 0 .45rem; }
    .stat { font-size:1.25rem; font-weight:600; }
    button { background:var(--accent); color:#fff; border:0; padding:.45rem .85rem; border-radius:6px; cursor:pointer; margin-right:.35rem; margin-bottom:.35rem; font-size:.82rem; }
    button.secondary { background:#334155; }
    button:hover { filter:brightness(1.08); }
    button:disabled { opacity:.5; cursor:wait; }
    table { width:100%; border-collapse:collapse; font-size:.78rem; }
    th, td { text-align:left; padding:.35rem .25rem; border-bottom:1px solid var(--border); }
    th { color:var(--muted); font-weight:500; font-size:.68rem; text-transform:uppercase; }
    .pos { color:var(--green); } .neg { color:var(--red); }
    .muted { color:var(--muted); font-size:.72rem; }
    #status { color:var(--muted); font-size:.8rem; min-height:1.1rem; margin:.4rem 0; }
    .legend { font-size:.72rem; color:var(--muted); margin-top:.45rem; line-height:1.45; }
    .badge { display:inline-block; padding:.12rem .4rem; border-radius:4px; font-size:.68rem; font-weight:600; }
    .badge.win { background:#14532d; color:#86efac; }
    .badge.lose { background:#450a0a; color:#fca5a5; }
    .compare-panel { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:1rem; margin-bottom:1rem; }
    .compare-panel h2 { font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:0 0 .75rem; }
    .compare-grid { display:grid; grid-template-columns:1fr 1fr; gap:1rem; }
    .compare-col { background:#111827; border-radius:8px; padding:.85rem; }
    .compare-col h3 { font-size:.78rem; margin:0 0 .6rem; color:var(--text); }
    .compare-col .period { font-size:.7rem; color:var(--muted); margin-bottom:.5rem; }
    .compare-row { display:flex; justify-content:space-between; padding:.3rem 0; font-size:.8rem; border-bottom:1px solid #1f2937; }
    .compare-row:last-child { border:0; }
    .compare-row .k { color:var(--muted); }
    .verdict-box { margin-top:.85rem; padding:.7rem .85rem; border-radius:8px; font-size:.8rem; line-height:1.45; }
    .verdict-box.good { background:#14532d33; border:1px solid #166534; }
    .verdict-box.warn { background:#78350f33; border:1px solid #92400e; }
    .verdict-box.bad { background:#450a0a33; border:1px solid #991b1b; }
    .verdict-box.collecting { background:#1e3a5f33; border:1px solid #1d4ed8; }
    .verdict-label { font-weight:700; font-size:.72rem; letter-spacing:.05em; margin-bottom:.25rem; }
    @media (max-width: 700px) { .compare-grid { grid-template-columns:1fr; } }
    .log-header { padding:.85rem 1rem; border-bottom:1px solid var(--border); font-size:.72rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); display:flex; justify-content:space-between; align-items:center; }
    .log-header span { color:var(--accent); font-weight:600; }
    #log-panel { flex:1; overflow-y:auto; padding:.65rem .85rem; font-family:ui-monospace, monospace; font-size:.72rem; line-height:1.55; }
    .log-line { padding:.15rem 0; border-bottom:1px solid #1a2332; }
    .log-line .ts { color:#4b5563; margin-right:.4rem; }
    .log-line .src { color:#6366f1; margin-right:.35rem; }
    .log-line.info .msg { color:#94a3b8; }
    .log-line.success .msg { color:var(--green); }
    .log-line.warn .msg { color:var(--warn); }
    .log-line.error .msg { color:var(--red); }
    .log-pulse { width:8px; height:8px; border-radius:50%; background:var(--green); display:inline-block; animation:pulse 1.5s infinite; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
    @media (max-width: 960px) {
      .page { grid-template-columns:1fr; }
      .sidebar { height:280px; position:relative; border-left:0; border-top:1px solid var(--border); }
    }
  </style>
</head>
<body>
<div class="page">
  <div class="main">
    <h1>Stock Screener 30d</h1>
    <p class="sub">30-day swing holds · paper trading dashboard</p>
    <div class="disclaimer">⚠️ Not financial advice. For educational and paper-trading purposes only. Past backtest performance does not guarantee future results.</div>

    <div class="backtest-banner" id="backtest-banner">
      <div><div class="label">Backend Validation</div><div class="val" id="bt-status">Loading…</div><div class="meta" id="bt-meta"></div></div>
      <div><div class="label">Strategy (ann.)</div><div class="val" id="bt-strategy">—</div></div>
      <div><div class="label">SPY Benchmark</div><div class="val" id="bt-bench">—</div></div>
      <div><div class="label">Excess Return</div><div class="val" id="bt-excess">—</div></div>
      <div style="margin-left:auto"><button class="secondary" id="bt-btn" onclick="runBacktest()">Run Backtest</button></div>
    </div>

    <div class="compare-panel" id="compare-panel">
      <h2>Paper vs Backtest — Are You On Track?</h2>
      <div class="compare-grid" id="compare-grid">Loading comparison…</div>
      <div class="verdict-box collecting" id="verdict-box"><div class="verdict-label" id="verdict-label">—</div><div id="verdict-msg"></div></div>
    </div>

    <div>
      <button onclick="runScan()">Run Scan</button>
      <button onclick="runLog(false)">Log Today</button>
      <button onclick="runLog(true)">Update &amp; Log</button>
      <button class="secondary" onclick="refresh()">Refresh</button>
    </div>
    <p id="status"></p>
    <div class="grid" id="stats"></div>

    <div class="card">
      <h2>Latest Scan — Entry &amp; Exit Targets</h2>
      <div id="scan-table">Loading…</div>
      <p class="legend"><b>Time exit:</b> sell at close on ExitDate.<br><b>Stop exit:</b> 50-day SMA.<br><b>Target exit:</b> 52-week high stretch target.</p>
    </div>
    <div class="card" style="margin-top:.85rem">
      <h2>Open Positions</h2>
      <div id="positions-table">Loading…</div>
    </div>
  </div>

  <aside class="sidebar">
    <div class="log-header">
      <div>Backend Activity <span class="log-pulse" id="log-pulse"></span></div>
      <div id="log-count">0 events</div>
    </div>
    <div id="log-panel"><div class="log-line info"><span class="msg">Waiting for backend events…</span></div></div>
  </aside>
</div>

<script>
  const $ = id => document.getElementById(id);
  const fmt = (n,d=2) => n==null?'—':Number(n).toFixed(d);
  const pct = n => n==null?'—':(n>=0?'+':'')+Number(n).toFixed(2)+'%';
  const money = n => n==null?'—':'$'+Number(n).toFixed(2);
  let lastLogId = 0;
  let logPollTimer = null;

  function appendLogs(logs) {
    if (!logs.length) return;
    const panel = $('log-panel');
    if (panel.querySelector('.msg')?.textContent === 'Waiting for backend events…') panel.innerHTML = '';
    logs.forEach(e => {
      const div = document.createElement('div');
      div.className = 'log-line ' + (e.level||'info');
      div.innerHTML = `<span class="ts">${e.ts}</span><span class="src">[${e.source}]</span><span class="msg">${e.message}</span>`;
      panel.appendChild(div);
    });
    panel.scrollTop = panel.scrollHeight;
    lastLogId = logs[logs.length-1].id;
    $('log-count').textContent = panel.querySelectorAll('.log-line').length + ' events';
  }

  async function pollLogs() {
    try {
      const r = await fetch('/api/logs?since='+lastLogId).then(x=>x.json());
      appendLogs(r.logs||[]);
    } catch(e) {}
  }

  function startLogPolling(interval=800) {
    if (logPollTimer) clearInterval(logPollTimer);
    logPollTimer = setInterval(pollLogs, interval);
  }

  async function refresh() {
    const [scan, pos, report, bt, cmp] = await Promise.all([
      fetch('/api/scan/latest').then(r=>r.json()),
      fetch('/api/positions').then(r=>r.json()),
      fetch('/api/report').then(r=>r.json()),
      fetch('/api/backtest').then(r=>r.json()),
      fetch('/api/comparison').then(r=>r.json()),
    ]);
    renderBacktest(bt);
    renderComparison(cmp);
    renderStats(report);
    renderScan(scan.picks||[]);
    renderPositions(pos.positions||[]);
    await pollLogs();
  }

  function colHtml(side, isPaper) {
    const avg = side.avg_return_pct;
    const avgCls = avg==null?'':(avg>=0?'pos':'neg');
    const ann = side.annualized_pct;
    const spy = side.vs_spy_pct;
    return `<div class="compare-col">
      <h3>${side.label}</h3>
      <div class="period">${side.period||'—'} · ${side.trades_total??0} trades</div>
      <div class="compare-row"><span class="k">Avg return</span><span class="${avgCls}">${avg==null?'—':pct(avg)}</span></div>
      <div class="compare-row"><span class="k">Win rate</span><span>${side.win_rate_pct!=null?side.win_rate_pct+'%':'—'}</span></div>
      <div class="compare-row"><span class="k">Annualized</span><span>${ann!=null?pct(ann):'—'}</span></div>
      <div class="compare-row"><span class="k">${isPaper?'SPY same window':'SPY (ann.)'}</span><span>${spy!=null?pct(spy):'—'}</span></div>
      <div class="period" style="margin-top:.5rem">${side.note||''}</div>
    </div>`;
  }

  function renderComparison(cmp) {
    $('compare-grid').innerHTML = colHtml(cmp.paper, true) + colHtml(cmp.backtest, false);
    const v = cmp.verdict||{};
    const box = $('verdict-box');
    box.className = 'verdict-box ' + (v.status||'collecting');
    $('verdict-label').textContent = v.label||'—';
    let msg = v.message||'';
    if (cmp.gap_pct!=null) msg += ` Gap vs backtest: ${cmp.gap_pct>=0?'+':''}${cmp.gap_pct}% per trade.`;
    $('verdict-msg').textContent = msg;
  }

  function renderBacktest(bt) {
    if (!bt.cached || bt.error) {
      $('bt-status').innerHTML = bt.message || bt.error || 'No backtest yet';
      $('bt-strategy').textContent = $('bt-bench').textContent = $('bt-excess').textContent = '—';
      $('bt-meta').textContent = 'Click Run Backtest — watch logs →';
      return;
    }
    $('bt-status').innerHTML = bt.beats_benchmark ? '<span class="badge win">BEATS SPY</span>' : '<span class="badge lose">UNDERPERFORMS</span>';
    $('bt-strategy').innerHTML = `<span class="${bt.strategy_annualized_pct>=0?'pos':'neg'}">${pct(bt.strategy_annualized_pct)}</span>`;
    $('bt-bench').textContent = pct(bt.benchmark_annualized_pct);
    $('bt-excess').innerHTML = `<span class="${bt.excess_annualized_pct>=0?'pos':'neg'}">${pct(bt.excess_annualized_pct)}</span>`;
    $('bt-meta').textContent = `${bt.period||'2019-2024'} · ${bt.num_rebalances||'?'} rebalances · cached ${bt.cached_at||''}`;
  }

  function renderStats(r) {
    $('stats').innerHTML = `
      <div class="card"><h2>Paper Trades</h2><div class="stat">${r.total_trades??0}</div></div>
      <div class="card"><h2>Open</h2><div class="stat">${r.open??0}</div></div>
      <div class="card"><h2>Closed</h2><div class="stat">${r.closed??0}</div></div>
      <div class="card"><h2>Win Rate</h2><div class="stat">${r.win_rate_pct!=null?r.win_rate_pct+'%':'—'}</div></div>
      <div class="card"><h2>Avg Return</h2><div class="stat ${(r.avg_return_net_pct??0)>=0?'pos':'neg'}">${r.avg_return_net_pct!=null?pct(r.avg_return_net_pct):'—'}</div></div>`;
  }

  function renderScan(picks) {
    if (!picks.length) { $('scan-table').textContent = 'No picks — click Run Scan.'; return; }
    $('scan-table').innerHTML = `<table><tr><th>Ticker</th><th>Entry</th><th>Time Exit</th><th>Stop Exit</th><th>Target Exit</th><th>Upside</th><th>Risk</th><th>Score</th></tr>
      ${picks.map(p=>`<tr><td><b>${p.ticker}</b><br><span class="muted">RSI ${fmt(p.rsi,1)}</span></td>
        <td>${money(p.entry_price)}</td><td>${p.exit_date||'—'}<br><span class="muted">@ close</span></td>
        <td class="neg">${money(p.exit_stop_price)}</td><td class="pos">${money(p.exit_target_price)}</td>
        <td class="pos">${pct(p.exit_target_pct)}</td><td>${fmt(p.risk_pct,1)}%</td><td>${fmt(p.score,3)}</td></tr>`).join('')}</table>`;
  }

  function renderPositions(pos) {
    if (!pos.length) { $('positions-table').textContent = 'No open paper trades.'; return; }
    $('positions-table').innerHTML = `<table><tr><th>Ticker</th><th>Entry</th><th>Current</th><th>P&amp;L</th><th>Time Exit</th><th>Stop</th><th>Days</th></tr>
      ${pos.map(p=>`<tr><td><b>${p.ticker}</b></td><td>${money(p.entry_price)}</td><td>${money(p.current_price)}</td>
        <td class="${(p.unrealized_pct??0)>=0?'pos':'neg'}">${pct(p.unrealized_pct)}</td>
        <td>${p.exit_date}</td><td class="neg">${money(p.stop_loss)}</td><td>${p.days_left}d</td></tr>`).join('')}</table>`;
  }

  async function runScan() {
    $('status').textContent = 'Running scan…';
    startLogPolling(400);
    await fetch('/api/scan');
    await refresh();
    $('status').textContent = 'Scan complete.';
    startLogPolling(2000);
  }

  async function runLog(update) {
    $('status').textContent = update ? 'Updating & logging…' : 'Logging…';
    startLogPolling(400);
    const r = await fetch('/api/log?update='+update, {method:'POST'}).then(x=>x.json());
    await refresh();
    $('status').textContent = `Logged ${r.appended} trade(s). Closed ${r.closed||0}.`;
    startLogPolling(2000);
  }

  async function runBacktest() {
    const btn = $('bt-btn');
    btn.disabled = true;
    $('status').textContent = 'Running backtest — watch activity log →';
    $('bt-status').textContent = 'Running…';
    startLogPolling(400);
    try {
      const bt = await fetch('/api/backtest', {method:'POST'}).then(r=>r.json());
      renderBacktest(bt);
      $('status').textContent = bt.error ? 'Backtest failed: '+bt.error : 'Backtest complete.';
    } finally {
      btn.disabled = false;
      startLogPolling(2000);
    }
    await pollLogs();
  }

  pollLogs();
  startLogPolling(2000);
  refresh();
  setInterval(refresh, 5*60*1000);
</script>
</body>
</html>"""