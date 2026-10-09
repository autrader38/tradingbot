from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import unittest

from tradingbot_broker.broker import BrokerOperationError
from tradingbot_broker.fake import FakeBroker
from tradingbot_broker.models import (BrokerEvent, BrokerReason as R, Commission, ConnectionStatus,
    EventKind, OrderState, Side, TradingMode)
from tests.broker_fixtures import AT, D, account, at, fill_event, permission, ready, request, submitted


class BrokerEventTests(unittest.TestCase):
    def test_rejected_order_preserves_code(self):
        broker = ready(FakeBroker(account(), reject_orders=True))
        order = request()
        ack = broker.place_order(order, AT, permission(order))
        self.assertEqual((ack.accepted, ack.state, ack.error_code), (False, OrderState.REJECTED, 201))
        self.assertEqual(broker.completed_orders()[0].state, OrderState.REJECTED)
        self.assertEqual(broker.audit[-1].error_code, 201)
        self.assertEqual(broker.fills(), ())

    def test_working_event(self):
        broker, order, order_id = submitted()
        broker.handle_event(BrokerEvent('working-1', EventKind.WORKING, at(1), at(1), order_id))
        self.assertEqual(broker.working_orders()[0].state, OrderState.WORKING)

    def test_partial_fill_not_full_or_completed(self):
        broker, order, order_id = submitted()
        broker.handle_event(fill_event(order_id))
        status = broker.order_status(order_id)
        self.assertEqual((status.state, status.filled_quantity, status.remaining_quantity),
                         (OrderState.PARTIALLY_FILLED, D(4), D(6)))
        self.assertEqual(status.average_fill_price, Fraction(123, 100))
        self.assertEqual(broker.completed_orders(), ())

    def test_two_fills_complete_with_exact_average(self):
        broker, order, order_id = submitted()
        broker.handle_event(fill_event(order_id))
        broker.handle_event(fill_event(order_id, '6', '1.25', fill_id='execution-2',
            event_id='event-2', kind=EventKind.FILLED, seconds=2))
        status = broker.order_status(order_id)
        self.assertEqual((status.state, status.filled_quantity, status.remaining_quantity), (OrderState.FILLED, D(10), D(0)))
        self.assertEqual(status.average_fill_price, Fraction(621, 500))
        self.assertEqual(len(broker.completed_orders()), 1)
        self.assertEqual(broker.working_orders(), ())

    def test_one_full_fill(self):
        broker, _, order_id = submitted()
        broker.handle_event(fill_event(order_id, '10', kind=EventKind.FILLED))
        self.assertEqual(broker.order_status(order_id).state, OrderState.FILLED)

    def test_cancel_partial_preserves_executed_quantity(self):
        broker, _, order_id = submitted()
        broker.handle_event(fill_event(order_id))
        self.assertTrue(broker.cancel_order(order_id, at(2)))
        status = broker.order_status(order_id)
        self.assertEqual((status.state, status.filled_quantity, status.remaining_quantity), (OrderState.CANCELLED, D(4), D(6)))
        self.assertEqual(len(broker.fills()), 1)

    def test_cancel_request_is_not_automatically_cancelled(self):
        broker, _, order_id = submitted(FakeBroker(account(), confirm_cancellations=False))
        self.assertTrue(broker.cancel_order(order_id, AT))
        self.assertEqual(broker.order_status(order_id).state, OrderState.ACKNOWLEDGED)
        broker.handle_event(BrokerEvent('cancel-1', EventKind.CANCELLED, at(1), at(1), order_id))
        self.assertEqual(broker.order_status(order_id).state, OrderState.CANCELLED)

    def test_cancel_all_working_skips_completed(self):
        broker, _, first = submitted()
        other = request('candidate-2')
        second = broker.place_order(other, AT, permission(other)).broker_order_id
        broker.handle_event(fill_event(first, '10', kind=EventKind.FILLED))
        self.assertEqual(broker.cancel_working_orders(at(2)), (second,))
        self.assertEqual(broker.cancellations, [second])

    def test_terminal_cancel_blocked(self):
        broker, _, order_id = submitted()
        broker.cancel_order(order_id, AT)
        self.assertFalse(broker.cancel_order(order_id, AT))
        self.assertEqual(broker.cancellations, [order_id])

    def test_expired_event(self):
        broker, _, order_id = submitted()
        broker.handle_event(BrokerEvent('expired-1', EventKind.EXPIRED, at(1), at(1), order_id))
        self.assertEqual(broker.completed_orders()[0].state, OrderState.EXPIRED)

    def test_rejected_event(self):
        broker, _, order_id = submitted()
        broker.handle_event(BrokerEvent('rejected-1', EventKind.REJECTED, at(1), at(1), order_id, error_code=202))
        self.assertEqual(broker.order_status(order_id).state, OrderState.REJECTED)
        self.assertEqual(broker.audit[-1].error_code, 202)

    def test_disconnect_preserves_working_orders_and_fills(self):
        broker, _, order_id = submitted()
        broker.handle_event(fill_event(order_id))
        old = broker.order_status(order_id)
        broker.handle_event(BrokerEvent('disconnect-1', EventKind.DISCONNECTED, at(2), at(2)))
        self.assertEqual(broker.connection_status, ConnectionStatus.DISCONNECTED)
        self.assertEqual(broker.order_status(order_id), old)
        self.assertEqual(len(broker.fills()), 1)

    def test_known_fill_can_arrive_while_disconnected(self):
        broker, _, order_id = submitted()
        broker.disconnect(AT)
        broker.handle_event(fill_event(order_id, '10', kind=EventKind.FILLED))
        self.assertEqual(broker.order_status(order_id).filled_quantity, D(10))
        self.assertEqual(broker.connection_status, ConnectionStatus.DISCONNECTED)

    def test_reconnect_requires_explicit_account(self):
        broker = ready()
        broker.disconnect(AT)
        with self.assertRaises(BrokerOperationError):
            broker.handle_event(BrokerEvent('reconnect-1', EventKind.RECONNECTED, at(1), at(1)))
        self.assertEqual(broker.connection_status, ConnectionStatus.DISCONNECTED)

    def test_reconnect_event_restores_connection_not_fills(self):
        broker, _, order_id = submitted()
        broker.disconnect(AT)
        broker.handle_event(BrokerEvent('reconnect-1', EventKind.RECONNECTED, at(1), at(1), account=account()))
        self.assertEqual(broker.connection_status, ConnectionStatus.CONNECTED)
        self.assertEqual(broker.order_status(order_id).filled_quantity, D(0))

    def test_duplicate_event_id_no_second_fill(self):
        broker, _, order_id = submitted()
        event = fill_event(order_id)
        broker.handle_event(event)
        status = broker.order_status(order_id)
        self.assertFalse(broker.handle_event(replace(event, received_at=at(2))))
        self.assertEqual(broker.order_status(order_id), status)
        self.assertEqual(len(broker.fills()), 1)
        self.assertEqual(broker.audit[-1].reasons, (R.DUPLICATE_EVENT,))

    def test_duplicate_fill_new_event_id_no_second_fill(self):
        broker, _, order_id = submitted()
        event = fill_event(order_id)
        broker.handle_event(event)
        status = broker.order_status(order_id)
        broker.handle_event(replace(event, event_id='repeat-1', received_at=at(2)))
        self.assertEqual(broker.order_status(order_id), status)
        self.assertEqual(len(broker.fills()), 1)

    def test_conflicting_duplicate_event_preserves_order(self):
        broker, _, order_id = submitted()
        event = fill_event(order_id)
        broker.handle_event(event)
        before = broker.order_status(order_id), broker.fills()
        with self.assertRaises(BrokerOperationError) as caught:
            broker.handle_event(replace(event, fill=replace(event.fill, price=D('2')), received_at=at(2)))
        self.assertEqual(caught.exception.reason, R.CONFLICTING_EVENT)
        self.assertEqual((broker.order_status(order_id), broker.fills()), before)
        self.assertTrue(broker.reconciliation_required)

    def test_overfill_rejected_before_state_mutation(self):
        broker, _, order_id = submitted()
        before = broker.order_status(order_id)
        with self.assertRaises(BrokerOperationError):
            broker.handle_event(fill_event(order_id, '11', kind=EventKind.FILLED))
        self.assertEqual(broker.order_status(order_id), before)
        self.assertEqual(broker.fills(), ())
        self.assertEqual(dict(broker.audit[-1].details)['fill_quantity'], D(11))

    def test_wrong_security_fill_rejected_atomically(self):
        broker, _, order_id = submitted()
        event = fill_event(order_id)
        event = replace(event, fill=replace(event.fill, security_id='security-2'))
        before = broker.order_status(order_id)
        with self.assertRaises(BrokerOperationError):
            broker.handle_event(event)
        self.assertEqual(broker.order_status(order_id), before)
        self.assertEqual(broker.fills(), ())

    def test_filled_status_without_execution_cannot_fabricate_fill(self):
        broker, _, order_id = submitted()
        with self.assertRaises(BrokerOperationError):
            broker.handle_event(BrokerEvent('filled-1', EventKind.FILLED, at(1), at(1), order_id))
        self.assertEqual(broker.fills(), ())
        self.assertEqual(broker.order_status(order_id).filled_quantity, D(0))

    def test_partial_status_with_full_quantity_rejected(self):
        broker, _, order_id = submitted()
        with self.assertRaises(BrokerOperationError):
            broker.handle_event(fill_event(order_id, '10'))
        self.assertEqual(broker.fills(), ())

    def test_late_new_execution_after_cancel_requires_reconciliation(self):
        broker, _, order_id = submitted()
        broker.cancel_order(order_id, AT)
        with self.assertRaises(BrokerOperationError):
            broker.handle_event(fill_event(order_id))
        self.assertTrue(broker.reconciliation_required)
        self.assertEqual(broker.fills(), ())

    def test_late_ack_does_not_regress_partial_state(self):
        broker, _, order_id = submitted()
        broker.handle_event(fill_event(order_id))
        with self.assertRaises(BrokerOperationError):
            broker.handle_event(BrokerEvent('late-ack', EventKind.ACKNOWLEDGED, AT, at(2), order_id))
        self.assertEqual(broker.order_status(order_id).state, OrderState.PARTIALLY_FILLED)

    def test_unknown_order_event_rejected(self):
        broker = ready()
        with self.assertRaises(BrokerOperationError) as caught:
            broker.handle_event(BrokerEvent('unknown-1', EventKind.WORKING, at(1), at(1), 'unknown-order'))
        self.assertEqual(caught.exception.reason, R.UNKNOWN_ORDER)

    def test_event_receipt_times_monotonic(self):
        broker, _, order_id = submitted()
        broker.handle_event(BrokerEvent('working-1', EventKind.WORKING, at(2), at(2), order_id))
        before = broker.audit
        with self.assertRaises(ValueError):
            broker.handle_event(BrokerEvent('working-2', EventKind.WORKING, at(1), at(1), order_id))
        self.assertEqual(broker.audit, before)

    def test_delayed_origin_preserved_separately_from_receipt(self):
        broker, _, order_id = submitted()
        broker.handle_event(fill_event(order_id, received_seconds=5))
        record = broker.audit[-1]
        self.assertEqual(record.timestamp, at(5))
        self.assertEqual(dict(record.details)['executed_at'], at(1))

    def test_commission_after_fill_is_separate(self):
        broker, _, order_id = submitted()
        broker.handle_event(fill_event(order_id))
        commission = Commission('report-1', 'execution-1', D('0.017'), 'USD', at(2))
        broker.handle_event(BrokerEvent('commission-1', EventKind.COMMISSION, at(2), at(2),
                                       order_id, commission=commission))
        self.assertEqual(broker.commissions(), (commission,))
        self.assertEqual(broker.order_status(order_id).filled_quantity, D(4))
        self.assertEqual(dict(broker.audit[-1].details)['amount'], D('0.017'))

    def test_commission_before_execution_rejected(self):
        broker, _, order_id = submitted()
        commission = Commission('report-1', 'unknown-execution', D('0.01'), 'USD', at(1))
        with self.assertRaises(BrokerOperationError):
            broker.handle_event(BrokerEvent('commission-1', EventKind.COMMISSION, at(1), at(1),
                                           order_id, commission=commission))
        self.assertEqual(broker.commissions(), ())

    def test_duplicate_commission_event_no_second_report(self):
        broker, _, order_id = submitted()
        broker.handle_event(fill_event(order_id))
        event = BrokerEvent('commission-1', EventKind.COMMISSION, at(2), at(2), order_id,
                            commission=Commission('report-1', 'execution-1', D('0.01'), 'USD', at(2)))
        broker.handle_event(event)
        broker.handle_event(replace(event, received_at=at(3)))
        self.assertEqual(len(broker.commissions()), 1)

    def test_successful_modify_keeps_existing_fill_evidence(self):
        broker, order, order_id = submitted()
        broker.handle_event(fill_event(order_id))
        changed = replace(order, quantity=D(8))
        self.assertTrue(broker.replace_order(order_id, changed, at(2), permission(changed)).accepted)
        status = broker.order_status(order_id)
        self.assertEqual((status.filled_quantity, status.remaining_quantity), (D(4), D(4)))
        self.assertEqual(len(broker.fills()), 1)

    def test_modify_cannot_reduce_below_executed_quantity(self):
        broker, order, order_id = submitted()
        broker.handle_event(fill_event(order_id))
        changed = replace(order, quantity=D(3))
        ack = broker.replace_order(order_id, changed, at(2), permission(changed))
        self.assertIn(R.INVALID_MODIFICATION, ack.reasons)
        self.assertEqual(broker.order_status(order_id).request, order)

    def test_fake_account_values_require_explicit_report_not_research_fills(self):
        broker, _, order_id = submitted()
        broker.handle_event(fill_event(order_id))
        self.assertEqual(broker.account_summary(at(2)).cash, D(10000))
        updated = account(cash=D('9995.08'), observed_at=at(2), available_at=at(2))
        broker.report_account(updated, at(2))
        self.assertEqual(broker.account_summary(at(2)).cash, D('9995.08'))

    def test_deterministic_audit_and_lifecycle(self):
        outputs = []
        for _ in range(2):
            broker, _, order_id = submitted()
            broker.handle_event(fill_event(order_id))
            broker.cancel_order(order_id, at(2))
            outputs.append((broker.audit, broker.fills(), broker.completed_orders()))
        self.assertEqual(outputs[0], outputs[1])

    def test_status_and_audit_views_are_immutable(self):
        broker, _, order_id = submitted()
        with self.assertRaises(FrozenInstanceError):
            broker.order_status(order_id).state = OrderState.FILLED
        self.assertIsInstance(broker.audit, tuple)

    def test_blocked_modify_audit_does_not_claim_original_order_was_rejected(self):
        broker, order, order_id = submitted()
        changed = replace(order, quantity=D(20))
        ack = broker.replace_order(order_id, changed, AT, permission(order))
        self.assertFalse(ack.accepted)
        self.assertEqual(broker.audit[-1].state, OrderState.ACKNOWLEDGED)
        self.assertEqual(dict(broker.audit[-1].details)['action_outcome'], 'REJECTED')
        self.assertEqual(broker.order_status(order_id).state, OrderState.ACKNOWLEDGED)

    def test_unknown_cancel_audited_without_transport_effect(self):
        broker = ready()
        self.assertFalse(broker.cancel_order('unknown-1', AT))
        self.assertEqual(broker.audit[-1].reasons, (R.UNKNOWN_ORDER,))
        self.assertEqual(broker.cancellations, [])

    def test_unknown_modify_audited_without_transport_effect(self):
        broker = ready()
        order = request()
        ack = broker.replace_order('unknown-1', order, AT, permission(order))
        self.assertEqual(ack.reasons, (R.UNKNOWN_ORDER,))
        self.assertEqual(broker.modifications, [])

    def test_disconnect_source_error_preserved(self):
        broker = ready()
        broker.handle_event(BrokerEvent('disconnect-1', EventKind.DISCONNECTED, at(1), at(1), error_code=1100))
        self.assertEqual(broker.audit[-1].error_code, 1100)
        self.assertEqual(broker.audit[-1].reasons, (R.BROKER_DISCONNECTED,))

    def test_reconnect_source_error_preserved(self):
        broker = ready()
        broker.disconnect(AT)
        broker.handle_event(BrokerEvent('reconnect-1', EventKind.RECONNECTED, at(1), at(1),
                                       error_code=1102, account=account()))
        self.assertEqual(broker.audit[-1].error_code, 1102)

    def test_repeated_commission_new_event_id_audited_as_duplicate(self):
        broker, _, order_id = submitted()
        broker.handle_event(fill_event(order_id))
        event = BrokerEvent('commission-1', EventKind.COMMISSION, at(2), at(2), order_id,
                            commission=Commission('report-1', 'execution-1', D('0.01'), 'USD', at(2)))
        broker.handle_event(event)
        self.assertFalse(broker.handle_event(replace(event, event_id='commission-repeat', received_at=at(3))))
        self.assertEqual(broker.audit[-1].reasons, (R.DUPLICATE_EVENT,))
        self.assertEqual(len(broker.commissions()), 1)
