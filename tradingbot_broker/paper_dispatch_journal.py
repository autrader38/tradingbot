"""Private C3C4C owner. Durable intent is evidence, never restart authority.

Setup is explicit and outside dispatch. One exact connection/thread/session is
retained for the coordinator lifetime. Callbacks never enter this module.
Private Python isolation does not protect against hostile interpreter access.
"""

from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal
import secrets
from threading import current_thread

from . import paper_execution_journal as j
from .paper_execution_journal_sqlite import _SQLiteJournalStore, _CommittedRecord
from .paper_dispatch_journal_context import _DispatchJournalContext
from .paper_execution import _timestamp_value
from .readonly_models import ReadOnlyError
from .ibkr_readonly import _JournalAccountBindingReceipt, _JournalAccountAssociation


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
                j._ClosureReason, j.AccountMode):
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


class _PaperDispatchJournalOwner:
    __slots__ = ('__association', '__components', '__identity', '__append', '__load', '__close',
                 '__history', '__consumed', '__inhibited', '__closed',
                 '__proof', '__proof_parts', '__proof_values', '__marker_possible', '__attempt')

    def __init__(self, *args, **kwargs):
        # Only the controlled setup factory publishes a registered owner.
        raise ReadOnlyError(_ERROR)

    def __setattr__(self, name, value):
        if name in ('_PaperDispatchJournalOwner__association',
                    '_PaperDispatchJournalOwner__components',
                    '_PaperDispatchJournalOwner__identity',
                    '_PaperDispatchJournalOwner__append',
                    '_PaperDispatchJournalOwner__load',
                    '_PaperDispatchJournalOwner__close') and hasattr(self, name):
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
        self._require_marker(coordinator, attempt, reservation, proof)
        self._append_event(j._SdkReturnedNormally(attempt.order.client_order_id))

    def _uncertain(self, coordinator, reason, attempt=None):
        # Safety precedes every optional persistence call and every publication.
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
            header = j._JournalHeader(1, 'journal-' + secrets.token_hex(16), binding)
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
                ('__marker_possible', False), ('__attempt', None)):
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
