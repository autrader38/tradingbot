"""PRE5 adversarial offline lifecycle/dispatch tests; never SDK connect/network."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal as D
import importlib
import importlib.util
from pathlib import Path
import sys
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import weakref

from tradingbot_broker import ibkr_paper_transport as paper
from tradingbot_broker import ibkr_paper_status_delivery as delivery
from tradingbot_broker import ibkr_order_status_pairing as pairing
from tradingbot_broker import ibkr_order_status_evidence as source
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests.test_ibkr_paper_transport import sdk, transport
from tests.test_ibkr_order_status_evidence import Proto, RAW, LEGACY, normalized
from tests.test_paper_dispatch_reservation import ReservationFixture


class Incoming:
    ORDER_STATUS = 37  # Anonymous value only; production discovers SDK identities.


class Decoder:
    def __init__(self, wrapper, serverVersion):
        self.wrapper, self.serverVersion = wrapper, serverVersion
        self.raw_action = self.legacy_action = self.before_parse = None
        self.orphan = self.repeat_raw = self.repeat_normalized = False
        self.override = None
        self.trace = []

    def processProtoBuf(self, payload, msgId):
        self.trace.append(('protobuf', msgId))
        handle = self.msgId2handleInfoProtoBuf.get(msgId)
        if handle is not None:
            return handle.processMeth(self, payload)
        return payload

    def processOrderStatusMsgProtoBuf(self, protobuf):
        if self.before_parse: self.before_parse()
        raw = Proto.FromString(protobuf)
        self.wrapper.orderStatusProtoBuf(raw)
        if self.repeat_raw: self.wrapper.orderStatusProtoBuf(raw)
        if self.raw_action: self.raw_action(raw)
        if self.orphan: return
        values = normalized(raw.values) if self.override is None else self.override
        self.wrapper.orderStatus(*values, '', 0.0)
        if self.repeat_normalized: self.wrapper.orderStatus(*values, '', 0.0)

    def interpret(self, fields, size=None):
        self.trace.append(('legacy', fields[0]))
        handle = self.msgId2handleInfo.get(fields[0])
        if handle is not None:
            return handle.processMeth(self, fields[1:])
        return fields

    def processOrderStatusMsg(self, fields):
        if self.before_parse: self.before_parse()
        if self.legacy_action: self.legacy_action()
        self.wrapper.orderStatus(*fields, '', 0.0)


Decoder.msgId2handleInfoProtoBuf = {37: NS(processMeth=Decoder.processOrderStatusMsgProtoBuf)}
Decoder.msgId2handleInfo = {37: NS(processMeth=Decoder.processOrderStatusMsg)}


def lifecycle_api(decoder_type=Decoder):
    api = sdk()
    base = api.Client
    class LifecycleClient(base):
        def __init__(self, wrapper):
            super().__init__(wrapper)
            self.decoder = None
            self.connected = False
            self.serverVersion_ = 0
            self.connect_action = self.run_action = None
            self.trace = []
        def serverVersion(self): return self.serverVersion_
        def connect(self, *args, **kwargs):
            # Qualified SDK shape; no Connection/socket/handshake operation here.
            self.trace.append('connect-double')
            self.decoder = decoder_type(self.wrapper, self.serverVersion())
            self.trace.append('bootstrap-decoder')
            if self.connect_action: self.connect_action()
            self.serverVersion_ = 200
            self.decoder.serverVersion = self.serverVersion()
            self.connected = True
            self.trace.extend(('reader-queued', 'connectAck', 'connect-return'))
            return 'offline-connect-result'
        def run(self):
            self.trace.append('run-double')
            if self.run_action: return self.run_action()
            return 'offline-run-result'
        def disconnect(self):
            self.trace.append('disconnect-double')
            self.connected = False
            self.wrapper.connectionClosed()
            # reset deliberately preserves decoder, like the qualified SDK.
    api.Client = LifecycleClient
    return api


class Case(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket.connect', 'socket.socket.connect_ex',
                     'socket.create_connection', 'socket.getaddrinfo'):
            self.enterContext(patch(name, side_effect=AssertionError('network forbidden')))

    def build(self, *, connect=True, accept=True, installed=False):
        decoder_type, incoming = Decoder, Incoming
        if installed:
            decoder_type = importlib.import_module('ibapi.decoder').Decoder
            incoming = importlib.import_module('ibapi.message').IN
        api = lifecycle_api(decoder_type)
        with patch.object(paper, '_load_paper_api', return_value=api):
            t = paper.PaperTWSTransport(paper.PaperTWSConfig(paper.PaperTransportIntent.LOCAL_PAPER_FOUNDATION))
        t._begin_generation()
        c = api.clients[-1]
        f = NS(api=api, transport=t, client=c, owner=t._status_delivery_owner,
               decoder_type=decoder_type, incoming=incoming, handoffs=[])
        self.addCleanup(t.disconnect)
        if connect:
            with patch.object(pairing, '_load_pairing_sdk', return_value=(decoder_type, incoming)):
                self.assertEqual(c.connect(), 'offline-connect-result')
            f.decoder, f.registration = c.decoder, t._status_registration
            if accept:
                f.receiver = f.owner._issue_handoff_receiver()
                original = delivery._PaperStatusDelivery._make_handoff
                def published(owner, entry, observation):
                    handoff = original(owner, entry, observation)
                    if owner is f.owner:
                        f.handoffs.append(handoff)
                        owner._accept_handoff(handoff, f.receiver)
                    return handoff
                self.enterContext(patch.object(delivery._PaperStatusDelivery, '_make_handoff', published))
        return f

    def protobuf(self, f, **changes):
        proto = (importlib.import_module('ibapi.decoder').OrderStatusProto
                 if f.decoder_type is not Decoder else Proto)
        payload = proto(**dict(RAW, **changes)).SerializeToString()
        return f.decoder.processProtoBuf(payload, f.incoming.ORDER_STATUS)

    def connect(self, f):
        with patch.object(pairing, '_load_pairing_sdk', return_value=(f.decoder_type, f.incoming)):
            return f.client.connect()

    def legacy(self, f, values=LEGACY):
        if f.decoder_type is Decoder:
            return f.decoder.interpret((37, *values))
        key = f.incoming.ORDER_STATUS
        key = key if type(key) is int else key.value
        tokens = [str(key).encode(), *(str(x).encode() for x in values), b'', b'0']
        return f.decoder.interpret(tokens)

    def closed(self, f):
        self.assertEqual(f.transport._status_fences, [])
        self.assertEqual(f.owner._PaperStatusDelivery__entries, [])
        self.assertEqual(f.owner._PaperStatusDelivery__dispatches, [])
        self.assertEqual(f.transport._callback_sequence, 0)
        self.assertEqual(f.transport._callback_evidence, [])

    def inhibited(self, f):
        self.assertTrue(f.transport._arrival_unusable)
        self.assertTrue(f.transport._reconciliation_required)
        self.assertFalse(f.transport._status_registration_eligible)
        self.closed(f)


class InitialTests(Case):
    def test_protobuf_handoff(self):
        f = self.build(); before = f.transport._arrival_sequence
        self.protobuf(f)
        self.assertEqual(f.transport._arrival_sequence, before + 1)
        self.assertEqual(f.handoffs[0].observation.protocol, source._OrderStatusProtocol.PROTOBUF)
        self.closed(f)


class LifecycleTests(Case):
    def test_decoder_absent_before_connect(self):
        f = self.build(connect=False)
        self.assertIsNone(f.client.decoder)
        self.assertIsNone(f.transport._status_registration)
        self.assertFalse(f.transport._status_registration_eligible)

    def test_final_decoder_version_registered(self):
        f = self.build()
        self.assertEqual(f.decoder.serverVersion, f.client.serverVersion())
        self.assertIs(f.owner._PaperStatusDelivery__decoder, f.decoder)
        self.assertEqual(f.registration.generation, f.transport._generation)
        self.assertEqual(f.client.trace[-1], 'connect-return')

    def test_bootstrap_not_registered(self):
        f = self.build(connect=False)
        def bootstrap():
            self.assertEqual(f.client.decoder.serverVersion, 0)
            self.assertIsNone(f.transport._status_registration)
            self.assertFalse(f.transport._status_registration_eligible)
            self.assertNotIn('run-double', f.client.trace)
        f.client.connect_action = bootstrap
        self.connect(f)

    def test_run_during_connect_inhibits_registration(self):
        f = self.build(connect=False)
        def premature_run():
            with self.assertRaises(ReadOnlyError): f.client.run()
        f.client.connect_action = premature_run
        with self.assertRaises(ReadOnlyError): self.connect(f)
        self.assertNotIn('run-double', f.client.trace)
        self.inhibited(f)

    def test_queued_reader_does_not_dispatch(self):
        f = self.build()
        self.assertIn('reader-queued', f.client.trace)
        self.assertNotIn('run-double', f.client.trace)
        self.assertEqual(f.decoder.trace, [])
        self.closed(f)

    def test_registered_run_delegates_once_and_retires_on_exit(self):
        f = self.build()
        self.assertEqual(f.client.run(), 'offline-run-result')
        self.assertEqual(f.client.trace.count('run-double'), 1)
        self.assertFalse(f.transport._status_registration_eligible)
        with self.assertRaises(ReadOnlyError): f.client.run()

    def test_run_dispatches_registered_status(self):
        f = self.build()
        f.client.run_action = lambda: self.protobuf(f)
        f.client.run()
        self.assertEqual(len(f.handoffs), 1)
        self.closed(f)

    def test_concurrent_run_rejected(self):
        f = self.build()
        entered, release = Event(), Event()
        def running(): entered.set(); self.assertTrue(release.wait(3))
        f.client.run_action = running
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(f.client.run)
            self.assertTrue(entered.wait(3))
            try:
                with self.assertRaises(ReadOnlyError): f.client.run()
            finally: release.set()
            future.result(timeout=3)
        self.assertEqual(f.client.trace.count('run-double'), 1)
        self.inhibited(f)

    def test_same_generation_reconnect_rejected_before_base(self):
        f = self.build()
        with self.assertRaises(ReadOnlyError): self.connect(f)
        self.assertEqual(f.client.trace.count('connect-double'), 1)
        self.inhibited(f)

    def test_disconnect_keeps_old_decoder_but_retires_it(self):
        f = self.build(); original = f.decoder
        f.client.disconnect()
        self.assertIs(f.client.decoder, original)
        self.assertFalse(f.transport._status_registration_eligible)
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.inhibited(f)

    def test_second_generation_gets_distinct_association(self):
        f = self.build(); old = f.decoder
        f.transport._begin_generation()
        c = f.api.clients[-1]
        with patch.object(pairing, '_load_pairing_sdk', return_value=(Decoder, Incoming)):
            c.connect()
        self.assertIsNot(c.decoder, old)
        self.assertIsNot(f.transport._status_registration, f.registration)
        self.assertIsNot(c.decoder._private_order_status_pairing_boundary,
                         old._private_order_status_pairing_boundary)
        self.assertEqual(f.transport._status_registration.generation, f.registration.generation + 1)

    def test_retired_decoder_cannot_announce_in_new_generation(self):
        f = self.build(); f.transport._begin_generation()
        marker = f.transport._arrival_sequence
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.assertEqual(f.transport._arrival_sequence, marker)

    def test_failed_connect_does_not_adopt_previous_decoder(self):
        f = self.build(connect=False)
        def failure(): raise ValueError('untrusted source text')
        f.client.connect_action = failure
        with self.assertRaises(ReadOnlyError) as caught: self.connect(f)
        self.assertNotIn('untrusted source text', str(caught.exception))
        self.inhibited(f)
        with self.assertRaises(ReadOnlyError): f.client.run()

    def test_wrong_final_version_rejected(self):
        f = self.build(connect=False)
        original = f.api.Client.connect
        def changed(client, *args, **kwargs):
            result = original(client, *args, **kwargs)
            client.decoder.serverVersion += 1
            return result
        with patch.object(f.api.Client, 'connect', changed), self.assertRaises(ReadOnlyError):
            self.connect(f)
        self.inhibited(f)

    def test_disconnected_connect_return_rejected(self):
        f = self.build(connect=False)
        original = f.api.Client.connect
        def disconnected(client, *args, **kwargs):
            result = original(client, *args, **kwargs); client.connected = False
            return result
        with patch.object(f.api.Client, 'connect', disconnected), self.assertRaises(ReadOnlyError):
            self.connect(f)
        self.inhibited(f)

    def test_no_global_decoder_or_table_mutation(self):
        original = (Decoder.processProtoBuf, Decoder.interpret,
                    tuple(Decoder.msgId2handleInfo.items()), tuple(Decoder.msgId2handleInfoProtoBuf.items()))
        f = self.build(); self.protobuf(f); self.legacy(f)
        self.assertEqual(original, (Decoder.processProtoBuf, Decoder.interpret,
                    tuple(Decoder.msgId2handleInfo.items()), tuple(Decoder.msgId2handleInfoProtoBuf.items())))
        self.assertIsNot(f.decoder.msgId2handleInfo, Decoder.msgId2handleInfo)

    def test_other_decoder_unaffected(self):
        f = self.build(); other = Decoder(NS(), 200)
        self.assertNotIn('interpret', vars(other))
        self.assertNotIn('processProtoBuf', vars(other))
        self.assertIs(other.msgId2handleInfo, Decoder.msgId2handleInfo)

    def test_post_registration_wrapper_change_rejected(self):
        f = self.build(); f.decoder.wrapper = NS()
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.inhibited(f)


class ProtocolTests(Case):
    def test_arrival_precedes_protobuf_parsing(self):
        f = self.build(); before = f.transport._arrival_sequence
        def before_parse():
            self.assertEqual(f.transport._arrival_sequence, before + 1)
            self.assertEqual(len(f.transport._status_fences), 1)
            self.assertFalse(f.transport._arrival_lock._is_owned())
            self.assertFalse(f.owner._PaperStatusDelivery__guard._is_owned())
        f.decoder.before_parse = before_parse
        self.protobuf(f); self.closed(f)

    def test_arrival_precedes_legacy_decoding(self):
        f = self.build(); before = f.transport._arrival_sequence
        f.decoder.before_parse = lambda: self.assertEqual(f.transport._arrival_sequence, before + 1)
        self.legacy(f); self.closed(f)

    def test_raw_getters_outside_all_capture_guards(self):
        f = self.build(); original = Proto.HasField
        def checked(raw, name):
            self.assertFalse(f.transport._arrival_lock._is_owned())
            self.assertFalse(f.transport._lock._is_owned())
            self.assertFalse(f.owner._PaperStatusDelivery__guard._is_owned())
            self.assertFalse(f.owner._PaperStatusDelivery__pairing._OrderStatusPairingBoundary__guard._is_owned())
            self.assertEqual(len(f.transport._status_fences), 1)
            return original(raw, name)
        with patch.object(Proto, 'HasField', checked): self.protobuf(f)

    def test_other_protobuf_message_unchanged(self):
        f = self.build(); before = f.transport._arrival_sequence
        data = b'local payload'
        self.assertIs(f.decoder.processProtoBuf(data, 999), data)
        self.assertEqual(f.transport._arrival_sequence, before)
        self.closed(f)

    def test_orphan_fails_closed(self):
        f = self.build(); f.decoder.orphan = True
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.inhibited(f)

    def test_duplicate_raw_rejected(self):
        f = self.build(); f.decoder.repeat_raw = True
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.assertEqual(f.handoffs, []); self.inhibited(f)

    def test_duplicate_normalized_rejected(self):
        f = self.build(); f.decoder.repeat_normalized = True
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.assertEqual(len(f.handoffs), 1); self.inhibited(f)

    def test_unknown_status_preserves_exact_pairing(self):
        f = self.build(); self.protobuf(f, status='unrecognized-state')
        self.assertEqual(f.handoffs[0].observation.status, 'UNKNOWN')
        self.assertNotIn('unrecognized-state', repr(f.handoffs))

    def test_unknown_status_mismatch_not_normalized_away(self):
        f = self.build(); f.decoder.override = tuple('another-state' if i == 1 else x for i, x in enumerate(LEGACY))
        with self.assertRaises(ReadOnlyError): self.protobuf(f, status='unrecognized-state')
        self.inhibited(f)

    def test_absent_protobuf_fields_remain_absent(self):
        f = self.build()
        f.decoder.processProtoBuf(Proto().SerializeToString(), 37)
        o = f.handoffs[0].observation
        self.assertIsNone(o.order_id); self.assertIsNone(o.filled)
        self.assertEqual(o.presence.wire_supplied, frozenset())
        self.closed(f)

    def test_supplied_zero_protobuf_identity(self):
        f = self.build(); self.protobuf(f, orderId=0, clientId=0, parentId=0)
        o = f.handoffs[0].observation
        self.assertEqual(o.order_id, 0)
        self.assertIs(o.presence.order_id, source._OrderStatusSourceQuality.SUPPLIED)

    def test_supplied_int32_max_is_not_absence(self):
        f = self.build(); self.protobuf(f, orderId=2147483647)
        self.assertEqual(f.handoffs[0].observation.order_id, 2147483647)
        self.assertEqual(f.transport._lifetime_order_floor, 0)

    def test_perm_id_preserved_without_ownership(self):
        f = self.build(); self.protobuf(f, permId=12345)
        self.assertEqual(f.handoffs[0].observation.perm_id, 12345)
        self.closed(f)

    def test_legacy_zero_stays_ambiguous(self):
        f = self.build(); values = tuple(0.0 if i in (4, 7) else 0 if i in (0, 5, 6, 8) else x
                                       for i, x in enumerate(LEGACY))
        self.legacy(f, values)
        self.assertIs(f.handoffs[0].observation.presence.order_id,
                      source._OrderStatusSourceQuality.LEGACY_AMBIGUOUS)

    def test_repeated_status_has_distinct_delivery_receipts(self):
        f = self.build(); before = f.transport._arrival_sequence
        self.protobuf(f); self.protobuf(f)
        self.assertIsNot(f.handoffs[0].delivery, f.handoffs[1].delivery)
        self.assertEqual(f.transport._arrival_sequence, before + 2)
        self.closed(f)

    def test_legacy_and_protobuf_sources_distinct(self):
        f = self.build(); self.legacy(f); self.protobuf(f)
        a, b = (x.observation for x in f.handoffs)
        self.assertNotEqual(a.protocol, b.protocol)
        self.assertIsNone(a.presence.wire_supplied)
        self.assertIsNotNone(b.presence.wire_supplied)
        self.closed(f)

    def test_nested_protobuf_receipts(self):
        f = self.build(); before = f.transport._arrival_sequence
        def nested(raw):
            f.decoder.raw_action = None
            self.protobuf(f, orderId=19)
        f.decoder.raw_action = nested; self.protobuf(f)
        self.assertEqual(len(f.handoffs), 2)
        self.assertEqual(f.transport._arrival_sequence, before + 2)
        self.assertNotEqual(f.handoffs[0].delivery.number, f.handoffs[1].delivery.number)
        self.closed(f)

    def test_legacy_nested_in_protobuf(self):
        f = self.build(); f.decoder.raw_action = lambda raw: self.legacy(f)
        self.protobuf(f)
        self.assertEqual([h.observation.protocol for h in f.handoffs],
                         [source._OrderStatusProtocol.LEGACY, source._OrderStatusProtocol.PROTOBUF])
        self.closed(f)

    def test_protobuf_nested_in_legacy(self):
        f = self.build(); f.decoder.legacy_action = lambda: self.protobuf(f)
        self.legacy(f)
        self.assertEqual([h.observation.protocol for h in f.handoffs],
                         [source._OrderStatusProtocol.PROTOBUF, source._OrderStatusProtocol.LEGACY])
        self.closed(f)

    def test_nested_legacy_receipts(self):
        f = self.build()
        def nested(): f.decoder.legacy_action = None; self.legacy(f)
        f.decoder.legacy_action = nested
        self.legacy(f); self.assertEqual(len(f.handoffs), 2); self.closed(f)

    def test_unqualified_normalized_without_decoder_rejected(self):
        f = self.build(connect=False)
        with self.assertRaises(ReadOnlyError): f.client.wrapper.orderStatus(*LEGACY, '', 0.0)
        self.inhibited(f)

    def test_unqualified_normalized_with_registered_decoder_rejected(self):
        f = self.build()
        with self.assertRaises(ReadOnlyError): f.client.wrapper.orderStatus(*LEGACY, '', 0.0)
        self.inhibited(f)

    def test_unqualified_raw_rejected_before_getters(self):
        f = self.build()
        class Raw:
            def __getattr__(self, name): raise AssertionError('must not inspect raw')
        with self.assertRaises(ReadOnlyError): f.client.wrapper.orderStatusProtoBuf(Raw())
        self.inhibited(f)

    def test_direct_native_handler_cannot_bypass_boundary(self):
        f = self.build()
        with self.assertRaises(ReadOnlyError): Decoder.processOrderStatusMsg(f.decoder, LEGACY)
        self.inhibited(f)

    def test_original_protobuf_dispatcher_cannot_borrow_outer_scope(self):
        f = self.build()
        f.decoder.raw_action = lambda raw: Decoder.processProtoBuf(f.decoder, Proto(**RAW).SerializeToString(), 37)
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.inhibited(f)


class HandoffTests(Case):
    def test_frozen_slotted_opaque_values(self):
        f = self.build(); self.protobuf(f)
        for value in (f.registration, f.receiver, f.handoffs[0], f.handoffs[0].delivery):
            self.assertFalse(hasattr(value, '__dict__'))
            self.assertEqual(repr(value), '<PrivatePaperStatusValue>')
            with self.assertRaises(FrozenInstanceError): setattr(value, fields(value)[0].name, None)

    def test_reconstructed_receiver_rejected(self):
        f = self.build(accept=False); receiver = f.owner._issue_handoff_receiver()
        original = delivery._PaperStatusDelivery._make_handoff
        def receive(owner, entry, observation):
            h = original(owner, entry, observation)
            owner._accept_handoff(h, replace(receiver)); return h
        with patch.object(delivery._PaperStatusDelivery, '_make_handoff', receive), self.assertRaises(ReadOnlyError):
            self.protobuf(f)
        self.inhibited(f)

    def test_equal_handoff_reconstruction_rejected(self):
        f = self.build(accept=False); receiver = f.owner._issue_handoff_receiver()
        original = delivery._PaperStatusDelivery._make_handoff
        def receive(owner, entry, observation):
            h = original(owner, entry, observation)
            owner._accept_handoff(replace(h), receiver); return h
        with patch.object(delivery._PaperStatusDelivery, '_make_handoff', receive), self.assertRaises(ReadOnlyError):
            self.protobuf(f)
        self.inhibited(f)

    def test_foreign_receiver_rejected(self):
        f = self.build(accept=False); foreign = self.build(accept=False)
        receiver = foreign.owner._issue_handoff_receiver()
        original = delivery._PaperStatusDelivery._make_handoff
        def receive(owner, entry, observation):
            h = original(owner, entry, observation)
            owner._accept_handoff(h, receiver); return h
        with patch.object(delivery._PaperStatusDelivery, '_make_handoff', receive), self.assertRaises(ReadOnlyError):
            self.protobuf(f)
        self.inhibited(f)

    def test_duplicate_acceptance_rejected(self):
        f = self.build(); original = delivery._PaperStatusDelivery._accept_handoff
        def receive(owner, h, receiver): original(owner, h, receiver); original(owner, h, receiver)
        with patch.object(delivery._PaperStatusDelivery, '_accept_handoff', receive), self.assertRaises(ReadOnlyError):
            self.protobuf(f)
        self.inhibited(f)

    def test_receipt_cannot_be_swapped_after_creation(self):
        f = self.build(accept=False); receiver = f.owner._issue_handoff_receiver()
        original = delivery._PaperStatusDelivery._make_handoff
        def receive(owner, entry, observation):
            h = original(owner, entry, observation)
            object.__setattr__(h, 'delivery', replace(h.delivery))
            owner._accept_handoff(h, receiver); return h
        with patch.object(delivery._PaperStatusDelivery, '_make_handoff', receive), self.assertRaises(ReadOnlyError):
            self.protobuf(f)
        self.inhibited(f)

    def test_handoff_reuse_after_exit_rejected(self):
        f = self.build(); self.protobuf(f)
        with self.assertRaises(ReadOnlyError): f.owner._accept_handoff(f.handoffs[0], f.receiver)
        self.inhibited(f)

    def test_receiver_issued_once(self):
        f = self.build()
        with self.assertRaises(ReadOnlyError): f.owner._issue_handoff_receiver()

    def test_acceptance_does_not_clear_fence_before_exit(self):
        f = self.build(); original = delivery._PaperStatusDelivery._accept_handoff
        def receive(owner, h, receiver):
            original(owner, h, receiver)
            self.assertEqual(len(f.transport._status_fences), 1)
        with patch.object(delivery._PaperStatusDelivery, '_accept_handoff', receive): self.protobuf(f)
        self.closed(f)

    def test_temporary_handoff_erased_on_exit(self):
        f = self.build(); retained = []
        original = delivery._PaperStatusDelivery._make_handoff
        def receive(owner, entry, observation):
            retained.append(entry); return original(owner, entry, observation)
        with patch.object(delivery._PaperStatusDelivery, '_make_handoff', receive): self.protobuf(f)
        for name in ('invocation', 'handler', 'handoff', 'observation', 'values'):
            self.assertIsNone(getattr(retained[0], name))

    def test_no_completed_history_accumulation(self):
        f = self.build()
        for _ in range(150): self.protobuf(f)
        self.assertEqual(f.owner._PaperStatusDelivery__counter, 150)
        self.closed(f)

    def test_no_authority_or_callback_sequence_fields(self):
        f = self.build(); self.protobuf(f)
        names = {x.name for x in fields(f.handoffs[0])}
        self.assertTrue(names.isdisjoint({'sequence', 'currentness', 'ownership', 'inventory',
            'authorization', 'account', 'request_id', 'received_at', 'recovery'}))

    def test_registration_constructor_cannot_authorize(self):
        f = self.build()
        with self.assertRaises(ReadOnlyError): f.transport._check_status_registration(f.owner, replace(f.registration))

    def test_early_fence_close_rejected(self):
        f = self.build()
        def attempted(raw):
            receipt = f.owner._PaperStatusDelivery__entries[-1].receipt
            with self.assertRaises(ReadOnlyError): f.transport._status_arrival_finished(f.owner, receipt)
            self.assertEqual(len(f.transport._status_fences), 1)
        f.decoder.raw_action = attempted; self.protobuf(f)
        self.closed(f)


class SafetyTests(Case):
    def test_decoder_replacement_during_delivery(self):
        f = self.build()
        def replacement(): f.client.decoder = Decoder(f.client.wrapper, 200)
        f.decoder.before_parse = replacement
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.assertEqual(f.handoffs, []); self.inhibited(f)

    def test_decimal_representation_tampering_rejected(self):
        f = self.build(accept=False); receiver = f.owner._issue_handoff_receiver()
        original = delivery._PaperStatusDelivery._make_handoff
        def changed(owner, entry, observation):
            h = original(owner, entry, observation)
            object.__setattr__(observation, 'filled', D('-0'))
            owner._accept_handoff(h, receiver); return h
        with patch.object(delivery._PaperStatusDelivery, '_make_handoff', changed), self.assertRaises(ReadOnlyError):
            self.protobuf(f, filled='0')
        self.inhibited(f)

    def test_sdk_parser_exception_is_sanitized(self):
        f = self.build()
        def failure(): raise ValueError('untrusted parser payload')
        f.decoder.before_parse = failure
        with self.assertRaises(ReadOnlyError) as caught: self.protobuf(f)
        self.assertNotIn('untrusted parser payload', str(caught.exception))
        self.inhibited(f)

    def test_primary_protobuf_interrupt_survives_cleanup_interrupt(self):
        f = self.build(); primary = KeyboardInterrupt('primary'); secondary = SystemExit('secondary')
        def interrupted(raw): raise primary
        original = f.transport._status_arrival_finished
        def cleanup(*args): original(*args); raise secondary
        f.decoder.raw_action = interrupted
        with patch.object(f.transport, '_status_arrival_finished', cleanup), self.assertRaises(KeyboardInterrupt) as caught:
            self.protobuf(f)
        self.assertIs(caught.exception, primary); self.inhibited(f)

    def test_primary_legacy_interrupt_survives_cleanup_interrupt(self):
        f = self.build(); primary = GeneratorExit('primary'); secondary = SystemExit('secondary')
        def interrupted(): raise primary
        original = f.transport._status_arrival_finished
        def cleanup(*args): original(*args); raise secondary
        f.decoder.legacy_action = interrupted
        with patch.object(f.transport, '_status_arrival_finished', cleanup), self.assertRaises(GeneratorExit) as caught:
            self.legacy(f)
        self.assertIs(caught.exception, primary); self.inhibited(f)

    def test_no_floor_adoption_from_status_handoff(self):
        f = self.build(); floor = f.transport._lifetime_order_floor
        self.protobuf(f, orderId=200000)
        self.assertEqual(f.transport._lifetime_order_floor, floor)

    def test_counter_exhaustion_fails_closed(self):
        f = self.build()
        marker = f.transport._arrival_sequence
        f.owner._set('counter', delivery._MAX_NUMBER)
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.assertEqual(f.transport._arrival_sequence, marker + 1)
        self.inhibited(f)

    def test_nesting_overflow_fails_closed_without_accumulation(self):
        f = self.build(); f.decoder.raw_action = lambda raw: self.protobuf(f)
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.inhibited(f)

    def test_foreign_concurrent_delivery_fails_before_parser(self):
        f = self.build(); entered, release = Event(), Event()
        def pause(): entered.set(); self.assertTrue(release.wait(3))
        f.decoder.before_parse = pause
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.protobuf, f)
            self.assertTrue(entered.wait(3))
            try:
                with self.assertRaises(ReadOnlyError): self.protobuf(f)
            finally: release.set()
            with self.assertRaises(ReadOnlyError): future.result(timeout=3)
        self.inhibited(f)

    def test_arrival_not_blocked_by_write_bookkeeping(self):
        f = self.build(); entered = Event()
        f.decoder.before_parse = entered.set
        with f.transport._lock, ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.protobuf, f)
            self.assertTrue(entered.wait(3))
            future.result(timeout=3)
        self.closed(f)

    def test_disconnect_during_delivery_latches_before_fence_removed(self):
        f = self.build()
        def disconnect(raw):
            f.client.disconnect()
            self.assertTrue(f.transport._reconciliation_required)
            self.assertTrue(f.transport._arrival_unusable)
        f.decoder.raw_action = disconnect
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.inhibited(f)

    def test_new_generation_does_not_clear_permanent_latch(self):
        f = self.build(accept=False)
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        f.transport._begin_generation()
        self.assertTrue(f.transport._reconciliation_required)
        self.assertEqual(f.transport._status_fences, [])

    def test_paging_unchanged_by_handoffs(self):
        f = self.build()
        before = (tuple(f.transport._callback_evidence), tuple(f.transport._callback_run_ends),
                  f.transport._callback_sequence, f.transport._last_callback_identity)
        self.protobuf(f); self.legacy(f)
        self.assertEqual(before, (tuple(f.transport._callback_evidence), tuple(f.transport._callback_run_ends),
                  f.transport._callback_sequence, f.transport._last_callback_identity))

    def test_error_repr_does_not_include_source_text(self):
        f = self.build(); f.decoder.override = tuple('private-source-text' if i == 1 else x for i,x in enumerate(LEGACY))
        with self.assertRaises(ReadOnlyError) as caught: self.protobuf(f)
        self.assertNotIn('private-source-text', str(caught.exception))
        self.assertNotIn('private-source-text', repr(f.owner))

    def test_callbacks_do_not_call_transport_old_status_path(self):
        f = self.build()
        with patch.object(f.transport, '_callback', side_effect=AssertionError('no old status publication')):
            self.protobuf(f); self.legacy(f)
        self.closed(f)

    def test_public_view_has_no_lifecycle_or_decoder(self):
        f = self.build()
        for name in ('connect', 'run', 'decoder', 'registration', 'pairing', 'delivery', 'client'):
            self.assertFalse(hasattr(f.transport._client, name))

    def test_account_mode_remains_unknown(self):
        f = self.build(); self.protobuf(f)
        self.assertIs(f.transport.account_mode, AccountMode.UNKNOWN)

    def test_no_frame_or_raw_retained_in_entries(self):
        f = self.build(); weak = []
        original = Proto.FromString.__func__
        def parsed(cls, payload):
            raw = original(cls, payload); weak.append(weakref.ref(raw)); return raw
        with patch.object(Proto, 'FromString', classmethod(parsed)): self.protobuf(f)
        self.assertIsNone(weak[0]())
        self.closed(f)

    def test_private_module_has_no_external_side_effect_imports(self):
        text = Path(delivery.__file__).read_text()
        for forbidden in ('sqlite3', '_SQLiteJournalStore', 'paper_dispatch_journal',
                          'paper_reconciliation', 'datetime.now', 'socket', 'requests', 'open('):
            self.assertNotIn(forbidden, text)
        import tradingbot_broker
        self.assertFalse(hasattr(tradingbot_broker, '_StatusHandoff'))

    def test_callback_thread_never_calls_sqlite(self):
        import sqlite3
        f = self.build()
        with patch.object(sqlite3, 'connect', side_effect=AssertionError('no callback connection')):
            with ThreadPoolExecutor(1) as pool:
                pool.submit(self.protobuf, f).result(timeout=3)
        self.closed(f)

    def test_no_journal_owner_calls(self):
        from tradingbot_broker.paper_dispatch_journal import _PaperDispatchJournalOwner
        f = self.build()
        with patch.object(_PaperDispatchJournalOwner, '_callback_snapshot', side_effect=AssertionError('no journal owner')):
            self.protobuf(f); self.legacy(f)
        self.closed(f)

    def test_legacy_handoff(self):
        f = self.build(); before = f.transport._arrival_sequence
        self.legacy(f)
        self.assertEqual(f.transport._arrival_sequence, before + 1)
        self.assertEqual(f.handoffs[0].observation.protocol, source._OrderStatusProtocol.LEGACY)
        self.closed(f)

    def test_no_production_receiver(self):
        f = self.build(accept=False)
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.inhibited(f)

    def test_no_legacy_receiver(self):
        f = self.build(accept=False)
        with self.assertRaises(ReadOnlyError): self.legacy(f)
        self.inhibited(f)

    def test_run_before_connect(self):
        f = self.build(connect=False)
        with self.assertRaises(ReadOnlyError): f.client.run()
        self.assertNotIn('run-double', f.client.trace)
        self.inhibited(f)

    def test_decoder_replacement(self):
        f = self.build()
        with self.assertRaises(ReadOnlyError): f.client.decoder = Decoder(f.client.wrapper, 200)
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.inhibited(f)

    def test_unrelated_normalized_reentry(self):
        f = self.build()
        f.decoder.raw_action = lambda raw: f.client.wrapper.orderStatus(*LEGACY, '', 0.0)
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.assertEqual(f.handoffs, [])
        self.inhibited(f)

    def test_non_status_legacy_unchanged(self):
        f = self.build(); marker = f.transport._arrival_sequence
        fields = (999, object())
        self.assertIs(f.decoder.interpret(fields), fields)
        self.assertEqual(f.transport._arrival_sequence, marker)
        self.closed(f)


class MalformedTests(Case):
    pass


def _malformed_protobuf(name, changes):
    def test(self):
        f = self.build(); before = f.transport._arrival_sequence
        # Inject malformed decoder output at the real raw-callback seam; the
        # valid wire encoder must not be asked to encode impossible primitives.
        raw = Proto(**dict(RAW, **changes))
        with patch.object(Proto, 'FromString', return_value=raw), self.assertRaises(ReadOnlyError):
            self.protobuf(f)
        self.assertEqual(f.transport._arrival_sequence, before + 1)
        self.inhibited(f)
    setattr(MalformedTests, 'test_protobuf_' + name, test)


for _name, _changes in (
    ('negative_filled', {'filled': '-1'}), ('nan_remaining', {'remaining': 'NaN'}),
    ('infinite_price', {'avgFillPrice': float('inf')}), ('nan_last_price', {'lastFillPrice': float('nan')}),
    ('negative_price', {'avgFillPrice': -1.0}), ('bool_order_id', {'orderId': True}),
    ('bool_client_id', {'clientId': True}), ('negative_order_id', {'orderId': -1}),
    ('oversized_perm_id', {'permId': 1 << 63}), ('quantity_wrong_type', {'filled': 1.25}),
    ('quantity_coefficient_bounds', {'remaining': '1' * 65}), ('quantity_exponent_bounds', {'filled': '1e65'}),
    ('status_wrong_type', {'status': 42}), ('status_non_ascii', {'status': 'bad\N{SNOWMAN}'}),
):
    _malformed_protobuf(_name, _changes)


class TamperingTests(Case):
    pass


def _tamper_test(name, tamper):
    def test(self):
        f = self.build()
        with tamper(f), self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.inhibited(f)
    setattr(TamperingTests, 'test_' + name, test)


for _name, _tamper in (
    ('decoder_version', lambda f: patch.object(f.decoder, 'serverVersion', 201)),
    ('bool_decoder_version', lambda f: patch.object(f.decoder, 'serverVersion', True)),
    ('instance_interpret_replacement', lambda f: patch.object(f.decoder, 'interpret', lambda *a: None)),
    ('private_instance_marker', lambda f: patch.dict(vars(f.decoder), {delivery._MARKER: object()})),
    ('shadow_table_replacement', lambda f: patch.object(f.decoder, 'msgId2handleInfo', {})),
    ('global_legacy_handler', lambda f: patch.object(Decoder, 'processOrderStatusMsg', lambda *a: None)),
    ('global_protobuf_dispatcher', lambda f: patch.object(Decoder, 'processProtoBuf', lambda *a: None)),
    ('original_legacy_handle', lambda f: patch.object(Decoder.msgId2handleInfo[37], 'processMeth', lambda *a: None)),
    ('generation_change', lambda f: patch.object(f.transport, '_callback_generation', f.transport._generation + 1)),
    ('wrapper_normalized_method', lambda f: patch.object(type(f.client.wrapper), 'orderStatus', lambda *a: None)),
):
    _tamper_test(_name, _tamper)


class InterruptionTests(Case):
    def entry_return_interrupted(self, exception_type):
        f = self.build(); error = exception_type('notification return interruption')
        code = delivery._PaperStatusDelivery._proto_enter.__code__
        previous = sys.gettrace()
        def trace(frame, event, arg):
            if frame.f_code is code and event == 'return': raise error
            return trace
        try:
            sys.settrace(trace)
            with self.assertRaises(exception_type) as caught: self.protobuf(f)
        finally:
            sys.settrace(previous)
        self.assertIs(caught.exception, error); self.inhibited(f)

    def interrupted(self, exception_type, seam):
        error = exception_type('private interruption')
        f = self.build(connect=seam != 'registration')
        method = {
            'registration': '_install', 'entry': '_push', 'handoff': '_make_handoff',
            'acceptance': '_accept_handoff',
        }.get(seam)
        if method:
            original = getattr(delivery._PaperStatusDelivery, method)
            def interrupt(owner, *args, **kwargs):
                if seam in ('entry', 'acceptance'): original(owner, *args, **kwargs)
                raise error
            context = patch.object(delivery._PaperStatusDelivery, method, interrupt)
        elif seam == 'raw':
            context = patch.object(pairing, '_snapshot', side_effect=error)
        elif seam == 'normalized':
            original = source._adapt_protobuf_order_status
            def interrupt(raw, *, normalized=None):
                if normalized is not None: raise error
                return original(raw)
            context = patch.object(source, '_adapt_protobuf_order_status', interrupt)
        else:
            name = '_status_arrival_finished' if seam == 'exit' else '_status_registration_retired'
            original = getattr(f.transport, name)
            def interrupt(*args, **kwargs): original(*args, **kwargs); raise error
            context = patch.object(f.transport, name, interrupt)
        with context, self.assertRaises(exception_type) as caught:
            if seam == 'registration': self.connect(f)
            elif seam == 'retirement': f.client.disconnect()
            else: self.protobuf(f)
        self.assertIs(caught.exception, error)
        self.inhibited(f)


for _exception in (KeyboardInterrupt, SystemExit, GeneratorExit):
    def _return_test(self, exception_type=_exception): self.entry_return_interrupted(exception_type)
    setattr(InterruptionTests, 'test_' + _exception.__name__ + '_entry_return', _return_test)
    for _seam in ('registration', 'entry', 'raw', 'normalized', 'handoff', 'acceptance', 'exit', 'retirement'):
        def _test(self, exception_type=_exception, seam=_seam): self.interrupted(exception_type, seam)
        setattr(InterruptionTests, 'test_' + _exception.__name__ + '_' + _seam, _test)


class ReservationTests(ReservationFixture, Case):
    def build_registered(self):
        ReservationFixture.build(self)
        api = lifecycle_api()
        w = self.write
        w._create_client = lambda gen: paper._make_paper_client(api, w, gen)
        w._begin_generation()
        c = api.clients[-1]
        with patch.object(pairing, '_load_pairing_sdk', return_value=(Decoder, Incoming)): c.connect()
        c.wrapper.nextValidId(10); w._synchronize_open_orders()
        owner = w._status_delivery_owner
        receiver = owner._issue_handoff_receiver()
        f = NS(client=c, decoder=c.decoder, transport=w, owner=owner,
               decoder_type=Decoder, incoming=Incoming)
        original = delivery._PaperStatusDelivery._make_handoff
        def receive(current, entry, observation):
            h = original(current, entry, observation)
            if current is owner: current._accept_handoff(h, receiver)
            return h
        self.enterContext(patch.object(delivery._PaperStatusDelivery, '_make_handoff', receive))
        return f

    def test_existing_reservation_invalidated_before_parser(self):
        f = self.build_registered(); w = f.transport
        with w._lock, w._arrival_lock:
            reservation = w._issue_reservation(w._generation, (17, 'BUY', D(10), 'MKT', 'DAY', 'entry-reference'))
            w._validate_reservation(self.coordinator, reservation)
        def observed():
            with w._lock, w._arrival_lock, self.assertRaises(ReadOnlyError):
                w._validate_reservation(self.coordinator, reservation)
        f.decoder.before_parse = observed
        Case.protobuf(self, f)
        self.assertGreater(w._lifetime_order_floor, reservation.order_id)
        self.assertFalse(any(call[0] == 'place' for call in f.client.trace if type(call) is tuple))

    def test_new_reservation_cannot_validate_while_fence_active(self):
        f = self.build_registered(); w = f.transport
        def observed():
            with w._lock, w._arrival_lock:
                r = w._issue_reservation(w._generation, (17, 'BUY', D(10), 'MKT', 'DAY', 'entry-reference'))
                # Snapshot marker is already current; only the NEW fence check stops it.
                self.assertEqual(r.arrival_marker, w._arrival_sequence)
                with self.assertRaises(ReadOnlyError): w._validate_reservation(self.coordinator, r)
                self.assertGreater(w._lifetime_order_floor, r.order_id)
        f.decoder.before_parse = observed
        Case.protobuf(self, f)


@unittest.skipUnless(importlib.util.find_spec('ibapi') is not None, 'optional installed ibapi unavailable; Windows required')
class InstalledSDKTests(Case):
    def test_real_decoder_protobuf_handoff_no_client_connect(self):
        f = self.build(installed=True); self.protobuf(f); self.closed(f)
        self.assertEqual(len(f.handoffs), 1)

    def test_real_decoder_legacy_handoff_no_client_connect(self):
        f = self.build(installed=True); self.legacy(f); self.closed(f)
        self.assertEqual(len(f.handoffs), 1)

    def test_real_decoder_unconsumed_handoff_fails_closed(self):
        f = self.build(installed=True, accept=False)
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.inhibited(f)
