"""Pure, exact EOD research metrics. No strategy qualification or trading authority."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction

from .models import _DailySeries, _DailySource, _check, _utc
from .reference import _TickerReference

__all__ = ()

_UNQUALIFIED = ('VOLATILITY_HIGH_VERY_HIGH_UNVERIFIED', 'ABCD_SETUP_UNQUALIFIED',
                'INTRADAY_MOMENTUM_UNAVAILABLE', 'FULL_STRATEGY_QUALIFICATION_NOT_AVAILABLE')
_METRICS = ('price', 'change_pct', 'volume', 'average_volume_10', 'relative_volume', 'market_cap')


class _ResearchOutcome(str, Enum):
    MET = 'EOD CRITERIA MET'
    NOT_MET = 'EOD CRITERIA NOT MET'
    INSUFFICIENT = 'INSUFFICIENT DATA'


def _ratio(value, places=2):
    """Exact ratio plus half-even rounded display; independent of Decimal context."""
    if value is None: return None
    _check(type(value) is Fraction)
    scale = 10 ** places
    whole, rest = divmod(abs(value.numerator) * scale, value.denominator)
    if 2 * rest > value.denominator or 2 * rest == value.denominator and whole % 2:
        whole += 1
    digits = str(whole).zfill(places + 1)
    display = ('-' if value < 0 and whole else '') + digits[:-places] + '.' + digits[-places:]
    return {'numerator': str(value.numerator), 'denominator': str(value.denominator), 'display': display}


@dataclass(frozen=True, slots=True, repr=False)
class _EODMetrics:
    source: _DailySeries | None
    reference: _TickerReference | None
    price: Fraction | None
    change_pct: Fraction | None
    volume: Fraction | None
    average_volume_10: Fraction | None
    relative_volume: Fraction | None
    market_cap: Fraction | None

    def __post_init__(self):
        _check(self.source is None or type(self.source) is _DailySeries)
        _check(self.reference is None or type(self.reference) is _TickerReference)
        for name in _METRICS:
            value = getattr(self, name)
            _check(value is None or type(value) is Fraction)
            if value is not None:
                _check(value >= -100 if name == 'change_pct' else
                       value > 0 if name in ('price', 'market_cap') else value >= 0)

    def payload(self):
        self.__post_init__()
        return {name: _ratio(getattr(self, name),
                    12 if name == 'price' and self.price is not None and self.price < Fraction(1,10000)
                    else 4 if name == 'price' else 2) for name in _METRICS}


def _calculate(source, now, reference=None):
    _utc(now)
    _check(source is None or type(source) is _DailySeries)
    _check(reference is None or type(reference) is _TickerReference)
    if reference is not None: reference.__post_init__()
    if source is not None:
        source.__post_init__()
        _check(source.source is _DailySource.HISTORY)
    # Preserve PRE1's conservative whole-series validation/availability contract.
    usable = source is not None and source.payload(now)['state'] == 'REAL END-OF-DAY'
    price = change = volume = average = relative = cap = None
    if usable:
        latest = source.bars[-1]
        price, volume = Fraction(latest.close), Fraction(latest.volume)
        if len(source.bars) >= 2:
            previous = Fraction(source.bars[-2].close)
            change = 100 * (price / previous - 1)
        if len(source.bars) >= 11:
            average = sum((Fraction(bar.volume) for bar in source.bars[-11:-1]), Fraction()) / 10
            if average > 0: relative = volume / average
        if (reference is not None and reference.ticker == source.ticker
            and reference.requested_date == latest.session_date
            and reference.payload(now)['state'] == 'REFERENCE AVAILABLE'):
            cap = Fraction(reference.market_cap)
    return _EODMetrics(source, reference, price, change, volume, average, relative, cap)


@dataclass(frozen=True, slots=True, repr=False)
class _EODFilters:
    max_price: Decimal = Decimal('5')
    min_change: Decimal = Decimal('3')
    min_market_cap: Decimal = Decimal('50000000')
    min_average_volume: Decimal = Decimal('1000000')
    min_relative_volume: Decimal | None = None
    require_market_cap: bool = True

    def __post_init__(self):
        _check(type(self.require_market_cap) is bool)
        for name, lower, upper in (('max_price', 0, 1000000), ('min_change', -100, 100000),
                                   ('min_market_cap', 0, 10 ** 15), ('min_average_volume', 0, 10 ** 15),
                                   ('min_relative_volume', 0, 1000000)):
            value = getattr(self, name)
            if name == 'min_relative_volume' and value is None: continue
            _check(type(value) is Decimal and value.is_finite()
                   and -12 <= value.as_tuple().exponent <= 12 and len(value.as_tuple().digits) <= 28
                   and lower <= value <= upper)
        _check(self.max_price > 0)


def _assess(metrics, filters):
    _check(type(metrics) is _EODMetrics and type(filters) is _EODFilters)
    metrics.__post_init__()
    filters.__post_init__()
    checks = [('price', filters.max_price, False), ('change_pct', filters.min_change, True),
              ('average_volume_10', filters.min_average_volume, False)]
    if filters.require_market_cap: checks.append(('market_cap', filters.min_market_cap, True))
    if filters.min_relative_volume is not None:
        checks.append(('relative_volume', filters.min_relative_volume, True))
    failed, missing = [], []
    for name, threshold, inclusive in checks:
        value = getattr(metrics, name)
        if value is None: missing.append(name); continue
        threshold = Fraction(threshold)
        passes = value < threshold if name == 'price' else value >= threshold if inclusive else value > threshold
        if not passes: failed.append(name)
    outcome = _ResearchOutcome.NOT_MET if failed else _ResearchOutcome.INSUFFICIENT if missing else _ResearchOutcome.MET
    return outcome, tuple(failed), tuple(missing)


def _research_row(ticker, history_record, reference_record, now):
    source, retrieved = history_record if history_record is not None else (None, None)
    reference, reference_retrieved = reference_record if reference_record is not None else (None, None)
    history_time_valid = retrieved is None or _utc(retrieved) <= _utc(now)
    reference_time_valid = reference_retrieved is None or _utc(reference_retrieved) <= _utc(now)
    values = _calculate(source if history_time_valid else None, now,
                        reference if reference_time_valid else None)
    history = source.payload(now) if source is not None and history_time_valid else None
    ref = reference.payload(now) if reference is not None else None
    if not history_time_valid:
        history = {'state': 'UNAVAILABLE', 'reason': 'RETRIEVAL_TIME_CONFLICT'}
    if ref is not None and not reference_time_valid:
        ref.update({'state': 'UNAVAILABLE', 'reason': 'RETRIEVAL_TIME_CONFLICT'})
    if ref is not None: ref['retrieved_at'] = _utc(reference_retrieved).isoformat()
    outcome, failed, missing = _assess(values, _EODFilters())
    return {'ticker': ticker, 'demo': False, 'provider': 'Massive', 'metrics': values.payload(),
            'history_loaded': source is not None,
            'history_eligible': history is not None and history['state'] == 'REAL END-OF-DAY',
            'history_state': 'UNLOADED' if history is None else history['state'],
            'history_reason': 'HISTORY_NOT_LOADED' if history is None else history['reason'],
            'history_retrieved_at': None if retrieved is None else _utc(retrieved).isoformat(),
            'market_date': source.bars[-1].session_date.isoformat() if source is not None and source.bars else None,
            'adjusted_for_splits': None if source is None else source.adjusted,
            'source_timestamps_ms': [] if source is None else [bar.timestamp_ms for bar in source.bars],
            'close_dates': [] if source is None else [bar.session_date.isoformat() for bar in source.bars[-2:]],
            'baseline_dates': [] if source is None or len(source.bars) < 11 else
                [bar.session_date.isoformat() for bar in source.bars[-11:-1]],
            'reference': ref, 'reference_eligible': values.market_cap is not None,
            'reference_alignment': 'REQUEST_DATE_MATCHES_BAR_DATE_NOT_SYNCHRONOUS'
                if values.market_cap is not None else 'UNAVAILABLE_OR_DATE_MISMATCH',
            'default_outcome': outcome.value, 'failed': list(failed), 'missing': list(missing),
            'unqualified_requirements': list(_UNQUALIFIED),
            'full_strategy_qualification': 'NOT AVAILABLE',
            'coverage': 'AVAILABLE_BARS_ONLY_NO_MARKET_CALENDAR_CONTINUITY_PROOF'}
