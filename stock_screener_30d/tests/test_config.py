from stock_screener_30d.config import load_config, get_universe, SP500_SAMPLE
import pytest


def test_load_config():
    cfg = load_config()
    assert "screening" in cfg
    assert cfg["screening"]["rsi_min"] == 40
    assert cfg["backtest"]["hold_days"] == 30


def test_get_universe():
    cfg = load_config()
    tickers = get_universe(cfg)
    assert len(tickers) == len(SP500_SAMPLE)
    assert "AAPL" in tickers
    assert "SQ" not in tickers


def test_invalid_config_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("screening:\n  rsi_min: 60\n  rsi_max: 40\noutput: {}\nbacktest: {}")
    with pytest.raises(ValueError, match="rsi_min"):
        load_config(bad)