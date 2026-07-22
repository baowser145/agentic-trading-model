from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_trading.live.portfolio import LivePortfolioSnapshot, load_live_portfolio


@dataclass
class OptionReviewRequest:
    """Payload for robinhood__review_option_order — never place from this alone."""

    ts: str
    account_number: str
    symbol: str
    option_id: str
    option_type: str
    contracts: int
    limit_price: str
    max_premium_usd: float
    estimated_debit_usd: float
    buying_power: float | None
    bp_free: bool
    blocked: bool
    block_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mcp_review_args: dict[str, Any] = field(default_factory=dict)
    place_allowed: bool = False
    human_confirm_required: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def bp_is_free(
    live: LivePortfolioSnapshot | None,
    *,
    estimated_debit_usd: float,
    min_bp_buffer: float = 5.0,
    bp_usage_pct: float = 1.0,
) -> tuple[bool, list[str]]:
    """True when usable buying power (BP × bp_usage_pct) can cover the debit."""
    reasons: list[str] = []
    if live is None:
        reasons.append("No live portfolio snapshot — refresh session first.")
        return False, reasons
    bp = float(live.buying_power or 0)
    usage = max(0.05, min(1.0, float(bp_usage_pct or 1.0)))
    usable = bp * usage
    need = float(estimated_debit_usd) + min_bp_buffer
    if usable < need:
        reasons.append(
            f"Usable buying power ${usable:.2f} ({usage:.0%} of broker BP ${bp:.2f}) "
            f"< required ~${need:.2f} "
            f"(debit ${estimated_debit_usd:.2f} + buffer ${min_bp_buffer:.2f})."
        )
        return False, reasons
    if usable < 25:
        reasons.append(
            f"Usable BP still thin (${usable:.2f} = {usage:.0%} of ${bp:.2f}); review may warn."
        )
    return True, reasons


def build_review_request(
    *,
    account_number: str,
    symbol: str,
    option_id: str,
    option_type: str,
    limit_price: float,
    contracts: int = 1,
    max_premium_usd: float = 100.0,
    live: LivePortfolioSnapshot | None = None,
    live_path: Path | None = None,
    place_without_confirm: bool = False,
    bp_usage_pct: float = 1.0,
) -> OptionReviewRequest:
    if live is None and live_path is not None:
        live = load_live_portfolio(live_path)

    contracts = max(1, int(contracts))
    limit_price = float(limit_price)
    if limit_price <= 0:
        raise ValueError("limit_price must be positive (debit per contract in dollars)")

    estimated = limit_price * 100.0 * contracts
    free, free_notes = bp_is_free(
        live, estimated_debit_usd=estimated, bp_usage_pct=bp_usage_pct
    )
    block: list[str] = []
    warnings: list[str] = []

    if estimated > max_premium_usd + 1e-6:
        block.append(
            f"Estimated debit ${estimated:.2f} exceeds max premium ${max_premium_usd:.2f}."
        )
    if not free:
        block.extend(free_notes)
    else:
        warnings.extend([n for n in free_notes if n])

    if not account_number:
        block.append("Missing agentic account_number.")
    if not option_id or option_id.startswith("<"):
        block.append("Invalid option_id — pick a real instrument UUID first.")

    mcp_args = {
        "account_number": account_number,
        "quantity": str(contracts),
        "type": "limit",
        "price": f"{limit_price:.2f}",
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
        "chain_symbol": symbol.upper(),
        "underlying_type": "equity",
        "legs": [
            {
                "option_id": option_id,
                "side": "buy",
                "position_effect": "open",
            }
        ],
    }

    blocked = bool(block)
    # Standing auth: place after clean review when user enabled place_without_confirm
    can_place = (not blocked) and free and bool(place_without_confirm)
    if place_without_confirm and not blocked:
        note = (
            "Call robinhood__review_option_order with mcp_review_args if not blocked. "
            "Standing user auth: place_option_order allowed after clean review "
            "(Agentic only; BP free; max 1 open; day halt still apply)."
        )
    else:
        note = (
            "Call robinhood__review_option_order with mcp_review_args only if not blocked. "
            "place_option_order requires explicit user confirm (or enable "
            "live.options_place_without_confirm)."
        )

    return OptionReviewRequest(
        ts=datetime.now(timezone.utc).isoformat(),
        account_number=account_number,
        symbol=symbol.upper(),
        option_id=option_id,
        option_type=option_type.lower(),
        contracts=contracts,
        limit_price=f"{limit_price:.2f}",
        max_premium_usd=float(max_premium_usd),
        estimated_debit_usd=round(estimated, 2),
        buying_power=live.buying_power if live else None,
        bp_free=free and not blocked,
        blocked=blocked,
        block_reasons=block,
        warnings=warnings,
        mcp_review_args=mcp_args,
        place_allowed=can_place,
        human_confirm_required=not can_place,
        note=note,
    )


def save_review_request(req: OptionReviewRequest, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(req.to_dict(), indent=2) + "\n")
    return path


def record_review_result(
    review_response: dict[str, Any],
    *,
    request: dict[str, Any] | None = None,
    path: Path,
    place_without_confirm: bool = False,
) -> Path:
    """Persist MCP review_option_order response for audit / place gate."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    req_blocked = bool((request or {}).get("blocked"))
    req_place = bool((request or {}).get("place_allowed"))
    # If review has any broker alert, that's live evidence from Robinhood itself —
    # it hard-blocks place regardless of standing auth (place_without_confirm can't override it).
    alerts = []
    if isinstance(review_response, dict):
        for k in ("alerts", "warnings", "blocking_alerts"):
            v = review_response.get(k)
            if isinstance(v, list):
                alerts.extend(v)
    can_place = (not req_blocked) and (req_place or place_without_confirm) and not alerts
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "request": request,
        "review_response": review_response,
        "place_allowed": can_place,
        "human_confirm_required": not can_place,
        "note": (
            "Standing auth: agent may place_option_order on Agentic after clean review."
            if can_place
            else (
                "Broker review returned alerts — place blocked, human review required "
                "regardless of standing auth."
                if alerts and not req_blocked
                else "Review recorded. Place only after explicit human yes or enable standing auth."
            )
        ),
        "alerts_seen": len(alerts),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path
