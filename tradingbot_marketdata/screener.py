"""Bounded PRE4C projection/view of PRE4A evidence. No I/O or provider work."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
import re
from sys import getsizeof

from .models import _check, _instant
from .multiday import _MultiDayFilters, _MultiDayScreen, _ScreenOutcome
from .research import _ratio

__all__ = ()

_MAX_SCREEN_BYTES = 64 * 1024 * 1024
_MAX_SCREEN_ROWS = 30_000
_MAX_SCREEN_BODY = 256 * 1024
_PAGE_SIZE = 50
_SORTS = ('change', 'price', 'price_desc', 'average', 'relative', 'volume', 'symbol')
_OUTCOMES = tuple(item.value for item in _ScreenOutcome)


@dataclass(frozen=True, slots=True, repr=False)
class _ScreenFilters:
    base: _MultiDayFilters
    min_relative: Decimal | None = None

    def __post_init__(self):
        _check(type(self.base) is _MultiDayFilters)
        self.base.__post_init__()
        value = self.min_relative
        _check(value is None or type(value) is Decimal and value.is_finite()
               and -12 <= value.as_tuple().exponent <= 12 and len(value.as_tuple().digits) <= 28
               and 0 <= value <= 1000000)

    def payload(self):
        # Echo the validated plain-decimal controls without exponent notation or
        # rounding. Decimal formatting retains supplied scale and signed zero.
        return {'price': format(self.base.max_price, 'f'), 'change': format(self.base.min_change, 'f'),
                'average': format(self.base.min_average_volume, 'f'),
                'relative': None if self.min_relative is None else format(self.min_relative, 'f')}


def _screen_filters(price='5', change='3', average='1000000', relative=''):
    def number(value):
        _check(type(value) is str and len(value) <= 32
               and re.fullmatch(r'-?(?:0|[1-9]\d*)(?:\.\d{1,12})?', value, re.ASCII))
        return Decimal(value)
    _check(type(relative) is str)
    return _ScreenFilters(_MultiDayFilters(number(price), number(change), number(average)),
                          None if relative == '' else number(relative))


@dataclass(frozen=True, slots=True, repr=False)
class _ScreenRow:
    ticker: str
    source_close: str
    price: Fraction
    change: Fraction | None
    average: Fraction | None
    relative: Fraction | None
    volume: Fraction
    source_timestamp_ms: int
    prior_dates: tuple
    outcome: str
    failed: tuple[str, ...]
    missing: tuple[str, ...]
    unavailable: tuple[str, ...]

    def payload(self, target):
        return {'ticker': self.ticker, 'target_date': target.isoformat(),
                'source_close': self.source_close, 'source_at': _instant(self.source_timestamp_ms).isoformat(),
                'price': _ratio(self.price, 12), 'change': _ratio(self.change),
                'average': _ratio(self.average), 'relative': _ratio(self.relative), 'volume': _ratio(self.volume),
                'previous_observation_date': self.prior_dates[-1].isoformat() if self.prior_dates else None,
                'prior_observation_dates': [day.isoformat() for day in self.prior_dates],
                'volume_baseline_dates': [day.isoformat() for day in self.prior_dates] if len(self.prior_dates) == 10 else [],
                'outcome': self.outcome, 'failed': list(self.failed), 'missing': list(self.missing),
                'unavailable': list(self.unavailable), 'market_cap_qualification': 'UNVERIFIED',
                'session_continuity': 'UNVERIFIED', 'adjustment_vintage': 'UNVERIFIED'}


def _row_charge(row):
    # Deliberately double-count immutable aliases and date references. No source
    # bars/history wrappers or complete PRE4A result are retained here.
    size = getsizeof(row)
    for name in row.__slots__:
        value = getattr(row, name)
        size += getsizeof(value)
        if type(value) is Fraction: size += getsizeof(value.numerator) + getsizeof(value.denominator)
        if type(value) is tuple: size += sum(getsizeof(item) for item in value)
    return max(2048, size)


@dataclass(frozen=True, slots=True, repr=False)
class _ScreenProjection:
    evaluation_id: int
    target: date
    filters: _ScreenFilters
    sources: tuple
    rows: tuple[_ScreenRow, ...]
    counts: tuple[int, ...]
    limitations: tuple[str, ...]
    charged_bytes: int


def _project_screen(evidence, filters, evaluation_id, sources):
    _check(type(evidence) is _MultiDayScreen and evidence.reason is None)
    _check(type(filters) is _ScreenFilters and filters.base == evidence.filters)
    filters.__post_init__()
    _check(type(evaluation_id) is int and 1 <= evaluation_id <= 2**63-1)
    _check(type(evidence.rows) is tuple and len(evidence.rows) <= _MAX_SCREEN_ROWS)
    _check(type(sources) is tuple and len(sources) <= 11)
    rows, counts, charged = [], [0, 0, 0], 8192
    for source in evidence.rows:
        failed = tuple(item.value for item in source.failed)
        missing = tuple(item.value for item in source.missing)
        # PRE4A remains authoritative for all calculations and its three filters.
        # This additional optional comparison consumes its exact RVOL Fraction.
        if filters.min_relative is not None:
            if source.relative_volume is None: missing += ('RELATIVE_VOLUME',)
            elif source.relative_volume < Fraction(filters.min_relative): failed += ('RELATIVE_VOLUME',)
        outcome = _OUTCOMES[1] if failed else _OUTCOMES[2] if missing else _OUTCOMES[0]
        row = _ScreenRow(source.ticker, str(source.target.bar.close), source.price,
                         source.change_pct, source.average_volume_10, source.relative_volume,
                         Fraction(source.target.bar.volume), source.target.bar.timestamp_ms,
                         tuple(item.requested_date for item in source.history), outcome,
                         failed, missing, tuple(item.value for item in source.unavailable))
        charged += _row_charge(row) + 16  # Tuple/list pointers during construction.
        if charged > _MAX_SCREEN_BYTES: raise MemoryError('Screen projection limit')
        rows.append(row); counts[_OUTCOMES.index(outcome)] += 1
    return _ScreenProjection(evaluation_id, evidence.target_date, filters, sources, tuple(rows),
                             tuple(counts), evidence.limitations, charged)


def _screen_metadata(snapshot):
    return {'state': 'HISTORICAL_SCREENING_RESULT', 'provider': 'Massive', 'demo': False,
            'evaluation_id': snapshot.evaluation_id, 'target_date': snapshot.target.isoformat(),
            'filters': snapshot.filters.payload(), 'target_tickers': len(snapshot.rows),
            'counts': dict(zip(_OUTCOMES, snapshot.counts)),
            'sources': [{'requested_date': day, 'retrieved_at': retrieved,
                         'received_rows': received, 'validated_rows': validated}
                        for day, retrieved, received, validated in snapshot.sources],
            'limitations': list(snapshot.limitations), 'market_cap_qualification': 'UNVERIFIED',
            'full_strategy_qualification': 'NOT AVAILABLE', 'page_size': _PAGE_SIZE,
            'charged_projection_bytes': snapshot.charged_bytes,
            'maximum_projection_bytes': _MAX_SCREEN_BYTES}


def _screen_page(snapshot, *, search='', sort='change', outcome='ALL', page=0):
    _check(type(snapshot) is _ScreenProjection)
    _check(type(search) is str and len(search) <= 7 and re.fullmatch(r'[A-Za-z.]*', search, re.ASCII) is not None)
    _check(type(sort) is str and sort in _SORTS)
    _check(type(outcome) is str and outcome in ('ALL', *_OUTCOMES))
    _check(type(page) is int and 0 <= page < _MAX_SCREEN_ROWS // _PAGE_SIZE)
    rows = [row for row in snapshot.rows if search.upper() in row.ticker and (outcome == 'ALL' or row.outcome == outcome)]
    def key(row):
        value = getattr(row, sort) if sort in ('change', 'average', 'relative', 'volume') else row.price
        if sort == 'symbol': return (False, 0, row.ticker)
        return (value is None, 0 if value is None else value if sort == 'price' else -value, row.ticker)
    rows.sort(key=key)
    pages = max(1, (len(rows)+_PAGE_SIZE-1)//_PAGE_SIZE)
    page = min(page, pages-1)
    return {**_screen_metadata(snapshot), 'matched_view_rows': len(rows), 'page': page,
            'pages': pages, 'search': search.upper(), 'sort': sort, 'outcome_filter': outcome,
            'rows': [row.payload(snapshot.target) for row in rows[page*_PAGE_SIZE:(page+1)*_PAGE_SIZE]]}
