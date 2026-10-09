"""Negative boundaries and genuine SDK-shaped read callback normalization."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal
import io
import logging
import subprocess
import sys
from threading import Event
from contextlib import redirect_stdout
from types import SimpleNamespace
import unittest
from unittest.mock import patch, PropertyMock

from tradingbot_broker.broker import BrokerOperationError
from tradingbot_broker.models import (BrokerEvent, BrokerReason as R, ConnectionStatus,
                                     EventKind, OrderState, OrderType, Side, TradingMode)
from tradingbot_broker.ibkr_readonly import (ReadOnlyTWSTransport, TWSReadOnlyConfig,
                                          execution_timestamp)
from tradingbot_broker.ibkr_readonly_broker import ReadOnlyIBKRBroker
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError, broker_decimal
from tests.readonly_fixtures import AT, D, Clock, broker, sdk, transport
from tests.broker_fixtures import request


class ConfigTests(unittest.TestCase):
    def test_defaults_are_settings_not_paper_attestation(self):
        self.assertEqual(TWSReadOnlyConfig().port, 4002)
        self.assertTrue(TWSReadOnlyConfig().read_only)
    def test_environment(self):
        c = TWSReadOnlyConfig.from_environment({'IBKR_PORT': '4001', 'IBKR_CLIENT_ID': '19', 'IBKR_TIMEZONE': 'UTC'})
        self.assertEqual((c.port, c.client_id, c.broker_timezone), (4001, 19, 'UTC'))
    def test_missing_dependency_is_sanitized(self):
        with patch('tradingbot_broker.ibkr_readonly.importlib.import_module', side_effect=ImportError('private')):
            with self.assertRaisesRegex(ReadOnlyError, '^OFFICIAL_IBAPI_DEPENDENCY_UNAVAILABLE$'):
                ReadOnlyTWSTransport(TWSReadOnlyConfig())
    def test_module_import_does_not_load_sdk(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys; import tradingbot_broker.ibkr_readonly; "
            "assert not any(k == 'ibapi' or k.startswith('ibapi.') for k in sys.modules)"],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
    def test_config_is_immutable(self):
        with self.assertRaises(FrozenInstanceError): TWSReadOnlyConfig().port = 4001


def invalid_config(name, values):
    def test(self):
        with self.assertRaises(ReadOnlyError): TWSReadOnlyConfig(**{name: values})
    return test


for label, field, value in (
    ('remote_host', 'host', 'example.invalid'), ('zero_port', 'port', 0),
    ('large_port', 'port', 65536), ('bool_port', 'port', True),
    ('zero_client', 'client_id', 0), ('negative_client', 'client_id', -1),
    ('bool_client', 'client_id', True), ('write_enabled', 'read_only', False),
    ('truthy_readonly', 'read_only', 'true'), ('none_readonly', 'read_only', None),
    ('bad_timeout', 'timeout_seconds', 0), ('bad_ttl', 'snapshot_ttl_seconds', 0),
    ('bad_zone', 'broker_timezone', 'invalid-zone')):
    setattr(ConfigTests, f'test_reject_{label}', invalid_config(field, value))


class NumericTests(unittest.TestCase):
    def test_decimal_string_is_exact(self):
        self.assertEqual(broker_decimal('0.12345678901234567890123456789'), D('0.12345678901234567890123456789'))
    def test_float_uses_text_not_binary_expansion(self):
        self.assertEqual(broker_decimal(1.231), D('1.231'))
    def test_integer(self): self.assertEqual(broker_decimal(17), D('17'))
    def test_signed_value(self): self.assertEqual(broker_decimal('-2.5'), D('-2.5'))
    def test_execution_timezone(self):
        self.assertEqual(execution_timestamp('20260701 10:00:00 America/New_York', None), AT)
    def test_execution_configured_timezone(self):
        self.assertEqual(execution_timestamp('20260701 14:00:00', 'UTC'), AT)
    def test_missing_timezone_fails(self):
        with self.assertRaisesRegex(ReadOnlyError, 'BROKER_TIMEZONE_REQUIRED'):
            execution_timestamp('20260701 14:00:00', None)
    def test_dst_ambiguity_fails(self):
        with self.assertRaises(ReadOnlyError): execution_timestamp('20261101 01:30:00 America/New_York', None)
    def test_dst_nonexistent_fails(self):
        with self.assertRaises(ReadOnlyError): execution_timestamp('20260308 02:30:00 America/New_York', None)


for label, value in (('bool', True), ('none', None), ('object', object()), ('nan', float('nan')),
                     ('infinity', float('inf')), ('sdk_unset', 1.7976931348623157e308),
                     ('invalid_text', 'private'), ('decimal_nan', D('NaN')), ('text_inf', 'Infinity')):
    def test(self, value=value):
        with self.assertRaisesRegex(ReadOnlyError, 'MALFORMED_BROKER_VALUE'): broker_decimal(value)
    setattr(NumericTests, f'test_reject_{label}', test)


class ReadTests(unittest.TestCase):
    def test_complete_account_batch(self):
        t, api, clock = transport()
        s = t.connect()
        self.addCleanup(t.disconnect)
        self.assertEqual((s.account.cash, s.account.equity, s.account.buying_power), (D('10000.01'), D('10012.31'), D('20000.02')))
        self.assertIsNone(s.account.mode)
        self.assertEqual(s.identities[0].mode, AccountMode.UNKNOWN)
        self.assertGreaterEqual(s.account.available_at, s.account.observed_at)
        self.assertEqual(s.broker_time, AT)
    def test_positions_and_precision(self):
        t, _, _ = transport(); s = t.connect(); self.addCleanup(t.disconnect)
        self.assertEqual((s.account.positions[0].security_id, s.account.positions[0].quantity, s.account.positions[0].average_cost),
                         ('ibkr-conid-17', D('10'), D('1.231')))
    def test_negative_position_supported(self):
        t, _, _ = transport(sdk(position_qty='-3')); s = t.connect(); self.addCleanup(t.disconnect)
        self.assertEqual(s.account.positions[0].quantity, D('-3'))
    def test_zero_position_not_fabricated(self):
        t, _, _ = transport(sdk(position_qty='0', cost=0)); s = t.connect(); self.addCleanup(t.disconnect)
        self.assertEqual(s.account.positions, ())
    def test_orders_are_observations_not_submission_requests(self):
        t, _, _ = transport(); s = t.connect(); self.addCleanup(t.disconnect)
        o = s.working_orders[0]
        self.assertEqual((o.order_type, o.side, o.quantity, o.limit_price), (OrderType.LIMIT, Side.BUY, D('10'), D('1.23')))
        self.assertFalse(hasattr(o, 'created_at'))
        self.assertEqual(s.completed_orders[0].state, OrderState.FILLED)
    def test_stop_and_sell(self):
        t, _, _ = transport(sdk(order_type='STP', side='SELL')); s = t.connect(); self.addCleanup(t.disconnect)
        self.assertEqual((s.working_orders[0].side, s.working_orders[0].stop_price), (Side.SELL, D('1.1')))
    def test_market_ignores_unset_price_fields(self):
        t, _, _ = transport(sdk(order_type='MKT')); s = t.connect(); self.addCleanup(t.disconnect)
        self.assertIsNone(s.working_orders[0].limit_price)
    def test_unknown_order_type_and_status_are_explicit(self):
        t, _, _ = transport(sdk(order_type='TRAIL', status='Mystery')); s = t.connect(); self.addCleanup(t.disconnect)
        self.assertIsNone(s.working_orders[0].order_type)
        self.assertEqual(s.working_orders[0].state, OrderState.UNKNOWN)
    def test_executions_commissions_are_separate(self):
        t, _, _ = transport(); s = t.connect(); self.addCleanup(t.disconnect)
        self.assertEqual((s.fills[0].quantity, s.fills[0].price), (D('4'), D('1.23')))
        self.assertEqual(s.commissions[0].amount, D('0.31'))
        self.assertEqual(s.commissions[0].fill_id, s.fills[0].fill_id)
    def test_duplicate_callbacks_idempotent(self):
        t, _, _ = transport(sdk(duplicate=True)); s = t.connect(); self.addCleanup(t.disconnect)
        self.assertEqual(tuple(map(len, (s.account.positions, s.working_orders, s.fills, s.commissions))), (1, 1, 1, 1))
    def test_completed_orders_capability(self):
        t, api, _ = transport(sdk(server_version=100)); s = t.connect(); self.addCleanup(t.disconnect)
        self.assertFalse(s.completed_orders_available)
        self.assertEqual(s.completed_orders, ())
        self.assertFalse(any(c[0] == 'completed' for c in api.calls))
    def test_informational_farm_event_not_failure(self):
        t, _, _ = transport(sdk(error_code=2104)); t.connect(); self.addCleanup(t.disconnect)
        self.assertTrue(t.connected)
        self.assertEqual(t.diagnostics, (('INFORMATIONAL', 2104),))
    def test_callback_failure_does_not_expose_raw_text(self):
        t, _, _ = transport(sdk(connect_error=True))
        with self.assertRaisesRegex(ReadOnlyError, '^IB_GATEWAY_DISCONNECTED$'): t.connect()
        self.assertNotIn('private', repr(t.diagnostics))
    def test_account_snapshot_immutable(self):
        t, _, _ = transport(); s = t.connect(); self.addCleanup(t.disconnect)
        with self.assertRaises(FrozenInstanceError): s.account.cash = D('9')
    def test_masked_identity_no_raw_account(self):
        t, _, _ = transport(); s = t.connect(); self.addCleanup(t.disconnect)
        self.assertNotIn('anonymous-fixture', repr(s))
        self.assertEqual(s.identities[0].masked_id, 'account-1')
    def test_cached_read_does_not_repeat_requests(self):
        t, api, _ = transport(); s = t.connect(); self.addCleanup(t.disconnect)
        n = len(api.calls)
        self.assertIs(t.connect(), s)
        self.assertEqual(len(api.calls), n)


for label, options, code in (
    ('no_accounts', {'accounts': ''}, 'ACCOUNT_NOT_READY'),
    ('multi_accounts', {'accounts': 'anonymous-one,anonymous-two'}, 'MULTIPLE_ACCOUNTS_UNSUPPORTED'),
    ('missing_balance', {'values': {'NetLiquidation': '100'}}, 'ACCOUNT_NOT_READY'),
    ('malformed_balance', {'values': {'NetLiquidation': 'private'}}, 'MALFORMED_BROKER_VALUE'),
    ('conflicting_balance', {'conflict': True}, 'CONFLICTING_BROKER_CALLBACK'),
    ('auth_failure', {'error_code': 502}, 'IB_GATEWAY_DISCONNECTED'),
    ('request_failure', {'error_code': 321}, 'IB_GATEWAY_REQUEST_FAILED'),
    ('future_execution', {'execution_time': '20260702 14:00:00 UTC'}, 'FUTURE_EXECUTION_TIMESTAMP'),
    ('malformed_position', {'position_qty': 'NaN'}, 'MALFORMED_BROKER_VALUE')):
    def test(self, options=options, code=code):
        t, _, _ = transport(sdk(**options))
        with self.assertRaisesRegex(ReadOnlyError, '^' + code + '$'): t.connect()
        self.assertFalse(t.connected)
    setattr(ReadTests, f'test_reject_{label}', test)


class SafetyTests(unittest.TestCase):
    def test_sdk_wire_write_allowlist(self):
        t, api, _ = transport(); t.connect(); self.addCleanup(t.disconnect)
        client = api.clients[-1]
        for message in ('3\0write', '4\0cancel', '58\0globalcancel', b'3\0write', 'malformed'):
            with self.assertRaisesRegex(ReadOnlyError, 'READ_ONLY_BROKER_TRANSPORT'): client.sendMsg(message)
        self.assertFalse(any(c[0] == 'send' for c in api.calls))
    def test_sdk_base_write_cannot_bypass_wire_guard(self):
        t, api, _ = transport(); t.connect(); self.addCleanup(t.disconnect)
        with self.assertRaises(ReadOnlyError): api.Client.placeOrder(api.clients[-1], 9)
        self.assertFalse(any(c[0] == 'send' for c in api.calls))
    def test_unknown_sdk_protobuf_encoding_fails_closed(self):
        t, api, _ = transport(); t.connect(); self.addCleanup(t.disconnect)
        with self.assertRaisesRegex(ReadOnlyError, 'UNSUPPORTED_IBAPI_WIRE_ENCODING'):
            api.clients[-1].sendMsgProtoBuf(3, b'write')
        self.assertFalse(any(c[0] == 'send' for c in api.calls))
    def test_direct_sdk_order_methods_blocked(self):
        t, api, _ = transport(); t.connect(); self.addCleanup(t.disconnect)
        for name in ('placeOrder', 'cancelOrder', 'reqGlobalCancel'):
            with self.assertRaises(ReadOnlyError): getattr(api.clients[-1], name)()
        self.assertFalse(any(c[0] == 'send' for c in api.calls))
    def test_read_wire_message_allowed(self):
        t, api, _ = transport(); t.connect(); self.addCleanup(t.disconnect)
        api.clients[-1].sendMsg(str(api.OUT.REQ_CURRENT_TIME) + '\0read')
        self.assertEqual(api.calls[-1][0], 'send')
    def test_no_client_zero_binding_requests(self):
        t, api, _ = transport(); t.connect(); self.addCleanup(t.disconnect)
        self.assertIn(('orders',), api.calls)
        self.assertFalse(hasattr(t, 'client'))
    def test_backend_hooks_block_even_base_facade_invocation(self):
        b, _, _, _ = broker()
        for call in (lambda: b._backend_place(request()), lambda: b._backend_replace('id', request()), lambda: b._backend_cancel('id')):
            with self.assertRaises(BrokerOperationError) as e: call()
            self.assertEqual(e.exception.reason, R.READ_ONLY_BROKER_TRANSPORT)
    def test_mode_config_or_port_not_account_proof(self):
        b, _, _, _ = broker(port=4001); b.connect(); self.addCleanup(b.disconnect)
        self.assertEqual(b.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(b.account_summary().mode)
    def test_account_mode_cannot_be_manually_promoted(self):
        b, _, _, clock = broker(); b.connect(); self.addCleanup(b.disconnect)
        with self.assertRaises(ReadOnlyError): b.report_account(replace(b.account_summary(), mode=TradingMode.PAPER), clock())
    def test_trading_enable_blocked(self):
        b, _, _, clock = broker()
        with self.assertRaises(BrokerOperationError): b.set_trading_enabled(True, clock())
        self.assertFalse(b.controls.trading_enabled)
    def test_sdk_logs_suppressed(self):
        stream = io.StringIO()
        logger = logging.getLogger('ibapi.fixture')
        logger.addHandler(logging.StreamHandler(stream))
        t, _, _ = transport(); t.connect(); self.addCleanup(t.disconnect)
        logger.error('anonymous-fixture private')
        self.assertEqual(stream.getvalue(), '')


for name in ('place_order', 'modify_order', 'replace_order', 'cancel_order', 'flatten_positions'):
    def test(self, name=name):
        t, api, _ = transport()
        with self.assertRaisesRegex(ReadOnlyError, '^READ_ONLY_BROKER_TRANSPORT$'): getattr(t, name)()
        self.assertEqual(api.calls, [])
    setattr(SafetyTests, f'test_transport_blocks_{name}', test)
for name, args in (
    ('place_order', lambda at: (request(mode=TradingMode.LIVE), at)),
    ('replace_order', lambda at: ('id', request(), at)),
    ('modify_order', lambda at: ('id', request(), at)),
    ('cancel_order', lambda at: ('id', at)),
    ('cancel_working_orders', lambda at: (at,)), ('flatten_positions', lambda at: (at,))):
    def test(self, name=name, args=args):
        b, _, api, clock = broker()
        with self.assertRaises(BrokerOperationError) as e: getattr(b, name)(*args(clock()))
        self.assertEqual(e.exception.reason, R.READ_ONLY_BROKER_TRANSPORT)
        self.assertEqual(api.calls, [])
        self.assertEqual(b.audit[-1].reasons, (R.READ_ONLY_BROKER_TRANSPORT,))
    setattr(SafetyTests, f'test_facade_blocks_{name}', test)


class LifecycleTests(unittest.TestCase):
    def test_stale_snapshot_cannot_reconnect_authoritative_account(self):
        b, t, _, clock = broker(); b.connect(); self.addCleanup(b.disconnect)
        previous = b.account_summary()
        snapshot = b._observation
        b.disconnect()
        stale = replace(snapshot, account=replace(previous, observed_at=previous.observed_at - timedelta(seconds=1)))
        with patch.object(t, 'connect', return_value=stale), patch.object(type(t), 'connected', new_callable=PropertyMock, return_value=True):
            self.assertEqual(b.connect(), ConnectionStatus.DISCONNECTED)
        self.assertEqual(b._accepted_account, previous)
    def test_disconnect_between_collection_and_install_fails_closed(self):
        b, t, _, _ = broker()
        snapshot = t.connect()
        t.disconnect()
        with patch.object(t, 'connect', return_value=snapshot):
            with self.assertRaises(BrokerOperationError): b.connect()
        self.assertEqual(b.connection_status, ConnectionStatus.DISCONNECTED)
        self.assertIsNone(b._accepted_account)
    def test_stale_disconnect_cannot_regress_newer_connection(self):
        b, _, _, clock = broker(); b.connect(); self.addCleanup(b.disconnect)
        self.assertFalse(b.handle_event(BrokerEvent('stale-close', EventKind.DISCONNECTED, AT, clock())))
        self.assertEqual(b.connection_status, ConnectionStatus.CONNECTED)
    def test_missing_account_proof_reconnect_rejected(self):
        b, _, _, clock = broker()
        at = clock()
        with self.assertRaises(BrokerOperationError):
            b.handle_event(BrokerEvent('unproved-connect', EventKind.RECONNECTED, at, at))
        self.assertEqual(b.connection_status, ConnectionStatus.DISCONNECTED)
    def test_read_audits_are_deterministic(self):
        def run():
            b, _, _, _ = broker(); b.connect(); b.account_summary(); b.disconnect()
            return b.audit
        self.assertEqual(run(), run())
    def test_initial_sdk_handshake_is_in_timeout_boundary(self):
        release = Event()
        t, api, _ = transport(sdk(connect_wait=release), timeout_seconds=1)
        self.addCleanup(release.set)
        with self.assertRaisesRegex(ReadOnlyError, 'IB_GATEWAY_TIMEOUT'): t.connect()
        self.assertFalse(t.connected)
        release.set()
        # Old worker is generation-invalid and cannot install account evidence.
        self.assertIsNone(t._snapshot)
        self.assertFalse(any(c[0] == 'summary' for c in api.calls))
    def test_timeout(self):
        t, _, _ = transport(sdk(no_ready=True), timeout_seconds=1)
        with self.assertRaisesRegex(ReadOnlyError, 'IB_GATEWAY_TIMEOUT'): t.connect()
        self.assertFalse(t.connected)
    def test_disconnect_callback_invalidates_account(self):
        b, _, api, clock = broker(); b.connect(); self.addCleanup(b.disconnect)
        api.clients[-1].wrapper.connectionClosed()
        self.assertEqual(b.connection_status, ConnectionStatus.DISCONNECTED)
        with self.assertRaises(BrokerOperationError): b.account_summary()
    def test_duplicate_sdk_disconnect_after_queue_drained_is_idempotent(self):
        b, _, api, _ = broker(); b.connect(); self.addCleanup(b.disconnect)
        wrapper = api.clients[-1].wrapper
        wrapper.connectionClosed()
        self.assertEqual(b.connection_status, ConnectionStatus.DISCONNECTED)
        before = b.audit
        wrapper.connectionClosed()
        self.assertEqual(b.connection_status, ConnectionStatus.DISCONNECTED)
        self.assertEqual(b.audit, before)
    def test_reconnect_requires_fresh_batch(self):
        b, _, api, clock = broker(); b.connect(); self.addCleanup(b.disconnect)
        old = b.account_summary()
        b.disconnect(); clock.advance(1); b.connect()
        self.assertGreater(b.account_summary().observed_at, old.observed_at)
        self.assertEqual(len(api.clients), 2)
    def test_old_generation_callback_ignored(self):
        b, _, api, clock = broker(); b.connect(); self.addCleanup(b.disconnect)
        old = api.clients[-1].wrapper
        b.refresh()
        old.connectionClosed()
        old.managedAccounts('different-private-value')
        self.assertEqual(b.connection_status, ConnectionStatus.CONNECTED)
        self.assertIsNone(b.account_summary().mode)
    def test_emergency_stop_stays_latched_after_refresh(self):
        b, _, _, clock = broker(); b.connect(); self.addCleanup(b.disconnect)
        b.emergency_stop(clock()); b.refresh()
        self.assertTrue(b.controls.emergency_stopped)
        self.assertFalse(b.controls.trading_enabled)
    def test_stale_snapshot_shared_boundary(self):
        b, _, _, clock = broker(); b.connect(); self.addCleanup(b.disconnect)
        account = b.account_summary()
        stale = replace(account, observed_at=account.observed_at - timedelta(seconds=1), cash=D('1'))
        with self.assertRaises(BrokerOperationError) as e: b.report_account(stale, clock())
        self.assertEqual(e.exception.reason, R.STALE_ACCOUNT_EVIDENCE)
        self.assertEqual(b.account_summary(), account)
    def test_equal_time_conflicting_snapshot_shared_boundary(self):
        b, _, _, clock = broker(); b.connect(); self.addCleanup(b.disconnect)
        a = b.account_summary()
        with self.assertRaises(BrokerOperationError): b.report_account(replace(a, cash=D('1')), clock())
        self.assertEqual(b.account_summary(), a)
        self.assertTrue(b.reconciliation_required)
    def test_stale_reconnect_shared_boundary(self):
        b, _, _, clock = broker(); b.connect(); self.addCleanup(b.disconnect)
        a = b.account_summary(); b.disconnect()
        event = BrokerEvent('stale-reconnect', EventKind.RECONNECTED, AT, clock(), account=a)
        self.assertFalse(b.handle_event(event))
        self.assertEqual(b.connection_status, ConnectionStatus.DISCONNECTED)
    def test_duplicate_disconnect_safe(self):
        b, _, _, clock = broker(); b.connect(); self.addCleanup(b.disconnect)
        at = clock()
        e = BrokerEvent('disconnected', EventKind.DISCONNECTED, at, at)
        self.assertTrue(b.handle_event(e))
        self.assertFalse(b.handle_event(replace(e, received_at=clock())))
        self.assertEqual(b.connection_status, ConnectionStatus.DISCONNECTED)
    def test_expired_account_is_not_current(self):
        b, _, _, clock = broker(); b.connect(); self.addCleanup(b.disconnect)
        clock.advance(16)
        with self.assertRaises(BrokerOperationError): b.account_summary()
    def test_observation_reads_do_not_create_simulated_orders(self):
        b, _, _, _ = broker(); b.connect(); self.addCleanup(b.disconnect)
        self.assertEqual((len(b.working_orders()), len(b.completed_orders()), len(b.fills()), len(b.commissions())), (1, 1, 1, 1))
        self.assertEqual(b._orders, {})
        self.assertEqual(b._fills, {})
        self.assertEqual(b.order_status('ibkr-permid-77').quantity, D('10'))
    def test_audit_has_no_identifiers_or_sdk_raw_errors(self):
        b, _, _, _ = broker(); b.connect(); b.account_summary(); b.disconnect()
        self.assertNotIn('anonymous-fixture', repr(b.audit))
        self.assertNotIn('private', repr(b.audit))
        self.assertEqual(b.audit[1].details[0], ('account_mode', 'UNKNOWN'))


class DiagnosticTests(unittest.TestCase):
    def test_missing_dependency_no_traceback(self):
        from tradingbot_broker import ibkr_diagnostics
        output = io.StringIO()
        with patch('tradingbot_broker.ibkr_readonly._load_official_api', side_effect=ReadOnlyError('OFFICIAL_IBAPI_DEPENDENCY_UNAVAILABLE')):
            with redirect_stdout(output): result = ibkr_diagnostics.main()
        self.assertEqual(result, 1)
        self.assertIn('official IBKR', output.getvalue())
        self.assertNotIn('Traceback', output.getvalue())
    def test_gateway_unavailable_no_success_claim(self):
        from tradingbot_broker import ibkr_diagnostics
        output = io.StringIO()
        with patch('tradingbot_broker.ibkr_readonly._load_official_api', return_value=sdk(connect_error=True)):
            with redirect_stdout(output): result = ibkr_diagnostics.main()
        self.assertEqual(result, 1)
        self.assertIn('UNAVAILABLE', output.getvalue())
        self.assertNotIn('private', output.getvalue())
    def test_safe_read_output_and_disconnect(self):
        from tradingbot_broker import ibkr_diagnostics
        output = io.StringIO(); api = sdk(execution_time='20260701 13:59:59 UTC')
        with patch('tradingbot_broker.ibkr_readonly._load_official_api', return_value=api):
            with redirect_stdout(output): result = ibkr_diagnostics.main()
        self.assertEqual(result, 0)
        self.assertIn('Account mode: UNKNOWN', output.getvalue())
        self.assertIn('Order transmission: DISABLED', output.getvalue())
        self.assertNotIn('anonymous-fixture', output.getvalue())
        self.assertFalse(api.clients[-1].isConnected())
