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
from .reference import _TickerReference, _decode_reference
from .research import _research_row
from .discovery import _DiscoveryFilters, _GroupedLimitError, _decode_grouped, _filter_decimal, _request_date
from .transport import _read_response, _read_grouped_response

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
    __slots__ = ('_enabled', '_watchlist', '_credential', '_cache', '_loaded', '_lock', '_active',
                 '_discovery', '_discovery_cache', '_shortlist', '_shortlist_sources')

    def __init__(self, *, enabled=False, watchlist=_DEFAULT_WATCHLIST):
        if type(enabled) is not bool: raise ValueError('Invalid market preview configuration')
        self._enabled, self._watchlist = enabled, _watchlist(watchlist)
        value = os.environ.get('MASSIVE_API_KEY') if enabled else None
        self._credential = value if (type(value) is str and 1 <= len(value) <= 512
                                    and all(33 <= ord(char) <= 126 for char in value)) else None
        self._cache, self._loaded, self._lock, self._active = {}, {}, Lock(), False
        self._discovery, self._discovery_cache = None, {}
        self._shortlist, self._shortlist_sources = (), ()

    def __repr__(self):
        return '<MarketDataPreview read-only>'

    def configuration(self):
        with self._lock:
            shortlist, sources = self._shortlist, self._shortlist_sources
        return {'enabled': self._enabled, 'configured': self._credential is not None,
                'state': 'UNAVAILABLE', 'reason': 'USER_LOAD_REQUIRED' if self._enabled and self._credential
                    else 'KEY_NOT_CONFIGURED' if self._enabled else 'PREVIEW_DISABLED',
                'provider': 'Massive', 'watchlist': list(self._watchlist),
                'research_watchlist': list(shortlist or self._watchlist),
                'session_shortlist': list(shortlist),
                'shortlist_sources': [{'ticker': ticker, 'discovery_date': observed} for ticker, observed in sources],
                'maximum_requests_per_minute': 4, 'cache_seconds': 300,
                'entitlement': 'Stocks Basic · end-of-day · not live',
                'license_scope': 'Personal, non-professional, non-business use only'}

    def research(self):
        # Local immutable evidence only. Never requests data or inspects credentials.
        now = _utc(_now())
        with self._lock:
            loaded, watchlist = dict(self._loaded), self._shortlist or self._watchlist
            shortlisted = bool(self._shortlist)
        rows = [_research_row(ticker, loaded.get((ticker, 'history')),
                              loaded.get((ticker, 'reference')), now) for ticker in watchlist]
        return {'provider': 'Massive', 'demo': False, 'evaluated_at': now.isoformat(),
                'watchlist_total': len(watchlist), 'rows': rows,
                'scope': 'SESSION_DISCOVERY_SHORTLIST' if shortlisted else 'CONFIGURED_WATCHLIST_ONLY_NOT_FULL_MARKET',
                'full_strategy_qualification': 'NOT AVAILABLE'}

    def select_research(self, tickers):
        """Explicit, bounded session presentation selection. Never provider I/O."""
        try:
            if type(tickers) is not tuple or len(tickers) > 5 or len(set(tickers)) != len(tickers):
                raise ValueError('Invalid shortlist')
            for ticker in tickers: _ticker(ticker)
            with self._lock:
                if self._active: return _unavailable('REQUEST_IN_PROGRESS', 1)
                if tickers:
                    valid = dict(self._shortlist_sources)
                    if self._discovery is not None:
                        valid.update({row.ticker: self._discovery[0].requested_date.isoformat()
                                      for row in self._discovery[0].rows})
                    if not valid: return _unavailable('DISCOVERY_REQUIRED')
                    if any(ticker not in valid for ticker in tickers): return _unavailable('INVALID_SHORTLIST')
                self._shortlist = tickers
                self._shortlist_sources = tuple((ticker, valid[ticker]) for ticker in tickers)
                allowed = set(self._watchlist + tickers)
                # Removed research selections cannot cause unbounded cache growth.
                self._loaded = {key: value for key, value in self._loaded.items() if key[0] in allowed}
                self._cache = {key: value for key, value in self._cache.items() if key[0] in allowed}
            return {**self.configuration(), 'state': 'RESEARCH_SELECTION_UPDATED'}
        except Exception:
            return _unavailable('INVALID_SHORTLIST')

    def discovery(self, *, max_price='5', min_volume='0', search='', sort='volume', page=0):
        """Local filtering/pagination only, over the last validated response."""
        try:
            now = _utc(_now())
            filters = _DiscoveryFilters(_filter_decimal(max_price), _filter_decimal(min_volume))
            with self._lock: loaded = self._discovery
            if loaded is None:
                result = _unavailable('USER_LOAD_REQUIRED' if self._enabled and self._credential
                                      else 'KEY_NOT_CONFIGURED' if self._enabled else 'PREVIEW_DISABLED')
                result.update({'rows': [], 'requested_date': None})
            else:
                series, retrieved = loaded
                if now < retrieved: raise ValueError('Invalid discovery time')
                result = series.page(now, filters=filters, search=search, sort=sort, page=page)
                result['retrieved_at'] = retrieved.isoformat()
            today = _eastern(now).date()
            suggested = today - timedelta(days=1)
            while suggested.weekday() >= 5: suggested -= timedelta(days=1)
            result.update({'earliest_date': (today - timedelta(days=730)).isoformat(),
                           'latest_date': (today - timedelta(days=1)).isoformat(),
                           'suggested_date': suggested.isoformat(), 'evaluated_at': now.isoformat(),
                           'date_policy': 'PAST_ET_WEEKDAY_ONLY_NOT_A_HOLIDAY_OR_SESSION_CALENDAR'})
            return result
        except Exception:
            return {**_unavailable('INVALID_DISCOVERY_FILTERS'), 'rows': []}

    def load_discovery(self, date_text):
        """One explicit grouped request, sharing the exact existing rate gate."""
        try:
            now, tick = _utc(_now()), time.monotonic()
            requested = _request_date(date_text, now)
            if not self._enabled: return _unavailable('PREVIEW_DISABLED')
            if self._credential is None: return _unavailable('KEY_NOT_CONFIGURED')
            with self._lock:
                if self._active: return _unavailable('REQUEST_IN_PROGRESS', 1)
                cached = self._discovery_cache.get(requested)
                if cached and tick < cached[2] and now >= cached[1]:
                    self._discovery = cached[:2]
                    result = cached[0].page(now)
                    result.update({'retrieved_at': cached[1].isoformat(), 'cached': True, 'retry_after_seconds': 0})
                    return result
                delay = _RATE.reserve(tick)
                if delay: return _unavailable('RATE_LIMITED', delay)
                self._active, self._discovery = True, None
            try:
                path = f'/v2/aggs/grouped/locale/us/market/stocks/{requested}?adjusted=true&include_otc=false'
                code, content_type, retry_after, body = _read_grouped_response(path, self._credential)
                retrieved = _utc(_now())
                if retrieved < now: raise ValueError('Invalid discovery time')
                if code != 200:
                    delay = _retry_delay(retry_after, now) if code in (429, 503) else 300 if code in (401, 403) else 60
                    _RATE.backoff(delay)
                    reason = {401: 'AUTHENTICATION_FAILED', 403: 'ACCESS_DENIED', 404: 'NOT_FOUND',
                              429: 'RATE_LIMITED'}.get(code, 'REDIRECT_REJECTED' if 300 <= code < 400 else 'PROVIDER_UNAVAILABLE')
                    return _unavailable(reason, delay)
                if type(content_type) is not str or content_type.split(';')[0].strip().lower() != 'application/json':
                    raise ValueError('Invalid discovery content')
                series = _decode_grouped(body, requested, retrieved)
                result = series.page(retrieved)
                with self._lock:
                    self._discovery = (series, retrieved)
                    self._discovery_cache = {requested: (series, retrieved, time.monotonic() + 300)}
                result.update({'retrieved_at': retrieved.isoformat(), 'cached': False, 'retry_after_seconds': 0})
                return result
            except _GroupedLimitError:
                _RATE.backoff(60)
                return _unavailable('RESPONSE_LIMIT_EXCEEDED', 60)
            except TimeoutError:
                _RATE.backoff(15)
                return _unavailable('TIMEOUT', 15)
            except OSError:
                _RATE.backoff(15)
                return _unavailable('NETWORK_UNAVAILABLE', 15)
            except Exception:
                _RATE.backoff(60)
                return _unavailable('INVALID_RESPONSE', 60)
            finally:
                with self._lock: self._active = False
        except Exception:
            return _unavailable('INVALID_REQUEST')

    def observe(self, ticker, view):
        try:
            _ticker(ticker)
            with self._lock: allowed = self._watchlist + self._shortlist
            if ticker not in allowed or view not in ('previous', 'history', 'reference'):
                return _unavailable('INVALID_REQUEST')
            if not self._enabled: return _unavailable('PREVIEW_DISABLED')
            if self._credential is None: return _unavailable('KEY_NOT_CONFIGURED')
            now, tick = _utc(_now()), time.monotonic()
            requested_date = None
            if view == 'reference':
                with self._lock: history = self._loaded.get((ticker, 'history'))
                if history is None or history[1] > now or history[0].payload(now)['state'] != 'REAL END-OF-DAY':
                    return _unavailable('ELIGIBLE_HISTORY_REQUIRED')
                requested_date = history[0].bars[-1].session_date
            key = (ticker, view)
            with self._lock:
                if ticker not in self._watchlist + self._shortlist: return _unavailable('INVALID_REQUEST')
                if key in self._cache:
                    series, retrieved, expires = self._cache[key]
                    if tick < expires and (view != 'reference' or series.requested_date == requested_date):
                        return self._publication(series, now, retrieved, view, True)
                    del self._cache[key]
                if self._active: return _unavailable('REQUEST_IN_PROGRESS', 1)
                delay = _RATE.reserve(tick)
                if delay: return _unavailable('RATE_LIMITED', delay)
                self._active = True
            try:
                start = end = None
                if view == 'previous': path = f'/v2/aggs/ticker/{ticker}/prev?adjusted=true'
                elif view == 'reference': path = f'/v3/reference/tickers/{ticker}?date={requested_date}'
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
                series = _decode_reference(body, ticker, requested_date) if view == 'reference' else \
                    _decode(body, ticker, view, retrieved, start, end)
                publication = self._publication(series, retrieved, retrieved, view, False)
                with self._lock:
                    if view in ('history', 'reference'):
                        self._loaded[key] = (series, retrieved)
                    if view == 'reference' or series.bars:
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
                       'source_endpoint': 'ticker-overview' if type(series) is _TickerReference else
                           'previous-day-bar' if view == 'previous' else 'custom-daily-bars'})
        return result
