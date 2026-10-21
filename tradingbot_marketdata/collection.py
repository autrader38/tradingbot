"""Private session-owned grouped evidence. No transport, persistence or timers."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from sys import getsizeof
from threading import Lock

from .discovery import _GroupedBar, _GroupedSummary, _MAX_ROWS
from .models import _check, _utc
from .multiday import _validate_sources, _screen_grouped_history

__all__ = ()

_MIB = 1024 * 1024
_MAX_DATES = 11
_MAX_RETAINED_ROWS = 180_000
_MAX_RETAINED_BYTES = 384 * _MIB
_DECODE_RESERVE = 192 * _MIB
_EVALUATION_RESERVE = 128 * _MIB
_PREVIEW_RESERVE = 64 * _MIB
_MAX_WORKING_BYTES = 768 * _MIB
_ROW_CHARGE = 2048
_ENTRY_CHARGE = 4096
_MAX_NEW_CHARGE = 2 * _ENTRY_CHARGE + _MAX_ROWS * (_ROW_CHARGE + 16)
_LIMITATIONS = ('RETAINED_EVIDENCE_NOT_HTTP_CACHE_OR_CURRENTNESS',
                'SESSION_CONTINUITY_UNVERIFIED', 'ADJUSTMENT_VINTAGE_UNVERIFIED',
                'RESPONSE_COVERAGE_NOT_COMPLETE_US_MARKET',
                'NO_AUTOMATIC_SCREENING_OR_FULL_STRATEGY_QUALIFICATION',
                'PERSONAL_NON_COMMERCIAL_RESEARCH_ONLY', 'NO_TRADING_AUTHORITY')


class _CollectionError(ValueError):
    def __init__(self, reason):
        self.reason = reason if type(reason) is str and reason in (
            'COLLECTION_MEMORY_LIMIT', 'COLLECTION_CLOSED', 'REQUEST_IN_PROGRESS',
            'COLLECTION_DATE_LIMIT', 'COLLECTION_SOURCE_INCOMPATIBLE', 'COLLECTION_TARGET_MISSING') else 'COLLECTION_SOURCE_INCOMPATIBLE'
        super().__init__('Grouped collection unavailable')


@dataclass(frozen=True, slots=True, repr=False)
class _RetainedSummary:
    summary: _GroupedSummary
    retrieved_at: datetime
    charged_bytes: int


def _charge(summary, retrieved):
    # Charge repeated immutable values independently, never undercount through
    # interning/aliasing. The floor additionally covers index/result references.
    total = _ENTRY_CHARGE + getsizeof(summary) + getsizeof(summary.rows) + getsizeof(retrieved)
    for row in summary.rows:
        _check(type(row) is _GroupedBar)
        cost = getsizeof(row) + sum(getsizeof(getattr(row, field)) for field in
            ('ticker', 'timestamp_ms', 'open', 'high', 'low', 'close', 'volume', 'vwap'))
        total += max(_ROW_CHARGE, cost)
    total += (summary.received_count - len(summary.rows)) * _ROW_CHARGE
    return total


class _GroupedCollection:
    """One preview lifetime. All operations serialized; no source eviction."""
    __slots__ = ('_entries', '_lock', '_closed', '_evaluating', '_revision')

    def __init__(self):
        self._entries, self._lock = {}, Lock()
        self._closed = self._evaluating = False
        self._revision = 0

    def __repr__(self):
        return '<GroupedCollection session-only>'

    def get(self, requested):
        with self._lock:
            return None if self._closed else self._entries.get(requested)

    def _capacity(self, rows, charged):
        if rows > _MAX_RETAINED_ROWS or charged > _MAX_RETAINED_BYTES:
            raise _CollectionError('COLLECTION_MEMORY_LIMIT')
        if charged + _DECODE_RESERVE + _EVALUATION_RESERVE + _PREVIEW_RESERVE > _MAX_WORKING_BYTES:
            raise _CollectionError('COLLECTION_MEMORY_LIMIT')

    def _totals(self):
        return (sum(item.summary.received_count for item in self._entries.values()),
                sum(item.charged_bytes for item in self._entries.values()))

    def require_capacity(self):
        """Reserve a maximum-size new response before spending provider quota."""
        with self._lock:
            if self._closed: raise _CollectionError('COLLECTION_CLOSED')
            if self._evaluating: raise _CollectionError('REQUEST_IN_PROGRESS')
            if len(self._entries) >= _MAX_DATES: raise _CollectionError('COLLECTION_DATE_LIMIT')
            rows, charged = self._totals()
            self._capacity(rows + _MAX_ROWS, charged + _MAX_NEW_CHARGE)

    def retain(self, summary, retrieved):
        with self._lock:
            if self._closed: raise _CollectionError('COLLECTION_CLOSED')
            if self._evaluating: raise _CollectionError('REQUEST_IN_PROGRESS')
            _check(type(summary) is _GroupedSummary)
            _check(type(summary.requested_date) is date)
            _check(type(retrieved) is datetime and retrieved.tzinfo is timezone.utc)
            _check(summary.requested_date not in self._entries)
            if len(self._entries) >= _MAX_DATES: raise _CollectionError('COLLECTION_DATE_LIMIT')
            # Date-key indexing explicitly orders user-selected dates for PRE4A;
            # its own strict validator still rejects incompatible source facts.
            ordered = tuple(sorted((*self._entries, summary.requested_date)))
            sources = tuple(summary if day == summary.requested_date else self._entries[day].summary for day in ordered)
            try: _validate_sources(sources, ordered[-1])
            except (ValueError, TypeError, ArithmeticError):
                raise _CollectionError('COLLECTION_SOURCE_INCOMPATIBLE') from None
            _check(all(_utc(retrieved) >= item.retrieved_at for item in self._entries.values()))
            charged = _charge(summary, retrieved)
            rows, retained = self._totals()
            self._capacity(rows + summary.received_count, retained + charged)
            entry = _RetainedSummary(summary, retrieved, charged)
            self._entries[summary.requested_date] = entry
            self._revision += 1
            return entry

    def status(self, target=None):
        with self._lock:
            entries = tuple(self._entries.values())
            earlier = sum(item.summary.requested_date < target for item in entries) if target else 0
            present = target in self._entries if target else False
            rows, charged = self._totals()
            return {'state': 'COLLECTION_CLOSED' if self._closed else 'RETAINED_EVIDENCE' if entries else 'EMPTY_COLLECTION',
                    'retained_count': len(entries), 'maximum_dates': _MAX_DATES, 'collection_revision': self._revision,
                    'dates': [{'requested_date': item.summary.requested_date.isoformat(),
                               'retrieved_at': item.retrieved_at.isoformat(),
                               'received_rows': item.summary.received_count, 'validated_rows': len(item.summary.rows)}
                              for item in entries],
                    'load_order_preserved': True, 'retained_rows': rows,
                    'charged_retained_bytes': charged, 'maximum_retained_bytes': _MAX_RETAINED_BYTES,
                    'maximum_retained_rows': _MAX_RETAINED_ROWS, 'maximum_working_bytes': _MAX_WORKING_BYTES,
                    'target_date': target.isoformat() if target else None,
                    'earlier_retained_dates': earlier, 'target_present': present,
                    'input_status': 'TARGET_NOT_RETAINED' if not present else
                        'TEN_EARLIER_DATES_RETAINED_TICKER_HISTORY_UNVERIFIED' if earlier == 10 else 'INSUFFICIENT_DATED_INPUTS',
                    'compatibility': 'SOURCE_VALIDATED_SESSION_AND_ADJUSTMENT_CONTINUITY_UNVERIFIED',
                    'evaluation_active': self._evaluating, 'limitations': list(_LIMITATIONS)}

    @contextmanager
    def evaluate(self, target, *, filters=None):
        """Internal scoped handoff only; never HTTP publication or automatic work.

        Callers must release the full result at scope exit. PRE4C retains only
        its separately bounded scalar projection. Later source dates are excluded.
        """
        with self._lock:
            if self._closed: raise _CollectionError('COLLECTION_CLOSED')
            if self._evaluating: raise _CollectionError('REQUEST_IN_PROGRESS')
            if type(target) is not date or target not in self._entries:
                raise _CollectionError('COLLECTION_TARGET_MISSING')
            sources = tuple(self._entries[day].summary for day in sorted(self._entries) if day <= target)
            self._evaluating = True
        try:
            yield _screen_grouped_history(sources, target) if filters is None else _screen_grouped_history(sources, target, filters=filters)
        finally:
            with self._lock: self._evaluating = False

    def clear(self, *, close=False):
        with self._lock:
            if self._evaluating and not close: raise _CollectionError('REQUEST_IN_PROGRESS')
            self._entries.clear()
            self._revision += 1
            if close: self._closed = True
