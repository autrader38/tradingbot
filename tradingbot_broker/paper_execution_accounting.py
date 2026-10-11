"""Private historical ENTRY acquisition accounting, never trading authority.

Only the exact live journal owner supplies runtime evidence. No store discovery,
callback-thread mutation, account-position adoption, exit accounting or recovery.
Private Python isolation is not protection against hostile interpreter access.
"""

from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from math import gcd
from threading import current_thread

from tradingbot_backtest.numerics import add, subtract, multiply, exact_ratio
from . import paper_execution_journal as j
from .readonly_models import ReadOnlyError


_ERROR = 'PAPER_EXECUTION_ACCOUNTING_UNAVAILABLE'


def _require(condition):
    if not condition:
        raise ReadOnlyError(_ERROR)


class _AccountingCurrentness(StrEnum):
    VERIFIED_PREFIX = 'VERIFIED_PREFIX'
    PENDING_CALLBACKS = 'PENDING_CALLBACKS'
    BLOCKED = 'BLOCKED'


class _AccountingObligation(StrEnum):
    EXECUTION_COVERAGE = 'EXECUTION_COVERAGE'
    MISSING_EXECUTION_PRICES = 'MISSING_EXECUTION_PRICES'
    COMMISSION_FINALITY_UNKNOWN = 'COMMISSION_FINALITY_UNKNOWN'
    CURRENT_INVENTORY_UNKNOWN = 'CURRENT_INVENTORY_UNKNOWN'
    DISPOSITION_COVERAGE_UNKNOWN = 'DISPOSITION_COVERAGE_UNKNOWN'
    PERSISTENCE_PENDING = 'PERSISTENCE_PENDING'


class _PrivateAccountingValue:
    __slots__ = ()

    def __repr__(self):
        return '<PrivatePaperExecutionAccountingValue>'


@dataclass(frozen=True, slots=True, repr=False)
class _ExactRatio(_PrivateAccountingValue):
    numerator: int
    denominator: int

    def __post_init__(self):
        _require(type(self.numerator) is int and type(self.denominator) is int
                 and self.denominator > 0 and gcd(self.numerator, self.denominator) == 1)


@dataclass(frozen=True, slots=True, repr=False)
class _KnownCommission(_PrivateAccountingValue):
    exec_id: str
    report_id: str
    amount: Decimal
    currency: str
    received_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class _ExecutionLot(_PrivateAccountingValue):
    client_order_id: str
    order_id: int
    exec_id: str
    security_id: str
    con_id: int
    symbol: str
    currency: str
    side: str
    shares: Decimal
    price: Decimal
    gross_notional: Decimal
    source: j._CallbackSource
    commission: _KnownCommission | None


@dataclass(frozen=True, slots=True, repr=False)
class _EntryExecutionAccounting(_PrivateAccountingValue):
    client_order_id: str
    order_id: int | None
    security_id: str | None
    symbol: str | None
    currency: str | None
    requested_quantity: Decimal | None
    recorded_execution_quantity: Decimal
    historical_filled_lower_bound: Decimal
    arithmetic_residual: Decimal | None
    lots: tuple[_ExecutionLot, ...]
    acquisition_quantity_fully_covered: bool
    gross_execution_prices_complete: bool
    recorded_gross_notional: Decimal
    exact_recorded_average_price: _ExactRatio | None
    gross_execution_cash_subtotal: Decimal
    known_commission_total: Decimal | None
    executions_with_known_commission: int
    executions_without_known_commission: int
    open_observations: tuple[j._OpenOrderObserved, ...]
    status_observations: tuple[j._OrderStatusObserved, ...]
    invocation_may_have_occurred: bool
    sdk_returned_normally: bool
    closure_reason: j._ClosureReason | None
    strong_open_order_identity_established: bool
    broker_observed: bool
    sdk_outcome_uncertain: bool
    broker_control_unresolved: bool
    reconciliation_reasons: tuple[j._ReconciliationReason, ...]
    replay_obligations: tuple[j._UnresolvedObligation, ...]
    obligations: tuple[_AccountingObligation, ...]
    commission_completeness: bool = field(default=False, init=False)
    current_bot_inventory_known: bool = field(default=False, init=False)
    disposition_coverage_complete: bool = field(default=False, init=False)
    current_bot_held_quantity: None = field(default=None, init=False)
    closed_quantity: None = field(default=None, init=False)
    working_remainder: None = field(default=None, init=False)
    fee_inclusive_net_cash: None = field(default=None, init=False)
    realized_trading_pnl: None = field(default=None, init=False)
    unrealized_pnl: None = field(default=None, init=False)
    current_marked_exposure: None = field(default=None, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class _PaperExecutionAccountingSnapshot(_PrivateAccountingValue):
    journal_id: str
    session_id: str
    journal_tail: int
    persistence_cursor: int
    callback_through_sequence: int
    staged_count: int
    covered_unpublished_count: int
    journal_inhibited_at_publication: bool
    currentness_at_publication: _AccountingCurrentness
    entries: tuple[_EntryExecutionAccounting, ...]
    new_entry_permitted: bool = field(default=False, init=False)
    recovery_authority: bool = field(default=False, init=False)
    restart_quarantine_required: bool = field(default=True, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class _AccountingEvidence(_PrivateAccountingValue):
    # Private handoff only. No broker/store/receipt reference in published data.
    header: j._JournalHeader
    records: tuple[j._JournalRecord, ...]
    session_id: str
    persistence_cursor: int
    through_sequence: int
    staged_count: int
    covered_count: int
    inhibited: bool


def _model_values(value):
    """Independent exact copies used to detect later private-model substitution."""
    from .paper_dispatch_journal import _values
    if type(value) in _ACCOUNTING_MODELS:
        return type(value), tuple((f.name, _model_values(getattr(value, f.name)))
                                 for f in fields(type(value)))
    if type(value) in (_AccountingCurrentness, _AccountingObligation, j._UnresolvedObligation):
        return type(value), value.value
    if type(value) is tuple:
        return tuple, tuple(_model_values(v) for v in value)
    return _values(value)


_ACCOUNTING_MODELS = (_ExactRatio, _KnownCommission, _ExecutionLot,
                     _EntryExecutionAccounting, _PaperExecutionAccountingSnapshot, _AccountingEvidence)


def _derive_accounting(evidence):
    """Pure projection. A resulting/equal value has no publication authority."""
    _require(type(evidence) is _AccountingEvidence and type(evidence.records) is tuple)
    replay = j._replay_paper_execution_journal(evidence.header, evidence.records)
    _require(len(replay.sessions) == 1 and replay.sessions[0].session_id == evidence.session_id)
    caught_up = (evidence.persistence_cursor == evidence.through_sequence
                 and evidence.staged_count == evidence.covered_count == 0)
    entries = []
    for attempt in replay.attempts:
        _require(attempt.consumed.session_id == evidence.session_id)
        order, contract = attempt.consumed.order, attempt.consumed.contract
        if order is not None:
            _require((order.requested_mode, order.requested_intent, order.side, order.order_type)
                     == ('PAPER', 'ENTRY', 'BUY', 'MKT'))
        if attempt.invocation_may_have_occurred:
            _require(order is not None and contract is not None
                     and (order.requested_mode, order.requested_intent, order.side, order.order_type)
                     == ('PAPER', 'ENTRY', 'BUY', 'MKT'))
        reports = {r.exec_id: r for r in attempt.commissions}
        lots, quantity, notional, fees = [], Decimal(0), Decimal(0), None
        for execution in attempt.executions:
            _require(attempt.invocation_may_have_occurred and contract is not None
                     and execution.side == 'BOT')
            gross = multiply(execution.shares, execution.price)
            quantity = add(quantity, execution.shares)
            notional = add(notional, gross)
            report = reports.get(execution.exec_id)
            commission = None if report is None else _KnownCommission(report.exec_id,
                report.report_id, report.amount, report.currency, report.received_at)
            if commission is not None:
                fees = commission.amount if fees is None else add(fees, commission.amount)
            lots.append(_ExecutionLot(attempt.consumed.client_order_id, execution.order_id,
                execution.exec_id, order.security_id, execution.con_id, order.symbol, order.currency,
                execution.side, execution.shares, execution.price, gross, execution.source, commission))
        _require(quantity == attempt.observed_execution_quantity)
        requested = None if order is None else order.quantity
        _require(requested is None or quantity <= requested)
        full = requested is not None and quantity == requested
        average = None
        if quantity > 0:
            ratio = exact_ratio(notional, quantity)
            average = _ExactRatio(ratio.numerator, ratio.denominator)
        known = sum(lot.commission is not None for lot in lots)
        obligations = {_AccountingObligation.COMMISSION_FINALITY_UNKNOWN,
                       _AccountingObligation.CURRENT_INVENTORY_UNKNOWN,
                       _AccountingObligation.DISPOSITION_COVERAGE_UNKNOWN}
        if not full:
            obligations.add(_AccountingObligation.EXECUTION_COVERAGE)
        if attempt.historical_filled_lower_bound > quantity:
            obligations.add(_AccountingObligation.MISSING_EXECUTION_PRICES)
        if not caught_up:
            obligations.add(_AccountingObligation.PERSISTENCE_PENDING)
        entries.append(_EntryExecutionAccounting(attempt.consumed.client_order_id, attempt.order_id,
            None if order is None else order.security_id, None if order is None else order.symbol,
            None if order is None else order.currency, requested, quantity,
            attempt.historical_filled_lower_bound,
            None if requested is None else subtract(requested, quantity), tuple(lots), full, full,
            notional, average, notional.copy_negate(), fees, known, len(lots) - known,
            attempt.open_observations, attempt.status_observations,
            attempt.invocation_may_have_occurred, attempt.sdk_returned_normally, attempt.closure_reason,
            attempt.strong_open_order_identity_established, attempt.broker_observed,
            attempt.sdk_outcome_uncertain, attempt.broker_control_unresolved, attempt.reconciliation_reasons,
            attempt.unresolved_obligations, tuple(o for o in _AccountingObligation if o in obligations)))
    return _PaperExecutionAccountingSnapshot(evidence.header.journal_id, evidence.session_id,
        replay.last_sequence, evidence.persistence_cursor, evidence.through_sequence,
        evidence.staged_count, evidence.covered_count, evidence.inhibited,
        _AccountingCurrentness.VERIFIED_PREFIX if caught_up else _AccountingCurrentness.PENDING_CALLBACKS,
        tuple(entries))


def _snapshot_matches_evidence(candidate, evidence):
    return (type(candidate) is _PaperExecutionAccountingSnapshot
            and (candidate.journal_id, candidate.session_id, candidate.journal_tail,
                 candidate.persistence_cursor, candidate.callback_through_sequence,
                 candidate.staged_count, candidate.covered_unpublished_count,
                 candidate.journal_inhibited_at_publication)
            == (evidence.header.journal_id, evidence.session_id, len(evidence.records),
                evidence.persistence_cursor, evidence.through_sequence, evidence.staged_count,
                evidence.covered_count, evidence.inhibited))


class _PaperExecutionAccountingOwner:
    __slots__ = ('__association', '__snapshot', '__failed', '__refreshing', '__candidate',
                 '__publication', '__lifecycle_owner', '__lifecycle_setup_started',
                 '__lifecycle_handoff')

    def __init__(self, *args, **kwargs):
        raise ReadOnlyError(_ERROR)

    def __repr__(self):
        return '<PrivatePaperExecutionAccountingOwner>'

    def __setattr__(self, name, value):
        if name.endswith(('__association', '__lifecycle_owner')) and hasattr(self, name):
            raise ReadOnlyError(_ERROR)
        object.__setattr__(self, name, value)

    def __delattr__(self, name):
        raise ReadOnlyError(_ERROR)

    def _initialize(self, association):
        _require(not hasattr(self, '_PaperExecutionAccountingOwner__association'))
        self.__association = association
        self.__snapshot, self.__failed, self.__refreshing, self.__candidate = None, False, False, None
        self.__publication, self.__lifecycle_owner, self.__lifecycle_handoff = None, None, None
        self.__lifecycle_setup_started = False

    def _matches_association(self, association):
        return (len(association) == len(self.__association)
                and all(v is original for v, original in zip(association, self.__association)))

    @property
    def _snapshot(self):
        # Historical as-of data; this accessor never promises live currentness.
        return self.__snapshot

    @property
    def _failed(self):
        return self.__failed

    def _secure_failure(self):
        self.__failed = True
        self.__candidate = None
        try:
            self.__association[0]._accounting_failure(self)
        except BaseException:
            pass  # Safety/diagnostics must not replace the original failure.

    def _failure_controls(self):
        return self.__association[1:3]

    @property
    def _currentness(self):
        if self.__failed or self.__snapshot is None:
            return _AccountingCurrentness.BLOCKED
        journal, c, control, read, write = self.__association[:5]
        try:
            journal._thread_check()
            with c._lock, control._lock, read._lifecycle, read._condition, write._lock, write._arrival_lock:
                state = journal._accounting_state(self)
                current = (len(state[0]) == self.__snapshot.journal_tail
                           and state[1] == state[2] == self.__snapshot.persistence_cursor
                           and not state[3] and not state[4])
                return (_AccountingCurrentness.VERIFIED_PREFIX if current
                        else _AccountingCurrentness.PENDING_CALLBACKS)
        except BaseException as error:
            self._secure_failure()
            if not isinstance(error, Exception):
                raise
            return _AccountingCurrentness.BLOCKED

    def _require_candidate(self, candidate, evidence):
        _require(self.__candidate is not None and candidate is self.__candidate[0]
                 and evidence is self.__candidate[1]
                 and _model_values(candidate) == self.__candidate[2])

    def _publish(self, candidate):
        _require(self.__candidate is not None)
        evidence = self.__candidate[1]
        self._require_candidate(candidate, evidence)
        self.__association[0]._consume_accounting_publication(self, evidence, candidate)
        # Retain exact successful publication provenance for the historical
        # lifecycle consumer. The accounting pointer remains the linearization.
        self.__publication = (candidate, self.__candidate[2])
        self.__snapshot = candidate

    def _create_lifecycle_owner(self):
        from .paper_working_order import _PaperWorkingOrderEvidenceOwner
        journal, c, control = self.__association[:3]
        journal._thread_check()
        with c._lock, control._lock:
            journal._accounting_check(self)
            _require(not self.__failed and not self.__refreshing
                     and not self.__lifecycle_setup_started)
            self.__lifecycle_setup_started = True
            try:
                owner = object.__new__(_PaperWorkingOrderEvidenceOwner)
                owner._initialize((self,) + self.__association)
                object.__setattr__(self, '_PaperExecutionAccountingOwner__lifecycle_owner', owner)
                return owner
            except BaseException:
                try:
                    journal._accounting_failure(self)
                except BaseException:
                    pass  # Secure failure must preserve the original interruption.
                raise

    def _lifecycle_check(self, owner):
        from .paper_working_order import _PaperWorkingOrderEvidenceOwner
        journal, c, control = self.__association[:3]
        _require(type(owner) is _PaperWorkingOrderEvidenceOwner
                 and owner is self.__lifecycle_owner
                 and owner._matches_association((self,) + self.__association)
                 and c._lock._is_owned() and control._lock._is_owned()
                 and not self.__failed and not self.__refreshing)
        journal._accounting_check(self)
        publication = self.__publication
        _require(publication is not None and self.__snapshot is publication[0]
                 and _model_values(self.__snapshot) == publication[1])

    def _lifecycle_state(self, owner):
        self._lifecycle_check(owner)
        return self.__association[0]._accounting_state(self)

    def _lifecycle_verified_source(self, owner):
        """Existing owner-thread storage verification; no capture locks during I/O."""
        journal, c, _, read, write, receipt, header, _, session = self.__association[:9]
        self._lifecycle_check(owner)
        binding = read._validate_journal_account_binding(receipt)
        from .paper_dispatch_journal import _values
        _require(_values(binding) == _values(header.account_binding))
        with read._lifecycle, read._condition, write._lock, write._arrival_lock:
            state = self._lifecycle_state(owner)
        journal._callback_io_boundary(c)
        loaded = journal._load()
        _require(_values(loaded.header) == _values(header)
                 and tuple(_values(r) for r in loaded.records) == state[0]
                 and tuple(_values(s) for s in loaded.replay.sessions) == (_values(session),))
        progress = loaded.replay.callback_progress
        _require(not progress and state[1] == 0 or len(progress) == 1
                 and progress[0].session_id == session.session_id
                 and progress[0].processed_sequence == state[1]
                 and progress[0].through_sequence <= state[2])
        evidence = _AccountingEvidence(loaded.header, loaded.records, session.session_id,
            state[1], state[2], len(state[3]), len(state[4]), state[5])
        verified = _derive_accounting(evidence)
        _require(_model_values(verified) == self.__publication[1])
        return verified, state

    def _capture_lifecycle_evidence(self, owner):
        from .paper_working_order import _LifecycleEvidence, _lifecycle_values
        _require(self.__lifecycle_handoff is None)
        verified, state = self._lifecycle_verified_source(owner)
        session = self.__association[8]
        evidence = _LifecycleEvidence(verified, session.client_id, session.write_generation)
        self.__lifecycle_handoff = (owner, evidence, self.__snapshot, state,
                                    self.__publication[1], _lifecycle_values(evidence))
        return evidence

    def _publish_lifecycle_snapshot(self, owner, evidence, candidate):
        from .paper_working_order import _lifecycle_values
        journal, c, control, read, write = self.__association[:5]
        verified, state = self._lifecycle_verified_source(owner)
        handoff = self.__lifecycle_handoff
        _require(handoff is not None and handoff[0] is owner and handoff[1] is evidence
                 and handoff[2] is self.__snapshot and handoff[3] == state
                 and handoff[4] == _model_values(verified)
                 and handoff[5] == _lifecycle_values(evidence))
        # Storage verification can outlive account/enrollment freshness. Repeat
        # authentication after that latency, before final capture synchronization.
        from .paper_dispatch_journal import _values
        binding = read._validate_journal_account_binding(self.__association[5])
        _require(_values(binding) == _values(self.__association[6].account_binding))
        with read._lifecycle, read._condition, write._lock, write._arrival_lock:
            _require(self._lifecycle_state(owner) == state
                     and c._pending_order is state[6][0])
            owner._require_candidate(candidate, evidence)
            self.__lifecycle_handoff = handoff + (candidate,)
            owner._publish(candidate)

    def _consume_lifecycle_publication(self, owner, evidence, candidate):
        _, c, control, read, write = self.__association[:5]
        handoff = self.__lifecycle_handoff
        _require(c._lock._is_owned() and control._lock._is_owned()
                 and read._lifecycle._is_owned() and read._condition._is_owned()
                 and write._lock._is_owned() and write._arrival_lock._is_owned()
                 and handoff is not None and len(handoff) == 7
                 and handoff[0] is owner and handoff[1] is evidence
                 and handoff[2] is self.__snapshot and handoff[6] is candidate
                 and self._lifecycle_state(owner) == handoff[3])
        self.__lifecycle_handoff = None

    def _lifecycle_currentness(self, owner, snapshot):
        journal, c, control, read, write = self.__association[:5]
        journal._thread_check()
        with c._lock, control._lock, read._lifecycle, read._condition, write._lock, write._arrival_lock:
            state = self._lifecycle_state(owner)
            return (len(state[0]) == snapshot.journal_tail
                    and state[1] == state[2] == snapshot.persistence_cursor
                    and not state[3] and not state[4]
                    and _model_values(self.__snapshot) == owner._accounting_values())

    def _lifecycle_failure(self, owner):
        _require(owner is self.__lifecycle_owner)
        self.__lifecycle_handoff = None
        self.__association[0]._accounting_failure(self)

    def _refresh(self):
        journal, c, control = self.__association[:3]
        entered = False
        try:
            _require(current_thread() is self.__association[-1]
                     and not self.__failed and not self.__refreshing)
            with c._lock, control._lock:
                self.__refreshing = True
                entered = True
                evidence = journal._capture_accounting_evidence(self)
                candidate = _derive_accounting(evidence)
                self.__candidate = (candidate, evidence, _model_values(candidate))
                journal._publish_accounting_snapshot(self, evidence, candidate)
                return candidate
        except BaseException as error:
            self._secure_failure()
            if not isinstance(error, Exception):
                raise
            raise ReadOnlyError(_ERROR) from None
        finally:
            if entered:
                self.__refreshing = False
                self.__candidate = None


def _create_paper_execution_accounting_owner(journal_owner):
    from .paper_dispatch_journal import _PaperDispatchJournalOwner
    _require(type(journal_owner) is _PaperDispatchJournalOwner)
    try:
        return journal_owner._create_accounting_owner()
    except Exception:
        raise ReadOnlyError(_ERROR) from None
