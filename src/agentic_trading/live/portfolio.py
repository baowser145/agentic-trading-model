from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class LiveEquityPosition:
    symbol: str
    quantity: float
    average_buy_price: float | None = None
    shares_available_for_sells: float | None = None
    shares_held_for_sells: float | None = None
    type: str = "long"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LiveOptionPosition:
    chain_symbol: str
    quantity: float
    average_price: float | None = None
    option_id: str | None = None
    type: str | None = None  # long/short
    expiration_date: str | None = None
    strike_price: str | None = None
    option_type: str | None = None  # call/put
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class LivePortfolioSnapshot:
    """Broker-truth snapshot for Agentic account (written via MCP or --write-snapshot)."""

    account_number: str
    account_nickname: str = "Agentic"
    agentic_allowed: bool = True
    ts: str = ""
    currency: str = "USD"
    total_value: float = 0.0
    equity_value: float = 0.0
    options_value: float = 0.0
    cash: float = 0.0
    buying_power: float = 0.0
    pending_deposits: float = 0.0
    equity_positions: list[LiveEquityPosition] = field(default_factory=list)
    option_positions: list[LiveOptionPosition] = field(default_factory=list)
    source: str = "mcp"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_number": self.account_number,
            "account_nickname": self.account_nickname,
            "agentic_allowed": self.agentic_allowed,
            "ts": self.ts,
            "currency": self.currency,
            "total_value": self.total_value,
            "equity_value": self.equity_value,
            "options_value": self.options_value,
            "cash": self.cash,
            "buying_power": self.buying_power,
            "pending_deposits": self.pending_deposits,
            "equity_positions": [p.to_dict() for p in self.equity_positions],
            "option_positions": [p.to_dict() for p in self.option_positions],
            "source": self.source,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LivePortfolioSnapshot:
        eqs = [
            LiveEquityPosition(
                symbol=str(p.get("symbol", "")).upper(),
                quantity=float(p.get("quantity") or 0),
                average_buy_price=(
                    float(p["average_buy_price"])
                    if p.get("average_buy_price") not in (None, "")
                    else None
                ),
                shares_available_for_sells=(
                    float(p["shares_available_for_sells"])
                    if p.get("shares_available_for_sells") not in (None, "")
                    else None
                ),
                shares_held_for_sells=(
                    float(p["shares_held_for_sells"])
                    if p.get("shares_held_for_sells") not in (None, "")
                    else None
                ),
                type=str(p.get("type") or "long"),
            )
            for p in (data.get("equity_positions") or [])
        ]
        opts = []
        for p in data.get("option_positions") or []:
            opts.append(
                LiveOptionPosition(
                    chain_symbol=str(p.get("chain_symbol") or p.get("symbol") or "").upper(),
                    quantity=float(p.get("quantity") or 0),
                    average_price=(
                        float(p["average_price"])
                        if p.get("average_price") not in (None, "")
                        else None
                    ),
                    option_id=p.get("option_id"),
                    type=p.get("type"),
                    expiration_date=p.get("expiration_date"),
                    strike_price=str(p["strike_price"]) if p.get("strike_price") is not None else None,
                    option_type=p.get("option_type") or p.get("type"),
                    raw=dict(p.get("raw") or {}),
                )
            )
        return cls(
            account_number=str(data.get("account_number") or ""),
            account_nickname=str(data.get("account_nickname") or "Agentic"),
            agentic_allowed=bool(data.get("agentic_allowed", True)),
            ts=str(data.get("ts") or ""),
            currency=str(data.get("currency") or "USD"),
            total_value=float(data.get("total_value") or 0),
            equity_value=float(data.get("equity_value") or 0),
            options_value=float(data.get("options_value") or 0),
            cash=float(data.get("cash") or 0),
            buying_power=float(
                data.get("buying_power")
                if not isinstance(data.get("buying_power"), dict)
                else (data.get("buying_power") or {}).get("buying_power") or 0
            ),
            pending_deposits=float(data.get("pending_deposits") or 0),
            equity_positions=eqs,
            option_positions=opts,
            source=str(data.get("source") or "mcp"),
            notes=list(data.get("notes") or []),
        )


def save_live_portfolio(snap: LivePortfolioSnapshot, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap.to_dict(), indent=2) + "\n")
    return path


def load_live_portfolio(path: Path) -> LivePortfolioSnapshot | None:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
        return LivePortfolioSnapshot.from_dict(data)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        return None


def _f(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def snapshot_from_broker_payloads(
    *,
    account_number: str,
    portfolio: dict[str, Any],
    equity_positions: list[dict[str, Any]] | None = None,
    option_positions: list[dict[str, Any]] | None = None,
    account_nickname: str = "Agentic",
    agentic_allowed: bool = True,
    notes: list[str] | None = None,
) -> LivePortfolioSnapshot:
    """
    Build a snapshot from Robinhood MCP-shaped payloads
    (get_portfolio / get_equity_positions / get_option_positions).
    """
    bp = portfolio.get("buying_power")
    if isinstance(bp, dict):
        buying_power = _f(bp.get("buying_power"))
    else:
        buying_power = _f(bp)

    eqs: list[LiveEquityPosition] = []
    for p in equity_positions or []:
        eqs.append(
            LiveEquityPosition(
                symbol=str(p.get("symbol", "")).upper(),
                quantity=_f(p.get("quantity")),
                average_buy_price=(
                    _f(p["average_buy_price"])
                    if p.get("average_buy_price") not in (None, "")
                    else None
                ),
                shares_available_for_sells=(
                    _f(p["shares_available_for_sells"])
                    if p.get("shares_available_for_sells") not in (None, "")
                    else None
                ),
                shares_held_for_sells=(
                    _f(p["shares_held_for_sells"])
                    if p.get("shares_held_for_sells") not in (None, "")
                    else None
                ),
                type=str(p.get("type") or "long"),
            )
        )

    opts: list[LiveOptionPosition] = []
    for p in option_positions or []:
        opts.append(
            LiveOptionPosition(
                chain_symbol=str(p.get("chain_symbol") or p.get("symbol") or "").upper(),
                quantity=_f(p.get("quantity")),
                average_price=(
                    _f(p["average_price"])
                    if p.get("average_price") not in (None, "")
                    else None
                ),
                option_id=p.get("option_id") or p.get("id"),
                type=p.get("type"),
                expiration_date=p.get("expiration_date"),
                strike_price=(
                    str(p["strike_price"]) if p.get("strike_price") is not None else None
                ),
                option_type=p.get("option_type"),
                raw=dict(p),
            )
        )

    note_list = list(notes or [])
    held = [e for e in eqs if (e.shares_held_for_sells or 0) > 0]
    if held:
        note_list.append(
            f"{len(held)} equity position(s) have shares held for open sells "
            "(reduces free inventory / may pin buying power)."
        )
    if buying_power < 10:
        note_list.append(
            f"Buying power is low (${buying_power:.2f}); new equity/option debits may fail."
        )
    if not opts:
        note_list.append("No open option positions.")

    return LivePortfolioSnapshot(
        account_number=str(account_number),
        account_nickname=account_nickname,
        agentic_allowed=agentic_allowed,
        ts=datetime.now(timezone.utc).isoformat(),
        currency=str(portfolio.get("currency") or "USD"),
        total_value=_f(portfolio.get("total_value")),
        equity_value=_f(portfolio.get("equity_value")),
        options_value=_f(portfolio.get("options_value")),
        cash=_f(portfolio.get("cash")),
        buying_power=buying_power,
        pending_deposits=_f(portfolio.get("pending_deposits")),
        equity_positions=eqs,
        option_positions=opts,
        source="mcp",
        notes=note_list,
    )
