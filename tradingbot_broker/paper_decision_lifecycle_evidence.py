"""Private PRE9 historical reports and execution-derived possible remainder bounds.

A selected received status is not current broker state. Independent evidence
domains have no common economic event clock and never grant management authority.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from threading import current_thread

from tradingbot_backtest.numerics import add, subtract
from . import paper_attributed_execution_evidence as pre8
from . import paper_cross_source_identity_evidence as pre6
from . import paper_decision_status_evidence as status
from . import paper_execution_journal as journal
from .readonly_models import ReadOnlyError


__all__ = ()
_ERROR = 'DECISION_LIFECYCLE_EVIDENCE_UNAVAILABLE'
_LIMITATIONS = ('HISTORICAL_DECISION_BOUNDARY_EVIDENCE_ONLY',
    'SELECTED_STATUS_IS_LATEST_RECEIVED_THROUGH_FRONTIER_NOT_CURRENT_STATE',
    'EXECUTION_QUANTITY_IS_LOWER_BOUND_ONLY', 'REMAINING_QUANTITY_IS_UPPER_BOUND_ONLY',
    'NO_CROSS_DOMAIN_EVENT_CHRONOLOGY', 'NO_EXECUTION_COMPLETENESS_PROOF',
    'ABSENCE_IS_NOT_NO_EXECUTION_PROOF', 'DELAYED_OR_IN_FLIGHT_CALLBACKS_POSSIBLE',
    'NO_NO_LATE_FILL_GUARANTEE', 'NO_BROKER_CURRENTNESS', 'NO_FINALITY',
    'NO_CURRENT_WORKING_REMAINDER', 'NO_CURRENT_INVENTORY_OR_POSITION',
    'NO_COMMISSION_OR_CASH_ACCOUNTING', 'NO_PROTECTIVE_OWNERSHIP',
    'NO_CANCEL_REPLACE_EXIT_AUTHORITY', 'NO_MANAGEMENT_RECOVERY_OR_TRADING_AUTHORITY',
    'NOT_RESTART_ADOPTABLE')


def _require(condition):
    if not condition: raise ReadOnlyError(_ERROR)


class _DecisionLifecycleClassification(StrEnum):
    BLOCKED = 'BLOCKED'
    NO_QUALIFIED_DECISION_STATUS = 'NO_QUALIFIED_DECISION_STATUS'
    HISTORICAL_SUBMISSION_REPORTED = 'HISTORICAL_SUBMISSION_REPORTED'
    HISTORICAL_WORKING_REPORTED = 'HISTORICAL_WORKING_REPORTED'
    HISTORICAL_FILLED_REPORTED = 'HISTORICAL_FILLED_REPORTED'
    HISTORICAL_TERMINAL_REPORTED = 'HISTORICAL_TERMINAL_REPORTED'
    HISTORICAL_LIFECYCLE_UNRESOLVED = 'HISTORICAL_LIFECYCLE_UNRESOLVED'
    HISTORICAL_LIFECYCLE_CONFLICT = 'HISTORICAL_LIFECYCLE_CONFLICT'


class _DecisionLifecycleConsistency(StrEnum):
    COMPATIBLE = 'COMPATIBLE'
    UNRESOLVED = 'UNRESOLVED'
    CONFLICTING = 'CONFLICTING'
    BLOCKED = 'BLOCKED'


class _DecisionLifecycleReason(StrEnum):
    STATUS_FILLED_EXCEEDS_REQUESTED = 'STATUS_FILLED_EXCEEDS_REQUESTED'
    STATUS_REMAINING_EXCEEDS_REQUESTED = 'STATUS_REMAINING_EXCEEDS_REQUESTED'
    STATUS_QUANTITY_SUM_MISMATCH = 'STATUS_QUANTITY_SUM_MISMATCH'
    FILLED_STATUS_FILLED_QUANTITY_MISMATCH = 'FILLED_STATUS_FILLED_QUANTITY_MISMATCH'
    FILLED_STATUS_REMAINING_QUANTITY_MISMATCH = 'FILLED_STATUS_REMAINING_QUANTITY_MISMATCH'


class _DecisionLifecycleObligation(StrEnum):
    UPSTREAM_EVIDENCE_BLOCKED = 'UPSTREAM_EVIDENCE_BLOCKED'
    AMBIGUOUS_DECISION_STATUS = 'AMBIGUOUS_DECISION_STATUS'
    NO_QUALIFIED_DECISION_STATUS = 'NO_QUALIFIED_DECISION_STATUS'
    STATUS_FIELD_UNAVAILABLE = 'STATUS_FIELD_UNAVAILABLE'
    STATUS_UNKNOWN = 'STATUS_UNKNOWN'
    STATUS_FILLED_UNAVAILABLE = 'STATUS_FILLED_UNAVAILABLE'
    STATUS_REMAINING_UNAVAILABLE = 'STATUS_REMAINING_UNAVAILABLE'
    CROSS_DOMAIN_EVENT_CHRONOLOGY_UNKNOWN = 'CROSS_DOMAIN_EVENT_CHRONOLOGY_UNKNOWN'
    EXECUTION_COMPLETENESS_UNKNOWN = 'EXECUTION_COMPLETENESS_UNKNOWN'
    NO_LATE_FILL_GUARANTEE = 'NO_LATE_FILL_GUARANTEE'
    CURRENT_BROKER_STATE_REQUIRED = 'CURRENT_BROKER_STATE_REQUIRED'
    CURRENT_REMAINDER_UNKNOWN = 'CURRENT_REMAINDER_UNKNOWN'
    CURRENT_INVENTORY_UNKNOWN = 'CURRENT_INVENTORY_UNKNOWN'


_BASE_OBLIGATIONS = tuple(_DecisionLifecycleObligation[name] for name in (
    'CROSS_DOMAIN_EVENT_CHRONOLOGY_UNKNOWN', 'EXECUTION_COMPLETENESS_UNKNOWN',
    'NO_LATE_FILL_GUARANTEE', 'CURRENT_BROKER_STATE_REQUIRED',
    'CURRENT_REMAINDER_UNKNOWN', 'CURRENT_INVENTORY_UNKNOWN'))
_STATUS_CLASSIFICATION = {
    journal._LifecycleStatus.PENDING_SUBMIT: _DecisionLifecycleClassification.HISTORICAL_SUBMISSION_REPORTED,
    journal._LifecycleStatus.PRE_SUBMITTED: _DecisionLifecycleClassification.HISTORICAL_WORKING_REPORTED,
    journal._LifecycleStatus.SUBMITTED: _DecisionLifecycleClassification.HISTORICAL_WORKING_REPORTED,
    journal._LifecycleStatus.FILLED: _DecisionLifecycleClassification.HISTORICAL_FILLED_REPORTED,
    journal._LifecycleStatus.CANCELLED: _DecisionLifecycleClassification.HISTORICAL_TERMINAL_REPORTED,
    journal._LifecycleStatus.API_CANCELLED: _DecisionLifecycleClassification.HISTORICAL_TERMINAL_REPORTED,
    journal._LifecycleStatus.INACTIVE: _DecisionLifecycleClassification.HISTORICAL_TERMINAL_REPORTED,
    journal._LifecycleStatus.EXPIRED: _DecisionLifecycleClassification.HISTORICAL_TERMINAL_REPORTED,
    journal._LifecycleStatus.UNKNOWN: _DecisionLifecycleClassification.HISTORICAL_LIFECYCLE_UNRESOLVED,
}


def _known(observation, name):
    # Exact existing PRE4 known-fact rule. Valid legacy quantity zero is decoded,
    # not ambiguous; the source validator forbids inventing ambiguous quantities.
    if getattr(observation.presence, name) in (journal._QualifiedStatusSourceQuality.SUPPLIED,
                                              journal._QualifiedStatusSourceQuality.LEGACY_DECODED):
        return getattr(observation, name)
    return None


def _ordered_obligations(obligations):
    return tuple(o for o in _DecisionLifecycleObligation if o in obligations)


def _blocked(obligation):
    return (None, None, None, None, _DecisionLifecycleClassification.BLOCKED,
            _DecisionLifecycleConsistency.BLOCKED, (),
            _ordered_obligations((*_BASE_OBLIGATIONS, obligation)))


def _analyze(root):
    outcome, classification, consistency = (pre8._AttributedExecutionOutcome,
        _DecisionLifecycleClassification, _DecisionLifecycleConsistency)
    obligation = _DecisionLifecycleObligation
    if root.outcome in (outcome.ATTRIBUTION_BLOCKED, outcome.EXECUTION_EVIDENCE_CONFLICT):
        return _blocked(obligation.UPSTREAM_EVIDENCE_BLOCKED)
    _require(root.outcome in (outcome.NO_POSITIVE_EXECUTION_OBSERVED,
                             outcome.EXECUTION_EVIDENCE_INCORPORATED))
    selected = root.pre7.pre6.pre5.status
    if selected.outcome is status._DecisionStatusOutcome.AMBIGUOUS_IDENTITY:
        return _blocked(obligation.AMBIGUOUS_DECISION_STATUS)
    quantity = root.pre7.attempt.consumed.order.quantity
    executions = root.historical_execution_quantity_lower_bound
    journal._amount(quantity, positive=True)
    journal._derived_amount(executions)
    _require(executions <= quantity)
    upper = subtract(quantity, executions)
    journal._derived_amount(upper)
    obligations = set(_BASE_OBLIGATIONS)
    if selected.outcome is status._DecisionStatusOutcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY:
        obligations.add(obligation.NO_QUALIFIED_DECISION_STATUS)
        return (upper, None, None, None, classification.NO_QUALIFIED_DECISION_STATUS,
                consistency.UNRESOLVED, (), _ordered_obligations(obligations))
    _require(selected.outcome is status._DecisionStatusOutcome.SUPPORTED_AT_DECISION_BOUNDARY)
    observation = selected.observation
    raw_status = _known(observation, 'status')
    reported = None if raw_status is None else journal._LifecycleStatus(raw_status)
    filled, remaining = _known(observation, 'filled'), _known(observation, 'remaining')
    if reported is None: obligations.add(obligation.STATUS_FIELD_UNAVAILABLE)
    elif reported is journal._LifecycleStatus.UNKNOWN: obligations.add(obligation.STATUS_UNKNOWN)
    if filled is None: obligations.add(obligation.STATUS_FILLED_UNAVAILABLE)
    if remaining is None: obligations.add(obligation.STATUS_REMAINING_UNAVAILABLE)
    reasons, reason = set(), _DecisionLifecycleReason
    # These tests involve only one selected status and local Q. In particular,
    # neither status quantity is compared to the independent execution evidence.
    if filled is not None and filled > quantity: reasons.add(reason.STATUS_FILLED_EXCEEDS_REQUESTED)
    if remaining is not None and remaining > quantity: reasons.add(reason.STATUS_REMAINING_EXCEEDS_REQUESTED)
    if filled is not None and remaining is not None and add(filled, remaining) != quantity:
        reasons.add(reason.STATUS_QUANTITY_SUM_MISMATCH)
    if reported is journal._LifecycleStatus.FILLED:
        if filled is not None and filled != quantity: reasons.add(reason.FILLED_STATUS_FILLED_QUANTITY_MISMATCH)
        if remaining is not None and remaining != 0: reasons.add(reason.FILLED_STATUS_REMAINING_QUANTITY_MISMATCH)
    ordered = tuple(r for r in reason if r in reasons)
    if reasons:
        kind, agreement = classification.HISTORICAL_LIFECYCLE_CONFLICT, consistency.CONFLICTING
    else:
        kind = (classification.HISTORICAL_LIFECYCLE_UNRESOLVED if reported is None
                else _STATUS_CLASSIFICATION[reported])
        agreement = (consistency.UNRESOLVED if reported in (None, journal._LifecycleStatus.UNKNOWN)
                     or filled is None or remaining is None else consistency.COMPATIBLE)
    return upper, reported, filled, remaining, kind, agreement, ordered, _ordered_obligations(obligations)


@dataclass(frozen=True, slots=True, repr=False)
class _DecisionLifecycleEvidence:
    pre8: pre8._AttributedExecutionEvidence
    historical_possible_remaining_upper_bound: Decimal | None
    status_reported_lifecycle: journal._LifecycleStatus | None
    status_reported_filled: Decimal | None
    status_reported_remaining: Decimal | None
    classification: _DecisionLifecycleClassification
    consistency: _DecisionLifecycleConsistency
    reasons: tuple[_DecisionLifecycleReason, ...]
    obligations: tuple[_DecisionLifecycleObligation, ...]
    limitations: tuple[str, ...] = _LIMITATIONS

    def __post_init__(self):
        try:
            pre8._values(self.pre8)
            upper, reported, filled, remaining, kind, agreement, reasons, obligations = _analyze(self.pre8)
            actual = self.historical_possible_remaining_upper_bound
            _require((actual is None if upper is None else
                      type(actual) is Decimal and actual.as_tuple() == upper.as_tuple())
                     and self.status_reported_lifecycle is reported
                     and self.status_reported_filled is filled and self.status_reported_remaining is remaining
                     and self.classification is kind and self.consistency is agreement
                     and type(self.reasons) is tuple and self.reasons == reasons
                     and all(type(r) is _DecisionLifecycleReason for r in self.reasons)
                     and type(self.obligations) is tuple and self.obligations == obligations
                     and all(type(o) is _DecisionLifecycleObligation for o in self.obligations)
                     and type(self.limitations) is tuple and self.limitations == _LIMITATIONS
                     and all(type(v) is str for v in self.limitations))
        except Exception:
            raise ReadOnlyError(_ERROR) from None

    @property
    def decision_status(self): return self.pre8.pre7.pre6.pre5.status

    @property
    def selected_observation(self):
        return None if self.consistency is _DecisionLifecycleConsistency.BLOCKED else self.decision_status.observation

    @property
    def requested_quantity(self):
        if self.consistency is not _DecisionLifecycleConsistency.BLOCKED:
            return self.pre8.pre7.attempt.consumed.order.quantity
        return None

    @property
    def execution_quantity_lower_bound(self):
        if self.consistency is not _DecisionLifecycleConsistency.BLOCKED:
            return self.pre8.historical_execution_quantity_lower_bound
        return None

    def __repr__(self): return '<PrivateDecisionLifecycleEvidence historical-only>'


def _derive(root):
    return _DecisionLifecycleEvidence(root, *_analyze(root))


def _values(result):
    _require(type(result) is _DecisionLifecycleEvidence)
    _DecisionLifecycleEvidence.__post_init__(result)
    upper = result.historical_possible_remaining_upper_bound
    return (pre8._values(result.pre8), None if upper is None else upper.as_tuple(),
            result.status_reported_lifecycle, result.classification, result.consistency,
            result.reasons, result.obligations, result.limitations)


class _DecisionLifecycleEvidenceOwner:
    __slots__ = ('__issuer', '__require_evidence', '__root', '__root_values', '__root_parts',
                 '__association', '__evaluating', '__result', '__values', '__parts')

    def __init__(self, *args, **kwargs): raise ReadOnlyError(_ERROR)
    def __setattr__(self, name, value): raise ReadOnlyError(_ERROR)
    def __delattr__(self, name): raise ReadOnlyError(_ERROR)
    def __repr__(self): return '<PrivateDecisionLifecycleEvidenceOwner historical-only>'
    def _set(self, name, value):
        object.__setattr__(self, f'_DecisionLifecycleEvidenceOwner__{name}', value)

    @property
    def _result(self): return self.__result

    def _check(self, root):
        _require(type(self) is _DecisionLifecycleEvidenceOwner and current_thread() is self.__association[-1]
                 and root is self.__root and type(self.__issuer) is pre8._AttributedExecutionEvidenceOwner
                 and self.__require_evidence.__self__ is self.__issuer
                 and self.__require_evidence.__func__ is type(self.__issuer)._require_evidence
                 and object.__getattribute__(self.__issuer, '_AttributedExecutionEvidenceOwner__association') is self.__association
                 and self.__association[3]._attributed_execution_evidence_owner is self.__issuer
                 and self.__association[3]._decision_lifecycle_evidence_owner is self
                 and pre8._values(root) == self.__root_values
                 and pre6._same_refs(pre6._parts(root), self.__root_parts))

    def _validate_root(self, root):
        self._check(root)
        _require(self.__require_evidence(root) is root)
        self._check(root)

    def _publish(self, result): return result  # Private interruption/substitution test seam.

    def _evaluate(self, pre8_result):
        entered = False
        try:
            self._check(pre8_result)
            _require(not self.__evaluating)
            entered = True
            object.__setattr__(self, '_DecisionLifecycleEvidenceOwner__evaluating', True)
            for name in ('result', 'values', 'parts'):
                object.__setattr__(self, f'_DecisionLifecycleEvidenceOwner__{name}', None)
            self._validate_root(pre8_result)
            result = _derive(pre8_result)
            values, parts = _values(result), pre6._parts(result)
            _require(self._publish(result) is result)
            self._validate_root(pre8_result)
            _require(_values(result) == values and pre6._same_refs(pre6._parts(result), parts))
            self._set('values', values); self._set('parts', parts)
            self._set('result', result)
            return result
        except BaseException as error:
            if entered:
                for name in ('result', 'values', 'parts'):
                    object.__setattr__(self, f'_DecisionLifecycleEvidenceOwner__{name}', None)
            if not isinstance(error, Exception): raise
            raise ReadOnlyError(_ERROR) from None
        finally:
            if entered: object.__setattr__(self, '_DecisionLifecycleEvidenceOwner__evaluating', False)

    def _require_evidence(self, result):
        entered = False
        try:
            self._check(self.__root)
            _require(not self.__evaluating and type(result) is _DecisionLifecycleEvidence
                     and result is self.__result and _values(result) == self.__values
                     and pre6._same_refs(pre6._parts(result), self.__parts))
            entered = True
            object.__setattr__(self, '_DecisionLifecycleEvidenceOwner__evaluating', True)
            self._validate_root(self.__root)
            _require(_values(result) == self.__values and pre6._same_refs(pre6._parts(result), self.__parts))
            return result
        except BaseException as error:
            if entered:
                for name in ('result', 'values', 'parts'):
                    object.__setattr__(self, f'_DecisionLifecycleEvidenceOwner__{name}', None)
            if not isinstance(error, Exception): raise
            raise ReadOnlyError(_ERROR) from None
        finally:
            if entered: object.__setattr__(self, '_DecisionLifecycleEvidenceOwner__evaluating', False)


def _create_decision_lifecycle_evidence_owner(attributed_execution_owner):
    """One exact PRE8 publication; no caller status, quantity or attempt authority."""
    try:
        _require(type(attributed_execution_owner) is pre8._AttributedExecutionEvidenceOwner)
        root = attributed_execution_owner._result
        _require(attributed_execution_owner._require_evidence(root) is root)
        association = object.__getattribute__(attributed_execution_owner, '_AttributedExecutionEvidenceOwner__association')
        read = association[3]
        _require(current_thread() is association[-1] and read._attributed_execution_evidence_owner is attributed_execution_owner
                 and getattr(read, '_decision_lifecycle_evidence_owner', None) is None)
        values, parts = pre8._values(root), pre6._parts(root)
        owner = object.__new__(_DecisionLifecycleEvidenceOwner)
        for name, value in (('issuer', attributed_execution_owner), ('root', root),
                ('require_evidence', type(attributed_execution_owner)._require_evidence.__get__(attributed_execution_owner)),
                ('association', association), ('root_values', values), ('root_parts', parts),
                ('evaluating', False), ('result', None), ('values', None), ('parts', None)):
            owner._set(name, value)
        read._decision_lifecycle_evidence_owner = owner
        return owner
    except Exception:
        raise ReadOnlyError(_ERROR) from None
