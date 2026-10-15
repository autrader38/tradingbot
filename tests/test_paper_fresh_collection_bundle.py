"""PRE5-PRE1: exact live consumers, anonymous SDKs, real network forbidden."""

import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from decimal import Decimal
import inspect
from pathlib import Path
import pickle
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tradingbot_broker import paper_fresh_collection_bundle as m
from tradingbot_broker import paper_fresh_open_order_collection as pre1
from tradingbot_broker import paper_fresh_execution_collection as pre2
from tradingbot_broker import paper_fresh_completed_order_collection as pre3
from tradingbot_broker import paper_dispatch_journal as journal
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker import paper_decision_status_evidence as status
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests.test_paper_dispatch import BridgeFixture
from tests.test_ibkr_reconciliation_evidence import CONTRACT, ORDER, EXECUTION, raw_proto
from tests.dispatch_journal_fixtures import AT


Owner = m._FreshCollectionPreparationOwner
Token = m._PreparedFreshCollectionEvidence
SOURCES = (pre1, pre2, pre3)
KINDS = tuple(s[1] for s in m._SOURCES)


class Fixture(BridgeFixture):
    def setUp(self):
        super().setUp()
        for name in ('socket.socket.connect', 'socket.socket.connect_ex',
                     'socket.create_connection', 'socket.getaddrinfo'):
            self.enterContext(patch(name, side_effect=AssertionError('network forbidden')))
        self.collect_sources()

    def collect_sources(self, *, counts=(1, 1, 1), encoding='LEGACY', contradictory=False):
        self.build()
        self.coordinator._broker_state_reconciliation_required = True
        wrapper = self.api.clients[-1].wrapper
        account = self.transport._accounts[0]
        def order(source):
            values = dict(ORDER)
            if contradictory and source == 'completed':
                values.update(totalQuantity=Decimal('41.00'), action='SELL')
            if encoding == 'PROTOBUF':
                raw = raw_proto(source, payload=values, contract=CONTRACT, status='Submitted',
                                **({'top': values['orderId']} if source == 'orders' else {}))
                getattr(wrapper, 'openOrderProtoBuf' if source == 'orders' else 'completedOrderProtoBuf')(raw)
            payload = NS(**values, account=account)
            if source == 'orders':
                wrapper.openOrder(values['orderId'], NS(**CONTRACT), payload, NS(status='Submitted'))
            else:
                wrapper.completedOrder(NS(**CONTRACT), payload, NS(status='Submitted'))
        def open_request(client):
            for _ in range(counts[0]): order('orders')
            wrapper.openOrderEnd()
        def execution_request(client, request_id, query_filter):
            for _ in range(counts[1]):
                values = dict(EXECUTION)
                if encoding == 'PROTOBUF':
                    wrapper.executionDetailsProtoBuf(raw_proto('executions', payload=values, contract=CONTRACT))
                wrapper.execDetails(request_id, NS(**CONTRACT), NS(**values, acctNumber=account))
            wrapper.execDetailsEnd(request_id)
        def completed_request(client, api_only):
            self.assertIs(api_only, False)
            for _ in range(counts[2]): order('completed')
            wrapper.completedOrdersEnd()
        with patch.object(self.api.Client, 'reqAllOpenOrders', open_request), \
                patch.object(self.api.Client, 'reqExecutions', execution_request), \
                patch.object(self.api.Client, 'reqCompletedOrders', completed_request):
            self.sources = []
            self.receipts = []
            for factory in (pre1._create_fresh_open_order_collection_owner,
                            pre2._create_fresh_execution_collection_owner,
                            pre3._create_fresh_completed_order_collection_owner):
                owner = factory(self.journal)
                self.sources.append(owner)
                self.receipts.append(owner._collect())
        self.sources = tuple(self.sources)
        self.receipts = tuple(self.receipts)
        self.before = self.safety()

    def safety(self):
        loaded = self.journal._load()
        return (loaded.records, loaded.replay, self.journal._persistence_cursor,
            self.coordinator._callback_cursor, self.write._callback_sequence, self.write._arrival_sequence,
            self.coordinator._broker_state_reconciliation_required, self.coordinator._reconciliation_required,
            self.broker._reconciliation_required, self.write._reconciliation_required,
            self.broker._paper_authority.status, self.transport._snapshot,
            self.transport._journal_account_association, tuple(self.api.calls), tuple(self.paper_api.calls))

    def owner(self):
        return m._create_fresh_collection_preparation_owner(self.journal)

    def consumed(self):
        return tuple(m._private(owner, kind, 'consumed') for owner, kind in zip(self.sources, KINDS))

    def change(self, obj, name, value):
        original = getattr(obj, name)
        object.__setattr__(obj, name, value)
        self.addCleanup(object.__setattr__, obj, name, original)

    def spies(self):
        calls = []
        for index, source in enumerate(self.sources):
            original = type(source)._consume_receipt
            def consume(owner, receipt, original=original, index=index):
                calls.append((index, owner, receipt))
                return original(owner, receipt)
            self.enterContext(patch.object(type(source), '_consume_receipt', consume))
        return calls

    def reject_before_consume(self, action):
        calls = self.spies()
        action()
        with self.assertRaises(ReadOnlyError): self.owner()
        self.assertEqual(calls, [])
        self.assertEqual(self.consumed(), (False, False, False))


class SuccessTests(Fixture):
    def test_exact_fixed_order_and_original_references(self):
        calls = self.spies(); owner = self.owner(); token = owner._prepare()
        self.assertEqual([(i, o is self.sources[i], r is self.receipts[i]) for i, o, r in calls],
                         [(0, True, True), (1, True, True), (2, True, True)])
        for name, receipt in zip(('open_order_receipt', 'execution_receipt', 'completed_order_receipt'), self.receipts):
            self.assertIs(getattr(token, name), receipt)
            self.assertIs(getattr(token, name).observations, receipt.observations)
        self.assertIs(owner._require_prepared(token), token)
        self.assertIs(owner._require_prepared(token), token)  # Validation is not another consume.
        self.assertEqual(self.consumed(), (True, True, True))
        self.assertEqual(self.safety(), self.before)

    def test_shared_provenance_exact_canonical_values(self):
        token = self.owner()._prepare()
        for name in m._SHARED:
            self.assertEqual(journal._values(getattr(token, name)), journal._values(getattr(self.receipts[0], name)))
        self.assertIs(token.authentication_deadline, self.receipts[0].authentication_deadline)
        self.assertIs(token.account_binding, self.receipts[0].account_binding)

    def test_empty_explicit_completed_collections(self):
        self.collect_sources(counts=(0, 0, 0)); token = self.owner()._prepare()
        self.assertEqual(tuple(r.count for r in (token.open_order_receipt, token.execution_receipt,
                                               token.completed_order_receipt)), (0, 0, 0))

    def test_duplicates_are_separate_and_not_merged(self):
        self.collect_sources(counts=(3, 2, 4)); token = self.owner()._prepare()
        self.assertEqual(tuple(r.count for r in self.receipts), (3, 2, 4))
        self.assertIs(token.open_order_receipt, self.receipts[0])
        self.assertEqual(self.safety(), self.before)

    def test_protobuf_presence_preserved_independently(self):
        self.collect_sources(encoding='PROTOBUF'); token = self.owner()._prepare()
        for receipt in self.receipts:
            self.assertEqual(receipt.observations[0].encoding, 'PROTOBUF')
            self.assertTrue(receipt.observations[0].presence.order_id)
        self.assertIs(token.execution_receipt.observations[0], self.receipts[1].observations[0])

    def test_superficially_conflicting_sources_are_not_interpreted(self):
        self.collect_sources(encoding='PROTOBUF', contradictory=True)
        token = self.owner()._prepare()
        self.assertEqual(token.open_order_receipt.observations[0].order_id,
                         token.completed_order_receipt.observations[0].order_id)
        self.assertEqual(token.open_order_receipt.observations[0].client_id,
                         token.completed_order_receipt.observations[0].client_id)
        self.assertEqual(token.completed_order_receipt.observations[0].action, 'SELL')
        self.assertEqual(token.limitations, m._LIMITATIONS)
        self.assertEqual(self.safety(), self.before)

    def test_no_global_sequence_or_marker_comparison(self):
        self.collect_sources(counts=(4, 0, 2)); token = self.owner()._prepare()
        self.assertEqual(tuple(r.final_sequence_watermark for r in self.receipts), (4, 0, 2))
        self.assertIs(token.execution_receipt, self.receipts[1])
        self.assertNotIn('sdk_request_id', {f.name for f in fields(token)})

    def test_individual_limitations_and_cross_source_limits_remain(self):
        token = self.owner()._prepare()
        for receipt, module in zip(self.receipts, SOURCES):
            self.assertEqual(receipt.coverage_limitations, module._LIMITATIONS)
        self.assertTrue({'INDEPENDENT_COLLECTION_INTERVALS', 'NO_CROSS_SOURCE_ATOMICITY',
                         'NO_CROSS_SOURCE_CORRELATION_YET', 'NO_DECISION_TIME_BINDING_YET'} <= set(token.limitations))

    def test_later_legitimate_boundary_append_does_not_reconsume(self):
        owner = self.owner(); token = owner._prepare()
        with patch.object(journal, '_decision_utc_now', return_value=AT + timedelta(seconds=1)):
            self.journal._certify_decision_boundary()  # Only a test of the intended later ordering.
        for source in self.sources:
            with patch.object(type(source), '_authenticate', side_effect=AssertionError('no stale receipt revalidation')):
                self.assertIs(owner._require_prepared(token), token)
        self.assertEqual(self.consumed(), (True, True, True))

    def test_journal_append_before_preparation_rejected(self):
        with patch.object(journal, '_decision_utc_now', return_value=AT + timedelta(seconds=1)):
            self.journal._certify_decision_boundary()
        with self.assertRaises(ReadOnlyError): self.owner()
        self.assertEqual(self.consumed(), (False, False, False))

    def test_deadline_remains_original_provenance_after_preparation(self):
        owner = self.owner(); token = owner._prepare()
        deadline = token.authentication_deadline
        self.transport._clock.now = deadline + timedelta(seconds=1)
        # Historical issuer validation does not renew a receipt or certify a
        # later decision's deadline. PRE5 must make that separate comparison.
        self.assertIs(owner._require_prepared(token), token)
        self.assertEqual(token.authentication_deadline, deadline)

    def test_full_source_occurrence_capacity_remains_bounded_and_independent(self):
        self.collect_sources(counts=(1024, 1024, 1024))
        token = self.owner()._prepare()
        self.assertEqual(tuple(r.count for r in self.receipts), (1024, 1024, 1024))
        self.assertIs(token.open_order_receipt.observations, self.receipts[0].observations)
        self.assertEqual(self.safety(), self.before)


class IdentityTests(Fixture):
    def test_owner_constructor_is_not_an_alternate_issuer(self):
        with self.assertRaises(ReadOnlyError): Owner()

    def test_factory_accepts_only_journal_no_receipts_or_owners(self):
        self.assertEqual(list(inspect.signature(m._create_fresh_collection_preparation_owner).parameters), ['journal'])
        for candidate in (None, object(), self.transport, self.sources[0]):
            with self.assertRaises(ReadOnlyError): m._create_fresh_collection_preparation_owner(candidate)

    def test_exact_registered_preparation_owner_one_lifetime(self):
        owner = self.owner()
        self.assertIs(self.transport._fresh_collection_preparation_owner, owner)
        with self.assertRaises(ReadOnlyError): self.owner()
        self.assertFalse(owner._failed)

    def test_receipts_must_already_exist(self):
        self.change(self.sources[1], KINDS[1] + '__receipt', None)
        with self.assertRaises(ReadOnlyError): self.owner()
        self.assertEqual(self.consumed(), (False, False, False))

    def test_exact_receipt_type_no_subclass(self):
        class Derived(pre1._FreshOpenOrderReceipt): pass
        clone = object.__new__(Derived)
        for field in fields(self.receipts[0]):
            object.__setattr__(clone, field.name, getattr(self.receipts[0], field.name))
        self.change(self.sources[0], KINDS[0] + '__receipt', clone)
        with self.assertRaises(ReadOnlyError): self.owner()
        self.assertEqual(self.consumed(), (False, False, False))

    def test_wrong_thread_factory_does_not_register(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(ReadOnlyError): pool.submit(self.owner).result(timeout=5)
        self.assertFalse(hasattr(self.transport, '_fresh_collection_preparation_owner'))

    def test_wrong_thread_prepare_terminal_without_consumption(self):
        owner = self.owner()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(ReadOnlyError): pool.submit(owner._prepare).result(timeout=5)
        self.assertTrue(owner._failed); self.assertIsNone(owner._prepared)
        self.assertEqual(self.consumed(), (False, False, False))

    def test_wrong_thread_token_validation_rejected(self):
        owner = self.owner(); token = owner._prepare()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(ReadOnlyError): pool.submit(owner._require_prepared, token).result(timeout=5)
        self.assertIs(owner._require_prepared(token), token)

    def test_equal_reconstructed_journal_owner_not_registered(self):
        clone = object.__new__(type(self.journal))
        for slot in type(self.journal).__slots__:
            object.__setattr__(clone, '_PaperDispatchJournalOwner' + slot, getattr(self.journal, '_PaperDispatchJournalOwner' + slot))
        with self.assertRaises(ReadOnlyError): m._create_fresh_collection_preparation_owner(clone)

    def test_equal_reconstructed_preparation_owner_not_registered(self):
        owner = self.owner(); token = owner._prepare(); clone = object.__new__(Owner)
        for slot in Owner.__slots__:
            object.__setattr__(clone, '_FreshCollectionPreparationOwner' + slot,
                               getattr(owner, '_FreshCollectionPreparationOwner' + slot))
        with self.assertRaises(ReadOnlyError): clone._require_prepared(token)

    def test_equal_reconstructed_token_not_issuer_evidence(self):
        owner = self.owner(); token = owner._prepare(); clone = replace(token)
        self.assertEqual(clone, token)
        with self.assertRaises(ReadOnlyError): owner._require_prepared(clone)
        self.assertIs(owner._require_prepared(token), token)

    def test_serialization_cannot_restore_exact_issuer_provenance(self):
        owner = self.owner(); token = owner._prepare(); clone = pickle.loads(pickle.dumps(token))
        self.assertEqual(clone, token)
        with self.assertRaises(ReadOnlyError): owner._require_prepared(clone)

    def test_no_journal_codec_registration_for_token(self):
        token = self.owner()._prepare()
        self.assertNotIn(Token, storage._MODELS)
        with self.assertRaises(ReadOnlyError): storage._encode(token)

    def test_foreign_runtime_token_rejected(self):
        owner = self.owner(); token = owner._prepare()
        self.collect_sources(); other = self.owner(); other._prepare()
        with self.assertRaises(ReadOnlyError): other._require_prepared(token)

    def test_wrong_token_types_rejected(self):
        owner = self.owner(); owner._prepare()
        for token in (None, object(), {}, self.receipts[0]):
            with self.assertRaises(ReadOnlyError): owner._require_prepared(token)

    def test_frozen_slotted_opaque(self):
        owner = self.owner(); token = owner._prepare()
        self.assertFalse(hasattr(owner, '__dict__')); self.assertFalse(hasattr(token, '__dict__'))
        self.assertEqual(repr(token), '<PrivatePreparedFreshCollectionEvidence evidence-only>')
        with self.assertRaises(FrozenInstanceError): token.session_id = 'substitute'
        with self.assertRaises(ReadOnlyError): owner.other = None
        with self.assertRaises(ReadOnlyError): del owner._prepared

    def test_second_prepare_cannot_reconsume_or_publish(self):
        owner = self.owner(); owner._prepare()
        with self.assertRaises(ReadOnlyError): owner._prepare()
        self.assertTrue(owner._failed); self.assertIsNone(owner._prepared)
        self.assertEqual(self.consumed(), (True, True, True))


class FailureTests(Fixture):
    def interrupted(self, error, index, *, after=False):
        owner = self.owner(); original = Owner._consume_source
        def consume(this, current):
            if current == index and not after: raise error
            result = original(this, current)
            if current == index and after: raise error
            return result
        with patch.object(Owner, '_consume_source', consume):
            with self.assertRaises(type(error)) as caught: owner._prepare()
        self.assertIs(caught.exception, error)
        if type(error) is SystemExit: self.assertEqual(caught.exception.code, 57)
        self.assertTrue(owner._failed); self.assertIsNone(owner._prepared)
        expected = tuple(i < index or after and i == index for i in range(3))
        self.assertEqual(self.consumed(), expected)
        with self.assertRaises(ReadOnlyError): owner._prepare()
        with self.assertRaises(ReadOnlyError): self.owner()
        self.assertEqual(self.consumed(), expected)
        self.assertEqual(self.safety(), self.before)

    def test_ordinary_failure_after_all_consumes_no_token(self):
        owner = self.owner()
        with patch.object(Owner, '_publish', side_effect=RuntimeError('private diagnostic must not leak')):
            with self.assertRaises(ReadOnlyError) as caught: owner._prepare()
        self.assertEqual(str(caught.exception), m._ERROR)
        self.assertIsNone(owner._prepared); self.assertTrue(owner._failed)
        self.assertEqual(self.consumed(), (True, True, True))
        with self.assertRaises(ReadOnlyError): owner._prepare()

    def test_mutation_during_final_publication_prevents_token(self):
        owner = self.owner(); original = Owner._publish
        def publish(this, token):
            values = original(this, token)
            self.write._arrival_sequence += 1
            return values
        with patch.object(Owner, '_publish', publish):
            with self.assertRaises(ReadOnlyError): owner._prepare()
        self.assertIsNone(owner._prepared); self.assertTrue(owner._failed)
        self.assertEqual(self.consumed(), (True, True, True))

    def test_consumption_replacement_after_factory_rejected_before_first(self):
        owner = self.owner()
        with patch.object(type(self.sources[2]), '_consume_receipt', side_effect=AssertionError('replaced consumer')):
            with self.assertRaises(ReadOnlyError): owner._prepare()
        self.assertEqual(self.consumed(), (False, False, False))

    def test_missing_original_receipt_after_factory_rejected_before_first(self):
        owner = self.owner(); self.change(self.sources[2], KINDS[2] + '__receipt', replace(self.receipts[2]))
        with self.assertRaises(ReadOnlyError): owner._prepare()
        self.assertEqual(self.consumed(), (False, False, False))

    def test_changed_read_configuration_rejected(self):
        owner = self.owner(); self.change(self.transport.config, 'client_id', self.transport.config.client_id + 1)
        with self.assertRaises(ReadOnlyError): owner._prepare()
        self.assertEqual(self.consumed(), (False, False, False))

    def test_equal_history_tuple_substitution_rejected(self):
        owner = self.owner(); history = self.journal._PaperDispatchJournalOwner__history
        self.change(self.journal, '_PaperDispatchJournalOwner__history', tuple(list(history)))
        with self.assertRaises(ReadOnlyError): owner._prepare()
        self.assertEqual(self.consumed(), (False, False, False))

    def test_same_source_observation_decimal_representation_tampering_rejected(self):
        owner = self.owner()
        event = self.receipts[1].observations[0]
        self.change(event, 'shares', Decimal(str(event.shares) + '0') if '.' in str(event.shares)
                    else Decimal(str(event.shares) + '.00'))
        with self.assertRaises(ReadOnlyError): owner._prepare()
        self.assertEqual(self.consumed(), (False, False, False))

    def test_token_validation_rejects_rewritten_prefix(self):
        owner = self.owner(); token = owner._prepare(); history = self.journal._PaperDispatchJournalOwner__history
        replacement = tuple(tuple(list(entry)) for entry in history)
        self.change(self.journal, '_PaperDispatchJournalOwner__history', replacement)
        with self.assertRaises(ReadOnlyError): owner._require_prepared(token)


class BoundaryTests(Fixture):
    def test_authentication_expiry_prevents_preparation_without_consumption(self):
        owner = self.owner()
        self.transport._clock.now = self.receipts[0].authentication_deadline
        with self.assertRaises(ReadOnlyError): owner._prepare()
        self.assertEqual(self.consumed(), (False, False, False))

    def test_write_arrival_before_preparation_rejected(self):
        owner = self.owner(); self.write._arrival_sequence += 1
        with self.assertRaises(ReadOnlyError): owner._prepare()
        self.assertEqual(self.consumed(), (False, False, False))

    def test_write_callback_before_preparation_rejected(self):
        owner = self.owner(); self.write._callback_sequence += 1
        with self.assertRaises(ReadOnlyError): owner._prepare()
        self.assertEqual(self.consumed(), (False, False, False))

    def test_changed_persistence_cursor_rejected(self):
        owner = self.owner(); self.journal._PaperDispatchJournalOwner__persistence_cursor += 1
        with self.assertRaises(ReadOnlyError): owner._prepare()
        self.assertEqual(self.consumed(), (False, False, False))

    def test_active_attempt_rejected(self):
        owner = self.owner(); self.coordinator._active_attempt = object()
        with self.assertRaises(ReadOnlyError): owner._prepare()
        self.assertEqual(self.consumed(), (False, False, False))


class NoAuthorityTests(Fixture):
    def test_preparation_and_token_validation_have_no_broker_durability_or_decision_calls(self):
        owner = self.owner()
        with patch.object(self.api.Client, 'reqAllOpenOrders', side_effect=AssertionError('request')), \
                patch.object(self.api.Client, 'reqExecutions', side_effect=AssertionError('request')), \
                patch.object(self.api.Client, 'reqCompletedOrders', side_effect=AssertionError('request')), \
                patch.object(self.paper_api.Client, 'placeOrder', side_effect=AssertionError('order')), \
                patch.object(self.paper_api.Client, 'cancelOrder', side_effect=AssertionError('cancel')), \
                patch.object(journal._PaperDispatchJournalOwner, '_append_event', side_effect=AssertionError('append')), \
                patch.object(journal._PaperDispatchJournalOwner, '_certify_decision_boundary', side_effect=AssertionError('producer')), \
                patch.object(storage._SQLiteJournalStore, '_append', side_effect=AssertionError('SQLite')), \
                patch.object(storage._SQLiteJournalStore, '_load', side_effect=AssertionError('SQLite')), \
                patch.object(status, '_evaluate_decision_status', side_effect=AssertionError('evaluator')), \
                patch('builtins.open', side_effect=AssertionError('filesystem')):
            token = owner._prepare()
            self.assertIs(owner._require_prepared(token), token)
        self.assertEqual(self.safety(), self.before)

    def test_callback_locks_not_held_for_sdk_state_inspection(self):
        original = self.api.Client.isConnected
        def connected(this):
            # The reviewed authentication contract checks local read connection
            # state under read-local locks. No write/arrival lock may be added.
            for lock in (self.write._lock, self.write._arrival_lock):
                self.assertFalse(lock._is_owned())
            return original(this)
        with patch.object(self.api.Client, 'isConnected', connected):
            self.owner()._prepare()

    def test_sdk_capability_inspection_outside_all_capture_and_control_locks(self):
        original = self.api.Client.serverVersion
        def version(client):
            for lock in (self.coordinator._lock, self.broker._lock, self.transport._lifecycle,
                         self.transport._condition, self.write._lock, self.write._arrival_lock):
                self.assertFalse(lock._is_owned())
            return original(client)
        with patch.object(self.api.Client, 'serverVersion', version):
            self.owner()._prepare()

    def test_no_authority_state_mutation(self):
        owner = self.owner(); owner._prepare()
        self.assertEqual(self.safety(), self.before)
        loaded = self.journal._load()
        self.assertTrue(loaded.replay.restart_quarantine_required)
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.transport._snapshot.account.mode)
        self.assertEqual(self.write_calls(), [])
        self.assertTrue(self.coordinator._broker_state_reconciliation_required)

    def test_module_private_no_source_interpretation_or_producer_calls(self):
        self.assertEqual(m.__all__, ())
        text = Path(m.__file__).read_text(); tree = ast.parse(text)
        calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertTrue({'reqAllOpenOrders', 'reqExecutions', 'reqCompletedOrders', '_collect',
            '_certify_decision_boundary', '_evaluate_decision_status', '_append_event', '_drain_callbacks'} .isdisjoint(calls))
        self.assertNotIn('paper_decision_status_evidence', text)
        self.assertFalse(any(isinstance(n, ast.Import) and any(v.name in ('sqlite3', 'socket') for v in n.names) for n in ast.walk(tree)))
        self.assertEqual([name for name in dir(Owner) if not name.startswith('_')], [])
        self.assertEqual({f.name for f in fields(Token)}, set(m._SHARED) | {
            'open_order_receipt', 'execution_receipt', 'completed_order_receipt', 'limitations'})


def _static_mismatch(index, field, value):
    def test(self):
        selected = self.receipts[index]
        chosen = value(self, selected) if callable(value) else value
        self.reject_before_consume(lambda: self.change(selected, field, chosen))
    return test


_MISMATCHES = {
    'account_binding': lambda f, r: j._JournalAccountBinding('binding-' + '9' * 32),
    'journal_id': 'journal-' + '9' * 32, 'session_id': 'session-' + '9' * 32,
    'read_generation': 99, 'write_generation': 99, 'read_client_id': 99, 'placing_client_id': 99,
    'authentication_deadline': lambda f, r: r.authentication_deadline + timedelta(seconds=1),
    'scope': 'OTHER_SCOPE', 'outcome': lambda f, r: SOURCES[f.receipts.index(r)]._CollectionOutcome.FAILED,
    'sdk_result': lambda f, r: SOURCES[f.receipts.index(r)]._SDKRequestResult.ATTEMPTED,
    'completion_observed': False, 'completion_marker': None, 'boundary_changed': True,
    'coverage_limitations': (), 'observations': [object()],
    'read_generation_bool': True, 'completion_marker_bool': True,
    'write_generation_bool': True, 'read_client_id_bool': True, 'placing_client_id_bool': True,
    'local_collection_id_bool': True, 'initial_sequence_watermark_bool': True,
    'final_sequence_watermark_bool': True,
}
for _index in range(3):
    for _label, _value in _MISMATCHES.items():
        _field = _label.removesuffix('_bool')
        setattr(IdentityTests, f'test_static_source_{_index}_{_label}', _static_mismatch(_index, _field, _value))


def _source_identity_test(index, *, reconstructed=False, consumed=False):
    def test(self):
        if consumed:
            self.sources[index]._consume_receipt(self.receipts[index])
            with self.assertRaises(ReadOnlyError): self.owner()
            self.assertEqual(self.consumed(), tuple(i == index for i in range(3)))
        else:
            owner = self.owner()
            clone = replace(self.receipts[index]) if reconstructed else self.receipts[(index + 1) % 3]
            self.change(self.sources[index], KINDS[index] + '__receipt', clone)
            with self.assertRaises(ReadOnlyError): owner._prepare()
            self.assertEqual(self.consumed(), (False, False, False))
    return test


for _index in range(3):
    setattr(IdentityTests, f'test_source_{_index}_equal_reconstructed_receipt', _source_identity_test(_index, reconstructed=True))
    setattr(IdentityTests, f'test_source_{_index}_substituted_receipt', _source_identity_test(_index))
    setattr(IdentityTests, f'test_source_{_index}_already_consumed', _source_identity_test(_index, consumed=True))


def _registered_owner_substitute(index):
    def test(self):
        owner = self.owner(); source = self.sources[index]; clone = object.__new__(type(source))
        for slot in type(source).__slots__:
            name = KINDS[index] + slot
            object.__setattr__(clone, name, getattr(source, name))
        self.change(self.transport, m._SOURCES[index][3], clone)
        with self.assertRaises(ReadOnlyError): owner._prepare()
        self.assertEqual(self.consumed(), (False, False, False))
    return test


for _index in range(3):
    setattr(IdentityTests, f'test_registered_source_owner_{_index}_clone_rejected', _registered_owner_substitute(_index))


def _token_tamper(field):
    def test(self):
        owner = self.owner(); token = owner._prepare()
        value = getattr(token, field)
        replacement = replace(value) if field.endswith('_receipt') or field == 'account_binding' else \
            value + timedelta(seconds=1) if field == 'authentication_deadline' else \
            () if field == 'limitations' else value + 1 if type(value) is int else 'substituted'
        self.change(token, field, replacement)
        with self.assertRaises(ReadOnlyError): owner._require_prepared(token)
    return test


for _field in ('open_order_receipt', 'execution_receipt', 'completed_order_receipt', *m._SHARED, 'limitations'):
    setattr(IdentityTests, 'test_token_tamper_' + _field, _token_tamper(_field))


def _interruption(index, error_type, after):
    def test(self):
        error = SystemExit(57) if error_type is SystemExit else error_type('interrupt')
        self.interrupted(error, index, after=after)
    return test


for _index in range(3):
    for _error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
        for _after in (False, True):
            setattr(FailureTests, f'test_{_error_type.__name__}_consume_{_index}_after_{_after}',
                    _interruption(_index, _error_type, _after))


def _publication_interrupt(error_type, seam):
    def test(self):
        owner = self.owner(); error = SystemExit(57) if error_type is SystemExit else error_type('interrupt')
        if seam == '_set':
            original = Owner._set
            def setter(this, name, value):
                if name == 'prepared': raise error
                return original(this, name, value)
            patcher = patch.object(Owner, '_set', setter)
        else:
            patcher = patch.object(Owner, seam, side_effect=error)
        with patcher:
            with self.assertRaises(error_type) as caught: owner._prepare()
        self.assertIs(caught.exception, error)
        self.assertTrue(owner._failed); self.assertIsNone(owner._prepared)
        self.assertEqual(self.consumed(), (False, False, False) if seam == '_prevalidate' else (True, True, True))
    return test


for _error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
    for _seam in ('_prevalidate', '_publish', '_set'):
        setattr(FailureTests, f'test_{_error_type.__name__}_{_seam}', _publication_interrupt(_error_type, _seam))


def _factory_interrupt(error_type):
    def test(self):
        error = SystemExit(57) if error_type is SystemExit else error_type('factory interruption')
        with patch.object(Owner, '_prevalidate', side_effect=error):
            with self.assertRaises(error_type) as caught: self.owner()
        self.assertIs(caught.exception, error)
        registered = self.transport._fresh_collection_preparation_owner
        self.assertTrue(registered._failed); self.assertIsNone(registered._prepared)
        self.assertEqual(self.consumed(), (False, False, False))
        with self.assertRaises(ReadOnlyError): self.owner()
    return test


for _error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
    setattr(FailureTests, f'test_factory_{_error_type.__name__}', _factory_interrupt(_error_type))


def _original_consumer_failure(index, error_type):
    def test(self):
        owner = self.owner(); source = self.sources[index]
        original = type(source)._authenticate
        error = SystemExit(57) if error_type is SystemExit else error_type('in original consumer')
        count = 0
        def authenticate(this):
            nonlocal count
            count += 1
            if count == 2: raise error  # First is full prevalidation; second is original consume.
            return original(this)
        with patch.object(type(source), '_authenticate', authenticate):
            with self.assertRaises(ReadOnlyError if error_type is RuntimeError else error_type) as caught:
                owner._prepare()
        if error_type is not RuntimeError:
            self.assertIs(caught.exception, error)
        self.assertTrue(owner._failed); self.assertIsNone(owner._prepared)
        self.assertTrue(source._failed)
        self.assertEqual(self.consumed(), tuple(i < index for i in range(3)))
        with self.assertRaises(ReadOnlyError): owner._prepare()
    return test


for _index in range(3):
    for _error_type in (RuntimeError, KeyboardInterrupt, SystemExit, GeneratorExit):
        setattr(FailureTests, f'test_original_consumer_{_index}_{_error_type.__name__}',
                _original_consumer_failure(_index, _error_type))


def _live_substitution(component):
    def test(self):
        owner = self.owner()
        obj, name, value = {
            'read_generation': (self.transport, '_generation', self.transport._generation + 1),
            'write_generation': (self.write, '_generation', self.write._generation + 1),
            'read_client': (self.transport, '_client', object()), 'write_client': (self.write, '_client', object()),
            'read_transport': (self.broker, '_transport', object()),
            'write_transport': (self.coordinator, '_write', object()),
            'session': (self.journal, '_PaperDispatchJournalOwner__components',
                tuple(replace(v) if i == 7 else v for i, v in enumerate(self.journal._PaperDispatchJournalOwner__components))),
            'header': (self.journal, '_PaperDispatchJournalOwner__components',
                tuple(replace(v) if i == 5 else v for i, v in enumerate(self.journal._PaperDispatchJournalOwner__components))),
            'store': (self.journal, '_PaperDispatchJournalOwner__components',
                tuple(object() if i == 6 else v for i, v in enumerate(self.journal._PaperDispatchJournalOwner__components))),
            'association': (self.transport, '_journal_account_association', replace(self.transport._journal_account_association)),
            'snapshot': (self.transport, '_snapshot', replace(self.transport._snapshot)),
            'read_disconnect': (self.transport, '_disconnect_emitted', True),
            'write_closed': (self.write, '_closed', True),
            'loss': (self.write, '_PaperTWSTransport__callback_evidence_lost_at', 1),
            'invalid_callback': (self.write, '_invalid_callback', True),
            'arrival_unusable': (self.write, '_arrival_unusable', True),
        }[component]
        self.change(obj, name, value)
        with self.assertRaises(ReadOnlyError): owner._prepare()
        self.assertEqual(self.consumed(), (False, False, False))
    return test


for _component in ('read_generation', 'write_generation', 'read_client', 'write_client', 'read_transport',
                   'write_transport', 'session', 'association', 'snapshot', 'read_disconnect', 'write_closed',
                   'header', 'store', 'loss', 'invalid_callback', 'arrival_unusable'):
    setattr(BoundaryTests, 'test_live_substitution_' + _component, _live_substitution(_component))


def _token_lifetime_change(component):
    def test(self):
        owner = self.owner(); token = owner._prepare()
        obj, name, value = {
            'read_generation': (self.transport, '_generation', self.transport._generation + 1),
            'write_generation': (self.write, '_generation', self.write._generation + 1),
            'read_client': (self.transport, '_client', object()),
            'read_disconnect': (self.transport, '_disconnect_emitted', True),
            'write_close': (self.write, '_closed', True),
            'configuration': (self.transport.config, 'client_id', self.transport.config.client_id + 1),
        }[component]
        self.change(obj, name, value)
        with self.assertRaises(ReadOnlyError): owner._require_prepared(token)
    return test


for _component in ('read_generation', 'write_generation', 'read_client', 'read_disconnect', 'write_close', 'configuration'):
    setattr(BoundaryTests, 'test_published_token_lifetime_' + _component, _token_lifetime_change(_component))


if __name__ == '__main__': unittest.main()
