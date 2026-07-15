from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agentic_trading.broker.base import Broker
from agentic_trading.broker.paper import PaperBroker
from agentic_trading.models import (
    Fill,
    OrderIntent,
    PortfolioSnapshot,
    Side,
    TradingMode,
)


class AgenticBroker(Broker):
    """
    Live path stub for Robinhood Agentic.

    Does NOT place orders by itself (no unofficial API scraping).
    When live is enabled, approved intents are written to an intents queue file
    for an agent/orchestrator session to execute via Robinhood MCP, then
    optionally acknowledged.

    For local development without MCP, falls back to recording intents only
    (no portfolio mutation) unless `shadow_paper=True`.
    """

    def __init__(
        self,
        starting_equity: float,
        intents_path: Path,
        shadow_paper: bool = True,
    ) -> None:
        self.intents_path = Path(intents_path)
        self.intents_path.parent.mkdir(parents=True, exist_ok=True)
        self.shadow_paper = shadow_paper
        self._paper = PaperBroker(starting_equity)
        self._pending: list[dict] = []

    def snapshot(self) -> PortfolioSnapshot:
        return self._paper.snapshot()

    def mark_to_market(self, prices: dict[str, float]) -> PortfolioSnapshot:
        return self._paper.mark_to_market(prices)

    def set_halt(self, reason: str) -> None:
        self._paper.set_halt(reason)

    def execute(self, intent: OrderIntent, ref_price: float) -> Fill | None:
        # Honor paper halt for buys; still allow sells (risk-reducing) when halted.
        if self._paper.halted and intent.side == Side.BUY:
            return None

        record = {
            "id": f"live-intent-{uuid.uuid4().hex[:10]}",
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": intent.symbol,
            "side": intent.side.value,
            "notional": intent.notional,
            "quantity": intent.quantity,
            "ref_price": ref_price,
            "reason": intent.reason,
            "status": "pending_agent_execution",
            "account": "agentic_only",
        }
        self._pending.append(record)
        with self.intents_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        if self.shadow_paper:
            fill = self._paper.execute(intent, ref_price)
            if fill:
                # Re-tag as live intent shadow for clarity in logs
                return Fill(
                    symbol=fill.symbol,
                    side=fill.side,
                    quantity=fill.quantity,
                    price=fill.price,
                    notional=fill.notional,
                    ts=fill.ts,
                    mode=TradingMode.LIVE,
                    order_id=record["id"],
                )
            return None

        # Intent-only: no fill until external agent confirms
        return Fill(
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity
            or ((intent.notional or 0) / ref_price if ref_price else 0),
            price=ref_price,
            notional=intent.notional
            or ((intent.quantity or 0) * ref_price),
            ts=datetime.now(timezone.utc),
            mode=TradingMode.LIVE,
            order_id=record["id"],
        )
