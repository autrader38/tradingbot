"""Read observations without invented submission times or executable intents."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import math

from .models import (AccountSnapshot, Commission, Fill, OrderState, OrderType, Side,
                     amount, enum, identifier)
from tradingbot_backtest.market import validate_timestamp


class AccountMode(StrEnum):
    PAPER = 'PAPER'
    LIVE = 'LIVE'
    UNKNOWN = 'UNKNOWN'


class ReadOnlyError(ValueError):
    """Only fixed internal codes, never raw SDK exception/message text."""
    def __init__(self, code):
        identifier(code)
        self.code = code
        super().__init__(code)


def broker_decimal(value):
    # SDK float precision cannot be recovered. Preserve its text representation.
    if type(value) is float:
        if not math.isfinite(value) or value == float('1.7976931348623157e308'):
            raise ReadOnlyError('MALFORMED_BROKER_VALUE')
        value = str(value)
    if type(value) not in (str, Decimal, int):
        raise ReadOnlyError('MALFORMED_BROKER_VALUE')
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ReadOnlyError('MALFORMED_BROKER_VALUE') from None
    if not result.is_finite():
        raise ReadOnlyError('MALFORMED_BROKER_VALUE')
    return result


@dataclass(frozen=True, slots=True)
class AccountIdentity:
    masked_id: str
    mode: AccountMode = AccountMode.UNKNOWN
    evidence: str = 'TWS_NO_ENVIRONMENT_ATTESTATION'

    def __post_init__(self):
        identifier(self.masked_id)
        identifier(self.evidence)
        enum(self.mode, AccountMode)


@dataclass(frozen=True, slots=True)
class ObservedOrder:
    """Broker observation, NOT an OrderRequest/authorization or fabricated fill."""
    broker_order_id: str
    security_id: str
    symbol: str
    side: Side
    order_type: OrderType | None
    quantity: Decimal
    currency: str
    state: OrderState
    available_at: datetime
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    source_id: str = 'ibkr-tws-readonly'

    def __post_init__(self):
        for v in (self.broker_order_id, self.security_id, self.symbol, self.currency, self.source_id):
            identifier(v)
        enum(self.side, Side)
        enum(self.state, OrderState)
        if self.order_type is not None:
            enum(self.order_type, OrderType)
        amount(self.quantity, positive=True)
        for v in (self.limit_price, self.stop_price):
            if v is not None:
                amount(v, positive=True)
        validate_timestamp(self.available_at)


@dataclass(frozen=True, slots=True)
class ReadOnlySnapshot:
    account: AccountSnapshot
    identities: tuple[AccountIdentity, ...]
    working_orders: tuple[ObservedOrder, ...]
    completed_orders: tuple[ObservedOrder, ...]
    fills: tuple[Fill, ...]
    commissions: tuple[Commission, ...]
    broker_time: datetime
    server_version: int
    completed_orders_available: bool

    def __post_init__(self):
        if not isinstance(self.account, AccountSnapshot) or self.account.mode is not None:
            raise TypeError('TWS environment is not independently attested')
        for values, kind in ((self.identities, AccountIdentity), (self.working_orders, ObservedOrder),
                             (self.completed_orders, ObservedOrder), (self.fills, Fill),
                             (self.commissions, Commission)):
            if type(values) is not tuple or any(not isinstance(v, kind) for v in values):
                raise TypeError('Immutable typed read observations required')
        validate_timestamp(self.broker_time)
        if type(self.server_version) is not int or type(self.completed_orders_available) is not bool:
            raise TypeError('Explicit server capability required')
