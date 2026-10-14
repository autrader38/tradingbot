"""PRE4 pure evidence: canonical anonymous V3 fixtures, never real certification."""

import ast
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from decimal import Decimal as D, Inexact, Rounded, localcontext
import inspect
from pathlib import Path
import unittest
from unittest.mock import patch

from tradingbot_broker import paper_decision_status_evidence as e
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker import paper_dispatch_journal as producer
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests import test_paper_execution_journal as f
from tests import test_paper_v2_callback_coverage_contract as v2
from tests import test_paper_decision_boundary_v3_contract as v3


Outcome = e._DecisionStatusOutcome
Selector = e._QualifiedStatusIdentity
Result = e._DecisionStatusEvidence
KNOWN = j._QualifiedStatusSourceQuality.SUPPLIED
UNAVAILABLE = j._QualifiedStatusSourceQuality.UNAVAILABLE
NAMES = j._QUALIFIED_STATUS_FIELDS
VALUES = dict(zip(NAMES, (10, 'Submitted', D('0'), D('10'), D('0'), 77, 0, D('0'), 2)))
OTHER = 'session-' + '2' * 32


def identity(**changes):
    return replace(Selector(f.SID, 3, 10, 2), **changes)


def status(sequence=1, *, absent=(), unavailable=(), legacy=False, sid=f.SID,
           generation=3, **changes):
    values = dict(VALUES, **changes)
    quality = []
    for name in NAMES:
        if name in absent or name in unavailable:
            values[name] = None; quality.append(UNAVAILABLE)
        elif legacy:
            quality.append(j._QualifiedStatusSourceQuality.LEGACY_AMBIGUOUS
                if name in j._QUALIFIED_STATUS_IDS | j._QUALIFIED_STATUS_PRICES and values[name] == 0
                else j._QualifiedStatusSourceQuality.LEGACY_DECODED)
        else: quality.append(KNOWN)
    presence = j._QualifiedStatusPresence(*quality,
        None if legacy else tuple(n for n in NAMES if n not in absent))
    observation = j._QualifiedStatusObservation(j._QualifiedStatusProtocol.LEGACY if legacy
        else j._QualifiedStatusProtocol.PROTOBUF, *(values[n] for n in NAMES), presence)
    return j._QualifiedOrderStatusObserved(f.source(sequence, sid, generation), observation)


def boundary(n=0, offset=0, *, sid=f.SID, generation=3):
    return j._QualifiedDecisionBoundaryObserved(sid, generation,
                                               f.AT + timedelta(seconds=offset), n)


def evidence(*events):
    h = v3.header()
    records = f.records((f.session(), *events), h)
    return j._replay_v3_decision_boundary_evidence(h, records)


def filler(n): return v2.untracked(n)


def evaluate(source, selector=None, index=0):
    return e._evaluate_decision_status(source, source.boundary_records[index],
                                       identity() if selector is None else selector)


class PureCase(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.create_connection',
                     'socket.getaddrinfo', 'sqlite3.connect', 'builtins.open', 'pathlib.Path.open',
                     'time.time', 'time.monotonic'):
            self.enterContext(patch(name, side_effect=AssertionError('pure evaluation only')))
        self.enterContext(patch.object(producer, '_decision_utc_now',
                                       side_effect=AssertionError('producer clock forbidden')))
        self.enterContext(patch.object(producer._PaperDispatchJournalOwner, '_append_event',
                                       side_effect=AssertionError('journal append forbidden')))
        self.enterContext(patch.object(producer._PaperDispatchJournalOwner, '_authenticate',
                                       side_effect=AssertionError('authentication forbidden')))

    def invalid(self, function, *args, **kwargs):
        with self.assertRaises(ReadOnlyError) as caught: function(*args, **kwargs)
        self.assertEqual(caught.exception.code, e._ERROR)


class SelectionTests(PureCase):
    def test_empty_zero_frontier_no_status_is_not_absence_proof(self):
        result = evaluate(evidence(boundary()))
        self.assertIs(result.outcome, Outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)
        self.assertIsNone(result.observation); self.assertIsNone(result.selected_record)
        self.assertEqual(result.through_sequence, 0)

    def test_one_status_at_exact_frontier(self):
        data = evidence(status(), boundary(1)); result = evaluate(data)
        self.assertIs(result.outcome, Outcome.SUPPORTED_AT_DECISION_BOUNDARY)
        self.assertIs(result.boundary, data.boundary_records[0])
        self.assertIs(result.selected_record, data.records[1])
        self.assertIs(result.observation, data.records[1].payload.observation)
        self.assertIs(result.decision_at, data.boundary_records[0].payload.decision_at)
        self.assertEqual((result.selected_sequence, result.through_sequence), (1, 1))
        self.assertIsNone(result.next_status_sequence)

    def test_status_remains_latest_through_later_covered_frontier(self):
        data = evidence(status(), *(filler(n) for n in range(2, 6)), boundary(5))
        result = evaluate(data)
        self.assertEqual((result.selected_sequence, result.through_sequence), (1, 5))
        self.assertIsNone(result.next_status_sequence)
        self.assertIn('NO_APPLICABILITY_BEYOND_FRONTIER', result.limitations)

    def test_half_open_sequence_selection_at_start_before_next_and_at_next(self):
        data = evidence(status(), boundary(1), filler(2), filler(3), boundary(3, 1),
                        status(4, status='Filled'), boundary(4, 2), filler(5), boundary(5, 3))
        self.assertEqual([evaluate(data, index=i).selected_sequence for i in range(4)], [1, 1, 4, 4])
        self.assertEqual([evaluate(data, index=i).observation.status for i in range(4)],
                         ['Submitted', 'Submitted', 'Filled', 'Filled'])
        self.assertTrue(all(evaluate(data, index=i).next_status_sequence is None for i in range(4)))

    def test_four_sequential_observations_remain_separate(self):
        events = []
        for n, value in enumerate(('Submitted', 'Submitted', 'Cancelled', 'Submitted'), 1):
            events.extend((status(n, status=value), boundary(n, n)))
        data = evidence(*events)
        self.assertEqual([evaluate(data, index=i).selected_sequence for i in range(4)], [1, 2, 3, 4])

    def test_callback_order_wins_over_journal_record_order(self):
        data = evidence(status(3, status='Filled'), status(1), status(2, status='Cancelled'), boundary(3))
        result = evaluate(data)
        self.assertEqual(result.selected_sequence, 3)
        self.assertEqual(result.selected_record.sequence, 2)
        self.assertEqual(result.observation.status, 'Filled')

    def test_unrelated_order_and_client_are_ignored(self):
        data = evidence(status(), status(2, order_id=11), status(3, client_id=3), boundary(3))
        self.assertEqual(evaluate(data).selected_sequence, 1)

    def test_other_session_is_separate_stream(self):
        data = evidence(status(), f.session(OTHER, generation=4),
                        status(1, sid=OTHER, generation=4, status='Filled'), boundary(1))
        self.assertEqual(evaluate(data).observation.status, 'Submitted')

    def test_no_status_before_first_qualified_occurrence(self):
        data = evidence(boundary(), status(), boundary(1, 1))
        earlier = evaluate(data)
        self.assertIs(earlier.outcome, Outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)
        self.assertIsNone(earlier.observation)
        self.assertEqual(evaluate(data, index=1).selected_sequence, 1)

    def test_ordinary_status_does_not_fill_missing_qualified_status(self):
        data = evidence(*f.prefix()[1:], f.status(1), boundary(1))
        self.assertIs(evaluate(data).outcome, Outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)

    def test_open_execution_and_commission_history_never_substitute_status(self):
        data = evidence(*v2.mixed()[1:], boundary(8))
        self.assertIs(evaluate(data).outcome, Outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)

    def test_unknown_and_absent_status_are_preserved_as_source_observations(self):
        for event in (status(status='UNKNOWN'), status(absent={'status'})):
            result = evaluate(evidence(event, boundary(1)))
            self.assertIs(result.outcome, Outcome.SUPPORTED_AT_DECISION_BOUNDARY)
            self.assertIs(result.observation, event.observation)

    def test_generation_is_not_carried_to_new_session(self):
        data = evidence(status(), f.session(OTHER, generation=4), boundary(0, sid=OTHER, generation=4))
        result = evaluate(data, identity(session_id=OTHER, write_generation=4))
        self.assertIs(result.outcome, Outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)


class StabilityTests(PureCase):
    def test_later_status_keeps_entire_earlier_result_structurally_unchanged(self):
        first = evidence(status(), boundary(1)); before = evaluate(first)
        h = first.replay.header
        later = f.records((status(2, status='Filled'), boundary(2, 1)), h)
        later = tuple(replace(r, sequence=len(first.records) + n) for n, r in enumerate(later, 1))
        after_data = j._replay_v3_decision_boundary_evidence(h, first.records + later)
        after = evaluate(after_data)
        self.assertEqual(after, before)
        self.assertIs(after.selected_record, before.selected_record)
        self.assertIs(after.boundary, before.boundary)
        self.assertIsNone(after.next_status_sequence)
        self.assertEqual(evaluate(after_data, index=1).selected_sequence, 2)

    def test_later_identity_conflict_cannot_retroactively_make_earlier_result_ambiguous(self):
        data = evidence(status(), boundary(1), status(2, perm_id=88), boundary(2, 1))
        self.assertIs(evaluate(data).outcome, Outcome.SUPPORTED_AT_DECISION_BOUNDARY)
        self.assertIs(evaluate(data, index=1).outcome, Outcome.AMBIGUOUS_IDENTITY)

    def test_same_frontier_later_time_same_selected_occurrence(self):
        data = evidence(status(), boundary(1), boundary(1, 1))
        first, second = evaluate(data), evaluate(data, index=1)
        self.assertIs(first.selected_record, second.selected_record)
        self.assertEqual(first.through_sequence, second.through_sequence)
        self.assertEqual(first.outcome, second.outcome)
        self.assertLess(first.decision_at, second.decision_at)

    def test_repeated_evaluation_has_no_mutation_or_nondeterminism(self):
        data = evidence(status(), boundary(1))
        snapshot = (data.records, data.boundary_records, data.replay)
        self.assertEqual(evaluate(data), evaluate(data))
        self.assertEqual(snapshot, (data.records, data.boundary_records, data.replay))

    def test_later_untracked_record_and_progress_cannot_change_frozen_result(self):
        data = evidence(status(), boundary(1), filler(2), j._CallbackProgress(f.SID, 2, 2))
        result = evaluate(data)
        self.assertEqual(len(result.prefix.records), 3)
        self.assertEqual(result.prefix.records[-1], data.boundary_records[0])
        self.assertEqual(result.through_sequence, 1)


class IdentityTests(PureCase):
    def test_no_fuzzy_or_reference_only_selector(self):
        self.assertEqual(tuple(f.name for f in fields(Selector)),
                         ('session_id', 'write_generation', 'order_id', 'client_id', 'perm_id'))
        for key in ('symbol', 'quantity', 'price', 'order_ref', 'strategy', 'at'):
            with self.assertRaises(TypeError): Selector(f.SID, 3, 10, 2, **{key: 'arbitrary'})
        data = evidence(status(), boundary(1))
        self.invalid(e._evaluate_decision_status, data, data.records[-1], {'order_ref': 'entry-1'})

    def test_all_512_protobuf_presence_sets_preserve_source_without_manufacturing_ids(self):
        for mask in range(512):
            absent = {name for i, name in enumerate(NAMES) if not mask & (1 << i)}
            event = status(absent=absent); result = evaluate(evidence(event, boundary(1)))
            supported = 'order_id' not in absent and 'client_id' not in absent
            self.assertIs(result.outcome, Outcome.SUPPORTED_AT_DECISION_BOUNDARY if supported
                          else Outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)
            if supported:
                self.assertIs(result.observation, event.observation)
                self.assertEqual(result.observation.presence.wire_supplied, tuple(n for n in NAMES if n not in absent))
            else: self.assertIsNone(result.observation)

    def test_optional_perm_exact_qualifier_and_mismatch(self):
        data = evidence(status(), boundary(1))
        self.assertIs(evaluate(data, identity(perm_id=77)).outcome, Outcome.SUPPORTED_AT_DECISION_BOUNDARY)
        self.assertIs(evaluate(data, identity(perm_id=88)).outcome, Outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)

    def test_conflicting_supplied_zero_and_positive_perm_do_not_collapse(self):
        result = evaluate(evidence(status(perm_id=0), status(2, perm_id=77), boundary(2)))
        self.assertIs(result.outcome, Outcome.AMBIGUOUS_IDENTITY)

    def test_conflicting_known_perms_never_choose_latest_even_with_explicit_qualifier(self):
        data = evidence(status(), status(2, perm_id=88), boundary(2))
        for perm in (None, 77, 88):
            result = evaluate(data, identity(perm_id=perm))
            self.assertIs(result.outcome, Outcome.AMBIGUOUS_IDENTITY)
            self.assertIsNone(result.selected_record); self.assertIsNone(result.observation)

    def test_later_missing_perm_cannot_inherit_earlier_perm(self):
        data = evidence(status(), status(2, absent={'perm_id'}), boundary(2))
        exact = evaluate(data, identity(perm_id=77))
        self.assertIs(exact.outcome, Outcome.AMBIGUOUS_IDENTITY)
        base = evaluate(data)
        self.assertEqual(base.selected_sequence, 2); self.assertIsNone(base.observation.perm_id)

    def test_unknown_perm_without_any_qualified_match_does_not_invent_identity(self):
        result = evaluate(evidence(status(absent={'perm_id'}), boundary(1)), identity(perm_id=77))
        self.assertIs(result.outcome, Outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)

    def test_no_protocol_ranking_when_both_numeric_identities_are_known(self):
        for legacy in (True, False):
            data = evidence(status(legacy=not legacy), status(2, legacy=legacy), boundary(2))
            result = evaluate(data)
            self.assertEqual(result.selected_sequence, 2)
            self.assertIs(result.observation.protocol,
                j._QualifiedStatusProtocol.LEGACY if legacy else j._QualifiedStatusProtocol.PROTOBUF)

    def test_client_identity_does_not_imply_placing_client_or_ownership(self):
        data = evidence(status(client_id=0), boundary(1))
        result = evaluate(data, identity(client_id=0))
        self.assertEqual(data.replay.sessions[0].client_id, 19)
        self.assertIs(result.outcome, Outcome.SUPPORTED_AT_DECISION_BOUNDARY)
        self.assertIn('NO_ORDER_ATTEMPT_OR_POSITION_OWNERSHIP', result.limitations)

    def test_supplied_perm_zero_is_preserved_and_distinct_from_absence(self):
        result = evaluate(evidence(status(perm_id=0), boundary(1)), identity(perm_id=0))
        self.assertEqual(result.observation.perm_id, 0)
        self.assertIs(result.observation.presence.perm_id, KNOWN)
        absent = evaluate(evidence(status(absent={'perm_id'}), boundary(1)), identity(perm_id=0))
        self.assertIs(absent.outcome, Outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)

    def test_legacy_ambiguous_perm_zero_never_becomes_supplied_identity(self):
        result = evaluate(evidence(status(legacy=True, perm_id=0), boundary(1)), identity(perm_id=0))
        self.assertIs(result.outcome, Outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)

    def test_unknown_identity_occurrence_is_not_attributed_by_adjacency(self):
        data = evidence(status(), status(2, absent={'client_id'}), boundary(2))
        result = evaluate(data)
        self.assertEqual(result.selected_sequence, 1)
        self.assertIn('UNKNOWN_IDENTITY_OCCURRENCES_NOT_ATTRIBUTED', result.limitations)


class InvalidTests(PureCase):
    def test_result_no_status_and_ambiguity_cannot_smuggle_a_record(self):
        ambiguous = evaluate(evidence(status(), status(2, perm_id=88), boundary(2)))
        self.invalid(replace, ambiguous, selected_record=ambiguous.prefix.records[1])
        absent = evaluate(evidence(status(client_id=3), boundary(1)))
        self.invalid(replace, absent, selected_record=absent.prefix.records[1])

    def test_raw_sdk_getters_never_run_on_type_rejection(self):
        class Raw:
            @property
            def records(self): raise AssertionError('raw SDK getter')
        self.invalid(e._evaluate_decision_status, Raw(), Raw(), Raw())

    def test_equal_reconstructed_boundary_record_is_rejected(self):
        data = evidence(status(), boundary(1))
        self.invalid(e._evaluate_decision_status, data, replace(data.boundary_records[0]), identity())

    def test_other_journal_boundary_is_rejected_even_if_payload_equal(self):
        first = evidence(status(), boundary(1)); other = evidence(status(), boundary(1))
        self.invalid(e._evaluate_decision_status, first, other.boundary_records[0], identity())

    def test_plain_record_tuple_not_qualified_companion(self):
        data = evidence(status(), boundary(1))
        self.invalid(e._evaluate_decision_status, data.records, data.boundary_records[0], identity())

    def test_no_boundary_or_timestamp_selector_fallback(self):
        data = evidence(status(), boundary(1))
        for value in (None, data.records[1], data.boundary_records[0].payload, f.AT, f.AT + timedelta(seconds=1)):
            self.invalid(e._evaluate_decision_status, data, value, identity())

    def test_v1_and_v2_replay_are_readable_but_not_qualified_inputs(self):
        for version in (1, 2):
            h = v3.header(version)
            replay = j._replay_paper_execution_journal(h, f.records((f.session(),), h))
            self.invalid(e._evaluate_decision_status, replay, None, identity())

    def test_omitted_companion_boundary_is_rejected(self):
        data = evidence(status(), boundary(1))
        bad = f.forged(data, boundary_records=())
        self.invalid(e._evaluate_decision_status, bad, data.records[-1], identity())

    def test_result_cannot_substitute_equal_selected_record(self):
        result = evaluate(evidence(status(), boundary(1)))
        self.invalid(replace, result, selected_record=replace(result.selected_record))

    def test_result_cannot_smuggle_status_into_unsupported_outcome(self):
        result = evaluate(evidence(status(), boundary(1)))
        self.invalid(replace, result, outcome=Outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)

    def test_result_revalidates_latest_selection_not_only_type(self):
        result = evaluate(evidence(status(), status(2), boundary(2)))
        self.invalid(replace, result, selected_record=result.prefix.records[1])

    def test_result_proof_must_stop_exactly_at_boundary(self):
        data = evidence(status(), boundary(1), filler(2))
        result = evaluate(data)
        self.invalid(replace, result, prefix=data)

    def test_result_limitations_are_fixed_not_caller_removed(self):
        result = evaluate(evidence(status(), boundary(1)))
        self.invalid(replace, result, limitations=())


class PurityTests(PureCase):
    def test_frozen_slotted_opaque_models_and_no_authority_fields(self):
        result = evaluate(evidence(status(), boundary(1)))
        for value in (result, result.identity):
            self.assertFalse(hasattr(value, '__dict__'))
            self.assertNotIn(f.SID, repr(value)); self.assertNotIn('Submitted', repr(value))
            with self.assertRaises(FrozenInstanceError): setattr(value, fields(value)[0].name, None)
            for name in ('authorize_paper', 'authorize_live', 'clear_reconciliation', 'clear_quarantine',
                'trading_enabled', 'consume_receipt', 'ownership', 'lifecycle', 'current_remainder',
                'inventory', 'position', 'protection', 'cancel', 'replace', 'exit', 'recovery',
                'accounting', 'incorporate_execution', 'interval_start_at', 'interval_end_at'):
                self.assertFalse(hasattr(value, name))
        self.assertTrue(result.prefix.replay.restart_quarantine_required)
        self.assertIs(result.prefix.replay.header.account_mode, AccountMode.UNKNOWN)

    def test_source_has_no_broker_collection_storage_producer_or_clock_import(self):
        # Inspect outside file patches by using the source file loaded before evaluation.
        tree = ast.parse(MODULE_SOURCE)
        modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertEqual(modules, {'dataclasses', 'enum', 'functools', None, 'readonly_models'})
        forbidden = {'now', 'utcnow', 'time', 'monotonic', 'connect', 'open', '_append', '_append_event',
            '_certify_decision_boundary', '_collect', '_consume_receipt', 'reqExecutions', 'reqAllOpenOrders'}
        calls = {getattr(n.func, 'attr', getattr(n.func, 'id', '')) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertFalse(calls & forbidden)

    def test_no_public_exports_or_constructor_grants_authority(self):
        import tradingbot_broker
        self.assertEqual(e.__all__, ())
        for name in ('QualifiedStatusIdentity', 'DecisionStatusEvidence', 'evaluate_decision_status'):
            self.assertFalse(hasattr(tradingbot_broker, name)); self.assertFalse(hasattr(e, name))

    def test_receipts_and_memory_evidence_cannot_substitute_durable_input(self):
        from tradingbot_broker import paper_fresh_open_order_collection as o
        from tradingbot_broker import paper_fresh_execution_collection as x
        from tradingbot_broker import paper_fresh_completed_order_collection as c
        from tradingbot_broker import ibkr_qualified_status_ledger as q
        data = evidence(status(), boundary(1))
        for kind in (o._FreshOpenOrderReceipt, x._FreshExecutionReceipt, c._FreshCompletedOrderReceipt,
                     q._QualifiedOrderStatusEvidence, producer._DecisionBoundaryReceipt):
            self.invalid(e._evaluate_decision_status, object.__new__(kind), data.records[-1], identity())

    def test_decimal_shape_survives_hostile_context_without_arithmetic(self):
        event = status(filled=D('-0.00'), remaining=D('10.000'), average_fill_price=D('1.2300'))
        data = evidence(event, boundary(1))
        with localcontext() as ctx:
            ctx.prec = 1; ctx.traps[Inexact] = True; ctx.traps[Rounded] = True
            result = evaluate(data)
        for name in ('filled', 'remaining', 'average_fill_price'):
            self.assertEqual(getattr(result.observation, name).as_tuple(), getattr(event.observation, name).as_tuple())


MODULE_SOURCE = inspect.getsource(e)


class RuntimeRegressionTests(unittest.TestCase):
    def test_actual_private_producer_history_evaluates_without_live_receipt(self):
        from tests.test_paper_decision_boundary_producer import ProducerTests
        case = ProducerTests('runTest'); case.setUp()
        try:
            case.start_producer(); case.qualified(); receipt = case.certify()
            loaded = case.journal._load()
            data = j._replay_v3_decision_boundary_evidence(loaded.header, loaded.records)
            selector = Selector(receipt.event.session_id, receipt.event.write_generation, 10, 2)
            state = (case.journal._persistence_cursor, case.write._callback_sequence,
                case.coordinator._callback_cursor, case.coordinator._reconciliation_required,
                case.broker._reconciliation_required, case.broker.controls,
                case.broker._paper_authority.status)
            with ExitStack() as scope:
                for name in ('sqlite3.connect', 'builtins.open', 'pathlib.Path.open', 'time.time', 'time.monotonic'):
                    scope.enter_context(patch(name, side_effect=AssertionError('pure evaluation')))
                scope.enter_context(patch.object(producer, '_decision_utc_now', side_effect=AssertionError('clock')))
                scope.enter_context(patch.object(producer._PaperDispatchJournalOwner, '_require_decision_receipt',
                                                side_effect=AssertionError('live receipt')))
                result = evaluate(data, selector)
            self.assertEqual(result.selected_sequence, 1)
            self.assertEqual(result.decision_at, receipt.event.decision_at)
            self.assertEqual(case.journal._load().records, loaded.records)
            self.assertEqual(state, (case.journal._persistence_cursor, case.write._callback_sequence,
                case.coordinator._callback_cursor, case.coordinator._reconciliation_required,
                case.broker._reconciliation_required, case.broker.controls,
                case.broker._paper_authority.status))
            self.assertIs(case.broker.account_mode, AccountMode.UNKNOWN)
            self.assertIsNone(case.transport._snapshot.account.mode)
            self.assertFalse(case.broker.controls.trading_enabled)
            # A later stored status and producer receipt never change the earlier result.
            case.qualified(status='Filled'); case.advance(); case.certify()
            later = case.journal._load()
            all_data = j._replay_v3_decision_boundary_evidence(later.header, later.records)
            self.assertEqual(evaluate(all_data, selector), result)
            self.assertEqual(evaluate(all_data, selector, index=1).selected_sequence, 2)
            # Closing the live owner does not grant restart adoption or impede historical evaluation.
            case.journal._close()
            self.assertEqual(evaluate(all_data, selector), result)
            self.assertTrue(result.prefix.replay.restart_quarantine_required)
        finally: case.tearDown(); case.doCleanups()


def identity_value_case(field, value, name):
    def test(self):
        self.invalid(identity, **{field: value})
        data = evidence(status(), boundary(1))
        self.invalid(e._evaluate_decision_status, data, data.records[-1], f.forged(identity(), **{field: value}))
    setattr(InvalidTests, f'test_bad_selector_{field}_{name}', test)

for field in ('order_id', 'client_id', 'write_generation', 'perm_id'):
    for name, value in (('bool', True), ('float', 1.0), ('text', '1'), ('negative', -1), ('decimal', D(1)),
                        ('oversized', 1 << (63 if field in ('write_generation', 'perm_id') else 31))):
        identity_value_case(field, value, name)
for field in ('order_id', 'client_id', 'write_generation'): identity_value_case(field, None, 'missing')
identity_value_case('write_generation', 0, 'zero')
for name, value in (('none', None), ('int', 1), ('reference', 'entry-1'), ('uppercase', 'session-'+'A'*32)):
    identity_value_case('session_id', value, name)


def numeric_identity_case(order, client, legacy):
    def test(self):
        data = evidence(status(order_id=order, client_id=client, legacy=legacy), boundary(1))
        result = evaluate(data, identity(order_id=order, client_id=client))
        supported = not legacy or order > 0 and client > 0
        self.assertIs(result.outcome, Outcome.SUPPORTED_AT_DECISION_BOUNDARY if supported
                      else Outcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)
    setattr(IdentityTests, f'test_{"legacy" if legacy else "protobuf"}_order_{order}_client_{client}', test)
for order in (0, 1, (1 << 31)-1):
    for client in (0, 1, (1 << 31)-1):
        for legacy in (False, True): numeric_identity_case(order, client, legacy)


def source_value_case(field, value, name):
    def test(self):
        event = status(**{field: value}); result = evaluate(evidence(event, boundary(1)))
        self.assertIs(result.observation, event.observation)
        if type(value) is D: self.assertEqual(getattr(result.observation, field).as_tuple(), value.as_tuple())
        else: self.assertEqual(getattr(result.observation, field), value)
    setattr(SelectionTests, f'test_preserves_{field}_{name}', test)
for field in ('filled', 'remaining', 'average_fill_price', 'last_fill_price'):
    for name, value in (('zero', D(0)), ('signed_zero', D('-0.00')), ('scaled', D('3.1400')),
                        ('tiny', D('1e-64')), ('large', D('9'*64))):
        source_value_case(field, value, name)
for value in ('PendingSubmit', 'PreSubmitted', 'Submitted', 'Filled', 'Cancelled', 'ApiCancelled',
              'Inactive', 'Expired', 'UNKNOWN'): source_value_case('status', value, value)


def unavailable_case(field, legacy):
    def test(self):
        event = status(unavailable={field}, legacy=legacy)
        result = evaluate(evidence(event, boundary(1)))
        self.assertIs(result.outcome, Outcome.SUPPORTED_AT_DECISION_BOUNDARY)
        self.assertIsNone(getattr(result.observation, field))
        self.assertIs(getattr(result.observation.presence, field), UNAVAILABLE)
        if not legacy: self.assertIn(field, result.observation.presence.wire_supplied)
    setattr(SelectionTests, f'test_supplied_unavailable_{field}_{"legacy" if legacy else "protobuf"}', test)
for field in ('filled', 'remaining', 'average_fill_price', 'last_fill_price'):
    for legacy in (False, True): unavailable_case(field, legacy)


def corruption_case(name, mutate):
    def test(self):
        data = evidence(status(), boundary(1)); mutated = mutate(data)
        self.invalid(e._evaluate_decision_status, mutated, mutated.records[-1], identity())
    setattr(InvalidTests, 'test_corrupt_' + name, test)

for name, mutate in (
    ('records_list', lambda d: f.forged(d, records=list(d.records))),
    ('replay_substitution', lambda d: f.forged(d, replay=replace(d.replay, last_sequence=1))),
    ('boundary_clone', lambda d: f.forged(d, boundary_records=(replace(d.records[-1]),))),
    ('sequence_gap', lambda d: f.forged(d, records=(d.records[0], d.records[-1]))),
    ('boundary_ahead', lambda d: tampered(d, d.records[-1].payload, through_sequence=2)),
    ('boundary_behind', lambda d: tampered(d, d.records[-1].payload, through_sequence=0)),
    ('boundary_wrong_generation', lambda d: tampered(d, d.records[-1].payload, write_generation=4)),
    ('boundary_wrong_session', lambda d: tampered(d, d.records[-1].payload, session_id=OTHER)),
    ('source_wrong_generation', lambda d: tampered(d, d.records[1].payload.source, write_generation=4)),
    ('source_wrong_session', lambda d: tampered(d, d.records[1].payload.source, session_id=OTHER)),
    ('source_gap', lambda d: tampered(d, d.records[1].payload.source, sequence=2)),
    ('source_bool', lambda d: tampered(d, d.records[1].payload.source, sequence=True)),
    ('header_future', lambda d: tampered(d, d.replay.header, schema_version=4)),
    ('header_v2', lambda d: tampered(d, d.replay.header, schema_version=2, format_id=j._FORMAT_ID_V2)),
    ('malformed_observation', lambda d: tampered(d, d.records[1].payload.observation, filled=D('NaN'))),
    ('bad_presence', lambda d: tampered(d, d.records[1].payload.observation.presence, order_id=UNAVAILABLE)),
    ('duplicate_source', lambda d: f.forged(d, records=(d.records[0], d.records[1],
        replace(d.records[1], sequence=3), replace(d.records[-1], sequence=4)))),
): corruption_case(name, mutate)


def tampered(data, value, **changes):
    for name, changed in changes.items(): object.__setattr__(value, name, changed)
    return data


def mismatch_case(field, value):
    def test(self):
        data = evidence(status(), boundary(1))
        self.invalid(e._evaluate_decision_status, data, data.records[-1], identity(**{field: value}))
    setattr(InvalidTests, 'test_boundary_selector_mismatch_' + field, test)
mismatch_case('session_id', OTHER); mismatch_case('write_generation', 4)


def interruption_case(seam, kind):
    def test(self):
        data = evidence(status(), boundary(1)); error = kind(41 if kind is SystemExit else 'original')
        target, name = (j, '_replay_v3_decision_boundary_evidence') if seam == 'prefix' else (
            (e, '_known_identity_value') if seam == 'selection' else (j, '_replay_paper_execution_journal'))
        with patch.object(target, name, side_effect=error):
            with self.assertRaises(kind) as caught: evaluate(data)
        self.assertIs(caught.exception, error)
        if kind is SystemExit: self.assertEqual(caught.exception.code, 41)
    setattr(PurityTests, f'test_{seam}_{kind.__name__}_identity', test)
for seam in ('revalidation', 'prefix', 'selection'):
    for kind in (KeyboardInterrupt, SystemExit, GeneratorExit): interruption_case(seam, kind)


if __name__ == '__main__': unittest.main()
