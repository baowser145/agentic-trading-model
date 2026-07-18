import pandas as pd

from stock_screener_30d.targets import enrich_with_targets, trading_days_ahead


def test_trading_days_ahead():
    start = pd.Timestamp("2026-07-02")
    exit_d = trading_days_ahead(start, 30)
    assert exit_d > start
    assert (exit_d - start).days >= 30


def test_enrich_with_targets():
    df = pd.DataFrame(
        [
            {
                "ticker": "CAT",
                "price": 963.53,
                "scan_date": pd.Timestamp("2026-07-01"),
                "sma_50": 900.0,
                "high_52w": 1000.0,
                "rsi": 50.9,
                "pullback_pct": 9.5,
                "score": 0.962,
            }
        ]
    )
    out = enrich_with_targets(df, hold_days=30)
    assert out.iloc[0]["entry_price"] == 963.53
    assert out.iloc[0]["stop_loss"] == 900.0
    assert out.iloc[0]["exit_target_price"] == 1000.0
    assert out.iloc[0]["exit_stop_price"] == 900.0
    assert out.iloc[0]["entry_date"] == "2026-07-01"
    assert out.iloc[0]["exit_date"] != out.iloc[0]["entry_date"]
    assert out.iloc[0]["risk_pct"] > 0