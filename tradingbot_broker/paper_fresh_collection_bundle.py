"""Private live receipt preparation; independent intervals, never broker truth.

Only the original collection owners consume receipts. The resulting token is
memory-only issuer evidence; it neither requests data nor binds a decision time.
"""

from dataclasses import dataclass
from datetime import datetime
from threading import current_thread

from . import paper_fresh_open_order_collection as pre1
from . import paper_fresh_execution_collection as pre2
from . import paper_fresh_completed_order_collection as pre3
from .ibkr_readonly import ReadOnlyTWSTransport
from .paper_dispatch_journal import _PaperDispatchJournalOwner, _values
from .paper_execution_journal import _JournalAccountBinding
from .readonly_models import ReadOnlyError


__all__ = ()
_ERROR = 'FRESH_COLLECTION_PREPARATION_UNAVAILABLE'
_LIMITATIONS = ('INDEPENDENT_COLLECTION_INTERVALS', 'NO_CROSS_SOURCE_ATOMICITY',
    'NO_CROSS_SOURCE_CORRELATION_YET', 'NO_DECISION_TIME_BINDING_YET',
    'NO_DELAYED_CALLBACK_OR_LATE_FILL_GUARANTEE', 'NO_CURRENTNESS_FINALITY_OR_OWNERSHIP',
    'NO_ACCOUNTING_MANAGEMENT_RECOVERY_OR_TRADING_AUTHORITY', 'NOT_RESTART_ADOPTABLE')
_SHARED = ('account_binding', 'journal_id', 'session_id', 'read_generation',
           'write_generation', 'read_client_id', 'placing_client_id', 'authentication_deadline')
_SOURCES = ((pre1, '_FreshOpenOrderCollectionOwner', '_FreshOpenOrderReceipt', '_fresh_open_order_owner'),
            (pre2, '_FreshExecutionCollectionOwner', '_FreshExecutionReceipt', '_fresh_execution_owner'),
            (pre3, '_FreshCompletedOrderCollectionOwner', '_FreshCompletedOrderReceipt', '_fresh_completed_order_owner'))


def _require(condition):
    if not condition:
        raise ReadOnlyError(_ERROR)


def _private(owner, kind, name):
    return object.__getattribute__(owner, kind + '__' + name)


def _validate_receipts(receipts):
    """Canonical source validators are checks, never replacement receipt issuers."""
    _require(type(receipts) is tuple and len(receipts) == 3)
    values = []
    shared = None
    for receipt, (module, _, kind, _) in zip(receipts, _SOURCES):
        _require(type(receipt) is getattr(module, kind))
        values.append(module._receipt_values(receipt))
        _require(type(receipt.scope) is str and receipt.scope == module._SCOPE
                 and receipt.outcome is module._CollectionOutcome.COMPLETED
                 and receipt.sdk_result is module._SDKRequestResult.RETURNED_NORMALLY
                 and receipt.completion_observed is True and receipt.boundary_changed is False
                 and type(receipt.completion_marker) is int
                 and type(receipt.coverage_limitations) is tuple
                 and all(type(v) is str for v in receipt.coverage_limitations)
                 and receipt.coverage_limitations == module._LIMITATIONS)
        current = tuple(_values(getattr(receipt, name)) for name in _SHARED)
        _require(shared is None or current == shared)
        shared = current
    return tuple(values), shared


@dataclass(frozen=True, slots=True, repr=False)
class _PreparedFreshCollectionEvidence:
    open_order_receipt: pre1._FreshOpenOrderReceipt
    execution_receipt: pre2._FreshExecutionReceipt
    completed_order_receipt: pre3._FreshCompletedOrderReceipt
    account_binding: _JournalAccountBinding
    journal_id: str
    session_id: str
    read_generation: int
    write_generation: int
    read_client_id: int
    placing_client_id: int
    authentication_deadline: datetime
    limitations: tuple[str, ...] = _LIMITATIONS

    def __post_init__(self):
        _, shared = _validate_receipts((self.open_order_receipt, self.execution_receipt,
                                        self.completed_order_receipt))
        _require(tuple(_values(getattr(self, name)) for name in _SHARED) == shared
                 and type(self.limitations) is tuple
                 and all(type(v) is str for v in self.limitations)
                 and self.limitations == _LIMITATIONS)

    def __repr__(self): return '<PrivatePreparedFreshCollectionEvidence evidence-only>'


def _token_values(token):
    _require(type(token) is _PreparedFreshCollectionEvidence)
    _PreparedFreshCollectionEvidence.__post_init__(token)
    values, shared = _validate_receipts((token.open_order_receipt, token.execution_receipt,
                                       token.completed_order_receipt))
    return values, shared, token.limitations


class _FreshCollectionPreparationOwner:
    __slots__ = ('__association', '__owners', '__receipts', '__consume', '__account_association',
                 '__clients', '__configs', '__snapshot', '__clock', '__history', '__identity',
                 '__started', '__failed', '__prepared', '__prepared_values', '__prepared_parts')

    def __init__(self, *args, **kwargs): raise ReadOnlyError(_ERROR)
    def __setattr__(self, name, value): raise ReadOnlyError(_ERROR)
    def __delattr__(self, name): raise ReadOnlyError(_ERROR)
    def __repr__(self): return '<PrivateFreshCollectionPreparationOwner evidence-only>'
    def _set(self, name, value):
        object.__setattr__(self, '_FreshCollectionPreparationOwner__' + name, value)

    @property
    def _prepared(self): return self.__prepared
    @property
    def _failed(self): return self.__failed

    def _association_check(self, *, before_publication=False):
        journal, c, control, read, write, receipt, header, store, session, thread = self.__association
        _require(type(self) is _FreshCollectionPreparationOwner
                 and current_thread() is thread and not self.__failed)
        journal._local_check(c, healthy=False)
        components = _private(journal, '_PaperDispatchJournalOwner', 'components')
        _require(len(components) == 9 and all(a is b for a, b in zip(components, self.__association[1:]))
                 and read._fresh_collection_preparation_owner is self
                 and read._journal_account_association is self.__account_association
                 and read._client is self.__clients[0] and write._client is self.__clients[1]
                 and read.config is self.__configs[0] and write._config is self.__configs[1]
                 and self.__owners[2]._configuration_values()
                     == _private(self.__owners[2], '_FreshCompletedOrderCollectionOwner', 'config_values')
                 and read._snapshot is self.__snapshot and read._clock is self.__clock
                 and read._failure is None and read._disconnect_emitted is False and write._closed is False
                 and (_values(receipt), _values(header), _values(session),
                      _values(self.__account_association.receipt),
                      _values(self.__account_association.valid_until)) == self.__identity)
        history = _private(journal, '_PaperDispatchJournalOwner', 'history')
        # After publication legitimate appends replace this tuple. The exact old
        # entries must survive; token validation never re-consumes stale receipts.
        _require(type(history) is tuple and len(history) >= len(self.__history)
                 and all(a is b for a, b in zip(history, self.__history)))
        if before_publication:
            _require(history is self.__history)
        for owner, consume, (module, kind, _, attribute) in zip(self.__owners, self.__consume, _SOURCES):
            _require(type(owner) is getattr(module, kind) and getattr(read, attribute) is owner
                     and all(a is b for a, b in zip(_private(owner, kind, 'association'), self.__association))
                     and len(_private(owner, kind, 'association')) == 10
                     and consume.__self__ is owner and consume.__func__ is type(owner)._consume_receipt)

    def _source_checks(self, *, consumed):
        self._association_check(before_publication=True)
        values, _ = _validate_receipts(self.__receipts)
        for owner, receipt, checked, (_, kind, _, _) in zip(self.__owners, self.__receipts, values, _SOURCES):
            _require(owner._receipt is receipt and checked == _private(owner, kind, 'receipt_values')
                     and _private(owner, kind, 'consumed') is consumed and owner._failed is False)
            if hasattr(type(owner), '_capture_check'):
                owner._capture_check()
            capture = _private(owner, kind, 'capture')
            _require(capture.sealed is True and capture.outcome is None
                     and capture.attempted is True and capture.returned is True and capture.end_observed is True
                     and owner._boundary() == _private(owner, kind, 'baseline'))

    def _authenticate_sources(self):
        # Capability/SDK state checks precede lower capture locks, as in PRE3.
        self._association_check(before_publication=True)
        self.__owners[2]._support_check()
        _, c, control = self.__association[:3]
        with c._lock, control._lock:
            for owner in self.__owners:
                owner._authenticate()

    def _prevalidate(self):
        # Every receipt is structurally checked before any live consume mutation.
        self._association_check(before_publication=True)
        _validate_receipts(self.__receipts)
        self._authenticate_sources()
        _, c, control, read, write = self.__association[:5]
        with c._lock, control._lock, read._lifecycle, read._condition, write._lock, write._arrival_lock:
            self._source_checks(consumed=False)

    def _consume_source(self, index):
        _require(self.__consume[index](self.__receipts[index]) is self.__receipts[index])

    def _publish(self, token):
        # Private fault-injection seam; no issuer state is published here.
        return _token_values(token)

    def _prepare(self):
        try:
            _require(current_thread() is self.__association[-1]
                     and not self.__started and not self.__failed)
            self._set('started', True)
            self._prevalidate()
            for index in range(3):
                self._consume_source(index)  # PRE1 -> PRE2 -> PRE3; never roll back.
            self._authenticate_sources()
            _, c, control, read, write = self.__association[:5]
            with c._lock, control._lock, read._lifecycle, read._condition, write._lock, write._arrival_lock:
                self._source_checks(consumed=True)
                token = _PreparedFreshCollectionEvidence(*self.__receipts,
                    *(getattr(self.__receipts[0], name) for name in _SHARED))
                values = self._publish(token)
                self._source_checks(consumed=True)
                _require(_token_values(token) == values
                         and all(getattr(token, name) is receipt for name, receipt in zip(
                             ('open_order_receipt', 'execution_receipt', 'completed_order_receipt'), self.__receipts)))
                self._set('prepared_values', values)
                self._set('prepared_parts', (token.account_binding, token.authentication_deadline))
                self._set('prepared', token)  # Nothing fallible after final publication.
                return token
        except BaseException as error:
            # No fallible cleanup/notification hook may replace an interruption.
            for name, value in (('failed', True), ('prepared', None),
                                ('prepared_values', None), ('prepared_parts', None)):
                object.__setattr__(self, '_FreshCollectionPreparationOwner__' + name, value)
            if not isinstance(error, Exception):
                raise
            raise ReadOnlyError(_ERROR) from None

    def _require_prepared(self, token):
        """Exact issuer validation, not a second source consume or decision binding."""
        try:
            self._association_check()
            _require(type(token) is _PreparedFreshCollectionEvidence and token is self.__prepared
                     and _token_values(token) == self.__prepared_values
                     and token.account_binding is self.__prepared_parts[0]
                     and token.authentication_deadline is self.__prepared_parts[1])
            for owner, receipt, name, (_, kind, _, _) in zip(self.__owners, self.__receipts,
                    ('open_order_receipt', 'execution_receipt', 'completed_order_receipt'), _SOURCES):
                _require(getattr(token, name) is receipt and owner._receipt is receipt
                         and _private(owner, kind, 'consumed') is True and owner._failed is False)
            return token
        except Exception:
            raise ReadOnlyError(_ERROR) from None


def _create_fresh_collection_preparation_owner(journal):
    """Retrieve exact registered sources; no caller owners, receipts or requests."""
    try:
        _require(type(journal) is _PaperDispatchJournalOwner)
        journal._thread_check()
        components = _private(journal, '_PaperDispatchJournalOwner', 'components')
        c, control, read, write, receipt, header, store, session, thread = components
        _require(type(read) is ReadOnlyTWSTransport)
        with c._lock, control._lock, read._lifecycle, read._condition, write._lock, write._arrival_lock:
            journal._local_check(c, healthy=False)
            _require(getattr(read, '_fresh_collection_preparation_owner', None) is None)
            owners = tuple(getattr(read, spec[3]) for spec in _SOURCES)
            _require(all(type(owner) is getattr(module, kind) for owner, (module, kind, _, _) in zip(owners, _SOURCES)))
            owner = object.__new__(_FreshCollectionPreparationOwner)
            association = read._journal_account_association
            for name, value in (('association', (journal,) + components), ('owners', owners),
                    ('receipts', tuple(item._receipt for item in owners)),
                    ('consume', tuple(type(item)._consume_receipt.__get__(item) for item in owners)),
                    ('account_association', association), ('clients', (read._client, write._client)),
                    ('configs', (read.config, write._config)), ('snapshot', read._snapshot), ('clock', read._clock),
                    ('history', _private(journal, '_PaperDispatchJournalOwner', 'history')),
                    ('identity', (_values(receipt), _values(header), _values(session),
                                  _values(association.receipt), _values(association.valid_until))),
                    ('started', False), ('failed', False), ('prepared', None),
                    ('prepared_values', None), ('prepared_parts', None)):
                owner._set(name, value)
            # Lazy private lifetime registration leaves callback/transport code unchanged.
            read._fresh_collection_preparation_owner = owner
        try:
            owner._prevalidate()
        except BaseException:
            object.__setattr__(owner, '_FreshCollectionPreparationOwner__failed', True)
            raise
        return owner
    except Exception:
        raise ReadOnlyError(_ERROR) from None
