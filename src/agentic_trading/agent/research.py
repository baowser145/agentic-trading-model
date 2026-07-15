from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_trading.config import AppConfig
from agentic_trading.market.quotes import FixtureQuoteProvider


@dataclass
class ResearchPick:
    symbol: str
    action: str  # buy_candidate | avoid | hold_watch
    conviction: float  # 0..1
    thesis: str
    risks: str = ""


@dataclass
class ResearchReport:
    ts: str
    mode: str  # llm | heuristic
    model: str | None
    market_view: str
    picks: list[ResearchPick] = field(default_factory=list)
    recommended_symbols: list[str] = field(default_factory=list)
    raw_text: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "mode": self.mode,
            "model": self.model,
            "market_view": self.market_view,
            "picks": [asdict(p) for p in self.picks],
            "recommended_symbols": self.recommended_symbols,
            "raw_text": self.raw_text,
            "notes": self.notes,
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Research report — {self.ts}",
            "",
            f"**Mode:** `{self.mode}`" + (f" · model `{self.model}`" if self.model else ""),
            "",
            "## Market view",
            self.market_view or "(none)",
            "",
            "## Picks",
        ]
        for p in self.picks:
            lines.append(
                f"- **{p.symbol}** — `{p.action}` "
                f"(conviction {p.conviction:.2f}): {p.thesis}"
            )
            if p.risks:
                lines.append(f"  - Risks: {p.risks}")
        lines += [
            "",
            "## Recommended focus list",
            ", ".join(self.recommended_symbols) or "(empty)",
            "",
            "## Notes",
        ]
        for n in self.notes:
            lines.append(f"- {n}")
        if self.raw_text and self.mode == "llm":
            lines += ["", "## Raw model output", "```", self.raw_text[:4000], "```"]
        lines.append("")
        return "\n".join(lines)


def _rel_strength(
    symbol: str, history: dict[str, list], market: str = "SPY", n: int = 10
) -> float | None:
    sc = [b.close for b in (history.get(symbol) or []) if b.close > 0]
    mc = [b.close for b in (history.get(market) or []) if b.close > 0]
    if len(sc) < n or len(mc) < n:
        return None
    return (sc[-1] / sc[-n] - 1.0) - (mc[-1] / mc[-n] - 1.0)


def _snapshot_context(config: AppConfig) -> dict[str, Any]:
    """Build a compact market context from the paper quote provider (or future live feed)."""
    provider = FixtureQuoteProvider()
    # Advance so history has shape; use current step
    symbols = list(dict.fromkeys(config.symbols + [config.strategy.market_symbol]))
    # Warm history by stepping so lookback is meaningful
    for _ in range(max(15, config.strategy.lookback_bars // 2)):
        provider.advance(1)
    quotes = provider.get_quotes(symbols)
    history = provider.get_history(symbols, config.strategy.lookback_bars)
    rows = []
    for sym in symbols:
        q = quotes.get(sym)
        rs = _rel_strength(sym, history, config.strategy.market_symbol)
        bars = history.get(sym) or []
        closes = [b.close for b in bars]
        chg = (closes[-1] / closes[0] - 1.0) if len(closes) >= 2 else 0.0
        rows.append(
            {
                "symbol": sym,
                "price": round(q.price, 4) if q else None,
                "range_change_pct": round(chg * 100, 3),
                "rs_vs_spy": None if rs is None else round(rs * 100, 3),
            }
        )
    rows.sort(key=lambda r: (r["rs_vs_spy"] is None, -(r["rs_vs_spy"] or -999)))
    return {
        "universe": symbols,
        "market_symbol": config.strategy.market_symbol,
        "risk": {
            "risk_per_trade_pct": config.risk.risk_per_trade_pct,
            "max_daily_loss_pct": config.risk.max_daily_loss_pct,
            "reward_risk_ratio": config.risk.reward_risk_ratio,
        },
        "rows": rows,
        "note": (
            "Paper fixture quotes are synthetic. Before live, replace with real "
            "quotes / news. This pass is a decision aid, not an order."
        ),
    }


def heuristic_research(config: AppConfig, context: dict[str, Any] | None = None) -> ResearchReport:
    """No API key needed — rank by relative strength / range change."""
    ctx = context or _snapshot_context(config)
    mkt = config.strategy.market_symbol
    rows = [r for r in ctx["rows"] if r["symbol"] != mkt]
    rows_sorted = sorted(
        rows,
        key=lambda r: (r.get("rs_vs_spy") is None, -(r.get("rs_vs_spy") or -999)),
    )
    picks: list[ResearchPick] = []
    for r in rows_sorted[:5]:
        rs = r.get("rs_vs_spy") or 0.0
        picks.append(
            ResearchPick(
                symbol=r["symbol"],
                action="buy_candidate" if rs >= 0 else "hold_watch",
                conviction=min(0.9, 0.4 + max(0.0, rs) / 10.0),
                thesis=(
                    f"Heuristic RS vs {mkt}: {rs:+.2f}% over lookback; "
                    f"range change {r.get('range_change_pct')}%."
                ),
                risks="Synthetic paper data; no news/fundamentals.",
            )
        )
    # Always keep SPY in focus for market filter
    focus = [mkt] + [p.symbol for p in picks if p.action == "buy_candidate"][:6]
    # Dedupe preserve order
    seen: set[str] = set()
    recommended = []
    for s in focus + config.symbols:
        if s not in seen:
            seen.add(s)
            recommended.append(s)

    spy_row = next((r for r in ctx["rows"] if r["symbol"] == mkt), None)
    market_view = (
        f"{mkt} fixture range change {spy_row.get('range_change_pct')}%; "
        "heuristic assumes trade only when playbook market filter is green."
        if spy_row
        else "No market row."
    )
    return ResearchReport(
        ts=datetime.now(timezone.utc).isoformat(),
        mode="heuristic",
        model=None,
        market_view=market_view,
        picks=picks,
        recommended_symbols=recommended[:10],
        notes=[
            "Heuristic only — set XAI_API_KEY and run with --llm for Grok research.",
            ctx.get("note", ""),
        ],
    )


def _extract_json_block(text: str) -> dict[str, Any] | None:
    text = text.strip()
    # fenced ```json
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # raw object
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def llm_research(config: AppConfig, context: dict[str, Any] | None = None) -> ResearchReport:
    """
    Call SpaceXAI / xAI (OpenAI-compatible) for a research pass.

    Requires: XAI_API_KEY and `pip install openai`.
    """
    ctx = context or _snapshot_context(config)
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        report = heuristic_research(config, ctx)
        report.notes.insert(
            0,
            "XAI_API_KEY not set — fell back to heuristic. "
            "export XAI_API_KEY=... then re-run with --llm",
        )
        return report

    try:
        from openai import OpenAI
    except ImportError:
        report = heuristic_research(config, ctx)
        report.notes.insert(
            0,
            "openai package missing — pip install 'agentic-trading[llm]' or openai. "
            "Fell back to heuristic.",
        )
        return report

    model = os.environ.get("XAI_MODEL", "grok-4.5")
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

    system = (
        "You are a cautious equity research assistant for a PAPER trading bot. "
        "You do NOT place orders. Output ONLY valid JSON matching the schema. "
        "Prefer liquid US mega-caps and ETFs. Respect that risk is aggressive (5% per trade). "
        "Never promise profits. Flag uncertainty."
    )
    user = {
        "task": (
            "Given this universe snapshot, produce a short research pass: "
            "market view, up to 5 picks with action buy_candidate|avoid|hold_watch, "
            "and recommended_symbols (8-10 tickers, always include SPY first)."
        ),
        "schema": {
            "market_view": "string",
            "picks": [
                {
                    "symbol": "STR",
                    "action": "buy_candidate|avoid|hold_watch",
                    "conviction": 0.0,
                    "thesis": "string",
                    "risks": "string",
                }
            ],
            "recommended_symbols": ["SPY", "..."],
        },
        "context": ctx,
    }

    # Prefer chat.completions for broad compatibility with OpenAI SDK
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(user, indent=2),
                },
            ],
            temperature=0.3,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001 — surface provider errors cleanly
        report = heuristic_research(config, ctx)
        report.notes.insert(0, f"LLM call failed ({e!r}); fell back to heuristic.")
        return report

    parsed = _extract_json_block(raw) or {}
    picks: list[ResearchPick] = []
    for p in parsed.get("picks") or []:
        try:
            picks.append(
                ResearchPick(
                    symbol=str(p.get("symbol", "")).upper(),
                    action=str(p.get("action", "hold_watch")),
                    conviction=float(p.get("conviction", 0.5)),
                    thesis=str(p.get("thesis", "")),
                    risks=str(p.get("risks", "")),
                )
            )
        except (TypeError, ValueError):
            continue

    recommended = [
        str(s).upper() for s in (parsed.get("recommended_symbols") or []) if str(s).strip()
    ]
    if "SPY" not in recommended:
        recommended = ["SPY"] + recommended
    if not recommended:
        recommended = config.symbols

    return ResearchReport(
        ts=datetime.now(timezone.utc).isoformat(),
        mode="llm",
        model=model,
        market_view=str(parsed.get("market_view") or raw[:500]),
        picks=picks,
        recommended_symbols=recommended[:12],
        raw_text=raw,
        notes=[
            "LLM research is advisory only — risk rails and playbook still govern orders.",
            "Review picks before any live Agentic use.",
            ctx.get("note", ""),
        ],
    )


def run_research(
    config: AppConfig,
    *,
    use_llm: bool = False,
    out_dir: Path | None = None,
) -> ResearchReport:
    out_dir = out_dir or (config.config_path.parent / "logs")
    out_dir.mkdir(parents=True, exist_ok=True)
    report = llm_research(config) if use_llm else heuristic_research(config)

    json_path = out_dir / "research_latest.json"
    md_path = out_dir / "research_latest.md"
    json_path.write_text(json.dumps(report.to_dict(), indent=2))
    md_path.write_text(report.to_markdown())
    report.notes.append(f"wrote {json_path}")
    report.notes.append(f"wrote {md_path}")
    return report


def apply_recommended_symbols(config_path: Path, symbols: list[str]) -> Path:
    """Rewrite config.yaml symbols list (preserves rest of file via YAML load/dump)."""
    import yaml

    data = yaml.safe_load(config_path.read_text()) or {}
    # Always put SPY first if present
    clean = []
    seen: set[str] = set()
    for s in symbols:
        u = str(s).upper().strip()
        if u and u not in seen:
            seen.add(u)
            clean.append(u)
    if "SPY" in clean:
        clean = ["SPY"] + [s for s in clean if s != "SPY"]
    data["symbols"] = clean
    config_path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))
    return config_path
