from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agentic_trading.agent.research import load_daily_focus
from agentic_trading.config import AppConfig
from agentic_trading.live.portfolio import (
    LivePortfolioSnapshot,
    day_baseline_path_for,
    day_pnl_pct,
    load_live_portfolio,
)
from agentic_trading.market.quotes import FixtureQuoteProvider


@dataclass
class OptionProposal:
    """Supervised long-premium proposal — never places an order."""

    ts: str
    account_number: str
    symbol: str
    option_type: str  # call | put
    bias: str
    contracts: int
    max_premium_usd: float
    min_dte: int
    max_dte: int
    target_delta_low: float
    target_delta_high: float
    estimated_underlying_price: float | None
    suggested_strike_hint: float | None
    suggested_expiry_window: list[str]
    market_filter: str
    daily_focus_picks: list[str]
    buying_power: float | None
    usable_buying_power: float | None  # BP × bp_usage_pct (learn-mode cap)
    bp_usage_pct: float
    cash: float | None
    blocked: bool
    block_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    thesis: str = ""
    risk_notes: list[str] = field(default_factory=list)
    manage_rules: dict[str, Any] = field(default_factory=dict)
    mcp_next_steps: list[dict[str, Any]] = field(default_factory=list)
    place_allowed: bool = False  # True only with standing auth + not blocked

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ref_price(symbol: str) -> float | None:
    try:
        q = FixtureQuoteProvider().get_quotes([symbol]).get(symbol)
        return float(q.price) if q else None
    except Exception:
        return None


def _expiry_window(min_dte: int, max_dte: int) -> list[str]:
    today = datetime.now(timezone.utc).date()
    start = today + timedelta(days=min_dte)
    end = today + timedelta(days=max_dte)
    # Prefer Fridays in window as common option expiries
    fridays: list[str] = []
    d = start
    while d <= end:
        if d.weekday() == 4:
            fridays.append(d.isoformat())
        d += timedelta(days=1)
    if not fridays:
        fridays = [start.isoformat(), end.isoformat()]
    return fridays[:6]


def propose_option(
    config: AppConfig,
    *,
    symbol: str | None = None,
    option_type: str = "call",
    max_premium: float | None = None,
    contracts: int | None = None,
    min_dte: int | None = None,
    max_dte: int | None = None,
    live: LivePortfolioSnapshot | None = None,
    live_path: Path | None = None,
) -> OptionProposal:
    """
    Build a long-premium option proposal for human/MCP review.

    Does NOT place orders. Does NOT call Robinhood APIs directly —
    emits mcp_next_steps for chains → instruments → review_option_order.
    """
    focus = (
        load_daily_focus(config.daily_focus.path)
        if config.daily_focus.path
        else None
    )
    picks = list((focus or {}).get("daily_picks") or [])
    picks = [str(s).upper() for s in picks]
    focus_bias = str((focus or {}).get("market_bias") or "").strip().lower()

    ot = option_type.strip().lower()
    # If caller left default "call" but morning bias is put, align proposal to puts
    if ot == "call" and focus_bias == "put" and symbol is None:
        ot = "put"
    if ot not in ("call", "put"):
        raise ValueError("option_type must be 'call' or 'put'")

    max_prem = float(
        max_premium
        if max_premium is not None
        else getattr(config, "max_option_premium", 100.0)
    )
    max_prem = max(1.0, min(max_prem, 500.0))
    n_contracts = int(contracts if contracts is not None else 1)
    n_contracts = max(1, min(n_contracts, int(getattr(config, "max_option_contracts", 1))))
    dte_lo = int(min_dte if min_dte is not None else getattr(config, "option_min_dte", 7))
    dte_hi = int(max_dte if max_dte is not None else getattr(config, "option_max_dte", 31))
    if dte_lo < 1:
        dte_lo = 1
    if dte_hi < dte_lo:
        dte_hi = dte_lo

    if symbol:
        sym = symbol.upper().strip()
    elif picks:
        sym = picks[0]
    else:
        # Prefer liquid names from config, skip indices
        pool = [
            s
            for s in config.symbols
            if s not in ("SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "SMH")
        ]
        sym = pool[0] if pool else "AAPL"

    if live is None and live_path is not None:
        live = load_live_portfolio(live_path)
    if live is None and getattr(config, "live_portfolio_path", None):
        live = load_live_portfolio(config.live_portfolio_path)  # type: ignore[arg-type]

    account = (
        live.account_number
        if live and live.account_number
        else str(getattr(config, "agentic_account_number", "") or "")
    )

    block: list[str] = []
    warnings: list[str] = []
    bp = live.buying_power if live else None
    cash = live.cash if live else None
    bp_usage = float(getattr(config, "bp_usage_pct", 1.0) or 1.0)
    bp_usage = max(0.05, min(1.0, bp_usage))
    usable_bp = (float(bp) * bp_usage) if bp is not None else None
    learning = bool(getattr(config, "learning_mode", False))

    day_halt = float(getattr(config.risk, "max_daily_loss_pct", 0.05))

    # Cap premium to usable BP (50% of broker BP in learn mode)
    if usable_bp is not None and usable_bp > 0:
        if max_prem > usable_bp:
            warnings.append(
                f"max_premium ${max_prem:.2f} capped to usable BP "
                f"${usable_bp:.2f} ({bp_usage:.0%} of broker BP ${bp:.2f})."
            )
            max_prem = max(1.0, usable_bp)

    if live and not live.agentic_allowed:
        block.append("Snapshot account is not agentic_allowed — refuse live proposals.")
    max_open = int(getattr(config, "max_open_options", 1))
    if live and live.option_positions:
        open_opts = [
            p for p in live.option_positions if abs(p.quantity) > 1e-9
        ]
        if len(open_opts) >= max_open:
            block.append(
                f"Already {len(open_opts)} open option position(s) at "
                f"max_open_options={max_open} — manage (TP/SL/time), do not add."
            )
        elif open_opts:
            warnings.append(
                f"Already {len(open_opts)} open option position(s); "
                f"playbook max_open_options={max_open} — manage (TP/SL/time)."
            )
    if live is not None and getattr(config, "live_portfolio_path", None):
        pnl = day_pnl_pct(live, day_baseline_path_for(config.live_portfolio_path))  # type: ignore[arg-type]
        if pnl <= -day_halt:
            block.append(
                f"Day P&L {pnl:.2%} breached account day-kill -{day_halt:.0%}; "
                "no new option risk today."
            )
    if usable_bp is not None and usable_bp < max_prem * 0.5:
        block.append(
            f"Usable buying power ${usable_bp:.2f} "
            f"({bp_usage:.0%} of broker BP ${bp:.2f}) is below half of max premium "
            f"${max_prem:.2f}; free BP (settle sells) before new debit."
        )
    if bp is not None and bp < 5:
        block.append(f"Buying power critically low (${bp:.2f}).")
    if usable_bp is not None and usable_bp < 5:
        block.append(
            f"Usable BP critically low (${usable_bp:.2f} = {bp_usage:.0%} of ${bp:.2f})."
        )
    if cash is not None and cash < max_prem * 0.25 and (
        usable_bp is None or usable_bp < max_prem
    ):
        warnings.append(
            f"Cash ${cash:.2f} may be insufficient for full premium ${max_prem:.2f}."
        )
    if learning:
        warnings.append(
            "learning_mode=true: paper process first; live only with free usable BP "
            f"({bp_usage:.0%} of broker buying power)."
        )
    if picks and sym not in picks:
        warnings.append(
            f"{sym} is not in today's daily_focus picks {picks}; override was explicit or fallback."
        )
    if dte_lo < 7:
        warnings.append("DTE window allows <7 days — higher theta risk.")

    px = _ref_price(sym)
    # Rough ATM / slightly OTM strike hint (fixture or None)
    strike_hint = None
    if px is not None:
        if ot == "call":
            strike_hint = round(px * 1.02 / 5) * 5  # ~2% OTM, $5 grid
        else:
            strike_hint = round(px * 0.98 / 5) * 5

    bias = "bullish_debit_call" if ot == "call" else "bearish_debit_put"
    market_filter = (
        "Prefer calls only if broad market filter green (e.g. SPY > SMA); "
        "puts if filter red. Confirm with live tape — fixtures are not live."
    )

    tp_lo = float(getattr(config, "option_take_profit_pct_low", 0.10))
    tp_hi = float(getattr(config, "option_take_profit_pct_high", 0.20))
    sl = float(getattr(config, "option_stop_loss_pct", 0.10))
    exit_dte = int(getattr(config, "option_exit_dte", 3))

    thesis = (
        f"Long {ot} on {sym}: defined risk = premium paid (max ${max_prem:.0f} "
        f"for {n_contracts} contract(s)). Target liquid expiry in {dte_lo}-{dte_hi} DTE, "
        f"delta ~0.30-0.50. Manage: take profit +{tp_lo:.0%}\u2013{tp_hi:.0%} premium, "
        f"stop \u2212{sl:.0%} premium, exit by {exit_dte} DTE. "
        f"Account day kill: halt new risk if day P&L \u2264 \u2212{day_halt:.0%}."
    )
    risk_notes = [
        "Max loss = premium paid (+ fees); do not average down automatically.",
        "No 0DTE in v1 playbook.",
        "Single-leg Level 2 only via Robinhood MCP (no multi-leg spreads).",
        f"Exit: +{tp_lo:.0%}–{tp_hi:.0%} take-profit OR −{sl:.0%} stop OR ≤{exit_dte} DTE.",
        f"If account loses ≥{day_halt:.0%} on the day → stop all new trades.",
        f"BP budget: only {bp_usage:.0%} of broker buying_power for new risk "
        f"(usable ≈ ${usable_bp:.2f})." if usable_bp is not None
        else f"BP budget: only {bp_usage:.0%} of broker buying_power for new risk.",
        "Always review_option_order before place_option_order.",
    ]
    place_wo = bool(getattr(config, "options_place_without_confirm", False))
    if place_wo:
        risk_notes.append(
            "Standing auth: place_option_order allowed after clean review without per-trade yes "
            "(Agentic only; BP/gates still apply)."
        )
    else:
        risk_notes.append("place_allowed false until explicit user confirm (or enable standing auth).")
    if sl <= 0.15:
        warnings.append(
            f"Option stop −{sl:.0%} is tight (bid/ask noise; BAC postmortem used −10%). "
            "Expect more stop-outs; size small and use liquid names."
        )

    manage_rules = {
        "take_profit_pct_low": tp_lo,
        "take_profit_pct_high": tp_hi,
        "stop_loss_pct": sl,
        "exit_dte": exit_dte,
        "max_open_options": max_open,
        "daily_account_halt_pct": day_halt,
        "metric": "option mark vs entry premium",
        "user_chosen_stop": True,
        "note": (
            f"Sell if mark ≤ entry×(1−{sl:.0%}); take profit when mark ≥ entry×(1+{tp_lo:.0%}) "
            f"(stretch to +{tp_hi:.0%} if still strong). Day P&L ≤ −{day_halt:.0%} → stop new trades."
        ),
    }

    expiries = _expiry_window(dte_lo, dte_hi)
    mcp_steps: list[dict[str, Any]] = [
        {
            "step": 1,
            "tool": "robinhood__get_accounts",
            "purpose": "Confirm agentic_allowed account matches proposal account_number",
        },
        {
            "step": 2,
            "tool": "robinhood__get_portfolio",
            "args": {"account_number": account},
            "purpose": "Re-check cash and buying_power before review",
        },
        {
            "step": 3,
            "tool": "robinhood__get_option_chains",
            "args": {"symbol": sym},
            "purpose": "Resolve chain_id for underlying",
        },
        {
            "step": 4,
            "tool": "robinhood__get_option_instruments",
            "args": {
                "chain_symbol": sym,
                "type": ot,
                "expiration_date_gte": expiries[0] if expiries else None,
                "expiration_date_lte": expiries[-1] if expiries else None,
            },
            "purpose": "Pick liquid strike near target delta / strike_hint",
            "strike_hint": strike_hint,
            "max_debit_per_contract": round(max_prem / max(n_contracts, 1) / 100.0, 2),
        },
        {
            "step": 5,
            "tool": "robinhood__review_option_order",
            "args": {
                "account_number": account,
                "quantity": str(n_contracts),
                "type": "limit",
                "legs": [
                    {
                        "option_id": "<from get_option_instruments>",
                        "side": "buy",
                        "position_effect": "open",
                    }
                ],
                "price": "<limit debit per contract>",
                "chain_symbol": sym,
                "underlying_type": "equity",
            },
            "purpose": "Simulate order; surface alerts — do not place yet",
        },
        {
            "step": 6,
            "tool": "robinhood__place_option_order",
            "purpose": (
                "After clean review — standing auth allows place without per-trade yes"
                if place_wo
                else "ONLY after human explicit confirm of review result"
            ),
            "blocked_by_cli": not place_wo,
            "standing_auth": place_wo,
        },
    ]

    # Proposal itself never places; place_allowed signals agent may continue to place later
    proposal_place_ok = place_wo and not bool(block)

    return OptionProposal(
        ts=datetime.now(timezone.utc).isoformat(),
        account_number=account,
        symbol=sym,
        option_type=ot,
        bias=bias,
        contracts=n_contracts,
        max_premium_usd=max_prem,
        min_dte=dte_lo,
        max_dte=dte_hi,
        target_delta_low=0.30,
        target_delta_high=0.50,
        estimated_underlying_price=px,
        suggested_strike_hint=strike_hint,
        suggested_expiry_window=expiries,
        market_filter=market_filter,
        daily_focus_picks=picks,
        buying_power=bp,
        usable_buying_power=round(usable_bp, 2) if usable_bp is not None else None,
        bp_usage_pct=bp_usage,
        cash=cash,
        blocked=bool(block),
        block_reasons=block,
        warnings=warnings,
        thesis=thesis,
        risk_notes=risk_notes,
        manage_rules=manage_rules,
        mcp_next_steps=mcp_steps,
        place_allowed=proposal_place_ok,
    )


def save_proposal(proposal: OptionProposal, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proposal.to_dict(), indent=2) + "\n")
    return path
