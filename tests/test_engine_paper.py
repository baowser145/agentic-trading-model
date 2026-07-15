from pathlib import Path

from agentic_trading.broker.paper import PaperBroker
from agentic_trading.config import load_config
from agentic_trading.engine import Engine
from agentic_trading.log import DecisionLogger
from agentic_trading.market.quotes import FixtureQuoteProvider


def test_engine_run_once_logs(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    # Force paper path
    log_path = tmp_path / "decisions.jsonl"
    series = {
        # Strong uptrend so ENTER_LONG fires after enough bars
        "SPY": [100 + i for i in range(30)],
        "QQQ": [200 + i for i in range(30)],
        "IWM": [50 + i * 0.5 for i in range(30)],
    }
    quotes = FixtureQuoteProvider(series=series, start_step=25)
    engine = Engine(
        config=cfg,
        broker=PaperBroker(cfg.starting_equity),
        quotes=quotes,
        logger=DecisionLogger(log_path),
    )
    result = engine.run_once()
    assert result.portfolio.equity > 0
    assert log_path.is_file()
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert "signals" in lines[0]


def test_config_forces_paper_without_allow_live(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    # Write temp config with live but allow_live false
    src = (root / "config.yaml").read_text()
    p = tmp_path / "config.yaml"
    p.write_text(
        src.replace("trading_mode: paper", "trading_mode: live")
    )
    cfg = load_config(p)
    assert cfg.trading_mode.value == "paper"
