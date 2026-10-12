"""Isolated PRE4 dispatcher tests; optional installed SDK, never an EClient."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal as D, Inexact, Rounded, localcontext
from enum import IntEnum
import importlib
import importlib.util
import inspect
from pathlib import Path
from types import MethodType, SimpleNamespace as NS
import unittest
from unittest.mock import patch
import weakref

from tradingbot_broker import ibkr_order_status_evidence as source
from tradingbot_broker import ibkr_order_status_pairing as p
from tradingbot_broker.readonly_models import ReadOnlyError
from tests.test_ibkr_order_status_evidence import Proto, RAW, LEGACY, normalized


class Incoming:
    ORDER_STATUS = 37  # Anonymous fixture value, never an assumed SDK opcode.


class DecoderDouble:
    """The qualified synchronous dispatcher/raw/conversion/normalized structure."""
    def __init__(self, wrapper, serverVersion=0):
        self.wrapper = wrapper
        self.action = None
        self.calls = []
        self.other_result = object()

    def processProtoBuf(self, protoBuf, msgId):
        self.calls.append((protoBuf, msgId))
        if self.action is not None:
            return self.action(protoBuf, msgId)
        handle = self.msgId2handleInfoProtoBuf.get(msgId)
        if handle is not None:
            return handle.processMeth(self, protoBuf)
        return self.other_result

    def processOrderStatusMsgProtoBuf(self, protobuf):
        raw = Proto.FromString(protobuf)
        self.wrapper.orderStatusProtoBuf(raw)
        self.wrapper.orderStatus(*normalized(raw.values), '', 0.0)

    def interpret(self, fields):
        return fields


DecoderDouble.msgId2handleInfoProtoBuf = {
    Incoming.ORDER_STATUS: NS(processMeth=DecoderDouble.processOrderStatusMsgProtoBuf)}


class Wrapper:
    def __init__(self):
        self.boundary = None
        self.results = []
        self.trace = []
        self.raw_action = self.normalized_action = None

    def logAnswer(self, *args): pass

    def error(self, *args): pass  # SDK reporting only; no broker or account here.

    def orderStatusProtoBuf(self, raw):
        receipt = self.boundary._current_invocation()
        self.trace.append(('raw', receipt))
        self.boundary._capture_raw(receipt, raw)
        if self.raw_action is not None:
            self.raw_action(raw, receipt)

    def orderStatus(self, *args):
        receipt = self.boundary._current_invocation()
        self.trace.append(('normalized', receipt))
        values = args[:9]
        if self.normalized_action is not None:
            values = self.normalized_action(values, receipt)
        self.results.append(self.boundary._consume_normalized(receipt, *values))


class Case(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('no network')))
        self.enterContext(patch('socket.socket.connect_ex', side_effect=AssertionError('no network')))
        self.enterContext(patch('socket.getaddrinfo', side_effect=AssertionError('no DNS')))
        self.enterContext(patch('socket.create_connection', side_effect=AssertionError('no network')))

    def fixture(self, installed=False):
        if installed:
            module = importlib.import_module('ibapi.decoder')
            decoder_type, incoming = module.Decoder, importlib.import_module('ibapi.message').IN
            message_type = module.OrderStatusProto
        else:
            decoder_type, incoming, message_type = DecoderDouble, Incoming, Proto
        wrapper = Wrapper()
        decoder = decoder_type(wrapper, 0)
        with patch.object(p, '_load_pairing_sdk', return_value=(decoder_type, incoming)):
            boundary = p._install_order_status_pairing(decoder)
        wrapper.boundary = boundary
        return NS(decoder=decoder, boundary=boundary, wrapper=wrapper,
                  decoder_type=decoder_type, incoming=incoming, message_type=message_type)

    def wire(self, fixture, **changes):
        return fixture.message_type(**dict(RAW, **changes)).SerializeToString()

    def dispatch(self, fixture, payload=None):
        return fixture.decoder.processProtoBuf(self.wire(fixture) if payload is None else payload,
                                              fixture.incoming.ORDER_STATUS)

    def invalid(self, function, *args, **kwargs):
        with self.assertRaises(ReadOnlyError) as caught:
            function(*args, **kwargs)
        self.assertEqual(str(caught.exception), p._ERROR)
        self.assertIsNone(caught.exception.__cause__)

    def closed(self, fixture):
        self.assertEqual(fixture.boundary._OrderStatusPairingBoundary__stack, [])
        self.invalid(fixture.boundary._current_invocation)

    def action(self, fixture, function):
        fixture.decoder.action = lambda payload, msg_id: function(fixture.boundary)


class InstallationTests(Case):
    def test_only_instance_method_changes(self):
        method, table = DecoderDouble.processProtoBuf, DecoderDouble.msgId2handleInfoProtoBuf
        contents = tuple(table.items())
        f = self.fixture()
        self.assertIs(DecoderDouble.processProtoBuf, method)
        self.assertIs(DecoderDouble.msgId2handleInfoProtoBuf, table)
        self.assertEqual(tuple(table.items()), contents)
        self.assertIs(f.decoder.processProtoBuf.__self__, f.decoder)

    def test_second_instance_unaffected(self):
        f = self.fixture(); other = DecoderDouble(Wrapper())
        self.assertIs(other.processProtoBuf.__func__, DecoderDouble.processProtoBuf)
        self.assertNotIn(p._INSTALLATION, vars(other))
        self.assertNotIn('processProtoBuf', vars(other))

    def test_double_installation_rejected(self):
        f = self.fixture()
        with patch.object(p, '_load_pairing_sdk', return_value=(DecoderDouble, Incoming)):
            self.invalid(p._install_order_status_pairing, f.decoder)

    def test_foreign_exact_type_rejected(self):
        with patch.object(p, '_load_pairing_sdk', return_value=(DecoderDouble, Incoming)):
            self.invalid(p._install_order_status_pairing, NS())

    def test_subclass_rejected(self):
        class Child(DecoderDouble): pass
        with patch.object(p, '_load_pairing_sdk', return_value=(DecoderDouble, Incoming)):
            self.invalid(p._install_order_status_pairing, Child(Wrapper()))

    def test_instance_original_replacement_rejected(self):
        decoder = DecoderDouble(Wrapper()); decoder.processProtoBuf = lambda *args: None
        with patch.object(p, '_load_pairing_sdk', return_value=(DecoderDouble, Incoming)):
            self.invalid(p._install_order_status_pairing, decoder)

    def test_copied_original_rejected(self):
        a, b = DecoderDouble(Wrapper()), DecoderDouble(Wrapper())
        b.processProtoBuf = a.processProtoBuf
        with patch.object(p, '_load_pairing_sdk', return_value=(DecoderDouble, Incoming)):
            self.invalid(p._install_order_status_pairing, b)

    def test_copied_installation_marker_rejected(self):
        f = self.fixture(); other = DecoderDouble(Wrapper())
        vars(other)[p._INSTALLATION] = f.boundary
        with patch.object(p, '_load_pairing_sdk', return_value=(DecoderDouble, Incoming)):
            self.invalid(p._install_order_status_pairing, other)

    def test_interceptor_explicit_foreign_receiver_rejected(self):
        f = self.fixture(); other = DecoderDouble(Wrapper())
        self.invalid(f.decoder.processProtoBuf.__func__, other, self.wire(f), Incoming.ORDER_STATUS)
        self.assertEqual(other.calls, [])

    def test_replaced_installed_method_rejected_by_boundary(self):
        f = self.fixture(); vars(f.decoder)['processProtoBuf'] = lambda *args: None
        self.invalid(f.boundary._current_invocation)

    def test_class_dispatcher_replacement_detected(self):
        f = self.fixture()
        with patch.object(DecoderDouble, 'processProtoBuf', lambda *args: None):
            self.invalid(self.dispatch, f)

    def test_handle_replacement_detected(self):
        f = self.fixture()
        with patch.dict(DecoderDouble.msgId2handleInfoProtoBuf,
                        {Incoming.ORDER_STATUS: NS(processMeth=DecoderDouble.processOrderStatusMsgProtoBuf)}):
            self.invalid(self.dispatch, f)

    def test_handler_mutation_detected(self):
        f = self.fixture(); handle = DecoderDouble.msgId2handleInfoProtoBuf[Incoming.ORDER_STATUS]
        with patch.object(handle, 'processMeth', lambda *args: None):
            self.invalid(self.dispatch, f)

    def test_replacement_after_pair_detected_at_exit_without_orphan(self):
        f=self.fixture(); replacement=None
        def mutate_after_pair(b):
            nonlocal replacement
            r=b._current_invocation(); b._capture_raw(r,Proto(**RAW)); b._consume_normalized(r,*LEGACY)
            replacement=patch.object(DecoderDouble,'processProtoBuf',lambda *args: None)
            replacement.start()
        self.action(f,mutate_after_pair)
        try: self.invalid(self.dispatch,f)
        finally:
            if replacement is not None: replacement.stop()
        self.closed(f)

    def test_concurrent_installation_only_one_succeeds(self):
        decoder = DecoderDouble(Wrapper())
        def install():
            try: return p._install_order_status_pairing(decoder)
            except ReadOnlyError: return None
        with patch.object(p, '_load_pairing_sdk', return_value=(DecoderDouble, Incoming)):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: install(), range(2)))
        self.assertEqual(sum(value is not None for value in results), 1)

    def test_boundary_cannot_be_constructed_or_rebound(self):
        self.invalid(p._OrderStatusPairingBoundary)
        f = self.fixture()
        self.invalid(setattr, f.boundary, '_OrderStatusPairingBoundary__decoder', object())
        self.invalid(delattr, f.boundary, '_OrderStatusPairingBoundary__decoder')

    def test_sdk_enum_identity_is_qualified_without_opcode_guess(self):
        class Messages(IntEnum): ORDER_STATUS=37
        decoder=DecoderDouble(Wrapper())
        with patch.object(p,'_load_pairing_sdk',return_value=(DecoderDouble,Messages)):
            boundary=p._install_order_status_pairing(decoder)
        decoder.wrapper.boundary=boundary
        decoder.processProtoBuf(Proto(**RAW).SerializeToString(),Messages.ORDER_STATUS)
        self.assertEqual(len(decoder.wrapper.results),1)


def malformed_sdk_test(label):
    def test(self):
        class Decoder(DecoderDouble):
            processProtoBuf = DecoderDouble.processProtoBuf
            processOrderStatusMsgProtoBuf = DecoderDouble.processOrderStatusMsgProtoBuf
            msgId2handleInfoProtoBuf = dict(DecoderDouble.msgId2handleInfoProtoBuf)
        class Messages: ORDER_STATUS = Incoming.ORDER_STATUS
        if label == 'bool': Messages.ORDER_STATUS = True
        elif label == 'zero': Messages.ORDER_STATUS = 0
        elif label == 'string': Messages.ORDER_STATUS = '37'
        elif label == 'missing': del Messages.ORDER_STATUS
        elif label == 'table': Decoder.msgId2handleInfoProtoBuf = []
        elif label == 'handle': Decoder.msgId2handleInfoProtoBuf = {37: NS(processMeth=lambda *args: None)}
        elif label == 'signature': Decoder.processProtoBuf = lambda self, *args: None
        elif label == 'method': Decoder.processProtoBuf = None
        with patch.object(p, '_load_pairing_sdk', return_value=(Decoder, Messages)):
            decoder = Decoder(Wrapper())
            self.invalid(p._install_order_status_pairing, decoder)
            self.assertNotIn(p._INSTALLATION, vars(decoder))
    return test


for label in ('bool', 'zero', 'string', 'missing', 'table', 'handle', 'signature', 'method'):
    setattr(InstallationTests, 'test_malformed_sdk_' + label, malformed_sdk_test(label))


class ScopeTests(Case):
    def test_single_invocation_exact_provenance(self):
        f = self.fixture(); self.dispatch(f)
        raw, follower = f.wrapper.trace
        self.assertIs(raw[1], follower[1])
        self.assertIs(f.wrapper.results[0].invocation, raw[1])
        self.assertEqual((raw[1].invocation_number, raw[1].depth, raw[1].parent_number), (1, 1, None))
        self.closed(f)

    def test_identical_messages_are_distinct_invocations(self):
        f = self.fixture(); self.dispatch(f); self.dispatch(f)
        a, b = f.wrapper.results
        self.assertEqual(a.observation, b.observation)
        self.assertIsNot(a.invocation, b.invocation)
        self.assertEqual((a.invocation.invocation_number, b.invocation.invocation_number), (1, 2))

    def test_nested_lifo_pairing(self):
        f = self.fixture()
        def inner(raw, receipt):
            if receipt.depth == 1: self.dispatch(f, self.wire(f, orderId=11))
        f.wrapper.raw_action = inner
        self.dispatch(f)
        trace = f.wrapper.trace
        self.assertEqual([kind for kind, _ in trace], ['raw','raw','normalized','normalized'])
        self.assertIs(trace[0][1], trace[3][1]); self.assertIs(trace[1][1], trace[2][1])
        self.assertEqual(trace[1][1].parent_number, trace[0][1].invocation_number)
        self.assertEqual([r.observation.order_id for r in f.wrapper.results], [11, 10])
        self.closed(f)

    def test_inner_cannot_consume_outer(self):
        f = self.fixture(); outer = []
        def nested(raw, receipt):
            if receipt.depth == 1:
                outer.append(receipt); self.dispatch(f)
            else:
                self.invalid(f.boundary._consume_normalized, outer[0], *LEGACY)
        f.wrapper.raw_action = nested
        self.dispatch(f); self.assertEqual(len(f.wrapper.results), 2)

    def test_wrong_lifo_close_does_not_remove_outer(self):
        f = self.fixture(); outer = []
        def nested(raw, receipt):
            if receipt.depth == 1:
                outer.append(receipt); self.dispatch(f)
            else: self.invalid(f.boundary._close, outer[0])
        f.wrapper.raw_action = nested
        self.dispatch(f); self.assertEqual(len(f.wrapper.results), 2); self.closed(f)

    def test_inner_orphan_does_not_poison_outer(self):
        f = self.fixture()
        def nested(raw, receipt):
            if receipt.depth == 1:
                prior = f.decoder.action
                self.action(f, lambda b: b._capture_raw(b._current_invocation(), Proto(**RAW)))
                self.invalid(self.dispatch, f)
                f.decoder.action = prior
        f.wrapper.raw_action = nested
        self.dispatch(f); self.assertEqual(len(f.wrapper.results), 1); self.closed(f)

    def test_orphan_cannot_be_consumed_later(self):
        f = self.fixture(); receipts = []
        def orphan(b):
            receipt = b._current_invocation(); receipts.append(receipt)
            b._capture_raw(receipt, Proto(**RAW))
        self.action(f, orphan); self.invalid(self.dispatch, f); self.closed(f)
        self.invalid(f.boundary._consume_normalized, receipts[0], *LEGACY)
        f.decoder.action = None; self.dispatch(f)
        self.assertEqual(f.wrapper.results[0].invocation.invocation_number, 2)

    def test_no_raw_is_an_orphan(self):
        f = self.fixture(); self.action(f, lambda b: None)
        self.invalid(self.dispatch, f); self.closed(f)

    def test_normalized_without_raw_rejected(self):
        f = self.fixture()
        self.action(f, lambda b: b._consume_normalized(b._current_invocation(), *LEGACY))
        self.invalid(self.dispatch, f); self.closed(f)

    def test_duplicate_raw_invalidates_invocation(self):
        f = self.fixture()
        f.wrapper.raw_action = lambda raw, r: f.boundary._capture_raw(r, raw)
        self.invalid(self.dispatch, f); self.closed(f)

    def test_duplicate_normalized_invalidates_invocation(self):
        f = self.fixture()
        def twice(b):
            r = b._current_invocation(); b._capture_raw(r, Proto(**RAW))
            b._consume_normalized(r, *LEGACY); b._consume_normalized(r, *LEGACY)
        self.action(f, twice); self.invalid(self.dispatch, f); self.closed(f)

    def test_caught_duplicate_still_cannot_report_success(self):
        f = self.fixture()
        def twice(b):
            r=b._current_invocation(); b._capture_raw(r, Proto(**RAW)); b._consume_normalized(r,*LEGACY)
            self.invalid(b._consume_normalized,r,*LEGACY)
        self.action(f, twice); self.invalid(self.dispatch, f); self.closed(f)

    def test_reconstructed_equal_receipt_rejected(self):
        f = self.fixture()
        f.wrapper.raw_action = lambda raw,r: self.invalid(f.boundary._consume_normalized, replace(r), *LEGACY)
        self.dispatch(f); self.assertEqual(len(f.wrapper.results), 1)

    def test_foreign_boundary_receipt_rejected(self):
        a,b = self.fixture(),self.fixture(); self.dispatch(a)
        foreign = a.wrapper.results[0].invocation
        b.wrapper.raw_action = lambda raw,r: self.invalid(b.boundary._consume_normalized, foreign, *LEGACY)
        self.dispatch(b)

    def test_reused_completed_receipt_rejected(self):
        f=self.fixture(); self.dispatch(f); old=f.wrapper.results[0].invocation
        f.wrapper.raw_action=lambda raw,r: self.invalid(f.boundary._capture_raw,old,raw)
        self.dispatch(f); self.closed(f)

    def test_tampered_receipt_does_not_establish_provenance(self):
        f=self.fixture()
        def tamper(raw,r): object.__setattr__(r,'invocation_number',27)
        f.wrapper.raw_action=tamper; self.invalid(self.dispatch,f); self.closed(f)

    def test_maximum_depth_fails_and_cleans_entire_stack(self):
        f=self.fixture(); f.wrapper.raw_action=lambda raw,r: self.dispatch(f)
        self.invalid(self.dispatch,f); self.closed(f)
        f.wrapper.raw_action=None; self.dispatch(f)
        self.assertEqual(f.wrapper.results[0].invocation.invocation_number,p._MAX_DEPTH+1)

    def test_invocation_number_exhaustion_not_reset(self):
        f=self.fixture()
        object.__setattr__(f.boundary,'_OrderStatusPairingBoundary__counter',p._MAX_INVOCATION)
        self.invalid(self.dispatch,f); self.invalid(self.dispatch,f); self.closed(f)

    def test_caller_cannot_invent_decoder_scope(self):
        f=self.fixture(); self.invalid(f.boundary._begin,object()); self.closed(f)

    def test_invalid_nesting_is_discarded_on_decoder_exit(self):
        f=self.fixture()
        def invalid_nesting(b):
            # Deliberate internal corruption seam, not an available capability.
            b._begin(b._OrderStatusPairingBoundary__entry_key)
        self.action(f,invalid_nesting); self.invalid(self.dispatch,f); self.closed(f)
        f.decoder.action=None; self.dispatch(f); self.closed(f)

    def test_early_close_cannot_publish_successful_dispatch(self):
        f=self.fixture()
        def early(b):
            r=b._current_invocation(); b._capture_raw(r,Proto(**RAW))
            b._consume_normalized(r,*LEGACY); b._close(r)
        self.action(f,early); self.invalid(self.dispatch,f); self.closed(f)

    def test_unrelated_nested_dispatch_cannot_borrow_outer_receipt(self):
        f=self.fixture()
        def nested(raw,r):
            prior=f.decoder.action
            def unrelated(payload,msg_id):
                self.invalid(f.boundary._current_invocation)
                self.invalid(f.boundary._consume_normalized,r,*LEGACY)
            f.decoder.action=unrelated
            f.decoder.processProtoBuf(b'unrelated',413)
            f.decoder.action=prior
        f.wrapper.raw_action=nested
        self.dispatch(f); self.assertEqual(len(f.wrapper.results),1); self.closed(f)

    def test_nested_status_inside_other_message_has_its_own_scope(self):
        f=self.fixture()
        def nested(raw,r):
            if r.depth!=1: return
            prior=f.decoder.action
            def unrelated(payload,msg_id):
                f.decoder.action=None
                self.dispatch(f,self.wire(f,orderId=11))
            f.decoder.action=unrelated
            f.decoder.processProtoBuf(b'unrelated',413)
            f.decoder.action=prior
        f.wrapper.raw_action=nested
        self.dispatch(f)
        self.assertEqual([r.observation.order_id for r in f.wrapper.results],[11,10]); self.closed(f)


class SourceTests(Case):
    def test_all_nine_fields_and_presence(self):
        f=self.fixture(); self.dispatch(f); result=f.wrapper.results[0]
        self.assertEqual(result.observation,source._adapt_protobuf_order_status(Proto(**RAW),normalized=LEGACY))
        self.assertEqual(len(result.observation.presence.wire_supplied),9)

    def test_supplied_zero_retained(self):
        f=self.fixture(); values=dict(orderId=0,clientId=0,permId=0,parentId=0,
            status='Submitted',filled='0',remaining='0',avgFillPrice=0.0,lastFillPrice=0.0)
        self.dispatch(f,Proto(**values).SerializeToString())
        obs=f.wrapper.results[0].observation
        self.assertEqual(obs.filled,D(0)); self.assertEqual(obs.remaining,D(0))
        self.assertEqual(obs.presence.filled,source._OrderStatusSourceQuality.SUPPLIED)

    def test_all_absent_fallbacks_unavailable(self):
        f=self.fixture(); self.dispatch(f,Proto().SerializeToString())
        obs=f.wrapper.results[0].observation
        self.assertEqual(obs.presence.wire_supplied,frozenset())
        for name,_,_,_ in source._SOURCE_FIELDS: self.assertIsNone(getattr(obs,name))

    def test_mixed_absent_supplied_zero_and_supplied_unavailable(self):
        f=self.fixture()
        values=dict(orderId=10,filled=str(source._UNSET_DECIMAL),remaining='0',avgFillPrice=0.0)
        self.dispatch(f,Proto(**values).SerializeToString()); obs=f.wrapper.results[0].observation
        self.assertIsNone(obs.filled); self.assertIsNone(obs.client_id)
        self.assertEqual(obs.remaining,D(0))
        self.assertEqual(obs.presence.wire_supplied,
                         frozenset(('order_id','filled','remaining','average_fill_price')))

    def test_unknown_raw_status_must_match_before_projection(self):
        f=self.fixture()
        def change(values,r):
            values=list(values); values[1]='unrecognized-B'; return tuple(values)
        f.wrapper.normalized_action=change
        self.invalid(self.dispatch,f,self.wire(f,status='unrecognized-A')); self.closed(f)

    def test_same_unknown_status_pairs_without_publishing_text(self):
        f=self.fixture(); self.dispatch(f,self.wire(f,status='unrecognized-A'))
        self.assertEqual(f.wrapper.results[0].observation.status,'UNKNOWN')
        self.assertNotIn('unrecognized',repr(f.wrapper.results[0]))

    def test_decimal_numeric_equivalence_preserves_raw_representation(self):
        f=self.fixture()
        def equivalent(values,r):
            values=list(values); values[2]=D('4'); values[3]=D('6'); return tuple(values)
        f.wrapper.normalized_action=equivalent; self.dispatch(f)
        self.assertEqual(f.wrapper.results[0].observation.filled.as_tuple(),D('4.00').as_tuple())

    def test_signed_zero_float_contradiction_rejected(self):
        f=self.fixture()
        def positive(values,r):
            values=list(values); values[4]=0.0; return tuple(values)
        f.wrapper.normalized_action=positive
        self.invalid(self.dispatch,f,self.wire(f,avgFillPrice=-0.0))

    def test_signed_zero_float_preserved(self):
        f=self.fixture(); self.dispatch(f,self.wire(f,avgFillPrice=-0.0,lastFillPrice=-0.0))
        self.assertEqual(f.wrapper.results[0].observation.average_fill_price.as_tuple(),D('-0.0').as_tuple())

    def test_decimal_context_does_not_round_or_change_flags(self):
        with localcontext() as context:
            context.prec=2; context.traps[Inexact]=context.traps[Rounded]=True
            initial=dict(context.flags)
            f=self.fixture(); self.dispatch(f,self.wire(f,filled='0.123456789123456789',remaining='9.876543210876543211'))
            self.assertEqual(dict(context.flags),initial)

    def test_source_mutation_detected_against_original_copy(self):
        f=self.fixture(); f.wrapper.raw_action=lambda raw,r: raw.values.update(filled='5')
        self.invalid(self.dispatch,f); self.closed(f)

    def test_no_absent_getter_used_during_raw_capture(self):
        f=self.fixture()
        def body(b):
            raw=Proto(); r=b._current_invocation(); b._capture_raw(r,raw)
            self.assertEqual(raw.reads,[]); b._consume_normalized(r,*normalized({}))
        self.action(f,body); self.dispatch(f)


def mismatch_test(index):
    def test(self):
        f=self.fixture()
        changes=(11,'Cancelled',D(5),D(5),1.26,78,1,1.6,3)
        def mismatch(values,r):
            values=list(values); values[index]=changes[index]; return tuple(values)
        f.wrapper.normalized_action=mismatch
        self.invalid(self.dispatch,f); self.closed(f)
    return test


for index,(name,_,_,_) in enumerate(source._SOURCE_FIELDS):
    setattr(SourceTests,'test_mismatch_'+name,mismatch_test(index))


def malformed_source_test(name,value):
    def test(self):
        f=self.fixture()
        self.action(f,lambda b:b._capture_raw(b._current_invocation(),Proto(**dict(RAW,**{name:value}))))
        self.invalid(self.dispatch,f); self.closed(f)
    return test


for label,name,value in (
    ('bool_id','orderId',True),('negative_id','orderId',-1),('bad_status','status',object()),
    ('long_status','status','x'*101),('empty_quantity','filled',''),('nan','filled','NaN'),
    ('infinity','filled','Infinity'),('negative','filled','-1'),('digits','filled','1'*65),
    ('exponent','filled','1E65'),('float_quantity','filled',1.0),('bool_price','avgFillPrice',True),
    ('nan_price','avgFillPrice',float('nan')),('integer_price','avgFillPrice',1)):
    setattr(SourceTests,'test_malformed_'+label,malformed_source_test(name,value))


class OrphanInterruptionTests(Case):
    def test_suppressed_normalized_callback_is_orphan(self):
        f=self.fixture()
        self.action(f,lambda b:b._capture_raw(b._current_invocation(),Proto(**RAW)))
        self.invalid(self.dispatch,f); self.closed(f)

    def test_badmessage_same_object_and_original_reporting(self):
        class BadMessage(Exception): pass
        error=BadMessage('anonymous fixture error'); f=self.fixture(); reports=[]
        def sdk_behavior(b):
            b._capture_raw(b._current_invocation(),Proto(**RAW)); reports.append('SDK_REPORT'); raise error
        self.action(f,sdk_behavior)
        with self.assertRaises(BadMessage) as caught: self.dispatch(f)
        self.assertIs(caught.exception,error); self.assertEqual(reports,['SDK_REPORT']); self.closed(f)


def interruption_test(kind,seam):
    def test(self):
        f=self.fixture(); error=kind()
        def body(b):
            r=b._current_invocation()
            if seam=='before_raw': raise error
            if seam=='raw_adapter':
                with patch.object(source,'_adapt_protobuf_order_status',side_effect=error):
                    b._capture_raw(r,Proto(**RAW))
            b._capture_raw(r,Proto(**RAW))
            if seam in ('after_raw','conversion'): raise error
            if seam=='verification':
                with patch.object(source,'_adapt_protobuf_order_status',side_effect=error):
                    b._consume_normalized(r,*LEGACY)
            b._consume_normalized(r,*LEGACY)
            if seam=='after_pair': raise error
        self.action(f,body)
        with self.assertRaises(kind) as caught: self.dispatch(f)
        self.assertIs(caught.exception,error); self.closed(f)
        f.decoder.action=None; self.dispatch(f)
        self.assertEqual(f.wrapper.results[-1].invocation.invocation_number,2)
    return test


def interrupted_entry_test(kind):
    def test(self):
        f=self.fixture(); error=kind()
        original=p._OrderStatusPairingBoundary._begin
        def enter_then_raise(boundary,key):
            original(boundary,key); raise error
        with patch.object(p._OrderStatusPairingBoundary,'_begin',enter_then_raise):
            with self.assertRaises(kind) as caught: self.dispatch(f)
        self.assertIs(caught.exception,error); self.closed(f)
        self.dispatch(f)
        self.assertEqual(f.wrapper.results[0].invocation.invocation_number,2)
    return test


for kind in (RuntimeError,KeyboardInterrupt,SystemExit,GeneratorExit):
    setattr(OrphanInterruptionTests,'test_'+kind.__name__+'_scope_entry',interrupted_entry_test(kind))
    for seam in ('before_raw','raw_adapter','after_raw','conversion','verification','after_pair'):
        setattr(OrphanInterruptionTests,'test_'+kind.__name__+'_'+seam,interruption_test(kind,seam))


class ThreadTests(Case):
    def foreign(self,operation):
        f=self.fixture()
        def probe(raw,r):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(operation,f.boundary,r,raw)
                with self.assertRaises(ReadOnlyError) as caught: future.result(2)
                self.assertEqual(str(caught.exception),p._ERROR)
        f.wrapper.raw_action=probe; self.dispatch(f)
        self.assertEqual(len(f.wrapper.results),1); self.closed(f)

    def test_foreign_raw(self): self.foreign(lambda b,r,raw:b._capture_raw(r,raw))
    def test_foreign_normalized(self): self.foreign(lambda b,r,raw:b._consume_normalized(r,*LEGACY))
    def test_foreign_close(self): self.foreign(lambda b,r,raw:b._close(r))
    def test_foreign_current(self): self.foreign(lambda b,r,raw:b._current_invocation())
    def test_foreign_decoder_entry(self):
        f=self.fixture()
        def probe(raw,r):
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaises(ReadOnlyError): pool.submit(self.dispatch,f).result(2)
        f.wrapper.raw_action=probe; self.dispatch(f); self.assertEqual(len(f.wrapper.results),1)

    def test_sequential_invocations_on_distinct_threads(self):
        f=self.fixture()
        with ThreadPoolExecutor(max_workers=1) as pool: pool.submit(self.dispatch,f).result(2)
        self.dispatch(f)
        a,b=f.wrapper.results
        self.assertNotEqual(a.invocation.thread_ident,b.invocation.thread_ident)
        self.assertEqual((a.invocation.invocation_number,b.invocation.invocation_number),(1,2))


class PrivacyBoundaryTests(Case):
    def test_raw_and_descriptor_released_before_consumption(self):
        f=self.fixture()
        def body(b):
            r=b._current_invocation(); raw=Proto(**RAW)
            class Descriptor: pass
            descriptor=Descriptor(); vars(descriptor).update(vars(raw.DESCRIPTOR))
            raw.DESCRIPTOR=descriptor
            raw_ref,descriptor_ref=weakref.ref(raw),weakref.ref(descriptor)
            del descriptor
            b._capture_raw(r,raw); del raw
            self.assertIsNone(raw_ref()); self.assertIsNone(descriptor_ref())
            b._consume_normalized(r,*LEGACY)
        self.action(f,body); self.dispatch(f)

    def test_temporary_unknown_text_erased_on_consumption_and_exit(self):
        f=self.fixture()
        def body(b):
            r=b._current_invocation(); b._capture_raw(r,Proto(**dict(RAW,status='private-X')))
            frame=b._OrderStatusPairingBoundary__stack[-1]
            args=list(LEGACY); args[1]='private-X'
            result=b._consume_normalized(r,*args)
            self.assertIsNone(frame.snapshot); self.assertIsNone(frame.observation)
            self.assertEqual(result.observation.status,'UNKNOWN')
            f.wrapper.results.append(result)
        self.action(f,body); self.dispatch(f); self.closed(f)
        self.assertNotIn('private-X',repr(f.wrapper.results))

    def test_result_and_receipt_frozen_slotted_opaque(self):
        f=self.fixture(); self.dispatch(f); result=f.wrapper.results[0]
        for value in (result,result.invocation):
            self.assertFalse(hasattr(value,'__dict__'))
            self.assertEqual(repr(value),'<PrivateOrderStatusPairingValue>')
            with self.assertRaises(FrozenInstanceError): setattr(value,fields(value)[0].name,None)

    def test_results_have_no_transport_authority_fields(self):
        f=self.fixture(); self.dispatch(f); result=f.wrapper.results[0]
        names={x.name for value in (result,result.invocation,result.observation) for x in fields(value)}
        self.assertTrue(names.isdisjoint({'generation','sequence','request_id','received_at','account',
            'order_ref','currentness','inventory','authorization','recovery','decoder','raw','payload','descriptor'}))

    def test_normalized_decimal_arguments_are_independent_copies(self):
        f=self.fixture(); values=tuple(LEGACY)
        def body(b):
            r=b._current_invocation(); b._capture_raw(r,Proto(**RAW))
            result=b._consume_normalized(r,*values)
            self.assertIsNot(result.observation.filled,values[2])
            self.assertIsNot(result.observation.remaining,values[3])
        self.action(f,body); self.dispatch(f); self.closed(f)

    def test_no_completed_history_accumulation(self):
        f=self.fixture()
        for _ in range(100): self.dispatch(f)
        self.closed(f)
        self.assertEqual(f.boundary._OrderStatusPairingBoundary__counter,100)

    def test_non_order_message_exact_delegation_no_adapter(self):
        f=self.fixture(); payload=bytes(bytearray(b'opaque bytes')); message=413
        with patch.object(source,'_adapt_protobuf_order_status',side_effect=AssertionError('no adaptation')):
            result=f.decoder.processProtoBuf(payload,message)
        self.assertIs(result,f.decoder.other_result)
        self.assertIs(f.decoder.calls[0][0],payload); self.assertIs(f.decoder.calls[0][1],message)
        self.assertEqual(len(f.decoder.calls),1); self.closed(f)
        self.assertEqual(f.boundary._OrderStatusPairingBoundary__counter,0)

    def test_legacy_interpret_unchanged(self):
        original=DecoderDouble.interpret; f=self.fixture(); values=object()
        self.assertIs(f.decoder.interpret(values),values)
        self.assertIs(DecoderDouble.interpret,original)
        self.assertEqual(f.boundary._OrderStatusPairingBoundary__counter,0)

    def test_module_has_no_public_exports_or_integration_imports(self):
        text=Path(p.__file__).read_text()
        for forbidden in ('sqlite3','paper_dispatch','paper_reconciliation','time.monotonic','datetime.now'):
            self.assertNotIn(forbidden,text)
        import tradingbot_broker
        self.assertFalse(hasattr(tradingbot_broker,'_OrderStatusPairingBoundary'))
        self.assertFalse(hasattr(tradingbot_broker,'_PairedOrderStatus'))


class InstalledSDKContractTests(Case):
    def fixtures(self):
        yield self.fixture()
        if importlib.util.find_spec('ibapi') is not None:
            yield self.fixture(installed=True)

    def test_real_or_double_handle_exact_and_global_tables_unchanged(self):
        for f in self.fixtures():
            table=f.decoder_type.msgId2handleInfoProtoBuf
            before=tuple(table.items()); dispatcher=f.decoder_type.processProtoBuf
            self.assertIs(table[f.incoming.ORDER_STATUS].processMeth,f.decoder_type.processOrderStatusMsgProtoBuf)
            self.dispatch(f)
            self.assertIs(f.decoder_type.msgId2handleInfoProtoBuf,table)
            self.assertIs(f.decoder_type.processProtoBuf,dispatcher)
            self.assertEqual(tuple(table.items()),before); self.closed(f)

    def test_real_or_double_sequential_receipts_distinct(self):
        for f in self.fixtures():
            self.dispatch(f); self.dispatch(f)
            self.assertEqual([r.invocation.invocation_number for r in f.wrapper.results],[1,2])

    def test_real_or_double_raw_normalized_same_receipt(self):
        for f in self.fixtures():
            self.dispatch(f)
            self.assertEqual([kind for kind,_ in f.wrapper.trace],['raw','normalized'])
            self.assertIs(f.wrapper.trace[0][1],f.wrapper.trace[1][1]); self.closed(f)

    def test_real_or_double_supplied_zero(self):
        for f in self.fixtures():
            self.dispatch(f,f.message_type(filled='0',remaining='0',avgFillPrice=0.0,orderId=0).SerializeToString())
            result=f.wrapper.results[0].observation
            self.assertEqual(result.filled,D(0)); self.assertEqual(result.order_id,0)
            self.assertEqual(result.presence.filled,source._OrderStatusSourceQuality.SUPPLIED)

    def test_real_or_double_absent_fallbacks(self):
        for f in self.fixtures():
            self.dispatch(f,f.message_type().SerializeToString())
            self.assertEqual(f.wrapper.results[0].observation.presence.wire_supplied,frozenset())

    def test_real_or_double_nested_invocations(self):
        for f in self.fixtures():
            f.wrapper.raw_action=lambda raw,r: self.dispatch(f,self.wire(f,orderId=11)) if r.depth==1 else None
            self.dispatch(f)
            self.assertEqual([r.observation.order_id for r in f.wrapper.results],[11,10]); self.closed(f)

    def test_real_or_double_orphan_from_raw_interruption(self):
        for f in self.fixtures():
            error=KeyboardInterrupt()
            def interrupt(raw,r): raise error
            f.wrapper.raw_action=interrupt
            with self.assertRaises(KeyboardInterrupt) as caught: self.dispatch(f)
            self.assertIs(caught.exception,error); self.closed(f)


if __name__=='__main__': unittest.main()
