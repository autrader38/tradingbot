"""Private PRE5 provenance binding: live preparation and read-only durable PRE4.

Independent source intervals are not an atomic snapshot. A binding neither
correlates their observations nor supplies ownership or management authority.
"""

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from threading import current_thread

from . import paper_fresh_collection_bundle as preparation
from . import paper_decision_status_evidence as status
from . import paper_execution_journal as journal
from .paper_dispatch_journal import _PaperDispatchJournalOwner, _values
from .paper_execution_journal_sqlite import _LoadedJournal
from .readonly_models import ReadOnlyError


__all__ = ()
_ERROR = 'PREPARED_DECISION_EVIDENCE_UNAVAILABLE'
_LIMITATIONS = ('INDEPENDENT_COLLECTION_INTERVALS', 'NO_CROSS_SOURCE_ATOMICITY',
    'NO_CROSS_SOURCE_CORRELATION', 'NO_CROSS_SOURCE_CONTRADICTION_POLICY',
    'NO_ORDER_OR_ATTEMPT_OWNERSHIP', 'NO_EXECUTION_ABSENCE_PROOF',
    'NO_DELAYED_CALLBACK_OR_LATE_FILL_GUARANTEE', 'NO_BROKER_CURRENTNESS', 'NO_FINALITY',
    'NO_LIFECYCLE_OR_REMAINDER', 'NO_INVENTORY_OR_POSITION', 'NO_ACCOUNTING_INCORPORATION',
    'NO_MANAGEMENT_RECOVERY_OR_TRADING_AUTHORITY', 'NOT_RESTART_ADOPTABLE')
_SHARED = ('journal_id', 'account_binding', 'session_id', 'write_generation', 'authentication_deadline')


def _require(condition):
    if not condition:
        raise ReadOnlyError(_ERROR)


def _private(owner, kind, name):
    return object.__getattribute__(owner, kind + '__' + name)


@dataclass(frozen=True, slots=True, repr=False)
class _PreparedDecisionStatusEvidence:
    prepared: preparation._PreparedFreshCollectionEvidence
    status: status._DecisionStatusEvidence
    boundary_record_sequence: int
    journal_id: str
    account_binding: journal._JournalAccountBinding
    session_id: str
    write_generation: int
    authentication_deadline: datetime
    limitations: tuple[str, ...] = _LIMITATIONS

    def __post_init__(self):
        # Structural fixture validity is not live issuer or durable-load authority.
        try:
            preparation._token_values(self.prepared)
            journal._exact(self.status, status._DecisionStatusEvidence)
            journal._integer(self.boundary_record_sequence, 1, journal._MAX_DECISION_BOUNDARY_INTEGER)
            boundary = self.status.boundary
            header = self.status.prefix.replay.header
            _require(self.boundary_record_sequence == boundary.sequence
                     and all(_values(getattr(self, name)) == _values(getattr(self.prepared, name))
                             for name in _SHARED)
                     and header.journal_id == self.journal_id
                     and _values(header.account_binding) == _values(self.account_binding)
                     and boundary.payload.session_id == self.session_id
                     and boundary.payload.write_generation == self.write_generation
                     and self.status.identity.session_id == self.session_id
                     and self.status.identity.write_generation == self.write_generation
                     and boundary.payload.decision_at < self.authentication_deadline
                     and type(self.limitations) is tuple
                     and all(type(v) is str for v in self.limitations) and self.limitations == _LIMITATIONS)
        except Exception:
            raise ReadOnlyError(_ERROR) from None

    def __repr__(self): return '<PrivatePreparedDecisionStatusEvidence evidence-only>'


def _result_values(result):
    _require(type(result) is _PreparedDecisionStatusEvidence)
    _PreparedDecisionStatusEvidence.__post_init__(result)
    evidence = result.status
    return (preparation._token_values(result.prepared),
        _values(evidence.prefix.replay.header), tuple(_values(r) for r in evidence.prefix.records),
        tuple(_values(getattr(evidence.identity, f.name)) for f in fields(evidence.identity)),
        _values(evidence.boundary), None if evidence.selected_record is None else _values(evidence.selected_record),
        evidence.outcome.value, evidence.limitations, result.boundary_record_sequence,
        tuple(_values(getattr(result, name)) for name in _SHARED), result.limitations)


def _result_parts(result):
    # Exact immutable PRE4 node/reference integrity, not a scan of fresh source
    # observations. Canonical snapshots separately preserve numeric representation.
    parts = [result.prepared, result.account_binding, result.authentication_deadline, result.limitations]
    def visit(value):
        if is_dataclass(value):
            parts.append(value)
            for f in fields(value): visit(getattr(value, f.name))
        elif type(value) is tuple:
            parts.append(value)
            for item in value: visit(item)
        elif type(value) in (datetime, Decimal, frozenset):
            parts.append(value)
    visit(result.status)
    return tuple(parts)


class _PreparedDecisionEvidenceOwner:
    __slots__ = ('__preparation', '__prepared', '__association', '__prefix', '__load', '__require_prepared',
                 '__reading', '__result', '__result_values', '__result_parts')

    def __init__(self, *args, **kwargs): raise ReadOnlyError(_ERROR)
    def __setattr__(self, name, value): raise ReadOnlyError(_ERROR)
    def __delattr__(self, name): raise ReadOnlyError(_ERROR)
    def __repr__(self): return '<PrivatePreparedDecisionEvidenceOwner evidence-only>'
    def _set(self, name, value):
        object.__setattr__(self, '_PreparedDecisionEvidenceOwner__' + name, value)

    @property
    def _result(self): return self.__result

    def _check(self, token):
        _require(type(self) is _PreparedDecisionEvidenceOwner
                 and type(self.__preparation) is preparation._FreshCollectionPreparationOwner
                 and token is self.__prepared
                 and self.__require_prepared.__self__ is self.__preparation
                 and self.__require_prepared.__func__ is type(self.__preparation)._require_prepared)
        _require(self.__require_prepared(token) is token)
        actual = _private(self.__preparation, '_FreshCollectionPreparationOwner', 'association')
        _require(actual is self.__association
                 and _private(self.__preparation, '_FreshCollectionPreparationOwner', 'history') is self.__prefix)
        j, _, _, read, write = actual[:5]
        _require(type(j) is _PaperDispatchJournalOwner and read._prepared_decision_evidence_owner is self
                 and self.__load.__self__ is j and self.__load.__func__ is type(j)._load
                 and _values(read._generation) == _values(token.read_generation)
                 and _values(write._generation) == _values(token.write_generation)
                 and _values(read.config.client_id) == _values(token.read_client_id)
                 and _values(write._config.client_id) == _values(token.placing_client_id))

    def _locked_check(self, token):
        _require(type(self) is _PreparedDecisionEvidenceOwner
                 and current_thread() is self.__association[-1])
        _, c, control, read, write = self.__association[:5]
        with c._lock, control._lock, read._lifecycle, read._condition, write._lock, write._arrival_lock:
            self._check(token)

    def _read(self):
        return self.__load()  # Sole reviewed creator-thread hash/codec/replay read path.

    def _derive(self, token, sequence, identity):
        self._locked_check(token)
        journal._integer(sequence, 1, journal._MAX_DECISION_BOUNDARY_INTEGER)
        journal._exact(identity, status._QualifiedStatusIdentity)
        _require(identity.session_id == token.session_id and identity.write_generation == token.write_generation)
        j, c = self.__association[:2]
        j._callback_io_boundary(c)  # No active attempt or lower capture lock across SQLite.
        _require(_private(j, '_PaperDispatchJournalOwner', 'draining') is False
                 and _private(j, '_PaperDispatchJournalOwner', 'decision_active') is False)
        history = _private(j, '_PaperDispatchJournalOwner', 'history')
        loaded = self._read()
        journal._exact(loaded, _LoadedJournal)
        _require(_values(loaded.header) == _values(j._header)
                 and loaded.header.journal_id == token.journal_id
                 and _values(loaded.header.account_binding) == _values(token.account_binding)
                 and tuple(_values(r) for r in loaded.records) == history
                 and _private(j, '_PaperDispatchJournalOwner', 'history') is history
                 and tuple(_values(r) for r in loaded.records[:len(self.__prefix)]) == self.__prefix
                 and len(self.__prefix) < sequence <= len(loaded.records))
        companion = journal._replay_v3_decision_boundary_evidence(loaded.header, loaded.records)
        _require(companion.replay == loaded.replay)
        sessions = tuple(s for s in companion.replay.sessions if s.session_id == token.session_id)
        _require(len(sessions) == 1 and sessions[0].write_generation == token.write_generation
                 and sessions[0].client_id == token.placing_client_id
                 and _values(sessions[0]) == _values(j._session))
        boundary = loaded.records[sequence - 1]
        _require(boundary.sequence == sequence
                 and type(boundary.payload) is journal._QualifiedDecisionBoundaryObserved
                 and any(record is boundary for record in companion.boundary_records)
                 and boundary.payload.session_id == token.session_id
                 and boundary.payload.write_generation == token.write_generation
                 and boundary.payload.decision_at < token.authentication_deadline)
        evidence = status._evaluate_decision_status(companion, boundary, identity)
        journal._exact(evidence, status._DecisionStatusEvidence)
        _require(evidence.boundary is boundary and evidence.identity is identity
                 and _values(evidence.prefix.replay.header) == _values(loaded.header)
                 and len(evidence.prefix.records) == sequence
                 and all(a is b for a, b in zip(evidence.prefix.records, loaded.records)))
        self._locked_check(token)
        _require(_private(j, '_PaperDispatchJournalOwner', 'history') is history)
        return _PreparedDecisionStatusEvidence(token, evidence, sequence,
                                               *(getattr(token, name) for name in _SHARED))

    def _publish(self, result):
        # Fault-injection seam; publication remains a final bounded owner slot.
        return _result_values(result), _result_parts(result)

    def _clear(self):
        # No fallible notification hook can replace an original interruption.
        for name, value in (('result', None), ('result_values', None), ('result_parts', None)):
            object.__setattr__(self, '_PreparedDecisionEvidenceOwner__' + name, value)

    def _evaluate(self, token, boundary_record_sequence, identity):
        entered = False
        try:
            self._locked_check(token)
            _, c, control = self.__association[:3]
            with c._lock, control._lock:
                _require(not self.__reading)
                entered = True
                self._clear()
                object.__setattr__(self, '_PreparedDecisionEvidenceOwner__reading', True)
                history = _private(self.__association[0], '_PaperDispatchJournalOwner', 'history')
                result = self._derive(token, boundary_record_sequence, identity)
                expected_values, expected_parts = _result_values(result), _result_parts(result)
                values, parts = self._publish(result)
                self._locked_check(token)
                _require(_private(self.__association[0], '_PaperDispatchJournalOwner', 'history') is history
                         and values == expected_values and len(parts) == len(expected_parts)
                         and all(a is b for a, b in zip(parts, expected_parts))
                         and _result_values(result) == values
                         and all(a is b for a, b in zip(_result_parts(result), parts))
                         and len(_result_parts(result)) == len(parts)
                         and result.prepared is token and result.status.identity is identity
                         and result.boundary_record_sequence == boundary_record_sequence)
                self._set('result_values', values); self._set('result_parts', parts)
                self._set('result', result)
                return result
        except BaseException as error:
            if entered:
                for name in ('result', 'result_values', 'result_parts'):
                    object.__setattr__(self, '_PreparedDecisionEvidenceOwner__' + name, None)
            if not isinstance(error, Exception): raise
            raise ReadOnlyError(_ERROR) from None
        finally:
            if entered:
                object.__setattr__(self, '_PreparedDecisionEvidenceOwner__reading', False)

    def _require_evidence(self, result):
        entered = False
        try:
            self._locked_check(self.__prepared)
            _, c, control = self.__association[:3]
            with c._lock, control._lock:
                _require(not self.__reading and type(result) is _PreparedDecisionStatusEvidence
                         and result is self.__result and _result_values(result) == self.__result_values)
                parts = _result_parts(result)
                _require(len(parts) == len(self.__result_parts)
                         and all(a is b for a, b in zip(parts, self.__result_parts)))
                entered = True
                object.__setattr__(self, '_PreparedDecisionEvidenceOwner__reading', True)
                checked = self._derive(self.__prepared, result.boundary_record_sequence, result.status.identity)
                _require(_result_values(checked) == self.__result_values)
                self._locked_check(self.__prepared)
                return result
        except BaseException as error:
            if entered:
                for name in ('result', 'result_values', 'result_parts'):
                    object.__setattr__(self, '_PreparedDecisionEvidenceOwner__' + name, None)
            if not isinstance(error, Exception): raise
            raise ReadOnlyError(_ERROR) from None
        finally:
            if entered:
                object.__setattr__(self, '_PreparedDecisionEvidenceOwner__reading', False)


def _create_prepared_decision_evidence_owner(preparation_owner):
    """Exact registered preparation root, never caller journal/replay authority."""
    try:
        _require(type(preparation_owner) is preparation._FreshCollectionPreparationOwner)
        token = preparation_owner._prepared
        _require(preparation_owner._require_prepared(token) is token)
        association = _private(preparation_owner, '_FreshCollectionPreparationOwner', 'association')
        j, c, control, read, write = association[:5]
        with c._lock, control._lock, read._lifecycle, read._condition, write._lock, write._arrival_lock:
            _require(preparation_owner._require_prepared(token) is token
                     and getattr(read, '_prepared_decision_evidence_owner', None) is None)
            owner = object.__new__(_PreparedDecisionEvidenceOwner)
            for name, value in (('preparation', preparation_owner), ('prepared', token),
                    ('association', association), ('prefix', _private(preparation_owner, '_FreshCollectionPreparationOwner', 'history')),
                    ('load', type(j)._load.__get__(j)),
                    ('require_prepared', type(preparation_owner)._require_prepared.__get__(preparation_owner)),
                    ('reading', False), ('result', None), ('result_values', None), ('result_parts', None)):
                owner._set(name, value)
            read._prepared_decision_evidence_owner = owner
            owner._check(token)
            return owner
    except Exception:
        raise ReadOnlyError(_ERROR) from None
