from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from agentic_trading.models import Bar, Quote


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

    Each call advances a step index so SMA cross can be exercised.
    Base prices are liquid-ETF-like; optional overrides for tests.
    """

    BASE = {"SPY": 500.0, "QQQ": 450.0, "IWM": 200.0}

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
        base = self.BASE.get(symbol, 100.0)
        # Gentle uptrend with small oscillation so SMA eventually trails
        return base * (1.0 + 0.001 * idx + 0.002 * ((idx % 7) - 3))

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
            # Ensure at least `lookback` points by padding earlier synthetic
            while len(bars) < lookback:
                i = self._step - len(bars)
                bars.insert(
                    0, Bar(symbol=sym, close=self._price_at(sym, max(i, 0)), ts=now)
                )
            out[sym] = bars[-lookback:]
        return out
