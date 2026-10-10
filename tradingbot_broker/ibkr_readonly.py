"""Optional official TWS SDK, read requests only; no network on module import."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import importlib
import logging
import os
import socket
from threading import Condition, Thread, RLock
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
        def openOrder(self, orderId, contract, order, orderState):
            owner._callback(generation, 'order', contract, order, orderState, False)
        def openOrderEnd(self): owner._callback(generation, 'orders_end')
        def completedOrder(self, contract, order, orderState):
            owner._callback(generation, 'order', contract, order, orderState, True)
        def completedOrdersEnd(self): owner._callback(generation, 'completed_end')
        def execDetails(self, reqId, contract, execution):
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
            now = self._clock()
            validate_timestamp(now)
            try:
                if kind == 'closed' or (kind == 'error' and args[0] in _DISCONNECT_CODES):
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
                    if args[0] == self._execution_id: self._complete('executions')
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
        self._phase[name] = 'COMPLETED'
        self._done.add(name)

    def _request(self, generation, name, method, *args):
        with self._condition:
            if generation != self._generation:
                raise ReadOnlyError('OBSOLETE_IBKR_GENERATION')
            self._phase[name] = 'ACTIVE'
            client = self._client
        method = getattr(client, method)
        method(*args)

    def _wait(self, predicate, deadline):
        with self._condition:
            while not predicate():
                if self._failure: raise ReadOnlyError(self._failure)
                remaining = deadline - monotonic()
                if remaining <= 0: raise ReadOnlyError('IB_GATEWAY_TIMEOUT')
                self._condition.wait(min(remaining, 0.25))
            if self._failure: raise ReadOnlyError(self._failure)

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
            else: self._done.add('completed')
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
                self._collecting = False
                return self._snapshot
        except Exception as error:
            code = error.code if isinstance(error, ReadOnlyError) else 'IB_GATEWAY_CONNECTION_FAILED'
            self.disconnect()
            raise ReadOnlyError(code) from None

    def disconnect(self):
        with self._lifecycle:
            return self._disconnect()

    def _disconnect(self):
        with self._condition:
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
