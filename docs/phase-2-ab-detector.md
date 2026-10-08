# Phase 2 — Completed-minute A/B detector

Implemented against the unchanged frozen [specification](strategy-spec-v1.0.md),
[defaults](strategy-parameters-v1.0.md) and [history](strategy-decision-history.md),
following Phase 1 checkpoint `1a49ed322b0d9c8b9a08ae0411516252e5547a21`.

This is a detector component, not a runnable historical portfolio backtester.
It contains no C/D, consolidation, entry, exit, sizing, stops, account, ranking,
universe calculations, higher-timeframe filters, broker or provider integration.

## Components and inputs

- `tradingbot_backtest/ab_detector.py`: immutable `ABState` snapshots,
  `EligibilityInputs`, and a stateful `ABDetector` for one stable security/ticker.
- `tradingbot_backtest/volume.py`: immutable per-participant evidence with the
  exact prior chronological baseline, classifications and finite RVOL ratio.
- `tests/test_ab_detector.py`: offline, hand-checkable signal, deadline,
  continuity, numerical and session fixtures. Existing Phase 1 tests remain intact.

Construct a detector with the Phase 1 calendar protocol, a run identifier,
dataset version and frozen configuration. Call `feed(interval, inputs)` only
after the interval completes. The method returns the interval's audit records;
the caller retains these. `detector.state` is the immutable current snapshot.
Different detectors must receive distinct stable security identifiers. Setup IDs
combine run/security/date with a deterministic per-detector serial number.

Calendar boundaries are supplied by a verified provider-neutral calendar;
holidays must return `None`, unknown boundaries must raise. No real calendar
provider is selected. Feed every chronological eligible minute, including
affirmative NO_TRADE and explicit MISSING/INVALID records. Omitting an expected
minute, duplicate/reverse timestamps, mismatched identities and out-of-session
inputs are ingestion errors, never synthetic no-trade intervals.

The caller supplies already-established static/day-level eligibility and a
trustworthy official prior close on the same point-in-time share basis as OHLC.
These reference inputs remain fixed for that date in this component. A changed
input or share-basis identifier requires corrected, verified historical inputs
and replay; the detector does not guess corporate-action transformations or
reinterpret reference-data corrections as a new signal. Security master,
market-cap/ADV/ADR calculations and corporate-action normalization remain outside
Phase 2. Real data must affirmatively support classifications and provenance.

## State and replay

The implemented path is ELIGIBLE → A_CANDIDATE → AB_IMPULSE → PROVISIONAL_B →
B_CONFIRMED. A_CANDIDATE and AB_IMPULSE transitions are individually audited even
when one completed interval also establishes a qualifying provisional B.
TERMINATED records preserve the completed setup until subsequent fresh detection.
B_CONFIRMED stops B progression; later bars cannot move its origin or frozen
average. Data, dynamic-eligibility and session guards still apply to this active
pre-entry setup. No transition into C is implemented.

A price selection uses previous fresh RTH traded candles, excluding the trigger;
there is no ten-candle warmup requirement. NO_TRADE intervals contribute volume
history without price participation. Before activation, trustworthy RTH history
can exist while dynamic activation gates fail; those gates control activation
and the subsequent active setup's life. Following a dynamic termination, the
restoration candle is excluded and fresh price history starts after restoration.
Ordinary termination clears price history while preserving valid chronological
volume context; unknown data clears both. New dates clear both histories.

Activation stores every qualifying volume participant in chronological order.
There is no behavioral tie-break among multiple qualifying participants; later
impulse qualification independently examines only A-through-B participants.
Every participant retains its own preceding intervals, average and RVOL.

Initial B uses the highest high/latest permitted tie in already-completed
impulse history. Post-origin known candles receive the same confirmation and
deadline checks as later arrivals. Logical B confirmation may precede activation;
audit record availability remains the activation interval's completion. Replay
records do not constitute retroactive execution or later-stage signals.
An A-origin high can remain the recorded highest high but cannot independently
prove an upward move from its own low; a permitted later retest/higher high
provides subsequent price evidence. No lower replacement B is manufactured.

`traded_candle_count` is the processing/deadline count from A, including later
confirmation candles. The actual impulse-volume length ends at B origin and
does not expand with confirmation. Allowed B moves reset confirmation without
resetting that processing count. Freeze the exact impulse average on confirmation.

## Precision and audit

Nonterminating averages/ratios use `Fraction`; OHLCV stays Decimal. Comparisons
do not depend on ambient Decimal precision. Audit strings for rational values
preserve exact numerator/denominator forms rather than rounded eligibility values.
No rounding tolerance or guessed tick schedule is introduced.

Records include classified source OHLCV, every RVOL baseline slot and volume,
dynamic inputs, A selection/activation, qualifying participants, B origins and
retests, traded counts, confirmation progress, impulse volumes/averages, logical
confirmation and termination. Generic event names and descriptive termination
labels are implementation audit vocabulary, not additional strategy rules.
Approved DATA_GAP/INVALID_DATA, dynamic-failure and RVOL-zero codes retain their
canonical meanings. No portfolio reporting engine is built.

Run the complete offline checks from the repository root with Python 3.11+:

```sh
python -m unittest discover -s tests -v
python -m compileall -q tradingbot_backtest tests
git diff --check
```

No dependencies, network access or service credentials are required. Passing
fixtures validates this component, not provider availability, historical
profitability, full-strategy results or realistic execution.
