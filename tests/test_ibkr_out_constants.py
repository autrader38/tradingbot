"""Official 10.50.2 OUT enum shape, exercised entirely with offline SDK doubles."""

from decimal import Decimal
from enum import Enum, IntEnum
from fractions import Fraction
from types import SimpleNamespace
import struct
import unittest

from tests.readonly_fixtures import sdk, transport
from tradingbot_broker.ibkr_readonly import _READ_MESSAGES, _read_opcodes
from tradingbot_broker.readonly_models import ReadOnlyError


# Values independently supplied by inspection of the official local SDK.
READ_IDS = {
    'START_API': 71, 'REQ_MANAGED_ACCTS': 17, 'REQ_ACCOUNT_SUMMARY': 62,
    'CANCEL_ACCOUNT_SUMMARY': 63, 'REQ_POSITIONS': 61, 'CANCEL_POSITIONS': 64,
    'REQ_ALL_OPEN_ORDERS': 16, 'REQ_EXECUTIONS': 7, 'REQ_CURRENT_TIME': 49,
    'REQ_COMPLETED_ORDERS': 99,
}
WRITE_IDS = {'PLACE_ORDER': 3, 'CANCEL_ORDER': 4, 'REQ_GLOBAL_CANCEL': 58}


def enum_sdk():
    api = sdk()
    api.OUT = Enum('OUT', {**READ_IDS, **WRITE_IDS})
    return api


def frame(body):
    return struct.pack('!I', len(body)) + body


class OUTCompatibilityTests(unittest.TestCase):
    def test_official_enum_members_normalize_to_exact_ints(self):
        out = enum_sdk().OUT
        self.assertIsNot(type(out.START_API), int)
        normalized = _read_opcodes(out)
        self.assertEqual(normalized, READ_IDS)
        self.assertTrue(all(type(v) is int for v in normalized.values()))

    def test_plain_int_sdk_fixture_compatibility(self):
        api = sdk()
        expected = {name: getattr(api.OUT, name) for name in _READ_MESSAGES}
        self.assertEqual(_read_opcodes(api.OUT), expected)
        t, _, _ = transport(api)
        self.addCleanup(t.disconnect)
        self.assertEqual(vars(t._api.OUT), expected)
        self.assertEqual(t.connect().account.cash, Decimal('10000.01'))

    def test_int_enum_members_also_have_valid_exact_int_values(self):
        self.assertEqual(_read_opcodes(IntEnum('OUT', READ_IDS)), READ_IDS)

    def test_enum_sdk_complete_read_collection_still_works(self):
        t, _, _ = transport(enum_sdk())
        self.addCleanup(t.disconnect)
        snapshot = t.connect()
        self.assertEqual(snapshot.account.cash, Decimal('10000.01'))
        self.assertEqual(len(snapshot.account.positions), 1)
        self.assertEqual(len(snapshot.working_orders), 1)
        self.assertEqual(len(snapshot.fills), 1)
        self.assertTrue(snapshot.completed_orders_available)

    def test_transport_metadata_contains_numeric_values_only(self):
        t, _, _ = transport(enum_sdk())
        self.assertEqual(vars(t._api.OUT), READ_IDS)
        self.assertFalse(hasattr(t._api, 'Client'))
        self.assertTrue(all(type(v) is int for v in vars(t._api.OUT).values()))

    def test_unapproved_order_members_cannot_enter_allowlist(self):
        normalized = _read_opcodes(enum_sdk().OUT)
        self.assertEqual(set(normalized), set(_READ_MESSAGES))
        self.assertTrue(set(normalized.values()).isdisjoint(WRITE_IDS.values()))

    def test_arbitrary_int_coercion_not_invoked(self):
        calls = []
        class Coercible:
            def __int__(self):
                calls.append('coerced')
                return 49
        with self.assertRaisesRegex(ReadOnlyError, '^UNSUPPORTED_IBAPI_WIRE_ENCODING$'):
            _read_opcodes(SimpleNamespace(REQ_CURRENT_TIME=Coercible()))
        self.assertEqual(calls, [])

    def test_arbitrary_value_attribute_is_not_enum_evidence(self):
        with self.assertRaises(ReadOnlyError):
            _read_opcodes(SimpleNamespace(REQ_CURRENT_TIME=SimpleNamespace(value=49)))

    def test_unrelated_enum_member_in_namespace_rejected(self):
        unrelated = Enum('Other', {'REQ_CURRENT_TIME': 49})
        with self.assertRaises(ReadOnlyError):
            _read_opcodes(SimpleNamespace(REQ_CURRENT_TIME=unrelated.REQ_CURRENT_TIME))

    def test_enum_alias_with_wrong_declared_name_rejected(self):
        out = Enum('OUT', {'OTHER_MESSAGE': 49, 'REQ_CURRENT_TIME': 49})
        with self.assertRaises(ReadOnlyError): _read_opcodes(out)

    def test_all_approved_enum_opcodes_pass_framed_socket_guard(self):
        t, api, _ = transport(enum_sdk())
        t.connect()
        self.addCleanup(t.disconnect)
        client = api.clients[-1]
        for name, value in READ_IDS.items():
            with self.subTest(name=name):
                body = f'{value}\0read'.encode()
                api.Client.sendMsg(client, body.decode())
                self.assertEqual(api.calls[-2], ('socket-send', frame(body)))

    def test_forbidden_numeric_base_and_raw_sends_never_reach_sink(self):
        t, api, _ = transport(enum_sdk())
        t.connect()
        self.addCleanup(t.disconnect)
        client = api.clients[-1]
        before = tuple(api.calls)
        for value in (*WRITE_IDS.values(), 99999):
            with self.subTest(opcode=value):
                with self.assertRaises(ReadOnlyError):
                    api.Client.sendMsg(client, f'{value}\0forbidden')
                with self.assertRaises(ReadOnlyError):
                    api.Connection.sendMsg(client.conn, frame(f'{value}\0forbidden'.encode()))
                with self.assertRaises(ReadOnlyError):
                    client.conn.socket.send(frame(f'{value}\0forbidden'.encode()))
                self.assertEqual(tuple(api.calls), before)

    def test_place_cancel_global_cancel_remain_blocked_with_enum_sdk(self):
        t, api, _ = transport(enum_sdk())
        t.connect()
        self.addCleanup(t.disconnect)
        client = api.clients[-1]
        before = tuple(api.calls)
        for method in ('placeOrder', 'cancelOrder', 'reqGlobalCancel'):
            with self.subTest(method=method):
                with self.assertRaises(ReadOnlyError): getattr(client, method)()
                with self.assertRaises(ReadOnlyError): getattr(api.Client, method)(client)
                self.assertEqual(tuple(api.calls), before)

    def test_protobuf_remains_blocked_with_enum_sdk(self):
        t, api, _ = transport(enum_sdk())
        t.connect()
        self.addCleanup(t.disconnect)
        before = tuple(api.calls)
        with self.assertRaisesRegex(ReadOnlyError, '^UNSUPPORTED_IBAPI_WIRE_ENCODING$'):
            api.clients[-1].sendMsgProtoBuf(49, b'unsupported')
        self.assertEqual(tuple(api.calls), before)

    def test_exact_official_initial_handshake_remains_accepted(self):
        t, api, _ = transport(enum_sdk())
        t.connect()
        self.addCleanup(t.disconnect)
        handshake = b'API\x00\x00\x00\x00\tv100..226'
        self.assertEqual(handshake, b'API\0' + frame(b'v100..226'))
        api.clients[-1].conn.socket.send(handshake)
        self.assertEqual(api.calls[-1], ('socket-send', handshake))


def invalid_enum_test(value):
    def test(self):
        api = sdk()
        api.OUT = Enum('OUT', {**READ_IDS, 'REQ_CURRENT_TIME': value})
        with self.assertRaisesRegex(ReadOnlyError, '^UNSUPPORTED_IBAPI_WIRE_ENCODING$'):
            transport(api)
        self.assertEqual(api.clients, [])
        self.assertEqual(api.calls, [])
    return test


for label, value in (
    ('string', '49'), ('float', 49.0), ('fractional_float', 49.1),
    ('bool_true', True), ('bool_false', False), ('zero', 0), ('negative', -49),
    ('decimal', Decimal('49')), ('fraction', Fraction(49)), ('none', None),
    ('object', object()),
):
    setattr(OUTCompatibilityTests, f'test_reject_enum_value_{label}', invalid_enum_test(value))


if __name__ == '__main__':
    unittest.main()
