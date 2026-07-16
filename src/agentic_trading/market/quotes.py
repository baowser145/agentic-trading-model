from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Literal

from agentic_trading.models import Bar, Quote

QuoteSource = Literal["fixture", "live"]


class QuoteProvider(ABC):
    @abstractmethod
    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        ...

    @abstractmethod
    def get_history(self, symbols: list[str], lookback: int) -> dict[str, list[Bar]]:
        ...


class FixtureQuoteProvider(QuoteProvider):
    """
    Deterministic synthetic prices for paper mode / tests.

    Each call advances a step index so setups can be exercised.
    Base prices approximate liquid ETFs / mega-caps; phase offsets desync symbols
    so the selector has differentiated relative-strength scores.
    """

    BASE = {
        "SPY": 500.0,
        "QQQ": 450.0,
        "IWM": 200.0,
        "DIA": 390.0,
        "AAPL": 190.0,
        "MSFT": 420.0,
        "NVDA": 120.0,
        "AMZN": 185.0,
        "META": 510.0,
        "GOOGL": 175.0,
        "TSLA": 250.0,
        "AMD": 160.0,
        "AVGO": 180.0,
        "NFLX": 700.0,
        "CRM": 280.0,
        "ORCL": 140.0,
        "JPM": 210.0,
        "BAC": 40.0,
        "XLF": 45.0,
        "XLE": 90.0,
        "XLK": 230.0,
        "SMH": 250.0,
        "COST": 900.0,
        "WMT": 80.0,
        "JNJ": 155.0,
        "UNH": 520.0,
        "V": 290.0,
        "MA": 480.0,
    }

    # Per-symbol phase so paths are not identical
    PHASE = {
        "SPY": 0,
        "QQQ": 1,
        "IWM": 2,
        "AAPL": 3,
        "MSFT": 4,
        "NVDA": 5,
        "AMZN": 6,
        "META": 7,
        "GOOGL": 8,
        "TSLA": 9,
        "AMD": 10,
        "AVGO": 11,
        "NFLX": 12,
        "CRM": 13,
        "JPM": 14,
        "XLK": 15,
        "SMH": 16,
    }

    def __init__(
        self,
        series: dict[str, list[float]] | None = None,
        start_step: int = 0,
    ) -> None:
        self._series = series or {}
        self._step = start_step

    def advance(self, n: int = 1) -> None:
        self._step += n

    def _price_at(self, symbol: str, idx: int) -> float:
        if symbol in self._series and self._series[symbol]:
            seq = self._series[symbol]
            return float(seq[min(idx, len(seq) - 1)])
        base = self.BASE.get(symbol.upper(), 100.0)
        phase = self.PHASE.get(symbol.upper(), hash(symbol) % 11)
        # Gentle trend + oscillation; phase shifts peaks so RS differs
        t = idx + phase
        return base * (1.0 + 0.0012 * t + 0.003 * ((t % 9) - 4) + 0.0004 * phase)

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        now = datetime.now(timezone.utc)
        out: dict[str, Quote] = {}
        for sym in symbols:
            px = self._price_at(sym, self._step)
            out[sym] = Quote(symbol=sym, price=px, ts=now)
        return out

    def get_history(self, symbols: list[str], lookback: int) -> dict[str, list[Bar]]:
        now = datetime.now(timezone.utc)
        out: dict[str, list[Bar]] = {}
        lookback = max(lookback, 1)
        for sym in symbols:
            bars: list[Bar] = []
            start = max(0, self._step - lookback + 1)
            for i in range(start, self._step + 1):
                bars.append(
                    Bar(symbol=sym, close=self._price_at(sym, i), ts=now)
                )
            while len(bars) < lookback:
                i = self._step - len(bars)
                bars.insert(
                    0, Bar(symbol=sym, close=self._price_at(sym, max(i, 0)), ts=now)
                )
            out[sym] = bars[-lookback:]
        return out


class YahooLiveQuoteProvider(QuoteProvider):
    """
    Public Yahoo Finance quotes via yfinance (delayed; not broker fills).

    Install: pip install yfinance
    Use for paper multi-day sessions — never for Agentic live order pricing alone.
    """

    def __init__(self, history_period: str = "5d", history_interval: str = "5m") -> None:
        self.history_period = history_period
        self.history_interval = history_interval
        self._yf = None

    def _client(self):
        if self._yf is None:
            try:
                import yfinance as yf  # type: ignore
            except ImportError as e:
                raise ImportError(
                    "YahooLiveQuoteProvider requires yfinance. "
                    "Install with: pip install yfinance"
                ) from e
            self._yf = yf
        return self._yf

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        yf = self._client()
        now = datetime.now(timezone.utc)
        out: dict[str, Quote] = {}
        # batch download last close-ish price
        uniq = sorted({s.upper().strip() for s in symbols if s})
        if not uniq:
            return out
        try:
            data = yf.download(
                tickers=" ".join(uniq),
                period="1d",
                interval="1m",
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
        except Exception:
            data = None

        for sym in uniq:
            px: float | None = None
            try:
                if data is not None and not getattr(data, "empty", True):
                    if len(uniq) == 1:
                        col = data["Close"] if "Close" in data.columns else None
                        if col is not None and len(col.dropna()):
                            px = float(col.dropna().iloc[-1])
                    else:
                        # MultiIndex columns (ticker, field)
                        if hasattr(data.columns, "levels") and sym in set(
                            data.columns.get_level_values(0)
                        ):
                            series = data[sym]["Close"].dropna()
                            if len(series):
                                px = float(series.iloc[-1])
                if px is None or px <= 0:
                    t = yf.Ticker(sym)
                    info = getattr(t, "fast_info", None)
                    if info is not None:
                        for key in ("last_price", "lastPrice", "regular_market_price"):
                            v = None
                            try:
                                v = info[key] if hasattr(info, "__getitem__") else getattr(info, key, None)
                            except Exception:
                                v = getattr(info, key, None)
                            if v is not None and float(v) > 0:
                                px = float(v)
                                break
                    if px is None or px <= 0:
                        hist = t.history(period="1d", interval="1m")
                        if hist is not None and len(hist) and "Close" in hist.columns:
                            px = float(hist["Close"].dropna().iloc[-1])
            except Exception:
                px = None
            if px is not None and px > 0:
                out[sym] = Quote(symbol=sym, price=float(px), ts=now)
        return out

    def get_history(self, symbols: list[str], lookback: int) -> dict[str, list[Bar]]:
        yf = self._client()
        now = datetime.now(timezone.utc)
        out: dict[str, list[Bar]] = {}
        lookback = max(lookback, 1)
        uniq = sorted({s.upper().strip() for s in symbols if s})
        for sym in uniq:
            bars: list[Bar] = []
            try:
                t = yf.Ticker(sym)
                hist = t.history(
                    period=self.history_period,
                    interval=self.history_interval,
                    auto_adjust=True,
                )
                if hist is not None and len(hist) and "Close" in hist.columns:
                    closes = [float(x) for x in hist["Close"].dropna().tolist()]
                    for c in closes[-lookback:]:
                        if c > 0:
                            bars.append(Bar(symbol=sym, close=c, ts=now))
            except Exception:
                bars = []
            # pad with last known if short (strategy needs min history)
            if bars and len(bars) < lookback:
                pad = bars[0]
                bars = [pad] * (lookback - len(bars)) + bars
            out[sym] = bars
        return out


def build_quote_provider(source: str = "fixture") -> QuoteProvider:
    src = (source or "fixture").strip().lower()
    if src in ("live", "yahoo", "yfinance"):
        return YahooLiveQuoteProvider()
    return FixtureQuoteProvider()
