from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "criteria.yaml"

# Liquid S&P 500 sample for MVP (no external ticker list dependency)
SP500_SAMPLE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "BRK-B", "JPM", "V", "UNH",
    "XOM", "JNJ", "WMT", "MA", "PG", "HD", "CVX", "MRK", "ABBV", "KO",
    "PEP", "COST", "AVGO", "TMO", "MCD", "CSCO", "ACN", "ABT", "DHR", "NEE",
    "LIN", "ADBE", "WFC", "PM", "TXN", "CRM", "AMD", "ORCL", "INTC", "QCOM",
    "IBM", "AMAT", "GE", "CAT", "BA", "DIS", "NFLX", "PYPL", "UBER",
]


def load_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or DEFAULT_CONFIG_PATH
    with config_path.open() as f:
        cfg = yaml.safe_load(f)
    if not cfg or not isinstance(cfg, dict):
        raise ValueError(f"Invalid config at {config_path}")
    for key in ("screening", "output", "backtest"):
        if key not in cfg:
            raise ValueError(f"Missing required config section: {key}")
    s = cfg["screening"]
    if s["rsi_min"] >= s["rsi_max"]:
        raise ValueError("rsi_min must be less than rsi_max")
    if s["pullback_min_pct"] >= s["pullback_max_pct"]:
        raise ValueError("pullback_min_pct must be less than pullback_max_pct")
    return cfg


def get_universe(config: dict[str, Any]) -> list[str]:
    source = config.get("universe", {}).get("tickers_source", "sp500_sample")
    if source == "sp500_sample":
        return SP500_SAMPLE.copy()
    raise ValueError(f"Unknown tickers_source: {source}")