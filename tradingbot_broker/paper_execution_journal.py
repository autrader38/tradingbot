"""Private journal contract and pure replay; no storage or execution authority.

Only a complete, explicitly supplied journal is replayed. V1 retains its original
producer-certified progress. V2 adds historical occurrence coverage,
not correction, migration, retirement or reconciliation-clear APIs.
"""

from dataclasses import dataclass, field, fields
from datetime import date, datetime, timezone
from decimal import (Context, Decimal, DivisionByZero, Inexact, InvalidOperation,
                     Overflow, Rounded, localcontext)
from enum import StrEnum
from hashlib import sha256 as _sha256
import re
from zoneinfo import ZoneInfo

from tradingbot_backtest.market import validate_timestamp
from .models import identifier
from .readonly_models import AccountMode, ReadOnlyError


_SCHEMA_VERSION = 1
_FORMAT_ID = 'paper-execution-journal-v1'
_FORMAT_ID_V2 = 'paper-execution-journal-v2'
# Copied from the reviewed C1 contract; importing the transport would add coupling.
_MIN_ORDER_ID = 0
_MAX_ORDER_ID = 2_147_483_647
_ERROR = 'INVALID_PAPER_EXECUTION_JOURNAL'


def _require(condition):
    if not condition:
        raise ReadOnlyError(_ERROR)


def _integer(value, minimum=0, maximum=None):
    _require(type(value) is int and value >= minimum
             and (maximum is None or value <= maximum))


def _identifier(value):
    _require(type(value) is str)
    try:
        identifier(value)
    except Exception:
        raise ReadOnlyError(_ERROR) from None


def _opaque_id(value, prefix):
    _require(type(value) is str and re.fullmatch(prefix + r'-[0-9a-f]{32}', value) is not None)


def _amount(value, *, positive=False, signed=False):
    _require(type(value) is Decimal and value.is_finite())
    parts = value.as_tuple()
    _require(len(parts.digits) <= 64 and -64 <= parts.exponent <= 64)
    _require((signed or value >= 0) and (not positive or value > 0))


def _derived_amount(value):
    _require(type(value) is Decimal and value.is_finite() and value >= 0)
    parts = value.as_tuple()
    _require(len(parts.digits) <= 256 and -64 <= parts.exponent <= 64)


def _timestamp(value):
    _require(type(value) is datetime)
    try:
        validate_timestamp(value)
    except Exception:
        raise ReadOnlyError(_ERROR) from None
    # Do not retain arbitrary tzinfo objects through an otherwise exact datetime.
    _require(type(value.tzinfo) in (timezone, ZoneInfo))


def _text(value, *, optional=False, empty=False):
    if optional and value is None:
        return
    _require(type(value) is str and (empty or len(value) > 0) and len(value) <= 100
             and value.isascii() and all(32 <= ord(c) <= 126 for c in value))


def _exact(value, model):
    _require(type(value) is model)
    model.__post_init__(value)  # Also reject dataclass construction bypasses.


def _sum(left, right):
    # 64 coefficient digits and exponents [-64,64] fit exactly in 256 digits.
    # Each positive execution sum is checked against a bounded order quantity.
    context = Context(prec=256, traps=[InvalidOperation, DivisionByZero, Overflow,
                                     Inexact, Rounded])
    with localcontext(context):
        return left + right


class _PrivateJournalValue:
    __slots__ = ()

    def __repr__(self):
        return '<PrivatePaperExecutionJournalValue>'


class _LifecycleStatus(StrEnum):
    PENDING_SUBMIT = 'PendingSubmit'
    PRE_SUBMITTED = 'PreSubmitted'
    SUBMITTED = 'Submitted'
    FILLED = 'Filled'
    CANCELLED = 'Cancelled'
    API_CANCELLED = 'ApiCancelled'
    INACTIVE = 'Inactive'
    EXPIRED = 'Expired'
    UNKNOWN = 'UNKNOWN'


_TERMINAL = frozenset((_LifecycleStatus.FILLED, _LifecycleStatus.CANCELLED,
    _LifecycleStatus.API_CANCELLED, _LifecycleStatus.INACTIVE, _LifecycleStatus.EXPIRED))


class _ReconciliationReason(StrEnum):
    DISPATCH_INTERRUPTED = 'DISPATCH_INTERRUPTED'
    SDK_EXCEPTION = 'SDK_EXCEPTION'
    CALLBACK_GAP = 'CALLBACK_GAP'
    IDENTITY_CONFLICT = 'IDENTITY_CONFLICT'
    EXECUTION_CONFLICT = 'EXECUTION_CONFLICT'
    SESSION_LOSS = 'SESSION_LOSS'
    TRANSPORT_POISON = 'TRANSPORT_POISON'
    ALLOCATOR_EXHAUSTION = 'ALLOCATOR_EXHAUSTION'
    UNRESOLVED_LIFECYCLE = 'UNRESOLVED_LIFECYCLE'


class _ClosureReason(StrEnum):
    LOCAL_GATE_DENIED = 'LOCAL_GATE_DENIED'
    PREFLIGHT_FAILED = 'PREFLIGHT_FAILED'


class _WitnessKind(StrEnum):
    A = 'A'
    B = 'B'
    C = 'C'
    D = 'D'


class _UnresolvedObligation(StrEnum):
    BROKER_CONTROL_RECONCILIATION = 'BROKER_CONTROL_RECONCILIATION'
    EXECUTION_PRICE_COVERAGE = 'EXECUTION_PRICE_COVERAGE'
    COMMISSION_ACCOUNTING = 'COMMISSION_ACCOUNTING'
    PROTECTIVE_MANAGEMENT = 'PROTECTIVE_MANAGEMENT'
    STRATEGY_MANAGEMENT = 'STRATEGY_MANAGEMENT'


@dataclass(frozen=True, slots=True, repr=False)
class _JournalAccountBinding(_PrivateJournalValue):
    opaque_id: str

    def __post_init__(self):
        _opaque_id(self.opaque_id, 'binding')


@dataclass(frozen=True, slots=True, repr=False)
class _JournalHeader(_PrivateJournalValue):
    schema_version: int
    journal_id: str
    account_binding: _JournalAccountBinding
    format_id: str = _FORMAT_ID
    account_mode: AccountMode = field(default=AccountMode.UNKNOWN, init=False)

    def __post_init__(self):
        _integer(self.schema_version, 1, 2)
        _opaque_id(self.journal_id, 'journal')
        _exact(self.account_binding, _JournalAccountBinding)
        _require(type(self.format_id) is str and self.format_id ==
                 (_FORMAT_ID if self.schema_version == 1 else _FORMAT_ID_V2)
                 and self.account_mode is AccountMode.UNKNOWN)


@dataclass(frozen=True, slots=True, repr=False)
class _SessionRegistered(_PrivateJournalValue):
    session_id: str
    client_id: int
    write_generation: int

    def __post_init__(self):
        _opaque_id(self.session_id, 'session')
        _integer(self.client_id, 1, _MAX_ORDER_ID)
        _integer(self.write_generation, 1)


@dataclass(frozen=True, slots=True, repr=False)
class _JournalOrderIntent(_PrivateJournalValue):
    client_order_id: str
    security_id: str
    symbol: str
    currency: str
    side: str
    quantity: Decimal
    order_type: str
    tif: str
    created_at: datetime
    expires_at: datetime
    requested_mode: str = 'PAPER'
    requested_intent: str = 'ENTRY'
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None

    def __post_init__(self):
        for value in (self.client_order_id, self.security_id, self.symbol, self.currency):
            _identifier(value)
        _require(type(self.side) is str and self.side in ('BUY', 'SELL')
                 and type(self.order_type) is str and self.order_type in ('MKT', 'LMT', 'STP')
                 and type(self.tif) is str and self.tif == 'DAY')
        _require(type(self.requested_mode) is str and self.requested_mode in ('PAPER', 'LIVE')
                 and type(self.requested_intent) is str
                 and self.requested_intent in ('ENTRY', 'EXIT', 'PROTECTIVE'))
        for value in (self.limit_price, self.stop_price):
            if value is not None:
                _amount(value, positive=True)
        _require(self.order_type == 'MKT' and self.limit_price is None and self.stop_price is None
                 or self.order_type == 'LMT' and self.limit_price is not None and self.stop_price is None
                 or self.order_type == 'STP' and self.stop_price is not None and self.limit_price is None)
        _amount(self.quantity, positive=True)
        _timestamp(self.created_at)
        _timestamp(self.expires_at)
        _require(self.expires_at > self.created_at)


@dataclass(frozen=True, slots=True, repr=False)
class _JournalContractSnapshot(_PrivateJournalValue):
    security_id: str
    con_id: int
    security_type: str
    exchange: str
    symbol: str
    currency: str
    valid_from: datetime
    valid_until: datetime
    available_at: datetime
    source_id: str
    verified: bool

    def __post_init__(self):
        for value in (self.security_id, self.symbol, self.currency, self.source_id):
            _identifier(value)
        _integer(self.con_id, 1, (1 << 64) - 1)
        _require(type(self.security_type) is str and self.security_type == 'STK'
                 and type(self.exchange) is str and self.exchange == 'SMART'
                 and type(self.verified) is bool)
        for value in (self.valid_from, self.valid_until, self.available_at):
            _timestamp(value)
        _require(self.valid_until > self.valid_from)


@dataclass(frozen=True, slots=True, repr=False)
class _StrategyWitness(_PrivateJournalValue):
    kind: _WitnessKind
    level: Decimal
    timestamp: datetime
    source_id: str

    def __post_init__(self):
        _require(type(self.kind) is _WitnessKind)
        _amount(self.level, positive=True)
        _timestamp(self.timestamp)
        _identifier(self.source_id)


@dataclass(frozen=True, slots=True, repr=False)
class _JournalStrategyContext(_PrivateJournalValue):
    strategy_version: str
    configuration_id: str
    setup_id: str
    security_id: str
    symbol: str
    session_date: date
    witnesses: tuple[_StrategyWitness, ...]
    eligible_entry_minute: datetime
    reference_entry: Decimal
    raw_reference_stop: Decimal
    tick_adjusted_reference_stop: Decimal
    raw_reference_target: Decimal
    tick_adjusted_reference_target: Decimal
    original_quantity: Decimal
    risk_budget: Decimal
    bod_equity: Decimal
    stop_tick_source: str
    target_tick_source: str

    def __post_init__(self):
        _require(type(self.strategy_version) is str and self.strategy_version == 'v1.0'
                 and type(self.session_date) is date and type(self.witnesses) is tuple
                 and len(self.witnesses) == 4)
        for value in (self.configuration_id, self.setup_id, self.security_id, self.symbol,
                      self.stop_tick_source, self.target_tick_source):
            _identifier(value)
        for value, kind in zip(self.witnesses, _WitnessKind):
            _exact(value, _StrategyWitness)
            _require(value.kind is kind)
        _timestamp(self.eligible_entry_minute)
        _require(self.eligible_entry_minute.second == 0 and self.eligible_entry_minute.microsecond == 0
                 and all(w.timestamp <= self.eligible_entry_minute for w in self.witnesses))
        for value in (self.reference_entry, self.raw_reference_stop,
                      self.tick_adjusted_reference_stop, self.raw_reference_target,
                      self.tick_adjusted_reference_target, self.original_quantity,
                      self.risk_budget, self.bod_equity):
            _amount(value, positive=True)
        _require(self.tick_adjusted_reference_stop <= self.raw_reference_stop < self.reference_entry
                 < self.raw_reference_target <= self.tick_adjusted_reference_target)
        # This validates copied reference fields, not strategy eligibility, tick
        # authority, sizing or actual-fill/partial-fill management semantics.


@dataclass(frozen=True, slots=True, repr=False)
class _JournalRiskContext(_PrivateJournalValue):
    order: _JournalOrderIntent
    permits_entry: bool
    available_at: datetime
    valid_until: datetime
    source_id: str
    current_equity: Decimal | None = None
    current_exposure: Decimal | None = None
    available_cash: Decimal | None = None

    def __post_init__(self):
        _exact(self.order, _JournalOrderIntent)
        _require(type(self.permits_entry) is bool)
        _timestamp(self.available_at)
        _timestamp(self.valid_until)
        _require(self.valid_until > self.available_at)
        _identifier(self.source_id)
        for value in (self.current_equity, self.current_exposure, self.available_cash):
            if value is not None:
                _amount(value)


@dataclass(frozen=True, slots=True, repr=False)
class _AttemptConsumed(_PrivateJournalValue):
    session_id: str
    client_order_id: str
    order: _JournalOrderIntent | None = None
    contract: _JournalContractSnapshot | None = None
    strategy: _JournalStrategyContext | None = None
    risk: _JournalRiskContext | None = None

    def __post_init__(self):
        _opaque_id(self.session_id, 'session')
        _identifier(self.client_order_id)
        if self.order is None:
            _require(self.contract is None and self.strategy is None and self.risk is None)
            return  # Retain the consumed identifier without unsafe/fabricated fields.
        _exact(self.order, _JournalOrderIntent)
        _require(self.client_order_id == self.order.client_order_id)
        if self.contract is not None:
            _exact(self.contract, _JournalContractSnapshot)
            _require((self.order.security_id, self.order.symbol, self.order.currency) ==
                     (self.contract.security_id, self.contract.symbol, self.contract.currency))
        if self.strategy is not None:
            _exact(self.strategy, _JournalStrategyContext)
            _require(self.strategy.original_quantity == self.order.quantity
                     and self.strategy.security_id == self.order.security_id
                     and self.strategy.symbol == self.order.symbol)
        if self.risk is not None:
            _exact(self.risk, _JournalRiskContext)
            _require(self.risk.order == self.order)


@dataclass(frozen=True, slots=True, repr=False)
class _NumericOrderIdReserved(_PrivateJournalValue):
    client_order_id: str
    order_id: int

    def __post_init__(self):
        _identifier(self.client_order_id)
        _integer(self.order_id, _MIN_ORDER_ID, _MAX_ORDER_ID)


@dataclass(frozen=True, slots=True, repr=False)
class _OrderIdFloorObserved(_PrivateJournalValue):
    session_id: str
    next_order_id: int

    def __post_init__(self):
        _opaque_id(self.session_id, 'session')
        _integer(self.next_order_id, _MIN_ORDER_ID, _MAX_ORDER_ID)


@dataclass(frozen=True, slots=True, repr=False)
class _BrokerOrderIdObserved(_PrivateJournalValue):
    session_id: str
    order_id: int

    def __post_init__(self):
        _opaque_id(self.session_id, 'session')
        _integer(self.order_id, _MIN_ORDER_ID, _MAX_ORDER_ID)


@dataclass(frozen=True, slots=True, repr=False)
class _AllocatorExhausted(_PrivateJournalValue):
    session_id: str

    def __post_init__(self):
        _opaque_id(self.session_id, 'session')


@dataclass(frozen=True, slots=True, repr=False)
class _InvocationMayOccur(_PrivateJournalValue):
    client_order_id: str

    def __post_init__(self):
        _identifier(self.client_order_id)


@dataclass(frozen=True, slots=True, repr=False)
class _SdkReturnedNormally(_PrivateJournalValue):
    """The entry-only named placeOrder invocation returned; not acceptance."""

    client_order_id: str

    def __post_init__(self):
        _identifier(self.client_order_id)


@dataclass(frozen=True, slots=True, repr=False)
class _AttemptClosedWithoutInvocation(_PrivateJournalValue):
    client_order_id: str
    reason: _ClosureReason

    def __post_init__(self):
        _identifier(self.client_order_id)
        _require(type(self.reason) is _ClosureReason)


@dataclass(frozen=True, slots=True, repr=False)
class _ReconciliationRequired(_PrivateJournalValue):
    reason: _ReconciliationReason
    client_order_id: str | None = None
    session_id: str | None = None

    def __post_init__(self):
        _require(type(self.reason) is _ReconciliationReason
                 and (self.client_order_id is not None or self.session_id is not None))
        if self.client_order_id is not None:
            _identifier(self.client_order_id)
        if self.session_id is not None:
            _opaque_id(self.session_id, 'session')


@dataclass(frozen=True, slots=True, repr=False)
class _CallbackSource(_PrivateJournalValue):
    session_id: str
    write_generation: int
    sequence: int

    def __post_init__(self):
        _opaque_id(self.session_id, 'session')
        _integer(self.write_generation, 1)
        _integer(self.sequence, 1)


def _callback_identity(event):
    _identifier(event.client_order_id)
    _exact(event.source, _CallbackSource)
    _integer(event.order_id, _MIN_ORDER_ID, _MAX_ORDER_ID)
    if event.client_id is not None:
        _integer(event.client_id, 0, _MAX_ORDER_ID)


@dataclass(frozen=True, slots=True, repr=False)
class _OpenOrderObserved(_PrivateJournalValue):
    client_order_id: str
    source: _CallbackSource
    order_id: int
    client_id: int | None
    order_ref: str | None
    con_id: int
    security_type: str
    exchange: str | None
    symbol: str | None
    currency: str | None
    side: str
    quantity: Decimal
    order_type: str
    tif: str
    status: _LifecycleStatus

    def __post_init__(self):
        _callback_identity(self)
        _text(self.order_ref, optional=True, empty=True)
        _integer(self.con_id, 1, (1 << 64) - 1)
        for value in (self.security_type, self.side, self.order_type, self.tif):
            _text(value)
        for value in (self.exchange, self.symbol, self.currency):
            _text(value, optional=True)
        _amount(self.quantity, positive=True)
        _require(type(self.status) is _LifecycleStatus)


@dataclass(frozen=True, slots=True, repr=False)
class _OrderStatusObserved(_PrivateJournalValue):
    client_order_id: str
    source: _CallbackSource
    order_id: int
    client_id: int
    status: _LifecycleStatus
    filled: Decimal
    remaining: Decimal
    average_price: Decimal
    last_price: Decimal
    parent_id: int

    def __post_init__(self):
        _callback_identity(self)
        _integer(self.client_id, 0, _MAX_ORDER_ID)
        _integer(self.parent_id, _MIN_ORDER_ID, _MAX_ORDER_ID)
        _require(type(self.status) is _LifecycleStatus)
        for value in (self.filled, self.remaining, self.average_price, self.last_price):
            _amount(value)


@dataclass(frozen=True, slots=True, repr=False)
class _ExecutionObserved(_PrivateJournalValue):
    client_order_id: str
    source: _CallbackSource
    order_id: int
    client_id: int | None
    order_ref: str | None
    exec_id: str
    con_id: int
    side: str
    shares: Decimal
    price: Decimal
    cumulative_quantity: Decimal | None = None
    execution_time: str | None = None

    def __post_init__(self):
        _callback_identity(self)
        _text(self.order_ref, optional=True, empty=True)
        _identifier(self.exec_id)
        _integer(self.con_id, 1, (1 << 64) - 1)
        _text(self.side)
        _amount(self.shares, positive=True)
        _amount(self.price)
        if self.cumulative_quantity is not None:
            _amount(self.cumulative_quantity, positive=True)
            _require(self.cumulative_quantity >= self.shares)
        _text(self.execution_time, optional=True)


@dataclass(frozen=True, slots=True, repr=False)
class _CommissionReceived(_PrivateJournalValue):
    client_order_id: str
    exec_id: str
    report_id: str
    amount: Decimal
    currency: str
    received_at: datetime

    def __post_init__(self):
        for value in (self.client_order_id, self.exec_id, self.report_id, self.currency):
            _identifier(value)
        _amount(self.amount, signed=True)
        _timestamp(self.received_at)


def _snapshot_order_ids(order_id, client_id, *, optional_client=False):
    _integer(order_id, _MIN_ORDER_ID, _MAX_ORDER_ID)
    if client_id is not None or not optional_client:
        _integer(client_id, 0, _MAX_ORDER_ID)


@dataclass(frozen=True, slots=True, repr=False)
class _OpenOrderCallbackSnapshot(_PrivateJournalValue):
    order_id: int
    client_id: int | None
    order_ref: str | None
    con_id: int
    security_type: str
    exchange: str | None
    symbol: str | None
    currency: str | None
    side: str
    quantity: Decimal
    order_type: str
    tif: str
    status: _LifecycleStatus

    def __post_init__(self):
        _snapshot_order_ids(self.order_id, self.client_id, optional_client=True)
        if self.order_ref is not None:
            _identifier(self.order_ref)
        _integer(self.con_id, 1, (1 << 64) - 1)
        for value in (self.security_type, self.side, self.order_type, self.tif):
            _text(value)
        for value in (self.exchange, self.symbol, self.currency):
            _text(value, optional=True)
        _amount(self.quantity, positive=True)
        _require(type(self.status) is _LifecycleStatus)


@dataclass(frozen=True, slots=True, repr=False)
class _OrderStatusCallbackSnapshot(_PrivateJournalValue):
    order_id: int
    client_id: int
    status: _LifecycleStatus
    filled: Decimal
    remaining: Decimal
    average_price: Decimal
    last_price: Decimal
    parent_id: int

    def __post_init__(self):
        _snapshot_order_ids(self.order_id, self.client_id)
        _integer(self.parent_id, _MIN_ORDER_ID, _MAX_ORDER_ID)
        _require(type(self.status) is _LifecycleStatus)
        for value in (self.filled, self.remaining, self.average_price, self.last_price):
            _amount(value)


@dataclass(frozen=True, slots=True, repr=False)
class _ExecutionCallbackSnapshot(_PrivateJournalValue):
    order_id: int
    client_id: int | None
    order_ref: str | None
    execution_id: str
    con_id: int
    side: str
    shares: Decimal
    price: Decimal

    def __post_init__(self):
        _snapshot_order_ids(self.order_id, self.client_id, optional_client=True)
        if self.order_ref is not None:
            _identifier(self.order_ref)
        _identifier(self.execution_id)
        _integer(self.con_id, 1, (1 << 64) - 1)
        _text(self.side)
        _amount(self.shares, positive=True)
        _amount(self.price)


def _commission_snapshot_report_id(exec_id, amount, currency):
    # Same versioned, typed framing as the qualified commission producer. No
    # Decimal normalize/arithmetic: economic equality ignores scale/signed zero.
    sign, digits, exponent = amount.as_tuple()
    if not any(digits):
        number = '0'
    else:
        digits = list(digits)
        while digits[-1] == 0:
            digits.pop()
            exponent += 1
        number = f'{sign}:{"".join(map(str, digits))}:{exponent}'
    encoded = b'paper-commission-report-v1\0'
    for tag, value in ((b'exec_id:str', exec_id), (b'amount:decimal', number),
                       (b'currency:str', currency)):
        data = value.encode('ascii')
        encoded += tag + b'\0' + len(data).to_bytes(4, 'big') + data
    return 'commission-' + _sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class _CommissionCallbackSnapshot(_PrivateJournalValue):
    exec_id: str
    report_id: str
    amount: Decimal
    currency: str
    received_at: datetime

    def __post_init__(self):
        _identifier(self.exec_id)
        _amount(self.amount, signed=True)
        _require(type(self.currency) is str and len(self.currency) == 3
                 and all('A' <= char <= 'Z' for char in self.currency))
        _require(type(self.report_id) is str and self.report_id ==
                 _commission_snapshot_report_id(self.exec_id, self.amount, self.currency))
        # Reject caller tzinfo before the common timestamp validator can ask it
        # for an offset. The qualified producer and codec supply fixed UTC only.
        _require(type(self.received_at) is datetime
                 and type(self.received_at.tzinfo) is timezone)
        _timestamp(self.received_at)
        # The codec reconstructs a fixed UTC timezone object. Require the exact
        # UTC metadata, not Python singleton identity, which is not serialized.
        _require(self.received_at.utcoffset() == timezone.utc.utcoffset(None)
                 and self.received_at.tzname() == 'UTC')


_ORDINARY_CALLBACK_SNAPSHOTS = (_OpenOrderCallbackSnapshot,
                              _OrderStatusCallbackSnapshot, _ExecutionCallbackSnapshot)


def _ordinary_snapshot_exact(snapshot):
    _require(type(snapshot) in _ORDINARY_CALLBACK_SNAPSHOTS)
    _exact(snapshot, type(snapshot))


@dataclass(frozen=True, slots=True, repr=False)
class _DuplicateCallbackCovered(_PrivateJournalValue):
    source: _CallbackSource
    snapshot: object
    root_record_sequence: int

    def __post_init__(self):
        _exact(self.source, _CallbackSource)
        _require(self.source.sequence > 1)
        _ordinary_snapshot_exact(self.snapshot)
        _integer(self.root_record_sequence, 1)


class _UntrackedCallbackReason(StrEnum):
    NO_TRACKED_NUMERIC_ORDER_IN_SESSION = 'NO_TRACKED_NUMERIC_ORDER_IN_SESSION'


@dataclass(frozen=True, slots=True, repr=False)
class _UntrackedCallbackObserved(_PrivateJournalValue):
    source: _CallbackSource
    snapshot: object
    reason: _UntrackedCallbackReason

    def __post_init__(self):
        _exact(self.source, _CallbackSource)
        _ordinary_snapshot_exact(self.snapshot)
        _require(type(self.reason) is _UntrackedCallbackReason)


@dataclass(frozen=True, slots=True, repr=False)
class _CommissionCallbackCovered(_PrivateJournalValue):
    source: _CallbackSource
    snapshot: _CommissionCallbackSnapshot
    commission_record_sequence: int

    def __post_init__(self):
        _exact(self.source, _CallbackSource)
        _exact(self.snapshot, _CommissionCallbackSnapshot)
        _integer(self.commission_record_sequence, 1)


@dataclass(frozen=True, slots=True, repr=False)
class _UntrackedCommissionCallbackCovered(_PrivateJournalValue):
    source: _CallbackSource
    snapshot: _CommissionCallbackSnapshot
    execution_record_sequence: int

    def __post_init__(self):
        _exact(self.source, _CallbackSource)
        _exact(self.snapshot, _CommissionCallbackSnapshot)
        _integer(self.execution_record_sequence, 1)


class _QualifiedStatusProtocol(StrEnum):
    LEGACY = 'LEGACY'
    PROTOBUF = 'PROTOBUF'


class _QualifiedStatusSourceQuality(StrEnum):
    SUPPLIED = 'SUPPLIED'
    LEGACY_DECODED = 'LEGACY_DECODED'
    LEGACY_AMBIGUOUS = 'LEGACY_AMBIGUOUS'
    UNAVAILABLE = 'UNAVAILABLE'


_QUALIFIED_STATUS_FIELDS = ('order_id', 'status', 'filled', 'remaining',
    'average_fill_price', 'perm_id', 'parent_id', 'last_fill_price', 'client_id')
_QUALIFIED_STATUS_IDS = frozenset(('order_id', 'perm_id', 'parent_id', 'client_id'))
_QUALIFIED_STATUS_QUANTITIES = frozenset(('filled', 'remaining'))
_QUALIFIED_STATUS_PRICES = frozenset(('average_fill_price', 'last_fill_price'))
_QUALIFIED_UNSET_QUANTITY = Decimal('170141183460469231731687303715884105727')


@dataclass(frozen=True, slots=True, repr=False)
class _QualifiedStatusPresence(_PrivateJournalValue):
    order_id: _QualifiedStatusSourceQuality
    status: _QualifiedStatusSourceQuality
    filled: _QualifiedStatusSourceQuality
    remaining: _QualifiedStatusSourceQuality
    average_fill_price: _QualifiedStatusSourceQuality
    perm_id: _QualifiedStatusSourceQuality
    parent_id: _QualifiedStatusSourceQuality
    last_fill_price: _QualifiedStatusSourceQuality
    client_id: _QualifiedStatusSourceQuality
    wire_supplied: tuple[str, ...] | None

    def __post_init__(self):
        for name in _QUALIFIED_STATUS_FIELDS:
            _require(type(getattr(self, name)) is _QualifiedStatusSourceQuality)
        if self.wire_supplied is not None:
            _require(type(self.wire_supplied) is tuple
                     and all(type(name) is str and name in _QUALIFIED_STATUS_FIELDS
                             for name in self.wire_supplied)
                     and self.wire_supplied == tuple(name for name in _QUALIFIED_STATUS_FIELDS
                                                    if name in self.wire_supplied))


@dataclass(frozen=True, slots=True, repr=False)
class _QualifiedStatusObservation(_PrivateJournalValue):
    protocol: _QualifiedStatusProtocol
    order_id: int | None
    status: str | None
    filled: Decimal | None
    remaining: Decimal | None
    average_fill_price: Decimal | None
    perm_id: int | None
    parent_id: int | None
    last_fill_price: Decimal | None
    client_id: int | None
    presence: _QualifiedStatusPresence

    def __post_init__(self):
        _require(type(self.protocol) is _QualifiedStatusProtocol)
        _exact(self.presence, _QualifiedStatusPresence)
        legacy = self.protocol is _QualifiedStatusProtocol.LEGACY
        _require((self.presence.wire_supplied is None) is legacy)
        quality_type = _QualifiedStatusSourceQuality
        for name in _QUALIFIED_STATUS_FIELDS:
            value, quality = getattr(self, name), getattr(self.presence, name)
            if value is None:
                _require(quality is quality_type.UNAVAILABLE)
                if legacy:
                    _require(name in _QUALIFIED_STATUS_QUANTITIES | _QUALIFIED_STATUS_PRICES)
                elif name in _QUALIFIED_STATUS_IDS or name == 'status':
                    _require(name not in self.presence.wire_supplied)
                continue
            if name in _QUALIFIED_STATUS_IDS:
                _integer(value, 0, (1 << 63) - 1 if name == 'perm_id' else _MAX_ORDER_ID)
            elif name == 'status':
                _require(type(value) is str and value in tuple(s.value for s in _LifecycleStatus))
            else:
                _amount(value)
                if name in _QUALIFIED_STATUS_QUANTITIES:
                    _require(value != _QUALIFIED_UNSET_QUANTITY)
            if legacy:
                expected = (quality_type.LEGACY_AMBIGUOUS
                    if name in _QUALIFIED_STATUS_IDS | _QUALIFIED_STATUS_PRICES and value == 0
                    else quality_type.LEGACY_DECODED)
                _require(quality is expected)
            else:
                _require(quality is quality_type.SUPPLIED
                         and name in self.presence.wire_supplied)


@dataclass(frozen=True, slots=True, repr=False)
class _QualifiedOrderStatusObserved(_PrivateJournalValue):
    source: _CallbackSource
    observation: _QualifiedStatusObservation

    def __post_init__(self):
        _exact(self.source, _CallbackSource)
        _exact(self.observation, _QualifiedStatusObservation)


@dataclass(frozen=True, slots=True, repr=False)
class _CallbackProgress(_PrivateJournalValue):
    session_id: str
    processed_sequence: int
    through_sequence: int

    def __post_init__(self):
        _opaque_id(self.session_id, 'session')
        _integer(self.processed_sequence)
        _integer(self.through_sequence)
        _require(self.processed_sequence <= self.through_sequence)


_EVENT_TYPES_V1 = (_SessionRegistered, _AttemptConsumed, _NumericOrderIdReserved,
    _OrderIdFloorObserved, _BrokerOrderIdObserved, _AllocatorExhausted,
    _InvocationMayOccur, _SdkReturnedNormally, _AttemptClosedWithoutInvocation,
    _ReconciliationRequired, _OpenOrderObserved, _OrderStatusObserved,
    _ExecutionObserved, _CommissionReceived, _CallbackProgress)
_COVERAGE_EVENT_TYPES = (_DuplicateCallbackCovered, _UntrackedCallbackObserved,
                       _CommissionCallbackCovered, _UntrackedCommissionCallbackCovered)
_EVENT_TYPES_V2 = _EVENT_TYPES_V1 + _COVERAGE_EVENT_TYPES + (_QualifiedOrderStatusObserved,)
_EVENT_TYPES = _EVENT_TYPES_V2  # Storage's closed union; record enforces version.


@dataclass(frozen=True, slots=True, repr=False)
class _JournalRecord(_PrivateJournalValue):
    sequence: int
    schema_version: int
    journal_id: str
    account_binding: _JournalAccountBinding
    payload: object

    def __post_init__(self):
        _integer(self.sequence, 1)
        _integer(self.schema_version, 1, 2)
        _opaque_id(self.journal_id, 'journal')
        _exact(self.account_binding, _JournalAccountBinding)
        allowed = _EVENT_TYPES_V1 if self.schema_version == 1 else _EVENT_TYPES_V2
        _require(type(self.payload) in allowed)
        _exact(self.payload, type(self.payload))


@dataclass(frozen=True, slots=True, repr=False)
class _AttemptReplayState(_PrivateJournalValue):
    consumed: _AttemptConsumed
    order_id: int | None
    invocation_may_have_occurred: bool
    sdk_returned_normally: bool
    closure_reason: _ClosureReason | None
    strong_open_order_identity_established: bool
    broker_observed: bool
    open_observations: tuple[_OpenOrderObserved, ...]
    status_observations: tuple[_OrderStatusObserved, ...]
    executions: tuple[_ExecutionObserved, ...]
    observed_execution_quantity: Decimal
    historical_filled_lower_bound: Decimal
    commissions: tuple[_CommissionReceived, ...]
    reconciliation_reasons: tuple[_ReconciliationReason, ...]
    sdk_outcome_uncertain: bool
    broker_control_unresolved: bool
    unresolved_obligations: tuple[_UnresolvedObligation, ...]

    def __post_init__(self):
        _exact(self.consumed, _AttemptConsumed)
        if self.order_id is not None:
            _integer(self.order_id, _MIN_ORDER_ID, _MAX_ORDER_ID)
        for value in (self.invocation_may_have_occurred, self.sdk_returned_normally,
                      self.strong_open_order_identity_established, self.broker_observed,
                      self.sdk_outcome_uncertain, self.broker_control_unresolved):
            _require(type(value) is bool)
        _require(self.closure_reason is None or type(self.closure_reason) is _ClosureReason)
        for values, model in ((self.open_observations, _OpenOrderObserved),
                (self.status_observations, _OrderStatusObserved),
                (self.executions, _ExecutionObserved), (self.commissions, _CommissionReceived)):
            _require(type(values) is tuple)
            for value in values:
                _exact(value, model)
        _derived_amount(self.observed_execution_quantity)
        _derived_amount(self.historical_filled_lower_bound)
        quantity = self.consumed.order.quantity if self.consumed.order is not None else Decimal(0)
        _require(self.observed_execution_quantity <= self.historical_filled_lower_bound <= quantity)
        for values, model in ((self.reconciliation_reasons, _ReconciliationReason),
                              (self.unresolved_obligations, _UnresolvedObligation)):
            _require(type(values) is tuple and all(type(v) is model for v in values)
                     and values == tuple(v for v in model if v in values))


@dataclass(frozen=True, slots=True, repr=False)
class _JournalReplayState(_PrivateJournalValue):
    header: _JournalHeader
    last_sequence: int
    sessions: tuple[_SessionRegistered, ...]
    consumed_client_order_ids: frozenset[str]
    reserved_order_ids: tuple[tuple[int, str], ...]
    order_id_floor: int
    allocator_exhausted: bool
    attempts: tuple[_AttemptReplayState, ...]
    callback_progress: tuple[_CallbackProgress, ...]
    observed_callback_watermarks: tuple[tuple[str, int], ...]
    reconciliation_required: bool
    reconciliation_reasons: tuple[_ReconciliationReason, ...]
    restart_quarantine_required: bool = field(default=True, init=False)

    def __post_init__(self):
        _exact(self.header, _JournalHeader)
        _integer(self.last_sequence)
        _integer(self.order_id_floor, _MIN_ORDER_ID, _MAX_ORDER_ID)
        _require(type(self.allocator_exhausted) is bool
                 and type(self.reconciliation_required) is bool
                 and self.restart_quarantine_required is True
                 and type(self.consumed_client_order_ids) is frozenset)
        for value in self.consumed_client_order_ids:
            _identifier(value)
        for values, model in ((self.sessions, _SessionRegistered),
                (self.attempts, _AttemptReplayState), (self.callback_progress, _CallbackProgress)):
            _require(type(values) is tuple)
            for value in values:
                _exact(value, model)
        _require(type(self.reserved_order_ids) is tuple
                 and type(self.observed_callback_watermarks) is tuple)
        for value in self.reserved_order_ids:
            _require(type(value) is tuple and len(value) == 2)
            _integer(value[0], _MIN_ORDER_ID, _MAX_ORDER_ID)
            _identifier(value[1])
        for value in self.observed_callback_watermarks:
            _require(type(value) is tuple and len(value) == 2)
            _opaque_id(value[0], 'session')
            _integer(value[1], 1)
        _require(type(self.reconciliation_reasons) is tuple
                 and all(type(v) is _ReconciliationReason for v in self.reconciliation_reasons)
                 and self.reconciliation_reasons == _ordered_reasons(self.reconciliation_reasons))


def _ordered_reasons(values):
    return tuple(reason for reason in _ReconciliationReason if reason in values)


def _execution_identity(event):
    # Source duplicate observations can differ in callback sequence, but every
    # other retained field, including absence and timestamp, must be identical.
    return tuple(getattr(event, f.name) for f in fields(event) if f.name != 'source')


def _ordinary_source_snapshot(event):
    """Retained old callback facts only; no SDK/transport import or attribution."""
    models = {_OpenOrderObserved: _OpenOrderCallbackSnapshot,
              _OrderStatusObserved: _OrderStatusCallbackSnapshot,
              _ExecutionObserved: _ExecutionCallbackSnapshot}
    _require(type(event) in models)
    _exact(event, type(event))
    model = models[type(event)]
    values = {}
    for item in fields(model):
        name = 'exec_id' if item.name == 'execution_id' else item.name
        value = getattr(event, name)
        values[item.name] = Decimal(value.as_tuple()) if type(value) is Decimal else value
    return model(**values)


def _callback_snapshot_identity(snapshot):
    """Closed representation identity. It describes expanded retained evidence."""
    _require(type(snapshot) in _ORDINARY_CALLBACK_SNAPSHOTS + (_CommissionCallbackSnapshot,))
    _exact(snapshot, type(snapshot))
    values = []
    for item in fields(type(snapshot)):
        value = getattr(snapshot, item.name)
        if type(value) is Decimal:
            value = (Decimal, value.as_tuple())
        elif type(value) is datetime:
            value = (datetime, value.isoformat(), value.fold)
        else:
            _require(type(value) in (str, int, type(None), _LifecycleStatus))
            value = (type(value), value)
        values.append((item.name, value))
    return type(snapshot), tuple(values)


class _V2CallbackCoverage:
    """Replay-local proof over actual records; never a producer or recovery owner."""
    __slots__ = ('sessions', 'attempts', 'executions', 'records', 'occurrences',
                 'frontiers', 'high_water', 'untracked_executions')

    def __init__(self, sessions, attempts, executions):
        self.sessions, self.attempts, self.executions = sessions, attempts, executions
        self.records, self.occurrences = {}, {}
        self.frontiers, self.high_water, self.untracked_executions = {}, {}, {}

    def _key(self, source):
        _exact(source, _CallbackSource)
        _require(source.session_id in self.sessions and source.write_generation ==
                 self.sessions[source.session_id].write_generation)
        return source.session_id, source.write_generation, source.sequence

    def _register(self, source, root=None):
        # Future exact direct-source branches can call this AFTER validating
        # their own semantics. No wildcard event acceptance is introduced here.
        key = self._key(source)
        _require(key not in self.occurrences)
        self.occurrences[key] = root
        sid, generation, _ = key
        self.high_water[sid] = max(self.high_water.get(sid, 0), source.sequence)
        frontier = self.frontiers.get(sid, 0)
        # O(actual explanations), never O(an untrusted processed integer).
        while (sid, generation, frontier + 1) in self.occurrences:
            frontier += 1
        self.frontiers[sid] = frontier

    def _backing(self, sequence):
        _integer(sequence, 1)
        _require(sequence in self.records)  # Only the already validated prefix.
        return self.records[sequence]

    def direct(self, record):
        event = record.payload
        if type(event) is _ExecutionObserved:
            _require(event.exec_id not in self.untracked_executions)
        self._register(event.source, record.sequence)

    def exceptional(self, record):
        event = record.payload
        key = self._key(event.source)
        _require(key not in self.occurrences)
        snapshot, kind = event.snapshot, type(event)
        if kind is _DuplicateCallbackCovered:
            root = self._backing(event.root_record_sequence)
            _require(type(root.payload) in (_OpenOrderObserved, _OrderStatusObserved, _ExecutionObserved)
                     and self._key(root.payload.source)[:2] == key[:2])
            preceding = key[:2] + (event.source.sequence - 1,)
            _require(preceding in self.occurrences and
                     self.occurrences[preceding] == root.sequence)
            _require(_callback_snapshot_identity(snapshot) ==
                     _callback_snapshot_identity(_ordinary_source_snapshot(root.payload)))
            self._register(event.source, root.sequence)
        elif kind is _UntrackedCallbackObserved:
            _require(not any(state['consumed'].session_id == event.source.session_id
                             and state['order_id'] == snapshot.order_id
                             for state in self.attempts.values()))
            if type(snapshot) is _ExecutionCallbackSnapshot:
                _require(snapshot.execution_id not in self.executions)
                identity = _callback_snapshot_identity(snapshot)
                prior = self.untracked_executions.get(snapshot.execution_id)
                _require(prior is None or prior == identity)
                self.untracked_executions[snapshot.execution_id] = identity
            self._register(event.source)
        elif kind is _CommissionCallbackCovered:
            backing = self._backing(event.commission_record_sequence).payload
            _require(type(backing) is _CommissionReceived)
            state = self.attempts[backing.client_order_id]
            _require(state['consumed'].session_id == event.source.session_id
                     and backing.exec_id in self.executions
                     and self.executions[backing.exec_id].client_order_id == backing.client_order_id
                     and (snapshot.exec_id, snapshot.report_id, snapshot.amount, snapshot.currency) ==
                         (backing.exec_id, backing.report_id, backing.amount, backing.currency))
            # Receipt instant/Decimal shape belong to THIS occurrence; accounting
            # duplicates use the original producer's economic equality contract.
            self._register(event.source)
        else:
            _require(kind is _UntrackedCommissionCallbackCovered)
            backing = self._backing(event.execution_record_sequence).payload
            _require(type(backing) is _UntrackedCallbackObserved
                     and type(backing.snapshot) is _ExecutionCallbackSnapshot
                     and self._key(backing.source)[:2] == key[:2]
                     and backing.snapshot.execution_id == snapshot.exec_id
                     and snapshot.exec_id not in self.executions)
            self._register(event.source)

    def progress(self, event):
        _require(event.processed_sequence <= self.frontiers.get(event.session_id, 0)
                 and event.through_sequence >= self.high_water.get(event.session_id, 0))

    def accepted(self, record):
        self.records[record.sequence] = record


def _replay(header, records):
    _exact(header, _JournalHeader)
    _require(type(records) is tuple)
    sessions, attempts, reservations = {}, {}, {}
    callbacks, watermarks, progress, executions, reports = {}, {}, {}, {}, {}
    reasons, session_reasons = set(), {}
    floor, exhausted = _MIN_ORDER_ID, False
    coverage = _V2CallbackCoverage(sessions, attempts, executions) if header.schema_version == 2 else None

    def attempt(key):
        _require(key in attempts)
        return attempts[key]

    def observe_id(order_id):
        nonlocal floor, exhausted
        floor = max(floor, min(order_id + 1, _MAX_ORDER_ID))
        if order_id == _MAX_ORDER_ID:
            exhausted = True

    for expected, record in enumerate(records, 1):
        _exact(record, _JournalRecord)
        _require(record.sequence == expected and record.schema_version == header.schema_version
                 and record.journal_id == header.journal_id
                 and record.account_binding == header.account_binding)
        event, kind = record.payload, type(record.payload)
        if kind is _SessionRegistered:
            prior = sessions.get(event.session_id)
            _require(prior is None or prior == event)
            sessions[event.session_id] = event
        elif kind is _AttemptConsumed:
            key = event.client_order_id
            _require(event.session_id in sessions and key not in attempts)
            attempts[key] = dict(consumed=event, order_id=None, invocation=False,
                returned=False, closure=None, strong=False, observed=False,
                opens=[], statuses=[], executions=[], total=Decimal(0),
                filled=Decimal(0), commissions=[],
                reasons=set(session_reasons.get(event.session_id, ())))
        elif kind in (_OrderIdFloorObserved, _BrokerOrderIdObserved, _AllocatorExhausted):
            _require(event.session_id in sessions)
            if kind is _OrderIdFloorObserved:
                floor = max(floor, event.next_order_id)  # Old observations cannot lower it.
            elif kind is _BrokerOrderIdObserved:
                observe_id(event.order_id)
            else:
                exhausted, floor = True, _MAX_ORDER_ID
                reasons.add(_ReconciliationReason.ALLOCATOR_EXHAUSTION)
        elif kind is _NumericOrderIdReserved:
            state = attempt(event.client_order_id)
            _require(not exhausted and state['order_id'] is None
                     and state['consumed'].order is not None
                     and not state['invocation'] and state['closure'] is None
                     and event.order_id not in reservations and event.order_id >= floor)
            state['order_id'] = event.order_id
            reservations[event.order_id] = event.client_order_id
            observe_id(event.order_id)
        elif kind is _InvocationMayOccur:
            state = attempt(event.client_order_id)
            consumed = state['consumed']
            _require(state['order_id'] is not None and not state['invocation']
                     and state['closure'] is None and consumed.order is not None
                     and consumed.order.requested_mode == 'PAPER'
                     and consumed.order.requested_intent == 'ENTRY'
                     and consumed.order.side == 'BUY' and consumed.order.order_type == 'MKT'
                     and consumed.contract is not None
                     and consumed.contract.verified and consumed.strategy is not None
                     and consumed.risk is not None and consumed.risk.permits_entry
                     and not reasons and not state['reasons'])
            state['invocation'] = True
        elif kind is _SdkReturnedNormally:
            state = attempt(event.client_order_id)
            _require(state['invocation'] and not state['returned'])
            state['returned'] = True
        elif kind is _AttemptClosedWithoutInvocation:
            state = attempt(event.client_order_id)
            _require(not state['invocation'] and state['closure'] is None)
            state['closure'] = event.reason
        elif kind is _ReconciliationRequired:
            if event.session_id is not None:
                _require(event.session_id in sessions)
            if event.client_order_id is not None:
                state = attempt(event.client_order_id)
                if event.session_id is not None:
                    _require(state['consumed'].session_id == event.session_id)
                state['reasons'].add(event.reason)
            elif event.session_id is not None:
                session_reasons.setdefault(event.session_id, set()).add(event.reason)
                for state in attempts.values():
                    if state['consumed'].session_id == event.session_id:
                        state['reasons'].add(event.reason)
            reasons.add(event.reason)
        elif kind is _CallbackProgress:
            _require(event.session_id in sessions)
            prior = progress.get(event.session_id)
            _require(prior is None or (event.processed_sequence >= prior.processed_sequence
                                      and event.through_sequence >= prior.through_sequence))
            if coverage is not None:
                coverage.progress(event)
            progress[event.session_id] = event
        elif kind is _CommissionReceived:
            state = attempt(event.client_order_id)
            _require(state['invocation'] and event.exec_id in executions
                     and executions[event.exec_id].client_order_id == event.client_order_id
                     and event.currency == state['consumed'].order.currency)
            prior = reports.get(event.exec_id)
            identity = (event.client_order_id, event.exec_id, event.report_id, event.amount, event.currency)
            _require(prior is None or prior == identity)
            if prior is None:
                reports[event.exec_id] = identity
                state['commissions'].append(event)
        elif kind in _COVERAGE_EVENT_TYPES:
            _require(coverage is not None)
            coverage.exceptional(record)
        elif kind is _QualifiedOrderStatusObserved:
            # Historical source only: no attempt, allocator, economic or
            # lifecycle interpretation. Every logical occurrence is direct.
            _require(coverage is not None)
            coverage.direct(record)
        else:
            state = attempt(event.client_order_id)
            consumed = state['consumed']
            source = event.source
            _require(state['invocation'] and source.session_id == consumed.session_id
                     and source.write_generation == sessions[source.session_id].write_generation
                     and event.order_id == state['order_id']
                     and (event.client_id is None or event.client_id == sessions[source.session_id].client_id))
            callback_key = (source.session_id, source.sequence)
            prior = callbacks.get(callback_key)
            _require(prior is None or prior == event)
            if coverage is not None:
                _require(prior is None)  # V1's identical physical repeats remain valid.
            if prior is not None:
                continue  # Exact source callback replay, not a second observation.
            callbacks[callback_key] = event
            watermarks[source.session_id] = max(watermarks.get(source.session_id, 0), source.sequence)
            observe_id(event.order_id)
            if kind is _OpenOrderObserved:
                contract, order = consumed.contract, consumed.order
                _require(event.con_id == contract.con_id and event.security_type == 'STK'
                         and event.exchange in (None, 'SMART')
                         and event.symbol in (None, order.symbol)
                         and event.currency in (None, order.currency)
                         and event.side == order.side and event.quantity == order.quantity
                         and event.order_type == order.order_type and event.tif == order.tif
                         and event.order_ref in (None, order.client_order_id))
                state['opens'].append(event)
                if event.order_ref is not None and event.status is not _LifecycleStatus.UNKNOWN:
                    state['strong'], state['observed'] = True, True
            elif kind is _OrderStatusObserved:
                quantity = consumed.order.quantity
                _require(_sum(event.filled, event.remaining) == quantity
                         and event.filled <= quantity and event.remaining <= quantity
                         and (event.status is not _LifecycleStatus.FILLED
                              or (event.filled == quantity and event.remaining == 0)))
                state['statuses'].append(event)
                if state['strong']:
                    state['filled'] = max(state['filled'], event.filled)
                if not state['strong'] and (event.status in _TERMINAL or event.filled > 0):
                    state['reasons'].add(_ReconciliationReason.UNRESOLVED_LIFECYCLE)
                    reasons.add(_ReconciliationReason.UNRESOLVED_LIFECYCLE)
            else:
                _require(kind is _ExecutionObserved and event.con_id == consumed.contract.con_id
                         and event.side == 'BOT'
                         and (event.order_ref == consumed.order.client_order_id
                              or event.order_ref is None and state['strong']
                              and event.client_id == sessions[source.session_id].client_id))
                prior = executions.get(event.exec_id)
                _require(prior is None or _execution_identity(prior) == _execution_identity(event))
                if prior is None:
                    total = _sum(state['total'], event.shares)
                    _require(total <= consumed.order.quantity)
                    if event.cumulative_quantity is not None:
                        _require(event.cumulative_quantity <= consumed.order.quantity)
                    executions[event.exec_id] = event
                    state['executions'].append(event)
                    state['total'], state['observed'] = total, True
                    if event.cumulative_quantity is not None:
                        state['filled'] = max(state['filled'], event.cumulative_quantity)
            if kind in (_OpenOrderObserved, _OrderStatusObserved) and event.status is _LifecycleStatus.UNKNOWN:
                state['reasons'].add(_ReconciliationReason.UNRESOLVED_LIFECYCLE)
                reasons.add(_ReconciliationReason.UNRESOLVED_LIFECYCLE)
            if coverage is not None:
                coverage.direct(record)
        if coverage is not None:
            coverage.accepted(record)

    result_attempts = []
    for key in sorted(attempts):
        s = attempts[key]
        invoked = s['invocation']
        obligations = set()
        if invoked or s['reasons']:
            obligations.add(_UnresolvedObligation.BROKER_CONTROL_RECONCILIATION)
        if invoked:
            obligations.update((_UnresolvedObligation.COMMISSION_ACCOUNTING,
                _UnresolvedObligation.PROTECTIVE_MANAGEMENT, _UnresolvedObligation.STRATEGY_MANAGEMENT))
            if s['total'] < s['consumed'].order.quantity:
                obligations.add(_UnresolvedObligation.EXECUTION_PRICE_COVERAGE)
        result_attempts.append(_AttemptReplayState(s['consumed'], s['order_id'], invoked,
            s['returned'], s['closure'], s['strong'], s['observed'], tuple(s['opens']),
            tuple(s['statuses']), tuple(sorted(s['executions'], key=lambda x: x.exec_id)),
            s['total'], max(s['filled'], s['total']),
            tuple(sorted(s['commissions'], key=lambda x: x.exec_id)), _ordered_reasons(s['reasons']),
            invoked and (not s['returned'] or bool(s['reasons'] & {
                _ReconciliationReason.DISPATCH_INTERRUPTED, _ReconciliationReason.SDK_EXCEPTION})),
            invoked or bool(s['reasons']),
            tuple(o for o in _UnresolvedObligation if o in obligations)))
    return _JournalReplayState(header, len(records), tuple(sessions[k] for k in sorted(sessions)),
        frozenset(attempts), tuple(sorted(reservations.items())), floor, exhausted,
        tuple(result_attempts), tuple(progress[k] for k in sorted(progress)),
        tuple(sorted(watermarks.items())), bool(reasons), _ordered_reasons(reasons))


def _replay_paper_execution_journal(header, records):
    """Replay exact immutable supplied facts; malformed history is unusable.

    No receipt, capability or caller assessment is accepted. BaseException passes
    through unchanged. Ordinary invalid input produces one sanitized fixed code.
    """
    try:
        return _replay(header, records)
    except Exception:
        raise ReadOnlyError(_ERROR) from None
