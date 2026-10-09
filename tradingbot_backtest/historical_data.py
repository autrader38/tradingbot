"""Provider-neutral, explicitly verified historical inputs; no network clients."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from typing import Protocol

from .entry_context import WeeklyBar
from .market import MarketInterval, validate_timestamp
from .sessions import SessionCalendar
from .trade_construction import TickSource


class SecurityType(StrEnum):
    COMMON_STOCK = 'COMMON_STOCK'
    ETF = 'ETF'
    ETN = 'ETN'
    CLOSED_END_FUND = 'CLOSED_END_FUND'
    MUTUAL_FUND = 'MUTUAL_FUND'
    PREFERRED = 'PREFERRED'
    WARRANT = 'WARRANT'
    RIGHT = 'RIGHT'
    UNIT = 'UNIT'
    OPTION = 'OPTION'
    BOND = 'BOND'
    CRYPTO = 'CRYPTO'
    ADR = 'ADR'
    OTHER = 'OTHER'


class Adjustment(StrEnum):
    RAW = 'RAW'
    VERIFIED_POINT_IN_TIME = 'VERIFIED_POINT_IN_TIME'
    UNKNOWN = 'UNKNOWN'
    UNSAFE = 'UNSAFE'


@dataclass(frozen=True, slots=True)
class Provenance:
    source_id: str
    available_at: datetime
    share_basis_id: str
    adjustment: Adjustment
    verified: bool
    quality_reason: str | None = None

    def __post_init__(self):
        validate_timestamp(self.available_at)
        if not self.source_id or not self.share_basis_id:
            raise ValueError('Source and share basis must be explicit')
        if not isinstance(self.adjustment, Adjustment) or type(self.verified) is not bool:
            raise TypeError('Supply explicit adjustment methodology and verification')


@dataclass(frozen=True, slots=True)
class SecurityReference:
    security_id: str
    ticker: str
    valid_from: datetime
    valid_until: datetime | None
    available_at: datetime
    listing_date: date
    listing_available_at: datetime
    delisting_at: datetime | None
    listing_venue: str
    security_type: SecurityType
    us_listed: bool
    supported_exchange: bool
    otc: bool
    verified: bool
    source_id: str

    def __post_init__(self):
        for value in (self.valid_from, self.available_at, self.listing_available_at,
                      self.valid_until, self.delisting_at):
            if value is not None:
                validate_timestamp(value)
        if not all((self.security_id, self.ticker, self.listing_venue, self.source_id)):
            raise ValueError('Security identity and classification provenance required')
        if not isinstance(self.security_type, SecurityType):
            raise TypeError('Never infer security type from a symbol')
        if any(type(value) is not bool for value in
               (self.us_listed, self.supported_exchange, self.otc, self.verified)):
            raise TypeError('Listing verification must be explicit')
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError('Invalid security-reference applicability interval')


@dataclass(frozen=True, slots=True)
class SplitAction:
    security_id: str
    old_basis: str
    new_basis: str
    factor: Fraction
    effective_at: datetime
    available_at: datetime
    source_id: str
    verified: bool
    simple_share_denomination: bool

    def __post_init__(self):
        validate_timestamp(self.effective_at)
        validate_timestamp(self.available_at)
        if type(self.verified) is not bool or type(self.simple_share_denomination) is not bool:
            raise TypeError('Corporate-action verification flags must be actual booleans')
        if type(self.factor) is not Fraction or self.factor <= 0:
            raise ValueError('Verified positive exact new-shares/old-shares factor required')
        if not all((self.security_id, self.old_basis, self.new_basis, self.source_id)):
            raise ValueError('Corporate-action identity/provenance required')


@dataclass(frozen=True, slots=True)
class DailySession:
    security_id: str
    trading_date: date
    completed_at: datetime
    provenance: Provenance
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal | None
    trustworthy: bool
    quality_reason: str | None = None

    def __post_init__(self):
        validate_timestamp(self.completed_at)
        _identity(self.security_id, self.trading_date)
        _financial(self.high, self.low, self.close, self.volume)
        if type(self.trustworthy) is not bool:
            raise TypeError('Explicit session verification required')


@dataclass(frozen=True, slots=True)
class PriorClose:
    security_id: str
    trading_date: date
    price: Decimal | None
    provenance: Provenance
    official_regular_close: bool

    def __post_init__(self):
        _identity(self.security_id, self.trading_date)
        _financial(self.price)
        if type(self.official_regular_close) is not bool:
            raise TypeError('Explicit official RTH-close verification required')


@dataclass(frozen=True, slots=True)
class MarketCapitalization:
    security_id: str
    valid_from: datetime
    valid_until: datetime
    available_at: datetime
    source_id: str
    verified: bool
    authoritative_value: Decimal | None = None
    shares_outstanding: Decimal | None = None
    compatible_price: Decimal | None = None
    shares_basis_id: str | None = None
    price_basis_id: str | None = None
    share_count_point_in_time_verified: bool = False
    quality_reason: str | None = None

    def __post_init__(self):
        for at in (self.valid_from, self.valid_until, self.available_at):
            validate_timestamp(at)
        if not self.security_id or not self.source_id or self.valid_until <= self.valid_from:
            raise ValueError('Explicit cap identity and applicability interval required')
        _financial(self.authoritative_value, self.shares_outstanding, self.compatible_price)
        if type(self.verified) is not bool or type(self.share_count_point_in_time_verified) is not bool:
            raise TypeError('Explicit market-cap verification required')


@dataclass(frozen=True, slots=True)
class HistoricalMinute:
    interval: MarketInterval
    opening_available_at: datetime
    completed_available_at: datetime
    provenance: Provenance

    def __post_init__(self):
        validate_timestamp(self.opening_available_at)
        validate_timestamp(self.completed_available_at)
        if self.opening_available_at < self.interval.timestamp or self.completed_available_at < self.interval.end:
            raise ValueError('Minute information cannot be available before its event')


@dataclass(frozen=True, slots=True)
class SessionBasis:
    security_id: str
    trading_date: date
    basis_id: str
    effective_at: datetime
    available_at: datetime
    source_id: str
    verified: bool

    def __post_init__(self):
        _identity(self.security_id, self.trading_date)
        for at in (self.effective_at, self.available_at):
            validate_timestamp(at)
        if not self.basis_id or not self.source_id or type(self.verified) is not bool:
            raise ValueError('Verified session basis must be explicit')


@dataclass(frozen=True, slots=True)
class HistoricalWeekly:
    bar: WeeklyBar
    provenance: Provenance

    def __post_init__(self):
        if self.provenance.share_basis_id != self.bar.share_basis_id:
            raise ValueError('Weekly provenance and supplied units conflict')


def _identity(security: str, day: date) -> None:
    if not security or type(day) is not date:
        raise ValueError('Explicit security identity and session date required')


def _financial(*values: Decimal | None) -> None:
    if any(v is not None and type(v) is not Decimal for v in values):
        raise TypeError('Historical financial observations require Decimal or unavailable None')


class HistoricalDataset(Protocol):
    """All historical identities, including inactive/delisted, not today's list.

    Calendar non-sessions and tick schedules must be verified independently.
    Record tuples retain classifications and source availability. Missing
    records are never evidence of NO_TRADE. A provider adapter must document
    listing/adjustment/basis/cap applicability; the engine supplies no defaults.
    """
    dataset_id: str
    source_version: str
    calendar: SessionCalendar
    ticks: TickSource
    calendar_version: str
    tick_version: str

    def security_references(self) -> tuple[SecurityReference, ...]: ...
    def session_basis(self, security_id: str, day: date) -> SessionBasis | None: ...
    def daily_sessions(self, security_id: str) -> tuple[DailySession, ...]: ...
    def prior_closes(self, security_id: str) -> tuple[PriorClose, ...]: ...
    def market_caps(self, security_id: str) -> tuple[MarketCapitalization, ...]: ...
    def split_actions(self, security_id: str) -> tuple[SplitAction, ...]: ...
    def minutes(self, security_id: str, day: date) -> tuple[HistoricalMinute, ...]: ...
    def weekly_history(self, security_id: str, day: date) -> tuple[HistoricalWeekly, ...]: ...


class HistoricalInputError(ValueError):
    """Unresolved source/contract behavior; never guessed by an adapter."""


@dataclass(frozen=True, slots=True)
class InMemoryDataset:
    """Offline reference implementation, retaining every historical identity.

    All financial/calendar/tick observations are supplied, never downloaded or
    synthesized. Conflicting identity/basis records fail the input contract.
    """
    dataset_id: str
    source_version: str
    calendar: SessionCalendar
    ticks: TickSource
    calendar_version: str
    tick_version: str
    references: tuple[SecurityReference, ...]
    bases: tuple[SessionBasis, ...]
    daily: tuple[DailySession, ...]
    closes: tuple[PriorClose, ...]
    caps: tuple[MarketCapitalization, ...]
    actions: tuple[SplitAction, ...]
    minute_records: tuple[HistoricalMinute, ...]
    weekly_records: tuple[HistoricalWeekly, ...]

    def security_references(self):
        return self.references

    def session_basis(self, security_id, day):
        matches = [r for r in self.bases if r.security_id == security_id and r.trading_date == day]
        if len(matches) > 1:
            raise HistoricalInputError('Conflicting session share bases')
        return matches[0] if matches else None

    def daily_sessions(self, security_id):
        return tuple(r for r in self.daily if r.security_id == security_id)

    def prior_closes(self, security_id):
        return tuple(r for r in self.closes if r.security_id == security_id)

    def market_caps(self, security_id):
        return tuple(r for r in self.caps if r.security_id == security_id)

    def split_actions(self, security_id):
        return tuple(r for r in self.actions if r.security_id == security_id)

    def minutes(self, security_id, day):
        from .sessions import NEW_YORK
        return tuple(r for r in self.minute_records if r.interval.security_id == security_id
                     and r.interval.timestamp.astimezone(NEW_YORK).date() == day)

    def weekly_history(self, security_id, day):
        return tuple(r for r in self.weekly_records if r.bar.security_id == security_id)
