"""Hand-calculated Phase 6 prices/caps with real Phase 4/5 approvals."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta, timezone
from decimal import Decimal as D, localcontext
from fractions import Fraction
import unittest

from tradingbot_backtest.codes import ReasonCode as R
from tradingbot_backtest.config import FROZEN_V1
from tradingbot_backtest.entry_validation import EntryValidator
from tradingbot_backtest.numerics import add, subtract
from tradingbot_backtest.trade_construction import (HeldPosition, PortfolioSnapshot, TickDirection as Direction, TickPurpose as P,
    TickRule, TradeConstructor, sizing_limits)
from tests.entry_fixtures import handoff_fixture


class SuppliedTicks:
    def __init__(self, at, stop='.01', target='.01'):
        self.calls = []
        self.rules = {
            purpose: TickRule('security', 'fixture-basis', purpose, D(tick), D(0), D(0), None,
                             at-timedelta(days=1), None, at, 'verified-fixture-ticks',
                             'fixture historical order grid', True)
            for purpose, tick in ((P.INITIAL_STOP, stop), (P.TARGET_2R, target)) if tick is not None}

    def resolve(self, query):
        self.calls.append(query)
        return self.rules.get(query.purpose)


class SuppliedBandTicks:
    """Explicit fixture bands; enumerate matching metadata, never invent a grid."""
    def __init__(self, rules):
        self.rules = tuple(rules)
        self.calls = []

    def resolve(self, query):
        self.calls.append(('INITIAL', query, None, None))
        return tuple(rule for rule in self.rules if rule.purpose == query.purpose
                     and rule.minimum_price <= query.raw_price
                     and (rule.maximum_price is None or query.raw_price < rule.maximum_price))

    def resolve_adjacent(self, query, boundary, direction):
        self.calls.append(('ADJACENT', query, boundary, direction))
        return tuple(rule for rule in self.rules if rule.purpose == query.purpose
                     and (rule.maximum_price == boundary if direction == Direction.DOWN
                          else rule.minimum_price == boundary))


class ConstructionTests(unittest.TestCase):
    def setUp(self):
        self.handoff, context, calendar, _ = handoff_fixture()
        self.approval = EntryValidator(calendar, run_id='phase6', data_version='fixture').evaluate(
            self.handoff, context).approval
        self.at = self.approval.scheduled_timestamp
        self.ticks = SuppliedTicks(self.at)

    def account(self, equity='10000', bod='10000', exposure='0', count=0):
        equity, exposure = D(equity), D(exposure)
        if not count and exposure:
            count = 1
        values = [exposure / D(count)] * count if count else []
        positions = tuple(HeldPosition(f'prior-{i}', f'other-{i}', f'OTHER{i}', 1, value,
                                      self.at, self.at, 'verified-opening-source', 'other-share-basis')
                          for i, value in enumerate(values))
        return PortfolioSnapshot(self.at, self.at, D(bod), equity, subtract(equity, exposure), exposure,
                                 positions, True, 'verified-account-fixture')

    def result(self, approval=None, account=None, ticks=None):
        return TradeConstructor(run_id='phase6', data_version='fixture').construct(
            approval or self.approval, account or self.account(), ticks or self.ticks, trade_id='new-trade')

    def test_complete_trade_uses_supplied_ticks(self):
        result = self.result()
        self.assertEqual(result.description, 'SIMULATED_ENTRY_FILLED')
        self.assertEqual(result.position.stop_tick.tick_size, D('.01'))
        self.assertEqual([q.purpose for q in self.ticks.calls], [P.INITIAL_STOP, P.TARGET_2R])
        self.assertEqual([q.raw_price for q in self.ticks.calls], [D('1.05470'), D('1.23')])
        self.assertTrue(all(q.timestamp == self.at and q.security_id == 'security' for q in self.ticks.calls))

    def test_missing_stop_tick_consumes_without_fallback(self):
        result = self.result(ticks=SuppliedTicks(self.at, stop=None))
        self.assertEqual(result.reason, R.ENTRY_TICK_SIZE_UNAVAILABLE)
        self.assertTrue(result.consumed)
        self.assertIsNone(result.position)
        self.assertEqual(result.accepted_entry_delta, 0)

    def test_missing_target_tick_consumes(self):
        result = self.result(ticks=SuppliedTicks(self.at, target=None))
        self.assertEqual(result.reason, R.ENTRY_TARGET_TICK_SIZE_UNAVAILABLE)
        self.assertIsNone(result.position)

    def test_fractional_stop_and_target_ticks(self):
        p = self.result(ticks=SuppliedTicks(self.at, stop='.0001', target='.0003')).position
        self.assertEqual(p.executable_stop, D('1.0547'))
        self.assertEqual(p.risk_per_share, D('.0553'))
        self.assertEqual(p.raw_target, D('1.2206'))
        self.assertEqual(p.executable_target, D('1.2207'))
        self.assertGreaterEqual(p.effective_target_r, 2)

    def test_tick_grid_origin_is_supplied(self):
        self.ticks.rules[P.INITIAL_STOP] = replace(self.ticks.rules[P.INITIAL_STOP], grid_origin=D('.005'))
        self.assertEqual(self.result().position.executable_stop, D('1.045'))

    def test_unverified_or_corrupt_tick_rejects(self):
        for change in ({'verified': False}, {'data_quality_reason': 'ambiguous historical rule'}, {'tick_size': D(0)}):
            ticks = SuppliedTicks(self.at)
            ticks.rules[P.INITIAL_STOP] = replace(ticks.rules[P.INITIAL_STOP], **change)
            with self.subTest(change=change):
                result = self.result(ticks=ticks)
                self.assertEqual(result.reason, R.ENTRY_TICK_SIZE_UNAVAILABLE)

    def test_tick_identity_basis_and_purpose_must_match(self):
        for change in ({'security_id': 'different'}, {'share_basis_id': 'incompatible'}, {'purpose': P.TARGET_2R}):
            ticks = SuppliedTicks(self.at)
            ticks.rules[P.INITIAL_STOP] = replace(ticks.rules[P.INITIAL_STOP], **change)
            self.assertEqual(self.result(ticks=ticks).reason, R.ENTRY_TICK_SIZE_UNAVAILABLE)

    def test_tick_rejection_audits_specific_mismatch_and_supplied_cause(self):
        self.ticks.rules[P.INITIAL_STOP] = replace(self.ticks.rules[P.INITIAL_STOP], security_id='other-security',
                                                   data_quality_reason='provider contract conflict')
        result = self.result()
        details = dict(next(e for e in result.audit if e.event_type == 'ENTRY_TICK_RULE_REJECTED').details)
        self.assertEqual(details['failures'], 'SOURCE_DATA_QUALITY_FAILURE|SECURITY_ID_MISMATCH')
        metadata = dict(next(e for e in result.audit if e.event_type == 'ENTRY_TICK_METADATA').details)
        self.assertEqual(metadata['supplied_security'], 'other-security')
        self.assertEqual(metadata['data_quality_reason'], 'provider contract conflict')

    def test_unavailable_tick_values_do_not_leak_into_audit(self):
        results = []
        for tick in ('.01', '.005'):
            ticks = SuppliedTicks(self.at, stop=tick)
            ticks.rules[P.INITIAL_STOP] = replace(ticks.rules[P.INITIAL_STOP], available_at=self.at+timedelta(seconds=1))
            results.append(self.result(ticks=ticks))
        self.assertEqual(results[0], results[1])

    def test_tick_validity_start_inclusive_end_exclusive(self):
        self.ticks.rules[P.INITIAL_STOP] = replace(self.ticks.rules[P.INITIAL_STOP], valid_from=self.at)
        self.assertIsNotNone(self.result().position)
        self.ticks.rules[P.INITIAL_STOP] = replace(self.ticks.rules[P.INITIAL_STOP], valid_until=self.at)
        self.assertEqual(self.result().reason, R.ENTRY_TICK_SIZE_UNAVAILABLE)

    def test_future_effective_tick_rejects(self):
        self.ticks.rules[P.INITIAL_STOP] = replace(self.ticks.rules[P.INITIAL_STOP], valid_from=self.at+timedelta(seconds=1))
        self.assertEqual(self.result().reason, R.ENTRY_TICK_SIZE_UNAVAILABLE)

    def test_raw_price_outside_tick_band_rejects(self):
        self.ticks.rules[P.INITIAL_STOP] = replace(self.ticks.rules[P.INITIAL_STOP], minimum_price=D('2'))
        self.assertEqual(self.result().reason, R.ENTRY_TICK_SIZE_UNAVAILABLE)

    def test_stop_rounding_across_verified_band_rejects(self):
        self.ticks.rules[P.INITIAL_STOP] = replace(self.ticks.rules[P.INITIAL_STOP], minimum_price=D('1.052'))
        self.assertEqual(self.result().reason, R.ENTRY_TICK_SIZE_UNAVAILABLE)

    def test_target_rounding_across_verified_band_rejects(self):
        self.ticks.rules[P.TARGET_2R] = replace(self.ticks.rules[P.TARGET_2R], tick_size=D('.02'), maximum_price=D('1.235'))
        self.assertEqual(self.result().reason, R.ENTRY_TARGET_TICK_SIZE_UNAVAILABLE)

    def test_raw_stop_and_downward_rounding(self):
        p = self.result().position
        self.assertEqual(p.raw_stop, D('1.05470'))
        self.assertEqual(p.executable_stop, D('1.05'))
        self.assertLessEqual(p.executable_stop, p.raw_stop)

    def test_exact_tick_aligned_stop_unchanged(self):
        p = self.result(ticks=SuppliedTicks(self.at, stop='.00001')).position
        self.assertEqual(p.executable_stop, p.raw_stop)

    def test_rounded_stop_is_authoritative_for_risk(self):
        p = self.result().position
        self.assertEqual(p.risk_per_share, D('.06'))
        self.assertEqual(p.sizing.risk, 1666)

    def test_invalid_risk_or_zero_stop_rejects(self):
        for c in ('1.20', '.001'):
            result = self.result(approval=replace(self.approval, c_price=D(c)))
            self.assertEqual(result.description, 'INVALID_INITIAL_STOP_OR_RISK')
            self.assertIsNone(result.position)

    def test_raw_target_exact_and_aligned_target_unchanged(self):
        p = self.result().position
        self.assertEqual((p.raw_target, p.executable_target, p.effective_target_r), (D('1.23'), D('1.23'), Fraction(2)))

    def test_target_rounds_up_and_does_not_change_size(self):
        p = self.result(ticks=SuppliedTicks(self.at, target='.02')).position
        self.assertEqual(p.executable_target, D('1.24'))
        self.assertEqual(p.effective_target_r, Fraction(13, 6))
        self.assertEqual(p.quantity, self.result().position.quantity)

    def test_one_percent_risk_budget_uses_bod(self):
        p = self.result(account=self.account(equity='20000', bod='10000')).position
        self.assertEqual(p.risk_budget, D('100'))
        self.assertEqual(p.sizing.risk, 1666)

    def test_risk_quantity_floors(self):
        p = self.result(account=self.account(bod='100')).position
        self.assertEqual(p.sizing.risk, 16)
        self.assertEqual(p.quantity, 16)
        self.assertIs(type(p.quantity), int)

    def test_risk_exact_division(self):
        p = self.result(account=self.account(bod='120')).position
        self.assertEqual(p.quantity, 20)

    def test_less_than_one_risk_share_rejects(self):
        r = self.result(account=self.account(bod='5'))
        self.assertEqual(r.description, 'ENTRY_QUANTITY_BELOW_MINIMUM')
        limits = dict(next(e for e in r.audit if e.event_type == 'ENTRY_SIZING').details)
        self.assertEqual(limits['risk_quantity'], 0)

    def test_below_share_cap_unaffected(self):
        self.assertEqual(self.result(account=self.account(bod='3000')).position.quantity, 500)

    def test_above_share_cap_is_capped(self):
        p = self.result().position
        self.assertEqual(p.sizing.risk, 1666)
        self.assertEqual(p.quantity, 1000)

    def test_exactly_one_thousand_shares_allowed(self):
        p = self.result(account=self.account(bod='6000')).position
        self.assertEqual((p.sizing.risk, p.quantity), (1000, 1000))

    def test_exact_twenty_percent_position_value_allowed(self):
        p = self.result(account=self.account(equity='5550')).position
        self.assertEqual(p.position_value, D('1110'))
        self.assertEqual(p.position_value / p.account_before.current_equity, D('.2'))

    def test_position_value_cap_prevents_excess(self):
        p = self.result(account=self.account(equity='5000')).position
        self.assertEqual(p.quantity, 900)
        self.assertEqual(p.position_value, D('999'))

    def test_value_cap_uses_current_equity_not_bod(self):
        p = self.result(account=self.account(equity='1110', bod='10000')).position
        self.assertEqual(p.quantity, 200)

    def test_cash_cap_exact_and_insufficient_flooring_in_isolation(self):
        # In a consistent full account the 60% exposure cap binds before cash.
        # These independently test the required cash formula, not valid account states.
        for cash, expected in (('2.22', 2), ('2.21', 1), ('0', 0)):
            with self.subTest(cash=cash):
                limits = sizing_limits(D('1.11'), D('.06'), D('100'), D('10000'), D(cash), D(0))
                self.assertEqual(limits.cash, expected)
                self.assertEqual(limits.final_quantity, expected)

    def test_cash_is_not_negative_and_input_snapshot_is_unchanged(self):
        before = self.account()
        p = self.result(account=before).position
        self.assertEqual(before.available_cash, D('10000'))
        self.assertEqual(p.account_after.available_cash, D('8890'))
        self.assertGreaterEqual(p.account_after.available_cash, 0)

    def test_no_margin_or_binary_floats_in_snapshot(self):
        with self.assertRaises(ValueError):
            replace(self.account(), available_cash=D('-1'))
        with self.assertRaises(TypeError):
            replace(self.account(), available_cash=1.0)

    def test_inconsistent_cash_equity_exposure_rejects(self):
        r = self.result(account=replace(self.account(), available_cash=D('1')))
        self.assertEqual(r.description, 'INCONSISTENT_PORTFOLIO_VALUATION')

    def test_account_conflict_audit_retains_exact_supplied_and_computed_values(self):
        r = self.result(account=replace(self.account(), available_cash=D('1')))
        details = dict(next(e for e in r.audit if e.event_type == 'ENTRY_ACCOUNT_VALUATION_CONFLICT').details)
        self.assertEqual((details['supplied_equity'], details['computed_equity'], details['cash']),
                         (D('10000'), D('1'), D('1')))

    def test_no_prior_exposure(self):
        p = self.result().position
        self.assertEqual(p.account_before.exposure, D(0))
        self.assertEqual(p.account_after.exposure, D('1110'))

    def test_exact_sixty_percent_post_entry_allowed(self):
        p = self.result(account=self.account(exposure='4890')).position
        self.assertEqual(p.quantity, 1000)
        self.assertEqual(p.account_after.exposure, D('6000'))

    def test_existing_exposure_reduces_size_without_exceeding_cap(self):
        p = self.result(account=self.account(exposure='5000')).position
        self.assertEqual(p.sizing.exposure, 900)
        self.assertEqual(p.quantity, 900)
        self.assertEqual(p.account_after.exposure, D('5999'))

    def test_already_at_or_above_exposure_cap_rejects(self):
        for exposure in ('6000', '6001'):
            self.assertEqual(self.result(account=self.account(exposure=exposure)).description,
                             'TOTAL_EXPOSURE_ALREADY_AT_OR_ABOVE_CAP')

    def test_zero_one_two_existing_positions_can_enter(self):
        for count, exposure in ((0, '0'), (1, '1000'), (2, '2000')):
            p = self.result(account=self.account(exposure=exposure, count=count)).position
            self.assertEqual(len(p.account_after.positions), count+1)

    def test_three_existing_positions_reject(self):
        self.assertEqual(self.result(account=self.account(exposure='3000', count=3)).description,
                         'MAXIMUM_SIMULTANEOUS_POSITIONS')

    def test_same_security_or_ticker_rejects(self):
        account = self.account(exposure='1000')
        for change in ({'security_id': 'security'}, {'ticker': 'TEST'}):
            held = replace(account.positions[0], **change)
            self.assertEqual(self.result(account=replace(account, positions=(held,))).description,
                             'SAME_TICKER_POSITION_ALREADY_OPEN')

    def test_two_shares_minimum_passes(self):
        self.assertEqual(self.result(account=self.account(equity='11.10')).position.quantity, 2)

    def test_one_share_rejects(self):
        self.assertEqual(self.result(account=self.account(equity='5.55')).description, 'ENTRY_QUANTITY_BELOW_MINIMUM')

    def test_zero_shares_rejects(self):
        self.assertEqual(self.result(account=self.account(equity='5')).description, 'ENTRY_QUANTITY_BELOW_MINIMUM')

    def test_supplied_daily_permission_blocks_fill(self):
        r = self.result(account=replace(self.account(), new_entries_permitted=False, permission_reason='daily net loss lockout'))
        self.assertEqual(r.description, 'SUPPLIED_DAILY_ENTRY_LOCKOUT')
        self.assertTrue(r.consumed)
        self.assertEqual(self.ticks.calls, [])
        self.assertIn('daily net loss lockout', str(r.audit))

    def test_missing_held_open_blocks_all_new_entries(self):
        account = self.account(exposure='1000')
        held = replace(account.positions[0], current_open=None, data_quality_reason='verified no-trade OPEN unavailable')
        r = self.result(account=replace(account, positions=(held,)))
        self.assertEqual(r.description, 'PORTFOLIO_VALUATION_UNAVAILABLE')
        self.assertIn('verified no-trade OPEN unavailable', str(r.audit))

    def test_stale_or_future_held_mark_cannot_value_entry(self):
        account = self.account(exposure='1000')
        for change in ({'opening_timestamp': self.at-timedelta(minutes=1)},
                       {'available_at': self.at+timedelta(microseconds=1)},
                       {'available_at': self.at-timedelta(microseconds=1)},
                       {'data_quality_reason': 'corrupt opening trade'}):
            self.assertEqual(self.result(account=replace(account, positions=(replace(account.positions[0], **change),))).description,
                             'PORTFOLIO_VALUATION_UNAVAILABLE')

    def test_future_account_metadata_does_not_enter_audit(self):
        account = replace(self.account(), available_at=self.at+timedelta(seconds=1))
        first = self.result(account=account)
        second = self.result(account=replace(account, beginning_of_day_equity=D('20000')))
        self.assertEqual(first, second)
        self.assertEqual(first.description, 'PORTFOLIO_STATE_UNAVAILABLE')

    def test_future_held_quality_values_do_not_enter_opening_audit(self):
        account = self.account(exposure='1000')
        results = []
        for cause in ('later provider correction', 'later corrupt opening trade'):
            held = replace(account.positions[0], available_at=self.at+timedelta(seconds=1), data_quality_reason=cause)
            results.append(self.result(account=replace(account, positions=(held,))))
        self.assertEqual(results[0], results[1])

    def test_stale_account_rejects(self):
        self.assertEqual(self.result(account=replace(self.account(), timestamp=self.at-timedelta(minutes=1))).description,
                         'PORTFOLIO_TIMESTAMP_MISMATCH')

    def test_whole_quantity_fills_at_open_and_zero_costs(self):
        p = self.result().position
        self.assertTrue(p.full_fill)
        self.assertEqual(p.entry_price, self.approval.reference_open)
        self.assertEqual((p.commission, p.fees, p.slippage), (D(0), D(0), D(0)))

    def test_preserves_fill_precision_without_artificial_rounding(self):
        p = self.result(approval=replace(self.approval, reference_open=D('1.110123'))).position
        self.assertEqual(p.entry_price, D('1.110123'))
        self.assertEqual(p.risk_per_share, D('.060123'))

    def test_account_effect_preserves_equity_and_generates_no_realized_loss(self):
        r = self.result()
        p = r.position
        self.assertEqual(p.account_after.current_equity, p.account_before.current_equity)
        self.assertEqual(add(p.account_after.available_cash, p.account_after.exposure), p.account_after.current_equity)
        self.assertEqual(r.accepted_entry_delta, 1)
        details = dict(r.audit[-1].details)
        self.assertEqual((details['completed_trade_delta'], details['realized_pnl_delta']), (0, D(0)))

    def test_scheduled_future_fields_cannot_change_construction(self):
        # Actual Phase 4 -> Phase 5 -> Phase 6 pipeline with each later field varied.
        _, context, calendar, _ = handoff_fixture()
        baseline = self.result()
        for field, value in (('high', D('100')), ('low', D('.01')), ('close', D('.96')), ('volume', D('999999'))):
            with self.subTest(field=field):
                h = replace(self.handoff, interval=replace(self.handoff.interval, **{field: value}))
                approval = EntryValidator(calendar, run_id='phase6', data_version='fixture').evaluate(h, context).approval
                self.assertEqual(self.result(approval=approval), baseline)

    def test_record_is_immutable_and_retains_approval_and_provenance(self):
        p = self.result().position
        self.assertIs(p.approval, self.approval)
        self.assertEqual((p.trade_id, p.run_id, p.data_version), ('new-trade', 'phase6', 'fixture'))
        with self.assertRaises(FrozenInstanceError):
            p.quantity = 1

    def test_audit_captures_all_caps_timestamps_and_normalizations(self):
        r = self.result()
        self.assertTrue(all(e.modeled_event_at == self.at and e.recorded_at == self.at for e in r.audit))
        self.assertTrue(all(e.interval_start == self.at and e.interval_end == self.at+timedelta(minutes=1) for e in r.audit))
        caps = dict(next(e for e in r.audit if e.event_type == 'ENTRY_SIZING').details)
        self.assertEqual({key:caps[key] for key in ('risk_quantity','share_cap','value_quantity','cash_quantity','exposure_quantity')},
                         {'risk_quantity':1666,'share_cap':1000,'value_quantity':1801,'cash_quantity':9009,'exposure_quantity':5405})
        self.assertEqual(sum(e.event_type == 'ENTRY_NORMALIZED_LEVEL' for e in r.audit), 2)
        self.assertEqual(r.audit[-1].event_type, 'SIMULATED_ENTRY_FILL')

    def test_rejected_setup_cannot_retry(self):
        constructor = TradeConstructor(run_id='phase6', data_version='fixture')
        constructor.construct(self.approval, self.account(), SuppliedTicks(self.at, stop=None), trade_id='new-trade')
        with self.assertRaisesRegex(ValueError, 'one construction'):
            constructor.construct(self.approval, self.account(), self.ticks, trade_id='other-id')

    def test_successful_setup_cannot_fill_twice(self):
        constructor = TradeConstructor(run_id='phase6', data_version='fixture')
        constructor.construct(self.approval, self.account(), self.ticks, trade_id='new-trade')
        with self.assertRaisesRegex(ValueError, 'one construction'):
            constructor.construct(self.approval, self.account(), self.ticks, trade_id='new-trade')

    def test_only_phase_five_approval_is_accepted(self):
        with self.assertRaises(TypeError):
            self.result(approval=self.handoff)

    def test_nonzero_cost_configuration_is_not_silently_ignored(self):
        for field in ('commission_baseline_usd', 'explicit_fee_baseline_usd', 'slippage_baseline'):
            with self.assertRaisesRegex(ValueError, 'ZERO-FRICTION'):
                TradeConstructor(run_id='phase6', data_version='fixture', config=replace(FROZEN_V1, **{field:D('.01')}))

    def test_precision_independent_construction(self):
        baseline = self.result()
        with localcontext() as ctx:
            ctx.prec = 2
            self.assertEqual(self.result(), baseline)

    def test_utc_tick_availability_matches_new_york_open(self):
        self.ticks.rules = {key:replace(rule, available_at=self.at.astimezone(timezone.utc),
                                      valid_from=rule.valid_from.astimezone(timezone.utc))
                            for key,rule in self.ticks.rules.items()}
        self.assertIsNotNone(self.result().position)

    def target_bands(self):
        base = self.ticks.rules[P.TARGET_2R]
        lower = replace(base, tick_size=D('.02'), maximum_price=D('1.24'), source_id='target-lower')
        upper = replace(base, minimum_price=D('1.24'), source_id='target-upper')
        return SuppliedBandTicks((self.ticks.rules[P.INITIAL_STOP], lower, upper))

    def stop_bands(self):
        base = self.ticks.rules[P.INITIAL_STOP]
        lower = replace(base, tick_size=D('.004'), maximum_price=D('1.052'), source_id='stop-lower')
        upper = replace(base, minimum_price=D('1.052'), source_id='stop-upper')
        return SuppliedBandTicks((lower, upper, self.ticks.rules[P.TARGET_2R]))

    def failed_gates(self, result):
        return dict(result.audit[-1].details)['failed_gates'].split('|')

    def test_cross_band_target_uses_verified_destination_and_audits_transition(self):
        ticks = self.target_bands()
        result = self.result(ticks=ticks)
        p = result.position
        self.assertIsNotNone(p)
        self.assertEqual((p.raw_target, p.executable_target), (D('1.23'), D('1.24')))
        self.assertEqual(p.target_tick.source_id, 'target-upper')
        transitions = [dict(record.details) for record in result.audit if record.event_type == 'ENTRY_TICK_BAND_TRANSITION']
        self.assertEqual(len(transitions), 1)
        self.assertEqual((transitions[0]['boundary'], transitions[0]['direction']), (D('1.24'), 'UP'))
        self.assertEqual((transitions[0]['from_tick'], transitions[0]['to_tick']), (D('.02'), D('.01')))
        metadata = [dict(record.details) for record in result.audit if record.event_type == 'ENTRY_TICK_METADATA']
        self.assertEqual([m['source_id'] for m in metadata], ['verified-fixture-ticks', 'target-lower', 'target-upper'])
        self.assertTrue(all(m['verified'] and m['available_at'] <= self.at for m in metadata))
        self.assertTrue(all(call[1].raw_price == D('1.23') for call in ticks.calls if call[1].purpose == P.TARGET_2R))

    def test_cross_band_stop_uses_nearest_downward_destination_grid(self):
        result = self.result(ticks=self.stop_bands())
        p = result.position
        self.assertIsNotNone(p)
        self.assertEqual((p.raw_stop, p.executable_stop, p.risk_per_share), (D('1.05470'), D('1.048'), D('.062')))
        self.assertEqual((p.stop_tick.tick_size, p.stop_tick.source_id), (D('.004'), 'stop-lower'))
        self.assertNotEqual(p.executable_stop, D('1.05'))  # Initial grid's extension is invalid in the lower band.
        transition = dict(next(r for r in result.audit if r.event_type == 'ENTRY_TICK_BAND_TRANSITION').details)
        self.assertEqual((transition['direction'], transition['boundary']), ('DOWN', D('1.052')))

    def test_cross_band_target_missing_destination_rejects_without_guess(self):
        ticks = self.target_bands()
        ticks.rules = ticks.rules[:-1]
        result = self.result(ticks=ticks)
        self.assertEqual(result.reason, R.ENTRY_TARGET_TICK_SIZE_UNAVAILABLE)
        self.assertIsNone(result.position)
        self.assertTrue(result.consumed)
        self.assertFalse(any(r.event_type == 'SIMULATED_ENTRY_FILL' for r in result.audit))

    def test_cross_band_stop_missing_destination_rejects_without_guess(self):
        ticks = self.stop_bands()
        ticks.rules = ticks.rules[1:]
        result = self.result(ticks=ticks)
        self.assertEqual(result.reason, R.ENTRY_TICK_SIZE_UNAVAILABLE)
        self.assertIsNone(result.position)
        self.assertTrue(result.consumed)

    def test_cross_band_target_checks_destination_alignment_not_old_candidate(self):
        ticks = self.target_bands()
        ticks.rules = ticks.rules[:-1] + (replace(ticks.rules[-1], grid_origin=D('.005')),)
        p = self.result(ticks=ticks).position
        self.assertEqual(p.executable_target, D('1.245'))
        self.assertEqual(p.target_tick.grid_origin, D('.005'))

    def test_cross_band_stop_excludes_upper_boundary_even_when_grid_aligned(self):
        p = self.result(ticks=self.stop_bands()).position
        # 1.052 is aligned with .004 but excluded from the lower band.
        self.assertEqual(p.executable_stop, D('1.048'))
        self.assertLess(p.executable_stop, p.stop_tick.maximum_price)

    def test_cross_band_target_visits_empty_intermediate_band(self):
        base = self.ticks.rules[P.TARGET_2R]
        ticks = SuppliedBandTicks((self.ticks.rules[P.INITIAL_STOP],
            replace(base, tick_size=D('.02'), maximum_price=D('1.231'), source_id='first'),
            replace(base, minimum_price=D('1.231'), maximum_price=D('1.235'), source_id='empty'),
            replace(base, tick_size=D('.005'), minimum_price=D('1.235'), source_id='last')))
        result = self.result(ticks=ticks)
        self.assertEqual(result.position.executable_target, D('1.235'))
        boundaries = [call[2] for call in ticks.calls if call[0] == 'ADJACENT']
        self.assertEqual(boundaries, [D('1.231'), D('1.235')])

    def test_cross_band_stop_visits_empty_intermediate_band(self):
        base = self.ticks.rules[P.INITIAL_STOP]
        ticks = SuppliedBandTicks((
            replace(base, tick_size=D('.002'), maximum_price=D('1.051'), source_id='last'),
            replace(base, minimum_price=D('1.051'), maximum_price=D('1.052'), source_id='empty'),
            replace(base, minimum_price=D('1.052'), source_id='first'), self.ticks.rules[P.TARGET_2R]))
        p = self.result(ticks=ticks).position
        self.assertEqual(p.executable_stop, D('1.05'))
        self.assertEqual([c[2] for c in ticks.calls if c[0] == 'ADJACENT'], [D('1.052'), D('1.051')])

    def test_cross_band_history_gap_cannot_be_skipped(self):
        ticks = self.target_bands()
        ticks.rules = ticks.rules[:-1] + (replace(ticks.rules[-1], minimum_price=D('1.241')),)
        self.assertEqual(self.result(ticks=ticks).reason, R.ENTRY_TARGET_TICK_SIZE_UNAVAILABLE)

    def test_cross_band_overlap_cannot_be_accepted_as_adjacent(self):
        ticks = self.target_bands()
        destination = replace(ticks.rules[-1], minimum_price=D('1.239'))
        ticks.resolve_adjacent = lambda query, boundary, direction: destination
        result = self.result(ticks=ticks)
        self.assertEqual(result.reason, R.ENTRY_TARGET_TICK_SIZE_UNAVAILABLE)
        failures = dict(next(r for r in result.audit if r.event_type == 'ENTRY_TICK_RULE_REJECTED').details)
        self.assertEqual(failures['failures'], 'ADJACENT_BAND_GAP_OR_OVERLAP')

    def test_conflicting_destination_rules_reject_instead_of_selecting_one(self):
        ticks = self.target_bands()
        ticks.rules += (replace(ticks.rules[-1], tick_size=D('.02'), source_id='conflicting-destination'),)
        result = self.result(ticks=ticks)
        self.assertEqual(result.reason, R.ENTRY_TARGET_TICK_SIZE_UNAVAILABLE)
        self.assertIn('CONFLICTING_TICK_BANDS', str(result.audit))
        self.assertIn('conflicting-destination', str(result.audit))

    def test_conflicting_initial_rules_reject_instead_of_selecting_one(self):
        ticks = self.stop_bands()
        ticks.rules += (replace(ticks.rules[1], tick_size=D('.02'), source_id='conflicting-initial'),)
        result = self.result(ticks=ticks)
        self.assertEqual(result.reason, R.ENTRY_TICK_SIZE_UNAVAILABLE)
        self.assertIn('CONFLICTING_TICK_BANDS', str(result.audit))

    def test_destination_metadata_unavailable_values_do_not_leak(self):
        for make_ticks, index, code in ((self.stop_bands, 0, R.ENTRY_TICK_SIZE_UNAVAILABLE),
                                       (self.target_bands, 2, R.ENTRY_TARGET_TICK_SIZE_UNAVAILABLE)):
            results = []
            for tick, basis, verified in (('.01', 'future-basis-a', True), ('.005', 'future-basis-b', False)):
                ticks = make_ticks()
                rules = list(ticks.rules)
                rules[index] = replace(rules[index], tick_size=D(tick), share_basis_id=basis, verified=verified,
                                       available_at=self.at+timedelta(microseconds=1))
                ticks.rules = tuple(rules)
                results.append(self.result(ticks=ticks))
            self.assertEqual(results[0], results[1])
            self.assertEqual(results[0].reason, code)
            self.assertNotIn('future-basis', str(results[0].audit))

    def test_destination_metadata_must_be_verified_effective_and_compatible(self):
        for make_ticks, index, code in ((self.stop_bands, 0, R.ENTRY_TICK_SIZE_UNAVAILABLE),
                                       (self.target_bands, 2, R.ENTRY_TARGET_TICK_SIZE_UNAVAILABLE)):
            for change in ({'verified': False}, {'valid_until': self.at},
                           {'valid_from': self.at+timedelta(microseconds=1)},
                           {'share_basis_id': 'incompatible'}, {'security_id': 'other'},
                           {'data_quality_reason': 'destination provider conflict'}):
                with self.subTest(code=code, change=change):
                    ticks = make_ticks()
                    rules = list(ticks.rules)
                    rules[index] = replace(rules[index], **change)
                    ticks.rules = tuple(rules)
                    self.assertEqual(self.result(ticks=ticks).reason, code)

    def test_all_consulted_bands_accept_availability_exactly_at_open_in_utc(self):
        ticks = self.target_bands()
        ticks.rules = tuple(replace(rule, available_at=self.at.astimezone(timezone.utc),
                                   valid_from=self.at.astimezone(timezone.utc)) for rule in ticks.rules)
        result = self.result(ticks=ticks)
        self.assertIsNotNone(result.position)
        metadata = [dict(r.details) for r in result.audit if r.event_type == 'ENTRY_TICK_METADATA']
        self.assertEqual(len(metadata), 3)
        self.assertTrue(all(m['available_at'] == self.at and m['valid_from'] == self.at for m in metadata))

    def test_raw_target_exactly_at_destination_boundary_stays_unchanged(self):
        ticks = self.target_bands()
        ticks.rules = (ticks.rules[0], replace(ticks.rules[1], maximum_price=D('1.23')),
                       replace(ticks.rules[2], minimum_price=D('1.23')))
        p = self.result(ticks=ticks).position
        self.assertEqual((p.raw_target, p.executable_target), (D('1.23'), D('1.23')))
        self.assertEqual(p.target_tick.source_id, 'target-upper')
        self.assertFalse(any(c[0] == 'ADJACENT' for c in ticks.calls))

    def test_raw_stop_exactly_at_destination_boundary_stays_unchanged(self):
        ticks = self.stop_bands()
        ticks.rules = (replace(ticks.rules[0], tick_size=D('.0001'), maximum_price=D('1.0547')),
                       replace(ticks.rules[1], tick_size=D('.0001'), minimum_price=D('1.0547')), ticks.rules[2])
        p = self.result(ticks=ticks).position
        self.assertEqual((p.raw_stop, p.executable_stop), (D('1.0547'), D('1.0547')))
        self.assertEqual(p.stop_tick.source_id, 'stop-upper')
        self.assertFalse(any(c[0] == 'ADJACENT' for c in ticks.calls))

    def test_multiple_failed_portfolio_gates_are_retained_with_stable_primary(self):
        account = self.account(exposure='6000', count=3, bod='5')
        same = replace(account.positions[0], security_id=self.approval.security_id, ticker=self.approval.ticker)
        account = replace(account, positions=(same,) + account.positions[1:])
        first, second = self.result(account=account), self.result(account=account)
        self.assertEqual(first, second)
        self.assertEqual(first.description, 'SAME_TICKER_POSITION_ALREADY_OPEN')
        self.assertEqual(set(self.failed_gates(first)), {
            'SAME_TICKER_POSITION_ALREADY_OPEN', 'MAXIMUM_SIMULTANEOUS_POSITIONS',
            'TOTAL_EXPOSURE_ALREADY_AT_OR_ABOVE_CAP', 'EXPOSURE_CAPACITY_BELOW_MINIMUM',
            'ENTRY_QUANTITY_BELOW_MINIMUM', 'RISK_QUANTITY_BELOW_MINIMUM'})
        self.assertTrue(first.consumed)
        self.assertIsNone(first.position)
        self.assertEqual(first.accepted_entry_delta, 0)
        self.assertFalse(any(r.event_type == 'SIMULATED_ENTRY_FILL' for r in first.audit))
        constructor = TradeConstructor(run_id='phase6', data_version='fixture')
        constructor.construct(self.approval, account, self.ticks, trade_id='first')
        with self.assertRaisesRegex(ValueError, 'one construction'):
            constructor.construct(self.approval, self.account(), self.ticks, trade_id='retry')

    def test_lockout_primary_still_preserves_other_known_failed_account_gates(self):
        account = self.account(exposure='6000', count=3)
        same = replace(account.positions[0], security_id=self.approval.security_id)
        account = replace(account, positions=(same,) + account.positions[1:], new_entries_permitted=False,
                          permission_reason='supplied loss streak')
        result = self.result(account=account)
        self.assertEqual(result.description, 'SUPPLIED_DAILY_ENTRY_LOCKOUT')
        self.assertTrue({'SUPPLIED_DAILY_ENTRY_LOCKOUT', 'SAME_TICKER_POSITION_ALREADY_OPEN',
                         'MAXIMUM_SIMULTANEOUS_POSITIONS', 'TOTAL_EXPOSURE_ALREADY_AT_OR_ABOVE_CAP',
                         'EXPOSURE_CAPACITY_BELOW_MINIMUM', 'ENTRY_QUANTITY_BELOW_MINIMUM'}
                        <= set(self.failed_gates(result)))
        self.assertNotIn('RISK_QUANTITY_BELOW_MINIMUM', self.failed_gates(result))
        self.assertEqual(self.ticks.calls, [])
        self.assertIn('ENTRY_UNEVALUATED_GATES', str(result.audit))
        self.assertTrue(result.consumed)

    def test_all_insufficient_sizing_capacities_are_audited(self):
        # Cash + three held marks = 5.55; marks are 1.85 each, so no inconsistency.
        account = self.account(equity='5.55', exposure='5.55', count=3, bod='5')
        result = self.result(account=account)
        self.assertEqual(result.description, 'MAXIMUM_SIMULTANEOUS_POSITIONS')
        self.assertTrue({'RISK_QUANTITY_BELOW_MINIMUM', 'POSITION_VALUE_CAPACITY_BELOW_MINIMUM',
                         'CASH_CAPACITY_BELOW_MINIMUM', 'EXPOSURE_CAPACITY_BELOW_MINIMUM',
                         'ENTRY_QUANTITY_BELOW_MINIMUM'} <= set(self.failed_gates(result)))
        sizes = dict(next(r for r in result.audit if r.event_type == 'ENTRY_SIZING').details)
        self.assertEqual((sizes['risk_quantity'], sizes['share_cap'], sizes['value_quantity'],
                          sizes['cash_quantity'], sizes['exposure_quantity']), (0, 1000, 1, 0, 0))
        self.assertNotIn('SHARE_CAP_BELOW_MINIMUM', self.failed_gates(result))

    def test_missing_stop_does_not_fabricate_risk_failure(self):
        result = self.result(account=self.account(equity='5.55'), ticks=SuppliedTicks(self.at, stop=None))
        self.assertEqual(result.reason, R.ENTRY_TICK_SIZE_UNAVAILABLE)
        self.assertTrue({'POSITION_VALUE_CAPACITY_BELOW_MINIMUM', 'ENTRY_QUANTITY_BELOW_MINIMUM',
                         R.ENTRY_TICK_SIZE_UNAVAILABLE.value} <= set(self.failed_gates(result)))
        self.assertNotIn('RISK_QUANTITY_BELOW_MINIMUM', self.failed_gates(result))
        self.assertFalse(any(r.event_type == 'ENTRY_SIZING' for r in result.audit))

    def test_missing_target_retains_evaluable_risk_quantity_failure(self):
        result = self.result(account=self.account(bod='5'), ticks=SuppliedTicks(self.at, target=None))
        self.assertEqual(result.reason, R.ENTRY_TARGET_TICK_SIZE_UNAVAILABLE)
        self.assertTrue({'RISK_QUANTITY_BELOW_MINIMUM', 'ENTRY_QUANTITY_BELOW_MINIMUM',
                         R.ENTRY_TARGET_TICK_SIZE_UNAVAILABLE.value} <= set(self.failed_gates(result)))

    def test_primary_portfolio_reason_survives_secondary_tick_failure(self):
        result = self.result(account=self.account(count=3, exposure='3000'), ticks=SuppliedTicks(self.at, stop=None))
        self.assertEqual(result.description, 'MAXIMUM_SIMULTANEOUS_POSITIONS')
        self.assertIsNone(result.reason)
        self.assertEqual(set(self.failed_gates(result)), {'MAXIMUM_SIMULTANEOUS_POSITIONS',
                                                         R.ENTRY_TICK_SIZE_UNAVAILABLE.value})

    def test_sub_dollar_entry_through_real_phase_four_five_handoff(self):
        handoff, context, calendar, _ = handoff_fixture(open_price='.111', factor='.1')
        approval = EntryValidator(calendar, run_id='phase6', data_version='fixture').evaluate(handoff, context).approval
        self.assertIsNotNone(approval)
        p = self.result(approval=approval, ticks=SuppliedTicks(self.at, stop='.001', target='.001')).position
        self.assertEqual((p.entry_price, p.executable_stop, p.risk_per_share, p.executable_target),
                         (D('.111'), D('.105'), D('.006'), D('.123')))
        self.assertEqual(p.quantity, 1000)

    def test_tiny_positive_risk_large_raw_quantity_is_safely_capped(self):
        handoff, context, calendar, _ = handoff_fixture(open_price='1.11E-12', factor='1E-12')
        approval = EntryValidator(calendar, run_id='phase6', data_version='fixture').evaluate(handoff, context).approval
        self.assertIsNotNone(approval)
        p = self.result(approval=approval, ticks=SuppliedTicks(self.at, stop='1E-14', target='1E-14')).position
        self.assertEqual(p.risk_per_share, D('6E-14'))
        self.assertEqual(p.sizing.risk, 1666666666666666)
        self.assertEqual(p.quantity, 1000)
        self.assertIs(type(p.sizing.risk), int)

    def test_exactly_zero_risk_rejects_without_repairs(self):
        result = self.result(approval=replace(self.approval, c_price=D('1.12')))
        self.assertEqual(result.description, 'INVALID_INITIAL_STOP_OR_RISK')
        self.assertTrue(result.consumed)
        values = dict(next(r for r in result.audit if r.event_type == 'ENTRY_INVALID_RISK').details)
        self.assertEqual((values['entry'], values['stop'], values['risk_per_share']), (D('1.11'), D('1.11'), D(0)))

    def test_equal_competing_caps_are_deterministic_and_individually_audited(self):
        account = self.account(equity='5550', bod='6000', exposure='2220')
        first, second = self.result(account=account), self.result(account=account)
        self.assertEqual(first, second)
        self.assertEqual(first.position.quantity, 1000)
        sizes = dict(next(r for r in first.audit if r.event_type == 'ENTRY_SIZING').details)
        self.assertEqual({key: sizes[key] for key in ('risk_quantity', 'share_cap', 'value_quantity', 'exposure_quantity')},
                         {'risk_quantity': 1000, 'share_cap': 1000, 'value_quantity': 1000, 'exposure_quantity': 1000})
        self.assertEqual(sizes['cash_quantity'], 3000)

    def test_unavailable_intermediate_band_cannot_be_skipped_for_valid_destination(self):
        base = self.ticks.rules[P.TARGET_2R]
        ticks = SuppliedBandTicks((self.ticks.rules[P.INITIAL_STOP],
            replace(base, tick_size=D('.02'), maximum_price=D('1.231'), source_id='first'),
            replace(base, minimum_price=D('1.231'), maximum_price=D('1.235'), source_id='future-intermediate',
                    available_at=self.at+timedelta(microseconds=1)),
            replace(base, tick_size=D('.005'), minimum_price=D('1.235'), source_id='available-final')))
        result = self.result(ticks=ticks)
        self.assertEqual(result.reason, R.ENTRY_TARGET_TICK_SIZE_UNAVAILABLE)
        self.assertEqual([c[2] for c in ticks.calls if c[0] == 'ADJACENT'], [D('1.231')])
        self.assertNotIn('available-final', str(result.audit))
        self.assertNotIn('future-intermediate', str(result.audit))

    def test_cross_band_normalization_is_independent_of_decimal_context(self):
        stop_baseline = self.result(ticks=self.stop_bands())
        target_baseline = self.result(ticks=self.target_bands())
        with localcontext() as ctx:
            ctx.prec = 2
            self.assertEqual(self.result(ticks=self.stop_bands()), stop_baseline)
            self.assertEqual(self.result(ticks=self.target_bands()), target_baseline)
