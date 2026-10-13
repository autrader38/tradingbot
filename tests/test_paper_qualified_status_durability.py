"""PRE9: exact historical source and verified V2 occurrences, offline only."""

import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from decimal import Decimal as D, Inexact, Rounded, localcontext
import inspect
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Event, current_thread
import unittest
from unittest.mock import patch

from tradingbot_broker import ibkr_order_status_evidence as source
from tradingbot_broker import ibkr_qualified_status_ledger as memory
from tradingbot_broker import ibkr_paper_transport as paper
from tradingbot_broker import paper_qualified_status_journal as adapter
from tradingbot_broker import paper_dispatch_journal as owner_module
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker import paper_execution_journal_sqlite as storage
from tradingbot_broker import paper_execution_accounting as accounting
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests import test_ibkr_order_status_evidence as wire
from tests import test_paper_execution_journal as journal_fixture
from tests import test_paper_v2_callback_coverage_contract as coverage_fixture
from tests import test_ibkr_qualified_status_publication as publication
from tests.test_paper_callback_persistence import PersistenceFixture


NAMES = ('order_id', 'status', 'filled', 'remaining', 'average_fill_price',
         'perm_id', 'parent_id', 'last_fill_price', 'client_id')
SDK_NAMES = ('orderId', 'status', 'filled', 'remaining', 'avgFillPrice',
             'permId', 'parentId', 'lastFillPrice', 'clientId')
Owner = owner_module._PaperDispatchJournalOwner
QP, QQ = j._QualifiedStatusProtocol, j._QualifiedStatusSourceQuality
OCCURRENCES = (j._OpenOrderObserved, j._OrderStatusObserved, j._ExecutionObserved,
    j._DuplicateCallbackCovered, j._UntrackedCallbackObserved, j._CommissionCallbackCovered,
    j._UntrackedCommissionCallbackCovered, j._QualifiedOrderStatusObserved)


def observation(protocol='protobuf', changes=None):
    if protocol == 'protobuf':
        return source._adapt_protobuf_order_status(wire.Proto(**dict(wire.RAW, **(changes or {}))))
    values = dict(zip(NAMES, wire.LEGACY)); values.update(changes or {})
    return source._adapt_legacy_order_status(*(values[name] for name in NAMES))


def evidence(obs=None, sequence=1, generation=3):
    return memory._QualifiedOrderStatusEvidence(generation, sequence,
                                               observation() if obs is None else obs)


def durable(obs=None, sequence=1):
    return adapter._adapt_qualified_status(evidence(obs, sequence), journal_fixture.session())


def identity(obs):
    # Independent, explicitly reviewed comparison, not adapter/dataclass equality.
    return (obs.protocol.value, tuple((name, type(getattr(obs, name)).__name__,
        getattr(obs, name).as_tuple() if type(getattr(obs, name)) is D else getattr(obs, name),
        getattr(obs.presence, name).value) for name in NAMES),
        None if obs.presence.wire_supplied is None else tuple(
            name for name in NAMES if name in obs.presence.wire_supplied))


def forged(value, **changes):
    return journal_fixture.forged(value, **changes)


class PureCase(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket.connect', 'socket.socket.connect_ex',
                     'socket.create_connection', 'socket.getaddrinfo', 'sqlite3.connect'):
            self.enterContext(patch(name, side_effect=AssertionError('external activity')))

    def invalid(self, function, *args):
        with self.assertRaises(ReadOnlyError): function(*args)


SAMPLES = dict(publication._SOURCES)
for name in SDK_NAMES:
    SAMPLES['absent_' + name] = ('protobuf', {k: v for k, v in wire.RAW.items() if k != name})


class SourceTests(PureCase):
    def test_all_512_protobuf_presence_sets_survive_exactly(self):
        for mask in range(512):
            raw = {name: wire.RAW[name] for i, name in enumerate(SDK_NAMES) if mask & (1 << i)}
            obs = source._adapt_protobuf_order_status(wire.Proto(**raw))
            event = durable(obs)
            rebuilt = storage._decode(storage._encode(event), type(event))
            self.assertEqual(identity(rebuilt.observation), identity(obs))

    def test_exact_layout_and_no_authority(self):
        event = durable()
        self.assertEqual(tuple(f.name for f in fields(event)), ('source', 'observation'))
        self.assertEqual(tuple(f.name for f in fields(event.observation)), ('protocol', *NAMES, 'presence'))
        self.assertFalse(hasattr(event, '__dict__'))
        for value in (event, event.source, event.observation, event.observation.presence):
            self.assertNotIn('Submitted', repr(value))
            with self.assertRaises(FrozenInstanceError): setattr(value, fields(value)[0].name, True)

    def test_independent_memory_and_journal_values(self):
        mem = evidence(); event = adapter._adapt_qualified_status(mem, journal_fixture.session())
        self.assertIsNot(event.observation, mem.observation)
        self.assertIsNot(event.observation.presence, mem.observation.presence)
        self.assertIsNot(event.observation.filled, mem.observation.filled)
        before = identity(event.observation)
        object.__setattr__(mem.observation, 'filled', D(99))
        self.assertEqual(identity(event.observation), before)

    def test_no_projection_is_invoked(self):
        with patch.object(memory, '_legacy_projection', side_effect=AssertionError('projection')):
            self.assertEqual(durable().source.sequence, 1)

    def test_memory_classification_remains_unchanged(self):
        self.assertIs(memory._consumer_support(evidence(), memory._LedgerConsumer.DURABLE_PERSISTENCE),
                      memory._ConsumerSupport.UNSUPPORTED_QUALIFIED_STATUS)

    def test_signed_zero_and_scale_change_identity(self):
        values = [evidence(observation(changes={'filled': text})) for text in ('0','0.0','-0.00')]
        self.assertEqual(len({adapter._qualified_callback_values(e) for e in values}), 3)

    def test_qualified_source_only_replay_has_no_attempt_or_allocator_effect(self):
        result = coverage_fixture.replay((journal_fixture.session(), durable(),
                                         j._CallbackProgress(journal_fixture.SID, 1, 1)))
        self.assertEqual(result.attempts, ())
        self.assertEqual(result.order_id_floor, 0)
        self.assertTrue(result.restart_quarantine_required)

    def test_qualified_quantity_is_not_an_accounting_fact(self):
        events = (*journal_fixture.prefix(), durable(observation(changes={'filled':'999'})),
                  j._CallbackProgress(journal_fixture.SID,1,1))
        h = coverage_fixture.header()
        result = accounting._derive_accounting(accounting._AccountingEvidence(h,
            journal_fixture.records(events,h), journal_fixture.SID,1,1,0,0,True))
        self.assertEqual(result.entries[0].recorded_execution_quantity, D(0))
        self.assertEqual(result.entries[0].historical_filled_lower_bound, D(0))
        self.assertIsNone(result.entries[0].known_commission_total)


def sample_case(protocol, payload, facet):
    def test(self):
        obs = (source._adapt_protobuf_order_status(wire.Proto(**payload)) if protocol == 'protobuf'
               else source._adapt_legacy_order_status(*payload))
        event = durable(obs)
        if facet == 'adapter':
            self.assertEqual(identity(event.observation), identity(obs))
            self.assertEqual(event.source, journal_fixture.source(1))
        elif facet == 'codec':
            rebuilt = storage._decode(storage._encode(event), type(event))
            self.assertEqual(identity(rebuilt.observation), identity(obs))
        else:
            result = coverage_fixture.replay((journal_fixture.session(),event,
                j._CallbackProgress(journal_fixture.SID,1,1)))
            self.assertEqual(result.callback_progress[0].processed_sequence,1)
            self.assertEqual(result.attempts,())
    return test


for name, (protocol, payload) in SAMPLES.items():
    for facet in ('adapter','codec','replay'):
        setattr(SourceTests, f'test_{name}_{facet}', sample_case(protocol,payload,facet))


class MalformedTests(PureCase):
    pass


BAD_VALUES = {
    'order_id': (True,-1,2**31,'10'), 'perm_id': (False,-1,2**63,'77'),
    'client_id': (True,-1,2**31,2.0), 'parent_id': (False,-1,2**31,D(0)),
    'status': ('future-status',123,object()),
    'filled': (True,D('NaN'),D('Infinity'),D(-1),D('1E-65'),D('1E65'),D('9'*65),wire.UNSET),
    'remaining': (D('NaN'),D(-1),D('1E65'),1),
    'average_fill_price': (D('Infinity'),D(-1),D('1E-65'),1.25),
    'last_fill_price': (D('NaN'),D(-1),D('9'*65),False),
    'protocol': ('PROTOBUF',None,source._OrderStatusProtocol.PROTOBUF),
    'presence': (None,object()),
}


def malformed_value_case(name,value):
    def test(self):
        bad = forged(durable().observation, **{name:value})
        self.invalid(j._QualifiedStatusObservation.__post_init__,bad)
        self.invalid(storage._encode,forged(durable(),observation=bad))
    return test


for name, values in BAD_VALUES.items():
    for index, value in enumerate(values):
        setattr(MalformedTests,f'test_invalid_{name}_{index}',malformed_value_case(name,value))


def bad_presence_case(name, quality):
    def test(self):
        obs = durable().observation
        bad = forged(obs,presence=forged(obs.presence,**{name:quality}))
        self.invalid(j._QualifiedStatusObservation.__post_init__,bad)
    return test


for name in NAMES:
    for quality in (QQ.LEGACY_DECODED,QQ.LEGACY_AMBIGUOUS,QQ.UNAVAILABLE):
        setattr(MalformedTests,f'test_quality_contradiction_{name}_{quality.name}',bad_presence_case(name,quality))


def presence_shape_case(supplied):
    def test(self):
        obs = durable().observation
        bad = forged(obs,presence=forged(obs.presence,wire_supplied=supplied))
        self.invalid(j._QualifiedStatusObservation.__post_init__,bad)
    return test


for index, supplied in enumerate((None,('order_id','order_id'),('missing',),tuple(reversed(NAMES)),
                                  list(NAMES),frozenset(NAMES),(True,),('order_id',))):
    setattr(MalformedTests,f'test_presence_shape_{index}',presence_shape_case(supplied))


class ReplayTests(PureCase):
    def test_gap_is_not_covered_by_qualified_high_water(self):
        self.invalid(coverage_fixture.replay,(journal_fixture.session(),durable(sequence=2),
            j._CallbackProgress(journal_fixture.SID,2,2)))

    def test_gap_may_remain_unprocessed_then_be_filled_truthfully(self):
        result=coverage_fixture.replay((journal_fixture.session(),durable(sequence=2),
            j._CallbackProgress(journal_fixture.SID,0,2),durable(),j._CallbackProgress(journal_fixture.SID,2,2)))
        self.assertEqual(result.callback_progress[0].processed_sequence,2)

    def test_source_is_v2_only(self):
        self.invalid(journal_fixture.records,(durable(),))

    def test_missing_session_is_invalid(self):
        self.invalid(coverage_fixture.replay,(durable(),))

    def test_wrong_generation_is_invalid(self):
        self.invalid(coverage_fixture.replay,(journal_fixture.session(),
            replace(durable(),source=journal_fixture.source(1,generation=4))))

    def test_qualified_cannot_be_ordinary_duplicate_root(self):
        marker=j._DuplicateCallbackCovered(journal_fixture.source(2),coverage_fixture.opened(),2)
        self.invalid(coverage_fixture.replay,(journal_fixture.session(),durable(),marker))

    def test_huge_progress_does_not_allocate_sequence_range(self):
        self.invalid(coverage_fixture.replay,(journal_fixture.session(),durable(),
            j._CallbackProgress(journal_fixture.SID,10**100,10**100)))


def occurrence_conflict_case(kind, reverse):
    def test(self):
        if kind=='qualified':
            prefix=(journal_fixture.session(),); other=durable()
        elif kind=='direct':
            prefix=journal_fixture.prefix(); other=journal_fixture.opened(1)
        elif kind=='untracked':
            prefix=(journal_fixture.session(),); other=coverage_fixture.untracked()
        elif kind=='duplicate':
            prefix=(*journal_fixture.prefix(),journal_fixture.opened(1))
            other=coverage_fixture.duplicate(); qualified=durable(sequence=2)
        elif kind=='commission':
            c=coverage_fixture.commission()
            prefix=(*journal_fixture.prefix(),journal_fixture.execution(2),
                     journal_fixture.commission(report_id=c.report_id))
            other=j._CommissionCallbackCovered(journal_fixture.source(1),c,6)
        else:
            prefix=(journal_fixture.session(),coverage_fixture.untracked(2,coverage_fixture.execution()))
            other=j._UntrackedCommissionCallbackCovered(journal_fixture.source(1),
                coverage_fixture.commission('foreign-exec'),2)
        qualified=durable(sequence=2 if kind=='duplicate' else 1)
        suffix=(other,qualified) if reverse else (qualified,other)
        self.invalid(coverage_fixture.replay,prefix+suffix)
    return test


for kind in ('qualified','direct','untracked','duplicate','commission','foreign_commission'):
    for reverse in (False,True):
        setattr(ReplayTests,f'test_occurrence_conflict_{kind}_{reverse}',occurrence_conflict_case(kind,reverse))


class DecimalTests(PureCase):
    pass


def decimal_case(text, name):
    def test(self):
        # Construct the source model directly with a supplied exact Decimal;
        # PRE3 adapter float price conversion has already happened upstream.
        obs=observation()
        obs=replace(obs,**{name:D(text)})
        with localcontext() as ctx:
            ctx.prec=2; ctx.traps[Inexact]=ctx.traps[Rounded]=True
            event=durable(obs)
            rebuilt=storage._decode(storage._encode(event),type(event))
            self.assertEqual(getattr(rebuilt.observation,name).as_tuple(),D(text).as_tuple())
    return test


for text_index,text in enumerate(('0','0.0','0.00','-0','-0.00','1','1.0','1.000','1E-64','1E64','9'*64)):
    for name in ('filled','average_fill_price'):
        setattr(DecimalTests,f'test_shape_{text_index}_{name}',decimal_case(text,name))


class ProductionCase(publication.RuntimeCase):
    def scenario(self,name):
        if name in ('qualified','qualified_duplicates','mixed'):
            self.registered(True)
        else:
            self.start()
        if name=='open': self.open()
        elif name=='status': self.status()
        elif name=='execution': self.execution()
        elif name.startswith('duplicate_') and name!='duplicate_commission':
            for _ in range(3): getattr(self,name.removeprefix('duplicate_'))()
        elif name.startswith('untracked_'):
            getattr(self,name.removeprefix('untracked_'))(order_id=99)
        elif name in ('commission','duplicate_commission'):
            self.execution(); self.commission()
            if name=='duplicate_commission': self.commission('fees')
        elif name=='foreign_commission':
            self.execution(order_id=99); self.commission()
        elif name=='unknown_commission': self.commission()
        elif name=='owned_lookahead': self.commission(); self.execution()
        elif name=='foreign_lookahead': self.commission(); self.execution(order_id=99)
        elif name=='qualified': self.qualified()
        elif name=='qualified_duplicates':
            for _ in range(3): self.qualified()
        elif name=='mixed':
            self.open(); self.open()
            self.write._callback(self.write._generation,'status',(99,'Submitted',D(0),D(10),0.0,0,0.0,2))
            self.execution(); self.commission(); self.commission('fees')
            self.execution(order_id=99,values={'execId':'foreign-execution'})
            self.commission(exec_id='foreign-execution'); self.qualified(); self.qualified()
        else: raise AssertionError(name)

    def occurrence_records(self):
        return tuple(r for r in self.journal._load().records if type(r.payload) in OCCURRENCES)

    def verified(self,result):
        self.assertIs(result.state,owner_module._CallbackPersistenceState.CURRENT)
        records=self.occurrence_records()
        self.assertEqual(sorted(r.payload.source.sequence for r in records),
                         list(range(1,self.write._callback_sequence+1)))
        self.assertEqual(len({(r.payload.source.session_id,r.payload.source.write_generation,
                              r.payload.source.sequence) for r in records}),len(records))
        self.assertEqual(self.journal._persistence_cursor,self.write._callback_sequence)


SCENARIOS = ('open','status','execution','duplicate_open','duplicate_status','duplicate_execution',
    'untracked_open','untracked_status','untracked_execution','commission','duplicate_commission',
    'foreign_commission','owned_lookahead','foreign_lookahead','qualified','qualified_duplicates','mixed')


class ProducerTests(ProductionCase):
    def test_fresh_owner_exact_v2_header_store_session(self):
        self.build(); h=self.journal._header
        self.assertEqual((h.schema_version,h.format_id),(2,'paper-execution-journal-v2'))
        self.assertIs(self.journal._store._header,h)
        self.assertIs(self.journal._load().records[0].payload.__class__,j._SessionRegistered)
        object.__setattr__(h,'schema_version',1)
        with self.assertRaises(ReadOnlyError): self.journal._load()

    def test_unknown_commission_stays_staged(self):
        self.scenario('unknown_commission'); result=self.drain()
        self.assertEqual((result.state.value,result.cursor,result.staged_count),('PENDING',0,1))
        self.assertEqual(self.occurrence_records(),())
        self.assertEqual(self.of_type(j._CallbackProgress),())

    def test_old_duplicates_reference_original_direct_root(self):
        self.scenario('duplicate_open'); self.verified(self.drain())
        records=self.occurrence_records(); root=records[0]
        self.assertEqual([r.payload.root_record_sequence for r in records[1:]],[root.sequence]*2)
        adjacent=self.journal._PaperDispatchJournalOwner__adjacent_observation
        self.assertEqual(owner_module._values(adjacent[2]),owner_module._values(root.payload))
        self.assertEqual(adjacent[3].record.sequence,root.sequence)

    def test_foreign_execution_retains_verified_durable_reference(self):
        self.scenario('foreign_commission'); self.verified(self.drain())
        identity_value,event,receipt=self.journal._PaperDispatchJournalOwner__foreign_executions['execution-1']
        self.assertIs(type(event),j._UntrackedCallbackObserved)
        self.assertIs(receipt.record.payload,event)
        marker=self.of_type(j._UntrackedCommissionCallbackCovered)[0]
        self.assertEqual(marker.execution_record_sequence,receipt.record.sequence)
        self.assertTrue(identity_value)

    def test_qualified_compression_never_reuses_source_receipt(self):
        self.scenario('qualified_duplicates')
        self.assertEqual(len(self.write._callback_evidence),1)
        self.assertEqual(self.write._callback_run_ends,[3])
        self.verified(self.drain())
        self.assertEqual(len(self.of_type(j._QualifiedOrderStatusObserved)),3)
        self.assertFalse(self.of_type(j._DuplicateCallbackCovered))

    def test_different_commission_receipt_and_scale_preserve_each_occurrence(self):
        self.start(); self.execution(); self.commission(amount=D('0.050'))
        self.receipt+=timedelta(seconds=1); self.commission('fees',amount=D('0.05'))
        self.verified(self.drain())
        markers=self.of_type(j._CommissionCallbackCovered)
        self.assertEqual(len(self.of_type(j._CommissionReceived)),1)
        self.assertNotEqual(markers[0].snapshot.received_at,markers[1].snapshot.received_at)
        self.assertEqual([m.snapshot.amount.as_tuple() for m in markers],[D('0.050').as_tuple(),D('0.05').as_tuple()])

    def test_no_attempt_is_needed_for_qualified_capture(self):
        self.registered(); self.qualified(orderId=99)
        self.verified(self.drain())
        self.assertEqual(self.journal._load().replay.attempts,())
        self.assertEqual(self.write_calls(),[])

    def test_unknown_hole_prevents_crossing_later_qualified_source(self):
        self.registered(True); self.commission(exec_id='unknown'); self.qualified()
        result=self.drain()
        self.assertEqual((result.state.value,result.cursor),('PENDING',0))
        self.assertEqual(len(self.of_type(j._QualifiedOrderStatusObserved)),1)
        self.assertEqual(self.of_type(j._CallbackProgress),())

    def test_source_after_sdk_return_is_storage_chronology_only(self):
        self.registered(); original=self.client.placeOrder
        def place(*args): self.qualified(); return original(*args)
        self.client.placeOrder=place; self.invoke(); self.verified(self.drain())
        records=self.journal._load().records
        returned=next(r.sequence for r in records if type(r.payload) is j._SdkReturnedNormally)
        qualified=next(r for r in records if type(r.payload) is j._QualifiedOrderStatusObserved)
        self.assertLess(returned,qualified.sequence)
        self.assertEqual(qualified.payload.source.sequence,1)


def producer_case(name,facet):
    def test(self):
        self.scenario(name); page=self.page(); self.verified(self.drain())
        if facet=='source':
            for record in self.occurrence_records():
                event=record.payload
                self.assertEqual(event.source.write_generation,self.journal._session.write_generation)
                self.assertEqual(event.source.session_id,self.journal._session.session_id)
                if type(event) is j._QualifiedOrderStatusObserved:
                    self.assertEqual(identity(event.observation),identity(page.events[event.source.sequence-1].observation))
        else:
            replay=self.journal._load().replay
            self.assertTrue(replay.restart_quarantine_required)
            expected=D(4) if name in ('execution','duplicate_execution','commission','duplicate_commission',
                                     'owned_lookahead','mixed') else D(0)
            self.assertEqual(replay.attempts[0].observed_execution_quantity,expected)
            self.assertIs(self.broker.account_mode,AccountMode.UNKNOWN)
            self.assertIsNone(self.broker.account_summary().mode)
            self.assertTrue(self.coordinator._pending_confirmation)
    return test


for name in SCENARIOS:
    for facet in ('source','economics'):
        setattr(ProducerTests,f'test_{name}_{facet}',producer_case(name,facet))


FAILURE_SCENARIOS = {
    j._OpenOrderObserved:'open', j._DuplicateCallbackCovered:'duplicate_open',
    j._UntrackedCallbackObserved:'untracked_execution', j._CommissionReceived:'commission',
    j._CommissionCallbackCovered:'commission', j._UntrackedCommissionCallbackCovered:'foreign_commission',
    j._QualifiedOrderStatusObserved:'qualified', j._CallbackProgress:'qualified',
}


class FailureTests(ProductionCase):
    pass


def append_failure_case(kind,code):
    def test(self):
        self.scenario(FAILURE_SCENARIOS[kind]); original=Owner._append_event
        def append(owner,event,**kwargs):
            if type(event) is kind: raise storage._StorageError(code)
            return original(owner,event,**kwargs)
        with patch.object(Owner,'_append_event',append): self.failed(self.drain())
        self.assertEqual(self.journal._persistence_cursor,0)
        self.assertFalse(self.of_type(j._CallbackProgress))
        with patch.object(storage._SQLiteJournalStore,'_begin',side_effect=AssertionError('retry')):
            self.assertEqual(self.drain().state.value,'FAILED')
    return test


for kind in FAILURE_SCENARIOS:
    for code in (storage._Code.BUSY,storage._Code.STORAGE_FAILURE,storage._Code.POISONED,
                 storage._Code.QUARANTINED,storage._Code.COMMIT_OUTCOME_UNKNOWN,
                 storage._Code.CORRUPT,storage._Code.CLOSED):
        setattr(FailureTests,f'test_append_{kind.__name__}_{code.name}',append_failure_case(kind,code))


def interruption_case(error_type,seam):
    def test(self):
        self.scenario('qualified'); error=error_type('opaque interruption')
        original=Owner._append_event
        if seam=='adapter': target=patch.object(owner_module,'_adapt_qualified_status',side_effect=error)
        elif seam=='revalidate': target=patch.object(Owner,'_callback_revalidate',side_effect=error)
        elif seam=='cursor': target=patch.object(Owner,'_publish_callback_cursor',side_effect=error)
        else:
            kind=j._CallbackProgress if seam.startswith('progress') else j._QualifiedOrderStatusObserved
            def append(owner,event,**kwargs):
                if type(event) is kind:
                    if seam.endswith('after'): original(owner,event,**kwargs)
                    raise error
                return original(owner,event,**kwargs)
            target=patch.object(Owner,'_append_event',append)
        with target,self.assertRaises(error_type) as caught: self.drain()
        self.assertIs(caught.exception,error)
        self.assertTrue(self.journal._persistence_failed)
        self.assertTrue(self.coordinator._reconciliation_required)
        self.assertEqual(self.journal._persistence_cursor,0)
        if seam in ('progress_after','cursor'):
            self.assertEqual(self.of_type(j._CallbackProgress)[-1].processed_sequence,1)
    return test


for error_type in (KeyboardInterrupt,SystemExit,GeneratorExit):
    for seam in ('adapter','source_before','source_after','progress_before','progress_after','revalidate','cursor'):
        setattr(FailureTests,f'test_{error_type.__name__}_{seam}',interruption_case(error_type,seam))


def actual_commit_case(kind,after):
    def test(self):
        self.scenario(FAILURE_SCENARIOS[kind]); original_append=Owner._append_event
        original_commit=storage._commit
        def commit(connection):
            if after: original_commit(connection)
            raise sqlite3.OperationalError('discarded diagnostic')
        def append(owner,event,**kwargs):
            if type(event) is kind:
                with patch.object(storage,'_commit',commit): return original_append(owner,event,**kwargs)
            return original_append(owner,event,**kwargs)
        with patch.object(Owner,'_append_event',append): self.failed(self.drain())
        self.assertTrue(self.journal._store._poisoned)
        self.assertEqual(self.journal._persistence_cursor,0)
        with closing(sqlite3.connect(Path(self.temporary.name)/'dispatch-1.db')) as connection:
            events=[storage._decode(row[0],j._JournalRecord).payload for row in
                    connection.execute('SELECT encoded FROM records ORDER BY sequence')]
        self.assertEqual(sum(type(e) is kind for e in events),int(after))
        if kind is j._CallbackProgress and after:
            replay=j._replay_paper_execution_journal(self.journal._header,
                journal_fixture.records(events,self.journal._header))
            self.assertEqual(replay.callback_progress[0].processed_sequence,self.write._callback_sequence)
        with patch.object(storage._SQLiteJournalStore,'_begin',side_effect=AssertionError('retry')):
            self.assertEqual(self.drain().state.value,'FAILED')
    return test


for kind in FAILURE_SCENARIOS:
    for after in (False,True):
        setattr(FailureTests,f'test_actual_commit_{kind.__name__}_{after}',actual_commit_case(kind,after))


def backing_interrupt_case(error_type,after):
    def test(self):
        self.scenario('commission'); error=error_type('opaque interruption'); original=Owner._append_event
        def append(owner,event,**kwargs):
            if type(event) is j._CommissionReceived:
                if after: original(owner,event,**kwargs)
                raise error
            return original(owner,event,**kwargs)
        with patch.object(Owner,'_append_event',append),self.assertRaises(error_type) as caught: self.drain()
        self.assertIs(caught.exception,error)
        self.assertTrue(self.journal._persistence_failed)
        self.assertEqual(self.journal._persistence_cursor,0)
        self.assertEqual(self.of_type(j._CommissionCallbackCovered),())
    return test


for error_type in (KeyboardInterrupt,SystemExit,GeneratorExit):
    for after in (False,True):
        setattr(FailureTests,f'test_backing_{error_type.__name__}_{after}',backing_interrupt_case(error_type,after))


class StorageTests(unittest.TestCase):
    def test_new_models_and_events_reopen_without_ddl_change(self):
        h=coverage_fixture.header()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.db'
            store=storage._SQLiteJournalStore._create(path,h)
            try:
                store._append(journal_fixture.session()); event=durable()
                receipt=store._append(event); store._append(j._CallbackProgress(journal_fixture.SID,1,1))
                self.assertIs(receipt.record.payload,event)
                self.assertEqual(store._connection.execute('PRAGMA application_id').fetchone()[0],0x504A4231)
                self.assertEqual(store._connection.execute('PRAGMA user_version').fetchone()[0],1)
                self.assertEqual(store._connection.execute('PRAGMA journal_mode').fetchone()[0],'wal')
                self.assertEqual(store._connection.execute('PRAGMA synchronous').fetchone()[0],2)
            finally: store._close()
            with storage._SQLiteJournalStore._open(path,h) as reopened:
                restored=reopened._load().records[1].payload
                self.assertEqual(identity(restored.observation),identity(event.observation))
                self.assertTrue(reopened._load().replay.restart_quarantine_required)

    def test_maximum_bounded_source_payload(self):
        maximum=(1<<63)-1
        obs=replace(observation(),status='PendingSubmit',order_id=2**31-1,
            client_id=2**31-1,parent_id=2**31-1,perm_id=maximum,
            filled=D('9'*64+'E-64'),remaining=D('9'*64+'E-64'),
            average_fill_price=D('9'*64+'E-64'),last_fill_price=D('9'*64+'E-64'))
        event=adapter._adapt_qualified_status(evidence(obs,maximum,maximum),
            journal_fixture.session(generation=maximum))
        h=coverage_fixture.header()
        encoded=storage._encode(j._JournalRecord(maximum,2,h.journal_id,h.account_binding,event))
        self.assertLess(len(encoded),262144)
        self.assertEqual(storage._encode(storage._decode(encoded,j._JournalRecord)),encoded)

    def test_source_without_progress_is_historical_only(self):
        h=coverage_fixture.header()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.db'
            with storage._SQLiteJournalStore._create(path,h) as store:
                store._append(journal_fixture.session()); store._append(durable())
            with storage._SQLiteJournalStore._open(path,h) as store:
                replay=store._load().replay
                self.assertEqual(replay.callback_progress,())
                self.assertTrue(replay.restart_quarantine_required)

    def test_duplicate_source_quarantines(self):
        h=coverage_fixture.header()
        with tempfile.TemporaryDirectory() as folder:
            with storage._SQLiteJournalStore._create(Path(folder)/'source.db',h) as store:
                store._append(journal_fixture.session()); store._append(durable())
                with self.assertRaises(storage._StorageError) as caught: store._append(durable())
                self.assertEqual(caught.exception.code,'QUARANTINED')
                self.assertTrue(store._quarantined)


def codec_corruption_case(facet):
    def test(self):
        encoded=json.loads(storage._encode(durable()))
        obs=encoded['fields']['observation']['fields']
        presence=obs['presence']['fields']
        if facet=='protocol': obs['protocol']['member']='FUTURE'
        elif facet=='quality': presence['filled']['member']='FUTURE'
        elif facet=='legacy_presence': obs['protocol']['member']='LEGACY'
        elif facet=='absent_id': obs['order_id']=None
        elif facet=='absent_status': obs['status']=None
        elif facet=='duplicate_presence': presence['wire_supplied']['items'].append('order_id')
        elif facet=='unknown_presence': presence['wire_supplied']['items'].append('unknown')
        elif facet=='reordered_presence': presence['wire_supplied']['items'].reverse()
        elif facet=='decimal_sign': obs['filled']['sign']=2
        elif facet=='decimal_digits': obs['filled']['digits']=[1]*65
        elif facet=='decimal_bool': obs['filled']['digits']=[True]
        elif facet=='decimal_exponent': obs['filled']['exponent']=65
        elif facet=='bool_id': obs['order_id']=True
        elif facet=='perm_id': obs['perm_id']=2**63
        else: obs['status']='unreviewed-broker-text'
        data=json.dumps(encoded,sort_keys=True,separators=(',',':')).encode()
        with self.assertRaises(storage._StorageError) as caught:
            storage._decode(data,j._QualifiedOrderStatusObserved)
        self.assertEqual(caught.exception.code,'CORRUPT')
    return test


for facet in ('protocol','quality','legacy_presence','absent_id','absent_status','duplicate_presence',
              'unknown_presence','reordered_presence','decimal_sign','decimal_digits','decimal_bool',
              'decimal_exponent','bool_id','perm_id','status'):
    setattr(StorageTests,'test_codec_corruption_'+facet,codec_corruption_case(facet))


class BoundaryTests(ProductionCase):
    def test_worker_callback_has_no_storage_or_owner_activity(self):
        self.registered()
        guards=[patch.object(storage._SQLiteJournalStore,'_begin',side_effect=AssertionError('storage')),
                patch.object(Owner,'_append_event',side_effect=AssertionError('owner')),
                patch('builtins.open',side_effect=AssertionError('filesystem'))]
        with guards[0],guards[1],guards[2]:
            with ThreadPoolExecutor(max_workers=1) as pool: pool.submit(self.qualified).result(timeout=5)
        self.assertEqual(self.journal._persistence_cursor,0)
        self.assertEqual(self.write._callback_sequence,1)
        self.assertEqual(self.write_calls(),[])
        self.verified(self.drain())

    def test_owner_thread_and_capture_lock_exclusion(self):
        self.scenario('mixed'); original=storage._SQLiteJournalStore._begin
        def begin(store,writing):
            self.assertIs(current_thread(),self.journal._thread)
            for lock in (self.transport._lifecycle,self.transport._condition,self.write._lock,self.write._arrival_lock):
                self.assertFalse(lock._is_owned())
            return original(store,writing)
        with patch.object(storage._SQLiteJournalStore,'_begin',begin): self.verified(self.drain())
        self.assertFalse(self.journal._store._connection.in_transaction)

    def test_wrong_thread_refuses_before_storage(self):
        self.scenario('qualified')
        with patch.object(storage._SQLiteJournalStore,'_begin',side_effect=AssertionError('storage')):
            with ThreadPoolExecutor(max_workers=1) as pool: result=pool.submit(self.drain).result(timeout=5)
        self.failed(result)

    def test_capture_can_finish_while_owner_waits_on_commit(self):
        self.registered(True); self.qualified(); start=Event(); captured=Event(); original=storage._commit
        def worker():
            self.assertTrue(start.wait(5)); self.qualified(orderId=99); captured.set()
        def commit(connection):
            start.set(); self.assertTrue(captured.wait(5)); return original(connection)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(worker)
            with patch.object(storage,'_commit',commit): result=self.drain()
            future.result(timeout=5)
        self.assertEqual((result.state.value,result.cursor),('PENDING',1))
        self.verified(self.drain())

    def test_loss_during_io_never_publishes_progress(self):
        self.scenario('qualified'); original=Owner._append_callback
        def append(owner,coordinator,event,staged):
            receipt=original(owner,coordinator,event,staged)
            for number in range(127): self.qualified(orderId=1000+number)
            with self.assertRaises(ReadOnlyError): self.qualified(orderId=1127)
            return receipt
        with patch.object(Owner,'_append_callback',append): self.failed(self.drain())
        self.assertIsNotNone(self.write._callback_evidence_lost_at)
        self.assertEqual(self.journal._persistence_cursor,0)
        self.assertEqual(self.of_type(j._CallbackProgress),())

    def test_128_runs_and_frozen_loss_have_no_durable_repair(self):
        self.registered(True)
        for number in range(128): self.qualified(orderId=1000+number)
        with self.assertRaises(ReadOnlyError): self.qualified(orderId=1128)
        self.assertEqual(len(self.write._callback_evidence),128)
        self.assertEqual(self.write._callback_evidence_lost_at,129)
        self.failed(self.drain())
        self.assertEqual(self.occurrence_records(),())

    def test_covered_set_requires_occurrence_not_backing_receipt(self):
        self.scenario('commission'); original=Owner._cover_callback
        def cover(owner,c,staged,attempts):
            value=original(owner,c,staged,attempts)
            if type(staged.evidence) is paper._CommissionEvidence:
                event,receipt=owner._PaperDispatchJournalOwner__commissions[staged.evidence.exec_id]
                return owner_module._CallbackCoverage(staged,event,receipt)
            return value
        with patch.object(Owner,'_cover_callback',cover): self.failed(self.drain())
        self.assertEqual(self.journal._persistence_cursor,0)

    def test_ephemeral_coverage_is_rejected_before_covered_set(self):
        self.scenario('untracked_open')
        with patch.object(Owner,'_cover_callback',side_effect=lambda c,s,a:
                          owner_module._CallbackCoverage(s,None,None)):
            self.failed(self.drain())
        self.assertEqual(self.journal._PaperDispatchJournalOwner__covered,{})

    def test_qualified_revalidation_detects_representation_change(self):
        self.scenario('qualified'); original=Owner._append_callback
        def append(owner,coordinator,event,staged):
            receipt=original(owner,coordinator,event,staged)
            retained=self.write._callback_evidence[0]
            self.write._callback_evidence[0]=replace(retained,observation=replace(
                retained.observation,filled=D('4.000')))
            return receipt
        with patch.object(Owner,'_append_callback',append): self.failed(self.drain())
        self.assertEqual(self.journal._persistence_cursor,0)

    def test_authority_and_accounting_lifecycle_remain_historical(self):
        self.scenario('mixed'); self.verified(self.drain())
        accounting_owner=self.journal._create_accounting_owner(); accounting_owner._refresh()
        snapshot=accounting_owner._snapshot
        self.assertIsNotNone(snapshot)
        entry=snapshot.entries[0]
        self.assertEqual(entry.recorded_execution_quantity,D(4))
        self.assertIsNone(entry.current_bot_held_quantity)
        self.assertIsNone(entry.working_remainder)
        working=accounting_owner._create_lifecycle_owner(); working._refresh()
        self.assertIsNone(working._snapshot.entries[0].current_working_remainder)
        self.assertTrue(self.coordinator._pending_confirmation)
        self.assertTrue(self.journal._load().replay.restart_quarantine_required)

    def test_journal_storage_layering_and_public_exports(self):
        import tradingbot_broker
        for module in (j,storage):
            imports={n.module for n in ast.walk(ast.parse(inspect.getsource(module))) if isinstance(n,ast.ImportFrom)}
            self.assertFalse(imports & {'ibkr_qualified_status_ledger','ibkr_order_status_evidence',
                                       'ibkr_paper_transport','ibapi'})
        self.assertFalse(hasattr(tradingbot_broker,'_QualifiedOrderStatusObserved'))
        self.assertEqual(adapter.__all__,())


if __name__ == '__main__': unittest.main()
