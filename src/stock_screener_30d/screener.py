from __future__ import annotations

from typing import Any

import pandas as pd

from stock_screener_30d.config import get_universe
from stock_screener_30d.data import fetch_ticker_metrics


def passes_criteria(metrics: dict[str, Any], screening: dict[str, Any]) -> bool:
    if not metrics["above_sma"]:
        return False
    if not (screening["rsi_min"] <= metrics["rsi"] <= screening["rsi_max"]):
        return False
    if not (screening["pullback_min_pct"] <= metrics["pullback_pct"] <= screening["pullback_max_pct"]):
        return False
    if metrics["avg_volume"] < screening["min_avg_volume"]:
        return False
    return True


def composite_score(metrics: dict[str, Any], screening: dict[str, Any]) -> float:
    """Higher = better 30-day swing candidate. RSI near midpoint + moderate pullback."""
    rsi_range = screening["rsi_max"] - screening["rsi_min"]
    pullback_range = screening["pullback_max_pct"] - screening["pullback_min_pct"]
    if rsi_range <= 0 or pullback_range <= 0:
        return 0.0
    rsi_mid = (screening["rsi_min"] + screening["rsi_max"]) / 2
    rsi_score = 1 - abs(metrics["rsi"] - rsi_mid) / rsi_range
    pullback_mid = (screening["pullback_min_pct"] + screening["pullback_max_pct"]) / 2
    pullback_score = 1 - abs(metrics["pullback_pct"] - pullback_mid) / pullback_range
    volume_score = min(metrics["avg_volume"] / screening["min_avg_volume"], 3) / 3
    return rsi_score * 0.4 + pullback_score * 0.4 + volume_score * 0.2


def run_scan(config: dict[str, Any], tickers: list[str] | None = None) -> pd.DataFrame:
    screening = config["screening"]
    top_n = max(1, config["output"]["top_n"])
    universe = tickers if tickers is not None else get_universe(config)
    sma_days = screening.get("above_sma_days", 50)

    rows: list[dict[str, Any]] = []
    for ticker in universe:
        metrics = fetch_ticker_metrics(ticker, sma_days=sma_days)
        if metrics is None:
            continue
        if not passes_criteria(metrics, screening):
            continue
        metrics["score"] = composite_score(metrics, screening)
        rows.append(metrics)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.sort_values("score", ascending=False).head(top_n)
    return df.reset_index(drop=True)