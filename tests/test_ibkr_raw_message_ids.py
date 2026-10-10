"""Official 10.50.2 raw-ID bytes; all outbound sinks are in-memory doubles."""

import struct
import unittest

from tests.readonly_fixtures import transport
from tests.test_ibkr_out_constants import READ_IDS, WRITE_IDS, frame
from tests.test_ibkr_sendmsg_signature import sdk_10502
from tradingbot_broker.ibkr_readonly_wire import guarded_socket
from tradingbot_broker.readonly_models import ReadOnlyError


# Exact packets supplied by inspection of the official SDK, not helper-generated.
RAW_START_API = b'\x00\x00\x00\x09\x00\x00\x00G2\x001\x00\x00'
ASCII_START_API = b'\x00\x00\x00\x0871\x002\x001\x00\x00'


class Sink:
    def __init__(self): self.sent = []
    def send(self, data):
        self.sent.append(bytes(data))
        return len(data)
    def sendall(self, data): self.send(data)


def raw_frame(opcode, payload=b'1\0'):
    # Mirrors official comm.make_msg's four-byte unsigned big-endian ID.
    return frame(opcode.to_bytes(4, 'big') + payload)


class DualWireTests(unittest.TestCase):
    def setUp(self):
        self.sink = Sink()
        self.view = guarded_socket(self.sink, Sink, frozenset(READ_IDS.values()))

    def blocked(self, packet):
        before = tuple(self.sink.sent)
        with self.assertRaises(ReadOnlyError): self.view.send(packet)
        self.assertEqual(tuple(self.sink.sent), before)

    def test_exact_official_raw_start_api_packet(self):
        self.assertEqual(self.view.send(RAW_START_API), 13)
        self.assertEqual(self.sink.sent, [RAW_START_API])

    def test_exact_official_legacy_start_api_packet(self):
        self.assertEqual(self.view.send(ASCII_START_API), 12)
        self.assertEqual(self.sink.sent, [ASCII_START_API])

    def test_all_ten_approved_opcodes_in_both_encodings(self):
        self.assertEqual(len(READ_IDS), 10)
        for name, opcode in READ_IDS.items():
            for packet in (raw_frame(opcode), frame(f'{opcode}\0'.encode() + b'1\0')):
                with self.subTest(name=name, packet=packet):
                    self.assertEqual(self.view.send(packet), len(packet))
                    self.assertEqual(self.sink.sent[-1], packet)

    def test_raw_unknown_ids_rejected(self):
        for opcode in (1, 72, 99999, 0xffffffff):
            with self.subTest(opcode=opcode): self.blocked(raw_frame(opcode))

    def test_raw_place_order_rejected(self):
        self.blocked(b'\x00\x00\x00\x06\x00\x00\x00\x031\x00')

    def test_raw_cancel_order_rejected(self):
        self.blocked(b'\x00\x00\x00\x06\x00\x00\x00\x041\x00')

    def test_raw_global_cancel_rejected(self):
        self.blocked(b'\x00\x00\x00\x06\x00\x00\x00:1\x00')

    def test_raw_zero_rejected(self):
        self.blocked(raw_frame(0))

    def test_truncated_raw_id_rejected(self):
        for body in (b'', b'\0', b'\0\0', b'\0\0\0'):
            with self.subTest(body=body): self.blocked(frame(body))

    def test_four_byte_id_requires_no_fabricated_payload(self):
        packet = raw_frame(49, b'')
        self.view.send(packet)
        self.assertEqual(self.sink.sent, [packet])

    def test_raw_payload_is_opaque_not_an_additional_header(self):
        packet = raw_frame(49, b'opaque\xffpayload')
        self.view.send(packet)
        self.assertEqual(self.sink.sent, [packet])

    def test_malformed_length_rejected_in_both_styles(self):
        for packet in (RAW_START_API, ASCII_START_API):
            for declared in (0, 1, len(packet) - 5, len(packet) - 3, 65537):
                with self.subTest(packet=packet, declared=declared):
                    self.blocked(struct.pack('!I', declared) + packet[4:])

    def test_truncated_length_prefix_rejected(self):
        for packet in (b'', b'\0', b'\0\0', b'\0\0\0'):
            with self.subTest(packet=packet): self.blocked(packet)

    def test_oversize_frame_rejected(self):
        self.blocked(raw_frame(49, b'x' * 65536))

    def test_ascii_garbage_and_missing_terminator_rejected(self):
        for body in (b'71', b'71x\0', b'+71\0', b' 71\0', b'garbage\0', b'3\0'):
            with self.subTest(body=body): self.blocked(frame(body))

    def test_mixed_malformed_id_headers_rejected(self):
        for body in (b'7G\0\0', b'71G\0', b'\0\0G71\0', b'\0\0\x0171\0'):
            with self.subTest(body=body): self.blocked(frame(body))

    def test_arbitrary_binary_prefix_rejected(self):
        for body in (b'\xff\0\0G1\0', b'\x01\0\0G1\0', b'\0\0\x01G1\0'):
            with self.subTest(body=body): self.blocked(frame(body))

    def test_combined_frames_rejected(self):
        self.blocked(RAW_START_API + raw_frame(3))
        self.blocked(ASCII_START_API + raw_frame(4))

    def test_sendall_obeys_same_allowlist(self):
        self.view.sendall(RAW_START_API)
        self.assertEqual(self.sink.sent, [RAW_START_API])
        with self.assertRaises(ReadOnlyError): self.view.sendall(raw_frame(3))
        self.assertEqual(self.sink.sent, [RAW_START_API])

    def test_pending_raw_send_requires_exact_suffix(self):
        class PartialSink(Sink):
            def send(self, data):
                super().send(data)
                return 5 if len(self.sent) == 1 else len(data)
        sink = PartialSink()
        view = guarded_socket(sink, PartialSink, frozenset(READ_IDS.values()))
        self.assertEqual(view.send(RAW_START_API), 5)
        for packet in (raw_frame(3), RAW_START_API, RAW_START_API[5:] + b'x'):
            with self.subTest(packet=packet):
                with self.assertRaises(ReadOnlyError): view.send(packet)
        self.assertEqual(sink.sent, [RAW_START_API])
        self.assertEqual(view.send(RAW_START_API[5:]), 8)
        view.send(ASCII_START_API)
        self.assertEqual(sink.sent, [RAW_START_API, RAW_START_API[5:], ASCII_START_API])

    def test_pending_raw_sendall_accepts_only_exact_suffix(self):
        class PartialSink(Sink):
            def send(self, data):
                super().send(data)
                return 2
            def sendall(self, data): self.sent.append(bytes(data))
        sink = PartialSink()
        view = guarded_socket(sink, PartialSink, frozenset(READ_IDS.values()))
        view.send(RAW_START_API)
        with self.assertRaises(ReadOnlyError): view.sendall(raw_frame(58))
        self.assertEqual(sink.sent, [RAW_START_API])
        view.sendall(RAW_START_API[2:])
        self.assertEqual(sink.sent, [RAW_START_API, RAW_START_API[2:]])

    def test_exact_handshake_unchanged_and_single_use(self):
        handshake = b'API\x00\x00\x00\x00\tv100..226'
        self.view.send(handshake)
        self.view.send(RAW_START_API)
        self.assertEqual(self.sink.sent, [handshake, RAW_START_API])
        self.blocked(handshake)

    def test_handshake_cannot_hide_raw_order_id(self):
        self.blocked(b'API\0' + raw_frame(3))


class RawSDKBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.t, self.api, _ = transport(sdk_10502(binary_ids=True))
        self.snapshot = self.t.connect()
        self.client = self.api.clients[-1]
        self.addCleanup(self.t.disconnect)

    def sink_calls(self): return tuple(call for call in self.api.calls if call[0] == 'socket-send')

    def test_real_shaped_sdk_collects_after_exact_raw_start_api(self):
        self.assertTrue(self.t.connected)
        self.assertIn(('socket-send', RAW_START_API), self.api.calls)
        self.assertNotIn(('BROKER_ERROR', 550), self.t.diagnostics)
        self.assertEqual(str(self.snapshot.account.cash), '10000.01')

    def test_all_named_read_ids_pass_sdk_and_socket(self):
        for name, opcode in READ_IDS.items():
            with self.subTest(name=name):
                self.client.sendMsg(getattr(self.api.OUT, name), '1\0')
                self.assertEqual(self.sink_calls()[-1], ('socket-send', raw_frame(opcode)))

    def test_unapproved_id_rejected_before_superclass_and_sink(self):
        before = tuple(self.api.calls)
        for opcode in (*WRITE_IDS.values(), 0, 99999):
            with self.subTest(opcode=opcode):
                with self.assertRaises(ReadOnlyError): self.client.sendMsg(opcode, '1\0')
        self.assertEqual(tuple(self.api.calls), before)

    def test_base_sdk_send_raw_write_cannot_bypass(self):
        before = self.sink_calls()
        for opcode in WRITE_IDS.values():
            with self.subTest(opcode=opcode):
                with self.assertRaises(ReadOnlyError): self.api.Client.sendMsg(self.client, opcode, '1\0')
        self.assertEqual(self.sink_calls(), before)

    def test_raw_connection_and_socket_write_paths_blocked(self):
        before = self.sink_calls()
        for opcode in WRITE_IDS.values():
            packet = raw_frame(opcode)
            for operation in (
                lambda: self.client.conn.sendMsg(packet),
                lambda: self.api.Connection.sendMsg(self.client.conn, packet),
                lambda: self.client.conn.socket.send(packet),
                lambda: self.client.conn.socket.sendall(packet),
            ):
                with self.subTest(opcode=opcode, operation=operation):
                    with self.assertRaises(ReadOnlyError): operation()
        self.assertEqual(self.sink_calls(), before)

    def test_order_methods_blocked_with_raw_sdk(self):
        before = tuple(self.api.calls)
        for method in ('placeOrder', 'cancelOrder', 'reqGlobalCancel'):
            with self.subTest(method=method):
                with self.assertRaises(ReadOnlyError): getattr(self.client, method)()
        self.assertEqual(tuple(self.api.calls), before)

    def test_bad_payload_rejected_before_superclass(self):
        before = tuple(self.api.calls)
        with self.assertRaises(ReadOnlyError): self.client.sendMsg(self.api.OUT.START_API, b'2\0')
        self.assertEqual(tuple(self.api.calls), before)

    def test_unexpected_raw_order_from_sdk_framer_blocked(self):
        original = self.api.Client.sendMsg
        self.api.Client.sendMsg = lambda client, msgId, msg: original(client, 3, msg)
        before = self.sink_calls()
        with self.assertRaises(ReadOnlyError): self.client.sendMsg(self.api.OUT.START_API, '2\0')
        self.assertEqual(self.sink_calls(), before)

    def test_protobuf_still_blocked_with_raw_sdk(self):
        before = tuple(self.api.calls)
        with self.assertRaisesRegex(ReadOnlyError, '^UNSUPPORTED_IBAPI_WIRE_ENCODING$'):
            self.client.sendMsgProtoBuf(self.api.OUT.START_API, b'unsupported')
        self.assertEqual(tuple(self.api.calls), before)


if __name__ == '__main__':
    unittest.main()
