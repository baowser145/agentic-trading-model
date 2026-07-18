from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from stock_screener_30d.backtest_cache import load_cached_backtest
from stock_screener_30d.config import load_config
from stock_screener_30d.data import fetch_history
from stock_screener_30d.paper_log import load_log, open_positions_status


def _spy_return_since(start_date: str, cost_pct: float = 0.1) -> float | None:
    """SPY total return % from start_date to today (single buy-hold, minus round-trip cost)."""
    end = date.today().isoformat()
    spy = fetch_history("SPY", start_date, end)
    if spy is None or len(spy) < 2:
        return None
    entry = float(spy.iloc[0])
    exit_p = float(spy.iloc[-1])
    gross = (exit_p - entry) / entry * 100
    return round(gross - cost_pct, 2)


def _verdict(
    paper_closed_avg: float | None,
    paper_closed_n: int,
    open_avg: float | None,
    backtest_avg: float | None,
    paper_spy: float | None,
) -> dict[str, str]:
    if paper_closed_n < 1:
        msg = "Collecting data — log daily and wait for trades to close (30 days)."
        if open_avg is not None:
            msg += f" Open positions averaging {open_avg:+.2f}% unrealized."
        return {"status": "collecting", "label": "TOO EARLY", "message": msg}

    if paper_closed_n < 3:
        return {
            "status": "collecting",
            "label": "EARLY SIGNAL",
            "message": f"{paper_closed_n} closed trade(s) so far (need 3+ for confidence). "
            f"Avg return: {paper_closed_avg:+.2f}% per trade.",
        }

    if backtest_avg is None:
        return {"status": "unknown", "label": "NO BACKTEST", "message": "Run backtest to compare."}

    ratio = paper_closed_avg / backtest_avg if backtest_avg != 0 else 0
    if ratio >= 0.8:
        label, status = "ON TRACK", "good"
        message = f"Paper avg ({paper_closed_avg:+.2f}%) tracks backtest ({backtest_avg:+.2f}% per period)."
    elif ratio >= 0.5:
        label, status = "LAGGING", "warn"
        message = f"Paper ({paper_closed_avg:+.2f}%) below backtest ({backtest_avg:+.2f}%). Watch closely."
    else:
        label, status = "OFF TRACK", "bad"
        message = f"Paper ({paper_closed_avg:+.2f}%) far below backtest ({backtest_avg:+.2f}%). Reshape criteria."

    if paper_spy is not None and paper_closed_avg is not None:
        if paper_closed_avg > paper_spy:
            message += f" Beating SPY ({paper_spy:+.2f}%) over same window."
        else:
            message += f" Under SPY ({paper_spy:+.2f}%) over same window."

    return {"status": status, "label": label, "message": message}


def paper_vs_backtest() -> dict[str, Any]:
    cfg = load_config()
    cost = cfg.get("backtest", {}).get("round_trip_cost_pct", 0.1)
    hold_days = cfg.get("backtest", {}).get("hold_days", 30)

    backtest = load_cached_backtest() or {}
    df = load_log()
    open_df = open_positions_status()

    paper: dict[str, Any] = {
        "label": "Paper Trading (Live)",
        "period": "—",
        "trades_total": 0,
        "trades_closed": 0,
        "trades_open": 0,
        "avg_return_pct": None,
        "win_rate_pct": None,
        "annualized_pct": None,
        "vs_spy_pct": None,
        "note": "No paper trades logged yet.",
    }

    if not df.empty:
        closed = df[df["status"] == "closed"]
        open_n = int((df["status"] == "open").sum())
        first_date = str(df["entry_date"].min())
        last_date = str(df["entry_date"].max())
        period = first_date if first_date == last_date else f"{first_date} → {last_date}"

        paper.update(
            {
                "period": period,
                "trades_total": len(df),
                "trades_closed": len(closed),
                "trades_open": open_n,
                "first_trade_date": first_date,
                "hold_days": hold_days,
            }
        )

        if not closed.empty:
            rets = closed["return_pct_net"].astype(float)
            wins = (rets > 0).sum()
            paper["avg_return_pct"] = round(rets.mean(), 2)
            paper["win_rate_pct"] = round(wins / len(closed) * 100, 1)
            paper["total_return_pct"] = round(rets.sum(), 2)
            # Rough annualization: assume ~12 trades/year at 30-day holds
            paper["annualized_pct"] = round(rets.mean() * 12, 2) if len(closed) >= 3 else None
            paper["note"] = f"{len(closed)} closed trade(s) realized."
        else:
            paper["note"] = f"{open_n} open trade(s) — no closed trades yet."

        if not open_df.empty and "unrealized_pct" in open_df.columns:
            unreal = open_df["unrealized_pct"].dropna()
            if len(unreal):
                paper["open_avg_unrealized_pct"] = round(unreal.mean(), 2)
                if closed.empty:
                    paper["avg_return_pct"] = paper["open_avg_unrealized_pct"]
                    paper["note"] = f"{open_n} open — avg unrealized {paper['open_avg_unrealized_pct']:+.2f}%."

        paper["vs_spy_pct"] = _spy_return_since(first_date, cost)

    simulated: dict[str, Any] = {
        "label": "Backtest (Simulated)",
        "period": backtest.get("period", "2019-2024"),
        "trades_total": backtest.get("num_rebalances"),
        "avg_return_pct": backtest.get("strategy_avg_per_period_pct"),
        "win_rate_pct": backtest.get("win_rate_pct"),
        "annualized_pct": backtest.get("strategy_annualized_pct"),
        "vs_spy_pct": backtest.get("benchmark_annualized_pct"),
        "cached_at": backtest.get("cached_at"),
        "note": "Historical simulation, monthly rebalances.",
    }
    if not backtest or backtest.get("error"):
        simulated["note"] = "Run backtest to populate."
        simulated["avg_return_pct"] = None

    verdict = _verdict(
        paper.get("avg_return_pct"),
        paper.get("trades_closed", 0),
        paper.get("open_avg_unrealized_pct"),
        simulated.get("avg_return_pct"),
        paper.get("vs_spy_pct"),
    )

    gap = None
    if paper.get("avg_return_pct") is not None and simulated.get("avg_return_pct") is not None:
        gap = round(paper["avg_return_pct"] - simulated["avg_return_pct"], 2)

    return {
        "paper": paper,
        "backtest": simulated,
        "gap_pct": gap,
        "verdict": verdict,
        "disclaimer": "Paper results are real logged picks. Backtest is simulated history. Compare after 3+ closed trades.",
    }