"""Offline visual/dashboard contracts. Only an ephemeral loopback test server."""

import ast
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, replace
from http.client import HTTPConnection
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import socket
import threading
import unittest
from unittest.mock import Mock, patch

from tradingbot_dashboard import __main__ as cli
from tradingbot_dashboard.demo_data import build_demo_snapshot
from tradingbot_dashboard.server import HOST, PORT, _CSP, create_server
from tradingbot_dashboard.snapshot import SYMBOLS, TIMEFRAMES


ROOT = Path(__file__).resolve().parents[1] / 'tradingbot_dashboard'
STATIC = ROOT / 'static'


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = build_demo_snapshot()
        self.payload = self.snapshot.to_payload()

    def test_deterministic_snapshot_and_candles(self):
        other = build_demo_snapshot()
        self.assertEqual(self.snapshot, other)
        self.assertEqual(self.payload, other.to_payload())

    def test_frozen_slotted_snapshot_and_source_values(self):
        for value in (self.snapshot, self.snapshot.series[0], self.snapshot.series[0].candles[0],
                      self.snapshot.scanner[0], self.snapshot.trades[0]):
            self.assertFalse(hasattr(value, '__dict__'))
            with self.assertRaises((FrozenInstanceError, TypeError, AttributeError)):
                value.extra = 'changed'

    def test_payload_is_an_independent_primitive_copy(self):
        self.payload['chart']['series'][0]['candles'][0]['open'] = 99999
        self.payload['scanner']['rows'][0]['symbol'] = 'substituted'
        self.assertNotEqual(self.payload, self.snapshot.to_payload())
        encoded = json.dumps(self.snapshot.to_payload(), allow_nan=False)
        self.assertEqual(json.loads(encoded), self.snapshot.to_payload())

    def test_no_active_evidence_or_quantity_fabricated(self):
        evidence = self.payload['evidence']
        self.assertFalse(evidence['active'])
        self.assertEqual(evidence['decision'], 'No active evidence loaded')
        for name in ('historical_lifecycle', 'execution_lower_bound', 'possible_remaining_upper_bound'):
            self.assertIsNone(evidence[name])
        self.assertEqual(evidence['current_remainder'], 'UNKNOWN')
        self.assertEqual(evidence['current_position'], 'UNKNOWN')

    def test_architectural_state_not_fake_broker_health(self):
        self.assertEqual(self.payload['broker'], {'connection': 'NOT CONNECTED',
            'gateway': 'READ-ONLY REQUIRED', 'account_mode': 'UNKNOWN', 'snapshot_mode': None})
        self.assertEqual(self.payload['system']['health'], 'LOCAL UI READY')
        self.assertEqual(self.payload['system']['restart_quarantine'], 'MANDATORY')

    def test_strategy_is_context_only(self):
        strategy = self.payload['strategy']
        self.assertEqual(strategy['name'], 'ABCD BREAKOUT')
        self.assertEqual(strategy['direction'], 'LONG ONLY')
        self.assertEqual(strategy['timeframes'], ['1m', '15m'])
        self.assertEqual(strategy['context'], 'Weekly')
        self.assertEqual(strategy['automation'], 'NOT AUTHORIZED')

    def test_demo_flags_on_all_market_performance_and_trade_domains(self):
        self.assertIs(self.payload['system']['demo_market_data'], True)
        for name in ('scanner', 'chart', 'trades', 'performance'):
            self.assertIs(self.payload[name]['demo'], True)
        self.assertIs(self.payload['performance']['equity']['demo'], True)
        for row in (*self.payload['scanner']['rows'], *self.payload['trades']['rows'], *self.payload['chart']['series']):
            self.assertIs(row['demo'], True)

    def test_every_symbol_has_four_different_timeframe_series(self):
        self.assertEqual(self.payload['chart']['timeframes'], list(TIMEFRAMES))
        for symbol in SYMBOLS:
            selected = [s for s in self.snapshot.series if s.symbol == symbol]
            self.assertEqual(tuple(s.timeframe for s in selected), TIMEFRAMES)
            self.assertEqual(len({s.candles for s in selected}), 4)

    def test_all_ohlcv_values_and_ordering(self):
        for series in self.snapshot.series:
            times = []
            for candle in series.candles:
                with self.subTest(symbol=series.symbol, timeframe=series.timeframe, time=candle.time):
                    self.assertGreaterEqual(candle.high, max(candle.open, candle.close))
                    self.assertLessEqual(candle.low, min(candle.open, candle.close))
                    self.assertGreaterEqual(candle.volume, 0)
                    self.assertTrue(candle.time.endswith('+00:00'))
                    times.append(candle.time)
            self.assertEqual(times, sorted(set(times)))

    def test_scanner_schema_is_bounded_and_synthetic(self):
        self.assertEqual(tuple(row['symbol'] for row in self.payload['scanner']['rows']), SYMBOLS)
        expected = {'symbol', 'price', 'change_percent', 'volume', 'relative_volume', 'setup', 'score', 'status', 'demo'}
        for row in self.payload['scanner']['rows']:
            self.assertEqual(set(row), expected)
            self.assertIn(row['status'], ('WATCHING', 'FORMING', 'READY', 'WAITING'))

    def test_serialization_does_not_use_arbitrary_object_dumping(self):
        text = (ROOT / 'snapshot.py').read_text(encoding='utf-8')
        self.assertNotIn('__dict__', text)
        self.assertNotIn('asdict', text)
        self.assertNotIn('default=', (ROOT / 'server.py').read_text(encoding='utf-8'))

    def test_no_identity_or_secret_fields(self):
        encoded = json.dumps(self.payload)
        self.assertIsNone(re.search(r'\b[DU]\d{6,}\b', encoded))
        for forbidden in ('account_number', 'username', 'credential', 'api_key', 'token', 'webhook', 'password', 'secret'):
            self.assertNotIn(forbidden, encoded.lower())

    def test_snapshot_generation_needs_no_network_or_files(self):
        with ExitStack() as stack:
            for target in ('socket.create_connection', 'socket.getaddrinfo', 'builtins.open', 'pathlib.Path.read_bytes'):
                stack.enter_context(patch(target, side_effect=AssertionError(target)))
            self.assertEqual(build_demo_snapshot().to_payload(), self.payload)

    def test_mutated_snapshot_revalidated_before_serialization(self):
        object.__setattr__(self.snapshot, 'mode', 'LIVE')
        with self.assertRaises(ValueError):
            self.snapshot.to_payload()

    def test_candle_bad_times_prices_and_volume_rejected(self):
        candle = self.snapshot.series[0].candles[0]
        for field, value in (('time', 'not-a-time'), ('time', '2025-02-12T12:00:00'),
            ('open', float('nan')), ('close', float('inf')), ('high', 0), ('low', 999),
            ('volume', True), ('volume', -1)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                replace(candle, **{field: value}).validate()

    def test_duplicate_and_incomplete_series_rejected(self):
        for values in (self.snapshot.series[:-1], self.snapshot.series[:-1] + (self.snapshot.series[0],)):
            with self.assertRaises(ValueError): replace(self.snapshot, series=values)
        first = self.snapshot.series[0]
        invalid = replace(first, candles=(first.candles[0], first.candles[0]))
        with self.assertRaises(ValueError): replace(self.snapshot, series=(invalid, *self.snapshot.series[1:]))


def _unsafe_snapshot(field, value):
    def test(self):
        with self.assertRaises(ValueError): replace(self.snapshot, **{field: value})
    return test


for _field, _value in (('mode', 'LIVE'), ('trading_authority', 'ALLOWED'),
    ('gateway', 'CONNECTED'), ('account_mode', 'PAPER'), ('restart_quarantine', 'CLEARED'),
    ('live_supported', True), ('live_supported', 0), ('demo_market_data', False), ('demo_market_data', 1)):
    setattr(SnapshotTests, 'test_reject_unsafe_' + _field + '_' + str(_value), _unsafe_snapshot(_field, _value))


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        original = socket.socket.connect
        port = self.server.server_port
        def local_only(sock, address):
            if address != ('127.0.0.1', port): raise AssertionError('Non-test network forbidden')
            return original(sock, address)
        self.guard = patch.object(socket.socket, 'connect', local_only)
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def request(self, path='/', method='GET', headers=None):
        connection = HTTPConnection(HOST, self.server.server_port, timeout=3)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally: connection.close()

    def test_fixed_ipv4_loopback_binding(self):
        self.assertEqual(HOST, '127.0.0.1')
        self.assertEqual(PORT, 8765)
        self.assertEqual(self.server.server_address[0], HOST)

    def test_caller_cannot_choose_host_or_invalid_port(self):
        with self.assertRaises(TypeError): create_server(host='0.0.0.0')
        for port in (True, -1, 65536, '8765'):
            with self.subTest(port=port), self.assertRaises(ValueError): create_server(port=port)

    def test_server_bind_needs_no_hostname_or_dns_lookup(self):
        with patch('socket.getfqdn', side_effect=AssertionError('hostname lookup')):
            with create_server(port=0) as server:
                self.assertEqual(server.server_name, HOST)
                self.assertEqual(server.server_address[0], HOST)

    def test_root_returns_real_dashboard(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('text/html'))
        self.assertEqual(body, (STATIC / 'index.html').read_bytes())

    def test_json_is_only_sanitized_snapshot(self):
        status, headers, body = self.request('/api/snapshot')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('application/json'))
        self.assertEqual(json.loads(body), build_demo_snapshot().to_payload())

    def test_static_assets_with_correct_types_and_exact_bytes(self):
        for name, content_type in (('styles.css', 'text/css'), ('app.js', 'text/javascript'), ('favicon.svg', 'image/svg+xml')):
            with self.subTest(asset=name):
                status, headers, body = self.request('/' + name)
                self.assertEqual(status, 200)
                self.assertTrue(headers['Content-Type'].startswith(content_type))
                self.assertEqual(body, (STATIC / name).read_bytes())

    def test_head_has_same_headers_and_no_body(self):
        for path in ('/', '/app.js', '/api/snapshot', '/missing'):
            with self.subTest(path=path):
                get, head = self.request(path), self.request(path, 'HEAD')
                self.assertEqual(get[0], head[0])
                # Date may advance between the two independent HTTP requests.
                self.assertEqual({k: v for k, v in get[1].items() if k != 'Date'},
                                 {k: v for k, v in head[1].items() if k != 'Date'})
                self.assertEqual(head[2], b'')
                self.assertEqual(int(head[1]['Content-Length']), len(get[2]))

    def test_responses_are_deterministic(self):
        for path in ('/', '/styles.css', '/app.js', '/api/snapshot'):
            first, second = self.request(path), self.request(path)
            self.assertEqual(first[2], second[2])

    def test_security_headers_on_success_and_error(self):
        for path, method in (('/', 'GET'), ('/api/snapshot', 'GET'), ('/missing', 'GET'), ('/', 'POST')):
            with self.subTest(path=path, method=method):
                _, headers, _ = self.request(path, method)
                self.assertEqual(headers['Content-Security-Policy'], _CSP)
                self.assertIn("default-src 'none'", _CSP)
                self.assertIn("frame-ancestors 'none'", _CSP)
                self.assertNotIn('unsafe-inline', _CSP)
                self.assertNotIn('unsafe-eval', _CSP)
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
                self.assertEqual(headers['X-Frame-Options'], 'DENY')
                self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_foreign_host_rejected(self):
        for host in ('example.invalid', 'localhost', '0.0.0.0', '127.0.0.1.evil.invalid', '127.0.0.2'):
            with self.subTest(host=host):
                self.assertEqual(self.request(headers={'Host': host})[0], 403)

    def test_files_are_never_resolved_from_request(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('request-time filesystem')):
            self.assertEqual(self.request('/styles.css')[0], 200)
            self.assertEqual(self.request('/../../AGENTS.md')[0], 404)


def _reject_method(method):
    def test(self):
        for path in ('/', '/api/snapshot', '/app.js'):
            status, headers, body = self.request(path, method)
            self.assertEqual(status, 405)
            self.assertEqual(headers['Allow'], 'GET, HEAD')
            self.assertNotIn(b'Traceback', body)
    return test


for _method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT'):
    setattr(HTTPTests, 'test_reject_method_' + _method, _reject_method(_method))


def _reject_path(path):
    def test(self):
        status, _, body = self.request(path)
        self.assertEqual(status, 404)
        self.assertEqual(body, b'Not found.\n')
    return test


for _index, _path in enumerate(('/missing', '/static/', '/../AGENTS.md', '/%2e%2e/AGENTS.md',
    '/..%2f.env', '/%252e%252e/.env', '/..\\.env', '/server.py', '/api/order', '/api/config',
    '/api/snapshot?file=.env', '/api/snapshot/extra', '//etc/passwd', '/app.js/../server.py')):
    setattr(HTTPTests, 'test_reject_unknown_or_traversal_' + str(_index), _reject_path(_path))


class _Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.elements = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class StaticTests(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / 'index.html').read_text(encoding='utf-8')
        self.css = (STATIC / 'styles.css').read_text(encoding='utf-8')
        self.js = (STATIC / 'app.js').read_text(encoding='utf-8')
        self.page = _Page(self.html)

    def test_major_visual_regions(self):
        for text in ('VELOCITY', 'TRADING GROUP', 'Momentum Scanner', 'Bot Status', 'Order Evidence',
            'ABCD BREAKOUT', 'Recent Trades', 'Equity Curve', 'Activity Feed', 'READ-ONLY',
            'TRADING BLOCKED', 'DEMO MARKET DATA', 'HISTORICAL EVIDENCE ≠ CURRENT BROKER STATE'):
            self.assertIn(text, self.html)
        for identity in ('scanner', 'candlestick-chart', 'bot-health', 'evidence', 'strategy', 'trades', 'equity-chart'):
            self.assertTrue(any(attrs.get('id') == identity for _, attrs in self.page.elements))

    def test_timeframe_controls_present(self):
        self.assertEqual([attrs['data-timeframe'] for _, attrs in self.page.elements if 'data-timeframe' in attrs], list(TIMEFRAMES))

    def test_semantic_tables_and_accessible_canvases(self):
        self.assertEqual(sum(tag == 'table' for tag, _ in self.page.elements), 2)
        self.assertGreaterEqual(sum(tag == 'th' for tag, _ in self.page.elements), 15)
        self.assertEqual(sum(tag == 'canvas' and 'aria-label' in attrs for tag, attrs in self.page.elements), 2)
        self.assertIn('focus-visible', self.css)
        self.assertIn('prefers-reduced-motion', self.css)
        self.assertIn('Skip to workspace', self.html)

    def test_demo_label_on_each_metric_and_visual_domain(self):
        metrics = re.findall(r'<article class="metric">(.*?)</article>', self.html)
        self.assertEqual(len(metrics), 5)
        for metric in metrics:
            self.assertIn('class="demo-tag">DEMO', metric)
        self.assertIn('DEMO EQUITY CURVE', self.html)
        self.assertIn('Example executions only', self.html)
        self.assertIn('SYNTHETIC', self.js)

    def test_no_external_resource_urls(self):
        for asset in STATIC.iterdir():
            # The standard SVG namespace is an identifier, not a fetched resource.
            text = asset.read_text(encoding='utf-8').replace('http://www.w3.org/2000/svg', '')
            self.assertIsNone(re.search(r'(?:https?:)?//[A-Za-z0-9]', text), asset.name)
            self.assertNotIn('@import', text)
            self.assertNotIn('@font-face', text)
        for tag, attrs in self.page.elements:
            for key in ('src', 'href'):
                if key in attrs:
                    self.assertTrue(attrs[key].startswith(('/', '#')), (tag, key))

    def test_no_inline_script_style_or_event_handlers(self):
        self.assertNotIn('<style', self.html)
        for tag, attrs in self.page.elements:
            self.assertFalse(any(key.startswith('on') or key == 'style' for key in attrs), tag)
            if tag == 'script': self.assertEqual(attrs.get('src'), '/app.js')

    def test_no_trade_controls_forms_or_credential_inputs(self):
        for tag, _ in self.page.elements:
            self.assertNotIn(tag, ('form', 'input', 'textarea'))
        for action in ('placeOrder', 'cancelOrder', 'flatten', 'Enable Trading', 'Switch Live', 'Auto Trade On'):
            self.assertNotIn(action, self.html + self.js)

    def test_real_canvas_chart_has_data_selection_hover_resize_and_volume(self):
        for operation in ('getContext', 'fillRect', 'lineTo', 'pointermove', 'ResizeObserver', 'ArrowLeft',
                          'tooltip(', 'selectSymbol(', 'setTimeframe(', 'drawEquity(', 'candle.volume'):
            self.assertIn(operation, self.js)
        self.assertNotIn('Math.random', self.js)
        self.assertNotIn('setInterval', self.js)
        self.assertNotIn('innerHTML', self.js)

    def test_only_fixed_snapshot_fetch_no_mutation(self):
        self.assertEqual(self.js.count('fetch('), 1)
        self.assertIn("fetch('/api/snapshot'", self.js)
        self.assertNotIn('XMLHttpRequest', self.js)
        self.assertNotIn('WebSocket', self.js)
        self.assertNotIn('localStorage', self.js)

    def test_production_imports_and_calls_are_isolated(self):
        allowed = {'argparse', 'webbrowser', 'http.server', 'json', 'pathlib', 'dataclasses', 'datetime',
                   'math', 'snapshot', 'demo_data', 'server', 'socketserver'}
        forbidden_calls = {'eval', 'exec', 'compile', 'system', 'popen', 'subprocess', 'reqAllOpenOrders',
                           'reqExecutions', 'reqCompletedOrders', 'placeOrder', 'cancelOrder'}
        for path in ROOT.glob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for item in ast.walk(tree):
                if isinstance(item, ast.Import):
                    for alias in item.names: self.assertIn(alias.name, allowed)
                elif isinstance(item, ast.ImportFrom): self.assertIn(item.module, allowed)
                elif isinstance(item, ast.Call):
                    name = item.func.id if isinstance(item.func, ast.Name) else item.func.attr if isinstance(item.func, ast.Attribute) else ''
                    self.assertNotIn(name, forbidden_calls)
            self.assertNotIn('tradingbot_broker', path.read_text(encoding='utf-8'))
            self.assertNotIn('tradingbot_backtest', path.read_text(encoding='utf-8'))


class CLITests(unittest.TestCase):
    def run_cli(self, args=(), interruption=None):
        server = Mock()
        server.serve_forever.side_effect = interruption or KeyboardInterrupt()
        with patch.object(cli, 'create_server', return_value=server), patch.object(cli.webbrowser, 'open') as browser, patch('builtins.print'):
            result = cli.main(list(args))
        return result, server, browser

    def test_default_does_not_open_browser_and_closes_cleanly(self):
        result, server, browser = self.run_cli()
        self.assertEqual(result, 0)
        browser.assert_not_called()
        server.server_close.assert_called_once()

    def test_open_flag_uses_only_fixed_local_url(self):
        result, server, browser = self.run_cli(('--open',))
        self.assertEqual(result, 0)
        browser.assert_called_once_with('http://127.0.0.1:8765', new=2)
        server.server_close.assert_called_once()

    def test_browser_failure_does_not_prevent_read_only_server(self):
        server = Mock(); server.serve_forever.side_effect = KeyboardInterrupt()
        with patch.object(cli, 'create_server', return_value=server), patch.object(cli.webbrowser, 'open', side_effect=OSError), patch('builtins.print'):
            self.assertEqual(cli.main(['--open']), 0)
        server.server_close.assert_called_once()

    def test_port_in_use_is_concise_without_traceback(self):
        with patch.object(cli, 'create_server', side_effect=OSError), patch('sys.stderr') as error:
            with self.assertRaises(SystemExit) as caught: cli.main([])
        self.assertEqual(caught.exception.code, 1)
        self.assertIn('Cannot open local port', ''.join(call.args[0] for call in error.write.call_args_list))

    def test_no_host_or_mutation_cli_options(self):
        for args in (['--host', '0.0.0.0'], ['--enable-trading'], ['--live']):
            with patch('sys.stderr'), self.assertRaises(SystemExit): cli.main(args)

    def test_unexpected_failure_still_closes_server(self):
        server = Mock(); server.serve_forever.side_effect = RuntimeError('offline fixture')
        with patch.object(cli, 'create_server', return_value=server), patch('builtins.print'):
            with self.assertRaises(RuntimeError): cli.main([])
        server.server_close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
