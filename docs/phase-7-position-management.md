# Phase 7 — Position management and exit engine

This phase adds single-position historical exit management after Phase 6 checkpoint
`7e5929419e3401e2527fd8693c6a2dd86c69823d`. The [frozen specification](strategy-spec-v1.0.md),
[64 defaults](strategy-parameters-v1.0.md) and [decision history](strategy-decision-history.md)
remain authoritative and unchanged. This guide documents implementation and data-resolution
conventions; it does not add strategy rules.

**RESEARCH BACKTEST MODEL — V1.0**

**ZERO-FRICTION BASELINE**

**FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH AND PARTIAL FILLS NOT MODELED**

## Input and modules

`PositionManager` accepts a Phase 6 `InitialPosition`, the verified session calendar
matching its entry approval, and a supplied `TickSource`. It retains the immutable
entry record, including identity, quantity, entry/stop/target/risk, BOD equity,
account snapshot, configuration, Phase 5 context and A/B/C/D references. It does not
recompute entry decisions or recheck dynamic universe eligibility while holding.

| Module | Responsibility |
|---|---|
| `position_management.py` | Immutable position snapshots, exit legs and completed results; stop/target/runner/EOD processing; pause/incomplete outputs and final-exit quarantine |
| `protective_ticks.py` | Exact downward normalization of new protective levels using verified supplied bands, with metadata availability, cross-band evidence and failure causes |
| `trade_construction.py` | Existing entry behavior unchanged; tick-purpose enumeration additionally identifies `BREAKEVEN_STOP` and `TRAILING_STOP` queries |
| `tests/test_position_management.py` | Offline fixtures using actual Phase 4/5/6 handoffs and hand-checkable exit expectations |

No dependency, concrete calendar/tick provider, network client or broker integration
is added. Use Python 3.11+ and the existing offline commands:

```sh
python -m unittest discover -s tests -v
python -m compileall -q tradingbot_backtest tests
git diff --check
```

## Interval and information contract

Supply every chronological eligible RTH one-minute interval, beginning with the
entry interval itself. Missing/corrupt minutes must be explicit `MISSING`/`INVALID`
records. Wrong identity, gaps, duplicates and incompatible sessions raise input
errors rather than being skipped. Entry-interval trustworthy OPEN must agree with
the existing Phase 6 fill.

`on_open(bar)` uses only interval identity/classification and OPEN. It never reads
the minute's subsequent HIGH, LOW, CLOSE or volume to decide an opening fill.
`on_close(bar)` consumes that same completed source record, evaluates extrema,
and establishes prospective trailing levels. Corrections require replay rather
than replacing the completion record silently. `feed(bar)` combines both calls
for single-position offline replay; a future portfolio owner must use the two
phases separately across securities.

Opening fills have modeled execution and availability at interval start.
Intrabar fills have an explicit containing interval, `modeled_event_at=None`, and
information availability at interval end. This records uncertainty without
inventing an exact sub-minute execution time. Intrabar proceeds must not fund an
earlier same-minute OPEN in the future portfolio engine. The canonical required-exit
before-new-entry ordering applies to events sharing an available modeled time;
Phase 7 does not implement that portfolio scheduler.

## Initial stop, 2R and ambiguity

The Phase 6 executable stop and target remain fixed references. Stop touch is
LOW <= stop. OPEN <= stop fills all remaining shares at OPEN; otherwise a stop
touch fills at stop. Target touch is HIGH >= target. Before the one partial has
occurred, OPEN >= target sells at OPEN; otherwise target touch sells at target.
The designated partial is `floor(original_quantity / 2)`, leaving the remainder.
There is no second partial and no minute-volume quantity cap.

Known OPEN events occur before later intrabar extrema. If neither event is resolved
at OPEN and the original stop and unfilled target are both reached, the original
stop wins and no partial occurs. If original stop is not reached but target and
the new executable breakeven stop are both reached with unknown order, the
canonical conservative result is partial followed by runner exit at breakeven,
with `SAME_BAR_2R_BREAKEVEN_AMBIGUITY`. This is the approved exception to prospective
level application, not an inferred LOW/HIGH path. A target gap at OPEN can likewise
activate a runner stop before subsequent intrabar LOW.

## Breakeven and trailing

After partial, normalize entry DOWN to the applicable protective tick grid, never
above actual entry. Active stop is the maximum of the last valid stop and this
executable breakeven floor. Actual entry and executable floor are recorded
separately: touching a raw entry price above the normalized floor is not touching
an executable stop. Stop origin is retained if a coarse new grid produces a level
below the previous stop; a runner phase does not itself imply a trailing-stop exit.

The partial interval is excluded from trailing history, including a partial at
OPEN. Three additional completed VALID TRADED candles activate trailing under
frozen defaults. The independent lookback default is three valid traded lows.
`NO_TRADE` neither counts nor resets either history. At each later traded completion,
take the minimum low in the trailing window; normalize a needed higher candidate
DOWN; select `max(previous_stop, breakeven_floor, normalized_candidate)`.
Never move the active stop down. A candidate already <= active stop requires no
replacement/tick calculation. A new stop applies only to subsequent intervals,
not to the candle whose completed low produced it. Runner stop touch and gaps
follow the same executable stop rules.

## Protective tick contract

Every newly required protective level uses verified security/share-basis/order-purpose
metadata, valid for the relevant price band and historical time. There is no
$0.01 fallback. Cross-band DOWN normalization consults adjacent supplied bands
and finds the nearest valid directional price without extrapolating an increment,
skipping a band gap or choosing among conflicting rules. Audit records retain raw
and executable price, grid origin/increment, source/band transitions, validity,
availability and exact failure cause. Unavailable substantive metadata is not
exposed as contemporaneously known evidence.

For a known opening partial, metadata must be available and applicable at OPEN.
For a prospective trailing replacement, it may become available at that completed
candle's boundary. For an intrabar partial with unknown activation instant,
the supplied rule must already be known at interval start and cover the entire
possible activation interval. If applicability cannot be established from those
inputs, fail as unavailable rather than inventing sub-minute timing or a schedule.
This is a data-sufficiency requirement of this interval model.

A required replacement failure returns
`INCOMPLETE_TICK_SIZE_UNAVAILABLE_OPEN_POSITION`, with shared-stop instruction,
last valid stop, remaining position, partial realized P&L and source cause preserved.
No exit is fabricated. Missing future metadata alone does not invalidate an already
valid stop when no new calculation requires it. A corrected tick dataset requires
a deterministic rerun rather than continuation from a later price.

## NO_TRADE, missing/invalid and replay

Verified `NO_TRADE` supplies zero volume and no OHLC. It cannot produce a fill,
trigger, trailing count or synthetic low. Last trustworthy price is retained only
as an explicitly carried reporting mark. Its remaining-share unrealized P&L is
reporting information, not realized P&L or confirmed portfolio equity.

`MISSING`/`INVALID` returns `PAUSED_DATA` with a required shared-portfolio pause;
retain the last trustworthy position/stop and exact `data_quality_reason`, using
`DATA_GAP`/`INVALID_DATA` respectively. Do not feed later intervals while paused.
`resume_with_replacement` accepts only a trustworthy replacement for the exact
failed interval and replays from its pre-interval checkpoint. The audit retains
the original cause, replacement source and shared-checkpoint replay requirement.
The future portfolio owner must rewind shared state too; this class does not
silently repair portfolio history. If reliable replacement cannot be obtained,
`mark_data_incomplete` returns a shared-stop instruction without a fabricated
exit or completed trade. `PAUSED_DATA`/`INCOMPLETE_DATA` are implementation status
labels for the frozen data-pause/incomplete contract.

## EOD and the one-minute resolution limit

Activate an irrevocable EOD liquidation instruction at
`session_close - 1 minute`: normal 15:59 ET, or the corresponding early-close
final interval. A trustworthy traded OPEN liquidates all remaining shares.
EOD instruction processing does not use that minute's later extrema. Missing or
invalid final-interval data pauses for repair; it is not evidence of no liquidity.

The frozen specification retains the general instruction to fill at the first
subsequent trustworthy eligible trade before close after an EOD no-trade interval.
**Under the current one-minute v1.0 data model, that fallback is unreachable when
the final liquidation interval itself is verified NO_TRADE:** it covers all
remaining time through official close. This is an implementation/data-resolution
limitation, not a strategy-rule change. A future higher-resolution execution model
could make that branch representable without changing the strategy rule.

Accordingly, final-minute verified `NO_TRADE` creates no fill and leaves the
instruction active. At official close return `UNRESOLVED_EOD_NO_LIQUIDITY` and
`INCOMPLETE_UNRESOLVED_EOD_LIQUIDITY` with a required shared-run stop. Preserve
remaining quantity, last trustworthy mark, reporting unrealized P&L, active stop,
partial realized P&L, instruction, NO_TRADE audit and official close. Do not use
16:00/next-day prices, a closing-auction proxy or a fabricated sub-minute trade.
Do not confirm final ending equity. Normal and early-close fixtures test this path.

## P&L, completion and reset handoff

Each full designated exit leg records quantity, reference/fill price, exact
proceeds and `(fill - entry) * sold_quantity` gross P&L. Commission, fees and
slippage remain separate zero fields; net equals gross. Partial sold shares are
realized; runner shares stay open. A final exit aggregates all legs and emits an
immutable `CompletedPosition`: positive NET is WIN, negative is LOSS, exact zero
is BREAKEVEN. Net return is an exact Fraction of original entry position value.
No daily loss/streak counter is updated here.

Completion quarantines the entire final-exit interval, even for a final OPEN exit.
`earliest_fresh_interval_start` is its end; `permits_fresh_price_candle` admits only
the same security's TRADED candles in wholly subsequent intervals. Intervening
NO_TRADE cannot participate. This is only the quarantine gate: a future detector
must separately check session/universe eligibility and rebuild fresh price history.
Partial exits do not release the ticker. Chronological volume continuity remains
governed by the existing volume rules. No new setup is detected in this phase.

## Audit and Phase 8 responsibilities

Immutable snapshots and audit records preserve identity/run/data version, interval
and availability times, pre-interval stop/target/quantity, fill reasons and gap or
ambiguity evidence, partial quantities/P&L, breakeven normalization, trailing
counts/window lows/raw and normalized levels/prospective boundaries, carried
marks, data/tick failures, EOD activation/close, final classification and quarantine.
Ordinary exit names and management status labels are implementation vocabulary;
canonical ambiguity and fatal-run codes are unchanged.

Phase 8 can consume each `ManagementResult`'s new exit legs and proceeds, immutable
remaining state, final result and shared pause/stop flags. It must apply cash,
exposure, portfolio marks, per-leg daily realized accounting, completed-trade loss
streaks and deterministic cross-security event ordering. Phase 7 supplies these
inputs without calculating account equity, allocating cash or incrementing daily
counters. Unknown intrabar timestamps remain explicitly unknown.

Historical calendar sessions, affirmative no-trade verification, complete trustworthy
minute data, compatible security/share units and point-in-time protective tick
schedules still require provider verification. Active-position unusual corporate
actions and execution realism remain separately deferred; no such handling is
invented here. Passing offline fixtures is not evidence of available historical
liquidity, profitability or live executability.

Not implemented: portfolio lockouts, portfolio-wide event loop, simultaneous-entry
ranking, multi-ticker allocation, scanner, bulk backtest runner, broker/TradingView
connections, live data or live trading.
