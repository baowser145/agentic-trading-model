"""Locks down the strategy math shared between the backtest and the live scan CLI.

These functions decide real-money alerts — any threshold change must show up here first.
"""
from datetime import date

import pytest

from strategies import (
    EarningsGapPeadParams,
    GapScanParams,
    earnings_gap_pead_entry,
    earnings_gap_pead_exit_due,
    earnings_window_ok,
    gap_scan,
    is_earnings_gap,
    qualifying_gap,
    trend_join_long,
)


class TestQualifyingGap:
    def test_qualifying_gap_hit(self):
        qualifies, gap_pct = qualifying_gap(105.0, 100.0, 105.0, 1_000_000, GapScanParams())
        assert qualifies
        assert gap_pct == pytest.approx(5.0)

    def test_gap_below_threshold(self):
        qualifies, gap_pct = qualifying_gap(104.9, 100.0, 105.0, 1_000_000, GapScanParams())
        assert not qualifies
        assert gap_pct == pytest.approx(4.9)

    def test_price_floor(self):
        qualifies, _ = qualifying_gap(2.9, 2.5, 2.9, 1_000_000, GapScanParams())
        assert not qualifies  # 16% gap but price under $3

    def test_volume_floor(self):
        qualifies, _ = qualifying_gap(105.0, 100.0, 105.0, 499_999, GapScanParams())
        assert not qualifies

    def test_zero_prev_close_no_crash(self):
        qualifies, gap_pct = qualifying_gap(105.0, 0.0, 105.0, 1_000_000, GapScanParams())
        assert not qualifies
        assert gap_pct == 0.0

    def test_gap_scan_is_same_math(self):
        args = (105.0, 100.0, 105.0, 1_000_000)
        assert gap_scan(*args) == qualifying_gap(*args, GapScanParams())

    def test_pead_params_same_thresholds_as_gap_scan(self):
        # The PEAD strategy was validated with the SAME gap thresholds as the original gap scan.
        g, p = GapScanParams(), EarningsGapPeadParams()
        assert (g.gap_pct_min, g.price_min, g.volume_min) == (p.gap_pct_min, p.price_min, p.volume_min)


class TestEarningsWindow:
    @pytest.mark.parametrize("days,expected", [
        (-1, False),  # gap BEFORE the report never qualifies
        (0, True),    # same-day am report
        (1, True),
        (3, True),    # boundary: pm report, weekend in between
        (4, False),
    ])
    def test_earnings_window_ok(self, days, expected):
        assert earnings_window_ok(days) is expected

    def test_is_earnings_gap_matches_window(self):
        edates = {date(2026, 1, 15)}
        assert not is_earnings_gap(date(2026, 1, 14), edates)
        assert is_earnings_gap(date(2026, 1, 15), edates)
        assert is_earnings_gap(date(2026, 1, 18), edates)
        assert not is_earnings_gap(date(2026, 1, 19), edates)

    def test_is_earnings_gap_any_of_multiple_dates(self):
        edates = {date(2025, 10, 20), date(2026, 1, 15)}
        assert is_earnings_gap(date(2025, 10, 21), edates)


class TestPeadEntry:
    def test_qualifying_gap_on_earnings_day_hits(self):
        hit, gap_pct = earnings_gap_pead_entry(
            105.0, 100.0, 105.0, 1_000_000, date(2026, 1, 15), {date(2026, 1, 15)}
        )
        assert hit
        assert gap_pct == pytest.approx(5.0)

    def test_qualifying_gap_off_earnings_day_misses(self):
        hit, gap_pct = earnings_gap_pead_entry(
            105.0, 100.0, 105.0, 1_000_000, date(2026, 2, 20), {date(2026, 1, 15)}
        )
        assert not hit
        assert gap_pct == pytest.approx(5.0)  # gap% still reported for visibility

    def test_weak_gap_on_earnings_day_misses(self):
        hit, _ = earnings_gap_pead_entry(
            103.0, 100.0, 103.0, 1_000_000, date(2026, 1, 15), {date(2026, 1, 15)}
        )
        assert not hit


class TestPeadExit:
    @pytest.mark.parametrize("days,due", [(19, False), (20, True), (21, True)])
    def test_exit_due_at_hold_period(self, days, due):
        assert earnings_gap_pead_exit_due(days) is due


class TestTrendJoinLong:
    def test_daily_only_conditions(self):
        assert trend_join_long(close=101.0, prev_day_high=100.0, sma200=90.0)
        assert not trend_join_long(close=99.0, prev_day_high=100.0, sma200=90.0)
        assert not trend_join_long(close=101.0, prev_day_high=100.0, sma200=110.0)

    def test_missing_indicators_never_hit(self):
        assert not trend_join_long(close=101.0, prev_day_high=None, sma200=90.0)
        assert not trend_join_long(close=101.0, prev_day_high=100.0, sma200=None)

    def test_optional_live_conditions(self):
        base = dict(close=101.0, prev_day_high=100.0, sma200=90.0)
        assert trend_join_long(**base, premarket_high=100.5, hour_et=10)
        assert not trend_join_long(**base, premarket_high=102.0, hour_et=10)  # below premarket high
        assert not trend_join_long(**base, premarket_high=100.5, hour_et=9)   # before cutoff hour
        assert not trend_join_long(**base, intraday_high=102.0)               # not a new intraday high
