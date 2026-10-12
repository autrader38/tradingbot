"""Private per-decoder pairing only; never installed by a production transport.

No callback ledger, generation, clock, storage or management authority lives here.
The intercepted SDK dispatcher remains responsible for parsing and BadMessage.
"""

from dataclasses import dataclass
from enum import Enum, StrEnum
import importlib
import inspect
from threading import RLock, current_thread, get_ident
from types import FunctionType, MappingProxyType, MethodType

from . import ibkr_order_status_evidence as source
from .readonly_models import ReadOnlyError


_ERROR = 'INVALID_IBKR_ORDER_STATUS_PAIRING'
_INSTALLATION = '_private_order_status_pairing_boundary'
_MAX_DEPTH = 32
_MAX_INVOCATION = (1 << 63) - 1
_INSTALL_LOCK = RLock()


def _require(condition):
    if not condition:
        raise ReadOnlyError(_ERROR)


class _OpaquePairingValue:
    __slots__ = ()

    def __repr__(self):
        return '<PrivateOrderStatusPairingValue>'


@dataclass(frozen=True, slots=True, repr=False)
class _OrderStatusInvocation(_OpaquePairingValue):
    namespace: object
    invocation_number: int
    thread_ident: int
    depth: int
    parent_number: int | None

    def __post_init__(self):
        _require(type(self.namespace) is object)
        _require(type(self.invocation_number) is int
                 and 1 <= self.invocation_number <= _MAX_INVOCATION)
        _require(type(self.thread_ident) is int and 0 < self.thread_ident < 1 << 64)
        _require(type(self.depth) is int and 1 <= self.depth <= _MAX_DEPTH)
        _require((self.parent_number is None and self.depth == 1)
                 or type(self.parent_number) is int and self.depth > 1
                 and 0 < self.parent_number < self.invocation_number)


@dataclass(frozen=True, slots=True, repr=False)
class _PairedOrderStatus(_OpaquePairingValue):
    invocation: _OrderStatusInvocation
    observation: source._OrderStatusObservation

    def __post_init__(self):
        _require(type(self.invocation) is _OrderStatusInvocation
                 and type(self.observation) is source._OrderStatusObservation)
        _OrderStatusInvocation.__post_init__(self.invocation)
        source._OrderStatusObservation.__post_init__(self.observation)
        _require(self.observation.protocol is source._OrderStatusProtocol.PROTOBUF)


@dataclass(frozen=True, slots=True)
class _Name:
    name: str


@dataclass(frozen=True, slots=True)
class _Field:
    number: int
    type: int
    has_presence: bool
    containing_oneof: _Name


@dataclass(frozen=True, slots=True)
class _Schema:
    full_name: str
    file: _Name
    fields_by_name: object


# Independent immutable metadata, not a reference to an SDK descriptor. The
# ORIGINAL descriptor is checked by PRE3 before any source getters are copied.
_SCHEMA = _Schema('protobuf.OrderStatus', _Name('OrderStatus.proto'), MappingProxyType({
    sdk: _Field(number, kind, True, _Name('_' + sdk))
    for _, sdk, number, kind in source._SOURCE_FIELDS}))


@dataclass(frozen=True, slots=True, repr=False)
class _RawSnapshot(_OpaquePairingValue):
    values: tuple
    DESCRIPTOR = _SCHEMA

    def HasField(self, name):
        return next(present for sdk, present, _ in self.values if sdk == name)

    def __getattr__(self, name):
        for sdk, present, value in self.values:
            if sdk == name and present:
                return value
        raise AttributeError(_ERROR)


def _snapshot(raw):
    try:
        snapshot = _copy_snapshot(raw)
    except Exception:
        raise ReadOnlyError(_ERROR) from None
    observation = source._adapt_protobuf_order_status(snapshot)
    return snapshot, observation


def _copy_snapshot(raw):
    source._schema(raw)
    values = []
    for _, sdk, _, kind in source._SOURCE_FIELDS:
        present = raw.HasField(sdk)
        _require(type(present) is bool)
        value = None
        if present:
            value = getattr(raw, sdk)
            if kind == 9:
                _require(type(value) is str and len(value) <= (100 if sdk == 'status' else 160))
                # PRE3 checks grammar/ASCII/semantics on this independent copy.
                value = value.encode('utf-8').decode('utf-8')
            else:
                _require(type(value) is (float if kind == 1 else int))
        values.append((sdk, present, value))
    return _RawSnapshot(tuple(values))


class _State(StrEnum):
    OPEN = 'OPEN'
    CAPTURING = 'CAPTURING'
    RAW_CAPTURED = 'RAW_CAPTURED'
    VERIFYING = 'VERIFYING'
    CONSUMED = 'CONSUMED'
    INVALID = 'INVALID'


class _Frame:
    __slots__ = ('receipt', 'receipt_values', 'thread', 'unpaired_depth', 'state',
                 'snapshot', 'observation', 'result')

    def __init__(self, receipt, thread, unpaired_depth):
        self.receipt, self.thread, self.state = receipt, thread, _State.OPEN
        self.unpaired_depth = unpaired_depth
        self.receipt_values = (receipt.namespace, receipt.invocation_number,
                              receipt.thread_ident, receipt.depth, receipt.parent_number)
        self.snapshot = self.observation = self.result = None

    def invalidate(self):
        self.state = _State.INVALID
        self.snapshot = self.observation = self.result = None


class _OrderStatusPairingBoundary:
    __slots__ = ('__decoder', '__decoder_type', '__original', '__dispatcher', '__table',
                 '__handle', '__handler', '__message', '__number', '__namespace',
                 '__interceptor', '__guard', '__stack', '__counter', '__entry_key', '__unpaired_depth')

    def __init__(self, *args, **kwargs):
        raise ReadOnlyError(_ERROR)  # Only the qualified installer publishes one.

    def __setattr__(self, name, value):
        raise ReadOnlyError(_ERROR)

    def __delattr__(self, name):
        raise ReadOnlyError(_ERROR)

    def __repr__(self):
        return '<PrivateOrderStatusPairingBoundary>'

    def _check_binding(self):
        try:
            decoder = self.__decoder
            values = object.__getattribute__(decoder, '__dict__')
            _require(type(decoder) is self.__decoder_type
                     and values.get(_INSTALLATION) is self
                     and values.get('processProtoBuf') is self.__interceptor
                     and self.__decoder_type.processProtoBuf is self.__dispatcher
                     and self.__decoder_type.msgId2handleInfoProtoBuf is self.__table
                     and self.__table.get(self.__message) is self.__handle
                     and self.__handle.processMeth is self.__handler
                     and self.__decoder_type.processOrderStatusMsgProtoBuf is self.__handler)
        except Exception:
            raise ReadOnlyError(_ERROR) from None

    def _scope(self, receipt):
        self._check_binding()
        _require(self.__stack and self.__stack[-1].thread is current_thread())
        frame = self.__stack[-1]
        _require(frame.unpaired_depth == self.__unpaired_depth)
        _require(type(receipt) is _OrderStatusInvocation and receipt is frame.receipt)
        _OrderStatusInvocation.__post_init__(receipt)
        _require((receipt.namespace, receipt.invocation_number, receipt.thread_ident,
                  receipt.depth, receipt.parent_number) == frame.receipt_values)
        _require(receipt.namespace is self.__namespace
                 and receipt.thread_ident == get_ident()
                 and receipt.depth == len(self.__stack)
                 and receipt.parent_number == (self.__stack[-2].receipt.invocation_number
                                                if len(self.__stack) > 1 else None))
        return frame

    def _current_invocation(self):
        with self.__guard:
            self._check_binding()
            _require(self.__stack and self.__stack[-1].thread is current_thread())
            receipt = self.__stack[-1].receipt
            self._scope(receipt)
            return receipt

    def _capture_raw(self, receipt, raw):
        with self.__guard:
            frame = self._scope(receipt)
            if frame.state is not _State.OPEN:
                frame.invalidate()
                raise ReadOnlyError(_ERROR)
            frame.state = _State.CAPTURING
        try:
            # Never hold our state lock across source getters/adaptation.
            snapshot, observation = _snapshot(raw)
            with self.__guard:
                _require(self._scope(receipt) is frame and frame.state is _State.CAPTURING)
                frame.snapshot, frame.observation = snapshot, observation
                frame.state = _State.RAW_CAPTURED
        except BaseException as error:
            with self.__guard:
                frame.invalidate()
            if isinstance(error, ReadOnlyError):
                raise ReadOnlyError(_ERROR) from None
            raise

    def _consume_normalized(self, receipt, *values):
        with self.__guard:
            frame = self._scope(receipt)
            if frame.state is not _State.RAW_CAPTURED:
                frame.invalidate()
                raise ReadOnlyError(_ERROR)
            frame.state = _State.VERIFYING
            snapshot, observation = frame.snapshot, frame.observation
        try:
            # PRE3 supplies ALL comparison rules, including UNKNOWN before
            # normalization, Decimal equivalence and signed-zero float hex.
            checked = source._adapt_protobuf_order_status(snapshot, normalized=values)
            _require(checked == observation)
            result = _PairedOrderStatus(receipt, checked)
            with self.__guard:
                _require(self._scope(receipt) is frame and frame.state is _State.VERIFYING)
                frame.result = result
                frame.snapshot = frame.observation = None
                frame.state = _State.CONSUMED
            return result
        except BaseException as error:
            with self.__guard:
                frame.invalidate()
            if isinstance(error, ReadOnlyError):
                raise ReadOnlyError(_ERROR) from None
            raise

    def _begin(self, entry_key):
        with self.__guard:
            _require(entry_key is self.__entry_key)
            self._check_binding()
            _require(not self.__stack or self.__stack[-1].thread is current_thread())
            _require(len(self.__stack) < _MAX_DEPTH and self.__counter < _MAX_INVOCATION)
            number = self.__counter + 1
            receipt = _OrderStatusInvocation(self.__namespace, number, get_ident(),
                len(self.__stack) + 1,
                self.__stack[-1].receipt.invocation_number if self.__stack else None)
            object.__setattr__(self, '_OrderStatusPairingBoundary__counter', number)
            frame = _Frame(receipt, current_thread(), self.__unpaired_depth)
            self.__stack.append(frame)
            return frame

    def _close(self, receipt, *, aborted=False):
        with self.__guard:
            frame = self._scope(receipt)  # Foreign close cannot corrupt the owner.
            successful = not aborted and frame.state is _State.CONSUMED
            frame.invalidate()
            self.__stack.pop()
            return successful

    def _intercept(self, decoder, protoBuf, msgId):
        self._check_binding()
        _require(decoder is self.__decoder)
        order_status = (type(msgId) is int and msgId == self.__number
                        or type(msgId) is type(self.__message) and msgId is self.__message)
        if not order_status:
            # An unrelated nested dispatcher cannot borrow its parent's scope.
            # This is only a visibility guard, never a pairing scope/receipt.
            with self.__guard:
                masked = bool(self.__stack and self.__stack[-1].thread is current_thread())
                prior_depth = self.__unpaired_depth
            try:
                if masked:
                    with self.__guard:
                        _require(prior_depth < _MAX_DEPTH)
                        object.__setattr__(self, '_OrderStatusPairingBoundary__unpaired_depth',
                                           prior_depth + 1)
                return self.__original(protoBuf, msgId)
            finally:
                if masked:
                    with self.__guard:
                        object.__setattr__(self, '_OrderStatusPairingBoundary__unpaired_depth',
                                           prior_depth)
        _require(type(protoBuf) is bytes)
        with self.__guard:
            _require(not self.__stack or self.__stack[-1].thread is current_thread())
            entry_depth = len(self.__stack)
        frame = None
        aborted = True
        try:
            # Enter INSIDE try: an interruption after stack insertion but before
            # this assignment must also be cleaned by the invocation finally.
            frame = self._begin(self.__entry_key)
            result = self.__original(protoBuf, msgId)
            aborted = False
            return result
        finally:
            # Close using the captured frame, even if the SDK raised after pairing.
            # Original exceptions must never be replaced by orphan reporting.
            with self.__guard:
                complete = (frame is not None and self.__stack and self.__stack[-1] is frame
                            and frame.thread is current_thread())
                success = complete and not aborted and frame.state is _State.CONSUMED
                if frame is not None:
                    frame.invalidate()
                # Invalid nesting must never leave descendant orphan state
                # behind. Preserve any outer invocation below this exact frame.
                index = next((i for i, active in enumerate(self.__stack) if active is frame), None)
                if (frame is None and len(self.__stack) > entry_depth
                        and self.__stack[entry_depth].thread is current_thread()):
                    index = entry_depth
                if index is not None:
                    for active in self.__stack[index:]:
                        active.invalidate()
                    del self.__stack[index:]
            if not aborted and not success:
                raise ReadOnlyError(_ERROR) from None
            if not aborted:
                self._check_binding()  # Cleanup precedes any final integrity error.


def _load_pairing_sdk():
    return (importlib.import_module('ibapi.decoder').Decoder,
            importlib.import_module('ibapi.message').IN)


def _install_order_status_pairing(decoder):
    """Lifetime-only instance interception. No EClient creation or SDK request."""
    with _INSTALL_LOCK:
        return _install(decoder)


def _install(decoder):
    try:
        decoder_type, incoming = _load_pairing_sdk()
        _require(type(decoder_type) is type and type(decoder) is decoder_type
                 and isinstance(incoming, type))
        values = object.__getattribute__(decoder, '__dict__')
        _require(type(values) is dict and _INSTALLATION not in values
                 and 'processProtoBuf' not in values)
        message = incoming.ORDER_STATUS
        number = message if type(message) is int else (
            message.value if isinstance(incoming, type) and issubclass(incoming, Enum)
            and type(message) is incoming and incoming.__members__.get('ORDER_STATUS') is message else None)
        _require(type(number) is int and number > 0)
        dispatcher, handler = decoder_type.processProtoBuf, decoder_type.processOrderStatusMsgProtoBuf
        _require(type(dispatcher) is FunctionType and type(handler) is FunctionType)
        for method, count in ((dispatcher, 3), (handler, 2)):
            parameters = tuple(inspect.signature(method).parameters.values())
            _require(len(parameters) == count and all(p.kind in
                (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty for p in parameters))
        table = decoder_type.msgId2handleInfoProtoBuf
        _require(type(table) is dict and message in table)
        handle = table[message]
        _require(handle.processMeth is handler)
        original = decoder.processProtoBuf
        _require(type(original) is MethodType and original.__self__ is decoder
                 and original.__func__ is dispatcher)
        boundary = object.__new__(_OrderStatusPairingBoundary)
        for name, value in (('decoder', decoder), ('decoder_type', decoder_type),
            ('original', original), ('dispatcher', dispatcher), ('table', table),
            ('handle', handle), ('handler', handler), ('message', message), ('number', number),
            ('namespace', object()), ('guard', RLock()), ('stack', []), ('counter', 0),
            ('entry_key', object()), ('unpaired_depth', 0)):
            object.__setattr__(boundary, '_OrderStatusPairingBoundary__' + name, value)
        def intercept(instance, protoBuf, msgId):
            return boundary._intercept(instance, protoBuf, msgId)
        interceptor = MethodType(intercept, decoder)
        object.__setattr__(boundary, '_OrderStatusPairingBoundary__interceptor', interceptor)
        # Dict assignment avoids SDK setters. Nothing global is changed; these
        # two stored objects form the exact one-time installation association.
        try:
            values[_INSTALLATION] = boundary
            values['processProtoBuf'] = interceptor
        except BaseException:
            if values.get(_INSTALLATION) is boundary:
                values.pop(_INSTALLATION, None)
            if values.get('processProtoBuf') is interceptor:
                values.pop('processProtoBuf', None)
            raise
        return boundary
    except Exception:
        raise ReadOnlyError(_ERROR) from None
