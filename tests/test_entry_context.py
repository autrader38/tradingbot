"""Exact completed-block and chronological weekly boundaries."""

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from fractions import Fraction

from tradingbot_backtest.codes import ReasonCode as R
from tradingbot_backtest.config import FROZEN_V1
from tradingbot_backtest.entry_context import (ContextMinute, WeeklyBar, WeeklyHistory,
                                             WeeklyClassification as W, intraday_context, weekly_context)
from tradingbot_backtest.market import IntervalClassification as K
from tradingbot_backtest.sessions import NEW_YORK
from tests.entry_fixtures import Calendar, minute, weekly_history


class IntradayTests(unittest.TestCase):
    def setUp(self):
        self.calendar = Calendar()
        self.opening = datetime(2026, 10, 8, 10, 15, tzinfo=NEW_YORK)
        self.session = self.calendar.session_for(self.opening.date())
        self.minutes = tuple(ContextMinute(minute(self.session.open + timedelta(minutes=i)),
                                          self.session.open + timedelta(minutes=i+1), 'basis') for i in range(45))

    def result(self, minutes=None, opening=None):
        return intraday_context(self.minutes if minutes is None else minutes, self.session,
                                opening or self.opening, 'security', 'basis', FROZEN_V1)

    def test_valid_equality_of_both_comparisons_passes(self):
        result = self.result()
        self.assertIsNone(result.reason)
        self.assertTrue(result.high_passed)
        self.assertTrue(result.low_passed)
        self.assertEqual([block.start for block in result.blocks],
                         [self.session.open + timedelta(minutes=i) for i in (0, 15, 30)])

    def test_lower_latest_high_fails(self):
        self.minutes = tuple(replace(item, interval=replace(item.interval, high=D('1.005')))
                             if i >= 30 else item for i, item in enumerate(self.minutes))
        result = self.result()
        self.assertFalse(result.high_passed)
        self.assertTrue(result.low_passed)
        self.assertEqual(result.reason, R.ENTRY_15M_STRUCTURE_BEARISH)

    def test_lower_latest_low_fails(self):
        self.minutes = tuple(replace(item, interval=replace(item.interval, low=D('.98')))
                             if i >= 30 else item for i, item in enumerate(self.minutes))
        result = self.result()
        self.assertTrue(result.high_passed)
        self.assertFalse(result.low_passed)
        self.assertEqual(result.reason, R.ENTRY_15M_STRUCTURE_BEARISH)

    def test_insufficient_blocks_at_ten_fourteen(self):
        result = self.result(opening=self.opening - timedelta(minutes=1))
        self.assertEqual(result.reason, R.ENTRY_INSUFFICIENT_15M_CONTEXT)

    def test_forming_block_cannot_substitute(self):
        forming = ContextMinute(minute(self.opening, low='.5', high='2'), self.opening + timedelta(minutes=1), 'basis')
        result = self.result(minutes=self.minutes + (forming,), opening=self.opening + timedelta(minutes=8))
        self.assertEqual(result.blocks, self.result().blocks)

    def test_previous_session_cannot_fill_missing_current_slot(self):
        previous = replace(self.minutes[0], interval=replace(self.minutes[0].interval,
                                                            timestamp=self.minutes[0].interval.timestamp - timedelta(days=1)))
        result = self.result(minutes=(previous,) + self.minutes[1:])
        self.assertEqual(result.reason, R.ENTRY_15M_DATA_UNAVAILABLE)

    def test_premarket_cannot_fill_context(self):
        premarket = replace(self.minutes[0], interval=replace(self.minutes[0].interval,
                                                             timestamp=self.session.open - timedelta(minutes=1)))
        self.assertEqual(self.result(minutes=(premarket,) + self.minutes[1:]).reason,
                         R.ENTRY_15M_DATA_UNAVAILABLE)

    def test_partial_traded_block_uses_actual_first_and_last_trades(self):
        items = []
        for i, item in enumerate(self.minutes):
            if i < 15 and i not in (1, 4, 10, 14):
                item = replace(item, interval=minute(item.interval.timestamp, kind=K.NO_TRADE))
            elif i == 1:
                item = replace(item, interval=minute(item.interval.timestamp, opening='.995', close='1', high='1.01'))
            elif i == 14:
                item = replace(item, interval=minute(item.interval.timestamp, close='1.005', high='1.01'))
            items.append(item)
        block = self.result(minutes=tuple(items)).blocks[0]
        self.assertEqual((block.open, block.high, block.low, block.close),
                         (D('.995'), D('1.01'), D('.99'), D('1.005')))
        self.assertEqual(block.volume, Fraction(400))
        self.assertEqual(sum(item.interval.classification == K.NO_TRADE for item in block.constituents), 11)

    def test_entire_no_trade_block_has_no_synthetic_ohlc(self):
        items = tuple(replace(item, interval=minute(item.interval.timestamp, kind=K.NO_TRADE))
                      if 15 <= i < 30 else item for i, item in enumerate(self.minutes))
        result = self.result(items)
        self.assertEqual(result.reason, R.ENTRY_15M_CONTEXT_NO_TRADE)
        block = result.blocks[1]
        self.assertEqual((block.open, block.high, block.low, block.close), (None, None, None, None))
        self.assertEqual(block.volume, 0)

    def test_missing_constituent_fails_with_original_cause(self):
        items = list(self.minutes)
        items[20] = replace(items[20], interval=minute(items[20].interval.timestamp, kind=K.MISSING,
                                                      cause='provider minute unavailable'))
        result = self.result(tuple(items))
        self.assertEqual(result.reason, R.ENTRY_15M_DATA_UNAVAILABLE)
        self.assertIn('provider minute unavailable', '|'.join(result.blocks[1].failures))

    def test_invalid_constituent_fails(self):
        items = list(self.minutes)
        items[20] = replace(items[20], interval=minute(items[20].interval.timestamp, kind=K.INVALID))
        self.assertEqual(self.result(tuple(items)).reason, R.ENTRY_15M_DATA_UNAVAILABLE)

    def test_bad_latest_block_cannot_be_skipped_for_older_valid_block(self):
        older = tuple(replace(item, interval=replace(item.interval, timestamp=item.interval.timestamp - timedelta(minutes=15)),
                              available_at=item.available_at - timedelta(minutes=15)) for item in self.minutes[:15])
        items = self.minutes[:30] + tuple(replace(item, interval=minute(item.interval.timestamp, kind=K.NO_TRADE))
                                         for item in self.minutes[30:])
        self.assertEqual(self.result(older + items).reason, R.ENTRY_15M_CONTEXT_NO_TRADE)

    def test_missing_slot_is_not_inferred_as_no_trade(self):
        self.assertEqual(self.result(self.minutes[:20] + self.minutes[21:]).reason, R.ENTRY_15M_DATA_UNAVAILABLE)

    def test_conflicting_duplicate_cannot_be_silently_resolved(self):
        self.assertEqual(self.result(self.minutes + (self.minutes[0],)).reason, R.ENTRY_15M_DATA_UNAVAILABLE)

    def test_future_corrected_history_unavailable_at_open(self):
        items = (replace(self.minutes[0], available_at=self.opening + timedelta(minutes=1)),) + self.minutes[1:]
        self.assertEqual(self.result(items).reason, R.ENTRY_15M_DATA_UNAVAILABLE)

    def test_wrong_security_or_share_basis_fails(self):
        for replacement in (replace(self.minutes[0], share_basis_id='other'),
                            replace(self.minutes[0], interval=replace(self.minutes[0].interval, security_id='other'))):
            self.assertEqual(self.result((replacement,) + self.minutes[1:]).reason, R.ENTRY_15M_DATA_UNAVAILABLE)


class WeeklyTests(unittest.TestCase):
    def setUp(self):
        self.calendar = Calendar()
        self.opening = datetime(2026, 10, 8, 10, 15, tzinfo=NEW_YORK)
        self.history = weekly_history(self.opening, self.calendar)

    def result(self, history=None, price='1'):
        return weekly_context(history or self.history, self.calendar, self.opening, D(price),
                              'security', 'fixture-basis', FROZEN_V1)

    def highs(self, values, count=12):
        self.history = weekly_history(self.opening, self.calendar, count=count, highs=values)

    def test_no_overhead_passes_with_correct_status(self):
        result = self.result()
        self.assertIsNone(result.reason)
        self.assertIsNone(result.nearest_resistance)
        self.assertEqual(result.status, R.NO_IDENTIFIED_WEEKLY_RESISTANCE)

    def test_confirmed_swing_and_exact_five_percent_room(self):
        self.highs(['.8', '.9', '1.05', '1.05', '.9'] + ['.8'] * 7)
        result = self.result()
        self.assertEqual(result.nearest_resistance, D('1.05'))
        self.assertEqual(result.room_pct, Fraction(5))
        self.assertIsNone(result.reason)
        self.assertEqual(result.evaluations[2].status, 'CONFIRMED')

    def test_less_than_five_percent_room_fails(self):
        self.highs(['.8', '.9', '1.049999', '.9', '.8'] + ['.8'] * 7)
        self.assertEqual(self.result().reason, R.ENTRY_INSUFFICIENT_WEEKLY_ROOM)

    def test_left_equality_disqualifies(self):
        self.highs(['1.05', '.9', '1.05', '.9', '.8'] + ['.8'] * 7)
        self.assertEqual(self.result().evaluations[2].status, 'NOT_SWING_HIGH')
        self.assertIsNone(self.result().nearest_resistance)

    def test_right_equality_permits_confirmation(self):
        self.highs(['.8', '.9', '1.10', '1.10', '1.10'] + ['.8'] * 7)
        self.assertEqual(self.result().evaluations[2].status, 'CONFIRMED')

    def test_right_higher_high_disqualifies(self):
        self.highs(['.8', '.9', '1.10', '1.11', '.8'] + ['.8'] * 7)
        self.assertEqual(self.result().evaluations[2].status, 'NOT_SWING_HIGH')

    def test_nearest_overhead_selected_not_more_favorable_farther_level(self):
        self.highs(['.8', '.9', '1.04', '.9', '.8', '.9', '1.20', '.9', '.8', '.8', '.8', '.8'])
        result = self.result()
        self.assertEqual(result.nearest_resistance, D('1.04'))
        self.assertEqual(result.reason, R.ENTRY_INSUFFICIENT_WEEKLY_ROOM)

    def test_resistance_at_or_below_open_is_ignored(self):
        self.highs(['.8', '.9', '1', '.9', '.8', '.9', '1.20', '.9', '.8', '.8', '.8', '.8'])
        self.assertEqual(self.result().nearest_resistance, D('1.20'))

    def test_last_two_candidates_unconfirmed(self):
        self.highs(['.8'] * 10 + ['1.02', '1.03'])
        result = self.result()
        self.assertEqual([item.status for item in result.evaluations[-2:]], ['UNCONFIRMED', 'UNCONFIRMED'])
        self.assertIsNone(result.nearest_resistance)

    def test_current_and_future_weeks_never_confirm_recent_candidates(self):
        self.highs(['.8'] * 10 + ['1.02', '1.03'])
        current = self.opening.date() - timedelta(days=self.opening.weekday())
        extras = tuple(replace(self.history.bars[-1], week_start=current + timedelta(days=7*i), high=D('.8'),
                               completed_at=self.opening + timedelta(days=7*i+1),
                               available_at=self.opening + timedelta(days=7*i+1)) for i in range(2))
        self.assertEqual(self.result(replace(self.history, bars=self.history.bars + extras)), self.result())

    def test_exact_twelve_history_evaluated(self):
        result = self.result()
        self.assertEqual(len(result.candidate_weeks), 12)
        self.assertEqual(len(result.evaluations), 12)
        self.assertEqual([item.status for item in result.evaluations[:2]], ['NONEXISTENT_HISTORY'] * 2)

    def test_eleven_actual_weeks_insufficient(self):
        history = weekly_history(self.opening, self.calendar, count=11)
        self.assertEqual(self.result(history).reason, R.ENTRY_INSUFFICIENT_WEEKLY_HISTORY)

    def test_context_outside_fifty_two_supports_oldest_candidate(self):
        values = ['.8', '.9', '1.10', '.9', '.8'] + ['.8'] * 49
        self.highs(values, count=54)
        result = self.result()
        self.assertEqual(len(result.candidate_weeks), 52)
        self.assertEqual(len(result.context_weeks), 2)
        self.assertEqual(result.evaluations[0].status, 'CONFIRMED')
        self.assertEqual(result.nearest_resistance, D('1.10'))

    def test_outside_window_context_cannot_be_resistance_candidate(self):
        values = ['.8', '.9', '1.01', '.9', '.8', '.8'] + ['.8'] * 49
        self.highs(values, count=55)
        result = self.result()
        self.assertEqual(len(result.candidate_weeks), 52)
        self.assertNotIn(self.history.bars[2].week_start, result.candidate_weeks)
        self.assertIsNone(result.nearest_resistance)

    def test_missing_existing_week_not_nonexistent_or_skipped(self):
        bars = self.history.bars[:4] + self.history.bars[5:]
        result = self.result(replace(self.history, bars=bars))
        self.assertEqual(result.reason, R.ENTRY_WEEKLY_DATA_UNAVAILABLE)
        self.assertEqual(result.status, 'UNAVAILABLE')

    def test_invalid_week_preserves_underlying_cause(self):
        bars = list(self.history.bars)
        bars[4] = replace(bars[4], classification=W.INVALID, high=None, data_quality_reason='corrupt weekly HIGH')
        result = self.result(replace(self.history, bars=tuple(bars)))
        self.assertEqual(result.reason, R.ENTRY_WEEKLY_DATA_UNAVAILABLE)
        self.assertIn('corrupt weekly HIGH', '|'.join(result.failures))

    def test_missing_left_context_cannot_be_filled_with_older_extra_week(self):
        history = weekly_history(self.opening, self.calendar, count=55)
        bars = history.bars[:1] + history.bars[2:]
        self.assertEqual(self.result(replace(history, bars=bars)).reason, R.ENTRY_WEEKLY_DATA_UNAVAILABLE)

    def test_duplicate_week_fails(self):
        self.assertEqual(self.result(replace(self.history, bars=self.history.bars + (self.history.bars[0],))).reason,
                         R.ENTRY_WEEKLY_DATA_UNAVAILABLE)

    def test_future_correction_to_required_week_cannot_be_used(self):
        bars = (replace(self.history.bars[0], available_at=self.opening + timedelta(minutes=1)),) + self.history.bars[1:]
        self.assertEqual(self.result(replace(self.history, bars=bars)).reason, R.ENTRY_WEEKLY_DATA_UNAVAILABLE)

    def test_incompatible_weekly_basis_rejects_without_adjustment(self):
        bars = (replace(self.history.bars[0], share_basis_id='post-future-split'),) + self.history.bars[1:]
        self.assertEqual(self.result(replace(self.history, bars=bars)).reason, R.CORPORATE_ACTION_DATA_UNAVAILABLE)

    def test_unavailable_listing_metadata_rejects(self):
        self.assertEqual(self.result(replace(self.history, listing_available_at=self.opening + timedelta(days=1))).reason,
                         R.ENTRY_WEEKLY_DATA_UNAVAILABLE)

    def test_unavailable_listing_requires_no_calendar_enumeration(self):
        class NoHistoryCalendar:
            def session_for(self, day):
                raise AssertionError('Unavailable listing must not cause historical calendar queries')

        results = []
        for listed in (date(1990, 1, 3), date(2026, 9, 30)):
            history = replace(self.history, listing_date=listed,
                              listing_available_at=self.opening + timedelta(minutes=1))
            result = weekly_context(history, NoHistoryCalendar(), self.opening, D('1'),
                                    'security', 'fixture-basis', FROZEN_V1)
            results.append(result)
            self.assertEqual(result.reason, R.ENTRY_WEEKLY_DATA_UNAVAILABLE)
            self.assertEqual((result.candidate_weeks, result.context_weeks,
                              result.bars, result.evaluations), ((), (), (), ()))
        self.assertEqual(results[0], results[1])

    def test_zero_session_calendar_week_is_skipped_as_legitimate(self):
        missing_week = self.history.bars[5].week_start
        self.calendar.holidays = frozenset(missing_week + timedelta(days=i) for i in range(5))
        history = weekly_history(self.opening, self.calendar, count=12)
        result = self.result(history)
        self.assertIsNone(result.reason)
        self.assertNotIn(missing_week, result.candidate_weeks)

    def test_holiday_shortened_and_early_close_week_uses_actual_completion(self):
        last = self.history.bars[-1]
        self.calendar.close_hour = 13
        self.calendar.holidays = frozenset((last.week_start + timedelta(days=4),))
        history = weekly_history(self.opening, self.calendar)
        result = self.result(history)
        self.assertIsNone(result.reason)
        self.assertEqual(result.bars[-1].completed_at.weekday(), 3)
        self.assertEqual(result.bars[-1].completed_at.hour, 13)

    def test_wrong_completion_timestamp_is_unavailable(self):
        bars = (replace(self.history.bars[0], completed_at=self.history.bars[0].completed_at - timedelta(minutes=1)),) + self.history.bars[1:]
        self.assertEqual(self.result(replace(self.history, bars=bars)).reason, R.ENTRY_WEEKLY_DATA_UNAVAILABLE)

    def test_expected_week_dates_use_independent_literal_history(self):
        # Explicit Monday/Friday pairs; no shared calendar-enumeration helper.
        pairs = (
            ('2026-07-13', '2026-07-17'), ('2026-07-20', '2026-07-24'),
            ('2026-07-27', '2026-07-31'), ('2026-08-03', '2026-08-07'),
            ('2026-08-10', '2026-08-14'), ('2026-08-17', '2026-08-21'),
            ('2026-08-24', '2026-08-28'), ('2026-08-31', '2026-09-04'),
            ('2026-09-07', '2026-09-11'), ('2026-09-14', '2026-09-18'),
            ('2026-09-21', '2026-09-25'), ('2026-09-28', '2026-10-02'),
        )
        bars = []
        for monday, friday in pairs:
            close = datetime.fromisoformat(friday + 'T16:00:00').replace(tzinfo=NEW_YORK)
            bars.append(WeeklyBar('security', date.fromisoformat(monday), close, close,
                                  'literal-weekly-source', 'fixture-basis', W.VALID, D('.8')))
        history = WeeklyHistory(date(2026, 7, 13), datetime(2026, 7, 13, tzinfo=NEW_YORK),
                                'literal-listing-source', tuple(bars))
        result = self.result(history)
        self.assertIsNone(result.reason)
        self.assertEqual(result.candidate_weeks, tuple(date.fromisoformat(monday) for monday, _ in pairs))
        self.assertEqual(result.context_weeks, ())
        self.assertEqual(tuple(item.candidate_week for item in result.evaluations), result.candidate_weeks)
        self.assertEqual(result.evaluations[2].left_weeks, (date(2026, 7, 13), date(2026, 7, 20)))
        self.assertEqual(result.evaluations[2].right_weeks, (date(2026, 8, 3), date(2026, 8, 10)))

    def test_midweek_listing_keeps_partial_first_week_and_nonexistent_left_context(self):
        self.highs(['.8', '.9', '1.05', '.9', '.8'] + ['.8'] * 7)
        history = replace(self.history, listing_date=date(2026, 7, 15),
                          listing_available_at=datetime(2026, 7, 15, 9, 30, tzinfo=NEW_YORK))
        result = self.result(history)
        self.assertIsNone(result.reason)
        self.assertEqual(len(result.candidate_weeks), 12)
        self.assertEqual(result.candidate_weeks[0], date(2026, 7, 13))
        self.assertEqual(result.bars[0].completed_at, datetime(2026, 7, 17, 16, tzinfo=NEW_YORK))
        self.assertEqual([item.status for item in result.evaluations[:2]], ['NONEXISTENT_HISTORY'] * 2)
        self.assertEqual(result.evaluations[2].status, 'CONFIRMED')
        self.assertEqual(result.nearest_resistance, D('1.05'))
        self.assertEqual(result.room_pct, Fraction(5))
