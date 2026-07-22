from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agentic_trading.agent.research import load_daily_focus
from agentic_trading.agent.selector import SelectorConfig, SetupSelectorAgent
from agentic_trading.broker.agentic import AgenticBroker
from agentic_trading.broker.base import Broker
from agentic_trading.broker.paper import PaperBroker
from agentic_trading.config import AppConfig
from agentic_trading.journal import TradeJournal
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
        watch_path: Path | None = None,
    ) -> None:
        self.config = config
        self.risk = RiskGate(config.risk)
        self.strategy = self._build_strategy(config)
        self.quotes = quotes or FixtureQuoteProvider()
        self.logger = logger or DecisionLogger(config.log_path)
        self.watch_path = watch_path
        self.selector = SetupSelectorAgent(
            SelectorConfig(
                enabled=config.selector.enabled,
                max_new_entries_per_tick=config.selector.max_new_entries_per_tick,
                market_symbol=config.strategy.market_symbol,
                prefer_relative_strength=config.selector.prefer_relative_strength,
                rs_lookback=config.selector.rs_lookback,
            )
        )
        self.open_plans: dict[str, OpenTradePlan] = {}
        self._plans_path = (
            config.paper_state_path.parent / "open_plans.json"
            if config.paper_state_path
            else Path("logs/open_plans.json")
        )
        log_dir = (
            config.paper_state_path.parent
            if config.paper_state_path
            else Path("logs")
        )
        self.journal = TradeJournal(log_dir)
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

    def _apply_daily_focus(self, signals: list[Signal]) -> tuple[list[Signal], list[str]]:
        """Block NEW entries outside today's research picks when focus is active."""
        df = self.config.daily_focus
        if not df.enabled or not df.path:
            return signals, []
        data = load_daily_focus(df.path)
        if not data:
            return signals, ["daily_focus: no file — all setups eligible"]
        if data.get("expired"):
            return signals, [
                f"daily_focus: expired ({data.get('date')}) — run research --apply-daily"
            ]
        allowed = {
            str(s).upper()
            for s in (data.get("daily_picks") or [])
            if str(s).strip()
        }
        bias = str(data.get("market_bias") or "call").strip().lower()
        if bias not in ("call", "put", "hold"):
            bias = "call"
        if not allowed and bias == "call":
            return signals, ["daily_focus: empty picks — all setups eligible"]
        out: list[Signal] = []
        notes = [
            f"daily_focus active: picks={', '.join(sorted(allowed)) or '(none)'} "
            f"bias={bias}"
        ]
        # Morning bias: long-only paper playbook — block NEW longs on put/hold days
        block_new_longs = bias in ("put", "hold")
        if block_new_longs:
            notes.append(
                f"market_bias={bias}: blocking NEW ENTER_LONG "
                "(put/hold day; manage/exits still allowed)"
            )
        for s in signals:
            if s.action != SignalAction.ENTER_LONG:
                out.append(s)
                continue
            if block_new_longs:
                out.append(
                    Signal(
                        symbol=s.symbol,
                        action=SignalAction.FLAT,
                        strength=0.0,
                        reason=(
                            f"market_bias={bias}: no new long equity "
                            f"(morning assess); was: {s.reason}"
                        ),
                        ref_price=s.ref_price,
                        stop_price=s.stop_price,
                        target_price=s.target_price,
                    )
                )
            elif allowed and s.symbol not in allowed:
                out.append(
                    Signal(
                        symbol=s.symbol,
                        action=SignalAction.FLAT,
                        strength=0.0,
                        reason=(
                            f"daily_focus: not in today's {len(allowed)} "
                            f"({', '.join(sorted(allowed))}); was: {s.reason}"
                        ),
                        ref_price=s.ref_price,
                        stop_price=s.stop_price,
                        target_price=s.target_price,
                    )
                )
            else:
                out.append(s)
        return out, notes

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

        # Daily focus: only NEW entries in today's research top-N (exits always ok)
        signals, focus_notes = self._apply_daily_focus(signals)
        notes.extend(focus_notes)

        # Selector agent: rank multi-name setups, keep best N new buys
        signals, sel_notes = self.selector.select(signals, quotes, history)
        notes.extend(sel_notes)

        # Cap new entries by remaining open-position slots
        open_n = sum(1 for p in portfolio.positions.values() if p.quantity > 0)
        slots = max(0, self.config.risk.max_open_positions - open_n)
        if slots == 0:
            trimmed: list[Signal] = []
            for s in signals:
                if s.action == SignalAction.ENTER_LONG:
                    trimmed.append(
                        Signal(
                            symbol=s.symbol,
                            action=SignalAction.FLAT,
                            strength=0.0,
                            reason=f"no open slots (max_open_positions); was: {s.reason}",
                            ref_price=s.ref_price,
                            stop_price=s.stop_price,
                            target_price=s.target_price,
                        )
                    )
                else:
                    trimmed.append(s)
            signals = trimmed
            notes.append("selector/slots: max open positions reached")

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
                sig = next((s for s in signals if s.symbol == sym), None)
                if fill.side == Side.BUY:
                    # Attach plan from signal if present
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
                    closed = self.journal.record_fill(
                        fill,
                        reason=(sig.reason if sig else decision.intent.reason),
                        stop=sig.stop_price if sig else None,
                        target=sig.target_price if sig else None,
                    )
                else:
                    plan = self.open_plans.pop(sym, None)
                    exit_reason = sig.reason if sig else decision.intent.reason
                    closed = self.journal.record_fill(
                        fill,
                        exit_reason=exit_reason,
                        stop=plan.stop if plan else None,
                        target=plan.target if plan else None,
                    )
                    if closed:
                        notes.append(
                            f"closed {closed.trade_id} {closed.symbol} "
                            f"pnl={closed.pnl:+.2f} ({closed.pnl_pct:+.2f}%)"
                        )
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
        self._write_watch_snapshot(result, quotes)
        return result

    def _write_watch_snapshot(self, result: TickResult, quotes: dict) -> None:
        """JSON snapshot for local watch UI / website poll."""
        path = self.watch_path
        if path is None and self.config.paper_state_path:
            path = self.config.paper_state_path.parent / "watch_snapshot.json"
        if path is None:
            path = Path("logs/watch_snapshot.json")
        try:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            quote_src = type(self.quotes).__name__
            journal = self.journal.summary()
            strat = self.strategy
            filter_state: dict = {}
            if isinstance(strat, DayTradePlaybookStrategy):
                filter_state = {
                    "tick": strat._tick,
                    "market_red_streak": strat._market_red_streak,
                    "hold_ticks": dict(strat._hold_ticks),
                    "cooldown_until": dict(strat._cooldown_until),
                    "market_red_exit_ticks": strat.config.market_red_exit_ticks,
                    "soft_exit_min_hold_ticks": strat.config.soft_exit_min_hold_ticks,
                    "reentry_cooldown_ticks": strat.config.reentry_cooldown_ticks,
                }
            payload = {
                "ts": result.ts.isoformat(),
                "mode": result.mode.value,
                "quote_provider": quote_src,
                "equity": round(result.portfolio.equity, 4),
                "cash": round(result.portfolio.cash, 4),
                "buying_power": round(result.portfolio.buying_power, 4),
                "halted": result.portfolio.halted,
                "halt_reason": result.portfolio.halt_reason,
                "positions": {
                    k: {
                        "qty": round(v.quantity, 6),
                        "avg_cost": round(v.avg_cost, 4),
                        "mark": round(quotes[k].price, 4) if k in quotes else None,
                    }
                    for k, v in result.portfolio.positions.items()
                    if v.quantity != 0
                },
                "signals": [
                    {
                        "symbol": s.symbol,
                        "action": s.action.value,
                        "reason": s.reason,
                        "ref_price": s.ref_price,
                    }
                    for s in result.signals
                ],
                "fills": [
                    {
                        "symbol": f.symbol,
                        "side": f.side.value,
                        "qty": f.quantity,
                        "price": f.price,
                    }
                    for f in result.fills
                ],
                "notes": result.notes,
                "open_plans": {
                    sym: {
                        "entry": p.entry,
                        "stop": p.stop,
                        "target": p.target,
                        "quantity": p.quantity,
                    }
                    for sym, p in self.open_plans.items()
                },
                "filter_state": filter_state,
                "journal": {
                    "closed_trades": journal.get("closed_trades"),
                    "win_rate": journal.get("win_rate"),
                    "total_pnl": journal.get("total_pnl"),
                    "open_symbols": journal.get("open_symbols"),
                    "recent": (journal.get("recent") or [])[-5:],
                },
            }
            path.write_text(json.dumps(payload, indent=2))
        except OSError:
            pass


def build_engine(
    config: AppConfig,
    quotes: QuoteProvider | None = None,
    watch_path: Path | None = None,
) -> Engine:
    return Engine(config, quotes=quotes, watch_path=watch_path)
