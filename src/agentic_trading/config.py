from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agentic_trading.models import TradingMode

# Hard floors — config may only tighten (smaller / fewer / stricter) relative to these ceilings.
FLOOR_MAX_ORDER_NOTIONAL = 5000.0
FLOOR_MAX_DAILY_LOSS_PCT = 0.10  # allow up to 10% daily; default playbook uses 5%
FLOOR_MAX_ORDERS_PER_DAY = 50
FLOOR_MAX_POSITION_PCT = 1.0
FLOOR_MAX_OPEN_POSITIONS = 10
FLOOR_MAX_RISK_PER_TRADE_PCT = 0.10  # allow up to 10% risk/trade; playbook uses 5%


@dataclass(frozen=True)
class RiskConfig:
    max_position_pct: float
    max_open_positions: int
    max_orders_per_day: int
    max_daily_loss_pct: float
    max_order_notional: float
    risk_per_trade_pct: float = 0.05  # $ risk if stop hits
    reward_risk_ratio: float = 2.0  # take-profit = R * this


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    sma_period: int
    lookback_bars: int
    market_symbol: str = "SPY"
    range_lookback: int = 8  # bars for range high/low (breakout / stop)
    pullback_tol_pct: float = 0.004  # how close to SMA counts as pullback
    # R1: market-red soft-exit hysteresis (autopsy: first red tick churn)
    market_red_exit_ticks: int = 2  # consecutive red ticks before soft-exit
    soft_exit_min_hold_ticks: int = 2  # no filter soft-exit until held this many ticks
    market_red_sma_buffer_pct: float = 0.001  # exit-red only if last < SMA*(1-buffer)
    # R2: re-entry cooldown after market-red soft-exit
    reentry_cooldown_ticks: int = 3


@dataclass(frozen=True)
class SelectorConfig:
    enabled: bool = True
    max_new_entries_per_tick: int = 2
    prefer_relative_strength: bool = True
    rs_lookback: int = 10


@dataclass(frozen=True)
class DailyFocusConfig:
    """Only allow NEW entries in today's research picks (exits always allowed)."""

    enabled: bool = True
    path: Path | None = None
    count: int = 3


@dataclass(frozen=True)
class AppConfig:
    trading_mode: TradingMode
    symbols: list[str]
    strategy: StrategyConfig
    risk: RiskConfig
    starting_equity: float
    currency: str
    allow_live: bool
    log_path: Path
    loop_interval_seconds: int
    config_path: Path
    settlement_days: int = 1  # track formal T+1 settlement
    # True: buy with any cash after a sale. False: settled-only (RH BP lag lesson).
    trade_when_cash_available: bool = False
    paper_state_path: Path | None = None
    selector: SelectorConfig = SelectorConfig()
    daily_focus: DailyFocusConfig = DailyFocusConfig()
    # Live Agentic (MCP snapshot + supervised option proposals)
    agentic_account_number: str = ""
    live_portfolio_path: Path | None = None
    option_proposal_path: Path | None = None
    max_option_premium: float = 100.0
    max_option_contracts: int = 1
    option_min_dte: int = 7
    option_max_dte: int = 31
    # Long-premium manage rules (premium % change from entry)
    option_take_profit_pct_low: float = 0.10
    option_take_profit_pct_high: float = 0.20
    option_stop_loss_pct: float = 0.10
    option_exit_dte: int = 3
    max_open_options: int = 1
    # True: agent may place after clean review without per-trade user yes (Agentic rails still apply)
    options_place_without_confirm: bool = False


def _clamp_risk(raw: dict[str, Any]) -> RiskConfig:
    max_order = float(raw.get("max_order_notional", 1000.0))
    max_order = min(max_order, FLOOR_MAX_ORDER_NOTIONAL)
    if max_order <= 0:
        max_order = 1000.0

    max_daily = float(raw.get("max_daily_loss_pct", 0.05))
    max_daily = min(max_daily, FLOOR_MAX_DAILY_LOSS_PCT)
    if max_daily <= 0:
        max_daily = 0.05

    max_orders = int(raw.get("max_orders_per_day", 10))
    max_orders = min(max_orders, FLOOR_MAX_ORDERS_PER_DAY)
    if max_orders < 1:
        max_orders = 1

    max_pos_pct = float(raw.get("max_position_pct", 0.50))
    max_pos_pct = min(max_pos_pct, FLOOR_MAX_POSITION_PCT)
    if max_pos_pct <= 0:
        max_pos_pct = 0.50

    max_open = int(raw.get("max_open_positions", 3))
    max_open = min(max_open, FLOOR_MAX_OPEN_POSITIONS)
    if max_open < 1:
        max_open = 1

    risk_pt = float(raw.get("risk_per_trade_pct", 0.05))
    risk_pt = min(max(risk_pt, 0.001), FLOOR_MAX_RISK_PER_TRADE_PCT)

    rr = float(raw.get("reward_risk_ratio", 2.0))
    if rr < 0.5:
        rr = 2.0

    return RiskConfig(
        max_position_pct=max_pos_pct,
        max_open_positions=max_open,
        max_orders_per_day=max_orders,
        max_daily_loss_pct=max_daily,
        max_order_notional=max_order,
        risk_per_trade_pct=risk_pt,
        reward_risk_ratio=rr,
    )


def load_config(path: str | Path | None = None) -> AppConfig:
    if path is None:
        candidates = [
            Path.cwd() / "config.yaml",
            Path(__file__).resolve().parents[2] / "config.yaml",
        ]
        cfg_path = next((p for p in candidates if p.is_file()), candidates[0])
    else:
        cfg_path = Path(path)

    if not cfg_path.is_file():
        raise FileNotFoundError(f"Config not found: {cfg_path}")

    data = yaml.safe_load(cfg_path.read_text()) or {}
    mode_str = str(data.get("trading_mode", "paper")).lower()
    try:
        mode = TradingMode(mode_str)
    except ValueError as e:
        raise ValueError(f"Invalid trading_mode: {mode_str}") from e

    broker = data.get("broker") or {}
    allow_live = bool(broker.get("allow_live", False))
    if mode == TradingMode.LIVE and not allow_live:
        mode = TradingMode.PAPER

    strat = data.get("strategy") or {}
    account = data.get("account") or {}
    logging_cfg = data.get("logging") or {}
    loop = data.get("loop") or {}

    log_path = Path(logging_cfg.get("path", "logs/decisions.jsonl"))
    if not log_path.is_absolute():
        log_path = (cfg_path.parent / log_path).resolve()

    symbols = list(data.get("symbols") or ["SPY"])
    symbols = [s.upper().strip() for s in symbols if str(s).strip()]

    settlement_days = int(broker.get("settlement_days", account.get("settlement_days", 1)))
    if settlement_days < 0:
        settlement_days = 1
    trade_when_cash_available = bool(broker.get("trade_when_cash_available", False))

    state_raw = logging_cfg.get("paper_state_path", "logs/paper_state.json")
    state_path = Path(state_raw)
    if not state_path.is_absolute():
        state_path = (cfg_path.parent / state_path).resolve()

    sel = data.get("selector") or {}
    selector = SelectorConfig(
        enabled=bool(sel.get("enabled", True)),
        max_new_entries_per_tick=max(1, int(sel.get("max_new_entries_per_tick", 2))),
        prefer_relative_strength=bool(sel.get("prefer_relative_strength", True)),
        rs_lookback=max(3, int(sel.get("rs_lookback", 10))),
    )

    df = data.get("daily_focus") or {}
    df_path_raw = df.get("path", "logs/daily_focus.json")
    df_path = Path(df_path_raw)
    if not df_path.is_absolute():
        df_path = (cfg_path.parent / df_path).resolve()
    daily_focus = DailyFocusConfig(
        enabled=bool(df.get("enabled", True)),
        path=df_path,
        count=max(1, int(df.get("count", 3))),
    )

    live_cfg = data.get("live") or {}
    agentic_acct = str(
        broker.get("agentic_account_number")
        or live_cfg.get("agentic_account_number")
        or ""
    ).strip()

    def _rel(raw: str, default: str) -> Path:
        p = Path(raw or default)
        if not p.is_absolute():
            p = (cfg_path.parent / p).resolve()
        return p

    live_port_path = _rel(
        str(live_cfg.get("portfolio_path", "logs/live_portfolio.json")),
        "logs/live_portfolio.json",
    )
    opt_prop_path = _rel(
        str(live_cfg.get("proposal_path", "logs/option_proposal.json")),
        "logs/option_proposal.json",
    )
    max_opt_prem = float(live_cfg.get("max_option_premium", 100.0))
    max_opt_prem = max(1.0, min(max_opt_prem, 500.0))
    max_opt_contracts = max(1, min(int(live_cfg.get("max_option_contracts", 1)), 5))
    opt_min_dte = max(1, int(live_cfg.get("min_dte", 7)))
    opt_max_dte = max(opt_min_dte, int(live_cfg.get("max_dte", 31)))
    tp_lo = max(0.05, min(2.0, float(live_cfg.get("option_take_profit_pct_low", 0.10))))
    tp_hi = max(tp_lo, min(3.0, float(live_cfg.get("option_take_profit_pct_high", 0.20))))
    # User may choose tight stops (e.g. 10%); allow 5%–90% (warn in proposals if ≤15%)
    opt_sl = max(0.05, min(0.90, float(live_cfg.get("option_stop_loss_pct", 0.10))))
    opt_exit_dte = max(1, min(14, int(live_cfg.get("option_exit_dte", 3))))
    max_open_opts = max(1, min(3, int(live_cfg.get("max_open_options", 1))))
    place_wo_confirm = bool(live_cfg.get("options_place_without_confirm", False))

    return AppConfig(
        trading_mode=mode,
        symbols=symbols,
        strategy=StrategyConfig(
            name=str(strat.get("name", "day_trade_playbook")),
            sma_period=int(strat.get("sma_period", 10)),
            lookback_bars=int(strat.get("lookback_bars", 30)),
            market_symbol=str(strat.get("market_symbol", "SPY")).upper(),
            range_lookback=int(strat.get("range_lookback", 8)),
            pullback_tol_pct=float(strat.get("pullback_tol_pct", 0.004)),
            market_red_exit_ticks=max(1, int(strat.get("market_red_exit_ticks", 2))),
            soft_exit_min_hold_ticks=max(0, int(strat.get("soft_exit_min_hold_ticks", 2))),
            market_red_sma_buffer_pct=max(
                0.0, float(strat.get("market_red_sma_buffer_pct", 0.001))
            ),
            reentry_cooldown_ticks=max(0, int(strat.get("reentry_cooldown_ticks", 3))),
        ),
        risk=_clamp_risk(data.get("risk") or {}),
        starting_equity=float(account.get("starting_equity", 1000.0)),
        currency=str(account.get("currency", "USD")),
        allow_live=allow_live,
        log_path=log_path,
        loop_interval_seconds=int(loop.get("interval_seconds", 60)),
        config_path=cfg_path.resolve(),
        settlement_days=settlement_days,
        trade_when_cash_available=trade_when_cash_available,
        paper_state_path=state_path,
        selector=selector,
        daily_focus=daily_focus,
        agentic_account_number=agentic_acct,
        live_portfolio_path=live_port_path,
        option_proposal_path=opt_prop_path,
        max_option_premium=max_opt_prem,
        max_option_contracts=max_opt_contracts,
        option_min_dte=opt_min_dte,
        option_max_dte=opt_max_dte,
        option_take_profit_pct_low=tp_lo,
        option_take_profit_pct_high=tp_hi,
        option_stop_loss_pct=opt_sl,
        option_exit_dte=opt_exit_dte,
        max_open_options=max_open_opts,
        options_place_without_confirm=place_wo_confirm,
    )
