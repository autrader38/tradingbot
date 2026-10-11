"""Historical lifecycle adversaries; pure facts and temporary anonymous SDK fixtures."""

import ast
import builtins
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from decimal import Decimal, Inexact, Rounded, localcontext
import inspect
import socket
import sqlite3
import time
import unittest
from unittest.mock import patch

from tradingbot_broker import paper_working_order as w
from tradingbot_broker import paper_execution_accounting as a
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker import ibkr_paper_transport as paper
from tradingbot_broker import paper_dispatch as dispatch
from tradingbot_broker.broker import BrokerOperationError
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tradingbot_broker.paper_execution import PaperExecutionAuthorizationStatus as Auth
from tradingbot_broker.models import Position
from tests import test_paper_execution_journal as facts
from tests.test_paper_execution_accounting import AccountingFixture


D = Decimal
S = j._LifecycleStatus
C = w._LifecycleClassification
K = w._LifecycleConsistency
O = w._LifecycleObligation
P = w._HistoricalPrefixState
Owner = w._PaperWorkingOrderEvidenceOwner
Accounting = a._PaperExecutionAccountingOwner


def evidence(*events, pending=False):
    through = max((e.source.sequence for e in events if hasattr(e, 'source')), default=0)
    cursor = 0 if pending else through
    rows = facts.records((*facts.prefix(), *events, j._CallbackProgress(facts.SID, cursor, through)))
    snapshot = a._derive_accounting(a._AccountingEvidence(facts.header(), rows, facts.SID,
        cursor, through, 1 if pending else 0, 0, True))
    return w._LifecycleEvidence(snapshot, 19, 3)


def projection(*events, **kwargs):
    return w._derive_lifecycle(evidence(*events, **kwargs)).entries[0]


def status_event(status, filled='3', sequence=2, **changes):
    filled = D(10) if status is S.FILLED else D(filled)
    return facts.status(sequence, status=status, filled=filled, remaining=D(10)-filled, **changes)


class PureTests(unittest.TestCase):
    def check(self, value, classification=None, consistency=K.COMPATIBLE):
        if classification is not None:
            self.assertIs(value.classification, classification)
        self.assertIs(value.consistency, consistency)
        for f in fields(value):
            if not f.init:
                self.assertIn(getattr(value, f.name), (False, None))
        self.assertIn(O.CURRENT_REMAINDER_UNKNOWN, value.obligations)
        self.assertIn(O.CURRENT_INVENTORY_UNKNOWN, value.obligations)
        self.assertIn(O.CURRENT_LIFECYCLE_EVIDENCE_REQUIRED, value.obligations)
        self.assertIn(O.PROTECTABLE_QUANTITY_UNQUALIFIED, value.obligations)

    def test_q_e_l_and_residual_stay_distinct(self):
        value = projection(facts.opened(), status_event(S.SUBMITTED, '5'), facts.execution())
        self.assertEqual((value.requested_quantity, value.recorded_execution_quantity,
            value.historical_filled_lower_bound, value.arithmetic_residual,
            value.historical_possible_remaining_upper_bound), (D(10), D(3), D(5), D(7), D(5)))
        self.assertIn(O.STATUS_EXECUTION_QUANTITY_GAP, value.obligations)
        self.assertIn(O.EXECUTION_PRICE_COVERAGE_MISSING, value.obligations)

    def test_same_event_quantities_and_prices_retained(self):
        status = status_event(S.SUBMITTED, average_price=D('10.25'), last_price=D('10.75'), parent_id=8)
        value = projection(facts.opened(), status, facts.execution())
        observed = value.observations[1]
        self.assertIsNone(observed.open_evidence)
        self.assertEqual(observed.status_evidence, status)
        self.assertIsNot(observed.status_evidence, status)
        self.assertIsNot(observed.source, status.source)
        self.assertEqual(observed.status_evidence.parent_id, 8)

    def test_open_terminal_not_merged_with_earlier_status(self):
        value = projection(facts.opened(), facts.status(), facts.opened(3,status=S.CANCELLED))
        self.check(value, C.HISTORICAL_TERMINAL_OBSERVED)
        self.assertEqual(value.observations[1].status_evidence.filled, 0)
        self.assertIs(value.observations[1].status, S.SUBMITTED)
        self.assertIsNone(value.observations[2].status_evidence)

    def test_later_open_does_not_qualify_prior_status(self):
        value = projection(status_event(S.SUBMITTED), facts.opened(3))
        self.check(value, C.HISTORICAL_LIFECYCLE_UNRESOLVED, K.UNRESOLVED)
        self.assertFalse(value.observations[0].identity_qualified)
        self.assertEqual(value.historical_filled_lower_bound, 0)
        self.assertIsNone(value.historical_possible_remaining_upper_bound)

    def test_missing_open_reference_remains_unqualified(self):
        value = projection(facts.opened(order_ref=None), facts.status())
        self.check(value, C.HISTORICAL_LIFECYCLE_UNRESOLVED, K.UNRESOLVED)
        self.assertIn(O.UNQUALIFIED_OBSERVATION, value.obligations)

    def test_filled_without_execution_prices(self):
        value = projection(facts.opened(), status_event(S.FILLED))
        self.check(value, C.HISTORICAL_FILLED_REPORTED)
        self.assertEqual(value.recorded_execution_quantity, 0)
        self.assertEqual(value.historical_filled_lower_bound, 10)
        self.assertIsNone(value.exact_recorded_average_price)
        self.assertIn(O.EXECUTION_PRICE_COVERAGE_MISSING, value.obligations)

    def test_full_executions_without_lifecycle(self):
        value = projection(facts.execution(shares=D(10)))
        self.check(value, C.NO_QUALIFIED_LIFECYCLE, K.UNRESOLVED)
        self.assertEqual(value.recorded_execution_quantity, 10)
        self.assertFalse(value.historical_terminal_observed)
        self.assertIsNone(value.historical_possible_remaining_upper_bound)

    def test_execution_exceeds_earlier_working_status(self):
        value = projection(facts.opened(), status_event(S.SUBMITTED), facts.execution(shares=D(5)))
        self.check(value, C.HISTORICAL_WORKING_OBSERVED)
        self.assertNotIn(O.TERMINAL_EXECUTION_DISAGREEMENT, value.obligations)
        self.assertEqual(value.historical_possible_remaining_upper_bound, 5)

    def test_duplicate_execution_has_one_acquisition(self):
        value = projection(facts.opened(), facts.execution(), facts.execution(4))
        self.assertEqual(value.recorded_execution_ids, ('exec-1',))
        self.assertEqual(value.recorded_execution_quantity, 3)

    def test_interleaved_execution_repeat_has_one_acquisition(self):
        value = projection(facts.opened(), facts.execution(), status_event(S.SUBMITTED,sequence=4), facts.execution(5))
        self.assertEqual(value.recorded_execution_quantity, 3)

    def test_execution_id_conflict_rejects_whole_projection(self):
        with self.assertRaisesRegex(ReadOnlyError, '^INVALID_PAPER_EXECUTION_JOURNAL$'):
            projection(facts.execution(), facts.execution(4,price=D(11)))

    def test_overfill_rejects_whole_projection(self):
        with self.assertRaisesRegex(ReadOnlyError, '^INVALID_PAPER_EXECUTION_JOURNAL$'):
            projection(facts.execution(), facts.execution(4,exec_id='exec-2',shares=D(8)))

    def test_same_source_conflict_rejects_whole_projection(self):
        with self.assertRaisesRegex(ReadOnlyError, '^INVALID_PAPER_EXECUTION_JOURNAL$'):
            projection(facts.opened(), facts.status(1))

    def test_identical_status_repeat_is_compatible(self):
        value = projection(facts.opened(), status_event(S.SUBMITTED), status_event(S.SUBMITTED,sequence=3))
        self.check(value, C.HISTORICAL_WORKING_OBSERVED)
        self.assertEqual(len(value.observations), 3)

    def test_status_average_never_replaces_fill_price(self):
        value = projection(facts.opened(), status_event(S.SUBMITTED,average_price=D(50)), facts.execution(price=D(10)))
        self.assertEqual(value.exact_recorded_average_price, a._ExactRatio(10,1))
        self.assertEqual(value.observations[1].status_evidence.average_price, 50)
        self.assertIn(O.STATUS_EXECUTION_PRICE_DIFFERENCE, value.obligations)
        self.assertIs(value.consistency, K.COMPATIBLE)

    def test_last_price_never_becomes_fill(self):
        value = projection(facts.opened(), status_event(S.SUBMITTED,last_price=D(999)))
        self.assertIsNone(value.exact_recorded_average_price)
        self.assertEqual(value.recorded_execution_quantity, 0)

    def test_zero_execution_price_is_retained(self):
        value = projection(facts.opened(), facts.execution(price=D(0)))
        self.assertEqual(value.exact_recorded_average_price, a._ExactRatio(0,1))

    def test_nonterminating_average_stays_exact(self):
        value = projection(facts.opened(), facts.execution(shares=D(1),price=D(1)),
            facts.execution(4,exec_id='exec-2',shares=D(2),price=D('1.5')))
        self.assertEqual(value.exact_recorded_average_price, a._ExactRatio(4,3))

    def test_low_decimal_precision_does_not_round_bound(self):
        data = evidence(facts.opened(), status_event(S.SUBMITTED,'3.123456789'))
        expected = w._derive_lifecycle(data)
        with localcontext() as ctx:
            ctx.prec=2; ctx.traps[Inexact]=True; ctx.traps[Rounded]=True
            self.assertEqual(w._derive_lifecycle(data), expected)
        self.assertEqual(expected.entries[0].historical_possible_remaining_upper_bound,D('6.876543211'))

    def test_source_order_not_append_order(self):
        value = projection(facts.opened(), status_event(S.SUBMITTED, '5',sequence=4),
            status_event(S.SUBMITTED, '3',sequence=2))
        self.assertEqual(tuple(o.source.sequence for o in value.observations),(1,2,4))
        self.assertIs(value.consistency,K.COMPATIBLE)

    def test_first_exact_execution_source_survives_duplicate(self):
        value = projection(facts.opened(), status_event(S.CANCELLED), facts.execution(), facts.execution(4))
        self.assertEqual(value.executions_received_after_terminal,('exec-1',))

    def test_no_executions_is_recorded_zero(self):
        value = projection(facts.opened())
        self.assertIn(O.ZERO_FILL_NOT_PROVEN,value.obligations)
        self.assertFalse(value.no_late_fill_guarantee)

    def test_unknown_status_after_anchor_stays_unresolved(self):
        value = projection(facts.opened(),status_event(S.UNKNOWN))
        self.check(value,C.HISTORICAL_LIFECYCLE_UNRESOLVED,K.UNRESOLVED)
        self.assertIn(O.UNKNOWN_LIFECYCLE,value.obligations)

    def test_identifier_only_denial_has_no_quantity_defaults(self):
        rows=facts.records((facts.session(),j._AttemptConsumed(facts.SID,'denied'),
            j._AttemptClosedWithoutInvocation('denied',j._ClosureReason.LOCAL_GATE_DENIED)))
        snap=a._derive_accounting(a._AccountingEvidence(facts.header(),rows,facts.SID,0,0,0,0,False))
        value=w._derive_lifecycle(w._LifecycleEvidence(snap,19,3)).entries[0]
        self.assertIsNone(value.requested_quantity)
        self.assertIsNone(value.arithmetic_residual)
        self.assertIsNone(value.historical_possible_remaining_upper_bound)
        self.assertNotIn(O.PROTECTABLE_QUANTITY_UNQUALIFIED,value.obligations)

    def test_missing_commissions_do_not_change_lifecycle(self):
        value=projection(facts.opened(),facts.execution())
        self.check(value,C.HISTORICAL_WORKING_OBSERVED)

    def test_known_commission_does_not_change_lifecycle(self):
        first=projection(facts.opened(),facts.execution())
        second=projection(facts.opened(),facts.execution(),facts.commission())
        self.assertEqual(first,second)

    def test_signed_rebate_does_not_change_lifecycle(self):
        self.assertEqual(projection(facts.opened(),facts.execution()),
            projection(facts.opened(),facts.execution(),facts.commission(amount=D('-0.25'))))

    def test_fixed_reason_order_and_no_duplicates(self):
        value=projection(facts.opened(),status_event(S.SUBMITTED,'5'),status_event(S.SUBMITTED,'3',sequence=3))
        self.assertEqual(value.obligations,tuple(o for o in O if o in value.obligations))
        self.assertEqual(len(value.obligations),len(set(value.obligations)))

    def test_pure_repeat_is_deterministic(self):
        data=evidence(facts.opened(),status_event(S.CANCELLED),facts.execution())
        self.assertEqual(w._derive_lifecycle(data),w._derive_lifecycle(data))

    def test_pure_projection_has_zero_io_or_clock(self):
        data=evidence(facts.opened(),facts.execution())
        with patch.object(builtins,'open',side_effect=AssertionError('file')), \
             patch.object(sqlite3,'connect',side_effect=AssertionError('sqlite')), \
             patch.object(socket,'socket',side_effect=AssertionError('socket')), \
             patch.object(time,'monotonic',side_effect=AssertionError('clock')):
            self.assertEqual(w._derive_lifecycle(data),w._derive_lifecycle(data))

    def test_malformed_same_event_pair_rejected(self):
        data=evidence(facts.opened(),status_event(S.SUBMITTED))
        entry=data.accounting.entries[0]
        event=replace(entry.status_observations[0],remaining=D(8))
        changed=replace(data,accounting=replace(data.accounting,entries=(replace(entry,status_observations=(event,)),)))
        with self.assertRaises(ReadOnlyError): w._derive_lifecycle(changed)

    def test_malformed_callback_generation_rejected(self):
        data=evidence(facts.opened())
        entry=data.accounting.entries[0]
        event=replace(entry.open_observations[0],source=facts.source(generation=4))
        changed=replace(data,accounting=replace(data.accounting,entries=(replace(entry,open_observations=(event,)),)))
        with self.assertRaises(ReadOnlyError): w._derive_lifecycle(changed)

    def test_arbitrary_handoff_type_rejected(self):
        with self.assertRaises(ReadOnlyError): w._derive_lifecycle(object())


def status_case(status):
    def test(self):
        first=facts.opened(status=S.PENDING_SUBMIT if status is S.PENDING_SUBMIT else status)
        value=projection(first,status_event(status))
        expected={S.PENDING_SUBMIT:C.HISTORICAL_SUBMISSION_OBSERVED,
            S.PRE_SUBMITTED:C.HISTORICAL_WORKING_OBSERVED,S.SUBMITTED:C.HISTORICAL_WORKING_OBSERVED,
            S.FILLED:C.HISTORICAL_FILLED_REPORTED,S.UNKNOWN:C.HISTORICAL_LIFECYCLE_UNRESOLVED}.get(status,C.HISTORICAL_PARTIAL_TERMINAL)
        self.check(value,expected,K.UNRESOLVED if status is S.UNKNOWN else K.COMPATIBLE)
        self.assertEqual(value.observations[-1].status,status)
    return test


for status in S:
    setattr(PureTests,'test_status_'+status.name.lower(),status_case(status))


def terminal_case(status, kind):
    def test(self):
        if kind=='zero':
            value=projection(facts.opened(),status_event(status,'0'))
            self.assertIn(O.ZERO_FILL_NOT_PROVEN,value.obligations)
            self.check(value,C.HISTORICAL_TERMINAL_OBSERVED)
        elif kind=='late':
            value=projection(facts.opened(),status_event(status),facts.execution())
            self.check(value,C.HISTORICAL_PARTIAL_TERMINAL)
            self.assertEqual(value.executions_received_after_terminal,('exec-1',))
        else:
            value=projection(facts.opened(),status_event(status),facts.execution(shares=D(5)))
            self.check(value,C.HISTORICAL_LIFECYCLE_UNRESOLVED,K.UNRESOLVED)
            self.assertEqual(value.recorded_execution_quantity,5)
            self.assertIn(O.TERMINAL_EXECUTION_DISAGREEMENT,value.obligations)
            self.assertIsNone(value.historical_possible_remaining_upper_bound)
    return test


for status in (S.CANCELLED,S.API_CANCELLED,S.INACTIVE,S.EXPIRED):
    for kind in ('zero','late','disagreement'):
        setattr(PureTests,'test_'+status.name.lower()+'_'+kind,terminal_case(status,kind))


_PROGRESSIONS=(
    (S.PENDING_SUBMIT,S.PRE_SUBMITTED,'0','0',None),
    (S.PRE_SUBMITTED,S.SUBMITTED,'0','3',None),
    (S.SUBMITTED,S.SUBMITTED,'3','5',None),
    (S.SUBMITTED,S.CANCELLED,'3','3',None),
    (S.SUBMITTED,S.FILLED,'3','10',None),
    (S.SUBMITTED,S.PRE_SUBMITTED,'3','3',O.STATUS_REGRESSION),
    (S.SUBMITTED,S.SUBMITTED,'5','3',O.FILLED_REGRESSION),
    (S.CANCELLED,S.SUBMITTED,'3','3',O.TERMINAL_TO_WORKING),
    (S.API_CANCELLED,S.PENDING_SUBMIT,'3','3',O.TERMINAL_TO_WORKING),
    (S.CANCELLED,S.API_CANCELLED,'3','3',O.CONFLICTING_TERMINAL_STATUS),
    (S.FILLED,S.CANCELLED,'10','10',O.CONFLICTING_TERMINAL_STATUS),
    (S.INACTIVE,S.EXPIRED,'0','0',O.CONFLICTING_TERMINAL_STATUS))


def progression_case(first,last,fill1,fill2,problem):
    def test(self):
        value=projection(facts.opened(status=first),status_event(first,fill1),status_event(last,fill2,sequence=3))
        self.assertEqual(len(value.observations),3)
        if problem is None:
            self.assertIs(value.consistency,K.COMPATIBLE)
            self.assertIsNotNone(value.historical_possible_remaining_upper_bound)
        else:
            self.assertIs(value.consistency,K.CONTRADICTORY)
            self.assertIn(problem,value.obligations)
            self.assertIsNone(value.historical_possible_remaining_upper_bound)
            if problem is O.FILLED_REGRESSION:
                self.assertIn(O.REMAINING_REGRESSION,value.obligations)
    return test


for number,case in enumerate(_PROGRESSIONS):
    setattr(PureTests,'test_progression_'+str(number),progression_case(*case))


class LifecycleFixture(AccountingFixture):
    def prepare_lifecycle(self):
        self.begin(); self.open(); self.record('3'); self.drain(); self.refresh()
        self.lifecycle=w._create_paper_working_order_evidence_owner(self.accounting)
        return self.lifecycle._refresh()

    def life_refresh(self): return self.lifecycle._refresh()

    def failed_lifecycle(self,prior):
        with self.assertRaisesRegex(ReadOnlyError,'^PAPER_WORKING_ORDER_EVIDENCE_UNAVAILABLE$'):
            self.life_refresh()
        self.assertTrue(self.lifecycle._failed)
        self.assertIs(self.lifecycle._snapshot,prior)
        self.assertIs(self.lifecycle._currentness,P.BLOCKED)
        self.assertTrue(self.journal._inhibited)
        self.assertTrue(self.coordinator._reconciliation_required)
        self.assertTrue(self.broker.reconciliation_required)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertIs(self.broker._paper_authority.status,Auth.INVALIDATED)


class OwnershipTests(LifecycleFixture):
    def test_exact_lifetime_association(self):
        snap=self.prepare_lifecycle()
        expected=(self.accounting,)+self.accounting._PaperExecutionAccountingOwner__association
        actual=self.lifecycle._PaperWorkingOrderEvidenceOwner__association
        self.assertTrue(all(x is y for x,y in zip(actual,expected)))
        self.assertIs(snap,self.lifecycle._snapshot)
        self.assertIs(self.lifecycle._currentness,P.VERIFIED_HISTORICAL_PREFIX)

    def test_direct_constructor_rejected(self):
        with self.assertRaises(ReadOnlyError): Owner()

    def test_factory_rejects_arbitrary_value(self):
        with self.assertRaises(ReadOnlyError): w._create_paper_working_order_evidence_owner(object())

    def test_copied_accounting_owner_rejected(self):
        self.prepare_lifecycle()
        with self.assertRaises(ReadOnlyError): w._create_paper_working_order_evidence_owner(copy(self.accounting))
        self.assertFalse(self.accounting._failed)

    def test_second_owner_rejected(self):
        prior=self.prepare_lifecycle()
        with self.assertRaises(ReadOnlyError): w._create_paper_working_order_evidence_owner(self.accounting)
        self.assertIs(self.lifecycle._snapshot,prior)
        self.assertFalse(self.lifecycle._failed)

    def test_copied_lifecycle_owner_has_no_provenance(self):
        self.prepare_lifecycle()
        with self.assertRaises(ReadOnlyError): copy(self.lifecycle)._refresh()

    def test_missing_successful_accounting_snapshot_blocks(self):
        self.begin()
        self.lifecycle=w._create_paper_working_order_evidence_owner(self.accounting)
        self.failed_lifecycle(None)

    def test_equal_reconstructed_accounting_snapshot_rejected(self):
        prior=self.prepare_lifecycle()
        self.accounting._PaperExecutionAccountingOwner__snapshot=replace(self.accounting._snapshot)
        self.failed_lifecycle(prior)

    def test_equal_reconstructed_handoff_rejected(self):
        prior=self.prepare_lifecycle(); original=Accounting._capture_lifecycle_evidence
        def capture(owner,consumer): return replace(original(owner,consumer))
        with patch.object(Accounting,'_capture_lifecycle_evidence',capture): self.failed_lifecycle(prior)

    def test_equal_reconstructed_candidate_rejected(self):
        prior=self.prepare_lifecycle(); original=Accounting._publish_lifecycle_snapshot
        def publish(owner,consumer,evidence,candidate): return original(owner,consumer,evidence,replace(candidate))
        with patch.object(Accounting,'_publish_lifecycle_snapshot',publish): self.failed_lifecycle(prior)

    def test_equal_reconstructed_lifecycle_snapshot_is_not_publication(self):
        self.prepare_lifecycle()
        self.lifecycle._PaperWorkingOrderEvidenceOwner__snapshot=replace(self.lifecycle._snapshot)
        self.assertIs(self.lifecycle._currentness,P.BLOCKED)
        self.assertTrue(self.lifecycle._failed)

    def test_direct_publication_rejected(self):
        prior=self.prepare_lifecycle()
        with self.assertRaises(ReadOnlyError): self.lifecycle._publish(prior)
        self.assertIs(self.lifecycle._snapshot,prior)

    def test_publication_permit_cannot_be_reused(self):
        prior=self.prepare_lifecycle()
        with self.assertRaises(ReadOnlyError):
            self.accounting._consume_lifecycle_publication(self.lifecycle,object(),prior)

    def test_foreign_lifecycle_consumer_rejected(self):
        self.prepare_lifecycle()
        foreign=object.__new__(Owner)
        foreign._initialize(self.lifecycle._PaperWorkingOrderEvidenceOwner__association)
        with self.coordinator._lock,self.broker._lock:
            with self.assertRaises(ReadOnlyError): self.accounting._capture_lifecycle_evidence(foreign)

    def test_owner_association_assignment_rejected(self):
        self.prepare_lifecycle()
        with self.assertRaises(ReadOnlyError): self.lifecycle._PaperWorkingOrderEvidenceOwner__association=()

    def test_wrong_thread_before_storage(self):
        prior=self.prepare_lifecycle()
        with patch.object(storage._SQLiteJournalStore,'_begin',side_effect=AssertionError('SQLite')) as begin:
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaises(ReadOnlyError): pool.submit(self.life_refresh).result(timeout=5)
        begin.assert_not_called()
        self.assertIs(self.lifecycle._snapshot,prior)
        self.assertTrue(self.lifecycle._failed)

    def test_factory_wrong_thread_before_storage(self):
        self.begin(); self.refresh()
        with patch.object(storage._SQLiteJournalStore,'_begin',side_effect=AssertionError('SQLite')) as begin:
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaises(ReadOnlyError):
                    pool.submit(w._create_paper_working_order_evidence_owner,self.accounting).result(timeout=5)
        begin.assert_not_called()

    def test_accounting_failure_blocks_lifecycle(self):
        prior=self.prepare_lifecycle(); self.accounting._secure_failure(); self.failed_lifecycle(prior)

    def test_failure_does_not_erase_accounting_acquisitions(self):
        prior=self.prepare_lifecycle(); accounting=self.accounting._snapshot
        with patch.object(w,'_analyze_lifecycle',side_effect=ValueError('discarded')):
            self.failed_lifecycle(prior)
        self.assertIs(self.accounting._snapshot,accounting)
        self.assertEqual(accounting.entries[0].recorded_execution_quantity,3)
        self.assertFalse(self.accounting._failed)


class CurrentnessTests(LifecycleFixture):
    def test_pending_callback_snapshot_can_project_history(self):
        self.begin(); self.record('3'); self.refresh()
        self.lifecycle=w._create_paper_working_order_evidence_owner(self.accounting)
        snap=self.life_refresh()
        self.assertIs(snap.currentness_at_publication,P.HISTORICAL_PREFIX_WITH_PENDING_CALLBACKS)
        self.assertEqual(snap.entries[0].recorded_execution_quantity,0)

    def test_staged_commission_remains_pending(self):
        self.begin(); self.commission(); self.drain(); self.refresh()
        self.lifecycle=w._create_paper_working_order_evidence_owner(self.accounting)
        snap=self.life_refresh()
        self.assertEqual(snap.staged_count,1)
        self.assertIs(self.lifecycle._currentness,P.HISTORICAL_PREFIX_WITH_PENDING_CALLBACKS)

    def test_covered_unpublished_suffix_remains_pending(self):
        self.begin(); self.commission(exec_id='unknown'); self.open(); self.record('3'); self.drain(); self.refresh()
        self.lifecycle=w._create_paper_working_order_evidence_owner(self.accounting)
        snap=self.life_refresh()
        self.assertGreater(snap.covered_unpublished_count,0)
        self.assertEqual(snap.entries[0].recorded_execution_quantity,3)
        self.assertIs(snap.currentness_at_publication,P.HISTORICAL_PREFIX_WITH_PENDING_CALLBACKS)

    def test_new_callback_marks_prior_snapshot_pending_without_mutation(self):
        prior=self.prepare_lifecycle(); self.record('1',exec_id='later')
        self.assertIs(self.lifecycle._currentness,P.HISTORICAL_PREFIX_WITH_PENDING_CALLBACKS)
        self.assertIs(self.lifecycle._snapshot,prior)
        self.assertIs(prior.currentness_at_publication,P.VERIFIED_HISTORICAL_PREFIX)

    def test_accounting_refresh_then_lifecycle_refresh(self):
        prior=self.prepare_lifecycle(); self.record('2',exec_id='later'); self.drain(); self.refresh()
        after=self.life_refresh()
        self.assertEqual(after.entries[0].recorded_execution_quantity,5)
        self.assertEqual(prior.entries[0].recorded_execution_quantity,3)

    def test_stale_accounting_snapshot_cannot_be_promoted(self):
        prior=self.prepare_lifecycle(); self.record('1',exec_id='later'); self.failed_lifecycle(prior)

    def test_late_commission_preserves_lifecycle(self):
        before=self.prepare_lifecycle(); self.commission(); self.drain(); self.refresh(); after=self.life_refresh()
        self.assertEqual(before.entries,after.entries)
        self.assertFalse(self.accounting._snapshot.entries[0].commission_completeness)

    def test_inhibited_owner_can_project_history(self):
        self.begin(); self.open(); self.record('3'); self.sync(); self.drain(); self.refresh()
        self.lifecycle=w._create_paper_working_order_evidence_owner(self.accounting)
        snap=self.life_refresh()
        self.assertTrue(snap.journal_inhibited_at_publication)
        self.assertEqual(snap.entries[0].recorded_execution_quantity,3)
        self.assertTrue(self.coordinator._reconciliation_required)

    def test_wrong_thread_currentness_blocks(self):
        self.prepare_lifecycle()
        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertIs(pool.submit(lambda:self.lifecycle._currentness).result(timeout=5),P.BLOCKED)


def association_case(index, replacement):
    def test(self):
        prior=self.prepare_lifecycle()
        parts=list(self.journal._PaperDispatchJournalOwner__association)
        parts[index]=replacement(self)
        object.__setattr__(self.journal,'_PaperDispatchJournalOwner__association',tuple(parts))
        self.failed_lifecycle(prior)
    return test


for label,index,replacement in (
    ('coordinator',0,lambda s:object()),('control',1,lambda s:object()),
    ('read',2,lambda s:object()),('write',3,lambda s:object()),
    ('receipt',4,lambda s:replace(s.journal._receipt)),
    ('header',5,lambda s:replace(s.journal._header)),('store',6,lambda s:object()),
    ('session',7,lambda s:replace(s.journal._session)),('thread',8,lambda s:object())):
    setattr(OwnershipTests,'test_substituted_'+label,association_case(index,replacement))


def changed_case(change):
    def test(self):
        prior=self.prepare_lifecycle(); change(self); self.failed_lifecycle(prior)
    return test


for label,change in (
    ('read_generation',lambda s:setattr(s.transport,'_generation',s.transport._generation+1)),
    ('write_generation',lambda s:s.write._begin_generation()),
    ('receipt_provenance',lambda s:setattr(s.transport,'_journal_account_association',None)),
    ('poison',lambda s:setattr(s.journal._store,'_poisoned',True)),
    ('quarantine',lambda s:setattr(s.journal._store,'_quarantined',True)),
    ('closed_store',lambda s:s.journal._close()),
    ('persistence_failure',lambda s:setattr(s.journal,'_PaperDispatchJournalOwner__persistence_failed',True)),
    ('tail',lambda s:setattr(s.journal,'_PaperDispatchJournalOwner__history',())),
    ('cursor',lambda s:setattr(s.journal,'_PaperDispatchJournalOwner__persistence_cursor',0)),
    ('callback_gap',lambda s:s.write._callback_run_ends.__setitem__(-1,s.write._callback_run_ends[-1]+1)),
    ('active_attempt',lambda s:setattr(s.coordinator,'_active_attempt',object()))):
    setattr(CurrentnessTests,'test_'+label+'_fails_closed',changed_case(change))


class AtomicityTests(LifecycleFixture):
    def test_authentication_expiry_during_storage_verification_blocks(self):
        prior=self.prepare_lifecycle(); original=storage._SQLiteJournalStore._begin; calls=0
        def begin(store,writing):
            nonlocal calls
            result=original(store,writing)
            if not writing:
                calls+=1
                if calls==2:
                    self.clock.advance((self.transport._snapshot.account.valid_until-self.clock()).total_seconds()+1)
            return result
        with patch.object(storage._SQLiteJournalStore,'_begin',begin): self.failed_lifecycle(prior)

    def test_modified_published_permission_blocks_currentness(self):
        prior=self.prepare_lifecycle()
        object.__setattr__(prior,'new_entry_permitted',True)
        self.assertIs(self.lifecycle._currentness,P.BLOCKED)
        self.assertTrue(self.journal._inhibited)

    def test_callback_worker_during_projection_rejects_publication(self):
        prior=self.prepare_lifecycle(); original=w._derive_lifecycle
        def derive(data):
            result=original(data)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(self.record,'1',exec_id='race').result(timeout=5)
            return result
        with patch.object(w,'_derive_lifecycle',derive): self.failed_lifecycle(prior)

    def test_sqlite_never_holds_capture_locks(self):
        self.prepare_lifecycle(); original=storage._SQLiteJournalStore._begin
        def begin(store,writing):
            for lock in (self.transport._lifecycle,self.transport._condition,self.write._lock,self.write._arrival_lock):
                self.assertFalse(lock._is_owned())
            return original(store,writing)
        with patch.object(storage._SQLiteJournalStore,'_begin',begin): self.life_refresh()

    def test_accounting_snapshot_change_during_derivation_rejects(self):
        prior=self.prepare_lifecycle(); original=w._derive_lifecycle
        def derive(data):
            result=original(data); self.refresh(); return result
        with patch.object(w,'_derive_lifecycle',derive): self.failed_lifecycle(prior)

    def test_inhibition_transition_during_derivation_rejects(self):
        prior=self.prepare_lifecycle(); original=w._derive_lifecycle
        def derive(data):
            result=original(data); self.journal._inhibit(); return result
        with patch.object(w,'_derive_lifecycle',derive): self.failed_lifecycle(prior)

    def test_new_durable_tail_during_derivation_rejects(self):
        prior=self.prepare_lifecycle(); original=w._derive_lifecycle
        def derive(data):
            result=original(data)
            self.journal._append_event(j._CallbackProgress(self.journal._session.session_id,
                self.journal._persistence_cursor,self.write._callback_sequence),diagnostic=True)
            return result
        with patch.object(w,'_derive_lifecycle',derive): self.failed_lifecycle(prior)

    def test_changed_candidate_rejects_publication(self):
        prior=self.prepare_lifecycle(); original=Accounting._publish_lifecycle_snapshot
        def publish(owner,consumer,evidence,candidate):
            object.__setattr__(candidate,'new_entry_permitted',True)
            return original(owner,consumer,evidence,candidate)
        with patch.object(Accounting,'_publish_lifecycle_snapshot',publish): self.failed_lifecycle(prior)

    def test_changed_handoff_rejects_publication(self):
        prior=self.prepare_lifecycle(); original=w._derive_lifecycle
        def derive(data):
            result=original(data); object.__setattr__(data,'write_generation',999); return result
        with patch.object(w,'_derive_lifecycle',derive): self.failed_lifecycle(prior)

    def test_storage_failure_preserves_prior(self):
        prior=self.prepare_lifecycle()
        with patch.object(storage._SQLiteJournalStore,'_begin',side_effect=sqlite3.OperationalError('discarded')):
            self.failed_lifecycle(prior)

    def test_failure_is_not_retried(self):
        prior=self.prepare_lifecycle()
        with patch.object(w,'_derive_lifecycle',side_effect=ValueError('discarded')): self.failed_lifecycle(prior)
        with patch.object(storage._SQLiteJournalStore,'_begin',side_effect=AssertionError('retry')) as begin:
            self.failed_lifecycle(prior)
        begin.assert_not_called()

    def test_reentrant_refresh_fails_closed(self):
        prior=self.prepare_lifecycle()
        with patch.object(w,'_derive_lifecycle',side_effect=lambda data:self.life_refresh()): self.failed_lifecycle(prior)

    def test_currentness_interruption_preserves_exception(self):
        self.prepare_lifecycle(); error=KeyboardInterrupt()
        with patch.object(Accounting,'_lifecycle_currentness',side_effect=error):
            with self.assertRaises(KeyboardInterrupt) as caught: self.lifecycle._currentness
        self.assertIs(caught.exception,error)
        self.assertTrue(self.lifecycle._failed)


def storage_failure_case(code):
    def test(self):
        prior=self.prepare_lifecycle()
        with patch.object(storage._SQLiteJournalStore,'_begin',side_effect=storage._StorageError(code)):
            self.failed_lifecycle(prior)
    return test


for code in (storage._Code.BUSY,storage._Code.COMMIT_OUTCOME_UNKNOWN,storage._Code.CORRUPT):
    setattr(AtomicityTests,'test_storage_'+code.value.lower(),storage_failure_case(code))


_SEAMS=(
    ('before_capture',Accounting,'_capture_lifecycle_evidence'),
    ('verified_handoff',Accounting,'_lifecycle_verified_source'),
    ('observation',w,'_HistoricalLifecycleObservation'),
    ('quantity_analysis',w,'_analyze_quantities'),
    ('contradiction_analysis',w,'_analyze_lifecycle'),
    ('before_revalidation',Accounting,'_publish_lifecycle_snapshot'),
    ('during_revalidation',Accounting,'_lifecycle_state'),
    ('before_publication',Owner,'_publish'),
    ('permit_consumption',Accounting,'_consume_lifecycle_publication'))


class InterruptionTests(LifecycleFixture): pass


def interruption_case(target,name,kind):
    def test(self):
        prior=self.prepare_lifecycle(); error=kind(37) if kind is SystemExit else kind()
        with patch.object(target,name,side_effect=error):
            with self.assertRaises(kind) as caught: self.life_refresh()
        self.assertIs(caught.exception,error)
        self.assertIs(self.lifecycle._snapshot,prior)
        self.assertTrue(self.lifecycle._failed)
        self.assertTrue(self.journal._inhibited)
        self.assertTrue(self.broker.reconciliation_required)
        self.assertTrue(self.write._requires_reconciliation())
    return test


for label,target,name in _SEAMS:
    for kind in (KeyboardInterrupt,SystemExit,GeneratorExit):
        setattr(InterruptionTests,'test_'+label+'_'+kind.__name__.lower(),interruption_case(target,name,kind))


class BoundaryTests(LifecycleFixture):
    def test_refresh_has_no_journal_append_or_dispatch(self):
        self.prepare_lifecycle(); before=self.history(); count=len(self.write_calls())
        with patch.object(storage._SQLiteJournalStore,'_append',side_effect=AssertionError('append')), \
             patch.object(paper.PaperTWSTransport,'_dispatch',side_effect=AssertionError('dispatch')):
            self.life_refresh()
        self.assertEqual(self.history(),before)
        self.assertEqual(len(self.write_calls()),count)

    def test_accounting_snapshot_not_replaced_by_lifecycle(self):
        self.prepare_lifecycle(); snapshot=self.accounting._snapshot
        self.life_refresh(); self.assertIs(self.accounting._snapshot,snapshot)

    def test_generation_change_never_adopts_restart(self):
        prior=self.prepare_lifecycle(); self.write._begin_generation(); self.failed_lifecycle(prior)
        self.assertTrue(prior.restart_quarantine_required)
        self.assertFalse(prior.new_entry_permitted)

    def test_public_broker_still_blocks_writes(self):
        self.prepare_lifecycle()
        with self.assertRaises(BrokerOperationError): self.broker.place_order(self.order,self.clock.now,self.permission)
        with self.assertRaises(BrokerOperationError): self.broker.cancel_order('not-owned',self.clock.now)

    def test_modes_unchanged(self):
        snap=self.prepare_lifecycle()
        self.assertIs(snap.account_mode,AccountMode.UNKNOWN)
        self.assertIs(self.write.account_mode,AccountMode.UNKNOWN)
        self.assertIsNone(self.transport._snapshot.account.mode)
        self.assertFalse(snap.live_supported)

    def test_opaque_values_and_privacy(self):
        snap=self.prepare_lifecycle()
        def visit(value):
            if is_dataclass(value):
                self.assertNotIn('exec-',repr(value))
                self.assertFalse(hasattr(value,'__dict__'))
                self.assertFalse(any(f.name in ('account','account_number','transport','store','capability','token') for f in fields(value)))
                for f in fields(value): visit(getattr(value,f.name))
            elif type(value) is tuple:
                for item in value: visit(item)
            else:
                self.assertIn(type(value),(str,int,bool,D,type(None),j._LifecycleStatus,
                    w._ObservationKind,w._LifecycleClassification,w._LifecycleConsistency,w._LifecycleObligation,
                    w._HistoricalPrefixState,AccountMode))
        visit(snap)
        self.assertEqual(repr(self.lifecycle),'<PrivateHistoricalEntryLifecycleOwner>')

    def test_frozen_nested_observations(self):
        snap=self.prepare_lifecycle()
        with self.assertRaises(FrozenInstanceError): snap.entries=()
        with self.assertRaises(FrozenInstanceError): snap.entries[0].observations[0].status=S.FILLED
        self.assertIs(type(snap.entries),tuple)

    def test_canonical_reason_types_only(self):
        for entry in self.prepare_lifecycle().entries:
            self.assertTrue(all(type(reason) is O for reason in entry.obligations))

    def test_module_has_no_network_storage_or_recovery_import(self):
        tree=ast.parse(inspect.getsource(w))
        imports=[node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
        self.assertNotIn('paper_execution_journal_sqlite',imports)
        self.assertNotIn('paper_recovery',imports)
        self.assertFalse(any(isinstance(node,ast.Import) and any(n.name in ('sqlite3','socket','time') for n in node.names) for node in ast.walk(tree)))
        self.assertFalse(any(name in vars(w) for name in ('PositionManager','PortfolioEngine','Broker')))

    def test_not_publicly_exported(self):
        import tradingbot_broker
        self.assertFalse(hasattr(tradingbot_broker,'PaperWorkingOrderEvidenceOwner'))
        self.assertFalse(any(name in vars(w) for name in ('place_order','cancel_order','apply_recovery')))


def position_case(quantity):
    def test(self):
        before=self.prepare_lifecycle()
        position=Position('security-1','DEMO',D(quantity),D(1),'USD')
        # Aggregate account evidence is intentionally absent from the projector.
        self.broker._account=replace(self.broker._account,positions=(position,))
        self.assertEqual(before.entries,self.life_refresh().entries)
        self.assertFalse(before.entries[0].current_bot_inventory_known)
    return test


for label,quantity in (('manual','7'),('preexisting','100'),('other_client','-9')):
    setattr(BoundaryTests,'test_'+label+'_aggregate_position_not_adopted',position_case(quantity))


def fixed_field_case(name):
    def test(self):
        data=evidence(facts.opened(),facts.execution())
        snap=w._derive_lifecycle(data)
        value=snap if name in ('new_entry_permitted','restart_quarantine_required','live_supported','account_mode') else snap.entries[0]
        with self.assertRaises(ValueError): replace(value,**{name:True})
    return test


for name in ('current_terminality_known','current_terminal_status','current_broker_order_state',
    'current_working_remainder','current_broker_remaining_quantity','currently_working_quantity',
    'definitely_cancelled_quantity','current_bot_inventory_known','current_bot_held_quantity','closed_quantity',
    'disposition_coverage_complete','protectable_quantity','protective_sizing_qualified','no_late_fill_guarantee',
    'cancellation_authority','trading_authority','recovery_authority','new_entry_permitted',
    'restart_quarantine_required','live_supported','account_mode'):
    setattr(PureTests,'test_fixed_'+name,fixed_field_case(name))


if __name__=='__main__': unittest.main()
