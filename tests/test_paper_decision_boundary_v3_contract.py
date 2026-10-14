"""Inactive V3 syntax/prefix proofs; fixtures are not qualified runtime producers."""

import ast
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone, tzinfo
import hashlib
import inspect
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker import paper_execution_journal_sqlite as s
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError
from tests import test_paper_execution_journal as f
from tests import test_paper_v2_callback_coverage_contract as v2
from tests import test_paper_qualified_status_durability as q
from tests import test_paper_execution_journal_sqlite as sql_fixture


Boundary = j._QualifiedDecisionBoundaryObserved
Companion = j._V3DecisionBoundaryReplayEvidence
MAX = (1 << 63) - 1


def header(version=3):
    return replace(f.header(), schema_version=version,
                   format_id={1:j._FORMAT_ID, 2:j._FORMAT_ID_V2, 3:j._FORMAT_ID_V3}[version])


def boundary(n=0, at=f.AT, sid=f.SID, generation=3):
    # Anonymous syntactic fixture only; no runtime clock, receipt or producer authority.
    return Boundary(sid, generation, at, n)


def records(events, h=None):
    return f.records(events, header() if h is None else h)


def replay(events, h=None):
    h = header() if h is None else h
    return j._replay_v3_decision_boundary_evidence(h, records(events,h))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


class PureCase(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.create_connection',
                     'socket.getaddrinfo', 'sqlite3.connect'):
            self.enterContext(patch(name, side_effect=AssertionError('external activity forbidden')))

    def denied(self, events):
        with self.assertRaises(ReadOnlyError) as caught: replay(events)
        self.assertEqual(caught.exception.code,j._ERROR)

    def corrupt(self, data, expected=Boundary):
        with self.assertRaises(s._StorageError) as caught: s._decode(data,expected)
        self.assertEqual(caught.exception.code,s._Code.CORRUPT.value)


class ContractTests(PureCase):
    def test_exact_four_field_layout(self):
        self.assertEqual(tuple(field.name for field in fields(Boundary)),
                         ('session_id','write_generation','decision_at','through_sequence'))

    def test_frozen_slotted_opaque(self):
        value=boundary()
        self.assertFalse(hasattr(value,'__dict__'))
        self.assertNotIn(f.SID,repr(value))
        self.assertNotIn('2026',repr(value))
        with self.assertRaises(FrozenInstanceError): value.through_sequence=1

    def test_fixed_utc_codec_clone_is_supported(self):
        at=f.AT.replace(tzinfo=timezone(timedelta(0),'UTC'))
        self.assertEqual(s._encode(boundary(at=at)),s._encode(boundary()))
        rebuilt=s._decode(s._encode(boundary(at=at)),Boundary)
        self.assertEqual(rebuilt.decision_at,f.AT)

    def test_zero_is_before_any_occurrence_without_fake_callback(self):
        result=replay((f.session(),boundary()))
        self.assertEqual(result.boundary_records[0].payload.through_sequence,0)
        self.assertEqual(result.replay.observed_callback_watermarks,())
        self.assertEqual(result.replay.callback_progress,())

    def test_exact_frontier_after_qualified_source(self):
        result=replay((f.session(),q.durable(),boundary(1)))
        self.assertEqual(result.boundary_records[0].sequence,3)
        self.assertEqual(result.replay.last_sequence,3)
        self.assertEqual(result.replay.attempts,())
        self.assertEqual(result.replay.callback_progress,())

    def test_complete_prefix_does_not_require_progress_to_mint_coverage(self):
        result=replay((f.session(),q.durable(),boundary(1),j._CallbackProgress(f.SID,1,1)))
        self.assertEqual(result.replay.callback_progress[0].processed_sequence,1)

    def test_pending_progress_cannot_conceal_undurable_suffix(self):
        self.denied((f.session(),j._CallbackProgress(f.SID,0,1),boundary()))

    def test_progress_behind_covered_frontier_is_not_a_coverage_hole(self):
        result=replay((f.session(),j._CallbackProgress(f.SID,0,0),q.durable(),boundary(1)))
        self.assertEqual(result.replay.callback_progress[0].processed_sequence,0)

    def test_complete_mixed_pre9_occurrence_explanations(self):
        result=replay((*v2.mixed(),boundary(8)))
        self.assertEqual(result.boundary_records[0].payload.through_sequence,8)
        self.assertEqual(result.replay.attempts[0].observed_execution_quantity,f.D(3))
        self.assertEqual(len(result.replay.attempts[0].commissions),1)

    def test_closed_unions_preserve_v1_v2(self):
        self.assertEqual(len(j._EVENT_TYPES_V1),15)
        self.assertEqual(j._EVENT_TYPES_V2,j._EVENT_TYPES_V1+j._COVERAGE_EVENT_TYPES+(j._QualifiedOrderStatusObserved,))
        self.assertNotIn(Boundary,j._EVENT_TYPES_V1+j._EVENT_TYPES_V2)
        self.assertEqual(j._EVENT_TYPES_V3,j._EVENT_TYPES_V2+(Boundary,))

    def test_explicit_v3_pair_default_header_unchanged(self):
        self.assertEqual((header().schema_version,header().format_id),(3,'paper-execution-journal-v3'))
        self.assertEqual((f.header().schema_version,f.header().format_id),(1,'paper-execution-journal-v1'))
        self.assertEqual(j._SCHEMA_VERSION,1)

    def test_inherited_qualified_source_preserved_in_v3(self):
        obs=q.observation(changes={'filled':'-0.00','remaining':'10.000'})
        event=q.durable(obs)
        value=records((f.session(),event,boundary(1)))[1]
        rebuilt=s._decode(s._encode(value),j._JournalRecord)
        self.assertEqual(q.identity(rebuilt.payload.observation),q.identity(obs))

    def test_unbounded_source_high_water_never_allocates_claimed_range(self):
        self.denied((f.session(),q.durable(sequence=10**100),boundary()))

    def test_zero_with_maximum_registered_generation(self):
        result=replay((f.session(generation=MAX),boundary(generation=MAX)))
        self.assertEqual(result.boundary_records[0].payload.write_generation,MAX)

    def test_boundary_not_a_duplicate_callback_root(self):
        self.denied((f.session(),q.durable(),boundary(1),v2.duplicate(2,root=3)))

    def test_boundary_does_not_cover_a_callback_gap(self):
        self.denied((f.session(),boundary(),j._CallbackProgress(f.SID,1,1)))

    def test_boundary_never_advances_callback_watermark(self):
        before=v2.replay((f.session(),q.durable()))
        after=replay((f.session(),q.durable(),boundary(1))).replay
        self.assertEqual(before.observed_callback_watermarks,after.observed_callback_watermarks)
        self.assertEqual(before.callback_progress,after.callback_progress)
        self.assertEqual(before.order_id_floor,after.order_id_floor)

    def test_replay_layout_is_unchanged(self):
        self.assertEqual(tuple(field.name for field in fields(j._JournalReplayState)),(
            'header','last_sequence','sessions','consumed_client_order_ids','reserved_order_ids',
            'order_id_floor','allocator_exhausted','attempts','callback_progress','observed_callback_watermarks',
            'reconciliation_required','reconciliation_reasons','restart_quarantine_required'))

    def test_plain_replay_keeps_old_result_type_for_v3(self):
        h=header(); rs=records((f.session(),boundary()),h)
        self.assertIs(type(j._replay_paper_execution_journal(h,rs)),j._JournalReplayState)

    def test_companion_contains_exact_record_references(self):
        h=header(); rs=records((f.session(),q.durable(),boundary(1)),h)
        value=j._replay_v3_decision_boundary_evidence(h,rs)
        self.assertIs(value.records,rs)
        self.assertIs(value.boundary_records[0],rs[2])
        self.assertFalse(hasattr(value,'__dict__'))
        self.assertNotIn(f.SID,repr(value))
        with self.assertRaises(FrozenInstanceError): value.records=()

    def test_companion_rejects_equal_substitute_boundary_record(self):
        value=replay((f.session(),boundary()))
        with self.assertRaises(ReadOnlyError): replace(value,boundary_records=(replace(value.boundary_records[0]),))

    def test_companion_rejects_omitted_boundary(self):
        value=replay((f.session(),boundary()))
        with self.assertRaises(ReadOnlyError): replace(value,boundary_records=())

    def test_companion_rejects_forged_replay(self):
        value=replay((f.session(),boundary()))
        with self.assertRaises(ReadOnlyError): replace(value,replay=replace(value.replay,last_sequence=1))

    def test_companion_rechecks_mutated_underlying_payload(self):
        value=replay((f.session(),boundary()))
        object.__setattr__(value.boundary_records[0].payload,'through_sequence',1)
        with self.assertRaises(ReadOnlyError): j._exact(value,Companion)

    def test_companion_is_not_a_durable_payload(self):
        with self.assertRaises(s._StorageError): s._encode(replay((f.session(),boundary())))

    def test_no_io_clock_or_sdk_in_contract_replay_codec(self):
        events=(f.session(),q.durable(),boundary(1))
        with ExitStack() as scope:
            for name in ('builtins.open','pathlib.Path.open','time.time','time.monotonic',
                         'sqlite3.connect','socket.getaddrinfo','socket.create_connection'):
                scope.enter_context(patch(name,side_effect=AssertionError('pure contract')))
            value=replay(events)
            data=s._encode(value.boundary_records[0])
            s._decode(data,j._JournalRecord)
            self.assertTrue(value.replay.restart_quarantine_required)

    def test_foreign_timezone_getters_are_not_called(self):
        class Foreign(tzinfo):
            def utcoffset(self,*args): raise AssertionError('foreign timezone getter')
            def tzname(self,*args): raise AssertionError('foreign timezone getter')
        at=datetime(2026,1,5,tzinfo=Foreign())
        with self.assertRaises(ReadOnlyError): boundary(at=at)

    def test_unsupported_future_record_version(self):
        with self.assertRaises(ReadOnlyError): j._JournalRecord(1,4,f.header().journal_id,f.header().account_binding,f.session())

    def test_unknown_event_type_rejected(self):
        class Unknown: pass
        with self.assertRaises(ReadOnlyError): records((Unknown(),))

    def test_dictionary_event_rejected(self):
        with self.assertRaises(ReadOnlyError): records(({'decision_at':f.AT,'through_sequence':0},))

    def test_boundary_fixture_construction_does_not_authenticate_producer(self):
        value=boundary()
        self.assertFalse(hasattr(value,'producer_receipt'))
        self.assertFalse(hasattr(value,'authentication'))
        self.assertFalse(hasattr(value,'qualified_owner'))


class OrderingTests(PureCase):
    def test_time_strictly_increases_with_same_frontier(self):
        result=replay((f.session(),boundary(),boundary(at=f.AT+timedelta(microseconds=1))))
        self.assertEqual(len(result.boundary_records),2)

    def test_time_and_frontier_progress(self):
        result=replay((f.session(),boundary(),q.durable(),boundary(1,at=f.AT+timedelta(seconds=1)),
            q.durable(sequence=2),boundary(2,at=f.AT+timedelta(seconds=2))))
        self.assertEqual([r.payload.through_sequence for r in result.boundary_records],[0,1,2])

    def test_timestamp_regression_rejected(self):
        self.denied((f.session(),boundary(),boundary(at=f.AT-timedelta(microseconds=1))))

    def test_equal_timestamp_same_frontier_rejected(self):
        self.denied((f.session(),boundary(),boundary()))

    def test_equal_timestamp_different_frontier_rejected(self):
        self.denied((f.session(),boundary(),q.durable(),boundary(1)))

    def test_frontier_regression_rejected_even_with_later_time(self):
        self.denied((f.session(),q.durable(),boundary(1),boundary(at=f.AT+timedelta(seconds=1))))

    def test_old_frontier_cannot_hide_later_qualified_source(self):
        self.denied((f.session(),q.durable(),q.durable(sequence=2),boundary(1)))

    def test_future_records_cannot_repair_boundary(self):
        self.denied((f.session(),boundary(1),q.durable()))

    def test_high_water_after_gap_is_not_frontier(self):
        self.denied((f.session(),q.durable(sequence=2),boundary(2)))

    def test_zero_frontier_cannot_conceal_gap(self):
        self.denied((f.session(),q.durable(sequence=2),boundary()))

    def test_gap_beyond_claimed_frontier_invalidates(self):
        self.denied((f.session(),q.durable(),q.durable(sequence=3),boundary(1)))

    def test_repaired_prefix_before_boundary_is_valid(self):
        result=replay((f.session(),q.durable(sequence=2),q.durable(),boundary(2)))
        self.assertEqual(result.boundary_records[0].payload.through_sequence,2)

    def test_progress_suffix_larger_than_claimed_frontier_rejected(self):
        self.denied((f.session(),q.durable(),j._CallbackProgress(f.SID,1,2),boundary(1)))

    def test_missing_session_rejected(self): self.denied((boundary(),))

    def test_wrong_session_rejected(self): self.denied((f.session(),boundary(sid=f.SID2)))

    def test_wrong_generation_rejected(self): self.denied((f.session(),boundary(generation=4)))

    def test_same_session_cannot_reregister_another_generation(self):
        self.denied((f.session(),boundary(),f.session(generation=4),boundary(at=f.AT+timedelta(seconds=1),generation=4)))

    def test_new_session_new_generation_is_independent(self):
        result=replay((f.session(),boundary(),f.session(f.SID2,generation=4),
                       boundary(at=f.AT-timedelta(days=1),sid=f.SID2,generation=4)))
        self.assertEqual(len(result.boundary_records),2)
        self.assertTrue(result.replay.restart_quarantine_required)

    def test_unrelated_session_gap_does_not_become_target_coverage(self):
        other=replace(q.durable(sequence=2),source=f.source(2,sid=f.SID2,generation=4))
        result=replay((f.session(),f.session(f.SID2,generation=4),other,boundary()))
        self.assertEqual(result.boundary_records[0].payload.session_id,f.SID)
        self.denied((f.session(),f.session(f.SID2,generation=4),other,boundary(sid=f.SID2,generation=4)))

    def test_journal_sequence_is_not_callback_sequence(self):
        result=replay((f.session(),f.session(f.SID2),q.durable(),boundary(1)))
        self.assertEqual(result.boundary_records[0].sequence,4)
        self.assertEqual(result.boundary_records[0].payload.through_sequence,1)

    def test_record_sequence_gap_rejected(self):
        h=header(); rs=records((f.session(),boundary()),h)
        with self.assertRaises(ReadOnlyError): j._replay_v3_decision_boundary_evidence(h,(rs[0],replace(rs[1],sequence=3)))

    def test_duplicate_callback_sequence_rejected(self):
        self.denied((f.session(),q.durable(),q.durable(),boundary(1)))

    def test_boundary_is_not_an_execution_or_commission_root(self):
        self.denied((f.session(),boundary(),j._UntrackedCommissionCallbackCovered(f.source(1),v2.commission(),2)))


class CodecTests(PureCase):
    def test_exact_boundary_golden_bytes(self):
        expected=(b'{"fields":{"decision_at":{"fold":0,"parts":[2026,1,5,15,45,0,0],"type":"datetime",'
                  b'"zone":{"kind":"fixed","name":"UTC","offset_us":0}},"session_id":"session-'
                  +b'1'*32+b'","through_sequence":0,"write_generation":3},"type":"_QualifiedDecisionBoundaryObserved"}')
        self.assertEqual(s._encode(boundary()),expected)
        self.assertEqual(s._encode(s._decode(expected,Boundary)),expected)

    def test_v3_record_roundtrip_is_canonical(self):
        value=records((f.session(),boundary()))[-1]
        data=s._encode(value)
        self.assertEqual(s._encode(s._decode(data,j._JournalRecord)),data)

    def test_trailing_byte_and_truncation(self):
        data=s._encode(boundary())
        self.corrupt(data+b' '); self.corrupt(data+b'{}')
        for length in (0,1,len(data)//2,len(data)-1): self.corrupt(data[:length])

    def test_invalid_utf8(self): self.corrupt(s._encode(boundary())+b'\xff')

    def test_duplicate_fields(self):
        data=s._encode(boundary())
        self.corrupt(data.replace(b'"through_sequence":0',b'"through_sequence":0,"through_sequence":0'))

    def test_noncanonical_spacing(self): self.corrupt(s._encode(boundary()).replace(b'":',b'": '))

    def test_missing_field(self):
        data=json.loads(s._encode(boundary())); del data['fields']['through_sequence']
        self.corrupt(canonical(data))

    def test_extra_field(self):
        data=json.loads(s._encode(boundary())); data['fields']['order_id']=7
        self.corrupt(canonical(data))

    def test_unknown_tag(self):
        data=json.loads(s._encode(boundary())); data['type']='_FutureDecisionBoundary'
        self.corrupt(canonical(data))

    def test_wrong_expected_model(self): self.corrupt(s._encode(boundary()),j._JournalRecord)

    def test_zoneinfo_encoding_rejected_before_timezone_loader(self):
        data=json.loads(s._encode(boundary()))
        data['fields']['decision_at']['zone']={'kind':'zoneinfo','key':'UTC','offset_us':0}
        with patch.object(s,'ZoneInfo',side_effect=AssertionError('timezone filesystem')) as lookup:
            self.corrupt(canonical(data))
        lookup.assert_not_called()

    def test_large_valid_event_is_bounded(self):
        value=boundary(MAX,at=datetime.max.replace(tzinfo=timezone.utc),generation=MAX)
        data=s._encode(records((value,))[0])
        self.assertLess(len(data),s._MAX_PAYLOAD_BYTES)
        self.assertEqual(s._encode(s._decode(data,j._JournalRecord)),data)

    def test_companion_determinism(self):
        events=(f.session(),q.durable(),boundary(1))
        self.assertEqual(replay(events),replay(events))


class CompatibilityTests(PureCase):
    def golden(self,version,events,expected):
        h=header(version); rs=records(events,h); raw=s._encode(h)
        digest=s._genesis(raw)
        for record in rs: digest=s._record_digest(raw,record.sequence,type(record.payload).__name__,s._encode(record),digest)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),expected[0])
        self.assertEqual(hashlib.sha256(b''.join(s._encode(r) for r in rs)).hexdigest(),expected[1])
        self.assertEqual(digest.hex(),expected[2])
        self.assertTrue(j._replay_paper_execution_journal(h,rs).restart_quarantine_required)

    def test_v1_original_bytes_and_hashes(self):
        events=(*f.prefix(),f.opened(1),f.status(2),f.execution(3),f.commission(),j._CallbackProgress(f.SID,3,3))
        self.golden(1,events,('f68cb69e5b98786ab65de192973908d9dfdfbaddffdb97f4d19e52c1be566218',
            'e7cb47803b7ba3472a9d51991b427794000e8235286fcc98bb78d2727d9b931d',
            '10a71fc4342901c43e9d466073a32958e256f3e77fd52a16d545deaaed63adbe'))

    def test_v2_original_bytes_and_hashes(self):
        events=(*v2.mixed(),q.durable(sequence=9),j._CallbackProgress(f.SID,9,9))
        self.golden(2,events,('a25fa8c219cd6bd67d6cd78f049eb5763c1b6ab46127537b91a62f23a6bd597d',
            '8ae2ea903c5f1468d85e3e9c802809c1dc909a126352c185722975c0bebc01b5',
            '4ddc2f9b8edfda30b94db7b77819101121a5342fff0ba20e091ab9d262ce54fc'))

    def test_v1_producer_certified_progress_remains_unchanged(self):
        value=j._replay_paper_execution_journal(header(1),records((f.session(),j._CallbackProgress(f.SID,5,5)),header(1)))
        self.assertEqual(value.callback_progress[0].processed_sequence,5)

    def test_v2_gap_progress_still_rejected(self):
        with self.assertRaises(ReadOnlyError): v2.replay((f.session(),j._CallbackProgress(f.SID,1,1)))

    def test_v3_uses_v2_gap_progress_semantics(self): self.denied((f.session(),j._CallbackProgress(f.SID,1,1)))

    def test_original_replay_semantics_across_v2_and_v3(self):
        a=v2.replay(v2.mixed()); b=replay(v2.mixed()).replay
        for field in fields(a):
            if field.name!='header': self.assertEqual(getattr(a,field.name),getattr(b,field.name))

    def test_inherited_decimal_and_presence_bytes_unchanged(self):
        for protocol in ('protobuf','legacy'):
            event=q.durable(q.observation(protocol=protocol))
            data=s._encode(event); rebuilt=s._decode(data,type(event))
            self.assertEqual(q.identity(rebuilt.observation),q.identity(event.observation))
            self.assertEqual(s._encode(rebuilt),data)


class StorageTests(sql_fixture.Fixture):
    def setUp(self):
        super().setUp()
        for name in ('socket.socket.connect','socket.create_connection','socket.getaddrinfo'):
            self.enterContext(patch(name,side_effect=AssertionError('network forbidden')))

    def new(self,path=None):
        store=s._SQLiteJournalStore._create(self.path if path is None else path,header())
        self.addCleanup(store._close); return store

    def reopen(self):
        store=s._SQLiteJournalStore._open(self.path,header())
        self.addCleanup(store._close); return store

    def test_v3_store_roundtrip_reopen_wal(self):
        self.append(f.session(),q.durable(),boundary(1))
        loaded=self.reopen()._load()
        value=j._replay_v3_decision_boundary_evidence(loaded.header,loaded.records)
        self.assertEqual(len(value.boundary_records),1)
        self.assertEqual(self.sql('PRAGMA journal_mode'),[('wal',)])
        self.assertEqual(self.sql('PRAGMA synchronous'),[(2,)])

    def test_sqlite_schema_and_versions_unchanged(self):
        self.assertEqual(self.sql('PRAGMA user_version'),[(1,)])
        self.assertEqual(self.sql('PRAGMA application_id'),[(0x504A4231,)])
        self.assertEqual(self.sql('SELECT name,type,tbl_name,sql FROM sqlite_schema'),
                         [(name,*value) for name,value in s._SCHEMA.items()])
        self.assertEqual(s._STORE_FORMAT,'paper-execution-journal-sqlite-v1')
        self.assertEqual(s._DOMAIN,b'paper-execution-journal-sqlite-v1')

    def test_invalid_frontier_quarantines_with_existing_policy(self):
        self.append(f.session())
        self.error(s._Code.QUARANTINED,self.store._append,boundary(1))
        self.error(s._Code.QUARANTINED,self.reopen)

    def test_invalid_earlier_boundary_cannot_be_repaired(self):
        self.append(f.session())
        self.error(s._Code.QUARANTINED,self.store._append,boundary(1))
        self.error(s._Code.QUARANTINED,self.store._append,q.durable())

    def test_timestamp_regression_quarantines(self):
        self.append(f.session(),boundary())
        self.error(s._Code.QUARANTINED,self.store._append,boundary(at=f.AT-timedelta(seconds=1)))

    def test_boundaries_do_not_advance_callback_progress(self):
        self.append(f.session(),q.durable(),boundary(1))
        self.assertEqual(self.store._load().replay.callback_progress,())

    def test_no_automatic_header_upgrade(self):
        self.error(s._Code.HEADER_MISMATCH,s._SQLiteJournalStore._open,self.path,header(2))

    def test_bad_digest_detected(self):
        self.append(f.session(),boundary())
        self.tamper('UPDATE records SET digest=? WHERE sequence=2',(b'x'*32,))
        self.error(s._Code.CORRUPT,self.store._load)

    def test_forged_canonical_frontier_recomputed_digest_is_still_corrupt(self):
        self.append(f.session(),boundary())
        loaded=self.store._load(); record=loaded.records[-1]
        encoded=s._encode(replace(record,payload=boundary(1)))
        previous=self.sql('SELECT previous_digest FROM records WHERE sequence=2')[0][0]
        digest=s._record_digest(s._encode(header()),2,Boundary.__name__,encoded,previous)
        self.tamper('UPDATE records SET encoded=?,digest=? WHERE sequence=2',(encoded,digest))
        self.tamper('UPDATE store_state SET tail_digest=? WHERE singleton=1',(digest,))
        self.error(s._Code.CORRUPT,self.store._load)


class InactivityTests(PureCase):
    def test_only_separately_qualified_owner_may_produce_boundary(self):
        # Source inspection is a test check, never part of replay semantics.
        root=Path(j.__file__).parent
        references=[]
        for path in root.glob('*.py'):
            if 'QualifiedDecisionBoundary' in path.read_text(): references.append(path.name)
        self.assertEqual(sorted(references),['paper_decision_status_evidence.py', 'paper_dispatch_journal.py',
            'paper_execution_journal.py','paper_execution_journal_sqlite.py'])
        constructors=[]
        for path in root.glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node,ast.Call) and getattr(node.func,'attr',getattr(node.func,'id','')) == '_QualifiedDecisionBoundaryObserved':
                    constructors.append(path.name)
        self.assertEqual(constructors,['paper_dispatch_journal.py'])
        self.assertFalse(hasattr(j,'_create_qualified_decision_boundary'))
        self.assertFalse(hasattr(j,'_evaluate_decision_status_intervals'))

    def test_no_transport_or_collection_import_to_replay(self):
        tree=ast.parse(inspect.getsource(j))
        modules={node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)}
        self.assertFalse(modules & {'ibapi','ibkr_paper_transport','paper_dispatch_journal',
            'paper_fresh_open_order_collection','paper_fresh_execution_collection',
            'paper_fresh_completed_order_collection','paper_recovery','paper_reconciliation',
            'paper_execution_journal_sqlite'})

    def test_no_public_export(self):
        import tradingbot_broker
        for name in ('QualifiedDecisionBoundaryObserved','V3DecisionBoundaryReplayEvidence'):
            self.assertFalse(hasattr(tradingbot_broker,name))
            self.assertFalse(hasattr(j,name))

    def test_no_authority_fields_or_management_methods(self):
        value=replay((f.session(),boundary()))
        for obj in (value,value.boundary_records[0].payload):
            for name in ('order_id','attempt_id','symbol','inventory','position','current_lifecycle',
                'current_remainder','ownership','protection','cancel','replace','exit','recovery',
                'clear_barrier','consume_receipt','restart_adoption','trading_authority','live_supported'):
                self.assertFalse(hasattr(obj,name))
        self.assertTrue(value.replay.restart_quarantine_required)
        self.assertIs(value.replay.header.account_mode,AccountMode.UNKNOWN)


class RuntimeInactivityTests(unittest.TestCase):
    def test_fresh_v3_runtime_never_produces_boundary_implicitly(self):
        from tests.test_ibkr_qualified_status_publication import RuntimeCase
        for name in ('socket.socket.connect','socket.create_connection','socket.getaddrinfo'):
            self.enterContext(patch(name,side_effect=AssertionError('network forbidden')))
        case=RuntimeCase('runTest'); case.setUp()
        try:
            case.registered(); case.qualified(); case.drain()
            loaded=case.journal._load()
            self.assertEqual((loaded.header.schema_version,loaded.header.format_id),(3,j._FORMAT_ID_V3))
            self.assertTrue(all(record.schema_version==3 for record in loaded.records))
            self.assertFalse(any(type(record.payload) is Boundary for record in loaded.records))
            self.assertTrue(loaded.replay.restart_quarantine_required)
        finally: case.tearDown(); case.doCleanups()


def invalid_primitive_case(name,field,value):
    def test(self):
        with self.assertRaises(ReadOnlyError): replace(boundary(),**{field:value})
        forged=f.forged(boundary(),**{field:value})
        with self.assertRaises(ReadOnlyError): records((f.session(),forged))
        with self.assertRaises(s._StorageError): s._encode(forged)
    setattr(ContractTests,'test_invalid_'+name,test)

for field in ('through_sequence','write_generation'):
    for name,value in (('bool',True),('float',1.0),('text','1'),('none',None),('negative',-1),('oversized',MAX+1)):
        invalid_primitive_case(field+'_'+name,field,value)
invalid_primitive_case('generation_zero','write_generation',0)
for name,value in (('none',None),('number',1),('foreign','session-unknown'),('uppercase','session-'+'A'*32),
                   ('wrong_prefix','journal-'+'1'*32),('nonascii','session-é'),('oversized','session-'+'1'*33)):
    invalid_primitive_case('session_'+name,'session_id',value)
for name,value in (('naive',f.AT.replace(tzinfo=None)),('text','2026-01-05'),('date',f.AT.date()),('none',None),
                   ('offset',f.AT.astimezone(timezone(timedelta(hours=1)))),
                   ('utc_alias',f.AT.replace(tzinfo=timezone(timedelta(0),'GMT'))),('fold',f.AT.replace(fold=1))):
    invalid_primitive_case('timestamp_'+name,'decision_at',value)


def version_isolation_case(version):
    def test(self):
        h=header(version)
        with self.assertRaises(ReadOnlyError): records((boundary(),),h)
        data=json.loads(s._encode(records((boundary(),))[0]))
        data['fields']['schema_version']=version
        self.corrupt(canonical(data),j._JournalRecord)
        with self.assertRaises(ReadOnlyError): j._replay_v3_decision_boundary_evidence(h,records((f.session(),),h))
    setattr(CompatibilityTests,f'test_boundary_rejected_under_v{version}',test)
for version in (1,2): version_isolation_case(version)


def bad_header_case(name,version,format_id):
    def test(self):
        with self.assertRaises(ReadOnlyError): replace(f.header(),schema_version=version,format_id=format_id)
    setattr(ContractTests,'test_bad_header_'+name,test)
for name,version,format_id in (
    ('zero',0,j._FORMAT_ID_V3),('future',4,j._FORMAT_ID_V3),('bool',True,j._FORMAT_ID_V3),
    ('float',3.0,j._FORMAT_ID_V3),('v3_v2',3,j._FORMAT_ID_V2),('v3_v1',3,j._FORMAT_ID),
    ('v1_v3',1,j._FORMAT_ID_V3),('v2_v3',2,j._FORMAT_ID_V3),('unknown_format',3,'unrecognized')):
    bad_header_case(name,version,format_id)


def codec_mutation_case(name,mutate):
    def test(self):
        data=json.loads(s._encode(boundary())); mutate(data['fields'])
        self.corrupt(canonical(data))
    setattr(CodecTests,'test_corrupt_'+name,test)
for field in ('through_sequence','write_generation'):
    for name,value in (('bool',True),('negative',-1),('oversized',MAX+1),('float',1.5),('text','1'),('null',None)):
        codec_mutation_case(field+'_'+name,lambda d,f=field,v=value:d.__setitem__(f,v))
for name,value in (('null',None),('text','today'),('missing_type',{}),('wrong_tag',{'type':'date','parts':[2026,1,5]})):
    codec_mutation_case('timestamp_'+name,lambda d,v=value:d.__setitem__('decision_at',v))
for name,field,value in (
    ('fold_bool','fold',False),('fold_one','fold',1),('short_parts','parts',[2026,1,5]),
    ('bool_part','parts',[2026,1,5,15,45,0,False]),('bad_month','parts',[2026,13,5,15,45,0,0]),
    ('bad_year','parts',[0,1,5,15,45,0,0]),('bad_microsecond','parts',[2026,1,5,15,45,0,1000000])):
    codec_mutation_case(name,lambda d,f=field,v=value:d['decision_at'].__setitem__(f,v))
for name,value in (('offset',{'kind':'fixed','name':'UTC','offset_us':1000000}),
                   ('alias',{'kind':'fixed','name':'GMT','offset_us':0}),
                   ('bool_offset',{'kind':'fixed','name':'UTC','offset_us':False}),
                   ('unknown_zone',{'kind':'unknown','name':'UTC','offset_us':0}),
                   ('null_zone',None)):
    codec_mutation_case(name,lambda d,v=value:d['decision_at'].__setitem__('zone',v))


def interruption_case(error_type,seam):
    def test(self):
        error=error_type(17 if error_type is SystemExit else 'original interruption')
        h=header(); rs=records((f.session(),boundary()),h)
        if seam=='model':
            context=patch.object(j,'_timestamp',side_effect=error); action=lambda:boundary()
        elif seam=='codec':
            context=patch.object(j,'_timestamp',side_effect=error); data=s._encode(rs[-1])
            action=lambda:s._decode(data,j._JournalRecord)
        else:
            context=patch.object(j._V2CallbackCoverage,'decision_boundary',side_effect=error)
            action=lambda:j._replay_v3_decision_boundary_evidence(h,rs)
        with context:
            with self.assertRaises(error_type) as caught: action()
        self.assertIs(caught.exception,error)
        if error_type is SystemExit: self.assertEqual(caught.exception.code,17)
    setattr(ContractTests,f'test_interrupt_{seam}_{error_type.__name__}',test)
for seam in ('model','codec','replay'):
    for error in (KeyboardInterrupt,SystemExit,GeneratorExit): interruption_case(error,seam)


def source_presence_case(index,protocol,changes):
    def test(self):
        obs=q.observation(protocol=protocol,changes=changes)
        event=q.durable(obs)
        result=replay((f.session(),event,boundary(1)))
        preserved=result.records[1].payload.observation
        self.assertEqual(q.identity(preserved),q.identity(obs))
        rebuilt=s._decode(s._encode(result.records[1]),j._JournalRecord)
        self.assertEqual(q.identity(rebuilt.payload.observation),q.identity(obs))
    setattr(ContractTests,f'test_inherited_presence_{index}_{protocol}',test)

for index,(protocol,changes) in enumerate((
    ('protobuf',{'filled':'0'}),('protobuf',{'filled':'-0.00'}),
    ('protobuf',{'remaining':'10.000'}),('protobuf',{'clientId':0}),
    ('protobuf',{'permId':0}),('protobuf',{'filled':str(j._QUALIFIED_UNSET_QUANTITY)}),
    ('legacy',{'order_id':0}),('legacy',{'client_id':0}),
    ('legacy',{'average_fill_price':0.0}),('legacy',{'last_fill_price':-0.0}),
    ('legacy',{'filled':f.D('0.000')}),('legacy',{'remaining':j._QUALIFIED_UNSET_QUANTITY}))):
    source_presence_case(index,protocol,changes)


def absent_presence_case(field):
    def test(self):
        from tradingbot_broker import ibkr_order_status_evidence as source
        from tests import test_ibkr_order_status_evidence as wire
        raw={k:v for k,v in wire.RAW.items() if k!=field}
        obs=source._adapt_protobuf_order_status(wire.Proto(**raw))
        result=replay((f.session(),q.durable(obs),boundary(1)))
        self.assertEqual(q.identity(result.records[1].payload.observation),q.identity(obs))
    setattr(ContractTests,'test_inherited_absence_'+field,test)
for field in q.SDK_NAMES: absent_presence_case(field)


def old_database_case(version):
    def test(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'old.db'; h=header(version)
            with s._SQLiteJournalStore._create(path,h) as store:
                store._append(f.session())
                before=store._connection.execute('SELECT header,genesis FROM metadata').fetchone()
            with s._SQLiteJournalStore._open(path,h) as store:
                loaded=store._load()
                self.assertEqual(loaded.header.schema_version,version)
                self.assertEqual(store._connection.execute('SELECT header,genesis FROM metadata').fetchone(),before)
                self.assertEqual(store._connection.execute('PRAGMA user_version').fetchone(),(1,))
                with self.assertRaises(ReadOnlyError): j._replay_v3_decision_boundary_evidence(loaded.header,loaded.records)
    setattr(RuntimeInactivityTests,f'test_old_v{version}_database_unchanged',test)
for version in (1,2): old_database_case(version)


def no_incorporation(self):
    h=header(); events=v2.mixed()
    before=j._replay_paper_execution_journal(h,records(events,h))
    after=replay((*events,boundary(8)),h).replay
    for field in fields(before):
        if field.name!='last_sequence': self.assertEqual(getattr(before,field.name),getattr(after,field.name))
ContractTests.test_boundary_does_not_change_accounting_lifecycle_reconciliation_or_quarantine=no_incorporation


def no_live_receipt_case(module_name,receipt_name):
    def test(self):
        import importlib
        receipt_class=getattr(importlib.import_module('tradingbot_broker.'+module_name),receipt_name)
        invalid=object.__new__(receipt_class)
        with self.assertRaises(ReadOnlyError): records((invalid,))
    setattr(InactivityTests,'test_does_not_consume_'+module_name,test)
for module_name,receipt_name in (
    ('paper_fresh_open_order_collection','_FreshOpenOrderReceipt'),
    ('paper_fresh_execution_collection','_FreshExecutionReceipt'),
    ('paper_fresh_completed_order_collection','_FreshCompletedOrderReceipt')):
    no_live_receipt_case(module_name,receipt_name)


def malformed_companion_case(name,mutate):
    def test(self):
        value=replay((f.session(),boundary()))
        changes=mutate(value)
        with self.assertRaises(ReadOnlyError): replace(value,**changes)
    setattr(ContractTests,'test_malformed_companion_'+name,test)
for name,mutate in (
    ('record_list',lambda v:{'records':list(v.records)}),
    ('boundary_list',lambda v:{'boundary_records':list(v.boundary_records)}),
    ('extra_boundary',lambda v:{'boundary_records':v.boundary_records*2}),
    ('boundary_wrong_reference',lambda v:{'boundary_records':(v.records[0],)}),
    ('missing_session',lambda v:{'records':(v.records[-1],)})):
    malformed_companion_case(name,mutate)
