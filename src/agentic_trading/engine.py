from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agentic_trading.broker.agentic import AgenticBroker
from agentic_trading.broker.base import Broker
from agentic_trading.broker.paper import PaperBroker
from agentic_trading.config import AppConfig
from agentic_trading.log import DecisionLogger
from agentic_trading.market.quotes import FixtureQuoteProvider, QuoteProvider
from agentic_trading.models import TickResult, TradingMode
from agentic_trading.risk.gate import RiskGate
from agentic_trading.strategy.simple_momentum import SimpleMomentumStrategy


class Engine:
    def __init__(
        self,
        config: AppConfig,
        broker: Broker | None = None,
        quotes: QuoteProvider | None = None,
        logger: DecisionLogger | None = None,
    ) -> None:
        self.config = config
        self.risk = RiskGate(config.risk)
        self.strategy = SimpleMomentumStrategy(config.strategy, config.symbols)
        self.quotes = quotes or FixtureQuoteProvider()
        self.logger = logger or DecisionLogger(config.log_path)

        if broker is not None:
            self.broker = broker
        elif config.trading_mode == TradingMode.LIVE and config.allow_live:
            intents = config.config_path.parent / "logs" / "live_intents.jsonl"
            self.broker = AgenticBroker(
                starting_equity=config.starting_equity,
                intents_path=intents,
                shadow_paper=True,
                settlement_days=config.settlement_days,
                state_path=config.paper_state_path,
            )
        else:
            self.broker = PaperBroker(
                config.starting_equity,
                settlement_days=config.settlement_days,
                state_path=config.paper_state_path,
            )

    def run_once(self) -> TickResult:
        notes: list[str] = []
        mode = self.config.trading_mode
        if mode == TradingMode.LIVE and not self.config.allow_live:
            mode = TradingMode.PAPER
            notes.append("live requested but allow_live=false; forced paper")

        quotes = self.quotes.get_quotes(self.config.symbols)
        history = self.quotes.get_history(
            self.config.symbols, self.config.strategy.lookback_bars
        )
        prices = {s: q.price for s, q in quotes.items()}
        portfolio = self.broker.mark_to_market(prices)
        portfolio = self.risk.evaluate_portfolio_halt(portfolio)
        if portfolio.halted and isinstance(self.broker, PaperBroker):
            self.broker.set_halt(portfolio.halt_reason or "halt")
        elif portfolio.halted and isinstance(self.broker, AgenticBroker):
            self.broker.set_halt(portfolio.halt_reason or "halt")

        signals = self.strategy.generate(quotes, history, portfolio)
        decisions = self.risk.process_signals(signals, portfolio)

        fills = []
        for decision in decisions:
            if not decision.approved or decision.intent is None:
                continue
            sym = decision.intent.symbol
            ref = quotes[sym].price if sym in quotes else 0.0
            fill = self.broker.execute(decision.intent, ref)
            if fill:
                fills.append(fill)
            else:
                notes.append(f"broker returned no fill for {sym}")

        # Advance fixture series so loops see time progression
        if isinstance(self.quotes, FixtureQuoteProvider):
            self.quotes.advance(1)

        portfolio = self.broker.mark_to_market(prices)
        portfolio = self.risk.evaluate_portfolio_halt(portfolio)

        result = TickResult(
            ts=datetime.now(timezone.utc),
            mode=mode,
            signals=signals,
            decisions=decisions,
            fills=fills,
            portfolio=portfolio,
            notes=notes,
        )
        self.logger.append(result)
        return result


def build_engine(config: AppConfig) -> Engine:
    return Engine(config)
