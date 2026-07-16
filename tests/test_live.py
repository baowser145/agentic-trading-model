import json
from pathlib import Path

from agentic_trading.config import load_config
from agentic_trading.live.portfolio import (
    load_live_portfolio,
    save_live_portfolio,
    snapshot_from_broker_payloads,
)
from agentic_trading.live.propose_option import propose_option
from agentic_trading.__main__ import main


def test_snapshot_from_broker_payloads_and_roundtrip(tmp_path: Path):
    snap = snapshot_from_broker_payloads(
        account_number="616665162",
        portfolio={
            "total_value": "1149.37",
            "equity_value": "1053.14",
            "options_value": "0",
            "cash": "96.23",
            "buying_power": {"buying_power": "1.29", "display_currency": "USD"},
            "currency": "USD",
        },
        equity_positions=[
            {
                "symbol": "PEGA",
                "quantity": "18",
                "average_buy_price": "31.59",
                "shares_available_for_sells": "0",
                "shares_held_for_sells": "18",
            }
        ],
        option_positions=[],
    )
    assert snap.buying_power == 1.29
    assert snap.cash == 96.23
    assert len(snap.equity_positions) == 1
    assert snap.equity_positions[0].symbol == "PEGA"
    assert any("held for open sells" in n for n in snap.notes)
    assert any("Buying power is low" in n for n in snap.notes)

    path = tmp_path / "live_portfolio.json"
    save_live_portfolio(snap, path)
    loaded = load_live_portfolio(path)
    assert loaded is not None
    assert loaded.account_number == "616665162"
    assert loaded.buying_power == 1.29


def test_propose_option_blocks_low_bp(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    live_path = tmp_path / "live.json"
    snap = snapshot_from_broker_payloads(
        account_number="616665162",
        portfolio={
            "total_value": "1000",
            "cash": "50",
            "buying_power": {"buying_power": "1.29"},
        },
        equity_positions=[],
        option_positions=[],
    )
    save_live_portfolio(snap, live_path)

    # Patch config paths via propose live_path arg
    prop = propose_option(
        cfg,
        symbol="AAPL",
        option_type="call",
        max_premium=100.0,
        live_path=live_path,
    )
    assert prop.place_allowed is False
    assert prop.symbol == "AAPL"
    assert prop.option_type == "call"
    assert prop.blocked is True
    assert prop.mcp_next_steps
    assert prop.mcp_next_steps[-1].get("blocked_by_cli") is True


def test_propose_option_unblocked_with_bp(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    live_path = tmp_path / "live.json"
    snap = snapshot_from_broker_payloads(
        account_number="616665162",
        portfolio={
            "total_value": "2000",
            "cash": "500",
            "buying_power": {"buying_power": "400"},
        },
    )
    save_live_portfolio(snap, live_path)
    prop = propose_option(
        cfg, symbol="MSFT", option_type="put", max_premium=80.0, live_path=live_path
    )
    assert prop.blocked is False
    assert prop.symbol == "MSFT"
    assert prop.option_type == "put"
    assert prop.max_premium_usd == 80.0


def test_cli_write_live_snapshot_and_status(tmp_path: Path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    cfg_src = (root / "config.yaml").read_text()
    cfg_path = tmp_path / "config.yaml"
    # Point live paths into tmp
    cfg_path.write_text(
        cfg_src
        + "\n"
        # config already has live: block; rewrite file with tmp paths via yaml-ish override
    )
    # Simpler: write payload and use --account with default config paths under root logs
    payload = {
        "account_number": "616665162",
        "portfolio": {
            "total_value": "1149.37",
            "equity_value": "1053.14",
            "options_value": "0",
            "cash": "96.23",
            "buying_power": {"buying_power": "1.29"},
            "currency": "USD",
        },
        "equity_positions": [
            {
                "symbol": "AA",
                "quantity": "2",
                "average_buy_price": "49.83",
                "shares_held_for_sells": "2",
                "shares_available_for_sells": "0",
            }
        ],
        "option_positions": [],
    }
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload))
    # Use project config so agentic account exists
    rc = main(
        [
            "-c",
            str(root / "config.yaml"),
            "write-live-snapshot",
            "--file",
            str(payload_path),
        ]
    )
    assert rc == 0
    live_path = root / "logs" / "live_portfolio.json"
    assert live_path.is_file()
    data = json.loads(live_path.read_text())
    assert data["buying_power"] == 1.29
    assert data["equity_positions"][0]["symbol"] == "AA"
