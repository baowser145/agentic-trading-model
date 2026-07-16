from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
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
from agentic_trading.config import AppConfig, load_config
from agentic_trading.engine import build_engine
from agentic_trading.live.pick_contract import pick_option_contract, save_picked
from agentic_trading.live.portfolio import (
    load_live_portfolio,
    save_live_portfolio,
    snapshot_from_broker_payloads,
)
from agentic_trading.live.propose_option import propose_option, save_proposal
from agentic_trading.live.session import build_session_refresh_plan, save_session_plan
from agentic_trading.live.supervised_review import (
    build_review_request,
    record_review_result,
    save_review_request,
)
from agentic_trading.market.quotes import build_quote_provider

ET = ZoneInfo("America/New_York")


def _with_session_dir(config: AppConfig, session_dir: Path | None) -> AppConfig:
    """Redirect paper state + decision log + journal into an isolated directory."""
    if session_dir is None:
        return config
    d = Path(session_dir)
    if not d.is_absolute():
        d = (config.config_path.parent / d).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return replace(
        config,
        log_path=d / "decisions.jsonl",
        paper_state_path=d / "paper_state.json",
    )


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

    def _add_quote_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--quotes",
            type=str,
            default="fixture",
            choices=["fixture", "live", "yahoo"],
            help="Quote source: fixture (synthetic) or live/yahoo (yfinance, delayed)",
        )
        p.add_argument(
            "--session-dir",
            type=Path,
            default=None,
            help="Isolate paper state/journal under this dir (e.g. logs/paper_live)",
        )

    once_p = sub.add_parser("run-once", help="Run a single strategy/risk/broker tick")
    _add_quote_args(once_p)
    loop_p = sub.add_parser("run-loop", help="Run ticks on an interval")
    _add_quote_args(loop_p)
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
    watch_p = sub.add_parser(
        "watch",
        help="Local website to watch paper bot (serves watch_snapshot.json)",
    )
    watch_p.add_argument(
        "--port",
        type=int,
        default=8787,
        help="Port (default 8787)",
    )
    watch_p.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Bind host (default 127.0.0.1 — local only)",
    )
    watch_p.add_argument(
        "--session-dir",
        type=Path,
        default=None,
        help="Session dir with watch_snapshot.json (default: config paper log dir)",
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
    trades_p = sub.add_parser(
        "trades",
        help="Trade journal summary (for early backtest review)",
    )
    trades_p.add_argument(
        "--session-dir",
        type=Path,
        default=None,
        help="Read journal from isolated session dir (e.g. logs/paper_live)",
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

    sess_p = sub.add_parser(
        "session-refresh",
        help="Print/write Agentic session bootstrap plan (Grok auto-refresh checklist)",
    )
    sess_p.add_argument(
        "--stale-seconds",
        type=int,
        default=900,
        help="Refresh if snapshot older than this (default 900s)",
    )
    sess_p.add_argument(
        "--min-bp",
        type=float,
        default=50.0,
        help="Min buying power to treat options path as free (default 50)",
    )
    sess_p.add_argument(
        "--no-write",
        action="store_true",
        help="Do not write logs/session_refresh_plan.json",
    )

    pick_p = sub.add_parser(
        "pick-option-contract",
        help="Pick best long-premium contract from get_option_instruments JSON",
    )
    pick_p.add_argument(
        "--file",
        type=Path,
        required=True,
        help="JSON file of instruments (MCP get_option_instruments response)",
    )
    pick_p.add_argument("--type", dest="option_type", default="call", choices=["call", "put"])
    pick_p.add_argument("--strike-hint", type=float, default=None)
    pick_p.add_argument("--underlying-price", type=float, default=None)
    pick_p.add_argument("--min-dte", type=int, default=None)
    pick_p.add_argument("--max-dte", type=int, default=None)
    pick_p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write pick JSON (default logs/picked_contract.json)",
    )

    prep_p = sub.add_parser(
        "prepare-option-review",
        help="Build review_option_order args if BP free (never places)",
    )
    prep_p.add_argument("--option-id", type=str, required=True)
    prep_p.add_argument("--price", type=float, required=True, help="Limit debit per contract")
    prep_p.add_argument("--symbol", type=str, required=True)
    prep_p.add_argument("--type", dest="option_type", default="call", choices=["call", "put"])
    prep_p.add_argument("--contracts", type=int, default=1)
    prep_p.add_argument("--max-premium", type=float, default=None)
    prep_p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write review request (default logs/option_review_request.json)",
    )

    rec_p = sub.add_parser(
        "record-option-review",
        help="Save MCP review_option_order response for audit (never places)",
    )
    rec_p.add_argument(
        "--file",
        type=Path,
        required=True,
        help="JSON file with review_option_order response body",
    )
    rec_p.add_argument(
        "--request-file",
        type=Path,
        default=None,
        help="Optional prepare-option-review JSON to attach",
    )
    rec_p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Default logs/option_review_result.json",
    )

    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.cmd == "session-refresh":
        plan = build_session_refresh_plan(
            config,
            stale_after_seconds=max(60, int(args.stale_seconds)),
            min_bp_for_options=float(args.min_bp),
        )
        wrote = None
        if not args.no_write:
            out_p = config.config_path.parent / "logs" / "session_refresh_plan.json"
            save_session_plan(plan, out_p)
            wrote = str(out_p)
        out = plan.to_dict()
        out["wrote"] = wrote
        print(json.dumps(out, indent=2), flush=True)
        return 0 if not plan.needs_refresh else 2

    if args.cmd == "pick-option-contract":
        raw = json.loads(Path(args.file).read_text())
        picked = pick_option_contract(
            raw,
            option_type=args.option_type,
            strike_hint=args.strike_hint,
            underlying_price=args.underlying_price,
            min_dte=int(args.min_dte if args.min_dte is not None else config.option_min_dte),
            max_dte=int(args.max_dte if args.max_dte is not None else config.option_max_dte),
        )
        if picked is None:
            print(
                json.dumps(
                    {
                        "picked": None,
                        "error": "No contract matched DTE/type/tradability filters",
                    },
                    indent=2,
                ),
                flush=True,
            )
            return 2
        out_path = args.out or (
            config.config_path.parent / "logs" / "picked_contract.json"
        )
        save_picked(picked, out_path)
        print(
            json.dumps({"picked": picked.to_dict(), "wrote": str(out_path)}, indent=2),
            flush=True,
        )
        return 0

    if args.cmd == "prepare-option-review":
        req = build_review_request(
            account_number=config.agentic_account_number,
            symbol=args.symbol,
            option_id=args.option_id,
            option_type=args.option_type,
            limit_price=float(args.price),
            contracts=int(args.contracts),
            max_premium_usd=float(
                args.max_premium
                if args.max_premium is not None
                else config.max_option_premium
            ),
            live_path=config.live_portfolio_path,
        )
        out_path = args.out or (
            config.config_path.parent / "logs" / "option_review_request.json"
        )
        save_review_request(req, out_path)
        out = req.to_dict()
        out["wrote"] = str(out_path)
        print(json.dumps(out, indent=2), flush=True)
        return 2 if req.blocked else 0

    if args.cmd == "record-option-review":
        review = json.loads(Path(args.file).read_text())
        request = None
        if args.request_file and Path(args.request_file).is_file():
            request = json.loads(Path(args.request_file).read_text())
        out_path = args.out or (
            config.config_path.parent / "logs" / "option_review_result.json"
        )
        record_review_result(review, request=request, path=out_path)
        print(
            json.dumps(
                {
                    "wrote": str(out_path),
                    "place_allowed": False,
                    "note": "Review stored. Wait for explicit user confirm before place.",
                },
                indent=2,
            ),
            flush=True,
        )
        return 0

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

    if args.cmd == "watch":
        from agentic_trading.watch_server import serve

        snap = None
        if getattr(args, "session_dir", None):
            d = Path(args.session_dir)
            if not d.is_absolute():
                d = (config.config_path.parent / d).resolve()
            snap = d / "watch_snapshot.json"
        elif config.paper_state_path:
            snap = config.paper_state_path.parent / "watch_snapshot.json"
        else:
            snap = Path("logs/watch_snapshot.json")
        serve(snap, host=args.host, port=args.port)
        return 0

    # Optional quote source + isolated session dir for paper runs
    session_dir = getattr(args, "session_dir", None)
    config = _with_session_dir(config, session_dir)
    quote_src = getattr(args, "quotes", None) or "fixture"
    quotes = None
    watch_path = None
    if args.cmd in ("run-once", "run-loop", "trades", "status"):
        if args.cmd in ("run-once", "run-loop"):
            quotes = build_quote_provider(quote_src)
            if config.paper_state_path:
                watch_path = config.paper_state_path.parent / "watch_snapshot.json"
        engine = build_engine(config, quotes=quotes, watch_path=watch_path)
    else:
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
                    "quotes": quote_src,
                    "quote_provider": type(engine.quotes).__name__,
                    "interval_seconds": interval,
                    "until": until_dt.isoformat() if until_dt else None,
                    "settlement_days": config.settlement_days,
                    "trade_when_cash_available": config.trade_when_cash_available,
                    "log_path": str(config.log_path),
                    "paper_state_path": str(config.paper_state_path),
                    "watch_snapshot": str(watch_path) if watch_path else None,
                    "watch_hint": "python -m agentic_trading watch"
                    + (
                        f" --session-dir {session_dir}"
                        if session_dir
                        else ""
                    ),
                    "r1_r2": {
                        "market_red_exit_ticks": config.strategy.market_red_exit_ticks,
                        "soft_exit_min_hold_ticks": config.strategy.soft_exit_min_hold_ticks,
                        "market_red_sma_buffer_pct": config.strategy.market_red_sma_buffer_pct,
                        "reentry_cooldown_ticks": config.strategy.reentry_cooldown_ticks,
                    },
                    "note": (
                        "Trade immediately when cash is available in the account "
                        f"(trade_when_cash_available={config.trade_when_cash_available}). "
                        f"Settlement still tracked as T+{config.settlement_days} for status. "
                        "Paper only unless allow_live + explicit live path."
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
