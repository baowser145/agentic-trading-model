from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_trading.config import AppConfig
from agentic_trading.live.portfolio import load_live_portfolio


DEFAULT_STALE_SECONDS = 15 * 60  # 15 minutes


@dataclass
class SessionRefreshPlan:
    """What Grok must do at session start for Agentic live state."""

    agentic_account_number: str
    portfolio_path: str
    stale_after_seconds: int
    snapshot_loaded: bool
    snapshot_ts: str | None
    snapshot_age_seconds: float | None
    needs_refresh: bool
    buying_power: float | None
    usable_buying_power: float | None
    bp_usage_pct: float
    bp_free_for_options: bool
    min_bp_for_options: float
    learning_mode: bool = False
    mcp_refresh_steps: list[dict[str, Any]] = field(default_factory=list)
    after_refresh_cli: list[str] = field(default_factory=list)
    supervised_option_steps: list[dict[str, Any]] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _age_seconds(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    except ValueError:
        return None


def build_session_refresh_plan(
    config: AppConfig,
    *,
    stale_after_seconds: int = DEFAULT_STALE_SECONDS,
    min_bp_for_options: float = 50.0,
) -> SessionRefreshPlan:
    acct = config.agentic_account_number or ""
    path = config.live_portfolio_path
    snap = load_live_portfolio(path) if path else None
    age = _age_seconds(snap.ts if snap else None)
    needs = snap is None or age is None or age > stale_after_seconds
    bp = snap.buying_power if snap else None
    bp_usage = float(getattr(config, "bp_usage_pct", 1.0) or 1.0)
    bp_usage = max(0.05, min(1.0, bp_usage))
    usable = (float(bp) * bp_usage) if bp is not None else None
    # Free for options = usable (capped) BP meets min, not full broker BP
    bp_free = usable is not None and usable >= min_bp_for_options
    learning = bool(getattr(config, "learning_mode", False))

    steps = [
        {
            "step": 1,
            "tool": "robinhood__get_accounts",
            "purpose": "Find agentic_allowed=true account (nickname Agentic)",
        },
        {
            "step": 2,
            "tool": "robinhood__get_portfolio",
            "args": {"account_number": acct},
            "purpose": "Cash, buying_power, total_value",
        },
        {
            "step": 3,
            "tool": "robinhood__get_equity_positions",
            "args": {"account_number": acct},
            "purpose": "Open stock positions",
        },
        {
            "step": 4,
            "tool": "robinhood__get_option_positions",
            "args": {"account_number": acct, "nonzero": True},
            "purpose": "Open options (if any)",
        },
        {
            "step": 5,
            "cli": (
                "python -m agentic_trading write-live-snapshot --file <payload.json>"
            ),
            "purpose": "Persist logs/live_portfolio.json",
            "payload_shape": {
                "account_number": acct,
                "account_nickname": "Agentic",
                "agentic_allowed": True,
                "portfolio": "<get_portfolio data>",
                "equity_positions": "<get_equity_positions.positions>",
                "option_positions": "<get_option_positions.positions>",
            },
        },
        {
            "step": 6,
            "cli": "python -m agentic_trading status --live-only",
            "purpose": "Verify snapshot",
        },
    ]

    supervised = [
        {
            "step": 1,
            "when": (
                f"usable_buying_power >= {min_bp_for_options} "
                f"(broker_bp × {bp_usage:.0%})"
            ),
            "cli": "python -m agentic_trading propose-option --type call",
        },
        {
            "step": 2,
            "tool": "robinhood__get_option_chains",
            "args": {"underlying_symbol": "<proposal.symbol>"},
        },
        {
            "step": 3,
            "tool": "robinhood__get_option_instruments",
            "args": {
                "chain_symbol": "<proposal.symbol>",
                "type": "<call|put>",
                "expiration_dates": "<comma YYYY-MM-DD from proposal window>",
                "state": "active",
                "tradability": "tradable",
            },
        },
        {
            "step": 4,
            "cli": (
                "python -m agentic_trading pick-option-contract "
                "--file <instruments.json> --type call --strike-hint <n>"
            ),
        },
        {
            "step": 5,
            "tool": "robinhood__get_option_quotes",
            "args": {"instrument_ids": ["<picked.option_id>"]},
            "purpose": "Set limit near ask for debit",
        },
        {
            "step": 6,
            "cli": (
                "python -m agentic_trading prepare-option-review "
                "--option-id <id> --price <limit> --symbol <SYM>"
            ),
            "purpose": "Gate on BP; emit mcp_review_args if free",
        },
        {
            "step": 7,
            "tool": "robinhood__review_option_order",
            "purpose": "Only if prepare-option-review not blocked",
        },
        {
            "step": 8,
            "cli": (
                "python -m agentic_trading record-option-review --file <review.json>"
            ),
        },
        {
            "step": 9,
            "action": (
                "If standing auth (options_place_without_confirm): place after clean review; "
                "else present review and wait for explicit yes"
            ),
        },
    ]

    place_wo = bool(getattr(config, "options_place_without_confirm", False))
    rules = [
        "Only Agentic account (agentic_allowed=true) for any live tool.",
        "Auto-refresh live snapshot when missing or older than stale_after_seconds.",
        "propose-option / pick / review never place by themselves.",
        (
            f"Standing auth ON: place_option_order after clean review without per-trade yes "
            f"(still Agentic + usable BP ({bp_usage:.0%} of broker) + "
            f"max_open_options={config.max_open_options} + day halt — "
            f"all now hard-blocking, not just warnings)."
            if place_wo
            else "place_option_order requires explicit user confirmation after review."
        ),
        f"BP budget: usable = broker buying_power × {bp_usage:.0%} "
        f"(learning_mode={learning}).",
        "If usable BP not free, stop after snapshot + status; do not thrash review/place.",
        "Paper mode remains default for stock loop; options live only via Agentic MCP path.",
    ]
    if learning:
        rules.insert(
            0,
            "LEARNING MODE: paper stocks first; live options only after free usable BP.",
        )

    return SessionRefreshPlan(
        agentic_account_number=acct,
        portfolio_path=str(path) if path else "",
        stale_after_seconds=stale_after_seconds,
        snapshot_loaded=snap is not None,
        snapshot_ts=snap.ts if snap else None,
        snapshot_age_seconds=age,
        needs_refresh=needs,
        buying_power=bp,
        usable_buying_power=round(usable, 2) if usable is not None else None,
        bp_usage_pct=bp_usage,
        bp_free_for_options=bp_free,
        min_bp_for_options=min_bp_for_options,
        learning_mode=learning,
        mcp_refresh_steps=steps,
        after_refresh_cli=[
            "python -m agentic_trading status --live-only",
            "python -m agentic_trading propose-option --type call",
        ],
        supervised_option_steps=supervised,
        rules=rules,
    )


def save_session_plan(plan: SessionRefreshPlan, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan.to_dict(), indent=2) + "\n")
    return path
