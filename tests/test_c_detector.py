"""Offline C fixtures with explicit post-origin candles and exact boundaries."""

import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D, localcontext
from fractions import Fraction

from tradingbot_backtest.ab_detector import EligibilityInputs
from tradingbot_backtest.c_detector import CDetector, consolidation_metrics
from tradingbot_backtest.codes import ReasonCode, SetupTermination
from tradingbot_backtest.market import IntervalClassification as K, MarketInterval
from tradingbot_backtest.sessions import NEW_YORK, TradingSession
from tradingbot_backtest.states import StrategyState as S


class Calendar:
    def session_for(self, day):
        return TradingSession(day, datetime(day.year, day.month, day.day, 9, 30, tzinfo=NEW_YORK),
                              datetime(day.year, day.month, day.day, 16, tzinfo=NEW_YORK), "fixture")


def candle(at, low="1", high="1.01", close="1", volume="100", kind=K.TRADED):
    fields = dict(security_id="security", ticker="TEST", timestamp=at,
                  classification=kind, source_id="fixture")
    if kind == K.TRADED:
        fields.update(open=D(close), low=D(low), high=D(high), close=D(close), volume=D(volume))
    elif kind == K.NO_TRADE:
        fields.update(volume=D(0), no_trade_verified=True)
    else:
        fields.update(data_quality_reason="fixture gap")
    return MarketInterval(**fields)


class CTests(unittest.TestCase):
    def setUp(self):
        self.detector = CDetector("security", "TEST", Calendar(), run_id="c-run", data_version="fixture")
        self.inputs = EligibilityInputs(D(".90"), True, "fixture-basis")
        self.at = datetime(2026, 10, 8, 9, 10, tzinfo=NEW_YORK)
        self.events = []
        for _ in range(20):
            self.send()

    def send(self, **kwargs):
        bar = candle(self.at, **kwargs)
        self.events.extend(self.detector.feed(bar, self.inputs))
        self.at += timedelta(minutes=1)
        return bar

    def b(self):
        self.send()
        return self.send(high="1.10", close="1.10", volume="200")

    def pullback(self, low="1.06", volume="100"):
        return self.send(low=low, high="1.09", close="1.08", volume=volume)

    def consolidation(self, low="1.07", volume="100", high="1.09", close="1.08"):
        return self.send(low=low, high=high, close=close, volume=volume)

    def developing(self, second_volume="100"):
        self.b()
        self.pullback()
        self.consolidation(volume=second_volume)
        self.assertEqual(self.detector.state.lifecycle, S.C_DEVELOPING)

    def locked(self):
        self.developing()
        self.consolidation()
        self.consolidation()
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)

    def test_b_origin_is_not_c_and_confirmation_candles_count(self):
        origin = self.b()
        self.assertEqual(self.detector.c_state.b_origin, origin)
        self.assertEqual(self.detector.c_state.development_count, 0)
        c = self.pullback()
        self.assertEqual(self.detector.state.lifecycle, S.PROVISIONAL_B)
        self.assertEqual(self.detector.c_state.provisional_origin, c)
        self.assertEqual(self.detector.c_state.development_count, 1)
        self.assertIsNone(self.detector.c_state.lock_timestamp)
        self.consolidation()
        self.assertEqual(self.detector.state.lifecycle, S.C_DEVELOPING)
        self.assertEqual(self.detector.c_state.development_count, 2)

    def test_higher_b_discards_old_potential_c_and_own_low(self):
        self.b()
        old_c = self.pullback()
        new_b = self.send(low="1.02", high="1.12", close="1.10", volume="250")
        self.assertEqual(self.detector.state.b_origin, new_b)
        self.assertIsNone(self.detector.c_state.provisional_origin)
        self.assertEqual(self.detector.c_state.development_count, 0)
        self.send(low="1.08", high="1.11", close="1.10")
        self.send(low="1.09", high="1.11", close="1.10")
        self.assertEqual(self.detector.c_state.development_count, 2)
        self.assertNotIn(old_c, self.detector.c_state.development_candles)
        self.assertEqual(self.detector.state.lifecycle, S.C_DEVELOPING)

    def test_equal_b_retest_restarts_c(self):
        self.b()
        self.pullback()
        new_b = self.send(low="1.02", high="1.10", close="1.08")
        self.assertEqual(self.detector.c_state.b_origin, new_b)
        self.assertEqual(self.detector.c_state.development_count, 0)
        self.assertEqual(self.detector.c_state.higher_low_candles, ())
        self.assertEqual(self.detector.c_state.consolidation_candles, ())

    def test_bad_potential_c_is_discarded_when_b_legally_moves(self):
        self.b()
        self.pullback(low="1.05")
        self.assertIsNotNone(self.detector.c_state.potential_failure)
        self.send(low="1.02", high="1.12", close="1.10")
        self.assertIsNone(self.detector.c_state.potential_failure)
        self.assertEqual(self.detector.state.lifecycle, S.PROVISIONAL_B)

    def test_b_confirmation_failure_discards_potential_c(self):
        self.b()
        self.send(low="1.06", high="1.099", close="1.095")
        self.send(low="1.07", high="1.099", close="1.095")
        self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)
        self.assertIsNone(self.detector.c_state.b_origin)
        self.assertTrue(any(e.event_type == "POTENTIAL_C_DISCARDED" for e in self.events))

    def test_exact_twenty_percent_qualifies(self):
        self.b()
        self.pullback(low="1.08")
        self.assertEqual(self.detector.c_state.retracement, Fraction(1, 5))
        self.assertEqual(self.detector.c_state.provisional_origin.low, D("1.08"))

    def test_below_twenty_percent_waits_without_invalidating(self):
        self.b()
        self.consolidation(low="1.085", close="1.088")
        self.consolidation(low="1.085", close="1.088")
        self.assertEqual(self.detector.state.lifecycle, S.C_DEVELOPING)
        self.assertIsNone(self.detector.c_state.provisional_origin)
        self.pullback(low="1.08")
        self.assertEqual(self.detector.c_state.retracement, Fraction(1, 5))

    def test_just_below_fifty_percent_qualifies(self):
        self.b()
        self.pullback(low="1.050001")
        self.consolidation()
        self.assertEqual(self.detector.state.lifecycle, S.C_DEVELOPING)
        self.assertLess(self.detector.c_state.retracement, Fraction(1, 2))

    def test_exact_fifty_percent_fails_when_b_confirms(self):
        self.b()
        self.pullback(low="1.05")
        self.assertEqual(self.detector.state.lifecycle, S.PROVISIONAL_B)
        self.consolidation()
        self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)
        self.assertEqual(self.detector.state.termination_description, "C_RETRACEMENT_AT_OR_BELOW_MIDPOINT")

    def test_below_midpoint_and_at_or_below_a_fail(self):
        for low in ("1.0499", "1", ".99"):
            with self.subTest(low=low):
                self.setUp()
                self.b()
                self.pullback(low=low)
                self.consolidation()
                self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)

    def test_same_c_origin_cannot_supply_higher_low_or_consolidation(self):
        self.b()
        self.pullback()
        self.assertEqual(self.detector.c_state.higher_low_candles, ())
        self.assertEqual(self.detector.c_state.consolidation_candles, ())

    def test_subsequent_higher_low_is_recorded(self):
        self.b()
        self.pullback()
        higher = self.consolidation()
        self.assertEqual(self.detector.c_state.higher_low_candles, (higher,))

    def test_equal_low_is_not_higher_and_does_not_move_c(self):
        self.b()
        origin = self.pullback()
        self.consolidation(low="1.06")
        self.consolidation(low="1.06")
        self.consolidation(low="1.06")
        self.assertEqual(self.detector.c_state.provisional_origin, origin)
        self.assertEqual(self.detector.c_state.higher_low_candles, ())
        self.assertEqual(self.detector.state.lifecycle, S.C_DEVELOPING)
        self.consolidation()
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)

    def test_lower_c_resets_progress_without_restarting_clock(self):
        self.developing()
        previous_count = self.detector.c_state.development_count
        origin = self.pullback(low="1.055")
        c = self.detector.c_state
        self.assertEqual(c.provisional_origin, origin)
        self.assertEqual(c.development_count, previous_count + 1)
        self.assertEqual(c.higher_low_candles, ())
        self.assertEqual(c.consolidation_candles, ())
        for _ in range(3):
            self.consolidation(low="1.06")
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)
        self.assertEqual(self.detector.c_state.locked_price, D("1.055"))

    def test_no_trade_cannot_update_c_or_supply_evidence(self):
        self.developing()
        previous = self.detector.c_state
        self.send(kind=K.NO_TRADE)
        self.assertEqual(self.detector.c_state, previous)

    def test_three_subsequent_consolidation_candles_required(self):
        self.developing()
        self.consolidation()
        self.assertEqual(len(self.detector.c_state.consolidation_candles), 2)
        self.assertEqual(self.detector.state.lifecycle, S.C_DEVELOPING)
        self.consolidation()
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)

    def test_no_trade_between_consolidation_does_not_count(self):
        self.developing()
        self.send(kind=K.NO_TRADE)
        self.consolidation()
        self.send(kind=K.NO_TRADE)
        self.assertEqual(self.detector.c_state.development_count, 3)
        self.assertEqual(self.detector.state.lifecycle, S.C_DEVELOPING)
        self.consolidation()
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)

    def test_range_exact_fifty_percent_passes_numeric_rule(self):
        bars = tuple(candle(self.at + timedelta(minutes=i), low="1.05", high="1.10", close="1.08")
                     for i in range(3))
        metrics = consolidation_metrics(bars, bars, Fraction(1, 10), Fraction(150), Fraction(1, 2))
        self.assertEqual(metrics.price_range, Fraction(1, 20))
        self.assertTrue(metrics.range_passed)

    def test_range_above_limit_fails_numeric_rule(self):
        bars = tuple(candle(self.at + timedelta(minutes=i), low="1.05", high="1.100001", close="1.08")
                     for i in range(3))
        metrics = consolidation_metrics(bars, bars, Fraction(1, 10), Fraction(150), Fraction(1, 2))
        self.assertFalse(metrics.range_passed)

    def test_volume_strictly_below_passes(self):
        self.locked()
        self.assertEqual(self.detector.c_state.lock_evidence.metrics.average_volume, 100)
        self.assertTrue(self.detector.c_state.lock_evidence.metrics.volume_passed)

    def test_volume_equal_and_above_do_not_lock(self):
        for volume in ("150", "151"):
            with self.subTest(volume=volume):
                self.setUp()
                self.developing(second_volume=volume)
                self.consolidation(volume=volume)
                self.consolidation(volume=volume)
                self.assertEqual(self.detector.state.lifecycle, S.C_DEVELOPING)
                evaluated = [e for e in self.events if e.event_type == "C_CONSOLIDATION_EVALUATED"][-1]
                self.assertFalse(dict(evaluated.details)["volume_passed"])

    def test_frozen_impulse_average_is_never_recalculated_during_c(self):
        self.developing()
        frozen = self.detector.state.frozen_impulse_volume_average
        self.consolidation(volume="10000")
        self.assertEqual(self.detector.state.frozen_impulse_volume_average, frozen)
        self.assertEqual(self.detector.state.impulse_volume_average, frozen)

    def test_valid_higher_consolidation_low_need_not_itself_retrace_twenty_percent(self):
        self.developing()
        self.consolidation(low="1.085", high="1.095", close="1.09")
        self.consolidation(low="1.085", high="1.095", close="1.09")
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)

    def test_tenth_candle_can_complete_lock(self):
        self.developing(second_volume="300")
        for _ in range(5):
            self.consolidation(volume="300")
        self.assertEqual(self.detector.c_state.development_count, 7)
        self.consolidation()
        self.consolidation()
        self.assertEqual(self.detector.state.lifecycle, S.C_DEVELOPING)
        self.send(kind=K.NO_TRADE)
        self.consolidation()
        self.assertEqual(self.detector.c_state.development_count, 10)
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)

    def test_eleventh_cannot_rescue_expired_c(self):
        self.developing(second_volume="150")
        for _ in range(8):
            self.consolidation(volume="150")
        self.assertEqual(self.detector.c_state.development_count, 10)
        self.assertEqual(self.detector.state.termination, SetupTermination.EXPIRED)
        self.assertEqual(self.detector.state.termination_description, "C_DEVELOPMENT_WINDOW_EXHAUSTED")
        self.consolidation()
        self.assertNotEqual(self.detector.state.lifecycle, S.C_LOCKED)

    def test_shallow_pullback_expires_at_tenth_traded_candle(self):
        self.b()
        for _ in range(10):
            self.consolidation(low="1.085", close="1.088")
        self.assertEqual(self.detector.state.termination, SetupTermination.EXPIRED)
        self.assertIsNone(self.detector.c_state.provisional_origin)

    def test_early_high_above_b_invalidates_not_d(self):
        self.developing()
        self.consolidation(high="1.100001")
        self.assertEqual(self.detector.state.termination_description, "EARLY_BREAK_ABOVE_B")
        self.assertFalse(any("D_CONFIRMED" == e.event_type for e in self.events))

    def test_touch_of_b_during_c_is_allowed(self):
        self.developing()
        self.consolidation(high="1.10")
        self.consolidation(high="1.10")
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)

    def test_early_break_beats_would_be_lock(self):
        self.developing()
        self.consolidation()
        self.consolidation(high="1.11")
        self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)
        self.assertIsNone(self.detector.c_state.lock_evidence)

    def test_missing_and_invalid_terminate_c_without_bridging(self):
        for kind, reason in ((K.MISSING, ReasonCode.DATA_GAP), (K.INVALID, ReasonCode.INVALID_DATA)):
            with self.subTest(kind=kind):
                self.setUp()
                self.developing()
                self.send(kind=kind)
                self.assertEqual(self.detector.state.termination_reason, reason)
                self.consolidation()
                self.assertIsNone(self.detector.state.setup_id)

    def test_dynamic_price_failure_terminates(self):
        self.developing()
        self.consolidation(high="5.1", close="5.01")
        self.assertEqual(self.detector.state.termination_reason, ReasonCode.DYNAMIC_PRICE_ABOVE_MAX)

    def test_dynamic_gain_failure_terminates(self):
        self.developing()
        self.send(low=".9", high="1.09", close=".92")
        self.assertEqual(self.detector.state.termination_reason, ReasonCode.DYNAMIC_DAILY_CHANGE_BELOW_MIN)

    def test_wick_above_five_below_b_does_not_fail_dynamic_gate(self):
        self.send(low="4", high="4.01", close="4")
        self.send(low="4", high="5.2", close="4.8", volume="200")
        self.send(low="4.8", high="5.1", close="4.9")
        self.send(low="4.85", high="5.1", close="4.9")
        self.send(low="4.86", high="5.15", close="4.9")
        self.assertEqual(self.detector.state.lifecycle, S.C_DEVELOPING)
        self.assertIsNone(self.detector.state.termination)

    def test_lock_preserves_all_supporting_evidence(self):
        self.locked()
        c = self.detector.c_state
        evidence = c.lock_evidence
        self.assertEqual(c.locked_price, D("1.06"))
        self.assertEqual(c.locked_origin, evidence.c_origin)
        self.assertEqual(c.lock_timestamp, self.at)
        self.assertEqual(len(evidence.development_candles), 4)
        self.assertEqual(len(evidence.consolidation_candles), 3)
        self.assertEqual(len(evidence.volume_candles), 3)
        self.assertEqual(evidence.frozen_impulse_average, 150)
        self.assertEqual(evidence.retracement, Fraction(2, 5))

    def test_locked_c_touch_and_no_trade_preserve_frozen_snapshot(self):
        self.locked()
        frozen = self.detector.c_state
        self.consolidation(low="1.06")
        self.send(kind=K.NO_TRADE)
        self.assertEqual(self.detector.c_state, frozen)
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)

    def test_below_locked_c_invalidates(self):
        self.locked()
        self.pullback(low="1.0599")
        self.assertEqual(self.detector.state.termination_description, "PRICE_BELOW_LOCKED_C")
        self.assertEqual(self.detector.c_state.locked_price, D("1.06"))

    def test_locked_c_break_and_above_b_is_conservative_invalidation(self):
        self.locked()
        self.consolidation(low="1.05", high="1.11")
        self.assertEqual(self.detector.state.termination_reason, ReasonCode.C_INVALIDATION_INTRABAR_AMBIGUITY)
        self.assertTrue(any(e.reason == ReasonCode.INTRABAR_SEQUENCE_AMBIGUITY for e in self.events))

    def test_above_b_after_lock_does_not_generate_d_or_entry(self):
        self.locked()
        self.consolidation(high="1.12")
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)
        self.assertFalse(any(e.event_type in ("D_CONFIRMED", "BREAKOUT_ATTEMPT", "ENTRY") for e in self.events))

    def test_premarket_never_supplies_c_evidence(self):
        self.assertEqual(self.detector.c_state.development_count, 0)
        self.assertIsNone(self.detector.c_state.b_origin)
        self.assertFalse(any(e.event_type.startswith("C_") for e in self.events))

    def test_no_c_state_carries_overnight(self):
        self.locked()
        while self.at.hour < 16:
            self.send(kind=K.NO_TRADE)
        self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)
        self.at = datetime(2026, 10, 9, 9, 10, tzinfo=NEW_YORK)
        self.send()
        self.assertIsNone(self.detector.c_state.locked_price)
        self.assertEqual(self.detector.c_state.development_count, 0)

    def replay_prefix(self):
        self.inputs = replace(self.inputs, prior_regular_close=D("1.37"))
        self.detector = CDetector("security", "TEST", Calendar(), run_id="replay-c", data_version="fixture")
        self.at = datetime(2026, 10, 8, 9, 10, tzinfo=NEW_YORK)
        for _ in range(20):
            self.send()
        self.send(low="1", high="1.42", close="1.42", volume="300")
        self.send(low="1", high="1.50", close="1.38", volume="200")
        self.send(low="1.30", high="1.40", close="1.39")
        self.send(low="1.31", high="1.40", close="1.39")
        self.send(low="1.31", high="1.40", close="1.39")

    def test_initial_replay_can_lock_only_at_activation_availability(self):
        self.replay_prefix()
        trigger = self.send(low="1.31", high="1.45", close="1.42")
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)
        self.assertLess(self.detector.state.logical_b_confirmation_timestamp, trigger.end)
        self.assertEqual(self.detector.c_state.lock_timestamp, trigger.end)
        self.assertEqual(self.detector.c_state.development_count, 4)

    def test_replay_does_not_lock_before_later_known_lower_c(self):
        self.replay_prefix()
        self.send(low="1.31", high="1.40", close="1.39")
        trigger = self.send(low="1.28", high="1.45", close="1.42")
        self.assertEqual(self.detector.state.lifecycle, S.C_DEVELOPING)
        self.assertEqual(self.detector.c_state.provisional_origin, trigger)
        self.assertEqual(self.detector.c_state.development_count, 5)
        self.assertEqual(self.detector.c_state.consolidation_candles, ())
        self.assertIsNone(self.detector.c_state.lock_timestamp)

    def test_activation_replay_permanent_invalidation_cannot_be_reversed(self):
        self.replay_prefix()
        invalidating = self.send(low="1.24", high="1.40", close="1.39")
        later_history = self.send(low="1.31", high="1.40", close="1.39")
        self.assertIsNone(self.detector.state.setup_id)
        trigger = self.send(low="1.31", high="1.45", close="1.42")
        self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)
        self.assertEqual(self.detector.state.termination, SetupTermination.INVALIDATED)
        self.assertEqual(self.detector.state.termination_description,
                         "C_RETRACEMENT_AT_OR_BELOW_MIDPOINT")
        self.assertLess(self.detector.state.logical_b_confirmation_timestamp, invalidating.end)
        self.assertEqual(self.detector.state.termination_timestamp, trigger.end)
        self.assertIsNone(self.detector.c_state.lock_timestamp)
        self.assertIsNone(self.detector.c_state.lock_evidence)
        self.assertIn(later_history, self.detector.c_state.development_candles)
        self.assertIn(trigger, self.detector.c_state.development_candles)
        violations = [event for event in self.events
                      if event.event_type == "C_INVALIDATING_EVIDENCE"]
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].interval_start, invalidating.timestamp)
        self.assertEqual(violations[0].recorded_at, trigger.end)
        self.assertFalse(any(event.event_type == "C_LOCKED" for event in self.events))

    def test_activation_replay_audits_separate_evidence_and_availability(self):
        self.replay_prefix()
        self.assertIsNone(self.detector.state.setup_id)
        self.assertFalse(any(event.event_type == "C_LOCKED" for event in self.events))
        first_c_timestamp = datetime(2026, 10, 8, 9, 32, tzinfo=NEW_YORK)
        trigger = self.send(low="1.31", high="1.45", close="1.42")
        established = [event for event in self.events
                       if event.event_type == "POTENTIAL_C_ESTABLISHED"]
        self.assertEqual(len(established), 1)
        self.assertEqual(established[0].interval_start, first_c_timestamp)
        self.assertEqual(established[0].modeled_event_at,
                         first_c_timestamp + timedelta(minutes=1))
        self.assertEqual(established[0].recorded_at, trigger.end)
        self.assertLess(established[0].modeled_event_at, established[0].recorded_at)
        locks = [event for event in self.events if event.event_type == "C_LOCKED"]
        self.assertEqual(len(locks), 1)
        self.assertEqual(locks[0].recorded_at, trigger.end)
        self.assertEqual(locks[0].modeled_event_at, trigger.end)
        self.assertEqual(self.detector.c_state.lock_timestamp, trigger.end)
        self.assertEqual(self.detector.c_state.locked_origin.timestamp, first_c_timestamp)
        self.assertTrue(all(event.modeled_event_at <= event.recorded_at
                            for event in self.events))
        self.assertTrue(all(bar.end <= trigger.end
                            for bar in self.detector.c_state.lock_evidence.development_candles))

    def test_exact_arithmetic_is_independent_of_decimal_context(self):
        with localcontext() as context:
            context.prec = 2
            self.locked()
        self.assertEqual(self.detector.c_state.retracement, Fraction(2, 5))
        self.assertEqual(self.detector.c_state.lock_evidence.metrics.price_range, Fraction(1, 50))

    def test_audits_and_snapshots_are_deterministic(self):
        self.locked()
        state, c_state, events = self.detector.state, self.detector.c_state, tuple(self.events)
        self.setUp()
        self.locked()
        self.assertEqual(state, self.detector.state)
        self.assertEqual(c_state, self.detector.c_state)
        self.assertEqual(events, tuple(self.events))

    def test_complete_potential_consolidation_cannot_lock_unconfirmed_b(self):
        self.send()
        self.send(high="1.03", volume="200")
        self.send(low="1.02", high="1.025", close="1.023", volume="50")
        for _ in range(3):
            self.send(low="1.021", high="1.026", close="1.023", volume="50")
        self.assertEqual(len(self.detector.c_state.consolidation_candles), 3)
        self.assertEqual(self.detector.state.lifecycle, S.AB_IMPULSE)
        self.assertIsNone(self.detector.c_state.lock_timestamp)

    def test_lower_low_at_tenth_candle_cannot_restart_window(self):
        self.developing(second_volume="150")
        for _ in range(7):
            self.consolidation(volume="150")
        self.pullback(low="1.055")
        self.assertEqual(self.detector.c_state.development_count, 10)
        self.assertEqual(self.detector.c_state.consolidation_candles, ())
        self.assertEqual(self.detector.state.termination, SetupTermination.EXPIRED)

    def test_required_higher_low_is_present_in_evaluated_three_bar_window(self):
        self.developing(second_volume="300")
        self.consolidation(low="1.06", volume="300")
        self.consolidation(low="1.06", volume="300")
        for _ in range(3):
            self.consolidation(low="1.06")
        self.assertTrue(self.detector.c_state.higher_low_candles)
        self.assertEqual(self.detector.state.lifecycle, S.C_DEVELOPING)
        latest = [event for event in self.events if event.event_type == "C_CONSOLIDATION_EVALUATED"][-1]
        self.assertTrue(dict(latest.details)["volume_passed"])
        self.assertFalse(dict(latest.details)["higher_low_passed"])
        self.consolidation()
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)

    def test_missing_or_invalid_after_lock_never_creates_price_evidence(self):
        for kind in (K.MISSING, K.INVALID):
            with self.subTest(kind=kind):
                self.setUp()
                self.locked()
                frozen = self.detector.c_state
                self.send(kind=kind)
                self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)
                self.assertEqual(self.detector.c_state, frozen)

    def test_dynamic_failure_after_lock_still_terminates(self):
        self.locked()
        self.send(low=".9", high="1.09", close=".92")
        self.assertEqual(self.detector.state.termination_reason, ReasonCode.DYNAMIC_DAILY_CHANGE_BELOW_MIN)

    def test_new_setup_does_not_reuse_terminated_c(self):
        self.developing()
        old_id = self.detector.state.setup_id
        self.consolidation(high="1.11")
        self.send(low="1.06", high="1.07", close="1.06")
        self.send(low="1.06", high="1.15", close="1.14", volume="500")
        self.assertNotEqual(self.detector.state.setup_id, old_id)
        self.assertEqual(self.detector.c_state.development_count, 0)
        self.assertIsNone(self.detector.c_state.provisional_origin)

    def test_early_close_expires_locked_c_at_session_specific_cutoff(self):
        class EarlyCalendar(Calendar):
            def session_for(self, day):
                return replace(super().session_for(day), close=datetime(2026, 10, 8, 13, tzinfo=NEW_YORK))
        self.detector = CDetector("security", "TEST", EarlyCalendar(), run_id="early-c", data_version="fixture")
        self.at = datetime(2026, 10, 8, 12, 2, tzinfo=NEW_YORK)
        for _ in range(20):
            self.send(low="1.1", high="1.11", close="1.1")
        self.locked()
        self.send(kind=K.NO_TRADE)
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)
        self.send(kind=K.NO_TRADE)
        self.assertEqual(self.detector.state.termination_description, "NO_PERMITTED_ENTRY_REMAINS")

    def test_metrics_reject_no_trade_as_price_or_volume_consolidation_evidence(self):
        bars = (candle(self.at, kind=K.NO_TRADE),)
        with self.assertRaises(ValueError):
            consolidation_metrics(bars, bars, Fraction(1, 10), Fraction(150), Fraction(1, 2))


if __name__ == "__main__":
    unittest.main()
