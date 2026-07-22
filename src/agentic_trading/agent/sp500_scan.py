"""
S&P 500 scan → top-N rank → optional deep-research gate.

Pipeline:
  1. Load S&P 500 universe (remote / cache / fallback sample)
  2. Score liquid names by RS vs SPY + dollar volume
  3. Keep top-N by market bias (call=strongest RS, put=weakest)
  4. Optionally run deep-research on top deep_n survivors
  5. Write logs/sp500_scan/ — never places orders

Not Russell 3000: small/illiquid names are out of scope for this bot.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agentic_trading.agent.deep_research import DeepResearchMemo, run_deep_research
from agentic_trading.agent.sp500_universe import INDEX_SKIP, load_sp500_universe
from agentic_trading.config import AppConfig
from agentic_trading.market.quotes import FixtureQuoteProvider, QuoteProvider

ET = ZoneInfo("America/New_York")

# Liquidity floor: avg daily dollar volume over lookback (USD)
DEFAULT_MIN_DOLLAR_VOL = 20_000_000.0
DEFAULT_RS_LOOKBACK = 10
DEFAULT_TOP_N = 10
DEFAULT_DEEP_N = 3


@dataclass
class ScanRow:
    symbol: str
    price: float | None
    rs_vs_spy: float | None  # percent
    range_change_pct: float | None
    avg_dollar_volume: float | None
    avg_volume: float | None
    bars: int = 0
    liquid: bool = False
    score: float = 0.0  # ranking score (higher = better for call bias)
    rank: int | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Sp500ScanResult:
    ts: str
    universe_source: str
    universe_count: int
    scored_count: int
    liquid_count: int
    bias: str
    top_n: int
    deep_n: int
    min_dollar_vol: float
    rs_lookback: int
    quote_source: str
    market: dict[str, Any]
    top: list[ScanRow] = field(default_factory=list)
    deep_memos: list[dict[str, Any]] = field(default_factory=list)
    survivors: list[str] = field(default_factory=list)  # pass/caution after deep
    notes: list[str] = field(default_factory=list)
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "universe_source": self.universe_source,
            "universe_count": self.universe_count,
            "scored_count": self.scored_count,
            "liquid_count": self.liquid_count,
            "bias": self.bias,
            "top_n": self.top_n,
            "deep_n": self.deep_n,
            "min_dollar_vol": self.min_dollar_vol,
            "rs_lookback": self.rs_lookback,
            "quote_source": self.quote_source,
            "market": self.market,
            "top": [r.to_dict() for r in self.top],
            "deep_memos": self.deep_memos,
            "survivors": self.survivors,
            "notes": self.notes,
            "paths": self.paths,
        }

    def to_markdown(self) -> str:
        lines = [
            f"# S&P 500 scan — {self.ts}",
            "",
            f"**Universe:** {self.universe_count} ({self.universe_source}) · "
            f"scored {self.scored_count} · liquid {self.liquid_count}",
            f"**Bias:** `{self.bias}` · top {self.top_n} · deep-research {self.deep_n}",
            f"**Liquidity floor:** ${self.min_dollar_vol:,.0f} avg dollar volume / day",
            f"**RS lookback:** {self.rs_lookback} bars · quotes `{self.quote_source}`",
            "",
            "> Scan + optional deep-research only. Does **not** place orders. "
            "Promote `survivors` via `research --apply-daily` if desired.",
            "",
            "## Market",
            "",
            f"- SPY: price={self.market.get('price')} · "
            f"range Δ={self.market.get('range_change_pct')}%",
            "",
            "## Top names",
            "",
            "| rank | symbol | price | RS vs SPY % | range % | $ vol (avg) | score |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for r in self.top:
            dvol = (
                f"{r.avg_dollar_volume:,.0f}"
                if r.avg_dollar_volume is not None
                else "—"
            )
            lines.append(
                f"| {r.rank} | **{r.symbol}** | {r.price if r.price is not None else '—'} | "
                f"{r.rs_vs_spy if r.rs_vs_spy is not None else '—'} | "
                f"{r.range_change_pct if r.range_change_pct is not None else '—'} | "
                f"{dvol} | {r.score:.3f} |"
            )
        lines += ["", "## Deep research", ""]
        if not self.deep_memos:
            lines.append("_(skipped — pass `--deep-research` to run top-N memos)_")
            lines.append("")
        else:
            for m in self.deep_memos:
                lines.append(
                    f"- **{m.get('ticker')}** — `{m.get('verdict')}` "
                    f"(conviction {m.get('conviction')}) — {m.get('one_liner')}"
                )
                if m.get("latest_md"):
                    lines.append(f"  - memo: `{m['latest_md']}`")
            lines.append("")
            lines.append(
                f"**Survivors (pass/caution):** "
                f"{', '.join(self.survivors) or '(none)'}"
            )
            lines.append("")

        lines += ["## Notes", ""]
        for n in self.notes:
            lines.append(f"- {n}")
        if self.paths:
            lines.append(f"- Wrote: {self.paths.get('md')} · {self.paths.get('json')}")
        lines.append("")
        return "\n".join(lines)


def _closes_and_volumes_from_history(
    bars: list[Any],
) -> tuple[list[float], list[float]]:
    closes: list[float] = []
    vols: list[float] = []
    for b in bars or []:
        c = float(getattr(b, "close", 0) or 0)
        if c <= 0:
            continue
        closes.append(c)
        # Fixture/Yahoo Bar may not have volume — treat missing as 0
        v = float(getattr(b, "volume", 0) or 0)
        vols.append(max(0.0, v))
    return closes, vols


def _score_from_provider(
    symbols: list[str],
    config: AppConfig,
    provider: QuoteProvider,
    *,
    rs_lookback: int,
    min_dollar_vol: float,
) -> tuple[list[ScanRow], dict[str, Any]]:
    """Score symbols using QuoteProvider history (fixture or yahoo)."""
    lookback = max(rs_lookback + 5, config.strategy.lookback_bars, 20)
    mkt = (config.strategy.market_symbol or "SPY").upper()
    all_syms = list(dict.fromkeys([mkt] + symbols))

    if isinstance(provider, FixtureQuoteProvider):
        for _ in range(max(20, lookback)):
            provider.advance(1)

    quotes = provider.get_quotes(all_syms)
    history = provider.get_history(all_syms, lookback)

    mkt_bars = history.get(mkt) or []
    mkt_closes, _ = _closes_and_volumes_from_history(mkt_bars)
    mkt_q = quotes.get(mkt)
    market = {
        "symbol": mkt,
        "price": round(mkt_q.price, 4) if mkt_q else None,
        "range_change_pct": (
            round((mkt_closes[-1] / mkt_closes[0] - 1.0) * 100, 3)
            if len(mkt_closes) >= 2
            else None
        ),
    }

    rows: list[ScanRow] = []
    n = rs_lookback
    for sym in symbols:
        su = sym.upper()
        if su in INDEX_SKIP or su == mkt:
            continue
        bars = history.get(su) or []
        closes, vols = _closes_and_volumes_from_history(bars)
        q = quotes.get(su)
        price = float(q.price) if q and q.price > 0 else (closes[-1] if closes else None)

        rs = None
        if len(closes) >= n and len(mkt_closes) >= n and mkt_closes[-n] > 0 and closes[-n] > 0:
            rs = (closes[-1] / closes[-n] - 1.0) - (mkt_closes[-1] / mkt_closes[-n] - 1.0)
            rs = round(rs * 100, 3)

        chg = None
        if len(closes) >= 2 and closes[0] > 0:
            chg = round((closes[-1] / closes[0] - 1.0) * 100, 3)

        # Dollar volume estimate: mean(close * volume). Fixture often has vol=0 —
        # treat missing volume as liquid if we have a price (fixture path).
        dvols: list[float] = []
        for i, c in enumerate(closes):
            v = vols[i] if i < len(vols) else 0.0
            if v > 0:
                dvols.append(c * v)
        avg_dvol = sum(dvols) / len(dvols) if dvols else None
        avg_vol = (sum(vols) / len(vols)) if vols and any(vols) else None

        fixture = isinstance(provider, FixtureQuoteProvider)
        if fixture:
            liquid = price is not None and price > 0 and len(closes) >= n
            # Synthetic dollar vol for ranking display
            if avg_dvol is None and price:
                avg_dvol = price * 5_000_000.0
        else:
            liquid = (
                price is not None
                and price > 0
                and len(closes) >= n
                and avg_dvol is not None
                and avg_dvol >= min_dollar_vol
            )

        score = float(rs) if rs is not None else -999.0
        note = ""
        if not liquid and not fixture:
            note = "below liquidity floor or insufficient history"
        rows.append(
            ScanRow(
                symbol=su,
                price=round(price, 4) if price is not None else None,
                rs_vs_spy=rs,
                range_change_pct=chg,
                avg_dollar_volume=round(avg_dvol, 0) if avg_dvol is not None else None,
                avg_volume=round(avg_vol, 0) if avg_vol is not None else None,
                bars=len(closes),
                liquid=liquid,
                score=score,
                notes=note,
            )
        )
    return rows, market


def _series_to_floats(series: Any) -> list[float]:
    try:
        return [float(x) for x in series.dropna().tolist()]
    except Exception:
        return []


def _extract_close_volume(df: Any, symbol: str | None = None) -> tuple[list[float], list[float]]:
    """
    Normalize yfinance frames across column layouts:
      - flat: Close / Volume
      - MultiIndex names Price,Ticker: ('Close', 'SPY')
      - MultiIndex names Ticker,Price: df['AAPL']['Close']
    """
    if df is None or getattr(df, "empty", True):
        return [], []
    cols = df.columns
    # Flat
    if "Close" in cols:
        closes = _series_to_floats(df["Close"])
        volumes = _series_to_floats(df["Volume"]) if "Volume" in cols else []
        return closes, volumes
    # MultiIndex
    if not hasattr(cols, "levels"):
        return [], []
    names = list(cols.names or [])
    # Ticker-first (group_by=ticker multi download)
    try:
        level0 = set(cols.get_level_values(0))
        if symbol and symbol in level0:
            sub = df[symbol]
            if "Close" in sub.columns:
                closes = _series_to_floats(sub["Close"])
                volumes = (
                    _series_to_floats(sub["Volume"]) if "Volume" in sub.columns else []
                )
                return closes, volumes
        # Price-first single download: columns like ('Close','SPY')
        if "Close" in level0:
            close_block = df["Close"]
            if symbol and hasattr(close_block, "columns") and symbol in close_block.columns:
                closes = _series_to_floats(close_block[symbol])
            elif hasattr(close_block, "dropna") and not hasattr(close_block, "columns"):
                closes = _series_to_floats(close_block)
            else:
                # single remaining ticker column
                try:
                    closes = _series_to_floats(close_block.iloc[:, 0])
                except Exception:
                    closes = _series_to_floats(close_block)
            volumes: list[float] = []
            if "Volume" in level0:
                vol_block = df["Volume"]
                if symbol and hasattr(vol_block, "columns") and symbol in vol_block.columns:
                    volumes = _series_to_floats(vol_block[symbol])
                elif hasattr(vol_block, "dropna") and not hasattr(vol_block, "columns"):
                    volumes = _series_to_floats(vol_block)
                else:
                    try:
                        volumes = _series_to_floats(vol_block.iloc[:, 0])
                    except Exception:
                        volumes = []
            return closes, volumes
    except Exception:
        return [], []
    # Last resort: Ticker.history-style already handled; try xs
    try:
        if symbol:
            closes = _series_to_floats(df.xs("Close", axis=1, level=-1)[symbol] if False else df["Close"])
    except Exception:
        pass
    return [], []


def _score_yahoo_batch(
    symbols: list[str],
    config: AppConfig,
    *,
    rs_lookback: int,
    min_dollar_vol: float,
    batch_size: int = 80,
) -> tuple[list[ScanRow], dict[str, Any]]:
    """
    Faster path: yfinance batch download for S&P-scale universes.
    Falls back to empty if yfinance missing.
    """
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return [], {"symbol": "SPY", "price": None, "range_change_pct": None, "error": "no_yfinance"}

    mkt = (config.strategy.market_symbol or "SPY").upper()
    period = "3mo"
    interval = "1d"
    n = rs_lookback

    # Market first — Ticker.history is the most stable shape
    mkt_closes: list[float] = []
    try:
        hist = yf.Ticker(mkt).history(period=period, interval=interval, auto_adjust=True)
        mkt_closes, _ = _extract_close_volume(hist, mkt)
        if not mkt_closes and hist is not None and not getattr(hist, "empty", True):
            if "Close" in hist.columns:
                mkt_closes = _series_to_floats(hist["Close"])
    except Exception:
        mkt_closes = []
    if not mkt_closes:
        try:
            spy = yf.download(
                mkt, period=period, interval=interval, progress=False, auto_adjust=True
            )
            mkt_closes, _ = _extract_close_volume(spy, mkt)
        except Exception:
            mkt_closes = []

    market = {
        "symbol": mkt,
        "price": round(mkt_closes[-1], 4) if mkt_closes else None,
        "range_change_pct": (
            round((mkt_closes[-1] / mkt_closes[0] - 1.0) * 100, 3)
            if len(mkt_closes) >= 2
            else None
        ),
    }

    rows: list[ScanRow] = []
    clean = [s.upper() for s in symbols if s.upper() not in INDEX_SKIP and s.upper() != mkt]
    for i in range(0, len(clean), batch_size):
        batch = clean[i : i + batch_size]
        try:
            data = yf.download(
                tickers=" ".join(batch),
                period=period,
                interval=interval,
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
        except Exception:
            data = None
        if data is None or getattr(data, "empty", True):
            for sym in batch:
                rows.append(
                    ScanRow(
                        symbol=sym,
                        price=None,
                        rs_vs_spy=None,
                        range_change_pct=None,
                        avg_dollar_volume=None,
                        avg_volume=None,
                        liquid=False,
                        score=-999.0,
                        notes="download failed",
                    )
                )
            continue

        for sym in batch:
            closes, volumes = _extract_close_volume(data, sym)
            # Single-ticker batch: frame may be Price-first MultiIndex without ticker level0
            if not closes and len(batch) == 1:
                closes, volumes = _extract_close_volume(data, batch[0])
            if not closes:
                rows.append(
                    ScanRow(
                        symbol=sym,
                        price=None,
                        rs_vs_spy=None,
                        range_change_pct=None,
                        avg_dollar_volume=None,
                        avg_volume=None,
                        liquid=False,
                        score=-999.0,
                        notes="parse failed",
                    )
                )
                continue

            price = closes[-1] if closes else None
            rs = None
            if (
                len(closes) >= n
                and len(mkt_closes) >= n
                and closes[-n] > 0
                and mkt_closes[-n] > 0
            ):
                rs = (closes[-1] / closes[-n] - 1.0) - (
                    mkt_closes[-1] / mkt_closes[-n] - 1.0
                )
                rs = round(rs * 100, 3)
            chg = (
                round((closes[-1] / closes[0] - 1.0) * 100, 3)
                if len(closes) >= 2 and closes[0] > 0
                else None
            )
            k = min(len(closes), len(volumes), 20) if volumes else 0
            dvols = []
            if k > 0:
                for j in range(1, k + 1):
                    c = closes[-j]
                    v = volumes[-j] if j <= len(volumes) else 0.0
                    if c > 0 and v > 0:
                        dvols.append(c * v)
            avg_dvol = sum(dvols) / len(dvols) if dvols else None
            avg_vol = sum(volumes[-k:]) / k if volumes and k > 0 else None
            liquid = (
                price is not None
                and price > 0
                and len(closes) >= n
                and avg_dvol is not None
                and avg_dvol >= min_dollar_vol
            )
            # If SPY missing, still rank on absolute range change so top is non-empty
            score = float(rs) if rs is not None else (
                float(chg) if chg is not None else -999.0
            )
            rows.append(
                ScanRow(
                    symbol=sym,
                    price=round(price, 4) if price is not None else None,
                    rs_vs_spy=rs,
                    range_change_pct=chg,
                    avg_dollar_volume=round(avg_dvol, 0) if avg_dvol is not None else None,
                    avg_volume=round(avg_vol, 0) if avg_vol is not None else None,
                    bars=len(closes),
                    liquid=liquid,
                    score=score,
                    notes="" if liquid else "below liquidity floor or thin history",
                )
            )
    return rows, market


def rank_top(
    rows: list[ScanRow],
    *,
    bias: str,
    top_n: int,
) -> list[ScanRow]:
    bias = (bias or "call").strip().lower()
    if bias not in ("call", "put", "hold"):
        bias = "call"
    # Prefer RS; fall back to range_change if SPY series missing
    liquid = [
        r
        for r in rows
        if r.liquid and (r.rs_vs_spy is not None or r.range_change_pct is not None)
    ]

    def _metric(r: ScanRow) -> float:
        if r.rs_vs_spy is not None:
            return float(r.rs_vs_spy)
        return float(r.range_change_pct or 0.0)

    if bias == "put":
        ordered = sorted(liquid, key=_metric)  # weakest first
    else:
        # call and hold: strongest RS (hold still shows leaders for watch)
        ordered = sorted(liquid, key=_metric, reverse=True)
    top_n = max(0, int(top_n))
    out = ordered[:top_n]
    for i, r in enumerate(out, 1):
        r.rank = i
        m = _metric(r)
        r.score = -m if bias == "put" else m
    return out


def write_scan_result(result: Sp500ScanResult, out_dir: Path) -> Sp500ScanResult:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(ET).date().isoformat()
    json_path = out_dir / f"scan_{day}.json"
    md_path = out_dir / f"scan_{day}.md"
    latest_json = out_dir / "latest.json"
    latest_md = out_dir / "latest.md"
    shortlist_path = out_dir / "shortlist.json"

    payload = result.to_dict()
    md = result.to_markdown()
    json_path.write_text(json.dumps(payload, indent=2))
    md_path.write_text(md)
    latest_json.write_text(json.dumps(payload, indent=2))
    latest_md.write_text(md)
    shortlist_path.write_text(
        json.dumps(
            {
                "date": day,
                "ts": result.ts,
                "bias": result.bias,
                "top": [r.symbol for r in result.top],
                "survivors": result.survivors,
                "deep_n": result.deep_n,
            },
            indent=2,
        )
    )
    result.paths = {
        "json": str(json_path),
        "md": str(md_path),
        "latest_json": str(latest_json),
        "latest_md": str(latest_md),
        "shortlist": str(shortlist_path),
    }
    # rewrite with paths
    payload = result.to_dict()
    json_path.write_text(json.dumps(payload, indent=2))
    latest_json.write_text(json.dumps(payload, indent=2))
    md_path.write_text(result.to_markdown())
    latest_md.write_text(result.to_markdown())
    return result


def run_sp500_scan(
    config: AppConfig,
    *,
    top_n: int = DEFAULT_TOP_N,
    deep_n: int = 0,
    deep_research: bool = False,
    use_llm: bool = True,
    bias: str = "call",
    min_dollar_vol: float = DEFAULT_MIN_DOLLAR_VOL,
    rs_lookback: int = DEFAULT_RS_LOOKBACK,
    quote_source: str = "yahoo",
    quote_provider: QuoteProvider | None = None,
    out_dir: Path | None = None,
    deep_out_dir: Path | None = None,
    allow_remote_universe: bool = True,
    universe_override: list[str] | None = None,
) -> Sp500ScanResult:
    """
    Scan S&P 500 (or override list), rank top_n, optionally deep-research deep_n.

    deep_research=True runs memos on first min(deep_n, top_n) of the ranked list.
    """
    root = config.config_path.parent
    out_dir = out_dir or (root / "logs" / "sp500_scan")
    deep_out_dir = deep_out_dir or (root / "logs" / "deep_research")
    cache_path = root / "logs" / "sp500_scan" / "universe_cache.json"

    if universe_override:
        universe = [s.upper().strip() for s in universe_override if s.strip()]
        uni_source = "override"
    else:
        universe, uni_source = load_sp500_universe(
            cache_path=cache_path,
            prefer_cache=True,
            allow_remote=allow_remote_universe,
        )

    src = (quote_source or "yahoo").strip().lower()
    provider = quote_provider
    notes: list[str] = []

    if src == "fixture":
        provider = provider or FixtureQuoteProvider()
        rows, market = _score_from_provider(
            universe,
            config,
            provider,
            rs_lookback=rs_lookback,
            min_dollar_vol=min_dollar_vol,
        )
        notes.append("Fixture quotes — synthetic RS/liquidity; use --quotes yahoo for real tape.")
    elif provider is not None:
        rows, market = _score_from_provider(
            universe,
            config,
            provider,
            rs_lookback=rs_lookback,
            min_dollar_vol=min_dollar_vol,
        )
    else:
        # Prefer batch yfinance for large universes
        rows, market = _score_yahoo_batch(
            universe,
            config,
            rs_lookback=rs_lookback,
            min_dollar_vol=min_dollar_vol,
        )
        if not rows or market.get("error") == "no_yfinance":
            notes.append("yfinance unavailable — falling back to QuoteProvider path.")
            provider = build_safe_provider(src)
            rows, market = _score_from_provider(
                universe,
                config,
                provider,
                rs_lookback=rs_lookback,
                min_dollar_vol=min_dollar_vol,
            )
        else:
            notes.append("Scored via yfinance daily batch download.")

    liquid_count = sum(1 for r in rows if r.liquid)
    top = rank_top(rows, bias=bias, top_n=top_n)

    deep_n_eff = 0
    deep_memos: list[dict[str, Any]] = []
    survivors: list[str] = []

    if deep_research:
        deep_n_eff = max(0, min(int(deep_n or DEFAULT_DEEP_N), len(top)))
        # Fixture-friendly quote provider for deep tape context
        deep_qp = provider
        if deep_qp is None:
            deep_qp = build_safe_provider(src)
        for r in top[:deep_n_eff]:
            try:
                memo: DeepResearchMemo = run_deep_research(
                    config,
                    r.symbol,
                    use_llm=use_llm,
                    out_dir=deep_out_dir,
                    quote_provider=deep_qp
                    if isinstance(deep_qp, QuoteProvider)
                    else FixtureQuoteProvider(),
                )
                entry = {
                    "ticker": memo.ticker,
                    "verdict": memo.verdict,
                    "conviction": memo.conviction,
                    "one_liner": memo.one_liner,
                    "mode": memo.mode,
                    "latest_md": memo.paths.get("latest_md"),
                    "latest_json": memo.paths.get("latest_json"),
                }
                deep_memos.append(entry)
                if memo.verdict in ("pass", "caution"):
                    survivors.append(memo.ticker)
            except Exception as e:  # noqa: BLE001
                deep_memos.append(
                    {
                        "ticker": r.symbol,
                        "verdict": "fail",
                        "conviction": 0.0,
                        "one_liner": f"deep-research error: {e!r}",
                        "mode": "error",
                    }
                )
        notes.append(
            f"Deep-research ran on {deep_n_eff} names "
            f"({'llm' if use_llm else 'heuristic'}); "
            f"survivors={survivors or '(none)'}."
        )
    else:
        notes.append("Deep-research skipped. Re-run with --deep-research --deep-n 3.")

    bias_n = (bias or "call").strip().lower()
    if bias_n == "hold":
        notes.append("Bias=hold: top list is strongest RS for watch only — no new risk implied.")
    elif bias_n == "put":
        notes.append("Bias=put: top list is weakest liquid RS (put/short watch — not long equity).")
    else:
        notes.append("Bias=call: top list is strongest liquid RS long candidates.")

    notes.append(
        "Does not write daily_focus. Optional: promote survivors manually "
        "or wire research --apply-daily after review."
    )

    result = Sp500ScanResult(
        ts=datetime.now(timezone.utc).isoformat(),
        universe_source=uni_source,
        universe_count=len(universe),
        scored_count=len(rows),
        liquid_count=liquid_count,
        bias=bias_n,
        top_n=top_n,
        deep_n=deep_n_eff,
        min_dollar_vol=min_dollar_vol,
        rs_lookback=rs_lookback,
        quote_source=src,
        market=market,
        top=top,
        deep_memos=deep_memos,
        survivors=survivors,
        notes=notes,
    )
    return write_scan_result(result, out_dir)


def build_safe_provider(source: str) -> QuoteProvider:
    from agentic_trading.market.quotes import build_quote_provider

    try:
        return build_quote_provider(source)
    except Exception:
        return FixtureQuoteProvider()
