from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agentic_trading.agent.research import (
    apply_recommended_symbols,
    apply_universe_from_report,
    load_daily_focus,
    run_research,
    write_daily_focus,
)
from agentic_trading.config import load_config
from agentic_trading.engine import build_engine

ET = ZoneInfo("America/New_York")


def _print_tick(result) -> None:
    p = result.portfolio
    print(
        json.dumps(
            {
                "ts": result.ts.isoformat(),
                "mode": result.mode.value,
                "fills": len(result.fills),
                "equity": round(p.equity, 4),
                "cash_total": round(p.cash, 4),
                "settled_cash": round(p.buying_power, 4),
                "unsettled_cash": round(p.unsettled_cash, 4),
                "halted": p.halted,
                "signals": [f"{s.symbol}:{s.action.value}" for s in result.signals],
                "notes": result.notes,
            },
            indent=2,
        ),
        flush=True,
    )


def _parse_until(s: str) -> datetime:
    """Parse YYYY-MM-DD as end-of-day America/New_York (23:59:59)."""
    d = date.fromisoformat(s)
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=ET)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentic-trading",
        description="Paper-first trading loop with hard risk rails and T+1 settlement.",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run-once", help="Run a single strategy/risk/broker tick")
    loop_p = sub.add_parser("run-loop", help="Run ticks on an interval")
    loop_p.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Seconds between ticks (default: config loop.interval_seconds)",
    )
    loop_p.add_argument(
        "--max-ticks",
        type=int,
        default=0,
        help="Stop after N ticks (0 = until Ctrl-C or --until)",
    )
    loop_p.add_argument(
        "--until",
        type=str,
        default=None,
        help="Stop after this local date (YYYY-MM-DD, America/New_York end of day)",
    )
    sub.add_parser("status", help="Show mode, risk caps, settlement, and log path")
    research_p = sub.add_parser(
        "research",
        help="Research pass: heuristic or LLM (SpaceXAI/Grok) before live",
    )
    research_p.add_argument(
        "--llm",
        action="store_true",
        help="Call Grok via XAI_API_KEY (SpaceXAI / api.x.ai); else heuristic",
    )
    research_p.add_argument(
        "--apply",
        action="store_true",
        help="Merge recommended/expanded symbols into config.yaml universe",
    )
    research_p.add_argument(
        "--apply-daily",
        action="store_true",
        help="Write today's 3 trade names to logs/daily_focus.json for the engine",
    )
    research_p.add_argument(
        "--daily-n",
        type=int,
        default=3,
        help="How many names to trade today (default 3)",
    )
    research_p.add_argument(
        "--no-expand",
        action="store_true",
        help="Do not suggest symbols outside current config",
    )

    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.cmd == "research":
        daily_n = max(1, int(args.daily_n))
        report = run_research(
            config,
            use_llm=bool(args.llm),
            daily_n=daily_n,
            expand=not bool(args.no_expand),
        )
        print(report.to_markdown(), flush=True)
        out: dict = {
            "daily_picks": report.daily_picks,
            "expanded_candidates": report.expanded_candidates,
            "mode": report.mode,
        }
        if args.apply_daily and report.daily_picks:
            focus_path = config.daily_focus.path or (
                config.config_path.parent / "logs" / "daily_focus.json"
            )
            write_daily_focus(report, focus_path, daily_n=daily_n)
            out["daily_focus_path"] = str(focus_path)
            out["daily_focus_applied"] = True
        if args.apply and (
            report.recommended_symbols or report.expanded_candidates or report.daily_picks
        ):
            path = apply_universe_from_report(config.config_path, report)
            out["universe_applied"] = True
            out["config"] = str(path)
        elif args.apply:
            out["universe_applied"] = False
        print(json.dumps(out, indent=2), flush=True)
        return 0

    engine = build_engine(config)

    if args.cmd == "status":
        snap = engine.broker.snapshot()
        print(
            json.dumps(
                {
                    "config": str(config.config_path),
                    "trading_mode": config.trading_mode.value,
                    "allow_live": config.allow_live,
                    "settlement_days": config.settlement_days,
                    "trade_when_cash_available": config.trade_when_cash_available,
                    "symbols": config.symbols,
                    "strategy": config.strategy.name,
                    "selector": {
                        "enabled": config.selector.enabled,
                        "max_new_entries_per_tick": config.selector.max_new_entries_per_tick,
                        "prefer_relative_strength": config.selector.prefer_relative_strength,
                    },
                    "daily_focus": {
                        "enabled": config.daily_focus.enabled,
                        "path": str(config.daily_focus.path),
                        "count": config.daily_focus.count,
                        "active": load_daily_focus(config.daily_focus.path)
                        if config.daily_focus.path
                        else None,
                    },
                    "risk": {
                        "risk_per_trade_pct": config.risk.risk_per_trade_pct,
                        "reward_risk_ratio": config.risk.reward_risk_ratio,
                        "max_order_notional": config.risk.max_order_notional,
                        "max_daily_loss_pct": config.risk.max_daily_loss_pct,
                        "max_orders_per_day": config.risk.max_orders_per_day,
                        "max_position_pct": config.risk.max_position_pct,
                        "max_open_positions": config.risk.max_open_positions,
                    },
                    "log_path": str(config.log_path),
                    "paper_state_path": str(config.paper_state_path),
                    "starting_equity": config.starting_equity,
                    "portfolio": {
                        "equity": snap.equity,
                        "cash_total": snap.cash,
                        "settled_cash": snap.buying_power,
                        "unsettled_cash": snap.unsettled_cash,
                        "orders_today": snap.orders_today,
                        "halted": snap.halted,
                        "positions": {
                            k: {"qty": v.quantity, "avg_cost": v.avg_cost}
                            for k, v in snap.positions.items()
                        },
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "run-once":
        result = engine.run_once()
        _print_tick(result)
        return 0

    if args.cmd == "run-loop":
        interval = args.interval or config.loop_interval_seconds
        until_dt = _parse_until(args.until) if args.until else None
        ticks = 0
        print(
            json.dumps(
                {
                    "event": "session_start",
                    "mode": config.trading_mode.value,
                    "interval_seconds": interval,
                    "until": until_dt.isoformat() if until_dt else None,
                    "settlement_days": config.settlement_days,
                    "trade_when_cash_available": config.trade_when_cash_available,
                    "log_path": str(config.log_path),
                    "paper_state_path": str(config.paper_state_path),
                    "note": (
                        "Trade immediately when cash is available in the account "
                        f"(trade_when_cash_available={config.trade_when_cash_available}). "
                        f"Settlement still tracked as T+{config.settlement_days} for status."
                    ),
                }
            ),
            flush=True,
        )
        try:
            while True:
                now = datetime.now(ET)
                if until_dt is not None and now >= until_dt:
                    print(
                        json.dumps(
                            {
                                "event": "session_end",
                                "reason": "until_reached",
                                "until": until_dt.isoformat(),
                                "ticks": ticks,
                            }
                        ),
                        flush=True,
                    )
                    break
                result = engine.run_once()
                _print_tick(result)
                ticks += 1
                if args.max_ticks and ticks >= args.max_ticks:
                    break
                if result.portfolio.halted:
                    print(
                        "HALTED — buys frozen (sells still allowed next tick if "
                        f"held): {result.portfolio.halt_reason}",
                        flush=True,
                    )
                    # Continue session until Friday even if halted; do not exit
                time.sleep(max(1, interval))
        except KeyboardInterrupt:
            print("\nStopped.", file=sys.stderr)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
