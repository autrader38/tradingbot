"""Pure assessment over a real fake/offline handshake bundle plus adversarial frozen records."""

from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from decimal import Decimal, localcontext
from enum import Enum
from unittest.mock import patch
import inspect
import unittest

from tradingbot_broker import paper_reconciliation as module
from tradingbot_broker import ibkr_readonly as read
from tradingbot_broker import paper_dispatch as dispatch
from tradingbot_broker import ibkr_paper_transport as write
from tradingbot_broker import ibkr_readonly_broker as broker_module
from tradingbot_broker.readonly_models import ReadOnlyError, AccountMode
from tradingbot_broker.paper_execution import PaperExecutionAuthorizationStatus as AS
from tests import test_paper_reconciliation_handshake as handshake

D = Decimal
S = module._AssessmentState
Q = module._QuantityCertainty
C = module._ControlConsistency
_SEED = None


def seed():
    global _SEED
    if _SEED is None:
        fixture = handshake.CoordinatorFixture()
        try:
            fixture.setUp()
            fixture.secured()
            attempt = fixture.begin()
            fixture.refresh()
            _SEED = fixture.consume(attempt)
        finally:
            fixture.doCleanups()
    return _SEED


def presence(event, **changes):
    p = event.presence
    supplied = set(p.supplied)
    for key, value in changes.items():
        if key in ('order_id', 'client_id', 'perm_id', 'order_ref', 'status'):
            if value is None:
                supplied.discard(key)
                if key == 'order_id': supplied.discard('top_order_id')
            else:
                supplied.add(key)
    return read._ReconciliationPresence(frozenset(supplied),
        'order_id' in supplied or 'top_order_id' in supplied, 'client_id' in supplied,
        'perm_id' in supplied, 'order_ref' in supplied, 'top_order_id' in supplied,
        event.encoding == 'PROTOBUF' and 'order_id' in supplied,
        'order_state' in supplied, 'status' in supplied)


def order(**changes):
    event = seed().read_receipt.private_snapshot.orders[0]
    pending = seed().final_local.pending
    event = replace(event, order_id=pending.order_id, client_id=pending.client_id,
        order_ref=pending.client_order_id, con_id=pending.con_id, quantity=pending.quantity,
        symbol=pending.symbol, currency=pending.currency, sequence=1)
    if changes.get('source') == 'COMPLETED_ORDER':
        event = replace(event, source='COMPLETED_ORDER', encoding='PROTOBUF')
    event = replace(event, **changes)
    return replace(event, presence=presence(event, **changes))


def execution(**changes):
    event = seed().read_receipt.private_snapshot.executions[0]
    pending = seed().final_local.pending
    event = replace(event, order_id=pending.order_id, client_id=pending.client_id,
        order_ref=pending.client_order_id, con_id=pending.con_id, symbol=pending.symbol,
        currency=pending.currency, sequence=2, shares=D(4), cumulative_quantity=None)
    event = replace(event, **changes)
    p = presence(event, **changes)
    supplied = set(p.supplied)
    if event.cumulative_quantity is None: supplied.discard('cumulative_quantity')
    else: supplied.add('cumulative_quantity')
    return replace(event, presence=replace(p, supplied=frozenset(supplied)))


def bundle(orders=(), executions=(), **local_changes):
    original = seed()
    local = replace(original.final_local, strong_open_order_identity_established=False,
        broker_observed=False, last_status=None, last_filled=D(0), last_remaining=None,
        execution_observations=(), execution_total=D(0), economic_observation=False,
        pending_confirmation=True, **local_changes)
    # The exact retained pending object is shared by both local captures.
    initial = local
    snapshot = original.read_receipt.private_snapshot
    snapshot = replace(snapshot, orders=tuple(orders), executions=tuple(executions),
        last_sequence=max((e.sequence for e in (*orders, *executions)), default=0))
    return replace(original, attempt=replace(original.attempt, initial_local=initial),
        final_local=local, read_receipt=replace(original.read_receipt, private_snapshot=snapshot))


def with_local(value, **changes):
    return replace(value, final_local=replace(value.final_local, **changes))


def status_bundle(status, filled, remaining, orders=(), executions=()):
    value = bundle(orders, executions)
    local = replace(value.final_local, strong_open_order_identity_established=True,
        broker_observed=True, pending_confirmation=False, last_status=status,
        last_filled=D(filled), last_remaining=D(remaining))
    return replace(value, final_local=local, attempt=replace(value.attempt, initial_local=local))


class EvaluatorTests(unittest.TestCase):
    def check(self, value, state, quantity=None, certainty=None):
        before = (value.attempt.initial_local, value.final_local, value.read_receipt)
        result = module._evaluate_broker_reconciliation(value)
        self.assertIs(result.state, state)
        if quantity is not None: self.assertEqual(result.executed_quantity, D(quantity))
        if certainty is not None: self.assertIs(result.quantity_certainty, certainty)
        self.assertEqual(before, (value.attempt.initial_local, value.final_local, value.read_receipt))
        self.assertEqual(result, module._evaluate_broker_reconciliation(value))
        return result

    def invalid(self, value):
        with self.assertRaises(ReadOnlyError) as caught:
            module._evaluate_broker_reconciliation(value)
        self.assertEqual(caught.exception.code, 'INVALID_BROKER_RECONCILIATION_BUNDLE')
        self.assertNotIn('private', str(caught.exception))

    def test_actual_consumed_bundle_evaluates_conservatively(self):
        result = module._evaluate_broker_reconciliation(seed())
        self.assertIn(result.state, (S.CONFLICT, S.UNRESOLVED))

    def test_empty_history_is_unresolved(self):
        self.check(bundle(), S.UNRESOLVED, 0, Q.NONE)

    def test_result_frozen_slotted_opaque(self):
        result = self.check(bundle((order(),)), S.OPEN_WORKING)
        self.assertEqual(repr(result), '<PrivateBrokerReconciliationAssessment>')
        self.assertFalse(hasattr(result, '__dict__'))
        with self.assertRaises(FrozenInstanceError): result.state = S.FILLED
        for name in ('success', 'can_clear', 'safe_to_trade', 'recovery_allowed', 'authorized'):
            self.assertFalse(hasattr(result, name))

    def test_snapshot_pair_markers_preserved(self):
        value = bundle((order(),)); result = self.check(value, S.OPEN_WORKING)
        receipt = value.read_receipt
        self.assertEqual((result.read_generation, result.read_start_marker, result.read_completion_marker),
            (receipt.generation, receipt.start_marker, receipt.completion_marker))
        self.assertEqual(result.completed_coverage, 'HISTORY_LIMITED')
        self.assertEqual(result.execution_coverage, 'HISTORY_LIMITED')
        self.assertTrue(result.open_source_complete)

    def test_collected_requires_public_snapshot(self):
        value = bundle((order(),))
        self.check(replace(value, read_receipt=replace(value.read_receipt, public_snapshot=None)), S.INCOMPLETE)

    def test_missing_pending_identity_cannot_restart_recover(self):
        self.invalid(with_local(bundle(), pending=None))

    def test_equal_pending_copy_is_not_retained_identity(self):
        value = bundle()
        self.invalid(with_local(value, pending=replace(value.final_local.pending)))

    def test_bundle_subclass_rejected(self):
        class Sub(dispatch._BrokerReconciliationBundle): pass
        value = bundle()
        self.invalid(Sub(value.attempt, value.read_receipt, value.final_local))

    def test_arbitrary_input_rejected_without_repr(self):
        class Raw:
            def __repr__(self): raise AssertionError('unsafe repr')
        self.invalid(Raw())

    def test_completed_unavailable_not_empty(self):
        value = bundle((order(),))
        snapshot = value.read_receipt.private_snapshot
        states = tuple(replace(s, supported=False, requested=False, state='UNAVAILABLE')
            if s.source == 'completed' else s for s in snapshot.collections)
        value = replace(value, read_receipt=replace(value.read_receipt,
            private_snapshot=replace(snapshot, collections=states)))
        self.check(value, S.OPEN_WORKING, 0, Q.NONE)

    def test_missing_exec_request_id_incomplete(self):
        value = bundle((order(),)); snap = value.read_receipt.private_snapshot
        states = tuple(replace(s, request_id=None) if s.source == 'executions' else s for s in snap.collections)
        self.check(replace(value, read_receipt=replace(value.read_receipt,
            private_snapshot=replace(snap, collections=states))), S.INCOMPLETE)

    def test_exact_duplicate_open_idempotent(self):
        e = order()
        self.check(bundle((e, replace(e, sequence=2))), S.OPEN_WORKING)

    def test_different_working_lifecycle_no_chronology(self):
        self.check(bundle((order(), order(sequence=2, status='PreSubmitted'))), S.UNRESOLVED)

    def test_unrelated_order_does_not_change_target(self):
        e = order(sequence=2, order_id=900, client_id=12, order_ref='unrelated', perm_id=99, action='SELL')
        self.check(bundle((e, order())), S.OPEN_WORKING)

    def test_missing_reference_execution_has_no_anchor(self):
        self.check(bundle(executions=(execution(order_ref=None),)), S.UNRESOLVED)

    def test_missing_reference_execution_fresh_open_anchor(self):
        self.check(bundle((order(),), (execution(order_ref=None),)), S.PARTIALLY_FILLED_WORKING, 4, Q.LOWER_BOUND)

    def test_missing_reference_execution_completed_anchor(self):
        self.check(bundle((order(source='COMPLETED_ORDER', status='Cancelled'),),
            (execution(order_ref=None),)), S.UNRESOLVED, 4, Q.LOWER_BOUND)

    def test_missing_reference_execution_retained_open_anchor(self):
        value = with_local(bundle(executions=(execution(order_ref=None),)),
            strong_open_order_identity_established=True, broker_observed=True)
        result = self.check(value, S.UNRESOLVED, 4, Q.LOWER_BOUND)
        self.assertTrue(result.strong_attribution)
        self.assertEqual(result.attributed_execution_count, 1)

    def test_broker_observed_is_not_missing_reference_anchor(self):
        result = self.check(with_local(bundle(executions=(execution(order_ref=None),)),
            broker_observed=True, observed_state=dispatch._CallbackState.EXECUTION_OBSERVED), S.UNRESOLVED)
        self.assertEqual(result.attributed_execution_count, 0)

    def test_prior_execution_is_not_missing_reference_anchor(self):
        result = self.check(bundle(executions=(execution(), execution(sequence=3,
            exec_id='second', order_ref=None))), S.UNRESOLVED)
        self.assertEqual(result.attributed_execution_count, 1)

    def test_execution_independent_full_sum(self):
        self.check(bundle(executions=(execution(shares=D(4)),
            execution(sequence=3, exec_id='second', shares=D(6)))), S.FILLED, 10, Q.EXACT)

    def test_full_cumulative_without_full_history(self):
        self.check(bundle(executions=(execution(cumulative_quantity=D(10)),)), S.FILLED, 10, Q.EXACT)

    def test_capture_order_not_execution_chronology(self):
        self.check(bundle(executions=(execution(cumulative_quantity=D(10)),
            execution(sequence=3, exec_id='earlier', shares=D(2), cumulative_quantity=D(2)))), S.FILLED, 10)

    def test_partial_execution_lower_bound_no_lifecycle(self):
        self.check(bundle(executions=(execution(),)), S.UNRESOLVED, 4, Q.LOWER_BOUND)

    def test_duplicate_execution_not_double_counted(self):
        e = execution()
        result = self.check(bundle(executions=(e, replace(e, sequence=3))), S.UNRESOLVED, 4)
        self.assertEqual(result.attributed_execution_count, 1)

    def test_overfill(self):
        self.check(bundle(executions=(execution(shares=D(7)),
            execution(sequence=3, exec_id='other', shares=D(4)))), S.CONFLICT)

    def test_execution_exchange_not_submission_route(self):
        self.check(bundle(executions=(execution(exchange='NASDAQ', shares=D(10)),)), S.FILLED)

    def test_execution_venue_disagreement_duplicate_id_conflicts(self):
        e = execution()
        self.check(bundle(executions=(e, replace(e, sequence=3, exchange='NYSE'))), S.CONFLICT)

    def test_full_execution_stale_working_order_unresolved(self):
        self.check(bundle((order(),), (execution(shares=D(10)),)), S.UNRESOLVED, 10, Q.EXACT)

    def test_full_execution_filled_lifecycle(self):
        self.check(bundle((order(source='COMPLETED_ORDER', status='Filled'),),
            (execution(shares=D(10)),)), S.FILLED, 10)

    def test_terminal_cancel_without_zero_economic_proof(self):
        self.check(bundle((order(source='COMPLETED_ORDER', status='Cancelled'),)), S.UNRESOLVED, 0, Q.NONE)

    def test_terminal_positive_zero_proof(self):
        # Aggregated fields have no same-event terminal/quantity provenance.
        self.check(status_bundle('Cancelled', 0, 10,
            (order(source='COMPLETED_ORDER', status='Cancelled'),)), S.UNRESOLVED, 0, Q.NONE)

    def test_partial_terminal_exact_local_economics(self):
        self.check(status_bundle('Cancelled', 4, 6,
            (order(source='COMPLETED_ORDER', status='Cancelled'),), (execution(),)),
            S.PARTIALLY_FILLED_TERMINAL, 4, Q.LOWER_BOUND)

    def test_partial_terminal_history_cannot_exclude_full_fill(self):
        self.check(bundle((order(source='COMPLETED_ORDER', status='Expired'),),
            (execution(),)), S.UNRESOLVED, 4, Q.LOWER_BOUND)

    def test_local_full_economics(self):
        self.check(status_bundle('Filled', 10, 0), S.FILLED, 10, Q.EXACT)

    def test_local_status_without_strong_identity_not_ownership(self):
        self.check(with_local(bundle(), last_status='Filled', last_filled=D(10), last_remaining=D(0)), S.UNRESOLVED)

    def test_local_working_fill_snapshot_remains_lower_bound(self):
        self.check(status_bundle('Submitted', 4, 6, (order(),)), S.PARTIALLY_FILLED_WORKING, 4, Q.LOWER_BOUND)

    def test_terminal_unfilled_and_positive_execution_conflict(self):
        self.check(status_bundle('Cancelled', 0, 10, executions=(execution(),)), S.UNRESOLVED, 4, Q.LOWER_BOUND)

    def test_local_execution_union_no_double_count(self):
        value = bundle(executions=(execution(),))
        p = value.final_local.pending
        identity = (p.generation, p.order_id, p.client_id, p.client_order_id,
                    'exec-1', p.con_id, 'BOT', D(4), D('1.25'))
        value = with_local(value, execution_observations=(('exec-1', identity),), execution_total=D(4))
        result = self.check(value, S.UNRESOLVED, 4)
        self.assertEqual(result.attributed_execution_count, 1)

    def test_local_read_execution_identity_union_distinct(self):
        value = bundle(executions=(execution(shares=D(6)),))
        p = value.final_local.pending
        identity = (p.generation,p.order_id,p.client_id,p.client_order_id,'local',p.con_id,'BOT',D(4),D(1))
        value = with_local(value,execution_observations=(('local',identity),), execution_total=D(4))
        self.check(value,S.FILLED,10,Q.EXACT)

    def test_local_read_same_exec_conflicting_price(self):
        value = bundle(executions=(execution(),)); p = value.final_local.pending
        identity = (p.generation,p.order_id,p.client_id,p.client_order_id,'exec-1',p.con_id,'BOT',D(4),D(9))
        self.check(with_local(value,execution_observations=(('exec-1',identity),),execution_total=D(4)), S.CONFLICT)

    def test_legacy_completed_reference_only_corroboration(self):
        e = order(source='COMPLETED_ORDER', status='Cancelled', encoding='LEGACY', order_id=None, client_id=None)
        self.check(bundle((e,)), S.UNRESOLVED)

    def test_permid_join_legacy_completed_requires_strong_anchor(self):
        e = order(source='COMPLETED_ORDER', status='Cancelled', encoding='LEGACY', order_id=None, client_id=None)
        self.check(status_bundle('Cancelled',4,6,orders=(e,),executions=(execution(),)), S.PARTIALLY_FILLED_TERMINAL)

    def test_permid_alone_never_initial_attribution(self):
        e = order(source='COMPLETED_ORDER', status='Cancelled', encoding='LEGACY',
            order_id=None, client_id=None, order_ref=None)
        self.check(bundle((e,)), S.UNRESOLVED)

    def test_permid_two_target_anchors_conflict(self):
        self.check(bundle((order(),), (execution(perm_id=88),)), S.CONFLICT)

    def test_same_target_reference_wrong_numeric_id_conflict(self):
        self.check(bundle((order(order_id=900),)), S.CONFLICT)

    def test_two_incompatible_terminals_conflict(self):
        self.check(bundle((order(source='COMPLETED_ORDER',status='Cancelled'),
            order(sequence=2,source='COMPLETED_ORDER',status='Expired'))), S.CONFLICT)

    def test_working_and_terminal_lifecycle_without_ordering(self):
        self.check(bundle((order(),order(sequence=2,source='COMPLETED_ORDER',status='Cancelled'))), S.UNRESOLVED)

    def test_open_absent_execution_present_full(self):
        self.check(bundle(executions=(execution(shares=D(10)),)), S.FILLED)

    def test_read_execution_sum_low_ambient_precision_exact(self):
        tiny = D('0.00000000000000000001')
        e1 = execution(shares=D('9.99999999999999999999'))
        e2 = execution(sequence=3,exec_id='tiny',shares=tiny)
        with localcontext() as ctx:
            ctx.prec = 2
            self.check(bundle(executions=(e1,e2)), S.FILLED, 10, Q.EXACT)

    def test_low_precision_does_not_hide_tiny_overfill(self):
        e1 = execution(shares=D(10)); e2 = execution(sequence=3,exec_id='tiny',shares=D('1e-64'))
        with localcontext() as ctx:
            ctx.prec = 2
            self.check(bundle(executions=(e1,e2)), S.CONFLICT)

    def test_general_barrier_is_control_uncertainty_not_lifecycle_conflict(self):
        value = with_local(bundle((order(),)), coordinator_reconciliation_required=True,
            control_reconciliation_required=True, write_reconciliation_required=True)
        result = self.check(value,S.OPEN_WORKING)
        self.assertIs(result.control_consistency,C.UNCERTAIN)
        self.assertTrue(all(result.final_barriers[2:5]))

    def test_authorization_refresh_invalidation_not_lifecycle_conflict(self):
        value = bundle((order(),))
        value = replace(value, attempt=replace(value.attempt, initial_local=replace(
            value.attempt.initial_local, authorization_status=AS.ARMED)))
        result = self.check(with_local(value, authorization_status=AS.INVALIDATED),S.OPEN_WORKING)
        self.assertIsNot(result.control_consistency,C.CONFLICT)
        self.assertIs(result.initial_authorization_status,AS.ARMED)
        self.assertIs(result.final_authorization_status,AS.INVALIDATED)

    def test_no_unread_change(self):
        result = self.check(bundle((order(),)),S.OPEN_WORKING)
        self.assertFalse(result.callback_watermark_changed)

    def test_unrelated_unread_callback_can_be_ignored(self):
        value = bundle((order(),)); f = value.final_local; p = f.pending
        event = write._OrderStatusEvidence(p.generation,f.callback_cursor+1,999,2,'Submitted',D(0),D(10),D(0),D(0),0)
        value = with_local(value,unread_events=(event,),write_callback_watermark=event.sequence,
            unread_through_sequence=event.sequence)
        result = self.check(value,S.OPEN_WORKING)
        self.assertTrue(result.callback_watermark_changed)

    def test_related_unread_callback_no_stale_positive(self):
        value = bundle((order(),)); f = value.final_local; p = f.pending
        event = write._OrderStatusEvidence(p.generation,f.callback_cursor+1,p.order_id,2,'Submitted',D(0),D(10),D(0),D(0),0)
        self.check(with_local(value,unread_events=(event,),write_callback_watermark=event.sequence,
            unread_through_sequence=event.sequence),S.UNRESOLVED)

    def test_unread_page_gap_unresolved(self):
        value = bundle((order(),)); f = value.final_local
        self.check(with_local(value,write_callback_watermark=f.callback_cursor+130,
            unread_through_sequence=f.callback_cursor), S.UNRESOLVED)

    def test_unread_internal_sequence_gap(self):
        value = bundle((order(),)); f=value.final_local; p=f.pending
        event=write._OrderStatusEvidence(p.generation,f.callback_cursor+2,999,2,'Submitted',D(0),D(10),D(0),D(0),0)
        self.check(with_local(value,unread_events=(event,),write_callback_watermark=event.sequence,
            unread_through_sequence=event.sequence),S.UNRESOLVED)

    def test_unread_identity_conflict_outranks_positive(self):
        value=bundle((order(),)); f=value.final_local; p=f.pending
        event=write._ExecutionEvidence(p.generation,f.callback_cursor+1,p.order_id,2,'wrong','late',17,'BOT',D(4),D(1))
        self.check(with_local(value,unread_events=(event,),write_callback_watermark=event.sequence,
            unread_through_sequence=event.sequence),S.CONFLICT)

    def test_initial_strong_provenance_cannot_disappear(self):
        value=bundle((order(),)); initial=replace(value.attempt.initial_local,strong_open_order_identity_established=True)
        self.check(replace(value,attempt=replace(value.attempt,initial_local=initial)),S.CONFLICT)

    def test_initial_barriers_cannot_downgrade(self):
        value=bundle((order(),)); initial=replace(value.attempt.initial_local,coordinator_reconciliation_required=True)
        self.check(replace(value,attempt=replace(value.attempt,initial_local=initial)),S.CONFLICT)

    def test_snapshot_account_not_used_for_order_ownership(self):
        value=bundle(); public=value.read_receipt.public_snapshot
        self.assertIsNone(public.account.mode)
        self.check(value,S.UNRESOLVED)
        self.assertFalse(any(f.name in ('account','positions','cash','equity') for f in fields(module._BrokerReconciliationAssessment)))

    def test_assessment_retains_no_raw_bundle_or_account(self):
        result=self.check(bundle((order(),)),S.OPEN_WORKING)
        for f in fields(result):
            v=getattr(result,f.name)
            self.assertTrue(type(v) in (str,int,bool,Decimal,tuple,type(None)) or isinstance(v,Enum))
            self.assertNotIn('anonymous-fixture',str(v))
        self.assertFalse(hasattr(module,'Broker'))

    def test_zero_side_effect_boundaries(self):
        value=bundle((order(),)); before=repr(value)
        names=((broker_module.ReadOnlyIBKRBroker,'refresh'),
            (read.ReadOnlyTWSTransport,'connect'),(read.ReadOnlyTWSTransport,'disconnect'),
            (read.ReadOnlyTWSTransport,'_consume_reconciliation_read_receipt'),
            (dispatch._PaperOrderDispatchCoordinator,'_sync_broker_callbacks'),
            (dispatch._PaperOrderDispatchCoordinator,'_dispatch_entry'),
            (write.PaperTWSTransport,'_dispatch'))
        from contextlib import ExitStack
        with ExitStack() as stack:
            for owner,name in names:
                stack.enter_context(patch.object(owner,name,side_effect=AssertionError('mutation forbidden')))
            stack.enter_context(patch('builtins.open',side_effect=AssertionError('I/O forbidden')))
            stack.enter_context(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))
            self.check(value,S.OPEN_WORKING)
        self.assertEqual(repr(value),before)
        self.assertEqual(value.final_local.callback_cursor,value.attempt.initial_local.callback_cursor)

    def test_evaluator_has_no_live_or_connection_surface(self):
        self.assertEqual(tuple(inspect.signature(module._evaluate_broker_reconciliation).parameters),('bundle',))
        self.assertFalse(any(hasattr(module._BrokerReconciliationAssessment,n) for n in ('connect','dispatch','recover')))
        self.assertIsNone(seed().read_receipt.public_snapshot.account.mode)
        self.assertEqual(AccountMode.UNKNOWN.value,'UNKNOWN')


# Independent field/rule matrices create discoverable tests with concrete assertions.
def open_status_case(status, expected):
    def test(self): self.check(bundle((order(status=status),)),expected,0,Q.NONE)
    return test
for status in ('PendingSubmit','PreSubmitted','Submitted',None,'UNKNOWN','Filled','Cancelled','ApiCancelled','Inactive','Expired'):
    setattr(EvaluatorTests,'test_open_status_'+str(status),open_status_case(status,S.OPEN_WORKING if status in module._WORKING else S.UNRESOLVED))


def order_conflict_case(name, value, completed=False):
    def test(self):
        changes={name:value}
        if completed: changes.update(source='COMPLETED_ORDER',status='Cancelled')
        self.check(bundle((order(**changes),)),S.CONFLICT)
    return test
for name,value in dict(order_ref='wrong',order_id=99,client_id=3,con_id=18,symbol='OTHER',currency='EUR',
    sec_type='OPT',exchange='NYSE',action='SELL',quantity=D(9),order_type='LMT',tif='GTC').items():
    for completed in (False,True):
        setattr(EvaluatorTests,f'test_related_{"completed" if completed else "open"}_conflict_{name}',order_conflict_case(name,value,completed))


def missing_identity_case(name, source):
    def test(self):
        changes={name:None}
        if source=='completed': changes.update(source='COMPLETED_ORDER',status='Cancelled')
        e=execution(**changes) if source=='execution' else order(**changes)
        value=bundle(executions=(e,)) if source=='execution' else bundle((e,))
        result=self.check(value,S.UNRESOLVED)
        self.assertFalse(result.strong_attribution)
    return test
for name in ('order_id','client_id','order_ref'):
    for source in ('open','completed','execution'):
        setattr(EvaluatorTests,f'test_missing_{source}_{name}',missing_identity_case(name,source))


def duplicate_conflict_case(name,value):
    def test(self):
        e=execution(); second=execution(sequence=3,**{name:value})
        self.check(bundle(executions=(e,second)),S.CONFLICT)
    return test
for name,value in dict(order_id=99,client_id=3,perm_id=88,order_ref='wrong',con_id=18,side='SLD',shares=D(3),
    price=D(2),cumulative_quantity=D(5),execution_time='other-time',symbol='OTHER',currency='EUR',exchange='NYSE',sec_type='OPT').items():
    setattr(EvaluatorTests,'test_execution_duplicate_conflict_'+name,duplicate_conflict_case(name,value))


def failure_case(outcome):
    def test(self):
        value=bundle((order(),)); r=value.read_receipt
        self.check(replace(value,read_receipt=replace(r,outcome=outcome,public_snapshot=None,
            private_snapshot=replace(r.private_snapshot,outcome=outcome))),S.INCOMPLETE)
    return test
for outcome in ('FAILED','TIMED_OUT','MALFORMED','INTERRUPTED','GENERATION_LOST'):
    setattr(EvaluatorTests,'test_failed_receipt_'+outcome,failure_case(outcome))


def provenance_case(field):
    def test(self):
        value=bundle((order(),)); receipt=value.read_receipt
        v=getattr(receipt,field)
        self.invalid(replace(value,read_receipt=replace(receipt,**{field:v+1})))
    return test
for field in ('request_id','baseline_generation','baseline_start_marker','issuance_marker','generation','start_marker','completion_marker'):
    setattr(EvaluatorTests,'test_malformed_provenance_'+field,provenance_case(field))


def incomplete_source_case(source,state):
    def test(self):
        value=bundle((order(),)); snap=value.read_receipt.private_snapshot
        states=tuple(replace(s,state=state) if s.source==source else s for s in snap.collections)
        self.check(replace(value,read_receipt=replace(value.read_receipt,private_snapshot=replace(snap,collections=states))),S.INCOMPLETE)
    return test
for source in ('orders','completed','executions'):
    for state in ('ACTIVE','NOT_REQUESTED','FAILED','TIMED_OUT'):
        setattr(EvaluatorTests,f'test_source_{source}_{state}',incomplete_source_case(source,state))


def terminal_partial_case(status):
    def test(self):
        e=order(source='COMPLETED_ORDER',status=status)
        self.check(status_bundle(status,4,6,(e,),(execution(),)),S.PARTIALLY_FILLED_TERMINAL,4,Q.LOWER_BOUND)
    return test
for status in ('Cancelled','ApiCancelled','Inactive','Expired'):
    setattr(EvaluatorTests,'test_partial_terminal_'+status,terminal_partial_case(status))


def cumulative_conflict_case(value):
    def test(self): self.check(bundle(executions=(execution(cumulative_quantity=D(value)),)),S.CONFLICT)
    return test
for name,value in (('overfill',11),('less_than_shares',3)):
    setattr(EvaluatorTests,'test_cumulative_'+name,cumulative_conflict_case(value))


def local_totals_conflict_case(filled,remaining):
    def test(self): self.check(status_bundle('Cancelled',filled,remaining),S.CONFLICT)
    return test
for name,filled,remaining in (('sum',4,5),('filled_over',11,0),('remaining_over',0,11)):
    setattr(EvaluatorTests,'test_local_totals_'+name,local_totals_conflict_case(filled,remaining))


def interruption_case(kind, boundary):
    def test(self):
        value=bundle((order(),)); error=kind(37)
        with patch.object(module,boundary,side_effect=error):
            with self.assertRaises(kind) as caught: module._evaluate_broker_reconciliation(value)
        self.assertIs(caught.exception,error)
        if kind is SystemExit:self.assertEqual(caught.exception.code,37)
        self.check(value,S.OPEN_WORKING)
    return test
for kind in (KeyboardInterrupt,SystemExit,GeneratorExit):
    for boundary in ('_validate','_BrokerReconciliationAssessment'):
        setattr(EvaluatorTests,f'test_{boundary}_{kind.__name__}',interruption_case(kind,boundary))


def supplied_default_case(name, value):
    def test(self):
        event=order(**{name:value})
        supplied=set(event.presence.supplied)
        supplied.discard(name)
        if name=='order_id':supplied.discard('top_order_id')
        flags={name:False}
        if name=='order_id':flags.update(top_order_id=False,nested_order_id=False)
        event=replace(event,presence=replace(event.presence,supplied=frozenset(supplied),**flags))
        self.invalid(bundle((event,)))
    return test
for name,value in (('order_id',0),('client_id',0),('order_ref','default-ref')):
    setattr(EvaluatorTests,'test_absent_presence_decoded_default_'+name,supplied_default_case(name,value))


def exact_ref_case(ref):
    def test(self):self.check(bundle((order(order_ref=ref),)),S.CONFLICT)
    return test
for name,ref in (('case','ENTRY-1'),('leading',' entry-1'),('trailing','entry-1 '),('suffix','entry-1-x')):
    # Actual fixture identifier is used rather than assuming its case/content.
    def reference_test(self, name=name):
        ref=seed().final_local.pending.client_order_id
        changed={'case':ref.upper(),'leading':' '+ref,'trailing':ref+' ','suffix':ref+'-x'}[name]
        self.assertNotEqual(changed,ref)
        self.check(bundle((order(order_ref=changed),)),S.CONFLICT)
    setattr(EvaluatorTests,'test_reference_exact_'+name,reference_test)


def perm_join_conflict(name,value):
    def test(self):
        completed=order(source='COMPLETED_ORDER',encoding='LEGACY',status='Cancelled',
            order_id=None,client_id=None,order_ref=None,sequence=3,**{name:value})
        self.check(bundle((completed,),(execution(),)),S.CONFLICT)
    return test
for name,value in (('con_id',18),('symbol','OTHER'),('currency','EUR'),('action','SELL'),('quantity',D(9))):
    setattr(EvaluatorTests,'test_permid_join_conflict_'+name,perm_join_conflict(name,value))


def status_regression_case(initial_status,final_status):
    def test(self):
        value=status_bundle(initial_status,0,10,(order(),))
        self.check(with_local(value,last_status=final_status),S.CONFLICT)
    return test
for name,a,b in (('terminal','Cancelled','Expired'),('working','Submitted','PendingSubmit'),('erased','Submitted',None)):
    setattr(EvaluatorTests,'test_local_status_regression_'+name,status_regression_case(a,b))


def remaining_regression(self):
    value=status_bundle('Submitted',4,6,(order(),))
    self.check(with_local(value,last_filled=D(3),last_remaining=D(7)),S.CONFLICT)
EvaluatorTests.test_local_remaining_regression=remaining_regression


def cumulative_partial(self):
    self.check(bundle((order(),),(execution(shares=D(2),cumulative_quantity=D(6)),)),S.PARTIALLY_FILLED_WORKING,6,Q.LOWER_BOUND)
EvaluatorTests.test_partial_cumulative_is_lower_bound=cumulative_partial


def low_precision_local_union(self):
    value=bundle(executions=(execution(shares=D('9.99999999999999999999')),))
    p=value.final_local.pending
    identity=(p.generation,p.order_id,p.client_id,p.client_order_id,'tiny',p.con_id,'BOT',D('1e-20'),D(1))
    value=with_local(value,execution_observations=(('tiny',identity),),execution_total=D('1e-20'))
    with localcontext() as ctx:
        ctx.prec=1
        self.check(value,S.FILLED,10,Q.EXACT)
EvaluatorTests.test_low_precision_local_read_union=low_precision_local_union


def boolean_id_case(name):
    def test(self):self.invalid(bundle((order(**{name:True}),)))
    return test
for name in ('order_id','client_id','perm_id','con_id'):
    setattr(EvaluatorTests,'test_boolean_identity_'+name,boolean_id_case(name))


def malformed_amount_case(value):
    def test(self):self.invalid(bundle(executions=(execution(shares=value),)))
    return test
for name,value in (('nan',D('NaN')),('infinite',D('Infinity')),('zero',D(0)),('negative',D(-1)),('float',1.0)):
    setattr(EvaluatorTests,'test_malformed_execution_quantity_'+name,malformed_amount_case(value))


def invalid_execid_case(value):
    def test(self):self.invalid(bundle(executions=(execution(exec_id=value),)))
    return test
for name,value in (('absent',None),('empty',''),('control','bad\x00id'),('long','x'*101)):
    setattr(EvaluatorTests,'test_invalid_execid_'+name,invalid_execid_case(value))


def incomplete_collections_not_empty(self):
    value=bundle(); snap=value.read_receipt.private_snapshot
    states=tuple(replace(s,requested=False,state='NOT_REQUESTED') for s in snap.collections)
    self.check(replace(value,read_receipt=replace(value.read_receipt,private_snapshot=replace(snap,collections=states))),S.INCOMPLETE)
EvaluatorTests.test_empty_tuple_without_collection_completion=incomplete_collections_not_empty


def control_callbacks_stale_generation(self):
    value=bundle((order(),)); f=value.final_local;p=f.pending
    event=write._ExecutionEvidence(p.generation+1,f.callback_cursor+1,p.order_id,2,'wrong','stale',17,'SLD',D(4),D(1))
    self.check(with_local(value,unread_events=(event,),write_callback_watermark=event.sequence,
        unread_through_sequence=event.sequence),S.OPEN_WORKING)
EvaluatorTests.test_stale_unread_generation_never_target_attribution=control_callbacks_stale_generation


def initial_unread_conflict_cannot_disappear(self):
    value=bundle((order(),)); f=value.final_local;p=f.pending
    event=write._ExecutionEvidence(p.generation,f.callback_cursor+1,p.order_id,2,'wrong','late',17,'BOT',D(4),D(1))
    initial=replace(f,unread_events=(event,),write_callback_watermark=event.sequence,unread_through_sequence=event.sequence)
    final=replace(f,callback_cursor=event.sequence,write_callback_watermark=event.sequence,unread_through_sequence=event.sequence)
    value=replace(value,attempt=replace(value.attempt,initial_local=initial),final_local=final)
    self.check(value,S.CONFLICT)
EvaluatorTests.test_initial_unread_conflict_retained=initial_unread_conflict_cannot_disappear


def reconnect_only_changes_control_uncertainty(self):
    value=with_local(bundle((order(),)),write_generation=seed().final_local.write_generation+3,
        write_reconciliation_required=True,coordinator_reconciliation_required=True)
    result=self.check(value,S.OPEN_WORKING)
    self.assertIs(result.control_consistency,C.UNCERTAIN)
EvaluatorTests.test_reconnect_not_read_write_counter_comparison=reconnect_only_changes_control_uncertainty


def no_execution_without_shares_economics(self):
    self.check(bundle((order(source='COMPLETED_ORDER',status='Filled'),)),S.UNRESOLVED)
EvaluatorTests.test_completed_filled_without_economics=no_execution_without_shares_economics


def completed_working_not_open(self):
    self.check(bundle((order(source='COMPLETED_ORDER',status='Submitted'),)),S.UNRESOLVED)
EvaluatorTests.test_completed_working_status_not_current_open=completed_working_not_open


def ordinary_constructor_sanitized(self):
    value=bundle((order(),))
    with patch.object(module,'_BrokerReconciliationAssessment',side_effect=RuntimeError('private SDK detail')):
        self.invalid(value)
EvaluatorTests.test_ordinary_constructor_error_sanitized=ordinary_constructor_sanitized


def large_capacity_snapshot(self):
    records=tuple(order(sequence=i+1,order_id=i+1000,client_id=99,order_ref='other-'+str(i),perm_id=i+1000) for i in range(1023))
    records+=(order(sequence=1024),)
    self.check(bundle(records),S.OPEN_WORKING)
    self.invalid(bundle(records+(order(sequence=1025),)))
EvaluatorTests.test_bounded_evidence_1024_then_1025=large_capacity_snapshot


def immutable_public_pair_not_mutated(self):
    value=bundle((order(),)); public=value.read_receipt.public_snapshot
    before=(public.account,public.working_orders,public.completed_orders,public.fills,public.commissions)
    self.check(value,S.OPEN_WORKING)
    self.assertEqual(before,(public.account,public.working_orders,public.completed_orders,public.fills,public.commissions))
EvaluatorTests.test_public_snapshot_pair_unchanged=immutable_public_pair_not_mutated


def no_read_clocks_or_random_calls(self):
    value=bundle((order(),))
    with patch.object(read,'monotonic',side_effect=AssertionError('clock forbidden')):
        self.check(value,S.OPEN_WORKING)
EvaluatorTests.test_no_clock_calls=no_read_clocks_or_random_calls


def reverse_watermark(self):
    value=bundle((order(),)); f=value.final_local
    initial=replace(f,write_callback_watermark=f.write_callback_watermark+1,
        unread_through_sequence=f.unread_through_sequence+1)
    self.check(replace(value,attempt=replace(value.attempt,initial_local=initial)),S.CONFLICT)
EvaluatorTests.test_write_watermark_cannot_rollback=reverse_watermark


def precommit_unread_callback_ignored(self):
    value=bundle((order(),)); f=value.final_local
    pending=replace(f.pending,commit_sequence=f.callback_cursor+5)
    initial=replace(value.attempt.initial_local,pending=pending)
    event=write._ExecutionEvidence(pending.generation,f.callback_cursor+1,pending.order_id,2,'wrong','old',17,'SLD',D(4),D(1))
    final=replace(f,pending=pending,unread_events=(event,),write_callback_watermark=event.sequence,
        unread_through_sequence=event.sequence)
    self.check(replace(value,attempt=replace(value.attempt,initial_local=initial),final_local=final),S.OPEN_WORKING)
EvaluatorTests.test_precommit_unread_cannot_attribute=precommit_unread_callback_ignored


def impossible_status_presence(self):
    event=order(status=None)
    supplied=event.presence.supplied|{'status'}
    self.invalid(bundle((replace(event,presence=replace(event.presence,status=True,supplied=supplied)),)))
EvaluatorTests.test_supplied_status_requires_retained_status=impossible_status_presence


def impossible_cumulative_presence(self):
    event=execution()
    event=replace(event,presence=replace(event.presence,supplied=event.presence.supplied|{'cumulative_quantity'}))
    self.invalid(bundle(executions=(event,)))
EvaluatorTests.test_supplied_cumulative_requires_primitive=impossible_cumulative_presence


def parallel_pure_evaluation(self):
    from concurrent.futures import ThreadPoolExecutor
    value=bundle((order(),),(execution(),))
    expected=module._evaluate_broker_reconciliation(value)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(module._evaluate_broker_reconciliation,(value,)*12))
    self.assertEqual(results,[expected]*12)
    self.assertEqual(value.final_local.callback_cursor,value.attempt.initial_local.callback_cursor)
EvaluatorTests.test_concurrent_evaluation_no_locks_or_mutation=parallel_pure_evaluation


def local_execution_bundle(fresh, orders=()):
    value = bundle(orders, fresh)
    p = value.final_local.pending
    # Exact committed fields(_ExecutionEvidence) layout, excluding sequence.
    identity = (p.generation, p.order_id, p.client_id, p.client_order_id,
                'exec-1', p.con_id, 'BOT', D(4), D('1.25'))
    local = replace(value.final_local, strong_open_order_identity_established=True,
        broker_observed=True, pending_confirmation=False, last_status='Submitted',
        execution_observations=(('exec-1', identity),), execution_total=D(4))
    return replace(value, final_local=local, attempt=replace(value.attempt, initial_local=local))


def regression_local_execid_conflict(field, changed):
    def test(self):
        # Destroy the ordinary ref/numeric/client/perm relationships as well.
        # Only the already-owned execId ties this fresh record to the target.
        changes = dict(order_id=99, client_id=3, order_ref='foreign', perm_id=88)
        changes[field] = changed
        self.check(local_execution_bundle((execution(**changes),), (order(),)), S.CONFLICT)
        # Also test the individual overlapping/target field with other keys exact.
        self.check(local_execution_bundle((execution(**{field: changed}),), (order(),)), S.CONFLICT)
    return test
for name, changed in dict(order_id=99, client_id=3, order_ref='foreign', con_id=18,
    side='SLD', shares=D(3), price=D(2), symbol='OTHER', currency='EUR', sec_type='OPT').items():
    setattr(EvaluatorTests, 'test_regression_cross_source_execid_conflict_'+name,
            regression_local_execid_conflict(name, changed))


def regression_same_execution_counted_once(self):
    result = self.check(local_execution_bundle((execution(cumulative_quantity=D(6)),), (order(),)),
                        S.PARTIALLY_FILLED_WORKING, 6, Q.LOWER_BOUND)
    self.assertEqual(result.attributed_execution_count, 1)
    # Extra cumulative evidence strengthens the lower bound without adding shares.
    result = self.check(local_execution_bundle((execution(),), (order(),)),
                        S.PARTIALLY_FILLED_WORKING, 4, Q.LOWER_BOUND)
    self.assertEqual(result.attributed_execution_count, 1)
EvaluatorTests.test_regression_cross_source_identical_dedupe = regression_same_execution_counted_once


def regression_missing_shared_field(field):
    def test(self):
        value = local_execution_bundle((execution(**{field: None}),), (order(),))
        expected = S.PARTIALLY_FILLED_WORKING if field == 'order_ref' else S.UNRESOLVED
        result = self.check(value, expected, 4, Q.LOWER_BOUND)
        self.assertEqual(result.attributed_execution_count, 1)
    return test
for name in ('order_ref', 'order_id', 'client_id'):
    setattr(EvaluatorTests, 'test_regression_same_execid_missing_'+name, regression_missing_shared_field(name))


def regression_local_missing_reference_cannot_hide_fresh_wrong_reference(self):
    value = local_execution_bundle((execution(order_id=99, client_id=3,
        order_ref='foreign', perm_id=88),), (order(),))
    key, identity = value.final_local.execution_observations[0]
    local = replace(value.final_local, execution_observations=((key, (*identity[:3], None, *identity[4:])),))
    value = replace(value, final_local=local, attempt=replace(value.attempt, initial_local=local))
    self.check(value, S.CONFLICT)
EvaluatorTests.test_regression_local_missing_ref_foreign_shared_execid = regression_local_missing_reference_cannot_hide_fresh_wrong_reference


def regression_foreign_different_execid_unrelated(self):
    fresh = execution(exec_id='foreign-exec', order_id=99, client_id=3,
                      order_ref='foreign', perm_id=88, con_id=18, symbol='OTHER')
    result = self.check(local_execution_bundle((fresh,), (order(),)), S.PARTIALLY_FILLED_WORKING, 4)
    self.assertEqual(result.attributed_execution_count, 1)
EvaluatorTests.test_regression_foreign_different_execid_unrelated = regression_foreign_different_execid_unrelated


def regression_shared_execution_venue_not_submission_route(self):
    self.check(local_execution_bundle((execution(exchange='NASDAQ'),), (order(),)),
               S.PARTIALLY_FILLED_WORKING, 4)
EvaluatorTests.test_regression_same_execid_execution_venue = regression_shared_execution_venue_not_submission_route


def regression_shared_execid_read_failure_first(self):
    value = local_execution_bundle((execution(order_id=99, client_id=3,
        order_ref='foreign', perm_id=88),), (order(),))
    r = value.read_receipt
    failed = replace(r, outcome='FAILED', public_snapshot=None,
                     private_snapshot=replace(r.private_snapshot, outcome='FAILED'))
    self.check(replace(value, read_receipt=failed), S.INCOMPLETE, 0, Q.NONE)
EvaluatorTests.test_regression_cross_source_conflict_read_failure_precedence = regression_shared_execid_read_failure_first


def regression_shared_execid_conflict_outranks_full_fill(self):
    wrong = execution(order_id=99, client_id=3, order_ref='foreign', perm_id=88)
    full = execution(sequence=3, exec_id='full', shares=D(10))
    self.check(local_execution_bundle((wrong, full)), S.CONFLICT)
EvaluatorTests.test_regression_cross_source_conflict_full_fill_precedence = regression_shared_execid_conflict_outranks_full_fill


def regression_partial_terminal_lower_bound(status):
    def test(self):
        self.check(status_bundle(status, 4, 6), S.PARTIALLY_FILLED_TERMINAL, 4, Q.LOWER_BOUND)
        self.check(status_bundle(status, 4, 6,
            orders=(order(source='COMPLETED_ORDER', status=status),),
            executions=(execution(shares=D(6)),)), S.PARTIALLY_FILLED_TERMINAL, 6, Q.LOWER_BOUND)
    return test
for status in ('Cancelled', 'ApiCancelled', 'Inactive', 'Expired'):
    setattr(EvaluatorTests, 'test_regression_terminal_partial_historical_'+status,
            regression_partial_terminal_lower_bound(status))


def regression_terminal_zero_no_same_event(self):
    self.check(status_bundle('Cancelled', 0, 10), S.UNRESOLVED, 0, Q.NONE)
EvaluatorTests.test_regression_aggregate_terminal_zero_unresolved = regression_terminal_zero_no_same_event


def regression_unread_terminal_zero(anchor):
    def test(self):
        value = bundle()
        f, p = value.final_local, value.final_local.pending
        event = write._OrderStatusEvidence(p.generation, f.callback_cursor+1, p.order_id,
            p.client_id, 'Cancelled', D(0), p.quantity, D(0), D(0), 0)
        value = with_local(value, strong_open_order_identity_established=anchor,
            broker_observed=anchor, unread_events=(event,),
            write_callback_watermark=event.sequence, unread_through_sequence=event.sequence)
        result = self.check(value, S.UNRESOLVED, 0, Q.NONE)
        self.assertIsNot(result.state, S.TERMINAL_UNFILLED)
        self.assertEqual(value.final_local.callback_cursor, f.callback_cursor)
    return test
for anchor in (False, True):
    setattr(EvaluatorTests, 'test_regression_unread_terminal_zero_anchor_'+str(anchor),
            regression_unread_terminal_zero(anchor))


def regression_full_proof_outgrows_historical_partial(self):
    self.check(status_bundle('Filled', 4, 6, executions=(execution(shares=D(10)),)),
               S.FILLED, 10, Q.EXACT)
    self.check(status_bundle('Filled', 0, 10, executions=(execution(shares=D(10)),)),
               S.FILLED, 10, Q.EXACT)
EvaluatorTests.test_regression_fresh_full_fill_outgrows_local_totals = regression_full_proof_outgrows_historical_partial


def regression_larger_working_partial_outgrows_history(self):
    self.check(status_bundle('Submitted', 4, 6, (order(),),
        (execution(shares=D(6)),)), S.PARTIALLY_FILLED_WORKING, 6, Q.LOWER_BOUND)
EvaluatorTests.test_regression_fresh_partial_outgrows_local_totals = regression_larger_working_partial_outgrows_history


def genuine_regression_fixture(self):
    fixture = handshake.CoordinatorFixture()
    self.addCleanup(fixture.doCleanups)
    fixture.setUp()
    fixture.secured()
    return fixture


def genuine_read_collection(fixture, *, full=False, foreign_exec=False):
    # Fake SDK read requests exercise the actual committed handshake/capture.
    from types import SimpleNamespace as NS
    from tests import test_ibkr_reconciliation_evidence as evidence
    fixture.api.Client.reqAllOpenOrders = lambda client: client.wrapper.openOrderEnd()
    fixture.api.Client.reqCompletedOrders = lambda client, apiOnly: client.wrapper.completedOrdersEnd()
    def executions(client, req_id, filt):
        if full or foreign_exec:
            values = dict(evidence.EXECUTION, orderRef=fixture.order.client_order_id,
                shares=D(10), cumQty=D(10), acctNumber=evidence.ACCOUNT)
            contract = dict(evidence.CONTRACT)
            if foreign_exec:
                values.update(execId='owned-exec', orderId=99, clientId=3,
                    orderRef='foreign', permId=99, shares=D(4), cumQty=D(4))
                contract.update(conId=18, symbol='OTHER')
            client.wrapper.execDetails(req_id, NS(**contract), NS(**values))
        client.wrapper.execDetailsEnd(req_id)
    fixture.api.Client.reqExecutions = executions
    if foreign_exec:
        def orders(client):
            client.wrapper.openOrder(fixture.pending_id, NS(**evidence.CONTRACT),
                NS(**dict(evidence.ORDER, orderId=fixture.pending_id,
                    orderRef=fixture.order.client_order_id, account=evidence.ACCOUNT,
                    lmtPrice=1.25, auxPrice=1.25)), NS(status='Submitted'))
            client.wrapper.openOrderEnd()
        fixture.api.Client.reqAllOpenOrders = orders
    attempt = fixture.begin()
    fixture.refresh()
    return fixture.consume(attempt)


def regression_genuine_terminal_after_stale_zero(status):
    def test(self):
        fixture = genuine_regression_fixture(self)
        fixture.status(status='Submitted', filled=D(0), remaining=D(10)); fixture.sync()
        fixture.open(status=status); fixture.sync()
        value = genuine_read_collection(fixture)
        self.assertEqual((value.final_local.last_status, value.final_local.last_filled,
                          value.final_local.last_remaining), (status, D(0), D(10)))
        before = fixture.safety()
        self.check(value, S.UNRESOLVED, 0, Q.NONE)
        self.assertEqual(fixture.safety(), before)
    return test
for status in ('Cancelled', 'ApiCancelled', 'Inactive', 'Expired'):
    setattr(EvaluatorTests, 'test_regression_genuine_stale_zero_then_'+status,
            regression_genuine_terminal_after_stale_zero(status))


def regression_genuine_filled_after_history(filled):
    def test(self):
        fixture = genuine_regression_fixture(self)
        fixture.status(status='Submitted', filled=D(filled), remaining=D(10-filled)); fixture.sync()
        if filled == 0:
            fixture.open(status='Filled'); fixture.sync()
        value = genuine_read_collection(fixture, full=True)
        self.assertEqual(value.final_local.last_filled, D(filled))
        before = fixture.safety()
        self.check(value, S.FILLED, 10, Q.EXACT)
        self.assertEqual(fixture.safety(), before)
    return test
for filled in (0, 4):
    setattr(EvaluatorTests, 'test_regression_genuine_full_after_historical_'+str(filled),
            regression_genuine_filled_after_history(filled))


def regression_genuine_foreign_known_execid(self):
    fixture = genuine_regression_fixture(self)
    fixture.execution(values={'execId': 'owned-exec', 'shares': D(4)}); fixture.sync()
    value = genuine_read_collection(fixture, foreign_exec=True)
    self.assertEqual(value.final_local.execution_observations[0][0], 'owned-exec')
    self.assertEqual(value.read_receipt.private_snapshot.executions[0].exec_id, 'owned-exec')
    before = fixture.safety()
    self.check(value, S.CONFLICT)
    self.assertEqual(fixture.safety(), before)
EvaluatorTests.test_regression_genuine_foreign_known_execid = regression_genuine_foreign_known_execid


if __name__ == '__main__':
    unittest.main()
