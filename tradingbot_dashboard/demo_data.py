"""Bundled synthetic fixtures. No provider, current clock, account or broker I/O."""

from datetime import datetime, timedelta, timezone
from .snapshot import Candle, ChartSeries, DashboardSnapshot, DemoTrade, ScannerRow, SYMBOLS, TIMEFRAMES


def _candles(symbol_index, timeframe_index):
    count = (96, 72, 64, 52)[timeframe_index]
    minutes = (1, 15, 1440, 10080)[timeframe_index]
    start = datetime(2025, 2, 12, 14, 30, tzinfo=timezone.utc) - timedelta(minutes=minutes * (count - 1))
    price = (1240, 2875, 634, 4218, 1870)[symbol_index]
    seed = 731 + symbol_index * 997 + timeframe_index * 173
    candles = []
    for index in range(count):
        seed = (1664525 * seed + 1013904223) % 2**32
        # Intentionally constructed impulse, pullback and continuation shapes.
        phase = index / count
        drift = 7 if phase < .27 else (-5 if phase < .48 else (6 if phase < .82 else -2))
        change = int(seed % 25) - 12 + drift + timeframe_index
        opening = price
        price = max(100, price + change)
        wick = 4 + (seed >> 9) % 14
        volume = 18000 + seed % 115000 + (45000 if phase > .48 and change > 8 else 0)
        candles.append(Candle((start + timedelta(minutes=index * minutes)).isoformat(),
                              opening / 100, (max(opening, price) + wick) / 100,
                              (min(opening, price) - wick) / 100, price / 100, volume))
    return tuple(candles)


def build_demo_snapshot():
    series = tuple(ChartSeries(symbol, timeframe, _candles(si, ti))
                   for si, symbol in enumerate(SYMBOLS) for ti, timeframe in enumerate(TIMEFRAMES))
    changes = (8.42, 5.68, -2.14, 12.76, 3.21)
    volumes = (2_840_000, 1_620_000, 940_000, 4_210_000, 1_180_000)
    relative = (4.8, 3.2, 1.7, 6.4, 2.6)
    scores = (94, 87, 68, 91, 76)
    statuses = ('READY', 'FORMING', 'WAITING', 'WATCHING', 'FORMING')
    scanner = tuple(ScannerRow(symbol, series[i * 4].candles[-1].close, changes[i], volumes[i],
                               relative[i], 'ABCD', scores[i], statuses[i])
                    for i, symbol in enumerate(SYMBOLS))
    trades = (DemoTrade('ABCD', 12.85, 13.42, 200, 114.00, '10:42:18'),
              DemoTrade('QBIT', 41.25, 42.36, 150, 166.50, '10:18:06'),
              DemoTrade('VELA', 28.40, 28.11, 120, -34.80, '09:58:42'),
              DemoTrade('MTRX', 18.65, 19.41, 240, 182.40, '09:46:12'))
    equity = tuple(25000 + i * 13 + ((i * 47) % 93) - 46 for i in range(36))
    return DashboardSnapshot(scanner, series, trades, equity)
