# TradingView strategy code

The first chart-only prototype is [`abcd_chart_prototype.pine`](abcd_chart_prototype.pine). It marks confirmed A/B/C pivots, a candidate tightening base below A resistance, and a confirmed one-minute close above resistance.

## Important limits

- This is an indicator for visual review, not a `strategy()` backtest and not an order system.
- It intentionally has no `alertcondition()` and cannot check IBKR account/order state, spread, Level 1/2, market cap, average daily volume, halts, buying power, risk locks, stop placement, or bridge status.
- The built-in swing-pivot confirmation means A/B/C labels appear several bars after the pivot. Changing pivot length changes those points.
- The initial numeric defaults are examples only and are unvalidated. Adjust them in the indicator settings during chart review; don't route them to an account.
- Higher-timeframe context uses only the last confirmed 15-minute and weekly bars to avoid acting on developing candles.

Never put bridge secrets or account credentials in Pine source or alert messages. Add alert logic only after the visual candidate rules and execution boundaries have been reviewed.
