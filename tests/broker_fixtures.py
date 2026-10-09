"""Anonymous offline execution fixtures; no accounts or connection endpoints."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tradingbot_broker.models import (AccountSnapshot, BrokerEvent, EventKind, Fill,
    OrderRequest, OrderType, RiskPermission, Side, TradingMode)
from tradingbot_broker.fake import FakeBroker
from tradingbot_broker.ibkr import (IBKRAccountData, IBKRAdapter, IBKRContract, InMemoryIBKRTransport)

AT = datetime(2026, 7, 1, 14, 0, tzinfo=timezone.utc)
END = AT + timedelta(hours=1)
D = Decimal


def at(seconds=0):
    return AT + timedelta(seconds=seconds)


def request(client_id='candidate-1', **kwargs):
    values = dict(client_order_id=client_id, security_id='security-1', symbol='DEMO',
                  mode=TradingMode.PAPER, side=Side.BUY, order_type=OrderType.MARKET,
                  quantity=D('10'), currency='USD', created_at=AT, expires_at=END)
    values.update(kwargs)
    return OrderRequest(**values)


def permission(order, **kwargs):
    values = dict(order=order, permits_entry=True, available_at=AT, valid_until=END,
                  source_id='risk-fixture', reason_code='APPROVED')
    values.update(kwargs)
    return RiskPermission(**values)


def account(**kwargs):
    values = dict(mode=TradingMode.PAPER, cash=D('10000'), equity=D('10000'),
                  buying_power=D('10000'), currency='USD', observed_at=AT,
                  available_at=AT, valid_until=END, source_id='account-fixture', verified=True)
    values.update(kwargs)
    return AccountSnapshot(**values)


def ready(broker=None):
    broker = broker or FakeBroker(account())
    broker.connect(AT)
    broker.set_trading_enabled(True, AT)
    return broker


def contract(**kwargs):
    values = dict(security_id='security-1', symbol='DEMO', con_id=1, exchange='SIM',
                  currency='USD', valid_from=AT, valid_until=END, available_at=AT,
                  source_id='contract-fixture', verified=True)
    values.update(kwargs)
    return IBKRContract(**values)


def ibkr_account(**kwargs):
    values = dict(mode=TradingMode.PAPER, cash='10000', equity='10000', buying_power='10000',
                  currency='USD', observed_at=AT, available_at=AT, valid_until=END,
                  source_id='ibkr-fixture', verified=True)
    values.update(kwargs)
    return IBKRAccountData(**values)


def ibkr(**kwargs):
    transport = InMemoryIBKRTransport(ibkr_account())
    return IBKRAdapter(transport, (contract(),), **kwargs), transport


def submitted(broker=None, order=None):
    broker = ready(broker)
    order = order or request()
    ack = broker.place_order(order, AT, permission(order))
    assert ack.accepted
    return broker, order, ack.broker_order_id


def fill_event(order_id, quantity='4', price='1.23', *, fill_id='execution-1', event_id='event-1',
               kind=EventKind.PARTIALLY_FILLED, seconds=1, received_seconds=None, **kwargs):
    fill = Fill(fill_id, order_id, 'security-1', 'DEMO', Side.BUY, D(quantity), D(price), 'USD', at(seconds))
    values = dict(event_id=event_id, kind=kind, occurred_at=at(seconds),
                  received_at=at(seconds if received_seconds is None else received_seconds),
                  broker_order_id=order_id, fill=fill)
    values.update(kwargs)
    return BrokerEvent(**values)
