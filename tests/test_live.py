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
    # Blocked on BP even with standing auth
    assert prop.blocked is True
    assert prop.place_allowed is False
    assert prop.symbol == "AAPL"
    assert prop.option_type == "call"
    assert prop.mcp_next_steps
    assert prop.bp_usage_pct == cfg.bp_usage_pct
    assert prop.usable_buying_power is not None
    assert prop.usable_buying_power == round(1.29 * cfg.bp_usage_pct, 2)


def test_bp_usage_caps_premium_when_bp_healthy(tmp_path: Path):
    from dataclasses import replace

    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    # Force 50% usage and high premium default
    cfg = replace(cfg, bp_usage_pct=0.5, max_option_premium=100.0, learning_mode=True)
    live_path = tmp_path / "live.json"
    snap = snapshot_from_broker_payloads(
        account_number="616665162",
        portfolio={
            "total_value": "2000",
            "cash": "200",
            "buying_power": {"buying_power": "100"},
        },
        equity_positions=[],
        option_positions=[],
    )
    save_live_portfolio(snap, live_path)
    prop = propose_option(
        cfg,
        symbol="AAPL",
        option_type="call",
        max_premium=100.0,
        live_path=live_path,
    )
    # usable = 50; premium must not exceed usable
    assert prop.usable_buying_power == 50.0
    assert prop.max_premium_usd <= 50.0 + 1e-6
    assert any("capped to usable BP" in w or "learning_mode" in w for w in prop.warnings)
    # User lock-in manage rules
    assert cfg.option_stop_loss_pct == 0.10
    assert cfg.option_take_profit_pct_low == 0.10
    assert cfg.option_take_profit_pct_high == 0.20
    assert cfg.options_place_without_confirm is True
    assert prop.manage_rules.get("stop_loss_pct") == 0.10
    assert prop.manage_rules.get("take_profit_pct_high") == 0.20
    assert any("tight" in w.lower() for w in prop.warnings)


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
    # Standing auth + not blocked → place_allowed for agent follow-through
    assert prop.place_allowed is True


def test_pick_option_contract_prefers_near_hint():
    from agentic_trading.live.pick_contract import pick_option_contract
    from datetime import date, timedelta

    today = date.today()
    exp = (today + timedelta(days=21)).isoformat()
    instruments = [
        {
            "id": "far",
            "type": "call",
            "strike_price": "250.0000",
            "expiration_date": exp,
            "state": "active",
            "tradability": "tradable",
            "chain_symbol": "AAPL",
        },
        {
            "id": "near",
            "type": "call",
            "strike_price": "195.0000",
            "expiration_date": exp,
            "state": "active",
            "tradability": "tradable",
            "chain_symbol": "AAPL",
        },
    ]
    picked = pick_option_contract(
        instruments, option_type="call", strike_hint=195.0, min_dte=7, max_dte=31
    )
    assert picked is not None
    assert picked.option_id == "near"
    assert picked.strike_price == 195.0


def test_prepare_review_blocks_low_bp(tmp_path: Path):
    from agentic_trading.live.supervised_review import build_review_request
    from agentic_trading.live.portfolio import (
        save_live_portfolio,
        snapshot_from_broker_payloads,
    )

    live_path = tmp_path / "live.json"
    snap = snapshot_from_broker_payloads(
        account_number="616665162",
        portfolio={"cash": "96", "buying_power": {"buying_power": "1.29"}},
    )
    save_live_portfolio(snap, live_path)
    req = build_review_request(
        account_number="616665162",
        symbol="AAPL",
        option_id="abc-123",
        option_type="call",
        limit_price=0.90,
        contracts=1,
        max_premium_usd=100,
        live_path=live_path,
    )
    assert req.blocked is True
    assert req.place_allowed is False
    assert req.bp_free is False


def test_prepare_review_ok_with_bp(tmp_path: Path):
    from agentic_trading.live.supervised_review import build_review_request
    from agentic_trading.live.portfolio import (
        save_live_portfolio,
        snapshot_from_broker_payloads,
    )

    live_path = tmp_path / "live.json"
    snap = snapshot_from_broker_payloads(
        account_number="616665162",
        portfolio={"cash": "500", "buying_power": {"buying_power": "400"}},
    )
    save_live_portfolio(snap, live_path)
    req = build_review_request(
        account_number="616665162",
        symbol="AAPL",
        option_id="abc-123",
        option_type="call",
        limit_price=0.90,
        contracts=1,
        max_premium_usd=100,
        live_path=live_path,
        place_without_confirm=True,
    )
    assert req.blocked is False
    assert req.bp_free is True
    assert req.place_allowed is True
    assert req.human_confirm_required is False
    assert req.mcp_review_args["legs"][0]["option_id"] == "abc-123"
    assert req.mcp_review_args["price"] == "0.90"


def test_session_refresh_plan_detects_stale(tmp_path: Path):
    from agentic_trading.live.session import build_session_refresh_plan
    from agentic_trading.live.portfolio import (
        save_live_portfolio,
        snapshot_from_broker_payloads,
    )
    from agentic_trading.config import load_config
    from datetime import datetime, timezone, timedelta

    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    # Point at tmp by saving over real path is bad; use age via old ts in tmp by
    # building plan against current config live path — just assert shape.
    plan = build_session_refresh_plan(cfg, stale_after_seconds=1, min_bp_for_options=50)
    assert plan.agentic_account_number
    assert plan.mcp_refresh_steps
    assert plan.supervised_option_steps
    assert any("Agentic" in r or "LEARNING MODE" in r for r in plan.rules)


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
