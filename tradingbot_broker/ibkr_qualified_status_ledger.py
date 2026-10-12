"""Private pure PRE6 values and prospective consumer coverage, never publication.

Synthetic generation/sequence values do not authenticate transport provenance.
No production consumer calls this module. A compatibility view never preserves
the entire qualified source, and is never proof of source-aware durability.
"""

from dataclasses import dataclass, fields, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from functools import wraps

from . import ibkr_order_status_evidence as source
from .ibkr_paper_transport import (
    _OpenOrderEvidence, _OrderStatusEvidence, _ExecutionEvidence, _CommissionEvidence,
    _EvidenceBatch, _EVIDENCE_CAPACITY, _callback_id, _callback_text, _callback_amount,
    _valid_order_id, _STATUSES,
)
from .readonly_models import ReadOnlyError


__all__ = ()
_ERROR = 'INVALID_QUALIFIED_STATUS_LEDGER_CONTRACT'


def _require(condition):
    if not condition:
        raise ReadOnlyError(_ERROR)


def _pure(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:
            raise ReadOnlyError(_ERROR) from None
    return checked


def _integer(value, minimum=0):
    _require(type(value) is int and value >= minimum)


def _validate_qualified(event):
    _require(type(event) is _QualifiedOrderStatusEvidence)
    _integer(event.generation, 1)
    _integer(event.sequence, 1)
    _require(type(event.observation) is source._OrderStatusObservation)
    source._OrderStatusObservation.__post_init__(event.observation)


def _copy_observation(observation):
    source._OrderStatusObservation.__post_init__(observation)
    presence = source._OrderStatusPresence(
        *(getattr(observation.presence, name) for name, _, _, _ in source._SOURCE_FIELDS),
        None if observation.presence.wire_supplied is None else
        frozenset(name.encode('ascii').decode('ascii')
                  for name in observation.presence.wire_supplied))
    values = []
    for name, _, _, _ in source._SOURCE_FIELDS:
        value = getattr(observation, name)
        if type(value) is Decimal:
            value = Decimal(value.as_tuple())
        elif type(value) is str:
            value = value.encode('ascii').decode('ascii')
        values.append(value)
    return source._OrderStatusObservation(observation.protocol, *values, presence)


class _Opaque:
    __slots__ = ()

    def __repr__(self):
        return '<PrivateQualifiedStatusLedgerValue>'


@dataclass(frozen=True, slots=True, repr=False)
class _QualifiedOrderStatusEvidence(_Opaque):
    generation: int
    sequence: int
    observation: source._OrderStatusObservation

    @_pure
    def __post_init__(self):
        _validate_qualified(self)
        object.__setattr__(self, 'observation', _copy_observation(self.observation))


class _LedgerConsumer(StrEnum):
    TRANSPORT = 'TRANSPORT'
    DISPATCH = 'DISPATCH'
    RECONCILIATION = 'RECONCILIATION'
    DURABLE_PERSISTENCE = 'DURABLE_PERSISTENCE'


class _ConsumerSupport(StrEnum):
    SUPPORTED_DIRECT = 'SUPPORTED_DIRECT'
    SUPPORTED_VIA_LOSSLESS_PROJECTION = 'SUPPORTED_VIA_LOSSLESS_PROJECTION'
    UNSUPPORTED_QUALIFIED_STATUS = 'UNSUPPORTED_QUALIFIED_STATUS'
    UNSUPPORTED_EXISTING_EVIDENCE = 'UNSUPPORTED_EXISTING_EVIDENCE'


class _CoverageState(StrEnum):
    COMPLETE = 'COMPLETE'
    UNSUPPORTED = 'UNSUPPORTED'
    GAP = 'GAP'
    INCOMPLETE = 'INCOMPLETE'
    GENERATION_MISMATCH = 'GENERATION_MISMATCH'


class _ReservationCollision(StrEnum):
    COLLISION = 'COLLISION'
    UNRESOLVED = 'UNRESOLVED'
    NO_COLLISION_OBSERVED = 'NO_COLLISION_OBSERVED'


@dataclass(frozen=True, slots=True, repr=False)
class _CoverageResult(_Opaque):
    requested_cursor: int
    supported_through_sequence: int
    target_sequence: int
    first_unsupported_sequence: int | None
    first_missing_sequence: int | None
    state: _CoverageState

    @_pure
    def __post_init__(self):
        for value in (self.requested_cursor, self.supported_through_sequence, self.target_sequence):
            _integer(value)
        _require(self.requested_cursor <= self.supported_through_sequence <= self.target_sequence
                 and type(self.state) is _CoverageState)
        for value in (self.first_unsupported_sequence, self.first_missing_sequence):
            if value is not None:
                _integer(value, 1)
                _require(value == self.supported_through_sequence + 1
                         and value <= self.target_sequence)
        if self.state is _CoverageState.COMPLETE:
            _require(self.supported_through_sequence == self.target_sequence
                     and self.first_unsupported_sequence is self.first_missing_sequence is None)
        else:
            _require(self.supported_through_sequence < self.target_sequence)
            unsupported = self.state in (_CoverageState.UNSUPPORTED, _CoverageState.GENERATION_MISMATCH)
            _require((self.first_unsupported_sequence is not None) is unsupported
                     and (self.first_missing_sequence is not None) is (not unsupported))

    @property
    def complete(self):
        return self.state is _CoverageState.COMPLETE


_OLD_TYPES = (_OpenOrderEvidence, _OrderStatusEvidence, _ExecutionEvidence, _CommissionEvidence)
_EVENT_TYPES = _OLD_TYPES + (_QualifiedOrderStatusEvidence,)
_REQUIRED_STATUS_FIELDS = ('order_id', 'client_id', 'status', 'filled', 'remaining',
                           'average_fill_price', 'last_fill_price', 'parent_id')
_SUPPORTED = (_ConsumerSupport.SUPPORTED_DIRECT, _ConsumerSupport.SUPPORTED_VIA_LOSSLESS_PROJECTION)


def _text(value, *, optional=False, reference=False):
    # Producer-normalized optional empty strings are None, not retained empties.
    _require(value is None and optional or type(value) is str and value != '')
    _callback_text(value, optional=optional, reference=reference)


@_pure
def _validate_event(event):
    """Exact sanitized models only; no raw object getters or normalization."""
    _require(type(event) in _EVENT_TYPES)
    if type(event) is _QualifiedOrderStatusEvidence:
        _validate_qualified(event)
        return
    _integer(event.generation, 1)
    _integer(event.sequence, 1)
    if type(event) is _CommissionEvidence:
        _CommissionEvidence.__post_init__(event)
        return
    _callback_id(event.order_id)
    _callback_id(event.client_id, optional=type(event) is not _OrderStatusEvidence)
    if type(event) is _OrderStatusEvidence:
        _callback_id(event.parent_id)
        _require(type(event.status) is str and event.status in _STATUSES | {'UNKNOWN'})
        for value in (event.filled, event.remaining, event.average_price, event.last_price):
            _callback_amount(value)
        return
    _callback_id(event.con_id, contract=True)
    _text(event.order_ref, optional=True, reference=True)
    _text(event.side)
    if type(event) is _ExecutionEvidence:
        _text(event.execution_id, reference=True)
        _callback_amount(event.shares, positive=True)
        _callback_amount(event.price)
        return
    for value in (event.security_type, event.order_type, event.tif):
        _text(value)
    for value in (event.exchange, event.symbol, event.currency):
        _text(value, optional=True)
    _callback_amount(event.quantity, positive=True)
    _require(type(event.status) is str and event.status in _STATUSES | {'UNKNOWN'})


def _canonical(value):
    kind = type(value)
    if kind in (source._OrderStatusObservation, source._OrderStatusPresence):
        return kind.__name__, tuple((f.name, _canonical(getattr(value, f.name))) for f in fields(kind))
    if kind is Decimal:
        parts = value.as_tuple()
        return 'Decimal', (parts.sign, tuple(parts.digits), parts.exponent)
    if kind in (source._OrderStatusProtocol, source._OrderStatusSourceQuality):
        return kind.__name__, value.value
    if kind is frozenset:
        return 'frozenset', tuple(sorted(value))
    if kind is datetime:
        return 'datetime', (value.isoformat(), value.fold)
    _require(kind in (str, int, type(None)))
    return kind.__name__, value


@_pure
def _source_identity(event):
    """Sequence-free content identity, with representation rather than numeric equality."""
    _validate_event(event)
    return type(event).__name__, tuple((f.name, _canonical(getattr(event, f.name)))
                                       for f in fields(type(event)) if f.name != 'sequence')


@_pure
def _duplicate_compressible(left, right):
    return _source_identity(left) == _source_identity(right)


@_pure
def _legacy_projection(event):
    """Exact required-fact VIEW only; the qualified source must remain retained."""
    _validate_qualified(event)
    observation = event.observation
    known = (source._OrderStatusSourceQuality.SUPPLIED,
             source._OrderStatusSourceQuality.LEGACY_DECODED)
    if any(getattr(observation, name) is None
           or getattr(observation.presence, name) not in known for name in _REQUIRED_STATUS_FIELDS):
        return None
    if observation.status not in _STATUSES:
        return None  # A normalized UNKNOWN is not a known legacy-required lifecycle fact.
    observation = _copy_observation(observation)
    projection = _OrderStatusEvidence(event.generation, event.sequence,
        observation.order_id, observation.client_id, observation.status,
        observation.filled, observation.remaining, observation.average_fill_price,
        observation.last_fill_price, observation.parent_id)
    _validate_event(projection)
    return projection


@_pure
def _consumer_support(event, consumer):
    """Prospective structural capability, never acknowledgement or authorization."""
    _validate_event(event)
    _require(type(consumer) is _LedgerConsumer)
    if type(event) is _QualifiedOrderStatusEvidence:
        if consumer is _LedgerConsumer.TRANSPORT:
            return _ConsumerSupport.SUPPORTED_DIRECT
        if consumer is _LedgerConsumer.DURABLE_PERSISTENCE:
            # Even a concrete-value projection lacks the retained source metadata.
            return _ConsumerSupport.UNSUPPORTED_QUALIFIED_STATUS
        return (_ConsumerSupport.SUPPORTED_VIA_LOSSLESS_PROJECTION
                if _legacy_projection(event) is not None
                else _ConsumerSupport.UNSUPPORTED_QUALIFIED_STATUS)
    if type(event) is _CommissionEvidence and consumer is _LedgerConsumer.DISPATCH:
        return _ConsumerSupport.UNSUPPORTED_EXISTING_EVIDENCE
    return _ConsumerSupport.SUPPORTED_DIRECT


@_pure
def _known_order_id(event):
    _validate_event(event)
    if type(event) is _CommissionEvidence:
        return None  # Order-independent evidence, not an unknown status ID.
    if type(event) is _QualifiedOrderStatusEvidence:
        observation = event.observation
        if observation.presence.order_id not in (source._OrderStatusSourceQuality.SUPPLIED,
                                                 source._OrderStatusSourceQuality.LEGACY_DECODED):
            return None
        return observation.order_id
    return event.order_id


def _page(events):
    _require(type(events) is tuple and len(events) <= _EVIDENCE_CAPACITY)
    for event in events:
        _validate_event(event)


@_pure
def _reservation_collision(events, *, generation, order_id):
    """Bounded observations only; NO_COLLISION_OBSERVED is not a reservation proof."""
    _page(events)
    _integer(generation, 1)
    _require(_valid_order_id(order_id))
    unresolved = False
    collision = False
    for event in events:
        if event.generation != generation or type(event) is _CommissionEvidence:
            continue
        known = _known_order_id(event)
        unresolved |= known is None
        collision |= known == order_id
    return (_ReservationCollision.COLLISION if collision else
            _ReservationCollision.UNRESOLVED if unresolved else
            _ReservationCollision.NO_COLLISION_OBSERVED)


@_pure
def _expand_runs(events, run_ends, *, cursor, through_sequence):
    """Pure prospective paging: at most 128 retained runs and 128 returned occurrences.

    Retained starts must cover a prefix starting at one. A watermark beyond the
    represented prefix remains informational; it must never fill a missing run.
    """
    _page(events)
    _require(type(run_ends) is tuple and len(run_ends) == len(events))
    _integer(cursor)
    _integer(through_sequence)
    _require(cursor <= through_sequence)
    expected = 1
    for event, end in zip(events, run_ends):
        _integer(end, 1)
        _require(event.sequence == expected and event.sequence <= end <= through_sequence)
        expected = end + 1
    expanded = []
    for event, end in zip(events, run_ends):
        start = max(event.sequence, cursor + 1)
        for sequence in range(start, min(end + 1, start + _EVIDENCE_CAPACITY - len(expanded))):
            expanded.append(replace(event, sequence=sequence))
        if len(expanded) == _EVIDENCE_CAPACITY:
            break
    return _EvidenceBatch(through_sequence, tuple(expanded))


@_pure
def _coverage(events, *, consumer, generation, cursor, through_sequence, target_sequence=None):
    """Highest prospective supported prefix only. Never fetch, acknowledge or mutate."""
    _page(events)
    _require(type(consumer) is _LedgerConsumer)
    _integer(generation, 1)
    _integer(cursor)
    _integer(through_sequence)
    target = through_sequence if target_sequence is None else target_sequence
    _integer(target)
    _require(cursor <= target <= through_sequence)
    previous = cursor
    for event in events:
        _require(previous < event.sequence <= through_sequence)
        previous = event.sequence
    supported = cursor
    for event in events:
        if supported == target:
            break
        expected = supported + 1
        if event.sequence != expected:
            return _CoverageResult(cursor, supported, target, None, expected, _CoverageState.GAP)
        if event.generation != generation:
            return _CoverageResult(cursor, supported, target, expected, None,
                                   _CoverageState.GENERATION_MISMATCH)
        if _consumer_support(event, consumer) not in _SUPPORTED:
            return _CoverageResult(cursor, supported, target, expected, None, _CoverageState.UNSUPPORTED)
        supported = expected
    if supported == target:
        return _CoverageResult(cursor, supported, target, None, None, _CoverageState.COMPLETE)
    return _CoverageResult(cursor, supported, target, None, supported + 1, _CoverageState.INCOMPLETE)
