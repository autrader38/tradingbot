"""PRE4-PRE2: real temporary SQLite, anonymous SDK doubles, network forbidden."""

import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta, timezone, tzinfo
import inspect
from pathlib import Path
from threading import Event, current_thread
import unittest
from unittest.mock import patch

from tradingbot_broker import paper_dispatch_journal as p
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker import paper_execution_journal_sqlite as s
from tradingbot_broker import ibkr_order_status_evidence as source
from tradingbot_broker import ibkr_qualified_status_ledger as memory
from tradingbot_broker import paper_qualified_status_journal as adapter
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests import test_ibkr_qualified_status_publication as publication
from tests import test_paper_qualified_status_durability as qualified
from tests.dispatch_journal_fixtures import AT as FIXTURE_AT


AT = FIXTURE_AT + timedelta(seconds=1)  # After the fixture's authenticated snapshot.


Owner = p._PaperDispatchJournalOwner
Boundary = j._QualifiedDecisionBoundaryObserved
Receipt = p._DecisionBoundaryReceipt
State = p._CallbackPersistenceState
PREFIX = '_PaperDispatchJournalOwner'
PRODUCTION_CLOCK = p._decision_utc_now


class Case(publication.RuntimeCase):
    def setUp(self):
        super().setUp()
        self.decision_at = AT
        self.enterContext(patch.object(p, '_decision_utc_now', side_effect=lambda: self.decision_at))
        for name in ('socket.socket.connect', 'socket.socket.connect_ex',
                     'socket.create_connection', 'socket.getaddrinfo'):
            self.enterContext(patch(name, side_effect=AssertionError('real network forbidden')))

    def start_producer(self):
        self.registered()

    def certify(self):
        return self.journal._certify_decision_boundary()

    def boundaries(self):
        return tuple(r for r in self.journal._load().records if type(r.payload) is Boundary)

    def advance(self):
        self.decision_at += timedelta(microseconds=1)

    def denied(self, *, not_ready=False):
        with self.assertRaises(p._DecisionBoundaryUnavailable if not_ready else ReadOnlyError):
            self.certify()
        self.assertIsNone(getattr(self.journal, PREFIX + '__decision_pending'))
        self.assertFalse(getattr(self.journal, PREFIX + '__decision_active'))

    def safe(self):
        self.assertTrue(self.journal._inhibited)
        self.assertTrue(self.coordinator._reconciliation_required)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertIsNone(getattr(self.journal, PREFIX + '__decision_receipt'))


class ProducerTests(Case):
    def test_fresh_exact_v3_header_registered_once_without_boundary(self):
        self.start_producer(); loaded = self.journal._load()
        self.assertEqual((loaded.header.schema_version, loaded.header.format_id),
                         (3, 'paper-execution-journal-v3'))
        self.assertEqual(tuple(r.payload for r in loaded.records), (self.journal._session,))
        self.assertTrue(loaded.replay.restart_quarantine_required)
        self.assertIs(loaded.header.account_mode, AccountMode.UNKNOWN)

    def test_zero_frontier_exact_internal_clock_and_committed_receipt(self):
        self.start_producer(); receipt = self.certify()
        self.assertIs(type(receipt), Receipt)
        self.assertEqual(receipt.event, Boundary(self.journal._session.session_id, 1, AT, 0))
        self.assertIs(receipt.committed.record.payload, receipt.event)
        self.assertEqual(receipt.committed.record.sequence, 2)
        self.journal._require_decision_receipt(receipt)
        loaded = self.journal._load()
        companion = j._replay_v3_decision_boundary_evidence(loaded.header, loaded.records)
        self.assertEqual(companion.boundary_records[0], receipt.committed.record)
        self.assertEqual(companion.replay.callback_progress, ())
        self.assertEqual(self.journal._persistence_cursor, 0)

    def test_nonzero_qualified_status_is_durable_before_boundary(self):
        self.start_producer(); self.qualified(); receipt = self.certify()
        self.assertEqual(receipt.event.through_sequence, 1)
        records = self.journal._load().records
        self.assertEqual([type(r.payload) for r in records], [j._SessionRegistered,
            j._QualifiedOrderStatusObserved, j._CallbackProgress, Boundary])
        self.assertEqual(records[1].payload.source.sequence, 1)

    def test_multiple_callbacks_and_same_frontier_later_boundary(self):
        self.start_producer()
        for order_id in range(10, 15): self.qualified(orderId=order_id)
        first = self.certify(); self.advance(); second = self.certify()
        self.assertEqual((first.event.through_sequence, second.event.through_sequence), (5, 5))
        self.assertLess(first.event.decision_at, second.event.decision_at)
        self.assertEqual(len(self.boundaries()), 2)

    def test_increasing_frontier_and_time(self):
        self.start_producer(); first = self.certify()
        self.qualified(); self.advance(); second = self.certify()
        self.assertEqual((first.event.through_sequence, second.event.through_sequence), (0, 1))

    def test_mixed_pre9_occurrences_do_not_change_semantics(self):
        self.start_producer(); self.open(order_id=99)
        self.client.decoder.interpret((37, 99, *publication.LEGACY[1:]))
        self.execution(order_id=99); self.commission(); self.qualified(orderId=99)
        receipt = self.certify()
        self.assertEqual(receipt.event.through_sequence, 5)
        loaded = self.journal._load()
        self.assertEqual(loaded.replay.attempts, ())
        self.assertEqual(loaded.replay.callback_progress[0].processed_sequence, 5)
        self.assertTrue(loaded.replay.restart_quarantine_required)

    def test_adjacent_compressed_qualified_callbacks_expand_before_boundary(self):
        self.start_producer()
        for _ in range(5): self.qualified()
        self.assertEqual(len(self.write._callback_evidence), 1)
        receipt = self.certify()
        self.assertEqual(receipt.event.through_sequence, 5)
        self.assertEqual([r.payload.source.sequence for r in self.journal._load().records
            if type(r.payload) is j._QualifiedOrderStatusObserved], list(range(1, 6)))

    def test_existing_ordinary_duplicate_coverage_before_boundary(self):
        self.start_producer()
        for _ in range(3): self.open(order_id=99)
        receipt = self.certify()
        self.assertEqual(receipt.event.through_sequence, 3)
        self.assertEqual(len(self.journal._load().replay.attempts), 0)

    def test_fresh_factory_cannot_adopt_existing_path(self):
        self.start_producer(); loaded = self.journal._load()
        with self.assertRaises(ReadOnlyError):
            p._create_paper_dispatch_journal_owner(self.coordinator,
                Path(self.temporary.name) / 'dispatch-1.db')
        self.assertEqual(self.journal._load().records, loaded.records)

    def test_producer_accepts_no_caller_provenance(self):
        self.start_producer()
        for args, kwargs in (((AT,), {}), ((0,), {}), ((), {'decision_at': AT}),
            ((), {'through_sequence': 0}), ((), {'session_id': self.journal._session.session_id}),
            ((), {'write_generation': 1})):
            with self.assertRaises(TypeError): self.journal._certify_decision_boundary(*args, **kwargs)
        self.assertEqual(self.boundaries(), ())

    def test_direct_owner_append_cannot_certify_fixture_boundary(self):
        self.start_producer(); event = Boundary(self.journal._session.session_id, 1, AT, 0)
        with self.coordinator._lock, self.broker._lock:
            with self.assertRaises(ReadOnlyError): self.journal._append_event(event)
            with self.assertRaises(ReadOnlyError): self.journal._append_event(event, diagnostic=True)
        self.assertEqual(self.boundaries(), ())
        self.assertFalse(self.journal._store._quarantined)


class RaceTests(Case):
    def test_callback_after_capture_before_append_stays_after_boundary(self):
        self.start_producer(); self.qualified(); self.drain()
        original = Owner._append_event
        def append(owner, event, **kwargs):
            if type(event) is Boundary:
                self.assertFalse(self.write._arrival_lock._is_owned())
                self.qualified(orderId=11)
                self.assertIs(self.drain().state, State.DEFERRED)
                self.assertEqual(owner._persistence_cursor, 1)
            return original(owner, event, **kwargs)
        with patch.object(Owner, '_append_event', append): receipt = self.certify()
        self.assertEqual(receipt.event.through_sequence, 1)
        self.assertEqual(self.write._callback_sequence, 2)
        self.assertEqual(self.journal._persistence_cursor, 1)
        self.assertIs(self.drain().state, State.CURRENT)
        records = self.journal._load().records
        boundary_index = next(i for i, r in enumerate(records) if type(r.payload) is Boundary)
        callback_index = next(i for i, r in enumerate(records)
            if type(r.payload) is j._QualifiedOrderStatusObserved and r.payload.source.sequence == 2)
        self.assertLess(boundary_index, callback_index)
        j._replay_v3_decision_boundary_evidence(self.journal._header, records)

    def test_callback_arrival_cannot_increment_frontier_during_clock_capture(self):
        self.start_producer()
        entered, attempted, finished = Event(), Event(), Event()
        def callback():
            self.assertTrue(entered.wait(5)); attempted.set()
            self.qualified(); finished.set()
        def clock():
            self.assertTrue(self.write._arrival_lock._is_owned())
            self.assertTrue(self.write._lock._is_owned())
            entered.set(); self.assertTrue(attempted.wait(5))
            self.assertFalse(finished.is_set()); self.assertEqual(self.write._callback_sequence, 0)
            return AT
        original = Owner._append_event
        def append(owner, event, **kwargs):
            if type(event) is Boundary: self.assertTrue(finished.wait(5))
            return original(owner, event, **kwargs)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(callback)
            with patch.object(p, '_decision_utc_now', clock), patch.object(Owner, '_append_event', append):
                receipt = self.certify()
            future.result(timeout=5)
        self.assertEqual(receipt.event.through_sequence, 0)
        self.assertEqual(self.write._callback_sequence, 1)
        self.drain()
        self.assertEqual(self.boundaries()[0].payload.through_sequence, 0)

    def test_callback_between_drain_and_capture_is_not_ready_without_clock(self):
        self.start_producer(); original = Owner._drain_callbacks
        def drain(owner, c):
            result = original(owner, c); self.qualified(); return result
        with patch.object(Owner, '_drain_callbacks', drain), patch.object(p, '_decision_utc_now') as clock:
            self.denied(not_ready=True)
        clock.assert_not_called()
        self.assertEqual(self.boundaries(), ())
        self.assertFalse(self.journal._inhibited)
        self.advance(); self.assertEqual(self.certify().event.decision_at, self.decision_at)

    def test_reentrant_callback_inside_test_clock_is_rejected(self):
        self.start_producer()
        def clock(): self.qualified(); return AT
        with patch.object(p, '_decision_utc_now', clock): self.denied()
        self.assertEqual(self.boundaries(), ()); self.safe()

    def test_reentrant_producer_cannot_enter_or_append_twice(self):
        self.start_producer(); original = Owner._append_event
        def append(owner, event, **kwargs):
            if type(event) is Boundary:
                with self.assertRaises(p._DecisionBoundaryUnavailable): owner._certify_decision_boundary()
            return original(owner, event, **kwargs)
        with patch.object(Owner, '_append_event', append): self.certify()
        self.assertEqual(len(self.boundaries()), 1)

    def test_callback_after_durable_append_before_publication_does_not_rewrite_frontier(self):
        self.start_producer(); original = Owner._append_event
        def append(owner, event, **kwargs):
            result = original(owner, event, **kwargs)
            if type(event) is Boundary: self.qualified()
            return result
        with patch.object(Owner, '_append_event', append): receipt = self.certify()
        self.assertEqual(receipt.event.through_sequence, 0)
        self.assertEqual(self.write._callback_sequence, 1)
        self.assertEqual(self.journal._persistence_cursor, 0)

    def test_real_storage_io_releases_every_capture_lock(self):
        self.start_producer(); self.qualified(); original = s._SQLiteJournalStore._begin
        def begin(store, writing):
            self.assertIs(current_thread(), self.journal._thread)
            for lock in (self.transport._lifecycle, self.transport._condition,
                         self.write._lock, self.write._arrival_lock): self.assertFalse(lock._is_owned())
            return original(store, writing)
        with patch.object(s._SQLiteJournalStore, '_begin', begin): self.certify()

    def test_authentication_and_sdk_inspection_are_outside_arrival_and_write_locks(self):
        self.start_producer(); original = self.transport._validate_journal_account_binding
        def authenticate(receipt):
            self.assertFalse(self.write._arrival_lock._is_owned())
            self.assertFalse(self.write._lock._is_owned())
            return original(receipt)
        with patch.object(self.transport, '_validate_journal_account_binding', authenticate): self.certify()


class FailureTests(Case):
    def test_ordinary_clock_exception_is_sanitized_without_poisoning_store(self):
        self.start_producer()
        with patch.object(p, '_decision_utc_now', side_effect=ValueError('private clock failure')):
            with self.assertRaises(p._DecisionBoundaryUnavailable) as caught: self.certify()
        self.assertEqual(caught.exception.code, 'PAPER_DECISION_BOUNDARY_NOT_READY')
        self.assertFalse(self.journal._inhibited)
        self.assertFalse(self.journal._store._poisoned)
        self.assertEqual(self.boundaries(), ())

    def test_captured_time_must_be_inside_original_authentication_deadline(self):
        self.start_producer()
        self.decision_at = self.transport._journal_account_association.valid_until
        self.denied(); self.safe(); self.assertEqual(self.boundaries(), ())

    def test_original_authentication_expiry_before_capture_cannot_renew(self):
        self.start_producer()
        deadline = self.transport._journal_account_association.valid_until
        self.clock.now = deadline
        self.denied(); self.safe(); self.assertEqual(self.boundaries(), ())
        self.assertIsNone(self.transport._journal_account_association)

    def test_authentication_expiry_after_append_retains_only_historical_record(self):
        self.start_producer(); deadline = self.transport._journal_account_association.valid_until
        original = Owner._append_event
        def append(owner, event, **kwargs):
            result = original(owner, event, **kwargs)
            if type(event) is Boundary: self.clock.now = deadline
            return result
        with patch.object(Owner, '_append_event', append): self.denied()
        self.safe(); self.assertEqual(len(self.boundaries()), 1)

    def test_active_drain_is_not_reentered(self):
        self.start_producer()
        setattr(self.journal, PREFIX + '__draining', True)
        with patch.object(Owner, '_drain_callbacks', side_effect=AssertionError('reentrant drain')):
            self.denied(not_ready=True)
        self.assertEqual(self.boundaries(), ())

    def test_equal_reconstructed_owner_cannot_certify(self):
        self.start_producer(); clone = object.__new__(Owner)
        for slot in Owner.__slots__:
            object.__setattr__(clone, PREFIX + slot, getattr(self.journal, PREFIX + slot))
        with self.assertRaises(ReadOnlyError): clone._certify_decision_boundary()
        self.assertEqual(self.boundaries(), ())

    def test_original_cleanup_failure_does_not_replace_clock_interruption(self):
        self.start_producer(); error = GeneratorExit('original')
        with patch.object(p, '_decision_utc_now', side_effect=error), \
             patch.object(type(self.coordinator), '_mark_reconciliation', side_effect=RuntimeError('cleanup')):
            with self.assertRaises(GeneratorExit) as caught: self.certify()
        self.assertIs(caught.exception, error)
        self.assertTrue(self.journal._inhibited)
        self.assertEqual(self.boundaries(), ())

    def test_original_cleanup_failure_does_not_replace_storage_interruption(self):
        self.start_producer(); error = SystemExit(61)
        with patch.object(s, '_commit', side_effect=error), \
             patch.object(type(self.coordinator), '_mark_reconciliation', side_effect=RuntimeError('cleanup')):
            with self.assertRaises(SystemExit) as caught: self.certify()
        self.assertIs(caught.exception, error); self.assertEqual(caught.exception.code, 61)
        self.assertTrue(self.journal._inhibited); self.assertTrue(self.journal._store._poisoned)

    def test_pending_unknown_commission_produces_no_boundary(self):
        self.start_producer(); self.commission()
        self.denied(not_ready=True)
        self.assertEqual(self.boundaries(), ())
        self.assertEqual(self.journal._persistence_cursor, 0)

    def test_unpublished_coverage_is_not_repaired_by_boundary(self):
        self.start_producer(); self.qualified()
        with patch.object(Owner, '_publish_callback_cursor', side_effect=ValueError('cursor failure')):
            self.denied(not_ready=True)
        self.assertEqual(self.boundaries(), ())
        self.assertTrue(self.journal._persistence_failed)

    def test_wrong_thread_never_reaches_sqlite_or_clock(self):
        self.start_producer()
        with patch.object(s._SQLiteJournalStore, '_begin', side_effect=AssertionError('storage')), \
             patch.object(p, '_decision_utc_now', side_effect=AssertionError('clock')):
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaises(ReadOnlyError): pool.submit(self.certify).result(timeout=5)
        self.assertEqual(self.boundaries(), ())

    def test_equal_clock_rejected_before_append_without_quarantine(self):
        self.start_producer(); self.certify()
        with patch.object(Owner, '_append_event', side_effect=AssertionError('append')):
            self.denied(not_ready=True)
        self.assertFalse(self.journal._store._quarantined)
        self.assertFalse(self.journal._store._poisoned)
        self.assertFalse(self.journal._inhibited)
        self.assertEqual(len(self.boundaries()), 1)

    def test_regressing_clock_rejected_without_editing_it(self):
        self.start_producer(); self.certify(); self.decision_at -= timedelta(microseconds=1)
        self.denied(not_ready=True)
        self.assertEqual(self.decision_at, AT - timedelta(microseconds=1))
        self.assertFalse(self.journal._store._quarantined)
        self.decision_at = AT + timedelta(microseconds=1)
        self.assertEqual(self.certify().event.decision_at, self.decision_at)

    def test_duplicate_coverage_high_water_cannot_be_hidden_by_cursor(self):
        self.start_producer(); self.qualified(); self.qualified(orderId=11)
        original = Owner._require_persistence_current
        def falsely_current(owner, c):
            original(owner, c)
            owner._append_event(j._CallbackProgress(owner._session.session_id, 2, 3))
        # Storage is forbidden under arrival even through a private reentrant helper.
        with patch.object(Owner, '_require_persistence_current', falsely_current): self.denied()
        self.assertEqual(self.boundaries(), ())

    def test_failure_after_commit_has_one_record_and_no_receipt_or_retry(self):
        self.start_producer(); error = ValueError('after durable commit')
        original = Owner._append_event
        def append(owner, event, **kwargs):
            result = original(owner, event, **kwargs)
            if type(event) is Boundary: raise error
            return result
        with patch.object(Owner, '_append_event', append):
            with self.assertRaises(ValueError) as caught: self.certify()
        self.assertIs(caught.exception, error); self.safe()
        self.assertEqual(len(self.boundaries()), 1)
        with self.assertRaises(ReadOnlyError): self.certify()
        self.assertEqual(len(self.boundaries()), 1)

    def test_uncertain_real_sqlite_commit_poison_and_no_receipt(self):
        self.start_producer(); original = s._commit
        error = KeyboardInterrupt('commit interruption')
        def commit(connection): original(connection); raise error
        with patch.object(s, '_commit', commit):
            with self.assertRaises(KeyboardInterrupt) as caught: self.certify()
        self.assertIs(caught.exception, error); self.safe()
        self.assertTrue(self.journal._store._poisoned)

    def test_boundary_must_remain_exact_after_append(self):
        self.start_producer(); original = Owner._append_event
        def append(owner, event, **kwargs):
            result = original(owner, event, **kwargs)
            if type(event) is Boundary: object.__setattr__(event, 'through_sequence', 1)
            return result
        with patch.object(Owner, '_append_event', append): self.denied()
        self.safe(); self.assertEqual(self.boundaries()[0].payload.through_sequence, 0)


class ReceiptTests(Case):
    def test_receipt_validation_requires_exact_creator_thread(self):
        self.start_producer(); receipt = self.certify()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(ReadOnlyError):
                pool.submit(self.journal._require_decision_receipt, receipt).result(timeout=5)

    def test_exact_producer_association_is_immutable(self):
        self.start_producer()
        for name in ('__decision_runtime', '__decision_runtime_values'):
            with self.assertRaises(ReadOnlyError): setattr(self.journal, PREFIX + name, ())
        self.certify()

    def test_exact_frozen_slotted_opaque_two_field_receipt(self):
        self.start_producer(); receipt = self.certify()
        self.assertEqual(tuple(f.name for f in fields(receipt)), ('event', 'committed'))
        self.assertFalse(hasattr(receipt, '__dict__'))
        self.assertNotIn(receipt.event.session_id, repr(receipt))
        self.assertNotIn('2026', repr(receipt))
        with self.assertRaises(FrozenInstanceError): receipt.event = None

    def test_equal_reconstructed_receipt_is_not_issued(self):
        self.start_producer(); receipt = self.certify()
        self.assertEqual(replace(receipt), receipt)
        with self.assertRaises(ReadOnlyError): self.journal._require_decision_receipt(replace(receipt))
        self.journal._require_decision_receipt(receipt)

    def test_only_latest_exact_publication_is_owner_registered(self):
        self.start_producer(); first = self.certify(); self.advance(); second = self.certify()
        with self.assertRaises(ReadOnlyError): self.journal._require_decision_receipt(first)
        self.journal._require_decision_receipt(second)
        self.assertEqual(len(self.boundaries()), 2)

    def test_equal_event_substitution_does_not_pass_payload_identity(self):
        self.start_producer(); receipt = self.certify()
        object.__setattr__(receipt, 'event', replace(receipt.event))
        with self.assertRaises(ReadOnlyError): self.journal._require_decision_receipt(receipt)

    def test_committed_digest_tampering_is_rejected(self):
        self.start_producer(); receipt = self.certify()
        object.__setattr__(receipt.committed, 'digest', b'x' * 32)
        with self.assertRaises(ReadOnlyError): self.journal._require_decision_receipt(receipt)

    def test_equal_event_and_committed_substitution_together_is_rejected(self):
        self.start_producer(); receipt = self.certify()
        event = replace(receipt.event)
        committed = replace(receipt.committed, record=replace(receipt.committed.record, payload=event))
        object.__setattr__(receipt, 'event', event)
        object.__setattr__(receipt, 'committed', committed)
        with self.assertRaises(ReadOnlyError): self.journal._require_decision_receipt(receipt)

    def test_another_exact_owner_cannot_recognize_receipt(self):
        self.start_producer(); receipt = self.certify()
        other = ProducerTests('runTest'); other.setUp()
        try:
            other.start_producer()
            with self.assertRaises(ReadOnlyError): other.journal._require_decision_receipt(receipt)
        finally: other.tearDown(); other.doCleanups()


class AuthorityTests(Case):
    def test_private_production_clock_is_fixed_utc_without_injection_parameters(self):
        self.assertEqual(tuple(inspect.signature(PRODUCTION_CLOCK).parameters), ())
        tree = ast.parse(inspect.getsource(PRODUCTION_CLOCK))
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        self.assertEqual((call.func.value.id, call.func.attr), ('datetime', 'now'))
        self.assertEqual((call.args[0].value.id, call.args[0].attr), ('timezone', 'utc'))

    def test_boundary_changes_only_journal_record_chronology(self):
        self.start_producer(); before = self.journal._load().replay
        state = (self.journal._persistence_cursor, self.write._callback_sequence,
            self.write._arrival_sequence, self.coordinator._callback_cursor,
            self.coordinator._reconciliation_required, self.broker._reconciliation_required,
            self.write._reconciliation_required, self.broker.controls,
            self.broker._paper_authority.status, tuple(self.paper_api.calls))
        receipt = self.certify(); after = self.journal._load().replay
        self.assertEqual(state, (self.journal._persistence_cursor, self.write._callback_sequence,
            self.write._arrival_sequence, self.coordinator._callback_cursor,
            self.coordinator._reconciliation_required, self.broker._reconciliation_required,
            self.write._reconciliation_required, self.broker.controls,
            self.broker._paper_authority.status, tuple(self.paper_api.calls)))
        for name in ('attempts', 'callback_progress', 'observed_callback_watermarks',
                     'order_id_floor', 'reserved_order_ids', 'restart_quarantine_required'):
            self.assertEqual(getattr(before, name), getattr(after, name))
        self.assertFalse(self.broker.controls.trading_enabled)
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.transport._snapshot.account.mode)
        for name in ('ownership', 'inventory', 'current_lifecycle', 'current_remainder',
                     'consume_receipt', 'protection', 'cancel', 'replace', 'exit', 'recovery',
                     'clear_barrier', 'restart_adoption', 'trading_authority'):
            self.assertFalse(hasattr(receipt, name))

    def test_no_public_export_or_unrelated_automatic_producer(self):
        import tradingbot_broker
        self.assertFalse(hasattr(tradingbot_broker, 'DecisionBoundaryReceipt'))
        root = Path(p.__file__).parent
        constructors, calls = [], []
        for path in root.glob('*.py'):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, 'attr', getattr(node.func, 'id', ''))
                    if name == '_QualifiedDecisionBoundaryObserved': constructors.append(path.name)
                    if name == '_certify_decision_boundary': calls.append(path.name)
        self.assertEqual(constructors, ['paper_dispatch_journal.py'])
        self.assertEqual(calls, [])

    def test_producer_does_not_import_collections_or_status_evaluator(self):
        modules = {n.module for n in ast.walk(ast.parse(inspect.getsource(p)))
                   if isinstance(n, ast.ImportFrom)}
        self.assertFalse(modules & {'paper_fresh_open_order_collection',
            'paper_fresh_execution_collection', 'paper_fresh_completed_order_collection',
            'paper_decision_status_intervals', 'paper_recovery', 'paper_working_order'})

    def test_no_network_no_sdk_requests_no_accounting_or_collection_consumption(self):
        self.start_producer(); calls = tuple(self.paper_api.calls)
        from tradingbot_broker import paper_fresh_open_order_collection as o
        from tradingbot_broker import paper_fresh_execution_collection as e
        from tradingbot_broker import paper_fresh_completed_order_collection as c
        from contextlib import ExitStack
        with ExitStack() as scope:
            for kind in (o._FreshOpenOrderCollectionOwner, e._FreshExecutionCollectionOwner,
                         c._FreshCompletedOrderCollectionOwner):
                scope.enter_context(patch.object(kind, '_collect', side_effect=AssertionError('collection')))
                scope.enter_context(patch.object(kind, '_consume_receipt', side_effect=AssertionError('receipt')))
            scope.enter_context(patch.object(Owner, '_create_accounting_owner', side_effect=AssertionError('accounting')))
            self.certify()
        self.assertEqual(tuple(self.paper_api.calls), calls)


def source_case(name, protocol, payload):
    def test(self):
        self.start_producer()
        obs = (source._adapt_protobuf_order_status(publication.Proto(**payload)) if protocol == 'protobuf'
               else source._adapt_legacy_order_status(*payload))
        # Anonymous callback fixture, never caller input to the production producer.
        if protocol == 'protobuf':
            self.client.decoder.processProtoBuf(publication.Proto(**payload).SerializeToString(), 37)
        else:
            self.client.decoder.interpret((37, *payload))  # Anonymous decoder's helper shape.
        receipt = self.certify()
        event = next(r.payload for r in self.journal._load().records
                     if type(r.payload) is j._QualifiedOrderStatusObserved)
        self.assertEqual(qualified.identity(event.observation), qualified.identity(obs))
        self.assertEqual(receipt.event.through_sequence, 1)
    setattr(ProducerTests, 'test_source_' + name, test)

for name, (protocol, payload) in publication._SOURCES.items(): source_case(name, protocol, payload)


def not_current_case(name, mutate):
    def test(self):
        self.start_producer()
        original = Owner._drain_callbacks
        def drain(owner, c):
            result = original(owner, c); mutate(self); return result
        with patch.object(Owner, '_drain_callbacks', drain): self.denied(not_ready=True)
        self.assertEqual(self.boundaries(), ())
        self.assertFalse(self.journal._store._quarantined)
    setattr(FailureTests, 'test_not_current_' + name, test)

for name, mutate in (
    ('staged', lambda f: getattr(f.journal, PREFIX + '__staged').update({1: object()})),
    ('covered', lambda f: getattr(f.journal, PREFIX + '__covered').update({1: object()})),
    ('cursor_behind', lambda f: f.qualified()),
): not_current_case(name, mutate)


def drain_outcome_case(state):
    def test(self):
        self.start_producer()
        result = p._CallbackPersistenceResult(state, 0, 0, 0)
        with patch.object(Owner, '_drain_callbacks', return_value=result), \
             patch.object(p, '_decision_utc_now', side_effect=AssertionError('clock')):
            self.denied(not_ready=True)
        self.assertEqual(self.boundaries(), ())
    setattr(FailureTests, 'test_drain_' + state.value, test)
for state in (State.PENDING, State.DEFERRED, State.FAILED): drain_outcome_case(state)


def durable_prefix_case(name, sequence=None):
    def test(self):
        self.start_producer()
        with self.coordinator._lock, self.broker._lock:
            if sequence is None:
                self.journal._append_event(j._CallbackProgress(self.journal._session.session_id, 0, 1))
            else:
                obs = qualified.observation()
                # Syntactic out-of-band fixture deliberately inconsistent with local capture.
                event = adapter._adapt_qualified_status(memory._QualifiedOrderStatusEvidence(1, sequence, obs),
                                                        self.journal._session)
                self.journal._append_event(event)
        self.denied()
        self.assertEqual(self.boundaries(), ())
        self.assertFalse(self.journal._store._quarantined)
        self.assertFalse(self.journal._store._poisoned)
    setattr(FailureTests, 'test_preflight_' + name, test)
for name, sequence in (('frontier_ahead', 1), ('highwater_gap', 2), ('known_undurable_suffix', None)):
    durable_prefix_case(name, sequence)


def malformed_clock_case(name, value):
    def test(self):
        self.start_producer()
        with patch.object(p, '_decision_utc_now', return_value=value): self.denied(not_ready=True)
        self.assertEqual(self.boundaries(), ())
        self.assertFalse(self.journal._store._quarantined)
        self.assertFalse(self.journal._inhibited)
    setattr(FailureTests, 'test_clock_' + name, test)

class ForeignTZ(tzinfo):
    def utcoffset(self, value): raise AssertionError('unqualified timezone getter')

for name, value in (('naive', AT.replace(tzinfo=None)), ('nonutc', AT.astimezone(timezone(timedelta(hours=1)))),
    ('alias', AT.replace(tzinfo=timezone(timedelta(0), 'GMT'))), ('foreign', AT.replace(tzinfo=ForeignTZ())),
    ('fold', AT.replace(fold=1)), ('none', None), ('text', AT.isoformat()), ('bool', True), ('date', AT.date())):
    malformed_clock_case(name, value)


def replace_client(f, transport):
    f.addCleanup(setattr, transport, '_client', transport._client)
    transport._client = object()


MUTATIONS = {
    'read_client': lambda f: replace_client(f, f.transport),
    'write_client': lambda f: replace_client(f, f.write),
    'read_generation': lambda f: setattr(f.transport, '_generation', f.transport._generation + 1),
    'write_generation': lambda f: setattr(f.write, '_generation', 2),
    'callback_generation': lambda f: setattr(f.write, '_callback_generation', 2),
    'read_config': lambda f: setattr(f.transport, 'config', replace(f.transport.config)),
    'write_config': lambda f: setattr(f.write, '_config', replace(f.write._config)),
    'read_snapshot': lambda f: setattr(f.transport, '_snapshot', replace(f.transport._snapshot)),
    'account_association': lambda f: setattr(f.transport, '_journal_account_association',
                                           replace(f.transport._journal_account_association)),
    'receipt': lambda f: object.__setattr__(f.transport._journal_account_association, 'receipt',
                                           replace(f.journal._receipt)),
    'deadline': lambda f: object.__setattr__(f.transport._journal_account_association, 'valid_until',
                                           AT + timedelta(seconds=1)),
    'read_disconnect': lambda f: setattr(f.transport, '_disconnect_emitted', True),
    'read_failure': lambda f: setattr(f.transport, '_failure', 'FAILED'),
    'write_closed': lambda f: setattr(f.write, '_closed', True),
    'loss': lambda f: object.__setattr__(f.write, '_PaperTWSTransport__callback_evidence_lost_at', 1),
    'invalid_callback': lambda f: setattr(f.write, '_invalid_callback', True),
    'arrival_unusable': lambda f: setattr(f.write, '_arrival_unusable', True),
    'fence': lambda f: f.write._status_fences.append(object()),
    'active_attempt': lambda f: setattr(f.coordinator, '_active_attempt', object()),
    'session': lambda f: object.__setattr__(f.journal._session, 'write_generation', 2),
    'header': lambda f: object.__setattr__(f.journal._header, 'schema_version', 2),
}


def association_case(name, mutate, seam):
    def test(self):
        self.start_producer()
        if seam == 'before':
            mutate(self); self.denied()
        else:
            original = Owner._append_event
            def append(owner, event, **kwargs):
                if type(event) is Boundary and seam == 'preappend': mutate(self)
                result = original(owner, event, **kwargs)
                if type(event) is Boundary and seam == 'postappend': mutate(self)
                return result
            with patch.object(Owner, '_append_event', append): self.denied()
        self.assertIsNone(getattr(self.journal, PREFIX + '__decision_receipt'))
        if name != 'active_attempt' or seam != 'before': self.assertTrue(self.journal._inhibited)
    setattr(FailureTests, f'test_{seam}_{name}', test)

for name, mutate in MUTATIONS.items():
    for seam in ('before', 'preappend', 'postappend'): association_case(name, mutate, seam)


def interrupt_case(seam, exception):
    def test(self):
        self.start_producer(); error = exception(47 if exception is SystemExit else 'interrupt')
        target, name = {
            'clock': (p, '_decision_utc_now'),
            'capture_before': (Owner, '_capture_decision_event'),
            'capture_after': (Owner, '_capture_decision_event'),
            'append_before': (Owner, '_append_event'),
            'append_after': (Owner, '_append_event'),
            'publish_before': (Owner, '_publish_decision_receipt'),
            'publish_after': (Owner, '_publish_decision_receipt'),
            'real_commit': (s, '_commit'),
        }[seam]
        original = getattr(target, name)
        def fail(*args, **kwargs):
            if name == '_append_event' and type(args[1]) is not Boundary:
                return original(*args, **kwargs)
            if seam.endswith('_after'): original(*args, **kwargs)
            raise error
        with patch.object(target, name, fail):
            with self.assertRaises(exception) as caught: self.certify()
        self.assertIs(caught.exception, error)
        if exception is SystemExit: self.assertEqual(caught.exception.code, 47)
        self.safe()
        self.assertIsNone(getattr(self.journal, PREFIX + '__decision_pending'))
        self.assertFalse(getattr(self.journal, PREFIX + '__decision_active'))
        if seam != 'real_commit':
            expected = 1 if seam in ('append_after', 'publish_before', 'publish_after') else 0
            self.assertEqual(len(self.boundaries()), expected)
        else: self.assertTrue(self.journal._store._poisoned)
    setattr(FailureTests, f'test_interrupt_{seam}_{exception.__name__}', test)

for seam in ('clock', 'capture_before', 'capture_after', 'append_before', 'append_after',
             'publish_before', 'publish_after', 'real_commit'):
    for exception in (KeyboardInterrupt, SystemExit, GeneratorExit): interrupt_case(seam, exception)


if __name__ == '__main__': unittest.main()
