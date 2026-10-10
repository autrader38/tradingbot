"""Isolated offline SDK foundation and private callback evidence.

All dispatch primitives are private and exercised only with offline SDK doubles.
The C3C2 coordinator is internal only. Real connection and production writes remain
unsupported; callbacks cannot attest PAPER account mode or grant authorization.
"""

from dataclasses import dataclass, fields, replace
from decimal import Decimal, Context, localcontext
from enum import Enum, StrEnum
import importlib
import inspect
import logging
from threading import RLock

from .ibkr_readonly import _load_official_api, _read_opcodes, _PROTOBUF_READ_BASES
from .ibkr_readonly_wire import sdk_call
from .ibkr_paper_wire import (_guarded_connection, _LEGACY_POLICY, _PROTOBUF_POLICY,
                              _LEGACY_WRITES, _PROTOBUF_WRITES)
from .models import Side, identifier
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
    order_ref: str | None = None

    def __post_init__(self):
        if (type(self.con_id) is not int or self.con_id <= 0 or type(self.side) is not Side
                or type(self.quantity) is not Decimal or not self.quantity.is_finite()
                or self.quantity <= 0):
            raise ReadOnlyError('INVALID_PAPER_ORDER_SPECIFICATION')
        if self.order_ref is not None:
            try:
                identifier(self.order_ref)
            except Exception:
                raise ReadOnlyError('INVALID_PAPER_ORDER_REFERENCE') from None


_EVIDENCE_CAPACITY = 128
_STATUSES = frozenset(('PendingSubmit', 'PreSubmitted', 'Submitted', 'Filled',
                       'Cancelled', 'ApiCancelled', 'Inactive', 'Expired'))


class _PrivateEvidence:
    __slots__ = ()

    def __repr__(self):
        return '<SanitizedPaperCallbackEvidence>'


@dataclass(frozen=True, slots=True, repr=False)
class _OpenOrderEvidence(_PrivateEvidence):
    generation: int
    sequence: int
    order_id: int
    client_id: int | None
    order_ref: str | None
    con_id: int
    security_type: str
    exchange: str | None
    symbol: str | None
    currency: str | None
    side: str
    order_type: str
    quantity: Decimal
    tif: str
    status: str


@dataclass(frozen=True, slots=True, repr=False)
class _OrderStatusEvidence(_PrivateEvidence):
    generation: int
    sequence: int
    order_id: int
    client_id: int
    status: str
    filled: Decimal
    remaining: Decimal
    average_price: Decimal
    last_price: Decimal
    parent_id: int


@dataclass(frozen=True, slots=True, repr=False)
class _ExecutionEvidence(_PrivateEvidence):
    generation: int
    sequence: int
    order_id: int
    client_id: int | None
    order_ref: str | None
    execution_id: str
    con_id: int
    side: str
    shares: Decimal
    price: Decimal


@dataclass(frozen=True, slots=True, repr=False)
class _EvidenceBatch(_PrivateEvidence):
    through_sequence: int
    events: tuple


def _callback_text(value, *, optional=False, reference=False):
    if optional and (value is None or type(value) is str and value == ''):
        return None
    if type(value) is not str or not 0 < len(value) <= 100:
        raise ValueError()
    if reference:
        identifier(value)
    elif not value.isascii() or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError()
    return value


def _callback_id(value, *, optional=False, contract=False):
    if optional and value is None:
        return None
    # conId is not an API orderId: do not apply the orderId int32 bound to it.
    if type(value) is not int or value < (1 if contract else 0):
        raise ValueError()
    if not contract and not _valid_order_id(value):
        raise ValueError()
    if contract and value.bit_length() > 64:  # Internal bounded evidence storage.
        raise ValueError()
    return value


def _callback_amount(value, *, positive=False, price=False):
    if price and type(value) in (float, int):
        value = Decimal(str(value))
    if (type(value) is not Decimal or not value.is_finite()
            or value < 0 or positive and value == 0):
        raise ValueError()
    # Bound copied evidence, not the SDK's unrelated numeric identifier contracts.
    parts = value.as_tuple()
    if len(parts.digits) > 64 or not -64 <= parts.exponent <= 64:
        raise ValueError()
    return value


def _callback_status(value):
    return value if type(value) is str and value in _STATUSES else 'UNKNOWN'


def _copy_callback(generation, sequence, kind, value):
    """Copy only qualified primitive fields; never inspect account/whyHeld text."""
    if kind == 'open':
        order_id, contract, order, state = value
        return _OpenOrderEvidence(generation, sequence, _callback_id(order_id),
            _callback_id(getattr(order, 'clientId', None), optional=True),
            _callback_text(getattr(order, 'orderRef', None), optional=True, reference=True),
            _callback_id(contract.conId, contract=True), _callback_text(contract.secType),
            _callback_text(getattr(contract, 'exchange', None), optional=True),
            _callback_text(getattr(contract, 'symbol', None), optional=True),
            _callback_text(getattr(contract, 'currency', None), optional=True),
            _callback_text(order.action), _callback_text(order.orderType),
            _callback_amount(order.totalQuantity, positive=True), _callback_text(order.tif),
            _callback_status(state.status))
    if kind == 'status':
        order_id, status, filled, remaining, average, parent, last, client_id = value
        return _OrderStatusEvidence(generation, sequence, _callback_id(order_id),
            _callback_id(client_id), _callback_status(status),
            _callback_amount(filled), _callback_amount(remaining),
            _callback_amount(average, price=True), _callback_amount(last, price=True),
            _callback_id(parent))
    contract, execution = value
    return _ExecutionEvidence(generation, sequence, _callback_id(execution.orderId),
        _callback_id(getattr(execution, 'clientId', None), optional=True),
        _callback_text(getattr(execution, 'orderRef', None), optional=True, reference=True),
        _callback_text(execution.execId, reference=True),
        _callback_id(contract.conId, contract=True), _callback_text(execution.side),
        _callback_amount(execution.shares, positive=True),
        _callback_amount(execution.price, price=True))


def _evidence_identity(event):
    return (type(event), tuple(getattr(event, f.name) for f in fields(event)
                              if f.name != 'sequence'))


def _sum_callback_amounts(left, right):
    # Accepted evidence has at most 64 digits and exponents in [-64, 64].
    # 256 digits preserve exact sums across the entire bounded 128-event ledger;
    # application Decimal context must not round away an execution overfill.
    with localcontext(Context(prec=256)):
        return left + right


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
            owner._callback(generation, 'open', (orderId, contract, order, orderState))
        def openOrderEnd(self): owner._callback(generation, 'open_end')
        def orderStatus(self, orderId, status, filled, remaining, avgFillPrice,
                        permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
            owner._callback(generation, 'status', (orderId, status, filled, remaining,
                avgFillPrice, parentId, lastFillPrice, clientId))
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
            owner._callback(generation, 'execution', (contract, execution))
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
        self._callback_sequence = 0  # Transport lifetime; never reset.
        self._callback_evidence = []
        self._callback_run_ends = []  # Exact duplicate sequence spans, bounded with the ledger.
        self._last_callback_identity = None
        self._commit_callback_sequence = 0
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
        if kind in ('error', 'open', 'status', 'execution'):
            # Announce before waiting on the dispatch lock. No fairness assumption
            # about RLock acquisition can let a later writer absorb this error.
            with self._arrival_lock:
                if (type(generation) is not int or generation != self._callback_generation
                        or kind != 'error' and self._closed):
                    return
                if kind == 'error' and self._write_invocation_started:
                    self._reconciliation_required = True
                elif kind != 'error' and self.__dispatch_binding is not None:
                    try:
                        event = _copy_callback(generation, self._callback_sequence + 1, kind, value)
                        # Accepted observed IDs enter lifetime history NOW, before
                        # deferred dispatch bookkeeping or generation reset.
                        self._raise_observed_floor(event.order_id)
                        self._callback_sequence += 1
                        identity = _evidence_identity(event)
                        # Consecutive exact duplicates have new sequence numbers
                        # but need no storage. Interleaved repeats are retained:
                        # Submitted -> Cancelled -> Submitted is a conflict.
                        if identity != self._last_callback_identity:
                            if len(self._callback_evidence) >= _EVIDENCE_CAPACITY:
                                self._reconciliation_required = True
                            else:
                                self._callback_evidence.append(event)
                                self._callback_run_ends.append(event.sequence)
                                self._last_callback_identity = identity
                        else:
                            self._callback_run_ends[-1] = event.sequence
                    except BaseException as error:
                        if not isinstance(error, Exception):
                            # Interrupted current-generation truth collection is
                            # uncertain. Secure local safety BEFORE propagation.
                            self._reconciliation_required = True
                            raise
                        if self._write_invocation_started:
                            self._reconciliation_required = True
            # Release arrival BEFORE dispatch bookkeeping; never reverse nesting.
        if kind in ('open', 'status'):
            kind, value = 'observed', value[0]
        with self._lock:
            if type(generation) is not int or generation != self._generation or self._closed:
                return
            if kind in ('next', 'observed'):
                if not _valid_order_id(value):
                    self._invalid_callback = True
                    return
                with self._arrival_lock:
                    if kind == 'next':
                        self._initialized = True
                        lower = value
                    else:
                        self._known.add(value)
                        self._raise_observed_floor(value)
                        if value == MAX_ORDER_ID:
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

    def _raise_observed_floor(self, order_id):
        # Caller owns arrival. Only lifetime safety state changes here; current
        # generation readiness/known IDs remain under dispatch bookkeeping.
        if order_id == MAX_ORDER_ID:
            self._lifetime_order_ids_exhausted = True
            self._lifetime_order_floor = MAX_ORDER_ID
        else:
            self._lifetime_order_floor = max(self._lifetime_order_floor, order_id + 1)

    def _evidence_since(self, owner, cursor):
        """Immutable, owner-bound snapshot. No acknowledgement/clear/reset API."""
        with self._arrival_lock:
            if (self.__dispatch_binding is None or owner is not self.__dispatch_binding[0]
                    or type(cursor) is not int or not 0 <= cursor <= self._callback_sequence):
                raise ReadOnlyError('PAPER_CALLBACK_CURSOR_INVALID')
            # Expand exact duplicate spans into bounded immutable event pages.
            # Every sequence is explicitly processed; a fetch upper bound is
            # never an acknowledgement of unseen/coalesced events.
            events = []
            for event, end in zip(self._callback_evidence, self._callback_run_ends):
                for sequence in range(max(event.sequence, cursor + 1), end + 1):
                    events.append(replace(event, sequence=sequence))
                    if len(events) == _EVIDENCE_CAPACITY:
                        return _EvidenceBatch(self._callback_sequence, tuple(events))
            return _EvidenceBatch(self._callback_sequence, tuple(events))

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
            if spec.order_ref is not None:
                order.orderRef = spec.order_ref
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
                self._commit_callback_sequence = self._callback_sequence
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
