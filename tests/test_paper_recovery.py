"""Offline advisory planning over immutable bundles from the reviewed fake handshake."""

from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal, localcontext
from enum import Enum
import inspect
import unittest
from unittest.mock import patch

from tradingbot_broker import paper_recovery as module
from tradingbot_broker import paper_reconciliation as evaluator
from tradingbot_broker import paper_dispatch as dispatch
from tradingbot_broker import ibkr_readonly as read
from tradingbot_broker.models import Commission, Position
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tradingbot_broker.paper_execution import PaperExecutionAuthorizationStatus as AS
from tests import test_paper_reconciliation as evidence

D = Decimal
S, Q = evaluator._AssessmentState, evaluator._QuantityCertainty
RD, DS, R = module._RecoveryDomain, module._DomainStatus, module._RecoveryReason
PC = module._PositionCorroboration


def scenario(state):
    if state is S.INCOMPLETE:
        value = evidence.bundle((evidence.order(),))
        return outcome(value, 'FAILED')
    if state is S.UNRESOLVED or state is S.TERMINAL_UNFILLED:
        return evidence.bundle()
    if state is S.CONFLICT:
        return evidence.bundle((evidence.order(order_ref='conflicting-reference'),))
    if state is S.OPEN_WORKING:
        return evidence.bundle((evidence.order(),))
    if state is S.PARTIALLY_FILLED_WORKING:
        return evidence.bundle((evidence.order(),), (evidence.execution(),))
    if state is S.PARTIALLY_FILLED_TERMINAL:
        return evidence.status_bundle('Cancelled', 4, 6,
            (evidence.order(source='COMPLETED_ORDER', status='Cancelled'),),
            (evidence.execution(),))
    return evidence.bundle(executions=(evidence.execution(shares=D(10)),))


def outcome(value, name):
    receipt = value.read_receipt
    return replace(value, read_receipt=replace(receipt, outcome=name,
        private_snapshot=replace(receipt.private_snapshot, outcome=name)))


def planned(state):
    value = scenario(state)
    if state is S.TERMINAL_UNFILLED:
        # Defensive future evaluator result only, never a caller argument.
        assessment = replace(evaluator._evaluate_broker_reconciliation(value),
            state=state, strong_attribution=True, quantity_certainty=Q.EXACT,
            lifecycle_status='Cancelled')
        with patch.object(module, '_evaluate_broker_reconciliation', return_value=assessment) as call:
            plan = module._plan_paper_recovery(value)
            call.assert_called_once_with(value)
            return plan
    return module._plan_paper_recovery(value)


def domain(plan, name):
    return next(item for item in plan.domains if item.domain is name)


def local_execution(value, exec_id='local', shares='4', price='1.25', **changes):
    pending = value.final_local.pending
    values = (pending.generation, pending.order_id, pending.client_id,
        pending.client_order_id, exec_id, pending.con_id, 'BOT', D(shares), D(price))
    names = ('generation', 'order_id', 'client_id', 'order_ref', 'exec_id',
             'con_id', 'side', 'shares', 'price')
    values = tuple(changes.get(name, v) for name, v in zip(names, values))
    return evidence.with_local(value, execution_observations=((exec_id, values),),
        execution_total=values[7])


def positions(value, *records):
    public = value.read_receipt.public_snapshot
    currency = records[0].currency if records else public.account.currency
    return replace(value, read_receipt=replace(value.read_receipt,
        public_snapshot=replace(public, account=replace(public.account,
            currency=currency, positions=tuple(records)))))


def target_position(value, quantity='10', **changes):
    pending = value.final_local.pending
    record = Position(pending.security_id, pending.symbol, D(quantity), D('1.25'), pending.currency)
    return replace(record, **changes)


def commissions(value, ids):
    public = value.read_receipt.public_snapshot
    records = tuple(Commission('report-' + key, key, D('0.01'), 'USD', public.broker_time) for key in ids)
    return replace(value, read_receipt=replace(value.read_receipt,
        public_snapshot=replace(public, commissions=records)))


class RecoveryPlannerTests(unittest.TestCase):
    def check(self, value, state=None):
        before = (value.attempt, value.final_local, value.read_receipt)
        result = module._plan_paper_recovery(value)
        self.assertEqual(result, module._plan_paper_recovery(value))
        self.assertEqual(before, (value.attempt, value.final_local, value.read_receipt))
        if state is not None:
            self.assertIs(result.assessment_state, state)
        self.assertFalse(result.new_entry_permitted)
        self.assertFalse(result.write_session_reusable)
        self.assertFalse(result.exact_position_quantity_recoverable)
        self.assertFalse(result.exact_cost_basis_recoverable)
        self.assertFalse(result.complete_commission_evidence)
        self.assertFalse(result.strategy_management_state_recoverable)
        self.assertTrue(result.explicit_reauthorization_required)
        return result

    def invalid(self, value):
        with self.assertRaises(ReadOnlyError) as caught:
            module._plan_paper_recovery(value)
        self.assertEqual(caught.exception.code, 'INVALID_BROKER_RECONCILIATION_BUNDLE')
        self.assertEqual(str(caught.exception), 'INVALID_BROKER_RECONCILIATION_BUNDLE')

    def test_real_consumed_fake_handshake_is_advisory(self):
        self.check(evidence.seed())

    def test_exact_single_bundle_signature(self):
        self.assertEqual(tuple(inspect.signature(module._plan_paper_recovery).parameters), ('bundle',))

    def test_evaluator_is_called_on_exact_bundle(self):
        value = scenario(S.FILLED)
        with patch.object(module, '_evaluate_broker_reconciliation',
                wraps=evaluator._evaluate_broker_reconciliation) as call:
            result = module._plan_paper_recovery(value)
        self.assertIs(result.assessment_state, S.FILLED)
        self.assertIs(call.call_args.args[0], value)
        call.assert_called_once_with(value)

    def test_plan_is_frozen_slotted_and_opaque(self):
        plan = self.check(scenario(S.FILLED))
        self.assertFalse(hasattr(plan, '__dict__'))
        self.assertEqual(repr(plan), '<PrivatePaperRecoveryPlan advisory-only>')
        with self.assertRaises(FrozenInstanceError):
            plan.executed_quantity = D(0)

    def test_nested_models_are_frozen_slotted_opaque(self):
        plan = self.check(scenario(S.FILLED))
        for value in (plan.barriers, *plan.domains):
            self.assertEqual(repr(value), '<PrivatePaperRecoveryValue>')
            self.assertFalse(hasattr(value, '__dict__'))
            name = fields(value)[0].name
            with self.assertRaises(FrozenInstanceError):
                setattr(value, name, None)

    def test_safety_flags_are_not_constructor_arguments(self):
        plan = planned(S.FILLED)
        for name in ('new_entry_permitted', 'write_session_reusable',
                'exact_position_quantity_recoverable', 'exact_cost_basis_recoverable',
                'complete_commission_evidence', 'strategy_management_state_recoverable',
                'explicit_reauthorization_required', 'account_mode', 'live_supported'):
            with self.assertRaises(ValueError):
                replace(plan, **{name: True})

    def test_model_has_no_apply_authority_surface(self):
        plan = planned(S.FILLED)
        for name in ('can_clear', 'resume_trading', 'safe_to_trade', 'authorized',
                'apply', 'execute', 'recover_now', 'capability', 'broker', 'transport', 'coordinator'):
            self.assertFalse(hasattr(plan, name))

    def test_all_domain_entries_unique_and_in_fixed_order(self):
        plan = planned(S.FILLED)
        self.assertEqual(tuple(item.domain for item in plan.domains), tuple(RD))
        for item in plan.domains:
            self.assertEqual(item.reasons, tuple(r for r in R if r in item.reasons))

    def test_no_fully_synchronized_summary(self):
        self.assertNotIn('FULLY_SYNCHRONIZED', module._RecoveryRequirement.__members__)
        for state in S:
            plan = planned(state)
            self.assertNotIn('FULLY_SYNCHRONIZED', tuple(item.value for item in plan.requirements))

    def test_plan_retains_only_immutable_values(self):
        value = scenario(S.FILLED)
        plan = self.check(value)
        def visit(item):
            if type(item) in (tuple, frozenset):
                for child in item: visit(child)
            elif type(item) in (module._PaperRecoveryPlan, module._RecoveryDomainRequirement,
                                module._RecoveryBarrierSummary):
                for f in fields(item): visit(getattr(item, f.name))
            else:
                self.assertTrue(type(item) in (str, bool, int, Decimal, type(None)) or isinstance(item, Enum))
                self.assertIsNot(item, value)
                self.assertIsNot(item, value.final_local.pending)
        visit(plan)

    def test_input_public_snapshot_identity_preserved(self):
        value = scenario(S.FILLED)
        public = value.read_receipt.public_snapshot
        account = public.account
        self.check(value)
        self.assertIs(value.read_receipt.public_snapshot, public)
        self.assertIs(public.account, account)

    def test_callback_cursor_and_receipt_are_unchanged(self):
        value = scenario(S.FILLED)
        before = (value.final_local.callback_cursor, value.final_local.unread_events,
                  value.attempt.read_request, value.read_receipt)
        self.check(value)
        self.assertEqual(before, (value.final_local.callback_cursor, value.final_local.unread_events,
            value.attempt.read_request, value.read_receipt))

    def test_freshness_markers_copied_without_generation_comparison(self):
        value = scenario(S.FILLED)
        plan = self.check(value)
        self.assertEqual((plan.read_generation, plan.read_start_marker, plan.read_completion_marker),
            (value.read_receipt.generation, value.read_receipt.start_marker, value.read_receipt.completion_marker))

    def test_complete_price_union_distinct_local_and_read(self):
        value = evidence.bundle(executions=(evidence.execution(shares=D(6)),))
        value = local_execution(value)
        plan = self.check(value, S.FILLED)
        self.assertTrue(plan.complete_execution_prices)
        self.assertEqual((plan.priced_execution_count, plan.priced_execution_quantity), (2, D(10)))

    def test_same_execid_local_read_counted_once(self):
        event = evidence.execution(shares=D(10))
        value = local_execution(evidence.bundle(executions=(event,)), exec_id=event.exec_id,
            shares='10', price=str(event.price))
        plan = self.check(value, S.FILLED)
        self.assertTrue(plan.complete_execution_prices)
        self.assertEqual(plan.priced_execution_count, 1)
        self.assertEqual(plan.priced_execution_quantity, D(10))

    def test_no_average_entry_price_synthesized(self):
        plan = self.check(scenario(S.FILLED))
        self.assertFalse(hasattr(plan, 'average_entry_price'))
        self.assertFalse(hasattr(plan, 'cost_basis'))

    def test_local_full_quantity_without_executions_has_no_prices(self):
        value = evidence.status_bundle('Filled', 10, 0)
        plan = self.check(value, S.FILLED)
        self.assertTrue(plan.order_quantity_proven)
        self.assertFalse(plan.complete_execution_prices)
        self.assertEqual(plan.priced_execution_quantity, D(0))
        self.assertIn(R.EXECUTION_PRICES_INCOMPLETE, plan.reasons)

    def test_cumulative_full_quantity_does_not_supply_missing_prices(self):
        value = evidence.bundle(executions=(evidence.execution(cumulative_quantity=D(10)),))
        plan = self.check(value, S.FILLED)
        self.assertFalse(plan.complete_execution_prices)
        self.assertEqual(plan.priced_execution_quantity, D(4))
        self.assertIn(R.EXECUTION_HISTORY_LIMITED, plan.reasons)

    def test_identical_duplicate_read_execution_deduped(self):
        event = evidence.execution(shares=D(10))
        plan = self.check(evidence.bundle(executions=(event, replace(event, sequence=3))), S.FILLED)
        self.assertTrue(plan.complete_execution_prices)
        self.assertEqual(plan.priced_execution_count, 1)

    def test_missing_reference_uses_existing_strong_anchor(self):
        value = evidence.status_bundle('Filled', 10, 0, executions=(
            evidence.execution(shares=D(10), order_ref=None),))
        plan = self.check(value, S.FILLED)
        self.assertTrue(plan.complete_execution_prices)

    def test_missing_reference_without_anchor_never_gains_price_coverage(self):
        value = evidence.bundle(executions=(evidence.execution(shares=D(10), order_ref=None),))
        self.assertFalse(self.check(value, S.UNRESOLVED).complete_execution_prices)

    def test_unrelated_execution_never_supplies_missing_target_price(self):
        value = evidence.status_bundle('Filled', 10, 0, executions=(evidence.execution(
            order_id=999, client_id=99, order_ref='another-order', perm_id=999, shares=D(10)),))
        plan = self.check(value, S.FILLED)
        self.assertFalse(plan.complete_execution_prices)
        self.assertEqual(plan.priced_execution_quantity, D(0))

    def test_low_decimal_precision_preserves_price_coverage_sum(self):
        value = evidence.bundle(executions=(evidence.execution(shares=D('9.99999999999999999999')),
            evidence.execution(sequence=3, exec_id='tiny', shares=D('0.00000000000000000001'))))
        with localcontext() as ctx:
            ctx.prec = 2
            ctx.traps[__import__('decimal').Inexact] = True
            plan = self.check(value, S.FILLED)
        self.assertTrue(plan.complete_execution_prices)
        self.assertEqual(plan.priced_execution_quantity, D(10))

    def test_concurrent_planning_is_deterministic_without_locks(self):
        from concurrent.futures import ThreadPoolExecutor
        value = scenario(S.FILLED)
        expected = self.check(value)
        with ThreadPoolExecutor(max_workers=4) as pool:
            plans = list(pool.map(module._plan_paper_recovery, (value,) * 12))
        self.assertEqual(plans, [expected] * 12)

    def test_terminal_aggregate_zero_stays_unresolved(self):
        plan = self.check(evidence.status_bundle('Cancelled', 0, 10), S.UNRESOLVED)
        self.assertFalse(plan.order_quantity_proven)
        self.assertIs(domain(plan, RD.CONTROL).status, DS.BLOCKED)

    def test_historical_partial_terminal_never_recovers_exact_position(self):
        plan = self.check(scenario(S.PARTIALLY_FILLED_TERMINAL), S.PARTIALLY_FILLED_TERMINAL)
        self.assertIs(plan.quantity_certainty, Q.LOWER_BOUND)
        self.assertIn(R.QUANTITY_LOWER_BOUND, plan.reasons)
        self.assertIn(R.STRATEGY_REPLAY_REQUIRED, plan.reasons)

    def test_lost_pending_restart_cannot_plan(self):
        self.invalid(evidence.with_local(scenario(S.FILLED), pending=None))

    def test_equal_plan_reconstruction_has_no_authority(self):
        plan = self.check(scenario(S.FILLED))
        reconstructed = replace(plan)
        self.assertEqual(plan, reconstructed)
        self.assertIsNot(plan, reconstructed)
        self.assertFalse(reconstructed.new_entry_permitted)
        self.assertFalse(hasattr(reconstructed, 'apply'))

    def test_pending_confirmation_false_is_not_recovery_readiness(self):
        value = evidence.with_local(scenario(S.FILLED), pending_confirmation=False, broker_observed=True)
        plan = self.check(value)
        self.assertFalse(plan.barriers.pending_confirmation)
        self.assertIs(domain(plan, RD.CONTROL).status, DS.BLOCKED)
        self.assertIn(R.PENDING_RETIREMENT_NO_API, plan.reasons)

    def test_socket_poison_allocator_exhaustion_not_invented(self):
        plan = self.check(scenario(S.FILLED))
        self.assertIn(R.ALLOCATOR_AND_SOCKET_SAFETY_NOT_CAPTURED, plan.reasons)
        self.assertFalse(hasattr(plan, 'socket_poisoned'))
        self.assertFalse(hasattr(plan, 'allocator_exhausted'))

    def test_private_module_not_added_to_package_exports(self):
        import tradingbot_broker
        self.assertFalse(hasattr(tradingbot_broker, '_plan_paper_recovery'))
        self.assertFalse(hasattr(tradingbot_broker, '_PaperRecoveryPlan'))

    def test_full_local_priced_executions_can_cover_quantity(self):
        value = local_execution(evidence.bundle(), shares='10')
        plan = self.check(value, S.FILLED)
        self.assertTrue(plan.complete_execution_prices)
        self.assertIn(R.COMMISSIONS_INCOMPLETE, plan.reasons)

    def test_cumulative_full_with_partial_prices_and_commission_stays_incomplete(self):
        value = evidence.bundle(executions=(evidence.execution(cumulative_quantity=D(10)),))
        plan = self.check(commissions(value, ('exec-1',)), S.FILLED)
        self.assertEqual(plan.matching_commission_reports, 1)
        self.assertFalse(plan.complete_execution_prices)

    def test_partial_priced_subset_is_not_complete_price_history(self):
        plan = self.check(scenario(S.PARTIALLY_FILLED_WORKING))
        self.assertEqual(plan.priced_execution_quantity, D(4))
        self.assertFalse(plan.complete_execution_prices)
        self.assertIn(R.EXACT_REMAINING_QUANTITY_REQUIRED, plan.reasons)

    def test_zero_price_is_valid_gross_evidence_without_recovery_authority(self):
        value = evidence.bundle(executions=(evidence.execution(shares=D(10), price=D(0)),))
        self.assertTrue(self.check(value, S.FILLED).complete_execution_prices)

    def test_reconnect_is_not_write_session_recovery(self):
        value = scenario(S.FILLED)
        value = evidence.with_local(value, write_generation=value.final_local.write_generation + 2)
        plan = self.check(value, S.FILLED)
        self.assertTrue(plan.barriers.write_generation_changed)
        self.assertIn(R.WRITE_SESSION_NOT_CURRENT, plan.reasons)
        self.assertFalse(plan.write_session_reusable)

    def test_no_latch_still_does_not_make_session_reusable(self):
        value = scenario(S.FILLED)
        initial = replace(value.attempt.initial_local, write_reconciliation_required=False)
        value = replace(value, attempt=replace(value.attempt, initial_local=initial),
            final_local=replace(value.final_local, write_reconciliation_required=False))
        plan = self.check(value, S.FILLED)
        self.assertFalse(plan.transport_retirement_required)
        self.assertFalse(plan.write_session_reusable)
        self.assertIn(R.ALLOCATOR_AND_SOCKET_SAFETY_NOT_CAPTURED, plan.reasons)

    def test_ordinary_planning_failure_is_sanitized(self):
        value = scenario(S.FILLED)
        with patch.object(module, '_public_diagnostics', side_effect=RuntimeError('raw private detail')):
            self.invalid(value)

    def test_unread_target_event_remains_unresolved_without_cursor_advance(self):
        from tradingbot_broker.ibkr_paper_transport import _OrderStatusEvidence
        value = scenario(S.FILLED)
        local = value.final_local
        pending = local.pending
        event = _OrderStatusEvidence(pending.generation, local.callback_cursor + 1,
            pending.order_id, pending.client_id, 'Filled', D(10), D(0), D(1), D(1), 0)
        value = evidence.with_local(value, unread_events=(event,),
            unread_through_sequence=event.sequence, write_callback_watermark=event.sequence)
        plan = self.check(value, S.UNRESOLVED)
        self.assertEqual(value.final_local.callback_cursor, local.callback_cursor)
        self.assertFalse(plan.complete_execution_prices)


# Named matrices exercise distinct state/domain contracts and adversarial input axes.
def add_test(name, function):
    if hasattr(RecoveryPlannerTests, name):
        raise AssertionError('duplicate test name')
    setattr(RecoveryPlannerTests, name, function)


for state in S:
    def state_test(self, state=state):
        plan = planned(state)
        self.assertIs(plan.assessment_state, state)
        self.assertFalse(plan.new_entry_permitted)
        self.assertTrue(plan.explicit_reauthorization_required)
        self.assertIn(R.ADVISORY_ONLY, plan.reasons)
        self.assertIn(R.RESTART_RECOVERY_UNSUPPORTED, plan.reasons)
        self.assertIs(plan.account_mode, AccountMode.UNKNOWN)
        self.assertFalse(plan.live_supported)
    add_test('test_state_' + state.value.lower(), state_test)
    for name in RD:
        def domain_test(self, state=state, name=name):
            item = domain(planned(state), name)
            if name in (RD.WRITE_SESSION, RD.AUTHORIZATION): expected = DS.BLOCKED
            elif name is RD.EVIDENCE:
                expected = DS.MANUAL_INTERVENTION if state is S.CONFLICT else (
                    DS.REQUIRES_MORE_EVIDENCE if state in (S.INCOMPLETE, S.UNRESOLVED) else DS.READY)
            elif state is S.CONFLICT: expected = DS.MANUAL_INTERVENTION
            elif state in (S.INCOMPLETE, S.UNRESOLVED) or name is RD.CONTROL: expected = DS.BLOCKED
            elif name is RD.PORTFOLIO:
                expected = DS.NOT_REQUIRED if state is S.TERMINAL_UNFILLED else DS.REQUIRES_MORE_EVIDENCE
            else:
                expected = DS.REQUIRES_INFRASTRUCTURE if state in (S.OPEN_WORKING,
                    S.PARTIALLY_FILLED_WORKING, S.PARTIALLY_FILLED_TERMINAL, S.FILLED) else DS.NOT_REQUIRED
            self.assertIs(item.status, expected)
            if name is RD.AUTHORIZATION: self.assertIn(R.FRESH_AUTHORIZATION_REQUIRED, item.reasons)
            if name is RD.CONTROL: self.assertIn(R.LOCAL_RECOVERY_APPLIER_UNAVAILABLE, item.reasons)
        add_test('test_domain_' + state.value.lower() + '_' + name.value.lower(), domain_test)


for state in (S.FILLED, S.PARTIALLY_FILLED_WORKING, S.PARTIALLY_FILLED_TERMINAL):
    for label, ids in (('missing', ()), ('target_report', ('exec-1',)), ('foreign_report', ('foreign',))):
        def commission_test(self, state=state, ids=ids):
            value = commissions(scenario(state), ids)
            plan = self.check(value, state)
            self.assertEqual(plan.matching_commission_reports, int('exec-1' in ids))
            self.assertIn(R.COMMISSIONS_INCOMPLETE, plan.reasons)
            self.assertFalse(plan.complete_commission_evidence)
        add_test('test_commission_' + state.value.lower() + '_' + label, commission_test)


for label, quantity, changes, expected in (
        ('matching_full', '10', {}, PC.QUANTITY_AGREES),
        ('smaller_full', '4', {}, PC.INCONSISTENT),
        ('larger_manual_trade', '15', {}, PC.INCONSISTENT),
        ('opposite_position', '-10', {}, PC.INCONSISTENT),
        ('different_currency', '10', {'currency': 'EUR'}, PC.INCONSISTENT),
        ('different_symbol', '10', {'symbol': 'OTHER'}, PC.INCONSISTENT),
        ('different_read_security_key', '10', {'security_id': 'ibkr-conid-other'}, PC.QUANTITY_AGREES)):
    def position_test(self, quantity=quantity, changes=changes, expected=expected):
        value = scenario(S.FILLED)
        value = positions(value, target_position(value, quantity, **changes))
        plan = self.check(value, S.FILLED)
        self.assertIs(plan.position_corroboration, expected)
        self.assertIn(R.POSITION_CONTRIBUTION_AMBIGUOUS, plan.reasons)
        self.assertEqual(R.POSITION_CORROBORATION_MISMATCH in plan.reasons, expected is PC.INCONSISTENT)
    add_test('test_position_' + label, position_test)


for label, state, qty, expected in (
        ('missing', S.FILLED, None, PC.INCONSISTENT),
        ('partial_equal', S.PARTIALLY_FILLED_WORKING, '4', PC.AMBIGUOUS),
        ('partial_larger', S.PARTIALLY_FILLED_TERMINAL, '10', PC.AMBIGUOUS),
        ('partial_smaller', S.PARTIALLY_FILLED_TERMINAL, '2', PC.INCONSISTENT),
        ('working_manual_position', S.OPEN_WORKING, '10', PC.AMBIGUOUS)):
    def position_extent_test(self, state=state, qty=qty, expected=expected):
        value = scenario(state)
        value = positions(value, *( () if qty is None else (target_position(value, qty),) ))
        plan = self.check(value, state)
        self.assertIs(plan.position_corroboration, expected)
        self.assertFalse(plan.exact_position_quantity_recoverable)
    add_test('test_position_extent_' + label, position_extent_test)


for name, changed in (('order_id', 999), ('client_id', 99), ('order_ref', 'foreign'),
        ('con_id', 999), ('side', 'SLD'), ('shares', D(5)), ('price', D(9)),
        ('symbol', 'OTHER'), ('currency', 'EUR'), ('sec_type', 'OPT')):
    def cross_source_test(self, name=name, changed=changed):
        event = evidence.execution(shares=D(10))
        value = local_execution(evidence.bundle(executions=(evidence.execution(shares=D(10),
            **{name: changed}) if name != 'shares' else evidence.execution(shares=changed),)),
            exec_id=event.exec_id, shares='10', price=str(event.price))
        plan = self.check(value, S.CONFLICT)
        self.assertFalse(plan.complete_execution_prices)
        self.assertIs(domain(plan, RD.CONTROL).status, DS.MANUAL_INTERVENTION)
    add_test('test_local_fresh_execid_conflict_' + name, cross_source_test)


for status in AS:
    def auth_test(self, status=status):
        value = evidence.with_local(scenario(S.FILLED), authorization_status=status)
        plan = self.check(value, S.FILLED)
        self.assertIs(value.final_local.authorization_status, status)
        self.assertIs(domain(plan, RD.AUTHORIZATION).status, DS.BLOCKED)
        self.assertIn(R.FRESH_AUTHORIZATION_REQUIRED, plan.reasons)
    add_test('test_authorization_' + status.value.lower(), auth_test)


for name, code in (('pending_confirmation', R.PENDING_CONFIRMATION_UNRESOLVED),
        ('broker_state_reconciliation_required', R.BROKER_STATE_BARRIER_NO_CLEAR_API),
        ('coordinator_reconciliation_required', R.COORDINATOR_BARRIER_NO_CLEAR_API),
        ('control_reconciliation_required', R.CONTROL_BARRIER_NO_CLEAR_API),
        ('write_reconciliation_required', R.WRITE_TRANSPORT_PERMANENTLY_LATCHED),
        ('write_closed', R.WRITE_SESSION_NOT_CURRENT)):
    def barrier_test(self, name=name, code=code):
        value = evidence.with_local(scenario(S.FILLED), **{name: True})
        plan = self.check(value, S.FILLED)
        self.assertIn(code, plan.reasons)
        if name == 'write_reconciliation_required':
            self.assertTrue(plan.transport_retirement_required)
            self.assertIn(R.AUTHORITATIVE_SESSION_MIGRATION_REQUIRED, plan.reasons)
    add_test('test_barrier_' + name, barrier_test)


for label, value_factory in (
        ('none', lambda: None), ('object', lambda: object()),
        ('assessment_instead', lambda: evaluator._evaluate_broker_reconciliation(scenario(S.FILLED))),
        ('lost_pending', lambda: evidence.with_local(scenario(S.FILLED), pending=None)),
        ('equal_pending', lambda: evidence.with_local(scenario(S.FILLED), pending=replace(evidence.seed().final_local.pending))),
        ('wrong_receipt_request', lambda: replace(scenario(S.FILLED), read_receipt=replace(
            scenario(S.FILLED).read_receipt, request_id=999))),
        ('cached_read_generation', lambda: replace(scenario(S.FILLED), read_receipt=replace(
            scenario(S.FILLED).read_receipt, generation=0))),
        ('bad_exec_presence', lambda: evidence.bundle(executions=(replace(evidence.execution(),
            presence=replace(evidence.execution().presence, order_ref=False)),)))):
    def malformed_test(self, value_factory=value_factory): self.invalid(value_factory())
    add_test('test_malformed_' + label, malformed_test)


for name in ('FAILED', 'TIMED_OUT', 'MALFORMED', 'INTERRUPTED', 'GENERATION_LOST'):
    def failure_test(self, name=name):
        event = evidence.execution(shares=D(10))
        value = local_execution(evidence.bundle(executions=(replace(event, order_id=999),)),
            exec_id=event.exec_id, shares='10', price=str(event.price))
        plan = self.check(outcome(value, name), S.INCOMPLETE)
        self.assertFalse(plan.complete_execution_prices)
        self.assertEqual(plan.matching_commission_reports, 0)
        self.assertIs(plan.position_corroboration, PC.NOT_ASSESSED)
    add_test('test_failed_receipt_precedence_' + name.lower(), failure_test)


for label, interruption in (('keyboard', KeyboardInterrupt()), ('system_exit', SystemExit(37)),
                           ('generator_exit', GeneratorExit())):
    for target in ('_evaluate_broker_reconciliation', '_public_diagnostics'):
        def interruption_test(self, interruption=interruption, target=target):
            value = scenario(S.FILLED)
            before = value.final_local
            with patch.object(module, target, side_effect=interruption):
                with self.assertRaises(type(interruption)) as caught:
                    module._plan_paper_recovery(value)
            self.assertIs(caught.exception, interruption)
            if type(interruption) is SystemExit: self.assertEqual(caught.exception.code, 37)
            self.assertIs(value.final_local, before)
            self.check(value, S.FILLED)
        add_test('test_interruption_' + label + '_' + target.lstrip('_'), interruption_test)


for target in (
        'tradingbot_broker.ibkr_readonly_broker.ReadOnlyIBKRBroker.connect',
        'tradingbot_broker.ibkr_readonly_broker.ReadOnlyIBKRBroker.disconnect',
        'tradingbot_broker.ibkr_readonly_broker.ReadOnlyIBKRBroker.refresh',
        'tradingbot_broker.ibkr_readonly_broker.ReadOnlyIBKRBroker.poll',
        'tradingbot_broker.ibkr_readonly_broker.ReadOnlyIBKRBroker.place_order',
        'tradingbot_broker.ibkr_readonly_broker.ReadOnlyIBKRBroker.cancel_order',
        'tradingbot_broker.ibkr_readonly_broker.ReadOnlyIBKRBroker.replace_order',
        'tradingbot_broker.paper_dispatch._PaperOrderDispatchCoordinator._sync_callbacks_locked',
        'tradingbot_broker.paper_dispatch._PaperOrderDispatchCoordinator._consume_broker_reconciliation_attempt',
        'tradingbot_broker.ibkr_paper_transport.PaperTWSTransport._dispatch_global_cancel',
        'tradingbot_broker.paper_execution._PaperExecutionAuthority._issue',
        'tradingbot_broker.paper_execution._PaperExecutionAuthority._revoke',
        'tradingbot_broker.ibkr_readonly.monotonic',
        'tradingbot_broker.ibkr_readonly.sdk_call',
        'tradingbot_broker.ibkr_paper_transport._make_paper_client',
        'tradingbot_backtest.position_management.PositionManager.__init__',
        'tradingbot_backtest.portfolio.PortfolioEngine._apply_management',
        'tradingbot_backtest.portfolio.PortfolioEngine._locks',
        'time.monotonic',
        'time.time',
        'secrets.token_hex',
        'builtins.open'):
    def mutation_guard_test(self, target=target):
        value = scenario(S.FILLED)
        with patch(target, side_effect=AssertionError('forbidden side effect')) as guard:
            self.check(value, S.FILLED)
            guard.assert_not_called()
    add_test('test_mutation_guard_' + target.replace('.', '_'), mutation_guard_test)


for forged_state in (S.FILLED, S.OPEN_WORKING, S.TERMINAL_UNFILLED):
    def forgery_test(self, forged_state=forged_state):
        value = scenario(S.UNRESOLVED)
        forged = replace(evaluator._evaluate_broker_reconciliation(value), state=forged_state,
            strong_attribution=True, control_consistency=evaluator._ControlConsistency.CONSISTENT,
            quantity_certainty=Q.EXACT, executed_quantity=D(10))
        with self.assertRaises(TypeError): module._plan_paper_recovery(value, forged)
        self.invalid(forged)
        self.check(value, S.UNRESOLVED)
    add_test('test_forged_assessment_' + forged_state.value.lower(), forgery_test)
