"""Pure private assessment of consumed reconciliation evidence; never recovery authority."""

from dataclasses import dataclass, fields
from decimal import Context, Decimal, localcontext
from enum import StrEnum
from math import isfinite
from types import MappingProxyType

from .paper_dispatch import (_BrokerReconciliationBundle, _BrokerReconciliationAttempt,
    _BrokerReconciliationLocalState, _PendingOrder, _CallbackState)
from .ibkr_readonly import (_ReconciliationReadRequest, _ReconciliationReadReceipt,
    _ReconciliationReadSnapshot, _ReconciliationCollectionState,
    _ReconciliationOrderEvidence, _ReconciliationExecutionEvidence, _ReconciliationPresence)
from .ibkr_paper_transport import _OpenOrderEvidence, _OrderStatusEvidence, _ExecutionEvidence
from .paper_execution import PaperExecutionAuthorizationStatus
from .readonly_models import ReadOnlyError, ReadOnlySnapshot
from .models import identifier


class _AssessmentState(StrEnum):
    INCOMPLETE = 'INCOMPLETE'
    UNRESOLVED = 'UNRESOLVED'
    CONFLICT = 'CONFLICT'
    OPEN_WORKING = 'OPEN_WORKING'
    PARTIALLY_FILLED_WORKING = 'PARTIALLY_FILLED_WORKING'
    PARTIALLY_FILLED_TERMINAL = 'PARTIALLY_FILLED_TERMINAL'
    FILLED = 'FILLED'
    TERMINAL_UNFILLED = 'TERMINAL_UNFILLED'


class _QuantityCertainty(StrEnum):
    NONE = 'NONE'
    LOWER_BOUND = 'LOWER_BOUND'
    EXACT = 'EXACT'


class _ControlConsistency(StrEnum):
    CONSISTENT = 'CONSISTENT'
    UNCERTAIN = 'UNCERTAIN'
    CONFLICT = 'CONFLICT'


@dataclass(frozen=True, slots=True, repr=False)
class _BrokerReconciliationAssessment:
    state: _AssessmentState
    client_order_id: str
    order_id: int
    strong_attribution: bool
    attributed_execution_count: int
    executed_quantity: Decimal
    quantity_certainty: _QuantityCertainty
    lifecycle_status: str | None
    read_generation: int
    read_start_marker: int
    read_completion_marker: int
    read_outcome: str
    open_source_complete: bool
    completed_coverage: str
    execution_coverage: str
    callback_watermark_changed: bool
    control_consistency: _ControlConsistency
    initial_barriers: tuple[bool, ...]
    final_barriers: tuple[bool, ...]
    initial_authorization_status: PaperExecutionAuthorizationStatus
    final_authorization_status: PaperExecutionAuthorizationStatus

    def __repr__(self):
        return '<PrivateBrokerReconciliationAssessment>'


_WORKING = frozenset(('PendingSubmit', 'PreSubmitted', 'Submitted'))
_TERMINAL = frozenset(('Filled', 'Cancelled', 'ApiCancelled', 'Inactive', 'Expired'))
_STATUSES = _WORKING | _TERMINAL
_STATUS_RANK = {'PendingSubmit': 0, 'PreSubmitted': 1, 'Submitted': 2,
                **{s: 3 for s in _TERMINAL}}
_OUTCOMES = frozenset(('COLLECTED', 'FAILED', 'MALFORMED', 'INTERRUPTED',
                       'GENERATION_LOST', 'TIMED_OUT'))
_FLAGS = ('pending_confirmation', 'broker_state_reconciliation_required',
          'coordinator_reconciliation_required', 'control_reconciliation_required',
          'write_reconciliation_required', 'write_closed')
_REQUIRED_CONTRACT = frozenset(('con_id', 'sec_type', 'exchange', 'symbol', 'currency'))
_REQUIRED_ORDER = _REQUIRED_CONTRACT | frozenset(('action', 'quantity', 'order_type', 'tif'))
_REQUIRED_EXECUTION = _REQUIRED_CONTRACT | frozenset(('exec_id', 'side', 'shares', 'price'))


def _require(condition):
    if not condition:
        raise ValueError()


def _integer(value, low=0, high=None):
    _require(type(value) is int and value >= low and (high is None or value <= high))


def _text(value, optional=False):
    if optional and value is None:
        return
    _require(type(value) is str and 0 < len(value) <= 100 and value.isascii()
             and all(32 <= ord(c) <= 126 for c in value))


def _amount(value, positive=False):
    _require(type(value) is Decimal and value.is_finite() and value >= 0
             and (not positive or value > 0))
    parts = value.as_tuple()
    # Captured primitives have 64 digits; exact bounded sums may have up to 256.
    _require(len(parts.digits) <= 256 and -64 <= parts.exponent <= 64)


def _clock(value):
    _require(type(value) in (int, float) and isfinite(value) and value >= 0)


def _barriers(local):
    return tuple(getattr(local, name) for name in _FLAGS)


def _presence(event):
    p = event.presence
    _require(type(p) is _ReconciliationPresence and type(p.supplied) is frozenset)
    _require(all(type(s) is str for s in p.supplied))
    for f in fields(p):
        if f.name != 'supplied':
            _require(type(getattr(p, f.name)) is bool)
    for name in ('order_id', 'client_id', 'perm_id', 'order_ref'):
        supplied = name in p.supplied or name == 'order_id' and 'top_order_id' in p.supplied
        _require(getattr(p, name) is supplied)
        value = getattr(event, name)
        _require(supplied or value is None)
        if name in ('order_id', 'client_id') and supplied:
            _integer(value, high=2147483647)
        elif name == 'perm_id' and value is not None:
            _integer(value, 1, 2**63 - 1)
        elif name == 'order_ref':
            _text(value, optional=True)
    _require(p.top_order_id == ('top_order_id' in p.supplied))
    _require(p.order_state == ('order_state' in p.supplied))
    _require(p.status == ('status' in p.supplied))
    if event.encoding == 'PROTOBUF':
        _require(p.nested_order_id == ('order_id' in p.supplied))
    else:
        _require(not p.top_order_id and not p.nested_order_id)
    return p


def _record(event, snapshot):
    _require(type(event) in (_ReconciliationOrderEvidence, _ReconciliationExecutionEvidence))
    _integer(event.generation, 1)
    _require(event.generation == snapshot.generation)
    _integer(event.sequence, 1, snapshot.last_sequence)
    _require(type(event.encoding) is str and event.encoding in ('LEGACY', 'PROTOBUF'))
    p = _presence(event)
    _integer(event.con_id, 1, 2**63 - 1)
    for name in ('sec_type', 'exchange', 'symbol', 'currency'):
        _text(getattr(event, name))
    if type(event) is _ReconciliationOrderEvidence:
        _require(type(event.source) is str and event.source in ('OPEN_ORDER', 'COMPLETED_ORDER'))
        _require(_REQUIRED_ORDER <= p.supplied)
        for name in ('action', 'order_type', 'tif'):
            _text(getattr(event, name))
        _amount(event.quantity, positive=True)
        _require(event.status is None or type(event.status) is str
                 and event.status in _STATUSES | {'UNKNOWN'})
        _require(p.status == (event.status is not None))
        _require(not p.status or p.order_state)
        if event.source == 'COMPLETED_ORDER':
            _require(p.order_state and p.status)
            if event.encoding == 'LEGACY':
                _require(event.order_id is None and event.client_id is None)
        if event.encoding == 'PROTOBUF':
            _require(p.order_state)
    else:
        _require(_REQUIRED_EXECUTION <= p.supplied)
        _text(event.exec_id)
        _text(event.side)
        _amount(event.shares, positive=True)
        _amount(event.price)
        for name in ('cumulative_quantity', 'execution_time'):
            value = getattr(event, name)
            _require((name in p.supplied) == (value is not None))
            if value is not None:
                _amount(value) if name == 'cumulative_quantity' else _text(value)


def _local(local, pending):
    _require(type(local) is _BrokerReconciliationLocalState and local.pending is pending)
    for name in _FLAGS + ('broker_observed', 'strong_open_order_identity_established',
                         'economic_observation'):
        _require(type(getattr(local, name)) is bool)
    _require(type(local.observed_state) is _CallbackState)
    _require(type(local.authorization_status) is PaperExecutionAuthorizationStatus)
    _require(local.last_status is None or type(local.last_status) is str
             and local.last_status in _STATUSES)
    _amount(local.last_filled)
    _amount(local.execution_total)
    if local.last_remaining is not None:
        _amount(local.last_remaining)
    for name in ('callback_cursor', 'write_callback_watermark', 'unread_through_sequence'):
        _integer(getattr(local, name))
    _integer(local.write_generation, 1)
    _require(local.callback_cursor <= local.unread_through_sequence <= local.write_callback_watermark)
    _require(type(local.execution_observations) is tuple and len(local.execution_observations) <= 128)
    seen = set()
    for pair in local.execution_observations:
        _require(type(pair) is tuple and len(pair) == 2)
        key, values = pair
        _text(key)
        _require(key not in seen and type(values) is tuple and len(values) == 9)
        seen.add(key)
        generation, order_id, client_id, ref, exec_id, con_id, side, shares, price = values
        _integer(generation, 1)
        _integer(order_id, high=2147483647)
        if client_id is not None:
            _integer(client_id, high=2147483647)
        _text(ref, optional=True)
        _text(exec_id)
        _require(key == exec_id)
        _integer(con_id, 1, 2**64 - 1)
        _text(side)
        _amount(shares, positive=True)
        _amount(price)
    _require(type(local.unread_events) is tuple and len(local.unread_events) <= 128)
    for event in local.unread_events:
        _require(type(event) in (_OpenOrderEvidence, _OrderStatusEvidence, _ExecutionEvidence))
        # Exact model plus primitives only; no callback object/code is evaluated.
        for f in fields(event):
            value = getattr(event, f.name)
            _require(type(value) in (str, int, Decimal, type(None)))
            if type(value) is str:
                _text(value)
            elif type(value) is Decimal:
                _amount(value)
        _integer(event.generation, 1)
        _integer(event.sequence, 1)
        _integer(event.order_id, high=2147483647)


def _validate(bundle):
    _require(type(bundle) is _BrokerReconciliationBundle)
    attempt, receipt, final = bundle.attempt, bundle.read_receipt, bundle.final_local
    _require(type(attempt) is _BrokerReconciliationAttempt)
    _integer(attempt.attempt_id, 1)
    _require(type(attempt.initial_local) is _BrokerReconciliationLocalState)
    pending = attempt.initial_local.pending
    _require(type(pending) is _PendingOrder)
    pending.__post_init__()
    for name in ('client_order_id', 'security_id', 'symbol', 'currency'):
        _text(getattr(pending, name))
    identifier(pending.client_order_id)
    _amount(pending.quantity, positive=True)
    _local(attempt.initial_local, pending)
    _local(final, pending)
    request = attempt.read_request
    _require(type(request) is _ReconciliationReadRequest and type(receipt) is _ReconciliationReadReceipt)
    for name in ('request_id', 'baseline_generation', 'baseline_start_marker', 'issuance_marker'):
        _integer(getattr(request, name), 1 if name in ('request_id', 'issuance_marker') else 0)
        _require(type(getattr(receipt, name)) is int and getattr(receipt, name) == getattr(request, name))
    _clock(request.issued_monotonic)
    _require(receipt.issued_monotonic == request.issued_monotonic)
    for name in ('generation', 'start_marker', 'completion_marker'):
        _integer(getattr(receipt, name), 1)
    _require(receipt.generation > request.baseline_generation
             and receipt.start_marker > request.issuance_marker > request.baseline_start_marker
             and receipt.completion_marker > receipt.start_marker)
    for value in (receipt.issued_monotonic, receipt.started_monotonic, receipt.completed_monotonic):
        _clock(value)
    _require(receipt.issued_monotonic <= receipt.started_monotonic <= receipt.completed_monotonic)
    _require(type(receipt.outcome) is str and receipt.outcome in _OUTCOMES)
    snapshot = receipt.private_snapshot
    _require(type(snapshot) is _ReconciliationReadSnapshot)
    for name in ('generation', 'start_marker', 'completion_marker', 'started_monotonic',
                 'completed_monotonic', 'outcome'):
        _require(type(getattr(snapshot, name)) is type(getattr(receipt, name))
                 and getattr(snapshot, name) == getattr(receipt, name))
    _integer(snapshot.last_sequence)
    _require(type(snapshot.collections) is tuple and len(snapshot.collections) == 3)
    collections = {}
    for source in snapshot.collections:
        _require(type(source) is _ReconciliationCollectionState
                 and type(source.source) is str and source.source in ('orders', 'completed', 'executions')
                 and source.source not in collections and type(source.requested) is bool
                 and type(source.supported) in (bool, type(None)))
        _require(type(source.state) is str and source.state in ('NOT_REQUESTED', 'REQUESTED',
                 'COMPLETED', 'UNAVAILABLE', 'FAILED', 'TIMED_OUT', 'IN_PROGRESS',
                 'ACTIVE', 'MALFORMED', 'INTERRUPTED', 'GENERATION_LOST'))
        _require(type(source.coverage) is str and source.coverage == ('CURRENT_OPEN_ORDERS' if source.source == 'orders' else 'HISTORY_LIMITED'))
        if source.request_id is not None:
            _integer(source.request_id, high=2147483647)
        collections[source.source] = source
    _require(type(snapshot.orders) is tuple and type(snapshot.executions) is tuple
             and len(snapshot.orders) + len(snapshot.executions) <= 1024)
    seen = set()
    for event in snapshot.orders + snapshot.executions:
        _record(event, snapshot)
        _require(event.sequence not in seen)
        seen.add(event.sequence)
    _require(all(type(e) is _ReconciliationOrderEvidence for e in snapshot.orders))
    _require(all(type(e) is _ReconciliationExecutionEvidence for e in snapshot.executions))
    return pending, attempt.initial_local, final, receipt, collections


def _identity(event):
    return tuple(getattr(event, f.name) for f in fields(event)
                 if f.name not in ('generation', 'sequence'))


def _related(event, pending, perm_ids):
    return (event.order_ref == pending.client_order_id
            or event.order_id == pending.order_id and event.client_id == pending.client_id
            or event.perm_id is not None and event.perm_id in perm_ids)


def _conflicting(event, pending):
    expected = dict(order_id=pending.order_id, client_id=pending.client_id,
                    order_ref=pending.client_order_id, con_id=pending.con_id,
                    sec_type='STK', symbol=pending.symbol, currency=pending.currency)
    if type(event) is _ReconciliationOrderEvidence:
        expected.update(exchange='SMART', action='BUY', quantity=pending.quantity,
                        order_type='MKT', tif='DAY')
    else:
        # Execution venue is not qualified as submission-route identity.
        expected.update(side='BOT')
    return any(getattr(event, k) is not None and getattr(event, k) != value
               for k, value in expected.items())


def _local_execution_conflicting(event, values):
    # Committed _ExecutionEvidence fields excluding sequence; generation belongs
    # to the write domain and is deliberately not compared with read generation.
    _, order_id, client_id, ref, exec_id, con_id, side, shares, price = values
    expected = dict(order_id=order_id, client_id=client_id, order_ref=ref,
                    exec_id=exec_id, con_id=con_id, side=side, shares=shares, price=price)
    return any(value is not None and getattr(event, name) is not None
               and getattr(event, name) != value for name, value in expected.items())


def _strong(event, pending):
    identity = (event.presence.order_id and event.order_id == pending.order_id
        and event.presence.client_id and event.client_id == pending.client_id
        and event.presence.order_ref and event.order_ref == pending.client_order_id)
    if not identity or _conflicting(event, pending):
        return False
    if type(event) is _ReconciliationOrderEvidence and event.source == 'COMPLETED_ORDER':
        return event.encoding == 'PROTOBUF' and event.status in _TERMINAL
    return True


def _complete(receipt, collections):
    if receipt.outcome != 'COLLECTED' or type(receipt.public_snapshot) is not ReadOnlySnapshot:
        return False
    for name in ('orders', 'executions'):
        source = collections[name]
        if source.supported is not True or not source.requested or source.state != 'COMPLETED':
            return False
    if collections['executions'].request_id is None:
        return False
    completed = collections['completed']
    return (completed.supported is False and not completed.requested and completed.state == 'UNAVAILABLE'
        or completed.supported is True and completed.requested and completed.state == 'COMPLETED')


def _unread(local, pending):
    """Inspect, never acknowledge, every captured event; incomplete page stays unresolved."""
    unresolved = local.unread_through_sequence != local.write_callback_watermark
    conflict = False
    expected = local.callback_cursor + 1
    for event in local.unread_events:
        if event.sequence != expected:
            unresolved = True
        expected = event.sequence + 1
        if event.generation != pending.generation or event.sequence <= pending.commit_sequence:
            continue
        ref = getattr(event, 'order_ref', None)
        related = ref == pending.client_order_id or event.order_id == pending.order_id
        if not related:
            continue
        unresolved = True  # Target lifecycle is not incorporated by this first evaluator.
        conflict |= (event.order_id != pending.order_id or event.client_id is not None
                     and event.client_id != pending.client_id)
        if type(event) is _OpenOrderEvidence:
            conflict |= (ref is not None and ref != pending.client_order_id
                or event.con_id != pending.con_id or event.security_type != 'STK'
                or event.exchange not in (None, 'SMART')
                or event.symbol not in (None, pending.symbol)
                or event.currency not in (None, pending.currency)
                or event.side != 'BUY' or event.quantity != pending.quantity
                or event.order_type != 'MKT' or event.tif != 'DAY')
        elif type(event) is _ExecutionEvidence:
            conflict |= (ref is not None and ref != pending.client_order_id
                or event.con_id != pending.con_id or event.side != 'BOT'
                or event.shares <= 0 or event.shares > pending.quantity)
        else:
            conflict |= (event.filled + event.remaining != pending.quantity
                         or event.filled > pending.quantity or event.remaining > pending.quantity)
    unresolved |= expected - 1 != local.unread_through_sequence
    return unresolved, conflict


def _assess(bundle, values):
    pending, initial, final, receipt, collections = values
    snapshot = receipt.private_snapshot
    changed = initial.write_callback_watermark != final.write_callback_watermark
    barriers = (_barriers(initial), _barriers(final))
    uncertain = any((initial.coordinator_reconciliation_required, initial.control_reconciliation_required,
        initial.write_reconciliation_required, final.coordinator_reconciliation_required,
        final.control_reconciliation_required, final.write_reconciliation_required,
        final.write_closed, final.write_generation != pending.generation,
        final.pending_confirmation, not final.broker_observed))
    strong, count, quantity, certainty, lifecycle = False, 0, Decimal(0), _QuantityCertainty.NONE, None
    control = _ControlConsistency.UNCERTAIN

    def result(state):
        return _BrokerReconciliationAssessment(state, pending.client_order_id, pending.order_id,
            strong, count, quantity, certainty, lifecycle, receipt.generation, receipt.start_marker,
            receipt.completion_marker, receipt.outcome, collections['orders'].state == 'COMPLETED',
            collections['completed'].coverage, collections['executions'].coverage, changed,
            _ControlConsistency.CONFLICT if state is _AssessmentState.CONFLICT else control,
            *barriers, initial.authorization_status, final.authorization_status)

    if not _complete(receipt, collections):
        return result(_AssessmentState.INCOMPLETE)
    events = snapshot.orders + snapshot.executions
    anchors = tuple(e for e in events if _strong(e, pending))
    perm_ids = {e.perm_id for e in anchors if e.perm_id is not None}
    conflict = len(perm_ids) > 1
    local_anchor = final.strong_open_order_identity_established
    strong = bool(anchors) or local_anchor
    fallback_anchor = local_anchor or any(type(e) is _ReconciliationOrderEvidence for e in anchors)
    statuses, working = set(), False
    executions = {}
    insufficient = False
    known_local_executions = MappingProxyType(dict(final.execution_observations))
    for event in events:
        local_execution = (known_local_executions.get(event.exec_id)
            if type(event) is _ReconciliationExecutionEvidence else None)
        # A processed target execId itself establishes a relationship. Compare
        # before filtering, even if every fresh order/client/reference is foreign.
        if local_execution is not None and _local_execution_conflicting(event, local_execution):
            conflict = True
            continue
        if local_execution is None and not _related(event, pending, perm_ids):
            continue
        if _conflicting(event, pending):
            conflict = True
            continue
        independent = _strong(event, pending)
        joined = event.perm_id is not None and event.perm_id in perm_ids
        if type(event) is _ReconciliationOrderEvidence:
            if not independent and not joined:
                insufficient = True
                continue
            if event.status in _STATUSES and event.presence.status:
                statuses.add(event.status)
                working |= event.source == 'OPEN_ORDER' and independent and event.status in _WORKING
            else:
                insufficient = True
        else:
            fallback = (event.order_ref is None and fallback_anchor and event.presence.order_id
                and event.order_id == pending.order_id and event.presence.client_id
                and event.client_id == pending.client_id)
            if not independent and not fallback:
                insufficient = True
                continue
            previous = executions.get(event.exec_id)
            if previous is not None and _identity(previous) != _identity(event):
                conflict = True
            else:
                executions[event.exec_id] = event
    # Validate duplicate execution truth even in unrelated records; never hide collisions.
    all_executions = {}
    for event in snapshot.executions:
        previous = all_executions.get(event.exec_id)
        if previous is not None and _identity(previous) != _identity(event):
            conflict = True
        all_executions[event.exec_id] = event
    shares = {key: e.shares for key, e in executions.items()}
    cumulative = Decimal(0)
    for event in executions.values():
        if event.cumulative_quantity is not None:
            conflict |= event.cumulative_quantity < event.shares or event.cumulative_quantity > pending.quantity
            cumulative = max(cumulative, event.cumulative_quantity)
    # Frozen local execution tuple layout comes from fields(_ExecutionEvidence), excluding sequence.
    local_total = Decimal(0)
    for key, v in final.execution_observations:
        gen, order_id, client_id, ref, exec_id, con_id, side, amount, price = v
        if (gen != pending.generation or order_id != pending.order_id or con_id != pending.con_id
                or side != 'BOT' or client_id not in (None, pending.client_id)
                or ref != pending.client_order_id and not (ref is None and local_anchor
                    and client_id == pending.client_id)):
            conflict = True
            continue
        strong = True
        local_total += amount
        shares[key] = amount
    conflict |= local_total != final.execution_total
    union_total = sum(shares.values(), Decimal(0))
    count = len(shares)
    quantity = max(union_total, cumulative)
    conflict |= quantity > pending.quantity
    if local_anchor:
        # last_status can come from a later openOrder than the orderStatus which
        # supplied these totals. Validate historical arithmetic, never associate
        # it with current terminal lifecycle or contradict newer execution truth.
        filled, remaining = final.last_filled, final.last_remaining
        conflict |= filled > pending.quantity
        if remaining is not None:
            conflict |= remaining > pending.quantity or filled + remaining != pending.quantity
        quantity = max(quantity, filled)
        if final.last_status in _TERMINAL:
            statuses.add(final.last_status)
    certainty = (_QuantityCertainty.EXACT if quantity == pending.quantity
        else _QuantityCertainty.LOWER_BOUND if quantity > 0 else _QuantityCertainty.NONE)
    initial_executions = dict(initial.execution_observations)
    final_executions = dict(final.execution_observations)
    conflict |= any(final_executions.get(k) != v for k, v in initial_executions.items())
    conflict |= initial.strong_open_order_identity_established and not local_anchor
    conflict |= initial.broker_observed and not final.broker_observed
    conflict |= initial.execution_total > final.execution_total
    conflict |= initial.last_filled > final.last_filled
    if initial.last_status is not None:
        conflict |= (final.last_status is None
            or initial.last_status in _TERMINAL and initial.last_status != final.last_status
            or final.last_status is not None
            and _STATUS_RANK[final.last_status] < _STATUS_RANK[initial.last_status])
    if initial.last_remaining is not None and final.last_remaining is not None:
        conflict |= initial.last_remaining < final.last_remaining
    conflict |= initial.callback_cursor > final.callback_cursor
    conflict |= initial.write_callback_watermark > final.write_callback_watermark
    conflict |= any(a and not b for a, b in zip(barriers[0][1:5], barriers[1][1:5]))
    unresolved, unread_conflict = _unread(final, pending)
    initial_unresolved, initial_conflict = _unread(initial, pending)
    # The immutable initial page remains evidence even if a later sync consumed it.
    unresolved |= initial_unresolved
    conflict |= unread_conflict or initial_conflict
    if conflict:
        return result(_AssessmentState.CONFLICT)
    control = _ControlConsistency.UNCERTAIN if uncertain or unresolved else _ControlConsistency.CONSISTENT
    terminal = statuses & _TERMINAL
    if len(terminal) > 1:
        return result(_AssessmentState.CONFLICT)
    # Capture order is not chronology. Differing lifecycle observations cannot be ordered here.
    ambiguity = len(statuses) > 1 or insufficient
    lifecycle = next(iter(statuses)) if len(statuses) == 1 else None
    if unresolved or ambiguity:
        return result(_AssessmentState.UNRESOLVED)
    if quantity == pending.quantity:
        if working or terminal and 'Filled' not in terminal:
            return result(_AssessmentState.UNRESOLVED)
        return result(_AssessmentState.FILLED)
    if terminal:
        if 'Filled' in terminal:
            return result(_AssessmentState.UNRESOLVED)
        if local_anchor and final.last_filled > 0 and 0 < quantity < pending.quantity:
            return result(_AssessmentState.PARTIALLY_FILLED_TERMINAL)
        # Aggregate zero is not terminal-zero proof. Unread same-event terminal
        # totals remain unincorporated under this evaluator's existing policy.
        return result(_AssessmentState.UNRESOLVED)
    if working:
        return result(_AssessmentState.PARTIALLY_FILLED_WORKING if quantity > 0
                      else _AssessmentState.OPEN_WORKING)
    return result(_AssessmentState.UNRESOLVED)


def _evaluate_broker_reconciliation(bundle):
    """Interpret immutable consumed provenance. No clock, I/O, locks or safety-state mutation."""
    try:
        values = _validate(bundle)
        # 1024 records of at most 64-digit primitives with exponents [-64,64],
        # plus bounded local totals, fit exactly. Ambient precision/traps are ignored.
        with localcontext(Context(prec=512)):
            return _assess(bundle, values)
    except Exception:
        raise ReadOnlyError('INVALID_BROKER_RECONCILIATION_BUNDLE') from None
