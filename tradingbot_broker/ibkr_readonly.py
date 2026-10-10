"""Optional official TWS SDK, read requests only; no network on module import."""

from dataclasses import dataclass, replace, fields
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from enum import Enum
import importlib
import logging
import math
import os
import socket
from threading import Condition, Thread, RLock, get_ident
from time import monotonic
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from tradingbot_backtest.market import validate_timestamp
from .models import (AccountSnapshot, BrokerEvent, Commission, EventKind, Fill,
                     OrderState, OrderType, Position, Side)
from .readonly_models import (AccountIdentity, ObservedOrder, ReadOnlyError,
                              ReadOnlySnapshot, broker_decimal)
from .ibkr_readonly_wire import guarded_connection, client_view, sdk_call


def utc_now():
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class TWSReadOnlyConfig:
    host: str = '127.0.0.1'
    port: int = 4002
    client_id: int = 1
    read_only: bool = True
    timeout_seconds: int = 10
    snapshot_ttl_seconds: int = 15
    broker_timezone: str | None = None

    def __post_init__(self):
        if self.host not in ('127.0.0.1', 'localhost', '::1'):
            raise ReadOnlyError('LOCAL_IB_GATEWAY_REQUIRED')
        if type(self.read_only) is not bool or not self.read_only:
            raise ReadOnlyError('READ_ONLY_BROKER_TRANSPORT')
        for value, maximum in ((self.port, 65535), (self.client_id, 2147483647),
                               (self.timeout_seconds, 60), (self.snapshot_ttl_seconds, 300)):
            if type(value) is not int or not 0 < value <= maximum:
                raise ReadOnlyError('INVALID_IBKR_CONFIGURATION')
        # Client ID zero has order-binding semantics; it is excluded deliberately.
        if self.broker_timezone is not None:
            try:
                ZoneInfo(self.broker_timezone)
            except (ValueError, KeyError, TypeError):
                raise ReadOnlyError('INVALID_IBKR_TIMEZONE') from None

    @classmethod
    def from_environment(cls, environ=None):
        env = os.environ if environ is None else environ
        try:
            if env.get('IBKR_READ_ONLY', 'true') != 'true':
                raise ReadOnlyError('READ_ONLY_BROKER_TRANSPORT')
            return cls(host=env.get('IBKR_HOST', '127.0.0.1'),
                       port=int(env.get('IBKR_PORT', '4002')),
                       client_id=int(env.get('IBKR_CLIENT_ID', '1')),
                       timeout_seconds=int(env.get('IBKR_TIMEOUT_SECONDS', '10')),
                       broker_timezone=env.get('IBKR_TIMEZONE') or None)
        except (TypeError, ValueError) as error:
            if isinstance(error, ReadOnlyError):
                raise
            raise ReadOnlyError('INVALID_IBKR_CONFIGURATION') from None


def _load_official_api():
    # No pip fallback and no dependency installation/download here.
    try:
        client = importlib.import_module('ibapi.client')
        return SimpleNamespace(Client=client.EClient,
            PROTOBUF_MSG_ID=getattr(client, 'PROTOBUF_MSG_ID', None),
            Wrapper=importlib.import_module('ibapi.wrapper').EWrapper,
            ExecutionFilter=importlib.import_module('ibapi.execution').ExecutionFilter,
            OUT=importlib.import_module('ibapi.message').OUT,
            Connection=importlib.import_module('ibapi.connection').Connection,
            SocketType=socket.socket,
            completed_min_version=importlib.import_module('ibapi.server_versions').MIN_SERVER_VER_COMPLETED_ORDERS)
    except (ImportError, AttributeError):
        raise ReadOnlyError('OFFICIAL_IBAPI_DEPENDENCY_UNAVAILABLE') from None
    except Exception:
        raise ReadOnlyError('SDK_IMPORT_FAILED') from None


_READ_MESSAGES = ('START_API', 'REQ_MANAGED_ACCTS', 'REQ_ACCOUNT_SUMMARY',
                  'CANCEL_ACCOUNT_SUMMARY', 'REQ_POSITIONS', 'CANCEL_POSITIONS',
                  'REQ_ALL_OPEN_ORDERS', 'REQ_EXECUTIONS', 'REQ_CURRENT_TIME',
                  'REQ_COMPLETED_ORDERS')
# Qualified official 10.50.2 contract. No other OUT name grants protobuf access.
_PROTOBUF_READ_BASES = (('START_API', 71), ('REQ_MANAGED_ACCTS', 17),
    ('REQ_ACCOUNT_SUMMARY', 62), ('CANCEL_ACCOUNT_SUMMARY', 63),
    ('REQ_POSITIONS', 61), ('CANCEL_POSITIONS', 64), ('REQ_ALL_OPEN_ORDERS', 16),
    ('REQ_EXECUTIONS', 7), ('REQ_CURRENT_TIME', 49), ('REQ_COMPLETED_ORDERS', 99))
_INFO_CODES = frozenset((2103, 2104, 2105, 2106, 2107, 2108, 2158))
_DISCONNECT_CODES = frozenset((1100, 1101, 1102, 1300, 502, 504))
_STATES = {'PendingSubmit': OrderState.SUBMITTED, 'PreSubmitted': OrderState.ACKNOWLEDGED,
           'Submitted': OrderState.WORKING, 'Filled': OrderState.FILLED,
           'Cancelled': OrderState.CANCELLED, 'ApiCancelled': OrderState.CANCELLED,
           'Inactive': OrderState.REJECTED, 'Expired': OrderState.EXPIRED}


def _read_opcodes(out):
    """Only approved SDK OUT names, with exact positive-int wire values."""
    result = {}
    for name in _READ_MESSAGES:
        if not hasattr(out, name):
            continue
        value = getattr(out, name)
        if type(value) is not int:
            if not (isinstance(out, type) and issubclass(out, Enum)
                    and isinstance(value, out) and value.name == name):
                raise ReadOnlyError('UNSUPPORTED_IBAPI_WIRE_ENCODING')
            value = value.value
        if type(value) is not int or value <= 0:
            raise ReadOnlyError('UNSUPPORTED_IBAPI_WIRE_ENCODING')
        result[name] = value
    return result


def _protobuf_read_opcodes(out, offset):
    """Qualify SDK evidence before translating the existing named read set."""
    if offset is None:
        return {}  # Older/unqualified SDK: legacy reads only, protobuf fails closed.
    if type(offset) is not int or offset != 200:
        raise ReadOnlyError('UNSUPPORTED_IBAPI_WIRE_ENCODING')
    bases = _read_opcodes(out)
    if bases != dict(_PROTOBUF_READ_BASES) or set(bases) != set(_READ_MESSAGES):
        raise ReadOnlyError('UNSUPPORTED_IBAPI_WIRE_ENCODING')
    return {name: value + offset for name, value in bases.items()}


def execution_timestamp(text, configured_zone):
    if type(text) is not str:
        raise ReadOnlyError('INVALID_EXECUTION_TIMESTAMP')
    parts = text.split()
    if len(parts) not in (2, 3):
        raise ReadOnlyError('INVALID_EXECUTION_TIMESTAMP')
    zone = parts[2] if len(parts) == 3 else configured_zone
    if zone is None:
        raise ReadOnlyError('BROKER_TIMEZONE_REQUIRED')
    try:
        local = datetime.strptime(' '.join(parts[:2]), '%Y%m%d %H:%M:%S').replace(tzinfo=ZoneInfo(zone))
        if local.utcoffset() != local.replace(fold=1).utcoffset():
            raise ReadOnlyError('AMBIGUOUS_EXECUTION_TIMESTAMP')
        result = local.astimezone(timezone.utc)
        if result.astimezone(ZoneInfo(zone)).replace(tzinfo=None) != local.replace(tzinfo=None):
            raise ReadOnlyError('INVALID_EXECUTION_TIMESTAMP')
        return result
    except (ValueError, KeyError):
        raise ReadOnlyError('INVALID_EXECUTION_TIMESTAMP') from None


def _identity(contract):
    if type(contract.conId) is not int or contract.conId <= 0:
        raise ReadOnlyError('INVALID_SECURITY_IDENTITY')
    return f'ibkr-conid-{contract.conId}'


def _order_id(order):
    if type(order.permId) is int and order.permId > 0:
        return f'ibkr-permid-{order.permId}'
    if type(order.clientId) is not int or type(order.orderId) is not int or order.orderId < 0:
        raise ReadOnlyError('INVALID_BROKER_ORDER_ID')
    return f'ibkr-client-{order.clientId}-order-{order.orderId}'


_RECONCILIATION_CAPACITY = 1024  # Combined order/execution records per collection.
_RECONCILIATION_SOURCES = ('orders', 'completed', 'executions')


class _PrivateReconciliationValue:
    __slots__ = ()

    def __repr__(self):
        return '<PrivateReadReconciliationEvidence>'


@dataclass(frozen=True, slots=True, repr=False)
class _ReconciliationPresence(_PrivateReconciliationValue):
    supplied: frozenset[str]
    order_id: bool
    client_id: bool
    perm_id: bool
    order_ref: bool
    top_order_id: bool = False
    nested_order_id: bool = False
    order_state: bool = False
    status: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class _ReconciliationOrderEvidence(_PrivateReconciliationValue):
    generation: int
    sequence: int
    source: str
    encoding: str
    order_id: int | None
    client_id: int | None
    perm_id: int | None
    order_ref: str | None
    con_id: int
    sec_type: str
    exchange: str
    symbol: str
    currency: str
    action: str
    quantity: Decimal
    order_type: str
    tif: str
    status: str | None
    presence: _ReconciliationPresence


@dataclass(frozen=True, slots=True, repr=False)
class _ReconciliationExecutionEvidence(_PrivateReconciliationValue):
    generation: int
    sequence: int
    encoding: str
    order_id: int | None
    client_id: int | None
    perm_id: int | None
    order_ref: str | None
    exec_id: str
    con_id: int
    sec_type: str
    exchange: str
    symbol: str
    currency: str
    side: str
    shares: Decimal
    price: Decimal
    cumulative_quantity: Decimal | None
    execution_time: str | None
    presence: _ReconciliationPresence


@dataclass(frozen=True, slots=True, repr=False)
class _ReconciliationCollectionState(_PrivateReconciliationValue):
    source: str
    supported: bool | None
    requested: bool
    request_id: int | None
    state: str
    coverage: str


@dataclass(frozen=True, slots=True, repr=False)
class _ReconciliationReadSnapshot(_PrivateReconciliationValue):
    generation: int
    start_marker: int
    completion_marker: int
    started_monotonic: float
    completed_monotonic: float
    last_sequence: int
    outcome: str
    collections: tuple[_ReconciliationCollectionState, ...]
    orders: tuple[_ReconciliationOrderEvidence, ...]
    executions: tuple[_ReconciliationExecutionEvidence, ...]


@dataclass(frozen=True, slots=True, repr=False)
class _ReconciliationReadRequest(_PrivateReconciliationValue):
    request_id: int
    baseline_generation: int
    baseline_start_marker: int
    issuance_marker: int
    issued_monotonic: float


@dataclass(frozen=True, slots=True, repr=False)
class _ReconciliationReadReceipt(_PrivateReconciliationValue):
    request_id: int
    baseline_generation: int
    baseline_start_marker: int
    issuance_marker: int
    issued_monotonic: float
    generation: int
    start_marker: int
    started_monotonic: float
    completion_marker: int
    completed_monotonic: float
    private_snapshot: _ReconciliationReadSnapshot
    public_snapshot: ReadOnlySnapshot | None
    outcome: str


def _reconciliation_clock(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ReadOnlyError('INVALID_RECONCILIATION_READ_CLOCK')
    return value


@dataclass(frozen=True, slots=True, repr=False)
class _ReconciliationProtoEnvelope(_PrivateReconciliationValue):
    generation: int
    source: str
    thread_id: int
    values: tuple
    supplied: frozenset[str]


def _reconciliation_text(value, *, reference=False):
    if type(value) is not str or len(value) > 100:
        raise ValueError()
    if not value.isascii() or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError()
    if not value and not reference:
        raise ValueError()
    return value or None


def _reconciliation_integer(value, *, positive=False, wide=False):
    maximum = 2**63 - 1 if wide else 2_147_483_647
    if type(value) is not int or not (1 if positive else 0) <= value <= maximum:
        raise ValueError()
    return value


def _reconciliation_amount(value, *, positive=False, price=False):
    if not price and type(value) not in (Decimal, str, int):
        raise ValueError()
    value = broker_decimal(value)
    parts = value.as_tuple()
    if (value < 0 or positive and value == 0 or len(parts.digits) > 64
            or not -64 <= parts.exponent <= 64):
        raise ValueError()
    return value


def _reconciliation_field(name, value):
    if name in ('order_id', 'top_order_id', 'client_id'):
        return _reconciliation_integer(value)
    if name == 'perm_id':
        return _reconciliation_integer(value, wide=True) or None
    if name == 'con_id':
        return _reconciliation_integer(value, positive=True, wide=True)
    if name in ('quantity', 'shares'):
        return _reconciliation_amount(value, positive=True)
    if name == 'cumulative_quantity':
        return _reconciliation_amount(value)
    if name == 'price':
        return _reconciliation_amount(value, price=True)
    return _reconciliation_text(value, reference=name == 'order_ref')


_RECONCILIATION_CONTRACT_FIELDS = (('con_id', 'conId'), ('sec_type', 'secType'),
    ('exchange', 'exchange'), ('symbol', 'symbol'), ('currency', 'currency'))
_RECONCILIATION_ID_FIELDS = (('order_id', 'orderId'), ('client_id', 'clientId'),
    ('perm_id', 'permId'), ('order_ref', 'orderRef'))
_RECONCILIATION_ORDER_FIELDS = (('action', 'action'), ('quantity', 'totalQuantity'),
    ('order_type', 'orderType'), ('tif', 'tif'))
_RECONCILIATION_EXECUTION_FIELDS = (('exec_id', 'execId'), ('side', 'side'),
    ('shares', 'shares'), ('price', 'price'), ('cumulative_quantity', 'cumQty'),
    ('execution_time', 'time'))


def _reconciliation_has_field(raw, name):
    present = raw.HasField(name)
    if type(present) is not bool:
        raise ValueError()
    return present


def _reconciliation_proto_copy(generation, source, raw):
    values, supplied = {}, set()
    payload_name = 'execution' if source == 'executions' else 'order'
    for group, specification in (
        ('contract', _RECONCILIATION_CONTRACT_FIELDS),
        (payload_name, _RECONCILIATION_ID_FIELDS + (
            _RECONCILIATION_EXECUTION_FIELDS if source == 'executions'
            else _RECONCILIATION_ORDER_FIELDS))):
        if not _reconciliation_has_field(raw, group):
            raise ValueError()
        payload = getattr(raw, group)
        for field, sdk_name in specification:
            if _reconciliation_has_field(payload, sdk_name):
                values[field] = _reconciliation_field(field, getattr(payload, sdk_name))
                supplied.add(field)
    if source == 'orders' and _reconciliation_has_field(raw, 'orderId'):
        values['top_order_id'] = _reconciliation_field('top_order_id', raw.orderId)
        supplied.add('top_order_id')
        if 'order_id' in supplied and values['order_id'] != values['top_order_id']:
            raise ValueError()
    if source != 'executions':
        # Both order sources require lifecycle structure. Open orders may omit
        # status explicitly; completed-order lifecycle evidence may not.
        if not _reconciliation_has_field(raw, 'orderState'):
            raise ValueError()
        supplied.add('order_state')
        lifecycle = raw.orderState
        if _reconciliation_has_field(lifecycle, 'status'):
            status = lifecycle.status
            _reconciliation_text(status, reference=True)  # Validate without normalizing.
            if source == 'completed' and not status:
                raise ValueError()
            values['status'] = status
            supplied.add('status')
        elif source == 'completed':
            raise ValueError()
    required = {n for n, _ in _RECONCILIATION_CONTRACT_FIELDS}
    required.update(('exec_id', 'side', 'shares', 'price') if source == 'executions'
                    else (n for n, _ in _RECONCILIATION_ORDER_FIELDS))
    if not required <= supplied:
        raise ValueError()
    return _ReconciliationProtoEnvelope(generation, source, get_ident(),
        tuple(sorted(values.items())), frozenset(supplied))


def _reconciliation_decoded_copy(generation, sequence, source, contract, payload,
                                  state, callback_order_id, envelope):
    specification = _RECONCILIATION_CONTRACT_FIELDS + (
        _RECONCILIATION_EXECUTION_FIELDS if source == 'executions'
        else _RECONCILIATION_ORDER_FIELDS)
    # Contract fields live on Contract, not the SDK Order/Execution object.
    contract_names = {n for n, _ in _RECONCILIATION_CONTRACT_FIELDS}
    values, supplied = {}, set()
    raw_values = {} if envelope is None else dict(envelope.values)
    for field, sdk_name in specification + _RECONCILIATION_ID_FIELDS:
        if source == 'completed' and envelope is None and field in ('order_id', 'client_id'):
            continue  # Legacy decoder does NOT supply these IDs. Never inspect defaults.
        if envelope is not None and field not in envelope.supplied:
            continue  # Never read absent protobuf defaults as supplied identity.
        obj = contract if field in contract_names else payload
        value = getattr(obj, sdk_name, None)
        if value is None and envelope is None and field in (
                'order_ref', 'cumulative_quantity', 'execution_time'):
            continue
        values[field] = _reconciliation_field(field, value)
        supplied.add(field)
        if envelope is not None and values[field] != raw_values[field]:
            raise ValueError()
    if source == 'orders':
        if envelope is None:
            if _reconciliation_integer(callback_order_id) != values['order_id']:
                raise ValueError()
        elif 'top_order_id' in envelope.supplied:
            top = raw_values['top_order_id']
            if (_reconciliation_integer(callback_order_id) != top
                    or _reconciliation_integer(payload.orderId) != top):
                raise ValueError()
            values['order_id'] = top
    if envelope is not None:
        supplied = set(envelope.supplied)
    status = None
    if source != 'executions':
        if envelope is None:
            status = getattr(state, 'status', '')
            if type(status) is not str:
                raise ValueError()
            if state is not None:
                supplied.update(('order_state', 'status'))  # Legacy decoder contract.
        elif 'status' in envelope.supplied:
            status = getattr(state, 'status', None)
            if type(status) is not str or status != raw_values['status']:
                raise ValueError()
        # Absent protobuf status never reads or promotes a decoded default.
        if status is not None:
            _reconciliation_text(status, reference=True)
            status = status if status in _STATES else 'UNKNOWN'
    presence = _ReconciliationPresence(frozenset(supplied),
        'order_id' in supplied or 'top_order_id' in supplied, 'client_id' in supplied,
        'perm_id' in supplied, 'order_ref' in supplied, 'top_order_id' in supplied,
        envelope is not None and 'order_id' in supplied,
        'order_state' in supplied, 'status' in supplied)
    identity = tuple(values.get(n) for n in ('order_id', 'client_id', 'perm_id', 'order_ref'))
    common = tuple(values[n] for n, _ in _RECONCILIATION_CONTRACT_FIELDS)
    encoding = 'LEGACY' if envelope is None else 'PROTOBUF'
    if source == 'executions':
        return _ReconciliationExecutionEvidence(generation, sequence, encoding,
            *identity, values['exec_id'], *common, values['side'], values['shares'],
            values['price'], values.get('cumulative_quantity'), values.get('execution_time'), presence)
    return _ReconciliationOrderEvidence(generation, sequence,
        'OPEN_ORDER' if source == 'orders' else 'COMPLETED_ORDER', encoding,
        *identity, *common, *(values[n] for n, _ in _RECONCILIATION_ORDER_FIELDS), status, presence)


def _make_client(api, owner, generation):
    read_opcodes = _read_opcodes(api.OUT)
    allowed = frozenset(read_opcodes.values())
    protobuf_allowed = frozenset(_protobuf_read_opcodes(
        api.OUT, getattr(api, 'PROTOBUF_MSG_ID', None)).values())
    # SDK logs may contain account identifiers/raw payloads. No propagation to app logs.
    for name in ('ibapi', *tuple(logging.Logger.manager.loggerDict)):
        if name == 'ibapi' or name.startswith('ibapi.'):
            sdk_log = logging.getLogger(name)
            sdk_log.handlers = [logging.NullHandler()]
            sdk_log.propagate = False
            sdk_log.disabled = True

    class Wrapper(api.Wrapper):
        def logAnswer(self, *args, **kwargs): pass  # SDK payloads are not application audits.
        def nextValidId(self, orderId): owner._callback(generation, 'ready')
        def managedAccounts(self, accountsList): owner._callback(generation, 'accounts', accountsList)
        def accountSummary(self, reqId, account, tag, value, currency):
            owner._callback(generation, 'summary', reqId, account, tag, value, currency)
        def accountSummaryEnd(self, reqId): owner._callback(generation, 'summary_end', reqId)
        def position(self, account, contract, pos, avgCost):
            owner._callback(generation, 'position', account, contract, pos, avgCost)
        def positionEnd(self): owner._callback(generation, 'positions_end')
        def orderStatus(self, orderId, status, filled, remaining, avgFillPrice,
                        permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
            owner._reconciliation_intervening(generation)
            inherited = getattr(super(), 'orderStatus', None)
            if inherited is not None:
                return inherited(orderId, status, filled, remaining, avgFillPrice,
                    permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice)
        def openOrderProtoBuf(self, raw): owner._reconciliation_proto(generation, 'orders', raw)
        def completedOrderProtoBuf(self, raw): owner._reconciliation_proto(generation, 'completed', raw)
        def executionDetailsProtoBuf(self, raw): owner._reconciliation_proto(generation, 'executions', raw)
        def openOrder(self, orderId, contract, order, orderState):
            owner._reconciliation_decoded(generation, 'orders', contract, order, orderState, orderId)
            owner._callback(generation, 'order', contract, order, orderState, False)
        def openOrderEnd(self): owner._callback(generation, 'orders_end')
        def completedOrder(self, contract, order, orderState):
            owner._reconciliation_decoded(generation, 'completed', contract, order, orderState)
            owner._callback(generation, 'order', contract, order, orderState, True)
        def completedOrdersEnd(self): owner._callback(generation, 'completed_end')
        def execDetails(self, reqId, contract, execution):
            owner._reconciliation_decoded(generation, 'executions', contract, execution, request_id=reqId)
            owner._callback(generation, 'execution', reqId, contract, execution)
        def execDetailsEnd(self, reqId): owner._callback(generation, 'executions_end', reqId)
        def commissionReport(self, commissionReport): owner._callback(generation, 'commission', commissionReport)
        def commissionAndFeesReport(self, report): owner._callback(generation, 'commission', report)
        def currentTime(self, time): owner._callback(generation, 'time', time)
        def connectionClosed(self): owner._callback(generation, 'closed')
        def error(self, *args):
            # Official SDK versions vary: reqId,[errorTime],errorCode,errorString,...
            code = args[2] if len(args) >= 3 and type(args[2]) is int else args[1] if len(args) >= 2 else None
            owner._callback(generation, 'error', code)

    connection = None

    class GuardedClient(api.Client):
        @property
        def conn(self): return connection
        @conn.setter
        def conn(self, value):
            nonlocal connection
            connection = None if value is None else guarded_connection(value, api, allowed, protobuf_allowed)
        def logRequest(self, *args, **kwargs): pass
        def sendMsgProtoBuf(self, msgId, msg):
            if (type(msgId) is not int or msgId <= 0 or msgId not in protobuf_allowed
                    or type(msg) is not bytes):
                raise ReadOnlyError('UNSUPPORTED_IBAPI_WIRE_ENCODING')
            return super().sendMsgProtoBuf(msgId, msg)
        def sendMsg(self, msgId, *payload):
            if payload:
                if len(payload) != 1 or type(payload[0]) is not str:
                    raise ReadOnlyError('READ_ONLY_BROKER_TRANSPORT')
                if type(msgId) is int:
                    opcode = msgId
                elif (isinstance(api.OUT, type) and issubclass(api.OUT, Enum)
                      and isinstance(msgId, api.OUT) and msgId.name in read_opcodes
                      and getattr(api.OUT, msgId.name) is msgId):
                    opcode = msgId.value
                else:
                    raise ReadOnlyError('READ_ONLY_BROKER_TRANSPORT')
                if type(opcode) is not int or opcode <= 0 or opcode not in allowed:
                    raise ReadOnlyError('READ_ONLY_BROKER_TRANSPORT')
                # Preserve the SDK's original enum/int argument for its own framing.
                return super().sendMsg(msgId, payload[0])

            # Older SDK/test doubles carry the opcode inside one string message.
            message = msgId
            try:
                if type(message) is not str:
                    raise ValueError
                opcode = int(message.split('\0', 1)[0])
                if opcode not in allowed:
                    raise ValueError
            except (ValueError, TypeError):
                raise ReadOnlyError('READ_ONLY_BROKER_TRANSPORT') from None
            return super().sendMsg(message)

        def placeOrder(self, *args, **kwargs): raise ReadOnlyError('READ_ONLY_BROKER_TRANSPORT')
        def cancelOrder(self, *args, **kwargs): raise ReadOnlyError('READ_ONLY_BROKER_TRANSPORT')
        def reqGlobalCancel(self, *args, **kwargs): raise ReadOnlyError('READ_ONLY_BROKER_TRANSPORT')

    return client_view(GuardedClient(Wrapper()))


class ReadOnlyTWSTransport:
    """SDK held privately; only named read requests and disconnect are dispatched."""
    def __init__(self, config: TWSReadOnlyConfig, *, clock=utc_now, enrollment_store=None):
        if not isinstance(config, TWSReadOnlyConfig):
            raise ReadOnlyError('INVALID_IBKR_CONFIGURATION')
        self.config = config
        from .paper_enrollment import PaperEnrollmentStore
        if enrollment_store is not None and type(enrollment_store) is not PaperEnrollmentStore:
            raise ReadOnlyError('INVALID_PAPER_ENROLLMENT_STORE')
        self._enrollment_store = enrollment_store
        api = sdk_call('SDK_INITIALIZATION_FAILED', _load_official_api)
        # Metadata only: no raw SDK client/connection class is exposed by transport.
        def metadata():
            return SimpleNamespace(OUT=SimpleNamespace(**_read_opcodes(api.OUT)),
                PROTOBUF_OUT=SimpleNamespace(**_protobuf_read_opcodes(
                    api.OUT, getattr(api, 'PROTOBUF_MSG_ID', None))),
                completed_min_version=api.completed_min_version)
        self._api = sdk_call('SDK_INITIALIZATION_FAILED', metadata)
        self._create_client = lambda generation: sdk_call('SDK_CLIENT_CONSTRUCTION_FAILED', _make_client, api, self, generation)
        self._execution_filter = lambda: sdk_call('SDK_EXECUTION_FILTER_FAILED', api.ExecutionFilter)
        self._clock = clock
        self._condition = Condition()
        self._lifecycle = RLock()
        self._client = None
        self._thread = None
        self._generation = 0
        self._request_sequence = 100
        self._events = []
        self._event_sequence = 0
        self._snapshot = None
        self._diagnostics = []
        self._reconciliation_sequence = 0  # Transport lifetime, never reset.
        self._reconciliation_marker = 0
        self._reconciliation_result = None
        self._reconciliation_failure = None
        self._reconciliation_read_sequence = 0
        self._reconciliation_read_request = None
        self._reconciliation_read_binding = None
        self._reconciliation_read_receipt = None
        self._reset()

    def _reset(self):
        self._disconnect_emitted = False
        self._ready = False
        self._accounts = ()
        self._summary = {}
        self._positions = {}
        self._orders_read = {}
        self._completed_read = {}
        self._fills_read = {}
        self._commissions_read = {}
        self._done = set()
        self._failure = None
        self._broker_time = None
        self._last_observed = None
        self._collecting = True
        self._phase = {name: 'NOT_REQUESTED' for name in
                       ('accounts', 'summary', 'positions', 'orders', 'completed', 'executions', 'time')}
        self._reconciliation_begin()

    def _reconciliation_begin(self):
        if hasattr(self, '_reconciliation_collections') and self._reconciliation_result is None:
            self._reconciliation_fail('GENERATION_LOST')
        self._reconciliation_marker += 1
        self._reconciliation_start_marker = self._reconciliation_marker
        self._reconciliation_started = monotonic()
        self._reconciliation_generation = self._generation
        self._reconciliation_result = None
        self._reconciliation_failure = None
        self._reconciliation_envelope = None  # Capacity ONE across all callback types.
        self._reconciliation_encodings = {}
        self._reconciliation_publication = None
        self._reconciliation_orders = []
        self._reconciliation_executions = {}
        self._reconciliation_collections = {
            name: _ReconciliationCollectionState(name, None if name == 'completed' else True,
                False, None, 'NOT_REQUESTED',
                'CURRENT_OPEN_ORDERS' if name == 'orders' else 'HISTORY_LIMITED')
            for name in _RECONCILIATION_SOURCES}
        request = self._reconciliation_read_request
        if (request is not None and self._reconciliation_read_binding is None
                and self._generation > request.baseline_generation
                and self._reconciliation_start_marker > request.issuance_marker):
            # First later READ collection only; disconnect alone never binds.
            self._reconciliation_read_binding = (
                self._reconciliation_generation, self._reconciliation_start_marker)
            try:
                started = _reconciliation_clock(self._reconciliation_started)
                if started < request.issued_monotonic:
                    raise ReadOnlyError('INVALID_RECONCILIATION_READ_CLOCK')
            except Exception:
                self._reconciliation_fail('FAILED')

    def _reconciliation_finish(self, outcome, *, public_snapshot=None):
        if self._reconciliation_result is not None:
            return
        if self._reconciliation_failure is not None and outcome != self._reconciliation_failure:
            return
        if outcome == 'COLLECTED' and any(
                state.state not in ('COMPLETED', 'UNAVAILABLE')
                for state in self._reconciliation_collections.values()):
            self._reconciliation_fail('FAILED')
            return
        if self._reconciliation_envelope is not None:
            self._reconciliation_fail('MALFORMED', self._reconciliation_envelope.source)
            return
        self._reconciliation_marker += 1
        try:
            snapshot = _ReconciliationReadSnapshot(
                self._reconciliation_generation, self._reconciliation_start_marker,
                self._reconciliation_marker, self._reconciliation_started, monotonic(),
                self._reconciliation_sequence, outcome,
                tuple(self._reconciliation_collections[n] for n in _RECONCILIATION_SOURCES),
                tuple(self._reconciliation_orders), tuple(self._reconciliation_executions.values()))
            receipt = self._reconciliation_receipt_for(snapshot, public_snapshot)
        except BaseException as error:
            # Publication is transactional. Never leave COLLECTED authority after
            # an interrupted/failed snapshot or receipt constructor.
            if self._reconciliation_failure is None:
                self._reconciliation_failure = 'FAILED' if isinstance(error, Exception) else 'INTERRUPTED'
            self._reconciliation_envelope = None
            for name, state in self._reconciliation_collections.items():
                if state.state in ('ACTIVE', 'COMPLETED'):
                    self._reconciliation_collections[name] = replace(
                        state, state=self._reconciliation_failure)
            raise
        self._reconciliation_result = snapshot
        if receipt is not None:
            self._reconciliation_read_receipt = receipt

    def _prepare_reconciliation_read_request(self):
        """Arm one future capture. No SDK call, refresh or safety-state mutation."""
        with self._condition:
            if type(self) is not ReadOnlyTWSTransport:
                raise ReadOnlyError('EXACT_RECONCILIATION_READ_TRANSPORT_REQUIRED')
            if self._reconciliation_read_request is not None:
                raise ReadOnlyError('RECONCILIATION_READ_REQUEST_OUTSTANDING')
            try:
                issued = _reconciliation_clock(monotonic())
                previous = (self._reconciliation_result.completed_monotonic
                    if self._reconciliation_result is not None else self._reconciliation_started)
                if issued < _reconciliation_clock(previous):
                    raise ReadOnlyError('INVALID_RECONCILIATION_READ_CLOCK')
                request = _ReconciliationReadRequest(self._reconciliation_read_sequence + 1,
                    self._generation, self._reconciliation_start_marker,
                    self._reconciliation_marker + 1, issued)
            except ReadOnlyError:
                raise
            except Exception:
                raise ReadOnlyError('RECONCILIATION_READ_REQUEST_UNAVAILABLE') from None
            # All fallible work precedes publication; interrupted construction
            # leaves neither a half-issued request nor a replayable authority.
            self._reconciliation_read_sequence = request.request_id
            self._reconciliation_marker = request.issuance_marker
            self._reconciliation_read_request = request
            return request

    def _reconciliation_receipt_for(self, snapshot, public_snapshot):
        request = self._reconciliation_read_request
        if (request is None or self._reconciliation_read_receipt is not None
                or self._reconciliation_read_binding != (snapshot.generation, snapshot.start_marker)):
            return None
        started = _reconciliation_clock(snapshot.started_monotonic)
        completed = _reconciliation_clock(snapshot.completed_monotonic)
        if snapshot.outcome == 'COLLECTED':
            publication = self._reconciliation_publication
            if (type(public_snapshot) is not ReadOnlySnapshot
                    or public_snapshot is not self._snapshot
                    or publication is None
                    or publication[:2] != (snapshot.generation, snapshot.start_marker)
                    or publication[2] is not public_snapshot
                    or snapshot.generation != self._generation
                    or not request.issued_monotonic <= started <= completed):
                raise ReadOnlyError('RECONCILIATION_READ_PUBLICATION_UNPROVEN')
        else:
            public_snapshot = None
        return _ReconciliationReadReceipt(request.request_id, request.baseline_generation,
            request.baseline_start_marker, request.issuance_marker, request.issued_monotonic,
            snapshot.generation, snapshot.start_marker, started, snapshot.completion_marker,
            completed, snapshot, public_snapshot, snapshot.outcome)

    def _consume_reconciliation_read_receipt(self, request):
        with self._condition:
            receipt = self._peek_reconciliation_read_receipt(request)
            self._reconciliation_read_request = None
            self._reconciliation_read_binding = None
            self._reconciliation_read_receipt = None
            return receipt

    def _peek_reconciliation_read_receipt(self, request):
        # Internal transactional preparation; the caller holds the read condition.
        if not self._condition._is_owned():
            raise ReadOnlyError('RECONCILIATION_READ_LOCK_REQUIRED')
        if (type(self) is not ReadOnlyTWSTransport
                or type(request) is not _ReconciliationReadRequest
                or request is not self._reconciliation_read_request):
            raise ReadOnlyError('INVALID_RECONCILIATION_READ_REQUEST')
        if self._reconciliation_read_receipt is None:
            raise ReadOnlyError('RECONCILIATION_READ_INCOMPLETE')
        return self._reconciliation_read_receipt

    def _reconciliation_fail(self, outcome, source=None):
        # Caller holds condition. Safety committed before constructing/reporting a result.
        if self._reconciliation_result is not None or self._reconciliation_failure is not None:
            return
        self._reconciliation_failure = outcome
        self._reconciliation_envelope = None
        for name, state in self._reconciliation_collections.items():
            if name == source or state.state == 'ACTIVE':
                self._reconciliation_collections[name] = replace(state, state=outcome)
        try:
            self._reconciliation_finish(outcome)
        except Exception:
            # Reporting failure cannot undo the latch or replace an interruption.
            # Private snapshot access stays unavailable rather than claiming success.
            pass

    def _reconciliation_snapshot(self):
        """Private immutable terminal capture only. Never connect, refresh or clear."""
        with self._condition:
            if self._reconciliation_result is None:
                raise ReadOnlyError('RECONCILIATION_READ_INCOMPLETE')
            return self._reconciliation_result

    def _reconciliation_intervening(self, generation):
        """Boundary only: no status arguments, evidence or earlier-plane locks."""
        with self._condition:
            if type(generation) is not int or generation != self._generation:
                return
            if self._reconciliation_envelope is not None:
                self._reconciliation_fail('MALFORMED', self._reconciliation_envelope.source)

    def _reconciliation_request(self, name, request_id=None):
        if (name not in _RECONCILIATION_SOURCES or self._reconciliation_result is not None
                or self._reconciliation_failure is not None):
            return
        prior = self._reconciliation_collections[name]
        if name == 'executions':
            try:
                _reconciliation_integer(request_id)
            except Exception:
                self._reconciliation_fail('MALFORMED', name)
                return
        self._reconciliation_collections[name] = replace(prior, supported=True,
            requested=True, request_id=request_id, state='ACTIVE')

    def _reconciliation_complete(self, name):
        if self._reconciliation_result is not None or self._reconciliation_failure is not None:
            return
        if self._reconciliation_envelope is not None:
            self._reconciliation_fail('MALFORMED', self._reconciliation_envelope.source)
            return
        if name not in _RECONCILIATION_SOURCES:
            return
        state = self._reconciliation_collections[name]
        if state.state == 'ACTIVE':
            self._reconciliation_collections[name] = replace(state, state='COMPLETED')

    def _reconciliation_proto(self, generation, source, raw):
        with self._condition:
            if (type(generation) is not int or generation != self._generation
                    or not self._collecting or self._reconciliation_result is not None
                    or self._reconciliation_failure is not None):
                return  # Do not touch stale raw objects.
            try:
                if (source not in _RECONCILIATION_SOURCES or self._phase[source] != 'ACTIVE'
                        or self._reconciliation_envelope is not None
                        or self._reconciliation_encodings.get(source) == 'LEGACY'):
                    raise ValueError()
                envelope = _reconciliation_proto_copy(generation, source, raw)
                self._reconciliation_encodings[source] = 'PROTOBUF'
                self._reconciliation_envelope = envelope
            except BaseException as error:
                self._reconciliation_fail('MALFORMED' if isinstance(error, Exception) else 'INTERRUPTED', source)
                if not isinstance(error, Exception):
                    raise

    def _reconciliation_decoded(self, generation, source, contract, payload,
                                state=None, callback_order_id=None, request_id=None):
        with self._condition:
            if (type(generation) is not int or generation != self._generation
                    or not self._collecting or self._reconciliation_result is not None
                    or self._reconciliation_failure is not None):
                return
            try:
                envelope = self._reconciliation_envelope
                if source not in _RECONCILIATION_SOURCES or self._phase[source] != 'ACTIVE':
                    if envelope is not None:
                        raise ValueError()
                    return
                if envelope is not None:
                    if (envelope.generation != generation or envelope.source != source
                            or envelope.thread_id != get_ident()):
                        raise ValueError()
                    self._reconciliation_envelope = None  # One-shot, even on later failure.
                elif self._reconciliation_encodings.get(source) == 'PROTOBUF':
                    raise ValueError()  # Replayed decoded callback is NOT legacy proof.
                else:
                    self._reconciliation_encodings[source] = 'LEGACY'
                if source == 'executions':
                    if type(request_id) is not int or request_id != self._execution_id:
                        if envelope is not None:
                            raise ValueError()
                        return
                    account = payload.acctNumber
                else:
                    account = payload.account
                if type(account) is not str:
                    raise ValueError()
                if account not in self._accounts:
                    return  # Existing private account filtering; never retain the identifier.
                event = _reconciliation_decoded_copy(generation, self._reconciliation_sequence + 1,
                    source, contract, payload, state, callback_order_id, envelope)
                if source == 'executions':
                    prior = self._reconciliation_executions.get(event.exec_id)
                    if prior is not None:
                        if any(getattr(prior, f.name) != getattr(event, f.name)
                               for f in fields(event) if f.name != 'sequence'):
                            raise ValueError()
                        self._reconciliation_sequence += 1
                        return
                if (len(self._reconciliation_orders) + len(self._reconciliation_executions)
                        >= _RECONCILIATION_CAPACITY):
                    self._reconciliation_fail('OVERFLOW', source)
                    return
                self._reconciliation_sequence += 1
                if source == 'executions':
                    self._reconciliation_executions[event.exec_id] = event
                else:
                    self._reconciliation_orders.append(event)
            except BaseException as error:
                self._reconciliation_fail('MALFORMED' if isinstance(error, Exception) else 'INTERRUPTED', source)
                if not isinstance(error, Exception):
                    raise

    @property
    def connected(self):
        with self._condition:
            try:
                return self._client is not None and self._client.isConnected() and self._failure is None
            except ReadOnlyError as error:
                self._failure = error.code
                self._diagnostics.append(('SDK_FAILURE', error.code))
                raise

    @property
    def diagnostics(self):
        with self._condition:
            return tuple(self._diagnostics)

    def _enrollment_identity(self):
        # Called under lifecycle AND callback locks; identity never leaves this boundary.
        if (not self.connected or not self._ready or self._snapshot is None
                or self._collecting or len(self._accounts) != 1):
            raise ReadOnlyError('PAPER_ENROLLMENT_CONNECTION_UNUSABLE')
        now = self._clock()
        validate_timestamp(now)
        if not self._snapshot.account.available_at <= now < self._snapshot.account.valid_until:
            raise ReadOnlyError('PAPER_ENROLLMENT_SNAPSHOT_STALE')
        if not self._snapshot.account.verified or self._snapshot.account.mode is not None:
            raise ReadOnlyError('PAPER_ENROLLMENT_CONNECTION_UNUSABLE')
        return self._accounts[0], now

    def _local_enrollment_store(self):
        from .paper_enrollment import PaperEnrollmentStore
        return self._enrollment_store if self._enrollment_store is not None else PaperEnrollmentStore()

    def _paper_execution_binding(self):
        # Private authenticated revision evidence; never an account-mode assertion.
        import hmac
        from .paper_enrollment import _fingerprint
        with self._lifecycle, self._condition:
            if type(self.config) is not TWSReadOnlyConfig:
                raise ReadOnlyError('INVALID_IBKR_CONFIGURATION')
            self.config.__post_init__()
            account, _ = self._enrollment_identity()
            record = self._local_enrollment_store()._read_record()
            if record is None:
                raise ReadOnlyError('PAPER_EXECUTION_ENROLLMENT_UNENROLLED')
            if not hmac.compare_digest(_fingerprint(record['salt'], account), record['fingerprint']):
                raise ReadOnlyError('PAPER_EXECUTION_ENROLLMENT_MISMATCH')
            return self._generation, record['authentication'], self._snapshot.account.valid_until

    def _current_sdk_connection_evidence(self, generation):
        # EClient.isConnected is a local state inspection, not a broker request.
        # Exact bool only; None means unavailable. Never return an SDK object/error.
        with self._lifecycle, self._condition:
            client = self._client
            if generation != self._generation or client is None:
                return None
            if self._failure is not None or self._disconnect_emitted:
                return False
            try:
                connected = client.isConnected()
            except Exception:
                return None
            if (type(connected) is not bool or generation != self._generation
                    or client is not self._client):
                return None
            return connected

    def _validate_paper_execution_observation(self, generation, at):
        # Final cheap check under the same lifecycle/callback locks. No SDK or file read.
        self.config.__post_init__()
        if (generation != self._generation or self._client is None or self._failure is not None
                or self._disconnect_emitted or not self._ready or self._snapshot is None
                or self._collecting or len(self._accounts) != 1):
            raise ReadOnlyError('PAPER_EXECUTION_CONNECTION_UNUSABLE')
        account = self._snapshot.account
        if (not account.verified or account.mode is not None
                or not account.available_at <= at < account.valid_until):
            raise ReadOnlyError('PAPER_EXECUTION_ACCOUNT_STALE')

    @property
    def paper_enrollment_status(self):
        from .paper_enrollment import PaperEnrollmentStatus
        with self._lifecycle, self._condition:
            try:
                account, _ = self._enrollment_identity()
                return self._local_enrollment_store()._match_account(account)
            except Exception:
                return PaperEnrollmentStatus.INVALID

    def enroll_paper_account(self, confirmation, *, replace_existing=False):
        from .paper_enrollment import require_confirmation
        require_confirmation(confirmation)
        with self._lifecycle, self._condition:
            account, now = self._enrollment_identity()
            store = self._local_enrollment_store()
            store._enroll_account(account, now, confirmation, replace_existing=replace_existing)
            return store._match_account(account)

    def _put(self, table, key, value):
        if key in table and table[key] != value:
            raise ReadOnlyError('CONFLICTING_BROKER_CALLBACK')
        table[key] = value

    def _callback(self, generation, kind, *args):
        with self._condition:
            if generation != self._generation:
                return  # Obsolete socket generation can never restore this session.
            if self._reconciliation_envelope is not None:
                # Qualified decoder pairing is immediate. An intervening callback
                # cannot leave an envelope available for a later, unrelated event.
                self._reconciliation_fail('MALFORMED', self._reconciliation_envelope.source)
            now = self._clock()
            validate_timestamp(now)
            try:
                if kind == 'closed' or (kind == 'error' and args[0] in _DISCONNECT_CODES):
                    self._reconciliation_fail('FAILED')
                    if self._disconnect_emitted:
                        return
                    self._disconnect_emitted = True
                    self._failure = 'IB_GATEWAY_DISCONNECTED'
                    self._event_sequence += 1
                    event = BrokerEvent(f'tws-disconnect-{generation}-{self._event_sequence}',
                                        EventKind.DISCONNECTED, now, now, error_code=args[0] if kind == 'error' else None)
                    self._events.append(event)
                elif kind == 'error':
                    code = args[0]
                    if type(code) is not int:
                        raise ReadOnlyError('INVALID_BROKER_ERROR')
                    self._diagnostics.append(('INFORMATIONAL' if code in _INFO_CODES else 'BROKER_ERROR', code))
                    if code not in _INFO_CODES:
                        self._failure = 'IB_GATEWAY_REQUEST_FAILED'
                        self._reconciliation_fail('FAILED')
                elif not self._collecting:
                    return
                elif kind not in ('ready', 'commission') and not self._active_callback(kind, args):
                    return
                elif kind == 'ready': self._ready = True
                elif kind == 'accounts':
                    accounts = tuple(sorted(set(v.strip() for v in args[0].split(',') if v.strip())))
                    if self._accounts and self._accounts != accounts:
                        raise ReadOnlyError('ACCOUNT_IDENTITY_CONFLICT')
                    self._accounts = accounts
                    self._complete('accounts')
                elif kind == 'summary':
                    req, account, tag, value, currency = args
                    if req != self._summary_id or account not in self._accounts:
                        return
                    self._put(self._summary, (tag, currency), broker_decimal(value) if tag != 'AccountType' else 'REPORTED')
                elif kind == 'summary_end':
                    if args[0] == self._summary_id: self._complete('summary')
                elif kind == 'position':
                    account, contract, qty, cost = args
                    if account not in self._accounts: return
                    qty = broker_decimal(qty)
                    if qty:
                        p = Position(_identity(contract), contract.symbol, qty, broker_decimal(cost), contract.currency)
                        self._put(self._positions, p.security_id, p)
                elif kind == 'positions_end': self._complete('positions')
                elif kind == 'order':
                    contract, order, state, completed = args
                    if order.account not in self._accounts: return
                    kind_value = {'MKT': OrderType.MARKET, 'LMT': OrderType.LIMIT, 'STP': OrderType.STOP}.get(order.orderType)
                    item = ObservedOrder(_order_id(order), _identity(contract), contract.symbol, Side(order.action),
                        kind_value, broker_decimal(order.totalQuantity), contract.currency,
                        _STATES.get(state.status, OrderState.UNKNOWN), now,
                        broker_decimal(order.lmtPrice) if kind_value == OrderType.LIMIT else None,
                        broker_decimal(order.auxPrice) if kind_value == OrderType.STOP else None)
                    table = self._completed_read if completed else self._orders_read
                    prior = table.get(item.broker_order_id)
                    if prior is not None: item = replace(item, available_at=prior.available_at)
                    self._put(table, item.broker_order_id, item)
                elif kind == 'orders_end': self._complete('orders')
                elif kind == 'completed_end': self._complete('completed')
                elif kind == 'execution':
                    req, contract, execution = args
                    if req != self._execution_id or execution.acctNumber not in self._accounts: return
                    stamp = execution_timestamp(execution.time, self.config.broker_timezone)
                    if stamp > now: raise ReadOnlyError('FUTURE_EXECUTION_TIMESTAMP')
                    fill = Fill(execution.execId, _order_id(execution), _identity(contract), contract.symbol,
                        {'BOT': Side.BUY, 'SLD': Side.SELL}[execution.side], broker_decimal(execution.shares),
                        broker_decimal(execution.price), contract.currency, stamp)
                    self._put(self._fills_read, fill.fill_id, fill)
                elif kind == 'executions_end':
                    if type(args[0]) is int and args[0] == self._execution_id:
                        self._complete('executions')
                elif kind == 'commission':
                    report = args[0]
                    value = getattr(report, 'commission', None)
                    if value is None: value = report.commissionAndFees
                    item = Commission(f'commission-{report.execId}', report.execId, broker_decimal(value), report.currency, now)
                    prior = self._commissions_read.get(item.fill_id)
                    if prior is not None: item = replace(item, timestamp=prior.timestamp)
                    self._put(self._commissions_read, item.fill_id, item)
                elif kind == 'time':
                    if type(args[0]) is not int: raise ReadOnlyError('INVALID_BROKER_TIMESTAMP')
                    self._broker_time = datetime.fromtimestamp(args[0], timezone.utc)
                    self._complete('time')
                self._last_observed = now
            except (ValueError, TypeError, AttributeError, KeyError, OverflowError) as error:
                self._failure = error.code if isinstance(error, ReadOnlyError) else 'MALFORMED_BROKER_CALLBACK'
                self._reconciliation_fail('MALFORMED')
            finally:
                self._condition.notify_all()

    def _active_callback(self, kind, args):
        collection = {'accounts': 'accounts', 'summary': 'summary', 'summary_end': 'summary',
            'position': 'positions', 'positions_end': 'positions', 'orders_end': 'orders',
            'completed_end': 'completed', 'execution': 'executions', 'executions_end': 'executions',
            'time': 'time'}.get(kind)
        if kind == 'order': collection = 'completed' if args[3] else 'orders'
        return collection is not None and self._phase[collection] == 'ACTIVE'

    def _complete(self, name):
        self._reconciliation_complete(name)
        self._phase[name] = 'COMPLETED'
        self._done.add(name)

    def _request(self, generation, name, method, *args):
        with self._condition:
            if generation != self._generation:
                raise ReadOnlyError('OBSOLETE_IBKR_GENERATION')
            self._phase[name] = 'ACTIVE'
            self._reconciliation_request(name, args[0] if name == 'executions' else None)
            client = self._client
        try:
            method = getattr(client, method)
            method(*args)
        except BaseException as error:
            with self._condition:
                self._reconciliation_fail('FAILED' if isinstance(error, Exception) else 'INTERRUPTED', name)
            raise

    def _wait(self, predicate, deadline):
        with self._condition:
            while not predicate():
                if self._failure:
                    self._reconciliation_fail('FAILED')
                    raise ReadOnlyError(self._failure)
                remaining = deadline - monotonic()
                if remaining <= 0:
                    self._reconciliation_fail('TIMED_OUT')
                    raise ReadOnlyError('IB_GATEWAY_TIMEOUT')
                self._condition.wait(min(remaining, 0.25))
            if self._failure:
                self._reconciliation_fail('FAILED')
                raise ReadOnlyError(self._failure)

    def _run(self, client, generation):
        try:
            client.connect(self.config.host, self.config.port, self.config.client_id)
            with self._condition:
                if generation != self._generation:
                    client.disconnect()
                    return
            client.run()
        except Exception as error:
            with self._condition:
                if generation == self._generation:
                    self._diagnostics.append(('SDK_FAILURE', error.code if isinstance(error, ReadOnlyError) else 'SDK_WORKER_FAILED'))
            self._callback(generation, 'closed')

    def connect(self):
        with self._lifecycle:
            try:
                return self._connect()
            except Exception as error:
                code = error.code if isinstance(error, ReadOnlyError) else 'IB_GATEWAY_CONNECTION_FAILED'
                try: self.disconnect()
                except ReadOnlyError: pass  # Structured cleanup cause retained in diagnostics.
                raise ReadOnlyError(code) from None

    def _connect(self):
        if self.connected and self._snapshot is not None:
            return self._snapshot
        self.disconnect()
        with self._condition:
            self._generation += 1
            generation = self._generation
            self._reset()
            self._request_sequence += 2
            self._summary_id, self._execution_id = self._request_sequence, self._request_sequence + 1
            self._client = self._create_client(generation)
        deadline = monotonic() + self.config.timeout_seconds
        try:
            self._thread = Thread(target=self._run, args=(self._client, self._generation), daemon=True)
            self._thread.start()
            self._wait(lambda: self._ready, deadline)
            self._request(generation, 'accounts', 'reqManagedAccts')
            self._wait(lambda: 'accounts' in self._done, deadline)
            if len(self._accounts) != 1:
                raise ReadOnlyError('ACCOUNT_NOT_READY' if not self._accounts else 'MULTIPLE_ACCOUNTS_UNSUPPORTED')
            self._request(generation, 'summary', 'reqAccountSummary', self._summary_id, 'All', 'AccountType,TotalCashValue,NetLiquidation,BuyingPower')
            self._request(generation, 'positions', 'reqPositions')
            self._request(generation, 'orders', 'reqAllOpenOrders')
            completed_available = (self._client.serverVersion() >= self._api.completed_min_version and
                                   hasattr(self._api.OUT, 'REQ_COMPLETED_ORDERS'))
            if completed_available: self._request(generation, 'completed', 'reqCompletedOrders', False)
            else:
                with self._condition:
                    if self._reconciliation_result is None:
                        prior = self._reconciliation_collections['completed']
                        self._reconciliation_collections['completed'] = replace(
                            prior, supported=False, state='UNAVAILABLE')
                self._done.add('completed')
            self._request(generation, 'executions', 'reqExecutions', self._execution_id, self._execution_filter())
            self._request(generation, 'time', 'reqCurrentTime')
            self._wait(lambda: {'summary', 'positions', 'orders', 'completed', 'executions', 'time'} <= self._done, deadline)
            with self._condition:
                if generation != self._generation: raise ReadOnlyError('OBSOLETE_IBKR_GENERATION')
                if not self.connected: raise ReadOnlyError('IB_GATEWAY_DISCONNECTED')
                needed = ('TotalCashValue', 'NetLiquidation', 'BuyingPower')
                currencies = {currency for tag, currency in self._summary if tag in needed}
                if len(currencies) != 1: raise ReadOnlyError('ACCOUNT_NOT_READY')
                currency = currencies.pop()
                if any((tag, currency) not in self._summary for tag in needed): raise ReadOnlyError('ACCOUNT_NOT_READY')
                now = self._clock()
                account = AccountSnapshot(None, *(self._summary[(tag, currency)] for tag in needed), currency,
                    self._last_observed, now, now + timedelta(seconds=self.config.snapshot_ttl_seconds),
                    'ibkr-tws-readonly-sdk-text', True, tuple(self._positions[k] for k in sorted(self._positions)))
                # Independent mode remains UNKNOWN; verified describes completed data collection only.
                commissions = tuple(self._commissions_read[k] for k in sorted(self._commissions_read)
                                    if k in self._fills_read)
                self._snapshot = ReadOnlySnapshot(account, (AccountIdentity('account-1'),),
                    tuple(self._orders_read[k] for k in sorted(self._orders_read)),
                    tuple(self._completed_read[k] for k in sorted(self._completed_read)),
                    tuple(self._fills_read[k] for k in sorted(self._fills_read)), commissions,
                    self._broker_time, self._client.serverVersion(), completed_available)
                self._reconciliation_publication = (
                    generation, self._reconciliation_start_marker, self._snapshot)
                self._reconciliation_finish('COLLECTED', public_snapshot=self._snapshot)
                self._collecting = False
                return self._snapshot
        except Exception as error:
            code = error.code if isinstance(error, ReadOnlyError) else 'IB_GATEWAY_CONNECTION_FAILED'
            with self._condition:
                self._reconciliation_fail('FAILED')
            self.disconnect()
            raise ReadOnlyError(code) from None

    def disconnect(self):
        with self._lifecycle:
            return self._disconnect()

    def _disconnect(self):
        with self._condition:
            self._reconciliation_fail('GENERATION_LOST')
            self._generation += 1
            client, self._client = self._client, None
            thread, self._thread = self._thread, None
            self._snapshot = None
            self._collecting = False
        error = None
        if client is not None:
            try: client.disconnect()
            except ReadOnlyError as failure:
                error = failure
                self._diagnostics.append(('SDK_FAILURE', failure.code))
        if thread is not None: sdk_call('SDK_WORKER_SHUTDOWN_FAILED', thread.join, timeout=1)
        if error is not None: raise error

    def drain_events(self, at):
        validate_timestamp(at)
        with self._condition:
            processing_at = max((at, self._clock(), *(e.occurred_at for e in self._events)))
            events = tuple(replace(e, received_at=processing_at) for e in self._events)
            self._events.clear()
            return events

    def _write_blocked(self, *args, **kwargs): raise ReadOnlyError('READ_ONLY_BROKER_TRANSPORT')
    place_order = modify_order = replace_order = cancel_order = flatten_positions = _write_blocked
