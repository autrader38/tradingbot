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
from tests.test_velocity_market_discovery import FIXTURE, NOW, DAY, KEY, ROOT, body, decode, large_body


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
        # Retained dates no longer trigger fresh requests; reject a new load.
        result = self.preview.load_discovery('2026-10-15')
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


class RootFieldDiagnostics(Offline):
    def fields(self, value):
        with self.assertRaises(d._GroupedValidationError) as caught:
            decode(value)
        error = caught.exception
        self.assertEqual(d._grouped_diagnostic(error), 'ROOT_FIELDS_UNEXPECTED')
        payload = d._grouped_root_fields(error)
        self.assertNotIn(KEY, json.dumps(payload))
        self.assertNotIn(KEY, repr(error))
        self.assertNotIn(KEY, repr(error.root_fields))
        return payload['unexpected_root_fields']

    def test_all_categories_and_multiple_unknown_keys_in_fixed_order(self):
        # Twelve total root keys is the existing structural cap. No root status
        # is needed to test rejection, which precedes status qualification.
        raw = {name: KEY for name in ('ticker','next_url','requestId',
                                      'pagination','message','error')}
        raw.update({f'unknown_{index}': KEY for index in range(6)})
        expected = [{'category': category.value, 'count': 6 if category is d._RootFieldCategory.OTHER else 1}
                    for category in d._RootFieldCategory]
        self.assertEqual(self.fields(json.dumps(raw).encode()), expected)
        self.assertEqual(self.fields(json.dumps(dict(reversed(tuple(raw.items())))).encode()), expected)

    def test_only_unknown_names_are_aggregated_not_echoed_or_fingerprinted(self):
        raw = {KEY + str(index): KEY for index in range(12)}
        self.assertEqual(self.fields(json.dumps(raw).encode()), [{'category':'OTHER','count':12}])

    def test_values_do_not_influence_root_name_classification(self):
        for value in (None, False, 0, KEY, [], 'not-a-ticker'):
            self.assertEqual(self.fields(body(ticker=value)), [{'category':'TICKER','count':1}])

    def test_escaped_key_uses_exact_decoded_name_only(self):
        self.assertEqual(self.fields(b'{"\\u0074icker":"anonymous"}'), [{'category':'TICKER','count':1}])

    def test_only_count_added_to_original_root_allowlist(self):
        self.assertEqual(d._ROOT_FIELDS, frozenset(('adjusted','queryCount','resultsCount','count','status','request_id','results')))
        self.assertEqual(decode().received_count, 8)
        self.assertEqual(d._grouped_root_fields(RuntimeError(KEY)), {})

    def test_structure_limit_still_precedes_root_classification(self):
        raw = {f'unknown_{index}': KEY for index in range(13)}
        for value in (json.dumps(raw).encode(), body(ticker={'nested':KEY}), body(**{'X'*300:KEY})):
            with self.assertRaises(d._GroupedValidationError) as caught:
                decode(value)
            self.assertEqual(d._grouped_diagnostic(caught.exception), 'JSON_STRUCTURE_INVALID')
            self.assertEqual(d._grouped_root_fields(caught.exception), {})

    def test_duplicate_root_keys_still_fail_structure_not_partial_classification(self):
        with self.assertRaises(d._GroupedValidationError) as caught:
            decode(b'{"ticker":1,"ticker":2}')
        self.assertEqual(d._grouped_diagnostic(caught.exception), 'JSON_STRUCTURE_INVALID')
        self.assertEqual(d._grouped_root_fields(caught.exception), {})

    def test_invalid_substituted_details_are_omitted_at_publication(self):
        category = d._RootFieldCategory
        for fields in (None, [], ((KEY,1),), ((category.OTHER,True),), ((category.OTHER,0),),
                       ((category.OTHER,-1),), ((category.OTHER,13),), ((category.TICKER,2),),
                       ((category.OTHER,1),(category.OTHER,1)), ((category.OTHER,12),(category.TICKER,1)),
                       ((category.TICKER,1),(category.OTHER,12)), ((category.OTHER,1,KEY),)):
            error = d._GroupedValidationError(d._GroupedDiagnostic.ROOT_FIELDS_UNEXPECTED)
            error.root_fields = fields
            error.args = (KEY,)
            self.assertEqual(d._grouped_root_fields(error), {})

    def test_no_details_for_unrelated_diagnostic_or_foreign_error(self):
        fields = ((d._RootFieldCategory.OTHER,1),)
        error = d._GroupedValidationError(d._GroupedDiagnostic.ROW_FIELDS_UNEXPECTED, root_fields=fields)
        self.assertEqual(error.root_fields, ())
        error.root_fields = fields
        self.assertEqual(d._grouped_root_fields(error), {})
        error.diagnostic = KEY
        self.assertEqual(d._grouped_root_fields(error), {})

    def test_error_retains_only_immutable_categories_and_counts(self):
        error = d._GroupedValidationError(d._GroupedDiagnostic.ROOT_FIELDS_UNEXPECTED,
                                          root_fields=d._classify_root_fields({KEY,'ticker'}))
        self.assertEqual(error.root_fields, ((d._RootFieldCategory.TICKER,1),(d._RootFieldCategory.OTHER,1)))
        first = d._grouped_root_fields(error)
        first['unexpected_root_fields'][0]['category'] = KEY
        self.assertEqual(d._grouped_root_fields(error)['unexpected_root_fields'][0]['category'], 'TICKER')

    def test_browser_and_python_root_category_sets_match(self):
        source = (ROOT/'tradingbot_dashboard/static/market-discovery.js').read_text(encoding='utf-8')
        values = ast.literal_eval(re.search(r'const rootCategories = new Set\((\[[\s\S]*?\])\);',source)[1])
        self.assertEqual(values,[category.value for category in d._RootFieldCategory])
        self.assertEqual(len(values),7)


def root_field_case(name, expected):
    def test(self):
        self.assertEqual(self.fields(body(**{name: KEY})), [{'category':expected,'count':1}])
    return test


for name, expected in (('ticker','TICKER'),('next_url','NEXT_URL'),
                       ('requestId','REQUEST_ID_CAMEL'),('pagination','PAGINATION'),
                       ('message','MESSAGE'),('error','ERROR')):
    setattr(RootFieldDiagnostics,'test_closed_'+name,root_field_case(name,expected))
for index, name in enumerate((KEY,'','\n'+KEY,'\u202e'+KEY,'<script>'+KEY+'</script>',
                              'Authorization: Bearer '+KEY,'X'*240,'Ticker','TICKER','requestid','requestID')):
    setattr(RootFieldDiagnostics,f'test_other_name_{index}',root_field_case(name,'OTHER'))


class RootFieldPublication(Offline):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.dict('os.environ', {'MASSIVE_API_KEY':KEY}))
        self.stack.enter_context(patch.object(massive, '_now', return_value=NOW))
        self.tick = self.stack.enter_context(patch.object(massive.time, 'monotonic', return_value=1000.0))
        self.stack.enter_context(patch.object(massive, '_RATE', massive._RateGate()))
        self.read = self.stack.enter_context(patch.object(massive, '_read_grouped_response'))
        self.old = self.stack.enter_context(patch.object(massive, '_read_response',side_effect=AssertionError('No chained calls')))
        self.preview = massive._MarketDataPreview(enabled=True)

    def test_bounded_output_no_partial_data_no_leakage_and_exact_manual_backoff(self):
        raw = {KEY+str(index):KEY for index in range(12)}
        self.read.return_value = (200,'application/json','',json.dumps(raw).encode())
        original = build_demo_snapshot().to_payload()
        result = self.preview.load_discovery('2026-10-16')
        self.assertEqual(result, {'provider':'Massive','state':'UNAVAILABLE','reason':'INVALID_RESPONSE',
            'demo':False,'bars':[],'retry_after_seconds':60,'diagnostic_code':'ROOT_FIELDS_UNEXPECTED',
            'unexpected_root_fields':[{'category':'OTHER','count':12}]})
        self.assertLess(len(json.dumps(result)),1024)
        self.assertNotIn(KEY,json.dumps(result))
        self.assertEqual(self.preview.discovery()['rows'],[])
        self.assertEqual(build_demo_snapshot().to_payload(),original)
        self.assertEqual(massive._RATE.blocked_until,1060.0)
        self.assertEqual(self.preview.load_discovery('2026-10-16')['reason'],'RATE_LIMITED')
        self.assertEqual(self.preview.observe('AAPL','history')['reason'],'RATE_LIMITED')
        self.read.assert_called_once();self.old.assert_not_called()

    def test_all_categories_fit_fixed_output_bound(self):
        raw = dict.fromkeys(d._ROOT_FIELD_NAMES,KEY)
        raw[KEY] = KEY
        self.read.return_value = (200,'application/json','',json.dumps(raw).encode())
        result = self.preview.load_discovery('2026-10-16')
        self.assertEqual(len(result['unexpected_root_fields']),7)
        self.assertLess(len(json.dumps(result)),1024)
        self.assertNotIn(KEY,json.dumps(result))

    def test_valid_count_one_manual_request_and_cache_without_extra_quota(self):
        self.read.return_value = (200,'application/json','',body(count=8))
        result = self.preview.load_discovery('2026-10-16')
        self.assertEqual((result['provider_results_count'],result['received_rows']), (8,8))
        self.assertNotIn('diagnostic_code',result)
        self.assertNotIn('count',result)
        self.assertNotIn(KEY,json.dumps(result))
        with patch.object(massive._RATE,'reserve',side_effect=AssertionError('No quota for cache hit')):
            self.assertTrue(self.preview.load_discovery('2026-10-16')['cached'])
        self.assertEqual(self.preview.observe('AAPL','history')['reason'],'RATE_LIMITED')
        self.assertEqual(tuple(massive._RATE.attempts),(1000.0,))
        self.assertEqual(massive._RATE.blocked_until,0)
        self.read.assert_called_once();self.old.assert_not_called()

    def test_invalid_count_sanitized_with_exact_existing_manual_backoff(self):
        self.read.return_value = (200,'application/json','',body(count=KEY,request_id=KEY))
        original = build_demo_snapshot().to_payload()
        result = self.preview.load_discovery('2026-10-16')
        self.assertEqual(result, {'provider':'Massive','state':'UNAVAILABLE','reason':'INVALID_RESPONSE',
            'demo':False,'bars':[],'retry_after_seconds':60,'diagnostic_code':'RESULT_COUNTS_INVALID'})
        self.assertNotIn(KEY,json.dumps(result))
        self.assertEqual(self.preview.discovery()['rows'],[])
        self.assertEqual(build_demo_snapshot().to_payload(),original)
        self.assertEqual(massive._RATE.blocked_until,1060.0)
        self.assertEqual(self.preview.load_discovery('2026-10-16')['reason'],'RATE_LIMITED')
        self.assertEqual(self.preview.observe('AAPL','history')['reason'],'RATE_LIMITED')
        self.read.assert_called_once();self.old.assert_not_called()

    def test_bad_count_cannot_republish_old_success_or_partial_rows(self):
        self.read.return_value = (200,'application/json','',body(count=8))
        self.preview.load_discovery('2026-10-16')
        self.tick.return_value += 400
        self.read.return_value = (200,'application/json','',body(count=7))
        result = self.preview.load_discovery('2026-10-15')
        self.assertEqual(result['diagnostic_code'],'RESULT_COUNTS_INVALID')
        self.assertEqual(result['retry_after_seconds'],60)
        self.assertEqual(self.preview.discovery()['rows'],[])
        for field in ('rows','received_rows','provider_results_count','unexpected_root_fields'):
            self.assertNotIn(field,result)
        self.assertEqual(self.read.call_count,2);self.old.assert_not_called()


class OptionalCountTests(Offline):
    def test_valid_optional_count_and_missing_count_publish_identical_evidence(self):
        original, compatible = decode(), decode(body(count=8))
        self.assertEqual(original, compatible)
        self.assertEqual(original.page(NOW), compatible.page(NOW))
        self.assertEqual(compatible.rows[0].close.as_tuple(), original.rows[0].close.as_tuple())

    def test_zero_count_zero_rows_and_optional_absent_results(self):
        for value in (body([],count=0), b'{"status":"OK","adjusted":true,"resultsCount":0,"count":0}'):
            result = decode(value).page(NOW)
            self.assertEqual(result['received_rows'],0)
            self.assertEqual(result['reason'],'NO_SUPPORTED_DAILY_OBSERVATIONS')
            self.assertEqual(result['rows'],[])

    def test_exact_maximum_count_and_received_rows(self):
        raw = json.loads(large_body(d._MAX_ROWS))
        raw['count'] = d._MAX_ROWS
        result = decode(json.dumps(raw).encode()).page(NOW)
        self.assertEqual(result['received_rows'],30000)
        self.assertEqual(result['validated_rows'],30000)
        self.assertEqual(len(result['rows']),50)

    def test_query_count_has_separate_unchanged_validation(self):
        for query in (None,0,1,9,30000):
            result = decode(body(count=8,queryCount=query))
            self.assertEqual(result.received_count,8)
            self.assertEqual(result.query_count,query)
        for query in (True,-1,30001,'8'):
            with self.assertRaises(d._GroupedValidationError) as caught:
                decode(body(count=8,queryCount=query))
            self.assertEqual(d._grouped_diagnostic(caught.exception),'RESULT_COUNTS_INVALID')

    def test_count_must_match_both_results_count_and_actual_received_length(self):
        for count, results in ((7,8),(8,7),(7,7),(9,9),(0,8)):
            with self.assertRaises(d._GroupedValidationError) as caught:
                decode(body(count=count,resultsCount=results))
            self.assertEqual(d._grouped_diagnostic(caught.exception),'RESULT_COUNTS_INVALID')

    def test_count_is_received_count_not_supported_or_filtered_candidate_count(self):
        original = decode().page(NOW)
        self.assertEqual((original['received_rows'],original['validated_rows'],original['eligible_candidates']),(8,6,5))
        for count in (6,5):
            with self.assertRaises(d._GroupedValidationError) as caught:
                decode(body(count=count))
            self.assertEqual(d._grouped_diagnostic(caught.exception),'RESULT_COUNTS_INVALID')

    def test_count_with_remaining_unknown_fields_still_rejected_and_classified(self):
        for count in (8,None,KEY):
            with self.assertRaises(d._GroupedValidationError) as caught:
                decode(body(count=count,next_url=KEY,**{KEY:KEY}))
            self.assertEqual(d._grouped_diagnostic(caught.exception),'ROOT_FIELDS_UNEXPECTED')
            payload = d._grouped_root_fields(caught.exception)
            self.assertEqual(payload,{'unexpected_root_fields':[{'category':'NEXT_URL','count':1},{'category':'OTHER','count':1}]})
            self.assertNotIn(KEY,json.dumps(payload))

    def test_integer_lexemes_only_not_decimal_or_exponent(self):
        for value in (b'8.0',b'8e0',b'8E+0'):
            with self.assertRaises(d._GroupedValidationError) as caught:
                decode(body(count=8).replace(b'"count":8',b'"count":'+value))
            self.assertEqual(d._grouped_diagnostic(caught.exception),'RESULT_COUNTS_INVALID')

    def test_duplicate_count_including_escaped_name_is_still_rejected(self):
        for value in (b'{"count":0,"count":0}',b'{"count":0,"co\\u0075nt":0}'):
            with self.assertRaises(d._GroupedValidationError) as caught:decode(value)
            self.assertEqual(d._grouped_diagnostic(caught.exception),'JSON_STRUCTURE_INVALID')

    def test_malformed_or_nonfinite_json_is_still_rejected(self):
        complete = body(count=8).rstrip()
        self.assertTrue(complete.endswith(b'}'))
        for value in (complete[:-1], complete+b'x', b'{"count":NaN}', b'{"count":Infinity}'):
            with self.assertRaises(d._GroupedValidationError) as caught:decode(value)
            self.assertEqual(d._grouped_diagnostic(caught.exception),'JSON_SYNTAX_INVALID')

    def test_valid_count_does_not_rescue_bad_ohlcv_or_timestamp(self):
        for field, value, expected in (('c',0,'ROW_OHLCV_INVALID'),('t',None,'ROW_TIMESTAMP_INVALID')):
            rows = json.loads(FIXTURE)['results']
            rows[-1][field] = value
            with self.assertRaises(d._GroupedValidationError) as caught:decode(body(rows,count=8))
            self.assertEqual(d._grouped_diagnostic(caught.exception),expected)


def invalid_optional_count(value):
    def test(self):
        with self.assertRaises(d._GroupedValidationError) as caught:decode(body(count=value))
        self.assertEqual(d._grouped_diagnostic(caught.exception),'RESULT_COUNTS_INVALID')
        self.assertEqual(d._grouped_root_fields(caught.exception),{})
    return test


for name, value in (('negative',-1),('oversized',30001),('large_integer',10**20),
                    ('bool_true',True),('bool_false',False),('decimal_integral',8.0),
                    ('decimal_fractional',8.5),('string','8'),('null',None)):
    setattr(OptionalCountTests,'test_invalid_'+name,invalid_optional_count(value))


if __name__ == '__main__':
    unittest.main()
