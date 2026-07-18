from stock_screener_30d.comparison import _verdict, paper_vs_backtest


def test_verdict_too_early():
    v = _verdict(None, 0, 1.5, 2.5, None)
    assert v["status"] == "collecting"
    assert "Collecting" in v["message"]


def test_verdict_on_track():
    v = _verdict(2.4, 5, None, 2.5, 1.0)
    assert v["label"] == "ON TRACK"


def test_verdict_off_track():
    v = _verdict(0.5, 5, None, 2.5, 0.2)
    assert v["label"] == "OFF TRACK"


def test_paper_vs_backtest_structure():
    r = paper_vs_backtest()
    assert "paper" in r
    assert "backtest" in r
    assert "verdict" in r
    assert r["paper"]["label"] == "Paper Trading (Live)"
    assert r["backtest"]["label"] == "Backtest (Simulated)"