from dataclasses import replace
import unittest

from tradingbot_broker.broker import Broker, BrokerOperationError
from tradingbot_broker.ibkr import (IBKRAdapter, IBKRExecution, IBKRPositionData, InMemoryIBKRTransport)
from tradingbot_broker.models import (BrokerReason as R, OrderState, OrderType, Side, TradingMode)
from tests.broker_fixtures import (AT, D, at, contract, ibkr, ibkr_account, permission, ready, request, submitted)


class IBKRAdapterTests(unittest.TestCase):
    def test_generic_protocol_implemented(self):
        adapter, _ = ibkr()
        self.assertIsInstance(adapter, Broker)

    def test_arbitrary_transport_cannot_be_activated(self):
        class ExternalClient:
            called = False
            def connect(self):
                self.called = True
                raise AssertionError('Must never be called')
        client = ExternalClient()
        with self.assertRaises(TypeError):
            IBKRAdapter(client, (contract(),))
        self.assertFalse(client.called)

    def test_transport_subclass_cannot_override_with_real_dispatch(self):
        class OtherTransport(InMemoryIBKRTransport):
            pass
        with self.assertRaises(TypeError):
            IBKRAdapter(OtherTransport(ibkr_account()), (contract(),))

    def test_market_buy_translation_exact(self):
        adapter, transport = ibkr()
        order = request(quantity=D('2.5'))
        submitted(adapter, order)
        c, raw = transport.submissions[0]
        self.assertEqual((c.con_id, raw.action, raw.order_type, raw.total_quantity, raw.tif),
                         (1, 'BUY', 'MKT', D('2.5'), 'DAY'))
        self.assertIsNone(raw.limit_price)
        self.assertIsNone(raw.auxiliary_price)

    def test_limit_sell_translation(self):
        order = request(side=Side.SELL, order_type=OrderType.LIMIT, limit_price=D('1.23456789'))
        raw = IBKRAdapter.translate_order(order)
        self.assertEqual((raw.action, raw.order_type, raw.limit_price), ('SELL', 'LMT', D('1.23456789')))

    def test_stop_translation_auxiliary_price(self):
        order = request(side=Side.SELL, order_type=OrderType.STOP, stop_price=D('0.98765'))
        raw = IBKRAdapter.translate_order(order)
        self.assertEqual((raw.order_type, raw.auxiliary_price), ('STP', D('0.98765')))

    def test_no_float_numeric_conversion(self):
        adapter = IBKRAdapter(InMemoryIBKRTransport(ibkr_account(cash=10000.1)), (contract(),))
        with self.assertRaises(BrokerOperationError):
            adapter.connect(AT)
        self.assertEqual(adapter.audit[-1].reasons, (R.TRANSPORT_FAILURE,))

    def test_account_and_position_translation(self):
        data = ibkr_account(cash='123.456789', positions=(IBKRPositionData(contract(), '3.5', '1.234'),))
        adapter = ready(IBKRAdapter(InMemoryIBKRTransport(data), (contract(),)))
        snapshot = adapter.account_summary(AT)
        self.assertEqual(snapshot.cash, D('123.456789'))
        self.assertEqual((snapshot.positions[0].security_id, snapshot.positions[0].quantity), ('security-1', D('3.5')))

    def test_live_request_cannot_reach_transport(self):
        adapter, transport = ibkr(mode=TradingMode.LIVE)
        ready(adapter)
        order = request(mode=TradingMode.LIVE)
        self.assertIn(R.LIVE_EXECUTION_DISABLED, adapter.place_order(order, AT, permission(order)).reasons)
        self.assertEqual(transport.submissions, [])

    def test_paper_live_account_mismatch_cannot_reach_transport(self):
        transport = InMemoryIBKRTransport(ibkr_account(mode=TradingMode.LIVE))
        adapter = ready(IBKRAdapter(transport, (contract(),)))
        order = request()
        self.assertIn(R.ACCOUNT_MODE_MISMATCH, adapter.place_order(order, AT, permission(order)).reasons)
        self.assertEqual(transport.submissions, [])

    def test_all_backend_interlocks_cover_ibkr(self):
        for gate in ('disabled', 'emergency', 'risk', 'disconnected', 'paused'):
            adapter, transport = ibkr()
            ready(adapter)
            if gate == 'disabled': adapter.set_trading_enabled(False, AT)
            if gate == 'emergency': adapter.emergency_stop(AT)
            if gate == 'disconnected': adapter.disconnect(AT)
            if gate == 'paused': adapter.pause_new_entries(AT)
            order = request()
            ack = adapter.place_order(order, AT, permission(order, permits_entry=gate != 'risk'))
            self.assertFalse(ack.accepted)
            self.assertEqual(transport.submissions, [])

    def test_missing_contract_preflight_not_unknown_dispatch(self):
        transport = InMemoryIBKRTransport(ibkr_account())
        adapter = ready(IBKRAdapter(transport, ()))
        order = request()
        ack = adapter.place_order(order, AT, permission(order))
        self.assertIn(R.CONTRACT_UNAVAILABLE, ack.reasons)
        self.assertFalse(adapter.reconciliation_required)
        self.assertEqual(transport.submissions, [])

    def test_future_expired_unverified_contracts_block(self):
        for c in (contract(available_at=at(1)), contract(verified=False),
                  contract(valid_from=at(-2), valid_until=AT)):
            transport = InMemoryIBKRTransport(ibkr_account())
            adapter = ready(IBKRAdapter(transport, (c,)))
            order = request()
            self.assertIn(R.CONTRACT_UNAVAILABLE, adapter.place_order(order, AT, permission(order)).reasons)
            self.assertEqual(transport.submissions, [])

    def test_symbol_currency_mismatch_blocks(self):
        for c in (contract(symbol='OTHER'), contract(currency='EUR')):
            transport = InMemoryIBKRTransport(ibkr_account())
            adapter = ready(IBKRAdapter(transport, (c,)))
            order = request()
            self.assertIn(R.CONTRACT_MISMATCH, adapter.place_order(order, AT, permission(order)).reasons)

    def test_duplicate_contract_id_mapping_rejected(self):
        transport = InMemoryIBKRTransport(ibkr_account())
        with self.assertRaises(ValueError):
            IBKRAdapter(transport, (contract(), contract(security_id='security-2')))

    def test_rejection_translation_and_error_code(self):
        transport = InMemoryIBKRTransport(ibkr_account(), submission_status='Inactive', error_code=201)
        adapter = ready(IBKRAdapter(transport, (contract(),)))
        order = request()
        ack = adapter.place_order(order, AT, permission(order))
        self.assertEqual((ack.state, ack.error_code), (OrderState.REJECTED, 201))
        self.assertEqual(adapter.audit[-1].error_code, 201)

    def test_unknown_submission_status_halts_for_reconciliation(self):
        transport = InMemoryIBKRTransport(ibkr_account(), submission_status='Unrecognized')
        adapter = ready(IBKRAdapter(transport, (contract(),)))
        order = request()
        ack = adapter.place_order(order, AT, permission(order))
        self.assertEqual(ack.state, OrderState.UNKNOWN)
        self.assertTrue(adapter.reconciliation_required)

    def test_pending_submit_and_ack_and_working_progress(self):
        transport = InMemoryIBKRTransport(ibkr_account(), submission_status='PendingSubmit')
        adapter = ready(IBKRAdapter(transport, (contract(),)))
        order = request()
        ack = adapter.place_order(order, AT, permission(order))
        self.assertEqual(ack.state, OrderState.SUBMITTED)
        adapter.receive_status(1, 'PreSubmitted', event_id='ack-1', occurred_at=at(1), received_at=at(1))
        adapter.receive_status(1, 'Submitted', event_id='working-1', occurred_at=at(2), received_at=at(2))
        self.assertEqual(adapter.order_status('ibkr-1').state, OrderState.WORKING)

    def test_partial_then_full_execution_translation(self):
        adapter, transport = ibkr()
        submitted(adapter)
        adapter.receive_execution(IBKRExecution('exec-1', 1, 1, 'BOT', '4', '1.23', at(1)), event_id='fill-1', received_at=at(1))
        self.assertEqual(adapter.order_status('ibkr-1').state, OrderState.PARTIALLY_FILLED)
        adapter.receive_execution(IBKRExecution('exec-2', 1, 1, 'BOT', '6', '1.25', at(2)), event_id='fill-2', received_at=at(2))
        self.assertEqual(adapter.order_status('ibkr-1').state, OrderState.FILLED)
        self.assertEqual(len(transport.submissions), 1)

    def test_sell_execution_translation(self):
        adapter, _ = ibkr()
        submitted(adapter, request(side=Side.SELL))
        adapter.receive_execution(IBKRExecution('exec-1', 1, 1, 'SLD', '10', '1.2', at(1)), event_id='fill-1', received_at=at(1))
        self.assertEqual(adapter.fills()[0].side, Side.SELL)

    def test_duplicate_execution_preserves_economics(self):
        adapter, _ = ibkr()
        submitted(adapter)
        execution = IBKRExecution('exec-1', 1, 1, 'BOT', '4', '1.23', at(1))
        adapter.receive_execution(execution, event_id='fill-1', received_at=at(1))
        adapter.receive_execution(execution, event_id='fill-1', received_at=at(2))
        adapter.receive_execution(execution, event_id='fill-repeat', received_at=at(3))
        self.assertEqual(adapter.order_status('ibkr-1').filled_quantity, D(4))
        self.assertEqual(len(adapter.fills()), 1)

    def test_commission_translation_does_not_invent_zero_cost(self):
        adapter, _ = ibkr()
        submitted(adapter)
        self.assertEqual(adapter.commissions(), ())
        adapter.receive_execution(IBKRExecution('exec-1', 1, 1, 'BOT', '10', '1.23', at(1)), event_id='fill-1', received_at=at(1))
        adapter.receive_commission('report-1', 'exec-1', '0.35', 'USD', event_id='commission-1',
                                   occurred_at=at(2), received_at=at(2))
        self.assertEqual(adapter.commissions()[0].amount, D('0.35'))

    def test_cancel_and_modify_use_supplied_broker_order_id(self):
        adapter, transport = ibkr()
        _, order, order_id = submitted(adapter)
        changed = replace(order, quantity=D(8))
        self.assertTrue(adapter.replace_order(order_id, changed, AT, permission(changed)).accepted)
        self.assertEqual(transport.modifications[0][0], 1)
        self.assertEqual(transport.modifications[0][2].total_quantity, D(8))
        self.assertTrue(adapter.cancel_order(order_id, AT))
        self.assertEqual(transport.cancellations, [1])

    def test_cancel_waits_for_callback_when_not_confirmed(self):
        transport = InMemoryIBKRTransport(ibkr_account(), confirm_cancellations=False)
        adapter = ready(IBKRAdapter(transport, (contract(),)))
        order = request()
        adapter.place_order(order, AT, permission(order))
        adapter.cancel_order('ibkr-1', AT)
        self.assertEqual(adapter.order_status('ibkr-1').state, OrderState.ACKNOWLEDGED)
        adapter.receive_status(1, 'ApiCancelled', event_id='cancel-1', occurred_at=at(1), received_at=at(1))
        self.assertEqual(adapter.order_status('ibkr-1').state, OrderState.CANCELLED)

    def test_unknown_callback_status_no_raw_message_logging(self):
        adapter, _ = ibkr()
        ready(adapter)
        with self.assertRaises(BrokerOperationError):
            adapter.receive_status(1, 'private transport diagnostic', event_id='bad-1', occurred_at=AT, received_at=AT)
        self.assertNotIn('private transport diagnostic', repr(adapter.audit))

    def test_float_execution_rejected_without_partial_mutation(self):
        adapter, _ = ibkr()
        submitted(adapter)
        before = adapter.order_status('ibkr-1')
        with self.assertRaises(BrokerOperationError):
            adapter.receive_execution(IBKRExecution('exec-1', 1, 1, 'BOT', '4', 1.23, at(1)), event_id='fill-1', received_at=at(1))
        self.assertEqual(adapter.order_status('ibkr-1'), before)
        self.assertEqual(adapter.fills(), ())

    def test_no_auto_replay_orders_on_reconnect(self):
        adapter, transport = ibkr()
        submitted(adapter)
        adapter.disconnect(AT)
        adapter.connect(at(1))
        self.assertEqual(len(transport.submissions), 1)
        self.assertEqual(transport.connections, 2)
        self.assertEqual(adapter.order_status('ibkr-1').filled_quantity, D(0))

    def test_bad_callback_preserves_known_order_audit_context(self):
        adapter, _ = ibkr()
        _, order, _ = submitted(adapter)
        with self.assertRaises(BrokerOperationError):
            adapter.receive_status(1, 'Unrecognized', event_id='bad-status-1', occurred_at=AT,
                                   received_at=AT, error_code=321)
        record = adapter.audit[-1]
        self.assertEqual(record.request, order)
        self.assertEqual(record.broker_order_id, 'ibkr-1')
        self.assertEqual(record.error_code, 321)
        self.assertEqual(dict(record.details)['event_id'], 'bad-status-1')
