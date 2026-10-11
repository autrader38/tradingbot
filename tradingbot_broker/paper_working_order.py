"""PRIVATE HISTORICAL ENTRY LIFECYCLE / REMAINDER EVIDENCE ONLY.

No current broker state, current remainder, inventory, management or recovery
authority. Only the exact registered C3C5A owner supplies publication evidence.
Private Python isolation is not protection against hostile interpreter access.
"""

from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from threading import current_thread

from tradingbot_backtest.numerics import add, subtract
from . import paper_execution_accounting as a
from . import paper_execution_journal as j
from .readonly_models import AccountMode, ReadOnlyError


_ERROR = 'PAPER_WORKING_ORDER_EVIDENCE_UNAVAILABLE'


def _require(condition):
    if not condition:
        raise ReadOnlyError(_ERROR)


class _HistoricalPrefixState(StrEnum):
    VERIFIED_HISTORICAL_PREFIX = 'VERIFIED_HISTORICAL_PREFIX'
    HISTORICAL_PREFIX_WITH_PENDING_CALLBACKS = 'HISTORICAL_PREFIX_WITH_PENDING_CALLBACKS'
    BLOCKED = 'BLOCKED'


class _LifecycleClassification(StrEnum):
    NO_QUALIFIED_LIFECYCLE = 'NO_QUALIFIED_LIFECYCLE'
    HISTORICAL_SUBMISSION_OBSERVED = 'HISTORICAL_SUBMISSION_OBSERVED'
    HISTORICAL_WORKING_OBSERVED = 'HISTORICAL_WORKING_OBSERVED'
    HISTORICAL_TERMINAL_OBSERVED = 'HISTORICAL_TERMINAL_OBSERVED'
    HISTORICAL_FILLED_REPORTED = 'HISTORICAL_FILLED_REPORTED'
    HISTORICAL_PARTIAL_TERMINAL = 'HISTORICAL_PARTIAL_TERMINAL'
    HISTORICAL_LIFECYCLE_UNRESOLVED = 'HISTORICAL_LIFECYCLE_UNRESOLVED'
    HISTORICAL_LIFECYCLE_CONTRADICTORY = 'HISTORICAL_LIFECYCLE_CONTRADICTORY'


class _LifecycleConsistency(StrEnum):
    COMPATIBLE = 'COMPATIBLE'
    UNRESOLVED = 'UNRESOLVED'
    CONTRADICTORY = 'CONTRADICTORY'


class _ObservationKind(StrEnum):
    OPEN_ORDER = 'OPEN_ORDER'
    ORDER_STATUS = 'ORDER_STATUS'


class _LifecycleObligation(StrEnum):
    FILLED_REGRESSION = 'FILLED_REGRESSION'
    REMAINING_REGRESSION = 'REMAINING_REGRESSION'
    STATUS_REGRESSION = 'STATUS_REGRESSION'
    TERMINAL_TO_WORKING = 'TERMINAL_TO_WORKING'
    CONFLICTING_TERMINAL_STATUS = 'CONFLICTING_TERMINAL_STATUS'
    STATUS_EXECUTION_QUANTITY_GAP = 'STATUS_EXECUTION_QUANTITY_GAP'
    TERMINAL_EXECUTION_DISAGREEMENT = 'TERMINAL_EXECUTION_DISAGREEMENT'
    UNKNOWN_LIFECYCLE = 'UNKNOWN_LIFECYCLE'
    UNQUALIFIED_OBSERVATION = 'UNQUALIFIED_OBSERVATION'
    EXECUTION_PRICE_COVERAGE_MISSING = 'EXECUTION_PRICE_COVERAGE_MISSING'
    STATUS_EXECUTION_PRICE_DIFFERENCE = 'STATUS_EXECUTION_PRICE_DIFFERENCE'
    ZERO_FILL_NOT_PROVEN = 'ZERO_FILL_NOT_PROVEN'
    HISTORICAL_PERSISTENCE_PENDING = 'HISTORICAL_PERSISTENCE_PENDING'
    CURRENT_LIFECYCLE_EVIDENCE_REQUIRED = 'CURRENT_LIFECYCLE_EVIDENCE_REQUIRED'
    CURRENT_REMAINDER_UNKNOWN = 'CURRENT_REMAINDER_UNKNOWN'
    CURRENT_INVENTORY_UNKNOWN = 'CURRENT_INVENTORY_UNKNOWN'
    PROTECTABLE_QUANTITY_UNQUALIFIED = 'PROTECTABLE_QUANTITY_UNQUALIFIED'


class _PrivateLifecycleValue:
    __slots__ = ()

    def __repr__(self):
        return '<PrivateHistoricalEntryLifecycleValue>'


@dataclass(frozen=True, slots=True, repr=False)
class _HistoricalLifecycleObservation(_PrivateLifecycleValue):
    kind: _ObservationKind
    source: j._CallbackSource
    status: j._LifecycleStatus
    identity_qualified: bool
    open_evidence: j._OpenOrderObserved | None
    status_evidence: j._OrderStatusObserved | None


@dataclass(frozen=True, slots=True, repr=False)
class _EntryLifecycleEvidence(_PrivateLifecycleValue):
    client_order_id: str
    order_id: int | None
    requested_quantity: Decimal | None
    recorded_execution_quantity: Decimal
    historical_filled_lower_bound: Decimal
    arithmetic_residual: Decimal | None
    recorded_execution_ids: tuple[str, ...]
    exact_recorded_average_price: a._ExactRatio | None
    observations: tuple[_HistoricalLifecycleObservation, ...]
    historical_working_observed: bool
    historical_terminal_observed: bool
    historical_terminal_statuses: tuple[j._LifecycleStatus, ...]
    executions_received_after_terminal: tuple[str, ...]
    classification: _LifecycleClassification
    consistency: _LifecycleConsistency
    historical_possible_remaining_upper_bound: Decimal | None
    obligations: tuple[_LifecycleObligation, ...]
    current_terminality_known: bool = field(default=False, init=False)
    current_terminal_status: None = field(default=None, init=False)
    current_broker_order_state: None = field(default=None, init=False)
    current_working_remainder: None = field(default=None, init=False)
    current_broker_remaining_quantity: None = field(default=None, init=False)
    currently_working_quantity: None = field(default=None, init=False)
    definitely_cancelled_quantity: None = field(default=None, init=False)
    current_bot_inventory_known: bool = field(default=False, init=False)
    current_bot_held_quantity: None = field(default=None, init=False)
    closed_quantity: None = field(default=None, init=False)
    disposition_coverage_complete: bool = field(default=False, init=False)
    protectable_quantity: None = field(default=None, init=False)
    protective_sizing_qualified: bool = field(default=False, init=False)
    no_late_fill_guarantee: bool = field(default=False, init=False)
    cancellation_authority: bool = field(default=False, init=False)
    trading_authority: bool = field(default=False, init=False)
    recovery_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class _PaperWorkingOrderEvidenceSnapshot(_PrivateLifecycleValue):
    journal_id: str
    session_id: str
    placing_client_id: int
    write_generation: int
    journal_tail: int
    persistence_cursor: int
    callback_through_sequence: int
    staged_count: int
    covered_unpublished_count: int
    journal_inhibited_at_publication: bool
    currentness_at_publication: _HistoricalPrefixState
    entries: tuple[_EntryLifecycleEvidence, ...]
    new_entry_permitted: bool = field(default=False, init=False)
    recovery_authority: bool = field(default=False, init=False)
    restart_quarantine_required: bool = field(default=True, init=False)
    account_mode: AccountMode = field(default=AccountMode.UNKNOWN, init=False)
    live_supported: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class _LifecycleEvidence(_PrivateLifecycleValue):
    # Independently reconstructed accounting values, no owner/store reference.
    accounting: a._PaperExecutionAccountingSnapshot
    placing_client_id: int
    write_generation: int


_MODELS = (_HistoricalLifecycleObservation, _EntryLifecycleEvidence,
           _PaperWorkingOrderEvidenceSnapshot, _LifecycleEvidence)
_ENUMS = (_HistoricalPrefixState, _LifecycleClassification, _LifecycleConsistency,
          _ObservationKind, _LifecycleObligation)
_EVENTS = (j._OpenOrderObserved, j._OrderStatusObserved, j._CallbackSource)
_TERMINAL = frozenset((j._LifecycleStatus.FILLED, j._LifecycleStatus.CANCELLED,
    j._LifecycleStatus.API_CANCELLED, j._LifecycleStatus.INACTIVE, j._LifecycleStatus.EXPIRED))
_WORKING = frozenset((j._LifecycleStatus.PRE_SUBMITTED, j._LifecycleStatus.SUBMITTED))
_RANK = {j._LifecycleStatus.PENDING_SUBMIT: 0, j._LifecycleStatus.PRE_SUBMITTED: 1,
         j._LifecycleStatus.SUBMITTED: 2, **{s: 3 for s in _TERMINAL}}
_CONTRADICTIONS = frozenset((_LifecycleObligation.FILLED_REGRESSION,
    _LifecycleObligation.REMAINING_REGRESSION, _LifecycleObligation.STATUS_REGRESSION,
    _LifecycleObligation.TERMINAL_TO_WORKING, _LifecycleObligation.CONFLICTING_TERMINAL_STATUS))


def _lifecycle_values(value):
    if type(value) in _MODELS:
        return type(value), tuple((f.name, _lifecycle_values(getattr(value, f.name)))
                                 for f in fields(type(value)))
    if type(value) in _ENUMS:
        return type(value), value.value
    if type(value) is tuple:
        return tuple, tuple(_lifecycle_values(v) for v in value)
    return a._model_values(value)


def _copy_value(value):
    """Copy reviewed immutable data, never arbitrary models or caller code."""
    kind = type(value)
    if kind in a._ACCOUNTING_MODELS + _EVENTS:
        return kind(**{f.name: _copy_value(getattr(value, f.name)) for f in fields(kind) if f.init})
    if kind is tuple:
        return tuple(_copy_value(v) for v in value)
    if kind is Decimal:
        _require(value.is_finite())
        return Decimal(value.as_tuple())
    if kind is datetime:
        a._model_values(value)
        return value.replace()
    a._model_values(value)
    return value


def _quantity(value, optional=False):
    _require(optional and value is None or type(value) is Decimal and value.is_finite() and value >= 0)


def _observations(entry, evidence):
    events = sorted(entry.open_observations + entry.status_observations,
                    key=lambda event: event.source.sequence)
    seen, anchors, result = {}, False, []
    for event in events:
        _require(type(event) in (j._OpenOrderObserved, j._OrderStatusObserved))
        type(event).__post_init__(event)
        source = event.source
        _require(source.session_id == evidence.accounting.session_id
                 and source.write_generation == evidence.write_generation
                 and source.sequence <= evidence.accounting.callback_through_sequence
                 and event.client_order_id == entry.client_order_id
                 and event.order_id == entry.order_id
                 and event.client_id in (None, evidence.placing_client_id))
        _require(source.sequence not in seen)
        seen[source.sequence] = event
        if type(event) is j._OpenOrderObserved:
            _require(event.order_ref in (None, entry.client_order_id)
                     and event.quantity == entry.requested_quantity and event.side == 'BUY'
                     and event.order_type == 'MKT' and event.tif == 'DAY'
                     and event.security_type == 'STK' and event.exchange in (None, 'SMART')
                     and event.symbol in (None, entry.symbol) and event.currency in (None, entry.currency))
            qualified = event.order_ref == entry.client_order_id and event.status is not j._LifecycleStatus.UNKNOWN
            anchors |= qualified
            result.append(_HistoricalLifecycleObservation(_ObservationKind.OPEN_ORDER,
                _copy_value(source), event.status, qualified, _copy_value(event), None))
        else:
            _require(add(event.filled, event.remaining) == entry.requested_quantity
                     and event.filled <= entry.requested_quantity
                     and (event.status is not j._LifecycleStatus.FILLED
                          or event.filled == entry.requested_quantity and event.remaining == 0))
            result.append(_HistoricalLifecycleObservation(_ObservationKind.ORDER_STATUS,
                _copy_value(source), event.status, anchors, None, _copy_value(event)))
    return tuple(result)


def _analyze_quantities(entry, observations, codes):
    previous = None
    for observation in observations:
        event = observation.status_evidence
        if event is None or not observation.identity_qualified:
            continue
        if previous is not None:
            if event.filled < previous.filled:
                codes.add(_LifecycleObligation.FILLED_REGRESSION)
            if event.remaining > previous.remaining:
                codes.add(_LifecycleObligation.REMAINING_REGRESSION)
        previous = event
        if entry.recorded_execution_quantity < event.filled:
            codes.add(_LifecycleObligation.STATUS_EXECUTION_QUANTITY_GAP)
        if event.status in _TERMINAL and entry.recorded_execution_quantity > event.filled:
            codes.add(_LifecycleObligation.TERMINAL_EXECUTION_DISAGREEMENT)
        if (entry.exact_recorded_average_price is not None
                and event.filled == entry.recorded_execution_quantity
                and Fraction(event.average_price) != Fraction(entry.exact_recorded_average_price.numerator,
                                                             entry.exact_recorded_average_price.denominator)):
            codes.add(_LifecycleObligation.STATUS_EXECUTION_PRICE_DIFFERENCE)


def _analyze_lifecycle(observations, codes):
    previous, terminals = None, set()
    for observation in observations:
        status = observation.status
        if status is j._LifecycleStatus.UNKNOWN:
            codes.add(_LifecycleObligation.UNKNOWN_LIFECYCLE)
            if not observation.identity_qualified:
                codes.add(_LifecycleObligation.UNQUALIFIED_OBSERVATION)
            continue
        if not observation.identity_qualified:
            codes.add(_LifecycleObligation.UNQUALIFIED_OBSERVATION)
            continue
        if previous is not None:
            if previous in _TERMINAL and status not in _TERMINAL:
                codes.add(_LifecycleObligation.TERMINAL_TO_WORKING)
            if _RANK[status] < _RANK[previous]:
                codes.add(_LifecycleObligation.STATUS_REGRESSION)
        if status in _TERMINAL:
            terminals.add(status)
        previous = status
    if len(terminals) > 1:
        codes.add(_LifecycleObligation.CONFLICTING_TERMINAL_STATUS)
    return tuple(s for s in j._LifecycleStatus if s in terminals)


def _classify(observations, terminals, codes):
    if codes & _CONTRADICTIONS:
        return _LifecycleClassification.HISTORICAL_LIFECYCLE_CONTRADICTORY, _LifecycleConsistency.CONTRADICTORY
    if codes & {_LifecycleObligation.UNKNOWN_LIFECYCLE, _LifecycleObligation.UNQUALIFIED_OBSERVATION,
                _LifecycleObligation.TERMINAL_EXECUTION_DISAGREEMENT}:
        return _LifecycleClassification.HISTORICAL_LIFECYCLE_UNRESOLVED, _LifecycleConsistency.UNRESOLVED
    if not observations:
        return _LifecycleClassification.NO_QUALIFIED_LIFECYCLE, _LifecycleConsistency.UNRESOLVED
    if j._LifecycleStatus.FILLED in terminals:
        return _LifecycleClassification.HISTORICAL_FILLED_REPORTED, _LifecycleConsistency.COMPATIBLE
    if terminals:
        partial = any(o.identity_qualified and o.status in _TERMINAL and o.status_evidence is not None
                      and o.status_evidence.filled > 0 and o.status_evidence.remaining > 0 for o in observations)
        return (_LifecycleClassification.HISTORICAL_PARTIAL_TERMINAL if partial
                else _LifecycleClassification.HISTORICAL_TERMINAL_OBSERVED), _LifecycleConsistency.COMPATIBLE
    if any(o.status in _WORKING for o in observations):
        return _LifecycleClassification.HISTORICAL_WORKING_OBSERVED, _LifecycleConsistency.COMPATIBLE
    return _LifecycleClassification.HISTORICAL_SUBMISSION_OBSERVED, _LifecycleConsistency.COMPATIBLE


def _derive_lifecycle(evidence):
    """Pure historical projection; equal output cannot establish publication authority."""
    _require(type(evidence) is _LifecycleEvidence
             and type(evidence.accounting) is a._PaperExecutionAccountingSnapshot)
    snapshot = evidence.accounting
    _require(type(evidence.placing_client_id) is int and evidence.placing_client_id > 0
             and type(evidence.write_generation) is int and evidence.write_generation > 0)
    _lifecycle_values(evidence)
    entries = []
    for entry in snapshot.entries:
        _require(type(entry) is a._EntryExecutionAccounting)
        q, e, lower = entry.requested_quantity, entry.recorded_execution_quantity, entry.historical_filled_lower_bound
        _quantity(q, optional=True); _quantity(e); _quantity(lower)
        _require(e <= lower and (q is None and e == lower == 0 and entry.arithmetic_residual is None
                 or q is not None and q > 0 and lower <= q and entry.arithmetic_residual == subtract(q, e)))
        _require(not entry.current_bot_inventory_known and entry.current_bot_held_quantity is None
                 and entry.closed_quantity is None and entry.working_remainder is None
                 and not entry.disposition_coverage_complete)
        observations = _observations(entry, evidence)
        codes = {_LifecycleObligation.CURRENT_LIFECYCLE_EVIDENCE_REQUIRED,
                 _LifecycleObligation.CURRENT_REMAINDER_UNKNOWN, _LifecycleObligation.CURRENT_INVENTORY_UNKNOWN}
        if entry.invocation_may_have_occurred:
            codes.add(_LifecycleObligation.PROTECTABLE_QUANTITY_UNQUALIFIED)
        if e == 0:
            codes.add(_LifecycleObligation.ZERO_FILL_NOT_PROVEN)
        if not entry.gross_execution_prices_complete:
            codes.add(_LifecycleObligation.EXECUTION_PRICE_COVERAGE_MISSING)
        if e < lower:
            codes.add(_LifecycleObligation.STATUS_EXECUTION_QUANTITY_GAP)
        if snapshot.currentness_at_publication is a._AccountingCurrentness.PENDING_CALLBACKS:
            codes.add(_LifecycleObligation.HISTORICAL_PERSISTENCE_PENDING)
        _analyze_quantities(entry, observations, codes)
        terminals = _analyze_lifecycle(observations, codes)
        classification, consistency = _classify(observations, terminals, codes)
        sources = {o.source.sequence for o in observations}
        late = []
        for lot in entry.lots:
            _require(type(lot) is a._ExecutionLot and lot.client_order_id == entry.client_order_id
                     and lot.order_id == entry.order_id and type(lot.source) is j._CallbackSource)
            j._CallbackSource.__post_init__(lot.source)
            _require(lot.source.session_id == snapshot.session_id
                     and lot.source.write_generation == evidence.write_generation
                     and lot.source.sequence <= snapshot.callback_through_sequence
                     and lot.source.sequence not in sources)
            sources.add(lot.source.sequence)
            if any(o.identity_qualified and o.status in _TERMINAL
                   and o.source.sequence < lot.source.sequence for o in observations):
                late.append(lot.exec_id)
        upper = subtract(q, lower) if q is not None and consistency is _LifecycleConsistency.COMPATIBLE else None
        entries.append(_EntryLifecycleEvidence(entry.client_order_id, entry.order_id, _copy_value(q),
            _copy_value(e), _copy_value(lower), _copy_value(entry.arithmetic_residual),
            tuple(lot.exec_id for lot in entry.lots), _copy_value(entry.exact_recorded_average_price), observations,
            any(o.identity_qualified and o.status in _WORKING for o in observations), bool(terminals), terminals,
            tuple(late), classification, consistency, upper,
            tuple(code for code in _LifecycleObligation if code in codes)))
    currentness = (_HistoricalPrefixState.VERIFIED_HISTORICAL_PREFIX
        if snapshot.currentness_at_publication is a._AccountingCurrentness.VERIFIED_PREFIX
        else _HistoricalPrefixState.HISTORICAL_PREFIX_WITH_PENDING_CALLBACKS)
    return _PaperWorkingOrderEvidenceSnapshot(snapshot.journal_id, snapshot.session_id,
        evidence.placing_client_id, evidence.write_generation, snapshot.journal_tail,
        snapshot.persistence_cursor, snapshot.callback_through_sequence, snapshot.staged_count,
        snapshot.covered_unpublished_count, snapshot.journal_inhibited_at_publication,
        currentness, tuple(entries))


class _PaperWorkingOrderEvidenceOwner:
    __slots__ = ('__association', '__snapshot', '__failed', '__refreshing', '__candidate',
                 '__source_values', '__publication')

    def __init__(self, *args, **kwargs):
        raise ReadOnlyError(_ERROR)

    def __repr__(self):
        return '<PrivateHistoricalEntryLifecycleOwner>'

    def __setattr__(self, name, value):
        if name.endswith('__association') and hasattr(self, name):
            raise ReadOnlyError(_ERROR)
        object.__setattr__(self, name, value)

    def __delattr__(self, name):
        raise ReadOnlyError(_ERROR)

    def _initialize(self, association):
        _require(not hasattr(self, '_PaperWorkingOrderEvidenceOwner__association'))
        self.__association = association
        self.__snapshot, self.__candidate, self.__source_values = None, None, None
        self.__publication = None
        self.__failed, self.__refreshing = False, False

    def _matches_association(self, association):
        return len(association) == len(self.__association) and all(
            x is original for x, original in zip(association, self.__association))

    @property
    def _snapshot(self): return self.__snapshot

    @property
    def _failed(self): return self.__failed

    def _accounting_values(self): return self.__source_values

    def _check_snapshot(self):
        if self.__snapshot is not None:
            _require(self.__publication is not None and self.__publication[0] is self.__snapshot
                     and self.__publication[1] == _lifecycle_values(self.__snapshot))

    def _secure_failure(self):
        self.__failed, self.__candidate = True, None
        try:
            self.__association[0]._lifecycle_failure(self)
        except BaseException:
            # Preserve original failure, including original interruption identity.
            pass
        try:
            _, journal, c, control = self.__association[:4]
            with c._lock, control._lock:
                journal._inhibit()
                c._mark_reconciliation(audit=False)
        except BaseException:
            pass

    @property
    def _currentness(self):
        if self.__failed or self.__snapshot is None:
            return _HistoricalPrefixState.BLOCKED
        try:
            self._check_snapshot()
            covered = self.__association[0]._lifecycle_currentness(self, self.__snapshot)
            return (_HistoricalPrefixState.VERIFIED_HISTORICAL_PREFIX if covered
                    else _HistoricalPrefixState.HISTORICAL_PREFIX_WITH_PENDING_CALLBACKS)
        except BaseException as error:
            self._secure_failure()
            if not isinstance(error, Exception):
                raise
            return _HistoricalPrefixState.BLOCKED

    def _require_candidate(self, candidate, evidence):
        _require(self.__candidate is not None and self.__candidate[0] is candidate
                 and self.__candidate[1] is evidence and self.__candidate[2] == _lifecycle_values(candidate)
                 and self.__candidate[3] == _lifecycle_values(evidence))

    def _publish(self, candidate):
        _require(self.__candidate is not None)
        evidence = self.__candidate[1]
        self._require_candidate(candidate, evidence)
        self.__association[0]._consume_lifecycle_publication(self, evidence, candidate)
        self.__source_values = a._model_values(evidence.accounting)
        self.__publication = (candidate, self.__candidate[2])
        self.__snapshot = candidate

    def _refresh(self):
        accounting, _, c, control = self.__association[:4]
        entered = False
        try:
            _require(current_thread() is self.__association[-1]
                     and not self.__failed and not self.__refreshing)
            self._check_snapshot()
            with c._lock, control._lock:
                self.__refreshing = True
                entered = True
                evidence = accounting._capture_lifecycle_evidence(self)
                candidate = _derive_lifecycle(evidence)
                self.__candidate = (candidate, evidence, _lifecycle_values(candidate), _lifecycle_values(evidence))
                accounting._publish_lifecycle_snapshot(self, evidence, candidate)
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


def _create_paper_working_order_evidence_owner(accounting_owner):
    _require(type(accounting_owner) is a._PaperExecutionAccountingOwner)
    try:
        return accounting_owner._create_lifecycle_owner()
    except Exception:
        raise ReadOnlyError(_ERROR) from None
