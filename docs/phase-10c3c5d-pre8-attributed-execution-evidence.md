# C3C5D-PRE8 — exact attributed execution incorporation evidence

PRE8 establishes positive historical execution evidence only. The first real PAPER
order remains blocked. Gateway Read-Only remains required, restart quarantine
mandatory, AccountMode UNKNOWN, public snapshot mode None and LIVE unsupported.
No current lifecycle/remainder, position/inventory, finality, accounting, commission
completeness, cash/P&L, protection, cancel/replace/exit, recovery, barrier clearing,
risk/strategy restoration or trading authority is added.

Starting checkpoint: `1c85729539c24d776b40568783dcb63e0d082c87`. The user reported
PRE7 independently reviewed and actually Windows-qualified on Python 3.12.10:
102 focused tests, 7,988 full-suite tests with 14 skips, 134 tracked Python files
compiled, clean tree/index and synchronized local/origin main. PRE8 requires its
own independent review and actual Windows qualification before lifecycle/remainder
work. Cloud validation does not establish Windows qualification.

## Exact original issuer and frozen sources

`paper_attributed_execution_evidence._create_attributed_execution_evidence_owner(attribution_owner)`
accepts only the exact registered `_AttemptAttributionEvidenceOwner`, with an already
published result. It calls the original PRE7 `_require_evidence`, captures its
original bound validator, exact publication, association/creator thread, canonical
values and immutable reference graph. One private owner is registered in the
original read transport's `_attributed_execution_evidence_owner` slot. No existing
transport module changes; no caller execution list, attempt, identity or replay
can substitute for the captured original PRE7 publication.

`owner._evaluate(pre7_result)` accepts only that captured exact result. The original
PRE7 validator runs before and after analysis/publication. Only
`ATTRIBUTED_TO_EXACT_LOCAL_ATTEMPT` permits incorporation. Every other PRE7 outcome
produces `ATTRIBUTION_BLOCKED`, no fact tuple and no lower bound. Earlier validators
remain stronger than defensive branches: malformed session/generation evidence may
already fail upstream rather than produce a classified negative.

The only two execution domains are:

1. Exact `pre7.attempt.executions`, from the replay frozen in
   `pre7.pre6.pre5.status.prefix.replay` at the selected decision boundary.
2. Exact `pre7.pre6.executions.candidates`, already qualified to the target numeric
   order/client pair. PRE8 does not reread raw PRE2 receipt observations, recalculate
   correlation, or use orderRef/symbol/permId to add a candidate.

Inherited PRE7/PRE6/PRE5 revalidation may read later durable history to verify
integrity. PRE8 adds no load path and uses no later record semantically. Later
executions, statuses, attempts, reservations, commissions or boundaries cannot
enrich the historical execution set. Restart loses the live issuer association.

## Required facts, duplicates and conservative conflicts

Within this exact attributed order, exact broker `exec_id` is the only deduplication
key. This is not a global cross-account execution ownership claim. Required common
facts for that ID are exact numeric order ID, conId, side, shares and price.
Decimal equality is numeric economic agreement: equal values with different scale
agree, while every source reference retains its original Decimal sign/digits/exponent.
No price arithmetic or source normalization occurs.

The original journal `_execution_identity` requires every durable field except
callback source to agree for repeated execIds, including optional fields and absence.
PRE2 independently requires every retained fresh field except local sequence to
agree for repeated occurrences. These stronger within-source rules remain intact.
PRE8 reuses them for fact validation; cross-model comparison uses only the required
common facts. Optional client absence, permId, orderRef, cumulative quantity and
execution-time strings are retained without new cross-model interpretation.
Missing/different orderRef cannot invalidate an already numeric-qualified fresh
candidate or rescue an unrelated one. Requested unresolved permId candidates remain
usable after successful PRE7 numeric attribution, without permId ownership.

Existing durable BUY-entry replay requires execution conId to equal the consumed
contract and side to be exactly `BOT`. PRE8 applies those same immutable rules to
fresh candidates. `BUY` is not silently rewritten to `BOT`. Contract/symbol/exchange,
order type/TIF and timestamps are not additional attribution heuristics.

The closed conflict reasons, in deterministic enum order, are:

- `EXEC_ID_REQUIRED_FACT_CONFLICT`
- `LOCAL_CONTRACT_CONFLICT`
- `LOCAL_SIDE_CONFLICT`
- `INCORPORATED_QUANTITY_EXCEEDS_REQUESTED`

No source wins. Any proved conflict withdraws all incorporated facts and the usable
lower bound; exact input occurrence tuples and reasons remain retained. No partial
safe-looking subset is published. Conflicting data is not capped or discarded.

An `_AttributedExecutionFact` has `exec_id`, `durable_occurrences` and
`fresh_occurrences`. All supplied supporting references are retained. Compatible
repeated PRE2 occurrences remain separate references but contribute once. Durable
replay already retains one validated execution per ID in `attempt.executions`;
PRE8 does not rewrite that contract or mine additional callback records. The full
frozen prefix remains retained through PRE7. An agreed ID in both domains counts
once while retaining both references. Unique facts are sorted lexically by execId,
a canonical non-temporal presentation, never broker chronology. A representative
observation is available only for an agreeing fact; it is not a source preference.

## Exact historical quantity lower bound

For conflict-free evidence, `historical_execution_quantity_lower_bound` is the sum
of shares for unique incorporated execIds. PRE8 reuses journal `_sum`: explicit
256-digit Decimal context with inexact/rounded traps, independent of ambient
precision and traps. Source numeric bounds remain unchanged. Original requested
quantity is only a consistency ceiling. A sum above it yields conflict/None;
there is no capping, subtraction, remainder, average price or notional calculation.
Cumulative quantity cannot manufacture executions or augment this sum.

Zero unique executions gives exact Decimal zero with
`NO_POSITIVE_EXECUTION_OBSERVED`. This means no positive execution was observed in
these retained domains, never that no execution occurred or the order is unfilled.
Positive quantity is a historical confirmed execution lower bound, not complete
execution history, final fills, holdings or current position. No source proves no
late fill remains possible, no later offsetting trade or no bust/correction.

The closed outcome precedence is `ATTRIBUTION_BLOCKED`, then
`EXECUTION_EVIDENCE_CONFLICT`, then `NO_POSITIVE_EXECUTION_OBSERVED`, otherwise
`EXECUTION_EVIDENCE_INCORPORATED`.

## Immutable result and lifetime validation

`_AttributedExecutionEvidence` is private, frozen, slotted, opaque and memory-only.
Its fields are `pre7`, `durable_executions`, `fresh_executions`, `facts`,
`historical_execution_quantity_lower_bound`, `reasons`, `outcome`, `limitations`.
`attempt` is derived from the retained PRE7 result only when attribution succeeded;
there is no independent caller-trusted attempt/order identifier.

Structural constructors are fixture/model validation, not runtime authority.
`owner._require_evidence(result)` requires its exact latest publication, original
root and original bound validator, canonical value snapshots and all immutable
reference nodes, then calls the original PRE7 validator again. Reconstructed,
pickled, foreign, superseded or tampered evidence fails closed, including equal
replacement attempts, execution/candidate tuples, observations and derived facts.
A superseded PRE7 root cannot be retargeted. One bounded latest-result slot is
retained; repeated valid evaluation is deterministic and supersedes only that slot.

Ordinary failures yield the fixed sanitized
`ATTRIBUTED_EXECUTION_EVIDENCE_UNAVAILABLE`. Entered failures clear local publication
metadata and release the busy guard via direct assignments. No usable partial
publication or new broker-safety poisoning is introduced. KeyboardInterrupt,
SystemExit with exact code and GeneratorExit preserve their original exception
objects. Valid roots can be reevaluated after non-storage/input failure; original
upstream storage-failure behavior remains unchanged.

Only the original creator thread constructs/evaluates/validates. Wrong-thread
calls fail before inherited live locks/storage. PRE8 adds no locks; inherited
coordinator -> control -> read lifecycle -> read condition -> write dispatch ->
arrival ordering remains intact, with lower locks released before the existing
owner-thread durable read. No callback work or persistence wait is added.

## Fixed limitations and unchanged boundaries

The fixed PRE8 limitations are:

- HISTORICAL_EXECUTION_LOWER_BOUND_ONLY
- EXECUTION_ID_DEDUPLICATION_IS_ATTRIBUTED_ORDER_SCOPED
- NO_EXECUTION_COMPLETENESS_PROOF
- ABSENCE_IS_NOT_NO_EXECUTION_PROOF
- NO_NO_LATE_FILL_GUARANTEE
- INDEPENDENT_EVIDENCE_DOMAINS
- NO_CROSS_SOURCE_ATOMICITY
- NO_CROSS_SOURCE_EVENT_CHRONOLOGY
- NO_CURRENT_BROKER_STATE
- NO_FINALITY
- NO_LIFECYCLE_OR_REMAINDER
- NO_INVENTORY_OR_POSITION
- NO_COMMISSION_FINALITY
- NO_CASH_ACCOUNTING_OR_PNL
- NO_PROTECTIVE_OWNERSHIP
- NO_CANCEL_REPLACE_EXIT_AUTHORITY
- NO_MANAGEMENT_RECOVERY_OR_TRADING_AUTHORITY
- NOT_RESTART_ADOPTABLE

Every PRE1–PRE7 limitation survives through exact source references, including
PRE2's limited history/default filter, non-universal history, non-atomic intervals,
non-economic receipt times and lack of management currentness/ownership.

No accounting owner/lot, working-order evaluator, status-quantity comparison,
recovery or dispatch integration is added. Journal V1/V2/V3 bytes/semantics,
SQLite schema/version/hash, PRE9, prior collection/issuer contracts, callback
sequences/cursors, risk/strategy, public Broker/snapshot APIs and wire policy remain
unchanged. `__all__ = ()`; there is no public API/CLI, journal event or codec.
Evaluation has no new I/O, broker request/order call, clock, receipt consumption,
collection, boundary production, journal append, SQLite write or callback drain.

## Validation and next foundations

Focused tests use original collectors/sanitizers/consumers, durable capture/replay
and PRE5–PRE7 validation with anonymous fake SDKs and temporary SQLite. Setup alone
collects/captures; PRE8 evaluation blocks broker operations, clocks, writes and
accounting/lifecycle/recovery. Network and DNS are forbidden. SQLite authorizer and
before/after safety snapshots enforce read-only behavior. Cases include all source
domains, duplicate/required-fact conflicts, low-precision/trapping Decimal contexts,
overflow, frozen later history, reference tampering and exact BaseExceptions.

PRE8 still requires independent review and actual Windows qualification on Python
3.12.10 with ibapi 10.50.2/protobuf 5.29.5 and real Gateway opt-in disabled. No real
Gateway connection is needed or authorized. Only afterward may a separately
authorized foundation assess lifecycle/remainder, including status/execution
contradictions and late-fill uncertainty. Inventory/dispositions, protection,
cancel/replace/exits, continuity/retirement and recovery/risk/strategy restoration
remain later. First PAPER authorization remains separately blocked.

## Cloud validation

On Python 3.12.14, the final focused PRE8 run passed all 134 tests. Selected
regressions ran 5,827 tests, OK with three optional installed-SDK skips, including
PRE7 102, PRE6 168, PRE5 151, PRE5-PRE1 207, PRE4 164, PRE4-PRE2 179,
PRE4-PRE1 195 and PRE1/PRE2/PRE3 180/229/284. Full offline discovery ran
8,122 tests, OK with four skips (three optional installed-SDK checks and disabled
real Gateway integration). All 136 tracked Python files compiled with caches
outside the checkout.

Diff/whitespace, secret/account, `.env`/credential, private API/export,
broker-operation/new-I/O, durability/write, current-clock, accounting/lifecycle
authority, scope and V1/V2/V3 compatibility scans passed. The five intended files
are the only changes; all 60 previously tracked broker/backtest production Python
files remain byte-for-byte unchanged. No dependency installation, real IBKR
connection, broker request/order operation or Gateway change occurred. PRE8 still
requires independent review and actual Windows qualification; these cloud results
do not establish Windows or Gateway behavior.
