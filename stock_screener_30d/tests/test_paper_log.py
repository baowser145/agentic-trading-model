from pathlib import Path

import pandas as pd

from stock_screener_30d.paper_log import (
    load_log,
    performance_report,
    save_log,
    _trade_id,
)


def test_trade_id():
    assert _trade_id("CAT", "2026-07-01") == "CAT_2026-07-01"


def test_append_and_dedupe(tmp_path: Path):
    log_file = tmp_path / "paper-trades.csv"
    row = {
        "trade_id": "CAT_2026-07-01",
        "logged_at": "2026-07-01T10:00:00",
        "ticker": "CAT",
        "entry_date": "2026-07-01",
        "entry_price": 963.53,
        "exit_date": "2026-08-15",
        "stop_loss": 900.0,
        "hold_days": 30,
        "score": 0.96,
        "rsi": 50.9,
        "pullback_pct": 9.5,
        "status": "open",
        "exit_price": pd.NA,
        "return_pct_gross": pd.NA,
        "return_pct_net": pd.NA,
        "closed_date": pd.NA,
    }
    save_log(pd.DataFrame([row]), log_file)
    loaded = load_log(log_file)
    assert len(loaded) == 1
    assert loaded.iloc[0]["ticker"] == "CAT"


def test_performance_report_empty(tmp_path: Path):
    r = performance_report(tmp_path / "missing.csv")
    assert r["total_trades"] == 0


def test_performance_report_closed(tmp_path: Path):
    log_file = tmp_path / "paper-trades.csv"
    rows = [
        {
            "trade_id": "CAT_2026-07-01",
            "logged_at": "2026-07-01",
            "ticker": "CAT",
            "entry_date": "2026-07-01",
            "entry_price": 100.0,
            "exit_date": "2026-08-01",
            "stop_loss": 90.0,
            "hold_days": 30,
            "score": 0.9,
            "rsi": 50.0,
            "pullback_pct": 10.0,
            "status": "closed",
            "exit_price": 110.0,
            "return_pct_gross": 10.0,
            "return_pct_net": 9.9,
            "closed_date": "2026-08-01",
        },
        {
            "trade_id": "AMD_2026-07-01",
            "logged_at": "2026-07-01",
            "ticker": "AMD",
            "entry_date": "2026-07-01",
            "entry_price": 100.0,
            "exit_date": "2026-08-01",
            "stop_loss": 90.0,
            "hold_days": 30,
            "score": 0.8,
            "rsi": 50.0,
            "pullback_pct": 10.0,
            "status": "closed",
            "exit_price": 95.0,
            "return_pct_gross": -5.0,
            "return_pct_net": -5.1,
            "closed_date": "2026-08-01",
        },
    ]
    save_log(pd.DataFrame(rows), log_file)
    r = performance_report(log_file)
    assert r["closed"] == 2
    assert r["win_rate_pct"] == 50.0
    assert r["avg_return_net_pct"] == 2.4