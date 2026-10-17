"""Private PRE8 positive execution lower bounds under the exact PRE7 issuer.

Received execution facts are historical, not complete fills or current inventory.
No accounting, lifecycle, management, durable publication or broker action occurs.
"""

from dataclasses import dataclass, fields
from decimal import Decimal
from enum import StrEnum
from threading import current_thread

from . import paper_attempt_attribution_evidence as pre7
from . import paper_cross_source_identity_evidence as pre6
from . import paper_execution_journal as journal
from . import paper_fresh_execution_collection as fresh
from .readonly_models import ReadOnlyError


__all__ = ()
_ERROR = 'ATTRIBUTED_EXECUTION_EVIDENCE_UNAVAILABLE'
_LIMITATIONS = ('HISTORICAL_EXECUTION_LOWER_BOUND_ONLY',
    'EXECUTION_ID_DEDUPLICATION_IS_ATTRIBUTED_ORDER_SCOPED',
    'NO_EXECUTION_COMPLETENESS_PROOF', 'ABSENCE_IS_NOT_NO_EXECUTION_PROOF',
    'NO_NO_LATE_FILL_GUARANTEE', 'INDEPENDENT_EVIDENCE_DOMAINS',
    'NO_CROSS_SOURCE_ATOMICITY', 'NO_CROSS_SOURCE_EVENT_CHRONOLOGY',
    'NO_CURRENT_BROKER_STATE', 'NO_FINALITY', 'NO_LIFECYCLE_OR_REMAINDER',
    'NO_INVENTORY_OR_POSITION', 'NO_COMMISSION_FINALITY', 'NO_CASH_ACCOUNTING_OR_PNL',
    'NO_PROTECTIVE_OWNERSHIP', 'NO_CANCEL_REPLACE_EXIT_AUTHORITY',
    'NO_MANAGEMENT_RECOVERY_OR_TRADING_AUTHORITY', 'NOT_RESTART_ADOPTABLE')


def _require(condition):
    if not condition: raise ReadOnlyError(_ERROR)


class _AttributedExecutionOutcome(StrEnum):
    ATTRIBUTION_BLOCKED = 'ATTRIBUTION_BLOCKED'
    NO_POSITIVE_EXECUTION_OBSERVED = 'NO_POSITIVE_EXECUTION_OBSERVED'
    EXECUTION_EVIDENCE_CONFLICT = 'EXECUTION_EVIDENCE_CONFLICT'
    EXECUTION_EVIDENCE_INCORPORATED = 'EXECUTION_EVIDENCE_INCORPORATED'


class _ExecutionConflictReason(StrEnum):
    EXEC_ID_REQUIRED_FACT_CONFLICT = 'EXEC_ID_REQUIRED_FACT_CONFLICT'
    LOCAL_CONTRACT_CONFLICT = 'LOCAL_CONTRACT_CONFLICT'
    LOCAL_SIDE_CONFLICT = 'LOCAL_SIDE_CONFLICT'
    INCORPORATED_QUANTITY_EXCEEDS_REQUESTED = 'INCORPORATED_QUANTITY_EXCEEDS_REQUESTED'


def _common(event):
    # Decimal numeric equality is economic agreement; source shape is untouched.
    # Optional fields are deliberately not compared across the two source models.
    return event.order_id, event.con_id, event.side, event.shares, event.price


@dataclass(frozen=True, slots=True, repr=False)
class _AttributedExecutionFact:
    exec_id: str
    durable_occurrences: tuple[journal._ExecutionObserved, ...]
    fresh_occurrences: tuple[fresh._ReconciliationExecutionEvidence, ...]

    def __post_init__(self):
        try:
            _require(type(self.exec_id) is str and type(self.durable_occurrences) is tuple
                     and type(self.fresh_occurrences) is tuple
                     and bool(self.durable_occurrences or self.fresh_occurrences))
            for event in self.durable_occurrences:
                _require(type(event) is journal._ExecutionObserved)
                journal._ExecutionObserved.__post_init__(event)
                _require(event.exec_id == self.exec_id
                         and journal._execution_identity(event)
                         == journal._execution_identity(self.durable_occurrences[0]))
            for event in self.fresh_occurrences:
                fresh._observation_copy(event)
                prior = self.fresh_occurrences[0]
                _require(event.exec_id == self.exec_id and all(
                    getattr(event, f.name) == getattr(prior, f.name)
                    for f in fields(event) if f.name != 'sequence'))
            _require(all(_common(event) == _common(self.observation)
                         for event in (*self.durable_occurrences, *self.fresh_occurrences)))
        except Exception:
            raise ReadOnlyError(_ERROR) from None

    @property
    def observation(self):
        # A representative only after required-fact agreement, never a source rank.
        return (self.durable_occurrences or self.fresh_occurrences)[0]

    def __repr__(self): return '<PrivateAttributedExecutionFact evidence-only>'


def _analyze(root):
    outcome, reason = _AttributedExecutionOutcome, _ExecutionConflictReason
    if root.outcome is not pre7._AttemptAttributionOutcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT:
        return (), (), (), None, (), outcome.ATTRIBUTION_BLOCKED
    attempt = root.attempt
    _require(attempt.consumed.order is not None and attempt.consumed.contract is not None)
    durable, candidates = attempt.executions, root.pre6.executions.candidates
    groups, reasons = {}, set()
    for domain, occurrences in enumerate((durable, candidates)):
        for event in occurrences:
            group = groups.setdefault(event.exec_id, ([], []))
            prior = next(iter(group[0] or group[1]), None)
            if prior is not None and _common(event) != _common(prior):
                reasons.add(reason.EXEC_ID_REQUIRED_FACT_CONFLICT)
            if event.con_id != attempt.consumed.contract.con_id:
                reasons.add(reason.LOCAL_CONTRACT_CONFLICT)
            if event.side != 'BOT':  # Exact existing BUY-entry journal replay rule.
                reasons.add(reason.LOCAL_SIDE_CONFLICT)
            group[domain].append(event)
    facts, quantity = [], Decimal(0)
    if not reasons:
        # Lexical execId order is canonical presentation, never broker chronology.
        for exec_id in sorted(groups):
            left, right = groups[exec_id]
            fact = _AttributedExecutionFact(exec_id, tuple(left), tuple(right))
            facts.append(fact)
            quantity = journal._sum(quantity, fact.observation.shares)
        if quantity > attempt.consumed.order.quantity:
            reasons.add(reason.INCORPORATED_QUANTITY_EXCEEDS_REQUESTED)
    if reasons:
        return durable, candidates, (), None, tuple(r for r in reason if r in reasons), outcome.EXECUTION_EVIDENCE_CONFLICT
    return durable, candidates, tuple(facts), quantity, (), (
        outcome.EXECUTION_EVIDENCE_INCORPORATED if facts else outcome.NO_POSITIVE_EXECUTION_OBSERVED)


@dataclass(frozen=True, slots=True, repr=False)
class _AttributedExecutionEvidence:
    pre7: pre7._AttemptAttributionEvidence
    durable_executions: tuple[journal._ExecutionObserved, ...]
    fresh_executions: tuple[fresh._ReconciliationExecutionEvidence, ...]
    facts: tuple[_AttributedExecutionFact, ...]
    historical_execution_quantity_lower_bound: Decimal | None
    reasons: tuple[_ExecutionConflictReason, ...]
    outcome: _AttributedExecutionOutcome
    limitations: tuple[str, ...] = _LIMITATIONS

    def __post_init__(self):
        # Syntactic construction/reconstruction is never original issuer authority.
        try:
            pre7._values(self.pre7)
            durable, candidates, facts, quantity, reasons, outcome = _analyze(self.pre7)
            _require(self.durable_executions is durable and self.fresh_executions is candidates
                     and type(self.facts) is tuple and len(self.facts) == len(facts))
            for actual, checked in zip(self.facts, facts):
                _require(type(actual) is _AttributedExecutionFact)
                _AttributedExecutionFact.__post_init__(actual)
                _require(actual.exec_id == checked.exec_id
                         and pre6._same_refs(actual.durable_occurrences, checked.durable_occurrences)
                         and pre6._same_refs(actual.fresh_occurrences, checked.fresh_occurrences))
            actual_quantity = self.historical_execution_quantity_lower_bound
            _require((actual_quantity is None if quantity is None else
                      type(actual_quantity) is Decimal and actual_quantity.as_tuple() == quantity.as_tuple())
                     and self.outcome is outcome and type(self.reasons) is tuple
                     and self.reasons == reasons and all(type(r) is _ExecutionConflictReason for r in self.reasons)
                     and type(self.limitations) is tuple and self.limitations == _LIMITATIONS
                     and all(type(v) is str for v in self.limitations))
        except Exception:
            raise ReadOnlyError(_ERROR) from None

    @property
    def attempt(self):
        if self.pre7.outcome is pre7._AttemptAttributionOutcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT:
            return self.pre7.attempt
        return None

    def __repr__(self): return '<PrivateAttributedExecutionEvidence evidence-only>'


def _incorporate(root):
    return _AttributedExecutionEvidence(root, *_analyze(root))


def _values(result):
    _require(type(result) is _AttributedExecutionEvidence)
    _AttributedExecutionEvidence.__post_init__(result)
    quantity = result.historical_execution_quantity_lower_bound
    return (pre7._values(result.pre7), None if quantity is None else quantity.as_tuple(),
            result.outcome, result.reasons, result.limitations)


class _AttributedExecutionEvidenceOwner:
    __slots__ = ('__issuer', '__require_evidence', '__root', '__root_values', '__root_parts',
                 '__association', '__evaluating', '__result', '__values', '__parts')

    def __init__(self, *args, **kwargs): raise ReadOnlyError(_ERROR)
    def __setattr__(self, name, value): raise ReadOnlyError(_ERROR)
    def __delattr__(self, name): raise ReadOnlyError(_ERROR)
    def __repr__(self): return '<PrivateAttributedExecutionEvidenceOwner evidence-only>'
    def _set(self, name, value):
        object.__setattr__(self, f'_AttributedExecutionEvidenceOwner__{name}', value)

    @property
    def _result(self): return self.__result

    def _check(self, root):
        _require(type(self) is _AttributedExecutionEvidenceOwner and current_thread() is self.__association[-1]
                 and root is self.__root and type(self.__issuer) is pre7._AttemptAttributionEvidenceOwner
                 and self.__require_evidence.__self__ is self.__issuer
                 and self.__require_evidence.__func__ is type(self.__issuer)._require_evidence
                 and object.__getattribute__(self.__issuer, '_AttemptAttributionEvidenceOwner__association') is self.__association
                 and self.__association[3]._attempt_attribution_evidence_owner is self.__issuer
                 and self.__association[3]._attributed_execution_evidence_owner is self
                 and pre7._values(root) == self.__root_values
                 and pre6._same_refs(pre6._parts(root), self.__root_parts))

    def _validate_root(self, root):
        self._check(root)
        _require(self.__require_evidence(root) is root)
        self._check(root)

    def _publish(self, result): return result  # Private interruption/substitution test seam.

    def _evaluate(self, pre7_result):
        entered = False
        try:
            self._check(pre7_result)
            _require(not self.__evaluating)
            entered = True
            object.__setattr__(self, '_AttributedExecutionEvidenceOwner__evaluating', True)
            for name in ('result', 'values', 'parts'):
                object.__setattr__(self, f'_AttributedExecutionEvidenceOwner__{name}', None)
            self._validate_root(pre7_result)
            result = _incorporate(pre7_result)
            values, parts = _values(result), pre6._parts(result)
            _require(self._publish(result) is result)
            self._validate_root(pre7_result)
            _require(_values(result) == values and pre6._same_refs(pre6._parts(result), parts))
            self._set('values', values); self._set('parts', parts)
            self._set('result', result)
            return result
        except BaseException as error:
            if entered:
                for name in ('result', 'values', 'parts'):
                    object.__setattr__(self, f'_AttributedExecutionEvidenceOwner__{name}', None)
            if not isinstance(error, Exception): raise
            raise ReadOnlyError(_ERROR) from None
        finally:
            if entered: object.__setattr__(self, '_AttributedExecutionEvidenceOwner__evaluating', False)

    def _require_evidence(self, result):
        entered = False
        try:
            self._check(self.__root)
            _require(not self.__evaluating and type(result) is _AttributedExecutionEvidence
                     and result is self.__result and _values(result) == self.__values
                     and pre6._same_refs(pre6._parts(result), self.__parts))
            entered = True
            object.__setattr__(self, '_AttributedExecutionEvidenceOwner__evaluating', True)
            self._validate_root(self.__root)
            _require(_values(result) == self.__values and pre6._same_refs(pre6._parts(result), self.__parts))
            return result
        except BaseException as error:
            if entered:
                for name in ('result', 'values', 'parts'):
                    object.__setattr__(self, f'_AttributedExecutionEvidenceOwner__{name}', None)
            if not isinstance(error, Exception): raise
            raise ReadOnlyError(_ERROR) from None
        finally:
            if entered: object.__setattr__(self, '_AttributedExecutionEvidenceOwner__evaluating', False)


def _create_attributed_execution_evidence_owner(attribution_owner):
    """One exact PRE7 publication; no caller execution list or attempt authority."""
    try:
        _require(type(attribution_owner) is pre7._AttemptAttributionEvidenceOwner)
        root = attribution_owner._result
        _require(attribution_owner._require_evidence(root) is root)
        association = object.__getattribute__(attribution_owner, '_AttemptAttributionEvidenceOwner__association')
        read = association[3]
        _require(current_thread() is association[-1] and read._attempt_attribution_evidence_owner is attribution_owner
                 and getattr(read, '_attributed_execution_evidence_owner', None) is None)
        values, parts = pre7._values(root), pre6._parts(root)
        owner = object.__new__(_AttributedExecutionEvidenceOwner)
        for name, value in (('issuer', attribution_owner), ('root', root),
                ('require_evidence', type(attribution_owner)._require_evidence.__get__(attribution_owner)),
                ('association', association), ('root_values', values), ('root_parts', parts),
                ('evaluating', False), ('result', None), ('values', None), ('parts', None)):
            owner._set(name, value)
        read._attributed_execution_evidence_owner = owner
        return owner
    except Exception:
        raise ReadOnlyError(_ERROR) from None
