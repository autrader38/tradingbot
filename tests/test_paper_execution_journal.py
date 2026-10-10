"""Offline journal facts, hostile inputs and pure complete-history replay."""

import ast
import builtins
from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal, Inexact, Rounded, localcontext
import inspect
import socket
import time
import unittest
from unittest.mock import patch

from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tradingbot_broker.ibkr_paper_transport import MIN_ORDER_ID, MAX_ORDER_ID


D = Decimal
AT = datetime(2026, 1, 5, 15, 45, tzinfo=timezone.utc)
SID = 'session-' + '1' * 32
SID2 = 'session-' + '2' * 32
S = j._LifecycleStatus


def header():
    return j._JournalHeader(1, 'journal-' + 'a' * 32,
                           j._JournalAccountBinding('binding-' + 'b' * 32))


def session(sid=SID, client=19, generation=3):
    return j._SessionRegistered(sid, client, generation)


def order(key='entry-1', quantity='10'):
    return j._JournalOrderIntent(key, 'security-test', 'TEST', 'USD', 'BUY', D(quantity),
                                'MKT', 'DAY', AT, AT + timedelta(minutes=5))


def contract():
    return j._JournalContractSnapshot('security-test', 1234, 'STK', 'SMART', 'TEST', 'USD',
        AT - timedelta(hours=1), AT + timedelta(days=1), AT - timedelta(minutes=1),
        'verified-contract-v1', True)


def strategy(quantity='10'):
    witnesses = tuple(j._StrategyWitness(kind, D(level), AT - timedelta(minutes=minutes),
        'witness-' + kind.value) for kind, level, minutes in
        zip(j._WitnessKind, ('7', '10', '9', '10.1'), (15, 10, 5, 1)))
    return j._JournalStrategyContext('v1.0', 'frozen-v1.0', 'setup-test', 'security-test',
        'TEST', date(2026, 1, 5), witnesses, AT, D(10), D('8.955'), D('8.95'),
        D('12.10'), D('12.10'), D(quantity), D(100), D(10000), 'stop-ticks-v1', 'target-ticks-v1')


def risk(key='entry-1', quantity='10'):
    return j._JournalRiskContext(order(key, quantity), True, AT - timedelta(minutes=1), AT + timedelta(minutes=5),
                               'external-risk-v1')


def consumed(key='entry-1', sid=SID, quantity='10'):
    return j._AttemptConsumed(sid, key, order(key, quantity), contract(), strategy(quantity), risk(key, quantity))


def source(sequence=1, sid=SID, generation=3):
    return j._CallbackSource(sid, generation, sequence)


def opened(sequence=1, **changes):
    return replace(j._OpenOrderObserved('entry-1', source(sequence), 7, 19, 'entry-1', 1234,
        'STK', 'SMART', 'TEST', 'USD', 'BUY', D(10), 'MKT', 'DAY', S.SUBMITTED), **changes)


def status(sequence=2, **changes):
    return replace(j._OrderStatusObserved('entry-1', source(sequence), 7, 19, S.SUBMITTED,
                                         D(0), D(10), D(0), D(0), 0), **changes)


def execution(sequence=3, **changes):
    return replace(j._ExecutionObserved('entry-1', source(sequence), 7, 19, 'entry-1',
        'exec-1', 1234, 'BOT', D(3), D('10.125')), **changes)


def commission(**changes):
    return replace(j._CommissionReceived('entry-1', 'exec-1', 'report-1', D('0.25'), 'USD', AT), **changes)


def prefix():
    return (session(), consumed(), j._NumericOrderIdReserved('entry-1', 7),
            j._InvocationMayOccur('entry-1'))


def records(events, h=None):
    h = header() if h is None else h
    return tuple(j._JournalRecord(n, h.schema_version, h.journal_id, h.account_binding, event)
                 for n, event in enumerate(events, 1))


def replay(*events):
    return j._replay_paper_execution_journal(header(), records(events))


def forged(value, **changes):
    result = object.__new__(type(value))
    for f in fields(value):
        object.__setattr__(result, f.name, changes.get(f.name, getattr(value, f.name)))
    return result


class JournalTests(unittest.TestCase):
    def invalid(self, *events):
        with self.assertRaises(ReadOnlyError) as caught:
            replay(*events)
        self.assertEqual(str(caught.exception), j._ERROR)
        self.assertEqual(caught.exception.code, j._ERROR)

    def test_empty_explicit_journal_is_quarantined_not_missing_storage(self):
        result = replay()
        self.assertEqual(result.last_sequence, 0)
        self.assertTrue(result.restart_quarantine_required)
        self.assertIs(result.header.account_mode, AccountMode.UNKNOWN)
        with self.assertRaises(ReadOnlyError):
            j._replay_paper_execution_journal(None, ())

    def test_exact_header_and_contiguous_records(self):
        result = replay(*prefix())
        self.assertEqual(result.last_sequence, 4)
        self.assertEqual(result.header, header())

    def test_record_schema_must_match_supported_version(self):
        bad = forged(records((session(),))[0], schema_version=2)
        with self.assertRaises(ReadOnlyError):
            j._replay_paper_execution_journal(header(), (bad,))

    def test_record_journal_identity_cannot_change(self):
        value = replace(records((session(),))[0], journal_id='journal-' + 'c' * 32)
        with self.assertRaises(ReadOnlyError):
            j._replay_paper_execution_journal(header(), (value,))

    def test_record_account_binding_cannot_change(self):
        value = replace(records((session(),))[0], account_binding=j._JournalAccountBinding('binding-' + 'c' * 32))
        with self.assertRaises(ReadOnlyError):
            j._replay_paper_execution_journal(header(), (value,))

    def test_record_sequences_reject_gap_duplicate_and_regression(self):
        values = records((session(), consumed()))
        for numbers in ((1, 3), (1, 1), (2, 1)):
            with self.subTest(numbers=numbers), self.assertRaises(ReadOnlyError):
                j._replay_paper_execution_journal(header(), tuple(
                    replace(v, sequence=n) for v, n in zip(values, numbers)))

    def test_mutable_record_container_rejected(self):
        with self.assertRaises(ReadOnlyError):
            j._replay_paper_execution_journal(header(), list(records(prefix())))

    def test_reconstructed_equal_facts_are_data_not_capabilities(self):
        self.assertEqual(j._replay_paper_execution_journal(replace(header()), records(prefix())), replay(*prefix()))

    def test_exact_session_repetition_is_idempotent(self):
        result = replay(session(), session())
        self.assertEqual(result.sessions, (session(),))

    def test_conflicting_session_registration_rejected(self):
        self.invalid(session(), session(client=20))
        self.invalid(session(), session(generation=4))

    def test_attempt_requires_registered_session(self):
        self.invalid(consumed())

    def test_denied_attempt_can_preserve_missing_optional_context(self):
        value = j._AttemptConsumed(SID, 'entry-1', order())
        result = replay(session(), value,
                        j._AttemptClosedWithoutInvocation('entry-1', j._ClosureReason.LOCAL_GATE_DENIED))
        self.assertEqual(result.consumed_client_order_ids, frozenset(('entry-1',)))
        self.assertFalse(result.attempts[0].invocation_may_have_occurred)
        self.assertEqual(result.attempts[0].consumed, value)

    def test_client_id_same_order_cannot_start_second_attempt(self):
        self.invalid(session(), consumed(), consumed())

    def test_client_id_different_order_cannot_start_second_attempt(self):
        self.invalid(session(), consumed(), consumed(quantity='11'))

    def test_client_id_new_session_cannot_start_second_attempt(self):
        self.invalid(session(), consumed(), session(SID2), consumed(sid=SID2))

    def test_client_id_after_denial_stays_consumed(self):
        self.invalid(session(), consumed(), j._AttemptClosedWithoutInvocation('entry-1',
                     j._ClosureReason.LOCAL_GATE_DENIED), consumed())

    def test_client_id_after_uncertainty_stays_consumed(self):
        self.invalid(*prefix(), consumed())

    def test_client_id_after_terminal_observation_stays_consumed(self):
        self.invalid(*prefix(), opened(status=S.CANCELLED), consumed())

    def test_context_contract_symbol_mismatch_rejected(self):
        with self.assertRaises(ReadOnlyError):
            replace(consumed(), contract=replace(contract(), symbol='OTHER'))

    def test_context_contract_security_mismatch_rejected(self):
        with self.assertRaises(ReadOnlyError):
            replace(consumed(), contract=replace(contract(), security_id='other'))

    def test_context_contract_currency_mismatch_rejected(self):
        with self.assertRaises(ReadOnlyError):
            replace(consumed(), contract=replace(contract(), currency='EUR'))

    def test_context_strategy_security_mismatch_rejected(self):
        with self.assertRaises(ReadOnlyError):
            replace(consumed(), strategy=replace(strategy(), security_id='other'))

    def test_context_strategy_quantity_mismatch_rejected(self):
        with self.assertRaises(ReadOnlyError):
            replace(consumed(), strategy=strategy('11'))

    def test_numeric_id_reserved_once(self):
        result = replay(session(), consumed(), j._NumericOrderIdReserved('entry-1', 7))
        self.assertEqual(result.reserved_order_ids, ((7, 'entry-1'),))
        self.assertEqual(result.order_id_floor, 8)

    def test_numeric_id_same_attempt_repeat_rejected(self):
        self.invalid(session(), consumed(), j._NumericOrderIdReserved('entry-1', 7),
                     j._NumericOrderIdReserved('entry-1', 7))

    def test_attempt_cannot_reserve_second_numeric_id(self):
        self.invalid(session(), consumed(), j._NumericOrderIdReserved('entry-1', 7),
                     j._NumericOrderIdReserved('entry-1', 8))

    def test_numeric_id_other_attempt_rejected(self):
        self.invalid(session(), consumed(), consumed('entry-2'),
                     j._NumericOrderIdReserved('entry-1', 7), j._NumericOrderIdReserved('entry-2', 7))

    def test_numeric_id_other_session_rejected(self):
        self.invalid(session(), consumed(), j._NumericOrderIdReserved('entry-1', 7),
                     session(SID2), consumed('entry-2', SID2), j._NumericOrderIdReserved('entry-2', 7))

    def test_numeric_id_after_closed_attempt_stays_consumed(self):
        self.invalid(session(), consumed(), j._NumericOrderIdReserved('entry-1', 7),
            j._AttemptClosedWithoutInvocation('entry-1', j._ClosureReason.PREFLIGHT_FAILED),
            consumed('entry-2'), j._NumericOrderIdReserved('entry-2', 7))

    def test_numeric_id_after_uncertainty_stays_consumed(self):
        self.invalid(*prefix(), consumed('entry-2'), j._NumericOrderIdReserved('entry-2', 7))

    def test_numeric_id_after_broker_observation_stays_consumed(self):
        self.invalid(*prefix(), opened(), consumed('entry-2'), j._NumericOrderIdReserved('entry-2', 7))

    def test_floor_lower_observation_is_conservatively_ignored(self):
        result = replay(session(), j._OrderIdFloorObserved(SID, 12), j._OrderIdFloorObserved(SID, 4))
        self.assertEqual(result.order_id_floor, 12)

    def test_observed_order_id_raises_next_floor(self):
        result = replay(session(), j._BrokerOrderIdObserved(SID, 12))
        self.assertEqual(result.order_id_floor, 13)
        self.invalid(session(), j._BrokerOrderIdObserved(SID, 12), consumed(),
                     j._NumericOrderIdReserved('entry-1', 12))

    def test_reserved_max_id_exhausts_without_wrapping(self):
        result = replay(session(), consumed(), j._NumericOrderIdReserved('entry-1', MAX_ORDER_ID),
                        j._InvocationMayOccur('entry-1'))
        self.assertTrue(result.allocator_exhausted)
        self.assertEqual(result.order_id_floor, MAX_ORDER_ID)

    def test_observed_max_id_permanently_exhausts(self):
        result = replay(session(), j._BrokerOrderIdObserved(SID, MAX_ORDER_ID), session(SID2))
        self.assertTrue(result.allocator_exhausted)
        self.invalid(session(), j._BrokerOrderIdObserved(SID, MAX_ORDER_ID), consumed(),
                     j._NumericOrderIdReserved('entry-1', MAX_ORDER_ID))

    def test_explicit_exhaustion_cannot_be_cleared(self):
        result = replay(session(), j._AllocatorExhausted(SID), j._OrderIdFloorObserved(SID, 0), session(SID2))
        self.assertEqual(result.order_id_floor, MAX_ORDER_ID)
        self.assertTrue(result.allocator_exhausted)
        self.assertTrue(result.reconciliation_required)

    def test_minimum_zero_numeric_id_is_preserved(self):
        result = replay(session(), consumed(), j._NumericOrderIdReserved('entry-1', MIN_ORDER_ID))
        self.assertEqual(result.attempts[0].order_id, 0)

    def test_marker_without_attempt_rejected(self):
        self.invalid(session(), j._InvocationMayOccur('entry-1'))

    def test_marker_without_reservation_rejected(self):
        self.invalid(session(), consumed(), j._InvocationMayOccur('entry-1'))

    def test_marker_duplicate_rejected(self):
        self.invalid(*prefix(), j._InvocationMayOccur('entry-1'))

    def test_marker_without_return_preserves_uncertainty(self):
        state = replay(*prefix()).attempts[0]
        self.assertTrue(state.invocation_may_have_occurred)
        self.assertTrue(state.sdk_outcome_uncertain)
        self.assertTrue(state.broker_control_unresolved)

    def test_no_marker_does_not_claim_not_called(self):
        state = replay(session(), consumed()).attempts[0]
        self.assertIsNone(state.closure_reason)
        self.assertFalse(state.invocation_may_have_occurred)
        self.assertFalse(hasattr(state, 'not_dispatched'))

    def test_return_without_marker_rejected(self):
        self.invalid(session(), consumed(), j._SdkReturnedNormally('entry-1'))

    def test_return_duplicate_rejected(self):
        self.invalid(*prefix(), j._SdkReturnedNormally('entry-1'), j._SdkReturnedNormally('entry-1'))

    def test_return_is_not_broker_acceptance(self):
        state = replay(*prefix(), j._SdkReturnedNormally('entry-1')).attempts[0]
        self.assertTrue(state.sdk_returned_normally)
        self.assertFalse(state.broker_observed)
        self.assertTrue(state.broker_control_unresolved)

    def test_open_before_return_and_return_after_open(self):
        state = replay(*prefix(), opened(), j._SdkReturnedNormally('entry-1')).attempts[0]
        self.assertTrue(state.strong_open_order_identity_established)
        self.assertTrue(state.sdk_returned_normally)

    def test_execution_before_return_and_return_after_execution(self):
        state = replay(*prefix(), execution(), j._SdkReturnedNormally('entry-1')).attempts[0]
        self.assertEqual(state.observed_execution_quantity, D(3))
        self.assertFalse(state.strong_open_order_identity_established)

    def test_callback_before_marker_rejected(self):
        self.invalid(session(), consumed(), j._NumericOrderIdReserved('entry-1', 7), opened())

    def test_close_without_marker_keeps_both_ids(self):
        result = replay(session(), consumed(), j._NumericOrderIdReserved('entry-1', 7),
                        j._AttemptClosedWithoutInvocation('entry-1', j._ClosureReason.PREFLIGHT_FAILED))
        self.assertEqual(result.reserved_order_ids, ((7, 'entry-1'),))
        self.assertIn('entry-1', result.consumed_client_order_ids)

    def test_close_after_marker_rejected(self):
        self.invalid(*prefix(), j._AttemptClosedWithoutInvocation('entry-1', j._ClosureReason.PREFLIGHT_FAILED))

    def test_marker_after_close_rejected(self):
        self.invalid(session(), consumed(), j._NumericOrderIdReserved('entry-1', 7),
            j._AttemptClosedWithoutInvocation('entry-1', j._ClosureReason.PREFLIGHT_FAILED),
            j._InvocationMayOccur('entry-1'))

    def test_duplicate_close_rejected(self):
        close = j._AttemptClosedWithoutInvocation('entry-1', j._ClosureReason.PREFLIGHT_FAILED)
        self.invalid(session(), consumed(), close, close)

    def test_reconciliation_reasons_are_monotonic_and_ordered(self):
        events = (j._ReconciliationRequired(j._ReconciliationReason.SESSION_LOSS, 'entry-1'),
                  j._ReconciliationRequired(j._ReconciliationReason.CALLBACK_GAP, 'entry-1'))
        result = replay(*prefix(), *events, events[0], opened(), session(SID2))
        self.assertEqual(result.reconciliation_reasons,
            (j._ReconciliationReason.CALLBACK_GAP, j._ReconciliationReason.SESSION_LOSS))
        self.assertEqual(result.attempts[0].reconciliation_reasons, result.reconciliation_reasons)

    def test_session_reconciliation_prevents_later_invocation(self):
        self.invalid(session(), consumed(), j._NumericOrderIdReserved('entry-1', 7),
            j._ReconciliationRequired(j._ReconciliationReason.TRANSPORT_POISON, session_id=SID),
            j._InvocationMayOccur('entry-1'))

    def test_reconciliation_wrong_attempt_session_rejected(self):
        self.invalid(*prefix(), session(SID2), j._ReconciliationRequired(
            j._ReconciliationReason.SESSION_LOSS, 'entry-1', SID2))

    def test_explicit_dispatch_uncertainty_cannot_be_cleared_by_return(self):
        result = replay(*prefix(), j._ReconciliationRequired(
            j._ReconciliationReason.DISPATCH_INTERRUPTED, 'entry-1'),
            j._SdkReturnedNormally('entry-1'))
        self.assertTrue(result.attempts[0].sdk_returned_normally)
        self.assertTrue(result.attempts[0].sdk_outcome_uncertain)
        self.assertTrue(result.reconciliation_required)

    def test_session_reconciliation_is_retained_on_owned_attempts(self):
        result = replay(session(), j._ReconciliationRequired(
            j._ReconciliationReason.TRANSPORT_POISON, session_id=SID), consumed())
        self.assertEqual(result.attempts[0].reconciliation_reasons,
                         (j._ReconciliationReason.TRANSPORT_POISON,))
        self.assertTrue(result.attempts[0].broker_control_unresolved)

    def test_open_missing_reference_is_secondary_only(self):
        state = replay(*prefix(), opened(order_ref=None)).attempts[0]
        self.assertFalse(state.strong_open_order_identity_established)
        self.assertIsNone(state.open_observations[0].order_ref)

    def test_unknown_open_status_cannot_confirm_lifecycle(self):
        result = replay(*prefix(), opened(status=S.UNKNOWN))
        self.assertFalse(result.attempts[0].strong_open_order_identity_established)
        self.assertTrue(result.reconciliation_required)

    def test_order_status_never_establishes_strong_identity(self):
        state = replay(*prefix(), status()).attempts[0]
        self.assertFalse(state.strong_open_order_identity_established)

    def test_terminal_status_before_identity_latches_reconciliation(self):
        result = replay(*prefix(), status(status=S.CANCELLED))
        self.assertTrue(result.reconciliation_required)
        self.assertFalse(result.attempts[0].broker_observed)

    def test_status_same_event_quantities_are_retained(self):
        event = status(filled=D(3), remaining=D(7))
        state = replay(*prefix(), opened(), event).attempts[0]
        self.assertEqual(state.status_observations, (event,))
        self.assertEqual(state.historical_filled_lower_bound, D(3))

    def test_unanchored_status_economics_are_not_owned_execution_totals(self):
        result = replay(*prefix(), status(filled=D(3), remaining=D(7)))
        self.assertTrue(result.reconciliation_required)
        self.assertEqual(result.attempts[0].observed_execution_quantity, D(0))
        self.assertEqual(result.attempts[0].historical_filled_lower_bound, D(0))

    def test_status_arithmetic_mismatch_rejected(self):
        self.invalid(*prefix(), status(filled=D(3), remaining=D(8)))

    def test_filled_status_requires_its_own_full_totals(self):
        self.invalid(*prefix(), status(status=S.FILLED))

    def test_terminal_open_cannot_attach_to_prior_zero_status_totals(self):
        state = replay(*prefix(), opened(), status(), opened(3, status=S.CANCELLED)).attempts[0]
        self.assertEqual(state.open_observations[-1].status, S.CANCELLED)
        self.assertEqual(state.status_observations[-1].status, S.SUBMITTED)
        self.assertFalse(hasattr(state, 'terminal_unfilled'))

    def test_missing_execution_ref_requires_prior_strong_open(self):
        self.invalid(*prefix(), execution(order_ref=None))
        self.invalid(*prefix(), execution(), execution(4, exec_id='exec-2', order_ref=None))

    def test_missing_execution_ref_after_open_requires_supplied_exact_client(self):
        state = replay(*prefix(), opened(), execution(order_ref=None)).attempts[0]
        self.assertEqual(state.observed_execution_quantity, D(3))
        self.invalid(*prefix(), opened(), execution(order_ref=None, client_id=None))

    def test_missing_execution_client_is_not_default_zero(self):
        state = replay(*prefix(), execution(client_id=None)).attempts[0]
        self.assertIsNone(state.executions[0].client_id)

    def test_exact_source_callback_replay_is_idempotent(self):
        event = execution()
        state = replay(*prefix(), event, event).attempts[0]
        self.assertEqual(len(state.executions), 1)
        self.assertEqual(state.observed_execution_quantity, D(3))

    def test_same_source_sequence_different_callback_rejected(self):
        self.invalid(*prefix(), opened(), status(1))

    def test_duplicate_exec_id_new_source_sequence_is_counted_once(self):
        state = replay(*prefix(), execution(), execution(4)).attempts[0]
        self.assertEqual(state.observed_execution_quantity, D(3))
        self.assertEqual(len(state.executions), 1)

    def test_distinct_executions_sum_without_double_counting(self):
        state = replay(*prefix(), execution(), execution(4, exec_id='exec-2', shares=D(7)),
                       execution(5)).attempts[0]
        self.assertEqual(state.observed_execution_quantity, D(10))

    def test_execution_overfill_rejected(self):
        self.invalid(*prefix(), execution(), execution(4, exec_id='exec-2', shares=D(8)))

    def test_execution_cumulative_cannot_exceed_order(self):
        self.invalid(*prefix(), execution(cumulative_quantity=D(11)))

    def test_cumulative_does_not_invent_missing_executions_or_prices(self):
        state = replay(*prefix(), execution(cumulative_quantity=D(10))).attempts[0]
        self.assertEqual(state.observed_execution_quantity, D(3))
        self.assertEqual(state.historical_filled_lower_bound, D(10))
        self.assertEqual(len(state.executions), 1)

    def test_execution_capture_order_is_not_economic_chronology(self):
        state = replay(*prefix(), execution(cumulative_quantity=D(6)),
            execution(2, exec_id='exec-earlier', cumulative_quantity=D(3))).attempts[0]
        self.assertEqual(state.observed_execution_quantity, D(6))

    def test_partial_fill_does_not_infer_current_remainder(self):
        state = replay(*prefix(), execution()).attempts[0]
        self.assertEqual(state.observed_execution_quantity, D(3))
        self.assertTrue(state.broker_control_unresolved)
        self.assertFalse(hasattr(state, 'current_remaining'))

    def test_full_execution_quantity_does_not_create_portfolio_readiness(self):
        state = replay(*prefix(), execution(shares=D(10))).attempts[0]
        self.assertEqual(state.observed_execution_quantity, D(10))
        self.assertIn(j._UnresolvedObligation.PROTECTIVE_MANAGEMENT, state.unresolved_obligations)
        self.assertIn(j._UnresolvedObligation.COMMISSION_ACCOUNTING, state.unresolved_obligations)

    def test_local_status_full_quantity_does_not_create_execution_prices(self):
        state = replay(*prefix(), opened(), status(status=S.FILLED, filled=D(10), remaining=D(0))).attempts[0]
        self.assertEqual(state.historical_filled_lower_bound, D(10))
        self.assertEqual(state.executions, ())
        self.assertIn(j._UnresolvedObligation.EXECUTION_PRICE_COVERAGE, state.unresolved_obligations)

    def test_late_commission_and_negative_rebate_are_representable(self):
        event = commission(amount=D('-0.05'))
        state = replay(*prefix(), execution(), j._SdkReturnedNormally('entry-1'), event).attempts[0]
        self.assertEqual(state.commissions, (event,))

    def test_commission_duplicate_does_not_create_completeness(self):
        state = replay(*prefix(), execution(), commission(), commission(received_at=AT + timedelta(hours=1))).attempts[0]
        self.assertEqual(len(state.commissions), 1)
        self.assertIn(j._UnresolvedObligation.COMMISSION_ACCOUNTING, state.unresolved_obligations)

    def test_conflicting_commission_amount_rejected(self):
        self.invalid(*prefix(), execution(), commission(), commission(amount=D('0.30')))

    def test_commission_without_exact_execution_link_rejected(self):
        self.invalid(*prefix(), commission())
        self.invalid(*prefix(), execution(), commission(exec_id='foreign'))

    def test_commission_currency_conflict_rejected(self):
        self.invalid(*prefix(), execution(), commission(currency='EUR'))

    def test_missing_commission_is_not_zero(self):
        state = replay(*prefix(), execution()).attempts[0]
        self.assertEqual(state.commissions, ())
        self.assertFalse(hasattr(state, 'commission_total'))

    def test_callback_subset_does_not_infer_gap_or_advance_cursor(self):
        result = replay(*prefix(), opened(5), execution(12))
        self.assertEqual(result.observed_callback_watermarks, ((SID, 12),))
        self.assertEqual(result.callback_progress, ())
        self.assertNotIn(j._ReconciliationReason.CALLBACK_GAP, result.reconciliation_reasons)

    def test_processed_cursor_and_fetched_upper_bound_are_distinct(self):
        progress = j._CallbackProgress(SID, 1, 12)
        result = replay(*prefix(), opened(), progress)
        self.assertEqual(result.callback_progress, (progress,))
        self.assertEqual(result.callback_progress[0].processed_sequence, 1)

    def test_cursor_regression_rejected(self):
        self.invalid(session(), j._CallbackProgress(SID, 3, 12), j._CallbackProgress(SID, 2, 12))

    def test_fetch_watermark_regression_rejected(self):
        self.invalid(session(), j._CallbackProgress(SID, 3, 12), j._CallbackProgress(SID, 3, 11))

    def test_new_session_source_sequence_can_start_independently(self):
        events = prefix() + (execution(), session(SID2, generation=1), consumed('entry-2', SID2),
            j._NumericOrderIdReserved('entry-2', 8), j._InvocationMayOccur('entry-2'),
            execution(1, client_order_id='entry-2', order_id=8, order_ref='entry-2',
                      exec_id='exec-new', source=source(1, SID2, 1)))
        result = replay(*events)
        self.assertEqual(result.observed_callback_watermarks, ((SID, 3), (SID2, 1)))

    def test_new_session_does_not_transfer_pending_ownership(self):
        self.invalid(*prefix(), session(SID2), execution(source=source(1, SID2)))

    def test_wrong_source_write_generation_rejected(self):
        self.invalid(*prefix(), execution(source=source(1, generation=4)))

    def test_session_change_preserves_all_safety_history(self):
        result = replay(*prefix(), execution(), j._ReconciliationRequired(
            j._ReconciliationReason.SESSION_LOSS, 'entry-1'), session(SID2, generation=1))
        self.assertEqual(result.consumed_client_order_ids, frozenset(('entry-1',)))
        self.assertEqual(result.reserved_order_ids, ((7, 'entry-1'),))
        self.assertEqual(result.order_id_floor, 8)
        self.assertEqual(result.attempts[0].observed_execution_quantity, D(3))
        self.assertTrue(result.reconciliation_required)
        self.assertTrue(result.attempts[0].sdk_outcome_uncertain)

    def test_exact_replay_is_deterministic_and_input_unchanged(self):
        h, rs = header(), records(prefix() + (opened(), execution(), commission()))
        before = tuple((r.sequence, r.payload) for r in rs)
        first = j._replay_paper_execution_journal(h, rs)
        self.assertEqual(first, j._replay_paper_execution_journal(h, rs))
        self.assertEqual(before, tuple((r.sequence, r.payload) for r in rs))

    def test_models_and_nested_results_are_frozen_slotted_opaque(self):
        values = (header(), header().account_binding, session(), consumed(), order(), contract(),
            strategy(), strategy().witnesses[0], risk(), records(prefix())[0], source(), opened(),
            status(), execution(), commission(), replay(*prefix()), replay(*prefix()).attempts[0])
        for value in values:
            with self.subTest(model=type(value).__name__):
                self.assertFalse(hasattr(value, '__dict__'))
                self.assertEqual(repr(value), '<PrivatePaperExecutionJournalValue>')
                with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
                    setattr(value, fields(value)[0].name, None)

    def test_result_has_no_authority_or_mutable_objects(self):
        result = replay(*prefix(), opened(), execution(), commission())
        def visit(value):
            if hasattr(type(value), '__dataclass_fields__'):
                for f in fields(value):
                    self.assertNotIn(f.name, ('can_trade', 'can_retry', 'safe_to_send', 'authorized',
                        'capability', 'token', 'transport', 'coordinator', 'account', 'acctNumber'))
                    visit(getattr(value, f.name))
            elif type(value) in (tuple, frozenset):
                for item in value: visit(item)
            else:
                self.assertTrue(type(value) in (str, int, bool, Decimal, datetime, date, type(None))
                                or isinstance(value, (j._LifecycleStatus, j._ClosureReason,
                                    j._ReconciliationReason, j._WitnessKind, j._UnresolvedObligation, AccountMode)))
        visit(result)

    def test_decimal_precision_and_traps_do_not_change_exact_execution_union(self):
        small = consumed(quantity='0.3')
        events = (session(), small, j._NumericOrderIdReserved('entry-1', 7),
            j._InvocationMayOccur('entry-1'), execution(shares=D('0.1')),
            execution(4, exec_id='exec-2', shares=D('0.2')), execution(5, shares=D('0.1')))
        with localcontext() as context:
            context.prec = 1
            context.traps[Inexact] = context.traps[Rounded] = True
            context.Emax, context.Emin = 1, -1
            self.assertEqual(replay(*events).attempts[0].observed_execution_quantity, D('0.3'))

    def test_decimal_extreme_supported_exponents_sum_exactly(self):
        quantity = D('1E64')
        value = replace(consumed(), order=replace(order(), quantity=quantity),
                        strategy=replace(strategy(), original_quantity=quantity), risk=risk(quantity=quantity))
        events = (session(), value, j._NumericOrderIdReserved('entry-1', 7),
            j._InvocationMayOccur('entry-1'), execution(shares=D('1E-64')),
            execution(4, exec_id='large', shares=D('9E63')))
        with localcontext() as context:
            context.prec = 1
            state = replay(*events).attempts[0]
        # Construct the exact comparison without ambient arithmetic rounding.
        self.assertEqual(state.observed_execution_quantity.as_tuple().exponent, -64)
        self.assertEqual(state.observed_execution_quantity.as_tuple().digits, (9,) + (0,) * 126 + (1,))

    def test_low_precision_cannot_hide_an_overfill(self):
        with localcontext() as context:
            context.prec = 1
            self.invalid(*prefix(), execution(shares=D(10)), execution(4, exec_id='extra', shares=D('1E-64')))

    def test_purity_guarded_io_clock_lock_network_boundaries(self):
        import threading
        h, rs = header(), records(prefix() + (opened(), execution()))
        def fail(*args, **kwargs):
            raise AssertionError('unexpected side effect')
        with patch.object(builtins, 'open', fail), patch.object(socket, 'socket', fail), \
             patch.object(time, 'time', fail), patch.object(time, 'monotonic', fail), \
             patch.object(threading, 'RLock', fail):
            self.assertEqual(j._replay_paper_execution_journal(h, rs).last_sequence, len(rs))

    def test_module_ast_has_no_storage_sdk_clock_or_runtime_hooks(self):
        tree = ast.parse(inspect.getsource(j))
        forbidden = {'sqlite3', 'pathlib', 'os', 'socket', 'ibapi', 'threading', 'time', 'random',
                     'secrets', 'paper_dispatch', 'ibkr_paper_transport', 'paper_execution',
                     'paper_reconciliation', 'paper_recovery'}
        imports = {alias.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names}
        imports.update(n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom))
        self.assertFalse(imports & forbidden)
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertFalse(calls & {'open', 'fsync', 'monotonic', 'eval', 'exec'})

    def test_no_package_export_or_generic_broker_api_addition(self):
        import tradingbot_broker
        from tradingbot_broker.broker import Broker
        self.assertFalse(hasattr(tradingbot_broker, '_replay_paper_execution_journal'))
        self.assertFalse(any('journal' in name for name in vars(Broker)))

    def test_orderref_is_exact_and_never_normalized(self):
        for ref in ('ENTRY-1', ' entry-1', 'entry-1 ', 'entry-1-x', ''):
            with self.subTest(ref=ref):
                event = execution(order_ref=ref)
                self.assertEqual(event.order_ref, ref)
                self.invalid(*prefix(), event)

    def test_output_constructors_reject_mutable_nested_containers(self):
        result = replay(*prefix(), execution())
        with self.assertRaises(ReadOnlyError):
            replace(result, sessions=list(result.sessions))
        with self.assertRaises(ReadOnlyError):
            replace(result.attempts[0], executions=list(result.attempts[0].executions))

    def test_bypassed_constructor_is_revalidated_before_replay(self):
        value = forged(consumed(), order=forged(order(), quantity=D(-1)))
        self.invalid(session(), value)

    def test_invocation_marker_requires_complete_context(self):
        for field_name in ('contract', 'strategy', 'risk'):
            with self.subTest(field=field_name):
                self.invalid(session(), replace(consumed(), **{field_name: None}),
                    j._NumericOrderIdReserved('entry-1', 7), j._InvocationMayOccur('entry-1'))

    def test_invocation_marker_rejects_unverified_contract_or_denied_risk(self):
        for value in (replace(consumed(), contract=replace(contract(), verified=False)),
                      replace(consumed(), risk=replace(risk(), permits_entry=False))):
            self.invalid(session(), value, j._NumericOrderIdReserved('entry-1', 7),
                         j._InvocationMayOccur('entry-1'))

    def test_risk_decision_must_bind_exact_order_payload(self):
        with self.assertRaises(ReadOnlyError):
            replace(consumed(), risk=risk(quantity='11'))

    def test_consumed_identifier_without_sanitizable_order_is_permanent(self):
        value = j._AttemptConsumed(SID, 'entry-1')
        result = replay(session(), value)
        self.assertIsNone(result.attempts[0].consumed.order)
        self.assertIn('entry-1', result.consumed_client_order_ids)
        self.invalid(session(), value, consumed())
        self.invalid(session(), value, j._NumericOrderIdReserved('entry-1', 7))

    def test_absent_order_context_is_not_explicit_denial_or_invocation(self):
        state = replay(session(), j._AttemptConsumed(SID, 'entry-1')).attempts[0]
        self.assertIsNone(state.closure_reason)
        self.assertFalse(state.invocation_may_have_occurred)

    def test_valid_generic_denials_can_be_captured_but_never_invoked(self):
        denied = (replace(order(), side='SELL'), replace(order(), requested_mode='LIVE'),
                  replace(order(), requested_intent='EXIT'),
                  replace(order(), order_type='LMT', limit_price=D(10)),
                  replace(order(), order_type='STP', stop_price=D(9)))
        for intent in denied:
            with self.subTest(intent=(intent.side, intent.order_type, intent.requested_mode, intent.requested_intent)):
                value = replace(consumed(), order=intent, risk=replace(risk(), order=intent))
                result = replay(session(), value, j._AttemptClosedWithoutInvocation('entry-1',
                                j._ClosureReason.LOCAL_GATE_DENIED))
                self.assertIn('entry-1', result.consumed_client_order_ids)
                self.invalid(session(), value, j._NumericOrderIdReserved('entry-1', 7),
                             j._InvocationMayOccur('entry-1'))


# Each generated case exercises a different field invariant or authoritative
# execution field. Names are normal unittest methods; no discovery overrides.
def model_case(factory, name, bad):
    def test(self):
        with self.assertRaises(ReadOnlyError) as caught:
            replace(factory(), **{name: bad})
        self.assertEqual(caught.exception.code, j._ERROR)
    return test


_INVALID_FIELDS = (
    ('header_schema', header, 'schema_version', 2),
    ('header_bool_schema', header, 'schema_version', True),
    ('header_format', header, 'format_id', 'unknown-format'),
    ('header_identity', header, 'journal_id', 'not-opaque'),
    ('header_binding_object', header, 'account_binding', object()),
    ('session_identity', session, 'session_id', 'old-session'),
    ('session_client_bool', session, 'client_id', True),
    ('session_client_zero', session, 'client_id', 0),
    ('session_client_high', session, 'client_id', MAX_ORDER_ID + 1),
    ('session_generation_zero', session, 'write_generation', 0),
    ('order_client_empty', order, 'client_order_id', ''),
    ('order_symbol_object', order, 'symbol', object()),
    ('order_side', order, 'side', 'OTHER'),
    ('order_type', order, 'order_type', 'STP'),
    ('order_tif', order, 'tif', 'GTC'),
    ('order_quantity_bool', order, 'quantity', True),
    ('order_quantity_zero', order, 'quantity', D(0)),
    ('order_quantity_nan', order, 'quantity', D('NaN')),
    ('order_quantity_infinite', order, 'quantity', D('Infinity')),
    ('order_quantity_exponent', order, 'quantity', D('1E65')),
    ('order_quantity_precision', order, 'quantity', D('1' * 65)),
    ('order_naive_time', order, 'created_at', AT.replace(tzinfo=None)),
    ('order_expiry', order, 'expires_at', AT),
    ('contract_conid_bool', contract, 'con_id', True),
    ('contract_conid_zero', contract, 'con_id', 0),
    ('contract_conid_high', contract, 'con_id', 1 << 64),
    ('contract_security_type', contract, 'security_type', 'OPT'),
    ('contract_route', contract, 'exchange', 'OTHER'),
    ('contract_verification_type', contract, 'verified', 1),
    ('contract_window', contract, 'valid_until', AT - timedelta(days=1)),
    ('strategy_version', strategy, 'strategy_version', 'v2.0'),
    ('strategy_setup', strategy, 'setup_id', 'bad setup'),
    ('strategy_date_type', strategy, 'session_date', AT),
    ('strategy_mutable_witnesses', strategy, 'witnesses', []),
    ('strategy_witness_count', strategy, 'witnesses', ()),
    ('strategy_minute_alignment', strategy, 'eligible_entry_minute', AT + timedelta(seconds=1)),
    ('strategy_stop_ordering', strategy, 'tick_adjusted_reference_stop', D(11)),
    ('strategy_target_ordering', strategy, 'tick_adjusted_reference_target', D(11)),
    ('strategy_risk_zero', strategy, 'risk_budget', D(0)),
    ('risk_permission_type', risk, 'permits_entry', 1),
    ('risk_window', risk, 'valid_until', AT - timedelta(minutes=1)),
    ('risk_negative_exposure', risk, 'current_exposure', D(-1)),
    ('execution_exec_id_empty', execution, 'exec_id', ''),
    ('execution_exec_id_long', execution, 'exec_id', 'x' * 101),
    ('execution_exec_id_control', execution, 'exec_id', 'x\n'),
    ('execution_shares_zero', execution, 'shares', D(0)),
    ('execution_price_negative', execution, 'price', D(-1)),
    ('execution_cumulative_own_shares', execution, 'cumulative_quantity', D(2)),
    ('execution_time_control', execution, 'execution_time', 'time\0'),
    ('execution_time_empty', execution, 'execution_time', ''),
    ('execution_source_object', execution, 'source', object()),
    ('execution_order_id_bool', execution, 'order_id', True),
    ('execution_client_id_bool', execution, 'client_id', True),
    ('execution_ref_object', execution, 'order_ref', object()),
    ('execution_ref_too_long', execution, 'order_ref', 'x' * 101),
    ('status_parent_high', status, 'parent_id', MAX_ORDER_ID + 1),
    ('status_lifecycle_string', status, 'status', 'Submitted'),
    ('status_negative_remaining', status, 'remaining', D(-1)),
    ('commission_raw_text', commission, 'report_id', 'raw broker message'),
    ('commission_nonfinite', commission, 'amount', D('NaN')),
)
for name, factory, field_name, bad in _INVALID_FIELDS:
    setattr(JournalTests, 'test_reject_' + name, model_case(factory, field_name, bad))


def conflict_case(field_name, value):
    def test(self):
        self.invalid(*prefix(), execution(), execution(4, **{field_name: value}))
    return test


for name, value in (('order_id', 8), ('client_id', 20), ('order_ref', 'foreign'),
    ('con_id', 1235), ('side', 'SLD'), ('shares', D(4)), ('price', D('10.126')),
    ('cumulative_quantity', D(4)), ('execution_time', '20260105 15:45:00 UTC')):
    setattr(JournalTests, 'test_same_exec_id_conflict_' + name, conflict_case(name, value))


def interruption_case(exception, construction=False):
    def test(self):
        if construction:
            class InterruptedTimezone(tzinfo):
                def utcoffset(self, dt): raise exception
            with self.assertRaises(type(exception)) as caught:
                replace(order(), created_at=AT.replace(tzinfo=InterruptedTimezone()))
        else:
            h, rs = header(), records(prefix())
            with patch.object(j, '_amount', side_effect=exception), self.assertRaises(type(exception)) as caught:
                j._replay_paper_execution_journal(h, rs)
        self.assertIs(caught.exception, exception)
        if isinstance(exception, SystemExit): self.assertEqual(caught.exception.code, 37)
    return test


for name, error in (('keyboard_interrupt', KeyboardInterrupt()), ('system_exit', SystemExit(37)),
                    ('generator_exit', GeneratorExit())):
    setattr(JournalTests, 'test_replay_' + name, interruption_case(error))
    setattr(JournalTests, 'test_constructor_' + name, interruption_case(error, construction=True))


if __name__ == '__main__':
    unittest.main()
