"""Private, memory-only decoder registration and fenced status handoff.

One exact receiver publishes qualified occurrences through the captured transport
method. Unaccepted or incompletely captured handoffs permanently inhibit delivery.
"""

from dataclasses import dataclass, fields
from decimal import Decimal
import dis
import sys
from threading import RLock, current_thread
from types import FunctionType, MethodType

from . import ibkr_order_status_evidence as source
from . import ibkr_order_status_pairing as pairing
from .readonly_models import ReadOnlyError


__all__ = ()
_ERROR = 'INVALID_PAPER_STATUS_DELIVERY'
_MAX_DEPTH = 32
_MAX_NUMBER = (1 << 63) - 1
_MAX_HANDLERS = 512
_MARKER = '_private_paper_status_delivery'


def _require(ok):
    if not ok:
        raise ReadOnlyError(_ERROR)


class _Opaque:
    __slots__ = ()

    def __repr__(self):
        return '<PrivatePaperStatusValue>'


@dataclass(frozen=True, slots=True, repr=False)
class _DecoderRegistration(_Opaque):
    namespace: object
    generation: int

    def __post_init__(self):
        _require(type(self.namespace) is object and type(self.generation) is int
                 and self.generation > 0)


@dataclass(frozen=True, slots=True, repr=False)
class _StatusDeliveryReceipt(_Opaque):
    registration: _DecoderRegistration
    number: int
    protocol: source._OrderStatusProtocol

    def __post_init__(self):
        _require(type(self.registration) is _DecoderRegistration)
        _DecoderRegistration.__post_init__(self.registration)
        _require(type(self.number) is int and 0 < self.number <= _MAX_NUMBER
                 and type(self.protocol) is source._OrderStatusProtocol)


@dataclass(frozen=True, slots=True, repr=False)
class _StatusArrivalFence(_Opaque):
    delivery: _StatusDeliveryReceipt
    arrival_marker: int

    def __post_init__(self):
        _require(type(self.delivery) is _StatusDeliveryReceipt)
        _StatusDeliveryReceipt.__post_init__(self.delivery)
        _require(type(self.arrival_marker) is int and self.arrival_marker > 0)


@dataclass(frozen=True, slots=True, repr=False)
class _StatusHandoff(_Opaque):
    generation: int
    delivery: _StatusDeliveryReceipt
    observation: source._OrderStatusObservation

    def __post_init__(self):
        _require(type(self.delivery) is _StatusDeliveryReceipt)
        _StatusDeliveryReceipt.__post_init__(self.delivery)
        _require(type(self.generation) is int
                 and self.generation == self.delivery.registration.generation
                 and type(self.observation) is source._OrderStatusObservation)
        source._OrderStatusObservation.__post_init__(self.observation)
        _require(self.observation.protocol is self.delivery.protocol)


@dataclass(frozen=True, slots=True, repr=False)
class _StatusReceiver(_Opaque):
    registration: _DecoderRegistration

    def __post_init__(self):
        _require(type(self.registration) is _DecoderRegistration)
        _DecoderRegistration.__post_init__(self.registration)


def _values(value):
    """Exact primitive snapshot, including Decimal representation/presence."""
    if type(value) in (source._OrderStatusObservation, source._OrderStatusPresence):
        return tuple((f.name, _values(getattr(value, f.name))) for f in fields(value))
    if type(value) is Decimal:
        return (Decimal, value.as_tuple())
    if type(value) in (source._OrderStatusProtocol, source._OrderStatusSourceQuality):
        return (type(value), value.value)
    if type(value) is frozenset:
        _require(all(type(v) is str for v in value))
        return (frozenset, tuple(sorted(value)))
    _require(type(value) in (str, int, type(None)))
    return (type(value), value)


class _Entry:
    __slots__ = ('receipt', 'receipt_values', 'thread', 'scope', 'invocation',
                 'handler', 'handoff', 'observation', 'values', 'accepted', 'state',
                 'publication_attempted', 'publication_started', 'publication_sequence',
                 'publication_complete', 'publication_result')

    def __init__(self, receipt, scope):
        self.receipt, self.scope, self.thread = receipt, scope, current_thread()
        self.receipt_values = (receipt.registration, receipt.number, receipt.protocol)
        self.invocation = self.handler = self.handoff = self.observation = self.values = None
        self.accepted, self.state = False, 'OPEN'
        self.publication_attempted = self.publication_started = self.publication_complete = False
        self.publication_sequence = self.publication_result = None

    def clear(self):
        self.invocation = self.handler = self.handoff = self.observation = self.values = None
        self.state = 'CLOSED'
        self.publication_attempted = self.publication_started = self.publication_complete = False
        self.publication_sequence = self.publication_result = None


class _LegacyHandle:
    """Instance-local handle facade; never mutate the original SDK HandleInfo."""
    __slots__ = ('__original', 'processMeth')

    def __init__(self, original, invoke):
        object.__setattr__(self, '_LegacyHandle__original', original)
        object.__setattr__(self, 'processMeth', invoke)

    def __setattr__(self, name, value):
        raise ReadOnlyError(_ERROR)

    def __getattr__(self, name):
        return getattr(self.__original, name)

    def __repr__(self):
        return '<PrivateLegacyStatusHandle>'


class _PaperStatusDelivery:
    __slots__ = ('__transport', '__generation', '__guard', '__client', '__wrapper',
                 '__wrapper_methods', '__decoder', '__phase', '__offline', '__ever_connected',
                 '__running', '__registration', '__registration_values', '__pairing',
                 '__decoder_type', '__legacy', '__protobuf', '__dispatcher', '__interpret',
                 '__legacy_name', '__legacy_table', '__legacy_items', '__shadow', '__shim',
                 '__interceptor', '__entries', '__dispatches', '__counter', '__receiver', '__closing',
                 '__legacy_processors', '__version', '__publisher', '__publication_type',
                 '__publication_method')

    def __init__(self, *args, **kwargs):
        raise ReadOnlyError(_ERROR)

    def __setattr__(self, name, value):
        raise ReadOnlyError(_ERROR)

    def __repr__(self):
        return '<PrivatePaperStatusDelivery>'

    def _set(self, name, value):
        object.__setattr__(self, '_PaperStatusDelivery__' + name, value)

    def _bind_client(self, client, wrapper):
        with self.__guard:
            _require(self.__client is None and self.__phase == 'NEW')
            self._set('client', client)
            self._set('wrapper', wrapper)
            self._set('wrapper_methods', (type(wrapper).orderStatusProtoBuf,
                                         type(wrapper).orderStatus))

    def _fixture_status(self):
        # Preserve the original isolated C1 doubles, which have NO SDK lifecycle.
        # Never a protocol classification or fallback after a registration error.
        with self.__guard:
            return self.__offline and self.__decoder is None

    def _decoder_get(self):
        with self.__guard:
            return self.__decoder

    def _decoder_set(self, value):
        with self.__guard:
            allowed = self.__phase in ('NEW', 'CONNECTING') and not self.__ever_connected
            if value is self.__decoder:
                return
            if allowed:
                self._set('decoder', value)
                if value is not None:
                    self._set('offline', False)
                return
        # Eligibility is retired BEFORE the replacement pointer is published.
        self._fail()
        with self.__guard:
            self._set('decoder', value)
        raise ReadOnlyError(_ERROR)

    def _fail(self):
        with self.__guard:
            self._set('phase', 'FAILED')
        self.__transport._status_delivery_failed(self)

    def _connect_begin(self):
        try:
            with self.__guard:
                _require(self.__client is not None and self.__phase == 'NEW'
                         and not self.__ever_connected and self.__running is None)
                self._set('offline', False)
                self._set('phase', 'CONNECTING')
            self.__transport._status_registration_not_ready(self)
        except BaseException:
            self._fail()
            raise

    def _connect_finished(self):
        try:
            with self.__guard:
                _require(self.__phase == 'CONNECTING')
                client, decoder = self.__client, self.__decoder
                self._set('phase', 'REGISTERING')
            # SDK state/getters/installation occur outside ALL transport/internal guards.
            _require(client.isConnected() is True)
            version = client.serverVersion()
            _require(type(version) is int and version > 0)
            decoder_type, _ = pairing._load_pairing_sdk()
            _require(type(decoder) is decoder_type and type(decoder.serverVersion) is int
                     and decoder.serverVersion == version and decoder.wrapper is self.__wrapper)
            self._set('version', version)
            self._install(decoder, decoder_type)
            self._arm_receiver()  # Complete capability BEFORE READY/run eligibility.
            with self.__guard:
                _require(self.__decoder is decoder and self.__phase == 'REGISTERING')
                self._set('ever_connected', True)
                self._set('phase', 'READY')
            self.__transport._status_registration_ready(self, self.__registration)
        except BaseException as error:
            self._fail()
            if isinstance(error, Exception):
                raise ReadOnlyError(_ERROR) from None
            raise

    def _install(self, decoder, decoder_type):
        legacy, interpret = decoder_type.processOrderStatusMsg, decoder_type.interpret
        _require(type(legacy) is FunctionType and type(interpret) is FunctionType)
        original = decoder.interpret
        _require(type(original) is MethodType and original.__self__ is decoder
                 and original.__func__ is interpret)
        values = object.__getattribute__(decoder, '__dict__')
        _require(type(values) is dict and 'interpret' not in values and _MARKER not in values)
        # Discover the SDK's legacy table by EXACT handler identity, not a guessed
        # field name/opcode. Require an ordinary self.attribute lookup in interpret.
        instructions = tuple(dis.get_instructions(interpret))
        lookups = {b.argval for a, b in zip(instructions, instructions[1:])
                   if a.opname == 'LOAD_FAST' and a.argval == 'self' and b.opname == 'LOAD_ATTR'}
        matches = []
        for name, table in vars(decoder_type).items():
            if name in lookups and type(table) is dict and 0 < len(table) <= _MAX_HANDLERS:
                for key, handle in table.items():
                    if getattr(handle, 'processMeth', None) is legacy:
                        matches.append((name, table, key, handle))
        _require(len(matches) == 1)
        name, table, key, handle = matches[0]
        _require(name not in values)
        registration = _DecoderRegistration(object(), self.__generation)
        self._set('registration', registration)
        self._set('registration_values', (registration.namespace, registration.generation))
        self._set('decoder_type', decoder_type)
        self._set('legacy', legacy)
        self._set('protobuf', decoder_type.processOrderStatusMsgProtoBuf)
        self._set('dispatcher', decoder_type.processProtoBuf)
        self._set('interpret', original)
        self._set('legacy_name', name)
        self._set('legacy_table', table)
        self._set('legacy_items', tuple(table.items()))
        self._set('legacy_processors', tuple((h, h.processMeth) for h in table.values()))
        def status(instance, fields):
            return self._legacy_status(instance, fields)
        def interpret_instance(instance, *args, **kwargs):
            return self._legacy_dispatch(instance, args, kwargs)
        shadow = dict(table)
        shadow[key] = _LegacyHandle(handle, status)
        interceptor = MethodType(interpret_instance, decoder)
        self._set('shadow', shadow)
        self._set('shim', status)
        self._set('interceptor', interceptor)
        boundary = pairing._install_order_status_pairing(decoder)
        self._set('pairing', boundary)
        # Only this instance is changed; global table/handlers/HandleInfo stay intact.
        values[name], values['interpret'], values[_MARKER] = shadow, interceptor, self
        pairing._install_status_delivery_observer(boundary, self)

    def _owns_pairing(self, boundary):
        return self.__pairing is boundary and self.__phase == 'REGISTERING'

    def _check(self, decoder=None):
        with self.__guard:
            _require(self.__phase == 'READY' and self.__registration is not None
                     and self.__decoder is not None and (decoder is None or decoder is self.__decoder))
            registration = self.__registration
            _require(type(self.__receiver) is _StatusReceiver
                     and self.__receiver.registration is registration
                     and type(self.__publisher) is MethodType
                     and self.__publisher.__self__ is self.__transport
                     and self.__publisher.__func__ is self.__publication_method)
            _DecoderRegistration.__post_init__(registration)
            _require((registration.namespace, registration.generation) == self.__registration_values)
            d, cls = self.__decoder, self.__decoder_type
        # SDK attribute checks occur outside internal/transport guards too.
        values = object.__getattribute__(d, '__dict__')
        _require(type(d) is cls and values.get('wrapper') is self.__wrapper
                 and type(values.get('serverVersion')) is int
                 and values['serverVersion'] == self.__version
                 and type(self.__wrapper).orderStatusProtoBuf is self.__wrapper_methods[0]
                 and type(self.__wrapper).orderStatus is self.__wrapper_methods[1])
        _require(values.get(_MARKER) is self and values.get('interpret') is self.__interceptor
                 and values.get(self.__legacy_name) is self.__shadow
                 and cls.processOrderStatusMsg is self.__legacy
                 and cls.processOrderStatusMsgProtoBuf is self.__protobuf
                 and cls.processProtoBuf is self.__dispatcher
                 and cls.interpret is self.__interpret.__func__
                 and getattr(cls, self.__legacy_name) is self.__legacy_table
                 and len(self.__legacy_table) == len(self.__legacy_items)
                 and len(self.__shadow) == len(self.__legacy_items))
        for key, handle in self.__legacy_items:
            _require(self.__legacy_table.get(key) is handle)
            actual = self.__shadow.get(key)
            if handle.processMeth is self.__legacy:
                _require(type(actual) is _LegacyHandle and actual.processMeth is self.__shim)
            else:
                _require(actual is handle)
        for handle, processor in self.__legacy_processors:
            _require(handle.processMeth is processor)
        # No internal guard while securing transport arrival synchronization.
        self.__transport._check_status_registration(self, registration)
        self.__pairing._check_binding()

    def _run_begin(self):
        try:
            self._check()
            with self.__guard:
                _require(self.__running is None)
                self._set('running', current_thread())
        except BaseException:
            self._fail()
            raise

    def _run_finished(self):
        with self.__guard:
            self._set('running', None)
        self._retire()

    def _retire(self):
        try:
            with self.__guard:
                self._set('phase', 'RETIRED')
            self.__transport._status_registration_retired(self)
        except BaseException:
            self._fail()
            raise

    def _push(self, protocol, scope):
        with self.__guard:
            _require(not self.__entries or self.__entries[-1].thread is current_thread())
            _require(len(self.__entries) + len(self.__dispatches) < _MAX_DEPTH
                     and self.__counter < _MAX_NUMBER)
            self._set('counter', self.__counter + 1)
            receipt = _StatusDeliveryReceipt(self.__registration, self.__counter, protocol)
            entry = _Entry(receipt, scope)
            self.__entries.append(entry)
            return entry

    def _top(self):
        _require(self.__entries and self.__entries[-1].thread is current_thread())
        entry = self.__entries[-1]
        _StatusDeliveryReceipt.__post_init__(entry.receipt)
        _require((entry.receipt.registration, entry.receipt.number, entry.receipt.protocol)
                 == entry.receipt_values and entry.receipt.registration is self.__registration)
        return entry

    def _cleanup(self, depth, successful):
        with self.__guard:
            removed = tuple(self.__entries[depth:])
            _require(all(e.thread is current_thread() for e in removed))
            success = (successful and len(removed) == 1 and removed[0].accepted
                       and removed[0].state == 'HANDOFF' and self.__phase == 'READY')
            self._set('closing', tuple(e.receipt for e in removed))
        if not success:
            self._fail()  # Safety before removing fences or temporary source state.
        try:
            for entry in removed:
                self.__transport._status_arrival_finished(self, entry.receipt)
        finally:
            with self.__guard:
                for entry in removed:
                    entry.clear()
                del self.__entries[depth:]
                self._set('closing', None)
        return success

    def _proto_check(self, decoder):
        try:
            self._check(decoder)
        except BaseException:
            self._fail()
            raise

    def _proto_enter(self, decoder):
        depth = len(self.__entries)
        witnessed = False
        try:
            self._check(decoder)
            frame = sys._getframe(1)
            _require(frame.f_code is pairing._OrderStatusPairingBoundary._intercept.__code__
                     and frame.f_locals.get('self') is self.__pairing)
            scope = id(frame)
            del frame
            witnessed = True
            entry = self._push(source._OrderStatusProtocol.PROTOBUF, scope)
            self.__transport._status_arrival_started(self, entry.receipt)
            return entry
        except BaseException:
            if witnessed:
                with self.__guard:
                    receipt = self.__entries[depth].receipt if len(self.__entries) > depth else None
                self.__transport._status_arrival_aborted(self, receipt)
            self._cleanup(depth, False)
            raise

    def _proto_bound(self, entry, invocation):
        with self.__guard:
            _require(self._top() is entry and entry.invocation is None)
            entry.invocation = invocation

    def _proto_exit(self, entry, successful):
        with self.__guard:
            _require(entry in self.__entries and entry.thread is current_thread())
            depth = self.__entries.index(entry)
        return self._cleanup(depth, successful)

    def _proto_cancel(self):
        # The caller might be interrupted after _proto_enter returned but BEFORE
        # it stored our entry. Find only that exact active dispatch frame; never
        # clear a parent's delivery or another thread's state.
        frame = sys._getframe(1)
        scope = id(frame)
        del frame
        with self.__guard:
            depth = next((i for i, entry in enumerate(self.__entries)
                          if entry.scope == scope and entry.thread is current_thread()), None)
        if depth is None:
            self._fail()
        else:
            self._cleanup(depth, False)

    def _legacy_dispatch(self, decoder, args, kwargs):
        marker = None
        try:
            self._check(decoder)
            with self.__guard:
                _require(not self.__dispatches or self.__dispatches[-1][0] is current_thread())
                _require(len(self.__entries) + len(self.__dispatches) < _MAX_DEPTH)
                marker = (current_thread(), id(sys._getframe()))
                self.__dispatches.append(marker)
            return self.__interpret(*args, **kwargs)
        except BaseException:
            self._fail()
            raise
        finally:
            if marker is not None:
                with self.__guard:
                    if self.__dispatches and self.__dispatches[-1] is marker:
                        self.__dispatches.pop()

    def _legacy_status(self, decoder, values):
        depth = len(self.__entries)
        successful = False
        witnessed = False
        try:
            self._check(decoder)
            f = sys._getframe(1)
            _require(f.f_code is self.__shim.__code__ and f.f_locals.get('instance') is decoder
                     and f.f_back.f_code is self.__interpret.__func__.__code__
                     and f.f_back.f_locals.get('self') is decoder)
            with self.__guard:
                _require(self.__dispatches and self.__dispatches[-1][0] is current_thread()
                         and id(f.f_back.f_back) == self.__dispatches[-1][1])
            del f
            witnessed = True
            entry = self._push(source._OrderStatusProtocol.LEGACY, id(sys._getframe()))
            self.__transport._status_arrival_started(self, entry.receipt)
            result = self.__legacy(decoder, values)
            successful = True
            return result
        except BaseException:
            if witnessed:
                with self.__guard:
                    receipt = self.__entries[depth].receipt if len(self.__entries) > depth else None
                self.__transport._status_arrival_aborted(self, receipt)
            self._fail()
            raise
        finally:
            try:
                complete = self._cleanup(depth, successful)
            except BaseException:
                self._fail()
                if successful:
                    raise
                complete = False  # Original SDK exception/interruption wins.
            if successful and not complete:
                raise ReadOnlyError(_ERROR)

    def _witness(self, entry, raw):
        # Examine only the known callback/handler route. Never retain any frame,
        # locals dict, source args or descriptor in entries/published values.
        f = sys._getframe(2)  # Wrapper, whose caller must be the exact SDK handler.
        try:
            _require(f.f_code is self.__wrapper_methods[0 if raw else 1].__code__
                     and f.f_locals.get('self') is self.__wrapper)
            handler = f.f_back
            proto = entry.receipt.protocol is source._OrderStatusProtocol.PROTOBUF
            _require(handler.f_code is (self.__protobuf if proto else self.__legacy).__code__
                     and handler.f_locals.get('self') is self.__decoder)
            if proto:
                _require(handler.f_back.f_code is self.__dispatcher.__code__
                         and handler.f_back.f_locals.get('self') is self.__decoder
                         and id(handler.f_back.f_back) == entry.scope)
            else:
                _require(not raw and handler.f_back.f_code is type(self)._legacy_status.__code__
                         and handler.f_back.f_locals.get('self') is self
                         and id(handler.f_back) == entry.scope)
            return id(handler)
        finally:
            del f

    def _raw(self, raw):
        try:
            self._check()
            with self.__guard:
                entry = self._top()
                _require(entry.receipt.protocol is source._OrderStatusProtocol.PROTOBUF
                         and entry.state == 'OPEN' and entry.invocation is not None)
                witness = self._witness(entry, True)
                entry.handler, entry.state = witness, 'CAPTURING'
                invocation = entry.invocation
            self.__pairing._capture_raw(invocation, raw)
            with self.__guard:
                _require(self._top() is entry and entry.state == 'CAPTURING')
                entry.state = 'RAW'
        except BaseException as error:
            self._fail()
            if isinstance(error, Exception):
                raise ReadOnlyError(_ERROR) from None
            raise

    def _normalized(self, values):
        try:
            self._check()
            with self.__guard:
                entry = self._top()
                witness = self._witness(entry, False)
                proto = entry.receipt.protocol is source._OrderStatusProtocol.PROTOBUF
                _require(entry.state == ('RAW' if proto else 'OPEN'))
                if proto:
                    _require(witness == entry.handler and entry.invocation is not None)
                entry.state = 'VERIFYING'
            if proto:
                paired = self.__pairing._consume_normalized(entry.invocation, *values)
                _require(paired.invocation is entry.invocation)
                observation = paired.observation
            else:
                observation = source._adapt_legacy_order_status(*values)
            handoff = self._make_handoff(entry, observation)
            self._receive_handoff(handoff, self.__receiver)
            return handoff
        except BaseException as error:
            self._fail()
            if isinstance(error, Exception):
                raise ReadOnlyError(_ERROR) from None
            raise

    def _make_handoff(self, entry, observation):
        handoff = _StatusHandoff(self.__generation, entry.receipt, observation)
        with self.__guard:
            _require(self._top() is entry and entry.state == 'VERIFYING')
            entry.handoff, entry.observation, entry.values = handoff, observation, _values(observation)
            entry.state = 'HANDOFF'
        return handoff

    def _arm_receiver(self):
        from .ibkr_paper_transport import PaperTWSTransport
        with self.__guard:
            _require(self.__phase == 'REGISTERING' and self.__registration is not None
                     and self.__receiver is None and self.__publisher is None
                     and type(self.__transport) is PaperTWSTransport)
            receiver = _StatusReceiver(self.__registration)
            publisher = PaperTWSTransport._publish_qualified_status.__get__(self.__transport)
            self._set('publisher', publisher)
            self._set('publication_method', publisher.__func__)
            self._set('publication_type', self.__transport._qualified_status_type)
            self._set('receiver', receiver)

    def _issue_handoff_receiver(self):
        # Registration already issued the only receiver; never issue/replace later.
        raise ReadOnlyError(_ERROR)

    def _live_handoff(self, handoff, receiver):
        # Caller owns PRE5 guard. No transport lock or SDK call here.
        entry = self._top()
        _require(type(receiver) is _StatusReceiver and receiver is self.__receiver
                 and receiver.registration is self.__registration
                 and type(handoff) is _StatusHandoff and handoff is entry.handoff
                 and handoff.delivery is entry.receipt and not entry.accepted
                 and entry.state == 'HANDOFF' and handoff.observation is entry.observation
                 and handoff.generation == self.__generation)
        _StatusHandoff.__post_init__(handoff)
        _require(_values(handoff.observation) == entry.values)
        return entry

    def _receive_handoff(self, handoff, receiver):
        try:
            self._check()
            with self.__guard:
                entry = self._live_handoff(handoff, receiver)
                _require(not entry.publication_attempted and self.__publisher is not None)
                entry.publication_attempted = True
                publisher = self.__publisher
            # Neither PRE5 nor PRE4 guard spans transport arrival synchronization.
            publisher(self, receiver, handoff, entry)
            self._accept_handoff(handoff, receiver)
        except BaseException:
            self._fail()
            raise

    def _accept_handoff(self, handoff, receiver):
        try:
            self._check()
            with self.__guard:
                entry = self._live_handoff(handoff, receiver)
                result = entry.publication_result
                _require(entry.publication_attempted is True and entry.publication_started is True
                         and entry.publication_complete is True
                         and type(entry.publication_sequence) is int and entry.publication_sequence > 0
                         and type(result) is tuple and len(result) == 2
                         and type(result[0]) is self.__publication_type
                         and type(result[1]) is int
                         and type(result[0].sequence) is int and type(result[0].generation) is int
                         and result[0].sequence == result[1] == entry.publication_sequence
                         and result[0].generation == handoff.generation
                         and _values(result[0].observation) == entry.values)
                source._OrderStatusObservation.__post_init__(result[0].observation)
                entry.accepted = True  # Fence remains until handler/delivery exit.
        except BaseException:
            self._fail()
            raise


def _new_status_delivery(transport, generation, *, offline_fixture=False):
    from .ibkr_paper_transport import PaperTWSTransport
    _require(type(transport) is PaperTWSTransport and type(generation) is int and generation > 0
             and type(offline_fixture) is bool)
    owner = object.__new__(_PaperStatusDelivery)
    for name in _PaperStatusDelivery.__slots__:
        owner._set(name[2:], None)
    for name, value in (('transport', transport), ('generation', generation), ('guard', RLock()),
                        ('phase', 'NEW'), ('offline', offline_fixture), ('ever_connected', False),
                        ('entries', []), ('dispatches', []), ('counter', 0)):
        owner._set(name, value)
    return owner
