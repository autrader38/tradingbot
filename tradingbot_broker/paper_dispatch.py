"""Internal offline NEW-entry bridge. No Broker implementation or connect API.

Private Python boundaries are application isolation, not a hostile-code sandbox.
"""

from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from threading import RLock

from .ibkr import IBKRContract
from .ibkr_readonly import TWSReadOnlyConfig
from .ibkr_readonly_broker import ReadOnlyIBKRBroker
from .ibkr_paper_transport import (PaperTWSTransport, PaperTWSConfig,
    _StockMarketSpec, _DispatchResult, _DispatchState, _Operation, _valid_order_id)
from .models import (OrderRequest, RiskPermission, TradingMode, Side, OrderType,
    OrderIntent, TimeInForce, BrokerReason, AccountSnapshot, identifier)
from .paper_execution import (PaperExecutionScope, PaperExecutionAuthorizationStatus,
    _timestamp_value)
from .readonly_models import AccountMode, ReadOnlyError
from .safety import Controls, submission_gates


class _BridgeState(StrEnum):
    DENIED = 'DENIED'
    DISPATCHED_PENDING_CONFIRMATION = 'DISPATCHED_PENDING_CONFIRMATION'
    OUTCOME_UNKNOWN = 'OUTCOME_UNKNOWN'


class _BridgeReason(StrEnum):
    LOCAL_GATE_FAILED = 'LOCAL_GATE_FAILED'
    DUPLICATE_ORDER = 'DUPLICATE_ORDER'
    PENDING_CONFIRMATION = 'PENDING_CONFIRMATION'
    RECONCILIATION_REQUIRED = 'RECONCILIATION_REQUIRED'
    SDK_RETURNED = 'SDK_RETURNED'
    DISPATCH_UNCERTAIN = 'DISPATCH_UNCERTAIN'


@dataclass(frozen=True, slots=True)
class _BridgeResult:
    client_order_id: str
    state: _BridgeState
    reason: _BridgeReason
    timestamp: datetime
    order_id: int | None = None
    reconciliation_required: bool = False
    pending_confirmation: bool = False

    def __post_init__(self):
        identifier(self.client_order_id)
        _timestamp_value(self.timestamp)
        if (type(self.state) is not _BridgeState or type(self.reason) is not _BridgeReason
                or type(self.reconciliation_required) is not bool
                or type(self.pending_confirmation) is not bool
                or (self.order_id is not None and not _valid_order_id(self.order_id))):
            raise ReadOnlyError('INVALID_PAPER_BRIDGE_RESULT')


@dataclass(frozen=True, slots=True, repr=False)
class _Attempt:
    order: OrderRequest
    permission: RiskPermission
    capability: object
    snapshots: tuple
    binding: tuple

    def __repr__(self):
        return '<PrivatePaperDispatchAttempt>'


_ORDER_TYPES = dict(client_order_id=str, security_id=str, symbol=str, mode=TradingMode,
    side=Side, order_type=OrderType, quantity=Decimal, currency=str, created_at=datetime,
    expires_at=datetime, intent=OrderIntent, time_in_force=TimeInForce,
    limit_price=Decimal, stop_price=Decimal)
_CONTRACT_TYPES = dict(security_id=str, symbol=str, con_id=int, exchange=str,
    currency=str, valid_from=datetime, valid_until=datetime, available_at=datetime,
    source_id=str, verified=bool, security_type=str)
_PERMISSION_TYPES = dict(permits_entry=bool, available_at=datetime, valid_until=datetime,
    source_id=str, reason_code=str)


def _snapshot(value, model, types):
    """Validate concrete fields before equality; keep independent primitive values."""
    if type(value) is not model:
        raise ReadOnlyError('INVALID_PAPER_BRIDGE_INPUT')
    result = []
    for name, expected in types.items():
        item = getattr(value, name)
        if model is OrderRequest and name in ('limit_price', 'stop_price') and item is None:
            result.append(None)
            continue
        if type(item) is not expected:
            raise ReadOnlyError('INVALID_PAPER_BRIDGE_INPUT')
        if expected is datetime:
            item = _timestamp_value(item)
        elif expected is Decimal:
            if not item.is_finite():
                raise ReadOnlyError('INVALID_PAPER_BRIDGE_INPUT')
            item = item.as_tuple()
        elif issubclass(expected, StrEnum):
            item = item.value
        result.append(item)
    value.__post_init__()
    return tuple(result)


def _order_snapshot(order):
    return _snapshot(order, OrderRequest, _ORDER_TYPES)


def _permission_snapshot(permission):
    values = _snapshot(permission, RiskPermission, _PERMISSION_TYPES)
    return (_order_snapshot(permission.order), values)


class _PaperOrderDispatchCoordinator:
    """Offline only. Every dispatch entry and state/result API is private.

    Order: coordinator -> broker -> read lifecycle -> read condition -> paper
    dispatch -> paper arrival. No callback wait, poll or worker join in this section.
    Preliminary checks release locks before translation; final commitment reacquires
    the same order and holds it until the single synchronous SDK invocation returns.
    """
    def __init__(self, control, write_transport, contracts):
        if type(control) is not ReadOnlyIBKRBroker or type(write_transport) is not PaperTWSTransport:
            raise ReadOnlyError('EXACT_PAPER_BRIDGE_COMPONENTS_REQUIRED')
        if type(contracts) is not tuple:
            raise ReadOnlyError('IMMUTABLE_IBKR_CONTRACTS_REQUIRED')
        try:
            self._contract_values = tuple(_snapshot(c, IBKRContract, _CONTRACT_TYPES) for c in contracts)
            if (len({c.security_id for c in contracts}) != len(contracts)
                    or len({c.con_id for c in contracts}) != len(contracts)):
                raise ValueError()
        except Exception:
            raise ReadOnlyError('INVALID_PAPER_CONTRACT_MAPPING') from None
        self._control, self._write, self._contracts = control, write_transport, contracts
        self._lock = RLock()
        self._attempts = set()
        self._pending_confirmation = False
        self._reconciliation_required = False
        self._active_attempt = None
        self._prepared_attempt = None
        self._pair_values = self._pairing()
        read = control._transport
        with self._lock, control._lock, read._lifecycle, read._condition, write_transport._lock:
            write_transport._claim_dispatch_coordinator(self)

    def __repr__(self):
        return '<OfflinePaperOrderDispatchCoordinator>'

    def _pairing(self, *, validate=True):
        read, write = self._control._transport.config, self._write._config
        if type(read) is not TWSReadOnlyConfig or type(write) is not PaperTWSConfig:
            raise ReadOnlyError('INVALID_PAPER_PLANE_PAIRING')
        if validate:
            read.__post_init__()
            write.__post_init__()
        if read.host != write.host or read.port != write.port or read.client_id == write.client_id:
            raise ReadOnlyError('INVALID_PAPER_PLANE_PAIRING')
        return tuple(getattr(c, f.name) for c in (read, write) for f in fields(c))

    def _check_current_observation(self, generation, at, *, collect):
        read = self._control._transport
        if collect:
            read._validate_paper_execution_observation(generation, at)
            return
        # The same read lifecycle/condition locks remain held after authenticated
        # preparation. Recheck local state without configuration validation, which
        # can load timezone data. No SDK request, file read or callback wait here.
        if (generation != read._generation or read._client is None or read._failure is not None
                or read._disconnect_emitted or not read._ready or read._snapshot is None
                or read._collecting or len(read._accounts) != 1):
            raise ReadOnlyError('PAPER_EXECUTION_CONNECTION_UNUSABLE')
        account = read._snapshot.account
        if (account.verified is not True or account.mode is not None
                or not account.available_at <= at < account.valid_until):
            raise ReadOnlyError('PAPER_EXECUTION_ACCOUNT_STALE')

    def _translate(self, order):
        contract = next((c for c in self._contracts if c.security_id == order.security_id), None)
        if contract is None:
            raise ReadOnlyError('PAPER_CONTRACT_UNAVAILABLE')
        return _StockMarketSpec(contract.con_id, order.side, order.quantity)

    def _check(self, order, permission, capability, snapshots, binding=None, *, collect=True):
        control, read, write = self._control, self._control._transport, self._write
        if (self._reconciliation_required or control._reconciliation_required
                or write._requires_reconciliation()):
            self._mark_reconciliation()
            raise ReadOnlyError('PAPER_WRITE_RECONCILIATION_REQUIRED')
        pair = self._pairing(validate=collect)
        if (self._pending_confirmation or len(pair) != len(self._pair_values)
                or any(type(current) is not type(original)
                       for current, original in zip(pair, self._pair_values))
                or pair != self._pair_values):
            raise ReadOnlyError('PAPER_BRIDGE_UNAVAILABLE')
        # Authenticated enrollment collection + current SDK state + dual clocks.
        if collect:
            control._check_paper_execution(control._processing_time())
        authority = control._paper_authority
        if not authority._accepts(capability, PaperExecutionScope.PLACE_ORDER):
            raise ReadOnlyError('PAPER_PLACE_AUTHORIZATION_REQUIRED')
        current_binding = (authority._binding, control._account, write._generation)
        if binding is not None and (current_binding[0] != binding[0]
                or current_binding[1] is not binding[1] or current_binding[2] != binding[2]):
            raise ReadOnlyError('PAPER_BRIDGE_BINDING_CHANGED')
        if (read._current_sdk_connection_evidence(capability.generation) is not True
                or not write._connected()):
            raise ReadOnlyError('PAPER_BRIDGE_DISCONNECTED')
        # Sample after ALL external/local SDK evidence collection, then pure gates.
        at = control._processing_time()
        mono = control._authorization_monotonic()
        reason = authority._expiry_reason(at, mono)
        if reason is not None:
            control._expire_paper_execution(reason, at)
            raise ReadOnlyError('PAPER_PLACE_AUTHORIZATION_REQUIRED')
        self._check_current_observation(capability.generation, at, collect=collect)
        if not authority._accepts(capability, PaperExecutionScope.PLACE_ORDER):
            raise ReadOnlyError('PAPER_PLACE_AUTHORIZATION_REQUIRED')
        if (_order_snapshot(order), _permission_snapshot(permission)) != snapshots:
            raise ReadOnlyError('PAPER_BRIDGE_INPUT_CHANGED')
        if tuple(_snapshot(c, IBKRContract, _CONTRACT_TYPES) for c in self._contracts) != self._contract_values:
            raise ReadOnlyError('PAPER_CONTRACT_MAPPING_CHANGED')
        account = control._account
        if (control.mode is not TradingMode.PAPER or control.account_mode is not AccountMode.UNKNOWN
                or type(account) is not AccountSnapshot or account.mode is not None
                or account.verified is not True or control._reconciliation_required):
            raise ReadOnlyError('PAPER_UNKNOWN_MODE_POLICY_REQUIRED')
        if (order.mode is not TradingMode.PAPER or order.intent is not OrderIntent.ENTRY
                or order.side is not Side.BUY or order.order_type is not OrderType.MARKET
                or order.time_in_force is not TimeInForce.DAY
                or order.limit_price is not None or order.stop_price is not None):
            raise ReadOnlyError('UNSUPPORTED_PAPER_ENTRY_SHAPE')
        # Ephemeral gate controls only, created AFTER authentic PLACE authorization.
        controls = Controls(True, control._controls.entries_paused, control._controls.emergency_stopped)
        if permission.permits_entry is not True or _order_snapshot(permission.order) != snapshots[0]:
            raise ReadOnlyError('PAPER_RISK_PERMISSION_MISMATCH')
        contract = next((c for c in self._contracts if c.security_id == order.security_id), None)
        if (contract is None or contract.verified is not True or contract.security_type != 'STK'
                or contract.exchange != 'SMART' or contract.symbol != order.symbol
                or contract.currency != order.currency):
            raise ReadOnlyError('PAPER_CONTRACT_UNAVAILABLE')
        # Final sample follows input integrity/contract translation checks too.
        at = control._processing_time()
        reason = authority._expiry_reason(at, control._authorization_monotonic())
        if reason is not None:
            control._expire_paper_execution(reason, at)
            raise ReadOnlyError('PAPER_PLACE_AUTHORIZATION_REQUIRED')
        self._check_current_observation(capability.generation, at, collect=collect)
        if not authority._accepts(capability, PaperExecutionScope.PLACE_ORDER):
            raise ReadOnlyError('PAPER_PLACE_AUTHORIZATION_REQUIRED')
        failures = submission_gates(control.mode, control._connection, account, controls,
                                    order, permission, at)
        if (type(failures) is not tuple or len(failures) != 1
                or type(failures[0]) is not BrokerReason
                or failures[0] is not BrokerReason.ACCOUNT_MODE_UNVERIFIED):
            raise ReadOnlyError('PAPER_ENTRY_GATE_FAILED')
        if not contract.available_at <= at or not contract.valid_from <= at < contract.valid_until:
            raise ReadOnlyError('PAPER_CONTRACT_UNAVAILABLE')
        return current_binding, at

    def _require_outer_locks(self):
        read = self._control._transport
        for lock in (self._lock, self._control._lock, read._lifecycle, read._condition, self._write._lock):
            if not lock._is_owned():
                raise ReadOnlyError('PAPER_COMMIT_LOCKS_REQUIRED')

    def _prepare_commit(self):
        self._require_outer_locks()
        attempt = self._active_attempt
        if (type(attempt) is not _Attempt or self._write._dispatch_coordinator_owner is not self
                or attempt.order.client_order_id not in self._attempts):
            raise ReadOnlyError('PAPER_COMMIT_ATTEMPT_REQUIRED')
        self._check(attempt.order, attempt.permission, attempt.capability,
                    attempt.snapshots, attempt.binding)
        self._prepared_attempt = attempt

    def _validate_commit(self, challenge):
        self._require_outer_locks()
        if not self._write._arrival_lock._is_owned():
            raise ReadOnlyError('PAPER_COMMIT_LOCKS_REQUIRED')
        attempt = self._active_attempt
        if (type(challenge) is not object or type(attempt) is not _Attempt
                or attempt is not self._prepared_attempt
                or self._write._dispatch_coordinator_owner is not self
                or attempt.order.client_order_id not in self._attempts):
            raise ReadOnlyError('PAPER_COMMIT_ATTEMPT_REQUIRED')
        # Enrollment was freshly authenticated under the same held read locks.
        # Recheck that binding and current evidence; no file read in commitment.
        self._check(attempt.order, attempt.permission, attempt.capability,
                    attempt.snapshots, attempt.binding, collect=False)
        return challenge

    def _mark_reconciliation(self, *, audit=True):
        # Safety first, before optional audit/report construction can fail.
        if not self._control._lock._is_owned():
            raise ReadOnlyError('PAPER_RECONCILIATION_LOCK_REQUIRED')
        changed = not self._reconciliation_required
        self._reconciliation_required = True
        self._write._latch_reconciliation()
        control = self._control
        control._reconciliation_required = True
        control._paper_authority._revoke(PaperExecutionAuthorizationStatus.INVALIDATED)
        if changed and audit:
            try:
                control._authorization_audit('PAPER_EXECUTION_INVALIDATED', 'PAPER_WRITE_RECONCILIATION_REQUIRED')
            except Exception:
                pass

    def _dispatch_entry(self, order, permission, capability):
        # A typed bounded request ID is consumed even if later gates deny it.
        if type(order) is not OrderRequest:
            raise ReadOnlyError('INVALID_PAPER_BRIDGE_INPUT')
        identifier(order.client_order_id)
        client_id = order.client_order_id
        with self._lock:
            def result(state, reason, order_id=None):
                if self._reconciliation_required:
                    state, reason = _BridgeState.OUTCOME_UNKNOWN, _BridgeReason.RECONCILIATION_REQUIRED
                try:
                    return _BridgeResult(client_id, state, reason, self._control._processing_time(),
                        order_id, self._reconciliation_required, self._pending_confirmation)
                except Exception:
                    raise ReadOnlyError('PAPER_BRIDGE_RESULT_UNAVAILABLE') from None
            control, read, write = self._control, self._control._transport, self._write
            # Fatal transport state outranks EVERY duplicate/pending fast return.
            with control._lock:
                if (self._reconciliation_required or control._reconciliation_required
                        or write._requires_reconciliation()):
                    self._attempts.add(client_id)
                    self._mark_reconciliation()
                    return result(_BridgeState.OUTCOME_UNKNOWN, _BridgeReason.RECONCILIATION_REQUIRED)
                if client_id in self._attempts:
                    return result(_BridgeState.DENIED, _BridgeReason.DUPLICATE_ORDER)
                if self._pending_confirmation:
                    self._attempts.add(client_id)
                    return result(_BridgeState.DENIED, _BridgeReason.PENDING_CONFIRMATION)
            self._attempts.add(client_id)
            try:
                snapshots = (_order_snapshot(order), _permission_snapshot(permission))
                # Poll outside lifecycle/callback/write locks; disconnect may join a reader.
                control.poll()
                with control._lock, read._lifecycle, read._condition, write._lock:
                    binding, _ = self._check(order, permission, capability, snapshots)
                spec = self._translate(order)
            except Exception:
                return result(_BridgeState.DENIED, _BridgeReason.LOCAL_GATE_FAILED)
            with control._lock, read._lifecycle, read._condition, write._lock:
                try:
                    self._check(order, permission, capability, snapshots, binding)
                except Exception:
                    return result(_BridgeState.DENIED, _BridgeReason.LOCAL_GATE_FAILED)
                self._active_attempt = _Attempt(order, permission, capability, snapshots, binding)
                try:
                    outcome = write._dispatch_new(spec)
                except BaseException as error:
                    if not isinstance(error, Exception):
                        # C1 only latches post-invocation uncertainty. A preflight
                        # interruption still consumes reservations, without making
                        # a false claim that SDK dispatch began.
                        if write._requires_reconciliation():
                            self._mark_reconciliation(audit=False)
                        raise
                    self._mark_reconciliation()
                    return result(_BridgeState.OUTCOME_UNKNOWN, _BridgeReason.DISPATCH_UNCERTAIN)
                finally:
                    self._active_attempt = self._prepared_attempt = None
                if (type(outcome) is not _DispatchResult or outcome.operation is not _Operation.PLACE_ORDER):
                    self._mark_reconciliation()
                    return result(_BridgeState.OUTCOME_UNKNOWN, _BridgeReason.DISPATCH_UNCERTAIN)
                if outcome.state is _DispatchState.NOT_DISPATCHED:
                    if outcome.reconciliation_required or write._requires_reconciliation():
                        self._mark_reconciliation()
                    return result(_BridgeState.DENIED, _BridgeReason.LOCAL_GATE_FAILED, outcome.order_id)
                if (outcome.state is _DispatchState.OUTCOME_UNKNOWN or outcome.reconciliation_required
                        or write._requires_reconciliation()):
                    self._mark_reconciliation()
                    return result(_BridgeState.OUTCOME_UNKNOWN, _BridgeReason.DISPATCH_UNCERTAIN, outcome.order_id)
                if outcome.state is not _DispatchState.DISPATCHED:
                    self._mark_reconciliation()
                    return result(_BridgeState.OUTCOME_UNKNOWN, _BridgeReason.DISPATCH_UNCERTAIN)
                self._pending_confirmation = True  # Secure before result construction.
                return result(_BridgeState.DISPATCHED_PENDING_CONFIRMATION, _BridgeReason.SDK_RETURNED,
                              outcome.order_id)
