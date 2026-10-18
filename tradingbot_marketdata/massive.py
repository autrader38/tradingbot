"""Explicit local EOD preview; bounded, cached and process-rate-limited.

No network or credential lookup on import. No automatic retry or refresh. Every
uncached attempt requires a user load; failures establish manual-retry backoff.
"""

from collections import deque
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import math
import os
from threading import Lock
import time

from .models import _decode, _eastern, _ticker, _utc, _watchlist
from .transport import _read_response

__all__ = ()

_DEFAULT_WATCHLIST = ('AAPL', 'MSFT', 'NVDA')


def _now():
    return datetime.now(timezone.utc)


class _RateGate:
    def __init__(self):
        self.lock = Lock()
        self.attempts = deque(maxlen=4)
        self.blocked_until = 0.0

    def reserve(self, when):
        with self.lock:
            if self.attempts and when < self.attempts[-1]: return 60
            while self.attempts and when - self.attempts[0] >= 60: self.attempts.popleft()
            remaining = max(0, self.blocked_until - when)
            if self.attempts: remaining = max(remaining, self.attempts[-1] + 15 - when)
            if len(self.attempts) == 4: remaining = max(remaining, self.attempts[0] + 60 - when)
            if remaining > 0: return math.ceil(remaining)
            self.attempts.append(when)
            return 0

    def backoff(self, seconds):
        with self.lock:
            self.blocked_until = max(self.blocked_until, time.monotonic() + seconds)


_RATE = _RateGate()


def _retry_delay(value, now):
    try:
        if type(value) is not str or len(value) > 128: return 60
        if value.isascii() and value.isdigit() and len(value) <= 10:
            seconds = int(value)
        else:
            seconds = math.ceil((_utc(parsedate_to_datetime(value)) - now).total_seconds())
        return max(60, min(2_147_483_647, seconds))
    except Exception:
        return 60


def _unavailable(reason, retry_after=0):
    return {'provider': 'Massive', 'state': 'UNAVAILABLE', 'reason': reason,
            'demo': False, 'bars': [], 'retry_after_seconds': retry_after}


class _MarketDataPreview:
    __slots__ = ('_enabled', '_watchlist', '_credential', '_cache', '_lock', '_active')

    def __init__(self, *, enabled=False, watchlist=_DEFAULT_WATCHLIST):
        if type(enabled) is not bool: raise ValueError('Invalid market preview configuration')
        self._enabled, self._watchlist = enabled, _watchlist(watchlist)
        value = os.environ.get('MASSIVE_API_KEY') if enabled else None
        self._credential = value if (type(value) is str and 1 <= len(value) <= 512
                                    and all(33 <= ord(char) <= 126 for char in value)) else None
        self._cache, self._lock, self._active = {}, Lock(), False

    def __repr__(self):
        return '<MarketDataPreview read-only>'

    def configuration(self):
        return {'enabled': self._enabled, 'configured': self._credential is not None,
                'state': 'UNAVAILABLE', 'reason': 'USER_LOAD_REQUIRED' if self._enabled and self._credential
                    else 'KEY_NOT_CONFIGURED' if self._enabled else 'PREVIEW_DISABLED',
                'provider': 'Massive', 'watchlist': list(self._watchlist),
                'maximum_requests_per_minute': 4, 'cache_seconds': 300,
                'entitlement': 'Stocks Basic · end-of-day · not live',
                'license_scope': 'Personal, non-professional, non-business use only'}

    def observe(self, ticker, view):
        try:
            _ticker(ticker)
            if ticker not in self._watchlist or view not in ('previous', 'history'):
                return _unavailable('INVALID_REQUEST')
            if not self._enabled: return _unavailable('PREVIEW_DISABLED')
            if self._credential is None: return _unavailable('KEY_NOT_CONFIGURED')
            now, tick = _utc(_now()), time.monotonic()
            key = (ticker, view)
            with self._lock:
                if key in self._cache:
                    series, retrieved, expires = self._cache[key]
                    if tick < expires:
                        return self._publication(series, now, retrieved, view, True)
                    del self._cache[key]
                if self._active: return _unavailable('REQUEST_IN_PROGRESS', 1)
                delay = _RATE.reserve(tick)
                if delay: return _unavailable('RATE_LIMITED', delay)
                self._active = True
            try:
                start = end = None
                if view == 'previous': path = f'/v2/aggs/ticker/{ticker}/prev?adjusted=true'
                else:
                    end = _eastern(now).date() - timedelta(days=1)
                    start = end - timedelta(days=60)
                    path = f'/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}?adjusted=true&sort=asc&limit=64'
                code, content_type, retry_after, body = _read_response(path, self._credential)
                retrieved = _utc(_now())
                if retrieved < now: raise ValueError('Invalid market response')
                if code != 200:
                    delay = _retry_delay(retry_after, now) if code in (429, 503) else 300 if code in (401, 403) else 60
                    _RATE.backoff(delay)
                    reason = {401: 'AUTHENTICATION_FAILED', 403: 'ACCESS_DENIED', 404: 'NOT_FOUND',
                              429: 'RATE_LIMITED'}.get(code, 'REDIRECT_REJECTED' if 300 <= code < 400 else 'PROVIDER_UNAVAILABLE')
                    return _unavailable(reason, delay)
                if type(content_type) is not str or content_type.split(';')[0].strip().lower() != 'application/json':
                    raise ValueError('Invalid market response')
                series = _decode(body, ticker, view, retrieved, start, end)
                publication = self._publication(series, retrieved, retrieved, view, False)
                if series.bars:
                    with self._lock:
                        self._cache[key] = (series, retrieved, time.monotonic() + 300)
                return publication
            except TimeoutError:
                return _unavailable('TIMEOUT', 15)
            except OSError:
                return _unavailable('NETWORK_UNAVAILABLE', 15)
            except Exception:
                _RATE.backoff(60)
                return _unavailable('INVALID_RESPONSE', 60)
            finally:
                with self._lock: self._active = False
        except Exception:
            return _unavailable('INVALID_REQUEST')

    @staticmethod
    def _publication(series, now, retrieved, view, cached):
        if now < retrieved: raise ValueError('Invalid market response')
        result = series.payload(now)
        result.update({'view': view, 'retrieved_at': retrieved.isoformat(),
                       'evaluated_at': now.isoformat(), 'cached': cached, 'retry_after_seconds': 0,
                       'source_endpoint': 'previous-day-bar' if view == 'previous' else 'custom-daily-bars'})
        return result
