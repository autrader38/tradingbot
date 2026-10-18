"""Explicit sanitized visual values, independent of broker models and authority."""

from dataclasses import dataclass
from datetime import datetime
import math


TIMEFRAMES = ('1m', '15m', '1D', '1W')
SYMBOLS = ('ABCD', 'VELA', 'NEXA', 'QBIT', 'MTRX', 'LUMA', 'NOVA', 'AERO',
           'FLUX', 'ORBI', 'VOLT', 'SOLA', 'KITE', 'PICO', 'PRSM', 'ZENA',
           'CORA', 'IONX', 'AXON', 'LYRA', 'TESS', 'VYRA', 'ELIO', 'RUNE')
VOLATILITIES = ('High', 'Very High')


def _check(condition):
    if not condition:
        raise ValueError('Invalid dashboard snapshot')


def _text(value, maximum=120):
    _check(type(value) is str and 0 < len(value) <= maximum
           and all(c.isprintable() for c in value))


def _number(value, minimum=0):
    _check(type(value) in (int, float) and math.isfinite(value) and minimum <= value <= 1_000_000_000_000)


@dataclass(frozen=True, slots=True)
class Candle:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int

    def validate(self):
        _text(self.time, 25)
        parsed = datetime.fromisoformat(self.time)
        _check(parsed.tzinfo is not None and self.time.endswith('+00:00'))
        for value in (self.open, self.high, self.low, self.close):
            _number(value)
        _check(self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high)
        _check(type(self.volume) is int and self.volume >= 0)

    def payload(self):
        self.validate()
        return {'time': self.time, 'open': self.open, 'high': self.high,
                'low': self.low, 'close': self.close, 'volume': self.volume}


@dataclass(frozen=True, slots=True)
class ChartSeries:
    symbol: str
    timeframe: str
    candles: tuple[Candle, ...]

    def validate(self):
        _check(self.symbol in SYMBOLS and self.timeframe in TIMEFRAMES)
        _check(type(self.candles) is tuple and 2 <= len(self.candles) <= 128)
        previous = ''
        for candle in self.candles:
            _check(type(candle) is Candle)
            candle.validate()
            _check(candle.time > previous)
            previous = candle.time


@dataclass(frozen=True, slots=True)
class ScannerRow:
    symbol: str
    price: float
    change_percent: float
    volume: int
    relative_volume: float
    setup: str
    score: int
    status: str
    market_cap: int
    average_volume_10d: int
    volatility: str

    def validate(self):
        _check(self.symbol in SYMBOLS)
        for value in (self.price, self.relative_volume):
            _number(value)
        _number(self.change_percent, -100)
        _check(type(self.volume) is int and self.volume >= 0)
        _check(type(self.score) is int and 0 <= self.score <= 100)
        _check(self.setup == 'ABCD' and self.status in ('WATCHING', 'FORMING', 'READY', 'WAITING'))
        for value in (self.market_cap, self.average_volume_10d):
            _check(type(value) is int and 0 <= value <= 1_000_000_000_000)
        _check(type(self.volatility) is str and self.volatility in VOLATILITIES)

    def payload(self):
        self.validate()
        return {'symbol': self.symbol, 'price': self.price, 'change_percent': self.change_percent,
                'volume': self.volume, 'relative_volume': self.relative_volume, 'setup': self.setup,
                'score': self.score, 'status': self.status, 'market_cap': self.market_cap,
                'average_volume_10d': self.average_volume_10d, 'volatility': self.volatility, 'demo': True}


@dataclass(frozen=True, slots=True)
class DemoTrade:
    symbol: str
    entry: float
    exit: float
    shares: int
    pnl: float
    time: str

    def validate(self):
        _check(self.symbol in SYMBOLS)
        _number(self.entry)
        _number(self.exit)
        _number(self.pnl, -1_000_000)
        _check(type(self.shares) is int and self.shares > 0)
        _text(self.time, 8)

    def payload(self):
        self.validate()
        return {'symbol': self.symbol, 'setup': 'ABCD Breakout', 'entry': self.entry,
                'exit': self.exit, 'shares': self.shares, 'pnl': self.pnl,
                'outcome': 'GAIN' if self.pnl >= 0 else 'LOSS', 'time': self.time, 'demo': True}


@dataclass(frozen=True, slots=True, repr=False)
class DashboardSnapshot:
    """Closed visual schema. Fixed safety facts cannot become runtime permits.

    Serialization is an explicit field allowlist, never arbitrary object dumping.
    Market/performance values are synthetic; no active evidence can be loaded here.
    """

    scanner: tuple[ScannerRow, ...]
    series: tuple[ChartSeries, ...]
    trades: tuple[DemoTrade, ...]
    equity: tuple[int, ...]
    mode: str = 'PAPER'
    trading_authority: str = 'BLOCKED'
    gateway: str = 'READ-ONLY REQUIRED'
    account_mode: str = 'UNKNOWN'
    restart_quarantine: str = 'MANDATORY'
    live_supported: bool = False
    demo_market_data: bool = True

    def __post_init__(self):
        for value in (self.mode, self.trading_authority, self.gateway, self.account_mode, self.restart_quarantine):
            _check(type(value) is str)
        _check(self.mode == 'PAPER' and self.trading_authority == 'BLOCKED'
               and self.gateway == 'READ-ONLY REQUIRED' and self.account_mode == 'UNKNOWN'
               and self.restart_quarantine == 'MANDATORY' and self.live_supported is False
               and self.demo_market_data is True)
        _check(type(self.scanner) is tuple and len(self.scanner) == len(SYMBOLS))
        _check(all(type(row) is ScannerRow for row in self.scanner))
        _check(tuple(row.symbol for row in self.scanner) == SYMBOLS)
        for row in self.scanner:
            _check(type(row) is ScannerRow)
            row.validate()
        prices = {row.symbol: row.price for row in self.scanner}
        _check(type(self.series) is tuple and len(self.series) == len(SYMBOLS) * len(TIMEFRAMES))
        _check(all(type(series) is ChartSeries for series in self.series))
        _check(tuple((s.symbol, s.timeframe) for s in self.series)
               == tuple((s, t) for s in SYMBOLS for t in TIMEFRAMES))
        for series in self.series:
            _check(type(series) is ChartSeries)
            series.validate()
            _check(series.candles[-1].close == prices[series.symbol])
        _check(type(self.trades) is tuple and 0 < len(self.trades) <= 12)
        for trade in self.trades:
            _check(type(trade) is DemoTrade)
            trade.validate()
        _check(type(self.equity) is tuple and 2 <= len(self.equity) <= 128)
        for value in self.equity:
            _check(type(value) is int and value >= 0)

    def __repr__(self):
        return '<DashboardSnapshot read-only demo>'

    def to_payload(self):
        DashboardSnapshot.__post_init__(self)
        return {
            'system': {'brand': 'VELOCITY TRADING GROUP', 'mode': self.mode,
                       'trading_authority': self.trading_authority, 'read_only': True,
                       'live_supported': self.live_supported, 'demo_market_data': self.demo_market_data,
                       'health': 'LOCAL UI READY', 'restart_quarantine': self.restart_quarantine},
            'broker': {'connection': 'NOT CONNECTED', 'gateway': self.gateway,
                       'account_mode': self.account_mode, 'snapshot_mode': None},
            'strategy': {'name': 'ABCD BREAKOUT', 'direction': 'LONG ONLY',
                         'timeframes': ['1m', '15m'], 'context': 'Weekly',
                         'automation': 'NOT AUTHORIZED', 'specification': 'Frozen v1.0'},
            'scanner': {'demo': True, 'rows': [row.payload() for row in self.scanner]},
            'chart': {'demo': True, 'timeframes': list(TIMEFRAMES), 'series': [
                {'symbol': series.symbol, 'timeframe': series.timeframe, 'demo': True,
                 'candles': [candle.payload() for candle in series.candles]}
                for series in self.series]},
            'evidence': {'active': False, 'decision': 'No active evidence loaded',
                         'historical_lifecycle': None, 'execution_lower_bound': None,
                         'possible_remaining_upper_bound': None, 'current_remainder': 'UNKNOWN',
                         'current_position': 'UNKNOWN',
                         'limitation': 'HISTORICAL EVIDENCE ≠ CURRENT BROKER STATE'},
            'trades': {'demo': True, 'rows': [trade.payload() for trade in self.trades]},
            'performance': {'demo': True, 'daily_pnl': '+$428.16', 'trades': '8',
                            'win_rate': '75.0%', 'buying_power': '$25,000.00', 'risk_used': '0.4%',
                            'equity': {'demo': True, 'values': list(self.equity)}},
            'activity': [
                {'kind': 'foundation', 'label': 'PRE9 safety foundation qualified', 'detail': 'Review & Windows validation reported complete.'},
                {'kind': 'safety', 'label': 'Trading authority remains blocked', 'detail': 'The first real PAPER order is not authorized.'},
                {'kind': 'safety', 'label': 'Gateway Read-Only required', 'detail': 'No broker connection is made by this dashboard.'},
                {'kind': 'local', 'label': 'Local read-only workspace', 'detail': 'Visuals run entirely on this computer.'},
                {'kind': 'demo', 'label': 'Demo chart data loaded', 'detail': 'Synthetic candles. No live market feed.'},
            ],
        }
