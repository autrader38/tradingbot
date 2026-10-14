"""Private one-shot collection provenance, never currentness or management authority.

The local request identity does not occur on the IBKR wire. Observations may be
requested, unsolicited or delayed. No receipt is durable or restart-adoptable.
"""

from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from math import isfinite
from threading import current_thread
from time import monotonic

from .ibkr_readonly import (ReadOnlyTWSTransport, _FreshOpenOrderCapture,
    _ReconciliationOrderEvidence, _ReconciliationPresence, _RECONCILIATION_CAPACITY,
    _reconciliation_field, _reconciliation_integer, _reconciliation_text)
from .paper_dispatch_journal import _PaperDispatchJournalOwner, _values
from .paper_execution_journal import _JournalAccountBinding
from .readonly_models import ReadOnlyError


__all__ = ()
_ERROR = 'FRESH_OPEN_ORDER_COLLECTION_UNAVAILABLE'
_SCOPE = 'DECLARED_ALL_OPEN_ORDERS_ACCOUNT_FILTERED'
_LIMITATIONS = ('LOCAL_ID_NOT_BROKER_REQUEST_ID', 'UNSOLICITED_OR_DELAYED_OBSERVATIONS_POSSIBLE',
    'RECEIPT_INTERVAL_NOT_ECONOMIC_EVENT_TIME', 'NOT_AN_ATOMIC_BROKER_SNAPSHOT',
    'ABSENCE_IS_NON_OBSERVATION_ONLY', 'NO_MANAGEMENT_CURRENTNESS_OR_OWNERSHIP')


def _require(condition):
    if not condition:
        raise ReadOnlyError(_ERROR)


def _clock():
    value = monotonic()
    _require(type(value) in (int, float) and isfinite(value) and value >= 0)
    return value


class _CollectionOutcome(StrEnum):
    COMPLETED = 'COMPLETED_DECLARED_OPEN_ORDER_COLLECTION_INTERVAL'
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
             and event.source == 'OPEN_ORDER' and type(event.encoding) is str
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
        supplied = name in p.supplied or name == 'order_id' and 'top_order_id' in p.supplied
        _require(getattr(p, name) is supplied)
        value = getattr(event, name)
        _require(supplied or value is None)
        if supplied and name in ('order_id', 'client_id'):
            _require(type(value) is int)
        if value is not None:
            _require(_reconciliation_field(name, value) == value)
    _require(p.top_order_id == ('top_order_id' in p.supplied)
             and p.nested_order_id == (event.encoding == 'PROTOBUF' and 'order_id' in p.supplied)
             and p.order_state == ('order_state' in p.supplied)
             and p.status == ('status' in p.supplied)
             and p.status == (event.status is not None) and (not p.status or p.order_state))
    _require(event.encoding != 'LEGACY' or not p.top_order_id)
    _require(event.encoding != 'PROTOBUF' or p.order_state)
    for name in required:
        value = getattr(event, name)
        _require(type(value) is (Decimal if name == 'quantity' else int if name == 'con_id' else str))
        _require(_reconciliation_field(name, value) == value)
    _require(event.status is None or type(event.status) is str and event.status in {
        'PendingSubmit', 'PreSubmitted', 'Submitted', 'Filled', 'Cancelled',
        'ApiCancelled', 'Inactive', 'Expired', 'UNKNOWN'})
    return replace(event, quantity=Decimal(event.quantity.as_tuple()),
                   presence=replace(p, supplied=frozenset(p.supplied)))


def _observation_values(event):
    copied = _observation_copy(event)
    return tuple((f.name, copied.quantity.as_tuple() if f.name == 'quantity'
                  else tuple(getattr(copied.presence, p.name) for p in fields(copied.presence))
                  if f.name == 'presence' else getattr(copied, f.name)) for f in fields(copied))


@dataclass(frozen=True, slots=True, repr=False)
class _FreshOpenOrderReceipt:
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
        for sequence, event in enumerate(observations, self.initial_sequence_watermark + 1):
            _require(event.sequence == sequence and event.generation == self.read_generation
                     and sequence <= self.final_sequence_watermark)
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

    def __repr__(self): return '<PrivateFreshOpenOrderCollectionReceipt evidence-only>'


def _receipt_values(receipt):
    _require(type(receipt) is _FreshOpenOrderReceipt)
    # Validate an independent reconstruction, never silently normalize a forged receipt.
    checked = replace(receipt)
    return tuple((f.name, tuple(_observation_values(e) for e in checked.observations)
                  if f.name == 'observations' else _values(getattr(checked, f.name))
                  if f.name not in ('sdk_result', 'outcome') else getattr(checked, f.name).value)
                 for f in fields(checked))


class _FreshOpenOrderCollectionOwner:
    __slots__ = ('__association', '__identity', '__account_association', '__clients',
                 '__deadline', '__monotonic_deadline', '__clock', '__started', '__failed',
                 '__receipt', '__receipt_values', '__consumed', '__capture', '__baseline', '__sdk_result',
                 '__read_snapshot', '__request', '__read_client_id')

    def __init__(self, *args, **kwargs): raise ReadOnlyError(_ERROR)
    def __repr__(self): return '<PrivateFreshOpenOrderCollectionOwner evidence-only>'
    def __setattr__(self, name, value): raise ReadOnlyError(_ERROR)
    def __delattr__(self, name): raise ReadOnlyError(_ERROR)
    def _set(self, name, value):
        object.__setattr__(self, '_FreshOpenOrderCollectionOwner__' + name, value)

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
            read._fresh_open_order_sequence, read._fresh_open_order_marker,
            read._fresh_open_order_request_sequence, write._arrival_sequence,
            write._callback_sequence, journal._persistence_cursor)))
        _require(all(type(value) is bool for value in (
            read._collecting, read._ready, read._disconnect_emitted, write._closed,
            write._arrival_unusable, write._invalid_callback, c._pending_confirmation,
            c._broker_state_reconciliation_required, c._reconciliation_required,
            control._reconciliation_required, write._reconciliation_required)))
        _require(not journal._persistence_failed and not self.__failed
                 and read._fresh_open_order_owner is self
                 and c._control is control and control._transport is read and c._write is write
                 and journal._receipt is receipt and journal._header is header
                 and journal._store is store and journal._session is session and journal._thread is thread
                 and read._journal_account_association is self.__account_association
                 and read._client is self.__clients[0] and write._client is self.__clients[1]
                 and read._snapshot is self.__read_snapshot
                 and read._clock is self.__clock
                 and self.__request.__self__ is self.__clients[0]
                 and self.__request.__func__ is type(self.__clients[0]).reqAllOpenOrders
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
            c._pending_confirmation, c._broker_state_reconciliation_required,
            c._reconciliation_required, control._reconciliation_required, write._reconciliation_required))

    def _secure_failure(self, outcome='INVALIDATED'):
        self._set('failed', True)
        # Only read-local synchronization; no earlier locks on interruption cleanup.
        try:
            read = self.__association[3]
            with read._condition:
                if read._fresh_open_order_capture is self.__capture:
                    read._fresh_open_order_fail(outcome)
        except BaseException:
            # Reporting/notification failure cannot replace the original interruption.
            capture = getattr(self, '_FreshOpenOrderCollectionOwner__capture', None)
            if type(capture) is _FreshOpenOrderCapture and not capture.sealed:
                if capture.outcome is None:
                    capture.outcome = outcome
                capture.envelope = None

    def _issue(self):
        read = self.__association[3]
        # Boundary construction may be delayed/interrupted. Authenticate again
        # before marking issuance; this finishes before the SDK call and wait.
        self._authenticate()
        with read._condition:
            capture = self.__capture
            _require(read._fresh_open_order_capture is capture and not capture.attempted
                     and capture.outcome is None and not capture.sealed)
            capture.attempted = True
            self._set('sdk_result', _SDKRequestResult.ATTEMPTED)
        # Every lifecycle/capture/coordinator lock is released before the SDK call.
        try:
            self.__request()
        except BaseException as error:
            self._set('sdk_result', _SDKRequestResult.RAISED_EXCEPTION if isinstance(error, Exception)
                      else _SDKRequestResult.INTERRUPTED)
            with read._condition:
                read._fresh_open_order_fail('FAILED' if isinstance(error, Exception) else 'INTERRUPTED')
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
                    read._fresh_open_order_fail('TIMED_OUT' if deadline <= self.__monotonic_deadline
                                               else 'INVALIDATED')
                    break
                read._condition.wait(min(remaining, .05))
                if read._generation != self.__capture.generation:
                    read._fresh_open_order_fail('INVALIDATED')

    def _publish(self, receipt):
        # Private test seam. Called with the final capture locks, never SDK/I/O.
        return _receipt_values(receipt)

    def _finish(self):
        journal, c, control, read, write = self.__association[:5]
        changed = False
        with c._lock, control._lock:
            try:
                self._authenticate()
            except Exception:
                changed = True
            with read._lifecycle, read._condition, write._lock, write._arrival_lock:
                capture = self.__capture
                try:
                    _require(read._fresh_open_order_capture is capture and not capture.sealed)
                    changed |= self._boundary() != self.__baseline
                except Exception:
                    changed = True
                if changed:
                    read._fresh_open_order_fail('INVALIDATED')
                outcome = (_CollectionOutcome[capture.outcome] if capture.outcome is not None
                           else _CollectionOutcome.COMPLETED)
                _require(outcome is not _CollectionOutcome.COMPLETED or capture.end_observed and capture.returned)
                _, _, _, _, _, binding, header, _, session = self.__association[:9]
                receipt = _FreshOpenOrderReceipt(capture.local_id, _SCOPE,
                    _JournalAccountBinding(header.account_binding.opaque_id), header.journal_id,
                    session.session_id, binding.read_generation, session.write_generation,
                    self.__read_client_id, session.client_id, capture.start_marker, capture.end_marker,
                    capture.start_sequence, read._fresh_open_order_sequence, self.__sdk_result,
                    capture.end_observed, outcome, self.__deadline, changed, tuple(capture.observations))
                values = self._publish(receipt)
                # Constructor/publication seams cannot silently change the final proof.
                _require(read._fresh_open_order_capture is capture and not capture.sealed
                         and _receipt_values(receipt) == values)
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
            journal, c, control, read, write = self.__association[:5]
            with c._lock, control._lock:
                self._authenticate()
                with read._lifecycle, read._condition, write._lock, write._arrival_lock:
                    _require(read._fresh_open_order_capture is None)
                    self._set('baseline', self._boundary())
                    read._fresh_open_order_marker += 1
                    read._fresh_open_order_request_sequence += 1
                    _reconciliation_integer(read._fresh_open_order_request_sequence, positive=True, wide=True)
                    capture = _FreshOpenOrderCapture(read._fresh_open_order_request_sequence, read._generation,
                        read._fresh_open_order_marker, read._fresh_open_order_sequence,
                        tuple(read._accounts), [])
                    self._set('capture', capture)
                    read._fresh_open_order_capture = capture
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
            _require(not self.__consumed and type(receipt) is _FreshOpenOrderReceipt
                     and receipt is self.__receipt and _receipt_values(receipt) == self.__receipt_values
                     and receipt.outcome is _CollectionOutcome.COMPLETED)
            _, c, control, read, write = self.__association[:5]
            with c._lock, control._lock:
                self._authenticate()
                with read._lifecycle, read._condition, write._lock, write._arrival_lock:
                    _require(read._fresh_open_order_capture is self.__capture
                             and self.__capture.sealed and self._boundary() == self.__baseline
                             and _receipt_values(receipt) == self.__receipt_values)
                    self._set('consumed', True)
                    return receipt  # Evidence only; no cursor/barrier/authorization mutation.
        except BaseException as error:
            self._secure_failure('INVALIDATED' if isinstance(error, Exception) else 'INTERRUPTED')
            if not isinstance(error, Exception):
                raise
            raise ReadOnlyError(_ERROR) from None


def _create_fresh_open_order_collection_owner(journal):
    """Exact live factory; no caller label, alternate store, renewal or restart adoption."""
    try:
        _require(type(journal) is _PaperDispatchJournalOwner)
        journal._thread_check()
        components = object.__getattribute__(journal, '_PaperDispatchJournalOwner__components')
        c, control, read, write, receipt, header, store, session, thread = components
        _require(type(read) is ReadOnlyTWSTransport)
        with c._lock, control._lock:
            journal._local_check(c, healthy=False)
            _require(read._fresh_open_order_owner is None)
            binding = read._validate_journal_account_binding(receipt)
            _require(_values(binding) == _values(header.account_binding))
            association = read._journal_account_association
            deadline = association.valid_until
            sampled = _clock()
            now = read._clock()
            _require(type(now) is datetime and type(deadline) is datetime
                     and now < deadline and deadline.tzinfo is timezone.utc)
            owner = object.__new__(_FreshOpenOrderCollectionOwner)
            for name, value in (
                ('association', (journal,) + components),
                ('identity', (_values(receipt), _values(header), _values(session))),
                ('account_association', association), ('clients', (read._client, write._client)),
                ('deadline', deadline), ('monotonic_deadline', sampled + (deadline-now).total_seconds()),
                ('clock', read._clock), ('started', False), ('failed', False),
                ('read_snapshot', read._snapshot),
                ('read_client_id', read.config.client_id),
                ('request', type(read._client).reqAllOpenOrders.__get__(read._client)),
                ('receipt', None), ('receipt_values', None), ('consumed', False),
                ('capture', None), ('baseline', None), ('sdk_result', _SDKRequestResult.NOT_ATTEMPTED)):
                owner._set(name, value)
            with read._lifecycle, read._condition, write._lock, write._arrival_lock:
                _require(read._fresh_open_order_owner is None)
                read._fresh_open_order_owner = owner
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
