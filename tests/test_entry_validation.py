"""Phase 5 entry-time boundaries, information barriers and immutable outcomes."""

import unittest
from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D, localcontext
from fractions import Fraction

from tradingbot_backtest.ab_detector import EligibilityInputs
from tradingbot_backtest.codes import ReasonCode as R
from tradingbot_backtest.entry_validation import EntryOutcome as O, EntryValidator, TickReference
from tradingbot_backtest.sessions import NEW_YORK
from tests.entry_fixtures import Calendar, handoff_fixture, weekly_history


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.handoff, self.context, self.calendar, self.detector = handoff_fixture()

    def result(self, handoff=None, context=None, calendar=None):
        return EntryValidator(calendar or self.calendar, run_id='entry-run', data_version='fixture').evaluate(
            handoff or self.handoff, context or self.context)

    def with_open(self, value):
        # Phase 4 hands off trustworthy opening inputs without approving them.
        bar = replace(self.handoff.interval, open=D(value), low=min(D('.95'), D(value)),
                      high=max(D('1.15'), D(value)))
        return replace(self.handoff, interval=bar)

    def test_complete_phase_four_handoff_produces_approval(self):
        result = self.result()
        self.assertEqual(result.outcome, O.ENTRY_APPROVED)
        self.assertFalse(result.consumed)
        self.assertIsNone(result.reason)
        self.assertEqual(result.approval.reference_open, D('1.11'))
        self.assertEqual(result.approval.scheduled_timestamp, self.handoff.signal.scheduled_interval)
        self.assertEqual(result.approval.a, self.handoff.signal.a)
        self.assertEqual(result.approval.b_origin, self.handoff.signal.b_origin)
        self.assertEqual(result.approval.c_evidence, self.handoff.signal.c_evidence)
        self.assertEqual(result.approval.d, self.handoff.signal.d)
        self.assertEqual(result.approval.d_attempt, self.handoff.signal.attempt)
        self.assertEqual(result.approval.d_attempt.previous_20, self.handoff.signal.attempt.previous_20)
        self.assertEqual(len(result.approval.intraday.blocks), 3)
        self.assertEqual(len(result.approval.weekly.candidate_weeks), 12)

    def test_open_exactly_b_fails(self):
        self.assertEqual(self.result(self.with_open('1.10')).reason, R.ENTRY_OPEN_AT_OR_BELOW_B)

    def test_open_below_b_fails(self):
        self.assertEqual(self.result(self.with_open('1.099999')).reason, R.ENTRY_OPEN_AT_OR_BELOW_B)

    def test_open_just_above_b_passes(self):
        self.assertEqual(self.result(self.with_open('1.100001')).outcome, O.ENTRY_APPROVED)

    def test_open_exact_two_percent_passes(self):
        result = self.result(self.with_open('1.122'))
        self.assertEqual(result.outcome, O.ENTRY_APPROVED)
        self.assertEqual(result.approval.b_extension, Fraction(1, 50))

    def test_open_above_two_percent_consumes(self):
        result = self.result(self.with_open('1.122001'))
        self.assertEqual(result.reason, R.ENTRY_OPEN_ABOVE_MAX)
        self.assertTrue(result.consumed)
        self.assertIsNone(result.approval)

    def test_rejected_opportunity_cannot_be_retried(self):
        validator = EntryValidator(self.calendar, run_id='entry-run', data_version='fixture')
        self.assertTrue(validator.evaluate(self.with_open('1.10'), self.context).consumed)
        with self.assertRaisesRegex(ValueError, 'one scheduled'):
            validator.evaluate(self.handoff, self.context)

    def test_approved_opportunity_cannot_be_validated_twice(self):
        validator = EntryValidator(self.calendar, run_id='entry-run', data_version='fixture')
        validator.evaluate(self.handoff, self.context)
        with self.assertRaises(ValueError):
            validator.evaluate(self.handoff, self.context)

    def test_later_high_cannot_change_approval_or_audit(self):
        handoff = replace(self.handoff, interval=replace(self.handoff.interval, high=D('100')))
        self.assertEqual(self.result(handoff), self.result())

    def test_later_low_cannot_change_approval_or_audit(self):
        handoff = replace(self.handoff, interval=replace(self.handoff.interval, low=D('.01')))
        self.assertEqual(self.result(handoff), self.result())

    def test_later_close_cannot_change_approval_or_audit(self):
        handoff = replace(self.handoff, interval=replace(self.handoff.interval, close=D('.96')))
        self.assertEqual(self.result(handoff), self.result())

    def test_completed_volume_cannot_change_approval_or_audit(self):
        handoff = replace(self.handoff, interval=replace(self.handoff.interval, volume=D('999999')))
        self.assertEqual(self.result(handoff), self.result())

    def test_exact_five_dollars_passes(self):
        handoff, context, calendar, _ = handoff_fixture('5', factor='4.5')
        result = self.result(handoff, context, calendar)
        self.assertEqual(result.outcome, O.ENTRY_APPROVED)
        self.assertTrue(result.approval.price_eligible)

    def test_above_five_dollars_fails_dynamic_gate(self):
        handoff, context, calendar, _ = handoff_fixture('5.000001', factor='4.5')
        self.assertEqual(self.result(handoff, context, calendar).reason, R.ENTRY_DYNAMIC_PRICE_ABOVE_MAX)

    def gain_boundary(self, opening):
        # Isolated opening-gate boundary: supplied and upstream reference units
        # agree. This does not claim that this edited fixture is a detector replay.
        handoff = replace(self.with_open(opening), eligibility_inputs=EligibilityInputs(D('1.08'), True, 'fixture-basis'))
        context = replace(self.context, eligibility=replace(self.context.eligibility, prior_regular_close=D('1.08')))
        return self.result(handoff, context)

    def test_exact_three_percent_gain_passes(self):
        result = self.gain_boundary('1.1124')
        self.assertEqual(result.outcome, O.ENTRY_APPROVED)
        self.assertEqual(result.approval.opening_change, Fraction(3, 100))

    def test_below_three_percent_gain_consumes(self):
        self.assertEqual(self.gain_boundary('1.112399').reason, R.ENTRY_DYNAMIC_DAILY_CHANGE_BELOW_MIN)

    def test_both_dynamic_gate_failures_are_preserved(self):
        handoff, context, calendar, _ = handoff_fixture('5.000001', factor='4.5')
        prior = D('5')
        handoff = replace(handoff, eligibility_inputs=EligibilityInputs(prior, True, 'fixture-basis'))
        context = replace(context, eligibility=replace(context.eligibility, prior_regular_close=prior))
        result = self.result(handoff, context, calendar)
        self.assertEqual(result.reason, R.ENTRY_DYNAMIC_MULTIPLE_ELIGIBILITY_FAILURES)
        details = dict(next(e for e in result.audit if e.event_type == 'ENTRY_DYNAMIC_ELIGIBILITY').details)
        self.assertFalse(details['price_passed'])
        self.assertFalse(details['change_passed'])

    def test_permitted_open_before_normal_cutoff(self):
        handoff, context, calendar, _ = handoff_fixture(scheduled=datetime(2026, 10, 8, 15, 29, tzinfo=NEW_YORK))
        self.assertEqual(self.result(handoff, context, calendar).outcome, O.ENTRY_APPROVED)

    def test_exact_cutoff_is_exclusive(self):
        # Boundary fixture: real Phase 4 would already prohibit this schedule.
        at = self.handoff.modeled_open_timestamp.replace(hour=15, minute=30)
        signal = replace(self.handoff.signal, scheduled_interval=at,
                         d=replace(self.handoff.signal.d, timestamp=at-timedelta(minutes=1)),
                         d_confirmation_timestamp=at)
        handoff = replace(self.handoff, signal=signal, interval=replace(self.handoff.interval, timestamp=at),
                          modeled_open_timestamp=at)
        self.assertEqual(self.result(handoff).reason, R.ENTRY_TIME_RESTRICTION)

    def test_after_cutoff_fails(self):
        at = self.handoff.modeled_open_timestamp.replace(hour=15, minute=31)
        signal = replace(self.handoff.signal, scheduled_interval=at,
                         d=replace(self.handoff.signal.d, timestamp=at-timedelta(minutes=1)),
                         d_confirmation_timestamp=at)
        handoff = replace(self.handoff, signal=signal, interval=replace(self.handoff.interval, timestamp=at),
                          modeled_open_timestamp=at)
        self.assertEqual(self.result(handoff).reason, R.ENTRY_TIME_RESTRICTION)

    def test_early_close_last_permitted_open_passes(self):
        handoff, context, calendar, _ = handoff_fixture(scheduled=datetime(2026, 10, 8, 12, 29, tzinfo=NEW_YORK),
                                                       calendar=Calendar(close_hour=13))
        self.assertEqual(self.result(handoff, context, calendar).outcome, O.ENTRY_APPROVED)

    def test_early_close_cutoff_fails(self):
        handoff, context, _, _ = handoff_fixture(scheduled=datetime(2026, 10, 8, 12, 29, tzinfo=NEW_YORK))
        at = handoff.modeled_open_timestamp + timedelta(minutes=1)
        signal = replace(handoff.signal, scheduled_interval=at,
                         d=replace(handoff.signal.d, timestamp=at-timedelta(minutes=1)), d_confirmation_timestamp=at)
        handoff = replace(handoff, signal=signal, interval=replace(handoff.interval, timestamp=at), modeled_open_timestamp=at)
        self.assertEqual(self.result(handoff, context, Calendar(close_hour=13)).reason, R.ENTRY_TIME_RESTRICTION)

    def test_confirmed_holiday_cancels(self):
        calendar = Calendar(holidays=(self.handoff.modeled_open_timestamp.date(),))
        self.assertEqual(self.result(calendar=calendar).reason, R.ENTRY_TIME_RESTRICTION)

    def test_pending_opportunity_cannot_carry_to_next_day(self):
        at = self.handoff.modeled_open_timestamp + timedelta(days=1)
        handoff = replace(self.handoff, interval=replace(self.handoff.interval, timestamp=at), modeled_open_timestamp=at,
                          signal=replace(self.handoff.signal, scheduled_interval=at))
        self.assertTrue(self.result(handoff).consumed)

    def test_insufficient_completed_context_consumes(self):
        handoff, context, calendar, _ = handoff_fixture(scheduled=datetime(2026, 10, 8, 10, 14, tzinfo=NEW_YORK))
        self.assertEqual(self.result(handoff, context, calendar).reason, R.ENTRY_INSUFFICIENT_15M_CONTEXT)

    def test_context_lower_high_rejection_has_comparison_evidence(self):
        items = tuple(replace(item, interval=replace(item.interval, high=D('2')))
                      if item.interval.timestamp < self.handoff.modeled_open_timestamp - timedelta(minutes=30)
                      else item for item in self.context.minutes)
        result = self.result(context=replace(self.context, minutes=items))
        self.assertEqual(result.reason, R.ENTRY_15M_STRUCTURE_BEARISH)
        comparison = next(e for e in result.audit if e.event_type == 'ENTRY_15M_COMPARISON')
        self.assertFalse(dict(comparison.details)['high_passed'])

    def test_weekly_room_rejection_uses_exact_nearest_price(self):
        history = weekly_history(self.handoff.modeled_open_timestamp, self.calendar,
                                 highs=['.8', '.9', '1.14', '.9', '.8'] + ['.8'] * 7)
        result = self.result(context=replace(self.context, weekly=history))
        self.assertEqual(result.reason, R.ENTRY_INSUFFICIENT_WEEKLY_ROOM)
        audit = next(e for e in result.audit if e.event_type == 'ENTRY_WEEKLY_RESULT')
        self.assertEqual(dict(audit.details)['nearest_resistance'], D('1.14'))
        self.assertTrue(result.consumed)

    def test_exact_five_percent_weekly_room_passes_entry_integration(self):
        history = weekly_history(self.handoff.modeled_open_timestamp, self.calendar,
                                 highs=['.8', '.9', '1.1655', '.9', '.8'] + ['.8'] * 7)
        result = self.result(context=replace(self.context, weekly=history))
        self.assertEqual(result.outcome, O.ENTRY_APPROVED)
        self.assertEqual(result.approval.weekly.room_pct, Fraction(5))

    def test_scheduled_or_future_minutes_in_context_cannot_influence_approval(self):
        from tradingbot_backtest.entry_context import ContextMinute
        future = ContextMinute(self.handoff.interval, self.handoff.interval.end, 'fixture-basis')
        context = replace(self.context, minutes=self.context.minutes + (future,))
        self.assertEqual(self.result(context=context), self.result())

    def test_no_resistance_result_is_preserved_in_approval_and_audit(self):
        result = self.result()
        self.assertEqual(result.approval.weekly.status, R.NO_IDENTIFIED_WEEKLY_RESISTANCE)
        audit = next(e for e in result.audit if e.event_type == 'ENTRY_WEEKLY_RESULT')
        self.assertEqual(dict(audit.details)['status'], R.NO_IDENTIFIED_WEEKLY_RESISTANCE.value)

    def test_incompatible_share_units_reject_without_adjustment(self):
        context = replace(self.context, eligibility=replace(self.context.eligibility, share_basis_id='unrelated-split-basis'))
        result = self.result(context=context)
        self.assertEqual(result.reason, R.CORPORATE_ACTION_DATA_UNAVAILABLE)
        self.assertEqual(self.handoff.interval.open, D('1.11'))

    def test_unverified_adjustment_method_rejects(self):
        context = replace(self.context, eligibility=replace(self.context.eligibility, normalization_verified=False))
        self.assertEqual(self.result(context=context).reason, R.CORPORATE_ACTION_DATA_UNAVAILABLE)

    def test_future_corporate_action_cannot_normalize_current_inputs(self):
        context = replace(self.context, eligibility=replace(self.context.eligibility,
                          normalization_effective_at=(self.handoff.modeled_open_timestamp + timedelta(days=1),)))
        self.assertEqual(self.result(context=context).reason, R.CORPORATE_ACTION_DATA_UNAVAILABLE)

    def test_missing_prior_close_rejects_with_explicit_recorded_cause(self):
        context = replace(self.context, eligibility=replace(self.context.eligibility, prior_regular_close=None))
        result = self.result(context=context)
        self.assertTrue(result.consumed)
        self.assertEqual(result.description, 'PRIOR_CLOSE_DATA_UNAVAILABLE')
        self.assertEqual(dict(result.audit[-1].details)['description'], 'PRIOR_CLOSE_DATA_UNAVAILABLE')

    def test_conflicting_prior_close_rejects_as_reference_integrity_issue(self):
        context = replace(self.context, eligibility=replace(self.context.eligibility, prior_regular_close=D('.91')))
        self.assertEqual(self.result(context=context).description, 'PRIOR_CLOSE_REFERENCE_CONFLICT')

    def test_static_gate_must_still_pass(self):
        context = replace(self.context, eligibility=replace(self.context.eligibility, static_eligible=False))
        self.assertEqual(self.result(context=context).description, 'ENTRY_STATIC_ELIGIBILITY_FAILED')

    def test_future_eligibility_input_is_not_used(self):
        context = replace(self.context, eligibility=replace(self.context.eligibility,
                          available_at=self.handoff.modeled_open_timestamp + timedelta(minutes=1)))
        self.assertEqual(self.result(context=context).description, 'OPENING_ELIGIBILITY_DATA_UNAVAILABLE')

    def test_unavailable_eligibility_values_cannot_change_rejection_or_audit(self):
        at = self.handoff.modeled_open_timestamp
        unavailable_at = at + timedelta(minutes=1)
        inputs = (
            replace(self.context.eligibility, available_at=unavailable_at),
            replace(self.context.eligibility, available_at=unavailable_at,
                    share_basis_id='unavailable-future-basis', normalization_verified=False,
                    normalization_effective_at=(at + timedelta(days=1),),
                    prior_regular_close=D('4.20'), static_eligible=False),
        )
        results = []
        for supplied in inputs:
            with self.subTest(basis=supplied.share_basis_id):
                validator = EntryValidator(self.calendar, run_id='entry-run', data_version='fixture')
                result = validator.evaluate(self.handoff, replace(self.context, eligibility=supplied))
                results.append(result)
                self.assertEqual(result.outcome, O.ENTRY_CONSUMED)
                self.assertEqual(result.description, 'OPENING_ELIGIBILITY_DATA_UNAVAILABLE')
                self.assertIsNone(result.approval)
                self.assertTrue(result.consumed)
                self.assertEqual(result.timestamp, at)
                provenance = next(e for e in result.audit if e.event_type == 'ENTRY_INPUT_PROVENANCE')
                self.assertEqual(dict(provenance.details),
                                 {'available_at': unavailable_at, 'status': 'UNAVAILABLE'})
                self.assertTrue(all(e.recorded_at == at for e in result.audit))
                self.assertFalse(any(e.event_type in ('ENTRY_B_BOUNDARY', 'ENTRY_DYNAMIC_ELIGIBILITY')
                                     for e in result.audit))
                with self.assertRaisesRegex(ValueError, 'one scheduled'):
                    validator.evaluate(self.handoff, self.context)
        self.assertEqual(results[0], results[1])

    def test_unavailable_listing_date_cannot_change_window_or_opening_audit(self):
        at = self.handoff.modeled_open_timestamp
        results = []
        for listed in (date(1990, 1, 3), date(2026, 9, 30)):
            with self.subTest(listing_date=listed):
                history = replace(self.context.weekly, listing_date=listed,
                                  listing_available_at=at + timedelta(minutes=1))
                validator = EntryValidator(self.calendar, run_id='entry-run', data_version='fixture')
                result = validator.evaluate(self.handoff, replace(self.context, weekly=history))
                results.append(result)
                self.assertEqual(result.reason, R.ENTRY_WEEKLY_DATA_UNAVAILABLE)
                self.assertEqual(result.outcome, O.ENTRY_CONSUMED)
                self.assertTrue(result.consumed)
                self.assertIsNone(result.approval)
                weekly = next(e for e in result.audit if e.event_type == 'ENTRY_WEEKLY_RESULT')
                self.assertEqual(dict(weekly.details)['candidate_weeks'], '')
                self.assertEqual(dict(weekly.details)['left_context_weeks'], '')
                self.assertFalse(any(e.event_type in ('ENTRY_WEEKLY_BAR', 'ENTRY_WEEKLY_CANDIDATE')
                                     for e in result.audit))
                self.assertTrue(all(e.recorded_at == at for e in result.audit))
                with self.assertRaisesRegex(ValueError, 'one scheduled'):
                    validator.evaluate(self.handoff, self.context)
        self.assertEqual(results[0], results[1])

    def test_utc_conversion_and_dst_preserve_opening_availability_and_sessions(self):
        # Literal UTC expectations straddle both U.S. DST changes in 2026.
        for month, day, utc_hour in ((3, 6, 15), (3, 9, 14), (10, 30, 14), (11, 2, 15)):
            with self.subTest(month=month, day=day):
                scheduled = datetime(2026, month, day, 10, 15, tzinfo=NEW_YORK)
                h, c, calendar, _ = handoff_fixture(scheduled=scheduled)
                expected_utc = datetime(2026, month, day, utc_hour, 15, tzinfo=timezone.utc)
                self.assertEqual(scheduled.astimezone(timezone.utc), expected_utc)
                c = replace(c, eligibility=replace(c.eligibility, available_at=scheduled))
                baseline = self.result(h, c, calendar)
                self.assertEqual(baseline.outcome, O.ENTRY_APPROVED)
                converted = replace(h, modeled_open_timestamp=expected_utc,
                                    interval=replace(h.interval, timestamp=expected_utc),
                                    signal=replace(h.signal, scheduled_interval=expected_utc,
                                                   d_confirmation_timestamp=expected_utc,
                                                   d=replace(h.signal.d, timestamp=expected_utc-timedelta(minutes=1))))
                utc_context = replace(c,
                    eligibility=replace(c.eligibility, available_at=expected_utc),
                    minutes=tuple(replace(item, interval=replace(item.interval,
                                      timestamp=item.interval.timestamp.astimezone(timezone.utc)),
                                      available_at=item.available_at.astimezone(timezone.utc)) for item in c.minutes),
                    weekly=replace(c.weekly, listing_available_at=c.weekly.listing_available_at.astimezone(timezone.utc),
                                   bars=tuple(replace(bar, completed_at=bar.completed_at.astimezone(timezone.utc),
                                                      available_at=bar.available_at.astimezone(timezone.utc))
                                              for bar in c.weekly.bars)))
                self.assertEqual(self.result(converted, utc_context, calendar), baseline)
                later = replace(utc_context, eligibility=replace(utc_context.eligibility,
                                available_at=expected_utc+timedelta(microseconds=1)))
                self.assertEqual(self.result(converted, later, calendar).description,
                                 'OPENING_ELIGIBILITY_DATA_UNAVAILABLE')

    def test_known_tick_reference_preserved_without_tick_or_trade_calculation(self):
        tick = TickReference('security-contract-snapshot', 'verified-source', self.context.eligibility.available_at)
        result = self.result(context=replace(self.context, tick_reference=tick))
        self.assertEqual(result.approval.tick_reference, tick)
        self.assertFalse(hasattr(result.approval, 'shares'))
        self.assertFalse(hasattr(result.approval, 'initial_stop'))

    def test_future_tick_reference_not_used_or_assumed(self):
        tick = TickReference('future-contract-snapshot', 'verified-source', self.handoff.modeled_open_timestamp + timedelta(days=1))
        result = self.result(context=replace(self.context, tick_reference=tick))
        self.assertEqual(result.outcome, O.ENTRY_APPROVED)
        self.assertIsNone(result.approval.tick_reference)

    def test_approval_is_immutable_and_contains_no_executable_position(self):
        result = self.result()
        with self.assertRaises(FrozenInstanceError):
            result.approval.reference_open = D('2')
        names = {field.name for field in fields(result.approval)}
        self.assertFalse(names & {'shares', 'cash', 'equity', 'position_count', 'fill_price', 'interval', 'handoff'})
        self.assertFalse(dict(result.audit[-1].details)['executed'])

    def test_validation_does_not_mutate_phase_four_or_account_state(self):
        before = self.detector.state, self.detector.c_state, self.detector.d_state
        self.result()
        self.assertEqual((self.detector.state, self.detector.c_state, self.detector.d_state), before)
        self.assertEqual(self.detector.state.lifecycle.value, 'PENDING_ENTRY')
        self.assertFalse(hasattr(self.detector, 'cash'))

    def test_opening_audits_use_only_opening_information_at_open(self):
        result = self.result()
        at = self.handoff.modeled_open_timestamp
        self.assertEqual(result.timestamp, at)
        self.assertTrue(all(e.recorded_at == at and e.modeled_event_at == at for e in result.audit))
        self.assertFalse(any(key in ('high', 'low', 'close', 'volume')
                             for event in result.audit if event.event_type == 'OPENING_REFERENCE'
                             for key, _ in event.details))
        self.assertTrue(all(item.interval.end <= at for block in result.approval.intraday.blocks
                            for item in block.constituents))

    def test_exact_comparisons_are_independent_of_decimal_precision(self):
        handoff = self.with_open('1.122')
        with localcontext() as context:
            context.prec = 2
            result = self.result(handoff)
        self.assertEqual(result.outcome, O.ENTRY_APPROVED)
        self.assertEqual(result.approval.b_extension, Fraction(1, 50))

    def test_entry_cannot_use_a_later_interval(self):
        handoff = replace(self.handoff, interval=replace(self.handoff.interval,
                          timestamp=self.handoff.interval.timestamp + timedelta(minutes=1)))
        self.assertEqual(self.result(handoff).reason, R.ENTRY_DATA_INVALID)

    def test_audit_rejection_contains_input_and_consumed_status(self):
        result = self.result(self.with_open('1.10'))
        event = next(e for e in result.audit if e.event_type == 'ENTRY_B_BOUNDARY')
        self.assertEqual(dict(event.details)['open'], D('1.10'))
        self.assertEqual(dict(event.details)['b'], D('1.10'))
        self.assertEqual(result.audit[-1].reason, R.ENTRY_OPEN_AT_OR_BELOW_B)
        self.assertTrue(dict(result.audit[-1].details)['consumed'])
