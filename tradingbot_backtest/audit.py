"""Small immutable audit records; no logger, execution or reporting engine."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction

from .codes import ReasonCode, RunStatus
from .market import validate_timestamp
from .states import StrategyState

AuditValue = str | int | bool | Decimal | Fraction | datetime | None


@dataclass(frozen=True, slots=True)
class AuditRecord:
    run_id: str
    data_version: str
    event_type: str
    recorded_at: datetime
    security_id: str | None = None
    ticker: str | None = None
    setup_id: str | None = None
    trade_id: str | None = None
    modeled_event_at: datetime | None = None
    interval_start: datetime | None = None
    interval_end: datetime | None = None
    state: StrategyState | None = None
    reason: ReasonCode | None = None
    status: RunStatus | None = None
    details: tuple[tuple[str, AuditValue], ...] = ()

    def __post_init__(self) -> None:
        validate_timestamp(self.recorded_at)
        for name in ("run_id", "data_version", "event_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonempty")
        for name in ("security_id", "ticker", "setup_id", "trade_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a nonempty identifier when supplied")
        for value in (self.modeled_event_at, self.interval_start, self.interval_end):
            if value is not None:
                validate_timestamp(value)
        if (self.interval_start is None) != (self.interval_end is None):
            raise ValueError("Supply both interval boundaries or neither")
        if (self.interval_start is not None
                and self.interval_end.astimezone(timezone.utc)
                <= self.interval_start.astimezone(timezone.utc)):
            raise ValueError("Interval end must follow its start")
        for name, expected in (("state", StrategyState), ("reason", ReasonCode), ("status", RunStatus)):
            value = getattr(self, name)
            if value is not None and not isinstance(value, expected):
                raise TypeError(f"{name} must use its structured enumeration")
        if not isinstance(self.details, tuple):
            raise TypeError("Details must be immutable tuples")
        keys = set()
        for pair in self.details:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise TypeError("Each detail must be a key/value tuple")
            key, value = pair
            if not isinstance(key, str) or not key or key in keys:
                raise ValueError("Detail keys must be nonempty and unique")
            keys.add(key)
            if value is not None and type(value) not in (str, int, bool, Decimal, Fraction, datetime):
                raise TypeError("Audit values must be exact supported scalars, never floats")
            if isinstance(value, Decimal) and not value.is_finite():
                raise ValueError("Audit Decimal values must be finite")
            if isinstance(value, datetime):
                validate_timestamp(value)
