"""IBKR translations with an explicitly in-memory-only transport boundary.

No TWS/Gateway imports, sockets, credentials, account IDs or real request path.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from tradingbot_backtest.market import validate_timestamp
from tradingbot_backtest.numerics import add, decimal

from .broker import BrokerOperationError, SimulatedBroker
from .models import (AccountSnapshot, BrokerEvent, BrokerReason as R, Commission,
                     EventKind, Fill, OrderRequest, OrderState, OrderType, Position,
                     Side, TradingMode, identifier)


@dataclass(frozen=True, slots=True)
class IBKRContract:
    security_id: str
    symbol: str
    con_id: int
    exchange: str
    currency: str
    valid_from: datetime
    valid_until: datetime
    available_at: datetime
    source_id: str
    verified: bool
    security_type: str = 'STK'

    def __post_init__(self):
        for v in (self.security_id, self.symbol, self.exchange, self.currency, self.source_id):
            identifier(v)
        if type(self.con_id) is not int or self.con_id <= 0 or type(self.verified) is not bool:
            raise TypeError('Explicit contract identity and verification required')
        for v in (self.valid_from, self.valid_until, self.available_at):
            validate_timestamp(v)
        if self.valid_until <= self.valid_from or self.security_type != 'STK':
            raise ValueError('Supply a valid stock contract window')


@dataclass(frozen=True, slots=True)
class IBKRPositionData:
    contract: IBKRContract
    quantity: Decimal | str
    average_cost: Decimal | str


@dataclass(frozen=True, slots=True)
class IBKRAccountData:
    """Sanitized fixture/transport data: never contains account identifiers."""
    mode: TradingMode | None
    cash: Decimal | str
    equity: Decimal | str
    buying_power: Decimal | str
    currency: str
    observed_at: datetime
    available_at: datetime
    valid_until: datetime
    source_id: str
    verified: bool
    positions: tuple[IBKRPositionData, ...] = ()


@dataclass(frozen=True, slots=True)
class IBKROrder:
    order_ref: str
    action: str
    order_type: str
    total_quantity: Decimal
    tif: str
    limit_price: Decimal | None
    auxiliary_price: Decimal | None


@dataclass(frozen=True, slots=True)
class IBKRSubmission:
    order_id: int
    status: str
    error_code: int | None = None


@dataclass(frozen=True, slots=True)
class IBKRExecution:
    execution_id: str
    order_id: int
    con_id: int
    side: str
    shares: Decimal | str
    price: Decimal | str
    timestamp: datetime


class IBKRTransport(Protocol):
    """Future transport contract; arbitrary implementations cannot be activated now."""
    def connect(self) -> IBKRAccountData: ...
    def disconnect(self) -> None: ...
    def place_order(self, contract: IBKRContract, order: IBKROrder) -> IBKRSubmission: ...
    def cancel_order(self, order_id: int) -> bool: ...
    def modify_order(self, order_id: int, contract: IBKRContract, order: IBKROrder) -> bool: ...


class InMemoryIBKRTransport:
    """Injected responses only. No connection endpoint or callable injection."""
    def __init__(self, account: IBKRAccountData, *, submission_status='PreSubmitted',
                 error_code=None, confirm_cancellations=True):
        if not isinstance(account, IBKRAccountData):
            raise TypeError('Typed sanitized account fixture required')
        if type(confirm_cancellations) is not bool:
            raise TypeError('Explicit cancellation confirmation required')
        self.account = account
        self.submission_status = submission_status
        self.error_code = error_code
        self.confirm_cancellations = confirm_cancellations
        self.submissions = []
        self.cancellations = []
        self.modifications = []
        self.connections = 0
        self.disconnections = 0
        self._next_id = 1

    def connect(self):
        self.connections += 1
        return self.account

    def disconnect(self):
        self.disconnections += 1

    def place_order(self, contract, order):
        self.submissions.append((contract, order))
        order_id = self._next_id
        self._next_id += 1
        return IBKRSubmission(order_id, self.submission_status, self.error_code)

    def cancel_order(self, order_id):
        self.cancellations.append(order_id)
        return self.confirm_cancellations

    def modify_order(self, order_id, contract, order):
        self.modifications.append((order_id, contract, order))
        return True


_STATUSES = {
    'PendingSubmit': OrderState.SUBMITTED,
    'PreSubmitted': OrderState.ACKNOWLEDGED,
    'Submitted': OrderState.WORKING,
    'Filled': OrderState.FILLED,
    'Cancelled': OrderState.CANCELLED,
    'ApiCancelled': OrderState.CANCELLED,
    'Inactive': OrderState.REJECTED,
    'Expired': OrderState.EXPIRED,
}


class IBKRAdapter(SimulatedBroker):
    def __init__(self, transport: IBKRTransport, contracts: tuple[IBKRContract, ...],
                 *, mode=TradingMode.PAPER):
        # Deliberate phase lock: a duck-typed network client or subclass is rejected.
        if type(transport) is not InMemoryIBKRTransport:
            raise TypeError('Phase 10C1 permits only the bundled in-memory transport')
        if type(contracts) is not tuple or any(not isinstance(c, IBKRContract) for c in contracts):
            raise TypeError('Immutable explicitly supplied contracts required')
        if len({c.security_id for c in contracts}) != len(contracts) or len({c.con_id for c in contracts}) != len(contracts):
            raise ValueError('Conflicting security/IBKR contract mapping')
        super().__init__(mode)
        self._transport = transport
        self._contracts = {c.security_id: c for c in contracts}
        self._con_ids = {c.con_id: c for c in contracts}

    def _backend_connect(self, at):
        data = self._transport.connect()
        if type(data.positions) is not tuple:
            raise TypeError('Immutable account positions required')
        positions = tuple(Position(p.contract.security_id, p.contract.symbol, decimal(p.quantity),
                          decimal(p.average_cost), p.contract.currency) for p in data.positions)
        return AccountSnapshot(data.mode, decimal(data.cash), decimal(data.equity), decimal(data.buying_power),
                               data.currency, data.observed_at, data.available_at, data.valid_until,
                               data.source_id, data.verified, positions)

    def _backend_disconnect(self):
        self._transport.disconnect()

    def _submission_prerequisites(self, order, at):
        c = self._contracts.get(order.security_id)
        if c is None or c.available_at > at:
            return (R.CONTRACT_UNAVAILABLE,)
        if not c.verified or not (c.valid_from <= at < c.valid_until):
            return (R.CONTRACT_UNAVAILABLE,)
        if c.symbol != order.symbol or c.currency != order.currency:
            return (R.CONTRACT_MISMATCH,)
        return ()

    @staticmethod
    def translate_order(order: OrderRequest) -> IBKROrder:
        return IBKROrder(order.client_order_id, order.side.value,
                         {OrderType.MARKET: 'MKT', OrderType.LIMIT: 'LMT', OrderType.STOP: 'STP'}[order.order_type],
                         order.quantity, order.time_in_force.value, order.limit_price, order.stop_price)

    def _backend_place(self, order):
        response = self._transport.place_order(self._contracts[order.security_id], self.translate_order(order))
        if type(response.order_id) is not int or response.order_id <= 0:
            raise ValueError('Invalid broker order ID')
        return f'ibkr-{response.order_id}', _STATUSES[response.status], response.error_code

    def _backend_cancel(self, order_id):
        return self._transport.cancel_order(int(order_id.removeprefix('ibkr-')))

    def _backend_replace(self, order_id, order):
        return self._transport.modify_order(int(order_id.removeprefix('ibkr-')),
                                            self._contracts[order.security_id], self.translate_order(order))

    def _translation_failed(self, at, *, order_id=None, event_id=None, error_code=None):
        self._time(at)
        self._reconciliation_required = True
        status = self._orders.get(order_id)
        details = ()
        if event_id is not None:
            identifier(event_id)
            details = (('event_id', event_id),)
        code = error_code if type(error_code) is int else None
        self._record('TRANSLATION_REJECTED', at, order=status.request if status else None,
                     order_id=order_id, state=status.state if status else None, details=details,
                     error_code=code, reasons=(R.INVALID_EVENT, R.RECONCILIATION_REQUIRED))
        raise BrokerOperationError(R.INVALID_EVENT) from None

    def receive_status(self, order_id: int, status: str, *, event_id: str,
                       occurred_at: datetime, received_at: datetime, error_code=None):
        with self._lock:
            if type(order_id) is not int or order_id <= 0 or status not in _STATUSES:
                self._translation_failed(received_at, order_id=f'ibkr-{order_id}' if type(order_id) is int and order_id > 0 else None,
                                         event_id=event_id, error_code=error_code)
            event = BrokerEvent(event_id, EventKind(_STATUSES[status].value), occurred_at,
                                received_at, f'ibkr-{order_id}', error_code=error_code)
            return self.handle_event(event)

    def receive_execution(self, execution: IBKRExecution, *, event_id: str, received_at: datetime):
        with self._lock:
            try:
                if (not isinstance(execution, IBKRExecution) or type(execution.order_id) is not int
                        or type(execution.con_id) is not int or execution.side not in ('BOT', 'SLD')):
                    raise ValueError('Invalid execution input')
                c = self._con_ids[execution.con_id]
                order_id = f'ibkr-{execution.order_id}'
                old = self.order_status(order_id)
                fill = Fill(execution.execution_id, order_id, c.security_id, c.symbol,
                            Side.BUY if execution.side == 'BOT' else Side.SELL,
                            decimal(execution.shares), decimal(execution.price), c.currency, execution.timestamp)
                # Duplicate executions retain their original classification for deduplication.
                previous = self._fills.get(fill.fill_id)
                total = old.filled_quantity if previous is not None else add(old.filled_quantity, fill.quantity)
                kind = EventKind.FILLED if total == old.request.quantity else EventKind.PARTIALLY_FILLED
                if previous is not None:
                    kind = next(e.kind for e in self._events.values() if e.fill == previous)
                event = BrokerEvent(event_id, kind, execution.timestamp, received_at, order_id, fill=fill)
            except (ValueError, TypeError, KeyError):
                self._translation_failed(received_at, order_id=f'ibkr-{execution.order_id}'
                    if isinstance(execution, IBKRExecution) and type(execution.order_id) is int and execution.order_id > 0 else None,
                    event_id=event_id)
            return self.handle_event(event)

    def receive_commission(self, report_id: str, fill_id: str, value: Decimal | str,
                           currency: str, *, event_id: str, occurred_at: datetime, received_at: datetime):
        with self._lock:
            try:
                fill = self._fills[fill_id]
                commission = Commission(report_id, fill_id, decimal(value), currency, occurred_at)
                event = BrokerEvent(event_id, EventKind.COMMISSION, occurred_at, received_at,
                                    fill.broker_order_id, commission=commission)
            except (ValueError, TypeError, KeyError):
                self._translation_failed(received_at)
            return self.handle_event(event)
