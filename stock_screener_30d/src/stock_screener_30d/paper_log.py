from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from stock_screener_30d.config import DEFAULT_CONFIG_PATH
from stock_screener_30d.data import _safe_float
from stock_screener_30d.screener import run_scan
from stock_screener_30d.targets import enrich_with_targets

PROJECT_ROOT = DEFAULT_CONFIG_PATH.parents[1]
DEFAULT_LOG_PATH = PROJECT_ROOT / "data" / "paper-trades.csv"

LOG_COLUMNS = [
    "trade_id",
    "logged_at",
    "ticker",
    "entry_date",
    "entry_price",
    "exit_date",
    "stop_loss",
    "hold_days",
    "score",
    "rsi",
    "pullback_pct",
    "status",
    "exit_price",
    "return_pct_gross",
    "return_pct_net",
    "closed_date",
]


def log_path(custom: Path | None = None) -> Path:
    path = custom or DEFAULT_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_log(path: Path | None = None) -> pd.DataFrame:
    p = log_path(path)
    if not p.exists():
        return pd.DataFrame(columns=LOG_COLUMNS)
    df = pd.read_csv(p)
    for col in LOG_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[LOG_COLUMNS]


def save_log(df: pd.DataFrame, path: Path | None = None) -> Path:
    p = log_path(path)
    df.to_csv(p, index=False)
    return p


def _trade_id(ticker: str, entry_date: str) -> str:
    return f"{ticker}_{entry_date}"


def _fetch_close_on_date(ticker: str, target: date) -> float | None:
    start = (pd.Timestamp(target) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    end = (pd.Timestamp(target) + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    try:
        hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
    except Exception:
        return None
    if hist is None or hist.empty:
        return None
    idx = hist.index.normalize()
    target_ts = pd.Timestamp(target).normalize()
    on_or_before = idx[idx <= target_ts]
    if on_or_before.empty:
        return None
    return _safe_float(hist.loc[on_or_before[-1], "Close"])


def scan_to_log_rows(cfg: dict[str, Any]) -> pd.DataFrame:
    hold_days = cfg.get("backtest", {}).get("hold_days", 30)
    df = run_scan(cfg)
    if df.empty:
        return pd.DataFrame(columns=LOG_COLUMNS)
    df = enrich_with_targets(df, hold_days=hold_days)
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for _, r in df.iterrows():
        entry_date = r["entry_date"]
        rows.append(
            {
                "trade_id": _trade_id(r["ticker"], entry_date),
                "logged_at": now,
                "ticker": r["ticker"],
                "entry_date": entry_date,
                "entry_price": round(float(r["entry_price"]), 4),
                "exit_date": r["exit_date"],
                "stop_loss": round(float(r["stop_loss"]), 4),
                "hold_days": int(r["hold_days"]),
                "score": round(float(r["score"]), 4),
                "rsi": round(float(r["rsi"]), 2),
                "pullback_pct": round(float(r["pullback_pct"]), 2),
                "status": "open",
                "exit_price": pd.NA,
                "return_pct_gross": pd.NA,
                "return_pct_net": pd.NA,
                "closed_date": pd.NA,
            }
        )
    return pd.DataFrame(rows)


def append_scan(cfg: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    existing = load_log(path)
    new_rows = scan_to_log_rows(cfg)
    if new_rows.empty:
        return {"appended": 0, "skipped": 0, "new_tickers": [], "dropped_tickers": [], "path": log_path(path)}

    existing_ids = set(existing["trade_id"].dropna().astype(str)) if not existing.empty else set()
    to_append = new_rows[~new_rows["trade_id"].isin(existing_ids)]
    skipped = len(new_rows) - len(to_append)

    combined = pd.concat([existing, to_append], ignore_index=True) if not to_append.empty else existing
    if not to_append.empty:
        save_log(combined, path)

    # Diff vs most recent log session (previous entry_date batch)
    new_tickers = to_append["ticker"].tolist()
    dropped: list[str] = []
    if not existing.empty:
        last_entry_date = existing["entry_date"].max()
        last_tickers = set(existing.loc[existing["entry_date"] == last_entry_date, "ticker"])
        today_tickers = set(new_rows["ticker"])
        dropped = sorted(last_tickers - today_tickers)

    return {
        "appended": len(to_append),
        "skipped": skipped,
        "new_tickers": new_tickers,
        "dropped_tickers": dropped,
        "path": log_path(path),
    }


def update_closed_trades(cfg: dict[str, Any], path: Path | None = None) -> int:
    df = load_log(path)
    if df.empty:
        return 0

    cost_pct = cfg.get("backtest", {}).get("round_trip_cost_pct", 0.1)
    today = date.today()
    closed_count = 0

    for i, row in df.iterrows():
        if str(row["status"]) != "open":
            continue
        exit_d = pd.Timestamp(row["exit_date"]).date()
        if today < exit_d:
            continue

        exit_price = _fetch_close_on_date(str(row["ticker"]), exit_d)
        if exit_price is None:
            continue

        entry = float(row["entry_price"])
        gross = (exit_price - entry) / entry * 100
        net = gross - cost_pct

        df.at[i, "status"] = "closed"
        df.at[i, "exit_price"] = round(exit_price, 4)
        df.at[i, "return_pct_gross"] = round(gross, 2)
        df.at[i, "return_pct_net"] = round(net, 2)
        df.at[i, "closed_date"] = exit_d.isoformat()
        closed_count += 1

    if closed_count:
        save_log(df, path)
    return closed_count


def open_positions_status(path: Path | None = None) -> pd.DataFrame:
    df = load_log(path)
    open_df = df[df["status"] == "open"].copy()
    if open_df.empty:
        return open_df

    rows = []
    for _, row in open_df.iterrows():
        ticker = str(row["ticker"])
        entry = float(row["entry_price"])
        try:
            hist = yf.Ticker(ticker).history(period="5d", auto_adjust=True)
            current = _safe_float(hist["Close"].iloc[-1]) if hist is not None and not hist.empty else None
        except Exception:
            current = None

        unrealized = ((current - entry) / entry * 100) if current else None
        stop = float(row["stop_loss"])
        hit_stop = current is not None and current <= stop

        rows.append(
            {
                "ticker": ticker,
                "entry_date": row["entry_date"],
                "exit_date": row["exit_date"],
                "entry_price": entry,
                "current_price": current,
                "unrealized_pct": round(unrealized, 2) if unrealized is not None else None,
                "stop_loss": stop,
                "below_stop": hit_stop,
                "days_left": (pd.Timestamp(row["exit_date"]).date() - date.today()).days,
            }
        )
    return pd.DataFrame(rows)


def performance_report(path: Path | None = None) -> dict[str, Any]:
    df = load_log(path)
    if df.empty:
        return {"total_trades": 0, "open": 0, "closed": 0}

    closed = df[df["status"] == "closed"].copy()
    open_count = int((df["status"] == "open").sum())

    result: dict[str, Any] = {
        "total_trades": len(df),
        "open": open_count,
        "closed": len(closed),
        "path": str(log_path(path)),
    }

    if closed.empty:
        result["message"] = "No closed trades yet — keep logging daily scans."
        return result

    returns = closed["return_pct_net"].astype(float)
    wins = returns > 0
    result.update(
        {
            "win_rate_pct": round(wins.mean() * 100, 1),
            "avg_return_net_pct": round(returns.mean(), 2),
            "total_return_net_pct": round(returns.sum(), 2),
            "best_trade": closed.loc[returns.idxmax(), "ticker"],
            "best_return_pct": round(returns.max(), 2),
            "worst_trade": closed.loc[returns.idxmin(), "ticker"],
            "worst_return_pct": round(returns.min(), 2),
        }
    )
    return result