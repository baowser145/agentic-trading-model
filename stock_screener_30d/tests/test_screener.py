from stock_screener_30d.screener import passes_criteria, composite_score


def test_passes_criteria_valid():
    metrics = {
        "above_sma": True,
        "rsi": 50,
        "pullback_pct": 10,
        "avg_volume": 1_000_000,
    }
    screening = {
        "rsi_min": 40,
        "rsi_max": 60,
        "pullback_min_pct": 5,
        "pullback_max_pct": 15,
        "min_avg_volume": 500_000,
    }
    assert passes_criteria(metrics, screening)


def test_passes_criteria_fails_rsi():
    metrics = {
        "above_sma": True,
        "rsi": 70,
        "pullback_pct": 10,
        "avg_volume": 1_000_000,
    }
    screening = {
        "rsi_min": 40,
        "rsi_max": 60,
        "pullback_min_pct": 5,
        "pullback_max_pct": 15,
        "min_avg_volume": 500_000,
    }
    assert not passes_criteria(metrics, screening)


def test_composite_score_higher_for_mid_rsi():
    screening = {
        "rsi_min": 40,
        "rsi_max": 60,
        "pullback_min_pct": 5,
        "pullback_max_pct": 15,
        "min_avg_volume": 500_000,
    }
    mid = {"rsi": 50, "pullback_pct": 10, "avg_volume": 1_000_000}
    edge = {"rsi": 40, "pullback_pct": 5, "avg_volume": 500_000}
    assert composite_score(mid, screening) > composite_score(edge, screening)