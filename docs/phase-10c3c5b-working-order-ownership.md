# Phase 10C3C5B — private historical entry lifecycle / remainder evidence owner

**HISTORICAL EVIDENCE ONLY. CURRENT WORKING REMAINDER AND INVENTORY REMAIN
UNQUALIFIED. THE FIRST REAL PAPER ORDER REMAINS BLOCKED.**

This phase classifies qualified durable entry lifecycle observations through the
exact C3C5A accounting owner. It adds no broker connection, read query, SDK request,
order write, cancellation, replacement, exit, protection, disposition accounting,
restart adoption, recovery application or barrier clearing. Gateway Read-Only
remains required. AccountMode stays UNKNOWN, public snapshot mode stays None and
LIVE stays unsupported. Independent review and actual Windows/Python 3.12
validation are required before the next phase.

## Source and lifetime ownership

The only source-of-truth chain is:

`C3C4 durable journal -> C3C5A acquisition accounting -> C3C5B historical projection`

`_create_paper_working_order_evidence_owner()` requires the exact registered
`_PaperExecutionAccountingOwner`. Its controlled factory registers at most one
lifecycle owner for that accounting-owner lifetime. Direct construction, copied
owners, equal reconstructed snapshots/receipts and foreign associations cannot
establish publication provenance. Private Python boundaries are application
isolation, not protection against hostile interpreter access.

The lifecycle association retains the exact accounting owner and its journal
owner, coordinator, control broker, read/write transports, authenticated receipt,
header, store, registered session and creator thread. Published values contain
none of those mutable owners, secrets, account numbers or raw SDK objects.

The accounting owner retains the identity and independent primitive values of
its successful publication. Its economics remain unchanged. Lifecycle refresh
requires that original successful snapshot and a fresh verified durable-history
comparison. A value-equal substituted snapshot is rejected.

Normal private usage is explicit:

1. Drain callbacks through the existing creator-thread persistence path.
2. Refresh the C3C5A accounting owner.
3. Refresh its registered C3C5B lifecycle owner.

Lifecycle refresh does not automatically drain, refresh accounting, overwrite its
snapshot, discover storage or adopt another session. A stale accounting snapshot
must not be promoted. Attempting publication from stale evidence permanently
fails the lifecycle owner rather than implicitly retrying.

## Controlled handoff and atomic publication

C3C5A checks the exact registered consumer, current association/thread, successful
accounting snapshot identity and immutable values. It captures the journal tail,
persistence cursor, callback through-sequence, staging/covered suffix, inhibition,
pending identity and arrival state under the established capture locks.

Capture locks are released before SQLite. The existing store verifies the entire
history; that history must match retained journal values and the exact registered
session. The durable progress declaration must agree with the producer cursor.
C3C5A deterministically reconstructs its own accounting facts and compares them
with its retained successful publication. Only independent immutable accounting
values and exact session/client/generation primitives enter the lifecycle handoff.

C3C5B derives a complete candidate off to the side. C3C5A repeats history,
authentication, snapshot and capture-state verification. A change in snapshot
identity, tail, acquisition facts, cursor, staging, covered suffix, inhibition,
pending identity, generation or arrival state rejects publication.

Final synchronization requires the exact issued handoff, exact consumer and exact
internally derived candidate. A one-use private publication permit is consumed
before one immutable lifecycle snapshot pointer is published. Equal reconstructed
handoffs/candidates and direct/replayed publication are rejected. No partial
candidate becomes visible. This permit publishes historical data only.

## Historical values

Published models are frozen, slotted, private and opaque:

- `_HistoricalLifecycleObservation` retains its kind, callback source, status,
  identity qualification and exactly one copied open-order or status event.
- `_EntryLifecycleEvidence` retains requested quantity, acquisition quantities,
  arithmetic residual, execution IDs, exact recorded average, historical
  observations/classification/consistency, bounded model evidence and obligations.
- `_PaperWorkingOrderEvidenceSnapshot` retains exact journal/session provenance,
  placing client/write generation, historical prefix metadata and immutable entries.

An open event has no fabricated status totals. Status filled, remaining, average
price, last price and parent/client identity remain paired with their own source
event. A terminal open observation cannot borrow earlier Submitted totals.

Source sequence is local callback arrival order, not economic execution time.
Observations are analyzed in source order. Journal append order may differ because
C3C4D supports staging/lookahead. Neither ordering is a broker chronology proof.

## Quantities and remainder bounds

| Term | Meaning |
| --- | --- |
| Q | Retained requested quantity; None for identifier-only denial. |
| E | Exact deduplicated C3C5A recorded acquisition quantity. |
| L | C3C5A historical filled lower bound, separately retained. |
| F_event / R_event | Filled and remaining supplied by the same historical status. |
| Q - E | Arithmetic residual only. |
| Q - L | Possible historical/model upper bound when lifecycle evidence is compatible. |

Every status retains `F_event + R_event == Q`. No quantity is guessed from another
event. Status filled does not become an execution lot or actual execution price.

The upper bound describes quantity not positively proven filled under the original
unchanged intent and internally compatible historical observations. It is never
current working quantity, broker remaining, canceled size, cancelable quantity or
protective sizing. It is None for contradictory/unresolved lifecycle evidence or
missing requested quantity. No positive lower bound on current working quantity
is published. A compatible pending historical prefix may retain a model bound;
that bound still grants no present management authority.

Current broker state, terminal status, working remainder, broker remaining,
currently working quantity, definitely canceled quantity, held inventory, closed
quantity and protectable quantity remain None. Current terminality/inventory,
disposition coverage, protective sizing and no-late-fill guarantee remain False.

## Classification and contradiction rules

The private classifications distinguish no qualified lifecycle, historical
submission, historical working, historical terminal, historical Filled report,
historical partial terminal, unresolved and contradictory evidence.

PendingSubmit is submission evidence. PreSubmitted/Submitted are historical
working observations. Filled is a historical terminal/full-fill report, even
when acquisition prices remain incomplete. Cancelled, ApiCancelled, Inactive and
Expired are terminal observations, never zero-fill or canceled-quantity proof.
UNKNOWN remains unresolved. An earlier strong open identity is required to qualify
secondary status observations; a later open cannot qualify earlier totals.

Filled quantities may increase and remaining quantities may decrease across
compatible observations. Working-to-working and working-to-terminal progression
is historical only. Decreasing filled, increasing remaining, lifecycle rank
regression, terminal-to-working and differing terminal statuses produce explicit
contradiction codes. No latest-wins policy, event discard or history rewrite exists.
Contradictory historical snapshots can be published as contradictory data, with
no model bound or management authority; acquisition facts remain owned by C3C5A.
Producer-prefix verification never implies lifecycle consistency.

E below qualified status filled creates an execution/price coverage obligation.
E above an earlier working total can be later acquisition evidence. E above a
terminal-reported filled total makes the relationship unresolved and withdraws the
bound, without discarding valid acquisitions. The first retained acquisition source
after cancellation or Filled is explicitly identified; later economic duplicates
retain the unchanged C3C5A first-source semantics. Receipt order alone creates no
economic chronology contradiction. Partial-terminal classification requires a
qualified terminal status's own positive filled/remaining pair.

Exact execution duplicates remain one acquisition. Conflicting execIds,
same-source conflicts, impossible status arithmetic and overfill retain unchanged
C3C4A/C3C5A rejection semantics. A rejected journal is unusable; no offending record
may be dropped or a clean prefix substituted. Quarantine/inhibition and manual
investigation requirements remain unchanged.

Status average/last prices never replace actual execution prices. Where the status
filled equals recorded E, a differing exact recorded average is a diagnostic code,
not a rewriting policy or an assumed broker rounding contract. Exact C3C5A rational
averages, including nonterminating ratios and supplied zero prices, are preserved.

## Zero fills, commissions and inventory

Recorded E == 0 does not prove no actual fill, no position or terminal unfilled.
Zero-filled cancellation/inactive/expired observations preserve ZERO_FILL_NOT_PROVEN.
No terminal-zero recovery shortcut is added.

Lifecycle classification does not require commission finality. Missing commission
remains unknown; a late report may update C3C5A accounting without rewriting
lifecycle observations. This phase changes no commission persistence or evaluator
semantics and introduces no completeness claim.

Aggregate positions never prove entry ownership, current held shares, canceled
quantity or protection size. Manual, pre-existing and other-client positions are
not adopted. Even complete acquisition evidence does not prove shares remain held;
production disposition ownership is absent. Every invoked entry preserves the
PROTECTABLE_QUANTITY_UNQUALIFIED obligation.

## Historical prefix and fresh-current evidence

VERIFIED_HISTORICAL_PREFIX means coverage of the controlled durable historical
prefix. HISTORICAL_PREFIX_WITH_PENDING_CALLBACKS preserves an undurable/staged or
covered-unpublished suffix. BLOCKED describes owner failure/no successful snapshot.
Published as-of values never change; the separate owner check can detect later
local evidence or accounting changes. It does not freshly verify external broker
history or renew authentication and cannot substitute for refresh revalidation.

Fresh current-lifecycle evidence remains required before any management decision.
Current reconciliation order models lack filled/remaining fields; existing refresh
disconnects/reconnects into a new read generation and cannot silently rebind the
original journal/accounting association. Completed/execution coverage is limited;
absence cannot prove zero fills. The older evaluator's unread-commission bridge
also remains separately qualified future work. C3C5B performs no broker read and
changes none of these boundaries.

## Threads, failure and restart

Only the original accounting/journal creator thread captures, verifies and
publishes. Pure projection is lock-, clock-, SDK- and I/O-free. Lock direction is:

`coordinator -> control -> read lifecycle -> read condition -> write dispatch -> arrival`

SQLite verification holds none of the read/write/arrival capture locks. Callback
threads never mutate the lifecycle owner or call SQLite and never wait for it.
No transaction spans SDK/network activity; callback capture can continue during
pure projection and invalidates publication if provenance changes.

Wrong ownership/thread, failed accounting, stale snapshot, changed provenance,
storage BUSY/poison/quarantine/corruption/unknown outcome, malformed observations,
gaps or interrupted publication permanently fail the lifecycle owner. The previous
historical snapshot and C3C5A positive acquisitions remain intact. Existing
journal/coordinator/control/write inhibition and authorization invalidation are
secured; no automatic retry, alternate store or clean-prefix publication exists.
KeyboardInterrupt, SystemExit and GeneratorExit propagate as the exact original
object after safety is secured.

Restart quarantine remains required. Recovery stays advisory. No pending retirement,
session migration, account rebinding, authorization re-arm or reconciliation clear
is added. The first real PAPER order still requires qualified current lifecycle,
inventory/disposition and protection allocation, protective/exit intents and
attribution, cancel/replace/fill races, quantity resizing, production strategy/risk
runtime and separately reviewed crash/restart/recovery/audit infrastructure.
