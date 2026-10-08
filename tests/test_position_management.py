"""Hand-calculated exits using actual Phase 4/5/6 construction, offline only."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta, timezone
from decimal import Decimal as D, localcontext
from fractions import Fraction
import unittest

from tradingbot_backtest.codes import ReasonCode as R, RunStatus
from tradingbot_backtest.entry_validation import EntryValidator
from tradingbot_backtest.market import MarketInterval, IntervalClassification as K
from tradingbot_backtest.position_management import (
    ExitReason as E, ManagementStatus as M, PositionManager, TradeOutcome as O,
)
from tradingbot_backtest.states import StrategyState as S
from tradingbot_backtest.trade_construction import TickPurpose as P, TradeConstructor
from tests.entry_fixtures import Calendar, handoff_fixture
from tests import test_trade_construction as construction_fixtures


class ManagementTicks:
    def __init__(self, at):
        base = construction_fixtures.SuppliedTicks(at).rules[P.INITIAL_STOP]
        self.rules = {purpose: (replace(base, purpose=purpose),) for purpose in (P.BREAKEVEN_STOP, P.TRAILING_STOP)}
        self.calls = []

    def resolve(self, query):
        self.calls.append(query)
        return tuple(rule for rule in self.rules.get(query.purpose, ())
                     if rule.minimum_price <= query.raw_price
                     and (rule.maximum_price is None or query.raw_price < rule.maximum_price))

    def resolve_adjacent(self, query, boundary, direction):
        return tuple(rule for rule in self.rules.get(query.purpose, ()) if rule.maximum_price == boundary)


class PositionManagementTests(unittest.TestCase):
    def make_manager(self, quantity=100, close_hour=16, prime=True):
        fixture = construction_fixtures.ConstructionTests(); fixture.setUp()
        calendar = Calendar(close_hour=close_hour)
        approval = fixture.approval
        if close_hour != 16:
            handoff, context, _, _ = handoff_fixture(calendar=calendar)
            approval = EntryValidator(calendar, run_id='phase7', data_version='fixture').evaluate(handoff, context).approval
        account = fixture.account(bod=str(quantity*6))
        position = TradeConstructor(run_id='phase7', data_version='fixture').construct(
            approval, account, construction_fixtures.SuppliedTicks(approval.scheduled_timestamp), trade_id='managed').position
        self.assertEqual(position.quantity, quantity)
        ticks = ManagementTicks(position.entry_timestamp)
        manager = PositionManager(position, calendar, ticks)
        if prime:
            manager.feed(self.bar(manager, opening='1.11', high='1.12', low='1.11', close='1.11'))
        return manager, ticks

    def bar(self, manager, opening='1.15', high='1.20', low='1.13', close='1.16', kind=K.TRADED, cause=None, at=None):
        fields = dict(security_id='security', ticker='TEST', timestamp=at or manager.next_interval,
                      classification=kind, source_id='verified-exit-fixture')
        if kind == K.TRADED:
            fields.update(open=D(opening), high=D(high), low=D(low), close=D(close), volume=D(100))
        elif kind == K.NO_TRADE:
            fields.update(volume=D(0), no_trade_verified=True)
        else:
            fields.update(data_quality_reason=cause or 'fixture source gap')
        return MarketInterval(**fields)

    def partial(self, manager, opening=False):
        return manager.feed(self.bar(manager, opening='1.23' if opening else '1.15',
                                     high='1.24', low='1.12', close='1.20'))

    def advance(self, manager, timestamp):
        while manager.next_interval < timestamp:
            manager.feed(self.bar(manager, kind=K.NO_TRADE))

    def test_initial_low_below_stop_exits_at_stop(self):
        manager, _ = self.make_manager()
        result = manager.feed(self.bar(manager, low='1.04'))
        self.assertEqual((result.state.status, result.state.completed.final_exit_price), (M.CLOSED, D('1.05')))

    def test_initial_low_exact_stop_exits(self):
        manager, _ = self.make_manager()
        self.assertEqual(manager.feed(self.bar(manager, low='1.05')).exit_fills[0].simulated_fill_price, D('1.05'))

    def test_stop_through_open_above_stop_uses_stop(self):
        manager, _ = self.make_manager()
        fill = manager.feed(self.bar(manager, opening='1.08', low='1.02', close='1.06')).exit_fills[0]
        self.assertEqual((fill.reference_price, fill.gap, fill.modeled_event_at), (D('1.05'), False, None))

    def test_open_exact_stop_exits_at_modeled_open(self):
        manager, _ = self.make_manager()
        bar = self.bar(manager, opening='1.05', low='1.04', close='1.06')
        fill = manager.on_open(bar).exit_fills[0]
        self.assertEqual((fill.reference_price, fill.modeled_event_at, fill.available_at), (D('1.05'), bar.timestamp, bar.timestamp))

    def test_gap_below_stop_uses_actual_open(self):
        manager, _ = self.make_manager()
        fill = manager.feed(self.bar(manager, opening='1.00', low='.99', close='1.06')).exit_fills[0]
        self.assertEqual((fill.reference_price, fill.gap, fill.gross_pnl), (D('1.00'), True, D('-11')))

    def test_no_stop_hit_preserves_open_position(self):
        manager, _ = self.make_manager()
        state = manager.feed(self.bar(manager)).state
        self.assertEqual((state.remaining_quantity, state.active_stop, state.phase), (100, D('1.05'), S.OPEN_POSITION))

    def test_entry_candle_intrabar_stop_is_managed(self):
        manager, _ = self.make_manager(prime=False)
        state = manager.feed(self.bar(manager, opening='1.11', low='1.04')).state
        self.assertEqual(state.status, M.CLOSED)

    def test_target_exact_touch_sells_partial_at_target(self):
        manager, _ = self.make_manager()
        fill = manager.feed(self.bar(manager, high='1.23')).exit_fills[0]
        self.assertEqual((fill.reason, fill.reference_price, fill.quantity), (E.PARTIAL_2R, D('1.23'), 50))

    def test_target_above_touch_uses_fixed_target(self):
        manager, _ = self.make_manager()
        self.assertEqual(self.partial(manager).exit_fills[0].reference_price, D('1.23'))

    def test_target_gap_exact_open_fills_open(self):
        manager, _ = self.make_manager()
        bar = self.bar(manager, opening='1.23', high='1.25', low='1.20', close='1.24')
        fill = manager.on_open(bar).exit_fills[0]
        self.assertEqual((fill.reference_price, fill.modeled_event_at), (D('1.23'), bar.timestamp))

    def test_target_gap_above_open_fills_open(self):
        manager, _ = self.make_manager()
        fill = manager.feed(self.bar(manager, opening='1.30', high='1.31', low='1.20', close='1.25')).exit_fills[0]
        self.assertEqual((fill.reference_price, fill.gap, fill.gross_pnl), (D('1.30'), True, D('9.50')))

    def test_same_bar_original_stop_wins_over_target(self):
        manager, ticks = self.make_manager()
        result = manager.feed(self.bar(manager, high='1.24', low='1.04'))
        self.assertEqual(len(result.exit_fills), 1)
        self.assertEqual((result.exit_fills[0].reason, result.exit_fills[0].quantity), (E.INITIAL_STOP, 100))
        self.assertFalse(result.state.partial_filled)
        self.assertEqual(ticks.calls, [])
        self.assertIn('ORIGINAL_STOP_FIRST', str(result.audit))

    def test_same_bar_exact_stop_and_target_touches_stop_wins(self):
        manager, _ = self.make_manager()
        result = manager.feed(self.bar(manager, high='1.23', low='1.05'))
        self.assertEqual(result.state.completed.net_pnl, D('-6'))

    def test_known_target_open_precedes_subsequent_original_stop_low(self):
        manager, _ = self.make_manager()
        result = manager.feed(self.bar(manager, opening='1.24', high='1.25', low='1.04'))
        self.assertEqual([fill.reason for fill in result.exit_fills], [E.PARTIAL_2R, E.BREAKEVEN_STOP])
        self.assertEqual([fill.reference_price for fill in result.exit_fills], [D('1.24'), D('1.11')])

    def test_known_stop_open_precedes_subsequent_target_high(self):
        manager, _ = self.make_manager()
        result = manager.feed(self.bar(manager, opening='1.04', high='1.24', low='1.03'))
        self.assertEqual([fill.reason for fill in result.exit_fills], [E.INITIAL_STOP])

    def test_same_bar_2r_breakeven_ambiguity_closes_runner(self):
        manager, _ = self.make_manager()
        result = manager.feed(self.bar(manager, high='1.24', low='1.10'))
        self.assertEqual([fill.reference_price for fill in result.exit_fills], [D('1.23'), D('1.11')])
        self.assertIn(R.SAME_BAR_2R_BREAKEVEN_AMBIGUITY.value, str(result.audit))
        self.assertEqual(result.state.completed.net_pnl, D('6'))

    def test_same_bar_exact_target_and_breakeven_touches_close_runner(self):
        manager, _ = self.make_manager()
        bar = self.bar(manager, high='1.23', low='1.11')
        result = manager.feed(bar)
        self.assertEqual([fill.reason for fill in result.exit_fills], [E.PARTIAL_2R, E.BREAKEVEN_STOP])
        self.assertEqual([fill.quantity for fill in result.exit_fills], [50, 50])
        self.assertEqual([fill.simulated_fill_price for fill in result.exit_fills], [D('1.23'), D('1.11')])
        self.assertTrue(all(fill.available_at == bar.end and fill.modeled_event_at is None
                            for fill in result.exit_fills))
        self.assertEqual((result.state.status, result.state.completed.net_pnl), (M.CLOSED, D('6')))
        ambiguity = next(record for record in result.audit
                         if record.event_type == 'SAME_BAR_2R_BREAKEVEN_AMBIGUITY')
        self.assertEqual(ambiguity.reason, R.SAME_BAR_2R_BREAKEVEN_AMBIGUITY)
        self.assertEqual(dict(ambiguity.details)['executable_breakeven'], D('1.11'))
        self.assertEqual(ambiguity.recorded_at, bar.end)

    def test_even_quantity_split(self):
        manager, _ = self.make_manager()
        state = self.partial(manager).state
        self.assertEqual((state.fills[0].quantity, state.remaining_quantity), (50, 50))

    def test_odd_quantity_split(self):
        manager, _ = self.make_manager(quantity=101)
        state = self.partial(manager).state
        self.assertEqual((state.fills[0].quantity, state.remaining_quantity), (50, 51))

    def test_two_share_quantity_split(self):
        manager, _ = self.make_manager(quantity=2)
        state = self.partial(manager).state
        self.assertEqual((state.fills[0].quantity, state.remaining_quantity), (1, 1))

    def test_partial_pnl_only_realizes_sold_shares(self):
        manager, _ = self.make_manager()
        state = self.partial(manager).state
        self.assertEqual((state.gross_realized_pnl, state.net_realized_pnl), (D('6'), D('6')))
        self.assertIsNone(state.completed)

    def test_partial_does_not_repeat_at_second_target_touch(self):
        manager, _ = self.make_manager()
        self.partial(manager)
        manager.feed(self.bar(manager, opening='1.24', high='1.30', low='1.20', close='1.25'))
        self.assertEqual(len(manager.state.fills), 1)

    def test_breakeven_moves_immediately_and_never_above_entry(self):
        manager, _ = self.make_manager()
        state = self.partial(manager).state
        self.assertEqual((state.phase, state.active_stop, state.breakeven_floor), (S.RUNNER, D('1.11'), D('1.11')))
        self.assertLessEqual(state.active_stop, state.entry.entry_price)

    def test_breakeven_noncent_tick_rounds_down(self):
        manager, ticks = self.make_manager()
        ticks.rules[P.BREAKEVEN_STOP] = (replace(ticks.rules[P.BREAKEVEN_STOP][0], tick_size=D('.02')),)
        state = self.partial(manager).state
        self.assertEqual(state.active_stop, D('1.10'))

    def test_raw_entry_touch_above_executable_breakeven_is_not_stop_touch(self):
        manager, ticks = self.make_manager()
        ticks.rules[P.BREAKEVEN_STOP] = (replace(ticks.rules[P.BREAKEVEN_STOP][0], tick_size=D('.02')),)
        state = manager.feed(self.bar(manager, high='1.24', low='1.105')).state
        self.assertEqual((state.status, state.active_stop, state.remaining_quantity), (M.ACTIVE, D('1.10'), 50))

    def test_partial_candle_not_counted_for_trailing(self):
        manager, _ = self.make_manager()
        self.assertEqual(self.partial(manager).state.trailing_candle_count, 0)

    def test_one_additional_traded_candle_cannot_activate_trailing(self):
        manager, ticks = self.make_manager()
        self.partial(manager); manager.feed(self.bar(manager))
        self.assertEqual(manager.state.trailing_candle_count, 1)
        self.assertFalse(any(query.purpose == P.TRAILING_STOP for query in ticks.calls))

    def test_two_additional_traded_candles_cannot_activate_trailing(self):
        manager, _ = self.make_manager()
        self.partial(manager)
        for _ in range(2): manager.feed(self.bar(manager))
        self.assertEqual((manager.state.trailing_candle_count, manager.state.active_stop), (2, D('1.11')))

    def test_three_additional_traded_candles_activate_trailing(self):
        manager, _ = self.make_manager()
        self.partial(manager)
        for low in ('1.13', '1.14', '1.15'):
            manager.feed(self.bar(manager, opening='1.16', low=low))
        self.assertEqual((manager.state.trailing_candle_count, manager.state.active_stop), (3, D('1.13')))

    def test_trailing_uses_minimum_of_three_completed_lows(self):
        manager, _ = self.make_manager()
        self.partial(manager)
        for low in ('1.14', '1.12', '1.15'):
            manager.feed(self.bar(manager, opening='1.16', low=low))
        self.assertEqual(manager.state.active_stop, D('1.12'))
        self.assertEqual([item.low for item in manager.state.trailing_window], [D('1.14'), D('1.12'), D('1.15')])

    def test_activation_count_and_low_lookback_are_independent_config_fields(self):
        manager, _ = self.make_manager()
        # A separately identified test configuration; frozen defaults stay 3/3.
        config = replace(manager.state.entry.config,
                         runner_trailing_activation_traded_candles=4,
                         runner_low_lookback_traded_candles=2)
        manager.state = replace(manager.state, entry=replace(manager.state.entry, config=config))
        self.partial(manager)
        for low in ('1.12', '1.13', '1.14'):
            manager.feed(self.bar(manager, opening='1.16', low=low))
        self.assertEqual(manager.state.active_stop, D('1.11'))
        manager.feed(self.bar(manager, opening='1.16', low='1.15'))
        self.assertEqual(manager.state.active_stop, D('1.14'))
        self.assertEqual([item.low for item in manager.state.trailing_window], [D('1.14'), D('1.15')])

    def test_no_trade_does_not_advance_or_reset_trailing_progress(self):
        manager, _ = self.make_manager()
        self.partial(manager); manager.feed(self.bar(manager))
        before = manager.state.trailing_window
        manager.feed(self.bar(manager, kind=K.NO_TRADE))
        self.assertEqual((manager.state.trailing_candle_count, manager.state.trailing_window), (1, before))

    def test_trailing_recalculates_sliding_window_after_later_candle(self):
        manager, _ = self.make_manager()
        self.partial(manager)
        for low in ('1.12', '1.13', '1.14', '1.15'):
            manager.feed(self.bar(manager, opening='1.16', low=low))
        self.assertEqual(manager.state.active_stop, D('1.13'))
        self.assertEqual([item.low for item in manager.state.trailing_window], [D('1.13'), D('1.14'), D('1.15')])

    def test_trailing_stop_cannot_move_downward(self):
        manager, ticks = self.make_manager()
        self.partial(manager)
        for _ in range(3): manager.feed(self.bar(manager, low='1.14'))
        ticks.rules[P.TRAILING_STOP] = (replace(ticks.rules[P.TRAILING_STOP][0], tick_size=D('.20')),)
        for _ in range(3): manager.feed(self.bar(manager, opening='1.16', low='1.15'))
        self.assertEqual(manager.state.active_stop, D('1.14'))

    def test_normalized_trailing_candidate_below_breakeven_cannot_lower_stop(self):
        manager, ticks = self.make_manager()
        self.partial(manager)
        ticks.rules[P.TRAILING_STOP] = (replace(ticks.rules[P.TRAILING_STOP][0], tick_size=D('.20')),)
        for _ in range(3): manager.feed(self.bar(manager, low='1.13'))
        self.assertEqual(manager.state.active_stop, D('1.11'))
        evidence = dict(next(r for r in manager.audit if r.event_type == 'TRAILING_STOP_UPDATED').details)
        self.assertEqual(evidence['normalized_candidate'], D('1.00'))

    def test_new_trailing_stop_is_not_retroactive_to_its_creation_candle(self):
        manager, _ = self.make_manager()
        self.partial(manager)
        for _ in range(3): manager.feed(self.bar(manager, low='1.13'))
        self.assertEqual((manager.state.status, manager.state.active_stop), (M.ACTIVE, D('1.13')))
        result = manager.feed(self.bar(manager, low='1.13'))
        self.assertEqual(result.state.completed.final_exit_reason, E.TRAILING_STOP)

    def test_runner_stop_touch_exits_remainder(self):
        manager, _ = self.make_manager()
        self.partial(manager)
        result = manager.feed(self.bar(manager, low='1.11'))
        self.assertEqual((result.exit_fills[0].quantity, result.exit_fills[0].reference_price), (50, D('1.11')))

    def test_runner_gap_stop_fills_actual_open(self):
        manager, _ = self.make_manager()
        self.partial(manager)
        fill = manager.feed(self.bar(manager, opening='1.08', low='1.07', close='1.10')).exit_fills[0]
        self.assertEqual((fill.reference_price, fill.gap), (D('1.08'), True))

    def test_no_trade_neither_exits_nor_invents_low(self):
        manager, ticks = self.make_manager()
        before = manager.state
        result = manager.feed(self.bar(manager, kind=K.NO_TRADE))
        self.assertEqual((result.state.remaining_quantity, result.state.active_stop, result.state.fills),
                         (before.remaining_quantity, before.active_stop, before.fills))
        self.assertEqual(ticks.calls, [])
        self.assertEqual(result.state.trailing_window, ())

    def test_no_trade_reporting_mark_cannot_become_a_fill(self):
        manager, _ = self.make_manager()
        result = manager.feed(self.bar(manager, kind=K.NO_TRADE))
        self.assertTrue(result.state.reporting_mark_carried)
        self.assertEqual(result.state.last_trustworthy_price, D('1.11'))
        self.assertEqual(result.exit_fills, ())

    def test_first_trade_after_no_trade_uses_normal_gap_stop(self):
        manager, _ = self.make_manager()
        manager.feed(self.bar(manager, kind=K.NO_TRADE))
        result = manager.feed(self.bar(manager, opening='1.00', low='.99', close='1.04'))
        self.assertEqual(result.exit_fills[0].reference_price, D('1.00'))

    def test_missing_data_pauses_with_exact_cause_and_last_stop(self):
        manager, _ = self.make_manager()
        before = manager.state
        result = manager.feed(self.bar(manager, kind=K.MISSING, cause='fixture missing source candle'))
        self.assertEqual((result.state.status, result.state.failure_reason), (M.PAUSED_DATA, R.DATA_GAP))
        self.assertEqual((result.state.active_stop, result.state.remaining_quantity), (before.active_stop, 100))
        self.assertTrue(result.requires_shared_pause)
        self.assertEqual(result.exit_fills, ())
        self.assertIn('fixture missing source candle', str(result.audit))

    def test_invalid_data_pauses_with_exact_cause(self):
        manager, _ = self.make_manager()
        result = manager.feed(self.bar(manager, kind=K.INVALID, cause='conflicting provider trades'))
        self.assertEqual(result.state.failure_reason, R.INVALID_DATA)
        self.assertIn('conflicting provider trades', str(result.audit))

    def test_paused_position_cannot_process_later_intervals(self):
        manager, _ = self.make_manager()
        missing = self.bar(manager, kind=K.MISSING)
        manager.feed(missing)
        before = manager.state
        with self.assertRaisesRegex(ValueError, 'paused/incomplete'):
            manager.feed(self.bar(manager, at=missing.end))
        self.assertEqual(manager.state, before)

    def test_reliable_replacement_replays_exact_failed_interval(self):
        manager, _ = self.make_manager()
        failed = self.bar(manager, kind=K.MISSING, cause='repairable source gap')
        manager.feed(failed)
        replacement = self.bar(manager, low='1.05', at=failed.timestamp)
        result = manager.resume_with_replacement(replacement)
        self.assertEqual(result.state.status, M.CLOSED)
        self.assertEqual(result.state.completed.net_pnl, D('-6'))
        self.assertIn('repairable source gap', str(result.audit))

    def test_replacement_cannot_skip_to_later_timestamp(self):
        manager, _ = self.make_manager()
        bar = self.bar(manager, kind=K.INVALID)
        manager.feed(bar)
        with self.assertRaisesRegex(ValueError, 'exact paused interval'):
            manager.resume_with_replacement(self.bar(manager, at=bar.end))

    def test_verified_no_trade_can_replace_previously_unknown_interval(self):
        manager, _ = self.make_manager()
        manager.feed(self.bar(manager, kind=K.MISSING))
        result = manager.resume_with_replacement(self.bar(manager, kind=K.NO_TRADE))
        self.assertEqual((result.state.status, result.state.remaining_quantity), (M.ACTIVE, 100))

    def test_unresolved_data_produces_incomplete_not_fabricated_exit(self):
        manager, _ = self.make_manager()
        manager.feed(self.bar(manager, kind=K.INVALID, cause='unrepairable feed conflict'))
        result = manager.mark_data_incomplete()
        self.assertEqual(result.state.status, M.INCOMPLETE_DATA)
        self.assertTrue(result.requires_shared_stop)
        self.assertIsNone(result.state.completed)
        self.assertEqual(result.state.remaining_quantity, 100)

    def test_missing_breakeven_tick_stops_run_after_realized_partial(self):
        manager, ticks = self.make_manager()
        ticks.rules[P.BREAKEVEN_STOP] = ()
        result = self.partial(manager)
        self.assertEqual(result.state.run_status, RunStatus.INCOMPLETE_TICK_SIZE_UNAVAILABLE_OPEN_POSITION)
        self.assertEqual((result.state.active_stop, result.state.remaining_quantity), (D('1.05'), 50))
        self.assertEqual((result.state.net_realized_pnl, len(result.exit_fills)), (D('6'), 1))
        self.assertIsNone(result.state.completed)
        self.assertTrue(result.requires_shared_stop)

    def test_breakeven_purpose_mismatch_preserves_supplied_and_requested_audit_fields(self):
        manager, ticks = self.make_manager()
        old_stop = manager.state.active_stop
        ticks.rules[P.BREAKEVEN_STOP] = (
            replace(ticks.rules[P.BREAKEVEN_STOP][0], purpose=P.INITIAL_STOP),)
        bar = self.bar(manager, high='1.24')
        result = manager.feed(bar)
        metadata = next(record for record in result.audit if record.event_type == 'PROTECTIVE_TICK_METADATA')
        fields = dict(metadata.details)
        self.assertEqual(fields['purpose'], 'INITIAL_STOP')
        self.assertEqual(fields['supplied_purpose'], 'INITIAL_STOP')
        self.assertEqual(fields['requested_purpose'], 'BREAKEVEN_STOP')
        self.assertEqual((metadata.recorded_at, fields['query_timestamp']), (bar.end, bar.timestamp))
        rejected = next(record for record in result.audit if record.event_type == 'PROTECTIVE_TICK_REJECTED')
        self.assertEqual(dict(rejected.details)['failures'], 'ORDER_PURPOSE_MISMATCH')
        self.assertEqual(result.state.data_quality_reason, 'ORDER_PURPOSE_MISMATCH')
        self.assertEqual(result.state.run_status, RunStatus.INCOMPLETE_TICK_SIZE_UNAVAILABLE_OPEN_POSITION)
        self.assertEqual((result.state.active_stop, result.state.remaining_quantity), (old_stop, 50))
        self.assertEqual((result.state.failure_at, result.state.net_realized_pnl), (bar.end, D('6')))
        # The valid target partial survives; no runner exit/protective level is fabricated.
        self.assertEqual([(fill.reason, fill.quantity) for fill in result.exit_fills], [(E.PARTIAL_2R, 50)])
        self.assertIsNone(result.state.breakeven_floor)
        self.assertIsNone(result.state.completed)
        self.assertTrue(result.requires_shared_stop)
        self.assertFalse(any(record.event_type == 'PROTECTIVE_LEVEL_NORMALIZED' for record in result.audit))

    def test_trailing_purpose_mismatch_preserves_supplied_and_requested_audit_fields(self):
        manager, ticks = self.make_manager()
        self.partial(manager)
        for _ in range(2):
            manager.feed(self.bar(manager))
        before = manager.state
        ticks.rules[P.TRAILING_STOP] = (
            replace(ticks.rules[P.TRAILING_STOP][0], purpose=P.TARGET_2R),)
        bar = self.bar(manager)
        result = manager.feed(bar)
        metadata = next(record for record in result.audit if record.event_type == 'PROTECTIVE_TICK_METADATA')
        fields = dict(metadata.details)
        self.assertEqual(fields['purpose'], 'TARGET_2R')
        self.assertEqual(fields['supplied_purpose'], 'TARGET_2R')
        self.assertEqual(fields['requested_purpose'], 'TRAILING_STOP')
        self.assertEqual((metadata.recorded_at, fields['query_timestamp']), (bar.end, bar.end))
        rejected = next(record for record in result.audit if record.event_type == 'PROTECTIVE_TICK_REJECTED')
        self.assertEqual(dict(rejected.details)['failures'], 'ORDER_PURPOSE_MISMATCH')
        self.assertEqual(result.state.data_quality_reason, 'ORDER_PURPOSE_MISMATCH')
        self.assertEqual(result.state.run_status, RunStatus.INCOMPLETE_TICK_SIZE_UNAVAILABLE_OPEN_POSITION)
        self.assertEqual(result.state.status, M.INCOMPLETE_TICK)
        self.assertEqual((result.state.active_stop, result.state.remaining_quantity),
                         (before.active_stop, before.remaining_quantity))
        self.assertEqual((result.state.fills, result.state.net_realized_pnl),
                         (before.fills, before.net_realized_pnl))
        self.assertEqual(result.state.failure_at, bar.end)
        self.assertEqual(result.exit_fills, ())
        self.assertIsNone(result.state.completed)
        self.assertTrue(result.requires_shared_stop)
        self.assertFalse(any(record.event_type == 'PROTECTIVE_LEVEL_NORMALIZED' for record in result.audit))

    def test_missing_trailing_tick_preserves_last_active_stop(self):
        manager, ticks = self.make_manager()
        self.partial(manager)
        ticks.rules[P.TRAILING_STOP] = ()
        for _ in range(3): result = manager.feed(self.bar(manager))
        self.assertEqual((result.state.status, result.state.active_stop), (M.INCOMPLETE_TICK, D('1.11')))
        self.assertEqual(result.state.remaining_quantity, 50)

    def test_no_future_tick_metadata_required_without_replacement(self):
        manager, ticks = self.make_manager()
        self.partial(manager)
        for _ in range(3): manager.feed(self.bar(manager, low='1.13'))
        ticks.rules[P.TRAILING_STOP] = ()
        # The still-present 1.13 historical low cannot improve the current stop.
        state = manager.feed(self.bar(manager, low='1.14')).state
        self.assertEqual((state.status, state.active_stop), (M.ACTIVE, D('1.13')))

    def test_future_breakeven_metadata_is_not_used_or_audited(self):
        manager, ticks = self.make_manager()
        partial_start = manager.next_interval
        rule = ticks.rules[P.BREAKEVEN_STOP][0]
        ticks.rules[P.BREAKEVEN_STOP] = (replace(rule, available_at=partial_start+timedelta(seconds=1),
                                                share_basis_id='future-basis-not-known'),)
        result = self.partial(manager)
        self.assertEqual(result.state.status, M.INCOMPLETE_TICK)
        self.assertNotIn('future-basis-not-known', str(result.audit))

    def test_intrabar_breakeven_tick_must_cover_possible_activation_times(self):
        manager, ticks = self.make_manager()
        rule = ticks.rules[P.BREAKEVEN_STOP][0]
        ticks.rules[P.BREAKEVEN_STOP] = (replace(rule, valid_until=manager.next_interval+timedelta(seconds=30)),)
        result = self.partial(manager)
        self.assertEqual(result.state.status, M.INCOMPLETE_TICK)
        self.assertIn('INTRABAR_TICK_APPLICABILITY_UNAVAILABLE', str(result.audit))

    def test_known_open_breakeven_only_requires_metadata_at_known_activation(self):
        manager, ticks = self.make_manager()
        rule = ticks.rules[P.BREAKEVEN_STOP][0]
        ticks.rules[P.BREAKEVEN_STOP] = (replace(rule, valid_until=manager.next_interval+timedelta(seconds=30)),)
        bar = self.bar(manager, opening='1.23', high='1.25', low='1.20', close='1.24')
        result = manager.on_open(bar)
        self.assertEqual((result.state.status, result.state.active_stop), (M.ACTIVE, D('1.11')))

    def test_trailing_uses_metadata_available_at_completed_close(self):
        manager, ticks = self.make_manager()
        self.partial(manager)
        for _ in range(2): manager.feed(self.bar(manager))
        bar = self.bar(manager)
        rule = ticks.rules[P.TRAILING_STOP][0]
        ticks.rules[P.TRAILING_STOP] = (replace(rule, available_at=bar.end, valid_from=bar.end),)
        result = manager.feed(bar)
        self.assertEqual((result.state.status, result.state.active_stop), (M.ACTIVE, D('1.13')))

    def test_cross_band_breakeven_uses_verified_lower_band(self):
        manager, ticks = self.make_manager()
        base = ticks.rules[P.BREAKEVEN_STOP][0]
        ticks.rules[P.BREAKEVEN_STOP] = (
            replace(base, tick_size=D('.02'), maximum_price=D('1.105'), source_id='lower'),
            replace(base, tick_size=D('.05'), minimum_price=D('1.105'), source_id='upper'))
        result = self.partial(manager)
        self.assertEqual(result.state.active_stop, D('1.10'))
        self.assertIn('PROTECTIVE_TICK_BAND_TRANSITION', str(result.audit))

    def test_missing_cross_band_protective_rule_is_fatal(self):
        manager, ticks = self.make_manager()
        base = ticks.rules[P.BREAKEVEN_STOP][0]
        ticks.rules[P.BREAKEVEN_STOP] = (replace(base, tick_size=D('.05'), minimum_price=D('1.105')),)
        self.assertEqual(self.partial(manager).state.status, M.INCOMPLETE_TICK)

    def test_future_protective_destination_band_is_unavailable_not_guessed(self):
        manager, ticks = self.make_manager()
        base = ticks.rules[P.BREAKEVEN_STOP][0]
        bar = self.bar(manager, high='1.24')
        ticks.rules[P.BREAKEVEN_STOP] = (
            replace(base, tick_size=D('.02'), maximum_price=D('1.105'), source_id='future-lower-band',
                    available_at=bar.end, share_basis_id='unavailable-future-basis'),
            replace(base, tick_size=D('.05'), minimum_price=D('1.105'), source_id='known-upper-band'))
        result = manager.feed(bar)
        self.assertEqual(result.state.run_status, RunStatus.INCOMPLETE_TICK_SIZE_UNAVAILABLE_OPEN_POSITION)
        self.assertEqual((result.state.active_stop, result.state.remaining_quantity), (D('1.05'), 50))
        self.assertIsNone(result.state.breakeven_floor)
        self.assertEqual([(fill.reason, fill.quantity) for fill in result.exit_fills], [(E.PARTIAL_2R, 50)])
        unavailable = next(record for record in result.audit if record.event_type == 'PROTECTIVE_TICK_UNAVAILABLE')
        self.assertEqual(dict(unavailable.details)['cause'], 'TICK_METADATA_NOT_YET_AVAILABLE')
        self.assertEqual(dict(unavailable.details)['available_at'], bar.end)
        self.assertEqual(dict(unavailable.details)['boundary'], D('1.105'))
        self.assertEqual(unavailable.recorded_at, bar.end)
        metadata = [dict(record.details) for record in result.audit
                    if record.event_type == 'PROTECTIVE_TICK_METADATA']
        self.assertEqual([fields['source_id'] for fields in metadata], ['known-upper-band'])
        self.assertFalse(any('unavailable-future-basis' == value
                             for record in result.audit for _, value in record.details))
        self.assertFalse(any(record.event_type == 'PROTECTIVE_LEVEL_NORMALIZED' for record in result.audit))
        self.assertTrue(result.requires_shared_stop)

    def test_unverified_protective_destination_band_cannot_supply_a_level(self):
        manager, ticks = self.make_manager()
        base = ticks.rules[P.BREAKEVEN_STOP][0]
        ticks.rules[P.BREAKEVEN_STOP] = (
            replace(base, tick_size=D('.02'), maximum_price=D('1.105'), source_id='unverified-lower-band',
                    verified=False),
            replace(base, tick_size=D('.05'), minimum_price=D('1.105'), source_id='verified-upper-band'))
        result = self.partial(manager)
        self.assertEqual(result.state.run_status, RunStatus.INCOMPLETE_TICK_SIZE_UNAVAILABLE_OPEN_POSITION)
        self.assertEqual((result.state.active_stop, result.state.remaining_quantity), (D('1.05'), 50))
        self.assertIsNone(result.state.breakeven_floor)
        self.assertEqual([(fill.reason, fill.quantity) for fill in result.exit_fills], [(E.PARTIAL_2R, 50)])
        lower = next(dict(record.details) for record in result.audit
                     if record.event_type == 'PROTECTIVE_TICK_METADATA'
                     and dict(record.details)['source_id'] == 'unverified-lower-band')
        self.assertFalse(lower['verified'])
        self.assertEqual(lower['boundary'], D('1.105'))
        self.assertEqual(lower['tick'], D('.02'))
        rejected = next(record for record in result.audit if record.event_type == 'PROTECTIVE_TICK_REJECTED')
        self.assertEqual(dict(rejected.details)['failures'], 'UNVERIFIED_TICK_RULE')
        self.assertFalse(any(record.event_type == 'PROTECTIVE_LEVEL_NORMALIZED' for record in result.audit))
        self.assertTrue(result.requires_shared_stop)

    def test_conflicting_protective_rules_cannot_choose_favorable_tick(self):
        manager, ticks = self.make_manager()
        base = ticks.rules[P.BREAKEVEN_STOP][0]
        ticks.rules[P.BREAKEVEN_STOP] += (replace(base, tick_size=D('.02')),)
        result = self.partial(manager)
        self.assertEqual(result.state.status, M.INCOMPLETE_TICK)
        self.assertIn('CONFLICTING_TICK_BANDS', str(result.audit))

    def test_normal_eod_liquidates_at_final_minute_open(self):
        manager, _ = self.make_manager()
        final = manager.session.close-timedelta(minutes=1)
        self.advance(manager, final)
        bar = self.bar(manager, opening='1.18', high='1.20', low='1.17', close='1.19')
        result = manager.on_open(bar)
        fill = result.exit_fills[0]
        self.assertEqual((fill.reason, fill.modeled_event_at, fill.reference_price), (E.EOD_LIQUIDATION, final, D('1.18')))
        self.assertEqual((final.hour, final.minute), (15, 59))
        self.assertTrue(result.state.eod_instruction_active)

    def test_early_close_eod_uses_actual_final_minute(self):
        manager, _ = self.make_manager(close_hour=13)
        final = manager.session.close-timedelta(minutes=1)
        self.advance(manager, final)
        result = manager.feed(self.bar(manager))
        self.assertEqual((final.hour, final.minute), (12, 59))
        self.assertEqual(result.exit_fills[0].reason, E.EOD_LIQUIDATION)

    def assert_unresolved_final_minute(self, close_hour):
        manager, _ = self.make_manager(close_hour=close_hour)
        final = manager.session.close-timedelta(minutes=1)
        self.advance(manager, final)
        bar = self.bar(manager, kind=K.NO_TRADE)
        opening = manager.on_open(bar)
        self.assertEqual(opening.state.status, M.ACTIVE)
        self.assertTrue(opening.state.eod_instruction_active)
        self.assertEqual(opening.state.eod_instruction_at, final)
        self.assertEqual(opening.exit_fills, ())
        closed = manager.on_close(bar)
        self.assertEqual(closed.state.run_status, RunStatus.INCOMPLETE_UNRESOLVED_EOD_LIQUIDITY)
        self.assertEqual(closed.state.failure_reason, R.UNRESOLVED_EOD_NO_LIQUIDITY)
        self.assertEqual(closed.state.failure_at, manager.session.close)
        self.assertEqual((closed.state.remaining_quantity, closed.state.active_stop), (100, D('1.05')))
        self.assertEqual(closed.state.last_trustworthy_price, D('1.11'))
        self.assertIsNone(closed.state.completed)
        self.assertTrue(closed.requires_shared_stop)
        self.assertIn('NO_TRADE', str(manager.audit))
        self.assertIn('ending_portfolio_equity', str(closed.audit))
        return manager

    def test_final_no_trade_normal_session_is_unresolved_at_official_close(self):
        self.assert_unresolved_final_minute(16)

    def test_final_no_trade_early_close_is_unresolved_at_official_close(self):
        self.assert_unresolved_final_minute(13)

    def test_unresolved_eod_cannot_use_next_day_price(self):
        manager = self.assert_unresolved_final_minute(16)
        before = manager.state
        bar = self.bar(manager, at=manager.next_interval+timedelta(days=1), opening='2', high='3', low='1', close='2')
        with self.assertRaisesRegex(ValueError, 'paused/incomplete'):
            manager.feed(bar)
        self.assertEqual(manager.state, before)

    def test_unresolved_eod_cannot_manufacture_official_close_fill(self):
        manager = self.assert_unresolved_final_minute(16)
        with self.assertRaisesRegex(ValueError, 'paused/incomplete'):
            manager.feed(self.bar(manager, at=manager.session.close))
        self.assertEqual(manager.state.fills, ())

    def test_missing_eod_is_data_pause_not_verified_no_liquidity(self):
        manager, _ = self.make_manager()
        self.advance(manager, manager.session.close-timedelta(minutes=1))
        result = manager.feed(self.bar(manager, kind=K.MISSING, cause='missing liquidation source candle'))
        self.assertEqual((result.state.status, result.state.failure_reason), (M.PAUSED_DATA, R.DATA_GAP))
        self.assertTrue(result.state.eod_instruction_active)
        self.assertIsNone(result.state.run_status)

    def test_eod_liquidation_precedes_later_same_bar_stop_and_target(self):
        manager, _ = self.make_manager()
        self.advance(manager, manager.session.close-timedelta(minutes=1))
        result = manager.feed(self.bar(manager, opening='1.18', high='1.30', low='1.00'))
        self.assertEqual([fill.reason for fill in result.exit_fills], [E.EOD_LIQUIDATION])
        self.assertEqual(result.exit_fills[0].quantity, 100)

    def assert_final_open_eod_precedence(self, opening, expected_pnl):
        manager, ticks = self.make_manager()
        final = manager.session.close-timedelta(minutes=1)
        self.advance(manager, final)
        bar = self.bar(manager, opening=opening, high='1.40', low='1.00')
        result = manager.on_open(bar)
        self.assertEqual(len(result.exit_fills), 1)
        fill = result.exit_fills[0]
        self.assertEqual((fill.reason, fill.quantity, fill.simulated_fill_price),
                         (E.EOD_LIQUIDATION, 100, D(opening)))
        self.assertEqual((fill.modeled_event_at, fill.available_at), (final, final))
        self.assertEqual((fill.gross_pnl, fill.net_pnl), (D(expected_pnl), D(expected_pnl)))
        self.assertEqual((result.state.remaining_quantity, result.state.completed.net_pnl), (0, D(expected_pnl)))
        self.assertFalse(result.state.partial_filled)
        self.assertEqual(ticks.calls, [])
        instruction = next(record for record in result.audit if record.event_type == 'EOD_LIQUIDATION_INSTRUCTION')
        self.assertEqual((instruction.recorded_at, instruction.modeled_event_at), (final, final))
        self.assertTrue(dict(instruction.details)['irrevocable'])
        self.assertEqual(dict(instruction.details)['official_close'], manager.session.close)
        before = result.state
        completion = manager.on_close(bar)
        self.assertEqual((completion.state, completion.exit_fills, completion.audit), (before, (), ()))
        self.assertEqual(len(manager.state.fills), 1)

    def test_final_minute_open_at_stop_is_one_eod_liquidation(self):
        self.assert_final_open_eod_precedence('1.05', '-6')

    def test_final_minute_open_below_stop_is_one_eod_liquidation(self):
        self.assert_final_open_eod_precedence('1.04', '-7')

    def test_final_minute_open_at_target_is_one_eod_liquidation(self):
        self.assert_final_open_eod_precedence('1.23', '12')

    def test_final_minute_open_above_target_is_one_eod_liquidation(self):
        self.assert_final_open_eod_precedence('1.30', '19')

    def test_eod_liquidates_only_remaining_runner_quantity(self):
        manager, _ = self.make_manager()
        self.partial(manager)
        self.advance(manager, manager.session.close-timedelta(minutes=1))
        result = manager.feed(self.bar(manager, opening='1.18', low='1.17', close='1.18'))
        self.assertEqual(result.exit_fills[0].quantity, 50)
        self.assertEqual(result.state.completed.net_pnl, D('9.50'))

    def test_eod_unresolved_retains_known_partial_pnl(self):
        manager, _ = self.make_manager()
        self.partial(manager)
        self.advance(manager, manager.session.close-timedelta(minutes=1))
        result = manager.feed(self.bar(manager, kind=K.NO_TRADE))
        self.assertEqual((result.state.remaining_quantity, result.state.net_realized_pnl), (50, D('6')))
        self.assertIsNone(result.state.completed)

    def test_finish_session_cannot_skip_unknown_required_minutes(self):
        manager, _ = self.make_manager()
        with self.assertRaisesRegex(ValueError, 'every required interval'):
            manager.finish_session()

    def test_final_stop_loss_is_net_loss_with_zero_costs(self):
        manager, _ = self.make_manager()
        result = manager.feed(self.bar(manager, low='1.05'))
        completed = result.state.completed
        self.assertEqual((completed.gross_pnl, completed.net_pnl, completed.outcome), (D('-6'), D('-6'), O.LOSS))
        self.assertEqual((completed.commissions, completed.fees, completed.slippage), (D(0), D(0), D(0)))

    def test_partial_and_runner_profits_aggregate_exactly(self):
        manager, _ = self.make_manager()
        self.partial(manager)
        for _ in range(3): manager.feed(self.bar(manager, low='1.13'))
        state = manager.feed(self.bar(manager, low='1.13')).state
        self.assertEqual((state.completed.net_pnl, state.completed.outcome), (D('7'), O.WIN))
        self.assertEqual(state.completed.net_return, Fraction(7, 111))

    def test_exact_zero_net_completed_trade_is_breakeven(self):
        manager, _ = self.make_manager()
        self.partial(manager)
        result = manager.feed(self.bar(manager, opening='.99', low='.98', close='1.02'))
        self.assertEqual((result.state.completed.net_pnl, result.state.completed.outcome), (D(0), O.BREAKEVEN))

    def test_exit_fills_and_snapshots_are_immutable(self):
        manager, _ = self.make_manager()
        state = manager.feed(self.bar(manager, low='1.05')).state
        with self.assertRaises(FrozenInstanceError): state.active_stop = D(0)
        with self.assertRaises(FrozenInstanceError): state.fills[0].quantity = 0

    def test_final_exit_containing_interval_is_quarantined(self):
        manager, _ = self.make_manager()
        bar = self.bar(manager, low='1.05')
        completed = manager.feed(bar).state.completed
        self.assertFalse(completed.permits_fresh_price_candle(bar))
        self.assertEqual(completed.earliest_fresh_interval_start, bar.end)

    def test_open_exit_still_quarantines_whole_interval(self):
        manager, _ = self.make_manager()
        bar = self.bar(manager, opening='1.04', low='1.03')
        completed = manager.feed(bar).state.completed
        self.assertFalse(completed.permits_fresh_price_candle(bar))
        self.assertEqual(completed.earliest_fresh_interval_start, bar.end)

    def test_first_subsequent_valid_traded_candle_can_be_fresh_participant(self):
        manager, _ = self.make_manager()
        bar = self.bar(manager, low='1.05')
        completed = manager.feed(bar).state.completed
        self.assertTrue(completed.permits_fresh_price_candle(self.bar(manager, at=bar.end)))

    def test_no_trade_is_not_fresh_price_participant(self):
        manager, _ = self.make_manager()
        bar = self.bar(manager, low='1.05')
        completed = manager.feed(bar).state.completed
        self.assertFalse(completed.permits_fresh_price_candle(self.bar(manager, at=bar.end, kind=K.NO_TRADE)))
        self.assertTrue(completed.permits_fresh_price_candle(self.bar(manager, at=bar.end+timedelta(minutes=1))))

    def test_closed_position_ignores_all_later_price_movement(self):
        manager, _ = self.make_manager()
        manager.feed(self.bar(manager, low='1.05'))
        before = manager.state
        result = manager.feed(self.bar(manager, opening='2', high='3', low='1', close='2'))
        self.assertEqual(result.state, before)
        self.assertEqual(result.exit_fills, ())

    def test_open_phase_does_not_use_future_high_low_close_or_volume(self):
        results = []
        for fields in ({}, {'high': D('1.30')}, {'low': D('1.00')}, {'close': D('1.14')}, {'volume': D(999999)}):
            manager, _ = self.make_manager()
            results.append(manager.on_open(replace(self.bar(manager), **fields)))
        self.assertTrue(all(result == results[0] for result in results))

    def test_intrabar_fill_has_no_invented_execution_timestamp(self):
        manager, _ = self.make_manager()
        bar = self.bar(manager, low='1.05')
        result = manager.feed(bar)
        self.assertIsNone(result.exit_fills[0].modeled_event_at)
        self.assertEqual(result.exit_fills[0].available_at, bar.end)
        fill_audit = next(record for record in result.audit if record.event_type == 'SIMULATED_EXIT_FILL')
        self.assertEqual(fill_audit.recorded_at, bar.end)
        self.assertIsNone(fill_audit.modeled_event_at)

    def test_trailing_cannot_use_future_lows(self):
        manager, _ = self.make_manager()
        self.partial(manager)
        for _ in range(3): manager.feed(self.bar(manager, low='1.13'))
        before = manager.state
        later = self.bar(manager, low='1.15')
        self.assertEqual(before.active_stop, D('1.13'))
        manager.on_open(later)
        self.assertEqual(manager.state.trailing_window, before.trailing_window)

    def test_precision_safe_exit_pnl_independent_of_decimal_context(self):
        manager, _ = self.make_manager()
        with localcontext() as context:
            context.prec = 2
            self.partial(manager)
            state = manager.feed(self.bar(manager, opening='1.08', low='1.07', close='1.10')).state
        self.assertEqual(state.completed.net_pnl, D('4.50'))

    def test_utc_interval_times_match_new_york_session(self):
        manager, _ = self.make_manager()
        bar = self.bar(manager, low='1.05')
        result = manager.feed(replace(bar, timestamp=bar.timestamp.astimezone(timezone.utc)))
        self.assertEqual(result.state.completed.net_pnl, D('-6'))

    def test_nonmatching_security_cannot_affect_position(self):
        manager, _ = self.make_manager()
        with self.assertRaisesRegex(ValueError, 'matching'):
            manager.feed(replace(self.bar(manager), security_id='unrelated-security'))

    def test_skipped_or_duplicate_interval_is_not_silently_accepted(self):
        manager, _ = self.make_manager()
        bar = self.bar(manager)
        with self.assertRaisesRegex(ValueError, 'contiguous'):
            manager.feed(replace(bar, timestamp=bar.end))
        manager.feed(bar)
        with self.assertRaisesRegex(ValueError, 'contiguous'):
            manager.feed(bar)

    def test_holiday_or_incompatible_calendar_cannot_manage_entry(self):
        manager, ticks = self.make_manager()
        with self.assertRaisesRegex(ValueError, 'calendar/session'):
            PositionManager(manager.state.entry, Calendar(holidays=(manager.session.trading_date,)), ticks)

    def test_only_phase_six_open_position_is_accepted(self):
        manager, ticks = self.make_manager()
        with self.assertRaises(TypeError): PositionManager(manager.state.entry.approval, Calendar(), ticks)

    def test_completion_cannot_differ_from_opening_source_record(self):
        manager, _ = self.make_manager()
        bar = self.bar(manager)
        manager.on_open(bar)
        with self.assertRaisesRegex(ValueError, 'Completion must match'):
            manager.on_close(replace(bar, low=D('1.14')))

    def test_coarse_breakeven_cannot_lower_existing_stop_or_invent_trailing_origin(self):
        manager, ticks = self.make_manager()
        ticks.rules[P.BREAKEVEN_STOP] = (replace(ticks.rules[P.BREAKEVEN_STOP][0], tick_size=D('.5')),)
        self.partial(manager)
        self.assertEqual((manager.state.active_stop, manager.state.breakeven_floor), (D('1.05'), D('1.00')))
        self.assertEqual(manager.state.trailing_candle_count, 0)
        result = manager.feed(self.bar(manager, low='1.05'))
        self.assertEqual(result.state.completed.final_exit_reason, E.INITIAL_STOP)

    def test_tick_failure_at_completion_retains_trustworthy_reporting_close(self):
        manager, ticks = self.make_manager()
        ticks.rules[P.BREAKEVEN_STOP] = ()
        result = self.partial(manager)
        self.assertEqual((result.state.last_trustworthy_price, result.state.reporting_unrealized_pnl), (D('1.20'), D('4.50')))
        self.assertEqual(result.state.net_realized_pnl, D('6'))
        self.assertIsNone(result.state.completed)

    def test_intrabar_partial_not_visible_in_opening_handoff(self):
        manager, _ = self.make_manager()
        bar = self.bar(manager, high='1.24')
        opening = manager.on_open(bar)
        self.assertEqual((opening.exit_fills, opening.state.net_realized_pnl), ((), D(0)))
        closing = manager.on_close(bar)
        self.assertEqual(closing.state.net_realized_pnl, D('6'))
        self.assertTrue(all(fill.available_at == bar.end for fill in closing.exit_fills))

    def test_partial_fill_quantity_is_not_capped_by_minute_volume(self):
        manager, _ = self.make_manager()
        result = manager.feed(replace(self.bar(manager, high='1.24'), volume=D(1)))
        self.assertEqual(result.exit_fills[0].quantity, 50)

    def test_fractional_gap_price_retains_precision(self):
        manager, _ = self.make_manager()
        result = manager.feed(self.bar(manager, opening='.98765', low='.98', close='1.00'))
        self.assertEqual(result.exit_fills[0].reference_price, D('.98765'))
        self.assertEqual(result.state.completed.net_pnl, D('-12.235'))

    def test_quarantine_boundary_can_be_consumed_by_next_session_fresh_detection(self):
        manager, _ = self.make_manager()
        bar = self.bar(manager, low='1.05')
        completed = manager.feed(bar).state.completed
        # This checks only quarantine; the next session's detector must separately
        # validate its calendar/universe and start entirely fresh price history.
        tomorrow = bar.timestamp.replace(hour=9, minute=30)+timedelta(days=1)
        self.assertTrue(completed.permits_fresh_price_candle(self.bar(manager, at=tomorrow)))
