from __future__ import annotations

from abc import ABC, abstractmethod

from agentic_trading.models import Bar, PortfolioSnapshot, Quote, Signal


class Strategy(ABC):
    @abstractmethod
    def generate(
        self,
        quotes: dict[str, Quote],
        history: dict[str, list[Bar]],
        portfolio: PortfolioSnapshot,
    ) -> list[Signal]:
        ...
