"""Private PRE6 identity-only correlation under the exact live PRE5 issuer.

Separate collection intervals are not simultaneous broker truth. Known identity
conflicts are retained, never resolved into ownership or management authority.
"""

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from threading import current_thread

from . import paper_prepared_decision_evidence as pre5
from . import paper_decision_status_evidence as status
from .readonly_models import ReadOnlyError


__all__ = ()
_ERROR = 'CROSS_SOURCE_IDENTITY_EVIDENCE_UNAVAILABLE'
_LIMITATIONS = ('INDEPENDENT_COLLECTION_INTERVALS', 'NO_CROSS_SOURCE_ATOMICITY',
    'SOURCE_CORRELATION_IS_IDENTITY_ONLY', 'NO_ORDER_OR_ATTEMPT_OWNERSHIP',
    'NO_GLOBAL_PERMID_OWNERSHIP', 'NO_STATUS_PRECEDENCE', 'NO_MODIFICATION_CHRONOLOGY',
    'NO_EXECUTION_ABSENCE_PROOF', 'NO_EXECUTION_INCORPORATION',
    'NO_DELAYED_CALLBACK_OR_LATE_FILL_GUARANTEE', 'NO_BROKER_CURRENTNESS', 'NO_FINALITY',
    'NO_LIFECYCLE_OR_REMAINDER', 'NO_INVENTORY_OR_POSITION', 'NO_ACCOUNTING_INCORPORATION',
    'NO_MANAGEMENT_RECOVERY_OR_TRADING_AUTHORITY', 'NOT_RESTART_ADOPTABLE')


def _require(condition):
    if not condition:
        raise ReadOnlyError(_ERROR)


class _Opaque:
    __slots__ = ()
    def __repr__(self): return '<PrivateCrossSourceIdentityEvidence evidence-only>'


class _IdentitySource(StrEnum):
    OPEN_ORDERS = 'OPEN_ORDERS'
    EXECUTIONS = 'EXECUTIONS'
    COMPLETED_ORDERS = 'COMPLETED_ORDERS'
    DECISION_STATUS = 'DECISION_STATUS'


class _SourceObservationCoverage(StrEnum):
    NO_QUALIFIED_CANDIDATE_OBSERVED = 'NO_QUALIFIED_CANDIDATE_OBSERVED'
    TARGET_MATCH_OBSERVED = 'TARGET_MATCH_OBSERVED'
    PERMID_UNRESOLVED = 'PERMID_UNRESOLVED'
    IDENTITY_CONFLICT_OBSERVED = 'IDENTITY_CONFLICT_OBSERVED'


class _IdentityContradictionReason(StrEnum):
    DURABLE_IDENTITY_AMBIGUITY = 'DURABLE_IDENTITY_AMBIGUITY'
    REQUESTED_PERMID_CONFLICT = 'REQUESTED_PERMID_CONFLICT'
    KNOWN_PERMID_CONFLICT = 'KNOWN_PERMID_CONFLICT'


class _CrossSourceIdentityOutcome(StrEnum):
    CORRELATED_NO_PROVEN_IDENTITY_CONTRADICTION = 'CORRELATED_NO_PROVEN_IDENTITY_CONTRADICTION'
    PARTIAL_IDENTITY_COVERAGE = 'PARTIAL_IDENTITY_COVERAGE'
    NO_QUALIFIED_FRESH_MATCH = 'NO_QUALIFIED_FRESH_MATCH'
    IDENTITY_CONTRADICTION = 'IDENTITY_CONTRADICTION'
    DURABLE_IDENTITY_AMBIGUOUS = 'DURABLE_IDENTITY_AMBIGUOUS'


_FRESH_SOURCES = (_IdentitySource.OPEN_ORDERS, _IdentitySource.EXECUTIONS,
                  _IdentitySource.COMPLETED_ORDERS)


@dataclass(frozen=True, slots=True, repr=False)
class _SourceIdentityCorrelation(_Opaque):
    source: _IdentitySource
    candidates: tuple
    matches: tuple
    unresolved_perm_id: tuple
    conflicts: tuple

    @property
    def coverage(self):
        if self.conflicts: return _SourceObservationCoverage.IDENTITY_CONFLICT_OBSERVED
        if self.unresolved_perm_id: return _SourceObservationCoverage.PERMID_UNRESOLVED
        if self.matches: return _SourceObservationCoverage.TARGET_MATCH_OBSERVED
        return _SourceObservationCoverage.NO_QUALIFIED_CANDIDATE_OBSERVED


@dataclass(frozen=True, slots=True, repr=False)
class _KnownPermIdFact(_Opaque):
    source: _IdentitySource
    observation: object
    perm_id: int


def _known_fresh(event, name):
    # Original receipt validators establish the boolean/supplied-set agreement.
    # Supplied-but-unavailable permId is still unknown. Explicit numeric zero in
    # a source that qualifies it stays zero; no truthiness or hidden SDK default.
    return getattr(event, name) if getattr(event.presence, name) is True else None


def _source_parts(source, observations, identity):
    candidates, matches, unresolved, conflicts = [], [], [], []
    facts = []
    for event in observations:
        if (_known_fresh(event, 'order_id') != identity.order_id
                or _known_fresh(event, 'client_id') != identity.client_id):
            continue
        candidates.append(event)
        perm = _known_fresh(event, 'perm_id')
        if perm is not None:
            facts.append(_KnownPermIdFact(source, event, perm))
        if identity.perm_id is None or perm == identity.perm_id:
            matches.append(event)
        elif perm is None:
            unresolved.append(event)
        else:
            conflicts.append(event)
    return _SourceIdentityCorrelation(source, tuple(candidates), tuple(matches),
                                      tuple(unresolved), tuple(conflicts)), tuple(facts)


def _analyze(root):
    # Called only after PRE5 structural/issuer validation. Source-local validators
    # are reused by PRE5; this is not a second receipt qualification algorithm.
    identity = root.status.identity
    receipts = (root.prepared.open_order_receipt, root.prepared.execution_receipt,
                root.prepared.completed_order_receipt)
    correlations, facts = [], []
    for source, receipt in zip(_FRESH_SOURCES, receipts):
        correlation, known = _source_parts(source, receipt.observations, identity)
        correlations.append(correlation)
        facts.extend(known)
    durable = root.status
    if durable.outcome is status._DecisionStatusOutcome.SUPPORTED_AT_DECISION_BOUNDARY:
        perm = status._known_identity_value(durable.observation, 'perm_id')
        if perm is not None:
            facts.append(_KnownPermIdFact(_IdentitySource.DECISION_STATUS, durable.observation, perm))
    reasons = []
    if durable.outcome is status._DecisionStatusOutcome.AMBIGUOUS_IDENTITY:
        reasons.append(_IdentityContradictionReason.DURABLE_IDENTITY_AMBIGUITY)
    if identity.perm_id is not None and any(f.perm_id != identity.perm_id for f in facts):
        reasons.append(_IdentityContradictionReason.REQUESTED_PERMID_CONFLICT)
    if len({f.perm_id for f in facts}) > 1:
        reasons.append(_IdentityContradictionReason.KNOWN_PERMID_CONFLICT)
    outcome = _CrossSourceIdentityOutcome
    if durable.outcome is status._DecisionStatusOutcome.AMBIGUOUS_IDENTITY:
        result = outcome.DURABLE_IDENTITY_AMBIGUOUS
    elif reasons:
        result = outcome.IDENTITY_CONTRADICTION
    elif not any(c.candidates for c in correlations):
        result = outcome.NO_QUALIFIED_FRESH_MATCH
    elif any(not c.matches or c.unresolved_perm_id for c in correlations):
        result = outcome.PARTIAL_IDENTITY_COVERAGE
    else:
        result = outcome.CORRELATED_NO_PROVEN_IDENTITY_CONTRADICTION
    return tuple(correlations), tuple(facts), tuple(reasons), result


def _same_refs(left, right):
    return type(left) is tuple and len(left) == len(right) and all(a is b for a, b in zip(left, right))


@dataclass(frozen=True, slots=True, repr=False)
class _CrossSourceIdentityEvidence(_Opaque):
    pre5: pre5._PreparedDecisionStatusEvidence
    identity: status._QualifiedStatusIdentity
    open_orders: _SourceIdentityCorrelation
    executions: _SourceIdentityCorrelation
    completed_orders: _SourceIdentityCorrelation
    decision_status: status._DecisionStatusEvidence
    known_perm_id_facts: tuple[_KnownPermIdFact, ...]
    reasons: tuple[_IdentityContradictionReason, ...]
    outcome: _CrossSourceIdentityOutcome
    limitations: tuple[str, ...] = _LIMITATIONS

    def __post_init__(self):
        # Syntactic construction is never live issuer qualification.
        try:
            pre5._result_values(self.pre5)
            _require(self.identity is self.pre5.status.identity and self.decision_status is self.pre5.status)
            expected, facts, reasons, outcome = _analyze(self.pre5)
            for actual, checked in zip((self.open_orders, self.executions, self.completed_orders), expected):
                _require(type(actual) is _SourceIdentityCorrelation and actual.source is checked.source)
                for name in ('candidates', 'matches', 'unresolved_perm_id', 'conflicts'):
                    _require(_same_refs(getattr(actual, name), getattr(checked, name)))
            _require(type(self.known_perm_id_facts) is tuple and len(self.known_perm_id_facts) == len(facts))
            for actual, checked in zip(self.known_perm_id_facts, facts):
                _require(type(actual) is _KnownPermIdFact and actual.source is checked.source
                         and actual.observation is checked.observation and type(actual.perm_id) is int
                         and actual.perm_id == checked.perm_id)
            _require(_same_refs(self.reasons, reasons) and self.outcome is outcome
                     and type(self.limitations) is tuple and self.limitations == _LIMITATIONS
                     and all(type(v) is str for v in self.limitations))
        except Exception:
            raise ReadOnlyError(_ERROR) from None


def _correlate(root):
    correlations, facts, reasons, outcome = _analyze(root)
    return _CrossSourceIdentityEvidence(root, root.status.identity, *correlations, root.status,
                                        facts, reasons, outcome)


def _values(result):
    _require(type(result) is _CrossSourceIdentityEvidence)
    _CrossSourceIdentityEvidence.__post_init__(result)
    # All derived values are independently checked against the retained root.
    return pre5._result_values(result.pre5), result.outcome, result.reasons, result.limitations


def _parts(value):
    # Capture both original source observations and derived tuple/node identity.
    # Decimal shape and primitive types are separately guarded by PRE5 values.
    result = []
    def visit(item):
        if is_dataclass(item):
            result.append(item)
            for field in fields(item): visit(getattr(item, field.name))
        elif type(item) is tuple:
            result.append(item)
            for child in item: visit(child)
        elif type(item) in (datetime, Decimal, frozenset):
            result.append(item)
    visit(value)
    return tuple(result)


class _CrossSourceIdentityEvidenceOwner(_Opaque):
    __slots__ = ('__issuer', '__require_evidence', '__root', '__root_values', '__root_parts',
                 '__association', '__evaluating', '__result', '__values', '__parts')

    def __init__(self, *args, **kwargs): raise ReadOnlyError(_ERROR)
    def __setattr__(self, name, value): raise ReadOnlyError(_ERROR)
    def __delattr__(self, name): raise ReadOnlyError(_ERROR)
    def _set(self, name, value):
        object.__setattr__(self, f'_CrossSourceIdentityEvidenceOwner__{name}', value)

    @property
    def _result(self): return self.__result

    def _check(self, root):
        _require(type(self) is _CrossSourceIdentityEvidenceOwner and current_thread() is self.__association[-1]
                 and root is self.__root and type(self.__issuer) is pre5._PreparedDecisionEvidenceOwner
                 and self.__require_evidence.__self__ is self.__issuer
                 and self.__require_evidence.__func__ is type(self.__issuer)._require_evidence
                 and pre5._private(self.__issuer, '_PreparedDecisionEvidenceOwner', 'association') is self.__association
                 and self.__association[3]._prepared_decision_evidence_owner is self.__issuer
                 and self.__association[3]._cross_source_identity_evidence_owner is self
                 and pre5._result_values(root) == self.__root_values
                 and _same_refs(_parts(root), self.__root_parts))

    def _validate_root(self, root):
        self._check(root)
        _require(self.__require_evidence(root) is root)  # Sole live/durable qualification authority.
        self._check(root)

    def _publish(self, result):
        return result  # Private fault-injection seam, no external callback surface.

    def _evaluate(self, pre5_result):
        entered = False
        try:
            self._check(pre5_result)
            _require(not self.__evaluating)
            entered = True
            object.__setattr__(self, '_CrossSourceIdentityEvidenceOwner__evaluating', True)
            for name in ('result', 'values', 'parts'):
                object.__setattr__(self, f'_CrossSourceIdentityEvidenceOwner__{name}', None)
            self._validate_root(pre5_result)
            result = _correlate(pre5_result)
            values, parts = _values(result), _parts(result)
            _require(self._publish(result) is result)
            self._validate_root(pre5_result)
            _require(_values(result) == values and _same_refs(_parts(result), parts))
            self._set('values', values); self._set('parts', parts)
            self._set('result', result)
            return result
        except BaseException as error:
            if entered:
                for name in ('result', 'values', 'parts'):
                    object.__setattr__(self, f'_CrossSourceIdentityEvidenceOwner__{name}', None)
            if not isinstance(error, Exception): raise
            raise ReadOnlyError(_ERROR) from None
        finally:
            if entered: object.__setattr__(self, '_CrossSourceIdentityEvidenceOwner__evaluating', False)

    def _require_evidence(self, result):
        entered = False
        try:
            self._check(self.__root)
            _require(not self.__evaluating and type(result) is _CrossSourceIdentityEvidence
                     and result is self.__result and _values(result) == self.__values
                     and _same_refs(_parts(result), self.__parts))
            entered = True
            object.__setattr__(self, '_CrossSourceIdentityEvidenceOwner__evaluating', True)
            self._validate_root(self.__root)
            _require(_values(result) == self.__values and _same_refs(_parts(result), self.__parts))
            return result
        except BaseException as error:
            if entered:
                for name in ('result', 'values', 'parts'):
                    object.__setattr__(self, f'_CrossSourceIdentityEvidenceOwner__{name}', None)
            if not isinstance(error, Exception): raise
            raise ReadOnlyError(_ERROR) from None
        finally:
            if entered: object.__setattr__(self, '_CrossSourceIdentityEvidenceOwner__evaluating', False)


def _create_cross_source_identity_evidence_owner(prepared_decision_owner):
    """Bind one exact currently issued PRE5 result; no caller receipt/identity input."""
    try:
        _require(type(prepared_decision_owner) is pre5._PreparedDecisionEvidenceOwner)
        root = prepared_decision_owner._result
        _require(prepared_decision_owner._require_evidence(root) is root)
        association = pre5._private(prepared_decision_owner, '_PreparedDecisionEvidenceOwner', 'association')
        read = association[3]
        _require(current_thread() is association[-1] and read._prepared_decision_evidence_owner is prepared_decision_owner
                 and getattr(read, '_cross_source_identity_evidence_owner', None) is None)
        values, parts = pre5._result_values(root), _parts(root)
        owner = object.__new__(_CrossSourceIdentityEvidenceOwner)
        for name, value in (('issuer', prepared_decision_owner), ('root', root),
                ('require_evidence', type(prepared_decision_owner)._require_evidence.__get__(prepared_decision_owner)),
                ('association', association), ('root_values', values), ('root_parts', parts),
                ('evaluating', False), ('result', None), ('values', None), ('parts', None)):
            owner._set(name, value)
        read._cross_source_identity_evidence_owner = owner
        return owner
    except Exception:
        raise ReadOnlyError(_ERROR) from None
