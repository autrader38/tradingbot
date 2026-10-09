"""Reviewed transport defects: SDK-shaped offline evidence, no network."""

import io
import struct
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import timedelta
from threading import Event, Thread
from unittest.mock import patch

from tests.readonly_fixtures import AT, sdk, transport, broker
from tradingbot_broker.broker import BrokerOperationError
from tradingbot_broker.ibkr_diagnostics import main
from tradingbot_broker.ibkr_readonly import ReadOnlyTWSTransport, TWSReadOnlyConfig
from tradingbot_broker.ibkr_readonly_wire import guarded_socket
from tradingbot_broker.models import ConnectionStatus
from tradingbot_broker.readonly_models import ReadOnlyError


def frame(body):
    return struct.pack('!I', len(body)) + body


class WireBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.t, self.api, _ = transport()
        self.t.connect()
        self.client = self.api.clients[-1]  # Fixture owns raw SDK; application does not.
        self.before = tuple(self.api.calls)
        self.addCleanup(self.t.disconnect)

    def blocked(self, operation):
        with self.assertRaises(ReadOnlyError): operation()
        self.assertEqual(tuple(self.api.calls), self.before)

    def test_base_sdk_send_cannot_bypass_socket_guard(self):
        self.blocked(lambda: self.api.Client.sendMsg(self.client, '3\0forbidden'))

    def test_raw_connection_sender_cannot_bypass(self):
        self.blocked(lambda: self.client.conn.sendMsg(frame(b'3\0forbidden')))

    def test_base_connection_sender_cannot_bypass(self):
        self.blocked(lambda: self.api.Connection.sendMsg(self.client.conn, frame(b'4\0forbidden')))

    def test_socket_send_cannot_bypass(self):
        self.blocked(lambda: self.client.conn.socket.send(frame(b'3\0forbidden')))

    def test_socket_sendall_cannot_bypass(self):
        self.blocked(lambda: self.client.conn.socket.sendall(frame(b'4\0forbidden')))

    def test_base_place_order_cannot_bypass(self):
        self.blocked(lambda: self.api.Client.placeOrder(self.client, 9, object(), object()))

    def test_base_cancel_order_cannot_bypass(self):
        self.blocked(lambda: self.api.Client.cancelOrder(self.client, 9))

    def test_modify_encoding_cannot_bypass(self):
        self.blocked(lambda: self.api.Client.sendMsg(self.client, '3\0existing-order\0replacement'))

    def test_unknown_opcode_cannot_reach_sink(self):
        self.blocked(lambda: self.api.Client.sendMsg(self.client, '99999\0unsupported'))

    def test_invalid_frame_cannot_reach_sink(self):
        self.blocked(lambda: self.client.conn.socket.send(b'3\0unframed'))

    def test_missing_legacy_field_terminator_rejected(self):
        self.blocked(lambda: self.client.conn.socket.send(frame(str(self.api.OUT.REQ_CURRENT_TIME).encode())))

    def test_combined_read_and_write_frames_rejected(self):
        allowed = frame(str(self.api.OUT.REQ_CURRENT_TIME).encode() + b'\0')
        self.blocked(lambda: self.client.conn.socket.send(allowed + frame(b'3\0forbidden')))

    def test_allowed_base_read_send_reaches_sink(self):
        body = str(self.api.OUT.REQ_CURRENT_TIME).encode() + b'\0'
        self.api.Client.sendMsg(self.client, body.decode())
        self.assertIn(('socket-send', frame(body)), self.api.calls)

    def test_application_has_no_raw_sdk_capabilities(self):
        for name in ('Client', 'Connection', 'SocketType'):
            self.assertFalse(hasattr(self.t._api, name))
        for name in ('conn', 'wrapper', 'sendMsg', 'placeOrder', 'cancelOrder', '__dict__'):
            self.assertFalse(hasattr(self.t._client, name))
        for name in ('fileno', 'detach', 'dup', 'makefile', '__dict__'):
            self.assertFalse(hasattr(self.client.conn.socket, name))

    def test_named_read_method_cannot_be_generic_dispatcher(self):
        self.blocked(lambda: self.t._client.reqCurrentTime(_method='placeOrder'))

    def test_protocol_negotiation_only_once(self):
        payload = b'API\0' + frame(b'v100..200')
        self.client.conn.socket.send(payload)
        self.before = tuple(self.api.calls)
        self.blocked(lambda: self.client.conn.socket.send(payload))

    def test_handshake_cannot_carry_order_body(self):
        self.blocked(lambda: self.client.conn.socket.send(b'API\0' + frame(b'3\0forbidden')))

    def test_partial_send_requires_identical_remaining_bytes(self):
        sent = []
        class Socket:
            def send(self, data):
                sent.append(data)
                return 2 if len(sent) == 1 else len(data)
        view = guarded_socket(Socket(), Socket, {49})
        allowed = frame(b'49\0')
        view.send(allowed)
        with self.assertRaises(ReadOnlyError): view.send(frame(b'3\0forbidden'))
        self.assertEqual(sent, [allowed])
        view.send(allowed[2:])
        self.assertEqual(sent, [allowed, allowed[2:]])


class LifecycleOwnershipTests(unittest.TestCase):
    def launch(self, operation):
        result = []
        def run():
            try: result.append(operation())
            except Exception as error: result.append(error)
        thread = Thread(target=run)
        thread.start()
        self.addCleanup(lambda: thread.join(5))
        return thread, result

    def gated(self):
        api = sdk()
        entered, release = Event(), Event()
        original = api.Client.connect
        def connect(client, *args):
            if len(api.clients) == 1:
                entered.set()
                if not release.wait(5): raise RuntimeError('fixture wait timeout')
            return original(client, *args)
        api.Client.connect = connect
        t, _, clock = transport(api, timeout_seconds=3)
        self.addCleanup(t.disconnect)
        self.addCleanup(release.set)
        return t, api, clock, entered, release

    def test_overlapping_connects_serialize_and_do_not_mix_data(self):
        t, api, _, entered, release = self.gated()
        first, a = self.launch(t.connect)
        self.assertTrue(entered.wait(1))
        started = Event()
        def second_connect():
            started.set()
            return t.connect()
        second, b = self.launch(second_connect)
        self.assertTrue(started.wait(1))
        self.assertFalse(t._lifecycle.acquire(blocking=False))
        release.set()
        first.join(2); second.join(2)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(len(api.clients), 1)
        self.assertIs(a[0], b[0])

    def test_connect_disconnect_overlap_cannot_restore_after_disconnect(self):
        t, _, _, entered, release = self.gated()
        first, result = self.launch(t.connect)
        self.assertTrue(entered.wait(1))
        second, stopped = self.launch(t.disconnect)
        release.set()
        first.join(2); second.join(2)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(len(result), 1)
        self.assertEqual(stopped, [None])
        self.assertFalse(t.connected)
        self.assertIsNone(t._snapshot)

    def test_cached_connect_cannot_extend_expiry(self):
        t, _, clock = transport()
        self.addCleanup(t.disconnect)
        first = t.connect()
        clock.advance(5)
        second = t.connect()
        self.assertIs(first, second)
        self.assertEqual(first.account.valid_until, second.account.valid_until)

    def test_new_sessions_have_distinct_request_ids(self):
        t, api, _ = transport()
        self.addCleanup(t.disconnect)
        t.connect(); t.disconnect(); t.connect()
        ids = [c[1] for c in api.calls if c[0] in ('summary', 'executions')]
        self.assertEqual(len(ids), len(set(ids)))

    def test_obsolete_callbacks_cannot_disconnect_new_session(self):
        t, api, _ = transport()
        self.addCleanup(t.disconnect)
        t.connect()
        old = api.clients[0].wrapper
        t.disconnect()
        current = t.connect()
        old.connectionClosed(); old.positionEnd(); old.openOrderEnd()
        self.assertTrue(t.connected)
        self.assertIs(t._snapshot, current)
        self.assertEqual(t.drain_events(AT), ())

    def test_obsolete_worker_cleanup_does_not_disconnect_new_client(self):
        api = sdk()
        t, _, _ = transport(api)
        self.addCleanup(t.disconnect)
        t.connect()
        old_client = t._client
        old_generation = t._generation
        t.disconnect(); current = t.connect()
        t._run(old_client, old_generation)
        self.assertTrue(t.connected)
        self.assertTrue(api.clients[-1].connected)
        self.assertFalse(api.clients[0].connected)
        self.assertIs(t._snapshot, current)


class CompletionOwnershipTests(unittest.TestCase):
    def unfinished(self, collection, premature=False):
        api = sdk(server_version=100)
        method, marker = {'positions': ('reqPositions', 'positionEnd'),
                          'orders': ('reqAllOpenOrders', 'openOrderEnd')}[collection]
        if premature:
            original = api.Client.connect
            def connect(client, *args):
                original(client, *args)
                getattr(client.wrapper, marker)()
            api.Client.connect = connect
        setattr(api.Client, method, lambda client: None)
        t, _, _ = transport(api, timeout_seconds=1)
        with self.assertRaisesRegex(ReadOnlyError, 'IB_GATEWAY_TIMEOUT'): t.connect()
        self.assertIsNone(t._snapshot)
        self.assertFalse(t.connected)

    def test_premature_position_end_cannot_authorize_snapshot(self):
        self.unfinished('positions', True)

    def test_premature_order_end_cannot_authorize_snapshot(self):
        self.unfinished('orders', True)

    def test_unfinished_position_request_is_not_zero(self):
        self.unfinished('positions')

    def test_unfinished_order_request_is_not_zero(self):
        self.unfinished('orders')

    def test_proper_empty_positions_are_valid(self):
        t, _, _ = transport(sdk(position=False))
        self.addCleanup(t.disconnect)
        self.assertEqual(t.connect().account.positions, ())

    def test_proper_empty_orders_are_valid(self):
        api = sdk(server_version=100)
        api.Client.reqAllOpenOrders = lambda client: client.wrapper.openOrderEnd()
        t, _, _ = transport(api)
        self.addCleanup(t.disconnect)
        self.assertEqual(t.connect().working_orders, ())

    def test_all_unrequested_completion_markers_ignored(self):
        t, _, _ = transport()
        generation = t._generation
        t._summary_id, t._execution_id = 102, 103
        for kind, args in (('positions_end', ()), ('orders_end', ()),
                           ('summary_end', (102,)), ('executions_end', (103,)),
                           ('completed_end', ()), ('accounts', ('anonymous-fixture',)),
                           ('time', (int(AT.timestamp()),))):
            t._callback(generation, kind, *args)
        self.assertEqual(t._done, set())
        self.assertIsNone(t._last_observed)

    def test_stale_generation_cannot_complete_active_collection(self):
        t, _, _ = transport()
        t._phase['positions'] = 'ACTIVE'
        t._callback(t._generation - 1, 'positions_end')
        self.assertEqual(t._phase['positions'], 'ACTIVE')
        self.assertNotIn('positions', t._done)

    def test_mismatched_request_id_cannot_complete(self):
        t, _, _ = transport()
        t._summary_id = 102
        t._phase['summary'] = 'ACTIVE'
        t._callback(t._generation, 'summary_end', 101)
        self.assertNotIn('summary', t._done)


class DrainTimingTests(unittest.TestCase):
    def test_disconnect_after_timestamp_capture_safely_invalidates_facade(self):
        b, t, api, clock = broker()
        b.connect()
        captured = clock()
        api.clients[-1].wrapper.connectionClosed()
        self.assertEqual(b.poll(captured), ConnectionStatus.DISCONNECTED)
        self.assertIsNone(b._account)
        with self.assertRaises(BrokerOperationError): b.account_summary(captured)
        self.assertFalse(t.connected)

    def test_received_timestamp_never_precedes_callback_occurrence(self):
        t, api, clock = transport()
        t.connect()
        self.addCleanup(t.disconnect)
        captured = clock()
        clock.advance(1)
        api.clients[-1].wrapper.connectionClosed()
        event, = t.drain_events(captured)
        self.assertGreaterEqual(event.received_at, event.occurred_at)
        self.assertGreater(event.occurred_at, captured)

    def test_disconnect_in_drain_window_is_processed_without_exception(self):
        b, t, api, _ = broker()
        b.connect()
        drain = t.drain_events
        def race(at):
            api.clients[-1].wrapper.connectionClosed()
            return drain(at)
        with patch.object(t, 'drain_events', side_effect=race):
            self.assertEqual(b.poll(), ConnectionStatus.DISCONNECTED)
        self.assertIsNone(b._account)

    def test_diagnostics_drain_race_reports_failure_without_traceback(self):
        b, t, api, _ = broker()
        drain = t.drain_events
        def race(at):
            if api.clients: api.clients[-1].wrapper.connectionClosed()
            return drain(at)
        out, err = io.StringIO(), io.StringIO()
        with patch('tradingbot_broker.ibkr_diagnostics.ReadOnlyTWSTransport', return_value=t), \
             patch('tradingbot_broker.ibkr_diagnostics.ReadOnlyIBKRBroker', return_value=b), \
             patch.object(t, 'drain_events', side_effect=race), redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(), 1)
        self.assertIn('UNAVAILABLE', out.getvalue())
        self.assertEqual(err.getvalue(), '')
        self.assertNotIn('Traceback', out.getvalue())
        self.assertIsNone(b._account)


class SDKRedactionTests(unittest.TestCase):
    sensitive = 'fixture-sensitive-account-and-token-text'

    def raises_sensitive(self, *args, **kwargs):
        raise RuntimeError(self.sensitive)

    def test_sdk_constructor_failure_sanitized(self):
        api = sdk()
        api.Client.__init__ = self.raises_sensitive
        t, _, _ = transport(api)
        with self.assertRaisesRegex(ReadOnlyError, '^SDK_CLIENT_CONSTRUCTION_FAILED$') as caught:
            t.connect()
        self.assertNotIn(self.sensitive, str(caught.exception))
        self.assertIsNone(t._client)

    def test_sdk_metadata_initialization_failure_sanitized(self):
        class API:
            @property
            def OUT(self): raise RuntimeError('fixture-sensitive-account-and-token-text')
        with patch('tradingbot_broker.ibkr_readonly._load_official_api', return_value=API()):
            with self.assertRaisesRegex(ReadOnlyError, '^SDK_INITIALIZATION_FAILED$'):
                ReadOnlyTWSTransport(TWSReadOnlyConfig())

    def test_sdk_connection_state_failure_invalidates_facade(self):
        b, t, api, _ = broker()
        b.connect()
        with patch.object(api.Client, 'isConnected', self.raises_sensitive):
            with self.assertRaisesRegex(ReadOnlyError, '^SDK_ISCONNECTED_FAILED$'): b.poll()
        self.assertEqual(b._connection, ConnectionStatus.DISCONNECTED)
        self.assertIsNone(b._account)
        self.assertNotIn(self.sensitive, repr(b.audit))
        self.assertNotIn(self.sensitive, repr(t.diagnostics))

    def test_shutdown_failure_sanitized_after_state_invalidation(self):
        b, t, api, _ = broker()
        b.connect()
        with patch.object(api.Client, 'disconnect', self.raises_sensitive):
            with self.assertRaisesRegex(ReadOnlyError, '^SDK_DISCONNECT_FAILED$'): b.disconnect()
        self.assertIsNone(t._client)
        self.assertIsNone(t._snapshot)
        self.assertEqual(b._connection, ConnectionStatus.DISCONNECTED)
        self.assertIsNone(b._account)

    def test_read_request_error_sanitized(self):
        api = sdk()
        api.Client.reqPositions = self.raises_sensitive
        t, _, _ = transport(api)
        with self.assertRaisesRegex(ReadOnlyError, '^SDK_REQPOSITIONS_FAILED$'): t.connect()
        self.assertNotIn(self.sensitive, repr(t.diagnostics))

    def test_execution_filter_constructor_error_sanitized(self):
        api = sdk()
        api.ExecutionFilter = self.raises_sensitive
        t, _, _ = transport(api)
        with self.assertRaisesRegex(ReadOnlyError, '^SDK_EXECUTION_FILTER_FAILED$'): t.connect()

    def test_diagnostics_constructor_error_has_no_raw_text_or_traceback(self):
        out, err = io.StringIO(), io.StringIO()
        with patch('tradingbot_broker.ibkr_diagnostics.ReadOnlyTWSTransport', side_effect=self.raises_sensitive), \
             redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(), 1)
        self.assertIn('READ_ONLY_DIAGNOSTIC_FAILED', out.getvalue())
        self.assertNotIn(self.sensitive, out.getvalue() + err.getvalue())
        self.assertNotIn('Traceback', out.getvalue() + err.getvalue())

    def test_diagnostics_sdk_shutdown_error_sanitized(self):
        b, t, api, _ = broker()
        out, err = io.StringIO(), io.StringIO()
        with patch('tradingbot_broker.ibkr_diagnostics.ReadOnlyTWSTransport', return_value=t), \
             patch('tradingbot_broker.ibkr_diagnostics.ReadOnlyIBKRBroker', return_value=b), \
             patch.object(api.Client, 'disconnect', self.raises_sensitive), \
             redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(), 1)
        self.assertIn('SDK_DISCONNECT_FAILED', out.getvalue())
        self.assertNotIn(self.sensitive, out.getvalue() + err.getvalue())
        self.assertEqual(err.getvalue(), '')

    def test_diagnostics_sdk_state_error_sanitized(self):
        b, t, api, _ = broker()
        b.connect()
        out, err = io.StringIO(), io.StringIO()
        with patch('tradingbot_broker.ibkr_diagnostics.ReadOnlyTWSTransport', return_value=t), \
             patch('tradingbot_broker.ibkr_diagnostics.ReadOnlyIBKRBroker', return_value=b), \
             patch.object(api.Client, 'isConnected', self.raises_sensitive), \
             redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(), 1)
        self.assertIn('SDK_ISCONNECTED_FAILED', out.getvalue())
        self.assertNotIn(self.sensitive, out.getvalue() + err.getvalue())
        self.assertEqual(err.getvalue(), '')
        self.assertIsNone(b._account)

    def test_socket_exceptions_sanitized(self):
        class Socket:
            def send(self, data): raise RuntimeError('fixture-sensitive-account-and-token-text')
        view = guarded_socket(Socket(), Socket, {49})
        with self.assertRaisesRegex(ReadOnlyError, '^SDK_SOCKET_SEND_FAILED$'): view.send(frame(b'49\0'))


if __name__ == '__main__':
    unittest.main()
