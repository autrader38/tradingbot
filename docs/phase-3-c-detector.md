# Phase 3 — C development and locking

Phase 3 follows checkpoint `5649b324f889a5912473276e6c972a2a39e6d8b6`.
The frozen [specification](strategy-spec-v1.0.md), [parameters](strategy-parameters-v1.0.md)
and [decision history](strategy-decision-history.md) remain unchanged and authoritative.

## Interface and scope

`tradingbot_backtest.c_detector.CDetector` extends the existing `ABDetector`.
Construct it with the same security/ticker, calendar, run/dataset identifiers,
configuration and verified eligibility inputs described in the [Phase 2 guide](phase-2-ab-detector.md).
Feed completed eligible one-minute intervals using the same chronology and
classification contract. `state` remains the setup lifecycle/A/B snapshot;
`c_state` contains immutable C evidence. `feed` returns immutable audit records
for the caller to retain. The A/B-only interface remains available and unchanged
in behavior; its small protected processing hook allows the C extension to
manage confirmed-B candles without moving a confirmed B.

The combined lifecycle advances through B_CONFIRMED → C_DEVELOPING → C_LOCKED.
It implements no breakout attempts, locked-C waiting timer, D, entry, fill,
sizing, stop, exit, portfolio, universe calculation, higher-timeframe gate or
external connection. Above-B movement after C lock does not generate a signal;
that handling belongs to a later authorized phase. Lock timestamp metadata is
retained for later timer initialization, without running that timer here.

## Conditional post-B evidence

Potential C starts with the first valid traded candle after the current B-origin.
The origin candle itself supplies no C low, higher low or consolidation evidence.
B-confirmation candles count toward potential C and its development clock.
Before B confirms, C evidence is conditional data, not an official C lifecycle
state or lock. A permitted higher/equal-high B-origin move discards the prior
origin's evidence and restarts tracking after the replacement origin.

A potential C violation is retained and enforced immediately upon successful
B recognition. It is not ignored because it occurred during confirmation.
Failure of B confirmation discards the conditional C data. A legal B-origin
move can discard an earlier origin's conditional violation because that exact
B never became the confirmed structure.

Initial A/B replay can recognize logical B confirmation before activation.
The C extension evaluates all known eligible post-origin candles, including
those after the logical confirmation, before attempting its first official
lock. C does not retrospectively lock on an earlier known candle. A later known
valid lower C resets the conditional consolidation first; a known invalidating
candle prevents locking. Official lock availability is the current completed
interval, recorded separately from B's logical confirmation and C's origin.

## C development and qualification

Use exact fractions for `(B - low) / (B - A)`. A shallow pullback below 20%
does not yet establish qualifying C. Valid C requires retracement >=20% and
strictly <50%, a low strictly above the midpoint and above A. At/below midpoint
or A invalidates the confirmed structure. Potential violations wait for B
recognition as described above.

The lowest valid provisional C is retained. A new valid lower low updates its
origin and resets higher-low/consolidation evidence, without restarting the
ten-valid-traded-candle clock. Equal C lows preserve the origin. The C-origin
candle cannot satisfy its own subsequent higher-low or consolidation requirement.

After that origin, collect actual traded consolidation candles and higher lows
strictly above C. C touches are permitted but do not supply higher-low evidence.
All consolidation lows hold C and highs remain at/below B. Above-B trading
before C lock invalidates rather than becoming a breakout attempt. When the
latest three consolidation candles are available, evaluate the canonical §9
conditions, including a strictly higher low in that evaluated window:

- range `max(high) - min(low) <= 0.5 * (B - A)`;
- arithmetic average volume strictly below the Phase 2 frozen impulse average;
- required subsequent higher-low evidence;
- confirmed B, valid C, trustworthy data, eligible close and an unexpired clock.

Failed aggregate range/volume qualification cannot be improved by skipping a
candle; subsequent windows roll over the actual post-C traded sequence. Volume
equality fails. NO_TRADE has no OHLC and supplies neither C, higher lows nor
consolidation volume observations.

With valid C strictly above midpoint and highs at/below B, range is necessarily
strictly below half the impulse. The approved inclusive range operator is still
implemented, and exact-half/above-half comparisons are tested independently as
numerical boundaries; those input ranges cannot themselves form a fully valid
frozen C setup. No boundary or price requirement has been changed.

## Clock, locking and termination

The first post-final-B traded candle is C-development candle 1. Confirmation
does not restart the clock; neither does a new provisional C. NO_TRADE skips
the traded count, while actual session/cutoff boundaries remain binding.
The tenth traded candle may finish qualification and lock. Otherwise expire at
its completion; an eleventh cannot rescue the expired setup.

Lock freezes C price/origin, availability timestamp and `CLockEvidence`: the
retracement, development candles, qualifying higher lows, range/volume windows,
exact metrics and unchanged frozen impulse average. Later C touches and verified
no-trade intervals preserve that snapshot. A low strictly below locked C
invalidates. If the same bar also trades above B, record the approved intrabar
ambiguity flag and invalidating reason; no D or entry is awarded.

Inherited guards apply first: missing/invalid intervals terminate the active
setup and break history continuity; dynamic close-based failure terminates;
session boundaries expire setups. A wick above $5 alone does not fail the dynamic
gate, though above-B structure failure remains independently enforceable.
Terminal C snapshots remain available for audit; fresh detection/new sessions
clear them before reuse. Price/volume reset policy remains the Phase 2 policy.

## Audits, validation and pending inputs

Audit events preserve starts/restarts, discarded origins, provisional updates,
retracements, clock counts, higher lows/resets, consolidation progress and exact
comparisons, lock evidence, conditional violations, invalidations and expiration.
Source OHLCV and interval classifications remain in inherited data events.
Descriptive C termination labels are audit vocabulary, not new strategy gates
or additions to the approved reason-code enumeration.

Run the complete offline suite from the repository root:

```sh
python -m unittest discover -s tests -v
python -m compileall -q tradingbot_backtest tests
git diff --check
```

No dependency is added. Calendar, corrected eligible OHLCV, affirmative no-trade
classification, compatible point-in-time share basis and verified eligibility
inputs remain caller/provider requirements. No provider is selected or connected.
Passing tests establishes this component's deterministic behavior, not a complete
historical backtest, dataset suitability, profitability or executable live fills.
