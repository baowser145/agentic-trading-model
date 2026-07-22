"""Historical underlying bars for options backtests."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DailyBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def _parse_day(s: str | date | datetime) -> date:
    if isinstance(s, date) and not isinstance(s, datetime):
        return s
    if isinstance(s, datetime):
        return s.date()
    return date.fromisoformat(str(s)[:10])


def synthetic_bars(
    symbol: str,
    *,
    n: int = 400,
    start: date | None = None,
    start_price: float = 100.0,
    drift: float = 0.0003,
    vol: float = 0.015,
    seed: int = 42,
) -> list[DailyBar]:
    """Deterministic geometric random walk for offline tests (no network)."""
    # Simple LCG for reproducibility without numpy RNG deps in tests
    state = seed + sum(ord(c) for c in symbol.upper()) * 17

    def rnd() -> float:
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF

    start = start or (date.today() - timedelta(days=int(n * 1.5)))
    bars: list[DailyBar] = []
    px = float(start_price)
    d = start
    i = 0
    while len(bars) < n:
        # skip weekends
        if d.weekday() < 5:
            # Box-Muller-ish from two uniforms
            u1 = max(1e-9, rnd())
            u2 = rnd()
            z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
            ret = drift + vol * z
            o = px
            c = max(0.5, px * (1.0 + ret))
            h = max(o, c) * (1.0 + 0.003 * rnd())
            l = min(o, c) * (1.0 - 0.003 * rnd())
            bars.append(
                DailyBar(
                    day=d,
                    open=round(o, 4),
                    high=round(h, 4),
                    low=round(l, 4),
                    close=round(c, 4),
                    volume=1_000_000.0,
                )
            )
            px = c
            i += 1
        d += timedelta(days=1)
    return bars


def load_cache(path: Path) -> dict[str, list[DailyBar]] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    out: dict[str, list[DailyBar]] = {}
    for sym, rows in (raw.get("symbols") or {}).items():
        bars = []
        for r in rows:
            bars.append(
                DailyBar(
                    day=_parse_day(r["day"]),
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=float(r.get("volume") or 0),
                )
            )
        bars.sort(key=lambda b: b.day)
        out[str(sym).upper()] = bars
    return out or None


def save_cache(path: Path, data: dict[str, list[DailyBar]], meta: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "meta": meta or {},
        "symbols": {
            sym: [
                {
                    "day": b.day.isoformat(),
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                for b in bars
            ]
            for sym, bars in data.items()
        },
    }
    path.write_text(json.dumps(payload) + "\n")


def fetch_yfinance(
    symbols: list[str],
    *,
    start: str | date = "2022-01-01",
    end: str | date | None = None,
    cache_path: Path | None = None,
) -> dict[str, list[DailyBar]]:
    """Download daily OHLCV via yfinance. Uses cache when present and fresh enough."""
    symbols = [s.upper().strip() for s in symbols]
    start_s = start.isoformat() if isinstance(start, date) else str(start)
    end_s = None
    if end is not None:
        end_s = end.isoformat() if isinstance(end, date) else str(end)

    if cache_path and cache_path.is_file():
        cached = load_cache(cache_path)
        if cached and all(s in cached and len(cached[s]) > 50 for s in symbols):
            return {s: cached[s] for s in symbols}

    try:
        import yfinance as yf
    except ImportError as e:
        raise ImportError(
            "yfinance required for live historical data. "
            "pip install yfinance  OR use --synthetic for offline bars."
        ) from e

    out: dict[str, list[DailyBar]] = {}
    for sym in symbols:
        t = yf.Ticker(sym)
        hist = t.history(start=start_s, end=end_s, auto_adjust=True)
        if hist is None or len(hist) == 0:
            continue
        bars: list[DailyBar] = []
        for idx, row in hist.iterrows():
            try:
                day = idx.date() if hasattr(idx, "date") else _parse_day(str(idx))
            except Exception:
                continue
            bars.append(
                DailyBar(
                    day=day,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row.get("Volume") or 0),
                )
            )
        bars.sort(key=lambda b: b.day)
        if bars:
            out[sym] = bars

    if cache_path and out:
        save_cache(cache_path, out, meta={"start": start_s, "end": end_s, "symbols": symbols})
    return out


def align_calendar(series: dict[str, list[DailyBar]]) -> list[date]:
    """Intersection of trading days across all symbols."""
    if not series:
        return []
    sets = [set(b.day for b in bars) for bars in series.values()]
    common = sets[0]
    for s in sets[1:]:
        common &= s
    return sorted(common)


def closes_up_to(bars: list[DailyBar], day: date) -> list[float]:
    return [b.close for b in bars if b.day <= day]


def bar_on(bars: list[DailyBar], day: date) -> DailyBar | None:
    for b in bars:
        if b.day == day:
            return b
    return None
