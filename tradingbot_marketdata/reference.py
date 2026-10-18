"""Allowlisted, date-scoped ticker reference facts. No network or clock access."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import json

from .models import _check, _constant, _eastern, _numeric, _pairs, _ticker

__all__ = ()


@dataclass(frozen=True, slots=True, repr=False)
class _TickerReference:
    ticker: str
    requested_date: date
    market_cap: Decimal | None
    currency: str | None
    market: str | None
    locale: str | None

    def __post_init__(self):
        _ticker(self.ticker)
        _check(type(self.requested_date) is date and 2007 <= self.requested_date.year <= 2099)
        if self.market_cap is not None:
            _check(type(self.market_cap) is Decimal)
            _numeric(self.market_cap, volume=True)
            _check(self.market_cap > 0)
        for value, expected in ((self.currency, 'usd'), (self.market, 'stocks'), (self.locale, 'us')):
            _check(value is None or type(value) is str and value == expected)

    def payload(self, now):
        self.__post_init__()
        age = (_eastern(now).date() - self.requested_date).days
        missing = [name for name in ('market_cap', 'currency', 'market', 'locale')
                   if getattr(self, name) is None]
        state, reason = 'REFERENCE AVAILABLE', 'DATE_SCOPED_REFERENCE'
        if age <= 0: state, reason = 'UNAVAILABLE', 'REFERENCE_DATE_NOT_COMPLETED'
        elif age > 4: state, reason = 'STALE', 'REFERENCE_DATE_STALE'
        elif missing: state, reason = 'UNAVAILABLE', 'MISSING_REFERENCE_FIELDS'
        return {'ticker': self.ticker, 'provider': 'Massive', 'source_endpoint': 'ticker-overview',
                'state': state, 'reason': reason, 'requested_date': self.requested_date.isoformat(),
                'source_as_of': None, 'currency': self.currency,
                'market': self.market, 'locale': self.locale,
                'market_cap': None if self.market_cap is None else str(self.market_cap),
                'missing_fields': missing, 'demo': False,
                'date_semantics': 'Requested point-in-time reference date; market-cap source timestamp unavailable',
                'alignment': 'NOT_A_SYNCHRONOUS_OHLCV_AND_REFERENCE_SNAPSHOT'}


def _decode_reference(body, ticker, requested_date):
    _ticker(ticker)
    _check(type(body) is bytes and len(body) <= 262144)
    raw = json.loads(body.decode('utf-8'), parse_float=Decimal,
                     parse_constant=_constant, object_pairs_hook=_pairs)
    _check(type(raw) is dict and raw.get('status') == 'OK' and not raw.get('next_url'))
    if 'count' in raw: _check(type(raw['count']) is int and raw['count'] == 1)
    row = raw.get('results')
    _check(type(row) is dict and row.get('ticker') == ticker)
    cap = row.get('market_cap')
    if cap is not None: cap = _numeric(cap, volume=True)
    return _TickerReference(ticker, requested_date, cap, row.get('currency_name'),
                            row.get('market'), row.get('locale'))
