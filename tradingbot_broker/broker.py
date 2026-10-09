"""Shared offline lifecycle and interlocks behind the provider-neutral protocol."""

from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from threading import RLock
from typing import Protocol, runtime_checkable

from tradingbot_backtest.market import validate_timestamp
from tradingbot_backtest.numerics import add

from .models import (AccountSnapshot, BrokerAuditRecord, BrokerEvent, BrokerReason as R,
                     Commission, ConnectionStatus, EventKind, Fill, FlattenPlan,
                     OrderAcknowledgement, OrderIntent, OrderRequest, OrderState,
                     OrderStatus, OrderType, Position, RiskPermission, Side,
                     TERMINAL_STATES, TradingMode, enum)
from .safety import Controls, account_gates, submission_gates


class BrokerOperationError(ValueError):
    def __init__(self, reason: R):
        self.reason = reason
        super().__init__(reason.value)


@runtime_checkable
class Broker(Protocol):
    @property
    def mode(self) -> TradingMode: ...
    @property
    def connection_status(self) -> ConnectionStatus: ...
    @property
    def audit(self) -> tuple[BrokerAuditRecord, ...]: ...
    def connect(self, at: datetime) -> ConnectionStatus: ...
    def disconnect(self, at: datetime) -> None: ...
    def account_summary(self, at: datetime) -> AccountSnapshot: ...
    def open_positions(self, at: datetime) -> tuple[Position, ...]: ...
    def working_orders(self) -> tuple[OrderStatus, ...]: ...
    def completed_orders(self) -> tuple[OrderStatus, ...]: ...
    def order_status(self, broker_order_id: str) -> OrderStatus: ...
    def place_order(self, order: OrderRequest, at: datetime,
                    permission: RiskPermission | None = None) -> OrderAcknowledgement: ...
    def cancel_order(self, broker_order_id: str, at: datetime) -> bool: ...
    def replace_order(self, broker_order_id: str, order: OrderRequest, at: datetime,
                      permission: RiskPermission | None = None) -> OrderAcknowledgement: ...
    def fills(self) -> tuple[Fill, ...]: ...
    def commissions(self) -> tuple[Commission, ...]: ...
    def handle_event(self, event: BrokerEvent) -> bool: ...
    def set_trading_enabled(self, enabled: bool, at: datetime) -> None: ...
    def pause_new_entries(self, at: datetime) -> None: ...
    def resume_new_entries(self, at: datetime) -> None: ...
    def emergency_stop(self, at: datetime) -> None: ...
    def cancel_working_orders(self, at: datetime) -> tuple[str, ...]: ...
    def flatten_positions(self, at: datetime) -> FlattenPlan: ...


class SimulatedBroker(ABC):
    """Only bundled in-memory implementations exist. No network methods here.

    Calls and callback receipt times must be monotonic; origin times may be older.
    The reentrant lock makes control changes and the submit check/dispatch atomic.
    """
    def __init__(self, mode: TradingMode):
        enum(mode, TradingMode)
        self._mode = mode
        self._connection = ConnectionStatus.DISCONNECTED
        self._account: AccountSnapshot | None = None
        # Accepted evidence survives disconnect; connected proof does not.
        self._accepted_account: AccountSnapshot | None = None
        self._connection_at: datetime | None = None
        self._controls = Controls()
        self._orders: dict[str, OrderStatus] = {}
        self._submitted_at: dict[str, datetime] = {}
        self._attempts: dict[str, OrderRequest] = {}
        self._fills: dict[str, Fill] = {}
        self._commissions: dict[str, Commission] = {}
        self._events: dict[str, BrokerEvent] = {}
        self._audit: list[BrokerAuditRecord] = []
        self._last_at: datetime | None = None
        self._reconciliation_required = False
        self._lock = RLock()

    @property
    def mode(self):
        return self._mode

    @property
    def connection_status(self):
        return self._connection

    @property
    def controls(self):
        return self._controls

    @property
    def audit(self):
        with self._lock:
            return tuple(self._audit)

    @property
    def reconciliation_required(self):
        return self._reconciliation_required

    def _time(self, at):
        validate_timestamp(at)
        if self._last_at is not None and at < self._last_at:
            raise ValueError('Broker calls and event receipt times must be monotonic')

    def _record(self, action, at, *, order=None, order_id=None, state=None,
                reasons=(), details=(), error_code=None):
        account_mode = (self._account.mode if self._account is not None
                        and self._account.available_at <= at else None)
        self._audit.append(BrokerAuditRecord(at, action, self.mode, account_mode,
            order, order_id, state, reasons, details, error_code))
        self._last_at = at

    def _gates(self, order, permission, at):
        failures = submission_gates(self.mode, self._connection, self._account,
                                    self._controls, order, permission, at)
        if self._reconciliation_required:
            failures += (R.RECONCILIATION_REQUIRED,)
        return failures + self._submission_prerequisites(order, at)

    def _submission_prerequisites(self, order, at):
        return ()

    def _install_account(self, snapshot, at):
        """One installation path: never roll back accepted observation evidence."""
        if not isinstance(snapshot, AccountSnapshot):
            raise TypeError('Typed account evidence required')
        reason = None
        details = [('available_at', snapshot.available_at)]
        if snapshot.available_at > at:
            reason = R.ACCOUNT_DATA_UNAVAILABLE
        else:
            details.append(('observed_at', snapshot.observed_at))
            previous = self._accepted_account
            if previous is not None:
                details.append(('accepted_observed_at', previous.observed_at))
                if snapshot.observed_at < previous.observed_at:
                    reason = R.STALE_ACCOUNT_EVIDENCE
                elif snapshot.observed_at == previous.observed_at and snapshot != previous:
                    reason = R.CONFLICTING_ACCOUNT_EVIDENCE
                    self._reconciliation_required = True
        if reason is not None:
            self._record('ACCOUNT_EVIDENCE_REJECTED', at, reasons=(reason,), details=tuple(details))
            return reason
        self._accepted_account = snapshot
        self._account = snapshot
        return None

    def _connection_stale(self, target, occurred_at):
        # Local actions use their action time. Ties conservatively favor disconnect.
        return self._connection_at is not None and (
            occurred_at < self._connection_at or
            (occurred_at == self._connection_at and
             self._connection == ConnectionStatus.DISCONNECTED and target == ConnectionStatus.CONNECTED))

    def _denied(self, action, order, at, reasons, *, order_id=None, error_code=None):
        existing = self._orders.get(order_id)
        self._record(action, at, order=order, order_id=order_id,
                     state=existing.state if existing else OrderState.REJECTED, reasons=reasons,
                     details=(('action_outcome', 'REJECTED'),), error_code=error_code)
        return OrderAcknowledgement(order.client_order_id, order_id, OrderState.REJECTED,
                                    at, False, reasons, error_code)

    @abstractmethod
    def _backend_connect(self, at: datetime) -> AccountSnapshot: ...
    @abstractmethod
    def _backend_disconnect(self) -> None: ...
    @abstractmethod
    def _backend_place(self, order: OrderRequest) -> tuple[str, OrderState, int | None]: ...
    @abstractmethod
    def _backend_cancel(self, order_id: str) -> bool: ...
    @abstractmethod
    def _backend_replace(self, order_id: str, order: OrderRequest) -> bool: ...

    def connect(self, at):
        with self._lock:
            self._time(at)
            if self._connection == ConnectionStatus.CONNECTED:
                self._record('CONNECT_ALREADY_CONNECTED', at)
                return self._connection
            if self._connection_stale(ConnectionStatus.CONNECTED, at):
                self._record('CONNECT_IGNORED', at, reasons=(R.STALE_CONNECTION_EVENT,),
                             details=(('connection_boundary', self._connection_at),))
                return self._connection
            try:
                account = self._backend_connect(at)
                if not isinstance(account, AccountSnapshot):
                    raise TypeError('Typed handshake required')
            except Exception:
                self._record('CONNECT_FAILED', at, reasons=(R.TRANSPORT_FAILURE,))
                raise BrokerOperationError(R.TRANSPORT_FAILURE) from None
            if self._install_account(account, at) is not None:
                return self._connection
            self._connection = ConnectionStatus.CONNECTED
            self._connection_at = at
            self._record('CONNECTED', at)
            return self._connection

    def disconnect(self, at):
        with self._lock:
            self._time(at)
            try:
                self._backend_disconnect()
            except Exception:
                self._record('DISCONNECT_FAILED', at, reasons=(R.TRANSPORT_FAILURE,))
            self._connection = ConnectionStatus.DISCONNECTED
            self._connection_at = at
            self._account = None
            self._record('DISCONNECTED', at, reasons=(R.BROKER_DISCONNECTED,))

    def report_account(self, snapshot: AccountSnapshot, at: datetime):
        with self._lock:
            self._time(at)
            reason = self._install_account(snapshot, at)
            if reason is not None:
                raise BrokerOperationError(reason)
            self._record('ACCOUNT_REPORTED', at, details=(('observed_at', snapshot.observed_at),
                         ('cash', snapshot.cash), ('equity', snapshot.equity),
                         ('buying_power', snapshot.buying_power), ('source_id', snapshot.source_id)))

    def account_summary(self, at):
        with self._lock:
            self._time(at)
            account = self._account
            if (self._connection != ConnectionStatus.CONNECTED or account is None
                    or account.available_at > at or at >= account.valid_until or not account.verified):
                self._record('ACCOUNT_UNAVAILABLE', at, reasons=(R.ACCOUNT_DATA_UNAVAILABLE,))
                raise BrokerOperationError(R.ACCOUNT_DATA_UNAVAILABLE)
            self._record('ACCOUNT_SUMMARY', at, details=(('cash', account.cash),
                         ('equity', account.equity), ('buying_power', account.buying_power),
                         ('observed_at', account.observed_at), ('source_id', account.source_id)))
            return account

    def open_positions(self, at):
        return self.account_summary(at).positions

    def working_orders(self):
        with self._lock:
            return tuple(o for o in self._orders.values() if o.state not in TERMINAL_STATES)

    def completed_orders(self):
        with self._lock:
            return tuple(o for o in self._orders.values() if o.state in TERMINAL_STATES)

    def order_status(self, broker_order_id):
        with self._lock:
            if broker_order_id not in self._orders:
                raise BrokerOperationError(R.UNKNOWN_ORDER)
            return self._orders[broker_order_id]

    def fills(self):
        with self._lock:
            return tuple(self._fills.values())

    def commissions(self):
        with self._lock:
            return tuple(self._commissions.values())

    def place_order(self, order, at, permission=None):
        with self._lock:
            self._time(at)
            if not isinstance(order, OrderRequest) or (permission is not None and not isinstance(permission, RiskPermission)):
                raise TypeError('Typed order and risk permission required')
            if order.client_order_id in self._attempts:
                return self._denied('ORDER_BLOCKED', order, at, (R.DUPLICATE_ORDER,))
            self._attempts[order.client_order_id] = order
            failures = self._gates(order, permission, at)
            if failures:
                return self._denied('ORDER_BLOCKED', order, at, failures)
            self._record('SUBMITTED', at, order=order, state=OrderState.SUBMITTED,
                         details=(('risk_source', permission.source_id), ('risk_reason', permission.reason_code)))
            try:
                order_id, state, code = self._backend_place(order)
                if state not in (OrderState.SUBMITTED, OrderState.ACKNOWLEDGED, OrderState.WORKING,
                                 OrderState.REJECTED) or order_id in self._orders:
                    raise ValueError('Invalid transport acknowledgement')
                enum(state, OrderState)
                if code is not None and type(code) is not int:
                    raise TypeError('Numeric broker error code required')
                from .models import identifier
                identifier(order_id)
            except Exception:
                # Unknown dispatch outcome is not a confirmed rejection or a retry license.
                self._reconciliation_required = True
                self._record('SUBMISSION_UNKNOWN', at, order=order, state=OrderState.UNKNOWN,
                             reasons=(R.TRANSPORT_FAILURE, R.RECONCILIATION_REQUIRED))
                return OrderAcknowledgement(order.client_order_id, None, OrderState.UNKNOWN,
                                            at, False, (R.TRANSPORT_FAILURE, R.RECONCILIATION_REQUIRED))
            self._orders[order_id] = OrderStatus(order_id, order, state, Decimal(0), None, at)
            self._submitted_at[order_id] = at
            reasons = (R.BROKER_REJECTED,) if state == OrderState.REJECTED else ()
            self._record(state.value, at, order=order, order_id=order_id, state=state,
                         reasons=reasons, error_code=code)
            return OrderAcknowledgement(order.client_order_id, order_id, state, at,
                                        state != OrderState.REJECTED, reasons, code)

    def cancel_order(self, broker_order_id, at):
        with self._lock:
            self._time(at)
            if broker_order_id not in self._orders:
                from .models import identifier
                identifier(broker_order_id)
                self._record('CANCEL_BLOCKED', at, order_id=broker_order_id, reasons=(R.UNKNOWN_ORDER,))
                return False
            status = self.order_status(broker_order_id)
            failures = account_gates(self.mode, self._connection, self._account, at)
            if status.state in TERMINAL_STATES:
                failures += (R.TERMINAL_ORDER,)
            if failures:
                self._record('CANCEL_BLOCKED', at, order=status.request, order_id=broker_order_id,
                             state=status.state, reasons=failures)
                return False
            self._record('CANCEL_REQUESTED', at, order=status.request, order_id=broker_order_id, state=status.state)
            try:
                confirmed = self._backend_cancel(broker_order_id)
                if type(confirmed) is not bool:
                    raise TypeError('Explicit cancellation confirmation required')
            except Exception:
                self._reconciliation_required = True
                self._record('CANCEL_UNKNOWN', at, order=status.request, order_id=broker_order_id,
                             reasons=(R.TRANSPORT_FAILURE, R.RECONCILIATION_REQUIRED))
                return False
            if confirmed:
                self._orders[broker_order_id] = replace(status, state=OrderState.CANCELLED, updated_at=at)
                self._record('CANCELLED', at, order=status.request, order_id=broker_order_id, state=OrderState.CANCELLED)
            return True

    def cancel_working_orders(self, at):
        with self._lock:
            self._time(at)
            return tuple(status.broker_order_id for status in self.working_orders()
                         if self.cancel_order(status.broker_order_id, at))

    def replace_order(self, broker_order_id, order, at, permission=None):
        with self._lock:
            self._time(at)
            if not isinstance(order, OrderRequest) or (permission is not None and not isinstance(permission, RiskPermission)):
                raise TypeError('Typed replacement and risk permission required')
            if broker_order_id not in self._orders:
                from .models import identifier
                identifier(broker_order_id)
                return self._denied('MODIFY_BLOCKED', order, at, (R.UNKNOWN_ORDER,), order_id=broker_order_id)
            status = self.order_status(broker_order_id)
            immutable = ('client_order_id', 'security_id', 'symbol', 'mode', 'side', 'order_type',
                         'currency', 'created_at', 'expires_at', 'intent', 'time_in_force')
            failures = self._gates(order, permission, at)
            if status.state in TERMINAL_STATES:
                failures += (R.TERMINAL_ORDER,)
            if any(getattr(order, k) != getattr(status.request, k) for k in immutable) or order.quantity <= status.filled_quantity:
                failures += (R.INVALID_MODIFICATION,)
            if failures:
                return self._denied('MODIFY_BLOCKED', order, at, failures, order_id=broker_order_id)
            try:
                confirmed = self._backend_replace(broker_order_id, order)
                if type(confirmed) is not bool:
                    raise TypeError('Explicit modification confirmation required')
            except Exception:
                self._reconciliation_required = True
                self._record('MODIFY_UNKNOWN', at, order=order, order_id=broker_order_id,
                             reasons=(R.TRANSPORT_FAILURE, R.RECONCILIATION_REQUIRED))
                return OrderAcknowledgement(order.client_order_id, broker_order_id, OrderState.UNKNOWN,
                                            at, False, (R.TRANSPORT_FAILURE, R.RECONCILIATION_REQUIRED))
            if not confirmed:
                return self._denied('MODIFY_REJECTED', order, at, (R.BROKER_REJECTED,), order_id=broker_order_id)
            self._orders[broker_order_id] = replace(status, request=order, updated_at=at)
            self._record('MODIFIED', at, order=order, order_id=broker_order_id, state=status.state,
                         details=(('previous_quantity', status.request.quantity),
                                  ('risk_source', permission.source_id), ('risk_reason', permission.reason_code)))
            return OrderAcknowledgement(order.client_order_id, broker_order_id, status.state, at, True)

    def set_trading_enabled(self, enabled, at):
        with self._lock:
            self._time(at)
            if type(enabled) is not bool:
                raise TypeError('Explicit boolean trading control required')
            self._controls = replace(self._controls, trading_enabled=enabled)
            self._record('TRADING_CONTROL', at, details=(('enabled', enabled),))

    def pause_new_entries(self, at):
        with self._lock:
            self._time(at)
            self._controls = replace(self._controls, entries_paused=True)
            self._record('ENTRIES_PAUSED', at)

    def resume_new_entries(self, at):
        with self._lock:
            self._time(at)
            self._controls = replace(self._controls, entries_paused=False)
            self._record('ENTRIES_RESUMED', at)

    def emergency_stop(self, at):
        with self._lock:
            self._time(at)
            self._controls = replace(self._controls, emergency_stopped=True)
            self._record('EMERGENCY_STOP', at, reasons=(R.EMERGENCY_STOP,))

    def flatten_positions(self, at):
        """Dry-run instructions only; not an emergency bypass of submission gates."""
        with self._lock:
            account = self.account_summary(at)
            if account_gates(self.mode, self._connection, account, at):
                self._record('FLATTEN_BLOCKED', at, reasons=account_gates(self.mode, self._connection, account, at))
                raise BrokerOperationError(account_gates(self.mode, self._connection, account, at)[0])
            requests = tuple(OrderRequest(f'flatten-{len(self._audit)}-{i}', p.security_id, p.symbol,
                TradingMode.PAPER, Side.SELL if p.quantity > 0 else Side.BUY, OrderType.MARKET,
                p.quantity.copy_abs(), p.currency, at, account.valid_until, OrderIntent.EXIT)
                for i, p in enumerate(sorted(account.positions, key=lambda p: p.security_id)))
            for request in requests:
                self._record('FLATTEN_PLANNED', at, order=request, reasons=(R.FLATTEN_PLAN_ONLY,))
            return FlattenPlan(at, account, requests)

    def _bad_event(self, event, reason):
        self._reconciliation_required = True
        status = self._orders.get(event.broker_order_id)
        details = [('event_id', event.event_id), ('occurred_at', event.occurred_at)]
        if status is not None:
            details.append(('submitted_at', self._submitted_at[event.broker_order_id]))
        if event.fill is not None:
            details.extend((('fill_id', event.fill.fill_id), ('fill_quantity', event.fill.quantity),
                            ('fill_price', event.fill.price), ('executed_at', event.fill.executed_at)))
        if event.commission is not None:
            details.extend((('report_id', event.commission.report_id), ('fill_id', event.commission.fill_id),
                            ('commission', event.commission.amount), ('currency', event.commission.currency)))
        self._record('EVENT_REJECTED', event.received_at, order=status.request if status else None,
                     order_id=event.broker_order_id, state=status.state if status else None,
                     reasons=(reason,), details=tuple(details), error_code=event.error_code)
        raise BrokerOperationError(reason)

    def handle_event(self, event):
        with self._lock:
            if not isinstance(event, BrokerEvent):
                raise TypeError('Typed broker event required')
            self._time(event.received_at)
            previous = self._events.get(event.event_id)
            if previous is not None:
                fields = ('kind', 'occurred_at', 'broker_order_id', 'fill', 'commission', 'error_code', 'account')
                if any(getattr(previous, k) != getattr(event, k) for k in fields):
                    self._bad_event(event, R.CONFLICTING_EVENT)
                self._record('EVENT_DUPLICATE', event.received_at, order_id=event.broker_order_id,
                             reasons=(R.DUPLICATE_EVENT,), details=(('event_id', event.event_id),))
                return False
            if event.kind in (EventKind.DISCONNECTED, EventKind.RECONNECTED):
                if event.broker_order_id is not None or event.fill is not None or event.commission is not None:
                    self._bad_event(event, R.INVALID_EVENT)
                if event.kind == EventKind.DISCONNECTED and event.account is not None:
                    self._bad_event(event, R.INVALID_EVENT)
                target = (ConnectionStatus.DISCONNECTED if event.kind == EventKind.DISCONNECTED
                          else ConnectionStatus.CONNECTED)
                if self._connection_stale(target, event.occurred_at):
                    self._events[event.event_id] = event
                    self._record('CONNECTION_EVENT_IGNORED', event.received_at,
                                 reasons=(R.STALE_CONNECTION_EVENT,), error_code=event.error_code,
                                 details=(('event_id', event.event_id), ('occurred_at', event.occurred_at),
                                          ('connection_boundary', self._connection_at),
                                          ('requested_connection', target.value),
                                          ('current_connection', self._connection.value)))
                    return False
                if event.kind == EventKind.DISCONNECTED:
                    self._connection = ConnectionStatus.DISCONNECTED
                    self._account = None
                else:
                    if event.account is None:
                        self._bad_event(event, R.ACCOUNT_DATA_UNAVAILABLE)
                    if self._install_account(event.account, event.received_at) is not None:
                        self._events[event.event_id] = event
                        return False
                    self._connection = ConnectionStatus.CONNECTED
                self._connection_at = event.occurred_at
                self._events[event.event_id] = event
                self._record(event.kind.value, event.received_at, details=(('event_id', event.event_id),
                             ('occurred_at', event.occurred_at)), error_code=event.error_code,
                             reasons=(R.BROKER_DISCONNECTED,) if event.kind == EventKind.DISCONNECTED else ())
                return True
            if event.account is not None or event.broker_order_id not in self._orders:
                self._bad_event(event, R.UNKNOWN_ORDER)
            status = self._orders[event.broker_order_id]
            submitted_at = self._submitted_at[event.broker_order_id]
            if event.occurred_at < submitted_at:
                self._bad_event(event, R.INVALID_EVENT)
            if event.kind == EventKind.COMMISSION:
                commission = event.commission
                if commission is None or event.fill is not None:
                    self._bad_event(event, R.INVALID_EVENT)
                fill = self._fills.get(commission.fill_id)
                if (fill is None or fill.broker_order_id != event.broker_order_id or commission.currency != fill.currency
                        or commission.timestamp < fill.executed_at or commission.timestamp > event.occurred_at):
                    self._bad_event(event, R.INVALID_EVENT)
                existing = self._commissions.get(commission.fill_id)
                if existing is not None and existing != commission:
                    self._bad_event(event, R.CONFLICTING_EVENT)
                self._commissions[commission.fill_id] = commission
                self._events[event.event_id] = event
                self._record('COMMISSION', event.received_at, order=status.request, order_id=event.broker_order_id,
                    state=status.state, details=(('fill_id', fill.fill_id), ('report_id', commission.report_id),
                                                ('amount', commission.amount), ('currency', commission.currency)),
                    error_code=event.error_code, reasons=(R.DUPLICATE_EVENT,) if existing is not None else ())
                return existing is None
            if event.commission is not None:
                self._bad_event(event, R.INVALID_EVENT)
            target = OrderState(event.kind.value)
            fill = event.fill
            filled = status.filled_quantity
            average = status.average_fill_price
            if fill is not None:
                if (target not in (OrderState.PARTIALLY_FILLED, OrderState.FILLED)
                        or fill.broker_order_id != event.broker_order_id
                        or any(getattr(fill, k) != getattr(status.request, k) for k in ('security_id', 'symbol', 'side', 'currency'))
                        or fill.executed_at < submitted_at or fill.executed_at > event.occurred_at):
                    self._bad_event(event, R.INVALID_EVENT)
                existing = self._fills.get(fill.fill_id)
                if existing is not None:
                    if existing != fill:
                        self._bad_event(event, R.CONFLICTING_EVENT)
                    self._events[event.event_id] = event
                    self._record('FILL_DUPLICATE', event.received_at, order=status.request,
                                 order_id=event.broker_order_id, state=status.state, reasons=(R.DUPLICATE_EVENT,),
                                 details=(('fill_id', fill.fill_id), ('event_id', event.event_id)))
                    return False
                filled = add(filled, fill.quantity)
                if filled > status.request.quantity:
                    self._bad_event(event, R.INVALID_EVENT)
                prior_value = Fraction(status.filled_quantity) * (average or Fraction(0))
                average = (prior_value + Fraction(fill.quantity) * Fraction(fill.price)) / Fraction(filled)
            if ((target == OrderState.FILLED and filled != status.request.quantity)
                    or (target == OrderState.PARTIALLY_FILLED and not (0 < filled < status.request.quantity))):
                self._bad_event(event, R.INVALID_EVENT)
            allowed = {
                OrderState.SUBMITTED: {OrderState.ACKNOWLEDGED, OrderState.WORKING, OrderState.PARTIALLY_FILLED,
                                       OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED, OrderState.EXPIRED},
                OrderState.ACKNOWLEDGED: {OrderState.WORKING, OrderState.PARTIALLY_FILLED, OrderState.FILLED,
                                          OrderState.REJECTED, OrderState.CANCELLED, OrderState.EXPIRED},
                OrderState.WORKING: {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.REJECTED,
                                     OrderState.CANCELLED, OrderState.EXPIRED},
                OrderState.PARTIALLY_FILLED: {OrderState.FILLED, OrderState.CANCELLED, OrderState.EXPIRED},
            }
            if target != status.state and target not in allowed.get(status.state, set()):
                self._bad_event(event, R.INVALID_TRANSITION)
            if fill is not None and status.state in TERMINAL_STATES:
                self._bad_event(event, R.RECONCILIATION_REQUIRED)
            updated = replace(status, state=target, filled_quantity=filled,
                              average_fill_price=average, updated_at=event.received_at)
            self._orders[event.broker_order_id] = updated
            if fill is not None:
                self._fills[fill.fill_id] = fill
            self._events[event.event_id] = event
            details = [('event_id', event.event_id), ('occurred_at', event.occurred_at),
                       ('submitted_at', submitted_at),
                       ('filled_quantity', filled), ('remaining_quantity', updated.remaining_quantity)]
            if fill is not None:
                details.extend((('fill_id', fill.fill_id), ('fill_quantity', fill.quantity),
                                ('fill_price', fill.price), ('executed_at', fill.executed_at)))
            self._record(event.kind.value, event.received_at, order=status.request,
                         order_id=event.broker_order_id, state=target, details=tuple(details),
                         reasons=(R.BROKER_REJECTED,) if target == OrderState.REJECTED else (),
                         error_code=event.error_code)
            return True
