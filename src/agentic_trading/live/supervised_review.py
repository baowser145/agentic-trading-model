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
) -> tuple[bool, list[str]]:
    """True when buying power can plausibly cover the debit."""
    reasons: list[str] = []
    if live is None:
        reasons.append("No live portfolio snapshot — refresh session first.")
        return False, reasons
    bp = float(live.buying_power or 0)
    need = float(estimated_debit_usd) + min_bp_buffer
    if bp < need:
        reasons.append(
            f"Buying power ${bp:.2f} < required ~${need:.2f} "
            f"(debit ${estimated_debit_usd:.2f} + buffer ${min_bp_buffer:.2f})."
        )
        return False, reasons
    if bp < 25:
        reasons.append(f"Buying power still thin (${bp:.2f}); review may warn.")
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
) -> OptionReviewRequest:
    if live is None and live_path is not None:
        live = load_live_portfolio(live_path)

    contracts = max(1, int(contracts))
    limit_price = float(limit_price)
    if limit_price <= 0:
        raise ValueError("limit_price must be positive (debit per contract in dollars)")

    estimated = limit_price * 100.0 * contracts
    free, free_notes = bp_is_free(live, estimated_debit_usd=estimated)
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
        bp_free=free and not block,
        blocked=bool(block),
        block_reasons=block,
        warnings=warnings,
        mcp_review_args=mcp_args,
        place_allowed=False,
        human_confirm_required=True,
        note=(
            "Call robinhood__review_option_order with mcp_review_args only if not blocked. "
            "NEVER call place_option_order unless the user explicitly confirms the review."
        ),
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
) -> Path:
    """Persist MCP review_option_order response for audit / next human confirm."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "request": request,
        "review_response": review_response,
        "place_allowed": False,
        "note": "Review recorded. Place only after explicit human yes.",
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path
