"""PRE8 adversarial publication. Anonymous decoder/client doubles, no broker I/O."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from decimal import Decimal as D
import inspect
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import patch

from tradingbot_broker import ibkr_paper_transport as paper
from tradingbot_broker import ibkr_paper_status_delivery as delivery
from tradingbot_broker import ibkr_order_status_pairing as pairing
from tradingbot_broker import ibkr_order_status_evidence as source
from tradingbot_broker import ibkr_qualified_status_ledger as q
from tradingbot_broker import paper_dispatch as dispatch
from tradingbot_broker import paper_reconciliation as reconciliation
from tradingbot_broker import paper_dispatch_journal as owner_module
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker import paper_execution_journal as journal
from tradingbot_broker.readonly_models import ReadOnlyError, AccountMode
from tests import test_ibkr_paper_status_delivery as sdk_fixture
from tests import test_ibkr_qualified_status_ledger as values
from tests import test_paper_callback_persistence as persistence
from tests import test_paper_reconciliation as recon_fixture
from tests.test_paper_reconciliation_commission_compatibility import page


Q = q._QualifiedOrderStatusEvidence
RAW, LEGACY, Proto = sdk_fixture.RAW, sdk_fixture.LEGACY, sdk_fixture.Proto
EXCEPTIONS = (KeyboardInterrupt, SystemExit, GeneratorExit, ValueError)


class PublicationCase(sdk_fixture.Case):
    def emit(self, f, protocol='protobuf', fact=10):
        if protocol == 'protobuf': return self.protobuf(f, orderId=fact)
        return self.legacy(f, (fact, *LEGACY[1:]))

    def fill(self, f, protocol='protobuf', count=128):
        for index in range(count): self.emit(f, protocol, 2000 + index)

    def truthful(self, f):
        with f.transport._arrival_lock: f.transport._check_callback_evidence()

    def failed(self, f):
        self.assertTrue(f.transport._reconciliation_required)
        self.assertTrue(f.transport._arrival_unusable)
        self.assertFalse(f.transport._status_registration_eligible)
        self.assertEqual(f.transport._status_fences, [])
        self.assertEqual(f.owner._PaperStatusDelivery__entries, [])
        self.truthful(f)


_SOURCES = {
    'protobuf_complete': ('protobuf', RAW),
    'protobuf_empty': ('protobuf', {}),
    'protobuf_zero_id': ('protobuf', dict(RAW, orderId=0)),
    'protobuf_max_id': ('protobuf', dict(RAW, orderId=paper.MAX_ORDER_ID)),
    'protobuf_positive_id': ('protobuf', dict(RAW, orderId=100)),
    'protobuf_absent_id': ('protobuf', {k: v for k, v in RAW.items() if k != 'orderId'}),
    'protobuf_absent_quantities': ('protobuf', {k: v for k, v in RAW.items() if k not in ('filled', 'remaining')}),
    'protobuf_absent_prices': ('protobuf', {k: v for k, v in RAW.items() if k not in ('avgFillPrice', 'lastFillPrice')}),
    'protobuf_unknown_status': ('protobuf', dict(RAW, status='future-status')),
    'protobuf_zero_quantities': ('protobuf', dict(RAW, filled='0', remaining='0')),
    'protobuf_signed_zero': ('protobuf', dict(RAW, filled='-0.00', avgFillPrice=-0.0)),
    'protobuf_decimal_shape': ('protobuf', dict(RAW, filled='4.000', remaining='6.00')),
    'protobuf_unavailable_quantity': ('protobuf', dict(RAW, filled=str(source._UNSET_DECIMAL))),
    'protobuf_unavailable_price': ('protobuf', dict(RAW, avgFillPrice=source._UNSET_DOUBLE)),
    'protobuf_absent_perm': ('protobuf', {k: v for k, v in RAW.items() if k != 'permId'}),
    'protobuf_max_perm': ('protobuf', dict(RAW, permId=(1 << 63) - 1)),
    'legacy_normal': ('legacy', LEGACY),
    'legacy_zero_id': ('legacy', (0, *LEGACY[1:])),
    'legacy_projectable': ('legacy', tuple(3 if i == 6 else x for i, x in enumerate(LEGACY))),
    'legacy_unknown_status': ('legacy', tuple('future-status' if i == 1 else x for i, x in enumerate(LEGACY))),
    'legacy_zero_prices': ('legacy', tuple(0.0 if i in (4, 7) else x for i, x in enumerate(LEGACY))),
    'legacy_zero_client_parent': ('legacy', tuple(0 if i in (6, 8) else x for i, x in enumerate(LEGACY))),
    'legacy_unavailable_quantity': ('legacy', tuple(source._UNSET_DECIMAL if i == 2 else x for i, x in enumerate(LEGACY))),
    'legacy_decimal_shape': ('legacy', tuple(D('4.000') if i == 2 else x for i, x in enumerate(LEGACY))),
}


class SourceTests(PublicationCase):
    pass


class MalformedSourceTests(PublicationCase):
    pass


def malformed_case(protocol, changes):
    def test(self):
        f = self.build()
        with self.assertRaises(ReadOnlyError):
            if protocol == 'protobuf':
                # Invalid Python primitive shapes cannot be encoded by this
                # qualified wire fixture. Inject only its decoded raw test value.
                raw = Proto(**dict(RAW, **changes))
                with patch.object(Proto, 'FromString', return_value=raw): self.protobuf(f)
            else:
                adapted = list(LEGACY)
                for index, value in changes.items(): adapted[index] = value
                self.legacy(f, tuple(adapted))
        self.assertEqual(f.transport._callback_sequence, 0)
        self.assertIsNone(f.transport._callback_evidence_lost_at)
        self.assertEqual(f.transport._callback_evidence, [])
        self.failed(f)
    return test


for _name, _protocol, _changes in (
    ('proto_bool_id', 'protobuf', {'orderId': True}),
    ('proto_negative_id', 'protobuf', {'orderId': -1}),
    ('proto_negative_fill', 'protobuf', {'filled': '-1'}),
    ('proto_nan_fill', 'protobuf', {'filled': 'NaN'}),
    ('proto_wrong_quantity_type', 'protobuf', {'filled': 4}),
    ('proto_infinite_price', 'protobuf', {'avgFillPrice': float('inf')}),
    ('legacy_bool_id', 'legacy', {0: True}),
    ('legacy_wrong_quantity', 'legacy', {2: 4.0}),
    ('legacy_negative_price', 'legacy', {4: -1.0}),
    ('legacy_nan_quantity', 'legacy', {2: D('NaN')}),
):
    setattr(MalformedSourceTests, f'test_{_name}', malformed_case(_protocol, _changes))


def source_case(protocol, payload, facet):
    def test(self):
        f = self.build()
        states = []
        original = delivery._PaperStatusDelivery._accept_handoff
        def accepted(owner, handoff, receiver):
            entry = owner._PaperStatusDelivery__entries[-1]
            self.assertTrue(entry.publication_attempted and entry.publication_complete)
            self.assertEqual(entry.publication_sequence, 1)
            self.assertEqual(len(f.transport._status_fences), 1)
            self.assertEqual(f.transport._callback_sequence, 1)
            self.assertFalse(f.transport._arrival_lock._is_owned())
            states.append(True)
            return original(owner, handoff, receiver)
        with patch.object(delivery._PaperStatusDelivery, '_accept_handoff', accepted):
            if protocol == 'protobuf': f.decoder.processProtoBuf(Proto(**payload).SerializeToString(), 37)
            else: self.legacy(f, payload)
        self.assertEqual(states, [True])
        event, = f.transport._callback_evidence
        self.assertIs(type(event), Q)
        self.assertEqual((event.generation, event.sequence), (1, 1))
        expected = (source._adapt_protobuf_order_status(Proto(**payload)) if protocol == 'protobuf'
                    else source._adapt_legacy_order_status(*payload))
        self.assertEqual(q._source_identity(event), q._source_identity(Q(1, 1, expected)))
        self.assertIsNot(event.observation, f.handoffs[0].observation)
        self.assertIsNot(event.observation.presence, f.handoffs[0].observation.presence)
        known = q._known_order_id(event)
        self.assertEqual(f.transport._lifetime_order_floor, 0 if known is None else min(known + 1, paper.MAX_ORDER_ID))
        if facet == 'privacy':
            self.assertEqual(tuple(field.name for field in fields(event)), ('generation', 'sequence', 'observation'))
            self.assertNotIn('future-status', repr(event))
            self.assertFalse(hasattr(event, 'delivery'))
            self.assertFalse(hasattr(event, 'order_id'))
        self.assertEqual(f.transport._arrival_sequence, 1)
        self.assertEqual(f.transport._callback_run_ends, [1])
        self.assertEqual(f.transport._status_fences, [])
        self.truthful(f)
    return test


for _name, (_protocol, _payload) in _SOURCES.items():
    for _facet in ('model', 'privacy'):
        setattr(SourceTests, f'test_{_name}_{_facet}', source_case(_protocol, _payload, _facet))


class ReceiverTests(PublicationCase):
    def test_receiver_armed_before_ready_publication(self):
        f = self.build(connect=False)
        original = f.transport._status_registration_ready
        def ready(owner, registration):
            self.assertIsNotNone(owner._PaperStatusDelivery__receiver)
            self.assertIsNotNone(owner._PaperStatusDelivery__publisher)
            self.assertIs(owner._PaperStatusDelivery__receiver.registration, registration)
            self.assertFalse(f.transport._status_registration_eligible)
            return original(owner, registration)
        with patch.object(f.transport, '_status_registration_ready', side_effect=ready): self.connect(f)

    def test_publisher_exact_reviewed_transport_method(self):
        f = self.build()
        publisher = f.owner._PaperStatusDelivery__publisher
        self.assertIs(publisher.__self__, f.transport)
        self.assertIs(publisher.__func__, paper.PaperTWSTransport._publish_qualified_status)

    def test_no_caller_callback_constructor_or_replacement(self):
        f = self.build()
        with self.assertRaises(ReadOnlyError): f.owner.publisher = lambda *args: None
        with self.assertRaises(ReadOnlyError): delivery._PaperStatusDelivery(lambda: None)
        with self.assertRaises(ReadOnlyError): f.owner._issue_handoff_receiver()

    def test_contract_capture_immutable(self):
        f = self.build()
        for value in (None, NS(), object()):
            with self.assertRaises(ReadOnlyError): f.transport._PaperTWSTransport__status_contract = value
        with self.assertRaises(ReadOnlyError): del f.transport._PaperTWSTransport__status_contract

    def test_bound_publisher_survives_instance_attribute_replacement(self):
        f = self.build()
        f.transport._publish_qualified_status = lambda *args: (_ for _ in ()).throw(AssertionError('mutable hook'))
        self.protobuf(f)
        self.assertEqual(f.transport._callback_sequence, 1)

    def test_duplicate_receive_does_not_allocate_twice(self):
        f = self.build()
        original = delivery._PaperStatusDelivery._receive_handoff
        def duplicate(owner, handoff, receiver):
            original(owner, handoff, receiver)
            original(owner, handoff, receiver)
        with patch.object(delivery._PaperStatusDelivery, '_receive_handoff', duplicate), self.assertRaises(ReadOnlyError):
            self.protobuf(f)
        self.assertEqual(f.transport._callback_sequence, 1)
        self.assertIsNone(f.transport._callback_evidence_lost_at)
        self.failed(f)

    def test_duplicate_direct_publisher_does_not_allocate_twice(self):
        f = self.build()
        original = f.transport._commit_callback_occurrence
        def committed(event, identity, known):
            result = original(event, identity, known)
            entry = f.owner._PaperStatusDelivery__entries[-1]
            with self.assertRaises(ReadOnlyError):
                paper.PaperTWSTransport._publish_qualified_status(f.transport, f.owner, f.receiver, entry.handoff, entry)
            return result
        with patch.object(f.transport, '_commit_callback_occurrence', side_effect=committed), self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.assertEqual(f.transport._callback_sequence, 1)
        self.assertTrue(f.transport._reconciliation_required)

    def test_equal_handoff_reconstruction_cannot_publish(self):
        f = self.build(); original = delivery._PaperStatusDelivery._receive_handoff
        def forged(owner, handoff, receiver): return original(owner, replace(handoff), receiver)
        with patch.object(delivery._PaperStatusDelivery, '_receive_handoff', forged), self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.assertEqual(f.transport._callback_sequence, 0); self.failed(f)

    def test_equal_receiver_reconstruction_cannot_publish(self):
        f = self.build(); original = delivery._PaperStatusDelivery._receive_handoff
        def forged(owner, handoff, receiver): return original(owner, handoff, replace(receiver))
        with patch.object(delivery._PaperStatusDelivery, '_receive_handoff', forged), self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.assertEqual(f.transport._callback_sequence, 0); self.failed(f)

    def test_foreign_receiver_cannot_publish(self):
        f, foreign = self.build(), self.build(); original = delivery._PaperStatusDelivery._receive_handoff
        def forged(owner, handoff, receiver): return original(owner, handoff, foreign.receiver)
        with patch.object(delivery._PaperStatusDelivery, '_receive_handoff', forged), self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.assertEqual(f.transport._callback_sequence, 0); self.failed(f)

    def test_acceptance_requires_completed_capture(self):
        f = self.build(); original = delivery._PaperStatusDelivery._make_handoff
        def early(owner, entry, observation):
            handoff = original(owner, entry, observation)
            owner._accept_handoff(handoff, f.receiver)
            return handoff
        with patch.object(delivery._PaperStatusDelivery, '_make_handoff', early), self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.assertEqual(f.transport._callback_sequence, 0); self.failed(f)

    def test_acceptance_rejects_changed_sequence(self):
        f = self.build(); original = delivery._PaperStatusDelivery._accept_handoff
        def changed(owner, handoff, receiver):
            owner._PaperStatusDelivery__entries[-1].publication_sequence += 1
            return original(owner, handoff, receiver)
        with patch.object(delivery._PaperStatusDelivery, '_accept_handoff', changed), self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.assertEqual(f.transport._callback_sequence, 1)
        self.assertIsNone(f.transport._callback_evidence_lost_at); self.failed(f)

    def test_entry_publication_fields_erased_after_exit(self):
        f = self.build(); entries = []; original = delivery._PaperStatusDelivery._make_handoff
        def observed(owner, entry, observation):
            entries.append(entry); return original(owner, entry, observation)
        with patch.object(delivery._PaperStatusDelivery, '_make_handoff', observed): self.protobuf(f)
        entry, = entries
        self.assertFalse(entry.publication_attempted or entry.publication_started or entry.publication_complete)
        self.assertIsNone(entry.publication_sequence); self.assertIsNone(entry.publication_result)

    def test_stale_handoff_after_exit_cannot_republish(self):
        f = self.build(); self.protobuf(f)
        with self.assertRaises(ReadOnlyError): f.owner._receive_handoff(f.handoffs[0], f.receiver)
        self.assertEqual(f.transport._callback_sequence, 1); self.failed(f)

    def test_new_generation_distinct_receiver_preserves_lifetime_sequence(self):
        f = self.build(); self.protobuf(f); old = f.owner; receiver = f.receiver
        f.transport._begin_generation(); client = f.api.clients[-1]
        with patch.object(pairing, '_load_pairing_sdk', return_value=(sdk_fixture.Decoder, sdk_fixture.Incoming)): client.connect()
        new = f.transport._status_delivery_owner
        self.assertIsNot(new, old); self.assertIsNot(new._PaperStatusDelivery__receiver, receiver)
        self.assertEqual(new._PaperStatusDelivery__receiver.registration.generation, 2)
        with self.assertRaises(ReadOnlyError): old._receive_handoff(f.handoffs[0], receiver)
        self.assertEqual(f.transport._callback_sequence, 1)
        client.decoder.processProtoBuf(Proto(**RAW).SerializeToString(), 37)
        self.assertEqual(f.transport._callback_sequence, 2)
        self.assertEqual([e.generation for e in f.transport._callback_evidence], [1, 2])

    def test_no_old_status_route_for_registered_protocols(self):
        f = self.build(); original = f.transport._callback; kinds = []
        def callback(generation, kind, value=None):
            kinds.append(kind); return original(generation, kind, value)
        with patch.object(f.transport, '_callback', side_effect=callback): self.protobuf(f); self.legacy(f)
        self.assertNotIn('status', kinds)
        self.assertEqual(f.transport._callback_sequence, 2)


class SpanTests(PublicationCase):
    pass


def span_case(protocol, scenario):
    def test(self):
        f = self.build()
        if scenario == 'adjacent':
            self.emit(f, protocol); self.emit(f, protocol)
            self.assertEqual(len(f.transport._callback_evidence), 1)
            self.assertEqual(f.transport._callback_run_ends, [2])
        elif scenario == 'interleaved':
            for fact in (10, 11, 10): self.emit(f, protocol, fact)
            self.assertEqual(f.transport._callback_run_ends, [1, 2, 3])
        elif scenario == 'long_span':
            for _ in range(300): self.emit(f, protocol)
            page_value = q._expand_runs(tuple(f.transport._callback_evidence), tuple(f.transport._callback_run_ends), cursor=0, through_sequence=300)
            self.assertEqual(len(page_value.events), 128)
            self.assertTrue(all(type(e) is Q for e in page_value.events))
            self.assertEqual([e.sequence for e in page_value.events], list(range(1, 129)))
        elif scenario == '127_append':
            self.fill(f, protocol, 127); self.emit(f, protocol, 2127)
            self.assertEqual(len(f.transport._callback_evidence), 128)
        elif scenario == 'capacity_duplicate':
            self.fill(f, protocol); self.emit(f, protocol, 2127)
            self.assertIsNone(f.transport._callback_evidence_lost_at)
            self.assertEqual(f.transport._callback_run_ends[-1], 129)
        else:
            self.fill(f, protocol)
            prefix, ends = tuple(f.transport._callback_evidence), tuple(f.transport._callback_run_ends)
            if scenario == 'existing_loss':
                f.client.wrapper.commissionReport(NS(execId='overflow', commission=D('0.1'), currency='USD'))
                self.assertEqual(f.transport._callback_evidence_lost_at, 129)
            with self.assertRaises(ReadOnlyError): self.emit(f, protocol, 9000)
            self.assertEqual(f.transport._callback_evidence_lost_at, 129)
            self.assertEqual(tuple(f.transport._callback_evidence), prefix)
            self.assertEqual(tuple(f.transport._callback_run_ends), ends)
            self.assertEqual(f.transport._callback_sequence, 130 if scenario == 'existing_loss' else 129)
            with self.assertRaises(ReadOnlyError): self.emit(f, protocol, 2127)
            self.assertEqual(tuple(f.transport._callback_run_ends), ends)
            self.failed(f)
        self.truthful(f)
    return test


for _protocol in ('protobuf', 'legacy'):
    for _scenario in ('adjacent', 'interleaved', 'long_span', '127_append', 'capacity_duplicate',
                      'capacity_loss', 'existing_loss', 'cannot_repair'):
        setattr(SpanTests, f'test_{_protocol}_{_scenario}', span_case(_protocol, _scenario))


class FailureTests(PublicationCase):
    pass


def failure_case(seam, exception, duplicate):
    def test(self):
        f = self.build()
        if duplicate and seam != 'loss': self.protobuf(f)
        if seam == 'loss':
            self.fill(f)
            if duplicate: self.emit(f, fact=2127)
        before = f.transport._callback_sequence
        prefix, ends = tuple(f.transport._callback_evidence), tuple(f.transport._callback_run_ends)
        error = exception('private payload must not escape')
        target, name = (Q, '__post_init__') if seam == 'construction' else (
            (q, '_validate_qualified') if seam == 'validation' else (
                (delivery._PaperStatusDelivery, '_accept_handoff') if seam == 'acceptance' else (
                    f.transport, {'identity': '_callback_content_identity',
                        'publish_before': '_publish_callback_evidence', 'publish_after': '_publish_callback_evidence',
                        'watermark_before': '_advance_callback_sequence', 'watermark_after': '_advance_callback_sequence',
                        'floor': '_raise_observed_floor', 'invariant_after': '_check_callback_evidence',
                        'loss': '_record_callback_evidence_loss'}[seam])))
        original, calls = getattr(target, name), []
        def fail(*args, **kwargs):
            calls.append(True)
            if seam in ('validation', 'invariant_after') and len(calls) == 1:
                return original(*args, **kwargs)
            if seam.endswith('_after') or seam == 'acceptance': original(*args, **kwargs)
            raise error
        with patch.object(target, name, new=fail):
            if isinstance(error, Exception):
                with self.assertRaises(ReadOnlyError) as caught:
                    self.emit(f, fact=9000) if seam == 'loss' else self.protobuf(f)
                self.assertNotIn('private payload', str(caught.exception))
            else:
                with self.assertRaises(exception) as caught:
                    self.emit(f, fact=9000) if seam == 'loss' else self.protobuf(f)
                self.assertIs(caught.exception, error)
        self.assertTrue(calls)
        self.assertEqual(f.transport._callback_sequence, before + 1)
        if seam == 'acceptance':
            self.assertIsNone(f.transport._callback_evidence_lost_at)
            self.assertEqual(f.transport._callback_run_ends[-1], before + 1)
        else:
            self.assertEqual(f.transport._callback_evidence_lost_at, before + 1)
            self.assertEqual(tuple(f.transport._callback_evidence), prefix)
            self.assertEqual(tuple(f.transport._callback_run_ends), ends)
        watermark = f.transport._callback_sequence
        with self.assertRaises(ReadOnlyError): self.protobuf(f)
        self.assertEqual(f.transport._callback_sequence, watermark)
        self.failed(f)
    return test


for _seam in ('construction', 'validation', 'identity', 'publish_before', 'publish_after',
              'watermark_before', 'watermark_after', 'floor', 'invariant_after', 'loss', 'acceptance'):
    for _exception in EXCEPTIONS:
        for _duplicate in (False, True):
            setattr(FailureTests, f'test_{_seam}_{_exception.__name__}_{"duplicate" if _duplicate else "append"}',
                    failure_case(_seam, _exception, _duplicate))


class RuntimeCase(persistence.PersistenceFixture):
    def registered(self, pending=False):
        api = sdk_fixture.lifecycle_api()
        original = paper._make_paper_client
        def connected(api_value, transport, generation):
            view = original(api_value, transport, generation)
            api_value.clients[-1].connect()  # Anonymous fake base, never EClient.connect.
            return view
        for name in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.create_connection', 'socket.getaddrinfo'):
            self.enterContext(patch(name, side_effect=AssertionError('network')))
        with patch.object(pairing, '_load_pairing_sdk', return_value=(sdk_fixture.Decoder, sdk_fixture.Incoming)), \
             patch.object(paper, '_make_paper_client', side_effect=connected):
            self.build(paper_api=api)
        if pending:
            result = self.invoke()
            self.assertEqual(len(self.write_calls()), 1)
            self.pending_id = result.order_id
        return self.write._status_delivery_owner

    def qualified(self, **changes):
        self.client.decoder.processProtoBuf(Proto(**dict(RAW, **changes)).SerializeToString(), 37)


class ConsumerTests(RuntimeCase):
    pass


_UNPROJECTABLE = {'absent_id': {'orderId': None}, 'absent_quantity': {'filled': None},
                 'absent_price': {'avgFillPrice': None}, 'unknown_status': {'status': 'future'},
                 'absent_client': {'clientId': None}, 'absent_parent': {'parentId': None}}


def dispatch_stop_case(case, pending):
    def test(self):
        self.registered(pending)
        raw = dict(RAW)
        for key, value in _UNPROJECTABLE[case].items():
            if value is None: raw.pop(key, None)
            else: raw[key] = value
        self.client.decoder.processProtoBuf(Proto(**raw).SerializeToString(), 37)
        self.qualified(orderId=11)
        with self.coordinator._lock, self.broker._lock:
            result = self.coordinator._sync_callbacks_locked()
        self.assertEqual(self.coordinator._callback_cursor, 0)
        self.assertTrue(result.reconciliation_required)
        self.assertEqual(self.journal._persistence_cursor, 0)
        self.assertIs(type(self.write._callback_evidence[0]), Q)
    return test


for _case in _UNPROJECTABLE:
    for _pending in (False, True):
        setattr(ConsumerTests, f'test_{_case}_{"pending" if _pending else "pre_entry"}', dispatch_stop_case(_case, _pending))


class ConsumerSuccessTests(RuntimeCase):
    def test_projectable_pending_view_never_replaces_source(self):
        self.registered(True); self.open()
        self.qualified(orderId=self.pending_id)
        with self.coordinator._lock, self.broker._lock: self.coordinator._sync_callbacks_locked()
        self.assertEqual(self.coordinator._callback_cursor, 2)
        self.assertIs(type(self.write._callback_evidence[-1]), Q)
        self.assertEqual(self.coordinator._execution_total, D(0))
        self.assertTrue(self.coordinator._broker_state_reconciliation_required)

    def test_projectable_pre_entry_same_sequence_consumption(self):
        self.registered(); self.qualified()
        with self.coordinator._lock, self.broker._lock: self.coordinator._sync_callbacks_locked()
        self.assertEqual(self.coordinator._callback_cursor, 1)
        self.assertEqual(self.journal._persistence_cursor, 0)
        self.assertIs(type(self.write._callback_evidence[0]), Q)

    def test_legacy_ambiguous_status_stops_without_attribute_error(self):
        self.registered(True)
        self.client.decoder.interpret((37, *LEGACY))
        with self.coordinator._lock, self.broker._lock: self.coordinator._sync_callbacks_locked()
        self.assertEqual(self.coordinator._callback_cursor, 0)
        self.assertTrue(self.coordinator._reconciliation_required)

    def test_old_supported_prefix_then_unknown_qualified_then_old_suffix(self):
        self.registered()
        self.open(order_id=1000)
        self.client.decoder.processProtoBuf(Proto().SerializeToString(), 37)
        self.open(order_id=1001)
        with self.coordinator._lock, self.broker._lock: self.coordinator._sync_callbacks_locked()
        self.assertEqual(self.coordinator._callback_cursor, 1)
        self.assertTrue(self.coordinator._reconciliation_required)


class ReconciliationTests(sdk_fixture.Case):
    pass


def reconciliation_case(case, facet):
    def test(self):
        bundle = recon_fixture.bundle((recon_fixture.order(),))
        if case == 'projectable': event = values.qualified(sequence=bundle.final_local.callback_cursor + 1)
        else:
            raw = dict(RAW)
            for key, value in _UNPROJECTABLE[case].items():
                if value is None: raw.pop(key, None)
                else: raw[key] = value
            event = values.qualified(raw=raw, sequence=bundle.final_local.callback_cursor + 1)
        changed = page(bundle, event)
        before = changed.final_local
        result = reconciliation._evaluate_broker_reconciliation(changed)
        self.assertIs(result.state, reconciliation._AssessmentState.UNRESOLVED)
        self.assertIs(changed.final_local, before)
        self.assertIs(changed.final_local.unread_events[0], event)
        self.assertEqual(result.executed_quantity, D(0))
        self.assertEqual(result.attributed_execution_count, 0)
        if facet == 'suffix':
            conflicting = values.old_status(event.sequence + 1, filled=D(20), remaining=D(0))
            result = reconciliation._evaluate_broker_reconciliation(page(bundle, event, conflicting))
            if case != 'projectable': self.assertIs(result.state, reconciliation._AssessmentState.UNRESOLVED)
    return test


for _case in ('projectable', *_UNPROJECTABLE):
    for _facet in ('source', 'suffix'):
        setattr(ReconciliationTests, f'test_{_case}_{_facet}', reconciliation_case(_case, _facet))


class DurabilityTests(RuntimeCase):
    def test_projectable_qualified_blocks_durable_progress(self):
        self.registered(True); self.qualified()
        self.failed(self.drain())
        self.assertEqual(self.journal._persistence_cursor, 0)
        self.assertEqual(self.of_type(journal._OrderStatusObserved), ())
        self.assertEqual(self.of_type(journal._CallbackProgress), ())

    def test_later_old_record_cannot_bypass_qualified(self):
        self.registered(True); self.qualified(); self.open()
        self.failed(self.drain())
        self.assertEqual(self.journal._persistence_cursor, 0)
        self.assertEqual(self.of_type(journal._OpenOrderObserved), ())

    def test_prefix_previously_persisted_stops_before_new_qualified(self):
        self.registered(True); self.open(); self.drain()
        self.assertEqual(self.journal._persistence_cursor, 1)
        self.qualified(); self.open(order_id=1000)
        self.failed(self.drain())
        self.assertEqual(self.journal._persistence_cursor, 1)
        self.assertTrue(all(p.processed_sequence < 2 for p in self.of_type(journal._CallbackProgress)))

    def test_memory_cursor_cannot_satisfy_durable_entry_gate(self):
        self.registered(); self.qualified()
        with self.coordinator._lock, self.broker._lock: self.coordinator._sync_callbacks_locked()
        self.assertEqual(self.coordinator._callback_cursor, 1)
        self.assertEqual(self.journal._persistence_cursor, 0)
        self.assert_denied(self.invoke())
        self.assertEqual(self.write_calls(), [])

    def test_source_qualified_never_enters_journal_codec(self):
        self.registered(True); before = self.history(); self.qualified()
        self.assertEqual(self.history(), before)
        self.failed(self.drain())
        self.assertFalse(any(type(e) is Q for e in self.history()))

    def test_restart_quarantine_after_memory_capture(self):
        self.registered(True); self.qualified()
        self.assertTrue(self.journal._load().replay.restart_quarantine_required)
        self.assertEqual(self.journal._persistence_cursor, 0)

    def test_normal_sdk_return_separate_from_synchronous_qualified_capture(self):
        self.registered()
        original = self.client.placeOrder
        def invoked(*args): self.qualified(); return original(*args)
        self.client.placeOrder = invoked
        result = self.invoke()
        self.assertEqual(len(self.write_calls()), 1)
        self.assertEqual(len(self.of_type(journal._SdkReturnedNormally)), 1)
        self.assertEqual(self.write._callback_sequence, 1)
        self.assertEqual(self.of_type(journal._OrderStatusObserved), ())
        self.assertTrue(self.coordinator._pending_confirmation)


class ReservationTests(RuntimeCase):
    def reserve(self):
        with self.write._lock, self.write._arrival_lock:
            return self.write._issue_reservation(self.write._generation,
                (17, 'BUY', D(10), 'MKT', 'DAY', 'synthetic-entry'))

    def prove(self, reservation):
        with self.write._lock, self.write._arrival_lock:
            self.write._validate_reservation(self.coordinator, reservation)

    def test_arrival_invalidates_existing_reservation_before_source_parse(self):
        self.registered(); reservation = self.reserve()
        def before_parse():
            self.assertEqual(self.write._callback_sequence, 0)
            self.assertTrue(self.write._status_fences)
            with self.assertRaises(ReadOnlyError): self.prove(reservation)
        self.client.decoder.before_parse = before_parse
        self.qualified(orderId=100)
        with self.assertRaises(ReadOnlyError): self.prove(reservation)

    def test_callback_unknown_id_blocks_new_reservation_proof(self):
        self.registered(); self.client.decoder.processProtoBuf(Proto().SerializeToString(), 37)
        reservation = self.reserve()
        self.assertEqual(reservation.callback_watermark, 1)
        with self.assertRaises(ReadOnlyError): self.prove(reservation)

    def test_legacy_ambiguous_zero_blocks_proof_without_raising_floor(self):
        self.registered(); before = self.write._lifetime_order_floor
        self.client.decoder.interpret((37, 0, *LEGACY[1:]))
        self.assertEqual(self.write._lifetime_order_floor, before)
        with self.assertRaises(ReadOnlyError): self.prove(self.reserve())

    def test_known_other_id_does_not_invent_collision(self):
        self.registered(); self.qualified(orderId=9)
        reservation = self.reserve(); self.prove(reservation)
        self.assertEqual(reservation.order_id, 10)
        self.assertEqual(self.write_calls(), [])

    def test_same_id_classified_collision(self):
        self.registered(); self.qualified(orderId=10)
        self.assertIs(q._reservation_collision(tuple(self.write._callback_evidence),
            generation=1, order_id=10), q._ReservationCollision.COLLISION)

    def test_collision_dominates_unknown_observation(self):
        self.registered(); self.client.decoder.processProtoBuf(Proto().SerializeToString(), 37); self.qualified(orderId=10)
        self.assertIs(q._reservation_collision(tuple(self.write._callback_evidence),
            generation=1, order_id=10), q._ReservationCollision.COLLISION)

    def test_commission_stays_order_independent(self):
        self.registered(); self.commission()
        reservation = self.reserve(); self.prove(reservation)
        self.assertEqual(reservation.order_id, 10)

    def test_supplied_zero_is_known_not_unknown(self):
        self.registered(); self.qualified(orderId=0)
        reservation = self.reserve(); self.prove(reservation)
        self.assertEqual(q._known_order_id(self.write._callback_evidence[0]), 0)

    def test_max_id_exhausts_allocator(self):
        self.registered(); self.qualified(orderId=paper.MAX_ORDER_ID)
        with self.assertRaises(ReadOnlyError): self.reserve()
        self.assertTrue(self.write._lifetime_order_ids_exhausted)
        self.assertEqual(self.write._lifetime_order_floor, paper.MAX_ORDER_ID)

    def test_permid_cannot_replace_absent_order_id(self):
        self.registered(); self.client.decoder.processProtoBuf(Proto(permId=100000).SerializeToString(), 37)
        self.assertEqual(self.write._lifetime_order_floor, 10)
        with self.assertRaises(ReadOnlyError): self.prove(self.reserve())

    def test_unknown_stale_generation_not_adopted_as_current(self):
        self.registered(); self.client.decoder.processProtoBuf(Proto().SerializeToString(), 37)
        self.assertIs(q._reservation_collision(tuple(self.write._callback_evidence),
            generation=2, order_id=10), q._ReservationCollision.NO_COLLISION_OBSERVED)
        self.assertEqual(self.write._generation, 1)

    def test_capture_failure_cannot_release_reserved_id(self):
        self.registered(); reservation = self.reserve(); floor = self.write._lifetime_order_floor
        error = KeyboardInterrupt('private interruption')
        with patch.object(self.write, '_callback_content_identity', side_effect=error), self.assertRaises(KeyboardInterrupt) as caught:
            self.qualified(orderId=reservation.order_id)
        self.assertIs(caught.exception, error)
        self.assertGreaterEqual(self.write._lifetime_order_floor, floor)
        with self.assertRaises(ReadOnlyError): self.reserve()


class BoundaryTests(PublicationCase):
    def test_no_callback_io_or_import(self):
        f = self.build(); before = tuple(f.api.calls)
        with patch('socket.socket', side_effect=AssertionError('socket')) as sock, \
             patch('socket.getaddrinfo', side_effect=AssertionError('DNS')) as dns, \
             patch('builtins.open', side_effect=AssertionError('file')) as opened, \
             patch('importlib.import_module', side_effect=AssertionError('callback import')) as imported, \
             patch.object(storage._SQLiteJournalStore, '_begin', side_effect=AssertionError('SQLite')) as db, \
             patch.object(owner_module._PaperDispatchJournalOwner, '_append_event', side_effect=AssertionError('journal')) as append:
            self.protobuf(f); self.legacy(f)
        for mocked in (sock, dns, opened, imported, db, append): mocked.assert_not_called()
        self.assertEqual(tuple(f.api.calls), before)

    def test_publication_has_arrival_but_no_delivery_or_pairing_guard(self):
        f = self.build(); original = f.transport._commit_callback_occurrence
        def checked(*args):
            self.assertTrue(f.transport._arrival_lock._is_owned())
            self.assertFalse(f.transport._lock._is_owned())
            self.assertFalse(f.owner._PaperStatusDelivery__guard._is_owned())
            self.assertFalse(f.owner._PaperStatusDelivery__pairing._OrderStatusPairingBoundary__guard._is_owned())
            return original(*args)
        with patch.object(f.transport, '_commit_callback_occurrence', side_effect=checked): self.protobuf(f)

    def test_capture_does_not_wait_for_write_bookkeeping_lock(self):
        f = self.build()
        with ThreadPoolExecutor(1) as pool:
            with f.transport._lock:
                pool.submit(self.protobuf, f).result(timeout=3)
        self.assertEqual(f.transport._callback_sequence, 1)

    def test_nested_protobuf_legacy_sequences(self):
        f = self.build()
        f.decoder.raw_action = lambda raw: self.legacy(f)
        self.protobuf(f)
        self.assertEqual([e.sequence for e in f.transport._callback_evidence], [1, 2])
        self.assertEqual([e.observation.protocol for e in f.transport._callback_evidence],
                         [source._OrderStatusProtocol.LEGACY, source._OrderStatusProtocol.PROTOBUF])

    def test_nested_legacy_protobuf_sequences(self):
        f = self.build(); f.decoder.legacy_action = lambda: self.protobuf(f)
        self.legacy(f)
        self.assertEqual([e.sequence for e in f.transport._callback_evidence], [1, 2])
        self.assertEqual(len(f.handoffs), 2); self.truthful(f)

    def test_nested_protobuf_unique_entries_and_sequences(self):
        f = self.build()
        def nested(raw): f.decoder.raw_action = None; self.protobuf(f, orderId=20)
        f.decoder.raw_action = nested; self.protobuf(f)
        self.assertEqual([e.observation.order_id for e in f.transport._callback_evidence], [20, 10])
        self.assertEqual([e.sequence for e in f.transport._callback_evidence], [1, 2])
        self.assertIsNot(f.handoffs[0].delivery, f.handoffs[1].delivery)

    def test_child_cannot_accept_parent_handoff(self):
        f = self.build(); parent = []; make = delivery._PaperStatusDelivery._make_handoff
        accept = delivery._PaperStatusDelivery._accept_handoff
        def handoff(owner, entry, observation):
            result = make(owner, entry, observation)
            if not parent:
                parent.append(result)
                self.protobuf(f, orderId=20)
            return result
        def wrong(owner, value, receiver):
            return accept(owner, parent[0], receiver)
        with patch.object(delivery._PaperStatusDelivery, '_make_handoff', handoff), \
             patch.object(delivery._PaperStatusDelivery, '_accept_handoff', wrong), self.assertRaises(ReadOnlyError):
            self.protobuf(f)
        self.assertEqual(f.transport._callback_sequence, 1)
        self.assertEqual(f.transport._callback_evidence[0].observation.order_id, 20)
        self.failed(f)

    def test_parent_cannot_reuse_completed_child_handoff(self):
        f = self.build(); child = []; receive = delivery._PaperStatusDelivery._receive_handoff
        nested = False
        def handoff(owner, value, receiver):
            nonlocal nested
            if not nested:
                nested = True
                self.protobuf(f, orderId=20)
                return receive(owner, child[0], receiver)
            child.append(value)
            return receive(owner, value, receiver)
        with patch.object(delivery._PaperStatusDelivery, '_receive_handoff', handoff), self.assertRaises(ReadOnlyError):
            self.protobuf(f)
        self.assertEqual(f.transport._callback_sequence, 1)
        self.assertEqual(f.transport._callback_evidence[0].observation.order_id, 20)
        self.failed(f)

    def test_protocol_difference_prevents_compression(self):
        f = self.build(); self.protobuf(f); self.legacy(f)
        self.assertEqual(len(f.transport._callback_evidence), 2)

    def test_representation_difference_prevents_compression(self):
        f = self.build(); self.protobuf(f, filled='4.0'); self.protobuf(f, filled='4.00')
        self.assertEqual(len(f.transport._callback_evidence), 2)

    def test_signed_zero_difference_prevents_compression(self):
        f = self.build(); self.protobuf(f, filled='0'); self.protobuf(f, filled='-0')
        self.assertEqual(len(f.transport._callback_evidence), 2)

    def test_presence_difference_prevents_compression(self):
        f = self.build()
        for raw in ({}, {'filled': str(source._UNSET_DECIMAL)}): f.decoder.processProtoBuf(Proto(**raw).SerializeToString(), 37)
        self.assertEqual(len(f.transport._callback_evidence), 2)

    def test_permid_difference_prevents_compression(self):
        f = self.build(); self.protobuf(f, permId=1); self.protobuf(f, permId=2)
        self.assertEqual(len(f.transport._callback_evidence), 2)

    def test_modes_and_no_public_receiver(self):
        f = self.build()
        self.assertIs(f.transport.account_mode, AccountMode.UNKNOWN)
        for name in ('receiver', 'publish_status', 'connect', 'run', 'place_order', 'clear_reconciliation'):
            self.assertFalse(hasattr(f.transport, name))
        self.assertEqual(tuple(field.name for field in fields(Q)), ('generation', 'sequence', 'observation'))

    def test_raw_sdk_object_not_stored_in_ledger(self):
        f = self.build(); self.protobuf(f)
        event, = f.transport._callback_evidence
        self.assertIs(type(event.observation), source._OrderStatusObservation)
        for value in (event, event.observation, event.observation.presence): self.assertFalse(hasattr(value, '__dict__'))
        self.assertNotIn('Decoder', repr(event)); self.assertNotIn('receipt', repr(event))

    def test_old_fixture_status_path_remains_isolated(self):
        t, api, client = sdk_fixture.transport()
        self.addCleanup(t.disconnect)
        client.wrapper.orderStatus(*LEGACY, '', 0.0)
        self.assertEqual(t._callback_sequence, 0)
        self.assertIsNone(t._status_delivery_owner._PaperStatusDelivery__receiver)
