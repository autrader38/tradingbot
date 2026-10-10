"""Private read evidence qualification with fake SDK/protobuf only."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import patch
import unittest

from tradingbot_broker import ibkr_readonly as module
from tradingbot_broker.readonly_models import ReadOnlyError, AccountMode
from tests.readonly_fixtures import transport, sdk, AT

D = Decimal
ACCOUNT = 'anonymous-fixture'
CONTRACT = dict(conId=17, secType='STK', exchange='SMART', symbol='DEMO', currency='USD')
ORDER = dict(orderId=10, clientId=2, permId=77, orderRef='entry-1', action='BUY',
             totalQuantity=D(10), orderType='MKT', tif='DAY')
EXECUTION = dict(orderId=10, clientId=2, permId=77, orderRef='entry-1', execId='exec-1',
                 side='BOT', shares=D(4), price=1.25, cumQty=D(4), time='20260701 13:59:59 UTC')


class Proto:
    def __init__(self, declared, **supplied):
        self._declared = frozenset(declared)
        self._supplied = dict(supplied)
        for name, value in supplied.items(): setattr(self, name, value)
    def HasField(self, name):
        if name not in self._declared: raise ValueError('unknown protobuf field')
        return name in self._supplied
    def __repr__(self): raise AssertionError('raw protobuf repr is forbidden')


def raw_proto(source, *, payload=None, contract=None, top=10, state=True, status='Submitted'):
    values = dict(EXECUTION if source == 'executions' else ORDER) if payload is None else payload
    contract_values = dict(CONTRACT) if contract is None else contract
    body = Proto(EXECUTION if source == 'executions' else ORDER, **values)
    name = 'execution' if source == 'executions' else 'order'
    supplied = dict(contract=Proto(CONTRACT, **contract_values), **{name: body})
    if source != 'executions' and state is not None:
        supplied['orderState'] = Proto(('status',), **({'status':status} if state is True else state))
    if source == 'orders' and top is not None: supplied['orderId'] = top
    return Proto(('contract', 'order', 'execution', 'orderId', 'orderState'), **supplied)


class EvidenceFixture(unittest.TestCase):
    def setUp(self):
        self.t, self.api, _ = transport(clock=lambda: AT)
        self.t._generation += 1
        self.t._reset()
        self.t._create_client(self.t._generation)  # No connect or request invocation.
        self.wrapper = self.api.clients[-1].wrapper
        self.activate()
    def activate(self):
        self.t._accounts = (ACCOUNT,)
        self.t._execution_id = 103
        for source in module._RECONCILIATION_SOURCES:
            self.t._phase[source] = 'ACTIVE'
            self.t._reconciliation_request(source, 103 if source == 'executions' else None)
    def decoded(self, source='orders', *, payload=None, contract=None, order_id=10, request_id=103, status='Submitted'):
        values = dict(EXECUTION if source == 'executions' else ORDER)
        values.update(payload or {})
        contract_values = dict(CONTRACT); contract_values.update(contract or {})
        obj = NS(**values)
        if source == 'executions':
            obj.acctNumber = ACCOUNT
            self.wrapper.execDetails(request_id, NS(**contract_values), obj)
        else:
            obj.account = ACCOUNT
            obj.lmtPrice = 1.25; obj.auxPrice = 1.25
            if source == 'orders': self.wrapper.openOrder(order_id, NS(**contract_values), obj, NS(status=status))
            else: self.wrapper.completedOrder(NS(**contract_values), obj, NS(status=status))
    def proto(self, source='orders', **kwargs):
        method = {'orders':'openOrderProtoBuf', 'completed':'completedOrderProtoBuf',
                  'executions':'executionDetailsProtoBuf'}[source]
        getattr(self.wrapper, method)(raw_proto(source, **kwargs))
    def finish(self):
        self.wrapper.openOrderEnd(); self.wrapper.completedOrdersEnd(); self.wrapper.execDetailsEnd(103)
        with self.t._condition:
            self.t._reconciliation_finish('COLLECTED')
            self.t._collecting = False
        return self.t._reconciliation_snapshot()
    def failed(self, outcome='MALFORMED'):
        result = self.t._reconciliation_snapshot()
        self.assertEqual(result.outcome, outcome)
        self.assertIsNone(self.t._reconciliation_envelope)
        self.assertTrue(any(s.state != 'COMPLETED' for s in result.collections))
        return result


class IdentityTests(EvidenceFixture):
    def test_legacy_open_exact_identity(self):
        self.decoded(); event = self.finish().orders[0]
        self.assertEqual((event.order_id,event.client_id,event.perm_id,event.order_ref),(10,2,77,'entry-1'))
        self.assertEqual((event.con_id,event.sec_type,event.exchange,event.symbol,event.currency),(17,'STK','SMART','DEMO','USD'))
        self.assertEqual((event.action,event.quantity,event.order_type,event.tif,event.status),('BUY',D(10),'MKT','DAY','Submitted'))
        self.assertEqual(event.encoding,'LEGACY')
        self.assertEqual(event.generation,self.t._generation)
        self.assertTrue(event.presence.order_id)
    def test_legacy_callback_id_conflict(self):
        self.decoded(order_id=11); self.failed()
    def test_protobuf_exact_open(self):
        self.proto(); self.decoded(); event=self.finish().orders[0]
        self.assertEqual((event.order_id,event.client_id,event.order_ref),(10,2,'entry-1'))
        self.assertEqual(event.encoding,'PROTOBUF')
        self.assertTrue(event.presence.top_order_id)
        self.assertTrue(event.presence.nested_order_id)
    def test_protobuf_top_nested_conflict(self):
        self.proto(top=11); self.assertEqual(self.failed().orders,())
    def test_protobuf_top_only_order_id(self):
        values=dict(ORDER); del values['orderId']
        self.proto(payload=values); self.decoded()
        event=self.finish().orders[0]
        self.assertEqual(event.order_id,10)
        self.assertTrue(event.presence.top_order_id)
        self.assertFalse(event.presence.nested_order_id)
    def test_protobuf_nested_only_order_id(self):
        self.proto(top=None); self.decoded(order_id=0)
        event=self.finish().orders[0]
        self.assertEqual(event.order_id,10)
        self.assertFalse(event.presence.top_order_id)
        self.assertTrue(event.presence.nested_order_id)
    def test_explicit_zero_ids_are_supplied(self):
        values=dict(ORDER,orderId=0,clientId=0)
        self.proto(payload=values,top=0); self.decoded(payload=values,order_id=0)
        event=self.finish().orders[0]
        self.assertEqual(event.order_id,0); self.assertEqual(event.client_id,0)
        self.assertTrue(event.presence.order_id); self.assertTrue(event.presence.client_id)
    def test_explicit_empty_ref_is_not_absence(self):
        values=dict(ORDER,orderRef='')
        self.proto(payload=values); self.decoded(payload=values)
        event=self.finish().orders[0]
        self.assertIsNone(event.order_ref); self.assertTrue(event.presence.order_ref)
    def test_legacy_completed_never_reads_default_order_client_ids(self):
        class Completed:
            account=ACCOUNT; permId=77; orderRef='entry-1'; action='BUY'
            totalQuantity=D(10); orderType='MKT'; tif='DAY'; lmtPrice=1.0; auxPrice=1.0
            @property
            def orderId(self): raise AssertionError('legacy completed order ID must not be inspected')
            @property
            def clientId(self): raise AssertionError('legacy completed client ID must not be inspected')
        self.wrapper.completedOrder(NS(**CONTRACT),Completed(),NS(status='Filled'))
        event=self.finish().orders[0]
        self.assertIsNone(event.order_id); self.assertIsNone(event.client_id)
        self.assertFalse(event.presence.order_id); self.assertFalse(event.presence.client_id)
        self.assertEqual((event.perm_id,event.order_ref),(77,'entry-1'))
        self.assertEqual(event.source,'COMPLETED_ORDER')
    def test_protobuf_completed_supplied_identity(self):
        self.proto('completed',status='Filled'); self.decoded('completed',status='Filled')
        event=self.finish().orders[0]
        self.assertEqual((event.order_id,event.client_id,event.order_ref,event.perm_id),(10,2,'entry-1',77))
    def test_unrelated_well_formed_order_is_retained(self):
        self.decoded(payload={'orderId':11,'orderRef':'unrelated','action':'SELL','orderType':'LMT','tif':'GTC'},order_id=11)
        event=self.finish().orders[0]
        self.assertEqual((event.order_id,event.action,event.order_type,event.tif),(11,'SELL','LMT','GTC'))
    def test_legacy_execution_identity(self):
        self.decoded('executions'); event=self.finish().executions[0]
        self.assertEqual((event.order_id,event.client_id,event.perm_id,event.order_ref,event.exec_id),(10,2,77,'entry-1','exec-1'))
        self.assertEqual((event.side,event.shares,event.price,event.cumulative_quantity),('BOT',D(4),D('1.25'),D(4)))
        self.assertEqual(event.execution_time,'20260701 13:59:59 UTC')
    def test_protobuf_execution_identity(self):
        self.proto('executions'); self.decoded('executions')
        event=self.finish().executions[0]
        self.assertEqual(event.encoding,'PROTOBUF')
        self.assertTrue(event.presence.order_ref)
    def test_duplicate_execution_id_is_idempotent(self):
        self.decoded('executions'); self.decoded('executions')
        result=self.finish()
        self.assertEqual(len(result.executions),1)
        self.assertEqual(result.last_sequence,2)
    def test_conflicting_execution_id_fails(self):
        self.decoded('executions'); self.decoded('executions',payload={'shares':D(5)})
        self.assertEqual(len(self.failed().executions),1)
    def test_zero_perm_is_not_positive_identity(self):
        self.decoded(payload={'permId':0})
        event=self.finish().orders[0]
        self.assertIsNone(event.perm_id); self.assertTrue(event.presence.perm_id)


def missing_proto_test(source, field):
    def test(self):
        values=dict(EXECUTION if source=='executions' else ORDER); del values[field]
        self.proto(source,payload=values,top=None if field=='orderId' else 10)
        default={'orderId':0,'clientId':0,'permId':0,'orderRef':''}[field]
        self.decoded(source,payload={field:default},order_id=0 if field=='orderId' else 10)
        result=self.finish(); event=(result.executions if source=='executions' else result.orders)[0]
        name={'orderId':'order_id','clientId':'client_id','permId':'perm_id','orderRef':'order_ref'}[field]
        self.assertIsNone(getattr(event,name)); self.assertFalse(getattr(event.presence,name))
    return test
for _source in ('orders','completed','executions'):
    for _field in ('orderId','clientId','permId','orderRef'):
        setattr(IdentityTests,'test_absent_'+_source+'_'+_field,missing_proto_test(_source,_field))


class EnvelopeTests(EvidenceFixture):
    def test_consumed_exactly_once(self):
        self.proto(); self.decoded(); self.assertIsNone(self.t._reconciliation_envelope)
        self.decoded(); self.assertEqual(len(self.failed().orders),1)
    def test_unmatched_envelope_at_completion_fails(self):
        self.proto(); self.wrapper.openOrderEnd(); self.failed()
    def test_one_envelope_capacity_across_sources(self):
        self.proto(); self.proto('executions'); self.failed()
    def test_wrong_decoded_source_fails(self):
        self.proto(); self.decoded('completed'); self.failed()
    def test_decoded_raw_identity_conflict(self):
        self.proto(); self.decoded(payload={'orderRef':'changed'}); self.failed()
    def test_raw_decoded_economic_conflict(self):
        self.proto(); self.decoded(payload={'totalQuantity':D(11)}); self.failed()
    def test_raw_decoded_contract_conflict(self):
        self.proto(); self.decoded(contract={'conId':18}); self.failed()
    def test_top_callback_argument_conflict(self):
        self.proto(); self.decoded(order_id=11); self.failed()
    def test_wrong_thread_cannot_consume(self):
        self.proto()
        with ThreadPoolExecutor(max_workers=1) as pool: pool.submit(self.decoded).result(2)
        self.failed()
    def test_wrong_execution_request_cannot_consume(self):
        self.proto('executions'); self.decoded('executions',request_id=104); self.failed()
    def test_mixed_encoding_is_not_guessed(self):
        self.decoded(); self.proto(); self.failed()
    def test_generation_loss_invalidates_unmatched_envelope(self):
        self.proto(); generation=self.t._generation
        self.t.disconnect(); result=self.failed('GENERATION_LOST')
        self.assertEqual(result.generation,generation)
        self.t._reset(); self.activate()
        self.t._create_client(self.t._generation)
        self.wrapper = self.api.clients[-1].wrapper
        self.decoded(); event=self.finish().orders[0]
        self.assertEqual(event.encoding,'LEGACY')
        self.assertNotEqual(event.generation,generation)
    def test_raw_missing_contract_fails(self):
        raw=Proto(('contract','order','orderId'),order=Proto(ORDER,**ORDER),orderId=10)
        self.wrapper.openOrderProtoBuf(raw); self.failed()
    def test_hasfield_non_bool_fails(self):
        class Malformed:
            def HasField(self,name): return 1
        self.wrapper.openOrderProtoBuf(Malformed()); self.failed()
    def test_no_raw_proto_survives_callback(self):
        raw=raw_proto('orders'); self.wrapper.openOrderProtoBuf(raw)
        envelope=self.t._reconciliation_envelope
        self.assertTrue(all(type(v) in (int,str,Decimal,type(None)) for _,v in envelope.values))
        raw.order.orderRef='changed'; self.decoded()
        self.assertEqual(self.finish().orders[0].order_ref,'entry-1')
    def test_raw_unrequested_source_is_failed_not_empty(self):
        self.t._phase['orders']='NOT_REQUESTED'
        self.proto(); self.failed()


class CollectionTests(EvidenceFixture):
    def test_matching_end_markers(self):
        result=self.finish()
        self.assertEqual(result.outcome,'COLLECTED')
        self.assertEqual([c.state for c in result.collections],['COMPLETED']*3)
        self.assertEqual(result.collections[2].request_id,103)
        self.assertEqual(result.orders,()); self.assertEqual(result.executions,())
    def test_wrong_execution_end_cannot_complete(self):
        self.wrapper.execDetailsEnd(104)
        self.assertEqual(self.t._reconciliation_collections['executions'].state,'ACTIVE')
        self.wrapper.execDetailsEnd(103)
        self.assertEqual(self.t._reconciliation_collections['executions'].state,'COMPLETED')
    def test_bool_execution_end_cannot_complete(self):
        self.t._execution_id=1; self.wrapper.execDetailsEnd(True)
        self.assertEqual(self.t._reconciliation_collections['executions'].state,'ACTIVE')
    def test_active_capture_has_no_final_snapshot(self):
        with self.assertRaisesRegex(ReadOnlyError,'RECONCILIATION_READ_INCOMPLETE'): self.t._reconciliation_snapshot()
    def test_not_requested_is_distinct(self):
        self.t._reset()
        with self.t._condition: self.t._reconciliation_finish('FAILED')
        self.assertEqual([s.state for s in self.t._reconciliation_snapshot().collections],['NOT_REQUESTED']*3)
    def test_timeout_is_not_empty_success(self):
        with self.assertRaisesRegex(ReadOnlyError,'IB_GATEWAY_TIMEOUT'):
            self.t._wait(lambda:False,0)
        self.failed('TIMED_OUT')
    def test_numeric_broker_error_marks_failed(self):
        self.wrapper.error(103,550,'private raw error text')
        self.failed('FAILED')
    def test_overflow_never_silently_evicts(self):
        with patch.object(module,'_RECONCILIATION_CAPACITY',2):
            for i in range(3): self.decoded(payload={'orderId':10+i,'permId':77+i},order_id=10+i)
        result=self.failed('OVERFLOW')
        self.assertEqual(len(result.orders),2)
        self.assertEqual(result.last_sequence,2)
    def test_lifetime_sequence_and_markers_survive_reset(self):
        self.decoded(); old=self.finish()
        self.t.disconnect(); self.t._reset(); self.activate()
        self.t._create_client(self.t._generation)
        self.wrapper = self.api.clients[-1].wrapper
        self.decoded()
        new=self.finish()
        self.assertGreater(new.orders[0].sequence,old.orders[0].sequence)
        self.assertGreater(new.start_marker,old.completion_marker)
        self.assertGreater(new.generation,old.generation)
        self.assertGreaterEqual(new.completed_monotonic,new.started_monotonic)
        self.assertEqual(len(old.orders),1)
    def test_snapshot_access_has_no_side_effect(self):
        self.decoded(); result=self.finish(); calls=tuple(self.api.calls)
        self.assertIs(self.t._reconciliation_snapshot(),result)
        self.assertEqual(tuple(self.api.calls),calls)
        self.assertIsNone(self.t._snapshot)
    def test_snapshot_and_nested_evidence_immutable(self):
        self.decoded(); result=self.finish()
        for obj,name,value in ((result,'generation',0),(result.orders[0],'quantity',D(20)),
                               (result.collections[0],'state','ACTIVE'),(result.orders[0].presence,'order_id',False)):
            with self.assertRaises(FrozenInstanceError): setattr(obj,name,value)
        self.assertIs(type(result.orders),tuple)
    def test_other_account_is_not_retained(self):
        obj=NS(**ORDER,account='different-private-account',lmtPrice=1,auxPrice=1)
        self.wrapper.openOrder(10,NS(**CONTRACT),obj,NS(status='Submitted'))
        result=self.finish()
        self.assertEqual(result.orders,())
        self.assertNotIn('different-private-account',repr(result))
    def test_completion_coverage_is_explicitly_limited(self):
        result=self.finish()
        self.assertEqual([s.coverage for s in result.collections],['CURRENT_OPEN_ORDERS','HISTORY_LIMITED','HISTORY_LIMITED'])


class PrivacySafetyTests(EvidenceFixture):
    def test_opaque_repr_and_no_raw_fields(self):
        self.decoded(); self.decoded('executions'); result=self.finish()
        surfaces=repr((result,result.orders,result.executions,result.collections,self.t._reconciliation_envelope))
        for forbidden in (ACCOUNT,'SimpleNamespace','acctNumber','entry-1','private raw error'):
            self.assertNotIn(forbidden,surfaces)
        for event in result.orders+result.executions:
            for forbidden in ('account','acctNumber','contract','order','execution','socket','client','whyHeld'):
                self.assertFalse(hasattr(event,forbidden))
    def test_account_mode_and_no_write_operations(self):
        self.decoded(); self.finish()
        self.assertEqual(self.api.calls,[])
        with self.assertRaisesRegex(ReadOnlyError, 'READ_ONLY_BROKER_TRANSPORT'):
            self.t.place_order()
        self.assertFalse(hasattr(self.t,'reconcile'))
    def test_no_network_or_dns(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')),patch('socket.getaddrinfo',side_effect=AssertionError('DNS forbidden')):
            self.proto(); self.decoded(); self.finish()
    def test_unknown_status_is_fixed_category(self):
        self.decoded(status='unexpected bounded status')
        self.assertEqual(self.finish().orders[0].status,'UNKNOWN')


def malformed_test(field,value,contract=False):
    def test(self):
        self.decoded(**{'contract' if contract else 'payload':{field:value}})
        self.assertEqual(self.failed().orders,())
    return test
for _field,_values,_contract in (
    ('orderId',(True,-1,2**31,'10'),False),('clientId',(True,-1,2**31,'2'),False),
    ('permId',(True,-1,2**80,'77'),False),('orderRef',(True,'x'*101,'bad\0ref','changed\nref'),False),
    ('conId',(True,0,-1,2**80),True),('totalQuantity',(True,D(0),D(-1),D('NaN'),D('Infinity'),D('1e100')),False),
    ('symbol',('',True,'x'*101,'bad\0symbol'),True)):
    for _i,_value in enumerate(_values):
        setattr(PrivacySafetyTests,f'test_malformed_{_field}_{_i}',malformed_test(_field,_value,_contract))


def interrupted_test(source,raw,exception_type,stale=False):
    def test(self):
        interruption=exception_type(37 if exception_type is SystemExit else 'private interruption marker')
        accessed=[]
        class Unsafe:
            def __repr__(self): raise AssertionError('raw repr must not run')
            @property
            def conId(self): accessed.append(True); raise interruption
            def HasField(self,name): accessed.append(True); raise interruption
        wrapper=self.wrapper
        if stale: self.t._generation+=1; self.t._reset(); self.activate()
        if raw:
            method={'orders':'openOrderProtoBuf','completed':'completedOrderProtoBuf','executions':'executionDetailsProtoBuf'}[source]
            invoke=lambda:getattr(wrapper,method)(Unsafe())
        else:
            payload=NS(**(EXECUTION if source=='executions' else ORDER),**({'acctNumber':ACCOUNT} if source=='executions' else {'account':ACCOUNT}))
            invoke=(lambda:wrapper.execDetails(103,Unsafe(),payload)) if source=='executions' else (lambda:wrapper.openOrder(10,Unsafe(),payload,NS(status='Submitted'))) if source=='orders' else (lambda:wrapper.completedOrder(Unsafe(),payload,NS(status='Filled')))
        if stale:
            invoke(); self.assertEqual(accessed,[])
            self.assertIsNone(self.t._reconciliation_result)
        else:
            with self.assertRaises(exception_type) as caught: invoke()
            self.assertIs(caught.exception,interruption)
            if exception_type is SystemExit: self.assertEqual(caught.exception.code,37)
            self.assertEqual(self.t._reconciliation_failure,'INTERRUPTED')
            result=self.failed('INTERRUPTED')
            self.assertEqual(result.orders,()); self.assertEqual(result.executions,())
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertIs(pool.submit(self.t._reconciliation_snapshot).result(2),result)
    return test
for _source in ('orders','completed','executions'):
    for _raw in (False,True):
        for _type in (KeyboardInterrupt,SystemExit,GeneratorExit):
            for _stale in (False,True):
                setattr(PrivacySafetyTests,f'test_{"stale_" if _stale else ""}{_source}_{"raw" if _raw else "decoded"}_{_type.__name__}',interrupted_test(_source,_raw,_type,_stale))


class PublicIntegrationTests(unittest.TestCase):
    def api(self, *, supported=True):
        api=sdk(server_version=200 if supported else 100)
        # Add qualified fields to this anonymous fake before forwarding callbacks.
        # Actual wrapper is constructed by transport; customize request callback payloads instead.
        def read_orders(client):
            client.order=NS(**ORDER,account=ACCOUNT,lmtPrice=1.25,auxPrice=1.25)
            client.wrapper.openOrder(10,NS(**CONTRACT),client.order,NS(status='Submitted'))
            client.wrapper.openOrderEnd()
        def completed(client,apiOnly):
            client.wrapper.completedOrder(NS(**CONTRACT),client.order,NS(status='Filled'))
            client.wrapper.completedOrdersEnd()
        def executions(client,request,executionFilter):
            client.wrapper.execDetails(request,NS(**CONTRACT),NS(**EXECUTION,acctNumber=ACCOUNT))
            client.wrapper.execDetailsEnd(request)
        api.Client.reqAllOpenOrders=read_orders
        api.Client.reqCompletedOrders=completed
        api.Client.reqExecutions=executions
        return api
    def test_public_snapshot_is_unchanged_and_private_is_additive(self):
        t,api,_=transport(self.api()); self.addCleanup(t.disconnect)
        public=t.connect(); private=t._reconciliation_snapshot()
        self.assertIs(public,t.connect())
        self.assertIs(t._reconciliation_snapshot(),private)
        self.assertEqual(private.outcome,'COLLECTED')
        self.assertEqual(len(private.orders),2); self.assertEqual(len(private.executions),1)
        self.assertIsNone(public.account.mode)
        self.assertFalse(hasattr(public,'reconciliation'))
        self.assertFalse(hasattr(public.working_orders[0],'order_ref'))
    def test_unsupported_completed_is_not_empty_completed(self):
        t,_,_=transport(self.api(supported=False)); self.addCleanup(t.disconnect)
        public=t.connect(); private=t._reconciliation_snapshot()
        state=private.collections[1]
        self.assertFalse(public.completed_orders_available)
        self.assertEqual(state.state,'UNAVAILABLE')
        self.assertFalse(state.supported); self.assertFalse(state.requested)
        self.assertEqual(private.outcome,'COLLECTED')


class AdditionalBoundaryTests(EvidenceFixture):
    def test_intervening_callback_invalidates_unmatched_raw_envelope(self):
        self.proto()
        self.wrapper.nextValidId(7)
        result = self.failed()
        self.decoded()
        self.assertEqual(result.orders, ())
        self.assertIs(self.t._reconciliation_snapshot(), result)

    def test_late_evidence_cannot_recover_failed_collection(self):
        self.proto(top=11)
        result = self.failed()
        self.proto(); self.decoded(); self.finish()
        self.assertIs(self.t._reconciliation_snapshot(), result)
        self.assertEqual(result.last_sequence, 0)

    def test_success_cannot_finalize_an_active_collection(self):
        with self.t._condition: self.t._reconciliation_finish('COLLECTED')
        self.failed('FAILED')

    def test_full_capacity_keeps_all_secured_records_before_overflow(self):
        for i in range(module._RECONCILIATION_CAPACITY + 1):
            self.decoded(payload={'orderId':i, 'permId':i+1}, order_id=i)
        result = self.failed('OVERFLOW')
        self.assertEqual(len(result.orders), 1024)
        self.assertEqual(result.orders[0].order_id, 0)
        self.assertEqual(result.orders[-1].order_id, 1023)
        self.assertEqual(result.last_sequence, 1024)

    def test_combined_order_execution_capacity(self):
        with patch.object(module, '_RECONCILIATION_CAPACITY', 2):
            self.decoded()
            self.decoded('executions')
            self.decoded('completed')
        result = self.failed('OVERFLOW')
        self.assertEqual((len(result.orders), len(result.executions)), (1, 1))

    def test_any_end_marker_with_unmatched_raw_envelope_fails(self):
        self.proto('executions')
        self.wrapper.openOrderEnd()
        self.failed()

    def test_capture_failure_does_not_destroy_public_projection(self):
        self.decoded(payload={'orderRef':'invalid\nreference'})
        result = self.failed()
        self.assertEqual(result.orders, ())
        self.assertEqual(len(self.t._orders_read), 1)
        self.assertIsNone(self.t._failure)

    def test_reference_case_and_spaces_preserved_exactly(self):
        ref = ' Entry-ABC '
        self.proto(payload=dict(ORDER, orderRef=ref))
        self.decoded(payload={'orderRef':ref})
        self.assertEqual(self.finish().orders[0].order_ref, ref)

    def test_reference_matching_has_no_case_folding(self):
        self.proto(payload=dict(ORDER, orderRef='Entry-ABC'))
        self.decoded(payload={'orderRef':'entry-abc'})
        self.failed()

    def test_safety_state_survives_snapshot_reporting_failure(self):
        interruption = KeyboardInterrupt('marker')
        class Unsafe:
            @property
            def conId(self): raise interruption
        with patch.object(module, '_ReconciliationReadSnapshot', side_effect=RuntimeError('report unavailable')):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.wrapper.openOrder(10, Unsafe(), NS(**ORDER,account=ACCOUNT), NS(status='Submitted'))
        self.assertIs(caught.exception, interruption)
        self.assertEqual(self.t._reconciliation_failure, 'INTERRUPTED')
        self.assertIsNone(self.t._reconciliation_envelope)
        with self.assertRaises(ReadOnlyError): self.t._reconciliation_snapshot()


def missing_required_proto(field, execution=False):
    def test(self):
        source = 'executions' if execution else 'orders'
        values = dict(EXECUTION if execution else ORDER)
        del values[field]
        self.proto(source, payload=values)
        self.failed()
    return test
for _field in ('action','totalQuantity','orderType','tif'):
    setattr(AdditionalBoundaryTests, 'test_missing_raw_order_' + _field, missing_required_proto(_field))
for _field in ('execId','side','shares','price'):
    setattr(AdditionalBoundaryTests, 'test_missing_raw_execution_' + _field, missing_required_proto(_field, True))


def malformed_execution(field, value):
    def test(self):
        self.decoded('executions', payload={field:value})
        self.assertEqual(self.failed().executions, ())
    return test
for _field, _values in (
    ('price', (True, -1, float('nan'), float('inf'))),
    ('cumQty', (True, -1, D('NaN'))),
    ('execId', ('', 'bad\0id', 'x'*101)),
    ('time', ('bad\ntime', True))):
    for _i, _value in enumerate(_values):
        setattr(AdditionalBoundaryTests, f'test_invalid_execution_{_field}_{_i}', malformed_execution(_field,_value))


class LifecyclePresenceTests(EvidenceFixture):
    def test_open_present_status_exact_match(self):
        self.proto(status='Submitted'); self.decoded(status='Submitted')
        event = self.finish().orders[0]
        self.assertEqual(event.status, 'Submitted')
        self.assertTrue(event.presence.order_state)
        self.assertTrue(event.presence.status)
        self.assertIn('status', event.presence.supplied)

    def test_open_present_status_conflict(self):
        self.proto(status='Cancelled'); self.decoded(status='Submitted')
        self.assertEqual(self.failed().orders, ())

    def test_open_absent_status_does_not_promote_decoded_value(self):
        self.proto(state={}); self.decoded(status='Filled')
        event = self.finish().orders[0]
        self.assertIsNone(event.status)
        self.assertTrue(event.presence.order_state)
        self.assertFalse(event.presence.status)
        self.assertNotIn('status', event.presence.supplied)
        # Independent public projection still follows the normal decoded callback.
        self.assertEqual(next(iter(self.t._orders_read.values())).state.value, 'FILLED')

    def test_absent_status_never_reads_decoded_default_for_private_capture(self):
        self.proto(state={})
        class UnsafeDefault:
            @property
            def status(self): raise AssertionError('private capture must ignore absent raw status')
        self.t._reconciliation_decoded(self.t._generation, 'orders', NS(**CONTRACT),
            NS(**ORDER, account=ACCOUNT), UnsafeDefault(), 10)
        event = self.finish().orders[0]
        self.assertIsNone(event.status)
        self.assertFalse(event.presence.status)

    def test_open_missing_order_state_fails(self):
        self.proto(state=None); self.decoded(status='Submitted')
        self.assertEqual(self.failed().orders, ())

    def test_completed_missing_order_state_fails(self):
        self.proto('completed', state=None); self.decoded('completed', status='Filled')
        self.assertEqual(self.failed().orders, ())

    def test_completed_missing_status_fails(self):
        self.proto('completed', state={}); self.decoded('completed', status='Filled')
        self.assertEqual(self.failed().orders, ())

    def test_completed_present_status_exact_match(self):
        self.proto('completed', status='Filled'); self.decoded('completed', status='Filled')
        event = self.finish().orders[0]
        self.assertEqual(event.status, 'Filled')
        self.assertTrue(event.presence.order_state)
        self.assertTrue(event.presence.status)

    def test_completed_present_status_conflict(self):
        self.proto('completed', status='Filled'); self.decoded('completed', status='Cancelled')
        self.assertEqual(self.failed().orders, ())

    def test_present_empty_open_status_has_presence_but_unknown_category(self):
        self.proto(status=''); self.decoded(status='')
        event = self.finish().orders[0]
        self.assertEqual(event.status, 'UNKNOWN')
        self.assertTrue(event.presence.status)

    def test_completed_empty_present_status_fails(self):
        self.proto('completed', status=''); self.decoded('completed', status='Filled')
        self.assertEqual(self.failed().orders, ())

    def test_bounded_unrecognized_status_discarded_after_exact_correlation(self):
        self.proto(status='bounded private lifecycle marker')
        self.decoded(status='bounded private lifecycle marker')
        event = self.finish().orders[0]
        self.assertEqual(event.status, 'UNKNOWN')
        self.assertNotIn('bounded private lifecycle marker', repr(event))
        self.assertIsNone(self.t._reconciliation_envelope)

    def test_unrecognized_status_conflict_checked_before_sanitization(self):
        self.proto(status='unrecognized A'); self.decoded(status='unrecognized B')
        self.assertEqual(self.failed().orders, ())

    def test_legacy_status_contract_unchanged(self):
        self.decoded(); self.decoded('completed', status='Filled')
        opened, completed = self.finish().orders
        self.assertEqual((opened.status, completed.status), ('Submitted', 'Filled'))
        self.assertTrue(opened.presence.status)
        self.assertTrue(completed.presence.order_state)
        self.assertIsNone(completed.order_id); self.assertIsNone(completed.client_id)

    def test_legacy_malformed_none_status_still_fails(self):
        self.decoded(status=None)
        self.assertEqual(self.failed().orders, ())

    def test_stale_lifecycle_object_not_accessed(self):
        old = self.wrapper; self.t._generation += 1; self.t._reset(); self.activate()
        class Unsafe:
            def HasField(self, name): raise AssertionError('stale proto was inspected')
        old.openOrderProtoBuf(Unsafe()); old.completedOrderProtoBuf(Unsafe())
        self.assertIsNone(self.t._reconciliation_result)
        self.assertIsNone(self.t._reconciliation_envelope)


def malformed_lifecycle_test(source, value):
    def test(self):
        self.proto(source, status=value)
        self.assertEqual(self.failed().orders, ())
    return test
for _source in ('orders', 'completed'):
    for _i, _value in enumerate((None, True, 1, 'bad\0status', 'bad\nstatus', 'x'*101)):
        setattr(LifecyclePresenceTests, f'test_malformed_raw_status_{_source}_{_i}', malformed_lifecycle_test(_source, _value))


def lifecycle_interruption_test(source, point, kind):
    def test(self):
        interruption = kind(37 if kind is SystemExit else 'private lifecycle interruption')
        raw = raw_proto(source)
        class UnsafeState:
            def HasField(self, name):
                if point == 'presence': raise interruption
                return True
            @property
            def status(self): raise interruption
            def __repr__(self): raise AssertionError('raw state repr prohibited')
        if point == 'structure':
            class UnsafeRaw:
                def HasField(self, name): return raw.HasField(name)
                def __getattr__(self, name): return getattr(raw, name)
                @property
                def orderState(self): raise interruption
                def __repr__(self): raise AssertionError('raw proto repr prohibited')
            message = UnsafeRaw()
        else:
            raw.orderState = UnsafeState(); message = raw
        method = 'openOrderProtoBuf' if source == 'orders' else 'completedOrderProtoBuf'
        with self.assertRaises(kind) as caught: getattr(self.wrapper, method)(message)
        self.assertIs(caught.exception, interruption)
        if kind is SystemExit: self.assertEqual(caught.exception.code, 37)
        self.assertEqual(self.t._reconciliation_failure, 'INTERRUPTED')
        result = self.failed('INTERRUPTED')
        self.assertEqual(result.orders, ())
        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertIs(pool.submit(self.t._reconciliation_snapshot).result(2), result)
        self.decoded(source, status='Filled'); self.finish()
        self.assertIs(self.t._reconciliation_snapshot(), result)
    return test
for _source in ('orders', 'completed'):
    for _point in ('structure', 'presence', 'value'):
        for _kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
            setattr(LifecyclePresenceTests, f'test_{_source}_{_point}_{_kind.__name__}', lifecycle_interruption_test(_source, _point, _kind))


STATUS_ARGS = (10, 'Submitted', D(0), D(10), 0.0, 77, 0, 0.0, 2, '', 0.0)


class OrderStatusBoundaryTests(EvidenceFixture):
    def test_no_envelope_preserves_healthy_capture_without_status_evidence(self):
        self.wrapper.orderStatus(*STATUS_ARGS)
        self.assertIsNone(self.t._reconciliation_failure)
        self.assertEqual(self.t._reconciliation_sequence, 0)
        self.assertEqual(self.t._reconciliation_orders, [])
        self.decoded()
        self.assertEqual(self.finish().outcome, 'COLLECTED')

    def test_no_envelope_delegates_inherited_sdk_behavior(self):
        calls = []
        def inherited(wrapper, *args): calls.append(args); return 'inherited result'
        with patch.object(self.api.Wrapper, 'orderStatus', inherited, create=True):
            self.assertEqual(self.wrapper.orderStatus(*STATUS_ARGS), 'inherited result')
        self.assertEqual(calls, [STATUS_ARGS])
        self.assertIsNone(self.t._reconciliation_failure)

    def test_envelope_invalidated_before_parent_callback(self):
        self.proto()
        observed = []
        def inherited(wrapper, *args):
            self.assertIsNone(self.t._reconciliation_envelope)
            self.assertEqual(self.t._reconciliation_failure, 'MALFORMED')
            with ThreadPoolExecutor(max_workers=1) as pool:
                observed.append(pool.submit(self.t._reconciliation_snapshot).result(2))
        with patch.object(self.api.Wrapper, 'orderStatus', inherited, create=True):
            self.wrapper.orderStatus(*STATUS_ARGS)
        self.assertIs(observed[0], self.failed())
        self.assertEqual(observed[0].orders, ())

    def test_order_status_then_new_raw_cannot_reuse_prior_envelope(self):
        self.proto(); self.wrapper.orderStatus(*STATUS_ARGS)
        result = self.failed()
        self.proto(); self.decoded()
        self.assertIs(self.t._reconciliation_snapshot(), result)
        self.assertEqual(result.orders, ())
        self.assertEqual(result.last_sequence, 0)

    def test_order_status_then_reconnect_never_publishes_prior_envelope(self):
        old = self.wrapper; self.proto(); self.wrapper.orderStatus(*STATUS_ARGS)
        result = self.failed(); self.t.disconnect(); self.t._reset(); self.activate()
        self.t._create_client(self.t._generation); self.wrapper = self.api.clients[-1].wrapper
        old.openOrder(10, NS(**CONTRACT), NS(**ORDER, account=ACCOUNT), NS(status='Submitted'))
        self.assertEqual(self.t._reconciliation_orders, [])
        self.assertIsNone(self.t._reconciliation_envelope)
        self.decoded()
        fresh = self.finish()
        self.assertEqual(fresh.outcome, 'COLLECTED')
        self.assertGreater(fresh.generation, result.generation)
        self.assertEqual(result.orders, ())
        self.assertEqual(fresh.orders[0].encoding, 'LEGACY')

    def test_stale_order_status_cannot_invalidate_current_envelope(self):
        old = self.wrapper; self.t._generation += 1; self.t._reset(); self.activate()
        self.t._create_client(self.t._generation); self.wrapper = self.api.clients[-1].wrapper
        self.proto(); old.orderStatus(*STATUS_ARGS)
        self.assertIsNotNone(self.t._reconciliation_envelope)
        self.assertIsNone(self.t._reconciliation_failure)
        self.decoded()
        self.assertEqual(self.finish().outcome, 'COLLECTED')

    def test_raw_status_arguments_not_retained(self):
        class Unsafe:
            def __repr__(self): raise AssertionError('status argument repr prohibited')
            def __str__(self): raise AssertionError('status argument str prohibited')
        self.proto(); self.wrapper.orderStatus(*(Unsafe() for _ in STATUS_ARGS))
        result = self.failed()
        self.assertEqual(result.orders, ())
        self.assertEqual(result.executions, ())
        self.assertEqual(repr(result), '<PrivateReadReconciliationEvidence>')


def order_status_pairing_test(source):
    def test(self):
        self.proto(source)
        self.wrapper.orderStatus(*STATUS_ARGS)
        self.decoded(source)
        result = self.failed()
        self.assertEqual(result.orders, ())
        self.assertEqual(result.executions, ())
        self.assertEqual(result.last_sequence, 0)
    return test
for _source in module._RECONCILIATION_SOURCES:
    setattr(OrderStatusBoundaryTests, 'test_intervening_order_status_' + _source, order_status_pairing_test(_source))


def interrupted_parent_status_test(kind):
    def test(self):
        self.proto(); interruption = kind(37 if kind is SystemExit else 'parent interruption')
        def inherited(wrapper, *args):
            self.assertEqual(self.t._reconciliation_failure, 'MALFORMED')
            self.assertIsNone(self.t._reconciliation_envelope)
            raise interruption
        with patch.object(self.api.Wrapper, 'orderStatus', inherited, create=True):
            with self.assertRaises(kind) as caught: self.wrapper.orderStatus(*STATUS_ARGS)
        self.assertIs(caught.exception, interruption)
        self.assertEqual(self.failed().orders, ())
    return test
for _kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    setattr(OrderStatusBoundaryTests, 'test_parent_' + _kind.__name__, interrupted_parent_status_test(_kind))


if __name__ == '__main__': unittest.main()


