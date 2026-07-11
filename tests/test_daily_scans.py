"""Offline tests for the scheduled daily scan scripts (no network calls).

Covers the pieces the Robinhood MCP used to provide and the message contracts the prompt docs
mandate: Black-Scholes delta, option-contract selection, gap scanning against synthetic bars, and
alert composition (including the verbatim PEAD caveat block).
"""

from unittest import mock

import pandas as pd
import pytest

import daily_pead_check
import daily_pre_earnings_screen as screen
import market_data as md


class TestBsCallDelta:
    def test_atm_is_near_half(self):
        delta = md.bs_call_delta(spot=100, strike=100, t_years=30 / 365, iv=0.3)
        assert 0.45 < delta < 0.65

    def test_deep_otm_near_zero_and_deep_itm_near_one(self):
        assert md.bs_call_delta(100, 200, 7 / 365, 0.3) < 0.01
        assert md.bs_call_delta(100, 50, 7 / 365, 0.3) > 0.99

    def test_monotonically_decreasing_in_strike(self):
        deltas = [md.bs_call_delta(100, k, 30 / 365, 0.4) for k in (90, 100, 110, 120, 130)]
        assert deltas == sorted(deltas, reverse=True)

    def test_degenerate_inputs_collapse_to_intrinsic(self):
        assert md.bs_call_delta(100, 90, 0, 0.3) == 1.0
        assert md.bs_call_delta(100, 110, 0, 0.3) == 0.0
        assert md.bs_call_delta(100, 110, 30 / 365, 0) == 0.0
        assert md.bs_call_delta(0, 110, 30 / 365, 0.3) == 0.0


class TestEarningsTiming:
    def test_premarket_is_am(self):
        assert md.earnings_timing(pd.Timestamp("2026-08-01 07:00")) == "am"

    def test_after_close_is_pm(self):
        assert md.earnings_timing(pd.Timestamp("2026-08-01 16:30")) == "pm"

    def test_midnight_placeholder_and_midday_are_unknown(self):
        assert md.earnings_timing(pd.Timestamp("2026-08-01 00:00")) == "unknown"
        assert md.earnings_timing(pd.Timestamp("2026-08-01 12:00")) == "unknown"


def _chain(rows):
    return pd.DataFrame(rows, columns=["strike", "bid", "ask", "impliedVolatility"])


class TestSelectCallFromChain:
    TODAY = pd.Timestamp("2026-07-10")
    EXP = pd.Timestamp("2026-08-07")

    def test_picks_starting_strike_when_it_qualifies(self):
        # spot 100 -> start strike nearest 107.5
        chain = _chain([(100, 5.0, 5.2, 0.4), (107, 2.0, 2.4, 0.4), (110, 1.0, 1.2, 0.4)])
        pick, reason = md.select_call_from_chain(chain, 100, self.EXP, self.TODAY)
        assert pick is not None and pick["strike"] == 107
        assert pick["cost"] == pytest.approx(240)
        assert pick["delta"] >= md.OPTION_MIN_DELTA

    def test_walks_deeper_otm_past_too_expensive_start(self):
        chain = _chain([(107, 3.4, 3.6, 0.4), (110, 2.4, 2.6, 0.4)])  # 107 costs $360 -> over budget
        pick, _ = md.select_call_from_chain(chain, 100, self.EXP, self.TODAY)
        assert pick is not None and pick["strike"] == 110

    def test_all_over_budget_reports_too_expensive(self):
        chain = _chain([(107, 4.0, 4.2, 0.4), (110, 3.3, 3.5, 0.4)])
        pick, reason = md.select_call_from_chain(chain, 100, self.EXP, self.TODAY)
        assert pick is None and reason == "too expensive even at deepest strike"

    def test_zero_bid_within_budget_reports_illiquid(self):
        chain = _chain([(107, 0.0, 2.4, 0.4), (110, 0.0, 1.2, 0.4)])
        pick, reason = md.select_call_from_chain(chain, 100, self.EXP, self.TODAY)
        assert pick is None and reason == "only illiquid/near-zero-delta strikes fit the budget"

    def test_low_delta_within_budget_reports_illiquid_reason(self):
        # far OTM + tiny IV -> delta ~0 even though cheap and bid > 0
        chain = _chain([(150, 0.05, 0.10, 0.05)])
        pick, reason = md.select_call_from_chain(chain, 100, self.EXP, self.TODAY)
        assert pick is None and reason == "only illiquid/near-zero-delta strikes fit the budget"

    def test_empty_chain(self):
        pick, reason = md.select_call_from_chain(_chain([]), 100, self.EXP, self.TODAY)
        assert pick is None and "no listed calls" in reason


def _bars(index, opens, closes, volume=1_000_000):
    return pd.DataFrame(
        {"Open": opens, "High": closes, "Low": opens, "Close": closes, "Volume": [volume] * len(index)},
        index=pd.DatetimeIndex(index),
    )


class TestPeadScan:
    def test_entry_and_exit_detected_from_synthetic_bars(self):
        days = pd.bdate_range("2026-06-01", periods=25)  # 25 business days; last = today
        today = days[-1]
        n = daily_pead_check.PARAMS.hold_trading_days  # 20

        # ENTRY ticker: +8% open gap on the last day.
        opens, closes = [100.0] * 25, [100.0] * 25
        opens[-1], closes[-1] = 108.0, 109.0
        entry_bars = _bars(days, opens, closes)

        # EXIT ticker: +8% gap exactly n trading days before today (position -(n+1)).
        opens2, closes2 = [50.0] * 25, [50.0] * 25
        opens2[-(n + 1)], closes2[-(n + 1)] = 54.0, 54.5
        exit_bars = _bars(days, opens2, closes2)

        entry_report = days[-1] - pd.Timedelta(days=1)      # reported yesterday -> window ok
        exit_report = days[-(n + 1)]                        # reported on the gap day

        def fake_earnings(ticker, limit=12):
            return {"ENTR": [entry_report + pd.Timedelta(hours=17)], "EXIT": [exit_report + pd.Timedelta(hours=7)]}[ticker]

        with mock.patch.object(md, "get_earnings_dates", side_effect=fake_earnings):
            entries, exits, notes = daily_pead_check.scan({"ENTR": entry_bars, "EXIT": exit_bars}, today)

        assert [e["ticker"] for e in entries] == ["ENTR"]
        assert entries[0]["gap_pct"] == pytest.approx(8.0)
        assert entries[0]["timing"] == "PM"
        assert [x["ticker"] for x in exits] == ["EXIT"]
        assert x_date_equal(exits[0]["entry_day"], days[-(n + 1)])
        assert notes == []

    def test_gap_without_earnings_data_is_skipped_with_note(self):
        days = pd.bdate_range("2026-06-01", periods=5)
        opens, closes = [100.0] * 5, [100.0] * 5
        opens[-1] = 108.0
        with mock.patch.object(md, "get_earnings_dates", return_value=[]):
            entries, exits, notes = daily_pead_check.scan({"ABC": _bars(days, opens, closes)}, days[-1])
        assert entries == [] and exits == []
        assert len(notes) == 1 and "ABC" in notes[0]

    def test_non_earnings_gap_is_not_an_entry(self):
        days = pd.bdate_range("2026-06-01", periods=5)
        opens, closes = [100.0] * 5, [100.0] * 5
        opens[-1] = 108.0
        far_report = [days[-1] - pd.Timedelta(days=30)]  # outside the 0-3 day window
        with mock.patch.object(md, "get_earnings_dates", return_value=far_report):
            entries, _, notes = daily_pead_check.scan({"ABC": _bars(days, opens, closes)}, days[-1])
        assert entries == [] and notes == []


def x_date_equal(a, b):
    return pd.Timestamp(a) == pd.Timestamp(b).normalize()


class TestPeadCompose:
    TODAY = pd.Timestamp("2026-07-10")

    def test_caveat_present_verbatim_and_fallbacks(self):
        msg = daily_pead_check.compose_message([], [], self.TODAY)
        assert daily_pead_check.CAVEAT in msg
        assert "No entry signals today." in msg
        assert "No exit signals today." in msg
        assert "informational only" in msg

    def test_entry_and_exit_cards_render(self):
        entries = [{"ticker": "ABC", "gap_pct": 8.0, "price": 109.0, "report_date": "2026-07-09", "timing": "PM"}]
        exits = [{"ticker": "XYZ", "entry_day": "2026-06-11", "price": 54.2}]
        msg = daily_pead_check.compose_message(entries, exits, self.TODAY)
        assert "🟢 ABC — gapped +8.0% (reported 2026-07-09, PM)" in msg
        assert "💰 Current Price: $109.00" in msg
        assert "🔴 XYZ — entered ~2026-06-11, 20 trading days elapsed" in msg
        assert daily_pead_check.CAVEAT in msg

    def test_market_closed_message(self):
        msg = daily_pead_check.compose_message([], [], self.TODAY, market_open=False)
        assert "Market appears closed today" in msg
        assert daily_pead_check.CAVEAT in msg


class TestScreenPieces:
    TODAY = pd.Timestamp("2026-07-10")

    def _calendar(self):
        return pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "ZZZZ", "COST"],
                "name": ["Apple", "Microsoft", "NotInSp500", "Costco"],
                "reportDate": pd.to_datetime(["2026-07-30", "2026-08-05", "2026-07-30", "2026-07-15"]),
            }
        )

    def test_build_candidates_filters_window_and_membership(self):
        # AAPL: 20 days out (in window). MSFT: 26 days (in). ZZZZ: not S&P 500. COST: 5 days (early).
        cands = screen.build_candidates(self._calendar(), ["AAPL", "MSFT", "COST"], self.TODAY)
        assert [c["ticker"] for c in cands] == ["AAPL", "MSFT"]
        assert all(c["verified"] is True and c["timing"] == "unknown" for c in cands)

    def test_card_with_option_pick_and_buy_under(self):
        row = pd.Series({"ticker": "AAPL", "report_date": "2026-07-30", "win_rate": 0.8})
        pick = {"strike": 215.0, "expiration": pd.Timestamp("2026-07-31"), "bid": 1.10, "ask": 1.25, "cost": 125.0, "delta": 0.22}
        card = screen.compose_card(row, price=200.0, name="Apple Inc.", timing="pm", pick=pick, reason="")
        assert card.startswith("🟢 AAPL — Apple Inc. (reports 2026-07-30 pm)")
        assert "🛒 Buy Under: $198.00" in card
        assert "📈 $215c exp 07/31 — Bid $1.10 / Ask $1.25 (buy = ask, $125/contract, delta 0.22)" in card

    def test_stock_only_card_and_thin_bid_flag(self):
        row = pd.Series({"ticker": "MSFT", "report_date": "2026-08-05", "win_rate": 0.7})
        card = screen.compose_card(row, 400.0, "Microsoft", "unknown", None, "too expensive even at deepest strike")
        assert card.startswith("🟡 MSFT — Microsoft (reports 2026-08-05)")
        assert "📈 Stock-only (too expensive even at deepest strike)" in card

        thin_pick = {"strike": 500.0, "expiration": pd.Timestamp("2026-08-07"), "bid": 0.05, "ask": 0.15, "cost": 15.0, "delta": 0.16}
        thin_card = screen.compose_card(row, 400.0, "Microsoft", "am", thin_pick, "")
        assert "thin bid" in thin_card

    def test_message_always_has_caveats_even_with_no_cards(self):
        msg = screen.compose_message([], self.TODAY, n_candidates=12, failed=["FOO"])
        assert "No qualifying candidates today" in msg
        assert "12 S&P 500 names" in msg
        assert "FOO" in msg
        for line in screen.CAVEAT_LINES:
            assert line in msg
