"""Pure advisory recovery requirements. Plans are values, never apply capabilities."""

from dataclasses import dataclass, field
from decimal import Context, Decimal, localcontext
from enum import StrEnum

from .paper_dispatch import _BrokerReconciliationBundle
from .paper_reconciliation import (_AssessmentState, _QuantityCertainty,
    _ControlConsistency, _evaluate_broker_reconciliation, _strong, _related,
    _conflicting)
from .ibkr_readonly import _ReconciliationOrderEvidence
from .models import AccountSnapshot, Commission, Position
from .readonly_models import AccountMode, ReadOnlyError


class _RecoveryDomain(StrEnum):
    EVIDENCE = 'EVIDENCE'
    CONTROL = 'CONTROL'
    PORTFOLIO = 'PORTFOLIO'
    ORDER_MANAGEMENT = 'ORDER_MANAGEMENT'
    WRITE_SESSION = 'WRITE_SESSION'
    AUTHORIZATION = 'AUTHORIZATION'


class _DomainStatus(StrEnum):
    NOT_REQUIRED = 'NOT_REQUIRED'
    READY = 'READY'
    BLOCKED = 'BLOCKED'
    REQUIRES_MORE_EVIDENCE = 'REQUIRES_MORE_EVIDENCE'
    REQUIRES_INFRASTRUCTURE = 'REQUIRES_INFRASTRUCTURE'
    MANUAL_INTERVENTION = 'MANUAL_INTERVENTION'


class _RecoveryReason(StrEnum):
    ASSESSMENT_INCOMPLETE = 'ASSESSMENT_INCOMPLETE'
    ASSESSMENT_UNRESOLVED = 'ASSESSMENT_UNRESOLVED'
    ASSESSMENT_CONFLICT = 'ASSESSMENT_CONFLICT'
    FRESH_EVIDENCE_REQUIRED = 'FRESH_EVIDENCE_REQUIRED'
    OPERATOR_INVESTIGATION_REQUIRED = 'OPERATOR_INVESTIGATION_REQUIRED'
    QUANTITY_LOWER_BOUND = 'QUANTITY_LOWER_BOUND'
    ZERO_FILL_NOT_PROVEN = 'ZERO_FILL_NOT_PROVEN'
    EXECUTION_HISTORY_LIMITED = 'EXECUTION_HISTORY_LIMITED'
    EXECUTION_PRICES_INCOMPLETE = 'EXECUTION_PRICES_INCOMPLETE'
    COMMISSIONS_INCOMPLETE = 'COMMISSIONS_INCOMPLETE'
    POSITION_CONTRIBUTION_AMBIGUOUS = 'POSITION_CONTRIBUTION_AMBIGUOUS'
    POSITION_CORROBORATION_MISMATCH = 'POSITION_CORROBORATION_MISMATCH'
    PORTFOLIO_RUNTIME_UNAVAILABLE = 'PORTFOLIO_RUNTIME_UNAVAILABLE'
    EXACT_REMAINING_QUANTITY_REQUIRED = 'EXACT_REMAINING_QUANTITY_REQUIRED'
    WORKING_ORDER_MANAGEMENT_UNAVAILABLE = 'WORKING_ORDER_MANAGEMENT_UNAVAILABLE'
    PROTECTIVE_ORDER_STATE_UNAVAILABLE = 'PROTECTIVE_ORDER_STATE_UNAVAILABLE'
    STRATEGY_REPLAY_REQUIRED = 'STRATEGY_REPLAY_REQUIRED'
    WRITE_TRANSPORT_PERMANENTLY_LATCHED = 'WRITE_TRANSPORT_PERMANENTLY_LATCHED'
    AUTHORITATIVE_SESSION_MIGRATION_REQUIRED = 'AUTHORITATIVE_SESSION_MIGRATION_REQUIRED'
    WRITE_SESSION_NOT_CURRENT = 'WRITE_SESSION_NOT_CURRENT'
    ALLOCATOR_AND_SOCKET_SAFETY_NOT_CAPTURED = 'ALLOCATOR_AND_SOCKET_SAFETY_NOT_CAPTURED'
    BROKER_STATE_BARRIER_NO_CLEAR_API = 'BROKER_STATE_BARRIER_NO_CLEAR_API'
    COORDINATOR_BARRIER_NO_CLEAR_API = 'COORDINATOR_BARRIER_NO_CLEAR_API'
    CONTROL_BARRIER_NO_CLEAR_API = 'CONTROL_BARRIER_NO_CLEAR_API'
    PENDING_CONFIRMATION_UNRESOLVED = 'PENDING_CONFIRMATION_UNRESOLVED'
    PENDING_RETIREMENT_NO_API = 'PENDING_RETIREMENT_NO_API'
    LOCAL_RECOVERY_APPLIER_UNAVAILABLE = 'LOCAL_RECOVERY_APPLIER_UNAVAILABLE'
    FRESH_AUTHORIZATION_REQUIRED = 'FRESH_AUTHORIZATION_REQUIRED'
    RESTART_RECOVERY_UNSUPPORTED = 'RESTART_RECOVERY_UNSUPPORTED'
    ACCOUNT_MODE_UNKNOWN = 'ACCOUNT_MODE_UNKNOWN'
    LIVE_UNSUPPORTED = 'LIVE_UNSUPPORTED'
    ADVISORY_ONLY = 'ADVISORY_ONLY'
    NEW_ENTRY_PROHIBITED = 'NEW_ENTRY_PROHIBITED'


class _PositionCorroboration(StrEnum):
    NOT_ASSESSED = 'NOT_ASSESSED'
    AMBIGUOUS = 'AMBIGUOUS'
    QUANTITY_AGREES = 'QUANTITY_AGREES'
    INCONSISTENT = 'INCONSISTENT'


class _RecoveryRequirement(StrEnum):
    MORE_EVIDENCE = 'MORE_EVIDENCE'
    MANUAL_REVIEW = 'MANUAL_REVIEW'
    CONTROL_BOOKKEEPING = 'CONTROL_BOOKKEEPING'
    PORTFOLIO_RECONCILIATION = 'PORTFOLIO_RECONCILIATION'
    ORDER_MANAGEMENT_FOUNDATION = 'ORDER_MANAGEMENT_FOUNDATION'
    SESSION_RETIREMENT_INFRASTRUCTURE = 'SESSION_RETIREMENT_INFRASTRUCTURE'
    EXPLICIT_REAUTHORIZATION = 'EXPLICIT_REAUTHORIZATION'


class _PrivateRecoveryValue:
    __slots__ = ()

    def __repr__(self):
        return '<PrivatePaperRecoveryValue>'


@dataclass(frozen=True, slots=True, repr=False)
class _RecoveryDomainRequirement(_PrivateRecoveryValue):
    domain: _RecoveryDomain
    status: _DomainStatus
    reasons: tuple[_RecoveryReason, ...]


@dataclass(frozen=True, slots=True, repr=False)
class _RecoveryBarrierSummary(_PrivateRecoveryValue):
    pending_confirmation: bool
    broker_state: bool
    coordinator: bool
    control: bool
    write_transport: bool
    write_closed: bool
    write_generation_changed: bool


@dataclass(frozen=True, slots=True, repr=False)
class _PaperRecoveryPlan(_PrivateRecoveryValue):
    assessment_state: _AssessmentState
    quantity_certainty: _QuantityCertainty
    executed_quantity: Decimal
    lifecycle_status: str | None
    strong_attribution: bool
    control_consistency: _ControlConsistency
    domains: tuple[_RecoveryDomainRequirement, ...]
    barriers: _RecoveryBarrierSummary
    working_order_observed: bool
    working_order_management_required: bool
    order_quantity_proven: bool
    complete_execution_prices: bool
    priced_execution_count: int
    priced_execution_quantity: Decimal
    matching_commission_reports: int
    position_corroboration: _PositionCorroboration
    transport_retirement_required: bool
    requirements: tuple[_RecoveryRequirement, ...]
    reasons: tuple[_RecoveryReason, ...]
    read_generation: int
    read_start_marker: int
    read_completion_marker: int
    # These describe this phase, not caller-selectable authority or future promises.
    exact_position_quantity_recoverable: bool = field(default=False, init=False)
    exact_cost_basis_recoverable: bool = field(default=False, init=False)
    complete_commission_evidence: bool = field(default=False, init=False)
    strategy_management_state_recoverable: bool = field(default=False, init=False)
    write_session_reusable: bool = field(default=False, init=False)
    explicit_reauthorization_required: bool = field(default=True, init=False)
    new_entry_permitted: bool = field(default=False, init=False)
    account_mode: AccountMode = field(default=AccountMode.UNKNOWN, init=False)
    live_supported: bool = field(default=False, init=False)
    restart_recovery_supported: bool = field(default=False, init=False)

    def __repr__(self):
        return '<PrivatePaperRecoveryPlan advisory-only>'


def _codes(values):
    """Canonical enum order makes diagnostics stable, unique and immutable."""
    return tuple(code for code in _RecoveryReason if code in values)


def _priced_executions(bundle):
    """Reuse evaluator attribution; diagnostic gross prices only, never fill booking.

    Called only after a positively classified assessment. The evaluator has already
    checked duplicate/cross-source conflicts and local tuple identity. Exact priced
    shares summing to Q positively cover that order quantity despite limited history;
    cumulative quantity and aggregate filled totals cannot fill a price-history gap.
    """
    local = bundle.final_local
    pending = local.pending
    snapshot = bundle.read_receipt.private_snapshot
    anchors = tuple(e for e in snapshot.orders + snapshot.executions if _strong(e, pending))
    perm_ids = {e.perm_id for e in anchors if e.perm_id is not None}
    fallback_anchor = local.strong_open_order_identity_established or any(
        type(e) is _ReconciliationOrderEvidence for e in anchors)
    executions = {key: (values[7], values[8]) for key, values in local.execution_observations}
    for event in snapshot.executions:
        if event.exec_id not in executions and not _related(event, pending, perm_ids):
            continue
        if _conflicting(event, pending):
            continue
        fallback = (event.order_ref is None and fallback_anchor
            and event.presence.order_id and event.order_id == pending.order_id
            and event.presence.client_id and event.client_id == pending.client_id)
        if _strong(event, pending) or fallback:
            executions[event.exec_id] = (event.shares, event.price)
    return executions


def _public_diagnostics(bundle, assessment, executions):
    public = bundle.read_receipt.public_snapshot
    if type(public.account) is not AccountSnapshot:
        raise ValueError()
    public.account.__post_init__()
    if public.account.mode is not None:
        raise ValueError()
    pending = bundle.final_local.pending
    positions = []
    for position in public.account.positions:
        if type(position) is not Position:
            raise ValueError()
        position.__post_init__()
        if (position.security_id == pending.security_id
                or position.symbol == pending.symbol and position.currency == pending.currency):
            positions.append(position)
    position_state = _PositionCorroboration.AMBIGUOUS
    if assessment.executed_quantity > 0:
        if len(positions) != 1 or any(p.symbol != pending.symbol or p.currency != pending.currency
                for p in positions):
            position_state = _PositionCorroboration.INCONSISTENT
        elif (assessment.quantity_certainty is _QuantityCertainty.EXACT
                and positions[0].quantity == assessment.executed_quantity):
            position_state = _PositionCorroboration.QUANTITY_AGREES
        elif positions[0].quantity < assessment.executed_quantity:
            position_state = _PositionCorroboration.INCONSISTENT
        elif assessment.quantity_certainty is _QuantityCertainty.EXACT:
            position_state = _PositionCorroboration.INCONSISTENT
    reports = set()
    for report in public.commissions:
        if type(report) is not Commission:
            raise ValueError()
        report.__post_init__()
        if report.fill_id in executions:
            reports.add(report.fill_id)
    return position_state, len(reports)


def _build_plan(bundle, assessment):
    S, R, D, T = _AssessmentState, _RecoveryReason, _RecoveryDomain, _DomainStatus
    local = bundle.final_local
    state = assessment.state
    classified = state not in (S.INCOMPLETE, S.UNRESOLVED, S.CONFLICT)
    working = state in (S.OPEN_WORKING, S.PARTIALLY_FILLED_WORKING)
    exposure = state in (S.PARTIALLY_FILLED_WORKING, S.PARTIALLY_FILLED_TERMINAL, S.FILLED)
    executions = _priced_executions(bundle) if classified else {}
    with localcontext(Context(prec=512)):
        priced_quantity = sum((value[0] for value in executions.values()), Decimal(0))
    complete_prices = (state is S.FILLED and assessment.strong_attribution
        and assessment.quantity_certainty is _QuantityCertainty.EXACT
        and priced_quantity == local.pending.quantity
        and len(executions) == assessment.attributed_execution_count)
    position, commissions = (_public_diagnostics(bundle, assessment, executions) if classified
        else (_PositionCorroboration.NOT_ASSESSED, 0))
    barriers = _RecoveryBarrierSummary(local.pending_confirmation,
        local.broker_state_reconciliation_required, local.coordinator_reconciliation_required,
        local.control_reconciliation_required, local.write_reconciliation_required,
        local.write_closed, local.write_generation != local.pending.generation)
    retirement = (local.write_reconciliation_required
        or bundle.attempt.initial_local.write_reconciliation_required)
    control_reasons = {R.LOCAL_RECOVERY_APPLIER_UNAVAILABLE, R.PENDING_RETIREMENT_NO_API}
    for flag, reason in ((barriers.pending_confirmation, R.PENDING_CONFIRMATION_UNRESOLVED),
            (barriers.broker_state, R.BROKER_STATE_BARRIER_NO_CLEAR_API),
            (barriers.coordinator, R.COORDINATOR_BARRIER_NO_CLEAR_API),
            (barriers.control, R.CONTROL_BARRIER_NO_CLEAR_API)):
        if flag:
            control_reasons.add(reason)
    portfolio_reasons = {R.EXECUTION_HISTORY_LIMITED, R.COMMISSIONS_INCOMPLETE,
        R.POSITION_CONTRIBUTION_AMBIGUOUS, R.PORTFOLIO_RUNTIME_UNAVAILABLE}
    if not complete_prices:
        portfolio_reasons.add(R.EXECUTION_PRICES_INCOMPLETE)
    if assessment.quantity_certainty is _QuantityCertainty.LOWER_BOUND:
        portfolio_reasons.add(R.QUANTITY_LOWER_BOUND)
    if state is S.OPEN_WORKING:
        portfolio_reasons.add(R.ZERO_FILL_NOT_PROVEN)
    if position is _PositionCorroboration.INCONSISTENT:
        portfolio_reasons.add(R.POSITION_CORROBORATION_MISMATCH)
    management_reasons = set()
    if working:
        management_reasons.update((R.WORKING_ORDER_MANAGEMENT_UNAVAILABLE,
                                   R.EXACT_REMAINING_QUANTITY_REQUIRED))
    if exposure:
        management_reasons.update((R.PROTECTIVE_ORDER_STATE_UNAVAILABLE, R.STRATEGY_REPLAY_REQUIRED))
    write_reasons = {R.ALLOCATOR_AND_SOCKET_SAFETY_NOT_CAPTURED, R.LOCAL_RECOVERY_APPLIER_UNAVAILABLE}
    if retirement:
        write_reasons.update((R.WRITE_TRANSPORT_PERMANENTLY_LATCHED,
                              R.AUTHORITATIVE_SESSION_MIGRATION_REQUIRED))
    if barriers.write_closed or barriers.write_generation_changed:
        write_reasons.add(R.WRITE_SESSION_NOT_CURRENT)
    evidence_reason = {S.INCOMPLETE: R.ASSESSMENT_INCOMPLETE,
        S.UNRESOLVED: R.ASSESSMENT_UNRESOLVED, S.CONFLICT: R.ASSESSMENT_CONFLICT}.get(state)
    evidence_reasons = set()
    if evidence_reason is not None:
        evidence_reasons.update((evidence_reason, R.OPERATOR_INVESTIGATION_REQUIRED
            if state is S.CONFLICT else R.FRESH_EVIDENCE_REQUIRED))
    evidence_status = (T.MANUAL_INTERVENTION if state is S.CONFLICT else
        T.REQUIRES_MORE_EVIDENCE if not classified else T.READY)
    blocked_status = T.MANUAL_INTERVENTION if state is S.CONFLICT else T.BLOCKED
    portfolio_status = (blocked_status if not classified else T.NOT_REQUIRED
        if state is S.TERMINAL_UNFILLED else T.REQUIRES_MORE_EVIDENCE)
    management_status = (blocked_status if not classified else T.REQUIRES_INFRASTRUCTURE
        if working or exposure else T.NOT_REQUIRED)
    if state is S.TERMINAL_UNFILLED:
        # Defensive only: current reviewed evaluator cannot produce this state.
        portfolio_reasons = set()
    domains = tuple(_RecoveryDomainRequirement(domain, status, _codes(reasons))
        for domain, status, reasons in (
            (D.EVIDENCE, evidence_status, evidence_reasons),
            (D.CONTROL, blocked_status, control_reasons | evidence_reasons),
            (D.PORTFOLIO, portfolio_status, portfolio_reasons | evidence_reasons),
            (D.ORDER_MANAGEMENT, management_status, management_reasons | evidence_reasons),
            (D.WRITE_SESSION, T.BLOCKED, write_reasons),
            (D.AUTHORIZATION, T.BLOCKED, {R.FRESH_AUTHORIZATION_REQUIRED})))
    requirements = {_RecoveryRequirement.CONTROL_BOOKKEEPING,
                    _RecoveryRequirement.EXPLICIT_REAUTHORIZATION}
    if not classified:
        requirements.add(_RecoveryRequirement.MANUAL_REVIEW if state is S.CONFLICT
                         else _RecoveryRequirement.MORE_EVIDENCE)
    if state is not S.TERMINAL_UNFILLED:
        requirements.add(_RecoveryRequirement.PORTFOLIO_RECONCILIATION)
    if working or exposure:
        requirements.add(_RecoveryRequirement.ORDER_MANAGEMENT_FOUNDATION)
    if retirement:
        requirements.add(_RecoveryRequirement.SESSION_RETIREMENT_INFRASTRUCTURE)
    reasons = {R.ADVISORY_ONLY, R.NEW_ENTRY_PROHIBITED, R.RESTART_RECOVERY_UNSUPPORTED,
               R.ACCOUNT_MODE_UNKNOWN, R.LIVE_UNSUPPORTED}
    reasons.update(reason for domain in domains for reason in domain.reasons)
    return _PaperRecoveryPlan(state, assessment.quantity_certainty, assessment.executed_quantity,
        assessment.lifecycle_status, assessment.strong_attribution, assessment.control_consistency,
        domains, barriers, working, working, state is S.FILLED, complete_prices,
        len(executions), priced_quantity, commissions, position, retirement,
        tuple(item for item in _RecoveryRequirement if item in requirements), _codes(reasons),
        assessment.read_generation, assessment.read_start_marker, assessment.read_completion_marker)


def _plan_paper_recovery(bundle):
    """Recompute reviewed assessment; return requirements without recovery authority."""
    try:
        if type(bundle) is not _BrokerReconciliationBundle:
            raise ValueError()
        assessment = _evaluate_broker_reconciliation(bundle)
        return _build_plan(bundle, assessment)
    except Exception:
        raise ReadOnlyError('INVALID_BROKER_RECONCILIATION_BUNDLE') from None
