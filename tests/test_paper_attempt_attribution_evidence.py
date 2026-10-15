"""PRE7 frozen-prefix attribution; anonymous local fixtures, no real network."""

import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
import inspect
from pathlib import Path
import pickle
import sqlite3
import unittest
from unittest.mock import patch

from tradingbot_broker import paper_attempt_attribution_evidence as m
from tradingbot_broker import paper_cross_source_identity_evidence as pre6
from tradingbot_broker import paper_decision_status_evidence as status
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker import paper_dispatch_journal as dispatch
from tradingbot_broker import paper_execution_accounting as accounting
from tradingbot_broker import paper_working_order as lifecycle
from tradingbot_broker import paper_recovery as recovery
from tradingbot_broker.readonly_models import ReadOnlyError, AccountMode
from tests.test_paper_cross_source_identity_evidence import Fixture as PRE6Fixture
from tests import test_paper_execution_journal as fixtures
from tests.dispatch_journal_fixtures import AT


Owner = m._AttemptAttributionEvidenceOwner
Result = m._AttemptAttributionEvidence
Outcome = m._AttemptAttributionOutcome
PREFIX = '_AttemptAttributionEvidenceOwner'


class Fixture(PRE6Fixture):
    def build(self, **kwargs):
        result = super().build(**kwargs)
        if getattr(self, 'real_fake_dispatch', False):
            self.invoke()  # Original bridge, anonymous SDK only; never a broker.
        else:
            for key, oid, stage, other in getattr(self, 'local_attempts', (('entry-1', 10, 'invocation', False),)):
                self.seed_attempt(key, oid, stage, other)
        return result

    def seed_attempt(self, key, oid, stage, other=False):
        # Canonical anonymous journal fixtures test syntax/replay semantics only.
        # These helpers do not certify real dispatch or become production APIs.
        sid = self.journal._session.session_id
        if other:
            sid = fixtures.SID2
            self.journal._append_event(j._SessionRegistered(sid, 2, self.write._generation))
        self.journal._append_event(fixtures.consumed(key, sid))
        if stage != 'consumed': self.journal._append_event(j._NumericOrderIdReserved(key, oid))
        if stage in ('invocation', 'returned', 'uncertain'):
            self.journal._append_event(j._InvocationMayOccur(key))
        if stage == 'returned': self.journal._append_event(j._SdkReturnedNormally(key))
        if stage == 'closed':
            self.journal._append_event(j._AttemptClosedWithoutInvocation(key, j._ClosureReason.PREFLIGHT_FAILED))
        if stage == 'uncertain':
            self.journal._append_event(j._ReconciliationRequired(j._ReconciliationReason.SDK_EXCEPTION,
                                                                  client_order_id=key, session_id=sid))

    def setUp(self):
        super().setUp()
        self.root6 = self.correlate()
        self.attributor = m._create_attempt_attribution_evidence_owner(self.cross)
        self.before = self.safety()

    def attribute(self): return self.attributor._evaluate(self.root6)

    def case(self, *, attempts=None, specs=None, statuses=({},), identity=None):
        self.local_attempts = (('entry-1', 10, 'invocation', False),) if attempts is None else attempts
        self.root6 = self.scenario(specs, statuses=statuses, identity=identity)
        self.attributor = m._create_attempt_attribution_evidence_owner(self.cross)
        self.before = self.safety()
        return self.attribute()

    def invalid(self, function, *args, **kwargs):
        with self.assertRaises(ReadOnlyError) as caught: function(*args, **kwargs)
        self.assertEqual(caught.exception.code, m._ERROR)
        self.assertEqual(str(caught.exception), m._ERROR)


class SuccessTests(Fixture):
    def test_exact_frozen_session_reservation_and_attempt_references(self):
        result = self.attribute(); replay = self.root6.pre5.status.prefix.replay
        self.assertIs(result.pre6, self.root6); self.assertIs(result.identity, self.root6.identity)
        self.assertIs(result.session, next(s for s in replay.sessions if s.session_id == result.identity.session_id))
        self.assertIs(result.reservation, next(p for p in replay.reserved_order_ids if p[0] == 10))
        self.assertIs(result.attempt, next(a for a in replay.attempts if a.order_id == 10))
        self.assertIs(result.outcome, Outcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT)
        self.assertEqual(result.client_order_id, 'entry-1')
        self.assertIs(self.attributor._require_evidence(result), result)
        self.assertEqual(self.safety(), self.before)

    def test_sdk_normal_return_absence_does_not_prevent_attribution(self):
        result = self.attribute()
        self.assertFalse(result.attempt.sdk_returned_normally)
        self.assertTrue(result.attempt.sdk_outcome_uncertain)
        self.assertIs(result.outcome, Outcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT)
        self.assertTrue(self.coordinator._broker_state_reconciliation_required)

    def test_original_fake_dispatch_to_fresh_sources_to_attribution(self):
        self.real_fake_dispatch = True
        result = self.case()
        self.assertIs(result.outcome, Outcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT)
        self.assertEqual(result.client_order_id, self.order.client_order_id)
        self.assertTrue(result.attempt.invocation_may_have_occurred)
        self.assertEqual(self.safety(), self.before)

    def test_pre4_support_alone_is_sufficient(self):
        result = self.case(specs=([], [], []))
        self.assertIs(result.outcome, Outcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT)
        self.assertEqual(self.root6.outcome, pre6._CrossSourceIdentityOutcome.NO_QUALIFIED_FRESH_MATCH)

    def test_unresolved_requested_perm_retains_numeric_attribution_only(self):
        result = self.case(specs=tuple([{'absent': ('permId',)}] for _ in range(3)),
                           statuses=(), identity={'perm_id': 77})
        self.assertIs(result.outcome, Outcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT)
        self.assertEqual(self.root6.outcome, pre6._CrossSourceIdentityOutcome.PARTIAL_IDENTITY_COVERAGE)
        self.assertEqual(result.identity.perm_id, 77)
        self.assertTrue(self.root6.open_orders.unresolved_perm_id)
        self.assertIn('LOCAL_ATTRIBUTION_DOES_NOT_ESTABLISH_PERMID_OWNERSHIP', result.limitations)

    def test_no_old_callback_attribution_prerequisite(self):
        result = self.attribute()
        self.assertFalse(result.attempt.strong_open_order_identity_established)
        self.assertFalse(result.attempt.broker_observed)
        self.assertEqual(result.attempt.open_observations, ())
        self.assertEqual(result.attempt.status_observations, ())
        self.assertEqual(result.attempt.executions, ())
        self.assertIs(result.outcome, Outcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT)

    def test_multiple_attempts_select_only_exact_reservation_not_latest(self):
        result = self.case(attempts=(('earlier', 10, 'invocation', False), ('later', 11, 'returned', False)))
        self.assertEqual(result.client_order_id, 'earlier')
        self.assertEqual(result.reservation, (10, 'earlier'))

    def test_valid_later_attempt_and_boundary_do_not_enrich_frozen_result(self):
        result = self.attribute(); prefix = result.pre6.pre5.status.prefix
        self.seed_attempt('later', 11, 'closed')
        self.certify(AT + timedelta(seconds=2))
        self.assertIs(self.attributor._require_evidence(result), result)
        self.assertIs(result.pre6.pre5.status.prefix, prefix)
        self.assertNotIn('later', {a.consumed.client_order_id for a in prefix.replay.attempts})
        self.assertEqual(result.client_order_id, 'entry-1')

    def test_future_reservation_cannot_repair_historical_no_reservation(self):
        result = self.case(attempts=())
        self.assertIs(result.outcome, Outcome.NO_LOCAL_NUMERIC_RESERVATION)
        self.seed_attempt('later', 10, 'closed')
        self.assertIs(self.attributor._require_evidence(result), result)
        self.assertIsNone(result.attempt); self.assertIsNone(result.client_order_id)

    def test_deterministic_repeat_evaluation_supersedes_only_local_slot(self):
        first = self.attribute(); second = self.attribute()
        self.assertEqual(first, second); self.assertIsNot(first, second)
        self.invalid(self.attributor._require_evidence, first)
        self.assertIs(self.attributor._require_evidence(second), second)

    def test_exact_pre6_validator_called_before_and_after_analysis(self):
        original = type(self.cross)._require_evidence; seen = []
        def checked(owner, evidence):
            seen.append((owner, evidence)); return original(owner, evidence)
        self.change(self.transport, '_attempt_attribution_evidence_owner', None)
        with patch.object(type(self.cross), '_require_evidence', checked):
            owner = m._create_attempt_attribution_evidence_owner(self.cross)
            result = owner._evaluate(self.root6)
            self.assertIs(owner._require_evidence(result), result)
        self.assertGreaterEqual(len(seen), 4)
        self.assertTrue(all(o is self.cross and r is self.root6 for o, r in seen))


class NegativeTests(Fixture):
    def test_caller_selected_reserved_numbers_are_not_broker_support(self):
        result = self.case(specs=([], [], []), statuses=())
        self.assertIs(result.outcome, Outcome.NO_BROKER_NUMERIC_PAIR_SUPPORT)
        self.assertIsNone(result.session); self.assertIsNone(result.reservation); self.assertIsNone(result.attempt)

    def test_legacy_completed_record_alone_is_not_numeric_support(self):
        result = self.case(specs=([], [], [{'encoding': 'LEGACY'}]), statuses=())
        self.assertIs(result.outcome, Outcome.NO_BROKER_NUMERIC_PAIR_SUPPORT)

    def test_durable_ambiguity_blocks_even_when_local_chain_matches(self):
        result = self.case(statuses=({'permId': 77}, {'permId': 88}))
        self.assertIs(result.outcome, Outcome.IDENTITY_EVIDENCE_BLOCKED)
        self.assertIsNone(result.attempt)

    def test_identity_conflict_blocks_even_when_local_chain_matches(self):
        result = self.case(specs=([{}], [{'values': {'permId': 88}}], [{}]))
        self.assertIs(result.outcome, Outcome.IDENTITY_EVIDENCE_BLOCKED)

    def test_durable_registered_client_mismatch_does_not_attribute(self):
        result = self.case(specs=tuple([{'values': {'clientId': 5}}] for _ in range(3)),
                           statuses=({'clientId': 5},), identity={'client_id': 5})
        self.assertIs(result.outcome, Outcome.LOCAL_CLIENT_MISMATCH)
        self.assertEqual(result.session.client_id, 2)
        self.assertIsNone(result.reservation); self.assertIsNone(result.attempt)

    def test_other_session_attempt_does_not_attribute(self):
        result = self.case(attempts=(('other-session', 10, 'invocation', True),))
        self.assertIs(result.outcome, Outcome.LOCAL_SESSION_MISMATCH)
        self.assertEqual(result.attempt.consumed.session_id, fixtures.SID2)
        self.assertIsNone(result.client_order_id)

    def test_orderref_symbol_contract_economics_cannot_rescue_numeric_mismatch(self):
        result = self.case(attempts=(('entry-1', 11, 'invocation', False),))
        self.assertIs(result.outcome, Outcome.NO_LOCAL_NUMERIC_RESERVATION)
        self.assertIsNone(result.attempt)


class IssuerTests(Fixture):
    def test_constructor_and_other_roots_cannot_mint_issuer(self):
        self.invalid(Owner)
        for value in (None, {}, object(), self.root6, self.combiner, self.token, self.journal):
            self.invalid(m._create_attempt_attribution_evidence_owner, value)
        self.assertEqual(list(inspect.signature(Owner._evaluate).parameters), ['self', 'pre6_result'])

    def test_single_registered_owner(self):
        self.invalid(m._create_attempt_attribution_evidence_owner, self.cross)
        self.assertIs(self.transport._attempt_attribution_evidence_owner, self.attributor)

    def test_reconstructed_pre6_owner_rejected(self):
        clone = object.__new__(type(self.cross))
        for slot in type(self.cross).__slots__:
            name = '_CrossSourceIdentityEvidenceOwner' + slot
            object.__setattr__(clone, name, getattr(self.cross, name))
        self.invalid(m._create_attempt_attribution_evidence_owner, clone)

    def test_reconstructed_pre6_root_rejected(self):
        self.invalid(self.attributor._evaluate, replace(self.root6))

    def test_superseded_pre6_root_rejected(self):
        result = self.attribute(); newer = self.correlate()
        self.assertEqual(newer, self.root6)
        self.invalid(self.attributor._require_evidence, result)
        self.invalid(self.attributor._evaluate, newer)

    def test_reconstructed_pickled_result_rejected(self):
        result = self.attribute()
        for value in (replace(result), pickle.loads(pickle.dumps(result))):
            self.assertEqual(value, result)
            self.invalid(self.attributor._require_evidence, value)
        self.assertIs(self.attributor._require_evidence(result), result)

    def test_frozen_slotted_opaque_no_authority_or_independent_client_id_field(self):
        result = self.attribute()
        self.assertFalse(hasattr(result, '__dict__')); self.assertFalse(hasattr(self.attributor, '__dict__'))
        with self.assertRaises(FrozenInstanceError): result.attempt = None
        with self.assertRaises(ReadOnlyError): self.attributor.authorized = True
        with self.assertRaises(ReadOnlyError): del self.attributor._result
        self.assertEqual(repr(result), '<PrivateAttemptAttributionEvidence evidence-only>')
        self.assertEqual({f.name for f in fields(Result)},
            {'pre6', 'identity', 'session', 'reservation', 'attempt', 'outcome', 'limitations'})

    def test_wrong_thread_before_validator_or_live_locks(self):
        result = self.attribute()
        with ThreadPoolExecutor(max_workers=1) as pool:
            for function, args in ((self.attributor._evaluate, (self.root6,)),
                    (self.attributor._require_evidence, (result,)),
                    (m._create_attempt_attribution_evidence_owner, (self.cross,))):
                with self.assertRaises(ReadOnlyError) as caught: pool.submit(function, *args).result(timeout=5)
                self.assertEqual(caught.exception.code, m._ERROR)
        self.assertIs(self.attributor._require_evidence(result), result)

    def test_corrupt_current_storage_still_rejected_by_inherited_validation(self):
        result = self.attribute(); self.corrupt_digest(self.boundary.committed.record.sequence)
        self.invalid(self.attributor._require_evidence, result)
        self.assertIsNone(self.attributor._result)

    def test_replaced_pre6_validator_cannot_bypass_original_issuer(self):
        with patch.object(type(self.cross), '_require_evidence', side_effect=AssertionError('substitution')):
            self.invalid(self.attribute)


class FailureTests(Fixture):
    def test_ordinary_failure_sanitized_and_repeatable(self):
        with patch.object(Owner, '_publish', side_effect=ValueError('private message')): self.invalid(self.attribute)
        self.assertIsNone(self.attributor._result)
        self.assertIs(self.attributor._require_evidence(self.attribute()), self.attributor._result)

    def test_reentry_cannot_publish_partial_result(self):
        with patch.object(Owner, '_publish', side_effect=lambda result: self.attribute()): self.invalid(self.attribute)
        self.assertIsNone(self.attributor._result)

    def test_equal_publication_substitution_rejected(self):
        with patch.object(Owner, '_publish', side_effect=lambda result: replace(result)): self.invalid(self.attribute)
        self.assertIsNone(self.attributor._result)


class NoAuthorityTests(Fixture):
    def test_existing_issuer_read_is_only_io_no_writes_or_operations_or_clocks(self):
        connection = self.journal._store._connection
        forbidden = {getattr(sqlite3, name) for name in ('SQLITE_INSERT', 'SQLITE_UPDATE', 'SQLITE_DELETE',
            'SQLITE_CREATE_TABLE', 'SQLITE_DROP_TABLE', 'SQLITE_CREATE_TRIGGER', 'SQLITE_DROP_TRIGGER',
            'SQLITE_ALTER_TABLE', 'SQLITE_CREATE_INDEX', 'SQLITE_DROP_INDEX')}
        seen = []
        def authorize(action, *args):
            if action in forbidden: seen.append(action); return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        connection.set_authorizer(authorize)
        try:
            with ExitStack() as stack:
                for cls in (self.api.Client, self.paper_api.Client):
                    for name in ('reqAllOpenOrders', 'reqExecutions', 'reqCompletedOrders', 'placeOrder', 'cancelOrder'):
                        if hasattr(cls, name): stack.enter_context(patch.object(cls, name, side_effect=AssertionError(name)))
                for source in self.sources:
                    for name in ('_collect', '_authenticate'):
                        stack.enter_context(patch.object(type(source), name, side_effect=AssertionError(name)))
                for cls, names in ((type(self.preparation), ('_prepare',)),
                    (type(self.journal), ('_certify_decision_boundary', '_append_event', '_drain_callbacks')),
                    (type(self.journal._store), ('_append',))):
                    for name in names: stack.enter_context(patch.object(cls, name, side_effect=AssertionError(name)))
                for module in (accounting, lifecycle, recovery):
                    for name in dir(module):
                        value = getattr(module, name)
                        if inspect.isfunction(value) and value.__module__ == module.__name__:
                            stack.enter_context(patch.object(module, name, side_effect=AssertionError(name)))
                for name in ('time.time', 'time.monotonic'):
                    stack.enter_context(patch(name, side_effect=AssertionError('clock')))
                stack.enter_context(patch.object(dispatch, '_decision_utc_now', side_effect=AssertionError('clock')))
                result = self.attribute()
                self.assertIs(self.attributor._require_evidence(result), result)
        finally: connection.set_authorizer(None)
        self.assertEqual(seen, []); self.assertEqual(self.safety(), self.before)
        self.assertEqual(self.consumed(), (True, True, True))

    def test_original_source_consumer_identity_is_not_weakened(self):
        with patch.object(type(self.sources[0]), '_consume_receipt', side_effect=AssertionError('consume')):
            self.invalid(self.attribute)  # Original issuer rejects method replacement before invocation.
        self.assertEqual(self.consumed(), (True, True, True))

    def test_no_authority_export_codec_or_state_mutation(self):
        result = self.attribute()
        self.assertEqual(m.__all__, ())
        self.assertEqual([name for name in dir(Owner) if not name.startswith('_')], [])
        self.assertNotIn(Result, storage._MODELS)
        with self.assertRaises(ReadOnlyError): storage._encode(result)
        self.assertTrue(result.pre6.pre5.status.prefix.replay.restart_quarantine_required)
        self.assertIs(self.broker.account_mode, AccountMode.UNKNOWN)
        self.assertIsNone(self.transport._snapshot.account.mode)
        self.assertFalse(self.broker.controls.trading_enabled)
        self.assertEqual(self.safety(), self.before)
        self.assertEqual(result.limitations, m._LIMITATIONS)
        self.assertEqual(len(result.limitations), 18)

    def test_ast_forbids_lifecycle_old_attribution_economics_arithmetic_and_new_io(self):
        tree = ast.parse(Path(m.__file__).read_text())
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertTrue({'open_observations', 'status_observations', 'commissions',
            'observed_execution_quantity', 'historical_filled_lower_bound', 'strong_open_order_identity_established',
            'broker_observed', 'sdk_returned_normally', 'order_ref', 'symbol', 'con_id', 'quantity', 'shares',
            'filled', 'remaining', 'price', 'action', 'placing_client_id', 'perm_id'} .isdisjoint(attrs))
        # Fresh executions correlation is accessed as root.executions solely for
        # positive candidates; it is not an attempt execution/economic field.
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == 'executions':
                self.assertIsInstance(node.value, ast.Name)
                self.assertEqual(node.value.id, 'root')
        calls = {n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id if isinstance(n.func, ast.Name) else ''
                 for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertTrue({'sum', 'max', 'min', 'sorted', 'open', 'connect', 'execute', '_append_event', '_append',
            '_collect', '_consume_receipt', '_prepare', '_certify_decision_boundary', '_drain_callbacks',
            '_load', 'now', 'time', 'monotonic', 'reqAllOpenOrders', 'reqExecutions', 'reqCompletedOrders',
            'placeOrder', 'cancelOrder'} .isdisjoint(calls))
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, ast.BinOp)
            and not isinstance(n.op, ast.BitOr)], [])  # Nullable type annotations use |.


def _stage(stage, expected):
    def test(self):
        result = self.case(attempts=(('entry-1', 10, stage, False),))
        self.assertIs(result.outcome, expected)
        self.assertEqual(self.safety(), self.before)
    return test


for _stage_name in ('consumed', 'reserved', 'closed', 'invocation', 'returned', 'uncertain'):
    _expected = Outcome.NO_LOCAL_NUMERIC_RESERVATION if _stage_name == 'consumed' else (
        Outcome.LOCAL_ATTEMPT_NOT_INVOCATION_ENABLED if _stage_name in ('reserved', 'closed') else Outcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT)
    setattr(SuccessTests, 'test_dispatch_stage_' + _stage_name, _stage(_stage_name, _expected))


def _source_support(index):
    def test(self):
        specs = [[], [], []]; specs[index] = [{}]
        result = self.case(specs=specs, statuses=())
        self.assertIs(result.outcome, Outcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT)
        self.assertIs(result.pre6.decision_status.outcome, status._DecisionStatusOutcome.NO_QUALIFIED_STATUS_THROUGH_BOUNDARY)
    return test


for _index in range(3): setattr(SuccessTests, f'test_fresh_source_{_index}_alone', _source_support(_index))


def _result_tamper(field):
    def test(self):
        result = self.attribute(); value = getattr(result, field)
        changed = tuple(list(value)) if type(value) is tuple else replace(value) if field not in ('outcome',) else None
        self.change(result, field, changed)
        self.invalid(self.attributor._require_evidence, result)
    return test


for _field in (f.name for f in fields(Result)):
    setattr(IssuerTests, 'test_result_reference_' + _field, _result_tamper(_field))


def _replay_tamper(field):
    def test(self):
        result = self.attribute(); replay = self.root6.pre5.status.prefix.replay
        if field == 'reservation_duplicate': self.change(replay, 'reserved_order_ids', replay.reserved_order_ids * 2)
        elif field == 'reservation_copy': self.change(replay, 'reserved_order_ids', tuple(tuple(list(p)) for p in replay.reserved_order_ids))
        elif field == 'reservation_change': self.change(replay, 'reserved_order_ids', ((11, 'entry-1'),))
        elif field == 'session_copy': self.change(replay, 'sessions', tuple(replace(s) for s in replay.sessions))
        elif field == 'attempt_copy': self.change(replay, 'attempts', tuple(replace(a) for a in replay.attempts))
        elif field == 'attempt_duplicate': self.change(replay, 'attempts', replay.attempts * 2)
        elif field == 'invocation': self.change(result.attempt, 'invocation_may_have_occurred', False)
        elif field == 'invocation_bool_alias': self.change(result.attempt, 'invocation_may_have_occurred', 1)
        elif field == 'order_id': self.change(result.attempt, 'order_id', 11)
        elif field == 'attempt_session': self.change(result.attempt.consumed, 'session_id', fixtures.SID2)
        elif field == 'generation': self.change(result.session, 'write_generation', result.session.write_generation + 1)
        elif field == 'generation_bool': self.change(result.session, 'write_generation', True)
        elif field == 'client_bool': self.change(result.session, 'client_id', True)
        else: self.change(result.identity, 'order_id', 11)
        self.invalid(self.attributor._require_evidence, result)
        self.invalid(self.attribute)
    return test


for _field in ('reservation_duplicate', 'reservation_copy', 'reservation_change', 'session_copy', 'attempt_copy',
               'attempt_duplicate', 'invocation', 'invocation_bool_alias', 'order_id', 'attempt_session',
               'generation', 'generation_bool', 'client_bool', 'identity'):
    setattr(IssuerTests, 'test_frozen_replay_tamper_' + _field, _replay_tamper(_field))


def _interruption(kind, seam, validating=False):
    def test(self):
        result = self.attribute(); error = kind(91) if kind is SystemExit else kind('private interruption')
        with patch.object(Owner, seam, side_effect=error):
            with self.assertRaises(kind) as caught:
                if validating: self.attributor._require_evidence(result)
                else: self.attribute()
        self.assertIs(caught.exception, error)
        if kind is SystemExit: self.assertEqual(caught.exception.code, 91)
        self.assertIsNone(self.attributor._result)
        self.assertFalse(getattr(self.attributor, PREFIX + '__evaluating'))
        self.assertEqual(self.safety(), self.before)
    return test


for _kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    for _seam in ('_validate_root', '_publish', '_set'):
        setattr(FailureTests, f'test_{_kind.__name__}_{_seam}', _interruption(_kind, _seam))
    setattr(FailureTests, f'test_{_kind.__name__}_revalidation', _interruption(_kind, '_validate_root', True))


def _reference_mismatch(value):
    def test(self):
        result = self.case(specs=tuple([{'values': {'orderRef': value}}] for _ in range(3)))
        self.assertIs(result.outcome, Outcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT)
    return test


for _value in ('unrelated-reference', ''):
    setattr(SuccessTests, 'test_orderref_not_attribution_' + ('missing' if not _value else 'different'), _reference_mismatch(_value))


def _after_assignment(kind):
    def test(self):
        original = Owner._set
        error = kind(91) if kind is SystemExit else kind('publication interrupted')
        def assign(owner, name, value):
            original(owner, name, value)
            if name == 'result': raise error
        with patch.object(Owner, '_set', assign):
            with self.assertRaises(kind) as caught: self.attribute()
        self.assertIs(caught.exception, error)
        self.assertIsNone(self.attributor._result)
        self.assertFalse(getattr(self.attributor, PREFIX + '__evaluating'))
        self.assertIs(self.attributor._require_evidence(self.attribute()), self.attributor._result)
    return test


for _kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    setattr(FailureTests, 'test_after_final_assignment_' + _kind.__name__, _after_assignment(_kind))


def _lifetime(component):
    def test(self):
        result = self.attribute()
        obj, name, value = {
            'read_generation': (self.transport, '_generation', self.transport._generation + 1),
            'write_generation': (self.write, '_generation', self.write._generation + 1),
            'read_client': (self.transport, '_client', object()),
            'write_client': (self.write, '_client', object()),
            'association': (self.transport, '_journal_account_association', replace(self.transport._journal_account_association)),
            'disconnect': (self.transport, '_disconnect_emitted', True),
            'write_closed': (self.write, '_closed', True),
            'pre6_registration': (self.transport, '_cross_source_identity_evidence_owner', object()),
            'pre7_registration': (self.transport, '_attempt_attribution_evidence_owner', object()),
            'frozen_prefix': (self.root6.pre5.status, 'prefix', replace(self.root6.pre5.status.prefix)),
            'correlation': (self.root6, 'open_orders', replace(self.root6.open_orders)),
        }[component]
        self.change(obj, name, value)
        self.invalid(self.attributor._require_evidence, result)
        self.invalid(self.attribute)
    return test


for _component in ('read_generation', 'write_generation', 'read_client', 'write_client', 'association',
                   'disconnect', 'write_closed', 'pre6_registration', 'pre7_registration', 'frozen_prefix', 'correlation'):
    setattr(IssuerTests, 'test_lifetime_' + _component, _lifetime(_component))


def _interrupted_original_validator(kind):
    def test(self):
        error = kind(91) if kind is SystemExit else kind('original issuer interrupted')
        def raising(owner, result): raise error
        self.change(self.attributor, PREFIX + '__require_evidence', raising.__get__(self.cross))
        with patch.object(type(self.cross), '_require_evidence', raising):
            with self.assertRaises(kind) as caught: self.attribute()
        self.assertIs(caught.exception, error)
        self.assertIsNone(self.attributor._result)
    return test


for _kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
    setattr(FailureTests, 'test_original_issuer_interruption_' + _kind.__name__, _interrupted_original_validator(_kind))


def _different_economics(spec):
    def test(self):
        result = self.case(specs=([{}], [{}], [spec]))
        self.assertIs(result.outcome, Outcome.ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT)
        self.assertEqual(result.client_order_id, 'entry-1')
    return test


for _label, _spec in (
        ('symbol_contract', {'contract': {'conId': 31, 'symbol': 'OTHER'}}),
        ('side', {'values': {'action': 'SELL'}}), ('quantity', {'values': {'totalQuantity': 200}}),
        ('order_type', {'values': {'orderType': 'LMT'}}), ('tif', {'values': {'tif': 'GTC'}}),
        ('status', {'status': 'Cancelled'})):
    setattr(SuccessTests, 'test_economics_irrelevant_' + _label, _different_economics(_spec))


if __name__ == '__main__': unittest.main()
