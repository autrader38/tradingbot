"""Invented provider-shaped EOD research fixtures; no credential or real network."""

import ast
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, Inexact, Rounded, localcontext
from fractions import Fraction
from http.client import HTTPConnection
import json
from pathlib import Path
import shutil
import socket
import threading
import unittest
from unittest.mock import patch

from tradingbot_dashboard.demo_data import build_demo_snapshot
from tradingbot_dashboard.server import create_server, _CSP
from tradingbot_marketdata import massive, models, reference, research

ROOT = Path(__file__).resolve().parents[1]
HISTORY = (ROOT / 'tests/fixtures/massive_research_history.json').read_bytes()
REFERENCE = (ROOT / 'tests/fixtures/massive_reference.json').read_bytes()
NOW = datetime(2026, 10, 19, 16, tzinfo=timezone.utc)
KEY = 'SYNTHETIC_TEST_CREDENTIAL'


def history(body=HISTORY, ticker='AAPL'):
    return models._decode(body, ticker, 'history', NOW, date(2026, 8, 19), date(2026, 10, 18))


def ref(body=REFERENCE, ticker='AAPL', requested=date(2026, 10, 16)):
    return reference._decode_reference(body, ticker, requested)


def history_body(*, ticker='AAPL', rows=None, **root):
    raw = json.loads(HISTORY); raw['ticker'] = ticker
    if rows is not None: raw['results'] = rows; raw['resultsCount'] = len(rows)
    raw.update(root)
    return json.dumps(raw).encode()


def reference_body(*, ticker='AAPL', **fields):
    raw = json.loads(REFERENCE); raw['results']['ticker'] = ticker
    raw['results'].update(fields)
    return json.dumps(raw).encode()


class Offline(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for target in ('socket.socket.connect', 'socket.getaddrinfo'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('External network forbidden')))


class CalculationTests(Offline):
    def metrics(self, source=None, cap=None, now=NOW):
        return research._calculate(history() if source is None else source, now, ref() if cap is None else cap)

    def test_exact_eod_metrics_from_eleven_source_bars(self):
        m = self.metrics()
        self.assertEqual(m.price, Fraction(103, 25))
        self.assertEqual(m.change_pct, Fraction(3))
        self.assertEqual(m.average_volume_10, Fraction(1000001))
        self.assertEqual(m.relative_volume, Fraction(2))
        self.assertEqual(m.volume, Fraction(2000002))
        self.assertEqual(m.market_cap, Fraction(50000000))
        self.assertIs(research._assess(m, research._EODFilters())[0], research._ResearchOutcome.MET)

    def test_exact_source_references_and_immutable_result(self):
        source, cap = history(), ref()
        result = research._calculate(source, NOW, cap)
        self.assertIs(result.source, source); self.assertIs(result.reference, cap)
        self.assertFalse(hasattr(result, '__dict__'))
        with self.assertRaises(FrozenInstanceError): result.price = Fraction(1)

    def test_no_reference_does_not_pass_required_cap(self):
        m = research._calculate(history(), NOW)
        outcome, failed, missing = research._assess(m, research._EODFilters())
        self.assertIs(outcome, research._ResearchOutcome.INSUFFICIENT)
        self.assertEqual(failed, ()); self.assertEqual(missing, ('market_cap',))

    def test_cap_can_be_explicitly_excluded_from_research_subset(self):
        m = research._calculate(history(), NOW)
        self.assertIs(research._assess(m, research._EODFilters(require_market_cap=False))[0], research._ResearchOutcome.MET)

    def test_latest_bar_is_excluded_from_ten_bar_baseline(self):
        rows = json.loads(HISTORY)['results']; rows[-1]['v'] = 9000000
        m = self.metrics(history(history_body(rows=rows)))
        self.assertEqual(m.average_volume_10, 1000001)
        self.assertEqual(m.relative_volume, Fraction(9000000, 1000001))

    def test_only_immediately_preceding_ten_available_bars_count(self):
        rows = json.loads(HISTORY)['results']
        rows.insert(0, {**rows[0], 't': 1790827200000, 'v': 99999999})  # Oct 1 ET midnight.
        self.assertEqual(self.metrics(history(history_body(rows=rows))).average_volume_10, 1000001)

    def test_varied_baseline_volumes_are_summed_exactly_not_rounded(self):
        rows=json.loads(HISTORY)['results']
        for index,row in enumerate(rows[:-1]):row['v']=index+1
        m=self.metrics(history(history_body(rows=rows)))
        self.assertEqual(m.average_volume_10,Fraction(11,2))
        self.assertEqual(m.relative_volume,Fraction(4000004,11))

    def test_ten_total_bars_are_insufficient_for_ten_prior_volumes(self):
        rows = json.loads(HISTORY)['results'][1:]
        m = self.metrics(history(history_body(rows=rows)))
        self.assertIsNone(m.average_volume_10); self.assertIsNone(m.relative_volume)
        self.assertEqual(m.change_pct, 3)

    def test_one_bar_has_price_but_no_change_or_baseline(self):
        rows = json.loads(HISTORY)['results'][-1:]
        m = self.metrics(history(history_body(rows=rows)))
        self.assertEqual(m.price, Fraction(103,25)); self.assertIsNone(m.change_pct)

    def test_zero_prior_volume_is_valid_average_but_rvol_unavailable(self):
        rows = json.loads(HISTORY)['results']
        for row in rows[:-1]: row['v'] = 0
        m = self.metrics(history(history_body(rows=rows)))
        self.assertEqual(m.average_volume_10, 0); self.assertIsNone(m.relative_volume)

    def test_zero_latest_volume_remains_supplied(self):
        rows = json.loads(HISTORY)['results']; rows[-1]['v'] = 0
        m = self.metrics(history(history_body(rows=rows)))
        self.assertEqual(m.volume, 0); self.assertEqual(m.relative_volume, 0)

    def test_missing_volume_preserves_pre1_unavailable_series_contract(self):
        rows = json.loads(HISTORY)['results']; rows[0]['v'] = None
        m = self.metrics(history(history_body(rows=rows)))
        self.assertTrue(all(getattr(m, name) is None for name in research._METRICS))

    def test_zero_or_negative_previous_close_rejected_before_calculation(self):
        for value in (0, -1, None):
            rows = json.loads(HISTORY)['results']; rows[-2]['c'] = value
            if value is None:
                self.assertIsNone(self.metrics(history(history_body(rows=rows))).change_pct)
            else:
                with self.assertRaises(ValueError): history(history_body(rows=rows))

    def test_missing_sessions_are_not_interpolated_or_claimed_contiguous(self):
        rows = json.loads(HISTORY)['results']; rows[0]['t'] = 1790308800000  # Sep 25.
        m = self.metrics(history(history_body(rows=rows)))
        self.assertEqual(m.average_volume_10, 1000001)
        row = research._research_row('AAPL', (m.source,NOW), (m.reference,NOW), NOW)
        self.assertIn('NO_MARKET_CALENDAR_CONTINUITY_PROOF', row['coverage'])

    def test_adjustment_false_or_missing_blocks_metrics(self):
        for flag in (None, False):
            m = self.metrics(history(history_body(adjusted=flag)))
            self.assertTrue(all(getattr(m, name) is None for name in research._METRICS))

    def test_delayed_series_is_not_promoted_to_eligible_eod(self):
        self.assertIsNone(self.metrics(history(history_body(status='DELAYED'))).price)

    def test_stale_source_and_reference_are_unusable(self):
        m = self.metrics(now=NOW+timedelta(days=2))
        self.assertIsNone(m.price); self.assertIsNone(m.market_cap)

    def test_reference_wrong_requested_date_or_ticker_cannot_supply_cap(self):
        for cap in (ref(requested=date(2026,10,15)), ref(reference_body(ticker='MSFT'),'MSFT')):
            self.assertIsNone(self.metrics(cap=cap).market_cap)

    def test_no_history_or_empty_history_is_not_successfully_scanned(self):
        for source in (None, history(history_body(rows=[]))):
            m = research._calculate(source, NOW, ref())
            self.assertTrue(all(getattr(m,name) is None for name in research._METRICS))

    def test_previous_endpoint_is_not_history_for_research(self):
        body = history_body(rows=json.loads(HISTORY)['results'][-1:])
        source = models._decode(body,'AAPL','previous',NOW)
        with self.assertRaises(ValueError): research._calculate(source,NOW)

    def test_arbitrary_source_objects_are_rejected(self):
        for source in ({}, [], True, 'AAPL'):
            with self.assertRaises(ValueError): research._calculate(source,NOW)

    def test_hostile_decimal_context_does_not_change_results_or_thresholds(self):
        expected = self.metrics().payload()
        with localcontext() as context:
            context.prec = 1; context.traps[Inexact] = context.traps[Rounded] = True
            result = self.metrics()
            self.assertEqual(result.payload(), expected)
            self.assertIs(research._assess(result,research._EODFilters())[0], research._ResearchOutcome.MET)

    def test_recurring_change_ratio_is_not_rounded_for_qualification(self):
        m = replace(self.metrics(), change_pct=Fraction(29999999999999999,10**16))
        self.assertEqual(m.payload()['change_pct']['display'],'3.00')
        self.assertIs(research._assess(m,research._EODFilters())[0],research._ResearchOutcome.NOT_MET)

    def test_exact_ratios_are_serialized_separately_from_display_rounding(self):
        value = research._ratio(Fraction(1,3))
        self.assertEqual(value,{'numerator':'1','denominator':'3','display':'0.33'})

    def test_small_positive_price_is_not_displayed_as_economic_zero(self):
        m=replace(self.metrics(),price=Fraction(1,10**12))
        self.assertEqual(m.payload()['price']['display'],'0.000000000001')

    def test_half_even_display_rounding_handles_negative_and_signed_near_zero(self):
        for fraction, display in ((Fraction(1005,1000),'1.00'),(Fraction(1015,1000),'1.02'),
                                  (Fraction(-1005,1000),'-1.00'),(Fraction(-1,10000),'0.00')):
            self.assertEqual(research._ratio(fraction)['display'],display)

    def test_source_decimal_shapes_are_retained_under_exact_references(self):
        source=history(HISTORY.replace(b'4.12',b'4.120000'))
        m=self.metrics(source)
        self.assertEqual(m.source.bars[-1].close.as_tuple().exponent,-6)

    def test_lower_bound_and_inclusive_filter_boundaries(self):
        m=self.metrics()
        for name, value, expected in (('price','4.99',True),('price','5',False),
            ('change_pct','2.99',False),('change_pct','3',True),
            ('market_cap','49999999',False),('market_cap','50000000',True),
            ('average_volume_10','1000000',False),('average_volume_10','1000001',True)):
            result=research._assess(replace(m,**{name:Fraction(Decimal(value))}),research._EODFilters())[0]
            self.assertEqual(result is research._ResearchOutcome.MET,expected,(name,value))

    def test_optional_rvol_threshold_is_inclusive(self):
        for value, outcome in (('2',research._ResearchOutcome.MET),('2.000000000001',research._ResearchOutcome.NOT_MET)):
            self.assertIs(research._assess(self.metrics(),research._EODFilters(min_relative_volume=Decimal(value)))[0],outcome)

    def test_valid_failure_takes_precedence_over_missing_data(self):
        m=replace(research._calculate(history(),NOW),price=Fraction(6))
        outcome,failed,missing=research._assess(m,research._EODFilters())
        self.assertIs(outcome,research._ResearchOutcome.NOT_MET)
        self.assertEqual(failed,('price',));self.assertEqual(missing,('market_cap',))

    def test_and_semantics_all_enabled_criteria_must_pass(self):
        for field in ('price','change_pct','average_volume_10','market_cap'):
            m=replace(self.metrics(),**{field:None})
            self.assertIs(research._assess(m,research._EODFilters())[0],research._ResearchOutcome.INSUFFICIENT)

    def test_filter_models_reject_nonfinite_bool_float_and_out_of_bounds(self):
        for name in ('max_price','min_change','min_market_cap','min_average_volume','min_relative_volume'):
            for value in (True,5.0,'5',Decimal('NaN'),Decimal('Infinity'),Decimal('1e30')):
                with self.subTest(name=name,value=value),self.assertRaises(ValueError):research._EODFilters(**{name:value})
        for name,value in (('max_price',Decimal(0)),('min_change',Decimal(-101)),('min_average_volume',Decimal(-1)),('require_market_cap',1)):
            with self.assertRaises(ValueError):research._EODFilters(**{name:value})

    def test_row_metadata_exposes_dates_not_synchronous_or_strategy_authority(self):
        row=research._research_row('AAPL',(history(),NOW),(ref(),NOW),NOW)
        self.assertEqual(row['market_date'],'2026-10-16')
        self.assertEqual(len(row['baseline_dates']),10)
        self.assertEqual(row['close_dates'],['2026-10-15','2026-10-16'])
        self.assertEqual(row['reference']['requested_date'],'2026-10-16')
        self.assertIsNone(row['reference']['source_as_of'])
        self.assertIn('NOT_SYNCHRONOUS',row['reference_alignment'])
        self.assertEqual(row['full_strategy_qualification'],'NOT AVAILABLE')
        self.assertIn('VOLATILITY_HIGH_VERY_HIGH_UNVERIFIED',row['unqualified_requirements'])
        self.assertNotIn('score',row);self.assertNotIn('signal',row)

    def test_evaluation_is_deterministic_and_timezone_consistent(self):
        a=research._research_row('AAPL',(history(),NOW),(ref(),NOW),NOW)
        b=research._research_row('AAPL',(history(),NOW),(ref(),NOW),NOW.astimezone(timezone(timedelta(hours=-5))))
        self.assertEqual(a,b)

    def test_naive_evaluation_datetime_is_rejected(self):
        with self.assertRaises(ValueError):self.metrics(now=NOW.replace(tzinfo=None))


class ReferenceTests(Offline):
    def test_exact_cap_and_date_scope_no_source_time_invented(self):
        result=ref(reference_body(market_cap=50000000.25)).payload(NOW)
        self.assertEqual(result['market_cap'],'50000000.25')
        self.assertEqual(result['state'],'REFERENCE AVAILABLE')
        self.assertEqual((result['market'],result['locale'],result['currency']),('stocks','us','usd'))
        self.assertIsNone(result['source_as_of']);self.assertEqual(result['requested_date'],'2026-10-16')

    def test_missing_cap_and_partial_context_stay_unavailable(self):
        for name in ('market_cap','currency_name','market','locale'):
            result=ref(reference_body(**{name:None})).payload(NOW)
            self.assertEqual(result['state'],'UNAVAILABLE')

    def test_wrong_ticker_market_locale_currency_and_numeric_fields_rejected(self):
        for fields in ({'ticker':'MSFT'},{'market':'fx'},{'locale':'global'},{'currency_name':'eur'},
            {'market_cap':True},{'market_cap':'50000000'},{'market_cap':-1},{'market_cap':0},{'market_cap':1e30}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):ref(reference_body(**fields))

    def test_malformed_root_pagination_counts_and_utf8_fail_closed(self):
        for body in (b'\xff',b'[]',b' ' * 262145,REFERENCE+b'x',REFERENCE.replace(b'50000000',b'NaN')):
            with self.assertRaises((ValueError,UnicodeError)):ref(body)
        for root in ({'status':'ERROR'},{'count':True},{'count':2},{'next_url':'https://example.invalid/private'},{'results':[]}):
            raw=json.loads(REFERENCE);raw.update(root)
            with self.assertRaises(ValueError):ref(json.dumps(raw).encode())

    def test_duplicate_keys_are_not_silently_overwritten(self):
        with self.assertRaises(ValueError):ref(REFERENCE.replace(b'"market_cap":',b'"market_cap":1,"market_cap":'))

    def test_unreviewed_fields_sensitive_values_never_escape(self):
        raw=json.loads(reference_body(homepage_url=KEY,address={'secret':KEY},name=KEY));raw['message']=KEY
        self.assertNotIn(KEY,json.dumps(ref(json.dumps(raw).encode()).payload(NOW)))

    def test_requested_date_must_be_exact_date_not_datetime_or_string(self):
        for value in ('2026-10-16',NOW,True,None,date(2006,1,1)):
            with self.assertRaises(ValueError):ref(requested=value)

    def test_reference_current_future_and_stale_date_are_not_qualified(self):
        for requested,state in ((date(2026,10,19),'UNAVAILABLE'),(date(2026,10,20),'UNAVAILABLE'),(date(2026,10,14),'STALE')):
            self.assertEqual(ref(requested=requested).payload(NOW)['state'],state)

    def test_reference_is_frozen_slotted_and_payload_copies(self):
        source=ref();self.assertFalse(hasattr(source,'__dict__'))
        with self.assertRaises(FrozenInstanceError):source.market_cap=Decimal(1)
        p=source.payload(NOW);p['market_cap']='1';self.assertEqual(source.payload(NOW)['market_cap'],'50000000')


class AdapterTests(Offline):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.dict('os.environ',{'MASSIVE_API_KEY':KEY}))
        self.stack.enter_context(patch.object(massive,'_now',return_value=NOW))
        self.tick=self.stack.enter_context(patch.object(massive.time,'monotonic',return_value=1000.0))
        self.stack.enter_context(patch.object(massive,'_RATE',massive._RateGate()))
        self.read=self.stack.enter_context(patch.object(massive,'_read_response',side_effect=self.response))
        self.preview=massive._MarketDataPreview(enabled=True,watchlist=('AAPL','MSFT'))

    @staticmethod
    def response(path,key):
        ticker='MSFT' if '/MSFT' in path else 'AAPL'
        return 200,'application/json','',reference_body(ticker=ticker) if '/reference/' in path else history_body(ticker=ticker)

    def load_both(self,ticker='AAPL'):
        self.preview.observe(ticker,'history');self.tick.return_value+=15
        return self.preview.observe(ticker,'reference')

    def test_default_startup_configuration_and_research_never_request_or_read_key(self):
        with patch.object(massive.os.environ,'get',side_effect=AssertionError('Credential access forbidden')):
            preview=massive._MarketDataPreview();preview.configuration();result=preview.research()
            self.assertEqual(preview.observe('AAPL','history')['reason'],'PREVIEW_DISABLED')
        self.assertEqual(result['watchlist_total'],3);self.read.assert_not_called()

    def test_reference_requires_existing_eligible_history_and_never_autoloads_it(self):
        self.assertEqual(self.preview.observe('AAPL','reference')['reason'],'ELIGIBLE_HISTORY_REQUIRED')
        self.read.assert_not_called()

    def test_each_load_calls_one_allowlisted_endpoint_and_no_followup(self):
        self.preview.observe('AAPL','history');self.assertEqual(self.read.call_count,1)
        self.tick.return_value+=15;result=self.preview.observe('AAPL','reference')
        self.assertEqual(self.read.call_count,2)
        self.assertEqual(self.read.call_args.args[0],'/v3/reference/tickers/AAPL?date=2026-10-16')
        self.assertEqual(result['source_endpoint'],'ticker-overview')
        self.assertEqual(result['retrieved_at'],NOW.isoformat());self.assertIsNone(result['source_as_of'])

    def test_source_records_feed_research_without_new_network(self):
        self.load_both();before=self.read.call_count
        data=self.preview.research();row=data['rows'][0]
        self.assertEqual(row['default_outcome'],'EOD CRITERIA MET')
        self.assertEqual(data['rows'][1]['history_state'],'UNLOADED')
        self.assertEqual(self.read.call_count,before);self.assertNotIn(KEY,json.dumps(data))

    def test_reference_request_uses_shared_rate_gate_with_history(self):
        self.preview.observe('AAPL','history')
        self.assertEqual(self.preview.observe('AAPL','reference')['reason'],'RATE_LIMITED')
        self.assertEqual(self.read.call_count,1)

    def test_four_call_rolling_budget_spans_all_endpoints_and_instances(self):
        self.load_both();self.tick.return_value+=15
        self.preview.observe('MSFT','history');self.tick.return_value+=15
        self.preview.observe('MSFT','reference')
        other=massive._MarketDataPreview(enabled=True)
        self.tick.return_value=1059.9
        self.assertEqual(other.observe('AAPL','previous')['reason'],'RATE_LIMITED')
        self.assertEqual(self.read.call_count,4)
        self.tick.return_value=1060;other.observe('AAPL','previous');self.assertEqual(self.read.call_count,5)

    def test_reference_cache_retains_source_date_and_uses_no_budget(self):
        first=self.load_both();cached=self.preview.observe('AAPL','reference')
        self.assertTrue(cached['cached']);self.assertEqual(self.read.call_count,2)
        self.assertEqual(first['requested_date'],cached['requested_date'])

    def test_reference_date_changes_do_not_reuse_an_old_date_cache(self):
        self.load_both();self.tick.return_value+=400
        old=json.loads(HISTORY);old['results']=old['results'][:-1];old['resultsCount']-=1
        self.read.side_effect=None;self.read.return_value=(200,'application/json','',json.dumps(old).encode())
        self.preview.observe('AAPL','history');self.tick.return_value+=15
        self.read.return_value=(200,'application/json','',REFERENCE)
        result=self.preview.observe('AAPL','reference')
        self.assertEqual(result['requested_date'],'2026-10-15')
        self.assertFalse(result['cached']);self.assertEqual(self.read.call_count,4)

    def test_reloaded_history_does_not_silently_align_old_reference(self):
        self.load_both();self.tick.return_value+=400
        raw=json.loads(HISTORY);raw['results']=raw['results'][:-1];raw['resultsCount']-=1
        self.read.side_effect=None;self.read.return_value=(200,'application/json','',json.dumps(raw).encode())
        self.preview.observe('AAPL','history')
        row=self.preview.research()['rows'][0]
        self.assertIsNone(row['metrics']['market_cap']);self.assertFalse(row['reference_eligible'])

    def test_stale_loaded_records_do_not_remain_qualified(self):
        self.load_both()
        with patch.object(massive,'_now',return_value=NOW+timedelta(days=2)):
            row=self.preview.research()['rows'][0]
            self.assertFalse(row['history_eligible']);self.assertFalse(row['reference_eligible'])
            self.assertEqual(row['history_state'],'STALE')

    def test_clock_regression_cannot_qualify_future_retrieved_history_or_reference(self):
        self.load_both()
        with patch.object(massive,'_now',return_value=NOW-timedelta(hours=1)):
            row=self.preview.research()['rows'][0]
            self.assertFalse(row['history_eligible']);self.assertFalse(row['reference_eligible'])
            self.assertEqual(row['history_reason'],'RETRIEVAL_TIME_CONFLICT')
            self.assertEqual(self.preview.observe('AAPL','reference')['reason'],'ELIGIBLE_HISTORY_REQUIRED')

    def test_missing_cap_retained_as_unavailable_not_invented(self):
        self.preview.observe('AAPL','history');self.tick.return_value+=15
        self.read.side_effect=None;self.read.return_value=(200,'application/json','',reference_body(market_cap=None))
        self.preview.observe('AAPL','reference')
        row=self.preview.research()['rows'][0]
        self.assertIsNone(row['metrics']['market_cap']);self.assertFalse(row['reference_eligible'])

    def test_provider_permissions_limits_timeout_and_malformed_reference_sanitized(self):
        for code,reason in ((401,'AUTHENTICATION_FAILED'),(403,'ACCESS_DENIED'),(404,'NOT_FOUND'),(429,'RATE_LIMITED'),(500,'PROVIDER_UNAVAILABLE')):
            with self.subTest(code=code):
                self.stack.enter_context(patch.object(massive,'_RATE',massive._RateGate()))
                preview=massive._MarketDataPreview(enabled=True)
                self.read.side_effect=self.response;preview.observe('AAPL','history');self.tick.return_value+=15
                self.read.side_effect=None;self.read.return_value=(code,'application/json','',KEY.encode())
                result=preview.observe('AAPL','reference')
                self.assertEqual(result['reason'],reason);self.assertNotIn(KEY,json.dumps(result));self.tick.return_value+=1000

    def test_invalid_reference_never_publishes_raw_error_or_overwrites_valid_evidence(self):
        self.load_both();self.tick.return_value+=400
        self.read.side_effect=None;self.read.return_value=(200,'application/json','',KEY.encode())
        result=self.preview.observe('AAPL','reference')
        self.assertEqual(result['reason'],'INVALID_RESPONSE');self.assertNotIn(KEY,json.dumps(result))
        self.assertTrue(self.preview.research()['rows'][0]['reference_eligible'])

    def test_timeout_and_network_failure_are_sanitized_without_followup(self):
        for error,reason in ((TimeoutError(KEY),'TIMEOUT'),(OSError(KEY),'NETWORK_UNAVAILABLE')):
            self.read.side_effect=self.response
            self.preview.observe('AAPL','history');self.tick.return_value+=400
            self.read.side_effect=error
            before=self.read.call_count
            result=self.preview.observe('AAPL','reference')
            self.assertEqual(result['reason'],reason);self.assertNotIn(KEY,json.dumps(result))
            self.assertEqual(self.read.call_count,before+1);self.tick.return_value+=400

    def test_loaded_and_cache_storage_are_bounded_by_five_ticker_policy(self):
        self.load_both('AAPL');self.tick.return_value+=15;self.load_both('MSFT')
        self.assertLessEqual(len(self.preview._loaded),2*len(self.preview._watchlist))
        self.assertLessEqual(len(self.preview._cache),3*len(self.preview._watchlist))

    def test_research_does_not_mutate_demo_snapshot_or_trading_state(self):
        before=build_demo_snapshot().to_payload();self.load_both();self.preview.research()
        self.assertEqual(before,build_demo_snapshot().to_payload())


def local_only(stack,port):
    connect,resolve=socket.socket.connect,socket.getaddrinfo
    def guarded_connect(sock,address):
        if address!=('127.0.0.1',port):raise AssertionError('External socket forbidden')
        return connect(sock,address)
    def guarded_resolve(host,*args,**kwargs):
        if host!='127.0.0.1':raise AssertionError('External DNS forbidden')
        return resolve(host,*args,**kwargs)
    stack.enter_context(patch.object(socket.socket,'connect',guarded_connect))
    stack.enter_context(patch.object(socket,'getaddrinfo',guarded_resolve))


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict('os.environ',{'MASSIVE_API_KEY':KEY}))
        self.stack.enter_context(patch.object(massive,'_now',return_value=NOW))
        self.tick=self.stack.enter_context(patch.object(massive.time,'monotonic',return_value=1000))
        self.stack.enter_context(patch.object(massive,'_RATE',massive._RateGate()))
        self.read=self.stack.enter_context(patch.object(massive,'_read_response',side_effect=AdapterTests.response))
        self.server=create_server(port=0,market_data_preview=True,watchlist=('AAPL','MSFT'))
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True);self.thread.start()
        self.addCleanup(self.stop);local_only(self.stack,self.server.server_port)

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)

    def request(self,path,method='GET',headers=None):
        c=HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            c.request(method,path,headers=headers or {});r=c.getresponse();return r.status,dict(r.getheaders()),r.read()
        finally:c.close()

    def test_research_get_and_head_are_local_only_no_provider_work(self):
        for method in ('GET','HEAD'):
            code,headers,body=self.request('/api/market-data/research',method)
            self.assertEqual(code,200);self.assertEqual(headers['Content-Security-Policy'],_CSP)
            if method=='HEAD':self.assertEqual(body,b'')
        self.read.assert_not_called()

    def test_explicit_reference_get_is_guarded_and_date_is_internal(self):
        header={'X-Velocity-Market-Preview':'1'}
        self.request('/api/market-data/observation?ticker=AAPL&view=history',headers=header)
        self.tick.return_value+=15
        code,_,body=self.request('/api/market-data/observation?ticker=AAPL&view=reference',headers=header)
        self.assertEqual(code,200);self.assertEqual(json.loads(body)['requested_date'],'2026-10-16')
        self.assertNotIn(KEY.encode(),body)

    def test_reference_rejects_head_writes_and_cross_origin_without_requests(self):
        route='/api/market-data/observation?ticker=AAPL&view=reference'
        for method in ('HEAD','POST','PUT','PATCH','DELETE','OPTIONS'):
            self.assertEqual(self.request(route,method)[0],405)
        for header in ({},{'X-Velocity-Market-Preview':'1','Origin':'https://example.invalid'},
            {'X-Velocity-Market-Preview':'1','Host':'example.invalid'},
            {'X-Velocity-Market-Preview':'1','Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(self.request(route,headers=header)[0],403)
        self.read.assert_not_called()

    def test_no_user_chosen_reference_date_urls_or_extra_query_fields(self):
        for suffix in ('&date=2026-10-16','&url=bad','&ticker=MSFT'):
            self.assertEqual(self.request('/api/market-data/observation?ticker=AAPL&view=reference'+suffix,
                headers={'X-Velocity-Market-Preview':'1'})[0],400)
        self.read.assert_not_called()

    def test_research_route_has_no_mutating_methods_or_cors(self):
        for method in ('POST','PUT','PATCH','DELETE'):
            self.assertEqual(self.request('/api/market-data/research',method)[0],405)
        self.assertEqual(self.request('/api/market-data/research',headers={'Host':'example.invalid'})[0],403)
        self.assertEqual(self.request('/api/market-data/research?url=bad')[0],404)
        self.read.assert_not_called()

    def test_new_script_is_fixed_allowlisted_local_asset(self):
        code,headers,body=self.request('/eod-scanner.js')
        self.assertEqual(code,200);self.assertIn('javascript',headers['Content-Type'])
        self.assertEqual(body,(ROOT/'tradingbot_dashboard/static/eod-scanner.js').read_bytes())
        self.assertEqual(self.request('/eod-scanner.js/../server.py')[0],404)


class StaticTests(unittest.TestCase):
    def test_pure_calculations_and_reference_parser_have_no_io_or_broker_imports(self):
        for name in ('research.py','reference.py'):
            tree=ast.parse((ROOT/'tradingbot_marketdata'/name).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):modules=[n.name for n in node.names]
                elif isinstance(node,ast.ImportFrom):modules=[node.module or '']
                else:modules=[]
                self.assertFalse(any(m.startswith(('tradingbot_broker','tradingbot_backtest','strategy','socket','http','sqlite3','subprocess','os','pathlib','time')) for m in modules))
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):self.assertNotIn(node.func.attr,('now','today','connect','request','write'))

    def test_frontend_is_local_only_has_no_persistence_order_api_or_auto_polling(self):
        text=(ROOT/'tradingbot_dashboard/static/eod-scanner.js').read_text()
        for forbidden in ('https://','api.massive.com','Bearer','MASSIVE_API_KEY','localStorage','sessionStorage',
                          'setInterval','setTimeout','WebSocket','innerHTML','placeOrder','cancelOrder','document.cookie'):
            self.assertNotIn(forbidden,text)
        self.assertIn('BigInt',text);self.assertIn('X-Velocity-Market-Preview',text)

    def test_real_panel_preserves_separate_demo_and_unqualified_strategy(self):
        html=(ROOT/'tradingbot_dashboard/static/index.html').read_text()
        for marker in ('Real EOD Watchlist Scanner','FULL STRATEGY QUALIFICATION: NOT AVAILABLE',
                       'DEMO MARKET DATA — NO LIVE FEED','High / Very High volatility and ABCD setup qualification',
                       'TRADING BLOCKED','READ-ONLY REQUIRED'):
            self.assertIn(marker,html)


class BrowserTests(unittest.TestCase):
    """Optional installed browser. Provider and quota grants are offline test seams."""
    @classmethod
    def setUpClass(cls):
        executable=shutil.which('chromium') or shutil.which('chromium-browser')
        if not executable:raise unittest.SkipTest('Optional installed Chromium unavailable')
        try:from playwright.sync_api import sync_playwright
        except ImportError:raise unittest.SkipTest('Optional installed Playwright unavailable')
        cls.driver=sync_playwright().start();cls.addClassCleanup(cls.driver.stop)
        cls.browser=cls.driver.chromium.launch(executable_path=executable,headless=True,
            args=['--no-sandbox','--disable-background-networking'])
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict('os.environ',{'MASSIVE_API_KEY':KEY}))
        self.stack.enter_context(patch.object(massive,'_now',return_value=NOW))
        self.stack.enter_context(patch.object(massive,'_RATE',massive._RateGate()))
        self.grant=self.stack.enter_context(patch.object(massive._RATE,'reserve',return_value=0))
        self.read=self.stack.enter_context(patch.object(massive,'_read_response',side_effect=AdapterTests.response))
        self.errors=[];self.external=[];self.requests=[]

    def open(self,enabled=True):
        self.server=create_server(port=0,market_data_preview=enabled,watchlist=('AAPL','MSFT','NVDA'))
        thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        def stop():self.server.shutdown();self.server.server_close();thread.join(timeout=3)
        self.addCleanup(stop);local_only(self.stack,self.server.server_port)
        origin=f'http://127.0.0.1:{self.server.server_port}'
        context=self.browser.new_context(viewport={'width':1440,'height':900},reduced_motion='reduce',service_workers='block')
        self.addCleanup(context.close)
        def route(request):
            self.requests.append(request.request.url)
            if not request.request.url.startswith(origin+'/'):
                self.external.append(request.request.url);request.abort()
            else:
                self.assertNotIn('authorization',request.request.headers)
                self.assertNotIn(KEY,request.request.url)
                request.continue_()
        context.route('**/*',route)
        self.page=context.new_page();self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.goto(origin+'/')
        self.page.locator('#eod-total').filter(has_text='3').wait_for()

    def tearDown(self):
        self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])
        if hasattr(self,'page'):self.assertNotIn(KEY,self.page.content())

    def row(self,ticker='AAPL'):
        return self.page.locator(f'#eod-rows tr[data-ticker="{ticker}"]')

    def load(self,ticker='AAPL',cap=True):
        self.page.locator('#eod-ticker').select_option(ticker)
        self.page.locator('#eod-load-history:not([disabled])').wait_for()
        self.page.locator('#eod-load-history').click()
        self.row(ticker).locator('td').nth(8).filter(has_text='REAL END-OF-DAY').wait_for()
        self.page.locator('#eod-load-history:not([disabled])').wait_for()
        if cap:
            self.page.locator('#eod-load-reference:not([disabled])').wait_for()
            self.page.locator('#eod-load-reference').click()
            self.row(ticker).locator('td').nth(6).filter(has_text='50000000.00').wait_for()
            self.page.locator('#eod-load-history:not([disabled])').wait_for()

    def test_default_demo_is_offline_and_unloaded_not_counted_as_scanned(self):
        self.open(enabled=False)
        self.assertTrue(self.page.locator('#eod-load-history').is_disabled())
        self.assertEqual(self.page.locator('#eod-history-count').inner_text(),'0')
        self.assertEqual(self.page.locator('#eod-match-count').inner_text(),'0')
        self.assertEqual(self.page.locator('#eod-incomplete-count').inner_text(),'3')
        self.assertEqual(self.page.locator('#scanner-counts').inner_text(),'13 MATCHES / 24 SCANNED')
        self.read.assert_not_called()

    def test_enabled_startup_and_filters_have_no_provider_requests(self):
        self.open();before=len(self.requests);self.page.locator('#eod-max-price').fill('10')
        self.page.locator('#eod-sort').select_option('price')
        self.page.locator('#eod-search').fill('aapl')
        self.page.locator('#eod-loosen').click();self.page.locator('#eod-reset').click()
        self.read.assert_not_called();self.assertTrue(self.page.locator('#eod-load-reference').is_disabled())
        self.assertEqual(len(self.requests),before)

    def test_history_then_reference_are_two_distinct_manual_loads(self):
        self.open();self.load(cap=False)
        self.assertEqual(self.read.call_count,1)
        self.assertIn('INSUFFICIENT DATA',self.row().inner_text())
        self.assertEqual(self.page.locator('#eod-history-count').inner_text(),'1')
        self.assertEqual(self.page.locator('#eod-reference-count').inner_text(),'0')
        self.page.locator('#eod-load-reference').click()
        self.row().locator('.eod-outcome').filter(has_text='EOD CRITERIA MET').wait_for()
        self.assertEqual(self.read.call_count,2)
        self.assertEqual(self.page.locator('#eod-match-count').inner_text(),'1')

    def test_exact_display_provenance_and_strategy_limitations(self):
        self.open();self.load()
        cells=self.row().locator('td')
        self.assertEqual(cells.nth(1).inner_text(),'4.1200')
        self.assertEqual(cells.nth(2).inner_text(),'3.00')
        self.assertEqual(cells.nth(4).inner_text(),'1000001.00')
        self.assertEqual(cells.nth(5).inner_text(),'2.00')
        self.assertIn('Cap as-of: UNAVAILABLE',cells.nth(7).inner_text())
        self.row().locator('summary').click()
        text=self.row().inner_text()
        self.assertIn('FULL STRATEGY QUALIFICATION: NOT AVAILABLE',text)
        self.assertIn('2026-10-15, 2026-10-16',text)
        self.assertIn('NOT SYNCHRONOUS',text)

    def test_price_change_cap_volume_and_rvol_filter_boundaries(self):
        self.open();self.load();before=self.read.call_count
        for identity,value,expected in (('eod-max-price','4.12','EOD CRITERIA NOT MET'),
            ('eod-max-price','4.120000000001','EOD CRITERIA MET'),
            ('eod-min-change','3.000000000001','EOD CRITERIA NOT MET'),
            ('eod-min-change','3','EOD CRITERIA MET'),('eod-min-cap','50.000000000001','EOD CRITERIA NOT MET'),
            ('eod-min-cap','50','EOD CRITERIA MET'),('eod-min-average','1000001','EOD CRITERIA NOT MET'),
            ('eod-min-average','1000000','EOD CRITERIA MET'),('eod-min-rvol','2','EOD CRITERIA MET'),
            ('eod-min-rvol','2.000000000001','EOD CRITERIA NOT MET')):
            self.page.locator('#'+identity).fill(value)
            self.assertEqual(self.row().locator('.eod-outcome').inner_text(),expected,(identity,value))
        self.assertEqual(self.read.call_count,before)

    def test_hidden_precision_is_not_replaced_by_display_rounding(self):
        rows=json.loads(HISTORY)['results']
        for r in rows[:-1]:r['v']=1000000
        body=history_body(rows=rows).replace(b'"v": 1000000',b'"v": 1000000.000000000001',1)
        self.read.side_effect=lambda path,key:(200,'application/json','',REFERENCE if '/reference/' in path else body)
        self.open();self.load()
        self.assertEqual(self.row().locator('td').nth(4).inner_text(),'1000000.00')
        self.assertEqual(self.row().locator('.eod-outcome').inner_text(),'EOD CRITERIA MET')

    def test_invalid_inputs_are_visible_and_publish_no_matches(self):
        self.open();self.load();before=self.read.call_count
        for value in ('','0','1000001','-1'):
            self.page.locator('#eod-max-price').fill(value)
            self.assertEqual(self.page.locator('#eod-max-price').get_attribute('aria-invalid'),'true')
            self.assertEqual(self.page.locator('#eod-match-count').inner_text(),'0')
        self.page.locator('#eod-reset').click()
        self.assertEqual(self.page.locator('#eod-match-count').inner_text(),'1')
        self.assertEqual(self.read.call_count,before)

    def test_native_bad_input_is_not_mistaken_for_optional_blank_rvol(self):
        self.open();self.load()
        control=self.page.locator('#eod-min-rvol')
        control.focus();control.press('e')
        self.assertTrue(control.evaluate('(el) => el.validity.badInput'))
        self.assertEqual(control.input_value(),'')
        self.assertEqual(control.get_attribute('aria-invalid'),'true')
        self.assertEqual(self.page.locator('#eod-match-count').inner_text(),'0')
        self.page.locator('#eod-reset').click()
        self.assertEqual(self.page.locator('#eod-match-count').inner_text(),'1')

    def test_reset_and_loosen_update_visible_controls_without_requests(self):
        self.open();self.load();before=self.read.call_count
        self.page.locator('#eod-min-rvol').fill('4');self.page.locator('#eod-loosen').click()
        for identity,value in (('eod-max-price','10'),('eod-min-change','2'),('eod-min-cap','25'),
                              ('eod-min-average','500000'),('eod-min-rvol','2')):
            self.assertEqual(self.page.locator('#'+identity).input_value(),value)
        self.page.locator('#eod-reset').click()
        self.assertEqual(self.page.locator('#eod-max-price').input_value(),'5')
        self.assertEqual(self.page.locator('#eod-min-rvol').input_value(),'')
        self.assertEqual(self.read.call_count,before)

    def test_optional_reference_requirement_does_not_upgrade_full_strategy(self):
        self.open();self.load(cap=False)
        self.page.locator('#eod-require-cap').uncheck()
        self.assertEqual(self.row().locator('.eod-outcome').inner_text(),'EOD CRITERIA MET')
        self.assertIn('FULL STRATEGY QUALIFICATION: NOT AVAILABLE',self.page.locator('#eod-watchlist').inner_text())

    def test_search_and_matches_view_have_honest_zero_state(self):
        self.open();self.load();before=self.read.call_count
        self.page.locator('#eod-search').fill('aaPl');self.assertEqual(self.page.locator('#eod-rows tr').count(),1)
        self.page.locator('#eod-search').fill('NONE');self.assertTrue(self.page.locator('#eod-empty').is_visible())
        self.page.locator('#eod-search').fill('');self.page.locator('#eod-matches-only').check()
        self.assertEqual(self.page.locator('#eod-rows tr').count(),1)
        self.page.locator('#eod-min-change').fill('4')
        self.assertTrue(self.page.locator('#eod-empty').is_visible())
        self.assertEqual(self.read.call_count,before)

    def test_sort_is_exact_deterministic_and_missing_last(self):
        self.open();self.load('MSFT');self.load('AAPL');before=self.read.call_count
        for mode in ('price','price_desc','change_pct','volume','relative_volume','market_cap'):
            self.page.locator('#eod-sort').select_option(mode)
            self.assertEqual(self.page.locator('#eod-rows tr').evaluate_all('(rows) => rows.map(r=>r.dataset.ticker)'),['AAPL','MSFT','NVDA'])
        self.assertEqual(self.read.call_count,before)

    def test_async_ticker_change_cannot_relabel_loaded_response(self):
        self.open();entered=threading.Event();release=threading.Event()
        def delayed(path,key):
            entered.set();release.wait(timeout=5);return AdapterTests.response(path,key)
        self.read.side_effect=delayed
        self.addCleanup(release.set)
        with self.page.expect_request('**view=history'):
            self.page.locator('#eod-load-history').click()
        self.assertTrue(entered.wait(timeout=3))
        self.page.locator('#eod-ticker').select_option('MSFT');release.set()
        self.row('AAPL').locator('td').nth(8).filter(has_text='REAL END-OF-DAY').wait_for()
        self.assertEqual(self.page.locator('#eod-ticker').input_value(),'MSFT')
        self.assertIn('UNLOADED',self.row('MSFT').inner_text())
        self.assertTrue(self.page.locator('#eod-load-reference').is_disabled())

    def test_old_preview_history_load_updates_real_panel_with_no_extra_api_call(self):
        self.open();self.page.locator('#market-load-history:not([disabled])').wait_for()
        self.page.locator('#market-load-history').click()
        self.page.locator('#eod-history-count').filter(has_text='1').wait_for()
        self.assertEqual(self.read.call_count,1)
        self.assertTrue(self.page.locator('#real-history-wrap').is_visible())

    def test_cooldown_and_failed_load_are_sanitized_and_keep_prior_evidence(self):
        self.open();self.load()
        self.grant.return_value=60
        # Fresh cache hits are allowed; expire the history entry only for this seam.
        self.server.market_data._cache.clear()
        self.page.locator('#eod-load-history').click()
        self.page.locator('#eod-message').filter(has_text='Retry after at least 60 seconds').wait_for()
        self.assertEqual(self.read.call_count,2)
        self.assertEqual(self.row().locator('.eod-outcome').inner_text(),'EOD CRITERIA MET')

    def test_recheck_updates_local_freshness_without_provider_request(self):
        self.open();self.load();before=self.read.call_count
        with patch.object(massive,'_now',return_value=NOW+timedelta(days=2)):
            self.page.locator('#eod-recheck').click()
            self.row().locator('td').nth(8).filter(has_text='STALE').wait_for()
        self.assertEqual(self.page.locator('#eod-history-count').inner_text(),'0')
        self.assertEqual(self.page.locator('#eod-reference-count').inner_text(),'0')
        self.assertEqual(self.read.call_count,before)

    def test_demo_chart_filters_and_safety_are_unchanged_by_real_loads(self):
        self.open();before=self.server.snapshot_json;self.load()
        self.assertEqual(self.server.snapshot_json,before)
        self.assertEqual(self.page.locator('#chart-symbol').inner_text(),'ABCD')
        self.assertEqual(self.page.locator('#filter-max-price').input_value(),'5.00')
        self.assertEqual(self.page.locator('#scanner-counts').inner_text(),'13 MATCHES / 24 SCANNED')
        self.assertIn('BLOCKED',self.page.locator('#bot-health').inner_text())

    def test_responsive_layout_keyboard_and_real_demo_labels(self):
        self.open();self.load()
        for width,height in ((1920,1080),(1440,900),(1024,768),(390,844)):
            self.page.set_viewport_size({'width':width,'height':height})
            self.assertTrue(self.page.evaluate('() => document.documentElement.scrollWidth <= innerWidth'))
        self.page.locator('#eod-max-price').focus()
        self.assertTrue(self.page.locator('#eod-max-price').evaluate('(e) => e===document.activeElement'))


if __name__=='__main__':unittest.main()
