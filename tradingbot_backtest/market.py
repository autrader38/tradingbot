"""Trusted internal minute records and explicit classification validation."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from collections.abc import Iterable


class IntervalClassification(StrEnum):
    TRADED = "TRADED"
    NO_TRADE = "NO_TRADE"
    MISSING = "MISSING"
    INVALID = "INVALID"


def validate_timestamp(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    # Reject nonexistent local timestamps, rather than silently shifting them.
    restored = value.astimezone(timezone.utc).astimezone(value.tzinfo)
    if (restored.replace(tzinfo=None) != value.replace(tzinfo=None)
            or restored.utcoffset() != value.utcoffset()):
        raise ValueError("Timestamp does not represent a valid local instant")


@dataclass(frozen=True, slots=True)
class MarketInterval:
    """Timestamp labels interval START; completion is start plus one minute.

    NO_TRADE requires affirmative provider verification. MISSING/INVALID records
    carry no trusted numeric payload; preserve raw evidence in audit details.
    """

    security_id: str
    ticker: str
    timestamp: datetime
    classification: IntervalClassification
    source_id: str
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume: Decimal | None = None
    no_trade_verified: bool = False
    data_quality_reason: str | None = None

    def __post_init__(self) -> None:
        validate_interval(self)

    @property
    def end(self) -> datetime:
        return (self.timestamp.astimezone(timezone.utc) + timedelta(minutes=1)).astimezone(
            self.timestamp.tzinfo
        )


def validate_interval(interval: MarketInterval) -> None:
    validate_timestamp(interval.timestamp)
    if interval.timestamp.second or interval.timestamp.microsecond:
        raise ValueError("Minute interval start must be minute-aligned")
    for name in ("security_id", "ticker", "source_id"):
        if not isinstance(getattr(interval, name), str) or not getattr(interval, name).strip():
            raise ValueError(f"{name} must be nonempty")
    if not isinstance(interval.classification, IntervalClassification):
        raise TypeError("Classification must be explicit IntervalClassification")
    if type(interval.no_trade_verified) is not bool:
        raise TypeError("no_trade_verified must be bool")
    prices = (interval.open, interval.high, interval.low, interval.close)
    if interval.classification == IntervalClassification.TRADED:
        for value in (*prices, interval.volume):
            if not isinstance(value, Decimal):
                raise TypeError("TRADED requires Decimal OHLCV")
            if not value.is_finite():
                raise ValueError("OHLCV must be finite")
        if any(value <= 0 for value in prices):
            raise ValueError("Prices must be positive")
        if interval.volume < 0:
            raise ValueError("Volume must be nonnegative")
        if (interval.high < interval.low or interval.high < interval.open
                or interval.high < interval.close or interval.low > interval.open
                or interval.low > interval.close):
            raise ValueError("Inconsistent OHLC")
        if interval.no_trade_verified or interval.data_quality_reason is not None:
            raise ValueError("TRADED cannot be marked no-trade or data-invalid")
    elif interval.classification == IntervalClassification.NO_TRADE:
        if any(value is not None for value in prices):
            raise ValueError("NO_TRADE has no OHLC; synthetic prices are forbidden")
        if (not isinstance(interval.volume, Decimal) or not interval.volume.is_finite()
                or interval.volume != 0 or not interval.no_trade_verified):
            raise ValueError("NO_TRADE requires verified zero Decimal volume")
        if interval.data_quality_reason is not None:
            raise ValueError("NO_TRADE is not missing/invalid data")
    else:
        if any(value is not None for value in (*prices, interval.volume)):
            raise ValueError("MISSING/INVALID cannot carry a trusted OHLCV payload")
        if interval.no_trade_verified:
            raise ValueError("Unknown/invalid data is not verified no-trade")
        if not isinstance(interval.data_quality_reason, str) or not interval.data_quality_reason.strip():
            raise ValueError("MISSING/INVALID requires a recorded data-quality reason")


def validate_sequence(intervals: Iterable[MarketInterval]) -> None:
    """Validate per-security strict order; reject all unresolved duplicates.

    Does not sort, infer gap classifications, or resolve provider corrections.
    Calendar-aware adapters must represent expected but unavailable minutes
    explicitly; this function cannot infer holidays or session eligibility.
    Different securities may legitimately share a timestamp.
    """
    previous: dict[str, datetime] = {}
    for interval in intervals:
        validate_interval(interval)
        timestamp = interval.timestamp.astimezone(timezone.utc)
        earlier = previous.get(interval.security_id)
        if earlier is not None:
            if timestamp <= earlier:
                raise ValueError("Per-security timestamps must be strictly increasing")
        previous[interval.security_id] = timestamp
