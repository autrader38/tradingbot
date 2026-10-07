# Strategy inputs needed

Complete these for one setup at a time. Leave unknown items blank; do not infer them from an attractive chart example.

## First setup

## Strategy 1 — ABCD Breakout

- Setup name: ABCD Breakout

- Long only, short only, or either:
Long only

- Chart timeframe(s):
1 week, 15 minute, 1 minute

- Pattern definition:
A = Strong upward price movement creating the initial impulse.
B = Price pulls back from the A move.
C = Price establishes a higher low after the B pullback.
D = Price consolidates/tightens below a clearly defined resistance level.
The setup is complete when price breaks above the D/resistance level.

The bot must identify A, B, C, and D objectively rather than using subjective visual judgment.

- Exact entry trigger:
Enter long only when ALL required conditions are true:

1. A-B-C-D structure has been identified.
2. C is a higher low relative to the beginning of the B pullback.
3. D is trading below or directly at the identified resistance level.
4. The D consolidation is tightening rather than expanding.
5. Price breaks above the D/resistance level.
6. The 1-minute candle breaks and closes above the resistance level.
7. Breakout volume is greater than the recent 1-minute volume baseline.
8. The breakout is not excessively extended from the resistance level at the time of entry.
9. The 15-minute structure is not bearish.
10. The weekly chart does not show immediate major resistance that would leave insufficient room for the trade.
11. Spread and liquidity meet the risk requirements.
12. No prohibited market/news conditions are active.

Initial implementation should use a breakout confirmation rather than entering simply because price touches resistance.

- Conditions that must already be true:

1. Clear A impulse move.
2. B pullback.
3. C higher low.
4. D consolidation beneath resistance.
5. Resistance must have been tested/established by prior price action.
6. Price range during D must contract relative to the earlier portion of the setup.
7. Volume should generally contract during the consolidation and expand on the breakout.
8. 15-minute structure must support the long trade.
9. Weekly structure must not show an immediately overhead major resistance level that makes the trade unattractive.
10. Stock must have sufficient liquidity for the intended position size.
11. Spread must be acceptable.
12. No active risk lockout or daily-loss lockout.
13. Only one position may be opened for the same setup unless a later version explicitly allows scaling.

- Invalidation / stop rule:

Initial stop is placed below the C higher-low / structural support level.

The stop must be placed immediately after entry and must exist before the position is considered fully active.

If price breaks the structural C support before the planned profit target is reached, exit the position.

If the calculated stop would create a loss larger than the maximum permitted risk, the trade must be rejected rather than increasing the stop distance.

The bot must never widen a protective stop simply to avoid taking a loss.

- Profit-taking and exit rule:

Initial position is divided into two portions.

1. First 50%:
Take partial profit at the first predetermined profit target based on the setup's risk/reward calculation.

Initial target: 2R, where R is the distance between entry and the initial structural stop.

2. Remaining 50%:
Allow the position to continue while the breakout remains healthy.

The remaining position exits when one of the following objective conditions occurs:

- Price reaches a significant resistance level identified before or during the trade.
- Price fails to make a new high and forms a defined lower-high structure.
- Price breaks below the prior 1-minute swing low after the breakout.
- Price loses VWAP after the breakout and the defined confirmation conditions are met.
- The original breakout structure fails.
- Protective stop is hit.

The bot must record which exit condition caused the trade to close.

The remaining-position resistance exit must be deterministic and testable. Do not use an undefined AI judgment such as "the chart looks weak."

- Trading session and days:

Monday-Friday on regular U.S. equity market days.

No new entries before the regular market open unless a separate premarket strategy is explicitly enabled.

Primary trading window:
9:30 AM - 3:30 PM Eastern Time.

No new entries after 3:30 PM Eastern Time.

All positions must be managed according to the strategy's exit rules and the configured end-of-day policy.

The bot must use the actual U.S. market calendar rather than assuming every Monday-Friday is a trading day.

- Symbols / price and liquidity limits:

Initial universe:

U.S.-listed stocks.

Preferred price:
$0.50 - $10.00.

Minimum market capitalization:
$50 million.

Minimum average daily volume:
1 million shares.

Prefer high-volatility stocks with meaningful intraday movement.

The bot must additionally require sufficient real-time liquidity and an acceptable bid/ask spread before entering.

The permanent user watchlist may contain stocks outside these scanner limits, but they must still pass the risk/liquidity checks before trading.

- Maximum position size or dollars at risk:

Initial paper-trading rule:

Risk no more than 1% of total account equity on a single trade.

The position size must be calculated from:

Account equity × maximum risk percentage ÷ distance from entry to stop.

Position size must also respect maximum-share and maximum-dollar limits configured in the risk engine.

Never increase position size simply because the setup score is higher.

Final dollar/share limits should remain configurable so they can be optimized during paper testing.

- Maximum trades per day:

Maximum 5 new trades per day.

A position that is partially exited still counts as one trade.

No new trades after the daily trade limit is reached.

- Daily stop / loss limit:

Stop initiating new trades when realized daily P/L reaches -2% of beginning-of-day account equity.

Also stop initiating new trades after 3 consecutive losing trades.

The daily loss limit cannot be overridden by the strategy engine.

Existing positions may continue to be managed according to their protective exits.

A future risk-engine version may add a hard account-level emergency kill switch.

- Conditions that prohibit a trade:

Do NOT enter if:

1. Required market data is unavailable or stale.
2. Level 1 data is unavailable.
3. Required Level 2 data is unavailable when Level 2 confirmation is enabled.
4. IBKR connection is unhealthy.
5. Account/order/position state cannot be reconciled.
6. A required protective stop cannot be established.
7. Bid/ask spread is too wide.
8. Liquidity is insufficient.
9. The stock is halted or trading conditions are abnormal.
10. The stock has already moved excessively far beyond the breakout level.
11. The breakout occurs directly into major overhead resistance with insufficient reward potential.
12. The 15-minute structure is materially bearish.
13. The weekly chart shows major immediate resistance that invalidates the expected reward.
14. Daily loss limit has been reached.
15. Maximum daily trade count has been reached.
16. Three consecutive losses have occurred.
17. Another position already occupies the same ticker.
18. The system detects duplicate or conflicting orders.
19. Market conditions are outside configured volatility/spread/liquidity limits.
20. The bot's internal state is uncertain.
21. The setup cannot be objectively identified.
22. The setup requires the AI to make an undefined subjective judgment.

If system state is uncertain, the default action is NO NEW TRADE.

- Example of a good setup (chart/screenshot):

Reference image supplied by user: [`references/abcd-example.gif`](../references/abcd-example.gif).

Visual reading: A marks the initial impulse high, B marks the pullback low, C marks the later higher-low/retest area, and the horizontal line appears to be the resistance from A. D is marked after price has broken above that resistance and extended higher. The drawing is useful as a positive visual reference, but it does not by itself specify the exact pivot or volume thresholds.

**Definition to reconcile before coding:** the written rules describe D as a tightening consolidation *below* resistance, with the breakout candle occurring after D. In the supplied image, D appears to label the post-breakout extension. Preserve both as reference material; do not silently change the written rule. The first signal prototype should wait until the user confirms whether D means the pre-breakout base or the breakout/extension leg.

A good example should show:

1. Weekly chart supporting the overall direction.
2. Strong A impulse.
3. B pullback.
4. C higher low.
5. D tightening consolidation.
6. Clearly identifiable resistance.
7. Decreasing range during consolidation.
8. Volume contraction during consolidation.
9. Volume expansion at breakout.
10. 15-minute structure supporting the breakout.
11. 1-minute breakout candle closing above resistance.
12. Sufficient liquidity and acceptable spread.
13. Adequate room to the next major resistance.

User will provide an actual screenshot later. Do not invent a screenshot or use a synthetic example as the final training example.

- Example of a bad setup (chart/screenshot):

A bad example should include situations such as:

1. No clear A-B-C-D structure.
2. C is not a meaningful higher low.
3. D is not tightening.
4. Resistance is unclear.
5. Breakout occurs on weak volume.
6. Breakout immediately fails.
7. Price is already excessively extended.
8. Major resistance is immediately overhead.
9. Poor liquidity.
10. Excessive bid/ask spread.
11. 15-minute structure is bearish.
12. Weekly structure conflicts with the trade.
13. Stop distance creates excessive risk.
14. Stock is halted or experiencing abnormal trading conditions.
15. Setup cannot be objectively measured.

User will provide an actual screenshot later. Do not invent a screenshot or use a synthetic example as the final training example.

- Strategy development requirement:

This is Strategy Version 1.0 and is intended for paper testing only.

Do not connect to a live brokerage account or place live orders.

Do not assume the initial thresholds are optimal.

All numerical thresholds must be configurable and tested against historical and forward-test data.

Do not optimize the strategy against a single historical period.

Avoid look-ahead bias when using the weekly and 15-minute timeframes.

The system must record the exact reason every trade was accepted, rejected, entered, partially exited, and fully exited.

Before live trading is permitted, the strategy must pass backtesting, forward testing, automated pape
## Paper success criteria

- Minimum observation period or sample size:
- Metrics to review (including costs and slippage):
- What result would make us stop or revise the setup:
- Who approves any strategy or risk-limit change: user approval required.
