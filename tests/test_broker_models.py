from dataclasses import FrozenInstanceError, fields, replace
from decimal import localcontext
from fractions import Fraction
import unittest

from tradingbot_broker.broker import Broker
from tradingbot_broker.models import (AccountSnapshot, BrokerAuditRecord, BrokerEvent, BrokerReason as R,
    Commission, EventKind, Fill, OrderAcknowledgement, OrderState, OrderType, Position, Side, TradingMode)
from tradingbot_broker.safety import Controls
from tests.broker_fixtures import AT, D, account, at, permission, ready, request, submitted, fill_event


class BrokerModelTests(unittest.TestCase):
    def test_provider_neutral_protocol(self):
        self.assertIsInstance(ready(), Broker)

    def test_market_request_has_no_price(self):
        self.assertIsNone(request().limit_price)
        self.assertIsNone(request().stop_price)

    def test_limit_request_requires_price(self):
        with self.assertRaises(ValueError):
            request(order_type=OrderType.LIMIT)
        self.assertEqual(request(order_type=OrderType.LIMIT, limit_price=D('1.234')).limit_price, D('1.234'))

    def test_stop_request_requires_price(self):
        with self.assertRaises(ValueError):
            request(order_type=OrderType.STOP)
        self.assertEqual(request(order_type=OrderType.STOP, stop_price=D('0.1234')).stop_price, D('0.1234'))

    def test_unrelated_price_fields_rejected(self):
        for kw in (dict(limit_price=D(1)), dict(stop_price=D(1)),
                   dict(order_type=OrderType.LIMIT, limit_price=D(1), stop_price=D(1))):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                request(**kw)

    def test_float_bool_nonfinite_and_nonpositive_quantities_rejected(self):
        for q in (1.1, True, D('NaN'), D('Infinity'), D(0), D(-1)):
            with self.subTest(q=q), self.assertRaises((ValueError, TypeError)):
                request(quantity=q)

    def test_untyped_enum_rejected(self):
        for kw in (dict(mode='PAPER'), dict(side='BUY'), dict(order_type='MARKET'), dict(time_in_force='DAY')):
            with self.subTest(kw=kw), self.assertRaises(TypeError):
                request(**kw)

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            request(created_at=AT.replace(tzinfo=None))

    def test_order_window_required(self):
        with self.assertRaises(ValueError):
            request(expires_at=AT)

    def test_request_and_snapshot_immutable(self):
        for obj, field, value in ((request(), 'quantity', D(1)), (account(), 'cash', D(1))):
            with self.subTest(field=field), self.assertRaises(FrozenInstanceError):
                setattr(obj, field, value)

    def test_permission_binds_full_request(self):
        order = request(order_type=OrderType.LIMIT, limit_price=D('1.1'))
        self.assertEqual(permission(order).order, order)
        self.assertNotEqual(permission(order).order, replace(order, limit_price=D('1.2')))

    def test_permission_and_controls_require_actual_booleans(self):
        for value in ('false', 'true', 1, 0, None):
            with self.subTest(value=value), self.assertRaises(TypeError):
                permission(request(), permits_entry=value)
            with self.subTest(value=value), self.assertRaises(TypeError):
                Controls(trading_enabled=value)

    def test_position_identity_and_signed_quantity(self):
        p = Position('security-1', 'DEMO', D('-2'), D('1.2'), 'USD')
        self.assertEqual(p.quantity, D(-2))
        with self.assertRaises(ValueError):
            replace(p, quantity=D(0))

    def test_duplicate_security_positions_rejected(self):
        p = Position('security-1', 'DEMO', D(2), D(1), 'USD')
        with self.assertRaises(ValueError):
            account(positions=(p, replace(p, symbol='OTHER')))

    def test_snapshot_availability_order(self):
        with self.assertRaises(ValueError):
            account(observed_at=at(1))

    def test_event_cannot_arrive_before_occurrence(self):
        with self.assertRaises(ValueError):
            BrokerEvent('event-1', EventKind.DISCONNECTED, at(2), at(1))

    def test_raw_error_text_not_an_error_code(self):
        with self.assertRaises(TypeError):
            BrokerEvent('event-1', EventKind.DISCONNECTED, AT, AT, error_code='private message')

    def test_fill_prices_do_not_round(self):
        fill = Fill('fill-1', 'fake-1', 'security-1', 'DEMO', Side.BUY, D('2.5'), D('0.123456789'), 'USD', AT)
        self.assertEqual(fill.price, D('0.123456789'))
        with self.assertRaises(FrozenInstanceError):
            fill.quantity = D(10)

    def test_commissions_are_not_zero_friction_assumptions(self):
        c = Commission('report-1', 'fill-1', D('0.0037'), 'USD', AT)
        self.assertEqual(c.amount, D('0.0037'))

    def test_audit_fields_preserve_request_and_error(self):
        order = request(order_type=OrderType.STOP, stop_price=D('0.9876'))
        record = BrokerAuditRecord(AT, 'ORDER_BLOCKED', TradingMode.PAPER, TradingMode.PAPER,
                                  order, None, OrderState.REJECTED, (R.TRADING_DISABLED,), error_code=201)
        self.assertEqual((record.request.symbol, record.request.side, record.request.quantity,
                          record.request.stop_price, record.error_code), ('DEMO', Side.BUY, D(10), D('0.9876'), 201))

    def test_audit_no_float_or_mutable_details(self):
        broker = ready()
        record = broker.audit[-1]
        for details in ((('amount', 1.1),), [('x', True)], (('x', 1), ('x', 2))):
            with self.subTest(details=details), self.assertRaises((TypeError, ValueError)):
                replace(record, details=details)

    def test_account_contract_has_no_credentials_fields(self):
        self.assertFalse({'account_id', 'account_number', 'password', 'api_key'} & {f.name for f in fields(AccountSnapshot)})

    def test_fill_average_exact_under_low_decimal_precision(self):
        broker, order, order_id = submitted(order=request(quantity=D(3)))
        with localcontext() as context:
            context.prec = 2
            broker.handle_event(fill_event(order_id, '1', '0.123456789'))
            broker.handle_event(fill_event(order_id, '2', '0.123456788', fill_id='execution-2',
                event_id='event-2', kind=EventKind.FILLED, seconds=2))
        status = broker.order_status(order_id)
        self.assertEqual(status.average_fill_price, Fraction(370370365, 3000000000))
        self.assertEqual(status.remaining_quantity, D(0))

    def test_invalid_output_models_rejected(self):
        with self.assertRaises(TypeError):
            OrderAcknowledgement('candidate-1', None, OrderState.REJECTED, AT, 'true')
