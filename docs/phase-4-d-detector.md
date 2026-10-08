# Phase 4 — Breakout attempts and D confirmation

Phase 4 extends checkpoint `6bedb90358cd9ebb4acc544e363a2e08a39289c8`.
The frozen [specification](strategy-spec-v1.0.md), [parameter table](strategy-parameters-v1.0.md)
and [decision history](strategy-decision-history.md) remain authoritative and unchanged.

## Interface and scope

`tradingbot_backtest.d_detector.DDetector` extends `CDetector` with the same
constructor and completed-interval `feed(interval, eligibility_inputs)` contract.
Feed every chronological eligible interval, including verified NO_TRADE and
explicit MISSING/INVALID intervals. Existing identity, chronological continuity,
calendar, share-basis and verified day-input guards remain in force.

`state` retains the A/B lifecycle, `c_state` retains immutable C evidence, and
`d_state` contains immutable timer, attempt, signal and handoff records. A/B-only
and A/B/C-only detectors remain available with their previous behavior.

This phase creates signals, not orders or simulated trades. It does not implement
fills, sizing, stops, targets, runner management, portfolio accounting, daily
lockouts, higher-timeframe filters, a universe scanner or external connections.

## Chronological timers and attempts

After C locks, the first wholly subsequent regular-session minute is waiting
minute 1. Both TRADED and verified NO_TRADE intervals advance the ten-minute
wait. A first attempt on minute 10 is allowed; absent an attempt, expiration
occurs at that minute's end. Touching B does not count or reset the timer.

A traded candle with HIGH strictly above B is an attempt. Its first interval
starts the five-minute resolution window and counts as resolution minute 1.
Thereafter that timer replaces the waiting limit; it never resets after a failed
attempt. NO_TRADE consumes resolution time without becoming an attempt. D may
confirm at minute 5's close; otherwise the unresolved setup expires.

The first two attempts may fail. The third must qualify or expire; a terminated
setup cannot receive a fourth attempt. Failed attempts preserve locked B/C and
never restart C development. Retry-eligible failure includes wick-only closes,
insufficient close buffer and failed volume qualification. Close strictly more
than 2% above B expires immediately regardless of volume.

## D qualification

All comparisons use exact fractions with Decimal source prices/volumes:

- Completed close >= B × 1.0025 and <= B × 1.02.
- Volume >= 1.5 × a positive, trustworthy previous-20 average.
- Volume strictly greater than the local previous-three average.
- All structural, data, completed-close dynamic eligibility and session guards pass.

Previous-20 evidence excludes the current interval, uses same-day chronological
eligible history (including permitted premarket context), and includes verified
NO_TRADE zeros. Missing/invalid data break continuity. A zero average produces
undefined RVOL and qualification failure, never infinity.

The local baseline uses exactly three immediately preceding chronological RTH
intervals, including verified zeros. Premarket is prohibited. A failed attempt
can naturally enter a later attempt's baseline. Local zero average may pass
against positive attempt volume, but cannot compensate for a failed RVOL test.

Locked-C touches are allowed. Any traded LOW below C invalidates. If the same
candle also trades above B, conservative invalidation takes precedence and no
attempt/D is awarded. No favorable intrabar HIGH/LOW sequence is inferred.

## Exactly one pending opportunity

Successful qualification emits D_CONFIRMED at the completed D candle's close,
then PENDING_ENTRY_SCHEDULED for the immediately following RTH interval. The
signal retains A, B-origin/price, locked C and its evidence, D and both volume
baselines. D's candle is never an entry interval and no retrospective D signal
is created from C-lock replay.

At the scheduled interval, NO_TRADE cancels with ENTRY_NO_TRADE. MISSING and
INVALID cancel with ENTRY_DATA_MISSING and ENTRY_DATA_INVALID, respectively,
and clear chronological volume history. Cancellation consumes the setup;
subsequent fresh price detection excludes the canceled interval. Trustworthy
volume history, including a canceled no-trade zero, remains available unless
an independent data-gap reset applies.

A trustworthy traded scheduled interval produces an `EntryHandoff`, not an
accepted entry. It retains the actual OPEN, source interval, verified eligibility
inputs and signal evidence. Opening-price acceptance, dynamic/static entry gates,
15-minute/weekly filters, tick availability and all sizing/account constraints
remain deferred to the future execution phase. No gate is assumed to have passed.

The handoff's modeled OPEN time is the interval start; its recording time is
interval completion because this component consumes completed bars. The future
execution owner must use only opening information for opening decisions, never
the later high/low/close. After handoff, feeding further intervals raises an
ownership-contract error: a future execution component must accept responsibility
for the ticker before progression continues. This is an interface boundary, not
an assumed fill, rejection or second entry opportunity.

## Session and audit contract

Actual exchange-session boundaries and the frozen strictly-before-close-minus-
30-minutes entry cutoff apply, including early closes. D cannot schedule an
impermissible next opening. The last permitted scheduled interval uses its OPEN
time, not its eventual completion time. No state carries overnight.

Inherited A/B/C behavior is preserved by default-no-op pending handling and the
existing no-trade audit extracted into a protected hook. The D extension handles
scheduled opportunities before close-based guards and advances timers through
the no-trade hook. It does not alter previous traded-candle clocks.

Retain the complete returned audit stream. It includes timer starts/counts and
expiration timestamps; attempts and their numbers/failures/remaining slots;
exact close extension, previous-20 baseline/RVOL and local constituent timestamps,
classifications/volumes; D confirmation; scheduling; cancellation and handoff.
Inherited source-data and RVOL events provide the full previous-20 observations.
Descriptive failure labels are audit vocabulary, not new strategy gates or reason enums.

## Validation and prerequisites

Run the offline suite and validation from the repository root:

```sh
python -m unittest discover -s tests -v
python -m compileall -q tradingbot_backtest tests
git diff --check
```

No dependencies or provider connections are added. Callers still must supply
verified session calendars, corrected eligible OHLCV, affirmative no-trade
classification, compatible point-in-time corporate-action units, official prior
close and verified static eligibility. Provider selection remains pending.

These components support the frozen RESEARCH BACKTEST MODEL — V1.0. They do not
prove historical data availability or realistic live execution. Future execution
must retain ZERO-FRICTION BASELINE and FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH
AND PARTIAL FILLS NOT MODELED disclosures when those models are used.
