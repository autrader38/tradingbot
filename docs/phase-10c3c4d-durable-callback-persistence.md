# Phase 10C3C4D — private durable PAPER callback persistence

**EVIDENCE ONLY. THE FIRST REAL PAPER ORDER REMAINS BLOCKED.**

This phase extends the exact C3C4C journal owner and private coordinator. It adds
no broker connection, public write API, recovery application, restart adoption,
session/transport migration, authorization re-arm, portfolio accounting, production
position management, protection, or cancel/replace lifecycle. Gateway Read-Only
remains required. AccountMode stays UNKNOWN, public snapshot mode stays None, and
LIVE remains unsupported.

## Ownership and threads

The existing exact coordinator/control/read/write/account-receipt/header/store/
session/creator-thread association remains permanent. Setup still creates a fresh,
explicitly located journal outside dispatch; there is no open/adopt fallback.
Equal reconstructed objects cannot substitute for that association. SQLite default
thread checks remain enabled.

Only the journal creator thread runs `_sync_callback_persistence()`. Transport
callbacks never call the store or journal owner, await persistence, or acquire
coordinator/control locks under arrival synchronization. The already qualified
PRE1 callback capture remains unchanged, including both commission variants.

Three independent counters retain different meanings:

| Counter | Meaning |
| --- | --- |
| Transport callback sequence | Accepted sanitized source occurrences, including compressed duplicates. |
| Coordinator callback cursor | Existing in-memory attribution/confirmation processing. |
| Journal-owner persistence cursor | Fully classified contiguous prefix with required evidence and progress committed and verified. |

The persistence cursor starts at zero, never from the coordinator cursor, fetched
watermark, replay, or an existing journal. A persistence result is frozen opaque
advisory data (`CURRENT`, `PENDING`, `DEFERRED`, or `FAILED`), never authorization.

## Capture, persistence and publication

Lock direction remains:

`coordinator -> control -> read lifecycle -> read condition -> write dispatch -> arrival`

The owner retains coordinator/control serialization while capturing a bounded page
and pending/session identity under the later locks. It validates the retained span
metadata before paging can expand it: starts are contiguous, positive exact integer
sequences; ends cover exactly the transport sequence; capacity is bounded. A corrupt
start must not become a fabricated occurrence through paging.

The owner then releases read lifecycle/condition, write-dispatch and arrival locks.
SQLite work checks that those locks are not owned by the executing thread. Callback
arrival capture and deferred transport bookkeeping can both proceed while storage
is slow. There is no second general queue or mailbox lock.

Evidence appends use the existing C3C4B transactional append, post-commit verification
and C3C4C exact-history checks. Before progress, and again after its verified commit,
the owner reacquires capture-side locks and checks exact owner/session/pending
identity, generation, monotonic source sequence, staged primitive copies and
transport safety flags. Cursor publication occurs under that synchronization.
New valid arrivals remain undurable and keep the persistence barrier closed.

Work and staging are bounded: at most 128 staged source occurrences, pages of 128,
and at most 128 pages per explicit call. Unresolved staging that prevents covering
an additional suffix at capacity fails closed. No evidence is dropped to create a
complete prefix. The original transport ledger remains retained and bounded.

## Event mappings and attribution

| Sanitized evidence | Durable value |
| --- | --- |
| Open order | `_OpenOrderObserved`: exact order/client/reference identity, contract fields, side, quantity, type, TIF, lifecycle. |
| Order status | `_OrderStatusObserved`: exact same-event status, filled, remaining, average/last prices, parent/client identity. |
| Execution | `_ExecutionObserved`: exact execId, order/client/reference identity, conId, side, shares and price; cumulative quantity and execution time remain None. |
| Commission | `_CommissionReceived`: attributed client reference, execId, report ID, signed amount, currency and actual PRE1 receipt timestamp. |
| Covered prefix | `_CallbackProgress`: exact session, verified processed prefix, informational captured through-watermark. |

Open/status/execution use `_CallbackSource` with exact session, captured generation
and occurrence sequence. The lifecycle mapping is closed; the transport's retained
UNKNOWN status remains UNKNOWN. No aggregate status totals become same-event facts.

Target numeric IDs come from retained durable attempt reservations. A known owned
execId relationship is checked before unrelated-order filtering. A different
numeric order is not adopted through a matching reference alone, preserving the
existing coordinator ownership rule. Well-formed unrelated order facts create no
target economic records; owner-controlled occurrence coverage records their
classification. Foreign execution identities remain available to classify linked
commissions and detect later attempted ownership/conflicting identity.

An attributable target callback must follow the exact PRE2 invocation boundary
recorded from the transport's consumed commitment. A reservation collision before
commitment cannot become evidence of this process's future invocation.

Missing execution orderRef requires a durably established matching strong open
identity at an earlier source sequence and exact supplied placing client ID.
A later open observation cannot manufacture that earlier anchor. Execution repeats
and overfill retain unchanged C3C4A conservative semantics and exact Decimal sums.

## Duplicates, source occurrences and append chronology

Each expanded occurrence is independently validated. Adjacent identical
open/status/execution occurrences may reference the same already committed fact
through private occurrence coverage; every sequence still has to be checked before
progress. Interleaved facts are not compressed this way. Distinct execution facts
are appended with their actual source and unioned by unchanged replay without
double-counting shares. No source maximum is used as proof of coverage.

Durable progress certifies the controlled producer's checked prefix. It does not
turn replay into an independently verifiable account of every duplicate occurrence.
The retained transport ledger, staging identity, backing committed record and
exact-history validation support this producer invariant. Coverage receipts are
released only after verified progress publication; the ledger and canonical
economic facts remain retained. No caller supplies an assessment, coverage receipt
or progress declaration as authority.

Broker source chronology and journal append chronology are different. Synchronous
callbacks can arrive inside placeOrder. They stay in memory during SDK activity;
the existing `_SdkReturnedNormally` append occurs after normal SDK return, and a
later owner drain writes the callbacks. This does not claim the callbacks arrived
after SDK return. Normal SDK return remains distinct from broker acceptance.

## Commission staging and financial conflicts

Commission ownership requires an already committed attributed execution with the
exact execId in this exact session generation. Currency must satisfy the consumed
order's unchanged journal requirements. Account position, symbol, guessed numeric
ID, timestamps, amounts, journal adjacency and caller labels are not attribution.

A commission before its execution remains staged and blocks prefix progress. A
later qualifying execution can be durably appended as bounded lookahead with its
actual later source sequence. Then the earlier commission may be committed and the
now-covered prefix advanced. Lookahead never acknowledges intervening sequences,
rewrites source chronology or manufactures an identity anchor.

Legacy/fees callbacks with the same execId/report ID/numeric amount/currency
reference one committed economic commission. Separate receipt times do not create
a financial conflict; first-receipt replay semantics remain unchanged. Private
occurrence coverage associates every duplicate with that backing fact before
progress. Signed rebates and supplied zero retain their exact Decimal values.

`_CommissionReceived` deliberately still has no `_CallbackSource`. Generation and
sequence association is a controlled in-process producer invariant, not something
replay can infer from adjacency. This phase adds no restart source reconstruction
or cursor adoption. Different report identity, amount or currency fails closed;
there is no latest-wins, correction, conversion, averaging or absolute-value policy.
Missing commission stays unknown and commission completeness is never claimed.

## Drain points, entry barriers and inhibition

The private explicit drain is usable after SDK activity has fully unwound. Owner-thread
callback synchronization also drains, as does reconciliation preparation and the
pre-entry path. Existing memory synchronization remains usable from other threads
without claiming durability. Explicit persistence or dispatch on the wrong owner
thread fails before SQLite and permanently inhibits entries.

A reentrant drain during an active dispatch or another drain returns DEFERRED with
zero SQLite work. Reentrant memory synchronization also leaves the future pending
order's callback occurrences unconsumed until dispatch unwinds. No transaction
spans SDK/network activity.

Every bound entry must satisfy a pure current-persistence barrier before proceeding,
at durable invocation preparation, and at PRE2 final commitment: exact owner/session,
cursor equals current callback sequence, no staged/covered suffix, no capture gap
or unusable arrival, and no persistence failure. PRE2 independently closes new
arrival/reservation races. A denied client reference remains consumed; unresolved
commission evidence cannot allow a numeric reservation or later payload upgrade.

The restricted evidence-only append path works while dispatch is already inhibited,
provided storage and exact lifetime ownership remain usable. It accepts callback
evidence and verified progress, never invocation/authorization/recovery events.
It does not restore owner health. Persistence alone never clears pending
confirmation or any reconciliation barrier; existing reviewed in-memory processing
still owns confirmation behavior.

## Failures, interruptions and restart

BUSY, storage failure, poisoning, quarantine, unknown commit outcome, corruption,
wrong thread/association, malformed provenance, sequence gap, overflow, semantic
rejection or interruption permanently fail persistence and inhibit the owner,
coordinator/control/write planes. Existing authorization invalidation is applied
before publication or propagation. Failed persistence is not retried; no alternate
store/session is selected. Best-effort fixed reconciliation diagnostics grant no
authority and never replace the original interruption.

Evidence and progress are separate verified commits. Failure after evidence but
before progress leaves the prefix unacknowledged. Failure after progress commit but
before local publication leaves the runtime blocked; it does not trigger reread/
resume authority. KeyboardInterrupt, SystemExit and GeneratorExit propagate as the
exact original object after safety is secured. A previously verified prefix is not
retracted, but no failed batch is published as complete.

Semantic-invalid append preserves the offending record and persistent quarantine
under unchanged C3C4B rules. The rejected journal is unusable for authoritative
replay; no offending record may be discarded and no partial replay presented as a
clean state. Manual investigation/recovery is required. This adds no recovery path.

`restart_quarantine_required` remains True. No journal adoption, session resume,
startup write permission, transport migration, re-arm, portfolio reconstruction,
pending retirement, barrier clearing or recovery application is implemented.

## Validation and remaining gates

The focused adversarial suite uses temporary SQLite and fake SDKs only. It covers
owner/thread substitution, lock release and slow-storage callback races, mappings,
staging/lookahead, duplicates/conflicts, pages/gaps/overflow, entry inhibition,
storage failures, interruption seams and privacy. Existing callback regressions
remain; the PRE1 preexisting-commission test now requires the new persistence gate
to block SDK invocation and numeric reservation, preserving capture assertions.

Offline Linux validation:

| Check | Result |
| --- | --- |
| New C3C4D focused suite | 156 passed. |
| Existing selected regressions | 1,539 passed. |
| Full offline suite | 4,104 run; 4,103 passed; one existing opt-in real integration skipped. |
| Python compilation | All 98 Python files passed without writing bytecode. |
| Whitespace and changed-file credential/account/`.env` scans | Passed. |

Commit uncertainty is exercised both before and after actual SQLite commit for
evidence and progress. A possibly committed record never permits cursor publication
or a persistence retry after failure.

Linux validation is not Windows qualification. Independent review and actual
Windows/Python 3.12 testing of C3C4D remain mandatory before the next phase.
Production exposure/accounting ownership, position runtime, partial/remainder and
protective/cancel lifecycle, strategy management, session retirement/migration,
restart authority and reviewed recovery/audit application remain absent.
Gateway Read-Only must remain enabled and the first real PAPER order remains blocked.
