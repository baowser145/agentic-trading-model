from __future__ import annotations

from abc import ABC, abstractmethod

from agentic_trading.models import Fill, OrderIntent, PortfolioSnapshot


class Broker(ABC):
    @abstractmethod
    def snapshot(self) -> PortfolioSnapshot:
        ...

    @abstractmethod
    def execute(self, intent: OrderIntent, ref_price: float) -> Fill | None:
        ...

    @abstractmethod
    def mark_to_market(self, prices: dict[str, float]) -> PortfolioSnapshot:
        ...
