"""PRE8 exact attributed execution evidence; anonymous SDKs, network forbidden."""

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

from tradingbot_broker import paper_attributed_execution_evidence as m
from tradingbot_broker import paper_attempt_attribution_evidence as pre7
from tradingbot_broker import paper_cross_source_identity_evidence as pre6
from tradingbot_broker import paper_dispatch_journal as dispatch
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker import paper_execution_accounting as accounting
from tradingbot_broker import paper_working_order as lifecycle
from tradingbot_broker import paper_recovery as recovery
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests.test_paper_attempt_attribution_evidence import Fixture as PRE7Fixture
from tests import test_paper_execution_journal as fixtures
from tests.dispatch_journal_fixtures import AT


Owner = m._AttributedExecutionEvidenceOwner
Result = m._AttributedExecutionEvidence
Fact = m._AttributedExecutionFact
Outcome = m._AttributedExecutionOutcome
Reason = m._ExecutionConflictReason
PREFIX = '_AttributedExecutionEvidenceOwner'


class Fixture(PRE7Fixture):
    def seed_attempt(self, *args, **kwargs):
        original = fixtures.consumed
        def consumed(*values, **named):
            event = original(*values, **named)
            return replace(event, contract=replace(event.contract, con_id=17))
        with patch.object(fixtures, 'consumed', side_effect=consumed):
            return super().seed_attempt(*args, **kwargs)

    def build(self, **kwargs):
        # Durable callback tests use the original dispatch commitment against an
        # anonymous SDK, preserving C3C4D's live invocation-boundary requirement.
        original = getattr(self, 'real_fake_dispatch', False)
        self.real_fake_dispatch = original or bool(getattr(self, 'durable_specs', ()))
        try:
            result = super().build(**kwargs)
        finally:
            self.real_fake_dispatch = original
        for spec in getattr(self, 'durable_specs', ()):
            self.emit_durable(**spec)
        return result

    def emit_durable(self, **changes):
        # Original capture and durable producer, anonymous fake client only.
        values = dict(orderId=10, clientId=2, orderRef=self.order.client_order_id, execId='exec-1',
                      side='BOT', shares=D('4'), price=D('1.25'))
        values.update(changes)
        self.client.wrapper.execDetails(0, NS(conId=17, secType='STK', exchange='SMART'), NS(**values))
        result = self.coordinator._sync_callback_persistence()
        self.assertIs(result.state, dispatch._CallbackPersistenceState.CURRENT)

    def setUp(self):
        super().setUp()
        self.root7 = self.attribute()
        self.incorporator = m._create_attributed_execution_evidence_owner(self.attributor)
        self.before = self.safety()

    def incorporate(self): return self.incorporator._evaluate(self.root7)

    def execution_case(self, *, durable=(), **kwargs):
        self.durable_specs = durable
        self.root7 = self.case(**kwargs)
        self.incorporator = m._create_attributed_execution_evidence_owner(self.attributor)
        self.before = self.safety()
        return self.incorporate()

    def invalid(self, function, *args, **kwargs):
        with self.assertRaises(ReadOnlyError) as caught: function(*args, **kwargs)
        self.assertEqual(caught.exception.code, m._ERROR)
        self.assertEqual(str(caught.exception), m._ERROR)


class SuccessTests(Fixture):
    def test_fresh_only_exact_root_and_occurrences(self):
        result = self.incorporate()
        self.assertIs(result.pre7, self.root7)
        self.assertIs(result.attempt, self.root7.attempt)
        self.assertIs(result.durable_executions, self.root7.attempt.executions)
        self.assertIs(result.fresh_executions, self.root7.pre6.executions.candidates)
        self.assertIs(result.facts[0].fresh_occurrences[0], result.fresh_executions[0])
        self.assertEqual(result.historical_execution_quantity_lower_bound, D(4))
        self.assertIs(result.outcome, Outcome.EXECUTION_EVIDENCE_INCORPORATED)
        self.assertIs(self.incorporator._require_evidence(result), result)
        self.assertEqual(self.safety(), self.before)

    def test_durable_only(self):
        result = self.execution_case(durable=({},), specs=([], [], []))
        self.assertEqual(result.historical_execution_quantity_lower_bound, D(4))
        self.assertEqual(result.fresh_executions, ())
        self.assertIs(result.facts[0].durable_occurrences[0], result.durable_executions[0])

    def test_same_execution_in_both_domains_counted_once(self):
        result = self.execution_case(durable=({},))
        self.assertEqual(len(result.facts), 1)
        self.assertEqual(result.historical_execution_quantity_lower_bound, D(4))
        self.assertIs(result.facts[0].durable_occurrences[0], result.durable_executions[0])
        self.assertIs(result.facts[0].fresh_occurrences[0], result.fresh_executions[0])

    def test_zero_is_only_non_observation(self):
        result = self.execution_case(specs=([], [], []))
        self.assertIs(result.outcome, Outcome.NO_POSITIVE_EXECUTION_OBSERVED)
        self.assertEqual(result.historical_execution_quantity_lower_bound, D(0))
        self.assertEqual(result.facts, ())
        self.assertIn('ABSENCE_IS_NOT_NO_EXECUTION_PROOF', result.limitations)


    def test_repeated_fresh_occurrences_preserved_but_count_once(self):
        result = self.execution_case(specs=([], [{}, {}, {}], []))
        self.assertEqual(len(result.facts), 1)
        self.assertEqual(len(result.facts[0].fresh_occurrences), 3)
        self.assertTrue(all(a is b for a, b in zip(result.facts[0].fresh_occurrences, result.fresh_executions)))
        self.assertEqual(result.historical_execution_quantity_lower_bound, D(4))

    def test_durable_replay_retains_its_reviewed_unique_execution_semantics(self):
        result = self.execution_case(durable=({}, {}), specs=([], [], []))
        self.assertEqual(len(result.durable_executions), 1)
        self.assertEqual(result.historical_execution_quantity_lower_bound, D(4))
        records = result.pre7.pre6.pre5.status.prefix.records
        self.assertEqual(sum(type(r.payload) is j._ExecutionObserved for r in records), 1)
        self.assertEqual(sum(type(r.payload) is j._DuplicateCallbackCovered for r in records), 1)

    def test_distinct_ids_not_deduplicated_by_equal_economics(self):
        specs = [{'values': {'execId': name, 'shares': D(2)}} for name in ('z-last', 'a-first')]
        result = self.execution_case(durable=({'shares': D(1)},), specs=([], specs, []))
        self.assertEqual(tuple(f.exec_id for f in result.facts), ('a-first', 'exec-1', 'z-last'))
        self.assertEqual(result.historical_execution_quantity_lower_bound, D(5))

    def test_numeric_equal_decimal_scales_agree_without_source_normalization(self):
        result = self.execution_case(durable=({'shares': D('4.00'), 'price': D('1.2500')},))
        self.assertIs(result.outcome, Outcome.EXECUTION_EVIDENCE_INCORPORATED)
        self.assertEqual(result.durable_executions[0].shares.as_tuple(), D('4.00').as_tuple())
        self.assertEqual(result.durable_executions[0].price.as_tuple(), D('1.2500').as_tuple())
        self.assertEqual(result.fresh_executions[0].shares.as_tuple(), D(4).as_tuple())

    def test_low_precision_and_traps_cannot_round_lower_bound(self):
        result = self.execution_case(durable=({'shares': D('0.1')},),
            specs=([], [{'values': {'execId': 'second', 'shares': D('0.2')}}], []))
        with localcontext() as context:
            context.prec = 1; context.traps[Inexact] = context.traps[Rounded] = True
            repeated = self.incorporate()
            self.assertEqual(repeated, result)
            self.assertEqual(repeated.historical_execution_quantity_lower_bound, D('0.3'))
            self.assertIs(self.incorporator._require_evidence(repeated), repeated)

    def test_full_requested_quantity_is_still_only_lower_bound(self):
        result = self.execution_case(specs=([], [{'values': {'shares': D(10)}}], []))
        self.assertEqual(result.historical_execution_quantity_lower_bound, D(10))
        self.assertIn('NO_EXECUTION_COMPLETENESS_PROOF', result.limitations)
        self.assertIn('NO_LIFECYCLE_OR_REMAINDER', result.limitations)

    def test_unresolved_requested_perm_candidate_is_incorporated(self):
        result = self.execution_case(specs=([], [{'absent': ('permId',)}], []),
                                     statuses=(), identity={'perm_id': 77})
        self.assertEqual(len(self.root7.pre6.executions.unresolved_perm_id), 1)
        self.assertIs(result.outcome, Outcome.EXECUTION_EVIDENCE_INCORPORATED)

    def test_unrelated_raw_observations_cannot_rescue_by_execid_or_orderref(self):
        specs = [{'values': {'orderId': 99}}, {'values': {'clientId': 7}},
                 {'values': {'execId': 'target', 'shares': D(2)}}]
        # Different execIds avoid violating PRE2's stronger same-source identity check.
        specs[0]['values']['execId'] = 'foreign-order'
        specs[1]['values']['execId'] = 'foreign-client'
        result = self.execution_case(specs=([], specs, []))
        self.assertEqual(tuple(f.exec_id for f in result.facts), ('target',))
        self.assertEqual(len(self.receipts[1].observations), 3)
        self.assertEqual(result.historical_execution_quantity_lower_bound, D(2))

    def test_unrelated_same_execid_does_not_conflict_with_durable_target(self):
        result = self.execution_case(durable=({},), specs=([], [
            {'values': {'orderId': 99, 'shares': D(9)}}], []))
        self.assertEqual(result.fresh_executions, ())
        self.assertEqual(result.historical_execution_quantity_lower_bound, D(4))

    def test_optional_fields_are_retained_without_cross_domain_interpretation(self):
        result = self.execution_case(durable=({},), specs=([], [{'values': {
            'orderRef': 'unrelated', 'cumQty': D(9), 'time': 'opaque-other-time'}}], []))
        self.assertEqual(result.historical_execution_quantity_lower_bound, D(4))
        self.assertEqual(result.fresh_executions[0].order_ref, 'unrelated')
        self.assertIsNone(result.durable_executions[0].cumulative_quantity)

    def test_optional_durable_client_absence_remains_absent(self):
        result = self.execution_case(durable=({'clientId': None},))
        self.assertIsNone(result.durable_executions[0].client_id)
        self.assertEqual(result.historical_execution_quantity_lower_bound, D(4))

    def test_fresh_cumulative_quantity_does_not_invent_execution_shares(self):
        result = self.execution_case(specs=([], [{'values': {'shares': D(2), 'cumQty': D(10)}}], []))
        self.assertEqual(result.historical_execution_quantity_lower_bound, D(2))

    def test_later_durable_execution_does_not_enrich_frozen_result(self):
        result = self.execution_case(durable=({},)); old_values = m._values(result)
        self.emit_durable(execId='later-execution', shares=D(2))
        self.certify(AT + timedelta(seconds=2))
        self.assertIs(self.incorporator._require_evidence(result), result)
        self.assertEqual(m._values(result), old_values)
        self.assertEqual(len(result.durable_executions), 1)
        self.assertEqual(tuple(f.exec_id for f in result.facts), ('exec-1',))

    def test_repeated_evaluation_is_equal_but_supersedes_issuer_slot(self):
        first = self.incorporate(); second = self.incorporate()
        self.assertEqual(first, second); self.assertIsNot(first, second)
        self.invalid(self.incorporator._require_evidence, first)
        self.assertIs(self.incorporator._require_evidence(second), second)

    def test_original_pre7_validator_before_and_after_analysis(self):
        original = type(self.attributor)._require_evidence; calls = []
        def checked(owner, result):
            calls.append((owner, result)); return original(owner, result)
        self.change(self.transport, '_attributed_execution_evidence_owner', None)
        with patch.object(type(self.attributor), '_require_evidence', checked):
            owner = m._create_attributed_execution_evidence_owner(self.attributor)
            result = owner._evaluate(self.root7)
            self.assertIs(owner._require_evidence(result), result)
        self.assertGreaterEqual(len(calls), 4)
        self.assertTrue(all(o is self.attributor and r is self.root7 for o, r in calls))


class ConflictTests(Fixture):
    def conflict(self, result, *reasons):
        self.assertIs(result.outcome, Outcome.EXECUTION_EVIDENCE_CONFLICT)
        self.assertIsNone(result.historical_execution_quantity_lower_bound)
        self.assertEqual(result.facts, ())
        self.assertEqual(result.reasons, tuple(r for r in Reason if r in reasons))
        self.assertIs(result.pre7, self.root7)
        self.assertEqual(self.safety(), self.before)

    def test_required_shares_conflict_no_source_wins(self):
        result = self.execution_case(durable=({},), specs=([], [{'values': {'shares': D(3)}}], []))
        self.conflict(result, Reason.EXEC_ID_REQUIRED_FACT_CONFLICT)
        self.assertEqual(result.durable_executions[0].shares, D(4))
        self.assertEqual(result.fresh_executions[0].shares, D(3))

    def test_required_price_conflict_no_source_wins(self):
        result = self.execution_case(durable=({},), specs=([], [{'values': {'price': D(2)}}], []))
        self.conflict(result, Reason.EXEC_ID_REQUIRED_FACT_CONFLICT)

    def test_required_conid_and_local_contract_conflict(self):
        result = self.execution_case(durable=({},), specs=([], [{'contract': {'conId': 31}}], []))
        self.conflict(result, Reason.EXEC_ID_REQUIRED_FACT_CONFLICT, Reason.LOCAL_CONTRACT_CONFLICT)

    def test_required_side_and_local_side_conflict(self):
        result = self.execution_case(durable=({},), specs=([], [{'values': {'side': 'SLD'}}], []))
        self.conflict(result, Reason.EXEC_ID_REQUIRED_FACT_CONFLICT, Reason.LOCAL_SIDE_CONFLICT)

    def test_fresh_only_wrong_contract_fails_incorporation(self):
        result = self.execution_case(specs=([], [{'contract': {'conId': 31}}], []))
        self.conflict(result, Reason.LOCAL_CONTRACT_CONFLICT)

    def test_fresh_only_wrong_side_fails_incorporation(self):
        result = self.execution_case(specs=([], [{'values': {'side': 'BUY'}}], []))
        self.conflict(result, Reason.LOCAL_SIDE_CONFLICT)

    def test_unique_sum_above_requested_no_cap_or_partial_salvage(self):
        result = self.execution_case(durable=({},), specs=([], [
            {'values': {'execId': 'second', 'shares': D(7)}}], []))
        self.conflict(result, Reason.INCORPORATED_QUANTITY_EXCEEDS_REQUESTED)
        self.assertEqual(len(result.durable_executions) + len(result.fresh_executions), 2)

    def test_hostile_context_cannot_hide_small_overflow(self):
        self.execution_case(durable=({'shares': D(10)},), specs=([], [
            {'values': {'execId': 'extra', 'shares': D('1E-64')}}], []))
        with localcontext() as context:
            context.prec = 1; context.traps[Inexact] = context.traps[Rounded] = True
            result = self.incorporate()
        self.conflict(result, Reason.INCORPORATED_QUANTITY_EXCEEDS_REQUESTED)


class IssuerTests(Fixture):
    def test_no_arbitrary_input_owner_or_execution_list(self):
        self.invalid(Owner)
        for value in (None, {}, object(), self.root7, self.root6, self.cross, self.journal):
            self.invalid(m._create_attributed_execution_evidence_owner, value)
        self.assertEqual(list(inspect.signature(Owner._evaluate).parameters), ['self', 'pre7_result'])

    def test_single_registration_and_original_owner_required(self):
        self.invalid(m._create_attributed_execution_evidence_owner, self.attributor)
        clone = object.__new__(type(self.attributor))
        for slot in type(clone).__slots__:
            name = '_AttemptAttributionEvidenceOwner' + slot
            object.__setattr__(clone, name, getattr(self.attributor, name))
        self.invalid(m._create_attributed_execution_evidence_owner, clone)

    def test_reconstructed_pre7_root_rejected(self):
        self.invalid(self.incorporator._evaluate, replace(self.root7))

    def test_superseded_pre7_root_invalidates_pre8(self):
        result = self.incorporate(); newer = self.attribute()
        self.assertEqual(newer, self.root7)
        self.invalid(self.incorporator._require_evidence, result)
        self.invalid(self.incorporator._evaluate, newer)

    def test_reconstructed_and_pickled_result_no_authority(self):
        result = self.incorporate()
        for clone in (replace(result), pickle.loads(pickle.dumps(result))):
            self.assertEqual(clone, result)
            self.invalid(self.incorporator._require_evidence, clone)
        self.assertIs(self.incorporator._require_evidence(result), result)

    def test_frozen_slotted_opaque_result_and_fact(self):
        result = self.incorporate(); fact = result.facts[0]
        for value in (result, fact, self.incorporator): self.assertFalse(hasattr(value, '__dict__'))
        with self.assertRaises(FrozenInstanceError): result.facts = ()
        with self.assertRaises(FrozenInstanceError): fact.exec_id = 'substituted'
        with self.assertRaises(ReadOnlyError): self.incorporator.authorized = True
        with self.assertRaises(ReadOnlyError): del self.incorporator._result
        self.assertEqual(repr(result), '<PrivateAttributedExecutionEvidence evidence-only>')
        self.assertNotIn('exec-1', repr(fact))
        self.assertEqual({f.name for f in fields(Result)}, {'pre7', 'durable_executions', 'fresh_executions',
            'facts', 'historical_execution_quantity_lower_bound', 'reasons', 'outcome', 'limitations'})

    def test_wrong_thread_rejected_before_issuer_io(self):
        result = self.incorporate()
        with ThreadPoolExecutor(max_workers=1) as pool:
            for function, args in ((self.incorporator._evaluate, (self.root7,)),
                    (self.incorporator._require_evidence, (result,)),
                    (m._create_attributed_execution_evidence_owner, (self.attributor,))):
                with self.assertRaises(ReadOnlyError) as caught: pool.submit(function, *args).result(timeout=5)
                self.assertEqual(caught.exception.code, m._ERROR)
        self.assertIs(self.incorporator._require_evidence(result), result)

    def test_storage_corruption_rejected_by_original_validation(self):
        result = self.incorporate(); self.corrupt_digest(self.boundary.committed.record.sequence)
        self.invalid(self.incorporator._require_evidence, result)
        self.assertIsNone(self.incorporator._result)

    def test_replaced_original_method_rejected(self):
        with patch.object(type(self.attributor), '_require_evidence', side_effect=AssertionError('changed issuer')):
            self.invalid(self.incorporate)


class FailureTests(Fixture):
    def test_ordinary_failure_is_sanitized_and_no_partial_publication(self):
        with patch.object(m, '_incorporate', side_effect=ValueError('private detail')):
            self.invalid(self.incorporate)
        self.assertIsNone(self.incorporator._result)
        self.assertIs(self.incorporator._require_evidence(self.incorporate()), self.incorporator._result)

    def test_reentrant_evaluation_cannot_publish(self):
        with patch.object(Owner, '_publish', side_effect=lambda result: self.incorporate()):
            self.invalid(self.incorporate)
        self.assertIsNone(self.incorporator._result)

    def test_equal_substitution_at_publication_is_rejected(self):
        with patch.object(Owner, '_publish', side_effect=lambda result: replace(result)):
            self.invalid(self.incorporate)
        self.assertIsNone(self.incorporator._result)


class NoAuthorityTests(Fixture):
    def test_only_inherited_issuer_io_no_operations_writes_or_clocks(self):
        connection = self.journal._store._connection
        forbidden = {getattr(sqlite3, name) for name in ('SQLITE_INSERT', 'SQLITE_UPDATE', 'SQLITE_DELETE',
            'SQLITE_CREATE_TABLE', 'SQLITE_DROP_TABLE', 'SQLITE_CREATE_TRIGGER', 'SQLITE_DROP_TRIGGER',
            'SQLITE_ALTER_TABLE', 'SQLITE_CREATE_INDEX', 'SQLITE_DROP_INDEX')}
        seen = []
        def authorize(action, *args):
            if action in forbidden: seen.append(action); return sqlite3.SQLITE_DENY
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
                for module in (accounting, lifecycle, recovery):
                    for name in dir(module):
                        value = getattr(module, name)
                        if inspect.isfunction(value) and value.__module__ == module.__name__:
                            stack.enter_context(patch.object(module, name, side_effect=AssertionError(name)))
                for name in ('time.time', 'time.monotonic'):
                    stack.enter_context(patch(name, side_effect=AssertionError('clock')))
                stack.enter_context(patch.object(dispatch, '_decision_utc_now', side_effect=AssertionError('clock')))
                result = self.incorporate()
                self.assertIs(self.incorporator._require_evidence(result), result)
        finally: connection.set_authorizer(None)
        self.assertEqual(seen, []); self.assertEqual(self.safety(), self.before)

    def test_no_reconsumption_and_original_source_method_guard_preserved(self):
        with patch.object(type(self.sources[0]), '_consume_receipt', side_effect=AssertionError('consume')):
            self.invalid(self.incorporate)
        self.assertEqual(self.consumed(), (True, True, True))

    def test_no_export_durability_or_authority(self):
        result = self.incorporate()
        self.assertEqual(m.__all__, ())
        self.assertEqual([name for name in dir(Owner) if not name.startswith('_')], [])
        self.assertNotIn(Result, storage._MODELS)
        with self.assertRaises(ReadOnlyError): storage._encode(result)
        self.assertTrue(self.root7.pre6.pre5.status.prefix.replay.restart_quarantine_required)
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.transport._snapshot.account.mode)
        self.assertFalse(self.broker.controls.trading_enabled)
        self.assertEqual(result.limitations, m._LIMITATIONS)
        self.assertEqual(self.safety(), self.before)

    def test_ast_forbids_economic_lifecycle_chronology_and_new_io(self):
        tree = ast.parse(Path(m.__file__).read_text())
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertTrue({'commissions', 'gross_notional', 'notional', 'average_price', 'filled', 'remaining',
            'historical_filled_lower_bound', 'observed_execution_quantity', 'order_ref', 'symbol',
            'execution_time', 'cumulative_quantity', 'perm_id', 'source', 'sequence'} .isdisjoint(attrs))
        imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertFalse(any('accounting' in (name or '') or 'working_order' in (name or '') for name in imports))
        calls = {n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id if isinstance(n.func, ast.Name) else ''
                 for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertTrue({'sum', 'max', 'min', 'open', 'connect', 'execute', '_append', '_append_event',
            '_load', '_collect', '_consume_receipt', '_prepare', '_certify_decision_boundary', '_drain_callbacks',
            'now', 'time', 'monotonic', 'reqAllOpenOrders', 'reqExecutions', 'reqCompletedOrders',
            'placeOrder', 'cancelOrder', 'subtract', 'multiply', 'exact_ratio'} .isdisjoint(calls))
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, ast.BinOp)
                          and not isinstance(n.op, ast.BitOr)], [])
        sums = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == '_sum']
        self.assertEqual(len(sums), 1)
        self.assertEqual(ast.unparse(sums[0]), 'journal._sum(quantity, fact.observation.shares)')


def _negative_case(**kwargs):
    def test(self):
        result = self.execution_case(**kwargs)
        self.assertIsNot(result.pre7.outcome, pre7._AttemptAttributionOutcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT)
        self.assertIs(result.outcome, Outcome.ATTRIBUTION_BLOCKED)
        self.assertEqual((result.durable_executions, result.fresh_executions, result.facts), ((), (), ()))
        self.assertIsNone(result.historical_execution_quantity_lower_bound)
        self.assertIsNone(result.attempt)
    return test


for _name, _kwargs in (
    ('no_support', {'specs': ([], [], []), 'statuses': ()}),
    ('no_reservation', {'attempts': ()}),
    ('not_invoked', {'attempts': (('entry-1', 10, 'reserved', False),)}),
    ('closed', {'attempts': (('entry-1', 10, 'closed', False),)}),
    ('session', {'attempts': (('other', 10, 'invocation', True),)}),
    ('client', {'specs': tuple([{'values': {'clientId': 5}}] for _ in range(3)),
                'statuses': ({'clientId': 5},), 'identity': {'client_id': 5}}),
    ('identity_conflict', {'specs': ([{}], [{'values': {'permId': 88}}], [{}])}),
    ('durable_ambiguity', {'statuses': ({'permId': 77}, {'permId': 88})})):
    setattr(SuccessTests, 'test_blocked_' + _name, _negative_case(**_kwargs))


class ClosedContractTests(unittest.TestCase):
    def test_every_negative_pre7_enum_takes_blocked_branch_without_reading_executions(self):
        # Defensive branch coverage only; these are not qualified runtime roots.
        for outcome in pre7._AttemptAttributionOutcome:
            if outcome is pre7._AttemptAttributionOutcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT: continue
            self.assertEqual(m._analyze(NS(outcome=outcome)), ((), (), (), None, (), Outcome.ATTRIBUTION_BLOCKED))

    def test_closed_outcomes_and_conflict_reasons(self):
        self.assertEqual(len(Outcome), 4); self.assertEqual(len(Reason), 4)
        self.assertEqual(len(m._LIMITATIONS), 18)

    def test_common_facts_include_numeric_order_not_optional_identity(self):
        event = NS(order_id=10, con_id=17, side='BOT', shares=D(4), price=D('1.25'))
        other = NS(**{**vars(event), 'order_id': 11})
        self.assertNotEqual(m._common(event), m._common(other))

    def test_empty_fact_is_not_a_positive_execution(self):
        with self.assertRaises(ReadOnlyError): Fact('exec-1', (), ())


def _source_semantics(spec, quantity=D(4)):
    def test(self):
        result = self.execution_case(specs=([], [spec], []))
        self.assertIs(result.outcome, Outcome.EXECUTION_EVIDENCE_INCORPORATED)
        self.assertEqual(result.historical_execution_quantity_lower_bound, quantity)
        self.assertIs(result.facts[0].fresh_occurrences[0], self.receipts[1].observations[0])
    return test


for _label, _spec in (
    ('legacy', {'encoding': 'LEGACY'}),
    ('protobuf', {'encoding': 'PROTOBUF'}),
    ('missing_perm', {'absent': ('permId',)}),
    ('missing_cumulative', {'absent': ('cumQty',)}),
    ('missing_execution_time', {'absent': ('time',)}),
    ('signed_zero_price', {'values': {'price': D('-0.00')}}),
    ('symbol_irrelevant', {'contract': {'symbol': 'OTHER'}}),
    ('exchange_irrelevant', {'contract': {'exchange': 'OTHER'}})):
    setattr(SuccessTests, 'test_source_semantics_' + _label, _source_semantics(_spec))


def _missing_numeric(field):
    def test(self):
        result = self.execution_case(specs=([], [{'absent': (field,)}], []))
        self.assertIs(result.outcome, Outcome.NO_POSITIVE_EXECUTION_OBSERVED)
        self.assertEqual(result.fresh_executions, ())
        self.assertEqual(result.historical_execution_quantity_lower_bound, D(0))
    return test


for _field in ('orderId', 'clientId'):
    setattr(SuccessTests, 'test_missing_numeric_identity_' + _field, _missing_numeric(_field))


def _durable_reference(field):
    def test(self):
        result = self.execution_case(durable=({},))
        if field == 'tuple':
            self.change(result, 'durable_executions', tuple(list(result.durable_executions)))
        elif field == 'attempt_tuple':
            self.change(result.attempt, 'executions', tuple(list(result.attempt.executions)))
        else:
            self.change(result, 'durable_executions', (replace(result.durable_executions[0]),))
        self.invalid(self.incorporator._require_evidence, result)
    return test


for _field in ('tuple', 'attempt_tuple', 'observation'):
    setattr(IssuerTests, 'test_equal_durable_reference_' + _field, _durable_reference(_field))


def _ordinary_failure(seam):
    def test(self):
        target = m if seam in ('_analyze', '_values', '_incorporate') else Owner
        with patch.object(target, seam, side_effect=ValueError('private detail')):
            self.invalid(self.incorporate)
        self.assertIsNone(self.incorporator._result)
        self.assertFalse(getattr(self.incorporator, PREFIX + '__evaluating'))
        self.assertEqual(self.safety(), self.before)
    return test


for _seam in ('_analyze', '_values', '_incorporate', '_publish', '_set', '_validate_root'):
    setattr(FailureTests, 'test_sanitized_failure_' + _seam, _ordinary_failure(_seam))


def _original_interruption(kind):
    def test(self):
        error = kind(91) if kind is SystemExit else kind('original issuer interruption')
        def raising(owner, result): raise error
        self.change(self.incorporator, PREFIX + '__require_evidence', raising.__get__(self.attributor))
        with patch.object(type(self.attributor), '_require_evidence', raising):
            with self.assertRaises(kind) as caught: self.incorporate()
        self.assertIs(caught.exception, error)
        if kind is SystemExit: self.assertEqual(caught.exception.code, 91)
        self.assertIsNone(self.incorporator._result)
    return test


for _kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    setattr(FailureTests, 'test_original_issuer_' + _kind.__name__, _original_interruption(_kind))


def _result_tamper(field):
    def test(self):
        result = self.incorporate(); value = getattr(result, field)
        if field == 'historical_execution_quantity_lower_bound': changed = D(9)
        elif field in ('outcome',): changed = Outcome.NO_POSITIVE_EXECUTION_OBSERVED
        elif field == 'pre7': changed = replace(value)
        elif field == 'fresh_executions': changed = tuple(replace(v) for v in value)
        elif field == 'facts': changed = tuple(replace(v) for v in value)
        elif field == 'durable_executions': changed = (result.fresh_executions[0],)
        else: changed = ('substituted',)
        self.change(result, field, changed)
        self.invalid(self.incorporator._require_evidence, result)
    return test


for _field in (f.name for f in fields(Result)):
    setattr(IssuerTests, 'test_result_tamper_' + _field, _result_tamper(_field))


def _graph_tamper(field):
    def test(self):
        result = self.incorporate(); event = result.fresh_executions[0]
        obj, name, value = {
            'attempt': (self.root7, 'attempt', replace(self.root7.attempt)),
            'requested': (self.root7.attempt.consumed.order, 'quantity', D(11)),
            'durable_tuple': (self.root7.attempt, 'executions', (event,)),
            'candidate_tuple': (self.root7.pre6.executions, 'candidates', tuple([event])),
            'fresh_observation': (result, 'fresh_executions', (replace(event),)),
            'fact': (result, 'facts', (replace(result.facts[0]),)),
            'fact_durable': (result.facts[0], 'durable_occurrences', (event,)),
            'fact_fresh': (result.facts[0], 'fresh_occurrences', (replace(event),)),
            'fact_id': (result.facts[0], 'exec_id', 'other'),
            'shares': (event, 'shares', D(3)), 'shares_shape': (event, 'shares', D('4.0')),
            'price': (event, 'price', D(2)), 'con_id': (event, 'con_id', 31),
            'side': (event, 'side', 'SLD'),
            'prefix': (self.root7.pre6.pre5.status, 'prefix', replace(self.root7.pre6.pre5.status.prefix)),
            'correlation': (self.root7.pre6, 'executions', replace(self.root7.pre6.executions)),
            'registration': (self.transport, '_attributed_execution_evidence_owner', object()),
            'issuer_registration': (self.transport, '_attempt_attribution_evidence_owner', object()),
            'read_generation': (self.transport, '_generation', self.transport._generation + 1),
            'write_generation': (self.write, '_generation', self.write._generation + 1),
            'read_client': (self.transport, '_client', object()),
            'disconnect': (self.transport, '_disconnect_emitted', True),
        }[field]
        self.change(obj, name, value)
        self.invalid(self.incorporator._require_evidence, result)
    return test


for _field in ('attempt', 'requested', 'durable_tuple', 'candidate_tuple', 'fresh_observation', 'fact',
    'fact_durable', 'fact_fresh', 'fact_id', 'shares', 'shares_shape', 'price', 'con_id', 'side',
    'prefix', 'correlation', 'registration', 'issuer_registration', 'read_generation',
    'write_generation', 'read_client', 'disconnect'):
    setattr(IssuerTests, 'test_graph_tamper_' + _field, _graph_tamper(_field))


def _interruption(kind, seam, validating=False):
    def test(self):
        result = self.incorporate(); error = kind(91) if kind is SystemExit else kind('interrupted')
        target, name = (m, seam) if seam in ('_incorporate', '_values', '_analyze') else (Owner, seam)
        with patch.object(target, name, side_effect=error):
            with self.assertRaises(kind) as caught:
                if validating: self.incorporator._require_evidence(result)
                else: self.incorporate()
        self.assertIs(caught.exception, error)
        if kind is SystemExit: self.assertEqual(caught.exception.code, 91)
        if seam != '_analyze' or not validating: self.assertIsNone(self.incorporator._result)
        self.assertFalse(getattr(self.incorporator, PREFIX + '__evaluating'))
        self.assertEqual(self.safety(), self.before)
    return test


for _kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    for _seam in ('_validate_root', '_publish', '_set', '_incorporate', '_values', '_analyze'):
        setattr(FailureTests, f'test_{_kind.__name__}_{_seam}', _interruption(_kind, _seam))
    setattr(FailureTests, f'test_{_kind.__name__}_revalidation', _interruption(_kind, '_validate_root', True))


def _after_assignment(kind):
    def test(self):
        original = Owner._set; error = kind(91) if kind is SystemExit else kind('after assignment')
        def assign(owner, name, value):
            original(owner, name, value)
            if name == 'result': raise error
        with patch.object(Owner, '_set', assign):
            with self.assertRaises(kind) as caught: self.incorporate()
        self.assertIs(caught.exception, error)
        self.assertIsNone(self.incorporator._result)
        self.assertFalse(getattr(self.incorporator, PREFIX + '__evaluating'))
        self.assertIs(self.incorporator._require_evidence(self.incorporate()), self.incorporator._result)
    return test


for _kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    setattr(FailureTests, 'test_after_final_assignment_' + _kind.__name__, _after_assignment(_kind))


def _reference_field(absent, value):
    def test(self):
        spec = {'absent': ('orderRef',)} if absent else {'values': {'orderRef': value}}
        result = self.execution_case(specs=([], [spec], []))
        self.assertIs(result.outcome, Outcome.EXECUTION_EVIDENCE_INCORPORATED)
        self.assertEqual(result.historical_execution_quantity_lower_bound, D(4))
    return test


for _label, _absent, _value in (('missing', True, None), ('different', False, 'foreign-reference'), ('empty', False, '')):
    setattr(SuccessTests, 'test_orderref_not_attribution_' + _label, _reference_field(_absent, _value))


if __name__ == '__main__': unittest.main()
