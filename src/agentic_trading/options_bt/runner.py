"""CLI helpers for options backtest + scenario search."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from agentic_trading.options_bt.agent import SearchConfig, format_plan_markdown, run_scenario_search
from agentic_trading.options_bt.backtest import run_backtest
from agentic_trading.options_bt.data import fetch_yfinance, synthetic_bars
from agentic_trading.options_bt.scenario import OptionScenario, SEED_SCENARIOS
from agentic_trading.options_bt.validate import validate_scenario


DEFAULT_SYMBOLS = [
    "SPY",
    "QQQ",
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "TSLA",
    "AMD",
    "AVGO",
    "NFLX",
    "SMH",
    "ORCL",
]


def load_series(
    *,
    symbols: list[str],
    start: str = "2022-01-01",
    end: str | None = None,
    synthetic: bool = False,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    if synthetic:
        out = {}
        for i, sym in enumerate(symbols):
            out[sym.upper()] = synthetic_bars(
                sym,
                n=500,
                start_price=100.0 + i * 20,
                drift=0.00025 + (i % 3) * 0.00005,
                vol=0.012 + (i % 4) * 0.003,
                seed=42 + i,
            )
        return out
    return fetch_yfinance(
        symbols,
        start=start,
        end=end,
        cache_path=cache_path,
    )


def run_single(
    scenario: OptionScenario,
    *,
    symbols: list[str] | None = None,
    start: str = "2022-01-01",
    end: str | None = None,
    synthetic: bool = False,
    cache_path: Path | None = None,
    validate: bool = True,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    syms = symbols or list(scenario.symbols) + [scenario.market_symbol]
    if scenario.market_symbol.upper() not in [s.upper() for s in syms]:
        syms = [scenario.market_symbol] + syms
    series = load_series(
        symbols=syms,
        start=start,
        end=end,
        synthetic=synthetic,
        cache_path=cache_path,
    )
    sc = OptionScenario.from_dict(scenario.to_dict())
    sc.symbols = [s for s in (symbols or scenario.symbols) if s.upper() != sc.market_symbol.upper()]
    bt = run_backtest(sc, series)
    payload: dict[str, Any] = {
        "scenario": sc.to_dict(),
        "metrics": bt.metrics.to_dict(),
        "n_trades": len(bt.trades),
        "notes": bt.notes,
        "trades_sample": [t.to_dict() for t in bt.trades[:20]],
    }
    if validate:
        val = validate_scenario(sc, series)
        payload["validation"] = val.to_dict()
    if out_dir:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "backtest_result.json").write_text(json.dumps(payload, indent=2) + "\n")
        if bt.trades:
            (out_dir / "trades.json").write_text(
                json.dumps([t.to_dict() for t in bt.trades], indent=2) + "\n"
            )
    return payload


def run_search(
    *,
    symbols: list[str] | None = None,
    iterations: int = 40,
    target_win_rate: float = 0.60,
    start: str = "2022-01-01",
    end: str | None = None,
    synthetic: bool = False,
    seed: int = 42,
    out_dir: Path | None = None,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    syms = symbols or DEFAULT_SYMBOLS
    series = load_series(
        symbols=syms,
        start=start,
        end=end,
        synthetic=synthetic,
        cache_path=cache_path,
    )
    trade_syms = [s for s in syms if s.upper() != "SPY"]
    cfg = SearchConfig(
        iterations=iterations,
        target_win_rate=target_win_rate,
        seed=seed,
        out_dir=out_dir,
        prefer_big_money=True,
    )
    result = run_scenario_search(series, cfg, symbols=trade_syms)
    return result.to_dict()


def scenario_from_name(name: str) -> OptionScenario:
    for s in SEED_SCENARIOS:
        if s.name == name:
            return OptionScenario.from_dict(s.to_dict())
    # allow path to json
    p = Path(name)
    if p.is_file():
        return OptionScenario.from_dict(json.loads(p.read_text()))
    raise ValueError(f"Unknown scenario {name!r}; known: {[s.name for s in SEED_SCENARIOS]}")
