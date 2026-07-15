from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agentic_trading.models import TradingMode

# Hard floors — config may only tighten (smaller / fewer / stricter).
FLOOR_MAX_ORDER_NOTIONAL = 500.0
FLOOR_MAX_DAILY_LOSS_PCT = 0.05
FLOOR_MAX_ORDERS_PER_DAY = 50
FLOOR_MAX_POSITION_PCT = 0.50
FLOOR_MAX_OPEN_POSITIONS = 10


@dataclass(frozen=True)
class RiskConfig:
    max_position_pct: float
    max_open_positions: int
    max_orders_per_day: int
    max_daily_loss_pct: float
    max_order_notional: float


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    sma_period: int
    lookback_bars: int


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
    # True: buy with any cash in account after a sale (no wait). False: settled-only.
    trade_when_cash_available: bool = True
    paper_state_path: Path | None = None


def _clamp_risk(raw: dict[str, Any]) -> RiskConfig:
    max_order = float(raw.get("max_order_notional", 100.0))
    max_order = min(max_order, FLOOR_MAX_ORDER_NOTIONAL)
    if max_order <= 0:
        max_order = 100.0

    max_daily = float(raw.get("max_daily_loss_pct", 0.02))
    max_daily = min(max_daily, FLOOR_MAX_DAILY_LOSS_PCT)
    if max_daily <= 0:
        max_daily = 0.02

    max_orders = int(raw.get("max_orders_per_day", 10))
    max_orders = min(max_orders, FLOOR_MAX_ORDERS_PER_DAY)
    if max_orders < 1:
        max_orders = 1

    max_pos_pct = float(raw.get("max_position_pct", 0.20))
    max_pos_pct = min(max_pos_pct, FLOOR_MAX_POSITION_PCT)
    if max_pos_pct <= 0:
        max_pos_pct = 0.20

    max_open = int(raw.get("max_open_positions", 3))
    max_open = min(max_open, FLOOR_MAX_OPEN_POSITIONS)
    if max_open < 1:
        max_open = 1

    return RiskConfig(
        max_position_pct=max_pos_pct,
        max_open_positions=max_open,
        max_orders_per_day=max_orders,
        max_daily_loss_pct=max_daily,
        max_order_notional=max_order,
    )


def load_config(path: str | Path | None = None) -> AppConfig:
    if path is None:
        # Prefer cwd config.yaml, then package-adjacent project root.
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
    # Live only if both mode=live AND allow_live
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
    # Default True: when cash is in the account, trade immediately (no 1-day delay).
    trade_when_cash_available = bool(broker.get("trade_when_cash_available", True))

    state_raw = logging_cfg.get("paper_state_path", "logs/paper_state.json")
    state_path = Path(state_raw)
    if not state_path.is_absolute():
        state_path = (cfg_path.parent / state_path).resolve()

    return AppConfig(
        trading_mode=mode,
        symbols=symbols,
        strategy=StrategyConfig(
            name=str(strat.get("name", "simple_momentum")),
            sma_period=int(strat.get("sma_period", 5)),
            lookback_bars=int(strat.get("lookback_bars", 20)),
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
    )
