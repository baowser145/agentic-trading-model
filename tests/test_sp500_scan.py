from pathlib import Path

from agentic_trading.agent.sp500_scan import rank_top, run_sp500_scan, ScanRow
from agentic_trading.agent.sp500_universe import _dedupe, load_sp500_universe
from agentic_trading.config import load_config
from agentic_trading.market.quotes import FixtureQuoteProvider


def test_dedupe_and_index_skip():
    out = _dedupe(["aapl", "AAPL", "SPY", "googl", "GOOG", "brk.b"])
    assert out.count("AAPL") == 1
    assert "SPY" not in out
    assert "GOOGL" in out
    assert "BRK-B" in out


def test_load_fallback_universe(tmp_path: Path):
    syms, src = load_sp500_universe(
        cache_path=tmp_path / "u.json",
        prefer_cache=False,
        allow_remote=False,
    )
    assert src == "fallback"
    assert len(syms) >= 50
    assert "AAPL" in syms


def test_rank_top_call_vs_put():
    rows = [
        ScanRow("AAA", 10, 5.0, 1.0, 1e8, 1e6, liquid=True, score=5),
        ScanRow("BBB", 10, -3.0, -1.0, 1e8, 1e6, liquid=True, score=-3),
        ScanRow("CCC", 10, 1.0, 0.0, 1e8, 1e6, liquid=True, score=1),
        ScanRow("DDD", 10, 2.0, 0.0, 1e3, 10, liquid=False, score=2),
    ]
    call = rank_top(rows, bias="call", top_n=2)
    assert [r.symbol for r in call] == ["AAA", "CCC"]
    put = rank_top(rows, bias="put", top_n=2)
    assert [r.symbol for r in put] == ["BBB", "CCC"]


def test_sp500_scan_fixture_no_deep(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    # Small override universe for speed
    uni = ["AAPL", "MSFT", "NVDA", "AMD", "TSLA", "JPM", "XOM", "META"]
    result = run_sp500_scan(
        cfg,
        top_n=3,
        deep_research=False,
        quote_source="fixture",
        quote_provider=FixtureQuoteProvider(),
        out_dir=tmp_path / "scan",
        allow_remote_universe=False,
        universe_override=uni,
    )
    assert result.universe_source == "override"
    assert result.scored_count >= 1
    assert len(result.top) <= 3
    assert (tmp_path / "scan" / "latest.json").is_file()
    assert (tmp_path / "scan" / "shortlist.json").is_file()
    assert not result.deep_memos


def test_sp500_scan_fixture_with_heuristic_deep(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    uni = ["AAPL", "MSFT", "NVDA", "AMD", "TSLA"]
    result = run_sp500_scan(
        cfg,
        top_n=3,
        deep_n=2,
        deep_research=True,
        use_llm=False,
        quote_source="fixture",
        quote_provider=FixtureQuoteProvider(),
        out_dir=tmp_path / "scan",
        deep_out_dir=tmp_path / "deep",
        allow_remote_universe=False,
        universe_override=uni,
    )
    assert len(result.deep_memos) == 2
    assert all(m.get("ticker") for m in result.deep_memos)
    # heuristic never auto-fail hard enough to empty survivors always, but may be caution
    assert result.survivors or result.deep_memos
    assert (tmp_path / "deep").is_dir()
