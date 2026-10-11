"""Pure commission compatibility over anonymous offline reconciliation fixtures."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, Inexact, Rounded, localcontext
import inspect
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tradingbot_broker import ibkr_paper_transport as paper
from tradingbot_broker import paper_reconciliation as evaluator
from tradingbot_broker import paper_recovery as recovery
from tradingbot_broker import paper_dispatch as dispatch
from tradingbot_broker import paper_dispatch_journal as journal_owner
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests import test_paper_reconciliation as fixture
from tests import test_paper_recovery as recovery_fixture
from tests import test_paper_execution_journal as journal_fixture
from tests.test_paper_commission_capture import CommissionFixture


D = Decimal
S = evaluator._AssessmentState
AT = datetime(2026, 2, 3, 14, 15, 16, tzinfo=timezone.utc)


def commission(*, amount=D('0.05'), exec_id='commission-execution', currency='USD',
               generation=1, sequence=2, received_at=AT):
    return paper._CommissionEvidence(generation, sequence, exec_id,
        paper._commission_report_id(exec_id, amount, currency), amount, currency, received_at)


def page(value, *events, cursor=None, through=None, watermark=None, initial=True):
    """Supply frozen diagnostic pages, never acknowledge the producing transport."""
    local = value.final_local
    cursor = local.callback_cursor if cursor is None else cursor
    through = max((e.sequence for e in events), default=cursor) if through is None else through
    watermark = through if watermark is None else watermark
    local = replace(local, callback_cursor=cursor, unread_events=tuple(events),
        unread_through_sequence=through, write_callback_watermark=watermark)
    return replace(value, final_local=local,
        attempt=replace(value.attempt, initial_local=local) if initial else value.attempt)


def open_callback(value, sequence):
    p = value.final_local.pending
    return paper._OpenOrderEvidence(p.generation, sequence, p.order_id, p.client_id,
        p.client_order_id, p.con_id, 'STK', 'SMART', p.symbol, p.currency, 'BUY',
        'MKT', p.quantity, 'DAY', 'Submitted')


def status_callback(value, sequence):
    p = value.final_local.pending
    return paper._OrderStatusEvidence(p.generation, sequence, p.order_id, p.client_id,
        'Submitted', D(4), D(6), D('1.25'), D('1.25'), 0)


def execution_callback(value, sequence):
    p = value.final_local.pending
    return paper._ExecutionEvidence(p.generation, sequence, p.order_id, p.client_id,
        p.client_order_id, 'commission-execution', p.con_id, 'BOT', D(4), D('1.25'))


class CompatibilityCase(unittest.TestCase):
    def setUp(self):
        self.value = fixture.bundle((fixture.order(),))

    def checked(self, value, state=S.UNRESOLVED):
        before = (value.attempt, value.final_local, value.read_receipt)
        result = evaluator._evaluate_broker_reconciliation(value)
        self.assertIs(result.state, state)
        self.assertEqual(result, evaluator._evaluate_broker_reconciliation(value))
        self.assertEqual(before, (value.attempt, value.final_local, value.read_receipt))
        return result

    def invalid(self, event):
        # The initial capture validates malformed evidence even when the final record is valid.
        valid = page(self.value, commission())
        local = replace(valid.attempt.initial_local, unread_events=(event,))
        value = replace(valid, attempt=replace(valid.attempt, initial_local=local))
        with self.assertRaises(ReadOnlyError) as caught:
            evaluator._evaluate_broker_reconciliation(value)
        self.assertEqual(str(caught.exception), 'INVALID_BROKER_RECONCILIATION_BUNDLE')
        self.assertIsNone(caught.exception.__cause__)

    def economics_unchanged(self, original, result):
        for name in ('executed_quantity', 'quantity_certainty', 'lifecycle_status',
                     'strong_attribution', 'attributed_execution_count'):
            self.assertEqual(getattr(original, name), getattr(result, name), name)


class ValidCommissionTests(CompatibilityCase):
    def test_positive_negative_and_supplied_zero(self):
        for amount in (D('0.05'), D('-0.05'), D('0'), D('-0')):
            with self.subTest(amount=amount):
                event = commission(amount=amount)
                result = self.checked(page(self.value, event))
                self.assertEqual(result.executed_quantity, D(0))
                self.assertEqual(event.amount.as_tuple(), amount.as_tuple())

    def test_digit_and_exponent_boundaries_are_exact(self):
        for amount in (D((1, (7,) * 64, -64)), D((0, (1,), 64)), D('1E-64')):
            with self.subTest(amount=amount):
                self.checked(page(self.value, commission(amount=amount)))

    def test_canonical_numeric_identity_preserves_input_decimal(self):
        a, b = commission(amount=D('-1.2300')), commission(amount=D('-1.23'))
        self.assertEqual(a.report_id, b.report_id)
        self.checked(page(self.value, a))
        self.assertEqual(a.amount.as_tuple().exponent, -4)

    def test_non_usd_currency_is_not_related_to_target_currency(self):
        result = self.checked(page(self.value, commission(currency='EUR')))
        self.assertIsNot(result.state, S.CONFLICT)
        self.assertEqual(result.attributed_execution_count, 0)

    def test_exact_utc_timestamp_is_retained(self):
        event = commission(received_at=AT + timedelta(microseconds=7))
        self.checked(page(self.value, event))
        self.assertIs(event.received_at.tzinfo, timezone.utc)
        self.assertEqual(event.received_at, AT + timedelta(microseconds=7))

    def test_producer_validator_is_reused_for_both_local_captures(self):
        event = commission()
        original = paper._CommissionEvidence.__post_init__
        with patch.object(paper._CommissionEvidence, '__post_init__', autospec=True,
                          side_effect=original) as call:
            evaluator._evaluate_broker_reconciliation(page(self.value, event))
        self.assertEqual(call.call_args_list, [unittest.mock.call(event)] * 2)

    def test_low_decimal_precision_and_traps_do_not_round(self):
        amount = D((1, (7,) * 64, -64))
        value = page(self.value, commission(amount=amount))
        expected = evaluator._evaluate_broker_reconciliation(value)
        for flagged in (False, True):
            with self.subTest(preexisting_flags=flagged), localcontext() as context:
                context.prec = 2
                context.traps[Inexact] = True
                context.traps[Rounded] = True
                context.clear_flags()
                context.flags[Inexact] = flagged
                context.flags[Rounded] = flagged
                before = dict(context.flags)
                self.assertEqual(evaluator._evaluate_broker_reconciliation(value), expected)
                self.assertEqual(dict(context.flags), before)


# Each invalid shape is forced into an exact frozen record to exercise validation
# of received values rather than merely the constructor rejecting test inputs.
_MALFORMED = (
    ('generation_types', 'generation', (True, 1.0, '1')),
    ('generation_bounds', 'generation', (0, -1)),
    ('sequence_types', 'sequence', (True, 2.0, '2')),
    ('sequence_bounds', 'sequence', (0, -1)),
    ('exec_types', 'exec_id', (None, 4, True)),
    ('exec_empty', 'exec_id', ('',)),
    ('exec_length', 'exec_id', ('x' * 101,)),
    ('exec_reference', 'exec_id', ('arbitrary message', 'line\ntext', 'é')),
    ('report_types', 'report_id', (None, 7, True)),
    ('report_format', 'report_id', ('report', 'commission-' + 'G' * 64)),
    ('report_fingerprint', 'report_id', ('commission-' + '0' * 64,)),
    ('amount_types', 'amount', (0.05, 0, True, '0.05', None)),
    ('amount_nan', 'amount', (D('NaN'), D('sNaN'))),
    ('amount_infinity', 'amount', (D('Infinity'), D('-Infinity'))),
    ('amount_digits', 'amount', (D((0, (1,) * 65, -64)),)),
    ('amount_high_exponent', 'amount', (D('1E65'),)),
    ('amount_low_exponent', 'amount', (D('1E-65'),)),
    ('currency_types', 'currency', (None, 123, True)),
    ('currency_length', 'currency', ('', 'US', 'USDD')),
    ('currency_lowercase', 'currency', ('usd', 'Usd')),
    ('currency_nonascii', 'currency', ('ÜSD',)),
    ('currency_text', 'currency', ('U D', '12A')),
    ('timestamp_types', 'received_at', (None, AT.isoformat(), 7)),
    ('timestamp_naive', 'received_at', (AT.replace(tzinfo=None),)),
    ('timestamp_offset', 'received_at', (AT.astimezone(timezone(timedelta(hours=1))),)),
    ('changed_amount_identity', 'amount', (D('0.06'),)),
    ('changed_currency_identity', 'currency', ('EUR',)),
    ('changed_exec_identity', 'exec_id', ('different-execution',)),
)


class MalformedTests(CompatibilityCase):
    def test_forged_other_model_with_equal_fields_rejected(self):
        event = commission()
        self.invalid(SimpleNamespace(**{f.name: getattr(event, f.name) for f in fields(event)}))

    def test_commission_subclass_rejected(self):
        class Forged(paper._CommissionEvidence):
            pass
        e = commission()
        self.invalid(Forged(*(getattr(e, f.name) for f in fields(e))))

    def test_primitive_subclasses_rejected(self):
        class Number(int): pass
        class Text(str): pass
        class Amount(Decimal): pass
        class Stamp(datetime): pass
        for name, bad in (('generation', Number(1)), ('exec_id', Text('commission-execution')),
                          ('amount', Amount('0.05')), ('received_at', Stamp(2026, 2, 3, tzinfo=timezone.utc))):
            event = commission()
            object.__setattr__(event, name, bad)
            with self.subTest(field=name):
                self.invalid(event)

    def test_utc_named_zone_is_not_exact_producer_utc(self):
        from zoneinfo import ZoneInfo
        event = commission()
        object.__setattr__(event, 'received_at', AT.astimezone(ZoneInfo('UTC')))
        self.invalid(event)

    def test_malformed_final_page_rejected(self):
        value = page(self.value, commission(), initial=False)
        object.__setattr__(value.final_local.unread_events[0], 'amount', D('NaN'))
        with self.assertRaises(ReadOnlyError):
            evaluator._evaluate_broker_reconciliation(value)


def malformed_test(name, values):
    def test(self):
        for bad in values:
            with self.subTest(value=repr(bad)):
                event = commission()
                object.__setattr__(event, name, bad)
                self.invalid(event)
    return test


for label, name, values in _MALFORMED:
    setattr(MalformedTests, 'test_' + label, malformed_test(name, values))


class SequenceTests(CompatibilityCase):
    def test_expected_sequence_does_not_acknowledge_commission(self):
        value = page(self.value, commission())
        before = (value.final_local.callback_cursor, value.final_local.unread_through_sequence,
                  value.final_local.write_callback_watermark)
        self.checked(value)
        self.assertEqual(before, (1, 2, 2))
        self.assertEqual(value.final_local.callback_cursor, 1)

    def test_gap_before_old_generation_commission_stays_unresolved(self):
        self.checked(page(self.value, commission(generation=2, sequence=3)))

    def test_gap_after_old_generation_commission_stays_unresolved(self):
        self.checked(page(self.value, commission(generation=2), through=3, watermark=3))

    def test_commission_beyond_through_stays_unresolved(self):
        value = page(self.value, commission(sequence=3), through=2, watermark=3)
        self.checked(value)

    def test_unread_through_behind_callback_watermark_stays_unresolved(self):
        self.checked(page(self.value, commission(generation=2), watermark=3))

    def test_precommit_commission_is_coverage_only(self):
        pending = replace(self.value.final_local.pending, commit_sequence=2)
        local = replace(self.value.final_local, pending=pending)
        value = replace(self.value, final_local=local,
                        attempt=replace(self.value.attempt, initial_local=local))
        result = self.checked(page(value, commission()), S.OPEN_WORKING)
        self.assertEqual(result, evaluator._evaluate_broker_reconciliation(value))

    def test_other_generation_is_coverage_only_without_adoption(self):
        result = self.checked(page(self.value, commission(generation=2)), S.OPEN_WORKING)
        self.assertEqual(result, evaluator._evaluate_broker_reconciliation(self.value))

    def test_initial_unread_commission_remains_evidence_after_final_cursor_advances(self):
        value = page(self.value, commission())
        value = replace(value, final_local=replace(value.final_local,
            callback_cursor=2, unread_events=()))
        self.checked(value)

    def test_callback_watermark_change_is_reported_without_acknowledgement(self):
        result = self.checked(page(self.value, commission(), initial=False))
        self.assertTrue(result.callback_watermark_changed)

    def test_reordered_or_repeated_source_sequence_does_not_hide_gap(self):
        for events in ((commission(sequence=3), commission()), (commission(), commission())):
            with self.subTest(sequences=tuple(e.sequence for e in events)):
                self.checked(page(self.value, *events))

    def test_page_capacity_is_not_expanded(self):
        events = tuple(commission(sequence=i) for i in range(2, 130))
        self.checked(page(self.value, *events))
        with self.assertRaises(ReadOnlyError):
            evaluator._evaluate_broker_reconciliation(page(self.value, *events, commission(sequence=130)))


class RelationshipTests(CompatibilityCase):
    def test_known_local_execution_changes_no_economic_fact(self):
        value = recovery_fixture.local_execution(self.value, exec_id='commission-execution')
        original = evaluator._evaluate_broker_reconciliation(value)
        result = self.checked(page(value, commission()))
        self.economics_unchanged(original, result)
        self.assertEqual((result.executed_quantity, result.attributed_execution_count), (D(4), 1))

    def test_fresh_only_execution_link_has_no_commission_effect(self):
        value = fixture.bundle((fixture.order(),),
                               (fixture.execution(exec_id='commission-execution'),))
        original = evaluator._evaluate_broker_reconciliation(value)
        self.economics_unchanged(original, self.checked(page(value, commission())))

    def test_unknown_execution_cannot_manufacture_acquisition(self):
        result = self.checked(page(fixture.bundle(), commission()))
        self.assertEqual((result.executed_quantity, result.attributed_execution_count), (D(0), 0))
        self.assertFalse(result.strong_attribution)
        self.assertIsNone(result.lifecycle_status)

    def test_duplicate_report_occurrences_are_unresolved_without_economics(self):
        a, b = commission(), commission(sequence=3, received_at=AT + timedelta(seconds=1))
        self.assertEqual(a.report_id, b.report_id)
        result = self.checked(page(self.value, a, b))
        self.assertEqual(result.attributed_execution_count, 0)

    def test_financial_conflicts_remain_distinct_without_lifecycle_conflict_policy(self):
        for changes in ({'amount': D('0.06')}, {'currency': 'EUR'}):
            with self.subTest(changes=changes):
                a, b = commission(), commission(sequence=3, **changes)
                self.assertNotEqual(a.report_id, b.report_id)
                value = page(self.value, a, b, commission(sequence=4))
                self.checked(value)
                self.assertEqual(value.final_local.unread_events, (a, b, commission(sequence=4)))

    def test_existing_replay_conflicting_commissions_still_reject(self):
        j = journal_fixture
        for changes in ({'amount': D('0.30')}, {'currency': 'EUR'}):
            with self.subTest(changes=changes), self.assertRaises(ReadOnlyError) as caught:
                j.replay(*j.prefix(), j.execution(), j.commission(), j.commission(**changes))
            self.assertEqual(caught.exception.code, 'INVALID_PAPER_EXECUTION_JOURNAL')

    def test_multiple_interleaved_commissions_preserve_all_source_occurrences(self):
        events = (commission(), open_callback(self.value, 3), commission(sequence=4, exec_id='other'),
                  execution_callback(self.value, 5), commission(sequence=6))
        value = page(self.value, *events)
        self.checked(value)
        self.assertEqual(value.final_local.unread_events, events)


class MixedPagesTests(CompatibilityCase):
    pass


def mixed_test(builder, commission_first):
    def test(self):
        a, b = (commission(), builder(self.value, 3)) if commission_first else (
            builder(self.value, 2), commission(sequence=3))
        value = page(self.value, a, b)
        result = self.checked(value)
        self.assertEqual(result.executed_quantity, D(0))
        self.assertEqual(result.attributed_execution_count, 0)
        self.assertEqual(tuple(e.sequence for e in value.final_local.unread_events), (2, 3))
    return test


for label, builder in (('open', open_callback), ('status', status_callback), ('execution', execution_callback)):
    for first in (True, False):
        setattr(MixedPagesTests, 'test_commission_' + ('before_' if first else 'after_') + label,
                mixed_test(builder, first))


class AssessmentStabilityTests(CompatibilityCase):
    def test_commission_free_zero_terminal_stays_unresolved(self):
        value = fixture.status_bundle('Cancelled', 0, 10,
            (fixture.order(source='COMPLETED_ORDER', status='Cancelled'),))
        result = self.checked(value)
        self.assertIsNot(result.state, S.TERMINAL_UNFILLED)
        self.assertEqual(result.executed_quantity, D(0))

    def test_defensive_terminal_unfilled_planner_still_grants_no_authority(self):
        plan = recovery_fixture.planned(S.TERMINAL_UNFILLED)
        self.assertFalse(plan.new_entry_permitted)
        self.assertFalse(plan.write_session_reusable)


_BASELINES = (
    (S.INCOMPLETE, D(0), evaluator._QuantityCertainty.NONE, False, 0, None),
    (S.UNRESOLVED, D(0), evaluator._QuantityCertainty.NONE, False, 0, None),
    (S.CONFLICT, D(0), evaluator._QuantityCertainty.NONE, False, 0, None),
    (S.OPEN_WORKING, D(0), evaluator._QuantityCertainty.NONE, True, 0, 'Submitted'),
    (S.PARTIALLY_FILLED_WORKING, D(4), evaluator._QuantityCertainty.LOWER_BOUND, True, 1, 'Submitted'),
    (S.PARTIALLY_FILLED_TERMINAL, D(4), evaluator._QuantityCertainty.LOWER_BOUND, True, 1, 'Cancelled'),
    (S.FILLED, D(10), evaluator._QuantityCertainty.EXACT, True, 1, None),
)


def baseline_test(state, quantity, certainty, strong, count, lifecycle):
    def test(self):
        value = recovery_fixture.scenario(state)
        original = self.checked(value, state)
        self.assertEqual((original.executed_quantity, original.quantity_certainty,
                          original.strong_attribution, original.attributed_execution_count,
                          original.lifecycle_status), (quantity, certainty, strong, count, lifecycle))
        result = self.checked(page(value, commission()),
            state if state in (S.INCOMPLETE, S.CONFLICT) else S.UNRESOLVED)
        self.economics_unchanged(original, result)
        unchanged = ('client_order_id', 'order_id', 'read_generation', 'read_start_marker',
            'read_completion_marker', 'read_outcome', 'open_source_complete', 'completed_coverage',
            'execution_coverage', 'initial_barriers', 'final_barriers',
            'initial_authorization_status', 'final_authorization_status')
        for name in unchanged:
            self.assertEqual(getattr(result, name), getattr(original, name), name)
    return test


for args in _BASELINES:
    setattr(AssessmentStabilityTests, 'test_baseline_' + args[0].value.lower(), baseline_test(*args))


class BoundaryTests(CompatibilityCase):
    def test_no_storage_network_sdk_clock_or_file_activity(self):
        value = page(self.value, commission())
        forbidden = AssertionError('compatibility must remain pure')
        with patch('sqlite3.connect', side_effect=forbidden), \
             patch('socket.socket.connect', side_effect=forbidden), \
             patch('builtins.open', side_effect=forbidden), \
             patch.object(paper, '_commission_receipt_time', side_effect=forbidden), \
             patch.object(paper.PaperTWSTransport, '_dispatch_new', side_effect=forbidden), \
             patch.object(storage._SQLiteJournalStore, '_append', side_effect=forbidden), \
             patch.object(journal_owner._PaperDispatchJournalOwner, '_load', side_effect=forbidden):
            self.checked(value)

    def test_no_callback_synchronization_or_authorization_mutation(self):
        value = page(self.value, commission())
        with patch.object(dispatch._PaperOrderDispatchCoordinator, '_sync_broker_callbacks',
                          side_effect=AssertionError('callback acknowledgement forbidden')):
            self.checked(value)
        self.assertEqual(value.final_local.authorization_status, self.value.final_local.authorization_status)
        self.assertEqual(evaluator._barriers(value.final_local), evaluator._barriers(self.value.final_local))

    def test_recovery_remains_advisory_with_valid_unread_commission(self):
        value = page(recovery_fixture.scenario(S.FILLED), commission())
        plan = recovery._plan_paper_recovery(value)
        self.assertIs(plan.assessment_state, S.UNRESOLVED)
        self.assertFalse(plan.new_entry_permitted)
        self.assertFalse(plan.write_session_reusable)
        self.assertFalse(plan.complete_commission_evidence)
        self.assertFalse(plan.exact_position_quantity_recoverable)
        self.assertFalse(plan.strategy_management_state_recoverable)
        self.assertTrue(plan.explicit_reauthorization_required)

    def test_immutable_opaque_values_and_sanitized_error(self):
        event = commission()
        result = self.checked(page(self.value, event))
        self.assertEqual(repr(event), '<SanitizedPaperCallbackEvidence>')
        self.assertEqual(repr(result), '<PrivateBrokerReconciliationAssessment>')
        self.assertFalse(hasattr(event, '__dict__'))
        with self.assertRaises(FrozenInstanceError):
            event.amount = D(9)
        object.__setattr__(event, 'exec_id', 'unapproved private text')
        self.invalid(event)

    def test_no_new_public_model_or_authority_field(self):
        import tradingbot_broker
        self.assertFalse(hasattr(tradingbot_broker, '_CommissionEvidence'))
        event = commission()
        self.assertEqual([f.name for f in fields(event)],
            ['generation', 'sequence', 'exec_id', 'report_id', 'amount', 'currency', 'received_at'])
        names = {f.name for f in fields(evaluator._evaluate_broker_reconciliation(self.value))}
        self.assertTrue(names.isdisjoint({'authorized', 'can_clear', 'safe_to_trade', 'fee_complete',
            'commission_total', 'new_entry_permitted', 'current_inventory', 'recovery_authority'}))
        self.assertEqual(tuple(inspect.signature(evaluator._evaluate_broker_reconciliation).parameters), ('bundle',))

    def test_unknown_account_snapshot_and_live_boundary_remain(self):
        self.assertIsNone(self.value.read_receipt.public_snapshot.account.mode)
        self.assertFalse(recovery._plan_paper_recovery(page(self.value, commission())).new_entry_permitted)

    def test_thread_neutral_deterministic_projection(self):
        value = page(self.value, commission())
        expected = evaluator._evaluate_broker_reconciliation(value)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = tuple(pool.map(evaluator._evaluate_broker_reconciliation, (value,) * 12))
        self.assertEqual(results, (expected,) * 12)
        self.assertEqual(value.final_local.callback_cursor, 1)


class InterruptionTests(CompatibilityCase):
    pass


def interruption_test(kind):
    def test(self):
        value = page(self.value, commission())
        original = kind('private interruption')
        with patch.object(paper._CommissionEvidence, '__post_init__', side_effect=original):
            with self.assertRaises(kind) as caught:
                evaluator._evaluate_broker_reconciliation(value)
        self.assertIs(caught.exception, original)
        self.assertEqual(value.final_local.callback_cursor, 1)
    return test


for kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    setattr(InterruptionTests, 'test_' + kind.__name__, interruption_test(kind))


class ProducerIntegrationTests(CommissionFixture):
    def safety_state(self):
        return (self.coordinator._reconciliation_required, self.coordinator._pending_confirmation,
                self.write._requires_reconciliation(), self.broker.reconciliation_required,
                self.broker.paper_execution_status, self.broker.account_summary())

    def test_both_real_capture_paths_are_accepted_without_acknowledgement(self):
        self.build()
        self.commission(amount=D('-0.03'))
        self.receipt += timedelta(seconds=1)
        self.commission('fees', amount=D('-0.03'))
        events = self.page().events
        self.assertEqual(events[0].report_id, events[1].report_id)
        value = page(fixture.bundle((fixture.order(),)), *events, cursor=0)
        before = (self.coordinator._callback_cursor, self.journal._persistence_cursor,
                  self.journal._load(), self.safety_state(), tuple(self.write_calls()), self.page())
        result = evaluator._evaluate_broker_reconciliation(value)
        self.assertIs(result.state, S.UNRESOLVED)
        self.assertEqual(before, (self.coordinator._callback_cursor, self.journal._persistence_cursor,
                  self.journal._load(), self.safety_state(), tuple(self.write_calls()), self.page()))

    def test_public_broker_and_private_live_entry_remain_blocked(self):
        from tradingbot_broker.broker import BrokerOperationError
        from tradingbot_broker.models import BrokerReason, TradingMode
        from tests.dispatch_journal_fixtures import permission
        self.build()
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.broker.account_summary().mode)
        live = replace(self.order, mode=TradingMode.LIVE)
        with self.assertRaises(BrokerOperationError) as caught:
            self.broker.place_order(live, self.clock(), permission(live))
        self.assertIs(caught.exception.reason, BrokerReason.READ_ONLY_BROKER_TRANSPORT)
        outcome = self.invoke(live, permission(live))
        self.assert_denied(outcome)
        self.assertFalse(self.write_calls())


if __name__ == '__main__':
    unittest.main()
