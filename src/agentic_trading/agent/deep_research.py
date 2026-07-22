"""
Single-ticker deep research (5-prompt stack) before daily_focus.

Prompts (adapted from public "5 prompts to analyze a stock" style content):
  1. Deep Dive       — business model, moat, catalysts, asymmetry
  2. Peer Comparison — relative valuation vs growth
  3. Bear Case       — red flags ranked by severity
  4. Bull / Variant  — what the market underprices; asymmetric upside
  5. Trade Plan      — entry, invalidation, size, time stop; go|wait|pass

Writes memo under logs/deep_research/ — does NOT place orders or write daily_focus.
Use after a scan ranks candidates; survivors can later feed daily_focus.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agentic_trading.config import AppConfig
from agentic_trading.market.quotes import FixtureQuoteProvider, QuoteProvider

ET = ZoneInfo("America/New_York")

# Sensible default peers when --peers not given (2 peers each).
DEFAULT_PEERS: dict[str, list[str]] = {
    "AAPL": ["MSFT", "GOOGL"],
    "MSFT": ["AAPL", "GOOGL"],
    "GOOGL": ["META", "MSFT"],
    "META": ["GOOGL", "SNAP"],
    "AMZN": ["WMT", "SHOP"],
    "NVDA": ["AMD", "AVGO"],
    "AMD": ["NVDA", "INTC"],
    "AVGO": ["NVDA", "QCOM"],
    "TSLA": ["F", "GM"],
    "NFLX": ["DIS", "WBD"],
    "ORCL": ["MSFT", "CRM"],
    "CRM": ["ORCL", "NOW"],
    "V": ["MA", "AXP"],
    "MA": ["V", "AXP"],
    "JPM": ["BAC", "WFC"],
    "BAC": ["JPM", "WFC"],
    "UNH": ["ELV", "CVS"],
    "JNJ": ["PFE", "ABBV"],
    "HOOD": ["SCHW", "COIN"],
    "COIN": ["HOOD", "MSTR"],
    "PLTR": ["SNOW", "DDOG"],
    "UBER": ["LYFT", "DASH"],
    "DIS": ["NFLX", "CMCSA"],
    "BA": ["LMT", "RTX"],
    "COST": ["WMT", "TGT"],
    "WMT": ["COST", "TGT"],
    "PYPL": ["SQ", "V"],
    "INTC": ["AMD", "TSM"],
    "TSM": ["INTC", "ASML"],
    "MU": ["SNDK", "WDC"],
}


def _load_dotenv() -> None:
    """Load project .env into os.environ if present (does not override existing)."""
    for candidate in (
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[3] / ".env",
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


@dataclass
class DeepResearchMemo:
    """Structured 5-section deep research memo for one ticker."""

    ts: str
    ticker: str
    peers: list[str]
    mode: str  # llm | heuristic
    model: str | None
    # Gate for daily_focus consideration (advisory only)
    verdict: str  # pass | caution | fail
    conviction: float  # 0..1
    one_liner: str
    deep_dive: dict[str, Any] = field(default_factory=dict)
    peer_comparison: dict[str, Any] = field(default_factory=dict)
    bear_case: dict[str, Any] = field(default_factory=dict)
    bull_case: dict[str, Any] = field(default_factory=dict)
    trade_plan: dict[str, Any] = field(default_factory=dict)
    tape_context: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    notes: list[str] = field(default_factory=list)
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        t = self.ticker
        lines = [
            f"# Deep research — {t}",
            "",
            f"**Generated:** {self.ts}  ",
            f"**Mode:** `{self.mode}`"
            + (f" · model `{self.model}`" if self.model else ""),
            f"**Peers:** {', '.join(self.peers) or '(none)'}  ",
            f"**Verdict (advisory):** `{self.verdict}` · conviction {self.conviction:.2f}  ",
            f"**One-liner:** {self.one_liner or '(n/a)'}",
            "",
            "> Does **not** write `daily_focus` or place orders. "
            "Use as a quality gate before locking a name into today's trade list.",
            "",
        ]

        if self.tape_context:
            lines += ["## Tape context (local quotes)", ""]
            price = self.tape_context.get("price")
            rs = self.tape_context.get("rs_vs_spy")
            chg = self.tape_context.get("range_change_pct")
            lines.append(
                f"- Price: {price} · range Δ: {chg}% · RS vs SPY: {rs}%"
                if price is not None
                else f"- {json.dumps(self.tape_context)}"
            )
            src = self.tape_context.get("source")
            if src:
                lines.append(f"- Source: `{src}`")
            lines.append("")

        dd = self.deep_dive or {}
        lines += [
            "## 1. The Deep Dive",
            "*Business model · moat · catalysts · asymmetry*",
            "",
            f"**Business model:** {dd.get('business_model') or '_(missing)_'}",
            "",
            f"**Moat:** {dd.get('moat') or '_(missing)_'}",
            "",
            f"**Top competitors:** {', '.join(dd.get('competitors') or []) or '_(missing)_'}",
            "",
            f"**Catalysts (12m):** {dd.get('catalysts') or '_(missing)_'}",
            "",
            f"**Asymmetry:** {dd.get('asymmetry') or '_(missing)_'}",
            "",
        ]

        pc = self.peer_comparison or {}
        lines += [
            "## 2. The Peer Comparison",
            "*Does the valuation make sense relative to growth?*",
            "",
            f"**Summary:** {pc.get('summary') or '_(missing)_'}",
            "",
        ]
        table = pc.get("table") or []
        if table:
            headers = [
                "ticker",
                "ps_ttm",
                "ps_fwd",
                "p_fcf",
                "ev_ebitda",
                "gross_margin",
                "rev_growth_yoy",
                "value_growth_score",
            ]
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join("---" for _ in headers) + " |")
            for row in table:
                cells = [str(row.get(h, "—")) for h in headers]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
            lines.append(
                "*Value/Growth Score ≈ P/S TTM ÷ revenue growth % "
                "(lower = more growth per dollar of valuation; "
                "handle carefully when growth is near zero).*"
            )
            lines.append("")
        if pc.get("cheapest_growth"):
            lines.append(f"**Best growth-per-dollar:** {pc['cheapest_growth']}")
            lines.append("")

        bc = self.bear_case or {}
        lines += [
            "## 3. The Bear Case",
            "*3 biggest reasons NOT to own this*",
            "",
        ]
        flags = bc.get("red_flags") or []
        if not flags:
            lines.append("_(no red flags returned)_")
            lines.append("")
        else:
            for i, flag in enumerate(flags, 1):
                if isinstance(flag, dict):
                    title = flag.get("title") or flag.get("flag") or f"Flag {i}"
                    sev = flag.get("severity") or "?"
                    detail = flag.get("detail") or flag.get("description") or ""
                    src = flag.get("source") or flag.get("sources") or ""
                    lines.append(f"### {i}. [{sev}] {title}")
                    lines.append("")
                    lines.append(detail)
                    if src:
                        lines.append("")
                        lines.append(f"*Source note:* {src}")
                    lines.append("")
                else:
                    lines.append(f"{i}. {flag}")
                    lines.append("")
        if bc.get("checklist"):
            lines += ["**Checklist coverage:**", ""]
            for k, v in (bc["checklist"] or {}).items():
                lines.append(f"- **{k}:** {v}")
            lines.append("")

        bu = self.bull_case or {}
        lines += [
            "## 4. The Bull Case / Variant Perception",
            "*What is the market underpricing?*",
            "",
            f"**Summary:** {bu.get('summary') or '_(missing)_'}",
            "",
            f"**Variant perception:** {bu.get('variant_perception') or '_(missing)_'}",
            "",
        ]
        bulls = bu.get("bull_points") or []
        if bulls:
            lines.append("**Bull points (ranked):**")
            lines.append("")
            for i, pt in enumerate(bulls, 1):
                if isinstance(pt, dict):
                    title = pt.get("title") or pt.get("point") or f"Point {i}"
                    impact = pt.get("impact") or pt.get("severity") or ""
                    detail = pt.get("detail") or pt.get("description") or ""
                    head = f"{i}. **{title}**"
                    if impact:
                        head += f" · impact `{impact}`"
                    lines.append(head)
                    if detail:
                        lines.append(f"   {detail}")
                else:
                    lines.append(f"{i}. {pt}")
            lines.append("")
        if bu.get("scenarios"):
            lines.append("**Scenarios:**")
            lines.append("")
            sc = bu["scenarios"]
            if isinstance(sc, dict):
                for k in ("bull", "base", "bear"):
                    if sc.get(k):
                        lines.append(f"- **{k}:** {sc[k]}")
            else:
                lines.append(str(sc))
            lines.append("")
        if bu.get("rerate_triggers"):
            lines.append(f"**Re-rate triggers:** {bu['rerate_triggers']}")
            lines.append("")

        tp = self.trade_plan or {}
        lines += [
            "## 5. The Trade Plan / Kill Criteria",
            "*How to put money on this — or pass*",
            "",
            f"**Decision:** `{tp.get('decision') or 'wait'}`  ",
            f"**Setup type:** {tp.get('setup_type') or '_(n/a)_'}",
            "",
            f"**Entry conditions:** {tp.get('entry') or '_(missing)_'}",
            "",
            f"**Invalidation / stop:** {tp.get('invalidation') or '_(missing)_'}",
            "",
            f"**Size / risk:** {tp.get('size_risk') or '_(missing)_'}",
            "",
            f"**Time stop:** {tp.get('time_stop') or '_(missing)_'}",
            "",
            f"**Options note (7–31 DTE):** {tp.get('options_note') or '_(n/a)_'}",
            "",
            f"**Daily focus fit:** {tp.get('daily_focus_fit') or '_(n/a)_'}",
            "",
        ]

        lines += ["## Notes", ""]
        for n in self.notes:
            lines.append(f"- {n}")
        if self.paths:
            lines.append(f"- Wrote: {self.paths.get('md')} · {self.paths.get('json')}")
        if self.raw_text and self.mode == "llm":
            lines += ["", "## Raw model output", "```", self.raw_text[:5000], "```"]
        lines.append("")
        return "\n".join(lines)


def resolve_peers(ticker: str, peers: list[str] | None = None) -> list[str]:
    t = ticker.upper().strip()
    if peers:
        out = [p.upper().strip() for p in peers if p and p.upper().strip() != t]
        return list(dict.fromkeys(out))[:4]
    defaults = DEFAULT_PEERS.get(t, ["SPY", "QQQ"])
    return [p for p in defaults if p != t][:2]


def _rel_strength(
    symbol: str, history: dict[str, list], market: str = "SPY", n: int = 10
) -> float | None:
    sc = [b.close for b in (history.get(symbol) or []) if b.close > 0]
    mc = [b.close for b in (history.get(market) or []) if b.close > 0]
    if len(sc) < n or len(mc) < n:
        return None
    return (sc[-1] / sc[-n] - 1.0) - (mc[-1] / mc[-n] - 1.0)


def _tape_context(
    ticker: str,
    peers: list[str],
    config: AppConfig,
    quote_provider: QuoteProvider | None = None,
) -> dict[str, Any]:
    provider: QuoteProvider = quote_provider or FixtureQuoteProvider()
    symbols = list(dict.fromkeys([ticker, "SPY"] + peers))
    if isinstance(provider, FixtureQuoteProvider):
        for _ in range(max(15, config.strategy.lookback_bars // 2)):
            provider.advance(1)
    quotes = provider.get_quotes(symbols)
    history = provider.get_history(symbols, config.strategy.lookback_bars)
    mkt = config.strategy.market_symbol or "SPY"

    def row(sym: str) -> dict[str, Any]:
        q = quotes.get(sym)
        bars = history.get(sym) or []
        closes = [b.close for b in bars]
        chg = (closes[-1] / closes[0] - 1.0) if len(closes) >= 2 else None
        rs = _rel_strength(sym, history, mkt)
        return {
            "symbol": sym,
            "price": round(q.price, 4) if q else None,
            "range_change_pct": None if chg is None else round(chg * 100, 3),
            "rs_vs_spy": None if rs is None else round(rs * 100, 3),
        }

    liveish = not isinstance(provider, FixtureQuoteProvider)
    primary = row(ticker)
    return {
        **primary,
        "source": "yahoo/live" if liveish else "fixture",
        "peers": [row(p) for p in peers],
        "market": row(mkt),
    }


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


def _normalize_verdict(v: str | None) -> str:
    s = (v or "caution").strip().lower()
    if s in ("pass", "go", "buy", "ok", "yes"):
        return "pass"
    if s in ("fail", "avoid", "no", "kill", "reject"):
        return "fail"
    return "caution"


def _normalize_trade_decision(v: str | None) -> str:
    """Trade-plan decision: go | wait | pass."""
    s = (v or "wait").strip().lower()
    if s in ("go", "buy", "enter", "yes", "long"):
        return "go"
    if s in ("pass", "avoid", "no", "skip", "reject", "fail"):
        return "pass"
    return "wait"


def heuristic_deep_research(
    ticker: str,
    peers: list[str],
    config: AppConfig,
    *,
    quote_provider: QuoteProvider | None = None,
    reason: str = "XAI_API_KEY not set or LLM unavailable",
) -> DeepResearchMemo:
    """Offline skeleton memo — tape only; fundamentals marked incomplete."""
    t = ticker.upper().strip()
    peer_list = resolve_peers(t, peers)
    tape = _tape_context(t, peer_list, config, quote_provider=quote_provider)
    rs = tape.get("rs_vs_spy")
    # Mild tape-based tilt only (not a fundamental verdict)
    if rs is not None and float(rs) > 2.0:
        verdict, conv = "caution", 0.45
    elif rs is not None and float(rs) < -2.0:
        verdict, conv = "caution", 0.35
    else:
        verdict, conv = "caution", 0.40

    return DeepResearchMemo(
        ts=datetime.now(timezone.utc).isoformat(),
        ticker=t,
        peers=peer_list,
        mode="heuristic",
        model=None,
        verdict=verdict,
        conviction=conv,
        one_liner=(
            f"{t} heuristic stub only — run with LLM (XAI_API_KEY) for full "
            "5-section stack (Deep Dive / Peer / Bear / Bull / Trade Plan)."
        ),
        deep_dive={
            "business_model": (
                f"[HEURISTIC] No LLM. Confirm how {t} makes money before sizing."
            ),
            "moat": "[HEURISTIC] Moat not evaluated — durable edge unknown.",
            "competitors": peer_list,
            "catalysts": "[HEURISTIC] Catalyst calendar not researched.",
            "asymmetry": "[HEURISTIC] Valuation floor vs growth ceiling not assessed.",
        },
        peer_comparison={
            "summary": (
                f"[HEURISTIC] Compare {t} vs {', '.join(peer_list)} on Yahoo Finance / "
                "Macrotrends: P/S TTM+fwd, P/FCF, EV/EBITDA, gross margin, YoY rev growth, "
                "Value/Growth = P/S TTM ÷ rev growth %."
            ),
            "table": [
                {
                    "ticker": s,
                    "ps_ttm": "—",
                    "ps_fwd": "—",
                    "p_fcf": "—",
                    "ev_ebitda": "—",
                    "gross_margin": "—",
                    "rev_growth_yoy": "—",
                    "value_growth_score": "—",
                }
                for s in [t, *peer_list]
            ],
            "cheapest_growth": "unknown (heuristic)",
        },
        bear_case={
            "red_flags": [
                {
                    "title": "Research incomplete",
                    "severity": "high",
                    "detail": (
                        "No SEC/transcript pass ran. Do not promote to daily_focus "
                        "until LLM deep-research (or manual) covers customer "
                        "concentration, margin compression, insider selling, "
                        "GAAP vs non-GAAP gap, and guidance cuts."
                    ),
                    "source": reason,
                },
                {
                    "title": "Tape-only signal",
                    "severity": "medium",
                    "detail": (
                        f"Local tape RS vs SPY = {rs}%. Price momentum is not a moat "
                        "or valuation thesis."
                    ),
                    "source": "local quote provider",
                },
                {
                    "title": "Liquidity / horizon fit",
                    "severity": "medium",
                    "detail": (
                        "Confirm name stays liquid enough for paper/options playbook "
                        "(7–31 DTE long premium; mega + liquid second tier preferred)."
                    ),
                    "source": "project AGENTS.md rails",
                },
            ],
            "checklist": {
                "customer_concentration": "not checked",
                "margin_compression": "not checked",
                "unscheduled_insider_selling": "not checked",
                "gaap_vs_nongaap_gap": "not checked",
                "guidance_cuts_12m": "not checked",
            },
        },
        bull_case={
            "summary": (
                f"[HEURISTIC] No variant perception built for {t}. "
                "Run --llm to identify what the market may be underpricing."
            ),
            "variant_perception": "[HEURISTIC] Unknown — consensus not mapped.",
            "bull_points": [
                {
                    "title": "Incomplete bull case",
                    "impact": "high",
                    "detail": (
                        "Without LLM, cannot rank catalyst-driven re-rate paths. "
                        "Do not size a long solely on RS."
                    ),
                }
            ],
            "scenarios": {
                "bull": "Not modeled (heuristic).",
                "base": "Not modeled (heuristic).",
                "bear": "Not modeled (heuristic).",
            },
            "rerate_triggers": "[HEURISTIC] List catalysts after full research.",
        },
        trade_plan={
            "decision": "wait",
            "setup_type": "none — research incomplete",
            "entry": (
                "[HEURISTIC] No entry until full 5-section memo. "
                "If promoting anyway: only liquid names, SPY-filter playbook, "
                f"risk ≤ {config.risk.risk_per_trade_pct:.0%} equity."
            ),
            "invalidation": (
                "[HEURISTIC] Soft: thesis incomplete. Hard: daily halt "
                f"({config.risk.max_daily_loss_pct:.0%} equity) or playbook stop."
            ),
            "size_risk": (
                f"Max risk/trade {config.risk.risk_per_trade_pct:.0%} equity; "
                f"max open positions {config.risk.max_open_positions}; "
                "do not exceed paper/live rails."
            ),
            "time_stop": (
                "Equity playbook: soft market-red + hold rules. "
                "Options: force manage by ≤3 DTE; default long-premium 7–31 DTE."
            ),
            "options_note": (
                "Long premium only if BP free, Agentic, max_open_options, "
                "and catalysts fit DTE window — never from this heuristic alone."
            ),
            "daily_focus_fit": "no — wait for LLM pass/caution with explicit go decision",
        },
        tape_context=tape,
        notes=[
            reason,
            "Heuristic mode never writes daily_focus — advisory memo only.",
            "Re-run: python -m agentic_trading deep-research --ticker "
            f"{t} --llm --quotes yahoo",
        ],
    )


def llm_deep_research(
    ticker: str,
    peers: list[str],
    config: AppConfig,
    *,
    quote_provider: QuoteProvider | None = None,
) -> DeepResearchMemo:
    t = ticker.upper().strip()
    peer_list = resolve_peers(t, peers)
    tape = _tape_context(t, peer_list, config, quote_provider=quote_provider)

    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        return heuristic_deep_research(
            t,
            peer_list,
            config,
            quote_provider=quote_provider,
            reason="XAI_API_KEY not set — fell back to heuristic deep-research.",
        )

    try:
        from openai import OpenAI
    except ImportError:
        return heuristic_deep_research(
            t,
            peer_list,
            config,
            quote_provider=quote_provider,
            reason="openai package missing — pip install -e '.[llm]'.",
        )

    model = os.environ.get("XAI_MODEL", "grok-4.5")
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

    system = (
        "You are a skeptical equity research analyst for a PAPER-FIRST trading bot. "
        "You do NOT place orders and you do NOT write daily trade lists. "
        "Produce a rigorous 5-part memo. Prefer liquid US names. "
        "Be concrete; flag uncertainty instead of inventing precise SEC figures. "
        "If you cite numbers (margins, concentration, multiples), mark approximate "
        "or 'verify in filings' when not certain. "
        "Section 5 must respect the bot risk rails in project_rails. "
        "Output ONLY valid JSON."
    )

    user = {
        "task": (
            f"Deep research on {t} vs peers {peer_list}. "
            "Run all five sections below as one memo."
        ),
        "sections": {
            "1_deep_dive": (
                f"Generate a comprehensive Deep Research Report on {t}. "
                "Cover: (1) Business Model — how they actually make money; core product "
                "in plain English. (2) Moat — top 3 competitors; durable edge (patent, "
                "switching cost, network effect, cost structure) rivals can't easily copy. "
                "(3) Catalysts — launches, earnings, regulatory events, partnerships in "
                "next 12 months. (4) Asymmetry — low valuation floor vs high growth "
                "ceiling? Why or why not?"
            ),
            "2_peer_comparison": (
                f"Build a relative valuation table for {t} vs "
                f"{' and '.join(peer_list)}. "
                "Include P/S (TTM and Forward), P/FCF, EV/EBITDA, gross margin, "
                "YoY revenue growth, and Value/Growth Score (P/S TTM ÷ revenue growth %). "
                "Lowest score = most growth per dollar of valuation. "
                "Use best-known approximate figures and label as approximate. "
                "Cheap only matters relative to growth."
            ),
            "3_bear_case": (
                f"Act as a skeptical short-seller researching {t}. "
                "Give the 3 most serious red flags, ranked by severity. "
                "Check for: customer concentration (any single customer over ~25% of "
                "revenue), margin compression (gross AND operating), unscheduled "
                "insider selling (not routine 10b5-1), widening GAAP vs non-GAAP gap, "
                "guidance cuts in last 12 months. Cite source types for each "
                "(10-K, earnings call, news). If you cannot verify a check, say so."
            ),
            "4_bull_case": (
                f"Build the Bull Case / Variant Perception for {t}. "
                "What is the market underpricing? Give 2–3 bull points ranked by impact. "
                "State the variant perception vs consensus explicitly. "
                "Provide brief bull / base / bear scenario narratives (not price targets "
                "as promises). List re-rate triggers over the next 3–12 months. "
                "If asymmetry is weak, say so — do not invent a hero story."
            ),
            "5_trade_plan": (
                f"Write The Trade Plan / Kill Criteria for {t} for a paper-first bot. "
                "Decision must be one of: go | wait | pass. "
                "Include: setup_type (long equity swing | long call debit | long put "
                "debit | watch only | avoid), concrete entry conditions, invalidation/"
                "stop (what proves thesis wrong), size_risk aligned to project rails "
                f"(risk_per_trade_pct={config.risk.risk_per_trade_pct}, "
                f"max_daily_loss_pct={config.risk.max_daily_loss_pct}, "
                f"max_open_positions={config.risk.max_open_positions}), "
                "time_stop, options_note for 7–31 DTE long premium if relevant, "
                "and daily_focus_fit (yes/no/maybe + why). "
                "This is advisory — never claim an order was placed."
            ),
        },
        "schema": {
            "verdict": "pass|caution|fail",
            "conviction": 0.0,
            "one_liner": "string",
            "deep_dive": {
                "business_model": "string",
                "moat": "string",
                "competitors": ["str"],
                "catalysts": "string",
                "asymmetry": "string",
            },
            "peer_comparison": {
                "summary": "string",
                "table": [
                    {
                        "ticker": "STR",
                        "ps_ttm": "num or str",
                        "ps_fwd": "num or str",
                        "p_fcf": "num or str",
                        "ev_ebitda": "num or str",
                        "gross_margin": "num or str",
                        "rev_growth_yoy": "num or str",
                        "value_growth_score": "num or str",
                    }
                ],
                "cheapest_growth": "string",
            },
            "bear_case": {
                "red_flags": [
                    {
                        "title": "string",
                        "severity": "high|medium|low",
                        "detail": "string",
                        "source": "string",
                    }
                ],
                "checklist": {
                    "customer_concentration": "string",
                    "margin_compression": "string",
                    "unscheduled_insider_selling": "string",
                    "gaap_vs_nongaap_gap": "string",
                    "guidance_cuts_12m": "string",
                },
            },
            "bull_case": {
                "summary": "string",
                "variant_perception": "string",
                "bull_points": [
                    {
                        "title": "string",
                        "impact": "high|medium|low",
                        "detail": "string",
                    }
                ],
                "scenarios": {
                    "bull": "string",
                    "base": "string",
                    "bear": "string",
                },
                "rerate_triggers": "string",
            },
            "trade_plan": {
                "decision": "go|wait|pass",
                "setup_type": "string",
                "entry": "string",
                "invalidation": "string",
                "size_risk": "string",
                "time_stop": "string",
                "options_note": "string",
                "daily_focus_fit": "string",
            },
        },
        "tape_context": tape,
        "project_rails": {
            "purpose": "Quality gate before daily_focus — not an entry signal",
            "horizon_note": (
                "Bot also trades 7–31 DTE long-premium options; note if catalysts "
                "fall inside that window"
            ),
            "risk": {
                "risk_per_trade_pct": config.risk.risk_per_trade_pct,
                "max_daily_loss_pct": config.risk.max_daily_loss_pct,
                "max_open_positions": config.risk.max_open_positions,
                "reward_risk_ratio": config.risk.reward_risk_ratio,
            },
            "options": {
                "min_dte": config.option_min_dte,
                "max_dte": config.option_max_dte,
                "max_open_options": config.max_open_options,
                "take_profit_pct_low": config.option_take_profit_pct_low,
                "stop_loss_pct": config.option_stop_loss_pct,
                "exit_dte": config.option_exit_dte,
            },
        },
    }

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, indent=2)},
            ],
            temperature=0.25,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        return heuristic_deep_research(
            t,
            peer_list,
            config,
            quote_provider=quote_provider,
            reason=f"LLM call failed ({e!r}); fell back to heuristic.",
        )

    parsed = _extract_json_block(raw) or {}
    dd = parsed.get("deep_dive") if isinstance(parsed.get("deep_dive"), dict) else {}
    pc = (
        parsed.get("peer_comparison")
        if isinstance(parsed.get("peer_comparison"), dict)
        else {}
    )
    bc = parsed.get("bear_case") if isinstance(parsed.get("bear_case"), dict) else {}
    bu = parsed.get("bull_case") if isinstance(parsed.get("bull_case"), dict) else {}
    tp = parsed.get("trade_plan") if isinstance(parsed.get("trade_plan"), dict) else {}
    if tp:
        tp = dict(tp)
        tp["decision"] = _normalize_trade_decision(tp.get("decision"))

    try:
        conv = float(parsed.get("conviction", 0.5))
    except (TypeError, ValueError):
        conv = 0.5
    conv = max(0.0, min(1.0, conv))

    return DeepResearchMemo(
        ts=datetime.now(timezone.utc).isoformat(),
        ticker=t,
        peers=peer_list,
        mode="llm",
        model=model,
        verdict=_normalize_verdict(str(parsed.get("verdict", "caution"))),
        conviction=conv,
        one_liner=str(parsed.get("one_liner") or raw[:200]),
        deep_dive=dd or {},
        peer_comparison=pc or {},
        bear_case=bc or {},
        bull_case=bu or {},
        trade_plan=tp or {},
        tape_context=tape,
        raw_text=raw,
        notes=[
            "LLM deep-research is advisory; verify critical numbers in filings.",
            "5-section stack: Deep Dive · Peer · Bear · Bull/Variant · Trade Plan.",
            "Does not write daily_focus — promote survivors manually or via research --apply-daily.",
            f"Peers used: {', '.join(peer_list)}.",
        ],
    )


def write_deep_research_memo(
    memo: DeepResearchMemo,
    out_dir: Path,
) -> DeepResearchMemo:
    """Write ticker memo + latest pointer under out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t = memo.ticker.upper()
    day = datetime.now(ET).date().isoformat()
    stem = f"{t}_{day}"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    latest_json = out_dir / f"{t}_latest.json"
    latest_md = out_dir / f"{t}_latest.md"
    index_path = out_dir / "index.json"

    payload = memo.to_dict()
    # paths filled after write
    json_path.write_text(json.dumps(payload, indent=2))
    md_path.write_text(memo.to_markdown())
    latest_json.write_text(json.dumps(payload, indent=2))
    latest_md.write_text(memo.to_markdown())

    # Update simple index of last run per ticker
    index: dict[str, Any] = {}
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text())
        except json.JSONDecodeError:
            index = {}
    index[t] = {
        "ts": memo.ts,
        "date": day,
        "verdict": memo.verdict,
        "conviction": memo.conviction,
        "mode": memo.mode,
        "peers": memo.peers,
        "one_liner": memo.one_liner,
        "md": str(md_path),
        "json": str(json_path),
        "latest_md": str(latest_md),
        "latest_json": str(latest_json),
    }
    index_path.write_text(json.dumps(index, indent=2))

    memo.paths = {
        "md": str(md_path),
        "json": str(json_path),
        "latest_md": str(latest_md),
        "latest_json": str(latest_json),
        "index": str(index_path),
    }
    # rewrite with paths populated
    payload = memo.to_dict()
    json_path.write_text(json.dumps(payload, indent=2))
    latest_json.write_text(json.dumps(payload, indent=2))
    md_path.write_text(memo.to_markdown())
    latest_md.write_text(memo.to_markdown())
    return memo


def run_deep_research(
    config: AppConfig,
    ticker: str,
    *,
    peers: list[str] | None = None,
    use_llm: bool = True,
    out_dir: Path | None = None,
    quote_provider: QuoteProvider | None = None,
) -> DeepResearchMemo:
    """
    Run 5-section deep research for one ticker and write under logs/deep_research/.
    """
    t = ticker.upper().strip()
    if not t or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", t):
        raise ValueError(f"Invalid ticker: {ticker!r}")

    peer_list = resolve_peers(t, peers)
    out_dir = out_dir or (config.config_path.parent / "logs" / "deep_research")

    if use_llm:
        memo = llm_deep_research(
            t, peer_list, config, quote_provider=quote_provider
        )
    else:
        memo = heuristic_deep_research(
            t,
            peer_list,
            config,
            quote_provider=quote_provider,
            reason="--no-llm / heuristic requested",
        )

    return write_deep_research_memo(memo, out_dir)
