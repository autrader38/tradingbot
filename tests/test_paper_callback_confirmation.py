"""Offline callback attribution: anonymous SDK objects, no broker connection."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from threading import Barrier, Event
from types import SimpleNamespace as NS
from unittest.mock import patch
import unittest

from tradingbot_broker import ibkr_paper_transport as paper
from tradingbot_broker.paper_dispatch import (_CallbackState as CS,
    _BridgeState as BS, _BridgeReason as BR, _PaperOrderDispatchCoordinator)
from tradingbot_broker.paper_execution import PaperExecutionAuthorizationStatus as AS
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests.test_paper_dispatch import BridgeFixture
from tests.test_ibkr_paper_transport import sdk
from tests.broker_fixtures import request, permission


D = Decimal


class CallbackFixture(BridgeFixture):
    def pending(self, encoding='proto'):
        self.build(paper_api=sdk(encoding=encoding))
        result = self.invoke()
        self.assertIs(result.state, BS.DISPATCHED_PENDING_CONFIRMATION)
        self.pending_id = result.order_id
        self.before_account = self.broker.account_summary()
        return result

    def open(self, *, client=None, order_id=None, contract_values=None,
             order_values=None, status='Submitted'):
        contract = NS(conId=17, secType='STK', exchange='SMART', symbol='DEMO', currency='USD')
        order = NS(clientId=2, orderRef=self.order.client_order_id,
                   action='BUY', orderType='MKT', totalQuantity=D(10), tif='DAY')
        for name, value in (contract_values or {}).items(): setattr(contract, name, value)
        for name, value in (order_values or {}).items(): setattr(order, name, value)
        (client or self.client).wrapper.openOrder(self.pending_id if order_id is None else order_id,
                                                 contract, order, NS(status=status))
        return contract, order

    def status(self, *, client=None, order_id=None, status='Submitted', filled=D(0),
               remaining=D(10), average=0.0, last=0.0, client_id=2, parent=0):
        (client or self.client).wrapper.orderStatus(self.pending_id if order_id is None else order_id,
            status, filled, remaining, average, 2**50, parent, last, client_id,
            'private held text', 0.0)

    def execution(self, *, client=None, order_id=None, values=None, contract_values=None):
        contract = NS(conId=17, secType='STK', exchange='SMART')
        execution = NS(orderId=self.pending_id if order_id is None else order_id,
            clientId=2, orderRef=self.order.client_order_id, execId='execution-1', side='BOT',
            shares=D(4), price=1.25, acctNumber='private-account-text', permId=2**50)
        for name, value in (values or {}).items(): setattr(execution, name, value)
        for name, value in (contract_values or {}).items(): setattr(contract, name, value)
        (client or self.client).wrapper.execDetails(0, contract, execution)
        return execution

    def sync(self): return self.coordinator._sync_broker_callbacks()

    def assert_blocked(self, *, reconcile=False):
        before = len(self.write_calls())
        other = request('next-entry')
        result = self.invoke(other, permission(other))
        self.assertIs(result.state, BS.OUTCOME_UNKNOWN if reconcile else BS.DENIED)
        self.assertEqual(len(self.write_calls()), before)
        self.assertFalse(hasattr(result, 'accepted'))

    def assert_reconciliation(self, result):
        self.assertIs(result.state, CS.RECONCILIATION_REQUIRED)
        self.assertTrue(result.reconciliation_required)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertTrue(self.broker.reconciliation_required)
        self.assertIs(self.broker.paper_execution_status, AS.INVALIDATED)
        self.assert_blocked(reconcile=True)


class IdentityTests(CallbackFixture):
    def test_exact_open_order_is_observation_and_keeps_entry_barrier(self):
        self.pending(); self.open(); result = self.sync()
        self.assertIs(result.state, CS.BROKER_OBSERVED)
        self.assertTrue(result.broker_observed)
        self.assertTrue(result.broker_state_reconciliation_required)
        self.assertFalse(result.reconciliation_required)
        self.assertFalse(self.coordinator._pending_confirmation)
        self.assertIs(self.broker.paper_execution_status, AS.ARMED)
        self.assertEqual(self.broker.account_summary(), self.before_account)
        self.assert_blocked()

    def test_no_callback_no_confirmation(self):
        self.pending(); result = self.sync()
        self.assertIs(result.state, CS.NO_CHANGE)
        self.assertFalse(result.broker_observed)
        self.assertTrue(self.coordinator._pending_confirmation)

    def test_matching_reference_wrong_numeric_id_is_unrelated(self):
        self.pending(); self.open(order_id=100); result = self.sync()
        self.assertFalse(result.broker_observed)
        self.assertFalse(result.reconciliation_required)
        self.assertGreaterEqual(self.write._lifetime_order_floor, 101)

    def test_missing_reference_does_not_confirm(self):
        self.pending(); self.open(order_values={'orderRef': None})
        self.assertFalse(self.sync().broker_observed)
        self.assertTrue(self.coordinator._pending_confirmation)

    def test_empty_reference_does_not_confirm(self):
        self.pending(); self.open(order_values={'orderRef': ''})
        self.assertFalse(self.sync().broker_observed)
        self.assertTrue(self.coordinator._pending_confirmation)

    def test_missing_optional_callback_fields_are_not_fabricated(self):
        self.pending()
        self.open(order_values={'clientId': None},
                  contract_values={'symbol': '', 'currency': '', 'exchange': ''})
        self.assertTrue(self.sync().broker_observed)

    def test_pending_record_is_independent_and_immutable(self):
        self.pending(); pending = self.coordinator._pending_order
        self.assertEqual(pending.client_order_id, self.order.client_order_id)
        self.assertEqual(pending.order_id, self.pending_id)
        with self.assertRaises(FrozenInstanceError): pending.quantity = D(20)
        object.__setattr__(self.order, 'quantity', D(20))
        self.open(); self.assertTrue(self.sync().broker_observed)
        self.assertEqual(pending.quantity, D(10))

    def test_sdk_mutation_of_caller_order_cannot_change_expected_identity(self):
        self.build()
        original = self.client.placeOrder
        def invoke(order_id, contract, order):
            object.__setattr__(self.order, 'quantity', D(20))
            original(order_id, contract, order)
        self.client.placeOrder = invoke
        self.pending_id = self.invoke().order_id
        self.assertEqual(self.coordinator._pending_order.quantity, D(10))
        self.open(); self.assertTrue(self.sync().broker_observed)

    def test_callback_copy_does_not_retain_mutable_sdk_objects(self):
        self.pending(); contract, order = self.open()
        contract.conId = 99; order.orderRef = 'changed'; order.totalQuantity = D(30)
        self.assertTrue(self.sync().broker_observed)

    def test_duplicate_exact_open_is_idempotent(self):
        self.pending(); self.open(); self.sync()
        before = self.broker.audit
        for _ in range(200): self.open()
        result = self.sync()
        self.assertIs(result.state, CS.NO_CHANGE)
        self.assertFalse(result.reconciliation_required)
        self.assertEqual(self.broker.audit, before)
        self.assertEqual(len(self.write._callback_evidence), 1)

    def test_conflict_after_observation_cannot_choose_latest_truth(self):
        self.pending(); self.open(); self.sync()
        self.open(order_values={'totalQuantity': D(11)})
        self.assert_reconciliation(self.sync())

    def test_matching_callback_never_clears_existing_uncertainty(self):
        self.pending(); self.open(); self.write._latch_reconciliation()
        result = self.sync()
        self.assertFalse(result.broker_observed)
        self.assert_reconciliation(result)

    def test_fresh_permission_and_authorization_do_not_clear_observed_barrier(self):
        self.pending(); self.open(); self.sync()
        self.arm()
        self.assert_blocked()

    def test_status_reference_is_not_guessed(self):
        self.pending(); self.status(); result = self.sync()
        self.assertFalse(result.broker_observed)
        self.assertTrue(self.coordinator._pending_confirmation)


def sdk_reference_test(encoding):
    def test(self):
        self.build(paper_api=sdk(encoding=encoding)); captured = []
        original = self.client.placeOrder
        def invoke(order_id, contract, order):
            captured.append(order.orderRef)
            original(order_id, contract, order)
            self.pending_id = order_id
            self.open(order_values={'orderRef': order.orderRef})
        self.client.placeOrder = invoke
        self.invoke()
        self.assertEqual(captured, [self.order.client_order_id])
        self.assertTrue(self.sync().broker_observed)
        self.assertEqual(len(self.write_calls()), 1)
    return test


for _encoding in ('ascii', 'proto'):
    setattr(IdentityTests, 'test_exact_sdk_order_ref_' + _encoding, sdk_reference_test(_encoding))


def open_mismatch_test(area, field, value):
    def test(self):
        self.pending()
        self.open(**{area + '_values': {field: value}})
        result = self.sync()
        self.assertFalse(result.broker_observed)
        self.assert_reconciliation(result)
    return test


for _area, _field, _values in (
    ('order', 'clientId', (3, 0)), ('order', 'orderRef',
        ('different', 'CANDIDATE-1', ' candidate-1', 'candidate-1 ', 'candidate', 'candidate-1-extra')),
    ('contract', 'conId', (18,)), ('contract', 'secType', ('FUT',)),
    ('contract', 'exchange', ('NYSE',)), ('contract', 'symbol', ('OTHER',)),
    ('contract', 'currency', ('EUR',)), ('order', 'action', ('SELL',)),
    ('order', 'orderType', ('LMT',)), ('order', 'totalQuantity', (D(9), D(11))),
    ('order', 'tif', ('GTC',))):
    for _index, _value in enumerate(_values):
        setattr(IdentityTests, f'test_open_mismatch_{_field}_{_index}',
                open_mismatch_test(_area, _field, _value))


class StatusTests(CallbackFixture):
    def test_decimal_rounding_cannot_hide_impossible_remaining(self):
        self.pending(); self.open()
        self.status(filled=D('5.00000000000000000000000000001'), remaining=D(5))
        self.assert_reconciliation(self.sync())

    def test_status_before_open_is_secondary_then_strongly_correlated(self):
        self.pending(); self.status(status='PreSubmitted'); self.sync()
        self.assertFalse(self.coordinator._broker_observed)
        self.open(); self.assertIs(self.sync().state, CS.BROKER_OBSERVED)

    def test_duplicate_status_does_not_duplicate_economics(self):
        self.pending(); self.open(); self.status(); self.sync()
        for _ in range(200): self.status()
        self.assertIs(self.sync().state, CS.NO_CHANGE)
        self.assertFalse(self.write._requires_reconciliation())

    def test_partial_fill_requires_control_reconciliation(self):
        self.pending(); self.open(); self.status(filled=D(4), remaining=D(6))
        result = self.sync()
        self.assertIs(result.observed_state, CS.EXECUTION_OBSERVED)
        self.assertEqual(self.broker.account_summary(), self.before_account)
        self.assert_reconciliation(result)

    def test_filled_without_identity_is_not_confirmation(self):
        self.pending(); self.status(status='Filled', filled=D(10), remaining=D(0))
        result = self.sync()
        self.assertFalse(result.broker_observed)
        self.assert_reconciliation(result)

    def test_decreasing_fill_in_same_batch_conflicts(self):
        self.pending(); self.open(); self.status(filled=D(4), remaining=D(6))
        self.status(filled=D(3), remaining=D(7))
        self.assert_reconciliation(self.sync())

    def test_terminal_then_duplicate_old_submitted_conflicts(self):
        self.pending(); self.open(); self.status(); self.status(status='Cancelled')
        self.status()
        self.assert_reconciliation(self.sync())

    def test_status_before_open_and_terminal_transition(self):
        self.pending(); self.status(status='PreSubmitted'); self.open()
        self.status(status='Cancelled')
        self.assertIs(self.sync().state, CS.BROKER_TERMINAL)
        self.assert_blocked()


def status_test(status):
    def test(self):
        self.pending(); self.open(status='PendingSubmit')
        self.status(status=status, filled=D(10) if status == 'Filled' else D(0),
                    remaining=D(0) if status == 'Filled' else D(10))
        result = self.sync()
        if status == 'Filled':
            self.assert_reconciliation(result)
        else:
            self.assertIs(result.state, CS.BROKER_TERMINAL if status in
                ('Cancelled', 'ApiCancelled', 'Inactive', 'Expired') else CS.BROKER_OBSERVED)
            self.assert_blocked()
    return test


for _status in paper._STATUSES:
    setattr(StatusTests, 'test_known_status_' + _status, status_test(_status))


def status_conflict_test(kwargs):
    def test(self):
        self.pending(); self.open(); self.status(**kwargs)
        self.assert_reconciliation(self.sync())
    return test


for _index, _values in enumerate((
    {'status':'Working'}, {'status':'unknown'}, {'filled':D(11), 'remaining':D(0)},
    {'remaining':D(9)}, {'status':'Filled'}, {'filled':D(-1)}, {'remaining':D(-1)},
    {'filled':True}, {'remaining':1.0}, {'average':float('nan')}, {'last':float('inf')},
    {'client_id':True}, {'client_id':3}, {'parent':True}, {'parent':2**80})):
    setattr(StatusTests, f'test_status_conflict_{_index}', status_conflict_test(_values))


class ExecutionTests(CallbackFixture):
    def test_decimal_rounding_cannot_hide_cumulative_overfill(self):
        self.pending(); self.execution(values={'shares':D('5.00000000000000000000000000001')})
        self.execution(values={'execId':'execution-2', 'shares':D(5)})
        self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._execution_total,D('5.00000000000000000000000000001'))

    def test_exact_execution_confirms_observation_and_requires_reconciliation(self):
        self.pending(); self.execution(); result = self.sync()
        self.assertTrue(result.broker_observed)
        self.assertIs(result.observed_state, CS.EXECUTION_OBSERVED)
        self.assertEqual(self.coordinator._execution_total, D(4))
        self.assertEqual(self.broker.account_summary(), self.before_account)
        self.assertFalse(hasattr(result, 'fill'))
        self.assert_reconciliation(result)

    def test_duplicate_execution_id_does_not_double_count(self):
        self.pending(); self.execution(); self.execution(); result = self.sync()
        self.assertEqual(self.coordinator._execution_total, D(4))
        self.assert_reconciliation(result)

    def test_multiple_exact_executions_sum_without_a_fill_model(self):
        self.pending(); self.execution()
        self.execution(values={'execId':'execution-2', 'shares':D(6)})
        result = self.sync()
        self.assertEqual(self.coordinator._execution_total, D(10))
        self.assertFalse(hasattr(result, 'fill'))
        self.assert_reconciliation(result)

    def test_conflicting_execution_id_cannot_choose_latest(self):
        self.pending(); self.execution()
        self.execution(values={'shares':D(5)})
        self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._execution_total, D(4))

    def test_cumulative_execution_over_order_quantity_conflicts(self):
        self.pending(); self.execution(values={'shares':D(6)})
        self.execution(values={'execId':'execution-2','shares':D(6)})
        self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._execution_total, D(6))

    def test_missing_reference_without_prior_strong_identity_is_ambiguous(self):
        self.pending(); self.execution(values={'orderRef':None})
        result = self.sync(); self.assertFalse(result.broker_observed)
        self.assert_reconciliation(result)

    def test_missing_reference_uses_only_existing_strong_open_identity(self):
        self.pending(); self.open(); self.sync()
        self.execution(values={'orderRef':None})
        result = self.sync(); self.assertTrue(result.broker_observed)
        self.assertEqual(self.coordinator._execution_total, D(4))
        self.assert_reconciliation(result)

    def test_missing_execution_reference_and_client_is_insufficient(self):
        self.pending(); self.open(); self.sync()
        self.execution(values={'orderRef':None, 'clientId':None})
        self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._execution_total, D(0))

    def test_unrelated_execution_cannot_confirm_matching_ref(self):
        self.pending(); self.execution(order_id=99)
        result = self.sync()
        self.assertFalse(result.broker_observed)
        self.assertFalse(result.reconciliation_required)

    def test_execution_objects_are_copied_not_retained(self):
        self.pending(); execution = self.execution()
        execution.orderRef='different'; execution.shares=D(99)
        result = self.sync(); self.assertTrue(result.broker_observed)
        self.assertEqual(self.coordinator._execution_total, D(4))


def execution_conflict_test(field, value, contract=False):
    def test(self):
        self.pending(); self.execution(**{'contract_values' if contract else 'values': {field:value}})
        self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._execution_total, D(0))
    return test


for _field, _values, _contract in (
    ('conId', (18, 0, True), True), ('orderRef', ('wrong', 'CANDIDATE-1', 'candidate-1 '), False),
    ('clientId', (3, True, -1), False), ('side', ('SLD','BUY','unknown'), False),
    ('shares', (D(0), D(-1), D(11), D('NaN'), 4.0, True), False),
    ('price', (float('nan'),float('inf'), -1.0, True), False),
    ('execId', ('', 'bad space', 'x'*101), False)):
    for _index, _value in enumerate(_values):
        setattr(ExecutionTests, f'test_execution_conflict_{_field}_{_index}',
                execution_conflict_test(_field, _value, _contract))


def ordering_test(ordering):
    def test(self):
        self.pending()
        for kind in ordering:
            if kind == 'open': self.open()
            elif kind == 'status': self.status()
            elif kind == 'filled': self.status(status='Filled', filled=D(10), remaining=D(0))
            else: self.execution()
        result = self.sync()
        self.assertTrue(result.broker_observed)
        self.assertFalse(hasattr(result, 'accepted'))
        if 'execution' in ordering or 'filled' in ordering:
            self.assert_reconciliation(result)
        else:
            self.assertFalse(result.reconciliation_required)
            self.assert_blocked()
    return test


for _index, _ordering in enumerate((('open','status'), ('status','open'),
    ('execution','open'), ('open','execution'), ('filled','execution'),
    ('execution','filled'), ('open','open'), ('status','status','open'),
    ('execution','execution'))):
    setattr(ExecutionTests, f'test_callback_ordering_{_index}', ordering_test(_ordering))


class LedgerTests(CallbackFixture):
    def test_lifetime_sequence_never_resets(self):
        self.pending(); self.open(); self.status()
        before = self.write._callback_sequence
        self.write._begin_generation(); self.write._reset()
        self.assertEqual(self.write._callback_sequence, before)
        self.write._callback(self.write._generation, 'observed', 200)
        self.assertEqual(self.write._callback_sequence, before)

    def test_owner_only_immutable_batch_and_cursor(self):
        self.pending(); self.open()
        with self.assertRaises(ReadOnlyError): self.write._evidence_since(object(), 0)
        batch = self.write._evidence_since(self.coordinator, 0)
        self.assertIs(type(batch.events), tuple)
        with self.assertRaises(FrozenInstanceError): batch.through_sequence = 0
        with self.assertRaises(FrozenInstanceError): batch.events[0].order_id = 99
        self.assertEqual(self.write._evidence_since(self.coordinator, batch.through_sequence).events, ())
        self.sync()
        self.assertEqual(self.coordinator._callback_cursor, batch.through_sequence)
        self.assertEqual(self.write._evidence_since(self.coordinator, 0), batch)

    def test_invalid_cursors_cannot_modify_history(self):
        self.pending(); self.open()
        for cursor in (-1, True, 2**80, 1.0, None):
            with self.assertRaises(ReadOnlyError): self.write._evidence_since(self.coordinator, cursor)
        self.assertTrue(self.sync().broker_observed)

    def test_overflow_is_permanent_reconciliation_not_silent_eviction(self):
        self.pending()
        for order_id in range(100, 100+paper._EVIDENCE_CAPACITY+1):
            self.open(order_id=order_id)
        self.assertEqual(len(self.write._callback_evidence), paper._EVIDENCE_CAPACITY)
        self.assertTrue(self.write._requires_reconciliation())
        self.assert_reconciliation(self.sync())

    def test_sequence_distinguishes_every_accepted_duplicate(self):
        self.pending(); self.open(); a = self.write._callback_sequence
        self.open(); self.assertEqual(self.write._callback_sequence, a+1)
        self.assertEqual(len(self.write._callback_evidence), 1)

    def test_precommit_evidence_cannot_confirm_later_dispatch(self):
        self.build(); self.pending_id=10; self.open(); self.status()
        sequence = self.write._callback_sequence
        self.invoke()
        self.assertEqual(self.coordinator._pending_order.commit_sequence, sequence)
        self.assertFalse(self.sync().broker_observed)

    def test_callback_arriving_during_sdk_invocation_is_preserved(self):
        self.build(); original=self.client.placeOrder
        def invoked(order_id, contract, order):
            original(order_id, contract, order)
            self.pending_id=order_id; self.open()
        self.client.placeOrder=invoked
        self.invoke(); self.assertTrue(self.sync().broker_observed)


class GenerationTests(CallbackFixture):
    def test_loss_of_pending_generation_requires_reconciliation(self):
        self.pending(); self.write._begin_generation()
        self.paper_api.clients[-1].wrapper.nextValidId(10)
        self.write._synchronize_open_orders()
        self.assert_reconciliation(self.sync())
        self.assertGreaterEqual(self.write._lifetime_order_floor, 11)

    def test_explicit_disconnect_requires_reconciliation_of_pending(self):
        self.pending(); self.write.disconnect(); self.assert_reconciliation(self.sync())

    def test_matching_new_generation_callback_cannot_confirm_old_pending(self):
        self.pending(); self.write._begin_generation()
        self.open(client=self.paper_api.clients[-1])
        result=self.sync(); self.assertFalse(result.broker_observed)
        self.assert_reconciliation(result)

    def test_old_callbacks_do_not_read_raw_objects_or_restore_readiness(self):
        self.pending(); old=self.client; self.write._begin_generation()
        before=self.write._callback_sequence
        old.wrapper.openOrder(10, object(), object(), object())
        old.wrapper.execDetails(0, object(), object())
        old.wrapper.nextValidId(10); old.wrapper.openOrderEnd()
        self.assertFalse(self.write._initialized)
        self.assertEqual(self.write._sync, 'NOT_REQUESTED')
        self.assertEqual(self.write._callback_sequence, before)

    def test_reconciliation_never_cleared_by_matching_new_callbacks(self):
        self.pending(); self.client.wrapper.error(10,550,'private broker text')
        self.sync(); self.write._begin_generation()
        self.open(client=self.paper_api.clients[-1])
        self.assert_reconciliation(self.sync())

    def test_unavailable_current_sdk_inspection_fails_closed(self):
        self.pending(); self.client.isConnected=lambda: (_ for _ in ()).throw(RuntimeError('private SDK'))
        self.assert_reconciliation(self.sync())


class ConcurrencyPrivacyTests(CallbackFixture):
    def test_generation_reset_cannot_drop_accepted_observed_lifetime_floor(self):
        self.pending(); recorded=Event(); actual=self.write._arrival_lock; write=self.write
        class Notify:
            def __enter__(self): actual.acquire(); return self
            def __exit__(self,*args):
                accepted=write._callback_sequence>0
                actual.release()
                if accepted: recorded.set()
        with patch.object(write,'_arrival_lock',Notify()), ThreadPoolExecutor(max_workers=1) as pool:
            with write._lock:
                callback=pool.submit(self.open,order_id=100)
                self.assertTrue(recorded.wait(5))
                self.assertGreaterEqual(write._lifetime_order_floor,101)
                # Reset wins before the old callback acquires dispatch bookkeeping.
                write._begin_generation()
            callback.result(5)
        self.assertGreaterEqual(write._lifetime_order_floor,101)
        self.assertFalse(write._initialized)
        self.assertEqual(write._sync,'NOT_REQUESTED')
        self.assert_reconciliation(self.sync())

    def test_accepted_max_exhausts_before_deferred_generation_bookkeeping(self):
        self.pending(); recorded=Event(); actual=self.write._arrival_lock; write=self.write
        class Notify:
            def __enter__(self): actual.acquire(); return self
            def __exit__(self,*args):
                exhausted=write._lifetime_order_ids_exhausted
                actual.release()
                if exhausted: recorded.set()
        with patch.object(write,'_arrival_lock',Notify()), ThreadPoolExecutor(max_workers=1) as pool:
            with write._lock:
                callback=pool.submit(self.open,order_id=paper.MAX_ORDER_ID)
                self.assertTrue(recorded.wait(5))
                write._begin_generation()
            callback.result(5)
        self.assertTrue(write._lifetime_order_ids_exhausted)
        self.assertTrue(write._exhausted)
        with self.assertRaises(ReadOnlyError): write._allocate_order_id(write._generation)

    def test_deferred_error_latches_before_any_later_coordinator_attempt(self):
        self.pending(); announced=Event(); actual=self.write._arrival_lock
        write=self.write
        class Notify:
            def __enter__(self): actual.acquire(); return self
            def __exit__(self,*args):
                latched=write._reconciliation_required
                actual.release()
                if latched: announced.set()
        with patch.object(self.write,'_arrival_lock',Notify()), ThreadPoolExecutor(max_workers=1) as pool:
            with self.write._lock:
                callback=pool.submit(self.client.wrapper.error,10,550,'private callback text')
                self.assertTrue(announced.wait(5))
                self.assertEqual(self.write._errors,[])
                self.assertTrue(self.write._requires_reconciliation())
            callback.result(5)
        self.assert_reconciliation(self.sync())

    def test_audit_failure_cannot_undo_execution_barrier(self):
        self.pending(); self.execution()
        with patch.object(self.broker,'_authorization_audit',side_effect=RuntimeError('private audit')):
            result=self.sync()
        self.assert_reconciliation(result)
        self.assertNotIn('private audit',repr(result))

    def test_reconstruction_cannot_clear_confirmed_broker_state_barrier(self):
        self.pending(); self.open(); self.sync()
        with self.assertRaises(ReadOnlyError):
            _PaperOrderDispatchCoordinator(self.broker,self.write,self.contracts)
        self.assert_blocked()

    def test_offline_lifecycle_cannot_touch_network_or_dns(self):
        with patch('socket.socket.connect', side_effect=AssertionError('NETWORK_FORBIDDEN')), \
             patch('socket.create_connection', side_effect=AssertionError('NETWORK_FORBIDDEN')), \
             patch('socket.getaddrinfo', side_effect=AssertionError('DNS_FORBIDDEN')):
            self.pending(); self.open(); self.assertTrue(self.sync().broker_observed)

    def test_callback_cannot_arm_disarmed_control_plane(self):
        self.build(arm=False); self.pending_id=10; self.open(); self.status()
        result=self.sync()
        self.assertFalse(result.broker_observed)
        self.assertIs(self.broker.paper_execution_status,AS.DISARMED)
        self.assertFalse(self.write_calls())

    def test_unknown_sdk_callback_status_is_not_retained_as_raw_text(self):
        self.pending(); self.open(status='private-unqualified-text')
        batch=self.write._evidence_since(self.coordinator,0)
        self.assertEqual(batch.events[0].status,'UNKNOWN')
        self.assertNotIn('private-unqualified-text',repr(self.write.__dict__))
        self.assert_reconciliation(self.sync())

    def test_callback_does_not_acquire_coordinator_or_broker_locks(self):
        self.pending()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.coordinator._lock, self.broker._lock:
                pool.submit(self.open).result(timeout=5)
        self.assertTrue(self.sync().broker_observed)

    def test_callback_evidence_announced_before_dispatch_bookkeeping(self):
        self.pending(); copied=Event(); original=paper._copy_callback
        def copying(*args):
            value=original(*args); copied.set(); return value
        with patch.object(paper, '_copy_callback', copying), ThreadPoolExecutor(max_workers=1) as pool:
            with self.write._lock:
                future=pool.submit(self.open)
                self.assertTrue(copied.wait(5))
                # Arrival lock acquisition completes the copy before dispatch can
                # process allocator bookkeeping. No reverse lock nesting.
                with self.write._arrival_lock:
                    self.assertEqual(len(self.write._callback_evidence),1)
            future.result(timeout=5)
        self.assertTrue(self.sync().broker_observed)

    def test_concurrent_sync_is_idempotent(self):
        self.pending(); self.open(); gate=Barrier(3)
        def sync(): gate.wait(5); return self.sync()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(sync); b=pool.submit(sync); gate.wait(5)
            results=(a.result(5), b.result(5))
        self.assertEqual({r.state for r in results}, {CS.BROKER_OBSERVED,CS.NO_CHANGE})
        self.assertEqual(len(self.write_calls()),1)

    def test_delayed_error_outranks_matching_callback_and_duplicate_entry(self):
        self.pending(); self.open(); self.client.wrapper.error(10,550,'private errorString')
        result=self.invoke()
        self.assertIs(result.state, BS.OUTCOME_UNKNOWN)
        self.assertFalse(self.coordinator._broker_observed)
        self.assertTrue(self.broker.reconciliation_required)

    def test_duplicate_submission_races_with_callback_sync_without_second_write(self):
        self.pending(); self.open(); gate=Barrier(3)
        def sync(): gate.wait(5); return self.sync()
        def duplicate(): gate.wait(5); return self.invoke()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(sync); b=pool.submit(duplicate); gate.wait(5)
            a.result(5); b.result(5)
        self.assertEqual(len(self.write_calls()),1)
        self.assertTrue(self.coordinator._broker_state_reconciliation_required)

    def test_disconnect_races_with_sync_without_deadlock(self):
        self.pending(); self.open(); gate=Barrier(3)
        def sync(): gate.wait(5); return self.sync()
        def disconnect(): gate.wait(5); self.write.disconnect()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(sync); b=pool.submit(disconnect); gate.wait(5)
            a.result(5); b.result(5)
        self.assert_reconciliation(self.sync())

    def test_callback_privacy_account_whyheld_sdk_objects_absent(self):
        self.pending(); self.open(); self.status(); self.execution()
        result=self.sync(); batch=self.write._evidence_since(self.coordinator,0)
        surfaces=repr((result,batch,self.coordinator._pending_order,self.broker.audit))
        for text in ('private-account-text','private held text','acctNumber',
                     self.capability.capability_id,'SimpleNamespace','orderRef'):
            self.assertNotIn(text,surfaces)
        for event in batch.events:
            for name in ('account','acctNumber','whyHeld','perm_id','contract','order','execution'):
                self.assertFalse(hasattr(event,name))
        self.assertTrue(result.reconciliation_required)

    def test_callbacks_cannot_attest_paper_or_grant_authorization(self):
        self.pending(); self.open(); self.sync()
        self.assertIs(self.broker.account_mode,AccountMode.UNKNOWN)
        self.assertIs(self.write.account_mode,AccountMode.UNKNOWN)
        self.assertIsNone(self.broker.account_summary().mode)
        self.assertFalse(self.broker.controls.trading_enabled)
        for name in ('connect','place_order','cancel_order','global_cancel','reconcile','clear'):
            self.assertFalse(hasattr(self.coordinator,name))
            self.assertFalse(hasattr(self.write,name))


def old_callback_test(kind):
    def test(self):
        self.pending(); old=self.client; self.write._begin_generation()
        before=self.write._callback_sequence
        if kind=='open': self.open(client=old)
        elif kind=='status': self.status(client=old)
        elif kind=='execution': self.execution(client=old)
        else: old.wrapper.error(10,550,'private-old-error')
        self.assertEqual(self.write._callback_sequence,before)
        self.assertFalse(self.write._requires_reconciliation())
        result=self.sync()
        self.assertFalse(result.broker_observed)
        # Losing the pending generation, not the stale callback, requires recovery.
        self.assert_reconciliation(result)
    return test


for _kind in ('open','status','execution','error'):
    setattr(GenerationTests,'test_old_generation_'+_kind,old_callback_test(_kind))


def malformed_open_test(area, field, value):
    def test(self):
        self.pending(); self.open(**{area+'_values':{field:value}})
        self.assertTrue(self.write._requires_reconciliation())
        self.assert_reconciliation(self.sync())
    return test


for _area, _field, _values in (
    ('order','clientId',(True,-1,2**80,'2')), ('order','orderRef',('x'*101,True,42)),
    ('order','totalQuantity',(True,10.0,D('NaN'),D('Infinity'),D(-1),D(0))),
    ('contract','conId',(True,0,-1,'17',2**80)), ('contract','secType',(None,True,'x'*101)),
    ('order','action',(None,True)), ('order','orderType',(None,True)),
    ('order','tif',(None,True)), ('contract','symbol',('raw\ntext',))):
    for _index,_value in enumerate(_values):
        setattr(ConcurrencyPrivacyTests,f'test_malformed_open_{_field}_{_index}',
                malformed_open_test(_area,_field,_value))


def malformed_id_test(kind, value):
    def test(self):
        self.pending()
        if kind=='open': self.open(order_id=value)
        elif kind=='status': self.status(order_id=value)
        else: self.execution(order_id=value)
        self.assert_reconciliation(self.sync())
    return test


for _kind in ('open','status','execution'):
    for _index,_value in enumerate((True,-1,2**80,'10')):
        setattr(ConcurrencyPrivacyTests,f'test_malformed_id_{_kind}_{_index}',
                malformed_id_test(_kind,_value))


class FinalTerminalCorrections(CallbackFixture):
    pass


def terminal_before_identity_test(status, filled, remaining):
    def test(self):
        self.pending(); self.status(status=status, filled=filled, remaining=remaining)
        result = self.sync()
        self.assert_reconciliation(result)
        self.assertFalse(result.broker_observed)
        self.assertFalse(self.coordinator._strong_open_order_identity_established)
        self.assertEqual(self.broker.account_summary(), self.before_account)
        self.assertTrue(self.coordinator._pending_confirmation)
        self.assertFalse(hasattr(result, 'accepted'))
    return test


for _status in ('Cancelled', 'ApiCancelled', 'Inactive', 'Expired', 'Filled', 'partial'):
    setattr(FinalTerminalCorrections, 'test_terminal_before_identity_' + _status,
        terminal_before_identity_test('Submitted' if _status == 'partial' else _status,
            D(4) if _status == 'partial' else D(10) if _status == 'Filled' else D(0),
            D(6) if _status == 'partial' else D(0) if _status == 'Filled' else D(10)))


def working_before_identity_test(status):
    def test(self):
        self.pending(); self.status(status=status)
        result = self.sync()
        self.assertFalse(result.broker_observed)
        self.assertFalse(result.reconciliation_required)
        self.assertFalse(self.coordinator._strong_open_order_identity_established)
        self.assertTrue(self.coordinator._pending_confirmation)
        self.assertEqual(self.broker.account_summary(), self.before_account)
        self.assert_blocked()
    return test


for _status in ('PendingSubmit', 'PreSubmitted', 'Submitted'):
    setattr(FinalTerminalCorrections, 'test_secondary_only_' + _status,
            working_before_identity_test(_status))


class FinalProvenanceCorrections(CallbackFixture):
    def test_exact_execution_does_not_establish_open_order_provenance(self):
        self.pending(); self.execution()
        result = self.sync()
        self.assertTrue(result.broker_observed)
        self.assertFalse(self.coordinator._strong_open_order_identity_established)
        self.assertEqual(self.coordinator._execution_total, D(4))
        self.assert_reconciliation(result)

    def test_prior_execution_cannot_attribute_missing_reference(self):
        self.pending(); self.execution()
        self.execution(values={'execId': 'execution-2', 'orderRef': None})
        self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._execution_total, D(4))
        self.assertNotIn('execution-2', self.coordinator._executions)
        self.assertFalse(self.coordinator._strong_open_order_identity_established)
        self.assertEqual(self.coordinator._callback_cursor, 1)

    def test_strong_open_order_is_required_and_sufficient_for_fallback(self):
        self.pending(); self.open(); self.sync()
        self.assertTrue(self.coordinator._strong_open_order_identity_established)
        self.execution(values={'orderRef': None})
        self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._execution_total, D(4))

    def test_wrong_client_cannot_use_open_order_fallback(self):
        self.pending(); self.open(); self.sync()
        self.execution(values={'orderRef': None, 'clientId': 99})
        self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._execution_total, D(0))

    def test_missing_client_cannot_use_open_order_fallback(self):
        self.pending(); self.open(); self.sync()
        self.execution(values={'orderRef': None, 'clientId': None})
        self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._execution_total, D(0))

    def test_status_cannot_supply_execution_provenance(self):
        self.pending(); self.status()
        self.execution(values={'orderRef': None})
        self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._execution_total, D(0))
        self.assertFalse(self.coordinator._strong_open_order_identity_established)

    def test_execution_observed_enum_cannot_supply_provenance(self):
        self.pending()
        self.coordinator._observed_state = CS.EXECUTION_OBSERVED
        self.coordinator._broker_observed = True
        self.execution(values={'orderRef': None})
        self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._execution_total, D(0))
        self.assertFalse(self.coordinator._strong_open_order_identity_established)


class FinalCursorCorrections(CallbackFixture):
    def test_conflict_one_does_not_acknowledge_unprocessed_two(self):
        self.pending(); self.open(order_values={'orderRef': 'wrong'}); self.open()
        self.assertEqual(self.write._callback_sequence, 2)
        self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._callback_cursor, 0)
        self.assertFalse(self.coordinator._broker_observed)
        self.assertEqual(len(self.write._evidence_since(self.coordinator, 0).events), 2)

    def test_valid_one_conflict_two_never_acknowledges_three(self):
        self.pending(); self.open(); self.open(order_values={'orderRef': 'wrong'}); self.open()
        self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._callback_cursor, 1)
        self.assertEqual([e.sequence for e in self.write._evidence_since(self.coordinator, 1).events], [2, 3])

    def test_processing_exception_keeps_last_completed_sequence(self):
        self.pending(); self.open(); self.status()
        original = self.coordinator._process_callback
        def fail_second(event):
            if event.sequence == 2: raise RuntimeError('private processing text')
            original(event)
        with patch.object(self.coordinator, '_process_callback', side_effect=fail_second):
            self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._callback_cursor, 1)

    def test_repeated_sync_after_reconciliation_never_skips_later_events(self):
        self.pending(); self.open(order_values={'orderRef': 'wrong'}); self.open()
        first = self.sync(); second = self.sync()
        self.assertEqual(first.processed_sequence, second.processed_sequence)
        self.assertEqual(second.processed_sequence, 0)
        self.assertFalse(second.broker_observed)
        self.assert_reconciliation(second)

    def test_normal_events_process_one_two_three(self):
        self.pending(); self.status(status='PendingSubmit')
        self.status(status='PreSubmitted'); self.open()
        result = self.sync()
        self.assertEqual(result.processed_sequence, 3)
        self.assertTrue(result.broker_observed)
        self.assertFalse(result.reconciliation_required)

    def test_processed_duplicate_sequences_are_acknowledged_individually(self):
        self.pending(); self.open(); self.open(); self.open()
        batch = self.write._evidence_since(self.coordinator, 0)
        self.assertEqual([e.sequence for e in batch.events], [1, 2, 3])
        self.assertEqual(len(self.write._callback_evidence), 1)
        self.assertEqual(self.sync().processed_sequence, 3)

    def test_coalesced_duplicate_pages_do_not_acknowledge_fetch_upper_bound(self):
        self.pending()
        for _ in range(300): self.open()
        self.assertEqual(self.sync().processed_sequence, 128)
        self.assertEqual(self.sync().processed_sequence, 256)
        result = self.sync()
        self.assertEqual(result.processed_sequence, 300)
        self.assertFalse(result.reconciliation_required)
        self.assertEqual(len(self.write._callback_evidence), 1)
        self.assertEqual(len(self.write._callback_run_ends), 1)

    def test_sequence_gap_requires_reconciliation_without_cursor_jump(self):
        self.pending(); self.open(); self.status()
        # Adversarial history corruption: fetched seq 2 cannot stand in for seq 1.
        del self.write._callback_evidence[0]
        del self.write._callback_run_ends[0]
        self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._callback_cursor, 0)


class FinalCallbackInterruptionCorrections(CallbackFixture):
    def interrupt_callback(self, area, interruption, *, stale=False):
        accessed = []
        class Unsafe:
            def __repr__(self):
                raise AssertionError('raw callback repr must never be accessed')
        field = ('conId' if area.endswith('contract') else
                 'clientId' if area == 'open_order' else 'orderId')
        def extract(raw):
            accessed.append(field)
            raise interruption
        setattr(Unsafe, field, property(extract))
        client = self.client
        if stale: self.write._begin_generation()
        if area.startswith('open'):
            contract = Unsafe() if area == 'open_contract' else NS(conId=17, secType='STK')
            order = Unsafe() if area == 'open_order' else NS(clientId=2, orderRef='candidate-1')
            callback = lambda: client.wrapper.openOrder(self.pending_id, contract, order, NS(status='Submitted'))
        else:
            contract = Unsafe() if area == 'execution_contract' else NS(conId=17)
            execution = Unsafe() if area == 'execution_object' else NS(orderId=self.pending_id,
                clientId=2, orderRef=self.order.client_order_id, execId='execution-1')
            callback = lambda: client.wrapper.execDetails(0, contract, execution)
        return callback, accessed


def interrupted_callback_test(area, interruption_type, stale=False):
    def test(self):
        self.pending()
        interruption = interruption_type(37 if interruption_type is SystemExit else 'private marker')
        callback, accessed = self.interrupt_callback(area, interruption, stale=stale)
        sequence = self.write._callback_sequence
        if stale:
            callback()
            self.assertEqual(accessed, [])
            self.assertFalse(self.write._requires_reconciliation())
        else:
            with self.assertRaises(interruption_type) as caught: callback()
            self.assertIs(caught.exception, interruption)
            if interruption_type is SystemExit: self.assertEqual(caught.exception.code, 37)
            self.assertTrue(accessed)
            self.assertTrue(self.write._requires_reconciliation())
        self.assertEqual(self.write._callback_sequence, sequence)
        self.assertEqual(len(self.write._callback_evidence), 0)
        # A separate thread proves local locks were released after propagation.
        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertEqual(pool.submit(lambda: self.write._evidence_since(self.coordinator, 0)).result(2).events, ())
            pool.submit(self.client.wrapper.nextValidId, 20).result(2)
        result = self.sync()
        self.assertFalse(result.broker_observed)
        self.assert_reconciliation(result)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(self.write.disconnect).result(2)
        self.assertTrue(self.write._requires_reconciliation())
    return test


for _area in ('open_contract', 'open_order', 'execution_contract', 'execution_object'):
    for _interruption_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
        for _stale in (False, True):
            setattr(FinalCallbackInterruptionCorrections,
                'test_' + ('stale_' if _stale else '') + _area + '_' + _interruption_type.__name__,
                interrupted_callback_test(_area, _interruption_type, _stale))


if __name__ == '__main__': unittest.main()
