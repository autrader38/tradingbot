"""Inactive V2 coverage: pure histories and temporary SQLite, never broker I/O."""

import ast
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta, timezone, tzinfo
from decimal import Decimal as D, Inexact, Rounded, localcontext
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from tests import test_paper_execution_journal as f
from tradingbot_broker import paper_execution_journal as j
from tradingbot_broker import paper_execution_journal_sqlite as s
from tradingbot_broker.readonly_models import AccountMode, ReadOnlyError


REASON = j._UntrackedCallbackReason.NO_TRACKED_NUMERIC_ORDER_IN_SESSION
SNAPSHOTS = (j._OpenOrderCallbackSnapshot, j._OrderStatusCallbackSnapshot,
             j._ExecutionCallbackSnapshot, j._CommissionCallbackSnapshot)
EVENTS = j._COVERAGE_EVENT_TYPES


def header():
    return replace(f.header(), schema_version=2, format_id='paper-execution-journal-v2')


def opened(order_id=7, **changes):
    return replace(j._OpenOrderCallbackSnapshot(order_id, 19, 'entry-1', 1234,
        'STK', 'SMART', 'TEST', 'USD', 'BUY', D(10), 'MKT', 'DAY', f.S.SUBMITTED), **changes)


def status(order_id=99, **changes):
    return replace(j._OrderStatusCallbackSnapshot(order_id, 19, f.S.SUBMITTED,
        D(0), D(10), D(0), D(0), 0), **changes)


def execution(order_id=99, **changes):
    return replace(j._ExecutionCallbackSnapshot(order_id, 19, 'entry-1',
        'foreign-exec', 1234, 'BOT', D(3), D('10.125')), **changes)


def commission(exec_id='exec-1', amount=D('0.25'), currency='USD', **changes):
    return replace(j._CommissionCallbackSnapshot(exec_id,
        j._commission_snapshot_report_id(exec_id, amount, currency), amount, currency, f.AT), **changes)


def untracked(sequence=1, snapshot=None, **changes):
    return replace(j._UntrackedCallbackObserved(f.source(sequence),
        opened(99) if snapshot is None else snapshot, REASON), **changes)


def duplicate(sequence=2, root=5, snapshot=None, **changes):
    return replace(j._DuplicateCallbackCovered(f.source(sequence),
        opened() if snapshot is None else snapshot, root), **changes)


def replay(events, h=None):
    h = header() if h is None else h
    return j._replay_paper_execution_journal(h, f.records(events, h))


def mixed():
    c = commission()
    return (*f.prefix(), f.opened(1), duplicate(), untracked(3, status()),
        f.execution(4), f.commission(report_id=c.report_id),
        j._CommissionCallbackCovered(f.source(5), c, 9), f.execution(6),
        untracked(7, execution()),
        j._UntrackedCommissionCallbackCovered(f.source(8), commission('foreign-exec'), 12),
        j._CallbackProgress(f.SID, 8, 8))


class PureCase(unittest.TestCase):
    def denied(self, events, h=None):
        with self.assertRaises(ReadOnlyError) as caught:
            replay(events, h)
        self.assertEqual(caught.exception.code, j._ERROR)

    def roundtrip(self, value):
        rebuilt = s._decode(s._encode(value), type(value))
        self.assertEqual(s._encode(rebuilt), s._encode(value))
        return rebuilt


class VersionTests(PureCase):
    def test_explicit_pairs_and_default_remains_v1(self):
        self.assertEqual((f.header().schema_version, f.header().format_id),
                         (1, 'paper-execution-journal-v1'))
        self.assertEqual((header().schema_version, header().format_id),
                         (2, 'paper-execution-journal-v2'))
        self.roundtrip(f.header()); self.roundtrip(header())

    def test_exact_baseline_v1_allowlist(self):
        self.assertEqual(tuple(t.__name__ for t in j._EVENT_TYPES_V1), (
            '_SessionRegistered', '_AttemptConsumed', '_NumericOrderIdReserved',
            '_OrderIdFloorObserved', '_BrokerOrderIdObserved', '_AllocatorExhausted',
            '_InvocationMayOccur', '_SdkReturnedNormally', '_AttemptClosedWithoutInvocation',
            '_ReconciliationRequired', '_OpenOrderObserved', '_OrderStatusObserved',
            '_ExecutionObserved', '_CommissionReceived', '_CallbackProgress'))
        self.assertEqual(j._EVENT_TYPES_V2, j._EVENT_TYPES_V1 + EVENTS + (j._QualifiedOrderStatusObserved,))

    def test_baseline_v1_canonical_bytes_and_chain_golden(self):
        # Independently compared with the exact 445ef299 baseline modules before
        # freezing these digests; no Git checkout or dynamic import during tests.
        events = (*f.prefix(), f.opened(1), f.status(2), f.execution(3), f.commission(),
                  j._CallbackProgress(f.SID, 3, 3))
        raw = s._encode(f.header()); records = f.records(events)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
            'f68cb69e5b98786ab65de192973908d9dfdfbaddffdb97f4d19e52c1be566218')
        digest = s._genesis(raw)
        for record in records:
            digest = s._record_digest(raw, record.sequence, type(record.payload).__name__,
                                      s._encode(record), digest)
        self.assertEqual(digest.hex(), '10a71fc4342901c43e9d466073a32958e256f3e77fd52a16d545deaaed63adbe')
        self.assertEqual(hashlib.sha256(b''.join(s._encode(r) for r in records)).hexdigest(),
            'e7cb47803b7ba3472a9d51991b427794000e8235286fcc98bb78d2727d9b931d')

    def test_v1_duplicate_source_physical_repeat_still_idempotent(self):
        old = replay((*f.prefix(), f.opened(1), f.opened(1)), f.header())
        self.assertEqual(len(old.attempts[0].open_observations), 1)
        self.denied((*f.prefix(), f.opened(1), f.opened(1)))

    def test_v1_duplicate_run_progress_remains_valid(self):
        old = replay((*f.prefix(), f.opened(1), j._CallbackProgress(f.SID, 3, 3)), f.header())
        self.assertEqual(old.callback_progress[0].processed_sequence, 3)
        self.denied((*f.prefix(), f.opened(1), j._CallbackProgress(f.SID, 3, 3)))

    def test_v1_untracked_progress_remains_valid(self):
        self.assertEqual(replay((f.session(), j._CallbackProgress(f.SID, 1, 1)), f.header())
                         .callback_progress[0].processed_sequence, 1)
        self.denied((f.session(), j._CallbackProgress(f.SID, 1, 1)))

    def test_v1_commission_progress_remains_valid(self):
        events = (*f.prefix(), f.execution(1), f.commission(), j._CallbackProgress(f.SID, 2, 2))
        self.assertEqual(len(replay(events, f.header()).attempts[0].commissions), 1)
        self.denied(events)

    def test_record_header_version_mismatch(self):
        self.denied((f.session(),), f.forged(header(), schema_version=1))


def header_case(changes):
    def test(self):
        with self.assertRaises(ReadOnlyError): replace(f.header(), **changes)
    return test

for name, changes in {
    'zero':dict(schema_version=0), 'negative':dict(schema_version=-1),
    'future':dict(schema_version=3), 'bool':dict(schema_version=True),
    'float':dict(schema_version=1.0), 'string':dict(schema_version='2'),
    'v2_v1_format':dict(schema_version=2), 'v1_v2_format':dict(format_id='paper-execution-journal-v2'),
    'unknown_format':dict(format_id='other'), 'format_type':dict(format_id=2),
}.items(): setattr(VersionTests, 'test_header_rejects_'+name, header_case(changes))


class ModelTests(PureCase):
    def test_foreign_timezone_getter_is_never_called(self):
        class ForeignZone(tzinfo):
            def utcoffset(self,value):
                raise AssertionError('foreign timezone getter')
        value=f.AT.replace(tzinfo=ForeignZone())
        with self.assertRaises(ReadOnlyError): commission(received_at=value)

    def test_snapshot_layout_is_exact_retained_callback_contract(self):
        expected = (
            ('order_id','client_id','order_ref','con_id','security_type','exchange','symbol',
             'currency','side','quantity','order_type','tif','status'),
            ('order_id','client_id','status','filled','remaining','average_price','last_price','parent_id'),
            ('order_id','client_id','order_ref','execution_id','con_id','side','shares','price'),
            ('exec_id','report_id','amount','currency','received_at'))
        for model, names in zip(SNAPSHOTS, expected):
            self.assertEqual(tuple(item.name for item in fields(model)), names)

    def test_source_snapshot_derivation_matches_independent_fixtures(self):
        for source, snapshot in ((f.opened(1), opened()),
            (f.status(1), status(7)), (f.execution(1), execution(7, execution_id='exec-1'))):
            self.assertEqual(j._callback_snapshot_identity(j._ordinary_source_snapshot(source)),
                             j._callback_snapshot_identity(snapshot))

    def test_missing_optional_open_fields_remain_none(self):
        value = opened(99, client_id=None, order_ref=None, exchange=None, symbol=None, currency=None)
        self.assertEqual(self.roundtrip(value), value)

    def test_identity_is_representation_sensitive(self):
        self.assertNotEqual(j._callback_snapshot_identity(status(filled=D('0.0'))),
                            j._callback_snapshot_identity(status(filled=D('-0.00'))))

    def test_commission_fingerprint_matches_cleared_producer(self):
        from tradingbot_broker.ibkr_paper_transport import _commission_report_id
        for value in (D('-0'), D('0.00'), D('-0.2500'), D('9E64'), D('1E-64')):
            self.assertEqual(commission(amount=value).report_id,
                             _commission_report_id('exec-1', value, 'USD'))


MODEL_VALUES = (opened(), status(), execution(), commission(), duplicate(), untracked(),
    j._CommissionCallbackCovered(f.source(1), commission(), 1),
    j._UntrackedCommissionCallbackCovered(f.source(1), commission(), 1))

def model_case(value, mode):
    def test(self):
        if mode == 'roundtrip': self.roundtrip(value)
        elif mode == 'frozen':
            with self.assertRaises(FrozenInstanceError): setattr(value, fields(value)[0].name, None)
        elif mode == 'opaque': self.assertEqual(repr(value), '<PrivatePaperExecutionJournalValue>')
        else:
            child = type('UnqualifiedSubclass', (type(value),), {})
            forged = object.__new__(child)
            for item in fields(value): object.__setattr__(forged, item.name, getattr(value,item.name))
            with self.assertRaises(s._StorageError): s._encode(forged)
    return test

for value in MODEL_VALUES:
    for mode in ('roundtrip','frozen','opaque','subclass'):
        setattr(ModelTests, 'test_'+type(value).__name__[1:]+'_'+mode, model_case(value, mode))


MALFORMED = {
    'open_bool_id':(opened(), 'order_id', True), 'open_negative_id':(opened(),'order_id',-1),
    'open_max_id':(opened(),'order_id',2**31), 'open_bool_client':(opened(),'client_id',True),
    'open_bad_ref':(opened(),'order_ref','bad\nreference'), 'open_bool_con':(opened(),'con_id',True),
    'open_zero_con':(opened(),'con_id',0), 'open_large_con':(opened(),'con_id',2**64),
    'open_bad_type':(opened(),'security_type',''), 'open_bad_exchange':(opened(),'exchange','\n'),
    'open_bad_symbol':(opened(),'symbol','x'*101), 'open_bad_currency':(opened(),'currency','é'),
    'open_bad_side':(opened(),'side',object()), 'open_zero_quantity':(opened(),'quantity',D(0)),
    'open_float_quantity':(opened(),'quantity',1.0), 'open_nan':(opened(),'quantity',D('NaN')),
    'open_inf':(opened(),'quantity',D('Infinity')), 'open_bad_order_type':(opened(),'order_type',''),
    'open_bad_tif':(opened(),'tif','\t'), 'open_string_status':(opened(),'status','Submitted'),
    'status_none_client':(status(),'client_id',None), 'status_bool_id':(status(),'order_id',True),
    'status_negative_parent':(status(),'parent_id',-1), 'status_bool_parent':(status(),'parent_id',False),
    'status_unknown_enum':(status(),'status',object()), 'status_negative_fill':(status(),'filled',D(-1)),
    'status_float_fill':(status(),'filled',0.0), 'status_negative_remaining':(status(),'remaining',D(-1)),
    'status_nan_average':(status(),'average_price',D('NaN')), 'status_inf_last':(status(),'last_price',D('Infinity')),
    'status_digits':(status(),'filled',D('1'*65)), 'status_exponent_high':(status(),'remaining',D('1E65')),
    'status_exponent_low':(status(),'last_price',D('1E-65')),
    'exec_bool_id':(execution(),'order_id',True), 'exec_bad_client':(execution(),'client_id',-1),
    'exec_empty_id':(execution(),'execution_id',''), 'exec_bad_ref':(execution(),'order_ref','\n'),
    'exec_bad_con':(execution(),'con_id',0), 'exec_bad_side':(execution(),'side','\n'),
    'exec_zero_shares':(execution(),'shares',D(0)), 'exec_float_shares':(execution(),'shares',3.0),
    'exec_nan_price':(execution(),'price',D('NaN')), 'exec_negative_price':(execution(),'price',D(-1)),
    'commission_empty_exec':(commission(),'exec_id',''), 'commission_wrong_report':(commission(),'report_id','commission-'+'0'*64),
    'commission_float':(commission(),'amount',0.25), 'commission_nan':(commission(),'amount',D('NaN')),
    'commission_inf':(commission(),'amount',D('Infinity')), 'commission_digits':(commission(),'amount',D('1'*65)),
    'commission_exponent':(commission(),'amount',D('1E65')), 'commission_lower_currency':(commission(),'currency','usd'),
    'commission_unicode_currency':(commission(),'currency','UŠD'), 'commission_short_currency':(commission(),'currency','US'),
    'commission_naive':(commission(),'received_at',f.AT.replace(tzinfo=None)),
    'commission_offset':(commission(),'received_at',f.AT.astimezone(timezone(timedelta(hours=1)))),
    'commission_timestamp_type':(commission(),'received_at','now'),
}

def malformed_case(value, field, bad):
    def test(self):
        with self.assertRaises(ReadOnlyError): replace(value, **{field:bad})
        with self.assertRaises(s._StorageError): s._encode(f.forged(value, **{field:bad}))
    return test

for name, (value, field, bad) in MALFORMED.items():
    setattr(ModelTests,'test_rejects_'+name, malformed_case(value,field,bad))


class CoverageTests(PureCase):
    def test_mixed_eight_exact_coverage_and_economics(self):
        result = replay(mixed()); state = result.attempts[0]
        self.assertEqual(result.callback_progress[0].processed_sequence,8)
        self.assertEqual(state.observed_execution_quantity,D(3))
        self.assertEqual(len(state.executions),1); self.assertEqual(len(state.commissions),1)
        self.assertEqual(len(state.open_observations),1); self.assertEqual(state.status_observations,())
        self.assertTrue(result.restart_quarantine_required)

    def test_duplicate_chain_can_span_multiple_progress_records(self):
        events = (*f.prefix(),f.opened(1),duplicate(),j._CallbackProgress(f.SID,2,3),
                  duplicate(3),j._CallbackProgress(f.SID,3,3))
        self.assertEqual(replay(events).callback_progress[0].processed_sequence,3)

    def test_nonadjacent_repeated_execution_has_direct_source(self):
        events = (*f.prefix(),f.execution(1),untracked(2,status()),f.execution(3),
                  j._CallbackProgress(f.SID,3,3))
        self.assertEqual(replay(events).attempts[0].observed_execution_quantity,D(3))

    def test_owned_commission_lookahead(self):
        c = commission()
        events = (*f.prefix(),f.execution(2),f.commission(report_id=c.report_id),
            j._CommissionCallbackCovered(f.source(1),c,6),j._CallbackProgress(f.SID,2,2))
        self.assertEqual(replay(events).callback_progress[0].processed_sequence,2)
        self.denied(events[:6]+(j._CallbackProgress(f.SID,2,2),))

    def test_untracked_commission_lookahead(self):
        events = (f.session(),untracked(2,execution()),
            j._UntrackedCommissionCallbackCovered(f.source(1),commission('foreign-exec'),2),
            j._CallbackProgress(f.SID,2,2))
        self.assertEqual(replay(events).attempts,())

    def test_unknown_commission_relationship_cannot_advance(self):
        self.denied((f.session(),j._CallbackProgress(f.SID,1,1)))

    def test_wrong_backing_journal_envelope(self):
        h = header(); records = list(f.records(mixed(),h))
        records[4]=replace(records[4],journal_id='journal-'+'c'*32)
        with self.assertRaises(ReadOnlyError): j._replay_paper_execution_journal(h,tuple(records))

    def test_same_number_in_other_session_does_not_adopt(self):
        events = (*f.prefix(),f.session(f.SID2,20,4),
                  untracked(1,opened(),source=f.source(1,f.SID2,4)),
                  j._CallbackProgress(f.SID2,1,1))
        self.assertEqual(len(replay(events).attempts[0].open_observations),0)


BAD_HISTORIES = {
 'duplicate_missing_root': (*f.prefix(),f.opened(1),duplicate(root=99)),
 'duplicate_forward_root': (*f.prefix(),f.opened(1),duplicate(root=7),f.opened(3)),
 'duplicate_self_root': (*f.prefix(),f.opened(1),duplicate(root=6)),
 'duplicate_progress_root': (*f.prefix(),f.opened(1),j._CallbackProgress(f.SID,1,1),duplicate(root=6)),
 'duplicate_wrong_type': (*f.prefix(),f.opened(1),duplicate(snapshot=status(7))),
 'duplicate_wrong_decimal_shape': (*f.prefix(),f.opened(1),duplicate(snapshot=opened(quantity=D('10.0')))),
 'duplicate_nonadjacent': (*f.prefix(),f.opened(1),untracked(2,status()),duplicate(3)),
 'duplicate_missing_previous': (*f.prefix(),f.opened(1),duplicate(3)),
 'duplicate_different_root': (*f.prefix(),f.opened(1),f.opened(2),duplicate(3,root=5)),
 'duplicate_twice': (*f.prefix(),f.opened(1),duplicate(),duplicate()),
 'duplicate_plus_source': (*f.prefix(),f.opened(1),duplicate(),f.opened(2)),
 'duplicate_wrong_session': (*f.prefix(),f.opened(1),f.session(f.SID2,20,4),duplicate(source=f.source(2,f.SID2,4))),
 'duplicate_wrong_generation': (*f.prefix(),f.opened(1),duplicate(source=f.source(2,generation=4))),
 'untracked_reserved_id': (*f.prefix(),untracked(snapshot=opened())),
 'untracked_source_conflict': (*f.prefix(),f.opened(1),untracked(snapshot=opened(99))),
 'untracked_twice': (f.session(),untracked(),untracked()),
 'untracked_wrong_generation': (f.session(),untracked(source=f.source(1,generation=4))),
 'untracked_missing_session': (untracked(),),
 'untracked_exec_conflict': (f.session(),untracked(1,execution()),untracked(2,execution(price=D(11)))),
 'tracked_then_untracked_exec': (*f.prefix(),f.execution(1),untracked(2,execution(execution_id='exec-1'))),
 'untracked_then_tracked_exec': (*f.prefix(),untracked(1,execution(execution_id='exec-1')),f.execution(2)),
 'progress_missing_prefix': (f.session(),untracked(2),j._CallbackProgress(f.SID,2,2)),
 'progress_missing_suffix': (f.session(),untracked(),j._CallbackProgress(f.SID,2,2)),
 'progress_impossible_through': (f.session(),untracked(2),j._CallbackProgress(f.SID,0,1)),
 'progress_processed_regression': (f.session(),untracked(),j._CallbackProgress(f.SID,1,2),j._CallbackProgress(f.SID,0,2)),
 'progress_through_regression': (f.session(),untracked(),j._CallbackProgress(f.SID,1,2),j._CallbackProgress(f.SID,1,1)),
 'progress_other_session': (f.session(),f.session(f.SID2,20,4),untracked(),j._CallbackProgress(f.SID2,1,1)),
 'progress_huge': (f.session(),j._CallbackProgress(f.SID,10**100,10**100)),
 'foreign_commission_wrong_exec': (f.session(),untracked(1,execution()),j._UntrackedCommissionCallbackCovered(f.source(2),commission(),2)),
 'foreign_commission_wrong_type': (f.session(),untracked(1,status()),j._UntrackedCommissionCallbackCovered(f.source(2),commission(),2)),
 'foreign_commission_missing_ref': (f.session(),j._UntrackedCommissionCallbackCovered(f.source(1),commission(),99)),
 'foreign_commission_forward_ref': (f.session(),j._UntrackedCommissionCallbackCovered(f.source(1),commission('foreign-exec'),3),untracked(2,execution())),
 'foreign_commission_duplicate': (f.session(),untracked(1,execution()),j._UntrackedCommissionCallbackCovered(f.source(2),commission('foreign-exec'),2),j._UntrackedCommissionCallbackCovered(f.source(2),commission('foreign-exec'),2)),
 'direct_duplicate_physical': (*f.prefix(),f.execution(1),f.execution(1)),
}

def bad_history_case(events):
    def test(self): self.denied(events)
    return test

for name, events in BAD_HISTORIES.items():
    setattr(CoverageTests,'test_rejects_'+name,bad_history_case(events))


def duplicate_kind_case(kind):
    def test(self):
        source = (f.opened if kind == 'open' else f.status if kind == 'status' else f.execution)(1)
        snapshot = opened() if kind == 'open' else status(7) if kind == 'status' else execution(7,execution_id='exec-1')
        events = (*f.prefix(),source,duplicate(2,snapshot=snapshot),duplicate(3,snapshot=snapshot),j._CallbackProgress(f.SID,3,3))
        result = replay(events)
        self.assertEqual(result.callback_progress[0].processed_sequence,3)
    return test

for kind in ('open','status','execution'):
    setattr(CoverageTests,'test_valid_duplicate_'+kind,duplicate_kind_case(kind))


def commission_case(amount, later=False):
    def test(self):
        c=commission(amount=amount); fact=f.commission(report_id=c.report_id,amount=amount)
        marker=j._CommissionCallbackCovered(f.source(2),c,6)
        events=(*f.prefix(),f.execution(1),fact,marker)
        if later:
            c2=replace(c,received_at=f.AT+timedelta(seconds=1))
            events+=(j._CommissionCallbackCovered(f.source(3),c2,6),)
        cursor=3 if later else 2
        result=replay(events+(j._CallbackProgress(f.SID,cursor,cursor),))
        self.assertEqual(len(result.attempts[0].commissions),1)
    return test

for index, amount in enumerate((D('0.25'),D('-0.25'),D('0'),D('-0.00'),D('0.2500'))):
    for later in (False,True):
        setattr(CoverageTests,f'test_commission_economic_{index}_{later}',commission_case(amount,later))


class ProgressTests(PureCase):
    def test_high_through_never_creates_coverage(self):
        events=(f.session(),untracked(),j._CallbackProgress(f.SID,1,10**100))
        self.assertEqual(replay(events).callback_progress[0].processed_sequence,1)
        self.denied(events+(j._CallbackProgress(f.SID,2,10**100),))

    def test_no_range_from_huge_processed(self):
        events=(f.session(),j._CallbackProgress(f.SID,10**100,10**100))
        with patch.object(j,'range',side_effect=AssertionError('unbounded range'),create=True): self.denied(events)

    def test_lookahead_hole_may_remain_unprocessed(self):
        events=(f.session(),untracked(3),j._CallbackProgress(f.SID,0,3),untracked(1),j._CallbackProgress(f.SID,1,3))
        self.assertEqual(replay(events).callback_progress[0].processed_sequence,1)

    def test_gap_completion_moves_frontier_across_existing_lookahead(self):
        events=(f.session(),untracked(3),untracked(1),untracked(2),j._CallbackProgress(f.SID,3,4))
        self.assertEqual(replay(events).callback_progress[0].processed_sequence,3)

    def test_repeated_identical_progress_valid(self):
        p=j._CallbackProgress(f.SID,1,1)
        self.assertEqual(replay((f.session(),untracked(),p,p)).callback_progress,(p,))


def version_event_case(value, version):
    def test(self):
        h=header() if version==2 else f.header()
        if version==1:
            with self.assertRaises(ReadOnlyError): f.records((value,),h)
        else: self.roundtrip(f.records((value,),h)[0])
    return test

for value in MODEL_VALUES[4:]:
    for version in (1,2):
        setattr(VersionTests,f'test_{type(value).__name__[1:]}_version_{version}',version_event_case(value,version))


def decimal_case(text, kind):
    def test(self):
        value=commission(amount=D(text)) if kind=='commission' else status(filled=D(text))
        name='amount' if kind=='commission' else 'filled'
        expected=getattr(value,name).as_tuple()
        with localcontext() as context:
            context.prec=1; context.traps[Inexact]=context.traps[Rounded]=True
            rebuilt=self.roundtrip(value)
            self.assertEqual(getattr(rebuilt,name).as_tuple(),expected)
    return test

for index,text in enumerate(('0','0.0','0.00','-0','-0.00','1','1.0','1.000','1E-64','9E64','9'*64)):
    for kind in ('commission','status'):
        setattr(ModelTests,f'test_decimal_{kind}_{index}',decimal_case(text,kind))


def pure_interruption_case(exception,seam):
    def test(self):
        error=exception('private interruption')
        if seam=='replay':
            target=patch.object(j._V2CallbackCoverage,'_register',side_effect=error)
            call=lambda: replay((f.session(),untracked()))
        else:
            value=commission()
            target=patch.object(j,'_commission_snapshot_report_id',side_effect=error)
            call=lambda: s._encode(value)
        with target:
            with self.assertRaises(exception) as caught: call()
        self.assertIs(caught.exception,error)
    return test

for exception in (KeyboardInterrupt,SystemExit,GeneratorExit):
    for seam in ('replay','codec'):
        setattr(ModelTests,f'test_{exception.__name__}_pure_{seam}',pure_interruption_case(exception,seam))


def payload_case(kind):
    def test(self):
        large=D('9'*64+'E64'); maximum=(1<<63)-1
        source=j._CallbackSource(f.SID,maximum,maximum)
        snap=opened(2**31-1,client_id=2**31-1,order_ref='r'*100,con_id=2**64-1,
            security_type='s'*100,exchange='e'*100,symbol='t'*100,currency='c'*100,
            side='b'*100,quantity=large,order_type='o'*100,tif='d'*100)
        cs=commission('e'*100,amount=large)
        values=(j._DuplicateCallbackCovered(source,snap,maximum),
                j._UntrackedCallbackObserved(source,snap,REASON),
                j._CommissionCallbackCovered(source,cs,maximum),
                j._UntrackedCommissionCallbackCovered(source,cs,maximum))
        value=values[kind]; h=header()
        record=j._JournalRecord(maximum,2,h.journal_id,h.account_binding,value)
        encoded=s._encode(record)
        self.assertLess(len(encoded),s._MAX_PAYLOAD_BYTES)
        self.assertEqual(s._encode(s._decode(encoded,j._JournalRecord)),encoded)
    return test

for kind in range(4): setattr(ModelTests,f'test_maximal_payload_{kind}',payload_case(kind))


MORE_BAD_HISTORIES = {
 'owned_commission_missing':(*f.prefix(),f.execution(1),j._CommissionCallbackCovered(f.source(2),commission(),99)),
 'owned_commission_wrong_type':(*f.prefix(),f.execution(1),j._CommissionCallbackCovered(f.source(2),commission(),5)),
 'owned_commission_forward':(*f.prefix(),f.execution(1),j._CommissionCallbackCovered(f.source(2),commission(),7),f.commission(report_id=commission().report_id)),
 'owned_commission_exec_mismatch':(*f.prefix(),f.execution(1),f.commission(report_id=commission().report_id),j._CommissionCallbackCovered(f.source(2),commission('other-exec'),6)),
 'owned_commission_report_mismatch':(*f.prefix(),f.execution(1),f.commission(),j._CommissionCallbackCovered(f.source(2),commission(),6)),
 'owned_commission_amount_mismatch':(*f.prefix(),f.execution(1),f.commission(report_id=commission().report_id,amount=D('0.5')),j._CommissionCallbackCovered(f.source(2),commission(),6)),
 'owned_commission_currency_mismatch':(*f.prefix(),f.execution(1),f.commission(report_id=commission().report_id),j._CommissionCallbackCovered(f.source(2),commission(currency='EUR'),6)),
 'owned_commission_cross_session':(*f.prefix(),f.execution(1),f.commission(report_id=commission().report_id),f.session(f.SID2,20,4),j._CommissionCallbackCovered(f.source(1,f.SID2,4),commission(),6)),
 'duplicate_commission_root':(*f.prefix(),f.execution(1),f.commission(report_id=commission().report_id),duplicate(root=6)),
 'duplicate_untracked_root':(f.session(),untracked(),duplicate(root=2)),
 'untracked_commission_cross_session':(f.session(),untracked(1,execution()),f.session(f.SID2,20,4),j._UntrackedCommissionCallbackCovered(f.source(1,f.SID2,4),commission('foreign-exec'),2)),
 'owned_commission_twice':(*f.prefix(),f.execution(1),f.commission(report_id=commission().report_id),j._CommissionCallbackCovered(f.source(2),commission(),6),j._CommissionCallbackCovered(f.source(2),commission(),6)),
}
for name,events in MORE_BAD_HISTORIES.items():
    setattr(CoverageTests,'test_rejects_'+name,bad_history_case(events))


class StorageCase(PureCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.path=Path(directory.name)/'journal.db'

    def create(self,h=None):
        value=s._SQLiteJournalStore._create(self.path,header() if h is None else h)
        self.addCleanup(value._close); return value

    def reopen(self,h=None):
        value=s._SQLiteJournalStore._open(self.path,header() if h is None else h)
        self.addCleanup(value._close); return value


class StorageTests(StorageCase):
    def test_complete_v2_mixed_roundtrip_and_reopen(self):
        store=self.create()
        for event in mixed():
            committed=store._append(event)
            self.assertIs(committed.record.payload,event)
        before=store._load(); store._close(); loaded=self.reopen()._load()
        self.assertEqual(tuple(s._encode(r) for r in before.records),tuple(s._encode(r) for r in loaded.records))
        self.assertEqual(loaded.replay.callback_progress[0].processed_sequence,8)

    def test_true_v1_database_unchanged_after_open(self):
        store=self.create(f.header())
        for event in (*f.prefix(),f.opened(1),j._CallbackProgress(f.SID,3,3)): store._append(event)
        before=store._load(); raw=store._connection.execute('SELECT header FROM metadata').fetchone()[0]
        digests=store._connection.execute('SELECT digest FROM records ORDER BY sequence').fetchall()
        store._close(); reopened=self.reopen(f.header())
        self.assertEqual(reopened._load().replay,before.replay)
        self.assertEqual(reopened._connection.execute('SELECT header FROM metadata').fetchone()[0],raw)
        self.assertEqual(reopened._connection.execute('SELECT digest FROM records ORDER BY sequence').fetchall(),digests)

    def test_v2_generic_container_constants_and_schema(self):
        store=self.create()
        self.assertEqual((s._APPLICATION_ID,s._USER_VERSION,s._STORE_FORMAT,s._DOMAIN),
            (0x504A4231,1,'paper-execution-journal-sqlite-v1',b'paper-execution-journal-sqlite-v1'))
        actual=store._connection.execute('SELECT name,type,tbl_name,sql FROM sqlite_schema').fetchall()
        self.assertEqual({name:(kind,table,sql) for name,kind,table,sql in actual},s._SCHEMA)
        for pragma,expected in (('application_id',s._APPLICATION_ID),('user_version',1),('journal_mode','wal'),('synchronous',2)):
            self.assertEqual(store._connection.execute('PRAGMA '+pragma).fetchone()[0],expected)

    def test_progress_gap_quarantines_v2(self):
        store=self.create(); store._append(f.session()); store._append(untracked())
        with self.assertRaises(s._StorageError) as caught: store._append(j._CallbackProgress(f.SID,2,2))
        self.assertEqual(caught.exception.code,s._Code.QUARANTINED.value)
        self.assertTrue(store._quarantined)

    def test_duplicate_physical_explanation_quarantines(self):
        store=self.create(); store._append(f.session()); store._append(untracked())
        with self.assertRaises(s._StorageError): store._append(untracked())
        self.assertTrue(store._quarantined)

    def test_backing_without_coverage_has_no_progress(self):
        store=self.create(); c=commission()
        for event in (*f.prefix(),f.execution(1),f.commission(report_id=c.report_id)): store._append(event)
        self.assertFalse(store._load().replay.callback_progress)
        with self.assertRaises(s._StorageError): store._append(j._CallbackProgress(f.SID,2,2))

    def test_coverage_without_progress_remains_historical(self):
        store=self.create(); store._append(f.session()); store._append(untracked()); store._close()
        result=self.reopen()._load().replay
        self.assertFalse(result.callback_progress); self.assertTrue(result.restart_quarantine_required)

    def test_wrong_header_does_not_migrate(self):
        store=self.create(f.header()); store._close()
        with self.assertRaises(s._StorageError): self.reopen()
        self.assertEqual(self.reopen(f.header())._header.schema_version,1)

    def test_wrong_thread_fails_before_write(self):
        store=self.create(); errors=[]
        # A driver thread-check error can mark the store closed while the worker
        # cannot close its owner-thread connection. Release that handle on this
        # thread even if the assertion fails, before Windows directory cleanup.
        self.addCleanup(s._SQLiteJournalStore._discard_connection,store._connection)
        def worker():
            try: store._append(f.session())
            except BaseException as error: errors.append(error)
        t=Thread(target=worker); t.start(); t.join(5); self.assertFalse(t.is_alive())
        self.assertEqual(len(errors),1)


def storage_interruption_case(exception,seam):
    def test(self):
        store=self.create(); store._append(f.session()); error=exception('private interruption')
        value=untracked(); expected=1
        if seam=='encode':
            target=patch.object(s,'_encode',side_effect=error)
        elif seam=='replay':
            target=patch.object(j._V2CallbackCoverage,'_register',side_effect=error)
        elif seam=='verify':
            target=patch.object(store,'_verify_commit',side_effect=error); expected=2
        else:
            original=s._commit
            def committing(connection):
                if seam=='postcommit': original(connection)
                raise error
            target=patch.object(s,'_commit',side_effect=committing)
            if seam=='postcommit': expected=2
        with target:
            with self.assertRaises(exception) as caught: store._append(value)
        self.assertIs(caught.exception,error)
        store._close(); loaded=self.reopen()._load()
        self.assertEqual(len(loaded.records),expected)
        self.assertFalse(loaded.replay.callback_progress)
    return test

for exception in (KeyboardInterrupt,SystemExit,GeneratorExit):
    for seam in ('encode','replay','precommit','postcommit','verify'):
        setattr(StorageTests,'test_'+exception.__name__+'_'+seam,storage_interruption_case(exception,seam))


class BoundaryTests(PureCase):
    def test_fresh_owner_v2_and_qualified_occurrence_is_durable(self):
        from tests.test_ibkr_qualified_status_publication import RuntimeCase
        case=RuntimeCase('runTest'); case.setUp()
        try:
            case.registered(); self.assertEqual(case.journal._header.schema_version,2)
            case.qualified(); self.assertEqual(case.drain().state.value,'CURRENT')
            self.assertEqual(case.journal._persistence_cursor,1)
            self.assertEqual(len(case.of_type(j._QualifiedOrderStatusObserved)),1)
        finally: case.tearDown(); case.doCleanups()

    def test_no_transport_storage_or_authority_import_in_journal(self):
        tree=ast.parse(inspect.getsource(j))
        modules={n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)}
        self.assertFalse(modules & {'ibapi','ibkr_paper_transport','paper_dispatch_journal',
            'paper_execution_journal_sqlite','paper_recovery','paper_reconciliation'})

    def test_account_mode_and_replay_layout_unchanged(self):
        result=replay(mixed())
        self.assertIs(result.header.account_mode,AccountMode.UNKNOWN)
        self.assertTrue(result.restart_quarantine_required)
        self.assertFalse(any('coverage' in item.name for item in fields(result)))

    def test_accounting_and_lifecycle_do_not_consume_exceptional_events(self):
        from tradingbot_broker import paper_execution_accounting as a
        events=mixed(); h=header(); records=f.records(events,h)
        evidence=a._AccountingEvidence(h,records,f.SID,8,8,0,0,True)
        result=a._derive_accounting(evidence)
        self.assertEqual(result.entries[0].recorded_execution_quantity,D(3))
        self.assertEqual(result.entries[0].known_commission_total,D('0.25'))

    def test_qualified_source_model_remains_private(self):
        import tradingbot_broker
        self.assertTrue(hasattr(j,'_QualifiedOrderStatusObserved'))
        self.assertFalse(any(hasattr(tradingbot_broker,t.__name__) for t in
                             EVENTS+SNAPSHOTS+(j._QualifiedOrderStatusObserved,)))


if __name__ == '__main__': unittest.main()
