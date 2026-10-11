"""C3C4C adversarial write-ahead integration; real SQLite, fake SDK only."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal
import inspect
from pathlib import Path
import sqlite3
from threading import Event, current_thread
from unittest.mock import patch

from tradingbot_broker import paper_dispatch as dispatch
from tradingbot_broker import paper_dispatch_journal as integration
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker.paper_execution import PaperExecutionAuthorizationStatus as Auth
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests.test_paper_dispatch import State
from tests.test_paper_callback_confirmation import CallbackFixture
from tests.dispatch_journal_fixtures import request, permission


Owner = integration._PaperDispatchJournalOwner


class JournalFixture(CallbackFixture):
    def history(self):
        return tuple(record.payload for record in self.journal._load().records)

    def fail_append(self, kind, error, *, after=False):
        original = Owner._append_event
        def append(owner, event, **kwargs):
            if type(event) is kind:
                if after:
                    original(owner, event, **kwargs)
                raise error
            return original(owner, event, **kwargs)
        return patch.object(Owner, '_append_event', append)

    def inhibited(self, result, calls=0):
        self.assertIs(result.state, State.OUTCOME_UNKNOWN)
        self.assertEqual(len(self.write_calls()), calls)
        self.assertTrue(self.journal._inhibited)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertTrue(self.coordinator._reconciliation_required)
        self.assertTrue(self.broker.reconciliation_required)
        self.assertIs(self.broker._paper_authority.status, Auth.INVALIDATED)


class OwnerTests(JournalFixture):
    def test_exact_owner_session_and_thread(self):
        self.build()
        self.assertIs(self.coordinator._journal_owner, self.journal)
        self.assertIs(self.journal._thread, current_thread())
        self.assertIs(self.journal._header, self.journal._store._header)
        self.assertEqual(self.history(), (self.journal._session,))
        self.assertEqual((self.journal._session.client_id, self.journal._session.write_generation), (2, 1))
        self.assertEqual(self.transport._validate_journal_account_binding(self.journal._receipt),
                         self.journal._header.account_binding)

    def test_no_owner_cannot_invoke(self):
        with patch('tests.test_paper_dispatch._create_paper_dispatch_journal_owner', return_value=None):
            # Fixture cleanup is installed only for a published owner.
            self.build()
        result = self.invoke()
        self.assertIs(result.state, State.DENIED)
        self.assertFalse(self.write_calls())
        self.assertEqual(self.write._lifetime_order_floor, 10)

    def test_owner_constructor_is_not_authority(self):
        with self.assertRaises(ReadOnlyError): Owner()

    def test_second_setup_never_creates_second_store(self):
        self.build()
        path = Path(self.temporary.name) / 'second.db'
        with self.assertRaises(ReadOnlyError):
            integration._create_paper_dispatch_journal_owner(self.coordinator, path)
        self.assertFalse(path.exists())
        self.assertIs(self.coordinator._journal_owner, self.journal)

    def test_owner_association_cannot_be_replaced(self):
        self.build()
        with self.assertRaises(AttributeError): self.coordinator._journal_owner = None
        with self.assertRaises(ReadOnlyError):
            self.coordinator._PaperOrderDispatchCoordinator__journal_owner = None
        with self.assertRaises(ReadOnlyError): self.coordinator._claim_journal_owner(self.journal)

    def test_forged_owner_cannot_claim_coordinator(self):
        self.build()
        clone = object.__new__(Owner)
        with self.assertRaises(ReadOnlyError): self.coordinator._claim_journal_owner(clone)

    def test_wrong_thread_dispatch_never_touches_sqlite(self):
        self.build()
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite reached')) as begin:
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(self.invoke).result(timeout=5)
        begin.assert_not_called()
        self.inhibited(result)
        self.assertIn(self.order.client_order_id, self.coordinator._attempts)

    def test_same_thread_guard_is_not_disabled(self):
        source = inspect.getsource(storage._connect)
        self.assertNotIn('check_same_thread=False', source)

    def test_existing_path_is_never_adopted(self):
        self.build()
        original = tuple(self.history())
        with self.assertRaises(ReadOnlyError):
            integration._create_paper_dispatch_journal_owner(self.coordinator,
                Path(self.temporary.name) / 'dispatch-1.db')
        self.assertEqual(self.history(), original)

    def test_owner_has_no_public_methods(self):
        self.assertEqual([name for name in dir(Owner) if not name.startswith('_')], [])

    def test_opaque_receipt_header_and_owner(self):
        self.build()
        for value in (self.journal, self.journal._receipt, self.journal._header):
            self.assertNotIn('anonymous-fixture', repr(value))
            self.assertNotIn(self.order.client_order_id, repr(value))
        self.assertIs(self.journal._header.account_mode, AccountMode.UNKNOWN)


def owner_slot_case(slot):
    def test(self):
        self.build()
        with self.assertRaises(ReadOnlyError):
            setattr(self.journal, '_PaperDispatchJournalOwner' + slot, object())
        with self.assertRaises(ReadOnlyError):
            delattr(self.journal, '_PaperDispatchJournalOwner' + slot)
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)
    return test


for slot in ('__association', '__components', '__identity', '__append', '__load', '__close'):
    setattr(OwnerTests, 'test_immutable_owner' + slot, owner_slot_case(slot))


def wrong_thread_operation(name):
    def test(self):
        self.build()
        action = {'load': self.journal._load, 'close': self.journal._close,
                  'append': lambda: self.journal._append_event(j._AttemptConsumed(
                      self.journal._session.session_id, 'foreign-thread'))}[name]
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite reached')):
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaises(ReadOnlyError): pool.submit(action).result(timeout=5)
        self.assertFalse(self.write_calls())
        self.assertEqual(len(self.history()), 1)
    return test


for name in ('load', 'close', 'append'):
    setattr(OwnerTests, 'test_wrong_thread_' + name, wrong_thread_operation(name))


class OrderingTests(JournalFixture):
    def test_sequential_owner_thread_attempts_cannot_create_second_order(self):
        with self.owner_worker() as pool:
            other = request('owner-thread-second')
            first = pool.submit(self.invoke)
            second = pool.submit(self.invoke, other, permission(other))
            self.assertIs(first.result(timeout=5).state, State.DISPATCHED_PENDING_CONFIRMATION)
            self.assertIs(second.result(timeout=5).state, State.DENIED)
            self.assertEqual(len(self.write_calls()), 1)
            self.assertEqual(self.write._lifetime_order_floor, 11)
            events = pool.submit(self.history).result(timeout=5)
            self.assertEqual([type(event) for event in events], [j._SessionRegistered,
                j._AttemptConsumed, j._NumericOrderIdReserved, j._InvocationMayOccur,
                j._SdkReturnedNormally, j._AttemptConsumed, j._AttemptClosedWithoutInvocation])
            self.assertIsNone(events[-2].order)

    def test_durable_marker_before_commitment_and_sdk(self):
        self.build()
        original = self.write._consume_commitment
        def committed(value):
            proof = self.coordinator._prepared_journal_proof
            self.assertIsInstance(proof.committed.record.payload, j._InvocationMayOccur)
            self.assertEqual(proof.committed.record.sequence, 4)
            self.assertFalse(self.journal._store._connection.in_transaction)
            original(value)
        self.write._consume_commitment = committed
        original_sdk = self.client.placeOrder
        def sdk(*args):
            self.assertFalse(self.write._arrival_lock._is_owned())
            self.assertFalse(self.journal._store._connection.in_transaction)
            self.assertIsInstance(self.history()[-1], j._InvocationMayOccur)
            return original_sdk(*args)
        self.client.placeOrder = sdk
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)
        self.assertEqual([type(event) for event in self.history()], [j._SessionRegistered,
            j._AttemptConsumed, j._NumericOrderIdReserved, j._InvocationMayOccur, j._SdkReturnedNormally])
        self.assertEqual(self.history()[2].order_id, 10)

    def test_every_storage_call_is_outside_arrival(self):
        self.build()
        original = Owner._append_event
        def append(owner, event, **kwargs):
            self.assertFalse(self.write._arrival_lock._is_owned())
            self.coordinator._require_outer_locks()
            return original(owner, event, **kwargs)
        with patch.object(Owner, '_append_event', append):
            self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)

    def test_complete_context_is_internal_and_reference_only(self):
        self.build()
        self.invoke()
        event = self.history()[1]
        self.assertEqual(event.strategy.bod_equity, Decimal(60))
        self.assertEqual(event.strategy.risk_budget, Decimal('.6'))
        self.assertEqual(event.strategy.original_quantity, Decimal(10))
        self.assertEqual(event.contract.con_id, 17)
        self.assertEqual(event.strategy.strategy_version, 'v1.0')
        self.assertEqual((event.risk.current_equity, event.risk.current_exposure,
                          event.risk.available_cash), (None, None, None))
        self.assertFalse(any(type(e) is j._ExecutionObserved for e in self.history()))

    def test_caller_context_argument_not_accepted(self):
        self.build()
        with self.assertRaises(TypeError):
            self.coordinator._dispatch_entry(self.order, self.permission, self.capability, context=object())
        self.assertFalse(self.write_calls())

    def test_identifier_only_denial_cannot_upgrade(self):
        self.build()
        self.broker.pause_new_entries(self.clock())
        self.assert_denied(self.invoke())
        consumed = self.history()[1]
        self.assertIsNone(consumed.order)
        self.assertIsInstance(self.history()[2], j._AttemptClosedWithoutInvocation)
        self.broker.resume_new_entries(self.clock())
        self.assert_denied(self.invoke())
        self.assertEqual(len(self.history()), 3)

    def test_sdk_normal_return_is_not_acceptance(self):
        self.build()
        self.invoke()
        replay = self.journal._load().replay
        self.assertTrue(replay.attempts[0].sdk_returned_normally)
        self.assertFalse(replay.attempts[0].broker_observed)
        self.assertTrue(replay.attempts[0].broker_control_unresolved)
        self.assertTrue(replay.restart_quarantine_required)

    def test_post_marker_denial_has_no_clean_closure(self):
        self.build()
        original = self.coordinator._check_reservation
        count = []
        def check(*args):
            original(*args)
            if self.coordinator._prepared_journal_proof is not None:
                count.append(True)
                raise ReadOnlyError('LOCAL_FAILURE')
        self.coordinator._check_reservation = check
        self.inhibited(self.invoke())
        self.assertTrue(count)
        self.assertTrue(any(type(e) is j._InvocationMayOccur for e in self.history()))
        self.assertFalse(any(type(e) is j._AttemptClosedWithoutInvocation for e in self.history()))

    def test_reserved_id_never_released_on_storage_failure(self):
        self.build()
        with self.fail_append(j._NumericOrderIdReserved, storage._StorageError(storage._Code.STORAGE_FAILURE)):
            self.inhibited(self.invoke())
        self.assertEqual(self.write._lifetime_order_floor, 11)
        before = self.history()
        self.inhibited(self.invoke(request('second'), permission(request('second'))))
        self.assertEqual(self.history(), before)

    def test_proof_reconstruction_fails_final_validation(self):
        self.build()
        original = self.write._stage_commitment
        def stage(res):
            result = original(res)
            self.coordinator._prepared_journal_proof = replace(self.coordinator._prepared_journal_proof)
            return result
        self.write._stage_commitment = stage
        self.inhibited(self.invoke())

    def test_captured_normal_return_hook_cannot_be_replaced_on_instance(self):
        self.build()
        self.coordinator._sdk_returned = lambda *args: None
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)
        self.assertIsInstance(self.history()[-1], j._SdkReturnedNormally)


class StorageFailureTests(JournalFixture):
    def test_real_busy_writer_blocks_sdk(self):
        self.build()
        with closing(sqlite3.connect(Path(self.temporary.name) / 'dispatch-1.db', timeout=0)) as connection:
            connection.execute('BEGIN IMMEDIATE')
            self.inhibited(self.invoke())
            connection.rollback()
        self.assertFalse(self.write._write_invocation_started)

    def test_actual_unknown_commit_blocks_sdk(self):
        self.build()
        original = storage._commit
        def commit(connection):
            original(connection)
            raise RuntimeError('discarded storage text')
        with patch.object(storage, '_commit', commit):
            self.inhibited(self.invoke())
        self.assertTrue(self.journal._store._poisoned)
        self.assertFalse(self.write._write_invocation_started)

    def test_corrupt_database_blocks_sdk(self):
        self.build()
        with closing(sqlite3.connect(Path(self.temporary.name) / 'dispatch-1.db')) as connection:
            connection.execute('PRAGMA writable_schema=ON')
            connection.execute("UPDATE sqlite_master SET sql='invalid' WHERE name='records'")
            connection.commit()
        self.inhibited(self.invoke())

    def test_unexpected_valid_history_blocks_sdk(self):
        self.build()
        self.journal._store._append(j._SessionRegistered('session-'+'e'*32, 3, 1))
        self.inhibited(self.invoke())

    def test_closed_store_blocks_sdk(self):
        self.build()
        self.journal._close()
        self.inhibited(self.invoke())

    def test_semantic_quarantine_retains_offending_record_and_blocks_sdk(self):
        self.build()
        offending = j._InvocationMayOccur('not-a-consumed-attempt')
        with self.assertRaises(storage._StorageError) as caught:
            self.journal._store._append(offending)
        self.assertEqual(caught.exception.code, storage._Code.QUARANTINED.value)
        self.inhibited(self.invoke())
        with closing(sqlite3.connect(Path(self.temporary.name) / 'dispatch-1.db')) as connection:
            self.assertEqual(connection.execute('SELECT quarantined FROM store_state').fetchone(), (1,))
            rows = connection.execute('SELECT encoded FROM records ORDER BY sequence').fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(storage._decode(rows[-1][0], j._JournalRecord).payload, offending)

    def unknown_at(self, event_type, calls):
        self.build()
        original_append, original_commit = Owner._append_event, storage._commit
        def commit(connection):
            original_commit(connection)
            raise RuntimeError('discarded post-commit text')
        def append(owner, event, **kwargs):
            if type(event) is event_type:
                with patch.object(storage, '_commit', commit):
                    return original_append(owner, event, **kwargs)
            return original_append(owner, event, **kwargs)
        with patch.object(Owner, '_append_event', append):
            self.inhibited(self.invoke(), calls=calls)
        self.assertTrue(self.journal._store._poisoned)
        with closing(sqlite3.connect(Path(self.temporary.name) / 'dispatch-1.db')) as connection:
            events = [type(storage._decode(row[0], j._JournalRecord).payload)
                      for row in connection.execute('SELECT encoded FROM records ORDER BY sequence')]
        self.assertIn(event_type, events)
        self.assertNotIn(j._AttemptClosedWithoutInvocation, events)

    def test_actual_unknown_commit_of_marker_prevents_commitment(self):
        self.unknown_at(j._InvocationMayOccur, 0)
        self.assertFalse(self.write._commitment_consumed)
        self.assertFalse(self.write._write_invocation_started)

    def test_actual_unknown_commit_after_sdk_remains_uncertain(self):
        self.unknown_at(j._SdkReturnedNormally, 1)
        self.assertTrue(self.write._write_invocation_started)

    def test_fabricated_committed_marker_cannot_replace_durable_append(self):
        original = storage._SQLiteJournalStore._append
        def append(store, event):
            if type(event) is j._InvocationMayOccur:
                loaded = store._load()
                header = store._header
                record = j._JournalRecord(len(loaded.records)+1, header.schema_version,
                    header.journal_id, header.account_binding, event)
                return storage._CommittedRecord(record, 'a'*64)
            return original(store, event)
        with patch.object(storage._SQLiteJournalStore, '_append', append): self.build()
        self.inhibited(self.invoke())
        self.assertFalse(any(type(event) is j._InvocationMayOccur for event in self.history()))
        self.assertFalse(self.write._write_invocation_started)


def storage_failure(kind, code):
    def test(self):
        self.build()
        with self.fail_append(kind, storage._StorageError(code)):
            self.inhibited(self.invoke())
        self.assertFalse(self.write._write_invocation_started)
        self.assertEqual(self.write._lifetime_order_floor, 11)
        self.assertIn(self.order.client_order_id, self.coordinator._attempts)
        self.assertFalse(self.write_calls())
    return test


for kind in (j._AttemptConsumed, j._NumericOrderIdReserved, j._InvocationMayOccur):
    for code in (storage._Code.BUSY, storage._Code.STORAGE_FAILURE, storage._Code.POISONED,
                 storage._Code.QUARANTINED, storage._Code.COMMIT_OUTCOME_UNKNOWN,
                 storage._Code.CLOSED, storage._Code.CORRUPT, storage._Code.INVALID_EVENT,
                 storage._Code.HEADER_MISMATCH):
        setattr(StorageFailureTests, 'test_'+kind.__name__+'_'+code.value, storage_failure(kind, code))


class PostDatabaseTests(JournalFixture):
    def during_marker(self, action):
        self.build()
        original = Owner._append_event
        def append(owner, event, **kwargs):
            result = original(owner, event, **kwargs)
            if type(event) is j._InvocationMayOccur: action(self)
            return result
        with patch.object(Owner, '_append_event', append):
            self.inhibited(self.invoke())
        self.assertFalse(self.write._write_invocation_started)


def post_database(name, action):
    def test(self): self.during_marker(action)
    setattr(PostDatabaseTests, 'test_post_database_' + name, test)


for name, action in (
    ('authorization_wall', lambda s: setattr(s.clock, 'now', s.capability.expires_at)),
    ('authorization_monotonic', lambda s: setattr(s.monotonic, 'value', s.broker._paper_authority._deadline)),
    ('risk_expiry', lambda s: setattr(s.clock, 'now', s.permission.valid_until)),
    ('order_expiry', lambda s: setattr(s.clock, 'now', s.order.expires_at)),
    ('account_stale', lambda s: setattr(s.clock, 'now', s.broker._account.valid_until)),
    ('read_generation', lambda s: setattr(s.transport, '_generation', s.transport._generation+1)),
    ('write_generation', lambda s: setattr(s.write, '_generation', s.write._generation+1)),
    ('receipt_destroyed', lambda s: setattr(s.transport, '_journal_account_association', None)),
    ('account_replaced', lambda s: setattr(s.transport, '_accounts', ['different-anonymous-fixture'])),
    ('disarm', lambda s: s.broker.disarm_paper_execution()),
    ('pause', lambda s: s.broker.pause_new_entries(s.clock())),
    ('emergency', lambda s: s.broker.emergency_stop(s.clock())),
    ('control_reconciliation', lambda s: setattr(s.broker, '_reconciliation_required', True)),
    ('write_reconciliation', lambda s: s.write._latch_reconciliation()),
    ('pending_confirmation', lambda s: setattr(s.coordinator, '_pending_confirmation', True)),
    ('broker_state_barrier', lambda s: setattr(s.coordinator, '_broker_state_reconciliation_required', True)),
    ('input_changed', lambda s: object.__setattr__(s.order, 'quantity', Decimal(11))),
    ('position_changed', lambda s: object.__setattr__(s.position, 'quantity', 11)),
    ('approval_changed', lambda s: object.__setattr__(s.approval, 'setup_id', 'changed-setup')),
    ('contract_expiry', lambda s: setattr(s.clock, 'now', s.contracts[0].valid_until)),
    ('closed_arrival', lambda s: s.client.wrapper.connectionClosed()),
    ('error_arrival', lambda s: s.client.wrapper.error(-1, 321, 'discarded broker text')),
    ('next_id_arrival', lambda s: s.client.wrapper.nextValidId(200)),
): post_database(name, action)


class ArrivalDuringCommitTests(JournalFixture):
    def arrival_during_commit(self, kind):
        self.build()
        announced, finished = Event(), Event()
        owner_thread = current_thread()
        real_arrival = self.write._arrival_lock
        commits, callbacks, observations = [], [], []
        class Announce:
            def __enter__(lock): real_arrival.acquire(); return lock
            def __exit__(lock, *args):
                real_arrival.release()
                if current_thread() is not owner_thread: announced.set()
            def _is_owned(lock): return real_arrival._is_owned()
        self.write._arrival_lock = Announce()
        def callback():
            try:
                order_id = self.write._active_reservation.order_id
                if kind == 'open': self.open(order_id=order_id)
                elif kind == 'open_foreign_ref':
                    self.open(order_id=order_id, order_values={'orderRef': 'different-order'})
                elif kind == 'status': self.status(order_id=order_id)
                elif kind == 'execution': self.execution(order_id=order_id)
                elif kind == 'error': self.client.wrapper.error(-1, 321, 'discarded text')
                elif kind == 'closed': self.client.wrapper.connectionClosed()
                elif kind == 'next': self.client.wrapper.nextValidId(order_id+100)
                elif kind == 'malformed': self.status(order_id=order_id, filled=Decimal('NaN'))
                else: raise AssertionError('Unknown test arrival')
            finally: finished.set()
        original_commit, original_begin = storage._commit, storage._SQLiteJournalStore._begin
        def begin(store, *args):
            self.assertIs(current_thread(), owner_thread, 'Callback entered SQLite')
            self.assertFalse(self.write._arrival_lock._is_owned())
            return original_begin(store, *args)
        with ThreadPoolExecutor(max_workers=1) as pool:
            def commit(connection):
                original_commit(connection)
                commits.append(True)
                if len(commits) == 3:  # Invocation marker append, before it returns.
                    before = self.write._arrival_sequence
                    callbacks.append(pool.submit(callback))
                    announced_in_time = announced.wait(5)
                    observations.append((connection.in_transaction,
                        self.write._arrival_lock._is_owned(), announced_in_time,
                        self.write._arrival_sequence > before,
                        kind not in ('error', 'closed') or not finished.is_set()))
            with patch.object(storage, '_commit', commit), \
                    patch.object(storage._SQLiteJournalStore, '_begin', begin):
                self.inhibited(self.invoke())
            self.assertEqual(len(callbacks), 1)
            callbacks[0].result(timeout=5)
        self.assertTrue(finished.is_set())
        self.assertEqual(observations, [(False, False, True, True, True)])
        self.assertFalse(self.journal._store._poisoned)
        self.assertFalse(self.write._write_invocation_started)
        self.assertTrue(any(type(event) is j._InvocationMayOccur for event in self.history()))
        self.assertFalse(any(type(event) is j._AttemptClosedWithoutInvocation for event in self.history()))


def arrival_during_commit(kind):
    def test(self): self.arrival_during_commit(kind)
    return test


for kind in ('open', 'open_foreign_ref', 'status', 'execution', 'error', 'closed', 'next', 'malformed'):
    setattr(ArrivalDuringCommitTests, 'test_commit_latency_' + kind, arrival_during_commit(kind))


class SdkOutcomeTests(JournalFixture):
    def test_pending_publication_failure_inhibits_registered_owner(self):
        self.build()
        with patch.object(dispatch, '_PendingOrder', side_effect=ReadOnlyError('PUBLICATION_FAILED')):
            self.inhibited(self.invoke(), calls=1)
        self.assertIsInstance(self.history()[-1], j._SdkReturnedNormally)
        self.inhibited(self.invoke(), calls=1)

    def test_result_publication_failure_inhibits_before_propagation(self):
        self.build()
        with patch.object(dispatch, '_BridgeResult', side_effect=ReadOnlyError('PUBLICATION_FAILED')):
            with self.assertRaises(ReadOnlyError): self.invoke()
        self.assertTrue(self.journal._inhibited)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertTrue(self.coordinator._reconciliation_required)
        self.assertTrue(self.broker.reconciliation_required)
        self.assertIs(self.broker._paper_authority.status, Auth.INVALIDATED)
        self.assertEqual(len(self.write_calls()), 1)
        self.assertIsInstance(self.history()[-1], j._SdkReturnedNormally)

    def test_sdk_exception_safety_before_diagnostic(self):
        self.build()
        self.client.placeOrder = lambda *args: (_ for _ in ()).throw(RuntimeError('discarded SDK text'))
        original = Owner._append_event
        observed = []
        def append(owner, event, **kwargs):
            if type(event) is j._ReconciliationRequired:
                observed.append((self.write._requires_reconciliation(),
                    self.coordinator._reconciliation_required, self.broker.reconciliation_required,
                    self.broker._paper_authority.status is Auth.INVALIDATED))
            return original(owner, event, **kwargs)
        with patch.object(Owner, '_append_event', append): self.inhibited(self.invoke())
        self.assertTrue(observed)
        self.assertTrue(all(value == (True, True, True, True) for value in observed))
        self.assertTrue(any(type(e) is j._ReconciliationRequired and
                            e.reason is j._ReconciliationReason.SDK_EXCEPTION for e in self.history()))

    def test_post_sdk_append_failure_is_unknown_without_retry(self):
        self.build()
        with self.fail_append(j._SdkReturnedNormally, storage._StorageError(storage._Code.STORAGE_FAILURE)):
            self.inhibited(self.invoke(), calls=1)
        self.assertFalse(any(type(e) is j._SdkReturnedNormally for e in self.history()))
        self.inhibited(self.invoke(), calls=1)

    def test_normal_return_record_precedes_result_construction(self):
        self.build()
        original = dispatch._DispatchResult
        # Result class lives in transport; observe publication there.
        from tradingbot_broker import ibkr_paper_transport as paper
        def result(*args, **kwargs):
            self.assertIsInstance(self.history()[-1], j._SdkReturnedNormally)
            return original(*args, **kwargs)
        with patch.object(paper, '_DispatchResult', result):
            self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)

    def test_callback_capture_during_sdk_is_not_durable(self):
        self.build()
        original = self.client.placeOrder
        def invoke(order_id, contract, order):
            self.client.wrapper.orderStatus(order_id, 'Submitted', Decimal(0), Decimal(10),
                                             0.0, 0, 0, 0.0, 2, '', 0.0)
            original(order_id, contract, order)
        self.client.placeOrder = invoke
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)
        self.assertGreater(self.write._callback_sequence, 0)
        self.assertFalse(any(type(e) in (j._OrderStatusObserved, j._OpenOrderObserved,
                                      j._ExecutionObserved, j._CallbackProgress) for e in self.history()))

    def test_sdk_exception_is_invoked_at_most_once(self):
        self.build()
        entered = []
        def fail(*args):
            entered.append(True)
            raise RuntimeError('discarded SDK text')
        self.client.placeOrder = fail
        self.inhibited(self.invoke())
        self.inhibited(self.invoke())
        self.assertEqual(entered, [True])

    def test_failed_diagnostic_does_not_mask_sdk_uncertainty(self):
        self.build()
        def fail(*args): raise RuntimeError('discarded SDK text')
        self.client.placeOrder = fail
        with self.fail_append(j._ReconciliationRequired, SystemExit(37)):
            self.inhibited(self.invoke())


class SetupFailureTests(JournalFixture):
    def test_interrupted_session_registration_closes_database_handle(self):
        captured = []
        original = storage._SQLiteJournalStore._create.__func__
        def create(cls, *args):
            store = original(cls, *args)
            captured.append(store)
            return store
        interruption = KeyboardInterrupt()
        with patch.object(storage._SQLiteJournalStore, '_create', classmethod(create)), \
                self.fail_append(j._SessionRegistered, interruption), \
                self.assertRaises(KeyboardInterrupt) as raised:
            self.build()
        self.assertIs(raised.exception, interruption)
        self.assertIsNone(self.coordinator._journal_owner)
        self.assertTrue(self.coordinator._journal_setup_started)
        with self.assertRaises(sqlite3.ProgrammingError): captured[0]._connection.execute('SELECT 1')
        # Deletion is meaningful on Windows and every legitimate owner is closed.
        Path(self.temporary.name, 'dispatch-1.db').unlink()
        self.assertFalse(self.write_calls())

    def test_setup_has_no_default_path(self):
        with self.assertRaises(TypeError): integration._create_paper_dispatch_journal_owner(None)

    def test_wrong_coordinator_type_cannot_create_store(self):
        path = Path(self.temporary.name, 'never-created.db')
        with self.assertRaises(ReadOnlyError): integration._create_paper_dispatch_journal_owner(object(), path)
        self.assertFalse(path.exists())


def registration_failure(code):
    def test(self):
        with self.fail_append(j._SessionRegistered, storage._StorageError(code)), self.assertRaises(ReadOnlyError):
            self.build()
        self.assertIsNone(self.coordinator._journal_owner)
        self.assertTrue(self.coordinator._reconciliation_required)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertFalse(self.write_calls())
        second = Path(self.temporary.name, 'second.db')
        with self.assertRaises(ReadOnlyError):
            integration._create_paper_dispatch_journal_owner(self.coordinator, second)
        self.assertFalse(second.exists())
    return test


for code in storage._Code:
    setattr(SetupFailureTests, 'test_registration_' + code.value, registration_failure(code))


class ProvenanceAttackTests(JournalFixture):
    def test_reservation_construction_failure_preserves_undurable_id_consumption(self):
        self.build()
        from tradingbot_broker import ibkr_paper_transport as paper
        with patch.object(paper, '_DispatchReservation', side_effect=ReadOnlyError('FAILED_RESERVATION')):
            self.inhibited(self.invoke())
        self.assertEqual(self.write._lifetime_order_floor, 11)
        self.assertFalse(self.write._write_invocation_started)
        self.assertFalse(any(type(event) is j._InvocationMayOccur for event in self.history()))
        other = request('after-undurable-reservation')
        self.inhibited(self.invoke(other, permission(other)))
        self.assertEqual(self.write._lifetime_order_floor, 11)

    def test_changed_header_object_is_rejected_before_sqlite(self):
        self.build()
        self.journal._store._header = replace(self.journal._header)
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite reached')):
            self.inhibited(self.invoke())

    def test_malformed_receipt_version_is_rejected(self):
        self.build()
        object.__setattr__(self.journal._receipt, 'version', 2)
        self.inhibited(self.invoke())

    def test_missing_approval_is_denied_without_reservation(self):
        self.build()
        result = self.coordinator._dispatch_entry(self.order, self.permission, self.capability,
            position=self.position)
        self.assert_denied(result)
        self.assertEqual(self.write._lifetime_order_floor, 10)

    def test_missing_position_is_denied_without_reservation(self):
        self.build()
        result = self.coordinator._dispatch_entry(self.order, self.permission, self.capability,
            approval=self.approval)
        self.assert_denied(result)
        self.assertEqual(self.write._lifetime_order_floor, 10)

    def test_maximum_numeric_id_remains_invokable_once(self):
        self.build()
        from tradingbot_broker.ibkr_paper_transport import MAX_ORDER_ID
        self.client.wrapper.nextValidId(MAX_ORDER_ID)
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)
        self.assertEqual(self.history()[2].order_id, MAX_ORDER_ID)
        self.assertTrue(self.write._lifetime_order_ids_exhausted)

    def test_no_req_ids_or_order_management_wire_added(self):
        self.build()
        self.invoke()
        from tradingbot_broker.paper_execution import FUTURE_PAPER_WRITE_IDS
        self.assertNotIn(8, FUTURE_PAPER_WRITE_IDS)
        self.assertNotIn(208, FUTURE_PAPER_WRITE_IDS)
        self.assertEqual([call[0] for call in self.write_calls()], ['place'])


def association_attack(index):
    def test(self):
        self.build()
        association = list(self.journal._PaperDispatchJournalOwner__association)
        if index == 4: association[index] = replace(association[index])
        elif index in (5, 7): association[index] = replace(association[index])
        else: association[index] = object()
        object.__setattr__(self.journal, '_PaperDispatchJournalOwner__association', tuple(association))
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite reached')):
            result = self.invoke()
        self.assertIs(result.state, State.OUTCOME_UNKNOWN)
        self.assertFalse(self.write_calls())
    return test


for index, name in enumerate(('coordinator', 'control', 'read', 'write', 'receipt',
                              'header', 'store', 'session', 'thread')):
    setattr(ProvenanceAttackTests, 'test_replaced_association_' + name, association_attack(index))


def proof_substitution(field):
    def test(self):
        original = dispatch._PaperOrderDispatchCoordinator._validate_commit
        def validate(coordinator, reservation, challenge):
            proof = coordinator._prepared_journal_proof
            replacement = replace(getattr(proof, field))
            object.__setattr__(proof, field, replacement)
            return original(coordinator, reservation, challenge)
        with patch.object(dispatch._PaperOrderDispatchCoordinator, '_validate_commit', validate):
            self.build()
        self.inhibited(self.invoke())
        self.assertFalse(self.write._write_invocation_started)
    return test


for field in ('attempt', 'reservation', 'context', 'event', 'committed'):
    setattr(ProvenanceAttackTests, 'test_marker_proof_substitution_' + field, proof_substitution(field))


def interruption(kind, seam, after):
    def test(self):
        self.build()
        error = kind(37)
        if seam == 'sdk':
            def sdk(*args): raise error
            self.client.placeOrder = sdk
            manager = None
        else:
            manager = self.fail_append(seam, error, after=after)
        if manager is None:
            with self.assertRaises(kind) as raised: self.invoke()
        else:
            with manager, self.assertRaises(kind) as raised: self.invoke()
        self.assertIs(raised.exception, error)
        self.assertTrue(self.coordinator._reconciliation_required)
        self.assertTrue(self.broker.reconciliation_required)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertIs(self.broker._paper_authority.status, Auth.INVALIDATED)
        self.assertEqual(len(self.write_calls()), int(seam is j._SdkReturnedNormally))
        self.assertEqual(self.write._lifetime_order_floor, 11)
    return test


class InterruptionTests(JournalFixture): pass


for kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    for seam, after in ((j._AttemptConsumed, False), (j._NumericOrderIdReserved, False),
                        (j._InvocationMayOccur, False), (j._InvocationMayOccur, True),
                        ('sdk', False), (j._SdkReturnedNormally, False)):
        label = seam if type(seam) is str else seam.__name__
        setattr(InterruptionTests, 'test_'+kind.__name__+'_'+label+('_after' if after else ''),
                interruption(kind, seam, after))


def preliminary_interruption(kind, seam):
    def test(self):
        self.build()
        error = kind(37)
        if seam == 'poll': manager = patch.object(self.broker, 'poll', side_effect=error)
        elif seam == 'context':
            manager = patch.object(dispatch, '_adapt_dispatch_journal_context', side_effect=error)
        else: manager = patch.object(dispatch, '_Attempt', side_effect=error)
        with manager, self.assertRaises(kind) as raised: self.invoke()
        self.assertIs(raised.exception, error)
        self.assertIn(self.order.client_order_id, self.coordinator._attempts)
        self.assertTrue(self.journal._inhibited)
        self.assertTrue(self.coordinator._reconciliation_required)
        self.assertTrue(self.broker.reconciliation_required)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertIs(self.broker._paper_authority.status, Auth.INVALIDATED)
        self.assertFalse(self.write_calls())
        self.assertFalse(self.write._write_invocation_started)
        self.assertEqual(self.write._lifetime_order_floor, 10)
        self.assertFalse(any(type(event) is j._InvocationMayOccur for event in self.history()))
        other = request('after-interruption')
        self.inhibited(self.invoke(other, permission(other)))
    return test


for kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    for seam in ('poll', 'context', 'attempt'):
        setattr(InterruptionTests, 'test_preliminary_'+kind.__name__+'_'+seam,
                preliminary_interruption(kind, seam))
