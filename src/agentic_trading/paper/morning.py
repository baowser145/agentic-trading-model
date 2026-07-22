"""
Morning paper routine (learned from live 2026-07-17):

1. Assess broad market (SPY/QQQ day change) → call | put | hold bias
2. Scan liquid universe / research for focus names
3. Watch N paper ticks (manage only if open; entries gated by bias)
4. Trigger: allow paper entries only when bias supports the playbook

Long equity playbook = bullish proxy. On **put** or **hold** bias we do not
open new longs (put day = wait / no spam). Settlement: use settled BP only.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agentic_trading.agent.research import (
    ResearchReport,
    heuristic_research,
    llm_research,
    write_daily_focus,
)
from agentic_trading.config import AppConfig
from agentic_trading.engine import Engine, build_engine
from agentic_trading.market.quotes import QuoteProvider, build_quote_provider

ET = ZoneInfo("America/New_York")

# Day-change thresholds for bias (percent)
GREEN_PCT = 0.15  # SPY or QQQ up this much → lean call
RED_PCT = -0.15  # both (or average) red → lean put


@dataclass
class MarketAssess:
    ts: str
    spy_last: float | None
    spy_prev: float | None
    spy_chg_pct: float | None
    qqq_last: float | None
    qqq_prev: float | None
    qqq_chg_pct: float | None
    aapl_last: float | None = None
    aapl_chg_pct: float | None = None
    bias: str = "hold"  # call | put | hold
    reason: str = ""
    quote_source: str = "fixture"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MorningResult:
    ts: str
    assess: MarketAssess
    report: ResearchReport | None
    daily_picks: list[str]
    watch_ticks: list[dict[str, Any]] = field(default_factory=list)
    trigger_ticks: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "assess": self.assess.to_dict(),
            "daily_picks": self.daily_picks,
            "market_view": self.report.market_view if self.report else None,
            "research_mode": self.report.mode if self.report else None,
            "watch_ticks": self.watch_ticks,
            "trigger_ticks": self.trigger_ticks,
            "notes": self.notes,
            "paths": self.paths,
        }


def _chg_pct(last: float | None, prev: float | None) -> float | None:
    if last is None or prev is None or prev <= 0:
        return None
    return (last / prev - 1.0) * 100.0


def _yahoo_last_prev(sym: str) -> tuple[float | None, float | None]:
    """Last trade + official prior session close via yfinance (best effort)."""
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return None, None
    try:
        t = yf.Ticker(sym)
        # fast_info often has lastPrice / previousClose
        fi = getattr(t, "fast_info", None) or {}
        last = None
        prev = None
        if isinstance(fi, dict):
            last = fi.get("lastPrice") or fi.get("last_price") or fi.get("regularMarketPrice")
            prev = fi.get("previousClose") or fi.get("previous_close")
        else:
            last = getattr(fi, "last_price", None) or getattr(fi, "lastPrice", None)
            prev = getattr(fi, "previous_close", None) or getattr(fi, "previousClose", None)
        if last is not None:
            last = float(last)
        if prev is not None:
            prev = float(prev)
        if last and prev:
            return last, prev
        hist = t.history(period="5d", interval="1d", auto_adjust=True)
        if hist is not None and len(hist) >= 2 and "Close" in hist.columns:
            closes = [float(x) for x in hist["Close"].dropna().tolist()]
            if len(closes) >= 2:
                return closes[-1], closes[-2]
    except Exception:
        return None, None
    return last if last else None, prev if prev else None


def assess_market(
    quotes: QuoteProvider,
    *,
    quote_source: str = "yahoo",
) -> MarketAssess:
    """
    Bias from live (or fixture) levels vs **prior session close**.

    Yahoo/live: prefer yfinance previousClose vs last.
    Fixture: short lookback first/last as proxy.
    """
    symbols = ["SPY", "QQQ", "AAPL"]
    qmap = quotes.get_quotes(symbols)
    hist = quotes.get_history(symbols, lookback=20)
    use_yahoo_prev = quote_source.strip().lower() in ("yahoo", "live", "yfinance")

    def last_prev(sym: str) -> tuple[float | None, float | None]:
        if use_yahoo_prev:
            yl, yp = _yahoo_last_prev(sym)
            if yl and yp:
                return yl, yp
        q = qmap.get(sym)
        last = float(q.price) if q and q.price > 0 else None
        bars = hist.get(sym) or []
        closes = [float(b.close) for b in bars if b.close and b.close > 0]
        prev: float | None = None
        if len(closes) >= 2:
            # Intraday bars: use session open-ish proxy (first of window)
            prev = closes[0]
            if last is None:
                last = closes[-1]
        elif len(closes) == 1 and last is None:
            last = closes[0]
        return last, prev

    spy_l, spy_p = last_prev("SPY")
    qqq_l, qqq_p = last_prev("QQQ")
    aapl_l, aapl_p = last_prev("AAPL")
    spy_c = _chg_pct(spy_l, spy_p)
    qqq_c = _chg_pct(qqq_l, qqq_p)
    aapl_c = _chg_pct(aapl_l, aapl_p)

    # Combine: average of available index changes
    chgs = [c for c in (spy_c, qqq_c) if c is not None]
    avg = sum(chgs) / len(chgs) if chgs else 0.0

    if avg <= RED_PCT:
        bias = "put"
        reason = (
            f"Risk-off: avg SPY/QQQ day chg {avg:+.2f}% "
            f"(SPY {spy_c:+.2f}% / QQQ {qqq_c:+.2f}%) — lean put / no new longs"
            if spy_c is not None and qqq_c is not None
            else f"Risk-off proxy avg {avg:+.2f}% — lean put"
        )
    elif avg >= GREEN_PCT:
        bias = "call"
        reason = (
            f"Risk-on: avg SPY/QQQ day chg {avg:+.2f}% "
            f"(SPY {spy_c:+.2f}% / QQQ {qqq_c:+.2f}%) — lean call / long equity ok"
            if spy_c is not None and qqq_c is not None
            else f"Risk-on proxy avg {avg:+.2f}% — lean call"
        )
    else:
        bias = "hold"
        reason = (
            f"Mixed/flat: avg SPY/QQQ {avg:+.2f}% within "
            f"[{RED_PCT}, {GREEN_PCT}] — hold, no new risk"
        )

    return MarketAssess(
        ts=datetime.now(timezone.utc).isoformat(),
        spy_last=spy_l,
        spy_prev=spy_p,
        spy_chg_pct=round(spy_c, 4) if spy_c is not None else None,
        qqq_last=qqq_l,
        qqq_prev=qqq_p,
        qqq_chg_pct=round(qqq_c, 4) if qqq_c is not None else None,
        aapl_last=aapl_l,
        aapl_chg_pct=round(aapl_c, 4) if aapl_c is not None else None,
        bias=bias,
        reason=reason,
        quote_source=quote_source,
    )


def _tick_summary(result) -> dict[str, Any]:
    p = result.portfolio
    settled = (
        p.settled_cash if p.settled_cash is not None else p.cash - p.unsettled_cash
    )
    return {
        "ts": result.ts.isoformat(),
        "fills": len(result.fills),
        "equity": round(p.equity, 4),
        "cash_total": round(p.cash, 4),
        "settled_cash": round(float(settled or 0), 4),
        "unsettled_cash": round(p.unsettled_cash, 4),
        "buying_power": round(p.buying_power, 4),
        "halted": p.halted,
        "signals": [f"{s.symbol}:{s.action.value}" for s in result.signals],
        "notes": list(result.notes or [])[:8],
    }


def run_morning_paper(
    config: AppConfig,
    *,
    quote_source: str = "yahoo",
    use_llm: bool = False,
    watch_ticks: int = 2,
    trigger: bool = True,
    daily_n: int = 3,
    session_dir: Path | None = None,
    out_dir: Path | None = None,
) -> MorningResult:
    """
    Full morning pipeline for paper.

    - assess market → bias
    - research scan → daily_focus with market_bias
    - watch_ticks of engine (bias gates entries)
    - if trigger and bias==call: extra trigger tick(s)
    """
    notes: list[str] = []
    quotes = build_quote_provider(quote_source)
    assess = assess_market(quotes, quote_source=quote_source)
    notes.append(f"bias={assess.bias}: {assess.reason}")

    # Settlement reminder
    if config.trade_when_cash_available:
        notes.append(
            "WARNING: trade_when_cash_available=true — paper can redeploy unsettled "
            "cash immediately (unlike RH BP lag 2026-07-17). Prefer false."
        )
    else:
        notes.append(
            "Settlement: strict settled-only BP (models cash≠BP lag after sells)."
        )

    # Scan with same quotes; daily_picks depend on bias:
    #   call → strongest RS | put → weakest liquid RS | hold → empty
    if use_llm:
        report = llm_research(
            config,
            daily_n=daily_n,
            expand=True,
            quote_provider=quotes,
            market_bias=assess.bias,
        )
    else:
        report = heuristic_research(
            config,
            daily_n=daily_n,
            expand=True,
            quote_provider=quotes,
            market_bias=assess.bias,
        )

    focus_path = config.daily_focus.path or (
        config.config_path.parent / "logs" / "daily_focus.json"
    )
    write_daily_focus(
        report,
        focus_path,
        daily_n=daily_n,
        market_bias=assess.bias,
        market_assess=assess.to_dict(),
    )
    rank_mode = {
        "call": "strongest_rs_long",
        "put": "weakest_liquid_put_watch",
        "hold": "none",
    }.get(assess.bias, "strongest_rs_long")
    notes.append(
        f"daily_focus written: {focus_path} picks={report.daily_picks} "
        f"rank={rank_mode}"
    )

    # Isolated paper session optional
    from dataclasses import replace

    engine_config = config
    if session_dir is not None:
        d = Path(session_dir)
        if not d.is_absolute():
            d = (config.config_path.parent / d).resolve()
        d.mkdir(parents=True, exist_ok=True)
        engine_config = replace(
            config,
            log_path=d / "decisions.jsonl",
            paper_state_path=d / "paper_state.json",
            daily_focus=replace(config.daily_focus, path=focus_path),
        )

    watch_path = None
    if engine_config.paper_state_path:
        watch_path = engine_config.paper_state_path.parent / "watch_snapshot.json"
    engine: Engine = build_engine(
        engine_config, quotes=quotes, watch_path=watch_path
    )

    watch_out: list[dict[str, Any]] = []
    n_watch = max(0, int(watch_ticks))
    for i in range(n_watch):
        # Fixture advances; yahoo re-fetches
        if hasattr(quotes, "advance"):
            quotes.advance(1)  # type: ignore[attr-defined]
        r = engine.run_once()
        summary = _tick_summary(r)
        summary["phase"] = "watch"
        summary["tick"] = i + 1
        watch_out.append(summary)
        notes.append(
            f"watch[{i+1}]: fills={summary['fills']} bp={summary['buying_power']} "
            f"signals={summary['signals'][:4]}"
        )

    trigger_out: list[dict[str, Any]] = []
    if trigger:
        if assess.bias == "call":
            if hasattr(quotes, "advance"):
                quotes.advance(1)  # type: ignore[attr-defined]
            r = engine.run_once()
            summary = _tick_summary(r)
            summary["phase"] = "trigger"
            trigger_out.append(summary)
            notes.append(
                f"trigger: bias=call — paper tick fills={summary['fills']} "
                f"equity={summary['equity']}"
            )
        else:
            notes.append(
                f"trigger: skipped — bias={assess.bias} (no new long equity on put/hold day)"
            )

    out_dir = out_dir or (config.config_path.parent / "logs")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = out_dir / "morning_paper_plan.json"
    result = MorningResult(
        ts=datetime.now(timezone.utc).isoformat(),
        assess=assess,
        report=report,
        daily_picks=list(report.daily_picks),
        watch_ticks=watch_out,
        trigger_ticks=trigger_out,
        notes=notes,
        paths={
            "daily_focus": str(focus_path),
            "morning_plan": str(plan_path),
            "paper_state": str(engine_config.paper_state_path or ""),
            "postmortem": str(
                config.config_path.parent
                / "logs"
                / "live_options_postmortem_2026-07-17.md"
            ),
        },
    )
    plan_path.write_text(json.dumps(result.to_dict(), indent=2))
    # Also human-readable
    md_path = out_dir / "morning_paper_plan.md"
    md_path.write_text(_morning_md(result))
    result.paths["morning_plan_md"] = str(md_path)
    plan_path.write_text(json.dumps(result.to_dict(), indent=2))
    return result


def _morning_md(r: MorningResult) -> str:
    a = r.assess
    lines = [
        f"# Morning paper plan — {r.ts}",
        "",
        f"**Bias:** `{a.bias}` · quotes `{a.quote_source}`",
        "",
        "## Market assess",
        a.reason,
        "",
        f"- SPY: {a.spy_last} (chg {a.spy_chg_pct}%)",
        f"- QQQ: {a.qqq_last} (chg {a.qqq_chg_pct}%)",
        f"- AAPL: {a.aapl_last} (chg {a.aapl_chg_pct}%)",
        "",
        "## Scanner focus (daily_picks)",
        ", ".join(r.daily_picks) or "(none)",
        "",
        "## Settlement",
        "Paper buys use **settled cash only** when `trade_when_cash_available: false`.",
        "Sell proceeds → unsettled until T+1 (models RH cash≠BP lag).",
        "",
        "## Watch ticks",
    ]
    for t in r.watch_ticks:
        lines.append(
            f"- tick {t.get('tick')}: equity={t.get('equity')} "
            f"settled={t.get('settled_cash')} unsettled={t.get('unsettled_cash')} "
            f"bp={t.get('buying_power')} fills={t.get('fills')} "
            f"signals={t.get('signals')}"
        )
    lines += ["", "## Trigger"]
    if r.trigger_ticks:
        for t in r.trigger_ticks:
            lines.append(
                f"- equity={t.get('equity')} bp={t.get('buying_power')} "
                f"fills={t.get('fills')} signals={t.get('signals')}"
            )
    else:
        lines.append("- (skipped or no fills)")
    lines += ["", "## Notes"]
    for n in r.notes:
        lines.append(f"- {n}")
    lines.append("")
    return "\n".join(lines)
