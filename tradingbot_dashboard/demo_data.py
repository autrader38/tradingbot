"""Bundled synthetic fixtures. No provider, current clock, account or broker I/O."""

from datetime import datetime, timedelta, timezone
from .snapshot import Candle, ChartSeries, DashboardSnapshot, DemoTrade, ScannerRow, SYMBOLS, TIMEFRAMES


# Symbol, price in cents, change in hundredths of a percent, market cap dollars,
# average 10-day shares, synthetic volatility, relative volume, score, state.
# Boundary fixtures are intentional; none are provider data or active signals.
_UNIVERSE = (
    ('ABCD', 420, 842, 120_000_000, 2_100_000, 'High', 4.8, 94, 'READY'),
    ('VELA', 375, 568, 85_000_000, 1_300_000, 'High', 3.2, 87, 'FORMING'),
    ('NEXA', 640, -214, 32_000_000, 940_000, 'High', 1.7, 68, 'WAITING'),
    ('QBIT', 499, 1276, 240_000_000, 3_200_000, 'Very High', 6.4, 91, 'WATCHING'),
    ('MTRX', 500, 321, 160_000_000, 1_180_000, 'High', 2.6, 76, 'FORMING'),
    ('LUMA', 285, 300, 50_000_000, 1_000_001, 'High', 2.2, 82, 'READY'),
    ('NOVA', 165, 1850, 75_000_000, 2_400_000, 'Very High', 8.1, 96, 'READY'),
    ('AERO', 360, 299, 90_000_000, 1_800_000, 'High', 1.8, 72, 'WAITING'),
    ('FLUX', 410, 640, 49_999_999, 2_000_000, 'Very High', 4.2, 84, 'FORMING'),
    ('ORBI', 195, 450, 50_000_000, 1_000_000, 'High', 2.5, 79, 'WATCHING'),
    ('VOLT', 480, 925, 125_000_000, 1_800_000, 'Very High', 5.1, 90, 'READY'),
    ('SOLA', 120, 710, 60_000_000, 1_000_001, 'High', 3.6, 81, 'FORMING'),
    ('KITE', 350, 325, 95_000_000, 1_300_000, 'High', 1.2, 73, 'WATCHING'),
    ('PICO', 85, 2100, 8_000_000, 350_000, 'Very High', 9.2, 64, 'WAITING'),
    ('PRSM', 525, 510, 300_000_000, 2_300_000, 'High', 2.9, 88, 'FORMING'),
    ('ZENA', 240, -125, 65_000_000, 1_600_000, 'High', .8, 59, 'WAITING'),
    ('CORA', 455, 302, 51_000_000, 1_100_000, 'Very High', 2.7, 85, 'WATCHING'),
    ('IONX', 205, 1440, 180_000_000, 2_700_000, 'Very High', 7.2, 93, 'READY'),
    ('AXON', 780, 820, 1_200_000_000, 3_100_000, 'High', 3.3, 89, 'WATCHING'),
    ('LYRA', 310, 300, 55_000_000, 1_400_000, 'High', 1.5, 74, 'FORMING'),
    ('TESS', 430, 120, 70_000_000, 2_100_000, 'High', 1.1, 62, 'WAITING'),
    ('VYRA', 495, 380, 78_000_000, 1_500_000, 'Very High', 2.1, 78, 'FORMING'),
    ('ELIO', 1250, 440, 500_000_000, 3_500_000, 'High', 2.4, 83, 'WATCHING'),
    ('RUNE', 475, 450, 65_000_000, 1_250_000, 'Very High', 2.5, 79, 'WATCHING'),
)


def _candles(symbol_index, timeframe_index):
    count = (96, 72, 64, 52)[timeframe_index]
    minutes = (1, 15, 1440, 10080)[timeframe_index]
    start = datetime(2025, 2, 12, 14, 30, tzinfo=timezone.utc) - timedelta(minutes=minutes * (count - 1))
    price = 1000
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
        candles.append(((start + timedelta(minutes=index * minutes)).isoformat(),
                        opening, max(opening, price) + wick, min(opening, price) - wick, price, volume))
    # Each timeframe ends at the same declared scanner price. Integer-cent scaling
    # preserves OHLC ordering and positivity, even for the cheapest demo symbol.
    anchor = candles[-1][4]
    target = _UNIVERSE[symbol_index][1]
    def scaled(value):
        return max(1, ((value - anchor + 1000) * target + 500) // 1000) / 100
    return tuple(Candle(time, scaled(opening), scaled(high), scaled(low), scaled(close), volume)
                 for time, opening, high, low, close, volume in candles)


def build_demo_snapshot():
    series = tuple(ChartSeries(symbol, timeframe, _candles(si, ti))
                   for si, symbol in enumerate(SYMBOLS) for ti, timeframe in enumerate(TIMEFRAMES))
    scanner = tuple(ScannerRow(symbol, cents / 100, change / 100, average + 740_000 + i * 13_000,
                               relative, 'ABCD', score, status, cap, average, volatility)
                    for i, (symbol, cents, change, cap, average, volatility, relative, score, status)
                    in enumerate(_UNIVERSE))
    trades = (DemoTrade('ABCD', 4.05, 4.20, 200, 30.00, '10:42:18'),
              DemoTrade('QBIT', 4.75, 4.99, 150, 36.00, '10:18:06'),
              DemoTrade('VELA', 3.85, 3.75, 120, -12.00, '09:58:42'),
              DemoTrade('MTRX', 4.65, 5.00, 240, 84.00, '09:46:12'))
    equity = tuple(25000 + i * 13 + ((i * 47) % 93) - 46 for i in range(36))
    return DashboardSnapshot(scanner, series, trades, equity)
