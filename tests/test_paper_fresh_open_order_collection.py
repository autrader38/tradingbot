"""One-shot same-session provenance; anonymous fake SDK only, no Gateway activity."""

import ast
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from decimal import Decimal as D, localcontext, Inexact, Rounded
from pathlib import Path
from threading import current_thread
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import weakref

from tradingbot_broker import paper_fresh_open_order_collection as m
from tradingbot_broker import ibkr_readonly as read
from tradingbot_broker import paper_dispatch_journal as journal
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests.test_paper_dispatch import BridgeFixture
from tests.test_ibkr_reconciliation_evidence import CONTRACT, ORDER, raw_proto


Outcome = m._CollectionOutcome
Owner = m._FreshOpenOrderCollectionOwner


class Fixture(BridgeFixture):
    def setUp(self):
        super().setUp()
        for name in ('socket.socket.connect', 'socket.socket.connect_ex',
                     'socket.create_connection', 'socket.getaddrinfo'):
            self.enterContext(patch(name, side_effect=AssertionError('network forbidden')))
        self.build()
        # Existing blocked runtime, without invoking even a simulated entry SDK call.
        self.coordinator._broker_state_reconciliation_required = True
        self.owner = m._create_fresh_open_order_collection_owner(self.journal)
        self.read_client = self.api.clients[-1]
        self.wrapper = self.read_client.wrapper
        self.before = self.safety()

    def safety(self):
        return (self.journal._load().records, self.journal._persistence_cursor,
            self.coordinator._callback_cursor, self.write._callback_sequence,
            self.coordinator._broker_state_reconciliation_required,
            self.coordinator._reconciliation_required, self.broker._reconciliation_required,
            self.write._reconciliation_required, self.broker._paper_authority.status,
            self.transport._generation, self.write._generation,
            self.transport._snapshot, self.transport._journal_account_association)

    def emit(self, *, encoding='LEGACY', order=None, contract=None, status='Submitted',
             order_id=None, account=None, raw=None):
        values = dict(ORDER); values.update(order or {})
        cv = dict(CONTRACT); cv.update(contract or {})
        if encoding == 'PROTOBUF':
            self.wrapper.openOrderProtoBuf(raw if raw is not None else raw_proto('orders',
                payload=values, contract=cv, top=values['orderId'], status=status))
        payload = NS(**values, account=self.transport._accounts[0] if account is None else account)
        self.wrapper.openOrder(values['orderId'] if order_id is None else order_id,
                               NS(**cv), payload, NS(status=status))
        return payload

    def collect(self, function=None, *, count=1, end=True, encoding='LEGACY'):
        def request(client):
            self.assertIs(client, self.read_client)
            for lock in (self.coordinator._lock, self.broker._lock, self.transport._lifecycle,
                         self.transport._condition, self.write._lock, self.write._arrival_lock):
                self.assertFalse(lock._is_owned(), 'SDK request under an incompatible lock')
            if function is not None:
                function()
            else:
                for i in range(count): self.emit(encoding=encoding)
                if end: self.wrapper.openOrderEnd()
        with patch.object(self.api.Client, 'reqAllOpenOrders', request):
            return self.owner._collect()

    def change(self, obj, name, value):
        original = getattr(obj, name)
        object.__setattr__(obj, name, value)
        self.addCleanup(object.__setattr__, obj, name, original)

    def timeout_clock(self):
        deadline = self.owner._FreshOpenOrderCollectionOwner__monotonic_deadline
        self.enterContext(patch.object(m, '_clock', return_value=deadline - .5))

    def assert_failed(self, receipt):
        self.assertIsNot(receipt.outcome, Outcome.COMPLETED)
        self.assertTrue(self.owner._failed)
        self.assertTrue(self.coordinator._broker_state_reconciliation_required)
        with self.assertRaises(ReadOnlyError): self.owner._consume_receipt(receipt)
        with self.assertRaises(ReadOnlyError): self.owner._collect()


class SuccessTests(Fixture):
    def test_exact_owner_association(self):
        components = self.owner._FreshOpenOrderCollectionOwner__association
        self.assertIs(components[0], self.journal)
        expected = (self.coordinator, self.broker, self.transport, self.write,
                    self.journal._receipt, self.journal._header, self.journal._store,
                    self.journal._session, current_thread())
        self.assertTrue(all(a is b for a, b in zip(components[1:], expected)))

    def test_constructor_cannot_mint_owner(self):
        with self.assertRaises(ReadOnlyError): Owner()

    def test_no_second_owner_or_alternate_journal(self):
        with self.assertRaises(ReadOnlyError): m._create_fresh_open_order_collection_owner(self.journal)
        self.assertIs(self.transport._fresh_open_order_owner, self.owner)

    def test_one_legacy_received_occurrence(self):
        receipt = self.collect()
        self.assertIs(receipt.outcome, Outcome.COMPLETED)
        self.assertIs(receipt.sdk_result, m._SDKRequestResult.RETURNED_NORMALLY)
        self.assertTrue(receipt.completion_observed)
        self.assertEqual(receipt.count, 1)
        self.assertEqual((receipt.first_observation_sequence, receipt.last_observation_sequence), (1, 1))
        self.assertEqual(receipt.observations[0].encoding, 'LEGACY')
        self.assertEqual(self.safety(), self.before)

    def test_protobuf_capture_same_existing_source_model(self):
        receipt = self.collect(encoding='PROTOBUF')
        event, = receipt.observations
        self.assertIs(type(event), read._ReconciliationOrderEvidence)
        self.assertEqual(event.encoding, 'PROTOBUF')
        self.assertTrue(event.presence.top_order_id and event.presence.nested_order_id)
        self.assertEqual((event.order_id, event.client_id, event.perm_id, event.order_ref), (10, 2, 77, 'entry-1'))

    def test_successful_empty_collection_requires_end(self):
        receipt = self.collect(count=0)
        self.assertIs(receipt.outcome, Outcome.COMPLETED)
        self.assertEqual(receipt.count, 0)
        self.assertIsNone(receipt.first_observation_sequence)
        self.assertIsNone(receipt.last_observation_sequence)
        self.assertEqual(receipt.final_sequence_watermark, receipt.initial_sequence_watermark)

    def test_many_records_keep_source_occurrences(self):
        receipt = self.collect(count=20)
        self.assertEqual([e.sequence for e in receipt.observations], list(range(1, 21)))
        self.assertEqual(receipt.count, 20)

    def test_exact_capacity_is_success(self):
        receipt = self.collect(count=1024)
        self.assertIs(receipt.outcome, Outcome.COMPLETED)
        self.assertEqual(receipt.count, 1024)

    def test_overflow_is_not_truncated_success(self):
        receipt = self.collect(count=1025)
        self.assertIs(receipt.outcome, Outcome.OVERFLOW)
        self.assertEqual(receipt.count, 1024)
        self.assertEqual(receipt.final_sequence_watermark, 1025)
        self.assert_failed(receipt)

    def test_receipt_fields_are_private_frozen_and_opaque(self):
        receipt = self.collect()
        self.assertFalse(hasattr(receipt, '__dict__'))
        with self.assertRaises(FrozenInstanceError): receipt.read_generation = 900
        self.assertNotIn('entry-1', repr(receipt))
        self.assertNotIn(self.transport._accounts[0], repr(receipt))
        self.assertEqual(tuple(inspect_fields(receipt)), tuple(f.name for f in fields(receipt)))

    def test_receipt_consumption_is_exact_and_one_shot(self):
        receipt = self.collect()
        self.assertIs(self.owner._consume_receipt(receipt), receipt)
        with self.assertRaises(ReadOnlyError): self.owner._consume_receipt(receipt)

    def test_equal_receipt_is_not_issuer_provenance(self):
        receipt = self.collect()
        equal = replace(receipt)
        self.assertEqual(equal, receipt)
        with self.assertRaises(ReadOnlyError): self.owner._consume_receipt(equal)

    def test_no_second_request_after_success(self):
        self.collect()
        with patch.object(self.api.Client, 'reqAllOpenOrders', side_effect=AssertionError('retry')):
            with self.assertRaises(ReadOnlyError): self.owner._collect()

    def test_collection_runs_while_journal_dispatch_inhibited(self):
        self.journal._inhibit()
        receipt = self.collect()
        self.assertIs(receipt.outcome, Outcome.COMPLETED)
        self.assertTrue(self.journal._inhibited)
        self.assertTrue(self.coordinator._broker_state_reconciliation_required)

    def test_no_read_snapshot_deadline_or_generation_renewal(self):
        snapshot = self.transport._snapshot
        association = self.transport._journal_account_association
        self.collect()
        self.assertIs(self.transport._snapshot, snapshot)
        self.assertIs(self.transport._journal_account_association, association)
        self.assertEqual(self.safety(), self.before)

    def test_existing_reconnect_handshake_unchanged(self):
        request = self.transport._prepare_reconciliation_read_request()
        original = self.transport._reconciliation_snapshot()
        self.collect()
        self.assertIs(self.transport._reconciliation_snapshot(), original)
        self.assertIsNone(self.transport._reconciliation_read_binding)
        with self.assertRaises(ReadOnlyError): self.transport._consume_reconciliation_read_receipt(request)

    def test_same_session_receipt_has_explicit_local_limitations(self):
        receipt = self.collect()
        self.assertEqual(receipt.local_collection_id, 1)
        self.assertIn('LOCAL_ID_NOT_BROKER_REQUEST_ID', receipt.coverage_limitations)
        self.assertIn('NOT_AN_ATOMIC_BROKER_SNAPSHOT', receipt.coverage_limitations)
        self.assertIn('UNSOLICITED_OR_DELAYED_OBSERVATIONS_POSSIBLE', receipt.coverage_limitations)

    def test_distinct_sequence_domains(self):
        self.transport._fresh_open_order_sequence = 19
        receipt = self.collect(count=2)
        self.assertEqual([e.sequence for e in receipt.observations], [20, 21])
        self.assertEqual(self.write._callback_sequence, self.before[3])
        self.assertEqual(self.coordinator._callback_cursor, self.before[2])
        self.assertEqual(self.journal._persistence_cursor, self.before[1])

    def test_pre_issue_callbacks_are_not_copied_from_cache(self):
        self.emit(); self.wrapper.openOrderEnd()
        receipt = self.collect(count=0)
        self.assertEqual(receipt.count, 0)

    def test_duplicate_source_observations_keep_sequence(self):
        receipt = self.collect(count=2)
        self.assertEqual([e.sequence for e in receipt.observations], [1, 2])
        self.assertEqual(receipt.observations[0].order_id, receipt.observations[1].order_id)

    def test_conflicting_repeat_remains_two_observations(self):
        def callbacks():
            self.emit(status='Submitted'); self.emit(status='Cancelled'); self.wrapper.openOrderEnd()
        receipt = self.collect(callbacks)
        self.assertEqual([e.status for e in receipt.observations], ['Submitted', 'Cancelled'])
        self.assertIs(receipt.outcome, Outcome.COMPLETED)  # Collection, not lifecycle resolution.

    def test_exact_decimal_representation_survives(self):
        def callbacks():
            self.emit(order={'totalQuantity': D('10.000')}); self.wrapper.openOrderEnd()
        with localcontext() as ctx:
            ctx.prec = 1; ctx.traps[Inexact] = ctx.traps[Rounded] = True
            receipt = self.collect(callbacks)
        self.assertEqual(receipt.observations[0].quantity.as_tuple(), D('10.000').as_tuple())

    def test_no_raw_order_or_contract_survives(self):
        refs = []
        class Raw(NS):
            def __repr__(self): raise AssertionError('raw repr forbidden')
        def callbacks():
            order = Raw(**ORDER, account=self.transport._accounts[0]); contract = Raw(**CONTRACT)
            refs.extend((weakref.ref(order), weakref.ref(contract)))
            self.wrapper.openOrder(10, contract, order, Raw(status='Submitted'))
            self.wrapper.openOrderEnd()
        receipt = self.collect(callbacks)
        self.assertTrue(all(r() is None for r in refs))
        self.assertEqual(receipt.count, 1)

    def test_other_account_filtered_without_identifier_retention(self):
        receipt = self.collect(lambda: (self.emit(account='foreign-anonymous-fixture'), self.wrapper.openOrderEnd()))
        self.assertEqual(receipt.count, 0)
        self.assertNotIn('foreign-anonymous-fixture', repr(receipt))

    def test_unknown_status_is_existing_category_not_new_authority(self):
        receipt = self.collect(lambda: (self.emit(status='unrecognized'), self.wrapper.openOrderEnd()))
        self.assertEqual(receipt.observations[0].status, 'UNKNOWN')

    def test_normal_sdk_return_is_not_completion(self):
        receipt = self.collect(self.timeout_clock)
        self.assertIs(receipt.sdk_result, m._SDKRequestResult.RETURNED_NORMALLY)
        self.assertFalse(receipt.completion_observed)
        self.assertIs(receipt.outcome, Outcome.TIMED_OUT)
        self.assert_failed(receipt)

    def test_observation_without_end_is_not_success(self):
        receipt = self.collect(lambda: (self.emit(), self.timeout_clock()))
        self.assertEqual(receipt.count, 1)
        self.assertIs(receipt.outcome, Outcome.TIMED_OUT)
        self.assert_failed(receipt)

    def test_end_before_sdk_return_keeps_distinct_facts(self):
        def callbacks():
            self.wrapper.openOrderEnd()
            self.assertIs(self.owner._FreshOpenOrderCollectionOwner__sdk_result, m._SDKRequestResult.ATTEMPTED)
            self.assertIsNone(self.owner._receipt)
        receipt = self.collect(callbacks)
        self.assertTrue(receipt.completion_observed)
        self.assertIs(receipt.sdk_result, m._SDKRequestResult.RETURNED_NORMALLY)

    def test_ordinary_sdk_exception_after_end_is_failed(self):
        def callbacks():
            self.wrapper.openOrderEnd(); raise ValueError('private fixture text')
        receipt = self.collect(callbacks)
        self.assertIs(receipt.outcome, Outcome.FAILED)
        self.assertIs(receipt.sdk_result, m._SDKRequestResult.RAISED_EXCEPTION)
        self.assert_failed(receipt)

    def test_ordinary_sdk_exception_before_callback_is_failed(self):
        def callbacks(): raise ValueError('private fixture text')
        receipt = self.collect(callbacks)
        self.assertEqual(receipt.count, 0)
        self.assert_failed(receipt)

    def test_callback_after_end_before_publication_is_failed(self):
        receipt = self.collect(lambda: (self.wrapper.openOrderEnd(), self.emit()))
        self.assertIs(receipt.outcome, Outcome.FAILED)
        self.assertEqual(receipt.count, 0)

    def test_duplicate_end_is_failed(self):
        receipt = self.collect(lambda: (self.wrapper.openOrderEnd(), self.wrapper.openOrderEnd()))
        self.assert_failed(receipt)

    def test_delayed_callbacks_cannot_repair_failed_request(self):
        receipt = self.collect(self.timeout_clock)
        self.emit(); self.wrapper.openOrderEnd()
        self.assertIs(self.owner._receipt, receipt)
        self.assertEqual(receipt.count, 0)
        self.assert_failed(receipt)

    def test_stale_callback_does_not_touch_source_getters(self):
        class Bad:
            def __getattribute__(self, name): raise AssertionError('stale getter')
        receipt = self.collect(lambda: (self.transport._reconciliation_decoded(
            self.transport._generation-1, 'orders', Bad(), Bad(), Bad(), 10), self.wrapper.openOrderEnd()))
        self.assertIs(receipt.outcome, Outcome.COMPLETED)
        self.assertEqual(receipt.count, 0)

    def test_account_expiry_before_issue_no_request(self):
        self.clock.advance(301)
        with patch.object(self.api.Client, 'reqAllOpenOrders', side_effect=AssertionError('expired request')):
            with self.assertRaises(ReadOnlyError): self.owner._collect()

    def test_expiry_during_collection_invalidates(self):
        receipt = self.collect(lambda: (self.emit(), self.clock.advance(301), self.wrapper.openOrderEnd()))
        self.assertIs(receipt.outcome, Outcome.INVALIDATED)
        self.assert_failed(receipt)

    def test_expiry_before_consumption_invalidates(self):
        receipt = self.collect(); self.clock.advance(301)
        with self.assertRaises(ReadOnlyError): self.owner._consume_receipt(receipt)
        self.assertTrue(self.owner._failed)

    def test_monotonic_deadline_cannot_be_renewed_by_clock_rollback(self):
        self.enterContext(patch.object(m, '_clock', return_value=
            self.owner._FreshOpenOrderCollectionOwner__monotonic_deadline + 1))
        with self.assertRaises(ReadOnlyError): self.owner._collect()

    def test_disconnected_sdk_local_state_fails_before_issue(self):
        self.read_client.connected = False
        with self.assertRaises(ReadOnlyError): self.owner._collect()

    def test_disconnect_callback_during_collection_invalidates(self):
        receipt = self.collect(lambda: (self.wrapper.connectionClosed(), self.wrapper.openOrderEnd()))
        self.assert_failed(receipt)

    def test_read_disconnect_during_collection_no_reconnect(self):
        receipt = self.collect(lambda: self.transport.disconnect())
        self.assertIs(receipt.outcome, Outcome.INVALIDATED)
        self.assert_failed(receipt)

    def test_receipt_cannot_be_consumed_after_disconnect(self):
        receipt = self.collect(); self.transport.disconnect()
        with self.assertRaises(ReadOnlyError): self.owner._consume_receipt(receipt)

    def test_wrong_thread_does_not_issue_sdk_request(self):
        with patch.object(self.api.Client, 'reqAllOpenOrders', side_effect=AssertionError('wrong thread request')):
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaises(ReadOnlyError): pool.submit(self.owner._collect).result(2)
        self.assertTrue(self.owner._failed)

    def test_wrong_thread_receipt_consumption(self):
        receipt = self.collect()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(ReadOnlyError): pool.submit(self.owner._consume_receipt, receipt).result(2)

    def test_overlapping_owner_request_is_fail_closed(self):
        def callbacks():
            with self.assertRaises(ReadOnlyError): self.owner._collect()
            self.wrapper.openOrderEnd()
        self.assert_failed(self.collect(callbacks))

    def test_restart_cannot_reconstruct_owner(self):
        forged = object.__new__(Owner)
        with self.assertRaises((ReadOnlyError, AttributeError)): forged._collect()
        self.assertTrue(self.journal._load().replay.restart_quarantine_required)

    def test_owner_fields_cannot_be_rebound(self):
        with self.assertRaises(ReadOnlyError): self.owner._FreshOpenOrderCollectionOwner__association = ()
        with self.assertRaises(ReadOnlyError): del self.owner._FreshOpenOrderCollectionOwner__association

    def test_no_sqlite_or_append_during_collection(self):
        with (patch.object(storage._SQLiteJournalStore, '_append', side_effect=AssertionError('append')),
              patch.object(storage._SQLiteJournalStore, '_load', side_effect=AssertionError('load'))):
            receipt = self.collect()
        self.assertIs(receipt.outcome, Outcome.COMPLETED)

    def test_callback_worker_has_no_earlier_locks_storage_or_filesystem(self):
        def callbacks():
            def deliver():
                for lock in (self.coordinator._lock, self.broker._lock, self.write._lock, self.write._arrival_lock):
                    self.assertFalse(lock._is_owned())
                with (patch('builtins.open', side_effect=AssertionError('file I/O')),
                     patch('pathlib.Path.open', side_effect=AssertionError('file I/O')),
                     patch('sqlite3.connect', side_effect=AssertionError('SQLite')),
                     patch.object(journal._PaperDispatchJournalOwner, '_append_event', side_effect=AssertionError('append'))):
                    self.emit(); self.wrapper.openOrderEnd()
            with ThreadPoolExecutor(max_workers=1) as pool: pool.submit(deliver).result(2)
        receipt = self.collect(callbacks)
        self.assertIs(receipt.outcome, Outcome.COMPLETED)


def inspect_fields(value):
    return (f.name for f in fields(value))


def mutation_case(name, mutate, during):
    def test(self):
        if during:
            try:
                receipt = self.collect(lambda: (mutate(self), self.wrapper.openOrderEnd()))
            except ReadOnlyError:
                self.assertTrue(self.owner._failed)
                self.assertIsNone(self.owner._receipt)
            else:
                self.assert_failed(receipt)
        else:
            mutate(self)
            with self.assertRaises(ReadOnlyError): self.owner._collect()
            self.assertTrue(self.owner._failed)
    setattr(SuccessTests, 'test_association_' + name + ('_during' if during else '_before'), test)


MUTATIONS = {
    'coordinator': lambda s: s.change(s.coordinator, '_control', object()),
    'control': lambda s: s.change(s.broker, '_transport', object()),
    'read_transport': lambda s: s.change(s.owner, '_FreshOpenOrderCollectionOwner__association',
        s.owner._FreshOpenOrderCollectionOwner__association[:3] + (object(),) + s.owner._FreshOpenOrderCollectionOwner__association[4:]),
    'write_transport': lambda s: s.change(s.coordinator, '_write', object()),
    'equal_binding': lambda s: s.change(s.transport._journal_account_association, 'receipt', replace(s.journal._receipt)),
    'account_association': lambda s: s.change(s.transport, '_journal_account_association', replace(s.transport._journal_account_association)),
    'session': lambda s: s.change(s.journal, '_PaperDispatchJournalOwner__components',
        s.journal._PaperDispatchJournalOwner__components[:7] + (replace(s.journal._session),) + s.journal._PaperDispatchJournalOwner__components[8:]),
    'header': lambda s: s.change(s.journal._store, '_header', replace(s.journal._header)),
    'client_id': lambda s: s.change(s.write._config, 'client_id', 100),
    'read_client_id': lambda s: s.change(s.transport.config, 'client_id', 100),
    'read_generation': lambda s: s.change(s.transport, '_generation', s.transport._generation+1),
    'write_generation': lambda s: s.change(s.write, '_generation', s.write._generation+1),
    'deadline': lambda s: s.change(s.transport._journal_account_association, 'valid_until', s.clock.now+timedelta(seconds=100)),
    'read_client': lambda s: s.change(s.transport, '_client', object()),
    'write_client': lambda s: s.change(s.write, '_client', object()),
}
for name, mutate in MUTATIONS.items():
    for during in (False, True): mutation_case(name, mutate, during)


def malformed_case(name, order=None, contract=None, status='Submitted', order_id=None):
    def test(self):
        receipt = self.collect(lambda: (self.emit(order=order, contract=contract, status=status,
                                                  order_id=order_id), self.wrapper.openOrderEnd()))
        self.assertIs(receipt.outcome, Outcome.FAILED)
        self.assertEqual(receipt.count, 0)
        self.assert_failed(receipt)
    setattr(SuccessTests, 'test_malformed_' + name, test)


for name, value in (('bool_id', True), ('negative_id', -1), ('text_id', '10')):
    malformed_case(name, order={'orderId': value}, order_id=value)
for name, value in (('bool_client', True), ('negative_client', -1), ('float_client', 2.0)):
    malformed_case(name, order={'clientId': value})
for name, value in (('nan_quantity', D('NaN')), ('infinite_quantity', D('Infinity')),
                    ('zero_quantity', D(0)), ('negative_quantity', D(-1)),
                    ('bool_quantity', True), ('float_quantity', 10.0),
                    ('exponent', D('1e65')), ('digits', D('1'*65))):
    malformed_case(name, order={'totalQuantity': value})
for name, value in (('bad_conid', 0), ('bool_conid', True)):
    malformed_case(name, contract={'conId': value})
malformed_case('nonascii_symbol', contract={'symbol': 'é'})
malformed_case('oversized_reference', order={'orderRef': 'x'*101})
malformed_case('nontext_status', status=100)
malformed_case('mismatching_callback_order_id', order_id=11)


def source_case(name, encoding, order, **options):
    def test(self):
        def callbacks():
            if options.get('raw') is not None:
                self.emit(encoding=encoding, order=order, raw=options['raw'])
            else: self.emit(encoding=encoding, order=order)
            self.wrapper.openOrderEnd()
        receipt = self.collect(callbacks)
        self.assertIs(receipt.outcome, Outcome.COMPLETED)
        self.assertEqual(receipt.count, 1)
        self.assertEqual(receipt.observations[0].encoding, encoding)
        self.assertFalse(hasattr(receipt.observations[0], 'filled'))
    setattr(SuccessTests, 'test_source_' + name, test)


source_case('legacy_zero_client', 'LEGACY', {'clientId': 0})
source_case('legacy_zero_order', 'LEGACY', {'orderId': 0})
source_case('legacy_maximum_order', 'LEGACY', {'orderId': 2147483647})
source_case('protobuf_zero_client', 'PROTOBUF', {'clientId': 0})
source_case('protobuf_zero_order', 'PROTOBUF', {'orderId': 0})
source_case('protobuf_maximum_order', 'PROTOBUF', {'orderId': 2147483647})
for omitted in ('orderId', 'clientId', 'permId', 'orderRef'):
    supplied = {k:v for k,v in ORDER.items() if k != omitted}
    source_case('protobuf_absent_' + omitted, 'PROTOBUF', {},
                raw=raw_proto('orders', payload=supplied, top=None if omitted=='orderId' else 10))


def interruption_case(stage, error_type):
    def test(self):
        error = error_type('opaque interruption')
        if stage in ('_authenticate', '_wait', '_finish', '_publish'):
            context = patch.object(Owner, stage, side_effect=error)
        elif stage == 'raw': context = patch.object(read, '_reconciliation_proto_copy', side_effect=error)
        elif stage == 'decoded': context = patch.object(read, '_reconciliation_decoded_copy', side_effect=error)
        elif stage == 'completion': context = patch.object(read.ReadOnlyTWSTransport,
            '_fresh_open_order_callback', side_effect=error)
        else: context = nullcontext()
        with context:
            with self.assertRaises(error_type) as caught:
                self.collect((lambda: (_ for _ in ()).throw(error)) if stage=='sdk' else None,
                             encoding='PROTOBUF' if stage=='raw' else 'LEGACY')
        self.assertIs(caught.exception, error)
        self.assertTrue(self.owner._failed)
        self.assertIsNone(self.owner._receipt)
        self.assertTrue(self.coordinator._broker_state_reconciliation_required)
    setattr(SuccessTests, 'test_interrupt_' + stage.strip('_') + '_' + error_type.__name__, test)


for stage in ('_authenticate', 'sdk', '_wait', 'raw', 'decoded', 'completion', '_finish', '_publish'):
    for error in (KeyboardInterrupt, SystemExit, GeneratorExit): interruption_case(stage, error)


class BoundaryTests(Fixture):
    def test_bool_capture_sequence_fails_before_increment(self):
        with self.assertRaises(ReadOnlyError):
            self.collect(lambda: (self.change(self.transport, '_fresh_open_order_sequence', False),
                self.emit(), self.wrapper.openOrderEnd()))
        self.assertIsNone(self.owner._receipt)
        self.assertEqual(len(self.transport._fresh_open_order_capture.observations), 0)

    def test_bool_completion_marker_fails_before_increment(self):
        receipt = self.collect(lambda: (self.change(self.transport, '_fresh_open_order_marker', True),
                                       self.wrapper.openOrderEnd()))
        self.assertIs(receipt.outcome, Outcome.FAILED)
        self.assertFalse(receipt.completion_observed)

    def test_float_generation_in_callback_invalidates_before_copy(self):
        receipt = self.collect(lambda: (self.change(self.transport, '_generation', float(self.transport._generation)),
                                       self.emit(), self.wrapper.openOrderEnd()))
        self.assertIs(receipt.outcome, Outcome.INVALIDATED)
        self.assertEqual(receipt.count, 0)

    def test_expiry_between_start_capture_and_sdk_invocation(self):
        original = Owner._issue
        def issue(owner):
            self.clock.advance(301)
            return original(owner)
        with patch.object(Owner, '_issue', issue):
            with self.assertRaises(ReadOnlyError): self.collect()
        self.assertIs(self.owner._FreshOpenOrderCollectionOwner__sdk_result, m._SDKRequestResult.NOT_ATTEMPTED)
        self.assertIsNone(self.owner._receipt)

    def test_generation_between_start_capture_and_sdk_invocation(self):
        original = Owner._issue
        def issue(owner):
            self.change(self.write, '_generation', self.write._generation+1)
            return original(owner)
        with patch.object(Owner, '_issue', issue):
            with self.assertRaises(ReadOnlyError): self.collect()
        self.assertIs(self.owner._FreshOpenOrderCollectionOwner__sdk_result, m._SDKRequestResult.NOT_ATTEMPTED)

    def test_callback_after_sdk_return_on_worker(self):
        original = Owner._wait
        def wait(owner, deadline):
            self.assertIs(owner._FreshOpenOrderCollectionOwner__sdk_result, m._SDKRequestResult.RETURNED_NORMALLY)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(lambda: (self.emit(), self.wrapper.openOrderEnd())).result(2)
            return original(owner, deadline)
        with patch.object(Owner, '_wait', wait): receipt = self.collect(count=0, end=False)
        self.assertIs(receipt.outcome, Outcome.COMPLETED)
        self.assertEqual(receipt.count, 1)

    def test_expiry_after_end_before_publication(self):
        original = Owner._finish
        def finish(owner):
            self.clock.advance(301)
            return original(owner)
        with patch.object(Owner, '_finish', finish): receipt = self.collect()
        self.assertIs(receipt.outcome, Outcome.INVALIDATED)

    def test_generation_after_end_before_publication(self):
        original = Owner._finish
        def finish(owner):
            self.change(self.write, '_generation', self.write._generation+1)
            return original(owner)
        with patch.object(Owner, '_finish', finish): receipt = self.collect()
        self.assertIs(receipt.outcome, Outcome.INVALIDATED)

    def test_publication_constructor_failure_leaves_no_receipt(self):
        with patch.object(m, '_FreshOpenOrderReceipt', side_effect=ValueError('private text')):
            with self.assertRaises(ReadOnlyError): self.collect()
        self.assertIsNone(self.owner._receipt)
        self.assertTrue(self.owner._failed)

    def test_publication_failure_has_no_visible_success_pointer(self):
        def publish(owner, receipt):
            self.assertIsNone(owner._receipt)
            raise ValueError('private text')
        with patch.object(Owner, '_publish', publish):
            with self.assertRaises(ReadOnlyError): self.collect()
        self.assertIsNone(self.owner._receipt)

    def test_no_pending_barrier_prevents_factory_or_request(self):
        self.coordinator._broker_state_reconciliation_required = False
        with self.assertRaises(ReadOnlyError): self.owner._collect()
        self.assertFalse(self.write_calls())

    def test_equal_reconstructed_journal_owner_rejected(self):
        clone = object.__new__(journal._PaperDispatchJournalOwner)
        for slot in journal._PaperDispatchJournalOwner.__slots__:
            object.__setattr__(clone, '_PaperDispatchJournalOwner'+slot,
                               getattr(self.journal, '_PaperDispatchJournalOwner'+slot))
        with self.assertRaises(ReadOnlyError): m._create_fresh_open_order_collection_owner(clone)

    def test_callback_never_acquires_earlier_plane_locks(self):
        class Forbidden:
            def __enter__(self): raise AssertionError('earlier-plane callback lock')
            def acquire(self, *args, **kwargs): raise AssertionError('earlier-plane callback lock')
        def callbacks():
            c_lock, control_lock = self.coordinator._lock, self.broker._lock
            self.coordinator._lock = self.broker._lock = Forbidden()
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(lambda: (self.emit(), self.wrapper.openOrderEnd())).result(2)
            finally:
                self.coordinator._lock, self.broker._lock = c_lock, control_lock
        self.assertIs(self.collect(callbacks).outcome, Outcome.COMPLETED)

    def test_owner_store_quarantine_prevents_issue(self):
        self.change(self.journal._store, '_quarantined', True)
        with self.assertRaises(ReadOnlyError): self.owner._collect()

    def test_owner_store_poison_prevents_issue(self):
        self.change(self.journal._store, '_poisoned', True)
        with self.assertRaises(ReadOnlyError): self.owner._collect()

    def test_no_connect_refresh_reset_or_auth_issuance(self):
        with (patch.object(read.ReadOnlyTWSTransport, 'connect', side_effect=AssertionError('connect')),
              patch.object(read.ReadOnlyTWSTransport, '_reset', side_effect=AssertionError('reset')),
              patch.object(read.ReadOnlyTWSTransport, '_issue_journal_account_binding', side_effect=AssertionError('renew')),
              patch.object(type(self.broker), 'refresh', side_effect=AssertionError('refresh'))):
            self.assertIs(self.collect().outcome, Outcome.COMPLETED)

    def test_malformed_raw_presence_failed_not_empty(self):
        raw = raw_proto('orders', contract={})
        self.assert_failed(self.collect(lambda: (self.wrapper.openOrderProtoBuf(raw), self.wrapper.openOrderEnd())))

    def test_predecoded_raw_before_issue_cannot_be_used(self):
        self.wrapper.openOrderProtoBuf(raw_proto('orders'))
        receipt = self.collect(count=0)
        self.assertEqual(receipt.count, 0)
        self.assertIsNone(self.transport._fresh_open_order_capture.envelope)

    def test_capture_list_substitution_cannot_publish_success(self):
        def callbacks():
            self.emit()
            self.transport._fresh_open_order_capture.observations.clear()
            self.wrapper.openOrderEnd()
        with self.assertRaises(ReadOnlyError): self.collect(callbacks)
        self.assertIsNone(self.owner._receipt)
        self.assertTrue(self.owner._failed)

    def test_persistence_cursor_boundary_change_invalidates(self):
        receipt = self.collect(lambda: (self.change(self.journal,
            '_PaperDispatchJournalOwner__persistence_cursor', 1), self.wrapper.openOrderEnd()))
        self.assertIs(receipt.outcome, Outcome.INVALIDATED)

    def test_fixed_deadline_exactly_reached_prevents_publication(self):
        original = Owner._publish
        def publish(owner, receipt):
            self.enterContext(patch.object(m, '_clock', return_value=owner._FreshOpenOrderCollectionOwner__monotonic_deadline))
            return original(owner, receipt)
        with patch.object(Owner, '_publish', publish):
            with self.assertRaises(ReadOnlyError): self.collect()
        self.assertIsNone(self.owner._receipt)

    def test_valid_maximum_bounded_quantity_shape(self):
        quantity = D('9'*64 + 'e64')
        receipt = self.collect(lambda: (self.emit(order={'totalQuantity': quantity}), self.wrapper.openOrderEnd()))
        self.assertEqual(receipt.observations[0].quantity.as_tuple(), quantity.as_tuple())

    def test_no_public_export_or_api_change(self):
        self.assertEqual(m.__all__, ())
        self.assertFalse(hasattr(self.broker, 'fresh_open_order_collection'))
        self.assertFalse(hasattr(self.transport._snapshot, 'fresh_open_order_collection'))

    def test_unknown_account_mode_and_none_public_snapshot(self):
        self.collect()
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.transport._snapshot.account.mode)

    def test_only_open_order_request_no_additional_collections(self):
        before = list(self.api.calls)
        self.collect()
        self.assertEqual(self.api.calls, before)  # Patched single request; no other SDK call.
        self.assertFalse(self.write_calls())

    def test_foreign_client_copied_reference_and_matching_quantity_not_adopted(self):
        receipt = self.collect(lambda: (self.emit(order={'clientId': 100, 'orderRef': self.order.client_order_id}),
                                       self.wrapper.openOrderEnd()))
        self.assertEqual(receipt.observations[0].client_id, 100)
        self.assertEqual(self.safety(), self.before)

    def test_same_reference_different_numeric_id_retained(self):
        def callbacks():
            self.emit(); self.emit(order={'orderId': 99}); self.wrapper.openOrderEnd()
        receipt = self.collect(callbacks)
        self.assertEqual([e.order_id for e in receipt.observations], [10, 99])

    def test_same_order_id_conflicting_contract_retained(self):
        receipt = self.collect(lambda: (self.emit(), self.emit(contract={'conId': 100}), self.wrapper.openOrderEnd()))
        self.assertEqual([e.con_id for e in receipt.observations], [17, 100])

    def test_raw_orphan_cannot_complete(self):
        receipt = self.collect(lambda: (self.wrapper.openOrderProtoBuf(raw_proto('orders')), self.wrapper.openOrderEnd()))
        self.assert_failed(receipt)

    def test_raw_decoded_conflict_cannot_complete(self):
        receipt = self.collect(lambda: (self.emit(encoding='PROTOBUF', order={'totalQuantity': D(11)},
            raw=raw_proto('orders')), self.wrapper.openOrderEnd()))
        self.assert_failed(receipt)

    def test_raw_wrong_thread_follower_cannot_complete(self):
        def callbacks():
            self.wrapper.openOrderProtoBuf(raw_proto('orders'))
            with ThreadPoolExecutor(max_workers=1) as pool: pool.submit(self.emit).result(2)
            self.wrapper.openOrderEnd()
        self.assert_failed(self.collect(callbacks))

    def test_raw_intervening_status_is_failed(self):
        def callbacks():
            self.wrapper.openOrderProtoBuf(raw_proto('orders'))
            self.wrapper.orderStatus(10, 'Submitted', D(0), D(10), 0, 77, 0, 0, 2, '', 0)
            self.wrapper.openOrderEnd()
        self.assert_failed(self.collect(callbacks))

    def test_two_raw_precursors_are_failed(self):
        self.assert_failed(self.collect(lambda: (self.wrapper.openOrderProtoBuf(raw_proto('orders')),
            self.wrapper.openOrderProtoBuf(raw_proto('orders')), self.wrapper.openOrderEnd())))

    def test_no_protocol_downgrade(self):
        self.assert_failed(self.collect(lambda: (self.emit(encoding='PROTOBUF'), self.emit(), self.wrapper.openOrderEnd())))

    def test_write_callback_arrival_boundary_invalidates(self):
        receipt = self.collect(lambda: (self.write._callback(self.write._generation, 'next', 100), self.wrapper.openOrderEnd()))
        self.assertIs(receipt.outcome, Outcome.INVALIDATED)

    def test_journal_tail_change_invalidates_without_new_append(self):
        original = self.journal._PaperDispatchJournalOwner__history
        receipt = self.collect(lambda: (self.change(self.journal, '_PaperDispatchJournalOwner__history', original+((),)),
                                       self.wrapper.openOrderEnd()))
        self.assertIs(receipt.outcome, Outcome.INVALIDATED)

    def test_receipt_tampering_is_rejected(self):
        receipt = self.collect()
        object.__setattr__(receipt, 'scope', 'CURRENT')
        with self.assertRaises(ReadOnlyError): self.owner._consume_receipt(receipt)

    def test_observation_tampering_is_rejected(self):
        receipt = self.collect()
        object.__setattr__(receipt.observations[0], 'client_id', 99)
        with self.assertRaises(ReadOnlyError): self.owner._consume_receipt(receipt)

    def test_restart_quarantine_and_no_new_entry_after_success(self):
        receipt = self.collect()
        self.owner._consume_receipt(receipt)
        self.assertTrue(self.journal._load().replay.restart_quarantine_required)
        self.invoke()
        self.assertFalse(self.write_calls())

    def test_authority_fields_are_absent(self):
        receipt = self.collect()
        for name in ('currentness', 'current_lifecycle', 'current_remainder', 'order_owner',
            'position_owner', 'protection', 'cancel', 'replace', 'exit', 'recovery',
            'clear_barrier', 'restart_adoption', 'trading_authority', 'live_supported'):
            self.assertFalse(hasattr(receipt, name))

    def test_no_durability_or_wire_import(self):
        tree = ast.parse(Path(m.__file__).read_text())
        modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        self.assertNotIn('paper_execution_journal_sqlite', modules)
        self.assertNotIn('ibkr_readonly_wire', modules)
        self.assertFalse(hasattr(m, '_SQLiteJournalStore'))


def primitive_boundary_case(name, component, attribute, value):
    def test(self):
        original = Owner._publish
        def publish(owner, receipt):
            self.change(component(self), attribute, value(self))
            return original(owner, receipt)
        with patch.object(Owner, '_publish', publish):
            with self.assertRaises(ReadOnlyError): self.collect()
        self.assertIsNone(self.owner._receipt)
        self.assertTrue(self.owner._failed)
    setattr(BoundaryTests, 'test_exact_primitive_' + name, test)


for case in (
    ('bool_write_generation', lambda s:s.write, '_generation', lambda s:True),
    ('float_write_generation', lambda s:s.write, '_generation', lambda s:float(s.write._generation)),
    ('float_read_generation', lambda s:s.transport, '_generation', lambda s:float(s.transport._generation)),
    ('float_placing_client', lambda s:s.write._config, 'client_id', lambda s:float(s.write._config.client_id)),
    ('int_barrier', lambda s:s.coordinator, '_broker_state_reconciliation_required', lambda s:1),
    ('bool_zero_watermark', lambda s:s.write, '_callback_sequence', lambda s:False)):
    primitive_boundary_case(*case)
