"""PRE6 pure contracts over synthetic values; no publication, SDK or storage."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timezone
from decimal import Decimal as D, Inexact, Rounded, localcontext
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tradingbot_broker import ibkr_qualified_status_ledger as contract
from tradingbot_broker import ibkr_order_status_evidence as source
from tradingbot_broker import ibkr_paper_transport as paper
from tradingbot_broker import ibkr_paper_status_delivery as delivery
from tradingbot_broker import paper_dispatch_journal as journal_owner
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests.test_ibkr_order_status_evidence import Proto, RAW, LEGACY, UNSET
from tests.test_ibkr_paper_transport import sdk


C = contract._LedgerConsumer
S = contract._ConsumerSupport
K = contract._CoverageState
R = contract._ReservationCollision
AT = datetime(2026, 2, 3, 14, 15, 16, tzinfo=timezone.utc)


def qualified(*, generation=1, sequence=1, raw=None, observation=None):
    if observation is None:
        observation = source._adapt_protobuf_order_status(Proto(**(RAW if raw is None else raw)))
    return contract._QualifiedOrderStatusEvidence(generation, sequence, observation)


def legacy(**changes):
    names = tuple(name for name, _, _, _ in source._SOURCE_FIELDS)
    values = dict(zip(names, LEGACY))
    values['parent_id'] = 3  # Every required legacy ID/price is unambiguous.
    values.update(changes)
    return source._adapt_legacy_order_status(*(values[name] for name in names))


def old_status(sequence=1, generation=1, **changes):
    event = paper._OrderStatusEvidence(generation, sequence, 10, 2, 'Submitted',
                                      D('4.00'), D('6.00'), D('1.25'), D('1.5'), 0)
    return replace(event, **changes)


def old_open(sequence=1, generation=1):
    return paper._OpenOrderEvidence(generation, sequence, 10, 2, 'synthetic-entry',
        17, 'STK', 'SMART', 'DEMO', 'USD', 'BUY', 'MKT', D(10), 'DAY', 'Submitted')


def old_execution(sequence=1, generation=1):
    return paper._ExecutionEvidence(generation, sequence, 10, 2, 'synthetic-entry',
                                   'synthetic-execution', 17, 'BOT', D(4), D('1.25'))


def commission(sequence=1, generation=1):
    exec_id, amount, currency = 'synthetic-execution', D('-0.05'), 'USD'
    return paper._CommissionEvidence(generation, sequence, exec_id,
        paper._commission_report_id(exec_id, amount, currency), amount, currency, AT)


def coverage(events, *, consumer=C.DISPATCH, generation=1, cursor=0, through=None, target=None):
    if through is None:
        through = max((event.sequence for event in events), default=cursor)
    return contract._coverage(tuple(events), consumer=consumer, generation=generation,
                              cursor=cursor, through_sequence=through, target_sequence=target)


class PureCase(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket.connect', 'socket.socket.connect_ex',
                     'socket.create_connection', 'socket.getaddrinfo', 'sqlite3.connect'):
            self.enterContext(patch(name, side_effect=AssertionError('external activity forbidden')))
        for owner, method in ((paper, '_load_paper_api'),
                              (delivery._PaperStatusDelivery, '_issue_handoff_receiver'),
                              (delivery._PaperStatusDelivery, '_accept_handoff'),
                              (journal_owner._PaperDispatchJournalOwner, '_append_event'),
                              (storage._SQLiteJournalStore, '_append')):
            self.enterContext(patch.object(owner, method,
                              side_effect=AssertionError('production activity forbidden')))

    def invalid(self, function, *args, **kwargs):
        with self.assertRaises(ReadOnlyError) as caught:
            function(*args, **kwargs)
        self.assertEqual(str(caught.exception), contract._ERROR)
        self.assertIsNone(caught.exception.__cause__)


class RecordTests(PureCase):
    def test_exact_fields_and_opaque_repr(self):
        event = qualified()
        self.assertEqual(tuple(f.name for f in fields(event)), ('generation', 'sequence', 'observation'))
        self.assertEqual(repr(event), '<PrivateQualifiedStatusLedgerValue>')

    def test_frozen_slotted_record(self):
        event = qualified()
        self.assertFalse(hasattr(event, '__dict__'))
        with self.assertRaises(FrozenInstanceError): event.sequence = 2

    def test_independent_nested_observation(self):
        observed = legacy()
        event = qualified(observation=observed)
        self.assertIsNot(event.observation, observed)
        self.assertIsNot(event.observation.presence, observed.presence)
        self.assertIsNot(event.observation.filled, observed.filled)
        self.assertEqual(contract._source_identity(event), contract._source_identity(qualified(observation=observed)))

    def test_source_mutation_cannot_change_record(self):
        observed = legacy()
        event = qualified(observation=observed)
        before = contract._source_identity(event)
        object.__setattr__(observed, 'filled', D(9))
        object.__setattr__(observed.presence, 'filled', source._OrderStatusSourceQuality.UNAVAILABLE)
        self.assertEqual(contract._source_identity(event), before)

    def test_nested_values_remain_frozen(self):
        event = qualified()
        with self.assertRaises(FrozenInstanceError): event.observation.filled = D(9)
        with self.assertRaises(FrozenInstanceError): event.observation.presence.filled = 'SUPPLIED'

    def test_unavailable_values_are_valid_source_evidence(self):
        event = qualified(raw={})
        contract._validate_event(event)
        self.assertIsNone(event.observation.order_id)
        self.assertIsNone(event.observation.filled)
        self.assertEqual(event.observation.presence.wire_supplied, frozenset())

    def test_revalidation_rejects_tampered_nested_values(self):
        event = qualified()
        object.__setattr__(event.observation, 'filled', D('NaN'))
        self.invalid(contract._source_identity, event)

    def test_revalidation_rejects_tampered_generation(self):
        event = qualified()
        object.__setattr__(event, 'generation', True)
        self.invalid(contract._validate_event, event)

    def test_revalidation_never_replaces_published_observation(self):
        event = qualified()
        original = event.observation
        contract._validate_event(event)
        contract._source_identity(event)
        self.assertIs(event.observation, original)

    def test_forged_outer_type_has_no_authority(self):
        self.invalid(contract._validate_event, NS(generation=1, sequence=1, observation=legacy()))


def bad_counter_test(field, label, value):
    def test(self):
        self.invalid(qualified, **{field: value})
    setattr(RecordTests, 'test_' + field + '_' + label, test)


for _field in ('generation', 'sequence'):
    for _label, _value in (('bool', True), ('zero', 0), ('negative', -1), ('float', 1.0),
                           ('string', '1'), ('decimal', D(1)), ('missing', None)):
        bad_counter_test(_field, _label, _value)


class NestedValidationTests(PureCase):
    def test_wrong_observation_type(self):
        self.invalid(contract._QualifiedOrderStatusEvidence, 1, 1, NS())

    def test_observation_subclass_rejected(self):
        class Forged(source._OrderStatusObservation): pass
        value = object.__new__(Forged)
        self.invalid(contract._QualifiedOrderStatusEvidence, 1, 1, value)

    def test_wrong_presence_type(self):
        observed = legacy()
        object.__setattr__(observed, 'presence', NS())
        self.invalid(qualified, observation=observed)

    def test_wrong_protocol_type(self):
        observed = legacy()
        object.__setattr__(observed, 'protocol', 'LEGACY')
        self.invalid(qualified, observation=observed)

    def test_false_quality_promotion_rejected(self):
        observed = source._adapt_protobuf_order_status(Proto())
        object.__setattr__(observed.presence, 'order_id', source._OrderStatusSourceQuality.SUPPLIED)
        self.invalid(qualified, observation=observed)

    def test_unqualified_wire_field_rejected(self):
        observed = source._adapt_protobuf_order_status(Proto(**RAW))
        object.__setattr__(observed.presence, 'wire_supplied', frozenset(('unqualified',)))
        self.invalid(qualified, observation=observed)


class IdentityTests(PureCase):
    def test_sequence_excluded_generation_included(self):
        event = qualified()
        self.assertTrue(contract._duplicate_compressible(event, replace(event, sequence=99)))
        self.assertFalse(contract._duplicate_compressible(event, replace(event, generation=2)))

    def test_legacy_and_protobuf_never_compress(self):
        legacy_event = qualified(observation=source._adapt_legacy_order_status(*LEGACY))
        self.assertFalse(contract._duplicate_compressible(legacy_event, qualified()))

    def test_qualified_and_compatibility_view_never_compress(self):
        event = qualified()
        self.assertFalse(contract._duplicate_compressible(event, contract._legacy_projection(event)))

    def test_absent_and_supplied_unavailable_are_distinct(self):
        absent = qualified(raw={})
        supplied = qualified(raw={'filled': str(UNSET)})
        self.assertIsNone(absent.observation.filled)
        self.assertIsNone(supplied.observation.filled)
        self.assertFalse(contract._duplicate_compressible(absent, supplied))

    def test_legacy_ambiguity_preserved(self):
        event = qualified(observation=legacy(average_fill_price=0.0))
        self.assertIn('LEGACY_AMBIGUOUS', str(contract._source_identity(event)))

    def test_identity_has_only_independent_primitive_values(self):
        event = qualified()
        def primitive(value):
            return type(value) in (str, int, type(None)) or type(value) is tuple and all(map(primitive, value))
        self.assertTrue(primitive(contract._source_identity(event)))

    def test_canonical_identity_ignores_ambient_decimal_context(self):
        event = qualified(raw=dict(RAW, filled='4.000000000000000000000000000000001'))
        before = contract._source_identity(event)
        with localcontext() as context:
            context.prec = 1
            context.traps[Inexact] = context.traps[Rounded] = True
            self.assertEqual(contract._source_identity(event), before)
            self.assertFalse(any(context.flags.values()))


def identity_difference_test(label, first, second):
    def test(self):
        left, right = qualified(raw=first), qualified(raw=second)
        self.assertNotEqual(contract._source_identity(left), contract._source_identity(right))
        self.assertFalse(contract._duplicate_compressible(left, right))
    setattr(IdentityTests, 'test_identity_' + label, test)


for _label, _first, _second in (
    ('order_id', RAW, dict(RAW, orderId=11)),
    ('status', RAW, dict(RAW, status='Cancelled')),
    ('filled_digits', RAW, dict(RAW, filled='4.01')),
    ('remaining_digits', RAW, dict(RAW, remaining='6.01')),
    ('filled_exponent', RAW, dict(RAW, filled='4.000')),
    ('remaining_exponent', RAW, dict(RAW, remaining='6.000')),
    ('average_price', RAW, dict(RAW, avgFillPrice=1.5)),
    ('last_price', RAW, dict(RAW, lastFillPrice=1.25)),
    ('perm_id', RAW, dict(RAW, permId=78)),
    ('parent_id', RAW, dict(RAW, parentId=3)),
    ('client_id', RAW, dict(RAW, clientId=3)),
    ('quantity_signed_zero', dict(RAW, filled='0.00'), dict(RAW, filled='-0.00')),
    ('float_derived_signed_zero', dict(RAW, avgFillPrice=0.0), dict(RAW, avgFillPrice=-0.0)),
    ('supplied_zero_vs_absent', {'orderId': 0}, {}),
    ('perm_presence', {'permId': 0}, {}),
):
    identity_difference_test(_label, _first, _second)


class ProjectionTests(PureCase):
    def test_fully_known_protobuf_projects_required_facts(self):
        event = qualified(generation=3, sequence=101)
        projection = contract._legacy_projection(event)
        self.assertIs(type(projection), paper._OrderStatusEvidence)
        self.assertEqual(projection, old_status(sequence=101, generation=3))
        self.assertEqual((projection.generation, projection.sequence), (3, 101))

    def test_unambiguous_legacy_projects(self):
        event = qualified(observation=legacy())
        self.assertEqual(contract._legacy_projection(event).parent_id, 3)

    def test_legacy_decoded_quantity_zero_is_known(self):
        event = qualified(observation=legacy(filled=D('-0.00')))
        projection = contract._legacy_projection(event)
        self.assertEqual(projection.filled.as_tuple(), D('-0.00').as_tuple())

    def test_unknown_status_is_retained_without_finality(self):
        event = qualified(raw=dict(RAW, status='unrecognized broker status'))
        self.assertEqual(event.observation.status, 'UNKNOWN')
        self.assertIsNone(contract._legacy_projection(event))

    def test_perm_id_is_not_required_by_legacy_view(self):
        raw = dict(RAW); del raw['permId']
        event = qualified(raw=raw)
        self.assertIsNotNone(contract._legacy_projection(event))
        self.assertIsNone(event.observation.perm_id)

    def test_projection_keeps_source_and_exact_representation(self):
        event = qualified(raw=dict(RAW, filled='4.000', avgFillPrice=-0.0))
        observed, before = event.observation, contract._source_identity(event)
        projection = contract._legacy_projection(event)
        self.assertIs(event.observation, observed)
        self.assertEqual(contract._source_identity(event), before)
        self.assertEqual(projection.filled.as_tuple(), D('4.000').as_tuple())
        self.assertEqual(projection.average_price.as_tuple(), D('-0.0').as_tuple())
        self.assertIsNot(projection.filled, observed.filled)

    def test_compatibility_is_not_qualified_source_durability(self):
        event = qualified()
        self.assertIsNotNone(contract._legacy_projection(event))
        self.assertIs(contract._consumer_support(event, C.DURABLE_PERSISTENCE), S.UNSUPPORTED_QUALIFIED_STATUS)

    def test_wrong_concrete_primitive_rejected_not_normalized(self):
        event = qualified()
        object.__setattr__(event.observation, 'filled', 4.0)
        self.invalid(contract._legacy_projection, event)


def missing_projection_test(model_name, sdk_name):
    def test(self):
        raw = dict(RAW); del raw[sdk_name]
        event = qualified(raw=raw)
        self.assertIsNone(contract._legacy_projection(event))
        self.assertIsNone(getattr(event.observation, model_name))
    setattr(ProjectionTests, 'test_unavailable_' + model_name, test)


for _model, _sdk in (('order_id', 'orderId'), ('client_id', 'clientId'), ('status', 'status'),
                     ('filled', 'filled'), ('remaining', 'remaining'),
                     ('average_fill_price', 'avgFillPrice'), ('last_fill_price', 'lastFillPrice'),
                     ('parent_id', 'parentId')):
    missing_projection_test(_model, _sdk)


def ambiguous_projection_test(name):
    def test(self):
        value = 0.0 if name in ('average_fill_price', 'last_fill_price') else 0
        self.assertIsNone(contract._legacy_projection(qualified(observation=legacy(**{name: value}))))
    setattr(ProjectionTests, 'test_ambiguous_legacy_' + name, test)


for _name in ('order_id', 'client_id', 'parent_id', 'average_fill_price', 'last_fill_price'):
    ambiguous_projection_test(_name)


def supplied_projection_test(label, changes, field, expected):
    def test(self):
        event = qualified(raw=dict(RAW, **changes))
        projection = contract._legacy_projection(event)
        self.assertEqual(getattr(projection, field), expected)
        self.assertEqual(projection.sequence, event.sequence)
    setattr(ProjectionTests, 'test_supplied_' + label, test)


for _label, _changes, _field, _expected in (
    ('zero_order_id', {'orderId': 0}, 'order_id', 0),
    ('maximum_order_id', {'orderId': 2147483647}, 'order_id', 2147483647),
    ('zero_filled', {'filled': '0'}, 'filled', D(0)),
    ('zero_price', {'avgFillPrice': 0.0}, 'average_price', D('0.0')),
):
    supplied_projection_test(_label, _changes, _field, _expected)


class KnownOrderTests(PureCase):
    def test_absent_order_id(self): self.assertIsNone(contract._known_order_id(qualified(raw={})))
    def test_supplied_zero(self): self.assertEqual(contract._known_order_id(qualified(raw={'orderId': 0})), 0)
    def test_supplied_maximum(self): self.assertEqual(contract._known_order_id(qualified(raw={'orderId': 2147483647})), 2147483647)
    def test_legacy_known(self): self.assertEqual(contract._known_order_id(qualified(observation=legacy())), 10)
    def test_legacy_ambiguous(self): self.assertIsNone(contract._known_order_id(qualified(observation=legacy(order_id=0))))
    def test_perm_id_never_substitutes(self): self.assertIsNone(contract._known_order_id(qualified(raw={'permId': 10})))
    def test_old_evidence_retains_existing_zero(self): self.assertEqual(contract._known_order_id(old_status(order_id=0)), 0)
    def test_commission_is_order_independent(self): self.assertIsNone(contract._known_order_id(commission()))
    def test_matching_id_is_collision(self): self.assertIs(contract._reservation_collision((qualified(),), generation=1, order_id=10), R.COLLISION)
    def test_unknown_id_is_unresolved(self): self.assertIs(contract._reservation_collision((qualified(raw={}),), generation=1, order_id=10), R.UNRESOLVED)
    def test_unrelated_id_is_only_no_observation(self): self.assertIs(contract._reservation_collision((qualified(),), generation=1, order_id=11), R.NO_COLLISION_OBSERVED)
    def test_collision_dominates_unknown(self): self.assertIs(contract._reservation_collision((qualified(raw={}), qualified(sequence=2)), generation=1, order_id=10), R.COLLISION)
    def test_stale_generation_is_not_adopted(self): self.assertIs(contract._reservation_collision((qualified(generation=2),), generation=1, order_id=10), R.NO_COLLISION_OBSERVED)
    def test_commission_never_fabricates_collision(self): self.assertIs(contract._reservation_collision((commission(),), generation=1, order_id=10), R.NO_COLLISION_OBSERVED)
    def test_malformed_event_rejected_even_when_foreign(self): self.invalid(contract._reservation_collision, (old_status(generation=0),), generation=1, order_id=10)


class MixedTests(PureCase):
    def test_exact_union_accepted(self):
        for event in (old_open(), old_status(), old_execution(), commission(), qualified()):
            contract._validate_event(event)

    def test_old_producer_normalized_unknown_is_structurally_valid(self):
        contract._validate_event(old_status(status='UNKNOWN'))

    def test_qualified_unavailable_supported_by_retention_contract(self):
        self.assertIs(contract._consumer_support(qualified(raw={}), C.TRANSPORT), S.SUPPORTED_DIRECT)

    def test_projectable_dispatch_contract(self):
        self.assertIs(contract._consumer_support(qualified(), C.DISPATCH), S.SUPPORTED_VIA_LOSSLESS_PROJECTION)

    def test_projectable_reconciliation_contract(self):
        self.assertIs(contract._consumer_support(qualified(), C.RECONCILIATION), S.SUPPORTED_VIA_LOSSLESS_PROJECTION)

    def test_unprojectable_dispatch_contract(self):
        self.assertIs(contract._consumer_support(qualified(raw={}), C.DISPATCH), S.UNSUPPORTED_QUALIFIED_STATUS)

    def test_unprojectable_reconciliation_contract(self):
        self.assertIs(contract._consumer_support(qualified(raw={}), C.RECONCILIATION), S.UNSUPPORTED_QUALIFIED_STATUS)

    def test_durable_contract_refuses_both_source_shapes(self):
        for event in (qualified(), qualified(raw={})):
            self.assertIs(contract._consumer_support(event, C.DURABLE_PERSISTENCE), S.UNSUPPORTED_QUALIFIED_STATUS)

    def test_old_events_directly_supported_by_durable_contract(self):
        for event in (old_open(), old_status(), old_execution(), commission()):
            self.assertIs(contract._consumer_support(event, C.DURABLE_PERSISTENCE), S.SUPPORTED_DIRECT)

    def test_old_commission_dispatch_remains_unincorporated(self):
        self.assertIs(contract._consumer_support(commission(), C.DISPATCH), S.UNSUPPORTED_EXISTING_EVIDENCE)

    def test_forged_object_rejected_without_getters(self):
        class Raw:
            def __getattr__(self, name): raise AssertionError('raw getter forbidden')
        self.invalid(contract._validate_event, Raw())

    def test_malformed_old_status_not_coerced(self):
        for event in (old_status(client_id=True), old_status(filled=4.0),
                      old_status(status='arbitrary text'), old_status(average_price=D('NaN'))):
            self.invalid(contract._validate_event, event)

    def test_old_optional_normalized_text_is_not_empty(self):
        self.invalid(contract._validate_event, replace(old_open(), order_ref=''))

    def test_invalid_consumer_not_inferred(self):
        self.invalid(contract._consumer_support, qualified(), 'DISPATCH')


class CoverageTests(PureCase):
    def test_supported_contiguous_range(self):
        result = coverage((old_open(100), old_status(101), old_execution(102)), cursor=99)
        self.assertTrue(result.complete)
        self.assertEqual(result.supported_through_sequence, 102)

    def test_unsupported_first(self):
        result = coverage((qualified(raw={}), old_status(2)))
        self.assertEqual((result.supported_through_sequence, result.first_unsupported_sequence), (0, 1))

    def test_supported_suffix_never_bypasses_unsupported_middle(self):
        result = coverage((old_status(100), qualified(sequence=101, raw={}), old_execution(102)), cursor=99)
        self.assertEqual(result.supported_through_sequence, 100)
        self.assertEqual(result.first_unsupported_sequence, 101)
        self.assertIs(result.state, K.UNSUPPORTED)

    def test_unsupported_last(self):
        result = coverage((old_status(), qualified(sequence=2, raw={})))
        self.assertEqual(result.supported_through_sequence, 1)
        self.assertFalse(result.complete)

    def test_target_before_unsupported(self):
        result = coverage((old_status(), qualified(sequence=2, raw={})), target=1)
        self.assertTrue(result.complete)
        self.assertIsNone(result.first_unsupported_sequence)

    def test_projectable_status_still_blocks_durable_progress(self):
        result = coverage((old_status(), qualified(sequence=2), old_execution(3)), consumer=C.DURABLE_PERSISTENCE)
        self.assertEqual(result.supported_through_sequence, 1)
        self.assertEqual(result.first_unsupported_sequence, 2)

    def test_transport_contract_covers_unavailable_status(self):
        self.assertTrue(coverage((qualified(raw={}),), consumer=C.TRANSPORT).complete)

    def test_compatibility_coverage_preserves_projected_occurrence_sequence(self):
        self.assertTrue(coverage((old_status(), qualified(sequence=2), old_execution(3))).complete)

    def test_sequence_gap_before_supported_suffix(self):
        result = coverage((old_status(), old_status(3)))
        self.assertEqual((result.supported_through_sequence, result.first_missing_sequence), (1, 2))
        self.assertIs(result.state, K.GAP)

    def test_overflow_watermark_is_incomplete(self):
        events = tuple(old_status(n) for n in range(1, 129))
        result = coverage(events, through=129)
        self.assertEqual(result.supported_through_sequence, 128)
        self.assertIs(result.state, K.INCOMPLETE)
        self.assertEqual(result.first_missing_sequence, 129)

    def test_empty_complete_range(self): self.assertTrue(coverage((), cursor=99).complete)
    def test_empty_unseen_watermark_never_acknowledged(self): self.assertFalse(coverage((), through=1).complete)

    def test_generation_mismatch_stops_prefix(self):
        result = coverage((old_status(), qualified(sequence=2, generation=2), old_status(3)))
        self.assertIs(result.state, K.GENERATION_MISMATCH)
        self.assertEqual(result.supported_through_sequence, 1)

    def test_nonzero_requested_cursor(self):
        result = coverage((old_status(5),), cursor=4)
        self.assertEqual((result.requested_cursor, result.supported_through_sequence), (4, 5))

    def test_coverage_result_is_opaque_frozen_slotted(self):
        result = coverage((old_status(),))
        self.assertEqual(repr(result), '<PrivateQualifiedStatusLedgerValue>')
        self.assertFalse(hasattr(result, '__dict__'))
        with self.assertRaises(FrozenInstanceError): result.supported_through_sequence = 9

    def test_no_input_or_cursor_mutation(self):
        event = qualified(raw={})
        before = contract._source_identity(event)
        for _ in range(3): coverage((event, old_status(2)))
        self.assertEqual(contract._source_identity(event), before)
        self.assertEqual(event.sequence, 1)

    def test_invalid_suffix_is_not_hidden_by_unsupported_prefix(self):
        self.invalid(coverage, (qualified(raw={}), old_status(2, filled=D('NaN'))))


def bad_coverage_test(label, events, changes):
    def test(self): self.invalid(coverage, events, **changes)
    setattr(CoverageTests, 'test_invalid_' + label, test)


for _label, _events, _changes in (
    ('duplicate_sequence', (old_status(), old_status()), {}),
    ('decreasing_sequence', (old_status(2), old_status()), {}),
    ('before_cursor', (old_status(),), {'cursor': 1}),
    ('beyond_watermark', (old_status(2),), {'through': 1}),
    ('target_beyond_watermark', (old_status(),), {'target': 2}),
    ('target_before_cursor', (), {'cursor': 1, 'target': 0}),
    ('bool_cursor', (), {'cursor': True}),
    ('bool_generation', (), {'generation': True}),
    ('bool_watermark', (), {'through': True}),
):
    bad_coverage_test(_label, _events, _changes)


class RunTests(PureCase):
    def test_qualified_run_preserves_type_source_and_each_sequence(self):
        event = qualified()
        batch = contract._expand_runs((event,), (3,), cursor=0, through_sequence=3)
        self.assertEqual(tuple(e.sequence for e in batch.events), (1, 2, 3))
        for expanded in batch.events:
            self.assertIs(type(expanded), contract._QualifiedOrderStatusEvidence)
            self.assertEqual(contract._source_identity(expanded), contract._source_identity(event))

    def test_interleaved_runs_keep_qualified_type(self):
        batch = contract._expand_runs((old_status(), qualified(sequence=3), old_execution(5)),
                                     (2, 4, 5), cursor=0, through_sequence=5)
        self.assertEqual(tuple(e.sequence for e in batch.events), (1, 2, 3, 4, 5))
        self.assertIs(type(batch.events[2]), contract._QualifiedOrderStatusEvidence)

    def test_cursor_inside_qualified_span(self):
        batch = contract._expand_runs((qualified(),), (5,), cursor=3, through_sequence=5)
        self.assertEqual(tuple(e.sequence for e in batch.events), (4, 5))

    def test_fetch_is_bounded_for_huge_duplicate_span(self):
        batch = contract._expand_runs((qualified(),), (10**30,), cursor=0, through_sequence=10**30)
        self.assertEqual(len(batch.events), 128)
        self.assertEqual(batch.through_sequence, 10**30)

    def test_128_retained_runs_share_capacity(self):
        events = tuple(qualified(sequence=n) if n % 2 else old_status(n) for n in range(1, 129))
        batch = contract._expand_runs(events, tuple(range(1, 129)), cursor=0, through_sequence=128)
        self.assertEqual(len(batch.events), 128)

    def test_overflow_prefix_cannot_fill_unseen_tail(self):
        batch = contract._expand_runs((old_status(),), (128,), cursor=128, through_sequence=129)
        self.assertEqual(batch.events, ())
        self.assertFalse(coverage(batch.events, cursor=128, through=129).complete)

    def test_fetch_does_not_acknowledge_or_clear(self):
        event = qualified()
        one = contract._expand_runs((event,), (3,), cursor=0, through_sequence=3)
        two = contract._expand_runs((event,), (3,), cursor=0, through_sequence=3)
        self.assertEqual(one, two)
        self.assertEqual(event.sequence, 1)

    def test_unsupported_expanded_span_stops_before_first_occurrence(self):
        batch = contract._expand_runs((old_status(), qualified(sequence=3, raw={})),
                                     (2, 5), cursor=0, through_sequence=5)
        result = coverage(batch.events)
        self.assertEqual(result.supported_through_sequence, 2)

    def test_empty_complete_ledger(self):
        self.assertEqual(contract._expand_runs((), (), cursor=0, through_sequence=0).events, ())

    def test_malformed_starts_not_renumbered(self):
        self.invalid(contract._expand_runs, (qualified(sequence=2),), (3,), cursor=0, through_sequence=3)

    def test_overlap_rejected(self):
        self.invalid(contract._expand_runs, (qualified(), old_status(2)), (2, 3), cursor=0, through_sequence=3)

    def test_end_before_start_rejected(self):
        self.invalid(contract._expand_runs, (qualified(), old_status(2)), (1, 1), cursor=0, through_sequence=2)

    def test_end_beyond_watermark_rejected(self):
        self.invalid(contract._expand_runs, (qualified(),), (3,), cursor=0, through_sequence=2)

    def test_excess_retained_capacity_rejected(self):
        self.invalid(contract._expand_runs, tuple(old_status(n) for n in range(1, 130)),
                     tuple(range(1, 130)), cursor=0, through_sequence=129)

    def test_metadata_length_mismatch_rejected(self):
        self.invalid(contract._expand_runs, (qualified(),), (), cursor=0, through_sequence=1)


class BoundaryTests(PureCase):
    def test_all_helpers_run_without_external_activity(self):
        event = qualified()
        contract._source_identity(event)
        contract._legacy_projection(event)
        contract._known_order_id(event)
        contract._consumer_support(event, C.DURABLE_PERSISTENCE)
        coverage((event,), consumer=C.DURABLE_PERSISTENCE)
        contract._expand_runs((event,), (2,), cursor=0, through_sequence=2)

    def test_no_public_export_or_authority_fields(self):
        import tradingbot_broker
        self.assertEqual(contract.__all__, ())
        self.assertFalse(hasattr(tradingbot_broker, '_QualifiedOrderStatusEvidence'))
        self.assertEqual({f.name for f in fields(qualified())}, {'generation', 'sequence', 'observation'})

    def test_no_storage_network_clock_thread_or_sdk_import_in_module(self):
        text = Path(contract.__file__).read_text()
        for forbidden in ('sqlite3', 'paper_dispatch_journal', 'paper_execution_journal',
                          'socket', 'requests', 'threading', 'datetime.now', '_load_paper_api',
                          '_StatusHandoff', '_issue_handoff_receiver', '_accept_handoff'):
            self.assertNotIn(forbidden, text)

    def test_qualified_source_durability_uses_owner_adapter_not_storage_import(self):
        self.assertIn('paper_qualified_status_journal',
            Path(contract.__file__).with_name('paper_dispatch_journal.py').read_text())
        for path in ('paper_execution_journal.py', 'paper_execution_journal_sqlite.py'):
            self.assertNotIn('ibkr_qualified_status_ledger',
                            Path(contract.__file__).with_name(path).read_text())
        for path in ('ibkr_paper_transport.py', 'paper_dispatch.py', 'paper_reconciliation.py'):
            self.assertIn('ibkr_qualified_status_ledger',
                          Path(contract.__file__).with_name(path).read_text())

    def test_transport_state_and_receiver_remain_unchanged(self):
        api = sdk()
        with patch.object(paper, '_load_paper_api', return_value=api):
            transport = paper.PaperTWSTransport(paper.PaperTWSConfig(paper.PaperTransportIntent.LOCAL_PAPER_FOUNDATION))
        transport._begin_generation()
        before = (transport._callback_sequence, tuple(transport._callback_evidence),
                  transport._arrival_sequence, transport._reconciliation_required)
        event = qualified(generation=transport._generation)
        contract._legacy_projection(event)
        coverage((event,))
        self.assertEqual(before, (transport._callback_sequence, tuple(transport._callback_evidence),
                                 transport._arrival_sequence, transport._reconciliation_required))
        self.assertIsNone(transport._status_delivery_owner._PaperStatusDelivery__receiver)
        self.assertIs(transport.account_mode, AccountMode.UNKNOWN)

    def test_old_status_shape_unchanged(self):
        self.assertEqual(tuple(f.name for f in fields(paper._OrderStatusEvidence)),
            ('generation', 'sequence', 'order_id', 'client_id', 'status', 'filled',
             'remaining', 'average_price', 'last_price', 'parent_id'))

    def test_thread_neutral_pure_contract(self):
        event = qualified()
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(contract._source_identity, (event,) * 8))
        self.assertTrue(all(result == results[0] for result in results))

    def test_ordinary_failure_sanitized_without_raw_text(self):
        event = qualified()
        with patch.object(source._OrderStatusObservation, '__post_init__',
                          side_effect=ValueError('untrusted payload')):
            self.invalid(contract._source_identity, event)


class InterruptionTests(PureCase):
    pass


def interruption_test(name, exception_type):
    def test(self):
        event = qualified()
        original, identity = exception_type('original interruption'), contract._source_identity(event)
        with patch.object(source._OrderStatusObservation, '__post_init__', side_effect=original):
            with self.assertRaises(exception_type) as caught:
                contract._legacy_projection(event)
        self.assertIs(caught.exception, original)
        self.assertEqual(contract._source_identity(event), identity)
    setattr(InterruptionTests, 'test_' + name + '_identity_preserved', test)


for _name, _exception in (('keyboard_interrupt', KeyboardInterrupt), ('system_exit', SystemExit),
                          ('generator_exit', GeneratorExit)):
    interruption_test(_name, _exception)
