"""Offline, anonymous grouped-response rejection stages; no provider requests."""

import ast
from contextlib import ExitStack
from datetime import date, datetime, timezone
import json
import re
import unittest
from unittest.mock import Mock, patch

from tradingbot_dashboard.demo_data import build_demo_snapshot
from tradingbot_marketdata import discovery as d, massive, transport
from tests.test_velocity_market_discovery import FIXTURE, NOW, DAY, KEY, ROOT, body, decode


def row_body(**fields):
    row = json.loads(FIXTURE)['results'][0]
    row.update(fields)
    return body([row])


class Offline(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target in ('socket.socket.connect', 'socket.getaddrinfo'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('No external network')))


class ParserDiagnostics(Offline):
    def test_documented_rows_with_declared_string_request_id_remain_accepted(self):
        # Official example's rows, without treating its request_id schema-object
        # illustration as a real response. Documentation says request_id:string.
        rows = [dict(T='KIMpL', c=25.9102, h=26.25, l=25.91, n=74, o=26.07,
                     t=1602705600000, v=4369, vw=26.0407),
                dict(T='TANH', c=23.4, h=24.763, l=22.65, n=1096, o=24.5,
                     t=1602705600000, v=25933.6, vw=23.493),
                dict(T='VSAT', c=34.24, h=35.47, l=34.21, n=4966, o=34.9,
                     t=1602705600000, v=312583, vw=34.4736)]
        result = decode(body(rows), date(2020, 10, 14), datetime(2020, 10, 16, 16, tzinfo=timezone.utc))
        self.assertEqual(result.received_count, 3)
        self.assertEqual(result.excluded_unsupported, 1)
        self.assertEqual(tuple(row.ticker for row in result.rows), ('TANH', 'VSAT'))

    def test_supported_format_exclusions_are_not_malformed_identity_rejections(self):
        result = decode(row_body(T='KIMpL'))
        self.assertEqual((result.excluded_unsupported, result.received_count), (1, 1))
        self.assertEqual(result.rows, ())

    def test_otc_rows_still_must_pass_all_other_validation(self):
        with self.assertRaises(d._GroupedValidationError) as caught:
            decode(row_body(otc=True, c=0))
        self.assertEqual(d._grouped_diagnostic(caught.exception), 'ROW_OHLCV_INVALID')

    def test_lf_and_crlf_truncation_remain_syntax_errors(self):
        for fixture in (FIXTURE.replace(b'\r\n', b'\n'), FIXTURE.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')):
            complete = fixture.rstrip()
            self.assertTrue(complete.endswith(b'}'))
            with self.assertRaises(d._GroupedValidationError) as caught:
                decode(complete[:-1])
            self.assertEqual(d._grouped_diagnostic(caught.exception), 'JSON_SYNTAX_INVALID')


def parser_case(make_body, expected):
    def test(self):
        with self.assertRaises(d._GroupedValidationError) as caught:
            decode(make_body())
        self.assertEqual(d._grouped_diagnostic(caught.exception), expected)
        self.assertEqual(str(caught.exception), 'Grouped response failed validation')
        self.assertNotIn(KEY, repr(caught.exception))
    return test


for name, factory, expected in (
    ('utf8', lambda: b'\xff', 'JSON_SYNTAX_INVALID'),
    ('trailing', lambda: FIXTURE + b'x', 'JSON_SYNTAX_INVALID'),
    ('nonfinite', lambda: b'{"status":NaN}', 'JSON_SYNTAX_INVALID'),
    ('duplicate_keys', lambda: b'{"status":"OK","status":"OK"}', 'JSON_STRUCTURE_INVALID'),
    ('nested', lambda: row_body(c={'secret': KEY}), 'JSON_STRUCTURE_INVALID'),
    ('primitive_rows', lambda: b'{"results":[1]}', 'JSON_STRUCTURE_INVALID'),
    ('oversize_token', lambda: body(request_id=KEY * 20), 'JSON_STRUCTURE_INVALID'),
    ('oversize_integer', lambda: b'{"resultsCount":' + b'1' * 29 + b'}', 'JSON_STRUCTURE_INVALID'),
    ('root_type', lambda: b'null', 'JSON_STRUCTURE_INVALID'),
    ('root_extra', lambda: body(**{KEY: KEY}), 'ROOT_FIELDS_UNEXPECTED'),
    ('root_status', lambda: body(status=KEY), 'ROOT_STATUS_INVALID'),
    ('root_adjusted', lambda: body(adjusted=False), 'ROOT_ADJUSTMENT_INVALID'),
    ('root_missing_adjusted', lambda: b'{"status":"OK","resultsCount":0}', 'ROOT_ADJUSTMENT_INVALID'),
    ('root_bool_adjusted', lambda: body(adjusted=1), 'ROOT_ADJUSTMENT_INVALID'),
    ('root_metadata', lambda: body(request_id='\n' + KEY), 'ROOT_METADATA_INVALID'),
    ('root_metadata_length', lambda: body(request_id='X' * 129), 'ROOT_METADATA_INVALID'),
    ('count_mismatch', lambda: body(resultsCount=7), 'RESULT_COUNTS_INVALID'),
    ('count_bool', lambda: body(resultsCount=True), 'RESULT_COUNTS_INVALID'),
    ('count_negative', lambda: body(resultsCount=-1), 'RESULT_COUNTS_INVALID'),
    ('query_invalid', lambda: body(queryCount=KEY), 'RESULT_COUNTS_INVALID'),
    ('query_overflow', lambda: body(queryCount=30001), 'RESULT_COUNTS_INVALID'),
    ('row_extra', lambda: row_body(**{KEY: KEY}), 'ROW_FIELDS_UNEXPECTED'),
    ('row_identity', lambda: row_body(T='\n' + KEY), 'ROW_IDENTITY_INVALID'),
    ('row_identity_empty', lambda: row_body(T=''), 'ROW_IDENTITY_INVALID'),
    ('duplicate_symbol', lambda: body([json.loads(FIXTURE)['results'][0]] * 2), 'ROW_IDENTITY_DUPLICATE'),
    ('row_price', lambda: row_body(c=KEY), 'ROW_OHLCV_INVALID'),
    ('row_zero_price', lambda: row_body(c=0), 'ROW_OHLCV_INVALID'),
    ('row_volume', lambda: row_body(v=-1), 'ROW_OHLCV_INVALID'),
    ('row_missing_volume', lambda: body([{k: v for k, v in json.loads(FIXTURE)['results'][0].items() if k != 'v'}]), 'ROW_OHLCV_INVALID'),
    ('row_range', lambda: row_body(h=1), 'ROW_OHLCV_INVALID'),
    ('row_timestamp_type', lambda: row_body(t=KEY), 'ROW_TIMESTAMP_INVALID'),
    ('row_timestamp_bool', lambda: row_body(t=True), 'ROW_TIMESTAMP_INVALID'),
    ('row_timestamp_missing', lambda: body([{k: v for k, v in json.loads(FIXTURE)['results'][0].items() if k != 't'}]), 'ROW_TIMESTAMP_INVALID'),
    ('row_timestamp_date', lambda: row_body(t=1792123199999), 'ROW_TIMESTAMP_INVALID'),
    ('row_timestamp_future', lambda: row_body(t=1792468800000), 'ROW_TIMESTAMP_INVALID'),
    ('row_vwap', lambda: row_body(vw=KEY), 'ROW_METADATA_INVALID'),
    ('row_transactions', lambda: row_body(n=True), 'ROW_METADATA_INVALID'),
    ('row_otc', lambda: row_body(otc=KEY), 'ROW_METADATA_INVALID'),
):
    setattr(ParserDiagnostics, 'test_' + name, parser_case(factory, expected))


class TransportDiagnostics(Offline):
    def check_headers(self, headers, received, expected):
        connection = Mock()
        response = connection.getresponse.return_value
        response.getheader.side_effect = lambda key, default=None: headers.get(key, default)
        response.read.return_value = received
        with patch.object(transport, 'HTTPSConnection', return_value=connection), self.assertRaises(d._GroupedValidationError) as caught:
            transport._read_grouped_response('/v2/aggs/grouped/locale/us/market/stocks/2026-10-16?adjusted=true&include_otc=false', KEY)
        self.assertEqual(d._grouped_diagnostic(caught.exception), expected)
        self.assertNotIn(KEY, str(caught.exception))
        connection.close.assert_called_once()
        self.assertLessEqual(response.read.call_count, 1)

    def test_encoding_is_separate_and_not_echoed(self):
        self.check_headers({'Content-Encoding': KEY}, b'', 'HTTP_CONTENT_ENCODING_INVALID')

    def test_malformed_length_is_separate_and_not_echoed(self):
        self.check_headers({'Content-Length': KEY}, b'', 'HTTP_CONTENT_LENGTH_INVALID')

    def test_short_length_mismatch(self):
        self.check_headers({'Content-Length': '3'}, b'{}', 'HTTP_CONTENT_LENGTH_INVALID')

    def test_long_length_mismatch(self):
        self.check_headers({'Content-Length': '1'}, b'{}', 'HTTP_CONTENT_LENGTH_INVALID')

    def test_oversize_still_uses_existing_limit_outcome(self):
        connection = Mock()
        connection.getresponse.return_value.getheader.side_effect = lambda key, default=None: str(d._MAX_BODY + 1) if key == 'Content-Length' else default
        with patch.object(transport, 'HTTPSConnection', return_value=connection), self.assertRaises(d._GroupedLimitError):
            transport._read_grouped_response('/v2/aggs/grouped/locale/us/market/stocks/2026-10-16?adjusted=true&include_otc=false', KEY)
        connection.getresponse.return_value.read.assert_not_called()
        connection.close.assert_called_once()


class PublicationDiagnostics(Offline):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.dict('os.environ', {'MASSIVE_API_KEY': KEY}))
        self.stack.enter_context(patch.object(massive, '_now', return_value=NOW))
        self.tick = self.stack.enter_context(patch.object(massive.time, 'monotonic', return_value=1000.0))
        self.stack.enter_context(patch.object(massive, '_RATE', massive._RateGate()))
        self.read = self.stack.enter_context(patch.object(massive, '_read_grouped_response', return_value=(200, 'application/json', '', FIXTURE)))
        self.per_ticker = self.stack.enter_context(patch.object(massive, '_read_response', side_effect=AssertionError('No chained calls')))
        self.preview = massive._MarketDataPreview(enabled=True)

    def assert_failure(self, expected):
        before = build_demo_snapshot().to_payload()
        result = self.preview.load_discovery('2026-10-16')
        self.assertEqual(result, {'provider': 'Massive', 'state': 'UNAVAILABLE', 'reason': 'INVALID_RESPONSE',
                                 'demo': False, 'bars': [], 'retry_after_seconds': 60, 'diagnostic_code': expected})
        self.assertEqual(massive._RATE.blocked_until, 1060.0)
        self.assertEqual(self.preview.discovery()['rows'], [])
        self.assertNotIn(KEY, json.dumps(result))
        self.assertEqual(build_demo_snapshot().to_payload(), before)
        self.assertEqual(self.preview.observe('AAPL', 'history')['reason'], 'RATE_LIMITED')
        self.assertEqual(self.preview.load_discovery('2026-10-16')['reason'], 'RATE_LIMITED')
        self.read.assert_called_once()
        self.per_ticker.assert_not_called()
        return result

    def test_http_content_type(self):
        self.read.return_value = (200, KEY, '', KEY.encode())
        self.assert_failure('HTTP_CONTENT_TYPE_INVALID')

    def test_real_parser_stage_and_request_id_never_published(self):
        self.read.return_value = (200, 'application/json', '', body(request_id=KEY, adjusted=False))
        self.assert_failure('ROOT_ADJUSTMENT_INVALID')

    def test_failure_after_valid_prefix_publishes_neither_prefix_nor_counts(self):
        rows = json.loads(FIXTURE)['results']
        rows[-1]['t'] = KEY
        self.read.return_value = (200, 'application/json', '', body(rows))
        self.assert_failure('ROW_TIMESTAMP_INVALID')

    def test_old_success_is_not_republished_after_bad_fresh_response(self):
        self.preview.load_discovery('2026-10-16')
        self.tick.return_value += 400
        self.read.return_value = (200, 'application/json', '', row_body(c=0))
        result = self.preview.load_discovery('2026-10-16')
        self.assertEqual(result['diagnostic_code'], 'ROW_OHLCV_INVALID')
        self.assertEqual(self.preview.discovery()['rows'], [])
        self.assertEqual(self.read.call_count, 2)

    def test_arbitrary_exception_text_is_not_a_diagnostic(self):
        self.read.side_effect = RuntimeError(KEY, {'request_id': KEY})
        self.assert_failure('VALIDATION_FAILED')

    def test_tampered_error_category_is_not_echoed(self):
        error = d._GroupedValidationError(d._GroupedDiagnostic.ROW_TIMESTAMP_INVALID)
        error.diagnostic = KEY
        error.args = (KEY,)
        self.read.side_effect = error
        self.assert_failure('VALIDATION_FAILED')

    def test_no_diagnostic_on_success_or_cached_success(self):
        result = self.preview.load_discovery('2026-10-16')
        self.assertNotIn('diagnostic_code', result)
        self.assertNotIn('diagnostic_code', self.preview.load_discovery('2026-10-16'))
        self.read.assert_called_once()

    def test_malicious_status_body_is_not_parsed_or_disclosed(self):
        self.read.return_value = (403, 'application/json', '', KEY.encode())
        result = self.preview.load_discovery('2026-10-16')
        self.assertEqual(result['reason'], 'ACCESS_DENIED')
        self.assertNotIn('diagnostic_code', result)
        self.assertNotIn(KEY, json.dumps(result))


def publication_case(category):
    def test(self):
        self.read.side_effect = d._GroupedValidationError(category)
        self.assert_failure(category.value)
    return test


for category in d._GroupedDiagnostic:
    setattr(PublicationDiagnostics, 'test_bounded_publication_' + category.value.lower(), publication_case(category))


class StaticDiagnostics(unittest.TestCase):
    def test_browser_and_python_code_sets_are_identical_and_bounded(self):
        source = (ROOT / 'tradingbot_dashboard/static/market-discovery.js').read_text(encoding='utf-8')
        codes = ast.literal_eval(re.search(r'const diagnostics = new Set\((\[[\s\S]*?\])\);', source)[1])
        self.assertEqual(set(codes), {code.value for code in d._GroupedDiagnostic})
        self.assertLessEqual(len(codes), 20)
        self.assertTrue(all(re.fullmatch(r'[A-Z_]{1,40}', code) for code in codes))
        self.assertIn("data.reason === 'INVALID_RESPONSE' && diagnostics.has(data.diagnostic_code)", source)

    def test_invalid_category_constructor_and_foreign_error_fall_back(self):
        error = d._GroupedValidationError(KEY)
        self.assertEqual(d._grouped_diagnostic(error), 'VALIDATION_FAILED')
        self.assertEqual(d._grouped_diagnostic(RuntimeError(KEY)), 'VALIDATION_FAILED')


if __name__ == '__main__':
    unittest.main()
