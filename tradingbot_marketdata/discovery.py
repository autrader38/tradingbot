"""Pure, bounded grouped-daily evidence and preliminary discovery, not signals."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from fractions import Fraction
import json
import re

from .models import _check, _constant, _eastern, _instant, _numeric, _ticker, _utc
from .research import _ratio

__all__ = ()

_MAX_BODY = 8 * 1024 * 1024
_MAX_ROWS = 30_000
_PAGE_SIZE = 50
_ROOT_FIELDS = frozenset(('adjusted', 'queryCount', 'resultsCount', 'status', 'request_id', 'results'))
_MISSING = ('CLOSE_TO_CLOSE_CHANGE', 'PREVIOUS_TEN_BAR_AVERAGE_VOLUME',
            'EOD_RELATIVE_VOLUME', 'DATED_MARKET_CAP', 'VOLATILITY_CLASSIFICATION',
            'ABCD_SETUP', 'INSTRUMENT_CLASSIFICATION')
_LIMITATIONS = ('PRELIMINARY EOD CANDIDATES — NOT FULLY QUALIFIED',
                'RESPONSE_COVERAGE_IS_NOT_COMPLETE_US_MARKET_COVERAGE',
                'NO_COMMON_STOCK_EXCHANGE_OR_ACTIVE_STATUS_PROOF',
                'REQUESTED_DATE_IS_NOT_MARKET_CALENDAR_OR_SESSION_COMPLETENESS_PROOF',
                'DAILY_RANGE_IS_NOT_CLOSE_TO_CLOSE_CHANGE',
                'PERSONAL_NON_COMMERCIAL_EOD_RESEARCH_ONLY', 'NO_TRADING_AUTHORITY')


class _GroupedLimitError(ValueError):
    def __init__(self):
        super().__init__('Grouped response exceeds fixed limits')


class _GroupedDiagnostic(Enum):
    HTTP_CONTENT_TYPE_INVALID = 'HTTP_CONTENT_TYPE_INVALID'
    HTTP_CONTENT_ENCODING_INVALID = 'HTTP_CONTENT_ENCODING_INVALID'
    HTTP_CONTENT_LENGTH_INVALID = 'HTTP_CONTENT_LENGTH_INVALID'
    JSON_SYNTAX_INVALID = 'JSON_SYNTAX_INVALID'
    JSON_STRUCTURE_INVALID = 'JSON_STRUCTURE_INVALID'
    ROOT_FIELDS_UNEXPECTED = 'ROOT_FIELDS_UNEXPECTED'
    ROOT_STATUS_INVALID = 'ROOT_STATUS_INVALID'
    ROOT_ADJUSTMENT_INVALID = 'ROOT_ADJUSTMENT_INVALID'
    ROOT_METADATA_INVALID = 'ROOT_METADATA_INVALID'
    RESULT_COUNTS_INVALID = 'RESULT_COUNTS_INVALID'
    ROW_FIELDS_UNEXPECTED = 'ROW_FIELDS_UNEXPECTED'
    ROW_IDENTITY_INVALID = 'ROW_IDENTITY_INVALID'
    ROW_IDENTITY_DUPLICATE = 'ROW_IDENTITY_DUPLICATE'
    ROW_OHLCV_INVALID = 'ROW_OHLCV_INVALID'
    ROW_TIMESTAMP_INVALID = 'ROW_TIMESTAMP_INVALID'
    ROW_METADATA_INVALID = 'ROW_METADATA_INVALID'
    VALIDATION_FAILED = 'VALIDATION_FAILED'


class _RootFieldCategory(Enum):
    TICKER = 'TICKER'
    COUNT = 'COUNT'
    NEXT_URL = 'NEXT_URL'
    REQUEST_ID_CAMEL = 'REQUEST_ID_CAMEL'
    PAGINATION = 'PAGINATION'
    MESSAGE = 'MESSAGE'
    ERROR = 'ERROR'
    OTHER = 'OTHER'


# Diagnostic labels only. None of these names becomes an accepted root field.
_ROOT_FIELD_NAMES = {'ticker': _RootFieldCategory.TICKER, 'count': _RootFieldCategory.COUNT,
                     'next_url': _RootFieldCategory.NEXT_URL, 'requestId': _RootFieldCategory.REQUEST_ID_CAMEL,
                     'pagination': _RootFieldCategory.PAGINATION, 'message': _RootFieldCategory.MESSAGE,
                     'error': _RootFieldCategory.ERROR}


def _safe_root_fields(fields):
    """Immutable enum/count pairs only; bounded by the existing 12-key JSON cap."""
    if type(fields) is not tuple or not 1 <= len(fields) <= len(_RootFieldCategory): return ()
    order, previous, total = tuple(_RootFieldCategory), -1, 0
    for entry in fields:
        if type(entry) is not tuple or len(entry) != 2: return ()
        category, count = entry
        if type(category) is not _RootFieldCategory or type(count) is not int or not 1 <= count <= 12: return ()
        if category is not _RootFieldCategory.OTHER and count != 1: return ()
        index = order.index(category)
        if index <= previous: return ()
        previous, total = index, total + count
    return fields if total <= 12 else ()


def _classify_root_fields(keys):
    # Read field names only, never their values; unknown names are not retained,
    # echoed or fingerprinted. Ordering is fixed, independent of response order.
    _check(type(keys) is set and 1 <= len(keys) <= 12)
    counts = dict.fromkeys(_RootFieldCategory, 0)
    for key in keys:
        _check(type(key) is str)
        counts[_ROOT_FIELD_NAMES.get(key, _RootFieldCategory.OTHER)] += 1
    return tuple((category, count) for category, count in counts.items() if count)


class _GroupedValidationError(ValueError):
    """Closed categories/counts only, never provider values, raw names or messages."""
    def __init__(self, diagnostic, *, root_fields=()):
        self.diagnostic = diagnostic if type(diagnostic) is _GroupedDiagnostic else _GroupedDiagnostic.VALIDATION_FAILED
        self.root_fields = _safe_root_fields(root_fields) if self.diagnostic is _GroupedDiagnostic.ROOT_FIELDS_UNEXPECTED else ()
        super().__init__('Grouped response failed validation')


def _grouped_diagnostic(error):
    # Recheck at the publication boundary; even a substituted/tampered error
    # cannot turn exception text, arguments or arbitrary attributes into JSON.
    value = getattr(error, 'diagnostic', None) if type(error) is _GroupedValidationError else None
    return value.value if type(value) is _GroupedDiagnostic else _GroupedDiagnostic.VALIDATION_FAILED.value


def _grouped_root_fields(error):
    if type(error) is not _GroupedValidationError or _grouped_diagnostic(error) != 'ROOT_FIELDS_UNEXPECTED': return {}
    fields = _safe_root_fields(getattr(error, 'root_fields', None))
    if not fields: return {}
    return {'unexpected_root_fields': [{'category': category.value, 'count': count} for category, count in fields]}


@contextmanager
def _validation_stage(diagnostic):
    try:
        yield
    except (_GroupedLimitError, _GroupedValidationError):
        raise
    except (ValueError, TypeError, ArithmeticError):
        raise _GroupedValidationError(diagnostic) from None


def _request_date(value, now):
    _check(type(value) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value, flags=re.ASCII))
    result = date.fromisoformat(value)
    today = _eastern(now).date()
    _check(today - timedelta(days=730) <= result < today and result.weekday() < 5)
    return result


def _preflight(text):
    """Bound containers/depth/tokens before JSON allocates decoded objects.

    Only a root object, one results array and flat row objects are supported.
    JSON itself still validates grammar, escapes, numbers and trailing content.
    """
    depth = containers = token = 0
    stack = []
    quoted = escaped = False
    for char in text:
        if quoted:
            token += 1
            _check(token <= 256)
            if escaped: escaped = False
            elif char == '\\': escaped = True
            elif char == '"': quoted = False; token = 0
        elif stack and stack[-1] == '[' and char not in ' \n\r\t,{}]':
            raise ValueError('Invalid grouped array')
        elif char == '"': quoted = True; token = 0
        elif char in '[{':
            depth += 1; containers += 1; token = 0
            _check(depth <= 3 and (char == '[' and depth == 2 or char == '{' and depth in (1, 3)))
            stack.append(char)
            if containers > _MAX_ROWS + 2: raise _GroupedLimitError()
        elif char in ']}':
            _check(stack and stack.pop() == ('[' if char == ']' else '{'))
            depth -= 1; token = 0
        elif char in ',: \n\r\t': token = 0
        else:
            token += 1
            _check(token <= 64)


def _pairs(pairs):
    with _validation_stage(_GroupedDiagnostic.JSON_STRUCTURE_INVALID):
        _check(len(pairs) <= 12)
        result = {}
        for key, value in pairs:
            _check(key not in result)
            result[key] = value
    return result


def _integer(text):
    with _validation_stage(_GroupedDiagnostic.JSON_STRUCTURE_INVALID):
        _check(len(text) <= 28)
    return int(text)


@dataclass(frozen=True, slots=True, repr=False)
class _GroupedBar:
    ticker: str
    timestamp_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    vwap: Decimal | None

    def __post_init__(self):
        _ticker(self.ticker)
        _instant(self.timestamp_ms)
        for name in ('open', 'high', 'low', 'close', 'volume', 'vwap'):
            value = getattr(self, name)
            if name == 'vwap' and value is None: continue
            _check(type(value) is Decimal)
            _numeric(value, volume=name == 'volume')
        _check(self.low <= self.open <= self.high and self.low <= self.close <= self.high)

    @property
    def range_pct(self):
        return 100 * (Fraction(self.high) - Fraction(self.low)) / Fraction(self.open)

    def payload(self):
        return {'ticker': self.ticker, 'source_timestamp_ms': self.timestamp_ms,
                'source_at': _instant(self.timestamp_ms).isoformat(),
                'source_date_eastern': _eastern(_instant(self.timestamp_ms)).date().isoformat(),
                **{name: None if getattr(self, name) is None else str(getattr(self, name))
                   for name in ('open', 'high', 'low', 'close', 'volume', 'vwap')},
                'daily_range_pct': _ratio(self.range_pct)}


@dataclass(frozen=True, slots=True, repr=False)
class _DiscoveryFilters:
    max_price: Decimal = Decimal('5')
    min_volume: Decimal = Decimal('0')

    def __post_init__(self):
        for value, maximum in ((self.max_price, 10**6), (self.min_volume, 10**15)):
            _check(type(value) is Decimal and value.is_finite()
                   and len(value.as_tuple().digits) <= 28
                   and -12 <= value.as_tuple().exponent <= 12 and 0 <= value <= maximum)
        _check(self.max_price > 0)


def _filter_decimal(value):
    _check(type(value) is str and len(value) <= 32
           and re.fullmatch(r'(?:0|[1-9]\d*)(?:\.\d{1,12})?', value, flags=re.ASCII))
    return Decimal(value)


@dataclass(frozen=True, slots=True, repr=False)
class _GroupedSummary:
    requested_date: date
    rows: tuple[_GroupedBar, ...]
    received_count: int
    query_count: int | None
    excluded_otc: int
    excluded_unsupported: int

    def __post_init__(self):
        _check(type(self.requested_date) is date and self.requested_date.weekday() < 5)
        _check(type(self.rows) is tuple and len(self.rows) <= _MAX_ROWS)
        for value in (self.received_count, self.excluded_otc, self.excluded_unsupported):
            _check(type(value) is int and 0 <= value <= _MAX_ROWS)
        _check(self.query_count is None or type(self.query_count) is int and 0 <= self.query_count <= _MAX_ROWS)
        _check(len(self.rows) + self.excluded_otc + self.excluded_unsupported == self.received_count)
        _check(len({row.ticker for row in self.rows}) == len(self.rows))
        for row in self.rows:
            _check(type(row) is _GroupedBar)
            row.__post_init__()

    def page(self, now, *, filters=_DiscoveryFilters(), search='', sort='volume', page=0):
        _check(type(filters) is _DiscoveryFilters)
        filters.__post_init__()
        _check(type(search) is str and len(search) <= 7
               and re.fullmatch(r'[A-Za-z.]*', search, flags=re.ASCII) is not None)
        _check(type(sort) is str and sort in ('volume', 'price', 'price_desc', 'range'))
        _check(type(page) is int and 0 <= page < _MAX_ROWS // _PAGE_SIZE)
        age = (_eastern(now).date() - self.requested_date).days
        _check(age > 0)
        eligible = [row for row in self.rows if row.close < filters.max_price
                    and (filters.min_volume == 0 or row.volume > filters.min_volume)]
        matches = [row for row in eligible if search.upper() in row.ticker]
        def key(row):
            value = Fraction(row.volume) if sort == 'volume' else row.range_pct if sort == 'range' else Fraction(row.close)
            return (value if sort == 'price' else -value, row.ticker)
        matches.sort(key=key)
        pages = max(1, (len(matches) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page = min(page, pages - 1)
        shown = matches[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE]
        return {'provider': 'Massive', 'source_endpoint': 'daily-market-summary', 'demo': False,
                'state': 'UNAVAILABLE' if not self.rows else 'STALE' if age > 4 else 'REAL END-OF-DAY',
                'reason': 'NO_SUPPORTED_DAILY_OBSERVATIONS' if not self.rows else 'VALIDATED_GROUPED_DAILY_SOURCE',
                'requested_date': self.requested_date.isoformat(), 'provider_results_count': self.received_count,
                'provider_query_count': self.query_count, 'received_rows': self.received_count,
                'validated_rows': len(self.rows), 'excluded_otc': self.excluded_otc,
                'excluded_unsupported': self.excluded_unsupported, 'excluded_malformed': 0,
                'eligible_candidates': len(eligible), 'matched_candidates': len(matches),
                'awaiting_deeper_research': len(eligible), 'displayed_count': len(shown),
                'page': page, 'pages': pages, 'page_size': _PAGE_SIZE,
                'rows': [row.payload() for row in shown], 'adjusted_for_splits': True,
                'include_otc': False, 'market_timezone': 'America/New_York',
                'age_calendar_days': age, 'market_session_completeness': 'NOT_ESTABLISHED',
                'timestamp_semantics': 'Provider-documented aggregate window end; retained verbatim, not a closing-event or session-completeness certificate',
                'quality': ['EXACT_REQUESTED_DATE', 'ALL_DECLARED_RESPONSE_ROWS_RECEIVED',
                            'UNKNOWN_TICKER_FORMATS_EXCLUDED_WITHOUT_CONVERSION',
                            'OTC_QUERY_EXCLUDED_AND_EXPLICIT_OTC_ROWS_EXCLUDED',
                            'FOUR_CALENDAR_DAY_FRESHNESS_RULE_NOT_MARKET_CALENDAR'],
                'missing_qualification': list(_MISSING), 'limitations': list(_LIMITATIONS)}


def _decode_grouped(body, requested_date, now):
    with _validation_stage(_GroupedDiagnostic.JSON_STRUCTURE_INVALID):
        _check(type(body) is bytes)
    if len(body) > _MAX_BODY: raise _GroupedLimitError()
    _check(type(requested_date) is date)
    _request_date(requested_date.isoformat(), now)
    with _validation_stage(_GroupedDiagnostic.JSON_SYNTAX_INVALID):
        text = body.decode('utf-8')
    with _validation_stage(_GroupedDiagnostic.JSON_STRUCTURE_INVALID):
        _preflight(text)
    with _validation_stage(_GroupedDiagnostic.JSON_SYNTAX_INVALID):
        raw = json.loads(text, parse_float=Decimal, parse_int=_integer,
                         parse_constant=_constant, object_pairs_hook=_pairs)
    with _validation_stage(_GroupedDiagnostic.JSON_STRUCTURE_INVALID):
        _check(type(raw) is dict)
    with _validation_stage(_GroupedDiagnostic.ROOT_FIELDS_UNEXPECTED):
        unexpected = set(raw) - _ROOT_FIELDS
        if unexpected:
            raise _GroupedValidationError(_GroupedDiagnostic.ROOT_FIELDS_UNEXPECTED,
                                          root_fields=_classify_root_fields(unexpected))
    with _validation_stage(_GroupedDiagnostic.ROOT_STATUS_INVALID):
        _check(raw.get('status') == 'OK')
    with _validation_stage(_GroupedDiagnostic.ROOT_ADJUSTMENT_INVALID):
        _check(raw.get('adjusted') is True)
    count = raw.get('resultsCount')
    with _validation_stage(_GroupedDiagnostic.RESULT_COUNTS_INVALID):
        _check(type(count) is int and count >= 0)
    if count > _MAX_ROWS: raise _GroupedLimitError()
    query_count = raw.get('queryCount')
    with _validation_stage(_GroupedDiagnostic.RESULT_COUNTS_INVALID):
        _check(query_count is None or type(query_count) is int and 0 <= query_count <= _MAX_ROWS)
    with _validation_stage(_GroupedDiagnostic.ROOT_METADATA_INVALID):
        if 'request_id' in raw:
            _check(type(raw['request_id']) is str and 1 <= len(raw['request_id']) <= 128
                   and raw['request_id'].isascii() and raw['request_id'].isprintable())
    rows = raw.get('results', [])
    with _validation_stage(_GroupedDiagnostic.RESULT_COUNTS_INVALID):
        _check(type(rows) is list and len(rows) == count)
    accepted, seen, otc, unsupported = [], set(), 0, 0
    for row in rows:
        with _validation_stage(_GroupedDiagnostic.ROW_FIELDS_UNEXPECTED):
            _check(type(row) is dict and set(row) <= {'T', 'o', 'h', 'l', 'c', 'v', 't', 'vw', 'n', 'otc'})
        symbol = row.get('T')
        with _validation_stage(_GroupedDiagnostic.ROW_IDENTITY_INVALID):
            _check(type(symbol) is str and 1 <= len(symbol) <= 32 and symbol.isascii() and symbol.isprintable())
        with _validation_stage(_GroupedDiagnostic.ROW_IDENTITY_DUPLICATE):
            _check(symbol not in seen)
        seen.add(symbol)
        with _validation_stage(_GroupedDiagnostic.ROW_OHLCV_INVALID):
            values = [_numeric(row.get(name), volume=name == 'v') for name in ('o', 'h', 'l', 'c', 'v')]
            opened, high, low, closed, volume = values
            _check(low <= opened <= high and low <= closed <= high)
        timestamp = row.get('t')
        with _validation_stage(_GroupedDiagnostic.ROW_TIMESTAMP_INVALID):
            instant = _instant(timestamp)
            eastern = _eastern(instant)
            _check(instant <= _utc(now))
            # Preserve the reviewed grouped timestamp/date contract exactly.
            _check(eastern.date() == requested_date or
                   eastern.date() == requested_date + timedelta(days=1)
                   and eastern.hour == eastern.minute == eastern.second == eastern.microsecond == 0)
        with _validation_stage(_GroupedDiagnostic.ROW_METADATA_INVALID):
            vwap = None if 'vw' not in row else _numeric(row['vw'])
            if 'n' in row: _check(type(row['n']) is int and 0 <= row['n'] <= 10**15)
            if 'otc' in row: _check(type(row['otc']) is bool)
        if row.get('otc', False): otc += 1; continue
        try: _ticker(symbol)
        except ValueError: unsupported += 1; continue
        accepted.append(_GroupedBar(symbol, timestamp, opened, high, low, closed, volume, vwap))
    return _GroupedSummary(requested_date, tuple(accepted), count, query_count, otc, unsupported)
