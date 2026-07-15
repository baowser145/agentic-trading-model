from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from agentic_trading.config import load_config
from agentic_trading.engine import build_engine


def _print_tick(result) -> None:
    print(
        json.dumps(
            {
                "ts": result.ts.isoformat(),
                "mode": result.mode.value,
                "fills": len(result.fills),
                "equity": round(result.portfolio.equity, 4),
                "cash": round(result.portfolio.cash, 4),
                "halted": result.portfolio.halted,
                "signals": [
                    f"{s.symbol}:{s.action.value}" for s in result.signals
                ],
            },
            indent=2,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentic-trading",
        description="Paper-first trading loop with hard risk rails.",
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
        help="Stop after N ticks (0 = until Ctrl-C)",
    )
    sub.add_parser("status", help="Show mode, risk caps, and log path")

    args = parser.parse_args(argv)
    config = load_config(args.config)
    engine = build_engine(config)

    if args.cmd == "status":
        print(
            json.dumps(
                {
                    "config": str(config.config_path),
                    "trading_mode": config.trading_mode.value,
                    "allow_live": config.allow_live,
                    "symbols": config.symbols,
                    "risk": {
                        "max_order_notional": config.risk.max_order_notional,
                        "max_daily_loss_pct": config.risk.max_daily_loss_pct,
                        "max_orders_per_day": config.risk.max_orders_per_day,
                        "max_position_pct": config.risk.max_position_pct,
                        "max_open_positions": config.risk.max_open_positions,
                    },
                    "log_path": str(config.log_path),
                    "starting_equity": config.starting_equity,
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
        ticks = 0
        try:
            while True:
                result = engine.run_once()
                _print_tick(result)
                ticks += 1
                if args.max_ticks and ticks >= args.max_ticks:
                    break
                if result.portfolio.halted:
                    print("HALTED — stopping loop:", result.portfolio.halt_reason)
                    break
                time.sleep(max(1, interval))
        except KeyboardInterrupt:
            print("\nStopped.", file=sys.stderr)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
