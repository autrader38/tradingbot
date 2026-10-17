"""PRE9 historical evidence under original issuers; anonymous SDKs, no network."""

import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from decimal import Decimal as D, Inexact, Rounded, localcontext
import inspect
from pathlib import Path
import pickle
import sqlite3
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tradingbot_broker import paper_decision_lifecycle_evidence as m
from tradingbot_broker import paper_attributed_execution_evidence as pre8
from tradingbot_broker import paper_decision_status_evidence as pre4
from tradingbot_broker import paper_dispatch_journal as dispatch
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker import paper_execution_accounting as accounting
from tradingbot_broker import paper_working_order as old_lifecycle
from tradingbot_broker import paper_recovery as recovery
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests.test_paper_attributed_execution_evidence import Fixture as PRE8Fixture
from tests import test_ibkr_paper_status_delivery as sdk
from tests.dispatch_journal_fixtures import AT


Owner = m._DecisionLifecycleEvidenceOwner
Result = m._DecisionLifecycleEvidence
Classification = m._DecisionLifecycleClassification
Consistency = m._DecisionLifecycleConsistency
Reason = m._DecisionLifecycleReason
Obligation = m._DecisionLifecycleObligation
PREFIX = '_DecisionLifecycleEvidenceOwner'


class Fixture(PRE8Fixture):
    def qualified(self, **changes):
        values = dict(sdk.RAW, filled='2', remaining='8')
        values.update(changes)
        if not getattr(self, 'legacy_status', False):
            return super().qualified(**values)
        # Original positive legacy decoder witness; never direct wrapper fallback.
        for name in ('filled', 'remaining'):
            values[name] = j._QUALIFIED_UNSET_QUANTITY if values[name] is None else D(values[name])
        normalized = tuple(values[name] for name in ('orderId', 'status', 'filled', 'remaining',
            'avgFillPrice', 'permId', 'parentId', 'lastFillPrice', 'clientId'))
        return self.client.decoder.interpret((37, *normalized))

    def setUp(self):
        super().setUp()
        self.root8 = self.incorporate()
        self.lifecycle_owner = m._create_decision_lifecycle_evidence_owner(self.incorporator)
        self.before = self.safety()

    def lifecycle(self): return self.lifecycle_owner._evaluate(self.root8)

    def lifecycle_case(self, **kwargs):
        self.root8 = self.execution_case(**kwargs)
        self.lifecycle_owner = m._create_decision_lifecycle_evidence_owner(self.incorporator)
        self.before = self.safety()
        return self.lifecycle()

    def invalid(self, function, *args, **kwargs):
        with self.assertRaises(ReadOnlyError) as caught: function(*args, **kwargs)
        self.assertEqual(caught.exception.code, m._ERROR)
        self.assertEqual(str(caught.exception), m._ERROR)


class SuccessTests(Fixture):
    def test_exact_pre8_pre4_observation_and_quantity_references(self):
        result = self.lifecycle(); selected = self.root8.pre7.pre6.pre5.status
        self.assertIs(result.pre8, self.root8)
        self.assertIs(result.decision_status, selected)
        self.assertIs(result.selected_observation, selected.observation)
        self.assertIs(result.requested_quantity, self.root8.pre7.attempt.consumed.order.quantity)
        self.assertIs(result.execution_quantity_lower_bound, self.root8.historical_execution_quantity_lower_bound)
        self.assertIs(result.status_reported_filled, selected.observation.filled)
        self.assertIs(result.status_reported_remaining, selected.observation.remaining)
        self.assertEqual(result.historical_possible_remaining_upper_bound, D(6))
        self.assertIs(result.classification, Classification.HISTORICAL_WORKING_REPORTED)
        self.assertIs(result.consistency, Consistency.COMPATIBLE)
        self.assertEqual(result.reasons, ())
        self.assertIs(self.lifecycle_owner._require_evidence(result), result)
        self.assertEqual(self.safety(), self.before)

    def test_no_selected_status_keeps_only_execution_derived_bound(self):
        result = self.lifecycle_case(statuses=())
        self.assertEqual(result.execution_quantity_lower_bound, D(4))
        self.assertEqual(result.historical_possible_remaining_upper_bound, D(6))
        self.assertIs(result.classification, Classification.NO_QUALIFIED_DECISION_STATUS)
        self.assertIs(result.consistency, Consistency.UNRESOLVED)
        self.assertIsNone(result.selected_observation)
        self.assertIsNone(result.status_reported_lifecycle)

    def test_execution_conflict_blocks_all_new_analysis(self):
        result = self.lifecycle_case(specs=([], [{'contract': {'conId': 31}}], []))
        self.assertIs(self.root8.outcome, pre8._AttributedExecutionOutcome.EXECUTION_EVIDENCE_CONFLICT)
        self.assertIs(result.consistency, Consistency.BLOCKED)
        for value in (result.requested_quantity, result.execution_quantity_lower_bound,
            result.historical_possible_remaining_upper_bound, result.selected_observation,
            result.status_reported_filled, result.status_reported_remaining): self.assertIsNone(value)

    def test_reported_filled_missing_quantities_remains_unresolved(self):
        result = self.lifecycle_case(statuses=({'status': 'Filled', 'filled': None, 'remaining': None},))
        self.assertIs(result.classification, Classification.HISTORICAL_FILLED_REPORTED)
        self.assertIs(result.consistency, Consistency.UNRESOLVED)
        self.assertIsNone(result.status_reported_filled); self.assertIsNone(result.status_reported_remaining)
        self.assertEqual(result.historical_possible_remaining_upper_bound, D(6))


    def test_zero_execution_lower_bound_is_not_zero_fill(self):
        result = self.lifecycle_case(specs=([], [], []), statuses=({'filled': '4', 'remaining': '6'},))
        self.assertIs(self.root8.outcome, pre8._AttributedExecutionOutcome.NO_POSITIVE_EXECUTION_OBSERVED)
        self.assertEqual(result.execution_quantity_lower_bound, D(0))
        self.assertEqual(result.historical_possible_remaining_upper_bound, D(10))
        self.assertEqual(result.status_reported_filled, D(4))
        self.assertIs(result.consistency, Consistency.COMPATIBLE)
        self.assertIn('ABSENCE_IS_NOT_NO_EXECUTION_PROOF', result.limitations)

    def test_no_status_even_full_execution_does_not_create_filled_lifecycle(self):
        result = self.lifecycle_case(statuses=(), specs=([], [{'values': {'shares': D(10)}}], []))
        self.assertEqual(result.historical_possible_remaining_upper_bound, D(0))
        self.assertIs(result.classification, Classification.NO_QUALIFIED_DECISION_STATUS)
        self.assertIsNone(result.status_reported_lifecycle)

    def test_no_execution_and_no_status_is_not_unfilled_or_working(self):
        result = self.lifecycle_case(statuses=(), specs=([{}], [], []))
        self.assertEqual(result.historical_possible_remaining_upper_bound, D(10))
        self.assertIs(result.classification, Classification.NO_QUALIFIED_DECISION_STATUS)
        self.assertIs(result.consistency, Consistency.UNRESOLVED)

    def test_earlier_terminal_status_does_not_create_transition_policy(self):
        result = self.lifecycle_case(statuses=({'status': 'Filled', 'filled': '10', 'remaining': '0'},
                                               {'status': 'Submitted', 'filled': '2', 'remaining': '8'}))
        self.assertIs(result.classification, Classification.HISTORICAL_WORKING_REPORTED)
        self.assertIs(result.consistency, Consistency.COMPATIBLE)

    def test_earlier_internal_conflict_does_not_replace_selected_report(self):
        result = self.lifecycle_case(statuses=({'filled': '7', 'remaining': '8'},
                                               {'filled': '2', 'remaining': '8'}))
        self.assertEqual(result.reasons, ())
        self.assertIs(result.consistency, Consistency.COMPATIBLE)

    def test_later_status_execution_and_boundary_cannot_enrich_result(self):
        result = self.lifecycle_case(durable=({},)); old_values = m._values(result)
        selected, observation = result.decision_status, result.selected_observation
        self.qualified(status='Filled', filled='10', remaining='0')
        self.emit_durable(execId='later-execution', shares=D(2))
        self.certify(AT + timedelta(seconds=2))
        before = self.safety()
        self.assertIs(self.lifecycle_owner._require_evidence(result), result)
        self.assertEqual(m._values(result), old_values)
        self.assertIs(result.decision_status, selected); self.assertIs(result.selected_observation, observation)
        self.assertEqual(result.execution_quantity_lower_bound, D(4))
        self.assertEqual(result.status_reported_filled, D(2))
        self.assertEqual(result.historical_possible_remaining_upper_bound, D(6))
        self.assertEqual(self.safety(), before)

    def test_low_precision_and_traps_do_not_change_derived_bound(self):
        result = self.lifecycle_case(specs=([], [{'values': {'shares': D('0.1')}}], []),
                                     statuses=({'filled': '0.2', 'remaining': '9.8'},))
        with localcontext() as context:
            context.prec = 1; context.traps[Inexact] = context.traps[Rounded] = True
            repeated = self.lifecycle()
            self.assertEqual(repeated, result)
            self.assertEqual(repeated.historical_possible_remaining_upper_bound, D('9.9'))
            self.assertIs(self.lifecycle_owner._require_evidence(repeated), repeated)

    def test_hostile_context_cannot_hide_status_sum_mismatch(self):
        self.lifecycle_case(statuses=({'filled': '10', 'remaining': '1E-64'},))
        with localcontext() as context:
            context.prec = 1; context.traps[Inexact] = context.traps[Rounded] = True
            result = self.lifecycle()
        self.assertIs(result.consistency, Consistency.CONFLICTING)
        self.assertEqual(result.reasons, (Reason.STATUS_QUANTITY_SUM_MISMATCH,))

    def test_repeat_is_deterministic_and_supersedes_only_local_publication(self):
        first = self.lifecycle(); second = self.lifecycle()
        self.assertEqual(first, second); self.assertIsNot(first, second)
        self.invalid(self.lifecycle_owner._require_evidence, first)
        self.assertIs(self.lifecycle_owner._require_evidence(second), second)

    def test_exact_original_pre8_validator_before_and_after_analysis(self):
        original = type(self.incorporator)._require_evidence; calls = []
        def checked(owner, result):
            calls.append((owner, result)); return original(owner, result)
        self.change(self.transport, '_decision_lifecycle_evidence_owner', None)
        with patch.object(type(self.incorporator), '_require_evidence', checked):
            owner = m._create_decision_lifecycle_evidence_owner(self.incorporator)
            result = owner._evaluate(self.root8)
            self.assertIs(owner._require_evidence(result), result)
        self.assertGreaterEqual(len(calls), 4)
        self.assertTrue(all(owner is self.incorporator and root is self.root8 for owner, root in calls))


class PresenceTests(Fixture):
    def test_legacy_zero_quantities_are_known_decoded_not_ambiguous(self):
        self.legacy_status = True
        result = self.lifecycle_case(statuses=({'filled': '0.00', 'remaining': '10.00', 'avgFillPrice': 0.0},))
        observation = result.selected_observation
        self.assertIs(observation.protocol, j._QualifiedStatusProtocol.LEGACY)
        self.assertIs(observation.presence.filled, j._QualifiedStatusSourceQuality.LEGACY_DECODED)
        self.assertIs(observation.presence.remaining, j._QualifiedStatusSourceQuality.LEGACY_DECODED)
        self.assertIs(observation.presence.average_fill_price, j._QualifiedStatusSourceQuality.LEGACY_AMBIGUOUS)
        self.assertEqual(result.status_reported_filled.as_tuple(), D('0.00').as_tuple())
        self.assertIs(result.consistency, Consistency.COMPATIBLE)

    def test_legacy_unavailable_quantity_stays_unavailable(self):
        self.legacy_status = True
        result = self.lifecycle_case(statuses=({'filled': None},))
        self.assertIsNone(result.status_reported_filled)
        self.assertIs(result.selected_observation.presence.filled, j._QualifiedStatusSourceQuality.UNAVAILABLE)
        self.assertIs(result.consistency, Consistency.UNRESOLVED)

    def test_supplied_unavailable_quantity_preserves_wire_presence(self):
        result = self.lifecycle_case(statuses=({'filled': str(j._QUALIFIED_UNSET_QUANTITY)},))
        self.assertIsNone(result.status_reported_filled)
        self.assertIn('filled', result.selected_observation.presence.wire_supplied)
        self.assertIs(result.consistency, Consistency.UNRESOLVED)

    def test_omitted_quantity_does_not_become_decoded_zero(self):
        result = self.lifecycle_case(statuses=({'filled': None, 'remaining': None},))
        self.assertIsNone(result.status_reported_filled); self.assertIsNone(result.status_reported_remaining)
        self.assertNotIn('filled', result.selected_observation.presence.wire_supplied)
        self.assertIs(result.classification, Classification.HISTORICAL_WORKING_REPORTED)
        self.assertIs(result.consistency, Consistency.UNRESOLVED)

    def test_unavailable_status_is_distinct_from_known_unknown_status(self):
        result = self.lifecycle_case(statuses=({'status': None},))
        self.assertIsNone(result.status_reported_lifecycle)
        self.assertIn(Obligation.STATUS_FIELD_UNAVAILABLE, result.obligations)
        self.assertNotIn(Obligation.STATUS_UNKNOWN, result.obligations)

    def test_unknown_status_is_retained_without_working_or_terminal_meaning(self):
        result = self.lifecycle_case(statuses=({'status': 'UNKNOWN'},))
        self.assertIs(result.status_reported_lifecycle, j._LifecycleStatus.UNKNOWN)
        self.assertIn(Obligation.STATUS_UNKNOWN, result.obligations)
        self.assertNotIn(Obligation.STATUS_FIELD_UNAVAILABLE, result.obligations)
        self.assertIs(result.classification, Classification.HISTORICAL_LIFECYCLE_UNRESOLVED)

    def test_invalid_ambiguous_quantity_quality_is_rejected_by_original_model(self):
        observation = self.root8.pre7.pre6.pre5.status.observation
        presence = replace(observation.presence, filled=j._QualifiedStatusSourceQuality.LEGACY_AMBIGUOUS)
        with self.assertRaises(ReadOnlyError): replace(observation, presence=presence)


class IssuerTests(Fixture):
    def test_factory_accepts_only_original_registered_pre8_owner(self):
        self.invalid(Owner)
        for value in (None, {}, object(), self.root8, self.root7, self.attributor, self.journal):
            self.invalid(m._create_decision_lifecycle_evidence_owner, value)
        self.invalid(m._create_decision_lifecycle_evidence_owner, self.incorporator)
        clone = object.__new__(type(self.incorporator))
        for slot in type(clone).__slots__:
            name = '_AttributedExecutionEvidenceOwner' + slot
            object.__setattr__(clone, name, getattr(self.incorporator, name))
        self.invalid(m._create_decision_lifecycle_evidence_owner, clone)
        self.assertEqual(list(inspect.signature(Owner._evaluate).parameters), ['self', 'pre8_result'])

    def test_reconstructed_and_superseded_pre8_results_rejected(self):
        self.invalid(self.lifecycle_owner._evaluate, replace(self.root8))
        result = self.lifecycle(); newer = self.incorporate()
        self.assertEqual(newer, self.root8)
        self.invalid(self.lifecycle_owner._require_evidence, result)
        self.invalid(self.lifecycle_owner._evaluate, newer)

    def test_reconstructed_and_pickled_pre9_result_rejected(self):
        result = self.lifecycle()
        for value in (replace(result), pickle.loads(pickle.dumps(result))):
            self.assertEqual(value, result)
            self.invalid(self.lifecycle_owner._require_evidence, value)
        self.assertIs(self.lifecycle_owner._require_evidence(result), result)

    def test_other_live_runtime_cannot_substitute_issuer_or_result(self):
        result, owner, root = self.lifecycle(), self.lifecycle_owner, self.root8
        other = self.lifecycle_case()
        self.invalid(owner._evaluate, self.root8)
        self.invalid(owner._require_evidence, other)
        self.invalid(self.lifecycle_owner._evaluate, root)
        self.invalid(self.lifecycle_owner._require_evidence, result)

    def test_frozen_slotted_opaque_memory_only_result(self):
        result = self.lifecycle()
        self.assertFalse(hasattr(result, '__dict__')); self.assertFalse(hasattr(self.lifecycle_owner, '__dict__'))
        with self.assertRaises(FrozenInstanceError): result.consistency = Consistency.COMPATIBLE
        with self.assertRaises(ReadOnlyError): self.lifecycle_owner.authorized = True
        with self.assertRaises(ReadOnlyError): del self.lifecycle_owner._result
        self.assertEqual(repr(result), '<PrivateDecisionLifecycleEvidence historical-only>')
        self.assertEqual({f.name for f in fields(Result)}, {'pre8', 'historical_possible_remaining_upper_bound',
            'status_reported_lifecycle', 'status_reported_filled', 'status_reported_remaining',
            'classification', 'consistency', 'reasons', 'obligations', 'limitations'})

    def test_wrong_thread_before_original_issuer_io(self):
        result = self.lifecycle()
        with ThreadPoolExecutor(max_workers=1) as pool:
            for function, args in ((self.lifecycle_owner._evaluate, (self.root8,)),
                (self.lifecycle_owner._require_evidence, (result,)),
                (m._create_decision_lifecycle_evidence_owner, (self.incorporator,))):
                with self.assertRaises(ReadOnlyError) as caught: pool.submit(function, *args).result(timeout=5)
                self.assertEqual(caught.exception.code, m._ERROR)
        self.assertIs(self.lifecycle_owner._require_evidence(result), result)

    def test_replaced_original_pre8_validator_is_rejected(self):
        with patch.object(type(self.incorporator), '_require_evidence', side_effect=AssertionError('replacement')):
            self.invalid(self.lifecycle)

    def test_corrupt_current_storage_still_fails_inherited_integrity_check(self):
        result = self.lifecycle(); self.corrupt_digest(self.boundary.committed.record.sequence)
        self.invalid(self.lifecycle_owner._require_evidence, result)
        self.assertIsNone(self.lifecycle_owner._result)


class FailureTests(Fixture):
    def test_ordinary_failure_sanitized_and_no_partial_publication(self):
        with patch.object(m, '_derive', side_effect=ValueError('private failure')): self.invalid(self.lifecycle)
        self.assertIsNone(self.lifecycle_owner._result)
        self.assertIs(self.lifecycle_owner._require_evidence(self.lifecycle()), self.lifecycle_owner._result)

    def test_reentrant_evaluation_cannot_publish_partial_result(self):
        with patch.object(Owner, '_publish', side_effect=lambda result: self.lifecycle()): self.invalid(self.lifecycle)
        self.assertIsNone(self.lifecycle_owner._result)

    def test_equal_substitution_during_publication_fails(self):
        with patch.object(Owner, '_publish', side_effect=lambda result: replace(result)): self.invalid(self.lifecycle)
        self.assertIsNone(self.lifecycle_owner._result)


class NoAuthorityTests(Fixture):
    def test_only_inherited_revalidation_io_no_writes_operations_or_clocks(self):
        connection = self.journal._store._connection
        writes = {getattr(sqlite3, name) for name in ('SQLITE_INSERT', 'SQLITE_UPDATE', 'SQLITE_DELETE',
            'SQLITE_CREATE_TABLE', 'SQLITE_DROP_TABLE', 'SQLITE_CREATE_TRIGGER', 'SQLITE_DROP_TRIGGER',
            'SQLITE_ALTER_TABLE', 'SQLITE_CREATE_INDEX', 'SQLITE_DROP_INDEX')}
        seen = []
        def authorize(action, *args):
            if action in writes: seen.append(action); return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        connection.set_authorizer(authorize)
        try:
            with ExitStack() as stack:
                for cls in (self.api.Client, self.paper_api.Client):
                    for name in ('reqAllOpenOrders', 'reqExecutions', 'reqCompletedOrders', 'placeOrder', 'cancelOrder'):
                        if hasattr(cls, name): stack.enter_context(patch.object(cls, name, side_effect=AssertionError(name)))
                for source in self.sources:
                    for name in ('_collect', '_authenticate'):
                        stack.enter_context(patch.object(type(source), name, side_effect=AssertionError(name)))
                for cls, names in ((type(self.preparation), ('_prepare',)),
                    (type(self.journal), ('_certify_decision_boundary', '_append_event', '_drain_callbacks')),
                    (type(self.journal._store), ('_append',))):
                    for name in names: stack.enter_context(patch.object(cls, name, side_effect=AssertionError(name)))
                for module in (accounting, old_lifecycle, recovery):
                    for name in dir(module):
                        value = getattr(module, name)
                        if inspect.isfunction(value) and value.__module__ == module.__name__:
                            stack.enter_context(patch.object(module, name, side_effect=AssertionError(name)))
                for name in ('time.time', 'time.monotonic'):
                    stack.enter_context(patch(name, side_effect=AssertionError('clock')))
                stack.enter_context(patch.object(dispatch, '_decision_utc_now', side_effect=AssertionError('clock')))
                result = self.lifecycle()
                self.assertIs(self.lifecycle_owner._require_evidence(result), result)
        finally: connection.set_authorizer(None)
        self.assertEqual(seen, []); self.assertEqual(self.safety(), self.before)

    def test_no_receipt_reconsumption_or_weaker_identity_guard(self):
        with patch.object(type(self.sources[0]), '_consume_receipt', side_effect=AssertionError('consume')):
            self.invalid(self.lifecycle)
        self.assertEqual(self.consumed(), (True, True, True))

    def test_no_public_export_codec_or_management_fields(self):
        result = self.lifecycle()
        self.assertEqual(m.__all__, ())
        self.assertEqual([name for name in dir(Owner) if not name.startswith('_')], [])
        self.assertNotIn(Result, storage._MODELS)
        with self.assertRaises(ReadOnlyError): storage._encode(result)
        for name in ('cancel_allowed', 'replace_allowed', 'exit_allowed', 'protectable_quantity',
            'management_authority', 'recovery_authority', 'trading_authority', 'new_entry_permitted',
            'current_remainder', 'current_position', 'inventory'): self.assertFalse(hasattr(result, name))
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.transport._snapshot.account.mode)
        self.assertFalse(self.broker.controls.trading_enabled)
        self.assertTrue(result.decision_status.prefix.replay.restart_quarantine_required)
        self.assertEqual(self.safety(), self.before)

    def test_global_obligations_survive_even_filled_full_quantity_report(self):
        result = self.lifecycle_case(statuses=({'status': 'Filled', 'filled': '10', 'remaining': '0'},),
                                     specs=([], [{'values': {'shares': D(10)}}], []))
        self.assertTrue(set(m._BASE_OBLIGATIONS) <= set(result.obligations))
        self.assertEqual(result.limitations, m._LIMITATIONS)
        self.assertEqual(len(result.limitations), 18)
        self.assertIn('NOT_RESTART_ADOPTABLE', result.limitations)

    def test_ast_forbids_other_sources_old_owners_clocks_and_new_io(self):
        tree = ast.parse(Path(m.__file__).read_text())
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertTrue({'open_orders', 'completed_orders', 'executions', 'candidates', 'facts',
            'commissions', 'average_fill_price', 'last_fill_price', 'price', 'notional', 'symbol',
            'order_ref', 'perm_id', 'execution_time', 'source', 'sequence', 'placing_client_id'}.isdisjoint(attrs))
        calls = {n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id if isinstance(n.func, ast.Name) else ''
                 for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertTrue({'open', 'connect', 'execute', '_load', '_append', '_append_event', '_collect',
            '_consume_receipt', '_prepare', '_authenticate', '_certify_decision_boundary', '_drain_callbacks',
            '_evaluate_decision_status', '_select', '_incorporate', '_sum', 'now', 'time', 'monotonic',
            'reqAllOpenOrders', 'reqExecutions', 'reqCompletedOrders', 'placeOrder', 'cancelOrder',
            'multiply', 'exact_ratio', 'max', 'min', 'sorted'}.isdisjoint(calls))
        imports = {alias.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for alias in n.names}
        self.assertTrue({'paper_execution_accounting', 'paper_working_order', 'paper_recovery'}.isdisjoint(imports))
        arithmetic = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Name) and n.func.id in ('add', 'subtract')]
        self.assertEqual(arithmetic, ['subtract(quantity, executions)', 'add(filled, remaining)'])
        for comparison in (n for n in ast.walk(tree) if isinstance(n, ast.Compare)):
            names = {n.id for n in ast.walk(comparison) if isinstance(n, ast.Name)}
            self.assertFalse('executions' in names and bool(names & {'filled', 'remaining'}))
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, ast.BinOp)
                          and not isinstance(n.op, ast.BitOr)], [])


def _status_case(value, expected):
    def test(self):
        quantities = {'filled': '10', 'remaining': '0'} if value == 'Filled' else {'filled': '2', 'remaining': '8'}
        result = self.lifecycle_case(statuses=({'status': value, **quantities},))
        self.assertIs(result.classification, expected)
        self.assertIs(result.status_reported_lifecycle, j._LifecycleStatus(value))
        self.assertIs(result.consistency, Consistency.UNRESOLVED if value == 'UNKNOWN' else Consistency.COMPATIBLE)
        self.assertEqual(result.reasons, ())
    return test


for _value, _classification in (
    ('PendingSubmit', Classification.HISTORICAL_SUBMISSION_REPORTED),
    ('PreSubmitted', Classification.HISTORICAL_WORKING_REPORTED),
    ('Submitted', Classification.HISTORICAL_WORKING_REPORTED),
    ('Filled', Classification.HISTORICAL_FILLED_REPORTED),
    ('Cancelled', Classification.HISTORICAL_TERMINAL_REPORTED),
    ('ApiCancelled', Classification.HISTORICAL_TERMINAL_REPORTED),
    ('Inactive', Classification.HISTORICAL_TERMINAL_REPORTED),
    ('Expired', Classification.HISTORICAL_TERMINAL_REPORTED),
    ('UNKNOWN', Classification.HISTORICAL_LIFECYCLE_UNRESOLVED)):
    setattr(SuccessTests, 'test_status_' + _value, _status_case(_value, _classification))


def _quantity_case(filled, remaining, reported, reasons):
    def test(self):
        result = self.lifecycle_case(statuses=({'status': reported, 'filled': filled, 'remaining': remaining},))
        self.assertEqual(result.reasons, tuple(r for r in Reason if r in reasons))
        self.assertEqual(result.status_reported_filled, None if filled is None else D(filled))
        self.assertEqual(result.status_reported_remaining, None if remaining is None else D(remaining))
        if reasons:
            self.assertIs(result.classification, Classification.HISTORICAL_LIFECYCLE_CONFLICT)
            self.assertIs(result.consistency, Consistency.CONFLICTING)
        else:
            self.assertIs(result.consistency, Consistency.UNRESOLVED if filled is None or remaining is None
                          else Consistency.COMPATIBLE)
        # Status-local contradictions never overwrite the independent PRE8 facts.
        self.assertEqual(result.execution_quantity_lower_bound, D(4))
        self.assertEqual(result.historical_possible_remaining_upper_bound, D(6))
    return test


for _label, _f, _r, _s, _reasons in (
    ('zero_ten', '0', '10', 'Submitted', ()), ('four_six', '4', '6', 'Submitted', ()),
    ('ten_zero', '10', '0', 'Submitted', ()),
    ('filled_exceeds', '11', '0', 'Submitted', (Reason.STATUS_FILLED_EXCEEDS_REQUESTED, Reason.STATUS_QUANTITY_SUM_MISMATCH)),
    ('remaining_exceeds', '0', '11', 'Submitted', (Reason.STATUS_REMAINING_EXCEEDS_REQUESTED, Reason.STATUS_QUANTITY_SUM_MISMATCH)),
    ('sum_mismatch', '4', '7', 'Submitted', (Reason.STATUS_QUANTITY_SUM_MISMATCH,)),
    ('filled_valid', '10', '0', 'Filled', ()),
    ('filled_invalid', '9', '1', 'Filled', (Reason.FILLED_STATUS_FILLED_QUANTITY_MISMATCH, Reason.FILLED_STATUS_REMAINING_QUANTITY_MISMATCH)),
    ('filled_only', '10', None, 'Filled', ()), ('remaining_only', None, '0', 'Filled', ()),
    ('filled_neither', None, None, 'Filled', ()),
    ('partial_filled_wrong', '9', None, 'Filled', (Reason.FILLED_STATUS_FILLED_QUANTITY_MISMATCH,)),
    ('partial_remaining_wrong', None, '1', 'Filled', (Reason.FILLED_STATUS_REMAINING_QUANTITY_MISMATCH,)),
    ('seven_eight', '7', '8', 'Submitted', (Reason.STATUS_QUANTITY_SUM_MISMATCH,)),
    ('unknown_status_conflict', '11', '0', 'UNKNOWN', (Reason.STATUS_FILLED_EXCEEDS_REQUESTED, Reason.STATUS_QUANTITY_SUM_MISMATCH))):
    setattr(SuccessTests, 'test_quantity_' + _label, _quantity_case(_f, _r, _s, _reasons))


def _cross_domain(status_value, filled, remaining, executions):
    def test(self):
        result = self.lifecycle_case(statuses=({'status': status_value, 'filled': filled, 'remaining': remaining},),
            specs=([], [] if executions == '0' else [{'values': {'shares': D(executions)}}], []))
        self.assertIs(result.consistency, Consistency.COMPATIBLE)
        self.assertEqual(result.reasons, ())
        self.assertEqual(result.execution_quantity_lower_bound, D(executions))
        self.assertEqual(result.status_reported_filled, D(filled))
        self.assertEqual(result.status_reported_remaining, D(remaining))
        self.assertIn(Obligation.CROSS_DOMAIN_EVENT_CHRONOLOGY_UNKNOWN, result.obligations)
    return test


for _label, _s, _f, _r, _e in (
    ('working_exec_above_status', 'Submitted', '2', '8', '4'),
    ('working_full_exec', 'Submitted', '8', '2', '10'),
    ('filled_incomplete_exec', 'Filled', '10', '0', '4'),
    ('cancelled_with_exec', 'Cancelled', '0', '10', '4'),
    ('api_cancelled_with_exec', 'ApiCancelled', '0', '10', '4'),
    ('inactive_with_exec', 'Inactive', '0', '10', '4'),
    ('expired_with_exec', 'Expired', '0', '10', '4'),
    ('pending_with_exec', 'PendingSubmit', '0', '10', '4'),
    ('filled_no_exec', 'Filled', '10', '0', '0')):
    setattr(SuccessTests, 'test_independent_' + _label, _cross_domain(_s, _f, _r, _e))


def _missing_fields(status_known, filled_known, remaining_known):
    def test(self):
        result = self.lifecycle_case(statuses=({'status': 'Submitted' if status_known else None,
            'filled': '2' if filled_known else None, 'remaining': '8' if remaining_known else None},))
        self.assertEqual(result.status_reported_filled, D(2) if filled_known else None)
        self.assertEqual(result.status_reported_remaining, D(8) if remaining_known else None)
        self.assertIs(result.consistency, Consistency.COMPATIBLE if all((status_known, filled_known, remaining_known))
                      else Consistency.UNRESOLVED)
        self.assertEqual(Obligation.STATUS_FIELD_UNAVAILABLE in result.obligations, not status_known)
        self.assertEqual(Obligation.STATUS_FILLED_UNAVAILABLE in result.obligations, not filled_known)
        self.assertEqual(Obligation.STATUS_REMAINING_UNAVAILABLE in result.obligations, not remaining_known)
        self.assertEqual(result.historical_possible_remaining_upper_bound, D(6))
    return test


for _s in (False, True):
    for _f in (False, True):
        for _r in (False, True):
            setattr(PresenceTests, f'test_presence_{int(_s)}{int(_f)}{int(_r)}', _missing_fields(_s, _f, _r))


def _zero_shape(text, field):
    def test(self):
        values = {'filled': text, 'remaining': '10'} if field == 'filled' else {'filled': '10', 'remaining': text}
        result = self.lifecycle_case(statuses=(values,))
        self.assertEqual(getattr(result, 'status_reported_' + field).as_tuple(), D(text).as_tuple())
        self.assertIs(result.consistency, Consistency.COMPATIBLE)
    return test


for _text in ('0', '0.0', '0.00', '-0.00'):
    for _field in ('filled', 'remaining'):
        setattr(PresenceTests, 'test_shape_' + _field + '_' + _text.replace('.', '_').replace('-', 'negative'),
                _zero_shape(_text, _field))


def _upstream_blocked(**kwargs):
    def test(self):
        result = self.lifecycle_case(**kwargs)
        self.assertIs(result.classification, Classification.BLOCKED)
        self.assertIs(result.consistency, Consistency.BLOCKED)
        self.assertIsNone(result.selected_observation)
        self.assertIsNone(result.execution_quantity_lower_bound)
        self.assertIsNone(result.historical_possible_remaining_upper_bound)
        self.assertEqual(result.reasons, ())
    return test


for _label, _kwargs in (
    ('no_reservation', {'attempts': ()}), ('no_support', {'specs': ([], [], []), 'statuses': ()}),
    ('not_invoked', {'attempts': (('entry-1', 10, 'reserved', False),)}),
    ('status_identity_ambiguous', {'statuses': ({'permId': 77}, {'permId': 88})}),
    ('exec_overflow', {'specs': ([], [{'values': {'shares': D(11)}}], [])})):
    setattr(SuccessTests, 'test_blocked_' + _label, _upstream_blocked(**_kwargs))


def _tamper(field):
    def test(self):
        result = self.lifecycle()
        values = {'pre8': replace(result.pre8), 'historical_possible_remaining_upper_bound': D(7),
            'status_reported_lifecycle': j._LifecycleStatus.FILLED, 'status_reported_filled': D('2.0'),
            'status_reported_remaining': D(9), 'classification': Classification.HISTORICAL_FILLED_REPORTED,
            'consistency': Consistency.UNRESOLVED, 'reasons': (Reason.STATUS_QUANTITY_SUM_MISMATCH,),
            'obligations': tuple(list(result.obligations)), 'limitations': ('substituted',)}
        self.change(result, field, values[field])
        self.invalid(self.lifecycle_owner._require_evidence, result)
    return test


for _field in (f.name for f in fields(Result)):
    setattr(IssuerTests, 'test_result_tamper_' + _field, _tamper(_field))


def _source_tamper(field):
    def test(self):
        result = self.lifecycle(); selected = result.decision_status; observation = selected.observation
        obj, name, value = {
            'pre4': (self.root8.pre7.pre6.pre5, 'status', replace(selected)),
            'observation': (selected.selected_record.payload, 'observation', replace(observation)),
            'filled': (observation, 'filled', D('2.0')), 'remaining': (observation, 'remaining', D(7)),
            'status': (observation, 'status', 'Cancelled'),
            'presence': (observation, 'presence', replace(observation.presence)),
            'prefix': (selected, 'prefix', replace(selected.prefix)),
            'executions': (self.root8, 'historical_execution_quantity_lower_bound', D(5)),
            'requested': (self.root8.pre7.attempt.consumed.order, 'quantity', D(11)),
            'read_generation': (self.transport, '_generation', self.transport._generation + 1),
            'write_generation': (self.write, '_generation', self.write._generation + 1),
            'read_client': (self.transport, '_client', object()),
            'disconnect': (self.transport, '_disconnect_emitted', True),
            'pre8_registration': (self.transport, '_attributed_execution_evidence_owner', object()),
            'pre9_registration': (self.transport, '_decision_lifecycle_evidence_owner', object()),
        }[field]
        self.change(obj, name, value)
        self.invalid(self.lifecycle_owner._require_evidence, result)
    return test


for _field in ('pre4', 'observation', 'filled', 'remaining', 'status', 'presence', 'prefix', 'executions',
               'requested', 'read_generation', 'write_generation', 'read_client', 'disconnect',
               'pre8_registration', 'pre9_registration'):
    setattr(IssuerTests, 'test_source_tamper_' + _field, _source_tamper(_field))


def _interruption(kind, seam, validating=False):
    def test(self):
        result = self.lifecycle(); error = kind(91) if kind is SystemExit else kind('interrupted')
        target = m if seam in ('_derive', '_analyze', '_values') else Owner
        with patch.object(target, seam, side_effect=error):
            with self.assertRaises(kind) as caught:
                if validating: self.lifecycle_owner._require_evidence(result)
                else: self.lifecycle()
        self.assertIs(caught.exception, error)
        if kind is SystemExit: self.assertEqual(caught.exception.code, 91)
        self.assertIsNone(self.lifecycle_owner._result)
        self.assertFalse(getattr(self.lifecycle_owner, PREFIX + '__evaluating'))
        self.assertEqual(self.safety(), self.before)
    return test


for _kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    for _seam in ('_derive', '_analyze', '_values', '_publish', '_validate_root', '_set'):
        setattr(FailureTests, f'test_{_kind.__name__}_{_seam}', _interruption(_kind, _seam))
    setattr(FailureTests, f'test_{_kind.__name__}_revalidation', _interruption(_kind, '_validate_root', True))


def _after_assignment(kind):
    def test(self):
        original = Owner._set; error = kind(91) if kind is SystemExit else kind('after publication')
        def assign(owner, name, value):
            original(owner, name, value)
            if name == 'result': raise error
        with patch.object(Owner, '_set', assign):
            with self.assertRaises(kind) as caught: self.lifecycle()
        self.assertIs(caught.exception, error); self.assertIsNone(self.lifecycle_owner._result)
        self.assertFalse(getattr(self.lifecycle_owner, PREFIX + '__evaluating'))
    return test


for _kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    setattr(FailureTests, 'test_after_final_assignment_' + _kind.__name__, _after_assignment(_kind))


class ClosedContractTests(unittest.TestCase):
    def test_blocked_branch_does_not_access_status_or_requested_quantity(self):
        for outcome in (pre8._AttributedExecutionOutcome.ATTRIBUTION_BLOCKED,
                        pre8._AttributedExecutionOutcome.EXECUTION_EVIDENCE_CONFLICT):
            values = m._analyze(NS(outcome=outcome))  # Defensive syntax, never a runtime authority.
            self.assertIs(values[4], Classification.BLOCKED)
            self.assertEqual(values[:4], (None, None, None, None))

    def test_defensive_ambiguous_status_cannot_be_repaired(self):
        root = NS(outcome=pre8._AttributedExecutionOutcome.EXECUTION_EVIDENCE_INCORPORATED,
            pre7=NS(pre6=NS(pre5=NS(status=NS(outcome=pre4._DecisionStatusOutcome.AMBIGUOUS_IDENTITY)))))
        values = m._analyze(root)  # Valid issuers ordinarily block this earlier.
        self.assertIs(values[5], Consistency.BLOCKED)
        self.assertIn(Obligation.AMBIGUOUS_DECISION_STATUS, values[7])

    def test_ambiguous_and_unavailable_quality_cannot_supply_values(self):
        for quality in j._QualifiedStatusSourceQuality:
            observation = NS(filled=D(0), presence=NS(filled=quality))
            value = m._known(observation, 'filled')
            self.assertEqual(value, D(0) if quality in (j._QualifiedStatusSourceQuality.SUPPLIED,
                             j._QualifiedStatusSourceQuality.LEGACY_DECODED) else None)


if __name__ == '__main__': unittest.main()
