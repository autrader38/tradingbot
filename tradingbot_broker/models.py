"""Immutable execution contracts, separate from frozen research fill models."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
import re

from tradingbot_backtest.market import validate_timestamp
from tradingbot_backtest.numerics import subtract


class TradingMode(StrEnum):
    PAPER = 'PAPER'
    LIVE = 'LIVE'


class ConnectionStatus(StrEnum):
    DISCONNECTED = 'DISCONNECTED'
    CONNECTED = 'CONNECTED'


class Side(StrEnum):
    BUY = 'BUY'
    SELL = 'SELL'


class OrderType(StrEnum):
    MARKET = 'MARKET'
    LIMIT = 'LIMIT'
    STOP = 'STOP'


class TimeInForce(StrEnum):
    DAY = 'DAY'


class OrderIntent(StrEnum):
    ENTRY = 'ENTRY'
    EXIT = 'EXIT'
    PROTECTIVE = 'PROTECTIVE'


class OrderState(StrEnum):
    SUBMITTED = 'SUBMITTED'
    ACKNOWLEDGED = 'ACKNOWLEDGED'
    WORKING = 'WORKING'
    PARTIALLY_FILLED = 'PARTIALLY_FILLED'
    FILLED = 'FILLED'
    CANCELLED = 'CANCELLED'
    REJECTED = 'REJECTED'
    EXPIRED = 'EXPIRED'
    UNKNOWN = 'UNKNOWN'


TERMINAL_STATES = frozenset((OrderState.FILLED, OrderState.CANCELLED,
                            OrderState.REJECTED, OrderState.EXPIRED))


class EventKind(StrEnum):
    SUBMITTED = 'SUBMITTED'
    ACKNOWLEDGED = 'ACKNOWLEDGED'
    WORKING = 'WORKING'
    PARTIALLY_FILLED = 'PARTIALLY_FILLED'
    FILLED = 'FILLED'
    CANCELLED = 'CANCELLED'
    REJECTED = 'REJECTED'
    EXPIRED = 'EXPIRED'
    COMMISSION = 'COMMISSION'
    DISCONNECTED = 'DISCONNECTED'
    RECONNECTED = 'RECONNECTED'


class BrokerReason(StrEnum):
    OK = 'OK'
    LIVE_EXECUTION_DISABLED = 'LIVE_EXECUTION_DISABLED'
    REQUEST_MODE_MISMATCH = 'REQUEST_MODE_MISMATCH'
    ACCOUNT_MODE_UNVERIFIED = 'ACCOUNT_MODE_UNVERIFIED'
    ACCOUNT_MODE_MISMATCH = 'ACCOUNT_MODE_MISMATCH'
    ACCOUNT_DATA_UNAVAILABLE = 'ACCOUNT_DATA_UNAVAILABLE'
    ACCOUNT_DATA_EXPIRED = 'ACCOUNT_DATA_EXPIRED'
    STALE_ACCOUNT_EVIDENCE = 'STALE_ACCOUNT_EVIDENCE'
    CONFLICTING_ACCOUNT_EVIDENCE = 'CONFLICTING_ACCOUNT_EVIDENCE'
    STALE_CONNECTION_EVENT = 'STALE_CONNECTION_EVENT'
    BROKER_DISCONNECTED = 'BROKER_DISCONNECTED'
    TRADING_DISABLED = 'TRADING_DISABLED'
    EMERGENCY_STOP = 'EMERGENCY_STOP'
    ENTRIES_PAUSED = 'ENTRIES_PAUSED'
    ORDER_NOT_YET_VALID = 'ORDER_NOT_YET_VALID'
    ORDER_EXPIRED = 'ORDER_EXPIRED'
    RISK_PERMISSION_UNAVAILABLE = 'RISK_PERMISSION_UNAVAILABLE'
    RISK_PERMISSION_NOT_YET_AVAILABLE = 'RISK_PERMISSION_NOT_YET_AVAILABLE'
    RISK_PERMISSION_EXPIRED = 'RISK_PERMISSION_EXPIRED'
    RISK_PERMISSION_MISMATCH = 'RISK_PERMISSION_MISMATCH'
    PORTFOLIO_ENTRY_DENIED = 'PORTFOLIO_ENTRY_DENIED'
    DUPLICATE_ORDER = 'DUPLICATE_ORDER'
    DUPLICATE_EVENT = 'DUPLICATE_EVENT'
    CONFLICTING_EVENT = 'CONFLICTING_EVENT'
    INVALID_EVENT = 'INVALID_EVENT'
    INVALID_TRANSITION = 'INVALID_TRANSITION'
    UNKNOWN_ORDER = 'UNKNOWN_ORDER'
    TERMINAL_ORDER = 'TERMINAL_ORDER'
    INVALID_MODIFICATION = 'INVALID_MODIFICATION'
    BROKER_REJECTED = 'BROKER_REJECTED'
    TRANSPORT_FAILURE = 'TRANSPORT_FAILURE'
    RECONCILIATION_REQUIRED = 'RECONCILIATION_REQUIRED'
    FLATTEN_PLAN_ONLY = 'FLATTEN_PLAN_ONLY'
    CONTRACT_UNAVAILABLE = 'CONTRACT_UNAVAILABLE'
    CONTRACT_MISMATCH = 'CONTRACT_MISMATCH'
    READ_ONLY_BROKER_TRANSPORT = 'READ_ONLY_BROKER_TRANSPORT'


def identifier(value: str) -> None:
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}', value):
        raise ValueError('Supply a non-secret bounded identifier, not a message or credential')


def amount(value: Decimal, *, positive=False, signed=False) -> None:
    if type(value) is not Decimal:
        raise TypeError('Broker amounts require Decimal, never float or bool')
    if not value.is_finite() or (positive and value <= 0) or (not signed and value < 0):
        raise ValueError('Invalid exact broker amount')


def enum(value, expected) -> None:
    if not isinstance(value, expected):
        raise TypeError(f'Supply {expected.__name__}, not an untyped value')


@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str
    security_id: str
    symbol: str
    mode: TradingMode
    side: Side
    order_type: OrderType
    quantity: Decimal
    currency: str
    created_at: datetime
    expires_at: datetime
    intent: OrderIntent = OrderIntent.ENTRY
    time_in_force: TimeInForce = TimeInForce.DAY
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None

    def __post_init__(self):
        for v in (self.client_order_id, self.security_id, self.symbol, self.currency):
            identifier(v)
        for v, t in ((self.mode, TradingMode), (self.side, Side), (self.order_type, OrderType),
                     (self.intent, OrderIntent), (self.time_in_force, TimeInForce)):
            enum(v, t)
        amount(self.quantity, positive=True)
        validate_timestamp(self.created_at)
        validate_timestamp(self.expires_at)
        if self.expires_at <= self.created_at:
            raise ValueError('Explicit order validity window required')
        for v in (self.limit_price, self.stop_price):
            if v is not None:
                amount(v, positive=True)
        if ((self.order_type == OrderType.MARKET and (self.limit_price is not None or self.stop_price is not None))
                or (self.order_type == OrderType.LIMIT and (self.limit_price is None or self.stop_price is not None))
                or (self.order_type == OrderType.STOP and (self.stop_price is None or self.limit_price is not None))):
            raise ValueError('Supply exactly the price required by the order type')


@dataclass(frozen=True, slots=True)
class RiskPermission:
    """An external risk decision bound to the entire immutable order payload."""
    order: OrderRequest
    permits_entry: bool
    available_at: datetime
    valid_until: datetime
    source_id: str
    reason_code: str

    def __post_init__(self):
        if not isinstance(self.order, OrderRequest) or type(self.permits_entry) is not bool:
            raise TypeError('Supply a typed order and an explicit boolean risk decision')
        validate_timestamp(self.available_at)
        validate_timestamp(self.valid_until)
        if self.valid_until <= self.available_at:
            raise ValueError('Explicit risk permission validity required')
        identifier(self.source_id)
        identifier(self.reason_code)


@dataclass(frozen=True, slots=True)
class Position:
    security_id: str
    symbol: str
    quantity: Decimal
    average_cost: Decimal
    currency: str

    def __post_init__(self):
        for v in (self.security_id, self.symbol, self.currency):
            identifier(v)
        amount(self.quantity, signed=True)
        amount(self.average_cost, positive=True)
        if not self.quantity:
            raise ValueError('Open position cannot have zero quantity')


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """No account number field. Values are reported, not reconstructed from fills."""
    mode: TradingMode | None
    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    currency: str
    observed_at: datetime
    available_at: datetime
    valid_until: datetime
    source_id: str
    verified: bool
    positions: tuple[Position, ...] = ()

    def __post_init__(self):
        if self.mode is not None:
            enum(self.mode, TradingMode)
        for v in (self.cash, self.equity, self.buying_power):
            amount(v, signed=True)
        for v in (self.observed_at, self.available_at, self.valid_until):
            validate_timestamp(v)
        if self.available_at < self.observed_at or self.valid_until <= self.available_at:
            raise ValueError('Invalid account information window')
        if type(self.verified) is not bool or type(self.positions) is not tuple:
            raise TypeError('Explicit verification and immutable positions required')
        identifier(self.source_id)
        identifier(self.currency)
        seen = set()
        for position in self.positions:
            if not isinstance(position, Position) or position.security_id in seen or position.currency != self.currency:
                raise ValueError('Invalid, duplicate or incompatible account positions')
            seen.add(position.security_id)


@dataclass(frozen=True, slots=True)
class OrderAcknowledgement:
    client_order_id: str
    broker_order_id: str | None
    state: OrderState
    timestamp: datetime
    accepted: bool
    reasons: tuple[BrokerReason, ...] = ()
    error_code: int | None = None

    def __post_init__(self):
        identifier(self.client_order_id)
        if self.broker_order_id is not None:
            identifier(self.broker_order_id)
        enum(self.state, OrderState)
        validate_timestamp(self.timestamp)
        if type(self.accepted) is not bool or type(self.reasons) is not tuple:
            raise TypeError('Explicit acknowledgement and immutable reasons required')
        for reason in self.reasons:
            enum(reason, BrokerReason)
        if self.error_code is not None and type(self.error_code) is not int:
            raise TypeError('Numeric error code required')


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: str
    broker_order_id: str
    security_id: str
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    currency: str
    executed_at: datetime

    def __post_init__(self):
        for v in (self.fill_id, self.broker_order_id, self.security_id, self.symbol, self.currency):
            identifier(v)
        enum(self.side, Side)
        amount(self.quantity, positive=True)
        amount(self.price, positive=True)
        validate_timestamp(self.executed_at)


@dataclass(frozen=True, slots=True)
class Commission:
    report_id: str
    fill_id: str
    amount: Decimal
    currency: str
    timestamp: datetime

    def __post_init__(self):
        identifier(self.report_id)
        identifier(self.fill_id)
        identifier(self.currency)
        amount(self.amount, signed=True)
        validate_timestamp(self.timestamp)


@dataclass(frozen=True, slots=True)
class OrderStatus:
    broker_order_id: str
    request: OrderRequest
    state: OrderState
    filled_quantity: Decimal
    average_fill_price: Fraction | None
    updated_at: datetime

    def __post_init__(self):
        identifier(self.broker_order_id)
        if not isinstance(self.request, OrderRequest):
            raise TypeError('Typed original order required')
        enum(self.state, OrderState)
        amount(self.filled_quantity)
        if self.filled_quantity > self.request.quantity:
            raise ValueError('Filled quantity cannot exceed the order quantity')
        if ((self.average_fill_price is None) != (self.filled_quantity == 0)
                or (self.average_fill_price is not None and
                    (type(self.average_fill_price) is not Fraction or self.average_fill_price <= 0))):
            raise ValueError('Exact fill average required for actual filled quantity')
        validate_timestamp(self.updated_at)
        if self.updated_at < self.request.created_at:
            raise ValueError('Order status cannot precede its request')

    @property
    def remaining_quantity(self) -> Decimal:
        return subtract(self.request.quantity, self.filled_quantity)


@dataclass(frozen=True, slots=True)
class BrokerEvent:
    event_id: str
    kind: EventKind
    occurred_at: datetime
    received_at: datetime
    broker_order_id: str | None = None
    fill: Fill | None = None
    commission: Commission | None = None
    error_code: int | None = None
    account: AccountSnapshot | None = None

    def __post_init__(self):
        identifier(self.event_id)
        enum(self.kind, EventKind)
        validate_timestamp(self.occurred_at)
        validate_timestamp(self.received_at)
        if self.received_at < self.occurred_at:
            raise ValueError('Broker event cannot arrive before it occurred')
        if self.broker_order_id is not None:
            identifier(self.broker_order_id)
        if self.fill is not None and not isinstance(self.fill, Fill):
            raise TypeError('Typed fill required')
        if self.commission is not None and not isinstance(self.commission, Commission):
            raise TypeError('Typed commission required')
        if self.account is not None and not isinstance(self.account, AccountSnapshot):
            raise TypeError('Typed account snapshot required')
        if self.error_code is not None and type(self.error_code) is not int:
            raise TypeError('Error information is a numeric code, never raw credential-bearing text')


AuditScalar = str | int | bool | Decimal | Fraction | datetime | None


@dataclass(frozen=True, slots=True)
class BrokerAuditRecord:
    timestamp: datetime
    action: str
    mode: TradingMode
    account_mode: TradingMode | None
    request: OrderRequest | None
    broker_order_id: str | None
    state: OrderState | None
    reasons: tuple[BrokerReason, ...]
    details: tuple[tuple[str, AuditScalar], ...] = ()
    error_code: int | None = None

    def __post_init__(self):
        validate_timestamp(self.timestamp)
        identifier(self.action)
        enum(self.mode, TradingMode)
        if self.account_mode is not None:
            enum(self.account_mode, TradingMode)
        if self.request is not None and not isinstance(self.request, OrderRequest):
            raise TypeError('Typed audit request required')
        if self.state is not None:
            enum(self.state, OrderState)
        if self.broker_order_id is not None:
            identifier(self.broker_order_id)
        if type(self.reasons) is not tuple or type(self.details) is not tuple:
            raise TypeError('Audit evidence must be immutable')
        for reason in self.reasons:
            enum(reason, BrokerReason)
        if self.error_code is not None and type(self.error_code) is not int:
            raise TypeError('Numeric error code required')
        keys = set()
        for detail in self.details:
            if type(detail) is not tuple or len(detail) != 2:
                raise TypeError('Audit detail entries require immutable two-element tuples')
            key, value = detail
            identifier(key)
            if key in keys or type(value) not in (str, int, bool, Decimal, Fraction, datetime, type(None)):
                raise ValueError('Audit details must be unique exact supported scalars')
            keys.add(key)
            if isinstance(value, Decimal):
                amount(value, signed=True)
            if isinstance(value, datetime):
                validate_timestamp(value)


@dataclass(frozen=True, slots=True)
class FlattenPlan:
    timestamp: datetime
    account_snapshot: AccountSnapshot
    requests: tuple[OrderRequest, ...]
    submitted: bool = False
