"""Read-only observations behind the existing account/connection safety boundary."""

from .broker import SimulatedBroker, BrokerOperationError
from .models import (BrokerReason as R, ConnectionStatus, EventKind, TradingMode)
from .readonly_models import AccountMode, ReadOnlyError
from .ibkr_readonly import ReadOnlyTWSTransport, utc_now
from .paper_enrollment import PaperEnrollmentStatus, require_confirmation
from tradingbot_backtest.market import validate_timestamp


class ReadOnlyIBKRBroker(SimulatedBroker):
    def __init__(self, transport: ReadOnlyTWSTransport, *, clock=utc_now):
        if type(transport) is not ReadOnlyTWSTransport:
            raise TypeError('Read-only TWS transport required')
        super().__init__(TradingMode.PAPER)  # Requested mode, never account proof.
        self._transport = transport
        self._clock = clock
        self._observation = None
        self._pending_account = None
        self._cleanup_error = None

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
                return self._connection
            except Exception as error:
                # Proof is invalidated even if SDK state inspection fails.
                super().disconnect(self._processing_time(at))
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
        return super().report_account(snapshot, at)

    def handle_event(self, event):
        if event.kind not in (EventKind.DISCONNECTED, EventKind.RECONNECTED):
            raise ReadOnlyError('READ_ONLY_OBSERVATION_REQUIRED')
        if event.account is not None and event.account.mode is not None:
            raise ReadOnlyError('TWS_ACCOUNT_MODE_NOT_ATTESTED')
        return super().handle_event(event)

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
