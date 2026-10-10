"""10.50.2-shaped offline SDK only. No network/session/login/order requests."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace as NS
import inspect
import struct
import unittest
from unittest.mock import patch
from threading import Event

from tradingbot_broker.ibkr_paper_transport import (
    PaperTWSConfig, PaperTransportIntent, PaperTWSTransport, _qualify,
    _StockMarketSpec, _DispatchState, _Operation, _DispatchResult, _Reason,
    MIN_ORDER_ID, MAX_ORDER_ID)
from tradingbot_broker.ibkr_paper_wire import (
    _guarded_socket, _LEGACY_POLICY, _PROTOBUF_POLICY)
from tradingbot_broker.ibkr_readonly import _read_opcodes, _protobuf_read_opcodes
from tradingbot_broker.ibkr_readonly_wire import guarded_socket
from tradingbot_broker.models import Side, TradingMode
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests.test_ibkr_protobuf_reads import protobuf_sdk
from tests.readonly_fixtures import transport as read_transport
from tests.test_paper_execution_authorization import AuthorizationFixture
from tradingbot_broker.paper_execution import PaperExecutionScope
from tradingbot_broker.broker import BrokerOperationError
from tradingbot_broker.models import BrokerReason
from tests.broker_fixtures import request, permission


READS = {'START_API': 71, 'REQ_MANAGED_ACCTS': 17, 'REQ_ACCOUNT_SUMMARY': 62,
         'CANCEL_ACCOUNT_SUMMARY': 63, 'REQ_POSITIONS': 61, 'CANCEL_POSITIONS': 64,
         'REQ_ALL_OPEN_ORDERS': 16, 'REQ_COMPLETED_ORDERS': 99,
         'REQ_EXECUTIONS': 7, 'REQ_CURRENT_TIME': 49}
WRITES = {'PLACE_ORDER': 3, 'CANCEL_ORDER': 4, 'REQ_GLOBAL_CANCEL': 58}
LEGACY = {3, 4, 7, 16, 17, 49, 58, 61, 62, 63, 64, 71, 99}
PROTO = {203, 204, 207, 216, 217, 249, 258, 261, 262, 263, 264, 271, 299}
SPEC = _StockMarketSpec(17, Side.BUY, Decimal('1.25'))


def frame(body): return struct.pack('!I', len(body)) + body
def wire(opcode, encoding='raw', payload=b'\x08\x01'):
    return frame((str(opcode).encode() + b'\0' if encoding == 'ascii'
                  else opcode.to_bytes(4, 'big')) + payload)


class Sink:
    def __init__(self): self.sent = []; self.limit = None
    def send(self, data):
        count = len(data) if self.limit is None else min(self.limit, len(data))
        self.sent.append(data[:count]); return count
    def sendall(self, data): self.sent.append(data)
    def close(self): pass
    def shutdown(self, how): pass
    def recv(self, size): return b''
    def settimeout(self, value): pass
    def connect(self, address): raise AssertionError('No real or fake connect is required')


def sdk(encoding='proto', observed=(), complete=True, raises=False, error=False):
    """Exact qualified Python write signatures; encoding selection stays in SDK."""
    api = NS(calls=[], clients=[], sinks=[], PROTOBUF_MSG_ID=200)
    class NumericOUT(Enum):
        def __add__(self, offset): return self.value + offset
    api.OUT = NumericOUT('OUT', {**READS, **WRITES, 'REQ_IDS': 8, 'OTHER': 50})
    class Wrapper: pass
    class Contract: pass
    class Order: pass
    class OrderCancel: pass
    class Connection:
        def __init__(self):
            self.socket = Sink(); api.sinks.append(self.socket)
        def sendMsg(self, msg): return self.socket.send(msg)
        def disconnect(self): self.socket.close()
    class Client:
        def __init__(self, wrapper):
            self.wrapper = wrapper; self.conn = Connection(); self.connected = True
            api.clients.append(self)
        def isConnected(self): return self.connected
        def disconnect(self):
            self.connected = False; self.conn.disconnect(); api.calls.append(('disconnect',))
        def sendMsg(self, msgId, msg):
            opcode = msgId.value if isinstance(msgId, Enum) else msgId
            return self.conn.sendMsg(wire(opcode, encoding, msg.encode()))
        def sendMsgProtoBuf(self, msgId, msg):
            return self.conn.sendMsg(wire(msgId, 'raw', msg))
        def reqAllOpenOrders(self):
            api.calls.append(('read-open',))
            self.sendMsgProtoBuf(216, b'') if encoding == 'proto' else self.sendMsg(api.OUT.REQ_ALL_OPEN_ORDERS, '')
            for order_id in observed:
                self.wrapper.openOrder(order_id, NS(account='anonymous-fixture'), NS(), NS())
            if complete: self.wrapper.openOrderEnd()
        def placeOrder(self, orderId, contract, order):
            assert type(orderId) is int and type(contract) is Contract and type(order) is Order
            api.calls.append(('place', orderId, contract.conId, order.action, order.totalQuantity))
            if error:
                self.wrapper.error(orderId, 550, 'anonymous private error')
                return
            if encoding == 'proto': self.placeOrderProtoBuf(NS())
            else: self.sendMsg(api.OUT.PLACE_ORDER, 'fixture\0')
            if raises: raise RuntimeError('anonymous private error')
        def placeOrderProtoBuf(self, placeOrderRequestProto):
            api.calls.append(('proto-place',))
            self.sendMsgProtoBuf(api.OUT.PLACE_ORDER + 200, b'\x08\x01')
        def cancelOrder(self, orderId, orderCancel):
            assert type(orderId) is int and type(orderCancel) is OrderCancel
            api.calls.append(('cancel', orderId))
            if encoding == 'proto': self.cancelOrderProtoBuf(NS())
            else: self.sendMsg(api.OUT.CANCEL_ORDER, 'fixture\0')
            if raises: raise RuntimeError('anonymous private error')
        def cancelOrderProtoBuf(self, cancelOrderRequestProto):
            self.sendMsgProtoBuf(api.OUT.CANCEL_ORDER + 200, b'\x08\x01')
        def reqGlobalCancel(self, orderCancel):
            assert type(orderCancel) is OrderCancel
            api.calls.append(('global',))
            if encoding == 'proto': self.reqGlobalCancelProtoBuf(NS())
            else: self.sendMsg(api.OUT.REQ_GLOBAL_CANCEL, 'fixture\0')
            if raises: raise RuntimeError('anonymous private error')
        def reqGlobalCancelProtoBuf(self, globalCancelRequestProto):
            self.sendMsgProtoBuf(api.OUT.REQ_GLOBAL_CANCEL + 200, b'\x08\x01')
    api.Client, api.Wrapper = Client, Wrapper
    api.Connection, api.SocketType = Connection, Sink
    api.Contract, api.Order, api.OrderCancel = Contract, Order, OrderCancel
    return api


def transport(api=None, ready=10, synchronize=True):
    api = api or sdk()
    with patch('tradingbot_broker.ibkr_paper_transport._load_paper_api', return_value=api):
        t = PaperTWSTransport(PaperTWSConfig(PaperTransportIntent.LOCAL_PAPER_FOUNDATION))
    t._begin_generation()
    if ready is not None: api.clients[-1].wrapper.nextValidId(ready)
    if synchronize and ready is not None: t._synchronize_open_orders()
    return t, api, api.clients[-1]


class ConfigAndQualificationTests(unittest.TestCase):
    def test_explicit_typed_intent_required(self):
        with self.assertRaises(TypeError): PaperTWSConfig()
        for intent in ('LOCAL_PAPER_FOUNDATION', TradingMode.LIVE, TradingMode.PAPER, True, None):
            with self.subTest(intent=intent), self.assertRaises(ReadOnlyError): PaperTWSConfig(intent)

    def test_no_environment_or_live_selector(self):
        self.assertFalse(hasattr(PaperTWSConfig, 'from_environment'))
        with self.assertRaises(TypeError): PaperTWSConfig(PaperTransportIntent.LOCAL_PAPER_FOUNDATION, mode=TradingMode.LIVE)

    def test_localhost_variants(self):
        for host in ('127.0.0.1', 'localhost', '::1'):
            self.assertEqual(PaperTWSConfig(PaperTransportIntent.LOCAL_PAPER_FOUNDATION, host=host).host, host)

    def test_other_hosts_fail(self):
        for host in ('0.0.0.0', 'example.invalid', '../localhost', None, 1):
            with self.subTest(host=host), self.assertRaises(ReadOnlyError):
                PaperTWSConfig(PaperTransportIntent.LOCAL_PAPER_FOUNDATION, host=host)

    def test_bounded_exact_config_numbers(self):
        for field, bad in (('client_id', 0), ('client_id', True), ('client_id', -1),
                           ('client_id', 2147483648), ('port', 0), ('port', 65536),
                           ('port', '4002'), ('timeout_seconds', 0), ('timeout_seconds', 61),
                           ('timeout_seconds', 1.0)):
            with self.subTest(field=field, bad=bad), self.assertRaises(ReadOnlyError):
                PaperTWSConfig(PaperTransportIntent.LOCAL_PAPER_FOUNDATION, **{field: bad})

    def test_config_immutable_and_port_not_attestation(self):
        for port in (4001, 4002, 1234):
            with patch('tradingbot_broker.ibkr_paper_transport._load_paper_api', return_value=sdk()):
                t = PaperTWSTransport(PaperTWSConfig(PaperTransportIntent.LOCAL_PAPER_FOUNDATION, port=port))
            self.assertIs(t.account_mode, AccountMode.UNKNOWN)
            with self.assertRaises(AttributeError): t._config.port = 4002

    def test_missing_dependency_sanitized(self):
        with patch('tradingbot_broker.ibkr_readonly.importlib.import_module', side_effect=ImportError('private marker')):
            with self.assertRaisesRegex(ReadOnlyError, '^OFFICIAL_IBAPI_DEPENDENCY_UNAVAILABLE$'):
                PaperTWSTransport(PaperTWSConfig(PaperTransportIntent.LOCAL_PAPER_FOUNDATION))

    def test_constructor_qualifies_without_client_or_network(self):
        api = sdk()
        with patch('tradingbot_broker.ibkr_paper_transport._load_paper_api', return_value=api):
            PaperTWSTransport(PaperTWSConfig(PaperTransportIntent.LOCAL_PAPER_FOUNDATION))
        self.assertEqual(api.clients, []); self.assertEqual(api.calls, [])

    def test_named_qualification_exact(self):
        self.assertEqual(_qualify(sdk()), WRITES)

    def test_plain_exact_integer_fixture_qualification(self):
        api = sdk(); api.OUT = NS(**READS, **WRITES)
        self.assertEqual(_qualify(api), WRITES)

    def test_each_write_mismatch_and_missing_member_fail(self):
        for name in WRITES:
            for value in (None, True, '3', 3.0, 0, -1, 50, 203):
                api = sdk(); values = {**READS, **WRITES}; values[name] = value
                api.OUT = Enum('OUT', values)
                with self.subTest(name=name, value=value), self.assertRaises(ReadOnlyError): _qualify(api)
            api = sdk(); api.OUT = Enum('OUT', {k: v for k, v in {**READS, **WRITES}.items() if k != name})
            with self.assertRaises(ReadOnlyError): _qualify(api)

    def test_malformed_offset_fails(self):
        for offset in (None, 199, 201, True, 200.0, '200'):
            api = sdk(); api.PROTOBUF_MSG_ID = offset
            with self.subTest(offset=offset), self.assertRaises(ReadOnlyError): _qualify(api)

    def test_arbitrary_coercible_or_foreign_enum_does_not_qualify(self):
        class Coerce:
            def __int__(self): return 3
        for member in (Coerce(), Enum('Foreign', {'PLACE_ORDER': 3}).PLACE_ORDER):
            api = sdk(); api.OUT = NS(**READS, **WRITES); api.OUT.PLACE_ORDER = member
            with self.assertRaises(ReadOnlyError): _qualify(api)

    def test_unqualified_read_contract_fails(self):
        api = sdk(); api.OUT = Enum('OUT', {**READS, **WRITES, 'START_API': 72})
        with self.assertRaises(ReadOnlyError): _qualify(api)

    def test_obsolete_cancel_signature_fails_qualification(self):
        api = sdk()
        api.Client.cancelOrder = lambda self, orderId: None
        with self.assertRaises(ReadOnlyError): _qualify(api)

    def test_obsolete_global_signature_fails_qualification(self):
        api = sdk(); api.Client.reqGlobalCancel = lambda self: None
        with self.assertRaises(ReadOnlyError): _qualify(api)

    def test_variadic_write_signature_fails_qualification(self):
        api = sdk(); api.Client.placeOrder = lambda self, *args: None
        with self.assertRaises(ReadOnlyError): _qualify(api)


class WireTests(unittest.TestCase):
    def setUp(self): self.sink = Sink(); self.socket = _guarded_socket(self.sink, Sink)

    def test_exact_fixed_sets(self):
        self.assertEqual(_LEGACY_POLICY, LEGACY); self.assertEqual(_PROTOBUF_POLICY, PROTO)
        self.assertEqual(len(_LEGACY_POLICY), 13); self.assertEqual(len(_PROTOBUF_POLICY), 13)

    def test_all_ascii_legacy_operations(self):
        for opcode in LEGACY: self.socket.sendall(wire(opcode, 'ascii'))
        self.assertEqual(len(self.sink.sent), 13)

    def test_all_raw_legacy_operations(self):
        for opcode in LEGACY: self.socket.sendall(wire(opcode))
        self.assertEqual(len(self.sink.sent), 13)

    def test_all_exact_protobuf_operations(self):
        for opcode in PROTO: self.socket.sendall(wire(opcode))
        self.assertEqual(len(self.sink.sent), 13)

    def test_empty_protobuf_payload(self):
        for opcode in PROTO: self.socket.sendall(wire(opcode, payload=b''))

    def test_protobuf_not_permitted_as_ascii(self):
        for opcode in PROTO:
            with self.assertRaises(ReadOnlyError): self.socket.sendall(wire(opcode, 'ascii'))
        self.assertEqual(self.sink.sent, [])

    def test_unknown_zero_high_reqids_not_allowed(self):
        for opcode in (0, 8, 208, 50, 250, 200, 999, 4294967295):
            for encoding in ('ascii', 'raw'):
                with self.subTest(opcode=opcode, encoding=encoding), self.assertRaises(ReadOnlyError):
                    self.socket.sendall(wire(opcode, encoding))
        self.assertEqual(self.sink.sent, [])

    def test_no_numeric_ranges(self):
        for opcode in range(1, 320):
            if opcode not in LEGACY | PROTO:
                with self.assertRaises(ReadOnlyError): self.socket.sendall(wire(opcode))
        self.assertEqual(self.sink.sent, [])

    def test_malformed_truncated_lengths_and_prefixes(self):
        for message in (b'', b'\0', b'\0\0\0', frame(b'\0'), frame(b'\0\0\0'),
                        wire(3)[:-1], wire(3) + b'junk', frame(b'garbage\0'),
                        frame(b'3x\0'), frame(b'3'), frame(b'\x01\0\0\x03')):
            with self.subTest(message=message), self.assertRaises(ReadOnlyError): self.socket.sendall(message)
        self.assertEqual(self.sink.sent, [])

    def test_oversized_frame(self):
        with self.assertRaises(ReadOnlyError): self.socket.sendall(wire(3, payload=b'x' * 65536))

    def test_two_frames_cannot_be_spliced(self):
        with self.assertRaises(ReadOnlyError): self.socket.sendall(wire(3) + wire(4))

    def test_handshake_exact_and_once(self):
        exact = b'API\0\0\0\0\x09v100..226'
        self.socket.sendall(exact)
        self.assertEqual(self.sink.sent, [exact])
        with self.assertRaises(ReadOnlyError): self.socket.sendall(exact)

    def test_malformed_handshake(self):
        with self.assertRaises(ReadOnlyError): self.socket.sendall(b'API\0' + frame(b'v100..226\0'))

    def test_partial_send_exact_suffix(self):
        message = wire(203, payload=b'\x08\x01\x10\x02')
        self.sink.limit = 3
        sent = self.socket.send(message)
        self.sink.limit = None
        self.socket.sendall(message[sent:])
        self.assertEqual(b''.join(self.sink.sent), message)

    def test_altered_or_replaced_pending_bytes_fail(self):
        message = wire(203); self.sink.limit = 2
        count = self.socket.send(message)
        for altered in (message[count:] + b'x', wire(204), message):
            with self.assertRaises(ReadOnlyError): self.socket.sendall(altered)
        self.sink.limit = None; self.socket.sendall(message[count:])
        self.assertEqual(b''.join(self.sink.sent), message)

    def test_no_raw_socket_or_descriptor(self):
        for name in ('fileno', 'socket', 'raw', '__dict__'):
            self.assertFalse(hasattr(self.socket, name))

    def test_exact_bytes_required(self):
        for value in (bytearray(wire(3)), memoryview(wire(3)), '3', None):
            with self.assertRaises(ReadOnlyError): self.socket.sendall(value)

    def test_read_policy_remains_distinct_lifetime(self):
        raw = Sink(); read = guarded_socket(raw, Sink, frozenset(READS.values()),
                                          frozenset(v + 200 for v in READS.values()))
        self.socket.sendall(wire(203))
        for opcode in (3, 4, 58, 203, 204, 258):
            with self.assertRaises(ReadOnlyError): read.sendall(wire(opcode))
        self.assertEqual(raw.sent, [])


class ClientBoundaryTests(unittest.TestCase):
    def test_each_approved_client_legacy_and_protobuf_message(self):
        t, api, client = transport()
        before = len(api.sinks[-1].sent)
        for opcode in LEGACY: client.sendMsg(opcode, '')
        for opcode in PROTO: client.sendMsgProtoBuf(opcode, b'')
        self.assertEqual(len(api.sinks[-1].sent) - before, 26)

    def test_named_view_no_generic_sender_or_sdk_objects(self):
        t, api, client = transport()
        for name in ('sendMsg', 'sendMsgProtoBuf', 'conn', 'socket', 'wrapper', 'fileno', '__dict__', 'connect', 'run'):
            self.assertFalse(hasattr(t._client, name))
        for name in ('connect', 'place_order', 'cancel_order', 'reqGlobalCancel', 'placeOrder',
                     'sendMsg', 'sendMsgProtoBuf', 'socket', 'conn'):
            self.assertFalse(hasattr(t, name))

    def test_no_arbitrary_method_dispatch(self):
        t, api, client = transport()
        with self.assertRaises(TypeError): t._client.reqAllOpenOrders(_method='reqIds')
        self.assertFalse(hasattr(t._client, 'reqIds'))

    def test_constructor_sdk_error_sanitized(self):
        api = sdk()
        def bad(self, wrapper): raise RuntimeError('anonymous private error')
        api.Client.__init__ = bad
        with patch('tradingbot_broker.ibkr_paper_transport._load_paper_api', return_value=api):
            t = PaperTWSTransport(PaperTWSConfig(PaperTransportIntent.LOCAL_PAPER_FOUNDATION))
        with self.assertRaisesRegex(ReadOnlyError, '^SDK_CLIENT_CONSTRUCTION_FAILED$'): t._begin_generation()

    def test_guarded_client_rejects_unknown_and_reqids(self):
        t, api, client = transport(); before = len(api.sinks[-1].sent)
        for value in (8, 208, 50, 250, 999, True, '3', 3.0, object()):
            for call in (lambda: client.sendMsg(value, ''), lambda: client.sendMsgProtoBuf(value, b'')):
                with self.assertRaises(ReadOnlyError): call()
        self.assertEqual(len(api.sinks[-1].sent), before)

    def test_client_exact_payload_types(self):
        t, api, client = transport()
        for bad in (bytearray(), memoryview(b''), '', None, object()):
            with self.assertRaises(ReadOnlyError): client.sendMsgProtoBuf(203, bad)
        for bad in (b'', bytearray(), None):
            with self.assertRaises(ReadOnlyError): client.sendMsg(3, bad)

    def test_other_enum_and_coercion_rejected(self):
        t, api, client = transport()
        class Coerce:
            def __int__(self): return 3
        for value in (Coerce(), Enum('Other', {'PLACE_ORDER': 3}).PLACE_ORDER):
            with self.assertRaises(ReadOnlyError): client.sendMsg(value, '')
            with self.assertRaises(ReadOnlyError): client.sendMsgProtoBuf(value, b'')

    def test_base_class_send_and_connection_cannot_bypass(self):
        t, api, client = transport(); before = len(api.sinks[-1].sent)
        for attempt in (lambda: api.Client.sendMsg(client, 8, ''),
                        lambda: api.Client.sendMsgProtoBuf(client, 208, b''),
                        lambda: api.Connection.sendMsg(client.conn, wire(208)),
                        lambda: client.conn.socket.send(wire(8)),
                        lambda: client.conn.socket.sendall(wire(999))):
            with self.assertRaises(ReadOnlyError): attempt()
        self.assertEqual(len(api.sinks[-1].sent), before)

    def test_read_only_sdk_and_views_still_block_all_writes(self):
        api = protobuf_sdk(); t, api, clock = read_transport(api)
        t.connect()
        client = api.clients[-1]
        for opcode in (3, 4, 58):
            with self.assertRaises(ReadOnlyError): client.sendMsg(opcode, '')
        for opcode in (203, 204, 258):
            with self.assertRaises(ReadOnlyError): client.sendMsgProtoBuf(opcode, b'')
        for name in ('placeOrder', 'cancelOrder', 'reqGlobalCancel', 'sendMsg', 'sendMsgProtoBuf', 'conn', 'socket'):
            self.assertFalse(hasattr(t._client, name))
        self.assertEqual(set(_read_opcodes(api.OUT).values()), set(READS.values()))
        self.assertEqual(set(_protobuf_read_opcodes(api.OUT, 200).values()), {v + 200 for v in READS.values()})
        t.disconnect()


class AllocatorTests(unittest.TestCase):
    def test_no_allocation_before_nextvalidid(self):
        t, api, client = transport(ready=None)
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.NOT_DISPATCHED)

    def test_zero_nextid_valid_and_ids_increase(self):
        t, api, client = transport(ready=0)
        self.assertEqual([t._allocate_order_id(t._generation) for _ in range(3)], [0, 1, 2])

    def test_repeated_nextid_never_moves_backwards(self):
        t, api, client = transport()
        client.wrapper.nextValidId(8); self.assertEqual(t._boundary, 10)
        self.assertEqual(t._allocate_order_id(t._generation), 10)
        client.wrapper.nextValidId(10); self.assertEqual(t._boundary, 11)
        client.wrapper.nextValidId(50); self.assertEqual(t._allocate_order_id(t._generation), 50)

    def test_observed_openorder_and_status_raise_boundary(self):
        t, api, client = transport()
        client.wrapper.openOrder(50, object(), object(), object())
        self.assertEqual(t._boundary, 51)
        client.wrapper.orderStatus(70, 'Submitted', Decimal(0), Decimal(1), 0.0, 0, 0, 0.0, 1, '', 0.0)
        self.assertEqual(t._boundary, 71)
        client.wrapper.openOrder(50, object(), object(), object())
        self.assertEqual(t._allocate_order_id(t._generation), 71)

    def test_openorder_before_nextvalidid_cannot_make_ready_or_lower_boundary(self):
        t, api, client = transport(ready=None)
        client.wrapper.openOrder(100, object(), object(), object())
        self.assertFalse(t._initialized)
        client.wrapper.nextValidId(10); t._synchronize_open_orders()
        self.assertEqual(t._allocate_order_id(t._generation), 101)

    def test_all_open_orders_sync_lower_bound(self):
        t, api, client = transport(sdk(observed=(2, 100, 99, 100)))
        self.assertEqual(t._allocate_order_id(t._generation), 101)

    def test_incomplete_sync_blocks_new_order(self):
        t, api, client = transport(sdk(complete=False))
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.NOT_DISPATCHED)
        self.assertFalse(any(c[0] == 'place' for c in api.calls))

    def test_premature_end_does_not_authorize_sync(self):
        t, api, client = transport(synchronize=False)
        client.wrapper.openOrderEnd()
        self.assertEqual(t._sync, 'NOT_REQUESTED')
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)

    def test_malformed_ids_do_not_corrupt_boundary_and_fail_closed(self):
        for kind in ('next', 'observed'):
            for value in (True, -1, '12', 1.0, None):
                t, api, client = transport()
                t._callback(t._generation, kind, value)
                self.assertEqual(t._boundary, 10)
                with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)

    def test_reconnect_requires_fresh_nextvalidid_and_sync(self):
        t, api, client = transport(); old = t._generation
        t._begin_generation(); new_client = api.clients[-1]
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)
        client.wrapper.nextValidId(1000)
        client.wrapper.openOrder(1000, object(), object(), object())
        client.wrapper.orderStatus(1000, '', 0, 0, 0, 0, 0, 0, 1, '', 0)
        client.wrapper.openOrderEnd()
        self.assertIsNone(t._boundary)
        new_client.wrapper.nextValidId(20); t._synchronize_open_orders()
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(old)
        self.assertEqual(t._allocate_order_id(t._generation), 20)

    def test_caller_cannot_select_new_order_id(self):
        t, api, client = transport()
        with self.assertRaises(TypeError): t._dispatch_new(SPEC, order_id=77)
        self.assertFalse(hasattr(t, 'allocate_order_id'))

    def test_concurrent_allocation_unique(self):
        t, api, client = transport()
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: t._allocate_order_id(t._generation), range(100)))
        self.assertEqual(sorted(ids), list(range(10, 110)))

    def test_closed_generation_callbacks_ignored(self):
        t, api, client = transport(); t.disconnect()
        client.wrapper.nextValidId(99)
        self.assertIsNone(t._boundary); self.assertFalse(t._initialized)

    def test_generation_bool_cannot_impersonate_current_session(self):
        t, api, client = transport(ready=None)
        self.assertEqual(t._generation, 1)
        t._callback(True, 'next', 20)
        self.assertIsNone(t._boundary)
        client.wrapper.nextValidId(10); t._synchronize_open_orders()
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(True)


class DispatchTests(unittest.TestCase):
    def test_result_is_immutable_and_requires_typed_safe_values(self):
        result = _DispatchResult(_DispatchState.NOT_DISPATCHED, _Operation.PLACE_ORDER,
                                 _Reason.PREFLIGHT_FAILED, 0)
        with self.assertRaises(AttributeError): result.state = _DispatchState.DISPATCHED
        for changes in ({'state': 'DISPATCHED'}, {'operation': 'anonymous-private-marker'},
                        {'generation': True}, {'order_id': -1}, {'error_codes': ('private',)},
                        {'error_codes': [550]}, {'reason': 'private'}):
            values = dict(state=_DispatchState.NOT_DISPATCHED, operation=_Operation.PLACE_ORDER,
                          reason=_Reason.PREFLIGHT_FAILED, generation=0)
            values.update(changes)
            with self.assertRaisesRegex(ReadOnlyError, '^INVALID_PAPER_DISPATCH_RESULT$'):
                _DispatchResult(**values)

    def test_method_names_or_arbitrary_operations_cannot_dispatch_or_leak(self):
        t, api, client = transport(); before = len(api.calls)
        for value in ('placeOrder', 'anonymous-private-marker', 3, object()):
            with self.assertRaisesRegex(ReadOnlyError, '^INVALID_PAPER_OPERATION$'):
                t._dispatch(value, SPEC)
        self.assertEqual(len(api.calls), before)

    def test_private_object_construction_error_is_not_dispatched(self):
        api = sdk()
        api.Order = lambda: (_ for _ in ()).throw(RuntimeError('anonymous private error'))
        t, api, client = transport(api)
        result = t._dispatch_new(SPEC)
        self.assertIs(result.state, _DispatchState.NOT_DISPATCHED)
        self.assertEqual(t._boundary, 10)
        self.assertFalse(any(c[0] == 'place' for c in api.calls))
        self.assertNotIn('private', repr(result))

    def test_ordinary_cancel_never_implies_global_cancel(self):
        t, api, client = transport(sdk(observed=(7,)))
        t._dispatch_cancel(7)
        self.assertFalse(any(c[0] == 'global' for c in api.calls))

    def test_shutdown_error_sanitized_and_allocator_unusable(self):
        t, api, client = transport()
        client.disconnect = lambda: (_ for _ in ()).throw(RuntimeError('anonymous private error'))
        with self.assertRaisesRegex(ReadOnlyError, '^SDK_SHUTDOWN_FAILED$'): t.disconnect()
        self.assertIsNone(t._client); self.assertIsNone(t._boundary)
        self.assertIs(t._dispatch_global_cancel().state, _DispatchState.NOT_DISPATCHED)

    def test_unexpected_sdk_connected_values_fail_closed(self):
        for value in (None, 1, 'CONNECTED', 1.0):
            t, api, client = transport(); client.isConnected = lambda: value
            self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.NOT_DISPATCHED)

    def test_synchronous_error_blocks_subsequent_dispatch_pending_reconciliation(self):
        t, api, client = transport(sdk(error=True))
        first = t._dispatch_new(SPEC)
        self.assertIs(first.state, _DispatchState.DISPATCHED)
        before = len(api.calls)
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.NOT_DISPATCHED)
        self.assertEqual(len(api.calls), before)

    def test_repeated_error_after_full_buffer_still_detected_during_dispatch(self):
        t, api, client = transport(sdk(error=True))
        for _ in range(128): client.wrapper.error(1, 550, 'anonymous')
        result = t._dispatch_new(SPEC)
        self.assertIs(result.state, _DispatchState.DISPATCHED)
        self.assertEqual(result.error_codes, (550,) * 128)
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.NOT_DISPATCHED)

    def test_exact_place_signature_and_private_sdk_construction(self):
        t, api, client = transport()
        result = t._dispatch_new(SPEC)
        self.assertIs(result.state, _DispatchState.DISPATCHED)
        self.assertEqual(result.order_id, 10)
        self.assertIn(('place', 10, 17, 'BUY', Decimal('1.25')), api.calls)
        self.assertEqual(int.from_bytes(api.sinks[-1].sent[-1][4:8], 'big'), 203)

    def test_cancel_two_argument_contract(self):
        t, api, client = transport(sdk(observed=(7,)))
        self.assertIs(t._dispatch_cancel(7).state, _DispatchState.DISPATCHED)
        self.assertIn(('cancel', 7), api.calls)
        self.assertEqual(int.from_bytes(api.sinks[-1].sent[-1][4:8], 'big'), 204)

    def test_global_cancel_one_argument_contract(self):
        t, api, client = transport()
        self.assertIs(t._dispatch_global_cancel().state, _DispatchState.DISPATCHED)
        self.assertEqual(api.calls.count(('global',)), 1)
        self.assertEqual(int.from_bytes(api.sinks[-1].sent[-1][4:8], 'big'), 258)

    def test_sdk_selects_each_encoding_for_all_named_writes(self):
        for encoding in ('ascii', 'raw', 'proto'):
            t, api, client = transport(sdk(encoding=encoding, observed=(7,)))
            for result in (t._dispatch_new(SPEC), t._dispatch_cancel(7), t._dispatch_global_cancel()):
                self.assertIs(result.state, _DispatchState.DISPATCHED)
            expected = (203, 204, 258) if encoding == 'proto' else (3, 4, 58)
            for data, opcode in zip(api.sinks[-1].sent[-3:], expected):
                actual = int(data[4:].split(b'\0')[0]) if encoding == 'ascii' else int.from_bytes(data[4:8], 'big')
                self.assertEqual(actual, opcode)

    def test_no_protobuf_helper_direct_invocation_in_transport(self):
        source = inspect.getsource(PaperTWSTransport)
        for helper in ('placeOrderProtoBuf(', 'cancelOrderProtoBuf(', 'reqGlobalCancelProtoBuf('):
            self.assertNotIn(helper, source)

    def test_unknown_existing_order_cannot_cancel(self):
        t, api, client = transport()
        for order_id in (999, True, '10', -1):
            self.assertIs(t._dispatch_cancel(order_id).state, _DispatchState.NOT_DISPATCHED)
        self.assertFalse(any(c[0] == 'cancel' for c in api.calls))

    def test_arbitrary_sdk_order_not_accepted_by_new_primitive(self):
        t, api, client = transport()
        for invalid in (api.Order(), NS(con_id=17, side=Side.BUY, quantity=Decimal(1)), object()):
            self.assertIs(t._dispatch_new(invalid).state, _DispatchState.NOT_DISPATCHED)
        self.assertEqual(t._boundary, 10)

    def test_invalid_specs_fail(self):
        for values in ((True, Side.BUY, Decimal(1)), (17, 'BUY', Decimal(1)),
                       (17, Side.BUY, 1.0), (17, Side.BUY, Decimal('NaN')),
                       (17, Side.BUY, Decimal(0))):
            with self.assertRaises(ReadOnlyError): _StockMarketSpec(*values)

    def test_mutated_spec_revalidated_before_invocation(self):
        t, api, client = transport(); spec = _StockMarketSpec(17, Side.BUY, Decimal(1))
        object.__setattr__(spec, 'quantity', 1.0)
        self.assertIs(t._dispatch_new(spec).state, _DispatchState.NOT_DISPATCHED)

    def test_sdk_state_failure_is_not_dispatched_and_sanitized(self):
        t, api, client = transport()
        client.isConnected = lambda: (_ for _ in ()).throw(RuntimeError('anonymous private error'))
        result = t._dispatch_new(SPEC)
        self.assertIs(result.state, _DispatchState.NOT_DISPATCHED)
        self.assertNotIn('private', repr(result))

    def test_sdk_disconnect_preflight_blocks(self):
        t, api, client = transport(); client.connected = False
        self.assertIs(t._dispatch_global_cancel().state, _DispatchState.NOT_DISPATCHED)
        self.assertFalse(any(c[0] == 'global' for c in api.calls))

    def test_uncertain_each_operation_no_retry_and_no_reuse(self):
        for operation in _Operation:
            t, api, client = transport(sdk(raises=True, observed=(7,)))
            method = { _Operation.PLACE_ORDER: lambda: t._dispatch_new(SPEC),
                       _Operation.CANCEL_ORDER: lambda: t._dispatch_cancel(7),
                       _Operation.GLOBAL_CANCEL: t._dispatch_global_cancel }[operation]
            result = method()
            self.assertIs(result.state, _DispatchState.OUTCOME_UNKNOWN)
            count = len(api.calls)
            self.assertIs(method().state, _DispatchState.NOT_DISPATCHED)
            self.assertEqual(len(api.calls), count)
            self.assertNotIn('private', repr(result))
            if operation is _Operation.PLACE_ORDER: self.assertEqual(t._boundary, 11)

    def test_normal_return_with_error_is_not_broker_acceptance(self):
        t, api, client = transport(sdk(error=True))
        before = len(api.sinks[-1].sent)
        result = t._dispatch_new(SPEC)
        self.assertIs(result.state, _DispatchState.DISPATCHED)
        self.assertEqual(result.error_codes, (550,))
        self.assertEqual(len(api.sinks[-1].sent), before)
        for attr in ('accepted', 'acknowledgement', 'fill', 'position'):
            self.assertFalse(hasattr(result, attr))
        self.assertNotIn('private', repr(result))

    def test_no_implicit_global_cancel_on_cleanup(self):
        t, api, client = transport(); t.disconnect(); t.disconnect()
        self.assertFalse(any(c[0] in ('place', 'cancel', 'global') for c in api.calls))

    def test_callback_foundation_discards_raw_sensitive_objects(self):
        t, api, client = transport(); marker = 'anonymous-private-marker'
        raw = NS(acctNumber=marker, account=marker)
        client.wrapper.execDetails(1, raw, raw)
        client.wrapper.execDetailsEnd(1)
        client.wrapper.commissionReport(raw); client.wrapper.commissionAndFeesReport(raw)
        client.wrapper.error(1, 550, marker)
        self.assertEqual(t._callback_kinds, {'execution', 'executions_end', 'commission'})
        self.assertEqual(t._errors, [550])
        self.assertNotIn(marker, repr(t)); self.assertNotIn(marker, repr(t.__dict__))

    def test_callback_errors_bounded_and_numeric_only(self):
        t, api, client = transport()
        for i in range(200): client.wrapper.error(1, 500, 'anonymous')
        client.wrapper.error(1, 'private', 'anonymous')
        self.assertEqual(len(t._errors), 128)
        self.assertEqual(set(t._errors), {500})

    def test_connection_closed_callback_invalidates_allocator(self):
        t, api, client = transport(); client.wrapper.connectionClosed()
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.NOT_DISPATCHED)
        self.assertIsNone(t._boundary)


class PermanentReadOnlyBrokerTests(AuthorizationFixture):
    def test_isolated_paper_wire_cannot_enable_armed_read_broker_even_with_risk(self):
        self.ready(); cap = self.arm(scopes=tuple(PaperExecutionScope))
        # Exercise the new offline paper policy while the old broker is armed.
        isolated, api, client = transport()
        self.assertIs(isolated._dispatch_new(SPEC).state, _DispatchState.DISPATCHED)
        order = request()
        for action in (
            lambda: self.broker.place_order(order, self.clock(), permission(order)),
            lambda: self.broker.replace_order('known-1', order, self.clock(), permission(order)),
            lambda: self.broker.modify_order('known-1', order, self.clock(), permission(order)),
            lambda: self.broker.cancel_order('known-1', self.clock()),
            lambda: self.broker.cancel_working_orders(self.clock()),
            lambda: self.broker.flatten_positions(self.clock()),
            lambda: self.broker.set_trading_enabled(True, self.clock()),
        ):
            with self.assertRaises(BrokerOperationError) as error: action()
            self.assertIs(error.exception.reason, BrokerReason.READ_ONLY_BROKER_TRANSPORT)
        self.assertTrue(self.broker.validate_paper_execution(cap, PaperExecutionScope.PLACE_ORDER))
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)


class OrderIdSecurityCorrections(unittest.TestCase):
    def test_qualified_constants(self):
        self.assertEqual((MIN_ORDER_ID, MAX_ORDER_ID), (0, 2147483647))

    def test_max_next_valid_and_exactly_one_allocation(self):
        t, api, client = transport(ready=MAX_ORDER_ID)
        self.assertEqual(t._allocate_order_id(t._generation), MAX_ORDER_ID)
        self.assertTrue(t._exhausted); self.assertIsNone(t._boundary)
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)

    def test_max_dispatch_once_and_no_second_sdk_invocation(self):
        t, api, client = transport(ready=MAX_ORDER_ID)
        result = t._dispatch_new(SPEC)
        self.assertIs(result.state, _DispatchState.DISPATCHED)
        self.assertEqual(result.order_id, MAX_ORDER_ID)
        before = len(api.calls)
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.NOT_DISPATCHED)
        self.assertEqual(len(api.calls), before)

    def test_lower_nextvalidid_cannot_revive_exhausted_allocator(self):
        t, api, client = transport(ready=MAX_ORDER_ID)
        t._allocate_order_id(t._generation)
        client.wrapper.nextValidId(0)
        client.wrapper.nextValidId(MAX_ORDER_ID)
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)
        self.assertTrue(t._exhausted); self.assertIsNone(t._boundary)

    def test_observed_max_before_nextvalidid_still_exhausts(self):
        t, api, client = transport(ready=None)
        client.wrapper.openOrder(MAX_ORDER_ID, object(), object(), object())
        client.wrapper.nextValidId(0); t._synchronize_open_orders()
        self.assertTrue(t._exhausted)
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.NOT_DISPATCHED)

    def test_known_max_can_cancel_without_new_allocation(self):
        t, api, client = transport(sdk(observed=(MAX_ORDER_ID,)))
        self.assertTrue(t._exhausted)
        result = t._dispatch_cancel(MAX_ORDER_ID)
        self.assertIs(result.state, _DispatchState.DISPATCHED)
        self.assertEqual(result.order_id, MAX_ORDER_ID)
        self.assertTrue(t._exhausted); self.assertIsNone(t._boundary)

    def test_unknown_max_cannot_cancel(self):
        t, api, client = transport()
        self.assertIs(t._dispatch_cancel(MAX_ORDER_ID).state, _DispatchState.NOT_DISPATCHED)
        self.assertFalse(any(c[0] == 'cancel' for c in api.calls))

    def test_reserved_max_never_recycled_after_final_local_failure(self):
        t, api, client = transport(ready=MAX_ORDER_ID)
        checks = iter((True, False))
        client.isConnected = lambda: next(checks, True)
        result = t._dispatch_new(SPEC)
        self.assertIs(result.state, _DispatchState.NOT_DISPATCHED)
        self.assertEqual(result.order_id, MAX_ORDER_ID)
        self.assertTrue(t._exhausted)
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.NOT_DISPATCHED)
        self.assertFalse(any(c[0] == 'place' for c in api.calls))

    def test_reserved_max_never_recycled_after_unknown_outcome(self):
        t, api, client = transport(sdk(raises=True), ready=MAX_ORDER_ID)
        result = t._dispatch_new(SPEC)
        self.assertIs(result.state, _DispatchState.OUTCOME_UNKNOWN)
        self.assertEqual(result.order_id, MAX_ORDER_ID)
        self.assertTrue(t._exhausted)
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)

    def test_concurrent_last_allocations_only_one_succeeds(self):
        t, api, client = transport(ready=MAX_ORDER_ID)
        def allocate(_):
            try: return t._allocate_order_id(t._generation)
            except ReadOnlyError: return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(allocate, range(12)))
        self.assertEqual(values.count(MAX_ORDER_ID), 1)
        self.assertEqual(values.count(None), 11)

    def test_observed_max_and_allocation_race_never_overflows(self):
        t, api, client = transport(ready=MAX_ORDER_ID)
        def allocate():
            try: return t._allocate_order_id(t._generation)
            except ReadOnlyError: return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            allocated = pool.submit(allocate)
            observed = pool.submit(client.wrapper.openOrder, MAX_ORDER_ID, object(), object(), object())
            value = allocated.result(timeout=5); observed.result(timeout=5)
        self.assertIn(value, (None, MAX_ORDER_ID))
        self.assertTrue(t._exhausted); self.assertIsNone(t._boundary)

    def test_max_dispatch_result_valid(self):
        self.assertEqual(_DispatchResult(_DispatchState.NOT_DISPATCHED, _Operation.PLACE_ORDER,
            _Reason.PREFLIGHT_FAILED, 1, MAX_ORDER_ID).order_id, MAX_ORDER_ID)


def invalid_order_id_callback(kind, value):
    def test(self):
        t, api, client = transport()
        previous = (t._boundary, set(t._known), t._exhausted)
        if kind == 'next': client.wrapper.nextValidId(value)
        elif kind == 'open': client.wrapper.openOrder(value, object(), object(), object())
        else: client.wrapper.orderStatus(value, '', 0, 0, 0, 0, 0, 0, 1, '', 0)
        self.assertEqual((t._boundary, t._known, t._exhausted), previous)
        before = len(api.calls)
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.NOT_DISPATCHED)
        self.assertEqual(len(api.calls), before)
    return test


for _kind in ('next', 'open', 'status'):
    for _label, _value in (('above_max', MAX_ORDER_ID + 1), ('huge', 2**80),
                          ('negative', -1), ('bool', True)):
        setattr(OrderIdSecurityCorrections, 'test_invalid_' + _kind + '_' + _label,
                invalid_order_id_callback(_kind, _value))


def observed_max_callback(kind):
    def test(self):
        t, api, client = transport()
        if kind == 'open': client.wrapper.openOrder(MAX_ORDER_ID, object(), object(), object())
        else: client.wrapper.orderStatus(MAX_ORDER_ID, '', 0, 0, 0, 0, 0, 0, 1, '', 0)
        self.assertTrue(t._exhausted); self.assertIsNone(t._boundary)
        self.assertIn(MAX_ORDER_ID, t._known)
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)
    return test


for _kind in ('open', 'status'):
    setattr(OrderIdSecurityCorrections, 'test_observed_max_' + _kind, observed_max_callback(_kind))


def invalid_id_cancel_result(value):
    def test(self):
        t, api, client = transport(); before = len(api.calls)
        self.assertIs(t._dispatch_cancel(value).state, _DispatchState.NOT_DISPATCHED)
        self.assertEqual(len(api.calls), before)
        with self.assertRaises(ReadOnlyError):
            _DispatchResult(_DispatchState.NOT_DISPATCHED, _Operation.CANCEL_ORDER,
                            _Reason.PREFLIGHT_FAILED, 1, value)
    return test


for _label, _value in (('above_max', MAX_ORDER_ID + 1), ('huge', 2**80), ('negative', -1), ('bool', True)):
    setattr(OrderIdSecurityCorrections, 'test_cancel_and_result_' + _label, invalid_id_cancel_result(_value))


class ReconciliationSecurityCorrections(unittest.TestCase):
    def assert_writes_blocked(self, t, api):
        before = (len(api.calls), t._boundary, t._exhausted)
        for result in (t._dispatch_new(SPEC), t._dispatch_cancel(7), t._dispatch_global_cancel()):
            self.assertIs(result.state, _DispatchState.NOT_DISPATCHED)
            self.assertTrue(result.reconciliation_required)
        self.assertEqual((len(api.calls), t._boundary, t._exhausted), before)

    def test_initial_state_no_barrier_or_write_attempt(self):
        t, api, client = transport()
        self.assertFalse(t._requires_reconciliation())
        self.assertFalse(t._write_invocation_started)

    def test_late_error_latches_before_next_attempt_baseline(self):
        t, api, client = transport(sdk(observed=(7,)))
        first = t._dispatch_new(SPEC)
        self.assertIs(first.state, _DispatchState.DISPATCHED)
        self.assertFalse(first.reconciliation_required)
        client.wrapper.error(first.order_id, 550, 'anonymous-private-marker')
        self.assertTrue(t._requires_reconciliation())
        self.assert_writes_blocked(t, api)
        self.assertIs(first.state, _DispatchState.DISPATCHED)  # No rejection/acceptance rewrite.
        self.assertNotIn('anonymous-private-marker', repr(t.__dict__))

    def test_error_before_any_write_does_not_create_write_attribution(self):
        t, api, client = transport(); client.wrapper.error(1, 550, 'anonymous')
        self.assertFalse(t._requires_reconciliation())
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.DISPATCHED)

    def test_no_unqualified_informational_error_exemption(self):
        t, api, client = transport(); t._dispatch_new(SPEC)
        client.wrapper.error(1, 2104, 'anonymous')
        self.assertTrue(t._requires_reconciliation())
        self.assert_writes_blocked(t, api)

    def test_current_error_after_earlier_generation_write_latches(self):
        t, api, client = transport(); t._dispatch_new(SPEC)
        t._begin_generation(); current = api.clients[-1]
        current.wrapper.nextValidId(20); t._synchronize_open_orders()
        current.wrapper.error(1, 550, 'anonymous')
        self.assertTrue(t._requires_reconciliation())
        self.assert_writes_blocked(t, api)

    def test_old_generation_error_does_not_poison_clean_current_state(self):
        t, api, old = transport(); t._dispatch_new(SPEC)
        t._begin_generation(); current = api.clients[-1]
        current.wrapper.nextValidId(20); t._synchronize_open_orders()
        old.wrapper.error(1, 550, 'anonymous')
        self.assertEqual(t._errors, [])
        self.assertFalse(t._requires_reconciliation())
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.DISPATCHED)

    def test_old_error_cannot_clear_latched_barrier(self):
        t, api, old = transport(sdk(raises=True)); t._dispatch_new(SPEC)
        t._begin_generation(); current = api.clients[-1]
        current.wrapper.nextValidId(20); t._synchronize_open_orders()
        old.wrapper.error(1, 550, 'anonymous')
        self.assertTrue(t._requires_reconciliation())
        self.assertEqual(t._errors, [])
        self.assert_writes_blocked(t, api)

    def test_synchronous_error_barrier_and_result_survive_reconnect(self):
        t, api, client = transport(sdk(error=True))
        result = t._dispatch_new(SPEC)
        self.assertIs(result.state, _DispatchState.DISPATCHED)
        self.assertTrue(result.reconciliation_required)
        self.assertEqual(result.error_codes, (550,))
        t._begin_generation(); api.clients[-1].wrapper.nextValidId(20); t._synchronize_open_orders()
        self.assert_writes_blocked(t, api)

    def test_late_error_on_disconnected_current_generation_still_latches(self):
        t, api, client = transport(); t._dispatch_new(SPEC); t.disconnect()
        client.wrapper.error(1, 550, 'anonymous')
        self.assertTrue(t._requires_reconciliation())
        t._begin_generation(); api.clients[-1].wrapper.nextValidId(20); t._synchronize_open_orders()
        self.assert_writes_blocked(t, api)

    def test_no_reconcile_or_clear_api(self):
        t, api, client = transport()
        for name in ('clear_reconciliation', 'reconcile', 'reset_uncertainty', 'unblock_writes'):
            self.assertFalse(hasattr(t, name))

    def notifying_arrival_lock(self, t, announced):
        actual = t._arrival_lock
        class Notify:
            def __enter__(self): actual.acquire(); return self
            def __exit__(self, *args):
                latched = t._reconciliation_required
                actual.release()
                if latched: announced.set()
        t._arrival_lock = Notify()

    def test_deferred_error_latches_before_bookkeeping_obtains_dispatch_lock(self):
        t, api, client = transport(); t._dispatch_new(SPEC)
        announced = Event(); self.notifying_arrival_lock(t, announced)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with t._lock:
                pending = pool.submit(client.wrapper.error, 1, 550, 'anonymous')
                self.assertTrue(announced.wait(5))
                self.assertEqual(t._errors, [])  # Bookkeeping is still waiting.
                self.assert_writes_blocked(t, api)  # Reentrant later dispatch cannot slip through.
            pending.result(timeout=5)
        self.assertEqual(t._errors, [550])

    def test_callback_racing_sdk_and_queued_second_write_no_deadlock_or_escape(self):
        t, api, client = transport()
        inside, release, announced = Event(), Event(), Event()
        self.notifying_arrival_lock(t, announced)
        original = client.placeOrder
        def paused(orderId, contract, order):
            original(orderId, contract, order)
            inside.set()
            if not release.wait(5): raise AssertionError('offline coordination failed')
        client.placeOrder = paused
        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(t._dispatch_new, SPEC)
            try:
                self.assertTrue(inside.wait(5))
                error = pool.submit(client.wrapper.error, 1, 550, 'anonymous')
                self.assertTrue(announced.wait(5))
                second = pool.submit(t._dispatch_new, SPEC)
            finally:
                release.set()
            result = first.result(timeout=5)
            self.assertIs(result.state, _DispatchState.DISPATCHED)
            self.assertTrue(result.reconciliation_required)
            self.assertIs(second.result(timeout=5).state, _DispatchState.NOT_DISPATCHED)
            error.result(timeout=5)
        self.assertEqual(len([c for c in api.calls if c[0] == 'place']), 1)


def unknown_across_generation(operation):
    def test(self):
        t, api, client = transport(sdk(raises=True, observed=(7,)))
        method = {_Operation.PLACE_ORDER: lambda: t._dispatch_new(SPEC),
                  _Operation.CANCEL_ORDER: lambda: t._dispatch_cancel(7),
                  _Operation.GLOBAL_CANCEL: t._dispatch_global_cancel}[operation]
        result = method()
        self.assertIs(result.state, _DispatchState.OUTCOME_UNKNOWN)
        self.assertTrue(result.reconciliation_required)
        old_generation = t._generation
        t.disconnect(); self.assertTrue(t._requires_reconciliation())
        t._begin_generation(); self.assertGreater(t._generation, old_generation)
        self.assert_writes_blocked(t, api)
        api.clients[-1].wrapper.nextValidId(10)
        self.assert_writes_blocked(t, api)
        t._synchronize_open_orders()
        self.assertEqual(t._sync, 'COMPLETED')
        self.assert_writes_blocked(t, api)
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)
        self.assertTrue(t._requires_reconciliation())
    return test


for _operation in _Operation:
    setattr(ReconciliationSecurityCorrections, 'test_unknown_reconnect_' + _operation.value.lower(),
            unknown_across_generation(_operation))


def malformed_delayed_error(value):
    def test(self):
        t, api, client = transport(); t._dispatch_new(SPEC)
        # Modern shape must not fall back to treating errorTime as errorCode.
        client.wrapper.error(1, 1234, value, 'anonymous-private-marker')
        self.assertTrue(t._requires_reconciliation())
        self.assertEqual(t._errors, [])
        self.assert_writes_blocked(t, api)
        self.assertNotIn('anonymous-private-marker', repr(t.__dict__))
    return test


for _label, _value in (('bool', True), ('negative', -1), ('huge', 2**80),
                      ('float', 550.0), ('str', '550'), ('none', None)):
    setattr(ReconciliationSecurityCorrections, 'test_malformed_delayed_code_' + _label,
            malformed_delayed_error(_value))


class SocketPoisonSecurityCorrections(unittest.TestCase):
    def assert_poisoned(self, view, sink, *messages):
        previous = (list(sink.sent), sink.calls)
        for method in ('send', 'sendall'):
            for message in messages:
                with self.assertRaisesRegex(ReadOnlyError, '^PAPER_SOCKET_POISONED$'):
                    getattr(view, method)(message)
        self.assertEqual((sink.sent, sink.calls), previous)

    def failing_sink(self, method, *, continuation=False, exception=OSError):
        class Fail(Sink):
            def __init__(self):
                super().__init__(); self.calls = 0; self.fail = True
            def send(self, data):
                self.calls += 1
                if method == 'send' and self.fail:
                    self.sent.append(data[:2]); raise exception('anonymous-private-marker')
                return super().send(data)
            def sendall(self, data):
                self.calls += 1
                if method == 'sendall' and self.fail:
                    self.sent.append(data[:2]); raise exception('anonymous-private-marker')
                self.sent.append(data)
        sink = Fail(); view = _guarded_socket(sink, Fail)
        original = wire(203, payload=b'\x08\x01\x10\x02')
        message = original
        if continuation:
            sink.fail = False; sink.limit = 2
            sent = view.send(original)
            message = original[sent:]
            sink.fail = True; sink.limit = None
        return view, sink, original, message

    def test_invalid_sendall_return_poisoned(self):
        class Invalid(Sink):
            calls = 0
            def sendall(self, data): self.calls += 1; return 5
        sink = Invalid(); view = _guarded_socket(sink, Invalid)
        with self.assertRaisesRegex(ReadOnlyError, '^SDK_SOCKET_SEND_FAILED$'): view.sendall(wire(203))
        self.assert_poisoned(view, sink, wire(203), wire(204))

    def test_zero_send_preserves_entire_suffix_without_poison_or_loop(self):
        sink = Sink(); sink.limit = 0; view = _guarded_socket(sink, Sink)
        message = wire(203)
        self.assertEqual(view.send(message), 0)
        self.assertEqual(sink.sent, [b''])
        with self.assertRaises(ReadOnlyError): view.send(wire(204))
        self.assertEqual(view.send(message), 0)
        self.assertEqual(sink.sent, [b'', b''])
        sink.limit = None; self.assertEqual(view.send(message), len(message))
        view.sendall(wire(204))
        self.assertEqual(b''.join(sink.sent), message + wire(204))

    def test_zero_send_during_continuation_preserves_exact_pending(self):
        sink = Sink(); sink.limit = 2; view = _guarded_socket(sink, Sink)
        message = wire(203); sent = view.send(message)
        sink.limit = 0; self.assertEqual(view.send(message[sent:]), 0)
        for bad in (message[sent+1:], message[sent:] + b'x', wire(204)):
            with self.assertRaises(ReadOnlyError): view.sendall(bad)
        sink.limit = None; view.sendall(message[sent:])
        self.assertEqual(b''.join(sink.sent), message)

    def test_prevalidation_failure_neither_touches_sink_nor_poisons(self):
        sink = Sink(); view = _guarded_socket(sink, Sink)
        for bad in (wire(8), wire(208), wire(203)[:-1], b'junk'):
            with self.assertRaises(ReadOnlyError): view.sendall(bad)
        self.assertEqual(sink.sent, [])
        view.sendall(wire(203)); self.assertEqual(sink.sent, [wire(203)])

    def test_poison_permanent_but_close_and_shutdown_available(self):
        view, sink, original, message = self.failing_sink('send')
        with self.assertRaises(ReadOnlyError): view.send(message)
        view.close(); view.shutdown(2)
        with self.assertRaises(AttributeError): view.poisoned = False
        self.assertFalse(hasattr(view, 'reset'))
        self.assert_poisoned(view, sink, original, wire(204))

    def test_poisoned_socket_does_not_poison_new_socket_instance(self):
        view, sink, original, message = self.failing_sink('send')
        with self.assertRaises(ReadOnlyError): view.send(message)
        fresh = Sink(); fresh_view = _guarded_socket(fresh, Sink)
        fresh_view.sendall(original)
        self.assertEqual(fresh.sent, [original])
        self.assert_poisoned(view, sink, original)

    def test_concurrent_send_cannot_escape_first_socket_poison(self):
        view, sink, original, message = self.failing_sink('send')
        def attempt(data):
            try: view.send(data); return 'sent'
            except ReadOnlyError as error: return str(error)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt, (original, wire(204), original, wire(258))))
        self.assertNotIn('sent', results)
        self.assertEqual(results.count('SDK_SOCKET_SEND_FAILED'), 1)
        self.assertEqual(results.count('PAPER_SOCKET_POISONED'), 3)
        self.assertEqual(sink.calls, 1)


def partial_exception_poison(method, continuation, exception):
    def test(self):
        view, sink, original, message = self.failing_sink(method,
            continuation=continuation, exception=exception)
        if issubclass(exception, Exception):
            with self.assertRaisesRegex(ReadOnlyError, '^SDK_SOCKET_SEND_FAILED$'):
                getattr(view, method)(message)
        else:
            with self.assertRaises(exception):
                getattr(view, method)(message)
        self.assert_poisoned(view, sink, original, message, wire(204))
    return test


for _method in ('send', 'sendall'):
    for _continuation in (False, True):
        for _label, _exception in (('os_error', OSError), ('interrupt', KeyboardInterrupt)):
            setattr(SocketPoisonSecurityCorrections,
                'test_' + _method + ('_continuation_' if _continuation else '_initial_') + _label,
                partial_exception_poison(_method, _continuation, _exception))


def invalid_send_count_poison(value):
    def test(self):
        class Invalid(Sink):
            calls = 0
            def send(self, data): self.calls += 1; return value
        sink = Invalid(); view = _guarded_socket(sink, Invalid)
        with self.assertRaisesRegex(ReadOnlyError, '^SDK_SOCKET_SEND_FAILED$'): view.send(wire(203))
        self.assert_poisoned(view, sink, wire(203), wire(204))
    return test


for _label, _value in (('non_int', None), ('float', 1.0), ('bool', True),
                      ('negative', -1), ('above_length', 100000)):
    setattr(SocketPoisonSecurityCorrections, 'test_invalid_send_return_' + _label, invalid_send_count_poison(_value))


class LifetimeOrderIdCorrections(unittest.TestCase):
    def reconnect(self, t, api, next_id):
        t.disconnect()
        t._begin_generation()
        api.clients[-1].wrapper.nextValidId(next_id)
        t._synchronize_open_orders()

    def local_failure(self, t, client):
        checks = iter((True, False))
        client.isConnected = lambda: next(checks, True)
        result = t._dispatch_new(SPEC)
        self.assertIs(result.state, _DispatchState.NOT_DISPATCHED)
        self.assertFalse(t._requires_reconciliation())
        return result

    def test_lifetime_floor_alone_is_not_permission(self):
        t, api, client = transport()
        self.assertEqual(t._lifetime_order_floor, 10)
        t._allocate_order_id(t._generation)
        t._begin_generation()
        self.assertEqual(t._lifetime_order_floor, 11)
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)
        api.clients[-1].wrapper.nextValidId(5)
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)
        t._synchronize_open_orders()
        self.assertEqual(t._allocate_order_id(t._generation), 11)

    def test_reservation_commits_lifetime_floor_before_return(self):
        t, api, client = transport()
        self.assertEqual(t._allocate_order_id(t._generation), 10)
        self.assertEqual(t._lifetime_order_floor, 11)
        self.assertFalse(t._lifetime_order_ids_exhausted)

    def test_reset_preserves_floor_and_revokes_generation_readiness(self):
        t, api, client = transport()
        t._allocate_order_id(t._generation)
        t._reset()
        self.assertEqual(t._lifetime_order_floor, 11)
        self.assertFalse(t._initialized)
        self.assertEqual(t._sync, 'NOT_REQUESTED')
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)

    def test_max_local_failure_reconnect_stays_exhausted_no_sdk_write(self):
        t, api, client = transport(ready=MAX_ORDER_ID)
        self.assertEqual(self.local_failure(t, client).order_id, MAX_ORDER_ID)
        self.assertTrue(t._lifetime_order_ids_exhausted)
        self.reconnect(t, api, MAX_ORDER_ID)
        self.assertTrue(t._lifetime_order_ids_exhausted)
        self.assertTrue(t._exhausted)
        self.assertIsNone(t._boundary)
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.NOT_DISPATCHED)
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)
        self.assertFalse(any(c[0] == 'place' for c in api.calls))

    def test_max_clean_dispatch_reconnect_stays_exhausted(self):
        t, api, client = transport(ready=MAX_ORDER_ID)
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.DISPATCHED)
        self.reconnect(t, api, 0)
        self.assertTrue(t._lifetime_order_ids_exhausted)
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.NOT_DISPATCHED)
        self.assertEqual(len([c for c in api.calls if c[0] == 'place']), 1)

    def test_unknown_preserves_floor_and_barrier_across_reconnect(self):
        t, api, client = transport(sdk(raises=True))
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.OUTCOME_UNKNOWN)
        self.assertEqual(t._lifetime_order_floor, 11)
        self.reconnect(t, api, 5)
        self.assertEqual(t._lifetime_order_floor, 11)
        self.assertTrue(t._requires_reconciliation())
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.NOT_DISPATCHED)
        self.assertEqual(len([c for c in api.calls if c[0] == 'place']), 1)

    def test_max_unknown_preserves_lifetime_exhaustion_and_barrier(self):
        t, api, client = transport(sdk(raises=True), ready=MAX_ORDER_ID)
        self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.OUTCOME_UNKNOWN)
        self.reconnect(t, api, MAX_ORDER_ID)
        self.assertTrue(t._lifetime_order_ids_exhausted)
        self.assertTrue(t._requires_reconciliation())
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)

    def test_reset_cannot_clear_lifetime_exhaustion(self):
        t, api, client = transport(ready=MAX_ORDER_ID)
        t._allocate_order_id(t._generation)
        t._reset()
        self.assertTrue(t._lifetime_order_ids_exhausted)
        self.assertTrue(t._exhausted)
        for next_id in (0, 10, MAX_ORDER_ID):
            client.wrapper.nextValidId(next_id)
            t._synchronize_open_orders()
            with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)
            self.assertEqual(t._lifetime_order_floor, MAX_ORDER_ID)

    def test_accepted_broker_floor_cannot_move_backward_on_reconnect(self):
        t, api, client = transport(ready=20)
        self.reconnect(t, api, 5)
        self.assertEqual(t._allocate_order_id(t._generation), 20)

    def test_old_generation_callbacks_cannot_change_current_or_lifetime_state(self):
        t, api, old = transport()
        t._allocate_order_id(t._generation)
        t._begin_generation()
        old.wrapper.nextValidId(MAX_ORDER_ID)
        old.wrapper.openOrder(MAX_ORDER_ID, NS(), NS(), NS())
        old.wrapper.orderStatus(MAX_ORDER_ID, '', 0, 0, 0, 0, 0, 0, 0, '', 0)
        old.wrapper.openOrderEnd()
        self.assertEqual(t._lifetime_order_floor, 11)
        self.assertFalse(t._lifetime_order_ids_exhausted)
        self.assertFalse(t._initialized)
        self.assertEqual(t._sync, 'NOT_REQUESTED')
        api.clients[-1].wrapper.nextValidId(5)
        t._synchronize_open_orders()
        self.assertEqual(t._allocate_order_id(t._generation), 11)

    def test_invalid_callback_does_not_change_lifetime_history(self):
        for kind in ('next', 'observed'):
            for value in (True, -1, MAX_ORDER_ID + 1, 2**80):
                with self.subTest(kind=kind, value=value):
                    t, api, client = transport()
                    t._allocate_order_id(t._generation)
                    t._callback(t._generation, kind, value)
                    self.assertEqual(t._lifetime_order_floor, 11)
                    self.assertFalse(t._lifetime_order_ids_exhausted)
                    self.reconnect(t, api, 5)
                    self.assertEqual(t._allocate_order_id(t._generation), 11)

    def test_concurrent_reconnect_and_allocations_never_reissue_consumed_id(self):
        t, api, client = transport()
        def reconnect_many():
            for _ in range(20):
                with t._lock:
                    self.reconnect(t, api, 5)
        def allocate_many():
            ids = []
            for _ in range(100):
                with t._lock:
                    ids.append(t._allocate_order_id(t._generation))
            return ids
        with ThreadPoolExecutor(max_workers=3) as pool:
            reset = pool.submit(reconnect_many)
            workers = [pool.submit(allocate_many) for _ in range(2)]
            ids = [value for worker in workers for value in worker.result(timeout=5)]
            reset.result(timeout=5)
        self.assertEqual(len(ids), 200)
        self.assertEqual(len(set(ids)), 200)
        self.assertEqual(set(ids), set(range(10, 210)))
        self.assertEqual(t._lifetime_order_floor, 210)

    def test_lifetime_history_has_no_public_constructor_or_reset_surface(self):
        t, api, client = transport()
        self.assertFalse(any(not name.startswith('_') and ('floor' in name or 'exhaust' in name
                             or 'reset' in name) for name in dir(t)))
        with self.assertRaises(TypeError):
            PaperTWSTransport(t._config, lifetime_order_floor=0)


def lifetime_reconnect_after_reservation(next_id, clean):
    def test(self):
        t, api, client = transport()
        result = t._dispatch_new(SPEC) if clean else self.local_failure(t, client)
        self.assertEqual(result.order_id, 10)
        self.assertIs(result.state, _DispatchState.DISPATCHED if clean else _DispatchState.NOT_DISPATCHED)
        self.assertEqual(t._lifetime_order_floor, 11)
        self.reconnect(t, api, next_id)
        self.assertEqual(t._allocate_order_id(t._generation), max(11, next_id))
        self.assertNotEqual(t._boundary, 10)
        self.assertEqual(len([c for c in api.calls if c[0] == 'place']), int(clean))
    return test


for _clean in (False, True):
    for _next_id in (5, 10, 20):
        setattr(LifetimeOrderIdCorrections, 'test_' + ('clean' if _clean else 'local_failure')
                + '_reconnect_next_' + str(_next_id), lifetime_reconnect_after_reservation(_next_id, _clean))


def lifetime_observed_id(kind, order_id):
    def test(self):
        t, api, client = transport()
        if kind == 'open': client.wrapper.openOrder(order_id, NS(), NS(), NS())
        else: client.wrapper.orderStatus(order_id, '', 0, 0, 0, 0, 0, 0, 0, '', 0)
        self.reconnect(t, api, 10)
        if order_id == MAX_ORDER_ID:
            self.assertTrue(t._lifetime_order_ids_exhausted)
            with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)
            self.assertIs(t._dispatch_new(SPEC).state, _DispatchState.NOT_DISPATCHED)
        else:
            self.assertGreaterEqual(t._lifetime_order_floor, order_id + 1)
            self.assertEqual(t._allocate_order_id(t._generation), order_id + 1)
        self.assertFalse(any(c[0] == 'place' for c in api.calls))
    return test


for _kind in ('open', 'status'):
    for _order_id in (100, MAX_ORDER_ID):
        setattr(LifetimeOrderIdCorrections, 'test_observed_' + _kind + '_' + str(_order_id)
                + '_survives_reconnect', lifetime_observed_id(_kind, _order_id))


class InterruptionSafetyCorrections(unittest.TestCase):
    def assert_blocked(self, t, api):
        before = (len(api.calls), t._lifetime_order_floor)
        for result in (t._dispatch_new(SPEC), t._dispatch_cancel(7), t._dispatch_global_cancel()):
            self.assertIs(result.state, _DispatchState.NOT_DISPATCHED)
            self.assertTrue(result.reconciliation_required)
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)
        self.assertEqual((len(api.calls), t._lifetime_order_floor), before)

    def test_latch_precedes_optional_result_construction_failure(self):
        t, api, client = transport(sdk(raises=True))
        with patch('tradingbot_broker.ibkr_paper_transport._DispatchResult',
                   side_effect=KeyboardInterrupt('anonymous-reporting-failure')):
            with self.assertRaises(KeyboardInterrupt): t._dispatch_new(SPEC)
        self.assertTrue(t._requires_reconciliation())
        self.assertEqual(t._lifetime_order_floor, 11)
        self.assert_blocked(t, api)


def sdk_invocation_interruption(operation, exception_type):
    def test(self):
        api = sdk(observed=(7,))
        interruption = exception_type('anonymous-interruption-marker')
        # Keep exactly qualified signatures. The SDK double emits only to its
        # in-memory sink, then interrupts while still inside the named method.
        original = getattr(api.Client, {_Operation.PLACE_ORDER: 'placeOrder',
            _Operation.CANCEL_ORDER: 'cancelOrder', _Operation.GLOBAL_CANCEL: 'reqGlobalCancel'}[operation])
        if operation is _Operation.PLACE_ORDER:
            def interrupted(self, orderId, contract, order):
                original(self, orderId, contract, order)
                raise interruption
            api.Client.placeOrder = interrupted
        elif operation is _Operation.CANCEL_ORDER:
            def interrupted(self, orderId, orderCancel):
                original(self, orderId, orderCancel)
                raise interruption
            api.Client.cancelOrder = interrupted
        else:
            def interrupted(self, orderCancel):
                original(self, orderCancel)
                raise interruption
            api.Client.reqGlobalCancel = interrupted
        t, api, client = transport(api)
        method = {_Operation.PLACE_ORDER: lambda: t._dispatch_new(SPEC),
                  _Operation.CANCEL_ORDER: lambda: t._dispatch_cancel(7),
                  _Operation.GLOBAL_CANCEL: t._dispatch_global_cancel}[operation]
        with self.assertRaises(exception_type) as caught: method()
        self.assertIs(caught.exception, interruption)
        self.assertTrue(t._requires_reconciliation())
        self.assertTrue(t._write_invocation_started)
        self.assertEqual(t._lifetime_order_floor, 11 if operation is _Operation.PLACE_ORDER else 10)
        self.assertNotIn('anonymous-interruption-marker', repr(t.__dict__))
        self.assert_blocked(t, api)
        # A different thread can take the dispatch lock and process callbacks.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(client.wrapper.openOrder, 100, NS(), NS(), NS()).result(timeout=5)
            pool.submit(t.disconnect).result(timeout=5)
            pool.submit(t._begin_generation).result(timeout=5)
        api.clients[-1].wrapper.nextValidId(10)
        t._synchronize_open_orders()
        self.assertEqual(t._lifetime_order_floor, 101)
        self.assert_blocked(t, api)
    return test


for _operation in _Operation:
    for _exception in (KeyboardInterrupt, SystemExit, GeneratorExit):
        setattr(InterruptionSafetyCorrections, 'test_sdk_' + _operation.value.lower()
                + '_' + _exception.__name__, sdk_invocation_interruption(_operation, _exception))


def before_invocation_interruption(exception_type, after_reservation):
    def test(self):
        t, api, client = transport()
        interruption = exception_type('anonymous-preflight-marker')
        if after_reservation:
            checks = iter((True,))
            def state():
                try: return next(checks)
                except StopIteration: raise interruption
            client.isConnected = state
        else:
            def build(spec): raise interruption
            t._build_order = build
        with self.assertRaises(exception_type) as caught: t._dispatch_new(SPEC)
        self.assertIs(caught.exception, interruption)
        self.assertFalse(t._write_invocation_started)
        self.assertFalse(t._requires_reconciliation())
        self.assertEqual(t._lifetime_order_floor, 11 if after_reservation else 10)
        self.assertFalse(any(c[0] == 'place' for c in api.calls))
        t._begin_generation()
        api.clients[-1].wrapper.nextValidId(5)
        t._synchronize_open_orders()
        self.assertEqual(t._allocate_order_id(t._generation), 11 if after_reservation else 10)
        self.assertNotIn('anonymous-preflight-marker', repr(t.__dict__))
    return test


for _exception in (KeyboardInterrupt, SystemExit, GeneratorExit):
    for _after in (False, True):
        setattr(InterruptionSafetyCorrections, 'test_pre_invocation_' + _exception.__name__
                + ('_reserved' if _after else '_unreserved'), before_invocation_interruption(_exception, _after))


def regular_invocation_exception(operation):
    def test(self):
        t, api, client = transport(sdk(raises=True, observed=(7,)))
        result = {_Operation.PLACE_ORDER: lambda: t._dispatch_new(SPEC),
                  _Operation.CANCEL_ORDER: lambda: t._dispatch_cancel(7),
                  _Operation.GLOBAL_CANCEL: t._dispatch_global_cancel}[operation]()
        self.assertIs(result.state, _DispatchState.OUTCOME_UNKNOWN)
        self.assertTrue(result.reconciliation_required)
        self.assert_blocked(t, api)
        self.assertNotIn('anonymous private error', repr(result))
        self.assertFalse(hasattr(result, 'acknowledged'))
    return test


for _operation in _Operation:
    setattr(InterruptionSafetyCorrections, 'test_regular_exception_' + _operation.value.lower(),
            regular_invocation_exception(_operation))


class SocketInterruptionDispatchCorrections(unittest.TestCase):
    def raw_failure_fixture(self, path, error, continuation=False):
        api = sdk(observed=(7,))
        if path == 'sendall' and not continuation:
            def send_all(self, msg): return self.socket.sendall(msg)
            api.Connection.sendMsg = send_all
        t, api, client = transport(api)
        sink = api.sinks[-1]
        calls = []
        if continuation:
            def send_pending(self, msg):
                sent = self.socket.send(msg)
                if sent == len(msg): return sent
                return getattr(self.socket, path)(msg[sent:])
            api.Connection.sendMsg = send_pending
        def raw_send(data):
            calls.append(('send', data))
            sink.sent.append(data[:2])
            if continuation and len(calls) == 1: return 2
            raise error
        def raw_sendall(data):
            calls.append(('sendall', data))
            sink.sent.append(data[:2])
            raise error
        sink.send, sink.sendall = raw_send, raw_sendall
        return t, api, client, sink, calls

    @staticmethod
    def invoke(t, operation):
        if operation is _Operation.PLACE_ORDER: return t._dispatch_new(SPEC)
        if operation is _Operation.CANCEL_ORDER: return t._dispatch_cancel(7)
        return t._dispatch_global_cancel()

    def assert_secured_after_failure(self, t, api, client, sink, calls, operation, continuation):
        self.assertTrue(t._requires_reconciliation())
        self.assertEqual(t._lifetime_order_floor,
                         11 if operation is _Operation.PLACE_ORDER else 10)
        self.assertEqual(len(calls), 2 if continuation else 1)  # No implicit retry.
        view = client.conn.socket  # Offline fixture, never application exposure.
        before = (list(calls), list(sink.sent), list(api.calls))
        for method in ('send', 'sendall'):
            for data in (calls[-1][1], wire(203), wire(204), wire(258)):
                with self.assertRaisesRegex(ReadOnlyError, '^PAPER_SOCKET_POISONED$'):
                    getattr(view, method)(data)
        for result in (t._dispatch_new(SPEC), t._dispatch_cancel(7), t._dispatch_global_cancel()):
            self.assertIs(result.state, _DispatchState.NOT_DISPATCHED)
            self.assertTrue(result.reconciliation_required)
        with self.assertRaises(ReadOnlyError): t._allocate_order_id(t._generation)
        self.assertEqual((calls, sink.sent, api.calls), before)
        self.assertNotIn('anonymous-socket-marker', repr(t.__dict__))
        # Exercise socket/transport locks from a different thread after propagation.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(view.send, wire(203))
            with self.assertRaisesRegex(ReadOnlyError, '^PAPER_SOCKET_POISONED$'):
                future.result(timeout=5)
            pool.submit(view.close).result(timeout=5)
            pool.submit(view.shutdown, 2).result(timeout=5)
            pool.submit(client.wrapper.openOrder, 100, NS(), NS(), NS()).result(timeout=5)
            self.assertEqual(t._lifetime_order_floor, 101)
            pool.submit(t.disconnect).result(timeout=5)
            pool.submit(t._begin_generation).result(timeout=5)
        self.assertTrue(t._requires_reconciliation())
        api.clients[-1].wrapper.nextValidId(10)
        t._synchronize_open_orders()
        before_retries = list(api.calls)
        for result in (t._dispatch_new(SPEC), t._dispatch_cancel(7), t._dispatch_global_cancel()):
            self.assertIs(result.state, _DispatchState.NOT_DISPATCHED)
            self.assertTrue(result.reconciliation_required)
        self.assertEqual(api.calls, before_retries)
        self.assertEqual(len([c for c in api.calls if c[0] in ('place', 'cancel', 'global')]), 1)
        self.assertEqual(calls, before[0])
        self.assertEqual(sink.sent, before[1])
        with self.assertRaisesRegex(ReadOnlyError, '^PAPER_SOCKET_POISONED$'): view.sendall(wire(203))
        self.assertEqual(t._lifetime_order_floor, 101)


def complete_socket_interruption(operation, path, exception_type, continuation):
    def test(self):
        if exception_type is SystemExit: interruption = SystemExit(37)
        elif exception_type is GeneratorExit: interruption = GeneratorExit()
        else: interruption = KeyboardInterrupt('anonymous-socket-marker')
        t, api, client, sink, calls = self.raw_failure_fixture(path, interruption, continuation)
        with self.assertRaises(exception_type) as caught: self.invoke(t, operation)
        self.assertIs(caught.exception, interruption)
        self.assertEqual(caught.exception.args, interruption.args)
        if exception_type is SystemExit: self.assertEqual(caught.exception.code, 37)
        self.assert_secured_after_failure(t, api, client, sink, calls, operation, continuation)
    return test


for _operation in _Operation:
    for _path in ('send', 'sendall'):
        for _exception in (KeyboardInterrupt, SystemExit, GeneratorExit):
            for _continuation in (False, True):
                setattr(SocketInterruptionDispatchCorrections,
                    'test_' + _operation.value.lower() + '_' + _path + '_' + _exception.__name__
                    + ('_pending' if _continuation else '_initial'),
                    complete_socket_interruption(_operation, _path, _exception, _continuation))


def complete_socket_regular_exception(operation, path):
    def test(self):
        for exception_type in (OSError, TimeoutError, RuntimeError):
            with self.subTest(exception_type=exception_type.__name__):
                error = exception_type('anonymous-socket-marker')
                t, api, client, sink, calls = self.raw_failure_fixture(path, error)
                result = self.invoke(t, operation)
                self.assertIs(result.state, _DispatchState.OUTCOME_UNKNOWN)
                self.assertTrue(result.reconciliation_required)
                self.assertNotIn('anonymous-socket-marker', repr(result))
                self.assertEqual(result.order_id,
                    10 if operation is _Operation.PLACE_ORDER else
                    (7 if operation is _Operation.CANCEL_ORDER else None))
                self.assertFalse(hasattr(result, 'acknowledged'))
                self.assert_secured_after_failure(t, api, client, sink, calls, operation, False)
    return test


for _operation in _Operation:
    for _path in ('send', 'sendall'):
        setattr(SocketInterruptionDispatchCorrections,
            'test_ordinary_' + _operation.value.lower() + '_' + _path,
            complete_socket_regular_exception(_operation, _path))


if __name__ == '__main__':
    unittest.main()
