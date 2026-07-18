from stock_screener_30d.screener import run_scan


def test_empty_ticker_list_returns_empty():
    from stock_screener_30d.config import load_config
    cfg = load_config()
    df = run_scan(cfg, tickers=[])
    assert df.empty


def test_composite_score_zero_width_range():
    from stock_screener_30d.screener import composite_score
    screening = {
        "rsi_min": 50,
        "rsi_max": 50,
        "pullback_min_pct": 10,
        "pullback_max_pct": 10,
        "min_avg_volume": 500_000,
    }
    metrics = {"rsi": 50, "pullback_pct": 10, "avg_volume": 1_000_000}
    assert composite_score(metrics, screening) == 0.0