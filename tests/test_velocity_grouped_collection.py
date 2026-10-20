"""Synthetic PRE4B collection tests; no personal data or external requests."""

import ast
from contextlib import ExitStack
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import gc
import json
from pathlib import Path
import threading
import tracemalloc
import unittest
from unittest.mock import patch

from tradingbot_dashboard.demo_data import build_demo_snapshot
from tradingbot_marketdata import collection as c, discovery as d, massive, multiday as m
from tests import test_velocity_market_discovery as prior
from tests.test_velocity_multiday import DAYS, TARGET, bar, stamp, summary, symbol

ROOT = Path(__file__).resolve().parents[1]
NOW, KEY = prior.NOW, prior.KEY
ACTION = {'X-Velocity-Market-Preview': '1'}


def fixture(day, count=1):
    # Entirely synthetic, independently allocated source rows and Decimal values.
    row = ','.join('{"T":"'+symbol(i)+'","o":4.12,"h":4.12,"l":4.12,"c":4.12,'
                   '"v":2000000.00,"vw":4.1200,"t":'+str(stamp(day))+'}' for i in range(count))
    return ('{"status":"OK","adjusted":true,"count":'+str(count)+',"resultsCount":'+str(count)+',"results":['+row+']}').encode()


class Offline(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for name in ('socket.socket.connect', 'socket.getaddrinfo'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('External network forbidden')))
        self.stack.enter_context(patch.dict('os.environ', {'MASSIVE_API_KEY': KEY}))
        self.clock = self.stack.enter_context(patch.object(massive, '_now', return_value=NOW))
        self.tick = self.stack.enter_context(patch.object(massive.time, 'monotonic', return_value=1000.0))
        self.stack.enter_context(patch.object(massive, '_RATE', massive._RateGate()))
        self.read = self.stack.enter_context(patch.object(massive, '_read_grouped_response', side_effect=self.response))
        self.other = self.stack.enter_context(patch.object(massive, '_read_response', side_effect=AssertionError('No ticker load')))
        self.preview = massive._MarketDataPreview(enabled=True)
        self.addCleanup(self.preview.close)

    @staticmethod
    def response(path, key):
        day = date.fromisoformat(path.split('/stocks/')[1].split('?')[0])
        return 200, 'application/json', '', fixture(day)

    def load(self, day=TARGET):
        self.tick.return_value += 60
        return self.preview.load_discovery(day.isoformat())

    def snapshot(self):
        return self.preview.collection_status(TARGET.isoformat())


class RetentionTests(Offline):
    def test_one_through_eleven_dates_retain_exact_sources_and_retrieval_times(self):
        for index, day in enumerate(DAYS):
            result = self.load(day)
            self.assertTrue(result['retained'])
            entry = self.preview._collection.get(day)
            self.assertIs(entry.summary, self.preview._discovery[0])
            self.assertEqual(entry.retrieved_at, NOW)
            self.assertFalse(hasattr(entry, '__dict__'))
            with self.assertRaises(FrozenInstanceError): entry.retrieved_at = NOW
            status = self.snapshot()
            self.assertEqual(status['retained_count'], index+1)
            self.assertEqual(status['dates'][-1]['requested_date'], day.isoformat())
            self.assertEqual(status['dates'][-1]['validated_rows'], 1)
        self.assertEqual(status['input_status'], 'TEN_EARLIER_DATES_RETAINED_TICKER_HISTORY_UNVERIFIED')
        self.assertEqual(self.read.call_count, 11); self.other.assert_not_called()

    def test_nonchronological_load_order_visible_but_explicit_handoff_sorted(self):
        days = (DAYS[-1], DAYS[1], DAYS[0])
        for day in days: self.load(day)
        self.assertEqual(tuple(item['requested_date'] for item in self.snapshot()['dates']), tuple(day.isoformat() for day in days))
        with self.preview._collection_evaluation(TARGET.isoformat()) as result:
            self.assertEqual(result.source_dates, tuple(sorted(days)))
            self.assertIs(result.rows[0].outcome, m._ScreenOutcome.PRELIMINARY_NOT_MET)
            self.assertIn(m._Criterion.AVERAGE_VOLUME_10, result.rows[0].missing)

    def test_repeat_beyond_cache_expiry_reuses_exact_evidence_without_quota_or_refresh(self):
        self.load(); entry = self.preview._collection.get(TARGET)
        self.tick.return_value += 10000
        self.clock.return_value += timedelta(days=6)
        with patch.object(massive._RATE, 'reserve', side_effect=AssertionError('No quota')):
            result = self.preview.load_discovery(TARGET.isoformat())
        self.assertTrue(result['retained']); self.assertFalse(result['http_cache_valid'])
        self.assertEqual(result['state'], 'STALE')
        self.assertEqual(result['retrieved_at'], entry.retrieved_at.isoformat())
        self.assertIs(entry, self.preview._collection.get(TARGET))
        self.read.assert_called_once()

    def test_switching_retained_dates_does_not_refresh_or_replace_the_cache(self):
        self.load(DAYS[0]); self.load(TARGET)
        cache = self.preview._discovery_cache
        result = self.preview.load_discovery(DAYS[0].isoformat())
        self.assertTrue(result['retained']); self.assertFalse(result['http_cache_valid'])
        self.assertIs(cache, self.preview._discovery_cache)
        self.assertEqual(self.read.call_count, 2)

    def test_twelfth_date_fails_before_quota_and_preserves_every_date(self):
        for day in DAYS: self.load(day)
        before = self.snapshot()
        with patch.object(massive._RATE, 'reserve', side_effect=AssertionError('No quota')):
            result = self.preview.load_discovery('2026-10-01')
        self.assertEqual(result['reason'], 'COLLECTION_DATE_LIMIT')
        self.assertEqual(self.snapshot(), before); self.assertEqual(self.read.call_count, 11)
        self.assertTrue(self.preview.load_discovery(TARGET.isoformat())['retained'])

    def test_empty_weekday_response_retained_without_inventing_session_or_rows(self):
        self.read.return_value = None; self.read.side_effect = lambda *args: (200, 'application/json', '', fixture(date(2026,7,3), 0))
        result = self.load(date(2026,7,3))
        self.assertEqual(result['reason'], 'NO_SUPPORTED_DAILY_OBSERVATIONS')
        status = self.preview.collection_status('2026-07-03')
        self.assertEqual((status['retained_count'], status['retained_rows']), (1, 0))
        self.assertIn('SESSION_CONTINUITY_UNVERIFIED', status['limitations'])

    def test_missing_weekend_current_or_malformed_dates_do_not_request(self):
        for value in ('2026-10-17', '2026-10-19', '', '2026-10-16?x=1', '2026-02-30'):
            self.assertEqual(self.preview.load_discovery(value)['reason'], 'INVALID_REQUEST')
        self.read.assert_not_called(); self.assertEqual(self.snapshot()['retained_count'], 0)

    def test_status_missing_target_and_incomplete_inputs_not_screening_success(self):
        self.assertEqual(self.snapshot()['input_status'], 'TARGET_NOT_RETAINED')
        self.load()
        self.assertEqual(self.snapshot()['input_status'], 'INSUFFICIENT_DATED_INPUTS')
        self.assertNotIn('rows', self.snapshot())
        self.assertEqual(self.preview.collection_status('bad')['reason'], 'INVALID_REQUEST')

    def test_eleven_dates_do_not_imply_ten_prior_ticker_observations(self):
        for index, day in enumerate(DAYS):
            self.read.side_effect = lambda path, key, day=day, index=index: (200, 'application/json', '', fixture(day, 0 if index==4 else 1))
            self.load(day)
        self.assertEqual(self.snapshot()['earlier_retained_dates'], 10)
        with self.preview._collection_evaluation(TARGET.isoformat()) as result:
            row = result.rows[0]
            self.assertEqual(len(row.history), 9); self.assertIsNone(row.average_volume_10)
            self.assertIn(m._Criterion.AVERAGE_VOLUME_10, row.missing)
            self.assertIn('SPLIT_ADJUSTED_SOURCE_CONTRACT_NOT_COMMON_ADJUSTMENT_VINTAGE_PROOF', row.limitations)

    def test_all_local_views_and_status_do_not_run_evaluator_or_request(self):
        with patch.object(c, '_screen_grouped_history', side_effect=AssertionError('No automatic evaluation')):
            self.load()
            self.preview.discovery(); self.preview.research(); self.preview.configuration(); self.snapshot()
        self.read.assert_called_once(); self.other.assert_not_called()

    def test_explicit_clear_releases_grouped_aliases_preserves_watchlist_and_shortlist(self):
        self.load(); ticker = self.preview._discovery[0].rows[0].ticker
        self.preview.select_research((ticker,))
        config = self.preview.configuration(); self.preview._loaded[('AAPL', 'history')] = ('anonymous', NOW)
        result = self.preview.clear_collection()
        self.assertEqual(result['retained_count'], 0)
        self.assertIsNone(self.preview._discovery); self.assertEqual(self.preview._discovery_cache, {})
        self.assertEqual(self.preview.configuration(), config)
        self.assertIn(('AAPL', 'history'), self.preview._loaded)
        self.assertEqual(self.preview.discovery()['reason'], 'USER_LOAD_REQUIRED')
        self.read.assert_called_once()

    def test_clear_allows_new_explicit_request_without_resetting_shared_rate_gate(self):
        self.load(); self.preview.clear_collection()
        self.assertEqual(self.preview.load_discovery(TARGET.isoformat())['reason'], 'RATE_LIMITED')
        self.load(); self.assertEqual(self.read.call_count, 2)

    def test_shutdown_releases_state_and_cannot_reactivate(self):
        self.load(); self.preview.close(); self.preview.close()
        self.assertEqual(self.snapshot()['state'], 'COLLECTION_CLOSED')
        self.assertIsNone(self.preview._credential); self.assertEqual(self.preview._loaded, {})
        self.assertEqual(self.preview.clear_collection()['reason'], 'COLLECTION_CLOSED')
        self.assertFalse(self.preview.load_discovery(TARGET.isoformat()).get('retained', False))
        self.read.assert_called_once()

    def test_status_payload_is_copied_bounded_and_contains_no_raw_source(self):
        self.load(); status = self.snapshot(); status['dates'].clear(); status['limitations'].clear()
        self.assertEqual(len(self.snapshot()['dates']), 1)
        data = json.dumps(self.snapshot())
        self.assertLess(len(data), 3000); self.assertNotIn(KEY, data)
        for name in ('request_id', 'credential', 'ticker', 'close', 'provider_body'):
            self.assertNotIn(name, self.snapshot())


class FailureTests(Offline):
    def test_http_and_malformed_responses_preserve_retention_but_clear_single_date_view(self):
        self.load(); before = self.snapshot()
        for response in ((403, 'application/json', '', KEY.encode()),
                         (200, 'text/html', '', KEY.encode()),
                         (200, 'application/json', '', b'{"results":['),
                         (200, 'application/json', '', b'{"status":"OK","adjusted":false,"resultsCount":0}')):
            self.read.side_effect = None; self.read.return_value = response
            result = self.load(DAYS[0])
            self.assertEqual(result['state'], 'UNAVAILABLE'); self.assertNotIn(KEY, json.dumps(result))
            self.assertEqual(self.snapshot(), before); self.assertIsNone(self.preview._discovery)
            self.tick.return_value += 1000

    def test_capacity_reservation_exhaustion_before_request_or_quota(self):
        self.load(); before = self.snapshot()
        for bound in ('_MAX_RETAINED_ROWS', '_MAX_RETAINED_BYTES', '_MAX_WORKING_BYTES'):
            with patch.object(c, bound, 1), patch.object(massive._RATE, 'reserve', side_effect=AssertionError('No quota')):
                self.assertEqual(self.preview.load_discovery(DAYS[0].isoformat())['reason'], 'COLLECTION_MEMORY_LIMIT')
            self.assertEqual(self.snapshot(), before)
        self.read.assert_called_once()

    def test_post_decode_charge_rejection_preserves_existing_evidence(self):
        self.load(); before = self.snapshot()
        with patch.object(c, '_charge', return_value=c._MAX_RETAINED_BYTES+1):
            result = self.load(DAYS[0])
        self.assertEqual(result['reason'], 'COLLECTION_MEMORY_LIMIT')
        self.assertEqual(self.snapshot(), before); self.assertIsNone(self.preview._discovery)

    def test_cross_date_source_identity_conflict_is_not_repaired_by_sorting(self):
        early, late = DAYS[-2:]
        same = stamp(late, 4)
        first = summary(early, [bar(early, timestamp=same)])
        second = summary(late, [bar(late, timestamp=same)])
        with patch.object(massive, '_decode_grouped', side_effect=(first, second)):
            self.load(early); before = self.preview.collection_status()
            result = self.load(late)
        self.assertEqual(result['reason'], 'COLLECTION_SOURCE_INCOMPATIBLE')
        self.assertEqual(self.preview.collection_status(), before)

    def test_backwards_retrieval_clock_does_not_replace_data(self):
        self.load(); before = self.snapshot()
        self.clock.return_value = NOW-timedelta(hours=1)
        result = self.load(DAYS[0])
        self.assertEqual(result['reason'], 'INVALID_RESPONSE')
        self.assertEqual(self.snapshot(), before)

    def test_exact_interruption_identity_and_active_cleanup(self):
        self.load()
        for error in (KeyboardInterrupt(), SystemExit(71), GeneratorExit()):
            self.read.side_effect = error
            with self.assertRaises(type(error)) as caught: self.load(DAYS[0])
            self.assertIs(caught.exception, error); self.assertFalse(self.preview._active)
            self.assertEqual(self.snapshot()['retained_count'], 1)

    def test_failed_result_and_exception_categories_cannot_leak_strings(self):
        self.assertNotIn(KEY, c._CollectionError(KEY).reason)
        for error in (MemoryError(KEY), RuntimeError(KEY), OSError(KEY), TimeoutError(KEY)):
            self.read.side_effect = error
            result = self.load()
            self.assertNotIn(KEY, json.dumps(result)); self.assertEqual(self.snapshot()['retained_count'], 0)
            if type(error) is MemoryError:
                self.assertEqual(result['reason'], 'COLLECTION_MEMORY_LIMIT')
                self.assertEqual(result['retry_after_seconds'], 60)
            self.tick.return_value += 1000


class ConcurrencyTests(Offline):
    def test_inflight_blocks_second_load_clear_selection_and_evaluation(self):
        started, release = threading.Event(), threading.Event()
        original = self.response
        def hold(*args):
            started.set(); self.assertTrue(release.wait(5)); return original(*args)
        self.read.side_effect = hold
        results = []
        thread = threading.Thread(target=lambda: results.append(self.load()))
        thread.start(); self.assertTrue(started.wait(3))
        try:
            self.assertTrue(self.snapshot()['request_in_progress'])
            for result in (self.preview.load_discovery(DAYS[0].isoformat()), self.preview.clear_collection(), self.preview.select_research(())):
                self.assertEqual(result['reason'], 'REQUEST_IN_PROGRESS')
            with self.assertRaises(c._CollectionError):
                with self.preview._collection_evaluation(TARGET.isoformat()): pass
            self.assertEqual(self.snapshot()['retained_count'], 0)
        finally: release.set(); thread.join(5)
        self.assertFalse(thread.is_alive()); self.assertTrue(results[0]['retained']); self.read.assert_called_once()

    def test_shutdown_during_inflight_request_cannot_resurrect_collection(self):
        started, release = threading.Event(), threading.Event(); results = []
        def hold(*args):
            started.set(); self.assertTrue(release.wait(5)); return self.response(*args)
        self.read.side_effect = hold
        thread = threading.Thread(target=lambda: results.append(self.load()))
        thread.start(); self.assertTrue(started.wait(3)); self.preview.close()
        release.set(); thread.join(5)
        self.assertFalse(thread.is_alive()); self.assertEqual(results[0]['reason'], 'COLLECTION_CLOSED')
        self.assertEqual(self.snapshot()['retained_count'], 0); self.assertIsNone(self.preview._discovery)

    def test_context_handoff_serializes_evaluation_and_preserves_exact_sources(self):
        for day in DAYS: self.load(day)
        with self.preview._collection_evaluation(TARGET.isoformat()) as result:
            self.assertIs(result.rows[0].target.bar, self.preview._collection.get(TARGET).summary.rows[0])
            self.assertEqual(result.rows[0].average_volume_10, Fraction(2000000))
            self.assertEqual(self.preview.clear_collection()['reason'], 'REQUEST_IN_PROGRESS')
            self.assertEqual(self.preview.load_discovery(DAYS[0].isoformat())['reason'], 'REQUEST_IN_PROGRESS')
            with self.assertRaises(c._CollectionError):
                with self.preview._collection_evaluation(TARGET.isoformat()): pass
        self.assertFalse(self.preview._active); self.assertEqual(self.read.call_count, 11)

    def test_handoff_excludes_later_dates_and_preserves_missing_ticker_history(self):
        self.load(DAYS[0]); self.load(TARGET)
        with self.preview._collection_evaluation(DAYS[0].isoformat()) as result:
            self.assertEqual(result.source_dates, (DAYS[0],)); self.assertIsNone(result.rows[0].change_pct)
        with self.assertRaises(c._CollectionError):
            with self.preview._collection_evaluation(DAYS[1].isoformat()): pass
        self.assertFalse(self.preview._active)

    def test_evaluator_exception_releases_active_state_and_retains_sources(self):
        self.load(); before = self.snapshot()
        error = KeyboardInterrupt()
        with patch.object(c, '_screen_grouped_history', side_effect=error):
            with self.assertRaises(KeyboardInterrupt) as caught:
                with self.preview._collection_evaluation(TARGET.isoformat()): pass
        self.assertIs(caught.exception, error); self.assertEqual(self.snapshot(), before)


class QuotaTests(Offline):
    def test_all_dates_and_other_endpoint_share_unmodified_rate_gate(self):
        self.preview.load_discovery(TARGET.isoformat())
        self.tick.return_value += 14.9
        self.assertEqual(self.preview.load_discovery(DAYS[0].isoformat())['retry_after_seconds'], 1)
        self.tick.return_value += .1
        self.preview.load_discovery(DAYS[0].isoformat())
        self.tick.return_value += 15
        self.preview.load_discovery(DAYS[1].isoformat())
        self.tick.return_value += 15
        self.other.side_effect = None; self.other.return_value = (404, 'application/json', '', b'')
        self.preview.observe('AAPL', 'previous')
        self.tick.return_value = 1059.9
        self.assertEqual(self.preview.load_discovery(DAYS[2].isoformat())['reason'], 'RATE_LIMITED')
        other = massive._MarketDataPreview(enabled=True)
        try: self.assertEqual(other.load_discovery(DAYS[2].isoformat())['reason'], 'RATE_LIMITED')
        finally: other.close()
        self.assertEqual(self.read.call_count+self.other.call_count, 4)

    def test_provider_429_backoff_survives_clear_and_no_automatic_retry(self):
        self.read.side_effect = None; self.read.return_value = (429, 'application/json', '90', KEY.encode())
        self.assertEqual(self.load()['retry_after_seconds'], 90)
        self.preview.clear_collection(); self.tick.return_value += 15
        self.assertEqual(self.preview.load_discovery(TARGET.isoformat())['retry_after_seconds'], 75)
        self.read.assert_called_once()

    def test_demo_and_missing_key_never_lookup_or_request_on_status_clear(self):
        with patch.object(massive.os.environ, 'get', side_effect=AssertionError('No credential lookup')):
            demo = massive._MarketDataPreview()
            demo.collection_status(); demo.clear_collection(); demo.load_discovery(TARGET.isoformat()); demo.close()
        with patch.dict('os.environ', {}, clear=True):
            blank = massive._MarketDataPreview(enabled=True)
            self.assertEqual(blank.load_discovery(TARGET.isoformat())['reason'], 'KEY_NOT_CONFIGURED'); blank.close()
        self.read.assert_not_called()


class HTTPTests(unittest.TestCase):
    setUp = prior.HTTPTests.setUp
    request = prior.HTTPTests.request

    def test_status_get_head_and_fixed_clear_route_security(self):
        for path in ('/api/market-data/collection', '/api/market-data/collection?target=2026-10-16'):
            code, headers, value = self.request(path)
            self.assertEqual(code, 200); self.assertEqual(headers['Content-Security-Policy'], prior._CSP)
            self.assertEqual(self.request(path, 'HEAD')[2], b''); self.assertNotIn(KEY.encode(), value)
        route = '/api/market-data/collection/clear'
        self.assertEqual(self.request(route)[0], 403)
        self.assertEqual(self.request(route, 'HEAD')[0], 405)
        for headers in ({**ACTION, 'Origin':'https://example.invalid'}, {**ACTION, 'Sec-Fetch-Site':'cross-site'}, {**ACTION, 'Host':'evil.invalid'}):
            self.assertEqual(self.request(route, headers=headers)[0], 403)
        self.assertEqual(self.request(route, headers=ACTION)[0], 200)
        self.read.assert_not_called(); self.old.assert_not_called()

    def test_malformed_queries_traversal_and_write_methods_rejected(self):
        for path in ('/api/market-data/collection?target=a&target=b', '/api/market-data/collection?url=x'):
            self.assertEqual(self.request(path)[0], 400)
        self.assertEqual(self.request('/api/market-data/collection/clear?anything=1', headers=ACTION)[0], 404)
        for path in ('/api/market-data/collection', '/api/market-data/collection/clear'):
            for method in ('POST','PUT','PATCH','DELETE','OPTIONS','TRACE','CONNECT'):
                self.assertEqual(self.request(path, method)[0], 405)
        self.assertEqual(self.request('/api/market-data/collection/../clear')[0], 404)
        self.read.assert_not_called()

    def test_manual_load_status_and_clear_do_not_expose_credentials_or_auto_enrich(self):
        self.request('/api/market-data/discovery/load?date=2026-10-16', headers=ACTION)
        code, _, value = self.request('/api/market-data/collection?target=2026-10-16')
        self.assertEqual(code, 200); self.assertEqual(json.loads(value)['retained_count'], 1)
        self.assertNotIn(KEY.encode(), value); self.assertNotIn(b'rows":[', value)
        self.assertEqual(json.loads(self.request('/api/market-data/collection/clear', headers=ACTION)[2])['retained_count'], 0)
        self.read.assert_called_once(); self.old.assert_not_called()

    def test_server_close_clears_retention_even_if_preview_still_referenced(self):
        preview = self.server.market_data
        self.request('/api/market-data/discovery/load?date=2026-10-16', headers=ACTION)
        self.server.server_close()
        self.assertEqual(preview.collection_status()['state'], 'COLLECTION_CLOSED')
        self.assertIsNone(preview._discovery); self.assertEqual(preview._discovery_cache, {})


class BrowserTests(unittest.TestCase):
    setUpClass = classmethod(prior.BrowserTests.setUpClass.__func__)
    setUp, open, load, select = prior.BrowserTests.setUp, prior.BrowserTests.open, prior.BrowserTests.load, prior.BrowserTests.select
    tearDown = prior.BrowserTests.tearDown

    def test_status_startup_load_reuse_and_clear_preserve_shortlist_and_demo(self):
        self.open(); self.read.assert_not_called(); self.load(); self.select('VELA')
        self.page.locator('#collection-count').filter(has_text='1 / 11').wait_for()
        self.assertEqual(self.page.locator('#collection-dates li').count(), 1)
        self.load(); self.read.assert_called_once()
        self.page.locator('#collection-clear').click()
        self.page.locator('#collection-count').filter(has_text='0 / 11').wait_for()
        self.assertIn('VELA', self.page.locator('#eod-ticker').inner_text())
        self.assertEqual(self.page.locator('#discovery-rows tr').count(), 0)
        self.assertIn('TRADING BLOCKED', self.page.content()); self.assertIn('DEMO MARKET DATA — NO LIVE FEED', self.page.content())
        self.read.assert_called_once(); self.ticker_read.assert_not_called()

    def test_date_change_is_local_only_and_mobile_status_does_not_overflow(self):
        self.open(width=600); self.load()
        self.page.locator('#discovery-date').fill('2026-10-15')
        self.page.locator('#discovery-date').dispatch_event('change')
        self.page.locator('#collection-status').filter(has_text='Selected target is not retained').wait_for()
        self.assertEqual(self.page.locator('#collection-dates li').count(), 1)
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > window.innerWidth'))
        self.read.assert_called_once()

    def test_stale_status_response_does_not_overwrite_new_target_selection(self):
        self.open(); self.load(); held = []
        def hold(route):
            if 'target=2026-10-15' in route.request.url: held.append(route)
            else: route.continue_()
        self.page.route('**/api/market-data/collection?*', hold)
        self.page.locator('#discovery-date').fill('2026-10-15'); self.page.locator('#discovery-date').dispatch_event('change')
        self.page.wait_for_timeout(30)
        self.page.locator('#discovery-date').fill('2026-10-16'); self.page.locator('#discovery-date').dispatch_event('change')
        self.page.locator('#collection-status').filter(has_text='fewer than ten').wait_for()
        self.assertGreaterEqual(len(held), 1)
        for route in held: route.fulfill(json=self.server.market_data.collection_status('2026-10-15'))
        self.page.wait_for_timeout(30)
        self.assertIn('fewer than ten', self.page.locator('#collection-status').inner_text())
        self.read.assert_called_once()


class StaticTests(unittest.TestCase):
    def test_collection_has_no_provider_io_persistence_clocks_or_authority(self):
        source = (ROOT/'tradingbot_marketdata/collection.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertIn(node.module, ('contextlib','dataclasses','datetime','sys','threading','discovery','models','multiday'))
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ''
                self.assertNotIn(name, ('now','today','open','connect','request','execute','eval','exec','placeOrder','cancelOrder'))
        self.assertEqual(c.__all__, ())
        script = (ROOT/'tradingbot_dashboard/static/market-discovery.js').read_text(encoding='utf-8')
        for forbidden in ('setInterval','setTimeout','localStorage','sessionStorage','document.cookie','innerHTML','https://'):
            self.assertNotIn(forbidden, script)
        self.assertNotIn('/evaluation', script); self.assertNotIn('_collection_evaluation', script)

    def test_safety_snapshot_unchanged_and_no_source_secret_in_repr(self):
        payload = build_demo_snapshot().to_payload()
        self.assertEqual(payload['system']['trading_authority'], 'BLOCKED')
        self.assertEqual(payload['broker']['account_mode'], 'UNKNOWN')
        self.assertFalse(payload['system']['live_supported'])
        self.assertNotIn('collection', payload)
        self.assertNotIn(KEY, repr(c._GroupedCollection()))


class MemoryTests(Offline):
    def test_maximum_new_source_charge_fits_pre_request_reservation(self):
        source = d._decode_grouped(fixture(TARGET,30000), TARGET, NOW)
        self.assertLessEqual(c._charge(source,NOW), c._MAX_NEW_CHARGE)
        self.assertLessEqual(c._MAX_RETAINED_BYTES+c._DECODE_RESERVE+c._EVALUATION_RESERVE+c._PREVIEW_RESERVE,
                             c._MAX_WORKING_BYTES)

    def test_eleven_representative_12630_row_dates_include_decoding_and_evaluation_budget(self):
        gc.collect(); tracemalloc.start()
        try:
            self.read.side_effect = lambda path, key: (200, 'application/json', '', fixture(date.fromisoformat(path.split('/stocks/')[1].split('?')[0]),12630))
            for day in DAYS:
                result = self.load(day); self.assertTrue(result.get('retained'), result)
            status = self.snapshot()
            self.assertEqual(status['retained_rows'], 138930)
            self.assertLessEqual(status['charged_retained_bytes'], c._MAX_RETAINED_BYTES)
            current, collection_peak = tracemalloc.get_traced_memory()
            with self.preview._collection_evaluation(TARGET.isoformat()) as evidence:
                self.assertEqual(len(evidence.rows), 12630)
                self.assertEqual(evidence.rows[0].average_volume_10, Fraction(2000000))
                self.assertIs(evidence.qualification, m._SourceQualification.SESSION_CONTINUITY_UNVERIFIED)
            _, peak = tracemalloc.get_traced_memory()
            self.assertLess(current, status['charged_retained_bytes'])
            self.assertLess(collection_peak, c._MAX_WORKING_BYTES)
            self.assertLess(peak, c._MAX_WORKING_BYTES)
            print(f'PRE4B_SYNTHETIC_MEMORY retained={current} collection_peak={collection_peak} evaluation_peak={peak} charged={status["charged_retained_bytes"]}', flush=True)
        finally: tracemalloc.stop()
        self.assertEqual(self.read.call_count, 11); self.other.assert_not_called()


if __name__ == '__main__': unittest.main()
