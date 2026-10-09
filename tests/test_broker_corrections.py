"""Regression coverage for the four reviewed offline broker defects."""

from dataclasses import asdict, replace
from fractions import Fraction
import json
import unittest

from tradingbot_broker.broker import BrokerOperationError
from tradingbot_broker.models import (BrokerEvent, BrokerReason as R, ConnectionStatus,
                                     EventKind, OrderState, TradingMode)
from tests.broker_fixtures import (AT, D, account, at, fill_event, ibkr,
                                  permission, ready, request)


class AccountEvidenceCorrectionTests(unittest.TestCase):
    def reject_snapshot(self, broker, snapshot, received, reason):
        before = broker._account, broker._accepted_account
        with self.assertRaises(BrokerOperationError) as caught:
            broker.report_account(snapshot, received)
        self.assertEqual(caught.exception.reason, reason)
        self.assertEqual((broker._account, broker._accepted_account), before)
        self.assertEqual(broker.audit[-1].reasons, (reason,))

    def test_newer_live_cannot_be_downgraded_by_delayed_paper(self):
        broker = ready()
        newer = account(mode=TradingMode.LIVE, observed_at=at(5), available_at=at(5))
        broker.report_account(newer, at(5))
        self.reject_snapshot(broker, account(), at(6), R.STALE_ACCOUNT_EVIDENCE)
        self.assertEqual(broker.account_summary(at(6)), newer)
        order = request()
        ack = broker.place_order(order, at(6), permission(order))
        self.assertIn(R.ACCOUNT_MODE_MISMATCH, ack.reasons)
        self.assertEqual(broker.submissions, [])

    def test_older_balances_cannot_replace_newer_balances(self):
        broker = ready()
        newer = account(cash=D(100), equity=D(200), buying_power=D(100),
                        observed_at=at(5), available_at=at(5))
        broker.report_account(newer, at(5))
        self.reject_snapshot(broker, account(), at(6), R.STALE_ACCOUNT_EVIDENCE)
        self.assertEqual(broker.account_summary(at(6)), newer)
        details = dict(broker.audit[-2].details)
        self.assertEqual((details['observed_at'], details['accepted_observed_at']), (AT, at(5)))

    def test_local_reconnect_rejects_stale_snapshot_and_retains_boundary(self):
        broker = ready()
        newer = account(mode=TradingMode.LIVE, observed_at=at(5), available_at=at(5))
        broker.report_account(newer, at(5))
        broker.disconnect(at(6))
        self.assertIsNone(broker._account)
        self.assertEqual(broker.connect(at(7)), ConnectionStatus.DISCONNECTED)
        self.assertEqual(broker._accepted_account, newer)
        self.assertEqual(broker.audit[-1].reasons, (R.STALE_ACCOUNT_EVIDENCE,))
        order = request()
        self.assertFalse(broker.place_order(order, at(7), permission(order)).accepted)
        self.assertEqual(broker.submissions, [])

    def test_callback_reconnect_rejects_stale_account_without_restoring_connection(self):
        broker = ready()
        newer = account(observed_at=at(5), available_at=at(5), cash=D(100))
        broker.report_account(newer, at(5))
        broker.disconnect(at(6))
        self.assertFalse(broker.handle_event(BrokerEvent('stale-account-reconnect',
            EventKind.RECONNECTED, at(7), at(8), account=account())))
        self.assertEqual(broker.connection_status, ConnectionStatus.DISCONNECTED)
        self.assertIsNone(broker._account)
        self.assertEqual(broker._accepted_account, newer)
        self.assertEqual(broker._connection_at, at(6))

    def test_genuinely_newer_snapshot_updates_normally(self):
        broker = ready()
        newer = account(observed_at=at(1), available_at=at(1), cash=D('9999.25'))
        broker.report_account(newer, at(2))
        self.assertEqual(broker.account_summary(at(2)), newer)
        self.assertFalse(broker.reconciliation_required)

    def test_newer_reconnect_snapshot_updates_normally(self):
        broker = ready()
        broker.disconnect(at(1))
        newer = account(observed_at=at(2), available_at=at(2), cash=D(9000))
        broker.reported_account = newer
        self.assertEqual(broker.connect(at(2)), ConnectionStatus.CONNECTED)
        self.assertEqual(broker.account_summary(at(2)), newer)

    def test_equal_observation_identical_snapshot_is_idempotent(self):
        broker = ready()
        original = account()
        broker.report_account(replace(original), at(1))
        self.assertEqual(broker.account_summary(at(1)), original)
        self.assertFalse(broker.reconciliation_required)

    def test_equal_observation_conflicting_mode_is_fail_closed_in_either_order(self):
        for initial, replacement in ((TradingMode.PAPER, TradingMode.LIVE),
                                     (TradingMode.LIVE, TradingMode.PAPER)):
            with self.subTest(initial=initial):
                broker = ready()
                first = account(mode=initial, observed_at=at(1), available_at=at(1))
                broker.report_account(first, at(1))
                self.reject_snapshot(broker, replace(first, mode=replacement), at(2),
                                     R.CONFLICTING_ACCOUNT_EVIDENCE)
                self.assertTrue(broker.reconciliation_required)
                self.assertEqual(broker.account_summary(at(2)), first)
                order = request()
                self.assertFalse(broker.place_order(order, at(2), permission(order)).accepted)
                self.assertEqual(broker.submissions, [])

    def test_equal_observation_conflicting_balances_are_not_merged(self):
        broker = ready()
        self.reject_snapshot(broker, account(cash=D(1)), at(1), R.CONFLICTING_ACCOUNT_EVIDENCE)
        self.assertEqual(broker.account_summary(at(1)), account())
        self.assertTrue(broker.reconciliation_required)

    def test_equal_observation_conflicting_reconnect_preserves_disconnected_state(self):
        broker = ready()
        broker.disconnect(at(1))
        broker.reported_account = account(mode=TradingMode.LIVE)
        self.assertEqual(broker.connect(at(2)), ConnectionStatus.DISCONNECTED)
        self.assertIsNone(broker._account)
        self.assertEqual(broker._accepted_account, account())
        self.assertTrue(broker.reconciliation_required)

    def test_unavailable_evidence_does_not_advance_observation_boundary(self):
        broker = ready()
        future = account(mode=TradingMode.LIVE, observed_at=at(5), available_at=at(6))
        self.reject_snapshot(broker, future, at(1), R.ACCOUNT_DATA_UNAVAILABLE)
        self.assertEqual(dict(broker.audit[-1].details), {'available_at': at(6)})
        newer = account(observed_at=at(2), available_at=at(2))
        broker.report_account(newer, at(2))
        self.assertEqual(broker.account_summary(at(2)), newer)

    def test_unknown_newer_mode_cannot_be_overridden_by_older_paper(self):
        broker = ready()
        broker.report_account(account(mode=None, observed_at=at(2), available_at=at(2)), at(2))
        self.reject_snapshot(broker, account(), at(3), R.STALE_ACCOUNT_EVIDENCE)
        order = request()
        ack = broker.place_order(order, at(3), permission(order))
        self.assertIn(R.ACCOUNT_MODE_UNVERIFIED, ack.reasons)
        self.assertEqual(broker.submissions, [])

    def test_ibkr_handshake_uses_shared_freshness_gate(self):
        adapter, transport = ibkr()
        ready(adapter)
        latest = account(mode=TradingMode.LIVE, observed_at=at(2), available_at=at(2))
        adapter.report_account(latest, at(2))
        adapter.disconnect(at(3))
        self.assertEqual(adapter.connect(at(4)), ConnectionStatus.DISCONNECTED)
        self.assertEqual(adapter._accepted_account, latest)
        order = request()
        self.assertFalse(adapter.place_order(order, at(4), permission(order)).accepted)
        self.assertEqual(transport.submissions, [])


class ConnectionChronologyCorrectionTests(unittest.TestCase):
    def test_newer_disconnect_ignores_older_reconnect(self):
        broker = ready()
        broker.handle_event(BrokerEvent('disconnect', EventKind.DISCONNECTED, at(5), at(5)))
        self.assertFalse(broker.handle_event(BrokerEvent('old-reconnect', EventKind.RECONNECTED,
                                                        at(1), at(6), account=account())))
        self.assertEqual(broker.connection_status, ConnectionStatus.DISCONNECTED)
        self.assertIsNone(broker._account)
        self.assertEqual(broker._connection_at, at(5))
        self.assertEqual(broker.audit[-1].reasons, (R.STALE_CONNECTION_EVENT,))
        order = request()
        self.assertFalse(broker.place_order(order, at(6), permission(order)).accepted)
        self.assertEqual(broker.submissions, [])

    def test_genuinely_later_reconnect_succeeds(self):
        broker = ready()
        broker.disconnect(at(1))
        self.assertTrue(broker.handle_event(BrokerEvent('later-reconnect', EventKind.RECONNECTED,
                                                       at(2), at(3), account=account())))
        order = request()
        self.assertTrue(broker.place_order(order, at(3), permission(order)).accepted)

    def test_newer_reconnect_ignores_older_disconnect(self):
        broker = ready()
        broker.disconnect(at(1))
        broker.handle_event(BrokerEvent('reconnect', EventKind.RECONNECTED, at(3), at(3), account=account()))
        self.assertFalse(broker.handle_event(BrokerEvent('old-disconnect', EventKind.DISCONNECTED, at(2), at(4))))
        self.assertEqual(broker.connection_status, ConnectionStatus.CONNECTED)
        self.assertEqual(broker.account_summary(at(4)), account())
        self.assertEqual(broker._connection_at, at(3))

    def test_equal_time_disconnect_wins_in_both_callback_orders(self):
        for reconnect_first in (True, False):
            with self.subTest(reconnect_first=reconnect_first):
                broker = ready()
                broker.disconnect(at(1))
                reconnect = BrokerEvent('reconnect', EventKind.RECONNECTED, at(2), at(2), account=account())
                disconnect = BrokerEvent('disconnect', EventKind.DISCONNECTED, at(2), at(2))
                first, second = (reconnect, disconnect) if reconnect_first else (disconnect, reconnect)
                broker.handle_event(first)
                broker.handle_event(replace(second, received_at=at(3)))
                self.assertEqual(broker.connection_status, ConnectionStatus.DISCONNECTED)
                self.assertIsNone(broker._account)
                self.assertEqual(broker._connection_at, at(2))

    def test_local_connect_cannot_override_equal_time_disconnect(self):
        broker = ready()
        broker.disconnect(at(1))
        self.assertEqual(broker.connect(at(1)), ConnectionStatus.DISCONNECTED)
        self.assertEqual(broker.connect(at(2)), ConnectionStatus.CONNECTED)

    def test_local_disconnect_also_blocks_delayed_callback_reconnect(self):
        broker = ready()
        broker.disconnect(at(5))
        self.assertFalse(broker.handle_event(BrokerEvent('old-reconnect', EventKind.RECONNECTED,
                                                        at(4), at(6), account=account())))
        self.assertEqual(broker.connection_status, ConnectionStatus.DISCONNECTED)

    def test_local_connect_also_blocks_delayed_callback_disconnect(self):
        broker = ready()
        broker.disconnect(at(1))
        broker.connect(at(3))
        self.assertFalse(broker.handle_event(BrokerEvent('old-disconnect', EventKind.DISCONNECTED, at(2), at(4))))
        self.assertEqual(broker.connection_status, ConnectionStatus.CONNECTED)

    def test_reconnect_does_not_clear_emergency_pause_or_global_control(self):
        broker = ready()
        broker.emergency_stop(at(1))
        broker.pause_new_entries(at(1))
        broker.set_trading_enabled(False, at(1))
        controls = broker.controls
        broker.disconnect(at(2))
        broker.handle_event(BrokerEvent('reconnect', EventKind.RECONNECTED, at(3), at(3), account=account()))
        self.assertEqual(broker.controls, controls)
        order = request()
        ack = broker.place_order(order, at(3), permission(order))
        self.assertTrue({R.EMERGENCY_STOP, R.ENTRIES_PAUSED, R.TRADING_DISABLED}.issubset(ack.reasons))
        self.assertEqual(broker.submissions, [])

    def test_stale_connection_replay_is_idempotent(self):
        broker = ready()
        broker.disconnect(at(5))
        event = BrokerEvent('old-reconnect', EventKind.RECONNECTED, at(1), at(6), account=account())
        broker.handle_event(event)
        self.assertFalse(broker.handle_event(replace(event, received_at=at(7))))
        self.assertEqual(broker.connection_status, ConnectionStatus.DISCONNECTED)
        self.assertEqual(broker.audit[-1].reasons, (R.DUPLICATE_EVENT,))


class SubmissionChronologyCorrectionTests(unittest.TestCase):
    def delayed_submission(self, adapter=False):
        broker = ibkr()[0] if adapter else ready()
        if adapter:
            ready(broker)
        order = request()
        ack = broker.place_order(order, at(10), permission(order))
        self.assertTrue(ack.accepted)
        return broker, order, ack.broker_order_id

    def assert_no_economic_mutation(self, broker, order_id, event):
        before = broker.order_status(order_id), broker.fills(), broker.commissions(), broker._account
        with self.assertRaises(BrokerOperationError) as caught:
            broker.handle_event(event)
        self.assertEqual(caught.exception.reason, R.INVALID_EVENT)
        self.assertEqual((broker.order_status(order_id), broker.fills(), broker.commissions(), broker._account), before)
        self.assertTrue(broker.reconciliation_required)
        self.assertEqual(broker.audit[-1].action, 'EVENT_REJECTED')
        self.assertEqual(dict(broker.audit[-1].details)['submitted_at'], at(10))

    def test_execution_before_actual_submission_has_no_economic_effect(self):
        broker, _, order_id = self.delayed_submission()
        self.assert_no_economic_mutation(broker, order_id, fill_event(order_id, '10',
            kind=EventKind.FILLED, seconds=1, received_seconds=11))
        self.assertEqual(dict(broker.audit[-1].details)['executed_at'], at(1))

    def test_execution_timestamp_checked_even_if_event_occurrence_is_later(self):
        broker, _, order_id = self.delayed_submission()
        event = fill_event(order_id, '10', kind=EventKind.FILLED, seconds=1, received_seconds=12)
        self.assert_no_economic_mutation(broker, order_id, replace(event, occurred_at=at(11)))

    def test_lifecycle_events_cannot_predate_actual_submission(self):
        for kind in (EventKind.SUBMITTED, EventKind.ACKNOWLEDGED, EventKind.WORKING,
                     EventKind.CANCELLED, EventKind.REJECTED, EventKind.EXPIRED):
            with self.subTest(kind=kind):
                broker, _, order_id = self.delayed_submission()
                self.assert_no_economic_mutation(broker, order_id,
                    BrokerEvent('early-status', kind, at(1), at(11), order_id))

    def test_execution_exactly_at_actual_submission_is_valid(self):
        broker, _, order_id = self.delayed_submission()
        broker.handle_event(fill_event(order_id, '10', kind=EventKind.FILLED, seconds=10))
        self.assertEqual(broker.order_status(order_id).filled_quantity, D(10))
        self.assertEqual(broker.order_status(order_id).state, OrderState.FILLED)

    def test_delayed_post_submission_execution_is_valid(self):
        broker, _, order_id = self.delayed_submission()
        broker.handle_event(fill_event(order_id, '10', kind=EventKind.FILLED, seconds=11, received_seconds=100))
        self.assertEqual(broker.fills()[0].executed_at, at(11))
        self.assertEqual(broker.audit[-1].timestamp, at(100))
        self.assertEqual(dict(broker.audit[-1].details)['submitted_at'], at(10))

    def test_partial_then_full_after_submission_remain_exact(self):
        broker, _, order_id = self.delayed_submission()
        broker.handle_event(fill_event(order_id, '4', '1.23', seconds=11, received_seconds=20))
        broker.handle_event(fill_event(order_id, '6', '1.25', kind=EventKind.FILLED,
            fill_id='execution-2', event_id='event-2', seconds=12, received_seconds=21))
        status = broker.order_status(order_id)
        self.assertEqual((status.filled_quantity, status.remaining_quantity), (D(10), D(0)))
        self.assertEqual(status.average_fill_price, Fraction(621, 500))

    def test_execution_chronology_is_independent_of_connection_watermark(self):
        broker, _, order_id = self.delayed_submission()
        broker.disconnect(at(20))
        broker.connect(at(21))
        broker.handle_event(fill_event(order_id, '10', kind=EventKind.FILLED, seconds=11, received_seconds=22))
        self.assertEqual(broker.order_status(order_id).filled_quantity, D(10))

    def test_modification_preserves_original_submission_boundary(self):
        broker, order, order_id = self.delayed_submission()
        changed = replace(order, quantity=D(8))
        self.assertTrue(broker.replace_order(order_id, changed, at(15), permission(changed)).accepted)
        broker.handle_event(fill_event(order_id, '4', seconds=11, received_seconds=16))
        self.assertEqual(broker._submitted_at[order_id], at(10))
        self.assertEqual(broker.order_status(order_id).remaining_quantity, D(4))

    def test_modification_cannot_legitimize_pre_submission_execution(self):
        broker, order, order_id = self.delayed_submission()
        changed = replace(order, quantity=D(8))
        broker.replace_order(order_id, changed, at(15), permission(changed))
        self.assert_no_economic_mutation(broker, order_id,
            fill_event(order_id, '4', seconds=1, received_seconds=16))

    def test_ibkr_callback_uses_actual_submission_boundary(self):
        from tradingbot_broker.ibkr import IBKRExecution
        broker, _, order_id = self.delayed_submission(adapter=True)
        before = broker.order_status(order_id)
        with self.assertRaises(BrokerOperationError):
            broker.receive_execution(IBKRExecution('early-fill', 1, 1, 'BOT', '10', '1.23', at(1)),
                                     event_id='early-event', received_at=at(11))
        self.assertEqual(broker.order_status(order_id), before)
        self.assertEqual(broker.fills(), ())


class AuditImmutabilityCorrectionTests(unittest.TestCase):
    def test_mutable_nested_pair_is_rejected(self):
        original = ready().audit[-1]
        pair = ['x', 1]
        with self.assertRaises(TypeError):
            replace(original, details=(pair,))
        pair[1] = 99
        self.assertEqual(original.details, (('enabled', True),))

    def test_malformed_detail_entries_are_rejected(self):
        original = ready().audit[-1]
        for detail in (None, 'xy', ('x',), ('x', 1, 2), {'x': 1}, 1):
            with self.subTest(detail=detail), self.assertRaises(TypeError):
                replace(original, details=(detail,))

    def test_nested_mutable_detail_value_is_rejected(self):
        original = ready().audit[-1]
        for value in ([], {}, set()):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(original, details=(('x', value),))

    def test_valid_immutable_details_preserve_order_and_exact_values(self):
        details = (('quantity', D('0.123456789')), ('ratio', Fraction(1, 3)), ('at', AT),
                   ('flag', False), ('count', 2), ('text', 'anonymous'), ('none', None))
        record = replace(ready().audit[-1], details=details)
        self.assertEqual(record.details, details)
        with self.assertRaises(TypeError):
            record.details[0][1] = D(99)

    def test_equality_and_serialization_remain_deterministic(self):
        records = [replace(ready().audit[-1], details=(('second', D('1.20')), ('first', Fraction(1, 3))))
                   for _ in range(2)]
        self.assertEqual(records[0], records[1])
        self.assertEqual(json.dumps(asdict(records[0]), default=str, sort_keys=True),
                         json.dumps(asdict(records[1]), default=str, sort_keys=True))
