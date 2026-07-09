"""Shared strategy rule definitions.

Used by both the historical backtest (run_backtest.py) and, later, the live scan
(docs/live-scan-prompt.md instructs the scheduled session to call these same functions via Bash,
rather than have the LLM re-derive the arithmetic itself).
"""

from dataclasses import dataclass


@dataclass
class GapScanParams:
    gap_pct_min: float = 5.0
    price_min: float = 3.0
    volume_min: float = 500_000


def gap_scan(open_price, prev_close, price, volume, params: GapScanParams = GapScanParams()):
    """Gap-up scan: today's open vs. prior close.

    Returns (hit: bool, gap_pct: float).
    """
    if prev_close <= 0:
        return False, 0.0
    gap_pct = (open_price - prev_close) / prev_close * 100
    hit = gap_pct >= params.gap_pct_min and price >= params.price_min and volume >= params.volume_min
    return hit, gap_pct


@dataclass
class TrendJoinLongParams:
    after_hour_et: int = 10


def trend_join_long(
    close,
    prev_day_high,
    sma200,
    premarket_high=None,
    hour_et=None,
    intraday_high=None,
    params: TrendJoinLongParams = TrendJoinLongParams(),
):
    """Trend-join-long: price above prior day high, above SMA200, above premarket high (if
    available), and (if intraday data available) making a new intraday high after the cutoff hour.

    premarket_high / hour_et / intraday_high are optional so the same function runs against:
    - daily-bar backtest (None) — checks prior-day-high + SMA200 only
    - live intraday data (populated) — full condition set
    """
    if sma200 is None or prev_day_high is None:
        return False

    conditions = [close > prev_day_high, close > sma200]

    if premarket_high is not None:
        conditions.append(close > premarket_high)

    if hour_et is not None:
        conditions.append(hour_et >= params.after_hour_et)

    if intraday_high is not None:
        conditions.append(close >= intraday_high)

    return all(conditions)


@dataclass
class EarningsGapPeadParams:
    gap_pct_min: float = 5.0
    price_min: float = 3.0
    volume_min: float = 500_000
    hold_trading_days: int = 20


def is_earnings_gap(gap_day, earnings_dates: set, max_days_before: int = 3) -> bool:
    """True if `gap_day` (a date) falls on or up to `max_days_before` calendar days after an
    earnings report date in `earnings_dates` (a set of dates) -- covers both same-day (am report)
    and next-day (pm report) gaps.
    """
    return any(0 <= (gap_day - ed).days <= max_days_before for ed in earnings_dates)


def earnings_gap_pead_entry(open_price, prev_close, price, volume, gap_day, earnings_dates: set,
                             params: EarningsGapPeadParams = EarningsGapPeadParams()):
    """Entry signal for the validated PEAD strategy: a qualifying gap on an earnings-reaction day.

    This is the ONLY strategy in this project with a statistically significant backtested edge
    (see backtest/report_pead_earnings_gap.md: +1.47%/trade, p=0.0013, 2yr/full S&P 500, confirmed
    directionally by an independent from-scratch re-implementation). It has NOT been validated
    out-of-sample on a market period different from the one it was found in -- treat accordingly.

    Returns (hit: bool, gap_pct: float).
    """
    if prev_close <= 0:
        return False, 0.0
    gap_pct = (open_price - prev_close) / prev_close * 100
    qualifies = gap_pct >= params.gap_pct_min and price >= params.price_min and volume >= params.volume_min
    hit = qualifies and is_earnings_gap(gap_day, earnings_dates)
    return hit, gap_pct


def earnings_gap_pead_exit_due(trading_days_elapsed: int, params: EarningsGapPeadParams = EarningsGapPeadParams()):
    """True once `trading_days_elapsed` (counted by the caller from the entry day to today,
    trading days only -- not calendar days) reaches the strategy's hold period. Keeps the "20"
    threshold in one place rather than duplicated at every call site.
    """
    return trading_days_elapsed >= params.hold_trading_days
