"""Anonymous synthetic provider fixtures only; no real API credential or network.

Fixture prices are invented test values, not claimed historical AAPL prices.
"""

import ast
from contextlib import ExitStack
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext, Inexact, Rounded
from http.client import HTTPConnection
import json
from pathlib import Path
import shutil
import socket
import threading
import unittest
from unittest.mock import Mock, patch

from tradingbot_dashboard import __main__ as cli
from tradingbot_dashboard.demo_data import build_demo_snapshot
from tradingbot_dashboard.server import create_server, _CSP
from tradingbot_marketdata import massive, models, transport


ROOT = Path(__file__).resolve().parents[1]
PREVIOUS = (ROOT / 'tests/fixtures/massive_previous.json').read_bytes()
HISTORY = (ROOT / 'tests/fixtures/massive_history.json').read_bytes()
NOW = datetime(2026, 10, 19, 16, tzinfo=timezone.utc)
TEST_CREDENTIAL = 'SYNTHETIC_TEST_CREDENTIAL'


def changed(*, field=None, value=None, root=None):
    raw = json.loads(PREVIOUS)
    if field is not None: raw['results'][0][field] = value
    if root: raw.update(root)
    return json.dumps(raw).encode()


def decode(body=PREVIOUS, view='previous', now=NOW):
    return models._decode(body, 'AAPL', view, now, NOW.date() - timedelta(days=61), NOW.date() - timedelta(days=1))


def local_network_only(stack, port):
    connect, resolve = socket.socket.connect, socket.getaddrinfo
    def local_connect(sock, address):
        if address != ('127.0.0.1', port): raise AssertionError('Only test loopback allowed')
        return connect(sock, address)
    def local_resolve(host, *args, **kwargs):
        if host != '127.0.0.1': raise AssertionError('External DNS forbidden')
        return resolve(host, *args, **kwargs)
    stack.enter_context(patch.object(socket.socket, 'connect', local_connect))
    stack.enter_context(patch.object(socket, 'getaddrinfo', local_resolve))


class _Offline(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for target in ('socket.socket.connect', 'socket.getaddrinfo'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('Real network forbidden')))


class ModelTests(_Offline):
    def test_exact_source_values_and_dates(self):
        result = decode().payload(NOW)
        self.assertEqual(result['state'], 'REAL END-OF-DAY')
        self.assertEqual(result['provider'], 'Massive')
        bar = result['bars'][0]
        self.assertEqual(bar['session_date'], '2026-10-16')
        self.assertEqual(bar['source_timestamp_ms'], 1792123200000)
        self.assertEqual(bar['window_start_at'], '2026-10-16T04:00:00+00:00')
        self.assertEqual(bar['open'], '120.00')
        self.assertEqual(bar['volume'], '1000001.0')
        self.assertFalse(result['demo'])
        self.assertIs(result['adjusted_for_splits'], True)

    def test_frozen_slotted_values_and_independent_payload(self):
        series = decode(); bar = series.bars[0]
        self.assertFalse(hasattr(bar, '__dict__'))
        with self.assertRaises(FrozenInstanceError): bar.close = Decimal('1')
        result = series.payload(NOW); result['bars'][0]['close'] = '1'
        self.assertEqual(series.payload(NOW)['bars'][0]['close'], '121.00')

    def test_history_is_bounded_and_source_order_preserved(self):
        series = decode(HISTORY, 'history')
        self.assertEqual(len(series.bars), 3)
        self.assertEqual([b.session_date.isoformat() for b in series.bars],
                         ['2026-10-14', '2026-10-15', '2026-10-16'])

    def test_no_scanner_facts_or_currentness_invented(self):
        result = decode().payload(NOW)
        self.assertEqual(result['missing_scanner_fields'], list(models._MISSING_SCANNER))
        self.assertIn('RECENCY_IS_NOT_SESSION_COMPLETENESS', result['quality'])
        self.assertNotIn('score', result['bars'][0])
        self.assertIn('extended-hours', result['market_session'])

    def test_all_missing_prices_and_volume_remain_missing(self):
        for field, name in zip(('o', 'h', 'l', 'c', 'v'), models._FIELDS):
            result = decode(changed(field=field, value=None)).payload(NOW)
            self.assertEqual(result['state'], 'UNAVAILABLE')
            self.assertIn(name, result['missing_fields'])
            self.assertIsNone(result['bars'][0][name])

    def test_zero_volume_is_supplied_not_missing(self):
        result = decode(changed(field='v', value=0)).payload(NOW)
        self.assertEqual(result['bars'][0]['volume'], '0')
        self.assertEqual(result['missing_fields'], [])

    def test_missing_adjustment_and_false_adjustment_not_promoted(self):
        for value, reason in ((None, 'MISSING_SOURCE_FIELDS'), (False, 'ADJUSTMENT_MISMATCH')):
            result = decode(changed(root={'adjusted': value})).payload(NOW)
            self.assertEqual(result['state'], 'UNAVAILABLE')
            self.assertEqual(result['reason'], reason)
            self.assertIs(result['adjusted_for_splits'], value)

    def test_delayed_provider_status(self):
        result = decode(changed(root={'status': 'DELAYED'})).payload(NOW)
        self.assertEqual(result['state'], 'DELAYED')
        self.assertEqual(result['provider_status'], 'DELAYED')

    def test_current_session_not_promoted_to_eod(self):
        result = decode(now=datetime(2026, 10, 16, 15, tzinfo=timezone.utc)).payload(
            datetime(2026, 10, 16, 15, tzinfo=timezone.utc))
        self.assertEqual(result['state'], 'DELAYED')

    def test_stale_age_is_conservative_calendar_rule(self):
        result = decode().payload(NOW + timedelta(days=2))
        self.assertEqual(result['state'], 'STALE')
        self.assertEqual(result['age_calendar_days'], 5)

    def test_stale_age_does_not_hide_original_delayed_provider_flag(self):
        result=decode(changed(root={'status':'DELAYED'})).payload(NOW+timedelta(days=2))
        self.assertEqual(result['state'],'STALE')
        self.assertEqual(result['provider_status'],'DELAYED')

    def test_no_bar_absence_not_a_scanner_match(self):
        result = decode(changed(root={'results': [], 'resultsCount': 0})).payload(NOW)
        self.assertEqual(result['reason'], 'NO_BAR_AVAILABLE')
        self.assertEqual(result['bars'], [])

    def test_dst_transitions_use_exact_utc_boundaries_without_zoneinfo(self):
        for stamp, offset in (('2026-03-08T06:59:59+00:00', -5), ('2026-03-08T07:00:00+00:00', -4),
                              ('2026-11-01T05:59:59+00:00', -4), ('2026-11-01T06:00:00+00:00', -5)):
            self.assertEqual(models._eastern(datetime.fromisoformat(stamp)).utcoffset(), timedelta(hours=offset))

    def test_equivalent_timezone_instants_have_same_classification(self):
        self.assertEqual(decode().payload(NOW), decode().payload(NOW.astimezone(timezone(timedelta(hours=-5)))))

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError): decode(now=NOW.replace(tzinfo=None))

    def test_daily_timestamp_must_be_et_midnight_on_weekday(self):
        for stamp in (1792123200001, 1792137600000, 1792209600000):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError): decode(changed(field='t', value=stamp))

    def test_future_source_timestamp_rejected(self):
        with self.assertRaises(ValueError): decode(now=datetime(2026, 10, 15, 16, tzinfo=timezone.utc))

    def test_ohlc_contradictions_rejected(self):
        for field, value in (('h', 119), ('l', 122), ('c', 123), ('o', 118)):
            with self.subTest(field=field), self.assertRaises(ValueError): decode(changed(field=field, value=value))

    def test_history_duplicate_regression_future_page_or_outside_range_rejected(self):
        raw = json.loads(HISTORY)
        for rows in (raw['results'][::-1], [raw['results'][0]] * 3):
            with self.assertRaises(ValueError): decode(json.dumps({**raw, 'results': rows}).encode(), 'history')
        with self.assertRaises(ValueError): decode(changed(root={'next_url': 'https://example.invalid/secret'}))
        with self.assertRaises(ValueError):
            models._decode(HISTORY, 'AAPL', 'history', NOW, NOW.date(), NOW.date())

    def test_duplicate_keys_invalid_utf8_nan_trailing_and_oversized_rejected(self):
        for body in (b'{"ticker":"AAPL","ticker":"AAPL"}', b'\xff', PREVIOUS+b'x',
                     PREVIOUS.replace(b'120.00', b'NaN'), b' ' * 262145):
            with self.assertRaises((ValueError, UnicodeError)): decode(body)

    def test_decimal_shape_survives_hostile_context(self):
        with localcontext() as context:
            context.prec = 2; context.traps[Inexact] = context.traps[Rounded] = True
            result = decode().payload(NOW)
        self.assertEqual(result['bars'][0]['open'], '120.00')

    def test_unreviewed_response_fields_never_escape(self):
        result = decode(changed(root={'account': 'ANONYMOUS', 'message': TEST_CREDENTIAL,
                                      'arbitrary': {'Authorization': TEST_CREDENTIAL}})).payload(NOW)
        self.assertNotIn(TEST_CREDENTIAL, json.dumps(result))
        self.assertNotIn('ANONYMOUS', json.dumps(result))

    def test_root_identity_counts_quality_and_types_fail_closed(self):
        for fields in ({'ticker':'MSFT'}, {'ticker':'aapl'}, {'ticker':None}, {'status':'ERROR'},
                       {'status':None}, {'resultsCount':True}, {'resultsCount':-1}, {'resultsCount':2},
                       {'resultsCount':None}, {'adjusted':'true'}, {'results':None}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):decode(changed(root=fields))

    def test_no_count_or_no_timestamp_is_not_inferred(self):
        raw=json.loads(PREVIOUS);del raw['resultsCount']
        with self.assertRaises(ValueError):decode(json.dumps(raw).encode())
        raw=json.loads(PREVIOUS);del raw['results'][0]['t']
        with self.assertRaises(ValueError):decode(json.dumps(raw).encode())


def _bad_field(field, value):
    def test(self):
        with self.assertRaises((ValueError, TypeError)):
            decode(changed(field=field, value=value))
    return test


for _field in ('o', 'h', 'l', 'c', 'v'):
    for _label, _value in (('bool', True), ('string', '120'), ('negative', -1), ('huge', 1e30)):
        setattr(ModelTests, 'test_bad_' + _field + '_' + _label, _bad_field(_field, _value))
for _label, _value in (('bool', True), ('float', 1792123200000.5), ('string', '1792123200000'), ('missing', None)):
    setattr(ModelTests, 'test_bad_timestamp_' + _label, _bad_field('t', _value))


class AdapterTests(_Offline):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.dict('os.environ', {'MASSIVE_API_KEY': TEST_CREDENTIAL}))
        self.stack.enter_context(patch.object(massive, '_now', return_value=NOW))
        self.clock = self.stack.enter_context(patch.object(massive.time, 'monotonic', return_value=1000.0))
        self.stack.enter_context(patch.object(massive, '_RATE', massive._RateGate()))
        self.read = self.stack.enter_context(patch.object(massive, '_read_response',
            return_value=(200, 'application/json; charset=utf-8', '', PREVIOUS)))
        self.preview = massive._MarketDataPreview(enabled=True)

    def test_constructor_import_configuration_and_default_never_request(self):
        self.preview.configuration(); massive._MarketDataPreview().configuration()
        self.read.assert_not_called()
        with patch.object(massive.os.environ, 'get', side_effect=AssertionError('No key read in demo')):
            massive._MarketDataPreview().configuration()

    def test_default_disabled_even_with_key(self):
        result = massive._MarketDataPreview().observe('AAPL', 'previous')
        self.assertEqual(result['reason'], 'PREVIEW_DISABLED'); self.read.assert_not_called()

    def test_no_key_and_invalid_header_credentials_never_request(self):
        for value in ('', 'key\r\nInjected:yes', 'not valid', 'ü', 'x' * 513):
            with patch.dict('os.environ', {'MASSIVE_API_KEY': value}):
                preview = massive._MarketDataPreview(enabled=True)
                self.assertEqual(preview.observe('AAPL', 'previous')['reason'], 'KEY_NOT_CONFIGURED')
        self.read.assert_not_called()

    def test_exact_endpoint_and_safe_credential_route(self):
        result = self.preview.observe('AAPL', 'previous')
        self.assertEqual(result['state'], 'REAL END-OF-DAY')
        self.read.assert_called_once_with('/v2/aggs/ticker/AAPL/prev?adjusted=true', TEST_CREDENTIAL)
        self.assertNotIn(TEST_CREDENTIAL, json.dumps(result) + repr(self.preview))

    def test_history_fixed_daily_range_and_no_pagination(self):
        self.read.return_value = (200, 'application/json', '', HISTORY)
        result = self.preview.observe('AAPL', 'history')
        path = self.read.call_args.args[0]
        self.assertEqual(path, '/v2/aggs/ticker/AAPL/range/1/day/2026-08-19/2026-10-18?adjusted=true&sort=asc&limit=64')
        self.assertEqual(len(result['bars']), 3)

    def test_watchlist_is_strict_bounded_and_no_arbitrary_url(self):
        for values in ((), ('AAPL',)*2, ('AAPL','MSFT','NVDA','TSLA','AMD','META'), ('aapl',), ('https://evil.invalid',)):
            with self.assertRaises(ValueError): massive._MarketDataPreview(enabled=True, watchlist=values)
        for ticker in ('TSLA', '../AAPL', 'AAPL?apiKey=invalid', 'AAPL/prev', True, None):
            self.assertEqual(self.preview.observe(ticker, 'previous')['reason'], 'INVALID_REQUEST')
        self.read.assert_not_called()

    def test_cached_evidence_preserves_source_and_retrieval_date(self):
        first = self.preview.observe('AAPL', 'previous')
        self.clock.return_value += 20
        with patch.object(massive, '_now', return_value=NOW+timedelta(minutes=1)):
            second = self.preview.observe('AAPL', 'previous')
        self.assertEqual(first['bars'], second['bars']); self.assertTrue(second['cached'])
        self.assertEqual(first['retrieved_at'], second['retrieved_at'])
        self.assertNotEqual(first['evaluated_at'], second['evaluated_at'])
        self.assertEqual(self.read.call_count, 1)

    def test_expiry_requires_manual_request_and_cache_bounded(self):
        self.preview.observe('AAPL', 'previous'); self.clock.return_value += 301
        self.assertEqual(self.read.call_count, 1)
        self.preview.observe('AAPL', 'previous'); self.assertEqual(self.read.call_count, 2)
        self.assertLessEqual(len(self.preview._cache), 10)

    def test_rate_limit_shared_across_instances(self):
        self.preview.observe('AAPL', 'previous')
        peer = massive._MarketDataPreview(enabled=True)
        self.assertEqual(peer.observe('AAPL', 'previous')['reason'], 'RATE_LIMITED')
        self.clock.return_value += 15
        self.assertEqual(peer.observe('AAPL', 'previous')['state'], 'REAL END-OF-DAY')
        self.assertEqual(self.read.call_count, 2)

    def test_sliding_allowance_and_spacing(self):
        gate = massive._RateGate()
        for tick in (0., 15., 30., 45.): self.assertEqual(gate.reserve(tick), 0)
        self.assertGreater(gate.reserve(59.999), 0)
        self.assertEqual(gate.reserve(60.), 0)
        self.assertGreater(gate.reserve(60.), 0)
        self.assertGreater(gate.reserve(59.), 0)

    def test_429_manual_backoff_no_automatic_retry(self):
        self.read.return_value = (429, 'text/plain', '120', TEST_CREDENTIAL.encode())
        result = self.preview.observe('AAPL', 'previous')
        self.assertEqual(result['reason'], 'RATE_LIMITED'); self.assertEqual(result['retry_after_seconds'], 120)
        self.clock.return_value += 119
        self.preview.observe('AAPL', 'previous'); self.assertEqual(self.read.call_count, 1)

    def test_retry_after_date_and_malformed_header(self):
        self.assertEqual(massive._retry_delay('Mon, 19 Oct 2026 16:02:00 GMT', NOW), 120)
        for value in ('bad', '-1', '2', '', 'x'*129):
            self.assertEqual(massive._retry_delay(value, NOW), 60)

    def test_timeouts_network_errors_and_raw_exception_never_disclosed(self):
        for error, reason in ((TimeoutError(TEST_CREDENTIAL), 'TIMEOUT'),
                              (OSError(TEST_CREDENTIAL), 'NETWORK_UNAVAILABLE'),
                              (RuntimeError(TEST_CREDENTIAL), 'INVALID_RESPONSE')):
            self.stack.enter_context(patch.object(massive, '_RATE', massive._RateGate()))
            self.read.side_effect = error
            result = self.preview.observe('AAPL', 'previous')
            self.assertEqual(result['reason'], reason); self.assertNotIn(TEST_CREDENTIAL, json.dumps(result))

    def test_bad_content_type_or_payload_fails_closed(self):
        for mime, body in (('text/html', PREVIOUS), ('application/json', b'{}'),
                           ('application/json', b'\xff'), ('application/json', PREVIOUS+b'x')):
            self.stack.enter_context(patch.object(massive, '_RATE', massive._RateGate()))
            self.read.return_value = (200, mime, '', body)
            self.assertEqual(self.preview.observe('AAPL', 'previous')['reason'], 'INVALID_RESPONSE')

    def test_concurrent_load_does_not_wait_or_duplicate_provider_request(self):
        entered, release = threading.Event(), threading.Event()
        def pending(*args):
            entered.set(); release.wait(timeout=3)
            return (200, 'application/json', '', PREVIOUS)
        self.read.side_effect = pending
        thread = threading.Thread(target=self.preview.observe, args=('AAPL', 'previous'))
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=2))
            self.assertEqual(self.preview.observe('AAPL', 'history')['reason'], 'REQUEST_IN_PROGRESS')
            self.assertEqual(self.read.call_count, 1)
        finally: release.set(); thread.join(timeout=3)


def _http_error(code, reason):
    def test(self):
        self.read.return_value = (code, 'application/json', '', TEST_CREDENTIAL.encode())
        result = self.preview.observe('AAPL', 'previous')
        self.assertEqual(result['reason'], reason)
        self.assertEqual(result['bars'], [])
        self.assertNotIn(TEST_CREDENTIAL, json.dumps(result))
        self.read.assert_called_once()
    return test


for _code, _reason in ((301,'REDIRECT_REJECTED'),(302,'REDIRECT_REJECTED'),(401,'AUTHENTICATION_FAILED'),
    (403,'ACCESS_DENIED'),(404,'NOT_FOUND'),(429,'RATE_LIMITED'),(500,'PROVIDER_UNAVAILABLE'),(503,'PROVIDER_UNAVAILABLE')):
    setattr(AdapterTests, 'test_http_status_'+str(_code), _http_error(_code,_reason))


class TransportTests(_Offline):
    def test_fixed_https_host_timeout_verification_bearer_and_no_url_key(self):
        connection = Mock(); response = connection.getresponse.return_value
        response.status=200; response.getheader.side_effect=['application/json','']; response.read.return_value=PREVIOUS
        with patch.object(transport,'HTTPSConnection',return_value=connection) as factory:
            transport._read_response('/v2/aggs/ticker/AAPL/prev?adjusted=true', TEST_CREDENTIAL)
        self.assertEqual(factory.call_args.args, ('api.massive.com',))
        self.assertEqual(factory.call_args.kwargs['timeout'],8)
        self.assertTrue(factory.call_args.kwargs['context'].check_hostname)
        self.assertEqual(connection.request.call_args.args[0], 'GET')
        self.assertNotIn(TEST_CREDENTIAL, connection.request.call_args.args[1])
        self.assertEqual(connection.request.call_args.kwargs['headers']['Authorization'], 'Bearer '+TEST_CREDENTIAL)
        response.read.assert_called_once_with(262145); connection.close.assert_called_once()
        connection.set_debuglevel.assert_called_once_with(0)

    def test_overflow_body_rejected_and_connection_closed(self):
        connection=Mock(); connection.getresponse.return_value.read.return_value=b' ' * 262145
        with patch.object(transport,'HTTPSConnection',return_value=connection), self.assertRaises(ValueError):
            transport._read_response('/v2/aggs/ticker/AAPL/prev?adjusted=true', TEST_CREDENTIAL)
        connection.close.assert_called_once()


class PreviewHTTPTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict('os.environ',{'MASSIVE_API_KEY':TEST_CREDENTIAL}))
        self.stack.enter_context(patch.object(massive,'_now',return_value=NOW))
        self.stack.enter_context(patch.object(massive,'_RATE',massive._RateGate()))
        self.read=self.stack.enter_context(patch.object(massive,'_read_response',return_value=(200,'application/json','',PREVIOUS)))
        self.server=create_server(port=0,market_data_preview=True,watchlist=('AAPL','MSFT'))
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True);self.thread.start()
        self.addCleanup(self.stop_server)
        local_network_only(self.stack, self.server.server_port)

    def stop_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)

    def request(self,path,method='GET',headers=None):
        connection=HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            connection.request(method,path,headers=headers or {})
            response=connection.getresponse();return response.status,dict(response.getheaders()),response.read()
        finally:connection.close()

    def test_configuration_and_startup_never_request_and_no_key_disclosure(self):
        code,_,body=self.request('/api/market-data')
        self.assertEqual(code,200);self.assertTrue(json.loads(body)['configured'])
        self.assertNotIn(TEST_CREDENTIAL.encode(),body);self.read.assert_not_called()

    def test_explicit_local_load_returns_only_allowlisted_source(self):
        code,headers,body=self.request('/api/market-data/observation?ticker=AAPL&view=previous',headers={'X-Velocity-Market-Preview':'1'})
        self.assertEqual(code,200);self.assertEqual(json.loads(body)['state'],'REAL END-OF-DAY')
        self.assertEqual(headers['Content-Security-Policy'],_CSP)
        self.assertEqual(headers['Cache-Control'],'no-store');self.assertNotIn(TEST_CREDENTIAL.encode(),body)

    def test_driveby_origin_fetch_site_missing_custom_header_rejected(self):
        for headers in ({},{'X-Velocity-Market-Preview':'1','Origin':'https://example.invalid'},
            {'X-Velocity-Market-Preview':'1','Sec-Fetch-Site':'cross-site'},
            {'X-Velocity-Market-Preview':'1','Host':'example.invalid'}):
            code,_,_=self.request('/api/market-data/observation?ticker=AAPL&view=previous',headers=headers)
            self.assertEqual(code,403)
        self.read.assert_not_called()

    def test_head_and_mutating_methods_never_request(self):
        for method in ('HEAD','POST','PUT','PATCH','DELETE','OPTIONS'):
            code,headers,_=self.request('/api/market-data/observation?ticker=AAPL&view=previous',method,{'X-Velocity-Market-Preview':'1'})
            self.assertEqual(code,405)
            self.assertEqual(headers['Allow'],'GET')
        self.read.assert_not_called()

    def test_invalid_query_traversal_unknown_views_and_foreign_tickers(self):
        for query in ('','ticker=TSLA&view=previous','ticker=AAPL&view=quote','ticker=AAPL&ticker=MSFT',
            'ticker=AAPL&view=previous&url=bad','ticker=../AAPL&view=previous','ticker=AAPL',
            'ticker=AAPL&view=previous&extra=x'):
            code,_,_=self.request('/api/market-data/observation?'+query,headers={'X-Velocity-Market-Preview':'1'})
            self.assertEqual(code,400)
        self.assertEqual(self.request('/api/market-data/../server.py')[0],404)
        self.read.assert_not_called()

    def test_snapshot_and_safety_remain_exactly_demo(self):
        before=json.loads(self.request('/api/snapshot')[2])
        self.request('/api/market-data/observation?ticker=AAPL&view=previous',headers={'X-Velocity-Market-Preview':'1'})
        after=json.loads(self.request('/api/snapshot')[2])
        self.assertEqual(before,after);self.assertEqual(after,build_demo_snapshot().to_payload())


class PreviewStaticTests(unittest.TestCase):
    def test_provider_package_has_no_broker_strategy_storage_or_process_import(self):
        for path in (ROOT/'tradingbot_marketdata').glob('*.py'):
            tree=ast.parse(path.read_text())
            for item in ast.walk(tree):
                if isinstance(item,ast.Import):names=[n.name for n in item.names]
                elif isinstance(item,ast.ImportFrom):names=[item.module or '']
                else:continue
                self.assertFalse(any(n.startswith(('tradingbot_broker','tradingbot_backtest','strategy','sqlite3','subprocess')) for n in names))

    def test_separate_frontend_has_no_credential_provider_url_or_storage(self):
        text=(ROOT/'tradingbot_dashboard/static/market-data.js').read_text()
        self.assertNotIn('api.massive.com',text);self.assertNotIn('Bearer',text)
        for value in ('localStorage','sessionStorage','setInterval','innerHTML','WebSocket'):self.assertNotIn(value,text)
        self.assertIn('X-Velocity-Market-Preview',text)
        self.assertIn('REAL END-OF-DAY',text)
        self.assertIn('DELAYED',text);self.assertIn('STALE',text);self.assertIn('UNAVAILABLE',text)

    def test_preview_launch_flag_does_not_make_request_and_demo_unchanged(self):
        server=Mock();server.serve_forever.side_effect=KeyboardInterrupt()
        with patch.object(cli,'create_server',return_value=server) as factory, patch('builtins.print'):
            self.assertEqual(cli.main(['--market-data-preview','--watchlist','AAPL,MSFT']),0)
        factory.assert_called_once_with(market_data_preview=True,watchlist=('AAPL','MSFT'))

    def test_watchlist_without_explicit_optin_rejected(self):
        with patch('sys.stderr'),self.assertRaises(SystemExit):cli.main(['--watchlist','AAPL'])

    def test_mistyped_cli_arguments_never_echo_accidental_credentials(self):
        for args in (['--api-key',TEST_CREDENTIAL], ['--market-data-preview='+TEST_CREDENTIAL]):
            with patch('sys.stderr') as error,self.assertRaises(SystemExit):cli.main(args)
            printed=''.join(call.args[0] for call in error.write.call_args_list)
            self.assertNotIn(TEST_CREDENTIAL,printed)

    def test_invalid_watchlist_cli_failure_is_sanitized(self):
        with patch('sys.stderr') as error,self.assertRaises(SystemExit):
            cli.main(['--market-data-preview','--watchlist',TEST_CREDENTIAL])
        self.assertNotIn(TEST_CREDENTIAL,''.join(call.args[0] for call in error.write.call_args_list))


class PreviewBrowserTests(unittest.TestCase):
    """Installed browser only; provider is always a synthetic in-process fixture."""

    @classmethod
    def setUpClass(cls):
        executable=shutil.which('chromium') or shutil.which('chromium-browser')
        if not executable:raise unittest.SkipTest('Optional installed Chromium unavailable')
        try:from playwright.sync_api import sync_playwright
        except ImportError:raise unittest.SkipTest('Optional installed Playwright unavailable')
        cls.driver=sync_playwright().start();cls.addClassCleanup(cls.driver.stop)
        cls.browser=cls.driver.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox','--disable-background-networking'])
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict('os.environ',{'MASSIVE_API_KEY':TEST_CREDENTIAL}))
        self.stack.enter_context(patch.object(massive,'_now',return_value=NOW))
        self.stack.enter_context(patch.object(massive,'_RATE',massive._RateGate()))
        self.read=self.stack.enter_context(patch.object(massive,'_read_response',side_effect=lambda path,key:
            (200,'application/json','',HISTORY if '/range/' in path else PREVIOUS)))
        self.errors=[];self.external=[];self.browser_requests=[]

    def open_page(self,*,enabled=True):
        server=create_server(port=0,market_data_preview=enabled,watchlist=('AAPL','MSFT'))
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        def stop():server.shutdown();server.server_close();thread.join(timeout=3)
        self.addCleanup(stop)
        local_network_only(self.stack, server.server_port)
        origin=f'http://127.0.0.1:{server.server_port}'
        context=self.browser.new_context(viewport={'width':1440,'height':900},reduced_motion='reduce',service_workers='block')
        self.addCleanup(context.close)
        def local(route):
            self.browser_requests.append(route.request)
            if not route.request.url.startswith(origin+'/'):
                self.external.append(route.request.url);route.abort()
            else:route.continue_()
        context.route('**/*',local)
        self.page=context.new_page();self.page.on('pageerror',lambda e:self.errors.append(str(e)))
        self.page.on('console',lambda m:self.errors.append(m.text) if m.type=='error' else None)
        self.page.goto(origin+'/')
        self.page.locator('#health-label').filter(has_text='LOCAL UI READY').wait_for()
        self.page.locator('#market-ticker option[value="AAPL"]').wait_for(state='attached')
        return server

    def tearDown(self):
        self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])
        for request in self.browser_requests:
            self.assertNotIn('authorization',request.headers)
            self.assertNotIn(TEST_CREDENTIAL,request.url)
        if hasattr(self,'page'):self.assertNotIn(TEST_CREDENTIAL,self.page.content())

    def loaded(self,state='REAL END-OF-DAY',button='market-load-previous'):
        self.page.locator('#market-load-previous:not([disabled])').wait_for()
        self.page.locator('#'+button).click()
        self.page.locator('#market-state').filter(has_text=state).wait_for()
        self.page.locator('#market-load-previous:not([disabled])').wait_for()

    def test_default_demo_has_no_provider_calls_even_with_key(self):
        self.open_page(enabled=False)
        self.assertTrue(self.page.locator('#market-load-previous').is_disabled())
        self.assertEqual(self.page.locator('#market-state').inner_text(),'UNAVAILABLE')
        self.read.assert_not_called()
        self.assertEqual(self.page.locator('#scanner-counts').inner_text(),'13 MATCHES / 24 SCANNED')

    def test_enabled_without_key_has_clear_configuration_state(self):
        with patch.dict('os.environ',{'MASSIVE_API_KEY':''}):self.open_page()
        self.assertTrue(self.page.locator('#market-load-previous').is_disabled())
        self.assertIn('MASSIVE_API_KEY',self.page.locator('#market-message').inner_text())
        self.read.assert_not_called()

    def test_enabled_startup_is_still_offline_until_user_load(self):
        self.open_page()
        self.page.locator('#market-load-previous:not([disabled])').wait_for()
        self.read.assert_not_called()
        self.assertIn('No provider request has been made',self.page.locator('#market-message').inner_text())

    def test_explicit_eod_source_dates_missing_scanner_and_demo_separation(self):
        server=self.open_page();snapshot=server.snapshot_json
        self.loaded()
        self.assertEqual(self.read.call_count,1)
        self.assertIn('2026-10-16',self.page.locator('#market-source').inner_text())
        self.assertIn('2026-10-16T04:00:00+00:00',self.page.locator('#market-provenance').inner_text())
        self.assertIn('Market cap',self.page.locator('#market-missing').inner_text())
        self.assertEqual(server.snapshot_json,snapshot)
        self.assertEqual(self.page.locator('#chart-symbol').inner_text(),'ABCD')
        self.assertEqual(self.page.locator('#scanner-counts').inner_text(),'13 MATCHES / 24 SCANNED')
        self.assertFalse(self.page.locator('#real-history-wrap').is_visible())

    def test_history_has_separate_real_canvas_source_table_and_no_demo_markers(self):
        self.open_page();self.loaded(button='market-load-history')
        self.assertTrue(self.page.locator('#real-history-wrap').is_visible())
        self.assertEqual(self.page.locator('#real-history-rows tr').count(),3)
        self.page.locator('#real-history-wrap summary').click()
        self.assertIn('120.00',self.page.locator('#real-history-rows').inner_text())
        self.assertEqual(self.page.locator('[data-timeframe="1m"]').get_attribute('aria-pressed'),'true')
        self.assertTrue(self.page.locator('#candlestick-chart').is_visible())

    def test_selection_changes_no_automatic_provider_request(self):
        self.open_page();self.loaded()
        self.page.locator('#market-ticker').select_option('MSFT')
        self.assertFalse(self.page.locator('#market-observation').is_visible())
        self.assertEqual(self.read.call_count,1)
        self.assertEqual(self.page.locator('#market-state').inner_text(),'UNAVAILABLE')

    def test_missing_prices_are_unavailable_not_zero(self):
        self.read.side_effect=None
        self.read.return_value=(200,'application/json','',changed(field='o',value=None))
        self.open_page();self.loaded('UNAVAILABLE')
        self.assertIn('UNAVAILABLE',self.page.locator('#market-values').inner_text())
        self.assertIn('Missing source: open',self.page.locator('#market-missing').inner_text())

    def test_delayed_source_visibly_distinct(self):
        self.read.side_effect=None;self.read.return_value=(200,'application/json','',HISTORY.replace(b'"OK"',b'"DELAYED"'))
        self.open_page();self.loaded('DELAYED',button='market-load-history')
        self.assertIn('completed EOD status is not established',self.page.locator('#market-message').inner_text())
        self.assertTrue(self.page.locator('#real-history-wrap').is_visible())
        self.assertEqual(self.page.locator('#real-history-state').inner_text(),'DELAYED')

    def test_stale_source_visibly_distinct(self):
        with patch.object(massive,'_now',return_value=NOW+timedelta(days=2)):
            self.open_page();self.loaded('STALE')
        self.assertIn('not an exchange calendar',self.page.locator('#market-message').inner_text())

    def test_failure_clears_source_and_discloses_no_provider_body(self):
        self.read.side_effect=None;self.read.return_value=(403,'application/json','',TEST_CREDENTIAL.encode())
        self.open_page();self.loaded('UNAVAILABLE')
        self.assertFalse(self.page.locator('#market-observation').is_visible())
        self.assertIn('denied access',self.page.locator('#market-message').inner_text())

    def test_cache_repeated_manual_load_does_not_repeat_network(self):
        self.open_page();self.loaded();self.loaded()
        self.assertEqual(self.read.call_count,1)
        self.assertIn('CACHED SOURCE',self.page.locator('#market-source').inner_text())

    def test_rate_limited_explicit_second_view_no_automatic_retry(self):
        self.open_page();self.loaded()
        self.page.locator('#market-load-history').click()
        self.page.locator('#market-message').filter(has_text='Retry after at least').wait_for()
        self.assertEqual(self.read.call_count,1)
        self.assertFalse(self.page.locator('#real-history-wrap').is_visible())

    def test_preview_responsive_and_keyboard_operable(self):
        self.open_page();self.loaded(button='market-load-history')
        for width,height in ((1920,1080),(1440,900),(1024,768),(390,844)):
            self.page.set_viewport_size({'width':width,'height':height})
            self.assertTrue(self.page.evaluate('() => document.documentElement.scrollWidth <= innerWidth'))
        self.page.locator('#market-ticker').focus()
        self.assertTrue(self.page.locator('#market-ticker').evaluate('(e) => e===document.activeElement'))


if __name__=='__main__':unittest.main()
