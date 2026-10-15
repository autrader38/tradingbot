"""Private PRE7 numeric-pair attribution through an exact frozen PRE6 prefix.

Invocation possibility is not acceptance, lifecycle, management or trading authority.
"""

from dataclasses import dataclass
from enum import StrEnum
from threading import current_thread

from . import paper_cross_source_identity_evidence as pre6
from . import paper_decision_status_evidence as status
from . import paper_execution_journal as journal
from .readonly_models import ReadOnlyError


__all__ = ()
_ERROR = 'ATTEMPT_ATTRIBUTION_EVIDENCE_UNAVAILABLE'
_LIMITATIONS = ('ATTRIBUTION_IS_NUMERIC_PAIR_ONLY',
    'LOCAL_ATTRIBUTION_DOES_NOT_ESTABLISH_PERMID_OWNERSHIP', 'NO_SDK_ACCEPTANCE_PROOF',
    'NO_BROKER_ACCEPTANCE_PROOF', 'NO_EXCHANGE_ACCEPTANCE_PROOF',
    'INDEPENDENT_COLLECTION_INTERVALS', 'NO_CROSS_SOURCE_ATOMICITY',
    'NO_EXECUTION_ABSENCE_PROOF', 'NO_DELAYED_CALLBACK_OR_LATE_FILL_GUARANTEE',
    'NO_BROKER_CURRENTNESS', 'NO_FINALITY', 'NO_LIFECYCLE_OR_REMAINDER',
    'NO_INVENTORY_OR_POSITION', 'NO_ACCOUNTING_INCORPORATION', 'NO_PROTECTIVE_OWNERSHIP',
    'NO_CANCEL_REPLACE_EXIT_AUTHORITY', 'NO_MANAGEMENT_RECOVERY_OR_TRADING_AUTHORITY',
    'NOT_RESTART_ADOPTABLE')


def _require(condition):
    if not condition: raise ReadOnlyError(_ERROR)


class _AttemptAttributionOutcome(StrEnum):
    ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT = 'ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT'
    IDENTITY_EVIDENCE_BLOCKED = 'IDENTITY_EVIDENCE_BLOCKED'
    NO_BROKER_NUMERIC_PAIR_SUPPORT = 'NO_BROKER_NUMERIC_PAIR_SUPPORT'
    NO_LOCAL_NUMERIC_RESERVATION = 'NO_LOCAL_NUMERIC_RESERVATION'
    LOCAL_SESSION_MISMATCH = 'LOCAL_SESSION_MISMATCH'
    LOCAL_GENERATION_MISMATCH = 'LOCAL_GENERATION_MISMATCH'
    LOCAL_CLIENT_MISMATCH = 'LOCAL_CLIENT_MISMATCH'
    LOCAL_ATTEMPT_NOT_INVOCATION_ENABLED = 'LOCAL_ATTEMPT_NOT_INVOCATION_ENABLED'


def _select(root):
    """Original validated frozen replay only; no newer load or callback economics."""
    outcome = _AttemptAttributionOutcome
    if root.outcome in (pre6._CrossSourceIdentityOutcome.DURABLE_IDENTITY_AMBIGUOUS,
                        pre6._CrossSourceIdentityOutcome.IDENTITY_CONTRADICTION):
        return None, None, None, outcome.IDENTITY_EVIDENCE_BLOCKED
    if (root.decision_status.outcome is not status._DecisionStatusOutcome.SUPPORTED_AT_DECISION_BOUNDARY
            and not any(source.candidates for source in
                        (root.open_orders, root.executions, root.completed_orders))):
        return None, None, None, outcome.NO_BROKER_NUMERIC_PAIR_SUPPORT
    identity = root.identity
    replay = root.pre5.status.prefix.replay
    sessions = tuple(s for s in replay.sessions if s.session_id == identity.session_id)
    _require(len(sessions) <= 1)
    if not sessions:
        return None, None, None, outcome.LOCAL_SESSION_MISMATCH
    session = sessions[0]
    if session.write_generation != identity.write_generation:
        return session, None, None, outcome.LOCAL_GENERATION_MISMATCH
    if session.client_id != identity.client_id:
        return session, None, None, outcome.LOCAL_CLIENT_MISMATCH
    reservations = tuple(pair for pair in replay.reserved_order_ids if pair[0] == identity.order_id)
    _require(len(reservations) <= 1)
    if not reservations:
        return session, None, None, outcome.NO_LOCAL_NUMERIC_RESERVATION
    reservation = reservations[0]
    attempts = tuple(a for a in replay.attempts if a.consumed.client_order_id == reservation[1])
    _require(len(attempts) == 1)
    attempt = attempts[0]
    _require(attempt.order_id == identity.order_id)
    if attempt.consumed.session_id != identity.session_id:
        return session, reservation, attempt, outcome.LOCAL_SESSION_MISMATCH
    if attempt.invocation_may_have_occurred is not True:
        return session, reservation, attempt, outcome.LOCAL_ATTEMPT_NOT_INVOCATION_ENABLED
    return session, reservation, attempt, outcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT


@dataclass(frozen=True, slots=True, repr=False)
class _AttemptAttributionEvidence:
    pre6: pre6._CrossSourceIdentityEvidence
    identity: status._QualifiedStatusIdentity
    session: journal._SessionRegistered | None
    reservation: tuple[int, str] | None
    attempt: journal._AttemptReplayState | None
    outcome: _AttemptAttributionOutcome
    limitations: tuple[str, ...] = _LIMITATIONS

    def __post_init__(self):
        # Structural fixture construction does not confer original issuer authority.
        try:
            pre6._values(self.pre6)
            _require(self.identity is self.pre6.identity)
            session, reservation, attempt, outcome = _select(self.pre6)
            _require(self.session is session and self.reservation is reservation
                     and self.attempt is attempt and self.outcome is outcome
                     and type(self.limitations) is tuple and self.limitations == _LIMITATIONS
                     and all(type(v) is str for v in self.limitations))
        except Exception:
            raise ReadOnlyError(_ERROR) from None

    @property
    def client_order_id(self):
        if self.outcome is _AttemptAttributionOutcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT:
            return self.attempt.consumed.client_order_id
        return None

    def __repr__(self): return '<PrivateAttemptAttributionEvidence evidence-only>'


def _attribute(root):
    return _AttemptAttributionEvidence(root, root.identity, *_select(root))


def _values(result):
    _require(type(result) is _AttemptAttributionEvidence)
    _AttemptAttributionEvidence.__post_init__(result)
    return pre6._values(result.pre6), result.outcome, result.limitations


class _AttemptAttributionEvidenceOwner:
    __slots__ = ('__issuer', '__require_evidence', '__root', '__root_values', '__root_parts',
                 '__association', '__evaluating', '__result', '__values', '__parts')

    def __init__(self, *args, **kwargs): raise ReadOnlyError(_ERROR)
    def __setattr__(self, name, value): raise ReadOnlyError(_ERROR)
    def __delattr__(self, name): raise ReadOnlyError(_ERROR)
    def __repr__(self): return '<PrivateAttemptAttributionEvidenceOwner evidence-only>'
    def _set(self, name, value):
        object.__setattr__(self, f'_AttemptAttributionEvidenceOwner__{name}', value)

    @property
    def _result(self): return self.__result

    def _check(self, root):
        _require(type(self) is _AttemptAttributionEvidenceOwner and current_thread() is self.__association[-1]
                 and root is self.__root and type(self.__issuer) is pre6._CrossSourceIdentityEvidenceOwner
                 and self.__require_evidence.__self__ is self.__issuer
                 and self.__require_evidence.__func__ is type(self.__issuer)._require_evidence
                 and object.__getattribute__(self.__issuer, '_CrossSourceIdentityEvidenceOwner__association') is self.__association
                 and self.__association[3]._cross_source_identity_evidence_owner is self.__issuer
                 and self.__association[3]._attempt_attribution_evidence_owner is self
                 and pre6._values(root) == self.__root_values
                 and pre6._same_refs(pre6._parts(root), self.__root_parts))

    def _validate_root(self, root):
        self._check(root)
        _require(self.__require_evidence(root) is root)
        self._check(root)

    def _publish(self, result): return result  # Private interruption/substitution test seam.

    def _evaluate(self, pre6_result):
        entered = False
        try:
            self._check(pre6_result)
            _require(not self.__evaluating)
            entered = True
            object.__setattr__(self, '_AttemptAttributionEvidenceOwner__evaluating', True)
            for name in ('result', 'values', 'parts'):
                object.__setattr__(self, f'_AttemptAttributionEvidenceOwner__{name}', None)
            self._validate_root(pre6_result)
            result = _attribute(pre6_result)
            values, parts = _values(result), pre6._parts(result)
            _require(self._publish(result) is result)
            self._validate_root(pre6_result)
            _require(_values(result) == values and pre6._same_refs(pre6._parts(result), parts))
            self._set('values', values); self._set('parts', parts)
            self._set('result', result)
            return result
        except BaseException as error:
            if entered:
                for name in ('result', 'values', 'parts'):
                    object.__setattr__(self, f'_AttemptAttributionEvidenceOwner__{name}', None)
            if not isinstance(error, Exception): raise
            raise ReadOnlyError(_ERROR) from None
        finally:
            if entered: object.__setattr__(self, '_AttemptAttributionEvidenceOwner__evaluating', False)

    def _require_evidence(self, result):
        entered = False
        try:
            self._check(self.__root)
            _require(not self.__evaluating and type(result) is _AttemptAttributionEvidence
                     and result is self.__result and _values(result) == self.__values
                     and pre6._same_refs(pre6._parts(result), self.__parts))
            entered = True
            object.__setattr__(self, '_AttemptAttributionEvidenceOwner__evaluating', True)
            self._validate_root(self.__root)
            _require(_values(result) == self.__values and pre6._same_refs(pre6._parts(result), self.__parts))
            return result
        except BaseException as error:
            if entered:
                for name in ('result', 'values', 'parts'):
                    object.__setattr__(self, f'_AttemptAttributionEvidenceOwner__{name}', None)
            if not isinstance(error, Exception): raise
            raise ReadOnlyError(_ERROR) from None
        finally:
            if entered: object.__setattr__(self, '_AttemptAttributionEvidenceOwner__evaluating', False)


def _create_attempt_attribution_evidence_owner(cross_source_owner):
    """One exact PRE6 publication; no caller identity, journal or attempt authority."""
    try:
        _require(type(cross_source_owner) is pre6._CrossSourceIdentityEvidenceOwner)
        root = cross_source_owner._result
        _require(cross_source_owner._require_evidence(root) is root)
        association = object.__getattribute__(cross_source_owner, '_CrossSourceIdentityEvidenceOwner__association')
        read = association[3]
        _require(current_thread() is association[-1] and read._cross_source_identity_evidence_owner is cross_source_owner
                 and getattr(read, '_attempt_attribution_evidence_owner', None) is None)
        values, parts = pre6._values(root), pre6._parts(root)
        owner = object.__new__(_AttemptAttributionEvidenceOwner)
        for name, value in (('issuer', cross_source_owner), ('root', root),
                ('require_evidence', type(cross_source_owner)._require_evidence.__get__(cross_source_owner)),
                ('association', association), ('root_values', values), ('root_parts', parts),
                ('evaluating', False), ('result', None), ('values', None), ('parts', None)):
            owner._set(name, value)
        read._attempt_attribution_evidence_owner = owner
        return owner
    except Exception:
        raise ReadOnlyError(_ERROR) from None
