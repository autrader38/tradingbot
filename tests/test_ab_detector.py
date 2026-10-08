"""Offline fixtures exercise signals and chronology, never simulated trades."""

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal as D, localcontext
from fractions import Fraction

from tradingbot_backtest.ab_detector import ABDetector, EligibilityInputs
from tradingbot_backtest.codes import ReasonCode
from tradingbot_backtest.market import IntervalClassification as K, MarketInterval
from tradingbot_backtest.sessions import NEW_YORK, TradingSession
from tradingbot_backtest.states import StrategyState as S
from tradingbot_backtest.volume import volume_evidence


class Calendar:
    def session_for(self, day):
        return TradingSession(day, datetime.combine(day, datetime.min.time(), NEW_YORK).replace(hour=9, minute=30),
                              datetime.combine(day, datetime.min.time(), NEW_YORK).replace(hour=16), "fixture")


def candle(at, *, low="1", high="1.01", close="1", volume="100", kind=K.TRADED):
    fields = dict(security_id="security", ticker="TEST", timestamp=at, classification=kind, source_id="fixture")
    if kind == K.TRADED:
        fields.update(open=D(close), high=D(high), low=D(low), close=D(close), volume=D(volume))
    elif kind == K.NO_TRADE:
        fields.update(volume=D(0), no_trade_verified=True)
    else:
        fields.update(data_quality_reason="fixture gap")
    return MarketInterval(**fields)


class ABTests(unittest.TestCase):
    def setUp(self):
        self.detector = ABDetector("security", "TEST", Calendar(), run_id="run", data_version="fixture")
        self.inputs = EligibilityInputs(D(".90"), True, "raw-2026-10-08")
        self.at = datetime(2026, 10, 8, 9, 10, tzinfo=NEW_YORK)
        self.events = []
        for _ in range(20):
            self.send()

    def send(self, **kwargs):
        bar = candle(self.at, **kwargs)
        self.events.extend(self.detector.feed(bar, self.inputs))
        self.at += timedelta(minutes=1)
        return bar

    def activate(self, high="1.08", volume="200"):
        self.send(high="1.01")
        return self.send(high=high, volume=volume)

    def confirm(self):
        self.send(high="1.07", close="1.06")
        self.send(high="1.07", close="1.06")

    def test_activation_exact_three_percent_high_not_close(self):
        self.activate("1.03")
        state = self.detector.state
        self.assertEqual(state.lifecycle, S.AB_IMPULSE)
        self.assertEqual(state.activation_rise, Fraction(3, 100))
        self.assertEqual(state.a.timestamp.minute, 30)
        self.assertEqual(state.activation_timestamp.minute, 32)
        self.assertEqual(len(state.activation_volume_participants), 1)
        self.assertEqual(state.activation_volume_participants[0].ratio, 2)

    def test_below_three_does_not_activate(self):
        self.activate("1.0299")
        self.assertIsNone(self.detector.state.setup_id)

    def test_earliest_equal_low_and_latest_equal_high(self):
        a = self.send(high="1.01", volume="200")
        self.send(high="1.04", volume="100")
        old = self.detector.state.b_origin.timestamp
        retest = self.send(high="1.04")
        self.assertEqual(self.detector.state.a.timestamp, a.timestamp)
        self.assertGreater(self.detector.state.b_origin.timestamp, old)
        self.assertEqual(self.detector.state.b_origin.timestamp, retest.timestamp)
        self.assertEqual(len(self.detector.state.origin_retests), 1)

    def test_no_trade_excluded_from_a_and_preserves_confirmation(self):
        a = self.send()
        self.send(kind=K.NO_TRADE)
        self.send(high="1.08", volume="200")
        self.assertEqual(self.detector.state.a.timestamp, a.timestamp)
        self.assertEqual(self.detector.state.traded_candle_count, 2)
        self.send(high="1.07", close="1.06")
        self.send(kind=K.NO_TRADE)
        self.send(high="1.07", close="1.06")
        self.assertEqual(self.detector.state.lifecycle, S.B_CONFIRMED)

    def test_exact_eight_and_confirmation(self):
        self.activate()
        self.assertTrue(self.detector.state.impulse_qualified)
        self.confirm()
        self.assertEqual(self.detector.state.lifecycle, S.B_CONFIRMED)
        self.assertEqual(self.detector.state.frozen_impulse_volume_average, 150)
        self.assertEqual(self.detector.state.impulse_volumes, (D(100), D(200)))

    def test_exact_one_percent_confirmation(self):
        self.activate()
        self.send(high="1.079", close="1.0692")
        self.send(high="1.079", close="1.0692")
        self.assertEqual(self.detector.state.lifecycle, S.B_CONFIRMED)

    def test_second_confirmation_insufficient_pullback_expires(self):
        self.activate()
        self.send(high="1.079", close="1.07")
        self.send(high="1.079", close="1.07")
        self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)
        self.assertEqual(self.detector.state.termination_description, "B_CONFIRMATION_PULLBACK_FAILED")

    def test_higher_and_equal_high_reset_and_recalculate(self):
        self.activate()
        self.send(high="1.07", close="1.06")
        self.send(high="1.09", volume="300")
        self.assertEqual(len(self.detector.state.confirmation_candles), 0)
        self.assertEqual(self.detector.state.impulse_volume_average, Fraction(700, 4))
        self.send(high="1.09", volume="400")
        self.assertEqual(self.detector.state.impulse_volume_average, Fraction(1100, 5))
        self.confirm()
        frozen = self.detector.state.frozen_impulse_volume_average
        self.send(high="1.10", volume="999")
        self.assertEqual(self.detector.state.frozen_impulse_volume_average, frozen)
        self.assertEqual(self.detector.state.provisional_b_price, D("1.09"))

    def deadline_setup(self):
        self.activate("1.03")
        for _ in range(7):
            self.send(high="1.03")
        self.assertEqual(self.detector.state.traded_candle_count, 9)

    def test_tenth_allowed_with_two_extra_confirmation_candles(self):
        self.deadline_setup()
        self.send(high="1.08")
        self.assertEqual(self.detector.state.traded_candle_count, 10)
        self.confirm()
        self.assertEqual(self.detector.state.lifecycle, S.B_CONFIRMED)
        self.assertEqual(self.detector.state.traded_candle_count, 12)

    def test_eleventh_cannot_rescue(self):
        self.deadline_setup()
        self.send(high="1.0799")
        old = self.detector.state.setup_id
        self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)
        self.send(high="1.08")
        self.assertIsNone(self.detector.state.setup_id)
        self.assertNotEqual(self.detector.state.setup_id, old)

    def test_no_trade_does_not_consume_impulse_slots(self):
        self.deadline_setup()
        self.send(kind=K.NO_TRADE)
        self.assertEqual(self.detector.state.traded_candle_count, 9)
        self.send(high="1.08")
        self.assertTrue(self.detector.state.impulse_qualified)

    def test_above_locked_b_invalidates(self):
        self.deadline_setup()
        self.send(high="1.08")
        self.send(high="1.0801")
        self.assertEqual(self.detector.state.termination_description, "HIGH_ABOVE_PRICE_LOCKED_B")

    def test_exact_touch_after_deadline_does_not_move_origin(self):
        self.deadline_setup()
        origin = self.send(high="1.08")
        self.send(high="1.08", close="1.06")
        self.assertEqual(self.detector.state.b_origin, origin)
        self.send(high="1.08", close="1.06")
        self.assertEqual(self.detector.state.lifecycle, S.B_CONFIRMED)

    def test_ninth_origin_can_confirm_tenth_eleventh(self):
        self.activate("1.03")
        for _ in range(6):
            self.send(high="1.03")
        self.send(high="1.08")
        self.confirm()
        self.assertEqual(self.detector.state.lifecycle, S.B_CONFIRMED)
        self.assertEqual(self.detector.state.traded_candle_count, 11)

    def test_missing_and_invalid_reset_both_histories(self):
        for kind, reason in ((K.MISSING, ReasonCode.DATA_GAP), (K.INVALID, ReasonCode.INVALID_DATA)):
            with self.subTest(kind=kind):
                self.setUp()
                self.activate()
                self.send(kind=kind)
                self.assertEqual(self.detector.state.termination_reason, reason)
                self.send()
                self.send(high="1.08", volume="1000000")
                self.assertIsNone(self.detector.state.setup_id)

    def test_dynamic_price_and_daily_change_fail(self):
        for kwargs, reason in ((dict(high="5.1", close="5.1"), ReasonCode.DYNAMIC_PRICE_ABOVE_MAX),
                               (dict(low=".9", close=".92"), ReasonCode.DYNAMIC_DAILY_CHANGE_BELOW_MIN)):
            with self.subTest(reason=reason):
                self.setUp()
                self.activate()
                self.send(**kwargs)
                self.assertEqual(self.detector.state.termination_reason, reason)

    def test_wick_above_five_does_not_fail_dynamic_close(self):
        self.activate()
        self.send(high="5.1", close="1.1")
        self.assertIsNone(self.detector.state.termination)

    def test_dynamic_recovery_does_not_reuse_old_price_structure(self):
        self.activate()
        old_id = self.detector.state.setup_id
        self.send(low=".9", close=".92")
        self.send(high="1.1")  # restoration candle excluded
        self.send(high="1.01")  # first wholly fresh A participant
        self.send(high="1.08", volume="300")
        self.assertNotEqual(self.detector.state.setup_id, old_id)
        self.assertEqual(self.detector.state.a.timestamp.minute, 34)

    def test_premarket_cannot_activate_but_supplies_volume(self):
        self.assertIsNone(self.detector.state.a)
        self.activate()
        evidence = self.detector.state.activation_volume_participants[0]
        self.assertTrue(any(item.timestamp.hour == 9 and item.timestamp.minute < 30
                            for item in evidence.preceding))

    def test_no_previous_session_price_or_volume(self):
        self.activate()
        while self.at.hour < 16:
            self.send(kind=K.NO_TRADE)
        self.at = datetime(2026, 10, 9, 9, 30, tzinfo=NEW_YORK)
        self.inputs = replace(self.inputs, share_basis_id="next-day")
        self.send(high="1.2", volume="100000")
        self.assertIsNone(self.detector.state.setup_id)
        self.send(high="1.3", volume="100000")
        self.assertIsNone(self.detector.state.setup_id)

    def test_unrepresented_gap_and_duplicate_rejected(self):
        self.at += timedelta(minutes=1)
        with self.assertRaisesRegex(ValueError, "missing minute"):
            self.send()
        self.at -= timedelta(minutes=2)
        with self.assertRaisesRegex(ValueError, "increasing"):
            self.send()

    def test_initial_highest_is_not_necessarily_trigger(self):
        # An A-origin high remains the recorded highest high; it cannot prove
        # same-candle sequential progression or be replaced by a lower high.
        self.send(high="1.12")
        self.send(high="1.08", volume="200")
        self.assertEqual(self.detector.state.provisional_b_price, D("1.12"))
        self.assertFalse(self.detector.state.impulse_qualified)
        self.assertTrue(any(event.reason == ReasonCode.INTRABAR_SEQUENCE_AMBIGUITY for event in self.events))
        self.send(high="1.12")
        self.assertTrue(self.detector.state.impulse_qualified)

    def test_same_candle_alone_cannot_activate(self):
        self.send(low="1", high="1.20", volume="300")
        self.assertIsNone(self.detector.state.setup_id)

    def test_all_zero_baseline_fails(self):
        self.detector = ABDetector("security", "TEST", Calendar(), run_id="zero", data_version="fixture")
        self.at = datetime(2026, 10, 8, 9, 10, tzinfo=NEW_YORK)
        for _ in range(20):
            self.send(kind=K.NO_TRADE)
        self.send(high="1.01", volume="100")
        self.assertTrue(any(event.reason == ReasonCode.RVOL_BASELINE_ZERO for event in self.events))
        self.send(high="1.08", volume="1")
        self.assertIsNone(self.detector.state.setup_id)

    def test_insufficient_history_cannot_activate(self):
        detector = ABDetector("security", "TEST", Calendar(), run_id="short", data_version="fixture")
        at = datetime(2026, 10, 8, 9, 30, tzinfo=NEW_YORK)
        detector.feed(candle(at), self.inputs)
        detector.feed(candle(at + timedelta(minutes=1), high="1.08", volume="99999"), self.inputs)
        self.assertIsNone(detector.state.setup_id)

    def test_mixed_zero_positive_volume_average_and_context_independence(self):
        at = datetime(2026, 10, 8, 9, 30, tzinfo=NEW_YORK)
        history = tuple(candle(at - timedelta(minutes=20-i), kind=K.NO_TRADE) for i in range(20))
        history = (candle(history[0].timestamp, volume="20000"),) + history[1:]
        with localcontext() as context:
            context.prec = 2
            evidence = volume_evidence(candle(at, volume="2000"), history, 20)
        self.assertEqual(evidence.baseline, 1000)
        self.assertEqual(evidence.ratio, 2)

    def test_input_basis_cannot_change_mid_session(self):
        self.inputs = replace(self.inputs, share_basis_id="unknown-new-basis")
        with self.assertRaisesRegex(ValueError, "share basis"):
            self.send()

    def test_a_lookback_limited_to_ten_traded_candles(self):
        self.send(low=".95", high="1.0", volume="1")
        for _ in range(10):
            self.send(low="1", high="1.01", volume="1")
        self.send(high="1.03", volume="300")
        self.assertEqual(self.detector.state.a.low, D(1))

    def test_logical_confirmation_at_activation_uses_known_final_b(self):
        self.inputs = replace(self.inputs, prior_regular_close=D(".98"))
        # Day inputs are fixed, so start an independent session replay.
        self.detector = ABDetector("security", "TEST", Calendar(), run_id="replay", data_version="fixture")
        self.at = datetime(2026, 10, 8, 9, 10, tzinfo=NEW_YORK)
        for _ in range(20):
            self.send(close="1.01")
        a = self.send(close="1.01")
        b = self.send(high="1.08", close="1", volume="200")  # close ineligible: no activation
        self.send(high="1.02", close="1.015")
        second = self.send(high="1.02", close="1.015")
        trigger = self.send(high="1.03", close="1.02")
        state = self.detector.state
        self.assertEqual(state.lifecycle, S.B_CONFIRMED)
        self.assertEqual(state.a, a)
        self.assertEqual(state.b_origin, b)
        self.assertEqual(state.activation_timestamp, trigger.end)
        self.assertEqual(state.logical_b_confirmation_timestamp, second.end)
        self.assertLess(state.logical_b_confirmation_timestamp, state.activation_timestamp)
        event = self.events[-1]
        self.assertEqual(event.recorded_at, trigger.end)
        self.assertEqual(event.modeled_event_at, second.end)

    def test_known_post_b_pullback_failure_at_activation_is_not_ignored(self):
        self.inputs = replace(self.inputs, prior_regular_close=D("1.06"))
        self.detector = ABDetector("security", "TEST", Calendar(), run_id="replay", data_version="fixture")
        self.at = datetime(2026, 10, 8, 9, 10, tzinfo=NEW_YORK)
        for _ in range(20):
            self.send()
        self.send(high="1.1", close="1.09", volume="200")
        self.send(high="1.2", close="1", volume="100")
        self.send(high="1.195", close="1.19")
        self.send(high="1.195", close="1.19")
        # A's large own high cannot be credited, and the B-candle close fails
        # eligibility. Activation on this second post-B close must still apply
        # the completed confirmation sequence's insufficient pullback.
        self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)

    def test_multiple_volume_participants_retained_without_behavior_tie_break(self):
        self.send(high="1.01", volume="200")
        self.send(high="1.08", volume="300")
        participants = self.detector.state.activation_volume_participants
        self.assertEqual(len(participants), 2)
        self.assertEqual([item.candle.volume for item in participants], [D(200), D(300)])

    def test_participant_after_b_cannot_qualify_impulse_volume(self):
        self.send(high="1.01")
        self.send(high="1.08", volume="100")  # price alone does not activate
        self.send(high="1.04", volume="300")
        self.assertIsNotNone(self.detector.state.setup_id)
        self.assertEqual(self.detector.state.provisional_b_price, D("1.08"))
        self.assertFalse(self.detector.state.impulse_qualified)

    def test_repeated_ineligible_candles_do_not_reanimate_terminated_setup(self):
        self.activate()
        self.send(low=".9", close=".92")
        self.send(low=".9", close=".92")
        self.send(low=".9", close=".92")
        self.assertFalse(self.detector.active)
        self.send(high="1.2")
        self.assertIsNone(self.detector.state.setup_id)

    def test_ordinary_termination_keeps_trustworthy_volume_context(self):
        self.activate()
        self.send(high="1.079", close="1.07")
        self.send(high="1.079", close="1.07")
        self.send(high="1.01")
        self.send(high="1.08", volume="500")
        self.assertIsNotNone(self.detector.state.setup_id)
        self.assertEqual(len(self.detector.state.activation_volume_participants[-1].preceding), 20)

    def test_session_cutoff_expires_and_early_close_is_respected(self):
        class EarlyCalendar(Calendar):
            def session_for(self, day):
                return replace(super().session_for(day), close=datetime(2026, 10, 8, 13, tzinfo=NEW_YORK))
        self.detector = ABDetector("security", "TEST", EarlyCalendar(), run_id="early", data_version="fixture")
        self.at = datetime(2026, 10, 8, 12, 7, tzinfo=NEW_YORK)
        for _ in range(20):
            self.send(low="1.1", high="1.11", close="1.1")
        self.activate()
        self.send(kind=K.NO_TRADE)
        self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)
        self.assertEqual(self.detector.state.termination_description, "NO_PERMITTED_ENTRY_REMAINS")

    def test_holiday_and_outside_session_input_rejected(self):
        class HolidayCalendar:
            def session_for(self, day):
                return None
        detector = ABDetector("security", "TEST", HolidayCalendar(), run_id="holiday", data_version="fixture")
        with self.assertRaises(ValueError):
            detector.feed(candle(self.at), self.inputs)
        outside = datetime(2026, 10, 8, 3, 59, tzinfo=NEW_YORK)
        with self.assertRaisesRegex(ValueError, "eligible regular"):
            self.detector.feed(candle(outside), self.inputs)

    def test_events_are_deterministic_and_all_volume_slots_auditable(self):
        self.activate()
        first_state, first_events = self.detector.state, tuple(self.events)
        self.setUp()
        self.activate()
        self.assertEqual(first_state, self.detector.state)
        self.assertEqual(first_events, tuple(self.events))
        evaluated = next(event for event in reversed(self.events) if event.event_type == "RVOL_EVALUATED")
        self.assertEqual(len(dict(evaluated.details)["baseline_intervals"].split("|")), 20)

    def test_previous_session_and_discontinuous_rvol_inputs_rejected(self):
        at = datetime(2026, 10, 8, 9, 30, tzinfo=NEW_YORK)
        evaluated = candle(at)
        history = (candle(at - timedelta(days=1)),)
        with self.assertRaisesRegex(ValueError, "Previous-session"):
            volume_evidence(evaluated, history, 20)
        history = (candle(at - timedelta(minutes=3)), candle(at - timedelta(minutes=1)))
        with self.assertRaisesRegex(ValueError, "missing chronological"):
            volume_evidence(evaluated, history, 20)

    def test_detector_is_precision_independent(self):
        with localcontext() as context:
            context.prec = 2
            self.activate()
            self.confirm()
        self.assertEqual(self.detector.state.lifecycle, S.B_CONFIRMED)
        self.assertEqual(self.detector.state.frozen_impulse_volume_average, Fraction(150))

    def test_cannot_omit_active_rest_of_session_by_jumping_to_next_date(self):
        self.activate()
        self.at = datetime(2026, 10, 9, 9, 30, tzinfo=NEW_YORK)
        with self.assertRaisesRegex(ValueError, "remaining active previous-session"):
            self.send()

    def test_dynamic_boundary_equalities_pass(self):
        for kwargs in (dict(high="5.1", close="5"),
                       dict(low=".9", high="1.07", close=".927")):
            with self.subTest(kwargs=kwargs):
                self.setUp()
                self.activate()
                self.send(**kwargs)
                self.assertIsNone(self.detector.state.termination)

    def test_volume_before_a_cannot_qualify_activation(self):
        self.send(low="1.1", high="1.11", close="1.1", volume="300")
        self.send(high="1.01", volume="100")
        self.send(high="1.03", volume="100")
        self.assertIsNone(self.detector.state.setup_id)

    def test_equal_high_retests_cannot_extend_tenth_candle_deadline(self):
        self.deadline_setup()
        self.send(high="1.03")
        self.assertEqual(self.detector.state.traded_candle_count, 10)
        self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)

    def test_inactive_gap_clears_premarket_volume_continuity(self):
        self.send(kind=K.MISSING)
        self.send()
        self.send(high="1.08", volume="99999")
        self.assertIsNone(self.detector.state.setup_id)


if __name__ == "__main__":
    unittest.main()
