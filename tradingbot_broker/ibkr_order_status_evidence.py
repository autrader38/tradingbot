"""Private pure source values for the inspected ibapi 10.50.2 orderStatus contract.

These adapters neither capture callbacks nor authenticate source ownership.
There is no generation, sequence, collection, currentness or management authority.
"""

from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from enum import StrEnum
from math import isfinite
import re

from .readonly_models import ReadOnlyError


_ERROR = 'INVALID_IBKR_ORDER_STATUS_EVIDENCE'
_UNSET_DECIMAL = Decimal('170141183460469231731687303715884105727')
_UNSET_DOUBLE = 1.7976931348623157e308
_UNSET_INTEGER = 2147483647
_UNSET_LONG = 9223372036854775807
_STATUSES = frozenset(('PendingSubmit', 'PreSubmitted', 'Submitted', 'Filled',
                      'Cancelled', 'ApiCancelled', 'Inactive', 'Expired', 'UNKNOWN'))
# Model field, exact SDK property, protobuf field number, protobuf scalar type.
# Types are the standard FieldDescriptor TYPE_INT32/STRING/DOUBLE/INT64 numbers.
_SOURCE_FIELDS = (
    ('order_id', 'orderId', 1, 5), ('status', 'status', 2, 9),
    ('filled', 'filled', 3, 9), ('remaining', 'remaining', 4, 9),
    ('average_fill_price', 'avgFillPrice', 5, 1), ('perm_id', 'permId', 6, 3),
    ('parent_id', 'parentId', 7, 5), ('last_fill_price', 'lastFillPrice', 8, 1),
    ('client_id', 'clientId', 9, 5),
)
_NAMES = frozenset(name for name, _, _, _ in _SOURCE_FIELDS)
_IDS = frozenset(('order_id', 'client_id', 'perm_id', 'parent_id'))
_QUANTITIES = frozenset(('filled', 'remaining'))
_PRICES = frozenset(('average_fill_price', 'last_fill_price'))
_NUMERIC_TEXT = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')


class _OrderStatusProtocol(StrEnum):
    LEGACY = 'LEGACY'
    PROTOBUF = 'PROTOBUF'


class _OrderStatusSourceQuality(StrEnum):
    SUPPLIED = 'SUPPLIED'
    LEGACY_DECODED = 'LEGACY_DECODED'
    LEGACY_AMBIGUOUS = 'LEGACY_AMBIGUOUS'
    UNAVAILABLE = 'UNAVAILABLE'


def _require(condition):
    if not condition:
        raise ReadOnlyError(_ERROR)


def _decimal_value(value):
    _require(type(value) is Decimal and value.is_finite() and value >= 0)
    parts = value.as_tuple()
    _require(len(parts.digits) <= 64 and -64 <= parts.exponent <= 64)
    return Decimal(parts)  # Exact independent value; no arithmetic or normalization.


def _quantity(value):
    _require(type(value) is Decimal and value.is_finite())
    if value == _UNSET_DECIMAL:
        return None
    return _decimal_value(value)


def _price(value):
    _require(type(value) is float and isfinite(value) and value >= 0)
    if value == _UNSET_DOUBLE:
        return None
    return _decimal_value(Decimal(str(value)))


def _id(name, value):
    _require(type(value) is int and 0 <= value <= (_UNSET_LONG if name == 'perm_id'
                                                else _UNSET_INTEGER))
    return value


def _status(value):
    _require(type(value) is str and len(value) <= 100 and value.isascii()
             and all(32 <= ord(c) <= 126 for c in value))
    return value.encode('ascii').decode('ascii') if value in _STATUSES else 'UNKNOWN'


class _PrivateOrderStatusValue:
    __slots__ = ()

    def __repr__(self):
        return '<PrivateOrderStatusSourceEvidence>'


@dataclass(frozen=True, slots=True, repr=False)
class _OrderStatusPresence(_PrivateOrderStatusValue):
    order_id: _OrderStatusSourceQuality
    status: _OrderStatusSourceQuality
    filled: _OrderStatusSourceQuality
    remaining: _OrderStatusSourceQuality
    average_fill_price: _OrderStatusSourceQuality
    perm_id: _OrderStatusSourceQuality
    parent_id: _OrderStatusSourceQuality
    last_fill_price: _OrderStatusSourceQuality
    client_id: _OrderStatusSourceQuality
    # None: legacy wire presence was not retained by the normalized callback.
    # A protobuf field may be supplied yet unusable (a reserved numeric sentinel).
    wire_supplied: frozenset[str] | None

    def __post_init__(self):
        for name in _NAMES:
            _require(type(getattr(self, name)) is _OrderStatusSourceQuality)
        _require(self.wire_supplied is None or type(self.wire_supplied) is frozenset
                 and all(type(name) is str and name in _NAMES for name in self.wire_supplied))


@dataclass(frozen=True, slots=True, repr=False)
class _OrderStatusObservation(_PrivateOrderStatusValue):
    protocol: _OrderStatusProtocol
    order_id: int | None
    status: str | None
    filled: Decimal | None
    remaining: Decimal | None
    average_fill_price: Decimal | None
    perm_id: int | None
    parent_id: int | None
    last_fill_price: Decimal | None
    client_id: int | None
    presence: _OrderStatusPresence

    def __post_init__(self):
        _require(type(self.protocol) is _OrderStatusProtocol
                 and type(self.presence) is _OrderStatusPresence)
        _OrderStatusPresence.__post_init__(self.presence)
        legacy = self.protocol is _OrderStatusProtocol.LEGACY
        _require((self.presence.wire_supplied is None) is legacy)
        for name in _NAMES:
            value, quality = getattr(self, name), getattr(self.presence, name)
            if value is None:
                _require(quality is _OrderStatusSourceQuality.UNAVAILABLE)
                if legacy:
                    _require(name in _QUANTITIES | _PRICES)
                elif name in _IDS or name == 'status':
                    _require(name not in self.presence.wire_supplied)
                continue
            if name in _IDS:
                _id(name, value)
            elif name == 'status':
                _require(type(value) is str and value in _STATUSES)
            else:
                _decimal_value(value)
                if name in _QUANTITIES:
                    _require(value != _UNSET_DECIMAL)
            if legacy:
                expected = (_OrderStatusSourceQuality.LEGACY_AMBIGUOUS
                    if name in _IDS | _PRICES and value == 0
                    else _OrderStatusSourceQuality.LEGACY_DECODED)
                _require(quality is expected)
            else:
                _require(quality is _OrderStatusSourceQuality.SUPPLIED
                         and name in self.presence.wire_supplied)


def _observation(protocol, values, supplied=None):
    qualities = []
    for (name, _, _, _), value in zip(_SOURCE_FIELDS, values):
        if value is None:
            quality = _OrderStatusSourceQuality.UNAVAILABLE
        elif protocol is _OrderStatusProtocol.PROTOBUF:
            quality = _OrderStatusSourceQuality.SUPPLIED
        elif name in _IDS | _PRICES and value == 0:
            quality = _OrderStatusSourceQuality.LEGACY_AMBIGUOUS
        else:
            quality = _OrderStatusSourceQuality.LEGACY_DECODED
        qualities.append(quality)
    presence = _OrderStatusPresence(*qualities, supplied)
    return _OrderStatusObservation(protocol, *values, presence)


def _adapt_legacy_order_status(order_id, status, filled, remaining, average_fill_price,
                               perm_id, parent_id, last_fill_price, client_id):
    """Copy the nine relevant normalized arguments; no raw-token presence claim."""
    try:
        values = (_id('order_id', order_id), _status(status), _quantity(filled),
            _quantity(remaining), _price(average_fill_price), _id('perm_id', perm_id),
            _id('parent_id', parent_id), _price(last_fill_price), _id('client_id', client_id))
        return _observation(_OrderStatusProtocol.LEGACY, values)
    except Exception:
        raise ReadOnlyError(_ERROR) from None


def _schema(raw):
    descriptor = raw.DESCRIPTOR
    _require(type(descriptor.full_name) is str and descriptor.full_name == 'protobuf.OrderStatus'
             and type(descriptor.file.name) is str and descriptor.file.name == 'OrderStatus.proto')
    for _, sdk_name, number, scalar_type in _SOURCE_FIELDS:
        field = descriptor.fields_by_name[sdk_name]
        _require(type(field.number) is int and field.number == number
                 and type(field.type) is int and field.type == scalar_type
                 and field.has_presence is True and field.containing_oneof is not None
                 and type(field.containing_oneof.name) is str
                 and field.containing_oneof.name == '_' + sdk_name)


def _quantity_text(value):
    _require(type(value) is str and 0 < len(value) <= 160
             and _NUMERIC_TEXT.fullmatch(value) is not None)
    # Invalid text must not change the caller's Decimal flags/traps either.
    with localcontext(Context(prec=128)):
        return Decimal(value)


def _decoded_match(name, value, expected):
    if name in _IDS:
        _require(type(value) is int and value == expected)
    elif name == 'status':
        _require(type(value) is str and value == expected)
    elif name in _QUANTITIES:
        _require(type(value) is Decimal and value.is_finite() and value == expected)
    else:
        _require(type(value) is float and isfinite(value) and value.hex() == expected.hex())


def _adapt_protobuf_order_status(raw, *, normalized=None):
    """Copy qualified raw presence; optional SDK argument tuple checks one-event agreement.

    normalized is the nine relevant SDK arguments in orderStatus order, not
    a second observation or ownership receipt. No raw/normalized reference survives.
    """
    try:
        _schema(raw)
        _require(normalized is None or type(normalized) is tuple and len(normalized) == 9)
        values, supplied = [], set()
        for index, (name, sdk_name, _, _) in enumerate(_SOURCE_FIELDS):
            present = raw.HasField(sdk_name)
            _require(type(present) is bool)
            if present:
                supplied.add(name)
                source = getattr(raw, sdk_name)
                if name in _IDS:
                    value = _id(name, source)
                    expected = source
                elif name == 'status':
                    value = _status(source)
                    expected = source  # Compare before UNKNOWN normalization.
                elif name in _QUANTITIES:
                    expected = _quantity_text(source)
                    value = _quantity(expected)
                else:
                    value = _price(source)
                    expected = source
            else:
                value = None  # Do not read a raw getter's scalar default.
                expected = ('' if name == 'status' else _UNSET_DECIMAL if name in _QUANTITIES
                    else _UNSET_DOUBLE if name in _PRICES else _UNSET_LONG if name == 'perm_id'
                    else _UNSET_INTEGER)
            if normalized is not None:
                _decoded_match(name, normalized[index], expected)
            values.append(value)
        return _observation(_OrderStatusProtocol.PROTOBUF, values, frozenset(supplied))
    except Exception:
        raise ReadOnlyError(_ERROR) from None
