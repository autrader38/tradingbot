"""PRE6 uses exact live issuers and anonymous SDKs; real network is forbidden."""

import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from decimal import Decimal, localcontext, Inexact, Rounded
import inspect
from pathlib import Path
import pickle
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tradingbot_broker import paper_cross_source_identity_evidence as m
from tradingbot_broker import paper_prepared_decision_evidence as pre5
from tradingbot_broker import paper_decision_status_evidence as status
from tradingbot_broker import paper_dispatch_journal as journal
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker import paper_execution_accounting as accounting
from tradingbot_broker import paper_working_order as lifecycle
from tradingbot_broker import paper_recovery as recovery
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests.test_paper_prepared_decision_evidence import Fixture as PRE5Fixture
from tests.test_ibkr_reconciliation_evidence import CONTRACT, ORDER, EXECUTION, raw_proto


Owner = m._CrossSourceIdentityEvidenceOwner
Result = m._CrossSourceIdentityEvidence
Outcome = m._CrossSourceIdentityOutcome
Reason = m._IdentityContradictionReason
Source = m._IdentitySource
Coverage = m._SourceObservationCoverage
PREFIX = '_CrossSourceIdentityEvidenceOwner'


class Fixture(PRE5Fixture):
    # Fixtures pass through the original collectors, source sanitizers, consumers,
    # producer, durable loader and PRE5. No reconstructed receipt is authority.
    def collect_sources(self, **ignored):
        self.build()
        self.coordinator._broker_state_reconciliation_required = True
        wrapper = self.api.clients[-1].wrapper
        account = self.transport._accounts[0]
        specifications = getattr(self, 'specifications', ([{}], [{}], [{}]))
        def emit(source, spec, request_id=None):
            values = dict(EXECUTION if source == 'executions' else ORDER)
            values.update(spec.get('values', {}))
            for name in spec.get('absent', ()): values.pop(name, None)
            contract = dict(CONTRACT, **spec.get('contract', {}))
            encoding = spec.get('encoding', 'PROTOBUF')
            state = spec.get('status', 'Submitted')
            if encoding == 'PROTOBUF':
                top = values.get('orderId') if source == 'orders' and spec.get('top', True) else None
                raw = raw_proto(source, payload=values, contract=contract, status=state, top=top)
                method = {'orders': 'openOrderProtoBuf', 'executions': 'executionDetailsProtoBuf',
                          'completed': 'completedOrderProtoBuf'}[source]
                getattr(wrapper, method)(raw)
            # Decoded defaults cannot qualify fields absent from raw protobuf.
            decoded = dict(EXECUTION if source == 'executions' else ORDER)
            decoded.update(values)
            if source == 'executions':
                wrapper.execDetails(request_id, NS(**contract), NS(**decoded, acctNumber=account))
            elif source == 'orders':
                wrapper.openOrder(decoded['orderId'], NS(**contract), NS(**decoded, account=account), NS(status=state))
            else:
                wrapper.completedOrder(NS(**contract), NS(**decoded, account=account), NS(status=state))
        def open_request(client):
            for spec in specifications[0]: emit('orders', spec)
            wrapper.openOrderEnd()
        def executions_request(client, request_id, query_filter):
            for spec in specifications[1]: emit('executions', spec, request_id)
            wrapper.execDetailsEnd(request_id)
        def completed_request(client, api_only):
            self.assertIs(api_only, False)
            for spec in specifications[2]: emit('completed', spec)
            wrapper.completedOrdersEnd()
        with patch.object(self.api.Client, 'reqAllOpenOrders', open_request), \
                patch.object(self.api.Client, 'reqExecutions', executions_request), \
                patch.object(self.api.Client, 'reqCompletedOrders', completed_request):
            from tradingbot_broker import paper_fresh_open_order_collection as pre1
            from tradingbot_broker import paper_fresh_execution_collection as pre2
            from tradingbot_broker import paper_fresh_completed_order_collection as pre3
            self.sources = tuple(factory(self.journal) for factory in (
                pre1._create_fresh_open_order_collection_owner,
                pre2._create_fresh_execution_collection_owner,
                pre3._create_fresh_completed_order_collection_owner))
            self.receipts = tuple(owner._collect() for owner in self.sources)
        self.before = self.safety()

    def setUp(self):
        super().setUp()
        self.root = self.evaluate()
        self.cross = m._create_cross_source_identity_evidence_owner(self.combiner)
        self.before = self.safety()

    def scenario(self, specs=None, *, statuses=({},), identity=None):
        self.specifications = ([{}], [{}], [{}]) if specs is None else specs
        self.collect_sources()
        self.prepare()
        for changes in statuses: self.qualified(**changes)
        self.boundary = self.certify()
        self.combiner = pre5._create_prepared_decision_evidence_owner(self.preparation)
        self.root = self.evaluate(identity=self.identity(**(identity or {})))
        self.cross = m._create_cross_source_identity_evidence_owner(self.combiner)
        self.before = self.safety()
        return self.correlate()

    def correlate(self): return self.cross._evaluate(self.root)

    def invalid(self, function, *args, **kwargs):
        with self.assertRaises(ReadOnlyError) as caught: function(*args, **kwargs)
        self.assertEqual(caught.exception.code, m._ERROR)
        self.assertEqual(str(caught.exception), m._ERROR)

    def correlations(self, result): return result.open_orders, result.executions, result.completed_orders


class SuccessTests(Fixture):
    def test_exact_original_sources_and_identity(self):
        result = self.correlate()
        self.assertIs(result.pre5, self.root)
        self.assertIs(result.identity, self.root.status.identity)
        self.assertIs(result.decision_status, self.root.status)
        for correlation, receipt in zip(self.correlations(result), self.receipts):
            self.assertEqual(correlation.coverage, Coverage.TARGET_MATCH_OBSERVED)
            self.assertEqual(len(correlation.candidates), 1)
            self.assertIs(correlation.candidates[0], receipt.observations[0])
            self.assertIs(correlation.matches[0], receipt.observations[0])
        self.assertEqual(result.outcome, Outcome.CORRELATED_NO_PROVEN_IDENTITY_CONTRADICTION)
        self.assertEqual(result.reasons, ())
        self.assertEqual(self.safety(), self.before)

    def test_exact_pre5_validator_is_used(self):
        original = type(self.combiner)._require_evidence
        calls = []
        def checked(owner, evidence):
            calls.append((owner, evidence))
            return original(owner, evidence)
        self.change(self.transport, '_cross_source_identity_evidence_owner', None)
        with patch.object(type(self.combiner), '_require_evidence', checked):
            owner = m._create_cross_source_identity_evidence_owner(self.combiner)
            result = owner._evaluate(self.root)
            self.assertIs(owner._require_evidence(result), result)
        self.assertGreaterEqual(len(calls), 4)
        self.assertTrue(all(o is self.combiner and r is self.root for o, r in calls))

    def test_known_perm_fact_references_include_supported_pre4(self):
        result = self.correlate()
        self.assertEqual([f.source for f in result.known_perm_id_facts], list(Source))
        for fact, receipt in zip(result.known_perm_id_facts, self.receipts):
            self.assertIs(fact.observation, receipt.observations[0])
        self.assertIs(result.known_perm_id_facts[-1].observation, self.root.status.observation)

    def test_duplicate_occurrences_are_kept_in_original_order(self):
        result = self.scenario(([{}, {}, {}], [{}, {}], [{}, {}, {}, {}]))
        for correlation, receipt in zip(self.correlations(result), self.receipts):
            self.assertEqual(len(correlation.candidates), receipt.count)
            self.assertTrue(all(a is b for a, b in zip(correlation.matches, receipt.observations)))
        self.assertEqual(len(result.executions.matches), 2)
        self.assertEqual(result.executions.matches[0].exec_id, result.executions.matches[1].exec_id)

    def test_unrelated_interleaving_preserves_source_order(self):
        result = self.scenario(([{}, {'values': {'orderId': 11}}, {}], [{}], [{}]))
        self.assertEqual([e.sequence for e in result.open_orders.candidates], [1, 3])
        self.assertIs(result.open_orders.candidates[-1], self.receipts[0].observations[-1])

    def test_all_fresh_empty_is_only_non_observation(self):
        result = self.scenario(([], [], []))
        self.assertEqual(result.outcome, Outcome.NO_QUALIFIED_FRESH_MATCH)
        self.assertIs(result.decision_status.outcome, status._DecisionStatusOutcome.SUPPORTED_AT_DECISION_BOUNDARY)
        self.assertEqual(result.reasons, ())
        self.assertTrue(all(c.coverage is Coverage.NO_QUALIFIED_CANDIDATE_OBSERVED for c in self.correlations(result)))

    def test_legacy_completed_numeric_absence_cannot_be_rescued(self):
        result = self.scenario(([{}], [{}], [{'encoding': 'LEGACY'}]))
        event = self.receipts[2].observations[0]
        self.assertEqual(event.perm_id, 77)
        self.assertEqual(event.order_ref, 'entry-1')
        self.assertIsNone(event.order_id); self.assertIsNone(event.client_id)
        self.assertEqual(result.completed_orders.candidates, ())
        self.assertEqual(result.outcome, Outcome.PARTIAL_IDENTITY_COVERAGE)

    def test_fresh_sources_do_not_repair_durable_ambiguity(self):
        result = self.scenario(statuses=({'permId': 77}, {'permId': 88}))
        self.assertIs(result.decision_status.outcome, status._DecisionStatusOutcome.AMBIGUOUS_IDENTITY)
        self.assertEqual(result.outcome, Outcome.DURABLE_IDENTITY_AMBIGUOUS)
        self.assertEqual(result.reasons, (Reason.DURABLE_IDENTITY_AMBIGUITY,))
        self.assertNotIn(Source.DECISION_STATUS, [f.source for f in result.known_perm_id_facts])

    def test_fresh_match_without_qualified_status(self):
        result = self.scenario(statuses=())
        self.assertEqual(result.outcome, Outcome.CORRELATED_NO_PROVEN_IDENTITY_CONTRADICTION)
        self.assertIs(result.decision_status.outcome, status._DecisionStatusOutcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)
        self.assertNotIn(Source.DECISION_STATUS, [f.source for f in result.known_perm_id_facts])

    def test_observed_client_can_differ_from_placing_client(self):
        specs = tuple([{'values': {'clientId': 5}}] for _ in range(3))
        result = self.scenario(specs, statuses=({'clientId': 5},), identity={'client_id': 5})
        self.assertNotEqual(result.identity.client_id, self.token.placing_client_id)
        self.assertEqual(result.outcome, Outcome.CORRELATED_NO_PROVEN_IDENTITY_CONTRADICTION)

    def test_no_target_perm_does_not_invent_one(self):
        result = self.correlate()
        self.assertIsNone(result.identity.perm_id)
        self.assertEqual({f.perm_id for f in result.known_perm_id_facts}, {77})

    def test_supported_status_unknown_perm_is_not_filled_from_fresh(self):
        result = self.scenario(statuses=({'permId': None},))
        self.assertIsNone(result.decision_status.observation.perm_id)
        self.assertNotIn(Source.DECISION_STATUS, [f.source for f in result.known_perm_id_facts])

    def test_later_callbacks_do_not_enrich_original_boundary(self):
        result = self.correlate()
        self.qualified(permId=88)
        self.assertIs(self.cross._require_evidence(result), result)
        self.assertEqual(result.decision_status.through_sequence, 1)
        self.assertIsNone(result.decision_status.next_status_sequence)
        self.assertEqual({f.perm_id for f in result.known_perm_id_facts}, {77})

    def test_repeat_evaluation_is_structurally_deterministic_but_new_issuer_slot(self):
        first = self.correlate(); second = self.correlate()
        self.assertEqual(first, second); self.assertIsNot(first, second)
        self.invalid(self.cross._require_evidence, first)
        self.assertIs(self.cross._require_evidence(second), second)

    def test_frozen_slotted_opaque_and_no_authority_fields(self):
        result = self.correlate()
        for value in (self.cross, result, result.open_orders, result.known_perm_id_facts[0]):
            self.assertFalse(hasattr(value, '__dict__'))
            self.assertEqual(repr(value), '<PrivateCrossSourceIdentityEvidence evidence-only>')
        with self.assertRaises(FrozenInstanceError): result.identity = None
        with self.assertRaises(ReadOnlyError): self.cross.authority = True
        with self.assertRaises(ReadOnlyError): del self.cross._result
        self.assertEqual({f.name for f in fields(Result)}, {'pre5', 'identity', 'open_orders', 'executions',
            'completed_orders', 'decision_status', 'known_perm_id_facts', 'reasons', 'outcome', 'limitations'})

    def test_individual_limits_survive_and_fixed_pre6_limits_are_exact(self):
        result = self.correlate()
        self.assertEqual(result.limitations, m._LIMITATIONS)
        self.assertEqual(len(result.limitations), 17)
        self.assertIs(result.pre5, self.root)
        self.assertIs(result.pre5.prepared, self.token)
        for receipt, original in zip((self.token.open_order_receipt, self.token.execution_receipt,
                                      self.token.completed_order_receipt), self.receipts):
            self.assertIs(receipt.coverage_limitations, original.coverage_limitations)

    def test_source_decimal_shapes_and_multiple_executions_are_not_calculated(self):
        specs = ([{'values': {'totalQuantity': Decimal('10.00')}}],
            [{'values': {'shares': Decimal('4.00'), 'price': Decimal('-0.00')}},
             {'values': {'execId': 'exec-2', 'shares': Decimal('7.000'), 'price': Decimal('2.00')}}],
            [{'values': {'totalQuantity': Decimal('400.000')}}])
        result = self.scenario(specs, statuses=({'filled': '-0.00'},))
        self.assertEqual(result.reasons, ())
        self.assertEqual(result.open_orders.matches[0].quantity.as_tuple(), Decimal('10.00').as_tuple())
        self.assertEqual(result.executions.matches[0].price.as_tuple(), Decimal('-0.00').as_tuple())
        self.assertEqual(result.executions.matches[1].shares.as_tuple(), Decimal('7.000').as_tuple())
        self.assertEqual(result.decision_status.observation.filled.as_tuple(), Decimal('-0.00').as_tuple())
        with localcontext() as context:
            context.prec = 1; context.traps[Inexact] = True; context.traps[Rounded] = True
            self.assertIs(self.cross._require_evidence(result), result)

    def test_source_contract_difference_is_not_identity_conflict(self):
        result = self.scenario(([{}], [{}], [{'contract': {'conId': 19, 'symbol': 'OTHER', 'exchange': 'ISLAND'}}]))
        self.assertEqual(result.reasons, ())
        self.assertEqual(result.outcome, Outcome.CORRELATED_NO_PROVEN_IDENTITY_CONTRADICTION)

    def test_matching_reference_perm_symbol_and_contract_without_pair_cannot_rescue(self):
        specs = tuple([{'absent': ('orderId', 'clientId'), 'top': False}] for _ in range(3))
        result = self.scenario(specs)
        self.assertEqual(result.outcome, Outcome.NO_QUALIFIED_FRESH_MATCH)
        self.assertEqual([f.source for f in result.known_perm_id_facts], [Source.DECISION_STATUS])

    def test_supported_legacy_status_ambiguous_perm_zero_remains_unknown(self):
        self.specifications = ([{}], [{}], [{}]); self.collect_sources(); self.prepare()
        from tests.test_ibkr_order_status_evidence import LEGACY
        values = list(LEGACY); values[5] = 0
        self.client.decoder.interpret((37, *values))  # Anonymous fake decoder shape only.
        self.boundary = self.certify()
        self.combiner = pre5._create_prepared_decision_evidence_owner(self.preparation)
        self.root = self.evaluate()
        self.cross = m._create_cross_source_identity_evidence_owner(self.combiner)
        result = self.correlate()
        self.assertEqual(result.decision_status.observation.perm_id, 0)
        self.assertNotIn(Source.DECISION_STATUS, [f.source for f in result.known_perm_id_facts])
        self.assertEqual(result.reasons, ())


class ConflictTests(Fixture):
    def test_conflicting_fresh_perms_have_no_winner(self):
        result = self.scenario(([{}], [{'values': {'permId': 88}}], [{}]))
        self.assertEqual(result.outcome, Outcome.IDENTITY_CONTRADICTION)
        self.assertEqual(result.reasons, (Reason.KNOWN_PERMID_CONFLICT,))
        self.assertEqual([f.perm_id for f in result.known_perm_id_facts], [77, 88, 77, 77])
        self.assertIs(result.executions.candidates[0], self.receipts[1].observations[0])

    def test_requested_perm_conflict_is_distinct_from_known_set_conflict(self):
        result = self.scenario(tuple([{'values': {'permId': 88}}] for _ in range(3)),
                               statuses=(), identity={'perm_id': 77})
        self.assertEqual(result.reasons, (Reason.REQUESTED_PERMID_CONFLICT,))
        self.assertTrue(all(c.conflicts and not c.matches for c in self.correlations(result)))

    def test_supported_pre4_perm_conflicts_with_fresh(self):
        result = self.scenario(tuple([{'values': {'permId': 88}}] for _ in range(3)))
        self.assertEqual(result.reasons, (Reason.KNOWN_PERMID_CONFLICT,))
        self.assertEqual(result.outcome, Outcome.IDENTITY_CONTRADICTION)

    def test_requested_and_cross_source_conflicts_both_retained(self):
        result = self.scenario(([{}], [{'values': {'permId': 88}}], [{}]), identity={'perm_id': 77})
        self.assertEqual(result.reasons, (Reason.REQUESTED_PERMID_CONFLICT, Reason.KNOWN_PERMID_CONFLICT))

    def test_unresolved_requested_perm_is_not_contradiction(self):
        result = self.scenario(tuple([{'absent': ('permId',)}] for _ in range(3)), identity={'perm_id': 77})
        self.assertEqual(result.outcome, Outcome.PARTIAL_IDENTITY_COVERAGE)
        self.assertEqual(result.reasons, ())
        for c in self.correlations(result):
            self.assertEqual(c.matches, ()); self.assertEqual(len(c.unresolved_perm_id), 1)
            self.assertEqual(c.coverage, Coverage.PERMID_UNRESOLVED)

    def test_unrelated_perm_facts_are_not_in_consistency_set(self):
        result = self.scenario(([{}, {'values': {'orderId': 99, 'permId': 88}}], [{}], [{}]))
        self.assertEqual(result.reasons, ())
        self.assertEqual({f.perm_id for f in result.known_perm_id_facts}, {77})

    def test_ambiguity_precedes_new_identity_conflict_without_hiding_reasons(self):
        result = self.scenario(([{}], [{'values': {'permId': 88}}], [{}]),
                               statuses=({'permId': 77}, {'permId': 88}), identity={'perm_id': 77})
        self.assertEqual(result.outcome, Outcome.DURABLE_IDENTITY_AMBIGUOUS)
        self.assertEqual(result.reasons, (Reason.DURABLE_IDENTITY_AMBIGUITY,
            Reason.REQUESTED_PERMID_CONFLICT, Reason.KNOWN_PERMID_CONFLICT))


class IssuerTests(Fixture):
    def test_constructor_and_alternate_inputs_not_authority(self):
        self.invalid(Owner)
        for candidate in (None, {}, object(), self.root, self.token, self.journal, self.preparation):
            self.invalid(m._create_cross_source_identity_evidence_owner, candidate)
        self.assertEqual(list(inspect.signature(Owner._evaluate).parameters), ['self', 'pre5_result'])

    def test_reconstructed_pre5_owner_is_not_registered(self):
        clone = object.__new__(type(self.combiner))
        for slot in type(self.combiner).__slots__:
            name = '_PreparedDecisionEvidenceOwner' + slot
            object.__setattr__(clone, name, getattr(self.combiner, name))
        self.invalid(m._create_cross_source_identity_evidence_owner, clone)

    def test_no_second_registered_owner(self):
        self.invalid(m._create_cross_source_identity_evidence_owner, self.combiner)
        self.assertIs(self.transport._cross_source_identity_evidence_owner, self.cross)

    def test_no_pre5_result_yet_is_rejected(self):
        self.change(self.combiner, '_PreparedDecisionEvidenceOwner__result', None)
        self.change(self.transport, '_cross_source_identity_evidence_owner', None)
        self.invalid(m._create_cross_source_identity_evidence_owner, self.combiner)

    def test_reconstructed_pre5_result_not_authority(self):
        clone = replace(self.root)
        self.assertEqual(clone, self.root)
        self.invalid(self.cross._evaluate, clone)

    def test_new_pre5_publication_supersedes_bound_root(self):
        result = self.correlate()
        newer = self.evaluate()
        self.assertEqual(newer, self.root); self.assertIsNot(newer, self.root)
        self.invalid(self.cross._require_evidence, result)
        self.invalid(self.cross._evaluate, newer)

    def test_reconstructed_or_pickled_pre6_result_not_issuer_evidence(self):
        result = self.correlate()
        for clone in (replace(result), pickle.loads(pickle.dumps(result))):
            self.assertEqual(clone, result)
            self.invalid(self.cross._require_evidence, clone)
        self.assertIs(self.cross._require_evidence(result), result)

    def test_foreign_runtime_result_rejected(self):
        result = self.correlate()
        self.scenario()
        self.invalid(self.cross._require_evidence, result)

    def test_wrong_thread_rejected_before_pre5_or_live_locks(self):
        result = self.correlate()
        with ThreadPoolExecutor(max_workers=1) as pool:
            for action, args in ((self.cross._evaluate, (self.root,)),
                                 (self.cross._require_evidence, (result,))):
                with self.assertRaises(ReadOnlyError) as caught: pool.submit(action, *args).result(timeout=5)
                self.assertEqual(caught.exception.code, m._ERROR)
        self.assertIs(self.cross._require_evidence(result), result)

    def test_replaced_pre5_method_is_rejected(self):
        with patch.object(type(self.combiner), '_require_evidence', side_effect=AssertionError('substituted')):
            self.invalid(self.correlate)

    def test_original_store_corruption_still_fails_pre5_validation(self):
        result = self.correlate()
        self.corrupt_digest(self.boundary.committed.record.sequence)
        self.invalid(self.cross._require_evidence, result)
        self.assertIsNone(self.cross._result)

    def test_equal_reconstructed_pre6_owner_cannot_replace_registered_owner(self):
        result = self.correlate(); clone = object.__new__(Owner)
        for slot in Owner.__slots__:
            name = PREFIX + slot
            object.__setattr__(clone, name, getattr(self.cross, name))
        self.invalid(clone._require_evidence, result)

    def test_equal_reconstructed_observation_in_derived_tuple_rejected(self):
        result = self.correlate()
        self.change(result.open_orders, 'candidates', (replace(result.open_orders.candidates[0]),))
        self.invalid(self.cross._require_evidence, result)

    def test_equal_new_matches_tuple_rejected_even_with_original_observations(self):
        result = self.correlate()
        self.change(result.open_orders, 'matches', tuple(list(result.open_orders.matches)))
        self.invalid(self.cross._require_evidence, result)


class NoAuthorityTests(Fixture):
    def test_forbidden_side_effects_and_clocks(self):
        with ExitStack() as stack:
            for cls in (self.api.Client, self.paper_api.Client):
                for name in ('reqAllOpenOrders', 'reqExecutions', 'reqCompletedOrders', 'placeOrder', 'cancelOrder'):
                    if hasattr(cls, name): stack.enter_context(patch.object(cls, name, side_effect=AssertionError(name)))
            for source in self.sources:
                stack.enter_context(patch.object(type(source), '_consume_receipt', side_effect=AssertionError('consume')))
            # Captured original method identity is part of preparation provenance;
            # use the already-captured validator rather than replacing source
            # class methods during the actual successful path below.
            self.invalid(self.correlate)
        with ExitStack() as stack:
            for source in self.sources:
                stack.enter_context(patch.object(type(source), '_authenticate', side_effect=AssertionError('auth')))
            for cls, names in ((type(self.preparation), ('_prepare',)),
                    (type(self.journal), ('_certify_decision_boundary', '_append_event', '_drain_callbacks')),
                    (type(self.journal._store), ('_append',))):
                for name in names: stack.enter_context(patch.object(cls, name, side_effect=AssertionError(name)))
            for cls in (self.api.Client, self.paper_api.Client):
                for name in ('reqAllOpenOrders', 'reqExecutions', 'reqCompletedOrders', 'placeOrder', 'cancelOrder'):
                    if hasattr(cls, name): stack.enter_context(patch.object(cls, name, side_effect=AssertionError(name)))
            for module in (accounting, lifecycle, recovery):
                for name in dir(module):
                    value = getattr(module, name)
                    if inspect.isfunction(value) and value.__module__ == module.__name__:
                        stack.enter_context(patch.object(module, name, side_effect=AssertionError(name)))
            stack.enter_context(patch('time.time', side_effect=AssertionError('wall clock')))
            stack.enter_context(patch('time.monotonic', side_effect=AssertionError('monotonic')))
            stack.enter_context(patch.object(journal, '_decision_utc_now', side_effect=AssertionError('clock')))
            result = self.correlate()
            self.assertIs(self.cross._require_evidence(result), result)
        self.assertEqual(self.safety(), self.before)
        self.assertEqual(self.consumed(), (True, True, True))

    def test_no_sqlite_data_or_schema_writes(self):
        connection = self.journal._store._connection
        import sqlite3
        writes = {getattr(sqlite3, name) for name in ('SQLITE_INSERT', 'SQLITE_UPDATE', 'SQLITE_DELETE',
            'SQLITE_CREATE_TABLE', 'SQLITE_DROP_TABLE', 'SQLITE_CREATE_TRIGGER', 'SQLITE_DROP_TRIGGER',
            'SQLITE_ALTER_TABLE', 'SQLITE_CREATE_INDEX', 'SQLITE_DROP_INDEX')}
        seen = []
        def authorize(action, *args):
            if action in writes: seen.append(action); return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        connection.set_authorizer(authorize)
        try:
            result = self.correlate()
            self.assertIs(self.cross._require_evidence(result), result)
        finally: connection.set_authorizer(None)
        self.assertEqual(seen, [])

    def test_no_authority_or_public_export_or_codec_registration(self):
        result = self.correlate()
        self.assertEqual(m.__all__, ())
        self.assertEqual([n for n in dir(Owner) if not n.startswith('_')], [])
        self.assertNotIn(Result, storage._MODELS)
        with self.assertRaises(ReadOnlyError): storage._encode(result)
        self.assertTrue(result.decision_status.prefix.replay.restart_quarantine_required)
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.transport._snapshot.account.mode)
        self.assertFalse(self.broker.controls.trading_enabled)
        self.assertEqual(self.write_calls(), [])
        self.assertEqual(self.safety(), self.before)

    def test_ast_prohibits_attempts_arithmetic_clocks_io_and_source_ranking(self):
        tree = ast.parse(Path(m.__file__).read_text())
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertTrue({'exec_id', 'shares', 'quantity', 'filled', 'remaining', 'symbol', 'con_id',
            'order_ref', 'placing_client_id', 'attempts', 'client_order_id', 'price', 'action'} .isdisjoint(attrs))
        calls = {n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id if isinstance(n.func, ast.Name) else ''
                 for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertTrue({'sum', 'max', 'min', 'sorted', 'open', 'connect', 'execute', '_append', '_append_event',
            '_prepare', '_consume_receipt', '_certify_decision_boundary', '_drain_callbacks',
            'now', 'time', 'monotonic', 'reqAllOpenOrders', 'reqExecutions', 'reqCompletedOrders',
            'placeOrder', 'cancelOrder'} .isdisjoint(calls))
        imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        self.assertFalse(any(v and ('accounting' in v or 'working_order' in v or 'recovery' in v) for v in imports))
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, ast.BinOp)], [])


def _source_matrix(index, case):
    def test(self):
        spec, identity = {}, {}
        if case == 'other_order': spec = {'values': {'orderId': 11}}
        elif case == 'other_client': spec = {'values': {'clientId': 3}}
        elif case == 'missing_order': spec = {'absent': ('orderId',), 'top': False}
        elif case == 'missing_client': spec = {'absent': ('clientId',)}
        elif case == 'perm_match': identity = {'perm_id': 77}
        elif case == 'perm_conflict': identity, spec = {'perm_id': 77}, {'values': {'permId': 88}}
        elif case == 'perm_missing': identity, spec = {'perm_id': 77}, {'absent': ('permId',)}
        elif case == 'perm_unavailable': identity, spec = {'perm_id': 77}, {'values': {'permId': 0}}
        elif case == 'missing_unqualified_perm': spec = {'absent': ('permId',)}
        elif case == 'legacy': spec = {'encoding': 'LEGACY'}
        specs = [[{}], [{}], [{}]]; specs[index] = [spec]
        result = self.scenario(specs, identity=identity)
        c = self.correlations(result)[index]
        unrelated = case in ('other_order', 'other_client', 'missing_order', 'missing_client') or case == 'legacy' and index == 2
        if unrelated:
            self.assertEqual(c.candidates, ())
        else:
            self.assertIs(c.candidates[0], self.receipts[index].observations[0])
            if case == 'perm_conflict':
                self.assertEqual(len(c.conflicts), 1)
                self.assertIn(Reason.REQUESTED_PERMID_CONFLICT, result.reasons)
            elif case in ('perm_missing', 'perm_unavailable'):
                self.assertEqual(len(c.unresolved_perm_id), 1)
                self.assertEqual(result.reasons, ())
            else: self.assertEqual(len(c.matches), 1)
    return test


for _index, _label in enumerate(('open', 'execution', 'completed')):
    for _case in ('known', 'other_order', 'other_client', 'missing_order', 'missing_client',
                  'perm_match', 'perm_conflict', 'perm_missing', 'perm_unavailable', 'missing_unqualified_perm', 'legacy'):
        setattr(SuccessTests, f'test_{_label}_{_case}', _source_matrix(_index, _case))


def _modification(field, value):
    def test(self):
        result = self.scenario(([{}], [{}], [{'values': {field: value}, 'status': 'Cancelled'}]))
        self.assertEqual(result.outcome, Outcome.CORRELATED_NO_PROVEN_IDENTITY_CONTRADICTION)
        self.assertEqual(result.reasons, ())
        self.assertIs(result.completed_orders.matches[0], self.receipts[2].observations[0])
    return test


for _field, _value in (('totalQuantity', Decimal('41.00')), ('orderType', 'LMT'), ('tif', 'GTC'),
                       ('action', 'SELL'), ('orderRef', 'different-reference')):
    setattr(ConflictTests, 'test_modification_not_identity_conflict_' + _field, _modification(_field, _value))


def _status_difference(value):
    def test(self):
        result = self.scenario(([{'status': 'Submitted'}], [{}], [{'status': value}]), statuses=({'status': 'PreSubmitted'},))
        self.assertEqual(result.reasons, ())
        self.assertEqual(result.outcome, Outcome.CORRELATED_NO_PROVEN_IDENTITY_CONTRADICTION)
        self.assertEqual(result.completed_orders.matches[0].status, value)
    return test


for _value in ('PendingSubmit', 'PreSubmitted', 'Submitted', 'Filled', 'Cancelled', 'ApiCancelled', 'Inactive', 'Expired', 'UNKNOWN'):
    setattr(ConflictTests, 'test_status_difference_' + _value, _status_difference(_value))


def _tamper(field):
    def test(self):
        result = self.correlate()
        value = getattr(result, field)
        if field in ('open_orders', 'executions', 'completed_orders'): changed = replace(value)
        elif field in ('identity', 'decision_status', 'pre5'): changed = replace(value)
        elif field == 'known_perm_id_facts': changed = tuple(replace(f) for f in value)
        elif field == 'limitations': changed = tuple(list(value))
        else: changed = None
        self.change(result, field, changed)
        self.invalid(self.cross._require_evidence, result)
    return test


for _field in (f.name for f in fields(Result)):
    setattr(IssuerTests, 'test_changed_result_' + _field, _tamper(_field))


def _source_reference_tamper(index, field):
    def test(self):
        result = self.correlate(); receipt = self.receipts[index]
        event = receipt.observations[0]
        if field == 'observations': self.change(receipt, field, tuple(replace(e) for e in receipt.observations))
        elif field == 'presence': self.change(event, field, replace(event.presence))
        else: self.change(event, field, getattr(event, field) + 1)
        self.invalid(self.cross._require_evidence, result)
        self.invalid(self.correlate)
    return test


for _index in range(3):
    for _field in ('observations', 'presence', 'order_id', 'client_id', 'perm_id'):
        setattr(IssuerTests, f'test_source_{_index}_tamper_{_field}', _source_reference_tamper(_index, _field))


def _interruption(seam, kind, validating=False):
    def test(self):
        result = self.correlate()
        error = kind(83) if kind is SystemExit else kind('private interruption')
        target = Owner if seam != '_require_evidence' else type(self.combiner)
        # Captured original PRE5 validator identity must still match its class.
        if seam == '_require_evidence':
            original = type(self.combiner)._require_evidence
            def raising(owner, value): raise error
            self.change(self.cross, PREFIX + '__require_evidence', raising.__get__(self.combiner))
            context = patch.object(type(self.combiner), seam, raising)
        else: context = patch.object(target, seam, side_effect=error)
        with context:
            with self.assertRaises(kind) as caught:
                if validating: self.cross._require_evidence(result)
                else: self.correlate()
        self.assertIs(caught.exception, error)
        if kind is SystemExit: self.assertEqual(caught.exception.code, 83)
        self.assertIsNone(self.cross._result)
        self.assertFalse(getattr(self.cross, PREFIX + '__evaluating'))
        self.assertEqual(self.safety(), self.before)
    return test


class FailureTests(Fixture):
    def test_ordinary_publication_failure_sanitized_and_repeatable(self):
        with patch.object(Owner, '_publish', side_effect=ValueError('private details')):
            self.invalid(self.correlate)
        self.assertIsNone(self.cross._result)
        self.assertIs(self.cross._require_evidence(self.correlate()), self.cross._result)

    def test_publication_substitution_rejected(self):
        with patch.object(Owner, '_publish', side_effect=lambda owner_result: replace(owner_result)):
            self.invalid(self.correlate)
        self.assertIsNone(self.cross._result)

    def test_publication_tampering_cannot_pass_new_snapshot(self):
        def altered(result):
            object.__setattr__(result.open_orders, 'matches', ())
            return result
        with patch.object(Owner, '_publish', side_effect=altered): self.invalid(self.correlate)
        self.assertIsNone(self.cross._result)

    def test_reentry_cannot_publish_partial_result(self):
        with patch.object(Owner, '_publish', side_effect=lambda result: self.correlate()):
            self.invalid(self.correlate)
        self.assertIsNone(self.cross._result)


for _kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    for _seam in ('_validate_root', '_publish', '_set', '_require_evidence'):
        setattr(FailureTests, f'test_{_kind.__name__}_{_seam}', _interruption(_seam, _kind))
    setattr(FailureTests, f'test_{_kind.__name__}_during_issuer_revalidation',
            _interruption('_validate_root', _kind, validating=True))


def _single_source(index):
    def test(self):
        specs = [[], [], []]; specs[index] = [{}]
        result = self.scenario(specs)
        self.assertEqual(result.outcome, Outcome.PARTIAL_IDENTITY_COVERAGE)
        self.assertEqual([bool(c.candidates) for c in self.correlations(result)], [i == index for i in range(3)])
    return test


for _index in range(3): setattr(SuccessTests, f'test_only_source_{_index}', _single_source(_index))


def _numeric_pair(order_id, client_id):
    def test(self):
        specs = tuple([{'values': {'orderId': order_id, 'clientId': client_id}}] for _ in range(3))
        result = self.scenario(specs, statuses=({'orderId': order_id, 'clientId': client_id},),
                               identity={'order_id': order_id, 'client_id': client_id})
        self.assertEqual(result.outcome, Outcome.CORRELATED_NO_PROVEN_IDENTITY_CONTRADICTION)
        for c in self.correlations(result):
            self.assertEqual((c.matches[0].order_id, c.matches[0].client_id), (order_id, client_id))
            self.assertTrue(c.matches[0].presence.order_id)
            self.assertTrue(c.matches[0].presence.client_id)
    return test


for _order, _client in ((0, 0), (0, 2), (10, 0), (2147483647, 2147483647)):
    setattr(SuccessTests, f'test_supplied_pair_{_order}_{_client}', _numeric_pair(_order, _client))


def _status_identity_presence(field, value):
    def test(self):
        result = self.scenario(statuses=({field: value},))
        self.assertIs(result.decision_status.observation, self.root.status.observation)
        if field == 'permId':
            expected = Outcome.IDENTITY_CONTRADICTION if value == 0 else Outcome.CORRELATED_NO_PROVEN_IDENTITY_CONTRADICTION
            self.assertEqual(result.outcome, expected)
        else:
            self.assertIs(result.decision_status.outcome, status._DecisionStatusOutcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)
        self.assertIs(result.decision_status, self.root.status)
    return test


for _field, _value in (('permId', None), ('permId', 0), ('clientId', None), ('orderId', None)):
    setattr(SuccessTests, f'test_pre4_presence_{_field}_{_value}', _status_identity_presence(_field, _value))


def _lifetime_change(component):
    def test(self):
        result = self.correlate()
        obj, name, value = {
            'association': (self.transport, '_journal_account_association', replace(self.transport._journal_account_association)),
            'read_generation': (self.transport, '_generation', self.transport._generation + 1),
            'write_generation': (self.write, '_generation', self.write._generation + 1),
            'read_generation_bool': (self.transport, '_generation', True),
            'write_generation_bool': (self.write, '_generation', True),
            'read_client': (self.transport, '_client', object()), 'write_client': (self.write, '_client', object()),
            'read_configuration': (self.transport, 'config', replace(self.transport.config)),
            'write_configuration': (self.write, '_config', replace(self.write._config)),
            'read_disconnect': (self.transport, '_disconnect_emitted', True),
            'write_closed': (self.write, '_closed', True),
            'token_deadline': (self.token, 'authentication_deadline', self.token.authentication_deadline + timedelta(days=1)),
            'token_session': (self.token, 'session_id', 'session-' + '9' * 32),
            'pre5_registration': (self.transport, '_prepared_decision_evidence_owner', object()),
            'pre6_registration': (self.transport, '_cross_source_identity_evidence_owner', object()),
        }[component]
        self.change(obj, name, value)
        self.invalid(self.cross._require_evidence, result)
        self.invalid(self.correlate)
    return test


for _component in ('association', 'read_generation', 'write_generation', 'read_generation_bool',
        'write_generation_bool', 'read_client', 'write_client', 'read_configuration', 'write_configuration',
        'read_disconnect', 'write_closed', 'token_deadline', 'token_session', 'pre5_registration', 'pre6_registration'):
    setattr(IssuerTests, 'test_lifetime_mismatch_' + _component, _lifetime_change(_component))


def _receipt_substitution(field):
    def test(self):
        result = self.correlate()
        self.change(self.token, field, replace(getattr(self.token, field)))
        self.invalid(self.cross._require_evidence, result)
        self.invalid(self.correlate)
    return test


for _field in ('open_order_receipt', 'execution_receipt', 'completed_order_receipt'):
    setattr(IssuerTests, 'test_reconstructed_receipt_' + _field, _receipt_substitution(_field))


def _after_publication_interruption(kind):
    def test(self):
        original = Owner._set
        error = kind(83) if kind is SystemExit else kind('publication interrupted')
        def assign(owner, name, value):
            original(owner, name, value)
            if name == 'result': raise error
        with patch.object(Owner, '_set', assign):
            with self.assertRaises(kind) as caught: self.correlate()
        self.assertIs(caught.exception, error)
        self.assertIsNone(self.cross._result)
        self.assertFalse(getattr(self.cross, PREFIX + '__evaluating'))
        self.assertIs(self.cross._require_evidence(self.correlate()), self.cross._result)
    return test


for _kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    setattr(FailureTests, 'test_after_final_assignment_' + _kind.__name__, _after_publication_interruption(_kind))


if __name__ == '__main__': unittest.main()
