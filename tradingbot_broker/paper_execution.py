"""Ephemeral user authorization only; never PAPER attestation or broker routing."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo
import math
import re
import secrets

from tradingbot_backtest.market import validate_timestamp
from .readonly_models import ReadOnlyError


ARM_CONFIRMATION = 'I ARM IBKR PAPER TRADING'
MAX_AUTHORIZATION_LIFETIME = timedelta(minutes=15)


class PaperExecutionAuthorizationStatus(StrEnum):
    UNAVAILABLE = 'UNAVAILABLE'
    DISARMED = 'DISARMED'
    ARMED = 'ARMED'
    EXPIRED = 'EXPIRED'
    INVALIDATED = 'INVALIDATED'


class PaperExecutionScope(StrEnum):
    PLACE_ORDER = 'PLACE_ORDER'
    CANCEL_ORDER = 'CANCEL_ORDER'
    GLOBAL_CANCEL = 'GLOBAL_CANCEL'


# Documentation contracts only. Never feed these into a transport allowlist.
FUTURE_PAPER_WRITE_IDS = (
    (PaperExecutionScope.PLACE_ORDER, 3, 203),
    (PaperExecutionScope.CANCEL_ORDER, 4, 204),
    (PaperExecutionScope.GLOBAL_CANCEL, 58, 258),
)
DEFAULT_PAPER_SCOPES = (PaperExecutionScope.PLACE_ORDER, PaperExecutionScope.CANCEL_ORDER)


def _scopes(values):
    if (type(values) is not tuple or not values
            or any(type(value) is not PaperExecutionScope for value in values)
            or len(set(values)) != len(values)):
        raise ReadOnlyError('INVALID_PAPER_EXECUTION_SCOPE')
    return tuple(sorted(values, key=lambda value: value.value))


@dataclass(frozen=True, slots=True)
class PaperExecutionAuthorization:
    generation: int
    issued_at: datetime
    expires_at: datetime
    scopes: tuple[PaperExecutionScope, ...]
    capability_id: str = field(repr=False)
    source: str = 'LOCAL_USER_SESSION_AUTHORIZATION'

    def __post_init__(self):
        if type(self.generation) is not int or self.generation <= 0:
            raise ReadOnlyError('INVALID_PAPER_EXECUTION_CAPABILITY')
        _timestamp_value(self.issued_at)
        _timestamp_value(self.expires_at)
        if not self.issued_at < self.expires_at <= self.issued_at + MAX_AUTHORIZATION_LIFETIME:
            raise ReadOnlyError('INVALID_PAPER_EXECUTION_CAPABILITY')
        if self.scopes != _scopes(self.scopes):
            raise ReadOnlyError('INVALID_PAPER_EXECUTION_SCOPE')
        if (type(self.capability_id) is not str
                or re.fullmatch('[0-9a-f]{64}', self.capability_id) is None
                or type(self.source) is not str or self.source != 'LOCAL_USER_SESSION_AUTHORIZATION'):
            raise ReadOnlyError('INVALID_PAPER_EXECUTION_CAPABILITY')


def _timestamp_value(value):
    # No datetime subclasses or caller-mutable/custom tzinfo in issuance state.
    if type(value) is not datetime or type(value.tzinfo) not in (timezone, ZoneInfo):
        raise ReadOnlyError('INVALID_PAPER_EXECUTION_CAPABILITY')
    validate_timestamp(value)
    zone = value.tzinfo.key if type(value.tzinfo) is ZoneInfo else value.tzname()
    return (value.isoformat(), value.fold, type(value.tzinfo).__name__, zone)


def _values(capability):
    """Independent primitive snapshot; never call equality on unvalidated fields."""
    if (type(capability) is not PaperExecutionAuthorization
            or type(capability.generation) is not int or capability.generation <= 0
            or type(capability.capability_id) is not str
            or re.fullmatch('[0-9a-f]{64}', capability.capability_id) is None
            or type(capability.source) is not str
            or capability.source != 'LOCAL_USER_SESSION_AUTHORIZATION'):
        raise ReadOnlyError('INVALID_PAPER_EXECUTION_CAPABILITY')
    scopes = capability.scopes
    if type(scopes) is not tuple or not scopes:
        raise ReadOnlyError('INVALID_PAPER_EXECUTION_SCOPE')
    # Identity, not enum/string equality. Only primitive indices enter the registry.
    members = (PaperExecutionScope.PLACE_ORDER, PaperExecutionScope.CANCEL_ORDER,
               PaperExecutionScope.GLOBAL_CANCEL)
    indices = []
    for scope in scopes:
        if type(scope) is not PaperExecutionScope:
            raise ReadOnlyError('INVALID_PAPER_EXECUTION_SCOPE')
        index = next((i for i, member in enumerate(members) if scope is member), None)
        if index is None or index in indices:
            raise ReadOnlyError('INVALID_PAPER_EXECUTION_SCOPE')
        indices.append(index)
    return (capability.generation, _timestamp_value(capability.issued_at),
            _timestamp_value(capability.expires_at), tuple(indices),
            capability.capability_id, capability.source)


class _PaperExecutionAuthority:
    """Broker-owned identity registry, used only under its lifecycle locks."""
    def __init__(self):
        self.status = PaperExecutionAuthorizationStatus.DISARMED
        self._issued = None
        self._issued_values = None
        self._binding = None
        self._deadline = None
        self._last_monotonic = None

    def _revoke(self, status):
        changed = self.status != status
        self._issued = self._issued_values = self._binding = self._deadline = None
        self.status = status
        return changed

    def _time(self, now):
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            raise ReadOnlyError('INVALID_PAPER_EXECUTION_CLOCK')
        if self._last_monotonic is not None and now < self._last_monotonic:
            raise ReadOnlyError('PAPER_EXECUTION_CLOCK_REGRESSED')
        self._last_monotonic = now

    def _issue(self, binding, at, monotonic_at, valid_until, scopes, monotonic_deadline=None):
        self._time(monotonic_at)
        scopes = _scopes(scopes)
        expires = min(at + MAX_AUTHORIZATION_LIFETIME, valid_until)
        capability = PaperExecutionAuthorization(binding[0], at, expires,
                                                scopes, secrets.token_hex(32))
        deadline = monotonic_at + (expires - at).total_seconds()
        if monotonic_deadline is not None:
            if (type(monotonic_deadline) not in (int, float)
                    or not math.isfinite(monotonic_deadline)):
                raise ReadOnlyError('INVALID_PAPER_EXECUTION_CLOCK')
            deadline = min(deadline, monotonic_deadline)
        if deadline <= monotonic_at:
            raise ReadOnlyError('PAPER_EXECUTION_EXPIRED')
        snapshot = _values(capability)
        self._issued = capability
        self._issued_values = snapshot
        self._binding = binding
        self._deadline = deadline
        self.status = PaperExecutionAuthorizationStatus.ARMED
        return capability

    def _expiry_reason(self, at, monotonic_at):
        self._time(monotonic_at)
        if self._issued is not None:
            if not self._intact(self._issued):
                return 'PAPER_EXECUTION_CAPABILITY_CHANGED'
            issued_at = datetime.fromisoformat(self._issued_values[1][0])
            expires_at = datetime.fromisoformat(self._issued_values[2][0])
            if at < issued_at:
                return 'PAPER_EXECUTION_CLOCK_REGRESSED'
            if at >= expires_at or monotonic_at >= self._deadline:
                return 'PAPER_EXECUTION_EXPIRED'
        return None

    def _intact(self, capability):
        if capability is not self._issued or self._issued_values is None:
            return False
        try:
            return _values(capability) == self._issued_values
        except Exception:
            return False

    def _accepts(self, capability, scope):
        return (self.status is PaperExecutionAuthorizationStatus.ARMED
                and self._intact(capability) and type(scope) is PaperExecutionScope
                and any(scope is member and index in self._issued_values[3]
                        for index, member in enumerate((PaperExecutionScope.PLACE_ORDER,
                            PaperExecutionScope.CANCEL_ORDER, PaperExecutionScope.GLOBAL_CANCEL))))
