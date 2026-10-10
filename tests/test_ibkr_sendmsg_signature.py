"""Two-argument official SDK call shape; offline doubles, no real sockets."""

from enum import Enum
import struct
import unittest

from tests.readonly_fixtures import transport
from tests.test_ibkr_out_constants import enum_sdk, READ_IDS, WRITE_IDS, frame
from tradingbot_broker.readonly_models import ReadOnlyError


START_PAYLOAD = '2\x001\x00\x00'


def sdk_10502(*, binary_ids=False):
    api = enum_sdk()
    old_client = api.Client

    class Client(old_client):
        def sendMsg(self, msgId, msg):
            api.calls.append(('sdk-send-args', msgId, msg))
            opcode = msgId.value if isinstance(msgId, Enum) else msgId
            body = ((struct.pack('!I', opcode) + msg.encode()) if binary_ids
                    else (str(opcode) + '\0' + msg).encode())
            return self.conn.sendMsg(frame(body))

        def startApi(self):
            self.sendMsg(api.OUT.START_API, START_PAYLOAD)

        def connect(self, *args):
            # A real server responds with nextValidId only after START_API.
            ready = self.wrapper.nextValidId
            self.wrapper.nextValidId = lambda order_id: None
            try:
                super().connect(*args)
            finally:
                self.wrapper.nextValidId = ready
            try:
                self.startApi()
            except Exception:
                # Mirrors the reported official SDK startApi error-550 boundary.
                self.wrapper.error(-1, 550, 'anonymous startup failure')
                return
            ready(7)

    def bind(method, message_name):
        def request(self, *args):
            self.sendMsg(getattr(api.OUT, message_name), '1\0')
            return getattr(old_client, method)(self, *args)
        return request

    for method, message_name in (
        ('reqManagedAccts', 'REQ_MANAGED_ACCTS'),
        ('reqAccountSummary', 'REQ_ACCOUNT_SUMMARY'),
        ('reqPositions', 'REQ_POSITIONS'),
        ('reqAllOpenOrders', 'REQ_ALL_OPEN_ORDERS'),
        ('reqCompletedOrders', 'REQ_COMPLETED_ORDERS'),
        ('reqExecutions', 'REQ_EXECUTIONS'),
        ('reqCurrentTime', 'REQ_CURRENT_TIME'),
    ):
        setattr(Client, method, bind(method, message_name))
    api.Client = Client
    return api


class TwoArgumentSendTests(unittest.TestCase):
    def setUp(self):
        self.t, self.api, _ = transport(sdk_10502())
        self.snapshot = self.t.connect()
        self.client = self.api.clients[-1]
        self.addCleanup(self.t.disconnect)

    def blocked_before_sdk(self, msgId, payload='1\0'):
        before = tuple(self.api.calls)
        with self.assertRaisesRegex(ReadOnlyError, '^READ_ONLY_BROKER_TRANSPORT$'):
            self.client.sendMsg(msgId, payload)
        self.assertEqual(tuple(self.api.calls), before)

    def test_start_api_two_argument_path_avoids_error_550(self):
        self.assertTrue(self.t.connected)
        self.assertNotIn(('BROKER_ERROR', 550), self.t.diagnostics)
        self.assertIn(('sdk-send-args', self.api.OUT.START_API, START_PAYLOAD), self.api.calls)

    def test_start_api_is_existing_approved_opcode_71(self):
        self.assertEqual(self.t._api.OUT.START_API, 71)
        self.assertIn(('socket-send', frame(b'71\0' + START_PAYLOAD.encode())), self.api.calls)

    def test_superclass_receives_original_enum_and_payload(self):
        member = self.api.OUT.REQ_CURRENT_TIME
        self.client.sendMsg(member, '1\0')
        self.assertIs(self.api.calls[-2][1], member)
        self.assertEqual(self.api.calls[-2], ('sdk-send-args', member, '1\0'))

    def test_superclass_receives_correct_two_integer_arguments(self):
        self.client.sendMsg(49, '1\0')
        self.assertEqual(self.api.calls[-2], ('sdk-send-args', 49, '1\0'))
        self.assertIs(type(self.api.calls[-2][1]), int)

    def test_sdk_framed_bytes_reach_guarded_sink(self):
        self.client.sendMsg(self.api.OUT.REQ_CURRENT_TIME, '1\0')
        self.assertEqual(self.api.calls[-1], ('socket-send', b'\0\0\0\x0549\x001\x00'))

    def test_all_ten_approved_enum_ids_pass(self):
        for name, opcode in READ_IDS.items():
            with self.subTest(name=name):
                self.client.sendMsg(getattr(self.api.OUT, name), '1\0')
                self.assertEqual(self.api.calls[-1], ('socket-send', frame(f'{opcode}\0'.encode() + b'1\0')))

    def test_unapproved_official_enum_rejected_before_sdk(self):
        for name in WRITE_IDS:
            with self.subTest(name=name): self.blocked_before_sdk(getattr(self.api.OUT, name))

    def test_unapproved_plain_integer_rejected_before_sdk(self):
        for opcode in (*WRITE_IDS.values(), 0, -1, 99999):
            with self.subTest(opcode=opcode): self.blocked_before_sdk(opcode)

    def test_other_enum_with_same_numeric_id_rejected(self):
        self.blocked_before_sdk(Enum('Other', {'REQ_CURRENT_TIME': 49}).REQ_CURRENT_TIME)

    def test_arbitrary_int_coercion_is_not_called(self):
        calls = []
        class Coercible:
            def __int__(self): calls.append('coerced'); return 49
        self.blocked_before_sdk(Coercible())
        self.assertEqual(calls, [])

    def test_enum_like_value_attribute_rejected(self):
        class EnumLike:
            value = 49
            name = 'REQ_CURRENT_TIME'
        self.blocked_before_sdk(EnumLike())

    def test_extra_payload_argument_fails_closed(self):
        before = tuple(self.api.calls)
        with self.assertRaises(ReadOnlyError): self.client.sendMsg(self.api.OUT.START_API, '1\0', 'extra')
        self.assertEqual(tuple(self.api.calls), before)

    def test_base_sdk_sends_cannot_bypass_socket_guard(self):
        for opcode in WRITE_IDS.values():
            with self.subTest(opcode=opcode):
                before = [call for call in self.api.calls if call[0] == 'socket-send']
                with self.assertRaises(ReadOnlyError): self.api.Client.sendMsg(self.client, opcode, 'forbidden\0')
                self.assertEqual([call for call in self.api.calls if call[0] == 'socket-send'], before)

    def test_raw_connection_sends_cannot_bypass(self):
        before = tuple(self.api.calls)
        with self.assertRaises(ReadOnlyError):
            self.api.Connection.sendMsg(self.client.conn, frame(b'3\0forbidden'))
        self.assertEqual(tuple(self.api.calls), before)

    def test_raw_socket_sends_cannot_bypass(self):
        before = tuple(self.api.calls)
        with self.assertRaises(ReadOnlyError): self.client.conn.socket.send(frame(b'4\0forbidden'))
        self.assertEqual(tuple(self.api.calls), before)

    def test_malformed_framed_bytes_rejected(self):
        before = tuple(self.api.calls)
        with self.assertRaises(ReadOnlyError): self.client.conn.sendMsg(b'49\0unframed')
        self.assertEqual(tuple(self.api.calls), before)

    def test_place_order_remains_blocked(self):
        before = tuple(self.api.calls)
        with self.assertRaises(ReadOnlyError): self.client.placeOrder(1)
        self.assertEqual(tuple(self.api.calls), before)

    def test_cancel_order_remains_blocked(self):
        before = tuple(self.api.calls)
        with self.assertRaises(ReadOnlyError): self.client.cancelOrder(1)
        self.assertEqual(tuple(self.api.calls), before)

    def test_global_cancel_remains_blocked(self):
        before = tuple(self.api.calls)
        with self.assertRaises(ReadOnlyError): self.client.reqGlobalCancel()
        self.assertEqual(tuple(self.api.calls), before)

    def test_protobuf_remains_blocked(self):
        before = tuple(self.api.calls)
        with self.assertRaisesRegex(ReadOnlyError, '^UNSUPPORTED_IBAPI_WIRE_ENCODING$'):
            self.client.sendMsgProtoBuf(49, b'unsupported')
        self.assertEqual(tuple(self.api.calls), before)

    def test_wrong_opcode_from_sdk_framer_cannot_reach_sink(self):
        original = self.api.Client.sendMsg
        def bad_framer(client, msgId, msg): return original(client, 3, msg)
        self.api.Client.sendMsg = bad_framer
        before = [call for call in self.api.calls if call[0] == 'socket-send']
        with self.assertRaises(ReadOnlyError): self.client.sendMsg(self.api.OUT.REQ_CURRENT_TIME, '1\0')
        self.assertEqual([call for call in self.api.calls if call[0] == 'socket-send'], before)


def bad_id_test(value):
    def test(self): self.blocked_before_sdk(value)
    return test


for label, value in (('bool_true', True), ('bool_false', False), ('float', 49.0),
                     ('string', '49'), ('object', object()), ('none', None)):
    setattr(TwoArgumentSendTests, f'test_bad_msgid_{label}', bad_id_test(value))


def bad_payload_test(value):
    def test(self): self.blocked_before_sdk(self.api.OUT.REQ_CURRENT_TIME, value)
    return test


class StringSubclass(str): pass


for label, value in (('bytes', b'1\0'), ('bool', True), ('integer', 1),
                     ('none', None), ('object', object()), ('subclass', StringSubclass('1\0'))):
    setattr(TwoArgumentSendTests, f'test_bad_payload_{label}', bad_payload_test(value))


class LegacyAndEncodingTests(unittest.TestCase):
    def test_legacy_one_argument_read_still_works(self):
        t, api, _ = transport(enum_sdk())
        t.connect(); self.addCleanup(t.disconnect)
        api.clients[-1].sendMsg('49\0read')
        self.assertEqual(api.calls[-2], ('socket-send', frame(b'49\0read')))

    def test_legacy_one_argument_forbidden_write_rejected(self):
        t, api, _ = transport(enum_sdk())
        t.connect(); self.addCleanup(t.disconnect)
        before = tuple(api.calls)
        with self.assertRaises(ReadOnlyError): api.clients[-1].sendMsg('3\0forbidden')
        self.assertEqual(tuple(api.calls), before)

    def test_raw_message_id_startup_passes_socket_guard(self):
        api = sdk_10502(binary_ids=True)
        t, _, _ = transport(api)
        t.connect(); self.addCleanup(t.disconnect)
        self.assertTrue(t.connected)
        self.assertIn(('socket-send', b'\x00\x00\x00\x09\x00\x00\x00G2\x001\x00\x00'), api.calls)
        self.assertNotIn(('BROKER_ERROR', 550), t.diagnostics)


if __name__ == '__main__':
    unittest.main()
