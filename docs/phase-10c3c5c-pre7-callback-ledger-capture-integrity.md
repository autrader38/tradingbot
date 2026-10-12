# Phase 10C3C5C-PRE7 — private callback ledger capture integrity

PRE7 repairs the shared production callback ledger before qualified status
publication. It does **not** issue a PRE5 production receiver, publish qualified
status handoffs or connect PRE6 to production consumers. Gateway Read-Only
remains required and the first real PAPER order remains blocked.

Starting checkpoint: `03bc083aadda360e50c08490a6f1ac12464c8fda`.
The user reported independent review and actual Windows validation of PRE6
complete at that checkpoint: 158 focused tests, PRE5/PRE4/PRE3 regressions and
5,063 full-suite tests, with 14 skips. Those results describe the preceding
checkpoint; PRE7 still requires independent review and Windows validation.

## Defect and invariant

Previously the ledger could reach its 128 retained-run capacity with fact A at
sequence 128, lose different fact B at 129, then extend A's duplicate span to
130 when A arrived again. Paging would fabricate A at 129. A reconciliation
latch alone did not preserve the truth of that span metadata.

Each compressed span now represents only consecutive, actually copied
occurrences of the same existing callback identity. Extension requires:

- a nonempty retained ledger and run-end list;
- no prior evidence loss;
- the previous run end equal to the new sequence minus one;
- the new identity equal to both the cached identity and the retained tail's
  actual identity, including the existing generation/content semantics.

PRE7 preserves the existing economic identity rules for open, legacy status,
execution and commission evidence. It does not introduce PRE6 source identity
semantics into those older types. Interleaved facts remain separate retained
runs; consecutive duplicates preserve every logical occurrence in their span.

## First loss and frozen prefix

`PaperTWSTransport._callback_evidence_lost_at` is a private read-only view of a
transport-lifetime first-loss value. It starts as `None`. Internally only an
exact positive `int`, excluding `bool`, can establish it. Normal assignment
or deletion of the property/backing attribute is refused. Later loss never
changes it. Disconnect, generation reset and reconnect never clear it.
It grants no acknowledgement, durability, ownership or authority.

At capacity, a new noncompressible valid evidence occurrence consumes its
callback sequence, records first loss and permanently requires reconciliation.
The retained prefix freezes: no later append, eviction or span extension is
allowed. `_last_callback_identity` clears immediately and remains `None` after
loss. Valid subsequent evidence occurrences still advance the callback
watermark. Memory remains bounded to 128 retained runs, with no second queue.
An adjacent duplicate at capacity can still extend a run **before any loss**.

Capacity loss itself leaves `_arrival_unusable` unchanged: it is not a malformed
arrival. The separate lifetime loss marker and permanent reconciliation latch
inhibit writes. Interrupted/failed capture updates set `_arrival_unusable` as
well as reconciliation. Generation reset can reset that generation-specific
flag but cannot repair lifetime capture loss or clear reconciliation.

Stale/closed callbacks are rejected before copying. Error, connection-closed,
allocator and collection-end callbacks retain their existing semantics and do
not create evidence loss merely because they have no evidence sequence.
Malformed source data that fails immutable copying is not presented as a valid
lost evidence occurrence: it does not advance the callback sequence or establish
the loss marker. The existing unusable-arrival policy still applies.

## Capture transition and interruption

One private helper runs under `_arrival_lock`. It checks the prefix, calculates
the next lifetime sequence, makes the existing sanitized immutable copy,
computes identity, chooses append/adjacent extension/capacity loss/frozen prefix,
prepares bounded replacement lists, commits the metadata and callback watermark,
updates the observed-ID lifetime floor, then checks the completed prefix again.

Other threads cannot page or validate a reservation between these updates.
Prepared lists do not mutate the old prefix in place. If a valid copied
occurrence's identity, publication, watermark, loss marking, floor update or
final invariant check fails, cleanup restores the untouched prefix, consumes
the occurrence without decreasing the watermark, records first loss if absent,
clears cached identity and secures permanent reconciliation/unusable arrival.
Cleanup bypasses the fallible transition helpers. It does not roll back any
conservative allocator state.

`KeyboardInterrupt`, `SystemExit` and `GeneratorExit` secure local safety before
the exact original exception object propagates. No interruption is replaced
with `ReadOnlyError`. Ordinary failures retain the existing callback convention:
capture fails closed without leaking a payload error into normal SDK callback
dispatch. Failed lossy capture does not repeat its failed transition through
deferred write bookkeeping. No callback capture performs journal or SQLite I/O.

## Metadata and paging

The private checker requires exact integer sequence/end values, matching list
lengths, at most 128 runs, recognized evidence types, positive exact generations
and a contiguous ordered prefix beginning at sequence one. Every run end is at
least its start and at most the callback watermark. With no loss, the prefix
ends at that watermark. With loss at N, it ends at N−1 and cached identity is
`None`. No run can cross N.

`_EvidenceBatch` is unchanged. Its `through_sequence` remains the informational
callback watermark, never an acknowledgement. Paging validates metadata before
expansion and returns at most 128 logical events. Before loss it expands the
actual retained spans. After loss it returns only the truthful prefix: a cursor
at N−1 receives an empty page whose watermark is greater than its cursor. A
cursor at or beyond N is refused. Fetching never acknowledges, clears or repairs
evidence.

For malformed metadata, paging latches unusable arrival/reconciliation and
returns **no events** with the still-valid, strictly greater integer watermark
when that visibly incomplete response preserves existing coordinator behavior.
If that cannot represent a missing suffix, it raises. BaseExceptions propagate
unchanged after safety is secured. Invalid metadata is never expanded or
renumbered into trustworthy evidence.

## Durable progress proof

No C3C4D production change is required. Its owner-thread callback snapshot
already validates retained contiguous spans before calling the pager and
requires the final retained end equal to the entire callback watermark.
After first loss at N, the end remains N−1 while the watermark is at least N.
That check therefore fails permanently, before evidence staging or progress.
Even forged span metadata that appears to cover the watermark is rejected by
the transport's additional loss-aware checker/pager.

A previously captured immutable page has only sequences through its then
watermark T. A later loss occurs after T; that page can never acknowledge the
lost occurrence. If loss occurs during owner-thread database work, revalidation
and the next snapshot retain conservative failure semantics. A verified
progress append for the older truthful prefix may exist even when its local
cursor publication fails; neither its processed sequence nor a published
persistence cursor crosses the first loss. A later duplicate cannot unblock
draining. Persistence failure continues to inhibit the journal/coordinator and
invalidate authorization. It creates no retry or alternate-store authority.

The owner can persist a complete truthful prefix **before loss**. This change
conservatively refuses new drains once loss is known rather than attempting to
salvage that prefix after loss. Neither callback synchronization nor fetching
copies an in-memory cursor into the persistence cursor.

Journal events, replay assumptions, SQLite schema/storage semantics and restart
quarantine remain unchanged. Loss is not reconstructed from later evidence.
After restart, the journal remains evidence only; no transport/owner adoption,
session migration, recovery application or barrier clearing is introduced.

## Order IDs and locks

Only an existing successfully sanitized order-bearing event can raise the
lifetime observed order-ID floor. Commission events have no order ID. The
normal floor update follows metadata/watermark commit, including explicit
capacity loss. If that transition is interrupted, cleanup still conservatively
preserves the actual copied ID's floor/exhaustion. Floor advancement does not
claim capture or durability; it only prevents ID reuse. MAX-ID exhaustion and
permanent numeric-ID consumption are not reset or rolled back.

Capture owns arrival synchronization and releases it before waiting for write
bookkeeping. It acquires no coordinator/control/store locks. Existing outer
direction remains coordinator → control → read lifecycle → read condition →
write dispatch → arrival. No new locks, SDK requests, network/DNS calls or
callback-thread I/O are added. PRE2 reconciliation/watermark/fence checks remain
unchanged and reject reservations affected by loss.

## Validation and boundaries

`tests/test_ibkr_callback_capture_integrity.py` covers 171 focused cases:
all existing evidence kinds and both commission variants; A/B/A overflow;
alternation and long repeats; adjacency, exact capacity and generation changes;
bounded paging and corrupt metadata; interruption/failure seams; blocked
bookkeeping and concurrent capture; ID-floor/exhaustion permanence; and actual
offline C3C4D persistence/progress behavior, including loss during append.
Anonymous SDK doubles and temporary test journals perform no real broker work.
Tests guard callback-thread storage/journal/network/DNS access.

Cloud/Linux results: focused PRE7 **171/171 passed**; selected regressions
**3,063 run, 3,060 passed, 3 optional installed-SDK skips**; full offline suite
**5,234 run, 5,230 passed, 4 skips**. The full-suite skips are those three
installed-SDK cases plus the disabled real Gateway opt-in. All 112 Python files
compiled. Diff/index whitespace checks, exact five-file scope, secret/account
identifier, hidden/ignored `.env`, public surface and network/storage scope
scans passed. No dependency was installed or real IBKR operation performed.

Regression validation includes PRE3/PRE4/PRE5/PRE6, commission compatibility,
PRE2 reservations, callback confirmation/commission capture/persistence,
historical accounting/lifecycle, reconciliation/recovery and journal/storage.
Actual PRE7 Windows installed-SDK validation remains mandatory; cloud SDK skips
must be reported separately. The real Gateway opt-in remains disabled.

PRE3/PRE4/PRE5/PRE6, C3C4D, C3C5A/B, journal/storage, public Broker APIs, wire
allowlists, strategy/backtest and LIVE behavior are unchanged. No production
qualified status receiver or publication, currentness, terminal finality,
current working remainder, ownership, inventory, protection, cancellation,
recovery or trading authority is added. AccountMode remains UNKNOWN and public
snapshot mode remains `None`. Gateway Read-Only remains required.

Independent review and Windows validation of PRE7 are required before
re-qualifying qualified status production ledger publication. Qualified
durability/reconciliation integration, fresh-source/session continuity and the
production inventory, protection/cancel/exit and reviewed recovery foundations
remain later work. The first real PAPER order remains blocked.
