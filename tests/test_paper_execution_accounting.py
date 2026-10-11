"""C3C5A: real temporary storage, anonymous fake SDKs, no broker connection."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import copy
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal, localcontext
import inspect
import sqlite3
from unittest.mock import patch

from tradingbot_broker import paper_execution_accounting as a
from tradingbot_broker import paper_dispatch_journal as integration
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker import ibkr_paper_transport as paper
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tradingbot_broker.paper_execution import PaperExecutionAuthorizationStatus as Auth
from tradingbot_broker.broker import BrokerOperationError
from tradingbot_broker.models import Position
from tests.test_paper_callback_persistence import PersistenceFixture


D = Decimal
Owner = a._PaperExecutionAccountingOwner
JournalOwner = integration._PaperDispatchJournalOwner
State = a._AccountingCurrentness


class AccountingFixture(PersistenceFixture):
    def begin(self):
        self.start()
        self.accounting = a._create_paper_execution_accounting_owner(self.journal)
        return self.accounting

    def record(self, shares='4', price='1.25', exec_id='execution-1', **values):
        self.execution(values={'shares': D(shares), 'price': D(price), 'execId': exec_id, **values})

    def refresh(self):
        return self.accounting._refresh()

    def entry(self):
        return self.refresh().entries[0]

    def filled(self):
        self.begin(); self.record('10'); self.drain()
        return self.entry()

    def failed_refresh(self, *, prior=None):
        with self.assertRaisesRegex(ReadOnlyError, '^PAPER_EXECUTION_ACCOUNTING_UNAVAILABLE$'):
            self.refresh()
        self.assertTrue(self.accounting._failed)
        self.assertIs(self.accounting._currentness, State.BLOCKED)
        self.assertIs(self.accounting._snapshot, prior)
        self.assertTrue(self.journal._inhibited)
        self.assertTrue(self.coordinator._reconciliation_required)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertTrue(self.broker.reconciliation_required)
        self.assertIs(self.broker._paper_authority.status, Auth.INVALIDATED)


class OwnershipTests(AccountingFixture):
    def test_exact_lifetime_association(self):
        self.begin()
        association = self.accounting._PaperExecutionAccountingOwner__association
        originals = (self.journal,) + self.journal._PaperDispatchJournalOwner__components
        self.assertTrue(all(x is y for x, y in zip(association, originals)))
        self.assertIs(self.refresh(), self.accounting._snapshot)

    def test_constructor_cannot_issue_owner(self):
        with self.assertRaises(ReadOnlyError): Owner()

    def test_factory_requires_exact_journal_type(self):
        with self.assertRaises(ReadOnlyError): a._create_paper_execution_accounting_owner(object())

    def test_second_accounting_owner_rejected(self):
        self.begin(); prior = self.refresh()
        with self.assertRaises(ReadOnlyError): a._create_paper_execution_accounting_owner(self.journal)
        self.assertFalse(self.accounting._failed)
        self.assertEqual(self.refresh(), prior)

    def test_copied_owner_is_not_registered_authority(self):
        self.begin(); clone = copy(self.accounting)
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite')) as begin:
            with self.assertRaises(ReadOnlyError): clone._refresh()
        begin.assert_not_called()
        self.assertFalse(self.accounting._failed)

    def test_owner_reinitialization_rejected(self):
        self.begin()
        with self.assertRaises(ReadOnlyError): self.accounting._initialize(())

    def test_wrong_thread_refresh_fails_before_sqlite(self):
        self.begin(); prior = self.refresh()
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite')) as begin:
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaises(ReadOnlyError): pool.submit(self.refresh).result(timeout=5)
        begin.assert_not_called()
        self.assertIs(self.accounting._snapshot, prior)
        self.assertIs(self.accounting._currentness, State.BLOCKED)

    def test_wrong_thread_creation_fails_before_sqlite(self):
        self.start()
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite')) as begin:
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaises(ReadOnlyError):
                    pool.submit(a._create_paper_execution_accounting_owner, self.journal).result(timeout=5)
        begin.assert_not_called()

    def test_forged_handoff_rejected(self):
        self.begin()
        with self.coordinator._lock, self.broker._lock:
            evidence = self.journal._capture_accounting_evidence(self.accounting)
            fake = replace(evidence)
            candidate = a._derive_accounting(fake)
            with self.assertRaises(ReadOnlyError):
                self.journal._publish_accounting_snapshot(self.accounting, fake, candidate)
        self.assertIsNone(self.accounting._snapshot)

    def test_equal_candidate_reconstruction_rejected(self):
        self.begin(); original = a._derive_accounting
        def candidate(evidence):
            value = original(evidence)
            # Attempt replacement at the final exact candidate seam.
            self.forged_candidate = replace(value)
            return value
        publish = JournalOwner._publish_accounting_snapshot
        def substituted(journal, owner, evidence, value):
            return publish(journal, owner, evidence, self.forged_candidate)
        with patch.object(a, '_derive_accounting', candidate), patch.object(JournalOwner, '_publish_accounting_snapshot', substituted):
            self.failed_refresh()

    def test_caller_replay_cannot_be_refresh_argument(self):
        self.begin(); replay = self.journal._load().replay
        with self.assertRaises(TypeError): self.accounting._refresh(replay)
        self.assertIsNone(self.accounting._snapshot)

    def test_consumed_handoff_cannot_publish_twice(self):
        self.begin(); self.refresh()
        self.assertIsNone(self.journal._PaperDispatchJournalOwner__accounting_handoff)

    def test_direct_publication_has_no_authority(self):
        self.begin(); snapshot = self.refresh()
        with self.assertRaises(ReadOnlyError): self.accounting._publish(replace(snapshot))
        self.assertIs(self.accounting._snapshot, snapshot)

    def test_mutated_computed_candidate_rejected(self):
        self.begin(); original = JournalOwner._publish_accounting_snapshot
        def changed(journal, owner, evidence, candidate):
            object.__setattr__(candidate, 'new_entry_permitted', True)
            return original(journal, owner, evidence, candidate)
        with patch.object(JournalOwner, '_publish_accounting_snapshot', changed): self.failed_refresh()

    def test_another_coordinator_journal_cannot_claim_owner(self):
        self.begin(); original = self.journal; account = self.accounting
        self.build(); foreign = self.journal
        with self.assertRaises(ReadOnlyError): foreign._accounting_check(account)
        self.journal = original
        self.assertFalse(account._failed)


def association_case(index, replacement):
    def test(self):
        self.begin(); prior = self.refresh()
        originals = self.journal._PaperDispatchJournalOwner__components
        self.addCleanup(object.__setattr__, self.journal, '_PaperDispatchJournalOwner__components', originals)
        self.addCleanup(object.__setattr__, self.journal, '_PaperDispatchJournalOwner__association', originals)
        parts = list(originals)
        parts[index] = replacement(parts[index])
        # Even equal replacement of BOTH retained tuples cannot rebind accounting.
        object.__setattr__(self.journal, '_PaperDispatchJournalOwner__components', tuple(parts))
        object.__setattr__(self.journal, '_PaperDispatchJournalOwner__association', tuple(parts))
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite')) as begin:
            self.failed_refresh(prior=prior)
        begin.assert_not_called()
    return test


for name, index, replacement in (
    ('coordinator', 0, lambda _: object()), ('control', 1, lambda _: object()),
    ('read_transport', 2, lambda _: object()), ('write_transport', 3, lambda _: object()),
    ('equal_receipt', 4, replace), ('equal_header', 5, replace),
    ('equal_store', 6, copy), ('equal_session', 7, replace), ('thread', 8, lambda _: object())):
    setattr(OwnershipTests, 'test_substituted_' + name, association_case(index, replacement))


class QuantityTests(AccountingFixture):
    def test_no_executions_is_only_recorded_zero(self):
        self.begin(); e = self.entry()
        self.assertEqual((e.requested_quantity, e.recorded_execution_quantity, e.arithmetic_residual), (10, 0, 10))
        self.assertFalse(e.acquisition_quantity_fully_covered)
        self.assertIsNone(e.current_bot_held_quantity)

    def test_partial_acquisition_owned(self):
        self.begin(); self.record('3'); self.drain(); e = self.entry()
        self.assertEqual((e.recorded_execution_quantity, e.arithmetic_residual, len(e.lots)), (3, 7, 1))
        self.assertFalse(e.acquisition_quantity_fully_covered)

    def test_full_quantity_coverage_is_not_inventory(self):
        e = self.filled()
        self.assertTrue(e.acquisition_quantity_fully_covered)
        self.assertTrue(e.gross_execution_prices_complete)
        self.assertFalse(e.current_bot_inventory_known)

    def test_multiple_execution_quantities_exact(self):
        self.begin(); self.record('3'); self.record('2', exec_id='execution-2'); self.drain()
        self.assertEqual(self.entry().recorded_execution_quantity, 5)

    def test_identical_execid_repeat_creates_one_lot(self):
        self.begin(); self.record(); self.record(); self.drain(); e = self.entry()
        self.assertEqual((len(e.lots), e.recorded_execution_quantity), (1, 4))

    def test_interleaved_execid_repeat_creates_one_lot(self):
        self.begin(); self.record(); self.status(); self.record(); self.drain()
        self.assertEqual(len(self.entry().lots), 1)

    def test_conflicting_execid_blocks_authoritative_projection(self):
        self.begin(); self.record(); self.drain(); prior = self.refresh()
        self.record(price='2'); self.drain(); self.failed_refresh(prior=prior)
        self.assertTrue(self.journal._store._quarantined)

    def test_distinct_overfill_rejects_whole_journal(self):
        self.begin(); self.record('6'); self.drain(); prior = self.refresh()
        self.record('5', exec_id='execution-2'); self.drain(); self.failed_refresh(prior=prior)
        self.assertTrue(self.journal._store._quarantined)

    def test_low_precision_cannot_hide_overfill(self):
        self.begin(); self.record('6'); self.drain(); prior = self.refresh()
        with localcontext() as context:
            context.prec = 1
            self.record('5', exec_id='execution-2'); self.drain(); self.failed_refresh(prior=prior)

    def test_qualified_missing_orderref_anchor(self):
        self.begin(); self.open(); self.record(orderRef=None); self.drain()
        self.assertEqual(self.entry().recorded_execution_quantity, 4)

    def test_missing_orderref_without_anchor_never_owned(self):
        self.begin(); self.record(orderRef=None); self.drain(); self.failed_refresh()

    def test_copied_reference_wrong_numeric_id_never_adopted(self):
        self.begin(); self.execution(order_id=99); self.drain()
        self.assertEqual(self.entry().lots, ())

    def test_foreign_execution_never_owned(self):
        self.begin(); self.execution(order_id=99, values={'orderRef': 'another-reference'}); self.drain()
        self.assertEqual(self.entry().recorded_execution_quantity, 0)

    def test_status_lower_bound_separate_from_priced_lots(self):
        self.begin(); self.open(); self.record('3'); self.status(filled=D(5), remaining=D(5)); self.drain()
        e = self.entry()
        self.assertEqual((e.recorded_execution_quantity, e.historical_filled_lower_bound), (3, 5))
        self.assertIn(a._AccountingObligation.MISSING_EXECUTION_PRICES, e.obligations)

    def test_filled_status_does_not_invent_prices(self):
        self.begin(); self.open(); self.status(status='Filled', filled=D(10), remaining=D(0), average=2.0)
        self.drain(); e = self.entry()
        self.assertEqual((e.historical_filled_lower_bound, e.lots, e.recorded_gross_notional), (10, (), 0))
        self.assertIsNone(e.exact_recorded_average_price)

    def test_terminal_partial_does_not_close_lots(self):
        self.begin(); self.open(); self.record('3'); self.status(status='Cancelled', filled=D(3), remaining=D(7))
        self.drain(); e = self.entry()
        self.assertEqual(e.recorded_execution_quantity, 3)
        self.assertIsNone(e.closed_quantity)

    def test_executions_without_terminal_status_remain_owned(self):
        self.begin(); self.record(); self.drain(); e = self.entry()
        self.assertEqual(len(e.lots), 1); self.assertEqual(e.status_observations, ())

    def test_residual_has_no_working_quantity_alias(self):
        self.begin(); e = self.entry()
        self.assertIsNone(e.working_remainder)
        self.assertFalse(hasattr(e, 'current_remaining_quantity'))

    def test_same_event_status_prices_not_combined(self):
        self.begin(); self.status(filled=D(2), remaining=D(8), average=3.0, last=4.0)
        self.status(filled=D(3), remaining=D(7), average=5.0, last=6.0); self.drain()
        first, second = self.entry().status_observations
        self.assertEqual((first.filled, first.remaining, first.average_price, first.last_price), (2, 8, 3, 4))
        self.assertEqual((second.filled, second.remaining, second.average_price, second.last_price), (3, 7, 5, 6))

    def test_open_lifecycle_remains_observation(self):
        self.begin(); self.open(); self.drain(); e = self.entry()
        self.assertIs(e.open_observations[0].status, j._LifecycleStatus.SUBMITTED)
        self.assertIsNone(e.working_remainder)

    def test_identifier_only_denial_has_no_fabricated_payload(self):
        self.build(); self.accounting = a._create_paper_execution_accounting_owner(self.journal)
        with self.coordinator._lock, self.broker._lock:
            self.journal._denied(self.coordinator, 'denied-identifier')
        e = self.entry()
        self.assertIsNone(e.requested_quantity); self.assertIsNone(e.arithmetic_residual)
        self.assertEqual(e.lots, ())

    def test_lot_retains_exact_source_and_contract_identity(self):
        self.begin(); self.record(); self.drain(); lot, = self.entry().lots
        self.assertEqual((lot.client_order_id, lot.order_id, lot.exec_id, lot.con_id, lot.side),
                         (self.order.client_order_id, 10, 'execution-1', 17, 'BOT'))
        self.assertEqual(lot.source, j._CallbackSource(self.journal._session.session_id, 1, 1))


class PriceTests(AccountingFixture):
    def test_gross_value_and_cash_subtotal(self):
        self.begin(); self.record('4', '1.25'); self.drain(); e = self.entry()
        self.assertEqual((e.recorded_gross_notional, e.gross_execution_cash_subtotal), (5, -5))

    def test_weighted_multiple_values(self):
        self.begin(); self.record('2', '1'); self.record('1', '2', 'execution-2'); self.drain(); e = self.entry()
        self.assertEqual(e.recorded_gross_notional, 4)
        self.assertEqual(e.exact_recorded_average_price, a._ExactRatio(4, 3))

    def test_zero_price_is_retained(self):
        self.begin(); self.record('10', '0'); self.drain(); e = self.entry()
        self.assertEqual(e.lots[0].price, 0)
        self.assertEqual(e.exact_recorded_average_price, a._ExactRatio(0, 1))

    def test_high_scale_notional_exact(self):
        self.begin(); self.record('3', '0.12345678901234567890123456789'); self.drain()
        self.assertEqual(self.entry().recorded_gross_notional, D('0.37037036703703703670370370367'))

    def test_low_precision_refresh_is_identical(self):
        self.begin(); self.record('3', '123.456789'); self.drain(); expected = self.refresh()
        with localcontext() as context:
            context.prec = 1
            self.assertEqual(self.refresh(), expected)

    def test_decimal_traps_do_not_round_execution_arithmetic(self):
        from decimal import Inexact, Rounded
        self.begin(); self.record('3', '0.123456789'); self.drain()
        with localcontext() as context:
            context.prec = 1; context.traps[Inexact] = True; context.traps[Rounded] = True
            self.assertEqual(self.entry().recorded_gross_notional, D('0.370370367'))

    def test_no_execution_average_is_unknown(self):
        self.begin(); self.assertIsNone(self.entry().exact_recorded_average_price)

    def test_reference_price_not_substituted(self):
        self.begin(); self.record(price='7.125'); self.drain()
        self.assertEqual(self.entry().lots[0].price, D('7.125'))

    def test_exact_ratio_validates_reduced_positive_denominator(self):
        for numerator, denominator in ((1, 0), (1, -1), (2, 4), (True, 1)):
            with self.subTest(pair=(numerator, denominator)), self.assertRaises(ReadOnlyError):
                a._ExactRatio(numerator, denominator)


class CommissionTests(AccountingFixture):
    def commission_entry(self, amount='0.05'):
        self.begin(); self.record(); self.commission(amount=D(amount)); self.drain()
        return self.entry()

    def test_missing_fee_is_unknown_not_zero(self):
        self.begin(); self.record(); self.drain(); e = self.entry()
        self.assertIsNone(e.known_commission_total)
        self.assertEqual((e.executions_with_known_commission, e.executions_without_known_commission), (0, 1))

    def test_supplied_zero_is_known(self):
        self.assertEqual(self.commission_entry('0').known_commission_total, D(0))

    def test_positive_amount_signed(self):
        self.assertEqual(self.commission_entry('0.05').known_commission_total, D('0.05'))

    def test_negative_rebate_signed(self):
        self.assertEqual(self.commission_entry('-0.05').known_commission_total, D('-0.05'))

    def test_late_commission_updates_only_reported_facts(self):
        self.begin(); self.record(); self.drain(); before = self.refresh()
        self.commission(); self.drain(); after = self.refresh()
        self.assertIsNone(before.entries[0].known_commission_total)
        self.assertEqual(after.entries[0].known_commission_total, D('0.05'))
        self.assertEqual(before.entries[0].recorded_execution_quantity, after.entries[0].recorded_execution_quantity)
        self.assertFalse(after.entries[0].commission_completeness)

    def test_legacy_fees_duplicate_no_double_effect(self):
        self.begin(); self.record(); self.commission(); self.commission('fees'); self.drain()
        self.assertEqual(self.entry().known_commission_total, D('0.05'))

    def test_distinct_executions_known_subtotal_with_missing_report(self):
        self.begin(); self.record('2'); self.record('3', exec_id='execution-2'); self.commission(); self.drain()
        e = self.entry()
        self.assertEqual((e.known_commission_total, e.executions_with_known_commission,
                          e.executions_without_known_commission), (D('0.05'), 1, 1))

    def test_signed_reports_sum_exactly(self):
        self.begin(); self.record('2'); self.record('3', exec_id='execution-2')
        self.commission(amount=D('0.15')); self.commission(exec_id='execution-2', amount=D('-0.05')); self.drain()
        self.assertEqual(self.entry().known_commission_total, D('0.10'))

    def test_all_reports_do_not_prove_finality(self):
        e = self.commission_entry(); self.assertEqual(e.executions_without_known_commission, 0)
        self.assertFalse(e.commission_completeness); self.assertIsNone(e.fee_inclusive_net_cash)

    def test_conflicting_amount_preserves_prior_snapshot(self):
        self.commission_entry(); prior = self.accounting._snapshot
        self.commission(amount=D('0.06')); self.drain(); self.failed_refresh(prior=prior)

    def test_conflicting_currency_preserves_prior_snapshot(self):
        self.commission_entry(); prior = self.accounting._snapshot
        self.commission(currency='EUR'); self.drain(); self.failed_refresh(prior=prior)

    def test_conflicting_report_identity_quarantines(self):
        self.commission_entry(); prior = self.accounting._snapshot
        with self.coordinator._lock, self.broker._lock:
            report = self.of_type(j._CommissionReceived)[0]
            with self.assertRaises(ReadOnlyError):
                self.journal._append_event(replace(report, report_id='different-report'), diagnostic=True)
        self.failed_refresh(prior=prior)

    def test_commission_before_execution_blocks_prefix(self):
        self.begin(); self.commission(); self.drain(); s = self.refresh()
        self.assertIs(s.currentness_at_publication, State.PENDING_CALLBACKS)
        self.assertEqual(s.staged_count, 1); self.assertEqual(s.persistence_cursor, 0)

    def test_lookahead_execution_owned_without_claiming_coverage(self):
        self.begin(); self.commission(exec_id='unresolved-execution'); self.record(); self.drain()
        s = self.refresh()
        self.assertEqual(s.entries[0].recorded_execution_quantity, 4)
        self.assertIs(s.currentness_at_publication, State.PENDING_CALLBACKS)
        self.assertEqual(s.persistence_cursor, 0)


class CurrentnessTests(AccountingFixture):
    def test_current_prefix_independent_of_complete_inventory(self):
        self.filled(); self.assertIs(self.accounting._currentness, State.VERIFIED_PREFIX)
        self.assertFalse(self.accounting._snapshot.entries[0].current_bot_inventory_known)

    def test_undrained_callback_is_pending(self):
        self.begin(); self.record(); s = self.refresh()
        self.assertIs(s.currentness_at_publication, State.PENDING_CALLBACKS)
        self.assertEqual(s.entries[0].recorded_execution_quantity, 0)

    def test_new_callback_invalidates_live_currentness(self):
        self.begin(); prior = self.refresh(); self.record()
        self.assertIs(self.accounting._currentness, State.PENDING_CALLBACKS)
        self.assertIs(self.accounting._snapshot, prior)

    def test_inhibited_journal_still_accounts_historical_facts(self):
        self.begin(); self.record(); self.sync(); self.assertTrue(self.journal._inhibited)
        s = self.refresh(); self.assertEqual(s.entries[0].recorded_execution_quantity, 4)
        self.assertTrue(s.journal_inhibited_at_publication)
        self.assertTrue(self.coordinator._reconciliation_required)

    def test_store_poison_preserves_history(self):
        self.filled(); prior = self.accounting._snapshot
        self.journal._store._poisoned = True; self.failed_refresh(prior=prior)

    def test_store_quarantine_preserves_history(self):
        self.filled(); prior = self.accounting._snapshot
        self.journal._store._quarantined = True; self.failed_refresh(prior=prior)

    def test_closed_store_blocks_refresh(self):
        self.filled(); prior = self.accounting._snapshot
        self.journal._close(); self.failed_refresh(prior=prior)

    def test_persistence_failure_blocks_refresh(self):
        self.filled(); prior = self.accounting._snapshot
        self.journal._PaperDispatchJournalOwner__persistence_failed = True
        self.failed_refresh(prior=prior)

    def test_write_generation_change_blocks_refresh(self):
        self.filled(); prior = self.accounting._snapshot
        self.write._begin_generation(); self.failed_refresh(prior=prior)

    def test_read_generation_change_blocks_refresh(self):
        self.filled(); prior = self.accounting._snapshot
        self.transport._generation += 1; self.failed_refresh(prior=prior)

    def test_lost_receipt_provenance_blocks_refresh(self):
        self.filled(); prior = self.accounting._snapshot
        self.transport._journal_account_association = None; self.failed_refresh(prior=prior)

    def test_sequence_gap_blocks_before_sqlite(self):
        self.begin(); prior = self.refresh(); self.record()
        self.write._callback_run_ends[-1] += 1
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite')) as begin:
            self.failed_refresh(prior=prior)
        begin.assert_not_called()

    def test_progress_number_cannot_adopt_unseen_prefix(self):
        self.begin(); prior = self.refresh()
        with self.coordinator._lock, self.broker._lock:
            self.journal._append_event(j._CallbackProgress(self.journal._session.session_id, 100, 100), diagnostic=True)
        self.failed_refresh(prior=prior)

    def test_retained_history_corruption_rejected(self):
        self.filled(); prior = self.accounting._snapshot
        self.journal._PaperDispatchJournalOwner__history = ()
        self.failed_refresh(prior=prior)

    def test_external_durable_tail_change_during_derivation(self):
        self.begin(); prior = self.refresh(); original = a._derive_accounting
        def derive(evidence):
            candidate = original(evidence)
            self.journal._store._append(j._ReconciliationRequired(j._ReconciliationReason.TRANSPORT_POISON))
            return candidate
        with patch.object(a, '_derive_accounting', derive): self.failed_refresh(prior=prior)

    def test_valid_journal_append_during_refresh_rejected(self):
        self.begin(); prior = self.refresh(); original = a._derive_accounting
        def derive(evidence):
            candidate = original(evidence)
            self.journal._append_event(j._ReconciliationRequired(j._ReconciliationReason.TRANSPORT_POISON), diagnostic=True)
            return candidate
        with patch.object(a, '_derive_accounting', derive): self.failed_refresh(prior=prior)

    def test_failed_refresh_is_not_retried(self):
        self.begin(); self.journal._store._poisoned = True; self.failed_refresh()
        with patch.object(JournalOwner, '_load', side_effect=AssertionError('retry')) as load:
            self.failed_refresh()
        load.assert_not_called()

    def test_callback_capture_continues_during_computation(self):
        self.begin(); prior = self.refresh(); original = a._derive_accounting
        def derive(evidence):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(self.record).result(timeout=5)
            return original(evidence)
        with patch.object(a, '_derive_accounting', derive): self.failed_refresh(prior=prior)
        self.assertEqual(self.write._callback_sequence, 1)

    def test_no_capture_locks_during_sqlite(self):
        self.begin(); original = storage._SQLiteJournalStore._begin; calls = []
        def begin(store, immediate):
            self.assertFalse(self.write._lock._is_owned())
            self.assertFalse(self.write._arrival_lock._is_owned())
            self.assertFalse(self.transport._lifecycle._is_owned())
            self.assertFalse(self.transport._condition._is_owned())
            calls.append(True); return original(store, immediate)
        with patch.object(storage._SQLiteJournalStore, '_begin', begin): self.refresh()
        self.assertGreaterEqual(len(calls), 2)

    def test_callback_thread_does_not_enter_accounting_or_sqlite(self):
        self.begin()
        with patch.object(Owner, '_refresh', side_effect=AssertionError('accounting')) as refresh, \
             patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite')) as begin:
            with ThreadPoolExecutor(max_workers=1) as pool: pool.submit(self.record).result(timeout=5)
        refresh.assert_not_called(); begin.assert_not_called()

    def test_sqlite_corruption_preserves_previous_snapshot(self):
        self.filled(); prior = self.accounting._snapshot
        with closing(sqlite3.connect(self.temporary.name + '/dispatch-1.db')) as connection:
            connection.execute('PRAGMA writable_schema=ON')
            connection.execute("UPDATE sqlite_schema SET sql='invalid schema' WHERE name='records'")
            connection.commit()
        self.failed_refresh(prior=prior)


def interruption_case(seam, kind):
    def test(self):
        self.filled(); prior = self.accounting._snapshot
        error = kind(37) if kind is SystemExit else kind()
        target, name = {
            'before_load': (JournalOwner, '_capture_accounting_evidence'),
            'during_load': (JournalOwner, '_load'),
            'lot': (a, '_ExecutionLot'), 'arithmetic': (a, 'multiply'),
            'candidate': (a, '_PaperExecutionAccountingSnapshot'),
            'revalidation': (JournalOwner, '_publish_accounting_snapshot'),
            'final_identity': (Owner, '_require_candidate'), 'before_publication': (Owner, '_publish')
        }[seam]
        with patch.object(target, name, side_effect=error):
            with self.assertRaises(kind) as caught: self.refresh()
        self.assertIs(caught.exception, error)
        self.assertIs(self.accounting._snapshot, prior)
        self.assertTrue(self.accounting._failed)
        self.assertTrue(self.journal._inhibited)
        self.assertTrue(self.write._requires_reconciliation())
    return test


class InterruptionTests(AccountingFixture):
    pass


for seam in ('before_load', 'during_load', 'lot', 'arithmetic', 'candidate', 'revalidation',
             'final_identity', 'before_publication'):
    for kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
        setattr(InterruptionTests, 'test_' + seam + '_' + kind.__name__, interruption_case(seam, kind))


class BoundaryTests(AccountingFixture):
    def test_published_state_is_independent_of_loaded_objects(self):
        self.filled(); s = self.accounting._snapshot
        loaded = self.journal._load()
        self.assertIsNot(s.entries[0].lots[0].source, loaded.replay.attempts[0].executions[0].source)

    def test_models_and_nested_containers_immutable(self):
        self.filled(); snapshot = self.accounting._snapshot
        for value in (snapshot, snapshot.entries[0], snapshot.entries[0].lots[0],
                      snapshot.entries[0].exact_recorded_average_price):
            with self.subTest(kind=type(value)), self.assertRaises(FrozenInstanceError):
                setattr(value, fields(type(value))[0].name, None)
        self.assertIs(type(snapshot.entries), tuple)
        self.assertIs(type(snapshot.entries[0].lots), tuple)

    def test_repeated_refresh_is_equal_and_not_duplicate_booking(self):
        self.filled(); first = self.accounting._snapshot; second = self.refresh()
        self.assertEqual(first, second); self.assertEqual(len(second.entries[0].lots), 1)

    def test_no_public_methods_or_exports(self):
        import tradingbot_broker
        self.assertEqual([name for name in dir(Owner) if not name.startswith('_')], [])
        self.assertFalse(hasattr(tradingbot_broker, '_PaperExecutionAccountingOwner'))

    def test_refresh_does_not_append_journal_or_invoke_write(self):
        self.begin(); self.record(); self.drain(); history = self.history(); calls = self.write_calls()
        with patch.object(JournalOwner, '_append_event', side_effect=AssertionError('append')), \
             patch.object(paper.PaperTWSTransport, '_dispatch', side_effect=AssertionError('write')):
            self.refresh()
        self.assertEqual(self.history(), history); self.assertEqual(self.write_calls(), calls)

    def test_no_pnl_or_complete_position_claim(self):
        e = self.filled()
        self.assertIsNone(e.current_bot_held_quantity); self.assertIsNone(e.closed_quantity)
        self.assertIsNone(e.realized_trading_pnl); self.assertIsNone(e.unrealized_pnl)
        self.assertIsNone(e.current_marked_exposure); self.assertIsNone(e.fee_inclusive_net_cash)

    def test_authorization_and_barriers_never_restored(self):
        self.begin(); self.record(); self.sync()
        before = (self.broker._paper_authority.status, self.coordinator._reconciliation_required,
                  self.coordinator._broker_state_reconciliation_required, self.write._requires_reconciliation())
        self.refresh()
        self.assertEqual(before, (self.broker._paper_authority.status, self.coordinator._reconciliation_required,
                  self.coordinator._broker_state_reconciliation_required, self.write._requires_reconciliation()))

    def test_mode_unknown_and_snapshot_none(self):
        self.begin(); s = self.refresh()
        self.assertIs(self.journal._header.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.transport._snapshot.account.mode)
        self.assertFalse(s.new_entry_permitted); self.assertFalse(s.recovery_authority)

    def test_restart_quarantine_never_removed(self):
        self.begin(); self.assertTrue(self.refresh().restart_quarantine_required)
        self.assertTrue(self.journal._load().replay.restart_quarantine_required)

    def test_opaque_reprs_and_no_account_data_fields(self):
        self.filled(); s = self.accounting._snapshot
        for value in (self.accounting, s, s.entries[0], s.entries[0].lots[0], s.entries[0].exact_recorded_average_price):
            self.assertNotIn(self.order.client_order_id, repr(value))
            self.assertNotIn('private-account-text', repr(value))
        self.assertFalse(any('account' in f.name and 'accounting' not in f.name for f in fields(a._ExecutionLot)))

    def test_public_broker_still_blocks_order(self):
        self.begin()
        with self.assertRaises(BrokerOperationError): self.broker.place_order(self.order, self.clock())
        self.assertEqual(len(self.write_calls()), 1)

    def test_no_storage_sdk_or_research_owner_in_accounting_source(self):
        source = inspect.getsource(a)
        for forbidden in ('sqlite3', 'placeOrder', 'PortfolioEngine', 'PositionManager', 'datetime.now'):
            self.assertNotIn(forbidden, source)


def storage_failure_case(code):
    def test(self):
        self.filled(); prior = self.accounting._snapshot
        with patch.object(JournalOwner, '_load', side_effect=storage._StorageError(code)) as load:
            self.failed_refresh(prior=prior)
        self.assertEqual(load.call_count, 1)
    return test


for code in (storage._Code.BUSY, storage._Code.STORAGE_FAILURE, storage._Code.COMMIT_OUTCOME_UNKNOWN,
             storage._Code.POISONED, storage._Code.QUARANTINED, storage._Code.CORRUPT,
             storage._Code.INVALID_DATABASE, storage._Code.CLOSED):
    setattr(CurrentnessTests, 'test_storage_failure_' + code.value, storage_failure_case(code))


def unrelated_position_case(quantity):
    def test(self):
        self.filled(); before = self.accounting._snapshot.entries
        snapshot = self.transport._snapshot
        # Account context remains valid; aggregate holdings are never attribution.
        position = Position(self.order.security_id, self.order.symbol, D(quantity), D(9), 'USD')
        self.transport._snapshot = replace(snapshot, account=replace(snapshot.account, positions=(position,)))
        self.assertEqual(self.refresh().entries, before)
    return test


for label, quantity in (('manual', '100'), ('preexisting', '27'), ('other_client', '2')):
    setattr(BoundaryTests, 'test_' + label + '_same_symbol_not_adopted', unrelated_position_case(quantity))


def unknown_field_case(field_name):
    def test(self):
        e = self.filled()
        self.assertIsNone(getattr(e, field_name))
        with self.assertRaises(ValueError): replace(e, **{field_name: D(10)})
    return test


for name in ('current_bot_held_quantity', 'closed_quantity', 'working_remainder',
             'fee_inclusive_net_cash', 'realized_trading_pnl', 'unrealized_pnl', 'current_marked_exposure'):
    setattr(BoundaryTests, 'test_unknown_cannot_be_replaced_' + name, unknown_field_case(name))
