"""Qualified Phase 5/6 references for anonymous, temporary-journal SDK tests."""

from dataclasses import fields, is_dataclass, replace
from decimal import Decimal
from datetime import timedelta

from tradingbot_backtest.entry_validation import EntryValidator
from tradingbot_backtest.trade_construction import PortfolioSnapshot, TradeConstructor
from tests import broker_fixtures
from tests.readonly_fixtures import Clock
from tests.entry_fixtures import handoff_fixture
from tests.test_trade_construction import SuppliedTicks


AT = broker_fixtures.AT + timedelta(minutes=15)


def request(client_id='candidate-1', **changes):
    changes.setdefault('created_at', AT)
    return broker_fixtures.request(client_id, **changes)


def permission(order, **changes):
    changes.setdefault('available_at', AT)
    return broker_fixtures.permission(order, **changes)


def contract(**changes):
    return broker_fixtures.contract(**changes)


def dispatch_clock():
    clock = Clock()
    clock.now = AT
    return clock


def _anonymous_identity(value):
    if type(value) is str:
        return {'security': 'security-1', 'TEST': 'DEMO'}.get(value, value)
    if type(value) is tuple:
        return tuple(_anonymous_identity(item) for item in value)
    if is_dataclass(value):
        return replace(value, **{field.name: _anonymous_identity(getattr(value, field.name))
                                 for field in fields(value) if field.init})
    return value


def dispatch_references():
    handoff, context, calendar, _ = handoff_fixture(scheduled=AT)
    approval = EntryValidator(calendar, run_id='dispatch-fixture', data_version='fixture').evaluate(
        handoff, context).approval
    approval = _anonymous_identity(approval)
    # Exact Phase 6 arithmetic produces ten shares at the supplied reference
    # stop. These reference account values never become broker accounting.
    account = PortfolioSnapshot(AT, AT, Decimal(60), Decimal(10000), Decimal(10000),
                                Decimal(0), (), True, 'dispatch-reference-account')
    ticks = SuppliedTicks(AT)
    ticks.rules = {kind: replace(rule, security_id='security-1')
                   for kind, rule in ticks.rules.items()}
    position = TradeConstructor(run_id='dispatch-fixture', data_version='fixture').construct(
        approval, account, ticks, trade_id='dispatch-reference').position
    assert position is not None and position.quantity == 10
    return approval, position
