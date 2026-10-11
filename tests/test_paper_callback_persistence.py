"""C3C4D adversarial persistence: temporary SQLite, anonymous SDK doubles only."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from decimal import Decimal, localcontext
import inspect
from pathlib import Path
import sqlite3
from threading import Event, current_thread, get_ident
from unittest.mock import patch

from tradingbot_broker import paper_dispatch_journal as integration
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker import ibkr_paper_transport as paper
from tradingbot_broker.ibkr_readonly_broker import ReadOnlyIBKRBroker
from tradingbot_broker.paper_execution import PaperExecutionAuthorizationStatus as Auth
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests.test_paper_commission_capture import CommissionFixture
from tests.dispatch_journal_fixtures import request, permission


D = Decimal
Owner = integration._PaperDispatchJournalOwner
State = integration._CallbackPersistenceState


class PersistenceFixture(CommissionFixture):
    def start(self):
        self.build()
        outcome = self.invoke()
        self.pending_id = outcome.order_id
        self.assertEqual(len(self.write_calls()), 1)
        return outcome

    def drain(self):
        return self.coordinator._sync_callback_persistence()

    def history(self):
        return tuple(r.payload for r in self.journal._load().records)

    def of_type(self, kind):
        return tuple(e for e in self.history() if type(e) is kind)

    def failed(self, result):
        self.assertIs(result.state, State.FAILED)
        self.assertTrue(self.journal._persistence_failed)
        self.assertTrue(self.journal._inhibited)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertTrue(self.coordinator._reconciliation_required)
        self.assertTrue(self.broker.reconciliation_required)
        self.assertIs(self.broker._paper_authority.status, Auth.INVALIDATED)
        calls = len(self.write_calls())
        self.invoke(request('blocked-next'), permission(request('blocked-next')))
        self.assertEqual(len(self.write_calls()), calls)

    def replace_evidence(self, **changes):
        original = self.write._callback_evidence[-1]
        forged = object.__new__(type(original))
        for field in fields(type(original)):
            object.__setattr__(forged, field.name, changes.get(field.name, getattr(original, field.name)))
        self.write._callback_evidence[-1] = forged

    def persistence_gate(self):
        c, r, w = self.coordinator, self.transport, self.write
        with c._lock, self.broker._lock, r._lifecycle, r._condition, w._lock, w._arrival_lock:
            self.journal._require_persistence_current(c)


class MappingTests(PersistenceFixture):
    def test_open_exact_fields_and_source(self):
        self.start(); self.open(); self.assertIs(self.drain().state, State.CURRENT)
        event, = self.of_type(j._OpenOrderObserved)
        self.assertEqual((event.client_order_id, event.order_id, event.client_id, event.order_ref,
            event.con_id, event.security_type, event.exchange, event.symbol, event.currency,
            event.side, event.quantity, event.order_type, event.tif, event.status),
            (self.order.client_order_id, 10, 2, self.order.client_order_id, 17, 'STK',
             'SMART', 'DEMO', 'USD', 'BUY', D(10), 'MKT', 'DAY', j._LifecycleStatus.SUBMITTED))
        self.assertEqual(event.source, j._CallbackSource(self.journal._session.session_id, 1, 1))

    def test_optional_open_fields_remain_absent(self):
        self.start(); self.open(order_values={'clientId': None, 'orderRef': None},
            contract_values={'exchange': None, 'symbol': None, 'currency': None})
        self.assertIs(self.drain().state, State.CURRENT)
        event, = self.of_type(j._OpenOrderObserved)
        self.assertEqual((event.client_id, event.order_ref, event.exchange, event.symbol, event.currency),
                         (None, None, None, None, None))
        self.assertFalse(self.journal._load().replay.attempts[0].strong_open_order_identity_established)

    def test_unknown_open_does_not_establish_strong_identity(self):
        self.start(); self.open(status='unsupported')
        self.drain()
        self.assertIs(self.of_type(j._OpenOrderObserved)[0].status, j._LifecycleStatus.UNKNOWN)
        self.assertFalse(self.journal._load().replay.attempts[0].strong_open_order_identity_established)

    def test_status_retains_same_event_quantities_and_prices(self):
        self.start(); self.status(filled=D(3), remaining=D(7), average=1.5, last=1.75)
        self.drain(); event, = self.of_type(j._OrderStatusObserved)
        self.assertEqual((event.filled, event.remaining, event.average_price, event.last_price,
                          event.parent_id, event.client_id), (D(3), D(7), D('1.5'), D('1.75'), 0, 2))
        self.assertEqual(event.source.sequence, 1)

    def test_open_filled_never_invents_status_totals_or_executions(self):
        self.start(); self.status(); self.open(status='Filled'); self.drain()
        replay = self.journal._load().replay.attempts[0]
        self.assertEqual(replay.observed_execution_quantity, 0)
        self.assertEqual(replay.status_observations[0].filled, 0)
        self.assertFalse(replay.executions)

    def test_execution_exact_fields_and_absent_uncaptured_fields(self):
        self.start(); self.execution(); self.drain()
        event, = self.of_type(j._ExecutionObserved)
        self.assertEqual((event.exec_id, event.con_id, event.side, event.shares, event.price),
                         ('execution-1', 17, 'BOT', D(4), D('1.25')))
        self.assertIsNone(event.cumulative_quantity); self.assertIsNone(event.execution_time)
        self.assertEqual((event.order_id, event.client_id, event.order_ref, event.source.sequence),
                         (10, 2, self.order.client_order_id, 1))

    def test_missing_reference_requires_earlier_durable_strong_open(self):
        self.start(); self.open(); self.execution(values={'orderRef': None}); self.drain()
        self.assertEqual(self.of_type(j._ExecutionObserved)[0].source.sequence, 2)

    def test_later_open_cannot_manufacture_prior_execution_anchor(self):
        self.start(); self.execution(values={'orderRef': None}); self.open()
        self.failed(self.drain()); self.assertEqual(self.journal._persistence_cursor, 0)

    def test_partial_execution_is_not_a_position_mutation(self):
        self.start(); account = self.broker.account_summary(); self.execution(); self.drain()
        self.assertEqual(self.journal._load().replay.attempts[0].observed_execution_quantity, 4)
        self.assertEqual(self.broker.account_summary(), account)
        self.assertTrue(self.coordinator._pending_confirmation)

    def test_full_execution_retains_recovery_obligations(self):
        self.start(); self.execution(values={'shares': D(10)}); self.drain()
        replay = self.journal._load().replay
        self.assertEqual(replay.attempts[0].observed_execution_quantity, 10)
        self.assertTrue(replay.attempts[0].unresolved_obligations)
        self.assertTrue(replay.restart_quarantine_required)

    def test_unrelated_different_numeric_order_never_creates_target_fact(self):
        self.start(); self.open(order_id=99); self.execution(order_id=99)
        self.drain(); self.assertFalse(self.of_type(j._ExecutionObserved))
        self.assertFalse(self.of_type(j._OpenOrderObserved)); self.assertEqual(self.journal._persistence_cursor, 2)

    def test_owned_execid_relationship_precedes_unrelated_numeric_filter(self):
        self.start(); self.execution(); self.drain()
        self.execution(order_id=99); self.failed(self.drain())
        self.assertEqual(self.journal._persistence_cursor, 1)

    def test_distinct_execution_overfill_quarantines_entire_history(self):
        self.start(); self.execution(values={'shares': D(6)})
        self.execution(values={'execId': 'execution-2', 'shares': D(5)})
        self.failed(self.drain()); self.assertTrue(self.journal._store._quarantined)
        with self.assertRaises(ReadOnlyError): self.journal._load()

    def test_low_decimal_precision_cannot_hide_distinct_overfill(self):
        self.start(); self.execution(values={'shares': D('5.00000000000000000000000000001')})
        self.execution(values={'execId': 'execution-2', 'shares': D(5)})
        with localcontext() as context:
            context.prec = 3
            self.failed(self.drain())
        self.assertTrue(self.journal._store._quarantined)

    def test_preinvocation_numeric_collision_never_becomes_owned_execution(self):
        self.prepare(lambda c, res: self.execution(order_id=res.order_id))
        self.invoke(); self.failed(self.drain()); self.assertEqual(len(self.write_calls()), 0)


class CommissionTests(PersistenceFixture):
    def test_after_execution_exact_mapping(self):
        self.start(); self.execution(); self.commission('fees', amount=D('-0.03')); self.drain()
        event, = self.of_type(j._CommissionReceived)
        self.assertEqual((event.client_order_id, event.exec_id, event.amount, event.currency, event.received_at),
            (self.order.client_order_id, 'execution-1', D('-0.03'), 'USD', self.receipt))
        self.assertEqual(event.report_id, self.page().events[1].report_id)
        self.assertFalse(hasattr(event, 'source'))

    def test_before_execution_stages_without_sqlite_commission_or_progress(self):
        self.start(); self.commission(); result = self.drain()
        self.assertIs(result.state, State.PENDING); self.assertEqual(result.staged_count, 1)
        self.assertEqual(result.cursor, 0); self.assertFalse(self.of_type(j._CommissionReceived))
        self.assertFalse(self.of_type(j._CallbackProgress))

    def test_later_execution_unlocks_staged_commission(self):
        self.start(); self.commission(); self.drain(); self.execution(); self.drain()
        self.assertEqual(self.journal._persistence_cursor, 2)
        history = self.history()
        execution = self.of_type(j._ExecutionObserved)[0]
        self.assertEqual(execution.source.sequence, 2)
        self.assertLess(history.index(execution), history.index(self.of_type(j._CommissionReceived)[0]))

    def test_lookahead_cannot_skip_another_unattributed_commission(self):
        self.start(); self.commission(); self.commission(exec_id='unknown-execution'); self.execution()
        self.drain(); self.assertEqual(self.journal._persistence_cursor, 1)
        self.assertEqual(self.of_type(j._ExecutionObserved)[0].source.sequence, 3)
        self.assertEqual(self.of_type(j._CallbackProgress)[-1].processed_sequence, 1)

    def test_legacy_and_fees_share_one_durable_economic_fact(self):
        self.start(); self.execution(); self.commission(); self.commission('fees'); self.drain()
        self.assertEqual(len(self.of_type(j._CommissionReceived)), 1)
        self.assertEqual(self.journal._persistence_cursor, 3)

    def test_different_receipt_times_are_duplicate_observations(self):
        self.start(); self.execution(); self.commission(); first = self.receipt
        self.receipt += timedelta(seconds=1); self.commission('fees'); self.drain()
        self.assertEqual(len(self.of_type(j._CommissionReceived)), 1)
        self.assertEqual(self.of_type(j._CommissionReceived)[0].received_at, first)
        self.assertEqual(self.journal._persistence_cursor, 3)

    def test_conflicting_amount_never_chooses_latest(self):
        self.start(); self.execution(); self.commission(); self.drain()
        self.commission(amount=D('0.06')); self.failed(self.drain())
        self.assertEqual(self.of_type(j._CommissionReceived)[0].amount, D('0.05'))

    def test_conflicting_currency_never_converts(self):
        self.start(); self.execution(); self.commission(); self.drain()
        self.commission(currency='EUR'); self.failed(self.drain())

    def test_currency_must_match_consumed_order(self):
        self.start(); self.execution(); self.commission(currency='EUR')
        self.failed(self.drain()); self.assertTrue(self.journal._store._quarantined)

    def test_forged_report_fingerprint_is_not_authority(self):
        self.start(); self.execution(); self.commission(); self.replace_evidence(report_id='commission-' + '0'*64)
        self.failed(self.drain())

    def test_foreign_execution_link_can_prove_unrelated_commission(self):
        self.start(); self.execution(order_id=99); self.commission(); self.drain()
        self.assertEqual(self.journal._persistence_cursor, 2)
        self.assertFalse(self.of_type(j._CommissionReceived))

    def test_same_execid_cannot_switch_from_foreign_to_owned(self):
        self.start(); self.execution(order_id=99); self.drain()
        self.execution(); self.failed(self.drain())

    def test_missing_commission_stays_unknown(self):
        self.start(); self.execution(); self.drain()
        replay = self.journal._load().replay.attempts[0]
        self.assertFalse(replay.commissions)
        self.assertIn(j._UnresolvedObligation.COMMISSION_ACCOUNTING, replay.unresolved_obligations)

    def test_commission_never_clears_pending_confirmation(self):
        self.start(); self.execution(); self.commission(); self.drain()
        self.assertTrue(self.coordinator._pending_confirmation)
        self.assertIsNotNone(self.coordinator._pending_order)


class SequenceTests(PersistenceFixture):
    def test_empty_stream_starts_at_zero(self):
        self.start(); result = self.drain()
        self.assertIs(result.state, State.CURRENT); self.assertEqual(result.cursor, 0)

    def test_cursor_not_initialized_from_coordinator_cursor(self):
        self.start(); self.open(); self.coordinator._callback_cursor = 1
        self.assertEqual(self.journal._persistence_cursor, 0); self.drain()
        self.assertEqual(len(self.of_type(j._OpenOrderObserved)), 1)

    def test_persistence_does_not_process_coordinator_cursor(self):
        self.start(); self.open(); self.drain()
        self.assertEqual(self.coordinator._callback_cursor, 0)
        self.assertEqual(self.journal._persistence_cursor, 1)

    def test_fetch_is_not_acknowledgement(self):
        self.start(); self.open(); self.page()
        self.assertEqual(self.journal._persistence_cursor, 0)

    def test_adjacent_duplicate_occurrences_checked_independently(self):
        self.start()
        for _ in range(12): self.open()
        self.drain(); self.assertEqual(self.journal._persistence_cursor, 12)
        self.assertEqual(len(self.of_type(j._OpenOrderObserved)), 1)

    def test_duplicate_run_crosses_page_boundary(self):
        self.start()
        for _ in range(260): self.open()
        self.drain(); self.assertEqual(self.journal._persistence_cursor, 260)
        self.assertEqual([p.processed_sequence for p in self.of_type(j._CallbackProgress)], [128, 256, 260])

    def test_exact_128_page_boundary(self):
        self.start()
        for _ in range(128): self.open()
        self.drain(); self.assertEqual(self.journal._persistence_cursor, 128)

    def test_interleaved_observations_are_not_coalesced(self):
        self.start(); self.open(); self.status(); self.open(); self.drain()
        self.assertEqual([e.source.sequence for e in self.of_type(j._OpenOrderObserved)], [1, 3])

    def test_compressed_span_with_unseen_sequence_fails(self):
        self.start(); self.open(); self.open()
        self.write._callback_evidence[0] = replace(self.write._callback_evidence[0], sequence=2)
        self.failed(self.drain()); self.assertEqual(self.journal._persistence_cursor, 0)

    def test_missing_entire_page_is_not_through_acknowledgement(self):
        self.start(); self.open(); self.write._callback_evidence.clear(); self.write._callback_run_ends.clear()
        self.failed(self.drain()); self.assertEqual(self.journal._persistence_cursor, 0)

    def test_transport_overflow_is_permanently_blocked(self):
        self.start()
        for number in range(129): self.status(order_id=100+number)
        self.failed(self.drain()); self.assertLess(self.journal._persistence_cursor, self.write._callback_sequence)

    def test_unresolved_staging_capacity_cannot_hide_suffix(self):
        self.start()
        for number in range(129): self.commission(exec_id='unknown-'+str(number))
        self.failed(self.drain()); self.assertLessEqual(len(self.journal._PaperDispatchJournalOwner__staged), 128)

    def test_lookahead_staging_over_compressed_page_is_bounded(self):
        self.start()
        for _ in range(129): self.commission()
        self.failed(self.drain()); self.assertEqual(self.journal._persistence_cursor, 0)

    def test_progress_follows_evidence_and_verification(self):
        self.start(); self.open(); seen = []
        original = Owner._append_event
        def append(owner, event, **kwargs):
            if type(event) is j._CallbackProgress:
                self.assertEqual(owner._persistence_cursor, 0)
                self.assertTrue(self.of_type(j._OpenOrderObserved))
                seen.append('progress')
            return original(owner, event, **kwargs)
        with patch.object(Owner, '_append_event', append): self.drain()
        self.assertEqual(seen, ['progress']); self.assertEqual(self.journal._persistence_cursor, 1)

    def test_repeated_drain_does_not_append_or_retry(self):
        self.start(); self.open(); self.drain(); count = len(self.history())
        with patch.object(Owner, '_append_event', side_effect=AssertionError('unexpected append')):
            self.assertIs(self.drain().state, State.CURRENT)
        self.assertEqual(len(self.history()), count)


class BoundaryTests(PersistenceFixture):
    def test_wrong_thread_fails_before_sqlite(self):
        self.start(); self.open()
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite reached')) as begin:
            with ThreadPoolExecutor(max_workers=1) as pool: result = pool.submit(self.drain).result(timeout=5)
        begin.assert_not_called(); self.failed(result)

    def test_worker_memory_sync_never_claims_durability(self):
        self.start(); self.open()
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite reached')):
            with ThreadPoolExecutor(max_workers=1) as pool: pool.submit(self.sync).result(timeout=5)
        self.assertEqual(self.coordinator._callback_cursor, 1)
        self.assertEqual(self.journal._persistence_cursor, 0)
        with self.assertRaises(ReadOnlyError): self.persistence_gate()

    def test_sqlite_never_holds_capture_locks_or_transaction_at_sdk(self):
        self.start(); self.open(); original = storage._SQLiteJournalStore._begin
        def begin(store, writing):
            for lock in (self.write._lock, self.write._arrival_lock, self.transport._lifecycle, self.transport._condition):
                self.assertFalse(lock._is_owned())
            self.assertIs(current_thread(), self.journal._thread)
            return original(store, writing)
        with patch.object(storage._SQLiteJournalStore, '_begin', begin): self.drain()
        self.assertFalse(self.journal._store._connection.in_transaction)

    def test_callback_capture_and_bookkeeping_complete_during_slow_sqlite(self):
        self.start(); self.open(); started = Event(); captured = Event()
        original = storage._commit
        def worker():
            self.assertTrue(started.wait(5)); self.status(); captured.set()
        def commit(connection):
            started.set(); self.assertTrue(captured.wait(5)); return original(connection)
        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(worker)
            with patch.object(storage, '_commit', commit): result = self.drain()
            task.result(timeout=5)
        self.assertEqual(result.cursor, 1); self.assertIs(result.state, State.PENDING)
        self.drain(); self.assertEqual(self.journal._persistence_cursor, 2)

    def test_error_during_sqlite_prevents_cursor_publication(self):
        self.start(); self.open(); original = storage._commit; fired = []
        def commit(connection):
            if not fired:
                fired.append(True)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(self.client.wrapper.error, -1, 321, 'discarded').result(timeout=5)
            return original(connection)
        with patch.object(storage, '_commit', commit): result = self.drain()
        self.failed(result); self.assertEqual(self.journal._persistence_cursor, 0)

    def test_generation_change_during_sqlite_is_not_migration(self):
        self.start(); self.open(); original = storage._commit; fired = []
        def commit(connection):
            if not fired:
                fired.append(True)
                with ThreadPoolExecutor(max_workers=1) as pool: pool.submit(self.write._begin_generation).result(timeout=5)
            return original(connection)
        with patch.object(storage, '_commit', commit): self.failed(self.drain())

    def test_reentrant_drain_during_sdk_defers_without_sqlite(self):
        self.build(); original = self.client.placeOrder; seen = []
        def place(*args):
            with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('nested SQLite')):
                self.open(order_id=args[0]); seen.append(self.drain())
            return original(*args)
        self.client.placeOrder = place; self.invoke()
        self.assertIs(seen[0].state, State.DEFERRED)
        self.assertFalse(self.of_type(j._OpenOrderObserved)); self.drain()
        history = self.history()
        self.assertLess(next(i for i,e in enumerate(history) if type(e) is j._SdkReturnedNormally),
                        next(i for i,e in enumerate(history) if type(e) is j._OpenOrderObserved))

    def test_reentrant_memory_sync_does_not_consume_future_pending_callbacks(self):
        self.build(); original = self.client.placeOrder
        def place(*args):
            self.open(order_id=args[0]); result = self.sync()
            self.assertEqual(result.processed_sequence, 0)
            return original(*args)
        self.client.placeOrder = place; self.invoke(); self.sync()
        self.assertEqual(self.coordinator._callback_cursor, 1)
        self.assertEqual(self.journal._persistence_cursor, 1)

    def test_repeated_staged_denial_does_not_upgrade_or_reclose_attempt(self):
        self.build(); self.commission(); self.invoke(); self.invoke()
        self.assertEqual(len(self.write_calls()), 0)
        self.assertEqual(len(self.of_type(j._AttemptConsumed)), 1)
        self.assertEqual(len(self.of_type(j._AttemptClosedWithoutInvocation)), 1)
        self.assertFalse(self.journal._store._quarantined)

    def test_equal_reconstructed_store_header_is_rejected(self):
        self.start(); self.open()
        object.__setattr__(self.journal._store, '_header', replace(self.journal._header))
        self.failed(self.drain())

    def test_pending_identity_substitution_during_commit_is_rejected(self):
        self.start(); self.open(); original = storage._commit; fired = []
        def commit(connection):
            if not fired:
                fired.append(True)
                self.coordinator._pending_order = replace(self.coordinator._pending_order)
            return original(connection)
        with patch.object(storage, '_commit', commit): self.failed(self.drain())
        self.assertEqual(self.journal._persistence_cursor, 0)

    def test_persistence_gate_is_pure_and_rejects_undurable_occurrence(self):
        self.start()
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite')):
            self.persistence_gate()
            self.open()
            with self.assertRaises(ReadOnlyError): self.persistence_gate()

    def test_reentrant_drain_during_drain_does_not_clear_guard(self):
        self.start(); self.open(); original = Owner._append_callback; seen = []
        def append(owner, c, event, staged):
            seen.append(self.drain()); self.assertTrue(owner._PaperDispatchJournalOwner__draining)
            return original(owner, c, event, staged)
        with patch.object(Owner, '_append_callback', append): self.drain()
        self.assertIs(seen[0].state, State.DEFERRED)

    def test_callback_worker_has_zero_sqlite_or_owner_calls(self):
        self.start()
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite')) as begin, \
             patch.object(Owner, '_append_event', side_effect=AssertionError('owner append')) as append:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(self.execution).result(timeout=5); pool.submit(self.commission, 'fees').result(timeout=5)
        begin.assert_not_called(); append.assert_not_called()
        self.assertEqual(self.journal._persistence_cursor, 0); self.drain()

    def test_callbacks_during_sdk_exception_remain_evidence_only(self):
        self.build()
        def place(order_id, *args):
            self.execution(order_id=order_id); self.commission(); raise RuntimeError('discarded SDK error')
        self.client.placeOrder = place; self.invoke(); self.drain()
        self.assertEqual(self.journal._persistence_cursor, 2)
        self.assertTrue(self.journal._inhibited); self.assertFalse(self.of_type(j._SdkReturnedNormally))

    def test_inhibited_owner_can_preserve_evidence_without_clearing(self):
        self.start(); self.execution(); self.sync()
        self.assertTrue(self.journal._inhibited)
        self.commission(); self.drain()
        self.assertEqual(len(self.of_type(j._CommissionReceived)), 1)
        self.assertTrue(self.journal._inhibited); self.assertTrue(self.broker.reconciliation_required)

    def test_no_retry_after_failed_drain(self):
        self.start(); self.open()
        with patch.object(Owner, '_append_callback', side_effect=storage._StorageError(storage._Code.BUSY)):
            self.failed(self.drain())
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('retry')) as begin:
            self.assertIs(self.drain().state, State.FAILED)
        begin.assert_not_called()

    def test_real_sqlite_busy_does_not_release_any_order_id(self):
        self.start(); self.open(); path = Path(self.temporary.name) / 'dispatch-1.db'
        with closing(sqlite3.connect(path, isolation_level=None, timeout=0)) as connection:
            connection.execute('BEGIN IMMEDIATE')
            try: self.failed(self.drain())
            finally: connection.rollback()
        self.assertEqual(self.write._lifetime_order_floor, 11)

    def test_result_and_staging_are_private_opaque_data(self):
        self.start(); self.commission(); result = self.drain()
        with self.assertRaises(FrozenInstanceError): result.cursor = 99
        self.assertFalse(hasattr(result, '__dict__'))
        self.assertNotIn('execution-1', repr(result)); self.assertNotIn(self.order.client_order_id, repr(result))
        self.assertFalse(any(name in {f.name for f in fields(result)} for name in ('authorized','can_clear','safe_to_trade')))

    def test_readonly_public_broker_and_mode_boundaries(self):
        self.start(); self.execution(); self.drain()
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.broker.account_summary().mode)
        self.assertIn('_blocked', inspect.getsource(ReadOnlyIBKRBroker.place_order))
        self.assertTrue(self.journal._load().replay.restart_quarantine_required)

    def test_store_thread_checks_are_preserved(self):
        self.assertNotIn('check_same_thread=False', inspect.getsource(storage._connect))

    def test_undurable_staged_commission_blocks_new_reservation(self):
        self.build(); self.commission(); self.invoke()
        self.assertEqual(len(self.write_calls()), 0); self.assertEqual(self.write._lifetime_order_floor, 10)
        self.assertEqual(self.journal._persistence_cursor, 0)

    def test_current_persistence_does_not_clear_broker_state_barrier(self):
        self.start(); self.open(); self.sync(); self.assertEqual(self.journal._persistence_cursor, 1)
        self.assertTrue(self.coordinator._broker_state_reconciliation_required)
        before = len(self.write_calls()); self.invoke(request('next'), permission(request('next')))
        self.assertEqual(len(self.write_calls()), before)


def malformed_case(kind, field, value):
    def test(self):
        self.start()
        {'open': self.open, 'status': self.status, 'execution': self.execution, 'commission': self.commission}[kind]()
        self.replace_evidence(**{field: value})
        self.failed(self.drain()); self.assertEqual(self.journal._persistence_cursor, 0)
    return test


class MalformedTests(PersistenceFixture):
    pass


for kind, field, value in (
    ('open','generation',True), ('open','sequence',0), ('open','order_id',True),
    ('open','client_id',True), ('open','con_id',0), ('open','security_type','OPT'),
    ('open','exchange','IEX'), ('open','symbol','OTHER'), ('open','currency','EUR'),
    ('open','side','SELL'), ('open','quantity',D(0)), ('open','order_type','LMT'),
    ('open','tif','GTC'), ('open','status','arbitrary'), ('open','order_ref','different-reference'),
    ('status','generation',2), ('status','sequence',2), ('status','client_id',99),
    ('status','filled',D('NaN')), ('status','remaining',D(-1)), ('status','average_price',D('Infinity')),
    ('status','last_price',D(-1)), ('status','parent_id',-1), ('status','filled',D(11)),
    ('execution','generation',2), ('execution','client_id',99), ('execution','order_ref','other'),
    ('execution','execution_id',''), ('execution','con_id',18), ('execution','side','SLD'),
    ('execution','shares',D(11)), ('execution','price',D(-1)), ('execution','shares',D('NaN')),
    ('commission','generation',2), ('commission','exec_id',''), ('commission','amount',D('NaN')),
    ('commission','currency','account-like'), ('commission','received_at',None)):
    name = 'test_' + kind + '_' + field
    if hasattr(MalformedTests, name): name += '_alternative'
    setattr(MalformedTests, name, malformed_case(kind, field, value))


class FaultTests(PersistenceFixture):
    def unknown_commit(self, event_type, committed_first):
        self.start(); self.open()
        original_append, original_commit = Owner._append_event, storage._commit
        def commit(connection):
            if committed_first:
                original_commit(connection)
            raise sqlite3.OperationalError('discarded commit diagnostic')
        def append(owner, event, **kwargs):
            if type(event) is event_type:
                with patch.object(storage, '_commit', commit):
                    return original_append(owner, event, **kwargs)
            return original_append(owner, event, **kwargs)
        with patch.object(Owner, '_append_event', append):
            self.failed(self.drain())
        self.assertTrue(self.journal._store._poisoned)
        self.assertEqual(self.journal._persistence_cursor, 0)
        with closing(sqlite3.connect(Path(self.temporary.name) / 'dispatch-1.db')) as connection:
            payloads = [storage._decode(row[0], j._JournalRecord).payload
                        for row in connection.execute('SELECT encoded FROM records ORDER BY sequence')]
        self.assertEqual(sum(type(event) is event_type for event in payloads), int(committed_first))
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('retry')) as begin:
            self.assertIs(self.drain().state, State.FAILED)
        begin.assert_not_called()

    def test_evidence_unknown_before_actual_commit(self):
        self.unknown_commit(j._OpenOrderObserved, False)

    def test_evidence_unknown_after_actual_commit(self):
        self.unknown_commit(j._OpenOrderObserved, True)

    def test_progress_unknown_before_actual_commit(self):
        self.unknown_commit(j._CallbackProgress, False)

    def test_progress_unknown_after_actual_commit(self):
        self.unknown_commit(j._CallbackProgress, True)


def storage_failure_case(code, kind):
    def test(self):
        self.start(); self.open(); original = Owner._append_event
        def append(owner, event, **kwargs):
            if type(event) is kind: raise storage._StorageError(code)
            return original(owner, event, **kwargs)
        with patch.object(Owner, '_append_event', append): self.failed(self.drain())
        self.assertEqual(self.journal._persistence_cursor, 0)
    return test


for code in (storage._Code.BUSY, storage._Code.STORAGE_FAILURE, storage._Code.POISONED,
             storage._Code.QUARANTINED, storage._Code.COMMIT_OUTCOME_UNKNOWN,
             storage._Code.CORRUPT, storage._Code.CLOSED, storage._Code.HEADER_MISMATCH):
    for kind in (j._OpenOrderObserved, j._CallbackProgress):
        setattr(FaultTests, 'test_' + code.value.lower() + '_' + kind.__name__, storage_failure_case(code, kind))


class InterruptionTests(PersistenceFixture):
    pass


def interruption_case(error_type, seam):
    def test(self):
        self.start(); self.open(); error = error_type(37) if error_type is SystemExit else error_type()
        if seam == 'association':
            guard = patch.object(Owner, '_cover_callback', side_effect=error)
        elif seam == 'publication':
            guard = patch.object(Owner, '_publish_callback_cursor', side_effect=error)
        else:
            original = Owner._append_event
            kind = j._OpenOrderObserved if seam.startswith('evidence') else j._CallbackProgress
            def append(owner, event, **kwargs):
                if type(event) is kind:
                    if seam.endswith('after'): original(owner, event, **kwargs)
                    raise error
                return original(owner, event, **kwargs)
            guard = patch.object(Owner, '_append_event', append)
        with guard, self.assertRaises(error_type) as caught: self.drain()
        self.assertIs(caught.exception, error)
        self.assertTrue(self.journal._inhibited); self.assertTrue(self.broker.reconciliation_required)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertIs(self.broker._paper_authority.status, Auth.INVALIDATED)
        self.assertEqual(self.journal._persistence_cursor, 0)
        if seam in ('progress_after', 'publication'):
            self.assertEqual(self.of_type(j._CallbackProgress)[-1].processed_sequence, 1)
    return test


for error_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
    for seam in ('evidence_before','evidence_after','progress_before','progress_after','publication','association'):
        setattr(InterruptionTests, 'test_' + error_type.__name__ + '_' + seam, interruption_case(error_type, seam))


def amount_case(amount):
    def test(self):
        self.start(); self.execution(); self.commission(amount=amount); self.drain()
        self.assertEqual(self.of_type(j._CommissionReceived)[0].amount.as_tuple(), amount.as_tuple())
        self.assertEqual(self.journal._persistence_cursor, 2)
    return test


for name, amount in (('positive',D('0.05')),('rebate',D('-0.05')),('zero',D(0)),('signed_zero',D('-0'))):
    setattr(CommissionTests, 'test_exact_' + name, amount_case(amount))


def association_case(index):
    def test(self):
        self.start(); self.open()
        association = list(self.journal._PaperDispatchJournalOwner__association)
        value = association[index]
        association[index] = replace(value) if index in (4,5,7) else object()
        object.__setattr__(self.journal, '_PaperDispatchJournalOwner__association', tuple(association))
        self.failed(self.drain()); self.assertEqual(self.journal._persistence_cursor, 0)
    return test


for index, name in enumerate(('coordinator','control','read','write','receipt','header','store','session','thread')):
    setattr(BoundaryTests, 'test_substituted_' + name, association_case(index))
