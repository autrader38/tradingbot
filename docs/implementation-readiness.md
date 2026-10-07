# ABCD v1 implementation readiness

The user's strategy-inputs document defines a long-only ABCD breakout and paper-only scope. This note translates it into engineering gates. Do not silently fill unresolved items with assumptions.

## Can be represented now

- Long-only, regular-hours entries from 9:30 a.m. to 3:30 p.m. America/New_York on the U.S. market calendar.
- A one-minute candle must close above a previously established resistance; touching resistance is not an entry.
- Initial stop is below the confirmed C higher low. Never widen it.
- Take half off at 2R; manage the remainder under a deterministic exit rule.
- Maximum 5 new trades/day, 1% of account equity risk/trade, stop new entries at -2% realized daily P/L or after 3 consecutive losses.
- Do not enter on stale/missing data, uncertain broker state, duplicate exposure, excessive spread, poor liquidity, halt/abnormal conditions, or missing protection.
- Paper only; the strategy may not control or change its own rules.

## Required before executable strategy alerts

1. **Swing and pattern measurement:** pivot confirmation length or another swing-point method; minimum A impulse size; allowed B retracement; minimum C higher-low margin; D consolidation lookback; objective compression formula; how many tests define resistance; resistance tolerance.
2. **Breakout quality:** 1-minute volume baseline/lookback and required multiple; maximum extension beyond resistance; precise rule for a non-bearish 15-minute structure.
3. **Weekly resistance:** objective source for major resistance and minimum required reward room. This must avoid look-ahead and use only confirmed weekly bars.
4. **Market quality:** maximum spread (absolute or percent), minimum real-time volume/liquidity, halt/abnormal-market source, and whether Level 2 is required. TradingView/Pine alerts may not expose all broker-side order-book and account state needed for these checks.
5. **Order behavior:** entry order type, time-in-force, partial-fill handling, stop order type/placement behavior, end-of-day policy, and what happens if the bridge or broker rejects a protective stop.
6. **Deterministic runner exit:** define significant resistance, lower-high pattern, prior 1-minute swing-low confirmation, VWAP-loss confirmation, and breakout-failure rules. Several are currently descriptive rather than executable.
7. **Paper acceptance:** observation period/sample size, metrics including fees/slippage, and criteria to stop, revise, or continue.

## Implementation sequence

1. **Done:** write a non-executing chart prototype with configurable thresholds.
2. Apply it to a 1-minute TradingView chart and compare candidates to the supplied example; record false positives and missed setups.
3. Freeze a versioned threshold set; backtest with confirmed higher-timeframe data and realistic costs.
4. Add TradingView alerts only after the above gates are reviewed.
5. Route alerts to an IBKR paper account through the selected hosted bridge; reconcile every alert against broker orders and fills.

The prototype must label values as unvalidated and must not place or route orders. No numerical threshold values are approved by the current specification unless they are explicitly stated above.
