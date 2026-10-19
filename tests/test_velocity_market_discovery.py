"""Anonymous grouped-data fixtures. No credentials or real provider requests."""

import ast
from contextlib import ExitStack
from dataclasses import FrozenInstanceError
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
from unittest.mock import Mock, patch

from tradingbot_dashboard.demo_data import build_demo_snapshot
from tradingbot_dashboard.server import create_server, _CSP
from tradingbot_marketdata import discovery as d, massive, transport
from tests.test_velocity_eod_research import history_body, reference_body, local_only

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (ROOT / 'tests/fixtures/massive_grouped_daily.json').read_bytes()
NOW = datetime(2026, 10, 19, 16, tzinfo=timezone.utc)
DAY = date(2026, 10, 16)
KEY = 'SYNTHETIC_TEST_CREDENTIAL'


def body(rows=None, **fields):
    raw = json.loads(FIXTURE)
    if rows is not None:
        raw['results'] = rows; raw['resultsCount'] = raw['queryCount'] = len(rows)
    raw.update(fields)
    return json.dumps(raw, separators=(',', ':')).encode()


def large_body(count):
    # Large provider-shaped response, generated deterministically without downloads.
    row = json.loads(FIXTURE)['results'][0]
    rows = []
    for index in range(count):
        n, letters = index, ''
        for _ in range(4): letters = chr(65 + n % 26) + letters; n //= 26
        rows.append({**row, 'T': 'D' + letters, 'v': index})
    return body(rows)


def decode(value=FIXTURE, day=DAY, now=NOW):
    return d._decode_grouped(value, day, now)


class Offline(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for target in ('socket.socket.connect', 'socket.getaddrinfo'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('External network forbidden')))


class SourceTests(Offline):
    def test_fixture_complete_counts_and_non_otc_supported_scope(self):
        result = decode().page(NOW)
        self.assertEqual((result['received_rows'], result['validated_rows'], result['excluded_otc'], result['excluded_unsupported']), (8, 6, 1, 1))
        self.assertEqual(result['eligible_candidates'], 5)
        self.assertEqual([r['ticker'] for r in result['rows']], ['ABCD', 'VELA', 'QBIT', 'BRK.B', 'MTRX'])
        self.assertEqual(result['provider_results_count'], 8)
        self.assertEqual(result['market_session_completeness'], 'NOT_ESTABLISHED')

    def test_original_timestamp_and_requested_date_are_independent(self):
        result = decode().page(NOW)
        self.assertEqual(result['requested_date'], '2026-10-16')
        self.assertEqual(result['rows'][0]['source_timestamp_ms'], 1792123200000)
        self.assertIn('window end', result['timestamp_semantics'])
        self.assertNotIn('window_start_at', result['rows'][0])

    def test_grouped_allows_own_non_midnight_timestamp(self):
        row = json.loads(FIXTURE)['results'][0]; row['t'] += 16 * 3600000
        result = decode(body([row])).page(NOW)
        self.assertEqual(result['rows'][0]['source_at'], '2026-10-16T20:00:00+00:00')

    def test_exact_next_midnight_is_retained_not_relabelled(self):
        row = json.loads(FIXTURE)['results'][0]; row['t'] += 24 * 3600000
        result = decode(body([row])).page(NOW)
        self.assertEqual(result['requested_date'], '2026-10-16')
        self.assertEqual(result['rows'][0]['source_date_eastern'], '2026-10-17')

    def test_one_millisecond_beyond_following_midnight_rejected(self):
        row = json.loads(FIXTURE)['results'][0]; row['t'] += 24 * 3600000 + 1
        with self.assertRaises(ValueError): decode(body([row]))

    def test_prior_date_timestamp_rejected(self):
        row = json.loads(FIXTURE)['results'][0]; row['t'] -= 1
        with self.assertRaises(ValueError): decode(body([row]))

    def test_dst_and_standard_time_dates(self):
        for day, utc_hour in ((date(2026,3,6),5), (date(2026,3,9),4), (date(2026,10,30),4), (date(2026,11,2),5)):
            row = json.loads(FIXTURE)['results'][0]
            row['t'] = int(datetime(day.year,day.month,day.day,utc_hour,tzinfo=timezone.utc).timestamp()*1000)
            result = decode(body([row]), day, datetime(2026,11,4,tzinfo=timezone.utc)).page(datetime(2026,11,4,tzinfo=timezone.utc))
            self.assertEqual(result['rows'][0]['source_date_eastern'], day.isoformat())

    def test_weekend_request_is_not_a_session_and_uses_no_inference(self):
        for day in (date(2026,10,17), date(2026,10,18)):
            with self.assertRaises(ValueError): decode(body([]), day)

    def test_empty_weekday_holiday_does_not_invent_market_session(self):
        result = decode(body([]), date(2026,7,3)).page(NOW)
        self.assertEqual(result['state'], 'UNAVAILABLE')
        self.assertEqual(result['received_rows'], 0)
        self.assertEqual(result['market_session_completeness'], 'NOT_ESTABLISHED')

    def test_future_and_current_eastern_date_rejected(self):
        for value in ('2026-10-19','2026-10-20','2024-10-18','2026-1-1','../bad',True,None):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                d._request_date(value, NOW)

    def test_eastern_boundary_not_utc_date(self):
        early = datetime(2026,10,20,1,tzinfo=timezone.utc)
        with self.assertRaises(ValueError): d._request_date('2026-10-19', early)

    def test_current_day_source_is_never_completed_eod(self):
        row = json.loads(FIXTURE)['results'][0]; row['t'] += 3*24*3600000
        with self.assertRaises(ValueError): decode(body([row]),date(2026,10,19))

    def test_old_evidence_remains_dated_and_explicitly_stale(self):
        result = decode().page(NOW + timedelta(days=5))
        self.assertEqual(result['state'], 'STALE'); self.assertEqual(result['requested_date'], DAY.isoformat())

    def test_empty_optional_results_field(self):
        self.assertEqual(decode(b'{"status":"OK","adjusted":true,"resultsCount":0}').received_count,0)

    def test_optional_vwap_and_trade_count(self):
        row = json.loads(FIXTURE)['results'][0]; row['n']=0
        self.assertEqual(decode(body([row])).rows[0].vwap, Decimal('4.2'))
        del row['vw']; self.assertIsNone(decode(body([row])).rows[0].vwap)

    def test_missing_or_malformed_bar_invalidates_whole_response_not_prefix(self):
        rows = json.loads(FIXTURE)['results']; del rows[-1]['v']
        with self.assertRaises(ValueError): decode(body(rows))

    def test_duplicate_even_identical_tickers_invalidates_whole_response(self):
        row = json.loads(FIXTURE)['results'][0]
        with self.assertRaises(ValueError): decode(body([row, row]))

    def test_unknown_symbols_excluded_without_case_conversion_or_truncation(self):
        row = json.loads(FIXTURE)['results'][0]
        for symbol in ('KIMpL','ABC/W','ABC-WS','ABC/../A','ABCDEFG','AAPL?x=1','<svg>'):
            result = decode(body([{**row,'T':symbol}])).page(NOW)
            self.assertEqual(result['excluded_unsupported'],1); self.assertEqual(result['rows'],[])

    def test_invalid_utf8_duplicate_keys_nonfinite_nested_trailing_and_truncated_json(self):
        complete = FIXTURE.rstrip()
        self.assertTrue(complete.endswith(b'}'))
        for value in (b'\xff', b'{"status":"OK","status":"OK"}', b'{"status":NaN}',
                      b'{"status":Infinity}', b'{"status":{"nested":{"bad":[]}}}', FIXTURE+b'x', complete[:-1]):
            with self.subTest(value=value[:30]), self.assertRaises((ValueError,UnicodeError)):
                decode(value)

    def test_unexpected_metadata_or_pagination_is_never_followed(self):
        for fields in ({'next_url':'https://example.invalid'}, {'ticker':'anonymous'}, {'request_id':{}}, {'request_id':'x'*129}):
            with self.subTest(fields=fields), self.assertRaises(ValueError): decode(body(**fields))

    def test_deep_or_excessive_number_and_string_tokens_are_bounded_before_decode(self):
        for value in (b'['*10+b']'*10, b'{"status":"'+b'x'*257+b'"}', b'{"resultsCount":'+b'1'*70+b'}'):
            with self.assertRaises(ValueError): decode(value)

    def test_exact_byte_capacity_and_first_overflow(self):
        padded = FIXTURE + b' ' * (d._MAX_BODY - len(FIXTURE))
        self.assertEqual(decode(padded).received_count,8)
        with self.assertRaises(d._GroupedLimitError): decode(padded+b' ')

    def test_large_realistic_response_exceeds_per_ticker_cap_and_is_fully_validated(self):
        fixture = large_body(12000)
        self.assertGreater(len(fixture),262144)
        result = decode(fixture).page(NOW)
        self.assertEqual(result['received_rows'],12000); self.assertEqual(result['validated_rows'],12000)
        self.assertEqual(len(result['rows']),50); self.assertEqual(result['pages'],240)

    def test_exact_row_capacity_and_first_overflow(self):
        self.assertEqual(decode(large_body(d._MAX_ROWS)).received_count,d._MAX_ROWS)
        with self.assertRaises(d._GroupedLimitError): decode(body([],resultsCount=d._MAX_ROWS+1))

    def test_container_count_bound_before_json_objects_are_allocated(self):
        value = b'{"results":[' + b','.join([b'{}']*(d._MAX_ROWS+1)) + b']}'
        with self.assertRaises(d._GroupedLimitError): decode(value)

    def test_primitive_result_arrays_rejected_before_json_allocation(self):
        value=b'{"results":['+b','.join([b'0']*(d._MAX_ROWS+1))+b']}'
        with patch.object(d.json,'loads',side_effect=AssertionError('Do not allocate primitive array')):
            with self.assertRaises(ValueError):decode(value)

    def test_model_immutable_slotted_and_payload_is_copied(self):
        source = decode(); self.assertFalse(hasattr(source,'__dict__'))
        with self.assertRaises(FrozenInstanceError): source.received_count=0
        value=source.page(NOW);value['rows'].clear()
        self.assertEqual(len(source.page(NOW)['rows']),5)

    def test_no_unsupported_metrics_or_strategy_results_in_rows(self):
        result=decode().page(NOW)
        for row in result['rows']:
            for key in ('change_pct','market_cap','relative_volume','average_volume_10','score','setup','outcome'):
                self.assertNotIn(key,row)
        self.assertIn('DATED_MARKET_CAP',result['missing_qualification'])
        self.assertIn('PRELIMINARY EOD CANDIDATES — NOT FULLY QUALIFIED',result['limitations'])


def invalid_root(fields):
    def test(self):
        with self.assertRaises(ValueError):decode(body(**fields))
    return test


for name, fields in {'false_adjusted':{'adjusted':False}, 'absent_adjusted':{'adjusted':None},
    'int_adjusted':{'adjusted':1}, 'delayed':{'status':'DELAYED'}, 'error':{'status':'ERROR'},
    'count_bool':{'resultsCount':True}, 'count_negative':{'resultsCount':-1}, 'count_mismatch':{'resultsCount':7},
    'query_bool':{'queryCount':True}, 'query_overflow':{'queryCount':30001}, 'nested_results':{'results':{}}}.items():
    setattr(SourceTests,'test_root_'+name,invalid_root(fields))


def invalid_row(field,value):
    def test(self):
        row=json.loads(FIXTURE)['results'][0];row[field]=value
        with self.assertRaises(ValueError):decode(body([row]))
    return test


for name, field, value in (('zero_close','c',0),('negative_volume','v',-1),('bool_volume','v',True),
    ('null_volume','v',None),('null_open','o',None),('string_price','c','2'),('inverted_high','h',1),
    ('inverted_low','l',5),('bool_timestamp','t',True),('fraction_timestamp','t',1792123200000.1),
    ('bool_otc','otc',1),('nested_price','c',{}),('negative_trade_count','n',-1),
    ('null_vwap','vw',None),('empty_ticker','T',''),('object_ticker','T',{})):
    setattr(SourceTests,'test_row_'+name,invalid_row(field,value))


class FilterTests(Offline):
    def test_strict_price_boundary(self):
        rows=decode().page(NOW)['rows']
        self.assertIn('VELA',[r['ticker'] for r in rows]);self.assertNotIn('NEXA',[r['ticker'] for r in rows])

    def test_zero_volume_threshold_is_explicitly_disabled(self):
        self.assertIn('MTRX',[r['ticker'] for r in decode().page(NOW)['rows']])

    def test_positive_volume_threshold_is_strict_and_not_ten_day_average(self):
        result=decode().page(NOW,filters=d._DiscoveryFilters(Decimal(5),Decimal(1000000)))
        self.assertEqual([r['ticker'] for r in result['rows']],['ABCD','VELA'])
        self.assertNotIn('average_volume_10',result['rows'][0])

    def test_search_and_filters_use_and_semantics_case_insensitively(self):
        result=decode().page(NOW,search='ve',filters=d._DiscoveryFilters(Decimal(5),Decimal(1000000)))
        self.assertEqual([r['ticker'] for r in result['rows']],['VELA'])
        self.assertEqual(result['eligible_candidates'],2);self.assertEqual(result['matched_candidates'],1)

    def test_all_sort_modes_are_deterministic_and_do_not_change_fixture(self):
        source=decode();before=source.rows
        for mode in ('volume','price','price_desc','range'):
            first=source.page(NOW,sort=mode);self.assertEqual(first,source.page(NOW,sort=mode))
        self.assertIs(source.rows,before)
        self.assertEqual(source.page(NOW,sort='price')['rows'][0]['ticker'],'MTRX')
        self.assertEqual(source.page(NOW,sort='price_desc')['rows'][0]['ticker'],'VELA')

    def test_range_formula_is_exact_and_not_close_to_close_change(self):
        row=decode().rows[0]
        self.assertEqual(row.range_pct,Fraction(275,2))
        self.assertEqual(row.payload()['daily_range_pct']['display'],'137.50')

    def test_hostile_decimal_context_does_not_round_evidence_or_qualification(self):
        with localcontext() as ctx:
            ctx.prec=2;ctx.traps[Inexact]=ctx.traps[Rounded]=True
            result=decode().page(NOW,sort='range')
            self.assertEqual(result['eligible_candidates'],5)
            self.assertEqual(decode().rows[0].close.as_tuple(),Decimal('4.99').as_tuple())

    def test_invalid_numeric_inputs_and_bounds(self):
        for value in ('NaN','Infinity','1e5','-1','+2','', '1.1234567890123','2'*33):
            with self.assertRaises(ValueError):d._filter_decimal(value)
        for price,volume in ((0,0),(1000001,0),(5,-1),(5,10**15+1)):
            with self.assertRaises(ValueError):d._DiscoveryFilters(Decimal(price),Decimal(volume))

    def test_invalid_page_search_sort(self):
        for fields in ({'page':True},{'page':-1},{'page':600},{'search':'A/B'},{'sort':'score'}):
            with self.assertRaises(ValueError):decode().page(NOW,**fields)

    def test_pagination_bounded_complete_without_duplicating_rows(self):
        source=decode(large_body(121));collected=[]
        for page in range(3):
            result=source.page(NOW,page=page);self.assertLessEqual(len(result['rows']),50)
            collected.extend(r['ticker'] for r in result['rows'])
        self.assertEqual(len(collected),121);self.assertEqual(len(set(collected)),121)

    def test_zero_matches_does_not_remove_source_coverage(self):
        result=decode().page(NOW,filters=d._DiscoveryFilters(Decimal('.1')))
        self.assertEqual(result['eligible_candidates'],0);self.assertEqual(result['received_rows'],8)
        self.assertEqual(result['rows'],[])


class AdapterTests(Offline):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.dict('os.environ',{'MASSIVE_API_KEY':KEY}))
        self.stack.enter_context(patch.object(massive,'_now',return_value=NOW))
        self.tick=self.stack.enter_context(patch.object(massive.time,'monotonic',return_value=1000.0))
        self.stack.enter_context(patch.object(massive,'_RATE',massive._RateGate()))
        self.read=self.stack.enter_context(patch.object(massive,'_read_grouped_response',return_value=(200,'application/json','',FIXTURE)))
        self.ticker_read=self.stack.enter_context(patch.object(massive,'_read_response',side_effect=self.per_ticker))
        self.preview=massive._MarketDataPreview(enabled=True,watchlist=('AAPL','MSFT'))

    @staticmethod
    def per_ticker(path,key):
        import re
        ticker=re.search(r'/ticker(?:s)?/([A-Z.]+)',path)[1]
        return 200,'application/json','',reference_body(ticker=ticker) if '/reference/' in path else history_body(ticker=ticker)

    def load(self):return self.preview.load_discovery('2026-10-16')

    def test_default_demo_no_network_no_key_access_even_for_discovery(self):
        with patch.object(massive.os.environ,'get',side_effect=AssertionError('Key forbidden')):
            preview=massive._MarketDataPreview();preview.discovery();preview.research();preview.configuration()
            self.assertEqual(preview.load_discovery('2026-10-16')['reason'],'PREVIEW_DISABLED')
        self.read.assert_not_called();self.ticker_read.assert_not_called()

    def test_enabled_without_key_is_unavailable_without_request(self):
        with patch.dict('os.environ',{'MASSIVE_API_KEY':''}):preview=massive._MarketDataPreview(enabled=True)
        self.assertEqual(preview.load_discovery('2026-10-16')['reason'],'KEY_NOT_CONFIGURED');self.read.assert_not_called()

    def test_one_explicit_action_one_fixed_request_no_chained_calls(self):
        result=self.load()
        self.read.assert_called_once_with('/v2/aggs/grouped/locale/us/market/stocks/2026-10-16?adjusted=true&include_otc=false',KEY)
        self.assertEqual(result['requested_date'],'2026-10-16');self.assertFalse(result['cached'])
        self.ticker_read.assert_not_called();self.assertNotIn(KEY,json.dumps(result))

    def test_local_filters_search_pagination_and_configuration_never_request(self):
        self.load()
        for sort in ('price','price_desc','range','volume'):
            self.preview.discovery(sort=sort,search='a',min_volume='1000000')
        self.preview.configuration();self.preview.research();self.read.assert_called_once()

    def test_five_minute_date_specific_cache_and_single_summary_capacity(self):
        first=self.load();again=self.load();self.assertTrue(again['cached']);self.read.assert_called_once()
        self.assertEqual(first['retrieved_at'],again['retrieved_at'])
        self.tick.return_value+=301
        self.load();self.assertEqual(self.read.call_count,2)
        self.tick.return_value+=15
        raw=json.loads(FIXTURE)
        for row in raw['results']:row['t']-=86400000
        self.read.return_value=(200,'application/json','',json.dumps(raw).encode())
        result=self.preview.load_discovery('2026-10-15')
        self.assertFalse(result['cached']);self.assertEqual(len(self.preview._discovery_cache),1)

    def test_quota_shared_with_existing_previous_history_and_reference_endpoints(self):
        self.load();self.assertEqual(self.preview.observe('AAPL','history')['reason'],'RATE_LIMITED')
        self.tick.return_value+=15;self.preview.observe('AAPL','history')
        self.tick.return_value+=15;self.preview.observe('AAPL','reference')
        self.tick.return_value+=15;self.preview.observe('MSFT','previous')
        self.tick.return_value=1059.9
        other=massive._MarketDataPreview(enabled=True)
        self.assertEqual(other.load_discovery('2026-10-16')['reason'],'RATE_LIMITED')
        self.assertEqual(self.read.call_count+self.ticker_read.call_count,4)

    def test_cache_hit_uses_no_quota(self):
        self.load()
        with patch.object(massive._RATE,'reserve',side_effect=AssertionError('No quota')):
            self.assertTrue(self.load()['cached'])

    def test_missing_end_of_day_current_weekend_invalid_date_never_calls_provider(self):
        for value in ('2026-10-17','2026-10-19','2026-10-20','not-a-date','2026-10-16?x=1'):
            self.assertEqual(self.preview.load_discovery(value)['reason'],'INVALID_REQUEST')
        self.read.assert_not_called()

    def test_response_limits_and_exceptions_sanitized_without_retry(self):
        for error,reason in ((d._GroupedLimitError(),'RESPONSE_LIMIT_EXCEEDED'),(TimeoutError(KEY),'TIMEOUT'),
                             (OSError(KEY),'NETWORK_UNAVAILABLE'),(RuntimeError(KEY),'INVALID_RESPONSE')):
            self.tick.return_value+=1000;self.read.side_effect=error
            before=self.read.call_count;result=self.load()
            self.assertEqual(result['reason'],reason);self.assertNotIn(KEY,json.dumps(result));self.assertEqual(self.read.call_count,before+1)

    def test_status_errors_and_retry_after_share_backoff_with_per_ticker_calls(self):
        for code,reason in ((301,'REDIRECT_REJECTED'),(401,'AUTHENTICATION_FAILED'),(403,'ACCESS_DENIED'),
                            (404,'NOT_FOUND'),(429,'RATE_LIMITED'),(503,'PROVIDER_UNAVAILABLE')):
            self.tick.return_value+=1000;self.read.return_value=(code,'application/json','120',KEY.encode())
            result=self.load();self.assertEqual(result['reason'],reason);self.assertNotIn(KEY,json.dumps(result))
            self.assertEqual(self.preview.observe('AAPL','previous')['reason'],'RATE_LIMITED')

    def test_invalid_response_cannot_publish_partial_or_previous_market_as_new_date(self):
        self.load();self.tick.return_value+=400
        complete = FIXTURE.rstrip()
        self.assertTrue(complete.endswith(b'}'))
        self.read.return_value=(200,'application/json','',complete[:-1])
        self.assertEqual(self.load()['reason'],'INVALID_RESPONSE');self.assertEqual(self.preview.discovery()['rows'],[])

    def test_shortlist_requires_valid_discovered_tickers_distinct_and_five_max(self):
        self.assertEqual(self.preview.select_research(('VELA',))['reason'],'DISCOVERY_REQUIRED')
        self.load()
        for values in (('UNSEEN',),('vela',),('VELA','VELA'),('KIMpL',),('OTCX',),('VELA/../A',),list(('VELA',)),tuple('ABCDEF')):
            self.assertIn(self.preview.select_research(values)['reason'],('INVALID_SHORTLIST','DISCOVERY_REQUIRED'))
        self.assertEqual(self.preview.configuration()['session_shortlist'],[])

    def test_select_five_from_discovery_preserves_configured_watchlist_and_makes_no_requests(self):
        self.load();selected=('VELA','QBIT','ABCD','MTRX','BRK.B')
        result=self.preview.select_research(selected)
        self.assertEqual(result['state'],'RESEARCH_SELECTION_UPDATED')
        self.assertEqual(result['watchlist'],['AAPL','MSFT']);self.assertEqual(result['research_watchlist'],list(selected))
        self.assertEqual(len(result['shortlist_sources']),5);self.ticker_read.assert_not_called();self.read.assert_called_once()
        research=self.preview.research();self.assertEqual(research['watchlist_total'],5)
        self.assertTrue(all(r['history_state']=='UNLOADED' for r in research['rows']))

    def test_shortlisted_symbol_can_use_exact_existing_manual_history_then_reference_workflow(self):
        self.load();self.preview.select_research(('VELA',));self.tick.return_value+=15
        self.assertEqual(self.preview.observe('VELA','history')['state'],'REAL END-OF-DAY')
        self.assertEqual(self.ticker_read.call_count,1)
        self.tick.return_value+=15;self.preview.observe('VELA','reference')
        self.assertEqual(self.ticker_read.call_count,2)
        self.assertEqual(self.preview.research()['rows'][0]['default_outcome'],'EOD CRITERIA MET')

    def test_discovery_evidence_is_never_used_as_pre2_history(self):
        self.load();self.preview.select_research(('VELA',))
        row=self.preview.research()['rows'][0]
        self.assertTrue(all(v is None for v in row['metrics'].values()))
        self.assertEqual(row['default_outcome'],'INSUFFICIENT DATA')

    def test_removal_and_clear_evict_nonconfigured_research_data_and_restore_original_view(self):
        self.load();self.preview.select_research(('VELA',));self.tick.return_value+=15
        self.preview.observe('VELA','history');self.preview.select_research(())
        self.assertEqual(self.preview.configuration()['research_watchlist'],['AAPL','MSFT'])
        self.assertNotIn(('VELA','history'),self.preview._loaded)
        self.assertEqual(self.preview.observe('VELA','history')['reason'],'INVALID_REQUEST')

    def test_shortlist_replacement_memory_is_bounded_and_each_source_date_retained(self):
        self.load();self.preview.select_research(('VELA',));self.tick.return_value+=400
        row=json.loads(FIXTURE)['results'][0];row.update(T='OTHER',t=row['t']-86400000)
        self.read.return_value=(200,'application/json','',body([row]))
        self.preview.load_discovery('2026-10-15')
        result=self.preview.select_research(('VELA','OTHER'))
        self.assertEqual(result['shortlist_sources'],[{'ticker':'VELA','discovery_date':'2026-10-16'},{'ticker':'OTHER','discovery_date':'2026-10-15'}])
        self.assertLessEqual(len(self.preview._loaded),20);self.assertLessEqual(len(self.preview._cache),30)

    def test_active_request_prevents_shortlist_race_and_second_provider_call(self):
        entered,release=threading.Event(),threading.Event()
        def pending(*args):entered.set();release.wait(3);return 200,'application/json','',FIXTURE
        self.read.side_effect=pending
        thread=threading.Thread(target=self.load);thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.preview.select_research(())['reason'],'REQUEST_IN_PROGRESS')
            self.assertEqual(self.load()['reason'],'REQUEST_IN_PROGRESS');self.read.assert_called_once()
        finally:release.set();thread.join(3)

    def test_safety_and_demo_snapshot_unchanged(self):
        original=build_demo_snapshot().to_payload();self.load();self.preview.select_research(('VELA',));self.preview.research()
        self.assertEqual(build_demo_snapshot().to_payload(),original)


class TransportTests(Offline):
    def test_separate_fixed_host_path_bearer_timeout_body_cap_and_close(self):
        connection=Mock();response=connection.getresponse.return_value
        response.status=200;response.getheader.side_effect=lambda key,default=None:{'Content-Type':'application/json','Content-Length':str(len(FIXTURE))}.get(key,default)
        response.read.return_value=FIXTURE
        path='/v2/aggs/grouped/locale/us/market/stocks/2026-10-16?adjusted=true&include_otc=false'
        with patch.object(transport,'HTTPSConnection',return_value=connection) as factory:
            result=transport._read_grouped_response(path,KEY)
        self.assertEqual(factory.call_args.args,('api.massive.com',));self.assertEqual(factory.call_args.kwargs['timeout'],12)
        self.assertTrue(factory.call_args.kwargs['context'].check_hostname)
        self.assertEqual(connection.request.call_args.args,('GET',path))
        self.assertEqual(connection.request.call_args.kwargs['headers']['Authorization'],'Bearer '+KEY)
        response.read.assert_called_once_with(8*1024*1024+1);connection.close.assert_called_once()
        self.assertEqual(result[3],FIXTURE);self.assertEqual(transport._MAX_BODY,262144)

    def test_invalid_external_or_expanded_path_never_creates_connection(self):
        with patch.object(transport,'HTTPSConnection') as connection:
            for path in ('https://example.invalid','/v2/aggs/grouped/locale/us/market/stocks/2026-10-16?adjusted=true',
                         '/v2/aggs/grouped/locale/us/market/stocks/2026-99-99?adjusted=true&include_otc=false'):
                with self.assertRaises(ValueError):transport._read_grouped_response(path,KEY)
            connection.assert_not_called()

    def test_size_encoding_and_incomplete_http_metadata_rejected_and_closed(self):
        for headers,received in (({'Content-Length':str(d._MAX_BODY+1)},b''),({'Content-Length':'10'},b'{}'),
                                 ({'Content-Encoding':'gzip'},b''),({'Content-Length':'-1'},b''),({},b'x'*(d._MAX_BODY+1))):
            connection=Mock();response=connection.getresponse.return_value
            response.getheader.side_effect=lambda key,default=None:headers.get(key,default)
            response.read.return_value=received
            with patch.object(transport,'HTTPSConnection',return_value=connection),self.assertRaises(ValueError):
                transport._read_grouped_response('/v2/aggs/grouped/locale/us/market/stocks/2026-10-16?adjusted=true&include_otc=false',KEY)
            connection.close.assert_called_once()


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict('os.environ',{'MASSIVE_API_KEY':KEY}))
        self.stack.enter_context(patch.object(massive,'_now',return_value=NOW))
        self.stack.enter_context(patch.object(massive,'_RATE',massive._RateGate()))
        self.read=self.stack.enter_context(patch.object(massive,'_read_grouped_response',return_value=(200,'application/json','',FIXTURE)))
        self.old=self.stack.enter_context(patch.object(massive,'_read_response',side_effect=AssertionError('No automatic ticker loads')))
        self.server=create_server(port=0,market_data_preview=True)
        thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        def stop():self.server.shutdown();self.server.server_close();thread.join(3)
        self.addCleanup(stop);local_only(self.stack,self.server.server_port)

    def request(self,path,method='GET',headers=None):
        connection=HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            connection.request(method,path,headers=headers or {});response=connection.getresponse()
            return response.status,dict(response.getheaders()),response.read()
        finally:connection.close()

    def test_metadata_get_head_and_asset_are_offline_and_security_headers_preserved(self):
        for path in ('/api/market-data/discovery','/market-discovery.js'):
            code,headers,data=self.request(path);self.assertEqual(code,200)
            self.assertEqual(headers['Content-Security-Policy'],_CSP);self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
            self.assertEqual(self.request(path,'HEAD')[2],b'');self.assertNotIn(KEY.encode(),data)
        self.read.assert_not_called()

    def test_rejection_publishes_only_fixed_diagnostic_and_unchanged_backoff(self):
        self.read.return_value=(200,'application/json','',body(adjusted=False,request_id=KEY))
        code,headers,value=self.request('/api/market-data/discovery/load?date=2026-10-16',
                                      headers={'X-Velocity-Market-Preview':'1'})
        self.assertEqual(code,200)
        self.assertEqual(headers['Content-Security-Policy'],_CSP)
        result=json.loads(value)
        self.assertEqual(result['reason'],'INVALID_RESPONSE')
        self.assertEqual(result['diagnostic_code'],'ROOT_ADJUSTMENT_INVALID')
        self.assertEqual(result['retry_after_seconds'],60)
        self.assertNotIn(KEY.encode(),value)
        for field in ('request_id','rows','received_rows','provider_results_count'):
            self.assertNotIn(field,result)
        self.assertEqual(json.loads(self.request('/api/market-data/discovery')[2])['rows'],[])
        self.read.assert_called_once();self.old.assert_not_called()

    def test_unexpected_root_names_publish_only_categories_and_bounded_counts(self):
        self.read.return_value=(200,'application/json','',body(ticker=KEY,**{KEY:KEY}))
        code,_,value=self.request('/api/market-data/discovery/load?date=2026-10-16',
                                 headers={'X-Velocity-Market-Preview':'1'})
        self.assertEqual(code,200)
        result=json.loads(value)
        self.assertEqual(result['diagnostic_code'],'ROOT_FIELDS_UNEXPECTED')
        self.assertEqual(result['unexpected_root_fields'],[{'category':'TICKER','count':1},{'category':'OTHER','count':1}])
        self.assertEqual(result['retry_after_seconds'],60)
        self.assertLess(len(value),1024);self.assertNotIn(KEY.encode(),value)
        for field in ('request_id','rows','received_rows','provider_results_count'):
            self.assertNotIn(field,result)
        self.read.assert_called_once();self.old.assert_not_called()

    def test_load_requires_explicit_header_same_origin_and_get(self):
        path='/api/market-data/discovery/load?date=2026-10-16'
        self.assertEqual(self.request(path)[0],403)
        self.assertEqual(self.request(path,headers={'X-Velocity-Market-Preview':'1','Origin':'https://example.invalid'})[0],403)
        self.assertEqual(self.request(path,'HEAD')[0],405)
        self.read.assert_not_called()
        self.assertEqual(self.request(path,headers={'X-Velocity-Market-Preview':'1'})[0],200);self.read.assert_called_once()

    def test_all_write_like_methods_rejected_on_all_new_routes(self):
        for path in ('/api/market-data/discovery','/api/market-data/discovery/load?date=2026-10-16','/api/market-data/shortlist?tickers=VELA'):
            for method in ('POST','PUT','PATCH','DELETE','OPTIONS','TRACE','CONNECT'):
                self.assertEqual(self.request(path,method)[0],405)
        self.read.assert_not_called()

    def test_shortlist_manual_local_only_and_no_provider_followup(self):
        headers={'X-Velocity-Market-Preview':'1'}
        self.request('/api/market-data/discovery/load?date=2026-10-16',headers=headers)
        path='/api/market-data/shortlist?tickers=VELA,QBIT'
        self.assertEqual(self.request(path)[0],403);self.assertEqual(self.request(path,'HEAD')[0],405)
        data=json.loads(self.request(path,headers=headers)[2])
        self.assertEqual(data['session_shortlist'],['VELA','QBIT'])
        self.read.assert_called_once();self.old.assert_not_called()

    def test_large_market_output_is_one_bounded_page_not_whole_universe(self):
        self.read.return_value=(200,'application/json','',large_body(12000))
        code,_,value=self.request('/api/market-data/discovery/load?date=2026-10-16',headers={'X-Velocity-Market-Preview':'1'})
        self.assertEqual(code,200);self.assertLess(len(value),65536)
        self.assertEqual(len(json.loads(value)['rows']),50)
        for page in (1,2,3):
            result=self.request(f'/api/market-data/discovery?page={page}')[2]
            self.assertLess(len(result),65536);self.assertEqual(len(json.loads(result)['rows']),50)
        self.read.assert_called_once()

    def test_invalid_query_host_duplicates_and_unknown_paths_never_request(self):
        for path in ('/api/market-data/discovery?url=bad','/api/market-data/discovery?price=1&price=2',
                     '/api/market-data/discovery?page=-1','/api/market-data/discovery/load?date=2026-10-16&date=2026-10-15',
                     '/api/market-data/shortlist?tickers=VELA&extra=x'):
            self.assertEqual(self.request(path,headers={'X-Velocity-Market-Preview':'1'})[0],400)
        self.assertEqual(self.request('/api/market-data/discovery',headers={'Host':'example.invalid'})[0],403)
        self.assertEqual(self.request('/market-discovery.js/../server.py')[0],404);self.read.assert_not_called()


class StaticTests(unittest.TestCase):
    def test_pure_discovery_has_no_io_broker_clock_or_strategy_dependency(self):
        tree=ast.parse((ROOT/'tradingbot_marketdata/discovery.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                names=[n.name for n in node.names] if isinstance(node,ast.Import) else [node.module or '']
                self.assertFalse(any(n.startswith(('socket','http','os','time','pathlib','sqlite3','tradingbot_broker','strategy')) for n in names))
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):self.assertNotIn(node.func.attr,('now','today','connect','write','request'))

    def test_ui_has_no_external_resources_persistence_polling_or_order_controls(self):
        text=(ROOT/'tradingbot_dashboard/static/market-discovery.js').read_text(encoding='utf-8')
        for forbidden in ('https://','api.massive.com','MASSIVE_API_KEY','Bearer','localStorage','sessionStorage',
                          'setInterval','setTimeout','innerHTML','placeOrder','cancelOrder','WebSocket','document.cookie'):
            self.assertNotIn(forbidden,text)
        self.assertIn('.slice(0, 50)',text)

    def test_visible_limitations_and_original_utf8_demo_label_preserved(self):
        text=(ROOT/'tradingbot_dashboard/static/index.html').read_text(encoding='utf-8')
        for marker in ('REAL EOD MARKET DISCOVERY','PRELIMINARY EOD CANDIDATES — NOT FULLY QUALIFIED',
                       'DEMO MARKET DATA — NO LIVE FEED','FULL STRATEGY QUALIFICATION: NOT AVAILABLE',
                       'TRADING BLOCKED','READ-ONLY REQUIRED','Session research shortlist'):
            self.assertIn(marker,text)


class BrowserTests(unittest.TestCase):
    """Optional installed Chromium; all provider paths use invented fixtures."""
    @classmethod
    def setUpClass(cls):
        executable=shutil.which('chromium') or shutil.which('chromium-browser')
        if not executable: raise unittest.SkipTest('Optional installed Chromium unavailable')
        try: from playwright.sync_api import sync_playwright
        except ImportError: raise unittest.SkipTest('Optional installed Playwright unavailable')
        cls.driver=sync_playwright().start();cls.addClassCleanup(cls.driver.stop)
        cls.browser=cls.driver.chromium.launch(executable_path=executable,headless=True,
            args=['--no-sandbox','--disable-background-networking'])
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict('os.environ',{'MASSIVE_API_KEY':KEY}))
        self.stack.enter_context(patch.object(massive,'_now',return_value=NOW))
        self.stack.enter_context(patch.object(massive,'_RATE',massive._RateGate()))
        self.stack.enter_context(patch.object(massive._RATE,'reserve',return_value=0))
        self.read=self.stack.enter_context(patch.object(massive,'_read_grouped_response',return_value=(200,'application/json','',FIXTURE)))
        self.ticker_read=self.stack.enter_context(patch.object(massive,'_read_response',side_effect=AdapterTests.per_ticker))
        self.errors=[];self.external=[]

    def open(self,enabled=True,width=1440):
        self.server=create_server(port=0,market_data_preview=enabled)
        thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        def stop():self.server.shutdown();self.server.server_close();thread.join(3)
        self.addCleanup(stop);local_only(self.stack,self.server.server_port)
        origin=f'http://127.0.0.1:{self.server.server_port}'
        context=self.browser.new_context(viewport={'width':width,'height':1000},reduced_motion='reduce',service_workers='block')
        self.addCleanup(context.close)
        def route(value):
            if not value.request.url.startswith(origin+'/'):
                self.external.append(value.request.url);value.abort()
            else:
                self.assertNotIn(KEY,value.request.url);self.assertNotIn('authorization',value.request.headers)
                value.continue_()
        context.route('**/*',route)
        self.page=context.new_page();self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.goto(origin+'/')
        self.page.wait_for_function("() => document.getElementById('discovery-date').value === '2026-10-16'")
        self.page.locator('#eod-total').filter(has_text='3').wait_for()

    def tearDown(self):
        self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])
        if hasattr(self,'page'):self.assertNotIn(KEY,self.page.content())

    def load(self):
        self.page.locator('#discovery-load').click()
        self.page.locator('#discovery-received').filter(has_text='8 / 8').wait_for()
        self.page.locator('#discovery-load:not([disabled])').wait_for()

    def select(self,ticker):
        self.page.locator(f'#discovery-rows button[data-ticker="{ticker}"]').click()
        self.page.wait_for_function('(ticker) => [...document.querySelectorAll("#eod-ticker option")].some(o => o.value === ticker)',arg=ticker)
        self.page.locator('#discovery-configured:not([disabled])').wait_for()

    def symbols(self):return self.page.locator('#discovery-rows tr').evaluate_all('(rows) => rows.map(r => r.dataset.ticker)')

    def test_default_demo_zero_provider_requests_and_original_scanner_chart_work(self):
        self.open(enabled=False)
        self.assertTrue(self.page.locator('#discovery-load').is_disabled())
        self.assertEqual(self.page.locator('#scanner-counts').inner_text(),'13 MATCHES / 24 SCANNED')
        self.page.locator('[data-timeframe="15m"]').click()
        self.read.assert_not_called();self.ticker_read.assert_not_called()

    def test_enabled_startup_waits_for_one_manual_load_and_displays_coverage(self):
        self.open();self.read.assert_not_called();self.load();self.read.assert_called_once();self.ticker_read.assert_not_called()
        self.assertEqual(self.page.locator('#discovery-validated').inner_text(),'6')
        self.assertEqual(self.page.locator('#discovery-excluded').inner_text(),'1 / 1')
        self.assertEqual(self.page.locator('#discovery-candidates').inner_text(),'5')
        self.assertIn('2026-10-16',self.page.locator('#discovery-source').inner_text())
        self.assertEqual(self.symbols(),['ABCD','VELA','QBIT','BRK.B','MTRX'])

    def test_filter_strict_boundaries_search_and_sort_have_no_provider_calls(self):
        self.open();self.load()
        self.page.locator('#discovery-volume').fill('1000000')
        self.page.wait_for_function('() => document.querySelectorAll("#discovery-rows tr").length === 2')
        self.assertEqual(self.symbols(),['ABCD','VELA'])
        self.page.locator('#discovery-search').fill('ve')
        self.page.wait_for_function('() => document.querySelectorAll("#discovery-rows tr").length === 1')
        self.assertEqual(self.symbols(),['VELA'])
        self.page.locator('#discovery-reset').click()
        self.page.wait_for_function('() => document.querySelectorAll("#discovery-rows tr").length === 5')
        self.page.locator('#discovery-sort').select_option('price')
        self.page.wait_for_function('() => document.querySelector("#discovery-rows tr").dataset.ticker === "MTRX"')
        self.read.assert_called_once();self.ticker_read.assert_not_called()

    def test_fixed_diagnostic_and_manual_backoff_visible_without_partial_results(self):
        self.read.return_value=(200,'application/json','',body(adjusted=False,request_id=KEY))
        self.open();self.page.locator('#discovery-load').click()
        self.page.locator('#discovery-message').filter(has_text='Diagnostic: ROOT_ADJUSTMENT_INVALID.').wait_for()
        message=self.page.locator('#discovery-message').inner_text()
        self.assertIn('Retry after at least 60 seconds; manual retry only.',message)
        self.assertNotIn(KEY,message)
        self.assertEqual(self.page.locator('#discovery-rows tr').count(),0)
        self.assertEqual(self.page.locator('#discovery-received').inner_text(),'—')
        self.read.assert_called_once();self.ticker_read.assert_not_called()

    def test_valid_optional_count_loads_original_candidates_and_coverage(self):
        self.read.return_value=(200,'application/json','',body(count=8))
        self.open();self.load()
        self.assertEqual(self.page.locator('#discovery-received').inner_text(),'8 / 8')
        self.assertEqual(self.page.locator('#discovery-validated').inner_text(),'6')
        self.assertEqual(self.symbols(),['ABCD','VELA','QBIT','BRK.B','MTRX'])
        self.assertNotIn('Diagnostic:',self.page.locator('#discovery-message').inner_text())
        self.assertNotIn(KEY,self.page.locator('#discovery-message').inner_text())
        self.read.assert_called_once();self.ticker_read.assert_not_called()

    def test_mismatched_optional_count_shows_safe_count_error_without_partial_data(self):
        self.read.return_value=(200,'application/json','',body(count=7,request_id=KEY))
        self.open();self.page.locator('#discovery-load').click()
        self.page.locator('#discovery-message').filter(has_text='Diagnostic: RESULT_COUNTS_INVALID.').wait_for()
        message=self.page.locator('#discovery-message').inner_text()
        self.assertIn('Retry after at least 60 seconds; manual retry only.',message)
        self.assertNotIn('Unexpected root categories:',message);self.assertNotIn(KEY,message)
        self.assertEqual(self.page.locator('#discovery-rows tr').count(),0)
        self.assertEqual(self.page.locator('#discovery-received').inner_text(),'—')
        self.read.assert_called_once();self.ticker_read.assert_not_called()

    def test_unknown_diagnostic_is_not_displayed_or_interpreted_as_html(self):
        self.open()
        self.page.route('**/api/market-data/discovery/load?*',lambda route:route.fulfill(
            json={'reason':'INVALID_RESPONSE','diagnostic_code':KEY+'<svg onload=alert(1)>',
                  'retry_after_seconds':60}))
        self.page.locator('#discovery-load').click()
        self.page.locator('#discovery-message').filter(has_text='Retry after at least 60 seconds').wait_for()
        message=self.page.locator('#discovery-message').inner_text()
        self.assertNotIn(KEY,message);self.assertNotIn('Diagnostic:',message)
        self.assertEqual(self.page.locator('#discovery-message svg').count(),0)
        self.read.assert_not_called();self.ticker_read.assert_not_called()

    def test_all_root_categories_visible_but_no_names_values_or_partial_data(self):
        raw=dict.fromkeys(('ticker','next_url','requestId','pagination','message','error'),KEY)
        raw.update({KEY+str(index):KEY for index in range(6)})
        self.read.return_value=(200,'application/json','',json.dumps(raw).encode())
        self.open();self.page.locator('#discovery-load').click()
        self.page.locator('#discovery-message').filter(has_text='Unexpected root categories:').wait_for()
        message=self.page.locator('#discovery-message').inner_text()
        for category in ('TICKER','NEXT_URL','REQUEST_ID_CAMEL','PAGINATION','MESSAGE','ERROR'):
            self.assertIn(f'{category} (1)',message)
        self.assertIn('OTHER (6)',message);self.assertNotIn(KEY,message)
        self.assertIn('Retry after at least 60 seconds; manual retry only.',message)
        self.assertEqual(self.page.locator('#discovery-rows tr').count(),0)
        self.read.assert_called_once();self.ticker_read.assert_not_called()

    def test_malicious_or_unbounded_root_details_never_reach_display(self):
        self.open()
        payload={'reason':'INVALID_RESPONSE','diagnostic_code':'ROOT_FIELDS_UNEXPECTED','retry_after_seconds':60}
        self.page.route('**/api/market-data/discovery/load?*',lambda route:route.fulfill(json=payload))
        cases=([{'category':KEY+'<svg>','count':1}], [{'category':'COUNT','count':1}], [{'category':'OTHER','count':True}],
               [{'category':'OTHER','count':0}], [{'category':'OTHER','count':13}],
               [{'category':'OTHER','count':1.5}], [{'category':'TICKER','count':2}],
               [{'category':'OTHER','count':1,'name':KEY}], [{'category':'OTHER','count':1}]*8,
               [{'category':'TICKER','count':1},{'category':'TICKER','count':1}],
               [{'category':'TICKER','count':1},{'category':'OTHER','count':12}],None,KEY)
        for fields in cases:
            payload['unexpected_root_fields']=fields
            self.page.locator('#discovery-load').click()
            self.page.locator('#discovery-message').filter(has_text='Retry after at least 60 seconds').wait_for()
            message=self.page.locator('#discovery-message').inner_text()
            self.assertIn('Diagnostic: ROOT_FIELDS_UNEXPECTED.',message)
            self.assertNotIn('Unexpected root categories:',message);self.assertNotIn(KEY,message)
            self.assertEqual(self.page.locator('#discovery-message svg').count(),0)
        self.read.assert_not_called();self.ticker_read.assert_not_called()

    def test_diagnostic_only_displayed_for_invalid_response(self):
        self.open()
        self.page.route('**/api/market-data/discovery/load?*',lambda route:route.fulfill(
            json={'reason':'ACCESS_DENIED','diagnostic_code':'ROW_TIMESTAMP_INVALID',
                  'retry_after_seconds':300}))
        self.page.locator('#discovery-load').click()
        self.page.locator('#discovery-message').filter(has_text='Retry after at least 300 seconds').wait_for()
        self.assertNotIn('ROW_TIMESTAMP_INVALID',self.page.locator('#discovery-message').inner_text())
        self.read.assert_not_called();self.ticker_read.assert_not_called()

    def test_selected_ticker_reaches_pre2_only_manual_history_then_reference_qualifies(self):
        self.open();self.load();self.select('VELA');self.ticker_read.assert_not_called()
        self.page.locator('#eod-total').filter(has_text='1').wait_for()
        self.assertIn('INSUFFICIENT DATA',self.page.locator('#eod-rows').inner_text())
        self.page.locator('#eod-load-history').click()
        self.page.locator('#eod-history-count').filter(has_text='1').wait_for()
        self.assertEqual(self.ticker_read.call_count,1)
        self.page.locator('#eod-load-reference:not([disabled])').wait_for();self.page.locator('#eod-load-reference').click()
        self.page.locator('#eod-match-count').filter(has_text='1').wait_for()
        self.assertEqual(self.ticker_read.call_count,2)
        self.assertEqual(self.page.locator('#market-ticker option').all_text_contents(),['AAPL','MSFT','NVDA'])
        self.assertIn('FULL STRATEGY QUALIFICATION: NOT AVAILABLE',self.page.locator('#eod-watchlist').inner_text())

    def test_five_selection_limit_visible_and_sixth_disabled(self):
        self.open();self.load();self.page.locator('#discovery-price').fill('10')
        self.page.wait_for_function('() => document.querySelectorAll("#discovery-rows tr").length === 6')
        for ticker in ('VELA','ABCD','QBIT','MTRX','BRK.B'):self.select(ticker)
        self.assertEqual(self.page.locator('#discovery-shortlist-count').inner_text(),'5 / 5')
        self.assertTrue(self.page.locator('#discovery-rows button[data-ticker="NEXA"]').is_disabled())
        self.ticker_read.assert_not_called();self.read.assert_called_once()

    def test_removal_and_restore_configured_watchlist_are_explicit_and_offline(self):
        self.open();self.load();self.select('VELA')
        self.page.locator('#discovery-configured').click()
        self.page.locator('#eod-total').filter(has_text='3').wait_for()
        self.assertEqual(self.page.locator('#eod-ticker option').all_text_contents(),['AAPL','MSFT','NVDA'])
        self.assertEqual(self.page.locator('#discovery-shortlist-count').inner_text(),'0 / 5')
        self.ticker_read.assert_not_called()

    def test_large_response_pagination_never_renders_more_than_fifty_rows(self):
        self.read.return_value=(200,'application/json','',large_body(121));self.open()
        self.page.locator('#discovery-load').click()
        self.page.locator('#discovery-received').filter(has_text='121 / 121').wait_for()
        self.assertEqual(self.page.locator('#discovery-rows tr').count(),50)
        self.page.locator('#discovery-next').click();self.page.locator('#discovery-page').filter(has_text='Page 2 / 3').wait_for()
        self.assertEqual(self.page.locator('#discovery-rows tr').count(),50)
        self.page.locator('#discovery-next').click();self.page.locator('#discovery-page').filter(has_text='Page 3 / 3').wait_for()
        self.assertEqual(self.page.locator('#discovery-rows tr').count(),21);self.read.assert_called_once()

    def test_invalid_inputs_and_zero_results_show_honest_empty_state(self):
        self.open();self.load();self.page.locator('#discovery-price').fill('0.1')
        self.page.wait_for_function('() => document.querySelectorAll("#discovery-rows tr").length === 0')
        self.assertTrue(self.page.locator('#discovery-empty').is_visible())
        self.page.locator('#discovery-price').fill('-1')
        self.page.locator('#discovery-message').filter(has_text='Invalid filter').wait_for()
        self.assertEqual(self.page.locator('#discovery-price').get_attribute('aria-invalid'),'true')
        self.read.assert_called_once()

    def test_cooldown_cached_load_and_source_dates_are_visible(self):
        self.open();self.load();self.load();self.read.assert_called_once()
        self.assertIn('cached response',self.page.locator('#discovery-message').inner_text())
        self.page.locator('#discovery-date').fill('2026-10-15')
        with patch.object(massive._RATE,'reserve',return_value=15):
            self.page.locator('#discovery-load').click()
            self.page.locator('#discovery-message').filter(has_text='Retry after at least 15 seconds').wait_for()
        self.read.assert_called_once()

    def test_async_filter_response_does_not_replace_newer_search(self):
        self.open();self.load();held=[]
        def hold(route):
            if 'search=VELA' in route.request.url:held.append(route)
            else:route.continue_()
        self.page.route('**/api/market-data/discovery?*',hold)
        with self.page.expect_request(lambda request:'search=VELA' in request.url):
            self.page.locator('#discovery-search').fill('VELA')
        self.page.locator('#discovery-search').fill('QBIT')
        self.page.wait_for_function('() => document.querySelectorAll("#discovery-rows tr").length === 1 && document.querySelector("#discovery-rows tr").dataset.ticker === "QBIT"')
        self.assertEqual(len(held),1)
        held[0].fulfill(status=200,content_type='application/json',body=json.dumps(self.server.market_data.discovery(search='VELA')))
        self.page.wait_for_load_state('networkidle');self.assertEqual(self.symbols(),['QBIT']);self.read.assert_called_once()

    def test_responsive_panel_has_no_document_overflow_and_no_trading_controls(self):
        self.open(width=600);self.load()
        self.assertTrue(self.page.evaluate('() => document.documentElement.scrollWidth <= window.innerWidth'))
        self.assertIn('TRADING BLOCKED',self.page.locator('body').inner_text())
        self.assertFalse(self.page.locator('button').filter(has_text='Enable Trading').count())
