# ABCD Breakout — Frozen Strategy Spec v1.0

Status: **FROZEN — user approved Decisions #1–47 and authorized documentation.**

**RESEARCH BACKTEST MODEL — V1.0**

**ZERO-FRICTION BASELINE**

**FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH AND PARTIAL FILLS NOT MODELED**

This is the canonical implementation specification for the initial long-only historical ABCD research strategy. [Parameters](strategy-parameters-v1.0.md) contains the frozen defaults and operators; [decision history](strategy-decision-history.md) maps all 47 approved decisions and their supersessions. Together these documents constitute Strategy Spec v1.0. The sections below contain only the final authoritative behavior, not superseded alternatives.

The strategy-design phase is complete. No additional strategy decisions are required before implementation. Freezing the specification does **not** establish that suitable historical data are available, that a backtester has been implemented or validated, or that fills are realistically executable in paper/live markets. Parameters are initial research values, not proven optimal values or profitability claims. A changed parameter/model must be recorded as a separate research configuration; do not silently alter v1.0.

Current authorization covers documentation only. No broker connection, TradingView connection, order placement, backtester construction, commit, or push is part of this documentation change. The existing Pine indicator is a historical chart prototype, not an implementation of this frozen specification.

## 1. Purpose, definitions, and authority

The strategy seeks a strong intraday upward impulse, a controlled pullback that holds above the impulse midpoint, and a confirmed resistance breakout. A/B/C formation alone never permits entry.

| Point | Authoritative meaning |
|---|---|
| A | Beginning of the impulse: the selected lowest eligible low before activation, frozen when the candidate activates |
| B | Highest eligible impulse high; resistance, with the latest permitted equal-high origin |
| C | Valid pullback/higher-low area above A and the A–B midpoint; provisional until qualified consolidation locks it |
| D | Confirmed breakout above B, not the pre-breakout base |

One-minute intervals govern pattern formation, entry, and position management. Completed current-session 15-minute blocks govern entry context. Completed historical weekly bars govern resistance/room. Both higher-timeframe filters are mandatory. No moving-average, discretionary visual, AI, VWAP, news, or other substitute gate/exit is introduced by v1.0. Broker/bridge candidates and the reference image do not override approved measurements.

Source: #1–#47, especially #1, #40 and #44–#47.

## 2. Time, intervals, and information availability

Use `America/New_York` and the authoritative exchange calendar, including DST, holidays, weekends and early closes. Use actual session open and close; normal sessions open at 09:30. Unknown official boundaries make the session invalid for testing until resolved. Unscheduled exchange-wide halts/emergency closures are a separate future treatment, not fabricated scheduled sessions.

- New entries may occur from actual regular-session open, **strictly before** `session_close - 30 minutes`.
- D confirmation does not reserve an entry. The next-interval entry itself must meet the cutoff. A D candle labeled 15:29 on a standard session implies an entry at 15:30, which is prohibited.
- Forced liquidation is irrevocably scheduled at the **open of the final regular-session minute**, `session_close - 1 minute`.
- Normal examples: entry cutoff 15:30 exclusive; liquidation 15:59. A 13:00 early close means cutoff 12:30 exclusive and liquidation 12:59.
- No pattern state carries overnight; no intentional overnight position is permitted. Expire remaining untriggered setups when no permitted next-interval entry can remain.

Maintain interval start, interval end, completion/availability time, modeled event time, and structural origin time separately. A minute labeled 10:21 represents its scheduled 10:21 interval; it is completed only at its end. OPEN events are modeled at the interval start under the approved research abstraction, not claimed exact first-trade times. Do not invent a sub-minute timestamp for an intrabar stop/target. Completed-candle decisions use completion time and may not act earlier.

Only already-completed information may enter lookbacks. Current trigger/attempt volume is excluded from its own comparison history. Current-day final prices, volume and future performance must not influence earlier decisions. Already-known structural confirmation discovered at activation is recorded separately from detector activation; no retroactive C lock, D confirmation or trade is allowed.

Source: #5, #10–#12, #19, #25, #27, #29, #44–#47.

## 3. Market-data classification and continuity

Classify every eligible minute before strategy use:

| Class | Meaning and permitted use |
|---|---|
| `TRADED` | Trustworthy qualifying trades/OHLCV; eligible price candle under the relevant session/state rules |
| `NO_TRADE` | Provider positively verifies zero qualifying trades; zero volume, no independent OHLC |
| `MISSING` | Expected activity cannot be established; absence is not proof of no trades |
| `INVALID` | Corrupt/impossible/conflicting data that cannot be authoritatively resolved |

Validate timestamps/order, positive prices, nonnegative volume, `high >= low`, high at least open/close, low at most open/close, and unresolved duplicates/conflicts. Use authoritative provider corrections and trade-condition filtering. Preserve correction/source provenance. Do not repair price history by interpolation or forward-filling; do not classify questionable data based on trading outcomes.

### No position open

Verified no-trade intervals do not automatically reset a setup. They cannot establish A/B/C, move price levels, provide a higher low, confirm B, satisfy consolidation, create D or count as attempts. They may supply zero volume in approved chronological baselines. They advance elapsed-minute timers, not valid-traded-candle counts.

Missing/invalid data invalidates an active pre-entry setup immediately with `DATA_GAP`/`INVALID_DATA`. It breaks price and volume continuity even if no setup is active. Detection resumes only with fresh trustworthy data after the gap, using the fresh-start rules. Rebuild required history after the gap; never bridge it with older bars. Apply the same distinction to premarket volume history.

### Position open

Missing/invalid required data pauses the **entire shared portfolio** at the affected timestamp. Preserve the last valid state; do not assume the position survived, invent fills, skip unknown movement, or fund later decisions. Resume only with reliable replacement historical data, replaying from the last valid state. If unresolved, the run is incomplete and affected trades unresolved. Exclude unresolved trades from completed-trade statistics; do not fabricate ending equity or simulate subsequent sessions from it. The same rule applies to a missing/invalid required liquidation minute.

Verified no-trade intervals cannot trigger stops/targets or recalculate trailing lows. Leave quantities and realized P&L unchanged. On resumed trading, apply the normal open/gap/intrabar rules to the first valid traded candle. Last trustworthy eligible traded prices may be carried forward **for reporting-only marks**, labeled as such. They cannot create fills, signals, highs/lows, trailing updates, or new-entry eligibility.

Source: #14, #27–#33, #41, #46–#47.

## 4. Point-in-time stock universe

All required gates must pass at activation and final entry as applicable:

| Gate | Frozen requirement |
|---|---|
| Security | U.S.-listed common operating-company stock on a supported exchange |
| Current price | `<= $5.00`; no additional minimum price |
| Current-day change | `>= 3%` versus compatible prior regular-session official close |
| Point-in-time market cap | `>= $50,000,000` |
| ADV10 | Previous 10 actual completed regular sessions' average share volume `> 1,000,000` |
| ADR20 | Prior 20 actual completed regular sessions' average daily range percentage `>= 5%` |

Exclude ETFs, ETNs, closed-end/mutual funds, preferreds, warrants, rights, units, options, bonds, crypto products, OTC securities, ADRs/depositary receipts and other non-common instruments where reliably identified. Do not automatically exclude SPACs, recent IPOs, reverse splits or unusual corporate actions when otherwise eligible and reliably modeled. History sufficiency gates still apply.

Include historically eligible inactive/delisted/merged/bankrupt names. Stable security identifiers must distinguish ticker changes and ticker reuse. Never select only today's survivors or eventual successful movers. Disclose provider coverage limitations and do not claim survivorship-bias-free results without supporting data.

### Market cap

Prefer authoritative point-in-time market cap. If derived, use applicable historical shares outstanding times compatible contemporaneous price, documenting methodology and availability. Today's shares outstanding cannot reconstruct earlier issuance/repurchases/conversions by split arithmetic alone. Unavailable trustworthy data excludes the symbol/date from new entries with `MARKET_CAP_DATA_UNAVAILABLE`; it does not terminate the portfolio merely because an unentered security cannot qualify.

### ADV10

Average regular-session share volume from the actual previous ten completed eligible exchange sessions; exclude today, extended hours and future sessions. Valid early-close sessions count. Normalize historical share volumes to the evaluation-date share basis through already-effective splits. Fewer than ten trustworthy sessions fails with `INSUFFICIENT_ADV_HISTORY`; do not estimate or replace missing history with future sessions.

### ADR20

For each actual prior 20 completed regular sessions, calculate `(high - low) / close * 100` using that session's close. ADR20 is the arithmetic average of those percentages. Exclude today and extended hours. Use unrounded values for comparisons.

- LOW: `< 5%`; fails.
- HIGH: `>= 5% and < 10%`; passes.
- VERY_HIGH: `>= 10%`; passes. No maximum ceiling.

Fix ADR20 before the current session; today's movement cannot alter it. Require exactly 20 trustworthy sessions, including valid early closes. Insufficient history gives `INSUFFICIENT_ADR20_HISTORY`; missing/invalid required observations give `ADR20_DATA_UNAVAILABLE`. Do not skip a bad required session and substitute the 21st. Simple consistent split scaling leaves each session's percentage invariant, but adjustment mismatches must not contaminate it.

ADR20 is distinct from today's +3% gate and the pattern's +8% impulse. Preserve continuous values and category, not only pass/fail. No arbitrary spread threshold is approved; record available bid/ask, absolute spread and spread/midpoint with timestamps. Missing quotes are disclosed, never reconstructed from OHLC. ADV10 does not prove executable order depth.

Source: #37–#38, #43.

## 5. Dynamic eligibility throughout pre-entry state

At every completed valid traded candle while a setup is active, use its **close** to evaluate price `<= $5` and daily change `>= 3%`. Intrabar highs above $5 or lows below the +3% level alone do not fail these universe gates. They still participate in other explicit high/low-based rules.

Before actual entry, failure terminates the setup immediately; do not pause, preserve its structure for recovery, or revive it. Applies from A candidate through pending entry. Use `DYNAMIC_PRICE_ABOVE_MAX`, `DYNAMIC_DAILY_CHANGE_BELOW_MIN`, or `DYNAMIC_MULTIPLE_ELIGIBILITY_FAILURES`, preserving all failed conditions.

At the scheduled entry, recheck both gates with the trustworthy **open**. Failure cancels and consumes with the corresponding `ENTRY_DYNAMIC_*` reason; no accepted trade or realized P&L results. Static/day-level inputs remain valid under their own point-in-time definitions; do not silently apply intraday price-transition logic to them. Erroneous reference inputs are research/data-integrity issues.

Verified no-trade intervals introduce no new price; retain last known eligibility unless an authoritative input independently changes it. A later fresh setup needs fresh valid traded candles after both termination and eligibility restoration. With no active setup, simply prohibit activation while ineligible; restored eligibility is not itself a new A.

After a valid entry, later failure of price, daily change, market cap, ADV10, ADR20 or higher-timeframe entry gates does not force an exit. Approved position-management rules alone govern that trade.

Source: #37–#39.

## 6. Session eligibility and canonical volume baselines

A/B/C/D and all price-pattern participants use **current regular-session traded candles only**. A lookback may contain up to ten available eligible fresh traded candles; ten are not required to start detection. No premarket or prior-day price participants and no overnight pattern state.

### Previous-20 relative volume

For each evaluated candle independently, use exactly the **20 immediately preceding chronological eligible minutes** on the same trading date, excluding that candle. Same-day verified premarket history from 04:00 until regular-session open may fill older slots. No prior-day history. Verified no-trade intervals occupy slots at zero volume; do not skip them. Missing/invalid gaps break continuity; insufficient history cannot be shortened or estimated.

`baseline20 = sum(previous20 interval volumes) / 20`.

- If positive, `RVOL = evaluated_volume / baseline20`.
- If exactly zero, RVOL is undefined and every required relative-volume qualification **fails**, even with positive current volume. Record `RVOL_BASELINE_ZERO`; no infinity, synthetic denominator, epsilon or ranking sentinel.
- Zero baseline is not missing/invalid data. A mixed baseline, even one positive interval and 19 verified zeros, is valid. No minimum positive-slot count or baseline-volume threshold is added.

Activation/impulse participants require volume `>= 2 * baseline20`; D requires `>= 1.5 * baseline20`. Both require a positive, complete baseline. A participant lacking a qualifying baseline may be bypassed if another eligible participant independently qualifies. Entry ranking uses only finite valid D RVOL.

### A–B impulse average

Arithmetic average of every traded impulse candle from A through the final B-origin candle, inclusive. Recalculate on a permitted higher or equal-high B-origin move. Exclude all post-B candles, including B-confirmation candles. Freeze at successful B confirmation. Do not remove unusual-volume impulse candles or change the frozen baseline after a C reset.

### C contraction and local D volume

C compares the three actual qualifying traded consolidation candles' average **strictly below** the frozen impulse average.

Each D attempt separately compares its volume **strictly above** the arithmetic average of the immediately preceding **three chronological regular-session intervals**, excluding the attempt. Verified no-trade slots contribute zero; do not reach farther back. Prior failed attempts naturally enter this rolling baseline. Never cross session open or a data-continuity reset. A zero local-three average may pass with positive attempt volume: this is an absolute comparison, not a ratio. The independent valid positive previous-20 requirement remains mandatory.

Source: #2–#3, #18, #23–#24, #27–#30, #46–#47.

## 7. A activation and A–B impulse

Only one active candidate per ticker. At a completed potential trigger candle, select A from the lowest low of up to the previous ten eligible fresh current-session **traded** candles, excluding the trigger. Equal lows select the **earliest** occurrence. With no eligible prior price candle, no A can be selected.

Activation requires `(trigger_high - A) / A >= 0.03` and at least one participating candle from A through trigger inclusive with valid previous-20 RVOL `>= 2`. The trigger need not close 3% above A. A/activation timestamps differ; freeze selected A on activation and never move it backward/lower afterward.

Initialize provisional B from all eligible completed candles from A through trigger, using the highest high and **latest eligible equal-high occurrence**, subject to the impulse deadline. Apply the sequential deadline/confirmation rules to already-known candles; reject initialization that would start an already-invalid candidate. Do not select a lower B, ignore intervening highs, or create past trades. This highest-high initialization does not override the independent sequencing test: A's candle may establish its low but its own high cannot prove a later rise from that low with unknown ordering; subsequent valid traded candles provide price-progression evidence. Keep the A candle in the impulse-volume baseline as approved.

Qualifying impulse: at least 8% from frozen A to B, at least one A-through-B impulse participant with its own valid previous-20 RVOL >=2, and the approved 1–10 valid traded candle duration **including A as candle 1**. The minimum duration does not authorize credit for an unknown same-A-candle low-to-high sequence. No-trade minutes do not consume traded slots. If +8% is absent at candle 10's end, invalidate. The activation timestamp does not restart or extend the historical A-origin deadline.

Source: #2–#3, #20–#21, #24–#26, #29, #45.

## 8. Provisional B and confirmation

After the impulse reaches +8%, track qualifying provisional B at the highest eligible high. While movement is permitted inside the ten-traded-candle impulse window:

- Higher high updates B price and origin and restarts confirmation.
- Equal-high retest leaves price unchanged but moves origin to its **latest eligible occurrence**, also restarting confirmation.
- Either origin move recalculates the impulse-volume baseline and discards previous potential-C data; C clock restarts after the new origin.

B requires **two consecutive valid traded candles after its origin** with no higher high; by the second close, price must be at least 1% below B. No-trade intervals neither serve as confirmation candles nor break traded-candle consecutiveness. If the second confirmation close fails the pullback requirement, expire; do not wait additional candles.

At the impulse deadline provisional B becomes price-locked. Up to two additional valid traded candles may solely finish confirmation; they do not extend the impulse. A higher high after the deadline while unconfirmed invalidates; an equal-high retest after the deadline does not move origin. Origin on candle 8 may confirm with 9/10; origin on 9 with 10/next; origin on 10 with the next two traded candles. All remain subject to session boundaries.

At activation, already-completed eligible post-B candles may establish structural confirmation under exactly these rules. Record the logical historical second-confirmation close separately from activation; no action occurs before activation. B-confirmation candles supply potential C information but cannot officially create/lock C before B is successfully recognized as confirmed.

Source: #3, #21–#23, #25–#26, #29.

## 9. Provisional C, consolidation, and locked C

Let `impulse = B - A`, `retracement = (B - pullback_low) / impulse`. Eligible post-B price action starts with the first valid traded candle after the final B-origin candle; **the B-origin candle is not a C candle**. B-confirmation candles participate and consume C-development slots, with their unfavorable lows fully enforced after B confirms.

Valid provisional C is the lowest eligible pullback low at retracement `>= 20% and < 50%`, strictly above midpoint and A. A valid lower low before locking updates provisional C and resets all associated higher-low/consolidation progress. Invalid retracement invalidates; no retrospective low selection.

After the current provisional-C candle, require at least three completed valid traded consolidation candles. The most recent three being evaluated must satisfy:

- No low below current C; exact C touches permitted.
- Price remains at/below B; a trade above B before qualification is an early breakout that invalidates.
- Total range `max(highs) - min(lows) <= 0.5 * (B - A)`.
- Average volume strictly below the frozen A–B impulse average.
- At least one subsequent completed candle has low **strictly above** C.

No-trade minutes may occur between qualifying traded candles but do not count. A new valid lower C before locking restarts progress. Lock C only once every condition is known from completed candles and B is confirmed. C must lock within ten valid traded candles after the final B-origin candle; confirmation does not restart that clock. Expire at the end of its final permitted slot if not locked.

Locked C never changes up/down. A later low strictly below it before D invalidates; touching it holds. Do not revive the setup by moving C. Waiting candles/higher lows do not extend timers or redefine B/C.

Source: #4–#5, #16, #22–#23, #26, #29, #45.

## 10. Waiting, attempts, D, and setup consumption

After C locks, count at most ten **elapsed regular-session minutes**, starting with the interval after the locking candle. A first trade **above** B must occur by minute 10; otherwise expire at its end. Touching B is not an attempt and does not restart the timer. Both traded/no-trade intervals advance this clock.

First attempt stops that waiting clock and starts a five-elapsed-minute resolution clock **including the attempt interval as minute 1**. Valid D may confirm at minute 5's close; otherwise expire. Allow at most two failed attempts; attempt three must qualify or expire at its failed close. Both limits apply; earlier permanent invalidation/expiration controls.

Every completed attempt must satisfy all of:

- C was already locked and qualified before this attempt.
- Close `>= B * 1.0025` and `<= B * 1.02`.
- Volume `>= 1.5 * positive complete previous20 baseline`.
- Volume strictly greater than the rolling local-three baseline.
- No already-applicable structural/data/eligibility/session invalidation.

Wick-only attempts, insufficient close buffer, and insufficient volume may retry within limits. B/C remain fixed. Close more than 2% above B immediately expires as overextended; a later pullback cannot reuse that setup. A valid D confirms only at close and creates exactly one pending next-interval opportunity. D never guarantees a trade.

Source: #5, #16–#19, #29–#30, #39, #45–#46.

## 11. Pending entry and mandatory final filters

Schedule entry at the **immediately following regular-session interval's open**. Never use B, D close, later available trade or a synthetic price. Record D confirmation price and actual entry separately. Accept only if all approved conditions pass:

1. Trustworthy traded scheduled interval; permissible session time.
2. Open **strictly above B**, and **no more than 2% above B**.
3. Dynamic price/change and static/day eligibility gates.
4. Mandatory 15-minute and weekly context.
5. Reliable ticks for both initial executable stop and fixed target; positive risk.
6. Portfolio valuation, cash, exposure, position, size, trade-count and lockout eligibility.

Verified no-trade entry interval cancels with `ENTRY_NO_TRADE`; missing/invalid scheduled data cancels with `ENTRY_DATA_MISSING`/`ENTRY_DATA_INVALID`, never as guessed no-trade. No delayed opportunity. Any unfillable/permanently rejected opportunity consumes its setup; no cash/exposure, accepted-trade count or realized P&L is created. Quantity below two shares rejects. Fresh detection requires fresh price action under the reset rules.

### Fifteen-minute context

Construct fixed calendar-aligned regular-session 15-minute blocks; normal start blocks are 09:30–09:44:59, 09:45–09:59:59 and 10:00–10:14:59. Preserve exact start/end and completion. Premarket and previous-day blocks are excluded.

A block with at least one trustworthy traded minute and otherwise only traded/verified-no-trade minutes is valid. OHLC uses first traded open, maximum traded high, minimum traded low and last traded close; volume sums actual volume plus zero no-trade slots. Do not fabricate constituent prices.

Entirely no-trade blocks are `NO_TRADE_15M`, zero volume and no OHLC. Any required missing/invalid constituent produces `INVALID_15M_DATA`.

Select the **three immediately preceding chronological completed blocks**, T1 oldest, T2 middle, T3 newest. All three must be valid traded blocks; never skip a no-trade or invalid block. Require **both** `T3.high >= T1.high` and `T3.low >= T1.low`. Either failed comparison rejects; T2 must be valid but has no directional condition.

At 10:15 the 10:00 block is completed and usable; at 10:08 it is forming and excluded. Normal earliest context-ready entry is 10:15 if all three initial blocks qualify. Reject/consume for insufficient blocks, required no-trade blocks, invalid data or bearish structure. Later qualifying context cannot revive D. No moving-average substitutes.

### Weekly resistance and room

Use up to the previous 52 **completed applicable trading weeks**, excluding current week; require at least 12 candidate weeks. Use actual weeks, with legitimate no-session calendar weeks excluded and holiday-shortened trading weeks allowed. Authoritative regular-session daily/weekly history is preferred; do not automatically impose one-minute completeness rules on weekly sources.

A weekly swing candidate has high strictly above each of two immediately prior weekly highs and at least equal to each of two immediately following highs. Both right bars must already be completed/knowable before entry. Latest unconfirmed candidates are not resistance. Equal right-side highs do not disqualify the candidate.

The 52-week limit governs candidate origins, not context. Up to two older bars may supply left context only (up to 54 loaded bars); they cannot be resistance candidates. With 12–51 available weeks use all. A newly listed security's earliest candidates lacking genuinely nonexistent pre-listing left context are unevaluable, not a reason to reject the entire filter once minimum history is met. Never fabricate predecessor/security history.

Every candidate/comparison week must supply trustworthy required weekly data, at minimum its HIGH for swing comparisons; preserve full OHLC where available. Required missing/invalid weeks cannot be skipped, filled or bridged. If they prevent reliable search/confirmation, reject/consume with `ENTRY_WEEKLY_DATA_UNAVAILABLE`, not a no-resistance pass. Minimum-history failure uses `ENTRY_INSUFFICIENT_WEEKLY_HISTORY`.

Normalize candidate/comparison prices to compatible entry-date share units. Choose the **nearest confirmed swing-high price strictly above scheduled entry reference open**; ignore levels at/below it. Require `(resistance - proposed_entry_open) / proposed_entry_open * 100 >= 5`. No identified overhead swing after valid evaluation passes with `NO_IDENTIFIED_WEEKLY_RESISTANCE`; do not substitute all-time highs, round numbers or analyst targets. Failure consumes D. After entry neither context filter is an exit rule.

Source: #5, #12–#13, #31, #34–#35, #37–#42.

## 12. Executable levels, ticks, risk, and sizing

Determine valid security/date/price/market/order ticks from authoritative information. Never assume a universal $0.01. Use deterministic decimal/integer-safe arithmetic; do not rely on binary floating point for tick equality/floor/ceiling. Actual authoritative market fills retain their supplied precision; do not round them as invented order levels.

For actual simulated entry `E` and locked `C`:

```text
raw_stop = C * 0.995
initial_stop = floor_to_valid_tick(raw_stop)
risk_per_share = E - initial_stop
risk_budget = beginning_of_day_equity * 0.01
shares_by_risk = floor(risk_budget / risk_per_share)
raw_target = E + 2 * risk_per_share
target_2R = ceil_to_valid_tick(raw_target)
effective_target_R = (target_2R - E) / risk_per_share
```

Initial stop rounds downward, never upward to create extra size. Target rounds upward, never below mathematical 2R. Exact ticks remain unchanged. Require risk positive and stop below entry; effective target R is at least 2 subject only to negligible numeric precision tolerance. Target rounding does not increase quantity. Stop never widens after entry; target remains fixed after later stop changes.

Start with $10,000 simulated cash/equity. Whole shares, rounded **down**, need not be even. Quantity is the smallest permitted by:

- Risk formula above.
- 1,000 shares.
- `floor(0.20 * current_equity / E)`.
- Remaining 60% total market-exposure capacity, including proposed entry.
- Available cash, no borrowing/margin.

Also require fewer than three existing positions, no position in the same ticker, and daily entry eligibility. Reduce quantity if limits permit a smaller valid position; reject below two. Never add after entry, average down or scale into winners. Planned price risk is not an artificial cap on actual economic losses.

For breakeven, use E as reference; if required by valid stop-order increments, normalize **downward**, never above E. Trailing low candidates likewise normalize downward when required. Active stop is `max(previous_stop, normalized_breakeven_floor, normalized_trailing_candidate)` and never decreases.

Use applicable ticks when an order level is established/replaced. Do not rewrite executed fills for later regimes or invent broker treatment of invalidated active orders. Pre-entry unavailable stop/target tick rejects and consumes only that setup; portfolio continues. Open-position uncertainty stops the entire run when required order calculation/replacement becomes impossible, preserving last valid stop and state with `INCOMPLETE_TICK_SIZE_UNAVAILABLE_OPEN_POSITION`. Do not loosen, cancel or fabricate that existing stop; its presence does not authorize continuation. Mere unavailable future metadata does not invalidate already-valid orders if no required action needs it. Corrected historical tick metadata requires a clean deterministic rerun, not later-timestamp continuation or outcome-based patching. Record the specific source of uncertainty: reference metadata, historical market rules, security type, price tier, broker/exchange ambiguity, corruption or another documented cause; keep it distinct from OHLCV gaps and no liquidity.

Source: #6–#8, #13, #34–#36.

## 13. Protective stop, partial 2R, and runner

An active stop triggers on **low <= stop**, including touch; it is not a close-based exit. If open <= stop, reference exit is open; otherwise if reached intrabar, reference is stop. Preserve gap losses above intended risk; later approved adverse slippage can worsen execution but never improve it.

The fixed executable 2R target triggers on **high >= target**. If open >= target before filling, partial reference is open; otherwise reference is target. Sell `floor(original_quantity / 2)` once; retain the rest (101 → 50/51; 3 → 1/2). Initial quantity >= 2 prevents zero partial size.

After an unambiguous partial, runner stop activates at the executable breakeven reference described above. It may never move downward. If original active stop and unfilled target are both reached with unknown order, assume original stop first: no partial, no breakeven activation. If original stop is not touched but a candle reaches 2R and also entry/breakeven with unknown order, fill partial then conservatively close runner at its applicable breakeven level; log `SAME_BAR_2R_BREAKEVEN_AMBIGUITY`. Known target gap-at-open activates breakeven immediately; subsequent low may trigger it. Preserve actual entry reference and any required tick normalization separately. Do not infer that a known opening target fill occurred after a later low.

After **three additional valid traded candles following the partial-exit candle** complete, begin trailing. Exclude the partial candle from the activation count. No-trade intervals neither advance nor reset it. At each subsequent valid traded close, candidate is the lowest low of the previous three valid traded candles (the newly completed traded candle and preceding eligible traded history), normalized if required. Apply the non-decreasing max rule. The new level applies **prospectively**, never retroactively within its calculation candle. No-trade minutes provide no synthetic lows or recalculations.

Runner exits all remaining shares at active-stop touch, with normal gap/open reference rules. No discretionary VWAP, lower-high, weekly-resistance or MA exit is added. Record final exit reason and combined partial/runner P&L.

Source: #6, #8–#9, #15, #32, #34, #36.

## 14. EOD liquidation and unresolved runs

Force all remaining shares out at the final scheduled minute's actual open, modeled at interval start. Once closed, ignore subsequent trade movement. No-trade liquidation interval cannot provide an invented fill: keep the instruction irrevocably active and execute at the first subsequent trustworthy eligible trade **before official close**. It is a required exit, not delayed discretionary entry.

If none exists before close, preserve `UNRESOLVED_EOD_NO_LIQUIDITY`. At official close stop the entire shared run with `INCOMPLETE_UNRESOLVED_EOD_LIQUIDITY`, even if all other positions closed. Do not simulate next day, closing-auction/official-close/last-price/next-open fills, or intentional overnight carry. Missing/invalid liquidation data instead follows the data-pause rule, not verified no liquidity.

Preserve known cash, partial realized P&L, quantities, active stops, last trade and reporting marks distinctly. Never convert an unrealized mark to an exit or confirmed ending equity. Earlier completed trustworthy trades remain reportable. Exclude unresolved trades from completed statistics; metrics needing a completed later portfolio path are unavailable/incomplete. Separately report partial realized P&L, unresolved quantities/marks and termination frequency across independent runs. A future liquidation model must be separately approved and identified.

Source: #11, #14, #32–#33, #44.

## 15. Shared portfolio valuation, event order, and daily lockouts

One chronological shared-account simulation across tickers; do not combine independent ticker backtests afterward. Given identical data/configuration/starting state, results are identical.

### Event phases

At a shared minute boundary, the preceding interval has completed. Its completed-candle decisions and prospective stop updates are available before the next interval's modeled OPEN processing. This does not make a newly calculated stop applicable to the preceding calculation interval. Preserve both interval identity and information-availability time so a close-based D creates only the next interval's opportunity.

1. At modeled interval start, process **known opening-price exits/required management**, including gap stops, gap targets and scheduled liquidation, before new entries. Update quantities, cash, realized P&L, exposure, completion/loss streak and sticky lockouts.
2. Value each remaining position using its current trustworthy open. No later high/low/close/VWAP. If even one held ticker lacks a reliable current open, reject all new entries at that timestamp. Reporting-only carried marks do not satisfy this requirement.
3. Collect scheduled valid entries; rank D's finite previous-20 RVOL highest first, then alphabetical ticker. Evaluate sequentially; after each acceptance update cash, positions, exposure, trade count and portfolio state.
4. Process subsequent intraminute events under conservative stop-first rules. Where cross-ticker order is unknowable, alphabetical ticker is the deterministic final tie-breaker; never alter independent fill prices. Later intrabar proceeds/lockouts cannot fund or invalidate earlier opening entries.
5. At completion, use newly completed data for state decisions and prospective stop updates; never reinterpret earlier opening decisions with later OHLC/volume.

Same-timestamp opening exits may fund other tickers' subsequent entries under the explicit timestamp abstraction. This does not permit later intraminute exits to do so.

### Equity and exposure

`current_equity = cash + sum(remaining_shares * current_timestamp_open)`; total exposure is that market-value sum. Include proposed position in post-entry exposure. Current equity includes unrealized movement and incurred costs. Recalculate between simultaneous acceptances. Entry-only 20%/60% limits do not force resizing/liquidation when market movement exceeds them; no new entry is allowed when exposure already >=60% or resulting exposure would exceed the cap.

After resolved EOD liquidation, final cash is ending equity and next valid session's beginning equity. Risk budget and daily realized-loss threshold use that beginning-of-day equity and do not change intraday with profits/losses.

### Daily counters

Reset at each actual regular-session start: accepted entries, realized P&L, completed-loss streak and lockout state. Each accepted position is one trade; partial/runner legs are not additional entries.

Lock **all new entries for the remainder of the day** when any occurs:

- Fifth accepted entry.
- Cumulative **net realized** daily P&L `<= -0.02 * beginning_of_day_equity`.
- Third consecutive completed **net losing** trade.

Update daily realized P&L at every exit leg, including partials and allocated entry costs. Unrealized P&L does not control the daily loss lock. Completion requires all shares exited; final net P&L <0 increments the streak, >0 resets it, exactly zero resets it as breakeven. Partial legs alone do not change the streak. Process known chronological events in order, with approved deterministic ambiguity tie-breaks.

Locks are one-way even after later profits. Existing positions continue approved stops/targets/trailing/EOD management, not loss-lockout forced liquidation. Do not retroactively approve/reject earlier entries using later events.

Source: #7, #10–#13, #32–#33, #36, #44.

## 16. Costs and research fill abstraction

Whenever approved rules establish a fill, assume the **entire designated whole-share quantity** fills at the approved price. At 2R this means the whole designated partial quantity, not the original entire position. One-minute volume is not available size at that exact price; do not cap quantity by minute volume or manufacture partial fills. Position/risk limits still apply. No-trade, unknown data, tick failures and ambiguity rules still control whether a fill exists.

OPEN events use interval-start modeled timestamps, with historical opening trade prices. This does not assert an actual trade occurred at precisely that timestamp. Do not invent opening trades for no-trade intervals. Exact sub-minute sequencing is deferred; known opening-before-intrabar ordering remains authoritative.

Initial baseline commission, fees and slippage are all zero. Every applicable report must state:

> ZERO-FRICTION BASELINE
>
> TRANSACTION COSTS AND SLIPPAGE NOT YET MODELED.
>
> FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH AND PARTIAL FILLS NOT MODELED

Never label baseline performance broker-realistic, live-executable, guaranteed or expected live returns. Realistic deployment evaluation requires an approved verified cost/execution model later.

Architecture must separately support gross fill-based P&L, commissions, explicit fees, slippage impact and net P&L. Nonzero slippage worsens BUY up / SELL down from separately recorded reference prices; it never improves or creates impossible execution. Sizing uses the actual simulated fill. Target remains fixed under approved initial risk even if sell slippage produces less than 2R realized profit. Do not subtract slippage twice: it is already reflected in fill-based gross P&L.

`net_realized_PnL = gross_fill_based_realized_PnL - commissions - explicit_fees`.

Support later verified per-share/per-order/minimum/maximum commission schedules and direction/venue/date/quantity/notional fee behavior. Do not assume today's advertised fee schedule applies historically. Deduct incurred costs in cash/equity; for realized-exit accounting allocate entry costs proportionally across exited shares, with deterministic indivisible remainders reconciling exactly. Recognize exit costs at their fills. Use net daily/completed-trade results, never before-cost breakeven classifications.

Nonzero entry slippage's pre-fill rejection/resizing/partial interaction is deferred to an explicitly approved model; do not silently enable it in this zero-friction configuration. Cost-reserved risk sizing is not part of initial v1.0. Cost assumptions are selected before results, applied consistently and logged per research configuration.

Source: #36, #44.

## 17. Intrabar setup conservatism

Do not manufacture OPEN→LOW→HIGH→CLOSE or OPEN→HIGH→LOW→CLOSE paths. Evaluate whether **every plausible ordering** compatible with OHLC and start-of-candle state produces the same valid result. If so, use it. If unknown order could invalidate/prevent the setup or permit qualification, choose the adverse result and log `INTRABAR_SEQUENCE_AMBIGUITY`.

- A-origin low cannot receive credit for a same-candle sequential upward impulse. It may establish A; later valid traded candles supply progression evidence.
- A established before the current candle may use its high normally.
- A B-origin candle's own low is not post-B C evidence.
- Already-locked C break and apparent D in the same candle invalidate; no D/entry, using `C_INVALIDATION_INTRABAR_AMBIGUITY`.
- Other materially unknown invalidation/qualification conflicts follow the same principle. Enforce rules on state existing at candle start; newly created states cannot be treated as already active beforehand.

Close and aggregate-volume conditions remain completed-candle tests; they do not reveal high/low order or erase permanent invalidation. Do not allocate candle volume to invented intrabar segments. Do not invalidate order-invariant results merely because multiple events coexist. Known OPEN and CLOSE chronology remains usable. Reliable higher-resolution sequencing requires a separate consistently applied research model, not selective favorable resolution.

Source: #15, #45.

## 18. Fresh detection, overlap, and reset

One setup per ticker through candidate, B, C, attempts, D and pending entry; no overlapping replacement based on better-looking later action. No setup detection on a ticker while any position shares remain. Partial exit does not free it.

After pre-entry invalidation/expiration/consumption, price history for new A starts fresh after termination under the applicable rule. Canceled/no-trade entry opportunities cannot be retried; after a no-trade entry interval, the first later valid traded candle is the earliest new price participant. Dynamic-eligibility restoration imposes its additional fresh boundary. Every setup has a new unique ID; a used impulse cannot be recycled.

After final position exit, quarantine its **entire exit-containing minute**, even for an exit modeled at OPEN. Its OHLC cannot supply A/trigger/impulse/B/C/consolidation/attempt/D. The earliest participant is the first completed valid traded candle in a **wholly subsequent interval**. Example: final exit in 10:21 excludes all of 10:21; valid 10:22 may be first. Skip verified no-trade price slots; unknown data follows its own reset rules. Do not invent an exit at 10:21:30 or 10:21:59.999 to justify reuse.

Accumulate up to ten fresh traded price candles; fewer are allowed. Do not backfill earlier occupied/terminated price structure. Keep trustworthy chronological volume history separately: a price reset alone does not erase eligible previous-20 volume. Pre-exit/exit-containing volumes may enter later baselines; a data-gap reset independently requires rebuilding volume continuity. Other tickers may use released cash under event rules; same-ticker fresh eligibility is still restricted. No overnight setup carry.

Source: #20, #27–#29, #31, #39, #47.

## 19. Point-in-time corporate-action normalization

Define current historical share basis at each evaluation using only actions already effective and knowable then. Preserve raw/provider/strategy-adjusted provenance. Do not use modern fully adjusted series incorporating future actions, mix incompatible series or double-adjust an already-correct value.

For simple `factor = new_shares / old_shares`, older compatible price becomes `price / factor`, older share volume becomes `volume * factor`. Compose already-effective factors between observation and evaluation. A 2-for-1 halves price/doubles share volume; a 1-for-10 reverse split multiplies price by ten/divides share volume by ten. Before effectiveness, preserve old units. Dollar turnover is not share-volume multiplication.

- Prior close and current price use compatible current-session units for daily change.
- The $5 gate uses actual price denomination at the historical timestamp, never a later split-adjusted replacement.
- ADV10 share volumes use the current evaluation basis.
- ADR20's internally consistent same-session percentage is scale-invariant; do not mix mismatched adjustments.
- Weekly comparisons and overhead levels use compatible entry-date basis; no fake split swing highs.
- Market-cap shares and price use the same historically applicable basis; pure split arithmetic does not economically change cap. Do not derive historical shares solely from today's shares through splits.

Use authoritative effective trading date/time, not announcement date as effectiveness. Verify whether provider series are raw, split/dividend/fully/point-in-time adjusted. Unknown ratio/date/treatment makes affected calculations unavailable with specific eligibility reasons and underlying `CORPORATE_ACTION_DATA_UNAVAILABLE` where applicable.

Complex mergers, spin-offs, unusual distributions/reorganizations and identity changes require verified economic treatment, not guessed simple split arithmetic. This input-normalization specification **does not authorize active-position share/order transformations across a corporate action**. Such unusual intraday execution must be flagged unresolved rather than inventing quantities, stops, targets or fills; a future explicit execution model would be needed.

Source: #37–#38, #40, #42–#43.

## 20. Deterministic state machine

The conceptual normal lifecycle is:

```text
INELIGIBLE → ELIGIBLE → A_CANDIDATE → A_B_IMPULSE → PROVISIONAL_B
→ B_CONFIRMED → C_DEVELOPING → C_LOCKED → BREAKOUT_ATTEMPT
→ D_CONFIRMED → PENDING_ENTRY → OPEN_POSITION → PARTIAL_2R
→ RUNNER → CLOSED
```

These labels describe logical phases; initialization may recognize already-known B/C information without pretending intermediate transitions happened live before activation. PARTIAL_2R may lead directly to CLOSED on a conservative same-bar runner exit. Timer, pending-entry and account states are separate from price levels; do not implement independent ticker accounts.

| From | Transition and guard | Alternative/terminal path |
|---|---|---|
| INELIGIBLE | Required static/day and dynamic gates become valid | No setup created merely by eligibility |
| ELIGIBLE | Completed trigger and eligible A/volume meet activation | Missing history/failed activation stays without a candidate |
| A_CANDIDATE / A_B_IMPULSE | Frozen A; qualifying >=8% by traded slot 10 | Deadline or permanent failure → INVALIDATED/EXPIRED |
| PROVISIONAL_B | Allowed higher/equal high moves origin and resets confirmation/C context | Post-deadline higher high → INVALIDATED |
| PROVISIONAL_B | Two qualifying traded confirmation closes with required pullback | Second-close pullback failure → EXPIRED |
| B_CONFIRMED / C_DEVELOPING | Replay eligible potential C; update valid provisional low/reset progress | Invalid retracement/early breakout/gap → INVALIDATED; slot-10 failure → EXPIRED |
| C_DEVELOPING | Three qualifying post-C traded candles and all conditions, within deadline | Lock only when completed data establishes qualification |
| C_LOCKED | Trade above B before elapsed wait deadline | Low<C → INVALIDATED; wait limit/session boundary → EXPIRED |
| BREAKOUT_ATTEMPT | Valid close/volume and all guards → D_CONFIRMED | Retry-eligible failure → locked waiting/resolution; attempt/time/extension failure → EXPIRED |
| D_CONFIRMED | Exactly one next-interval opportunity → PENDING_ENTRY | No retrospective/delayed opportunity |
| PENDING_ENTRY | Valid open, contexts, ticks and portfolio permit quantity>=2 | Rejection/cancellation → CONSUMED; no actual trade counted |
| OPEN_POSITION | Stop fill → CLOSED; target partial → PARTIAL_2R/RUNNER | Data pause or required tick failure interrupts shared run |
| RUNNER | Non-decreasing stop; prospective updates; stop/EOD fill → CLOSED | Unresolved EOD liquidity → shared run INCOMPLETE |
| CLOSED / TERMINATED | Fresh interval/history and applicable eligibility permit a new unique setup | Quarantine boundaries and session reset prohibit reuse |

Any active pre-entry state may terminate on dynamic failure, missing/invalid data or applicable session boundary. INVALIDATED, EXPIRED and CONSUMED are permanently terminated **setup** states; OPEN positions instead obey their approved exit/incomplete-run rules. No terminated setup resumes. Daily portfolio lockouts prohibit entry but do not liquidate positions.

### Timer registry

| Counter | Unit / origin | Boundary |
|---|---|---|
| A lookback | Up to 10 eligible fresh traded candles preceding trigger | Current session only |
| A–B impulse | Valid traded candles; A = 1 | Qualifying B by 10 inclusive |
| B confirmation | Next consecutive valid traded candles after applicable B origin | Second close must meet 1% pullback; up to 2 solely-confirmation candles after impulse deadline |
| C development | Valid traded candles; first after final B origin = 1 | C locked by 10 inclusive |
| C consolidation | Actual traded candles after current provisional C | At least 3; resets on lower valid provisional C |
| Locked-C wait | Elapsed eligible minutes; first after lock interval = 1 | First attempt by minute 10 inclusive |
| Breakout resolution | Elapsed eligible minutes; first attempt = 1 | D by minute 5 close inclusive |
| Failed attempts | Actual attempts | At most 2 failures; third must succeed |
| Runner activation | Valid traded candles after partial interval | Begin after third completes |
| Entry/EOD | Actual calendar session time | Entry cutoff exclusive; liquidation final-minute open |

Source: #1–#47; default values in the parameter table.

## 21. Reason codes and run outcomes

Codes below preserve approved exact names. Conditions without an approved exact code must retain an explicit recorded reason; an implementation may choose stable labels without changing behavior. Do not silently promote the suggested naming convention into a strategy rule.

| Class | Approved exact codes / required recorded causes |
|---|---|
| Data classification | `TRADED`, `NO_TRADE`, `MISSING`, `INVALID`, `NO_TRADE_15M`, `INVALID_15M_DATA`, `NONEXISTENT_HISTORY`, `MISSING_OR_INVALID_HISTORY` |
| Pre-entry data termination | `DATA_GAP`, `INVALID_DATA` |
| Volume qualification | `RVOL_BASELINE_ZERO`; insufficient trustworthy lookback must be recorded |
| Universe history | `MARKET_CAP_DATA_UNAVAILABLE`, `INSUFFICIENT_ADV_HISTORY`, `INSUFFICIENT_ADR20_HISTORY`, `ADR20_DATA_UNAVAILABLE` |
| Dynamic termination | `DYNAMIC_PRICE_ABOVE_MAX`, `DYNAMIC_DAILY_CHANGE_BELOW_MIN`, `DYNAMIC_MULTIPLE_ELIGIBILITY_FAILURES` |
| Dynamic entry cancellation | `ENTRY_DYNAMIC_PRICE_ABOVE_MAX`, `ENTRY_DYNAMIC_DAILY_CHANGE_BELOW_MIN`, `ENTRY_DYNAMIC_MULTIPLE_ELIGIBILITY_FAILURES` |
| Scheduled entry | `ENTRY_NO_TRADE`, `ENTRY_DATA_MISSING`, `ENTRY_DATA_INVALID`, `ENTRY_OPEN_ABOVE_MAX`, `ENTRY_OPEN_AT_OR_BELOW_B`, `ENTRY_TIME_RESTRICTION` |
| Entry ticks | `ENTRY_TICK_SIZE_UNAVAILABLE`, `ENTRY_TARGET_TICK_SIZE_UNAVAILABLE` |
| Fifteen-minute filters | `ENTRY_15M_STRUCTURE_BEARISH`, `ENTRY_INSUFFICIENT_15M_CONTEXT`, `ENTRY_15M_CONTEXT_NO_TRADE`, `ENTRY_15M_DATA_UNAVAILABLE` |
| Weekly filters | `ENTRY_INSUFFICIENT_WEEKLY_HISTORY`, `ENTRY_WEEKLY_DATA_UNAVAILABLE`, `ENTRY_INSUFFICIENT_WEEKLY_ROOM`; pass annotation `NO_IDENTIFIED_WEEKLY_RESISTANCE` |
| Corporate-action cause | `CORPORATE_ACTION_DATA_UNAVAILABLE` |
| Ambiguity | `INTRABAR_SEQUENCE_AMBIGUITY`, `C_INVALIDATION_INTRABAR_AMBIGUITY`, `SAME_BAR_2R_BREAKEVEN_AMBIGUITY` |
| Unresolved EOD | `UNRESOLVED_EOD_NO_LIQUIDITY`; run `INCOMPLETE_UNRESOLVED_EOD_LIQUIDITY` |
| Open-position tick failure | Run `INCOMPLETE_TICK_SIZE_UNAVAILABLE_OPEN_POSITION` |

Also record reasons for impulse deadline failure, insufficient B-confirmation pullback, post-deadline new B high, invalid C retracement, locked-C break, early breakout, C-development timeout, first-attempt timeout, resolution timeout, third failed attempt, overextension, session expiration, daily trade/loss/streak lockout, position-count/ticker limits, unreliable portfolio valuation, invalid risk, insufficient cash/exposure/size and quantity below two. Record normal protective/runner/gap/EOD exits separately from setup rejections and failures. Preserve all failed gates when one primary code is chosen deterministically.

Do not label an incomplete run COMPLETE. Preserve earlier trustworthy completed results, while unknown subsequent metrics remain unavailable. Missing-position-data pause/incomplete status and ordinary complete-run labels need stable implementation names; their behavior is already approved. Reported marks never masquerade as executable/realized values.

## 22. Audit records and reproducibility

All records must be attributable to a run/configuration, data version, stable security/ticker, session, setup ID and trade ID where applicable. Preserve both historical structural timestamps and modeled detection/execution times. Minimum record families:

| Record | Required content |
|---|---|
| Configuration/provenance | Frozen spec version, all parameter/model choices, source versions/corrections, coverage/adjustment/tick/calendar methodology, disclosure flags |
| Universe evaluation | Security type/listing identity, price/prior close/change, cap and derivation, ADV session dates/raw-normalized volume, ADR dates/HLC/ranges/continuous value/category and failure reason |
| Interval quality | Timestamp/session type/classification, invalidity cause, terminated setup, reset/resumption eligibility |
| Setup lifecycle | Unique ID, A origin/price, activation time/high/rise/qualifying-volume participant, B origin/high/changes/equal retests, impulse counts and baseline volumes, confirmation closes/logical time, C-development slots/potential data/changes/locking, states and terminal reasons |
| Waiting/attempts | C-lock time, traded/elapsed counters, first-attempt time, attempt count, OHLC/close buffer/extension, each failure, resolution/expiration time/reason, D confirmation |
| Volume tests | Per-interval timestamps/classifications/volumes, sums/averages, zero flags, ratios/thresholds, both D test results and every consolidation-vs-impulse comparison |
| Higher-timeframe context | All constituent counts/classifications/first-last traded minutes and valid 15m OHLCV; exact T1/T2/T3 and comparisons; weekly window/context dates/candidates/confirmation/missing/nonexistent bars/nearest resistance/room |
| Pending entry | Scheduled interval/classification/open, D price/time, dynamic/context/tick checks, accepted/canceled/consumed outcome, intended and actual fill, all rejection reasons |
| Competition/account state | Same-time candidates/RVOL/tie-break, accepts/rejects, portfolio before/after each event, cash/equity/exposure, valuation prices/unavailable held ticker, count/lockout state |
| Executable levels | C/raw stop/tick/executable stop, actual entry, risk/budget/risk quantity/all caps/final quantity, raw-target/tick/executable-target/effective R; normalized breakeven and raw/normalized trailing candidates/active stop changes |
| Executions | Modeled timestamp and containing interval, event/side/requested and filled quantity/full-fill flag, reference/fill prices, commission/fees/slippage assumptions and dollar impact, cash/exposure before/after |
| Partial/runner | Original quantity, target, sold and runner quantities, partial fill/realized P&L, new stop, activation counts, every stop adjustment, final reason/price/P&L and combined trade totals |
| Daily/trade outcomes | BOD equity, accepted trade count, gross and net realized P&L/costs, loss streak, sticky lockout time/reasons and whether costs accelerated it, resolved EOD equity; final net WIN/LOSS/BREAKEVEN and net return |
| Ambiguity | Start-of-candle state/OHLC/levels, competing events/plausible outcomes, adverse selection/result/reason and flags |
| Corporate actions | Stable ID/evaluation basis, type/effectiveness/factor/cumulative factor, raw-normalized values/provider status, prior-close/ADV/weekly transformations and compatible market-cap shares/price |
| Final exit/reset | Final quantity reaching zero, reason/fill/modeled time, exit interval start/end, quarantine/reset boundary, first eligible fresh traded candle/new setup ID |
| Unresolved/terminal run | Affected IDs/ticker/entry/remaining quantity/stop/2R-runner state/realized partial P&L/last trustworthy trade and mark; required missing calculation, liquidation schedule/session close/delay, exact pause/stop boundary/status/reason |

Completed trades combine every exit leg and all allocated costs. Keep gross/net and slippage attribution separately without double-counting. No future equity progression, return, drawdown or trade statistics may be fabricated after a terminal unknown portfolio state. Preserve sufficient state for approved replay or clean rerun, as appropriate to failure class.

## 23. Required data and deferred scope

Required for a trustworthy complete dataset: point-in-time historical security master/instrument types/listing-delistings/stable identities; inactive/delisted coverage; market cap or applicable shares outstanding; official prior close; regular-session daily volume/HLC for actual ADV/ADR windows; valid completed weekly history/context; authoritative effective corporate actions and adjustment provenance; one-minute regular-session OHLCV; same-day premarket chronological volume history; affirmative no-trade versus missing/invalid classification; historical ticks; actual exchange calendar. Insufficient inputs must produce approved unavailable/rejected/incomplete outcomes, not guesses. Data availability is unverified.

Quote history is optional for initial spread research/reporting. Deferred realism: realistic commissions/fees/slippage beyond the approved disclosed baseline, bid/ask execution modeling, depth/Level 2, queue, available-size/partial fills, market impact, broker/venue routing, latency, liquidity-sensitive fill probabilities and exact sub-minute sequencing. Deferred research: different parameter values, multiple overlapping patterns, delayed entries, alternate EOD liquidation/overnight models, unusual active-position corporate-action treatment, and a separately approved optimization/out-of-sample methodology. None is silently enabled.

Historical research implementation may begin only when separately authorized. Paper/live connections and deployment require subsequent explicit reviewed authorization and verified executable broker/data behavior; frozen research rules and baseline performance alone establish neither readiness nor profitability.
