"""Supplied point-in-time context and exact higher-timeframe filters.

No provider selection, adjustment factors or strategy price levels are invented.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction

from .codes import ContextDataClassification, ReasonCode
from .config import StrategyConfig
from .market import IntervalClassification as Kind, MarketInterval, validate_timestamp
from .sessions import NEW_YORK, SessionCalendar, TradingSession


@dataclass(frozen=True, slots=True)
class ContextMinute:
    interval: MarketInterval
    available_at: datetime
    share_basis_id: str

    def __post_init__(self) -> None:
        validate_timestamp(self.available_at)
        if not self.share_basis_id:
            raise ValueError('Supply minute share-basis provenance')


@dataclass(frozen=True, slots=True)
class ContextBlock:
    start: datetime
    end: datetime
    classification: str
    constituents: tuple[ContextMinute, ...]
    failures: tuple[str, ...]
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume: Fraction | None = None


@dataclass(frozen=True, slots=True)
class IntradayContextResult:
    blocks: tuple[ContextBlock, ...]
    high_passed: bool | None
    low_passed: bool | None
    reason: ReasonCode | None


def intraday_context(minutes: tuple[ContextMinute, ...], session: TradingSession,
                     opening: datetime, security_id: str, share_basis_id: str,
                     config: StrategyConfig) -> IntradayContextResult:
    duration = timedelta(minutes=config.context_timeframe_minutes)
    completed = int((opening - session.open) // duration)
    if completed < config.context_completed_blocks:
        return IntradayContextResult((), None, None, ReasonCode.ENTRY_INSUFFICIENT_15M_CONTEXT)
    selected = []
    for index in range(completed - config.context_completed_blocks, completed):
        start, end = session.open + index * duration, session.open + (index + 1) * duration
        # Only source intervals in required, already-completed blocks matter.
        # Extra premarket, previous-session and forming/future data cannot substitute.
        relevant = tuple(item for item in minutes if start <= item.interval.timestamp < end)
        slots: dict[datetime, list[ContextMinute]] = {}
        for item in relevant:
            slots.setdefault(item.interval.timestamp, []).append(item)
        constituents = []
        failures = []
        for offset in range(config.context_timeframe_minutes):
            at = start + timedelta(minutes=offset)
            matches = slots.get(at, [])
            if len(matches) != 1:
                failures.append(f'{at.isoformat()}: missing or conflicting constituent')
                constituents.extend(matches)
                continue
            item = matches[0]
            constituents.append(item)
            bar = item.interval
            if bar.security_id != security_id:
                failures.append(f'{at.isoformat()}: incompatible security identity')
            if item.share_basis_id != share_basis_id:
                failures.append(f'{at.isoformat()}: incompatible share basis')
            if item.available_at < bar.end or item.available_at > opening:
                failures.append(f'{at.isoformat()}: trustworthy completed data unavailable at OPEN')
            if bar.classification in (Kind.MISSING, Kind.INVALID):
                failures.append(f'{at.isoformat()}: {bar.classification}: {bar.data_quality_reason}')
        if failures:
            block = ContextBlock(start, end, ContextDataClassification.INVALID_15M_DATA.value,
                                 tuple(constituents), tuple(failures))
        else:
            traded = [item.interval for item in constituents if item.interval.classification == Kind.TRADED]
            if not traded:
                block = ContextBlock(start, end, ContextDataClassification.NO_TRADE_15M.value,
                                     tuple(constituents), (), volume=Fraction())
            else:
                block = ContextBlock(start, end, 'TRADED_15M', tuple(constituents), (),
                                     traded[0].open, max(bar.high for bar in traded),
                                     min(bar.low for bar in traded), traded[-1].close,
                                     sum((Fraction(item.interval.volume) for item in constituents), Fraction()))
        selected.append(block)
    blocks = tuple(selected)
    if any(block.classification == ContextDataClassification.INVALID_15M_DATA for block in blocks):
        return IntradayContextResult(blocks, None, None, ReasonCode.ENTRY_15M_DATA_UNAVAILABLE)
    if any(block.classification == ContextDataClassification.NO_TRADE_15M for block in blocks):
        return IntradayContextResult(blocks, None, None, ReasonCode.ENTRY_15M_CONTEXT_NO_TRADE)
    oldest = blocks[-1 - config.context_comparison_lag_blocks]
    latest = blocks[-1]
    high_passed, low_passed = latest.high >= oldest.high, latest.low >= oldest.low
    return IntradayContextResult(blocks, high_passed, low_passed,
                                 None if high_passed and low_passed else ReasonCode.ENTRY_15M_STRUCTURE_BEARISH)


class WeeklyClassification(StrEnum):
    VALID = 'VALID'
    MISSING = 'MISSING'
    INVALID = 'INVALID'


@dataclass(frozen=True, slots=True)
class WeeklyBar:
    security_id: str
    week_start: date
    completed_at: datetime
    available_at: datetime
    source_id: str
    share_basis_id: str
    classification: WeeklyClassification
    high: Decimal | Fraction | None = None
    open: Decimal | Fraction | None = None
    low: Decimal | Fraction | None = None
    close: Decimal | Fraction | None = None
    data_quality_reason: str | None = None

    def __post_init__(self) -> None:
        for at in (self.completed_at, self.available_at):
            validate_timestamp(at)
        if type(self.week_start) is not date or self.week_start.weekday() != 0:
            raise ValueError('Weekly labels must use Monday dates')
        if not all((self.security_id, self.source_id, self.share_basis_id)):
            raise ValueError('Supply weekly security/source/basis identifiers')
        if not isinstance(self.classification, WeeklyClassification):
            raise TypeError('Supply an explicit weekly classification')
        if self.classification == WeeklyClassification.VALID:
            if (type(self.high) not in (Decimal, Fraction)
                    or (isinstance(self.high, Decimal) and not self.high.is_finite()) or self.high <= 0):
                raise ValueError('Valid weekly comparison requires trustworthy positive exact HIGH')
            for value in (self.open, self.low, self.close):
                if value is not None and (type(value) not in (Decimal, Fraction)
                                          or (isinstance(value, Decimal) and not value.is_finite())
                                          or value <= 0 or value > self.high):
                    raise ValueError('Invalid optional weekly OHLC')
            if self.low is not None and any(value is not None and value < self.low
                                            for value in (self.open, self.close)):
                raise ValueError('Invalid weekly LOW relationship')
        elif self.high is not None or not self.data_quality_reason:
            raise ValueError('Unknown weekly HIGH needs a recorded cause, not a trusted price')


@dataclass(frozen=True, slots=True)
class WeeklyHistory:
    listing_date: date
    listing_available_at: datetime
    listing_source_id: str
    bars: tuple[WeeklyBar, ...]

    def __post_init__(self) -> None:
        validate_timestamp(self.listing_available_at)
        if type(self.listing_date) is not date or not self.listing_source_id:
            raise ValueError('Supply verified point-in-time security listing history')


@dataclass(frozen=True, slots=True)
class WeeklyEvaluation:
    candidate_week: date
    left_weeks: tuple[date, ...]
    right_weeks: tuple[date, ...]
    status: str
    high: Decimal | Fraction | None


@dataclass(frozen=True, slots=True)
class WeeklyContextResult:
    candidate_weeks: tuple[date, ...]
    context_weeks: tuple[date, ...]
    bars: tuple[WeeklyBar, ...]
    evaluations: tuple[WeeklyEvaluation, ...]
    confirmed_highs: tuple[Decimal | Fraction, ...]
    nearest_resistance: Decimal | Fraction | None
    room_pct: Fraction | None
    reason: ReasonCode | None
    status: str
    failures: tuple[str, ...] = ()


def weekly_context(history: WeeklyHistory, calendar: SessionCalendar, opening: datetime,
                   entry_open: Decimal, security_id: str, share_basis_id: str,
                   config: StrategyConfig) -> WeeklyContextResult:
    """Enumerate real applicable weeks; missing supplied history cannot be skipped.

    Caller supplies verified listing identity and prices already normalized to
    the evaluation basis. This function never manufactures an adjustment.
    """
    if history.listing_available_at > opening:
        return WeeklyContextResult((), (), (), (), (), None, None,
                                   ReasonCode.ENTRY_WEEKLY_DATA_UNAVAILABLE, 'UNAVAILABLE',
                                   ('Listing metadata unavailable at OPEN',))
    current_week = opening.astimezone(NEW_YORK).date()
    current_week -= timedelta(days=current_week.weekday())
    first_week = history.listing_date - timedelta(days=history.listing_date.weekday())
    expected: list[tuple[date, datetime]] = []
    week = current_week - timedelta(days=7)
    while week >= first_week and len(expected) < config.weekly_max_candidate_weeks + config.weekly_swing_left_bars:
        sessions = [calendar.session_for(week + timedelta(days=day)) for day in range(5)]
        sessions = [session for session in sessions
                    if session is not None and session.trading_date >= history.listing_date]
        if sessions:
            expected.append((week, max(session.close for session in sessions)))
        week -= timedelta(days=7)
    expected.reverse()
    candidates = tuple(week for week, _ in expected[-config.weekly_max_candidate_weeks:])
    context = tuple(week for week, _ in expected if week not in candidates)
    if len(candidates) < config.weekly_min_candidate_history_weeks:
        return WeeklyContextResult(candidates, context, (), (), (), None, None,
                                   ReasonCode.ENTRY_INSUFFICIENT_WEEKLY_HISTORY, 'INSUFFICIENT_HISTORY')
    mapping: dict[date, list[WeeklyBar]] = {}
    expected_dates = {week for week, _ in expected}
    for bar in history.bars:
        if bar.week_start in expected_dates:
            mapping.setdefault(bar.week_start, []).append(bar)
    supplied = []
    failures = []
    incompatible_basis = False
    for week, close in expected:
        matches = mapping.get(week, [])
        if len(matches) != 1:
            failures.append(f'{week}: missing or conflicting weekly bar')
            supplied.extend(matches)
            continue
        bar = matches[0]
        supplied.append(bar)
        if bar.security_id != security_id:
            failures.append(f'{week}: incompatible security identity')
        if bar.share_basis_id != share_basis_id:
            failures.append(f'{week}: incompatible share basis')
            incompatible_basis = True
        if bar.completed_at != close or bar.available_at < close or bar.available_at > opening:
            failures.append(f'{week}: trustworthy completed weekly data unavailable at OPEN')
        if bar.classification != WeeklyClassification.VALID:
            failures.append(f'{week}: {bar.classification}: {bar.data_quality_reason}')
    if failures:
        return WeeklyContextResult(candidates, context, tuple(supplied), (), (), None, None,
                                   ReasonCode.CORPORATE_ACTION_DATA_UNAVAILABLE if incompatible_basis
                                   else ReasonCode.ENTRY_WEEKLY_DATA_UNAVAILABLE, 'UNAVAILABLE', tuple(failures))
    bars = tuple(supplied)
    evaluations = []
    highs = []
    for index, bar in enumerate(bars):
        if bar.week_start not in candidates:
            continue
        left = bars[max(0, index - config.weekly_swing_left_bars):index]
        right = bars[index + 1:index + 1 + config.weekly_swing_right_bars]
        if len(left) < config.weekly_swing_left_bars:
            status = 'NONEXISTENT_HISTORY'
        elif len(right) < config.weekly_swing_right_bars:
            status = 'UNCONFIRMED'
        elif (all(bar.high > item.high for item in left)
              and all(bar.high >= item.high for item in right)):
            status = 'CONFIRMED'
            highs.append(bar.high)
        else:
            status = 'NOT_SWING_HIGH'
        evaluations.append(WeeklyEvaluation(bar.week_start, tuple(item.week_start for item in left),
                                            tuple(item.week_start for item in right), status, bar.high))
    overhead = [high for high in highs if high > entry_open]
    nearest = min(overhead) if overhead else None
    room = ((Fraction(nearest) - Fraction(entry_open)) / Fraction(entry_open) * 100
            if nearest is not None else None)
    reason = ReasonCode.ENTRY_INSUFFICIENT_WEEKLY_ROOM if room is not None and room < config.weekly_min_room_pct else None
    return WeeklyContextResult(candidates, context, bars, tuple(evaluations), tuple(highs), nearest, room,
                               reason, 'RESISTANCE_IDENTIFIED' if nearest is not None
                               else ReasonCode.NO_IDENTIFIED_WEEKLY_RESISTANCE.value)
