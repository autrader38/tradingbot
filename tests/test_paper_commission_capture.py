"""PRE1 memory-only commission facts; anonymous SDK doubles, no connection."""

import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import inspect
from threading import Event, get_ident
from types import SimpleNamespace as NS
from unittest.mock import patch
import weakref

from tradingbot_broker import ibkr_paper_transport as paper
from tradingbot_broker import paper_dispatch_journal as journal_owner
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker import paper_execution_journal as journal
from tradingbot_broker.readonly_models import AccountMode
from tests.test_paper_dispatch_reservation import ReservationFixture
from tests.test_ibkr_paper_transport import transport


D = Decimal
RECEIPT = datetime(2026, 2, 3, 14, 15, 16, tzinfo=timezone.utc)


class CommissionFixture(ReservationFixture):
    def build(self, **kwargs):
        self.receipt = RECEIPT
        with patch.object(paper, '_commission_receipt_time', lambda: self.receipt):
            return super().build(**kwargs)

    def commission(self, variant='legacy', *, client=None, amount=D('0.05'),
                   exec_id='execution-1', currency='USD', report=None):
        report = report if report is not None else NS(execId=exec_id, currency=currency,
            **{'commission' if variant == 'legacy' else 'commissionAndFees': amount})
        wrapper = (client or self.client).wrapper
        getattr(wrapper, 'commissionReport' if variant == 'legacy' else 'commissionAndFeesReport')(report)
        return report

    def page(self, cursor=0):
        return self.write._evidence_since(self.coordinator, cursor)

    def evidence(self):
        return self.page().events[-1]

    def invalid(self, report, variant='legacy'):
        sequence, size = self.write._callback_sequence, len(self.write._callback_evidence)
        marker = self.write._arrival_sequence
        self.commission(variant, report=report)
        self.assertTrue(self.write._arrival_unusable)
        self.assertEqual(self.write._callback_sequence, sequence)
        self.assertEqual(len(self.write._callback_evidence), size)
        self.assertEqual(self.write._arrival_sequence, marker + 1)


class CaptureTests(CommissionFixture):
    def test_legacy_required_fields_only(self):
        self.build(); self.commission()
        e = self.evidence()
        self.assertIs(type(e), paper._CommissionEvidence)
        self.assertEqual((e.generation, e.sequence, e.exec_id, e.amount, e.currency, e.received_at),
                         (1, 1, 'execution-1', D('0.05'), 'USD', RECEIPT))
        self.assertEqual([f.name for f in fields(e)],
            ['generation', 'sequence', 'exec_id', 'report_id', 'amount', 'currency', 'received_at'])

    def test_fees_required_fields_only(self):
        self.build(); self.commission('fees', amount=D('-0.02'), currency='EUR')
        self.assertEqual((self.evidence().amount, self.evidence().currency), (D('-0.02'), 'EUR'))

    def test_positive_negative_and_supplied_zero(self):
        self.build()
        for value in (D('0.01'), D('-0.01'), D(0), D('-0')):
            with self.subTest(value=value):
                self.receipt += timedelta(microseconds=1)
                self.commission(amount=value)
                self.assertEqual(self.evidence().amount.as_tuple(), value.as_tuple())
        self.assertFalse(self.write._arrival_unusable)

    def test_float_and_integer_use_decimal_text_without_rounding(self):
        self.build()
        for value in (0.1, -0.1, 0, 7, -7):
            with self.subTest(value=value):
                self.commission(amount=value)
                self.assertIs(type(self.evidence().amount), Decimal)
                self.assertEqual(self.evidence().amount, D(str(value)))

    def test_numeric_bounds_accept_exact_endpoints(self):
        self.build()
        for value in (D((1, (7,) * 64, -64)), D((0, (1,), 64))):
            with self.subTest(value=value):
                self.commission(amount=value)
                self.assertEqual(self.evidence().amount.as_tuple(), value.as_tuple())

    def test_receipt_is_captured_under_arrival_without_outer_locks(self):
        self.build(); called = []
        def clock():
            self.assertTrue(self.write._arrival_lock._is_owned())
            self.assertFalse(self.coordinator._lock._is_owned())
            self.assertFalse(self.broker._lock._is_owned())
            self.assertFalse(self.write._lock._is_owned())
            called.append(True)
            return RECEIPT
        self.write._commission_clock = clock
        self.commission()
        self.assertEqual(called, [True])

    def test_clock_is_captured_not_looked_up_on_each_callback(self):
        self.build()
        with patch.object(paper, '_commission_receipt_time', side_effect=AssertionError('late clock lookup')):
            self.commission()
        self.assertEqual(self.evidence().received_at, RECEIPT)

    def test_aware_local_receipt_normalizes_to_utc(self):
        self.build()
        self.receipt = RECEIPT.astimezone(timezone(timedelta(hours=5, minutes=30)))
        self.commission()
        self.assertEqual(self.evidence().received_at, RECEIPT)
        self.assertIs(self.evidence().received_at.tzinfo, timezone.utc)

    def test_transport_default_clock_is_aware_local_time(self):
        stamp = paper._commission_receipt_time()
        self.assertIs(type(stamp), datetime)
        self.assertIs(stamp.tzinfo, timezone.utc)

    def test_before_execution_is_staged_without_attribution(self):
        self.build(); self.commission()
        self.assertFalse(self.coordinator._executions)
        self.assertFalse(hasattr(self.evidence(), 'client_order_id'))
        self.execution(order_id=9)
        self.assertEqual([type(e) for e in self.page().events],
                         [paper._CommissionEvidence, paper._ExecutionEvidence])

    def test_after_execution_is_staged_without_attribution(self):
        self.build(); self.execution(order_id=9); self.commission()
        self.assertEqual([type(e) for e in self.page().events],
                         [paper._ExecutionEvidence, paper._CommissionEvidence])
        self.assertFalse(self.coordinator._executions)

    def test_common_sequence_for_all_four_evidence_kinds(self):
        self.build(); self.open(order_id=9); self.commission()
        self.status(order_id=9); self.execution(order_id=9); self.commission('fees')
        self.assertEqual([e.sequence for e in self.page().events], [1, 2, 3, 4, 5])
        self.assertEqual(self.write._callback_sequence, 5)

    def test_generation_change_never_resets_sequence_or_evidence(self):
        self.build(); self.commission(); self.write._begin_generation()
        self.client = self.paper_api.clients[-1]
        self.commission('fees')
        self.assertEqual([(e.generation, e.sequence) for e in self.page().events], [(1, 1), (2, 2)])

    def test_stale_generation_is_rejected_before_report_or_clock_access(self):
        self.build(); old = self.client; self.write._begin_generation()
        class Unsafe:
            @property
            def execId(raw): raise AssertionError('stale payload accessed')
        self.write._commission_clock = lambda: (_ for _ in ()).throw(AssertionError('stale clock'))
        before = self.write._arrival_sequence
        self.commission(client=old, report=Unsafe())
        self.assertEqual(self.write._arrival_sequence, before)
        self.assertEqual(self.write._callback_sequence, 0)

    def test_closed_generation_is_rejected_without_payload_access(self):
        self.build(); self.write.disconnect()
        self.write._commission_clock = lambda: (_ for _ in ()).throw(AssertionError('closed clock'))
        self.commission(report=object())
        self.assertEqual(self.write._callback_sequence, 0)

    def test_commission_does_not_change_numeric_id_floor(self):
        self.build(); before = self.write._lifetime_order_floor
        self.commission()
        self.assertEqual(self.write._lifetime_order_floor, before)

    def test_capture_before_coordinator_binding_is_still_sanitized(self):
        write, api, client = transport()
        self.addCleanup(write.disconnect)
        client.wrapper.commissionReport(NS(execId='execution-early', commission=-0.1, currency='GBP'))
        self.assertIs(type(write._callback_evidence[0]), paper._CommissionEvidence)
        self.assertEqual(write._callback_sequence, 1)
        self.assertIsNone(write._dispatch_coordinator_owner)

    def test_synchronous_callback_during_sdk_is_only_in_memory(self):
        self.build(); original = self.client.placeOrder
        def invoke(*args):
            self.commission()
            self.assertTrue(self.write._write_invocation_started)
            original(*args)
        self.client.placeOrder = invoke
        self.invoke()
        self.assertEqual(len(self.write_calls()), 1)
        self.assertIs(type(self.evidence()), paper._CommissionEvidence)
        history = self.journal._load().records
        self.assertTrue(any(type(r.payload) is journal._SdkReturnedNormally for r in history))
        self.assertFalse(any(type(r.payload) in (journal._CommissionReceived, journal._CallbackProgress) for r in history))

    def test_after_sdk_return_capture_preserves_pending_barrier(self):
        self.build(); self.invoke(); self.commission()
        self.assertTrue(self.coordinator._pending_confirmation)
        self.assertEqual(self.coordinator._callback_cursor, 0)
        self.assertFalse(self.coordinator._executions)

    def test_existing_pending_sync_fails_closed_without_commission_ack(self):
        self.build(); self.invoke(); self.commission()
        self.assert_reconciliation(self.sync())
        self.assertEqual(self.coordinator._callback_cursor, 0)
        self.assertEqual(len(self.page().events), 1)


class IdentityLedgerTests(CommissionFixture):
    def test_variants_share_economic_fingerprint(self):
        self.build(); self.commission(); first = self.evidence()
        self.receipt += timedelta(seconds=1)
        self.commission('fees'); second = self.evidence()
        self.assertEqual(first.report_id, second.report_id)
        self.assertEqual(paper._commission_economic_identity(first), paper._commission_economic_identity(second))
        self.assertNotEqual(paper._evidence_identity(first), paper._evidence_identity(second))

    def test_fingerprint_has_versioned_typed_canonical_preimage(self):
        self.build(); self.commission(amount=D('0.0500'))
        self.assertRegex(self.evidence().report_id, r'^commission-[0-9a-f]{64}$')
        # Fixed vector locks the framing, numeric canonicalization and domain.
        self.assertEqual(self.evidence().report_id,
            'commission-29c3449aa1fac6fccf9ef2f5fb368b389415d48a0b652e75fc5d8c202ddcb9ca')

    def test_fingerprint_independent_of_sequence_time_and_transport(self):
        self.build(); self.commission(); expected = self.evidence().report_id
        self.receipt += timedelta(days=1); self.status(order_id=9); self.commission('fees')
        self.assertEqual(self.evidence().report_id, expected)
        write, api, client = transport()
        self.addCleanup(write.disconnect)
        client.wrapper.commissionAndFeesReport(NS(execId='execution-1', commissionAndFees=0.05, currency='USD'))
        self.assertEqual(write._callback_evidence[0].report_id, expected)

    def test_equal_numeric_encodings_and_signed_zero_share_fingerprint(self):
        self.build()
        for values in ((D('1'), D('1.00'), 1.0, 1), (D('0'), D('-0'), 0.0, -0.0)):
            ids = []
            for value in values:
                self.receipt += timedelta(microseconds=1)
                self.commission(amount=value); ids.append(self.evidence().report_id)
            self.assertEqual(len(set(ids)), 1)

    def test_hostile_decimal_context_cannot_round_amount_or_fingerprint(self):
        self.build(); exact = D('-123456789.01234567890123456789')
        expected = paper._commission_report_id('execution-1', exact, 'USD')
        with localcontext() as context:
            context.prec = 1
            self.commission(amount=exact)
        self.assertEqual(self.evidence().amount.as_tuple(), exact.as_tuple())
        self.assertEqual(self.evidence().report_id, expected)

    def test_changed_amount_currency_and_execution_remain_distinguishable(self):
        self.build(); ids = []
        for kwargs in ({}, {'amount': D('0.06')}, {'currency': 'EUR'}, {'exec_id': 'execution-2'}):
            self.commission(**kwargs); ids.append(self.evidence().report_id)
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(len(self.write._callback_evidence), 4)

    def test_adjacent_source_duplicates_preserve_every_sequence_span(self):
        self.build(); self.commission(); self.commission('fees'); self.commission()
        self.assertEqual(len(self.write._callback_evidence), 1)
        self.assertEqual([e.sequence for e in self.page().events], [1, 2, 3])
        self.assertTrue(all(e.received_at == RECEIPT for e in self.page().events))

    def test_distinct_receipt_instants_are_not_invented_by_span_expansion(self):
        self.build(); self.commission(); self.receipt += timedelta(microseconds=1); self.commission()
        events = self.page().events
        self.assertEqual(len(self.write._callback_evidence), 2)
        self.assertEqual([e.received_at for e in events], [RECEIPT, self.receipt])
        self.assertEqual(events[0].report_id, events[1].report_id)

    def test_interleaved_conflicting_reports_are_all_retained(self):
        self.build(); self.commission(); self.commission(amount=D('-0.01')); self.commission('fees')
        events = self.page().events
        self.assertEqual([e.amount for e in events], [D('0.05'), D('-0.01'), D('0.05')])
        self.assertEqual(events[0].report_id, events[2].report_id)
        self.assertNotEqual(events[0].report_id, events[1].report_id)
        self.assertFalse(self.coordinator._executions)

    def test_fetch_never_acknowledges_clears_or_touches_journal(self):
        self.build(); self.commission(); original = self.page()
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite')):
            self.assertEqual(self.page(), original)
            self.assertEqual(self.page(), original)
        self.assertEqual(self.coordinator._callback_cursor, 0)
        self.assertEqual(len(self.write._callback_evidence), 1)

    def test_bounded_duplicate_paging_does_not_acknowledge_upper_watermark(self):
        self.build()
        for _ in range(300): self.commission()
        pages = [self.page(cursor) for cursor in (0, 128, 256)]
        self.assertEqual([len(p.events) for p in pages], [128, 128, 44])
        self.assertEqual([p.through_sequence for p in pages], [300, 300, 300])
        self.assertEqual([e.sequence for p in pages for e in p.events], list(range(1, 301)))
        self.assertEqual(len(self.write._callback_evidence), 1)
        self.assertEqual(self.coordinator._callback_cursor, 0)

    def test_distinct_receipt_capacity_overflow_permanently_inhibits(self):
        self.build()
        for _ in range(paper._EVIDENCE_CAPACITY + 1):
            self.receipt += timedelta(microseconds=1); self.commission()
        self.assertEqual(len(self.write._callback_evidence), 128)
        self.assertEqual(self.write._callback_sequence, 129)
        self.assertTrue(self.write._requires_reconciliation())
        self.assertEqual(self.page(128).events, ())
        self.assertEqual(self.coordinator._callback_cursor, 0)
        self.assert_reconciliation(self.sync())
        self.write._begin_generation()
        self.assertTrue(self.write._requires_reconciliation())

    def test_distinct_economic_report_capacity_is_also_bounded(self):
        self.build()
        for number in range(129): self.commission(exec_id=f'execution-{number}')
        self.assertEqual(len(self.write._callback_evidence), 128)
        self.assertTrue(self.write._requires_reconciliation())


class MalformedTests(CommissionFixture):
    def test_missing_malformed_and_unbounded_amounts_fail_closed(self):
        self.build()
        for value in (D('NaN'), D('sNaN'), D('Infinity'), D('-Infinity'), float('nan'),
                      float('inf'), None, True, '0.05', object(), D('1e65'), D('1e-65'),
                      D('1' * 65), 1.7976931348623157e308):
            with self.subTest(kind=type(value).__name__):
                self.invalid(NS(execId='execution-1', commission=value, currency='USD'))
        self.invalid(NS(execId='execution-1', currency='USD'))

    def test_exec_identifier_must_be_exact_bounded_and_nonempty(self):
        self.build()
        for value in (None, '', True, 17, 'x'*101, 'arbitrary message', 'private\ntext', 'é'):
            with self.subTest(kind=type(value).__name__):
                self.invalid(NS(execId=value, commission=0.05, currency='USD'))
        self.invalid(NS(commission=0.05, currency='USD'))

    def test_currency_must_be_a_nonempty_ascii_currency_code(self):
        self.build()
        for value in (None, '', True, 1, 'usd', 'US', 'USDD', 'U D', 'U\nD', 'ÜSD', 'account-1'):
            with self.subTest(kind=type(value).__name__):
                self.invalid(NS(execId='execution-1', commission=0.05, currency=value))

    def test_no_variant_amount_fallback_or_fabricated_zero(self):
        self.build()
        self.invalid(NS(execId='execution-1', commissionAndFees=0.05, currency='USD'))
        self.invalid(NS(execId='execution-1', commission=0.05, currency='USD'), 'fees')
        self.invalid(NS(execId='execution-1', commission=None, commissionAndFees=0.05, currency='USD'))
        self.invalid(NS(execId='execution-1', currency='USD'), 'fees')

    def test_naive_or_invalid_receipt_never_becomes_evidence(self):
        self.build()
        for value in (RECEIPT.replace(tzinfo=None), None, 'now', 1):
            with self.subTest(kind=type(value).__name__):
                self.receipt = value
                self.invalid(NS(execId='execution-1', commission=0.05, currency='USD'))

    def test_malformed_after_sdk_boundary_latches_reconciliation(self):
        self.build(); self.invoke()
        self.invalid(NS(execId='execution-1', commission=D('NaN'), currency='USD'))
        self.assertTrue(self.write._requires_reconciliation())
        self.assert_reconciliation(self.sync())


class RaceTests(CommissionFixture):
    def test_commission_between_reservation_and_commitment_blocks_sdk(self):
        self.prepare(lambda owner, reservation: self.commission())
        self.consumed_without_invocation(self.invoke())
        self.assertEqual(self.evidence().sequence, self.reservation.callback_watermark + 1)

    def test_preexisting_commission_has_no_invented_numeric_id(self):
        self.build(); self.commission()
        # C3C4D: capture still assigns no numeric ID, and an unattributed,
        # undurable commission now prohibits even preliminary entry reservation.
        self.invoke()
        self.assertEqual(len(self.write_calls()), 0)
        self.assertFalse(hasattr(self.evidence(), 'order_id'))
        self.assertEqual(self.write._lifetime_order_floor, 10)

    def blocked(self, *, malformed=False, interruption=None):
        announced, done = Event(), Event()
        dispatch_thread = get_ident(); futures = []
        pool = ThreadPoolExecutor(max_workers=1); self.addCleanup(pool.shutdown)
        def action(owner, reservation):
            lock = self.write._arrival_lock
            class Announce:
                def __enter__(wrapper): lock.acquire(); return wrapper
                def __exit__(wrapper, *args):
                    lock.release()
                    if get_ident() != dispatch_thread: announced.set()
                def _is_owned(wrapper): return lock._is_owned()
            self.write._arrival_lock = Announce()
            if interruption is not None:
                def clock(): raise interruption
                self.write._commission_clock = clock
            def callback():
                try: self.commission(amount=D('NaN') if malformed else D('0.05'))
                finally: done.set()
            futures.append(pool.submit(callback))
            self.assertTrue(announced.wait(5))
            if interruption is None: self.assertFalse(done.is_set())
            self.assertGreater(self.write._arrival_sequence, reservation.arrival_marker)
        self.prepare(action)
        self.consumed_without_invocation(self.invoke())
        if interruption is None:
            futures[0].result(timeout=5)
        else:
            with self.assertRaises(type(interruption)) as caught: futures[0].result(timeout=5)
            self.assertIs(caught.exception, interruption)
            self.assertTrue(self.write._requires_reconciliation())
        self.assertTrue(done.is_set())
        self.assertEqual(len(self.write_calls()), 0)

    def test_valid_commission_announced_before_blocked_bookkeeping(self): self.blocked()
    def test_malformed_commission_announced_before_blocked_bookkeeping(self): self.blocked(malformed=True)
    def test_keyboard_interrupt_announced_before_deferred_bookkeeping(self): self.blocked(interruption=KeyboardInterrupt())
    def test_system_exit_announced_before_deferred_bookkeeping(self): self.blocked(interruption=SystemExit(37))
    def test_generator_exit_announced_before_deferred_bookkeeping(self): self.blocked(interruption=GeneratorExit())

    def test_post_commit_commission_cannot_allow_another_invocation(self):
        self.build(); original = self.client.placeOrder
        def invoke(*args): self.commission(); original(*args)
        self.client.placeOrder = invoke
        self.invoke(); self.assert_reconciliation(self.sync())
        self.assert_blocked(reconcile=True)
        self.assertEqual(len(self.write_calls()), 1)


class InterruptionTests(CommissionFixture):
    def interrupt(self, interruption):
        for after_sdk in (False, True):
            for seam in ('clock', 'exec_id', 'amount', 'currency'):
                with self.subTest(after_sdk=after_sdk, seam=seam):
                    self.build()
                    if after_sdk: self.invoke()
                    report = NS(execId='execution-1', commission=D('0.05'), currency='USD')
                    if seam == 'clock':
                        def clock(): raise interruption
                        self.write._commission_clock = clock
                    else:
                        class Unsafe: pass
                        field = {'exec_id': 'execId', 'amount': 'commission', 'currency': 'currency'}[seam]
                        def extract(raw): raise interruption
                        setattr(Unsafe, field, property(extract))
                        for name, value in report.__dict__.items():
                            if name != field: setattr(Unsafe, name, value)
                        report = Unsafe()
                    with self.assertRaises(type(interruption)) as caught: self.commission(report=report)
                    self.assertIs(caught.exception, interruption)
                    self.assertTrue(self.write._arrival_unusable)
                    self.assertTrue(self.write._requires_reconciliation())
                    self.assertEqual(self.write._callback_sequence, 0)
                    self.assertEqual(len(self.write._callback_evidence), 0)
                    self.assertEqual(len(self.write_calls()), int(after_sdk))

    def test_keyboard_interrupt_identity_and_safety(self): self.interrupt(KeyboardInterrupt())
    def test_system_exit_identity_and_safety(self): self.interrupt(SystemExit(37))
    def test_generator_exit_identity_and_safety(self): self.interrupt(GeneratorExit())


class BoundaryTests(CommissionFixture):
    def test_worker_callback_never_touches_sqlite_journal_or_coordinator(self):
        self.build()
        history = self.journal._load().records
        with patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite')) as begin, \
             patch.object(journal_owner._PaperDispatchJournalOwner, '_append_event', side_effect=AssertionError('journal')) as append, \
             patch.object(type(self.coordinator), '_mark_reconciliation', side_effect=AssertionError('coordinator')) as coordinator:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(self.commission).result(timeout=5)
                pool.submit(self.commission, 'fees').result(timeout=5)
            self.page()
        begin.assert_not_called(); append.assert_not_called(); coordinator.assert_not_called()
        self.assertEqual(self.journal._load().records, history)
        self.assertFalse(self.write_calls())

    def test_report_and_sensitive_unneeded_fields_are_never_retained_or_read(self):
        self.build()
        class Report:
            execId = 'execution-1'; commission = D('-0.05'); currency = 'USD'
            @property
            def account(raw): raise AssertionError('sensitive field read')
            @property
            def realizedPNL(raw): raise AssertionError('unused financial field read')
            def __repr__(raw): raise AssertionError('raw repr read')
        report = Report(); ref = weakref.ref(report)
        self.commission(report=report); del report
        self.assertIsNone(ref())
        self.assertIs(type(self.evidence().amount), Decimal)
        self.assertEqual(repr(self.evidence()), '<SanitizedPaperCallbackEvidence>')

    def test_evidence_is_frozen_slotted_and_contains_only_primitives(self):
        self.build(); report = self.commission(); evidence = self.evidence()
        report.execId = 'changed'; report.commission = 99; report.currency = 'EUR'
        self.assertEqual((evidence.exec_id, evidence.amount, evidence.currency), ('execution-1', D('0.05'), 'USD'))
        with self.assertRaises(FrozenInstanceError): evidence.amount = D(99)
        self.assertFalse(hasattr(evidence, '__dict__'))
        self.assertEqual([type(getattr(evidence, f.name)) for f in fields(evidence)],
                         [int, int, str, str, Decimal, str, datetime])

    def test_private_callback_helpers_have_no_journal_storage_imports(self):
        tree = ast.parse(inspect.getsource(paper))
        imports = [node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any('journal' in name or 'sqlite' in name for name in imports))
        for function in (paper._copy_commission, paper._commission_receipt_time, paper._commission_report_id):
            source = inspect.getsource(function)
            for forbidden in ('_SQLiteJournalStore', '_CommissionReceived', '_CallbackProgress', '_append_event'):
                self.assertNotIn(forbidden, source)

    def test_account_mode_and_public_readonly_boundaries_unchanged(self):
        self.build(); self.commission()
        self.assertIs(self.write.account_mode, AccountMode.UNKNOWN)
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.broker.account_summary().mode)
        self.assertFalse(hasattr(self.write, 'connect'))
        self.assertFalse(hasattr(self.write, 'place_order'))
        self.assertFalse(hasattr(self.evidence(), 'authority'))
        self.assertNotIn(self.evidence().exec_id, repr(self.evidence()))
