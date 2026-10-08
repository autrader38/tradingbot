"""Calendar abstraction only: dates/boundaries must come from verified inputs."""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo

from .market import validate_timestamp

NEW_YORK = ZoneInfo("America/New_York")


class SessionPeriod(StrEnum):
    REGULAR = "REGULAR"
    PREMARKET_VOLUME_CONTEXT = "PREMARKET_VOLUME_CONTEXT"
    OUTSIDE = "OUTSIDE"


@dataclass(frozen=True, slots=True)
class TradingSession:
    trading_date: date
    open: datetime
    close: datetime
    source_id: str

    def __post_init__(self) -> None:
        if type(self.trading_date) is not date:
            raise TypeError("trading_date must be a date")
        for value in (self.open, self.close):
            validate_timestamp(value)
            if value.astimezone(NEW_YORK).date() != self.trading_date:
                raise ValueError("Session boundaries must belong to the trading date in New York")
            if value.second or value.microsecond:
                raise ValueError("Session boundaries must be minute-aligned")
        if self.close.astimezone(timezone.utc) <= self.open.astimezone(timezone.utc):
            raise ValueError("Session close must follow open")
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("Session source must be recorded")

    def period_at(self, timestamp: datetime, *, premarket_start: time) -> SessionPeriod:
        """Classify start timestamps, with regular close excluded.

        Caller supplies the frozen config's 04:00 context start. No holiday or
        session boundary is inferred here; a calendar must supply the session.
        """
        validate_timestamp(timestamp)
        if premarket_start.tzinfo is not None:
            raise ValueError("Premarket start is a local New York wall-clock time")
        local = timestamp.astimezone(NEW_YORK)
        if local.date() != self.trading_date:
            return SessionPeriod.OUTSIDE
        instant = timestamp.astimezone(timezone.utc)
        opening, closing = self.open.astimezone(timezone.utc), self.close.astimezone(timezone.utc)
        if opening <= instant < closing:
            return SessionPeriod.REGULAR
        premarket = datetime.combine(self.trading_date, premarket_start, NEW_YORK)
        if premarket.astimezone(timezone.utc) <= instant < opening:
            return SessionPeriod.PREMARKET_VOLUME_CONTEXT
        return SessionPeriod.OUTSIDE


class SessionCalendar(Protocol):
    """Verified sessions, including scheduled early closes; no provider selected.

    Return None only for a verified non-session (holiday/weekend). Raise an
    error for unavailable/unknown boundaries, never pretend it is a holiday.
    """

    def session_for(self, trading_date: date) -> TradingSession | None: ...
