"""Offline breakout/signal fixtures; no entry or account simulation."""

import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D, localcontext
from fractions import Fraction

from tradingbot_backtest.ab_detector import EligibilityInputs
from tradingbot_backtest.codes import ReasonCode, SetupTermination
from tradingbot_backtest.d_detector import DDetector
from tradingbot_backtest.market import IntervalClassification as K, MarketInterval
from tradingbot_backtest.sessions import NEW_YORK, TradingSession
from tradingbot_backtest.states import StrategyState as S


class Calendar:
    def __init__(self, close_hour=16):
        self.close_hour = close_hour

    def session_for(self, day):
        return TradingSession(day, datetime(day.year, day.month, day.day, 9, 30, tzinfo=NEW_YORK),
                              datetime(day.year, day.month, day.day, self.close_hour, tzinfo=NEW_YORK),
                              'fixture')


def candle(at, *, low='1.07', high='1.09', close='1.08', volume='100', kind=K.TRADED, opening=None,
           data_quality_reason='fixture gap'):
    fields = dict(security_id='security', ticker='TEST', timestamp=at,
                  classification=kind, source_id='fixture')
    if kind == K.TRADED:
        fields.update(open=D(opening if opening is not None else close), low=D(low),
                      high=D(high), close=D(close), volume=D(volume))
    elif kind == K.NO_TRADE:
        fields.update(volume=D(0), no_trade_verified=True)
    else:
        fields.update(data_quality_reason=data_quality_reason)
    return MarketInterval(**fields)


class DTests(unittest.TestCase):
    def setUp(self, premarket_volume='100'):
        self.calendar = Calendar()
        self.inputs = EligibilityInputs(D('.90'), True, 'fixture-basis')
        self.detector = DDetector('security', 'TEST', self.calendar,
                                  run_id='d-run', data_version='fixture')
        self.at = datetime(2026, 10, 8, 9, 10, tzinfo=NEW_YORK)
        self.events = []
        for _ in range(20):
            self.send(low='1', high='1.01', close='1', volume=premarket_volume)
        self.send(low='1', high='1.01', close='1')
        self.send(low='1', high='1.10', close='1.10', volume='200')
        self.send(low='1.06', high='1.09', close='1.08')
        for _ in range(3):
            self.send()
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)
        self.assertEqual(self.detector.c_state.locked_price, D('1.06'))

    def send(self, **kwargs):
        bar = candle(self.at, **kwargs)
        self.events.extend(self.detector.feed(bar, self.inputs))
        self.at += timedelta(minutes=1)
        return bar

    def attempt(self, close='1.105', volume='200', low='1.07', high='1.13'):
        return self.send(low=low, high=high, close=close, volume=volume)

    def test_valid_d_schedules_once_for_next_interval_not_d_candle(self):
        bar = self.attempt()
        signal = self.detector.d_state.signal
        self.assertEqual(self.detector.state.lifecycle, S.PENDING_ENTRY)
        self.assertEqual(signal.d, bar)
        self.assertEqual(signal.d_confirmation_timestamp, bar.end)
        self.assertEqual(signal.scheduled_interval, bar.end)
        self.assertGreater(signal.scheduled_interval, bar.timestamp)
        self.assertEqual(signal.a.low, D('1'))
        self.assertEqual(signal.b_price, D('1.10'))
        self.assertEqual(signal.c_price, D('1.06'))
        self.assertEqual(signal.c_evidence, self.detector.c_state.lock_evidence)
        self.assertEqual(sum(e.event_type == 'D_CONFIRMED' for e in self.events), 1)
        self.assertIsNone(self.detector.d_state.handoff)

    def test_first_attempt_at_wait_minute_ten_allowed(self):
        for _ in range(9):
            self.send(kind=K.NO_TRADE)
        self.attempt()
        self.assertEqual(self.detector.d_state.wait_elapsed_minutes, 10)
        self.assertEqual(self.detector.state.lifecycle, S.PENDING_ENTRY)

    def test_wait_expires_at_end_of_tenth_minute(self):
        for _ in range(9):
            self.send()
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)
        final = self.send()
        self.assertEqual(self.detector.d_state.wait_elapsed_minutes, 10)
        self.assertEqual(self.detector.state.termination_timestamp, final.end)
        self.assertEqual(self.detector.state.termination_description, 'LOCKED_C_WAIT_EXHAUSTED')

    def test_no_trade_consumes_wait_without_price_evidence(self):
        for _ in range(10):
            self.send(kind=K.NO_TRADE)
        self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)
        self.assertEqual(self.detector.d_state.attempts, ())
        self.assertEqual(self.detector.d_state.wait_elapsed_minutes, 10)

    def test_touching_b_does_not_attempt_or_reset_wait(self):
        self.send(high='1.10')
        self.assertEqual(self.detector.d_state.attempts, ())
        self.assertEqual(self.detector.d_state.wait_elapsed_minutes, 1)
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)

    def test_strictly_above_b_is_attempt_even_with_wick_only_close(self):
        bar = self.attempt(close='1.10')
        self.assertEqual(self.detector.state.lifecycle, S.BREAKOUT_ATTEMPT)
        self.assertEqual(self.detector.d_state.attempts[0].candle, bar)
        self.assertIn('D_CLOSE_BUFFER_FAILED', self.detector.d_state.attempts[0].failures)

    def test_first_two_failures_preserve_b_c_and_allow_third_valid(self):
        before = self.detector.c_state
        self.attempt(close='1.10')
        self.attempt(close='1.10')
        self.assertEqual(self.detector.c_state, before)
        self.assertEqual(self.detector.state.provisional_b_price, D('1.10'))
        self.attempt(volume='400')
        self.assertEqual(len(self.detector.d_state.attempts), 3)
        self.assertEqual(self.detector.state.lifecycle, S.PENDING_ENTRY)

    def test_third_failed_attempt_expires_and_fourth_impossible(self):
        for _ in range(3):
            self.attempt(close='1.10')
        self.assertEqual(self.detector.state.termination_description, 'BREAKOUT_ATTEMPT_LIMIT_EXHAUSTED')
        setup = self.detector.state.setup_id
        attempts = self.detector.d_state.attempts
        self.attempt()
        self.assertNotEqual(self.detector.state.setup_id, setup)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(self.detector.d_state.attempts, ())

    def test_first_attempt_counts_as_resolution_minute_one(self):
        bar = self.attempt(close='1.10')
        self.assertEqual(self.detector.d_state.first_attempt_timestamp, bar.timestamp)
        self.assertEqual(self.detector.d_state.resolution_elapsed_minutes, 1)

    def test_resolution_no_trade_expires_at_fifth_minute(self):
        self.attempt(close='1.10')
        for _ in range(3):
            self.send(kind=K.NO_TRADE)
        self.assertEqual(self.detector.state.lifecycle, S.BREAKOUT_ATTEMPT)
        last = self.send(kind=K.NO_TRADE)
        self.assertEqual(self.detector.state.termination_timestamp, last.end)
        self.assertEqual(self.detector.d_state.resolution_elapsed_minutes, 5)
        self.assertEqual(self.detector.state.termination_description, 'BREAKOUT_RESOLUTION_WINDOW_EXHAUSTED')

    def test_retry_does_not_reset_resolution(self):
        self.attempt(close='1.10')
        self.send(kind=K.NO_TRADE)
        self.attempt(close='1.10')
        self.assertEqual(self.detector.d_state.resolution_elapsed_minutes, 3)
        self.send(kind=K.NO_TRADE)
        self.send()
        self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)

    def test_fifth_resolution_minute_can_confirm(self):
        self.attempt(close='1.10')
        for _ in range(3):
            self.send(kind=K.NO_TRADE)
        self.attempt()
        self.assertEqual(self.detector.d_state.resolution_elapsed_minutes, 5)
        self.assertEqual(self.detector.state.lifecycle, S.PENDING_ENTRY)

    def test_first_attempt_stops_wait_clock(self):
        for _ in range(9):
            self.send(kind=K.NO_TRADE)
        self.attempt(close='1.10')
        self.send(kind=K.NO_TRADE)
        self.assertEqual(self.detector.state.lifecycle, S.BREAKOUT_ATTEMPT)
        self.assertEqual(self.detector.d_state.wait_elapsed_minutes, 10)
        self.assertEqual(self.detector.d_state.resolution_elapsed_minutes, 2)

    def test_exact_quarter_percent_close_passes(self):
        self.attempt(close='1.10275')
        self.assertEqual(self.detector.state.lifecycle, S.PENDING_ENTRY)
        self.assertEqual(self.detector.d_state.attempts[0].close_extension, Fraction(1, 400))

    def test_below_quarter_percent_fails(self):
        self.attempt(close='1.102749')
        self.assertEqual(self.detector.state.lifecycle, S.BREAKOUT_ATTEMPT)

    def test_exact_two_percent_passes(self):
        self.attempt(close='1.122')
        self.assertEqual(self.detector.state.lifecycle, S.PENDING_ENTRY)

    def test_above_two_percent_expires_immediately(self):
        self.attempt(close='1.122001', volume='1')
        self.assertEqual(self.detector.state.termination_description, 'D_CLOSE_OVEREXTENDED')
        self.assertEqual(self.detector.state.termination, SetupTermination.EXPIRED)

    def test_exact_one_point_five_rvol_passes(self):
        # Previous 20 volumes are nineteen 100s and one 200: mean 105.
        self.attempt(volume='157.5')
        a = self.detector.d_state.attempts[0]
        self.assertEqual(a.previous_20.baseline, Fraction(105))
        self.assertEqual(a.previous_20.ratio, Fraction(3, 2))
        self.assertTrue(a.rvol_passed)
        self.assertEqual(self.detector.state.lifecycle, S.PENDING_ENTRY)

    def test_below_one_point_five_rvol_fails(self):
        self.attempt(volume='157.499')
        self.assertFalse(self.detector.d_state.attempts[0].rvol_passed)
        self.assertEqual(self.detector.state.lifecycle, S.BREAKOUT_ATTEMPT)

    def test_local_volume_equality_fails_independently(self):
        # Verified no-trades lower the 20 baseline while latest three are 100.
        self.setUp(premarket_volume='0')
        for _ in range(6):
            self.send(kind=K.NO_TRADE)
        for _ in range(3):
            self.send()
        self.attempt(volume='100')
        a = self.detector.d_state.attempts[0]
        self.assertTrue(a.rvol_passed)
        self.assertEqual(a.local_average, Fraction(100))
        self.assertFalse(a.local_volume_passed)
        self.assertEqual(self.detector.state.lifecycle, S.BREAKOUT_ATTEMPT)

    def test_local_volume_strictly_greater_passes(self):
        self.setUp(premarket_volume='0')
        for _ in range(6):
            self.send(kind=K.NO_TRADE)
        for _ in range(3):
            self.send()
        self.attempt(volume='100.001')
        self.assertTrue(self.detector.d_state.attempts[0].local_volume_passed)
        self.assertEqual(self.detector.state.lifecycle, S.PENDING_ENTRY)

    def test_no_trade_occupies_local_three_without_skipping(self):
        self.send(volume='120')
        self.send(kind=K.NO_TRADE)
        self.send(volume='60')
        self.attempt()
        a = self.detector.d_state.attempts[0]
        self.assertEqual(a.local_average, Fraction(60))
        self.assertEqual([b.volume for b in a.local_intervals], [D('120'), D(0), D('60')])
        self.assertEqual([b.classification for b in a.local_intervals], [K.TRADED, K.NO_TRADE, K.TRADED])

    def test_failed_attempt_enters_later_local_baseline(self):
        first = self.attempt(close='1.10', volume='300')
        self.send(kind=K.NO_TRADE)
        self.attempt(volume='500')
        a = self.detector.d_state.attempts[-1]
        self.assertIn(first, a.local_intervals)
        self.assertEqual(a.local_average, Fraction(400, 3))
        self.assertEqual(self.detector.state.lifecycle, S.PENDING_ENTRY)

    def test_zero_local_baseline_can_pass_absolute_comparison(self):
        for _ in range(3):
            self.send(kind=K.NO_TRADE)
        self.attempt()
        a = self.detector.d_state.attempts[0]
        self.assertEqual(a.local_average, 0)
        self.assertTrue(a.local_volume_passed)
        self.assertTrue(a.rvol_passed)

    def test_zero_previous_twenty_cannot_confirm_d(self):
        # A supplies the qualifying impulse volume; B and the next 19
        # trustworthy intervals have zero volume. C locks at traded slot 10.
        self.detector = DDetector('security', 'TEST', self.calendar,
                                  run_id='zero-d', data_version='fixture')
        self.at = datetime(2026, 10, 8, 9, 10, tzinfo=NEW_YORK)
        self.events = []
        for _ in range(20):
            self.send(low='1', high='1.01', close='1')
        self.send(low='1', high='1.01', close='1', volume='200')
        self.send(low='1', high='1.10', close='1.10', volume='0')
        for _ in range(6):
            self.send(low='1.085', high='1.09', close='1.085', volume='0')
        self.send(low='1.06', high='1.09', volume='0')
        for _ in range(3):
            self.send(volume='0')
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)
        self.assertEqual(self.detector.c_state.development_count, 10)
        for _ in range(9):
            self.send(kind=K.NO_TRADE)
        self.attempt()
        a = self.detector.d_state.attempts[0]
        self.assertIsNone(a.previous_20.ratio)
        self.assertFalse(a.rvol_passed)
        self.assertTrue(a.local_volume_passed)
        self.assertIn('RVOL_BASELINE_ZERO', a.failures)
        self.assertIsNone(self.detector.d_state.signal)

    def test_missing_active_setup_invalidates(self):
        self.send(kind=K.MISSING)
        self.assertEqual(self.detector.state.termination_reason, ReasonCode.DATA_GAP)
        self.assertEqual(self.detector.d_state.attempts, ())

    def test_invalid_active_setup_invalidates(self):
        self.attempt(close='1.10')
        self.send(kind=K.INVALID)
        self.assertEqual(self.detector.state.termination_reason, ReasonCode.INVALID_DATA)

    def test_below_c_invalidates_before_attempt(self):
        self.attempt(low='1.059')
        self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)
        self.assertEqual(self.detector.d_state.attempts, ())
        self.assertIsNone(self.detector.d_state.signal)
        self.assertEqual(self.detector.state.termination_reason, ReasonCode.C_INVALIDATION_INTRABAR_AMBIGUITY)

    def test_touching_c_allows_d(self):
        self.attempt(low='1.06')
        self.assertEqual(self.detector.state.lifecycle, S.PENDING_ENTRY)

    def test_locked_c_break_without_breakout_invalidates(self):
        self.send(low='1.059')
        self.assertEqual(self.detector.state.termination_description, 'PRICE_BELOW_LOCKED_C')

    def test_dynamic_gain_failure_terminates_before_d(self):
        self.send(low='.92', high='1.13', close='.92')
        self.assertEqual(self.detector.state.termination_reason, ReasonCode.DYNAMIC_DAILY_CHANGE_BELOW_MIN)
        self.assertIsNone(self.detector.d_state.signal)

    def test_wick_above_five_does_not_fail_dynamic_close(self):
        self.attempt(high='5.01')
        self.assertEqual(self.detector.state.lifecycle, S.PENDING_ENTRY)

    def test_dynamic_close_above_five_terminates(self):
        self.send(high='5.1', close='5.01')
        self.assertEqual(self.detector.state.termination_reason, ReasonCode.DYNAMIC_PRICE_ABOVE_MAX)

    def test_pending_no_trade_cancels_and_consumes(self):
        self.attempt()
        bar = self.send(kind=K.NO_TRADE)
        self.assertEqual(self.detector.state.termination_reason, ReasonCode.ENTRY_NO_TRADE)
        self.assertEqual(self.detector.state.termination, SetupTermination.CONSUMED)
        self.assertEqual(self.detector.state.termination_timestamp, bar.end)
        self.assertIsNone(self.detector.d_state.handoff)

    def test_pending_missing_cancels_with_entry_reason(self):
        self.attempt()
        bar = self.send(kind=K.MISSING, data_quality_reason='missing source candle')
        self.assertEqual(self.detector.state.termination_reason, ReasonCode.ENTRY_DATA_MISSING)
        self.assertEqual(self.detector._volume, [])
        self.assertEqual(self.detector.state.termination, SetupTermination.CONSUMED)
        self.assertEqual(self.detector.state.termination_timestamp, bar.end)
        self.assertIsNone(self.detector.d_state.handoff)
        for event_type in ('DATA_INTERVAL', 'PENDING_ENTRY_CANCELED'):
            event = next(e for e in self.events if e.event_type == event_type
                         and e.interval_start == bar.timestamp)
            self.assertEqual(dict(event.details)['data_quality_reason'], 'missing source candle')
            self.assertEqual(event.recorded_at, bar.end)
            self.assertEqual(event.modeled_event_at, bar.end)

    def test_pending_invalid_cancels_with_entry_reason(self):
        self.attempt()
        bar = self.send(kind=K.INVALID, data_quality_reason='provider conflict: corrupt interval')
        self.assertEqual(self.detector.state.termination_reason, ReasonCode.ENTRY_DATA_INVALID)
        self.assertEqual(self.detector.state.termination, SetupTermination.CONSUMED)
        self.assertEqual(self.detector.state.termination_timestamp, bar.end)
        self.assertIsNone(self.detector.d_state.handoff)
        for event_type in ('DATA_INTERVAL', 'PENDING_ENTRY_CANCELED'):
            event = next(e for e in self.events if e.event_type == event_type
                         and e.interval_start == bar.timestamp)
            self.assertEqual(dict(event.details)['data_quality_reason'], 'provider conflict: corrupt interval')
            self.assertEqual(event.recorded_at, bar.end)
            self.assertEqual(event.modeled_event_at, bar.end)

    def test_calendar_confirmed_holiday_rejects_phase_four_progression(self):
        class HolidayCalendar(Calendar):
            def session_for(self, day):
                if day == holiday.date():
                    return None
                return super().session_for(day)

        holiday = datetime(2026, 12, 25, 9, 30, tzinfo=NEW_YORK)
        self.detector.calendar = HolidayCalendar()
        before = self.detector.state, self.detector.c_state, self.detector.d_state
        with self.assertRaisesRegex(ValueError, 'No eligible session'):
            self.detector.feed(candle(holiday, high='1.13', close='1.105', volume='200'), self.inputs)
        self.assertEqual((self.detector.state, self.detector.c_state, self.detector.d_state), before)
        self.assertIsNone(self.detector.d_state.signal)

    def test_third_failure_on_resolution_minute_five_expires_without_fourth(self):
        self.attempt(close='1.10')
        self.send(kind=K.NO_TRADE)
        self.attempt(close='1.10')
        self.send(kind=K.NO_TRADE)
        final = self.attempt(close='1.10')
        self.assertEqual(self.detector.d_state.resolution_elapsed_minutes, 5)
        self.assertEqual(len(self.detector.d_state.attempts), 3)
        self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)
        self.assertEqual(self.detector.state.termination, SetupTermination.EXPIRED)
        self.assertEqual(self.detector.state.termination_description, 'BREAKOUT_ATTEMPT_LIMIT_EXHAUSTED')
        self.assertEqual(self.detector.state.termination_timestamp, final.end)
        setup = self.detector.state.setup_id
        self.attempt()
        attempts = [e for e in self.events if e.setup_id == setup and e.event_type == 'BREAKOUT_ATTEMPT']
        self.assertEqual([dict(e.details)['attempt_number'] for e in attempts], [1, 2, 3])
        self.assertFalse(any(e.setup_id == setup and e.event_type == 'D_CONFIRMED' for e in self.events))

    def test_timer_start_and_expiration_timestamps_include_no_trade_minutes(self):
        lock = self.detector.c_state.lock_timestamp
        started = next(e for e in self.events if e.event_type == 'LOCKED_C_WAIT_STARTED')
        self.assertEqual(dict(started.details)['first_interval'], lock)
        self.assertEqual(dict(started.details)['expiration'], lock + timedelta(minutes=10))
        self.send(kind=K.NO_TRADE)
        self.send(kind=K.NO_TRADE)
        first = self.attempt(close='1.10')
        self.assertEqual(first.timestamp, lock + timedelta(minutes=2))
        self.assertEqual(self.detector.d_state.first_attempt_timestamp, first.timestamp)
        self.assertEqual(self.detector.d_state.wait_elapsed_minutes, 3)
        attempt = next(e for e in self.events if e.event_type == 'BREAKOUT_ATTEMPT')
        self.assertEqual(dict(attempt.details)['resolution_elapsed_minutes'], 1)
        self.assertEqual(dict(attempt.details)['resolution_expiration'], first.timestamp + timedelta(minutes=5))
        for _ in range(4):
            last = self.send(kind=K.NO_TRADE)
        progress = [e for e in self.events if e.event_type == 'BREAKOUT_RESOLUTION_PROGRESS'][-1]
        self.assertEqual(dict(progress.details)['first_attempt'], first.timestamp)
        self.assertEqual(dict(progress.details)['expiration'], first.timestamp + timedelta(minutes=5))
        self.assertEqual(dict(progress.details)['elapsed_minutes'], 5)
        self.assertEqual(last.end, first.timestamp + timedelta(minutes=5))
        self.assertEqual(self.detector.state.termination_timestamp, last.end)
        self.assertEqual(self.detector.state.termination_description, 'BREAKOUT_RESOLUTION_WINDOW_EXHAUSTED')

    def test_traded_scheduled_interval_hands_off_not_fills(self):
        self.attempt()
        bar = self.send(high='1.12', close='1.09', opening='1.11')
        handoff = self.detector.d_state.handoff
        self.assertEqual(handoff.interval, bar)
        self.assertEqual(handoff.modeled_open_timestamp, bar.timestamp)
        self.assertEqual(handoff.interval.open, D('1.11'))
        self.assertEqual(self.detector.state.lifecycle, S.PENDING_ENTRY)
        self.assertNotEqual(self.detector.state.lifecycle, S.OPEN_POSITION)
        audit = self.events[-1]
        self.assertEqual(audit.event_type, 'PENDING_ENTRY_HANDOFF')
        self.assertEqual(audit.modeled_event_at, bar.timestamp)
        self.assertEqual(audit.recorded_at, bar.end)
        self.assertFalse(dict(audit.details)['executed'])

    def test_handoff_requires_explicit_future_owner_no_second_opportunity(self):
        self.attempt()
        self.send()
        with self.assertRaises(RuntimeError):
            self.send()
        self.assertEqual(sum(e.event_type == 'PENDING_ENTRY_HANDOFF' for e in self.events), 1)

    def test_open_based_entry_checks_are_deferred_not_close_based(self):
        self.attempt()
        bar = self.send(low='1', high='5.1', close='5.01', opening='1.11')
        self.assertEqual(self.detector.d_state.handoff.interval.open, D('1.11'))
        self.assertEqual(bar.close, D('5.01'))
        self.assertIsNone(self.detector.state.termination)

    def test_cancelled_no_trade_preserves_volume_but_not_price_history(self):
        self.attempt()
        canceled = self.send(kind=K.NO_TRADE)
        next_bar = self.send()
        self.assertIn(canceled, self.detector._volume)
        self.assertEqual([e.candle for e in self.detector._price], [next_bar])
        self.assertIsNone(self.detector.d_state.signal)

    def test_cancelled_gap_rebuilds_history(self):
        self.attempt()
        self.send(kind=K.MISSING)
        next_bar = self.send()
        self.assertEqual(self.detector._volume, [next_bar])
        self.assertEqual([e.candle for e in self.detector._price], [next_bar])

    def test_pending_open_at_cutoff_cancels(self):
        # Signal-stage boundary fixture, using a verified early-close session.
        self.calendar.close_hour = 13
        scheduled = datetime(2026, 10, 8, 12, 30, tzinfo=NEW_YORK)
        self.attempt()
        self.detector.d_state = replace(self.detector.d_state,
                                       signal=replace(self.detector.d_state.signal, scheduled_interval=scheduled))
        self.detector._previous = candle(scheduled - timedelta(minutes=1))
        self.at = scheduled
        self.send()
        self.assertEqual(self.detector.state.termination_reason, ReasonCode.ENTRY_TIME_RESTRICTION)

    def test_last_permitted_entry_minute_uses_open_not_end_cutoff(self):
        self.calendar.close_hour = 13
        self.attempt()
        scheduled = datetime(2026, 10, 8, 12, 29, tzinfo=NEW_YORK)
        self.detector.d_state = replace(self.detector.d_state,
                                       signal=replace(self.detector.d_state.signal, scheduled_interval=scheduled))
        self.detector._previous = candle(scheduled - timedelta(minutes=1))
        self.at = scheduled
        self.send()
        self.assertIsNotNone(self.detector.d_state.handoff)
        self.assertIsNone(self.detector.state.termination)

    def test_session_cutoff_expires_waiting_setup(self):
        self.calendar.close_hour = 13
        self.detector._previous = candle(datetime(2026, 10, 8, 12, 28, tzinfo=NEW_YORK))
        self.at = self.detector._previous.end
        self.send(kind=K.NO_TRADE)
        self.assertEqual(self.detector.state.termination_description, 'NO_PERMITTED_ENTRY_REMAINS')

    def test_omitted_minutes_rejected_not_silent_deadline_pause(self):
        self.at += timedelta(minutes=1)
        with self.assertRaises(ValueError):
            self.send()

    def test_audits_reconstruct_attempts_timers_and_baselines(self):
        self.attempt(close='1.10')
        self.send(kind=K.NO_TRADE)
        self.attempt(volume='400')
        attempts = [e for e in self.events if e.event_type == 'BREAKOUT_ATTEMPT']
        self.assertEqual(len(attempts), 2)
        details = dict(attempts[-1].details)
        self.assertEqual(details['attempt_number'], 2)
        self.assertEqual(details['resolution_elapsed_minutes'], 3)
        self.assertTrue(details['rvol_passed'])
        self.assertIn('NO_TRADE', details['local_intervals'])
        self.assertTrue(all(e.modeled_event_at <= e.recorded_at for e in self.events))

    def test_no_d_retroactively_on_c_locking_candle(self):
        self.assertEqual(self.detector.d_state.attempts, ())
        self.assertIsNone(self.detector.d_state.signal)
        self.assertEqual(self.detector.d_state.wait_elapsed_minutes, 0)

    def test_d_arithmetic_is_independent_of_decimal_precision(self):
        with localcontext() as context:
            context.prec = 2
            self.attempt(close='1.10275', volume='157.5')
        self.assertEqual(self.detector.state.lifecycle, S.PENDING_ENTRY)
        self.assertEqual(self.detector.d_state.attempts[0].close_extension, Fraction(1, 400))
        self.assertEqual(self.detector.d_state.attempts[0].previous_20.ratio, Fraction(3, 2))

    def test_signal_records_and_audits_are_reproducible(self):
        self.attempt()
        expected = self.detector.d_state, tuple(self.events)
        self.setUp()
        self.attempt()
        self.assertEqual((self.detector.d_state, tuple(self.events)), expected)

    def test_no_d_state_carries_overnight(self):
        self.attempt(close='1.10')
        while self.at.hour < 16:
            self.send(kind=K.NO_TRADE)
        self.at = datetime(2026, 10, 9, 9, 10, tzinfo=NEW_YORK)
        self.send()
        self.assertEqual(self.detector.d_state.attempts, ())
        self.assertEqual(self.detector.d_state.wait_elapsed_minutes, 0)
        self.assertIsNone(self.detector.d_state.signal)
        self.assertIsNone(self.detector.c_state.locked_price)

    def late_locked_setup(self):
        self.calendar.close_hour = 13
        self.detector = DDetector('security', 'TEST', self.calendar,
                                  run_id='late-d', data_version='fixture')
        self.at = datetime(2026, 10, 8, 12, 2, tzinfo=NEW_YORK)
        self.events = []
        for _ in range(20):
            self.send(low='1.001', high='1.01', close='1.005')
        self.send(low='1', high='1.01', close='1')
        self.send(low='1', high='1.10', close='1.10', volume='200')
        self.send(low='1.06')
        for _ in range(3):
            self.send()
        self.assertEqual(self.detector.state.lifecycle, S.C_LOCKED)
        self.assertEqual(self.at, datetime(2026, 10, 8, 12, 28, tzinfo=NEW_YORK))

    def test_end_to_end_last_permitted_open_handoff(self):
        self.late_locked_setup()
        self.attempt()
        bar = self.send()
        self.assertEqual(bar.timestamp, datetime(2026, 10, 8, 12, 29, tzinfo=NEW_YORK))
        self.assertIsNotNone(self.detector.d_state.handoff)

    def test_end_to_end_d_cannot_schedule_cutoff_entry(self):
        self.late_locked_setup()
        self.send()
        self.attempt()
        self.assertEqual(self.detector.state.lifecycle, S.TERMINATED)
        self.assertEqual(self.detector.state.termination_description, 'NO_PERMITTED_ENTRY_REMAINS')
        self.assertIsNone(self.detector.d_state.signal)


if __name__ == '__main__':
    unittest.main()
