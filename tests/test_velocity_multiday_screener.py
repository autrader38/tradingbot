"""PRE4C synthetic offline research/UI fixtures. No credentials or provider I/O."""

import ast
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Inexact, Rounded, localcontext
from fractions import Fraction
import gc
import json
from pathlib import Path
import tracemalloc
import unittest
from unittest.mock import patch

from tradingbot_dashboard import server as web
from tradingbot_dashboard.demo_data import build_demo_snapshot
from tradingbot_marketdata import collection as c, massive, multiday as m, screener as s
from tests import test_velocity_grouped_collection as previous
from tests import test_velocity_market_discovery as discovery_tests
from tests.test_velocity_multiday import DAYS, TARGET, bar, summary, symbol

ROOT = Path(__file__).resolve().parents[1]
KEY, NOW = previous.KEY, previous.NOW
ACTION = previous.ACTION


def sources(count=None):
    result = []
    for index, day in enumerate(DAYS):
        if count is not None:
            rows = [bar(day, symbol(i), '4.12' if index==10 else '4.00', '4000000' if index==10 else '2000000') for i in range(count)]
        else:
            rows = []
            for ticker, close in (('VELA','4.1200'), ('ABCD','5.00'), ('NEXA','4.12'), ('QBIT','4.119999999996')):
                if ticker == 'NEXA' and index == 9: continue
                rows.append(bar(day, ticker, close if index==10 else '4.0000', '4000000' if index==10 else '2000000'))
        result.append(summary(day, rows))
    return tuple(result)


def seed(preview, values=None):
    for source in sources() if values is None else values: preview._collection.retain(source, NOW)


class Offline(previous.Offline):
    def setUp(self):
        super().setUp(); seed(self.preview)

    def evaluate(self, **filters):
        return self.preview.evaluate_screening(TARGET.isoformat(), **filters)


class EvaluationTests(Offline):
    def test_explicit_handoff_exact_engine_metrics_and_source_provenance(self):
        with patch.object(c, '_screen_grouped_history', wraps=c._screen_grouped_history) as engine:
            result = self.evaluate()
        engine.assert_called_once()
        called_sources, target = engine.call_args.args
        self.assertEqual(target, TARGET)
        for source, day in zip(called_sources, DAYS): self.assertIs(source, self.preview._collection.get(day).summary)
        self.assertEqual(result['counts'], {'PRELIMINARY_MATCH':1, 'PRELIMINARY_NOT_MET':2, 'INSUFFICIENT_HISTORY':1})
        row = next(row for row in result['rows'] if row['ticker']=='VELA')
        self.assertEqual(row['source_close'], '4.1200')
        self.assertEqual(row['change'], {'numerator':'3','denominator':'1','display':'3.00'})
        self.assertEqual(row['average']['numerator'], '2000000')
        self.assertEqual(row['relative']['numerator'], '2')
        self.assertEqual(row['volume']['numerator'], '4000000')
        self.assertEqual(row['prior_observation_dates'], [day.isoformat() for day in DAYS[:-1]])
        self.assertEqual(row['volume_baseline_dates'], row['prior_observation_dates'])
        self.assertEqual(result['sources'][0]['retrieved_at'], NOW.isoformat())
        self.read.assert_not_called(); self.other.assert_not_called()

    def test_missing_history_is_not_repaired_by_eleven_retained_dates(self):
        result = self.evaluate(); row = next(row for row in result['rows'] if row['ticker']=='NEXA')
        self.assertEqual(row['outcome'], 'INSUFFICIENT_HISTORY')
        self.assertEqual(len(row['prior_observation_dates']), 9)
        self.assertIsNone(row['average']); self.assertIsNone(row['relative'])
        self.assertEqual(row['volume_baseline_dates'], [])
        self.assertEqual(row['previous_observation_date'], DAYS[-3].isoformat())
        self.assertIn('AVERAGE_VOLUME_10', row['missing'])

    def test_strict_price_boundary_and_inclusive_gain(self):
        result = self.evaluate(); rows = {row['ticker']:row for row in result['rows']}
        self.assertIn('PRICE', rows['ABCD']['failed'])
        self.assertEqual(rows['VELA']['outcome'], 'PRELIMINARY_MATCH')
        self.assertEqual(rows['QBIT']['change']['display'], '3.00')
        self.assertIn('OBSERVED_CHANGE', rows['QBIT']['failed'])
        relaxed = self.evaluate(price='5.000000000001')
        self.assertEqual(next(row for row in relaxed['rows'] if row['ticker']=='ABCD')['outcome'], 'PRELIMINARY_MATCH')

    def test_average_volume_is_strictly_above_threshold(self):
        result = self.evaluate(average='2000000')
        self.assertEqual(result['counts']['PRELIMINARY_MATCH'], 0)
        self.assertIn('AVERAGE_VOLUME_10', next(row for row in result['rows'] if row['ticker']=='VELA')['failed'])

    def test_optional_relative_threshold_exact_and_inclusive(self):
        self.assertEqual(self.evaluate(relative='2')['counts']['PRELIMINARY_MATCH'], 1)
        result = self.evaluate(relative='2.000000000001')
        self.assertEqual(result['counts']['PRELIMINARY_MATCH'], 0)
        self.assertIn('RELATIVE_VOLUME', next(row for row in result['rows'] if row['ticker']=='VELA')['failed'])
        missing = next(row for row in result['rows'] if row['ticker']=='NEXA')
        self.assertIn('RELATIVE_VOLUME', missing['missing'])
        self.assertEqual(missing['outcome'], 'INSUFFICIENT_HISTORY')

    def test_zero_relative_threshold_still_requires_supported_metric(self):
        result = self.evaluate(relative='0')
        self.assertIn('RELATIVE_VOLUME', next(row for row in result['rows'] if row['ticker']=='NEXA')['missing'])
        zero = tuple(summary(day,[bar(day,volume='0')]) for day in DAYS)
        self.preview.clear_collection(); seed(self.preview, zero)
        result = self.evaluate(relative='0')
        self.assertIsNone(result['rows'][0]['relative'])
        self.assertIn('ZERO_BASELINE_VOLUME', result['rows'][0]['unavailable'])
        self.assertEqual(result['rows'][0]['outcome'], 'PRELIMINARY_NOT_MET')

    def test_hostile_decimal_context_does_not_change_results(self):
        expected = self.evaluate(); expected.pop('evaluation_id')
        with localcontext() as context:
            context.prec=1; context.traps[Inexact]=context.traps[Rounded]=True
            result = self.evaluate(); result.pop('evaluation_id')
        self.assertEqual(result, expected)

    def test_explicit_earlier_target_does_not_include_later_sources(self):
        result = self.preview.evaluate_screening(DAYS[-2].isoformat())
        self.assertEqual(result['target_date'], DAYS[-2].isoformat())
        self.assertEqual(len(result['sources']), 10)
        self.assertNotIn(TARGET.isoformat(), [source['requested_date'] for source in result['sources']])
        self.assertEqual(result['counts']['PRELIMINARY_MATCH'], 0)

    def test_market_cap_and_full_strategy_never_qualified(self):
        result = self.evaluate()
        self.assertEqual(result['market_cap_qualification'], 'UNVERIFIED')
        self.assertEqual(result['full_strategy_qualification'], 'NOT AVAILABLE')
        for row in result['rows']:
            self.assertEqual(row['market_cap_qualification'], 'UNVERIFIED')
            self.assertEqual(row['session_continuity'], 'UNVERIFIED')
            self.assertEqual(row['adjustment_vintage'], 'UNVERIFIED')
            self.assertNotIn('score', row); self.assertNotIn('signal', row)
        self.assertIn('NO_TRADING_AUTHORITY', result['limitations'])

    def test_missing_target_and_bad_filters_are_safe_and_do_not_request(self):
        self.assertEqual(self.preview.evaluate_screening('2026-10-01')['reason'], 'COLLECTION_TARGET_MISSING')
        for field, value in (('price','NaN'), ('price','Infinity'), ('price','0'), ('price','-1'),
                             ('change','-101'), ('change','100001'), ('average','-1'), ('average','1e6'),
                             ('relative','-1'), ('relative','1000001'), ('relative',None), ('price',KEY)):
            result = self.evaluate(**{field:value})
            self.assertEqual(result['reason'], 'INVALID_SCREENING_REQUEST'); self.assertNotIn(KEY,json.dumps(result))
        self.read.assert_not_called(); self.other.assert_not_called()

    def test_default_reset_settings_restore_original_assessment(self):
        original = self.evaluate()
        self.assertEqual(self.evaluate(price='1')['counts']['PRELIMINARY_MATCH'],0)
        reset = self.evaluate()
        self.assertEqual(reset['filters'], {'price':'5','change':'3','average':'1000000','relative':None})
        self.assertEqual(reset['counts'],original['counts'])


class ViewTests(Offline):
    def test_local_views_never_reenter_engine_or_provider(self):
        result = self.evaluate()
        with patch.object(c, '_screen_grouped_history', side_effect=AssertionError('No recalculation')):
            self.preview.screening_status()
            for sort in s._SORTS:
                self.preview.screening_page(result['evaluation_id'], sort=sort)
        self.read.assert_not_called(); self.other.assert_not_called()

    def test_case_insensitive_search_and_outcome_are_combined_with_and(self):
        result = self.evaluate(); serial=result['evaluation_id']
        rows = self.preview.screening_page(serial, search='veLa', outcome='PRELIMINARY_MATCH')['rows']
        self.assertEqual([row['ticker'] for row in rows],['VELA'])
        empty=self.preview.screening_page(serial, search='NEXA', outcome='PRELIMINARY_MATCH')
        self.assertEqual(empty['rows'],[]); self.assertEqual(empty['matched_view_rows'],0)
        self.assertEqual(empty['counts']['PRELIMINARY_MATCH'],1)

    def test_exact_sorting_nulls_last_and_deterministic_ties(self):
        serial=self.evaluate()['evaluation_id']
        for sort, expected in (('change',['ABCD','NEXA','VELA','QBIT']),
                               ('price',['QBIT','NEXA','VELA','ABCD']),
                               ('price_desc',['ABCD','NEXA','VELA','QBIT']),
                               ('relative',['ABCD','QBIT','VELA','NEXA']),
                               ('average',['ABCD','QBIT','VELA','NEXA']),
                               ('symbol',['ABCD','NEXA','QBIT','VELA'])):
            first=self.preview.screening_page(serial,sort=sort)
            self.assertEqual([row['ticker'] for row in first['rows']],expected)
            self.assertEqual(first,self.preview.screening_page(serial,sort=sort))

    def test_invalid_view_input_has_no_partial_data(self):
        serial=self.evaluate()['evaluation_id']
        for kwargs in ({'page':True}, {'page':600}, {'page':-1}, {'search':KEY}, {'search':'<svg>'},
                       {'sort':'market_cap'}, {'outcome':'BUY'}):
            self.assertEqual(self.preview.screening_page(serial,**kwargs)['reason'],'INVALID_SCREENING_REQUEST')
        self.assertEqual(self.preview.screening_page(True)['reason'],'SCREENING_SUPERSEDED')

    def test_pagination_never_exposes_more_than_fifty_rows(self):
        self.preview.clear_collection(); seed(self.preview,sources(120)); result=self.evaluate()
        self.assertEqual(len(result['rows']),50); self.assertEqual(result['pages'],3)
        second=self.preview.screening_page(result['evaluation_id'],page=1)
        final=self.preview.screening_page(result['evaluation_id'],page=599)
        self.assertEqual((len(second['rows']),len(final['rows'])),(50,20))
        self.assertEqual(final['page'],2)
        self.assertFalse({row['ticker'] for row in result['rows']} & {row['ticker'] for row in second['rows']})
        self.assertLess(len(json.dumps(final).encode()), s._MAX_SCREEN_BODY)


class LifetimeTests(Offline):
    def test_startup_metadata_and_collection_changes_never_evaluate(self):
        with patch.object(c,'_screen_grouped_history',side_effect=AssertionError('Explicit only')):
            self.assertEqual(self.preview.screening_status()['reason'],'USER_EVALUATION_REQUIRED')
            self.preview.configuration(); self.preview.collection_status(); self.preview.discovery(); self.preview.research()
        self.read.assert_not_called()

    def test_replacement_invalidates_old_page_and_preserves_single_projection(self):
        first=self.evaluate(); old=self.preview._screening
        second=self.evaluate(price='1')
        self.assertGreater(second['evaluation_id'],first['evaluation_id'])
        self.assertIsNot(self.preview._screening,old)
        self.assertEqual(self.preview.screening_page(first['evaluation_id'])['reason'],'SCREENING_SUPERSEDED')
        self.assertFalse(hasattr(self.preview._screening,'evidence'))

    def test_clear_and_shutdown_release_projection_without_touching_safe_snapshot(self):
        before=build_demo_snapshot().to_payload()
        self.evaluate(); self.preview.clear_collection()
        self.assertIsNone(self.preview._screening)
        self.assertEqual(self.preview.screening_status()['reason'],'USER_EVALUATION_REQUIRED')
        seed(self.preview); self.evaluate(); self.preview.close()
        self.assertIsNone(self.preview._screening); self.assertEqual(self.preview.collection_status()['retained_count'],0)
        self.assertEqual(build_demo_snapshot().to_payload(),before)

    def test_new_date_invalidates_projection_but_reusing_date_does_not(self):
        self.evaluate(); snapshot=self.preview._screening
        self.preview.load_discovery(TARGET.isoformat())
        self.assertIs(self.preview._screening,snapshot); self.read.assert_not_called()
        self.preview.clear_collection(); seed(self.preview,sources()[1:]); self.evaluate()
        self.preview.load_discovery(DAYS[0].isoformat())
        self.assertIsNone(self.preview._screening); self.read.assert_called_once()

    def test_bounded_projection_error_does_not_publish_partial_or_stale_result(self):
        self.evaluate()
        with patch.object(s,'_MAX_SCREEN_BYTES',1):
            result=self.evaluate()
        self.assertEqual(result['reason'],'SCREENING_MEMORY_LIMIT'); self.assertIsNone(self.preview._screening)
        self.assertFalse(self.preview._active); self.assertEqual(self.preview.collection_status()['retained_count'],11)

    def test_no_provider_quota_clocks_or_credentials_used_by_screening(self):
        with patch.object(massive._RATE,'reserve',side_effect=AssertionError('No quota')), \
             patch.object(massive.os.environ,'get',side_effect=AssertionError('No key')), \
             patch.object(massive.time,'monotonic',side_effect=AssertionError('No clock')):
            self.evaluate()
        self.read.assert_not_called(); self.other.assert_not_called()

    def test_failure_sanitization_and_original_interruptions(self):
        for error in (RuntimeError(KEY), MemoryError(KEY), KeyboardInterrupt(KEY), SystemExit(89), GeneratorExit(KEY)):
            with patch.object(massive,'_project_screen',side_effect=error):
                if isinstance(error,(KeyboardInterrupt,SystemExit,GeneratorExit)):
                    with self.assertRaises(type(error)) as caught:self.evaluate()
                    self.assertIs(caught.exception,error)
                else:
                    result=self.evaluate(); self.assertNotIn(KEY,json.dumps(result))
            self.assertFalse(self.preview._active); self.assertIsNone(self.preview._screening)

    def test_source_incompatibility_publishes_no_metrics(self):
        with patch.object(c,'_screen_grouped_history',side_effect=lambda values,target,**kwargs:m._screen_grouped_history((),target,**kwargs)):
            result=self.evaluate()
        self.assertEqual(result['reason'],'SCREENING_SOURCE_INCOMPATIBLE'); self.assertNotIn('rows',result)
        self.assertIsNone(self.preview._screening)

    def test_projection_is_frozen_slotted_opaque_and_has_no_source_wrappers(self):
        self.evaluate(); snapshot=self.preview._screening
        for value,field in ((snapshot,'rows'),(snapshot.rows[0],'price')):
            self.assertFalse(hasattr(value,'__dict__'))
            self.assertNotIn('VELA',repr(value))
            with self.assertRaises(FrozenInstanceError):setattr(value,field,None)
        self.assertFalse(hasattr(snapshot.rows[0],'bar')); self.assertFalse(hasattr(snapshot,'replay'))


class HTTPTests(unittest.TestCase):
    setUp=discovery_tests.HTTPTests.setUp
    request=discovery_tests.HTTPTests.request

    def evaluate(self):
        seed(self.server.market_data)
        code,headers,value=self.request('/api/market-data/multiday/evaluate?target=2026-10-16',headers=ACTION)
        self.assertEqual(code,200);return json.loads(value)

    def test_explicit_local_get_and_unchanged_csp_host_origin_protections(self):
        route='/api/market-data/multiday/evaluate?target=2026-10-16'
        for headers in ({},{**ACTION,'Origin':'https://example.invalid'},{**ACTION,'Sec-Fetch-Site':'cross-site'}, {**ACTION,'Host':'evil.invalid'}):
            self.assertEqual(self.request(route,headers=headers)[0],403)
        self.assertEqual(self.request(route,'HEAD',headers=ACTION)[0],405)
        result=self.evaluate();self.assertEqual(result['state'],'HISTORICAL_SCREENING_RESULT')
        self.read.assert_not_called();self.old.assert_not_called()

    def test_status_page_head_and_manual_only_evaluation(self):
        with patch.object(c,'_screen_grouped_history',wraps=c._screen_grouped_history) as engine:
            self.assertEqual(self.request('/api/market-data/multiday')[0],200)
            self.assertEqual(self.request('/api/market-data/multiday','HEAD')[2],b'');engine.assert_not_called()
            result=self.evaluate();engine.assert_called_once()
            path='/api/market-data/multiday/page?evaluation='+str(result['evaluation_id'])
            code,headers,value=self.request(path)
            self.assertEqual(code,200);self.assertLess(len(value),s._MAX_SCREEN_BODY)
            self.assertEqual(headers['Content-Security-Policy'],web._CSP)
            self.assertEqual(self.request(path,'HEAD')[2],b'');engine.assert_called_once()
            self.assertNotIn(KEY.encode(),value)

    def test_closed_routes_queries_and_all_mutating_methods_fail(self):
        for path in ('/api/market-data/multiday','/api/market-data/multiday/evaluate?target=2026-10-16','/api/market-data/multiday/page?evaluation=1'):
            for method in ('POST','PUT','PATCH','DELETE','OPTIONS','TRACE','CONNECT'):
                self.assertEqual(self.request(path,method,headers=ACTION)[0],405)
        for path in ('/api/market-data/multiday/evaluate?target=x&target=y',
                     '/api/market-data/multiday/evaluate?target=2026-10-16&url=x',
                     '/api/market-data/multiday/page?evaluation=True',
                     '/api/market-data/multiday/page?evaluation=1&page=9999'):
            self.assertEqual(self.request(path,headers=ACTION)[0],400)
        self.assertEqual(self.request('/api/market-data/multiday/../export')[0],404)
        self.read.assert_not_called()

    def test_response_byte_limit_fails_closed_without_rows_or_secrets(self):
        seed(self.server.market_data)
        with patch.object(web,'_MAX_SCREEN_BODY',1):
            value=self.request('/api/market-data/multiday/evaluate?target=2026-10-16',headers=ACTION)[2]
        self.assertEqual(json.loads(value)['reason'],'SCREENING_RESPONSE_LIMIT')
        self.assertEqual(json.loads(value)['rows'],[]);self.assertNotIn(KEY.encode(),value)


class BrowserTests(unittest.TestCase):
    setUpClass=classmethod(discovery_tests.BrowserTests.setUpClass.__func__)
    setUp,open,tearDown=discovery_tests.BrowserTests.setUp,discovery_tests.BrowserTests.open,discovery_tests.BrowserTests.tearDown

    def prepare(self,count=None,width=1440):
        self.open(width=width);seed(self.server.market_data,sources(count))
        self.page.locator('#multi-refresh-dates').click()
        self.page.locator('#multi-target option').filter(has_text='2026-10-16').wait_for(state='attached')
        self.page.locator('#multi-evaluate:not([disabled])').wait_for()

    def evaluate(self):
        self.page.locator('#multi-evaluate').click()
        self.page.locator('#multi-applied').filter(has_text='Applied target').wait_for()
        self.page.locator('#multi-evaluate:not([disabled])').wait_for()

    def test_startup_dates_and_threshold_edits_do_not_automatically_evaluate(self):
        self.prepare()
        self.assertIsNone(self.server.market_data._screening)
        self.page.locator('#multi-price').fill('6')
        self.assertIsNone(self.server.market_data._screening)
        self.assertEqual(self.page.locator('#multi-rows tr').count(),0)
        self.page.locator('#multi-target').select_option('2026-10-15')
        self.assertIsNone(self.server.market_data._screening)
        self.read.assert_not_called();self.ticker_read.assert_not_called()

    def test_metrics_outcomes_sources_and_unverified_cap_are_visible(self):
        self.prepare();self.evaluate()
        self.assertEqual(self.page.locator('#multi-total').inner_text(),'4')
        self.assertEqual(self.page.locator('#multi-matches').inner_text(),'1')
        row=self.page.locator('#multi-rows tr[data-ticker="NEXA"]')
        self.assertIn('INSUFFICIENT_HISTORY',row.inner_text())
        self.assertIn('UNAVAILABLE',row.inner_text());self.assertIn('Market cap UNVERIFIED',row.inner_text())
        row.locator('summary').click();self.assertIn('9 prior observations',row.inner_text())
        self.assertEqual(self.page.locator('#multi-sources li').count(),11)
        self.read.assert_not_called();self.ticker_read.assert_not_called()

    def test_search_sort_outcome_pagination_and_reset(self):
        self.prepare(120);self.evaluate();serial=self.server.market_data._screening.evaluation_id
        self.assertEqual(self.page.locator('#multi-rows tr').count(),50)
        self.page.locator('#multi-next').click()
        self.page.locator('#multi-page').filter(has_text='Page 2 / 3').wait_for()
        self.page.locator('#multi-search').fill('daaaa')
        self.page.wait_for_function('() => document.querySelectorAll("#multi-rows tr").length===1')
        self.page.locator('#multi-outcome').select_option('INSUFFICIENT_HISTORY')
        self.page.wait_for_function('() => document.querySelectorAll("#multi-rows tr").length===0')
        self.assertTrue(self.page.locator('#multi-empty').is_visible())
        self.assertEqual(self.server.market_data._screening.evaluation_id,serial)
        self.page.locator('#multi-price').fill('1')
        self.assertIn('Pending',self.page.locator('#multi-message').inner_text())
        self.page.locator('#multi-reset').click()
        self.assertEqual(self.page.locator('#multi-price').input_value(),'5')
        self.assertEqual(self.page.locator('#multi-relative').input_value(),'')
        self.assertEqual(self.server.market_data._screening.evaluation_id,serial)
        self.evaluate();self.assertGreater(self.server.market_data._screening.evaluation_id,serial)
        self.read.assert_not_called()

    def test_filter_changes_require_explicit_action_and_invalid_input_does_not_evaluate(self):
        self.prepare();self.evaluate();serial=self.server.market_data._screening.evaluation_id
        self.page.locator('#multi-relative').fill('2.000000000001');self.evaluate()
        self.assertEqual(self.page.locator('#multi-matches').inner_text(),'0')
        serial=self.server.market_data._screening.evaluation_id
        self.page.locator('#multi-price').fill('-1');self.page.locator('#multi-evaluate').click()
        self.assertEqual(self.server.market_data._screening.evaluation_id,serial)
        self.assertEqual(self.page.locator('#multi-price').get_attribute('aria-invalid'),'true')

    def test_late_response_cannot_relabel_changed_target(self):
        self.prepare();held=[]
        self.page.route('**/api/market-data/multiday/evaluate?*',lambda route:held.append(route))
        self.page.locator('#multi-evaluate').click();self.page.wait_for_timeout(40)
        self.assertEqual(len(held),1)
        result=self.server.market_data.evaluate_screening(TARGET.isoformat())
        self.page.locator('#multi-target').select_option(DAYS[-2].isoformat())
        held[0].fulfill(json=result);self.page.wait_for_timeout(40)
        self.assertEqual(self.page.locator('#multi-rows tr').count(),0)
        self.assertNotIn('Applied target',self.page.locator('#multi-applied').inner_text())

    def test_malformed_optional_numeric_input_is_not_silently_disabled(self):
        self.prepare();self.evaluate();serial=self.server.market_data._screening.evaluation_id
        self.page.locator('#multi-relative').press_sequentially('1e')
        self.assertTrue(self.page.locator('#multi-relative').evaluate('(node)=>node.validity.badInput'))
        self.page.locator('#multi-evaluate').click()
        self.assertEqual(self.server.market_data._screening.evaluation_id,serial)
        self.assertEqual(self.page.locator('#multi-relative').get_attribute('aria-invalid'),'true')
        self.page.locator('#multi-reset').click()
        self.assertEqual(self.page.locator('#multi-relative').get_attribute('aria-invalid'),'false')

    def test_clear_retention_invalidates_result_and_keeps_demo_intact(self):
        self.prepare();self.evaluate();self.page.locator('#discovery-date').fill('2026-10-16')
        self.page.locator('#discovery-date').dispatch_event('change')
        self.page.locator('#collection-clear:not([disabled])').wait_for();self.page.locator('#collection-clear').click()
        self.page.wait_for_function('() => document.querySelectorAll("#multi-rows tr").length===0')
        self.assertIsNone(self.server.market_data._screening)
        self.assertIn('DEMO MARKET DATA — NO LIVE FEED',self.page.content())
        self.assertIn('TRADING BLOCKED',self.page.content());self.read.assert_not_called()

    def test_narrow_layout_contains_table_scroll_and_keyboard_controls(self):
        self.prepare(width=600);self.evaluate()
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > window.innerWidth'))
        self.page.locator('#multi-search').focus()
        self.assertTrue(self.page.locator('#multi-search').evaluate('(node)=>node===document.activeElement'))


class StaticTests(unittest.TestCase):
    def test_projection_and_frontend_do_not_calculate_source_metrics_or_request_external_data(self):
        module=(ROOT/'tradingbot_marketdata/screener.py').read_text(encoding='utf-8')
        for node in ast.walk(ast.parse(module)):
            if isinstance(node,ast.ImportFrom):
                self.assertIn(node.module,('dataclasses','datetime','decimal','fractions','sys','models','multiday','research'))
            if isinstance(node,ast.Call):
                name=node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id if isinstance(node.func,ast.Name) else ''
                self.assertNotIn(name,('now','today','open','connect','request','execute','eval','exec','placeOrder','cancelOrder'))
        script=(ROOT/'tradingbot_dashboard/static/multiday-screener.js').read_text(encoding='utf-8')
        for forbidden in ('setTimeout','setInterval','localStorage','sessionStorage','document.cookie','innerHTML','https://','http://','MASSIVE_API_KEY','BigInt','parseFloat'):
            self.assertNotIn(forbidden,script)
        self.assertEqual(s.__all__,())

    def test_utf8_visible_safety_and_research_labels(self):
        html=(ROOT/'tradingbot_dashboard/static/index.html').read_text(encoding='utf-8')
        for text in ('REAL MULTI-DAY EOD SCREENER','MARKET CAP ≥ $50M: UNVERIFIED',
                     'FULL STRATEGY QUALIFICATION: NOT AVAILABLE','DEMO MARKET DATA — NO LIVE FEED',
                     'Restart requires manually reloading dates','TRADING BLOCKED'):
            self.assertIn(text,html)


class ConcurrencyTests(Offline):
    def test_only_one_evaluation_and_no_clear_or_collection_during_handoff(self):
        import threading
        entered,release=threading.Event(),threading.Event();results=[]
        engine=c._screen_grouped_history
        def hold(*args,**kwargs):
            entered.set();self.assertTrue(release.wait(5));return engine(*args,**kwargs)
        with patch.object(c,'_screen_grouped_history',side_effect=hold):
            thread=threading.Thread(target=lambda:results.append(self.evaluate()))
            thread.start();self.assertTrue(entered.wait(3))
            try:
                self.assertEqual(self.evaluate()['reason'],'REQUEST_IN_PROGRESS')
                self.assertEqual(self.preview.clear_collection()['reason'],'REQUEST_IN_PROGRESS')
                self.assertEqual(self.preview.load_discovery(TARGET.isoformat())['reason'],'REQUEST_IN_PROGRESS')
            finally:release.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(results[0]['state'],'HISTORICAL_SCREENING_RESULT')
        self.read.assert_not_called()

    def test_shutdown_during_evaluation_cannot_republish_projection(self):
        with patch.object(massive,'_project_screen',side_effect=lambda *args:(self.preview.close(),None)[1]):
            result=self.evaluate()
        self.assertEqual(result['reason'],'COLLECTION_CLOSED')
        self.assertIsNone(self.preview._screening);self.assertFalse(self.preview._active)


class MemoryTests(Offline):
    def test_max_target_projection_and_page_are_bounded_without_source_duplication(self):
        self.preview.clear_collection()
        seed(self.preview,(summary(TARGET,[bar(TARGET,symbol(i),'4.12','4000000') for i in range(30000)]),))
        # Only one target is needed for worst target-row projection; PRE4A/B
        # independently test maximum history. This test measures the added work.
        gc.collect();tracemalloc.start()
        try:
            result=self.evaluate();_,peak=tracemalloc.get_traced_memory()
        finally:tracemalloc.stop()
        self.assertEqual(result['target_tickers'],30000);self.assertEqual(len(result['rows']),50)
        self.assertLessEqual(result['charged_projection_bytes'],s._MAX_SCREEN_BYTES)
        self.assertLess(peak,c._EVALUATION_RESERVE+s._MAX_SCREEN_BYTES+c._PREVIEW_RESERVE)
        self.assertLess(len(json.dumps(result).encode()),s._MAX_SCREEN_BODY)
        self.assertEqual(len(self.preview._screening.rows),30000)
        print(f'PRE4C_SYNTHETIC_MEMORY rows=30000 peak={peak} charged={result["charged_projection_bytes"]}',flush=True)

    def test_representative_complete_history_and_projection_fit_additional_budget(self):
        self.preview.clear_collection();seed(self.preview,sources(12630));gc.collect();tracemalloc.start()
        try:
            result=self.evaluate();retained,peak=tracemalloc.get_traced_memory()
        finally:tracemalloc.stop()
        self.assertEqual(result['counts']['PRELIMINARY_MATCH'],12630)
        self.assertLess(retained,s._MAX_SCREEN_BYTES)
        self.assertLess(peak,c._EVALUATION_RESERVE+s._MAX_SCREEN_BYTES+c._PREVIEW_RESERVE)
        self.assertLessEqual(result['charged_projection_bytes'],s._MAX_SCREEN_BYTES)
        print(f'PRE4C_SYNTHETIC_MEMORY rows=12630 dates=11 retained={retained} peak={peak} charged={result["charged_projection_bytes"]}',flush=True)


if __name__=='__main__':unittest.main()
