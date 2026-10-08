# Phase 5 — Opening validation and mandatory context

Phase 5 follows checkpoint `26d55fe19f935c3d623b95fe9e8b95bcd76b9a88`.
The frozen [specification](strategy-spec-v1.0.md), [parameters](strategy-parameters-v1.0.md)
and [decision history](strategy-decision-history.md) remain unchanged and authoritative.

## Interface and ownership

`EntryValidator(calendar, run_id=..., data_version=...).evaluate(handoff, context)`
consumes a Phase 4 `EntryHandoff` and supplied `EntryContext`. The returned immutable
`EntryValidationResult` contains ENTRY_APPROVED with an `EntryApproval`, or
ENTRY_CONSUMED with an exact approved reason where one exists and an explicit
recorded cause. It also contains the complete Phase 5 audit stream.

The validator permits one evaluation per security/setup ID in its run. Repeated
validation raises an ownership-contract error; it does not create a retry or
replace the original result. Each independent research run needs its own validator.
Rejections consume the pending opportunity; the owner must apply the frozen fresh
price-history reset before later setup detection. The Phase 4 detector remains a
handoff snapshot: this phase does not mutate it or introduce a ticker/portfolio
transition controller. Approval transfers the candidate to future Phase 6 checks.

ENTRY_APPROVED means **the Phase 5 checks passed**, not that all entry/execution
requirements passed. Tick-level establishment, risk, sizing, cash, exposure,
competition and daily lockouts remain unevaluated. No shares, fills, cash effects,
trade counts or portfolio objects are created.

## Opening information boundary

The scheduled interval must be the immediately next interval after D, belong to
the same trading date, match security/ticker identity and provide a trustworthy
traded OPEN. A/B/C/D evidence must be earlier completed structure.

The validator reads the scheduled interval's OPEN, classification, source and time
labels only. It does not read its HIGH, LOW, CLOSE or completed volume. The approval
record retains the reference open and prior A/B/C/D evidence, not the scheduled
minute's full OHLCV or original handoff. Tests vary all four later fields and assert
identical approval and audit output; supplying that minute as context cannot use it.

Under Decision #44, the modeled opening information/decision timestamp is the
interval start. Phase 5 audit recording timestamps use this modeled availability,
not the offline ingestion time at which Phase 4 received a completed source bar.
No simulated execution is produced or backdated. Future execution must separately
preserve ingestion provenance and the approved OPEN event convention.

## Opening gates

Entry OPEN must be strictly greater than B and no more than 2% above B. Equality
with B fails; exactly 2% extension passes. Failures use ENTRY_OPEN_AT_OR_BELOW_B
and ENTRY_OPEN_ABOVE_MAX and consume the opportunity.

Recheck dynamic eligibility from OPEN: price <= $5 and daily gain >=3%, calculated
against the compatible official prior close. Both equalities pass. Record both
gates even when choosing ENTRY_DYNAMIC_MULTIPLE_ELIGIBILITY_FAILURES. Static/day
gates are received as verified point-in-time eligibility; this phase calculates
no market cap, ADV10 or ADR20 and assumes no scanner/provider implementation.

Require an actual regular-session minute strictly before exchange close minus
30 minutes. A normal 15:30 open is prohibited; an early-close 12:30 open is also
prohibited. Verified non-session dates reject. No next-day/delayed opportunity
is accepted. Calendar boundaries remain supplied through the existing interface.

## Supplied provenance and share units

`OpeningEligibility` supplies a verified source, information-availability time,
compatible official prior close, static qualification, share-basis identifier,
normalization-verification flag and effective timestamps of adjustments used.
This verification attests to the common point-in-time normalization pipeline for
opening/structure, context and reference data. Minute and weekly observations
also carry their own basis/source/availability identifiers.

Incompatible or unverified units, or normalization through a future action,
consume with CORPORATE_ACTION_DATA_UNAVAILABLE or the affected context's data
unavailable reason and underlying cause. The validator never computes a split
factor or trusts a provider's field name "adjusted". Missing prior close, reference
conflicts, unavailable opening eligibility and failed static prerequisites retain
explicit descriptions because the frozen spec assigns no separate exact code to
those conditions. Corrected reference inputs require deterministic replay, not
selective adjustment of a winning/losing result.

## Fifteen-minute context

`intraday_context` constructs fixed blocks anchored at the supplied RTH session
open. Select the three immediately preceding chronological completed blocks;
forming, premarket and previous-day data cannot substitute. At 10:15, the initial
three normal-session blocks are completed. Before then, reject with
ENTRY_INSUFFICIENT_15M_CONTEXT.

Every expected minute needs exactly one matching security/basis observation that
was completed and available by OPEN. Missing slots, unresolved duplicates, explicit
MISSING/INVALID intervals or unavailable corrections make required context invalid.
NO_TRADE never receives synthetic OHLC.

A block with actual trades and otherwise trustworthy traded/no-trade minutes uses
the first traded open, maximum traded high, minimum traded low, last traded close
and sum of constituent volumes. An entirely no-trade block has zero volume and
no OHLC. Required invalid/no-trade blocks reject with ENTRY_15M_DATA_UNAVAILABLE
or ENTRY_15M_CONTEXT_NO_TRADE; no bad block is skipped.

Require T3.high >= T1.high AND T3.low >= T1.low. Either failure consumes with
ENTRY_15M_STRUCTURE_BEARISH. No moving-average/indicator substitute is used.

## Weekly resistance

`WeeklyHistory` supplies verified point-in-time listing identity and completed
regular-session weekly observations. The calendar enumerates actual applicable
trading weeks. A zero-session calendar week is skipped legitimately; a holiday-
shortened week uses its actual final session close. Pre-listing history is genuinely
nonexistent, not missing. Security identity/continuity verification is external.

Use up to 52 previous completed candidate weeks, excluding the current week;
require at least 12. Up to two older weeks are left context only. The loader must
supply all actual expected weeks; missing/invalid/conflicting required observations
cannot be skipped, bridged or replaced by older weeks. Reject unavailable context
with ENTRY_WEEKLY_DATA_UNAVAILABLE, or insufficient actual listing history with
ENTRY_INSUFFICIENT_WEEKLY_HISTORY.

A candidate's high must be strictly greater than both immediately prior highs,
and >= both immediately following highs. Right bars must already be completed
and available; the latest candidates may remain unconfirmed. Early newly listed
candidates lacking genuinely nonexistent left history remain unevaluable without
rejecting the whole sufficient window. Current/future weeks cannot confirm a swing.

Find the nearest confirmed high strictly above OPEN, using compatible normalized
units. Require room >=5%, with exact equality passing. Insufficient room consumes
with ENTRY_INSUFFICIENT_WEEKLY_ROOM. No confirmed overhead swing passes with
NO_IDENTIFIED_WEEKLY_RESISTANCE only after trustworthy history was evaluated.

## Approval, audits and Phase 6

`EntryApproval` preserves setup/security/ticker, reference OPEN and modeled time,
A/B/C/D evidence, prior close and exact dynamic/B-gap ratios, required three blocks,
weekly window/evaluations/resistance/room/status, calendar and normalization/source
provenance. Any supplied already-known `TickReference` is retained opaquely; missing
or future tick metadata never creates a guessed increment. Phase 6 must verify
actual applicable stop/target tick metadata before accepting any entry.

Retain returned audits for opening inputs/boundaries/gate results; constituent
minute classifications/source causes/availability/OHLCV; block construction and
comparisons; weekly bars/candidates/confirmation context/selected resistance;
rejection/consumption or approval. Later scheduled-minute OHLCV never appears as
decision evidence. Values from unavailable future corrections are not used.

No dependencies, APIs or provider connections are added. Required external inputs
remain the verified exchange calendar, official prior close/static qualification,
corrected eligible intraday records with affirmative no-trade classification,
security listing history, trustworthy regular-session weekly highs and verified
point-in-time corporate-action normalization. A suitable dataset is not established.

Run offline validation:

```sh
python -m unittest discover -s tests -v
python -m compileall -q tradingbot_backtest tests
git diff --check
```

Phase 6 must implement separately authorized trade/tick/risk/account checks and
execution. Preserve RESEARCH BACKTEST MODEL — V1.0, ZERO-FRICTION BASELINE and
FULL-FILL RESEARCH ASSUMPTION — MARKET DEPTH AND PARTIAL FILLS NOT MODELED
disclosures when applicable; Phase 5 makes no claim of live executability.
