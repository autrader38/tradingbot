"""PRE5 uses real temporary durable loads and anonymous SDKs, never a Gateway."""

import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import inspect
from pathlib import Path
import pickle
import sqlite3
import unittest
from unittest.mock import patch

from tradingbot_broker import paper_prepared_decision_evidence as m
from tradingbot_broker import paper_fresh_collection_bundle as prep
from tradingbot_broker import paper_decision_status_evidence as status
from tradingbot_broker import paper_dispatch_journal as p
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker import paper_execution_journal_sqlite as s
from tradingbot_broker import ibkr_paper_transport as paper
from tradingbot_broker import ibkr_order_status_pairing as pairing
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests.test_paper_fresh_collection_bundle import Fixture as CollectionFixture
from tests import test_ibkr_paper_status_delivery as sdk
from tests.dispatch_journal_fixtures import AT


Owner = m._PreparedDecisionEvidenceOwner
Result = m._PreparedDecisionStatusEvidence
Outcome = status._DecisionStatusOutcome
PREFIX = '_PreparedDecisionEvidenceOwner'


class Fixture(CollectionFixture):
    def build(self, **kwargs):
        api = sdk.lifecycle_api()
        original = paper._make_paper_client
        def connected(api_value, transport, generation):
            view = original(api_value, transport, generation)
            api_value.clients[-1].connect()  # Fake local lifecycle only, no SDK Connection.
            return view
        with patch.object(pairing, '_load_pairing_sdk', return_value=(sdk.Decoder, sdk.Incoming)), \
                patch.object(paper, '_make_paper_client', side_effect=connected):
            result = super().build(paper_api=api, **kwargs)
        if getattr(self, 'boundary_before_collections', False):
            self.coordinator._broker_state_reconciliation_required = True
            self.before_collection_boundary = self.certify(AT + timedelta(seconds=1))
        return result

    def setUp(self):
        super().setUp()
        self.prepare()
        self.qualified()
        self.boundary = self.certify()
        self.combiner = m._create_prepared_decision_evidence_owner(self.preparation)
        self.before = self.safety()

    def prepare(self):
        self.preparation = self.owner()
        self.token = self.preparation._prepare()

    def qualified(self, **changes):
        raw = {name: value for name, value in dict(sdk.RAW, **changes).items() if value is not None}
        self.client.decoder.processProtoBuf(sdk.Proto(**raw).SerializeToString(), 37)

    def certify(self, at=None):
        with patch.object(p, '_decision_utc_now', return_value=AT + timedelta(seconds=1) if at is None else at):
            return self.journal._certify_decision_boundary()

    def identity(self, **changes):
        return replace(status._QualifiedStatusIdentity(self.token.session_id, self.token.write_generation, 10, 2), **changes)

    def evaluate(self, *, sequence=None, identity=None):
        return self.combiner._evaluate(self.token,
            self.boundary.committed.record.sequence if sequence is None else sequence,
            self.identity() if identity is None else identity)

    def invalid(self, function, *args, **kwargs):
        with self.assertRaises(ReadOnlyError) as caught: function(*args, **kwargs)
        self.assertEqual(caught.exception.code, m._ERROR)

    def append_contract_fixture(self, event):
        # Syntactic fixture only, NOT runtime certification. Deliberately invalid
        # producer-time facts exercise the stricter PRE5 deadline relation.
        committed = self.journal._store._append(event)
        old = self.journal._PaperDispatchJournalOwner__history
        self.journal._PaperDispatchJournalOwner__history = old + (p._values(committed.record),)
        return committed.record.sequence

    def corrupt_digest(self, sequence):
        # Anonymous corruption fixture; restore the exact immutable-table trigger
        # so the original hash inspector, rather than a schema mismatch, rejects it.
        connection = self.journal._store._connection
        definition = connection.execute("SELECT sql FROM sqlite_schema WHERE name='records_no_update'").fetchone()[0]
        connection.execute('DROP TRIGGER records_no_update')
        try:
            connection.execute('UPDATE records SET digest=? WHERE sequence=?', (b'x' * 32, sequence))
        finally:
            connection.execute(definition)


class SuccessTests(Fixture):
    def test_supported_exact_sources_and_durable_boundary(self):
        result = self.evaluate()
        self.assertIs(result.prepared, self.token)
        self.assertIs(result.status.outcome, Outcome.SUPPORTED_AT_DECISION_BOUNDARY)
        self.assertEqual(result.boundary_record_sequence, self.boundary.committed.record.sequence)
        self.assertEqual(result.status.decision_at, self.boundary.event.decision_at)
        self.assertEqual(result.status.through_sequence, 1)
        self.assertEqual(result.status.selected_sequence, 1)
        self.assertLess(result.status.decision_at, result.authentication_deadline)
        self.assertIs(self.combiner._require_evidence(result), result)
        self.assertEqual(self.consumed(), (True, True, True))
        self.assertEqual(self.safety(), self.before)

    def test_exact_issuer_path_and_original_durable_load_are_used(self):
        root = self.preparation
        require = type(root)._require_prepared
        load = type(self.journal)._load
        calls = []
        def checked(owner, token):
            calls.append(('issuer', owner is root, token is self.token))
            return require(owner, token)
        def loaded(owner):
            calls.append(('load', owner is self.journal))
            return load(owner)
        # New runtime registry solely for this instrumented test. Existing owner
        # method identity must not be swapped after registration.
        self.change(self.transport, '_prepared_decision_evidence_owner', None)
        with patch.object(type(root), '_require_prepared', checked), patch.object(type(self.journal), '_load', loaded):
            combiner = m._create_prepared_decision_evidence_owner(root)
            result = combiner._evaluate(self.token, self.boundary.committed.record.sequence, self.identity())
            self.assertIs(result.prepared, self.token)
        self.assertTrue(any(c[0] == 'load' and c[1] for c in calls))
        self.assertTrue(all(all(c[1:]) for c in calls))

    def test_pre4_called_with_exact_loaded_boundary_reference(self):
        evaluate = status._evaluate_decision_status
        seen = []
        def checked(companion, boundary, identity):
            self.assertIs(boundary, companion.records[boundary.sequence - 1])
            self.assertTrue(any(r is boundary for r in companion.boundary_records))
            evidence = evaluate(companion, boundary, identity)
            seen.append((boundary, identity, evidence))
            return evidence
        with patch.object(status, '_evaluate_decision_status', checked): result = self.evaluate()
        self.assertEqual(len(seen), 1)
        self.assertIs(result.status, seen[0][2]); self.assertIs(result.status.boundary, seen[0][0])

    def test_no_qualified_status_is_valid_combined_provenance(self):
        result = self.evaluate(identity=self.identity(order_id=99))
        self.assertIs(result.status.outcome, Outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)
        self.assertIsNone(result.status.observation)
        self.assertIs(self.combiner._require_evidence(result), result)

    def test_ambiguous_identity_is_valid_combined_provenance(self):
        self.qualified(permId=88); self.boundary = self.certify(AT + timedelta(seconds=2))
        result = self.evaluate()
        self.assertIs(result.status.outcome, Outcome.AMBIGUOUS_IDENTITY)
        self.assertIsNone(result.status.observation)
        self.assertIs(self.combiner._require_evidence(result), result)

    def test_observed_client_different_from_placing_client_is_not_ownership(self):
        self.qualified(clientId=5); self.boundary = self.certify(AT + timedelta(seconds=2))
        result = self.evaluate(identity=self.identity(client_id=5))
        self.assertIs(result.status.outcome, Outcome.SUPPORTED_AT_DECISION_BOUNDARY)
        self.assertNotEqual(result.status.identity.client_id, self.token.placing_client_id)
        self.assertEqual(result.status.observation.client_id, 5)
        self.assertNotIn('owner', {f.name for f in fields(result)})

    def test_exact_earlier_boundary_not_latest(self):
        earlier_sequence = self.boundary.committed.record.sequence
        first = self.evaluate()
        self.qualified(status='Filled'); later = self.certify(AT + timedelta(seconds=2))
        again = self.evaluate(sequence=earlier_sequence)
        self.assertEqual(again, first)
        self.assertIsNone(first.status.next_status_sequence)
        newest = self.evaluate(sequence=later.committed.record.sequence)
        self.assertEqual(newest.status.observation.status, 'Filled')
        self.assertEqual(first.status.observation.status, 'Submitted')

    def test_bounded_latest_publication_rejects_prior_result_without_changing_it(self):
        first = self.evaluate(); snapshot = m._result_values(first)
        second = self.evaluate()
        self.assertEqual(first, second)
        self.assertEqual(m._result_values(first), snapshot)
        self.invalid(self.combiner._require_evidence, first)
        self.assertIs(self.combiner._require_evidence(second), second)

    def test_later_durable_history_keeps_current_issued_historical_result_valid(self):
        first = self.evaluate(); before = m._result_values(first)
        self.qualified(status='Cancelled'); self.certify(AT + timedelta(seconds=2))
        self.assertIs(self.combiner._require_evidence(first), first)
        self.assertEqual(m._result_values(first), before)

    def test_source_representation_is_attached_without_projection(self):
        self.qualified(filled='-0.00', remaining='10.000', avgFillPrice=-0.0, clientId=0)
        self.boundary = self.certify(AT + timedelta(seconds=2))
        result = self.evaluate(identity=self.identity(client_id=0))
        self.assertEqual(result.status.observation.filled.as_tuple(), Decimal('-0.00').as_tuple())
        self.assertEqual(result.status.observation.remaining.as_tuple(), Decimal('10.000').as_tuple())
        self.assertEqual(result.status.observation.protocol, j._QualifiedStatusProtocol.PROTOBUF)
        self.assertIs(result.status.observation.presence.client_id, j._QualifiedStatusSourceQuality.SUPPLIED)

    def test_empty_collections_remain_non_observation(self):
        self.collect_sources(counts=(0, 0, 0)); self.prepare()
        self.boundary = self.certify(); self.combiner = m._create_prepared_decision_evidence_owner(self.preparation)
        result = self.evaluate()
        self.assertEqual(tuple(r.count for r in self.receipts), (0, 0, 0))
        self.assertIs(result.status.outcome, Outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)
        self.assertIn('NO_EXECUTION_ABSENCE_PROOF', result.limitations)

    def test_superficially_contradictory_sources_are_not_resolved(self):
        self.collect_sources(encoding='PROTOBUF', contradictory=True); self.prepare()
        self.qualified(status='Cancelled'); self.boundary = self.certify()
        self.combiner = m._create_prepared_decision_evidence_owner(self.preparation)
        result = self.evaluate()
        self.assertEqual(result.prepared.open_order_receipt.observations[0].action, 'BUY')
        self.assertEqual(result.prepared.completed_order_receipt.observations[0].action, 'SELL')
        self.assertEqual(result.status.observation.status, 'Cancelled')
        self.assertIn('NO_CROSS_SOURCE_CONTRADICTION_POLICY', result.limitations)

    def test_no_wall_clock_requirement_after_original_deadline(self):
        self.transport._clock.now = self.token.authentication_deadline + timedelta(days=1)
        result = self.evaluate()
        self.assertLess(result.status.decision_at, self.token.authentication_deadline)
        self.assertIs(self.combiner._require_evidence(result), result)

    def test_fixed_individual_and_combined_limitations_remain(self):
        result = self.evaluate()
        self.assertEqual(result.limitations, m._LIMITATIONS)
        self.assertEqual(result.prepared.limitations, prep._LIMITATIONS)
        self.assertEqual(result.status.limitations, status._LIMITATIONS)
        for receipt, spec in zip(self.receipts, prep._SOURCES):
            self.assertEqual(receipt.coverage_limitations, spec[0]._LIMITATIONS)


class BoundaryTests(Fixture):
    def test_boundary_already_in_prepared_prefix_is_rejected(self):
        self.boundary_before_collections = True
        self.collect_sources(); self.prepare(); self.boundary_before_collections = False
        self.boundary = self.certify(AT + timedelta(seconds=2))
        self.combiner = m._create_prepared_decision_evidence_owner(self.preparation)
        self.invalid(self.evaluate, sequence=self.before_collection_boundary.committed.record.sequence)
        self.assertIsNone(self.combiner._result)
        self.assertEqual(self.evaluate().boundary_record_sequence, self.boundary.committed.record.sequence)

    def test_decision_one_microsecond_before_deadline_is_valid(self):
        boundary = self.certify(self.token.authentication_deadline - timedelta(microseconds=1))
        result = self.evaluate(sequence=boundary.committed.record.sequence)
        self.assertLess(result.status.decision_at, result.authentication_deadline)

    def test_decision_equal_deadline_contract_fixture_rejected(self):
        event = j._QualifiedDecisionBoundaryObserved(self.token.session_id, self.token.write_generation,
                                                    self.token.authentication_deadline, 1)
        sequence = self.append_contract_fixture(event)
        self.invalid(self.evaluate, sequence=sequence)
        self.assertIsNone(self.combiner._result)

    def test_decision_after_deadline_contract_fixture_rejected(self):
        event = j._QualifiedDecisionBoundaryObserved(self.token.session_id, self.token.write_generation,
                                                    self.token.authentication_deadline + timedelta(microseconds=1), 1)
        self.invalid(self.evaluate, sequence=self.append_contract_fixture(event))

    def test_selected_record_is_not_a_boundary(self):
        self.invalid(self.evaluate, sequence=2)  # Qualified status occurrence, not decision boundary.

    def test_current_history_tuple_rewrite_rejected(self):
        history = self.journal._PaperDispatchJournalOwner__history
        changed = tuple(tuple(list(v)) for v in history)
        self.change(self.journal, '_PaperDispatchJournalOwner__history', changed)
        self.invalid(self.evaluate)

    def test_prepared_prefix_tuple_reconstruction_rejected(self):
        prefix = self.preparation._FreshCollectionPreparationOwner__history
        self.change(self.preparation, '_FreshCollectionPreparationOwner__history', tuple(list(prefix)))
        self.invalid(self.evaluate)

    def test_load_is_outside_capture_locks_and_in_creator_thread(self):
        original = Owner._read
        def read(owner):
            for lock in (self.transport._lifecycle, self.transport._condition, self.write._lock, self.write._arrival_lock):
                self.assertFalse(lock._is_owned())
            self.assertTrue(self.coordinator._lock._is_owned())
            self.assertTrue(self.broker._lock._is_owned())
            return original(owner)
        with patch.object(Owner, '_read', read): self.evaluate()

    def test_active_attempt_forbids_storage_read(self):
        self.coordinator._active_attempt = object()
        with patch.object(Owner, '_read', side_effect=AssertionError('read during active attempt')):
            self.invalid(self.evaluate)

    def test_active_callback_drain_forbids_storage_read(self):
        self.journal._PaperDispatchJournalOwner__draining = True
        with patch.object(Owner, '_read', side_effect=AssertionError('read during active drain')):
            self.invalid(self.evaluate)

    def test_active_boundary_producer_forbids_storage_read(self):
        self.journal._PaperDispatchJournalOwner__decision_active = True
        with patch.object(Owner, '_read', side_effect=AssertionError('read during producer')):
            self.invalid(self.evaluate)

    def test_same_journal_other_registered_session_boundary_rejected(self):
        other = j._SessionRegistered('session-' + '9' * 32, 9, 33)
        self.append_contract_fixture(other)
        sequence = self.append_contract_fixture(j._QualifiedDecisionBoundaryObserved(
            other.session_id, other.write_generation, AT + timedelta(seconds=2), 0))
        self.invalid(self.evaluate, sequence=sequence)


class IssuerTests(Fixture):
    def test_constructor_cannot_mint_owner(self):
        self.invalid(Owner)

    def test_factory_accepts_only_registered_preparation_owner(self):
        for value in (None, object(), self.token, self.journal, self.transport):
            self.invalid(m._create_prepared_decision_evidence_owner, value)
        self.assertEqual(list(inspect.signature(m._create_prepared_decision_evidence_owner).parameters), ['preparation_owner'])

    def test_one_registered_combiner_per_lifetime(self):
        self.invalid(m._create_prepared_decision_evidence_owner, self.preparation)
        self.assertIs(self.transport._prepared_decision_evidence_owner, self.combiner)

    def test_no_arbitrary_replay_or_pre4_result_input(self):
        self.assertEqual(list(inspect.signature(Owner._evaluate).parameters),
                         ['self', 'token', 'boundary_record_sequence', 'identity'])
        result = self.evaluate()
        for value in (result.status, result.status.prefix, result.status.boundary, result.status.decision_at):
            self.invalid(self.combiner._evaluate, self.token, value, self.identity())

    def test_equal_prepared_token_reconstruction_rejected(self):
        self.invalid(self.combiner._evaluate, replace(self.token), self.boundary.committed.record.sequence, self.identity())

    def test_equal_combined_result_reconstruction_rejected(self):
        result = self.evaluate(); clone = replace(result)
        self.assertEqual(result, clone)
        self.invalid(self.combiner._require_evidence, clone)
        self.assertIs(self.combiner._require_evidence(result), result)

    def test_pickle_does_not_restore_issuer_authority(self):
        result = self.evaluate(); clone = pickle.loads(pickle.dumps(result))
        self.assertEqual(result, clone)
        self.invalid(self.combiner._require_evidence, clone)

    def test_foreign_runtime_token_and_result_rejected(self):
        old = self.evaluate(); combiner = self.combiner
        self.collect_sources(); self.prepare(); self.boundary = self.certify()
        self.combiner = m._create_prepared_decision_evidence_owner(self.preparation)
        self.invalid(self.combiner._evaluate, old.prepared, self.boundary.committed.record.sequence, self.identity())
        self.invalid(self.combiner._require_evidence, old)
        self.invalid(combiner._require_evidence, self.evaluate())

    def test_equal_preparation_owner_not_registered(self):
        clone = object.__new__(type(self.preparation))
        for slot in type(self.preparation).__slots__:
            name = '_FreshCollectionPreparationOwner' + slot
            object.__setattr__(clone, name, getattr(self.preparation, name))
        self.invalid(m._create_prepared_decision_evidence_owner, clone)

    def test_equal_combiner_not_registered(self):
        result = self.evaluate(); clone = object.__new__(Owner)
        for slot in Owner.__slots__:
            name = PREFIX + slot
            object.__setattr__(clone, name, getattr(self.combiner, name))
        self.invalid(clone._require_evidence, result)

    def test_wrong_thread_evaluation_never_reads_store(self):
        with patch.object(Owner, '_read', side_effect=AssertionError('wrong thread read')):
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.invalid(pool.submit(self.evaluate).result, timeout=5)

    def test_wrong_thread_result_validation_never_reads_store(self):
        result = self.evaluate()
        with patch.object(Owner, '_read', side_effect=AssertionError('wrong thread read')):
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.invalid(pool.submit(self.combiner._require_evidence, result).result, timeout=5)

    def test_wrong_thread_rejected_before_any_coordinator_lock(self):
        touched = []
        class ForbiddenLock:
            def __enter__(self):
                touched.append(True)
                raise AssertionError('wrong-thread lock acquisition')
            def __exit__(self, *_): return False
        self.change(self.coordinator, '_lock', ForbiddenLock())
        with ThreadPoolExecutor(max_workers=1) as pool:
            self.invalid(pool.submit(self.evaluate).result, timeout=5)
        self.assertEqual(touched, [])

    def test_frozen_slotted_opaque_models(self):
        result = self.evaluate()
        self.assertFalse(hasattr(result, '__dict__')); self.assertFalse(hasattr(self.combiner, '__dict__'))
        self.assertEqual(repr(result), '<PrivatePreparedDecisionStatusEvidence evidence-only>')
        with self.assertRaises(FrozenInstanceError): result.session_id = 'substituted'
        self.invalid(setattr, self.combiner, 'extra', None)
        self.invalid(delattr, self.combiner, '_result')

    def test_combined_result_has_no_journal_codec_registration(self):
        result = self.evaluate(); self.assertNotIn(Result, s._MODELS)
        with self.assertRaises(ReadOnlyError): s._encode(result)


class FailureTests(Fixture):
    def test_ordinary_load_failure_sanitized_no_result_and_repeatable(self):
        with patch.object(Owner, '_read', side_effect=RuntimeError('private storage diagnostic')):
            self.invalid(self.evaluate)
        self.assertIsNone(self.combiner._result)
        self.assertFalse(self.combiner._PreparedDecisionEvidenceOwner__reading)
        self.assertIs(self.evaluate().status.outcome, Outcome.SUPPORTED_AT_DECISION_BOUNDARY)

    def test_ordinary_pre4_failure_sanitized(self):
        with patch.object(status, '_evaluate_decision_status', side_effect=RuntimeError('private source diagnostic')):
            self.invalid(self.evaluate)
        self.assertIsNone(self.combiner._result)

    def test_publication_substitution_rejected_before_success(self):
        original = Owner._publish
        def publish(owner, result):
            object.__setattr__(result, 'status', replace(result.status))
            return original(owner, result)
        with patch.object(Owner, '_publish', publish): self.invalid(self.evaluate)
        self.assertIsNone(self.combiner._result)

    def test_reentrant_evaluation_rejected_without_fabricated_result(self):
        def read(owner):
            return owner._evaluate(self.token, self.boundary.committed.record.sequence, self.identity())
        with patch.object(Owner, '_read', read): self.invalid(self.evaluate)
        self.assertIsNone(self.combiner._result)
        self.assertFalse(self.combiner._PreparedDecisionEvidenceOwner__reading)

    def test_changed_load_method_not_accepted_as_authority(self):
        with patch.object(type(self.journal), '_load', side_effect=AssertionError('substitute load')):
            self.invalid(self.evaluate)

    def test_changed_preparation_validator_not_accepted_as_authority(self):
        with patch.object(type(self.preparation), '_require_prepared', return_value=self.token):
            self.invalid(self.evaluate)

    def test_storage_hash_corruption_rejected_by_original_load(self):
        self.corrupt_digest(self.boundary.committed.record.sequence)
        self.invalid(self.evaluate)
        self.assertIsNone(self.combiner._result)
        self.assertTrue(self.journal._store._poisoned)

    def test_future_corruption_invalidates_earlier_selected_result(self):
        old_sequence = self.boundary.committed.record.sequence
        self.qualified(status='Filled'); later = self.certify(AT + timedelta(seconds=2))
        self.corrupt_digest(later.committed.record.sequence)
        self.invalid(self.evaluate, sequence=old_sequence)

    def test_load_truncation_cannot_claim_selected_boundary(self):
        loaded = self.journal._load(); records = loaded.records[:1]
        truncated = s._LoadedJournal(loaded.header, records, j._replay_paper_execution_journal(loaded.header, records))
        with patch.object(Owner, '_read', return_value=truncated): self.invalid(self.evaluate)

    def test_self_consistent_foreign_journal_is_not_exact_runtime_load(self):
        loaded = self.journal._load(); header = replace(loaded.header, journal_id='journal-' + '9' * 32)
        records = tuple(replace(r, journal_id=header.journal_id) for r in loaded.records)
        foreign = s._LoadedJournal(header, records, j._replay_paper_execution_journal(header, records))
        with patch.object(Owner, '_read', return_value=foreign): self.invalid(self.evaluate)

    def test_impossible_boundary_frontier_replay_fails_without_future_repair(self):
        loaded = self.journal._load()
        wrong = replace(loaded.records[-1], payload=replace(loaded.records[-1].payload, through_sequence=2))
        # A load-shaped fixture cannot skip the actual V3 replay check.
        forged = object.__new__(s._LoadedJournal)
        for name, value in [('header', loaded.header), ('records', loaded.records[:-1] + (wrong,)), ('replay', loaded.replay)]:
            object.__setattr__(forged, name, value)
        with patch.object(Owner, '_read', return_value=forged): self.invalid(self.evaluate)

    def test_history_mutation_during_read_rejected_before_publication(self):
        original = Owner._read
        def read(owner):
            loaded = original(owner)
            self.journal._PaperDispatchJournalOwner__history = tuple(list(self.journal._PaperDispatchJournalOwner__history))
            return loaded
        with patch.object(Owner, '_read', read): self.invalid(self.evaluate)
        self.assertIsNone(self.combiner._result)

    def test_result_validation_reload_failure_clears_publication(self):
        result = self.evaluate()
        with patch.object(Owner, '_read', side_effect=RuntimeError('read failure')):
            self.invalid(self.combiner._require_evidence, result)
        self.assertIsNone(self.combiner._result)

    def test_changed_pre4_decision_timestamp_rejected_by_issuer_snapshot(self):
        result = self.evaluate()
        self.change(result.status.boundary.payload, 'decision_at', result.status.decision_at + timedelta(microseconds=1))
        self.invalid(self.combiner._require_evidence, result)

    def test_reconstructed_status_source_observation_reference_rejected(self):
        result = self.evaluate()
        event = result.status.selected_record.payload
        self.change(event, 'observation', replace(event.observation))
        self.invalid(self.combiner._require_evidence, result)

    def test_reconstructed_status_presence_reference_rejected(self):
        result = self.evaluate()
        obs = result.status.observation
        self.change(obs, 'presence', replace(obs.presence))
        self.invalid(self.combiner._require_evidence, result)


class NoAuthorityTests(Fixture):
    def test_no_requests_writes_clocks_reconsumption_or_new_boundary(self):
        # PRE5-PRE1 checks exact original consumer method identity. Keep that
        # invariant intact; success with already-consumed flags cannot reconsume.
        with ExitStack() as scope:
            for owner in self.sources:
                scope.enter_context(patch.object(type(owner), '_authenticate', side_effect=AssertionError('source consume/auth')))
            for name in ('reqAllOpenOrders', 'reqExecutions', 'reqCompletedOrders'):
                scope.enter_context(patch.object(self.api.Client, name, side_effect=AssertionError('broker request')))
            for name in ('placeOrder', 'cancelOrder'):
                scope.enter_context(patch.object(self.paper_api.Client, name, side_effect=AssertionError('order operation')))
            for name in ('_append_event', '_certify_decision_boundary', '_drain_callbacks'):
                scope.enter_context(patch.object(p._PaperDispatchJournalOwner, name, side_effect=AssertionError('mutation')))
            scope.enter_context(patch.object(s._SQLiteJournalStore, '_append', side_effect=AssertionError('SQLite write')))
            scope.enter_context(patch.object(p, '_decision_utc_now', side_effect=AssertionError('clock')))
            scope.enter_context(patch('time.time', side_effect=AssertionError('clock')))
            scope.enter_context(patch('time.monotonic', side_effect=AssertionError('clock')))
            result = self.evaluate()
            self.assertIs(self.combiner._require_evidence(result), result)
        self.assertEqual(self.safety(), self.before)
        self.assertEqual(self.consumed(), (True, True, True))

    def test_sqlite_authorizer_allows_reads_and_rejects_data_writes(self):
        writes = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                  sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE}
        def authorize(action, *_):
            self.assertNotIn(action, writes)
            return sqlite3.SQLITE_OK
        self.journal._store._connection.set_authorizer(authorize)
        try:
            result = self.evaluate(); self.combiner._require_evidence(result)
        finally:
            self.journal._store._connection.set_authorizer(None)

    def test_unchanged_quarantine_mode_and_controls(self):
        result = self.evaluate()
        self.assertTrue(result.status.prefix.replay.restart_quarantine_required)
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.transport._snapshot.account.mode)
        self.assertFalse(self.broker.controls.trading_enabled)
        self.assertEqual(self.write_calls(), [])
        self.assertEqual(self.safety(), self.before)

    def test_module_has_no_cross_source_observation_access_or_broker_calls(self):
        self.assertEqual(m.__all__, ())
        text = Path(m.__file__).read_text(); tree = ast.parse(text)
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertTrue({'observations', 'order_id', 'perm_id', 'exec_id', 'symbol', 'shares',
            'filled', 'remaining', 'price', 'action', 'quantity', 'contract'} .isdisjoint(attrs))
        calls = {n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id if isinstance(n.func, ast.Name) else ''
                 for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertTrue({'reqAllOpenOrders', 'reqExecutions', 'reqCompletedOrders', 'placeOrder', 'cancelOrder',
                        '_consume_receipt', '_prepare', '_certify_decision_boundary', '_drain_callbacks',
                        '_append', '_append_event', 'now', 'monotonic', 'time'} .isdisjoint(calls))
        self.assertEqual([n for n in dir(Owner) if not n.startswith('_')], [])
        self.assertEqual({f.name for f in fields(Result)}, {'prepared', 'status', 'boundary_record_sequence',
            'journal_id', 'account_binding', 'session_id', 'write_generation', 'authentication_deadline', 'limitations'})

    def test_changed_source_consumer_identity_cannot_bypass_preparation_issuer(self):
        with patch.object(type(self.sources[0]), '_consume_receipt', side_effect=AssertionError('source consume')):
            self.invalid(self.evaluate)
        self.assertEqual(self.consumed(), (True, True, True))


def _selector_failure(value, label):
    def test(self):
        self.invalid(self.combiner._evaluate, self.token, value, self.identity())
        self.assertIsNone(self.combiner._result)
    setattr(BoundaryTests, 'test_selector_' + label, test)


for _value, _label in ((None, 'none'), (True, 'bool'), (False, 'false'), (1.0, 'float'), (Decimal(1), 'decimal'),
        ('4', 'text'), (0, 'zero'), (-1, 'negative'), (1 << 63, 'oversized'), (99999, 'missing'), (AT, 'datetime')):
    _selector_failure(_value, _label)


def _identity_failure(field, value):
    def test(self):
        self.invalid(self.evaluate, identity=self.identity(**{field: value}))
    setattr(BoundaryTests, 'test_identity_mismatch_' + field, test)
_identity_failure('session_id', 'session-' + '9' * 32)
_identity_failure('write_generation', 99)


def _result_tamper(field):
    def test(self):
        result = self.evaluate(); original = getattr(result, field)
        value = replace(original) if field in ('prepared', 'status', 'account_binding') else \
                original + timedelta(seconds=1) if field == 'authentication_deadline' else \
                () if field == 'limitations' else original + 1 if type(original) is int else 'substituted'
        self.change(result, field, value)
        self.invalid(self.combiner._require_evidence, result)
    setattr(IssuerTests, 'test_result_tamper_' + field, test)
for _field in ('prepared', 'status', 'boundary_record_sequence', *m._SHARED, 'limitations'):
    _result_tamper(_field)


def _status_reference_tamper(field):
    def test(self):
        result = self.evaluate(); value = getattr(result.status, field)
        self.change(result.status, field, replace(value))
        self.invalid(self.combiner._require_evidence, result)
    setattr(IssuerTests, 'test_status_reference_tamper_' + field, test)
for _field in ('identity', 'boundary', 'prefix', 'selected_record'):
    _status_reference_tamper(_field)


def _interruption(seam, error_type, validating=False):
    def test(self):
        result = self.evaluate() if validating else None
        error = SystemExit(73) if error_type is SystemExit else error_type('original interruption')
        target, name = (status, '_evaluate_decision_status') if seam == 'pre4' else \
            (j, '_replay_v3_decision_boundary_evidence') if seam == 'replay' else (Owner, seam)
        with patch.object(target, name, side_effect=error):
            with self.assertRaises(error_type) as caught:
                self.combiner._require_evidence(result) if validating else self.evaluate()
        self.assertIs(caught.exception, error)
        if error_type is SystemExit: self.assertEqual(caught.exception.code, 73)
        self.assertIsNone(self.combiner._result)
        self.assertFalse(self.combiner._PreparedDecisionEvidenceOwner__reading)
        self.assertEqual(self.consumed(), (True, True, True))
    setattr(FailureTests, f'test_interrupt_{seam}_{error_type.__name__}_{validating}', test)


for _seam in ('_read', 'replay', 'pre4', '_publish'):
    for _error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
        _interruption(_seam, _error_type)
for _seam in ('_read', 'replay', 'pre4'):
    for _error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
        _interruption(_seam, _error_type, validating=True)


def _lifetime_change(component):
    def test(self):
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
            'token_journal': (self.token, 'journal_id', 'journal-' + '9' * 32),
            'token_account': (self.token, 'account_binding', j._JournalAccountBinding('binding-' + '9' * 32)),
            'token_session': (self.token, 'session_id', 'session-' + '9' * 32),
            'token_placing': (self.token, 'placing_client_id', self.token.placing_client_id + 1),
        }[component]
        self.change(obj, name, value)
        self.invalid(self.evaluate)
    setattr(IssuerTests, 'test_lifetime_mismatch_' + component, test)


for _component in ('association', 'read_generation', 'write_generation', 'read_generation_bool',
        'write_generation_bool', 'read_client', 'write_client',
        'read_configuration', 'write_configuration', 'read_disconnect', 'write_closed', 'token_deadline',
        'token_journal', 'token_account', 'token_session', 'token_placing'):
    _lifetime_change(_component)


def _source_form(form, value):
    def test(self):
        if form == 'status': changes = {'status': value}
        elif form == 'absent': changes = {value: None}
        else: changes = {'filled': value, 'remaining': '10.00'}
        self.qualified(**changes); self.boundary = self.certify(AT + timedelta(seconds=2))
        result = self.evaluate()
        self.assertIs(result.status.outcome, Outcome.SUPPORTED_AT_DECISION_BOUNDARY)
        self.assertIs(result.prepared, self.token)
        self.assertIs(self.combiner._require_evidence(result), result)
        if form == 'absent':
            field = {'avgFillPrice': 'average_fill_price', 'lastFillPrice': 'last_fill_price',
                     'permId': 'perm_id'}.get(value, value)
            self.assertIsNone(getattr(result.status.observation, field))
        if form == 'decimal':
            self.assertEqual(result.status.observation.filled.as_tuple(), Decimal(value).as_tuple())
    return test


for _status in ('PendingSubmit', 'PreSubmitted', 'Submitted', 'Filled', 'Cancelled', 'ApiCancelled',
                'Inactive', 'Expired', 'future-status'):
    setattr(SuccessTests, 'test_qualified_status_not_lifecycle_' + _status, _source_form('status', _status))
for _field in ('filled', 'remaining', 'avgFillPrice', 'lastFillPrice', 'permId'):
    setattr(SuccessTests, 'test_qualified_absence_preserved_' + _field, _source_form('absent', _field))
for _shape in ('0', '0.0', '-0', '-0.00', '1.00', '1e-4'):
    setattr(SuccessTests, 'test_qualified_decimal_shape_' + _shape, _source_form('decimal', _shape))


def _publication_interruption(error_type):
    def test(self):
        error = SystemExit(73) if error_type is SystemExit else error_type('publication interruption')
        original = Owner._set
        def setter(owner, name, value):
            if name == 'result': raise error
            return original(owner, name, value)
        with patch.object(Owner, '_set', setter):
            with self.assertRaises(error_type) as caught: self.evaluate()
        self.assertIs(caught.exception, error)
        self.assertIsNone(self.combiner._result)
        self.assertFalse(self.combiner._PreparedDecisionEvidenceOwner__reading)
    return test


for _kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    setattr(FailureTests, 'test_final_publication_' + _kind.__name__, _publication_interruption(_kind))


def _cleanup_interruption(error_type):
    def test(self):
        error = SystemExit(73) if error_type is SystemExit else error_type('primary interruption')
        original = Owner._clear
        calls = 0
        def clear(owner):
            nonlocal calls
            calls += 1
            if calls > 1: raise RuntimeError('fallible cleanup must not replace primary')
            return original(owner)
        with patch.object(Owner, '_read', side_effect=error), patch.object(Owner, '_clear', clear):
            with self.assertRaises(error_type) as caught: self.evaluate()
        self.assertIs(caught.exception, error)
        self.assertEqual(calls, 1)
        self.assertIsNone(self.combiner._result)
        self.assertFalse(self.combiner._PreparedDecisionEvidenceOwner__reading)
    return test


for _kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    setattr(FailureTests, 'test_cleanup_preserves_' + _kind.__name__, _cleanup_interruption(_kind))


if __name__ == '__main__': unittest.main()
