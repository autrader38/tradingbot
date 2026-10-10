"""One-shot provenance only; every SDK/socket/collection is an offline double."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from datetime import datetime
from decimal import Decimal
from threading import Barrier, Event
from types import SimpleNamespace as NS
from unittest.mock import patch
import inspect
import unittest

from tradingbot_broker import ibkr_readonly as read
from tradingbot_broker import paper_dispatch as dispatch
from tradingbot_broker.readonly_models import ReadOnlyError, AccountMode
from tradingbot_broker.paper_execution import PaperExecutionAuthorizationStatus as AS
from tests.readonly_fixtures import transport
from tests import test_ibkr_reconciliation_evidence as evidence
from tests import test_paper_callback_confirmation as callbacks
from tests import test_ibkr_paper_transport as paper_tests


def read_api():
    return evidence.PublicIntegrationTests().api()


class ReadFixture(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('real connection forbidden')))
        self.enterContext(patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden')))
        self.t, self.api, _ = transport(read_api())
        self.addCleanup(self.t.disconnect)

    def collect(self):
        self.t.disconnect()
        return self.t.connect()

    def bind_without_requests(self):
        self.t.disconnect()
        with self.t._condition:
            self.t._generation += 3
            self.t._reset()

    def error_code(self, code, function, *args):
        with self.assertRaises(ReadOnlyError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)


class RequestTests(ReadFixture):
    def test_exact_baseline_and_logical_issuance(self):
        baseline=(self.t._generation,self.t._reconciliation_start_marker,self.t._reconciliation_marker)
        request=self.t._prepare_reconciliation_read_request()
        self.assertEqual((request.request_id,request.baseline_generation,request.baseline_start_marker),(1,*baseline[:2]))
        self.assertEqual(request.issuance_marker,baseline[2]+1)
        self.assertIs(self.t._reconciliation_read_request,request)
        self.assertIsNone(self.t._reconciliation_read_binding)

    def test_no_caller_request_id_or_sdk_call(self):
        before=list(self.api.calls)
        self.assertEqual(tuple(inspect.signature(self.t._prepare_reconciliation_read_request).parameters),())
        with self.assertRaises(TypeError):self.t._prepare_reconciliation_read_request(17)
        self.t._prepare_reconciliation_read_request()
        self.assertEqual(self.api.calls,before)

    def test_equal_forgery_not_authority(self):
        request=self.t._prepare_reconciliation_read_request()
        forged=replace(request)
        self.assertEqual(forged,request)
        self.error_code('INVALID_RECONCILIATION_READ_REQUEST',self.t._consume_reconciliation_read_receipt,forged)
        self.assertIs(self.t._reconciliation_read_request,request)

    def test_second_unbound_request_denied(self):
        self.t._prepare_reconciliation_read_request()
        self.error_code('RECONCILIATION_READ_REQUEST_OUTSTANDING',self.t._prepare_reconciliation_read_request)

    def test_second_bound_request_denied(self):
        self.t._prepare_reconciliation_read_request();self.bind_without_requests()
        self.error_code('RECONCILIATION_READ_REQUEST_OUTSTANDING',self.t._prepare_reconciliation_read_request)

    def test_second_terminal_request_denied(self):
        self.t._prepare_reconciliation_read_request();self.collect()
        self.error_code('RECONCILIATION_READ_REQUEST_OUTSTANDING',self.t._prepare_reconciliation_read_request)

    def test_wrong_transport_rejected(self):
        request=self.t._prepare_reconciliation_read_request()
        other,_,_=transport(read_api());self.addCleanup(other.disconnect)
        other._prepare_reconciliation_read_request()
        self.error_code('INVALID_RECONCILIATION_READ_REQUEST',other._consume_reconciliation_read_receipt,request)

    def test_request_opaque_and_frozen(self):
        request=self.t._prepare_reconciliation_read_request()
        self.assertEqual(repr(request),'<PrivateReadReconciliationEvidence>')
        with self.assertRaises(FrozenInstanceError):request.request_id=2
        self.assertFalse(hasattr(request,'__dict__'))

    def test_cached_connect_does_not_bind(self):
        public=self.t.connect();private=self.t._reconciliation_snapshot()
        request=self.t._prepare_reconciliation_read_request();before=list(self.api.calls)
        self.assertIs(self.t.connect(),public)
        self.assertIs(self.t._reconciliation_snapshot(),private)
        self.assertEqual(self.api.calls,before)
        self.assertIsNone(self.t._reconciliation_read_binding)
        self.error_code('RECONCILIATION_READ_INCOMPLETE',self.t._consume_reconciliation_read_receipt,request)

    def test_disconnect_alone_does_not_bind(self):
        request=self.t._prepare_reconciliation_read_request()
        self.t.disconnect();self.t.disconnect()
        self.assertIs(self.t._reconciliation_read_request,request)
        self.assertIsNone(self.t._reconciliation_read_binding)
        self.error_code('RECONCILIATION_READ_INCOMPLETE',self.t._consume_reconciliation_read_receipt,request)

    def test_reset_without_new_generation_does_not_bind(self):
        request=self.t._prepare_reconciliation_read_request()
        with self.t._condition:self.t._reset()
        self.assertIsNone(self.t._reconciliation_read_binding)
        self.error_code('RECONCILIATION_READ_INCOMPLETE',self.t._consume_reconciliation_read_receipt,request)

    def test_equal_monotonic_samples_have_logical_order(self):
        with patch.object(read,'monotonic',return_value=100.0):
            self.t,self.api,_=transport(read_api());self.addCleanup(self.t.disconnect)
            request=self.t._prepare_reconciliation_read_request();self.collect()
            receipt=self.t._consume_reconciliation_read_receipt(request)
        self.assertEqual(receipt.issued_monotonic,receipt.started_monotonic)
        self.assertGreater(receipt.start_marker,receipt.issuance_marker)
        self.assertGreater(receipt.generation,receipt.baseline_generation)

    def test_generation_jump_not_plus_one(self):
        request=self.t._prepare_reconciliation_read_request()
        for _ in range(4):self.t.disconnect()
        self.t.connect();receipt=self.t._consume_reconciliation_read_receipt(request)
        self.assertGreater(receipt.generation,request.baseline_generation+1)

    def test_request_sequence_never_resets(self):
        first=self.t._prepare_reconciliation_read_request();self.collect()
        self.t._consume_reconciliation_read_receipt(first)
        self.t.disconnect();second=self.t._prepare_reconciliation_read_request()
        self.assertEqual(second.request_id,first.request_id+1)

    def test_clock_regression_fails_bound_collection(self):
        with patch.object(read,'monotonic',return_value=100.0):
            self.t,self.api,_=transport(read_api());self.addCleanup(self.t.disconnect)
            request=self.t._prepare_reconciliation_read_request()
        with patch.object(read,'monotonic',return_value=99.0):self.bind_without_requests()
        receipt=self.t._consume_reconciliation_read_receipt(request)
        self.assertEqual(receipt.outcome,'FAILED')
        self.assertIsNone(receipt.public_snapshot)

    def test_concurrent_issuance_one_owner(self):
        gate=Barrier(8)
        def issue(_):
            gate.wait()
            try:return self.t._prepare_reconciliation_read_request()
            except ReadOnlyError as error:return error.code
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(issue,range(8)))
        self.assertEqual(sum(type(r) is read._ReconciliationReadRequest for r in results),1)
        self.assertEqual(results.count('RECONCILIATION_READ_REQUEST_OUTSTANDING'),7)


class ReceiptTests(ReadFixture):
    def successful(self):
        request=self.t._prepare_reconciliation_read_request();public=self.collect()
        return request,public,self.t._reconciliation_snapshot()

    def test_exact_public_private_pair(self):
        request,public,private=self.successful()
        receipt=self.t._consume_reconciliation_read_receipt(request)
        self.assertIs(receipt.public_snapshot,public)
        self.assertIs(receipt.private_snapshot,private)
        self.assertEqual((receipt.generation,receipt.start_marker,receipt.completion_marker),
            (private.generation,private.start_marker,private.completion_marker))
        self.assertLessEqual(request.issued_monotonic,receipt.started_monotonic)
        self.assertLessEqual(receipt.started_monotonic,receipt.completed_monotonic)
        self.assertEqual(receipt.outcome,'COLLECTED')
        self.assertIsNone(public.account.mode)

    def test_exact_consumption_once(self):
        request,_,_=self.successful();receipt=self.t._consume_reconciliation_read_receipt(request)
        self.error_code('INVALID_RECONCILIATION_READ_REQUEST',self.t._consume_reconciliation_read_receipt,request)
        self.assertEqual(receipt.outcome,'COLLECTED')

    def test_terminal_equal_request_rejected(self):
        request,_,_=self.successful()
        self.error_code('INVALID_RECONCILIATION_READ_REQUEST',self.t._consume_reconciliation_read_receipt,replace(request))
        self.assertEqual(self.t._consume_reconciliation_read_receipt(request).outcome,'COLLECTED')

    def test_in_progress_request_not_retired(self):
        request=self.t._prepare_reconciliation_read_request();self.bind_without_requests()
        self.error_code('RECONCILIATION_READ_INCOMPLETE',self.t._consume_reconciliation_read_receipt,request)
        self.assertIs(self.t._reconciliation_read_request,request)
        self.assertIsNotNone(self.t._reconciliation_read_binding)

    def test_terminal_receipt_not_replaced_by_later_collection(self):
        request,public,private=self.successful();binding=self.t._reconciliation_read_binding
        later=self.collect()
        self.assertIsNot(later,public)
        self.assertEqual(self.t._reconciliation_read_binding,binding)
        receipt=self.t._consume_reconciliation_read_receipt(request)
        self.assertIs(receipt.public_snapshot,public);self.assertIs(receipt.private_snapshot,private)
        self.assertLess(receipt.generation,self.t._generation)

    def test_consumed_history_survives_reset(self):
        request,_,_=self.successful();receipt=self.t._consume_reconciliation_read_receipt(request)
        old=(receipt.generation,receipt.start_marker,receipt.private_snapshot)
        self.collect()
        self.assertEqual((receipt.generation,receipt.start_marker,receipt.private_snapshot),old)
        with self.assertRaises(FrozenInstanceError):receipt.outcome='FAILED'
        with self.assertRaises(FrozenInstanceError):receipt.private_snapshot.orders=()

    def test_first_bound_generation_loss_not_second_success(self):
        request=self.t._prepare_reconciliation_read_request();self.bind_without_requests()
        bound=self.t._reconciliation_read_binding;self.t.disconnect();self.t.connect()
        receipt=self.t._consume_reconciliation_read_receipt(request)
        self.assertEqual(receipt.outcome,'GENERATION_LOST')
        self.assertEqual((receipt.generation,receipt.start_marker),bound)
        self.assertIsNone(receipt.public_snapshot)

    def test_failed_capture_never_uses_prior_public_snapshot(self):
        prior=self.t.connect();request=self.t._prepare_reconciliation_read_request()
        self.bind_without_requests()
        with self.t._condition:self.t._reconciliation_fail('MALFORMED')
        receipt=self.t._consume_reconciliation_read_receipt(request)
        self.assertIsNone(receipt.public_snapshot);self.assertIsNotNone(prior)

    def test_cannot_publish_collected_receipt_without_publication(self):
        request=self.t._prepare_reconciliation_read_request();self.bind_without_requests()
        with self.t._condition:
            for name in read._RECONCILIATION_SOURCES:
                self.t._reconciliation_request(name,103 if name=='executions' else None)
                self.t._reconciliation_complete(name)
            with self.assertRaises(ReadOnlyError):self.t._reconciliation_finish('COLLECTED')
        self.assertEqual(self.t._reconciliation_failure,'FAILED')
        self.assertIsNone(self.t._reconciliation_read_receipt)
        self.error_code('RECONCILIATION_READ_INCOMPLETE',self.t._consume_reconciliation_read_receipt,request)

    def test_receipt_consumption_has_no_sdk_or_public_mutation(self):
        request,public,private=self.successful();before=list(self.api.calls)
        self.t._consume_reconciliation_read_receipt(request)
        self.assertEqual(self.api.calls,before)
        self.assertIs(self.t._snapshot,public);self.assertIs(self.t._reconciliation_snapshot(),private)

    def test_concurrent_consumption_exactly_once(self):
        request,_,_=self.successful();gate=Barrier(8)
        def consume(_):
            gate.wait()
            try:return self.t._consume_reconciliation_read_receipt(request)
            except ReadOnlyError as error:return error.code
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(consume,range(8)))
        self.assertEqual(sum(type(r) is read._ReconciliationReadReceipt for r in results),1)
        self.assertEqual(results.count('INVALID_RECONCILIATION_READ_REQUEST'),7)

    def test_unavailable_completed_source_preserved(self):
        self.api.Client.serverVersion=lambda client:100
        request,public,_=self.successful();receipt=self.t._consume_reconciliation_read_receipt(request)
        self.assertFalse(public.completed_orders_available)
        self.assertEqual(receipt.private_snapshot.collections[1].state,'UNAVAILABLE')

    def test_real_capture_timeout_receipt(self):
        request=self.t._prepare_reconciliation_read_request()
        self.t.config=replace(self.t.config,timeout_seconds=1)
        original=self.t._create_client
        def no_ready(generation):
            client=original(generation)
            self.api.clients[-1].wrapper.nextValidId=lambda order_id:None
            return client
        with patch.object(self.t,'_create_client',no_ready):
            self.error_code('IB_GATEWAY_TIMEOUT',self.collect)
        receipt=self.t._consume_reconciliation_read_receipt(request)
        self.assertEqual(receipt.outcome,'TIMED_OUT')
        self.assertIsNone(receipt.public_snapshot)

    def test_actual_malformed_read_callback_failure_receipt(self):
        request=self.t._prepare_reconciliation_read_request()
        def malformed(client):
            order=NS(**dict(evidence.ORDER,totalQuantity=Decimal('NaN')),account=evidence.ACCOUNT)
            client.wrapper.openOrder(10,NS(**evidence.CONTRACT),order,NS(status='Submitted'))
        with patch.object(self.api.Client,'reqAllOpenOrders',malformed):
            with self.assertRaises(ReadOnlyError):self.collect()
        receipt=self.t._consume_reconciliation_read_receipt(request)
        self.assertEqual(receipt.outcome,'MALFORMED')
        self.assertIsNone(receipt.public_snapshot)

    def test_cached_public_snapshot_cannot_prove_new_collection_pair(self):
        cached=self.t.connect();request=self.t._prepare_reconciliation_read_request()
        with self.t._condition:
            self.t._generation+=1;self.t._reset()
            self.assertIs(self.t._snapshot,cached)
            for name in read._RECONCILIATION_SOURCES:
                self.t._reconciliation_request(name,103 if name=='executions' else None)
                self.t._reconciliation_complete(name)
            with self.assertRaises(ReadOnlyError):
                self.t._reconciliation_finish('COLLECTED',public_snapshot=cached)
        self.assertIsNone(self.t._reconciliation_read_receipt)
        self.assertEqual(self.t._reconciliation_failure,'FAILED')
        self.error_code('RECONCILIATION_READ_INCOMPLETE',self.t._consume_reconciliation_read_receipt,request)

    def test_equal_public_snapshot_copy_not_published_object(self):
        request=self.t._prepare_reconciliation_read_request()
        original=self.t._reconciliation_receipt_for
        def substituted(snapshot,public):
            return original(snapshot,None if public is None else replace(public))
        with patch.object(self.t,'_reconciliation_receipt_for',substituted):
            with self.assertRaises(ReadOnlyError):self.collect()
        self.assertIsNone(self.t._reconciliation_read_receipt)
        self.assertEqual(self.t._reconciliation_failure,'FAILED')
        self.error_code('RECONCILIATION_READ_INCOMPLETE',self.t._consume_reconciliation_read_receipt,request)


def failed_receipt_test(outcome):
    def test(self):
        request=self.t._prepare_reconciliation_read_request();self.bind_without_requests()
        with self.t._condition:self.t._reconciliation_fail(outcome)
        receipt=self.t._consume_reconciliation_read_receipt(request)
        self.assertEqual(receipt.outcome,outcome)
        self.assertIsNone(receipt.public_snapshot)
        self.error_code('INVALID_RECONCILIATION_READ_REQUEST',self.t._consume_reconciliation_read_receipt,request)
        self.collect();self.assertEqual(receipt.outcome,outcome)
    return test
for _outcome in ('TIMED_OUT','MALFORMED','INTERRUPTED','GENERATION_LOST','FAILED','OVERFLOW'):
    setattr(ReceiptTests,'test_terminal_failure_'+_outcome,failed_receipt_test(_outcome))


def invalid_clock_test(value):
    def test(self):
        with patch.object(read,'monotonic',return_value=value):
            self.error_code('INVALID_RECONCILIATION_READ_CLOCK',self.t._prepare_reconciliation_read_request)
        self.assertIsNone(self.t._reconciliation_read_request)
        self.assertEqual(self.t._reconciliation_read_sequence,0)
    return test
for _index,_value in enumerate((True,-1,float('nan'),float('inf'),None,'100')):
    setattr(RequestTests,'test_invalid_issuance_clock_'+str(_index),invalid_clock_test(_value))


def issuance_interruption_test(kind, constructor=False):
    def test(self):
        error=kind(37)
        name='_ReconciliationReadRequest' if constructor else 'monotonic'
        before=(self.t._reconciliation_read_sequence,self.t._reconciliation_marker)
        with patch.object(read,name,side_effect=error):
            with self.assertRaises(kind) as caught:self.t._prepare_reconciliation_read_request()
        self.assertIs(caught.exception,error)
        if kind is SystemExit:self.assertEqual(caught.exception.code,37)
        self.assertIsNone(self.t._reconciliation_read_request)
        self.assertEqual((self.t._reconciliation_read_sequence,self.t._reconciliation_marker),before)
        with ThreadPoolExecutor(max_workers=1) as pool:
            request=pool.submit(self.t._prepare_reconciliation_read_request).result(2)
        self.assertEqual(request.request_id,1)
    return test
for _kind in (KeyboardInterrupt,SystemExit,GeneratorExit):
    for _constructor in (False,True):
        setattr(RequestTests,f'test_issuance_{_kind.__name__}_{_constructor}',issuance_interruption_test(_kind,_constructor))


class CoordinatorFixture(callbacks.CallbackFixture):
    def setUp(self):
        super().setUp()
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('real connection forbidden')))
        self.enterContext(patch('socket.getaddrinfo',side_effect=AssertionError('DNS forbidden')))

    def ready(self, **kwargs):
        return super().ready(api=read_api(),**kwargs)

    def secured(self):
        self.pending();self.open();self.sync()
        self.assertTrue(self.coordinator._broker_state_reconciliation_required)
        return self.coordinator

    def begin(self):return self.coordinator._begin_broker_reconciliation_attempt()
    def consume(self,attempt):return self.coordinator._consume_broker_reconciliation_attempt(attempt)
    def refresh(self):return self.broker.refresh()
    def code(self,code,function,*args):
        with self.assertRaises(ReadOnlyError) as caught:function(*args)
        self.assertEqual(caught.exception.code,code)

    def safety(self):
        c=self.coordinator
        return (c._pending_order,c._pending_confirmation,frozenset(c._attempts),c._callback_cursor,
            c._observed_state,c._broker_observed,c._strong_open_order_identity_established,
            c._broker_state_reconciliation_required,c._last_status,c._last_filled,c._last_remaining,
            c._economic_observation,dict(c._executions),c._execution_total,c._reconciliation_required,
            self.broker._reconciliation_required,self.write._requires_reconciliation(),
            self.broker._paper_authority.status,self.broker._account,self.broker._controls)


class CoordinatorTests(CoordinatorFixture):
    def test_retained_pending_broker_barrier_allowed(self):
        self.secured();attempt=self.begin()
        self.assertIs(attempt.initial_local.pending,self.coordinator._pending_order)
        self.assertTrue(attempt.initial_local.broker_state_reconciliation_required)
        self.assertIs(self.transport._reconciliation_read_request,attempt.read_request)

    def test_general_reconciliation_barrier_allowed(self):
        self.pending();self.write._latch_reconciliation();self.sync()
        attempt=self.begin()
        self.assertTrue(attempt.initial_local.coordinator_reconciliation_required)
        self.assertTrue(attempt.initial_local.control_reconciliation_required)
        self.assertTrue(attempt.initial_local.write_reconciliation_required)

    def test_pending_only_not_general_refresh_permission(self):
        self.pending()
        self.code('PAPER_RECONCILIATION_BARRIER_REQUIRED',self.begin)
        self.assertIsNone(self.transport._reconciliation_read_request)

    def test_no_identity_even_when_reconciliation_latched(self):
        self.build();self.write._latch_reconciliation()
        self.code('PAPER_RECONCILIATION_IDENTITY_UNAVAILABLE',self.begin)
        self.assertIsNone(self.transport._reconciliation_read_request)

    def test_ambiguous_dispatch_does_not_reconstruct_identity(self):
        self.build(paper_api=paper_tests.sdk(raises=True));self.invoke()
        self.assertIsNone(self.coordinator._pending_order)
        self.code('PAPER_RECONCILIATION_IDENTITY_UNAVAILABLE',self.begin)
        self.assertIsNone(self.transport._reconciliation_read_request)

    def test_one_active_attempt_only(self):
        self.secured();attempt=self.begin()
        self.code('PAPER_RECONCILIATION_ATTEMPT_OUTSTANDING',self.begin)
        self.assertIs(self.coordinator._broker_reconciliation_attempt,attempt)

    def test_begin_changes_only_handshake_bookkeeping(self):
        self.secured();before=self.safety();calls=list(self.api.calls)+list(self.paper_api.calls)
        with patch.object(self.coordinator,'_sync_broker_callbacks',side_effect=AssertionError('sync forbidden')), \
             patch.object(self.coordinator,'_sync_callbacks_locked',side_effect=AssertionError('sync forbidden')), \
             patch.object(self.broker,'refresh',side_effect=AssertionError('refresh forbidden')):
            self.begin()
        self.assertEqual(self.safety(),before)
        self.assertEqual(list(self.api.calls)+list(self.paper_api.calls),calls)

    def test_initial_pending_all_fields_frozen(self):
        self.secured();attempt=self.begin()
        values=tuple(getattr(attempt.initial_local.pending,f.name) for f in fields(dispatch._PendingOrder))
        expected=tuple(getattr(self.coordinator._pending_order,f.name) for f in fields(dispatch._PendingOrder))
        self.assertEqual(values,expected)
        with self.assertRaises(FrozenInstanceError):attempt.initial_local.callback_cursor=99
        with self.assertRaises(FrozenInstanceError):attempt.initial_local.pending.order_id=99

    def test_unread_evidence_not_acknowledged(self):
        self.secured();self.status();cursor=self.coordinator._callback_cursor
        attempt=self.begin()
        self.assertEqual(attempt.initial_local.callback_cursor,cursor)
        self.assertEqual(self.coordinator._callback_cursor,cursor)
        self.assertEqual(len(attempt.initial_local.unread_events),1)
        self.assertGreater(attempt.initial_local.write_callback_watermark,cursor)

    def test_final_view_retains_callbacks_arriving_during_read(self):
        self.secured();attempt=self.begin();self.refresh();self.status()
        cursor=self.coordinator._callback_cursor;bundle=self.consume(attempt)
        self.assertEqual(self.coordinator._callback_cursor,cursor)
        self.assertGreater(bundle.final_local.write_callback_watermark,attempt.initial_local.write_callback_watermark)
        self.assertEqual(len(bundle.final_local.unread_events),1)
        self.assertIs(bundle.attempt,attempt)

    def test_refresh_authorization_invalidation_not_failure(self):
        self.secured();attempt=self.begin()
        self.assertIs(attempt.initial_local.authorization_status,AS.ARMED)
        public=self.refresh();bundle=self.consume(attempt)
        self.assertEqual(bundle.read_receipt.outcome,'COLLECTED')
        self.assertIs(bundle.final_local.authorization_status,AS.INVALIDATED)
        self.assertIs(self.broker._paper_authority.status,AS.INVALIDATED)
        self.assertIs(bundle.read_receipt.public_snapshot,self.transport._snapshot)

    def test_incomplete_consumption_does_not_retire(self):
        self.secured();attempt=self.begin()
        self.code('RECONCILIATION_READ_INCOMPLETE',self.consume,attempt)
        self.assertIs(self.coordinator._broker_reconciliation_attempt,attempt)
        self.refresh();self.assertIs(self.consume(attempt).attempt,attempt)

    def test_equal_attempt_not_authority(self):
        self.secured();attempt=self.begin();self.refresh()
        self.code('INVALID_PAPER_RECONCILIATION_ATTEMPT',self.consume,replace(attempt))
        self.assertIs(self.consume(attempt).attempt,attempt)

    def test_consumed_attempt_replay_rejected(self):
        self.secured();attempt=self.begin();self.refresh();bundle=self.consume(attempt)
        self.code('INVALID_PAPER_RECONCILIATION_ATTEMPT',self.consume,attempt)
        self.assertIs(bundle.attempt,attempt)
        self.assertIsNone(self.coordinator._broker_reconciliation_attempt)

    def test_pending_disappeared_before_consumption(self):
        self.secured();attempt=self.begin();self.refresh();self.coordinator._pending_order=None
        self.code('PAPER_RECONCILIATION_LOCAL_STATE_CHANGED',self.consume,attempt)
        self.assertIs(self.transport._reconciliation_read_request,attempt.read_request)

    def test_equal_replaced_pending_not_original_identity(self):
        self.secured();attempt=self.begin();self.refresh()
        self.coordinator._pending_order=replace(self.coordinator._pending_order)
        self.code('PAPER_RECONCILIATION_LOCAL_STATE_CHANGED',self.consume,attempt)

    def test_changed_pending_identity_fails_before_receipt_consumption(self):
        self.secured();attempt=self.begin();self.refresh()
        self.coordinator._pending_order=replace(self.coordinator._pending_order,order_id=11)
        self.code('PAPER_RECONCILIATION_LOCAL_STATE_CHANGED',self.consume,attempt)
        self.assertIsNotNone(self.transport._reconciliation_read_receipt)

    def test_finish_changes_only_one_shot_bookkeeping(self):
        self.secured();attempt=self.begin();self.refresh();before=self.safety()
        calls=list(self.api.calls)+list(self.paper_api.calls)
        with patch.object(self.coordinator,'_sync_callbacks_locked',side_effect=AssertionError('sync forbidden')):
            self.consume(attempt)
        self.assertEqual(self.safety(),before)
        self.assertEqual(list(self.api.calls)+list(self.paper_api.calls),calls)

    def test_second_attempt_has_new_request_and_requires_new_collection(self):
        self.secured();a=self.begin();self.refresh();self.consume(a)
        b=self.begin();self.assertGreater(b.attempt_id,a.attempt_id)
        self.assertGreater(b.read_request.request_id,a.read_request.request_id)
        self.code('RECONCILIATION_READ_INCOMPLETE',self.consume,b)

    def test_bundle_construction_failure_preserves_receipt_for_retry(self):
        self.secured();attempt=self.begin();self.refresh()
        with patch.object(dispatch,'_BrokerReconciliationBundle',side_effect=RuntimeError('private text')):
            self.code('PAPER_RECONCILIATION_BUNDLE_UNAVAILABLE',self.consume,attempt)
        self.assertIs(self.consume(attempt).attempt,attempt)

    def test_concurrent_attempt_creation_one_winner(self):
        self.secured();gate=Barrier(4)
        def begin(_):
            gate.wait()
            try:return self.begin()
            except ReadOnlyError as error:return error.code
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(begin,range(4)))
        self.assertEqual(sum(type(v) is dispatch._BrokerReconciliationAttempt for v in results),1)
        self.assertEqual(results.count('PAPER_RECONCILIATION_ATTEMPT_OUTSTANDING'),3)

    def test_concurrent_bundle_consumption_one_winner(self):
        self.secured();attempt=self.begin();self.refresh();gate=Barrier(4)
        def consume(_):
            gate.wait()
            try:return self.consume(attempt)
            except ReadOnlyError as error:return error.code
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(consume,range(4)))
        self.assertEqual(sum(type(v) is dispatch._BrokerReconciliationBundle for v in results),1)
        self.assertEqual(results.count('INVALID_PAPER_RECONCILIATION_ATTEMPT'),3)

    def test_processed_execution_identity_is_primitive_only(self):
        self.secured();self.execution();self.sync();attempt=self.begin()
        self.assertEqual(attempt.initial_local.execution_total,Decimal(4))
        self.assertEqual(len(attempt.initial_local.execution_observations),1)
        for _,values in attempt.initial_local.execution_observations:
            self.assertTrue(all(type(v) in (str,int,Decimal,type(None)) for v in values))

    def test_bounded_unread_duplicate_page_preserves_upper_watermark(self):
        self.secured()
        for _ in range(130):self.status()
        cursor=self.coordinator._callback_cursor;attempt=self.begin()
        self.assertEqual(len(attempt.initial_local.unread_events),128)
        self.assertEqual(attempt.initial_local.unread_through_sequence,cursor+130)
        self.assertEqual(self.coordinator._callback_cursor,cursor)


class HandshakeBoundaryTests(CoordinatorFixture):
    def test_attempt_from_another_coordinator_rejected(self):
        self.secured();attempt=self.begin()
        other=CoordinatorFixture();other.setUp();self.addCleanup(other.doCleanups)
        other.secured();other.begin()
        other.code('INVALID_PAPER_RECONCILIATION_ATTEMPT',other.consume,attempt)

    def test_direct_request_conflict_does_not_erase_other_authority(self):
        self.secured();request=self.transport._prepare_reconciliation_read_request()
        self.code('RECONCILIATION_READ_REQUEST_OUTSTANDING',self.begin)
        self.assertIs(self.transport._reconciliation_read_request,request)
        self.assertIsNone(self.coordinator._broker_reconciliation_attempt)

    def test_malformed_authorization_status_not_retained(self):
        self.secured();self.broker._paper_authority.status='ARMED'
        self.code('PAPER_RECONCILIATION_LOCAL_STATE_INVALID',self.begin)
        self.assertIsNone(self.transport._reconciliation_read_request)

    def test_unowned_coordinator_cannot_begin(self):
        self.secured()
        with patch.object(type(self.write),'_dispatch_coordinator_owner',new=property(lambda owner:None)):
            self.code('EXACT_PAPER_COORDINATOR_REQUIRED',self.begin)

    def test_restart_missing_pending_cannot_issue(self):
        self.build();self.broker._reconciliation_required=True
        self.code('PAPER_RECONCILIATION_IDENTITY_UNAVAILABLE',self.begin)
        self.assertEqual(self.transport._reconciliation_read_sequence,0)

    def test_write_reconnect_visible_without_clearing_identity_or_barriers(self):
        self.secured();attempt=self.begin();pending=self.coordinator._pending_order
        self.write._begin_generation();self.refresh();bundle=self.consume(attempt)
        self.assertIs(bundle.final_local.pending,pending)
        self.assertGreater(bundle.final_local.write_generation,attempt.initial_local.write_generation)
        self.assertTrue(bundle.final_local.broker_state_reconciliation_required)

    def test_receipt_preserved_across_second_broker_refresh(self):
        self.secured();attempt=self.begin();self.refresh()
        public=self.transport._snapshot;generation=self.transport._generation
        self.refresh();bundle=self.consume(attempt)
        self.assertIs(bundle.read_receipt.public_snapshot,public)
        self.assertEqual(bundle.read_receipt.generation,generation)
        self.assertGreater(self.transport._generation,generation)

    def test_successful_handshake_does_not_authorize_next_order(self):
        self.secured();attempt=self.begin();self.refresh();self.consume(attempt)
        self.assert_blocked()
        self.assertFalse(self.broker.controls.trading_enabled)

    def test_failed_bundle_retires_once_without_safety_changes(self):
        self.secured();attempt=self.begin()
        self.transport.disconnect()
        with self.transport._condition:
            self.transport._generation+=1;self.transport._reset()
            self.transport._reconciliation_fail('TIMED_OUT')
        before=self.safety();bundle=self.consume(attempt)
        self.assertEqual(bundle.read_receipt.outcome,'TIMED_OUT')
        self.assertIsNone(bundle.read_receipt.public_snapshot)
        self.assertEqual(self.safety(),before)
        self.code('INVALID_PAPER_RECONCILIATION_ATTEMPT',self.consume,attempt)

    def test_capture_execution_errors_not_sanitized_as_success(self):
        self.secured()
        self.coordinator._executions={'unsafe':(object(),('private',))}
        self.code('PAPER_RECONCILIATION_LOCAL_STATE_INVALID',self.begin)
        self.assertIsNone(self.transport._reconciliation_read_request)

    def test_account_mode_and_public_surface_unchanged(self):
        self.secured();attempt=self.begin();self.refresh();bundle=self.consume(attempt)
        self.assertIs(self.broker.account_mode,AccountMode.UNKNOWN)
        self.assertIs(self.write.account_mode,AccountMode.UNKNOWN)
        self.assertIsNone(bundle.read_receipt.public_snapshot.account.mode)
        self.assertEqual([n for n in dir(dispatch._PaperOrderDispatchCoordinator) if not n.startswith('_')],[])
        self.assertFalse(hasattr(self.write,'connect'))
        for name in ('reconcile','recover','consume_reconciliation','prepare_reconciliation'):
            self.assertFalse(hasattr(self.broker,name))

    def test_models_are_frozen_opaque_and_contain_no_raw_objects(self):
        self.secured();attempt=self.begin();self.refresh();bundle=self.consume(attempt)
        seen=set()
        def inspect_value(value):
            if id(value) in seen:return
            seen.add(id(value))
            if type(value) in (str,int,float,bool,Decimal,type(None)):
                if type(value) is str:self.assertNotIn('anonymous-fixture',value)
                return
            if type(value) is datetime:return
            if isinstance(value,(tuple,frozenset)):
                for item in value:inspect_value(item)
                return
            if isinstance(value,(dispatch._CallbackState,AS,AccountMode)) or isinstance(value,read.Enum):return
            self.assertTrue(is_dataclass(value),type(value).__name__)
            self.assertTrue(type(value).__module__.startswith('tradingbot_broker.'))
            self.assertTrue(type(value).__dataclass_params__.frozen)
            for field in fields(value):inspect_value(getattr(value,field.name))
        inspect_value(bundle)
        for value in (attempt,attempt.initial_local,bundle,bundle.final_local):
            self.assertEqual(repr(value),'<PrivateBrokerReconciliationHandshake>')
        for value in (attempt.read_request,bundle.read_receipt):
            self.assertEqual(repr(value),'<PrivateReadReconciliationEvidence>')
        with self.assertRaises(FrozenInstanceError):bundle.final_local=None
        self.assertFalse(hasattr(bundle,'accepted'))
        self.assertFalse(hasattr(bundle,'success'))

    def test_callback_during_paused_refresh_is_frozen_without_deadlock(self):
        self.secured();attempt=self.begin();entered=Event();release=Event()
        original=self.api.Client.reqAllOpenOrders
        def paused(client):
            entered.set()
            if not release.wait(3):raise AssertionError('test release missing')
            return original(client)
        self.addCleanup(release.set)
        with patch.object(self.api.Client,'reqAllOpenOrders',paused):
            with ThreadPoolExecutor(max_workers=2) as pool:
                future=pool.submit(self.refresh)
                self.assertTrue(entered.wait(2))
                pool.submit(self.status).result(2)
                release.set();future.result(3)
        bundle=self.consume(attempt)
        self.assertGreater(bundle.final_local.write_callback_watermark,attempt.initial_local.write_callback_watermark)

    def test_consumption_racing_refresh_keeps_first_receipt(self):
        self.secured();attempt=self.begin();self.refresh()
        public=self.transport._snapshot;gate=Barrier(2)
        def again():gate.wait();self.refresh()
        def consume():gate.wait();return self.consume(attempt)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(again);b=pool.submit(consume)
            a.result(3);bundle=b.result(3)
        self.assertIs(bundle.read_receipt.public_snapshot,public)


def coordinator_interruption_test(kind,point):
    def test(self):
        self.secured();before=self.safety();error=kind(37)
        owner,name=(read,'monotonic') if point=='issuance' else (dispatch,'_BrokerReconciliationAttempt') if point=='attempt' else (dispatch,'_BrokerReconciliationBundle')
        attempt=None
        if point=='bundle':
            attempt=self.begin();self.refresh();before=self.safety()
        with patch.object(owner,name,side_effect=error):
            with self.assertRaises(kind) as caught:
                self.consume(attempt) if point=='bundle' else self.begin()
        self.assertIs(caught.exception,error)
        if kind is SystemExit:self.assertEqual(caught.exception.code,37)
        self.assertEqual(self.safety(),before)
        if point=='bundle':
            self.assertIs(self.coordinator._broker_reconciliation_attempt,attempt)
            self.assertIs(self.consume(attempt).attempt,attempt)
        else:
            self.assertIsNone(self.coordinator._broker_reconciliation_attempt)
            self.assertIsNone(self.transport._reconciliation_read_request)
            with ThreadPoolExecutor(max_workers=1) as pool:pool.submit(self.begin).result(2)
    return test
for _kind in (KeyboardInterrupt,SystemExit,GeneratorExit):
    for _point in ('issuance','attempt','bundle'):
        setattr(HandshakeBoundaryTests,f'test_{_point}_{_kind.__name__}',coordinator_interruption_test(_kind,_point))


def callback_interruption_test(kind,source,raw):
    def test(self):
        request=self.t._prepare_reconciliation_read_request();self.bind_without_requests()
        self.t._accounts=(evidence.ACCOUNT,);self.t._execution_id=103
        self.t._client=self.t._create_client(self.t._generation)
        wrapper=self.api.clients[-1].wrapper
        with self.t._condition:
            self.t._phase[source]='ACTIVE'
            self.t._reconciliation_request(source,103 if source=='executions' else None)
        error=kind(37)
        class Unsafe:
            def HasField(self,name):raise error
            @property
            def conId(self):raise error
            def __repr__(self):raise AssertionError('unsafe repr')
        if raw:
            name={'orders':'openOrderProtoBuf','completed':'completedOrderProtoBuf','executions':'executionDetailsProtoBuf'}[source]
            invoke=lambda:getattr(wrapper,name)(Unsafe())
        else:
            proto_name={'orders':'openOrderProtoBuf','completed':'completedOrderProtoBuf','executions':'executionDetailsProtoBuf'}[source]
            getattr(wrapper,proto_name)(evidence.raw_proto(source,status='Filled' if source=='completed' else 'Submitted'))
            if source=='executions':
                payload=NS(**evidence.EXECUTION,acctNumber=evidence.ACCOUNT)
                invoke=lambda:wrapper.execDetails(103,Unsafe(),payload)
            else:
                payload=NS(**evidence.ORDER,account=evidence.ACCOUNT)
                invoke=(lambda:wrapper.openOrder(10,Unsafe(),payload,NS(status='Submitted'))) if source=='orders' else (lambda:wrapper.completedOrder(Unsafe(),payload,NS(status='Filled')))
        with self.assertRaises(kind) as caught:invoke()
        self.assertIs(caught.exception,error)
        if kind is SystemExit:self.assertEqual(caught.exception.code,37)
        self.assertEqual(self.t._reconciliation_failure,'INTERRUPTED')
        with ThreadPoolExecutor(max_workers=1) as pool:
            receipt=pool.submit(self.t._consume_reconciliation_read_receipt,request).result(2)
        self.assertEqual(receipt.outcome,'INTERRUPTED')
        self.assertIsNone(receipt.public_snapshot)
        self.assertEqual(receipt.private_snapshot.orders,())
        self.assertEqual(receipt.private_snapshot.executions,())
    return test
for _kind in (KeyboardInterrupt,SystemExit,GeneratorExit):
    for _source in ('orders','completed','executions'):
        for _raw in (True,False):
            setattr(ReceiptTests,f'test_callback_{_source}_{_raw}_{_kind.__name__}',callback_interruption_test(_kind,_source,_raw))


def no_pending_interrupted_dispatch_test(kind):
    def test(self):
        self.build();error=kind(37)
        with patch.object(self.client,'placeOrder',side_effect=error):
            with self.assertRaises(kind) as caught:self.invoke()
        self.assertIs(caught.exception,error)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertIsNone(self.coordinator._pending_order)
        self.code('PAPER_RECONCILIATION_IDENTITY_UNAVAILABLE',self.begin)
        self.assertIsNone(self.transport._reconciliation_read_request)
    return test
for _kind in (KeyboardInterrupt,SystemExit,GeneratorExit):
    setattr(HandshakeBoundaryTests,'test_no_pending_'+_kind.__name__,no_pending_interrupted_dispatch_test(_kind))


if __name__=='__main__':unittest.main()
