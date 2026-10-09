from dataclasses import replace
import unittest

from tradingbot_broker.broker import BrokerOperationError
from tradingbot_broker.fake import FakeBroker
from tradingbot_broker.models import (BrokerReason as R, ConnectionStatus, OrderIntent,
                                      OrderState, Position, Side, TradingMode)
from tests.broker_fixtures import AT, D, END, account, at, permission, ready, request, submitted


class BrokerSafetyTests(unittest.TestCase):
    def blocked(self, broker, order, reason, proof=None, when=AT):
        ack = broker.place_order(order, when, proof)
        self.assertFalse(ack.accepted)
        self.assertIn(reason, ack.reasons)
        self.assertEqual(broker.submissions, [])
        self.assertEqual(broker.audit[-1].reasons, ack.reasons)
        return ack

    def test_paper_all_gates_allow(self):
        broker, order, order_id = submitted()
        self.assertEqual(broker.submissions, [order])
        self.assertEqual(broker.order_status(order_id).state, OrderState.ACKNOWLEDGED)
        self.assertEqual(broker.fills(), ())

    def test_default_disabled(self):
        broker = FakeBroker(account())
        broker.connect(AT)
        order = request()
        self.blocked(broker, order, R.TRADING_DISABLED, permission(order))

    def test_live_config_hard_disabled(self):
        broker = ready(FakeBroker(account(mode=TradingMode.LIVE), mode=TradingMode.LIVE))
        order = request(mode=TradingMode.LIVE)
        self.blocked(broker, order, R.LIVE_EXECUTION_DISABLED, permission(order))

    def test_live_request_never_falls_back_to_paper(self):
        order = request(mode=TradingMode.LIVE)
        broker = ready()
        ack = self.blocked(broker, order, R.LIVE_EXECUTION_DISABLED, permission(order))
        self.assertIn(R.REQUEST_MODE_MISMATCH, ack.reasons)
        self.assertEqual(broker.mode, TradingMode.PAPER)

    def test_paper_request_under_live_config_blocked(self):
        broker = ready(FakeBroker(account(), mode=TradingMode.LIVE))
        order = request()
        self.blocked(broker, order, R.LIVE_EXECUTION_DISABLED, permission(order))

    def test_account_live_mismatch(self):
        broker = ready(FakeBroker(account(mode=TradingMode.LIVE)))
        order = request()
        self.blocked(broker, order, R.ACCOUNT_MODE_MISMATCH, permission(order))

    def test_account_unknown_mode_blocked(self):
        order = request()
        self.blocked(ready(FakeBroker(account(mode=None))), order, R.ACCOUNT_MODE_UNVERIFIED, permission(order))

    def test_unverified_account_blocked(self):
        order = request()
        self.blocked(ready(FakeBroker(account(verified=False))), order, R.ACCOUNT_MODE_UNVERIFIED, permission(order))

    def test_future_account_mode_does_not_leak_into_audit(self):
        order = request()
        broker = ready(FakeBroker(account(mode=TradingMode.LIVE, available_at=at(1))))
        self.blocked(broker, order, R.ACCOUNT_DATA_UNAVAILABLE, permission(order))
        self.assertIsNone(broker.audit[-1].account_mode)
        self.assertNotIn(R.ACCOUNT_MODE_MISMATCH, broker.audit[-1].reasons)

    def test_expired_account_blocked(self):
        order = request(expires_at=at(100))
        broker = ready(FakeBroker(account(valid_until=at(1))))
        self.blocked(broker, order, R.ACCOUNT_DATA_EXPIRED, permission(order), at(1))

    def test_missing_permission_blocked(self):
        self.blocked(ready(), request(), R.RISK_PERMISSION_UNAVAILABLE)

    def test_denied_daily_risk_blocked(self):
        order = request()
        self.blocked(ready(), order, R.PORTFOLIO_ENTRY_DENIED, permission(order, permits_entry=False, reason_code='DAILY_LOCKOUT'))

    def test_future_permission_substantive_fields_not_used(self):
        order = request()
        proof = permission(request('different'), available_at=at(1), permits_entry=False)
        ack = self.blocked(ready(), order, R.RISK_PERMISSION_NOT_YET_AVAILABLE, proof)
        self.assertNotIn(R.RISK_PERMISSION_MISMATCH, ack.reasons)
        self.assertNotIn(R.PORTFOLIO_ENTRY_DENIED, ack.reasons)

    def test_expired_permission_blocked_at_exact_boundary(self):
        order = request()
        self.blocked(ready(), order, R.RISK_PERMISSION_EXPIRED,
                     permission(order, valid_until=at(1)), at(1))

    def test_full_payload_permission_mismatch(self):
        for kw in (dict(quantity=D(20)), dict(symbol='OTHER'), dict(side=Side.SELL)):
            order = request()
            self.blocked(ready(), order, R.RISK_PERMISSION_MISMATCH, permission(replace(order, **kw)))

    def test_exact_availability_equality_passes(self):
        order = request()
        self.assertTrue(ready().place_order(order, AT, permission(order)).accepted)

    def test_future_order_blocked(self):
        order = request(created_at=at(1))
        self.blocked(ready(), order, R.ORDER_NOT_YET_VALID, permission(order))

    def test_expired_order_blocked(self):
        order = request(expires_at=at(1))
        self.blocked(ready(), order, R.ORDER_EXPIRED, permission(order), at(1))

    def test_emergency_stop_latches(self):
        broker = ready()
        broker.emergency_stop(AT)
        broker.resume_new_entries(AT)
        broker.set_trading_enabled(True, AT)
        order = request()
        self.blocked(broker, order, R.EMERGENCY_STOP, permission(order))

    def test_pause_blocks_entries_and_resume_requires_new_id(self):
        broker = ready()
        broker.pause_new_entries(AT)
        order = request()
        self.blocked(broker, order, R.ENTRIES_PAUSED, permission(order))
        broker.resume_new_entries(AT)
        self.blocked(broker, order, R.DUPLICATE_ORDER, permission(order))
        fresh = request('candidate-2')
        self.assertTrue(broker.place_order(fresh, AT, permission(fresh)).accepted)

    def test_pause_does_not_classify_exit_as_new_entry(self):
        broker = ready()
        broker.pause_new_entries(AT)
        order = request(intent=OrderIntent.EXIT)
        self.assertTrue(broker.place_order(order, AT, permission(order)).accepted)

    def test_exit_cannot_bypass_universal_emergency_or_risk_gates(self):
        for emergency in (True, False):
            broker = ready()
            if emergency:
                broker.emergency_stop(AT)
            order = request(intent=OrderIntent.EXIT)
            reason = R.EMERGENCY_STOP if emergency else R.PORTFOLIO_ENTRY_DENIED
            self.blocked(broker, order, reason, permission(order, permits_entry=False))

    def test_multiple_failed_gates_all_audited(self):
        broker = FakeBroker(account(mode=TradingMode.LIVE))
        broker.connect(AT)
        broker.emergency_stop(AT)
        broker.pause_new_entries(AT)
        order = request()
        ack = self.blocked(broker, order, R.ACCOUNT_MODE_MISMATCH, permission(order, permits_entry=False))
        self.assertEqual(ack.reasons, (R.ACCOUNT_MODE_MISMATCH, R.TRADING_DISABLED, R.EMERGENCY_STOP,
                                      R.ENTRIES_PAUSED, R.PORTFOLIO_ENTRY_DENIED))

    def test_disconnect_blocks_new_orders(self):
        broker = ready()
        broker.disconnect(AT)
        order = request()
        self.blocked(broker, order, R.BROKER_DISCONNECTED, permission(order))

    def test_reconnect_rechecks_reported_account(self):
        broker = ready()
        broker.disconnect(AT)
        broker.reported_account = account(mode=TradingMode.LIVE, observed_at=at(1), available_at=at(1))
        broker.connect(at(1))
        order = request()
        self.blocked(broker, order, R.ACCOUNT_MODE_MISMATCH, permission(order), at(1))

    def test_reconnect_preserves_emergency_stop(self):
        broker = ready()
        broker.emergency_stop(AT)
        broker.disconnect(AT)
        broker.connect(at(1))
        order = request()
        self.blocked(broker, order, R.EMERGENCY_STOP, permission(order), at(1))

    def test_cancel_allowed_during_emergency_only_in_simulated_paper(self):
        broker, _, order_id = submitted()
        broker.emergency_stop(AT)
        self.assertTrue(broker.cancel_order(order_id, AT))
        self.assertEqual(broker.order_status(order_id).state, OrderState.CANCELLED)

    def test_cancel_blocked_after_account_changes_to_live(self):
        broker, _, order_id = submitted()
        broker.report_account(account(mode=TradingMode.LIVE, observed_at=at(1), available_at=at(1)), at(1))
        self.assertFalse(broker.cancel_order(order_id, at(1)))
        self.assertEqual(broker.cancellations, [])

    def test_flatten_produces_unsent_exact_long_and_short_plan(self):
        p1 = Position('security-1', 'DEMO', D('3.5'), D(1), 'USD')
        p2 = Position('security-2', 'OTHER', D('-2'), D(1), 'USD')
        broker = ready(FakeBroker(account(positions=(p2, p1))))
        broker.emergency_stop(AT)
        plan = broker.flatten_positions(AT)
        self.assertFalse(plan.submitted)
        self.assertEqual([r.side.value for r in plan.requests], ['SELL', 'BUY'])
        self.assertEqual([r.quantity for r in plan.requests], [D('3.5'), D(2)])
        self.assertEqual(broker.submissions, [])
        for order in plan.requests:
            self.blocked(broker, order, R.EMERGENCY_STOP, permission(order))

    def test_flatten_live_account_rejected(self):
        broker = ready(FakeBroker(account(mode=TradingMode.LIVE)))
        with self.assertRaises(BrokerOperationError):
            broker.flatten_positions(AT)
        self.assertEqual(broker.submissions, [])

    def test_trading_flag_cannot_use_truthy_string(self):
        with self.assertRaises(TypeError):
            ready().set_trading_enabled('true', AT)

    def test_modify_rechecks_full_risk_and_emergency_gates(self):
        broker, order, order_id = submitted()
        modified = replace(order, quantity=D(20))
        ack = broker.replace_order(order_id, modified, AT, permission(order))
        self.assertIn(R.RISK_PERMISSION_MISMATCH, ack.reasons)
        broker.emergency_stop(AT)
        ack = broker.replace_order(order_id, modified, AT, permission(modified))
        self.assertIn(R.EMERGENCY_STOP, ack.reasons)
        self.assertEqual(broker.modifications, [])
        self.assertEqual(broker.order_status(order_id).request, order)

    def test_modify_cannot_change_mode_security_or_direction(self):
        broker, order, order_id = submitted()
        for kw in (dict(mode=TradingMode.LIVE), dict(security_id='security-2')):
            modified = replace(order, **kw)
            ack = broker.replace_order(order_id, modified, AT, permission(modified))
            self.assertIn(R.INVALID_MODIFICATION, ack.reasons)
        self.assertEqual(broker.modifications, [])

    def test_unknown_dispatch_blocks_retry_and_further_submissions(self):
        class FailingFake(FakeBroker):
            def _backend_place(self, order):
                raise RuntimeError('private transport diagnostic')
        broker = ready(FailingFake(account()))
        order = request()
        ack = broker.place_order(order, AT, permission(order))
        self.assertEqual(ack.state, OrderState.UNKNOWN)
        self.assertTrue(broker.reconciliation_required)
        self.assertNotIn('private transport diagnostic', repr(broker.audit))
        fresh = request('candidate-2')
        self.blocked(broker, fresh, R.RECONCILIATION_REQUIRED, permission(fresh))
