"""Qualified 10.50.2 read protobuf contract; anonymous offline SDK/sinks only."""

from enum import Enum, IntEnum
from types import SimpleNamespace
import struct
import unittest
from unittest.mock import patch

from tests.readonly_fixtures import transport
from tests.test_ibkr_out_constants import enum_sdk, READ_IDS, WRITE_IDS, frame
from tests.test_ibkr_raw_message_ids import Sink
from tradingbot_broker.ibkr_readonly import (
    _load_official_api, _protobuf_read_opcodes, _READ_MESSAGES)
from tradingbot_broker.ibkr_readonly_wire import guarded_socket
from tradingbot_broker.readonly_models import ReadOnlyError


# Independent expectations from the supplied official SDK qualification.
PROTO_READS = {
    'START_API': 271, 'REQ_MANAGED_ACCTS': 217, 'REQ_ACCOUNT_SUMMARY': 262,
    'CANCEL_ACCOUNT_SUMMARY': 263, 'REQ_POSITIONS': 261, 'CANCEL_POSITIONS': 264,
    'REQ_ALL_OPEN_ORDERS': 216, 'REQ_COMPLETED_ORDERS': 299,
    'REQ_EXECUTIONS': 207, 'REQ_CURRENT_TIME': 249,
}
PROTO_WRITES = (203, 204, 258)
START_BYTES = b'\x08\x02\x10\x01'  # Opaque anonymous fixture payload, not real account data.


def proto_frame(opcode, payload=b''):
    return frame(opcode.to_bytes(4, 'big') + payload)


def protobuf_sdk():
    api = enum_sdk()
    base = api.Client

    class NumericOUT(Enum):
        def __add__(self, offset): return self.value + offset

    api.OUT = NumericOUT('OUT', {**READ_IDS, **WRITE_IDS, 'OTHER_READ': 50})
    api.PROTOBUF_MSG_ID = 200

    class Client(base):
        def sendMsg(self, msgId, msg):
            opcode = msgId.value if isinstance(msgId, Enum) else msgId
            api.calls.append(('sdk-legacy-send', msgId, msg))
            return self.conn.sendMsg(proto_frame(opcode, msg.encode()))

        def sendMsgProtoBuf(self, msgId, msg):
            api.calls.append(('sdk-protobuf-send', msgId, msg))
            # Exactly the supplied official comm.make_msg_proto framing contract.
            return self.conn.sendMsg(proto_frame(msgId, msg))

        def startApi(self): self.startApiProtoBuf()
        def startApiProtoBuf(self):
            self.sendMsgProtoBuf(api.OUT.START_API + api.PROTOBUF_MSG_ID, START_BYTES)

        def connect(self, *args):
            ready = self.wrapper.nextValidId
            self.wrapper.nextValidId = lambda order_id: None
            try:
                super().connect(*args)
            finally:
                self.wrapper.nextValidId = ready
            try:
                self.startApi()
            except Exception:
                self.wrapper.error(-1, 550, 'anonymous startup failure')
                return
            ready(7)

    def bind(method, name):
        helper = method + 'ProtoBuf'
        def proto(self, *args):
            self.sendMsgProtoBuf(getattr(api.OUT, name) + api.PROTOBUF_MSG_ID, b'\x08\x01')
        def request(self, *args):
            getattr(self, helper)(*args)
            if hasattr(base, method): return getattr(base, method)(self, *args)
        setattr(Client, helper, proto)
        setattr(Client, method, request)

    for method, name in (
        ('reqManagedAccts', 'REQ_MANAGED_ACCTS'),
        ('reqAccountSummary', 'REQ_ACCOUNT_SUMMARY'),
        ('cancelAccountSummary', 'CANCEL_ACCOUNT_SUMMARY'),
        ('reqPositions', 'REQ_POSITIONS'), ('cancelPositions', 'CANCEL_POSITIONS'),
        ('reqAllOpenOrders', 'REQ_ALL_OPEN_ORDERS'),
        ('reqCompletedOrders', 'REQ_COMPLETED_ORDERS'),
        ('reqExecutions', 'REQ_EXECUTIONS'), ('reqCurrentTime', 'REQ_CURRENT_TIME'),
    ): bind(method, name)
    api.Client = Client
    return api


class QualificationTests(unittest.TestCase):
    def test_exact_named_protobuf_mapping_and_integer_types(self):
        api = protobuf_sdk()
        actual = _protobuf_read_opcodes(api.OUT, api.PROTOBUF_MSG_ID)
        self.assertEqual(actual, PROTO_READS)
        self.assertEqual(set(actual.values()), {207, 216, 217, 249, 261, 262, 263, 264, 271, 299})
        self.assertEqual(set(actual), set(_READ_MESSAGES))
        self.assertTrue(all(type(v) is int and v > 0 for v in actual.values()))

    def test_missing_offset_does_not_guess_200(self):
        self.assertEqual(_protobuf_read_opcodes(protobuf_sdk().OUT, None), {})

    def test_nonapproved_out_names_never_generate_permissions(self):
        api = protobuf_sdk()
        self.assertIn('OTHER_READ', api.OUT.__members__)
        self.assertNotIn(250, _protobuf_read_opcodes(api.OUT, 200).values())
        self.assertTrue(set(PROTO_WRITES).isdisjoint(_protobuf_read_opcodes(api.OUT, 200).values()))

    def test_unqualified_base_values_fail_before_connection(self):
        api = protobuf_sdk()
        values = {**READ_IDS, 'START_API': 3}
        api.OUT = Enum('OUT', values)
        with self.assertRaisesRegex(ReadOnlyError, '^UNSUPPORTED_IBAPI_WIRE_ENCODING$'): transport(api)
        self.assertEqual(api.clients, [])
        self.assertEqual(api.calls, [])

    def test_missing_qualified_name_fails_closed(self):
        api = protobuf_sdk()
        api.OUT = Enum('OUT', {k: v for k, v in READ_IDS.items() if k != 'REQ_COMPLETED_ORDERS'})
        with self.assertRaises(ReadOnlyError): transport(api)
        self.assertEqual(api.calls, [])

    def test_offset_int_coercion_not_called(self):
        calls = []
        class Coercible:
            def __int__(self): calls.append('int'); return 200
        with self.assertRaises(ReadOnlyError): _protobuf_read_opcodes(protobuf_sdk().OUT, Coercible())
        self.assertEqual(calls, [])

    def test_official_loader_uses_client_module_constant(self):
        api = protobuf_sdk()
        modules = {
            'ibapi.client': SimpleNamespace(EClient=api.Client, PROTOBUF_MSG_ID=200),
            'ibapi.wrapper': SimpleNamespace(EWrapper=api.Wrapper),
            'ibapi.execution': SimpleNamespace(ExecutionFilter=api.ExecutionFilter),
            'ibapi.message': SimpleNamespace(OUT=api.OUT),
            'ibapi.connection': SimpleNamespace(Connection=api.Connection),
            'ibapi.server_versions': SimpleNamespace(MIN_SERVER_VER_COMPLETED_ORDERS=150),
        }
        with patch('tradingbot_broker.ibkr_readonly.importlib.import_module', side_effect=modules.__getitem__):
            self.assertEqual(_load_official_api().PROTOBUF_MSG_ID, 200)


def invalid_offset_test(value):
    def test(self):
        api = protobuf_sdk()
        api.PROTOBUF_MSG_ID = value
        with self.assertRaisesRegex(ReadOnlyError, '^UNSUPPORTED_IBAPI_WIRE_ENCODING$'): transport(api)
        self.assertEqual(api.clients, [])
        self.assertEqual(api.calls, [])
    return test


for label, value in (('bool_true', True), ('bool_false', False), ('float', 200.0),
                     ('string', '200'), ('zero', 0), ('negative', -200),
                     ('other_offset', 201), ('object', object())):
    setattr(QualificationTests, f'test_bad_offset_{label}', invalid_offset_test(value))


class ProtobufClientTests(unittest.TestCase):
    def setUp(self):
        self.t, self.api, _ = transport(protobuf_sdk())
        self.snapshot = self.t.connect()
        self.client = self.api.clients[-1]  # Raw SDK belongs only to the offline fixture.
        self.addCleanup(self.t.disconnect)

    def blocked_before_sdk(self, opcode, payload=b''):
        before = tuple(self.api.calls)
        with self.assertRaisesRegex(ReadOnlyError, '^UNSUPPORTED_IBAPI_WIRE_ENCODING$'):
            self.client.sendMsgProtoBuf(opcode, payload)
        self.assertEqual(tuple(self.api.calls), before)

    def sink_calls(self): return tuple(c for c in self.api.calls if c[0] == 'socket-send')

    def test_protobuf_start_api_271_reaches_guarded_sink(self):
        self.assertIn(('sdk-protobuf-send', 271, START_BYTES), self.api.calls)
        self.assertIn(('socket-send', b'\x00\x00\x00\x08\x00\x00\x01\x0f\x08\x02\x10\x01'), self.api.calls)
        self.assertNotIn(('BROKER_ERROR', 550), self.t.diagnostics)
        self.assertTrue(self.t.connected)

    def test_protobuf_read_collection_completes_offline(self):
        self.assertEqual(str(self.snapshot.account.cash), '10000.01')
        self.assertEqual(len(self.snapshot.working_orders), 1)
        self.assertEqual(len(self.snapshot.fills), 1)
        self.assertFalse(any(c[0] == 'sdk-legacy-send' for c in self.api.calls))

    def test_all_ten_ids_delegate_exact_two_arguments(self):
        for opcode in PROTO_READS.values():
            with self.subTest(opcode=opcode):
                payload = b'\x08\x01'
                self.client.sendMsgProtoBuf(opcode, payload)
                self.assertEqual(self.api.calls[-2], ('sdk-protobuf-send', opcode, payload))
                self.assertEqual(self.api.calls[-1], ('socket-send', proto_frame(opcode, payload)))
                self.assertIs(self.api.calls[-2][2], payload)

    def test_all_ten_official_style_helpers_pass(self):
        for method, opcode in (
            ('startApiProtoBuf', 271), ('reqManagedAcctsProtoBuf', 217),
            ('reqAccountSummaryProtoBuf', 262), ('cancelAccountSummaryProtoBuf', 263),
            ('reqPositionsProtoBuf', 261), ('cancelPositionsProtoBuf', 264),
            ('reqAllOpenOrdersProtoBuf', 216), ('reqCompletedOrdersProtoBuf', 299),
            ('reqExecutionsProtoBuf', 207), ('reqCurrentTimeProtoBuf', 249),
        ):
            with self.subTest(method=method):
                getattr(self.client, method)()
                self.assertEqual(self.api.calls[-2][0:2], ('sdk-protobuf-send', opcode))

    def test_empty_bytes_payload_passes_exact_sdk_frame(self):
        self.client.sendMsgProtoBuf(271, b'')
        self.assertEqual(self.api.calls[-1], ('socket-send', b'\x00\x00\x00\x04\x00\x00\x01\x0f'))

    def test_place_order_203_blocked_at_client(self): self.blocked_before_sdk(203)
    def test_cancel_order_204_blocked_at_client(self): self.blocked_before_sdk(204)
    def test_global_cancel_258_blocked_at_client(self): self.blocked_before_sdk(258)
    def test_nonapproved_out_plus_200_blocked(self): self.blocked_before_sdk(self.api.OUT.OTHER_READ + 200)

    def test_no_generic_range_or_offset_permission(self):
        for opcode in range(400):
            if opcode not in PROTO_READS.values():
                with self.subTest(opcode=opcode): self.blocked_before_sdk(opcode)
        self.blocked_before_sdk(0xffffffff)
        self.blocked_before_sdk(10**30)

    def test_int_coercion_not_called(self):
        calls = []
        class Coercible:
            def __int__(self): calls.append('int'); return 271
        self.blocked_before_sdk(Coercible())
        self.assertEqual(calls, [])

    def test_bytes_coercion_not_called(self):
        calls = []
        class Coercible:
            def __bytes__(self): calls.append('bytes'); return b''
        self.blocked_before_sdk(271, Coercible())
        self.assertEqual(calls, [])

    def test_legacy_sender_cannot_use_protobuf_id(self):
        before = tuple(self.api.calls)
        with self.assertRaises(ReadOnlyError): self.client.sendMsg(271, '1\0')
        self.assertEqual(tuple(self.api.calls), before)

    def test_base_protobuf_sender_write_cannot_bypass_socket(self):
        before = self.sink_calls()
        for opcode in (*PROTO_WRITES, 250, 99999):
            with self.subTest(opcode=opcode):
                with self.assertRaises(ReadOnlyError): self.api.Client.sendMsgProtoBuf(self.client, opcode, b'')
        self.assertEqual(self.sink_calls(), before)

    def test_raw_base_connection_and_socket_writes_blocked(self):
        before = self.sink_calls()
        for opcode in (*PROTO_WRITES, *WRITE_IDS.values()):
            packet = proto_frame(opcode)
            for operation in (
                lambda: self.client.conn.sendMsg(packet),
                lambda: self.api.Connection.sendMsg(self.client.conn, packet),
                lambda: self.client.conn.socket.send(packet),
                lambda: self.client.conn.socket.sendall(packet),
            ):
                with self.subTest(opcode=opcode):
                    with self.assertRaises(ReadOnlyError): operation()
        self.assertEqual(self.sink_calls(), before)

    def test_wrong_sdk_framing_opcode_still_blocked(self):
        original = self.api.Client.sendMsgProtoBuf
        self.api.Client.sendMsgProtoBuf = lambda client, msgId, msg: original(client, 203, msg)
        before = self.sink_calls()
        with self.assertRaises(ReadOnlyError): self.client.sendMsgProtoBuf(271, b'')
        self.assertEqual(self.sink_calls(), before)

    def test_application_views_expose_only_numeric_metadata(self):
        self.assertEqual(vars(self.t._api.OUT), READ_IDS)
        self.assertEqual(vars(self.t._api.PROTOBUF_OUT), PROTO_READS)
        for name in ('Client', 'Connection', 'SocketType'): self.assertFalse(hasattr(self.t._api, name))
        for name in ('conn', 'wrapper', 'sendMsgProtoBuf', 'sendMsg', '__dict__'):
            self.assertFalse(hasattr(self.t._client, name))
        for name in ('fileno', 'detach', 'dup', '__dict__'):
            self.assertFalse(hasattr(self.client.conn.socket, name))

    def test_metadata_mutation_does_not_grant_write_permission(self):
        self.t._api.PROTOBUF_OUT.PLACE_ORDER = 203
        self.blocked_before_sdk(203)
        before = self.sink_calls()
        with self.assertRaises(ReadOnlyError): self.client.conn.socket.send(proto_frame(203))
        self.assertEqual(self.sink_calls(), before)

    def test_order_entry_points_remain_blocked(self):
        before = tuple(self.api.calls)
        for method in ('placeOrder', 'cancelOrder', 'reqGlobalCancel'):
            with self.subTest(method=method):
                with self.assertRaises(ReadOnlyError): getattr(self.client, method)()
        self.assertEqual(tuple(self.api.calls), before)


def bad_id_test(value):
    def test(self): self.blocked_before_sdk(value)
    return test


class IntSubclass(int): pass
class BytesSubclass(bytes): pass


for label, value in (('bool', True), ('float', 271.0), ('str', '271'), ('none', None),
                     ('enum', Enum('Other', {'START_API': 271}).START_API),
                     ('int_enum', IntEnum('OtherInt', {'START_API': 271}).START_API),
                     ('int_subclass', IntSubclass(271)), ('object', object()),
                     ('bytearray', bytearray()), ('memoryview', memoryview(b''))):
    setattr(ProtobufClientTests, f'test_bad_id_{label}', bad_id_test(value))


def bad_payload_test(value):
    def test(self): self.blocked_before_sdk(271, value)
    return test


for label, value in (('bool', True), ('float', 1.0), ('int', 1), ('str', ''), ('none', None),
                     ('bytearray', bytearray()), ('memoryview', memoryview(b'')),
                     ('bytes_subclass', BytesSubclass()), ('object', object())):
    setattr(ProtobufClientTests, f'test_bad_payload_{label}', bad_payload_test(value))


class ProtobufSocketTests(unittest.TestCase):
    def setUp(self):
        api = protobuf_sdk()
        self.sink = Sink()
        self.view = guarded_socket(self.sink, Sink, frozenset(READ_IDS.values()),
                                   frozenset(_protobuf_read_opcodes(api.OUT, 200).values()))

    def blocked(self, packet):
        before = tuple(self.sink.sent)
        with self.assertRaises(ReadOnlyError): self.view.send(packet)
        self.assertEqual(tuple(self.sink.sent), before)

    def test_all_ten_protobuf_ids_pass_final_boundary(self):
        for opcode in PROTO_READS.values():
            with self.subTest(opcode=opcode):
                packet = proto_frame(opcode, b'\x08\x01')
                self.view.send(packet)
                self.assertEqual(self.sink.sent[-1], packet)

    def test_place_order_203_blocked_at_socket(self): self.blocked(proto_frame(203))
    def test_cancel_order_204_blocked_at_socket(self): self.blocked(proto_frame(204))
    def test_global_cancel_258_blocked_at_socket(self): self.blocked(proto_frame(258))

    def test_opaque_payload_does_not_expand_permissions(self):
        packet = proto_frame(271, b'\x00\xffopaque')
        self.view.send(packet)
        self.assertEqual(self.sink.sent, [packet])
        self.blocked(proto_frame(203, b'\x00\xffopaque'))

    def test_no_generic_high_id_acceptance(self):
        for opcode in range(200, 400):
            if opcode not in PROTO_READS.values():
                with self.subTest(opcode=opcode): self.blocked(proto_frame(opcode))
        self.blocked(proto_frame(0))
        self.blocked(proto_frame(0xffffffff))

    def test_truncated_raw_id_fails(self):
        for body in (b'', b'\0', b'\0\0', b'\0\0\x01'):
            with self.subTest(body=body): self.blocked(frame(body))

    def test_malformed_length_fails(self):
        packet = proto_frame(271, START_BYTES)
        for declared in (0, 3, 7, 9, 65537):
            with self.subTest(declared=declared): self.blocked(struct.pack('!I', declared) + packet[4:])

    def test_ascii_protobuf_ids_never_authorized(self):
        for opcode in PROTO_READS.values():
            with self.subTest(opcode=opcode): self.blocked(frame(f'{opcode}\0'.encode()))

    def test_all_legacy_ascii_and_raw_reads_preserved(self):
        for opcode in READ_IDS.values():
            for packet in (frame(f'{opcode}\0'.encode()), proto_frame(opcode, b'1\0')):
                with self.subTest(opcode=opcode, packet=packet):
                    self.view.send(packet)
                    self.assertEqual(self.sink.sent[-1], packet)

    def test_legacy_write_ids_remain_blocked(self):
        for opcode in WRITE_IDS.values():
            with self.subTest(opcode=opcode):
                self.blocked(frame(f'{opcode}\0'.encode()))
                self.blocked(proto_frame(opcode))

    def test_handshake_unchanged(self):
        handshake = b'API\x00\x00\x00\x00\tv100..226'
        self.view.send(handshake)
        self.view.send(proto_frame(271))
        self.assertEqual(self.sink.sent, [handshake, proto_frame(271)])
        self.blocked(handshake)

    def test_partial_send_only_identical_protobuf_suffix(self):
        class PartialSink(Sink):
            def send(self, data):
                super().send(data)
                return 6 if len(self.sent) == 1 else len(data)
        sink = PartialSink()
        view = guarded_socket(sink, PartialSink, frozenset(READ_IDS.values()), frozenset(PROTO_READS.values()))
        packet = proto_frame(271, START_BYTES)
        self.assertEqual(view.send(packet), 6)
        for bad in (proto_frame(203), packet[6:] + b'x', packet):
            with self.subTest(bad=bad):
                with self.assertRaises(ReadOnlyError): view.send(bad)
        self.assertEqual(sink.sent, [packet])
        view.send(packet[6:])
        self.assertEqual(sink.sent, [packet, packet[6:]])

    def test_combined_frames_cannot_be_spliced(self):
        self.blocked(proto_frame(271) + proto_frame(203))
        self.blocked(proto_frame(271) + proto_frame(217))

    def test_absent_sdk_qualification_keeps_protobuf_blocked(self):
        sink = Sink()
        view = guarded_socket(sink, Sink, frozenset(READ_IDS.values()))
        with self.assertRaises(ReadOnlyError): view.send(proto_frame(271))
        self.assertEqual(sink.sent, [])


if __name__ == '__main__':
    unittest.main()
