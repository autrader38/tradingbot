"""Read-only observations behind the existing account/connection safety boundary."""

from .broker import SimulatedBroker, BrokerOperationError
from .models import (BrokerReason as R, ConnectionStatus, EventKind, TradingMode)
from .readonly_models import AccountMode, ReadOnlyError
from .ibkr_readonly import ReadOnlyTWSTransport, utc_now
from .paper_enrollment import PaperEnrollmentStatus, require_confirmation
from .paper_execution import (ARM_CONFIRMATION, DEFAULT_PAPER_SCOPES,
    MAX_AUTHORIZATION_LIFETIME,
    PaperExecutionAuthorizationStatus as AuthorizationStatus, _PaperExecutionAuthority)
from time import monotonic
from tradingbot_backtest.market import validate_timestamp


class ReadOnlyIBKRBroker(SimulatedBroker):
    def __init__(self, transport: ReadOnlyTWSTransport, *, clock=utc_now,
                 authorization_monotonic=monotonic):
        if type(transport) is not ReadOnlyTWSTransport:
            raise TypeError('Read-only TWS transport required')
        super().__init__(TradingMode.PAPER)  # Requested mode, never account proof.
        self._transport = transport
        self._clock = clock
        self._observation = None
        self._pending_account = None
        self._cleanup_error = None
        self._paper_authority = _PaperExecutionAuthority()
        self._authorization_monotonic = authorization_monotonic

    def _authorization_audit(self, action, reason, at=None):
        try:
            now = self._processing_time(at)
            self._time(now)
        except Exception:
            # Disarm/revocation must still succeed with a failed injected clock.
            now = max(value for value in (utc_now(), self._last_at) if value is not None)
        self._record(action, now, details=(('authorization_status', self._paper_authority.status.value),
            ('reason_code', reason)))

    def _invalidate_paper_execution(self, reason, at=None):
        if self._paper_authority.status is AuthorizationStatus.ARMED:
            self._paper_authority._revoke(AuthorizationStatus.INVALIDATED)
            self._authorization_audit('PAPER_EXECUTION_INVALIDATED', reason, at)

    def _paper_context(self, at):
        self._paper_eligibility()
        account = super().account_summary(at)
        generation, revision, valid_until = self._transport._paper_execution_binding()
        config = self._transport.config
        return (generation, self._connection_at, revision, config.host, config.port,
                config.client_id, config.read_only), min(account.valid_until, valid_until)

    def _paper_eligibility(self):
        if self.mode is not TradingMode.PAPER:
            raise ReadOnlyError('LIVE_EXECUTION_DISABLED')
        if self._connection is not ConnectionStatus.CONNECTED:
            raise ReadOnlyError('PAPER_EXECUTION_DISCONNECTED')
        if self._controls.emergency_stopped:
            raise ReadOnlyError('PAPER_EXECUTION_EMERGENCY_STOP')
        if self._reconciliation_required:
            raise ReadOnlyError('PAPER_EXECUTION_RECONCILIATION_REQUIRED')

    def _final_paper_context(self, binding, valid_until):
        # Refresh local SDK state after collection, then sample both clocks.
        # No request, callback wait or worker shutdown happens under these locks.
        sdk_connected = self._transport._current_sdk_connection_evidence(binding[0])
        at = self._processing_time()
        monotonic_at = self._authorization_monotonic()
        self._paper_authority._time(monotonic_at)
        if sdk_connected is not True:
            raise ReadOnlyError('PAPER_EXECUTION_SDK_DISCONNECTED' if sdk_connected is False
                                else 'PAPER_EXECUTION_SDK_UNAVAILABLE')
        self._paper_eligibility()
        account = super().account_summary(at)
        self._transport._validate_paper_execution_observation(binding[0], at)
        config = self._transport.config
        if binding[1] != self._connection_at or binding[3:] != (
                config.host, config.port, config.client_id, config.read_only):
            raise ReadOnlyError('PAPER_EXECUTION_BINDING_CHANGED')
        if at >= valid_until:
            raise ReadOnlyError('PAPER_EXECUTION_ACCOUNT_STALE')
        return at, monotonic_at, min(valid_until, account.valid_until)

    def _expire_paper_execution(self, reason, at):
        status = (AuthorizationStatus.EXPIRED if reason == 'PAPER_EXECUTION_EXPIRED'
                  else AuthorizationStatus.INVALIDATED)
        self._paper_authority._revoke(status)
        self._authorization_audit('PAPER_EXECUTION_' + status.value, reason, at)

    def _check_paper_execution(self, at):
        authority = self._paper_authority
        try:
            reason = authority._expiry_reason(at, self._authorization_monotonic())
            if reason is not None:
                self._expire_paper_execution(reason, at)
                return
            binding, valid_until = self._paper_context(at)
            # Check fixed deadlines before account staleness to preserve EXPIRED.
            final_at = self._processing_time()
            final_monotonic = self._authorization_monotonic()
            reason = authority._expiry_reason(final_at, final_monotonic)
            if reason is not None:
                self._expire_paper_execution(reason, final_at)
                return
            final_at, final_monotonic, _ = self._final_paper_context(binding, valid_until)
            reason = authority._expiry_reason(final_at, final_monotonic)
            if reason is not None:
                self._expire_paper_execution(reason, final_at)
                return
            if authority.status is AuthorizationStatus.ARMED and binding != authority._binding:
                self._invalidate_paper_execution('PAPER_EXECUTION_BINDING_CHANGED', at)
            elif authority.status is AuthorizationStatus.UNAVAILABLE:
                authority._revoke(AuthorizationStatus.DISARMED)
        except Exception:
            if authority.status is AuthorizationStatus.ARMED:
                self._invalidate_paper_execution('PAPER_EXECUTION_EVIDENCE_UNAVAILABLE', at)
            elif authority.status is AuthorizationStatus.DISARMED:
                authority._revoke(AuthorizationStatus.UNAVAILABLE)

    @property
    def paper_execution_status(self):
        with self._lock:
            try:
                self.poll()
                with self._transport._lifecycle, self._transport._condition:
                    self._check_paper_execution(self._processing_time())
            except Exception:
                self._invalidate_paper_execution('PAPER_EXECUTION_EVIDENCE_UNAVAILABLE')
                if self._paper_authority.status is AuthorizationStatus.DISARMED:
                    self._paper_authority._revoke(AuthorizationStatus.UNAVAILABLE)
            return self._paper_authority.status

    def arm_paper_execution(self, confirmation, *, scopes=DEFAULT_PAPER_SCOPES):
        with self._lock:
            try:
                if type(confirmation) is not str or confirmation != ARM_CONFIRMATION:
                    raise ReadOnlyError('PAPER_EXECUTION_CONFIRMATION_REQUIRED')
                self.poll()
                with self._transport._lifecycle, self._transport._condition:
                    at = self._processing_time()
                    self._time(at)
                    initial_monotonic = self._authorization_monotonic()
                    self._paper_authority._time(initial_monotonic)
                    binding, valid_until = self._paper_context(at)
                    proposed_expiry = min(at + MAX_AUTHORIZATION_LIFETIME, valid_until)
                    proposed_deadline = initial_monotonic + (proposed_expiry - at).total_seconds()
                    final_at, final_monotonic, valid_until = self._final_paper_context(binding, valid_until)
                    if final_at >= proposed_expiry or final_monotonic >= proposed_deadline:
                        raise ReadOnlyError('PAPER_EXECUTION_EXPIRED')
                    capability = self._paper_authority._issue(binding, final_at,
                        final_monotonic, min(valid_until, proposed_expiry), scopes, proposed_deadline)
                    self._record('PAPER_EXECUTION_ARMED', final_at, details=(
                        ('authorization_status', AuthorizationStatus.ARMED.value),
                        ('scopes', ','.join(scope.value for scope in capability.scopes)),
                        ('generation', capability.generation),
                        ('expires_at', capability.expires_at),
                        ('reason_code', 'EXPLICIT_USER_ARM'),
                        ('source', 'LOCAL_USER_SESSION_AUTHORIZATION')))
                    return capability
            except Exception as error:
                self._invalidate_paper_execution('PAPER_EXECUTION_ARM_FAILED')
                self._authorization_audit('PAPER_EXECUTION_ARM_FAILED', 'PAPER_EXECUTION_ARM_DENIED')
                code = error.code if isinstance(error, ReadOnlyError) else 'PAPER_EXECUTION_ARM_DENIED'
                raise ReadOnlyError(code) from None

    def disarm_paper_execution(self):
        with self._lock:
            if self._paper_authority._revoke(AuthorizationStatus.DISARMED):
                self._authorization_audit('PAPER_EXECUTION_DISARMED', 'EXPLICIT_USER_DISARM')
            return AuthorizationStatus.DISARMED

    def validate_paper_execution(self, capability, scope):
        # This validates only user authorization, never a complete order permission.
        with self._lock:
            try:
                self.poll()
                with self._transport._lifecycle, self._transport._condition:
                    self._check_paper_execution(self._processing_time())
                    return self._paper_authority._accepts(capability, scope)
            except Exception:
                self._invalidate_paper_execution('PAPER_EXECUTION_EVIDENCE_UNAVAILABLE')
                return False

    def emergency_stop(self, at):
        with self._lock:
            try:
                return super().emergency_stop(at)
            finally:
                self._invalidate_paper_execution('PAPER_EXECUTION_EMERGENCY_STOP', at)

    @property
    def account_mode(self):
        return AccountMode.UNKNOWN

    @property
    def paper_enrollment_status(self):
        with self._lock:
            try:
                self.account_summary()
                return self._transport.paper_enrollment_status
            except Exception:
                return PaperEnrollmentStatus.INVALID

    def enroll_paper_account(self, confirmation, *, replace_existing=False):
        require_confirmation(confirmation)
        with self._lock:
            self._invalidate_paper_execution('PAPER_EXECUTION_ENROLLMENT_CHANGED')
            self.account_summary()
            status = self._transport.enroll_paper_account(confirmation, replace_existing=replace_existing)
            self._record('PAPER_ACCOUNT_ENROLLED', self._processing_time(), details=(
                ('enrollment_status', status.value), ('replaced_existing', replace_existing),
                ('account_mode', self.account_mode.value)))
            return status

    def poll(self, at=None):
        with self._lock:
            at = self._processing_time(at)
            try:
                for event in self._transport.drain_events(at):
                    super().handle_event(event)
                if self._connection == ConnectionStatus.CONNECTED and not self._transport.connected:
                    super().disconnect(self._processing_time(at))
                if self._connection is not ConnectionStatus.CONNECTED:
                    self._invalidate_paper_execution('PAPER_EXECUTION_DISCONNECTED', at)
                elif self._paper_authority.status is AuthorizationStatus.ARMED:
                    with self._transport._lifecycle, self._transport._condition:
                        self._check_paper_execution(self._processing_time(at))
                return self._connection
            except Exception as error:
                # Proof is invalidated even if SDK state inspection fails.
                super().disconnect(self._processing_time(at))
                self._invalidate_paper_execution('PAPER_EXECUTION_DISCONNECTED', at)
                code = error.code if isinstance(error, ReadOnlyError) else 'READ_ONLY_EVENT_PROCESSING_FAILED'
                raise ReadOnlyError(code) from None

    def _processing_time(self, at=None):
        if at is not None: validate_timestamp(at)
        return max(x for x in (at, self._clock(), self._last_at) if x is not None)

    @property
    def connection_status(self):
        return self.poll()

    def connect(self, at=None):
        with self._lock:
            self.poll(at)
            if self._connection == ConnectionStatus.CONNECTED:
                return super().connect(self._processing_time(at))
            try:
                observation = self._transport.connect()
            except ReadOnlyError as error:
                now = self._processing_time(at)
                self._time(now)
                self._record('READ_ONLY_CONNECT_FAILED', now, reasons=(R.TRANSPORT_FAILURE,),
                             details=(('setup_code', error.code),))
                raise
            now = self._processing_time(at)
            self._pending_account = observation.account
            try:
                result = super().connect(now)
                if result == ConnectionStatus.CONNECTED:
                    self._observation = observation
                    self._record('READ_ONLY_SNAPSHOT', now, details=(
                        ('account_mode', self.account_mode.value),
                        ('positions', len(observation.account.positions)),
                        ('working_orders', len(observation.working_orders)),
                        ('completed_orders_available', observation.completed_orders_available),
                        ('broker_time', observation.broker_time)))
                else:
                    self._transport.disconnect()
                return result
            except Exception:
                self._transport.disconnect()
                raise
            finally:
                self._pending_account = None

    def refresh(self, at=None):
        # A fresh generation prevents untagged position/order callbacks mixing batches.
        with self._lock:
            self.disconnect(at)
            return self.connect()

    def disconnect(self, at=None):
        with self._lock:
            self._invalidate_paper_execution('PAPER_EXECUTION_DISCONNECTED', at)
            result = super().disconnect(self._processing_time(at))
            if self._cleanup_error is not None:
                raise ReadOnlyError(self._cleanup_error) from None
            return result

    def _backend_connect(self, at):
        if not self._transport.connected:
            raise ReadOnlyError('IB_GATEWAY_DISCONNECTED')
        return self._pending_account

    def _backend_disconnect(self):
        self._cleanup_error = None
        try:
            self._transport.disconnect()
        except ReadOnlyError as error:
            self._cleanup_error = error.code
            raise

    def account_summary(self, at=None):
        with self._lock:
            self.poll(at)
            return super().account_summary(self._processing_time(at))

    def report_account(self, snapshot, at):
        if snapshot.mode is not None:
            raise ReadOnlyError('TWS_ACCOUNT_MODE_NOT_ATTESTED')
        with self._lock:
            try:
                return super().report_account(snapshot, at)
            finally:
                if self._paper_authority.status is AuthorizationStatus.ARMED:
                    with self._transport._lifecycle, self._transport._condition:
                        self._check_paper_execution(self._processing_time(at))

    def handle_event(self, event):
        if event.kind not in (EventKind.DISCONNECTED, EventKind.RECONNECTED):
            raise ReadOnlyError('READ_ONLY_OBSERVATION_REQUIRED')
        if event.account is not None and event.account.mode is not None:
            raise ReadOnlyError('TWS_ACCOUNT_MODE_NOT_ATTESTED')
        with self._lock:
            try:
                return super().handle_event(event)
            finally:
                if self._paper_authority.status is AuthorizationStatus.ARMED:
                    with self._transport._lifecycle, self._transport._condition:
                        self._check_paper_execution(self._processing_time())

    def _read(self, field):
        with self._lock:
            self.account_summary()
            if self._observation is None:
                raise BrokerOperationError(R.ACCOUNT_DATA_UNAVAILABLE)
            return getattr(self._observation, field)

    def working_orders(self):
        return self._read('working_orders')

    def completed_orders(self):
        return self._read('completed_orders')

    def fills(self):
        return self._read('fills')

    def commissions(self):
        return self._read('commissions')

    def account_identities(self):
        return self._read('identities')

    def order_status(self, broker_order_id):
        for order in self.working_orders() + self.completed_orders():
            if order.broker_order_id == broker_order_id:
                return order
        raise BrokerOperationError(R.UNKNOWN_ORDER)

    def _blocked(self, action, at=None, order=None, order_id=None):
        with self._lock:
            now = self._clock() if at is None else at
            self._time(now)
            self._record(action, now, order=order, order_id=order_id,
                         reasons=(R.READ_ONLY_BROKER_TRANSPORT,))
            raise BrokerOperationError(R.READ_ONLY_BROKER_TRANSPORT)

    def place_order(self, order, at, permission=None):
        return self._blocked('READ_ONLY_SUBMIT_BLOCKED', at, order)

    def replace_order(self, broker_order_id, order, at, permission=None):
        return self._blocked('READ_ONLY_REPLACE_BLOCKED', at, order, broker_order_id)

    modify_order = replace_order

    def cancel_order(self, broker_order_id, at):
        return self._blocked('READ_ONLY_CANCEL_BLOCKED', at, order_id=broker_order_id)

    def cancel_working_orders(self, at):
        return self._blocked('READ_ONLY_CANCEL_ALL_BLOCKED', at)

    def flatten_positions(self, at):
        return self._blocked('READ_ONLY_FLATTEN_BLOCKED', at)

    def set_trading_enabled(self, enabled, at):
        if enabled is not False:
            return self._blocked('READ_ONLY_ENABLE_BLOCKED', at)
        return super().set_trading_enabled(False, at)

    def _backend_place(self, order):
        raise BrokerOperationError(R.READ_ONLY_BROKER_TRANSPORT)

    def _backend_replace(self, order_id, order):
        raise BrokerOperationError(R.READ_ONLY_BROKER_TRANSPORT)

    def _backend_cancel(self, order_id):
        raise BrokerOperationError(R.READ_ONLY_BROKER_TRANSPORT)
