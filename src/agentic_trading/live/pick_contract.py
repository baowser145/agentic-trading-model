from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class PickedContract:
    option_id: str
    chain_symbol: str
    option_type: str
    strike_price: float
    expiration_date: str
    state: str | None = None
    tradability: str | None = None
    score: float = 0.0
    reason: str = ""
    alternatives: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_instruments_payload(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    # Common MCP envelopes
    for key in ("results", "instruments", "option_instruments", "data"):
        val = data.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
        if isinstance(val, dict):
            for k2 in ("results", "instruments"):
                if isinstance(val.get(k2), list):
                    return [x for x in val[k2] if isinstance(x, dict)]
    # Single instrument
    if data.get("id") or data.get("option_id"):
        return [data]
    return []


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _id(row: dict[str, Any]) -> str | None:
    for k in ("id", "option_id", "instrument_id", "uuid"):
        if row.get(k):
            return str(row[k])
    return None


def pick_option_contract(
    instruments: Any,
    *,
    option_type: str = "call",
    strike_hint: float | None = None,
    min_dte: int = 7,
    max_dte: int = 31,
    underlying_price: float | None = None,
    max_candidates: int = 5,
) -> PickedContract | None:
    """
    Pick a liquid-ish long-premium candidate from get_option_instruments rows.

    Prefers active/tradable, DTE in [min_dte, max_dte], strike nearest strike_hint
    (or slightly OTM vs underlying_price).
    """
    ot = option_type.strip().lower()
    rows = _parse_instruments_payload(instruments)
    today = datetime.now(timezone.utc).date()
    scored: list[tuple[float, dict[str, Any], str]] = []

    for row in rows:
        rid = _id(row)
        if not rid:
            continue
        rtype = str(row.get("type") or row.get("option_type") or "").lower()
        if rtype and rtype not in (ot,):
            # allow missing type if filtered upstream
            if rtype in ("call", "put") and rtype != ot:
                continue
        state = str(row.get("state") or "active").lower()
        if state and state not in ("active", ""):
            continue
        trad = str(row.get("tradability") or "tradable").lower()
        if trad in ("untradable", "untradeable"):
            continue
        exp_s = str(row.get("expiration_date") or row.get("expiry") or "")[:10]
        try:
            exp_d = date.fromisoformat(exp_s)
        except ValueError:
            continue
        dte = (exp_d - today).days
        if dte < min_dte or dte > max_dte:
            continue
        strike = _f(row.get("strike_price") or row.get("strike"))
        if strike is None:
            continue

        # Score: closer to hint better; prefer mid-window DTE (~21)
        if strike_hint is not None:
            strike_pen = abs(strike - strike_hint) / max(strike_hint, 1.0)
        elif underlying_price is not None:
            # Slightly OTM: call above spot, put below
            target = underlying_price * (1.02 if ot == "call" else 0.98)
            strike_pen = abs(strike - target) / max(underlying_price, 1.0)
        else:
            strike_pen = 0.0
        dte_pen = abs(dte - 21) / 45.0
        score = 1.0 - min(1.0, strike_pen) * 0.7 - min(1.0, dte_pen) * 0.3
        reason = f"dte={dte} strike={strike} score={score:.3f}"
        scored.append((score, row, reason))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best, reason = scored[0]
    strike = float(best.get("strike_price") or best.get("strike"))
    exp_s = str(best.get("expiration_date") or best.get("expiry") or "")[:10]
    alts = []
    for s, row, r in scored[1:max_candidates]:
        alts.append(
            {
                "option_id": _id(row),
                "strike_price": row.get("strike_price") or row.get("strike"),
                "expiration_date": str(row.get("expiration_date") or "")[:10],
                "score": round(s, 4),
                "reason": r,
            }
        )
    return PickedContract(
        option_id=str(_id(best)),
        chain_symbol=str(
            best.get("chain_symbol")
            or best.get("symbol")
            or best.get("underlying")
            or ""
        ).upper(),
        option_type=ot,
        strike_price=strike,
        expiration_date=exp_s,
        state=str(best.get("state") or "") or None,
        tradability=str(best.get("tradability") or "") or None,
        score=float(best_score),
        reason=reason,
        alternatives=alts,
    )


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text())


def save_picked(picked: PickedContract, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(picked.to_dict(), indent=2) + "\n")
    return path
