"""Private offline grouped-history screening. No collection or trading authority."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from fractions import Fraction

from .discovery import _GroupedBar, _GroupedSummary, _MAX_ROWS
from .models import _check, _eastern, _instant

__all__ = ()

_MAX_SUMMARIES = 11
_MAX_TOTAL_ROWS = _MAX_SUMMARIES * _MAX_ROWS
_MAX_TARGET_TICKERS = _MAX_ROWS
_MAX_RETAINED_OBSERVATIONS = _MAX_TOTAL_ROWS
_LIMITATIONS = (
    'PRELIMINARY_RESEARCH_ONLY_NOT_FULL_EOD_STRATEGY_QUALIFICATION',
    'OBSERVED_DATES_NOT_VERIFIED_CONSECUTIVE_MARKET_SESSIONS',
    'RESPONSE_COVERAGE_IS_NOT_COMPLETE_US_MARKET_COVERAGE',
    'SPLIT_ADJUSTED_SOURCE_CONTRACT_NOT_COMMON_ADJUSTMENT_VINTAGE_PROOF',
    'NO_SPLIT_FACTOR_OR_CORPORATE_ACTION_CONTINUITY_PROOF',
    'INDEPENDENT_GROUPED_REQUESTS_NOT_AN_ATOMIC_SNAPSHOT',
    'MARKET_CAP_50_MILLION_UNVERIFIED',
    'INSTRUMENT_EXCHANGE_ACTIVE_LISTING_UNVERIFIED',
    'NO_SYNCHRONIZED_POINT_IN_TIME_REFERENCE_DATA',
    'HIGH_VERY_HIGH_VOLATILITY_UNVERIFIED',
    'ABCD_SETUP_UNQUALIFIED', 'NO_INTRADAY_MOMENTUM_OR_ENTRY_SIGNAL',
    'HISTORICAL_DATES_NOT_CURRENTNESS_OR_SESSION_COMPLETENESS_PROOF',
    'PERSONAL_NON_COMMERCIAL_RESEARCH_ONLY', 'NO_TRADING_AUTHORITY')


class _ScreenOutcome(Enum):
    PRELIMINARY_MATCH = 'PRELIMINARY_MATCH'
    PRELIMINARY_NOT_MET = 'PRELIMINARY_NOT_MET'
    INSUFFICIENT_HISTORY = 'INSUFFICIENT_HISTORY'


class _SourceQualification(Enum):
    SESSION_CONTINUITY_UNVERIFIED = 'SESSION_CONTINUITY_UNVERIFIED'
    SOURCE_INCOMPATIBLE = 'SOURCE_INCOMPATIBLE'


class _SourceIssue(Enum):
    INPUT_TYPE_INVALID = 'INPUT_TYPE_INVALID'
    SUMMARY_COUNT_INVALID = 'SUMMARY_COUNT_INVALID'
    TARGET_DATE_INVALID = 'TARGET_DATE_INVALID'
    SOURCE_TYPE_INVALID = 'SOURCE_TYPE_INVALID'
    ROW_LIMIT_EXCEEDED = 'ROW_LIMIT_EXCEEDED'
    SOURCE_VALUES_INVALID = 'SOURCE_VALUES_INVALID'
    DATE_ORDER_INVALID = 'DATE_ORDER_INVALID'
    TARGET_SOURCE_MISSING = 'TARGET_SOURCE_MISSING'
    TIMESTAMP_DATE_MISMATCH = 'TIMESTAMP_DATE_MISMATCH'
    SOURCE_IDENTITY_CONFLICT = 'SOURCE_IDENTITY_CONFLICT'


class _Criterion(Enum):
    PRICE = 'PRICE'
    OBSERVED_CHANGE = 'OBSERVED_CHANGE'
    AVERAGE_VOLUME_10 = 'AVERAGE_VOLUME_10'


class _HistoryGap(Enum):
    PREVIOUS_OBSERVATION_MISSING = 'PREVIOUS_OBSERVATION_MISSING'
    TEN_PRIOR_OBSERVATIONS_MISSING = 'TEN_PRIOR_OBSERVATIONS_MISSING'
    ZERO_BASELINE_VOLUME = 'ZERO_BASELINE_VOLUME'


@dataclass(frozen=True, slots=True, repr=False)
class _MultiDayFilters:
    max_price: Decimal = Decimal('5')
    min_change: Decimal = Decimal('3')
    min_average_volume: Decimal = Decimal('1000000')

    def __post_init__(self):
        for value, lower, upper in ((self.max_price, 0, 1000000),
                                   (self.min_change, -100, 100000),
                                   (self.min_average_volume, 0, 10**15)):
            _check(type(value) is Decimal and value.is_finite()
                   and -12 <= value.as_tuple().exponent <= 12
                   and len(value.as_tuple().digits) <= 28 and lower <= value <= upper)
        _check(self.max_price > 0)


@dataclass(frozen=True, slots=True, repr=False)
class _DatedObservation:
    requested_date: date
    bar: _GroupedBar


@dataclass(frozen=True, slots=True, repr=False)
class _DateCoverage:
    requested_date: date
    received_rows: int
    supported_rows: int
    excluded_otc: int
    excluded_unsupported: int


@dataclass(frozen=True, slots=True, repr=False)
class _TickerScreen:
    ticker: str
    target: _DatedObservation
    history: tuple[_DatedObservation, ...]
    price: Fraction
    change_pct: Fraction | None
    average_volume_10: Fraction | None
    relative_volume: Fraction | None
    outcome: _ScreenOutcome
    failed: tuple[_Criterion, ...]
    missing: tuple[_Criterion, ...]
    unavailable: tuple[_HistoryGap, ...]
    qualification: _SourceQualification = _SourceQualification.SESSION_CONTINUITY_UNVERIFIED
    limitations: tuple[str, ...] = _LIMITATIONS

    @property
    def previous(self):
        return self.history[-1] if self.history else None

    @property
    def baseline_dates(self):
        return tuple(item.requested_date for item in self.history) if len(self.history) == 10 else ()


@dataclass(frozen=True, slots=True, repr=False)
class _MultiDayScreen:
    target_date: date | None
    sources: tuple[_DateCoverage, ...]
    rows: tuple[_TickerScreen, ...]
    filters: _MultiDayFilters
    qualification: _SourceQualification
    reason: _SourceIssue | None
    limitations: tuple[str, ...] = _LIMITATIONS

    @property
    def source_dates(self):
        return tuple(source.requested_date for source in self.sources)

    @property
    def received_rows(self):
        if self.reason is not None: return None
        return sum(source.received_rows for source in self.sources)


class _IncompatibleSource(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('Incompatible multi-day source evidence')


def _require(condition, reason):
    if not condition: raise _IncompatibleSource(reason)


def _validate_sources(summaries, target_date):
    _require(type(target_date) is date and 2007 <= target_date.year <= 2099
             and target_date.weekday() < 5, _SourceIssue.TARGET_DATE_INVALID)
    _require(type(summaries) is tuple, _SourceIssue.INPUT_TYPE_INVALID)
    _require(1 <= len(summaries) <= _MAX_SUMMARIES, _SourceIssue.SUMMARY_COUNT_INVALID)
    total, previous = 0, None
    # Bound every collection before row traversal or allocating an index.
    for source in summaries:
        _require(type(source) is _GroupedSummary, _SourceIssue.SOURCE_TYPE_INVALID)
        _require(type(source.rows) is tuple and len(source.rows) <= _MAX_ROWS,
                 _SourceIssue.ROW_LIMIT_EXCEEDED)
        _require(type(source.received_count) is int and 0 <= source.received_count <= _MAX_ROWS,
                 _SourceIssue.ROW_LIMIT_EXCEEDED)
        total += source.received_count
        _require(total <= _MAX_TOTAL_ROWS, _SourceIssue.ROW_LIMIT_EXCEEDED)
        _require(type(source.requested_date) is date and 2007 <= source.requested_date.year <= 2099,
                 _SourceIssue.SOURCE_VALUES_INVALID)
        _require(previous is None or previous < source.requested_date, _SourceIssue.DATE_ORDER_INVALID)
        previous = source.requested_date
    _require(previous == target_date, _SourceIssue.TARGET_SOURCE_MISSING)
    # A last timestamp per ticker also detects duplicate source identity across
    # the decoder's permitted requested-date / following-midnight boundary.
    last_stamp, coverage = {}, []
    for source in summaries:
        # The legacy summary validator hashes identities before validating rows.
        # Reject substituted objects before any property/hash hook can execute.
        for bar in source.rows:
            _require(type(bar) is _GroupedBar and type(bar.ticker) is str and len(bar.ticker) <= 7,
                     _SourceIssue.SOURCE_VALUES_INVALID)
        source.__post_init__()
        for bar in source.rows:
            eastern = _eastern(_instant(bar.timestamp_ms))
            _require(eastern.date() == source.requested_date or
                     eastern.date() == source.requested_date + timedelta(days=1)
                     and eastern.hour == eastern.minute == eastern.second == eastern.microsecond == 0,
                     _SourceIssue.TIMESTAMP_DATE_MISMATCH)
            _require(bar.timestamp_ms > last_stamp.get(bar.ticker, 0), _SourceIssue.SOURCE_IDENTITY_CONFLICT)
            last_stamp[bar.ticker] = bar.timestamp_ms
        coverage.append(_DateCoverage(source.requested_date, source.received_count, len(source.rows),
                                      source.excluded_otc, source.excluded_unsupported))
    return tuple(coverage)


def _calculate(ticker, observations, filters):
    target, history = observations[-1], tuple(observations[:-1])
    price = Fraction(target.bar.close)
    change = average = relative = None
    unavailable = []
    if history:
        previous = Fraction(history[-1].bar.close)
        change = 100 * (price - previous) / previous
    else:
        unavailable.append(_HistoryGap.PREVIOUS_OBSERVATION_MISSING)
    if len(history) == 10:
        average = sum((Fraction(item.bar.volume) for item in history), Fraction()) / 10
        if average > 0: relative = Fraction(target.bar.volume) / average
        else: unavailable.append(_HistoryGap.ZERO_BASELINE_VOLUME)
    else:
        unavailable.append(_HistoryGap.TEN_PRIOR_OBSERVATIONS_MISSING)
    checks = ((_Criterion.PRICE, price, Fraction(filters.max_price)),
              (_Criterion.OBSERVED_CHANGE, change, Fraction(filters.min_change)),
              (_Criterion.AVERAGE_VOLUME_10, average, Fraction(filters.min_average_volume)))
    failed, missing = [], []
    for criterion, value, threshold in checks:
        if value is None: missing.append(criterion); continue
        passes = value < threshold if criterion is _Criterion.PRICE else value >= threshold if criterion is _Criterion.OBSERVED_CHANGE else value > threshold
        if not passes: failed.append(criterion)
    outcome = (_ScreenOutcome.PRELIMINARY_NOT_MET if failed else
               _ScreenOutcome.INSUFFICIENT_HISTORY if missing else _ScreenOutcome.PRELIMINARY_MATCH)
    return _TickerScreen(ticker, target, history, price, change, average, relative,
                         outcome, tuple(failed), tuple(missing), tuple(unavailable))


def _screen_grouped_history(summaries, target_date, *, filters=_MultiDayFilters()):
    """All-or-nothing source validation, then exact per-target-ticker research.

    Only PRE3D's split-adjusted _GroupedSummary model is supported. It cannot
    certify a common adjustment vintage, calendar coverage or present freshness.
    """
    try:
        _check(type(filters) is _MultiDayFilters)
        filters.__post_init__()
    except (ValueError, TypeError, ArithmeticError):
        raise ValueError('Invalid multi-day research filters') from None
    try:
        coverage = _validate_sources(summaries, target_date)
        targets = {bar.ticker: [] for bar in summaries[-1].rows}
        _require(len(targets) <= _MAX_TARGET_TICKERS, _SourceIssue.ROW_LIMIT_EXCEEDED)
        retained = 0
        for source in summaries:
            for bar in source.rows:
                if bar.ticker in targets:
                    retained += 1
                    _require(retained <= _MAX_RETAINED_OBSERVATIONS, _SourceIssue.ROW_LIMIT_EXCEEDED)
                    targets[bar.ticker].append(_DatedObservation(source.requested_date, bar))
        rows = tuple(_calculate(ticker, targets[ticker], filters) for ticker in sorted(targets))
        return _MultiDayScreen(target_date, coverage, rows, filters,
                               _SourceQualification.SESSION_CONTINUITY_UNVERIFIED, None)
    except _IncompatibleSource as error:
        reason = error.reason
    except Exception:
        reason = _SourceIssue.SOURCE_VALUES_INVALID
    # Invalid batches expose no prefix observations, metrics or coverage counts.
    return _MultiDayScreen(target_date if type(target_date) is date and 2007 <= target_date.year <= 2099 else None,
                           (), (), filters, _SourceQualification.SOURCE_INCOMPATIBLE, reason)
