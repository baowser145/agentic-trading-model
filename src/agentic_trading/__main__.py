from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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

    args = parser.parse_args(argv)
    config = load_config(args.config)
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
                    "risk": {
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
