"""Black-Scholes pricing + greeks for long-premium simulation."""

from __future__ import annotations

import math
from dataclasses import dataclass


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass(frozen=True)
class BSPrice:
    premium: float
    delta: float
    d1: float
    d2: float


def black_scholes(
    spot: float,
    strike: float,
    t_years: float,
    iv: float,
    rate: float = 0.04,
    option_type: str = "call",
) -> BSPrice:
    """European BS price. Returns intrinsic floor when t or iv collapses."""
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be > 0")
    ot = option_type.lower()
    if ot not in ("call", "put"):
        raise ValueError("option_type must be call or put")

    if t_years <= 1e-8 or iv <= 1e-8:
        if ot == "call":
            prem = max(0.0, spot - strike)
            delta = 1.0 if spot > strike else 0.0
        else:
            prem = max(0.0, strike - spot)
            delta = -1.0 if spot < strike else 0.0
        return BSPrice(premium=prem, delta=delta, d1=0.0, d2=0.0)

    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t_years) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    df = math.exp(-rate * t_years)

    if ot == "call":
        prem = spot * _norm_cdf(d1) - strike * df * _norm_cdf(d2)
        delta = _norm_cdf(d1)
    else:
        prem = strike * df * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0

    return BSPrice(premium=max(0.0, prem), delta=delta, d1=d1, d2=d2)


def strike_for_target_delta(
    spot: float,
    t_years: float,
    iv: float,
    target_delta: float,
    option_type: str = "call",
    rate: float = 0.04,
    *,
    lo_mult: float = 0.5,
    hi_mult: float = 1.8,
    steps: int = 40,
) -> float:
    """
    Approximate strike whose |delta| is closest to target_delta.

    For calls target_delta in (0,1); for puts use positive target and match |delta|.
    """
    ot = option_type.lower()
    want = abs(float(target_delta))
    best_k = spot
    best_err = 1e9
    lo = spot * lo_mult
    hi = spot * hi_mult
    for i in range(steps + 1):
        k = lo + (hi - lo) * i / steps
        if k <= 0:
            continue
        px = black_scholes(spot, k, t_years, iv, rate=rate, option_type=ot)
        err = abs(abs(px.delta) - want)
        if err < best_err:
            best_err = err
            best_k = k
    # Snap to $0.50 or $1 grid for realism
    if best_k < 50:
        return round(best_k * 2) / 2.0
    if best_k < 200:
        return round(best_k)
    return round(best_k / 5.0) * 5.0


def realized_vol(closes: list[float], window: int = 20, annualize: float = 252.0) -> float:
    """Annualized log-return stdev over trailing window. Floor at 10%."""
    if len(closes) < window + 1:
        window = max(5, len(closes) - 1)
    if window < 2 or len(closes) < window + 1:
        return 0.25
    rets: list[float] = []
    seg = closes[-(window + 1) :]
    for i in range(1, len(seg)):
        if seg[i - 1] > 0 and seg[i] > 0:
            rets.append(math.log(seg[i] / seg[i - 1]))
    if len(rets) < 2:
        return 0.25
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return max(0.10, min(1.50, math.sqrt(var * annualize)))


def sma(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def momentum_pct(closes: list[float], lookback: int) -> float | None:
    if lookback <= 0 or len(closes) < lookback + 1:
        return None
    base = closes[-(lookback + 1)]
    if base <= 0:
        return None
    return (closes[-1] / base) - 1.0
