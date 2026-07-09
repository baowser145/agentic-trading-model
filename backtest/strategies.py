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


def qualifying_gap(open_price, prev_close, price, volume, params):
    """Shared gap-qualification math: gap% of open vs. prior close, plus price/volume floors.

    `params` is any object with gap_pct_min / price_min / volume_min attributes
    (GapScanParams or EarningsGapPeadParams — both strategies use identical thresholds math).
    Every gap computation in this project must go through here so the live CLI
    (live_scan/evaluate.py) can never drift from the backtested rules.

    Returns (qualifies: bool, gap_pct: float).
    """
    if prev_close <= 0:
        return False, 0.0
    gap_pct = (open_price - prev_close) / prev_close * 100
    qualifies = gap_pct >= params.gap_pct_min and price >= params.price_min and volume >= params.volume_min
    return qualifies, gap_pct


def gap_scan(open_price, prev_close, price, volume, params: GapScanParams = GapScanParams()):
    """Gap-up scan: today's open vs. prior close.

    Returns (hit: bool, gap_pct: float).
    """
    return qualifying_gap(open_price, prev_close, price, volume, params)


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


# A gap counts as an earnings reaction if it lands on the report day (am report) or up to this
# many calendar days AFTER it (pm report -> next-day gap; weekend in between for a Friday pm report).
EARNINGS_WINDOW_MAX_DAYS_AFTER = 3


def earnings_window_ok(days_since_earnings: int, max_days_after: int = EARNINGS_WINDOW_MAX_DAYS_AFTER) -> bool:
    """True if a gap `days_since_earnings` calendar days after a report counts as an earnings
    reaction (0 = same-day am report, 1-3 = pm report causing a later gap). Negative values
    (gap BEFORE the report) never qualify.
    """
    return 0 <= days_since_earnings <= max_days_after


def is_earnings_gap(gap_day, earnings_dates: set, max_days_after: int = EARNINGS_WINDOW_MAX_DAYS_AFTER) -> bool:
    """True if `gap_day` (a date) falls on or up to `max_days_after` calendar days after an
    earnings report date in `earnings_dates` (a set of dates) -- covers both same-day (am report)
    and next-day (pm report) gaps.
    """
    return any(earnings_window_ok((gap_day - ed).days, max_days_after) for ed in earnings_dates)


def earnings_gap_pead_entry(open_price, prev_close, price, volume, gap_day, earnings_dates: set,
                             params: EarningsGapPeadParams = EarningsGapPeadParams()):
    """Entry signal for the validated PEAD strategy: a qualifying gap on an earnings-reaction day.

    This is the ONLY strategy in this project with a statistically significant backtested edge,
    validated in-sample (2024-2026: +1.70%/trade, p=0.00019, see backtest/report_pead_earnings_gap.md)
    AND out-of-sample (2022-2024: +3.57%/trade, p<0.00001, see
    backtest/report_pead_out_of_sample_2022_2024.md). Remaining caveats: survivorship bias
    (current S&P 500 list applied to historical periods) and large single-trade tail risk
    (worst historical trade -38%, no stop-loss — stops tested and rejected).

    Returns (hit: bool, gap_pct: float).
    """
    qualifies, gap_pct = qualifying_gap(open_price, prev_close, price, volume, params)
    hit = qualifies and is_earnings_gap(gap_day, earnings_dates)
    return hit, gap_pct


def earnings_gap_pead_exit_due(trading_days_elapsed: int, params: EarningsGapPeadParams = EarningsGapPeadParams()):
    """True once `trading_days_elapsed` (counted by the caller from the entry day to today,
    trading days only -- not calendar days) reaches the strategy's hold period. Keeps the "20"
    threshold in one place rather than duplicated at every call site.
    """
    return trading_days_elapsed >= params.hold_trading_days
