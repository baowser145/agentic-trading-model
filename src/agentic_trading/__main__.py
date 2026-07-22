from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agentic_trading.agent.deep_research import run_deep_research
from agentic_trading.agent.research import (
    apply_recommended_symbols,
    apply_universe_from_report,
    load_daily_focus,
    run_research,
    write_daily_focus,
)
from agentic_trading.agent.sp500_scan import run_sp500_scan
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
from agentic_trading.options_bt.runner import (
    DEFAULT_SYMBOLS,
    run_search,
    run_single,
    scenario_from_name,
)
from agentic_trading.options_bt.scenario import OptionScenario, SEED_SCENARIOS

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
    morning_p = sub.add_parser(
        "morning-paper",
        help=(
            "Morning paper routine: assess market (call/put/hold) → scan → "
            "watch ticks → optional trigger (uses yahoo quotes by default)"
        ),
    )
    morning_p.add_argument(
        "--quotes",
        type=str,
        default="yahoo",
        choices=["fixture", "live", "yahoo"],
        help="Quote source (default yahoo/live for real tape; fixture for offline)",
    )
    morning_p.add_argument(
        "--llm",
        action="store_true",
        help="Use Grok research for scanner (else heuristic RS scan)",
    )
    morning_p.add_argument(
        "--watch-ticks",
        type=int,
        default=2,
        help="Paper ticks to watch after scan before trigger (default 2)",
    )
    morning_p.add_argument(
        "--no-trigger",
        action="store_true",
        help="Assess + scan + watch only; do not run a trigger tick",
    )
    morning_p.add_argument(
        "--daily-n",
        type=int,
        default=3,
        help="How many focus names to lock for the day (default 3)",
    )
    morning_p.add_argument(
        "--session-dir",
        type=Path,
        default=None,
        help="Isolate paper state (e.g. logs/paper_morning)",
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

    deep_p = sub.add_parser(
        "deep-research",
        help=(
            "5-section single-ticker memo (Deep Dive · Peer · Bear · Bull · Trade Plan) "
            "before promoting a name to daily_focus — never places orders"
        ),
    )
    deep_p.add_argument(
        "--ticker",
        "-t",
        type=str,
        required=True,
        help="Ticker to research (e.g. AAPL, PLTR)",
    )
    deep_p.add_argument(
        "--peers",
        type=str,
        default=None,
        help="Comma-separated peers for valuation table (default: built-in map)",
    )
    deep_p.add_argument(
        "--llm",
        action="store_true",
        help="Call Grok via XAI_API_KEY (recommended). Default if neither flag set.",
    )
    deep_p.add_argument(
        "--no-llm",
        action="store_true",
        help="Heuristic skeleton only (tape + incomplete fundies; no API)",
    )
    deep_p.add_argument(
        "--quotes",
        type=str,
        default="yahoo",
        choices=["fixture", "live", "yahoo"],
        help="Quote source for tape context (default yahoo)",
    )
    deep_p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Memo directory (default: logs/deep_research)",
    )

    sp500_p = sub.add_parser(
        "sp500-scan",
        help=(
            "S&P 500 liquidity+RS scan → top-N shortlist; "
            "optional deep-research on top deep-n (never places orders)"
        ),
    )
    sp500_p.add_argument(
        "--top",
        type=int,
        default=10,
        help="How many liquid names to keep after rank (default 10)",
    )
    sp500_p.add_argument(
        "--deep-n",
        type=int,
        default=3,
        help="How many top names to deep-research when enabled (default 3)",
    )
    sp500_p.add_argument(
        "--deep-research",
        action="store_true",
        help="Run 5-section deep-research on top deep-n names",
    )
    sp500_p.add_argument(
        "--llm",
        action="store_true",
        help="Use Grok for deep-research (default when --deep-research set)",
    )
    sp500_p.add_argument(
        "--no-llm",
        action="store_true",
        help="Heuristic deep-research only (no API)",
    )
    sp500_p.add_argument(
        "--bias",
        type=str,
        default="call",
        choices=["call", "put", "hold"],
        help="call=strongest RS, put=weakest RS, hold=strongest for watch (default call)",
    )
    sp500_p.add_argument(
        "--min-dollar-vol",
        type=float,
        default=20_000_000.0,
        help="Min avg daily dollar volume filter (default 20e6)",
    )
    sp500_p.add_argument(
        "--rs-lookback",
        type=int,
        default=10,
        help="Bars for RS vs SPY (default 10)",
    )
    sp500_p.add_argument(
        "--quotes",
        type=str,
        default="yahoo",
        choices=["fixture", "live", "yahoo"],
        help="Quote source (default yahoo; fixture for offline tests)",
    )
    sp500_p.add_argument(
        "--no-remote-universe",
        action="store_true",
        help="Do not fetch live S&P 500 list (use cache/fallback sample)",
    )
    sp500_p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Scan output dir (default logs/sp500_scan)",
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

    obt_p = sub.add_parser(
        "options-backtest",
        help="Backtest one long-premium scenario (BS model; research only)",
    )
    obt_p.add_argument(
        "--scenario",
        type=str,
        default="balanced_40d_tp80_sl50",
        help="Seed name or path to scenario JSON",
    )
    obt_p.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated underlyings (default: liquid mega-caps + SPY)",
    )
    obt_p.add_argument("--start", type=str, default="2022-01-01")
    obt_p.add_argument("--end", type=str, default=None)
    obt_p.add_argument(
        "--synthetic",
        action="store_true",
        help="Use synthetic bars (no network; for tests)",
    )
    obt_p.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip train/test validation block",
    )
    obt_p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write result JSON under this dir (default logs/options_bt)",
    )
    obt_p.add_argument(
        "--list-scenarios",
        action="store_true",
        help="List built-in seed scenarios and exit",
    )

    osearch_p = sub.add_parser(
        "options-search",
        help="Iterate scenarios with a mutator agent until a good plan emerges",
    )
    osearch_p.add_argument(
        "--iterations",
        type=int,
        default=40,
        help="How many scenarios to evaluate (default 40)",
    )
    osearch_p.add_argument(
        "--target-win-rate",
        type=float,
        default=0.60,
        help="Target win rate (default 0.60)",
    )
    osearch_p.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated underlyings (include SPY for market filter)",
    )
    osearch_p.add_argument("--start", type=str, default="2022-01-01")
    osearch_p.add_argument("--end", type=str, default=None)
    osearch_p.add_argument("--synthetic", action="store_true")
    osearch_p.add_argument("--seed", type=int, default=42)
    osearch_p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output dir (default logs/options_search)",
    )

    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.cmd == "options-backtest":
        if args.list_scenarios:
            print(
                json.dumps(
                    [
                        {
                            "name": s.name,
                            "option_type": s.option_type,
                            "target_delta": s.target_delta,
                            "target_dte": s.target_dte,
                            "take_profit_pct": s.take_profit_pct,
                            "stop_loss_pct": s.stop_loss_pct,
                            "notes": s.notes,
                        }
                        for s in SEED_SCENARIOS
                    ],
                    indent=2,
                ),
                flush=True,
            )
            return 0
        try:
            scenario = scenario_from_name(args.scenario)
        except ValueError as e:
            print(json.dumps({"error": str(e)}), flush=True)
            return 2
        symbols = (
            [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
            if args.symbols
            else None
        )
        out_dir = args.out or (config.config_path.parent / "logs" / "options_bt")
        cache = config.config_path.parent / "logs" / "options_bt" / "price_cache.json"
        payload = run_single(
            scenario,
            symbols=symbols,
            start=args.start,
            end=args.end,
            synthetic=bool(args.synthetic),
            cache_path=None if args.synthetic else cache,
            validate=not bool(args.no_validate),
            out_dir=out_dir,
        )
        # Compact stdout (full trades in out dir)
        summary = {
            "scenario": payload.get("scenario", {}).get("name"),
            "metrics": payload.get("metrics"),
            "validation": (
                {
                    "passes": (payload.get("validation") or {}).get("passes"),
                    "reasons": (payload.get("validation") or {}).get("reasons"),
                    "test": (payload.get("validation") or {}).get("test"),
                    "train_n": ((payload.get("validation") or {}).get("train") or {}).get(
                        "n_trades"
                    ),
                    "score": (payload.get("validation") or {}).get("score"),
                }
                if payload.get("validation")
                else None
            ),
            "notes": payload.get("notes"),
            "out_dir": str(out_dir),
        }
        print(json.dumps(summary, indent=2), flush=True)
        return 0

    if args.cmd == "options-search":
        symbols = (
            [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
            if args.symbols
            else list(DEFAULT_SYMBOLS)
        )
        out_dir = args.out or (config.config_path.parent / "logs" / "options_search")
        cache = config.config_path.parent / "logs" / "options_bt" / "price_cache.json"
        result = run_search(
            symbols=symbols,
            iterations=max(5, int(args.iterations)),
            target_win_rate=float(args.target_win_rate),
            start=args.start,
            end=args.end,
            synthetic=bool(args.synthetic),
            seed=int(args.seed),
            out_dir=out_dir,
            cache_path=None if args.synthetic else cache,
        )
        # Compact leaderboard on stdout
        best = result.get("best") or {}
        plan = result.get("recommended_plan") or {}
        summary = {
            "iterations": result.get("iterations"),
            "target_win_rate": result.get("target_win_rate"),
            "best_name": (best.get("scenario") or {}).get("name"),
            "best_score": best.get("score"),
            "best_metrics": best.get("full_metrics"),
            "validation_passes": (best.get("validation") or {}).get("passes"),
            "validation_reasons": (best.get("validation") or {}).get("reasons"),
            "recommended_plan": plan,
            "leaderboard": [
                {
                    "name": (c.get("scenario") or {}).get("name"),
                    "score": c.get("score"),
                    "win_rate": (c.get("full_metrics") or {}).get("win_rate"),
                    "n_trades": (c.get("full_metrics") or {}).get("n_trades"),
                    "total_pnl_usd": (c.get("full_metrics") or {}).get("total_pnl_usd"),
                    "passes": (c.get("validation") or {}).get("passes"),
                }
                for c in (result.get("leaderboard") or [])[:10]
            ],
            "out_dir": str(out_dir),
            "note": (
                "Research only — BS model. Read logs/options_search/OPTIONS_PLAN.md. "
                "Paper before live; never auto-place."
            ),
        }
        print(json.dumps(summary, indent=2), flush=True)
        return 0

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
            place_without_confirm=bool(config.options_place_without_confirm),
            bp_usage_pct=float(getattr(config, "bp_usage_pct", 1.0)),
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
        record_review_result(
            review,
            request=request,
            path=out_path,
            place_without_confirm=bool(config.options_place_without_confirm),
        )
        recorded = json.loads(Path(out_path).read_text())
        print(
            json.dumps(
                {
                    "wrote": str(out_path),
                    "place_allowed": recorded.get("place_allowed"),
                    "human_confirm_required": recorded.get("human_confirm_required"),
                    "note": recorded.get("note"),
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
        if config.options_place_without_confirm and not proposal.blocked:
            out["note"] = (
                "PROPOSAL — standing auth ON: after chains/pick/review, agent may "
                "place_option_order without per-trade yes (Agentic rails still apply). "
                "This command itself never places."
            )
        else:
            out["note"] = (
                "PROPOSAL ONLY — place needs confirm or standing auth. "
                "This command itself never places."
            )
        print(json.dumps(out, indent=2), flush=True)
        return 2 if proposal.blocked else 0

    if args.cmd == "morning-paper":
        from agentic_trading.paper.morning import run_morning_paper

        session = getattr(args, "session_dir", None)
        if session is None:
            # Default isolated morning session under logs/
            today = datetime.now(ET).date().isoformat()
            session = Path(f"logs/paper_morning_{today}")
        result = run_morning_paper(
            config,
            quote_source=str(args.quotes or "yahoo"),
            use_llm=bool(args.llm),
            watch_ticks=max(0, int(args.watch_ticks)),
            trigger=not bool(args.no_trigger),
            daily_n=max(1, int(args.daily_n)),
            session_dir=session,
        )
        print(json.dumps(result.to_dict(), indent=2), flush=True)
        md = result.paths.get("morning_plan_md")
        if md:
            print(f"\n# plan: {md}", flush=True)
        return 0

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

    if args.cmd == "deep-research":
        ticker = str(args.ticker).upper().strip()
        peers = None
        if args.peers:
            peers = [p.strip() for p in str(args.peers).split(",") if p.strip()]
        use_llm = True
        if args.no_llm:
            use_llm = False
        elif args.llm:
            use_llm = True
        quote_src = str(args.quotes or "yahoo")
        # Fixture for offline; yahoo/live need yfinance
        quotes = build_quote_provider("fixture" if quote_src == "fixture" else quote_src)
        try:
            memo = run_deep_research(
                config,
                ticker,
                peers=peers,
                use_llm=use_llm,
                out_dir=args.out_dir,
                quote_provider=quotes,
            )
        except ValueError as e:
            print(json.dumps({"error": str(e)}, indent=2), flush=True)
            return 2
        print(memo.to_markdown(), flush=True)
        summary = {
            "ticker": memo.ticker,
            "peers": memo.peers,
            "mode": memo.mode,
            "verdict": memo.verdict,
            "conviction": memo.conviction,
            "one_liner": memo.one_liner,
            "paths": memo.paths,
            "note": (
                "Advisory only — does not write daily_focus or place orders. "
                "Promote pass/caution names via research --apply-daily after scan."
            ),
        }
        print(json.dumps(summary, indent=2), flush=True)
        # Non-zero if fail verdict so scripts can gate
        return 1 if memo.verdict == "fail" else 0

    if args.cmd == "sp500-scan":
        use_llm = True
        if args.no_llm:
            use_llm = False
        elif args.llm:
            use_llm = True
        quote_src = str(args.quotes or "yahoo")
        result = run_sp500_scan(
            config,
            top_n=max(1, int(args.top)),
            deep_n=max(0, int(args.deep_n)),
            deep_research=bool(args.deep_research),
            use_llm=use_llm,
            bias=str(args.bias or "call"),
            min_dollar_vol=float(args.min_dollar_vol),
            rs_lookback=max(2, int(args.rs_lookback)),
            quote_source=quote_src,
            out_dir=args.out_dir,
            allow_remote_universe=not bool(args.no_remote_universe),
        )
        print(result.to_markdown(), flush=True)
        summary = {
            "universe_source": result.universe_source,
            "universe_count": result.universe_count,
            "liquid_count": result.liquid_count,
            "bias": result.bias,
            "top": [r.symbol for r in result.top],
            "survivors": result.survivors,
            "deep_memos": [
                {"ticker": m.get("ticker"), "verdict": m.get("verdict")}
                for m in result.deep_memos
            ],
            "paths": result.paths,
            "note": (
                "Advisory only — does not write daily_focus or place orders. "
                "Review survivors, then research --apply-daily if you want them in focus."
            ),
        }
        print(json.dumps(summary, indent=2), flush=True)
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
                "take_profit_pct_low": config.option_take_profit_pct_low,
                "take_profit_pct_high": config.option_take_profit_pct_high,
                "stop_loss_pct": config.option_stop_loss_pct,
                "exit_dte": config.option_exit_dte,
                "max_open_options": config.max_open_options,
                "daily_account_halt_pct": config.risk.max_daily_loss_pct,
                "place_without_confirm": config.options_place_without_confirm,
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
