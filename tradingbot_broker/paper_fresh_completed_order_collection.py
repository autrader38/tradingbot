"""Private one-shot untagged completed-order provenance, never lifecycle authority.

Local identity is not broker request correlation. History and scope are limited;
no order/execution absence, ownership, currentness or restart authority is claimed.
"""

from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from math import isfinite
from threading import current_thread
from time import monotonic

from .ibkr_readonly import (ReadOnlyTWSTransport, TWSReadOnlyConfig,
    _FreshCompletedOrderCapture, _FreshOpenOrderCapture, _FreshExecutionCapture,
    _ReconciliationOrderEvidence, _ReconciliationPresence, _RECONCILIATION_CAPACITY,
    _reconciliation_field, _reconciliation_integer, _reconciliation_text,
    _completed_order_keys, _completed_order_agreement)
from .paper_dispatch_journal import _PaperDispatchJournalOwner, _values
from .paper_execution_journal import _JournalAccountBinding
from .ibkr_paper_transport import PaperTWSConfig, PaperTransportIntent
from .readonly_models import ReadOnlyError


__all__ = ()
_ERROR = 'FRESH_COMPLETED_ORDER_COLLECTION_UNAVAILABLE'
_SCOPE = 'DECLARED_COMPLETED_ORDERS_API_ONLY_FALSE_ACCOUNT_FILTERED'
_LIMITATIONS = ('LOCAL_ID_NOT_BROKER_REQUEST_ID',
    'UNSOLICITED_OR_DELAYED_OBSERVATIONS_POSSIBLE', 'HISTORY_LIMITED', 'COMPLETED_ORDER_SCOPE_LIMITED',
    'ABSENCE_IS_NOT_NO_ORDER_PROOF', 'ABSENCE_IS_NOT_NO_EXECUTION_PROOF',
    'NOT_EXECUTION_EVIDENCE', 'NOT_CURRENT_OPEN_ORDER_STATE', 'NOT_AN_ATOMIC_BROKER_SNAPSHOT',
    'NO_NO_LATE_FILL_GUARANTEE', 'NO_MANAGEMENT_CURRENTNESS',
    'NO_ORDER_OR_POSITION_OWNERSHIP', 'NO_ACCOUNTING_OR_TRADING_AUTHORITY')


def _require(condition):
    if not condition:
        raise ReadOnlyError(_ERROR)


def _clock():
    value = monotonic()
    _require(type(value) in (int, float) and isfinite(value) and value >= 0)
    return value


class _CollectionOutcome(StrEnum):
    COMPLETED = 'COMPLETED_DECLARED_COMPLETED_ORDER_COLLECTION_INTERVAL'
    FAILED = 'FAILED'
    TIMED_OUT = 'TIMED_OUT'
    INVALIDATED = 'INVALIDATED'
    OVERFLOW = 'OVERFLOW'
    INTERRUPTED = 'INTERRUPTED'


class _SDKRequestResult(StrEnum):
    NOT_ATTEMPTED = 'NOT_ATTEMPTED'
    ATTEMPTED = 'ATTEMPTED'
    RETURNED_NORMALLY = 'RETURNED_NORMALLY'
    RAISED_EXCEPTION = 'RAISED_EXCEPTION'
    INTERRUPTED = 'INTERRUPTED'


def _observation_copy(event):
    """Recheck existing producer primitives and preserve representation, not economics."""
    _require(type(event) is _ReconciliationOrderEvidence and type(event.source) is str
             and event.source == 'COMPLETED_ORDER' and type(event.encoding) is str
             and event.encoding in ('LEGACY', 'PROTOBUF'))
    _reconciliation_integer(event.generation, positive=True, wide=True)
    _reconciliation_integer(event.sequence, positive=True, wide=True)
    p = event.presence
    _require(type(p) is _ReconciliationPresence and type(p.supplied) is frozenset
             and all(type(v) is str for v in p.supplied))
    required = {'con_id', 'sec_type', 'exchange', 'symbol', 'currency',
                'action', 'quantity', 'order_type', 'tif'}
    _require(required <= p.supplied and p.supplied <= required | {
        'order_id', 'client_id', 'perm_id', 'order_ref', 'top_order_id', 'order_state', 'status'})
    for item in fields(p):
        if item.name != 'supplied':
            _require(type(getattr(p, item.name)) is bool)
    for name in ('order_id', 'client_id', 'perm_id', 'order_ref'):
        supplied = name in p.supplied
        _require(getattr(p, name) is supplied)
        value = getattr(event, name)
        _require(supplied or value is None)
        if supplied and name in ('order_id', 'client_id'):
            _require(type(value) is int)
        if value is not None:
            _require(_reconciliation_field(name, value) == value)
    _require(not p.top_order_id and 'top_order_id' not in p.supplied
             and p.nested_order_id == (event.encoding == 'PROTOBUF' and 'order_id' in p.supplied)
             and p.order_state == ('order_state' in p.supplied)
             and p.status == ('status' in p.supplied)
             and p.status == (event.status is not None) and (not p.status or p.order_state))
    _require(event.encoding != 'LEGACY' or event.order_id is event.client_id is None
             and not p.order_id and not p.client_id and not p.nested_order_id)
    _require(p.order_state and p.status)
    for name in required:
        value = getattr(event, name)
        _require(type(value) is (Decimal if name == 'quantity' else int if name == 'con_id' else str))
        _require(_reconciliation_field(name, value) == value)
    _require(type(event.status) is str and event.status in {
        'PendingSubmit', 'PreSubmitted', 'Submitted', 'Filled', 'Cancelled',
        'ApiCancelled', 'Inactive', 'Expired', 'UNKNOWN'})
    return replace(event, quantity=Decimal(event.quantity.as_tuple()),
                   presence=replace(p, supplied=frozenset(p.supplied)))


def _observation_values(event):
    copied = _observation_copy(event)
    return tuple((f.name, tuple((p.name, tuple(sorted(copied.presence.supplied))
                  if p.name == 'supplied' else _values(getattr(copied.presence, p.name)))
                  for p in fields(copied.presence)) if f.name == 'presence'
                  else _values(getattr(copied, f.name))) for f in fields(copied))


@dataclass(frozen=True, slots=True, repr=False)
class _FreshCompletedOrderReceipt:
    local_collection_id: int
    scope: str
    account_binding: _JournalAccountBinding
    journal_id: str
    session_id: str
    read_generation: int
    write_generation: int
    read_client_id: int
    placing_client_id: int
    issuance_marker: int
    completion_marker: int | None
    initial_sequence_watermark: int
    final_sequence_watermark: int
    sdk_result: _SDKRequestResult
    completion_observed: bool
    outcome: _CollectionOutcome
    authentication_deadline: datetime
    boundary_changed: bool
    observations: tuple[_ReconciliationOrderEvidence, ...]
    coverage_limitations: tuple[str, ...] = _LIMITATIONS

    def __post_init__(self):
        for name in ('local_collection_id', 'read_generation', 'write_generation',
                     'read_client_id', 'placing_client_id', 'issuance_marker'):
            _reconciliation_integer(getattr(self, name), positive=True, wide=True)
        for name in ('initial_sequence_watermark', 'final_sequence_watermark'):
            _reconciliation_integer(getattr(self, name), wide=True)
        _require(self.scope == _SCOPE and type(self.scope) is str
                 and type(self.account_binding) is _JournalAccountBinding
                 and type(self.sdk_result) is _SDKRequestResult
                 and type(self.outcome) is _CollectionOutcome
                 and type(self.completion_observed) is bool and type(self.boundary_changed) is bool
                 and self.final_sequence_watermark >= self.initial_sequence_watermark
                 and type(self.coverage_limitations) is tuple and self.coverage_limitations == _LIMITATIONS)
        _JournalAccountBinding.__post_init__(self.account_binding)
        _reconciliation_text(self.journal_id); _reconciliation_text(self.session_id)
        deadline = self.authentication_deadline
        _require(type(deadline) is datetime and deadline.tzinfo is timezone.utc)
        _require(self.completion_observed == (self.completion_marker is not None))
        if self.completion_marker is not None:
            _reconciliation_integer(self.completion_marker, positive=True, wide=True)
            _require(self.completion_marker > self.issuance_marker)
        _require(type(self.observations) is tuple and len(self.observations) <= _RECONCILIATION_CAPACITY)
        observations = tuple(_observation_copy(e) for e in self.observations)
        identities = {}
        for sequence, event in enumerate(observations, self.initial_sequence_watermark + 1):
            _require(event.sequence == sequence and event.generation == self.read_generation
                     and sequence <= self.final_sequence_watermark)
            for key in _completed_order_keys(event):
                prior = identities.setdefault(key, event)
                _require(_completed_order_agreement(prior, event))
        if self.outcome is _CollectionOutcome.COMPLETED:
            _require(self.sdk_result is _SDKRequestResult.RETURNED_NORMALLY
                     and self.completion_observed and not self.boundary_changed
                     and self.final_sequence_watermark == self.initial_sequence_watermark + len(observations))
        object.__setattr__(self, 'observations', observations)

    @property
    def count(self): return len(self.observations)

    @property
    def first_observation_sequence(self):
        return self.observations[0].sequence if self.observations else None

    @property
    def last_observation_sequence(self):
        return self.observations[-1].sequence if self.observations else None

    def __repr__(self): return '<PrivateFreshCompletedOrderCollectionReceipt evidence-only>'


def _receipt_values(receipt):
    _require(type(receipt) is _FreshCompletedOrderReceipt)
    # Validate an independent reconstruction, never silently normalize a forged receipt.
    checked = replace(receipt)
    return tuple((f.name, tuple(_observation_values(e) for e in checked.observations)
                  if f.name == 'observations' else _values(getattr(checked, f.name))
                  if f.name not in ('sdk_result', 'outcome') else getattr(checked, f.name).value)
                 for f in fields(checked))


class _FreshCompletedOrderCollectionOwner:
    __slots__ = ('__association', '__identity', '__account_association', '__clients',
                 '__deadline', '__monotonic_deadline', '__clock', '__started', '__failed',
                 '__receipt', '__receipt_values', '__consumed', '__capture', '__baseline', '__sdk_result',
                 '__read_snapshot', '__request', '__read_client_id', '__server_version', '__api', '__support_values',
                 '__capture_identity', '__journal_history', '__configs', '__config_values')

    def __init__(self, *args, **kwargs): raise ReadOnlyError(_ERROR)
    def __repr__(self): return '<PrivateFreshCompletedOrderCollectionOwner evidence-only>'
    def __setattr__(self, name, value): raise ReadOnlyError(_ERROR)
    def __delattr__(self, name): raise ReadOnlyError(_ERROR)
    def _set(self, name, value):
        object.__setattr__(self, '_FreshCompletedOrderCollectionOwner__' + name, value)

    @property
    def _receipt(self): return self.__receipt
    @property
    def _failed(self): return self.__failed

    def _thread_check(self):
        _require(current_thread() is self.__association[-1])

    def _check(self):
        journal, c, control, read, write, receipt, header, store, session, thread = self.__association
        journal._local_check(c, healthy=False)
        _require(all(type(value) is int and value >= 0 for value in (
            read._generation, write._generation, read.config.client_id, write._config.client_id,
            read._fresh_completed_order_sequence, read._fresh_completed_order_marker,
            read._fresh_completed_order_request_sequence, write._arrival_sequence,
            write._callback_sequence, journal._persistence_cursor)))
        _require(all(type(value) is bool for value in (
            read._collecting, read._ready, read._disconnect_emitted, write._closed,
            write._arrival_unusable, write._invalid_callback, c._pending_confirmation,
            c._broker_state_reconciliation_required, c._reconciliation_required,
            control._reconciliation_required, write._reconciliation_required)))
        for value in (read._fresh_completed_order_sequence, read._fresh_completed_order_marker,
                      read._fresh_completed_order_request_sequence):
            _reconciliation_integer(value, wide=True)
        _require(not journal._persistence_failed and not self.__failed
                 and read._fresh_completed_order_owner is self
                 and c._control is control and control._transport is read and c._write is write
                 and journal._receipt is receipt and journal._header is header
                 and journal._store is store and journal._session is session and journal._thread is thread
                 and object.__getattribute__(journal, '_PaperDispatchJournalOwner__history')
                 is self.__journal_history
                 and read._journal_account_association is self.__account_association
                 and read._client is self.__clients[0] and write._client is self.__clients[1]
                 and read.config is self.__configs[0] and write._config is self.__configs[1]
                 and self._configuration_values() == self.__config_values
                 and read._snapshot is self.__read_snapshot
                 and read._clock is self.__clock
                 and read._api is self.__api[0] and read._api.OUT is self.__api[1]
                 and self.__server_version.__self__ is self.__clients[0]
                 and self.__server_version.__func__ is type(self.__clients[0]).serverVersion
                 and self._support_values() == self.__support_values
                 and self.__request.__self__ is self.__clients[0]
                 and self.__request.__func__ is type(self.__clients[0]).reqCompletedOrders
                 and (_values(receipt), _values(header), _values(session)) == self.__identity
                 and read._generation == receipt.read_generation
                 and write._generation == session.write_generation
                 and write._config.client_id == session.client_id
                 and not read._collecting and read._failure is None and not read._disconnect_emitted
                 and not write._closed and not write._arrival_unusable and not write._invalid_callback
                 and write._callback_evidence_lost_at is None and not write._status_fences
                 and c._active_attempt is None
                 and any((c._pending_confirmation, c._broker_state_reconciliation_required,
                          c._reconciliation_required, control._reconciliation_required,
                          write._reconciliation_required)))
        _require(_clock() < self.__monotonic_deadline)

    def _configuration_values(self):
        read_config, write_config = self.__configs
        _require(type(read_config) is TWSReadOnlyConfig and type(write_config) is PaperTWSConfig
                 and type(write_config.intent) is PaperTransportIntent)
        return _values((tuple(getattr(read_config, item.name) for item in fields(read_config)),
                        write_config.intent.value, write_config.host, write_config.port,
                        write_config.client_id, write_config.timeout_seconds))

    def _support_values(self):
        read = self.__association[3]
        minimum = read._api.completed_min_version
        opcode = getattr(read._api.OUT, 'REQ_COMPLETED_ORDERS', None)
        version = self.__read_snapshot.server_version
        _require(type(minimum) is int and minimum > 0 and type(opcode) is int and opcode > 0
                 and type(version) is int and version >= minimum
                 and self.__read_snapshot.completed_orders_available is True)
        return _values((minimum, opcode, version, self.__read_snapshot.completed_orders_available))

    def _support_check(self):
        # External SDK inspection is outside coordinator/lifecycle/capture locks.
        self._thread_check()
        self._check()
        version = self.__server_version()
        _require(type(version) is int and version == self.__read_snapshot.server_version)
        self._check()

    def _authenticate(self):
        journal, c, control, read, write, receipt, header = self.__association[:7]
        # No arrival/write capture lock across authentication I/O or SDK state inspection.
        self._check()
        binding = read._validate_journal_account_binding(receipt)
        _require(_values(binding) == _values(header.account_binding)
                 and read._journal_account_association is self.__account_association
                 and read._journal_account_association.valid_until == self.__deadline
                 and self.__clients[1].isConnected() is True)
        self._check()

    def _boundary(self):
        journal, c, control, read, write = self.__association[:5]
        self._check()
        return _values((read._generation, write._generation, read.config.client_id, write._config.client_id,
            tuple(getattr(read.config, f.name) for f in fields(read.config)),
            read._snapshot.account.available_at, read._snapshot.account.valid_until,
            read._snapshot.account.verified, read._snapshot.account.mode,
            read._accounts, read._failure, read._disconnect_emitted,
            write._closed, write._arrival_sequence, write._callback_sequence,
            write._arrival_unusable, write._invalid_callback, write._callback_evidence_lost_at,
            journal._persistence_cursor, len(object.__getattribute__(journal,
                '_PaperDispatchJournalOwner__history')),
            read._fresh_completed_order_request_sequence, id(read._fresh_open_order_capture),
            id(read._fresh_execution_capture),
            (read._api.completed_min_version, getattr(read._api.OUT, 'REQ_COMPLETED_ORDERS', None),
             read._snapshot.server_version, read._snapshot.completed_orders_available),
            c._pending_confirmation, c._broker_state_reconciliation_required,
            c._reconciliation_required, control._reconciliation_required, write._reconciliation_required))

    @staticmethod
    def _capture_values(capture):
        _require(type(capture) is _FreshCompletedOrderCapture)
        return _values((capture.local_id, capture.generation,
                        capture.start_marker, capture.start_sequence, capture.accounts))

    def _capture_check(self):
        read = self.__association[3]
        capture = self.__capture
        _require(read._fresh_completed_order_capture is capture
                 and self._capture_values(capture) == self.__capture_identity
                 and type(capture.observations) is list and type(capture.identities) is dict
                 and all(type(getattr(capture, name)) is bool for name in
                         ('attempted', 'returned', 'end_observed', 'sealed')))
        _require(capture.local_id == read._fresh_completed_order_request_sequence)
        _require(read._fresh_completed_order_marker == capture.start_marker + int(capture.end_observed)
                 and (capture.end_marker == read._fresh_completed_order_marker if capture.end_observed
                      else capture.end_marker is None))
        if capture.end_observed:
            _reconciliation_integer(capture.end_marker, positive=True, wide=True)
        _require(capture.attempted is (self.__sdk_result is not _SDKRequestResult.NOT_ATTEMPTED)
                 and capture.returned is (self.__sdk_result is _SDKRequestResult.RETURNED_NORMALLY))
        _require(capture.encoding is None or type(capture.encoding) is str
                 and capture.encoding in ('LEGACY', 'PROTOBUF'))
        _require(capture.outcome is None or type(capture.outcome) is str
                 and capture.outcome in {item.name for item in _CollectionOutcome
                                         if item is not _CollectionOutcome.COMPLETED})
        _require(not (capture.end_observed or capture.sealed or capture.outcome is not None)
                 or capture.envelope is None)
        _require(len(capture.observations) <= _RECONCILIATION_CAPACITY
                 and len(capture.identities) <= 2 * len(capture.observations))
        first = {}
        for sequence, event in enumerate(capture.observations, capture.start_sequence + 1):
            _observation_copy(event)
            _require(event.sequence == sequence and event.generation == capture.generation
                     and event.encoding == capture.encoding)
            for key in _completed_order_keys(event):
                prior = first.setdefault(key, event)
                _require(_completed_order_agreement(prior, event))
        _require(capture.identities.keys() == first.keys()
                 and all(capture.identities[key] is event for key, event in first.items()))
        _require(read._fresh_completed_order_sequence == capture.start_sequence + len(capture.observations)
                 + (1 if capture.outcome == 'OVERFLOW' else 0))

    def _secure_failure(self, outcome='INVALIDATED'):
        self._set('failed', True)
        # Only read-local synchronization; no earlier locks on interruption cleanup.
        try:
            read = self.__association[3]
            with read._condition:
                if read._fresh_completed_order_capture is self.__capture:
                    read._fresh_completed_order_fail(outcome)
        except BaseException:
            # Reporting/notification failure cannot replace the original interruption.
            capture = getattr(self, '_FreshCompletedOrderCollectionOwner__capture', None)
            if type(capture) is _FreshCompletedOrderCapture and not capture.sealed:
                if capture.outcome is None:
                    capture.outcome = outcome
                capture.envelope = None

    def _issue(self):
        read = self.__association[3]
        # Boundary construction may be delayed/interrupted. Authenticate again
        # before marking issuance; this finishes before the SDK call and wait.
        self._support_check()
        self._authenticate()
        with read._condition:
            self._capture_check()
            capture = self.__capture
            _require(read._fresh_completed_order_capture is capture and not capture.attempted
                     and capture.outcome is None and not capture.sealed)
            capture.attempted = True
            self._set('sdk_result', _SDKRequestResult.ATTEMPTED)
        # Every lifecycle/capture/coordinator lock is released before the SDK call.
        try:
            self.__request(False)  # Exact existing startup apiOnly policy; never caller-controlled.
        except BaseException as error:
            self._set('sdk_result', _SDKRequestResult.RAISED_EXCEPTION if isinstance(error, Exception)
                      else _SDKRequestResult.INTERRUPTED)
            try:
                with read._condition:
                    read._fresh_completed_order_fail('FAILED' if isinstance(error, Exception) else 'INTERRUPTED')
            except BaseException:
                pass  # Local failure precedes notification; preserve the original SDK interruption.
            if not isinstance(error, Exception):
                raise
        else:
            with read._condition:
                capture.returned = True
                self._set('sdk_result', _SDKRequestResult.RETURNED_NORMALLY)

    def _wait(self, deadline):
        read = self.__association[3]
        with read._condition:
            while not self.__capture.end_observed and self.__capture.outcome is None:
                remaining = min(deadline, self.__monotonic_deadline) - _clock()
                if remaining <= 0:
                    read._fresh_completed_order_fail('TIMED_OUT' if deadline <= self.__monotonic_deadline
                                               else 'INVALIDATED')
                    break
                read._condition.wait(min(remaining, .05))
                if read._generation != self.__capture.generation:
                    read._fresh_completed_order_fail('INVALIDATED')

    def _publish(self, receipt):
        # Private test seam. Called with the final capture locks, never SDK/I/O.
        return _receipt_values(receipt)

    def _capture_result_values(self):
        capture = self.__capture
        return _values((capture.attempted, capture.returned, capture.end_observed,
                        capture.end_marker, capture.outcome, capture.encoding, capture.sealed,
                        self.__sdk_result.value, self.__association[3]._fresh_completed_order_sequence))

    def _finish(self):
        journal, c, control, read, write = self.__association[:5]
        changed = False
        try:
            self._support_check()
        except Exception:
            changed = True
        with c._lock, control._lock:
            try:
                self._authenticate()
            except Exception:
                changed = True
            with read._lifecycle, read._condition, write._lock, write._arrival_lock:
                capture = self.__capture
                try:
                    self._capture_check()
                    _require(read._fresh_completed_order_capture is capture and not capture.sealed)
                    changed |= self._boundary() != self.__baseline
                except Exception:
                    changed = True
                if changed:
                    read._fresh_completed_order_fail('INVALIDATED')
                outcome = (_CollectionOutcome[capture.outcome] if capture.outcome is not None
                           else _CollectionOutcome.COMPLETED)
                _require(outcome is not _CollectionOutcome.COMPLETED or capture.end_observed and capture.returned)
                _, _, _, _, _, binding, header, _, session = self.__association[:9]
                receipt = _FreshCompletedOrderReceipt(capture.local_id, _SCOPE,
                    _JournalAccountBinding(header.account_binding.opaque_id), header.journal_id,
                    session.session_id, binding.read_generation, session.write_generation,
                    self.__read_client_id, session.client_id, capture.start_marker, capture.end_marker,
                    capture.start_sequence, read._fresh_completed_order_sequence, self.__sdk_result,
                    capture.end_observed, outcome, self.__deadline, changed, tuple(capture.observations))
                # Capture identity and occurrence count must not be rewritten by a seam.
                self._capture_check()
                result_values = self._capture_result_values()
                values = self._publish(receipt)
                self._capture_check()
                # Constructor/publication seams cannot silently change the final proof.
                _require(read._fresh_completed_order_capture is capture and not capture.sealed
                         and _receipt_values(receipt) == values
                         and self._capture_result_values() == result_values
                         and tuple(_observation_values(e) for e in capture.observations)
                         == tuple(_observation_values(e) for e in receipt.observations))
                if outcome is _CollectionOutcome.COMPLETED:
                    _require(self._boundary() == self.__baseline)
                capture.sealed = True
                if outcome is not _CollectionOutcome.COMPLETED:
                    self._set('failed', True)
                self._set('receipt_values', values)
                self._set('receipt', receipt)  # Final publication; no fallible work follows.
                return receipt

    def _collect(self):
        try:
            self._thread_check()
            _require(not self.__started and not self.__failed)
            self._set('started', True)  # One attempt for this exact lifetime, no retry.
            self._support_check()
            journal, c, control, read, write = self.__association[:5]
            with c._lock, control._lock:
                self._authenticate()
                with read._lifecycle, read._condition, write._lock, write._arrival_lock:
                    _require(read._fresh_completed_order_capture is None)
                    for previous, kind in ((read._fresh_open_order_capture, _FreshOpenOrderCapture),
                                           (read._fresh_execution_capture, _FreshExecutionCapture)):
                        _require(previous is None or type(previous) is kind and previous.sealed is True)
                    local_id = read._fresh_completed_order_request_sequence + 1
                    marker = read._fresh_completed_order_marker + 1
                    _reconciliation_integer(local_id, positive=True, wide=True)
                    _reconciliation_integer(marker, positive=True, wide=True)
                    read._fresh_completed_order_request_sequence = local_id
                    read._fresh_completed_order_marker = marker
                    capture = _FreshCompletedOrderCapture(local_id, read._generation,
                        marker, read._fresh_completed_order_sequence, tuple(read._accounts), [], {})
                    self._set('capture', capture)
                    self._set('capture_identity', self._capture_values(capture))
                    read._fresh_completed_order_capture = capture
                    self._set('baseline', self._boundary())
                    deadline = _clock() + read.config.timeout_seconds
            self._issue()
            self._wait(deadline)
            return self._finish()
        except BaseException as error:
            self._secure_failure('INVALIDATED' if isinstance(error, Exception) else 'INTERRUPTED')
            # An interrupted publication must not leave a usable partial receipt.
            self._set('receipt', None); self._set('receipt_values', None)
            if not isinstance(error, Exception):
                raise
            raise ReadOnlyError(_ERROR) from None

    def _consume_receipt(self, receipt):
        try:
            self._thread_check()
            self._support_check()
            self._capture_check()
            _require(not self.__consumed and type(receipt) is _FreshCompletedOrderReceipt
                     and receipt is self.__receipt and _receipt_values(receipt) == self.__receipt_values
                     and receipt.outcome is _CollectionOutcome.COMPLETED)
            _require(self.__capture.outcome is None and self.__capture.attempted
                     and self.__capture.returned and self.__capture.end_observed)
            _, c, control, read, write = self.__association[:5]
            with c._lock, control._lock:
                self._authenticate()
                with read._lifecycle, read._condition, write._lock, write._arrival_lock:
                    _require(read._fresh_completed_order_capture is self.__capture
                             and self.__capture.sealed and self._boundary() == self.__baseline
                             and _receipt_values(receipt) == self.__receipt_values)
                    self._set('consumed', True)
                    return receipt  # Evidence only; no cursor/barrier/authorization mutation.
        except BaseException as error:
            self._secure_failure('INVALIDATED' if isinstance(error, Exception) else 'INTERRUPTED')
            if not isinstance(error, Exception):
                raise
            raise ReadOnlyError(_ERROR) from None


def _create_fresh_completed_order_collection_owner(journal):
    """Exact live factory; no caller label, alternate store, renewal or restart adoption."""
    try:
        _require(type(journal) is _PaperDispatchJournalOwner)
        journal._thread_check()
        components = object.__getattribute__(journal, '_PaperDispatchJournalOwner__components')
        c, control, read, write, receipt, header, store, session, thread = components
        _require(type(read) is ReadOnlyTWSTransport)
        with c._lock, control._lock:
            journal._local_check(c, healthy=False)
            _require(read._fresh_completed_order_owner is None)
            binding = read._validate_journal_account_binding(receipt)
            _require(_values(binding) == _values(header.account_binding))
            association = read._journal_account_association
            deadline = association.valid_until
            sampled = _clock()
            now = read._clock()
            _require(type(now) is datetime and type(deadline) is datetime
                     and now < deadline and deadline.tzinfo is timezone.utc)
            owner = object.__new__(_FreshCompletedOrderCollectionOwner)
            for name, value in (
                ('association', (journal,) + components),
                ('identity', (_values(receipt), _values(header), _values(session))),
                ('account_association', association), ('clients', (read._client, write._client)),
                ('deadline', deadline), ('monotonic_deadline', sampled + (deadline-now).total_seconds()),
                ('clock', read._clock), ('started', False), ('failed', False),
                ('read_snapshot', read._snapshot),
                ('configs', (read.config, write._config)),
                ('journal_history', object.__getattribute__(journal, '_PaperDispatchJournalOwner__history')),
                ('read_client_id', read.config.client_id),
                ('request', type(read._client).reqCompletedOrders.__get__(read._client)),
                ('receipt', None), ('receipt_values', None), ('consumed', False),
                ('capture', None), ('capture_identity', None), ('baseline', None),
                ('server_version', type(read._client).serverVersion.__get__(read._client)),
                ('api', (read._api, read._api.OUT)),
                ('support_values', _values((read._api.completed_min_version,
                    getattr(read._api.OUT, 'REQ_COMPLETED_ORDERS', None),
                    read._snapshot.server_version, read._snapshot.completed_orders_available))),
                ('sdk_result', _SDKRequestResult.NOT_ATTEMPTED)):
                owner._set(name, value)
            owner._set('config_values', owner._configuration_values())
            with read._lifecycle, read._condition, write._lock, write._arrival_lock:
                _require(read._fresh_completed_order_owner is None)
                read._fresh_completed_order_owner = owner
                try:
                    owner._check()
                except BaseException:
                    # A failed association cannot be replaced by a second collector.
                    owner._set('failed', True)
                    raise
            try:
                owner._authenticate()  # Recheck after constructor/registration latency.
            except BaseException:
                owner._secure_failure()
                raise
            return owner
    except Exception:
        raise ReadOnlyError(_ERROR) from None
