"""PRE2: fake SDK only; deterministic arrival races, no journal or network."""

import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal
from pathlib import Path
from threading import Event, get_ident
from unittest.mock import patch

from tradingbot_broker import ibkr_paper_transport as paper
from tradingbot_broker import paper_dispatch as dispatch
from tradingbot_broker.broker import BrokerOperationError
from tradingbot_broker.models import TradingMode
from tradingbot_broker.paper_dispatch import _PaperOrderDispatchCoordinator as Coordinator
from tradingbot_broker.paper_dispatch import _BridgeState as State
from tradingbot_broker.paper_execution import PaperExecutionAuthorizationStatus as AuthStatus
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests.broker_fixtures import request, permission
from tests.test_paper_callback_confirmation import CallbackFixture
from tests.test_ibkr_paper_transport import sdk


D = Decimal


class ReservationFixture(CallbackFixture):
    def prepare(self, action):
        original = Coordinator._prepare_commit
        def prepared(owner, reservation):
            self.reservation = reservation
            original(owner, reservation)
            action(owner, reservation)
        with patch.object(Coordinator, '_prepare_commit', prepared):
            self.build()

    def validate(self, action):
        original = Coordinator._validate_commit
        def checked(owner, reservation, challenge):
            self.reservation = reservation
            action(owner, reservation)
            return original(owner, reservation, challenge)
        with patch.object(Coordinator, '_validate_commit', checked):
            self.build()

    @contextmanager
    def outer(self):
        c, r, w = self.coordinator, self.transport, self.write
        with c._lock, self.broker._lock, r._lifecycle, r._condition, w._lock, w._arrival_lock:
            yield

    def consumed_without_invocation(self, result):
        self.assert_denied(result)
        self.assertGreaterEqual(self.write._lifetime_order_floor, 11)
        self.assertIn(self.order.client_order_id, self.coordinator._attempts)
        self.assertIsNone(self.write._active_reservation)
        self.assertIsNone(self.write._active_commitment)
        self.assertIsNone(self.coordinator._prepared_reservation)

    def arrival(self, kind, reservation):
        order_id = reservation.order_id
        if kind == 'open': self.open(order_id=order_id)
        elif kind == 'open_foreign_ref':
            self.open(order_id=order_id, order_values={'orderRef': 'other-reference'})
        elif kind == 'open_higher':
            self.open(order_id=order_id+100, order_values={'orderRef': 'unrelated'})
        elif kind == 'open_lower':
            self.open(order_id=order_id-1, order_values={'orderRef': 'unrelated'})
        elif kind == 'status': self.status(order_id=order_id)
        elif kind == 'execution': self.execution(order_id=order_id)
        elif kind == 'error': self.client.wrapper.error(-1, 321, 'discarded broker text')
        elif kind == 'closed': self.client.wrapper.connectionClosed()
        elif kind == 'next': self.client.wrapper.nextValidId(order_id+100)
        elif kind == 'malformed': self.status(order_id=order_id, filled=D('NaN'))
        else: raise AssertionError('Unknown fixture arrival')


class ProvenanceTests(ReservationFixture):
    def test_exact_reservation_handoff_and_commitment(self):
        def action(owner, res):
            self.assertIs(owner._prepared_reservation, res)
            self.assertIs(owner._active_attempt, owner._prepared_attempt)
            self.assertEqual((res.generation, res.order_id, res.client_id), (1, 10, 2))
            self.assertEqual((res.con_id, res.side, res.quantity, res.order_type, res.tif,
                              res.order_ref), (17, 'BUY', D(10), 'MKT', 'DAY', self.order.client_order_id))
            self.assertEqual(res.order_id_floor, 11)
            self.assertFalse(res.allocator_exhausted)
            self.assertEqual(res.arrival_marker, self.write._arrival_sequence)
            self.assertEqual(res.callback_watermark, self.write._callback_sequence)
        self.prepare(action)
        original = self.client.placeOrder
        def invoked(*args):
            c = self.write._active_commitment
            self.assertIs(c.reservation, self.reservation)
            self.assertTrue(self.write._commitment_consumed)
            self.assertTrue(self.write._write_invocation_started)
            self.assertEqual(self.write._commit_callback_sequence, c.callback_watermark)
            self.assertFalse(self.write._arrival_lock._is_owned())
            original(*args)
        self.client.placeOrder = invoked
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)
        self.assertEqual(len(self.write_calls()), 1)

    def test_equal_reconstructed_reservation_has_no_provenance(self):
        def action(owner, res):
            clone = replace(res)
            self.assertEqual(clone, res)
            with self.write._arrival_lock, self.assertRaises(ReadOnlyError):
                self.write._validate_reservation(owner, clone)
        self.prepare(action)
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)

    def test_foreign_coordinator_has_no_provenance(self):
        def action(owner, res):
            foreign = object.__new__(Coordinator)
            with self.write._arrival_lock, self.assertRaises(ReadOnlyError):
                self.write._validate_reservation(foreign, res)
        self.prepare(action)
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)

    def test_foreign_transport_has_no_provenance(self):
        other_api = sdk()
        with patch.object(paper, '_load_paper_api', return_value=other_api):
            other = paper.PaperTWSTransport(paper.PaperTWSConfig(
                paper.PaperTransportIntent.LOCAL_PAPER_FOUNDATION, client_id=3))
        self.addCleanup(other.disconnect)
        def action(owner, res):
            with other._lock, other._arrival_lock, self.assertRaises(ReadOnlyError):
                other._validate_reservation(owner, res)
        self.prepare(action)
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)

    def test_prepared_reservation_substitution_denied(self):
        self.validate(lambda owner, res: setattr(owner, '_prepared_reservation', replace(res)))
        self.consumed_without_invocation(self.invoke())

    def test_equal_attempt_substitution_denied(self):
        self.validate(lambda owner, res: setattr(owner, '_active_attempt', replace(owner._active_attempt)))
        self.consumed_without_invocation(self.invoke())

    def test_reservation_and_commitment_are_frozen_slotted_opaque(self):
        self.prepare(lambda owner, res: None)
        saved = []
        original = self.write._consume_commitment
        def consume(c):
            saved.append(c)
            original(c)
        self.write._consume_commitment = consume
        self.invoke()
        for value in (self.reservation, saved[0]):
            self.assertFalse(hasattr(value, '__dict__'))
            name = fields(value)[0].name
            with self.assertRaises(FrozenInstanceError): setattr(value, name, None)
            self.assertNotIn(self.order.client_order_id, repr(value))
            self.assertNotIn('object at', repr(value))
        self.assertNotIn('account', {f.name for f in fields(self.reservation)})

    def test_completed_reservation_cannot_be_reused(self):
        self.prepare(lambda owner, res: None)
        self.invoke()
        with self.outer(), self.assertRaises(ReadOnlyError):
            self.write._validate_reservation(self.coordinator, self.reservation)

    def test_failed_reservation_cannot_be_reused_for_new_attempt(self):
        saved = []
        def action(owner, res):
            if saved:
                with self.write._arrival_lock, self.assertRaises(ReadOnlyError):
                    self.write._validate_reservation(owner, saved[0])
            saved.append(res)
            raise ReadOnlyError('DENIED_FIXTURE')
        self.prepare(action)
        self.assert_denied(self.invoke())
        order = request('second-attempt')
        self.assert_denied(self.invoke(order, permission(order)))
        self.assertEqual([r.order_id for r in saved], [10, 11])
        self.assertEqual(self.write._lifetime_order_floor, 12)

    def test_commitment_cannot_be_consumed_twice(self):
        self.build()
        original = self.write._consume_commitment
        def consume(c):
            original(c)
            with self.assertRaises(ReadOnlyError): original(c)
        self.write._consume_commitment = consume
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)
        self.assertEqual(len(self.write_calls()), 1)

    def test_equal_commitment_is_not_authority(self):
        self.build()
        original = self.write._consume_commitment
        def consume(c):
            with self.assertRaises(ReadOnlyError): original(replace(c))
            original(c)
        self.write._consume_commitment = consume
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)

    def test_reentrant_attempt_cannot_replace_active_attempt(self):
        def action(owner, res):
            other = request('recursive-attempt')
            active = owner._active_attempt
            result = self.invoke(other, permission(other))
            self.assertIs(result.state, State.DENIED)
            self.assertIs(owner._active_attempt, active)
            self.assertIs(owner._prepared_reservation, res)
            self.assertIn(other.client_order_id, owner._attempts)
        self.prepare(action)
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)
        self.assertEqual(len(self.write_calls()), 1)

    def test_bound_direct_new_without_owner_locks_never_invokes(self):
        self.build()
        result = self.write._dispatch_new(self.coordinator._translate(self.order))
        self.assertIs(result.state, paper._DispatchState.NOT_DISPATCHED)
        self.assertFalse(self.write_calls())
        self.assertEqual(self.write._lifetime_order_floor, 11)


def changed_reservation(field, value):
    def test(self):
        self.validate(lambda owner, res: object.__setattr__(res, field, value))
        self.consumed_without_invocation(self.invoke())
    return test


for field, value in (('generation', 2), ('order_id', 9), ('client_id', 3), ('con_id', 18),
        ('side', 'SELL'), ('quantity', D(11)), ('order_type', 'LMT'), ('tif', 'GTC'),
        ('order_ref', 'substitute'), ('arrival_marker', 999), ('callback_watermark', 999),
        ('order_id_floor', 99), ('allocator_exhausted', True)):
    setattr(ProvenanceTests, 'test_reservation_changed_'+field, changed_reservation(field, value))


class ArrivalTests(ReservationFixture):
    def test_prior_lower_unrelated_history_remains_safe(self):
        self.prepare(lambda owner, res: None)
        self.open(order_id=3, order_values={'orderRef': 'unrelated'})
        seq = self.write._callback_sequence
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)
        self.assertEqual(self.reservation.callback_watermark, seq)
        self.assertEqual(self.coordinator._pending_order.commit_sequence, seq)

    def test_arrival_marker_is_lifetime_monotonic_across_generation(self):
        self.build()
        marker = self.write._arrival_sequence
        self.write._begin_generation()
        self.assertGreaterEqual(self.write._arrival_sequence, marker)
        self.paper_api.clients[-1].wrapper.nextValidId(2)
        self.assertGreater(self.write._arrival_sequence, marker)

    def test_stale_generation_does_not_inspect_dangerous_properties(self):
        class Dangerous:
            def __getattribute__(self, name): raise AssertionError('stale property read')
        def action(owner, res):
            before = self.write._arrival_sequence
            self.write._callback(0, 'open', (res.order_id, Dangerous(), Dangerous(), Dangerous()))
            self.write._callback(0, 'execution', (Dangerous(), Dangerous()))
            self.write._callback(0, 'error', 321)
            self.write._callback(0, 'closed')
            self.assertEqual(self.write._arrival_sequence, before)
        self.prepare(action)
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)

    def test_existing_lifetime_latch_blocks_reservation(self):
        self.build(); self.write._latch_reconciliation()
        self.assertIs(self.invoke().state, State.OUTCOME_UNKNOWN)
        self.assertFalse(self.write_calls())
        self.assertEqual(self.write._lifetime_order_floor, 10)

    def test_generation_change_after_reservation_blocks_and_preserves_floor(self):
        self.prepare(lambda owner, res: self.write._begin_generation())
        self.consumed_without_invocation(self.invoke())
        self.paper_api.clients[-1].wrapper.nextValidId(1)
        self.assertGreaterEqual(self.write._lifetime_order_floor, 11)

    def test_arrival_during_allocator_cannot_be_hidden_in_new_watermark(self):
        self.build()
        original = self.write._allocate_order_id
        def allocate(generation):
            value = original(generation)
            self.open(order_id=value+100, order_values={'orderRef': 'unrelated'})
            return value
        self.write._allocate_order_id = allocate
        self.consumed_without_invocation(self.invoke())
        self.assertGreaterEqual(self.write._lifetime_order_floor, 111)

    def test_callback_copy_mutation_does_not_change_retained_identity(self):
        def action(owner, res):
            contract, order = self.open(order_id=res.order_id)
            contract.conId = 999; order.orderRef = 'mutated'
        self.prepare(action)
        self.consumed_without_invocation(self.invoke())
        event = self.write._callback_evidence[-1]
        self.assertEqual(event.con_id, 17)
        self.assertEqual(event.order_ref, self.order.client_order_id)


def arrival_blocks(kind, final=False):
    def test(self):
        (self.validate if final else self.prepare)(lambda owner, res: self.arrival(kind, res))
        self.consumed_without_invocation(self.invoke())
    return test


for kind in ('open', 'open_foreign_ref', 'open_higher', 'open_lower', 'status', 'execution',
             'error', 'closed', 'next', 'malformed'):
    setattr(ArrivalTests, 'test_arrival_after_reservation_'+kind, arrival_blocks(kind))
for kind in ('open', 'error', 'closed'):
    setattr(ArrivalTests, 'test_arrival_immediately_before_final_proof_'+kind, arrival_blocks(kind, True))


class ThreadedArrivalTests(ReservationFixture):
    def blocked_bookkeeping(self, kind):
        arrived, done = Event(), Event()
        dispatch_thread = get_ident()
        pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(pool.shutdown)
        futures = []
        def action(owner, res):
            before = self.write._arrival_sequence
            errors_before = self.write._error_sequence
            real = self.write._arrival_lock
            class Announce:
                def __enter__(lock): real.acquire(); return lock
                def __exit__(lock, *args):
                    real.release()
                    if get_ident() != dispatch_thread: arrived.set()
                def _is_owned(lock): return real._is_owned()
            self.write._arrival_lock = Announce()
            def callback():
                try: self.arrival(kind, res)
                finally: done.set()
            futures.append(pool.submit(callback))
            self.assertTrue(arrived.wait(5), 'Callback did not secure arrival')
            self.assertFalse(done.is_set(), 'Bookkeeping must still be blocked')
            self.assertGreater(self.write._arrival_sequence, before)
            if kind == 'error': self.assertEqual(self.write._error_sequence, errors_before)
            if kind == 'closed': self.assertFalse(self.write._closed)
        self.prepare(action)
        self.consumed_without_invocation(self.invoke())
        futures[0].result(timeout=5)
        self.assertTrue(done.is_set())
        with self.outer(): pass  # Locks are still usable.

    def test_concurrent_dispatch_attempts_cannot_share_reservation(self):
        self.build()
        orders = (self.order, request('concurrent-other'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.invoke, o, permission(o)) for o in orders]
            results = [f.result(timeout=5) for f in futures]
        self.assertEqual(sum(r.state is State.DISPATCHED_PENDING_CONFIRMATION for r in results), 1)
        self.assertEqual(len(self.write_calls()), 1)
        self.assertEqual(self.write._lifetime_order_floor, 11)

    def test_repeated_contention_cannot_bypass_reserved_id_collision(self):
        for _ in range(8):
            self.blocked_bookkeeping('open')


for kind in ('open', 'open_foreign_ref', 'status', 'execution', 'error', 'closed', 'next', 'malformed'):
    def test(self, kind=kind): self.blocked_bookkeeping(kind)
    setattr(ThreadedArrivalTests, 'test_bookkeeping_waits_but_arrival_blocks_'+kind, test)


class CommitmentTests(ReservationFixture):
    def test_sdk_mutation_during_final_validation_cannot_substitute_intent(self):
        self.validate(lambda owner, res: setattr(self.sdk_order, 'orderRef', 'substitute'))
        original = self.write._build_order
        def build(spec):
            contract, self.sdk_order = original(spec)
            return contract, self.sdk_order
        self.write._build_order = build
        self.consumed_without_invocation(self.invoke())

    def test_after_commit_error_secures_uncertainty_without_second_invocation(self):
        self.build()
        real = self.write._arrival_lock
        fired = []
        class AfterCommit:
            def __enter__(lock): real.acquire(); return lock
            def __exit__(lock, *args):
                real.release()
                if self.write._commitment_consumed and not fired:
                    fired.append(True)
                    self.client.wrapper.error(-1, 321, 'discarded text')
            def _is_owned(lock): return real._is_owned()
        self.write._arrival_lock = AfterCommit()
        outcome = self.invoke()
        self.assertIs(outcome.state, State.OUTCOME_UNKNOWN)
        self.assertEqual(len(self.write_calls()), 1)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertTrue(self.coordinator._reconciliation_required)
        self.assertTrue(self.broker.reconciliation_required)
        self.assertIs(self.invoke().state, State.OUTCOME_UNKNOWN)
        self.assertEqual(len(self.write_calls()), 1)

    def test_callback_during_sdk_invocation_keeps_commit_watermark(self):
        self.build()
        original = self.client.placeOrder
        before = self.write._callback_sequence
        def invoke(order_id, contract, order):
            self.open(order_id=order_id)
            original(order_id, contract, order)
        self.client.placeOrder = invoke
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)
        self.assertEqual(self.coordinator._pending_order.commit_sequence, before)
        self.assertGreater(self.write._callback_sequence, before)
        self.assertTrue(self.sync().broker_observed)
        self.assertTrue(self.coordinator._broker_state_reconciliation_required)

    def test_maximum_id_reservation_exhausts_permanently(self):
        self.prepare(lambda owner, res: None)
        self.write._boundary = self.write._lifetime_order_floor = paper.MAX_ORDER_ID
        self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)
        self.assertEqual(self.reservation.order_id, paper.MAX_ORDER_ID)
        self.assertTrue(self.reservation.allocator_exhausted)
        self.write._begin_generation()
        self.paper_api.clients[-1].wrapper.nextValidId(1)
        self.assertTrue(self.write._lifetime_order_ids_exhausted)
        with self.assertRaises(ReadOnlyError): self.write._allocate_order_id(self.write._generation)

    def test_reservation_validation_requires_arrival_and_dispatch_locks(self):
        self.prepare(lambda owner, res: None)
        self.invoke()
        with self.assertRaises(ReadOnlyError):
            self.write._validate_reservation(self.coordinator, self.reservation)


def interrupted(seam, exc_type):
    def test(self):
        interruption = exc_type(37)
        if seam == 'prepare':
            self.prepare(lambda owner, res: (_ for _ in ()).throw(interruption))
        elif seam == 'validator':
            self.validate(lambda owner, res: (_ for _ in ()).throw(interruption))
        else:
            self.build()
            if seam == 'sdk':
                self.client.placeOrder = lambda *args: (_ for _ in ()).throw(interruption)
            elif seam == 'reservation_constructor':
                self.enterContext(patch.object(paper, '_DispatchReservation', side_effect=interruption))
            else:
                original = self.write._consume_commitment
                def consume(c):
                    if seam == 'after_commit': original(c)
                    raise interruption
                self.write._consume_commitment = consume
        with self.assertRaises(exc_type) as caught: self.invoke()
        self.assertIs(caught.exception, interruption)
        if exc_type is SystemExit: self.assertEqual(caught.exception.code, 37)
        self.assertEqual(self.write._lifetime_order_floor, 11)
        self.assertIn(self.order.client_order_id, self.coordinator._attempts)
        self.assertIsNone(self.write._active_reservation)
        self.assertIsNone(self.coordinator._prepared_reservation)
        post = seam in ('after_commit', 'sdk')
        self.assertIs(self.write._write_invocation_started, post)
        self.assertIs(self.write._requires_reconciliation(), post)
        self.assertIs(self.coordinator._reconciliation_required, post)
        self.assertIs(self.broker.reconciliation_required, post)
        if post: self.assertIs(self.broker.paper_execution_status, AuthStatus.INVALIDATED)
        self.assertFalse(self.write_calls())
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(self.write.disconnect).result(timeout=5)
    return test


for seam in ('reservation_constructor', 'prepare', 'validator', 'before_commit', 'after_commit', 'sdk'):
    for exc in (KeyboardInterrupt, SystemExit, GeneratorExit):
        setattr(CommitmentTests, 'test_interrupted_'+seam+'_'+exc.__name__, interrupted(seam, exc))


def sdk_changed(field, value):
    def test(self):
        original = Coordinator._prepare_commit
        def prepare(owner, res):
            original(owner, res)
            setattr(self.sdk_order if field != 'conId' else self.sdk_contract, field, value)
        with patch.object(Coordinator, '_prepare_commit', prepare): self.build()
        build = self.write._build_order
        def constructed(spec):
            self.sdk_contract, self.sdk_order = build(spec)
            return self.sdk_contract, self.sdk_order
        self.write._build_order = constructed
        self.consumed_without_invocation(self.invoke())
    return test


for field, value in (('conId', 18), ('action', 'SELL'), ('totalQuantity', D(11)),
                     ('orderType', 'LMT'), ('tif', 'GTC'), ('orderRef', 'substitute')):
    setattr(CommitmentTests, 'test_sdk_intent_changed_'+field, sdk_changed(field, value))


def publication_interruption(model, exc_type):
    def test(self):
        self.build()
        interruption = exc_type(37)
        with patch.object(dispatch, model, side_effect=interruption):
            with self.assertRaises(exc_type) as caught: self.invoke()
        self.assertIs(caught.exception, interruption)
        self.assertEqual(len(self.write_calls()), 1)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertTrue(self.coordinator._reconciliation_required)
        self.assertTrue(self.broker.reconciliation_required)
        self.assertIs(self.broker.paper_execution_status, AuthStatus.INVALIDATED)
        with self.outer(): pass
    return test


for model in ('_PendingOrder', '_BridgeResult'):
    for exc in (KeyboardInterrupt, SystemExit, GeneratorExit):
        setattr(CommitmentTests, 'test_publication_interrupted_'+model+'_'+exc.__name__,
                publication_interruption(model, exc))


class BoundaryTests(ReservationFixture):
    def test_no_journal_storage_or_recovery_import(self):
        root = Path(__file__).resolve().parents[1] / 'tradingbot_broker'
        for name in ('paper_dispatch.py', 'ibkr_paper_transport.py'):
            tree = ast.parse((root / name).read_text())
            imports = [n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
            self.assertFalse(any('journal' in n or 'sqlite' in n or 'recovery' in n for n in imports))

    def test_account_mode_unknown_public_snapshot_none(self):
        self.build()
        self.assertIs(self.write.account_mode, AccountMode.UNKNOWN)
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.broker.account_summary().mode)

    def test_live_order_still_denied(self):
        self.build()
        live = replace(self.order, mode=TradingMode.LIVE)
        self.assert_denied(self.invoke(live, permission(live)))

    def test_public_broker_still_refuses_order_write(self):
        self.build()
        with self.assertRaises(BrokerOperationError):
            self.broker.place_order(self.order, self.clock.now, self.permission)
        self.assertFalse(self.write_calls())

    def test_no_public_reservation_or_commitment_route(self):
        self.build()
        for owner in (self.write, self.coordinator, self.broker):
            self.assertFalse(any('reservation' in n or 'commitment' in n
                                 for n in dir(owner) if not n.startswith('_')))

    def test_no_sqlite_or_journal_side_effect(self):
        self.build()
        from tradingbot_broker.paper_execution_journal_sqlite import _SQLiteJournalStore
        with patch('sqlite3.connect', side_effect=AssertionError('SQLite forbidden')), \
                patch.object(_SQLiteJournalStore, '_append', side_effect=AssertionError('Journal forbidden')):
            self.assertIs(self.invoke().state, State.DISPATCHED_PENDING_CONFIRMATION)
        self.assertEqual(len(self.write_calls()), 1)

    def test_privacy_no_sdk_or_control_objects_in_reservation(self):
        self.prepare(lambda owner, res: None)
        self.invoke()
        allowed = (int, str, Decimal, bool)
        for field in fields(self.reservation):
            self.assertIn(type(getattr(self.reservation, field.name)), allowed)
        self.assertNotIn(self.order.client_order_id, repr(self.reservation))
