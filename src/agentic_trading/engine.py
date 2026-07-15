from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agentic_trading.broker.agentic import AgenticBroker
from agentic_trading.broker.base import Broker
from agentic_trading.broker.paper import PaperBroker
from agentic_trading.config import AppConfig
from agentic_trading.log import DecisionLogger
from agentic_trading.market.quotes import FixtureQuoteProvider, QuoteProvider
from agentic_trading.models import (
    OpenTradePlan,
    Side,
    Signal,
    SignalAction,
    TickResult,
    TradingMode,
)
from agentic_trading.risk.gate import RiskGate
from agentic_trading.strategy.day_trade_playbook import DayTradePlaybookStrategy
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
        self.strategy = self._build_strategy(config)
        self.quotes = quotes or FixtureQuoteProvider()
        self.logger = logger or DecisionLogger(config.log_path)
        self.open_plans: dict[str, OpenTradePlan] = {}
        self._plans_path = (
            config.paper_state_path.parent / "open_plans.json"
            if config.paper_state_path
            else Path("logs/open_plans.json")
        )
        self._load_plans()

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
                trade_when_cash_available=config.trade_when_cash_available,
            )
        else:
            self.broker = PaperBroker(
                config.starting_equity,
                settlement_days=config.settlement_days,
                state_path=config.paper_state_path,
                trade_when_cash_available=config.trade_when_cash_available,
            )

    @staticmethod
    def _build_strategy(config: AppConfig):
        name = (config.strategy.name or "").lower()
        if name in ("day_trade_playbook", "daytrade", "playbook"):
            return DayTradePlaybookStrategy(
                config.strategy, config.symbols, config.risk
            )
        return SimpleMomentumStrategy(config.strategy, config.symbols)

    def _load_plans(self) -> None:
        if not self._plans_path.is_file():
            return
        try:
            raw = json.loads(self._plans_path.read_text())
            for sym, p in (raw or {}).items():
                self.open_plans[sym] = OpenTradePlan(
                    symbol=sym,
                    entry=float(p["entry"]),
                    stop=float(p["stop"]),
                    target=float(p["target"]),
                    quantity=float(p["quantity"]),
                    reason=str(p.get("reason", "")),
                )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            self.open_plans = {}

    def _save_plans(self) -> None:
        self._plans_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            sym: {
                "entry": p.entry,
                "stop": p.stop,
                "target": p.target,
                "quantity": p.quantity,
                "reason": p.reason,
            }
            for sym, p in self.open_plans.items()
        }
        self._plans_path.write_text(json.dumps(payload, indent=2))

    def _stop_target_exits(
        self,
        quotes: dict,
        portfolio,
    ) -> list[Signal]:
        """Force EXIT when price hits stop or target on an open plan."""
        exits: list[Signal] = []
        for sym, plan in list(self.open_plans.items()):
            if portfolio.position_qty(sym) <= 0:
                del self.open_plans[sym]
                continue
            q = quotes.get(sym)
            if not q:
                continue
            px = q.price
            if px <= plan.stop:
                exits.append(
                    Signal(
                        symbol=sym,
                        action=SignalAction.EXIT_LONG,
                        strength=1.0,
                        reason=(
                            f"STOP hit: px={px:.4f} <= stop={plan.stop:.4f} "
                            f"(entry={plan.entry:.4f})"
                        ),
                        ref_price=px,
                        stop_price=plan.stop,
                        target_price=plan.target,
                    )
                )
            elif px >= plan.target:
                exits.append(
                    Signal(
                        symbol=sym,
                        action=SignalAction.EXIT_LONG,
                        strength=1.0,
                        reason=(
                            f"TARGET hit: px={px:.4f} >= target={plan.target:.4f} "
                            f"({self.config.risk.reward_risk_ratio:.1f}R from "
                            f"entry={plan.entry:.4f})"
                        ),
                        ref_price=px,
                        stop_price=plan.stop,
                        target_price=plan.target,
                    )
                )
        return exits

    def run_once(self) -> TickResult:
        notes: list[str] = []
        mode = self.config.trading_mode
        if mode == TradingMode.LIVE and not self.config.allow_live:
            mode = TradingMode.PAPER
            notes.append("live requested but allow_live=false; forced paper")

        # Ensure market symbol is in quote universe
        symbols = list(self.config.symbols)
        mkt = self.config.strategy.market_symbol
        if mkt and mkt not in symbols:
            symbols = symbols + [mkt]

        quotes = self.quotes.get_quotes(symbols)
        history = self.quotes.get_history(
            symbols, self.config.strategy.lookback_bars
        )
        prices = {s: q.price for s, q in quotes.items()}
        portfolio = self.broker.mark_to_market(prices)
        portfolio = self.risk.evaluate_portfolio_halt(portfolio)
        if portfolio.halted and isinstance(self.broker, PaperBroker):
            self.broker.set_halt(portfolio.halt_reason or "halt")
        elif portfolio.halted and isinstance(self.broker, AgenticBroker):
            self.broker.set_halt(portfolio.halt_reason or "halt")

        # Stop / target exits take priority over strategy entries
        forced = self._stop_target_exits(quotes, portfolio)
        signals = self.strategy.generate(quotes, history, portfolio)
        # Merge: forced exits replace same-symbol signals
        forced_syms = {s.symbol for s in forced}
        signals = forced + [s for s in signals if s.symbol not in forced_syms]

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
                if fill.side == Side.BUY:
                    # Attach plan from signal if present
                    sig = next((s for s in signals if s.symbol == sym), None)
                    if sig and sig.stop_price and sig.target_price:
                        self.open_plans[sym] = OpenTradePlan(
                            symbol=sym,
                            entry=fill.price,
                            stop=sig.stop_price,
                            target=sig.target_price,
                            quantity=fill.quantity,
                            reason=sig.reason,
                        )
                        notes.append(
                            f"plan {sym}: stop={sig.stop_price:.4f} "
                            f"target={sig.target_price:.4f}"
                        )
                elif fill.side == Side.SELL:
                    self.open_plans.pop(sym, None)
            else:
                notes.append(f"broker returned no fill for {sym}")

        self._save_plans()

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
