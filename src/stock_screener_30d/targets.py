from __future__ import annotations

from typing import Any

import pandas as pd


def trading_days_ahead(start: pd.Timestamp, n_days: int) -> pd.Timestamp:
    """Project exit date n trading days ahead using US business days."""
    return start + pd.tseries.offsets.BDay(n_days)


def enrich_with_targets(df: pd.DataFrame, hold_days: int = 30) -> pd.DataFrame:
    """Add entry, exit date, stop-loss, and risk columns to scan results."""
    if df.empty:
        return df

    out = df.copy()
    out["entry_price"] = out["price"]
    out["entry_date"] = pd.to_datetime(out["scan_date"]).dt.strftime("%Y-%m-%d")
    out["exit_date"] = out["scan_date"].apply(
        lambda d: trading_days_ahead(pd.Timestamp(d), hold_days).strftime("%Y-%m-%d")
    )
    out["stop_loss"] = out["sma_50"]
    out["exit_stop_price"] = out["sma_50"]
    out["exit_target_price"] = out["high_52w"]
    out["exit_target_pct"] = (
        (out["exit_target_price"] - out["entry_price"]) / out["entry_price"] * 100
    ).round(2)
    out["risk_pct"] = ((out["entry_price"] - out["stop_loss"]) / out["entry_price"] * 100).round(2)
    out["hold_days"] = hold_days
    out["exit_rule"] = out["exit_date"].apply(
        lambda d: f"Time exit: sell at close on {d} ({hold_days} trading days)"
    )
    return out


def format_targets_table(df: pd.DataFrame) -> str:
    """Format scan results with trade targets for terminal output."""
    display = df[
        [
            "ticker",
            "entry_price",
            "entry_date",
            "exit_date",
            "exit_stop_price",
            "exit_target_price",
            "exit_target_pct",
            "risk_pct",
            "rsi",
            "pullback_pct",
            "score",
        ]
    ].copy()
    display.columns = [
        "Ticker",
        "Entry",
        "EntryDate",
        "ExitDate",
        "StopExit",
        "TargetExit",
        "Target%",
        "Risk%",
        "RSI",
        "Pullback%",
        "Score",
    ]
    display["Entry"] = display["Entry"].map(lambda x: f"${x:.2f}")
    display["StopExit"] = display["StopExit"].map(lambda x: f"${x:.2f}")
    display["TargetExit"] = display["TargetExit"].map(lambda x: f"${x:.2f}")
    display["Target%"] = display["Target%"].map(lambda x: f"+{x:.1f}%")
    display["Risk%"] = display["Risk%"].map(lambda x: f"{x:.1f}%")
    display["RSI"] = display["RSI"].map(lambda x: f"{x:.1f}")
    display["Pullback%"] = display["Pullback%"].map(lambda x: f"{x:.1f}%")
    display["Score"] = display["Score"].map(lambda x: f"{x:.3f}")
    return display.to_string(index=False)


def targets_summary(config: dict[str, Any]) -> str:
    hold = config.get("backtest", {}).get("hold_days", 30)
    cost = config.get("backtest", {}).get("round_trip_cost_pct", 0.1)
    return (
        f"Trade plan: enter at close on EntryDate, exit at close on ExitDate "
        f"({hold} trading days). Stop reference: 50-day SMA (not auto-sold). "
        f"Backtest assumes {cost}% round-trip cost."
    )