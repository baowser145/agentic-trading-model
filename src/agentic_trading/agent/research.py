from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agentic_trading.config import AppConfig
from agentic_trading.market.quotes import FixtureQuoteProvider

ET = ZoneInfo("America/New_York")


def _load_dotenv() -> None:
    """Load project .env into os.environ if present (does not override existing)."""
    for candidate in (
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[3] / ".env",  # project root from src/...
        Path(__file__).resolve().parents[2] / ".env",
    ):
        if not candidate.is_file():
            continue
        try:
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v
        except OSError:
            pass
        break


_load_dotenv()

# Liquid pool the LLM/heuristic can pull from when expanding the universe
EXPANSION_POOL = [
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "TSLA",
    "AMD",
    "AVGO",
    "NFLX",
    "CRM",
    "ORCL",
    "JPM",
    "BAC",
    "XLF",
    "XLE",
    "XLK",
    "SMH",
    "COST",
    "WMT",
    "JNJ",
    "UNH",
    "V",
    "MA",
]


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
    daily_picks: list[str] = field(default_factory=list)  # exactly N trade names for the day
    expanded_candidates: list[str] = field(default_factory=list)  # new names beyond current cfg
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
            "daily_picks": self.daily_picks,
            "expanded_candidates": self.expanded_candidates,
            "raw_text": self.raw_text,
            "notes": self.notes,
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Research report — {self.ts}",
            "",
            f"**Mode:** `{self.mode}`" + (f" · model `{self.model}`" if self.model else ""),
            "",
            "## Today's trade list (max 3)",
            ", ".join(f"**{s}**" for s in self.daily_picks) or "(none)",
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
            "## Expanded candidates (new names)",
            ", ".join(self.expanded_candidates) or "(none)",
            "",
            "## Recommended watch universe",
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


def _snapshot_context(
    config: AppConfig, extra_symbols: list[str] | None = None
) -> dict[str, Any]:
    provider = FixtureQuoteProvider()
    symbols = list(
        dict.fromkeys(
            list(config.symbols)
            + [config.strategy.market_symbol]
            + list(extra_symbols or [])
        )
    )
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
                "in_config": sym in config.symbols,
            }
        )
    rows.sort(key=lambda r: (r["rs_vs_spy"] is None, -(r["rs_vs_spy"] or -999)))
    return {
        "universe": symbols,
        "current_config_symbols": config.symbols,
        "expansion_pool": EXPANSION_POOL,
        "market_symbol": config.strategy.market_symbol,
        "risk": {
            "risk_per_trade_pct": config.risk.risk_per_trade_pct,
            "max_daily_loss_pct": config.risk.max_daily_loss_pct,
            "reward_risk_ratio": config.risk.reward_risk_ratio,
            "max_open_positions": config.risk.max_open_positions,
        },
        "rows": rows,
        "note": (
            "Paper fixture quotes are synthetic. Before live, use real quotes/news. "
            "daily_picks = the only names allowed for NEW entries today (besides managing opens)."
        ),
    }


def _pick_daily(
    picks: list[ResearchPick],
    recommended: list[str],
    config: AppConfig,
    n: int = 3,
) -> list[str]:
    """Choose up to n trade names: buy_candidates by conviction, else recommended."""
    mkt = config.strategy.market_symbol
    ordered: list[str] = []
    for p in sorted(picks, key=lambda x: x.conviction, reverse=True):
        if p.action == "buy_candidate" and p.symbol and p.symbol != mkt:
            if p.symbol not in ordered:
                ordered.append(p.symbol)
    for s in recommended:
        if s != mkt and s not in ordered:
            ordered.append(s)
    for s in config.symbols:
        if s != mkt and s not in ordered:
            ordered.append(s)
    return ordered[: max(1, n)]


def _expanded_from(recommended: list[str], config: AppConfig) -> list[str]:
    cur = set(config.symbols)
    return [s for s in recommended if s not in cur and s != config.strategy.market_symbol]


def heuristic_research(
    config: AppConfig,
    context: dict[str, Any] | None = None,
    *,
    daily_n: int = 3,
    expand: bool = True,
) -> ResearchReport:
    extra = EXPANSION_POOL if expand else None
    ctx = context or _snapshot_context(config, extra)
    mkt = config.strategy.market_symbol
    rows = [r for r in ctx["rows"] if r["symbol"] != mkt]
    rows_sorted = sorted(
        rows,
        key=lambda r: (r.get("rs_vs_spy") is None, -(r.get("rs_vs_spy") or -999)),
    )
    picks: list[ResearchPick] = []
    for r in rows_sorted[:8]:
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

    # Universe: config + top expansion names
    focus = [mkt] + [p.symbol for p in picks if p.action == "buy_candidate"]
    seen: set[str] = set()
    recommended: list[str] = []
    for s in focus + list(ctx.get("universe") or []) + config.symbols:
        if s not in seen:
            seen.add(s)
            recommended.append(s)
    recommended = recommended[:16]
    daily = _pick_daily(picks, recommended, config, n=daily_n)
    expanded = _expanded_from(recommended, config)

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
        recommended_symbols=recommended,
        daily_picks=daily,
        expanded_candidates=expanded[:8],
        notes=[
            f"Daily trade list locked to {len(daily)} names: {', '.join(daily)}.",
            "Heuristic only — use --llm for Grok expansion + narrative.",
            ctx.get("note", ""),
        ],
    )


def _extract_json_block(text: str) -> dict[str, Any] | None:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def llm_research(
    config: AppConfig,
    context: dict[str, Any] | None = None,
    *,
    daily_n: int = 3,
    expand: bool = True,
) -> ResearchReport:
    extra = EXPANSION_POOL if expand else None
    ctx = context or _snapshot_context(config, extra)
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        report = heuristic_research(config, ctx, daily_n=daily_n, expand=expand)
        report.notes.insert(
            0,
            "XAI_API_KEY not set — fell back to heuristic.",
        )
        return report

    try:
        from openai import OpenAI
    except ImportError:
        report = heuristic_research(config, ctx, daily_n=daily_n, expand=expand)
        report.notes.insert(0, "openai package missing — fell back to heuristic.")
        return report

    model = os.environ.get("XAI_MODEL", "grok-4.5")
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

    system = (
        "You are a cautious equity research assistant for a PAPER trading bot. "
        "You do NOT place orders. Output ONLY valid JSON matching the schema. "
        "Prefer liquid US mega-caps and major sector ETFs. "
        f"Pick exactly {daily_n} names for daily_picks (trade today only; NOT including SPY). "
        "You may suggest expanded_candidates from expansion_pool that are NOT already in "
        "current_config_symbols. Respect aggressive 5% risk-per-trade. Never promise profits."
    )
    user = {
        "task": (
            f"Research liquid names. Suggest more stocks if useful (expanded_candidates). "
            f"Pick exactly {daily_n} daily_picks for NEW long entries today (highest quality). "
            "Also return recommended_symbols watch universe (include SPY first, 8-12 names)."
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
            "daily_picks": ["T1", "T2", "T3"],
            "expanded_candidates": ["NEW1", "NEW2"],
            "recommended_symbols": ["SPY", "..."],
        },
        "context": ctx,
    }

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, indent=2)},
            ],
            temperature=0.3,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        report = heuristic_research(config, ctx, daily_n=daily_n, expand=expand)
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
        recommended = list(config.symbols)

    daily_raw = [
        str(s).upper() for s in (parsed.get("daily_picks") or []) if str(s).strip()
    ]
    daily_raw = [s for s in daily_raw if s != config.strategy.market_symbol]
    if len(daily_raw) < daily_n:
        daily = _pick_daily(picks, recommended, config, n=daily_n)
        # prefer model order when partial
        for s in daily_raw:
            if s not in daily:
                daily.insert(0, s)
        daily = daily[:daily_n]
    else:
        daily = daily_raw[:daily_n]

    expanded = [
        str(s).upper()
        for s in (parsed.get("expanded_candidates") or [])
        if str(s).strip() and str(s).upper() not in config.symbols
    ]
    if not expanded:
        expanded = _expanded_from(recommended, config)

    # Merge expanded into recommended universe
    for s in expanded + daily:
        if s not in recommended:
            recommended.append(s)
    recommended = recommended[:16]

    return ResearchReport(
        ts=datetime.now(timezone.utc).isoformat(),
        mode="llm",
        model=model,
        market_view=str(parsed.get("market_view") or raw[:500]),
        picks=picks,
        recommended_symbols=recommended,
        daily_picks=daily,
        expanded_candidates=expanded[:10],
        raw_text=raw,
        notes=[
            f"Daily trade list ({daily_n}): {', '.join(daily)} — engine will only NEW-enter these.",
            "LLM research is advisory; risk rails still govern size/stops.",
            ctx.get("note", ""),
        ],
    )


def write_daily_focus(
    report: ResearchReport,
    out_path: Path,
    *,
    daily_n: int = 3,
) -> Path:
    """Persist today's 3 trade names for the engine."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now(ET).date().isoformat()
    payload = {
        "date": today,
        "ts": report.ts,
        "mode": report.mode,
        "model": report.model,
        "daily_picks": report.daily_picks[:daily_n],
        "expanded_candidates": report.expanded_candidates,
        "recommended_symbols": report.recommended_symbols,
        "market_view": report.market_view,
        "picks": [asdict(p) for p in report.picks if p.symbol in report.daily_picks],
    }
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path


def load_daily_focus(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    # Expire if not today (America/New_York)
    today = datetime.now(ET).date().isoformat()
    if data.get("date") and data["date"] != today:
        return {**data, "expired": True}
    return data


def run_research(
    config: AppConfig,
    *,
    use_llm: bool = False,
    out_dir: Path | None = None,
    daily_n: int = 3,
    expand: bool = True,
) -> ResearchReport:
    out_dir = out_dir or (config.config_path.parent / "logs")
    out_dir.mkdir(parents=True, exist_ok=True)
    if use_llm:
        report = llm_research(config, daily_n=daily_n, expand=expand)
    else:
        report = heuristic_research(config, daily_n=daily_n, expand=expand)

    json_path = out_dir / "research_latest.json"
    md_path = out_dir / "research_latest.md"
    json_path.write_text(json.dumps(report.to_dict(), indent=2))
    md_path.write_text(report.to_markdown())
    report.notes.append(f"wrote {json_path}")
    report.notes.append(f"wrote {md_path}")
    return report


def apply_recommended_symbols(config_path: Path, symbols: list[str]) -> Path:
    import yaml

    data = yaml.safe_load(config_path.read_text()) or {}
    clean: list[str] = []
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


def apply_universe_from_report(config_path: Path, report: ResearchReport) -> Path:
    """Merge expanded + recommended into config symbols (SPY first)."""
    import yaml

    data = yaml.safe_load(config_path.read_text()) or {}
    current = [str(s).upper() for s in (data.get("symbols") or [])]
    merged = (
        ["SPY"]
        + report.daily_picks
        + report.expanded_candidates
        + report.recommended_symbols
        + current
    )
    clean: list[str] = []
    seen: set[str] = set()
    for s in merged:
        if s and s not in seen:
            seen.add(s)
            clean.append(s)
    # Cap universe size for paper
    data["symbols"] = clean[:16]
    config_path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))
    return config_path
