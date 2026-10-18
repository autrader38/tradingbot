"""Synthetic scanner contracts; optional installed-browser interaction checks.

The application and required tests need only the standard library. Browser tests
use an already installed Playwright/Chromium when available, otherwise explicitly
skip. They never install anything, open the user's browser or request remote data.
"""

from dataclasses import FrozenInstanceError, replace
from html.parser import HTMLParser
from pathlib import Path
import shutil
import threading
import unittest

from tradingbot_dashboard.demo_data import build_demo_snapshot
from tradingbot_dashboard.server import create_server
from tradingbot_dashboard.snapshot import SYMBOLS, TIMEFRAMES, VOLATILITIES


STATIC = Path(__file__).resolve().parents[1] / 'tradingbot_dashboard' / 'static'
DEFAULTS = {'filter-max-price': '5.00', 'filter-min-change': '3.00',
    'filter-min-cap': '50', 'filter-min-average-volume': '1000000',
    'filter-min-relative-volume': '', 'filter-min-score': '0'}
DEFAULT_MATCHES = ('NOVA', 'IONX', 'QBIT', 'VOLT', 'ABCD', 'SOLA', 'VELA',
                   'RUNE', 'VYRA', 'KITE', 'CORA', 'LUMA', 'LYRA')


class _Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}
        self.labels = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs: self.ids[attrs['id']] = (tag, attrs)
        if tag == 'label' and 'for' in attrs: self.labels.add(attrs['for'])


class ScannerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = build_demo_snapshot()
        cls.html = (STATIC / 'index.html').read_text(encoding='utf-8')
        cls.js = (STATIC / 'app.js').read_text(encoding='utf-8')
        cls.elements = _Elements()
        cls.elements.feed(cls.html)

    def test_universe_has_24_unique_synthetic_symbols_including_originals(self):
        self.assertEqual(len(SYMBOLS), 24)
        self.assertEqual(len(set(SYMBOLS)), 24)
        self.assertEqual(SYMBOLS[:5], ('ABCD', 'VELA', 'NEXA', 'QBIT', 'MTRX'))

    def test_every_timeframe_closes_at_corresponding_scanner_price(self):
        prices = {row.symbol: row.price for row in self.snapshot.scanner}
        for series in self.snapshot.series:
            with self.subTest(symbol=series.symbol, timeframe=series.timeframe):
                self.assertEqual(series.candles[-1].close, prices[series.symbol])
        self.assertEqual(len(self.snapshot.series), len(SYMBOLS) * len(TIMEFRAMES))

    def test_price_disagreement_is_rejected_before_publication(self):
        row = replace(self.snapshot.scanner[0], price=123)
        with self.assertRaises(ValueError):
            replace(self.snapshot, scanner=(row, *self.snapshot.scanner[1:]))

    def test_new_source_fields_have_explicit_primitive_serialization(self):
        row = self.snapshot.scanner[0]
        payload = row.payload()
        for field in ('market_cap', 'average_volume_10d', 'volatility'):
            self.assertEqual(payload[field], getattr(row, field))
        self.assertIs(payload['demo'], True)
        self.assertFalse(hasattr(row, '__dict__'))
        with self.assertRaises(FrozenInstanceError): row.market_cap = 1

    def test_valid_integer_field_endpoints_and_volatility_categories(self):
        for field in ('market_cap', 'average_volume_10d'):
            for value in (0, 1, 1_000_000_000_000):
                replace(self.snapshot.scanner[0], **{field: value}).validate()
        for value in VOLATILITIES:
            replace(self.snapshot.scanner[0], volatility=value).validate()

    def test_payload_revalidates_tampered_new_fields(self):
        row = replace(self.snapshot.scanner[0])
        object.__setattr__(row, 'market_cap', 'unreviewed')
        with self.assertRaises(ValueError): row.payload()

    def test_default_controls_match_declared_scanner_policy(self):
        for identity, default in DEFAULTS.items():
            tag, attrs = self.elements.ids[identity]
            self.assertEqual(tag, 'input')
            self.assertEqual(attrs['type'], 'number')
            self.assertEqual(attrs.get('value', ''), default)
            self.assertIn(identity, self.elements.labels)
            self.assertIn('min', attrs)
            self.assertIn('max', attrs)
        for identity in ('volatility-high', 'volatility-very-high'):
            self.assertIn('checked', self.elements.ids[identity][1])

    def test_explicit_demo_and_strategy_separation(self):
        for phrase in ('DEMO MARKET DATA — NO LIVE FEED', 'not trading rules',
                       'not authorization or recommendations', 'Synthetic volatility'):
            self.assertIn(phrase, self.html)
        self.assertIn('Frozen Strategy Spec v1.0 is unchanged', self.html)

    def test_accessible_filter_search_sort_and_empty_regions(self):
        for identity in ('filter-max-price', 'filter-min-change', 'filter-min-cap',
                         'filter-min-average-volume', 'filter-min-relative-volume',
                         'filter-min-score', 'scanner-search', 'scanner-sort'):
            self.assertIn(identity, self.elements.labels)
        self.assertEqual(self.elements.ids['filter-toggle'][1]['aria-controls'], 'filter-controls')
        for identity in ('filter-feedback', 'scanner-counts', 'chart-selection-note'):
            self.assertEqual(self.elements.ids[identity][1]['role'], 'status')
        self.assertIn('hidden', self.elements.ids['scanner-empty'][1])

    def test_no_persistence_or_new_transport_in_browser(self):
        for forbidden in ('localStorage', 'sessionStorage', 'indexedDB', 'document.cookie',
                          'XMLHttpRequest', 'WebSocket', 'sendBeacon', 'innerHTML', 'eval('):
            self.assertNotIn(forbidden, self.js)
        self.assertEqual(self.js.count('fetch('), 1)
        self.assertIn("fetch('/api/snapshot'", self.js)

    def test_all_safety_and_empty_evidence_fields_unchanged(self):
        payload = self.snapshot.to_payload()
        self.assertEqual(payload['system'], {'brand': 'VELOCITY TRADING GROUP', 'mode': 'PAPER',
            'trading_authority': 'BLOCKED', 'read_only': True, 'live_supported': False,
            'demo_market_data': True, 'health': 'LOCAL UI READY', 'restart_quarantine': 'MANDATORY'})
        self.assertEqual(payload['broker'], {'connection': 'NOT CONNECTED',
            'gateway': 'READ-ONLY REQUIRED', 'account_mode': 'UNKNOWN', 'snapshot_mode': None})
        self.assertFalse(payload['evidence']['active'])
        self.assertEqual(payload['evidence']['current_remainder'], 'UNKNOWN')
        self.assertEqual(payload['evidence']['current_position'], 'UNKNOWN')


def _invalid_field(field, value):
    def test(self):
        with self.assertRaises(ValueError):
            replace(self.snapshot.scanner[0], **{field: value}).validate()
    return test


for _field in ('market_cap', 'average_volume_10d'):
    for _label, _value in (('bool', True), ('negative', -1), ('float', 1.5),
        ('oversized', 1_000_000_000_001), ('nan', float('nan')), ('infinity', float('inf')),
        ('string', '1000000'), ('none', None)):
        setattr(ScannerContractTests, 'test_invalid_' + _field + '_' + _label, _invalid_field(_field, _value))
for _index, _value in enumerate(('', 'Low', 'high', None, True, ['High'])):
    setattr(ScannerContractTests, 'test_invalid_volatility_' + str(_index), _invalid_field('volatility', _value))


class ScannerBrowserTests(unittest.TestCase):
    """Real DOM/Canvas interactions, only if a browser is already available."""

    @classmethod
    def setUpClass(cls):
        executable = shutil.which('chromium') or shutil.which('chromium-browser')
        if not executable:
            raise unittest.SkipTest('Optional installed Chromium unavailable; manual browser validation required')
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional installed Playwright unavailable; manual browser validation required')
        cls.driver = sync_playwright().start()
        cls.addClassCleanup(cls.driver.stop)
        cls.browser = cls.driver.chromium.launch(executable_path=executable, headless=True,
            args=['--no-sandbox', '--disable-background-networking', '--disable-component-update'])
        cls.addClassCleanup(cls.browser.close)
        cls.server = create_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
            kwargs={'poll_interval': .01}, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.stop_server)
        cls.origin = f'http://127.0.0.1:{cls.server.server_port}'
        cls.rows = {r.symbol: r.payload() for r in build_demo_snapshot().scanner}

    @classmethod
    def stop_server(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        self.errors, self.forbidden_requests, self.requests = [], [], []
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 900},
            service_workers='block', reduced_motion='reduce')
        self.addCleanup(self.context.close)
        def local_only(route):
            request = route.request
            self.requests.append((request.method, request.url))
            if not request.url.startswith(self.origin + '/') or request.method not in ('GET', 'HEAD'):
                self.forbidden_requests.append((request.method, request.url))
                route.abort()
            else: route.continue_()
        self.context.route('**/*', local_only)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('console', lambda message: self.errors.append(message.text) if message.type == 'error' else None)
        self.page.goto(self.origin + '/')
        # A locator wait honors CSP; string-predicate polling can require eval.
        self.page.locator('#health-label').filter(has_text='LOCAL UI READY').wait_for()

    def tearDown(self):
        self.assertEqual(self.errors, [])
        self.assertEqual(self.forbidden_requests, [])
        self.assertFalse(self.page.locator('#load-error').is_visible())

    def symbols(self):
        return self.page.locator('#scanner-rows tr').evaluate_all('(rows) => rows.map(row => row.dataset.symbol)')

    def set_values(self, **values):
        self.page.evaluate('''(values) => {
          for (const [id, value] of Object.entries(values)) {
            const input = document.getElementById(id); input.value = value;
            input.dispatchEvent(new Event('input', {bubbles: true}));
          }
        }''', values)

    def relaxed(self):
        self.set_values(**{'filter-max-price': '10000', 'filter-min-change': '-100',
            'filter-min-cap': '0', 'filter-min-average-volume': '0',
            'filter-min-relative-volume': '', 'filter-min-score': '0'})

    def search(self, value):
        self.page.locator('#scanner-search').fill(value)

    def test_defaults_actual_counts_and_initial_selection(self):
        self.assertEqual(tuple(self.symbols()), DEFAULT_MATCHES)
        self.assertEqual(self.page.locator('#scanner-counts').inner_text(), '13 MATCHES / 24 SCANNED')
        self.assertEqual(self.page.locator('#scanner-filtered').inner_text(), '11 filtered out')
        self.assertEqual(self.page.locator('#chart-symbol').inner_text(), 'ABCD')
        for identity, value in DEFAULTS.items():
            self.assertEqual(self.page.locator('#' + identity).input_value(), value)

    def test_strict_price_boundary(self):
        self.relaxed(); self.set_values(**{'filter-max-price': '5'})
        self.search('QBIT'); self.assertEqual(self.symbols(), ['QBIT'])
        self.search('MTRX'); self.assertEqual(self.symbols(), [])

    def test_inclusive_change_boundary(self):
        self.relaxed(); self.set_values(**{'filter-min-change': '3'})
        self.search('AERO'); self.assertEqual(self.symbols(), [])
        self.search('LUMA'); self.assertEqual(self.symbols(), ['LUMA'])

    def test_inclusive_market_cap_boundary(self):
        self.relaxed(); self.set_values(**{'filter-min-cap': '50'})
        self.search('FLUX'); self.assertEqual(self.symbols(), [])
        self.search('LUMA'); self.assertEqual(self.symbols(), ['LUMA'])

    def test_strict_average_volume_boundary(self):
        self.relaxed(); self.set_values(**{'filter-min-average-volume': '1000000'})
        self.search('ORBI'); self.assertEqual(self.symbols(), [])
        self.search('LUMA'); self.assertEqual(self.symbols(), ['LUMA'])

    def test_all_volatility_combinations_including_empty(self):
        for high, very_high in ((True, True), (True, False), (False, True), (False, False)):
            with self.subTest(high=high, very_high=very_high):
                self.page.locator('#volatility-high').set_checked(high)
                self.page.locator('#volatility-very-high').set_checked(very_high)
                expected = [s for s in DEFAULT_MATCHES if
                    (high and self.rows[s]['volatility'] == 'High') or
                    (very_high and self.rows[s]['volatility'] == 'Very High')]
                self.assertEqual(self.symbols(), expected)
        self.assertIn('Select High and/or Very High', self.page.locator('#scanner-empty-reason').inner_text())

    def test_relative_volume_and_score_inclusive_and_filter_and_logic(self):
        self.set_values(**{'filter-min-relative-volume': '4.8', 'filter-min-score': '94'})
        self.assertEqual(self.symbols(), ['NOVA', 'ABCD'])
        self.search('ab'); self.assertEqual(self.symbols(), ['ABCD'])
        self.set_values(**{'filter-min-relative-volume': '4.81'})
        self.assertEqual(self.symbols(), [])

    def test_case_insensitive_search_clear_and_filter_intersection(self):
        self.search('vElA'); self.assertEqual(self.symbols(), ['VELA'])
        self.page.locator('#clear-search').click()
        self.assertEqual(tuple(self.symbols()), DEFAULT_MATCHES)
        self.assertTrue(self.page.locator('#clear-search').is_disabled())
        self.search('MTRX'); self.assertEqual(self.symbols(), [])
        self.set_values(**{'filter-max-price': '5.01'})
        self.assertEqual(self.symbols(), ['MTRX'])

    def test_search_text_is_not_executable_or_regular_expression(self):
        for text in ('<img>', '.*', '[AB]', 'NO-SYMBOL'):
            self.search(text)
            self.assertEqual(self.symbols(), [])
        self.assertEqual(self.page.locator('#scanner-rows img').count(), 0)

    def test_all_sort_modes_and_deterministic_symbol_ties(self):
        fields = {'change-desc': ('change_percent', -1), 'price-asc': ('price', 1),
            'price-desc': ('price', -1), 'relative-desc': ('relative_volume', -1),
            'volume-desc': ('volume', -1), 'score-desc': ('score', -1)}
        for mode, (field, direction) in fields.items():
            self.page.locator('#scanner-sort').select_option(mode)
            expected = sorted(DEFAULT_MATCHES, key=lambda s: (direction * self.rows[s][field], s))
            self.assertEqual(self.symbols(), expected)
        self.page.locator('#scanner-sort').select_option('change-desc')
        self.assertEqual(self.symbols()[-2:], ['LUMA', 'LYRA'])

    def test_reset_restores_every_default_search_sort_and_volatility(self):
        self.set_values(**{'filter-max-price': '2', 'filter-min-score': '90'})
        self.search('NOVA')
        self.page.locator('#scanner-sort').select_option('price-desc')
        self.page.locator('#volatility-high').uncheck()
        self.page.locator('#reset-filters').click()
        for identity, value in DEFAULTS.items():
            self.assertEqual(self.page.locator('#' + identity).input_value(), value)
        self.assertEqual(self.page.locator('#scanner-search').input_value(), '')
        self.assertEqual(self.page.locator('#scanner-sort').input_value(), 'change-desc')
        self.assertTrue(self.page.locator('#volatility-high').is_checked())
        self.assertTrue(self.page.locator('#volatility-very-high').is_checked())
        self.assertEqual(tuple(self.symbols()), DEFAULT_MATCHES)

    def test_loosen_updates_visible_values_and_preserves_search_sort(self):
        self.set_values(**{'filter-min-relative-volume': '3', 'filter-min-score': '70'})
        self.search('MTRX'); self.page.locator('#scanner-sort').select_option('price-asc')
        self.page.locator('#loosen-filters').click()
        expected = dict(zip(DEFAULTS, ('7.50', '2.00', '40.00', '750000', '2.50', '60')))
        for identity, value in expected.items():
            self.assertEqual(self.page.locator('#' + identity).input_value(), value)
        self.assertEqual(self.symbols(), ['MTRX'])
        self.assertEqual(self.page.locator('#scanner-search').input_value(), 'MTRX')
        self.assertEqual(self.page.locator('#scanner-sort').input_value(), 'price-asc')
        self.assertIn('Search and sort unchanged', self.page.locator('#filter-feedback').inner_text())

    def test_loosen_clamps_to_bounds_and_keeps_optional_minimum_blank(self):
        self.set_values(**{'filter-max-price': '10000', 'filter-min-change': '-100',
            'filter-min-cap': '0', 'filter-min-average-volume': '0'})
        self.page.locator('#loosen-filters').click()
        for identity, value in (('filter-max-price', 10000), ('filter-min-change', -100),
            ('filter-min-cap', 0), ('filter-min-average-volume', 0), ('filter-min-score', 0)):
            self.assertEqual(float(self.page.locator('#' + identity).input_value()), value)
        self.assertEqual(self.page.locator('#filter-min-relative-volume').input_value(), '')

    def test_invalid_numeric_values_fail_visibly_without_stale_results(self):
        invalid = {'filter-max-price': ('', '0', '10000.01', '1e2', '4.999'),
            'filter-min-change': ('-100.01', '1000.01'), 'filter-min-cap': ('-1', '1000000.01'),
            'filter-min-average-volume': ('-1', '1000000000001', '1.5'),
            'filter-min-relative-volume': ('-1', '100.01'), 'filter-min-score': ('-1', '101', '1.5')}
        for identity, values in invalid.items():
            for value in values:
                with self.subTest(identity=identity, value=value):
                    self.page.locator('#reset-filters').click()
                    self.set_values(**{identity: value})
                    self.assertEqual(self.symbols(), [])
                    self.assertEqual(self.page.locator('#' + identity).get_attribute('aria-invalid'), 'true')
                    self.assertIn('Correct the invalid filters', self.page.locator('#scanner-empty-reason').inner_text())
                    self.page.locator('#loosen-filters').click()
                    self.assertEqual(self.symbols(), [])

    def test_nan_infinity_and_nonnumeric_input_cannot_create_matches(self):
        for value in ('NaN', 'Infinity', 'not-a-number'):
            # HTML number inputs sanitize these to empty, which remains invalid.
            self.set_values(**{'filter-max-price': value})
            self.assertEqual(self.symbols(), [])
            self.assertEqual(self.page.locator('#filter-max-price').get_attribute('aria-invalid'), 'true')

    def test_optional_relative_volume_distinguishes_empty_from_bad_number(self):
        relative = self.page.locator('#filter-min-relative-volume')
        relative.press_sequentially('e')
        self.assertTrue(relative.evaluate('(e) => e.validity.badInput'))
        self.assertEqual(relative.input_value(), '')
        self.assertEqual(relative.get_attribute('aria-invalid'), 'true')
        self.assertEqual(self.symbols(), [])
        relative.fill('')
        self.assertEqual(relative.get_attribute('aria-invalid'), 'false')
        self.assertEqual(tuple(self.symbols()), DEFAULT_MATCHES)

    def test_score_slider_and_number_stay_synchronized(self):
        self.page.locator('#filter-score-slider').fill('91')
        self.assertEqual(self.page.locator('#filter-min-score').input_value(), '91')
        self.assertEqual(self.symbols(), ['NOVA', 'IONX', 'QBIT', 'ABCD'])
        self.set_values(**{'filter-min-score': '85'})
        self.assertEqual(self.page.locator('#filter-score-slider').input_value(), '85')

    def test_selection_removed_by_filters_switches_to_first_match(self):
        self.set_values(**{'filter-max-price': '2'})
        self.assertEqual(self.symbols(), ['NOVA', 'SOLA'])
        self.assertEqual(self.page.locator('#chart-symbol').inner_text(), 'NOVA')
        self.assertIn('ABCD no longer matches', self.page.locator('#scanner-announcement').inner_text())

    def test_zero_results_keep_chart_stable_and_outside_notice_visible(self):
        before = self.page.locator('#chart-symbol').inner_text()
        self.search('NOT-FOUND')
        self.assertEqual(self.symbols(), [])
        self.assertTrue(self.page.locator('#scanner-empty').is_visible())
        self.assertEqual(self.page.locator('#scanner-counts').inner_text(), '0 MATCHES / 24 SCANNED')
        self.assertEqual(self.page.locator('#chart-symbol').inner_text(), before)
        self.assertIn('Outside filtered results', self.page.locator('#chart-selection-note').inner_text())
        self.page.locator('[data-timeframe="1W"]').click()
        self.assertIn('1W', self.page.locator('#chart-context').inner_text())

    def test_every_matching_symbol_can_load_chart_and_all_timeframes(self):
        self.relaxed()
        self.assertEqual(len(self.symbols()), 24)
        for symbol in SYMBOLS:
            self.page.get_by_role('button', name=f'Show {symbol} demo chart', exact=True).click()
            self.assertEqual(self.page.locator('#chart-symbol').inner_text(), symbol)
            self.assertEqual(float(self.page.locator('#chart-price').inner_text()), self.rows[symbol]['price'])
        for timeframe in TIMEFRAMES:
            self.page.locator(f'[data-timeframe="{timeframe}"]').click()
            self.assertIn(timeframe, self.page.locator('#chart-context').inner_text())
            self.assertEqual(float(self.page.locator('#chart-price').inner_text()), self.rows['RUNE']['price'])

    def test_market_profile_has_exact_cap_average_and_synthetic_volatility(self):
        self.search('LUMA')
        self.page.get_by_label('LUMA market cap, average volume and volatility').click()
        details = self.page.locator('#scanner-rows details')
        self.assertIn('$50,000,000', details.inner_text())
        self.assertIn('1,000,001 shares', details.inner_text())
        self.assertIn('High · SYNTHETIC', details.inner_text())

    def test_trade_can_view_outside_results_without_claiming_scanner_match(self):
        self.page.get_by_role('button', name='Explore MTRX demo trade markers', exact=True).click()
        self.assertEqual(self.page.locator('#chart-symbol').inner_text(), 'MTRX')
        self.assertNotIn('MTRX', self.symbols())
        self.assertIn('Outside filtered results', self.page.locator('#chart-selection-note').inner_text())

    def test_pre1_chart_hover_keyboard_overlay_zoom_and_timeframes_preserved(self):
        canvas = self.page.locator('#candlestick-chart')
        canvas.focus(); canvas.press('ArrowLeft')
        self.assertTrue(self.page.locator('#chart-tooltip').is_visible())
        self.assertIn('DEMO', self.page.locator('#ohlc-readout').inner_text())
        canvas.press('Escape')
        self.assertFalse(self.page.locator('#chart-tooltip').is_visible())
        before = canvas.screenshot()
        self.page.locator('#vwap-toggle').click()
        self.assertEqual(self.page.locator('#vwap-toggle').get_attribute('aria-pressed'), 'false')
        self.assertNotEqual(canvas.screenshot(), before)
        self.page.locator('#levels-toggle').click()
        self.assertEqual(self.page.locator('#levels-toggle').get_attribute('aria-pressed'), 'false')
        self.page.locator('#zoom-in').click(); self.page.locator('#zoom-out').click()
        self.page.locator('#reset-chart').click()
        pictures = []
        for timeframe in TIMEFRAMES:
            self.page.locator(f'[data-timeframe="{timeframe}"]').click()
            pictures.append(canvas.screenshot())
        self.assertEqual(len(set(pictures)), 4)

    def test_filter_collapse_preserves_values_and_keyboard_access(self):
        self.page.locator('#filter-max-price').focus()
        self.assertEqual(self.page.locator('#filter-max-price').evaluate('(e)=>e === document.activeElement'), True)
        self.page.locator('#filter-toggle').click()
        self.assertFalse(self.page.locator('#filter-controls').is_visible())
        self.assertEqual(self.page.locator('#filter-toggle').get_attribute('aria-expanded'), 'false')
        self.page.locator('#filter-toggle').press('Enter')
        self.assertTrue(self.page.locator('#filter-controls').is_visible())
        self.assertEqual(tuple(self.symbols()), DEFAULT_MATCHES)

    def test_responsive_layout_does_not_overflow_or_lose_chart(self):
        for width, height in ((1920, 1080), (1440, 900), (1024, 768), (390, 844)):
            self.page.set_viewport_size({'width': width, 'height': height})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            self.assertGreater(self.page.locator('#candlestick-chart').bounding_box()['width'], 250)
            self.assertTrue(self.page.locator('#filter-max-price').is_visible())

    def test_filters_issue_no_http_requests_or_safety_changes(self):
        before = self.page.locator('#bot-health').inner_text(), self.page.locator('#evidence').inner_text()
        request_count = len(self.requests)
        self.search('VELA'); self.page.locator('#loosen-filters').click()
        self.page.locator('#reset-filters').click()
        self.page.locator('#scanner-sort').select_option('score-desc')
        self.assertEqual(len(self.requests), request_count)
        self.assertEqual(before, (self.page.locator('#bot-health').inner_text(), self.page.locator('#evidence').inner_text()))
        self.assertEqual(self.page.evaluate('localStorage.length'), 0)
        self.assertEqual(self.context.cookies(), [])


if __name__ == '__main__':
    unittest.main()
