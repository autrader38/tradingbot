"""Closed, source-only daily aggregate values. No scanner calculations."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import json
import re

__all__ = ()

_MISSING_SCANNER = ('market_cap', 'average_volume_10d', 'relative_volume',
                    'volatility', 'setup_score', 'intraday_price_change', 'ABCD_signal')
_QUALITY = ('SOURCE_TIMESTAMP_IS_AGGREGATE_WINDOW_START',
            'DAILY_ET_WINDOW_MAY_INCLUDE_EXTENDED_HOURS',
            'END_OF_DAY_IS_NOT_INTRADAY', 'RECENCY_IS_NOT_SESSION_COMPLETENESS',
            'NOT_A_SCANNER_RESULT_OR_TRADING_SIGNAL')
_FIELDS = ('open', 'high', 'low', 'close', 'volume')


class _Availability(str, Enum):
    EOD = 'REAL END-OF-DAY'
    DELAYED = 'DELAYED'
    STALE = 'STALE'
    UNAVAILABLE = 'UNAVAILABLE'


def _check(condition):
    if not condition:
        raise ValueError('Invalid market observation')


def _ticker(value):
    _check(type(value) is str and re.fullmatch(r'[A-Z]{1,5}(?:\.[A-Z])?', value) is not None)
    return value


def _watchlist(value):
    _check(type(value) is tuple and 1 <= len(value) <= 5)
    for symbol in value: _ticker(symbol)
    _check(len(set(value)) == len(value))
    return value


def _utc(value):
    _check(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc)


def _eastern(value):
    """US Eastern post-2007 rules, without a Windows tzdata dependency.

    Used only to interpret modern daily ET aggregate windows, not as a market
    calendar. No holiday, early-close, regular-session or completeness claim.
    """
    value = _utc(value)
    _check(2007 <= value.year <= 2099)
    march = datetime(value.year, 3, 1, 7, tzinfo=timezone.utc)
    november = datetime(value.year, 11, 1, 6, tzinfo=timezone.utc)
    start = march + timedelta(days=(6 - march.weekday()) % 7 + 7)
    end = november + timedelta(days=(6 - november.weekday()) % 7)
    offset = -4 if start <= value < end else -5
    return value.astimezone(timezone(timedelta(hours=offset)))


def _instant(milliseconds):
    _check(type(milliseconds) is int and 1_167_609_600_000 <= milliseconds < 4_102_444_800_000)
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=milliseconds)


def _numeric(value, *, volume=False):
    _check(type(value) in (int, Decimal))
    value = Decimal(value)
    _check(value.is_finite() and -12 <= value.as_tuple().exponent <= 12
           and len(value.as_tuple().digits) <= 28
           and (value >= 0 if volume else value > 0)
           and value <= (Decimal('1e15') if volume else Decimal('1e12')))
    return value


@dataclass(frozen=True, slots=True, repr=False)
class _DailyBar:
    ticker: str
    timestamp_ms: int
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal | None

    def __post_init__(self):
        _ticker(self.ticker)
        eastern = _eastern(_instant(self.timestamp_ms))
        _check(eastern.hour == eastern.minute == eastern.second == eastern.microsecond == 0)
        _check(eastern.weekday() < 5)
        for field in _FIELDS:
            value = getattr(self, field)
            if value is not None:
                _check(type(value) is Decimal)
                _numeric(value, volume=field == 'volume')
        for value in (self.open, self.close, self.low):
            if value is not None and self.high is not None: _check(self.high >= value)
        for value in (self.open, self.close, self.high):
            if value is not None and self.low is not None: _check(self.low <= value)

    @property
    def session_date(self):
        return _eastern(_instant(self.timestamp_ms)).date()

    def payload(self):
        self.__post_init__()
        return {'ticker': self.ticker, 'source_timestamp_ms': self.timestamp_ms,
                'window_start_at': _instant(self.timestamp_ms).isoformat(),
                'session_date': self.session_date.isoformat(),
                **{field: None if getattr(self, field) is None else str(getattr(self, field))
                   for field in _FIELDS}}


@dataclass(frozen=True, slots=True, repr=False)
class _DailySeries:
    ticker: str
    bars: tuple[_DailyBar, ...]
    adjusted: bool | None
    delayed: bool

    def __post_init__(self):
        _ticker(self.ticker)
        _check(self.adjusted is None or type(self.adjusted) is bool)
        _check(type(self.delayed) is bool and type(self.bars) is tuple and len(self.bars) <= 64)
        previous = 0
        for bar in self.bars:
            _check(type(bar) is _DailyBar and bar.ticker == self.ticker and bar.timestamp_ms > previous)
            bar.__post_init__()
            previous = bar.timestamp_ms

    def payload(self, now):
        self.__post_init__()
        today = _eastern(now).date()
        missing = sorted({field for bar in self.bars for field in _FIELDS if getattr(bar, field) is None})
        if self.adjusted is None: missing.append('adjustment_status')
        age = (today - self.bars[-1].session_date).days if self.bars else None
        state, reason = _Availability.EOD, 'VALIDATED_DAILY_SOURCE'
        if not self.bars: state, reason = _Availability.UNAVAILABLE, 'NO_BAR_AVAILABLE'
        elif age < 0: raise ValueError('Invalid market observation')
        elif missing: state, reason = _Availability.UNAVAILABLE, 'MISSING_SOURCE_FIELDS'
        elif self.adjusted is not True: state, reason = _Availability.UNAVAILABLE, 'ADJUSTMENT_MISMATCH'
        elif age > 4: state, reason = _Availability.STALE, 'OLDER_THAN_FOUR_CALENDAR_DAYS'
        elif self.delayed or age == 0: state, reason = _Availability.DELAYED, 'END_OF_DAY_NOT_ESTABLISHED'
        return {'state': state.value, 'reason': reason, 'ticker': self.ticker,
                'provider': 'Massive', 'entitlement': 'Stocks Basic · end-of-day',
                'provider_status': 'DELAYED' if self.delayed else 'OK',
                'demo': False, 'adjusted_for_splits': self.adjusted,
                'market_timezone': 'America/New_York',
                'market_session': 'Provider daily ET aggregate; may include extended-hours qualifying trades',
                'timestamp_semantics': 'Aggregate window start; not economic event or receipt time',
                'age_calendar_days': age, 'missing_fields': missing,
                'missing_scanner_fields': list(_MISSING_SCANNER), 'quality': list(_QUALITY),
                'bars': [bar.payload() for bar in self.bars]}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _check(key not in result)
        result[key] = value
    return result


def _constant(_):
    raise ValueError('Invalid market observation')


def _decode(body, ticker, view, now, start=None, end=None):
    _ticker(ticker)
    _check(type(body) is bytes and len(body) <= 262144 and view in ('previous', 'history'))
    raw = json.loads(body.decode('utf-8'), parse_float=Decimal,
                     parse_constant=_constant, object_pairs_hook=_pairs)
    _check(type(raw) is dict and raw.get('ticker') == ticker and raw.get('status') in ('OK', 'DELAYED'))
    count = raw.get('resultsCount')
    _check(type(count) is int and 0 <= count <= (1 if view == 'previous' else 64))
    rows = raw.get('results', [])
    _check(type(rows) is list and len(rows) == count and not raw.get('next_url'))
    adjusted = raw.get('adjusted')
    _check(adjusted is None or type(adjusted) is bool)
    bars = []
    for row in rows:
        _check(type(row) is dict)
        values = [None if row.get(key) is None else _numeric(row[key], volume=key == 'v')
                  for key in ('o', 'h', 'l', 'c', 'v')]
        bar = _DailyBar(ticker, row.get('t'), *values)
        _check(_instant(bar.timestamp_ms) <= _utc(now))
        if view == 'history': _check(start <= bar.session_date <= end)
        bars.append(bar)
    return _DailySeries(ticker, tuple(bars), adjusted, raw['status'] == 'DELAYED')
