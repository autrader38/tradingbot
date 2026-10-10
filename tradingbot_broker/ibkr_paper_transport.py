"""10C3C1 isolated SDK foundation. No connect API or production write dispatch.

All dispatch primitives are private and exercised only with offline SDK doubles.
An authorization/risk/dispatch bridge and real connection are deferred to 10C3C2.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, StrEnum
import importlib
import inspect
import logging
from threading import RLock

from .ibkr_readonly import _load_official_api, _read_opcodes, _PROTOBUF_READ_BASES
from .ibkr_readonly_wire import sdk_call
from .ibkr_paper_wire import (_guarded_connection, _LEGACY_POLICY, _PROTOBUF_POLICY,
                              _LEGACY_WRITES, _PROTOBUF_WRITES)
from .models import Side
from .readonly_models import AccountMode, ReadOnlyError


MIN_ORDER_ID = 0
MAX_ORDER_ID = 2_147_483_647


def _valid_order_id(value):
    return type(value) is int and MIN_ORDER_ID <= value <= MAX_ORDER_ID


class PaperTransportIntent(StrEnum):
    LOCAL_PAPER_FOUNDATION = 'LOCAL_PAPER_FOUNDATION'


@dataclass(frozen=True, slots=True)
class PaperTWSConfig:
    intent: PaperTransportIntent  # Required, exact typed intent; no environment switch.
    host: str = '127.0.0.1'
    port: int = 4002
    client_id: int = 1
    timeout_seconds: int = 10

    def __post_init__(self):
        if type(self.intent) is not PaperTransportIntent:
            raise ReadOnlyError('PAPER_ONLY_TRANSPORT_REQUIRED')
        if type(self.host) is not str or self.host not in ('127.0.0.1', 'localhost', '::1'):
            raise ReadOnlyError('LOCAL_IB_GATEWAY_REQUIRED')
        for value, maximum in ((self.port, 65535), (self.client_id, 2147483647),
                               (self.timeout_seconds, 60)):
            if type(value) is not int or not 0 < value <= maximum:
                raise ReadOnlyError('INVALID_IBKR_CONFIGURATION')


class _DispatchState(StrEnum):
    NOT_DISPATCHED = 'NOT_DISPATCHED'
    DISPATCHED = 'DISPATCHED'
    OUTCOME_UNKNOWN = 'OUTCOME_UNKNOWN'


class _Operation(StrEnum):
    PLACE_ORDER = 'PLACE_ORDER'
    CANCEL_ORDER = 'CANCEL_ORDER'
    GLOBAL_CANCEL = 'GLOBAL_CANCEL'


class _Reason(StrEnum):
    SDK_RETURNED = 'SDK_RETURNED'  # Not broker receipt or acceptance.
    PREFLIGHT_FAILED = 'PREFLIGHT_FAILED'
    SDK_STATE_UNAVAILABLE = 'SDK_STATE_UNAVAILABLE'
    SDK_INVOCATION_UNCERTAIN = 'SDK_INVOCATION_UNCERTAIN'


@dataclass(frozen=True, slots=True)
class _DispatchResult:
    state: _DispatchState
    operation: _Operation
    reason: _Reason
    generation: int
    order_id: int | None = None
    error_codes: tuple[int, ...] = ()
    reconciliation_required: bool = False

    def __post_init__(self):
        if (type(self.state) is not _DispatchState or type(self.operation) is not _Operation
                or type(self.reason) is not _Reason or type(self.generation) is not int
                or self.generation < 0 or (self.order_id is not None and
                    not _valid_order_id(self.order_id))
                or type(self.reconciliation_required) is not bool
                or type(self.error_codes) is not tuple
                or any(type(code) is not int or not 0 <= code <= 2147483647
                       for code in self.error_codes)):
            raise ReadOnlyError('INVALID_PAPER_DISPATCH_RESULT')


@dataclass(frozen=True, slots=True)
class _StockMarketSpec:
    """Minimal private SDK construction fixture; no production order translation."""
    con_id: int
    side: Side
    quantity: Decimal

    def __post_init__(self):
        if (type(self.con_id) is not int or self.con_id <= 0 or type(self.side) is not Side
                or type(self.quantity) is not Decimal or not self.quantity.is_finite()
                or self.quantity <= 0):
            raise ReadOnlyError('INVALID_PAPER_ORDER_SPECIFICATION')


_WRITE_BASES = (('PLACE_ORDER', 3), ('CANCEL_ORDER', 4), ('REQ_GLOBAL_CANCEL', 58))


def _qualify(api):
    if type(api.PROTOBUF_MSG_ID) is not int or api.PROTOBUF_MSG_ID != 200:
        raise ReadOnlyError('UNSUPPORTED_PAPER_IBAPI_CONTRACT')
    if _read_opcodes(api.OUT) != dict(_PROTOBUF_READ_BASES):
        raise ReadOnlyError('UNSUPPORTED_PAPER_IBAPI_CONTRACT')
    result = {}
    for name, expected in _WRITE_BASES:
        value = getattr(api.OUT, name, None)
        if type(value) is not int:
            if not (isinstance(api.OUT, type) and issubclass(api.OUT, Enum)
                    and type(value) is api.OUT and value.name == name):
                raise ReadOnlyError('UNSUPPORTED_PAPER_IBAPI_CONTRACT')
            value = value.value
        if type(value) is not int or value != expected:
            raise ReadOnlyError('UNSUPPORTED_PAPER_IBAPI_CONTRACT')
        result[name] = value
    # Numeric mappings are never supplied by a caller or expanded from a range.
    if frozenset(result.values()) != _LEGACY_WRITES:
        raise ReadOnlyError('UNSUPPORTED_PAPER_IBAPI_CONTRACT')
    if frozenset(value + api.PROTOBUF_MSG_ID for value in result.values()) != _PROTOBUF_WRITES:
        raise ReadOnlyError('UNSUPPORTED_PAPER_IBAPI_CONTRACT')
    for method, parameters in (
        ('placeOrder', ('self', 'orderId', 'contract', 'order')),
        ('cancelOrder', ('self', 'orderId', 'orderCancel')),
        ('reqGlobalCancel', ('self', 'orderCancel')),
    ):
        signature = inspect.signature(getattr(api.Client, method))
        if tuple(signature.parameters) != parameters or any(
            p.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD
            or p.default is not inspect.Parameter.empty
            for p in signature.parameters.values()
        ):
            raise ReadOnlyError('UNSUPPORTED_PAPER_IBAPI_CONTRACT')
    return result


def _load_paper_api():
    api = _load_official_api()
    try:
        api.Contract = importlib.import_module('ibapi.contract').Contract
        api.Order = importlib.import_module('ibapi.order').Order
        api.OrderCancel = importlib.import_module('ibapi.order_cancel').OrderCancel
        return api
    except ImportError:
        raise ReadOnlyError('OFFICIAL_IBAPI_DEPENDENCY_UNAVAILABLE') from None
    except Exception:
        raise ReadOnlyError('SDK_IMPORT_FAILED') from None


def _make_paper_client(api, owner, generation):
    sdk_call('UNSUPPORTED_PAPER_IBAPI_CONTRACT', _qualify, api)
    legacy, protobuf = _LEGACY_POLICY, _PROTOBUF_POLICY
    # Suppress SDK logging of raw request/response payloads, as on the read path.
    for name in ('ibapi', *tuple(logging.Logger.manager.loggerDict)):
        if name == 'ibapi' or name.startswith('ibapi.'):
            logger = logging.getLogger(name)
            logger.handlers = [logging.NullHandler()]
            logger.propagate = False
            logger.disabled = True

    class Wrapper(api.Wrapper):
        def logAnswer(self, *args, **kwargs): pass
        def nextValidId(self, orderId): owner._callback(generation, 'next', orderId)
        def openOrder(self, orderId, contract, order, orderState):
            owner._callback(generation, 'observed', orderId)
        def openOrderEnd(self): owner._callback(generation, 'open_end')
        def orderStatus(self, orderId, status, filled, remaining, avgFillPrice,
                        permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
            owner._callback(generation, 'observed', orderId)
        def error(self, *args):
            # Legacy fixture: reqId,code,text. Modern: reqId,time,code,text[,advanced].
            # Never reinterpret a malformed modern code as its timestamp.
            if len(args) in (4, 5):
                code = args[2]
            elif len(args) == 3:
                code = args[1]
            else:
                code = None
            owner._callback(generation, 'error', code)
        def execDetails(self, reqId, contract, execution):
            owner._callback(generation, 'execution')
        def execDetailsEnd(self, reqId): owner._callback(generation, 'executions_end')
        def commissionReport(self, report): owner._callback(generation, 'commission')
        def commissionAndFeesReport(self, report): owner._callback(generation, 'commission')
        def connectionClosed(self): owner._callback(generation, 'closed')

    connection = None

    class GuardedClient(api.Client):
        @property
        def conn(self): return connection
        @conn.setter
        def conn(self, value):
            nonlocal connection
            connection = None if value is None else _guarded_connection(value, api)
        def logRequest(self, *args, **kwargs): pass
        def sendMsg(self, msgId, msg):
            if type(msgId) is int:
                opcode = msgId
            elif (isinstance(api.OUT, type) and issubclass(api.OUT, Enum)
                  and type(msgId) is api.OUT and getattr(api.OUT, msgId.name, None) is msgId):
                opcode = msgId.value
            else:
                raise ReadOnlyError('PAPER_WIRE_MESSAGE_FORBIDDEN')
            if type(opcode) is not int or opcode not in legacy or type(msg) is not str:
                raise ReadOnlyError('PAPER_WIRE_MESSAGE_FORBIDDEN')
            return super().sendMsg(msgId, msg)
        def sendMsgProtoBuf(self, msgId, msg):
            if type(msgId) is not int or msgId not in protobuf or type(msg) is not bytes:
                raise ReadOnlyError('PAPER_WIRE_MESSAGE_FORBIDDEN')
            return super().sendMsgProtoBuf(msgId, msg)

    client = GuardedClient(Wrapper())
    # No raw SDK object, generic sender, connect, run, socket or descriptor on view.
    class PaperClientView:
        __slots__ = ()
        def isConnected(self): return sdk_call('SDK_STATE_UNAVAILABLE', client.isConnected)
        def disconnect(self): return sdk_call('SDK_SHUTDOWN_FAILED', client.disconnect)
        def reqAllOpenOrders(self): return sdk_call('SDK_READ_FAILED', client.reqAllOpenOrders)
        def placeOrder(self, orderId, contract, order):
            return sdk_call('SDK_WRITE_FAILED', client.placeOrder, orderId, contract, order)
        def cancelOrder(self, orderId, orderCancel):
            return sdk_call('SDK_WRITE_FAILED', client.cancelOrder, orderId, orderCancel)
        def reqGlobalCancel(self, orderCancel):
            return sdk_call('SDK_WRITE_FAILED', client.reqGlobalCancel, orderCancel)
        def __repr__(self): return '<IsolatedPaperTWSClient>'
    return PaperClientView()


class PaperTWSTransport:
    """No public connect or write methods. Never installed in a broker facade.

The constructor qualifies the optional SDK but makes no network connection.
Private session/dispatch methods support offline qualification only in this phase.
"""
    def __init__(self, config: PaperTWSConfig):
        if type(config) is not PaperTWSConfig:
            raise ReadOnlyError('PAPER_ONLY_TRANSPORT_REQUIRED')
        config.__post_init__()
        api = sdk_call('SDK_INITIALIZATION_FAILED', _load_paper_api)
        sdk_call('UNSUPPORTED_PAPER_IBAPI_CONTRACT', _qualify, api)
        self._config = config
        self._lock = RLock()
        # Never held while waiting for _lock or calling SDK methods. An error can
        # revoke write eligibility even when its bookkeeping waits on dispatch.
        self._arrival_lock = RLock()
        self._reconciliation_required = False  # Instance lifetime; no clear API.
        self._write_invocation_started = False  # Never reset by reconnect.
        self._callback_generation = 0
        self._lifetime_order_floor = MIN_ORDER_ID
        self._lifetime_order_ids_exhausted = False
        self.__dispatch_binding = None  # Strong lifetime owner + captured methods.
        self._create_client = lambda generation: sdk_call('SDK_CLIENT_CONSTRUCTION_FAILED', _make_paper_client, api, self, generation)
        self._build_order = lambda spec: self._sdk_order(api, spec)
        self._build_cancel = lambda: sdk_call('SDK_OBJECT_CONSTRUCTION_FAILED', api.OrderCancel)
        self._client = None
        self._generation = 0
        self._reset()

    @property
    def account_mode(self): return AccountMode.UNKNOWN

    def __setattr__(self, name, value):
        if name == '_PaperTWSTransport__dispatch_binding' and hasattr(self, name):
            raise ReadOnlyError('PAPER_COORDINATOR_BINDING_IMMUTABLE')
        super().__setattr__(name, value)

    def __delattr__(self, name):
        if name == '_PaperTWSTransport__dispatch_binding':
            raise ReadOnlyError('PAPER_COORDINATOR_BINDING_IMMUTABLE')
        super().__delattr__(name)

    @property
    def _dispatch_coordinator_owner(self):
        return None if self.__dispatch_binding is None else self.__dispatch_binding[0]

    def _claim_dispatch_coordinator(self, owner):
        from .paper_dispatch import _PaperOrderDispatchCoordinator
        if type(owner) is not _PaperOrderDispatchCoordinator:
            raise ReadOnlyError('EXACT_PAPER_COORDINATOR_REQUIRED')
        with self._lock:
            if self.__dispatch_binding is not None:
                raise ReadOnlyError('PAPER_COORDINATOR_ALREADY_BOUND')
            # Capture reviewed methods, not mutable per-instance hook attributes.
            binding = (owner,
                _PaperOrderDispatchCoordinator._prepare_commit.__get__(owner),
                _PaperOrderDispatchCoordinator._validate_commit.__get__(owner))
            object.__setattr__(self, '_PaperTWSTransport__dispatch_binding', binding)

    def __repr__(self): return '<PaperTWSTransport offline-foundation>'

    def _reset(self):
        self._boundary = None
        self._exhausted = self._lifetime_order_ids_exhausted
        self._initialized = False
        self._sync = 'NOT_REQUESTED'
        self._known = set()
        self._closed = False
        self._invalid_callback = False
        self._errors = []
        self._error_sequence = 0
        self._callback_kinds = set()  # No raw objects/economic state retained.

    def _begin_generation(self):
        # No SDK connect call. Test doubles alone supply a connected session.
        with self._lock:
            self.disconnect()
            with self._arrival_lock:
                self._generation += 1
                self._callback_generation = self._generation
            self._reset()
            self._client = self._create_client(self._generation)

    def disconnect(self):
        with self._lock:
            client, self._client = self._client, None
            self._closed = True
            self._initialized = False
            self._boundary = None
            self._known.clear()
            if client is not None:
                sdk_call('SDK_SHUTDOWN_FAILED', client.disconnect)
            # Never cancel/global-cancel in cleanup; no reader thread is joined.

    def _callback(self, generation, kind, value=None):
        if kind == 'error':
            # Announce before waiting on the dispatch lock. No fairness assumption
            # about RLock acquisition can let a later writer absorb this error.
            with self._arrival_lock:
                if type(generation) is not int or generation != self._callback_generation:
                    return
                if self._write_invocation_started:
                    self._reconciliation_required = True
        with self._lock:
            if type(generation) is not int or generation != self._generation or self._closed:
                return
            if kind in ('next', 'observed'):
                if not _valid_order_id(value):
                    self._invalid_callback = True
                    return
                if kind == 'next':
                    self._initialized = True
                    lower = value
                else:
                    self._known.add(value)
                    if value == MAX_ORDER_ID:
                        self._lifetime_order_ids_exhausted = True
                        self._lifetime_order_floor = MAX_ORDER_ID
                        self._exhausted = True
                        self._boundary = None
                        return
                    lower = value + 1
                self._lifetime_order_floor = max(self._lifetime_order_floor, lower)
                if not self._lifetime_order_ids_exhausted:
                    self._boundary = max(self._lifetime_order_floor,
                        lower if self._boundary is None else self._boundary)
            elif kind == 'open_end':
                if self._sync == 'ACTIVE': self._sync = 'COMPLETED'
            elif kind == 'closed':
                self._closed = True
                self._initialized = False
                self._boundary = None
            elif kind == 'error':
                if type(value) is int and 0 <= value <= 2147483647:
                    self._errors = (self._errors + [value])[-128:]
                    self._error_sequence += 1
            elif kind in ('execution', 'executions_end', 'commission'):
                self._callback_kinds.add(kind)

    def _connected(self):
        if self._client is None or self._closed or self._invalid_callback:
            return False
        return self._client.isConnected() is True

    def _requires_reconciliation(self):
        with self._arrival_lock:
            return self._reconciliation_required

    def _latch_reconciliation(self):
        with self._arrival_lock:
            self._reconciliation_required = True

    def _synchronize_open_orders(self):
        with self._lock:
            if not self._connected() or not self._initialized or self._sync == 'ACTIVE':
                raise ReadOnlyError('PAPER_SESSION_NOT_READY')
            self._sync = 'ACTIVE'
            try:
                self._client.reqAllOpenOrders()
            except Exception:
                self._sync = 'NOT_REQUESTED'
                raise ReadOnlyError('SDK_READ_FAILED') from None

    def _allocate_order_id(self, generation):
        with self._lock, self._arrival_lock:
            if (type(generation) is not int or generation != self._generation
                    or not self._initialized or self._boundary is None
                    or not _valid_order_id(self._boundary) or self._exhausted
                    or self._lifetime_order_ids_exhausted
                    or self._closed or self._invalid_callback or self._sync != 'COMPLETED'
                    or self._reconciliation_required):
                raise ReadOnlyError('PAPER_ORDER_ID_UNAVAILABLE')
            result = max(self._boundary, self._lifetime_order_floor)
            if result == MAX_ORDER_ID:
                self._lifetime_order_ids_exhausted = True
                self._lifetime_order_floor = MAX_ORDER_ID
                self._exhausted = True
                self._boundary = None
            else:
                # Commit lifetime consumption before returning the reservation.
                self._lifetime_order_floor = result + 1
                self._boundary = self._lifetime_order_floor
            return result

    @staticmethod
    def _sdk_order(api, spec):
        # Deliberately tiny private stock/market adapter, not a strategy translation.
        if type(spec) is not _StockMarketSpec:
            raise ReadOnlyError('INVALID_PAPER_ORDER_SPECIFICATION')
        spec.__post_init__()
        def build():
            contract, order = api.Contract(), api.Order()
            contract.conId, contract.secType, contract.exchange = spec.con_id, 'STK', 'SMART'
            order.action, order.orderType, order.tif = spec.side.value, 'MKT', 'DAY'
            order.totalQuantity = spec.quantity
            return contract, order
        return sdk_call('SDK_OBJECT_CONSTRUCTION_FAILED', build)

    def _dispatch_new(self, spec):
        return self._dispatch(_Operation.PLACE_ORDER, spec)

    def _dispatch_cancel(self, known_order_id):
        return self._dispatch(_Operation.CANCEL_ORDER, known_order_id)

    def _dispatch_global_cancel(self):
        return self._dispatch(_Operation.GLOBAL_CANCEL, None)

    def _dispatch(self, operation, argument):
        with self._lock:
            if type(operation) is not _Operation:
                raise ReadOnlyError('INVALID_PAPER_OPERATION')
            generation = self._generation
            order_id = None
            try:
                if self._requires_reconciliation() or not self._connected():
                    raise ReadOnlyError('PAPER_SESSION_NOT_READY')
                binding = self.__dispatch_binding
                if binding is not None and operation is not _Operation.PLACE_ORDER:
                    raise ReadOnlyError('PAPER_COORDINATOR_NEW_ENTRY_ONLY')
                if operation is _Operation.PLACE_ORDER:
                    contract, order = self._build_order(argument)
                    order_id = self._allocate_order_id(generation)
                elif operation is _Operation.CANCEL_ORDER:
                    if not _valid_order_id(argument) or argument not in self._known:
                        raise ReadOnlyError('UNKNOWN_PAPER_ORDER')
                    order_id, cancel = argument, self._build_cancel()
                elif operation is _Operation.GLOBAL_CANCEL:
                    cancel = self._build_cancel()
                else:
                    raise ReadOnlyError('INVALID_PAPER_OPERATION')
                if generation != self._generation or not self._connected():
                    raise ReadOnlyError('PAPER_SESSION_NOT_READY')
                # Refresh authenticated local evidence outside the arrival lock.
                # The permanently bound owner already holds all outer locks.
                if binding is not None:
                    binding[1]()
            except Exception:
                return _DispatchResult(_DispatchState.NOT_DISPATCHED, operation,
                    _Reason.PREFLIGHT_FAILED, generation, order_id,
                    reconciliation_required=self._requires_reconciliation())
            # Final dispatch commitment is ordered against callback arrival.
            # No file I/O/callback wait occurs under the arrival lock. An error that
            # latches before commitment prevents this invocation; after commitment
            # it revokes all later writes without waiting for SDK return.
            with self._arrival_lock:
                if self._reconciliation_required:
                    return _DispatchResult(_DispatchState.NOT_DISPATCHED, operation,
                        _Reason.PREFLIGHT_FAILED, generation, order_id,
                        reconciliation_required=True)
                if binding is not None:
                    challenge = object()  # Local, fresh; no retained/replayable proof.
                    try:
                        proof = binding[2](challenge)
                        if proof is not challenge:
                            raise ReadOnlyError('PAPER_COMMIT_PROOF_INVALID')
                    except Exception:
                        return _DispatchResult(_DispatchState.NOT_DISPATCHED, operation,
                            _Reason.PREFLIGHT_FAILED, generation, order_id,
                            reconciliation_required=self._reconciliation_required)
                    if self._reconciliation_required:
                        return _DispatchResult(_DispatchState.NOT_DISPATCHED, operation,
                            _Reason.PREFLIGHT_FAILED, generation, order_id,
                            reconciliation_required=True)
                if operation is _Operation.PLACE_ORDER:
                    self._known.add(order_id)
                self._write_invocation_started = True
            errors_before = self._error_sequence
            try:
                if operation is _Operation.PLACE_ORDER:
                    self._client.placeOrder(order_id, contract, order)
                elif operation is _Operation.CANCEL_ORDER:
                    self._client.cancelOrder(order_id, cancel)
                else:
                    self._client.reqGlobalCancel(cancel)
            except BaseException as error:
                # Only SDK write invocation is inside this handler. Secure the
                # barrier before reporting or propagating an interruption.
                self._latch_reconciliation()
                if not isinstance(error, Exception):
                    raise
                return _DispatchResult(_DispatchState.OUTCOME_UNKNOWN, operation,
                    _Reason.SDK_INVOCATION_UNCERTAIN, generation, order_id, tuple(self._errors), True)
            # SDK normal return can include synchronous wrapper.error and no bytes.
            # This result explicitly does not establish broker receipt/acceptance.
            codes = tuple(self._errors) if self._error_sequence != errors_before else ()
            if codes:
                self._latch_reconciliation()
            return _DispatchResult(_DispatchState.DISPATCHED, operation,
                _Reason.SDK_RETURNED, generation, order_id, codes, self._requires_reconciliation())
