"""PRE7 truthful capture prefixes. Anonymous SDK doubles; no real broker I/O."""

import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import inspect
from threading import Event
from textwrap import dedent
from unittest.mock import patch

from tradingbot_broker import ibkr_paper_transport as paper
from tradingbot_broker import ibkr_qualified_status_ledger as qualified
from tradingbot_broker import paper_dispatch_journal as integration
from tradingbot_broker import paper_execution_journal as journal
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests import test_paper_commission_capture as capture
from tests import test_paper_callback_persistence as persistence


KINDS = ('open', 'status', 'execution', 'commission', 'fees')
INTERRUPTIONS = (KeyboardInterrupt, SystemExit, GeneratorExit)


class IntegrityFixture(capture.CommissionFixture):
    def emit(self, kind, fact):
        if kind == 'open':
            self.open(order_id=1000 + fact)
        elif kind == 'status':
            self.status(order_id=1000 + fact)
        elif kind == 'execution':
            self.execution(order_id=1000 + fact, values={'execId': f'execution-{fact}'})
        else:
            self.commission('fees' if kind == 'fees' else 'legacy', exec_id=f'execution-{fact}')

    def fill(self, kind, count=128):
        for fact in range(count):
            self.emit(kind, fact)

    def check(self):
        with self.write._arrival_lock:
            self.write._check_callback_evidence()

    def frozen(self, prefix, ends, lost=129):
        self.assertEqual(tuple(self.write._callback_evidence), prefix)
        self.assertEqual(tuple(self.write._callback_run_ends), ends)
        self.assertEqual(self.write._callback_evidence_lost_at, lost)
        self.assertIsNone(self.write._last_callback_identity)
        self.assertTrue(self.write._requires_reconciliation())
        self.check()


class CoverageTests(IntegrityFixture):
    pass


def coverage_case(kind, scenario):
    def test(self):
        self.build()
        if scenario == 'adjacent':
            self.emit(kind, 1); self.emit(kind, 1)
            self.assertEqual(len(self.write._callback_evidence), 1)
            self.assertEqual(self.write._callback_run_ends, [2])
            self.assertEqual([e.sequence for e in self.page().events], [1, 2])
        elif scenario == 'interleaved':
            for fact in (1, 2, 1): self.emit(kind, fact)
            self.assertEqual(self.write._callback_run_ends, [1, 2, 3])
            self.assertEqual(len(self.write._callback_evidence), 3)
        elif scenario == 'generation':
            self.emit(kind, 1)
            self.write._begin_generation(); self.client = self.paper_api.clients[-1]
            self.emit(kind, 1)
            self.assertEqual([(e.generation, e.sequence) for e in self.page().events], [(1, 1), (2, 2)])
            self.assertEqual(self.write._callback_run_ends, [1, 2])
        elif scenario == 'at_capacity_duplicate':
            self.fill(kind)
            self.emit(kind, 127)
            self.assertEqual(self.write._callback_run_ends[-1], 129)
            self.assertIsNone(self.write._callback_evidence_lost_at)
            self.assertEqual(len(self.page(128).events), 1)
        elif scenario == 'capacity_boundary':
            self.fill(kind, 127)
            self.assertEqual(len(self.write._callback_evidence), 127)
            self.emit(kind, 127)
            self.assertEqual(len(self.write._callback_evidence), 128)
            self.assertIsNone(self.write._callback_evidence_lost_at)
        else:
            self.fill(kind)
            prefix, ends = tuple(self.write._callback_evidence), tuple(self.write._callback_run_ends)
            self.emit(kind, 128)  # B is lost immediately after retained A.
            self.frozen(prefix, ends)
            if scenario == 'reproduction':
                self.emit(kind, 127)
                page = self.page()
                self.assertEqual(page.through_sequence, 130)
                self.assertEqual([e.sequence for e in page.events], list(range(1, 129)))
                for target in (129, 130):
                    result = qualified._coverage(page.events,
                        consumer=qualified._LedgerConsumer.DURABLE_PERSISTENCE, generation=1,
                        cursor=0, through_sequence=130, target_sequence=target)
                    self.assertIsNot(result.state, qualified._CoverageState.COMPLETE)
                    self.assertEqual(result.supported_through_sequence, 128)
            elif scenario == 'alternating':
                for fact in (127, 128, 127, 128, 127, 128): self.emit(kind, fact)
                self.assertEqual(self.write._callback_sequence, 135)
            elif scenario == 'long_duplicates':
                for _ in range(260): self.emit(kind, 128)
                self.assertEqual(self.write._callback_sequence, 389)
                self.assertEqual(len(self.page().events), 128)
            elif scenario == 'reconnect_loss':
                old = self.client
                self.write._begin_generation(); self.client = self.paper_api.clients[-1]
                self.emit(kind, 127)
                self.assertEqual(self.write._callback_sequence, 130)
                self.assertFalse(self.write._arrival_unusable)  # Loss itself is lifetime inhibition.
                self.emit(kind, 128)
                self.assertEqual(self.write._callback_sequence, 131)
                self.assertIsNot(old, self.client)
            elif scenario == 'paging_loss':
                page = self.page(127)
                self.assertEqual([e.sequence for e in page.events], [128])
                self.assertEqual(page.through_sequence, 129)
                self.assertEqual(self.page(128).events, ())
                with self.assertRaises(ReadOnlyError): self.page(129)
                self.assertEqual(self.coordinator._callback_cursor, 0)
            self.frozen(prefix, ends)
        self.check()
    return test


for _kind in KINDS:
    for _scenario in ('adjacent', 'interleaved', 'generation', 'at_capacity_duplicate',
                      'capacity_boundary', 'reproduction', 'alternating', 'long_duplicates',
                      'reconnect_loss', 'paging_loss'):
        setattr(CoverageTests, f'test_{_kind}_{_scenario}', coverage_case(_kind, _scenario))


class FailureTests(IntegrityFixture):
    def inject(self, seam, error, duplicate=False):
        self.build()
        if duplicate and seam != 'loss': self.commission()
        if seam == 'loss':
            self.fill('commission')
            if duplicate: self.emit('commission', 127)
        before = self.write._callback_sequence
        prefix, ends = tuple(self.write._callback_evidence), tuple(self.write._callback_run_ends)
        module, name = (paper, '_copy_commission') if seam == 'copy' else (
            (paper, '_evidence_identity') if seam == 'identity' else
            (self.write, {'publish_before': '_publish_callback_evidence',
                          'publish_after': '_publish_callback_evidence',
                          'watermark_before': '_advance_callback_sequence',
                          'watermark_after': '_advance_callback_sequence',
                          'loss': '_record_callback_evidence_loss',
                          'floor': '_raise_observed_floor',
                          'invariant_after': '_check_callback_evidence'}[seam]))
        original = getattr(module, name)
        calls = []
        def fail(*args, **kwargs):
            calls.append(True)
            self.assertTrue(self.write._arrival_lock._is_owned())
            if seam == 'invariant_after' and len(calls) == 1:
                return original(*args, **kwargs)
            if seam.endswith('_after'):
                original(*args, **kwargs)
            if seam == 'publish_after':
                # The events pointer changed, but original lists stayed untouched.
                self.assertIsNot(self.write._callback_evidence, prefix)
            raise error
        with patch.object(module, name, side_effect=fail):
            action = (lambda: self.emit('status', 555)) if seam == 'floor' else (
                (lambda: self.emit('commission', 999)) if seam == 'loss' else self.commission)
            if isinstance(error, Exception):
                action()  # Existing callback convention inhibits ordinary failures.
            else:
                with self.assertRaises(type(error)) as caught: action()
                self.assertIs(caught.exception, error)
        self.assertTrue(calls)
        self.assertTrue(self.write._arrival_unusable)
        if seam == 'copy':
            self.assertEqual(self.write._callback_sequence, before)
            self.assertIsNone(self.write._callback_evidence_lost_at)
            self.assertEqual(tuple(self.write._callback_evidence), prefix)
            self.assertEqual(tuple(self.write._callback_run_ends), ends)
            if not isinstance(error, Exception): self.assertTrue(self.write._requires_reconciliation())
        else:
            self.assertEqual(self.write._callback_sequence, before + 1)
            self.frozen(prefix, ends, lost=before + 1)
            if seam == 'floor': self.assertGreaterEqual(self.write._lifetime_order_floor, 1556)
            self.commission()
            self.assertEqual(self.write._callback_sequence, before + 2)
            self.frozen(prefix, ends, lost=before + 1)
        self.check()


def interruption_case(seam, exception, duplicate):
    def test(self): self.inject(seam, exception('private interruption'), duplicate)
    return test


for _seam in ('copy', 'identity', 'publish_before', 'publish_after', 'watermark_before',
              'watermark_after', 'loss', 'floor', 'invariant_after'):
    for _exception in INTERRUPTIONS:
        for _duplicate in (False, True):
            setattr(FailureTests, f'test_{_seam}_{_exception.__name__}_{"duplicate" if _duplicate else "append"}',
                    interruption_case(_seam, _exception, _duplicate))
    for _duplicate in (False, True):
        setattr(FailureTests, f'test_{_seam}_ordinary_{"duplicate" if _duplicate else "append"}',
                interruption_case(_seam, ValueError, _duplicate))


class MetadataTests(IntegrityFixture):
    def damaged(self, mutation):
        self.build(); self.commission(); self.commission(exec_id='second')
        mutation(self.write)
        try:
            page = self.page()
        except ReadOnlyError:
            pass
        else:
            self.assertEqual(page.events, ())
            self.assertIs(type(page.through_sequence), int)
            self.assertGreater(page.through_sequence, 0)
        self.assertTrue(self.write._arrival_unusable)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertEqual(self.coordinator._callback_cursor, 0)


_BAD_METADATA = {
    'missing_end': lambda w: w._callback_run_ends.pop(),
    'extra_end': lambda w: w._callback_run_ends.append(3),
    'bool_end': lambda w: w._callback_run_ends.__setitem__(0, True),
    'float_end': lambda w: w._callback_run_ends.__setitem__(0, 1.0),
    'negative_end': lambda w: w._callback_run_ends.__setitem__(0, -1),
    'end_past_watermark': lambda w: w._callback_run_ends.__setitem__(1, 3),
    'overlapping': lambda w: w._callback_run_ends.__setitem__(0, 2),
    'start_gap': lambda w: w._callback_evidence.__setitem__(0, replace(w._callback_evidence[0], sequence=2)),
    'second_start_gap': lambda w: w._callback_evidence.__setitem__(1, replace(w._callback_evidence[1], sequence=3)),
    'wrong_generation': lambda w: object.__setattr__(w._callback_evidence[0], 'generation', 0),
    'wrong_type': lambda w: w._callback_evidence.__setitem__(0, object()),
    'tuple_events': lambda w: setattr(w, '_callback_evidence', tuple(w._callback_evidence)),
    'tuple_ends': lambda w: setattr(w, '_callback_run_ends', tuple(w._callback_run_ends)),
    'bool_watermark': lambda w: setattr(w, '_callback_sequence', True),
    'watermark_gap': lambda w: setattr(w, '_callback_sequence', 3),
}


def metadata_case(mutation):
    def test(self): self.damaged(mutation)
    return test


for _name, _mutation in _BAD_METADATA.items():
    setattr(MetadataTests, f'test_{_name}', metadata_case(_mutation))


class BoundaryTests(IntegrityFixture):
    def test_marker_initial_none_without_public_attribute(self):
        self.build()
        self.assertIsNone(self.write._callback_evidence_lost_at)
        self.assertFalse(hasattr(self.write, 'callback_evidence_lost_at'))

    def test_marker_cannot_be_assigned_or_deleted(self):
        self.build()
        for name in ('_callback_evidence_lost_at', '_PaperTWSTransport__callback_evidence_lost_at'):
            for value in (None, False, True, 0, -1, 1, 129, 1.0, '129'):
                with self.subTest(name=name, value=value), self.assertRaises(ReadOnlyError):
                    setattr(self.write, name, value)
            with self.assertRaises(ReadOnlyError): delattr(self.write, name)
        self.assertIsNone(self.write._callback_evidence_lost_at)

    def test_private_loss_recorder_rejects_wrong_sequence_types(self):
        self.build()
        for value in (None, False, True, 0, -1, 1.0, '1'):
            with self.subTest(value=value), self.write._arrival_lock, self.assertRaises(ReadOnlyError):
                self.write._record_callback_evidence_loss(value)
        self.assertIsNone(self.write._callback_evidence_lost_at)

    def test_loss_first_value_never_moves(self):
        self.build(); self.fill('commission'); self.emit('commission', 128)
        with self.write._arrival_lock: self.write._record_callback_evidence_loss(200)
        self.assertEqual(self.write._callback_evidence_lost_at, 129)
        self.check()

    def test_loss_does_not_mark_arrival_unusable(self):
        self.build(); self.fill('commission'); self.emit('commission', 128)
        self.assertFalse(self.write._arrival_unusable)
        self.assertTrue(self.write._requires_reconciliation())

    def test_cached_identity_cannot_replace_retained_fact_identity(self):
        self.build(); self.commission(exec_id='first'); self.commission(exec_id='second')
        self.write._last_callback_identity = paper._evidence_identity(self.write._callback_evidence[0])
        self.commission(exec_id='first')
        self.assertEqual(self.write._callback_run_ends, [1, 2, 3])
        self.assertEqual([e.exec_id for e in self.page().events], ['first', 'second', 'first'])
        self.check()

    def test_cleanup_never_rolls_back_an_advanced_watermark(self):
        self.build()
        error = KeyboardInterrupt('private interruption')
        def interrupted(sequence):
            self.write._callback_sequence = sequence + 1
            raise error
        with patch.object(self.write, '_advance_callback_sequence', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt) as caught: self.commission()
        self.assertIs(caught.exception, error)
        self.assertEqual(self.write._callback_sequence, 2)
        self.assertEqual(self.write._callback_evidence_lost_at, 1)
        self.assertEqual(self.write._callback_evidence, [])
        self.assertEqual(self.write._callback_run_ends, [])
        self.check()

    def test_malformed_copy_has_no_evidence_loss_claim(self):
        self.build(); self.commission(amount=Decimal('NaN'))
        self.assertIsNone(self.write._callback_evidence_lost_at)
        self.assertEqual(self.write._callback_sequence, 0)
        self.assertTrue(self.write._arrival_unusable)

    def test_stale_closed_and_non_evidence_callbacks_do_not_record_loss(self):
        self.build(); old = self.client; self.write._begin_generation()
        old.wrapper.commissionReport(object())
        self.client = self.paper_api.clients[-1]
        self.client.wrapper.nextValidId(300)
        self.client.wrapper.error(0, 100, 'private ignored error')
        self.client.wrapper.openOrderEnd()
        self.client.wrapper.execDetailsEnd(0)
        self.client.wrapper.connectionClosed()
        self.commission(report=object())
        self.assertEqual(self.write._callback_sequence, 0)
        self.assertIsNone(self.write._callback_evidence_lost_at)

    def test_metadata_cannot_bridge_known_loss(self):
        self.build(); self.fill('commission'); self.emit('commission', 128)
        self.write._callback_run_ends[-1] = 129
        page = self.page()
        self.assertEqual(page.events, ())
        self.assertEqual(page.through_sequence, 129)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertEqual(self.write._callback_evidence_lost_at, 129)

    def test_old_cursor_and_foreign_owner_rejected(self):
        self.build(); self.commission()
        for cursor in (-1, True, 2, 1.0):
            with self.subTest(cursor=cursor), self.assertRaises(ReadOnlyError): self.page(cursor)
        with self.assertRaises(ReadOnlyError): self.write._evidence_since(object(), 0)

    def test_long_compressed_run_paging_is_bounded_and_does_not_acknowledge(self):
        self.build()
        for _ in range(300): self.commission()
        self.assertEqual(self.write._callback_run_ends, [300])
        first, second, third = self.page(), self.page(128), self.page(256)
        self.assertEqual([len(p.events) for p in (first, second, third)], [128, 128, 44])
        self.assertEqual([e.sequence for p in (first, second, third) for e in p.events], list(range(1, 301)))
        self.assertEqual(self.page(), first)
        self.assertEqual(self.coordinator._callback_cursor, 0)
        self.assertEqual(self.journal._persistence_cursor, 0)

    def test_loss_blocks_allocator_without_lowering_floor(self):
        self.build(); self.fill('open'); self.emit('open', 128)
        floor = self.write._lifetime_order_floor
        with self.assertRaises(ReadOnlyError): self.write._allocate_order_id(self.write._generation)
        self.assertEqual(self.write._lifetime_order_floor, floor)
        self.emit('open', 9000)
        self.assertGreaterEqual(self.write._lifetime_order_floor, 10001)

    def test_max_id_after_loss_permanently_exhausts_allocator(self):
        self.build(); self.fill('commission'); self.emit('commission', 128)
        self.status(order_id=paper.MAX_ORDER_ID)
        self.assertTrue(self.write._lifetime_order_ids_exhausted)
        self.assertEqual(self.write._lifetime_order_floor, paper.MAX_ORDER_ID)
        self.write._begin_generation()
        self.assertTrue(self.write._lifetime_order_ids_exhausted)
        self.assertEqual(self.write._callback_evidence_lost_at, 129)

    def test_loss_after_reservation_prevents_fake_sdk_invocation(self):
        self.build()
        self.prepare(lambda owner, reservation: (self.fill('commission'), self.emit('commission', 128)))
        self.consumed_without_invocation(self.invoke())
        self.assertEqual(self.write._callback_evidence_lost_at, 129)
        self.assertEqual(len(self.write_calls()), 0)

    def test_bookkeeping_wait_does_not_hide_capacity_loss(self):
        self.build(); self.fill('commission')
        arrived = Event()
        original = self.write._advance_callback_sequence
        def advance(sequence):
            original(sequence)
            arrived.set()
        with patch.object(self.write, '_advance_callback_sequence', side_effect=advance), ThreadPoolExecutor(1) as pool:
            with self.write._lock:
                pending = pool.submit(self.emit, 'commission', 128)
                self.assertTrue(arrived.wait(2))
                with self.write._arrival_lock:
                    self.assertEqual(self.write._callback_evidence_lost_at, 129)
                    self.assertTrue(self.write._requires_reconciliation())
                self.assertFalse(pending.done())
            pending.result(timeout=2)

    def test_concurrent_capture_has_unique_sequences_and_permanent_prefix(self):
        self.build()
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda fact: self.emit('commission', fact), range(180)))
        self.assertEqual(self.write._callback_sequence, 180)
        self.assertEqual(self.write._callback_evidence_lost_at, 129)
        self.assertEqual([e.sequence for e in self.page().events], list(range(1, 129)))
        self.check()

    def test_no_callback_thread_storage_network_or_owner_calls(self):
        self.build(); before = self.journal._load().records
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('storage')) as db, \
             patch.object(integration._PaperDispatchJournalOwner, '_append_event', side_effect=AssertionError('owner')) as owner, \
             patch('socket.socket', side_effect=AssertionError('network')) as network, \
             patch('socket.getaddrinfo', side_effect=AssertionError('DNS')) as dns:
            with ThreadPoolExecutor(1) as pool:
                pool.submit(self.fill, 'commission').result(timeout=5)
                pool.submit(self.emit, 'commission', 128).result(timeout=5)
                pool.submit(self.emit, 'commission', 127).result(timeout=5)
        for mocked in (db, owner, network, dns): mocked.assert_not_called()
        self.assertEqual(self.journal._load().records, before)
        self.assertEqual(self.write_calls(), [])

    def test_safety_modes_and_no_production_status_receiver(self):
        self.build()
        self.assertIs(self.write.account_mode, AccountMode.UNKNOWN)
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.broker.account_summary().mode)
        self.assertIsNone(self.write._status_registration)
        self.assertIsNone(self.write._status_delivery_owner._PaperStatusDelivery__receiver)
        self.assertEqual(self.write._status_fences, [])

    def test_helper_scope_contains_no_io_or_qualified_publication(self):
        for name in ('_capture_callback_evidence', '_publish_callback_evidence',
                     '_check_callback_evidence', '_record_callback_evidence_loss'):
            source = inspect.getsource(getattr(paper.PaperTWSTransport, name))
            ast.parse(dedent(source))
            for forbidden in ('sqlite', '_append_event', 'socket', '_StatusReceiver', '_StatusHandoff',
                              '_QualifiedOrderStatusEvidence', 'placeOrder', 'reqAllOpenOrders'):
                self.assertNotIn(forbidden, source)


class InterruptionRaceTests(IntegrityFixture):
    pass


def blocked_interruption(exception):
    def test(self):
        self.build(); self.commission()
        error = exception('private interruption')
        with patch.object(paper, '_evidence_identity', side_effect=error), ThreadPoolExecutor(1) as pool:
            with self.write._lock:
                pending = pool.submit(self.commission)
                with self.assertRaises(exception) as caught: pending.result(timeout=2)
                self.assertIs(caught.exception, error)
                self.assertTrue(self.write._reconciliation_required)
                self.assertTrue(self.write._arrival_unusable)
                self.assertEqual(self.write._callback_evidence_lost_at, 2)
                self.assertEqual(self.write._callback_run_ends, [1])
        self.check()
    return test


def paging_interruption(exception):
    def test(self):
        self.build(); self.commission()
        error = exception('private interruption')
        with patch.object(self.write, '_check_callback_evidence', side_effect=error):
            with self.assertRaises(exception) as caught: self.page()
        self.assertIs(caught.exception, error)
        self.assertTrue(self.write._reconciliation_required)
        self.assertTrue(self.write._arrival_unusable)
        self.assertIsNone(self.write._callback_evidence_lost_at)
        self.assertEqual(self.write._callback_sequence, 1)
        self.check()
    return test


for _exception in INTERRUPTIONS:
    setattr(InterruptionRaceTests, f'test_blocked_bookkeeping_{_exception.__name__}',
            blocked_interruption(_exception))
    setattr(InterruptionRaceTests, f'test_paging_{_exception.__name__}',
            paging_interruption(_exception))


class PersistenceTests(persistence.PersistenceFixture):
    def fill_status(self):
        for index in range(128): self.status(order_id=1000 + index)

    def progress_below_loss(self):
        self.assertLess(self.journal._persistence_cursor, self.write._callback_evidence_lost_at)
        for progress in self.of_type(journal._CallbackProgress):
            self.assertLess(progress.processed_sequence, self.write._callback_evidence_lost_at)

    def test_truthful_prefix_can_persist_before_loss_but_never_cross_it(self):
        self.start(); self.fill_status()
        self.assertIs(self.drain().state, persistence.State.CURRENT)
        self.assertEqual(self.journal._persistence_cursor, 128)
        self.status(order_id=1128); self.status(order_id=1127)
        self.failed(self.drain()); self.progress_below_loss()
        self.assertEqual(self.journal._persistence_cursor, 128)

    def test_loss_before_drain_cannot_persist_any_fabricated_status(self):
        self.start(); self.fill_status(); self.status(order_id=1128); self.status(order_id=1127)
        self.failed(self.drain()); self.progress_below_loss()
        self.assertEqual(self.of_type(journal._OrderStatusObserved), ())
        self.assertEqual(self.journal._persistence_cursor, 0)

    def test_post_loss_duplicates_cannot_unblock_drain(self):
        self.start(); self.fill_status(); self.status(order_id=1128)
        self.failed(self.drain())
        for _ in range(200): self.status(order_id=1127)
        self.failed(self.drain()); self.progress_below_loss()
        self.assertEqual(self.write._callback_run_ends[-1], 128)

    def test_reconnect_cannot_restore_durable_coverage(self):
        self.start(); self.fill_status(); self.status(order_id=1128)
        self.write._begin_generation(); self.client = self.paper_api.clients[-1]
        self.status(order_id=1127)
        self.failed(self.drain()); self.progress_below_loss()
        self.assertEqual(self.write._callback_evidence_lost_at, 129)

    def test_forged_span_cannot_override_loss_marker_in_drain(self):
        self.start(); self.fill_status(); self.status(order_id=1128)
        self.write._callback_run_ends[-1] = 129
        self.failed(self.drain()); self.progress_below_loss()
        self.assertEqual(self.of_type(journal._CallbackProgress), ())

    def test_loss_during_append_never_publishes_progress_across_gap(self):
        self.start(); self.fill_status()
        original = integration._PaperDispatchJournalOwner._append_event
        arrived = []
        def append(owner, event, **kwargs):
            self.assertFalse(self.write._arrival_lock._is_owned())
            if not arrived and type(event) is journal._CallbackProgress:
                arrived.append(True)
                with ThreadPoolExecutor(1) as pool:
                    pool.submit(self.status, order_id=1128).result(timeout=2)
                    pool.submit(self.status, order_id=1127).result(timeout=2)
            return original(owner, event, **kwargs)
        with patch.object(integration._PaperDispatchJournalOwner, '_append_event', append):
            self.failed(self.drain())
        self.assertTrue(arrived); self.progress_below_loss()
        self.assertEqual(self.journal._persistence_cursor, 0)

    def test_commission_overflow_does_not_manufacture_durable_economics(self):
        self.start()
        for index in range(128): self.commission(exec_id=f'execution-{index}')
        self.commission(exec_id='execution-lost'); self.commission(exec_id='execution-127')
        self.failed(self.drain()); self.progress_below_loss()
        self.assertEqual(self.of_type(journal._CommissionReceived), ())
        self.assertEqual(self.of_type(journal._CallbackProgress), ())

    def test_capture_loss_interrupt_keeps_cursors_separate(self):
        self.start(); self.status(order_id=1000)
        error = KeyboardInterrupt('private interruption')
        with patch.object(paper, '_evidence_identity', side_effect=error), self.assertRaises(KeyboardInterrupt) as caught:
            self.status(order_id=1001)
        self.assertIs(caught.exception, error)
        self.failed(self.drain()); self.progress_below_loss()
        self.assertEqual(self.journal._persistence_cursor, 0)
        self.assertEqual(self.coordinator._callback_cursor, 0)
