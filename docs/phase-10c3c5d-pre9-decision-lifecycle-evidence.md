# C3C5D-PRE9 — historical decision-boundary lifecycle and conservative remainder evidence

PRE9 adds historical evidence only, never current manageability. The first real
PAPER order remains blocked. Gateway Read-Only remains required, restart quarantine
mandatory, AccountMode UNKNOWN, public snapshot mode None and LIVE unsupported.
No current lifecycle/remainder, position/inventory, finality, protection,
cancel/replace/exit, recovery, barrier clearing, risk/strategy restoration or trading
authority is added. No accounting, commission, cash or P&L calculation occurs.

Starting checkpoint: `be7ce57d55b4e240d1752e7448ebbd6157accf64`. The user reported
PRE8 independently reviewed and actually Windows-qualified on Python 3.12, with
focused/full offline tests, compilation and diff checks passed, clean tree/index
and synchronized main/origin/main. PRE9 requires its own independent review and
actual Windows qualification. Cloud validation is not Windows qualification.

## Exact original PRE8 issuer and sources

`paper_decision_lifecycle_evidence._create_decision_lifecycle_evidence_owner(attributed_execution_owner)`
accepts only the exact registered `_AttributedExecutionEvidenceOwner` with an
already published PRE8 result. It captures that original publication, original
bound `_require_evidence` method, exact association/creator thread, canonical values
and immutable reference graph. One new private owner registers lazily in the
original read transport's `_decision_lifecycle_evidence_owner` slot. No existing
transport or producer module changes.

`owner._evaluate(pre8_result)` accepts only the captured exact root. It cannot accept
a caller status, attempt, quantity, identity, replay, boundary or execution tuple
as alternate authority. The original PRE8 `_require_evidence` runs before and after
analysis/publication. Its inherited durable revalidation remains intact, including
the original PRE5/PRE4 fixed-selector integrity checks; PRE9 neither independently
reevaluates PRE4 nor loads/selects another source or boundary.

The only semantic sources are:

- Local attempt: exact `pre8.pre7.attempt`.
- Requested Q: exact `pre8.pre7.attempt.consumed.order.quantity`.
- Execution E: exact PRE8 `historical_execution_quantity_lower_bound`.
- Status: exact `pre8.pre7.pre6.pre5.status`, including its original selected
  observation and exact V3 decision boundary.

PRE8 ATTRIBUTION_BLOCKED or EXECUTION_EVIDENCE_CONFLICT produces BLOCKED consistency
and classification, with no new quantities, selected observation or lifecycle
report. Status cannot repair an upstream execution conflict. Defensive PRE4
AMBIGUOUS_IDENTITY also blocks; valid original issuers ordinarily reject/block it
earlier. No root is weakened to make that defensive branch reachable.

NO_POSITIVE_EXECUTION_OBSERVED and EXECUTION_EVIDENCE_INCORPORATED permit analysis.
PRE8 zero remains non-observation, not zero fills. PRE1 open orders and PRE3 completed
orders remain retained through the original source graph but are never inspected
for currentness, lifecycle corroboration or finality here.

## Independent historical quantity bound

PRE9 revalidates exact positive Q and finite nonnegative E with E <= Q. It derives
`historical_possible_remaining_upper_bound = subtract(Q, E)` using the reviewed
`tradingbot_backtest.numerics` exact finite-Decimal helper, independent of ambient
precision/traps. The bound is nonnegative and its canonical Decimal tuple is
deterministic and revalidated. Source Q/E and status Decimal representations are
retained unchanged.

The bound means only that, under the retained original quantity, at most Q-E could
have remained unexecuted after the positively evidenced executions. E=0/Q=10 gives
upper bound 10 without proving ten remain. E=4 gives six without proving six work;
E=10 gives zero without current inventory, lifecycle or finality authority.

It is not broker-reported remaining, current remainder, cancelable quantity or
protectable quantity. No status quantity is used to increase E, cap this bound or
manufacture another reported field. No execution completeness or absence claim is
created, even at zero or E=Q.

## Qualified selected status fields

The exact durable `_QualifiedStatusSourceQuality` rules remain authoritative.
SUPPLIED and LEGACY_DECODED permit a retained field value. LEGACY_AMBIGUOUS and
UNAVAILABLE supply no semantic value. Absence never becomes zero; unavailable
protobuf sentinels keep their original wire-supplied metadata. The complete original
observation, including protocol, presence and Decimal sign/digits/exponent, survives.

The current source contract makes legacy status and valid legacy filled/remaining
values LEGACY_DECODED, including quantity zero. LEGACY_AMBIGUOUS zero applies to
IDs/prices, not quantities/status. PRE9 preserves that stronger rule; a fabricated
ambiguous quantity is rejected by original source qualification rather than
accepted as a new source form. Valid legacy unavailable quantities remain None.

Only the existing `journal._LifecycleStatus` vocabulary is used. The mapping is:

| Qualified reported status | Historical classification |
| --- | --- |
| PendingSubmit | HISTORICAL_SUBMISSION_REPORTED |
| PreSubmitted, Submitted | HISTORICAL_WORKING_REPORTED |
| Filled | HISTORICAL_FILLED_REPORTED |
| Cancelled, ApiCancelled, Inactive, Expired | HISTORICAL_TERMINAL_REPORTED |
| UNKNOWN or unavailable status field | HISTORICAL_LIFECYCLE_UNRESOLVED |

PRE4 NO_QUALIFIED_STATUS_THROUGH_BOUNDARY instead gives
NO_QUALIFIED_DECISION_STATUS with unresolved consistency, retaining Q/E/upper bound
but no selected observation or reported lifecycle. Execution evidence alone never
creates a Filled/partial/working/terminal status, even when E=Q.

`status_reported_filled` and `status_reported_remaining` retain independent exact
source references only when available. One is never calculated from Q minus the
other. A selected Filled report with either/both quantity fields unavailable stays
HISTORICAL_FILLED_REPORTED with UNRESOLVED consistency unless a known value proves
a contradiction. Missing status differs from a known UNKNOWN value.

## Internal consistency, never cross-domain chronology

The following checks use only selected status values and local Q:

- Known F <= Q and known R <= Q.
- When both are known, exact `add(F, R) == Q`.
- For a Filled report, known F must equal Q and known R must equal zero.

The closed conflict reasons, in deterministic enum order, are:

- STATUS_FILLED_EXCEEDS_REQUESTED
- STATUS_REMAINING_EXCEEDS_REQUESTED
- STATUS_QUANTITY_SUM_MISMATCH
- FILLED_STATUS_FILLED_QUANTITY_MISMATCH
- FILLED_STATUS_REMAINING_QUANTITY_MISMATCH

Any proved violation produces HISTORICAL_LIFECYCLE_CONFLICT and CONFLICTING
consistency. Raw qualified F/R remain diagnostic source reports, never conclusions
of exact broker remainder. No value is capped, rewritten or inferred. The independent
PRE8 E and Q-E bound remain retained: a status-local contradiction does not erase
positive execution facts or turn their upper bound into broker working quantity.

Otherwise, missing/UNKNOWN status or missing F/R makes consistency UNRESOLVED;
fully known internally consistent reports are COMPATIBLE. COMPATIBLE means only
no proved internal selected-status/local-Q contradiction, not cross-domain agreement,
completeness, economic simultaneity or currentness.

PRE8 executions and PRE4 status have no qualified common broker event clock. E>F,
R>Q-E, a Submitted report with E=Q, a Filled report with E<Q, or a terminal/pending
report with positive executions is not automatically contradictory. For Q=10,
Submitted F2/R8 and E4 remain separate: historical working reported, F2/R8, E4,
execution-derived upper bound six, chronology unknown. Filled F10/R0 with E4 does
not upgrade E to ten; Submitted F8/R2 with E10 does not erase the R2 report.

No source is newer/preferred and no transition ranking is introduced. Earlier
status reports are not independently reinterpreted. The old C3C5B working-order
owner's distinct historical rules remain unchanged; PRE9 neither imports, invokes,
consumes nor publishes through that owner or the accounting owner.

## Immutable publication, failures and locks

`_DecisionLifecycleEvidence` is private, frozen, slotted, opaque and memory-only.
Its ten fields are `pre8`, `historical_possible_remaining_upper_bound`,
`status_reported_lifecycle`, `status_reported_filled`, `status_reported_remaining`,
`classification`, `consistency`, `reasons`, `obligations`, `limitations`.
`decision_status`, `selected_observation`, `requested_quantity` and
`execution_quantity_lower_bound` are derived properties, not alternate authority.
Blocked results expose None through the latter three properties.

The original issuer validates only its exact latest publication, original PRE8 root,
original bound method, canonical snapshots and exact immutable reference graph.
Reconstructed, pickled, foreign, substituted and superseded proofs fail closed,
including equal replacement source objects and derived fields. Repeated evaluation
of the same valid root is deterministic and supersedes one bounded local result
slot, without enriching caller-held historical values. A newer PRE8 publication
invalidates the captured root rather than retargeting this owner.

Later valid status/execution callbacks and boundaries may be checked for durable
integrity by inherited issuers but cannot change the original selected boundary,
status, E or upper bound. Restart loses the live issuer; no receipt/result is adopted.

Only the exact creator thread constructs/evaluates/validates; wrong-thread calls
fail before inherited locks or storage. PRE9 adds no locks or callback work.
Inherited coordinator -> control -> read lifecycle -> read condition -> write
dispatch -> arrival order is unchanged, with lower locks released before durable
reads. No persistence wait, filesystem/network/SDK operation or new I/O is added
beyond original PRE8 issuer revalidation.

Ordinary failures yield only DECISION_LIFECYCLE_EVIDENCE_UNAVAILABLE. Entered failures
clear local publication metadata and release the busy guard through direct assignments.
KeyboardInterrupt, SystemExit including code, and GeneratorExit preserve the exact
original objects. No usable partial result or new safety poisoning is introduced;
original upstream storage-failure behavior is unchanged.

## Obligations, limits and unchanged boundaries

The six permanent obligations are CROSS_DOMAIN_EVENT_CHRONOLOGY_UNKNOWN,
EXECUTION_COMPLETENESS_UNKNOWN, NO_LATE_FILL_GUARANTEE, CURRENT_BROKER_STATE_REQUIRED,
CURRENT_REMAINDER_UNKNOWN and CURRENT_INVENTORY_UNKNOWN. Closed additional obligations
identify upstream blocking, defensive status ambiguity, no qualified selected
status, unavailable/UNKNOWN status and unavailable F/R. Conflict reasons are separate.

The fixed limitations are:

- HISTORICAL_DECISION_BOUNDARY_EVIDENCE_ONLY
- SELECTED_STATUS_IS_LATEST_RECEIVED_THROUGH_FRONTIER_NOT_CURRENT_STATE
- EXECUTION_QUANTITY_IS_LOWER_BOUND_ONLY
- REMAINING_QUANTITY_IS_UPPER_BOUND_ONLY
- NO_CROSS_DOMAIN_EVENT_CHRONOLOGY
- NO_EXECUTION_COMPLETENESS_PROOF
- ABSENCE_IS_NOT_NO_EXECUTION_PROOF
- DELAYED_OR_IN_FLIGHT_CALLBACKS_POSSIBLE
- NO_NO_LATE_FILL_GUARANTEE
- NO_BROKER_CURRENTNESS
- NO_FINALITY
- NO_CURRENT_WORKING_REMAINDER
- NO_CURRENT_INVENTORY_OR_POSITION
- NO_COMMISSION_OR_CASH_ACCOUNTING
- NO_PROTECTIVE_OWNERSHIP
- NO_CANCEL_REPLACE_EXIT_AUTHORITY
- NO_MANAGEMENT_RECOVERY_OR_TRADING_AUTHORITY
- NOT_RESTART_ADOPTABLE

All PRE1–PRE8 source limitations survive. V1/V2/V3 bytes/semantics, SQLite schema/
version/hash, PRE9 qualified durability, prior issuers, collections/callbacks,
accounting, old lifecycle, recovery, risk/strategy, public Broker/snapshot APIs and
wire policy are unchanged. There is no event, codec, migration, V4 or public API/CLI;
`__all__ = ()`. No safety flag/cursor/authority is changed. No fee/price/notional/
cash/P&L or current inventory calculation occurs.

## Validation and remaining foundations

Tests use original collector/issuer/capture/durable paths with anonymous fake SDKs
and temporary SQLite. Network and DNS are blocked. PRE9 evaluation forbids broker
requests/orders, source collection/consumption/authentication, preparation, boundary
production, writes/drain, old lifecycle/accounting/recovery and current clocks.
SQLite authorizers and before/after snapshots enforce the write/safety boundary.
Matrices cover all statuses, qualified presence/zero shape, incomplete fields,
internal contradictions, independent-domain non-contradictions, frozen later history,
exact references and BaseExceptions.

PRE9 requires independent review and actual Windows qualification on Python 3.12
with the qualified installed SDK/protobuf and real Gateway opt-in disabled.
No real connection is needed or authorized. Currentness/corroboration and safe
management still require separately reviewed fresh broker coverage, continuity,
late-fill/correction handling and inventory/disposition/ownership foundations.
Protection/resizing, cancel/replace/exits, session retirement, recovery/risk/strategy
restoration and first-PAPER authorization remain later and blocked.

Cloud/Linux offline validation on Python 3.12.14:

- PRE9 focused: 143 tests passed.
- Selected regressions: 5,961 tests OK, skipped=3, across 34 modules. These include
  PRE8/7/6/5/preparation/4 (134/102/168/151/207/164), PRE1/2/3 (180/229/284),
  V3 producer/contract (179/195), C3C5C qualified durability (399), V2 coverage (253),
  journal/storage/dispatch, accounting (144), old working order (191), reconciliation
  (242), recovery (181), source/pairing/delivery and read-only safety.
- Full offline discovery: 8,265 tests OK, skipped=4. Real Gateway opt-in was unset;
  installed-SDK checks unavailable in cloud remain a Windows requirement.
- All 138 tracked Python files compiled successfully; staged and unstaged
  `git diff --check` passed.
- Scope and static scans: only the new module/test/document and narrow README/
  readiness updates; all 61 existing broker/backtest production Python files remain
  byte-for-byte unchanged. Secret/account, credential/.env, public-export, broker,
  new-I/O/write, clock, accounting/lifecycle-authority and V1/V2/V3 compatibility
  scans passed.

No dependencies were installed, no real IBKR connection or broker operation occurred,
and Gateway settings were untouched. Independent review and actual Windows
qualification remain required before the next lifecycle phase.
