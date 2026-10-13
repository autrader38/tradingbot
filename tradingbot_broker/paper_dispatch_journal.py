"""Private C3C4C owner. Durable intent is evidence, never restart authority.

Setup is explicit and outside dispatch. One exact connection/thread/session is
retained for the coordinator lifetime. Callbacks never enter this module.
Private Python isolation does not protect against hostile interpreter access.
"""

from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
import secrets
from threading import current_thread

from . import paper_execution_journal as j
from .paper_execution_journal_sqlite import _SQLiteJournalStore, _CommittedRecord
from .paper_dispatch_journal_context import _DispatchJournalContext
from .paper_execution import _timestamp_value
from .readonly_models import ReadOnlyError
from .ibkr_readonly import _JournalAccountBindingReceipt, _JournalAccountAssociation
from .ibkr_paper_transport import (PaperTWSTransport, _OpenOrderEvidence,
    _OrderStatusEvidence, _ExecutionEvidence, _CommissionEvidence, _EVIDENCE_CAPACITY)
from .ibkr_qualified_status_ledger import _QualifiedOrderStatusEvidence
from .paper_qualified_status_journal import (_adapt_qualified_status,
                                            _qualified_callback_values)


_ERROR = 'PAPER_DISPATCH_JOURNAL_UNAVAILABLE'


def _require(condition):
    if not condition:
        raise ReadOnlyError(_ERROR)


def _values(value):
    """Pure independent primitives, including exact timestamp/fold identity."""
    kind = type(value)
    if kind in (str, int, bool, bytes, type(None)):
        return kind, value
    if kind is Decimal:
        _require(value.is_finite())
        return kind, value.as_tuple()
    if kind is datetime:
        return kind, _timestamp_value(value)
    if kind is date:
        return kind, value.isoformat()
    if kind in (j._WitnessKind, j._LifecycleStatus, j._ReconciliationReason,
                j._ClosureReason, j.AccountMode, j._UntrackedCallbackReason,
                j._QualifiedStatusProtocol, j._QualifiedStatusSourceQuality):
        return kind, value.value
    if kind is tuple:
        return kind, tuple(_values(item) for item in value)
    # Exact reviewed models only; never invoke caller equality or raw SDK getters.
    from .paper_execution_journal_sqlite import _MODELS
    _require(kind in _MODELS or kind in (_DispatchJournalContext, _CommittedRecord,
                                       _JournalAccountBindingReceipt))
    return kind, tuple((field.name, _values(getattr(value, field.name)))
                       for field in fields(kind))


@dataclass(frozen=True, slots=True, repr=False)
class _DurableInvocationProof:
    attempt: object
    reservation: object
    context: _DispatchJournalContext
    event: j._InvocationMayOccur
    committed: _CommittedRecord

    def __repr__(self):
        return '<PrivateDurableInvocationProof>'


class _CallbackPersistenceState(StrEnum):
    CURRENT = 'CURRENT'
    PENDING = 'PENDING'
    DEFERRED = 'DEFERRED'
    FAILED = 'FAILED'


@dataclass(frozen=True, slots=True, repr=False)
class _CallbackPersistenceResult:
    state: _CallbackPersistenceState
    cursor: int
    through_sequence: int
    staged_count: int

    def __repr__(self):
        return '<PrivateCallbackPersistenceResult>'


@dataclass(frozen=True, slots=True, repr=False)
class _StagedCallback:
    evidence: object
    values: tuple

    def __repr__(self):
        return '<PrivateStagedCallback>'


@dataclass(frozen=True, slots=True, repr=False)
class _CallbackCoverage:
    staged: _StagedCallback
    event: object
    committed: object

    def __repr__(self):
        return '<PrivateCallbackCoverage>'


_CALLBACK_TYPES = (_OpenOrderEvidence, _OrderStatusEvidence, _ExecutionEvidence,
                   _CommissionEvidence, _QualifiedOrderStatusEvidence)
_OCCURRENCE_TYPES = (j._OpenOrderObserved, j._OrderStatusObserved, j._ExecutionObserved,
    j._DuplicateCallbackCovered, j._UntrackedCallbackObserved,
    j._CommissionCallbackCovered, j._UntrackedCommissionCallbackCovered,
    j._QualifiedOrderStatusObserved)
_STATUS = {'PendingSubmit': j._LifecycleStatus.PENDING_SUBMIT, 'PreSubmitted': j._LifecycleStatus.PRE_SUBMITTED,
    'Submitted': j._LifecycleStatus.SUBMITTED,
    'Filled': j._LifecycleStatus.FILLED, 'Cancelled': j._LifecycleStatus.CANCELLED,
    'ApiCancelled': j._LifecycleStatus.API_CANCELLED, 'Inactive': j._LifecycleStatus.INACTIVE,
    'Expired': j._LifecycleStatus.EXPIRED, 'UNKNOWN': j._LifecycleStatus.UNKNOWN}


def _callback_values(event):
    _require(type(event) in _CALLBACK_TYPES)
    if type(event) is _QualifiedOrderStatusEvidence:
        return _qualified_callback_values(event)
    return tuple((field.name, _values(getattr(event, field.name))) for field in fields(type(event)))


def _callback_projection(event, key, session):
    # Constructors revalidate exact primitives even for otherwise unrelated facts.
    _require(type(event.generation) is int and event.generation == session.write_generation
             and type(event.sequence) is int and event.sequence > 0)
    if type(event) is _CommissionEvidence:
        _CommissionEvidence.__post_init__(event)
        return j._CommissionReceived(key, event.exec_id, event.report_id,
            event.amount, event.currency, event.received_at)
    source = j._CallbackSource(session.session_id, event.generation, event.sequence)
    if type(event) is _OpenOrderEvidence:
        _require(type(event.status) is str and event.status in _STATUS)
        return j._OpenOrderObserved(key, source, event.order_id, event.client_id,
            event.order_ref, event.con_id, event.security_type, event.exchange, event.symbol,
            event.currency, event.side, event.quantity, event.order_type, event.tif, _STATUS[event.status])
    if type(event) is _OrderStatusEvidence:
        _require(type(event.status) is str and event.status in _STATUS)
        return j._OrderStatusObserved(key, source, event.order_id, event.client_id,
            _STATUS[event.status], event.filled, event.remaining, event.average_price,
            event.last_price, event.parent_id)
    _require(type(event) is _ExecutionEvidence)
    return j._ExecutionObserved(key, source, event.order_id, event.client_id, event.order_ref,
        event.execution_id, event.con_id, event.side, event.shares, event.price)


def _callback_source(event, session):
    _require(type(event.generation) is int and event.generation == session.write_generation)
    return j._CallbackSource(session.session_id, event.generation, event.sequence)


def _ordinary_callback_snapshot(event, session):
    _require(type(event) in (_OpenOrderEvidence, _OrderStatusEvidence, _ExecutionEvidence))
    return j._ordinary_source_snapshot(_callback_projection(event, 'validation-only', session))


def _commission_callback_snapshot(event):
    _require(type(event) is _CommissionEvidence)
    _CommissionEvidence.__post_init__(event)
    return j._CommissionCallbackSnapshot(event.exec_id, event.report_id,
        Decimal(event.amount.as_tuple()), event.currency, event.received_at.replace())


def _callback_event_agreement(evidence, event, session):
    """Exact reviewed correspondence, including backing vs occurrence distinction."""
    kind = type(event)
    if kind is j._QualifiedOrderStatusObserved:
        _require(_values(event) == _values(_adapt_qualified_status(evidence, session)))
    elif kind in (j._OpenOrderObserved, j._OrderStatusObserved,
                  j._ExecutionObserved, j._CommissionReceived):
        _require(_values(event) == _values(_callback_projection(evidence, event.client_order_id, session)))
    else:
        _require(kind in j._COVERAGE_EVENT_TYPES
                 and _values(event.source) == _values(_callback_source(evidence, session)))
        snapshot = (_commission_callback_snapshot(evidence)
                    if kind in (j._CommissionCallbackCovered, j._UntrackedCommissionCallbackCovered)
                    else _ordinary_callback_snapshot(evidence, session))
        _require(_values(event.snapshot) == _values(snapshot))


class _PaperDispatchJournalOwner:
    __slots__ = ('__association', '__components', '__identity', '__append', '__load', '__close',
                 '__history', '__consumed', '__inhibited', '__closed',
                 '__proof', '__proof_parts', '__proof_values', '__marker_possible', '__attempt',
                 '__persistence_cursor', '__persistence_failed', '__staged', '__covered',
                 '__executions', '__commissions', '__foreign_executions', '__anchors', '__draining',
                 '__invocation_boundaries', '__adjacent_observation',
                 '__accounting_owner', '__accounting_setup_started', '__accounting_handoff')

    def __init__(self, *args, **kwargs):
        # Only the controlled setup factory publishes a registered owner.
        raise ReadOnlyError(_ERROR)

    def __setattr__(self, name, value):
        if name in ('_PaperDispatchJournalOwner__association',
                    '_PaperDispatchJournalOwner__components',
                    '_PaperDispatchJournalOwner__identity',
                    '_PaperDispatchJournalOwner__append',
                    '_PaperDispatchJournalOwner__load',
                    '_PaperDispatchJournalOwner__close',
                    '_PaperDispatchJournalOwner__accounting_owner') and hasattr(self, name):
            raise ReadOnlyError(_ERROR)
        object.__setattr__(self, name, value)

    def __delattr__(self, name):
        raise ReadOnlyError(_ERROR)

    def __repr__(self):
        return '<PrivatePaperDispatchJournalOwner>'

    @property
    def _store(self): return self.__components[6]

    @property
    def _header(self): return self.__components[5]

    @property
    def _receipt(self): return self.__components[4]

    @property
    def _session(self): return self.__components[7]

    @property
    def _thread(self): return self.__components[8]

    @property
    def _inhibited(self): return self.__inhibited

    @property
    def _persistence_cursor(self): return self.__persistence_cursor

    @property
    def _persistence_failed(self): return self.__persistence_failed

    def _thread_check(self):
        _require(current_thread() is self._thread)

    def _local_check(self, coordinator, *, current=True, healthy=True, setup=False):
        self._thread_check()  # Before any SQLite access, including close/load.
        c, control, read, write, receipt, header, store, session, thread = self.__association
        _require(all(value is original for value, original in
                     zip(self.__association, self.__components))
                 and len(self.__association) == len(self.__components)
                 and coordinator is c and (c._journal_owner is self or
                 setup and c._journal_owner is None and c._journal_setup_started
                 and c._active_attempt is None)
                 and c._control is control and c._write is write
                 and control._transport is read and write._dispatch_coordinator_owner is c
                 and type(store) is _SQLiteJournalStore and store._header is header
                 and type(header) is j._JournalHeader and type(session) is j._SessionRegistered
                 and header.schema_version == 2 and header.format_id == j._FORMAT_ID_V2
                 and not self.__closed
                 and (_values(header), _values(session), _values(receipt)) == self.__identity)
        _require(not store._closed and not store._poisoned and not store._quarantined)
        if healthy:
            _require(not self.__inhibited)
        if current:
            account_association = read._journal_account_association
            _require(type(account_association) is _JournalAccountAssociation
                     and account_association.receipt is receipt
                     and read._generation == receipt.read_generation
                     and write._generation == session.write_generation
                     and write._config.client_id == session.client_id)

    def _inhibit(self):
        self.__inhibited = True

    def _append_event(self, event, *, diagnostic=False, setup=False):
        c, _, _, write = self.__association[:4]
        self._local_check(c, current=not diagnostic, healthy=not diagnostic, setup=setup)
        _require(not write._arrival_lock._is_owned())
        try:
            loaded = self.__load()
            _require(_values(loaded.header) == _values(self._header)
                     and tuple(_values(record) for record in loaded.records) == self.__history)
            committed = self.__append(event)
            _require(type(committed) is _CommittedRecord
                     and committed.record.payload is event
                     and committed.record.sequence == len(self.__history) + 1
                     and committed.record.journal_id == self._header.journal_id
                     and committed.record.account_binding == self._header.account_binding)
            _CommittedRecord.__post_init__(committed)
            # Independent copies reject later substitution/tampering of models.
            snapshot = _values(committed.record)
            expected = self.__history + (snapshot,)
            durable = self.__load()
            _require(_values(durable.header) == _values(self._header)
                     and tuple(_values(record) for record in durable.records) == expected)
            self.__history = expected
            if type(event) is j._AttemptConsumed:
                self.__consumed.add(event.client_order_id)
            return committed
        except BaseException:
            self._inhibit()
            c._mark_reconciliation(audit=False)
            raise

    def _authenticate(self, coordinator, *, setup=False):
        self._local_check(coordinator, setup=setup)
        _, _, read, write = self.__association[:4]
        _require(not write._arrival_lock._is_owned())
        binding = read._validate_journal_account_binding(self._receipt)
        _require(_values(binding) == _values(self._header.account_binding))

    def _prepare(self, coordinator, attempt, reservation):
        self._local_check(coordinator)
        _require(self.__attempt is None and self.__proof is None
                 and attempt is coordinator._active_attempt
                 and type(attempt.context) is _DispatchJournalContext)
        self.__attempt = attempt
        context, key = attempt.context, attempt.order.client_order_id
        _require(context.order.client_order_id == key
                 and (reservation.con_id, reservation.side, reservation.quantity,
                      reservation.order_type, reservation.tif, reservation.order_ref) ==
                     (context.contract.con_id, context.order.side, context.order.quantity,
                      context.order.order_type, context.order.tif, key))
        self._append_event(j._AttemptConsumed(self._session.session_id, key,
            context.order, context.contract, context.strategy, context.risk))
        self._append_event(j._NumericOrderIdReserved(key, reservation.order_id))
        # Once this append begins, failure/interruptions may leave a durable marker.
        self.__marker_possible = True
        event = j._InvocationMayOccur(key)
        committed = self._append_event(event)
        proof = _DurableInvocationProof(attempt, reservation, context, event, committed)
        self.__proof_parts = (attempt, reservation, context, event, committed)
        self.__proof_values = (_values(context), _values(event), _values(committed))
        self.__proof = proof
        return proof

    def _require_marker(self, coordinator, attempt, reservation, proof):
        # Pure final check: no store call, authentication I/O or clock here.
        self._local_check(coordinator)
        _require(type(proof) is _DurableInvocationProof and proof is self.__proof
                 and all(value is original for value, original in zip(
                     (proof.attempt, proof.reservation, proof.context, proof.event, proof.committed),
                     self.__proof_parts))
                 and attempt is self.__attempt and attempt is coordinator._active_attempt
                 and proof.attempt is attempt and proof.reservation is reservation
                 and proof.context is attempt.context
                 and proof.event is proof.committed.record.payload
                 and proof.event.client_order_id == attempt.order.client_order_id
                 and (_values(proof.context), _values(proof.event),
                      _values(proof.committed)) == self.__proof_values)

    def _marker_may_exist(self, attempt):
        return attempt is self.__attempt and self.__marker_possible

    def _returned(self, coordinator, attempt, reservation, proof):
        self._record_callback_boundary(coordinator, attempt)
        self._require_marker(coordinator, attempt, reservation, proof)
        self._append_event(j._SdkReturnedNormally(attempt.order.client_order_id))

    def _uncertain(self, coordinator, reason, attempt=None):
        # Safety precedes every optional persistence call and every publication.
        self._record_callback_boundary(coordinator, attempt)
        self._inhibit()
        coordinator._mark_reconciliation(audit=False)
        if current_thread() is not self._thread:
            return
        key = None if attempt is None else attempt.order.client_order_id
        if key not in self.__consumed:
            key = None
        try:
            self._append_event(j._ReconciliationRequired(reason, key,
                self._session.session_id), diagnostic=True)
        except BaseException:
            # Best-effort diagnostic must never replace original uncertainty or
            # an original KeyboardInterrupt/SystemExit/GeneratorExit.
            pass

    def _denied(self, coordinator, key):
        self._local_check(coordinator)
        _require(not self.__marker_possible or self.__attempt.order.client_order_id != key)
        if key not in self.__consumed:
            self._append_event(j._AttemptConsumed(self._session.session_id, key))
        self._append_event(j._AttemptClosedWithoutInvocation(key,
            j._ClosureReason.LOCAL_GATE_DENIED))

    def _close(self):
        self._thread_check()
        if self.__closed:
            return
        self.__closed = True
        self._inhibit()
        self.__close()

    def _load(self):
        self._local_check(self.__association[0], current=False, healthy=False)
        _require(not self.__association[3]._arrival_lock._is_owned())
        return self.__load()

    def _create_accounting_owner(self):
        """One evidence consumer, never a replacement/adoption or dispatch gate."""
        from .paper_execution_accounting import _PaperExecutionAccountingOwner
        c, control, read, write = self.__association[:4]
        self._thread_check()
        with c._lock, control._lock:
            self._local_check(c, healthy=False)
            _require(not self.__accounting_setup_started)
            self.__accounting_setup_started = True
            try:
                binding = read._validate_journal_account_binding(self._receipt)
                _require(_values(binding) == _values(self._header.account_binding))
                with read._lifecycle, read._condition, write._lock, write._arrival_lock:
                    self._local_check(c, healthy=False)
                    _require(c._active_attempt is None and not self.__draining
                             and not self.__persistence_failed)
                    owner = object.__new__(_PaperExecutionAccountingOwner)
                    owner._initialize((self,) + self.__components)
                    object.__setattr__(self, '_PaperDispatchJournalOwner__accounting_owner', owner)
                    return owner
            except BaseException:
                self._inhibit()
                c._mark_reconciliation(audit=False)
                raise

    def _accounting_check(self, owner):
        from .paper_execution_accounting import _PaperExecutionAccountingOwner
        self._thread_check()
        _require(type(owner) is _PaperExecutionAccountingOwner
                 and owner is self.__accounting_owner
                 and owner._matches_association((self,) + self.__components))
        c = self.__association[0]
        self._local_check(c, healthy=False)
        _require(not self.__persistence_failed and not self.__draining
                 and c._active_attempt is None)

    def _accounting_state(self, owner):
        # Capture-side only. No SQLite, no authentication I/O under arrival.
        self._accounting_check(owner)
        c, _, read, write = self.__association[:4]
        _require(read._lifecycle._is_owned() and read._condition._is_owned()
                 and write._lock._is_owned() and write._arrival_lock._is_owned())
        batch, state = self._callback_snapshot(c)
        cursor = self.__persistence_cursor
        _require(type(cursor) is int and 0 <= cursor <= batch.through_sequence
                 and set(self.__covered) <= set(self.__staged))
        staged = []
        for sequence, value in sorted(self.__staged.items()):
            _require(type(sequence) is int and cursor < sequence <= batch.through_sequence
                     and value.evidence.sequence == sequence
                     and value.values == _callback_values(value.evidence))
            staged.append((sequence, value.values))
        covered = []
        for sequence, value in sorted(self.__covered.items()):
            _require(value.staged is self.__staged[sequence])
            self._require_callback_coverage(value)
            covered.append((sequence, _values(value.event)))
        return (self.__history, cursor, batch.through_sequence, tuple(staged), tuple(covered),
                self.__inhibited, state, write._arrival_sequence)

    def _capture_accounting_evidence(self, owner):
        from .paper_execution_accounting import _AccountingEvidence
        c, control, read, write = self.__association[:4]
        _require(c._lock._is_owned() and control._lock._is_owned())
        self._accounting_check(owner)
        # Fresh issuer validation remains evidence-only; it cannot re-arm.
        binding = read._validate_journal_account_binding(self._receipt)
        _require(_values(binding) == _values(self._header.account_binding))
        with read._lifecycle, read._condition, write._lock, write._arrival_lock:
            state = self._accounting_state(owner)
        self._callback_io_boundary(c)
        loaded = self._load()
        _require(_values(loaded.header) == _values(self._header)
                 and tuple(_values(r) for r in loaded.records) == state[0])
        replay = j._replay_paper_execution_journal(loaded.header, loaded.records)
        _require(tuple(_values(s) for s in replay.sessions) == (_values(self._session),))
        progress = replay.callback_progress
        _require(not progress and state[1] == 0 or len(progress) == 1
                 and progress[0].session_id == self._session.session_id
                 and progress[0].processed_sequence == state[1]
                 and progress[0].through_sequence <= state[2])
        evidence = _AccountingEvidence(loaded.header, loaded.records, self._session.session_id,
            state[1], state[2], len(state[3]), len(state[4]), state[5])
        self.__accounting_handoff = (owner, evidence, state, _values(evidence.header),
                                     tuple(_values(r) for r in evidence.records))
        return evidence

    def _publish_accounting_snapshot(self, owner, evidence, candidate):
        from .paper_execution_accounting import _snapshot_matches_evidence
        c, control, read, write = self.__association[:4]
        _require(c._lock._is_owned() and control._lock._is_owned())
        self._accounting_check(owner)
        self._callback_io_boundary(c)
        durable = self._load()
        _require(_values(durable.header) == _values(self._header)
                 and tuple(_values(r) for r in durable.records) == self.__history)
        binding = read._validate_journal_account_binding(self._receipt)
        _require(_values(binding) == _values(self._header.account_binding))
        with read._lifecycle, read._condition, write._lock, write._arrival_lock:
            handoff = self.__accounting_handoff
            _require(handoff is not None and handoff[0] is owner and handoff[1] is evidence
                     and self._accounting_state(owner) == handoff[2]
                     and c._pending_order is handoff[2][6][0]
                     and _values(evidence.header) == handoff[3]
                     and tuple(_values(r) for r in evidence.records) == handoff[4]
                     and _snapshot_matches_evidence(candidate, evidence))
            owner._require_candidate(candidate, evidence)
            self.__accounting_handoff = handoff + (candidate,)
            owner._publish(candidate)  # One immutable pointer, final mutation.

    def _consume_accounting_publication(self, owner, evidence, candidate):
        c, control, read, write = self.__association[:4]
        handoff = self.__accounting_handoff
        _require(c._lock._is_owned() and control._lock._is_owned()
                 and read._lifecycle._is_owned() and read._condition._is_owned()
                 and write._lock._is_owned() and write._arrival_lock._is_owned()
                 and handoff is not None and len(handoff) == 6
                 and handoff[0] is owner and handoff[1] is evidence and handoff[5] is candidate)
        self._accounting_check(owner)
        self.__accounting_handoff = None

    def _accounting_failure(self, owner):
        # No persistence retry or diagnostic append. Existing safety first.
        _require(owner is self.__accounting_owner)
        c, control = owner._failure_controls()
        with c._lock, control._lock:
            self.__accounting_handoff = None
            self._inhibit()
            c._mark_reconciliation(audit=False)

    def _require_persistence_current(self, coordinator):
        """Pure gate; caller holds arrival. No store, clock or authentication call."""
        self._local_check(coordinator)
        write = self.__association[3]
        _require(write._arrival_lock._is_owned() and not self.__persistence_failed
                 and not self.__staged and not self.__covered
                 and not write._arrival_unusable
                 and self.__persistence_cursor == write._callback_sequence)

    def _record_callback_boundary(self, coordinator, attempt):
        write = self.__association[3]
        if (attempt is not None and attempt is self.__attempt
                and coordinator is self.__association[0] and write._bound_invocation_may_occur):
            self.__invocation_boundaries[attempt.order.client_order_id] = write._commit_callback_sequence

    def _callback_snapshot(self, coordinator):
        _, control, read, write = self.__association[:4]
        with read._lifecycle, read._condition, write._lock, write._arrival_lock:
            self._local_check(coordinator, current=False, healthy=False)
            _require(write._generation == self._session.write_generation)
            # Validate retained span metadata BEFORE paging can expand/renumber
            # it. A malformed start must never become a fabricated occurrence.
            _require(len(write._callback_evidence) == len(write._callback_run_ends)
                     and len(write._callback_evidence) <= _EVIDENCE_CAPACITY)
            expected_start = 1
            for retained, end in zip(write._callback_evidence, write._callback_run_ends):
                _require(type(retained) in _CALLBACK_TYPES and type(retained.sequence) is int
                         and retained.sequence == expected_start and type(end) is int
                         and end >= retained.sequence)
                if type(retained) is _QualifiedOrderStatusEvidence:
                    _adapt_qualified_status(retained, self._session)
                expected_start = end + 1
            _require(type(write._callback_sequence) is int
                     and expected_start == write._callback_sequence + 1
                     and write._callback_evidence_lost_at is None)
            batch = PaperTWSTransport._evidence_since(write, coordinator, self.__persistence_cursor)
            _require(type(batch.through_sequence) is int
                     and batch.through_sequence >= self.__persistence_cursor
                     and type(batch.events) is tuple and len(batch.events) <= _EVIDENCE_CAPACITY)
            expected = self.__persistence_cursor + 1
            for event in batch.events:
                _require(type(event) in _CALLBACK_TYPES and type(event.sequence) is int
                         and event.sequence == expected and event.sequence <= batch.through_sequence)
                if type(event) is _QualifiedOrderStatusEvidence:
                    _adapt_qualified_status(event, self._session)
                else:
                    _callback_projection(event, 'validation-only', self._session)
                expected += 1
            _require(batch.events or batch.through_sequence == self.__persistence_cursor)
            # Arrival failure is never concealed by an unchanged evidence sequence.
            _require(not write._arrival_unusable and not write._invalid_callback)
            pending = coordinator._pending_order
            pending_values = None if pending is None else tuple(
                (f.name, _values(getattr(pending, f.name))) for f in fields(type(pending)))
            state = (pending, pending_values, write._generation, write._closed,
                     write._reconciliation_required, write._arrival_unusable)
            return batch, state

    def _callback_revalidate(self, coordinator, state, through, *, publish=None):
        _, _, read, write = self.__association[:4]
        with read._lifecycle, read._condition, write._lock, write._arrival_lock:
            self._local_check(coordinator, current=False, healthy=False)
            pending = coordinator._pending_order
            values = None if pending is None else tuple(
                (f.name, _values(getattr(pending, f.name))) for f in fields(type(pending)))
            _require(pending is state[0] and values == state[1]
                     and write._generation == state[2] == self._session.write_generation
                     and (write._closed, write._reconciliation_required, write._arrival_unusable) == state[3:]
                     and not write._invalid_callback and write._callback_sequence >= through
                     and write._callback_evidence_lost_at is None)
            page = PaperTWSTransport._evidence_since(write, coordinator, self.__persistence_cursor)
            for event in page.events:
                staged = self.__staged.get(event.sequence)
                if staged is not None:
                    _require(_callback_values(event) == staged.values)
            for sequence, staged in self.__staged.items():
                _require(staged.evidence.sequence == sequence
                         and _callback_values(staged.evidence) == staged.values)
            if publish is not None:
                self._publish_callback_cursor(publish)

    def _append_callback(self, coordinator, event, staged):
        # Restricted evidence-only path; inhibition remains monotonic. No general
        # invocation/attempt/authorization event may enter through this method.
        _require(type(event) in _OCCURRENCE_TYPES + (j._CommissionReceived,)
                 and self.__staged.get(staged.evidence.sequence) is staged
                 and _callback_values(staged.evidence) == staged.values)
        _callback_event_agreement(staged.evidence, event, self._session)
        self._callback_io_boundary(coordinator)
        return self._append_event(event, diagnostic=True)

    def _require_committed_callback(self, event, committed):
        _require(type(committed) is _CommittedRecord
                 and committed.record.payload is event
                 and 0 < committed.record.sequence <= len(self.__history)
                 and committed.record.schema_version == self._header.schema_version
                 and committed.record.journal_id == self._header.journal_id
                 and _values(committed.record.account_binding) == _values(self._header.account_binding)
                 and _values(committed.record) == self.__history[committed.record.sequence - 1])
        _CommittedRecord.__post_init__(committed)
        return committed.record.sequence

    def _require_callback_coverage(self, coverage):
        _require(type(coverage) is _CallbackCoverage
                 and type(coverage.staged) is _StagedCallback
                 and self.__staged.get(coverage.staged.evidence.sequence) is coverage.staged
                 and coverage.staged.values == _callback_values(coverage.staged.evidence)
                 and type(coverage.event) in _OCCURRENCE_TYPES)
        self._require_committed_callback(coverage.event, coverage.committed)
        _require(_values(coverage.event.source) ==
                 _values(_callback_source(coverage.staged.evidence, self._session)))
        _callback_event_agreement(coverage.staged.evidence, coverage.event, self._session)

    def _callback_io_boundary(self, coordinator):
        self._local_check(coordinator, current=False, healthy=False)
        _, _, read, write = self.__association[:4]
        _require(not self.__persistence_failed and coordinator._active_attempt is None
                 and not write._arrival_lock._is_owned() and not write._lock._is_owned()
                 and not read._lifecycle._is_owned() and not read._condition._is_owned())

    def _cover_callback(self, coordinator, staged, attempts):
        event = staged.evidence
        _require(self.__staged.get(event.sequence) is staged
                 and _callback_values(event) == staged.values)
        if type(event) is _QualifiedOrderStatusEvidence:
            mapped = _adapt_qualified_status(event, self._session)
            committed = self._append_callback(coordinator, mapped, staged)
            return _CallbackCoverage(staged, mapped, committed)
        if type(event) is _CommissionEvidence:
            owned = self.__executions.get(event.exec_id)
            foreign = self.__foreign_executions.get(event.exec_id)
            _require(owned is None or foreign is None)
            if foreign is not None:
                _, original, committed = foreign
                reference = self._require_committed_callback(original, committed)
                mapped = j._UntrackedCommissionCallbackCovered(_callback_source(event, self._session),
                    _commission_callback_snapshot(event), reference)
                return _CallbackCoverage(staged, mapped, self._append_callback(coordinator, mapped, staged))
            if owned is None:
                return None  # Bounded staging, never a fabricated target label.
            execution, execution_receipt = owned
            self._require_committed_callback(execution, execution_receipt)
            _require(event.sequence > self.__invocation_boundaries[execution.client_order_id])
            mapped = _callback_projection(event, execution.client_order_id, self._session)
            prior = self.__commissions.get(event.exec_id)
            if prior is not None:
                original, committed = prior
                _require((mapped.client_order_id, mapped.exec_id, mapped.report_id, mapped.amount, mapped.currency) ==
                         (original.client_order_id, original.exec_id, original.report_id, original.amount, original.currency))
            else:
                committed = self._append_callback(coordinator, mapped, staged)
                original = mapped
                self.__commissions[event.exec_id] = (original, committed)
            reference = self._require_committed_callback(original, committed)
            covered = j._CommissionCallbackCovered(_callback_source(event, self._session),
                _commission_callback_snapshot(event), reference)
            return _CallbackCoverage(staged, covered, self._append_callback(coordinator, covered, staged))
        related_exec = self.__executions.get(event.execution_id) if type(event) is _ExecutionEvidence else None
        target = next((a for a in attempts if a.order_id == event.order_id), None)
        if related_exec is not None:
            _require(target is not None and target.consumed.client_order_id == related_exec[0].client_order_id)
        if target is None:
            # Exact lifetime numeric reservations define the current order owner.
            # A copied reference alone cannot adopt another numeric broker order.
            # A known execId relationship was checked BEFORE this filtering.
            if type(event) is _ExecutionEvidence:
                prior = self.__foreign_executions.get(event.execution_id)
                identity = tuple((f.name, _values(getattr(event, f.name))) for f in fields(type(event))
                                 if f.name not in ('sequence', 'generation'))
                _require(prior is None or prior[0] == identity)
            mapped = j._UntrackedCallbackObserved(_callback_source(event, self._session),
                _ordinary_callback_snapshot(event, self._session),
                j._UntrackedCallbackReason.NO_TRACKED_NUMERIC_ORDER_IN_SESSION)
            committed = self._append_callback(coordinator, mapped, staged)
            if type(event) is _ExecutionEvidence:
                self.__foreign_executions.setdefault(event.execution_id, (identity, mapped, committed))
            return _CallbackCoverage(staged, mapped, committed)
        key = target.consumed.client_order_id
        _require(target.invocation_may_have_occurred and key in self.__invocation_boundaries
                 and event.sequence > self.__invocation_boundaries[key])
        mapped = _callback_projection(event, key, self._session)
        if type(event) is _ExecutionEvidence:
            _require(event.execution_id not in self.__foreign_executions)
            if event.order_ref is None:
                anchor = self.__anchors.get(key)
                _require(anchor is not None and anchor < event.sequence
                         and event.client_id == self._session.client_id)
        identity = (type(event), tuple((name, value) for name, value in staged.values if name != 'sequence'))
        previous = self.__adjacent_observation
        if previous is not None and previous[0] + 1 == event.sequence and previous[1] == identity:
            original, committed = previous[2:]
            reference = self._require_committed_callback(original, committed)
            covered = j._DuplicateCallbackCovered(_callback_source(event, self._session),
                _ordinary_callback_snapshot(event, self._session), reference)
            occurrence = self._append_callback(coordinator, covered, staged)
            self.__adjacent_observation = (event.sequence, identity, original, committed)
            return _CallbackCoverage(staged, covered, occurrence)
        committed = self._append_callback(coordinator, mapped, staged)
        self.__adjacent_observation = (event.sequence, identity, mapped, committed)
        if type(mapped) is j._OpenOrderObserved and mapped.order_ref == key and mapped.status is not j._LifecycleStatus.UNKNOWN:
            self.__anchors[key] = min(self.__anchors.get(key, event.sequence), event.sequence)
        if type(mapped) is j._ExecutionObserved:
            self.__executions.setdefault(mapped.exec_id, (mapped, committed))
        return _CallbackCoverage(staged, mapped, committed)

    def _publish_callback_cursor(self, cursor):
        # Private interruption seam AFTER verified progress, before publication.
        self.__persistence_cursor = cursor
        for sequence in tuple(self.__covered):
            if sequence <= cursor:
                del self.__covered[sequence]
                del self.__staged[sequence]

    def _drain_callbacks(self, coordinator):
        """Owner-thread only, bounded evidence preservation; never trading recovery."""
        _require(coordinator._lock._is_owned() and coordinator._control._lock._is_owned())
        through = self.__persistence_cursor
        entered = False
        try:
            self._thread_check()
            if self.__persistence_failed:
                self._inhibit()
                coordinator._mark_reconciliation(audit=False)
                return _CallbackPersistenceResult(_CallbackPersistenceState.FAILED,
                    self.__persistence_cursor, through, len(self.__staged))
            self._local_check(coordinator, current=False, healthy=False)
            _require(not self.__persistence_failed)
            if coordinator._active_attempt is not None or self.__draining:
                return _CallbackPersistenceResult(_CallbackPersistenceState.DEFERRED,
                    self.__persistence_cursor, through, len(self.__staged))
            self._callback_io_boundary(coordinator)
            self.__draining = True
            entered = True
            for _ in range(_EVIDENCE_CAPACITY):  # Bounded work even for very long compressed runs.
                batch, state = self._callback_snapshot(coordinator)
                through = batch.through_sequence
                if not batch.events:
                    break
                self._callback_io_boundary(coordinator)
                loaded = self._load()
                _require(tuple(_values(r) for r in loaded.records) == self.__history)
                attempts = tuple(a for a in loaded.replay.attempts
                                 if a.consumed.session_id == self._session.session_id)
                for event in batch.events:
                    prior = self.__staged.get(event.sequence)
                    if prior is None:
                        _require(len(self.__staged) < _EVIDENCE_CAPACITY)
                        self.__staged[event.sequence] = _StagedCallback(event, _callback_values(event))
                    else:
                        _require(prior.values == _callback_values(event))
                # Lookahead records carry their ACTUAL sequence; no cursor changes.
                for sequence in sorted(self.__staged):
                    if sequence not in self.__covered:
                        coverage = self._cover_callback(coordinator, self.__staged[sequence], attempts)
                        if coverage is not None:
                            self._require_callback_coverage(coverage)
                            self.__covered[sequence] = coverage
                # A later durable execution can now qualify earlier commissions.
                for sequence in sorted(self.__staged):
                    if sequence not in self.__covered:
                        coverage = self._cover_callback(coordinator, self.__staged[sequence], attempts)
                        if coverage is not None:
                            self._require_callback_coverage(coverage)
                            self.__covered[sequence] = coverage
                cursor = self.__persistence_cursor
                while cursor + 1 in self.__covered:
                    cursor += 1
                    coverage = self.__covered[cursor]
                    _require(coverage.staged is self.__staged[cursor]
                             and _callback_values(coverage.staged.evidence) == coverage.staged.values)
                    self._require_callback_coverage(coverage)
                self._callback_revalidate(coordinator, state, through)
                if cursor == self.__persistence_cursor:
                    _require(len(self.__staged) < _EVIDENCE_CAPACITY
                             or through <= max(self.__staged))
                    break
                self._callback_io_boundary(coordinator)
                self._append_event(j._CallbackProgress(self._session.session_id, cursor, through), diagnostic=True)
                self._callback_revalidate(coordinator, state, through, publish=cursor)
                if cursor == through:
                    break
            final_batch, _ = self._callback_snapshot(coordinator)
            through = final_batch.through_sequence
            result_state = (_CallbackPersistenceState.CURRENT if not self.__staged
                and self.__persistence_cursor == through else _CallbackPersistenceState.PENDING)
            return _CallbackPersistenceResult(result_state, self.__persistence_cursor, through, len(self.__staged))
        except BaseException as error:
            self.__persistence_failed = True
            self._inhibit()
            coordinator._mark_reconciliation(audit=False)
            try:
                self._uncertain(coordinator, j._ReconciliationReason.TRANSPORT_POISON)
            except BaseException:
                pass  # Diagnostics never replace the secured original failure.
            if not isinstance(error, Exception):
                raise
            return _CallbackPersistenceResult(_CallbackPersistenceState.FAILED,
                self.__persistence_cursor, through, len(self.__staged))
        finally:
            if entered:
                self.__draining = False


def _create_paper_dispatch_journal_owner(coordinator, path):
    """Explicit fresh setup only, on the dispatch/store creator thread.

    No open/adopt fallback: an existing path is rejected by C3C4B. A failed
    setup permanently consumes this coordinator's single association attempt.
    """
    from .paper_dispatch import _PaperOrderDispatchCoordinator
    _require(type(coordinator) is _PaperOrderDispatchCoordinator)
    c, control, write = coordinator, coordinator._control, coordinator._write
    read, thread = control._transport, current_thread()
    store, owner = None, None
    with c._lock, control._lock, read._lifecycle, read._condition, write._lock:
        _require(not c._journal_setup_started)
        c._start_journal_setup()
        try:
            _require(c._journal_owner is None and not c._attempts
                     and c._active_attempt is None and c._pending_order is None
                     and not c._pending_confirmation and not c._broker_state_reconciliation_required
                     and not c._reconciliation_required and not control._reconciliation_required
                     and not write._requires_reconciliation() and not write._write_invocation_started
                     and write._generation > 0 and write._connected()
                     and not write._arrival_lock._is_owned())
            generation, client_id = write._generation, write._config.client_id
            receipt = read._issue_journal_account_binding()
            binding = read._validate_journal_account_binding(receipt)
            header = j._JournalHeader(2, 'journal-' + secrets.token_hex(16), binding,
                                      format_id=j._FORMAT_ID_V2)
            store = _SQLiteJournalStore._create(path, header)
            loaded = store._load()
            _require(not loaded.records and _values(loaded.header) == _values(header)
                     and store._header is header)
            session = j._SessionRegistered('session-' + secrets.token_hex(16), client_id, generation)
            owner = object.__new__(_PaperDispatchJournalOwner)
        except BaseException as error:
            c._mark_reconciliation(audit=False)
            if store is not None:
                _SQLiteJournalStore._discard_connection(store._connection)
            if not isinstance(error, Exception):
                raise
            raise ReadOnlyError(_ERROR) from None
        try:
            association = (c, control, read, write, receipt, header, store, session, thread)
            prefix = '_PaperDispatchJournalOwner'
            for name, value in (
                ('__association', association),
                ('__components', tuple(list(association))),
                ('__identity', (_values(header), _values(session), _values(receipt))),
                ('__append', _SQLiteJournalStore._append.__get__(store)),
                ('__load', _SQLiteJournalStore._load.__get__(store)),
                ('__close', _SQLiteJournalStore._close.__get__(store)),
                ('__history', ()), ('__consumed', set()), ('__inhibited', False),
                ('__closed', False), ('__proof', None), ('__proof_parts', None), ('__proof_values', None),
                ('__marker_possible', False), ('__attempt', None),
                ('__persistence_cursor', 0), ('__persistence_failed', False),
                ('__staged', {}), ('__covered', {}), ('__executions', {}),
                ('__commissions', {}), ('__foreign_executions', {}), ('__anchors', {}),
                ('__draining', False), ('__invocation_boundaries', {}), ('__adjacent_observation', None),
                ('__accounting_owner', None), ('__accounting_setup_started', False),
                ('__accounting_handoff', None)):
                object.__setattr__(owner, prefix + name, value)
            owner._authenticate(c, setup=True)
            owner._append_event(session, setup=True)
            owner._authenticate(c, setup=True)
            _require(generation == write._generation and client_id == write._config.client_id
                     and not c._attempts and not c._reconciliation_required
                     and not control._reconciliation_required and not write._requires_reconciliation())
            # Publish only after durable registration and fresh authentication.
            c._claim_journal_owner(owner)
            return owner
        except BaseException as error:
            if owner is not None:
                owner._inhibit()
            c._mark_reconciliation(audit=False)
            if store is not None:
                _SQLiteJournalStore._discard_connection(store._connection)
            if not isinstance(error, Exception):
                raise
            raise ReadOnlyError(_ERROR) from None
