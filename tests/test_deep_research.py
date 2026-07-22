from pathlib import Path

from agentic_trading.agent.deep_research import (
    heuristic_deep_research,
    resolve_peers,
    run_deep_research,
    write_deep_research_memo,
)
from agentic_trading.config import load_config
from agentic_trading.market.quotes import FixtureQuoteProvider


def test_resolve_peers_default_and_override():
    assert "MSFT" in resolve_peers("AAPL")
    assert resolve_peers("AAPL", ["MSFT", "GOOGL", "AAPL"]) == ["MSFT", "GOOGL"]
    # unknown ticker falls back to index peers
    peers = resolve_peers("ZZZZ")
    assert peers
    assert "ZZZZ" not in peers


def test_heuristic_deep_research_writes_memo(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    memo = run_deep_research(
        cfg,
        "AAPL",
        peers=["MSFT", "GOOGL"],
        use_llm=False,
        out_dir=tmp_path,
        quote_provider=FixtureQuoteProvider(),
    )
    assert memo.ticker == "AAPL"
    assert memo.mode == "heuristic"
    assert memo.verdict in ("pass", "caution", "fail")
    assert memo.peers == ["MSFT", "GOOGL"]
    assert memo.deep_dive.get("business_model")
    assert len(memo.bear_case.get("red_flags") or []) >= 1
    assert memo.bull_case.get("summary")
    assert memo.trade_plan.get("decision") in ("go", "wait", "pass")
    assert (tmp_path / "AAPL_latest.md").is_file()
    assert (tmp_path / "AAPL_latest.json").is_file()
    assert (tmp_path / "index.json").is_file()
    md = (tmp_path / "AAPL_latest.md").read_text()
    assert "Deep Dive" in md
    assert "Peer Comparison" in md
    assert "Bear Case" in md
    assert "Bull Case" in md
    assert "Trade Plan" in md


def test_invalid_ticker_raises():
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    try:
        run_deep_research(cfg, "not a ticker!!!", use_llm=False, out_dir=Path("/tmp"))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_write_memo_paths_populated(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    memo = heuristic_deep_research(
        "PLTR",
        ["SNOW", "DDOG"],
        cfg,
        quote_provider=FixtureQuoteProvider(),
    )
    written = write_deep_research_memo(memo, tmp_path)
    assert written.paths.get("md")
    assert written.paths.get("json")
    assert "PLTR" in written.paths["md"]
