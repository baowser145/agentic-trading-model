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
from agentic_trading.live.portfolio import (
    load_live_portfolio,
    save_live_portfolio,
    snapshot_from_broker_payloads,
)
from agentic_trading.live.propose_option import propose_option, save_proposal

ET = ZoneInfo("America/New_York")


def _print_tick(result) -> None:
    p = result.portfolio
    settled = (
        p.settled_cash if p.settled_cash is not None else p.cash - p.unsettled_cash
    )
    print(
        json.dumps(
            {
                "ts": result.ts.isoformat(),
                "mode": result.mode.value,
                "fills": len(result.fills),
                "equity": round(p.equity, 4),
                "cash_total": round(p.cash, 4),
                "settled_cash": round(settled, 4),
                "unsettled_cash": round(p.unsettled_cash, 4),
                "buying_power": round(p.buying_power, 4),
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
    status_p = sub.add_parser(
        "status",
        help="Show mode, risk caps, settlement, and portfolio (paper and/or --live)",
    )
    status_p.add_argument(
        "--live",
        action="store_true",
        help="Include Agentic live snapshot from logs/live_portfolio.json",
    )
    status_p.add_argument(
        "--live-only",
        action="store_true",
        help="Print only the live Agentic snapshot (implies --live)",
    )
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
    sub.add_parser(
        "trades",
        help="Trade journal summary (for early backtest review)",
    )

    prop_p = sub.add_parser(
        "propose-option",
        help="Propose a long call/put for Agentic (review only — never places)",
    )
    prop_p.add_argument("--symbol", type=str, default=None, help="Underlying (default: daily focus #1)")
    prop_p.add_argument(
        "--type",
        dest="option_type",
        type=str,
        default="call",
        choices=["call", "put"],
        help="call or put (default call)",
    )
    prop_p.add_argument(
        "--max-premium",
        type=float,
        default=None,
        help="Max debit USD for the structure (default: config live.max_option_premium)",
    )
    prop_p.add_argument(
        "--contracts",
        type=int,
        default=None,
        help="Contract count (default 1, capped by config)",
    )
    prop_p.add_argument(
        "--no-write",
        action="store_true",
        help="Do not write logs/option_proposal.json",
    )

    sync_p = sub.add_parser(
        "write-live-snapshot",
        help="Write logs/live_portfolio.json from MCP JSON payload (stdin or --file)",
    )
    sync_p.add_argument(
        "--file",
        type=Path,
        default=None,
        help="JSON file with portfolio + equity_positions + option_positions",
    )
    sync_p.add_argument(
        "--account",
        type=str,
        default=None,
        help="Override agentic account number",
    )

    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.cmd == "write-live-snapshot":
        if args.file:
            payload = json.loads(Path(args.file).read_text())
        else:
            payload = json.load(sys.stdin)
        acct = (
            args.account
            or payload.get("account_number")
            or config.agentic_account_number
        )
        if not acct:
            print(
                "account_number required (config broker.agentic_account_number or --account)",
                file=sys.stderr,
            )
            return 2
        port = payload.get("portfolio") or payload
        eqs = payload.get("equity_positions") or payload.get("positions") or []
        opts = payload.get("option_positions") or []
        # If portfolio nested under data (raw MCP envelope)
        if "data" in payload and isinstance(payload["data"], dict):
            # allow {data: {portfolio fields}} or full multi-key
            if "total_value" in payload["data"] or "buying_power" in payload["data"]:
                port = payload["data"]
        snap = snapshot_from_broker_payloads(
            account_number=str(acct),
            portfolio=port if isinstance(port, dict) else {},
            equity_positions=list(eqs) if isinstance(eqs, list) else [],
            option_positions=list(opts) if isinstance(opts, list) else [],
            account_nickname=str(payload.get("account_nickname") or "Agentic"),
            agentic_allowed=bool(payload.get("agentic_allowed", True)),
            notes=list(payload.get("notes") or []),
        )
        out_path = config.live_portfolio_path or (
            config.config_path.parent / "logs" / "live_portfolio.json"
        )
        save_live_portfolio(snap, out_path)
        print(
            json.dumps(
                {
                    "wrote": str(out_path),
                    "account_number": snap.account_number,
                    "total_value": snap.total_value,
                    "cash": snap.cash,
                    "buying_power": snap.buying_power,
                    "equity_positions": len(snap.equity_positions),
                    "option_positions": len(snap.option_positions),
                    "ts": snap.ts,
                },
                indent=2,
            ),
            flush=True,
        )
        return 0

    if args.cmd == "propose-option":
        live_path = config.live_portfolio_path
        proposal = propose_option(
            config,
            symbol=args.symbol,
            option_type=args.option_type,
            max_premium=args.max_premium,
            contracts=args.contracts,
            live_path=live_path,
        )
        wrote = None
        if not args.no_write:
            prop_path = config.option_proposal_path or (
                config.config_path.parent / "logs" / "option_proposal.json"
            )
            save_proposal(proposal, prop_path)
            wrote = str(prop_path)
        out = proposal.to_dict()
        out["wrote"] = wrote
        out["note"] = (
            "PROPOSAL ONLY — place_allowed=false. Use mcp_next_steps with human confirm; "
            "never auto-place from this command."
        )
        print(json.dumps(out, indent=2), flush=True)
        return 2 if proposal.blocked else 0

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

    if args.cmd == "trades":
        print(json.dumps(engine.journal.summary(), indent=2), flush=True)
        return 0

    if args.cmd == "status":
        want_live = bool(args.live or args.live_only)
        live_path = config.live_portfolio_path
        live_snap = load_live_portfolio(live_path) if live_path else None
        live_block: dict = {
            "path": str(live_path) if live_path else None,
            "agentic_account_number": config.agentic_account_number or None,
            "loaded": live_snap is not None,
            "snapshot": live_snap.to_dict() if live_snap else None,
            "how_to_refresh": (
                "From a Grok/MCP session: call robinhood get_portfolio + "
                "get_equity_positions + get_option_positions on the Agentic "
                "account, then: python -m agentic_trading write-live-snapshot "
                "--file <payload.json>  (or pipe JSON on stdin)."
            ),
        }
        if args.live_only:
            if live_snap is None:
                print(json.dumps(live_block, indent=2), flush=True)
                return 2
            print(json.dumps(live_block, indent=2), flush=True)
            return 0

        snap = engine.broker.snapshot()
        settled = (
            snap.settled_cash
            if snap.settled_cash is not None
            else snap.cash - snap.unsettled_cash
        )
        payload = {
            "config": str(config.config_path),
            "trading_mode": config.trading_mode.value,
            "allow_live": config.allow_live,
            "settlement_days": config.settlement_days,
            "trade_when_cash_available": config.trade_when_cash_available,
            "agentic_account_number": config.agentic_account_number or None,
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
            "options_playbook": {
                "max_option_premium": config.max_option_premium,
                "max_option_contracts": config.max_option_contracts,
                "min_dte": config.option_min_dte,
                "max_dte": config.option_max_dte,
                "proposal_path": str(config.option_proposal_path)
                if config.option_proposal_path
                else None,
            },
            "log_path": str(config.log_path),
            "paper_state_path": str(config.paper_state_path),
            "starting_equity": config.starting_equity,
            "portfolio": {
                "source": "paper",
                "equity": snap.equity,
                "cash_total": snap.cash,
                "settled_cash": settled,
                "unsettled_cash": snap.unsettled_cash,
                "buying_power": snap.buying_power,
                "orders_today": snap.orders_today,
                "halted": snap.halted,
                "positions": {
                    k: {"qty": v.quantity, "avg_cost": v.avg_cost}
                    for k, v in snap.positions.items()
                },
            },
            "journal": engine.journal.summary(),
        }
        if want_live:
            payload["live"] = live_block
        print(json.dumps(payload, indent=2), flush=True)
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
