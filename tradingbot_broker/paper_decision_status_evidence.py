"""Private pure PRE4 evidence through one exact durable V3 decision boundary.

Local received ordering is not broker event time or management currentness.
Later records never enrich the selected boundary's frozen historical result.
"""

from dataclasses import dataclass
from enum import StrEnum
from functools import wraps

from . import paper_execution_journal as j
from .readonly_models import ReadOnlyError


__all__ = ()
_ERROR = 'INVALID_DECISION_STATUS_EVIDENCE'
_LIMITATIONS = ('LOCAL_RECEIVED_STREAM_ONLY', 'KNOWN_NUMERIC_IDENTITY_ONLY',
    'UNKNOWN_IDENTITY_OCCURRENCES_NOT_ATTRIBUTED', 'NEXT_STATUS_SEQUENCE_UNKNOWN_AT_BOUNDARY',
    'NO_APPLICABILITY_BEYOND_FRONTIER', 'NO_WALL_CLOCK_STATUS_INTERVAL',
    'NO_BROKER_OR_EXCHANGE_EVENT_TIME', 'DELAYED_OR_IN_FLIGHT_CALLBACKS_POSSIBLE',
    'NO_NO_LATE_FILL_GUARANTEE', 'NO_EXECUTION_INFERENCE', 'NO_FINALITY_OR_CURRENTNESS',
    'NO_LIFECYCLE_OR_REMAINDER', 'NO_ORDER_ATTEMPT_OR_POSITION_OWNERSHIP',
    'NO_ACCOUNTING_INCORPORATION', 'NO_MANAGEMENT_RECOVERY_OR_TRADING_AUTHORITY',
    'NO_BARRIER_CLEARING_OR_RESTART_ADOPTION')


def _require(condition):
    if not condition:
        raise ReadOnlyError(_ERROR)


def _pure(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:
            raise ReadOnlyError(_ERROR) from None
    return checked


class _Opaque:
    __slots__ = ()

    def __repr__(self):
        return '<PrivateDecisionStatusEvidence evidence-only>'


@dataclass(frozen=True, slots=True, repr=False)
class _QualifiedStatusIdentity(_Opaque):
    session_id: str
    write_generation: int
    order_id: int
    client_id: int
    perm_id: int | None = None

    @_pure
    def __post_init__(self):
        j._opaque_id(self.session_id, 'session')
        j._integer(self.write_generation, 1, j._MAX_DECISION_BOUNDARY_INTEGER)
        j._integer(self.order_id, 0, j._MAX_ORDER_ID)
        j._integer(self.client_id, 0, j._MAX_ORDER_ID)
        if self.perm_id is not None:
            j._integer(self.perm_id, 0, (1 << 63) - 1)


class _DecisionStatusOutcome(StrEnum):
    SUPPORTED_AT_DECISION_BOUNDARY = 'SUPPORTED_AT_DECISION_BOUNDARY'
    NO_QUALIFIED_STATUS_THROUGH_BOUNDARY = 'NO_QUALIFIED_STATUS_THROUGH_BOUNDARY'
    AMBIGUOUS_IDENTITY = 'AMBIGUOUS_IDENTITY'


def _known_identity_value(observation, name):
    # Same known-fact rule as PRE6. Explicit protobuf zero is known; ambiguous
    # legacy zero and absence never become supplied identity. No coercion/view.
    if getattr(observation.presence, name) in (j._QualifiedStatusSourceQuality.SUPPLIED,
                                              j._QualifiedStatusSourceQuality.LEGACY_DECODED):
        return getattr(observation, name)
    return None


def _boundary_check(evidence, boundary, identity):
    j._exact(evidence, j._V3DecisionBoundaryReplayEvidence)
    j._exact(identity, _QualifiedStatusIdentity)
    j._exact(boundary, j._JournalRecord)
    _require(evidence.replay.header.schema_version == 3
             and evidence.replay.header.format_id == j._FORMAT_ID_V3
             and type(boundary.payload) is j._QualifiedDecisionBoundaryObserved
             and any(record is boundary for record in evidence.boundary_records)
             and boundary.payload.session_id == identity.session_id
             and boundary.payload.write_generation == identity.write_generation)


def _select(prefix, identity):
    """Exact source sequence wins within this qualified prefix, never journal order."""
    frontier = prefix.records[-1].payload.through_sequence
    candidates = []
    for record in prefix.records:
        event = record.payload
        if type(event) is not j._QualifiedOrderStatusObserved:
            continue
        source, observation = event.source, event.observation
        if (source.session_id == identity.session_id
                and source.write_generation == identity.write_generation
                and source.sequence <= frontier
                and _known_identity_value(observation, 'order_id') == identity.order_id
                and _known_identity_value(observation, 'client_id') == identity.client_id):
            candidates.append(record)
    outcome = _DecisionStatusOutcome
    if not candidates:
        return outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY, None
    # A numeric pair cannot select between positively incompatible qualified
    # permanent-ID facts. Zero remains an explicit fact when actually supplied.
    known_perms = {_known_identity_value(r.payload.observation, 'perm_id') for r in candidates}
    known_perms.discard(None)
    if len(known_perms) > 1:
        return outcome.AMBIGUOUS_IDENTITY, None
    selected = max(candidates, key=lambda r: r.payload.source.sequence)
    if identity.perm_id is not None:
        perm = _known_identity_value(selected.payload.observation, 'perm_id')
        if perm is None and identity.perm_id in known_perms:
            # An older matching permId cannot fill the latest observation's absence.
            return outcome.AMBIGUOUS_IDENTITY, None
        if perm != identity.perm_id:
            return outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY, None
    return outcome.SUPPORTED_AT_DECISION_BOUNDARY, selected


@dataclass(frozen=True, slots=True, repr=False)
class _DecisionStatusEvidence(_Opaque):
    prefix: j._V3DecisionBoundaryReplayEvidence
    identity: _QualifiedStatusIdentity
    boundary: j._JournalRecord
    selected_record: j._JournalRecord | None
    outcome: _DecisionStatusOutcome
    limitations: tuple[str, ...] = _LIMITATIONS

    @_pure
    def __post_init__(self):
        _boundary_check(self.prefix, self.boundary, self.identity)
        _require(self.prefix.records[-1] is self.boundary
                 and type(self.outcome) is _DecisionStatusOutcome
                 and type(self.limitations) is tuple and self.limitations == _LIMITATIONS)
        outcome, record = _select(self.prefix, self.identity)
        _require(self.outcome is outcome and self.selected_record is record)

    @property
    def decision_at(self): return self.boundary.payload.decision_at

    @property
    def through_sequence(self): return self.boundary.payload.through_sequence

    @property
    def selected_sequence(self):
        return None if self.selected_record is None else self.selected_record.payload.source.sequence

    @property
    def observation(self):
        return None if self.selected_record is None else self.selected_record.payload.observation

    @property
    def next_status_sequence(self):
        # Unknown at this boundary, not infinity. No later-record lookahead.
        return None


@_pure
def _evaluate_decision_status(evidence, boundary, identity):
    """Latest qualified identity match through N; no live receipt/time parameters.

    Full companion revalidation fails closed on corrupt input. Semantic selection
    and the returned proof contain only the prefix ending at the exact boundary.
    """
    _boundary_check(evidence, boundary, identity)
    prefix = j._replay_v3_decision_boundary_evidence(evidence.replay.header,
                                                   evidence.records[:boundary.sequence])
    outcome, selected = _select(prefix, identity)
    return _DecisionStatusEvidence(prefix, identity, boundary, selected, outcome)
