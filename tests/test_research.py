from pathlib import Path

from agentic_trading.agent.research import (
    apply_recommended_symbols,
    heuristic_research,
    run_research,
)
from agentic_trading.config import load_config


def test_heuristic_research_writes_files(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    report = run_research(cfg, use_llm=False, out_dir=tmp_path)
    assert report.mode == "heuristic"
    assert "SPY" in report.recommended_symbols
    assert (tmp_path / "research_latest.json").is_file()
    assert (tmp_path / "research_latest.md").is_file()
    assert report.picks


def test_apply_recommended_symbols(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "symbols:\n  - SPY\n  - AAPL\nselector:\n  max_new_entries_per_tick: 2\n"
    )
    apply_recommended_symbols(cfg_path, ["NVDA", "SPY", "MSFT"])
    text = cfg_path.read_text()
    assert "SPY" in text and "NVDA" in text and "MSFT" in text
    # SPY first
    assert text.index("SPY") < text.index("NVDA")


def test_heuristic_direct():
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    r = heuristic_research(cfg)
    assert r.mode == "heuristic"
    assert r.recommended_symbols
